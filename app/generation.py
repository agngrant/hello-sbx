"""Procedural dungeon generation (generated-maps spec §3) — BSP, stdlib only.

The GM picks an exact ``cols`` x ``rows`` size and (optionally) a seed; the
server generates a dungeon of that exact size — an outer border wall, solid
rooms, and **sparse tree-structured doorways** — with **no image involved**.

Algorithm (spec §3.2/§3.3, two phases over ONE ``random.Random`` instance so
the output is byte-stable for a given ``(cols, rows, seed)``):

* **Phase 1 — partition.** Binary-space-partition the *interior* region
  ``(1, 1, cols-2, rows-2)``. The border ring is never carved, so it stays
  wall (invariant I2 by construction). A region is a leaf when it can no
  longer split (a split needs >= 6 on its axis); otherwise it splits into two
  children, each keeping >= 3 cells on the split axis with a 1-cell wall
  between them. The recorded order of splits is a pre-order DFS.
* **Carving.** Every cell starts ``"wall"``; each leaf room then fills its
  ENTIRE region with ``"floor"`` (no inset). Walls therefore come from exactly
  two sources: the outer border and the 1-cell split lines.
* **Phase 2 — door carving.** For each internal node in the recorded (preorder)
  order, carve exactly one ``"doorway"`` on its split line, at a uniformly
  chosen position where BOTH opposite sides are already walkable (floor, or a
  door carved by an earlier node). One door per internal node ⇒ the room
  adjacency graph is a **tree**: connected, acyclic, ``#doors == #rooms - 1``.

The generator returns a plain :class:`app.models.Grid` only (no room/door
metadata — the QA helpers in ``tests/test_generation.py`` recover everything
from the cells). Cell semantics are explicit: carving writes
``"floor"``/``"wall"``/``"doorway"`` directly; :func:`app.detection.classify_doors`
is deliberately NOT run (it would only re-flag the same door cells and could
over-flag GM-painted maps), keeping the module dependency-free.

Invariants (proven in spec §3.6, tested in spec §9):

* I1 exact size, I2 all-wall border, I3 connectivity, I4 doors == rooms - 1,
  I5 the detour property, I6 determinism, I7 A*-traversable corridor doors.
"""

from __future__ import annotations

import random

from app.models import Grid

#: Smallest legal grid edge (interior 6x6 → guaranteed >= 4 rooms + I5).
GEN_MIN_EDGE = 8
#: Largest legal grid edge (matches detection.MAX_GRID_EDGE and the upload cap).
GEN_MAX_EDGE = 60

_WALKABLE = ("floor", "doorway")


def generate_grid(
    cols: int, rows: int, name: str, seed: int | None = None
) -> Grid:
    """Generate a dungeon grid of exactly ``cols`` x ``rows`` cells.

    ``seed=None`` → unseeded ``random.Random()`` (different map each call);
    ``seed=<int>`` → reproducible: same ``(cols, rows, seed)`` ⇒ identical
    ``cells``. ONE ``random.Random`` instance is used for the whole map (phase
    1 tie-break/size draws, then phase 2 door-position draws), so the RNG call
    order — and therefore the output — is fully determined by the algorithm +
    ``(cols, rows, seed)``. ``name`` is stored on the returned grid but has NO
    effect on geometry.

    Raises ``ValueError`` if ``cols``/``rows`` are not ints in
    ``[GEN_MIN_EDGE, GEN_MAX_EDGE]`` (bools are explicitly rejected —
    ``isinstance`` alone would admit them, so they are checked first, same
    style as ``app/server.py``).
    """
    if isinstance(cols, bool) or not isinstance(cols, int) or not (
        GEN_MIN_EDGE <= cols <= GEN_MAX_EDGE
    ):
        raise ValueError("'cols' must be an integer in 8-60")
    if isinstance(rows, bool) or not isinstance(rows, int) or not (
        GEN_MIN_EDGE <= rows <= GEN_MAX_EDGE
    ):
        raise ValueError("'rows' must be an integer in 8-60")

    rng = random.Random(seed) if seed is not None else random.Random()

    # All cells start wall; rooms (below) overwrite floor; doorways overwrite
    # a single wall cell each. cells[y][x] per the Grid convention.
    cells: list[list[str]] = [["wall" for _ in range(cols)] for _ in range(rows)]

    # -------------------------------------------------------------------
    # Phase 1 — BSP partition of the interior (border ring stays wall).
    # -------------------------------------------------------------------
    # region = (x, y, w, h) of the interior; the border ring (x=0, y=0, last
    # col, last row) is never carved, so it stays wall (invariant I2).
    stack = [(1, 1, cols - 2, rows - 2)]
    rooms: list[tuple[int, int, int, int]] = []      # leaves, stack-visit order
    internal: list[tuple[tuple[int, int, int, int], str, tuple, tuple]] = []
    while stack:
        (x, y, w, h) = stack.pop()
        # A split needs >= 6 on its axis (a 1-cell wall + >= 3 + >= 3).
        can_v = w >= 6
        can_h = h >= 6
        if not (can_v or can_h):
            rooms.append((x, y, w, h))               # LEAF → one room
            continue
        # Both axes available → coin-flip the axis; else forced.
        if can_v and can_h:
            axis = "v" if rng.random() < 0.5 else "h"
        else:
            axis = "v" if can_v else "h"
        if axis == "v":
            # left_w in [3, w-3]: both children keep >= 3 wide.
            left_w = rng.randint(3, w - 3)
            lo = (x, y, left_w, h)
            hi = (x + left_w + 1, y, w - left_w - 1, h)
            # shared wall column = x + left_w
            internal.append(((x, y, w, h), "v", lo, hi))
        else:
            top_h = rng.randint(3, h - 3)
            lo = (x, y, w, top_h)
            hi = (x, y + top_h + 1, w, h - top_h - 1)
            # shared wall row = y + top_h
            internal.append(((x, y, w, h), "h", lo, hi))
        # Push hi then lo: LIFO means lo is processed first. The order is part
        # of the algorithm (determinism) — do not reorder.
        stack.append(hi)
        stack.append(lo)

    # Carving: each leaf fills its ENTIRE region with floor (no inset).
    for (rx, ry, rw, rh) in rooms:
        for cy in range(ry, ry + rh):
            for cx in range(rx, rx + rw):
                cells[cy][cx] = "floor"

    # -------------------------------------------------------------------
    # Phase 2 — door carving, one door per internal node (recorded order).
    # -------------------------------------------------------------------
    def walkable(x: int, y: int) -> bool:
        return cells[y][x] in _WALKABLE

    for (region, axis, lo, _hi) in internal:
        (ax, ay, aw, ah) = region
        if axis == "v":
            # The split line is the 1 wall column between the two children:
            # lo's right edge = ax + left_w (left child spans ax..col-1, the
            # right child begins at col+1).
            col = lo[0] + lo[2]
            span = range(ay, ay + ah)     # full length of the line
        else:
            # Likewise, lo's bottom edge = ay + top_h (top child spans
            # ay..row-1, the bottom child begins at row+1).
            row = lo[1] + lo[3]
            span = range(ax, ax + aw)
        # A door only works where BOTH opposite sides are already walkable:
        # "floor" (all carving is done) or a "doorway" placed by an earlier
        # phase-2 node (doors add walkability, never remove it). `candidates`
        # is never empty (corner lemma, §3.6) — generation cannot fail.
        candidates = []
        for t in span:
            if axis == "v":
                a, b = (col - 1, t), (col + 1, t)     # left / right of the line
            else:
                a, b = (t, row - 1), (t, row + 1)     # up / down of the line
            if walkable(a[0], a[1]) and walkable(b[0], b[1]):
                candidates.append(t)
        t = candidates[rng.randrange(len(candidates))]
        if axis == "v":
            # The cell written is on this node's own split line; no two split
            # lines cross (§3.6) so it can never be a door or a floor — assert
            # the invariant (it is a test invariant, not a runtime branch).
            assert cells[t][col] == "wall"
            cells[t][col] = "doorway"
        else:
            assert cells[row][t] == "wall"
            cells[row][t] = "doorway"

    return Grid(name=name, width=cols, height=rows, cells=cells, image=None)
