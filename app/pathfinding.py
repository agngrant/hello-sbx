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

Doors (docs/design/door-features.md §5): a **closed** door (a ``doorway``
cell whose state is not ``"O"``) blocks walkability, steps, and line of
sight **exactly like a wall** — including the diagonal no-corner-cut rule —
while an **open** door is sight- and movement-transparent (today's
doorway). The predicates take an OPTIONAL ``doors`` parameter: a set/frozenset
of ``(x, y)`` of CLOSED door cells. When omitted (``None``) the set is
derived from ``grid.doors``, so existing two/three-argument call sites keep
working and a bare ``Grid`` (``doors=None``) behaves all-locked (spec D1/A2).

Safe-room doors (docs/design/safe-room-doors.md §5, D3/D4): a **closed**
safe door (``grid.safe[key] == "C"``) joins the closed set — it blocks
movement AND line of sight exactly like a wall (entity-agnostic). An **open**
safe door is sight-transparent (LOS has no team), but the movement predicates
take an OPTIONAL ``team`` parameter: for a ``team`` NOT in
:data:`app.models.SAFE_DOOR_TEAMS` (i.e. ``hostile``) an open safe door is
ADDED to the blocked set (a wall to the hostile — the entity restriction,
SAFE-3/AC5); for ``party``/``neutral``/``None`` the open-safe-door term is
empty, so ``team=None`` callers (LOS, visibility, QA re-derivation) are
entity-agnostic and safe-less grids behave byte-for-byte as before (AC16).
The blocked set is computed ONCE per predicate call (AC15).
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Iterator

from app.models import SAFE_DOOR_TEAMS, Grid

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


def _closed_doors(grid: Grid) -> frozenset[tuple[int, int]]:
    """The set of ``(x, y)`` of CLOSED doors (state ``!= 'O'``). Pure.

    A ``doorway`` cell is CLOSED unless its door state records it as ``"O"``
    (open): a NORMAL door via ``grid.doors`` (absent entry ⇒ closed+locked
    default), a SAFE-room door via ``grid.safe`` (a ``"C"`` safe door blocks
    like a wall — safe-room spec §5.1; safe and normal doors are mutually
    exclusive, D1/I1). Floors/walls are never in the set (they are not
    doors).

    For a grid with NO safe doors (``grid.safe is None`` / empty) the
    ``key in safe`` branch never fires — the output is byte-for-byte
    identical to the pre-feature implementation (AC16).
    """
    doors = grid.doors or {}
    safe = grid.safe or {}
    closed: set[tuple[int, int]] = set()
    for y in range(grid.height):
        for x in range(grid.width):
            if grid.cells[y][x] != "doorway":
                continue
            key = f"{x},{y}"
            if key in safe:
                if safe[key] != "O":  # a CLOSED safe door blocks like a wall
                    closed.add((x, y))
            elif doors.get(key) != "O":  # a CLOSED normal door blocks
                closed.add((x, y))
    return frozenset(closed)


def _open_safe_doors(grid: Grid) -> frozenset[tuple[int, int]]:
    """The ``(x, y)`` of OPEN safe-room doors (``grid.safe[key] == "O"``).
    Pure; empty for a grid with no safe doors."""
    safe = grid.safe or {}
    return frozenset(
        (int(k.split(",")[0]), int(k.split(",")[1]))
        for k, st in safe.items() if st == "O"
    )


def _blocked_for(
    grid: Grid,
    doors: frozenset[tuple[int, int]] | None,
    team: str | None,
) -> frozenset[tuple[int, int]]:
    """The set of cells ``team`` cannot walk on: every CLOSED door (normal
    or safe — :func:`_closed_doors`), PLUS — only when ``team`` is NOT in
    :data:`SAFE_DOOR_TEAMS` (i.e. a hostile) — every OPEN safe door
    (the entity restriction, SAFE-3: an open safe door is a wall to a
    hostile). ``team=None`` (entity-agnostic callers: LOS, visibility, the
    QA re-derivation) and ``party``/``neutral`` return the plain closed set.
    """
    closed = doors if doors is not None else _closed_doors(grid)
    if team is None or team in SAFE_DOOR_TEAMS:
        return closed
    return closed | _open_safe_doors(grid)


def walkable(
    grid: Grid,
    x: int,
    y: int,
    doors: frozenset[tuple[int, int]] | None = None,
    team: str | None = None,
) -> bool:
    """True if ``(x, y)`` is in-bounds AND ``cells[y][x]`` is ``"floor"``
    or a walkable ``doorway`` (a closed door is not walkable — spec §5.1).
    Walls, out-of-bounds, and closed doors are blocked. ``team`` (optional,
    safe-room spec §5.1): for a hostile team an OPEN safe door is blocked
    too (the entity restriction); ``team=None`` / party / neutral leave the
    set unchanged."""
    if not _in_bounds(grid, x, y):
        return False
    if grid.cells[y][x] not in WALKABLE_CELLS:
        return False
    if (x, y) in _blocked_for(grid, doors, team):
        return False  # a closed door, or an open safe door for a hostile
    return True


def is_valid_step(
    grid: Grid,
    a: tuple[int, int],
    b: tuple[int, int],
    doors: frozenset[tuple[int, int]] | None = None,
    team: str | None = None,
) -> bool:
    """True if moving from ``a`` to ``b`` is a legal one-step move.

    Rules (PROJECT.md §6 — the anti-cheat rule, door-aware per §5.1,
    team-aware per safe-room §5.1):
      * ``a`` and ``b`` are distinct (x, y) pairs.
      * King-move adjacency: ``max(|dx|, |dy|) == 1``.
      * ``b`` is walkable (floor/walkable doorway, in-bounds; a closed door
        is NOT walkable; for a hostile an open safe door is NOT walkable).
      * If the move is DIAGONAL (both dx and dy nonzero), BOTH orthogonal
        elbow cells — ``(a.x + dx, a.y)`` and ``(a.x, a.y + dy)`` — must be
        walkable FOR THE TEAM.  Diagonally squeezing around a wall corner
        (or between two closed doors, or around an open safe door for a
        hostile) is forbidden — no "slipping past" the safe door
        diagonally. no-corner-cut preserved for all teams.
    """
    a = _as_cell(a)
    b = _as_cell(b)
    if a == b:
        return False
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if max(abs(dx), abs(dy)) != 1:
        return False
    closed = _blocked_for(grid, doors, team)
    if not walkable(grid, b[0], b[1], closed):
        return False
    if dx != 0 and dy != 0:  # diagonal: forbid corner cutting
        if not walkable(grid, a[0] + dx, a[1], closed):
            return False
        if not walkable(grid, a[0], a[1] + dy, closed):
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
    grid: Grid,
    start: tuple[int, int],
    goal: tuple[int, int],
    doors: frozenset[tuple[int, int]] | None = None,
    team: str | None = None,
) -> list[tuple[int, int]] | None:
    """A* over the 8 directions, using :func:`is_valid_step` for expansion.

    ``doors`` is the optional closed-door set (see :func:`walkable`); when
    omitted it is derived from ``grid.doors`` ONCE (not per step), so A*
    routes around closed doors and through open ones with only a constant
    factor of extra cost (spec §5.2 / AC15). ``team`` (optional, safe-room
    spec §5.3): the moving entity's team — for a hostile team the blocked
    set also includes every OPEN safe door (the entity restriction), so a
    hostile A* treats an open safe door as a wall and routes around it (or
    returns ``None`` when sealed, e.g. ``"no route — wall in the way"``).
    The team-aware blocked set is computed ONCE per call (AC15).

    Returns the list of (x, y) cells from ``start`` to ``goal`` inclusive,
    or ``None`` if no path exists.  Semantics:

    * start or goal not walkable FOR THE TEAM (wall / closed door /
      hostile-blocked open safe door / out of bounds) → ``None``.
    * ``start == goal`` (on a walkable cell) → ``[start]``.
    * A diagonal step only expands when the corner-cut rule allows it (for
      the team), so a returned path never squeezes around a wall corner and
      never visits a non-walkable cell.

    Deterministic: fixed direction order and a unique heap tie-break counter.
    """
    start = _as_cell(start)
    goal = _as_cell(goal)
    closed_doors = _blocked_for(grid, doors, team)
    if not walkable(grid, *start, closed_doors) or not walkable(grid, *goal, closed_doors):
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
            if nxt in closed or not is_valid_step(grid, current, nxt, closed_doors):
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


def _blocks_sight(grid: Grid, cell: tuple[int, int],
                  closed: frozenset[tuple[int, int]]) -> bool:
    """True if ``cell`` blocks line of sight: an in-bounds ``wall`` cell OR a
    CLOSED door cell (a closed door is a wall to sight — spec §5.1).

    Used by :func:`has_line_of_sight` for both the on-line cell test and the
    corner-cut elbow test.  For an in-bounds sight line the elbows are always
    in-bounds, so the OOB guard is belt-and-braces.
    """
    x, y = _as_cell(cell)
    if not _in_bounds(grid, x, y):
        return False
    return grid.cells[y][x] == "wall" or (x, y) in closed


def has_line_of_sight(
    grid: Grid,
    a: tuple[int, int],
    b: tuple[int, int],
    doors: frozenset[tuple[int, int]] | None = None,
) -> bool:
    """True if a straight line from ``a`` to ``b`` is not blocked.

    The line is digitized with Bresenham; it is blocked (→ False) when
    EITHER:

    * ANY cell strictly between ``a`` and ``b`` is a ``wall`` cell OR a
      CLOSED door cell, OR
    * a DIAGONAL step of the line squeezes between two wall/closed-door
      corners — i.e. BOTH orthogonal "elbow" cells the diagonal touch are
      walls or closed doors.  This mirrors the movement no-corner-cut rule
      (``is_valid_step``): a sight line may not "cut" through the zero-width
      gap between two blockers.  A diagonal that merely grazes a single
      blocker corner (one elbow open — floor/open door) still passes.

    An OPEN door is sight-transparent (exactly today's ``doorway``) — so
    too is an OPEN safe door (LOS has no team, safe-room spec §5.1: sight is
    team-agnostic; the safe-door entity restriction is a movement/occupancy
    rule, not a sight rule).  ``a == b`` → True.  The endpoints themselves
    never block (entities can stand on the cells they occupy).  Used by the
    awareness three-tier model, the explored-map ``visible_cells``, and the
    optional fog-of-war toggle.
    """
    a = _as_cell(a)
    b = _as_cell(b)
    if a == b:
        return True
    if not (_in_bounds(grid, *a) and _in_bounds(grid, *b)):
        return False
    closed = _closed_doors(grid) if doors is None else doors
    prev: tuple[int, int] | None = None
    for x, y in _bresenham(a[0], a[1], b[0], b[1]):
        if prev is not None:
            px, py = prev
            if x != px and y != py:  # diagonal step: check corner cutting
                if _blocks_sight(grid, (x, py), closed) \
                        and _blocks_sight(grid, (px, y), closed):
                    return False  # squeezed between two wall/door corners
        if (x, y) == b:
            break  # endpoint never blocks; cells after are not "between"
        if (x, y) != a and _blocks_sight(grid, (x, y), closed):
            return False  # a wall or closed door lies on the sight line
        prev = (x, y)
    return True
