"""Calibrated furnace residual-heat compensation.

This module is deliberately independent from Home Assistant and actuator
commands.  The furnace sensor can only reduce a positive PID demand after
recent, comparable completed cycles establish a measured relationship between
furnace heat still stored at burner-off and the room-temperature coast that
follows.  It never supplies an on/off threshold.
"""

from math import isfinite
from statistics import median


MIN_CYCLES = 6
MAX_RECORD_AGE_SECONDS = 14 * 86400
MIN_RUNTIME_SECONDS = 60


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


def _furnace_features(record):
    """Extract the furnace state at the observed burner-off boundary."""
    samples = record.get('furnace_samples')
    if not isinstance(samples, list) or len(samples) < 2:
        return None
    valid = [(t, value) for t, value in samples if _finite(t) and _finite(value)]
    if len(valid) < 2:
        return None
    stop = record.get('stopped')
    if not _finite(stop):
        return None
    # Samples can continue while the room settles.  Use readings available at
    # the actual off edge, never later heat that has already been delivered.
    at_stop = [sample for sample in valid if sample[0] <= stop]
    if len(at_stop) < 2:
        return None
    temperature = at_stop[-1][1]
    slope = _slope(at_stop)
    if slope is None:
        return None
    return temperature, slope


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


def predict_pending_rise(records, now, furnace_temperature, furnace_slope,
                         runtime_seconds, heating):
    """Predict residual room rise if the current positive call ended now.

    Returns ``(rise_c, diagnostics)``.  A ``None`` rise means the model is
    deliberately inactive; the ordinary PID must be used unchanged.
    """
    if not all(_finite(value) for value in (now, furnace_temperature, runtime_seconds)):
        return None, {'status': 'invalid_live_sensor'}
    if furnace_slope is not None and not _finite(furnace_slope):
        return None, {'status': 'invalid_live_slope'}
    baseline = _baseline(records, now)
    if baseline is None:
        return None, {'status': 'insufficient_baseline'}
    live_excess = max(0.0, furnace_temperature - baseline)
    if live_excess <= 0:
        return 0.0, {'status': 'at_rest', 'baseline_c': round(baseline, 3)}

    comparable = []
    for record in records:
        completed = record.get('completed')
        coast = record.get('coast')
        runtime = record.get('runtime_seconds')
        features = _furnace_features(record)
        if (not all(_finite(value) for value in (completed, coast, runtime))
                or features is None or not 0 <= now - completed <= MAX_RECORD_AGE_SECONDS
                or runtime < MIN_RUNTIME_SECONDS or coast < 0):
            continue
        temperature, slope = features
        excess = max(0.0, temperature - baseline)
        if excess <= 0:
            continue
        # A live call is compared only to physically similar completed burns.
        if not 0.5 * runtime <= runtime_seconds <= 2.0 * runtime:
            continue
        slope_distance = abs((furnace_slope or 0.0) - slope)
        distance = (abs(live_excess - excess) / max(excess, 1.0)
                    + abs(runtime_seconds - runtime) / max(runtime, 1.0)
                    + slope_distance / max(abs(slope), 0.1))
        comparable.append((distance, coast))
    if len(comparable) < MIN_CYCLES:
        return None, {'status': 'insufficient_comparable_cycles', 'baseline_c': round(baseline, 3),
                      'comparable_cycles': len(comparable)}
    comparable.sort(key=lambda item: item[0])
    # Conservative lower quartile: never let an unusually high coast estimate
    # create an aggressive reduction in heat demand.
    values = sorted(coast for _, coast in comparable[:9])
    predicted = values[(len(values) - 1) // 4]
    return predicted, {
        'status': 'calibrated',
        'baseline_c': round(baseline, 3),
        'comparable_cycles': len(comparable),
        'pending_rise_c': round(predicted, 3),
        'heating': bool(heating),
    }


class FurnaceFeedForward:
    """Stateful live sampler with a pure, conservative prediction API."""

    def __init__(self):
        self._samples = []
        self._room_samples = []
        self._manual_coast_started = None
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

    def begin_manual_coast(self, now):
        """Respect a user stop until live telemetry shows stored heat is spent."""
        if _finite(now):
            self._manual_coast_started = now

    def manual_coast_active(self, records, now):
        """Return whether a manual stop is still releasing useful heat.

        This is not a fixed furnace-temperature cutoff. It uses the learned
        resting temperature plus live furnace and room slopes, so a user stop
        cannot be immediately overwritten while heat is still reaching the home.
        """
        if self._manual_coast_started is None:
            return False

        furnace_temperature = self._samples[-1][1] if self._samples else None
        furnace_slope = _slope(self._samples)
        room_slope = _slope(self._room_samples)
        if not _finite(furnace_temperature) or furnace_slope is None or room_slope is None:
            self.last_diagnostics = {
                'status': 'manual_coast_waiting_for_telemetry',
                'manual_coast_started': round(self._manual_coast_started, 3),
            }
            return True

        baseline = _baseline(records, now)
        above_rest = baseline is None or furnace_temperature > baseline
        thermal_release = furnace_slope > 0 or (above_rest and room_slope > 0)
        if thermal_release:
            self.last_diagnostics = {
                'status': 'manual_coast_hold',
                'manual_coast_started': round(self._manual_coast_started, 3),
                'furnace_temperature_c': round(furnace_temperature, 3),
                'furnace_slope_c_per_min': round(furnace_slope, 3),
                'room_slope_c_per_min': round(room_slope, 3),
                'baseline_c': round(baseline, 3) if baseline is not None else None,
            }
            return True

        self._manual_coast_started = None
        self.last_diagnostics = {
            'status': 'manual_coast_released',
            'furnace_slope_c_per_min': round(furnace_slope, 3),
            'room_slope_c_per_min': round(room_slope, 3),
            'baseline_c': round(baseline, 3) if baseline is not None else None,
        }
        return False

    def snapshot(self):
        return {'manual_coast_started': self._manual_coast_started}

    def restore(self, data):
        if isinstance(data, dict) and _finite(data.get('manual_coast_started')):
            self._manual_coast_started = data['manual_coast_started']

    def pending_rise(self, records, now, runtime_seconds, heating):
        if not self._samples:
            self.last_diagnostics = {'status': 'waiting_for_sensor'}
            return None
        rise, diagnostics = predict_pending_rise(
            records, now, self._samples[-1][1], _slope(self._samples), runtime_seconds, heating)
        self.last_diagnostics = diagnostics
        return rise
