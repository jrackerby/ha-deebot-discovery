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

import importlib.util
import os
import sys
import types

# tests/ sits directly under the component root in both layouts: this repo
# standing alone, and this repo installed as custom_components/deebot_estate.
# One expression covers both.
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    """Load const.py and probe.py by path, bypassing the package __init__ --
    which will import homeassistant once this integration has one, and would
    otherwise drag the whole HA runtime in to reach two import-free modules."""
    pkg = types.ModuleType("deebot_estate")
    pkg.__path__ = [PKG_DIR]
    sys.modules["deebot_estate"] = pkg
    out = {}
    for name in ("const", "probe"):
        spec = importlib.util.spec_from_file_location(
            f"deebot_estate.{name}", os.path.join(PKG_DIR, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"deebot_estate.{name}"] = mod
        spec.loader.exec_module(mod)
        out[name] = mod
    return out["const"], out["probe"]


const, probe = _load()
OK, UNPARSED, NO_RESPONSE, OFFLINE = (
    const.ProbeOutcome.OK,
    const.ProbeOutcome.UNPARSED,
    const.ProbeOutcome.NO_RESPONSE,
    const.ProbeOutcome.OFFLINE,
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
]

# Deliberately WRONG expectations for real scenarios. Every one must fail.
FAIL_CASES = [
    ("self-test: one miss must not demote", case_one_miss_does_not_demote, {"station": UNSUPPORTED}),
    ("self-test: a void pass must not demote", case_control_failure_voids_the_pass, {"station": UNSUPPORTED}),
    ("self-test: void passes must not accumulate", case_void_pass_does_not_accumulate_misses, {"misses": 10}),
    ("self-test: no-control must not be admissible", case_no_control_is_void, {"void": False}),
    ("self-test: a seed must not grant belief", case_seed_orders_but_never_believes, {"map_support": SUPPORTED}),
]


def diff(expected, got):
    return [f"{k}: expected {v!r}, got {got.get(k)!r}" for k, v in expected.items() if got.get(k) != v]


def main():
    print("SELF-TEST -- every case below must FAIL:")
    ok = True
    for name, fn, wrong in FAIL_CASES:
        d = diff(wrong, fn())
        print(f"  {'ok (failed as required)' if d else 'BROKEN (passed!)':<24} {name}")
        ok = ok and bool(d)
    if not ok:
        print("\nself-test did not fail where it must -- no result below is evidence")
        return 1

    print("\nCASES:")
    failed = 0
    for name, fn, expected in CASES:
        d = diff(expected, fn())
        print(f"  {'PASS' if not d else 'FAIL'}  {name}")
        for line in d:
            print(f"          {line}")
        failed += bool(d)
    print(f"\n{len(CASES) - failed}/{len(CASES)} cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
