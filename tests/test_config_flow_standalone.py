"""Execute the flow methods with fake HA flow/registry APIs (not an HA boot)."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from uuid import uuid4

import voluptuous as vol

from test_ui_configuration_standalone import COMPONENT, ui


class AlreadyConfigured(Exception):
    pass


class Flow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if self.unique_id in getattr(self, "existing", set()):
            raise AlreadyConfigured

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}


def load_flow():
    tree = ast.parse((COMPONENT / "config_flow.py").read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = dict(vars(ui))
    namespace.update(config_entries=SimpleNamespace(ConfigFlow=Flow, OptionsFlow=Flow),
                     DOMAIN="smart_thermostat", callback=lambda f: f, uuid4=uuid4, vol=vol,
                     slugify=lambda s: "legacy_generated_id", er=SimpleNamespace(async_get=lambda hass: hass.registry))
    exec(compile(tree, str(COMPONENT / "config_flow.py"), "exec"), namespace)

    def validate(data):
        if not data.get("target_sensor"):
            raise vol.Invalid("Missing sensor")
        return ui.serialize_configuration(data)

    namespace["validate_configuration"] = validate
    namespace["form_schema"] = lambda fields, values: {"fields": fields, "values": values}
    return namespace


def hass_with_state(attrs=None):
    return SimpleNamespace(
        registry=SimpleNamespace(async_get_entity_id=lambda *args: "climate.thermostat"),
        states=SimpleNamespace(get=lambda entity_id: SimpleNamespace(attributes=attrs) if attrs is not None else None))


class ConfigFlowTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = load_flow()
        self.config = {"name": "Thermostat", "unique_id": "house_thermostat",
                       "target_sensor": "sensor.average_temperature", "sleep_temp": 19.5,
                       "heater": ["input_boolean.furnace_boolean"],
                       "min_cycle_duration": {"seconds": 150}}

    async def test_import_keeps_identity_and_original_snapshot(self):
        flow = self.ns["SmartThermostatConfigFlow"]()
        flow.hass = hass_with_state({"sleep_temp": 20.2})
        result = await flow.async_step_import(self.config)
        self.assertEqual(flow.unique_id, "house_thermostat")
        self.assertEqual(result["data"]["configuration"]["sleep_temp"], 20.2)
        self.assertEqual(result["data"]["imported_yaml"]["sleep_temp"], 19.5)
        self.assertEqual(self.config["sleep_temp"], 19.5)

    async def test_import_respects_runtime_disabled_preset(self):
        flow = self.ns["SmartThermostatConfigFlow"]()
        flow.hass = hass_with_state({"sleep_temp": None})
        result = await flow.async_step_import(self.config)
        self.assertNotIn("sleep_temp", result["data"]["configuration"])

    async def test_leftover_yaml_does_not_overwrite_entry(self):
        flow = self.ns["SmartThermostatConfigFlow"]()
        flow.hass = hass_with_state()
        flow.existing = {"house_thermostat"}
        with self.assertRaises(AlreadyConfigured):
            await flow.async_step_import(self.config)

    async def test_import_failure_does_not_create_entry(self):
        flow = self.ns["SmartThermostatConfigFlow"]()
        result = await flow.async_step_import({"name": "Bad"})
        self.assertEqual(result, {"type": "abort", "reason": "invalid_configuration"})

    async def test_legacy_missing_identity_uses_original_generation_path(self):
        flow = self.ns["SmartThermostatConfigFlow"]()
        flow.hass = hass_with_state()
        result = await flow.async_step_import({**self.config, "unique_id": "none"})
        self.assertEqual(result["data"]["configuration"]["unique_id"], "legacy_generated_id")

    def options(self, attrs=None):
        flow = self.ns["SmartThermostatOptionsFlow"]()
        flow.hass = hass_with_state(attrs)
        flow.config_entry = SimpleNamespace(data={"configuration": self.config}, options={})
        return flow

    async def test_options_show_effective_sleep(self):
        result = await self.options({"sleep_temp": 20.2}).async_step_presets()
        self.assertEqual(result["data_schema"]["values"]["sleep_temp"], 20.2)

    async def test_options_preserve_identity_and_unrelated_fields(self):
        result = await self.options().async_step_presets({"sleep_temp": 20.4})
        config = result["data"]["configuration"]
        self.assertEqual(config["unique_id"], "house_thermostat")
        self.assertEqual(config["heater"], self.config["heater"])
        self.assertEqual(config["min_cycle_duration"], {"seconds": 150})

    async def test_options_remove_preset_instead_of_falling_back_to_entry_data(self):
        result = await self.options().async_step_presets({})
        self.assertNotIn("sleep_temp", result["data"]["configuration"])

    async def test_invalid_options_remain_on_form(self):
        result = await self.options().async_step_controller({"name": "Missing sensor"})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "invalid_configuration")

    async def test_config_entry_lifecycle_delegates_platform_cleanup(self):
        namespace = {}
        exec((COMPONENT / "__init__.py").read_text(), namespace)
        registered = []
        entry = SimpleNamespace(entry_id="test", async_on_unload=registered.append,
                                add_update_listener=lambda callback: callback)
        manager = SimpleNamespace(async_forward_entry_setups=AsyncMock(),
                                  async_unload_platforms=AsyncMock(return_value=True),
                                  async_reload=AsyncMock())
        hass = SimpleNamespace(config_entries=manager)
        self.assertTrue(await namespace["async_setup_entry"](hass, entry))
        manager.async_forward_entry_setups.assert_awaited_once_with(entry, ["climate"])
        self.assertEqual(len(registered), 1)
        await registered[0](hass, entry)
        manager.async_reload.assert_awaited_once_with("test")
        self.assertTrue(await namespace["async_unload_entry"](hass, entry))
        manager.async_unload_platforms.assert_awaited_once_with(entry, ["climate"])
