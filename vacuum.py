"""The robot itself.

WHY THIS ENTITY'S ACTIONS ARE NOT PROBED, since it is the one place the
integration builds something it did not measure directly. Start, pause, stop
and return-to-dock have no read command: the only way to find out whether
`Clean` works is to send it, and sending it is cleaning the house. So they
are IMPLIED -- `discovery.py` records the implication and its reason, and the
antecedent is the `state` probe, which was measured on this robot.

The implication is honest about what it buys. It says the robot has the state
machine these actions drive; it does not promise every action succeeds. One
that does not raises, carrying the command's own name, rather than returning
quietly and leaving a button that appears to do nothing.

SUPPORTED FEATURES ARE READ FRESH, NOT FROZEN AT CONSTRUCTION. A capability
can be promoted by a later probe pass, and a vacuum entity that captured its
feature set the first time it was built would need a reload to notice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from deebot_client.commands.json.clean import CleanArea, CleanMode
from deebot_client.events import FanSpeedEvent, StateEvent
from deebot_client.models import CleanAction, State

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform

from .const import DOMAIN
from .entity import DeebotEntity, DeebotEntityDescription, async_setup_capability_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeebotConfigEntry, DeebotCoordinator

PARALLEL_UPDATES = 1

_ACTIVITY = {
    State.IDLE: VacuumActivity.IDLE,
    State.CLEANING: VacuumActivity.CLEANING,
    State.RETURNING: VacuumActivity.RETURNING,
    State.DOCKED: VacuumActivity.DOCKED,
    State.ERROR: VacuumActivity.ERROR,
    State.PAUSED: VacuumActivity.PAUSED,
}

#: One entity, gated on the capability that MEASURED the state machine.
VACUUM = DeebotEntityDescription(capability="state", key="vacuum", name=None)

SERVICE_CLEAN_ROOMS = "clean_rooms"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacuum entity."""
    async_setup_capability_entities(entry, (VACUUM,), DeebotVacuum, async_add_entities)

    # AN ENTITY SERVICE, NOT A DOMAIN ONE. The target of "clean these rooms"
    # is a robot, and an entity service lets Home Assistant resolve which one
    # from an entity, a device, an area or a label without this integration
    # writing a resolver of its own -- and it is still called
    # `deebot_discovery.clean_rooms`.
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_CLEAN_ROOMS,
        {
            vol.Required("rooms"): vol.All(cv.ensure_list, [cv.string]),
            # Passed through to the robot's own `count`. Not clamped to a
            # range this integration cannot measure: what the hardware
            # accepts is the hardware's to say, and a refusal surfaces as the
            # command failing rather than as a value silently altered here.
            vol.Optional("cleanings", default=1): vol.All(
                vol.Coerce(int), vol.Range(min=1)
            ),
        },
        "async_clean_rooms",
    )


class DeebotVacuum(DeebotEntity, StateVacuumEntity):
    """The robot."""

    _attr_name = None

    def __init__(
        self, coordinator: DeebotCoordinator, description: DeebotEntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        capability = coordinator.device.capabilities.fan_speed
        self._attr_fan_speed_list = (
            [str(member.name).lower() for member in capability.types]
            if capability is not None
            else []
        )
        self._fan_speeds = {
            str(member.name).lower(): member
            for member in (capability.types if capability is not None else ())
        }

    # -- features -----------------------------------------------------------

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """What this robot has been shown to support, right now."""
        usable = self.coordinator.usable
        features = VacuumEntityFeature.STATE
        if "clean_action" in usable:
            features |= (
                VacuumEntityFeature.START
                | VacuumEntityFeature.PAUSE
                | VacuumEntityFeature.STOP
            )
        if "charge" in usable:
            features |= VacuumEntityFeature.RETURN_HOME
        if "play_sound" in usable:
            features |= VacuumEntityFeature.LOCATE
        if "fan_speed" in usable and self._fan_speeds:
            features |= VacuumEntityFeature.FAN_SPEED
        return features

    # -- readings -----------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _on_state(event: StateEvent) -> None:
            # A state the mapping does not carry reads as unknown rather than
            # as idle: a robot reporting something this code has never seen
            # is not a robot sitting still.
            self._attr_activity = _ACTIVITY.get(event.state)
            self.async_write_ha_state()

        async def _on_fan_speed(event: FanSpeedEvent) -> None:
            self._attr_fan_speed = str(event.speed.name).lower()
            self.async_write_ha_state()

        self._subscribe(StateEvent, _on_state)
        self._subscribe(FanSpeedEvent, _on_fan_speed)

    # -- actions ------------------------------------------------------------

    async def async_start(self) -> None:
        await self._async_clean(CleanAction.START)

    async def async_pause(self) -> None:
        await self._async_clean(CleanAction.PAUSE)

    async def async_stop(self, **kwargs: Any) -> None:
        await self._async_clean(CleanAction.STOP)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self._execute(self.coordinator.device.capabilities.charge.execute())

    async def async_locate(self, **kwargs: Any) -> None:
        await self._execute(self.coordinator.device.capabilities.play_sound.execute())

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        capability = self.coordinator.device.capabilities.fan_speed
        await self._execute(capability.set(self._fan_speeds[fan_speed]))

    async def _async_clean(self, action: CleanAction) -> None:
        capability = self.coordinator.device.capabilities.clean.action
        await self._execute(capability.command(action))

    async def async_clean_rooms(self, rooms: list[str], cleanings: int = 1) -> None:
        """Clean the named rooms, in one job, and nothing else.

        NAMES ARE RESOLVED AGAINST WHAT THE ROBOT REPORTS, NOT ACCEPTED ON
        TRUST. An id the robot does not know is not sent: `CleanArea` would
        be answered with an errno the owner sees as "the command failed",
        and the actual mistake -- a room renamed in the Ecovacs app, or a
        typo -- would be nowhere in that message. So an unknown room raises
        BEFORE anything is sent, and says which rooms exist.

        ONE COMMAND FOR ALL OF THEM, because `CleanArea` takes a list and the
        robot plans a single route through it. Sending one command per room
        would queue several jobs, each returning to the dock, which is not
        what "clean the kitchen and the hallway" means.
        """
        known = self.coordinator.rooms
        if not known:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_rooms"
            )

        by_name = {room.name.casefold(): room for room in known}
        by_id = {str(room.id): room for room in known}

        wanted: list[int] = []
        unknown: list[str] = []
        for token in rooms:
            key = token.strip()
            room = by_name.get(key.casefold()) or by_id.get(key)
            if room is None:
                unknown.append(key)
            elif room.id not in wanted:
                wanted.append(room.id)

        if unknown:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_rooms",
                translation_placeholders={
                    "rooms": ", ".join(unknown),
                    "known": ", ".join(room.name for room in known),
                },
            )

        await self._execute(
            CleanArea(mode=CleanMode.SPOT_AREA, area=wanted, cleanings=cleanings)
        )
