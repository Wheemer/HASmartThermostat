"""Synthetic checks for the offline experiment, not real furnace validation."""

import unittest
from thermal_model_lab import exposure, fit_response_model


def window(index, heat_rate=4, drift=-0.15, delay=120, tau=300, step=30):
    start = index * 86400
    intervals = [(start + 120, start + 420 + index * 30)]
    # Independent numerical integration, rather than using the fitted formula
    # to generate the target data. One-second steps resolve delay and filter.
    filtered = 0.0
    temperature = 20.0
    samples = [(start, temperature)]
    for t in range(1, 2401):
        u = float(intervals[0][0] <= start + t - delay < intervals[0][1])
        filtered += (u - filtered) / tau
        temperature += (heat_rate * filtered + drift) / 3600
        if t % step == 0:
            samples.append((start + t, temperature))
    return {'initial_response_at_rest': True, 'samples': samples, 'on_intervals': intervals}


def fit(training=None, validation=None):
    return fit_response_model(training or [window(0), window(1)],
                              validation or [window(2), window(3)],
                              delays=[0, 120, 240], time_constants=[120, 300, 600])


class ModelTests(unittest.TestCase):
    def test_independently_simulated_delayed_response_is_recovered(self):
        result = fit()
        self.assertEqual(result['delay_seconds'], 120)
        self.assertEqual(result['response_time_seconds'], 300)
        self.assertAlmostEqual(result['heat_rate_c_per_hour'], 4, delta=0.03)
        self.assertAlmostEqual(result['background_drift_c_per_hour'], -0.15, delta=0.01)
        self.assertLess(result['validation_rmse_c'], 0.003)
        self.assertTrue(result['beats_persistence'])
        self.assertTrue(result['beats_drift_only'])
        self.assertIsNone(result['initial_gains'])

    def test_holdout_changes_cannot_change_fitted_parameters(self):
        original = fit()
        changed = fit(validation=[window(2, heat_rate=0), window(3, heat_rate=0)])
        for key in ('delay_seconds', 'response_time_seconds', 'heat_rate_c_per_hour'):
            self.assertEqual(original[key], changed[key])
        self.assertFalse(changed['beats_persistence'])

    def test_different_sampling_density_preserves_fit(self):
        result = fit(training=[window(0, step=60), window(1, step=30)])
        self.assertAlmostEqual(result['heat_rate_c_per_hour'], 4, delta=0.03)

    def test_initial_residual_cannot_be_assumed_zero(self):
        data = window(0)
        del data['initial_response_at_rest']
        with self.assertRaisesRegex(ValueError, 'initial response'):
            fit(training=[data, window(1)])

    def test_overlapping_holdout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            fit(validation=[window(1), window(3)])

    def test_sensor_gap_rejected(self):
        data = window(0)
        data['samples'] = [s for i, s in enumerate(data['samples']) if not 5 <= i <= 12]
        with self.assertRaisesRegex(ValueError, 'sensor gap'):
            fit(training=[data, window(1)])

    def test_no_heat_excitation_cannot_identify_gain(self):
        data = [window(i) for i in range(4)]
        for w in data:
            w['on_intervals'] = []
        with self.assertRaises(ValueError):
            fit(training=data[:2], validation=data[2:])

    def test_integrated_tail_continues_after_off(self):
        intervals = [(0, 120)]
        self.assertGreater(exposure(240, intervals, 0, 300), exposure(120, intervals, 0, 300))
        self.assertAlmostEqual(exposure(12000, intervals, 0, 300), 120)
        self.assertEqual(exposure(60, intervals, 120, 300), 0)

    def test_constant_temperature_does_not_identify_heating(self):
        data = [window(i) for i in range(4)]
        for w in data:
            w['samples'] = [(t, 20) for t, _ in w['samples']]
        self.assertEqual(fit(data[:2], data[2:])['status'], 'not_identified')

    def test_unobservable_delay_does_not_fabricate_a_fit(self):
        result = fit_response_model([window(0), window(1)], [window(2), window(3)],
                                    delays=[10000], time_constants=[300])
        self.assertEqual(result['status'], 'not_identified')

    def test_invalid_candidate_timing_rejected(self):
        for delay in (True, -1, float('nan')):
            with self.assertRaises(ValueError):
                fit_response_model([window(0), window(1)], [window(2), window(3)],
                                   delays=[delay], time_constants=[300])

    def test_search_boundary_is_reported_not_silently_accepted(self):
        result = fit_response_model([window(0), window(1)], [window(2), window(3)],
                                    delays=[120], time_constants=[300])
        self.assertTrue(result['on_search_boundary'])
        self.assertEqual(result['status'], 'experimental_model_only')


if __name__ == '__main__':
    unittest.main()
