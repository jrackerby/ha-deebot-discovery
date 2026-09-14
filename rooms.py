"""Which rooms the robot's own map carries, and what they are called.

IMPORTS NOTHING FROM `homeassistant` AND NOTHING FROM `deebot_client`, like
the rest of the pure layer: the caller decompresses the payload with the
library's own helper and hands the decoded rows in here. That split is what
lets the risky half -- reading a positional vendor format -- be exercised by
a suite that needs neither package installed.

WHY THIS PARSER EXISTS AT ALL, BECAUSE IT LOOKS LIKE DUPLICATING THE LIBRARY.
deebot-client already reads room subsets, and for two shapes it does it well:
a 10-field row and an 11-field row (its comment names the X11 as the reason
for the second) both carry the room's name at index 1, and it dispatches a
`RoomsEvent` carrying id and name. Any OTHER width falls through to a branch
that emits the ids alone and asks for `GetMapSubSet` per room to fetch the
names.

THIS ROBOT EMITS TWELVE FIELDS AND REFUSES `getMapSubSet`. Measured live on
a DEEBOT T90 PRO OMNI Care, firmware 1.103.0: `getMapSetV2` answers with
eight 12-field rows, and all eight follow-up `getMapSubSet` calls come back
`20003 rcp not support`. So the library's named path never runs, its fallback
path cannot complete, and the rooms arrive with no names attached -- while
the names were sitting in the payload the whole time.

The layout at the front of the row is the library's own documented one and
does not move between the widths it knows about:

    index 0 -> room id
    index 1 -> room name
    index 2 -> icon number
    ...      (positions past here differ per width and are not read)

So this reads the two fields upstream already reads, over a width upstream
does not yet handle, and nothing else. It is not a second opinion about the
format; it is the same opinion applied one column wider. When deebot-client
learns this width, `parse_rooms` becomes dead code and should go.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["MIN_NAMED_FIELDS", "Room", "find_room", "parse_rooms"]

#: Where the two fields worth reading sit. Positional because the vendor
#: format is positional -- there are no keys to read.
_ID: Final = 0
_NAME: Final = 1

#: The narrowest row that carries a name at index 1. Below this the payload
#: is the older shape whose index 1 is NOT a name (upstream documents it as
#: "unknown"), and whose names only ever arrive from `getMapSubSet`.
#:
#: THE FLOOR IS A REFUSAL, NOT A GUESS. A 9-field row read with this code
#: would publish whatever happens to sit at index 1 as the room's name, and
#: a button labelled with an icon number is worse than no button: it is
#: indistinguishable from a room that really is called that.
MIN_NAMED_FIELDS: Final = 10


@dataclass(frozen=True, slots=True)
class Room:
    """One room of the robot's current map."""

    #: The id the robot addresses this room by, and the only stable handle on
    #: it: renaming a room in the Ecovacs app changes `name` and leaves this
    #: alone, so it is what an entity's unique id hangs off.
    id: int

    #: What the owner called it in the Ecovacs app.
    name: str


def parse_rooms(subsets: Iterable[Any]) -> list[Room]:
    """Read `(id, name)` out of a decoded map-set payload.

    Returns rooms sorted by id. A row this cannot read is DROPPED rather than
    guessed at, and a payload whose rows are all too narrow yields an empty
    list -- which the caller treats as "this robot will not tell us its room
    names", not as "this robot has no rooms". Those are different, and only
    the second one would justify showing the owner nothing.
    """
    rooms: dict[int, Room] = {}
    for row in subsets:
        if not isinstance(row, (list, tuple)) or len(row) < MIN_NAMED_FIELDS:
            continue
        try:
            room_id = int(str(row[_ID]).strip())
        except (TypeError, ValueError):
            continue
        name = str(row[_NAME]).strip() if row[_NAME] is not None else ""
        if not name:
            # A blank name is a real value in this payload -- unnamed
            # positions come through as a single space -- and a button with
            # no name on it is not something to ship.
            continue
        # First row wins: a duplicated id is a malformed payload, and taking
        # the later one would silently prefer whichever the vendor happened
        # to append last.
        rooms.setdefault(room_id, Room(id=room_id, name=name))
    return sorted(rooms.values(), key=lambda room: room.id)


def find_room(rooms: Iterable[Room], room_id: int) -> Room | None:
    """The room the map currently carries under this id, or None.

    KEPT HERE, IN THE PURE LAYER, because the entity layer needs to ask it on
    every coordinator update and this is the half that can be exercised
    without Home Assistant. Two callers depend on the None:

      * a room's button is named from what this returns, so a rename in the
        Ecovacs app reaches the glass; and
      * the same button reports unavailable when it returns None, because a
        room DELETED from the map still has an entity -- entities are never
        removed on a demotion (`entity.py`) -- and pressing it would send
        `CleanArea` for an area the robot no longer has, which surfaces as a
        bare "command failed" naming nothing.

    A linear scan, deliberately: this runs per entity per update over a map
    with single-digit room counts, and an index would have to be invalidated
    every time the map changed.
    """
    for room in rooms:
        if room.id == room_id:
            return room
    return None
