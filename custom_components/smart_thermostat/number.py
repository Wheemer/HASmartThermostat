"""Editable preset temperatures for Smart Thermostat."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from . import DOMAIN

_PRESETS = ("away", "eco", "boost", "comfort", "home", "sleep", "activity")


async def async_setup_entry(hass, entry, async_add_entities):
    """Expose the established helper-compatible preset number entities."""
    thermostat = hass.data[DOMAIN]["thermostats"].get(entry.entry_id)
    if thermostat is None:
        return
    async_add_entities([PresetTemperatureNumber(thermostat, preset) for preset in _PRESETS])


class PresetTemperatureNumber(NumberEntity, RestoreEntity):
    """One editable setpoint for a Smart Thermostat preset."""

    _attr_has_entity_name = False
    _attr_mode = NumberMode.BOX

    def __init__(self, thermostat, preset):
        self._thermostat = thermostat
        self._preset = preset
        self._attr_name = f"{thermostat.name} Preset {preset.title()} Temp"
        self.entity_id = f"number.{slugify(thermostat.name)}_preset_{preset}_temp"
        self._attr_unique_id = f"{thermostat.unique_id}_preset_{preset}_temp"
        self._attr_native_min_value = thermostat.min_temp
        self._attr_native_max_value = thermostat.max_temp
        self._attr_native_step = thermostat.target_temperature_step
        self._attr_native_unit_of_measurement = thermostat.temperature_unit
        self._remove_listener = None

    @property
    def native_value(self):
        return getattr(self._thermostat, f"_{self._preset}_temp", None)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state is not None:
            try:
                value = float(old_state.state)
            except (TypeError, ValueError):
                value = None
            if value is not None and self._attr_native_min_value <= value <= self._attr_native_max_value:
                setattr(self._thermostat, f"_{self._preset}_temp", value)
                if self._thermostat.preset_mode == self._preset:
                    self._thermostat._target_temp = value
                self._thermostat.async_write_ha_state()

        @callback
        def _thermostat_updated(event):
            self.async_write_ha_state()

        self._remove_listener = async_track_state_change_event(
            self.hass, [self._thermostat.entity_id], _thermostat_updated
        )

    async def async_will_remove_from_hass(self):
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    async def async_set_native_value(self, value):
        await self._thermostat.async_set_preset_temp(**{f"{self._preset}_temp": value})
        self.async_write_ha_state()
