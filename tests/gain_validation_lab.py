"""Conservative offline regression gate, not authorization to apply PID gains."""

from math import isfinite

METRICS = ('overshoot_c', 'last_third_mae_c', 'heat_starts')


def compare_results(baseline, candidate):
    """Withhold any missing/nonfinite or regressing scenario; do not average failures away.

    Zero-regression is a local experimental policy, not a SIMC tuning rule.
    Passing is only eligibility for further tests, never approval for hardware.
    """
    if not baseline or set(baseline) != set(candidate):
        return {'status': 'rejected', 'failures': ['scenario_coverage_mismatch'], 'apply': False}
    failures, improved = [], False
    for scenario in baseline:
        for metric in METRICS:
            before, after = baseline[scenario].get(metric), candidate[scenario].get(metric)
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not isfinite(v) or v < 0
                   for v in (before, after)):
                failures.append(f'{scenario}:{metric}:invalid')
                continue
            if after > before + 1e-9:
                failures.append(f'{scenario}:{metric}:regression')
            elif after < before - 1e-9:
                improved = True
    return {
        'status': 'rejected' if failures else 'eligible_for_further_validation' if improved else 'no_improvement',
        'failures': failures, 'apply': False,
    }
