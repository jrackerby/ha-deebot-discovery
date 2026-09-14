#!/usr/bin/env python3
"""Reading room ids and names out of the vendor's positional payload.

WHY THIS SUITE EXISTS. `parse_rooms` reads two fields out of a row that has
no keys, from a format that has already changed width three times that we
know of (10 fields, 11 on the X11, 12 on this robot). Every failure mode it
has is silent on a dashboard:

  * Read index 1 of a row that does not carry a name there and every button
    is labelled with an icon number, which looks exactly like a room someone
    named "0".
  * Drop a width the robot really does emit and the owner gets no buttons at
    all, which looks exactly like a robot with no rooms.
  * Let a duplicate id through and two buttons claim the same room; the
    second one silently wins the entity id.

THE FIXTURE IS REAL. `MEASURED` below is the actual decompressed payload
from the DEEBOT T90 PRO OMNI Care this integration was written for
(firmware 1.103.0), copied from the probe that read it rather than invented
-- so if upstream's index-1-is-the-name claim were wrong for this width,
this file would have caught it by printing somebody's icon number as a room.

SELF-TEST, LAW.md §4: `the assertions can fail` drives the same parser over
inputs that must NOT satisfy them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load, run_suite  # noqa: E402

rooms = load("rooms")

#: The eight 12-field rows this robot actually returned. Trailing fields are
#: kept exactly as measured; the parser must not care how many there are.
MEASURED = [
    ["1", "Powder Room", "0", "13", "0", "-6275", "8825", "1-0-2", " ", "0", "0", "0"],
    ["5", "Foyer", "0", "13", "0", "-4375", "9775", "1-0-2", " ", "0", "0", "0"],
    ["8", "Kitchen", "0", "10-12-13", "0", "-875", "5575", "1-0-2", " ", "0", "0", "0"],
    ["9", "Main Bathroom", "0", "11", "0", "-16325", "1425", "1-0-2", " ", "0", "0", "0"],
    ["10", "Sunroom", "0", "8-12", "0", "-1675", "-1275", "1-0-2", " ", "0", "0", "0"],
    ["11", "Main Bedroom", "0", "9-13", "0", "-11275", "2825", "1-0-2", " ", "0", "0", "0"],
    ["12", "Family Room", "0", "8-10-13", "0", "-4725", "3025", "1-0-2", " ", "0", "0", "0"],
    ["13", "Hallway 1", "0", "1-5-8-11-12", "0", "-8375", "7425", "1-0-2", " ", "0", "0", "0"],
]

EXPECTED = [
    (1, "Powder Room"),
    (5, "Foyer"),
    (8, "Kitchen"),
    (9, "Main Bathroom"),
    (10, "Sunroom"),
    (11, "Main Bedroom"),
    (12, "Family Room"),
    (13, "Hallway 1"),
]


def pairs(parsed):
    return [(room.id, room.name) for room in parsed]


def case_measured_payload():
    return {"rooms": pairs(rooms.parse_rooms(MEASURED))}


def case_ten_field_row():
    """The width upstream already handles must read identically."""
    ten = [row[:10] for row in MEASURED]
    return {"rooms": pairs(rooms.parse_rooms(ten))}


def case_eleven_field_row():
    """And the X11's width, for the same reason."""
    eleven = [row[:11] for row in MEASURED]
    return {"rooms": pairs(rooms.parse_rooms(eleven))}


def case_too_narrow_is_refused():
    """A 9-field row has no name at index 1 -- take nothing from it."""
    narrow = [row[:9] for row in MEASURED]
    return {"rooms": pairs(rooms.parse_rooms(narrow))}


def case_blank_names_dropped():
    rows = [
        ["3", " ", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
        ["4", "", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
        ["7", "Study", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
    ]
    return {"rooms": pairs(rooms.parse_rooms(rows))}


def case_unreadable_id_dropped():
    rows = [
        ["not-a-number", "Ghost", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
        ["7", "Study", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
    ]
    return {"rooms": pairs(rooms.parse_rooms(rows))}


def case_duplicate_id_takes_the_first():
    rows = [
        ["8", "Kitchen", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
        ["8", "Scullery", "0", "x", "0", "0", "0", "1-0-2", " ", "0"],
    ]
    return {"rooms": pairs(rooms.parse_rooms(rows))}


def case_sorted_by_id():
    return {"rooms": pairs(rooms.parse_rooms(list(reversed(MEASURED))))}


def case_junk_rows_survived():
    rows = [None, "string", 42, {}, MEASURED[2]]
    return {"rooms": pairs(rooms.parse_rooms(rows))}


def case_empty():
    return {"rooms": pairs(rooms.parse_rooms([]))}


CASES = [
    ("the measured 12-field payload reads as eight named rooms", case_measured_payload, {"rooms": EXPECTED}),
    ("a 10-field row reads the same", case_ten_field_row, {"rooms": EXPECTED}),
    ("an 11-field row reads the same", case_eleven_field_row, {"rooms": EXPECTED}),
    ("a 9-field row carries no name and is refused", case_too_narrow_is_refused, {"rooms": []}),
    ("a blank name is not a name", case_blank_names_dropped, {"rooms": [(7, "Study")]}),
    ("a row whose id will not parse is dropped", case_unreadable_id_dropped, {"rooms": [(7, "Study")]}),
    ("a duplicated id takes the first row", case_duplicate_id_takes_the_first, {"rooms": [(8, "Kitchen")]}),
    ("rooms come back sorted by id", case_sorted_by_id, {"rooms": EXPECTED}),
    ("junk rows do not stop the readable one", case_junk_rows_survived, {"rooms": [(8, "Kitchen")]}),
    ("an empty payload is empty, not an error", case_empty, {"rooms": []}),
]

# Each expects a WRONG answer, so a parser that produced it would be broken.
FAIL_CASES = [
    (
        "self-test: reading the icon number as the name must not pass",
        case_measured_payload,
        {"rooms": [(row_id, "0") for row_id, _ in EXPECTED]},
    ),
    (
        "self-test: a 9-field row must not yield rooms",
        case_too_narrow_is_refused,
        {"rooms": EXPECTED},
    ),
    (
        "self-test: blank names must not survive",
        case_blank_names_dropped,
        {"rooms": [(3, " "), (4, ""), (7, "Study")]},
    ),
    (
        "self-test: the duplicate must not take the last row",
        case_duplicate_id_takes_the_first,
        {"rooms": [(8, "Scullery")]},
    ),
    (
        "self-test: unsorted output must not pass",
        case_sorted_by_id,
        {"rooms": list(reversed(EXPECTED))},
    ),
]


# -- find_room: the lookup the room buttons make on every update -------------
#
# It carries two consequences and they fail differently. A miss on a RENAME
# leaves a button labelled with the old name; a miss on a DELETION leaves a
# button that is still pressable and sends `CleanArea` for an area the robot
# no longer has. The second is why `None` has to mean "gone", never "empty".

PARSED = rooms.parse_rooms(MEASURED)


def case_find_by_id():
    room = rooms.find_room(PARSED, 8)
    return {"found": (room.id, room.name) if room else None}


def case_find_missing_id_is_none():
    """A room deleted from the map. Must be None, not the nearest thing."""
    return {"found": rooms.find_room(PARSED, 9999)}


def case_find_in_empty_map_is_none():
    return {"found": rooms.find_room([], 8)}


def case_find_reads_the_current_name():
    """The rename case: same id, new name, and the lookup follows the name."""
    renamed = [
        rooms.Room(id=8, name="Scullery") if room.id == 8 else room
        for room in PARSED
    ]
    room = rooms.find_room(renamed, 8)
    return {"found": (room.id, room.name) if room else None}


def case_find_does_not_match_on_name():
    """The id is the handle. A string that looks like one must not match."""
    return {"found": rooms.find_room(PARSED, "8")}


CASES += [
    ("find_room returns the room with that id", case_find_by_id, {"found": (8, "Kitchen")}),
    ("a deleted room's id yields None", case_find_missing_id_is_none, {"found": None}),
    ("an empty map yields None", case_find_in_empty_map_is_none, {"found": None}),
    (
        "a renamed room is found under its id, with the new name",
        case_find_reads_the_current_name,
        {"found": (8, "Scullery")},
    ),
    ("a string id does not match an int id", case_find_does_not_match_on_name, {"found": None}),
]

FAIL_CASES += [
    (
        "self-test: a missing id must not resolve to a room",
        case_find_missing_id_is_none,
        {"found": (8, "Kitchen")},
    ),
    (
        "self-test: a rename must not keep reporting the old name",
        case_find_reads_the_current_name,
        {"found": (8, "Kitchen")},
    ),
]


def main():
    return run_suite(CASES, FAIL_CASES)


if __name__ == "__main__":
    sys.exit(main())
