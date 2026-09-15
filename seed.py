"""The seed hypothesis, and the read command behind every probed capability.

This is the only module that knows what a deebot-client command is called.
It imports `deebot_client` and nothing from `homeassistant`.

WHAT A SEED IS FOR, ONCE MORE, BECAUSE IT IS THE EASY THING TO GET WRONG.
`deebot_client.hardware` carries a per-model capability table, and four class
strings for the DEEBOT T90 PRO OMNI resolve to modules that are BYTE-IDENTICAL
in 18.5.1 -- one capability set with four names on it. The robot this was
written for is a retail variant of that same hardware whose class string is in
none of them, so the table is the right SHAPE and has no row.

So the sibling's table is taken for two things and refused for a third:

  1. It supplies the `Capabilities` object deebot-client needs to construct a
     `Device` at all. Nothing else will do -- the dataclass has required
     fields, and there is no partial form of it. This buys a working protocol
     client, not a belief about the robot.
  2. It says which keys to probe FIRST (`discovery.SEED_HYPOTHESIS`).
  3. It does NOT say which entities exist. Not one entity in this integration
     is built because the sibling's table declares something; every one of
     them is built because a probe answered, or because
     `discovery.CAPABILITIES` records a measured antecedent for it.

If that distinction is ever collapsed, this integration becomes the thing it
was written to replace, and it will be wrong in exactly the way upstream is
wrong here -- confidently, and about a robot nobody measured.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from deebot_client.commands.json.auto_empty import GetAutoEmpty
from deebot_client.commands.json.battery import GetBattery
from deebot_client.commands.json.charge_state import GetChargeState
from deebot_client.commands.json.child_lock import GetChildLock
from deebot_client.commands.json.clean import CleanAreaV2, CleanMode, CleanV2
from deebot_client.commands.json.clean_count import GetCleanCount
from deebot_client.commands.json.clean_logs import GetCleanLogs
from deebot_client.commands.json.continuous_cleaning import GetContinuousCleaning
from deebot_client.commands.json.error import GetError
from deebot_client.commands.json.fan_speed import GetFanSpeed
from deebot_client.commands.json.life_span import GetLifeSpan
from deebot_client.commands.json.map import GetCachedMapInfo
from deebot_client.commands.json.mop_auto_wash_frequency import GetMopAutoWashFrequency
from deebot_client.commands.json.network import GetNetInfo
from deebot_client.commands.json.ota import GetOta
from deebot_client.commands.json.stats import GetStats, GetTotalStats
from deebot_client.commands.json.sweep_mode import GetSweepMode
from deebot_client.commands.json.true_detect import GetTrueDetect
from deebot_client.commands.json.voice_assistant_state import GetVoiceAssistantState
from deebot_client.commands.json.volume import GetVolume
from deebot_client.commands.json.water_info import GetWaterInfo
from deebot_client.commands.json.work_mode import GetWorkMode
from deebot_client.capabilities import CapabilityCleanAction
from deebot_client.events import LifeSpan
from deebot_client.hardware import get_static_device_info

from .discovery import PROBED_KEYS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from deebot_client.command import Command
    from deebot_client.models import StaticDeviceInfo

__all__ = ["CLEAN_AREA_MODE", "SEED_CLASS", "build_probe", "load_seed"]

#: The sibling class whose table is borrowed. `k2iwaq`, `qhh2k5`, `rx6f4s` and
#: `twunby` are all DEEBOT T90 PRO OMNI and their modules are byte-identical in
#: deebot-client 18.5.1 -- measured, not assumed -- so which of the four is
#: named here changes nothing. It is pinned rather than searched because a
#: search over 248 modules for "one that looks close" is a guess with a loop
#: around it.
SEED_CLASS: Final = "rx6f4s"

#: The `content.type` this robot answers to for a per-area clean.
#:
#: MEASURED ONE TOKEN AT A TIME AGAINST THE ROBOT (#27), because `clean_V2`
#: reads its target off `content.type` and this firmware's vocabulary is not
#: the one deebot-client assumes. Swept 2026-09-15 on firmware 1.103.0, every
#: candidate sent with a room id the map does not carry:
#:
#:     freeClean    -> code 0, ok                 <- this one
#:     entrust      -> code 0, ok                 (delegated, not per-area)
#:     spot         -> code 1000, a C++ range check over `content.value`
#:     mapPoint     -> code 1000, the same        (both take coordinates)
#:     spotArea     -> code 20011, unknown type
#:     customArea   -> code 20011, unknown type
#:     area, room, rooms, spotarea, selectArea, zone, border, singleRoom,
#:     subArea, mapArea                           -> all 20011, unknown type
#:
#: `20011 unknown type` is this firmware saying it has no such mode, and it is
#: what `CleanMode.SPOT_AREA` -- deebot-client's own default for an area
#: clean, and what this integration sent until now -- gets every time.
#:
#: WHY IT LIVES HERE AND NOT AT THE CALL SITE. The mode is half of the wire
#: form; the command class is the other half, and `_with_v2_clean` below
#: already owns that half. Splitting them put `CleanMode.SPOT_AREA` in
#: `vacuum.py` and `button.py` as a literal nobody had measured, which is the
#: same shape of mistake as the hardcoded `CleanArea` those two call sites
#: just stopped carrying. One module knows what a command is called.
#:
#: IT ALSO MAKES `cleanings` REAL, which is a second bug closing quietly.
#: `CleanAreaV2` prefixes the pass count to `content.value` for FREE_CLEAN
#: ALONE and drops it silently for every other mode, so `clean_rooms` has
#: advertised a `cleanings` parameter it could not honour for as long as it
#: sent SPOT_AREA.
CLEAN_AREA_MODE: Final = CleanMode.FREE_CLEAN

#: The consumables the life-span probe asks about. This is a QUESTION, not a
#: claim: the response names which of them this robot actually reports, and
#: the sensors are built from that answer rather than from this list. Asking
#: about a component the robot does not have costs one absent row in the
#: reply, which is the cheapest possible way to find out.
_LIFE_SPAN_QUERY: Final = (
    LifeSpan.BRUSH,
    LifeSpan.CLEANING_SOLUTION,
    LifeSpan.DUST_BAG,
    LifeSpan.DUST_BUCKET,
    LifeSpan.DUST_CONTAINER_FILTER,
    LifeSpan.FILTER,
    LifeSpan.HEAVY_DUTY_CLEANING_SOLUTION,
    LifeSpan.MOP_WASHING_TRAY,
    LifeSpan.ROUND_MOP,
    LifeSpan.SIDE_BRUSH,
    LifeSpan.STRAINER,
    LifeSpan.UNIT_CARE,
)


#: Key -> the READ command that probes it. Every entry is a factory rather
#: than an instance so one pass cannot hand a mutated command to the next.
#:
#: EVERY COMMAND IN HERE IS A `Get`. That is the rule the catalogue's
#: probed/implied split exists to keep: an action cannot appear here, because
#: sending it to find out whether it works IS running it.
_PROBES: Final[Mapping[str, Callable[[], Command]]] = {
    "battery": GetBattery,
    # GetChargeState rather than GetWorkState: both feed the state event, and
    # only this one is specific to the robot. GetWorkState answers for the
    # station too, so it cannot tell a robot with a station from one without
    # -- see `station_state` in discovery.py.
    "state": GetChargeState,
    "error": GetError,
    "fan_speed": GetFanSpeed,
    "water": GetWaterInfo,
    "life_span": lambda: GetLifeSpan(list(_LIFE_SPAN_QUERY)),
    "stats_clean": GetStats,
    "stats_total": GetTotalStats,
    "clean_log": GetCleanLogs,
    "network": GetNetInfo,
    "volume": GetVolume,
    "child_lock": GetChildLock,
    "true_detect": GetTrueDetect,
    "sweep_mode": GetSweepMode,
    "ota": GetOta,
    "voice_assistant": GetVoiceAssistantState,
    "continuous_cleaning": GetContinuousCleaning,
    "clean_count": GetCleanCount,
    "work_mode": GetWorkMode,
    "auto_empty": GetAutoEmpty,
    "mop_auto_wash_frequency": GetMopAutoWashFrequency,
    # The map, not the rooms. `GetMapSetV2` is what lists rooms and it needs
    # the map's id, which is runtime state -- and every entry here is a
    # zero-argument factory on purpose, so one pass cannot hand a mutated
    # command to the next. `GetCachedMapInfo` takes nothing, is a Get, and
    # answers the question this key is actually gating: does this robot keep
    # a map to have rooms in. The coordinator does the second call.
    "rooms": GetCachedMapInfo,
}


def _validate() -> None:
    """Every probed key has a command, and every command a probed key.

    At import, so a catalogue row added without its command cannot reach a
    running instance as a capability that is silently never probed -- which
    would read as UNKNOWN for ever and look exactly like a robot that does
    not have it.
    """
    catalogue = set(PROBED_KEYS)
    commands = set(_PROBES)
    if missing := sorted(catalogue - commands):
        msg = f"probed capabilities with no command: {', '.join(missing)}"
        raise ValueError(msg)
    if extra := sorted(commands - catalogue):
        msg = f"probe commands for no catalogue capability: {', '.join(extra)}"
        raise ValueError(msg)


_validate()


def build_probe(key: str) -> Command:
    """Build the read command that probes `key`.

    The returned command is marked as an availability check. That flag is
    deebot-client's own, and it changes exactly one thing: the errno-500
    branch logs at INFO instead of WARNING. It belongs here because it is
    true -- a probe IS an availability question, and the answer it is hunting
    for is the failure. Without it, a capability this robot genuinely does not
    have emits a warning on every pass, for ever, telling the owner about a
    condition no edit of theirs can fix. Log level splits on who acts.
    """
    command = _PROBES[key]()
    command._is_available_check = True  # noqa: SLF001
    return command


async def load_seed() -> StaticDeviceInfo:
    """Resolve the sibling model's table.

    Raises `LookupError` when deebot-client no longer carries the class. That
    is a real possibility -- the tables are the part of that library that
    moves -- and it must fail loudly at setup rather than quietly leaving a
    robot with no capabilities, which is indistinguishable from the upstream
    bug this integration exists to route around.
    """
    static = await get_static_device_info(SEED_CLASS)
    if static is None:
        msg = (
            f"deebot-client no longer carries the seed class {SEED_CLASS!r}; "
            "the sibling table this integration borrows its protocol client "
            "from is gone and a new one must be chosen"
        )
        raise LookupError(msg)
    return _with_v2_clean(static)


def _with_v2_clean(static: StaticDeviceInfo) -> StaticDeviceInfo:
    """Swap the borrowed table's clean commands for their `_V2` forms.

    MEASURED, NOT ASSUMED, AND IT COST A NIGHT'S CLEAN TO FIND OUT. The seed
    table wires `clean.action` to `Clean`/`CleanArea`, whose wire name is
    `clean`. On this robot every one of those answers
    `{'code': 20003, 'msg': 'rcp not support'}` -- the same refusal it gives
    `getMapSubSet`, and the same code the probe treats as "does not support
    the command" everywhere else. So `clean_rooms`, every room button and
    `vacuum.start`/`pause`/`stop` all failed, silently, until one of them was
    finally sent.

    WHY THIS IS AN OVERRIDE AND NOT A PROBE. `clean_action` is the one
    capability the catalogue cannot measure: every command it carries would
    send the robot out cleaning, so `discovery.py` implies it from `state`
    rather than probing it. That implication is sound about WHETHER the robot
    takes a clean order -- it answered `GetChargeState`, so it has the state
    machine the order drives -- and says nothing about which command NAME
    carries it. This fills exactly that gap and nothing else.

    WHY NOT CHANGE `SEED_CLASS` INSTEAD, which would be one constant: no `_V2`
    table in deebot-client is a superset of this one. Measured over all 106 of
    them, the closest miss either `voice_assistant` or
    `mop_auto_wash_frequency`, and this robot reports both -- so borrowing a
    `_V2` table wholesale would delete a working entity to fix an unrelated
    command. The narrow swap keeps every probed capability exactly as it was.

    The rest of the table is left alone deliberately. `state` also carries
    `GetWorkState`, which the `_V2` tables replace with `GetCleanInfoV2`;
    whether this robot refuses that one too is unmeasured, and swapping it on
    the strength of this finding would be the second guess the catalogue
    refuses to laundering through the first.
    """
    capabilities = static.capabilities
    return replace(
        static,
        capabilities=replace(
            capabilities,
            clean=replace(
                capabilities.clean,
                action=CapabilityCleanAction(command=CleanV2, area=CleanAreaV2),
            ),
        ),
    )
