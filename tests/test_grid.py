"""Grid helper tests (stdlib unittest; Iteration 1/2)."""

from __future__ import annotations

import unittest

from app.grid import (
    SAMPLE_MAP_ID,
    build_sample_map,
    from_dict,
    get_cell,
    in_bounds,
    set_cell,
    to_dict,
)
from app.models import Grid

SMALL = Grid(
    name="tiny",
    width=3,
    height=2,
    cells=[
        ["wall", "floor", "wall"],
        ["floor", "wall", "doorway"],
    ],
)


class TestInBounds(unittest.TestCase):
    def test_corners(self):
        self.assertTrue(in_bounds(SMALL, 0, 0))
        self.assertTrue(in_bounds(SMALL, 2, 0))
        self.assertTrue(in_bounds(SMALL, 0, 1))
        self.assertTrue(in_bounds(SMALL, 2, 1))

    def test_out_of_bounds(self):
        self.assertFalse(in_bounds(SMALL, -1, 0))
        self.assertFalse(in_bounds(SMALL, 0, -1))
        self.assertFalse(in_bounds(SMALL, 3, 0))
        self.assertFalse(in_bounds(SMALL, 0, 2))
        self.assertFalse(in_bounds(SMALL, 10, 10))


class TestGetSetCell(unittest.TestCase):
    def test_get_cells(self):
        self.assertEqual(get_cell(SMALL, 0, 0), "wall")
        self.assertEqual(get_cell(SMALL, 1, 0), "floor")
        self.assertEqual(get_cell(SMALL, 2, 1), "doorway")

    def test_set_cell_mutates_and_returns(self):
        self.assertEqual(set_cell(SMALL, 1, 0, "wall"), "wall")
        self.assertEqual(get_cell(SMALL, 1, 0), "wall")
        self.assertEqual(set_cell(SMALL, 0, 1, "doorway"), "doorway")
        self.assertEqual(get_cell(SMALL, 0, 1), "doorway")

    def test_out_of_bounds_raises(self):
        with self.assertRaises(IndexError):
            get_cell(SMALL, 3, 0)
        with self.assertRaises(IndexError):
            get_cell(SMALL, 0, 2)
        with self.assertRaises(IndexError):
            set_cell(SMALL, -1, 0, "floor")


class TestSerialization(unittest.TestCase):
    def test_roundtrip(self):
        data = to_dict(SMALL)
        self.assertEqual(data["width"], 3)
        self.assertEqual(data["height"], 2)
        self.assertEqual(data["name"], "tiny")
        self.assertIsNone(data["image"])
        self.assertEqual(data["cells"], SMALL.cells)
        back = from_dict(data)
        self.assertEqual(back, SMALL)
        self.assertIsNot(back.cells, SMALL.cells)  # fresh structure, not aliased
        self.assertIsNot(back.cells[0], SMALL.cells[0])

    def test_roundtrip_preserves_image(self):
        g = Grid(
            name="crypt",
            width=SMALL.width,
            height=SMALL.height,
            cells=[row[:] for row in SMALL.cells],
            image="crypt.png",
        )
        back = from_dict(to_dict(g))
        self.assertEqual(back.image, "crypt.png")
        self.assertEqual(back, g)

    def test_grid_shape_validated(self):
        with self.assertRaises(ValueError):
            Grid(name="bad", width=2, height=2, cells=[["wall", "floor"]])
        with self.assertRaises(ValueError):
            Grid(name="bad", width=2, height=2,
                 cells=[["wall", "floor"], ["wall", "floor", "wall"]])
        with self.assertRaises(ValueError):
            Grid(name="bad", width=2, height=1, cells=[["wall", "lava"]])


class TestSampleMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = build_sample_map()

    def test_shape(self):
        self.assertEqual((self.g.width, self.g.height), (16, 12))
        self.assertIsNone(self.g.image)
        self.assertEqual(self.g.name, "Sample Dungeon")

    def test_all_valid_cell_types(self):
        for row in self.g.cells:
            for cell in row:
                self.assertIn(cell, ("floor", "wall", "doorway"))

    def test_border_is_wall(self):
        for x in range(self.g.width):
            self.assertEqual(self.g.cells[0][x], "wall")
            self.assertEqual(self.g.cells[self.g.height - 1][x], "wall")
        for y in range(self.g.height):
            self.assertEqual(self.g.cells[y][0], "wall")
            self.assertEqual(self.g.cells[y][self.g.width - 1], "wall")

    def test_expected_doorways(self):
        self.assertEqual(get_cell(self.g, 5, 5), "doorway")   # gap in left interior wall
        self.assertEqual(get_cell(self.g, 10, 4), "doorway")  # gap in right interior wall
        self.assertEqual(get_cell(self.g, 9, 7), "doorway")   # gap in horizontal wall

    def test_exactly_three_doorways(self):
        doors = [
            (x, y)
            for y in range(self.g.height)
            for x in range(self.g.width)
            if self.g.cells[y][x] == "doorway"
        ]
        self.assertEqual(len(doors), 3)
        self.assertEqual(set(doors), {(5, 5), (10, 4), (9, 7)})

    def test_interior_walls_present(self):
        # left interior wall: (5,1)-(5,4) and (5,6)-(5,10)
        for y in (1, 2, 3, 4, 6, 7, 8, 9, 10):
            self.assertEqual(get_cell(self.g, 5, y), "wall")
        # right interior wall: (10,1)-(10,3) and (10,6)-(10,7)
        for y in (1, 2, 3, 6, 7):
            self.assertEqual(get_cell(self.g, 10, y), "wall")
        # horizontal wall: row 7, cols 6-13 (minus the (9,7) doorway)
        for x in (6, 7, 8, 10, 11, 12, 13):
            self.assertEqual(get_cell(self.g, x, 7), "wall")

    def test_cell_counts(self):
        walls = sum(row.count("wall") for row in self.g.cells)
        floors = sum(row.count("floor") for row in self.g.cells)
        doors = sum(row.count("doorway") for row in self.g.cells)
        self.assertEqual(walls, 73)
        self.assertEqual(floors, 116)
        self.assertEqual(doors, 3)
        self.assertEqual(walls + floors + doors, 16 * 12)  # 192 total cells

    def test_every_doorway_is_a_gap_between_opposite_walls(self):
        """A doorway sits in a wall line: its two opposite orthogonal
        neighbors are walls (matches the detection heuristic, §7.7)."""
        g = self.g
        for (x, y) in [(5, 5), (10, 4), (9, 7)]:
            self.assertTrue(in_bounds(g, x - 1, y) and in_bounds(g, x + 1, y))
            self.assertTrue(in_bounds(g, x, y - 1) and in_bounds(g, x, y + 1))
        # (5,5): gap in the LEFT VERTICAL wall (col 5) → up/down are walls
        self.assertEqual(get_cell(g, 5, 4), "wall")
        self.assertEqual(get_cell(g, 5, 6), "wall")
        # (10,4): gap in the RIGHT VERTICAL wall (col 10) → up/down are walls
        self.assertEqual(get_cell(g, 10, 3), "wall")
        self.assertEqual(get_cell(g, 10, 5), "wall")
        # (9,7): gap in the HORIZONTAL wall (row 7) → left/right are walls
        self.assertEqual(get_cell(g, 8, 7), "wall")
        self.assertEqual(get_cell(g, 10, 7), "wall")

    def test_sample_id_constant(self):
        self.assertEqual(SAMPLE_MAP_ID, "sample-dungeon")


if __name__ == "__main__":
    unittest.main()
