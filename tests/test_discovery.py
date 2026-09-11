#!/usr/bin/env python3
"""Exercise the capability catalogue and the rules it enforces.

WHAT IS AT STAKE HERE. `usable_keys` decides which entities exist. Every way
it can be wrong produces a plausible-looking integration:

  * If UNKNOWN were treated as usable, every capability would get an entity
    at first setup and the robot would look exactly like one described by a
    per-model table -- which is the artefact this repo exists to replace.
  * If an implication could hang off another implication, one measured fact
    could be spun into a chain of unmeasured ones, and the chain would read
    in diagnostics exactly like evidence.
  * If a catalogue row could claim an implication with no reason, the next
    person to read it could not tell an inference from a guess.

The validator runs at import, so a malformed row fails the integration's
load rather than reaching a running instance. That makes these cases checks
on a GATE, not on a convention.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load, run_suite  # noqa: E402

const, probe, discovery = load("const", "probe", "discovery")
SUPPORTED, UNSUPPORTED, UNKNOWN = (
    const.Support.SUPPORTED,
    const.Support.UNSUPPORTED,
    const.Support.UNKNOWN,
)
State = probe.CapabilityState
Capability = discovery.Capability


def _states(**kwargs):
    return {key: State(value, 0) for key, value in kwargs.items()}


def _refuses(*capabilities):
    """Run the catalogue validator over a hand-built set; report the failure.

    Reaches the module-level validator through a swap rather than a copy: a
    second implementation in the suite would pass while the real one rotted.
    """
    real = discovery.CAPABILITIES
    real_index = discovery.BY_KEY
    try:
        discovery.CAPABILITIES = tuple(capabilities)
        discovery.BY_KEY = {c.key: c for c in capabilities}
        discovery._validate()
    except ValueError as err:
        return {"refused": True, "why": type(err).__name__, "message": str(err)}
    else:
        return {"refused": False, "why": None, "message": ""}
    finally:
        discovery.CAPABILITIES = real
        discovery.BY_KEY = real_index


# --- cases -----------------------------------------------------------------

def case_unknown_is_not_usable():
    # "We have not looked" must not build an entity. This is the case that
    # separates this integration from a lookup table.
    usable = discovery.usable_keys(_states(battery=UNKNOWN, state=UNKNOWN))
    return {"battery": "battery" in usable, "state": "state" in usable}


def case_unsupported_is_not_usable():
    usable = discovery.usable_keys(_states(battery=UNSUPPORTED))
    return {"battery": "battery" in usable}


def case_absent_key_is_not_usable():
    return {"battery": "battery" in discovery.usable_keys({})}


def case_supported_is_usable():
    usable = discovery.usable_keys(_states(battery=SUPPORTED))
    return {"battery": "battery" in usable}


def case_implication_follows_its_antecedent():
    # `state` measured -> the vacuum's actions are offered. Nothing else is.
    usable = discovery.usable_keys(_states(state=SUPPORTED))
    return {
        "clean_action": "clean_action" in usable,
        "charge": "charge" in usable,
        "station_action": "station_action" in usable,
        "play_sound": "play_sound" in usable,
    }


def case_station_hangs_off_the_station_only_read():
    # The discriminator that matters: a robot with no dock answers `state`
    # and cannot answer `auto_empty`, so the station actions must not appear.
    without = discovery.usable_keys(_states(state=SUPPORTED, auto_empty=UNSUPPORTED))
    with_station = discovery.usable_keys(_states(state=SUPPORTED, auto_empty=SUPPORTED))
    return {
        "without": "station_action" in without,
        "without_state": "station_state" in without,
        "with": "station_action" in with_station,
        "with_state": "station_state" in with_station,
    }


def case_implication_dies_with_its_antecedent():
    usable = discovery.usable_keys(_states(state=UNSUPPORTED))
    return {"clean_action": "clean_action" in usable, "charge": "charge" in usable}


def case_every_implied_key_names_a_probed_antecedent():
    offenders = [
        c.key
        for c in discovery.CAPABILITIES
        if not c.probed
        and (
            c.implied_by not in discovery.BY_KEY
            or not discovery.BY_KEY[c.implied_by].probed
        )
    ]
    return {"offenders": offenders}


def case_every_implied_key_states_a_reason():
    return {
        "unreasoned": [
            c.key for c in discovery.CAPABILITIES if not c.probed and not c.reason
        ]
    }


def case_control_is_a_probed_capability():
    return {
        "controls": list(discovery.CONTROL_KEYS),
        "all_probed": all(
            key in discovery.BY_KEY and discovery.BY_KEY[key].probed
            for key in discovery.CONTROL_KEYS
        ),
    }


def case_seed_never_reaches_an_implied_key():
    # A seed is probe ORDER. An implied key in the hypothesis would be
    # meaningless at best -- nothing probes it -- and at worst would read as
    # a table granting belief, which is the failure this repo is about.
    implied = {c.key for c in discovery.CAPABILITIES if not c.probed}
    return {"leaked": sorted(discovery.SEED_HYPOTHESIS & implied)}


def case_validator_refuses_a_chained_implication():
    return _refuses(
        Capability(key="root", description="probed"),
        Capability(
            key="middle",
            description="implied",
            probed=False,
            implied_by="root",
            reason="because",
        ),
        Capability(
            key="leaf",
            description="implied by an implication",
            probed=False,
            implied_by="middle",
            reason="because",
        ),
    )


def case_validator_refuses_an_unreasoned_implication():
    return _refuses(
        Capability(key="root", description="probed"),
        Capability(key="leaf", description="implied", probed=False, implied_by="root"),
    )


def case_validator_refuses_an_orphan_implication():
    return _refuses(
        Capability(
            key="leaf",
            description="implied by nothing that exists",
            probed=False,
            implied_by="nowhere",
            reason="because",
        ),
    )


def case_validator_accepts_the_real_catalogue():
    # The counterweight to the three refusals above: a validator that
    # refused everything would satisfy them all and reject the shipping
    # catalogue too.
    return _refuses(*discovery.CAPABILITIES)


CASES = [
    ("unknown is not usable", case_unknown_is_not_usable,
     {"battery": False, "state": False}),
    ("unsupported is not usable", case_unsupported_is_not_usable, {"battery": False}),
    ("a key never seen is not usable", case_absent_key_is_not_usable, {"battery": False}),
    ("supported is usable", case_supported_is_usable, {"battery": True}),
    ("an implication follows its measured antecedent",
     case_implication_follows_its_antecedent,
     {"clean_action": True, "charge": True, "station_action": False,
      "play_sound": False}),
    ("the station hangs off the station-only read",
     case_station_hangs_off_the_station_only_read,
     {"without": False, "without_state": False, "with": True, "with_state": True}),
    ("an implication dies with its antecedent", case_implication_dies_with_its_antecedent,
     {"clean_action": False, "charge": False}),
    ("every implication names a probed antecedent",
     case_every_implied_key_names_a_probed_antecedent, {"offenders": []}),
    ("every implication states its reason", case_every_implied_key_states_a_reason,
     {"unreasoned": []}),
    ("the control is itself a probed capability", case_control_is_a_probed_capability,
     {"controls": ["battery"], "all_probed": True}),
    ("the seed hypothesis never names an implied key",
     case_seed_never_reaches_an_implied_key, {"leaked": []}),
    ("the validator refuses a chained implication",
     case_validator_refuses_a_chained_implication, {"refused": True}),
    ("the validator refuses an unreasoned implication",
     case_validator_refuses_an_unreasoned_implication, {"refused": True}),
    ("the validator refuses an implication with no antecedent",
     case_validator_refuses_an_orphan_implication, {"refused": True}),
    ("the validator accepts the shipping catalogue",
     case_validator_accepts_the_real_catalogue, {"refused": False}),
]

FAIL_CASES = [
    ("self-test: unknown must not be usable", case_unknown_is_not_usable,
     {"battery": True}),
    ("self-test: an unmeasured station must not be offered",
     case_station_hangs_off_the_station_only_read, {"without": True}),
    ("self-test: a chained implication must not be accepted",
     case_validator_refuses_a_chained_implication, {"refused": False}),
    ("self-test: the validator must not refuse the real catalogue",
     case_validator_accepts_the_real_catalogue, {"refused": True}),
]


def main():
    return run_suite(CASES, FAIL_CASES)


if __name__ == "__main__":
    sys.exit(main())
