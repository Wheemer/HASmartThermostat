"""UI setup, lossless YAML import, and section-based options."""

from uuid import uuid4

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, entity_registry as er, selector
from homeassistant.util import slugify

from . import DOMAIN
from .ui_configuration import (
    BOOLEAN_FIELDS, DURATIONS, PRESETS, SECTIONS,
    replace_section, serialize_configuration,
)


def validate_configuration(data):
    """Use the same schema and defaults as the installed YAML platform."""
    from .climate import PLATFORM_SCHEMA

    validated = PLATFORM_SCHEMA({"platform": DOMAIN, **data})
    if not validated.get("heater") and not validated.get("cooler"):
        raise vol.Invalid("A heater or cooler is required")
    if validated.get("min_temp", 7) >= validated.get("max_temp", 35):
        raise vol.Invalid("Minimum temperature must be below maximum")
    if validated["output_min"] >= validated["output_max"]:
        raise vol.Invalid("Output minimum must be below maximum")
    if validated["out_clamp_low"] > validated["out_clamp_high"]:
        raise vol.Invalid("Output clamps are reversed")
    return serialize_configuration(validated)


def form_schema(fields, values):
    """Native selectors; optional fields use suggested values so they can clear."""
    schema = {}
    choices = {"initial_hvac_mode": ["off", "heat", "cool"],
               "preset_sync_mode": ["sync", "none"]}
    for key in fields:
        marker_type = vol.Required if key in ("name", "target_sensor", "keep_alive") else vol.Optional
        marker = marker_type(key, description={"suggested_value": values[key]}) if key in values else marker_type(key)
        if key in ("heater", "cooler", "target_sensor", "outdoor_sensor"):
            field = selector.EntitySelector(selector.EntitySelectorConfig(
                multiple=key in ("heater", "cooler")))
        elif key in BOOLEAN_FIELDS:
            field = selector.BooleanSelector()
        elif key in choices:
            field = selector.SelectSelector(selector.SelectSelectorConfig(options=choices[key]))
        elif key in ("name", "autotune"):
            field = selector.TextSelector()
        else:
            number_options = {"mode": "box", "step": "any"}
            if key in DURATIONS:
                number_options.update(min=0, unit_of_measurement="s")
                if key in values:
                    marker = marker_type(key, description={
                        "suggested_value": cv.time_period(values[key]).total_seconds()})
            field = selector.NumberSelector(selector.NumberSelectorConfig(**number_options))
        schema[marker] = field
    return vol.Schema(schema)


def normalize_form(data):
    return {key: {"seconds": value} if key in DURATIONS else value
            for key, value in data.items()}


class SmartThermostatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One config entry for each existing thermostat identity."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmartThermostatOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = validate_configuration(normalize_form(user_input))
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_configuration"
            else:
                data["unique_id"] = uuid4().hex
                await self.async_set_unique_id(data["unique_id"])
                return self.async_create_entry(title=data["name"], data={"configuration": data})
        fields = (*SECTIONS["controller"], "keep_alive")
        return self.async_show_form(step_id="user", data_schema=form_schema(
            fields, normalize_form(user_input) if user_input is not None else {
                "name": "Smart Thermostat", "keep_alive": {"seconds": 60},
                "invert_heater": False, "force_off_state": True, "ac_mode": False}),
            errors=errors)

    async def async_step_import(self, import_data):
        try:
            original = validate_configuration(import_data)
        except (vol.Invalid, ValueError, TypeError):
            return self.async_abort(reason="invalid_configuration")
        data = dict(original)
        if data.get("unique_id", "none") == "none":
            data["unique_id"] = slugify(
                f"{DOMAIN}_{data['name']}_{data.get('heater')}")
        await self.async_set_unique_id(data["unique_id"])
        # A leftover YAML entry must never overwrite later UI edits.
        self._abort_if_unique_id_configured()
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id("climate", DOMAIN, data["unique_id"])
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is not None:
            for key in PRESETS:
                if key in state.attributes:
                    value = state.attributes[key]
                    if value is None:
                        data.pop(key, None)
                    else:
                        data[key] = value
        return self.async_create_entry(title=data["name"], data={
            "configuration": data, "imported_yaml": original})


class SmartThermostatOptionsFlow(config_entries.OptionsFlow):
    """Edit one section without resetting unrelated or runtime-learned values."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=list(SECTIONS))

    async def _section(self, section, user_input):
        entry = self.config_entry
        config = dict(entry.options.get("configuration", entry.data["configuration"]))
        errors = {}
        if user_input is not None:
            try:
                candidate = replace_section(config, section, normalize_form(user_input))
                validated = validate_configuration(candidate)
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_configuration"
            else:
                return self.async_create_entry(title="", data={"configuration": validated})
        values = {key: config[key] for key in SECTIONS[section] if key in config}
        if section == "presets":
            registry = er.async_get(self.hass)
            entity_id = registry.async_get_entity_id("climate", DOMAIN, config["unique_id"])
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is not None:
                for key in PRESETS:
                    if key in state.attributes:
                        if state.attributes[key] is None:
                            values.pop(key, None)
                        else:
                            values[key] = state.attributes[key]
        if user_input is not None:
            values = normalize_form(user_input)
        return self.async_show_form(step_id=section,
                                    data_schema=form_schema(SECTIONS[section], values), errors=errors)

    async def async_step_controller(self, user_input=None):
        return await self._section("controller", user_input)

    async def async_step_temperatures(self, user_input=None):
        return await self._section("temperatures", user_input)

    async def async_step_presets(self, user_input=None):
        return await self._section("presets", user_input)

    async def async_step_timing(self, user_input=None):
        return await self._section("timing", user_input)

    async def async_step_pid(self, user_input=None):
        return await self._section("pid", user_input)

    async def async_step_output(self, user_input=None):
        return await self._section("output", user_input)
