# Design — Generated Maps: GM-Specified X×Y Dungeon Generator

**Status:** build-ready spec. New feature: the GM picks a grid size in
**cols × rows** (X by Y) and the server **procedurally generates** a dungeon —
outer border wall, rooms, solid 1-cell walls, sparse doorways — for that exact
size. No image involved. Generated maps plug into the exact same
preview → "Open map in session" flow as uploads and remain fully editable by
GM paint.
**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins. (No divergence expected: generation reuses the frozen
data model, cell types, REST shapes, and WS protocol.)
**Code referenced (read, not modified by the spec):** `app/grid.py`
(cell conventions, sample map), `app/detection.py` (`classify_doors` doorway
semantics, `grid_to_thumbnail_png`), `app/server.py` (`_handle_upload`
response/error shapes), `app/main.py` (`maps_registry`, id helpers),
`app/pathfinding.py` (A* corner-cut rule), `app/imaging.py` (PNG encode),
`app/static/index.html` / `app/static/app.js` / `app/static/style.css`
(`#upload-view`), `tests/`, `docs/design/gm-controller.md`,
`docs/design/awareness-ring.md`, `docs/design/wireframes.md` §7.

---

## 1. What changes (summary)

| # | Change | Where |
|---|---|---|
| G1 | New **map generator**: BSP (binary space partition) dungeon generation into a `cols`×`rows` grid — rooms, 1-cell walls, **sparse tree-structured doorways**, guaranteed connectivity. Pure function, stdlib only. | new module `app/generation.py` |
| G2 | New REST endpoint **`POST /api/maps/generate`** with the same response shape as `POST /api/maps/upload` (so the existing preview flow is reused unchanged). | `app/server.py` |
| G3 | The GM's "New map" view gains a **"Generate map" tab** next to "Upload map": Cols / Rows / optional Seed + [Generate] → same preview panes → same "Open map in session". | `app/static/index.html`, `app/static/app.js`, `app/static/style.css` |
| G4 | Acceptance test suite: generator unit tests (deterministic invariants), endpoint tests, e2e pathfinding proof. | `tests/test_generation.py`, `tests/test_api.py` (new class), `tests/test_ws.py` or `scripts/e2e_proof.py` (e2e step) |

**What does NOT change:** the data model (`Grid` = `{name,width,height,
cells, image}`, `cells ∈ floor|wall|doorway`), the sample dungeon, image
upload/detection, the WS protocol (no new message types), movement/
pathfinding rules, awareness rules, GM paint, and the 1-GM + 6-player session
rules. A generated map is just another entry in `maps_registry` — everything
downstream (`GET /api/maps`, `GET /api/maps/{id}`, `use_map`, paint, A*,
awareness, thumbnails) works on it with zero changes.

---

## 2. Behavior statement

The GM specifies a map as **`cols` × `rows`** (X by Y, integers, 8–60 each)
plus an optional **seed**. The server generates and registers a dungeon map of
that exact size in which:

1. **Every room is reachable from every other room.** The walkable-cell graph
   (BFS over `floor` + `doorway` from any floor cell) visits **all** floor
   cells. This is the *connectivity invariant* (tested, §9 C5).
2. **Doors are sparse, by construction.** Each BSP split cuts exactly **one**
   doorway, so the room-adjacency (door) graph is a **tree**: connected with
   **no loops**. `doors = rooms − 1` exactly. There is **no** probability of
   extra doors — the default (and only) behavior makes detours a *normal*
   property of the map: many pairs of geometrically adjacent rooms share a
   solid wall with no gap, so reaching some rooms requires routing through
   others. This is the *sparseness + detour invariant* (tested, §9 C6/C7).
3. **The map boundary is all wall** (a ring of wall cells around the entire
   grid). Every room is a solid rectangle of **at least 3×3** floor (by
   construction), so the map is never "all wall" and entity spawn
   (`_find_free_floor`) always finds a cell.
4. **Same seed + same size ⇒ same map** (reproducible); no seed ⇒ a different
   map on every call (stdlib `random.Random`).
5. **GM stays editor of record.** After generation, the GM paints
   `floor`/`wall`/`doorway` on the map exactly as after an upload (WS `paint`
   / REST paint). Generation is a suggestion; nothing locks the grid.

---

## 3. Generation algorithm (BSP) — precise specification

New module: **`app/generation.py`**, stdlib only (`random`, and the `Grid`
dataclass from `app.models`). Public API:

```python
GEN_MIN_EDGE = 8
GEN_MAX_EDGE = 60

def generate_grid(
    cols: int, rows: int, name: str, seed: int | None = None
) -> Grid:
    """Generate a dungeon grid of exactly ``cols`` x ``rows`` cells.

    ``seed=None`` → unseeded random (different map each call);
    ``seed=<int>`` → reproducible: same (cols, rows, seed) ⇒ identical
    ``cells``. Raises ``ValueError`` if cols/rows are not ints in
    [GEN_MIN_EDGE, GEN_MAX_EDGE] (bools are NOT ints — reject ``isinstance``
    with an explicit bool check, same style as ``app/server.py``).
    """
```

### 3.1 Terminology and invariants

A **room** = the floor rectangle carved for one BSP leaf. A **door** = the
single `"doorway"` cell carved at one BSP internal node. The generator returns
a `Grid` only; it does **not** return room/door metadata (QA helpers in §9
recover them from the cells alone — this keeps the wire/registry surface
minimal).

**Invariants (proven in §3.6, tested in §9):**

- **(I1) Exact size:** `width == cols`, `height == rows`.
- **(I2) Border wall:** every cell with `x==0 or y==0 or x==cols-1 or
  y==rows-1` is `"wall"`.
- **(I3) Connectivity:** BFS/DFS from any single floor cell over walkable
  cells (`floor`+`doorway`) visits **every** floor cell.
- **(I4) Door count = tree count:** `#doorway cells == #rooms − 1`, where
  `#rooms` = connected components of "floor" cells computed with **doorways
  treated as walls** (flood fill over cells `== "floor"`, 4-directional).
  (No stray doorways: the generator only ever writes the door cells it
  carved, and GM painting is a later, user-caused mutation.)
- **(I5) Detour property:** for `#rooms >= 3` there exists **at least one
  pair of rooms sharing a wall edge with no doorway on that edge** — i.e. two
  geometrically adjacent rooms that are *not* connected by a door.
- **(I6) Determinism:** `generate_grid(c, r, name, seed=S)` called twice
  yields identical `cells`; different seeds differ (tested with ≥ 2 seeds;
  "high probability" phrasing is avoided in the test — it asserts equality
  for the same seed and inequality for two specific seeds, both
  deterministically true for the fixed seed set below).
- **(I7) A*-traversable passages:** every door is a 1-cell gap in an
  otherwise solid 1-cell wall, flanked by **walkable** cells (floor — or a
  doorway carved earlier, which is walkable too) on both opposite sides —
  traversable by `app/pathfinding.py` in exactly two **orthogonal** steps
  (orthogonal steps need no elbow cells, so the diagonal corner-cut rule can
  never block a corridor crossing; a diagonal *through* a door is legal
  whenever both diagonal neighbours happen to be floor — harmless either
  way).

### 3.2 Partition (phase 1)

```
rng = random.Random(seed)          # seed may be any int (0, negative OK);
                                   # seed=None → random.Random() (unseeded)

# Interior region: the border ring (x=0, y=0, last col, last row) is NEVER
# carved — it stays wall. This guarantees (I2) by construction.
region = (1, 1, cols-2, rows-2)    # (x, y, w, h) of the interior

# Phase 1: partition. Purely geometric + rng for tie-breaks only.
stack = [region]
rooms = []          # leaf regions, in stack-visit order
internal = []       # internal nodes: (region, axis, lo, hi), in the SAME
                    #   (preorder) order the splits were made

while stack:
    (x, y, w, h) = stack.pop()
    # A region is a leaf if it cannot split:
    #   vertical split needs w >= 6   (a 1-cell wall + >=3 + >=3)
    #   horizontal split needs h >= 6
    can_v = w >= 6
    can_h = h >= 6
    if not (can_v or can_h):
        rooms.append((x, y, w, h))          # LEAF → one room
        continue
    if can_v and can_h:
        axis = "v" if rng.random() < 0.5 else "h"   # tie-break draw
    else:
        axis = "v" if can_v else "h"
    if axis == "v":
        # left_w in [3, w-3]: both children are >= 3 wide.
        left_w = rng.randint(3, w - 3)
        lo = (x, y, left_w, h)
        hi = (x + left_w + 1, y, w - left_w - 1, h)
        # shared wall column = x + left_w; shared rows = y .. y+h-1
        internal.append(((x, y, w, h), "v", lo, hi))
    else:
        top_h = rng.randint(3, h - 3)
        lo = (x, y, w, top_h)
        hi = (x, y + top_h + 1, w, h - top_h - 1)
        # shared wall row = y + top_h; shared cols = x .. x+w-1
        internal.append(((x, y, w, h), "h", lo, hi))
    stack.append(hi)      # push in any fixed order (hi then lo); the
    stack.append(lo)      # ORDER IS PART OF THE ALGORITHM (determinism)
```

**Carving:** start with `cells[y][x] = "wall"` for **every** cell. Then, for
each leaf room `(x, y, w, h)`, set **every cell of the region**
`(x..x+w-1, y..y+h-1)` to `"floor"` — a leaf **fills its entire region**
(no inset). Walls therefore come from exactly two sources:

  (a) the **outer border** — the root region is the interior
  `(1, 1, cols-2, rows-2)`, so the border ring (`x=0`, `y=0`, `x=cols-1`,
  `y=rows-1`) belongs to no region and is never carved (guarantees I2);
  and
  (b) the **split lines** of internal nodes — the single wall line a parent
  keeps between its two children (e.g. column `ax+aw` for a vertical split).
  A split line belongs to **no leaf region**: the left child's region ends
  at `ax+aw-1`, the right child's begins at `ax+aw+1`, so carving never
  overwrites it.

Consequences (all used by the invariants): walls between rooms are exactly
**1 cell thick**; every interior non-wall cell belongs to exactly one room;
every room is a solid rectangle of **≥ 3×3** (a split's children each keep
≥ 3 on the split axis, and a dimension can never shrink below 3); and no
two split lines cross (proven in §3.6).

### 3.3 Door carving (phase 2)

For each internal node, in the **recorded (preorder) order**, carve exactly
one doorway:

```
for (region, axis, lo, hi) in internal:          # same order as phase 1
    (ax, ay, aw, ah) = region
    if axis == "v":
        col  = ax + aw                 # the node's split line (1 wall column)
        span = range(ay, ay + ah)      # full length of the line
    else:
        row  = ay + ah
        span = range(ax, ax + aw)
    candidates = []
    for t in span:
        if axis == "v":
            a, b = (col - 1, t), (col + 1, t)   # the two sides of the line
        else:
            a, b = (t, row - 1), (t, row + 1)
        # A door only works where BOTH sides of the line are already
        # walkable: "floor" (all carving is done by now) or a "doorway"
        # placed by an earlier phase-2 node (doors add walkability, never
        # remove it). This is what keeps every door a real corridor: a
        # naive uniform pick could land on a row where one side is a
        # descendant's perpendicular split line (a wall cell), dead-ending
        # the door and disconnecting a sub-tree.
        if walkable(cells, a) and walkable(cells, b):
            candidates.append(t)
    # `candidates` is NEVER empty: it always contains at least 3 entries —
    # the top 3 rows/cols of the line have floor on both sides (corner
    # lemma, §3.6) — so this draw can never fail.
    t = candidates[rng.randrange(len(candidates))]
    if axis == "v":
        cells[t][col] = "doorway"
    else:
        cells[row][t] = "doorway"
```

where `walkable(cells, (x, y))` is `cells[y][x] in ("floor", "doorway")`.

Notes:

- **Erratum (pseudocode vs implementation):** the pseudocode's `col = ax + aw` / `row = ay + ah` (the parent region's far edge) is incorrect — the implemented (correct) split line is the child's far edge `lo[0]+lo[2]` / `lo[1]+lo[3]`, i.e. `x + left_w` per §3.2, because a region's cells span ax..ax+aw-1, so `ax+aw` is one past the edge and would be wall-free floor outside the node's region. The line's span (`range(ay, ay+ah)` / `range(ax, ax+aw)`) is identical either way, so the RNG call order is unaffected and determinism and the §3.6 proofs hold unchanged.
- The cell being written is always `"wall"`: it lies on the node's own split
  line, and **no two split lines cross** (§3.6) — so it can never be another
  node's door, and no earlier phase-2 write touched it. The reference
  implementation asserts the pre-write value is `"wall"` (a test invariant,
  not a runtime path).
- `candidates` is never empty (corner lemma, §3.6), so
  `rng.randrange(len(candidates))` never raises — **generation cannot fail
  for any legal size; there is no retry/fallback path to implement**.
- After placement, the door cell has **walkable cells on both opposite
  sides** (left/right for a vertical line, up/down for a horizontal line) —
  that is the corridor tokens walk through — and **wall cells on the other
  two sides** (the rest of its own line). That is exactly the geometry the
  image detector's doorway heuristic (`app/detection.classify_doors`: a cell
  with walls on two opposite sides) recognizes, so a generated door is
  indistinguishable from a detected one to every consumer (pathfinding,
  paint tools, thumbnails, the legend).

**Why a tree:** each internal node's door connects exactly the two child
sub-trees (left vs right, or top vs bottom). A room is a *leaf* — internal
nodes carve no floor of their own — so the adjacency graph over rooms has
one edge (door) per internal node. A binary tree with `#internal =
#rooms − 1` edges is connected and acyclic (full proof in §3.6, with the
inductive argument over the node's two child sub-trees).

### 3.4 Sizing rules (design decision #2)

- **`cols`, `rows`: integers in `[8, 60]` inclusive** (`GEN_MIN_EDGE`,
  `GEN_MAX_EDGE`). Rationale for the floor: 8 (interior 6×6) is the smallest
  size at which the BSP is **guaranteed to produce ≥ 4 rooms** (and hence
  the detour property, I5); below 8 the interior is ≤ 5 on at least one
  axis and the root region may not split at all (7×7 → interior 5×5 → a
  single 5×5 room, 0 doors) — a "dungeon" with one room and no doors does
  not meet the requirement, so the size is rejected rather than produced.
  Rationale for the cap: matches the upload auto-size cap
  (`detection.MAX_GRID_EDGE = 60`) and the frontend's existing `min=8 max=60`
  number inputs, so the canvas renderer, thumbnails, and A* all operate
  within the ranges they are already validated for.
- **Degenerate small sizes (still valid):**
  - `8×8`: interior 6×6. First split forced 3|3 (the coin decides the
    axis); the 3×6 (or 6×3) child splits 3|3 along its long dimension
    (forced); the 2×6 (or 6×2) child splits 3|3 (forced). Result:
    **exactly 4 rooms — two 3×3 and two 2×3 (transposed depending on the
    first axis) — and 3 doors**. (So the first split is the *only* coin for
    8×8 — the map is one of exactly two layouts.)
  - `8×16`: interior 6×14 — e.g. a vertical 3|3 split gives a 3×14 and a
    2×14 band, each of which splits repeatedly along the long dimension:
    rooms are 3×k and 2×k slabs (k ≥ 3). Still ≥ 4 rooms, all invariants.
- **Max size:** `60×60` → interior 58×58 → roughly 100–150 rooms (split
  depth ~ log with a 3-cell minimum); generation is O(cells) plus O(rooms)
  random draws — well under 10 ms. 60 is a hard cap, not a performance edge.
- **Aspect ratios:** fine at any ratio; rooms keep the BSP rectangle shape.
  (A 60×8 map: interior 58×6 → the 6-tall axis can split (6 ≥ 6), the 58
  axis splits repeatedly → a stack of 3–5 tall rooms, ≥ 4 rooms by the same
  root-split argument.)
- Non-integer / out-of-range / bool values are rejected by the endpoint
  (§5) before `generate_grid` is ever called; `generate_grid` still
  validates defensively and raises `ValueError` (mirroring `detect_grid`'s
  `cols and rows must both be positive integers` style — but with the 8–60
  range).

### 3.5 Seed (design decision #3)

- Optional `seed` (JSON int). Any integer, including 0 and negatives, is a
  valid seed. `null` / omitted ⇒ `random.Random()` (OS-entropy unseeded) —
  different map per call.
- **One `random.Random` instance for the whole map.** Phase 1 consumes
  tie-break/size draws; phase 2 consumes door-position draws, **in the same
  recorded node order**. The call order is fully determined by the
  algorithm + (cols, rows, seed), so output is byte-stable:
  **same `cols`, `rows`, `seed` ⇒ identical `cells`** (name has no effect on
  geometry).
- This also means (a) the GM can share a seed to reproduce a map, and (b)
  tests can pin exact expected layouts for small sizes (e.g. 8×8) without
  snapshot-brittle large maps — the 8×8/8×12 families are small enough to
  assert by hand in a test.

### 3.6 Why the invariants hold (proof sketch — for the engineer's review,
not to be tested separately)

- **Border (I2):** the root region is the interior `(1, 1, cols-2, rows-2)`;
  the border ring belongs to no region, so it is written only by the
  initial all-wall fill and never carved.
- **Every region has width ≥ 3 and height ≥ 3.** A split's children each
  keep ≥ 3 on the split axis (`randint(3, w-3)`), and dimensions only change
  via splits. Hence every room (leaf region) is a solid ≥ 3×3 floor
  rectangle — spawn (`_find_free_floor`) always succeeds (§2 bullet 3).
- **Rooms ≥ 4; recursion terminates.** Interior ≥ 6×6 ⇒ the root splits
  (both axes available; the coin picks one). Each root child inherits the
  parent's *other* dimension (≥ 6) ⇒ each root child splits again ⇒ leaves
  ≥ 4 (each root child's sub-tree has ≥ 2 leaves, one per grandchild side;
  deeper splits only add leaves). The minimum is exactly 4 (the 8×8 case).
  Termination: a split consumes its 1-cell line, so each child's area ≤
  parent's area − 3·(other dimension) < parent's area — strictly decreasing
  area, so the recursion must end. (60×60 ≈ 100–150 rooms; O(cells) work.)
- **No two split lines cross.** A descendant node's region lies strictly
  inside one child of the ancestor, so (a) a descendant's *same-orientation*
  line is ≥ 3 cells away from the ancestor's line along the split direction
  (region inset ≥ 1, plus the child's minimum width 3), and (b) a
  descendant's *perpendicular* line spans only the descendant's own
  columns/rows, which exclude the ancestor's line entirely. Lines of the
  same level (disjoint sibling regions) are disjoint. Consequence: each
  line's cells are wall **except its own single door** — so (i) a door's
  along-line neighbours are always wall (C4's "walls on two opposite sides"
  shape is automatic), and (ii) two doors can never share a cell, so the
  door count is exact (I4).
- **Corner lemma (a door can always be placed).** For an internal node's
  vertical line at column `x`, line rows `ay..ay+ah-1`: the top corner cell
  of the left child region, `(x-1, ay)`, belongs to a leaf whose region has
  right boundary `x-1` and top boundary `ay` (the leaf containing a region
  corner must carry that corner on its own boundary; no split line passes
  through a region corner). That leaf fills its region and has height ≥ 3,
  so `(x-1, ay)`, `(x-1, ay+1)`, `(x-1, ay+2)` are all floor. Symmetrically,
  the right child's top-corner leaf makes `(x+1, ay..ay+2)` floor. Hence
  **rows `ay`, `ay+1`, `ay+2` always have floor on both sides** of the line
  (likewise the bottom 3 rows) — i.e. `candidates` always has ≥ 3 entries.
  (If a child is a leaf, the whole line has floor on that side; if it is
  internal, the corner argument applies recursively to its sub-tree.) Same
  statement rotated 90° for horizontal lines (leftmost/rightmost 3 columns).
- **The door graph is a tree (I3, I4).** Induction on the node. A leaf:
  one 4-directionally connected floor rectangle — one room, one connected
  walkable set. An internal node: its walkable set = (left child's walkable
  set) ∪ (right child's walkable set) ∪ {its door}. The door has a walkable
  neighbour in **each** child's set (placement rule; walkability is monotone
  — later doors only add cells), so the union is connected; the two child
  sets are disjoint and joined through exactly one cell, so no cycle is
  introduced. By induction the root's walkable set — **all** floor + doorway
  cells — is 4-directionally connected ⇒ BFS from any floor cell visits
  every floor cell (I3). Rooms: a 4-directional flood fill over strict
  `floor` cells (doorways treated as walls) yields exactly one component
  per leaf (each leaf is a ≥ 3×3 floor rectangle; two distinct leaves never
  share an edge without at least one wall cell — their regions are
  separated by a split line). Doors: exactly one per internal node, and in
  any full binary tree #internal = #leaves − 1 ⇒ **#doors = #rooms − 1**
  (I4).
- **Detour property (I5).** Take any internal node and its split line. By
  the corner lemma, ≥ 3 line cells have floor on both sides; the line
  carries exactly one door. Hence at least two line cells are **door-free
  wall cells with floor immediately on both sides**; the floor cells on
  those two sides belong to two *distinct* rooms that share a wall edge
  with **no doorway on it**. (Rooms ≥ 4 ⇒ at least one internal node always
  exists.) This is the structural reason detours are the norm, not a
  rare accident; QA verifies it directly from the grid in §9 C7 (which is
  why the acceptance test is grid-level rather than relying on this
  argument).
- **A* traversal (I7).** Each door is a 1-cell gap in a 1-cell wall with
  walkable cells on both opposite sides (placement rule) → crossed by two
  orthogonal A* steps; orthogonal steps never invoke the corner-cut rule.
  A corner-to-corner route is a sequence of room interiors and such
  corridor crossings (I3 says the walkable set is connected, and the only
  cells that join two different rooms are doors, all of which are
  corridors).

### 3.7 Placement of the code

- `app/generation.py` — `GEN_MIN_EDGE`, `GEN_MAX_EDGE`, `generate_grid`.
  Imports: `random`, `from app.models import Grid`. Nothing else (no
  `detection` import needed — carving writes `"floor"`/`"wall"`/`"doorway"`
  directly; we do *not* run `classify_doors`, which would only flag the same
  door cells and could over-flag painted maps, keeping the generator
  dependency-free and the cell semantics explicit).
- `app/server.py` — new route + `_handle_generate` (thin; mirrors
  `_handle_upload`).
- No changes to `app/models.py`, `app/grid.py`, `app/session.py`,
  `app/pathfinding.py`, `app/awareness.py`.

---

## 4. Data model — no changes

The generator returns a standard `Grid`:

- `name`: the trimmed request `name`.
- `width/height`: `cols`/`rows` exactly (I1).
- `cells`: `list[list[str]]`, `cells[y][x]`, values in `CELL_TYPES`
  (validated by `Grid.__post_init__`).
- `image`: **`None`** (no source image — the upload flow sets this to the
  file name; generation has none, and `GET /api/maps/{id}` / WS `map`
  payloads already tolerate `"image": null`).

Registration: `_register_map(map_id, grid)` — identical to upload. The
`use_map` swap, REST/WS paint, `GET /api/maps[/{id}]`, and the WS `map`
payload all therefore work with **zero changes**.

---

## 5. REST endpoint contract (design decision #5)

### 5.1 Request

`POST /api/maps/generate` — JSON body (NOT multipart; same 32 MB cap and
JSON parsing path as upload, reusing `_read_body_checked` +
`_parse_json_body`):

```json
{ "name": "The Deep Warrens", "cols": 24, "rows": 16, "seed": 1337 }
```

| Field | Type | Required | Validation |
|---|---|---|---|
| `name` | string | yes | non-empty after `.strip()` (else 400 `'name' must be a non-empty string`). Trimmed value is stored. |
| `cols` | int | yes | integer (bool rejected), `8 ≤ cols ≤ 60` |
| `rows` | int | yes | integer (bool rejected), `8 ≤ rows ≤ 60` |
| `seed` | int | no | if present and not `null`: integer (bool rejected), any magnitude |

Validation order (first failure wins): body-object check → `name` →
`cols` (type, then range) → `rows` (type, then range) → `seed` (type only).

Error style — **identical to `_handle_upload`**: `400` +
`{"error": "<message>"}` via `_error_json`, with the exact message strings:

- not a JSON object → `400 "request body must be a JSON object"`
- bad JSON / body > 32 MB → `400 "request body must be JSON"` /
  `400 "request body too large"` (shared helpers)
- `400 "'name' must be a non-empty string"`
- `400 "'cols' must be an integer in 8-60"` (covers non-int, bool, and
  out-of-range — one message, checked as `isinstance(cols, bool) or not
  isinstance(cols, int) or not (8 <= cols <= 60)`)
- `400 "'rows' must be an integer in 8-60"`
- `400 "'seed' must be an integer"`

(`generate_grid`'s defensive `ValueError` would also map to 400 with its
message, mirroring upload's `except ValueError → 400 str(exc)`, but the
endpoint checks first, so it is a belt-and-braces path.)

### 5.2 Response (mirrors upload exactly)

`200`, `Cache-Control: no-store`:

```json
{
  "id": "the-deep-warrens",
  "name": "The Deep Warrens",
  "width": 24,
  "height": 16,
  "cells": [["wall", "wall", "..."], "..."],
  "thumbnail": "data:image/png;base64,iVBOR..."
}
```

- **Key set is exactly `{"id","name","width","height","cells","thumbnail"}`**
  — identical to the upload response (tests pin the upload key set with
  `assertEqual(set(data.keys()), ...)`; the frontend and the registry both
  assume this shape). Note: `image` is NOT in the upload response (it only
  appears in `GET /api/maps/{id}`), so it is not in this one either.
- `id`: `slug_map_id(name)` → `_unique_map_id(...)`, falling back to
  `_timestamp_map_id()` when the slug is empty (e.g. non-ASCII name) —
  exactly the upload id path, so two generated maps with the same name get
  `-2`, `-3`, … suffixes.
- `width == cols`, `height == rows` (I1).
- `thumbnail`: `grid_to_thumbnail_png(grid)` (4 px/cell, same palette as
  upload: dark wall / light floor / amber doorway) — so generated maps show
  up in the preview and in the topbar `#map-thumbnail` exactly like uploads.
- The map is registered **before** the response is sent (same as upload: no
  draft state; if the GM never clicks "Open map in session" the map still
  exists server-side, listed by `GET /api/maps`).

### 5.3 Route registration notes (engineer)

- Register the route in `build_app()` immediately next to `@app.post(
  "/api/maps/upload")` (after the permissive `GET`-only detail route is
  spliced; POST is not matched by that route, so no shadowing — same reason
  `/api/maps/upload` works today).
- No new imports beyond `from app.generation import generate_grid`
  (plus `grid_to_thumbnail_png` which `app/server.py` already imports).

---

## 6. Frontend — "Generate map" tab in `#upload-view` (design decision #6)

### 6.1 Decision: tabs in the SAME view, SAME preview, SAME open flow

`#upload-view` currently has two panels: `#upload-form` (idle) and
`#upload-preview` (preview state). We add **one source tab bar** with two
tabs — **Upload** (the existing form, untouched) and **Generate** (a new
form) — and **one shared preview panel** that serves both flows.

Why not a second preview panel: the preview panes, note line, and
`#btn-start-map` → `use_map` flow are byte-identical for both sources;
duplicating them would double the surface area and the BUG-002/BUG-008
invariants that live in `openUploadedMap()`. The preview panel is relabeled
per source (cosmetic) and otherwise unchanged.

**States:** `#upload-view[data-state]` stays `idle | preview` (the CSS
already keys off it). A new module-level `state.uploadSource =
"upload" | "generate"` (JS only, not DOM) drives: which form is visible in
`idle`, and the preview's title/copy. New preview data-state is NOT needed —
the preview panel is source-agnostic.

### 6.2 Wireframe

**A. Idle, "Generate" tab active (desktop ≥ 1024px; panels are 720px
centered, same as today):**

```
┌────────────────────────────────────────────────────────────────────────┐
│ LITTLEDUNGEONS ▸ New map                              [Back (Esc)]     │
├────────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐   │
│ │  [ Upload map ]   [ Generate map ]            #map-source-tabs   │   │
│ │   (tab buttons; the active tab is accent-filled; disabled        │   │
│ │    while #upload-view data-state="preview")                      │   │
│ └──────────────────────────────────────────────────────────────────┘   │
│ ┌ GENERATE MAP ────────────────────────────────────────────────────┐   │
│ │                                                                  │   │
│ │  Map name   [ The Deep Warrens____________ ]   #gen-name         │   │
│ │                                                                  │   │
│ │  Grid size                                                        │   │
│ │    Cols (X)  [ 24 ]        Rows (Y)  [ 16 ]                       │   │
│ │    #gen-cols (min 8 max 60)   #gen-rows (min 8 max 60)           │   │
│ │                                                                  │   │
│ │  Seed (optional)  [          ]   #gen-seed                       │   │
│ │    (same seed + same size ⇒ same map; blank = random)            │   │
│ │                                                                  │   │
│ │  Note: walls are generated for this exact size; doors are sparse │   │
│ │  (a tree — no loops), so some rooms require detours.             │   │
│ │  #gen-note (muted small)                                         │   │
│ │                                                                  │   │
│ │                                        [ Generate map ]          │   │
│ │                                        #btn-generate (primary;    │   │
│ │                                        busy state "Generating…") │   │
│ └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

**B. Idle, "Upload" tab active:** identical to today (tab bar added above;
`#upload-form` unchanged).

**C. Preview (both sources):**

```
│ ┌ DETECTED MAP  ────────────────────────────────────────────────────┐   │
│ │   (title: "Detected map" for upload, "Generated map" for generate)│   │
│ │   #preview-title                                                 │   │
│ │  Source            Grid / Detection        Server thumbnail      │   │
│ │  ┌───────────┐    ┌──────────────────────┐   ┌────────────────┐  │   │
│ │  │  [img]    │    │  #preview-canvas     │   │ #preview-      │  │   │
│ │  │           │    │  (shared renderer)   │   │  thumbnail     │  │   │
│ │  └───────────┘    └──────────────────────┘   └────────────────┘  │   │
│ │  #pane-source       #pane-grid-title (per source)  (always shown) │   │
│ │  (HIDDEN for generate — there is no source image)                 │   │
│ │  Generated 24×16 grid — map id "the-deep-warrens".  #upload-note  │   │
│ │  (upload copy: "Detected 24×16 grid — map id …")                 │   │
│ │  Note: generation is a suggestion — you are the editor of record. │   │
│ │  (upload copy: "Note: detection is a suggestion — …")             │   │
│ │  #preview-note                                                    │   │
│ │                         [ Start over ]  [ Open map in session ]   │   │
│ │                         #btn-back       #btn-start-map (primary)  │   │
│ └──────────────────────────────────────────────────────────────────┘   │
```

Mobile (< 768px): the existing `.upload-preview-panes { flex-direction:
column; }` already stacks the panes; the generate form is single-column
already; tab bar wraps under 360px if needed (two short tabs — it fits).

### 6.3 Exact element IDs and states

**HTML (`app/static/index.html`, inside `#upload-view`):**

New:

| ID | Element | Notes |
|---|---|---|
| `#map-source-tabs` | `div` with two `button`s | placed between the topbar and `#upload-form`; class `map-source-tabs` (new CSS block, mirrors `.upload-actions` rhythm) |
| `#tab-upload` | `button` | text `Upload map`; default active |
| `#tab-generate` | `button` | text `Generate map` |
| `#gen-form` | `div.panel` | `hidden` initially (Upload tab is the default) |
| `#gen-name` | `input[type=text]` | maxlength 40, placeholder `The Deep Warrens` (same convention as `#upload-name`) |
| `#gen-cols` | `input[type=number]` | `min=8 max=60 step=1`, default value `24` |
| `#gen-rows` | `input[type=number]` | `min=8 max=60 step=1`, default value `16` |
| `#gen-seed` | `input[type=number]` | `step=1`, `placeholder="random"`, no default value |
| `#gen-note` | `p.muted.small` | the note copy in wireframe A |
| `#btn-generate` | `button.btn.btn-primary` | `disabled` initially |
| `#preview-title` | (repurpose) `h2.section-label` of `#upload-preview` | current text `Detected map` becomes this id; JS sets it per source |
| `#pane-source` | `div` | wraps the existing `<h3>Source</h3> + <img #preview-image>`; `hidden` for generate |
| `#pane-grid-title` | (repurpose) the `<h3>` above `#preview-canvas` | text `Detection` (upload) / `Grid` (generate) |
| `#preview-note` | (repurpose) the note `<p>` in `#upload-preview` | copy swapped per source |

Unchanged: `#upload-form` and all its children, `#upload-preview`,
`#preview-image`, `#preview-canvas`, `#preview-thumbnail`, `#upload-note`,
`#btn-back` (its visible text changes to `Start over` — id unchanged),
`#btn-start-map`, `#btn-detect`, `#btn-back-top`.

**JS (`app/static/app.js`):**

- `els`: add `mapSourceTabs`, `tabUpload`, `tabGenerate`, `genForm`,
  `genName`, `genCols`, `genRows`, `genSeed`, `btnGenerate`,
  `genNote`, `previewTitle`, `paneSource`, `paneGridTitle`, `previewNote`.
  (The test harness auto-stubs any `#id` queried at boot via
  `makeEl()` — new `els` entries are safe. `tests/test_frontend.py`
  already asserts on real IDs by grepping `index.html`; new static-assert
  tests should use the same approach.)
- `state.uploadSource = "upload"` (added to the view-local `state` object;
  purely client-side — never sent over the wire, no server counterpart).
- Tab switching:

  ```js
  function setSourceTab(source) {           // "upload" | "generate"
    if (els.uploadView.dataset.state === "preview") return;  // locked in preview
    state.uploadSource = source;
    els.uploadForm.hidden = source !== "upload";
    els.genForm.hidden = source !== "generate";
    syncTabStyles();
    if (source === "generate") syncGenerateButton();
  }
  ```

  Tab buttons: `aria-pressed` (or an `is-active` class) marks the active
  tab; disabled (aria-disabled + no-op) while `data-state="preview"`.
- `resetUploadForm()` (existing) now also resets the generate tab:
  `setSourceTab("upload")`, `genSeed.value = ""`, `genName.value = ""`,
  `syncGenerateButton()`, and restores the preview's upload-side copy
  (`#preview-title` = `Detected map`, etc.) — so "New map…" always reopens
  the view on the Upload tab, exactly like today.
- `syncGenerateButton()`:

  ```js
  function syncGenerateButton() {
    const nameOk = els.genName.value.trim().length > 0;
    const cols = Number(els.genCols.value), rows = Number(els.genRows.value);
    const sizeOk = Number.isInteger(cols) && Number.isInteger(rows)
      && cols >= 8 && cols <= 60 && rows >= 8 && rows <= 60;
    els.btnGenerate.disabled = !(nameOk && sizeOk) || genBusy();
  }
  ```

  (mirrors `syncUploadButton()`; wired to `input` events on `#gen-name`,
  `#gen-cols`, `#gen-rows`.)
- `generateMap()` (parallel to `uploadMap()`):

  ```js
  async function generateMap() {
    setGenerateBusy(true);
    try {
      const body = { name: els.genName.value.trim(),
                     cols: Number(els.genCols.value),
                     rows: Number(els.genRows.value) };
      if (els.genSeed.value !== "") body.seed = Number(els.genSeed.value);
      const resp = await fetch("/api/maps/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error ||
        `generate failed (HTTP ${resp.status})`);
      state.uploadedMap = {
        id: data.id, name: data.name, width: data.width, height: data.height,
        cells: data.cells, thumbnail: data.thumbnail || null,
        dataUrl: null,           // no source image → #pane-source hidden
      };
      showUploadPreview();
    } catch (err) {
      toast(`Generate failed: ${err.message}`, "error");
      setGenerateBusy(false);
    }
  }
  ```

  `setGenerateBusy(busy)` mirrors `setUploadBusy` but on `#btn-generate`
  (label `Generating…`).
- `showUploadPreview()` — the ONE shared function — gains the source branch
  (everything else identical, including the preview-canvas render via the
  shared `drawGridOnCanvas` and `#btn-start-map.disabled = false`):

  ```js
  const gen = state.uploadSource === "generate";
  els.previewTitle.textContent = gen ? "Generated map" : "Detected map";
  els.paneSource.hidden = gen;                       // no source image
  els.previewImage.src = m.dataUrl || "";            // only when shown
  els.paneGridTitle.textContent = gen ? "Grid" : "Detection";
  els.previewNote.textContent = gen
    ? "Generation is a suggestion — you are the editor of record. " +
      "Paint to add rooms, walls, or extra doors."
    : "Note: detection is a suggestion — you are the editor of record. " +
      "(Side-by-side before/after painting lands in Iteration 6.)";
  els.uploadNote.textContent = gen
    ? `Generated ${m.width}×${m.height} grid — map id “${m.id}”.`
    : `Detected ${m.width}×${m.height} grid — map id “${m.id}”.`;
  ```

- `openUploadedMap()` — **UNCHANGED.** `#btn-start-map` sends
  `{ type: "use_map", map_id: state.uploadedMap.id }` on the same socket and
  switches to the map view; a generated map is already in `maps_registry`,
  so the BUG-002 flow works verbatim. The map view's topbar then shows the
  generated map's name + `#map-thumbnail` (server thumbnail — same data-URL
  as upload).
- `#btn-back` ("Start over") → `resetUploadForm()` (back to idle, Upload
  tab, generate fields reset).
- Keyboard: `Enter` in `#gen-name` / `#gen-cols` / `#gen-rows` / `#gen-seed`
  triggers `generateMap()` when `#btn-generate` is enabled (parity with the
  lobby's Enter behavior).
- Toasts: success `Map “{name}” generated and registered.` (replaces the
  upload toast text for the generate source); failure as above.

**CSS (`app/static/style.css`, small new block near the upload styles):**

```css
/* Map source tabs (generated-maps spec §6) */
.map-source-tabs { display: flex; gap: var(--s1); max-width: 720px;
                   margin: 0 auto var(--s3); }
.map-source-tabs .tab-btn {
  padding: 8px 14px; border-radius: var(--r-control);
  background: transparent; border: 1px solid var(--border, #2a3040);
  color: inherit; cursor: pointer;
}
.map-source-tabs .tab-btn.is-active { background: var(--accent, #1971c2);
                                      border-color: transparent; }
.map-source-tabs .tab-btn:disabled { opacity: .5; cursor: default; }
#gen-form .gen-grid-size { display: flex; gap: var(--s3); }   /* Cols + Rows side by side, like .upload-grid-size */
```

(Reuse existing tokens; if `--border` is not defined in the app, use the
existing button border color. Tablet/mobile: no new breakpoints needed —
the tab bar is two short buttons that fit ≥ 320px; forms are already
single-column; the preview panes already stack < 768px.)

### 6.4 UX invariants to keep (explicit, for the engineer)

- Upload flow is pixel- and behavior-identical (tab just defaults to it).
  All existing `#upload-*` IDs, the `#btn-detect` busy state, and the
  `#preview-image` behavior for uploads are unchanged.
- Only ONE of `#upload-form` / `#gen-form` is visible at a time in `idle`;
  the tab bar is the only way to switch. In `preview` the tabs are locked
  (disabled) and `#upload-preview` is the only visible panel — same as
  today's idle→preview exclusivity.
- The GM's role gating is unchanged: `#btn-new-map` (which opens this
  view) is `gm-only`; players never see the upload view (they get
  `#no-map`).
- Editor of record: after "Open map in session", the map is the session's
  map; the bottom-bar paint tools (Select/Floor/Wall/Door) work on it
  because it is in the same session grid — no special code.

---

## 7. Edge cases

| # | Case | Behavior |
|---|---|---|
| E1 | **Min size `8×8`** | Exactly 4 rooms (two 3×3 + two 2×3, transposed), 3 doors; one of exactly two layouts (the first split is the only coin). All invariants hold (I1–I7). Valid, playable. |
| E2 | **`8×16` / `60×8` (extreme aspect)** | ≥ 4 rooms; long axis splits repeatedly; short axis never splits below 3/3. Invariants hold. |
| E3 | **Seed reproducibility** | Same `(cols, rows, seed)` → identical `cells` (I6). `seed: 0` and `seed: -7` are valid seeds. Same seed, **different size** → different (non-comparable) maps; that is expected, not a bug. |
| E4 | **No seed** | Fresh unseeded `random.Random()`; consecutive calls differ (not asserted in tests beyond sanity). |
| E5 | **Spawn / `use_map` swap** | ≥ 4 rooms of ≥ 3×3 floor ⇒ `_find_free_floor()` always finds a cell; entities re-park on swap exactly as with uploads (session code unchanged). |
| E6 | **Awareness / fog** | Works automatically — awareness only reads `grid.cells` (LOS + Chebyshev). Generated doorways are walkable + wall-adjacent, identical to detected ones. No changes. |
| E7 | **GM paints after generate** | WS `paint` / REST paint mutate the registered grid (same object identity as session grid after `use_map`). The tree invariants may then be *broken by the user* (extra doors, walled-off rooms) — that is the editor-of-record contract, not a bug. |
| E8 | **Two maps, same name** | Second id gets the `-2` suffix (upload id rules). |
| E9 | **Non-ASCII name** (e.g. "Crypté") | Slug empty → `_timestamp_map_id()` fallback (same as upload). Map works; preview toast shows the original name. |
| E10 | **`cols`/`rows` floats/strings/bools** | 400 per §5.1 (e.g. `cols: 24.0` → `"cols" must be an integer in 8-60`; `true` → same). |
| E11 | **Very deep maps at 60×60** | Generation < 10 ms; thumbnail 240×240 px (60×4) — same as any 60-wide upload. |
| E12 | **Generate while a session is mid-game on another map** | The generated map is merely registered; the session keeps its current map until the GM clicks "Open map in session" (`use_map`), identical to today's upload flow. |

---

## 8. Wire protocol recap (for the engineer)

- **No WS protocol changes.** No new message types, no field additions.
  Generated maps flow through the existing surfaces only:
  - `POST /api/maps/generate` → registers the map (new REST route, §5).
  - `GET /api/maps` → lists it (`{id, name, width, height}`).
  - `GET /api/maps/{id}` → serves it (`{..., "image": null, "cells", ...}`).
  - `{type:"use_map", map_id}` → GM swaps the session to it (unchanged).
  - `{type:"paint", x, y, cell_type}` → GM edits it (unchanged).
  - `welcome`/`state` → `map` payload carries it like any grid (unchanged).
- **New REST route:** `POST /api/maps/generate` — request/response/error
  shapes per §5 (response key set byte-identical to `/api/maps/upload`).
- **Cell convention:** generated gaps are `"doorway"` (walkable; matches
  detection semantics, paint tools, thumbnails, and the legend).
- **Frozen-surface check:** `GET /health`, all existing routes, the WS
  protocol, the sample dungeon, and the upload route are untouched.

---

## 9. Acceptance criteria (for QA)

Testable with the existing harness: pure-Python generator (unittest-style
`tests/test_generation.py`), REST via `tests/test_api.py`'s
`ServerTestCase` (`get_json`/`post_json` on a real port), WS via
`tests/wsclient.py` + `make_server` (e2e), Node static checks via
`tests/test_frontend.py`. All criteria below are deterministic.

### 9.1 Shared test helpers (define once in `tests/test_generation.py`,
importable from the API/e2e tests)

```python
def room_components(cells, w, h):
    """Flood fill 4-dir over cells == 'floor' ONLY (doorways count as
    walls here). Returns {component_id: [cell, ...]}."""

def room_id_map(cells, w, h):
    """(cell → component_id) for every floor cell; None elsewhere."""

def wall_adjacent_pairs(cells, w, h):
    """Room pairs separated by a solid wall edge: for every horizontal wall
    cell (x, y) with floor on left AND right → pair {room(x-1, y),
    room(x+1, y)}; for every vertical wall cell (x, y) with floor above AND
    below → pair {room(x, y-1), room(x, y+1)}. Returns a set of frozenset
    room-id pairs (skip pairs whose two ids are equal — impossible across a
    wall, but explicit)."""

def doorway_pairs(cells, w, h):
    """For each 'doorway' cell d with floor on both opposite sides
    (up+down or left+right), the room pair {room_id(left), room_id(right)}
    or {room_id(up), room_id(down)}. Returns a set of frozenset room-id
    pairs. (Every generated door satisfies the side check by C4; the check
    here keeps the helper honest for hand-made grids too.)"""

def floor_reachable(cells, w, h):
    """BFS from the FIRST floor cell (row-major) over floor+doorway
    (4-dir is sufficient — if 4-dir connects all floors, 8-dir does too);
    returns the set of reached cells."""

def count_doors(cells, w, h):
    return sum(row.count("doorway") for row in cells)
```

Generation convenience used by all of them:

```python
def gen(cols, rows, seed=0, name="t"):
    return generate_grid(cols, rows, name, seed)
```

Every criterion below runs over a **fixed sweep** of cases, e.g.
`SIZES = [(8,8), (8,16), (16,8), (12,12), (24,16), (40,30), (60,60)]` and
`SEEDS = [0, 1, 42, 1337, -7]` (subset per criterion where noted) —
cheap enough that the sweep is one assertion per pair.

### 9.2 Criteria

- **C1 — Exact dimensions.** For every `(c, r)` in `SIZES`: response
  `width == c`, `height == r`; `len(cells) == r` and every row length `c`.
  (Both at the generator level and via the endpoint.)
- **C2 — Outer border all wall.** Every border cell is `"wall"` (the
  `x==0 / y==0 / x==c-1 / y==r-1` ring), for every case in the sweep.
- **C3 — Cell vocabulary.** Every cell is in `{"floor","wall","doorway"}`;
  at least one `"floor"` exists; no cell is out of `CELL_TYPES` (grid
  construction would raise, but assert anyway from the JSON).
- **C4 — Doors sit in walls (geometry).** Every `"doorway"` cell has
  walls on **both opposite sides** (up+down or left+right) — the detection
  doorway shape — and **walkable** cells (floor or doorway) on the other
  pair of opposite sides. (Implies the passage is 2 orthogonal A* steps.)
- **C5 — Connectivity invariant (the marquee test).** For every case:
  `floor_reachable(cells, w, h)` (BFS from any/first floor cell over
  floor+doorway) **== the set of all floor cells**. Run from the first
  floor cell; optionally spot-check from a second, non-adjacent floor
  cell. Failure mode this catches: a missing door that isolates a
  sub-tree.
- **C6 — Sparseness (tree bound).** For every case:
  `count_doors == len(room_components) − 1` (rooms counted by flood-filling
  `floor` cells with doorways treated as walls). Given connectivity (C5),
  this single equation proves (a) the door graph is a **tree** — a loop
  would need `doors ≥ rooms` — and (b) the count is exact (every
  `"doorway"` cell is one corridor between two rooms; no stray doorway
  cell). Also assert `count_doors >= 3` (≥ 4 rooms ⇒ ≥ 3 doors).
- **C7 — Detour property (no door between every adjacent room).** For
  every case: `wall_adjacent_pairs − doorway_pairs ≠ ∅` — there exists at
  least one pair of rooms sharing a wall edge with **no** doorway on it.
  Plus a behavioral variant for a mid-size case (e.g. 24×16, seed 42):
  pick one such door-less pair `(A, B)`, pick floor cells `a ∈ A`,
  `b ∈ B` nearest the shared wall, and assert
  `find_path(grid, a, b)` (the real A* from `app.pathfinding`) **visits at
  least 3 distinct room components** — i.e. the route demonstrably
  detours through an intermediate room rather than going straight across.
- **C8 — Seed reproducibility.** `generate_grid(c, r, name, seed=S)`
  called twice → identical `cells` (deep compare). For one fixed case
  (24×16): `cells(seed=1) != cells(seed=2)` (deterministic given the
  algorithm — verified once, then the assertion is stable). Also: name
  does not affect cells (`generate_grid(c, r, "A", seed=S).cells ==
  generate_grid(c, r, "B", seed=S).cells`).
- **C9 — Validation errors (endpoint).** `POST /api/maps/generate`:
  - `{name:"x", cols:7, rows:10}` → `400 {"error":"'cols' must be an integer in 8-60"}`
  - `{name:"x", cols:10, rows:61}` → `400` (rows message)
  - `{name:"x", cols:"24", rows:10}` → `400` (cols message)
  - `{name:"x", cols:true, rows:10}` → `400` (bool rejected)
  - `{name:"", cols:10, rows:10}` → `400 {"error":"'name' must be a non-empty string"}`
  - `{cols:10, rows:10}` (no name) → `400` (name message)
  - `{name:"x", cols:10, rows:10, seed:"abc"}` → `400 {"error":"'seed' must be an integer"}`
  - `{name:"x", cols:10, rows:10, seed:true}` → `400` (seed message)
  - malformed JSON / non-object body → `400` (shared helpers' messages).
- **C10 — Success response shape (endpoint).** `200`;
  `set(body.keys()) == {"id","name","width","height","cells","thumbnail"}`
  (byte-identical to the upload key set — assert with the same
  `assertEqual(set(...), ...)` idiom `test_upload_creates_map_with_doorway`
  uses). `name` is the trimmed request name; `id` is a slug (e.g. name
  `"The Deep Warrens"` → id `"the-deep-warrens"`, `-2` on repeat);
  `thumbnail` starts with `data:image/png;base64,` and decodes to a PNG
  (`\x89PNG`); the new id appears in `GET /api/maps` and
  `GET /api/maps/{id}` returns the same `cells` with `"image": null`.
- **C11 — e2e: generate → open → pathfind corner to corner.** Over the
  live server (WS test or an `e2e_proof.py` step, chosen by the engineer —
  recommended: an `e2e_proof.py` step 8 mirroring step 6):
  1. `POST /api/maps/generate` `{name:"E2E Crypt", cols:24, rows:16,
     seed:42}` → 200, C1/C2 pass on the returned cells.
  2. GM joins a fresh session (`ws?session=e2e-gen-<seed>`); GM sends
     `{type:"use_map", map_id: <id>}` → next `state.map` has
     `width==24, height==16` and `cells ==` the generated cells.
  3. First player joins → spawns on a walkable cell (`welcome.you.entity_id`
     non-null, spawn cell is floor/doorway in the map).
  4. GM selects the player's token and sends `move` to the walkable cell
     furthest from spawn (pick via BFS distance on the returned grid —
     compute the BFS-farthest walkable cell from the spawn, that's the
     "opposite corner room"): server replies with a `path` frame (no
     `error`), path length ≥ 2, and every path step is a legal
     `is_valid_step` (no corner cuts; assert with the imported rule).
     A* finds a route **because C5 guarantees connectivity** — this is the
     end-to-end proof that generated maps are actually playable.
  5. GM paints a wall on the generated map (WS `paint`) → `state`
     broadcasts it (editor-of-record works on generated maps).
- **C12 — Frontend static checks (`tests/test_frontend.py` idiom).**
  `index.html` contains `#map-source-tabs`, `#tab-upload`, `#tab-generate`,
  `#gen-form`, `#gen-name`, `#gen-cols` (with `min="8"`/`max="60"`),
  `#gen-rows`, `#gen-seed`, `#btn-generate`, `#pane-source`,
  `#preview-title`; the existing upload IDs are all still present
  (regression guard). JS checks (harness): boot with the stubbed DOM
  doesn't throw; `setSourceTab("generate")` hides `#upload-form`, shows
  `#gen-form`, and sets `state.uploadSource === "generate"` (add
  `setSourceTab` + `generateMap` to the harness `EXPORTS` and make
  `els.genCols.value` etc. settable on the stub elements — the harness
  registry already auto-creates elements with settable `value`).
  *Optional* (recommended if time permits): extend `tests/js/harness.js`
  `fetch` from the hard reject to a recorded stub
  (`(url, opts) => { sent.push({url, opts}); return customResponse; }`)
  so `generateMap()` can be driven end-to-end in Node; the current reject
  stub still works (error toast path, no crash).
- **C13 — Regression (the existing suite).** `python -m pytest` **and**
  `python -m unittest discover -s tests -t .` fully green with **no
  modifications to existing tests** (new tests only); `scripts/e2e_proof.py`
  all-✓ (plus the new C11 step if added there); `GET /health` ok; the
  sample map is byte-identical (do not touch `app/grid.py`).

---

## 10. Explicit non-changes & open items

- **No change** to: `models.py`, `grid.py` (sample dungeon byte-stable),
  `detection.py`, `pathfinding.py`, `awareness.py`, `session.py`, `ws.py`,
  `imaging.py`; the WS message set; the upload route's behavior or error
  strings; the paint routes; the 1-GM + 6-player rules; `requirements.txt`
  (stdlib-only addition — `random` is already stdlib).
- **No map delete endpoint** (same as upload: re-generate/re-upload
  supersedes; maps are in-memory per PROJECT.md).
- **Open item (PM, optional polish, not blocking):** per-size "room
  density" preference or a loop-probability parameter (extra doors to make
  loops). Deliberately out of scope: the spec default is a pure tree
  (0 extra doors) because the requirement asks for detours to be the norm;
  a `loop_probability` (float, default 0) would be a trivial extension of
  phase 2 (for a fraction of internal nodes, carve a second door at a
  uniform position on the same shared line, skipping the first) and does
  **not** weaken I3/I5 — it only relaxes I4 to `doors ≤ rooms − 1 +
  extra`. Flagged here so it is a conscious decision, not an oversight.
- **Open item (PM, optional):** persisting generated maps to disk —
  rejected by the in-memory contract; revisit only if session save/load
  ships.
