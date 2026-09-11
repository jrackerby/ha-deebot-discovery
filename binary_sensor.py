"""Two readings about the robot, and one about whether we can see it.

THE CONNECTIVITY SENSOR IS THE DELIBERATE EXCEPTION TO THIS INTEGRATION'S
AVAILABILITY RULE, and it is the whole reason this platform is not just two
entities. Every other entity goes unavailable when the cloud reports the
robot offline, because it has nothing to show. This one must not: a monitor
whose blind spot correlates with what it monitors is worse than none, and an
entity that vanishes exactly when the robot goes away cannot report that the
robot went away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deebot_client.events import ErrorEvent
from deebot_client.events.water_info import MopAttachedEvent

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .entity import (
    DeebotEntity,
    DeebotEntityDescription,
    async_setup_capability_entities,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from deebot_client.events import Event

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeebotConfigEntry, DeebotCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DeebotBinarySensorEntityDescription(
    DeebotEntityDescription, BinarySensorEntityDescription
):
    """A yes/no reading and the event it rides on."""

    #: None for the one entity that reads the coordinator rather than a
    #: robot event. Declaring an event type it never subscribes to would be
    #: a field that lies -- and the next person to add a sensor would copy it.
    event_type: type[Event] | None = None
    value_fn: Callable[[Any], bool] | None = None


BINARY_SENSORS: tuple[DeebotBinarySensorEntityDescription, ...] = (
    DeebotBinarySensorEntityDescription(
        capability="water",
        key="mop_attached",
        translation_key="mop_attached",
        event_type=MopAttachedEvent,
        value_fn=lambda event: bool(event.value),
    ),
    DeebotBinarySensorEntityDescription(
        capability="error",
        key="problem",
        event_type=ErrorEvent,
        # Zero is "no error", and it is a READING, not an absence. Reporting
        # it as `off` rather than as unknown is the difference between "the
        # robot says it is fine" and "we have not heard".
        value_fn=lambda event: event.code != 0,
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
)

#: Gated on the CONTROL capability, and that is not an arbitrary choice: the
#: control probe is what the availability signal is derived from, so this
#: entity exists exactly when there is something behind it to report.
CONNECTIVITY = DeebotBinarySensorEntityDescription(
    capability="battery",
    key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


def _build(
    coordinator: DeebotCoordinator, description: DeebotBinarySensorEntityDescription
) -> DeebotEntity:
    if description.key == CONNECTIVITY.key:
        return DeebotConnectivitySensor(coordinator, description)
    return DeebotBinarySensor(coordinator, description)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_setup_capability_entities(
        entry, (*BINARY_SENSORS, CONNECTIVITY), _build, async_add_entities
    )


class DeebotBinarySensor(DeebotEntity, BinarySensorEntity):
    """One yes/no reading from one robot event."""

    entity_description: DeebotBinarySensorEntityDescription

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        description = self.entity_description
        if description.event_type is None or description.value_fn is None:
            # Loudly, not quietly. `EventBus.subscribe(None, ...)` files the
            # handler under a key nothing ever notifies, so the entity would
            # sit at `unknown` for ever with nothing in any log -- which is
            # the failure mode this whole integration is written against.
            msg = (
                f"{description.key!r} has no event to read; it belongs on the "
                "coordinator-backed class instead"
            )
            raise RuntimeError(msg)

        async def _on_event(event: Any) -> None:
            self._attr_is_on = description.value_fn(event)
            self.async_write_ha_state()

        self._subscribe(description.event_type, _on_event)


class DeebotConnectivitySensor(DeebotEntity, BinarySensorEntity):
    """Whether the cloud can reach the robot.

    Subscribes to nothing: it reports the coordinator's own view, which is
    fed by deebot-client's availability check. And it overrides `available`
    down to "can we reach the cloud at all" -- see the module docstring.
    """

    entity_description: DeebotBinarySensorEntityDescription

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self.coordinator.robot_available
