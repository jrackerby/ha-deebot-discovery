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

from deebot_client.events import FanSpeedEvent, StateEvent
from deebot_client.models import CleanAction, State

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacuum entity."""
    async_setup_capability_entities(entry, (VACUUM,), DeebotVacuum, async_add_entities)


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
