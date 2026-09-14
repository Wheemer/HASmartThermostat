"""Initial PID candidates for an identified integrating-plus-lag response.

SIMC series settings: Skogestad/Grimholt, PIDbook chapter 5, Table 5.1,
integrating with lag; conversion to parallel form: equation 5.30.
https://skoge.folk.ntnu.no/publications/2012/skogestad-improved-simc-pid/PIDbook-chapter5.pdf

This calculator does not accept a fit as validated, command an output, mutate
gains, or select new PWM/minimum-cycle settings. It is not wired to live tuning.
The host differentiates measurement rather than setpoint; conversion preserves
feedback coefficients, not identical response to a setpoint step.
"""

from math import isfinite


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def initial_pid_candidate(*, heat_rate_c_per_hour, delay_seconds, response_time_seconds,
                          output_span, closed_loop_seconds):
    """Return a dimensional gain candidate, never authorization to apply it.

Heat rate describes full normalized input (0..1). output_span maps this to
the host's control units, e.g. 100 for percentage output. All times are seconds.
closed_loop_seconds is a design input; it is not the furnace minimum on time.
"""
    positive = (heat_rate_c_per_hour, output_span, closed_loop_seconds)
    nonnegative = (delay_seconds, response_time_seconds)
    if not all(_number(v) and v > 0 for v in positive):
        raise ValueError('heat rate, output span and closed-loop time must be finite and positive')
    if not all(_number(v) and v >= 0 for v in nonnegative):
        raise ValueError('delay and response time must be finite and nonnegative')
    if closed_loop_seconds < delay_seconds:
        raise ValueError('closed-loop time below the measured delay is not supported')
    rate_per_second_per_output = heat_rate_c_per_hour / 3600 / output_span
    horizon = closed_loop_seconds + delay_seconds
    if rate_per_second_per_output == 0 or not isfinite(horizon):
        raise ValueError('model scaling is outside numerical range')
    series_gain = 1 / rate_per_second_per_output / horizon
    integral_time = 4 * horizon
    derivative_time = response_time_seconds
    gains = {
        'kp': series_gain * (1 + derivative_time / integral_time),
        'ki': series_gain / integral_time,
        'kd': series_gain * derivative_time,
    }
    if (not isfinite(integral_time) or not all(_number(v) and v >= 0 for v in gains.values())
            or gains['kp'] <= 0 or gains['ki'] <= 0):
        raise ValueError('calculated gains are outside numerical range')
    return {
        'status': 'candidate_requires_validation',
        'method': 'simc_integrating_with_lag_parallel_seconds',
        'gains': gains,
        'series_gain': series_gain,
        'integral_time_seconds': integral_time,
        'derivative_time_seconds': derivative_time,
        'closed_loop_seconds': closed_loop_seconds,
        'output_span': output_span,
        'derivative_lag_dominant': response_time_seconds > delay_seconds,
        'applied': False,
    }
