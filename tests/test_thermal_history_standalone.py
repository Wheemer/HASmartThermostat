"""History provenance for the offline model experiment."""

import unittest
from thermal_history_lab import extract_windows
from thermal_model_lab import fit_response_model, residual_bound
from test_thermal_model_standalone import window


def history():
    return {
        'temperature': [{'timestamp': t, 'state': str(20 + t / 10000)} for t in range(0, 1801, 30)],
        'climate': [{'timestamp': -5000, 'state': 'heat', 'attributes': {
            'pid_mode': 'auto', 'temperature': 22, 'kp': 100, 'ki': 0, 'kd': 0}, 'user_id': None}],
        'heater': [{'timestamp': t, 'state': s, 'user_id': None}
                   for t, s in [(-5000, 'off'), (120, 'on'), (420, 'off')]],
    }


def extract(data):
    return extract_windows(data, 'temperature', 'climate', 'heater')


class HistoryModelTests(unittest.TestCase):
    def test_physical_pulse_retains_prior_off_and_raw_samples(self):
        data = history()
        windows, report = extract(data)
        self.assertEqual(report['windows'], 1)
        self.assertEqual(windows[0]['prior_off_seconds'], 5120)
        self.assertNotIn('initial_response_at_rest', windows[0])
        self.assertEqual(windows[0]['on_intervals'], [(120, 420)])
        self.assertTrue(report['context_ids_complete'])

    def test_missing_context_ids_are_not_evidence_of_automatic_operation(self):
        data = history()
        del data['heater'][1]['user_id']
        windows, report = extract(data)
        self.assertEqual(len(windows), 1)
        self.assertFalse(report['context_ids_complete'])

    def test_output_unknown_and_reheat_each_invalidate_window(self):
        for state in ('on', 'unavailable'):
            data = history()
            data['heater'].append({'timestamp': 700, 'state': state})
            self.assertEqual(extract(data)[0], [])

    def test_manual_output_is_excluded(self):
        data = history()
        data['heater'][2]['user_id'] = 'person'
        self.assertEqual(extract(data)[0], [])

    def test_target_change_is_excluded(self):
        data = history()
        data['climate'].append({**data['climate'][0], 'timestamp': 300, 'attributes': {
            **data['climate'][0]['attributes'], 'temperature': 23}})
        self.assertEqual(extract(data)[0], [])

    def test_sensor_gap_is_excluded(self):
        data = history()
        data['temperature'] = [r for r in data['temperature'] if not 300 <= r['timestamp'] <= 600]
        self.assertEqual(extract(data)[0], [])

    def test_initial_residual_bound_includes_transport_delay(self):
        self.assertEqual(residual_bound({'prior_off_seconds': 60}, 120, 300), 1)
        self.assertAlmostEqual(residual_bound({'prior_off_seconds': 420}, 120, 300), 0.367879441)

    def test_uncertain_holdout_does_not_select_different_fit(self):
        data = [window(i) for i in range(4)]
        for w in data[2:]:
            del w['initial_response_at_rest']
            w['prior_off_seconds'] = 100
        result = fit_response_model(data[:2], data[2:], delays=[0, 120, 240], time_constants=[120, 300, 600])
        self.assertEqual(result['status'], 'holdout_initial_state_uncertain')
        self.assertEqual(result['delay_seconds'], 120)
        self.assertEqual(result['response_time_seconds'], 300)

    def test_insufficient_prior_off_withholds_training_fit(self):
        data = [window(i) for i in range(4)]
        for w in data:
            del w['initial_response_at_rest']
            w['prior_off_seconds'] = 100
        result = fit_response_model(data[:2], data[2:], delays=[120], time_constants=[300])
        self.assertEqual(result['status'], 'not_identified')

    def test_one_bad_holdout_window_is_visible_separately(self):
        result = fit_response_model([window(0), window(1)],
                                    [window(2), window(3, heat_rate=0)],
                                    delays=[0, 120, 240], time_constants=[120, 300, 600])
        self.assertEqual(len(result['validation_windows']), 2)
        self.assertLess(result['validation_windows'][0]['rmse_c'], 0.003)
        self.assertGreater(result['validation_windows'][1]['max_error_c'], 0.1)
        self.assertFalse(result['every_window_beats_drift_only'])

    def test_boundary_sensor_need_not_coincide_with_relay_edge(self):
        data = history()
        data['heater'][1]['timestamp'] = 125
        windows, _ = extract(data)
        self.assertEqual(windows[0]['samples'][0][0], 120)
        self.assertEqual(windows[0]['on_intervals'][0][0], 125)
        self.assertNotIn(125, [t for t, _ in windows[0]['samples']])


if __name__ == '__main__':
    unittest.main()
