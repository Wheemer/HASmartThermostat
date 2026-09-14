"""Initial gains use host PID units, not reference integration gain constants."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'smart_thermostat'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate = load('_initial_gain_test', ROOT / 'initial_pid.py').initial_pid_candidate
PID = load('_initial_host_pid', ROOT / 'pid_controller' / '__init__.py').PID


def calculate(**changes):
    arguments = dict(heat_rate_c_per_hour=6, delay_seconds=300, response_time_seconds=30,
                     output_span=100, closed_loop_seconds=900)
    return candidate(**{**arguments, **changes})


class InitialPIDTests(unittest.TestCase):
    def test_numeric_integrating_lag_case_and_parallel_conversion(self):
        result = calculate()
        self.assertAlmostEqual(result['series_gain'], 50)
        self.assertEqual(result['integral_time_seconds'], 4800)
        self.assertAlmostEqual(result['gains']['kp'], 50.3125)
        self.assertAlmostEqual(result['gains']['ki'], 50 / 4800)
        self.assertAlmostEqual(result['gains']['kd'], 1500)
        self.assertFalse(result['applied'])

    def test_series_and_parallel_feedback_polynomials_match(self):
        r = calculate()
        for s in (complex(0, 0.001), complex(0, 0.01), complex(0, 1)):
            series = r['series_gain'] * (1 + 1 / (r['integral_time_seconds'] * s)) * (1 + r['derivative_time_seconds'] * s)
            parallel = r['gains']['kp'] + r['gains']['ki'] / s + r['gains']['kd'] * s
            self.assertAlmostEqual(abs(series - parallel), 0)

    def test_output_fraction_and_percentage_gains_scale_consistently(self):
        percent, fraction = calculate(), calculate(output_span=1)
        for key in ('kp', 'ki', 'kd'):
            self.assertAlmostEqual(percent['gains'][key], fraction['gains'][key] * 100)

    def test_host_pid_actual_integral_and_derivative_use_seconds(self):
        g = calculate()['gains']
        pid = PID(**g, out_min=0, out_max=100)
        pid.calc(20.0, 20.2, input_time=0)
        pid.calc(20.02, 20.2, input_time=30, last_input_time=0)
        self.assertAlmostEqual(pid.proportional, g['kp'] * 0.18)
        self.assertAlmostEqual(pid.integral, g['ki'] * 0.18 * 30)
        self.assertAlmostEqual(pid.derivative, -g['kd'] * 0.02 / 30)

    def test_zero_lag_does_not_fabricate_derivative_gain(self):
        self.assertEqual(calculate(response_time_seconds=0)['gains']['kd'], 0)

    def test_smoother_design_reduces_gains_without_changing_model(self):
        first, slower = calculate(), calculate(closed_loop_seconds=1800)
        for key in ('kp', 'ki', 'kd'):
            self.assertLess(slower['gains'][key], first['gains'][key])

    def test_invalid_inputs_rejected(self):
        for key in ('heat_rate_c_per_hour', 'output_span', 'closed_loop_seconds',
                    'delay_seconds', 'response_time_seconds'):
            for value in (True, '300', float('nan'), float('inf'), -1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    calculate(**{key: value})
        for key in ('heat_rate_c_per_hour', 'output_span', 'closed_loop_seconds'):
            with self.assertRaises(ValueError):
                calculate(**{key: 0})

    def test_overaggressive_design_is_not_accepted(self):
        with self.assertRaises(ValueError):
            calculate(closed_loop_seconds=100)

    def test_extreme_scaling_is_not_reported_as_usable_gains(self):
        with self.assertRaises(ValueError):
            calculate(heat_rate_c_per_hour=1e-320, output_span=1e300)
