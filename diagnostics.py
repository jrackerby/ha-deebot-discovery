"""What this integration knows, and how it came to know it.

The capability map is the ONLY record of what was measured on this robot, and
it lives nowhere but this integration's own store -- removing the config entry
removes it. So the dump is written to be readable on its own: every key
carries what it means, whether it was probed or inferred, and for an inferred
one, from what and why.

A dump that said `{"station_action": "supported"}` and nothing else would be
indistinguishable from the per-model table this integration exists to
replace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .discovery import BY_KEY, CONTROL_KEYS
from .seed import SEED_CLASS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import DeebotConfigEntry

#: The account and the robot's cloud id. `did` is redacted because it is the
#: address the cloud routes commands to, not because it is secret.
TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "did", "name", "nick", "resource"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DeebotConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    states = coordinator.states

    capabilities: dict[str, Any] = {}
    for key, capability in BY_KEY.items():
        state = states.get(key)
        row: dict[str, Any] = {
            "description": capability.description,
            "support": state.support.value if state else "unknown",
            "usable": key in coordinator.usable,
        }
        if capability.probed:
            row["evidence"] = "probed"
            row["consecutive_misses"] = state.misses if state else 0
            row["in_seed_hypothesis"] = capability.seeded
        else:
            row["evidence"] = "implied"
            row["implied_by"] = capability.implied_by
            row["reason"] = capability.reason
        capabilities[key] = row

    device = coordinator.device
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "robot": {
            "class": device.device_info["class"],
            # The two facts that make this robot the case this integration
            # was written for: its class, and the sibling class whose table
            # supplied the protocol client.
            "seed_class": SEED_CLASS,
            "seed_class_is_own_class": device.device_info["class"] == SEED_CLASS,
            "model": device.device_info.get("deviceName"),
            "firmware": device.fw_version,
            "available": coordinator.robot_available,
        },
        "probe": {
            "control_keys": list(CONTROL_KEYS),
            "life_span_components_reported": sorted(
                component.name.lower() for component in coordinator.life_span_components
            ),
        },
        "capabilities": capabilities,
    }
