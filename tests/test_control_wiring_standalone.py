"""Exercise the actual control method with fake HA services; no heater calls."""

import ast
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_history_learning_standalone import CoastControl, Observer
from test_lifecycle_standalone import lifecycle


source = Path(__file__).resolve().parents[1] / 'custom_components/smart_thermostat/climate.py'
tree = ast.parse(source.read_text())
thermostat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SmartThermostat')
method = next(n for n in thermostat.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == '_async_control_heating')
namespace = {'time': time, 'HVACMode': SimpleNamespace(OFF='off', HEAT='heat'),
             'entity_operation': lifecycle.entity_operation,
             '_LOGGER': logging.getLogger(__name__)}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
control_method = namespace['_async_control_heating']


class RemovalBase:
    def __init__(self):
        self._output_reconcile_task = None

    async def async_will_remove_from_hass(self):
        self.base_removed = True


remove_method = next(n for n in thermostat.body if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == 'async_will_remove_from_hass')
removal_class = ast.ClassDef(name='RemovalHarness', bases=[ast.Name(id='RemovalBase', ctx=ast.Load())],
                             keywords=[], body=[remove_method], decorator_list=[], type_params=[])
namespace.update(RemovalBase=RemovalBase, asyncio=asyncio)
exec(compile(ast.fix_missing_locations(ast.Module(body=[removal_class], type_ignores=[])),
             str(source), 'exec'), namespace)


class WiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_unload_cancels_import_and_persists_without_actuation(self):
        instance = namespace['RemovalHarness']()
        instance._operations = lifecycle.EntityOperations()
        instance._history_import_task = asyncio.create_task(asyncio.sleep(60))
        instance._observer_store = SimpleNamespace(async_save=AsyncMock())
        instance._learning_snapshot = lambda: {'completed_cycles': 6}
        instance.entity_id = 'climate.thermostat'
        await instance.async_will_remove_from_hass()
        self.assertTrue(instance._history_import_task.cancelled())
        instance._observer_store.async_save.assert_awaited_once_with({'completed_cycles': 6})
        self.assertTrue(instance.base_removed)

    async def test_unload_drains_control_before_saving_learning(self):
        from test_lifecycle_standalone import Device

        device = Device()
        task = asyncio.create_task(device.waiting_control())
        await device.started.wait()
        instance = namespace['RemovalHarness']()
        instance._operations = device._operations
        instance._history_import_task = None
        instance._learning_snapshot = lambda: {'completed_cycles': 6}
        instance.entity_id = 'climate.thermostat'

        async def save(snapshot):
            self.assertTrue(task.cancelled())
            self.assertFalse(instance._operations.tasks)

        instance._observer_store = SimpleNamespace(async_save=AsyncMock(side_effect=save))
        await instance.async_will_remove_from_hass()
        await device.send()
        self.assertEqual(device.calls, [])
        self.assertTrue(instance.base_removed)

    async def test_persistence_failure_still_runs_base_cleanup(self):
        instance = namespace['RemovalHarness']()
        instance._operations = lifecycle.EntityOperations()
        instance._history_import_task = None
        instance._learning_snapshot = lambda: {}
        instance._observer_store = SimpleNamespace(async_save=AsyncMock(side_effect=OSError('disk error')))
        instance.entity_id = 'climate.thermostat'
        with self.assertLogs(namespace['_LOGGER'], level='ERROR'):
            await instance.async_will_remove_from_hass()
        self.assertTrue(instance.base_removed)
        self.assertTrue(instance._operations.closed)

    async def test_unload_cancels_reconciliation_before_it_even_starts(self):
        instance = namespace['RemovalHarness']()
        instance._operations = lifecycle.EntityOperations()
        pending = AsyncMock()
        instance._output_reconcile_task = asyncio.create_task(pending())
        instance._history_import_task = None
        instance._observer_store = None
        instance.entity_id = 'climate.thermostat'
        await instance.async_will_remove_from_hass()
        self.assertTrue(instance._output_reconcile_task.cancelled())
        pending.assert_not_awaited()
        self.assertTrue(instance.base_removed)

    def make_thermostat(self):
        now = time.time()
        sensor = SimpleNamespace(state='21.8', last_updated=datetime.now(timezone.utc))
        output = SimpleNamespace(state='on')
        t = SimpleNamespace(
            _operations=lifecycle.EntityOperations(),
            _temp_lock=asyncio.Lock(), _observer=Observer(), _coast_control=CoastControl(),
            _observer_store=SimpleNamespace(async_delay_save=Mock()),
            _observe_temperature=Mock(), _learning_snapshot=Mock(),
            hass=SimpleNamespace(states=SimpleNamespace(get=lambda e: sensor if e == 'sensor.temp' else output)),
            _sensor_entity_id='sensor.temp', _heater_entity_id=['switch.heat'],
            _active=True, _hvac_mode='heat', _current_temp=21.8, _target_temp=22,
            _force_off_state=True, _is_device_active=True, _pwm=900,
            _heater_polarity_invert=False, _ac_mode=False, _autotune='none', pid_mode='auto',
            _sensor_stall=0, _sampling_period=timedelta(0), _ext_temp=None,
            _pid_output=20, _e=0, _output_precision=1, _max_out=100, _min_out=0,
            _min_on_cycle_duration=timedelta(seconds=150),
            _async_heater_turn_off=AsyncMock(), set_control_value=AsyncMock(),
            async_write_ha_state=Mock(), entity_id='climate.thermostat',
            heater_or_cooler_entity=['switch.heat'], calc_pid=AsyncMock(),
        )
        t._observer.records = [dict(completed=now - 3600, runtime_seconds=150, coast=0.3)
                               for _ in range(6)]
        t._coast_control.observe_output(now - 180, False)
        t._coast_control.observe_output(now - 151, True)
        return t, sensor, output

    async def test_rejected_predictive_cutoff_is_no_longer_wired(self):
        t, _, _ = self.make_thermostat()
        await control_method(t)
        t._async_heater_turn_off.assert_not_awaited()
        t.set_control_value.assert_awaited_once()
        self.assertEqual(t._control_output, 20)
        self.assertEqual(t._target_temp, 22)
        t._observer_store.async_delay_save.assert_not_called()

    async def test_explicit_off_takes_precedence(self):
        t, _, _ = self.make_thermostat()
        t._hvac_mode = 'off'
        await control_method(t)
        t._async_heater_turn_off.assert_awaited_once_with(force=True)
        self.assertFalse(t._coast_control.suppressed)

    async def test_force_off_state_false_does_not_emit_repeated_off_commands(self):
        t, _, _ = self.make_thermostat()
        t._hvac_mode = 'off'
        t._force_off_state = False
        await control_method(t)
        t._async_heater_turn_off.assert_not_awaited()
        t.set_control_value.assert_not_awaited()

    async def test_unavailable_sensor_does_not_apply_calibration(self):
        t, sensor, _ = self.make_thermostat()
        sensor.state = 'unavailable'
        await control_method(t)
        t._async_heater_turn_off.assert_not_awaited()
        t.set_control_value.assert_awaited_once()

    async def test_unavailable_startup_sensor_defers_control_until_valid_reading(self):
        t, _, _ = self.make_thermostat()
        t._active = False
        t._current_temp = None

        await control_method(t, calc_pid=True)
        t.calc_pid.assert_not_awaited()
        t.set_control_value.assert_not_awaited()

        t._current_temp = 21.8
        await control_method(t, calc_pid=True)
        t.calc_pid.assert_awaited_once()
        t.set_control_value.assert_awaited_once()
        self.assertTrue(t._active)

    async def test_stale_sensor_does_not_apply_calibration(self):
        t, sensor, _ = self.make_thermostat()
        sensor.last_updated -= timedelta(minutes=10)
        await control_method(t)
        t._async_heater_turn_off.assert_not_awaited()
        t.set_control_value.assert_awaited_once()

    async def test_ac_controller_unchanged(self):
        t, _, _ = self.make_thermostat()
        t._ac_mode = True
        await control_method(t)
        t.set_control_value.assert_awaited_once()
        t._async_heater_turn_off.assert_not_awaited()

    async def test_no_predictive_hold_changes_normal_output(self):
        t, _, output = self.make_thermostat()
        await control_method(t)
        output.state = 'off'
        t._is_device_active = False
        await control_method(t)
        await control_method(t)
        t._async_heater_turn_off.assert_not_awaited()
        self.assertEqual(t.set_control_value.await_count, 3)

    async def test_control_output_respects_effective_output_max(self):
        t, _, _ = self.make_thermostat()
        t._pid_output = 33.6
        t._max_out = 10
        await control_method(t)
        self.assertEqual(t._control_output, 10)
        t.set_control_value.assert_awaited_once()

    async def test_disabled_feature_preserves_output(self):
        t, _, _ = self.make_thermostat()
        t._coast_control = None
        await control_method(t)
        self.assertEqual(t._control_output, 20)
        t.set_control_value.assert_awaited_once()

    async def test_keep_alive_tick_advances_autotune_with_last_valid_temperature(self):
        t, _, _ = self.make_thermostat()
        t._autotune = 'tyreus-luyben'
        await control_method(t)
        t.calc_pid.assert_awaited_once()
        t.set_control_value.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
