"""Thermal cycle measurements, independent of HA and actuator commands."""

from math import isfinite
from statistics import median
from .pid_cycle_metrics import measure_cycle
from .thermal_response import measure_response, summarize_responses


def has_session_timing(record):
    period = record.get('pwm_seconds')
    return (record.get('cycle_basis') == 'demand_session'
            and isinstance(period, (int, float)) and not isinstance(period, bool)
            and isfinite(period) and period >= 0)


class ThermalObserver:
    """Learn post-heating coast from uninterrupted cycles, never command a heater.

    A new process starts unarmed: an initial ON is not a measured cycle.
    Only completed records survive restarts; interrupted cycles are discarded.
    """

    def __init__(self, settle_seconds=900, max_gap=120, rise_tolerance=0.0):
        self.settle_seconds = settle_seconds
        self.max_gap = max_gap
        self.rise_tolerance = rise_tolerance
        self.records = []
        self.history_report = {"status": "not_imported"}
        self.status = "waiting_for_off"
        self.reason = None
        self._last = None
        self._armed = False
        self._cycle = None

    def invalidate(self, reason):
        self._cycle = None
        self._armed = False
        self.reason = reason
        self.status = "waiting_for_off"

    def sample(self, now, temperature, target, heating, enabled=True, gains=None, demand=None,
               pwm_seconds=None, demand_debounce_seconds=None):
        """Accept a timestamped observation; return True after a completed cycle."""
        values = (now, temperature, target)
        if not all(isinstance(v, (int, float)) and isfinite(v) for v in values):
            self.invalidate("invalid_temperature")
            self._last = None
            return False
        if heating not in (True, False) or not enabled:
            self.invalidate("inactive_or_unavailable")
            self._last = None
            return False
        if pwm_seconds is not None and (isinstance(pwm_seconds, bool)
                or not isinstance(pwm_seconds, (int, float)) or not isfinite(pwm_seconds)
                or pwm_seconds < 0):
            self.invalidate('invalid_pwm_period')
            self._last = None
            return False
        if self._last is not None:
            elapsed = now - self._last
            if elapsed <= 0:
                return False
            if elapsed > self.max_gap:
                self.invalidate("sensor_gap")
        self._last = now
        cycle = self._cycle
        basis = 'demand_session' if isinstance(demand, bool) else 'relay_pulse'
        has_demand = demand if basis == 'demand_session' else heating
        if cycle and cycle['basis'] != basis:
            self.invalidate('demand_basis_changed')
            cycle = None
        if cycle and cycle['pwm_seconds'] != pwm_seconds:
            self.invalidate('pwm_period_changed')
            cycle = None
        if cycle and cycle.get('gains') != gains:
            self.invalidate('pid_gains_changed')
            cycle = None
        if cycle and abs(target - cycle["target"]) > 0.001:
            self.invalidate("target_changed")
            cycle = None
        if not self._armed:
            if not heating and not has_demand:
                self._armed = True
                self.status = "ready"
            return False
        if cycle is None:
            if heating and has_demand:
                self._cycle = {"start": now, "target": target,
                               "start_temperature": temperature,
                               "samples": [(now, temperature)],
                               "basis": basis, "heating": heating, "last_sample": now,
                               "pwm_seconds": pwm_seconds,
                               "runtime": 0.0,
                               "on_started": now, "on_intervals": [],
                               "gains": dict(gains) if gains is not None else None}
                self.status = "heating"
                self.reason = None
            return False
        if len(cycle['samples']) >= 4096:
            self.invalidate('cycle_sample_limit')
            return False
        cycle['samples'].append((now, temperature))
        if cycle['heating']:
            cycle['runtime'] += now - cycle['last_sample']
            if not heating:
                cycle.update(last_off=now, last_off_temperature=temperature)
                cycle['on_intervals'].append((cycle.pop('on_started'), now))
        elif heating:
            cycle['on_started'] = now
        cycle.update(heating=heating, last_sample=now)
        if "stop" not in cycle:
            if has_demand:
                cycle.pop('zero_since', None)
            else:
                cycle.setdefault('zero_since', now)
            debounce = (demand_debounce_seconds if demand_debounce_seconds is not None
                        else 2 * (pwm_seconds or 0) if basis == 'demand_session' else 0)
            if (not heating and not has_demand
                    and now - cycle['zero_since'] >= debounce):
                if cycle['runtime'] < 120:
                    self.invalidate("short_cycle")
                    return False
                stopped = cycle['last_off']
                settling_samples = [(t, value) for t, value in cycle['samples'] if t >= stopped]
                peak_at, peak = max(settling_samples, key=lambda point: (point[1], point[0]))
                cycle.update(stop=stopped, stop_temperature=cycle['last_off_temperature'],
                             peak=peak, peak_at=peak_at)
                self.status = "settling"
            return False
        if heating or has_demand:
            # A fresh pulse contaminates measurement of the previous pulse's coast.
            self.invalidate("reheated_before_settling")
            return False
        if temperature > cycle["peak"]:
            cycle.update(peak=temperature, peak_at=now)
        falling = temperature <= cycle["peak"] - 0.05 and now - cycle["peak_at"] >= 120
        if now - cycle["stop"] < self.settle_seconds and not falling:
            return False
        self.records.append({
            'thermal_response': measure_response(cycle['samples'], cycle['on_intervals']),
            "pid_metrics": measure_cycle(cycle['samples'], cycle['target'], cycle['stop'],
                                         self.rise_tolerance),
            "gains": cycle['gains'],
            "cycle_basis": cycle['basis'],
            "pwm_seconds": cycle['pwm_seconds'],
            "started": cycle["start"],
            "stopped": cycle["stop"],
            "completed": now,
            "runtime_seconds": cycle['runtime'],
            "target": cycle["target"],
            "stop_temperature": cycle["stop_temperature"],
            "peak": cycle["peak"],
            "coast": cycle["peak"] - cycle["stop_temperature"],
            "overshoot": max(0, cycle["peak"] - cycle["target"]),
        })
        self.records = self.records[-30:]
        self._cycle = None
        self.status = "ready"
        return True

    def restore(self, data):
        """Validate persisted data, retaining completed measurements only."""
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        records = data.get("records", [])
        if not isinstance(records, list):
            return
        keys = ("started", "stopped", "completed", "runtime_seconds", "target", "stop_temperature",
                "peak", "coast", "overshoot")
        self.records = [dict(r) for r in records if isinstance(r, dict)
                        and all(isinstance(r.get(k), (int, float))
                                and isfinite(r[k]) for k in keys)
                        and r["runtime_seconds"] >= 120
                        and r['started'] < r['stopped'] <= r['completed']
                        and 0 <= r["coast"] <= 10
                        and 0 <= r["overshoot"] <= 10][-30:]

    def merge_history(self, records, window_start):
        """Deduplicate replay and live samples of the same physical heat pulse."""
        combined = sorted([*records, *self.records], key=lambda r: r['stopped'])
        merged = []
        for record in combined:
            if record['started'] < window_start:
                continue
            if merged and abs(record['stopped'] - merged[-1]['stopped']) <= self.max_gap:
                if (record.get('cycle_basis') == 'demand_session'
                        and (merged[-1].get('cycle_basis') != 'demand_session'
                             or (has_session_timing(record) and not has_session_timing(merged[-1])))):
                    merged[-1] = record
                continue
            merged.append(record)
        self.restore({'version': 1, 'records': merged})

    def snapshot(self):
        return {"version": 1, "records": [dict(r) for r in self.records]}

    def diagnostics(self):
        records = self.records
        return {
            "mode": "observe",
            "status": self.status,
            "rejection_reason": self.reason,
            "completed_cycles": len(records),
            "demand_sessions": sum(r.get('cycle_basis') == 'demand_session' for r in records),
            "timed_demand_sessions": sum(has_session_timing(r) for r in records),
            "median_coast_c": round(median(r["coast"] for r in records), 3) if records else None,
            "median_overshoot_c": round(median(r["overshoot"] for r in records), 3) if records else None,
            "recommendation": "insufficient_data" if len(records) < 6 else "review_cycle_measurements",
            "automatic_adjustments": False,
            "history": self.history_report,
            "calibration_evidence": summarize_responses(records),
        }
