#!/usr/bin/env python3
"""Exercise the capability resolver on a plain python3.

WHY IT EXISTS. This resolver decides which entities the robot gets. Its whole
job is to survive an ambiguity that cannot be resolved by looking harder at a
single probe: errno 500 means "not supported" AND "the network ate it", in
deebot-client's own words. Every rule below exists because of a specific way
that ambiguity can produce a confident wrong answer, and none of those ways
can be observed on demand against a live robot -- you would have to break the
wifi at the exact moment a probe pass runs.

probe.py imports nothing from homeassistant and nothing from deebot_client,
so this runs the real functions directly -- no HA, no cloud, no mocking.

Self-test discipline: FAIL_CASES assert deliberately WRONG outcomes
for real scenarios, and main() proves every one of them actually fails before
trusting any PASS below. A suite that cannot fail is not evidence.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load, run_suite  # noqa: E402

const, probe = load("const", "probe")
OK, UNPARSED, NO_RESPONSE, OFFLINE, INCONCLUSIVE = (
    const.ProbeOutcome.OK,
    const.ProbeOutcome.UNPARSED,
    const.ProbeOutcome.NO_RESPONSE,
    const.ProbeOutcome.OFFLINE,
    const.ProbeOutcome.INCONCLUSIVE,
)
SUPPORTED, UNSUPPORTED, UNKNOWN = (
    const.Support.SUPPORTED,
    const.Support.UNSUPPORTED,
    const.Support.UNKNOWN,
)
State = probe.CapabilityState
CONTROL = ("battery",)


def run(previous, outcomes, controls=CONTROL, threshold=3):
    return probe.resolve(previous, outcomes, controls, miss_threshold=threshold)


def misses_after(n, outcome=NO_RESPONSE, start=None):
    """Fold n identical passes, each with a passing control."""
    state = start if start is not None else {"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 0)}
    for _ in range(n):
        state = run(state, {"battery": OK, "station": outcome}).states
    return state


# --- cases -----------------------------------------------------------------

def case_promotes_on_one_ok():
    r = run({}, {"battery": OK, "station": OK})
    return {"void": r.void, "station": r.states["station"].support, "changed": r.changed}


def case_one_miss_does_not_demote():
    r = run({"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 0)}, {"battery": OK, "station": NO_RESPONSE})
    return {"station": r.states["station"].support, "misses": r.states["station"].misses, "changed": r.changed}


def case_three_misses_demote():
    s = misses_after(3)
    return {"station": s["station"].support, "misses": s["station"].misses}


def case_unparsed_never_deletes_a_feature():
    # The robot answered; only our parser is behind. Treating this as a miss
    # would let a parser bug silently strip a working capability.
    r = run({"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 2)}, {"battery": OK, "station": UNPARSED})
    return {"station": r.states["station"].support, "misses": r.states["station"].misses}


def case_control_failure_voids_the_pass():
    prev = {"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 0)}
    r = run(prev, {"battery": NO_RESPONSE, "station": NO_RESPONSE})
    return {"void": r.void, "station": r.states["station"].support, "misses": r.states["station"].misses, "changed": r.changed}


def case_void_pass_does_not_accumulate_misses():
    # The failure this guards: a flaky network produces pass after pass of
    # misses; if those counted, the streak reaches the threshold and every
    # capability is demoted at once -- indistinguishable from a correct
    # reading of a robot that lost its features.
    state = {"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 0)}
    for _ in range(10):
        state = run(state, {"battery": NO_RESPONSE, "station": NO_RESPONSE}).states
    return {"station": state["station"].support, "misses": state["station"].misses}


def case_offline_is_named_as_itself():
    r = run({}, {"battery": OFFLINE, "station": NO_RESPONSE})
    return {"void": r.void, "reason": r.reason}


def case_no_control_is_void():
    # all([]) is True -- the exact shape in which a gate stops gating.
    r = run({"station": State(SUPPORTED, 0)}, {"station": NO_RESPONSE}, controls=())
    return {"void": r.void, "reason": r.reason}


def case_control_not_attempted_is_void():
    r = run({"station": State(SUPPORTED, 0)}, {"station": OK})
    return {"void": r.void, "reason": r.reason}


def case_unprobed_key_is_untouched():
    r = run({"battery": State(SUPPORTED, 0), "map": State(SUPPORTED, 1)}, {"battery": OK})
    return {"map": r.states["map"].support, "misses": r.states["map"].misses}


def case_ok_resets_the_streak():
    s = misses_after(2)
    s = run(s, {"battery": OK, "station": OK}).states
    return {"misses": s["station"].misses, "station": s["station"].support}


def case_unknown_and_unsupported_do_not_collapse():
    # "we have not looked" vs "we looked and it is not there".
    r = run({}, {"battery": OK, "station": NO_RESPONSE})
    return {"station": r.states["station"].support}


def case_inconclusive_is_not_a_miss():
    # Rule 4. Exactly one failure code carries the "or does not support the
    # command" reading; anything else is an answer nobody has classified, and
    # spending one of three misses on it is how a cloud-side error nobody
    # understands deletes a working feature.
    s = misses_after(2)
    r = run(s, {"battery": OK, "station": INCONCLUSIVE})
    return {"station": r.states["station"].support, "misses": r.states["station"].misses,
            "changed": r.changed}


def case_inconclusive_creates_no_state():
    # The subtler half: an unclassified answer about a key never seen before
    # must not bring that key into existence with a miss on it, or "we have
    # not looked" silently acquires a streak.
    r = run({"battery": State(SUPPORTED, 0)}, {"battery": OK, "station": INCONCLUSIVE})
    return {"present": "station" in r.states, "void": r.void}


def case_inconclusive_control_voids_the_pass():
    prev = {"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 0)}
    r = run(prev, {"battery": INCONCLUSIVE, "station": NO_RESPONSE})
    return {"void": r.void, "station": r.states["station"].support,
            "misses": r.states["station"].misses}


def case_ten_inconclusive_passes_demote_nothing():
    state = {"battery": State(SUPPORTED, 0), "station": State(SUPPORTED, 0)}
    for _ in range(10):
        state = run(state, {"battery": OK, "station": INCONCLUSIVE}).states
    return {"station": state["station"].support, "misses": state["station"].misses}


def case_every_outcome_is_classified():
    # COMPLETENESS, not behaviour. Each ProbeOutcome must land in exactly one
    # of the resolver's three readings: proves-present, no-evidence, or a
    # miss (the implicit remainder). An outcome added to const.py without
    # being classified would silently fall into the remainder and be counted
    # as a miss -- the most expensive default available.
    proves = probe._PROVES_PRESENT
    no_evidence = probe._NO_EVIDENCE
    both = proves & no_evidence
    remainder = set(const.ProbeOutcome) - proves - no_evidence
    return {
        "overlap": sorted(o.value for o in both),
        "proves": sorted(o.value for o in proves),
        "no_evidence": sorted(o.value for o in no_evidence),
        "counted_as_miss": sorted(o.value for o in remainder),
    }


def case_seed_orders_but_never_believes():
    known = {"station": State(UNKNOWN, 0), "map": State(UNKNOWN, 0), "battery": State(SUPPORTED, 0)}
    order = probe.seed_order(known, hypothesis={"map"}, candidates=["station", "map", "battery"])
    return {"first": order[0], "last": order[-1], "map_support": known["map"].support}


CASES = [
    # `changed` names BOTH keys: previous was empty, so the control itself
    # also moved UNKNOWN -> SUPPORTED. That is a real transition and belongs
    # in the list -- the first expectation written here said ("station",) and
    # was simply wrong about what the pass did.
    ("one OK promotes immediately", case_promotes_on_one_ok,
     {"void": False, "station": SUPPORTED, "changed": ("battery", "station")}),
    ("one miss demotes nothing", case_one_miss_does_not_demote,
     {"station": SUPPORTED, "misses": 1, "changed": ()}),
    ("three misses demote", case_three_misses_demote, {"station": UNSUPPORTED, "misses": 3}),
    ("an unparsed answer never deletes a feature", case_unparsed_never_deletes_a_feature,
     {"station": SUPPORTED, "misses": 0}),
    ("a failed control voids the pass and changes nothing", case_control_failure_voids_the_pass,
     {"void": True, "station": SUPPORTED, "misses": 0, "changed": ()}),
    ("ten void passes accumulate nothing", case_void_pass_does_not_accumulate_misses,
     {"station": SUPPORTED, "misses": 0}),
    ("offline is reported as offline", case_offline_is_named_as_itself,
     {"void": True, "reason": "robot reported offline"}),
    ("a pass with no control is void", case_no_control_is_void,
     {"void": True, "reason": "pass carried no control probe"}),
    ("a control that was not attempted voids the pass", case_control_not_attempted_is_void,
     {"void": True, "reason": "control probe(s) not attempted: battery"}),
    ("a key not probed is left alone", case_unprobed_key_is_untouched,
     {"map": SUPPORTED, "misses": 1}),
    ("an answer resets the miss streak", case_ok_resets_the_streak,
     {"misses": 0, "station": SUPPORTED}),
    ("unknown does not collapse into unsupported", case_unknown_and_unsupported_do_not_collapse,
     {"station": UNKNOWN}),
    ("a seed orders the probe and grants no belief", case_seed_orders_but_never_believes,
     {"first": "map", "last": "battery", "map_support": UNKNOWN}),
    ("an inconclusive answer is not a miss", case_inconclusive_is_not_a_miss,
     {"station": SUPPORTED, "misses": 2, "changed": ()}),
    ("an inconclusive answer creates no belief to miss against",
     case_inconclusive_creates_no_state, {"present": False, "void": False}),
    ("an inconclusive control voids the pass", case_inconclusive_control_voids_the_pass,
     {"void": True, "station": SUPPORTED, "misses": 0}),
    ("ten inconclusive passes demote nothing",
     case_ten_inconclusive_passes_demote_nothing, {"station": SUPPORTED, "misses": 0}),
    ("every outcome is classified exactly once", case_every_outcome_is_classified,
     {"overlap": [], "proves": ["ok", "unparsed"], "no_evidence": ["inconclusive"],
      "counted_as_miss": ["no_response", "offline"]}),
]

# Deliberately WRONG expectations for real scenarios. Every one must fail.
FAIL_CASES = [
    ("self-test: one miss must not demote", case_one_miss_does_not_demote, {"station": UNSUPPORTED}),
    ("self-test: a void pass must not demote", case_control_failure_voids_the_pass, {"station": UNSUPPORTED}),
    ("self-test: void passes must not accumulate", case_void_pass_does_not_accumulate_misses, {"misses": 10}),
    ("self-test: no-control must not be admissible", case_no_control_is_void, {"void": False}),
    ("self-test: a seed must not grant belief", case_seed_orders_but_never_believes, {"map_support": SUPPORTED}),
    ("self-test: inconclusive must not count as a miss", case_inconclusive_is_not_a_miss, {"misses": 3}),
    ("self-test: inconclusive must not invent a key", case_inconclusive_creates_no_state, {"present": True}),
    ("self-test: an inconclusive control must not be admissible", case_inconclusive_control_voids_the_pass, {"void": False}),
    ("self-test: no outcome may be both proof and no-evidence", case_every_outcome_is_classified, {"overlap": ["ok"]}),
]


def main():
    return run_suite(CASES, FAIL_CASES)


if __name__ == "__main__":
    sys.exit(main())
