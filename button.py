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

import logging
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
from .rooms import find_room
from .seed import CLEAN_AREA_MODE

if TYPE_CHECKING:
    from collections.abc import Callable

    from deebot_client.command import Command

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeebotConfigEntry

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DeebotButtonEntityDescription(DeebotEntityDescription, ButtonEntityDescription):
    """An action and how to build the command that performs it."""

    command_fn: Callable[[Any], Command]

    #: Set only on the per-room buttons: the map id this button addresses.
    #: Carried here rather than parsed back out of `key` so the lookup does
    #: not depend on how the key happens to be spelled.
    room_id: int | None = None


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


def _room_descriptions(rooms: Any) -> list[DeebotButtonEntityDescription]:
    """One button per room the robot's own map reports.

    KEYED ON THE ROOM ID, NAMED BY THE ROOM NAME, and those are deliberately
    different fields. Renaming a room in the Ecovacs app changes what the
    button is called and must not change which entity it is -- an entity id
    that moved would take the owner's automations with it. The id is the
    robot's own handle and is what the clean command is given.

    THE WHOLE WIRE FORM COMES FROM `seed.py`, not from an import here. Both
    halves of it -- which command class carries a per-area clean, and which
    `content.type` names one -- are properties of this robot's firmware, and
    both were wrong here at different times: these buttons sent the refused
    `clean` until #28, and then the refused `spotArea` until #27 closed. A
    literal at the call site is a guess nobody measured; `CLEAN_AREA_MODE`
    carries the measurement that replaced it.
    """
    return [
        DeebotButtonEntityDescription(
            capability="rooms",
            key=f"clean_room_{room.id}",
            translation_key="clean_room",
            placeholders={"room": room.name},
            room_id=room.id,
            command_fn=(
                lambda room_id: lambda c: c.clean.action.area(
                    CLEAN_AREA_MODE, [room_id], 1
                )
            )(room.id),
        )
        for room in rooms
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
            *_room_descriptions(coordinator.rooms),
        ]

    def _build(
        coordinator: Any, description: DeebotButtonEntityDescription
    ) -> DeebotEntity:
        # Only the room buttons need the live lookup; everything else is
        # fixed for the life of the entry and pays nothing for it.
        if description.room_id is not None:
            return DeebotRoomButton(coordinator, description)
        return DeebotButton(coordinator, description)

    async_setup_capability_entities(
        entry, LazyDescriptions(_descriptions), _build, async_add_entities
    )


class DeebotButton(DeebotEntity, ButtonEntity):
    """One action against the robot or its station."""

    entity_description: DeebotButtonEntityDescription

    async def async_press(self) -> None:
        await self._execute(
            self.entity_description.command_fn(self.coordinator.device.capabilities)
        )


class DeebotRoomButton(DeebotButton):
    """A room button that re-reads its room on every coordinator update.

    The map is not fixed for the life of the config entry: a room can be
    renamed in the Ecovacs app, and a room can be deleted from the map
    entirely. Both used to need an entry reload to show up here, because a
    description is read once when the entity is constructed and
    `async_setup_capability_entities` never rebuilds a key it has already
    added -- correctly, since rebuilding is how an entity id moves.

    WHAT DOES NOT CHANGE IS THE ENTITY ID. The button stays keyed on the room
    id (`button.py`'s `_room_descriptions` says why), so a rename moves the
    NAME and nothing else, and a deletion leaves the row in the registry
    rather than taking the owner's automations with it.
    """

    entity_description: DeebotButtonEntityDescription

    def __init__(
        self, coordinator: Any, description: DeebotButtonEntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        if description.room_id is None:
            raise ValueError(f"{description.key} is not a room button")
        self._room_id: int = description.room_id
        room = find_room(coordinator.rooms, self._room_id)
        self._room_name: str | None = room.name if room else None
        #: Latched, so the warning below is deduped on the CONDITION rather
        #: than on its message text (LAW.md §15).
        self._rename_unrenderable = False

    @property
    def available(self) -> bool:
        """Unavailable once the room leaves the map.

        The base rule cannot answer this: it gates on the `rooms` CAPABILITY,
        which stays usable while the robot still has a map -- so a button for
        a deleted room stayed pressable and sent a clean order for an area the
        robot no longer knows, which comes back as a bare "command failed"
        naming nothing. Refusing at the entity is the same refusal
        `deebot_discovery.clean_rooms` already makes by name.
        """
        return super().available and find_room(self.coordinator.rooms, self._room_id) is not None

    def _handle_coordinator_update(self) -> None:
        room = find_room(self.coordinator.rooms, self._room_id)
        if room is not None and room.name != self._room_name:
            self._rename(room.name)
        super()._handle_coordinator_update()

    def _rename(self, name: str) -> None:
        """Point the button's translated name at the room's new name.

        INVALIDATING THE CACHE IS THE WHOLE JOB, and it is not the obvious
        line. Assigning `_attr_translation_placeholders` invalidates the
        cached `translation_placeholders` and NOTHING DERIVED FROM IT, while
        the displayed name comes from `Entity.name` -- a `cached_property`
        that substitutes the placeholders once, inside `_name_internal`.
        Setting the placeholders alone therefore changes nothing on the wall,
        which is the failure this method exists to avoid shipping.

        Popping the entry is how Home Assistant's own `CachedProperties`
        setters invalidate, and once `name` differs from the registry's
        stored `original_name`, `async_get_full_entity_name` prefers the live
        value -- so the new name reaches the state's `friendly_name`. Read
        against home-assistant 2026.9.2; the registry's own `original_name`
        is left alone, so Settings keeps showing the name the room had when
        the entity was first added.

        If a future core stops caching `name` this way the pop becomes a
        no-op and the symptom returns silently, so the failure is checked for
        rather than assumed away.
        """
        previous = self.name
        self._room_name = name
        self._attr_translation_placeholders = {"room": name}
        self.__dict__.pop("name", None)
        if self.name == previous and not self._rename_unrenderable:
            self._rename_unrenderable = True
            _LOGGER.warning(
                "Room %s is now called %r on the robot's map, but this button's"
                " name did not change -- Home Assistant's entity-name cache was"
                " not invalidated by this integration. Reload the config entry"
                " to pick the name up, and see DeebotRoomButton._rename",
                self._room_id,
                name,
            )
