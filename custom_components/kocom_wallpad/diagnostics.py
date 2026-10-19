"""Connection diagnostics without host addresses or raw home-device packets."""
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    return hass.data[DOMAIN][entry.entry_id].diagnostics()
