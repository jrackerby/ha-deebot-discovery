"""Settings whose values come from a fixed set.

THE OPTION LIST IS THE SEED'S, AND THAT IS A DELIBERATE, BOUNDED EXCEPTION.
Everything else in this integration refuses the borrowed table as evidence,
but there is no read command that enumerates the values a robot accepts: the
protocol answers with the CURRENT value and nothing else. So the option list
comes from the sibling model's table, and the exception is kept honest two
ways -- the entity only exists because the robot ANSWERED the read that owns
these options, and an option the robot rejects surfaces as an error rather
than as a silent no-op.

Fan speed is NOT here. Home Assistant's vacuum platform carries it natively,
and a select beside the vacuum entity would be a second control for one
setting -- two surfaces for one reading, which is a defect rather than a
convenience.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deebot_client.events import WorkModeEvent
from deebot_client.events.auto_empty import AutoEmptyEvent

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory

from .entity import (
    DeebotEntity,
    DeebotEntityDescription,
    async_setup_capability_entities,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from deebot_client.command import Command
    from deebot_client.events import Event

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeebotConfigEntry, DeebotCoordinator

PARALLEL_UPDATES = 1


def _option(member: Any) -> str:
    """One spelling for every option, whatever kind of enum it came from.

    `.name.lower()` rather than `.value`: the values are IntEnum members on
    fan speed and work mode and a StrEnum on auto-empty, so `.value` would
    put integers in a select on two of the three.
    """
    return str(member.name).lower()


@dataclass(frozen=True, kw_only=True)
class DeebotSelectEntityDescription(DeebotEntityDescription, SelectEntityDescription):
    """A fixed-set setting, its event, and how to write it."""

    event_type: type[Event]
    capability_fn: Callable[[Any], Any]
    current_fn: Callable[[Any], Any | None]
    command_fn: Callable[[Any, Any], Command]


def _typed(getter: Callable[[Any], Any]) -> Callable[[Any], bool]:
    def _check(capabilities: Any) -> bool:
        capability = getter(capabilities)
        return (
            capability is not None
            and hasattr(capability, "set")
            and bool(getattr(capability, "types", ()))
        )

    return _check


SELECTS: tuple[DeebotSelectEntityDescription, ...] = (
    DeebotSelectEntityDescription(
        capability="work_mode",
        key="work_mode",
        translation_key="work_mode",
        event_type=WorkModeEvent,
        capability_fn=lambda c: c.clean.work_mode,
        current_fn=lambda event: event.mode,
        command_fn=lambda capability, member: capability.set(member),
        supported_fn=_typed(lambda c: c.clean.work_mode),
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotSelectEntityDescription(
        capability="auto_empty",
        key="auto_empty",
        translation_key="auto_empty",
        event_type=AutoEmptyEvent,
        capability_fn=lambda c: c.station.auto_empty if c.station else None,
        current_fn=lambda event: event.frequency,
        # SetAutoEmpty takes (enable, frequency) and picking a frequency
        # without enabling it would set a cadence for a feature left off --
        # the option would stick and nothing would happen.
        command_fn=lambda capability, member: capability.set(True, member),
        supported_fn=_typed(lambda c: c.station.auto_empty if c.station else None),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    async_setup_capability_entities(entry, SELECTS, DeebotSelect, async_add_entities)


class DeebotSelect(DeebotEntity, SelectEntity):
    """One fixed-set robot setting."""

    entity_description: DeebotSelectEntityDescription

    def __init__(
        self, coordinator: DeebotCoordinator, description: DeebotSelectEntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        capability = description.capability_fn(coordinator.device.capabilities)
        self._members = {_option(member): member for member in capability.types}
        self._attr_options = list(self._members)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _on_event(event: Any) -> None:
            current = self.entity_description.current_fn(event)
            # An option the robot reports but the borrowed table never listed
            # reads as unknown rather than being added: the list is what this
            # entity will accept back, and offering a value we cannot write
            # is a control that does not control.
            option = _option(current) if current is not None else None
            self._attr_current_option = option if option in self._members else None
            self.async_write_ha_state()

        self._subscribe(self.entity_description.event_type, _on_event)

    async def async_select_option(self, option: str) -> None:
        description = self.entity_description
        capability = description.capability_fn(self.coordinator.device.capabilities)
        await self._execute(description.command_fn(capability, self._members[option]))
