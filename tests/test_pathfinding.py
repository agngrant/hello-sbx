"""Pathfinding tests (stdlib unittest; Iteration 4).

Covers the movement core rules (PROJECT.md §6): walkability, the
corner-cut anti-cheat rule, A* routing (open floor / full-wall block /
doorway gap), and Bresenham line of sight for the optional fog.
"""

from __future__ import annotations

import unittest

from app.models import Grid
from app.pathfinding import (
    find_path,
    has_line_of_sight,
    is_valid_step,
    walkable,
)


def make_grid(rows: list[list[str]], name: str = "test") -> Grid:
    """Build a :class:`Grid` from a list of rows of cell strings
    (``"floor"``, ``"wall"``, ``"doorway"``)."""
    height = len(rows)
    width = len(rows[0])
    for row in rows:
        if len(row) != width:
            raise ValueError(f"ragged test grid: {rows!r}")
    return Grid(name=name, width=width, height=height, cells=[list(r) for r in rows])


def check_path(
    case: unittest.TestCase,
    grid: Grid,
    path: list[tuple[int, int]],
    start: tuple[int, int],
    goal: tuple[int, int],
    doors: frozenset[tuple[int, int]] | None = None,
) -> None:
    """Assert a path is well-formed: endpoints, valid steps, walkable cells.

    ``doors`` is the optional closed-door set forwarded to ``is_valid_step``
    so a route through an OPEN door validates as legal (an empty set = no
    closed doors = every doorway open)."""
    case.assertIsNotNone(path)
    case.assertEqual(path[0], start)
    case.assertEqual(path[-1], goal)
    for a, b in zip(path, path[1:]):
        case.assertTrue(is_valid_step(grid, a, b, doors), f"illegal step {a} -> {b}")
    for (x, y) in path:
        case.assertIn(grid.cells[y][x], ("floor", "doorway"),
                      f"path visits non-walkable cell {(x, y)}")


# An EMPTY closed-door set = no door is closed = every doorway is open. This
# is how the A1-updated tests "open the door first" to preserve the original
# open-doorway assertion (door-features spec §13/AC5).
OPEN = frozenset()


class TestWalkable(unittest.TestCase):
    def setUp(self):
        self.grid = make_grid(
            [
                ["wall", "floor", "doorway"],
                ["floor", "wall", "floor"],
            ]
        )

    def test_floor_and_doorway_walkable(self):
        self.assertTrue(walkable(self.grid, 1, 0))   # floor
        # A1 (door-features): a BARE doorway is now CLOSED (locked) by default
        # → not walkable. Open the (2,0) door first (empty closed set) to
        # preserve the original "doorway is walkable" assertion.
        self.assertFalse(walkable(self.grid, 2, 0))  # closed door: blocked
        self.assertTrue(walkable(self.grid, 2, 0, OPEN))  # open door: walkable
        self.assertTrue(walkable(self.grid, 0, 1))   # floor

    def test_wall_blocked(self):
        self.assertFalse(walkable(self.grid, 0, 0))  # wall
        self.assertFalse(walkable(self.grid, 1, 1))  # wall

    def test_out_of_bounds_blocked(self):
        self.assertFalse(walkable(self.grid, -1, 0))
        self.assertFalse(walkable(self.grid, 3, 0))
        self.assertFalse(walkable(self.grid, 0, -1))
        self.assertFalse(walkable(self.grid, 0, 2))
        self.assertFalse(walkable(self.grid, 99, 99))


class TestIsValidStep(unittest.TestCase):
    def test_same_cell_invalid(self):
        grid = make_grid([["floor"]])
        self.assertFalse(is_valid_step(grid, (0, 0), (0, 0)))

    def test_non_adjacent_invalid(self):
        grid = make_grid([["floor", "floor", "floor"]])
        self.assertFalse(is_valid_step(grid, (0, 0), (2, 0)))

    def test_orthogonal_step_ok(self):
        grid = make_grid([["floor", "floor", "floor"]])
        self.assertTrue(is_valid_step(grid, (0, 0), (1, 0)))
        self.assertTrue(is_valid_step(grid, (1, 0), (0, 0)))

    def test_step_onto_wall_invalid(self):
        grid = make_grid([["floor", "wall"]])
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 0)))

    def test_corner_cut_forbidden_both_elbows(self):
        # (0,0) -> (1,1) diagonal: elbow (1,0) is wall → illegal.
        g1 = make_grid([
            ["floor", "wall"],
            ["floor", "floor"],
        ])
        self.assertFalse(is_valid_step(g1, (0, 0), (1, 1)))
        # elbow (0,1) is wall → also illegal.
        g2 = make_grid([
            ["floor", "floor"],
            ["wall", "floor"],
        ])
        self.assertFalse(is_valid_step(g2, (0, 0), (1, 1)))

    def test_diagonal_ok_when_both_elbows_walkable(self):
        grid = make_grid([
            ["floor", "floor"],
            ["floor", "floor"],
        ])
        self.assertTrue(is_valid_step(grid, (0, 0), (1, 1)))

    def test_elbows_may_be_doorways(self):
        grid = make_grid([
            ["floor", "doorway"],
            ["doorway", "floor"],
        ])
        # A1: closed door elbows block the diagonal; open the two doorway
        # elbows (empty closed set) to preserve the original assertion.
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 1)))       # closed
        self.assertTrue(is_valid_step(grid, (0, 0), (1, 1), OPEN))  # open


class TestFindPathOpenFloor(unittest.TestCase):
    def test_path_is_a_valid_sequence(self):
        grid = make_grid([["floor"] * 5 for _ in range(4)])
        path = find_path(grid, (0, 0), (4, 3))
        check_path(self, grid, path, (0, 0), (4, 3))
        # Octile-shortest: 4 diagonal + 1 orthogonal step → 5 cells.
        self.assertEqual(len(path), 5)

    def test_start_equals_goal(self):
        grid = make_grid([["floor", "wall"], ["floor", "floor"]])
        self.assertEqual(find_path(grid, (1, 1), (1, 1)), [(1, 1)])

    def test_diagonal_shortcut_used_when_legal(self):
        grid = make_grid([["floor", "floor"], ["floor", "floor"]])
        path = find_path(grid, (0, 0), (1, 1))
        self.assertEqual(path, [(0, 0), (1, 1)])

    def test_list_coordinates_accepted(self):
        grid = make_grid([["floor"] * 3])
        self.assertEqual(find_path(grid, [0, 0], [2, 0]),
                         [(0, 0), (1, 0), (2, 0)])


class TestFindPathBlocked(unittest.TestCase):
    def test_full_wall_column_blocks(self):
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
        ])
        self.assertIsNone(find_path(grid, (0, 0), (2, 0)))

    def test_full_wall_row_blocks(self):
        grid = make_grid([
            ["floor", "floor", "floor", "floor"],
            ["wall", "wall", "wall", "wall"],
            ["floor", "floor", "floor", "floor"],
        ])
        self.assertIsNone(find_path(grid, (1, 0), (1, 2)))

    def test_unwalkable_start_or_goal(self):
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "floor", "floor"],
        ])
        self.assertIsNone(find_path(grid, (1, 0), (2, 1)))  # start on wall
        self.assertIsNone(find_path(grid, (0, 1), (1, 0)))  # goal on wall
        self.assertIsNone(find_path(grid, (0, 0), (9, 9)))  # goal out of bounds

    def test_walls_seal_the_box_no_path(self):
        grid = make_grid([
            ["wall", "wall", "wall"],
            ["wall", "floor", "wall"],
            ["wall", "wall", "wall"],
        ])
        self.assertIsNone(find_path(grid, (1, 1), (0, 1)))


class TestFindPathDoorway(unittest.TestCase):
    def test_routes_through_the_door_gap(self):
        # Full wall column except a single doorway at (1,1): the path MUST
        # pass through it — proving an OPEN doorway is walkable. A1: the
        # door is closed by default (no path); opening it (empty closed set)
        # restores the original route-through-the-gap assertion.
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "doorway", "floor"],
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
        ])
        self.assertIsNone(find_path(grid, (0, 0), (2, 3)))          # closed: blocked
        path = find_path(grid, (0, 0), (2, 3), OPEN)                 # open
        self.assertIsNotNone(path)
        self.assertIn((1, 1), path)
        check_path(self, grid, path, (0, 0), (2, 3), OPEN)

    def test_door_diagonal_elbow_is_walkable(self):
        # Diagonal into the doorway from (0,0): elbow (1,0) is a wall →
        # forbidden; the path must approach the doorway orthogonally. A1:
        # open the (1,1) door (empty closed set) to keep the route.
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "doorway", "floor"],
            ["floor", "wall", "floor"],
        ])
        self.assertIsNone(find_path(grid, (0, 0), (2, 1)))           # closed
        path = find_path(grid, (0, 0), (2, 1), OPEN)                 # open
        self.assertIsNotNone(path)
        self.assertIn((1, 1), path)
        steps = list(zip(path, path[1:]))
        self.assertNotIn(((0, 0), (1, 1)), steps)
        check_path(self, grid, path, (0, 0), (2, 1), OPEN)

    def test_door_gap_fully_walled_is_blocked(self):
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
        ])
        self.assertIsNone(find_path(grid, (0, 1), (2, 1)))


class TestCornerCut(unittest.TestCase):
    def test_forbidden_step_never_expanded(self):
        # The corner-cut case from the spec: walls at (1,0) and (0,1),
        # start (0,0), goal (1,1) — the direct diagonal is illegal, and
        # (1,1) is sealed off from the rest of the map, so no path.
        grid = make_grid([
            ["floor", "wall"],
            ["wall", "floor"],
        ])
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 1)))
        self.assertIsNone(find_path(grid, (0, 0), (1, 1)))

    def test_diagonally_sealed_pocket_is_unreachable(self):
        # A floor pocket with walls on all four orthogonal neighbors can
        # only be entered by a diagonal from a corner — and every such
        # diagonal has a wall elbow, so the no-corner-cut rule makes the
        # pocket unreachable. BFS over legal steps reaches everything
        # walkable EXCEPT the pocket; find_path to it returns None.
        # (If corner cutting were allowed, (1,1)->(2,2) would "squeeze"
        # the character in — exactly what the rule forbids.)
        grid = make_grid([
            ["floor", "floor", "floor", "floor", "floor"],
            ["floor", "wall",  "wall",  "wall",  "floor"],
            ["floor", "wall",  "floor", "wall",  "floor"],
            ["floor", "wall",  "wall",  "wall",  "floor"],
            ["floor", "floor", "floor", "floor", "floor"],
        ])
        self.assertTrue(walkable(grid, 2, 2))  # the pocket is walkable
        self.assertIsNone(find_path(grid, (0, 0), (2, 2)))
        # Breadth-first reachability using only legal steps.
        start = (0, 0)
        reachable: set[tuple[int, int]] = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nxt = (current[0] + dx, current[1] + dy)
                    if nxt not in reachable and is_valid_step(grid, current, nxt):
                        reachable.add(nxt)
                        frontier.append(nxt)
        self.assertNotIn((2, 2), reachable)
        all_walkable = {(x, y) for y in range(grid.height)
                        for x in range(grid.width) if walkable(grid, x, y)}
        self.assertEqual(all_walkable - {(2, 2)}, reachable)

    def test_path_detours_around_wall_corner(self):
        # Wall at (1,0): the "short" diagonal (0,0)->(1,1) would cut the
        # corner — forbidden. A* must detour via (0,1)->(1,1)->(2,1),
        # which is longer (5 cells) than the 3-cell shortcut that does
        # not exist legally.
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "floor", "floor"],
            ["floor", "floor", "floor"],
        ])
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 1)))
        path = find_path(grid, (0, 0), (2, 0))
        self.assertIsNotNone(path)
        self.assertNotIn(((0, 0), (1, 1)), list(zip(path, path[1:])))
        check_path(self, grid, path, (0, 0), (2, 0))
        self.assertEqual(len(path), 5)  # forced detour, never the corner cut

    def test_detour_across_diagonal_gap_is_legal(self):
        # The corner-gap grid: a legal route (0,2)->(1,2)->(1,1) exists
        # because that diagonal's elbows are open — the rule only blocks
        # actual corner cutting, and A* still finds a safe path.
        grid = make_grid([
            ["wall", "wall", "floor"],
            ["wall", "floor", "floor"],
            ["floor", "floor", "floor"],
        ])
        path = find_path(grid, (0, 2), (1, 1))
        self.assertIsNotNone(path)
        check_path(self, grid, path, (0, 2), (1, 1))


class TestLineOfSight(unittest.TestCase):
    def test_same_cell(self):
        grid = make_grid([["wall", "floor"]])
        self.assertTrue(has_line_of_sight(grid, (1, 0), (1, 0)))

    def test_clear_orthogonal_line(self):
        grid = make_grid([["floor", "floor", "floor"]])
        self.assertTrue(has_line_of_sight(grid, (0, 0), (2, 0)))

    def test_wall_on_straight_line_blocks(self):
        grid = make_grid([["floor", "wall", "floor"]])
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 0)))

    def test_doorway_does_not_block_sight(self):
        # A1 (door-features): a CLOSED door DOES block sight (like a wall);
        # opening the (1,0) door (empty closed set) restores the original
        # "doorway does not block sight" assertion.
        grid = make_grid([["floor", "doorway", "floor"]])
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 0)))   # closed
        self.assertTrue(has_line_of_sight(grid, (0, 0), (2, 0), OPEN))  # open

    def test_wall_off_the_line_does_not_block(self):
        grid = make_grid([
            ["floor", "wall"],
            ["floor", "floor"],
        ])
        # (0,0) -> (1,1) line: digitized cells are (0,0), (1,1); the wall
        # at (1,0) is adjacent to the line, not on it.
        self.assertTrue(has_line_of_sight(grid, (0, 0), (1, 1)))

    def test_diagonal_line_through_wall(self):
        grid = make_grid([
            ["floor", "floor", "floor"],
            ["floor", "wall", "floor"],
            ["floor", "floor", "floor"],
        ])
        # (0,0) -> (2,2) passes through the center wall.
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 2)))

    def test_endpoints_do_not_block(self):
        grid = make_grid([
            ["wall", "floor", "floor"],
            ["wall", "floor", "floor"],
        ])
        # Viewing from the edge of the wall room: (1,0)->(1,1) is clear.
        self.assertTrue(has_line_of_sight(grid, (1, 0), (1, 1)))

    def test_diagonal_corner_cut_is_blocked(self):
        # Adversarial regression (three-tier awareness LOS, check #3a):
        # a diagonal sight line must NOT "cut" through the zero-width gap
        # between two wall corners — this mirrors the movement
        # no-corner-cut rule (``is_valid_step``). The Bresenham line has NO
        # wall cell on it (it only visits the diagonal), so the block must
        # come from the two WALL ELBOWS of the diagonal step, not from an
        # on-line wall. Both adjacent (squeezed) and the longer (2,2) line
        # must be blocked.
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["wall", "floor", "floor"],
            ["floor", "floor", "floor"],
        ])
        # (0,0)->(1,1): the single diagonal step touches wall elbows
        # (1,0) and (0,1) — squeezed between two wall corners.
        self.assertFalse(has_line_of_sight(grid, (0, 0), (1, 1)))
        # (0,0)->(2,2): the first diagonal step is the same squeeze.
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 2)))

    def test_diagonal_corner_cut_on_final_step_blocked(self):
        # Same rule, but the squeeze happens on the FINAL diagonal step
        # INTO the target cell (target endpoint never blocks, but the
        # elbows of the step still must).
        grid = make_grid([
            ["floor", "floor", "wall"],
            ["floor", "floor", "wall"],
            ["floor", "wall", "floor"],
        ])
        # (0,0)->(2,2) last diagonal step (1,1)->(2,2): elbows (2,1) and
        # (1,2) are both walls.
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 2)))

    def test_single_elbow_wall_does_not_block(self):
        # Guard against over-blocking: a diagonal that grazes ONE wall
        # corner (only one elbow a wall) is NOT a corner cut and still has
        # clear sight (the wall is adjacent to the line, not on it). This is
        # the existing "wall off the line" behavior that must be preserved.
        grid = make_grid([
            ["floor", "wall"],
            ["floor", "floor"],
        ])
        self.assertTrue(has_line_of_sight(grid, (0, 0), (1, 1)))

    def test_clear_diagonal_still_has_sight(self):
        # A diagonal through open floor (no corner pinch, no on-line wall)
        # must remain unblocked.
        grid = make_grid([
            ["floor", "floor", "floor"],
            ["floor", "floor", "floor"],
            ["floor", "floor", "floor"],
        ])
        self.assertTrue(has_line_of_sight(grid, (0, 0), (2, 2)))


# ---------------------------------------------------------------------------
# Doors (door-features spec §5 / AC4, AC5): a closed door is a wall (not
# walkable, blocks LOS incl. corner-cut); an open door is a doorway. The
# optional ``doors`` parameter pins the exact closed-door set.
# ---------------------------------------------------------------------------


class TestDoorWalkable(unittest.TestCase):
    """AC5: closed door not walkable, open door walkable, doors=None ⇒ locked."""

    def test_closed_door_not_walkable(self):
        grid = make_grid([["floor", "doorway", "floor"]])
        # A bare grid (doors=None): the doorway is the CLOSED (locked) default.
        self.assertFalse(walkable(grid, 1, 0))
        # Explicitly closed (L or U) via the derived set: blocked.
        self.assertFalse(walkable(grid, 1, 0, frozenset({(1, 0)})))
        # Open door (not in the closed set): walkable.
        self.assertTrue(walkable(grid, 1, 0, frozenset()))

    def test_floor_unaffected_by_doors(self):
        grid = make_grid([["floor", "wall"]])
        self.assertTrue(walkable(grid, 0, 0))                      # floor walkable
        self.assertTrue(walkable(grid, 0, 0, frozenset()))         # empty set
        self.assertFalse(walkable(grid, 1, 0))                     # wall

    def test_doors_none_means_all_locked(self):
        # Regression: a bare grid's doorway is blocked by default (A2).
        grid = make_grid([["floor", "doorway"]])
        self.assertFalse(walkable(grid, 1, 0))
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 0)))


class TestDoorStep(unittest.TestCase):
    """AC5: a diagonal into a closed door is illegal; an open door is fine."""

    def test_onto_closed_door_illegal(self):
        grid = make_grid([["floor", "doorway"]])
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 0)))        # closed
        self.assertTrue(is_valid_step(grid, (0, 0), (1, 0), frozenset()))  # open

    def test_diagonal_elbows_closed_doors_corner_cut(self):
        # Elbows (1,0) and (0,1) are doorways; the diagonal (0,0)->(1,1) needs
        # BOTH elbows walkable. A closed door elbow is not walkable, so closing
        # EITHER elbow blocks the diagonal (movement corner-cut preserved);
        # with both open the diagonal is legal.
        grid = make_grid([
            ["floor", "doorway"],
            ["doorway", "floor"],
        ])
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 1),
                                       frozenset({(1, 0), (0, 1)})))  # both closed
        self.assertFalse(is_valid_step(grid, (0, 0), (1, 1),
                                       frozenset({(1, 0)})))  # one elbow closed
        self.assertTrue(is_valid_step(grid, (0, 0), (1, 1), frozenset()))  # open


class TestDoorLineOfSight(unittest.TestCase):
    """AC4: closed door blocks LOS like a wall (incl. corner-cut); open = clear."""

    def test_closed_door_blocks_sight_open_transparent(self):
        grid = make_grid([["floor", "doorway", "floor"]])
        closed = frozenset({(1, 0)})
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 0), closed))
        self.assertTrue(has_line_of_sight(grid, (0, 0), (2, 0), frozenset()))

    def test_closed_door_blocks_like_a_wall(self):
        # Identical geometry with a wall vs a closed door → identical LOS.
        wall = make_grid([["floor", "wall", "floor"]])
        door = make_grid([["floor", "doorway", "floor"]])
        self.assertFalse(has_line_of_sight(wall, (0, 0), (2, 0)))
        self.assertFalse(has_line_of_sight(door, (0, 0), (2, 0),
                                          frozenset({(1, 0)})))
        self.assertEqual(
            has_line_of_sight(wall, (0, 0), (2, 0)),
            has_line_of_sight(door, (0, 0), (2, 0), frozenset({(1, 0)})),
        )

    def test_diagonal_both_elbows_closed_doors_blocked(self):
        # A diagonal whose both elbows are closed doors is a corner-cut.
        grid = make_grid([
            ["floor", "doorway", "floor"],
            ["doorway", "floor", "floor"],
            ["floor", "floor", "floor"],
        ])
        closed_both = frozenset({(1, 0), (0, 1)})
        self.assertFalse(has_line_of_sight(grid, (0, 0), (1, 1), closed_both))
        self.assertFalse(has_line_of_sight(grid, (0, 0), (2, 2), closed_both))
        # One elbow open → the line passes (a single closed corner grazes).
        self.assertTrue(has_line_of_sight(grid, (0, 0), (1, 1),
                                          frozenset({(1, 0)})))

    def test_endpoint_never_blocks(self):
        # A token on the cell sees itself even if that cell is a closed door.
        grid = make_grid([["doorway", "floor"]])
        self.assertTrue(has_line_of_sight(grid, (0, 0), (0, 0),
                                          frozenset({(0, 0)})))


class TestDoorFindPath(unittest.TestCase):
    """AC5: A* routes around a closed door / through an open one; None sealed."""

    WALL_WITH_DOOR = [
        ["floor", "wall", "floor"],
        ["floor", "doorway", "floor"],
        ["floor", "wall", "floor"],
    ]

    def test_sealed_by_closed_door_none_via_open(self):
        grid = make_grid(self.WALL_WITH_DOOR)
        self.assertIsNone(find_path(grid, (0, 1), (2, 1), frozenset({(1, 1)})))
        path = find_path(grid, (0, 1), (2, 1), frozenset())
        self.assertIsNotNone(path)
        self.assertIn((1, 1), path)

    def test_routes_around_closed_door(self):
        # A closed door in one place, an open detour elsewhere: A* takes the
        # open route and never visits the closed door.
        grid = make_grid([
            ["floor", "doorway", "floor"],
            ["floor", "floor", "floor"],
        ])
        closed = frozenset({(1, 0)})
        path = find_path(grid, (0, 0), (2, 0), closed)
        self.assertIsNotNone(path)
        self.assertNotIn((1, 0), path)
        for a, b in zip(path, path[1:]):
            self.assertTrue(is_valid_step(grid, a, b, closed))

    def test_doors_none_blocks_bare_doorway(self):
        grid = make_grid(self.WALL_WITH_DOOR)
        # A bare grid (doors=None): the doorway is the closed default → None.
        self.assertIsNone(find_path(grid, (0, 1), (2, 1)))


# ---------------------------------------------------------------------------
# Safe-room doors (safe-room spec §5 / AC4, AC5): a closed safe door is a
# wall for movement + LOS (entity-agnostic); an open safe door is
# walkable/transparent for party/neutral/team=None but a WALL to a hostile
# (the entity restriction, SAFE-3). `team=None` (and every safe-less grid)
# stays byte-for-byte the pre-feature behaviour.
# ---------------------------------------------------------------------------


def safe_grid(rows, safe, name="safe-test"):
    """A :class:`Grid` with the given ``safe`` ("<x>,<y>" -> "C"|"O") set."""
    g = make_grid(rows, name=name)
    for key, st in safe.items():
        x, y = (int(p) for p in key.split(","))
        g.set_safe_door(x, y, st)
    return g


class TestSafeDoorWalkable(unittest.TestCase):
    """AC5: closed safe door not walkable for ANY team (incl. team=None);
    open safe door walkable for party/neutral/None, NOT for hostile."""

    ROWS = [["floor", "doorway", "floor"]]

    def test_closed_safe_door_blocks_every_team(self):
        g = safe_grid(self.ROWS, {"1,0": "C"})
        for team in (None, "party", "neutral", "hostile"):
            with self.subTest(team=team):
                self.assertFalse(walkable(g, 1, 0, team=team))

    def test_open_safe_door_walkable_for_party_neutral_none(self):
        g = safe_grid(self.ROWS, {"1,0": "O"})
        for team in (None, "party", "neutral"):
            with self.subTest(team=team):
                self.assertTrue(walkable(g, 1, 0, team=team))

    def test_open_safe_door_not_walkable_for_hostile(self):
        g = safe_grid(self.ROWS, {"1,0": "O"})
        self.assertFalse(walkable(g, 1, 0, team="hostile"))

    def test_closed_safe_door_blocks_like_a_wall_geometry(self):
        # Identical geometry: a wall cell vs a closed safe door → both
        # unwalkable for every team; identical for LOS (team-agnostic).
        wall = make_grid(self.ROWS)
        wall.cells[0][1] = "wall"
        closed = safe_grid(self.ROWS, {"1,0": "C"})
        for team in (None, "party", "neutral", "hostile"):
            self.assertEqual(walkable(wall, 1, 0, team=team),
                             walkable(closed, 1, 0, team=team),
                             f"walkable mismatch for {team}")
        self.assertEqual(has_line_of_sight(wall, (0, 0), (2, 0)),
                         has_line_of_sight(closed, (0, 0), (2, 0)),
                         False)

    def test_floor_unaffected_by_safe_doors(self):
        g = safe_grid(self.ROWS, {"1,0": "C"})
        self.assertTrue(walkable(g, 0, 0, team="hostile"))
        self.assertTrue(walkable(g, 2, 0, team="hostile"))


class TestSafeDoorStep(unittest.TestCase):
    """AC5: diagonal into/around an open safe door is blocked for a hostile
    (no slip-through) and legal for party/neutral; no-corner-cut preserved."""

    def test_diagonal_into_open_safe_door_blocked_for_hostile_only(self):
        g = safe_grid(
            [["floor", "doorway", "floor"],
             ["floor", "floor", "floor"]],
            {"1,0": "O"},
        )
        # (0,1) -> (1,0): orthogonal step onto the open safe door.
        for team in ("party", "neutral", None):
            with self.subTest(team=team):
                self.assertTrue(is_valid_step(g, (0, 1), (1, 0), team=team))
        self.assertFalse(is_valid_step(g, (0, 1), (1, 0), team="hostile"))

    def test_diagonal_with_open_safe_door_elbow_blocked_for_hostile(self):
        # (0,0) -> (1,1): elbow (1,0) is the OPEN safe door — walkable for
        # party/neutral (and the elbow (0,1) is floor), blocked for a
        # hostile (elbow not walkable for its team → no slip-through).
        g = safe_grid(
            [["floor", "doorway", "floor"],
             ["floor", "floor", "floor"]],
            {"1,0": "O"},
        )
        for team in ("party", "neutral", None):
            with self.subTest(team=team):
                self.assertTrue(is_valid_step(g, (0, 0), (1, 1), team=team))
        self.assertFalse(is_valid_step(g, (0, 0), (1, 1), team="hostile"))

    def test_diagonal_with_closed_safe_door_elbow_blocked_for_all(self):
        g = safe_grid(
            [["floor", "doorway", "floor"],
             ["floor", "floor", "floor"]],
            {"1,0": "C"},
        )
        for team in (None, "party", "neutral", "hostile"):
            with self.subTest(team=team):
                self.assertFalse(is_valid_step(g, (0, 0), (1, 1), team=team))


class TestSafeDoorLineOfSight(unittest.TestCase):
    """AC4: a closed safe door blocks LOS exactly like a wall (incl.
    corner-cut); an open safe door is transparent. LOS has NO team — the
    behaviour is identical for all teams by construction (no team param)."""

    def test_closed_safe_door_blocks_sight_open_transparent(self):
        closed = safe_grid([["floor", "doorway", "floor"]], {"1,0": "C"})
        open_ = safe_grid([["floor", "doorway", "floor"]], {"1,0": "O"})
        self.assertFalse(has_line_of_sight(closed, (0, 0), (2, 0)))
        self.assertTrue(has_line_of_sight(open_, (0, 0), (2, 0)))

    def test_closed_safe_door_blocks_like_a_wall(self):
        wall = make_grid([["floor", "wall", "floor"]])
        door = safe_grid([["floor", "doorway", "floor"]], {"1,0": "C"})
        self.assertEqual(
            has_line_of_sight(wall, (0, 0), (2, 0)),
            has_line_of_sight(door, (0, 0), (2, 0)),
            False,
        )

    def test_diagonal_both_elbows_closed_safe_doors_blocked(self):
        # A diagonal whose both elbows are closed safe doors is a
        # corner-cut (identical to the closed-normal-door rule).
        g = safe_grid(
            [["floor", "doorway", "floor"],
             ["doorway", "floor", "floor"],
             ["floor", "floor", "floor"]],
            {"1,0": "C", "0,1": "C"},
        )
        self.assertFalse(has_line_of_sight(g, (0, 0), (1, 1)))
        self.assertFalse(has_line_of_sight(g, (0, 0), (2, 2)))
        # One elbow open → the line passes (a single closed corner grazes).
        g_one = safe_grid(
            [["floor", "doorway", "floor"],
             ["doorway", "floor", "floor"],
             ["floor", "floor", "floor"]],
            {"1,0": "C", "0,1": "O"},
        )
        self.assertTrue(has_line_of_sight(g_one, (0, 0), (1, 1)))

    def test_open_safe_door_is_sight_transparent_for_a_hostile_too(self):
        # Sight is team-agnostic: a hostile behind an open safe door is
        # SEEN (the restriction is occupancy/movement, not sight — E14).
        g = safe_grid([["floor", "doorway", "floor"]], {"1,0": "O"})
        self.assertTrue(has_line_of_sight(g, (0, 0), (2, 0)))


class TestSafeDoorFindPath(unittest.TestCase):
    """AC5/AC6: find_path(team=...) — hostile routes AROUND an open safe
    door (None when sealed), party/neutral route THROUGH it; the team=None
    (entity-agnostic) regression stays identical for safe-less grids."""

    WALL_WITH_DOOR = [
        ["floor", "wall", "floor"],
        ["floor", "doorway", "floor"],
        ["floor", "wall", "floor"],
    ]

    def test_sealed_open_safe_door_none_for_hostile(self):
        # The ONLY route across the wall column is the open safe door: a
        # hostile cannot use it → None; party/neutral/team=None can.
        g = safe_grid(self.WALL_WITH_DOOR, {"1,1": "O"})
        self.assertIsNone(find_path(g, (0, 1), (2, 1), team="hostile"))
        for team in (None, "party", "neutral"):
            with self.subTest(team=team):
                path = find_path(g, (0, 1), (2, 1), team=team)
                self.assertIsNotNone(path, f"team={team} sealed?!")
                self.assertIn((1, 1), path)

    def test_closed_safe_door_seals_for_every_team(self):
        g = safe_grid(self.WALL_WITH_DOOR, {"1,1": "C"})
        for team in (None, "party", "neutral", "hostile"):
            with self.subTest(team=team):
                self.assertIsNone(find_path(g, (0, 1), (2, 1), team=team))

    def test_hostile_routes_around_open_safe_door(self):
        # A detour exists (bottom row): the hostile A* must take it and
        # never visit the open safe door cell; party takes the direct line.
        g = safe_grid(
            [["floor", "wall", "floor"],
             ["floor", "doorway", "floor"],
             ["floor", "floor", "floor"]],
            {"1,1": "O"},
        )
        hostile = find_path(g, (0, 0), (2, 0), team="hostile")
        self.assertIsNotNone(hostile)
        self.assertNotIn((1, 1), hostile)
        party = find_path(g, (0, 0), (2, 0), team="party")
        self.assertIsNotNone(party)
        self.assertIn((1, 1), party)  # the straight door-gap route is legal

    def test_open_safe_door_is_a_wall_to_a_hostile(self):
        # The core no-slip claim: to a hostile an OPEN safe door blocks
        # movement exactly like a WALL. Compare hostile A* reachability with
        # the door open vs. with a wall at the same cell — identical.
        rows = [
            ["floor", "wall", "floor"],
            ["floor", "doorway", "floor"],
            ["floor", "wall", "floor"],
        ]
        g_open = safe_grid(rows, {"1,1": "O"})
        g_wall = make_grid(rows)
        g_wall.cells[1][1] = "wall"
        # The only gap in the wall column is (1,1): hostile sealed either way.
        self.assertIsNone(find_path(g_open, (0, 1), (2, 1), team="hostile"))
        self.assertIsNone(find_path(g_wall, (0, 1), (2, 1)))
        # ...but party routes through the open door (and the wallless case
        # shows the door cell is otherwise a normal open doorway).
        self.assertIsNotNone(find_path(g_open, (0, 1), (2, 1), team="party"))

    def test_diagonal_elbow_around_open_safe_door_blocked_for_hostile(self):
        # A hostile cannot diagonal AROUND the open safe door: the door
        # cell is a walkable elbow for party/neutral but NOT for a hostile,
        # so a diagonal whose elbow is the open safe door is illegal only
        # for the hostile (no slip-through), exactly the wall case.
        g = safe_grid(
            [["floor", "doorway", "floor"],
             ["floor", "floor", "floor"],
             ["floor", "floor", "floor"]],
            {"1,0": "O"},
        )
        g_wall = make_grid(
            [["floor", "wall", "floor"],
             ["floor", "floor", "floor"],
             ["floor", "floor", "floor"]])
        # (0,0)->(1,1) diagonal: elbow (1,0) is the open safe door / wall.
        for team in ("party", "neutral", None):
            self.assertTrue(is_valid_step(g, (0, 0), (1, 1), team=team))
        self.assertFalse(is_valid_step(g, (0, 0), (1, 1), team="hostile"))
        # identical to the wall geometry for a hostile:
        self.assertFalse(is_valid_step(g_wall, (0, 0), (1, 1)))

    def test_safeless_grid_team_param_is_a_noop_regression(self):
        # AC5 regression: on a grid with NO safe doors, team=... produces
        # byte-identical results to the no-team call (the open-safe term is
        # empty and SAFE_DOOR_TEAMS never applies).
        g = make_grid(self.WALL_WITH_DOOR)
        for team in ("party", "neutral", "hostile"):
            with self.subTest(team=team):
                self.assertEqual(
                    find_path(g, (0, 1), (2, 1), team=team),
                    find_path(g, (0, 1), (2, 1)),
                )
                self.assertEqual(walkable(g, 1, 1, team=team),
                                 walkable(g, 1, 1))

    def test_team_none_is_entity_agnostic_on_safe_grid(self):
        # team=None: the open safe door is walkable (LOS/visibility/QA
        # callers are entity-agnostic) — identical to a plain open doorway.
        g = safe_grid(self.WALL_WITH_DOOR, {"1,1": "O"})
        self.assertTrue(walkable(g, 1, 1, team=None))
        self.assertIsNotNone(find_path(g, (0, 1), (2, 1), team=None))


if __name__ == "__main__":
    unittest.main()
