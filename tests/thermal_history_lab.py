"""Offline extraction of isolated physical heat pulses from exported history.

No interpolation, thermostat commands, or claims of causal purity. Missing
context IDs are reported; absence of user_id is not proof of automatic control.
"""

from bisect import bisect_right
from collections import Counter
from math import isfinite


def extract_windows(history, sensor_id, climate_id, heater_id, *, post_off_seconds=900):
    if not isinstance(post_off_seconds, (int, float)) or not isfinite(post_off_seconds) or post_off_seconds <= 0:
        raise ValueError('positive post-off observation period required')
    rows = {key: sorted(history.get(key, []), key=lambda r: r['timestamp'])
            for key in (sensor_id, climate_id, heater_id)}
    rejected = Counter()
    if any(not group for group in rows.values()):
        return [], {'rejections': {'missing_entity': 1}, 'windows': 0}
    temperatures, climates, outputs = (rows[key] for key in (sensor_id, climate_id, heater_id))
    temp_times = [r['timestamp'] for r in temperatures]
    climate_times = [r['timestamp'] for r in climates]
    edges = []
    for row in outputs:
        if not edges or edges[-1]['state'] != row['state']:
            edges.append(row)
    windows = []
    for index in range(1, len(edges) - 1):
        before, on, off = edges[index - 1:index + 2]
        if on['state'] != 'on':
            continue
        if before['state'] != 'off' or off['state'] != 'off':
            rejected['unknown_output_boundary'] += 1
            continue
        on_time, off_time = on['timestamp'], off['timestamp']
        if off_time - on_time < 120:
            rejected['short_pulse'] += 1
            continue
        end = off_time + post_off_seconds
        if index + 2 < len(edges) and edges[index + 2]['timestamp'] <= end:
            rejected['output_changed_during_coast'] += 1
            continue
        first = bisect_right(temp_times, on_time) - 1
        if first < 0 or on_time - temp_times[first] > 120:
            rejected['missing_initial_temperature'] += 1
            continue
        start = temp_times[first]
        if start < before['timestamp']:
            rejected['unobserved_prior_off'] += 1
            continue
        selected = temperatures[first:bisect_right(temp_times, end)]
        if not selected or end - selected[-1]['timestamp'] > 120:
            rejected['incomplete_coast_history'] += 1
            continue
        try:
            samples = [(r['timestamp'], float(r['state'])) for r in selected]
        except (ValueError, TypeError):
            rejected['invalid_temperature'] += 1
            continue
        if (any(not isfinite(value) for _, value in samples)
                or any(not 0 < b[0] - a[0] <= 120 for a, b in zip(samples, samples[1:]))):
            rejected['invalid_temperature_or_gap'] += 1
            continue
        climate_first = bisect_right(climate_times, start) - 1
        if climate_first < 0:
            rejected['missing_climate_context'] += 1
            continue
        context = climates[climate_first:bisect_right(climate_times, end)]
        def signature(row):
            attributes = row.get('attributes', {})
            return (row['state'], attributes.get('pid_mode'), attributes.get('temperature'),
                    *(attributes.get(k) for k in ('kp', 'ki', 'kd')))
        expected = signature(context[0])
        if expected[:2] != ('heat', 'auto') or any(signature(r) != expected for r in context):
            rejected['changed_or_inactive_controller'] += 1
            continue
        output_context = [r for r in outputs if start <= r['timestamp'] <= end]
        if any(r.get('user_id') for r in [*context, *output_context]):
            rejected['explicit_manual_context'] += 1
            continue
        if windows and start <= windows[-1]['samples'][-1][0]:
            rejected['overlapping_window'] += 1
            continue
        windows.append({
            'samples': samples, 'on_intervals': [(on_time, off_time)],
            'prior_off_seconds': start - before['timestamp'],
            'context_ids_complete': all('user_id' in r for r in [*context, *output_context]),
        })
    return windows, {
        'windows': len(windows), 'rejections': dict(rejected),
        'context_ids_complete': bool(windows) and all(w['context_ids_complete'] for w in windows),
        'status': 'offline_observations_only',
    }
