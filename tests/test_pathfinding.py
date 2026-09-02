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
) -> None:
    """Assert a path is well-formed: endpoints, valid steps, walkable cells."""
    case.assertIsNotNone(path)
    case.assertEqual(path[0], start)
    case.assertEqual(path[-1], goal)
    for a, b in zip(path, path[1:]):
        case.assertTrue(is_valid_step(grid, a, b), f"illegal step {a} -> {b}")
    for (x, y) in path:
        case.assertIn(grid.cells[y][x], ("floor", "doorway"),
                      f"path visits non-walkable cell {(x, y)}")


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
        self.assertTrue(walkable(self.grid, 2, 0))   # doorway
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
        self.assertTrue(is_valid_step(grid, (0, 0), (1, 1)))


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
        # pass through it — proving doorways are walkable.
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "doorway", "floor"],
            ["floor", "wall", "floor"],
            ["floor", "wall", "floor"],
        ])
        path = find_path(grid, (0, 0), (2, 3))
        self.assertIsNotNone(path)
        self.assertIn((1, 1), path)
        check_path(self, grid, path, (0, 0), (2, 3))

    def test_door_diagonal_elbow_is_walkable(self):
        # Diagonal into the doorway from (0,0): elbow (1,0) is a wall →
        # forbidden; the path must approach the doorway orthogonally.
        grid = make_grid([
            ["floor", "wall", "floor"],
            ["floor", "doorway", "floor"],
            ["floor", "wall", "floor"],
        ])
        path = find_path(grid, (0, 0), (2, 1))
        self.assertIsNotNone(path)
        self.assertIn((1, 1), path)
        steps = list(zip(path, path[1:]))
        self.assertNotIn(((0, 0), (1, 1)), steps)
        check_path(self, grid, path, (0, 0), (2, 1))

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
        grid = make_grid([["floor", "doorway", "floor"]])
        self.assertTrue(has_line_of_sight(grid, (0, 0), (2, 0)))

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


if __name__ == "__main__":
    unittest.main()
