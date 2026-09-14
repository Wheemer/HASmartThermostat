"""Offline delayed-response experiment; deliberately not imported by HA.

Model: dT/dt = background_drift + heat_rate * filtered_delayed_relay.
The relay filter is first order. Its time constant is NOT a building cooling
time constant. Known-rest initial conditions are required. Background drift is
constant across fitted windows, an assumption that held-out data must challenge.
NumPy lstsq solves the two linear coefficients for each explicitly supplied
delay/filter-time candidate. No PID gains or deployment decisions are produced.
"""

from math import exp, expm1, isfinite
import numpy as np


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def exposure(t, intervals, delay, tau):
    """Integrated first-order relay response in seconds, including post-off tail."""
    def step(age):
        if age <= 0:
            return 0.0
        return age if tau == 0 else age + tau * expm1(-age / tau)
    return sum(step(t - start - delay) - step(t - stop - delay)
               for start, stop in intervals)


def _validate(windows):
    previous_end = None
    for window in windows:
        quiet = window.get('prior_off_seconds')
        if window.get('initial_response_at_rest') is not True and not (_number(quiet) and quiet >= 0):
            raise ValueError('initial response state is not established')
        samples = window['samples']
        intervals = window['on_intervals']
        if len(samples) < 4 or not intervals:
            raise ValueError('insufficient response samples')
        if any(not _number(t) or not _number(value) for t, value in samples):
            raise ValueError('invalid sample')
        if any(not 0 < b[0] - a[0] <= 120 for a, b in zip(samples, samples[1:])):
            raise ValueError('unordered samples or sensor gap')
        start, end = samples[0][0], samples[-1][0]
        if previous_end is not None and start <= previous_end:
            raise ValueError('windows overlap or are not chronological')
        previous_end = end
        previous_stop = start
        for on, off in intervals:
            if (not _number(on) or not _number(off)
                    or not previous_stop <= on < off <= end):
                raise ValueError('invalid relay interval')
            previous_stop = off
        if intervals[-1][1] >= end:
            raise ValueError('post-off observations required')


def _matrix(windows, delay, tau):
    rows, changes, weights = [], [], []
    for window in windows:
        samples, intervals = window['samples'], window['on_intervals']
        start, initial = samples[0]
        duration = samples[-1][0] - start
        for index, (t, value) in enumerate(samples):
            rows.append([exposure(t, intervals, delay, tau) / 3600, (t - start) / 3600])
            changes.append(value - initial)
            left = samples[max(0, index - 1)][0]
            right = samples[min(len(samples) - 1, index + 1)][0]
            # Equal total weight per window, independent of sampling density.
            weights.append((right - left) / (2 * duration * len(windows)))
    return np.asarray(rows), np.asarray(changes), np.asarray(weights)


def residual_bound(window, delay, tau):
    """Bound normalized initial filter state under the candidate model only."""
    if window.get('initial_response_at_rest') is True:
        return 0.0
    quiet = window['prior_off_seconds'] - delay
    if quiet < 0:
        return 1.0
    return exp(-quiet / tau) if tau else 0.0


def fit_response_model(training, validation, *, delays, time_constants, max_initial_response=0.001):
    """Select using training only; report untouched chronological holdout errors."""
    if len(training) < 2 or len(validation) < 2:
        raise ValueError('at least two training and two held-out windows required')
    if not delays or not time_constants:
        raise ValueError('explicit candidate grids required')
    if any(not _number(v) or v < 0 for v in [*delays, *time_constants]):
        raise ValueError('invalid candidate timing')
    if not _number(max_initial_response) or not 0 <= max_initial_response < 1:
        raise ValueError('invalid initial response tolerance')
    _validate([*training, *validation])
    candidates = []
    for delay in sorted(set(delays)):
        for tau in sorted(set(time_constants)):
            if any(residual_bound(w, delay, tau) > max_initial_response for w in training):
                continue
            x, y, weights = _matrix(training, delay, tau)
            weighted_x = x * np.sqrt(weights)[:, None]
            scales = np.linalg.norm(weighted_x, axis=0)
            if np.any(scales == 0):
                continue
            normalized = weighted_x / scales
            beta, _, rank, singular = np.linalg.lstsq(normalized, y * np.sqrt(weights), rcond=None)
            if rank != 2 or singular[0] / singular[-1] > 1e6:
                continue
            beta /= scales
            if not np.all(np.isfinite(beta)) or beta[0] <= 0:
                continue
            mse = float(np.sum(weights * (x @ beta - y) ** 2))
            candidates.append((mse, delay, tau, beta))
    if not candidates:
        return {'status': 'not_identified', 'initial_gains': None}
    mse, delay, tau, beta = min(candidates, key=lambda item: item[:3])
    # Holdout initial-state evidence must not select a different training fit.
    holdout_bound = max(residual_bound(w, delay, tau) for w in validation)
    if holdout_bound > max_initial_response:
        return {'status': 'holdout_initial_state_uncertain', 'initial_gains': None,
                'delay_seconds': delay, 'response_time_seconds': tau,
                'holdout_initial_response_bound': holdout_bound}
    training_x, training_y, training_weights = _matrix(training, delay, tau)
    drift_only, _, _, _ = np.linalg.lstsq(
        training_x[:, 1:] * np.sqrt(training_weights)[:, None],
        training_y * np.sqrt(training_weights), rcond=None)
    x, y, weights = _matrix(validation, delay, tau)
    errors = x @ beta - y
    validation_rmse = float(np.sqrt(np.sum(weights * errors ** 2)))
    persistence_rmse = float(np.sqrt(np.sum(weights * y ** 2)))
    drift_rmse = float(np.sqrt(np.sum(weights * (x[:, 1] * drift_only[0] - y) ** 2)))
    per_window = []
    for window in validation:
        wx, wy, ww = _matrix([window], delay, tau)
        per_window.append({
            'started': window['samples'][0][0],
            'rmse_c': float(np.sqrt(np.sum(ww * (wx @ beta - wy) ** 2))),
            'max_error_c': float(np.max(np.abs(wx @ beta - wy))),
            'drift_only_rmse_c': float(np.sqrt(np.sum(ww * (wx[:, 1] * drift_only[0] - wy) ** 2))),
        })
    return {
        'status': 'experimental_model_only',
        'delay_seconds': delay,
        'response_time_seconds': tau,
        'heat_rate_c_per_hour': float(beta[0]),
        'background_drift_c_per_hour': float(beta[1]),
        'training_rmse_c': mse ** 0.5,
        'validation_rmse_c': validation_rmse,
        'validation_persistence_rmse_c': persistence_rmse,
        'validation_drift_only_rmse_c': drift_rmse,
        'validation_windows': per_window,
        'every_window_beats_drift_only': all(w['rmse_c'] < w['drift_only_rmse_c'] for w in per_window),
        'beats_persistence': validation_rmse < persistence_rmse,
        'beats_drift_only': validation_rmse < drift_rmse,
        'on_search_boundary': delay in (min(delays), max(delays))
            or tau in (min(time_constants), max(time_constants)),
        'initial_gains': None,
        'max_initial_response': max_initial_response,
        'holdout_initial_response_bound': holdout_bound,
    }
