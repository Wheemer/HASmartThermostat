"""Whole-day holdout avoids temperature-sample leakage across a split."""

import unittest
from test_thermal_model_standalone import window
from thermal_validation_lab import validate_by_day


def dataset():
    windows = []
    for index in range(6):
        w = window(index)
        shift = (index // 2) * 86400 + (index % 2) * 3600 - index * 86400
        w['samples'] = [(t + shift, v) for t, v in w['samples']]
        w['on_intervals'] = [(a + shift, b + shift) for a, b in w['on_intervals']]
        windows.append(w)
    return windows


def validate(windows):
    return validate_by_day(windows, timezone='UTC', delays=[0, 120, 240], time_constants=[120, 300, 600])


class DayValidationTests(unittest.TestCase):
    def test_expanding_training_uses_only_earlier_whole_days(self):
        report = validate(dataset())
        self.assertEqual([f['training_windows'] for f in report['folds']], [2, 4])
        self.assertEqual([f['validation_windows_count'] for f in report['folds']], [2, 2])
        self.assertEqual(len(report['skipped_days']), 1)
        self.assertIsNone(report['initial_gains'])

    def test_future_temperatures_cannot_change_earlier_fold(self):
        data = dataset()
        before = validate(data)
        data[-1]['samples'] = [(t, v + (t - data[-1]['samples'][0][0]) / 1000)
                               for t, v in data[-1]['samples']]
        after = validate(data)
        self.assertEqual(before['folds'][0], after['folds'][0])

    def test_missing_provenance_is_not_inferred_from_good_prediction(self):
        report = validate(dataset())
        self.assertFalse(report['context_ids_complete'])
        self.assertEqual(report['status'], 'not_ready_for_gain_synthesis')

    def test_single_day_cannot_validate_itself(self):
        report = validate(dataset()[:2])
        self.assertEqual(report['folds'], [])
        self.assertIsNone(report['fitted_parameter_ranges']['delay_seconds'])

    def test_midnight_crossing_window_belongs_to_completion_day(self):
        w = window(0)
        shift = 23 * 3600 + 40 * 60
        w['samples'] = [(t + shift, v) for t, v in w['samples']]
        w['on_intervals'] = [(a + shift, b + shift) for a, b in w['on_intervals']]
        report = validate([w])
        self.assertEqual(report['skipped_days'][0]['day'], '1970-01-02')
