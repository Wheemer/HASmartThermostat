"""A good aggregate or one improved metric cannot hide a regression."""

import unittest
from gain_validation_lab import compare_results


class GainValidationTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {'nominal': {'overshoot_c': 0.2, 'last_third_mae_c': 0.1, 'heat_starts': 4}}

    def test_less_overshoot_but_worse_temperature_error_is_rejected(self):
        candidate = {'nominal': {'overshoot_c': 0.1, 'last_third_mae_c': 0.2, 'heat_starts': 4}}
        self.assertEqual(compare_results(self.baseline, candidate)['status'], 'rejected')

    def test_more_cycles_is_not_hidden_by_better_temperature_metrics(self):
        candidate = {'nominal': {'overshoot_c': 0.1, 'last_third_mae_c': 0.05, 'heat_starts': 5}}
        self.assertIn('nominal:heat_starts:regression', compare_results(self.baseline, candidate)['failures'])

    def test_missing_scenario_or_invalid_metric_cannot_pass(self):
        self.assertEqual(compare_results(self.baseline, {})['status'], 'rejected')
        for value in (None, True, -1, float('nan'), float('inf')):
            candidate = {'nominal': {**self.baseline['nominal'], 'overshoot_c': value}}
            self.assertEqual(compare_results(self.baseline, candidate)['status'], 'rejected')

    def test_identical_results_are_not_an_improvement(self):
        self.assertEqual(compare_results(self.baseline, self.baseline)['status'], 'no_improvement')

    def test_passing_simulation_does_not_authorize_live_application(self):
        candidate = {'nominal': {'overshoot_c': 0.1, 'last_third_mae_c': 0.05, 'heat_starts': 4}}
        result = compare_results(self.baseline, candidate)
        self.assertEqual(result['status'], 'eligible_for_further_validation')
        self.assertFalse(result['apply'])
