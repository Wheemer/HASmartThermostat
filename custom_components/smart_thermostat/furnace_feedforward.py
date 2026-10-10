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
COAST_REMAINING_ENERGY_FRACTION = 0.15


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


def _learned_post_off_peak_seconds(records, now, runtime_seconds=None):
    """Return the measured room-peak delay after comparable recent heat calls."""
    values = []
    for record in records:
        completed = record.get('completed')
        response = record.get('thermal_response')
        if (not _finite(completed) or not isinstance(response, dict)
                or response.get('version') != 1
                or not 0 <= now - completed <= MAX_RECORD_AGE_SECONDS):
            continue
        peak_seconds = response.get('post_off_peak_seconds')
        observed_seconds = response.get('post_off_observed_seconds')
        observed_runtime = response.get('on_seconds', record.get('runtime_seconds'))
        if (not all(_finite(value) for value in (peak_seconds, observed_seconds))
                or peak_seconds < 0 or peak_seconds > observed_seconds):
            continue
        if (_finite(runtime_seconds) and runtime_seconds > 0
                and (not _finite(observed_runtime)
                     or not 0.5 * runtime_seconds <= observed_runtime <= 2.0 * runtime_seconds)):
            continue
        values.append(peak_seconds)
    # A single cycle is too vulnerable to an unusual door, weather, or sensor
    # event. Three independently observed cycles give the first useful estimate.
    return median(values) if len(values) >= 3 else None


def _furnace_temperature_at_elapsed(record, elapsed_seconds):
    """Return the historical furnace temperature at a burn-relative time."""
    runtime = record.get('runtime_seconds')
    stopped = record.get('stopped')
    started = record.get('started')
    if not all(_finite(value) for value in (runtime, stopped, elapsed_seconds)):
        return None
    if not _finite(started):
        started = stopped - runtime
    target = min(stopped, started + max(0.0, elapsed_seconds))
    samples = record.get('furnace_samples')
    if not isinstance(samples, list):
        return None
    valid = sorted((time, value) for time, value in samples
                   if _finite(time) and _finite(value) and time <= stopped)
    if not valid or target < valid[0][0] or target > valid[-1][0]:
        return None
    previous = valid[0]
    for following in valid[1:]:
        if following[0] >= target:
            if following[0] == previous[0]:
                return following[1]
            ratio = (target - previous[0]) / (following[0] - previous[0])
            return previous[1] + ratio * (following[1] - previous[1])
        previous = following
    return valid[-1][1]


def _phase_comparable_cycles(records, now, furnace_temperature, runtime_seconds, current_baseline):
    """Match an active burn against historical furnace curves at the same phase."""
    comparable = []
    for record in records:
        completed = record.get('completed')
        coast = record.get('coast')
        runtime = record.get('runtime_seconds')
        baseline = record.get('furnace_baseline')
        if (not all(_finite(value) for value in
                    (completed, coast, runtime, baseline))
                or not 0 <= now - completed <= MAX_RECORD_AGE_SECONDS
                or runtime < MIN_RUNTIME_SECONDS or coast < 0
                or runtime_seconds <= 0 or runtime_seconds >= runtime):
            continue
        historical_temperature = _furnace_temperature_at_elapsed(
            record, runtime_seconds)
        if historical_temperature is None:
            continue
        live_excess = max(0.0, furnace_temperature - current_baseline)
        historical_excess = max(0.0, historical_temperature - baseline)
        if historical_excess <= 0:
            continue
        distance = abs(live_excess - historical_excess) / max(
            historical_excess, 1.0)
        comparable.append((distance, coast))
    return comparable


def predict_pending_rise(records, now, furnace_temperature, furnace_slope,
                         runtime_seconds, heating):
    """Predict residual room rise if the current positive call ended now.

    A None rise deliberately leaves the ordinary PID unchanged. The active
    burn branch compares the live furnace curve to the same elapsed point in
    historical cycles, so the actual furnace temperature can reduce demand
    before burner-off samples exist for the current call.
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
    strategy = 'burner_off'
    if heating:
        comparable = _phase_comparable_cycles(
            records, now, furnace_temperature, runtime_seconds, baseline)
        if comparable:
            strategy = 'heating_phase'

    if len(comparable) < MIN_CYCLES:
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
            if not 0.5 * runtime <= runtime_seconds <= 2.0 * runtime:
                continue
            slope_distance = abs((furnace_slope or 0.0) - slope)
            distance = (abs(live_excess - excess) / max(excess, 1.0)
                        + abs(runtime_seconds - runtime) / max(runtime, 1.0)
                        + slope_distance / max(abs(slope), 0.1))
            comparable.append((distance, coast))
        strategy = 'burner_off'

    if len(comparable) < MIN_CYCLES:
        return None, {
            'status': 'insufficient_comparable_cycles',
            'baseline_c': round(baseline, 3),
            'comparable_cycles': len(comparable),
        }

    comparable.sort(key=lambda item: item[0])
    values = sorted(coast for _, coast in comparable[:9])
    predicted = values[(len(values) - 1) // 4]
    return predicted, {
        'status': 'calibrated' if strategy == 'burner_off' else 'calibrated_heating_phase',
        'baseline_c': round(baseline, 3),
        'comparable_cycles': len(comparable),
        'pending_rise_c': round(predicted, 3),
        'heating': bool(heating),
        'strategy': strategy,
    }

class FurnaceFeedForward:
    """Stateful live sampler with a pure, conservative prediction API."""

    def __init__(self):
        self._samples = []
        self._room_samples = []
        self._coast_started = None
        self._coast_runtime_seconds = None
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

    def begin_coast(self, now, runtime_seconds=None):
        """Begin a live residual-heat coast after a heat call ends."""
        if _finite(now):
            self._coast_started = now
            self._coast_runtime_seconds = runtime_seconds if (
                _finite(runtime_seconds) and runtime_seconds > 0) else None
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
        self._coast_runtime_seconds = None
        self._coast_peak_temperature = furnace_temperature
        self.last_diagnostics = {
            'status': 'coast_resumed_from_live_furnace_heat',
            'coast_started': round(now, 3),
            'furnace_temperature_c': round(furnace_temperature, 3),
            'baseline_c': round(baseline, 3),
        }
        return True

    def coast_active(self, records, now):
        """Return whether the last heat call is still releasing useful heat.

        This is not a fixed furnace-temperature cutoff. It uses the learned
        resting temperature plus live furnace and room slopes, so neither an
        automatic nor a manual burner-off can be immediately overwritten while
        heat is still reaching the home.
        """
        if self._coast_started is None:
            return False

        furnace_temperature = self._samples[-1][1] if self._samples else None
        furnace_slope = _slope(self._samples)
        room_slope = _slope(self._room_samples)
        learned_peak_seconds = _learned_post_off_peak_seconds(
            records, now, self._coast_runtime_seconds)
        coast_elapsed = max(0.0, now - self._coast_started)
        if not _finite(furnace_temperature) or furnace_slope is None or room_slope is None:
            if learned_peak_seconds is not None and coast_elapsed < learned_peak_seconds:
                self.last_diagnostics = {
                    'status': 'coast_waiting_for_telemetry',
                    'coast_started': round(self._coast_started, 3),
                    'coast_elapsed_seconds': round(coast_elapsed, 3),
                    'learned_peak_seconds': round(learned_peak_seconds, 3),
                }
                return True
            self._coast_started = None
            self.last_diagnostics = {
                'status': 'coast_released_no_telemetry',
                'coast_started': None,
            }
            return False

        baseline = _baseline(records, now)
        above_rest = baseline is None or furnace_temperature > baseline

        # A declining furnace sensor still represents stored heat. Keep the
        # post-burn hold until the live excess above this cycle's resting
        # baseline has materially decayed from its observed peak.
        if _finite(furnace_temperature):
            if (not _finite(self._coast_peak_temperature)
                    or furnace_temperature > self._coast_peak_temperature):
                self._coast_peak_temperature = furnace_temperature
        # Learned peak timing is useful context, but it cannot overrule the
        # measured thermal energy still stored in the furnace. A short
        # historical coast must never permit another heat call while the
        # current furnace remains materially above this burn's observed peak.
        if (_finite(baseline) and _finite(self._coast_peak_temperature)
                and self._coast_peak_temperature > baseline):
            peak_excess = self._coast_peak_temperature - baseline
            remaining_excess = max(0.0, furnace_temperature - baseline)
            if remaining_excess >= peak_excess * COAST_REMAINING_ENERGY_FRACTION:
                self.last_diagnostics = {
                    'status': 'coast_hold_remaining_furnace_energy',
                    'coast_started': round(self._coast_started, 3),
                    'furnace_temperature_c': round(furnace_temperature, 3),
                    'furnace_peak_c': round(self._coast_peak_temperature, 3),
                    'baseline_c': round(baseline, 3),
                    'remaining_energy_fraction': round(remaining_excess / peak_excess, 3),
                }
                return True

        # The room's own observed peak is the primary coast boundary. A
        # declining furnace sensor still represents stored heat; it must not
        # release the next call merely because the furnace has begun cooling.
        # The live sensor may end the hold early only after the furnace has
        # returned to its learned resting temperature and the room is no longer
        # warming.
        if (learned_peak_seconds is not None and coast_elapsed < learned_peak_seconds
                and (above_rest or room_slope > 0)):
            self.last_diagnostics = {
                'status': 'coast_hold_learned_peak',
                'coast_started': round(self._coast_started, 3),
                'coast_elapsed_seconds': round(coast_elapsed, 3),
                'learned_peak_seconds': round(learned_peak_seconds, 3),
                'furnace_temperature_c': round(furnace_temperature, 3),
                'furnace_slope_c_per_min': round(furnace_slope, 3),
                'room_slope_c_per_min': round(room_slope, 3),
                'baseline_c': round(baseline, 3) if baseline is not None else None,
            }
            return True

        thermal_release = furnace_slope > 0 or (above_rest and room_slope > 0)
        if thermal_release:
            self.last_diagnostics = {
                'status': 'coast_hold',
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
            'coast_runtime_seconds': self._coast_runtime_seconds,
            'coast_peak_temperature': self._coast_peak_temperature,
        }

    def restore(self, data):
        if not isinstance(data, dict):
            return
        started = data.get('coast_started', data.get('manual_coast_started'))
        if _finite(started):
            self._coast_started = started
        runtime_seconds = data.get('coast_runtime_seconds')
        if _finite(runtime_seconds) and runtime_seconds > 0:
            self._coast_runtime_seconds = runtime_seconds
        peak_temperature = data.get('coast_peak_temperature')
        if _finite(peak_temperature):
            self._coast_peak_temperature = peak_temperature

    def pending_rise(self, records, now, runtime_seconds, heating):
        if not self._samples:
            self.last_diagnostics = {'status': 'waiting_for_sensor'}
            return None
        rise, diagnostics = predict_pending_rise(
            records, now, self._samples[-1][1], _slope(self._samples), runtime_seconds, heating)
        self.last_diagnostics = diagnostics
        return rise
