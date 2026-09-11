"""Constants for deebot_estate.

This integration exists because `deebot-client` resolves a robot's
capabilities from a per-model lookup table keyed on an opaque class string,
and not every shipping robot is in it -- the one this was written against
(`nv8fz5`, a DEEBOT T90 PRO OMNI Care) is absent even though four sibling
T90 PRO OMNI classes are present. An unlisted class gets no
entities at all, because v14 of that library removed the fallback that used
to degrade gracefully.

So the capability set is DISCOVERED here rather than looked up.

THIS MODULE IMPORTS NOTHING FROM `homeassistant` AND NOTHING FROM
`deebot_client`, and the suite loads it by path on a plain python3 to prove
it. Home-Assistant-coupled constants (the platform list) live in
`__init__.py`, where they are used.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Final

DOMAIN: Final = "deebot_estate"

#: `logging` is the standard library, so this module's no-HA/no-vendor
#: contract is intact and the suite still loads it on a plain python3.
LOGGER: Final = logging.getLogger(__package__)


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

    #: The robot answered something this code has never seen: not `ok`, and
    #: not one of the two errnos whose meaning is documented. NOT A MISS.
    #: Only errno 500 carries the "or does not support the command" reading;
    #: folding an unrecognised failure into it would let a cloud-side error
    #: nobody has classified demote a working capability after
    #: DEFAULT_MISS_THRESHOLD of them. It leaves the belief and the miss
    #: streak exactly where they were, and it voids a pass when the CONTROL
    #: returns it -- a control that answered something unreadable did not
    #: establish that the robot is reachable.
    INCONCLUSIVE = "inconclusive"


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

# -- config entry keys -------------------------------------------------------

CONF_COUNTRY: Final = "country"
CONF_VERIFICATION_CODE: Final = "verification_code"

#: The robot's cloud id (`did`). One entry, one robot: an account with two
#: robots gets two entries, so that removing one takes only its own ledger.
CONF_DID: Final = "did"

#: The client id Ecovacs binds device verification to. MINTED IN THE CONFIG
#: FLOW AND PERSISTED, never derived at setup: the emailed code the owner
#: typed verified THIS id, and a value regenerated on each start would ask
#: them for a new code for ever.
CONF_DEVICE_ID: Final = "device_id"

# -- storage -----------------------------------------------------------------

#: The capability map is this integration's own ledger and nothing else keeps
#: it: removing the config entry drops it, and the next setup rebuilds it by
#: probing. That is deliberate -- a map restored for a robot that is no longer
#: on the account would be belief with no measurement behind it.
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.capabilities"

# -- cadence -----------------------------------------------------------------

#: How long between maintenance probe passes. State itself arrives over MQTT
#: push, so this is NOT a state poll: it is what lets a firmware update add a
#: function and be seen, and what accumulates the consecutive misses a
#: demotion needs. One pass is roughly one cloud call per probed capability.
PROBE_INTERVAL_SECONDS: Final = 3 * 60 * 60

#: Ceiling on one pass, so a pass that is going nowhere ends rather than
#: stacking up behind the next one. `seed_order` exists because of this bound:
#: a truncated pass should have spent itself on the probes most likely to
#: resolve something.
PROBE_PASS_TIMEOUT_SECONDS: Final = 120

#: Probes issued at once. deebot-client's own Device holds a semaphore of 3
#: against this same cloud; the probe path does not go through it, so it
#: carries its own.
PROBE_CONCURRENCY: Final = 3
