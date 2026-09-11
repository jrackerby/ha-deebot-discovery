"""One-shot actions: the station's, and resetting a consumable's counter.

Both sets are built from something MEASURED rather than from the borrowed
table alone:

  * The station buttons exist only when `auto_empty` -- a station-only read --
    answered on this robot. `discovery.py` carries that implication and its
    reason. Which actions they are still comes from the seed, because the
    protocol has no way to enumerate them.
  * A reset button exists for each consumable the robot has actually REPORTED
    in a life-span event, not for each one the seeded query asked about. The
    query deliberately asks about more components than any one robot has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deebot_client.commands import StationAction

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory

from .entity import (
    DeebotEntity,
    DeebotEntityDescription,
    LazyDescriptions,
    async_setup_capability_entities,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from deebot_client.command import Command

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeebotConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class DeebotButtonEntityDescription(DeebotEntityDescription, ButtonEntityDescription):
    """An action and how to build the command that performs it."""

    command_fn: Callable[[Any], Command]


#: Action -> translation key, written out as literals rather than built from
#: the enum member's name. Two reasons, and the second is the load-bearing
#: one: a translated name should not change because a vendor library renamed
#: an enum member, and the wiring suite can only check a string it can SEE --
#: an f-string key is invisible to a static check, so a missing translation
#: would ship as a blank name on a dashboard with nothing to catch it.
_STATION_KEYS = {
    StationAction.EMPTY_DUSTBIN: "station_empty_dustbin",
    StationAction.DRY_MOP: "station_dry_mop",
    StationAction.CLEAN_BASE: "station_clean_base",
    StationAction.WASH_MOP: "station_wash_mop",
}


def _station_descriptions(capabilities: Any) -> list[DeebotButtonEntityDescription]:
    station = capabilities.station
    if station is None:
        return []

    descriptions = []
    for action in station.action.types:
        slug = str(action.name).lower()
        translation_key = _STATION_KEYS.get(action)
        descriptions.append(
            DeebotButtonEntityDescription(
                capability="station_action",
                key=f"station_{slug}",
                # An action this map has never seen still gets its button,
                # under a plain English name rather than a blank one.
                # Dropping it would be the worse failure: a capability the
                # station declared, silently absent from Home Assistant.
                translation_key=translation_key,
                name=None if translation_key else slug.replace("_", " ").capitalize(),
                command_fn=(lambda member: lambda c: c.station.action.execute(member))(
                    action
                ),
            )
        )
    return descriptions


def _life_span_descriptions(components: Any) -> list[DeebotButtonEntityDescription]:
    return [
        DeebotButtonEntityDescription(
            capability="life_span",
            key=f"reset_life_span_{str(component.name).lower()}",
            translation_key="reset_life_span",
            placeholders={
                "component": str(component.name).lower().replace("_", " ")
            },
            command_fn=(lambda member: lambda c: c.life_span.reset(member))(component),
            entity_category=EntityCategory.CONFIG,
        )
        for component in components
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data

    def _descriptions() -> list[DeebotButtonEntityDescription]:
        return [
            *_station_descriptions(coordinator.device.capabilities),
            *_life_span_descriptions(coordinator.life_span_components),
        ]

    async_setup_capability_entities(
        entry, LazyDescriptions(_descriptions), DeebotButton, async_add_entities
    )


class DeebotButton(DeebotEntity, ButtonEntity):
    """One action against the robot or its station."""

    entity_description: DeebotButtonEntityDescription

    async def async_press(self) -> None:
        await self._execute(
            self.entity_description.command_fn(self.coordinator.device.capabilities)
        )
