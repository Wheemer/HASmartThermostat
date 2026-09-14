"""Mode-change safeguards for slow or stateful output devices."""

import ast
import logging
from functools import partial
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_lifecycle_standalone import lifecycle


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/smart_thermostat/climate.py"
tree = ast.parse(SOURCE.read_text())
thermostat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmartThermostat")
methods = [n for n in thermostat.body if isinstance(n, ast.AsyncFunctionDef)
           and n.name in ("async_set_hvac_mode", "async_turn_on", "async_turn_off",
                          "async_set_pid", "async_set_preset_temp", "async_set_temperature")]
namespace = {
    "HVACMode": SimpleNamespace(OFF="off", HEAT="heat", COOL="cool", HEAT_COOL="heat_cool"),
    "ClimateEntityFeature": SimpleNamespace(PRESET_MODE=8),
    "ATTR_TEMPERATURE": "temperature",
    "PRESET_NONE": "none",
    "entity_operation": lifecycle.entity_operation,
    "_LOGGER": logging.getLogger(__name__),
}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
             str(SOURCE), "exec"), namespace)


class ModeChangeTests(unittest.IsolatedAsyncioTestCase):
    def thermostat(self, mode="off", active=False):
        t = SimpleNamespace(
            _operations=lifecycle.EntityOperations(),
            _hvac_mode=mode,
            _last_active_hvac_mode=mode if mode in ("heat", "cool", "heat_cool") else None,
            hvac_modes=["heat", "cool", "off"],
            _output_clamp_low=0,
            _output_clamp_high=100,
            _output_min=0,
            _min_out=0,
            _max_out=100,
            _control_output=50,
            _support_flags=1,
            _preset_modes_temp={},
            _preset_temp_modes={},
            _preset_sync_mode="none",
            min_temp=7,
            max_temp=35,
            _pwm=True,
            _pid_controller=SimpleNamespace(
                out_max=None, out_min=None, clear_samples=Mock(),
                set_pid_param=Mock()),
            _kp=1,
            _ki=2,
            _kd=3,
            _ke=4,
            _previous_temp="previous",
            _previous_temp_time="previous_time",
            _async_heater_turn_off=AsyncMock(),
            _async_set_valve_value=AsyncMock(),
            _async_control_heating=AsyncMock(),
            async_set_preset_mode=AsyncMock(),
            async_write_ha_state=Mock(),
            entity_id="climate.test",
        )
        t._is_device_active = active
        t.async_set_hvac_mode = partial(namespace["async_set_hvac_mode"], t)
        return t

    async def test_enabling_from_off_does_not_send_preemptive_off(self):
        t = self.thermostat(mode="off")
        await namespace["async_set_hvac_mode"](t, "heat")
        t._async_heater_turn_off.assert_not_awaited()
        t._async_control_heating.assert_awaited_once_with(calc_pid=True)

    async def test_retriggering_same_mode_does_not_send_preemptive_off(self):
        t = self.thermostat(mode="heat")
        await namespace["async_set_hvac_mode"](t, "heat")
        t._async_heater_turn_off.assert_not_awaited()
        t._async_control_heating.assert_awaited_once_with(calc_pid=True)

    async def test_switching_between_active_modes_turns_outputs_off_first(self):
        t = self.thermostat(mode="heat", active=True)
        await namespace["async_set_hvac_mode"](t, "cool")
        t._async_heater_turn_off.assert_awaited_once_with(force=True)
        t._async_control_heating.assert_awaited_once_with(calc_pid=True)

    async def test_turning_off_still_turns_outputs_off(self):
        t = self.thermostat(mode="heat", active=True)
        await namespace["async_set_hvac_mode"](t, "off")
        t._async_heater_turn_off.assert_awaited_once_with(force=True)
        t._async_control_heating.assert_not_awaited()

    async def test_turning_off_non_pwm_uses_proportional_output_path(self):
        t = self.thermostat(mode="heat", active=True)
        t._pwm = False
        await namespace["async_set_hvac_mode"](t, "off")
        t._async_heater_turn_off.assert_not_awaited()
        t._async_set_valve_value.assert_awaited_once_with(0)
        t._async_control_heating.assert_not_awaited()

    async def test_climate_turn_off_uses_hvac_off_path(self):
        t = self.thermostat(mode="heat", active=True)
        await namespace["async_turn_off"](t)
        t._async_heater_turn_off.assert_awaited_once_with(force=True)
        self.assertEqual(t._hvac_mode, "off")

    async def test_climate_turn_on_restores_last_active_mode(self):
        t = self.thermostat(mode="off")
        t._last_active_hvac_mode = "cool"
        await namespace["async_turn_on"](t)
        self.assertEqual(t._hvac_mode, "cool")
        t._async_control_heating.assert_awaited_once_with(calc_pid=True)

    async def test_climate_turn_on_defaults_to_supported_heat_mode(self):
        t = self.thermostat(mode="off")
        t._last_active_hvac_mode = None
        t.hvac_modes = ["heat", "off"]
        await namespace["async_turn_on"](t)
        self.assertEqual(t._hvac_mode, "heat")

    async def test_climate_turn_on_defaults_to_cool_for_cool_only_entity(self):
        t = self.thermostat(mode="off")
        t._last_active_hvac_mode = None
        t.hvac_modes = ["cool", "off"]
        await namespace["async_turn_on"](t)
        self.assertEqual(t._hvac_mode, "cool")

    async def test_pid_gain_update_does_not_immediately_actuate_output(self):
        t = self.thermostat(mode="heat", active=True)
        await namespace["async_set_pid"](t, kp=10)
        self.assertEqual(t._kp, 10)
        t._pid_controller.set_pid_param.assert_called_once_with(10, 2, 3, 4)
        t._async_control_heating.assert_not_awaited()
        t.async_write_ha_state.assert_called_once()

    async def test_target_temperature_change_forces_immediate_pid_sample(self):
        t = self.thermostat(mode="heat", active=True)
        t._current_temp = 21
        await namespace["async_set_temperature"](t, temperature=22)
        self.assertEqual(t._target_temp, 22)
        self.assertTrue(t._force_on)
        t.async_set_preset_mode.assert_awaited_once_with("none")
        t._async_control_heating.assert_awaited_once_with(calc_pid=True, force_pid=True)
        t.async_write_ha_state.assert_called_once()

    async def test_setting_preset_temp_enables_preset_mode_feature(self):
        t = self.thermostat(mode="heat", active=True)
        await namespace["async_set_preset_temp"](t, sleep_temp=20)
        self.assertEqual(t._sleep_temp, 20)
        self.assertTrue(t._support_flags & namespace["ClimateEntityFeature"].PRESET_MODE)
        t._async_control_heating.assert_awaited_once_with(calc_pid=True)

    async def test_disabling_last_preset_removes_preset_mode_feature(self):
        t = self.thermostat(mode="heat", active=True)
        t._support_flags |= namespace["ClimateEntityFeature"].PRESET_MODE
        t._sleep_temp = 20
        t._preset_modes_temp = {"sleep": None}
        await namespace["async_set_preset_temp"](t, sleep_temp_disable=True)
        self.assertIsNone(t._sleep_temp)
        self.assertFalse(t._support_flags & namespace["ClimateEntityFeature"].PRESET_MODE)


if __name__ == "__main__":
    unittest.main()
