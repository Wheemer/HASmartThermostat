"""Exercise actual entity restore wiring with fake HA, without heat commands."""

import ast
import logging
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from test_ui_configuration_standalone import COMPONENT, ui
from test_lifecycle_standalone import lifecycle


class Base:
    async def async_added_to_hass(self):
        pass


class RuntimeStore:
    saved = None

    def __init__(self, *_args):
        pass

    async def async_load(self):
        return self.saved

    def async_delay_save(self, callback, _delay):
        self.saved = callback()


class StripImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None


def harness(methods=("async_added_to_hass", "_runtime_snapshot_is_valid", "_restore_state_is_valid", "_runtime_restore_state")):
    source = COMPONENT / "climate.py"
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmartThermostat")
    body = [StripImports().visit(n) for n in cls.body
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name in methods]
    new_class = ast.ClassDef(name="RestoringThermostat", bases=[ast.Name(id="Base", ctx=ast.Load())],
                             keywords=[], body=body, decorator_list=[], type_params=[])
    namespace = {
        "Base": Base, "callback": lambda f: f, "CoreState": SimpleNamespace(running="running"),
        "EVENT_HOMEASSISTANT_STOP": "homeassistant_stop",
        "HVACMode": SimpleNamespace(OFF="off"), "STATE_UNKNOWN": "unknown",
        "ATTR_TEMPERATURE": "temperature", "ATTR_PRESET_MODE": "preset_mode",
        "restore_attributes": ui.restore_attributes, "_LOGGER": logging.getLogger(__name__),
        "Store": RuntimeStore, "STATE_UNAVAILABLE": "unavailable",
        "State": lambda entity_id, state, attributes: SimpleNamespace(
            entity_id=entity_id, state=state, attributes=attributes),
        "async_track_state_change_event": Mock(return_value=Mock()),
        "_async_listen_once_until_remove": Mock(return_value=Mock()),
        "isfinite": __import__("math").isfinite,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[new_class], type_ignores=[])), str(source), "exec"), namespace)
    obj = namespace["RestoringThermostat"]()
    obj._operations = lifecycle.EntityOperations()
    obj.hass = SimpleNamespace(state="running", states=SimpleNamespace(get=lambda key: None))
    obj._observer = obj._pid_adaptation = None
    obj._sensor_entity_id = "sensor.average_temperature"
    obj._ext_sensor_entity_id = obj._heater_entity_id = obj._cooler_entity_id = None
    obj._keep_alive = obj._sampling_period = None
    obj._hvac_mode = obj._target_temp = None
    obj._pid_controller = SimpleNamespace(set_pid_param=Mock(), integral=0, mode="auto")
    obj._ac_mode = False
    obj.min_temp, obj.max_temp = 7, 35
    obj.entity_id = "climate.thermostat"
    obj.unique_id = "house_thermostat"
    obj._attr_hvac_modes = ["heat", "off"]
    obj._runtime_store = obj._runtime_saved = None
    obj.async_on_remove = Mock()
    obj._async_sensor_changed = AsyncMock()
    obj._async_control_heating = AsyncMock()
    obj._kp, obj._ki, obj._kd, obj._ke = 100, 0, 0, 0
    obj._sleep_temp = 19.5
    obj._attr_preset_mode = "none"

    async def set_mode(mode):
        obj._hvac_mode = mode

    obj.async_set_hvac_mode = AsyncMock(side_effect=set_mode)
    return obj


class RestoreTests(IsolatedAsyncioTestCase):
    async def test_first_import_retains_off_runtime_sleep_and_setpoint(self):
        obj = harness()
        obj._configured_settings = {"sleep_temp": 19.5, "kp": 100}
        obj.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            entity_id=obj.entity_id, state="off",
            attributes={"temperature": 20.2, "sleep_temp": 20.2, "preset_mode": "sleep", "kp": 120}))
        await obj.async_added_to_hass()
        obj.async_set_hvac_mode.assert_awaited_once_with("off")
        self.assertEqual(obj._hvac_mode, "off")
        self.assertEqual(obj._sleep_temp, 20.2)
        self.assertEqual(obj._target_temp, 20.2)
        self.assertEqual(obj._kp, 100)

    async def test_ui_edits_survive_actual_restore_path(self):
        obj = harness()
        obj._configured_settings = {"sleep_temp": 20.4, "kp": 90}
        obj._kp = 90
        obj.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            entity_id=obj.entity_id, state="heat", attributes={"temperature": 21,
                "sleep_temp": 20.2, "kp": 120, "pid_i": 50,
                "configured_settings": {"sleep_temp": 19.5, "kp": 100}}))
        await obj.async_added_to_hass()
        self.assertEqual(obj._sleep_temp, 20.4)
        self.assertEqual(obj._kp, 90)
        self.assertEqual(obj._pid_controller.integral, 50)
        self.assertEqual(obj._target_temp, 21)

    async def test_learning_store_can_restore_runtime_gain_without_state_attribute_restore(self):
        obj = harness(methods=("_validated_stored_gains", "_adaptive_gains", "_learning_snapshot"))
        obj._configured_settings = {"kp": 100}
        obj._pid_adaptation = SimpleNamespace(limits={"kp": (10, 500), "ki": (0, 1), "kd": (0, 12000)})
        self.assertEqual(obj._validated_stored_gains({"kp": 95, "ki": 0, "kd": 0}),
                         {"kp": 95.0, "ki": 0.0, "kd": 0.0})
        self.assertIsNone(obj._validated_stored_gains({"kp": 5, "ki": 0, "kd": 0}))

    async def test_first_ui_install_defaults_off_without_stored_state(self):
        obj = harness()
        obj._configured_settings = {"kp": 100}
        obj.async_get_last_state = AsyncMock(return_value=None)
        await obj.async_added_to_hass()
        self.assertEqual(obj._hvac_mode, "off")
        obj.async_set_hvac_mode.assert_not_awaited()

    async def test_unavailable_restore_uses_last_valid_runtime_snapshot(self):
        obj = harness()
        obj._configured_settings = {"activity_temp": 22.5}
        RuntimeStore.saved = {"hvac_mode": "heat", "temperature": 22.5, "preset_mode": "activity"}
        obj.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            entity_id=obj.entity_id, state="unavailable", attributes={}))
        await obj.async_added_to_hass()
        obj.async_set_hvac_mode.assert_awaited_once_with("heat")
        self.assertEqual(obj._target_temp, 22.5)
        self.assertEqual(obj._attr_preset_mode, "activity")

    async def test_invalid_restore_without_snapshot_does_not_apply_unavailable_mode(self):
        obj = harness()
        obj._configured_settings = {"kp": 100}
        RuntimeStore.saved = None
        obj.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            entity_id=obj.entity_id, state="unavailable", attributes={}))
        await obj.async_added_to_hass()
        obj.async_set_hvac_mode.assert_not_awaited()
        self.assertEqual(obj._hvac_mode, "off")
