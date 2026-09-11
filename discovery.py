"""The capability catalogue: what may be probed, and what may be inferred.

IMPORTS NOTHING FROM `homeassistant` AND NOTHING FROM `deebot_client`. The
keys in here are the vocabulary everything else speaks -- the probe map is
keyed on them, the stored ledger is keyed on them, and every platform decides
whether to build an entity by asking about one -- so they are defined where a
plain python3 can exercise the rules about them.

TWO KINDS OF CAPABILITY, AND THE LINE BETWEEN THEM IS THE WHOLE POINT.

PROBED. The vendor protocol has a read command for it. Sending that command
costs nothing and moves nothing: the robot either answers or it does not, and
`classify` turns the answer into evidence. Every probed capability earns its
belief the same way, and none of them is ever believed on the strength of a
table.

IMPLIED. The capability's only command is an ACTION -- `Charge` drives the
robot to the dock, `PlaySound` makes it beep, `StationAction` runs a mop wash.
There is no read to send, and probing by doing is not probing: it is operating
someone's robot at 3am to find out whether it can be operated. So an implied
capability is offered only when a PROBED capability that requires the same
hardware has answered, and the implication carries its reason in the row.

This is deliberately NOT the seed hypothesis wearing a different hat, and the
difference is the one this integration exists to hold:

  * A SEED buys probe ORDER and nothing else. It says a sibling model's table
    declares this key, so try it early. It never grants belief, because the
    sibling's table is exactly the artefact that was wrong about this robot.
  * An IMPLICATION buys BELIEF, and it is only allowed to because its
    antecedent was MEASURED on this robot, this session. `station_action` is
    offered because `auto_empty` -- a station-only read -- answered here, not
    because some table says a T90 has a station.

An implication whose antecedent is itself implied would launder a guess
through a second guess, so the rules below refuse one, and the suite proves
they refuse it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .const import Support

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .probe import CapabilityState

__all__ = [
    "CAPABILITIES",
    "CONTROL_KEYS",
    "Capability",
    "PROBED_KEYS",
    "SEED_HYPOTHESIS",
    "usable_keys",
]


@dataclass(frozen=True, kw_only=True)
class Capability:
    """One row of the catalogue."""

    #: The stable key. It reaches the stored ledger and must not be renamed
    #: without a storage migration -- a renamed key reads as a capability
    #: never probed, which silently deletes every entity behind it.
    key: str

    #: Why this key exists, in the words a diagnostics dump can print.
    description: str

    #: True when the vendor protocol has a READ command for this capability,
    #: i.e. when it can be measured. False means it is inferred from
    #: `implied_by`.
    probed: bool = True

    #: The PROBED key whose support grants this one. Required when
    #: `probed` is False, forbidden otherwise.
    implied_by: str | None = None

    #: Why the implication holds. A stated reason is the difference between
    #: an inference and a guess, and this field is what makes an unreasoned
    #: one impossible to add quietly.
    reason: str | None = None

    #: Whether the four byte-identical DEEBOT T90 PRO OMNI modules in
    #: deebot-client declare this capability. ORDER ONLY -- see the module
    #: docstring. Meaningless on an implied row.
    seeded: bool = True


#: The control probe. Its success is what makes a pass admissible at all, so
#: it is the one capability whose absence is not a finding about the robot but
#: a finding about the pass. `GetBattery` is deebot-client's own availability
#: check, which is the strongest argument for using it: if it does not answer,
#: the library would call the robot unreachable too.
CONTROL_KEYS: Final = ("battery",)


CAPABILITIES: Final[tuple[Capability, ...]] = (
    # -- probed: the read commands ------------------------------------------
    Capability(
        key="battery",
        description="Battery level. Also the control probe for every pass.",
    ),
    Capability(
        key="state",
        description="Charge/work state -- the robot's state machine.",
    ),
    Capability(key="error", description="Current error code."),
    Capability(key="fan_speed", description="Suction level."),
    Capability(
        key="water",
        description=(
            "Water box: custom amount and whether a mop is attached. ONE key "
            "for both, because one command (GetWaterInfo) answers for both "
            "and a single probe cannot tell them apart. Two keys here would "
            "report two independent measurements where there is one."
        ),
    ),
    Capability(key="life_span", description="Consumable wear, per component."),
    Capability(key="stats_clean", description="Statistics for the current job."),
    Capability(key="stats_total", description="Lifetime statistics."),
    Capability(
        key="clean_log",
        description=(
            "History of finished jobs. NOT A ROBOT CAPABILITY: GetCleanLogs "
            "sets `_targets_bot = False` and queries the cloud's own log "
            "service, so this probe answers for Ecovacs rather than for the "
            "hardware and can never miss because a robot lacks the feature. "
            "It still gates the right entity -- that entity reads exactly "
            "this endpoint -- but an EMPTY result is normal and is not a "
            "fault: measured live, the service returned no rows for a robot "
            "with 27 recorded cleanings, and deebot-client's own comment on "
            "the handler says the API is changing."
        ),
    ),
    Capability(key="network", description="Wifi signal and addresses."),
    Capability(key="volume", description="Speaker volume."),
    Capability(key="child_lock", description="Child lock."),
    Capability(key="true_detect", description="TrueDetect obstacle avoidance."),
    Capability(key="sweep_mode", description="Mopping sweep (ZigZag) mode."),
    Capability(key="ota", description="Automatic firmware updates."),
    Capability(key="voice_assistant", description="Voice assistant / YIKO."),
    Capability(key="continuous_cleaning", description="Resume after charging."),
    Capability(key="clean_count", description="Passes per clean."),
    Capability(key="work_mode", description="Vacuum / mop / both."),
    Capability(
        key="auto_empty",
        description=(
            "Station auto-empty frequency. STATION-ONLY: a robot with no "
            "station has nothing to answer this with, which is why it is the "
            "discriminator every station capability below hangs off."
        ),
    ),
    Capability(key="mop_auto_wash_frequency", description="Minutes between mop washes."),
    # -- implied: the action-only capabilities -------------------------------
    Capability(
        key="clean_action",
        description="Start, pause, resume and stop a clean.",
        probed=False,
        implied_by="state",
        reason=(
            "The only commands are actions that would send the robot out "
            "cleaning, so there is nothing safe to probe. `state` answered, "
            "so the robot has the state machine these actions drive, and "
            "Home Assistant models them as supported_features of the one "
            "vacuum entity that state built -- issuing one is the user's "
            "deliberate act, never a probe's."
        ),
    ),
    Capability(
        key="charge",
        description="Send the robot back to the dock.",
        probed=False,
        implied_by="state",
        reason=(
            "`Charge` is an action; probing it would drive the robot home. "
            "The `state` probe is GetChargeState, so an answer proves the "
            "robot models a charge state, and `Charge` is the action against "
            "exactly that state."
        ),
    ),
    Capability(
        key="play_sound",
        description="Make the robot beep so it can be found.",
        probed=False,
        implied_by="volume",
        reason=(
            "There is no read for the sound itself and probing it would make "
            "the robot beep -- at whatever hour the pass runs. `GetVolume` "
            "answering proves the speaker `PlaySound` needs, on this robot, "
            "this session."
        ),
    ),
    Capability(
        key="station_action",
        description="Empty the dustbin, wash the mop, dry the mop.",
        probed=False,
        implied_by="auto_empty",
        reason=(
            "Every one of these is an action against the station, and "
            "running one to find out whether it can be run is not a probe. "
            "`GetAutoEmpty` is a station-only read, so an answer establishes "
            "that a station is attached -- the one fact these actions need."
        ),
    ),
    Capability(
        key="station_state",
        description="What the station is doing.",
        probed=False,
        implied_by="auto_empty",
        reason=(
            "deebot-client reads station state with GetWorkState, the same "
            "command behind `state`, so that probe cannot tell a robot with "
            "a station from one without. `auto_empty` can, so the station's "
            "own reading hangs off it rather than off a probe that would "
            "answer either way."
        ),
    ),
)


def _index() -> Mapping[str, Capability]:
    by_key: dict[str, Capability] = {}
    for capability in CAPABILITIES:
        if capability.key in by_key:
            msg = f"duplicate capability key: {capability.key!r}"
            raise ValueError(msg)
        by_key[capability.key] = capability
    return by_key


BY_KEY: Final[Mapping[str, Capability]] = _index()

PROBED_KEYS: Final[tuple[str, ...]] = tuple(
    c.key for c in CAPABILITIES if c.probed
)
IMPLIED_KEYS: Final[tuple[str, ...]] = tuple(
    c.key for c in CAPABILITIES if not c.probed
)

#: What the sibling T90 PRO OMNI table declares. PROBE ORDER ONLY.
SEED_HYPOTHESIS: Final[frozenset[str]] = frozenset(
    c.key for c in CAPABILITIES if c.probed and c.seeded
)


def _validate() -> None:
    """Refuse a catalogue that could launder a guess into a belief.

    Run at import, so a malformed row cannot reach a running instance: the
    integration fails to load loudly rather than building entities for a
    capability nobody measured.
    """
    for capability in CAPABILITIES:
        if capability.probed:
            if capability.implied_by is not None or capability.reason is not None:
                msg = (
                    f"{capability.key!r} is probed, so it must not also claim "
                    "an implication"
                )
                raise ValueError(msg)
            continue

        if not capability.implied_by:
            msg = f"{capability.key!r} is not probed and names no antecedent"
            raise ValueError(msg)
        if not capability.reason:
            msg = (
                f"{capability.key!r} claims an implication with no reason -- a "
                "stated reason is what makes it an inference rather than a guess"
            )
            raise ValueError(msg)

        antecedent = BY_KEY.get(capability.implied_by)
        if antecedent is None:
            msg = (
                f"{capability.key!r} is implied by {capability.implied_by!r}, "
                "which is not a capability"
            )
            raise ValueError(msg)
        if not antecedent.probed:
            msg = (
                f"{capability.key!r} is implied by {capability.implied_by!r}, "
                "which is itself implied -- that is a guess laundered through "
                "a second guess"
            )
            raise ValueError(msg)

    for key in CONTROL_KEYS:
        control = BY_KEY.get(key)
        if control is None or not control.probed:
            msg = f"control key {key!r} is not a probed capability"
            raise ValueError(msg)


_validate()


def usable_keys(states: Mapping[str, CapabilityState]) -> frozenset[str]:
    """Which capabilities may build entities, given what is believed.

    A probed key is usable when it is SUPPORTED -- never when it is UNKNOWN,
    which is "we have not looked", and never when it is UNSUPPORTED. An
    implied key is usable when its antecedent is.
    """
    usable = {
        capability.key
        for capability in CAPABILITIES
        if capability.probed
        and (state := states.get(capability.key)) is not None
        and state.support is Support.SUPPORTED
    }
    usable.update(
        capability.key
        for capability in CAPABILITIES
        if not capability.probed and capability.implied_by in usable
    )
    return frozenset(usable)


def describe(keys: Iterable[str]) -> dict[str, str]:
    """Key -> description, for a diagnostics dump."""
    return {key: BY_KEY[key].description for key in sorted(keys) if key in BY_KEY}
