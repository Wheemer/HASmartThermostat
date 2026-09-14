"""Exercise upstream gain rules and proposal/validation boundaries."""

import sys
from copy import deepcopy
from pathlib import Path
import hashlib
import unittest
from test_history_learning_standalone import PACKAGE

Adaptation = sys.modules[f'{PACKAGE}.pid_adaptation'].PIDAdaptation


class PIDTests(unittest.TestCase):
    def setUp(self):
        self.gains = {'kp': 100.0, 'ki': 0.01, 'kd': 100.0}
        self.engine = Adaptation({'kp': (10, 500), 'ki': (0, 1), 'kd': (0, 12000)})

    def cycle(self, start=0, gains=None, **metrics):
        return dict(started=start, completed=start + 100, gains=gains or self.gains,
                    **({'overshoot': 0.4, 'undershoot': 0.0, 'oscillations': 0,
                        'rise_time': 4.0, 'settling_time': 8.0} | metrics))

    def propose(self, **metrics):
        return self.engine.propose([self.cycle(i * 200, **metrics) for i in range(6)],
                                   self.gains, 2000, 900)

    def test_upstream_moderate_overshoot_changes_derivative(self):
        result = self.propose()
        self.assertEqual(result['new'], self.gains | {'kd': 120.0})

    def test_proposal_is_not_reported_as_applied(self):
        self.propose()
        self.assertIsNone(self.engine.pending)
        self.assertIsNone(self.engine.last_change)

    def test_undershoot_adjusts_integral(self):
        result = self.propose(overshoot=0.0, undershoot=0.5)
        self.assertGreater(result['new']['ki'], self.gains['ki'])
        self.assertLessEqual(result['new']['ki'], 1.2 * self.gains['ki'])

    def test_slow_response_adjusts_proportional(self):
        result = self.propose(overshoot=0, rise_time=65)
        self.assertAlmostEqual(result['new']['kp'], 110)

    def test_pwm_oscillations_are_not_mistaken_for_bad_tuning(self):
        self.assertIsNone(self.propose(overshoot=0, oscillations=10))

    def test_zero_gains_seed_missing_derivative_from_overshoot(self):
        self.gains['ki'] = self.gains['kd'] = 0
        result = self.propose()
        self.assertEqual(result['new']['ki'], 0)
        self.assertGreater(result['new']['kd'], 0)
        self.assertEqual(result['seeded_gains'], ['kd'])
        self.assertEqual(self.engine.reason, 'proposal_ready')

    def test_zero_integral_seeds_from_undershoot(self):
        self.gains['ki'] = self.gains['kd'] = 0
        result = self.propose(overshoot=0, undershoot=0.5)
        self.assertGreater(result['new']['ki'], 0)
        self.assertEqual(result['new']['kd'], 0)
        self.assertEqual(result['seeded_gains'], ['ki'])

    def test_zero_gains_still_wait_without_a_rule(self):
        self.gains['ki'] = self.gains['kd'] = 0
        self.assertIsNone(self.propose(overshoot=0, undershoot=0, rise_time=4, settling_time=8))
        self.assertEqual(self.engine.reason, 'no_rule_triggered')

    def test_zero_proportional_still_requires_initialization(self):
        self.gains['kp'] = self.gains['ki'] = self.gains['kd'] = 0
        self.assertIsNone(self.propose())
        self.assertEqual(self.engine.reason, 'history_based_initialization_required')

    def test_old_gain_cycles_not_used(self):
        rows = [self.cycle(i * 200, gains=self.gains | {'kp': 99}) for i in range(6)]
        self.assertIsNone(self.engine.propose(rows, self.gains, 2000, 900))

    def test_missing_metrics_not_invented(self):
        self.assertIsNone(self.propose(rise_time=None))

    def test_rollback_after_five_worse_cycles(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        for i in range(4):
            self.assertIsNone(self.engine.validate(self.cycle(
                2100 + i * 200, proposal['new'], overshoot=0.8)))
        rollback = self.engine.validate(self.cycle(3000, proposal['new'], overshoot=0.8))
        self.assertEqual(rollback, self.gains)
        self.assertIsNotNone(self.engine.pending)
        self.assertEqual(self.engine.validate(self.cycle(3200, proposal['new'])), self.gains)
        self.assertEqual(len(self.engine.pending['cycles']), 5)
        self.engine.rollback_committed(3100)
        self.assertIsNone(self.engine.pending)

    def test_underheating_is_not_misreported_as_improvement(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        result = None
        for i in range(5):
            result = self.engine.validate(self.cycle(
                2100 + i * 200, proposal['new'], overshoot=0, undershoot=0.5))
        self.assertEqual(result, self.gains)

    def test_same_cycle_cannot_complete_validation(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        cycle = self.cycle(2100, proposal['new'])
        for _ in range(8):
            self.engine.validate(cycle)
        self.assertEqual(len(self.engine.pending['cycles']), 1)

    def test_good_validation_keeps_cooldown(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        for i in range(5):
            self.engine.validate(self.cycle(2100 + i * 200, proposal['new'], overshoot=0.2))
        self.assertIsNone(self.engine.pending)
        self.assertEqual(self.engine.last_change, 2000)
        self.assertIsNone(self.propose())

    def test_validation_survives_restore(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        self.engine.validate(self.cycle(2100, proposal['new']))
        restored = Adaptation(self.engine.limits)
        self.assertTrue(restored.restore(self.engine.snapshot(), proposal['new'], 2300))
        self.assertEqual(len(restored.pending['cycles']), 1)
        self.assertEqual(restored.last_change, 2000)
        self.assertIsNone(restored.propose([], proposal['new'], 2300, 900))

    def test_restore_rejects_gain_mismatch(self):
        self.engine.committed(self.propose(), 2000)
        restored = Adaptation(self.engine.limits)
        self.assertFalse(restored.restore(self.engine.snapshot(), self.gains, 2300))

    def test_restore_rejects_future_cooldown(self):
        self.assertFalse(self.engine.restore({'last_change': 2400, 'pending': None}, self.gains, 2300))

    def test_restore_rejects_corrupt_validation(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        saved = self.engine.snapshot()
        saved['pending']['baseline']['undershoot'] = float('nan')
        self.assertFalse(Adaptation(self.engine.limits).restore(saved, proposal['new'], 2300))

    def test_one_large_overshoot_does_not_drive_a_gain_proposal(self):
        rows = [self.cycle(i * 200, overshoot=0.1) for i in range(6)]
        rows[-1]['overshoot'] = 3.0
        self.assertIsNone(self.engine.propose(rows, self.gains, 2000, 900))
        self.assertEqual(self.engine.reason, 'no_rule_triggered')

    def test_latest_cycles_selected_by_time_not_input_order(self):
        old = [self.cycle(i * 200, overshoot=2.0) for i in range(6)]
        recent = [self.cycle(2000 + i * 200, overshoot=0.1) for i in range(6)]
        self.assertIsNone(self.engine.propose(recent + old, self.gains, 4000, 900))

    def test_duplicate_records_do_not_satisfy_minimum_cycle_count(self):
        rows = [deepcopy(self.cycle()) for _ in range(6)]
        self.assertIsNone(self.engine.propose(rows, self.gains, 2000, 900))
        self.assertEqual(self.engine.reason, 'insufficient_comparable_cycles')

    def test_conflicting_duplicate_is_withheld_in_either_order(self):
        rows = [self.cycle(i * 200) for i in range(6)]
        conflict = self.cycle(0, overshoot=3)
        for candidates in (rows + [conflict], [conflict] + rows):
            self.assertIsNone(self.engine.propose(candidates, self.gains, 2000, 900))

    def test_disturbed_copy_cannot_be_undone_by_clean_duplicate(self):
        rows = [self.cycle(i * 200) for i in range(6)]
        disturbed = {**self.cycle(), 'disturbed': True}
        self.assertIsNone(self.engine.propose([disturbed] + rows, self.gains, 2000, 900))

    def test_invalid_values_are_not_treated_as_metrics(self):
        for invalid in (True, -1, float('inf'), float('nan'), None, '0.4'):
            with self.subTest(value=invalid):
                self.assertIsNone(self.propose(overshoot=invalid))

    def test_malformed_timestamps_and_records_are_ignored_without_crashing(self):
        rows = [self.cycle(i * 200) for i in range(5)]
        invalids = [None, {}, self.cycle(-200), {**self.cycle(1000), 'completed': 900},
                    {**self.cycle(1000), 'started': True}, self.cycle(3000)]
        self.assertIsNone(self.engine.propose(rows + invalids, self.gains, 2000, 900))

    def test_invalid_validation_cycle_is_not_counted(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        for row in (None, {}, self.cycle(2100, proposal['new'], overshoot=True),
                    {**self.cycle(2100, proposal['new']), 'completed': 2000}):
            self.assertIsNone(self.engine.validate(row))
        self.assertEqual(self.engine.pending['cycles'], [])

    def test_validation_does_not_filter_out_a_large_worsening(self):
        proposal = self.propose()
        self.engine.committed(proposal, 2000)
        for i in range(4):
            self.engine.validate(self.cycle(2100 + i * 200, proposal['new'], overshoot=0.4))
        self.assertEqual(self.engine.validate(self.cycle(3000, proposal['new'], overshoot=2)), self.gains)

    def test_robust_statistics_are_unchanged_from_reference_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        copied = root / 'custom_components/smart_thermostat/adaptive_robust_stats.py'
        self.assertEqual(hashlib.sha256(copied.read_text().encode()).hexdigest(),
                         'caa8fd849e1965b4efda4c8535d36b7ff7d124a09bec98bb7686a3f1f9ce7df8')


if __name__ == '__main__':
    unittest.main()
