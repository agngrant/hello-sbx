"""LittleDungeons pathfinding (PROJECT.md §6) — pure standard library, no server state.

Core movement rules (hard requirements #3/#4, §6):

* **Walkable cells:** ``floor`` and ``doorway``.  ``wall`` blocks movement,
  and out-of-bounds is treated as blocked.
* **8-direction (king) moves:** one cell in any direction per step.
* **Corner cutting is forbidden:** a diagonal step is legal only when BOTH
  orthogonal cells it touches (the step's two "elbow" cells) are walkable.
  This is the anti-cheat rule that stops a character squeezing diagonally
  through a wall corner.
* **A* search** with the consistent octile heuristic (orthogonal = 10,
  diagonal = 14), min-heap, and a unique tie-break counter so the result is
  deterministic for a given grid.  Works on any grid — nothing is hard-coded.

Also provides :func:`has_line_of_sight` (Bresenham digitization, blocked by
``wall``) which powers the optional fog-of-war toggle (§5).
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Iterator

from app.models import Grid

#: Walkable cell types (§6: "wall is blocked; floor and doorway are walkable").
WALKABLE_CELLS = frozenset({"floor", "doorway"})

#: Movement costs — octile distance, consistent with the 8-direction grid.
ORTHOGONAL_COST = 10
DIAGONAL_COST = 14

#: The 8 king-move directions, in a fixed order (deterministic expansion).
_EIGHT_DIRS = (
    (0, -1), (1, -1), (1, 0), (1, 1),
    (0, 1), (-1, 1), (-1, 0), (-1, -1),
)


# ---------------------------------------------------------------------------
# Cell predicates
# ---------------------------------------------------------------------------


def _as_cell(cell: Any) -> tuple[int, int]:
    """Normalize an (x, y) pair (list or tuple) to a tuple of ints."""
    return (int(cell[0]), int(cell[1]))


def _in_bounds(grid: Grid, x: int, y: int) -> bool:
    return 0 <= x < grid.width and 0 <= y < grid.height


def walkable(grid: Grid, x: int, y: int) -> bool:
    """True if ``(x, y)`` is in-bounds AND ``cells[y][x]`` is ``"floor"``
    or ``"doorway"``.  Walls and out-of-bounds are blocked."""
    if not _in_bounds(grid, x, y):
        return False
    return grid.cells[y][x] in WALKABLE_CELLS


def is_valid_step(grid: Grid, a: tuple[int, int], b: tuple[int, int]) -> bool:
    """True if moving from ``a`` to ``b`` is a legal one-step move.

    Rules (PROJECT.md §6 — the anti-cheat rule):
      * ``a`` and ``b`` are distinct (x, y) pairs.
      * King-move adjacency: ``max(|dx|, |dy|) == 1``.
      * ``b`` is walkable (floor/doorway, in-bounds).
      * If the move is DIAGONAL (both dx and dy nonzero), BOTH orthogonal
        elbow cells — ``(a.x + dx, a.y)`` and ``(a.x, a.y + dy)`` — must be
        walkable.  Diagonally squeezing around a wall corner is forbidden.
    """
    a = _as_cell(a)
    b = _as_cell(b)
    if a == b:
        return False
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if max(abs(dx), abs(dy)) != 1:
        return False
    if not walkable(grid, b[0], b[1]):
        return False
    if dx != 0 and dy != 0:  # diagonal: forbid corner cutting
        if not walkable(grid, a[0] + dx, a[1]):
            return False
        if not walkable(grid, a[0], a[1] + dy):
            return False
    return True


# ---------------------------------------------------------------------------
# A* pathfinding
# ---------------------------------------------------------------------------


def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Octile distance scaled to the step costs (consistent → admissible)."""
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return ORTHOGONAL_COST * max(dx, dy) + (DIAGONAL_COST - ORTHOGONAL_COST) * min(dx, dy)


def find_path(
    grid: Grid, start: tuple[int, int], goal: tuple[int, int]
) -> list[tuple[int, int]] | None:
    """A* over the 8 directions, using :func:`is_valid_step` for expansion.

    Returns the list of (x, y) cells from ``start`` to ``goal`` inclusive,
    or ``None`` if no path exists.  Semantics:

    * start or goal not walkable (wall / out of bounds) → ``None``.
    * ``start == goal`` (on a walkable cell) → ``[start]``.
    * A diagonal step only expands when the corner-cut rule allows it, so a
      returned path never squeezes around a wall corner and never visits a
      non-walkable cell.

    Deterministic: fixed direction order and a unique heap tie-break counter.
    """
    start = _as_cell(start)
    goal = _as_cell(goal)
    if not walkable(grid, *start) or not walkable(grid, *goal):
        return None
    if start == goal:
        return [start]

    # heap entries: (f, tiebreak, cell). The tiebreak is a unique counter,
    # so cells are never compared against each other.
    open_heap: list[tuple[int, int, tuple[int, int]]] = [(0, 0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], int] = {start: 0}
    closed: set[tuple[int, int]] = set()
    counter = 0

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path
        closed.add(current)
        cx, cy = current
        g_current = g_score[current]
        for dx, dy in _EIGHT_DIRS:
            nxt = (cx + dx, cy + dy)
            if nxt in closed or not is_valid_step(grid, current, nxt):
                continue
            step_cost = DIAGONAL_COST if (dx != 0 and dy != 0) else ORTHOGONAL_COST
            tentative = g_current + step_cost
            if tentative < g_score.get(nxt, math.inf):
                g_score[nxt] = tentative
                came_from[nxt] = current
                counter += 1
                heapq.heappush(
                    open_heap, (tentative + _heuristic(nxt, goal), counter, nxt)
                )
    return None


# ---------------------------------------------------------------------------
# Line of sight (optional fog of war, §5)
# ---------------------------------------------------------------------------


def _bresenham(
    x0: int, y0: int, x1: int, y1: int
) -> Iterator[tuple[int, int]]:
    """Yield the (x, y) cells of the line from (x0, y0) to (x1, y1),
    endpoints included (symmetric Bresenham digitization)."""
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        yield (x, y)
        if (x, y) == (x1, y1):
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def has_line_of_sight(
    grid: Grid, a: tuple[int, int], b: tuple[int, int]
) -> bool:
    """True if a straight line from ``a`` to ``b`` is not blocked by a wall.

    The line is digitized with Bresenham; if ANY cell strictly between
    ``a`` and ``b`` is a ``wall`` cell → False, else True.  ``a == b`` →
    True.  The endpoints themselves never block (entities can stand on the
    cells they occupy).  Used by the optional fog-of-war toggle.
    """
    a = _as_cell(a)
    b = _as_cell(b)
    if a == b:
        return True
    if not (_in_bounds(grid, *a) and _in_bounds(grid, *b)):
        return False
    for x, y in _bresenham(a[0], a[1], b[0], b[1]):
        if (x, y) == b:
            break  # endpoint never blocks; cells after are not "between"
        if (x, y) != a and grid.cells[y][x] == "wall":
            return False
    return True
