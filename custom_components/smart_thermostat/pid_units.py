"""Conversions for the equivalent I/D terms, not proportional formulations.

Adaptive Climate computes Ki * error * dt_hours and -Kd * delta / dt_hours.
The installed controller uses dt_seconds. P-on-M is NOT converted to P-on-E.
"""

from math import isfinite


def hourly_to_seconds_id(ki, kd):
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and isfinite(value) and value >= 0 for value in (ki, kd)):
        raise ValueError('I/D gains must be finite nonnegative numbers')
    return ki / 3600.0, kd * 3600.0
