"""Component setup for Kocom Wallpad."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP

from .const import DOMAIN, PLATFORMS
from .gateway import KocomGateway


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kocom Wallpad from a config entry."""
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]

    gateway = KocomGateway(hass, entry, host=host, port=port)
    try:
        await gateway.async_get_entity_registry()
        await gateway.async_start()
    except (OSError, TimeoutError) as exc:
        await gateway.async_stop()
        raise ConfigEntryNotReady(f"Cannot connect to Kocom gateway: {exc}") from exc
    except BaseException:
        await gateway.async_stop()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = gateway
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        try:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        finally:
            await gateway.async_stop()
            hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, gateway.async_stop)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        gateway: KocomGateway = hass.data[DOMAIN].pop(entry.entry_id)
        await gateway.async_stop()
    return unload_ok
