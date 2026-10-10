"""Tests for the single-boundary furnace residual-heat interlock."""

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

    def test_guard_has_only_pre_fire_api(self):
        guard = module.FurnaceFeedForward()
        self.assertTrue(hasattr(guard, 'blocks_new_heat_call'))
        self.assertFalse(hasattr(guard, 'coast_active'))
        self.assertFalse(hasattr(guard, 'observe_room'))
        self.assertFalse(hasattr(guard, 'resume_coast_from_live_furnace_heat'))

    def test_rising_furnace_blocks_new_heat_call(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 30, 47.6)
        guard.begin_coast(self.now - 30)
        guard.observe(self.now, 51.2)

        self.assertTrue(guard.blocks_new_heat_call(self.records, self.now))
        self.assertEqual(guard.last_diagnostics['status'], 'coast_hold_stored_furnace_heat')

    def test_actual_rapid_refire_trace_remains_blocked_while_furnace_is_hot(self):
        """Replay the observed 47.6 -> 51.2 -> 50.8 C rapid-refire sequence."""
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 102, 47.6)
        guard.begin_coast(self.now - 102)
        guard.observe(self.now - 60, 51.2)
        guard.observe(self.now, 50.8)

        self.assertTrue(guard.blocks_new_heat_call(self.records, self.now))
        self.assertEqual(guard.last_diagnostics['furnace_temperature_c'], 50.8)

    def test_guard_releases_after_measured_energy_has_mostly_dissipated(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 60, 47.6)
        guard.begin_coast(self.now - 60)
        guard.observe(self.now - 30, 51.2)
        guard.observe(self.now, 27.0)

        self.assertFalse(guard.blocks_new_heat_call(self.records, self.now))
        self.assertEqual(guard.last_diagnostics['status'], 'coast_released')

    def test_missing_telemetry_never_changes_pid_or_latches_off(self):
        guard = module.FurnaceFeedForward()
        guard.begin_coast(self.now)

        self.assertFalse(guard.blocks_new_heat_call(self.records, self.now + 1))
        self.assertEqual(guard.last_diagnostics['status'], 'coast_unverified')

    def test_warm_furnace_at_startup_does_not_invent_a_coast(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 30, 50.0)
        guard.observe(self.now, 49.0)

        self.assertFalse(guard.blocks_new_heat_call(self.records, self.now))

    def test_explicit_coast_snapshot_survives_restart_only_until_checked(self):
        original = module.FurnaceFeedForward()
        original.observe(self.now - 60, 47.6)
        original.begin_coast(self.now - 60)
        restored = module.FurnaceFeedForward()
        restored.restore(original.snapshot())
        restored.observe(self.now - 30, 51.2)
        restored.observe(self.now, 50.8)

        self.assertTrue(restored.blocks_new_heat_call(self.records, self.now))

    def test_coast_cannot_latch_indefinitely(self):
        guard = module.FurnaceFeedForward()
        guard.observe(self.now - 1900, 47.6)
        guard.begin_coast(self.now - 1900)
        guard.observe(self.now - 30, 51.2)
        guard.observe(self.now, 50.8)

        self.assertFalse(guard.blocks_new_heat_call(self.records, self.now))
        self.assertEqual(guard.last_diagnostics['status'], 'coast_expired')


if __name__ == '__main__':
    unittest.main()
