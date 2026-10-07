"""The smart_thermostat component."""

from __future__ import annotations

import asyncio

DOMAIN = "smart_thermostat"
PLATFORMS = ["climate", "number"]
DATA_THERMOSTATS = "thermostats"
DATA_READY_EVENTS = "ready_events"


async def async_setup_entry(hass, entry):
    """Set up a thermostat and its preset controls as one config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault(DATA_THERMOSTATS, {})
    domain_data.setdefault(DATA_READY_EVENTS, {})[entry.entry_id] = asyncio.Event()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    """Unload the thermostat and its preset controls together."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.get(DATA_THERMOSTATS, {}).pop(entry.entry_id, None)
        domain_data.get(DATA_READY_EVENTS, {}).pop(entry.entry_id, None)
    return unloaded
