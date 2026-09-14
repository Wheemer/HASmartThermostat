"""Sensor-event regressions that should not require a running Home Assistant."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_lifecycle_standalone import lifecycle


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/smart_thermostat/climate.py"
tree = ast.parse(SOURCE.read_text())
thermostat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmartThermostat")
methods = [n for n in thermostat.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
           and n.name in ("_async_sensor_changed", "_async_update_temp")]


def callback(func):
    return func


class GenericStub:
    def __class_getitem__(cls, _item):
        return cls


namespace = {
    "callback": callback,
    "entity_operation": lifecycle.entity_operation,
    "Event": GenericStub,
    "EventStateChangedData": GenericStub,
    "time": SimpleNamespace(time=Mock(return_value=1234.5)),
    "_LOGGER": SimpleNamespace(debug=Mock()),
}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
             str(SOURCE), "exec"), namespace)


class SensorEventTests(unittest.IsolatedAsyncioTestCase):
    def thermostat(self):
        t = SimpleNamespace(
            _operations=lifecycle.EntityOperations(),
            _observer=None,
            _previous_temp_time=10,
            _cur_temp_time=20,
            _previous_temp=21.0,
            _current_temp=21.2,
            _last_sensor_update=100,
            _sensor_entity_id="sensor.room",
            _trigger_source=None,
            _async_control_heating=AsyncMock(),
            async_write_ha_state=Mock(),
            entity_id="climate.room",
        )
        t._async_update_temp = lambda state, update_time=None: namespace["_async_update_temp"](
            t, state, update_time)
        return t

    async def test_invalid_sensor_event_does_not_advance_pid_timestamps(self):
        t = self.thermostat()
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="unavailable")})

        await namespace["_async_sensor_changed"](t, event)

        self.assertEqual(t._previous_temp_time, 10)
        self.assertEqual(t._cur_temp_time, 20)
        self.assertEqual(t._current_temp, 21.2)
        self.assertEqual(t._last_sensor_update, 100)
        t._async_control_heating.assert_not_awaited()
        t.async_write_ha_state.assert_not_called()

    async def test_valid_sensor_event_updates_temperature_before_control(self):
        t = self.thermostat()
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="21.4")})

        await namespace["_async_sensor_changed"](t, event)

        self.assertEqual(t._previous_temp_time, 20)
        self.assertEqual(t._cur_temp_time, 1234.5)
        self.assertEqual(t._previous_temp, 21.2)
        self.assertEqual(t._current_temp, 21.4)
        self.assertEqual(t._last_sensor_update, 1234.5)
        t._async_control_heating.assert_awaited_once_with(calc_pid=True)
        t.async_write_ha_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()
