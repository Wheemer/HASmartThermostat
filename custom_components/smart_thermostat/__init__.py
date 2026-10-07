"""The smart_thermostat component."""

DOMAIN = "smart_thermostat"
PLATFORMS = ["climate", "number"]


async def async_setup_entry(hass, entry):
    """Load the thermostat before its preset controls."""
    await hass.config_entries.async_forward_entry_setups(entry, ["climate"])
    await hass.config_entries.async_forward_entry_setups(entry, ["number"])
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    """Unload the thermostat and its preset controls together."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).get("thermostats", {}).pop(entry.entry_id, None)
    return unloaded
