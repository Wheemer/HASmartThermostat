"""Exercise actual command boundaries and availability-triggered reconciliation."""

import ast
import asyncio
from datetime import timedelta
import importlib.util
import logging
import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from test_lifecycle_standalone import COMPONENT, lifecycle

spec = importlib.util.spec_from_file_location("output_readiness", COMPONENT / "output_readiness.py")
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)


def state(value, restored=False):
    return SimpleNamespace(state=value, attributes={"restored": restored})


def harness(states):
    source = COMPONENT / "climate.py"
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmartThermostat")
    names = {
        "_is_toggle_entity_domain", "_async_heater_turn_on", "_async_heater_turn_off",
        "_async_set_entity_value", "_async_set_valve_value", "_async_switch_changed",
    }
    methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    new_cls = ast.ClassDef(name="Harness", bases=[], keywords=[], body=methods, decorator_list=[], type_params=[])
    namespace = dict(entity_operation=lifecycle.entity_operation, callback=lambda f: f,
                     output_available=readiness.output_available, time=time,
                     _LOGGER=logging.getLogger(__name__), Event=dict, EventStateChangedData=dict,
                     ATTR_ENTITY_ID="entity_id", SERVICE_TURN_ON="turn_on", SERVICE_TURN_OFF="turn_off",
                     HA_DOMAIN="homeassistant", ATTR_VALUE="value", SERVICE_SET_VALUE="set_value",
                     VALVE_DOMAIN="valve", LIGHT_DOMAIN="light", SERVICE_TURN_LIGHT_ON="turn_on",
                     SERVICE_SET_VALVE_POSITION="set_valve_position", ATTR_POSITION="position",
                     ATTR_BRIGHTNESS_PCT="brightness_pct")
    exec(compile(ast.fix_missing_locations(ast.Module(body=[new_cls], type_ignores=[])), str(source), "exec"), namespace)
    obj = namespace["Harness"]()
    obj._operations = lifecycle.EntityOperations()
    obj.hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get, is_state=lambda entity, expected: states.get(entity).state == expected),
        services=SimpleNamespace(async_call=AsyncMock()), async_create_task=asyncio.create_task)
    obj._heater_entity_id = list(states)
    obj._cooler_entity_id = None
    obj.heater_or_cooler_entity = list(states)
    obj._is_device_active = False
    obj._last_heat_cycle_time = time.time() - 100
    obj._min_off_cycle_duration = obj._min_on_cycle_duration = timedelta(seconds=60)
    obj._heater_polarity_invert = False
    obj._output_min = 0
    obj._output_max = 100
    obj.entity_id = "climate.thermostat"
    obj._observer = None
    obj._restoration_complete = True
    obj._output_reconcile_task = None
    obj._async_control_heating = AsyncMock()
    obj.async_write_ha_state = Mock()
    obj._get_number_entity_domain = lambda entity: "number"
    return obj


class ReadinessTests(IsolatedAsyncioTestCase):
    async def test_missing_unknown_unavailable_and_restored_outputs_do_not_receive_commands(self):
        for value in (None, state("unknown"), state("unavailable"), state("on", restored=True)):
            with self.subTest(state=value):
                obj = harness({"switch.heat": value})
                before = obj._last_heat_cycle_time
                await obj._async_heater_turn_on()
                await obj._async_heater_turn_off(force=True)
                await obj._async_set_valve_value(25)
                obj.hass.services.async_call.assert_not_awaited()
                self.assertEqual(obj._last_heat_cycle_time, before)

    async def test_ready_output_uses_existing_command_and_polarity(self):
        obj = harness({"switch.heat": state("off")})
        await obj._async_heater_turn_on()
        obj.hass.services.async_call.assert_awaited_once_with("homeassistant", "turn_on", {"entity_id": "switch.heat"})
        obj.hass.services.async_call.reset_mock()
        obj._heater_polarity_invert = True
        obj._is_device_active = True
        await obj._async_heater_turn_off(force=True)
        obj.hass.services.async_call.assert_awaited_once_with("homeassistant", "turn_on", {"entity_id": "switch.heat"})

    async def test_duplicate_on_and_off_commands_are_not_replayed(self):
        obj = harness({"switch.heat": state("on")})
        obj._is_device_active = True
        await obj._async_heater_turn_on()
        obj.hass.services.async_call.assert_not_awaited()

        obj = harness({"switch.heat": state("off")})
        obj._is_device_active = False
        await obj._async_heater_turn_off()
        obj.hass.services.async_call.assert_not_awaited()

    async def test_pwm_valve_output_uses_position_not_turn_services(self):
        obj = harness({"valve.heat": state("0")})
        obj._heater_entity_id = ["valve.heat"]
        obj.heater_or_cooler_entity = ["valve.heat"]
        await obj._async_heater_turn_on()
        obj.hass.services.async_call.assert_awaited_once_with(
            "valve", "set_valve_position", {"entity_id": "valve.heat", "position": 100})

        obj.hass.services.async_call.reset_mock()
        obj._is_device_active = True
        await obj._async_heater_turn_off(force=True)
        obj.hass.services.async_call.assert_awaited_once_with(
            "valve", "set_valve_position", {"entity_id": "valve.heat", "position": 0})

    async def test_on_waits_for_all_outputs_but_off_can_stop_available_one(self):
        obj = harness({"switch.one": state("on"), "switch.two": state("unavailable")})
        await obj._async_heater_turn_on()
        obj.hass.services.async_call.assert_not_awaited()
        obj._is_device_active = True
        await obj._async_heater_turn_off(force=True)
        obj.hass.services.async_call.assert_awaited_once_with("homeassistant", "turn_off", {"entity_id": "switch.one"})

    async def test_readiness_does_not_bypass_minimum_off_time(self):
        obj = harness({"switch.heat": state("off")})
        obj._last_heat_cycle_time = time.time()
        await obj._async_heater_turn_on()
        obj.hass.services.async_call.assert_not_awaited()

    async def test_available_transition_coalesces_one_reconciliation(self):
        obj = harness({"switch.heat": state("on")})
        event = SimpleNamespace(event_type="state_changed", data={"old_state": state("unavailable"), "new_state": state("on")})
        obj._async_switch_changed(event)
        first = obj._output_reconcile_task
        obj._async_switch_changed(event)
        self.assertIs(obj._output_reconcile_task, first)
        await first
        obj._async_control_heating.assert_awaited_once_with(calc_pid=True)

    async def test_normal_change_is_not_a_new_recovery_path(self):
        obj = harness({"switch.heat": state("on")})
        obj._async_switch_changed(SimpleNamespace(event_type="state_changed", data={
            "old_state": state("off"), "new_state": state("on")}))
        self.assertIsNone(obj._output_reconcile_task)

    async def test_unloading_or_restoring_does_not_start_reconciliation(self):
        for closed in (False, True):
            obj = harness({"switch.heat": state("on")})
            obj._operations.closed = closed
            obj._restoration_complete = closed
            obj._async_switch_changed(SimpleNamespace(event_type="state_changed", data={
                "old_state": None, "new_state": state("on")}))
            self.assertIsNone(obj._output_reconcile_task)
