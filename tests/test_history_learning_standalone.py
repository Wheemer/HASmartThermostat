"""Pure learning tests, runnable without Home Assistant installed."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'smart_thermostat'
PACKAGE = '_thermal_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
for name in ('adaptive_cycle_analysis', 'pid_cycle_metrics', 'adaptive', 'history_learning', 'coast_control',
             'adaptive_pid_constants', 'adaptive_pid_rules', 'pid_adaptation'):
    spec = importlib.util.spec_from_file_location(f'{PACKAGE}.{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

replay = sys.modules[f'{PACKAGE}.history_learning'].replay_history
Observer = sys.modules[f'{PACKAGE}.adaptive'].ThermalObserver
CoastControl = sys.modules[f'{PACKAGE}.coast_control'].CoastControl
predict_coast = sys.modules[f'{PACKAGE}.coast_control'].predict_coast


def row(timestamp, state, **attributes):
    return dict(timestamp=timestamp, state=state, attributes=attributes)


def history():
    return {
        'sensor.temp': [row(t, str(20 + min(t, 900) / 1800))
                        for t in range(0, 1501, 30)],
        'climate.heat': [row(0, 'heat', temperature=21, pid_mode='auto')],
        'switch.heat': [row(0, 'off'), row(60, 'on'), row(360, 'off')],
    }


def run(data):
    return replay(data, 'sensor.temp', 'climate.heat', ['switch.heat'], 0, 1500)


class HistoryTests(unittest.TestCase):
    def test_six_legacy_pulses_do_not_claim_calibration_confidence(self):
        data = {entity: [] for entity in history()}
        for offset in range(0, 6 * 1800, 1800):
            for entity, rows in history().items():
                data[entity].extend({**r, 'timestamp': r['timestamp'] + offset} for r in rows)
        _, report = replay(data, 'sensor.temp', 'climate.heat', ['switch.heat'], 0, 10800)
        self.assertEqual(report['completed_cycles'], 6)
        self.assertEqual(report['timed_demand_sessions'], 0)
        self.assertEqual(report['confidence'], 'limited')

    def test_legacy_control_output_history_can_be_pwm_tagged_by_importer(self):
        data = {'sensor.temp': [], 'climate.heat': [], 'switch.heat': []}
        for offset in range(0, 6 * 1800, 1800):
            data['sensor.temp'].extend(row(t + offset, str(20 + min(t, 900) / 1800))
                                       for t in range(0, 1501, 30))
            data['climate.heat'].extend([
                row(offset, 'heat', temperature=21, pid_mode='auto',
                    control_output=0, kp=100, ki=0, kd=0),
                row(offset + 60, 'heat', temperature=21, pid_mode='auto',
                    control_output=100, kp=100, ki=0, kd=0),
                row(offset + 360, 'heat', temperature=21, pid_mode='auto',
                    control_output=0, kp=100, ki=0, kd=0),
            ])
            data['switch.heat'].extend([
                row(offset, 'off'),
                row(offset + 60, 'on'),
                row(offset + 360, 'off'),
            ])
        snapshot, report = replay(
            data, 'sensor.temp', 'climate.heat', ['switch.heat'], 0, 10800,
            fallback_pwm_seconds=900)
        self.assertEqual(report['timed_demand_sessions'], 6)
        self.assertTrue(all(r['pwm_seconds'] == 900 for r in snapshot['records']))

    def test_pre_window_context_keeps_unchanged_climate(self):
        data = history()
        data['climate.heat'][0]['timestamp'] = -86400
        data['switch.heat'][0]['timestamp'] = -3600
        self.assertEqual(run(data)[1]['completed_cycles'], 1)

    def test_latest_pre_window_context_wins_even_when_unsorted(self):
        data = history()
        data['climate.heat'] = [row(-10, 'heat', temperature=21, pid_mode='auto'),
                                row(-3600, 'off', temperature=21, pid_mode='auto')]
        self.assertEqual(run(data)[1]['completed_cycles'], 1)

    def test_boundary_state_supersedes_pre_window_context(self):
        data = history()
        data['climate.heat'].append(row(-10, 'off', temperature=21, pid_mode='auto'))
        self.assertEqual(run(data)[1]['completed_cycles'], 1)

    def test_pre_window_heating_is_not_a_complete_cycle(self):
        data = history()
        data['switch.heat'] = [row(-300, 'on'), row(360, 'off')]
        self.assertEqual(run(data)[0]['records'], [])

    def test_old_temperature_context_is_not_freshened(self):
        data = history()
        data['sensor.temp'] = [row(-3600, '20')]
        saved, report = run(data)
        self.assertEqual(saved['records'], [])
        self.assertGreater(report['rejections']['stale_temperature'], 0)

    def test_complete_cycle(self):
        saved, report = run(history())
        self.assertEqual(report['completed_cycles'], 1)
        self.assertEqual(saved['records'][0]['runtime_seconds'], 300)
        self.assertAlmostEqual(saved['records'][0]['coast'], 0.3)
        self.assertEqual(report['confidence'], 'limited')

    def test_initial_on_not_invented_cycle(self):
        data = history()
        data['switch.heat'] = [row(0, 'on'), row(360, 'off')]
        self.assertEqual(run(data)[0]['records'], [])

    def test_target_change_rejects_cycle(self):
        data = history()
        data['climate.heat'].append(row(240, 'heat', temperature=22, pid_mode='auto'))
        self.assertEqual(run(data)[0]['records'], [])

    def test_gain_change_rejects_cycle(self):
        data = history()
        data['climate.heat'][0]['attributes'].update(kp=100, ki=0, kd=0)
        data['climate.heat'].append(row(240, 'heat', temperature=21, pid_mode='auto',
                                         kp=80, ki=0, kd=0))
        self.assertEqual(run(data)[0]['records'], [])

    def test_records_keep_gains_that_produced_them(self):
        data = history()
        data['climate.heat'][0]['attributes'].update(kp=100, ki=0, kd=0)
        saved, _ = run(data)
        self.assertEqual(saved['records'][0]['gains'], {'kp': 100, 'ki': 0, 'kd': 0})

    def test_manual_off_rejects_cycle(self):
        data = history()
        data['switch.heat'][-1]['user_id'] = 'person'
        self.assertEqual(run(data)[0]['records'], [])

    def test_sensor_gap_rejects_cycle(self):
        data = history()
        data['sensor.temp'] = [r for r in data['sensor.temp'] if not 180 < r['timestamp'] < 600]
        self.assertEqual(run(data)[0]['records'], [])

    def test_restart_rejects_cycle(self):
        data = history()
        data['switch.heat'].append(row(240, 'unavailable'))
        self.assertEqual(run(data)[0]['records'], [])

    def test_reheat_rejects_unfinished_coast(self):
        data = history()
        data['switch.heat'].append(row(600, 'on'))
        self.assertEqual(run(data)[0]['records'], [])

    def test_missing_entity(self):
        data = history()
        del data['sensor.temp']
        self.assertEqual(run(data)[0]['records'], [])

    def test_duplicate_timestamp(self):
        data = history()
        data['sensor.temp'].append(row(120, '20.1'))
        self.assertEqual(run(data)[1]['completed_cycles'], 1)

    def test_restore_does_not_restore_incomplete_cycle(self):
        observer = Observer()
        observer.restore(run(history())[0])
        self.assertEqual(len(observer.records), 1)
        self.assertIsNone(observer._cycle)
        self.assertFalse(observer._armed)

    def test_import_does_not_count_same_pulse_twice(self):
        observer = Observer()
        records = run(history())[0]['records']
        observer.restore({'version': 1, 'records': records})
        shifted = [dict(r, stopped=r['stopped'] + 0.2, completed=r['completed'] + 0.3)
                   for r in records]
        observer.merge_history(shifted, 0)
        self.assertEqual(len(observer.records), 1)

    def test_invalid_temperature(self):
        data = history()
        data['sensor.temp'].append(row(210, 'nan'))
        self.assertEqual(run(data)[0]['records'], [])


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_fourteen_days_without_commands(self):
        end = datetime(2026, 9, 10, tzinfo=timezone.utc)
        calls = []

        def query(hass, start, stop, entities, **kwargs):
            calls.append((start, stop, entities, kwargs))
            return {}

        class Executor:
            async def async_add_executor_job(self, function, *args):
                return function(*args)

        recorder = Executor()
        modules = {}
        for name in ('homeassistant', 'homeassistant.components',
                     'homeassistant.components.recorder',
                     'homeassistant.components.recorder.history',
                     'homeassistant.helpers', 'homeassistant.helpers.recorder'):
            modules[name] = types.ModuleType(name)
        modules['homeassistant.components.recorder.history'].get_significant_states = query
        modules['homeassistant.helpers.recorder'].get_instance = lambda hass: recorder
        importer = sys.modules[f'{PACKAGE}.history_learning'].import_recent_history
        with patch.dict(sys.modules, modules):
            saved, report = await importer(
                Executor(), 'sensor.temp', 'climate.heat', ['switch.heat'], end)
        self.assertEqual(len(calls), 14)
        self.assertEqual(calls[0][0], end - timedelta(days=14))
        self.assertEqual(calls[-1][1], end)
        self.assertTrue(calls[0][3]['include_start_time_state'])
        self.assertFalse(calls[1][3]['include_start_time_state'])
        self.assertTrue(all(not c[3]['minimal_response'] for c in calls))
        self.assertEqual(saved['records'], [])
        self.assertEqual(report['confidence'], 'limited')

    async def test_cancellation_propagates(self):
        import asyncio

        class CancelledExecutor:
            async def async_add_executor_job(self, *args):
                raise asyncio.CancelledError

        modules = {}
        for name in ('homeassistant', 'homeassistant.components',
                     'homeassistant.components.recorder',
                     'homeassistant.components.recorder.history',
                     'homeassistant.helpers', 'homeassistant.helpers.recorder'):
            modules[name] = types.ModuleType(name)
        modules['homeassistant.components.recorder.history'].get_significant_states = lambda: None
        modules['homeassistant.helpers.recorder'].get_instance = lambda hass: CancelledExecutor()
        importer = sys.modules[f'{PACKAGE}.history_learning'].import_recent_history
        with patch.dict(sys.modules, modules), self.assertRaises(asyncio.CancelledError):
            await importer(None, 'sensor.temp', 'climate.heat', ['switch.heat'],
                           datetime.now(timezone.utc))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.records = [dict(completed=0, runtime_seconds=150, coast=0.3) for _ in range(6)]
        self.control = CoastControl()

    def evaluate(self, now, temperature=21.8, heating=True, target=22, enabled=True):
        return self.control.evaluate(now, temperature, target, heating, self.records, 150, enabled)

    def begin(self):
        self.evaluate(0, heating=False)
        self.evaluate(30)

    def test_cutoff_only_after_minimum_on(self):
        self.begin()
        self.assertFalse(self.evaluate(179))
        self.assertTrue(self.evaluate(180))

    def test_unknown_initial_runtime_does_not_cut(self):
        self.assertFalse(self.evaluate(180))
        self.assertFalse(self.evaluate(400))

    def test_does_not_cut_far_below_target(self):
        self.begin()
        self.assertFalse(self.evaluate(180, temperature=21.0))

    def test_does_not_immediately_reheat_after_cutoff(self):
        self.begin()
        self.assertTrue(self.evaluate(180))
        self.assertTrue(self.evaluate(181, heating=False))
        self.assertTrue(self.evaluate(210, heating=False, temperature=21.85))

    def test_releases_when_temperature_falls(self):
        self.begin()
        self.evaluate(180)
        self.evaluate(181, heating=False)
        self.evaluate(240, temperature=21.95, heating=False)
        self.assertFalse(self.evaluate(390, temperature=21.85, heating=False))

    def test_bounded_hold(self):
        self.begin()
        self.evaluate(180)
        self.evaluate(181, heating=False)
        self.assertFalse(self.evaluate(1081, heating=False))

    def test_target_change_cancels_hold(self):
        self.begin()
        self.evaluate(180)
        self.evaluate(181, heating=False)
        self.assertFalse(self.evaluate(210, heating=False, target=23))

    def test_disabled_never_suppresses(self):
        self.begin()
        self.evaluate(180)
        self.assertFalse(self.evaluate(181, enabled=False))

    def test_pending_off_expires(self):
        self.begin()
        self.evaluate(180)
        # It may make a fresh prediction, but cannot remain latched to an old one.
        self.assertFalse(self.evaluate(301, temperature=20))
        self.assertIsNone(self.control._pending)

    def test_restore_preserves_hold_but_not_runtime(self):
        self.begin()
        self.evaluate(180)
        self.evaluate(181, heating=False)
        saved = self.control.snapshot()
        self.control = CoastControl()
        self.control.restore(saved, 200)
        self.assertTrue(self.evaluate(210, heating=False))
        self.assertIsNone(self.control._started)

    def test_restore_ignores_expired_hold(self):
        self.begin()
        self.evaluate(180)
        self.evaluate(181, heating=False)
        saved = self.control.snapshot()
        self.control = CoastControl()
        self.control.restore(saved, 2000)
        self.assertFalse(self.evaluate(2000, heating=False))

    def test_outlier_does_not_drive_estimate(self):
        self.records[-1]['coast'] = 8
        self.assertAlmostEqual(predict_coast(self.records, 150, 400), 0.3)

    def test_expired_data_not_used(self):
        self.assertIsNone(predict_coast(self.records, 150, 15 * 86400))

    def test_sparse_data_not_used(self):
        self.assertIsNone(predict_coast(self.records[:2], 150, 400))

    def test_dissimilar_runtime_not_used(self):
        self.assertIsNone(predict_coast(self.records, 900, 1000))

    def test_new_cycles_change_prediction(self):
        self.assertAlmostEqual(predict_coast(self.records, 150, 400), 0.3)
        for record in self.records:
            record['coast'] = 0.2
        self.assertAlmostEqual(predict_coast(self.records, 150, 400), 0.2)

    def test_restored_hold_waits_for_telemetry_then_expires(self):
        self.begin()
        self.evaluate(180)
        self.evaluate(181, heating=False)
        self.assertTrue(self.control.wait_for_telemetry(240, 22))
        self.assertFalse(self.control.wait_for_telemetry(1100, 22))

    def test_output_event_sets_exact_start(self):
        self.control.observe_output(0, False)
        self.control.observe_output(5, True)
        self.assertFalse(self.evaluate(154))
        self.assertTrue(self.evaluate(155))

    def test_settled_cycle_learned_before_reheat(self):
        observer = Observer()
        observer.sample(0, 20, 21, False)
        observer.sample(30, 20, 21, True)
        observer.sample(120, 20.1, 21, True)
        observer.sample(180, 20.2, 21, False)
        observer.sample(240, 20.4, 21, False)
        observer.sample(300, 20.38, 21, False)
        self.assertTrue(observer.sample(360, 20.3, 21, False))
        observer.sample(390, 20.3, 21, True)
        self.assertEqual(len(observer.records), 1)


if __name__ == '__main__':
    unittest.main()
