"""Reconstruct physical furnace response from Recorder history.

This intentionally does not read a climate entity or a setpoint.  The heat
remaining in the furnace after a burner-off edge is a physical measurement,
so it can be learned from the output, room, and furnace sensors even when a
previous thermostat entity has been replaced.
"""

from collections import Counter
from itertools import groupby
from math import isfinite


MIN_RUNTIME_SECONDS = 90
MIN_COAST_SECONDS = 300
MAX_COAST_SECONDS = 1200
MAX_RUNTIME_SECONDS = 1800


def _number(row):
    try:
        value = float(row['state'])
    except (KeyError, TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _append_sample(samples, timestamp, value):
    if value is None:
        return
    if not samples or samples[-1] != (timestamp, value):
        samples.append((timestamp, value))


def _finalize(cycle, completed_at, rejections):
    if cycle is None:
        return None
    if cycle['manual']:
        rejections['manual_output_change'] += 1
        return None
    stopped = cycle.get('stopped')
    room_at_stop = cycle.get('room_at_stop')
    if stopped is None or room_at_stop is None:
        rejections['incomplete_cycle'] += 1
        return None
    runtime = stopped - cycle['started']
    if not MIN_RUNTIME_SECONDS <= runtime <= MAX_RUNTIME_SECONDS:
        rejections['runtime_out_of_range'] += 1
        return None
    coast_end = min(completed_at, stopped + MAX_COAST_SECONDS)
    room_after = [value for timestamp, value in cycle['room_samples']
                  if stopped <= timestamp <= coast_end]
    if coast_end - stopped < MIN_COAST_SECONDS or len(room_after) < 2:
        rejections['insufficient_coast_observation'] += 1
        return None
    at_stop_furnace = [(timestamp, value) for timestamp, value in cycle['furnace_samples']
                       if timestamp <= stopped]
    if cycle['furnace_baseline'] is None or len(at_stop_furnace) < 2:
        rejections['insufficient_furnace_observation'] += 1
        return None
    return {
        'started': cycle['started'],
        'stopped': stopped,
        'completed': coast_end,
        'runtime_seconds': runtime,
        'coast': max(0.0, max(room_after) - room_at_stop),
        'furnace_baseline': cycle['furnace_baseline'],
        'furnace_samples': cycle['furnace_samples'],
        'source': 'furnace_response_history',
    }


def replay_furnace_response_history(history, sensor_id, heater_ids,
                                    furnace_sensor_id, start, end):
    """Extract clean completed furnace response cycles from an explicit window."""
    required = {sensor_id, furnace_sensor_id, *heater_ids}
    report = {
        'window_start': start,
        'window_end': end,
        'available_entities': sorted(entity for entity in required if history.get(entity)),
    }
    if not required.issubset(history):
        report.update(status='missing_history_entities', completed_cycles=0, rejections={})
        return [], report

    events = []
    for entity_id in sorted(required):
        rows = history[entity_id]
        prior = [row for row in rows if row['timestamp'] < start]
        if prior:
            events.append((start, entity_id, max(prior, key=lambda row: row['timestamp'])))
        events.extend((row['timestamp'], entity_id, row) for row in rows
                      if start <= row['timestamp'] <= end)
    events.sort(key=lambda event: event[0])

    current = {}
    active = None
    cooling = None
    completed = []
    rejections = Counter()
    was_heating = False

    for timestamp, batch in groupby(events, key=lambda event: event[0]):
        changed = []
        manual = False
        for _, entity_id, row in batch:
            previous = current.get(entity_id)
            current[entity_id] = row
            changed.append(entity_id)
            if (entity_id in heater_ids and row.get('user_id')
                    and (previous is None or previous.get('state') != row.get('state'))):
                manual = True

        heater_states = [current.get(entity_id, {}).get('state') for entity_id in heater_ids]
        heating = any(state == 'on' for state in heater_states)
        sensor_value = _number(current.get(sensor_id, {}))
        furnace_value = _number(current.get(furnace_sensor_id, {}))

        if not was_heating and heating:
            record = _finalize(cooling, timestamp, rejections)
            if record is not None:
                completed.append(record)
            cooling = None
            if sensor_value is None or furnace_value is None:
                rejections['missing_start_measurement'] += 1
                active = None
            else:
                active = {
                    'started': timestamp,
                    'furnace_baseline': furnace_value,
                    'furnace_samples': [(timestamp, furnace_value)],
                    'room_samples': [(timestamp, sensor_value)],
                    'manual': manual,
                }
        elif was_heating and not heating:
            if active is not None:
                active['stopped'] = timestamp
                active['room_at_stop'] = sensor_value
                active['manual'] = active['manual'] or manual
                cooling = active
            active = None

        target = active if heating else cooling
        if target is not None:
            target['manual'] = target['manual'] or manual
            if sensor_id in changed:
                _append_sample(target['room_samples'], timestamp, sensor_value)
            if furnace_sensor_id in changed:
                _append_sample(target['furnace_samples'], timestamp, furnace_value)
        was_heating = heating

    record = _finalize(cooling, end, rejections)
    if record is not None:
        completed.append(record)
    report.update(
        status='ok',
        completed_cycles=len(completed),
        rejections=dict(rejections),
    )
    return completed, report
