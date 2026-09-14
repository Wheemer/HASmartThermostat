"""Prove equivalent I/D output across controller time units."""

import importlib
import unittest
from test_history_learning_standalone import PACKAGE
from test_initial_pid_standalone import PID, ROOT, load

PIDAutotune = load('_autotune_host_pid', ROOT / 'pid_controller' / '__init__.py').PIDAutotune

convert = importlib.import_module(PACKAGE + '.pid_units').hourly_to_seconds_id


class UnitTests(unittest.TestCase):
    def test_integral_output_equivalence_at_different_intervals(self):
        ki_seconds, _ = convert(8.0, 0.8)
        for seconds in (5, 30, 60, 300, 3600):
            with self.subTest(seconds=seconds):
                self.assertAlmostEqual(8.0 * 0.4 * (seconds / 3600), ki_seconds * 0.4 * seconds)

    def test_derivative_output_equivalence_at_different_intervals(self):
        _, kd_seconds = convert(8.0, 0.8)
        for seconds in (5, 30, 60, 300, 3600):
            with self.subTest(seconds=seconds):
                self.assertAlmostEqual(-0.8 * 0.02 / (seconds / 3600), -kd_seconds * 0.02 / seconds)

    def test_no_arbitrary_nonzero_gain_is_invented(self):
        self.assertEqual(convert(0, 0), (0, 0))

    def test_invalid_gains_rejected(self):
        for value in (float('nan'), float('inf'), -1, None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                convert(value, 0)

    def test_event_driven_pid_keeps_real_sensor_delta(self):
        pid = PID(0, 0.01, 0, out_min=-100, out_max=100, sampling_period=0)
        pid.calc(20, 21, input_time=0)
        pid.calc(20, 21, input_time=575, last_input_time=0)
        self.assertEqual(pid.dt, 575)
        self.assertAlmostEqual(pid.integral, 5.75)

    def test_sampled_pid_integral_uses_configured_period_not_sensor_gap(self):
        pid = PID(0, 0.01, 0, out_min=-100, out_max=100, sampling_period=30)
        pid.calc(20, 21, input_time=0)
        pid.calc(20, 21, input_time=575, last_input_time=0)
        self.assertEqual(pid.dt, 30)
        self.assertAlmostEqual(pid.integral, 0.3)

    def test_autotune_analysis_scans_full_buffer_for_peaks(self):
        tuner = PIDAutotune(out_step=10, lookback=60, out_min=0, out_max=100, noiseband=0.2)
        tuner._sampletime = 10
        tuner._setpoint = 20
        tuner._state = PIDAutotune.STATE_RELAY_STEP_UP
        samples = [19.6, 20.4, 19.5, 20.5, 19.4, 20.6, 19.5, 20.5, 19.6, 20.4]
        for index, value in enumerate(samples):
            tuner._inputs.append(value)
            tuner._inputs_timestamps.append(index * 10)

        tuner.analysis()
        self.assertGreater(tuner.peak_count, 0)


if __name__ == '__main__':
    unittest.main()
