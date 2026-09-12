"""Shared independent test oracles (deduplicated across test modules).

The canonical re-derivation oracles used by the visibility / awareness /
session tests: a fresh S-set derivation straight from the spec's rules
(S1 walkable cells with real LOS + anchor; S2 walls and closed doors
revealed via a walkable 4-orthogonal neighbour) written directly on top of
the real :func:`app.pathfinding.has_line_of_sight`. It deliberately does
NOT call ``visible_cells`` / ``build_visibility_mask`` /
``derive_visible_mask``, so a bug in the implementation fails here even if
a pinned literal were mistyped.

Spec provenance: visibility spec §12 ("the re-derivation is the oracle").
"""

from __future__ import annotations

from app.models import Grid
from app.pathfinding import _closed_doors, has_line_of_sight


def oracle_walkable(g: Grid, x: int, y: int, closed) -> bool:
    """Door-aware walkable: in-bounds, floor/doorway, and NOT a closed door."""
    if not (0 <= x < g.width and 0 <= y < g.height):
        return False
    if g.cells[y][x] not in ("floor", "doorway"):
        return False
    return (x, y) not in closed


def oracle_visible(
    g: Grid, pos: tuple[int, int], closed: frozenset[tuple[int, int]] | None = None
) -> set[tuple[int, int]]:
    """Re-derive the S-set straight from the spec's rules + real LOS.

    (S1) every WALKABLE cell c (floor or OPEN doorway — a CLOSED door is
    not walkable) with ``has_line_of_sight(g, pos, c)`` — plus the anchor
    itself, unconditionally (S-B: the walkability predicate is waived for
    the anchor, even when its cell is a wall — edge case E6);
    (S2) every WALL cell w AND every CLOSED door (D5: a closed door's face
    is revealed exactly like a wall) that has a walkable 4-orthogonal
    neighbour in the (S1) set.

    Door-aware: LOS is the real :func:`has_line_of_sight` with the grid's
    closed-door set (or the caller-supplied ``closed`` set — used by the
    session tests that inject controlled door state), so a closed door
    blocks exactly like a wall (incl. corner-cut) and an open door is
    transparent. Deliberately independent of the visibility/session
    implementation (re-implements the same rules).
    """
    if closed is None:
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
