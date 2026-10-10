"""Measured residual-heat interlock for an already-finished heat call."""

from math import isfinite
from statistics import median


MIN_CYCLES = 6
MAX_RECORD_AGE_SECONDS = 14 * 86400
MAX_COAST_SECONDS = 30 * 60
SENSOR_NOISE_C = 0.5
RELEASE_FRACTION = 0.10


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _slope(samples):
    if len(samples) < 2:
        return None
    older, newer = samples[-2], samples[-1]
    dt = newer[0] - older[0]
    if not _finite(dt) or dt <= 0:
        return None
    return (newer[1] - older[1]) * 60.0 / dt


def _baseline(records, now):
    """Return a recent resting-furnace baseline only when established."""
    values = []
    for record in records:
        completed = record.get('completed')
        baseline = record.get('furnace_baseline')
        if (_finite(completed) and _finite(baseline)
                and 0 <= now - completed <= MAX_RECORD_AGE_SECONDS):
            values.append(baseline)
    return median(values) if len(values) >= MIN_CYCLES else None


class FurnaceFeedForward:
    """Read-only pre-fire interlock for a thermostat-owned prior heat call."""

    def __init__(self):
        self._samples = []
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

    def begin_coast(self, now):
        """Record a successful normal OFF command from this thermostat."""
        if _finite(now):
            self._coast_started = now
            self._coast_peak_temperature = self._samples[-1][1] if self._samples else None

    def clear_coast(self):
        self._coast_started = None
        self._coast_peak_temperature = None

    def blocks_new_heat_call(self, records, now):
        """Return true only while the preceding call has measured stored heat.

        This method never changes demand, controls no output, and is called only
        immediately before a new OFF-to-ON output command.
        """
        if self._coast_started is None:
            return False
        if not _finite(now) or now - self._coast_started > MAX_COAST_SECONDS:
            self.clear_coast()
            self.last_diagnostics = {'status': 'coast_expired'}
            return False

        current = self._samples[-1][1] if self._samples else None
        slope = _slope(self._samples)
        if not _finite(current) or slope is None:
            self.last_diagnostics = {'status': 'coast_unverified'}
            return False

        baseline = _baseline(records, now)
        if (not _finite(self._coast_peak_temperature)
                or current > self._coast_peak_temperature):
            self._coast_peak_temperature = current

        if baseline is None:
            if slope > 0:
                self.last_diagnostics = {
                    'status': 'coast_hold_rising_furnace',
                    'furnace_temperature_c': round(current, 3),
                    'furnace_slope_c_per_min': round(slope, 3),
                }
                return True
            self.clear_coast()
            self.last_diagnostics = {'status': 'coast_released_without_baseline'}
            return False

        peak = self._coast_peak_temperature
        release = baseline + max(SENSOR_NOISE_C, (peak - baseline) * RELEASE_FRACTION)
        if current > release:
            self.last_diagnostics = {
                'status': 'coast_hold_stored_furnace_heat',
                'furnace_temperature_c': round(current, 3),
                'furnace_peak_c': round(peak, 3),
                'furnace_slope_c_per_min': round(slope, 3),
                'baseline_c': round(baseline, 3),
                'release_temperature_c': round(release, 3),
            }
            return True

        self.clear_coast()
        self.last_diagnostics = {
            'status': 'coast_released',
            'furnace_temperature_c': round(current, 3),
            'baseline_c': round(baseline, 3),
            'release_temperature_c': round(release, 3),
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
        started = data.get('coast_started')
        if _finite(started):
            self._coast_started = started
        peak = data.get('coast_peak_temperature')
        if _finite(peak):
            self._coast_peak_temperature = peak
