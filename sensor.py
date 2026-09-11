"""Read-only readings, each gated on a capability that answered.

Life-span sensors are the one set built at a finer grain than a capability:
`life_span` answering proves the robot tracks consumable wear, and the reply
names WHICH consumables. So the seeded query asks about twelve components and
a sensor appears for each one the robot actually reports -- never for one it
did not, however confidently a sibling model's table lists it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from deebot_client.events import (
    BatteryEvent,
    CleanLogEvent,
    ErrorEvent,
    LifeSpanEvent,
    NetworkInfoEvent,
    StatsEvent,
    TotalStatsEvent,
)
from deebot_client.events.station import StationEvent
from deebot_client.events.station import State as StationState

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)

from .entity import (
    DeebotEntity,
    DeebotEntityDescription,
    LazyDescriptions,
    async_setup_capability_entities,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from deebot_client.events import Event

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import DeebotConfigEntry, DeebotCoordinator

#: Nothing here commands the robot, so a queue of one would buy nothing.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DeebotSensorEntityDescription(DeebotEntityDescription, SensorEntityDescription):
    """A reading, the event it rides on, and how to read it."""

    event_type: type[Event]
    value_fn: Callable[[Any], StateType | datetime]
    attributes_fn: Callable[[Any], dict[str, Any]] | None = None


def _last_clean(event: CleanLogEvent) -> datetime | None:
    if not event.logs:
        return None
    return datetime.fromtimestamp(event.logs[0].timestamp, tz=UTC)


def _last_clean_attributes(event: CleanLogEvent) -> dict[str, Any]:
    if not event.logs:
        return {}
    entry = event.logs[0]
    return {
        "area": entry.area,
        "duration": entry.duration,
        "type": entry.type,
        "stop_reason": entry.stop_reason.name.lower(),
    }


SENSORS: tuple[DeebotSensorEntityDescription, ...] = (
    DeebotSensorEntityDescription(
        capability="battery",
        key="battery",
        event_type=BatteryEvent,
        value_fn=lambda event: event.value,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    DeebotSensorEntityDescription(
        capability="error",
        key="error",
        translation_key="error",
        event_type=ErrorEvent,
        value_fn=lambda event: event.code,
        attributes_fn=lambda event: {"description": event.description},
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DeebotSensorEntityDescription(
        capability="network",
        key="wifi_rssi",
        translation_key="wifi_rssi",
        event_type=NetworkInfoEvent,
        value_fn=lambda event: event.rssi,
        attributes_fn=lambda event: {"ssid": event.ssid, "ip": event.ip},
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DeebotSensorEntityDescription(
        capability="stats_clean",
        key="clean_area",
        translation_key="clean_area",
        event_type=StatsEvent,
        value_fn=lambda event: event.area,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeebotSensorEntityDescription(
        capability="stats_clean",
        key="clean_duration",
        translation_key="clean_duration",
        event_type=StatsEvent,
        value_fn=lambda event: event.time,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DeebotSensorEntityDescription(
        capability="stats_total",
        key="total_clean_area",
        translation_key="total_clean_area",
        event_type=TotalStatsEvent,
        value_fn=lambda event: event.area,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    DeebotSensorEntityDescription(
        capability="stats_total",
        key="total_clean_duration",
        translation_key="total_clean_duration",
        event_type=TotalStatsEvent,
        value_fn=lambda event: event.time,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    DeebotSensorEntityDescription(
        capability="stats_total",
        key="total_cleanings",
        translation_key="total_cleanings",
        event_type=TotalStatsEvent,
        value_fn=lambda event: event.cleanings,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    DeebotSensorEntityDescription(
        capability="clean_log",
        key="last_clean",
        translation_key="last_clean",
        event_type=CleanLogEvent,
        value_fn=_last_clean,
        attributes_fn=_last_clean_attributes,
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    DeebotSensorEntityDescription(
        capability="station_state",
        key="station_state",
        translation_key="station_state",
        event_type=StationEvent,
        value_fn=lambda event: event.state.name.lower(),
        device_class=SensorDeviceClass.ENUM,
        options=[state.name.lower() for state in StationState],
    ),
)


def _life_span_description(component: str) -> DeebotSensorEntityDescription:
    """Build the description for one consumable the robot actually reported."""
    return DeebotSensorEntityDescription(
        capability="life_span",
        key=f"life_span_{component}",
        translation_key="life_span",
        placeholders={"component": component.replace("_", " ")},
        event_type=LifeSpanEvent,
        value_fn=lambda event: event.percent,
        attributes_fn=lambda event: {"remaining_minutes": event.remaining},
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors, and keep setting them up as capabilities arrive."""
    coordinator = entry.runtime_data

    def _descriptions() -> list[DeebotSensorEntityDescription]:
        return [
            *SENSORS,
            *(
                _life_span_description(component.name.lower())
                for component in coordinator.life_span_components
            ),
        ]

    # The life-span set grows as the robot reports components, so the list is
    # rebuilt on every sync rather than captured once. A tuple captured at
    # setup would freeze the consumables at whatever had arrived in the first
    # few seconds -- which, on a robot that is docked and quiet, is none.
    async_setup_capability_entities(
        entry, LazyDescriptions(_descriptions), DeebotSensor, async_add_entities
    )


class DeebotSensor(DeebotEntity, SensorEntity):
    """One reading from one robot event."""

    entity_description: DeebotSensorEntityDescription

    def __init__(
        self,
        coordinator: DeebotCoordinator,
        description: DeebotSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description)
        self._component: str | None = None
        if description.key.startswith("life_span_"):
            self._component = description.key.removeprefix("life_span_")

    def _apply(self, event: Any) -> None:
        description = self.entity_description
        self._attr_native_value = description.value_fn(event)
        if description.attributes_fn is not None:
            self._attr_extra_state_attributes = description.attributes_fn(event)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # SEEDED FROM THE COORDINATOR'S CACHE FIRST. The event bus replays
        # only the last event of each TYPE, and every consumable shares one
        # LifeSpanEvent type -- so this sensor was created by its own
        # component's event and then handed somebody else's on subscribe.
        # Without this it reads `unknown` until that component is reported
        # again, which measured live as 12 of 13 sensors empty.
        if self._component is not None:
            cached = self.coordinator.life_span_events.get(self._component)
            if cached is not None:
                self._apply(cached)

        async def _on_event(event: Any) -> None:
            # A life-span event names ONE component, and every life-span
            # sensor is subscribed to all of them. Without this filter the
            # brush sensor would show the filter's percentage -- the entities
            # would all read plausibly and all be wrong.
            if self._component is not None and event.type.name.lower() != self._component:
                return
            self._apply(event)
            self.async_write_ha_state()

        self._subscribe(self.entity_description.event_type, _on_event)
