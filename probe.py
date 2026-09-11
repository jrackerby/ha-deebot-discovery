"""Resolve a robot's capability map from probe results.

IMPORTS NOTHING FROM `homeassistant` AND NOTHING FROM `deebot_client` --
the same contract `jrackerby/household-state`'s resolver keeps, and for the
same reason: this is the part where being wrong is expensive, so it has to be
exercisable on a plain python3 with no HA, no cloud account, and no robot.

WHAT MAKES THIS HARD. The robot does not publish what it supports; there is
no `getSupportedFunctions` anywhere in the protocol, which is precisely why
upstream maintains 55k lines of hand-written per-model tables. So support
must be inferred from whether a command answers -- and the failure code that
means "not supported" is the SAME code that means "the network dropped it"
(errno 500). The discipline that answers it: name the benign state that
produces the same output, then measure a known instance of it. That is what
the control is for.

THE THREE RULES, and each exists because of a specific way this can lie:

  1. EVERY PASS CARRIES A CONTROL -- a command this robot has already
     answered. If the control does not answer, the pass says nothing about
     anything and is VOID: the previous map stands untouched. Without this,
     one bad minute of wifi silently strips every capability at once, and
     the stripped map looks exactly like a correct reading of a robot that
     lost its features. A monitor whose blind spot correlates with what it
     monitors is worse than none.
  2. A PASS WITH NO CONTROL IS ALSO VOID, not merely unguarded. A sweep that
     cannot assert its own completeness is not evidence.
  3. PROMOTE ON ONE OK, DEMOTE ONLY ON `miss_threshold` CONSECUTIVE MISSES
     ACROSS VOID-FREE PASSES. Fall dwell, never rise dwell.
  4. AN OUTCOME NOBODY HAS CLASSIFIED IS NOT A MISS. Exactly one failure
     code -- errno 500 -- carries the "or does not support the command"
     reading; everything else the cloud can answer says nothing about the
     capability, so INCONCLUSIVE leaves the belief and the streak untouched
     rather than spending one of the three misses a demotion costs. `ok at
     zero` and `could not read` are different values at the source.

WHAT IT DELIBERATELY DOES NOT DO: it never invents a capability it has not
seen answer. A seed hypothesis borrowed from a sibling model marks a key as
worth probing FIRST; it never marks it SUPPORTED. Seeding the belief instead
of the probe order is how a near-neighbour table's mistakes become ours, and
upstream already has an open report of a recognized T90 whose declared
actions do not work.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .const import DEFAULT_MISS_THRESHOLD, ProbeOutcome, Support

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

__all__ = ["CapabilityState", "PassResult", "resolve", "seed_order"]

#: Outcomes that prove the command exists. UNPARSED counts: the robot
#: answered, so the capability is there and only our reader is behind.
_PROVES_PRESENT = frozenset({ProbeOutcome.OK, ProbeOutcome.UNPARSED})

#: Outcomes that are not evidence either way. Rule 4. Kept as a set rather
#: than an `is` test so that a future outcome added to const.py has one
#: obvious place to be classified, and so that the two sets can be asserted
#: disjoint by the suite.
_NO_EVIDENCE = frozenset({ProbeOutcome.INCONCLUSIVE})


@dataclass(frozen=True)
class CapabilityState:
    """What we believe about one capability, and how sure we are."""

    support: Support = Support.UNKNOWN
    #: Consecutive misses across VOID-free passes. Reset by any answer.
    misses: int = 0
    #: The last outcome that COUNTED for this key. None until one has.
    #:
    #: It exists to make one designed-for state visible. UNPARSED and OK both
    #: mean SUPPORTED, and they mean different things to whoever is looking:
    #: "this works" versus "the robot answered and our reader is behind, so
    #: the entity is real and empty". Without this the two are the same row
    #: in the map and the second is indistinguishable from a bug.
    #:
    #: An INCONCLUSIVE pass does not write it, for the same reason it writes
    #: nothing else: rule 4 means that outcome is not evidence about the
    #: capability, and recording it would make the newest unclassified error
    #: overwrite the last real reading.
    last_outcome: ProbeOutcome | None = None

    @property
    def usable(self) -> bool:
        """Whether an entity should be built for this capability."""
        return self.support is Support.SUPPORTED


@dataclass(frozen=True)
class PassResult:
    """The outcome of one discovery pass."""

    states: Mapping[str, CapabilityState]
    void: bool = False
    #: Why the pass was void, in words a log line can use directly. None when
    #: the pass counted.
    reason: str | None = None
    #: Keys whose `support` changed value in this pass. Empty on a void pass
    #: by construction -- a void pass changes nothing.
    changed: tuple[str, ...] = ()


def resolve(
    previous: Mapping[str, CapabilityState],
    outcomes: Mapping[str, ProbeOutcome],
    controls: Collection[str],
    *,
    miss_threshold: int = DEFAULT_MISS_THRESHOLD,
) -> PassResult:
    """Fold one pass of probe outcomes into the capability map.

    `previous` is last known state; `outcomes` is what this pass observed,
    which need not cover every key -- a key not probed is left exactly as it
    was rather than being treated as a miss. `controls` names the probes
    whose success makes the pass admissible at all.
    """
    states = dict(previous)

    # Rule 2: no control, no evidence. Checked before the control outcomes so
    # that an empty `controls` cannot pass vacuously through an all() below --
    # `all([])` is True, and that is exactly how a gate stops gating.
    if not controls:
        return PassResult(states, void=True, reason="pass carried no control probe")

    missing = [key for key in controls if key not in outcomes]
    if missing:
        return PassResult(
            states,
            void=True,
            reason=f"control probe(s) not attempted: {', '.join(sorted(missing))}",
        )

    # Rule 1: the control must have answered. OFFLINE is called out separately
    # from a plain miss because it is the one outcome that is unambiguous --
    # it says the robot is not there, which is a fact worth logging as itself
    # rather than as "the control failed".
    for key in sorted(controls):
        outcome = outcomes[key]
        if outcome not in _PROVES_PRESENT:
            why = (
                "robot reported offline"
                if outcome is ProbeOutcome.OFFLINE
                else f"control probe {key!r} returned {outcome.value}"
            )
            return PassResult(states, void=True, reason=why)

    changed: list[str] = []
    for key, outcome in outcomes.items():
        # Rule 4, before anything reads or writes `states`: an unclassified
        # answer must not create a CapabilityState for a key that has never
        # been seen either, or "we have not looked" acquires a miss count.
        if outcome in _NO_EVIDENCE:
            continue

        before = states.get(key, CapabilityState())

        if outcome in _PROVES_PRESENT:
            after = CapabilityState(Support.SUPPORTED, 0, outcome)
        else:
            misses = before.misses + 1
            # Rule 3. Below the threshold the belief does not move at all --
            # not to UNSUPPORTED, and not to UNKNOWN either. A capability
            # mid-demotion is still usable, which is the point of the dwell:
            # the entity keeps working through a flaky patch.
            support = (
                Support.UNSUPPORTED if misses >= miss_threshold else before.support
            )
            after = replace(
                before, support=support, misses=misses, last_outcome=outcome
            )

        states[key] = after
        if after.support is not before.support:
            changed.append(key)

    return PassResult(states, void=False, changed=tuple(sorted(changed)))


def seed_order(
    known: Mapping[str, CapabilityState],
    hypothesis: Collection[str],
    candidates: Collection[str],
) -> tuple[str, ...]:
    """Order the probe set: most-likely-to-answer first.

    `hypothesis` is what a sibling model's table declares. It buys probe
    ORDER and nothing else -- see the module docstring on why it must never
    buy belief. Ordering matters because a pass is bounded by time and the
    cloud's patience, so a truncated pass should have spent itself on the
    probes most likely to resolve something.

    Already-settled keys go last rather than being dropped: a firmware update
    can add a function, and a set that only ever shrinks would never see it.
    """
    ranked = sorted(candidates)
    unknown_hypothesis = [
        key
        for key in ranked
        if key in hypothesis and known.get(key, CapabilityState()).support is Support.UNKNOWN
    ]
    unknown_rest = [
        key
        for key in ranked
        if key not in hypothesis and known.get(key, CapabilityState()).support is Support.UNKNOWN
    ]
    settled = [key for key in ranked if key not in unknown_hypothesis and key not in unknown_rest]
    return tuple(unknown_hypothesis + unknown_rest + settled)
