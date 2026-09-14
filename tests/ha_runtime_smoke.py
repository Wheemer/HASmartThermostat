"""Run in an offline HA container against simulated entities, never live HA."""

import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.const import __version__
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er
from homeassistant import loader, bootstrap


class ThermostatLogChecks(logging.Handler):
    def __init__(self):
        super().__init__()
        self.failures = []

    def emit(self, record):
        message = record.getMessage()
        if ("does not support entities input_boolean.test_heater" in message
                or (record.levelno >= logging.ERROR and
                    (record.name.startswith("custom_components.smart_thermostat")
                     or "smart_thermostat" in message or "entry Thermostat" in message))):
            self.failures.append((record.name, message))


async def main():
    logging.basicConfig(level=logging.WARNING)
    log_checks = ThermostatLogChecks()
    logging.getLogger().addHandler(log_checks)
    with tempfile.TemporaryDirectory(prefix="thermostat-test-") as directory:
        config = Path(directory)
        (config / "custom_components").mkdir()
        (config / "custom_components" / "smart_thermostat").symlink_to(
            Path("/work/custom_components/smart_thermostat"), target_is_directory=True)
        sys.path.insert(0, directory)
        hass = HomeAssistant(directory)
        hass.config.skip_pip = True
        loader.async_setup(hass)
        try:
            assert await bootstrap.async_from_config_dict({"homeassistant": {},
                "input_boolean": {"test_heater": {"name": "Simulated heater"}}}, hass)
            registry = er.async_get(hass)
            old = registry.async_get_or_create("climate", "smart_thermostat", "house_thermostat",
                                               suggested_object_id="thermostat")
            assert old.entity_id == "climate.thermostat"
            hass.states.async_set("sensor.test_temperature", "21.0", {"unit_of_measurement": "°C"})
            yaml = {
                "platform": "smart_thermostat", "name": "Thermostat", "unique_id": "house_thermostat",
                "heater": "input_boolean.test_heater", "target_sensor": "sensor.test_temperature",
                "keep_alive": {"seconds": 60}, "min_cycle_duration": {"minutes": 2.5},
                "sleep_temp": 19.5, "preset_sync_mode": "sync", "precision": 0.1,
            }
            assert await async_setup_component(hass, "climate", {"climate": [yaml]})
            from custom_components.smart_thermostat import climate as thermostat_platform
            await thermostat_platform.async_setup_platform(
                hass, thermostat_platform.PLATFORM_SCHEMA(yaml), lambda entities: None)
            await hass.async_start()
            await hass.async_block_till_done()
            entries = hass.config_entries.async_entries("smart_thermostat")
            assert len(entries) == 1, entries
            entry = entries[0]
            print("ENTRY", entry.state, flush=True)
            state = hass.states.get("climate.thermostat")
            assert state is not None and state.state == "off", state
            assert state.attributes["sleep_temp"] == 19.5, state.attributes
            assert registry.async_get("climate.thermostat").config_entry_id == entry.entry_id
            assert entry.data["configuration"]["min_cycle_duration"] == {"seconds": 150.0}
            for service in ("set_pid_gain", "set_pid_mode", "set_preset_temp", "clear_integral"):
                assert hass.services.has_service("smart_thermostat", service), service

            # A late-loading/reconnecting output must reconcile against OFF,
            # without waiting for the 60-second keep-alive interval.
            hass.states.async_set("input_boolean.test_heater", "unavailable")
            await hass.async_block_till_done()
            await hass.services.async_call("input_boolean", "turn_on",
                                           {"entity_id": "input_boolean.test_heater"}, blocking=True)
            await asyncio.wait_for(hass.async_block_till_done(), 5)
            assert hass.states.get("input_boolean.test_heater").state == "off"
            assert hass.states.get("climate.thermostat").state == "off"

            # Read the real options flow, including HA selector serialization.
            menu = await hass.config_entries.options.async_init(entry.entry_id)
            assert menu["type"] == "menu", menu
            form = await hass.config_entries.options.async_configure(menu["flow_id"], {"next_step_id": "presets"})
            assert form["type"] == "form", form
            from homeassistant.helpers import config_validation as cv
            import voluptuous_serialize
            fields = voluptuous_serialize.convert(form["data_schema"], custom_serializer=cv.custom_serializer)
            assert any(field["name"] == "sleep_temp" for field in fields), fields
            updated = await hass.config_entries.options.async_configure(form["flow_id"], {"sleep_temp": 20.4})
            assert updated["type"] == "create_entry", updated
            await hass.async_block_till_done()
            state = hass.states.get("climate.thermostat")
            assert state.state == "off", state
            assert state.attributes["sleep_temp"] == 20.4, state.attributes
            from custom_components.smart_thermostat.ui_configuration import SECTIONS
            for section, expected in SECTIONS.items():
                options_menu = await hass.config_entries.options.async_init(entry.entry_id)
                options_form = await hass.config_entries.options.async_configure(
                    options_menu["flow_id"], {"next_step_id": section})
                fields = voluptuous_serialize.convert(options_form["data_schema"], custom_serializer=cv.custom_serializer)
                assert {field["name"] for field in fields} == set(expected), (section, fields)
                hass.config_entries.options.async_abort(options_form["flow_id"])

            duplicate = await hass.config_entries.flow.async_init("smart_thermostat", context={"source": "import"}, data=yaml)
            assert duplicate["type"] == "abort" and duplicate["reason"] == "already_configured", duplicate
            assert len(hass.config_entries.async_entries("smart_thermostat")) == 1

            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
            assert hass.states.get("input_boolean.test_heater").state == "off"
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            state = hass.states.get("climate.thermostat")
            assert state.state == "off", state
            assert state.attributes["sleep_temp"] == 20.4, state.attributes
            # Hold a real service call open while unloading the actual entity.
            from homeassistant.helpers import entity_platform
            platforms = entity_platform.async_get_platforms(hass, "smart_thermostat")
            entity = next(platform.entities["climate.thermostat"] for platform in platforms
                          if "climate.thermostat" in platform.entities)
            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def waiting_gain_update(**kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            entity._async_control_heating = waiting_gain_update
            service_task = asyncio.create_task(hass.services.async_call(
                "smart_thermostat", "set_preset_temp",
                {"entity_id": "climate.thermostat", "sleep_temp": 20.4}, blocking=True))
            await asyncio.wait_for(started.wait(), 5)
            assert await hass.config_entries.async_unload(entry.entry_id)
            await asyncio.gather(service_task, return_exceptions=True)
            assert cancelled.is_set()
            assert entity._operations.closed and not entity._operations.tasks
            await entity.async_set_hvac_mode("heat")
            assert hass.states.get("input_boolean.test_heater").state == "off"
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            # Exercise HA-owned background import cancellation, not Recorder calibration.
            import_started, import_cancelled = asyncio.Event(), asyncio.Event()

            async def pending_import(*args, **kwargs):
                import_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    import_cancelled.set()

            with patch.object(thermostat_platform, "import_recent_history", pending_import):
                base_options = dict(entry.options["configuration"])
                hass.config_entries.async_update_entry(entry, options={"configuration": {
                    **base_options, "adaptive_observe": True}})
                await asyncio.wait_for(import_started.wait(), 10)
                await asyncio.wait_for(hass.async_block_till_done(), 5)
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert import_cancelled.is_set()
                hass.config_entries.async_update_entry(entry, options={"configuration": base_options})
                await hass.async_block_till_done()
                if entry.state.value != "loaded":
                    assert await hass.config_entries.async_setup(entry.entry_id)
                    await hass.async_block_till_done()
            print(json.dumps({"ha_version": __version__, "passed": [
                "YAML import", "registry identity adoption", "fractional timing", "entity services",
                "all six options schemas", "UI preset update and reload", "duplicate import",
                "unload/setup", "off state preserved", "in-flight service cancellation",
                "old instance blocked after reload", "background import cancelled on unload",
                "returning output reconciled immediately against OFF"]}), flush=True)
        finally:
            await hass.async_stop()

        restarted = HomeAssistant(directory)
        restarted.config.skip_pip = True
        loader.async_setup(restarted)
        try:
            assert await bootstrap.async_from_config_dict({"homeassistant": {},
                "input_boolean": {"test_heater": {"name": "Simulated heater"}}}, restarted)
            restarted.states.async_set("sensor.test_temperature", "21.0", {"unit_of_measurement": "°C"})
            await restarted.async_start()
            await restarted.async_block_till_done()
            entries = restarted.config_entries.async_entries("smart_thermostat")
            assert len(entries) == 1
            state = restarted.states.get("climate.thermostat")
            assert state is not None and state.state == "off", state
            assert state.attributes["sleep_temp"] == 20.4, state.attributes
            assert restarted.states.get("input_boolean.test_heater").state == "off"
            print("PASS: full HA stop/start, UI-only entry persistence, same entity ID and off state", flush=True)
        finally:
            await restarted.async_stop()

        cold_directory = config / "first-yaml-boot"
        (cold_directory / "custom_components").mkdir(parents=True)
        (cold_directory / "custom_components" / "smart_thermostat").symlink_to(
            Path("/work/custom_components/smart_thermostat"), target_is_directory=True)
        cold = HomeAssistant(str(cold_directory))
        cold.config.skip_pip = True
        loader.async_setup(cold)
        try:
            cold.states.async_set("sensor.test_temperature", "21.0", {"unit_of_measurement": "°C"})
            assert await bootstrap.async_from_config_dict({"homeassistant": {}, "climate": [yaml],
                "input_boolean": {"test_heater": {"name": "Simulated heater"}}}, cold)
            await cold.async_start()
            await cold.async_block_till_done()
            entries = cold.config_entries.async_entries("smart_thermostat")
            assert len(entries) == 1, entries
            assert cold.states.get("climate.thermostat").state == "off"
            assert entries[0].data["configuration"]["sleep_temp"] == 19.5
            print("PASS: first YAML import through full HA bootstrap", flush=True)
        finally:
            await cold.async_stop()
        assert not log_checks.failures, log_checks.failures
        print("PASS: no thermostat errors or commands to unloaded test-heater entities", flush=True)


asyncio.run(main())
