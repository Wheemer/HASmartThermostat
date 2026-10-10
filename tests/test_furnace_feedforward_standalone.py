"""Tests for furnace residual-heat compensation without Home Assistant."""

import importlib.util
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'custom_components' /
          'smart_thermostat' / 'furnace_feedforward.py')
spec = importlib.util.spec_from_file_location('furnace_feedforward', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(index, furnace_end=50.0, coast=0.35, runtime=240, peak_seconds=300.0):
    start = 1_000_000 + index * 1_000
    stopped = start + runtime
    return {
        'started': start,
        'completed': stopped + 900,
        'stopped': stopped,
        'runtime_seconds': runtime,
        'coast': coast,
        'furnace_baseline': 25.0,
        'furnace_samples': [(start + 60, 25.0), (stopped - 60, furnace_end - 2),
                            (stopped, furnace_end)],
        'thermal_response': {
            'version': 1,
            'on_seconds': runtime,
            'post_off_peak_seconds': peak_seconds,
            'post_off_observed_seconds': 900.0,
        },
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

    def test_active_burn_matches_same_historical_furnace_phase(self):
        rise, diagnostics = module.predict_pending_rise(
            self.records, self.now, 35.0, 1.0, 120, True)
        self.assertAlmostEqual(rise, 0.35)
        self.assertEqual(diagnostics['status'], 'calibrated_heating_phase')
        self.assertEqual(diagnostics['strategy'], 'heating_phase')
        self.assertEqual(diagnostics['comparable_cycles'], 6)

    def test_coast_holds_through_the_learned_peak_while_furnace_cools(self):
        model = module.FurnaceFeedForward()
        now = self.now
        model.observe(now - 20, 56.0)
        model.observe(now - 10, 55.0)
        model.observe_room(now - 20, 21.4)
        model.observe_room(now - 10, 21.35)
        model.begin_coast(now - 15)
        self.assertTrue(model.coast_active(self.records, now + 55))
        self.assertEqual(
            model.last_diagnostics['status'],
            'coast_hold_remaining_furnace_energy')

        model.observe(now + 400, 24.9)
        model.observe_room(now + 400, 21.3)
        self.assertFalse(model.coast_active(self.records, now + 400))
        self.assertEqual(model.last_diagnostics['status'], 'coast_released')

    def test_coast_holds_when_a_hot_furnace_has_started_cooling(self):
        model = module.FurnaceFeedForward()
        now = self.now
        records = [dict(row, thermal_response={}) for row in self.records]
        model.observe(now - 10, 27.0)
        model.observe_room(now - 10, 21.5)
        model.begin_coast(now, 90.0)

        # This is the failure mode from the live system: a short burn ends,
        # the furnace continues to climb, then has only just started cooling.
        model.observe(now + 150, 34.8)
        model.observe_room(now + 150, 21.5)
        self.assertTrue(model.coast_active(records, now + 150))
        self.assertEqual(
            model.last_diagnostics['status'],
            'coast_hold_remaining_furnace_energy')

    def test_coast_uses_only_similar_length_burns_for_peak_timing(self):
        records = ([record(index, runtime=90, peak_seconds=180.0) for index in range(3)]
                   + [record(index + 10, runtime=600, peak_seconds=900.0) for index in range(3)])
        now = max(row['completed'] for row in records) + 60
        model = module.FurnaceFeedForward()
        model.observe(now - 20, 56.0)
        model.observe(now - 10, 55.0)
        model.observe_room(now - 20, 21.4)
        model.observe_room(now - 10, 21.35)
        model.begin_coast(now - 15, 90.0)

        self.assertTrue(model.coast_active(records, now + 200))
        self.assertEqual(
            model.last_diagnostics['status'],
            'coast_hold_remaining_furnace_energy')

    def test_restart_preserves_the_runtime_needed_for_comparable_coast(self):
        records = ([record(index, runtime=90, peak_seconds=180.0) for index in range(3)]
                   + [record(index + 10, runtime=600, peak_seconds=900.0) for index in range(3)])
        now = max(row['completed'] for row in records) + 60
        model = module.FurnaceFeedForward()
        model.begin_coast(now - 15, 90.0)
        restored = module.FurnaceFeedForward()
        restored.restore(model.snapshot())
        restored.observe(now - 20, 56.0)
        restored.observe(now - 10, 55.0)
        restored.observe_room(now - 20, 21.4)
        restored.observe_room(now - 10, 21.35)

        self.assertTrue(restored.coast_active(records, now + 200))
        self.assertEqual(
            restored.last_diagnostics['status'],
            'coast_hold_remaining_furnace_energy')

    def test_reload_rebuilds_coast_from_live_furnace_heat_when_marker_is_missing(self):
        model = module.FurnaceFeedForward()
        model.observe(self.now, 34.4)

        self.assertTrue(model.resume_coast_from_live_furnace_heat(
            self.records, self.now))
        self.assertEqual(
            model.last_diagnostics['status'],
            'coast_resumed_from_live_furnace_heat')

        model.observe(self.now + 30, 34.0)
        model.observe_room(self.now, 21.0)
        model.observe_room(self.now + 30, 21.0)
        self.assertTrue(model.coast_active(self.records, self.now + 30))
        self.assertEqual(
            model.last_diagnostics['status'],
            'coast_hold_remaining_furnace_energy')

    def test_reload_does_not_create_coast_at_or_below_resting_temperature(self):
        model = module.FurnaceFeedForward()
        model.observe(self.now, 25.0)

        self.assertFalse(model.resume_coast_from_live_furnace_heat(
            self.records, self.now))


    def test_missing_telemetry_only_holds_through_learned_peak(self):
        model = module.FurnaceFeedForward()
        model.begin_coast(self.now)

        self.assertTrue(model.coast_active(self.records, self.now + 60))
        self.assertEqual(model.last_diagnostics['status'], 'coast_waiting_for_telemetry')

        self.assertFalse(model.coast_active(self.records, self.now + 301))
        self.assertEqual(model.last_diagnostics['status'], 'coast_released_no_telemetry')

if __name__ == '__main__':
    unittest.main()
