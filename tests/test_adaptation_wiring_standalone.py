"""Exercise the actual thermostat gain-update methods without live HA services."""

import ast
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import logging
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock
from test_history_learning_standalone import PACKAGE, Observer
from test_pid_adaptation_standalone import Adaptation

transaction = importlib.import_module(PACKAGE + '.gain_transaction')
has_session_timing = importlib.import_module(PACKAGE + '.adaptive').has_session_timing
source = Path(__file__).resolve().parents[1] / 'custom_components/smart_thermostat/climate.py'
tree = ast.parse(source.read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SmartThermostat')
names = ('_adaptive_gains', '_adaptive_context', '_learning_snapshot', '_async_adapt_pid')
methods = [deepcopy(n) for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
for method in methods:
    method.decorator_list = []
harness = ast.ClassDef(name='Harness', bases=[], keywords=[], body=methods, decorator_list=[], type_params=[])
namespace = dict(time=time, isfinite=isfinite, HVACMode=SimpleNamespace(HEAT='heat'),
                 has_session_timing=has_session_timing,
                 commit_gain_change=transaction.commit_gain_change, _LOGGER=logging.getLogger(__name__))
exec(compile(ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[])), str(source), 'exec'), namespace)


class WiringTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        t = namespace['Harness']()
        now = time.time()
        t._kp, t._ki, t._kd = 100, 0.01, 100
        t._pid_adaptation = Adaptation({'kp': (10, 500), 'ki': (0, 1), 'kd': (0, 12000)})
        t._adaptive_ready, t._adaptive_error, t._adaptive_journal = True, None, None
        t._hvac_mode, t._ac_mode, t._pwm, t._heater_polarity_invert = 'heat', False, 900, False
        t._autotune, t.pid_mode, t._target_temp = 'none', 'auto', 22
        t._pid_controller = SimpleNamespace(set_pid_param=Mock(), integral=7)
        t._observer = Observer()
        t._observer.records = [dict(started=now - 2000 + i * 200, stopped=now - 1850 + i * 200,
            completed=now - 1800 + i * 200, gains=t._adaptive_gains(), cycle_basis='demand_session', pwm_seconds=900,
            pid_metrics=dict(overshoot=0.4, undershoot=0, oscillations=0, rise_time=4, settling_time=8))
            for i in range(6)]
        t._observer_store = SimpleNamespace(async_save=AsyncMock())
        sensor = SimpleNamespace(state='22', last_updated=datetime.now(timezone.utc))
        output = SimpleNamespace(state='off')
        t.hass = SimpleNamespace(states=SimpleNamespace(get=lambda e: sensor if e == 'sensor.temp' else output))
        t._sensor_entity_id, t._heater_entity_id = 'sensor.temp', ['switch.heat']
        t.async_write_ha_state = Mock()
        t.entity_id = 'climate.thermostat'
        return t, sensor, output

    async def test_real_method_changes_pid_not_target_or_mode(self):
        t, _, _ = self.make()
        self.assertTrue(await t._async_adapt_pid())
        t._pid_controller.set_pid_param.assert_called_once_with(kp=100, ki=0.01, kd=120)
        self.assertEqual((t._target_temp, t._hvac_mode), (22, 'heat'))
        self.assertEqual(t._pid_controller.integral, 0)
        self.assertIsNotNone(t._pid_adaptation.pending)

    async def test_no_change_while_heater_on(self):
        t, _, output = self.make()
        output.state = 'on'
        self.assertFalse(await t._async_adapt_pid())
        t._pid_controller.set_pid_param.assert_not_called()

    async def test_legacy_pulse_records_cannot_drive_automatic_gain_changes(self):
        t, _, _ = self.make()
        for record in t._observer.records:
            record.pop('cycle_basis')
        self.assertFalse(await t._async_adapt_pid())
        t._pid_controller.set_pid_param.assert_not_called()
        self.assertEqual(t._pid_adaptation.reason, 'insufficient_comparable_cycles')

    async def test_unknown_or_different_pwm_records_cannot_drive_gains(self):
        for period in (None, 600):
            t, _, _ = self.make()
            for record in t._observer.records:
                record['pwm_seconds'] = period
            self.assertFalse(await t._async_adapt_pid())
            t._pid_controller.set_pid_param.assert_not_called()

    async def test_real_method_rolls_back_after_worse_cycles(self):
        t, _, _ = self.make()
        with self.assertLogs(namespace['_LOGGER'], level='WARNING'):
            self.assertTrue(await t._async_adapt_pid())
        now = time.time()
        t._pid_adaptation.pending['applied_at'] = now - 2000
        t._pid_adaptation.last_change = now - 2000
        t._observer.records = [dict(started=now - 1500 + i * 200, completed=now - 1400 + i * 200,
            gains=t._adaptive_gains(), cycle_basis='demand_session', pwm_seconds=900, pid_metrics=dict(overshoot=0.8, undershoot=0,
                oscillations=0, rise_time=4, settling_time=8)) for i in range(5)]
        with self.assertLogs(namespace['_LOGGER'], level='WARNING'):
            self.assertTrue(await t._async_adapt_pid())
        self.assertEqual(t._kd, 100)
        self.assertEqual(t._pid_adaptation.reason, 'rolled_back')
        self.assertIsNone(t._pid_adaptation.pending)
        self.assertEqual((t._target_temp, t._hvac_mode), (22, 'heat'))

    async def test_missing_derivative_is_seeded_and_validated(self):
        t, _, _ = self.make()
        t._ki = t._kd = 0
        for record in t._observer.records:
            record['gains'] = t._adaptive_gains()
        self.assertTrue(await t._async_adapt_pid())
        t._pid_controller.set_pid_param.assert_called_once()
        gains = t._pid_controller.set_pid_param.call_args.kwargs
        self.assertEqual(gains['ki'], 0)
        self.assertGreater(gains['kd'], 0)
        self.assertEqual(t._pid_adaptation.pending['seeded_gains'], ['kd'])

    async def test_nan_sensor_cannot_allow_gain_change(self):
        t, sensor, _ = self.make()
        sensor.state = 'nan'
        self.assertFalse(await t._async_adapt_pid())

    async def test_completed_gain_change_recalculates_even_when_final_save_fails(self):
        t, _, _ = self.make()
        t._observer_store.async_save.side_effect = [None, OSError('write failed')]
        with self.assertLogs(namespace['_LOGGER'], level='ERROR'):
            self.assertTrue(await t._async_adapt_pid())
        self.assertEqual(t._kd, 120)
        self.assertEqual(t._adaptive_error, 'gain_update_failed')

    async def test_manual_off_during_storage_write_prevents_apply(self):
        t, _, _ = self.make()
        async def save(data):
            t._hvac_mode = 'off'
        t._observer_store.async_save.side_effect = save
        self.assertFalse(await t._async_adapt_pid())
        t._pid_controller.set_pid_param.assert_not_called()


if __name__ == '__main__':
    unittest.main()
