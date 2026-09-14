"""Lossless config-entry serialization and explicit UI edit precedence."""

from datetime import timedelta
from enum import Enum
from math import isfinite

SECTIONS = {
    "controller": ("name", "heater", "cooler", "target_sensor", "outdoor_sensor",
                   "ac_mode", "invert_heater", "force_off_state"),
    "temperatures": ("min_temp", "max_temp", "target_temp", "cold_tolerance",
                     "hot_tolerance", "precision", "target_temp_step", "initial_hvac_mode"),
    "presets": ("preset_sync_mode", "away_temp", "eco_temp", "boost_temp",
                "comfort_temp", "home_temp", "sleep_temp", "activity_temp"),
    "timing": ("keep_alive", "min_cycle_duration", "min_off_cycle_duration",
               "min_cycle_duration_pid_off", "min_off_cycle_duration_pid_off",
               "sampling_period", "sensor_stall", "pwm"),
    "pid": ("kp", "ki", "kd", "ke", "derivative_filter_alpha", "adaptive_observe",
            "adaptive_learning", "autotune", "noiseband", "lookback", "boost_pid_off"),
    "output": ("output_precision", "output_min", "output_max", "out_clamp_low",
               "out_clamp_high", "output_safety", "debug"),
}
FIELDS = frozenset(key for fields in SECTIONS.values() for key in fields)
PRESETS = tuple(key for key in SECTIONS["presets"] if key.endswith("_temp"))
DURATIONS = frozenset(SECTIONS["timing"]) | {"lookback"}
BOOLEAN_FIELDS = {"ac_mode", "invert_heater", "force_off_state", "adaptive_observe",
                  "adaptive_learning", "boost_pid_off", "debug"}
RESTORABLE = (*PRESETS, "kp", "ki", "kd", "ke", "target_temp")


def serialize_configuration(config):
    """Normalize validated YAML (including timedeltas) without losing fractions."""
    result = {}
    for key, value in config.items():
        if key not in FIELDS and key != "unique_id":
            continue
        if isinstance(value, timedelta):
            value = {"seconds": value.total_seconds()}
        elif isinstance(value, Enum):
            value = value.value
        elif isinstance(value, (list, tuple)):
            value = list(value)
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"Nonfinite setting: {key}")
        result[key] = value
    return result


def replace_section(config, section, submitted):
    """Omitted optional fields are removed, not resurrected from old options."""
    fields = SECTIONS[section]
    if set(submitted) - set(fields):
        raise ValueError("Unexpected field")
    return {**{k: v for k, v in config.items() if k not in fields}, **submitted}


def restore_attributes(attributes, configuration):
    """Use new UI values only for settings explicitly changed since last load."""
    result = dict(attributes)
    previous = attributes.get("configured_settings")
    if not isinstance(previous, dict):
        return result  # First YAML import retains existing runtime state.
    gains_changed = False
    for key in RESTORABLE:
        if configuration.get(key) == previous.get(key):
            continue
        state_key = "temperature" if key == "target_temp" else key
        result.pop(state_key, None)
        if key in ("kp", "ki", "kd", "ke"):
            result.pop(key.capitalize(), None)
            gains_changed = True
        if configuration.get(key) is not None:
            result[state_key] = configuration[key]
        elif key in PRESETS and attributes.get("preset_mode") == key.removesuffix("_temp"):
            result["preset_mode"] = "none"
    if gains_changed:
        result.pop("pid_i", None)
    return result
