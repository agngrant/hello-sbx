# Design — Explored Map: Player Fog of War with Memory

**Status:** build-ready spec. New feature: each **player's** rendered map shows
**three cell tiers** — cells in the player's current line of sight at full
detail, cells they have seen before (but can't see right now) **greyed out**,
and never-seen cells **not drawn at all**. The tiers are computed
server-side per player on every state recompute. The GM's map is unchanged
(full detail, always). **The entity awareness system is unchanged** — the
awareness overlay (tokens/dots/"?" markers) renders exactly as it does today
on top of the tiered map, and the `fog` field stays a wire-compat no-op.
**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins. (No divergence expected: this feature reuses the frozen
data model, cell types, WS message set, awareness model, and movement rules —
it adds one per-player, per-cell, render-only tier matrix.)
**Code referenced (read, not modified by the spec):** `app/models.py`
(`Grid`, `Player`, `to_dict` shapes), `app/grid.py` (sample dungeon),
`app/awareness.py` (`build_awareness` — UNCHANGED), `app/pathfinding.py`
(`has_line_of_sight` — reused, unchanged), `app/session.py` (`GameSession`:
`state_for`, `_broadcast`, `_on_use_map`, `_find_free_floor`),
`app/static/app.js` (`layoutCanvas` / `drawGridOnCanvas` /
`drawEntitiesAndDots` / `applyState` / click handling),
`app/static/index.html` (legend), `app/static/style.css` (tokens),
`tests/` (harness + unittest idioms), `docs/design/awareness-ring.md`,
`docs/design/generated-maps.md`.

---

## 1. What changes (summary)

| # | Change | Where |
|---|---|---|
| V1 | New pure module **`app/visibility.py`**: `visible_cells(grid, pos)` (the exact LOS set per §3) and `build_visibility_mask(grid, explored, pos)` (rows of `"S"/"E"/"H"` per §3.4). | new module `app/visibility.py` |
| V2 | **Per-player explored set** — a session-level dict `self._explored: dict[str, set[tuple[int,int]]]` on `GameSession` (NOT on the `Player` dataclass — §3.3). Updated on every state recompute; cleared on `use_map` (D3); frozen for token-less players (D2); removed on `leave` (D6). | `app/session.py` |
| V3 | **Wire:** every player's `welcome`/`state` payload gains an additive field `"visibility": <list of row-strings>` (§4). The **GM payload is byte-identical to today** — the key is absent (D4). The `map` field still carries the full grid (the client is trusted; the server-side map sharing is exactly today's). | `app/session.py` (`state_for`) |
| V4 | **Player rendering:** `drawGridOnCanvas` gains an optional `visibility` matrix parameter. When present, the cell loop honors the tiers: `S` → today's art, `E` → greyed palette (§6.1), `H` → nothing drawn (dark background shows). GM pass and **preview-canvas pass never pass a matrix** (§6.3). | `app/static/app.js` |
| V5 | **Legend:** players see three extra chips — in sight (full swatch), explored (grey swatch), hidden (dark swatch); GM legend unchanged. | `app/static/index.html`, `app/static/style.css` |
| V6 | **Tests:** visibility unit tests, session payload/lifecycle tests, awareness-unchanged + GM-unchanged regressions, perf budget test, e2e proof step, frontend harness/static checks. | `tests/test_visibility.py` (new), `tests/test_session.py`, `tests/test_ws.py`, `tests/test_frontend.py`, `tests/js/harness.js`, `scripts/e2e_proof.py` |

**What does NOT change:** the three-tier entity awareness model (FULL /
APPROXIMATE / INVISIBLE), `build_awareness`, awareness items, the
`set_awareness` message, all awareness test expectations (hard constraint —
§10); movement / A* / corner-cut rule / permissions; upload / generate
endpoints; the WS message set (only the additive `visibility` field); the
`players[]` / `Player.to_dict()` shapes; the sample dungeon; the GM's map,
awareness, tools, and payload; the `fog` flag (remains a wire-compat no-op).

---

## 2. Behavior statement

Given the requirement — *"the displayed map to the user only shows the
environment that they can see in line of sight in full detail, anything they
have been in before is shown greyed out when not in line of sight and
anything unexplored is not shown. Awareness still takes effect for entities,
and should not change behaviour."* — the behavior is:

1. **Three tiers, per player, per cell.** For player P with token at
   `(x, y)`, every cell of the session grid is in exactly one tier:
   - **S (seen):** the cell is in P's current line of sight (§3). Rendered in
     full detail — floor fill + grid lines, wall fill + hatch + border,
     doorway amber border + arch — byte-identical to today's rendering.
   - **E (explored):** the cell was S for P at some earlier moment on this
     map (it is in P's **explored set**) and is not S now. Rendered **greyed
     out / desaturated** (§6.1) — recognizably "known, but not in front of
     me".
   - **H (hidden):** never seen. **Not drawn** — no floor fill, no grid
     lines, no wall art, no doorway art; the canvas's existing dark
     background (`#171b26`) shows through, indistinguishable from empty
     space.
2. **"They have been in before" = their line of sight covered the cell at
   some earlier moment** — the explored set accumulates every cell that is
   ever S, *not* only cells their token physically stood on. (A token in an
   open room explores the whole room, not just its path.)
3. **Memory is monotonic within a map:** once a cell is E or S it never goes
   back to H while the same map is in play (D2). It returns to H only when
   the map is swapped (`use_map`), which clears all explored sets (D3).
4. **Sight is recomputed live.** Every state recompute (any mutation that
   triggers `_broadcast`: move, place, create/delete entity, set_team,
   set_awareness, paint, set_fog, use_map, join) recomputes S from the
   player's CURRENT token position and the CURRENT grid — so a GM-painted
   wall instantly hides what it blocks for players, and a GM's `place`
   instantly re-anchors the re-parked token's sight (the server never trusts
   client claims; the tier matrix is server-authoritative).
5. **The entity awareness overlay is untouched and renders ON TOP** of the
   tiered map exactly as today: awareness rings, own token, FULL contacts,
   APPROXIMATE grey-"?" markers, the sidebar list — all unchanged. An
   APPROXIMATE "?" marker may sit over a **hidden (H) region** — that is
   intended and correct: awareness senses unseen areas, this feature only
   tiers the *grid* (explicit per the requirement).
6. **The GM is exempt.** The GM's rendered map is always full detail (no
   tiering) and the GM's `welcome`/`state` payload is byte-identical to
   today's (no `visibility` key).
7. **The client's map data is unchanged.** `state.map` still carries the full
   grid in every payload (players already receive it today; every player
   already receives the full grid, so this leaks nothing new). The client is
   trusted to render only S/E cells; the tier matrix is a render directive,
   not a data filter.

---

## 3. The visibility model (server)

### 3.1 Where the code lives (design decision)

New module **`app/visibility.py`** — stdlib only, no session state:

```python
# app/visibility.py
"""Per-player map visibility (explored-map spec): pure functions over a Grid.

visible_cells(grid, pos)      -> set of (x, y) currently in line of sight
build_visibility_mask(grid, explored, pos) -> list of height row-strings
                                     of "S" / "E" / "H" (or [] when pos is
                                     None — no anchor, no sight)
"""

def visible_cells(grid: Grid, pos: tuple[int, int]) -> set[tuple[int, int]]:
    ...

def build_visibility_mask(
    grid: Grid, explored: set[tuple[int, int]] | None,
    pos: tuple[int, int] | None,
) -> list[str]:
    ...
```

Imported by `app/session.py` (the only server consumer). It imports
`has_line_of_sight` from `app.pathfinding` (reused verbatim) and the
`Grid` type from `app.models`. No other module imports it. Keeping the
algorithm a pure function of `(grid, pos)` — exactly the shape
`build_awareness(viewer, entities, grid)` already establishes — means it is
unit-testable without a session, and the session layer stays a thin
orchestrator (compute S, fold into explored, build the mask).

### 3.2 `visible_cells(grid, pos)` — the exact "cells in line of sight" set

**Chosen algorithm: full per-cell LOS against `has_line_of_sight` (D1).**

A cell `c` is **in line of sight** for a token at `pos` iff:

- **(S1)** `c` is walkable (`"floor"` or `"doorway"`) AND
  `has_line_of_sight(grid, pos, c)` is True — *or*
- **(S2)** `c` is a `"wall"` AND at least one of `c`'s **four orthogonal
  neighbours** (up/down/left/right, in-bounds) satisfies (S1).

The token's own cell `(pos)` is always in the set: `a == b` → True in
`has_line_of_sight`, and the walkability predicate is waived for the anchor
itself (the degenerate-spawn case, E6 in §11 — an entity may sit on a wall
cell via GM override/`place`, and must still see where it stands).

**Wall-reveal model note (consequence of S2):** a wall is revealed by the
*floor in front of it*, not by a sight line that merely ends at it. So a wall
at the far end of a diagonal corridor whose orthogonally-facing floor cells
are themselves hidden stays hidden — a deliberate choice: it keeps (S-C)
symmetry (symmetry would break under a "line-terminator" rule) and it is the
standard roguelike fog convention (you see the faces of walls bounding the
floor you can see). It also makes the corner-cut case sharp: a wall cell
diagonal to the token is revealed only through an *orthogonal* visible
facing cell, never through the token's own diagonal corner (worked example
W2). 8-neighbourhood reveal was considered and rejected: it would light up
diagonally-adjacent walls (e.g. the corner wall in W2) and visually nullify
the no-corner-cut rule.

Why this and not the alternatives (D1, with rationale):

- **Per-cell `has_line_of_sight` (chosen).** The spec is *exactly* the
  function the awareness system already uses for entities — so map sight and
  entity sight agree by construction (if the GM can see the enemy's token,
  the floor under it is also S; a token on the far side of a corner cannot
  be seen and the floor there cannot be either). It inherits the
  Bresenham corner-cut rule for free: a diagonal sight line squeezes through
  two wall elbows only if at least one elbow is walkable — the exact rule
  `has_line_of_sight` implements today. There is no second LOS definition to
  keep in sync.
- **Reconsidered alternative — walkable flood fill:** a BFS over walkable
  cells from the token would light up *every* walkable cell connected to the
  token, i.e. **all rooms linked by doorways**, with zero wall blocking.
  That is wrong for LOS: a wall with no door must hide what is behind it
  (worked example W2). Flood fill models *reachability*, not *visibility*.
  Rejected.
- **Raycast / DDA grid:** faster asymptotically but a second, independent
  LOS implementation that could disagree with `has_line_of_sight` on
  diagonal-edge cases, and the complexity buys nothing at this scale (see
  the performance budget, §9 — full per-cell LOS is comfortably within budget
  for ≤60×60 with ≤6 players, measured in AC11).

**Precise pseudo-code:**

```python
def visible_cells(grid: Grid, pos: tuple[int, int]) -> set[tuple[int, int]]:
    """The set of cells a token at ``pos`` can see (explored-map spec §3.2).

    (S1) walkable cell  : in sight iff has_line_of_sight(grid, pos, cell)
           (the anchor pos itself always counts, even if its cell is a
           wall — degenerate GM-placed token; edge case E6).
    (S2) wall cell      : in sight iff any of its four in-bounds
           orthogonal neighbours satisfies (S1) — you see the FACES of
           walls bounding what you can see; walls beyond corners stay
           hidden (wall-reveal model note, above).
    """
    seen: set[tuple[int, int]] = set()
    w, h = grid.width, grid.height
    for y in range(h):
        for x in range(w):
            cell = grid.cells[y][x]
            if cell == "wall":
                # (S2): wall visible iff a walkable 4-orthogonal neighbour is (S1).
                for nx, ny in _four_neighbours(x, y, w, h):
                    if grid.cells[ny][nx] in ("floor", "doorway") and \
                       has_line_of_sight(grid, pos, (nx, ny)):
                        seen.add((x, y))
                        break
            else:
                if (x, y) == pos or has_line_of_sight(grid, pos, (x, y)):
                    seen.add((x, y))
    return seen
```

**Invariants (all tested, §9):**

- **(S-A) Determinism.** Same `(grid, pos)` → identical set; the set is the
  same object type, no ordering dependence (sets of tuples; the mask
  builder iterates row-major, so the wire encoding is deterministic).
- **(S-B) Token cell always S.** `pos ∈ visible_cells(grid, pos)` for any
  `pos` in bounds — even when the cell is a wall (waived predicate) and even
  when all eight neighbours are walls (an isolated token still sees its own
  square — see E6).
- **(S-C) Symmetry.** `a ∈ visible_cells(grid, b)` iff
  `b ∈ visible_cells(grid, a)` for any two walkable `a`, `b` (Bresenham
  digitization is symmetric and the blocker test is a pure function of the
  cells between them). Tests exploit this to cross-check (a token standing
  where the player is must see the player's cell — and, being walkable, the
  reverse holds too).
- **(S-D) No cell of a non-walkable type can be S except via (S2).** A
  doorway is walkable, so doorways are S only via (S1) — a door is seen when
  sight passes through it (worked example W3).
- **(S-E) Cost bound.** `|visible_cells| ≤ w·h`; computation is
  `O(w·h · L)` where `L = max(w,h)` is the Bresenham length bound, with a
  small ×4 factor absorbed into the constant for the wall-neighbour
  predicate. No allocations beyond the returned set (no per-cell lists).
- **(S-F) Grid reads only.** Pure over `grid.cells`; never mutates.

**Worked examples** (all four are AC-pinned; the mini grids below are
`W`=wall, `.`=floor, `D`=doorway, `T`=token; 16×12 sample-dungeon refs use
its actual coordinates — see `app/grid.py`).

- **W1 — Open room.** Token in the middle of an open floor region: every
  floor/doorway cell of the region is S (no walls on any Bresenham line
  inside an open region), and **every wall cell 4-adjacent to the region is
  S** — the region's bounding wall faces. A second room across a
  door-less wall: its floor is NOT S (every line crosses a wall), and its
  walls are NOT S (none of their facing walkable neighbours are S). Result:
  the whole room + its wall faces light up; everything behind the wall is H.
- **W2 — The corner (the case that kills flood fill).** Pure corner-cut
  case, 4×3 grid (x0–3, y0–2), token `T` at (1,0); the diagonal wall (2,1)
  has **both orthogonal elbows** (2,0) and (1,1) as walls:

  ```
  y=0: . T W .
  y=1: . W W .
  y=2: . . . .
  ```

  Bresenham `T→(2,1)` is the single diagonal step (1,0)→(2,1); no wall
  lies *on* the line — but its elbows (2,0) and (1,1) are **both walls** →
  corner cut → `has_line_of_sight` False → **the wall (2,1) is NOT in the S
  set** (its four neighbours (2,0)/(1,1) are walls; (3,1)/(2,2) are unseen
  floor — no wall reveals through unseen cells) — exactly the no-corner-cut
  rule movement uses. The full mask for this grid (row-major, hand-verified
  — every line traced through the exact Bresenham + elbow rule):

  ```
  y=0: S S S H      (2,0) is S: it faces the token cell (1,0); (3,0) H: line crosses (2,0)
  y=1: S S H H      (1,1) is S: it faces (1,0) and (0,1); (2,1) H: corner cut
  y=2: S H H H      (1,2) H: line crosses the wall (1,1); (2,2)/(3,1)/(3,2) H
  ```

  **Variant:** repaint (1,1) to floor (one elbow opens). Then: (1,1) is S
  (clear line from (1,0)); the wall (2,1) is S — the step (1,0)→(2,1) now
  has elbows (2,0) wall + (1,1) floor → **one elbow open → passes** (a
  diagonal grazing a single wall corner passes), and the wall also faces
  the newly seen (1,1); (1,2) is S (the line (1,0)→(1,2) now passes
  through the open (1,1)). (2,2), (3,1), (3,2) stay H — their lines still
  cross the wall (2,1). New mask rows: `SSSH / SSSH / SSHH`.

  These are the exact "corner cut" and "grazing one wall corner" semantics
  of `has_line_of_sight`. **AC5 pins both masks string-for-string and
  re-derives every wall cell via the real `has_line_of_sight` (spec rule
  ⟺ implementation).**
- **W3 — Doorways extend sight only along lines through the door.** Sample
  dungeon, token at (1,1). The line (1,1)→(6,6) samples (2,2) (3,3) (4,4)
  (5,5) (6,6): no wall cell lies on it — (5,5) is the open doorway — and
  each diagonal step has at least one open elbow ((4,4)→(5,5): elbows
  (5,4) wall + (4,5) floor → passes; (5,5)→(6,6): elbows (6,5) floor +
  (5,6) wall → passes). So **(6,6) is S** — sight flows *through* the door
  along that exact diagonal. **The trap, worth pinning: (6,5) — the floor
  immediately past the door on the same ROW — is H.** Bresenham
  (1,1)→(6,5) samples (1,1) (2,2) (3,3) (4,4) (5,4) (6,5): the wall (5,4)
  blocks it *before the line ever reaches the doorway*. Likewise (6,7) is
  H ((1,1)→(6,7) samples (1,1) (2,3) (3,5) (4,7) (5,6) (6,7) — the wall
  (5,6) blocks it), the row-5 corridor floors (7,5)–(9,5) are H (e.g.
  (1,1)→(7,5) samples the wall (5,4)), the row-7 doorway (9,7) is H
  ((1,1)→(9,7) samples (5,4) → blocked before the door), the col-10
  doorway (10,4) is H ((1,1)→(10,4) samples (5,3) → blocked), and the
  right room plus the bottom band (x 6–13, y 8–10) are H (every line to
  them crosses col-5 wall cells). The moral: a doorway extends sight
  exactly like the entity-LOS rule — you see what a straight line from
  your token actually reaches, and the digitized line, not "the door
  opens", decides.
- **W4 — Sample dungeon, Alice's spawn, complete expected mask.** Token at
  (1,1) on the 16×12 sample map (rows y=0…11, cols x=0…15). Ground truth
  from `_SAMPLE_MAP_LINES`: the left region is floors x1–4 × y1–10 —
  **including row 7** (the row-7 wall starts at x5) — col-5 walls at
  y1–4 and y6–10 (doorway (5,5)); middle room x6–9 × y1–6; col-10 walls
  at y1–3 and y5–10 (doorway (10,4)); row-7 walls at x5–8 and x10–13
  (doorway (9,7)); bottom band x6–14 × y8–10. The mask (left = x0),
  every cell hand-verified against the exact Bresenham + corner rule and
  the 4-adjacent wall-face rule:

  ```
  y=0:  HSSSSHHHHHHHHHHH
  y=1:  SSSSSSHHHHHHHHHH
  y=2:  SSSSSSHHHHHHHHHH
  y=3:  SSSSSSHHHHHHHHHH
  y=4:  SSSSSSHHHHHHHHHH
  y=5:  SSSSSSHHHHHHHHHH
  y=6:  SSSSSSSHHHHHHHHH
  y=7:  SSSSSSHHHHHHHHHH
  y=8:  SSSSSSHHHHHHHHHH
  y=9:  SSSSSSHHHHHHHHHH
  y=10: SSSSSSHHHHHHHHHH
  y=11: HSSSSHHHHHHHHHHH
  ```

  - **S = 69 cells:** the left region's **40 floors** (all in clear LOS —
    spot: (1,1)→(4,10) samples (2,3), (3,6), (4,9), all floor); the
    doorway **(5,5)**; the single middle-room floor **(6,6)** (the
    diagonal through the door, W3); the **9 col-5 wall faces** (5,1)–(5,4)
    and (5,6)–(5,10) (each faces a seen col-4 floor); the **10 left-border
    faces** (0,1)–(0,10) (each faces a seen col-1 floor — (0,7) counts,
    since (1,7) is floor and seen); the **4 top-border faces** (1,0)–(4,0)
    and **4 bottom-border faces** (1,11)–(4,11). Count: 40 + 1 + 1 + 9 +
    10 + 4 + 4 = 69. ✓
  - **E = 0** (the explored set is empty before the first recompute).
    **H = 123.**
  - Qualitative facts QA must see (independent of the string): the entire
    left region is S; **exactly one** middle-room cell is S — (6,6); the
    famous trap **(6,5), directly past the door on the same row, is H**
    ((1,1)→(6,5) samples the wall (5,4) before reaching the door); (6,7)
    is H ((1,1)→(6,7) samples wall (5,6)); the corner wall (7,7) is H;
    the row-7 doorway (9,7) is H; the col-10 doorway (10,4) is H; the
    whole right room and the bottom band are H (every line crosses col-5
    wall cells); the top/bottom borders are H except above/below the left
    region (so (0,0), (0,11), (5,0), (5,11), (6,0) are H).
  - **AC2 asserts** this 12-row literal verbatim, the counts 69/0/123, a
    battery of spot cells (all the S/E/H cells listed above), and an
    **independent re-derivation** of the S-set straight from the real
    `has_line_of_sight` + the spec's wall-face rule (§12). The
    re-derivation is the oracle: if the literal and the re-derivation
    ever disagree, the re-derivation defines correctness and the literal
    is corrected (it is a fixture, not the definition).

### 3.3 The explored set — lifecycle (design decision D2)

**Storage: a session-level dict, NOT a field on `Player`.**

```python
# GameSession.__init__:
self._explored: dict[str, set[tuple[int, int]]] = {}   # player id -> cells
```

**Why not on the `Player` dataclass:** `Player.to_dict()`'s shape
(`{"id","name","role","entity_id","awareness_radius"}`) is pinned by
existing tests (the awareness-ring criteria assert `players[]` entries) and
by the frontend contract; adding a field would change the `players[]`
payload for **everyone** — and a set of coordinates has no JSON form that
belongs in `players[]` anyway (the cells travel only in the per-viewer
`visibility` matrix, per viewer). Session-level state also matches how the
session already keeps non-serialized per-viewer bookkeeping (`self._socks`,
`self._cid_by_sock`, `self._senders`) and it means the `players[]` /
`Player` wire shape is **byte-identical to today** — a hard requirement for
the awareness regression criteria.

**Lifecycle rules (each AC-pinned):**

- **Created lazily** at the player's first `state_for` (an empty set) — so a
  fresh join has an explored set with no special-casing in `join`.
- **Accumulated on every recompute** (every state snapshot for that player,
  whether broadcast, welcome, or `request_state`):
  `explored |= visible_cells(grid, token_pos)`. The fold is idempotent and
  cheap (set union into an existing set).
- **Monotonic within a map:** a cell never goes S/E → H. The only way out of
  E is `use_map` (D3) or the player leaving (D6).
- **Anchor = current token position.** Recomputed from the token's live
  position every time — after the GM `place`s it, after a reparking, after a
  move. If `player.entity_id is None` or the entity is missing from
  `self.entities` (token deleted — the same anchor-missing case that makes
  awareness empty): **no S region at all; the explored set is FROZEN** —
  no new cells are folded in, and every cell the player has seen is shown
  **greyed (E)** (the mask is built with `pos=None` → no S). Rationale:
  the requirement says *seen* things stay visible as greyed memory and
  *unseen* things stay dark; with no token there is no anchor to generate
  new sight, so the player keeps a "memory map" of everything they saw
  before their token was deleted — the least surprising reading, consistent
  with the awareness anchor-missing rule (they see no *entities*, they keep
  *map memory*). Nothing new is ever revealed. (AC8.)
- **Reconnect = memory kept.** The explored set lives on the session, and
  `detach()` (disconnect) deliberately keeps the Player; a reconnecting
  player re-attaches to the same Player record and therefore keeps their
  explored set. Their `welcome` mask reflects full memory (all past S/E).
  This is stated explicitly because the requirement's "been in before"
  implies durable memory — and it costs nothing (in-memory, per session,
  like everything else). (AC9.)
- **Map swap → cleared (D3).** `use_map` swaps the grid; old cells reference
  a different map (possibly a different size). `_on_use_map` does
  `self._explored.clear()` **before** the broadcast that recomputes from the
  new positions — so a swap never leaks old-map memory into the new map and
  no stale-coordinate cell can be marked E out of bounds. (AC7.)
- **Player leaves → pruned (D6).** `leave()` does
  `self._explored.pop(player_id, None)`. Rationale: `leave` is the full
  exit (the Player record is deleted, so any re-join is a brand-new player
  who legitimately starts fresh); pruning also bounds memory for
  churn-heavy sessions.
- **GM is exempt** — no entry is ever created for role `"gm"` (the GM has no
  token and never gets a mask; D4).
- **No persistence across process restarts** — the in-memory contract
  (PROJECT.md: no session save/load yet). A restart is a new session, so
  "fresh memory" is correct there by definition.

### 3.4 `build_visibility_mask(grid, explored, pos)`

```python
def build_visibility_mask(grid, explored, pos):
    """Rows of exactly `width` chars: "S" | "E" | "H".

    row y = "".join("S" if (x,y) in visible_cells(grid, pos)
                    else "E" if (x,y) in explored
                    else "H"  for x in range(width))

    pos is None  -> explored only: E where explored, else H (no S).
    explored None -> treated as empty (nothing explored yet).
    """
```

- Output: **`list[str]` of length `height`, each string exactly
  `width` characters** over the alphabet `{"S","E","H"}` — the wire shape
  of §4.
- S wins over E (a cell in current sight is always S, even if it was
  explored long ago).
- Deterministic row-major construction → byte-stable encoding for a given
  `(grid, explored, pos)`.
- Cost: one `visible_cells` call (the expensive part) plus an O(w·h)
  row-major classification pass — two set lookups per cell worst case.

### 3.5 `GameSession.state_for` — the integration point

```python
def state_for(self, viewer):
    is_gm = viewer.role == "gm"
    own = self.entities.get(viewer.entity_id) if viewer.entity_id else None
    payload = {
        "type": "state",
        "map": self.grid.to_dict(),
        "players": [p.to_dict() for p in self.players.values()],
        "entities": [e.to_dict() for e in self.entities.values()] if is_gm else [],
        "you_entity": own.to_dict() if (own is not None and not is_gm) else None,
        "awareness": self._awareness_for(viewer),
        "fog": self.fog,
    }
    if not is_gm:
        pos = (own.x, own.y) if own is not None else None
        mask = build_visibility_mask(self.grid,
                                     self._explored.setdefault(viewer.id, set()),
                                     pos)
        if pos is not None:
            self._explored[viewer.id] |= visible_cells(self.grid, pos)
        payload["visibility"] = mask
    return payload
```

- **Single choke point:** `welcome_for` and `request_state` both route
  through `state_for`, and `_broadcast`/`_announce_join` call
  `state_for` per connected viewer — so **every** server-built player
  snapshot carries the field with zero other wiring, and GM payloads are
  structurally untouched (the key is simply never added).
- **`state_for` is called under `self._lock`** everywhere (broadcast,
  request_state, announce_join) → the read-union of `_explored` is safe.
- The union runs for **that viewer only** inside their own `state_for` —
  there is no cross-viewer coupling: Alice's snapshot never mutates Bob's
  explored set, so per-viewer snapshots stay mutually independent (the
  property the whole awareness design relies on).
- `_on_use_map` additionally does `self._explored.clear()` immediately
  before its `self._run_b(self._broadcast())` (order: swap grid → re-park
  entities → clear explored → broadcast; the broadcast's `state_for`
  calls then re-seed every player's set from their NEW positions).
- `leave()` prunes the set. No other handler touches `_explored`.

---

## 4. Wire protocol (design decision D4 — additive field)

### 4.1 The `visibility` field

- **Players only.** Present in every player `welcome` and `state` payload:

  ```json
  "visibility": [
    "HHHHHHHHHHHHHHHH",
    "HSSESSHHHHHHSSEH",
    "..."
  ]
  ```

  - A JSON **array of `height` strings**, each string **exactly `width`
    characters**; row `y` corresponds to grid row `y`; char `x` corresponds
    to grid column `x` (same `cells[y][x]` orientation as `map.cells`).
  - Alphabet: `"S"` = seen now (full detail), `"E"` = explored (greyed),
    `"H"` = hidden (not drawn).
  - A matrix is **well-formed** iff: `len(vis) == map.height`, every row has
    `len == map.width`, and every char is in `"SEH"`. Clients may treat a
    malformed matrix as absent (defensive; the server never sends one).
- **GM: the key is ABSENT** (not `null`, not `[]`) — the GM payload is
  byte-identical to the pre-feature build in every byte except this key,
  which is deliberately not present. `null`/`[]` would force the client to
  special-case three states for a role that never uses them.
- **Player with no token (token deleted):** the key IS present; the mask is
  E/H only (no S) — §3.3 frozen-memory rule.
- **Size:** 1 char per cell → 3,600 chars at the 60×60 cap (plus 60 quote/
  comma overhead) ≈ **3.7 KB raw ≈ 3.7 KB on the wire** (it is ASCII, so
  JSON text ≈ raw). A 16×12 sample map is 192 chars. This rides in every
  snapshot exactly like `awareness` does today — snapshots are already
  per-viewer and full; one small extra field per player snapshot is within
  the existing "small game → snapshot is simplest" design (PROJECT.md §9).
- **Why row-strings (vs RLE rows or a flat int list):** the grids are small
  (≤ 3600 cells), the encoding is human-debuggable in a WS inspector (a QA
  engineer can *read* a mask), it is trivially verifiable in tests
  (`vis[y][x] == "S"`), and a flat 3600-entry int array would be the same
  size with worse ergonomics; RLE would save bytes only on very homogeneous
  maps for added encode/decode surface. Row-strings win on simplicity at
  this scale. (If a future map cap ever grows past ~200×200, RLE becomes
  worth revisiting — not now.)

### 4.2 What is NOT on the wire

- No new WS **message types** (client→server or server→client).
- No changes to `welcome`'s `you`, `map`, `entities`, `players`, `awareness`,
  `fog`, or to `path` / `error` frames.
- **`map` still carries the full grid for players** — same as today (players
  already receive the full `cells` array; this feature adds no data leak).
  The `visibility` matrix is a *render directive* over data the client
  already holds.
- **`fog` is unchanged** — stored, broadcast, and still a **no-op** that does
  not gate anything. The old fog mechanism was removed when the three-tier
  awareness model landed; the **explored map is now the actual fog-of-war
  mechanic** (memory + line-of-sight over the *map*, not over *entities*).
  The GM's `set_fog` toggle and the UI's fog checkbox keep working exactly
  as today (no-op), and nothing in this feature reads or writes the flag.

---

## 5. GM exemption

- `state_for(gm)` never computes a mask and never adds the key; the GM
  payload is deep-equal to the pre-feature payload (AC10).
- The GM's canvas render path passes **no** visibility matrix (§6.3) — full
  detail, always, including when players are in the same session (each
  client renders its own payload).
- The GM's awareness list, tools, paint, rings, and legend are untouched.

---

## 6. Frontend — rendering the tiers (design decision D5)

### 6.0 Wireframe — what a player sees (mockup)

ASCII mockup of the player's canvas shortly after the player has walked
from spawn (1,1) into the middle room (position ~(7,2)): `▢` = full-detail
cell (S), `░` = greyed cell (E), `·` = hidden, nothing drawn (H). The same
drawing for the **GM** is all `▢` (no tiering) — this is the entire visual
difference the feature introduces, per role.

```
   player view                          gm view
┌────────────────────────┐  ┌────────────────────────┐
│························│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢░░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢░░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢░░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢▢░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢░░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢░░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢▢░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢░░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢▢░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│░░▢▢▢▢▢▢▢▢▢░░░░░░░░░░░░░│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
│························│  │▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢▢│
└────────────────────────┘  └────────────────────────┘
 │ left region: ░ (E — seen at spawn,        │ GM: always full
 │ now out of sight)                         │ detail (unchanged)
 │ middle room: ▢ (S — in sight now)         │
 │ right room / bottom band: · (H — never    │
 │ seen, not drawn — dark background)        │
```

On the real canvas, `▢`/`░` are the §6.1 palettes (full-detail art vs
greyed art) and `·` is the untouched `#171b26` background — so the `·`
region is pixel-identical to the empty margin around the grid. The
awareness ring (dashed square) and the own token (blue "YOU" ring) render
on top exactly as today; an APPROXIMATE grey "?" may sit inside the `·`
region (intended, §2.5). The legend row for a player, bottom-left, reads
(existing chips first, then the three new `legend-explored` chips — §7):

```
[▢] floor   [▣] wall   [▣] doorway  │  [▲] friend  [●] neutral  [■] enemy
[?] unseen contact (within awareness range, sight blocked)   [⌗] awareness range
[▢] in sight   [░] explored   [·] hidden (not shown)
```

Responsive: the canvas layout is unchanged (the existing
`layoutCanvas()` sizing/centering and the debounced resize handler already
handle every viewport — the tiers are a paint-layer detail, not a layout
one); on mobile (< 768px) the legend already stacks (existing
`flex-direction: column` rule) and the new chips flow in the same wrapped
row, so no new breakpoint is needed.

### 6.1 Exact visual treatment (the greyed palette)

**`S` — seen (current line of sight):** rendered **exactly as today**:
floor fill `#efe9dc`, grid lines `#d9d1bd`, wall fill `#3b4252` with hatch
`#262b36` and border `#20242f`, doorway amber `#d97706` border + arch
glyph. No alpha changes, no color changes — pixel-identical to the
pre-feature build for those cells.

**`E` — explored (greyed / desaturated):** the same *art* (floor stays
flat, walls keep hatch + border, doorways keep border + arch — so the
memory map stays *readable as the same geometry*, not as a new texture)
recolored to a flat grey scale, with grid lines dimmed:

| Element | Value | Rationale |
|---|---|---|
| Explored floor fill | `#6b7280` (slate grey) | Clearly "paper but not lit": far darker than the full-detail floor `#efe9dc`, far lighter than the hidden background `#171b26` — the three tiers form a readable light→dark ramp (full ≈ 92% L, greyed ≈ 46% L, hidden ≈ 11% L). |
| Explored wall fill | `#4b5563` | Distinct from the explored floor (value contrast ≈ 0.35), and from the full-detail wall `#3b4252` (a desaturated *lighter* grey, not the same blue-grey) — a greyed room is recognizably the same room. |
| Explored wall hatch + border | `#3f4753` (both) | Flat and low-contrast: hatching remains as *texture* (walls stay identifiable as walls) but loses the crisp "in front of me" contrast of the full-detail hatch `#262b36`. |
| Explored doorway | floor fill `#6b7280`, border + arch `#8b94a3` (desaturated amber → neutral grey-blue) | Doorways stay identifiable (border + arch shape) with the amber saturation removed — "I know there's a door here; I'm not looking at it right now." |
| Grid lines | `#d9d1bd` at **30% alpha** (`rgba(217, 209, 189, 0.3)`), drawn only across S/E cells (§6.2) | The grid survives in memory (cells stay countable) but recedes; 30% is the "dimmed" target from the requirement decode. |

Contrast requirements (asserted by design, sanity-checked by QA): an `E`
cell must be **clearly distinguishable from both** an `S` cell (lighter,
warm) and an `H` region (darker, flat) at the minimum cell size (8px); an
`E` floor must be distinguishable from an `E` wall (≈0.35 value difference);
the greyed doorway border must be distinguishable from the greyed floor
(≈0.25 value difference). All values above satisfy these; the hex/alpha
constants live in the `T` token object (§6.4).

**`H` — hidden (never seen):** **nothing is drawn.** No floor fill, no grid
lines, no wall fill/hatch/border, no doorway art. The canvas's existing
background `#171b26` (painted by `layoutCanvas` before the grid) shows
through, so a hidden region is indistinguishable from the empty margin
around the grid. This is the point: the player cannot tell *where* the map
ends and unexplored space begins.

### 6.2 How `drawGridOnCanvas` changes

`drawGridOnCanvas` is **shared** by `#map-canvas` (the live map) and
`#preview-canvas` (upload/generate preview, GM-only flow). The tiering must
apply **only to the player's live map canvas**.

- Signature: `drawGridOnCanvas(canvas, ctx, visibility = null)`.
- When `visibility == null` (GM pass, preview pass, any future caller):
  the function runs **byte-for-byte today's behavior** — full-detail
  everything. This guarantees the preview canvas (GM-only, pre-session) and
  the GM view are untouched by construction, not by a role check.
- When `visibility != null`:
  - **Floor base (step 1):** instead of one big `fillRect` for the whole
    grid + one grid-line pass, iterate cells: for each S/E cell fill it
    with the tier's floor color; skip H cells entirely. Grid lines: for
    each cell edge that has an S/E cell on **at least one** side, draw the
    segment at the tier's line style (full `#d9d1bd` if the edge is
    S-side; `rgba(217,209,189,0.3)` if E-side; a shared S|E edge draws the
    S side's style). This keeps the existing "lines over the whole drawn
    area" look and avoids over-drawing H regions. (Implementation detail,
    not behavior: the engineer may batch the line pass into one path per
    style.)
  - **Walls (step 2):** the existing hatches/rects/borders lists are
    populated **only for S/E wall cells**, using the tier's palette.
  - **Doorways (step 3):** drawn only for S/E doorway cells, with the
    tier's border/arch color.
  - **Step 4 is UNCHANGED:** the `if (canvas.id === "map-canvas")
    drawEntitiesAndDots(ctx, s, ox, oy);` call and everything inside it —
    awareness rings, selection ring, tokens, FULL items, APPROXIMATE "?"
    markers, hover ring, paint preview — runs exactly as today.
- **Entity pass on top (subtlety (b), guaranteed):** the own token is
  always drawn regardless of its cell's tier. In practice its cell is **S
  by construction** (§3.2 (S-B): the token cell is always in its own
  visible set — including the degenerate on-wall case), so the own token
  always sits on full-detail art; the guarantee holds even if a future
  change ever produced a non-S own cell, because `drawEntitiesAndDots`
  never consults the matrix.
- **APPROXIMATE markers over hidden cells are INTENDED** (subtlety (a)):
  `drawUnknownDot` is unaffected; a grey "?" block may appear in a fully
  dark region — that is the awareness system doing its job (sensory
  awareness without eyes) and the requirement explicitly keeps it.

### 6.3 Call sites

| Call site | Change |
|---|---|
| `layoutCanvas()` (the map-canvas pass) | `drawGridOnCanvas(canvas, ctx, state.role === "player" ? state.visibility : null)`. Players get tiered cells; the GM gets `null` → today's full-detail render. |
| `showUploadPreview()` (preview canvas) | **UNCHANGED** — `drawGridOnCanvas(els.previewCanvas, ...)` with no third argument. The preview flow is GM-only and pre-session anyway; nothing can leak a matrix there. |
| Any future caller | Defaults to `null` = full detail. |

### 6.4 State + tokens (`app/static/app.js`)

- `state.visibility = null` (added to the `state` object; purely client-
  side render data).
- `applyState(msg)`: **`state.visibility = (state.role === "player" &&
  Array.isArray(msg.visibility)) ? msg.visibility : null;`**
  - For the GM `msg.visibility` is absent → stays `null`.
  - A malformed matrix (wrong lengths/charset) → treated as `null`
    (defensive; never crashes the render).
  - **Map swap / dimension change:** when `msg.map` dimensions differ from
    `state.grid` (the `mapChanged` branch already detects this), the stored
    `visibility` from the old map is discarded (the new message's matrix —
    which is always post-swap, §3.5 order — replaces it; and the server
    cleared explored on swap, so no old-map "E" can survive).
- New `T` tokens (alongside the existing palette):

  ```js
  exploredFloor: "#6b7280",
  exploredWall:  "#4b5563",
  exploredWallHatch: "#3f4753",
  exploredWallBorder: "#3f4753",
  exploredDoor:  "#8b94a3",
  gridLineDim:   "rgba(217, 209, 189, 0.3)",
  ```

- Mirrored CSS custom properties in `:root` (for the legend swatches):
  `--explored-floor: #6b7280; --explored-wall: #4b5563;` (hidden reuses the
  existing `--chrome-bg`/background `#171b26` family — the legend's hidden
  swatch uses `#171b26` to match the canvas background exactly).

### 6.5 Interaction over hidden cells (subtlety (d))

**Movement is UNCHANGED server-side and the client click logic is
UNCHANGED.** Concretely:

- A player **can** click a hidden cell (clicks are grid coordinates; the
  server validates bounds/ownership/walkability exactly as today; the
  client already works off `state.grid.cells`, which players already
  receive in full).
- Clicking a walkable-looking hidden cell: the server A* knows the real
  grid. If a path exists, the token walks there and **sight expands as it
  goes** — each step re-broadcasts a new mask; the player's token literally
  walks into the dark and the map reveals cell by cell around it. This is
  the intended, fun part of the feature; no routing restriction is added
  (restricting A* to "explored-only" would change movement semantics and
  fight the requirement, which is about *display*, not *legality*).
- Clicking a cell that is a **wall** (the client knows this from
  `state.grid.cells` even when the cell is drawn dark): the existing hint
  fires exactly as today — "Walls block movement" (players) / "…enable
  'Ignore walls'" (GM). Nothing new: the client's wall check reads the grid
  data, not the pixels, so hidden walls still reject a move. (The
  alternative — never telling the player why a move failed — would break
  the existing hint UX and is explicitly NOT done.)
- Hover ring: unchanged (drawn by `drawEntitiesAndDots`, map-canvas only,
  over whatever tier the cell has).
- Paint tools: GM-only, unaffected; the GM never has a matrix.

### 6.6 Awareness rings (subtlety (e))

The player's own dashed awareness ring (`drawAwarenessRings`) is **unchanged**:
it still draws around the own token at `awareness_radius`, on top of the
tiered cells, exactly as today — it may extend over E and H regions (it is a
*sensor* boundary, not a sight boundary; same logic as APPROXIMATE markers
over H).

---

## 7. Legend (design decision — player-only chips)

The `#legend` div is in the shared map view; chips are toggled per role
(exactly the pattern `.gm-only` uses today — but for the *legend* the chips
live inside the player-visible DOM and are hidden via a `body.is-gm` CSS
rule, since the legend has no `gm-only`-classed wrapper today).

- **New chips, players only** (class `legend-explored`, hidden by
  `body.is-gm .legend-explored { display: none; }`):
  - `<span class="legend-chip legend-explored"><i class="swatch floor"></i>in sight</span>`
    (reuses the existing full-detail floor swatch)
  - `<span class="legend-chip legend-explored"><i class="swatch explored"></i>explored</span>`
  - `<span class="legend-chip legend-explored"><i class="swatch hidden"></i>hidden (not shown)</span>`
- **New CSS:** `.swatch.explored { background: var(--explored-floor);
  border: 1px solid var(--explored-wall); }` and
  `.swatch.hidden { background: #171b26; border: 1px solid #2a3040; }`.
- The existing floor/wall/doorway/entity chips and the existing
  "in sight → named token …" awareness note stay for everyone; the note
  already mentions "hidden", so no copy collision (the new chips concern
  *map cells*, the note concerns *contacts*).
- **GM legend unchanged** — the three chips are display:none for the GM.

---

## 8. Accessibility & documentation notes

- The tiers are **visual-only render states**: the awareness **sidebar**
  list (the DOM, `aria`-safe structure) is unchanged — it still lists FULL /
  APPROXIMATE contacts exactly as today; a cell's tier changes no DOM node,
  no `aria` attribute, no announced text. (No new `aria-live` churn:
  re-renders are canvas repaints.)
- The dark-hidden design is a deliberate game feel, not an a11y defect:
  players can always `request_state` / read the DOM sidebar for their
  entities, and movement errors ("no route — wall in the way") still surface
  via the accessible toast path.
- README (Iteration 6 docs pass): one paragraph — players see an explored
  fog of war (in sight / greyed memory / dark unknown); the GM sees the full
  map; awareness (entity sensing) is independent and unchanged.

---

## 9. Performance & complexity (budget)

**Cost of `visible_cells(grid, pos)`:** one pass over all `w·h` cells.
Each walkable cell costs one `has_line_of_sight` (Bresenham: ≤ `L + 1`
steps, `L = max(|dx|, |dy|) ≤ max(w, h)`; each step a few dict/list reads +
a couple of wall checks for the diagonal elbows). Each wall cell costs up
to 4 LOS calls (breaks on the first S neighbour — in practice 1 for a
visible wall face, up to 4 only for hidden walls in the all-neighbours-H
case).
So: `O(w·h · L)` with a small constant; no allocations beyond the returned
set (Bresenham yields cell tuples — Python's unavoidable tuple churn, same
as today's per-entity awareness LOS calls).

**Cost of `build_visibility_mask`:** `visible_cells` (if not cached for the
call) + one O(w·h) row-major classification. The session calls
`visible_cells` once per player snapshot (its result feeds BOTH the fold
and the mask — the §3.5 code computes it once; `build_visibility_mask`
takes the mask from the same seen set via an internal fast path, or
recomputes — implementation may cache, spec requires only one computation
per snapshot).

**Recompute frequency:** every `_broadcast` builds a `state_for` for every
connected viewer (per-viewer snapshots — the existing design), and every
`request_state` / `welcome`. A mutation by anyone (GM or player) therefore
recomputes one mask per connected player. There is no timer, no polling,
no per-frame recompute — sight only changes when game state changes.

**Budget at the caps (60×60 grid, 6 players + GM connected, one mutation):**

- Per player: ≤ 3,600 cells × ≤ 60 Bresenham steps ≈ **216k steps**,
  plus the wall-neighbour factor (≤ 4×, usually far less) → comfortably
  **< 150 ms of pure-Python cell work** (Bresenham steps are ~1–3 ns of C
  machinery + ~100–300 ns of Python-level work; the reference implementation
  should land around 30–80 ms per player on modest hardware).
- Per mutation, all 6 players: **< 250 ms** worst case — budgeted at
  **≤ 250 ms, tested at ≤ 500 ms** with margin (AC11; the suite's per-test
  timeout is 30 s, so even a 2× regression cannot break the suite — the
  AC11 bound is the real guard).
- Wire: ≤ ~3.7 KB per player snapshot added (one-time per mutation;
  `path`+`state` are already sent together today).
- **Why this is acceptable:** this is a local LAN tabletop tool at 6-player
  scale (the stated product: 1 GM + ≤6 players, small maps, human-speed
  mutations — a few broadcasts per minute at most, even a fast GM
  painting clicks at ~5/s ≈ 1.25 s of CPU per second across the whole
  60×60×6-player worst case, on a machine running one browser session per
  player). If profiling ever shows a problem, the documented escape hatches
  (NOT to be built now) are, in order: (1) cache `(grid-identity, pos,
  grid-version-counter)` → seen-set (a GM paint bumps the version);
  (2) restrict the S-candidate walkable cells to the bounding box of
  Chebyshev radius `max(w,h)` around `pos` (already the whole map at 60×60
  but tighter on elongated maps); (3) a DDA raycaster. The spec pins the
  *semantics* (full per-cell LOS); a future optimization must pass the same
  ACs.

**Complexity summary:** `visible_cells` — O(w·h·L) time, O(w·h) space,
pure, deterministic; mask construction — O(w·h) extra; per-player explored
set — O(seen cells) space, amortized O(1) per newly seen cell; session
memory — O(6 · w · h) tuples ≈ ≤ 21,600 tuples worst case (~1 MB,
negligible, pruned on leave, cleared on swap).

---

## 10. Explicit non-changes

- **Awareness (HARD CONSTRAINT):** `build_awareness` (three-tier FULL /
  APPROXIMATE / INVISIBLE), awareness items and their shapes,
  `Player.awareness_radius`, the `set_awareness` message, the awareness
  rings, the awareness sidebar — **unchanged in behavior AND in output**:
  for any fixed scenario, the `awareness` list in a post-feature payload is
  byte-identical to the pre-feature build's (AC6). No awareness test is
  modified; the existing suite runs untouched.
- **`app/awareness.py`, `app/pathfinding.py`, `app/models.py`,
  `app/grid.py`: not modified.** (`app/visibility.py` only *imports*
  `has_line_of_sight` from pathfinding.)
- **Movement / pathfinding / permissions:** A*, the corner-cut rule,
  `move`/`place`/override semantics, "no route — wall in the way", player
  self-move-only — unchanged. Players may walk into hidden regions
  (§6.5).
- **Upload / generate / paint / REST:** unchanged; `GET /api/maps` responses
  (which pin their key sets) are unaffected — `visibility` lives only in
  WS `welcome`/`state` player payloads.
- **WS message set:** no new message types, in either direction; the only
  payload delta is the additive player-side `visibility` field.
- **`fog` field:** unchanged, still a wire-compat no-op (§4.2).
- **GM:** payload, rendered map, awareness, tools, legend — unchanged (§5).
- **`players[]` / `Player.to_dict()` shape:** unchanged (explored state is
  session-level, §3.3).
- **Sample dungeon, thumbnails, generated-map invariants:** untouched.
- **Preview canvas** (`#preview-canvas`): untouched (§6.3).

---

## 11. Edge cases

| # | Case | Behavior |
|---|---|---|
| E1 | **First player to join (fresh session).** Explored set starts empty; the `welcome` mask has S around the spawn, no E, rest H. (AC2.) |
| E2 | **Player whose token was deleted.** No anchor → **no S anywhere; explored set frozen; every previously seen cell renders E (greyed), everything else H.** Nothing new is ever revealed; awareness is already empty for this player (existing anchor-missing rule — unchanged). (AC8.) |
| E3 | **Token deleted, then a new one spawned for the same player** (the GM can `place`-repurpose entities; re-join never deletes the Player). If the GM creates a new player token owned by the same Player… note: `join` re-attaching does NOT respawn tokens; a new token for a leaving-then-rejoining player is a new Player. In-session: if a token somehow reappears for a token-less player (GM cannot create player-kind tokens — `create_entity` allows only npc/enemy — so this is reachable only via a future feature), the fold simply resumes from the frozen set. Behavior is defined either way: no crash, no data loss. |
| E4 | **Disconnect / reconnect.** The explored set survives (session-level; `detach` keeps the Player). Reconnect `welcome` carries the full memory mask. (AC9.) |
| E5 | **Map swap while players are connected.** Explored sets cleared BEFORE the swap broadcast → new mask = S at new positions, rest H, zero E. Re-parked tokens see from their new cells. (AC7.) |
| E6 | **Token on a wall cell** (GM `place` or `override` move). Token cell is S (predicate waived, S-B) — even if all 8 neighbours are walls, the mask has exactly one S. The player sees a single lit square; explored folds in only that cell. No crash (Bresenham from a wall cell works — the endpoints never block). |
| E7 | **GM paints a wall over the player's current sight** (paint is a mutation → broadcast). The wall becomes E (it was S) and everything behind it that is no longer S becomes E (never H) — memory is monotonic. The painted wall cell itself renders as greyed wall art. |
| E8 | **GM paints a wall over an H region.** No visible change for the player (still H) — but the cell is recorded as E the next time sight crosses it… precisely: painting never folds cells into explored (only `visible_cells` does); a painted-over cell is E iff it was previously seen. |
| E9 | **GM paints a floor through a wall (opening sight).** Newly visible cells become S immediately (recompute is live) and fold into explored — the map lights up again without a player move. |
| E10 | **Two players, same cell / adjacent tokens.** Independent masks (per-viewer sets); no cross-contamination (the fold is per-viewer inside their own `state_for`). |
| E11 | **Player moves while another's snapshot is in flight.** All state reads/writes are under the session `RLock`; each snapshot is a consistent point-in-time (existing invariant, unchanged). |
| E12 | **60×60 map, 6 players, rapid GM painting.** Within the §9 budget (AC11); no per-frame work. |
| E13 | **`request_state` from a player.** Carries the current mask (routes through `state_for`). |
| E14 | **GM joins a session that already has players with explored memory.** The GM gets no mask (never has); players' memory is unaffected by GM joins. |
| E15 | **A doorway in a hidden region adjacent to a seen wall face.** The doorway is walkable → S only via (S1): a door beyond a corner stays hidden even if its adjacent wall face is visible, unless the door cell itself has clear LOS. Consistent with entity sight (you see a creature *through* a door you can see). |
| E16 | **Degenerate map (fully walled, the (1,1) fallback spawn of `_find_free_floor`).** Token at (1,1) on a wall: mask = 1 S cell (E6), explored grows only when the GM opens floor. No crash. |

---

## 12. Acceptance criteria (for QA)

Testable with the existing harness: in-process
`GameSession` + `FakeConn`/`drive` (`tests/test_session.py` idioms), pure
`app.visibility` unit tests (new `tests/test_visibility.py`), raw-socket WS
(`tests/wsclient.py` + `make_server`), Node harness for the frontend
(`tests/js/harness.js`), and `scripts/e2e_proof.py` over the live server.
All criteria are deterministic.

**Shared helper** (in `tests/test_visibility.py`, importable):

```python
def mask_rows(mask) -> str:  # "SSE…\nHHE…" pretty-print for failure output
def cell(mask, x, y) -> str:  # mask[y][x]
```

- **AC1 — Payload shape (players vs GM).** Fresh session, GM + 2 players on
  the sample dungeon: every player `welcome`/`state` payload
  (`state_for`, `welcome_for`, and the actual broadcast frames) has
  `visibility` = a list of 12 strings each 16 chars, every char in
  `"SEH"`, and `len(vis) == map.height` / `len(row) == map.width` per row.
  The GM payload has **no `"visibility"` key at all** (`assertNotIn`).
  `request_state` from a player also carries it.
- **AC2 — Initial mask is exact (the worked W4 map).** A single player at
  the sample spawn (1,1) (fresh session: GM joins first so the joiner is a
  player): `visibility` equals the W4 mask of §3.2 **string-for-string** —
  69 `"S"` cells, **zero** `"E"` (no memory yet), 123 `"H"`; spot-assert
  the critical cells: `S` at (1,1), (4,10), (5,5), (6,6), (5,4), (5,6),
  (0,1), (4,0), (0,10); `H` at (6,5) (W3's trap — the same-row cell past
  the door), (6,7) (line blocked by wall (5,6)), (7,7), (7,5), (9,5),
  (9,7) (row-7 doorway), (10,4) (col-10 doorway), (12,5), (12,9), (14,8),
  (6,0), (0,0). Then re-derive the S-set independently —
  `{c : c walkable and has_line_of_sight(grid,(1,1),c)} ∪ {w wall : some
  4-orthogonal neighbour of w is in that set}` — using the real
  `has_line_of_sight` and assert it equals the mask's S-set (the
  re-derivation is the oracle; the literal is a fixture, §3.2 W4).
- **AC3 — Tiers flip correctly on a move (and E is monotonic).**
  Continuing AC2's session: the GM moves the player (1,1) → (7,2) (one
  A* move through the (5,5) doorway; the test asserts the `path` frame's
  steps are legal `is_valid_step`s). In the next player `state`: (1) the
  S-set equals the re-derived `visible_cells(grid, (7,2))` (oracle, as in
  AC2); (2) hand-verified spot cells: `S` at (7,2) (token cell), (6,6)
  (clear line via (6,4)/(6,5)), (5,5) (line via (6,3)/(6,4)), (6,1),
  (6,5) (row-5 corridor), (9,7) (the row-7 door, line via (8,3)/(8,4)/
  (8,5)), (10,8) and (12,5) (lines through the (10,4) door); `E` at (1,1)
  (every line back crosses a col-5 wall — e.g. (7,2)→(1,1) samples wall
  (5,1)), (2,5), (4,9), (0,5) (left border no longer facing seen floor),
  (3,11); `H` at (13,1), (14,9), (12,9), (13,6) (lines cross the col-10
  wall (10,1)/(10,5)). Then assert **monotonicity**: over a scripted
  sequence of 5 further GM moves (into the right room, through the (9,7)
  door, down into the bottom band), track every mask and assert no cell
  ever transitions S/E → H (S → E allowed, E → S allowed; nothing ever
  becomes H).
- **AC4 — Explored set grows monotonically within a map** (session-level):
  after the AC3 sequence, `session._explored[pid]` ⊇ (union of every mask's
  S-cells ever seen), and equals exactly that union (no phantom cells, no
  missing cells).
- **AC5 — The corner-cut case (worked W2, both variants).** Use the W2
  mini grid of §3.2 (4×3, no borders — out-of-bounds acts as "no cell",
  which is fine: `has_line_of_sight` only checks in-bounds blockers and
  the grid edges are all wall or floor per the grid): token at (1,0).
  (a) Base grid (`S S S H / S S H H / S H H H`): assert `visible_cells`
  equals that mask exactly — in particular the diagonal wall (2,1) is NOT
  S (both elbows (2,0)/(1,1) walls → corner cut). (b) Variant grid
  ((1,1) repainted floor → `SSSH / SSSH / SSHH`): assert the mask exactly —
  (2,1) is now S (grazing one wall corner passes; the wall also faces the
  seen (1,1)); (2,2) stays H. (c) **Oracle equivalence (the real check):**
  for every wall cell w in the grid, assert
  `w ∈ visible_cells(g, p)` ⟺ ∃ 4-orthogonal neighbour n of w with
  `cells[n] in ("floor","doorway") and has_line_of_sight(g, p, n)` — the
  test re-derives (a)+(b) from the spec's rule + the real
  `has_line_of_sight`, so a wall-reveal bug (e.g. accidental 8-adjacency or
  a broken corner rule) fails here even if a fixture were mistyped.
- **AC6 — AWARENESS UNCHANGED (regression, the hard constraint).** (a) The
  entire existing awareness suite (`tests/test_awareness.py`,
  `tests/test_session.py::TestPlayerVisibilityTiers`,
  `tests/test_ws.py` awareness tests) passes **unmodified**. (b) New
  focused test: for 3 fixed scenarios (player with LOS to a party + hostile
  pair; a no-LOS pair at Chebyshev 3 with radius default and radius 7; the
  sample-dungeon spawn layout), assert the `awareness` list of the new
  `state_for` payload is **exactly equal** (deep `assertEqual`, including
  ordering and surrogate ids) to the expected literal lists — i.e.
  byte-identical to what `build_awareness` produced pre-feature (the
  literals are copied from the current test expectations). (c) The GM's
  awareness list deep-equals the pre-feature shape (all entities, full +
  labeled).
- **AC7 — Map swap clears memory.** GM + player on sample dungeon; player
  makes several moves (explored non-empty, `session._explored[pid]`
  verified non-empty); GM `use_map` to a second registered map (upload a
  small known map first, then `use_map`): the next player `state` has the
  new `map` dims, `visibility` contains **no `"E"` anywhere** (fresh
  memory), S around the re-parked spawn, and `session._explored[pid]`
  contains only cells of the NEW map (no stale coordinates, e.g. none with
  x ≥ new width).
- **AC8 — Token deleted (D2 frozen memory).** The player's own token is
  protected from `delete_entity` (server rule — "cannot delete a player's
  own entity"), so the test constructs the anchor-missing state the way
  the existing awareness suite does: the player explores first (several
  GM moves; `session._explored[pid]` verified non-empty), then the test
  removes the token directly with `del session.entities[token_id]` (and
  sets `player.entity_id = None` to mirror a full deletion). Then:
  `state_for(player)` returns a `visibility` mask with **no `"S"`
  anywhere**, `"E"` exactly at every previously explored cell, rest
  `"H"` (frozen memory — §3.3). A further mutation (the GM paints a far
  cell) broadcasts a mask with still no `"S"` and the SAME E set —
  nothing new is revealed. (Awareness in the same payload is `[]`, as
  today — cross-check with AC6 that the anchor-missing awareness rule is
  untouched.)
- **AC9 — Reconnect keeps memory.** Player explores (moves ≥ 2);
  `session.detach(conn)`; a new `FakeConn` re-joins with the same
  name+role; its `welcome` mask has `"E"` at every previously explored
  (now not-in-sight) cell and S around the current token — full memory.
- **AC10 — GM payload deep-equals pre-feature.** For a fixed scenario
  (GM + 2 players + 1 npc, sample dungeon), the GM `state_for` payload
  deep-equals the hand-written expected dict built from the pre-feature
  shapes — in particular **no `"visibility"` key**, `you_entity is None`,
  full `entities`, all-awareness labeled. (A deep-equal against a
  literal keeps the "GM unchanged" promise machine-checked, not eyeballed.)
- **AC11 — Performance budget.** On a 60×60 grid (built by
  `generate_grid(60, 60, "perf", seed=1)` from `app.generation`) with 6
  players + a GM attached (fake conns, no sends needed — measure
  `state_for` directly): one full recompute (loop over all 6 players,
  `state_for` each) completes in **< 250 ms** on the reference CI machine
  class; assert with `time.perf_counter` at a **500 ms** bound (generous
  margin against CI jitter; the suite's 30 s per-test timeout means even a
  10× regression fails loudly here, not in production). Also assert one
  single-player 60×60 mask build is < 120 ms (budget: < 150 ms per
  §9; assert at 120–150 ms margin as the engineer's call between 120 and
  200, pinned in the test once measured).
- **AC12 — e2e doorway walk (live server, `scripts/e2e_proof.py` new step
  9, mirroring step 8's structure):** GM + 1 player join a fresh session
  on the sample dungeon. (a) The player's `welcome` mask equals the AC2
  W4 mask (69 S / 0 E / 123 H, asserted from the script by re-deriving
  `visible_cells` from the welcome's `map.cells` and the `you_entity`
  position — no hard-coded literals on the wire path); the GM's welcome
  has no `visibility` key. (b) The GM moves the player (1,1) → (5,5) →
  (9,7) → (12,5) (all legal A* routes through the three doorways; the
  script asserts each `path` frame's steps are legal
  `is_valid_step`s). After each move the player's `state` mask is checked
  the same way (S-set == re-derived `visible_cells(grid, token_pos)`;
  E-set == explored-so-far − S, where explored-so-far is the union the
  script tracked from every prior player state; H = rest). Final
  assertions (the marquee behavior, "old room greyed, not dark"): in the
  final mask the left region (e.g. (1,1), (4,10)) is `"E"` (seen at spawn,
  no longer in LOS); the middle-room cells seen en route (e.g. (6,5),
  (7,5)) are `"E"`; the right-room cells around (12,5) (e.g. (12,5) itself,
  (14,5), (13,6)) are `"S"`; no cell ever observed as S/E is `"H"` in the
  final mask (monotonicity across the whole walk).
- **AC13 — Frontend (harness + static).**
  (a) `index.html` contains the three player legend chips
  (`legend-explored` with `in sight`, `explored`, `hidden` text) and the
  CSS defines `.swatch.explored` / `.swatch.hidden` (static checks,
  `test_frontend.py` idiom); the pre-existing legend chips are all still
  present (regression guard).
  (b) Harness (add `drawGridOnCanvas`, `layoutCanvas` to the harness
  `EXPORTS`; the stub canvas context records `fillRect`/`fillStyle` —
  extend `makeCtx` to record `fillRect` calls with the current fillStyle,
  and `strokeStyle` per `stroke()` if needed): feed a player `welcome`
  (via the existing `onWelcome` path) with a hand-built `visibility` matrix
  on a small 5×4 grid; after `renderAll`, assert: no `fillRect` with the
  full floor color `#efe9dc` covers an H cell region (recorded fills for
  H cells are absent); fills at E cells use `#6b7280`; fills at S cells use
  `#efe9dc`; a GM `welcome` (no `visibility`) produces the un-tiered
  behavior (full floor fill across the whole grid — the pre-feature path).
  (c) **Preview untouched:** drive `showUploadPreview()` (or call
  `drawGridOnCanvas(previewCanvas, ctx)` directly with no third arg) on the
  harness and assert the recorded fills are full-detail (no
  `#6b7280` anywhere) — the preview pass never sees a matrix.
  (d) Boot-with-stubbed-DOM regression: the app still boots (existing test
  stays green).
- **AC14 — Full regression.** `python -m pytest` **and**
  `python -m unittest discover -s tests -t .` fully green with **no
  modifications to existing tests** (new test files/classes only — new
  expectations may be *added* to existing files, never rewritten);
  `scripts/e2e_proof.py` all-✓ including the new step 9; `GET /health` ok;
  the sample map byte-identical (do not touch `app/grid.py`).

---

## 13. Wire protocol recap (for the engineer)

- **No new WS message types.** No new client→server messages.
- **One additive server→client field:** `"visibility": ["HHHH…", …]` —
  player `welcome`/`state` payloads only; **absent for the GM**; row `y`,
  char `x`, alphabet `S`/`E`/`H`, `height` rows of `width` chars.
- `map`, `players`, `entities`, `you_entity`, `awareness`, `fog`, `path`,
  `error` — all byte-identical in shape to today.
- The explored set is **never** on the wire (server state only); the matrix
  is its per-viewer rendering.
- Client contract: store `state.visibility` on `applyState`; pass it to
  `drawGridOnCanvas` **only** for the map-canvas pass **only** when
  `state.role === "player"`; treat a missing/malformed matrix as
  full-detail (defensive).

---

## 14. Placement of the code (engineer checklist)

1. **`app/visibility.py` (new):** `visible_cells`, `build_visibility_mask`
   (+ private `_four_neighbours`); imports: `app.models.Grid`,
   `app.pathfinding.has_line_of_sight`. No other imports.
2. **`app/session.py`:** import the two functions; `self._explored` in
   `__init__`; the `state_for` extension (§3.5 — the only hot-path change);
   `self._explored.clear()` in `_on_use_map` (before the broadcast);
   `self._explored.pop(player_id, None)` in `leave()`. Nothing else in the
   file changes.
3. **`app/static/app.js`:** `state.visibility`; `applyState` line (§6.4);
   `T` tokens; `drawGridOnCanvas(canvas, ctx, visibility = null)` tier
   logic (§6.2); the `layoutCanvas` call-site arg (§6.3).
4. **`app/static/index.html`:** three legend chips (§7).
5. **`app/static/style.css`:** `--explored-floor` / `--explored-wall`
   tokens, `.swatch.explored`, `.swatch.hidden`,
   `body.is-gm .legend-explored { display: none; }`.
6. **Tests:** new `tests/test_visibility.py`; additions to
   `tests/test_session.py`, `tests/test_ws.py`, `tests/test_frontend.py`,
   `tests/js/harness.js` (EXPORTS + fill recording); new `e2e_proof.py`
   step 9.
7. **Do not touch:** `app/awareness.py`, `app/pathfinding.py`,
   `app/models.py`, `app/grid.py`, `app/main.py`, `app/server.py`, the
   GM-facing surfaces, existing tests' expectations.
