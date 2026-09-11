"""Runtime capability discovery for Ecovacs Deebot robots.

`deebot-client` resolves a robot's capabilities from a per-model lookup table
keyed on an opaque class string. The robot this was written for -- a DEEBOT
T90 PRO OMNI Care, class `nv8fz5` -- is in none of that library's hardware
modules even though four sibling T90 PRO OMNI classes are, and since v14
removed the fallback an unlisted class gets ZERO entities. This integration
builds the entity set from what the robot ANSWERS instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.const import Platform

from .coordinator import DeebotConfigEntry, DeebotCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

#: Every platform is forwarded unconditionally and each decides for itself
#: whether it has anything to build. The alternative -- forwarding only the
#: platforms the current capability map needs -- would mean a capability
#: promoted later could never build its entity without a reload, which is
#: precisely the "a firmware update adds a function and nobody sees it" case
#: the probe passes exist to catch.
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]


async def async_setup_entry(hass: HomeAssistant, entry: DeebotConfigEntry) -> bool:
    """Set up the robot from a config entry."""
    coordinator = DeebotCoordinator(hass, entry)
    await coordinator.async_connect()
    try:
        # The first probe pass IS the setup check: it exercises the same
        # cloud channel every later reading uses, against the robot the entry
        # names, and a pass that establishes nothing fails setup rather than
        # loading an entry with no entities.
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeebotConfigEntry) -> bool:
    """Unload the entry, closing the cloud session behind it."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok
