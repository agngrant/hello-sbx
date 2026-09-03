"""Door state model tests (door-features spec §3, AC1, AC2, AC13).

Pure unit tests of :class:`app.models.Grid`'s additive ``doors`` field:
round-trip via ``to_dict``/``from_dict``, the closed+locked default
(``doors=None``), ``__post_init__`` validation (doorway-only / in-bounds /
valid state), and the derived accessors + paint-sync helper.
"""

from __future__ import annotations

import unittest

from app.grid import build_sample_map
from app.models import DOOR_STATES, Grid


def _grid(rows, doors=None, name="t"):
    height = len(rows)
    width = len(rows[0])
    return Grid(
        name=name, width=width, height=height,
        cells=[list(r) for r in rows], doors=doors,
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


if __name__ == "__main__":
    unittest.main()
