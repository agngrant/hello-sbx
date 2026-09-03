"""Generated-map generator tests (generated-maps spec §9) — pure generator.

Drives ``app.generation.generate_grid`` and verifies the invariants I1–I7
empirically from the returned cells alone (the generator returns no room/door
metadata — everything is recovered here). The helpers below are the shared,
importable QA primitives from spec §9.1 and are also imported by the
endpoint/e2e tests.

Acceptance sweep: every criterion runs over the fixed
``SIZES``/``SEEDS`` matrix (a subset per criterion where the spec notes one)
so a single regression anywhere in the algorithm is caught.
"""

from __future__ import annotations

import unittest

from app.generation import generate_grid
from app.models import Grid
from app.pathfinding import find_path, is_valid_step

# Fixed sweep (spec §9): cheap enough to run per criterion.
SIZES = [(8, 8), (8, 16), (16, 8), (12, 12), (24, 16), (40, 30), (60, 60)]
SEEDS = [0, 1, 42, 1337, -7]

WALKABLE = ("floor", "doorway")


# ---------------------------------------------------------------------------
# Shared helpers (spec §9.1) — importable from the API/e2e tests too.
# ---------------------------------------------------------------------------


def gen(cols: int, rows: int, seed: int = 0, name: str = "t") -> Grid:
    """Generate a grid (convenience used by every sweep)."""
    return generate_grid(cols, rows, name, seed)


def room_components(cells: list[list[str]], w: int, h: int) -> dict[int, list]:
    """Flood fill 4-dir over cells == 'floor' ONLY (doorways count as walls).

    Returns ``{component_id: [(x, y), ...]}`` — one entry per room (a solid
    floor rectangle separated from the others by at least one wall cell).
    """
    comps: dict[int, list] = {}
    seen: set = set()
    for y in range(h):
        for x in range(w):
            if cells[y][x] != "floor" or (x, y) in seen:
                continue
            cid = len(comps)
            seen.add((x, y))
            stack = [(x, y)]
            comp: list = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (
                        0 <= nx < w
                        and 0 <= ny < h
                        and (nx, ny) not in seen
                        and cells[ny][nx] == "floor"
                    ):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            comps[cid] = comp
    return comps


def room_id_map(cells: list[list[str]], w: int, h: int) -> dict:
    """(cell → component_id) for EVERY cell.

    Floor cells map to their room id; every non-floor cell maps to ``None``.
    (Building the full map keeps the pair helpers below trivial to consume.)
    """
    rid: dict = {}
    for cid, comp in room_components(cells, w, h).items():
        for cell in comp:
            rid[cell] = cid
    for y in range(h):
        for x in range(w):
            if (x, y) not in rid:
                rid[(x, y)] = None
    return rid


def _room_pairs_for(
    cells: list[list[str]], w: int, h: int, kind: str
) -> set:
    """Room-id pairs separated by a ``kind`` cell with FLOOR on both
    opposite sides.

    * ``kind == "wall"``    → ``wall_adjacent_pairs`` (solid wall edge)
    * ``kind == "doorway"`` → ``doorway_pairs``      (a carved door)

    For every cell of ``kind``: if the LEFT and RIGHT neighbours are both
    floor → pair ``{room(x-1, y), room(x+1, y)}``; if the UP and DOWN
    neighbours are both floor → pair ``{room(x, y-1), room(x, y+1)}``.
    Pairs whose two ids are equal are skipped (impossible across a
    separating cell, but explicit).
    """
    rid = room_id_map(cells, w, h)
    out: set = set()
    for y in range(h):
        for x in range(w):
            if cells[y][x] != kind:
                continue
            if x > 0 and x < w - 1 and cells[y][x - 1] == "floor" and cells[y][x + 1] == "floor":
                a, b = rid[(x - 1, y)], rid[(x + 1, y)]
                if a is not None and b is not None and a != b:
                    out.add(frozenset((a, b)))
            if y > 0 and y < h - 1 and cells[y - 1][x] == "floor" and cells[y + 1][x] == "floor":
                a, b = rid[(x, y - 1)], rid[(x, y + 1)]
                if a is not None and b is not None and a != b:
                    out.add(frozenset((a, b)))
    return out


def wall_adjacent_pairs(cells: list[list[str]], w: int, h: int) -> set:
    """Room pairs separated by a SOLID wall edge.

    For every wall cell with floor on both opposite sides (left+right, or
    up+down), the pair of the two rooms on those sides. Returns a set of
    ``frozenset`` room-id pairs.
    """
    return _room_pairs_for(cells, w, h, "wall")


def doorway_pairs(cells: list[list[str]], w: int, h: int) -> set:
    """Room pairs connected by a ``"doorway"`` cell.

    For each doorway cell with floor on both opposite sides (up+down or
    left+right), the room pair it bridges. Returns a set of ``frozenset``
    room-id pairs. (Every generated door satisfies the side check by C4; the
    check here keeps the helper honest for hand-made grids too.)
    """
    return _room_pairs_for(cells, w, h, "doorway")


def floor_reachable(cells: list[list[str]], w: int, h: int) -> set:
    """The set of FLOOR cells reachable from the FIRST floor cell.

    BFS from the first floor cell (row-major) over walkable cells
    (``floor`` + ``doorway``, 4-dir — if 4-dir connects all floors, 8-dir
    does too). Returns the reached cells filtered to ``floor`` so the C5
    marquee assertion ``floor_reachable == set(all floor cells)`` is exact.
    """
    start = None
    for y in range(h):
        for x in range(w):
            if cells[y][x] == "floor":
                start = (x, y)
                break
        if start is not None:
            break
    if start is None:
        return set()
    seen = {start}
    stack = [start]
    while stack:
        cx, cy = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (
                0 <= nx < w
                and 0 <= ny < h
                and (nx, ny) not in seen
                and cells[ny][nx] in WALKABLE
            ):
                seen.add((nx, ny))
                stack.append((nx, ny))
    return {cell for cell in seen if cells[cell[1]][cell[0]] == "floor"}


def count_doors(cells: list[list[str]], w: int, h: int) -> int:
    return sum(row.count("doorway") for row in cells)


def _sweep_cases():
    """Yield (cols, rows, seed) over the full SIZES × SEEDS matrix."""
    for (cols, rows) in SIZES:
        for seed in SEEDS:
            yield cols, rows, seed


# ---------------------------------------------------------------------------
# C1 — Exact dimensions.
# ---------------------------------------------------------------------------


class TestC1Dimensions(unittest.TestCase):
    def test_exact_dimensions(self):
        for cols, rows in SIZES:
            grid = gen(cols, rows, 0)
            self.assertEqual(grid.width, cols, f"{cols}x{rows}")
            self.assertEqual(grid.height, rows, f"{cols}x{rows}")
            self.assertEqual(len(grid.cells), rows, f"{cols}x{rows}")
            self.assertTrue(
                all(len(row) == cols for row in grid.cells), f"{cols}x{rows}"
            )


# ---------------------------------------------------------------------------
# C2 — Outer border all wall.
# ---------------------------------------------------------------------------


class TestC2Border(unittest.TestCase):
    def test_border_all_wall(self):
        for cols, rows, seed in _sweep_cases():
            grid = gen(cols, rows, seed)
            cells, w, h = grid.cells, cols, rows
            for x in range(w):
                self.assertEqual(cells[0][x], "wall", f"{cols}x{rows} s={seed} top {x}")
                self.assertEqual(cells[h - 1][x], "wall", f"{cols}x{rows} s={seed} bottom {x}")
            for y in range(h):
                self.assertEqual(cells[y][0], "wall", f"{cols}x{rows} s={seed} left {y}")
                self.assertEqual(cells[y][w - 1], "wall", f"{cols}x{rows} s={seed} right {y}")


# ---------------------------------------------------------------------------
# C3 — Cell vocabulary.
# ---------------------------------------------------------------------------


class TestCVocabulary(unittest.TestCase):
    def test_cell_vocabulary_and_floor_exists(self):
        for cols, rows, seed in _sweep_cases():
            grid = gen(cols, rows, seed)
            flat = [c for row in grid.cells for c in row]
            self.assertTrue(all(c in ("floor", "wall", "doorway") for c in flat),
                            f"{cols}x{rows} s={seed}")
            self.assertIn("floor", flat, f"{cols}x{rows} s={seed}")


# ---------------------------------------------------------------------------
# C4 — Doors sit in walls (geometry).
# ---------------------------------------------------------------------------


class TestC4DoorGeometry(unittest.TestCase):
    def test_doors_have_opposite_walls_and_walkable_other_pair(self):
        for cols, rows, seed in _sweep_cases():
            cells, w, h = gen(cols, rows, seed).cells, cols, rows
            for y in range(h):
                for x in range(w):
                    if cells[y][x] != "doorway":
                        continue
                    up_wall = cells[y - 1][x] == "wall"
                    down_wall = cells[y + 1][x] == "wall"
                    left_wall = cells[y][x - 1] == "wall"
                    right_wall = cells[y][x + 1] == "wall"
                    where = f"{cols}x{rows} s={seed} door ({x},{y})"
                    # Walls on BOTH opposite sides (up+down OR left+right).
                    self.assertTrue(
                        (up_wall and down_wall) or (left_wall and right_wall),
                        where,
                    )
                    # Walkable on the OTHER opposite pair.
                    if up_wall and down_wall:
                        self.assertIn(cells[y][x - 1], WALKABLE, where)
                        self.assertIn(cells[y][x + 1], WALKABLE, where)
                    else:
                        self.assertIn(cells[y - 1][x], WALKABLE, where)
                        self.assertIn(cells[y + 1][x], WALKABLE, where)


# ---------------------------------------------------------------------------
# C5 — Connectivity invariant (the marquee test).
# ---------------------------------------------------------------------------


class TestC5Connectivity(unittest.TestCase):
    def test_all_floor_reachable(self):
        for cols, rows, seed in _sweep_cases():
            grid = gen(cols, rows, seed)
            cells, w, h = grid.cells, cols, rows
            all_floor = {
                (x, y) for y in range(h) for x in range(w) if cells[y][x] == "floor"
            }
            self.assertEqual(floor_reachable(cells, w, h), all_floor,
                             f"{cols}x{rows} s={seed}")


# ---------------------------------------------------------------------------
# C6 — Sparseness (tree bound).
# ---------------------------------------------------------------------------


class TestC6Sparseness(unittest.TestCase):
    def test_doors_eq_rooms_minus_one(self):
        for cols, rows, seed in _sweep_cases():
            cells, w, h = gen(cols, rows, seed).cells, cols, rows
            rooms = room_components(cells, w, h)
            doors = count_doors(cells, w, h)
            self.assertEqual(doors, len(rooms) - 1, f"{cols}x{rows} s={seed}")
            self.assertGreaterEqual(doors, 3, f"{cols}x{rows} s={seed}")


# ---------------------------------------------------------------------------
# C7 — Detour property (no door between every adjacent room).
# ---------------------------------------------------------------------------


class TestC7Detour(unittest.TestCase):
    def test_some_adjacent_rooms_have_no_door(self):
        for cols, rows, seed in _sweep_cases():
            cells, w, h = gen(cols, rows, seed).cells, cols, rows
            doorless = wall_adjacent_pairs(cells, w, h) - doorway_pairs(cells, w, h)
            self.assertTrue(doorless, f"{cols}x{rows} s={seed}")

    def test_a_star_detours_through_an_intermediate_room(self):
        # Behavioral variant (spec §9 C7): for 24x16 seed 42, pick a door-less
        # adjacent room pair (A, B), the floor cells nearest the shared wall,
        # and assert the real A* route detours through >= 3 rooms.
        cols, rows, seed = 24, 16, 42
        grid = gen(cols, rows, seed)
        # A1 (door-features): carved doorways are now closed+locked by
        # default, so OPEN every door first ("O") to reproduce the original
        # connectivity the detour test was written against (an open doorway
        # is exactly today's walkable gap).
        grid.doors = {
            f"{x},{y}": "O" for y in range(rows) for x in range(cols)
            if grid.cells[y][x] == "doorway"
        }
        cells, w, h = grid.cells, cols, rows
        rooms = room_components(cells, w, h)
        doorless = wall_adjacent_pairs(cells, w, h) - doorway_pairs(cells, w, h)
        self.assertTrue(doorless)
        # Deterministic pick: the lexicographically first door-less pair.
        pair = sorted(doorless, key=lambda p: (min(p), max(p)))[0]
        a_id, b_id = sorted(pair)
        # Floor cells a in A, b in B nearest the shared wall = minimum
        # Manhattan-distance floor pair (tie-break on coordinates).
        best = min(
            (
                (abs(ca[0] - cb[0]) + abs(ca[1] - cb[1]), ca, cb)
                for ca in rooms[a_id]
                for cb in rooms[b_id]
            ),
            key=lambda t: (t[0], t[1], t[2]),
        )
        a, b = best[1], best[2]
        path = find_path(grid, a, b)
        self.assertIsNotNone(path, "no A* route between adjacent rooms")
        # Every step is a legal king move (no corner cuts, in-bounds walkable).
        for i in range(len(path) - 1):
            self.assertTrue(is_valid_step(grid, path[i], path[i + 1]))
        rid = room_id_map(cells, w, h)
        rooms_on_path = {rid[cell] for cell in path if rid[cell] is not None}
        self.assertGreaterEqual(len(rooms_on_path), 3,
                                f"route {a}->{b} did not detour (rooms={rooms_on_path})")


# ---------------------------------------------------------------------------
# C8 — Seed reproducibility.
# ---------------------------------------------------------------------------


class TestC8Reproducibility(unittest.TestCase):
    def test_same_seed_same_size_identical(self):
        for cols, rows in SIZES:
            for seed in SEEDS:
                g1 = gen(cols, rows, seed)
                g2 = gen(cols, rows, seed)
                self.assertEqual(g1.cells, g2.cells, f"{cols}x{rows} s={seed}")

    def test_different_seeds_differ_24x16(self):
        self.assertNotEqual(gen(24, 16, 1).cells, gen(24, 16, 2).cells)

    def test_name_does_not_affect_geometry(self):
        self.assertEqual(
            generate_grid(24, 16, "A", 42).cells,
            generate_grid(24, 16, "B", 42).cells,
        )


# ---------------------------------------------------------------------------
# Defensive validation: bad sizes raise ValueError.
# ---------------------------------------------------------------------------


class TestValidation(unittest.TestCase):
    def test_rejects_out_of_range_and_bools(self):
        for cols, rows in [(7, 10), (61, 10), (10, 7), (10, 61)]:
            with self.assertRaises(ValueError):
                gen(cols, rows, 0)
        # bools are NOT ints — reject them explicitly.
        for cols, rows in [(True, 10), (10, False), (True, True)]:
            with self.assertRaises(ValueError):
                gen(cols, rows, 0)
        # non-int types are rejected too.
        for cols, rows in [("8", 10), (10, 8.0)]:
            with self.assertRaises(ValueError):
                gen(cols, rows, 0)


if __name__ == "__main__":
    unittest.main()
