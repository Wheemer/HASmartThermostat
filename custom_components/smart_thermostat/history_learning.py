"""Replay recorded state events without issuing commands or changing setpoints."""

from collections import Counter
from itertools import groupby
from math import isfinite

from .adaptive import ThermalObserver, has_session_timing
from .thermal_response import summarize_responses


def replay_history(history, sensor_id, climate_id, heater_ids, start, end,
                   fallback_pwm_seconds=None):
    """Extract complete thermal cycles from an explicit, bounded history window.

    Rows have timestamp, state, attributes and optional user_id fields. Recorder
    start-state rows establish context, but cannot fabricate a heat-on edge.
    """
    observer = ThermalObserver()
    current = {}
    rejected = Counter()
    required = {sensor_id, climate_id, *heater_ids}
    events = []
    if required.issubset(history):
        for entity_id in sorted(required):
            rows = history[entity_id]
            prior = [row for row in rows if row['timestamp'] < start]
            if prior:
                # Establish boundary context without inventing a fresh reading
                # or replaying a heat-on transition from before the window.
                events.append((start, entity_id, max(prior, key=lambda row: row['timestamp'])))
            events.extend((row['timestamp'], entity_id, row) for row in rows
                          if start <= row['timestamp'] <= end)
    events.sort(key=lambda event: event[0])
    # Avoid comparing dictionaries when two rows share a timestamp and entity.
    for timestamp, batch in groupby(events, key=lambda event: event[0]):
        manual = False
        for _, entity_id, row in batch:
            previous = current.get(entity_id)
            current[entity_id] = row
            if entity_id in heater_ids and row.get("user_id") and (
                previous is None or previous["state"] != row["state"]
            ):
                manual = True
        if manual:
            observer.invalidate("manual_output_change")
            rejected["manual_output_change"] += 1
        if not required.issubset(current):
            continue
        temperature = current[sensor_id]
        climate = current[climate_id]
        heaters = [current[entity_id]["state"] for entity_id in heater_ids]
        if timestamp - temperature["timestamp"] > observer.max_gap:
            observer.invalidate("stale_temperature")
            rejected["stale_temperature"] += 1
            continue
        try:
            value = float(temperature["state"])
            target = float(climate.get("attributes", {}).get("temperature"))
        except (ValueError, TypeError):
            observer.invalidate("invalid_temperature")
            rejected["invalid_temperature"] += 1
            continue
        known = all(state in ("on", "off") for state in heaters)
        enabled = known and climate["state"] == "heat" and (
            climate.get("attributes", {}).get("pid_mode") == "auto"
        )
        previous_reason = observer.reason
        attributes = climate.get('attributes', {})
        gains = {key: attributes.get(key) for key in ('kp', 'ki', 'kd')}
        if not all(isinstance(value, (int, float)) for value in gains.values()):
            gains = None
        output = attributes.get('control_output')
        demand = (output > 0 if isinstance(output, (int, float)) and not isinstance(output, bool)
                  and isfinite(output) else None)
        demand_debounce_seconds = None
        pwm_seconds = attributes.get('learning_pwm_seconds')
        if pwm_seconds is None and demand is not None:
            pwm_seconds = fallback_pwm_seconds
            demand_debounce_seconds = 0
        observer.sample(timestamp, value, target, any(s == "on" for s in heaters), enabled, gains, demand,
                        pwm_seconds=pwm_seconds,
                        demand_debounce_seconds=demand_debounce_seconds)
        if observer.reason and observer.reason != previous_reason:
            rejected[observer.reason] += 1
    return observer.snapshot(), {
        "window_start": start,
        "window_end": end,
        "available_entities": sorted(entity for entity in required if history.get(entity)),
        "completed_cycles": len(observer.records),
        "demand_sessions": sum(r.get('cycle_basis') == 'demand_session' for r in observer.records),
        "timed_demand_sessions": sum(has_session_timing(r) for r in observer.records),
        "rejections": dict(rejected),
        # A count alone does not establish comparable gains/timing or causality.
        # Automatic gain application applies its own stricter qualification.
        "confidence": "limited",
        "calibration_evidence": summarize_responses(observer.records),
    }


async def import_recent_history(hass, sensor_id, climate_id, heater_ids, end,
                                fallback_pwm_seconds=None):
    """Read the last fourteen days through Recorder's own executor, day by day."""
    from datetime import timedelta
    from functools import partial

    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.helpers.recorder import get_instance

    start = end - timedelta(days=14)
    entities = [sensor_id, climate_id, *heater_ids]
    rows = {entity_id: [] for entity_id in entities}
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=1), end)
        result = await get_instance(hass).async_add_executor_job(partial(
            get_significant_states, hass,
            cursor if cursor == start else cursor - timedelta(microseconds=1), stop, entities,
            include_start_time_state=cursor == start, significant_changes_only=False,
            minimal_response=False, no_attributes=False,
        ))
        for entity_id, states in result.items():
            for state in states:
                timestamp = state.last_updated.timestamp()
                # Adjacent daily reads include a start-state copy. Keep only one.
                row = {"timestamp": timestamp, "state": state.state,
                       "attributes": dict(state.attributes),
                       "user_id": state.context.user_id}
                if rows[entity_id] and timestamp <= rows[entity_id][-1]["timestamp"]:
                    continue
                rows[entity_id].append(row)
        cursor = stop
    return await hass.async_add_executor_job(
        replay_history, rows, sensor_id, climate_id, heater_ids,
        start.timestamp(), end.timestamp(), fallback_pwm_seconds,
    )
