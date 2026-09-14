"""Gain proposal/validation lifecycle around the upstream Adaptive Climate rules.

No actuator access. Callers must supply measured cycle metrics and gain bounds
in their PID controller's units. The only automatic zero-gain initialization
here is bounded, cycle-derived seeding for missing I/D terms, followed by the
same commit and rollback validation as ordinary gain changes.
"""

from copy import deepcopy
from math import isfinite, prod
from statistics import mean

from .adaptive_pid_rules import (
    evaluate_pid_rules, detect_rule_conflicts, resolve_rule_conflicts,
)
from .adaptive_robust_stats import robust_average

KEYS = ('kp', 'ki', 'kd')
METRICS = ('overshoot', 'undershoot', 'oscillations', 'rise_time', 'settling_time')


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def measured_cycle(cycle):
    """Missing, negative, or malformed metrics are not measured zeroes."""
    return (isinstance(cycle, dict) and not cycle.get('disturbed', False)
            and all(finite_number(cycle.get(k)) and cycle[k] >= 0
                    for k in (*METRICS, 'started', 'completed'))
            and cycle['started'] < cycle['completed'])


def comparable_cycles(records, gains, now, since):
    """Select unique records; conflicting copies of a cycle are withheld."""
    by_start = {}
    rejected = set()
    for record in records:
        if not isinstance(record, dict) or not finite_number(record.get('started')):
            continue
        start = record['started']
        if not measured_cycle(record):
            rejected.add(start)
            continue
        fingerprint = {k: record.get(k) for k in (*METRICS, 'completed', 'gains')}
        previous = by_start.get(start)
        if previous is not None and fingerprint != {
            k: previous.get(k) for k in (*METRICS, 'completed', 'gains')
        }:
            rejected.add(start)
        by_start[start] = record
    return sorted((record for start, record in by_start.items()
                   if start not in rejected and record.get('gains') == gains
                   and 0 <= now - record['completed'] <= 14 * 86400
                   and (since is None or start > since)), key=lambda r: r['completed'])


def _positive_robust(records, path):
    values = []
    for record in records:
        value = record
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if finite_number(value) and value > 0:
            values.append(value)
    if not values:
        return None
    return robust_average(values)[0]


def _seed_missing_gain(key, cycles, gains, limits, pwm_seconds):
    """Seed a zero I/D gain from recent cycles without bypassing validation.

    The values are intentionally modest. They only give multiplicative tuning
    something real to adjust, and the caller still records the change as pending
    validation before trusting it.
    """
    kp = gains.get('kp')
    if not finite_number(kp) or kp <= 0:
        return None
    lower, upper = limits[key]
    if key == 'ki':
        # A gentle integral seed: roughly one Kp worth of accumulated error over
        # many PWM periods, so it corrects drift without forcing a long burn.
        horizon = max(7200.0, 20.0 * max(float(pwm_seconds or 0), 60.0))
        seed = kp / horizon
    elif key == 'kd':
        peak_delay = _positive_robust(cycles, ('thermal_response', 'post_off_peak_seconds'))
        if peak_delay is None:
            peak_delay = _positive_robust(cycles, ('settling_time',))
            if peak_delay is not None:
                peak_delay *= 60.0
        delay = min(900.0, max(60.0, peak_delay or 300.0))
        # Convert observed lag into seconds-based derivative gain. This is much
        # smaller than a full physics-derived jump, but enough to reduce demand
        # while temperature is still rising toward the set point.
        seed = kp * delay / 60.0
    else:
        return None
    if not finite_number(seed) or seed <= 0:
        return None
    return min(upper, max(lower, seed))


class PIDAdaptation:
    """Separate proposing gains, committing them, and validating their effect."""

    def __init__(self, limits):
        self.limits = limits
        self.pending = None
        self.last_change = None
        self.reason = 'collecting_metrics'

    def propose(self, records, gains, now, pwm_seconds):
        if self.pending:
            self.reason = 'validating_previous_change'
            return None
        if not finite_number(gains.get('kp')) or gains['kp'] <= 0:
            self.reason = 'history_based_initialization_required'
            return None
        if not all(finite_number(gains.get(k)) and gains[k] >= 0 for k in KEYS):
            self.reason = 'invalid_baseline_gains'
            return None
        if self.last_change is not None and now - self.last_change < 36 * 3600:
            self.reason = 'cooldown'
            return None
        cycles = comparable_cycles(records, gains, now, self.last_change)
        required = 6 if self.last_change is None else 8
        if len(cycles) < required:
            self.reason = 'insufficient_comparable_cycles'
            return None
        averages = {k: robust_average([r[k] for r in cycles[-required:]])[0] for k in METRICS}
        results = evaluate_pid_rules(*(averages[k] for k in METRICS))
        if pwm_seconds > 0:
            results = [r for r in results if r.rule.priority != 3]
        results = resolve_rule_conflicts(results, detect_rule_conflicts(results))
        if not results:
            self.reason = 'no_rule_triggered'
            return None
        candidate = {}
        seeded = []
        for key in KEYS:
            factor = prod(getattr(r, key + '_factor') for r in results)
            # Per-proposal trust region; distinct from upstream's lifetime caps.
            factor = min(1.2, max(0.8, factor))
            lower, upper = self.limits[key]
            if not lower <= gains[key] <= upper:
                self.reason = 'baseline_outside_controller_bounds'
                return None
            if gains[key] == 0:
                if factor <= 1.0:
                    candidate[key] = 0
                    continue
                seeded_gain = _seed_missing_gain(key, cycles[-required:], gains, self.limits, pwm_seconds)
                if seeded_gain is None:
                    self.reason = f'{key}_initialization_unavailable'
                    return None
                candidate[key] = seeded_gain
                seeded.append(key)
                continue
            candidate[key] = min(upper, max(lower, gains[key] * factor))
        if candidate == gains:
            self.reason = 'no_gain_change'
            return None
        self.reason = 'proposal_ready'
        return {'old': dict(gains), 'new': candidate, 'baseline': averages,
                'reasons': [r.reason for r in results],
                'seeded_gains': seeded, 'proposed_at': now}

    def committed(self, proposal, now):
        """Only call after the owner has actually applied and saved the gains."""
        self.pending = deepcopy(proposal)
        self.pending.update(applied_at=now, cycles=[])
        self.last_change = now
        self.reason = 'validating_previous_change'

    def validate(self, cycle):
        pending = self.pending
        if pending and len(pending['cycles']) >= 5:
            return self._validation_outcome()
        if not pending or not measured_cycle(cycle) or cycle.get('gains') != pending['new']:
            return None
        if cycle['started'] <= pending['applied_at']:
            return None
        if any(r['started'] == cycle['started'] for r in pending['cycles']):
            return None
        pending['cycles'].append(deepcopy(cycle))
        if len(pending['cycles']) < 5:
            return None
        return self._validation_outcome()

    def _validation_outcome(self):
        pending = self.pending
        degraded = []
        for metric in ('overshoot', 'undershoot'):
            baseline = pending['baseline'][metric]
            measured = mean(r[metric] for r in pending['cycles'])
            if measured - baseline > 0.3 * max(baseline, 0.1):
                degraded.append(metric)
        if degraded:
            self.reason = 'rollback_required:' + ','.join(degraded)
            return dict(pending['old'])
        self.pending = None
        self.reason = 'validated'
        return None

    def rollback_committed(self, now):
        self.pending = None
        self.last_change = now
        self.reason = 'rolled_back'

    def snapshot(self):
        return deepcopy({'pending': self.pending, 'last_change': self.last_change})

    def restore(self, data, current_gains, now):
        """Restore validation only when saved gains match the actual controller.

        Return False on invalid state so the owner can withhold auto-application;
        do not silently treat an unreadable pending change as a new installation.
        """
        if not isinstance(data, dict):
            return False
        last_change = data.get('last_change')
        if last_change is not None and not (
            finite_number(last_change) and 0 <= last_change <= now
        ):
            return False
        pending = data.get('pending')
        if pending is not None:
            if not isinstance(pending, dict) or pending.get('new') != current_gains:
                return False
            for name in ('old', 'new'):
                gains = pending.get(name)
                if not isinstance(gains, dict) or not all(
                    finite_number(gains.get(k))
                    and self.limits[k][0] <= gains[k] <= self.limits[k][1] for k in KEYS
                ):
                    return False
            baseline = pending.get('baseline')
            if not isinstance(baseline, dict) or not all(
                finite_number(baseline.get(k))
                and baseline[k] >= 0 for k in METRICS
            ):
                return False
            applied = pending.get('applied_at')
            if not finite_number(applied) or applied != last_change:
                return False
            cycles = pending.get('cycles')
            if not isinstance(cycles, list) or len(cycles) > 5:
                return False
            starts = set()
            for cycle in cycles:
                if not measured_cycle(cycle) or cycle.get('gains') != current_gains:
                    return False
                if not applied < cycle['started'] < cycle['completed'] <= now or cycle['started'] in starts:
                    return False
                starts.add(cycle['started'])
        self.pending = deepcopy(pending)
        self.last_change = last_change
        self.reason = 'validating_previous_change' if pending else 'restored'
        return True
