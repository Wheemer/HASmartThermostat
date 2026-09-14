"""Metric semantics from Adaptive Climate, tested against heating edge cases."""

import sys
import unittest
from test_history_learning_standalone import PACKAGE

measure = sys.modules[f'{PACKAGE}.pid_cycle_metrics'].measure_cycle


class MetricTests(unittest.TestCase):
    def test_recovery_start_is_not_settling_undershoot(self):
        metrics = measure([(0, 18), (60, 19), (120, 20.9), (180, 21.1), (240, 21)], 21, 120)
        self.assertAlmostEqual(metrics['undershoot'], 0.1)
        self.assertAlmostEqual(metrics['overshoot'], 0.1)

    def test_unreached_target_is_not_zero_rise_time(self):
        metrics = measure([(0, 18), (60, 19), (120, 20)], 21, 60)
        self.assertIsNone(metrics['rise_time'])
        self.assertIsNone(metrics['overshoot'])
        self.assertIsNone(metrics['settling_time'])

    def test_missing_settling_data_is_not_zero_error(self):
        metrics = measure([(0, 18), (60, 19)], 21, 120)
        self.assertIsNone(metrics['undershoot'])
        self.assertIsNone(metrics['settling_mae'])

    def test_rise_tolerance_is_explicit(self):
        samples = [(0, 20), (60, 20.85), (120, 21)]
        self.assertEqual(measure(samples, 21, 60, 0)['rise_time'], 2)
        self.assertEqual(measure(samples, 21, 60, 0.2)['rise_time'], 1)

    def test_noise_not_counted_as_temperature_oscillations(self):
        metrics = measure([(0, 20.99), (60, 21.01), (120, 20.99), (180, 21.01)], 21, 60)
        self.assertEqual(metrics['oscillations'], 0)


if __name__ == '__main__':
    unittest.main()
