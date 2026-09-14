"""Response measurements cannot pretend to be physical system identification."""

import unittest

from test_history_learning_standalone import Observer
from _thermal_test.thermal_response import measure_response, summarize_responses


class ResponseTests(unittest.TestCase):
    def test_on_rise_excludes_pwm_gap_and_coast(self):
        samples = [(0, 20), (120, 20.2), (240, 20.5), (360, 20.8), (480, 21)]
        result = measure_response(samples, [(0, 120), (240, 360)])
        self.assertEqual(result['on_seconds'], 240)
        self.assertAlmostEqual(result['on_temperature_change_c'], 0.5)
        self.assertAlmostEqual(result['net_on_rate_c_per_hour'], 7.5)
        self.assertAlmostEqual(result['post_off_rise_c'], 0.2)
        self.assertEqual(result['post_off_peak_seconds'], 120)

    def test_rate_is_weighted_by_actual_duration_not_pulse_count(self):
        result = measure_response([(0, 20), (60, 20.1), (120, 20.1), (300, 20.2)],
                                  [(0, 60), (120, 300)])
        self.assertAlmostEqual(result['net_on_rate_c_per_hour'], 3)

    def test_peak_plateau_uses_first_maximum(self):
        result = measure_response([(0, 20), (120, 20.2), (240, 20.5), (360, 20.5)], [(0, 120)])
        self.assertEqual(result['post_off_peak_seconds'], 120)

    def test_cooling_during_heating_is_not_hidden_or_clamped(self):
        result = measure_response([(0, 20), (120, 19.9), (240, 19.8)], [(0, 120)])
        self.assertLess(result['net_on_rate_c_per_hour'], 0)
        summary = summarize_responses([{'thermal_response': result}])
        self.assertEqual(summary['nonpositive_on_rise_count'], 1)
        self.assertIsNone(summary['initial_gains'])

    def test_missing_endpoints_do_not_interpolate_unknown_temperatures(self):
        self.assertIsNone(measure_response([(0, 20), (120, 20.2)], [(0, 60)]))

    def test_invalid_intervals_rejected(self):
        for intervals in ([(0, 0)], [(120, 0)], [(0, 120), (60, 120)], [(True, 120)]):
            self.assertIsNone(measure_response([(0, 20), (60, 20.1), (120, 20.2)], intervals))

    def test_invalid_samples_rejected(self):
        for samples in ([(0, 20), (0, 21)], [(120, 20), (0, 21)], [(0, 20), (120, float('nan'))]):
            self.assertIsNone(measure_response(samples, [(0, 120)]))

    def test_old_records_do_not_get_invented_responses(self):
        summary = summarize_responses([{'runtime_seconds': 300, 'coast': 0.3}])
        self.assertEqual(summary['response_count'], 0)
        self.assertEqual(summary['status'], 'insufficient_observations')

    def test_delayed_temperature_rise_is_not_counted_as_on_interval_rise(self):
        result = measure_response([(0, 20), (120, 20), (240, 20.3), (360, 20.4)], [(0, 120)])
        self.assertEqual(result['net_on_rate_c_per_hour'], 0)
        self.assertAlmostEqual(result['post_off_rise_c'], 0.4)
        summary = summarize_responses([{'thermal_response': result}] * 10)
        self.assertEqual(summary['status'], 'observations_only')
        self.assertIsNone(summary['initial_gains'])

    def test_invalid_saved_response_does_not_pollute_summary(self):
        result = measure_response([(0, 20), (120, 20.2), (240, 20.3)], [(0, 120)])
        for field, invalid in [('on_seconds', 0), ('net_on_rate_c_per_hour', float('nan')),
                               ('post_off_rise_c', -1), ('post_off_peak_seconds', 1000)]:
            summary = summarize_responses([{'thermal_response': {**result, field: invalid}}])
            self.assertEqual(summary['response_count'], 0)

    def test_observer_persists_measured_intervals_without_restoring_active_cycle(self):
        observer = Observer(settle_seconds=0)
        for t in range(0, 601, 30):
            on = 60 <= t < 240 or 300 <= t < 480
            observer.sample(t, 20 + t / 1000, 21, on, demand=60 <= t < 480, pwm_seconds=30)
        self.assertEqual(len(observer.records), 1)
        response = observer.records[0]['thermal_response']
        self.assertEqual(response['on_intervals'], 2)
        self.assertEqual(response['on_seconds'], 360)
        restored = Observer()
        restored.restore(observer.snapshot())
        self.assertEqual(restored.records[0]['thermal_response'], response)
        self.assertIsNone(restored._cycle)
        self.assertEqual(restored.diagnostics()['calibration_evidence']['response_count'], 1)


if __name__ == '__main__':
    unittest.main()
