"""Tests for furnace residual-heat compensation without Home Assistant."""

import importlib.util
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'custom_components' /
          'smart_thermostat' / 'furnace_feedforward.py')
spec = importlib.util.spec_from_file_location('furnace_feedforward', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(index, furnace_end=50.0, coast=0.35, runtime=240):
    start = 1_000_000 + index * 1_000
    stopped = start + runtime
    return {
        'completed': stopped + 900,
        'stopped': stopped,
        'runtime_seconds': runtime,
        'coast': coast,
        'furnace_baseline': 25.0,
        'furnace_samples': [(start + 60, 25.0), (stopped - 60, furnace_end - 2),
                            (stopped, furnace_end)],
    }


class FurnaceFeedForwardTests(unittest.TestCase):
    def setUp(self):
        self.records = [record(index) for index in range(6)]
        self.now = max(row['completed'] for row in self.records) + 60

    def test_no_prediction_without_six_comparable_completed_cycles(self):
        rise, diagnostics = module.predict_pending_rise(
            self.records[:5], self.now, 50, 2.0, 240, True)
        self.assertIsNone(rise)
        self.assertIn(diagnostics['status'], {'insufficient_baseline', 'insufficient_comparable_cycles'})

    def test_calibrated_prediction_is_conservative_and_not_a_threshold(self):
        rise, diagnostics = module.predict_pending_rise(
            self.records, self.now, 50, 2.0, 240, True)
        self.assertAlmostEqual(rise, 0.35)
        self.assertEqual(diagnostics['status'], 'calibrated')
        self.assertEqual(diagnostics['comparable_cycles'], 6)

    def test_high_furnace_temperature_is_not_an_off_command(self):
        rise, diagnostics = module.predict_pending_rise(
            self.records, self.now, 75, 2.0, 240, True)
        self.assertIsNotNone(rise)
        self.assertEqual(diagnostics['status'], 'calibrated')
        # The model only supplies an estimate.  It has no actuator API and
        # therefore cannot turn the furnace off at any raw sensor reading.
        self.assertFalse(hasattr(module.FurnaceFeedForward(), 'turn_off'))

    def test_resting_temperature_does_not_change_pid_demand(self):
        rise, diagnostics = module.predict_pending_rise(
            self.records, self.now, 24.5, -0.1, 240, False)
        self.assertEqual(rise, 0.0)
        self.assertEqual(diagnostics['status'], 'at_rest')

    def test_invalid_live_sensor_leaves_controller_uncompensated(self):
        rise, diagnostics = module.predict_pending_rise(
            self.records, self.now, None, 2.0, 240, True)
        self.assertIsNone(rise)
        self.assertEqual(diagnostics['status'], 'invalid_live_sensor')

    def test_outlier_coast_does_not_create_aggressive_prediction(self):
        records = [record(index, coast=0.35) for index in range(8)]
        records[-1]['coast'] = 4.0
        rise, _ = module.predict_pending_rise(records, self.now, 50, 2.0, 240, True)
        self.assertAlmostEqual(rise, 0.35)

    def test_ninety_second_burns_can_calibrate_the_feedforward_model(self):
        records = [record(index, runtime=90) for index in range(6)]
        rise, diagnostics = module.predict_pending_rise(
            records, self.now, 50, 2.0, 90, True)
        self.assertAlmostEqual(rise, 0.35)
        self.assertEqual(diagnostics['status'], 'calibrated')

    def test_manual_coast_holds_while_furnace_and_room_are_still_rising(self):
        model = module.FurnaceFeedForward()
        model.observe(1_000, 55.0)
        model.observe(1_010, 56.0)
        model.observe_room(1_000, 21.4)
        model.observe_room(1_010, 21.5)
        model.begin_manual_coast(1_005)
        self.assertTrue(model.manual_coast_active(self.records, self.now))
        self.assertEqual(model.last_diagnostics['status'], 'manual_coast_hold')

        model.observe(1_020, 55.5)
        model.observe_room(1_020, 21.45)
        self.assertFalse(model.manual_coast_active(self.records, self.now))
        self.assertEqual(model.last_diagnostics['status'], 'manual_coast_released')


if __name__ == '__main__':
    unittest.main()
