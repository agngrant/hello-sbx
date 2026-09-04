"""Door state model tests (door-features spec §3, AC1, AC2, AC13).

Pure unit tests of :class:`app.models.Grid`'s additive ``doors`` field:
round-trip via ``to_dict``/``from_dict``, the closed+locked default
(``doors=None``), ``__post_init__`` validation (doorway-only / in-bounds /
valid state), and the derived accessors + paint-sync helper.
"""

from __future__ import annotations

import unittest

from app.grid import build_sample_map
from app.models import DOOR_STATES, SAFE_DOOR_STATES, SAFE_DOOR_TEAMS, Grid


def _grid(rows, doors=None, safe=None, name="t"):
    height = len(rows)
    width = len(rows[0])
    return Grid(
        name=name, width=width, height=height,
        cells=[list(r) for r in rows], doors=doors, safe=safe,
    )


# A tiny map with two doorway cells at (1,0) and (3,2).
_DOORS_ROWS = [
    ["floor", "doorway", "wall", "floor"],
    ["wall", "floor", "floor", "floor"],
    ["wall", "wall", "floor", "doorway"],
]


class TestDoorConstants(unittest.TestCase):
    def test_door_states_are_l_u_o(self):
        self.assertEqual(DOOR_STATES, ("L", "U", "O"))


class TestDoorRoundTrip(unittest.TestCase):
    """AC1(a): ``Grid.from_dict(g.to_dict())`` preserves every door state."""

    def test_roundtrip_preserves_states(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O", "3,2": "U"})
        back = Grid.from_dict(g.to_dict())
        self.assertEqual(back.doors, {"1,0": "O", "3,2": "U"})

    def test_roundtrip_preserves_partial_state(self):
        # Only one door recorded (the other stays the L default in memory).
        g = _grid(_DOORS_ROWS, {"1,0": "O"})
        back = Grid.from_dict(g.to_dict())
        self.assertEqual(back.doors, {"1,0": "O"})
        self.assertEqual(back.door_state_at(3, 2), "L")  # unrecorded → L

    def test_to_dict_emits_doors_when_present(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O"})
        d = g.to_dict()
        self.assertEqual(d["doors"], {"1,0": "O"})


class TestDoorDefaultLocked(unittest.TestCase):
    """AC1(b), AC2(a): doors=None ⇒ every doorway is L; to_dict omits the key."""

    def test_none_means_all_locked(self):
        g = _grid(_DOORS_ROWS)  # doors=None (default)
        self.assertIsNone(g.doors)
        self.assertEqual(g.door_state_at(1, 0), "L")
        self.assertEqual(g.door_state_at(3, 2), "L")
        self.assertTrue(g.is_door_closed(1, 0))
        self.assertTrue(g.is_door_closed(3, 2))

    def test_to_dict_omits_doors_key_when_none(self):
        g = _grid(_DOORS_ROWS)
        self.assertNotIn("doors", g.to_dict())
        # Round-trips back to doors=None (the all-locked default).
        self.assertIsNone(Grid.from_dict(g.to_dict()).doors)

    def test_from_dict_no_doors_key_all_locked(self):
        # AC12 / A2: an old payload without `doors` parses all-locked.
        d = Grid(name="x", width=4, height=3,
                 cells=[[list(r) for r in _DOORS_ROWS][0]] * 3,
                 ).to_dict()
        self.assertNotIn("doors", d)  # doors=None ⇒ no doors key emitted
        g = Grid.from_dict(d)
        self.assertIsNone(g.doors)
        self.assertEqual(g.door_state_at(1, 0), "L")

    def test_empty_doors_equivalent_to_none(self):
        g = _grid(_DOORS_ROWS, {})
        self.assertEqual(g.doors, {})
        self.assertEqual(g.door_state_at(1, 0), "L")
        # to_dict omits an empty object (AC1: "omits the key (or emits {})").
        self.assertNotIn("doors", g.to_dict())

    def test_sample_map_doors_all_locked(self):
        # AC2(a): build_sample_map's 3 doorways are all L (doors=None).
        g = build_sample_map()
        self.assertIsNone(g.doors)
        for x, y in ((5, 5), (10, 4), (9, 7)):
            self.assertEqual(g.door_state_at(x, y), "L")
            self.assertTrue(g.is_door_closed(x, y))
            self.assertEqual(g.cells[y][x], "doorway")


class TestDoorPostInitValidation(unittest.TestCase):
    """AC1(d): __post_init__ rejects malformed door keys / bad states."""

    def test_rejects_door_on_floor_cell(self):
        rows = [["floor", "floor", "wall"]]
        with self.assertRaises(ValueError):
            _grid(rows, {"0,0": "L"})  # (0,0) is floor

    def test_rejects_door_on_wall_cell(self):
        rows = [["wall", "floor", "floor"]]
        with self.assertRaises(ValueError):
            _grid(rows, {"0,0": "L"})  # (0,0) is wall

    def test_rejects_out_of_bounds_key(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, {"9,9": "L"})

    def test_rejects_bad_state_char(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, {"1,0": "X"})

    def test_rejects_malformed_key_no_comma(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, {"10": "L"})

    def test_rejects_malformed_key_non_numeric(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, {"a,b": "L"})

    def test_accepts_valid_states(self):
        for st in ("L", "U", "O"):
            g = _grid(_DOORS_ROWS, {"1,0": st})
            self.assertEqual(g.door_state_at(1, 0), st)

    def test_none_bypasses_validation(self):
        # None ⇒ no keys to validate ⇒ no error even with no doorways.
        g = _grid([["floor", "wall"]])
        self.assertIsNone(g.doors)


class TestDoorAccessors(unittest.TestCase):
    """AC1(e/f): door_state_at / is_door_closed / set_door semantics."""

    def test_door_state_at_none_for_non_doorway(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O"})
        self.assertIsNone(g.door_state_at(0, 0))  # floor
        self.assertIsNone(g.door_state_at(2, 0))  # wall

    def test_door_state_at_default_and_recorded(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O"})
        self.assertEqual(g.door_state_at(1, 0), "O")  # recorded
        self.assertEqual(g.door_state_at(3, 2), "L")  # unrecorded doorway → L

    def test_is_door_closed(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O", "3,2": "U"})
        self.assertFalse(g.is_door_closed(1, 0))  # O → open
        self.assertTrue(g.is_door_closed(3, 2))   # U → closed
        self.assertFalse(g.is_door_closed(0, 0))  # floor → not a door


class TestSetDoor(unittest.TestCase):
    def test_set_door_materializes_and_sets(self):
        g = _grid(_DOORS_ROWS)
        self.assertIsNone(g.doors)
        g.set_door(1, 0, "O")
        self.assertEqual(g.doors, {"1,0": "O"})

    def test_set_door_rejects_non_doorway(self):
        g = _grid(_DOORS_ROWS)
        with self.assertRaises(ValueError):
            g.set_door(0, 0, "O")  # floor

    def test_set_door_rejects_bad_state(self):
        g = _grid(_DOORS_ROWS)
        with self.assertRaises(ValueError):
            g.set_door(1, 0, "X")


class TestSyncDoorsAfterCellSet(unittest.TestCase):
    """§3.5 / §9 (D4): paint-sync keeps doors consistent with the cell type."""

    def test_paint_doorway_from_none_creates_empty_map(self):
        g = _grid(_DOORS_ROWS)  # all L by default (doors=None)
        # Paint an existing doorway: no state change (stays default L).
        g.sync_doors_after_cell_set(1, 0)
        self.assertEqual(g.doors, {})  # materialized empty; door is L default
        self.assertEqual(g.door_state_at(1, 0), "L")

    def test_paint_floor_over_door_deletes_state(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O"})
        g.cells[0][1] = "floor"
        g.sync_doors_after_cell_set(1, 0)
        self.assertNotIn("1,0", g.doors)

    def test_paint_wall_over_door_deletes_state(self):
        g = _grid(_DOORS_ROWS, {"3,2": "U"})
        g.cells[2][3] = "wall"
        g.sync_doors_after_cell_set(3, 2)
        self.assertNotIn("3,2", g.doors)

    def test_paint_existing_doorway_keeps_state(self):
        # Repainting an existing doorway keeps its current state (no reset).
        g = _grid(_DOORS_ROWS, {"1,0": "O"})
        g.cells[0][1] = "doorway"  # already a doorway
        g.sync_doors_after_cell_set(1, 0)
        self.assertEqual(g.door_state_at(1, 0), "O")  # unchanged

    def test_paint_new_doorway_on_none(self):
        # Paint a floor → doorway on a doors=None grid: door exists (L).
        g = _grid(_DOORS_ROWS)
        g.cells[0][0] = "doorway"  # was floor
        g.sync_doors_after_cell_set(0, 0)
        self.assertEqual(g.doors, {})
        self.assertEqual(g.door_state_at(0, 0), "L")


class TestDoorsForWire(unittest.TestCase):
    """§8.1/A9/I5/AC10: the wire object is the FULL door set (default L)."""

    def test_wire_full_for_sample_map(self):
        g = build_sample_map()  # doors=None
        self.assertEqual(g.doors_for_wire(),
                         {"5,5": "L", "10,4": "L", "9,7": "L"})

    def test_wire_reflects_recorded_state(self):
        g = build_sample_map()
        g.doors = {"5,5": "O"}
        self.assertEqual(g.doors_for_wire(),
                         {"5,5": "O", "10,4": "L", "9,7": "L"})

    def test_wire_none_when_no_doorways(self):
        g = _grid([["floor", "wall"], ["wall", "floor"]])
        self.assertIsNone(g.doors_for_wire())


# ---------------------------------------------------------------------------
# Safe-room doors (safe-room spec §3, AC1, AC12) — a new additive layer on
# Grid: the `safe` dict, the constants, validation, accessors, the paint-
# sync point, and the wire partition (doors ∪ safe = all doorways).
# ---------------------------------------------------------------------------


class TestSafeDoorConstants(unittest.TestCase):
    def test_safe_door_states_are_c_o(self):
        self.assertEqual(SAFE_DOOR_STATES, ("C", "O"))

    def test_safe_door_teams_exclude_hostile(self):
        self.assertEqual(SAFE_DOOR_TEAMS, frozenset({"party", "neutral"}))
        self.assertNotIn("hostile", SAFE_DOOR_TEAMS)


class TestSafeDoorRoundTrip(unittest.TestCase):
    """AC1(a/b/c): `safe` round-trips to_dict/from_dict; absent ⇒ no safe
    doors; present ⇒ every state preserved (and every doors state too)."""

    def test_none_omits_key_and_round_trips(self):
        g = _grid(_DOORS_ROWS)
        self.assertIsNone(g.safe)
        d = g.to_dict()
        self.assertNotIn("safe", d)
        self.assertIsNone(Grid.from_dict(d).safe)

    def test_single_closed_safe_door_round_trips(self):
        # (1,0) is a doorway of _DOORS_ROWS.
        g = _grid(_DOORS_ROWS, safe={"1,0": "C"})
        d = g.to_dict()
        self.assertEqual(d["safe"], {"1,0": "C"})
        back = Grid.from_dict(d)
        self.assertEqual(back.safe, {"1,0": "C"})

    def test_safe_and_doors_both_preserved(self):
        # A safe door at (1,0) and a normal door at (3,2) survive together.
        g = _grid(_DOORS_ROWS, {"3,2": "U"}, safe={"1,0": "O"})
        back = Grid.from_dict(g.to_dict())
        self.assertEqual(back.safe, {"1,0": "O"})
        self.assertEqual(back.doors, {"3,2": "U"})

    def test_empty_safe_equivalent_to_none(self):
        g = _grid(_DOORS_ROWS, safe={})
        self.assertEqual(g.safe, {})
        self.assertFalse(g.is_safe_door(1, 0))
        # to_dict omits an empty object (like doors).
        self.assertNotIn("safe", g.to_dict())

    def test_old_constructor_positional_still_works(self):
        # AC12: the old Grid(name, width, height, cells, image, doors)
        # positional form still constructs; safe defaults to None.
        g = Grid("t", 4, 3, [list(r) for r in _DOORS_ROWS], None,
                 {"1,0": "O"})
        self.assertIsNone(g.safe)
        self.assertEqual(g.door_state_at(1, 0), "O")

    def test_sample_map_has_no_safe_doors(self):
        # AC2(a)/AC16: build_sample_map is byte-unchanged — no safe doors
        # are pre-marked (a GM must author them).
        g = build_sample_map()
        self.assertIsNone(g.safe)
        for x, y in ((5, 5), (10, 4), (9, 7)):
            self.assertFalse(g.is_safe_door(x, y))
            self.assertIsNone(g.safe_door_state_at(x, y))


class TestSafeDoorPostInitValidation(unittest.TestCase):
    """AC1(d): __post_init__ rejects a safe key on floor/wall, out of
    bounds, a bad state char, a malformed key, and (mutual exclusion, I1)
    a key present in BOTH doors and safe."""

    def test_rejects_safe_on_floor_cell(self):
        rows = [["floor", "doorway", "wall"]]
        with self.assertRaises(ValueError):
            _grid(rows, safe={"0,0": "C"})  # (0,0) is floor

    def test_rejects_safe_on_wall_cell(self):
        rows = [["wall", "doorway", "floor"]]
        with self.assertRaises(ValueError):
            _grid(rows, safe={"0,0": "C"})  # (0,0) is wall

    def test_rejects_out_of_bounds_key(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, safe={"9,9": "C"})

    def test_rejects_bad_state_char(self):
        for bad in ("L", "U", "X", "c", ""):
            with self.assertRaises(ValueError):
                _grid(_DOORS_ROWS, safe={"1,0": bad})

    def test_rejects_malformed_key_no_comma(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, safe={"10": "C"})

    def test_rejects_malformed_key_non_numeric(self):
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, safe={"a,b": "C"})

    def test_rejects_key_in_both_doors_and_safe(self):
        # I1 mutual exclusion: the same cell may not be both a normal door
        # (recorded) and a safe door.
        with self.assertRaises(ValueError):
            _grid(_DOORS_ROWS, {"1,0": "L"}, safe={"1,0": "C"})

    def test_accepts_valid_states(self):
        for st in ("C", "O"):
            g = _grid(_DOORS_ROWS, safe={"1,0": st})
            self.assertEqual(g.safe_door_state_at(1, 0), st)

    def test_none_bypasses_validation(self):
        g = _grid([["floor", "wall"]])
        self.assertIsNone(g.safe)


class TestSafeDoorAccessors(unittest.TestCase):
    """AC1(e): is_safe_door / safe_door_state_at / is_safe_door_closed."""

    def test_is_safe_door_false_for_non_doorway(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "C"})
        self.assertFalse(g.is_safe_door(0, 0))  # floor
        self.assertFalse(g.is_safe_door(2, 0))  # wall

    def test_is_safe_door_false_for_normal_door(self):
        g = _grid(_DOORS_ROWS, {"1,0": "O"})  # recorded normal door
        self.assertFalse(g.is_safe_door(1, 0))
        self.assertIsNone(g.safe_door_state_at(1, 0))

    def test_safe_door_state_at(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "O"})
        self.assertEqual(g.safe_door_state_at(1, 0), "O")
        self.assertIsNone(g.safe_door_state_at(0, 0))  # non-safe
        self.assertIsNone(g.safe_door_state_at(3, 2))  # plain doorway

    def test_is_safe_door_closed(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "C", "3,2": "O"})
        self.assertTrue(g.is_safe_door_closed(1, 0))   # C → closed
        self.assertFalse(g.is_safe_door_closed(3, 2))  # O → open
        self.assertFalse(g.is_safe_door_closed(0, 0))  # not a safe door

    def test_door_state_at_none_for_safe_door_cell(self):
        # §4.4: a safe door has no NORMAL door state (safe record is the
        # only door record for the cell) — door_state_at returns None.
        g = _grid(_DOORS_ROWS, safe={"1,0": "C"})
        self.assertIsNone(g.door_state_at(1, 0))


class TestSetSafeDoor(unittest.TestCase):
    def test_set_safe_door_materializes_and_sets(self):
        g = _grid(_DOORS_ROWS)
        self.assertIsNone(g.safe)
        g.set_safe_door(1, 0, "C")
        self.assertEqual(g.safe, {"1,0": "C"})

    def test_set_safe_door_rejects_non_doorway(self):
        g = _grid(_DOORS_ROWS)
        with self.assertRaises(ValueError):
            g.set_safe_door(0, 0, "C")  # floor

    def test_set_safe_door_rejects_bad_state(self):
        g = _grid(_DOORS_ROWS)
        with self.assertRaises(ValueError):
            g.set_safe_door(1, 0, "L")

    def test_set_safe_door_rejects_recorded_normal_door(self):
        # I1: set_safe_door is a model-level invariant tripwire — a cell
        # with a RECORDED normal door must be converted (the session mark
        # path drops the normal record first), never overwritten in place.
        g = _grid(_DOORS_ROWS, {"1,0": "L"})
        with self.assertRaises(ValueError):
            g.set_safe_door(1, 0, "C")


class TestUnmarkSafeDoor(unittest.TestCase):
    """§3.5 / A6: unmark reverts a safe door to a NORMAL door, preserving
    open/closed (C → U, O → O)."""

    def test_unmark_closed_reverts_to_u(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "C"})
        g.unmark_safe_door(1, 0)
        self.assertIsNone(g.safe)  # last safe door removed → None
        self.assertEqual(g.doors, {"1,0": "U"})  # closed + unlocked
        self.assertEqual(g.door_state_at(1, 0), "U")

    def test_unmark_open_reverts_to_o(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "O"})
        g.unmark_safe_door(1, 0)
        self.assertIsNone(g.safe)
        self.assertEqual(g.doors, {"1,0": "O"})  # open preserved
        self.assertEqual(g.door_state_at(1, 0), "O")

    def test_unmark_keeps_other_states(self):
        g = _grid(_DOORS_ROWS, {"3,2": "L"}, safe={"1,0": "C"})
        g.unmark_safe_door(1, 0)
        self.assertEqual(g.doors, {"1,0": "U", "3,2": "L"})
        self.assertIsNone(g.safe)

    def test_unmark_rejects_non_safe(self):
        g = _grid(_DOORS_ROWS, {"1,0": "L"})
        with self.assertRaises(ValueError):
            g.unmark_safe_door(1, 0)  # a normal door, not a safe door


class TestSafeDoorSyncAfterCellSet(unittest.TestCase):
    """§3.5: painting floor/wall over a safe door deletes its record — the
    same single sync point as normal doors."""

    def test_paint_floor_over_safe_door_deletes(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "C"})
        g.cells[0][1] = "floor"
        g.sync_doors_after_cell_set(1, 0)
        self.assertIsNone(g.safe)
        self.assertFalse(g.is_safe_door(1, 0))

    def test_paint_wall_over_safe_door_deletes(self):
        g = _grid(_DOORS_ROWS, safe={"3,2": "O"})
        g.cells[2][3] = "wall"
        g.sync_doors_after_cell_set(3, 2)
        self.assertIsNone(g.safe)
        self.assertFalse(g.is_safe_door(3, 2))

    def test_paint_doorway_over_safe_door_keeps(self):
        # Repainting an existing safe doorway leaves the safe state intact.
        g = _grid(_DOORS_ROWS, safe={"1,0": "O"})
        g.cells[0][1] = "doorway"  # already a doorway
        g.sync_doors_after_cell_set(1, 0)
        self.assertEqual(g.safe, {"1,0": "O"})

    def test_paint_floor_only_affects_that_key(self):
        g = _grid(_DOORS_ROWS, safe={"1,0": "C", "3,2": "O"})
        g.cells[0][1] = "floor"
        g.sync_doors_after_cell_set(1, 0)
        self.assertEqual(g.safe, {"3,2": "O"})  # the other safe door stays


class TestSafeDoorWire(unittest.TestCase):
    """§8.1 / AC1 / AC10 / I5: doors skips safe cells (disjoint, jointly
    covering all doorways); safe_for_wire emits full or None."""

    def test_doors_for_wire_excludes_safe_cells(self):
        g = build_sample_map()
        g.set_safe_door(5, 5, "C")
        self.assertEqual(g.doors_for_wire(), {"10,4": "L", "9,7": "L"})
        self.assertEqual(g.safe_for_wire(), {"5,5": "C"})
        # disjoint and jointly covering every doorway:
        self.assertEqual(set(g.doors_for_wire()) | set(g.safe_for_wire()),
                         {"5,5", "10,4", "9,7"})

    def test_wire_partition_with_multiple_safe_doors(self):
        g = build_sample_map()
        g.set_safe_door(5, 5, "C")
        g.set_safe_door(10, 4, "O")
        self.assertEqual(g.doors_for_wire(), {"9,7": "L"})
        self.assertEqual(g.safe_for_wire(), {"5,5": "C", "10,4": "O"})

    def test_safe_for_wire_none_without_safe_doors(self):
        g = build_sample_map()
        self.assertIsNone(g.safe_for_wire())
        # and doors is byte-identical to the no-safe build (nothing skipped)
        self.assertEqual(g.doors_for_wire(),
                         {"5,5": "L", "10,4": "L", "9,7": "L"})

    def test_to_dict_emits_safe_only_when_present(self):
        g = build_sample_map()
        self.assertNotIn("safe", g.to_dict())
        g.set_safe_door(9, 7, "O")
        d = g.to_dict()
        self.assertEqual(d["safe"], {"9,7": "O"})
        self.assertNotIn("doors", d)  # no recorded normal doors


if __name__ == "__main__":
    unittest.main()
