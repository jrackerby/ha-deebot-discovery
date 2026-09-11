#!/usr/bin/env python3
"""Exercise the raw-response classifier on a plain python3.

WHY IT EXISTS. `classify` is where a cloud payload becomes evidence, and
every one of the payloads that matters is a payload a live robot will not
produce on demand: you cannot ask a working robot to answer errno 500, and
you cannot ask the portal to type its errno as a string on the day you are
watching. Those are exactly the cases that decide whether a capability is
demoted, so they are asserted here against hand-written dicts.

THE SHAPE OF THE ERROR THIS GUARDS. There is one errno -- 500 -- that may be
read as "not supported", on deebot-client's own authority. If a real 500
failed to be recognised (because it arrived as `"500"` and the comparison was
against `500`), a genuinely absent capability would never accumulate a miss
and would stay SUPPORTED for ever, with an entity that never works and
nothing in any log to say why. If an unrecognised failure were read AS 500,
a working capability would be demoted after three of them. Both directions
are covered below.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load, run_suite  # noqa: E402

const, classify_mod = load("const", "classify")
classify = classify_mod.classify
OK, UNPARSED, NO_RESPONSE, OFFLINE, INCONCLUSIVE = (
    const.ProbeOutcome.OK,
    const.ProbeOutcome.UNPARSED,
    const.ProbeOutcome.NO_RESPONSE,
    const.ProbeOutcome.OFFLINE,
    const.ProbeOutcome.INCONCLUSIVE,
)


def outcome(response, *, parsed=True):
    return {"outcome": classify(response, parsed=parsed)}


# --- cases -----------------------------------------------------------------

def case_ok_and_parsed():
    return outcome({"ret": "ok", "resp": {"battery": {"value": 84}}})


def case_ok_but_unparsed():
    # The robot answered; our reader is behind. Counting this as a miss would
    # let a parser bug in a dependency silently delete a working feature.
    return outcome({"ret": "ok", "resp": {"unexpected": True}}, parsed=False)


def case_errno_500_is_the_ambiguous_one():
    return outcome({"ret": "fail", "errno": 500, "error": "wait for response timed out"})


def case_errno_500_as_a_string():
    # THE TYPE TRAP. The portal is not type-faithful and the same field comes
    # back as 500 and as "500". A miss that fails to be recognised is silent
    # in both directions: nothing logs, and the capability simply never
    # demotes.
    return outcome({"ret": "fail", "errno": "500"})


def case_errno_4200_is_offline():
    return outcome({"ret": "fail", "errno": 4200, "error": "bot offline"})


def case_unknown_errno_is_inconclusive():
    return outcome({"ret": "fail", "errno": 3, "error": "something else entirely"})


def case_failure_with_no_errno_is_inconclusive():
    return outcome({"ret": "fail"})


def case_empty_response_is_inconclusive():
    # The probe path hands back {} when the request never completed. A
    # timeout is a statement about the network, not about the robot.
    return outcome({})


def case_none_response_is_inconclusive():
    return outcome(None)


def case_unparsed_only_splits_ok():
    # `parsed` may split OK from UNPARSED and may never turn a failure into
    # something else: a parser that could not read an errno payload must not
    # change what that errno means.
    return {
        "miss": classify({"ret": "fail", "errno": 500}, parsed=False),
        "offline": classify({"errno": 4200}, parsed=False),
    }


def case_garbage_errno_does_not_crash():
    return {
        "text": classify({"ret": "fail", "errno": "not a number"}, parsed=False),
        "null": classify({"ret": "fail", "errno": None}, parsed=False),
        "bool": classify({"ret": "fail", "errno": True}, parsed=False),
    }


CASES = [
    ("an ok response that parsed is proof", case_ok_and_parsed, {"outcome": OK}),
    ("an ok response that did not parse is still proof", case_ok_but_unparsed,
     {"outcome": UNPARSED}),
    ("errno 500 is the one readable as a miss", case_errno_500_is_the_ambiguous_one,
     {"outcome": NO_RESPONSE}),
    ("errno 500 is recognised when typed as a string", case_errno_500_as_a_string,
     {"outcome": NO_RESPONSE}),
    ("errno 4200 is offline, not a miss", case_errno_4200_is_offline,
     {"outcome": OFFLINE}),
    ("an errno nobody classified is not a miss", case_unknown_errno_is_inconclusive,
     {"outcome": INCONCLUSIVE}),
    ("a failure with no errno is not a miss", case_failure_with_no_errno_is_inconclusive,
     {"outcome": INCONCLUSIVE}),
    ("an empty response is not a miss", case_empty_response_is_inconclusive,
     {"outcome": INCONCLUSIVE}),
    ("no response at all is not a miss", case_none_response_is_inconclusive,
     {"outcome": INCONCLUSIVE}),
    ("the parse flag only ever splits ok", case_unparsed_only_splits_ok,
     {"miss": NO_RESPONSE, "offline": OFFLINE}),
    ("an unreadable errno is not a miss", case_garbage_errno_does_not_crash,
     {"text": INCONCLUSIVE, "null": INCONCLUSIVE, "bool": INCONCLUSIVE}),
]

# Deliberately WRONG expectations for real scenarios. Every one must fail.
FAIL_CASES = [
    ("self-test: an ok response must never be a miss", case_ok_and_parsed,
     {"outcome": NO_RESPONSE}),
    ("self-test: an unparsed answer must not be a miss", case_ok_but_unparsed,
     {"outcome": NO_RESPONSE}),
    ("self-test: a string errno 500 must not slip through as unclassified",
     case_errno_500_as_a_string, {"outcome": INCONCLUSIVE}),
    ("self-test: an unknown errno must not be read as a miss",
     case_unknown_errno_is_inconclusive, {"outcome": NO_RESPONSE}),
    ("self-test: offline must not be read as a miss", case_errno_4200_is_offline,
     {"outcome": NO_RESPONSE}),
]


def main():
    return run_suite(CASES, FAIL_CASES)


if __name__ == "__main__":
    sys.exit(main())
