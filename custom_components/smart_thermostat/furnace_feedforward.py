"""Furnace residual-heat guard.

This module is deliberately independent from Home Assistant and actuator
commands. It protects against an immediate re-fire only while the furnace is
still measurably releasing heat from the preceding call. PID, house-average,
and outdoor-temperature learning remain the thermostat's control model.
"""

from math import isfinite
from statistics import median


MIN_CYCLES = 6
MAX_RECORD_AGE_SECONDS = 14 * 86400


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _slope(samples):
    """Return the final rate in degrees C per minute, or None without two samples."""
    if len(samples) < 2:
        return None
    older, newer = samples[-2], samples[-1]
    dt = newer[0] - older[0]
    if not _finite(dt) or dt <= 0:
        return None
    return (newer[1] - older[1]) * 60.0 / dt


def _baseline(records, now):
    """Learn the resting furnace temperature at clean heat-call starts."""
    candidates = []
    for record in records:
        completed = record.get('completed')
        value = record.get('furnace_baseline')
        if not _finite(completed) or not _finite(value):
            continue
        if 0 <= now - completed <= MAX_RECORD_AGE_SECONDS:
            candidates.append(value)
    if len(candidates) < MIN_CYCLES:
        return None
    return median(candidates)


class FurnaceFeedForward:
    """Stateful live sampler for the pre-fire residual-heat guard."""

    def __init__(self):
        self._samples = []
        self._room_samples = []
        self._coast_started = None
        self._coast_peak_temperature = None
        self.last_diagnostics = {'status': 'waiting_for_sensor'}

    def observe(self, now, temperature):
        if not all(_finite(value) for value in (now, temperature)):
            self._samples = []
            self.last_diagnostics = {'status': 'invalid_live_sensor'}
            return
        self._samples.append((now, temperature))
        self._samples = self._samples[-8:]

    def observe_room(self, now, temperature):
        """Record room temperature for the manual residual-heat guard."""
        if not all(_finite(value) for value in (now, temperature)):
            self._room_samples = []
            return
        self._room_samples.append((now, temperature))
        self._room_samples = self._room_samples[-8:]

    def begin_coast(self, now):
        """Begin a live residual-heat coast after a heat call ends."""
        if _finite(now):
            self._coast_started = now
            self._coast_peak_temperature = (
                self._samples[-1][1] if self._samples else None)

    def resume_coast_from_live_furnace_heat(self, records, now):
        """Rebuild a lost coast marker from live, measured residual heat.

        This is used only after a reload while the physical heat output is
        already off. It requires a learned resting baseline and never acts as
        an actuator threshold: it only restores the normal coast guard until
        the measured furnace energy has dissipated.
        """
        if self._coast_started is not None or not _finite(now):
            return False
        furnace_temperature = self._samples[-1][1] if self._samples else None
        baseline = _baseline(records, now)
        if (not _finite(furnace_temperature) or not _finite(baseline)
                or furnace_temperature <= baseline):
            return False
        self._coast_started = now
        self._coast_peak_temperature = furnace_temperature
        self.last_diagnostics = {
            'status': 'coast_resumed_from_live_furnace_heat',
            'coast_started': round(now, 3),
            'furnace_temperature_c': round(furnace_temperature, 3),
            'baseline_c': round(baseline, 3),
        }
        return True

    def coast_active(self, records, now):
        """Return whether the preceding heat call is still actively purging."""
        if self._coast_started is None:
            return False

        furnace_temperature = self._samples[-1][1] if self._samples else None
        furnace_slope = _slope(self._samples)
        room_slope = _slope(self._room_samples)
        if not _finite(furnace_temperature) or furnace_slope is None or room_slope is None:
            # This guard may defer a call only when the live sensors prove that
            # the prior cycle is still delivering heat. Missing data must not
            # become a second thermostat or hold the home cold indefinitely.
            self._coast_started = None
            self.last_diagnostics = {'status': 'coast_unverified'}
            return False

        baseline = _baseline(records, now)
        if (not _finite(self._coast_peak_temperature)
                or furnace_temperature > self._coast_peak_temperature):
            self._coast_peak_temperature = furnace_temperature

        # A falling furnace temperature is still stored heat.  The next burn
        # must wait until the observed post-off heat has returned to the
        # furnace's learned resting level; this remains a pre-fire guard and
        # never changes PID demand or turns an active burn off.
        if baseline is not None and furnace_temperature > baseline:
            self.last_diagnostics = {
                'status': 'coast_hold_stored_furnace_heat',
                'coast_started': round(self._coast_started, 3),
                'furnace_temperature_c': round(furnace_temperature, 3),
                'furnace_peak_c': round(self._coast_peak_temperature, 3),
                'furnace_slope_c_per_min': round(furnace_slope, 3),
                'room_slope_c_per_min': round(room_slope, 3),
                'baseline_c': round(baseline, 3),
            }
            return True

        if furnace_slope > 0:
            self.last_diagnostics = {
                'status': 'coast_hold_active_purge',
                'coast_started': round(self._coast_started, 3),
                'furnace_temperature_c': round(furnace_temperature, 3),
                'furnace_slope_c_per_min': round(furnace_slope, 3),
                'room_slope_c_per_min': round(room_slope, 3),
                'baseline_c': round(baseline, 3) if baseline is not None else None,
            }
            return True

        self._coast_started = None
        self._coast_peak_temperature = None
        self.last_diagnostics = {
            'status': 'coast_released',
            'furnace_slope_c_per_min': round(furnace_slope, 3),
            'room_slope_c_per_min': round(room_slope, 3),
            'baseline_c': round(baseline, 3) if baseline is not None else None,
        }
        return False

    def snapshot(self):
        return {
            'coast_started': self._coast_started,
            'coast_peak_temperature': self._coast_peak_temperature,
        }

    def restore(self, data):
        if not isinstance(data, dict):
            return
        started = data.get('coast_started', data.get('manual_coast_started'))
        if _finite(started):
            self._coast_started = started
        peak_temperature = data.get('coast_peak_temperature')
        if _finite(peak_temperature):
            self._coast_peak_temperature = peak_temperature
