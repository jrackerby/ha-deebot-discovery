"""Settings that take a number, with the bounds the protocol declares."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deebot_client.events import CleanCountEvent, VolumeEvent
from deebot_client.events.mop_auto_wash_frequency import MopAutoWashFrequencyEvent
from deebot_client.events.water_info import WaterCustomAmountEvent

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime

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


@dataclass(frozen=True, kw_only=True)
class DeebotNumberEntityDescription(DeebotEntityDescription, NumberEntityDescription):
    """A numeric setting, its event, and how to write it."""

    event_type: type[Event]
    capability_fn: Callable[[Any], Any]
    value_fn: Callable[[Any], float | None]
    command_fn: Callable[[Any, int], Command]
    #: Bounds the robot itself reports, when it reports any. Volume is the
    #: case: the event carries the maximum, and hard-coding one would put a
    #: slider on a scale the robot does not use.
    bounds_fn: Callable[[Any], tuple[float, float] | None] | None = None


def _bounded(getter: Callable[[Any], Any]) -> Callable[[Any], bool]:
    """Only build a number over a capability that declares min and max.

    `water.amount` is the reason this exists: it is a CapabilityNumber on
    this hardware and a CapabilitySetTypes on others, and the enum form has
    no bounds and a `set` that takes a different type entirely.
    """

    def _check(capabilities: Any) -> bool:
        capability = getter(capabilities)
        return (
            capability is not None
            and hasattr(capability, "set")
            and hasattr(capability, "min")
            and hasattr(capability, "max")
        )

    return _check


def _unbounded(getter: Callable[[Any], Any]) -> Callable[[Any], bool]:
    def _check(capabilities: Any) -> bool:
        capability = getter(capabilities)
        return capability is not None and hasattr(capability, "set")

    return _check


NUMBERS: tuple[DeebotNumberEntityDescription, ...] = (
    DeebotNumberEntityDescription(
        capability="volume",
        key="volume",
        translation_key="volume",
        event_type=VolumeEvent,
        capability_fn=lambda c: c.settings.volume,
        value_fn=lambda event: event.volume,
        command_fn=lambda capability, value: capability.set(value),
        bounds_fn=lambda event: (0, event.maximum) if event.maximum else None,
        supported_fn=_unbounded(lambda c: c.settings.volume),
        native_min_value=0,
        native_max_value=10,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotNumberEntityDescription(
        capability="clean_count",
        key="clean_count",
        translation_key="clean_count",
        event_type=CleanCountEvent,
        capability_fn=lambda c: c.clean.count,
        value_fn=lambda event: event.count,
        command_fn=lambda capability, value: capability.set(value),
        supported_fn=_unbounded(lambda c: c.clean.count),
        native_min_value=1,
        native_max_value=4,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotNumberEntityDescription(
        capability="water",
        key="water_amount",
        translation_key="water_amount",
        event_type=WaterCustomAmountEvent,
        capability_fn=lambda c: c.water.amount if c.water else None,
        value_fn=lambda event: event.value,
        command_fn=lambda capability, value: capability.set(value),
        supported_fn=_bounded(lambda c: c.water.amount if c.water else None),
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotNumberEntityDescription(
        capability="mop_auto_wash_frequency",
        key="mop_auto_wash_frequency",
        translation_key="mop_auto_wash_frequency",
        event_type=MopAutoWashFrequencyEvent,
        capability_fn=lambda c: c.settings.mop_auto_wash_frequency,
        value_fn=lambda event: event.value,
        command_fn=lambda capability, value: capability.set(value),
        supported_fn=_bounded(lambda c: c.settings.mop_auto_wash_frequency),
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    async_setup_capability_entities(entry, NUMBERS, DeebotNumber, async_add_entities)


class DeebotNumber(DeebotEntity, NumberEntity):
    """One numeric robot setting."""

    entity_description: DeebotNumberEntityDescription

    def __init__(
        self, coordinator: DeebotCoordinator, description: DeebotNumberEntityDescription
    ) -> None:
        super().__init__(coordinator, description)
        capability = description.capability_fn(coordinator.device.capabilities)
        # The protocol's own bounds win over the description's defaults where
        # it declares them -- the description only carries a fallback for the
        # capabilities that do not.
        if hasattr(capability, "min") and hasattr(capability, "max"):
            self._attr_native_min_value = capability.min
            self._attr_native_max_value = capability.max

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _on_event(event: Any) -> None:
            description = self.entity_description
            self._attr_native_value = description.value_fn(event)
            if description.bounds_fn is not None and (
                bounds := description.bounds_fn(event)
            ):
                self._attr_native_min_value, self._attr_native_max_value = bounds
            self.async_write_ha_state()

        self._subscribe(self.entity_description.event_type, _on_event)

    async def async_set_native_value(self, value: float) -> None:
        description = self.entity_description
        capability = description.capability_fn(self.coordinator.device.capabilities)
        await self._execute(description.command_fn(capability, int(value)))
