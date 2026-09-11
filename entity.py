"""The device row, the availability rule, and the event plumbing.

THE AVAILABILITY SPLIT. Three different things can be wrong, and collapsing
them into one value is how a monitor stops being one:

  * WE cannot reach the Ecovacs cloud. We hold no reading and no entity can
    say anything. Unavailable, and that is what the quality scale's
    `entity-unavailable` rule is about.
  * The cloud CAN be reached and reports the robot offline. That is a
    READING. Most entities have nothing to show, so they go unavailable --
    but `binary_sensor.<robot>_connectivity` must NOT, because an entity that
    disappears with its subject cannot report the subject down.
  * The capability behind this entity has been DEMOTED. The robot is fine and
    this particular function is not there. The entity goes unavailable and
    stays in the registry rather than being deleted: a demotion is a belief
    this integration can revise on the next pass, and deleting the row would
    take the user's automations and history with it on the strength of three
    consecutive misses.

WHY ENTITIES ARE NEVER REMOVED ON DEMOTION, said once here rather than in six
platform files: promotion is cheap and reversible, deletion is neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DeebotCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable, Mapping

    from deebot_client.command import Command
    from deebot_client.events import Event

    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeebotConfigEntry


@dataclass(frozen=True, kw_only=True)
class DeebotEntityDescription(EntityDescription):
    """An entity description that names the capability gating it.

    EVERY description in this integration carries one. That is the whole
    contract in one field: an entity with no capability key would be an
    entity nobody measured, and the wiring suite fails the build if a
    platform ever constructs one.
    """

    capability: str

    #: An extra, SEED-SHAPE test: does the borrowed capability object have
    #: the shape this entity needs? It answers a different question from
    #: `capability`, which is about the robot. `water.amount` is a number on
    #: this hardware and an enum on others, and a number entity built over
    #: the enum form would be wired to a `set` that takes the wrong type --
    #: a failure that only appears when someone moves the slider.
    supported_fn: Callable[[Any], bool] | None = None

    #: Values for a translation string's placeholders, for the descriptions
    #: built per component rather than written out. Named `placeholders`
    #: rather than `translation_placeholders` deliberately: the base
    #: `EntityDescription` may or may not carry a field of that name
    #: depending on the core version, and silently shadowing one would be a
    #: bug that only appears as a missing name on a dashboard.
    placeholders: Mapping[str, str] | None = None


#: Written with `TypeVar` rather than PEP 695 syntax so every module here
#: parses on an interpreter older than the one it runs on. The wiring suite
#: reads all of them with `ast`, and a file it cannot parse is a file it
#: cannot check -- which is the failure mode where a gate silently stops
#: gating.
_EventT = TypeVar("_EventT", bound="Event")


class DeebotEntity(CoordinatorEntity[DeebotCoordinator]):
    """Base for every entity this integration builds."""

    _attr_has_entity_name = True
    entity_description: DeebotEntityDescription

    def __init__(
        self, coordinator: DeebotCoordinator, description: DeebotEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        if description.placeholders:
            self._attr_translation_placeholders = dict(description.placeholders)
        device = coordinator.device
        info = device.device_info
        did = info["did"]
        self._attr_unique_id = f"{did}_{description.key}"

        connections = set()
        if device.mac:
            connections.add((CONNECTION_NETWORK_MAC, device.mac))

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, did)},
            manufacturer="Ecovacs",
            # `nick` is what the owner called it in the Ecovacs app; `name`
            # is the account-side identifier and is not a name anyone reads.
            name=info.get("nick") or info.get("name") or did,
            model=info.get("deviceName"),
            # The class string is the thing upstream's table is keyed on and
            # has no row for. Surfacing it on the device page means the next
            # person to hit this does not have to read a log to find it.
            model_id=info["class"],
            serial_number=did,
            sw_version=device.fw_version,
            connections=connections,
        )

    @property
    def _capability(self) -> str:
        return self.entity_description.capability

    @property
    def available(self) -> bool:
        """See the module docstring for the three cases this folds."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.robot_available
            and self._capability in self.coordinator.usable
        )

    async def _execute(self, command: Command) -> None:
        """Send a command and refuse to report a silent no-op as success.

        `Device.execute_command` answers with the raw response on success and
        with an EMPTY DICT on every failure path -- a timeout, an errno, a
        robot that is not there. Returning quietly on that would leave the
        user looking at a switch that flipped back a second later with
        nothing in the log, so it becomes the error the quality scale's
        `action-exceptions` rule asks for.
        """
        response = await self.coordinator.device.execute_command(command)
        if not response:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": command.NAME},
            )

    def _subscribe(
        self,
        event_type: type[_EventT],
        handler: Callable[[_EventT], Coroutine[Any, Any, None]],
    ) -> None:
        """Subscribe to one robot event for as long as this entity exists.

        Subscribing happens here rather than at integration setup so that the
        unsubscribe is handed to `async_on_remove` and cannot outlive the
        entity -- the quality scale's `entity-event-setup` rule.
        """
        self.async_on_remove(self.coordinator.device.events.subscribe(event_type, handler))


@callback
def async_setup_capability_entities(
    entry: DeebotConfigEntry,
    descriptions: Iterable[DeebotEntityDescription],
    build: Callable[[DeebotCoordinator, Any], DeebotEntity],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an entity for each capability that is usable, now and later.

    Called once at platform setup and again on every coordinator update, so a
    capability promoted by a later probe pass gets its entity without a
    reload -- which is the point of continuing to probe at all.

    It only ever ADDS. A capability that is demoted keeps its entity and the
    entity reports unavailable; see this module's docstring for why deleting
    on a demotion is not symmetric with creating on a promotion.
    """
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _sync() -> None:
        new = [
            build(coordinator, description)
            for description in descriptions
            if description.capability in coordinator.usable
            and description.key not in added
            and (
                description.supported_fn is None
                or description.supported_fn(coordinator.device.capabilities)
            )
        ]
        if new:
            added.update(entity.entity_description.key for entity in new)
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class LazyDescriptions:
    """An iterable of descriptions that re-reads its source on every pass.

    Needed wherever the set of entities is not fixed at platform setup --
    consumables appear as the robot reports them. A tuple captured once would
    freeze that set at whatever had arrived in the first few seconds, which
    on a robot that is docked and quiet is nothing at all.
    """

    def __init__(self, source: Callable[[], Iterable[DeebotEntityDescription]]) -> None:
        self._source = source

    def __iter__(self) -> Any:
        return iter(self._source())
