"""Detection tests (stdlib unittest; Iteration 3 — the key tests).

Images are built as exact pixel rows and encoded with ``encode_png``, then
fed to ``detect_grid`` with ``cols=image_w`` / ``rows=image_h`` so there is
NO resize ambiguity: pixel == cell.
"""

from __future__ import annotations

import unittest

from app.detection import classify_doors, detect_grid
from app.imaging import decode_image, encode_png, to_gray
from app.models import Grid

# Reuse the hand-crafted multi-channel PNG builder from the imaging tests
# (it emits Average/Paeth-filtered scanlines that ``encode_png`` never
# produces, so the BUG-004 decode path is genuinely exercised here).
from tests.test_imaging import _build_png

DARK = (0, 0, 0, 255)
LIGHT = (255, 255, 255, 255)


def _map_a_rows() -> list[list[tuple[int, int, int, int]]]:
    """16x12: dark(0) walls = 1px border + 2px interior vertical wall
    (cols 8-9, rows 1-10) with a 1-cell gap at (8,5) — plus stability bites
    at (9,4),(9,5),(9,6) so the 3x3 majority filter keeps the gap open.
    Light(255) = floor."""
    w, h = 16, 12

    def is_wall(x: int, y: int) -> bool:
        border = x in (0, w - 1) or y in (0, h - 1)
        col8 = x == 8 and 1 <= y <= 10 and (x, y) != (8, 5)
        col9 = x == 9 and 1 <= y <= 10 and (x, y) not in ((9, 4), (9, 5), (9, 6))
        return border or col8 or col9

    return [[DARK if is_wall(x, y) else LIGHT for x in range(w)] for y in range(h)]


def _png(rows: list[list[tuple[int, int, int, int]]]) -> bytes:
    return encode_png(len(rows[0]), len(rows), rows)


#: Pinned final 16x12 grid for map A (verified against the deterministic
#: pipeline: Otsu t=128, 3x3 majority, doorway heuristic).
MAP_A_EXPECTED = [
    "WWWWWWWWWWWWWWWW",
    "WW.....WWWW...WW",
    "W.......WW.....W",
    "W.......WW.....W",
    "W.......W......W",
    "W.......D......W",
    "W.......W......W",
    "W.......WW.....W",
    "W.......WW.....W",
    "W.......WW.....W",
    "WW.....WWWW...WW",
    "WWWWWWWWWWWWWWWW",
]


def _expected_cells(rows: list[str]) -> list[list[str]]:
    return [[{"W": "wall", ".": "floor", "D": "doorway"}[ch] for ch in row] for row in rows]


class TestDetectGrid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png = _png(_map_a_rows())
        cls.grid = detect_grid(cls.png, name="Map A", cols=16, rows=12)

    def test_shape_and_name(self):
        self.assertEqual(self.grid.name, "Map A")
        self.assertEqual(self.grid.width, 16)
        self.assertEqual(self.grid.height, 12)
        self.assertIsInstance(self.grid, Grid)
        self.assertEqual(len(self.grid.cells), 12)
        self.assertTrue(all(len(row) == 16 for row in self.grid.cells))

    def test_exact_final_grid(self):
        self.assertEqual(self.grid.cells, _expected_cells(MAP_A_EXPECTED))

    def test_wall_floor_doorway_cells(self):
        # The gap cell is the doorway.
        self.assertEqual(self.grid.cells[5][8], "doorway")
        # Wall cells: border + interior wall line.
        self.assertEqual(self.grid.cells[0][0], "wall")
        self.assertEqual(self.grid.cells[0][8], "wall")
        self.assertEqual(self.grid.cells[11][15], "wall")
        self.assertEqual(self.grid.cells[4][8], "wall")
        # Floor cells.
        self.assertEqual(self.grid.cells[5][1], "floor")
        self.assertEqual(self.grid.cells[3][5], "floor")
        walls = sum(row.count("wall") for row in self.grid.cells)
        floors = sum(row.count("floor") for row in self.grid.cells)
        doors = sum(row.count("doorway") for row in self.grid.cells)
        self.assertEqual(walls, 76)
        self.assertEqual(floors, 115)
        self.assertEqual(doors, 1)

    def test_undersized_grid_rejected(self):
        with self.assertRaises(ValueError):
            detect_grid(self.png, name="bad", cols=16, rows=0)


class TestDarkIsWallFlag(unittest.TestCase):
    """16x12 2px-light-border / dark-interior image: exactly 50/50, so
    NEITHER interpretation trips the auto-invert and the flag is
    observable in the final grid (dark becomes floor and vice versa)."""

    @classmethod
    def setUpClass(cls):
        w, h = 16, 12
        rows = [
            [
                LIGHT if (x in (0, 1, w - 2, w - 1) or y in (0, 1, h - 2, h - 1))
                else DARK
                for x in range(w)
            ]
            for y in range(h)
        ]
        cls.png = _png(rows)

    def test_dark_is_wall_true(self):
        grid = detect_grid(self.png, name="A", cols=16, rows=12, dark_is_wall=True)
        self.assertEqual(grid.cells[5][5], "wall")   # dark interior -> wall
        self.assertEqual(grid.cells[0][0], "floor")  # light border -> floor
        walls = sum(row.count("wall") for row in grid.cells)
        self.assertEqual(walls, 96)  # 50%: no auto-invert

    def test_dark_is_wall_false_inverts(self):
        grid = detect_grid(self.png, name="B", cols=16, rows=12, dark_is_wall=False)
        self.assertEqual(grid.cells[5][5], "floor")  # dark interior -> floor
        self.assertEqual(grid.cells[0][0], "wall")   # light border -> wall
        self.assertEqual(grid.cells[1][1], "wall")   # light border row 1 -> wall
        walls = sum(row.count("wall") for row in grid.cells)
        # 96 border pixels + 4 diagonal corner bites ((2,2),(13,2),(2,9),
        # (13,9)) that the 3x3 majority keeps; still < 60%, no auto-invert.
        self.assertEqual(walls, 100)


class TestAutoInvert(unittest.TestCase):
    def test_mostly_dark_image_auto_inverts(self):
        """1px light border (27%) / dark interior (73% > 60%): the default
        dark_is_wall=True would make 73% walls, so auto-invert flips it —
        final grid is the light border as walls."""
        w, h = 16, 12
        rows = [
            [LIGHT if (x in (0, w - 1) or y in (0, h - 1)) else DARK for x in range(w)]
            for y in range(h)
        ]
        grid = detect_grid(_png(rows), name="dark", cols=16, rows=12)
        self.assertEqual(grid.cells[0][0], "wall")    # light border -> wall
        self.assertEqual(grid.cells[5][5], "floor")   # dark interior -> floor
        walls = sum(row.count("wall") for row in grid.cells)
        self.assertEqual(walls, 52)
        self.assertLess(walls / (w * h), 0.6)


class TestClassifyDoors(unittest.TestCase):
    def test_opposite_wall_gap_found(self):
        cells = [
            ["wall"] * 5,
            ["wall", "floor", "wall", "floor", "wall"],
            ["wall", "floor", "wall", "floor", "wall"],
            ["wall", "floor", "wall", "floor", "wall"],
            ["wall"] * 5,
        ]
        out = classify_doors(cells, 5, 5)
        doors = [(x, y) for y in range(5) for x in range(5) if out[y][x] == "doorway"]
        # Wall line x=2 splits the grid. The floor columns on either side
        # (x=1 and x=3, rows 1-3) each have walls on BOTH horizontal sides
        # (the x=2 line plus the x=0 / x=4 borders) → doorways. The x=2
        # cells themselves are walls, so never doorways.
        self.assertEqual(
            sorted(doors),
            [(1, 1), (1, 2), (1, 3), (3, 1), (3, 2), (3, 3)],
        )

    def test_out_of_bounds_is_not_wall(self):
        # Floor corner between two in-bounds walls: no opposite pair
        # (out-of-bounds counts as not-wall), so it stays floor.
        cells = [["floor", "wall", "wall"], ["wall", "wall", "wall"],
                 ["wall", "wall", "wall"]]
        out = classify_doors(cells, 3, 3)
        self.assertEqual(out[0][0], "floor")

    def test_horizontal_gap(self):
        # Two vertical wall stubs (x=1 and x=3, rows 0 and 2). Gaps:
        # (1,1) and (3,1) — walls on BOTH vertical neighbours; the corridor
        # cells (2,0) and (2,2) — walls on BOTH horizontal neighbours.
        cells = [
            ["floor", "wall", "floor", "wall", "floor"],
            ["floor", "floor", "floor", "floor", "floor"],
            ["floor", "wall", "floor", "wall", "floor"],
        ]
        out = classify_doors(cells, 5, 3)
        doors = [(x, y) for y in range(3) for x in range(5) if out[y][x] == "doorway"]
        self.assertEqual(sorted(doors), [(1, 1), (2, 0), (2, 2), (3, 1)])

    def test_gap_needs_opposite_walls(self):
        # (0,0) has two wall neighbours, but they are ADJACENT (down and
        # right), not opposite; the other two sides are out-of-bounds and
        # count as not-wall → it stays floor, not a doorway.
        cells = [
            ["floor", "wall", "wall"],
            ["wall", "wall", "wall"],
            ["wall", "wall", "wall"],
        ]
        out = classify_doors(cells, 3, 3)
        self.assertEqual(out[0][0], "floor")

    def test_does_not_mutate_input(self):
        cells = [
            ["wall", "wall", "wall"],
            ["wall", "floor", "wall"],
            ["wall", "wall", "wall"],
        ]
        snapshot = [row[:] for row in cells]
        classify_doors(cells, 3, 3)
        self.assertEqual(cells, snapshot)

    def test_preserves_doorway_cells(self):
        cells = [
            ["wall", "wall", "wall"],
            ["wall", "doorway", "wall"],
            ["wall", "wall", "wall"],
        ]
        out = classify_doors(cells, 3, 3)
        self.assertEqual(out[1][1], "doorway")


# ---------------------------------------------------------------------------
# BUG-004 detection-correctness end-to-end: an RGB (color type 2) map PNG
# whose scanlines use the Paeth / Average filters (never emitted by the
# stdlib encoder, which writes filter 0) must decode to the exact intended
# pixels and therefore classify the walls + doorway correctly. Under the old
# buggy channel indexing the green/blue channels corrupted the gray, moving
# the detected walls/doorway to the wrong cells.
# ---------------------------------------------------------------------------


class TestMultiChannelDetection(unittest.TestCase):
    """BUG-004 end-to-end: the SAME map A pixels (already pinned to
    ``MAP_A_EXPECTED`` via the stdlib filter-0 encoder) re-emitted as a
    multi-channel (RGB, color type 2) PNG whose scanlines use the Paeth /
    Average filters. Those filters are never produced by ``encode_png`` (it
    writes filter 0), so this genuinely exercises the buggy decode path. The
    decoded grid must match the pinned map-A layout (walls + the (8,5) gap as
    a doorway) — under the old channel indexing the RGB decode corrupted the
    gray and the walls/doorway landed in the wrong cells."""

    @classmethod
    def setUpClass(cls):
        rows = _map_a_rows()  # 16x12, DARK(0) walls / LIGHT(255) floor
        cls.w, cls.h = 16, 12

        # Map each pixel to DISTINCT channels (so the BUG-004 green/blue
        # channel-offset bug is actually sensitive) while keeping the luminance
        # bimodal: dark walls -> luma ~5, light floor -> luma ~250.
        def rgb(v: int) -> tuple[int, int, int]:
            return (8, 4, 0) if v < 128 else (255, 250, 240)

        recon = []
        for y in range(cls.h):
            row = []
            for x in range(cls.w):
                r, g, b, _a = rows[y][x]
                R, G, B = rgb(r)
                row.extend((R, G, B))
            recon.append(row)
        # Row 0 = None (no up/left refs); the rest alternate Paeth/Average.
        filters = [0 if y == 0 else (4 if y % 2 else 3) for y in range(cls.h)]
        cls.png = _build_png(cls.w, cls.h, 3, 2, recon, filters)
        cls.grid = detect_grid(cls.png, name="Multi", cols=cls.w, rows=cls.h)
        # Intended gray of the exact RGB image (bimodal: luma ~5 / ~250).
        intended_rows = [
            [rgb(rows[y][x][0]) + (255,) for x in range(cls.w)]
            for y in range(cls.h)
        ]
        cls.intended_gray = to_gray(intended_rows)

    def test_decodes_to_intended_gray(self):
        # The decode must be EXACT: the gray image is the intended bimodal
        # layout (no green/blue channel corruption from the filter bug).
        from app.imaging import decode_image
        _, _, decoded = decode_image(self.png)
        self.assertEqual(to_gray(decoded), self.intended_gray)

    def test_grid_matches_pinned_map_a(self):
        # Same pixels -> same detected grid as the stdlib filter-0 test.
        self.assertEqual(self.grid.cells, _expected_cells(MAP_A_EXPECTED))

    def test_doorway_at_gap(self):
        self.assertEqual(self.grid.cells[5][8], "doorway")


if __name__ == "__main__":
    unittest.main()
