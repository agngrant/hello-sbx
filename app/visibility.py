"""Per-player map visibility (explored-map spec) — pure functions over a Grid.

This module is the server-side core of the "explored map" feature
(docs/design/explored-map.md §3): it computes, for a token standing at a
cell, exactly which cells of the grid that token is in line of sight of,
and renders that knowledge (plus a player's accumulated memory) into the
per-viewer ``visibility`` tier matrix that rides in every player state
payload.

The module is pure stdlib and stateless: both functions are a pure function
of their arguments (same shape as ``app.awareness.build_awareness``), so they
are unit-testable without a session and the session layer stays a thin
orchestrator (compute the seen set, fold it into the player's explored set,
build the mask).

Two entry points:

* :func:`visible_cells` — the set of cells a token at ``pos`` can see now
  (spec §3.2, rules S1/S2). Line of sight is
  :func:`app.pathfinding.has_line_of_sight`, reused **verbatim** — map
  sight and entity awareness sight agree by construction (the same
  Bresenham digitization, the same no-corner-cut rule).
* :func:`build_visibility_mask` — the wire encoding: a list of ``height``
  row-strings of exactly ``width`` chars over the alphabet ``"S"`` (in
  sight now) / ``"E"`` (explored — previously seen, not in sight now) /
  ``"H"`` (hidden — never seen), row ``y`` = grid row ``y``, char ``x`` =
  grid column ``x`` (the same ``cells[y][x]`` orientation as ``map.cells``).
"""

from __future__ import annotations

from app.models import Grid
from app.pathfinding import has_line_of_sight

#: Walkable cell types — the (S1) sight predicate (same set as movement).
WALKABLE = ("floor", "doorway")

#: The four in-bounds orthogonal neighbour offsets (S2 wall-face rule), in a
#: fixed order so iteration is deterministic.
_ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _four_neighbours(x: int, y: int, w: int, h: int):
    """Yield the in-bounds orthogonal (4-neighbour) cells of ``(x, y)``."""
    for dx, dy in _ORTHOGONAL:
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h:
            yield (nx, ny)


def visible_cells(grid: Grid, pos: tuple[int, int]) -> set[tuple[int, int]]:
    """The set of cells a token at ``pos`` can see (spec §3.2).

    (S1) walkable cell  : in sight iff ``has_line_of_sight(grid, pos, cell)``
           (the anchor ``pos`` itself always counts, even if its cell is a
           wall — degenerate GM-placed token; edge case E6).
    (S2) wall cell      : in sight iff any of its four in-bounds
           orthogonal neighbours satisfies (S1) — you see the FACES of
           walls bounding what you can see; walls beyond corners stay
           hidden (wall-reveal model, spec §3.2).

    The sight relation is exactly the entity-awareness LOS: a walkable cell
    is S iff the token could see an entity standing on it.

    Invariants (all AC-tested): deterministic (S-A); the token cell is
    always in the result (S-B); symmetric for walkable cells (S-C); only
    walkable cells via (S1) / walls via (S2) (S-D); ``O(w*h*L)`` with
    ``L = max(w, h)`` (S-E); the grid is only read, never mutated (S-F).
    """
    # (S-B): the token cell is ALWAYS in sight — the walkability predicate
    # is waived for the anchor itself, even when it is a wall (a GM may
    # place an entity on a wall via override/``place``; edge cases E6/E16:
    # an isolated token still sees exactly its own square).
    seen: set[tuple[int, int]] = {pos}
    w, h = grid.width, grid.height
    for y in range(h):
        for x in range(w):
            cell = grid.cells[y][x]
            if cell == "wall":
                # (S2): wall visible iff a walkable 4-orthogonal neighbour
                # itself has line of sight from the token.
                for nx, ny in _four_neighbours(x, y, w, h):
                    if grid.cells[ny][nx] in WALKABLE and \
                       has_line_of_sight(grid, pos, (nx, ny)):
                        seen.add((x, y))
                        break
            else:
                if (x, y) == pos or has_line_of_sight(grid, pos, (x, y)):
                    seen.add((x, y))
    return seen


def build_visibility_mask(
    grid: Grid,
    explored: set[tuple[int, int]] | None,
    pos: tuple[int, int] | None,
    visible: set[tuple[int, int]] | None = None,
) -> list[str]:
    """Rows of exactly ``grid.width`` chars: ``"S" | "E" | "H"`` (spec §3.4).

    * row ``y`` = ``"".join("S" if (x, y) in visible_cells(grid, pos)
      else "E" if (x, y) in explored else "H" for x in range(width))``
    * ``pos is None`` → the explored set is FROZEN (no anchor, no sight):
      ``E`` where explored, else ``H`` — no ``S`` anywhere.
    * ``explored is None`` → treated as empty (nothing explored yet).
    * ``visible``: a precomputed ``visible_cells(grid, pos)`` set. When
      ``None`` it is computed here (the 3-argument form is self-contained);
      the session passes its own single computation so one snapshot performs
      ``visible_cells`` exactly once (spec §9 budget — the same set feeds
      both the explored fold and the mask).

    S wins over E (a cell in current sight is always S, even if it was
    explored long ago). Deterministic row-major construction → byte-stable
    encoding for a given ``(grid, explored, pos)``.
    """
    explored = explored or set()
    if pos is None:
        visible = set()
    elif visible is None:
        visible = visible_cells(grid, pos)
    rows: list[str] = []
    for y in range(grid.height):
        row = grid.cells[y]
        chars = []
        for x in range(grid.width):
            c = (x, y)
            if c in visible:
                chars.append("S")
            elif c in explored:
                chars.append("E")
            else:
                chars.append("H")
        rows.append("".join(chars))
    return rows
