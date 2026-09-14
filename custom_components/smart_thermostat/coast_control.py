"""Bounded anticipatory heat cutoff learned from recent completed cycles."""

from math import isfinite
from statistics import median


def predict_coast(records, runtime, now):
    """Estimate rise using similar runtimes, never extrapolate far beyond data.

    This is an empirical estimate, not a claim of identified physical PID gains.
    Use the lower quartile of local estimates to avoid aggressive early cutoff.
    """
    recent = [r for r in records if 0 <= now - r['completed'] <= 14 * 86400]
    if len(recent) < 6 or runtime < 120:
        return None
    similar = [r for r in recent if 0.5 * runtime <= r['runtime_seconds'] <= 2 * runtime]
    if len(similar) < 3:
        return None
    similar = sorted(similar, key=lambda r: abs(r['runtime_seconds'] - runtime))[:9]
    estimates = sorted(r['coast'] * runtime / r['runtime_seconds'] for r in similar)
    center = median(estimates)
    if median(abs(v - center) for v in estimates) > 0.15:
        return None
    return min(1.0, estimates[(len(estimates) - 1) // 4])


class CoastControl:
    """Can only inhibit heating; never request heat, override OFF or set targets."""

    def __init__(self):
        self._last_heating = None
        self._started = None
        self._pending = None
        self._hold = None
        self.suppressed = False
        self.reason = 'waiting_for_complete_cycle'
        self.predicted_rise = None

    def reset(self, reason):
        self._last_heating = None
        self._started = None
        self._pending = self._hold = None
        self.suppressed = False
        self.reason = reason
        self.predicted_rise = None

    def evaluate(self, now, temperature, target, heating, records, min_on, enabled=True):
        if not enabled or not all(isfinite(v) for v in (now, temperature, target, min_on)):
            self.reset('inactive_or_invalid_data')
            return False
        self.observe_output(now, heating)
        self.suppressed = False
        self.predicted_rise = None
        if self._pending:
            if abs(target - self._pending['target']) > 0.001 or now > self._pending['expires']:
                self._pending = None
            elif not heating:
                self._hold = {'target': target, 'off': now, 'peak': temperature, 'peak_at': now}
                self._pending = None
            else:
                self.reason = 'awaiting_heat_off'
                self.suppressed = True
                return True
        if self._hold:
            hold = self._hold
            if heating or abs(target - hold['target']) > 0.001:
                self._hold = None
            else:
                if temperature > hold['peak']:
                    hold.update(peak=temperature, peak_at=now)
                falling = temperature <= hold['peak'] - 0.05 and now - hold['peak_at'] >= 120
                if now - hold['off'] >= 900 or falling:
                    self._hold = None
                    self.reason = 'coast_finished'
                    return False
                self.reason = 'allowing_residual_heat_to_settle'
                self.suppressed = True
                return True
        if not heating:
            self._started = None
            self.reason = 'normal_controller'
            return False
        if self._started is None:
            self.reason = 'unknown_cycle_start'
            return False
        runtime = now - self._started
        if runtime < min_on:
            self.reason = 'minimum_on_time'
            return False
        self.predicted_rise = predict_coast(records, runtime, now)
        if self.predicted_rise is None:
            self.reason = 'insufficient_comparable_history'
            return False
        if self.predicted_rise > 0 and temperature < target <= temperature + self.predicted_rise:
            self._pending = {'target': target, 'expires': now + 120}
            self.reason = 'predicted_target_reached_after_off'
            self.suppressed = True
            return True
        self.reason = 'normal_controller'
        return False

    def observe_output(self, now, heating):
        """Capture the actual output edge, not the next temperature report."""
        if self._last_heating is False and heating:
            self._started = now
        elif not heating:
            self._started = None
        self._last_heating = heating

    def wait_for_telemetry(self, now, target):
        """Keep a restored bounded hold while startup entities become available."""
        self._last_heating = None
        self._started = None
        if self._pending and (now > self._pending['expires'] or target != self._pending['target']):
            self._pending = None
        if self._hold and (now - self._hold['off'] >= 900 or target != self._hold['target']):
            self._hold = None
        self.suppressed = bool(self._pending or self._hold)
        self.reason = 'waiting_for_telemetry'
        return self.suppressed

    def snapshot(self):
        return {'pending': dict(self._pending) if self._pending else None,
                'hold': dict(self._hold) if self._hold else None}

    def restore(self, data, now):
        """Restore only a bounded hold, never a guessed pre-restart heat runtime."""
        if not isinstance(data, dict):
            return
        for name, keys in (('pending', ('target', 'expires')),
                           ('hold', ('target', 'off', 'peak', 'peak_at'))):
            value = data.get(name)
            if not isinstance(value, dict) or not all(
                isinstance(value.get(k), (int, float)) and isfinite(value[k]) for k in keys
            ):
                continue
            valid = (0 <= value['expires'] - now <= 120) if name == 'pending' else (
                0 <= now - value['off'] < 900 and value['off'] <= value['peak_at'] <= now)
            if valid:
                setattr(self, '_' + name, dict(value))

    def diagnostics(self):
        return {'adaptive_enabled': True, 'automatic_adjustments': self.suppressed,
                'inhibiting_heat': self.suppressed,
                'reason': self.reason, 'predicted_coast_c': self.predicted_rise}
