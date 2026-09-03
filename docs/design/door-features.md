# Design — Openable / Closable Doors

**Status:** build-ready spec. New feature: every `doorway` cell becomes a **door**
with a **state machine** (locked / closed-unlocked / open). By default **every
door is CLOSED AND LOCKED** (so, at map creation — upload, generate, sample, or
painted — a doorway reads as a closed, locked door). Only the **GM** can
**unlock** (and re-**lock**) a door. Any client may **open / close** a door
**only while it is unlocked**. A **closed** door (locked *or* unlocked)
**blocks line of sight and movement exactly like a wall** (including the
diagonal no-corner-cut rule); an **open** door is sight- and movement-
**transparent** (behaves exactly like today's `doorway`). The **entity
awareness** three-tier model and the **explored-map** S/E/H mechanics are
**unchanged** — the new door behavior *falls out of* the existing line-of-sight
code, so this spec pins that claim rather than inventing new awareness /
visibility logic. Doors render in a **color distinct from floor and wall**, and
the three states are visually distinguishable (full + explored tiers).

**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins. Where the *user requirement* (quoted in §1.1) and an existing
frozen test assumption diverge, the **user requirement wins** and the affected
tests are enumerated and updated in §12 — this is the single deliberate,
documented deviation in the spec (A1, §13).

**Code referenced (read, not modified by the spec):** `app/models.py`
(`Grid`, `Entity`, `Player`, `to_dict`/`from_dict`), `app/grid.py` (sample
dungeon, `set_cell`), `app/pathfinding.py` (`walkable`, `is_valid_step`,
`has_line_of_sight`, Bresenham, no-corner-cut), `app/visibility.py`
(`visible_cells` S1/S2, `build_visibility_mask` S/E/H), `app/awareness.py`
(`build_awareness` three-tier), `app/session.py` (`GameSession`: `state_for`,
`_visibility_for`, `_on_paint`, `_on_move`, `_on_use_map`, `_gm_only`,
error-string house style, `_explored`), `app/detection.py` (`classify_doors`,
thumbnail), `app/generation.py` (BSP doors), `app/server.py` (REST map routes),
`app/static/app.js` (`T` tokens, `drawGridOnCanvas`, doorway art, tool bar,
click/paint handling, `state`), `app/static/index.html` (tool bar, legend),
`app/static/style.css` (tokens, legend swatches), `tests/` (harness +
unittest idioms), `scripts/e2e_proof.py`, `scripts/qa_explored_map.py`.

---

## 1. What changes (summary)

### 1.1 The user requirement (verbatim, source of truth)

> "build a new feature request for the doorways to be openable and closable -
> this would be like a wall that can be opened, by default all the doors should
> be closed and locked, the gm is the only player who can unlock a door. doors
> can be opened and closed, and will block line of sight, awareness will still
> work, but anything behind a closed door to an area should not be seen if it
> has not been explored and if it has, it should be greyed out like an area out
> of line of sight. The doors should be a different colour to the floors and
> walls."

### 1.2 Change table

| # | Change | Where |
|---|---|---|
| DOOR-1 | New **door state model**: an additive optional `Grid.doors` field — a dict keyed `"<x>,<y>"` (a `doorway` cell) → one of `"L"` (closed+locked), `"U"` (closed, unlocked), `"O"` (open). **Absent/`None` ⇒ every door is `L`** (all doors closed+locked — the default). A door key may only exist on a `doorway` cell. | `app/models.py` (`Grid`) |
| DOOR-2 | **State machine + permissions** (§4). `locked →(GM unlock)→ unlocked →(open)→ open →(close)→ unlocked`; GM `lock` from `unlocked` or `open` (lock-while-open **force-closes**). Players may only `open`/`close` an **unlocked** door. | `app/session.py` (`_on_door`) |
| DOOR-3 | **Movement**: a closed door is **not walkable** (blocks A* like a wall, incl. no-corner-cut); an open door is walkable (like today's `doorway`). GM `override:true` **bypasses closed doors** (the teleport exception). | `app/pathfinding.py`, `app/session.py` |
| DOOR-4 | **Line of sight / awareness**: a closed door **blocks** `has_line_of_sight` like a wall (incl. diagonal corner-cut); an open door is transparent. Entity awareness is therefore **unchanged in code** — it inherits the new blocking for free. | `app/pathfinding.py` |
| DOOR-5 | **Explored map (S/E/H)**: a closed door's far side is **H** if never explored, **E** (greyed) if explored — exactly like an out-of-line-of-sight area. A closed door's own cell is revealed by the **wall-face rule** (D5) so it renders in the current tier. Follows from LOS blocking; pinned in §6.2. | `app/visibility.py`, `app/session.py` |
| DOOR-6 | **Rendering**: a distinct door palette (full + explored tiers), three visually distinct states, grid-line/hatch treatment, legend chips. | `app/static/app.js`, `index.html`, `style.css` |
| DOOR-7 | **Wire protocol**: new client→server `{type:"door", x, y, action}` (action ∈ unlock/lock/open/close); **no new server→client broadcast type** — door state rides inside the existing `map` payload (`map.doors`). | `app/session.py`, `app/static/app.js` |
| DOOR-8 | **REST**: additive `doors` field in every `map` object (`GET /api/maps/{id}`, upload, generate, and the session `welcome`/`state`). **No new REST route** — the REST surface stays frozen. | `app/server.py`, `app/models.py` |
| DOOR-9 | **Paint interaction** (D4): painting a `doorway` creates it **closed+locked**; painting `floor`/`wall` over a door **deletes** its state; upload/generate/sample start all-locked. | `app/models.py` (`Grid` sync), `app/session.py`, `app/server.py` |
| DOOR-10 | **Frontend UX**: a GM **Door tool** with state sub-buttons; a player **taps a doorway cell** to open/close an unlocked door; door hints. | `app/static/app.js`, `index.html` |
| DOOR-11 | **Tests + live proof**: door state machine / LOS / A* / visibility / session / wire / frontend tests, an `e2e_proof.py` step, a new `scripts/qa_doors.py`. | `tests/*`, `scripts/*` |

**What does NOT change:** the three-tier entity awareness model (FULL /
APPROXIMATE / INVISIBLE) and `build_awareness`'s *logic* (it reuses
`has_line_of_sight`, which now sees closed doors — §6.1); the explored-map
S/E/H *algorithm* (it reuses `visible_cells`/`has_line_of_sight` — §6.2); the
`players[]` / `Player.to_dict()` shapes; the cell type vocabulary (`floor` /
`wall` / `doorway` — a door is a `doorway` cell + a state, **not** a new cell
type); the `fog` flag (still a wire-compat no-op); the GM's exemption (GM view
is never filtered and is never door-gated); the sample dungeon geometry
(`app/grid.py` untouched — its three doorways simply *are* doors, closed+locked
by default); the `path` / `error` frame shapes; the awareness ring / sidebar.

---

## 2. Behavior statement

Given the user requirement above, the behavior is:

1. **Every `doorway` cell is a door with a state.** The three states are
   **`locked`** (closed + locked — the default), **`unlocked`** (closed,
   unlocked), and **`open`**. The state is **map state** (shared by all
   viewers, GM-editable, part of the `Grid`, served by REST and WS) — *not*
   per-viewer state (D1, §3).
2. **Default is closed + locked.** Every door created by upload, generation,
   the sample map, or GM paint is `locked` (CR1). A `doorway` cell whose state
   is absent from `Grid.doors` is `locked` (D1). This is the deliberate
   behavior change from "every doorway is an open gap."
3. **Only the GM unlocks (and re-locks)** (CR2). Players can never `lock` or
   `unlock`; they can only `open`/`close` a door **that is already unlocked**.
4. **A closed door is a wall** (for the purposes of this feature): it blocks
   **line of sight** (CR4) and **movement** (CR7), including the diagonal
   no-corner-cut rule, exactly the way a `wall` cell does today.
5. **An open door is a doorway**: sight-transparent and walkable — byte-for-
   byte today's `doorway` behavior.
6. **Awareness still works** (CR5) and is **unchanged in code**: because a
   closed door blocks LOS, an entity behind a closed door shows as
   **APPROXIMATE** (grey "?", identity-free) if within the player's awareness
   radius, else **INVISIBLE**; an open door gives clear LOS → **FULL** when
   unobstructed. Implementers must not "fix" awareness to special-case doors
   (§6.1).
7. **Explored map still works** (CR6) and is **unchanged in code**: the area
   beyond a **closed** door is **H** (undrawn) if never explored and **E**
   (greyed) if previously explored — *exactly* like an area out of line of
   sight. Opening the door makes newly-visible cells **S** and folds them into
   memory; closing it again falls back to **E**/**H**. This is a consequence
   of LOS blocking, verified against `app/visibility.py` and pinned in §6.2.
8. **Doors are a different color** (CR8) from floor (`#efe9dc`) and wall
   (`#3b4252`), and the three states are visually distinguishable (§7).

---

## 3. Data model — door state on `Grid` (design decision D1)

### 3.1 Storage: an additive optional field on `Grid`, NOT a session-side dict

**Storage: a new optional field on the `Grid` dataclass.**

```python
# app/models.py
DOOR_STATES = ("L", "U", "O")   # "L" closed+locked, "U" closed+unlocked, "O" open

@dataclass
class Grid:
    name: str = "Untitled map"
    width: int = 0
    height: int = 0
    cells: list[list[str]] = field(default_factory=list)  # cells[y][x]
    image: str | None = None
    doors: dict[str, str] | None = None   # NEW (D1): "<x>,<y>" -> "L"|"U"|"O"
```

**Why a `Grid` field and not a session dict like `_explored` (D1 rationale):**
door state is **map state**, not per-viewer state:

- It is **shared** by every viewer (one door has one state for the whole
  session), **GM-editable**, **lives in `maps_registry`** (the session grid is
  the *same object* as the registry entry, so REST and WS see the same doors),
  and is **served by REST** (`GET /api/maps/{id}`). The `_explored` pattern is
  per-*viewer* memory; door state is a property of the *map*. Putting it on
  `Grid` is therefore the structurally correct home, and it means `use_map`
  swaps, REST reads, and WS broadcasts all carry it with zero extra wiring.
- A session dict keyed by map would (a) desync from `maps_registry` on
  `use_map`/REST paint, (b) be invisible to `GET /api/maps/{id}`, and (c) need
  manual re-seeding — all wrong.

**Encoding — a dict keyed `"<x>,<y>"` → state char, NOT a 2D rows-of-chars
matrix:**

- A door is an *exception* on top of the `cells` grid; most cells are **not**
  doors, so a sparse map of *only* door cells is compact, human-readable in a
  WS inspector, and trivially diffed in tests (`doors.get("5,5") == "O"`).
- A full 2D `list[list[str]]` would be 3,600 entries at 60×60, mostly empty,
  and would duplicate the `cells` orientation bookkeeping for no benefit.
- The key form `"<x>,<y>"` (comma, no padding) matches the existing client-side
  key idiom (`paintCell` uses `` `${x},${y},${state.tool}` ``) and is
  unambiguous.
- The state char is a **single letter** for wire compactness:
  **`"L"`** = closed+locked (the default), **`"U"`** = closed+unlocked,
  **`"O"`** = open. A door is **closed iff its state is not `"O"`**
  (`"L"` and `"U"` are both closed); a door is **locked iff its state is
  `"L"`**.

### 3.2 `to_dict` / `from_dict` (round-trip)

```python
def to_dict(self) -> dict[str, Any]:
    d = {
        "name": self.name,
        "width": self.width,
        "height": self.height,
        "cells": [list(row) for row in self.cells],
        "image": self.image,
    }
    # Additive: include the doors object. Emitted as an OBJECT (possibly empty)
    # whenever a door exists; omitted entirely only when there are no door
    # entries at all (the all-locked default) — see §8.1 for wire policy.
    if self.doors:
        d["doors"] = dict(self.doors)
    return d

@classmethod
def from_dict(cls, data: dict[str, Any]) -> "Grid":
    return cls(
        name=data.get("name", "Untitled map"),
        width=int(data["width"]),
        height=int(data["height"]),
        cells=[list(row) for row in data["cells"]],
        image=data.get("image"),
        doors=data.get("doors"),            # None -> all doors locked
    )
```

**Round-trip invariants (AC1):** `Grid.from_dict(g.to_dict())` preserves every
door state; a `Grid` with `doors=None` serializes *without* a `doors` key and
re-parses to `doors=None`; a `Grid` with one open door serializes with
`"doors": {"5,5": "O"}` and re-parses identically. The new key is **additive**
— old payloads (no `doors`) still parse (to `doors=None` ⇒ all locked).

### 3.3 `__post_init__` validation (state only on doorway cells)

```python
def __post_init__(self) -> None:
    # ... existing cells validation (rows/width/cell-type) ...
    if self.doors is None:
        self.doors = None          # keep None = "all doors locked"
        return
    clean: dict[str, str] = {}
    for key, st in self.doors.items():
        if not isinstance(key, str) or "," not in key:
            raise ValueError(f"invalid door key {key!r}")
        xs, ys = key.split(",")
        x, y = int(xs), int(ys)
        if st not in DOOR_STATES:
            raise ValueError(f"invalid door state {st!r} at {key!r}")
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"door key {key!r} out of bounds")
        if self.cells[y][x] != "doorway":
            raise ValueError(f"door at {key!r} is not on a doorway cell")
        clean[key] = st
    self.doors = clean
```

- **State only on `doorway` cells** (I1): a door key on a `floor`/`wall` cell
  is a `ValueError` — this is the single source of truth for "a door lives
  only on a doorway."
- **`None` is preserved** as the all-locked default (no need to materialize a
  full dict of `"L"`s — §3.4).
- **Unknown states / malformed keys / out-of-bounds keys** are rejected at
  construction, so an in-memory `Grid` is always well-formed.

### 3.4 Backward compatibility (no `doors` ⇒ all locked)

- **Old payloads without `doors`** (any client built before this feature, a
  stale `maps_registry` entry, or a hand-written `Grid.from_dict`): parse to
  `doors=None` ⇒ **every door is `locked`** (A2). This is *safe by default* —
  it never unlocks a door on legacy data.
- **`doors: {}` (empty object) and `doors: None`** are equivalent (all
  locked). The server *emits* the key only when a non-default state exists
  (§8.1), but always *accepts* it.
- **Existing `Grid(...)` constructors** are unaffected: `doors` is a new
  trailing field defaulting to `None`, so `Grid(name, width, height, cells,
  image)` (positional or keyword) still works unchanged.
- **`Session.to_dict` / `Session.from_dict`** (in `app/models.py`) and
  `Session` itself are **unchanged** — the `map` sub-object carries `doors`.

### 3.5 Derived accessors + paint-sync helper (on `Grid`)

```python
def door_state_at(self, x: int, y: int) -> str | None:
    """The door state char at (x,y), or None if the cell is not a door.
    A `doorway` cell with no entry is `L` (the default)."""
    if self.cells[y][x] != "doorway":
        return None
    if self.doors is None:
        return "L"
    return self.doors.get(f"{x},{y}", "L")

def is_door_closed(self, x: int, y: int) -> bool:
    """True iff (x,y) is a doorway whose state is not open (L or U).
    A closed door is a wall for LOS + movement."""
    st = self.door_state_at(x, y)
    return st is not None and st != "O"

def set_door(self, x: int, y: int, state: str) -> None:
    """Set (x,y)'s door to `state`; materializes self.doors. Raises
    ValueError if (x,y) is not a doorway (caller validates first)."""
    if self.cells[y][x] != "doorway":
        raise ValueError(f"no door at ({x},{y})")
    if state not in DOOR_STATES:
        raise ValueError(f"invalid door state {state!r}")
    self.doors = dict(self.doors or {})
    self.doors[f"{x},{y}"] = state

def sync_doors_after_cell_set(self, x: int, y: int) -> None:
    """D4: keep `doors` consistent after a cell is (re)typed by paint.
    Painted to `doorway`  -> the door exists in the DEFAULT `L` state
    (only added if it is not already recorded, so a repainted door keeps
    its current state — painting is a no-op for door state on an existing
    doorway).
    Painted to `floor`/`wall` -> the door state is DELETED (a cell that is no
    longer a doorway has no door)."""
    if self.doors is None:
        if self.cells[y][x] == "doorway":
            self.doors = {}     # materialize an empty map; door is L by default
        return
    key = f"{x},{y}"
    if self.cells[y][x] != "doorway":
        self.doors.pop(key, None)
    # if still a doorway: leave the entry (or its absence) untouched — a
    # repainted doorway keeps whatever state it already had.
```

`sync_doors_after_cell_set` is the **single paint-sync point** (§9, D4): the
WS `paint` handler and the REST paint route both call it after setting
`grid.cells[y][x]`, so door state can never desync from the cell type.
Because `Grid.__post_init__` only runs at *construction* (in-place cell
mutation does not re-run it), the paint path must call this helper explicitly
— that is the one place door state is kept in sync with `cells` at runtime.

---

## 4. State machine + permissions (design decision D2)

### 4.1 The state machine

Three states. **Locked (L)** is the only state where the door cannot be
opened by a player; **Unlocked (U)** and **Open (O)** are the
player-interactive states.

```
                 GM unlock
            ┌───────────────────────────┐
            ▼                           │
        ┌────────┐     open      ┌────────┐     close     ┌────────┐
        │  LOCKED │ ────────────> │ UNLOCKED │ ───────────> │  OPEN  │
        │   (L)   │  (GM ONLY,  │   (U)   │   (any)      │   (O)   │
        └────────┘   not player)└────────┘               └────────┘
            ▲                                    ▲  │
            │   GM lock (from U or O)           │  │ GM lock
            └────────────────────────────────────┘  │ (force-closes)
                                                    └────────┘  (O --lock--> L)
```

- **`locked → unlocked`**: `unlock`, **GM only**.
- **`unlocked → open`**: `open`, **any client** (GM or player), door is
  unlocked.
- **`open → unlocked`**: `close`, **any client**, door is open (always
  unlocked to be open).
- **`{unlocked, open} → locked`**: `lock`, **GM only**. **Lock-while-open
  FORCE-CLOSES** (a door that is `open` and gets `lock`ed becomes `locked`,
  i.e. closed) — this is a deliberate decision (D2): there is no "open and
  locked" state; a locked door is, by definition, closed. The GM cannot leave
  a door open-and-locked, which avoids an ambiguous "locked but ajar" state.

Every `(state, action)` pair is **totally determined**: it is either exactly
one legal transition or a rejected error (no partial states, §10 invariant
I7). The full legal/illegal table:

| Current | `unlock` | `lock` | `open` | `close` |
|---|---|---|---|---|
| **locked (L)** | GM → unlocked | — (already locked) | "door is locked" | "door is locked" |
| **unlocked (U)** | "door is already unlocked" | GM → locked | any → open | "door is already closed" |
| **open (O)** | "door is already unlocked"¹ | GM → locked (force-closed) | "door is already open" | any → unlocked |

¹ `unlock`/`lock` on an already-unlocked/locked door is a no-op-style error
(never a crash); `open`/`close` are gated by the unlocked/locked condition.

### 4.2 Permission matrix (CR2/CR3)

| Action | GM | Player |
|---|---|---|
| `unlock` | **allowed** (from locked) | **`"not allowed"`** (GM-only) |
| `lock` | **allowed** (from unlocked or open; force-closes open) | **`"not allowed"`** (GM-only) |
| `open` | allowed (door must be unlocked) | allowed **iff door is unlocked** (`"door is locked"` otherwise) |
| `close` | allowed (door must be open) | allowed **iff door is open** (which implies unlocked; `"door is locked"` if it is somehow locked) |

So: **players can only open/close a door while it is UNLOCKED**; GM unlock /
GM lock are the only state changes available to a player through the door
tool. This matches "the GM is the only player who can unlock a door" and
"doors can be opened and closed" (CR2/CR3), with the recommendation that a
player's open/close is conditioned on the unlocked state.

### 4.3 Validation order (deterministic, pinned for tests)

`_on_door` validates in this **exact** order (first failure wins), so the
error returned for any malformed/illegal request is deterministic and
testable (AC3):

1. **GM-only actions first?** No — parse, then bounds, then cell, then
   transition, then role. Concretely:
   1. `x`/`y` must be ints (reject bools) → else `"x and y must be integers"`.
   2. **Bounds** → else `"destination out of bounds"`.
   3. **Cell must be a `doorway`** → else `"not a doorway"`.
   4. `action` must be one of `unlock`/`lock`/`open`/`close` → else
      `"action must be one of unlock/lock/open/close"`.
   5. **Transition legality** (state machine) → else the state-specific error
      (`"door is locked"`, `"door is already unlocked"`, `"door is already
      locked"`, `"door is already open"`, `"door is already closed"`).
   6. **Role** (GM-only for `unlock`/`lock`) → else `"not allowed"`.
   7. **Occupancy** (close only, D3) → else `"cannot close a door with a
      token on it"`.
   8. Apply `set_door`, broadcast, return `None` (the broadcast carries the
      new state).

**Exact WS error strings (house style, lowercase, em-dash where natural —
cf. `"not a player token"`, `"awareness must be an integer 0–20"`,
`"no route — wall in the way"`):**

| Case | `message` |
|---|---|
| `x`/`y` not int | `x and y must be integers` |
| out of bounds | `destination out of bounds` |
| cell not a doorway | `not a doorway` |
| bad/missing action | `action must be one of unlock/lock/open/close` |
| `open`/`close` on a **locked** door (any role) | `door is locked` |
| `unlock` when already unlocked | `door is already unlocked` |
| `lock` when already locked | `door is already locked` |
| `open` when already open | `door is already open` |
| `close` when already closed | `door is already closed` |
| `unlock`/`lock` by a **player** | `not allowed` |
| `close` with an entity on the cell (D3) | `cannot close a door with a token on it` |

The role check (#6) runs *after* the transition check (#5) **for
`open`/`close`** (so a player `open`ing a locked door gets `"door is locked"`,
not `"not allowed"` — the door's state is the more informative failure), but
**before** occupancy (#7). For the GM-only actions `unlock`/`lock`, the
transition check still runs first (so a GM `lock`ing an already-locked door
gets `"door is already locked"`), then the role check (so a player gets
`"not allowed"`). This ordering is AC-pinned.

### 4.4 The WS handler

```python
# app/session.py
DOOR_ACTIONS = ("unlock", "lock", "open", "close")

# in handle_message, alongside the other GM tool dispatches:
if mtype == "door":
    return self._on_door(player, is_gm, msg)

def _on_door(self, player: Player, is_gm: bool, msg: dict[str, Any]) -> dict[str, Any] | None:
    x = _as_int(msg.get("x"))
    y = _as_int(msg.get("y"))
    if x is None or y is None:
        return {"type": "error", "message": "x and y must be integers"}
    with self._lock:
        if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
            return {"type": "error", "message": "destination out of bounds"}
        if self.grid.cells[y][x] != "doorway":
            return {"type": "error", "message": "not a doorway"}
        action = msg.get("action")
        if action not in DOOR_ACTIONS:
            return {"type": "error", "message": "action must be one of unlock/lock/open/close"}
        cur = self.grid.door_state_at(x, y)           # "L" | "U" | "O"
        # transition legality (before role, so state errors are informative)
        if action in ("open", "close") and cur == "L":
            return {"type": "error", "message": "door is locked"}
        if action == "open" and cur == "O":
            return {"type": "error", "message": "door is already open"}
        if action == "close" and cur != "O":
            return {"type": "error", "message": "door is already closed"}
        if action == "unlock" and cur != "L":
            return {"type": "error", "message": "door is already unlocked"}
        if action == "lock" and cur == "L":
            return {"type": "error", "message": "door is already locked"}
        # role
        if action in ("unlock", "lock") and not is_gm:
            return {"type": "error", "message": NOT_ALLOWED}
        # occupancy (close only, D3): an entity must never be left on a
        # non-walkable (closed) cell.
        if action == "close" and any(e.x == x and e.y == y for e in self.entities.values()):
            return {"type": "error", "message": "cannot close a door with a token on it"}
        new_state = {("unlock", "L"): "U", ("open", "U"): "O",
                     ("close", "O"): "U", ("lock", "U"): "L",
                     ("lock", "O"): "L"}[(action, cur)]
        self.grid.set_door(x, y, new_state)
        self._run_b(self._broadcast())
        return None
```

Success has **no per-client reply** — the `state` broadcast carries the new
`map.doors`, consistent with `paint`/`set_team`/`set_awareness`. Errors are the
usual `{"type":"error","message":...}` to the offending client.

---

## 5. Movement — A* and the GM override (CR7)

A **closed door is not walkable** and an **open door is walkable**, so
`app/pathfinding.py`'s walkability predicate must consult door state.

### 5.1 Door-aware predicates (optional `doors` parameter, backward-compatible)

The three cell predicates gain an **optional** `doors` parameter. Passing
`None` means "derive the default" — for the module's own callers the default
is read from the grid, so **existing two/three-argument call sites keep
working** (and, because the default is all-locked, a bare `Grid` with no
`doors` behaves as all-locked, matching D1).

```python
# app/pathfinding.py
def _closed_doors(grid: Grid) -> frozenset[tuple[int, int]]:
    """The set of (x, y) of CLOSED doors (state != 'O'). Pure over grid.

    A doorway cell is CLOSED unless grid.doors records it as 'O' (open). A
    doorway with no entry (or grid.doors is None) is therefore CLOSED (locked)
    — the default. Floors/walls are never in the set (they are not doors).
    """
    doors = grid.doors or {}
    closed = set()
    for y in range(grid.height):
        for x in range(grid.width):
            if grid.cells[y][x] == "doorway" and doors.get(f"{x},{y}") != "O":
                closed.add((x, y))
    return frozenset(closed)

def walkable(grid, x, y, doors=None) -> bool:
    if not _in_bounds(grid, x, y):
        return False
    if grid.cells[y][x] not in WALKABLE_CELLS:     # floor or doorway
        return False
    if (x, y) in (doors if doors is not None else _closed_doors(grid)):
        return False                               # a CLOSED door is not walkable
    return True

# is_valid_step(grid, a, b, doors=None) -> walkable(b) AND both diagonal
#   elbows walkable, with `walkable` door-aware (no-corner-cut preserved).
# has_line_of_sight(grid, a, b, doors=None) -> Bresenham; a CLOSED door cell
#   strictly between a and b blocks (like a wall), AND a diagonal step whose
#   both elbows are CLOSED doors (or walls) is a corner-cut (like walls).
```

**Key semantics (AC4, AC5):**

- A **closed door cell** on a Bresenham sight line (strictly between `a` and
  `b`) blocks, exactly like a `wall` cell — this is what makes "a closed door
  blocks LOS like a wall" (CR4) true *by construction*.
- The **diagonal no-corner-cut rule** is preserved: a diagonal step is illegal
  for movement and a sight line is "cut" when **both** orthogonal elbow cells
  are walls **or closed doors** (open doors and floors are open elbows). This
  means a closed door cannot be "sneaked" around at a corner, matching
  `is_valid_step` today.
- **Endpoints never block** (unchanged): a token may sit on a cell and see
  itself even if that cell is a closed door (degenerate GM-placed token, §11
  E16).
- **Open door == today's doorway**: walkable and sight-transparent, so all
  existing "doorway is walkable / does not block sight" behavior is preserved
  for `doors.get(k) == "O"` (AC5).

### 5.2 A* and `find_path`

`find_path` uses `walkable`/`is_valid_step`, both of which are now door-aware
(via the grid's `doors`). So **A* automatically routes around closed doors and
through open ones** — no separate "door pathfinding" code. A destination
behind a closed door with no open route → `find_path` returns `None` →
`_on_move` returns `"no route — wall in the way"` (the existing message is
reused verbatim — a closed door *is* a wall to movement; AC5).

### 5.3 GM `override:true` bypasses closed doors (D2/CR7 decision)

`override:true` is the GM's **"ignore walls" teleport** (PROJECT.md §6). It
**already ignores all walkability** (`_on_move` sets `entity.x, entity.y =
x, y` directly, no `find_path`). **Decision: the override also bypasses closed
doors** — it is a teleport exception, and a GM should be able to place a token
anywhere, closed door or no. This is *automatic* (the override path never
consults `walkable`) and needs no code change beyond the existing behavior;
AC5 asserts it. A player can never send `override:true` (existing rule), so
players can never teleport through a closed door.

> **Assumption A3:** "GM ignore-walls override bypasses closed doors" is the
> recommended interpretation (it is a teleport, not a walk). It requires no
> new code; it is pinned by an AC so it is not accidentally "fixed" to block.

---

## 6. Awareness + explored map (CR5, CR6) — the "unchanged, but pinned" sections

These two sections are the core of the requirement that **awareness and the
explored map keep working and are NOT re-implemented**. The feature changes
nothing in `app/awareness.py` or in the S/E/H *algorithm*; it changes the
**line-of-sight predicate they consume** (§5.1), so the new door behavior is
inherited. This section **verifies the claim against `app/visibility.py`**
(as required) and pins it so implementers do not "fix" awareness.

### 6.1 Awareness (entity three-tier) — UNCHANGED (CR5)

`build_awareness(viewer, entities, grid)` (app/awareness.py) is **not
modified**. For a **player** anchored at own token `O`, per other entity `E`:

- **FULL** iff `has_line_of_sight(grid, O, E)` — and that call now (via §5.1)
  treats a **closed door as a wall**. So:
  - `E` behind a **closed** door (no LOS) → **not** FULL.
  - `E` behind an **open** door with an otherwise clear line → **FULL** (named
    token, color, label).
- **APPROXIMATE** iff not FULL and `chebyshev(O, E) <= awareness_radius`: a
  coarse 2×2-block grey "?" with no identity. So an entity behind a **closed**
  door within the radius shows as a grey "?" (the door blocks LOS, not the
  sensor).
- **INVISIBLE** iff not FULL and beyond the radius: absent. So an entity far
  behind a **closed** door is invisible.

**This is the explicit "do not fix awareness" pin (CR5):** the requirement says
*an entity behind a closed door shows as APPROXIMATE grey "?" if within the
player's awareness radius, INVISIBLE beyond; an open door gives clear LOS →
FULL when unobstructed*. That is **exactly** what `build_awareness` + the
door-aware `has_line_of_sight` already produce. **Implementers must not add
door special-casing to `app/awareness.py`** — the awareness code is unchanged
in behavior *and* output modulo the (intended) LOS blocking. AC6 asserts this
with concrete scenarios (entity behind closed door within/beyond radius; open
door → FULL; GM unfiltered).

The **GM** is exempt (no LOS/distance filtering) — the GM sees every entity
regardless of door state (I3).

### 6.2 Explored map (S/E/H) — UNCHANGED (CR6), verified against `app/visibility.py`

`visible_cells(grid, pos)` (app/visibility.py) and `build_visibility_mask` are
**not modified in their S/E/H logic**. They iterate cells and call
`has_line_of_sight` — which is now door-aware (§5.1). Verifying the claim the
requirement asks us to pin:

**(a) A closed door blocks sight, so its far side is H (never explored).**
In `visible_cells`, a walkable cell `c` (floor *or* doorway) is S iff
`has_line_of_sight(grid, pos, c)` (rule S1). A floor cell **behind a closed
door**: every Bresenham line from `pos` to it crosses the closed-door cell
(now a blocker, §5.1) → `has_line_of_sight` is False → **not S**. If the
player has never seen it, it is **H** (undrawn). If the player *has* seen it
(before the door closed, or from another vantage), it is in the player's
`explored` set → **E (greyed)**. **This is exactly the requirement:**
"anything behind a closed door to an area should not be seen if it has not
been explored and if it has, it should be greyed out like an area out of line
of sight." ✓ It falls out of LOS blocking with **no new code**.

**(b) A closed door's own cell is revealed by the wall-face rule (D5).**
A closed door is a `doorway` cell, so the *existing* S1 branch would test
`has_line_of_sight(grid, pos, doorCell)` — which is **False** (a closed door
blocks its own line). So a naive reading would make a closed door's cell **H**
even when the player is standing right in front of it — visually wrong (the
player should *see* the closed door and know it is there). **Decision D5:
treat a CLOSED door as a wall for the S2 wall-face rule** — a closed door's
cell is S iff any of its four in-bounds **walkable** (floor/open-doorway)
orthogonal neighbours satisfies S1. This reuses the *existing* S2 mechanism
(verbatim the wall case) so a closed door **reveals its face** and renders in
the current tier (S when facing a seen floor). The implementation adds a small
closed-door branch to the cell loop (a doorway in `closed_doors` uses the S2
face test instead of S1); open doorways and floors are untouched. AC7 pins the
closed-door face-reveal and the behind-the-door H/E behavior.

**(c) Opening the door makes newly-visible cells S and folds them into
memory; closing falls back to E/H.** Because `visible_cells`/`_visibility_for`
recompute **live** on every mutation (a door open/close is a mutation →
`_broadcast` → `state_for` per viewer → `visible_cells` re-run), opening a door
immediately expands the S-set (new cells S) and `_visibility_for` folds them
into `explored` (monotonic). Closing it re-computes the S-set smaller; the
newly-hidden cells that were S/E become **E** (memory) or, if they were only
ever reachable *through* that door and were never S, **H**. No new mechanism —
it is the existing live recompute + monotonic explored set. AC7/AC8 pin it.

**(d) No awareness/visibility code is rewritten.** The only server changes are
§5.1 (door-aware `has_line_of_sight`/`walkable`/`is_valid_step`) and the small
closed-door face branch in `visible_cells` (D5). `app/awareness.py` is
**byte-unchanged**. This is a hard constraint (AC6).

---

## 7. Frontend — rendering the door states (design decision D7, CR8)

### 7.1 Palette (the full door color table)

Doors must be a **color different from floor (`#efe9dc`) and wall (`#3b4252`)**
(CR8) and the three states must be **visually distinguishable**. The existing
`doorway` amber (`#d97706`) is already distinct from both; we **extend** it into
a three-state palette and add the explored (grey) tier (mirroring the explored-
map §6.1 approach: *same art, recolored*).

**Full-detail tier ("S" / GM / preview):**

| State | Fill (cell base) | Border + glyph | Rationale |
|---|---|---|---|
| **open (`O`)** | floor `#efe9dc` | border + arch **amber `#d97706`** | Identical to today's doorway — an open door *is* a doorway (regression-identical art). |
| **closed, unlocked (`U`)** | floor `#efe9dc` | border + arch **amber `#f59f00`** + a **centered horizontal "bar"** | Same amber family as open (reads "it's a door") but a *distinct* lighter amber + a bar glyph so it is not mistaken for open. |
| **closed, locked (`L`)** | floor `#efe9dc` | border + arch **red `#e03131`** + a **padlock glyph** (bar + a small lock notch) | Red (the existing `--danger`/`enemy` red, `#e03131`) is maximally distinct from both the amber doors and the wall/floor — "locked = the thing you can't pass." |

All three door colors (`#d97706`, `#f59f00`, `#e03131`) are **distinct from
floor `#efe9dc` and wall `#3b4252`** (CR8), and the three are mutually
distinguishable by **both hue and glyph** (colorblind-safe: red/amber/amber +
padlock/bar/arch shapes, not color alone).

**Explored tier ("E", greyed memory) — the same door art recolored to the flat
grey family** (matching the explored floor `#6b7280` / wall `#4b5563` ramp so a
greyed door reads as "a door I know about, not in front of me"):

| State | Fill | Border + glyph (desaturated) |
|---|---|---|
| **open (`O`)** | `#6b7280` | `#8b94a3` (the explored-door grey, same as today's explored doorway) + arch |
| **closed, unlocked (`U`)** | `#6b7280` | `#9a8f7a` (desaturated amber) + bar |
| **closed, locked (`L`)** | `#6b7280` | `#a06b6b` (desaturated red) + padlock |

(**ERRATUM / build note:** the three explored-tier border hexes above
— `#8b94a3` / `#9a8f7a` / `#a06b6b` — are the values that were actually
shipped and are the *final* hexes (they match the `T` tokens in
§7.3 and `app/static/app.js`/`style.css` byte-for-byte). They satisfy the
constraint: grey-family, value-distinct from the explored floor `#6b7280`
and from each other, and distinguishable at the 8px minimum cell size. The
"engineer to finalize at build time" phrasing is therefore resolved in
favour of the pinned values above; there is no remaining ambiguity.)

**Grid-line / hatch treatment:** door cells (all three states) are **floor-
based** (fill = floor color, not wall), so they get the **grid line** of their
tier (full `#d9d1bd` at S/GM, `rgba(217,209,189,0.3)` dimmed at E) and **no
wall hatch** (a door is not a wall — it is a distinct object sitting in a wall
gap). The door's **border + glyph** is drawn over the floor base, exactly like
today's doorway border + arch (which is already drawn over the floor base in
`drawGridOnCanvas`'s doorway pass). So the only per-state change to the
*existing* doorway draw code is: pick the border/glyph color from the door
state (instead of always amber) and draw the state glyph (arch / bar /
padlock).

### 7.2 How `drawGridOnCanvas` changes

Today's doorway pass (`for ... if g.cells[y][x] !== "doorway" continue; ...
strokeStyle = palette(t).door; ... arch glyph`) is extended to consult the door
state:

- New `state.doors` = the `map.doors` object (from `applyState`; `{}` when
  absent). A helper `doorStateAt(x, y)` returns `"L"|"U"|"O"` for a `doorway`
  cell (default `"L"`), mirroring `Grid.door_state_at` on the client.
- The doorway pass now: `const st = doorStateAt(x, y);` and selects
  `palette(t).doorOpen / .doorUnlocked / .doorLocked` (full tier) or the
  explored equivalents, and draws the **arch** (open) / **bar** (unlocked) /
  **padlock** (locked) glyph. The `H` tier is still skipped (a hidden door is
  not drawn, consistent with the explored map).
- **A closed door renders as a closed-door cell (not as a wall, not as an open
  doorway):** it keeps its floor base + grid line + the state border/glyph. It
  is visually a *door*, distinct from the hatched wall and the open doorway.
- The **GM and preview passes** (no visibility matrix) render doors with the
  full-tier palette (the GM always sees true door state); the **player live
  pass** renders with the tier's (S/E) door palette. The matrix tiering (S/E/H)
  is unchanged — only the door color/glyph within S/E is state-driven.

### 7.3 `state` + `T` tokens

- `state.doors` (client-side render data; `{}` default) and the `doorStateAt`
  helper. `applyState` sets `state.doors = (msg.map && msg.map.doors) || {}`
  (additive; a missing key ⇒ `{}` ⇒ all doors render locked).
- New `T` tokens (alongside the existing palette):
  `doorOpen: "#d97706"`, `doorUnlocked: "#f59f00"`, `doorLocked: "#e03131"`,
  `exploredDoorOpen: "#8b94a3"`, `exploredDoorUnlocked: "#9a8f7a"`,
  `exploredDoorLocked: "#a06b6b"` (final hex at build, §7.1 constraint).

### 7.4 Legend chips (GM and player)

Doors concern **both** roles (the GM edits them; the player walks through
unlocked ones and must read the states), so **all door chips are visible to
everyone** (unlike the player-only explored chips). In `#legend`, after the
existing `doorway` chip, add (class `legend-doors`):

- `<span class="legend-chip legend-doors"><i class="swatch door-open"></i>open door</span>`
- `<span class="legend-chip legend-doors"><i class="swatch door-unlocked"></i>closed (unlocked)</span>`
- `<span class="legend-chip legend-doors"><i class="swatch door-locked"></i>locked</span>`

CSS: `.swatch.door-open { background: var(--floor); border: 2px solid var(--door-open); }`
and so on for the three states, with the corresponding `--door-open` /
`--door-unlocked` / `--door-locked` `:root` tokens mirroring `T`. The existing
`--doorway` token is kept (still used by the preview/thumbnail and the
"doorway" chip), and the three new tokens are added.

### 7.5 GM controls (D7) — a Door tool with state sub-buttons

The bottom `#paint-group` (GM-only) gains a **`Door`** tool **plus** a compact
**state sub-button row** (the recommendation: a Door tool mode *with* state
sub-buttons, consistent with the existing select/floor/wall/doorway bar):

- New tool `"door"` in `state.tool`; a `data-tool="door"` button `🚪 Door` in
  `#paint-group`. Selecting it arms door editing (GM-only, like the other
  paint tools).
- When the Door tool is active, a small **action sub-row** (or, simpler and
  recommended: **four** sub-buttons) appears: **Unlock**, **Lock**, **Open**,
  **Close** — each a `data-door-action` button. The GM picks an action, then
  **clicks a door cell** to apply it (same click-to-apply ergonomics as paint).
  (Alternative accepted: a single "Door" tool that, on clicking a door, shows
  a small inline action menu for *that* door; the four-button row is the
  default spec because it reuses the existing tool-row pattern.)
- Client `sendDoor(x, y, action)` → `wsSend({type:"door", x, y, action})`. The
  server is authoritative (state reconciles from the broadcast; no optimistic
  door mutation — doors are a GM action, low frequency).
- A **locked door** clicked by the GM with `open`/`close` gets the server's
  `"door is locked"` toast (no client gating — the server decides).

### 7.6 Player open/close (D7) — tap the door cell

> **ERRATUM (QA, door-features sign-off):** the original wording of this
> section mapped the player tap to the *same-direction* action — `U` → `close`,
> `O` → `open`. That mapping is **logically incoherent**: a door can only be
> `U` (closed, unlocked) when it is closed, so `U → close` is always
> "already closed", and an open door (`O → open`) is always "already open" —
> the player could **never** open a door, contradicting the user requirement
> "doors can be opened and closed" (the very behavior the feature exists to
> provide). It also contradicted this same section's *second* bullet ("tapping
> an open door toggles it closed", i.e. `O → close`) and AC11(d). The shipped
> mapping is the **inverse action** — a tap performs the action that *changes*
> the door — which is the only mapping that lets a player actually open a door.
> This section (and AC11(d)) is corrected below to pin the **shipped, coherent**
> behavior.

A **player** (no door tool) interacts by **tapping a doorway cell**:

- In the canvas `click` handler, **before** the "move" branch: if the cell is
  a `doorway` (it is therefore always a **door**) and the clicked cell has **no
  entity** on it, the player's tap is interpreted as the **inverse door action**
  for the door's current state (send `{type:"door", x, y, action}`):
  `U` (closed, unlocked) → `open`, `O` (open) → `close`, `L` (locked) → `open`
  (the server replies `"door is locked"` — shown as a toast — since a locked
  door is closed). The `L → open` case is the "locked door, you can't open it"
  feedback: the player cannot unlock (no such button), so the toast is the
  signal. A tap on a cell occupied by an entity is **not** a door action
  (entity selection/movement keeps priority).
- **Priority with movement:** a player taps a **door cell** (doorway) to
  *act on the door*; a player taps a **floor cell** to *move*. Because a door
  is a `doorway` (not a `floor`), there is no ambiguity: tapping a doorway cell
  acts on the door (open/close), tapping a floor cell moves. (A player cannot
  walk *onto* a closed door — it is not walkable; they open it first, then the
  cell becomes walkable and a subsequent floor/walkable click moves them
  through. For an *open* door, tapping the doorway cell toggles it *closed* —
  see the open-door tap rule above — so to walk through an open door the
  player simply clicks the *floor cell on the other side*, not the door cell
  itself.) This is pinned in AC11 and documented in the hint (§7.7).
- **Assumption A4:** "a player opens/closes a door by tapping the door cell"
  is the recommended interaction (CR7/UX). It reuses the existing single-tap
  click path and needs no new UI chrome for players.

### 7.7 Hints (control-bar `#control-hint` + canvas hints)

- GM, Door tool: `"Click a door to <action>"` (action from the selected
  sub-button).
- Player, hovering/tapping a locked door: `"That door is locked — the GM must unlock it"`.
- Player, tapping an open door to close it: `"...closing the door"` (server
  confirms via broadcast).
- Movement hint: the existing "Walls block movement" is **extended** — when a
  player/GM (without override) targets a cell that is **unreachable because of
  a closed door**, the server returns the existing `"no route — wall in the
  way"` (unchanged message — a closed door *is* a wall to movement). A
  player clicking a **closed door cell** to walk through gets the canvas hint
  `"That door is closed — open it to pass"` (client-side, since the client
  knows the door state).

---

## 8. Wire protocol (design decision D6)

### 8.1 The `doors` field (additive, inside `map`)

- **Server→client:** door state is **not** a new broadcast type. It rides
  inside the **existing `map` object** of every `welcome`/`state` payload (and
  the REST map responses, §8.2), as an additive **`doors`** field:
  - `map.doors` is a JSON **object** `{"<x>,<y>": "L"|"U"|"O", ...}` containing
    **every door cell and its current state**. (Emitting the full door set —
    including the default `"L"` entries — makes the wire self-contained and
    trivially testable; the cost is at most one entry per doorway, which is
    small: the sample map has 3, a 60×60 BSP dungeon has ~rooms−1.)
  - **When `map.doors` is absent** (no door entries at all — a map with no
    doorway cells, or an all-locked map serialized by the *minimal* policy)
    ⇒ the client treats every doorway as **`L`** (locked). To avoid any
    ambiguity about "is this door open?", the **server emits the full object
    whenever the grid has ≥ 1 doorway** (so every door's state is explicit on
    the wire); the client *also* defensively defaults a missing `doors` to all
    `L`.
  - The client stores it in `state.doors` (`applyState`) and uses
    `doorStateAt(x, y)` for rendering. A malformed `doors` (wrong charset /
    non-object) is treated as `{}` (all locked) — defensive, never crashes.
- **No new server→client message type.** `welcome`/`state`/`path`/`error`
  shapes are unchanged except the additive `map.doors` key. The `visibility`
  (explored) field, `players[]`, `entities[]`, `you_entity`, `awareness`, and
  `fog` are all **byte-identical** in shape to today.

### 8.2 REST (D8) — additive `doors` in every `map` object

The REST surface is **frozen except** for the additive `doors` key in the map
object (CR: "the REST surface stays frozen apart from the additive field"):

- `GET /api/maps/{id}` → the map detail object gains `"doors": {...}` (all
  doors at their current state; the sample map returns the 3 doors, all `L`
  until changed). **Existing pinned keys are unchanged** (`id`,`name`,`width`,
  `height`,`cells`,`image`,`entities`,`players`); `doors` is additive.
- `POST /api/maps/upload` response → `"doors": {...}` (all detected doorway
  cells, **all `L`** — a fresh upload starts all-locked, CR1/D4).
- `POST /api/maps/generate` response → `"doors": {...}` (all carved doorways,
  **all `L`**).
- `POST /api/maps/{id}/paint` → the response shape is **unchanged**
  (`{"ok":true,"x","y","cell_type"}`); painting a `doorway` creates it locked
  (the door state is applied server-side, D4), but the *response* does not
  echo door state (frozen). A subsequent `GET /api/maps/{id}` reflects it.
- **No new REST route** for doors (door actions are WS-only, like the other GM
  tools). The `doors` field is **additive and optional to parse** — a client
  that ignores it still works (it just renders all doors locked, which is the
  safe default).

Because the REST and WS both read the **same `Grid` object** from
`maps_registry`/the session (object identity shared), the door state is
consistent across both surfaces with no extra synchronization.

### 8.3 The `door` message (client→server)

```json
{ "type": "door", "x": 5, "y": 5, "action": "unlock" }
```

- `action` ∈ `unlock` | `lock` | `open` | `close`.
- Sent by the **GM** (any action) and by **players** (`open`/`close` on an
  unlocked door). The server enforces the full permission matrix + state
  machine (§4) and replies per-client error on any failure; on success it
  applies the state and broadcasts the `state` (which carries the new
  `map.doors`). No per-client success frame (house style, cf. `paint`).
- Validation order is the deterministic sequence of §4.3 (AC3).

---

## 9. Paint interaction (design decision D4)

- **Painting a `doorway`** (WS `paint` with `cell_type:"doorway"`, or REST
  `POST /api/maps/{id}/paint`) **creates a door in the DEFAULT `locked`
  state** — i.e. the cell becomes a `doorway` and, if it had no prior door
  state, it is `L`. If the cell was *already* a doorway (already a door),
  repainting it as a doorway is a **no-op for door state** (it keeps its
  current `L`/`U`/`O`) — painting is a cell-type edit, not a state reset.
- **Painting `floor` or `wall` over a door** **deletes its door state** (the
  cell is no longer a doorway, so it has no door; `doors` drops the key).
- **Upload / generate / sample maps start all-locked** (CR1): the detected /
  carved / hand-authored `doorway` cells are all `L` (i.e. `doors` is either
  absent or all `L`).
- **Implementation:** the single sync point is `Grid.sync_doors_after_cell_set`
  (§3.5), called by **both** the WS `_on_paint` (after `self.grid.cells[y][x]
  = cell_type`) and the REST `_handle_paint` (after `grid.cells[y][x] =
  cell_type`). This guarantees door state can never desync from the cell type,
  whether the paint came over WS or REST. (No other code path mutates
  `cells` at runtime; `_on_use_map` swaps the whole `Grid` object, bringing
  its doors with it — §11 E3.)

---

## 10. Invariants (all AC-tested)

- **I1 — State only on doorway cells.** `Grid.doors` (when not `None`) contains
  a key **only** for in-bounds `doorway` cells, and every value is in
  `{"L","U","O"}`. `__post_init__` enforces this; `door_state_at` returns
  `None` for non-doorway cells.
- **I2 — Default is locked.** A `doorway` cell with **no** entry in `doors` (or
  `doors is None`) is **`L`** (closed+locked). No door is ever "unlocked by
  default."
- **I3 — The GM view is never door-filtered.** The GM's `state` payload carries
  the full `entities` list (all, true colors, labeled) and the full `map.doors`
  regardless of door state; the GM's awareness is unfiltered (existing rule).
  Doors never hide anything *from the GM*.
- **I4 — A closed door is a wall, an open door is a doorway.** For every
  (grid, door state) pair: `walkable(closed door) == False`,
  `walkable(open door) == True`; `has_line_of_sight` is blocked by a closed
  door and transparent to an open door; the diagonal no-corner-cut rule treats
  closed doors like walls. (AC4, AC5.)
- **I5 — Every `map` payload carries the full door state.** Every `welcome` /
  `state` / REST map object for a grid that has ≥ 1 doorway carries
  `map.doors` with **every** door's current state (no partial/stale door
  state). (AC1, AC10.)
- **I6 — Awareness + explored logic are unchanged.** `app/awareness.py` is
  byte-unchanged; `visible_cells`/`build_visibility_mask` S/E/H logic is
  unchanged (only the door-aware `has_line_of_sight` they call changed, plus
  the D5 closed-door face branch). (AC6, AC7, AC8.)
- **I7 — Atomic state machine.** A door action either fully applies one legal
  transition or is fully rejected (an error); no partial state; the
  `(state, action)` → result mapping is total and deterministic. (AC3.)
- **I8 — No entity left on a closed door.** A `close` is rejected when any
  entity occupies the door cell (D3); therefore a closed door cell never has an
  entity standing on it after a successful mutation. (AC9.)
- **I9 — Monotonic explored memory.** Within a map, a cell never goes S/E → H
  (existing explored-map invariant, preserved; doors only *shrink* the S-set,
  which moves cells to E, never to H, unless never seen). (AC8.)
- **I10 — Server-authoritative.** The server never trusts a client-claimed door
  state; the door state is only ever changed by a validated `door` message or a
  GM paint, and the broadcast is the source of truth the clients render.

---

## 11. Edge cases

| # | Case | Behavior |
|---|---|---|
| E1 | **Entity on a door when it closes (D3).** A player/NPC/enemy token is standing on a door cell (reachable only if the door is currently **open** — a closed door is not walkable, so a token can only be *on* a door while it is open). GM (or that player) sends `close` → **REJECTED** with `"cannot close a door with a token on it"` (I8). The door stays open. The GM must move the token off first. (This is the occupancy guard — an entity must never be left on a non-walkable cell.) AC9. |
| E2 | **A player closing a door that another player's in-flight path used.** Movement is **re-validated server-side on every `move` request** (the server runs A* fresh against the *current* grid each time — there is no stored "committed path" that survives a door change; the `path` frame is just the route for the *current* move). If the door closes after a player *requested* a move through it but before the server processes it, the server's A* sees the closed door → **`"no route — wall in the way"`** (or a longer detour if one exists). If the move was already processed (token past the door), nothing changes. A token *mid-animation* is a client-side render concern — the authoritative position is the server's, and the next `state` reconciles it. **Behavior: the door closing simply makes the route illegal/longer for any *subsequent* (not-yet-processed) move; no crash, no stuck entity.** AC14/e2e. |
| E3 | **`use_map` swap while doors are open.** `_on_use_map` swaps `self.grid` to the target `Grid` object **and clears `_explored`** (existing D3 of explored-map). The new `Grid` **carries its own `doors`** (from `maps_registry`) — so **door state resets with the new grid** (each map's doors are its own; an open door on map A does not carry to map B). Because the grid object is swapped wholesale (not cell-patched), `doors` follows automatically — **verified against `_on_use_map`** (it re-parks entities and clears explored; no door-specific code needed). AC14. |
| E4 | **Painting a non-doorway cell with a door action.** A `door` message on a `floor`/`wall` cell → `"not a doorway"` (§4.3 step 3). No state change. AC3. |
| E5 | **Out-of-bounds door action.** `door` message with `x`/`y` outside the grid → `"destination out of bounds"` (before the cell check). AC3. |
| E6 | **`door` message with a bad/missing `action`.** → `"action must be one of unlock/lock/open/close"`. AC3. |
| E7 | **A player standing on a doorway cell while the GM closes it.** A player can only be *on* a door cell while it is **open** (closed doors are not walkable; a spawn `_find_free_floor` may place a token on a doorway only if it is a *free floor/doorway* — with all doors locked by default, a spawn never lands on a closed door; it lands on a floor). So the situation "player standing on the door the GM closes" means the door is currently **open** and the player is on it → the GM's `close` is **REJECTED** (E1 / D3, `"cannot close a door with a token on it"`). The player is never trapped on a closed door. (If the GM instead `lock`s it — a locked door is still closed, but a token on it is allowed by D3 since D3 only guards `close`... see A5.) AC9. |
| E8 | **Reconnect / disconnect.** Door state is on the `Grid` (map state), so it **survives** disconnect/reconnect and process-internal re-attach exactly like the grid (no per-player door memory). (A process restart is a fresh in-memory state, as with everything else.) |
| E9 | **Two GM actions race on the same door.** All door actions run under the session `RLock`; the second action sees the first's new state (e.g. GM `unlock` then GM `lock` serialize; the second is either legal or `"door is already …"`). Deterministic. (I7.) |
| E10 | **A door at a map border / a doorway with no wall neighbours.** Door state and behavior are independent of the doorway *heuristic* (opposite-wall) — any `doorway` cell (however created) is a door. A closed border doorway blocks sight/movement like any closed door. No special-casing. |
| E11 | **GM `place` / `override` puts a token ON a closed door.** The GM *can* teleport/place a token onto a closed door cell (override ignores walkability, §5.3). The token then sits on a non-walkable cell — it simply **cannot move away via A*** (start cell non-walkable → `find_path` returns `None` → `"no route — wall in the way"`) until the GM opens the door or moves it by override/place. This mirrors the existing "token on a wall via override" degenerate case (explored-map E6) — no crash; the GM resolves it. (Documented, not a supported normal flow.) |
| E12 | **`doors` present but a key for a cell later painted to wall.** Impossible to persist: paint-to-wall deletes the key (D4, §9), and `__post_init__` rejects a door key on a non-doorway cell. A hand-crafted inconsistent `doors` is caught at construction (I1). |

> **Assumption A5 (edge E7 nuance, pinned by AC9):** D3 (occupancy guard)
> applies to **`close`** specifically (the action that makes a cell
> non-walkable). **`lock`** (GM-only) on a door that is *closed but a token is
> somehow on it* is **allowed** (it does not change walkability — a locked
> door is already closed, so a token on it was already on a non-walkable cell
> via the E11 GM-place degenerate case). If a token is on an *open* door, the
> GM `lock`-ing it (which force-closes, D2) **also** closes it → this is
> guarded by the same occupancy check (lock-while-open ⇒ close ⇒ occupancy
> check). **Pinned:** the occupancy check fires on any transition that results
> in the door becoming **closed** (`close`, and `lock` from `open`). A `lock`
> from `unlocked` (already closed) is not occupancy-guarded (the door was
> already closed).

---

## 12. Explicit non-changes

- **Awareness (HARD CONSTRAINT):** `app/awareness.py` is **byte-unchanged**; the
  three-tier FULL/APPROXIMATE/INVISIBLE model, `build_awareness`'s logic,
  awareness items, the awareness ring, the awareness sidebar — **unchanged**.
  Door state affects awareness *only* through the (changed) line-of-sight
  predicate (AC6).
- **Explored-map algorithm:** `visible_cells`/`build_visibility_mask` S/E/H
  logic unchanged (only the door-aware LOS they consume + the D5 closed-door
  face branch). The `_explored` lifecycle (frozen/cleared/pruned) unchanged.
- **Cell type vocabulary:** `floor`/`wall`/`doorway` unchanged — a door is a
  `doorway` cell + a state, **not** a fourth cell type (the `CELL_TYPES`
  tuple, `is_valid_step`'s base walkability, detection, generation, and the
  "doorway is a gap in a wall" geometry all keep their exact meaning).
- **`players[]` / `Player.to_dict()`:** unchanged (door state is map state,
  not per-player).
- **`fog`:** unchanged, still a wire-compat no-op.
- **`path` / `error` frame shapes:** unchanged (the `door` *action* replies
  with the existing `error` shape; a successful door action uses the existing
  `state` broadcast, not a new frame).
- **The sample dungeon geometry** (`app/grid.py`): byte-unchanged — its three
  `doorway` cells are simply doors (closed+locked by default).
- **GM:** never door-filtered (I3); tools gain a Door tool, nothing else changes.
- **Movement permission rules** (player self-move-only, `override` GM-only,
  `"no route — wall in the way"`, `"not allowed"`, bounds) unchanged.
- **Upload / generate endpoints' request shapes** unchanged (only the response
  gains the additive `doors` key).
- **`app/main.py` (registry/session), `app/imaging.py`, `app/ws.py`:**
  unchanged.

---

## 13. Explicit assumptions (per PROJECT.md ambiguity convention)

Every ambiguous point in the requirement is resolved here and pinned by an AC.

- **A1 — Default state (CR1) supersedes the frozen "open doorway" test
  baseline.** The requirement says *"by default all the doors should be closed
  and locked."* Existing tests + `e2e_proof` step 9 + the explored-map W4 mask
  literal were written when **every doorway was an open, transparent gap**.
  Making doors **closed+locked by default** therefore **changes the default
  behavior those tests assumed** (a closed door is a wall to movement and LOS).
  **Resolution:** the requirement wins (PROJECT.md: "prefer the interpretation
  stated ... and note the assumption"). The affected existing tests are
  enumerated below and updated to either (a) assert the new closed-by-default
  behavior, or (b) first **unlock + open** the relevant door to reproduce the
  original open-doorway scenario. This is the **only** place in the spec where
  pre-existing tests are modified, and every such modification is called out
  in §14 (test plan) so QA can audit it.
  - *Affected existing tests (must be updated, §14):*
    - `tests/test_pathfinding.py`: `test_floor_and_doorway_walkable` (a
      bare `doorway` is now **not** walkable → update to open the door first,
      or assert the closed door is blocked), `test_elbows_may_be_doorways`
      (doorway elbows are now closed→blocked → open them first),
      `test_routes_through_the_door_gap`, `test_door_diagonal_elbow_is_walkable`,
      `test_doorway_does_not_block_sight` (a closed door **does** block sight
      → open it first to keep the "does not block" assertion).
    - `tests/test_awareness.py`: `test_doorway_passes_line_of_sight` (closed
      door blocks → open first).
    - `tests/test_session.py`: `test_move_to_doorway_is_walkable` (open the
      (5,5) door first).
    - `tests/test_visibility.py`: the **W4 mask literal** and every test that
      relies on the sample dungeon's doors being open (e.g.
      `test_sd_doorways_seen_only_via_s1`) → recompute/adjust for closed-by-
      default doors (the re-derivation oracle in that file is the source of
      truth; the literal is a fixture to be regenerated).
    - `tests/test_ws.py`: `test_no_route_without_override_and_gm_override`
      (any scenario relying on a doorway being passable → open first).
    - `tests/test_frontend.py`: the `doorway` legend/chip assertions are
      unchanged, but any snapshot of door rendering gains the state-aware
      palette (extend, don't rewrite).
    - `scripts/e2e_proof.py` **step 9** (explored-map doorway walk): the GM
      must **unlock + open** the doorways before the player walks through them
      (add door messages); the S-set re-derivation must use the door-aware
      `has_line_of_sight`.
  - *New tests (closed-by-default behavior):* every door is `L` after
    upload/generate/sample/paint; a closed door blocks A* and LOS; open door
    walkable + transparent (the §14 "door" test classes).
- **A2 — Backward compatibility = all locked.** A payload/grid with no `doors`
  key ⇒ every door is **locked** (the safe default). Never "unlocked by
  default" (that would be a security/behavior surprise). (AC1, AC13.)
- **A3 — GM `override:true` bypasses closed doors** (it is a teleport
  exception, not a walk). Automatic (the override path ignores walkability);
  pinned by AC5.
- **A4 — Player opens/closes a door by tapping the door cell** (UX), GM uses
  the Door tool + action sub-buttons. Pinned by AC11 (frontend).
- **A5 — Occupancy guard (D3) fires on any transition that makes the door
  *closed*** (`close`, and `lock` from `open`). `lock` from `unlocked` is not
  guarded (already closed). (AC9, §11 E7/A5.)
- **A6 — "A different colour to the floors and walls"** is satisfied by the
  red (locked) / amber (unlocked) / amber (open) palette, all distinct from
  `#efe9dc` and `#3b4252`; the three states are distinguishable by hue **and**
  glyph. (AC10.)
- **A7 — Lock-while-open force-closes** (no "open and locked" state). (AC3.)
- **A8 — REST stays frozen apart from the additive `doors` field** (no new
  door REST route; door actions are WS-only). (AC10.)
- **A9 — `map.doors` is emitted as the full door object whenever the grid has
  ≥ 1 doorway** (so the wire is unambiguous); a missing key ⇒ all locked.
  (AC1, AC10.)
- **A10 — A closed door's own cell is revealed by the wall-face rule (D5)** so
  a player sees the closed door (its face) when standing in front of it, and it
  renders in the current tier. (AC7.)

---

## 14. Test plan

Mapped to the existing files + a new live script. All deterministic. New
behavior is added in **new test classes** (so it never perturbs unrelated
assertions); the **A1-affected existing tests** are updated as enumerated in
§13 (and cross-referenced here).

- **`tests/test_models.py` (new, or a new class in `tests/test_grid.py`):**
  door state model (AC1, AC2, AC13). `doors` round-trip via `to_dict`/
  `from_dict`; default `None` ⇒ all locked; `__post_init__` rejects a door key
  on a non-doorway cell, an out-of-bounds key, and a bad state char;
  `door_state_at`/`is_door_closed`/`set_door`/`sync_doors_after_cell_set`
  (paint sync: doorway→locked, wall/floor→delete, repainted doorway keeps
  state).
- **`tests/test_pathfinding.py`** (A1-updates + new door classes):
  - *Update (A1):* `test_floor_and_doorway_walkable`, `test_elbows_may_be_
    doorways`, `test_routes_through_the_door_gap`,
    `test_door_diagonal_elbow_is_walkable`,
    `test_doorway_does_not_block_sight` — open the door(s) first (pass
    `doors={"x,y":"O"}`) to preserve the original open-doorway assertion.
  - *New:* closed door is **not** `walkable` and **not** a legal step target;
    open door is walkable; a diagonal whose elbows are closed doors is a
    corner-cut (blocked); `has_line_of_sight` blocked by a closed door,
    transparent to an open door; `find_path` routes around a closed door /
    through an open one; `None` path behind an all-closed door; `doors=None`
    ⇒ all locked (regression: a bare grid's doorway is blocked). (AC4, AC5.)
- **`tests/test_visibility.py`** (A1-updates + new):
  - *Update (A1):* the **W4 mask literal** + tests relying on open sample doors
    (`test_sd_doorways_seen_only_via_s1`, any W3/W4-dependent spot asserts) —
    regenerate the literal for **closed-by-default** doors (the file's
    re-derivation is the oracle) or open the doors first in the fixtures.
  - *New:* a closed door's far side is **H** (never explored) / **E** (explored
    before); a closed door's **face** is S (D5, revealed by an adjacent seen
    floor); opening a door expands S and folds into explored; closing falls
    back to E/H; S is monotonic (I9). (AC6, AC7, AC8.)
- **`tests/test_session.py`** (A1-updates + new `_on_door` classes):
  - *Update (A1):* `test_move_to_doorway_is_walkable` (open (5,5) first).
  - *New:* full state-machine + permission matrix (AC3) — every legal
    transition; every illegal `(state, action)` returns the exact error string
    in the deterministic validation order (§4.3); GM-only `unlock`/`lock` →
    player `"not allowed"`; player `open`/`close` on locked → `"door is
    locked"`; occupancy `close` rejection (AC9, D3); `use_map` swap resets
    doors with the grid (AC14, E3); `map.doors` present + full in every
    `state`/`welcome` (AC1, I5); GM unfiltered (I3); awareness unchanged
    through door state (AC6); explored unchanged (AC7/8).
- **`tests/test_ws.py`** (A1-updates + new wire tests): the `door` message over
  a real WS (GM + player); error replies per-client; success → broadcast
  carries new `map.doors`; a player `open`ing an unlocked door works; a player
  `unlock` gets `"not allowed"`; the A1-affected
  `test_no_route_without_override_and_gm_override` updated (open the door).
- **`tests/test_frontend.py`** (new, extend): `index.html` has the three
  `legend-doors` chips + `#paint-group` has a `data-tool="door"` button and the
  four `data-door-action` sub-buttons (static checks, existing idiom); the
  `T` tokens + `state.doors` present; (harness) `drawGridOnCanvas` renders the
  three door states in the correct full-tier colors and the explored-tier
  greys, a GM pass (no matrix) renders full-tier, and a `map.doors`-driven
  `doorStateAt` returns the right state. (AC10, AC11.)
- **`tests/js/harness.js`:** add `drawGridOnCanvas` (already exported),
  `doorStateAt`, and `state` to `EXPORTS`; the stub canvas `ctx` already
  records `fillStyle`/`strokeStyle` — extend to record `strokeRect`/`moveTo`/
  `lineTo` for the door glyph if needed, so the harness can assert the
  per-state door color.
- **`scripts/e2e_proof.py` — new step 10 (door feature, live server):**
  - (a) GM + player join a fresh session on the **sample dungeon**; assert
    every door is `L` in the welcome `map.doors` (all locked, AC2/CR1).
  - (b) GM `unlock` the (5,5) door → `state` shows it `U`; GM `open` it → `O`.
    The player now **walks** (1,1)→(5,5)→(7,2) **through the open door** (legal
    A* steps; `is_valid_step` on each `path` frame). Before the door was opened,
    that move was `"no route — wall in the way"` (asserted first, closed).
  - (c) **Sight/awareness:** with the door **closed**, an enemy behind it shows
    the player as **APPROXIMATE** (within radius) / **INVISIBLE** (beyond);
    with the door **open**, **FULL**. (AC6.)
  - (d) **Explored:** with the door closed, the room beyond is **H** (never
    seen) then **E** (after the GM moves the player through once and back);
    opening the door reveals it as **S**. (AC7/8.)
  - (e) **Occupancy:** GM tries to `close` a door with a token on it →
    `"cannot close a door with a token on it"` (AC9).
  - (f) **Permissions over the wire:** player `unlock` → `"not allowed"`;
    player `open` on a locked door → `"door is locked"`; player `open` on an
    unlocked door → success (AC3).
  - The script uses the existing independent S-set re-derivation helper,
    **extended to treat closed doors as blockers** (so the wire is checked
    against the spec, not the server code).
- **`scripts/qa_doors.py` (new live script):** a standalone, human-runnable
  door QA script (mirrors `scripts/qa_explored_map.py`'s structure): boots the
  server, drives GM + player over WS, and prints a check per door behavior
  (default locked, GM unlock/open, walk-through, closed-door sight + awareness
  tiers, explored H/E, occupancy rejection, player permissions, REST
  `doors` field present on `GET /api/maps/{id}` + upload + generate). Exits
  non-zero on any failure. This is the "live" companion to `e2e_proof.py`.

---

## 15. Acceptance criteria (for QA)

Individually testable, following `explored-map.md` §12. The harness: in-process
`GameSession` + `FakeConn`/`drive` (`tests/test_session.py` idioms), pure
`app.pathfinding`/`app.visibility`/`app.models` unit tests, raw-socket WS
(`tests/wsclient.py` + `make_server`), the Node harness (`tests/js/harness.js`
+ `tests/test_frontend.py`), and `scripts/e2e_proof.py` / `scripts/qa_doors.py`
over the live server. All deterministic.

- **AC1 — Door state model + round-trip.** `Grid.doors` (a) round-trips
  `to_dict`→`from_dict` preserving every state; (b) `doors=None` ⇒ every
  doorway is `L` (all locked) and `to_dict` omits the key (or emits `{}`); (c)
  `doors={"5,5":"O"}` round-trips; (d) `__post_init__` raises `ValueError` for
  a door key on a `floor`/`wall` cell, an out-of-bounds key, and a state char
  not in `{"L","U","O"}`; (e) `door_state_at` returns `None` for non-doorway
  cells, `L` for an unrecorded doorway, and the recorded state otherwise;
  (f) `is_door_closed` is True for `L`/`U`, False for `O`.
- **AC2 — Default locked on every creation path.** (a) `build_sample_map()`
  has its 3 doorways all `L` (doors=None); (b) a `POST /api/maps/upload` with a
  known doorway returns `doors` where every doorway is `L`; (c) a
  `POST /api/maps/generate` (fixed seed) returns `doors` where every carved
  doorway is `L`; (d) GM WS `paint` of a `floor` cell to `doorway` creates it
  `L`. (CR1.)
- **AC3 — State machine + permissions (exact).** For each of the 3 states × 4
  actions: the legal `(state, action, role)` combinations apply the transition
  and broadcast; every illegal combination returns the **exact** error string
  in the §4.3 deterministic order. Specifically: GM `unlock` L→U; GM `lock`
  U→L; GM `lock` O→L (**force-closed**, A7); GM/player `open` U→O; GM/player
  `close` O→U; player `unlock`/`lock` → `"not allowed"`; player/GM `open`/
  `close` on `L` → `"door is locked"`; `open` on `O` → `"door is already
  open"`; `close` on `L`/`U` → `"door is already closed"`; `unlock` on `U`/`O`
  → `"door is already unlocked"`; `lock` on `L` → `"door is already locked"`;
  non-doorway cell → `"not a doorway"`; OOB → `"destination out of bounds"`;
  bad action → `"action must be one of unlock/lock/open/close"`.
- **AC4 — Closed door blocks LOS like a wall (incl. corner-cut).** `has_line_of_sight`
  with a **closed** door cell strictly between `a`,`b` → **False** (identical
  to the same geometry with a `wall`); with the same door **open** → **True**;
  a diagonal sight step whose **both elbows are closed doors** → blocked
  (corner-cut), one elbow open → passes. The GM is never filtered (I3).
- **AC5 — Closed door blocks movement; open door walkable; override bypass.**
  `walkable(closed door)==False`, `walkable(open door)==True`; `find_path`
  behind an all-closed door → `None` (→ `"no route — wall in the way"` in
  session); `find_path` through an open door → a path (legal `is_valid_step`s);
  a diagonal into a closed door is illegal (elbow/step blocked); GM
  `override:true` moves a token **onto/through** a closed door (bypass, A3);
  a player can never `override` (existing rule).
- **AC6 — Awareness UNCHANGED, door-driven only via LOS (CR5).** (a)
  `app/awareness.py` is byte-unchanged (no diff). (b) Scenarios: an enemy
  behind a **closed** door within the player's radius → **APPROXIMATE**
  (grey "?", no identity); beyond the radius → **INVISIBLE** (absent); behind
  an **open** door with a clear line → **FULL** (named/labeled); a **closed**
  door with an *open elbow detour* giving a true LOS → FULL (LOS is exact). (c)
  The GM's awareness is unfiltered regardless of door state. (d) For the
  sample dungeon **with all doors open**, the `awareness` output is
  **byte-identical to the pre-feature build** (regression pin: opening the
  doors reproduces the old behavior exactly).
- **AC7 — Explored map UNCHANGED, closed-door far side is H/E, face is S (CR6,
  D5).** (a) A closed door's far-side floor cell, never seen → **H**; seen
  before → **E (greyed)** (exactly like an out-of-LOS area). (b) A closed
  door's **own cell** is **S** when an adjacent walkable neighbour is in sight
  (the D5 face rule — the player sees the closed door). (c) Opening the door
  makes newly-visible cells **S** and folds them into the player's explored
  set; closing again moves them to **E** (seen) / **H** (never seen). (d) S is
  **monotonic** (no S/E → H) across door open/close cycles within a map. (e)
  For the sample dungeon **with all doors open**, the visibility mask equals
  the **pre-feature** mask (regression pin).
- **AC8 — Monotonicity + freeze preserved.** Across a scripted door
  open/close/walk sequence, no cell ever goes S/E → H (I9); a token-less
  player's explored set stays frozen (existing rule) while doors change.
- **AC9 — Occupancy guard (D3, A5).** `close` with any entity on the door cell
  → `"cannot close a door with a token on it"`, door stays open; `lock` from
  `open` with an entity on the cell → same rejection (it force-closes); `lock`
  from `unlocked` is **not** occupancy-guarded (A5); after any successful
  close, **no** entity occupies a closed door cell (I8).
- **AC10 — Wire + REST + rendering (D6/D8/D7/CR8).** (a) Every player & GM
  `welcome`/`state` carries `map.doors` with **every** doorway's state (I5); a
  grid with no doorways omits it (client ⇒ all locked). (b) `GET
  /api/maps/{id}`, upload, and generate responses carry `doors` (all `L` on
  fresh maps); no **new** REST route (A8); existing REST keys unchanged. (c)
  The `T` palette has the three full-tier door colors (`#d97706`/`#f59f00`/
  `#e03131`) all **distinct from floor `#efe9dc` and wall `#3b4252`** (CR8,
  A6) plus the explored-tier variants; (d) `index.html` has the three
  `legend-doors` chips and the GM Door tool + 4 action sub-buttons;
  `#paint-group` unchanged otherwise. (e) The GM legend and player legend both
  show the door chips.
- **AC11 — Frontend rendering + interaction (harness + static).** (a)
  `drawGridOnCanvas` renders the three door states in the §7.1 full-tier
  colors (open=amber+arch, unlocked=amber+bar, locked=red+padlock) and the
  explored-tier greys when a visibility matrix is present; the GM/preview pass
  (no matrix) renders full-tier. (b) `state.doors` is set from `msg.map.doors`
  (`{}` when absent); a malformed `doors` ⇒ `{}` (all locked), never crashes.
  (c) GM Door tool: selecting an action + clicking a door cell sends
  `{type:"door", x, y, action}`. (d) Player tap sends the **inverse** action for
  the door's state (ERRATUM — see §7.6): tapping a `U` (closed, unlocked) door
  cell sends `open`, tapping an `O` (open) door cell sends `close`, tapping a
  `L` (locked) door cell sends `open` (server replies `"door is locked"`).
  (e) Preview canvas untouched (still no door state beyond the map's).
- **AC12 — Backward compatibility (A2).** A `Grid.from_dict` of a payload with
  **no** `doors` key yields an all-locked grid; the old two/three-argument
  `walkable`/`is_valid_step`/`has_line_of_sight` calls still run (and behave
  as all-locked for a bare grid's doorways); old `welcome`/`state` consumers
  that ignore `doors` still work (all doors render locked).
- **AC13 — A1 test-impact audit.** Every existing test enumerated in §13 (A1)
  is updated exactly as described (open the door first, or assert the new
  closed-by-default behavior), and **no** existing test's *intent* is silently
  dropped; the updated tests pass; the new door test classes pass. (This is the
  auditable bridge between the requirement and the frozen baseline.)
- **AC14 — e2e + live proof.** `scripts/e2e_proof.py` **step 10** (door feature)
  is all-✓ (default locked, unlock/open, walk-through, closed-door awareness
  tiers, explored H/E, occupancy rejection, player permissions, re-derived
  S-set with door-aware LOS); `scripts/qa_doors.py` is all-✓ over the live
  server; `GET /health` ok.
- **AC15 — Performance budget.** On a 60×60 grid (`generate_grid(60, 60,
  "perf", seed=1)`) with **many doors** (every carved doorway, all in a mixed
  open/closed state) and 6 players + a GM attached (fake conns, measure
  `state_for` directly): one full recompute (loop over all 6 players,
  `state_for` each) completes in **< 250 ms** on the reference machine class;
  assert with `time.perf_counter` at a **500 ms** bound. Also assert one
  single-player 60×60 `visible_cells` + mask build is within the explored-map
  §9 budget (the door-aware `has_line_of_sight` adds only a constant-factor
  door lookup per Bresenham step — no new asymptotic cost). The door-aware
  predicates must not degrade A* beyond a constant factor (assert a 60×60
  `find_path` across many open doors < 50 ms).
- **AC16 — Full regression.** `python -m pytest` **and** `python -m unittest
  discover -s tests -t .` fully green **with only the §13(A1)-enumerated
  modifications** (all other existing tests unmodified); `scripts/e2e_proof.py`
  all-✓ including the new step 10; `GET /health` ok; the sample dungeon
  geometry (`app/grid.py`) byte-identical; `app/awareness.py` byte-identical.

---

## 16. Non-goals

- **No door swing / open-close animation or sound** (doors are instant state
  changes; the client re-renders from the broadcast — consistent with the
  existing no-animation-for-structural-changes stance).
- **No per-door ACLs beyond the lock state** (no "only player X may open this
  door", no per-door team permissions — the only gate is GM-unlock + the
  unlocked precondition).
- **No auto-open-on-approach / proximity auto-open** (a door only changes
  state on an explicit `door` message or GM paint).
- **No client-predicted door state** (the server is authoritative; the client
  renders the broadcast `map.doors`, no optimistic door mutation).
- **No door "thickness"/multi-cell doors, no sliding/rotating geometry, no
  door on a `floor`/`wall` cell** (a door is exactly one `doorway` cell + a
  state).
- **No new REST door endpoints** (WS-only door actions; REST only gains the
  additive `doors` field).
- **No persistence / save-load of door state** (in-memory, like all session
  state — a restart is fresh).
- **No changes to the doorway *detection heuristic*** (opposite-wall rule) or
  to generated/sample map *geometry* — this feature changes door *state*, not
  where doorways are.

---

## 17. Implementation impact table (file by file)

| File | Change | Summary |
|---|---|---|
| `app/models.py` | **Modify** | Add `DOOR_STATES = ("L","U","O")`. `Grid`: new field `doors: dict[str,str] \| None = None`; `__post_init__` validates door keys (doorway-only, in-bounds, valid state); `to_dict` emits additive `doors`; `from_dict` reads it; add `door_state_at`, `is_door_closed`, `set_door`, `sync_doors_after_cell_set` (§3.5). `Entity`/`Player`/`Session` **unchanged**. |
| `app/grid.py` | **Unchanged** | Sample dungeon geometry untouched (its 3 doorways are doors, `L` by default — `doors=None`). |
| `app/pathfinding.py` | **Modify** | Add `_closed_doors(grid)`; make `walkable`, `is_valid_step`, `has_line_of_sight` door-aware via an optional `doors` param (default derived from the grid ⇒ all-locked for a bare grid). Closed door blocks walk/step/LOS like a wall; open door transparent; no-corner-cut treats closed doors like walls (§5.1). `find_path` unchanged (uses the door-aware predicates). |
| `app/visibility.py` | **Modify (minimal)** | `visible_cells`: add the **D5 closed-door face branch** (a doorway in `closed_doors` is S via the wall-face 4-neighbour test instead of S1). `build_visibility_mask` S/E/H **unchanged**. Reuses the door-aware `has_line_of_sight`. |
| `app/awareness.py` | **Unchanged (byte-identical, I6)** | No door special-casing; inherits door blocking through `has_line_of_sight` (AC6/AC16). |
| `app/session.py` | **Modify** | Add `DOOR_ACTIONS`, `mtype == "door"` dispatch → `_on_door` (the §4.4 state machine + permissions + occupancy). `_on_paint`: call `self.grid.sync_doors_after_cell_set(x, y)` after setting the cell (D4). `_on_move`/`_on_place` unchanged (door-awareness flows through `find_path`/walkability). `state_for`/`_visibility_for` unchanged in logic (the door-aware `visible_cells` does the work). |
| `app/detection.py` | **Unchanged (behavior)** | Detection still produces `doorway` cells (now all `L`). The **thumbnail** (`_THUMB_DOOR` amber) is left as-is (a preview hint; optional future: render locked — out of scope, non-goal). |
| `app/generation.py` | **Unchanged (behavior)** | BSP still carves `doorway` cells (all `L` by default). No geometry change. |
| `app/server.py` | **Modify (additive)** | `maps_detail` (`GET /api/maps/{id}`), upload, and generate responses gain the additive `"doors"` key (from `grid.to_dict()` / `grid.doors`). `_handle_paint` calls `grid.sync_doors_after_cell_set` after the cell set (D4, REST side). **No new route.** |
| `app/static/index.html` | **Modify** | `#paint-group`: add `data-tool="door"` `Door` button + the four `data-door-action` sub-buttons (Unlock/Lock/Open/Close). `#legend`: add the three `legend-doors` chips. |
| `app/static/app.js` | **Modify** | `state.doors` + `doorStateAt`; `applyState` sets `state.doors` from `msg.map.doors`; `T` door tokens (§7.3); `drawGridOnCanvas` doorway pass renders the three states (color + glyph) per tier (§7.2); `state.tool` gains `"door"` + a door-action selection; canvas click: GM door-tool click → `sendDoor`, player door-cell tap → open/close (§7.5/7.6); movement closed-door hint; control-hint copy (§7.7). |
| `app/static/style.css` | **Modify** | `:root` tokens `--door-open/--door-unlocked/--door-locked` (+ explored variants); `.swatch.door-open/.door-unlocked/.door-locked`; `#paint-group` Door tool + sub-button styling; `mode-paint-door` cursor. |
| `tests/test_models.py` / `tests/test_grid.py` | **New / add** | Door state model + round-trip + paint sync (AC1, AC2, AC12). |
| `tests/test_pathfinding.py` | **Update (A1) + new** | Update the 5 doorway-assuming tests (open the door first); add closed-door walk/step/LOS/corner-cut + `find_path` tests (AC4, AC5). |
| `tests/test_visibility.py` | **Update (A1) + new** | Regenerate the W4 literal / update open-door-dependent tests; add closed-door H/E + face-reveal (D5) + open/close S/E/H + monotonicity (AC6, AC7, AC8). |
| `tests/test_session.py` | **Update (A1) + new** | Update `test_move_to_doorway_is_walkable`; add `_on_door` state machine / permissions / occupancy / `map.doors` in payload / `use_map` door reset / awareness + explored unchanged (AC3, AC6–AC9, I3, I5). |
| `tests/test_ws.py` | **Update (A1) + new** | Update `test_no_route_without_override_and_gm_override`; add `door` message wire tests (GM + player, error replies, broadcast `map.doors`) (AC3, AC14). |
| `tests/test_frontend.py` | **Extend + new** | Static checks (legend-doors chips, Door tool + sub-buttons, `T` tokens); harness render tests for the three door states (full + explored tiers), `state.doors`, GM/player click→`door` message (AC10, AC11). |
| `tests/js/harness.js` | **Extend** | Add `doorStateAt` + `state` to `EXPORTS`; extend the stub `ctx` to record the door glyph strokes if needed. |
| `scripts/e2e_proof.py` | **Add step 10** | Door-feature live proof (default locked, unlock/open, walk-through, closed-door awareness + explored tiers, occupancy, player permissions, door-aware S-set re-derivation) (AC14). |
| `scripts/qa_doors.py` | **New** | Standalone live door QA script (mirrors `qa_explored_map.py`): boots the server, drives GM + player over WS, prints a check per door behavior, exits non-zero on failure (AC14). |
| `PROJECT.md` | **Doc note (optional)** | A short addendum pointing to this spec (doors = doorway cells + a state; closed+locked by default; GM unlock; door-aware LOS/movement; awareness + explored unchanged). The frozen external surface (routes, JSON error shapes, WS message set) is **extended additively** by the `door` message + `map.doors` field. |
| `README.md` | **Doc note** | A short feature paragraph (doors are closed+locked by default; the GM unlocks; players open/close unlocked doors; a closed door blocks sight + movement like a wall; awareness + explored map keep working). |

> **Do not touch:** `app/main.py`, `app/imaging.py`, `app/ws.py`,
> `app/awareness.py` (byte-identical), `app/grid.py` (byte-identical sample),
> `app/detection.py` / `app/generation.py` (behavior), the `players[]` /
> `Player.to_dict()` shapes, the `fog` flag, and all **non-A1** existing tests.

---

## 18. Wire protocol recap (for the engineer)

- **One new client→server message:** `{type:"door", x, y, action}`,
  `action ∈ {unlock, lock, open, close}`. Validated in the §4.3 deterministic
  order (ints → bounds → cell-is-doorway → action valid → transition legal →
  role allowed → occupancy); per-client `error` replies on any failure; on
  success, no per-client frame — the `state` broadcast carries the new state.
- **One additive server→client field:** `map.doors` (a JSON object
  `{"<x>,<y>": "L"|"U"|"O"}`) inside every `welcome`/`state`/REST map object
  for a grid that has ≥ 1 doorway (emitted in full whenever doorways exist,
  A9; a missing key ⇒ all locked). **No new server→client broadcast type.**
- **No changes** to `you`, `entities`, `players`, `awareness`, `fog`,
  `you_entity`, `visibility`, `path`, or `error` shapes.
- **Client contract:** store `state.doors` in `applyState` (`{}` default);
  render door state in `drawGridOnCanvas` (per tier); GM sends `door` messages
  from the Door tool; a player sends `door` (open/close) when tapping a
  doorway cell. The client never trusts or predicts door state — it renders
  the broadcast.
