"""The smart_thermostat component."""

DOMAIN = "smart_thermostat"
PLATFORMS = ["climate"]


async def async_setup_entry(hass, entry):
    """Load the same thermostat through a UI-owned config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    """Let entity removal cancel its subscriptions and learning tasks."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
