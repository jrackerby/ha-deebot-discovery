"""Owns the cloud session, the robot, and the capability map.

WHAT THIS COORDINATOR REFRESHES IS NOT STATE. Readings arrive over MQTT push,
through deebot-client's own event bus, and entities subscribe to that bus
themselves. What the interval drives is a PROBE PASS: the thing that decides
which entities should exist at all. Two reasons it has to keep running rather
than settle once:

  * A firmware update can add a function, and a set that only ever shrinks
    would never see it.
  * A demotion costs `miss_threshold` consecutive misses across VOID-free
    passes, so a capability that has genuinely gone needs passes to go.

A VOID PASS IS NOT AN UPDATE FAILURE. It is the resolver working: the control
did not answer, so the pass says nothing and the previous map stands. Raising
`UpdateFailed` there would take every entity unavailable on the strength of a
reading the resolver has already declared inadmissible -- which is the exact
inversion this integration exists to refuse.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.commands.json.map import (
    GetCachedMapInfo,
    GetMapSetV2,
    decompress_base64_data,
)
from deebot_client.device import Device
from deebot_client.events import AvailabilityEvent, LifeSpan, LifeSpanEvent, RoomsEvent
from deebot_client.events.map import CachedMapInfoEvent, MapSetType
from deebot_client.exceptions import (
    DeebotError,
    InvalidAuthenticationError,
)
from deebot_client.message import HandlingState
from deebot_client.models import DeviceInfo
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .classify import classify
from .const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_DID,
    DEFAULT_MISS_THRESHOLD,
    DOMAIN,
    LOGGER,
    PROBE_CONCURRENCY,
    PROBE_INTERVAL_SECONDS,
    PROBE_PASS_TIMEOUT_SECONDS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    ProbeOutcome,
    Support,
)
from .discovery import CONTROL_KEYS, PROBED_KEYS, SEED_HYPOTHESIS, usable_keys
from .probe import CapabilityState, PassResult, resolve, seed_order
from .rooms import Room, parse_rooms
from .seed import build_probe, load_seed

if TYPE_CHECKING:
    from collections.abc import Mapping

    from deebot_client.command import Command


def _restore(raw: Any) -> dict[str, CapabilityState]:
    """Rebuild the map from storage, dropping anything unreadable.

    A row this version cannot read is DISCARDED rather than guessed at: the
    cost of dropping one is a capability that reads UNKNOWN until the next
    pass re-measures it, and the cost of guessing is a belief with nothing
    behind it. Those are not the same size.
    """
    states: dict[str, CapabilityState] = {}
    if not isinstance(raw, dict):
        return states
    for key, value in (raw.get("capabilities") or {}).items():
        if not isinstance(value, dict):
            continue
        try:
            support = Support(value["support"])
            misses = int(value["misses"])
        except (KeyError, TypeError, ValueError):
            LOGGER.debug("Dropping unreadable stored capability row %s: %s", key, value)
            continue
        # Absent on a row written before this field existed, and unreadable if
        # the vocabulary moved. Neither costs anything: it is a record of the
        # last reading, so losing it means one pass with less to say.
        try:
            outcome = (
                ProbeOutcome(value["last_outcome"])
                if value.get("last_outcome") is not None
                else None
            )
        except ValueError:
            outcome = None
        states[key] = CapabilityState(support, misses, outcome)
    return states


def _dump(states: Mapping[str, CapabilityState]) -> dict[str, Any]:
    return {
        "capabilities": {
            key: {
                "support": state.support.value,
                "misses": state.misses,
                "last_outcome": (
                    state.last_outcome.value if state.last_outcome else None
                ),
            }
            for key, state in sorted(states.items())
        }
    }


class DeebotCoordinator(DataUpdateCoordinator[frozenset[str]]):
    """Runs probe passes and publishes the capability map."""

    config_entry: DeebotConfigEntry

    def __init__(self, hass: HomeAssistant, entry: DeebotConfigEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=PROBE_INTERVAL_SECONDS),
        )
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}"
        )
        self._states: dict[str, CapabilityState] = {}
        self._authenticator: Authenticator | None = None
        self._mqtt: MqttClient | None = None
        self._device: Device | None = None
        self._available = False
        # Deduped on a STABLE CONDITION TOKEN, never on the rendered message:
        # a count or a member list inside the compared string turns one
        # condition into a new line every time membership moves.
        self._logged_condition: str | None = None
        self._unloading = False
        # Consumables are discovered FINER THAN THE CAPABILITY. `life_span`
        # answering says the robot tracks wear; it does not say for which
        # parts, and the seeded query deliberately asks about more components
        # than any one robot has. The reply names the real ones, so the
        # sensors are built from what arrived rather than from what was asked.
        #
        # THE EVENT IS KEPT, NOT JUST THE NAME, and that is the whole fix for
        # a defect that shipped: deebot-client's event bus replays only the
        # LAST event of each TYPE to a new subscriber, and every component
        # shares the one LifeSpanEvent type. A sensor is created when its
        # component's event arrives, subscribes a moment later, and is handed
        # whichever component was most recent by then -- which it correctly
        # filters out, and then sits at `unknown` until that component is
        # reported again. Measured live: 12 of 13 sensors empty.
        self._life_span: dict[LifeSpan, LifeSpanEvent] = {}
        # The saved map and the rooms in it. Two separate reads: the map id
        # comes from GetCachedMapInfo, and GetMapSetV2 needs that id, so
        # neither can be a probe (a probe command takes no arguments).
        self._map_id: str | None = None
        self._rooms: list[Room] = []
        # Set when deebot-client itself parsed the room set. It only manages
        # that for subset widths it knows (10 and 11 fields); this robot
        # sends 12 and falls through. Preferring the library's answer where
        # it has one means the day upstream learns this width, `rooms.py`
        # stops being consulted without anything here changing.
        self._rooms_from_library = False

    # -- lifecycle ----------------------------------------------------------

    @property
    def device(self) -> Device:
        """The robot. Only valid after `async_connect`."""
        if self._device is None:
            msg = "coordinator used before async_connect"
            raise RuntimeError(msg)
        return self._device

    @property
    def states(self) -> Mapping[str, CapabilityState]:
        """The capability map as currently believed."""
        return dict(self._states)

    @property
    def robot_available(self) -> bool:
        """Whether the cloud last reported the robot as reachable."""
        return self._available

    async def async_connect(self) -> None:
        """Authenticate, find the robot, and bring up its MQTT session.

        Exercises REST and then MQTT in the order the integration will use
        them, so a credential or a broker that will not work is a setup
        failure rather than a silent absence of readings later. A setup check
        that exercises a different channel than the one that will be used
        certifies nothing.
        """
        entry = self.config_entry
        data = entry.data
        session = async_get_clientsession(self.hass)
        # Minted by the config flow and persisted there, never derived here:
        # Ecovacs binds its device verification to this id, so a value that
        # moved would ask the owner for an emailed code for ever.
        device_id: str = data[CONF_DEVICE_ID]
        country: str = data[CONF_COUNTRY]

        rest_config = create_rest_config(
            session, device_id=device_id, alpha_2_country=country
        )
        # `password_hash`, not the password -- see config_flow.py for what
        # sending the wrong one looks like from the outside.
        authenticator = Authenticator(
            rest_config, data[CONF_USERNAME], md5(data[CONF_PASSWORD])
        )
        api_client = ApiClient(authenticator)

        try:
            devices = await api_client.get_devices()
        except InvalidAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DeebotError as err:
            raise ConfigEntryNotReady(f"Could not reach the Ecovacs cloud: {err}") from err

        did: str = data[CONF_DID]
        # `not_supported` FIRST and deliberately: that is the list this robot
        # lands in, because its class is in no deebot-client table. `mqtt`
        # is read too so the integration still works if upstream later adds
        # the class -- at which point this robot stops being special and
        # nothing here needs to change.
        api_info = next(
            (d for d in devices.not_supported if d.get("did") == did),
            None,
        ) or next(
            (d.api for d in devices.mqtt if d.api.get("did") == did),
            None,
        )
        if api_info is None:
            raise ConfigEntryNotReady(
                f"The Ecovacs account no longer lists a robot with id {did}"
            )

        static = await load_seed()
        device = Device(DeviceInfo(api_info, static), authenticator)

        # STILL NO `ssl_context` ARGUMENT, DELIBERATELY, AND THIS COST AN
        # OUTAGE. An earlier pass passed one, to keep the context build off
        # the event loop. That change was right about the warning and wrong
        # about the lever.
        #
        # The library's default branch does not just build a context. It also
        # turns OFF hostname checking and certificate verification, because
        # the Ecovacs broker answers 443 with a certificate that does not
        # validate. Handing it a context takes the other branch and skips
        # that, so a context that is merely correct fails verification and the
        # client dies in a five-second reconnect loop -- measured live at 175
        # failures before anyone noticed.
        #
        # WHY NOBODY NOTICED: probe passes go over REST, so capabilities kept
        # answering `ok` and every entity kept a plausible value while push
        # was dead. Stale is not the same as wrong, and a health read cannot
        # tell them apart.
        #
        # WHAT MOVES INSTEAD IS THE CALL SITE. `create_mqtt_config` is wholly
        # synchronous, and the only expensive thing in it is the context
        # build: `ssl.create_default_context()` reads the system CA bundle off
        # disk, which is blocking I/O that Home Assistant detects and names.
        # Running the library's own factory in the executor puts that read on
        # a worker thread and leaves every decision it makes -- which branch,
        # which hostname, what it does to the context afterwards -- exactly
        # where it was. This integration still states no TLS policy of its
        # own, which is the whole reason the `ssl_context` argument is not
        # coming back.
        #
        # `partial`, not a call: `async_add_executor_job(create_mqtt_config(...))`
        # would evaluate the config on the loop and hand the executor a value.
        # tests/test_wiring.py asserts the difference.
        mqtt_config = await self.hass.async_add_executor_job(
            partial(create_mqtt_config, device_id=device_id, country=country)
        )
        mqtt = MqttClient(mqtt_config, authenticator)
        try:
            await device.initialize(mqtt)
        except DeebotError as err:
            await authenticator.teardown()
            raise ConfigEntryNotReady(f"Could not subscribe to the robot: {err}") from err

        device.events.subscribe(AvailabilityEvent, self._on_availability)
        device.events.subscribe(LifeSpanEvent, self._on_life_span)
        device.events.subscribe(CachedMapInfoEvent, self._on_cached_map_info)
        device.events.subscribe(RoomsEvent, self._on_rooms)

        self._authenticator = authenticator
        self._mqtt = mqtt
        self._device = device
        self._states = _restore(await self._store.async_load())

    async def async_shutdown(self) -> None:
        """Tear the cloud session down.

        The unloading flag is set BEFORE teardown so nothing races to log a
        connectivity crossing for a session that is deliberately going away.
        """
        self._unloading = True
        await super().async_shutdown()
        if self._device is not None:
            await self._device.teardown()
        if self._mqtt is not None:
            await self._mqtt.disconnect()
        if self._authenticator is not None:
            await self._authenticator.teardown()

    # -- availability -------------------------------------------------------

    async def _on_availability(self, event: AvailabilityEvent) -> None:
        was_available = self._available
        self._available = event.available
        if was_available is not event.available:
            self._log_condition(
                "robot-unreachable" if not event.available else "robot-reachable",
                "The robot is not answering the Ecovacs cloud"
                if not event.available
                else "The robot is answering again",
            )
        self.async_update_listeners()

    async def _on_life_span(self, event: LifeSpanEvent) -> None:
        first_time = event.type not in self._life_span
        self._life_span[event.type] = event
        if first_time:
            # The platforms add entities on a coordinator update, and this is
            # one in every sense that matters: something became buildable that
            # was not buildable a moment ago.
            self.async_update_listeners()

    # -- the map and its rooms ----------------------------------------------

    async def _on_cached_map_info(self, event: CachedMapInfoEvent) -> None:
        """Remember which saved map the robot is actually using.

        A robot can hold several maps -- this one has four slots and one
        built. Only the one in use can be cleaned by room, so a map id is
        taken from `using` and never from "the first one listed", which on
        this robot is an empty slot.
        """
        in_use = next((m for m in event.maps if m.using), None)
        self._map_id = in_use.id if in_use is not None else None

    async def _on_rooms(self, event: RoomsEvent) -> None:
        """Take the library's own room list when it managed to produce one."""
        self._rooms = sorted(
            (Room(id=room.id, name=room.name.strip()) for room in event.rooms if room.name.strip()),
            key=lambda room: room.id,
        )
        self._rooms_from_library = True

    @property
    def rooms(self) -> tuple[Room, ...]:
        """The rooms of the map currently in use, sorted by id."""
        return tuple(self._rooms)

    async def _refresh_rooms(self) -> None:
        """Read the map's room list. Two calls, both Gets, neither a probe.

        THE ROOM NAMES ARE IN THE FIRST REPLY AND THE LIBRARY CANNOT REACH
        THEM. `GetMapSetV2` returns the rooms as a compressed blob;
        deebot-client decodes it and, for a 10- or 11-field row, dispatches a
        `RoomsEvent` carrying id and name. This robot sends TWELVE fields, so
        that branch does not run and the library instead asks for
        `GetMapSubSet` per room -- which this robot answers `20003 rcp not
        support`, eight times. Measured live, firmware 1.103.0.

        So the blob is decompressed with the library's own helper and the two
        fields upstream already reads are read out of it. `_on_rooms` still
        wins where it fires; see `rooms.py` for why this is one column wider
        rather than a second opinion.
        """
        self._rooms_from_library = False
        result, _ = await GetCachedMapInfo()._execute(  # noqa: SLF001
            self._authenticator, self.device.device_info, self.device.events
        )
        # A FAILED READ IS NOT A READING, and the room list follows the same
        # rule as the capability map: the previous one stands. Emptying it
        # here would mean a single bad minute on the map service silently
        # answered "this house has no rooms" -- which is the shape of mistake
        # this whole integration is built to refuse.
        if result.state is not HandlingState.SUCCESS:
            return
        if self._map_id is None:
            # Read succeeded and says no map is in use. THAT is a reading: a
            # robot that has never finished a mapping run, or whose map was
            # deleted, genuinely has no rooms to offer.
            self._rooms = []
            return

        _, raw = await GetMapSetV2(mid=self._map_id, type=MapSetType.ROOMS)._execute(  # noqa: SLF001
            self._authenticator, self.device.device_info, self.device.events
        )
        if self._rooms_from_library:
            return

        blob = (
            raw.get("resp", {}).get("body", {}).get("data", {}).get("subsets")
            if isinstance(raw, dict)
            else None
        )
        if not isinstance(blob, str) or not blob:
            self._log_condition(
                "rooms-unreadable", "The robot's map carried no readable room list"
            )
            return

        rows = json.loads(decompress_base64_data(blob))
        parsed = parse_rooms(rows)
        if rows and not parsed:
            # Rows arrived and not one was readable -- a subset width this
            # parser does not know. A finding about the PAYLOAD, not about
            # the house, so it keeps the rooms it had and says so once rather
            # than deleting a working room list on a format change.
            self._log_condition(
                "rooms-unparsed",
                f"The robot's room list is in an unrecognised format ({len(rows)} rows)",
            )
            LOGGER.debug("Unparsed room subsets: %s", rows)
            return
        self._rooms = parsed

    @property
    def life_span_components(self) -> frozenset[LifeSpan]:
        """The consumables this robot has actually reported."""
        return frozenset(self._life_span)

    @property
    def life_span_events(self) -> Mapping[str, LifeSpanEvent]:
        """The latest reading per consumable, keyed as the entities key them.

        A sensor added after its own event has already been dispatched reads
        its value from here rather than waiting for the component to be
        reported again.
        """
        return {
            component.name.lower(): event
            for component, event in self._life_span.items()
        }

    @callback
    def _log_condition(self, token: str, message: str) -> None:
        """Log a source condition once at the crossing and once on recovery.

        Gated on Home Assistant reaching RUNNING, never on the readings. The
        registry restores an entity row long before its owner publishes a
        state, so the boot polls truly see nothing and saying so is noise
        about the boot. The gate does NOT record while silent: a condition
        that were marked as already-reported during startup would then never
        log at all, which is worse than the noise.
        """
        if self._unloading or self.hass.state is not CoreState.running:
            return
        if self._logged_condition == token:
            return
        self._logged_condition = token
        LOGGER.info("%s", message)

    # -- the pass -----------------------------------------------------------

    async def _async_update_data(self) -> frozenset[str]:
        """Run one probe pass and publish the resulting usable key set."""
        try:
            result = await self._probe_pass()
        except InvalidAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DeebotError as err:
            raise UpdateFailed(f"Could not reach the Ecovacs cloud: {err}") from err

        if result.void:
            if not self._states:
                # VOID WITH NOTHING TO STAND ON IS A SETUP FAILURE. Later a
                # void pass is the resolver working -- the previous map
                # stands. On the first one there is no previous map, so
                # "entities stay as they were" means no entities at all, and
                # an entry that loaded with nothing reads exactly like a
                # robot that has nothing. Fail, and let Home Assistant retry.
                raise UpdateFailed(f"Nothing measured yet: {result.reason}")
            # Not a failure. The previous map stands, entities stay exactly as
            # they were, and the reason is logged once per condition rather
            # than once per pass.
            self._log_condition(f"void:{result.reason}", f"Probe pass void: {result.reason}")
            return self.data if self.data is not None else usable_keys(self._states)

        self._logged_condition = None
        self._states = dict(result.states)
        await self._store.async_save(_dump(self._states))

        if result.changed:
            for key in result.changed:
                LOGGER.info(
                    "Capability %s is now %s", key, self._states[key].support.value
                )

        usable = usable_keys(self._states)
        if "rooms" in usable:
            # AFTER the resolve, so it runs against this pass's answer rather
            # than the last one's, and inside its own guard: the room list is
            # a convenience and a pass that measured twenty-two capabilities
            # must not be thrown away because the map service had a bad
            # minute. A failure leaves the previous list standing, exactly as
            # a void pass leaves the previous map standing.
            try:
                await self._refresh_rooms()
            except InvalidAuthenticationError:
                raise
            except Exception:  # noqa: BLE001
                self._log_condition(
                    "rooms-unreadable", "Could not read the robot's room list"
                )
                LOGGER.debug("Room refresh failed", exc_info=True)
        return usable

    async def _probe_pass(self) -> PassResult:
        """Probe in seed order, controls first, until the pass runs out of time.

        Truncation is an ordinary outcome and needs no special handling: a key
        not probed is left exactly as it was rather than counted as a miss,
        which is why the order matters -- a pass that ran out of time should
        have spent itself on the probes most likely to resolve something.
        """
        ordered = [
            *CONTROL_KEYS,
            *(k for k in seed_order(self._states, SEED_HYPOTHESIS, PROBED_KEYS) if k not in CONTROL_KEYS),
        ]
        outcomes: dict[str, ProbeOutcome] = {}
        deadline = self.hass.loop.time() + PROBE_PASS_TIMEOUT_SECONDS

        for start in range(0, len(ordered), PROBE_CONCURRENCY):
            if start and self.hass.loop.time() >= deadline:
                LOGGER.debug(
                    "Probe pass truncated after %d of %d probes", start, len(ordered)
                )
                break
            batch = ordered[start : start + PROBE_CONCURRENCY]
            results = await asyncio.gather(*(self._probe_one(key) for key in batch))
            outcomes.update(dict(zip(batch, results, strict=True)))

        return resolve(
            self._states,
            outcomes,
            CONTROL_KEYS,
            miss_threshold=DEFAULT_MISS_THRESHOLD,
        )

    async def _probe_one(self, key: str) -> ProbeOutcome:
        """Send one read command and classify the RAW response.

        THE ONE PRIVATE REACH IN THIS INTEGRATION, AND WHY THERE IS NO PUBLIC
        WAY TO DO IT. `Command.execute()` returns a `DeviceCommandResult`
        whose `raw_response` is populated only when the command SUCCEEDED --
        every failure path returns `DeviceCommandResult(device_reached=False)`
        with an empty dict. The failure path is exactly the one carrying the
        errno this integration has to read: errno 500, the code that means
        "unsupported" and "the network ate it" with equal authority.
        `Command._execute()` returns `(HandlingResult, raw_response)` and is
        the only place both halves are available together.

It is also deliberately NOT `device.execute_command`: that path holds
        the device semaphore and marks the robot available on success. A probe
        must not do the second -- the control decides whether this pass is
        admissible, and a probe that fed the availability signal it is being
        judged against would be marking its own homework.
        """
        assert self._authenticator is not None
        command: Command = build_probe(key)
        try:
            result, response = await command._execute(  # noqa: SLF001
                self._authenticator,
                self.device.device_info,
                self.device.events,
            )
        except InvalidAuthenticationError:
            raise
        except Exception:  # noqa: BLE001
            # Not a miss. A probe that never completed is a statement about
            # this process or this network, not about the robot.
            LOGGER.debug("Probe of %s raised", key, exc_info=True)
            return ProbeOutcome.INCONCLUSIVE

        return classify(response, parsed=result.state is HandlingState.SUCCESS)

    # -- for the platforms --------------------------------------------------

    @property
    def usable(self) -> frozenset[str]:
        """Capability keys that may build entities right now."""
        return self.data if self.data is not None else frozenset()


#: The entry carries its coordinator in `runtime_data`; nothing of this
#: integration's lives in `hass.data`. Declared AFTER the class so the
#: subscript is a real class rather than a forward reference, and written as
#: a plain assignment rather than a `type` statement so that this module
#: still PARSES on an interpreter older than the one it runs on -- the
#: wiring suite reads every module with `ast`, and a file it cannot parse is
#: a file it cannot check.
DeebotConfigEntry = ConfigEntry[DeebotCoordinator]
