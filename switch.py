"""The robot's on/off settings.

Every switch here is gated TWICE, and the two gates answer different
questions. `capability` asks whether this robot answered the read command --
a fact measured on this robot, this session. `supported_fn` asks whether the
borrowed protocol table still carries a way to WRITE the setting: the seed's
`ota` row, for instance, is a set-enable on some models and a read-only event
on others, and a switch built over the read-only form would have no `set` to
call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deebot_client.events import (
    ChildLockEvent,
    ContinuousCleaningEvent,
    OtaEvent,
    SweepModeEvent,
    TrueDetectEvent,
    VoiceAssistantStateEvent,
)

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
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

    from .coordinator import DeebotConfigEntry

#: Commands, so one at a time: the robot answers a burst of writes by
#: dropping some of them, and a dropped write reads as a switch that bounced.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class DeebotSwitchEntityDescription(DeebotEntityDescription, SwitchEntityDescription):
    """A setting, where to find its commands, and how to read its event."""

    event_type: type[Event]
    #: Where this setting lives on the borrowed `Capabilities` object.
    capability_fn: Callable[[Any], Any]
    value_fn: Callable[[Any], bool | None]


def _settable(getter: Callable[[Any], Any]) -> Callable[[Any], bool]:
    """A `supported_fn` that demands a writable capability object."""

    def _check(capabilities: Any) -> bool:
        capability = getter(capabilities)
        return capability is not None and hasattr(capability, "set")

    return _check


#: Written out one by one rather than built by a helper, and the repetition
#: is the point. `capability` has to be a LITERAL at the place the entity is
#: declared or the wiring suite cannot check it, and a helper that defaulted
#: it from `key` would make a misspelling undetectable in exactly the file
#: where six of them sit side by side.
SWITCHES: tuple[DeebotSwitchEntityDescription, ...] = (
    DeebotSwitchEntityDescription(
        capability="child_lock",
        key="child_lock",
        translation_key="child_lock",
        event_type=ChildLockEvent,
        capability_fn=lambda c: c.settings.child_lock,
        value_fn=lambda event: event.enabled,
        supported_fn=_settable(lambda c: c.settings.child_lock),
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotSwitchEntityDescription(
        capability="true_detect",
        key="true_detect",
        translation_key="true_detect",
        event_type=TrueDetectEvent,
        capability_fn=lambda c: c.settings.true_detect,
        value_fn=lambda event: event.enabled,
        supported_fn=_settable(lambda c: c.settings.true_detect),
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotSwitchEntityDescription(
        capability="sweep_mode",
        key="sweep_mode",
        translation_key="sweep_mode",
        event_type=SweepModeEvent,
        capability_fn=lambda c: c.settings.sweep_mode,
        value_fn=lambda event: event.enabled,
        supported_fn=_settable(lambda c: c.settings.sweep_mode),
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotSwitchEntityDescription(
        capability="voice_assistant",
        key="voice_assistant",
        translation_key="voice_assistant",
        event_type=VoiceAssistantStateEvent,
        capability_fn=lambda c: c.settings.voice_assistant,
        value_fn=lambda event: event.enabled,
        supported_fn=_settable(lambda c: c.settings.voice_assistant),
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotSwitchEntityDescription(
        capability="continuous_cleaning",
        key="continuous_cleaning",
        translation_key="continuous_cleaning",
        event_type=ContinuousCleaningEvent,
        capability_fn=lambda c: c.clean.continuous,
        value_fn=lambda event: event.enabled,
        supported_fn=_settable(lambda c: c.clean.continuous),
        entity_category=EntityCategory.CONFIG,
    ),
    DeebotSwitchEntityDescription(
        capability="ota",
        key="ota",
        translation_key="ota",
        event_type=OtaEvent,
        # OtaEvent is not an EnableEvent: it carries the auto-update flag
        # under a different name, and `None` there means the robot reported
        # an OTA event that says nothing about the setting -- unknown, never
        # off.
        value_fn=lambda event: event.auto_enabled,
        capability_fn=lambda c: c.settings.ota,
        supported_fn=_settable(lambda c: c.settings.ota),
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeebotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    async_setup_capability_entities(entry, SWITCHES, DeebotSwitch, async_add_entities)


class DeebotSwitch(DeebotEntity, SwitchEntity):
    """One robot setting."""

    entity_description: DeebotSwitchEntityDescription

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _on_event(event: Any) -> None:
            self._attr_is_on = self.entity_description.value_fn(event)
            self.async_write_ha_state()

        self._subscribe(self.entity_description.event_type, _on_event)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(enable=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(enable=False)

    async def _async_set(self, *, enable: bool) -> None:
        capability = self.entity_description.capability_fn(
            self.coordinator.device.capabilities
        )
        await self._execute(capability.set(enable))
