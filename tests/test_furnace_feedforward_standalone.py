"""Tests for the measured post-off furnace residual-heat guard."""

import importlib.util
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'custom_components' /
          'smart_thermostat' / 'furnace_feedforward.py')
spec = importlib.util.spec_from_file_location('furnace_feedforward', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(index, baseline=25.0):
    return {'completed': 1_000_000 + index * 1_000, 'furnace_baseline': baseline}


class FurnaceResidualHeatGuardTests(unittest.TestCase):
    def setUp(self):
        self.records = [record(index) for index in range(6)]
        self.now = max(row['completed'] for row in self.records) + 60

    def test_guard_has_no_predictive_pid_api(self):
        guard = module.FurnaceFeedForward()
        self.assertFalse(hasattr(guard, 'pending_rise'))
        self.assertFalse(hasattr(guard, 'turn_off'))

    def test_rising_furnace_temperature_blocks_immediate_refire(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 30, 31.0)
        guard.observe(self.now, 34.4)
        guard.observe_room(self.now - 30, 21.0)
        guard.observe_room(self.now, 21.0)
        guard.begin_coast(self.now - 1)

        self.assertTrue(guard.coast_active(self.records, self.now))
        self.assertEqual(
            guard.last_diagnostics['status'], 'coast_hold_stored_furnace_heat')

    def test_falling_but_still_hot_furnace_blocks_immediate_refire(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 30, 36.0)
        guard.observe(self.now, 34.0)
        guard.observe_room(self.now - 30, 21.0)
        guard.observe_room(self.now, 21.0)
        guard.begin_coast(self.now - 1)

        self.assertTrue(guard.coast_active(self.records, self.now))
        self.assertEqual(
            guard.last_diagnostics['status'], 'coast_hold_stored_furnace_heat')

    def test_guard_releases_only_after_furnace_returns_to_resting_baseline(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 30, 25.4)
        guard.observe(self.now, 25.0)
        guard.observe_room(self.now - 30, 21.1)
        guard.observe_room(self.now, 21.0)
        guard.begin_coast(self.now - 1)

        self.assertFalse(guard.coast_active(self.records, self.now))
        self.assertEqual(guard.last_diagnostics['status'], 'coast_released')

    def test_missing_telemetry_does_not_hold_heat_off(self):
        guard = module.FurnaceFeedForward()
        guard.begin_coast(self.now)

        self.assertFalse(guard.coast_active(self.records, self.now + 1))
        self.assertEqual(guard.last_diagnostics['status'], 'coast_unverified')

    def test_restart_marker_uses_new_live_measurements(self):
        original = module.FurnaceFeedForward()
        original.observe(self.now - 1, 47.6)
        original.begin_coast(self.now - 1)
        restored = module.FurnaceFeedForward()
        restored.restore(original.snapshot())
        restored.observe(self.now - 30, 31.0)
        restored.observe(self.now, 34.4)
        restored.observe_room(self.now - 30, 21.0)
        restored.observe_room(self.now, 21.0)

        self.assertTrue(restored.coast_active(self.records, self.now))
        self.assertEqual(restored.snapshot()['coast_peak_temperature'], 47.6)

    def test_reload_resumes_guard_only_above_learned_baseline(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now, 34.4)
        self.assertTrue(guard.resume_coast_from_live_furnace_heat(self.records, self.now))

        at_rest = module.FurnaceFeedForward()
        at_rest.observe(self.now, 25.0)
        self.assertFalse(at_rest.resume_coast_from_live_furnace_heat(self.records, self.now))


if __name__ == '__main__':
    unittest.main()
