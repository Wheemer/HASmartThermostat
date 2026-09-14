"""Map measured heat-pulse history to the ported Adaptive Climate functions.

Metric definitions follow managers/cycle_metrics.py at upstream 0d32c1f.
Cycle boundary selection is still the local observer's responsibility.
"""

from datetime import datetime, timezone

from .adaptive_cycle_analysis import (
    calculate_overshoot, calculate_undershoot, calculate_rise_time,
    calculate_settling_time, calculate_settling_mae, count_oscillations,
)


def measure_cycle(samples, target, stopped, rise_tolerance=0.0):
    history = [(datetime.fromtimestamp(t, timezone.utc), value) for t, value in samples]
    off_time = datetime.fromtimestamp(stopped, timezone.utc)
    settling = [(t, value) for t, value in history if t >= off_time]
    return {
        'overshoot': calculate_overshoot(history, target),
        # Do not treat a cold starting room as steady-state undershoot.
        'undershoot': calculate_undershoot(settling, target),
        'oscillations': count_oscillations(history, target),
        'rise_time': calculate_rise_time(history, history[0][1], target,
                                        threshold=rise_tolerance) if history else None,
        'settling_time': calculate_settling_time(history, target, reference_time=off_time),
        'settling_mae': calculate_settling_mae(history, target, off_time),
    }
