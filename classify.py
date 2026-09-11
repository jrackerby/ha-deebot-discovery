"""Turn one raw cloud response into one `ProbeOutcome`.

IMPORTS NOTHING FROM `homeassistant` AND NOTHING FROM `deebot_client`, for
the same reason probe.py does not: this is where a wrong reading becomes a
wrong belief, so it has to be exercisable on a plain python3 against
hand-written payloads -- including the payloads a live robot will not produce
on demand.

WHY THIS IS NOT `HandlingState`. deebot-client hands its callers a
`HandlingState`, and that enum cannot answer the question this integration
asks. It folds

  * "the robot answered `ok` and our parser could not read the payload", and
  * "the robot failed with an errno nobody matched"

into the same ANALYSE value. Those mean OPPOSITE things about whether the
capability exists: the first proves it does, the second proves nothing. So
the raw response dict is classified here, and the handling state is used only
for the one thing it is authoritative about -- whether the payload parsed.

THE ERRNO READING, which is the whole ambiguity this integration is built
around. From deebot-client's own branch on errno 500: "This can happen if the
device has network issues or does not support the command." That sentence is
why a single probe can never settle a capability, and why exactly one errno
may be read as a miss. 4200 is documented as "bot offline" and says nothing
about any capability. Everything else is INCONCLUSIVE -- named as unclassified
rather than guessed at, because a guess here is spent as one of the three
misses a demotion costs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import ProbeOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ERRNO_BOT_OFFLINE", "ERRNO_NO_RESPONSE", "classify"]

#: errno 4200. The robot is not reachable by the cloud at all.
ERRNO_BOT_OFFLINE = 4200

#: errno 500. THE ambiguous one: unsupported command, or a dropped message.
ERRNO_NO_RESPONSE = 500


def _errno(response: Mapping[str, Any]) -> int | None:
    """Read `errno` as an int, whatever the cloud typed it as.

    The portal is not type-faithful: the same field comes back as `500` and
    as `"500"` depending on the endpoint and the day. A str/int comparison
    that missed would classify the one errno whose meaning is documented as
    INCONCLUSIVE, and a capability that is genuinely absent would then never
    be demoted -- a silent failure, because nothing about it looks wrong.
    """
    raw = response.get("errno")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def classify(response: Mapping[str, Any] | None, *, parsed: bool) -> ProbeOutcome:
    """Classify one probe's raw cloud response.

    `parsed` is whether the library's own handler read the payload. It is
    consulted ONLY on an `ok` response, to split OK from UNPARSED; it can
    never turn an answer into a miss, or a parser bug in a dependency would
    silently delete a working feature from this robot.

    A missing or empty response is INCONCLUSIVE, not a miss: the probe path
    returns `{}` when the request itself never completed (a timeout), and a
    timeout is a statement about the network, not about the robot.
    """
    if not response:
        return ProbeOutcome.INCONCLUSIVE

    if response.get("ret") == "ok":
        return ProbeOutcome.OK if parsed else ProbeOutcome.UNPARSED

    match _errno(response):
        case int(n) if n == ERRNO_BOT_OFFLINE:
            return ProbeOutcome.OFFLINE
        case int(n) if n == ERRNO_NO_RESPONSE:
            return ProbeOutcome.NO_RESPONSE
        case _:
            return ProbeOutcome.INCONCLUSIVE
