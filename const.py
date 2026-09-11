"""Constants for deebot_estate.

This integration exists because `deebot-client` resolves a robot's
capabilities from a per-model lookup table keyed on an opaque class string,
and not every shipping robot is in it -- the one this was written against
(`nv8fz5`, a DEEBOT T90 PRO OMNI Care) is absent even though four sibling
T90 PRO OMNI classes are present. An unlisted class gets no
entities at all, because v14 of that library removed the fallback that used
to degrade gracefully.

So the capability set is DISCOVERED here rather than looked up.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

DOMAIN: Final = "deebot_estate"


class ProbeOutcome(StrEnum):
    """What one probe of one command actually told us.

    Derived from the RAW cloud response, deliberately, not from
    deebot-client's HandlingState: that enum folds "responded fine but the
    parser could not read it" and "failed with an errno nobody matched" into
    the same ANALYSE value, and those two mean opposite things about whether
    the capability exists.
    """

    #: `ret == "ok"`. The command exists on this robot.
    OK = "ok"

    #: `ret == "ok"` but the payload did not parse. The capability IS present
    #: -- the robot answered -- and the gap is ours. Never counted as a miss,
    #: or a parser bug would silently delete a working feature.
    UNPARSED = "unparsed"

    #: errno 500. deebot-client's own comment on this branch: "This can happen
    #: if the device has network issues or does not support the command."
    #: AMBIGUOUS BY CONSTRUCTION -- this single value is the entire reason the
    #: resolver needs a control and a miss threshold rather than one probe.
    NO_RESPONSE = "no_response"

    #: errno 4200, bot offline. Says nothing about any capability.
    OFFLINE = "offline"


class Support(StrEnum):
    """What we currently believe about one capability."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    #: Never probed, or only ever probed in passes that came back VOID.
    #: Distinct from UNSUPPORTED: "we have not looked" and "we looked and it
    #: is not there" are different facts and must not collapse.
    UNKNOWN = "unknown"


#: Consecutive misses, across VOID-free passes only, before a capability is
#: demoted. Fall dwell, never rise dwell: one OK promotes immediately, one
#: miss demotes nothing. Set above 1 because NO_RESPONSE is ambiguous; set
#: low because a genuinely absent command misses every single time and there
#: is no reason to wait longer than it takes to be sure.
DEFAULT_MISS_THRESHOLD: Final = 3
