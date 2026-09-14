"""Observed response evidence, not an identified plant or PID recommendation.

Keep heating intervals separate from PWM gaps and post-off coast. Temperature
rise per heater-on second is a net observation, not delivered furnace power:
heat loss, other heat sources, and sensor lag are not independently identified.
"""

from math import isfinite
from statistics import median


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def measure_response(samples, intervals):
    """Measure complete on intervals with exact recorded temperature endpoints."""
    if not samples or not intervals:
        return None
    if any(not _finite(t) or not _finite(value) for t, value in samples):
        return None
    if any(right[0] <= left[0] for left, right in zip(samples, samples[1:])):
        return None
    temperatures = dict(samples)
    runtime = 0.0
    on_rise = 0.0
    previous_stop = None
    for start, stop in intervals:
        if (not _finite(start) or not _finite(stop) or start >= stop
                or start not in temperatures or stop not in temperatures
                or (previous_stop is not None and start < previous_stop)):
            return None
        runtime += stop - start
        on_rise += temperatures[stop] - temperatures[start]
        previous_stop = stop
    stopped = intervals[-1][1]
    after = [(t, value) for t, value in samples if t >= stopped]
    # First observed maximum: a plateau must not fabricate a longer coast lag.
    peak_at, peak = max(after, key=lambda point: point[1])
    return {
        'version': 1,
        'on_intervals': len(intervals),
        'on_seconds': runtime,
        'on_temperature_change_c': on_rise,
        'net_on_rate_c_per_hour': on_rise * 3600 / runtime,
        'post_off_rise_c': peak - temperatures[stopped],
        'post_off_peak_seconds': peak_at - stopped,
        'post_off_observed_seconds': samples[-1][0] - stopped,
    }


def summarize_responses(records):
    """Expose evidence coverage without asserting gains can be initialized."""
    responses = []
    for record in records:
        response = record.get('thermal_response')
        if not isinstance(response, dict) or response.get('version') != 1:
            continue
        keys = ('on_seconds', 'on_temperature_change_c', 'net_on_rate_c_per_hour',
                'post_off_rise_c', 'post_off_peak_seconds', 'post_off_observed_seconds')
        if not all(_finite(response.get(key)) for key in keys):
            continue
        if (response['on_seconds'] <= 0 or response['post_off_rise_c'] < 0
                or not 0 <= response['post_off_peak_seconds'] <= response['post_off_observed_seconds']):
            continue
        responses.append(response)
    return {
        'status': 'observations_only' if responses else 'insufficient_observations',
        'response_count': len(responses),
        'nonpositive_on_rise_count': sum(r['on_temperature_change_c'] <= 0 for r in responses),
        'median_net_on_rate_c_per_hour': median(r['net_on_rate_c_per_hour'] for r in responses)
            if responses else None,
        'median_post_off_rise_c': median(r['post_off_rise_c'] for r in responses) if responses else None,
        'median_post_off_peak_seconds': median(r['post_off_peak_seconds'] for r in responses)
            if responses else None,
        'initial_gains': None,
        'initialization_blocker': 'plant_identification_and_validation_required',
    }
