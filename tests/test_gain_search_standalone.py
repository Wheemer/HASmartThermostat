"""The offline search cannot turn failed simulations into an approved tune."""

import unittest
from gain_search_lab import candidates, search, SCENARIOS


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def test_factor_grid_is_bounded_and_keeps_positive_integral(self):
        rows = list(candidates({'kp': 10, 'ki': 0.1, 'kd': 100}, [1], [0, 0.5], [0, 1]))
        self.assertEqual(rows, [{'kp': 10, 'ki': 0.05, 'kd': 0}, {'kp': 10, 'ki': 0.05, 'kd': 100}])
        with self.assertRaises(ValueError):
            list(candidates({}, list(range(257)), [1], [1]))

    def test_invalid_grids_rejected(self):
        for values in ([], [True], [-1], [float('nan')]):
            with self.assertRaises(ValueError):
                list(candidates({'kp': 1, 'ki': 1, 'kd': 1}, values, [1], [1]))

    async def test_every_candidate_uses_all_unchanged_scenarios(self):
        calls = []
        async def runner(gains, **kwargs):
            calls.append((dict(gains), dict(kwargs)))
            return {'overshoot_c': 0.2, 'last_third_mae_c': 0.1, 'heat_starts': 4}
        result = await search({'kp': 10, 'ki': 0.1, 'kd': 1}, kp_factors=[1], ki_factors=[1],
                              kd_factors=[0, 1], runner=runner)
        self.assertEqual(len(calls), len(SCENARIOS) * 3)
        self.assertEqual(result['trial_count'], 2)
        self.assertEqual(result['eligible_count'], 0)
        self.assertFalse(result['apply'])
