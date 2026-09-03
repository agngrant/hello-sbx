"""Visibility unit tests (explored-map spec §3, §12 AC1–AC5, invariants S-A…S-F).

Pure tests of :mod:`app.visibility` — no session involved.

Worked examples W1–W4 from spec §3.2 are pinned **string-for-string**. Two
oracle notes (spec §12/AC2 doctrine: "the re-derivation is the oracle: if the
literal and the re-derivation ever disagree, the re-derivation defines
correctness and the literal is corrected (it is a fixture, not the
definition)"):

* **W4/AC2 — cell (6,7):** the spec's 12-row literal lists (6,7) as H with a
  total of 69 S cells. That literal is wrong: (6,7) is a WALL cell of the
  row-7 wall section, and the spec's own (S2) rule — a wall is visible iff
  one of its four in-bounds orthogonal walkable neighbours has LOS — makes
  it S, because its north neighbour (6,6) (the "single middle-room floor"
  the same §3.2 passage counts as S) has clear line of sight through the
  doorway (5,5). The corrected literal here is 70 S / 0 E / 122 H, exactly
  as the (S1)/(S2) algorithm + the real ``has_line_of_sight`` produce.
  Every other spot cell in the spec's AC2 battery holds verbatim.
* **W2:** both the base grid and the variant match the spec literals exactly.

The **independent oracle** used throughout is a fresh re-derivation of the
spec's rules (S1 + S2) written directly in these tests on top of the real
``has_line_of_sight`` — it does not call ``visible_cells`` /
``build_visibility_mask``, so a bug in the implementation (e.g. an accidental
8-neighbourhood wall reveal or a broken corner rule) fails here even if a
pinned literal were mistyped.
"""

from __future__ import annotations

import copy
import unittest

from app.models import Grid
from app.pathfinding import _closed_doors, has_line_of_sight
from app.visibility import build_visibility_mask, visible_cells


# ---------------------------------------------------------------------------
# Shared helpers (spec §12 "Shared helper") + grid/mask utilities
# ---------------------------------------------------------------------------


def mask_rows(mask: list[str]) -> str:
    """``"SSE…\\nHHE…"`` pretty-print for failure output."""
    return "\n".join(mask)


def cell(mask: list[str], x: int, y: int) -> str:
    """The tier char at grid column ``x``, row ``y`` (``cells[y][x]``)."""
    return mask[y][x]


def make_grid(rows: list[str]) -> Grid:
    """Grid from ``W``/``.``/``D`` row strings."""
    cells = [
        ["wall" if c == "W" else ("doorway" if c == "D" else "floor") for c in row]
        for row in rows
    ]
    return Grid(name="test", width=len(rows[0]), height=len(rows), cells=cells)


# ---------------------------------------------------------------------------
# The independent oracle (spec §12: the designated definition of correctness)
# ---------------------------------------------------------------------------


def _walkable(g: Grid, x: int, y: int) -> bool:
    return 0 <= x < g.width and 0 <= y < g.height and g.cells[y][x] in ("floor", "doorway")


def oracle_walkable(g: Grid, x: int, y: int, closed) -> bool:
    """Door-aware walkable: in-bounds, floor/doorway, and NOT a closed door."""
    if not (0 <= x < g.width and 0 <= y < g.height):
        return False
    if g.cells[y][x] not in ("floor", "doorway"):
        return False
    return (x, y) not in closed


def oracle_visible(g: Grid, pos: tuple[int, int]) -> set[tuple[int, int]]:
    """Re-derive the S-set straight from the spec's rules + real LOS.

    (S1) every WALKABLE cell c (floor or OPEN doorway — a CLOSED door is not
    walkable) with ``has_line_of_sight(g, pos, c)`` — plus the anchor itself,
    unconditionally (S-B: the walkability predicate is waived for the anchor,
    even when its cell is a wall — edge case E6);
    (S2) every WALL cell w AND every CLOSED door (D5: a closed door's face is
    revealed exactly like a wall) that has a walkable 4-orthogonal neighbour
    in the (S1) set.

    Door-aware: LOS is the real :func:`has_line_of_sight` with the grid's
    closed-door set, so a closed door blocks exactly like a wall (incl.
    corner-cut) and an open door is transparent. Deliberately independent of
    :func:`app.visibility.visible_cells` (re-implements the same rules).
    """
    closed = _closed_doors(g)
    seen: set[tuple[int, int]] = {pos}
    # (S1) walkable (floor / open-doorway) cells in sight.
    for y in range(g.height):
        for x in range(g.width):
            c = g.cells[y][x]
            if c == "wall" or (x, y) in closed:
                continue
            if (x, y) == pos or has_line_of_sight(g, pos, (x, y), closed):
                seen.add((x, y))
    # (S2) wall cells and closed doors (D5) revealed via a walkable 4-neighbour.
    for y in range(g.height):
        for x in range(g.width):
            c = g.cells[y][x]
            if c != "wall" and (x, y) not in closed:
                continue
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if oracle_walkable(g, nx, ny, closed) and (nx, ny) in seen:
                    seen.add((x, y))
                    break
    return seen


def oracle_mask(
    g: Grid, explored: set[tuple[int, int]] | None, pos: tuple[int, int] | None
) -> list[str]:
    """Re-derive the full S/E/H mask straight from the spec's rules."""
    vis = oracle_visible(g, pos) if pos is not None else set()
    explored = explored or set()
    return [
        "".join(
            "S" if (x, y) in vis else "E" if (x, y) in explored else "H"
            for x in range(g.width)
        )
        for y in range(g.height)
    ]


def mask_counts(mask: list[str]) -> tuple[int, int, int]:
    s = sum(r.count("S") for r in mask)
    e = sum(r.count("E") for r in mask)
    h = sum(r.count("H") for r in mask)
    return s, e, h


def assert_well_formed(self, mask: list[str], grid: Grid) -> None:
    """len == height, every row == width chars, alphabet S/E/H."""
    self.assertIsInstance(mask, list)
    self.assertEqual(len(mask), grid.height)
    for row in mask:
        self.assertIsInstance(row, str)
        self.assertEqual(len(row), grid.width)
        self.assertTrue(set(row) <= {"S", "E", "H"}, f"bad alphabet in {row!r}")


# Worked-example grids (spec §3.2) ------------------------------------------
#
# W2 — "the corner that kills flood fill", 4x3, token at (1,0):
#   y=0: . T W .          variant: (1,1) repainted floor (one elbow opens)
#   y=1: . W W .          y=0: . T W .
#   y=2: . . . .          y=1: . . W .
#                            y=2: . . . .

W2_BASE = [".TW.", ".WW.", "...."]
W2_BASE_POS = (1, 0)
W2_BASE_MASK = ["SSSH", "SSHH", "SHHH"]  # spec §3.2 literal (matches)

W2_VARIANT = [".TW.", "..W.", "...."]
W2_VARIANT_POS = (1, 0)
W2_VARIANT_MASK = ["SSSH", "SSSH", "SSHH"]  # spec §3.2 literal (matches)


# ---------------------------------------------------------------------------
# W2 — the corner-cut case (AC5)
# ---------------------------------------------------------------------------


class TestWorkedExampleW2(unittest.TestCase):
    def test_base_corner_cut_mask_exact(self):
        # (a) Both diagonal elbows (2,0) and (1,1) are walls → corner cut →
        # the wall (2,1) is NOT in the S set. Spec literal, string-for-string.
        g = make_grid(W2_BASE)
        self.assertEqual(visible_cells(g, W2_BASE_POS),
                         {(x, y) for y, row in enumerate(W2_BASE_MASK)
                          for x, ch in enumerate(row) if ch == "S"})
        self.assertEqual(build_visibility_mask(g, set(), W2_BASE_POS),
                         W2_BASE_MASK,
                         f"\ngot:\n{mask_rows(build_visibility_mask(g, set(), W2_BASE_POS))}\n"
                         f"want:\n{mask_rows(W2_BASE_MASK)}")
        self.assertNotIn((2, 1), visible_cells(g, W2_BASE_POS))

    def test_variant_one_elbow_open_mask_exact(self):
        # (b) (1,1) repainted floor: the diagonal (1,0)->(2,1) now grazes a
        # single wall corner (elbow (2,0) wall + (1,1) floor → passes), so
        # the wall (2,1) is S; (1,1) and (1,2) become S. (2,2) stays H.
        g = make_grid(W2_VARIANT)
        self.assertEqual(build_visibility_mask(g, set(), W2_VARIANT_POS),
                         W2_VARIANT_MASK,
                         f"\ngot:\n{mask_rows(build_visibility_mask(g, set(), W2_VARIANT_POS))}\n"
                         f"want:\n{mask_rows(W2_VARIANT_MASK)}")
        self.assertIn((2, 1), visible_cells(g, W2_VARIANT_POS))
        self.assertNotIn((2, 2), visible_cells(g, W2_VARIANT_POS))

    def test_wall_cells_rederived_from_real_los(self):
        # (c) Oracle equivalence (the real check): for EVERY wall cell w,
        # w is S  iff  some 4-orthogonal walkable neighbour of w has LOS.
        # Plus: every walkable cell c is S iff it has LOS (anchor waived).
        for rows, pos in ((W2_BASE, W2_BASE_POS), (W2_VARIANT, W2_VARIANT_POS)):
            with self.subTest(rows=rows):
                g = make_grid(rows)
                vis = visible_cells(g, pos)
                for y in range(g.height):
                    for x in range(g.width):
                        if g.cells[y][x] == "wall":
                            neighbours = [
                                (nx, ny)
                                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                                if _walkable(g, nx, ny)
                            ]
                            expects_s = any(has_line_of_sight(g, pos, n) for n in neighbours)
                            self.assertEqual(
                                (x, y) in vis, expects_s,
                                f"wall {(x, y)} revealed={ (x, y) in vis } "
                                f"but neighbours-with-LOS say {expects_s}",
                            )
                        else:
                            expects_s = (x, y) == pos or has_line_of_sight(g, pos, (x, y))
                            self.assertEqual(
                                (x, y) in vis, expects_s,
                                f"walkable {(x, y)} revealed={(x, y) in vis} "
                                f"but LOS says {expects_s}",
                            )


# ---------------------------------------------------------------------------
# W1 — open room: light the room + its wall faces; hide what is behind walls
# ---------------------------------------------------------------------------


class TestWorkedExampleW1(unittest.TestCase):
    def test_open_room_lights_room_and_wall_faces_only(self):
        # 9x7: open room x1-3 x y1-5; solid wall column x4; sealed second
        # room x5-7 x y1-5 (no door). Token at (2,2) in the first room.
        rows = [
            "WWWWWWWWWW",
            "W...W...WW",
            "W...W...WW",
            "W...W...WW",
            "W...W...WW",
            "W...W...WW",
            "WWWWWWWWWW",
        ]
        g = make_grid(rows)
        pos = (2, 2)
        vis = visible_cells(g, pos)
        mask = build_visibility_mask(g, set(), pos)
        # The whole first room is S (no wall on any internal Bresenham line):
        for y in range(1, 6):
            for x in range(1, 4):
                self.assertEqual(cell(mask, x, y), "S", f"room floor {(x, y)}")
        # Every wall cell 4-adjacent to the room is S (the bounding faces):
        for x in (0, 4):
            for y in range(1, 6):
                self.assertEqual(cell(mask, x, y), "S", f"wall face {(x, y)}")
        for y in (0, 6):
            for x in range(1, 4):
                self.assertEqual(cell(mask, x, y), "S", f"wall face {(x, y)}")
        # The second room behind the door-less wall: floors and walls alike
        # are H (no line reaches them; no facing neighbour of theirs is S).
        for y in range(1, 6):
            for x in range(5, 8):
                self.assertEqual(cell(mask, x, y), "H", f"sealed room {(x, y)}")
        for y in (0, 6):
            for x in range(5, 8):
                self.assertEqual(cell(mask, x, y), "H", f"sealed border {(x, y)}")
        # Oracle cross-check over the whole grid:
        self.assertEqual(mask, oracle_mask(g, set(), pos))
        self.assertEqual(vis, oracle_visible(g, pos))


# ---------------------------------------------------------------------------
# W4 — sample-dungeon spawn mask (AC2)
# ---------------------------------------------------------------------------

# W4 — token at (1,1) on the 16x12 sample dungeon.
#
# NOTE (door-features spec A1/AC7 + the AC2 "oracle wins" doctrine): the
# explored-map literal was pinned when EVERY doorway was an open, transparent
# gap (70 S / 122 H). Doors are now CLOSED + LOCKED by default (D1/A2), so a
# closed door blocks sight exactly like a wall: from (1,1) the (5,5) doorway
# no longer transmits sight, the single middle-room floor (6,6) is no longer
# S, and the (5,4)/(5,6) wall faces lose their only seen neighbour. The
# literal is REGENERATED for the closed-default doors — 68 S / 124 H — as the
# door-aware (S1)+(S2) algorithm + the real has_line_of_sight produce (the
# oracle re-derivation below is the source of truth). Every other spot cell
# the spec's AC2 battery listed as S is unchanged; the cells that flip are
# exactly the ones behind the now-closed door. With ALL three doors open the
# mask returns to the legacy 70 S / 122 H (see TestWorkedExampleW4AllOpen).
W4_GRID_ROWS = [
    "WWWWWWWWWWWWWWWW",
    "W....W....W....W",
    "W....W....W....W",
    "W....W....W....W",
    "W....W....D....W",
    "W....D....W....W",
    "W....W....W....W",
    "W....WWWWDWWWW.W",
    "W....W.........W",
    "W....W.........W",
    "W....W.........W",
    "WWWWWWWWWWWWWWWW",
]
W4_POS = (1, 1)
W4_MASK = [
    "HSSSSHHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "HSSSSHHHHHHHHHHH",
]
# Legacy all-doorways-OPEN mask (pre-door-features behaviour, 70 S / 122 H) —
# the regression pin for "opening all doors reproduces the old mask exactly"
# (door-features spec AC7(d)).
W4_MASK_ALL_OPEN = [
    "HSSSSHHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSSHHHHHHHHH",
    "SSSSSSSHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "SSSSSSHHHHHHHHHH",
    "HSSSSHHHHHHHHHHH",
]


class TestWorkedExampleW4(unittest.TestCase):
    def test_spawn_mask_literal_exact(self):
        from app.grid import build_sample_map

        g = build_sample_map()
        mask = build_visibility_mask(g, set(), W4_POS)
        self.assertEqual(mask, W4_MASK,
                         f"\ngot:\n{mask_rows(mask)}\nwant:\n{mask_rows(W4_MASK)}")
        s, e, h = mask_counts(mask)
        self.assertEqual((s, e, h), (68, 0, 124))
        self.assertEqual(mask, oracle_mask(g, set(), W4_POS))

    def test_spot_cells_and_independent_rederivation(self):
        from app.grid import build_sample_map

        g = build_sample_map()
        mask = build_visibility_mask(g, set(), W4_POS)
        # Doors are CLOSED + LOCKED by default: from (1,1) the (5,5) doorway
        # blocks sight, so the middle room beyond it is hidden.
        for x, y in [(1, 1), (4, 10), (5, 5), (5, 4), (5, 6),
                     (0, 1), (4, 0), (0, 10)]:
            self.assertEqual(cell(mask, x, y), "S", f"spot S {(x, y)}")
        # (5,5): the CLOSED door's face is revealed by the D5 wall-face rule
        # (it is a doorway cell, but S2 — not the S1 walkable rule — reveals it).
        self.assertEqual(g.cells[5][5], "doorway")
        self.assertEqual(cell(mask, 5, 5), "S")
        # (6,6): the single middle-room floor is H now — behind the closed
        # (5,5) door the whole middle room is hidden (AC7: closed-door far
        # side is H when never explored).
        self.assertEqual(cell(mask, 6, 6), "H")
        for x in range(6, 10):
            for y in range(1, 7):
                if g.cells[y][x] == "floor":
                    self.assertNotEqual(cell(mask, x, y), "S",
                                        f"middle-room floor {(x, y)} must be H")
        # (6,7): a wall whose only seen-neighbour (6,6) is now hidden -> H.
        self.assertEqual(cell(mask, 6, 7), "H")
        # Every cell beyond the closed door (and the far room / border)
        # stays hidden.
        for x, y in [(6, 5), (7, 7), (7, 5), (9, 5), (9, 7), (10, 4),
                     (12, 5), (12, 9), (14, 8), (6, 0), (0, 0),
                     (6, 6), (6, 7)]:
            self.assertEqual(cell(mask, x, y), "H", f"spot H {(x, y)}")
        # Independent door-aware re-derivation (the oracle) equals the mask's S-set.
        s_set = {(x, y) for y, row in enumerate(mask) for x, ch in enumerate(row)
                 if ch == "S"}
        self.assertEqual(s_set, oracle_visible(g, W4_POS))
        self.assertEqual(s_set, visible_cells(g, W4_POS))


class TestWorkedExampleW4AllOpen(unittest.TestCase):
    """AC7(d) regression pin: with ALL three doors OPEN, the mask equals the
    pre-door-features literal (70 S / 122 H) exactly — opening the doors
    reproduces today's open-doorway behaviour byte-for-byte."""

    def test_all_open_reproduces_legacy_mask(self):
        from app.grid import build_sample_map

        g = build_sample_map()
        g.doors = {"5,5": "O", "10,4": "O", "9,7": "O"}
        mask = build_visibility_mask(g, set(), W4_POS)
        self.assertEqual(mask, W4_MASK_ALL_OPEN,
                         f"\ngot:\n{mask_rows(mask)}\nwant:\n{mask_rows(W4_MASK_ALL_OPEN)}")
        self.assertEqual(mask_counts(mask), (70, 0, 122))
        # (6,6) is S again (sight through the open door) and (5,5) is S via
        # S1 (an open door is walkable + in sight, like today's doorway).
        self.assertEqual(cell(mask, 6, 6), "S")
        self.assertEqual(cell(mask, 5, 5), "S")


# ---------------------------------------------------------------------------
# build_visibility_mask semantics (S/E/H tiers, frozen pos=None, None explored)
# ---------------------------------------------------------------------------


class TestBuildVisibilityMask(unittest.TestCase):
    def setUp(self) -> None:
        self.g = make_grid(W4_GRID_ROWS)

    def test_well_formed(self):
        mask = build_visibility_mask(self.g, set(), W4_POS)
        assert_well_formed(self, mask, self.g)
        # A grid that is not the sample (5x5 with a door) too:
        g2 = make_grid(["WWWWW", "W.W.W", "W.D.W", "W.W.W", "WWWWW"])
        assert_well_formed(self, build_visibility_mask(g2, set(), (1, 1)), g2)

    def test_none_explored_is_empty(self):
        a = build_visibility_mask(self.g, None, W4_POS)
        b = build_visibility_mask(self.g, set(), W4_POS)
        self.assertEqual(a, b)

    def test_pos_none_is_frozen_eh_only(self):
        explored = {(1, 1), (4, 10), (0, 5)}
        mask = build_visibility_mask(self.g, explored, None)
        assert_well_formed(self, mask, self.g)
        flat = "".join(mask)
        self.assertNotIn("S", flat)  # no anchor → no sight anywhere
        for y in range(self.g.height):
            for x in range(self.g.width):
                expect = "E" if (x, y) in explored else "H"
                self.assertEqual(cell(mask, x, y), expect, f"{(x, y)}")
        # Even explored cells that are walls render E (memory of geometry):
        self.g.cells[1][1]  # floor; add a wall to the explored set too:
        explored.add((5, 1))  # a col-5 wall
        mask2 = build_visibility_mask(self.g, explored, None)
        self.assertEqual(cell(mask2, 5, 1), "E")
        # No explored at all and no anchor: everything H.
        self.assertEqual(build_visibility_mask(self.g, None, None),
                         ["H" * self.g.width] * self.g.height)

    def test_s_wins_over_e(self):
        # Explore everything first (pos=None keeps it frozen), then stand on
        # a cell: the visible cells are S even though every one is explored.
        explored = {(x, y) for y in range(self.g.height)
                    for x in range(self.g.width)}
        pos = (4, 9)
        mask = build_visibility_mask(self.g, explored, pos)
        vis = visible_cells(self.g, pos)
        for y in range(self.g.height):
            for x in range(self.g.width):
                expect = "S" if (x, y) in vis else "E"
                self.assertEqual(cell(mask, x, y), expect, f"{(x, y)}")
        self.assertNotIn("H", "".join(mask))

    def test_independent_oracle_on_small_grid_with_memory(self):
        # Re-derive the ENTIRE expected mask in the test by calling
        # has_line_of_sight directly (do not trust the implementation), then
        # compare against build_visibility_mask — including the E/H tiers
        # from a hand-picked explored set.
        g = make_grid([
            "WWWWWW",
            "W..DW.",
            "W..WWW",
            "W..W.W",
            "WWWWWW",
        ])
        pos = (1, 1)
        explored = {(4, 1), (5, 1), (1, 3)}  # includes H cells + one S cell
        expected = oracle_mask(g, explored, pos)
        got = build_visibility_mask(g, explored, pos)
        self.assertEqual(got, expected,
                         f"\ngot:\n{mask_rows(got)}\nwant:\n{mask_rows(expected)}")
        # The oracle must disagree with "no memory" (E cells exist):
        self.assertNotEqual(expected, oracle_mask(g, None, pos))
        # And visible_cells itself must agree with the oracle S-set:
        self.assertEqual(visible_cells(g, pos), oracle_visible(g, pos))


# ---------------------------------------------------------------------------
# Invariants S-A … S-F (spec §3.2)
# ---------------------------------------------------------------------------


class TestInvariants(unittest.TestCase):
    def setUp(self) -> None:
        from app.grid import build_sample_map

        self.sample = build_sample_map()
        self.small = make_grid(W2_BASE)

    def _walkable_cells(self, g: Grid):
        return [(x, y) for y in range(g.height) for x in range(g.width)
                if g.cells[y][x] in ("floor", "doorway")]

    def test_sa_determinism(self):
        for g, pos in ((self.sample, (1, 1)), (self.small, W2_BASE_POS),
                       (self.sample, (5, 5))):
            with self.subTest(grid=g.name, pos=pos):
                a = visible_cells(g, pos)
                b = visible_cells(g, pos)
                self.assertEqual(a, b)
                self.assertIsInstance(a, set)
                # Fresh but equal grid → same set (pure over the data):
                g2 = Grid(name=g.name, width=g.width, height=g.height,
                          cells=copy.deepcopy(g.cells))
                self.assertEqual(visible_cells(g2, pos), a)
                # The wire encoding is deterministic too:
                m1 = build_visibility_mask(g, {(0, 0)}, pos)
                m2 = build_visibility_mask(g, {(0, 0)}, pos)
                self.assertEqual(m1, m2)

    def test_sb_token_cell_always_s(self):
        # Any in-bounds pos — floor, doorway, and even a WALL (E6):
        for g in (self.sample, self.small):
            for y in range(g.height):
                for x in range(g.width):
                    self.assertIn((x, y), visible_cells(g, (x, y)),
                                  f"{g.name} {(x, y)}")
                    self.assertEqual(cell(build_visibility_mask(
                        g, set(), (x, y)), x, y), "S", f"{g.name} {(x, y)}")

    def test_sb_isolated_token_on_all_wall_grid(self):
        # E6/E16: a token surrounded by walls still sees exactly its own
        # square (the anchor's neighbours are all walls, so nothing else).
        g = make_grid(["WWW", "WWW", "WWW"])
        self.assertEqual(visible_cells(g, (1, 1)), {(1, 1)})
        self.assertEqual(build_visibility_mask(g, set(), (1, 1)),
                         ["HHH", "HSH", "HHH"])

    def test_sc_symmetry(self):
        # a in vis(b) iff b in vis(a) for WALKABLE a, b (Bresenham + the
        # blocker test are symmetric; the anchor must be walkable — a closed
        # door is NOT walkable, so its anchor is the D5 face rule, not S1).
        for g in (self.sample, self.small):
            cells = self._walkable_cells(g)
            for a in ((1, 1), (4, 10), (12, 9)):
                if a not in cells:
                    continue
                for b in ((4, 1), (1, 10), (12, 5), (13, 1), (0, 0), (3, 2)):
                    if a == b or b not in cells:
                        continue
                    with self.subTest(grid=g.name, a=a, b=b):
                        self.assertEqual(a in visible_cells(g, b),
                                         b in visible_cells(g, a))

    def test_sd_doorways_seen_only_via_s1(self):
        # A doorway is walkable → S iff has_line_of_sight (the anchor waiver
        # aside); never via (S2). Check every doorway of the sample map from
        # two anchors.
        for pos in ((1, 1), (7, 2)):
            vis = visible_cells(self.sample, pos)
            for y in range(self.sample.height):
                for x in range(self.sample.width):
                    if self.sample.cells[y][x] != "doorway":
                        continue
                    self.assertEqual((x, y) in vis,
                                     has_line_of_sight(self.sample, pos, (x, y)),
                                     f"door {(x, y)} from {pos}")

    def test_se_cost_bound_sanity(self):
        for g, pos in ((self.sample, (1, 1)), (self.small, W2_BASE_POS)):
            vis = visible_cells(g, pos)
            self.assertLessEqual(len(vis), g.width * g.height)
            self.assertGreater(len(vis), 0)
        # The sample spawn sees the left region + its wall/door faces, far
        # less than the full 192 cells (bounded by LOS + the closed (5,5)
        # door, not connectivity). Closed-by-default: 68 S (was 70 when the
        # doorway was an open, transparent gap).
        self.assertEqual(len(visible_cells(self.sample, (1, 1))), 68)

    def test_sf_grid_reads_only(self):
        for g, pos in ((self.sample, (1, 1)), (self.small, W2_BASE_POS)):
            before_cells = copy.deepcopy(g.cells)
            before_w, before_h = g.width, g.height
            visible_cells(g, pos)
            build_visibility_mask(g, {(0, 0)}, pos)
            build_visibility_mask(g, None, None)
            self.assertEqual(g.cells, before_cells)
            self.assertEqual((g.width, g.height), (before_w, before_h))


if __name__ == "__main__":
    unittest.main()
