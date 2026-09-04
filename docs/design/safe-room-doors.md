# Design — GM "Safe Room" Doors

**Status:** build-ready spec. New feature: the **GM** may mark any `doorway`
cell as a **safe-room door**. A safe-room door is a **special kind of door**
that (a) is **always unlocked** (it has no lock state), (b) **starts closed**,
and (c) **only `party` (player characters) or `neutral` (neutral NPCs) entities
may step onto / stand on it** — a **`hostile`** (enemy) entity can **never**
path onto, stand on, or be placed on a safe-room door cell, in either state
(open *or* closed). Safe-room doors render as a **green cross** —
unmistakably different from a normal door's red padlock / amber bar / amber
arch.

It **builds directly on the QA-passed Doors feature**
(`docs/design/door-features.md`). It is **additive**: it introduces a new
additive `Grid.safe` field, a new client→server `safe_door` message, a new
additive `map.safe` wire/REST field, a new GM tool, and a new render — while
leaving the normal-door state machine, the cell vocabulary, the awareness
model, and the explored-map algorithm **byte-for-byte compatible**. The entity
restriction and the closed/open sight behavior **fall out of the existing
line-of-sight / walkability code** (extended with an optional `team`
parameter), so no new awareness or visibility logic is invented.

**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins. Where the *user requirement* (quoted in §1.1) is ambiguous,
the decision is pinned in the numbered assumptions (§13) and an AC.

**Code referenced (read, not modified by the spec):** `app/models.py`
(`Grid` + `doors` accessors, `Entity`, `Player`), `app/pathfinding.py`
(`walkable`, `is_valid_step`, `has_line_of_sight`, `_closed_doors`, A*
`find_path`, Bresenham, no-corner-cut), `app/visibility.py` (`visible_cells`
S1/S2, `build_visibility_mask` S/E/H), `app/awareness.py` (`build_awareness`
three-tier), `app/session.py` (`GameSession`: `state_for`, `_visibility_for`,
`_on_paint`, `_on_door`, `_on_move`, `_on_place`, `_on_create_entity`,
`_on_set_team`, `_gm_only`, error-string house style, `_explored`),
`app/server.py` (REST map routes, `_with_doors`), `app/static/app.js`
(`T` tokens, `drawGridOnCanvas`, door art, tool bar, click/paint handling,
`state`), `app/static/index.html` (tool bar, legend),
`app/static/style.css` (tokens, legend swatches), `tests/` (harness +
unittest idioms, `tests/test_door_session.py`), `scripts/e2e_proof.py`,
`scripts/qa_doors.py`.

---

## 1. What changes (summary)

### 1.1 The user requirement (verbatim, source of truth)

> "create a new branch for a new feature, the new feature will be to allow the
> GM to add 'safe room' doors to the map - the safe room will allow only
> neutral npcs or player characters to step onto the safe room door. The door
> will always be unlocked but can be closed, and starts closed. The icon for
> the door should be a green cross."

### 1.2 Change table

| # | Change | Where |
|---|---|---|
| SAFE-1 | New **safe-room door model**: an additive optional `Grid.safe` field — a dict keyed `"<x>,<y>"` (a `doorway` cell) → one of `"C"` (closed) / `"O"` (open). **No lock state** (always unlocked). **Absent/`None` ⇒ no safe doors.** A safe door is a `doorway` cell (cell vocabulary is frozen — **not** a fourth cell type) and is **mutually exclusive** with a normal door in `Grid.doors`. | `app/models.py` (`Grid`) |
| SAFE-2 | **State machine + permissions** (§4). A safe door is **always unlocked** and **starts `C`** (closed) when marked. Only the **GM** can `mark` / `unmark` / `open` / `close` it. A **player** can never act on a safe door (there is no lock to unlock, and it is GM-controlled). | `app/session.py` (`_on_safe_door`) |
| SAFE-3 | **Entity restriction** (§5): only `party` / `neutral` entities may **step onto / stand on** a safe-room door cell. A `hostile` is blocked — an **open** safe door is a wall to a hostile (no path, no stand), and a **closed** safe door is a wall to **everyone** (incl. no-corner-cut). Enforced via an optional `team` parameter on the pathfinding predicates. | `app/pathfinding.py`, `app/session.py` |
| SAFE-4 | **Hostile override guard** (§5.4): the restriction is a **safety rule** — it holds **even under GM `override:true` / `place` / `create_entity`**. A hostile is **never** placed on a safe-room door cell (open or closed). Party/neutral keep the normal `override` (ignore-walls) ability to go onto a **closed** safe door. | `app/session.py` |
| SAFE-5 | **Line of sight / awareness / explored map**: a **closed** safe door **blocks LOS + movement like a wall** (incl. diagonal no-corner-cut); an **open** safe door is **sight-transparent**. This is entity-agnostic, so awareness (three-tier) and the explored-map S/E/H are **unchanged in code** — they inherit the behavior from the door-aware `has_line_of_sight` / `visible_cells`. | `app/pathfinding.py` (via updated `_closed_doors`) |
| SAFE-6 | **Rendering**: a **green cross** icon for safe-room doors, distinct from normal doors (red/amber) and from floor/wall, in both the full and explored (grey) tiers; open vs closed distinguished by the cross + a bar. | `app/static/app.js`, `index.html`, `style.css` |
| SAFE-7 | **Wire protocol**: new client→server `{type:"safe_door", x, y, action}` (action ∈ mark/unmark/open/close, all GM-only); **no new server→client broadcast type** — safe state rides inside the existing `map` payload as an additive `map.safe` object. | `app/session.py`, `app/static/app.js` |
| SAFE-8 | **REST**: additive `safe` field in every `map` object (`GET /api/maps/{id}`, upload, generate, and the session `welcome`/`state`). **No new REST route** — the REST surface stays frozen. | `app/server.py`, `app/models.py` |
| SAFE-9 | **GM UX**: a new bottom-bar **`🛡 Safe door`** tool with **Mark / Unmark / Open / Close** sub-buttons; click a `doorway` cell to apply. Marking a normal door converts it to a safe door (closed); unmarking converts it back to a normal door (open/closed preserved). Painting `floor`/`wall` over a safe door removes it (like a normal door). | `app/static/app.js`, `index.html` |
| SAFE-10 | **Tests + live proof**: safe-door state machine / restriction / LOS / awareness / session / wire / REST / frontend tests, an `e2e_proof.py` step, a new `scripts/qa_safe_doors.py`. | `tests/*`, `scripts/*` |

**What does NOT change:** the cell type vocabulary (`floor` / `wall` /
`doorway` — a safe door is a `doorway` cell + an additive record, **not** a
fourth cell type); the **normal** door state machine, its wire frame
(`{type:"door", x, y, action}` with `unlock/lock/open/close`), and its error
strings (byte-for-byte unchanged — a safe door cell simply is *not* a normal
door, so a `door` message on it gets a clean error, §4.4); the **three-tier
entity awareness** model and `build_awareness`'s logic; the **explored-map**
S/E/H algorithm; the `players[]` / `Player.to_dict()` shapes; the `fog` flag;
the GM exemption (GM view is never filtered); the sample dungeon geometry
(`app/grid.py`); the `path` / `error` frame shapes; all **non-safe** existing
tests (the design is additive — §12).

---

## 2. Behavior statement

Given the user requirement above, the behavior is:

1. **A safe-room door is a special kind of door on a `doorway` cell.** The GM
   marks an existing `doorway` cell as a **safe** door; the cell **becomes a
   safe-room door** and is **no longer a normal door** (the two are mutually
   exclusive — a cell is either a normal door in `Grid.doors` or a safe door
   in `Grid.safe`, never both). The cell type stays `doorway` (the cell
   vocabulary is frozen). (D1, §3.)
2. **It is always unlocked and starts closed.** A freshly marked safe door is
   **`C`** (closed). There is **no lock state** — it is unlocked by definition
   (there is nothing to unlock or lock). The GM can **open** and **close** it;
   it cannot be "locked." (D2, §4.)
3. **Only `party` or `neutral` entities may step onto / stand on it.** A
   **`hostile`** entity **cannot** path onto, stand on, or be placed on the
   cell. This holds **regardless of the door's open/closed state**: when the
   door is **open**, `party`/`neutral` may walk through/onto it but a
   **`hostile` is blocked** (it is a wall to the hostile); when **closed**, it
   is a wall to **everyone** (movement + line of sight). (D3, §5.) The
   restriction is judged by the **entity's `team`** — so a **GM moving a
   hostile** is blocked exactly as if it were the hostile's own move.
4. **Closed safe door = a wall** (for movement and line of sight), exactly
   like a normal closed door and like a `wall` cell (incl. the diagonal
   no-corner-cut rule). **Open safe door = a doorway for party/neutral**
   (walkable + sight-transparent), but **still a wall to a hostile**.
5. **Awareness + explored map still work and are unchanged in code** (they
   reuse `has_line_of_sight` / `visible_cells`, which now see safe doors —
   §6). A closed safe door hides what is behind it (APPROXIMATE within the
   radius, INVISIBLE beyond; explored **H** / **E** in the map), exactly like a
   normal closed door.
6. **The icon is a green cross** (§7): a green **plus/cross** glyph over a
   floor base, clearly distinct from a normal door's red padlock (locked) /
   amber bar (closed-unlocked) / amber arch (open), and from floor/wall and
   from the party-green token.

---

## 3. Data model — safe-room door state on `Grid` (design decision D1)

### 3.1 Storage: a new additive optional field `Grid.safe`, mirroring `Grid.doors`

```python
# app/models.py
SAFE_DOOR_STATES = ("C", "O")   # "C" closed, "O" open  (no lock char — always
                                 # unlocked; "C" is the closed default)
#: Teams allowed to occupy a safe-room door cell (the entity restriction,
#: SAFE-3). "hostile" is the only team excluded.
SAFE_DOOR_TEAMS = frozenset({"party", "neutral"})

@dataclass
class Grid:
    name: str = "Untitled map"
    width: int = 0
    height: int = 0
    cells: list[list[str]] = field(default_factory=list)  # cells[y][x]
    image: str | None = None
    doors: dict[str, str] | None = None   # NORMAL doors (existing, unchanged)
    safe:  dict[str, str] | None = None   # NEW (D1): "<x>,<y>" -> "C"|"O"
```

**Why a separate `safe` field (and not a new value in `doors`):**

- A **safe door** and a **normal door** are **mutually exclusive** on a cell:
  a doorway is *either* a normal door (locked/unlocked/open — in `doors` /
  default `"L"`) *or* a safe door (closed/open — in `safe`). Encoding the
  "safe" flag as an extra value in the *existing* `doors` dict
  (e.g. `"S"`) would (a) **break the frozen normal-door state machine**
  (`"L"|"U"|"O"` — the `test_bad_action` error strings, `DOOR_STATES`, the
  `_on_door` transitions, and the `test_models.py` round-trip/pin assertions
  all assume exactly those three chars) and (b) conflate "is it a door" with
  "which kind of door." Keeping a **separate sparse dict** keeps the normal
  door **byte-for-byte unchanged** and makes safe doors an **exception layer**
  on top — exactly the additive stance the requirement's "allow the GM to add"
  asks for. (This is the single deliberate model choice; it is additive and is
  pinned by AC1/AC12.)
- `safe` is keyed identically to `doors` (`"<x>,<y>"`, comma, no padding) and
  is sparse — only safe-door cells have an entry. `None` (or no key) ⇒ the
  cell is **not** a safe door.
- **A safe door is a `doorway` cell** (cell vocabulary frozen — no fourth cell
  type). `__post_init__` rejects a `safe` key that is not on an in-bounds
  `doorway` cell, exactly mirroring the existing `doors` validation.

**Mutual exclusion (enforced in `__post_init__` and at mutation time):** a key
may not appear in **both** `doors` and `safe` for the same cell; a `safe` entry
is **only** ever written to a cell that is *not* a recorded normal door, and a
normal-door `set_door`/`doors_for_wire` **skips** safe cells. This invariant
keeps "is this doorway a normal door or a safe door?" total and unambiguous.

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
    if self.doors:
        d["doors"] = dict(self.doors)
    # Additive: emit the safe object only when >= 1 safe door is recorded.
    if self.safe:
        d["safe"] = dict(self.safe)
    return d

@classmethod
def from_dict(cls, data: dict[str, Any]) -> "Grid":
    return cls(
        name=data.get("name", "Untitled map"),
        width=int(data["width"]),
        height=int(data["height"]),
        cells=[list(row) for row in data["cells"]],
        image=data.get("image"),
        doors=data.get("doors"),
        safe=data.get("safe"),              # None -> no safe doors
    )
```

**Round-trip invariants (AC1):** `Grid.from_dict(g.to_dict())` preserves every
`safe` state *and* every `doors` state; a `Grid` with `safe=None` serializes
*without* a `safe` key and re-parses to `safe=None`; a `Grid` with one closed
safe door serializes with `"safe": {"5,5": "C"}` and re-parses identically.
The new key is **additive** — old payloads (no `safe`) still parse (to
`safe=None` ⇒ no safe doors). A payload with a `safe` key but no `doors` is
equally valid (normal doors simply all default `"L"` on their doorway cells).

### 3.3 `__post_init__` validation (safe state only on doorway cells, mutually exclusive)

```python
# appended to the existing __post_init__ door validation block:
if self.safe is not None:
    safe_clean: dict[str, str] = {}
    for key, st in self.safe.items():
        if not isinstance(key, str) or "," not in key:
            raise ValueError(f"invalid safe door key {key!r}")
        xs, ys = key.split(",")
        x, y = int(xs), int(ys)
        if st not in SAFE_DOOR_STATES:
            raise ValueError(f"invalid safe door state {st!r} at {key!r}")
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"safe door key {key!r} out of bounds")
        if self.cells[y][x] != "doorway":
            raise ValueError(f"safe door at {key!r} is not on a doorway cell")
        if (self.doors or {}).get(key) is not None:
            raise ValueError(f"door at {key!r} is both normal and safe")
        safe_clean[key] = st
    self.safe = safe_clean
```

- **State only on `doorway` cells** (mirrors I1 for `doors`): a `safe` key on
  a `floor`/`wall` cell is a `ValueError`.
- **Mutual exclusion** (D1): a key present in **both** `doors` and `safe` is a
  `ValueError` — the single source of truth for "a doorway is one kind of
  door."
- **Unknown states / malformed keys / out-of-bounds keys** are rejected at
  construction, so an in-memory `Grid` is always well-formed.

### 3.4 Backward compatibility (no `safe` ⇒ no safe doors)

- **Old payloads without `safe`** (any client built before this feature, a
  stale `maps_registry` entry, or a hand-written `Grid.from_dict`): parse to
  `safe=None` ⇒ **no safe doors** — existing maps behave **exactly as they do
  today** (a doorway is a normal door, closed+locked by default). This is safe
  by default: it adds no restriction to existing data.
- **`safe: {}` (empty object) and `safe: None`** are equivalent (no safe
  doors). The server *emits* the key only when ≥ 1 safe door exists (§8.1).
- **Existing `Grid(...)` constructors** are unaffected: `safe` is a new
  trailing field defaulting to `None`, so `Grid(name, width, height, cells,
  image, doors)` (positional or keyword) still works unchanged.
- **`Session.to_dict` / `Session.from_dict`** are **unchanged** — the `map`
  sub-object carries `safe`.

### 3.5 Derived accessors + paint-sync helper (on `Grid`)

```python
def is_safe_door(self, x: int, y: int) -> bool:
    """True iff (x,y) is a doorway marked as a safe-room door."""
    if self.cells[y][x] != "doorway":
        return False
    return self.safe is not None and f"{x},{y}" in self.safe

def safe_door_state_at(self, x: int, y: int) -> str | None:
    """The safe-door state at (x,y) — "C"|"O" for a safe door, else None."""
    if not self.is_safe_door(x, y):
        return None
    return self.safe[f"{x},{y}"]

def is_safe_door_closed(self, x: int, y: int) -> bool:
    """True iff (x,y) is a CLOSED safe door (state "C")."""
    st = self.safe_door_state_at(x, y)
    return st is not None and st != "O"

def set_safe_door(self, x: int, y: int, state: str) -> None:
    """Set (x,y) to a safe door in `state`; materializes self.safe.

    The cell must be a doorway and must NOT be a recorded normal door
    (the mutual-exclusion invariant, D1). Raises ValueError otherwise."""
    if self.cells[y][x] != "doorway":
        raise ValueError(f"no doorway at ({x},{y})")
    if state not in SAFE_DOOR_STATES:
        raise ValueError(f"invalid safe door state {state!r}")
    key = f"{x},{y}"
    if (self.doors or {}).get(key) is not None:
        raise ValueError(f"door at {key!r} is a normal door, not safe")
    self.safe = dict(self.safe or {})
    self.safe[key] = state

def unmark_safe_door(self, x: int, y: int) -> None:
    """Remove the safe marking from (x,y), reverting it to a NORMAL door.

    Preserves the open/closed intent: a CLOSED safe door ("C") becomes a
    closed+UNLOCKED normal door ("U"); an OPEN safe door ("O") becomes an open
    normal door ("O"). (A fresh safe door is closed+always-unlocked, so its
    natural normal-door reversion is "U".) Raises ValueError if not safe."""
    key = f"{x},{y}"
    if not self.is_safe_door(x, y):
        raise ValueError(f"no safe door at ({x},{y})")
    st = self.safe[key]
    self.safe = dict(self.safe)
    del self.safe[key]
    if not self.safe:
        self.safe = None
    # Reversion to a normal door, preserving open/closed:
    new_state = "O" if st == "O" else "U"
    self.doors = dict(self.doors or {})
    self.doors[key] = new_state
```

`sync_doors_after_cell_set` (the **existing** paint-sync point) already deletes
`doors` state when a cell is repainted `floor`/`wall`; it is **extended in one
line** to also delete the `safe` key (a cell that is no longer a doorway has
no door of either kind). That single shared sync point means **painting
`floor`/`wall` over a safe door removes it** — the "how removing it works"
path (SAFE-9). Marking/unmarking (the GM tool) go through `set_safe_door` /
`unmark_safe_door` and are the **only** other writers of `safe`.

---

## 4. State machine + permissions (design decision D2)

### 4.1 The state machine

**Two states, no lock.** A safe door is **always unlocked**; it is either
**closed (`C`)** or **open (`O`)**. It is marked **closed by default**.

```
        GM mark (on a normal doorway)         GM unmark (revert to normal)
   ┌──────────────────────────────┐           ┌─────────────────────────────┐
   ▼                              │           │                             │
┌────────┐      open      ┌────────┐         │            unmark            │
│  CLOSED │ ─────────────> │  OPEN   │ ───────────────────────────────────────┤
│   (C)   │  (GM ONLY)     │   (O)   │ <──────┘  (normal door, open preserved)
└────────┘                  └────────┘
   ▲                              │
   │        close                 │ close (GM ONLY, occupancy-guarded)
   └──────────────────────────────┘
```

- **`mark`**: turn a `doorway` cell into a safe door, **starting `C`** (GM-only).
- **`unmark`**: revert a safe door to a **normal** door, **preserving
  open/closed** (`C`→`"U"` closed+unlocked, `O`→`"O"` open) (GM-only).
- **`open`**: `C` → `O` (GM-only).
- **`close`**: `O` → `C` (GM-only, **occupancy-guarded** — §4.3).

Every `(state, action)` pair is **totally determined**: exactly one legal
transition or a rejected error (no partial states, I7). The full table:

| Current | `mark` | `unmark` | `open` | `close` |
|---|---|---|---|---|
| *(normal door)* | GM → safe, `C` | "not a safe door" | *(not a safe door)* | *(not a safe door)* |
| **closed (C)** | "already a safe door" | GM → normal door (`U`) | GM → open | "safe door is already closed" |
| **open (O)** | "already a safe door" | GM → normal door (`O`) | "safe door is already open" | GM → closed (occupancy-guarded) |

> **No `lock`/`unlock` on a safe door.** A safe door has **no lock state**
> ("always unlocked"), so `unlock`/`lock` are not `safe_door` actions — a
> `safe_door` frame with `action:"lock"`/`"unlock"` is a **bad action** error
> (§4.3 step 4). (A player can never "unlock" it because there is nothing to
> unlock, and it is GM-controlled regardless.)

### 4.2 Permission matrix

| Action | GM | Player |
|---|---|---|
| `mark` | **allowed** (on a normal doorway) | **`"not allowed"`** (GM-only) |
| `unmark` | **allowed** (on a safe door) | **`"not allowed"`** (GM-only) |
| `open` | **allowed** (safe door must be closed) | **`"not allowed"`** (GM-only) |
| `close` | **allowed** (safe door must be open, occupancy-guarded) | **`"not allowed"`** (GM-only) |

So a **safe-room door is GM-controlled end-to-end**. Players **cannot** open,
close, mark, unmark, lock, or unlock it — the only reason a normal door lets
players open/close is the *unlocked* state, and a safe door has no such
player-interaction surface (it is a deliberate, always-unlocked,
GM-managed entry; pin, A4, §13).

### 4.3 Validation order (deterministic, pinned for tests)

`_on_safe_door` validates in this **exact** order (first failure wins), so the
error for any malformed/illegal request is deterministic and testable (AC3):

1. **GM-only gate first**: if the sender is **not** the GM → `"not allowed"`.
   (Unlike the normal `door` handler, the safe-door handler is **wholly
   GM-gated**, so the role check runs first — there is no player path at all.)
2. `x`/`y` must be ints (reject bools) → else `"x and y must be integers"`.
3. **Bounds** → else `"destination out of bounds"`.
4. **Cell must be a `doorway`** → else `"not a doorway"`.
5. `action` must be one of `mark`/`unmark`/`open`/`close` → else
   `"action must be one of mark/unmark/open/close"`.
6. **Transition legality** (the state machine) → else the state-specific error
   (`"already a safe door"`, `"not a safe door"`, `"safe door is already
   closed"`, `"safe door is already open"`).
7. **`mark` occupancy** → if a token sits on the cell →
   `"cannot mark a safe door with a token on it"` (E1: never convert a
   cell a token is standing on).
8. Apply, broadcast, return `None` (the broadcast carries the new `map.safe`
   / `map.doors`).

**Exact WS error strings (house style, lowercase, em-dash where natural — cf.
`"door is locked"`, `"cannot close a door with a token on it"`):**

| Case | `message` |
|---|---|
| non-GM sends any `safe_door` action | `not allowed` |
| `x`/`y` not int | `x and y must be integers` |
| out of bounds | `destination out of bounds` |
| cell not a doorway | `not a doorway` |
| bad/missing action | `action must be one of mark/unmark/open/close` |
| `mark` on a cell that is already a safe door | `already a safe door` |
| `unmark`/`open`/`close` on a cell that is **not** a safe door | `not a safe door` |
| `open` on a safe door already open | `safe door is already open` |
| `close` on a safe door already closed | `safe door is already closed` |
| `mark` with a token on the cell | `cannot mark a safe door with a token on it` |
| `close` with an entity on the cell (SAFE-2 / I8) | `cannot close a door with a token on it` |

### 4.4 The WS handler + the frozen normal-`door` guard

```python
# app/session.py
SAFE_DOOR_ACTIONS = ("mark", "unmark", "open", "close")

# in handle_message, alongside the "door" dispatch:
if mtype == "safe_door":
    return self._on_safe_door(player, is_gm, msg)

def _on_safe_door(self, player: Player, is_gm: bool,
                  msg: dict[str, Any]) -> dict[str, Any] | None:
    # GM-only, first (the safe-door surface has NO player path — §4.2).
    if not is_gm:
        return {"type": "error", "message": NOT_ALLOWED}
    x = _as_int(msg.get("x")); y = _as_int(msg.get("y"))
    if x is None or y is None:
        return {"type": "error", "message": "x and y must be integers"}
    with self._lock:
        if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
            return {"type": "error", "message": "destination out of bounds"}
        if self.grid.cells[y][x] != "doorway":
            return {"type": "error", "message": "not a doorway"}
        action = msg.get("action")
        if action not in SAFE_DOOR_ACTIONS:
            return {"type": "error",
                    "message": "action must be one of mark/unmark/open/close"}
        is_safe = self.grid.is_safe_door(x, y)
        if action == "mark":
            if is_safe:
                return {"type": "error", "message": "already a safe door"}
            if self._any_entity_at(x, y):
                return {"type": "error",
                        "message": "cannot mark a safe door with a token on it"}
            self.grid.set_safe_door(x, y, "C")          # starts CLOSED
        elif action == "unmark":
            if not is_safe:
                return {"type": "error", "message": "not a safe door"}
            self.grid.unmark_safe_door(x, y)             # revert to normal
        else:  # open / close
            if not is_safe:
                return {"type": "error", "message": "not a safe door"}
            cur = self.grid.safe_door_state_at(x, y)     # "C" | "O"
            if action == "open" and cur == "O":
                return {"type": "error", "message": "safe door is already open"}
            if action == "close" and cur == "C":
                return {"type": "error",
                        "message": "safe door is already closed"}
            if action == "close" and self._any_entity_at(x, y):
                return {"type": "error",
                        "message": "cannot close a door with a token on it"}
            self.grid.set_safe_door(x, y, "O" if action == "open" else "C")
        self._run_b(self._broadcast())
    return None
```

**Success has no per-client reply** — the `state` broadcast carries the new
`map.safe` (+ updated `map.doors` for `mark`/`unmark`), consistent with
`paint`/`set_team`/`set_awareness`. Errors are the usual
`{"type":"error","message":...}` to the offending client.

**The frozen normal-`door` guard.** The **existing** `_on_door` (normal doors)
is **byte-for-byte unchanged** in its state machine and its error strings —
but a `door` message (unlock/lock/open/close) sent on a **safe-door cell** must
not corrupt the safe record. A **safe** doorway is *not* a normal door, so the
normal handler's existing guard — "the cell is a `doorway`" — still passes
(a safe door *is* a `doorway`), which would let a stray `door` message **write
a `doors` entry on a safe cell** and break the mutual-exclusion invariant.
**Therefore one guard line is added at the top of `_on_door`'s in-lock block
(after the cell-is-doorway check):**

```python
if self.grid.is_safe_door(x, y):
    return {"type": "error", "message": "not a normal door"}
```

This is **additive and unambiguous**: it never fires for any cell that is not a
safe door (so **every** existing normal-door test is unaffected), and it keeps
the `doors`/`safe` partition total. (Pinned by AC13b.) `door_state_at` likewise
returns `None` for a safe-door cell (a safe door has no normal-door state),
and `doors_for_wire` **skips** safe cells (§8.1) — so `doors` and `safe` never
overlap on the wire.

---

## 5. Movement, the entity restriction, and the GM override (design decisions D3, D4)

The **core new rule** is that a safe-room door is **restricted by the entity's
team**, not just by its open/closed state. This is implemented by giving the
pathfinding predicates an **optional `team` parameter**: the *blocked-for-this-
entity* set is **the closed doors ∪ (open safe doors, when the team is not
`party`/`neutral`)**. For `team=None` (entity-agnostic callers — line of sight,
visibility, the QA scripts) the open-safe-door term is empty, so behavior is
unchanged.

### 5.1 Team-aware walkability (optional `team` parameter, backward-compatible)

```python
# app/pathfinding.py
from app.models import SAFE_DOOR_TEAMS     # frozenset({"party", "neutral"})

def _open_safe_doors(grid: Grid) -> frozenset[tuple[int, int]]:
    """(x, y) of OPEN safe-room doors (state "O"). Pure over grid."""
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
    """The set of cells this `team` cannot walk on: every CLOSED door/doorway
    (normal or safe), PLUS — when `team` is NOT in SAFE_DOOR_TEAMS (i.e. a
    hostile) — every OPEN safe door. `team=None` => the plain closed set
    (entity-agnostic)."""
    closed = _closed_doors(grid) if doors is None else doors
    if team is None or team in SAFE_DOOR_TEAMS:
        return closed
    return closed | _open_safe_doors(grid)      # a hostile can't use open safes
```

**Why `_closed_doors` must be safe-aware.** The existing `_closed_doors(grid)`
currently returns *every* doorway that is not `"O"` in `grid.doors`. After
SAFE-1, a **safe** doorway is **not** in `grid.doors` (mutual exclusion), so
without a change a closed safe door would be seen as *open/walkable* — wrong.
`_closed_doors` is extended to also include safe doorways whose `safe` state is
`"C"`:

```python
def _closed_doors(grid: Grid) -> frozenset[tuple[int, int]]:
    doors = grid.doors or {}
    safe = grid.safe or {}
    closed = set()
    for y in range(grid.height):
        for x in range(grid.width):
            if grid.cells[y][x] != "doorway":
                continue
            key = f"{x},{y}"
            if key in safe:
                if safe[key] != "O":      # a CLOSED safe door blocks like a wall
                    closed.add((x, y))
            elif doors.get(key) != "O":   # a CLOSED normal door blocks (unchanged)
                closed.add((x, y))
    return frozenset(closed)
```

For a grid with **no** safe doors, this is **byte-for-byte identical** to today
(the `key in safe` branch never fires) — so **every** existing
pathfinding/visibility/awareness/session/WS test keeps passing (AC16).

`walkable` / `is_valid_step` / `find_path` gain the **optional `team`**
parameter (default `None`), threading it through `_blocked_for`:

```python
def walkable(grid, x, y, doors=None, team: str | None = None) -> bool:
    if not _in_bounds(grid, x, y):
        return False
    if grid.cells[y][x] not in WALKABLE_CELLS:
        return False
    if (x, y) in _blocked_for(grid, doors, team):
        return False                       # closed door, or open safe for a hostile
    return True

# is_valid_step(grid, a, b, doors=None, team=None) -> walkable(b, team=team)
#   AND both diagonal elbows walkable(team=team)  — the team applies to the
#   target AND the elbows, so for a hostile an OPEN safe door blocks a
#   diagonal the same way a wall does (no "slipping past" the safe door
#   diagonally). no-corner-cut preserved for all teams.
#
# find_path(grid, start, goal, doors=None, team=None) -> A* over the
#   team-aware predicates; computes the blocked set ONCE (constant factor).
#
# has_line_of_sight(grid, a, b, doors=None)  — UNCHANGED signature; it is
#   ENTITY-AGNOSTIC (LOS has no "team"). A CLOSED safe door blocks LOS (via the
#   updated _closed_doors); an OPEN safe door is sight-transparent.
```

**Key semantics (AC4, AC5, AC6):**

- **Closed safe door** is in the blocked set for **every** team (it is in
  `_closed_doors`), so it blocks movement and LOS **like a wall** — incl. the
  diagonal no-corner-cut rule (`is_valid_step`/`has_line_of_sight` treat it
  exactly as a closed normal door / wall).
- **Open safe door + `team` ∈ {party, neutral}**: NOT in the blocked set →
  **walkable** and LOS-transparent (like today's open doorway).
- **Open safe door + `team` == "hostile"**: in the blocked set (via
  `_open_safe_doors`) → **not walkable**, and LOS is unaffected (LOS has no
  team) — so a hostile simply **cannot path onto/through** the open safe door
  (its A* treats the cell as a wall); the cell is still *seen* (LOS) by
  everyone, but a hostile token can never *be* on it.
- **`team=None`** (entity-agnostic): identical to today — a safe door behaves
  exactly as its open/closed state dictates (open = walkable-for-the-purpose-of
  LOS/visibility). This is why `has_line_of_sight`, `visible_cells`, and the
  QA re-derivation helpers need **no team** and are unchanged.

### 5.2 A* and the GM's "ignore walls" override — the hostile exception (D4)

- **Normal pathfinding** (`find_path`) is **team-aware** because the session
  passes `team=entity.team` (§5.3). So a **hostile** whose only route would
  cross an **open** safe door gets `None` (→ `"no route — wall in the way"`);
  a **party/neutral** gets a route through it.
- **GM `override:true` (ignore walls)** and **GM `place` / `create_entity`**
  bypass walkability today. **Decision (D4, per the task's lean):** the safe-
  room restriction is a **deliberate safety rule** and **holds even under
  override** — but **only for the hostile team**. Concretely:
  - A **hostile** may **never** be moved (override), placed, or created onto a
    safe-room door cell, **in either state** (open or closed). These are
    rejected with `"cannot place a hostile on a safe room door"`. This is
    **intentionally different** from a normal *closed* door (where override
    *does* bypass — door-features A3); the safe rule is stronger on purpose
    ("the safe room only lets party/neutral in"), and it is pinned by AC7.
  - A **party/neutral** entity keeps the normal `override`/`place`/`create`
    behavior: it may be teleported onto a **closed** safe door (ignore-walls),
    exactly like a closed normal door (door-features A3). There is no
    party/neutral restriction to override past.

> **Assumption A4:** "the GM may **open/close** a safe door; players cannot"
> and "a hostile **cannot** occupy a safe-room door even under override" are
> the recommended interpretations. The hostile override guard is the single
> place this feature **differs** from the normal closed-door override
> (door-features A3); it is the safety rule and is pinned by AC7 + an AC.

### 5.3 Session wiring (the restriction is on the entity's TEAM)

`_on_move` passes the **moving entity's team** to `find_path`, so the
restriction is judged by the **entity's team** — a **GM moving a hostile** is
blocked exactly as the hostile's own move would be:

```python
# _on_move (the A* branch; the override branch is guarded separately, §5.2):
if override:
    # GM "ignore walls": blocked for a HOSTILE onto a safe door (D4).
    if self.grid.is_safe_door(x, y) and entity.team == "hostile":
        return {"type": "error",
                "message": "cannot place a hostile on a safe room door"}
    entity.x, entity.y = x, y
    path = [{"x": x, "y": y}]
else:
    path = find_path(self.grid, (entity.x, entity.y), (x, y),
                     team=entity.team)      # team-aware walkability
    if path is None:
        return {"type": "error", "message": NO_ROUTE}
    entity.x, entity.y = x, y
    path = [{"x": px, "y": py} for (px, py) in path]
```

`_on_place` and `_on_create_entity` get the **same one-line hostile guard**
(reject a hostile onto a safe-door cell, open or closed):

```python
# in _on_place and _on_create_entity, after bounds check, before mutating:
if self.grid.is_safe_door(x, y) and (entity.team if <entity> else team) == "hostile":
    return {"type": "error", "message": "cannot place a hostile on a safe room door"}
```

> The restriction is therefore **not bypassable by the GM for hostiles** via
> any of the four "put a token on a cell" paths (`move`+override, `place`,
> `create_entity`, or `set_team` — the last is covered by §5.4 E4). AC7 pins it.

### 5.4 Edge case — `set_team` to hostile while on a safe door (E4)

A **party/neutral** token may legally stand on an **open** safe door (§5). If
the GM then `set_team`s it to **`hostile`** while it stands on the safe door,
the invariant "no hostile on a safe door" is broken. **Decision: `set_team` to
`hostile` is rejected when the entity is standing on a safe-room door cell** →
`"cannot place a hostile on a safe room door"`. This is the last path by which
a hostile could end up on a safe cell, and guarding it keeps the invariant
total (I4). Setting *from* hostile to party/neutral while off the cell is
unaffected. (A hostile can only be *on* a safe cell via this degenerate
sequence, since create/place/move-override are all guarded; the `set_team`
guard makes "no hostile on a safe cell" **invariant under every mutation**.)

---

## 6. Awareness + explored map (design decisions — the "unchanged, but
inherited" sections)

The safe-room feature changes **nothing** in `app/awareness.py` (byte-
identical, I6) or in the `visible_cells` / `build_visibility_mask` **S/E/H
logic**. It changes only the **line-of-sight predicate they consume**
(`has_line_of_sight`, via the updated `_closed_doors`, §5.1). Because that
predicate is **entity-agnostic** (LOS has no "team"), the new safe-door
behavior **falls out of the existing code** with no new awareness or
visibility logic — exactly as the door-features spec pinned for normal doors.
This section pins that claim so implementers do not "fix" awareness.

### 6.1 Awareness (entity three-tier) — UNCHANGED (byte-identical `awareness.py`)

`build_awareness(viewer, entities, grid)` is **not modified**. For a **player**
anchored at own token `O`, per other entity `E`:

- **FULL** iff `has_line_of_sight(grid, O, E)` — which now (via §5.1) treats a
  **closed safe door as a wall** and an **open safe door as transparent**. So:
  - `E` behind a **closed** safe door (no LOS) → **not** FULL.
  - `E` behind an **open** safe door with an otherwise clear line → **FULL**
    (named token, color, label) — *regardless of `E`'s team*, because LOS is
    team-agnostic (a player can *see* a hostile through an open safe door; they
    just can't walk there).
- **APPROXIMATE** iff not FULL and `chebyshev(O, E) <= awareness_radius`: a
  coarse 2×2-block grey "?" with no identity (the safe door blocks LOS, not the
  sensor).
- **INVISIBLE** iff not FULL and beyond the radius: absent.

**The "do not fix awareness" pin:** a **hostile** behind a **closed** safe
door shows as **APPROXIMATE** (grey "?") within the radius / **INVISIBLE**
beyond; a hostile behind an **open** safe door shows **FULL** (named/labeled).
This is **exactly** what `build_awareness` + the safe-aware
`has_line_of_sight` already produce. **Implementers must not add
safe-door/`team` special-casing to `app/awareness.py`** — awareness is
unchanged in behavior *and* output modulo the intended LOS blocking (AC8).
The **GM** is exempt (no LOS/distance filtering) — the GM sees every entity
regardless of safe-door state (I3).

### 6.2 Explored map (S/E/H) — UNCHANGED, verified against `app/visibility.py`

`visible_cells(grid, pos)` and `build_visibility_mask` are **not modified in
their S/E/H logic**. They iterate cells and call `has_line_of_sight` (now
safe-aware via `_closed_doors`). Verifying the claim:

**(a) A closed safe door blocks sight, so its far side is H (never explored) /
E (explored before)** — exactly like an out-of-line-of-sight area. A floor
cell behind a closed safe door: every Bresenham line crosses the closed safe-
door cell (now a blocker) → not S → **H** if never seen, **E (greyed)** if
seen. It falls out of LOS blocking with **no new code**.

**(b) A closed safe door's own cell is revealed by the wall-face rule (D5).**
A closed safe door is a `doorway` cell, but (a) it is **not** in the walkable
set for S1, and (b) it **is** in `_closed_doors` — so the **existing** S2
wall-face branch in `visible_cells` (which already treats a *closed door* as a
wall for face-reveal, door-features §6.2b) **also covers it** with **no new
code**: a closed safe door's cell is S iff one of its four in-bounds walkable
(neighbouring) cells is in sight. So the player **sees the closed safe door's
face** (the green cross) when standing in front of it. (AC8.)

**(c) An OPEN safe door is fully transparent to sight and to the map** for
*every* viewer (LOS + S/E/H are team-agnostic): it reveals the far side as S
exactly like an open normal door, and it does **not** reveal any entity
restriction (the restriction is a *movement/occupancy* rule, not a *sight*
rule — a player can see a hostile beyond an open safe door, they just cannot
walk there).

**(d) No awareness/visibility code is rewritten.** The only server changes
are §5.1 (safe-aware `_closed_doors` + team-aware `walkable`/`is_valid_step`/
`find_path`) and the §5.3/§5.4 session guards. `app/awareness.py` and
`app/visibility.py` are **byte-unchanged**. Hard constraint (AC8/AC16).

---

## 7. Frontend — rendering the safe-room door as a GREEN CROSS (D5, CR: "green cross")

### 7.1 Palette

A safe door must be **unmistakably different** from a normal door (red
padlock / amber bar / amber arch) and from floor (`#efe9dc`) / wall
(`#3b4252`). **Decision: a green cross (plus sign) glyph, in a bright "safe"
green, over a floor base with a green border.**

**Base green — `#3ddc84` (a bright mint / spring green), chosen over
`#2ecc71` (flat UI green).** Justification of distinctness:

- **From floor `#efe9dc` and wall `#3b4252`** — obviously (green vs. cream /
  blue-grey).
- **From the *party* awareness green `#2f9e44` (the `T.ally` token green,
  "friend"):** `#3ddc84` is a much **brighter, more saturated, minty** green
  than the darker forest-green `#2f9e44` used for *tokens*. But more
  importantly the safe door is a **cross (plus-sign) glyph** on a *cell*, while
  a party token is a **filled circle** — so the two are distinguished by
  **shape as well as by exact green**. (If a stricter hue gap is desired at
  build time, `#3ddc84` is the pick; the glyph shape is the primary
  discriminator, so the two will never be confused in practice.)
- **From the normal door colors** (`#d97706` amber, `#f59f00` lighter amber,
  `#e03131` red): green is a different hue family entirely, and the cross is a
  different glyph from arch/bar/padlock.

The cross glyph is drawn **over the floor base** (same as a normal door — a
door is a floor-based object, not a wall, so it gets the grid line and no
hatch), with a **green border** around the cell and the **cross** centered.

**Full-detail tier ("S" / GM / preview):**

| State | Fill (cell base) | Border + glyph |
|---|---|---|
| **open (`O`)** | floor `#efe9dc` | border **green `#3ddc84`** + a **centered green cross (plus sign)** — the "open / can step on" safe entry |
| **closed (`C`)** | floor `#efe9dc` | border **green `#3ddc84`** + the **green cross + a horizontal bar across the middle** (the bar reads "closed / shut" — same "bar = closed" idiom as a normal closed door, but in green) |

So **open vs closed** is distinguished by **the bar** (present when closed,
absent when open), and **safe vs normal** is distinguished by **green + cross**
vs. **red/amber + padlock/bar/arch** — i.e. by both **hue** and **shape**.

**Explored tier ("E", greyed memory) — the same green cross recolored to a
desaturated sage-green** (consistent with the explored grey family, floor
`#6b7280`):

| State | Fill | Border + glyph (desaturated) |
|---|---|---|
| **open (`O`)** | `#6b7280` | `#8fae9c` (sage green) + cross |
| **closed (`C`)** | `#6b7280` | `#8fae9c` + cross + bar `#7d9385` |

**Grid-line / hatch treatment:** a safe-door cell (both states) is
**floor-based** (fill = floor color) → it gets its tier's **grid line** (full
`#d9d1bd` at S/GM, dimmed `rgba(217,209,189,0.3)` at E) and **no wall hatch** —
it is a door, not a wall. Only the per-cell art changes: pick the
border/glyph color from the **safe-door state** and draw the **cross** (plus a
bar when closed) instead of a normal door's arch/bar/padlock.

### 7.2 How `drawGridOnCanvas` changes

The existing **door pass** (`for ... if g.cells[y][x] !== "doorway" continue;
... doorColor/drawDoorGlyph ...`) is extended to check the **kind** first:

- New `state.safe` = the `map.safe` object (from `applyState`; `{}` when
  absent). Helpers: `isSafeDoor(x, y)` (cell is `doorway` AND in `state.safe`)
  and `safeDoorStateAt(x, y)` → `"C"|"O"` (default `"C"`), mirroring the server
  `Grid` accessors.
- In the doorway pass: **if `isSafeDoor(x, y)`** → draw the **safe-door art**
  (green border + green cross, plus a bar when closed, in the tier's palette),
  **skip** the normal-door branch. **Else** → the existing normal-door branch
  (amber/red arch/bar/padlock) **byte-for-byte unchanged**.
- The **GM and preview passes** (no visibility matrix) render safe doors with
  the full-tier green palette (the GM always sees the true safe-door state);
  the **player live pass** renders the tier's (S/E) green palette. The S/E/H
  matrix tiering is unchanged — only the green cross within S/E is
  state-driven (bar present iff closed).
- A **closed** safe door renders as a **closed safe-door cell** (floor base +
  green border + green cross + bar) — visually a *door*, distinct from the
  hatched wall and from the open safe door.

### 7.3 `state` + `T` tokens

- `state.safe` (client render data; `{}` default) + `isSafeDoor` +
  `safeDoorStateAt`. `applyState` sets `state.safe = validateSafe(msg.map ?
  msg.map.safe : undefined)` (additive; absent ⇒ `{}` ⇒ no safe doors;
  malformed ⇒ `{}`, defensive).
- New `T` tokens (alongside the existing door palette):
  `safeOpen: "#3ddc84"`, `safeClosed: "#3ddc84"`,
  `exploredSafeOpen: "#8fae9c"`, `exploredSafeClosed: "#8fae9c"` (the bar uses
  `#7d9385` when closed, E tier). (Full-tier open/closed share the same green;
  the **bar** distinguishes them. The explored variants are the desaturated
  sage family.)

### 7.4 Legend chips (GM and player)

Safe doors concern **both** roles (the GM edits them; a player must read the
icon and know they can walk on it while enemies cannot), so the chip is
**visible to everyone** (like the door chips — not gated by `body.is-gm`). In
`#legend`, after the three door-state chips, add (class `legend-safe`):

- `<span class="legend-sep legend-safe">|</span>`
- `<span class="legend-chip legend-safe"><i class="swatch safe-door"></i>safe door (green cross)</span>`

CSS: `.swatch.safe-door { background: var(--floor); border: 2px solid var(--safe-open);
   position: relative; }` with a small green cross pseudo-element (or a tiny
inline SVG) so the legend swatch shows the cross, not just a green box. The
`--safe-open` / `--explored-safe-open` `:root` tokens mirror `T`.

### 7.5 GM controls (D5) — a **`🛡 Safe door`** tool with sub-buttons

The bottom `#paint-group` (GM-only) gains a **`Safe door`** tool **plus** a
compact **action sub-row**, consistent with the existing select/floor/wall/
doorway/Door bar and its sub-button pattern:

- New tool `"safeDoor"` in `state.tool`; a `data-tool="safeDoor"` button
  `🛡 Safe door` in `#paint-group` (GM-only, like the other paint tools).
  Selecting it arms safe-door editing.
- When armed, a small **action sub-row** appears: **Mark**, **Unmark**,
  **Open**, **Close** — each a `data-safe-action` button (mirroring the Door
  tool's `data-door-action` row). The GM picks an action, then **clicks a
  `doorway` cell** to apply it (same click-to-apply ergonomics as paint).
  - **Mark** → `{type:"safe_door", x, y, action:"mark"}` — converts the
    (normal) doorway cell to a **safe door, closed**.
  - **Unmark** → `action:"unmark"` — converts a safe door back to a **normal
    door** (open/closed preserved: `C`→`U`, `O`→`O`). This is the
    "remove / toggle" path (a dedicated **Unmark** button is clearer than a
    "toggle" since the state machine is directional).
  - **Open** / **Close** → `action:"open"` / `action:"close"` — GM opens/closes
    the safe door (GM-only; the door is always unlocked so there is no
    Lock/Unlock).
- Client `sendSafeDoor(x, y, action)` → `wsSend({type:"safe_door", x, y,
  action})`. The server is authoritative (state reconciles from the broadcast;
  **no optimistic safe-door mutation** — safe doors are a GM action, low
  frequency). A bad cell/state gets the server's error toast (no client gating
  beyond the tool being GM-only).
- **Toggle/convert semantics (clicking a doorway vs floor/wall):**
  - **Mark** on a `doorway` that is a **normal** door → converts it to a safe
    door (closed). **Mark** on a cell that is **already a safe door** → server
    `"already a safe door"`.
  - **Unmark** on a **safe** door → reverts to a normal door (preserving
    open/closed). **Unmark** on a normal door → `"not a safe door"`.
  - Clicking a **`floor`/`wall`** cell → server `"not a doorway"` (a safe door
    must live on a `doorway`). To add a safe door to a non-doorway cell, the GM
    first paints it a `doorway` (existing tool), then **Mark**s it.
  - **Removing a safe door = Unmark** (reverts to a normal door) **or**
    painting `floor`/`wall` over it (deletes the safe record, §3.5) — two
    paths, consistent with how a normal door is removed by painting over it.
- A locked-style hint: GM, Safe door tool: `"Click a doorway to <action>"`.

### 7.6 Player interaction — tap is a no-op on a safe door

A **player** (no door tool) taps a cell. For a **safe-door cell**, the client
treats the tap as a **no-op** (it does **not** send a door frame and does
**not** try to move there — a closed safe door is not walkable and an open one
is a *destination*, not a *door-action* target, for a player). The player
walks onto an **open** safe door by clicking the **floor cell on the other
side** (the normal movement path), and they may *see* the cross (GM-managed).
This keeps the existing normal-door tap logic (§7.6 of door-features)
**unchanged**: the player tap branch checks `doorStateAt` (normal door) and is
gated on the cell **not** being a safe door — so a safe cell falls through to
the no-op/selection handling without ever emitting a normal `door` frame.
(AC11d.)

### 7.7 Hints

- GM, Safe door tool: `"Click a doorway to <action>"`.
- Player, hovering/clicking a **closed** safe door cell: `"That safe door is
  closed — the GM controls it"` (client-side, since the client knows the
  safe-door state). For an **open** safe door: the player may walk onto it
  (party/neutral), so no blocking hint.
- Movement: a **hostile** token (GM-controlled) whose route is blocked by an
  **open** safe door → the server returns the existing
  `"no route — wall in the way"` (a hostile cannot use an open safe door —
  it is a wall to them); the "Move anyway" (override) toast path is suppressed
  by the §5.2 hostile guard (a hostile override onto a safe door is rejected,
  not teleported) — so the GM gets `"cannot place a hostile on a safe room
  door"` instead (AC7).

---

## 8. Wire protocol (design decision D6)

### 8.1 The `safe` field (additive, inside `map`) + the `doors` partition

- **Server→client:** safe state is **not** a new broadcast type. It rides
  inside the **existing `map` object** of every `welcome`/`state` payload (and
  the REST map responses, §8.2) as an additive **`safe`** field:
  - `map.safe` is a JSON **object** `{"<x>,<y>": "C"|"O", ...}` containing
    **every safe-door cell and its current state** (emitted in full whenever
    the grid has ≥ 1 safe door, so the wire is unambiguous — a client can tell
    open from closed, and a safe door from a normal door, with no ambiguity).
  - **When `map.safe` is absent** (no safe doors) ⇒ the client treats the grid
    as having **no safe doors** (all doorways are normal doors). The client
    defensively defaults a missing `safe` to `{}`.
  - **The `doors` object now EXCLUDES safe-door cells** (`doors_for_wire`
    skips any cell in `grid.safe`), so `doors` ∪ `safe` covers **every**
    `doorway` cell exactly once and the two never overlap on the wire. A
    client reading `doors` sees only normal doors; a client reading `safe`
    sees only safe doors. For a grid with no safe doors, `doors` is
    **byte-identical** to today (nothing is skipped) — so existing door
    consumers and tests are unaffected.
- **No new server→client message type.** `welcome`/`state`/`path`/`error`
  shapes are unchanged except the additive `map.safe` key and the (possibly
  smaller) `map.doors`. The `visibility` (explored) field, `players[]`,
  `entities[]`, `you_entity`, `awareness`, and `fog` are all **byte-identical**
  in shape to today.
- The client stores `state.safe` in `applyState` and uses `isSafeDoor` /
  `safeDoorStateAt` for rendering + the GM tool. A malformed `safe` (wrong
  charset / non-object / bad key) is treated as `{}` — defensive, never
  crashes (mirrors `validateDoors`).

### 8.2 REST (D7) — additive `safe` in every `map` object

The REST surface is **frozen except** for the additive `safe` key in the map
object (and the additive `safe`-aware partition of `doors`):

- `GET /api/maps/{id}` → the map detail object **gains `"safe": {...}`**
  whenever the grid has ≥ 1 safe door (and `"doors"` **excludes** those
  cells). **Existing pinned keys are unchanged** (`id`,`name`,`width`,
  `height`,`cells`,`image`,`entities`,`players`,`doors`); `safe` is additive.
  For a grid with **no** safe doors, the response is **byte-identical** to
  today (no `safe` key, `doors` unchanged) — so the existing REST key-set
  assertions (which pin the exact key set for normal maps) keep passing.
- `POST /api/maps/upload` / `POST /api/maps/generate` responses → **gain
  `"safe": {...}` only if safe doors were painted**; a **fresh** upload or
  generated map has **no** safe doors (they are GM-authored, not detected), so
  these responses are **unchanged** for fresh maps (no `safe` key). (A safe
  door can only appear after a GM `safe_door` `mark`, which is WS-only;
  REST never *creates* one.)
- `POST /api/maps/{id}/paint` → response shape **unchanged**
  (`{"ok":true,"x","y","cell_type"}`); painting `floor`/`wall` over a safe
  door deletes the safe record server-side (shared sync point, §3.5), but the
  *response* does not echo safe state (frozen). A subsequent
  `GET /api/maps/{id}` reflects it.
- **No new REST route** for safe doors (safe-door actions are WS-only, like the
  other GM tools). The `safe` field is **additive and optional to parse** — a
  client that ignores it still works (it just treats safe doors as normal
  doorways, which is the *walkable-when-open* default — the safe semantics
  only matter on the authoritative server, which is the source of truth).

Because REST and WS both read the **same `Grid` object** from
`maps_registry`/the session (object identity shared), safe-door state is
consistent across both surfaces with no extra synchronization.

### 8.3 The `safe_door` message (client→server)

```json
{ "type": "safe_door", "x": 5, "y": 5, "action": "mark" }
```

- `action` ∈ `mark` | `unmark` | `open` | `close`.
- Sent **only by the GM** (the server rejects non-GM with `"not allowed"`
  first). The server enforces the full permission matrix + state machine (§4)
  and replies per-client error on any failure; on success it applies the state
  and broadcasts the `state` (which carries the new `map.safe` / `map.doors`).
  No per-client success frame (house style, cf. `paint`).
- Validation order is the deterministic sequence of §4.3 (AC3).
- The **normal** `{type:"door", x, y, action}` frame is **unchanged**; on a
  safe-door cell it is rejected with `"not a normal door"` (§4.4) — so the two
  surfaces never interfere.

---

## 9. GM UX to ADD safe-room doors (design decision D5, recapped)

> Full detail in §7.5. The interaction, in one place:

- **New bottom-bar tool `🛡 Safe door`** (`data-tool="safeDoor"`, GM-only), with
  an **action sub-row**: **Mark / Unmark / Open / Close**
  (`data-safe-action`). This is the recommended form — it reuses the exact
  Door-tool idiom (a tool button + a revealed action sub-row + click-to-apply),
  which is the established pattern in `app.js`/`index.html`.
- **What clicking does:**
  - On a **`doorway` cell** with **Mark**: a normal door becomes a **safe door,
    closed** (`"C"`); an already-safe cell → `"already a safe door"`.
  - On a **`doorway` cell** with **Unmark**: a safe door reverts to a **normal
    door** (closed→`"U"`, open→`"O"`); a normal cell → `"not a safe door"`.
  - On a **`doorway` cell** with **Open**/**Close**: opens/closes the safe door
    (GM-only; occupancy-guarded on close).
  - On a **`floor`/`wall` cell**: `"not a doorway"` (a safe door lives on a
    `doorway`; paint a doorway first, then Mark).
- **Toggle/convert:** **Mark** converts a normal door **→** a safe door;
  **Unmark** converts a safe door **→** a normal door (the "normal door ↔ safe
  door" toggle, directional and explicit).
- **Removing it:** **Unmark** (revert to normal) or **paint `floor`/`wall`**
  over it (delete the record) — two consistent paths, mirroring how a normal
  door is removed by painting over it.

---

## 10. Invariants (all AC-tested)

- **I1' — Safe state only on doorway cells.** `Grid.safe` (when not `None`)
  contains a key **only** for an in-bounds `doorway` cell, and every value is
  in `{"C","O"}`. `__post_init__` enforces this.
- **I1 — Safe/normal mutual exclusion.** A `doorway` cell appears in `Grid.doors`
  **or** `Grid.safe`, **never both** (enforced at construction and at
  `mark`/`unmark`/`set_door`; `doors_for_wire` skips safe cells). (D1.)
- **I2 — Default is closed, no lock.** A safe door, when marked, is **`C`**
  (closed) and has **no** lock state (always unlocked). There is no "unlocked
  by default" question — there is no lock.
- **I3 — The GM view is never door-filtered.** The GM's `state` payload carries
  the full `entities` list and the full `map.safe`/`map.doors` regardless of
  safe-door state; the GM's awareness is unfiltered (existing rule).
- **I4 — A closed safe door is a wall; an open safe door is restricted.** For
  every (grid, state, team) triple: `walkable(closed safe door, any team) ==
  False`; `walkable(open safe door, party/neutral) == True`;
  `walkable(open safe door, hostile) == False`; a hostile can **never** occupy
  a safe-door cell in either state (I4b — invariant under move-override,
  place, create, and set_team, §5.2/§5.4).
- **I5 — Every `map` payload carries the full door partition.** Every
  `welcome`/`state`/REST map object for a grid with ≥ 1 doorway carries
  `map.doors` (normal doors) **and** `map.safe` (safe doors, when any),
  disjoint and jointly covering all doorways. (AC1, AC10.)
- **I6 — Awareness + explored logic are unchanged.** `app/awareness.py` and
  `app/visibility.py` are **byte-identical**; the S/E/H and three-tier logic is
  unchanged (only the safe-aware `_closed_doors` they consume changed, plus the
  team-aware `walkable`/`find_path`). (AC8, AC16.)
- **I7 — Atomic state machine.** A safe-door action either fully applies one
  legal transition or is fully rejected; no partial state; the
  `(state, action, role)` → result mapping is total and deterministic. (AC3.)
- **I8 — No entity left on a closed safe door.** A `close` is rejected when any
  entity occupies the safe-door cell (occupancy guard); `mark` is rejected when
  a token is on the cell. So a **closed** safe door never has an entity on it
  after a successful mutation. (An **open** safe door may be occupied — only
  by party/neutral, I4.) (AC9.)
- **I9 — Monotonic explored memory.** Within a map, a cell never goes S/E → H
  (preserved; safe doors only *shrink* the S-set, moving cells to E, never to
  H, unless never seen). (AC8.)
- **I10 — Server-authoritative.** The server never trusts a client-claimed safe
  state; safe state is only ever changed by a validated `safe_door` message (or
  a GM paint that deletes it), and the broadcast is the source of truth.

---

## 11. Edge cases

| # | Case | Behavior |
|---|---|---|
| E1 | **Mark a cell with a token on it.** GM `mark`s a doorway that currently has a token standing on it → **REJECTED** `"cannot mark a safe door with a token on it"` (never convert a cell someone is standing on). (I8.) AC9. |
| E2 | **Hostile already on the cell when the GM `mark`s it.** Handled by E1's occupancy guard: a token (of any team) on the cell blocks `mark`. So the GM cannot make a cell a safe door while a hostile (or anyone) is on it — the invariant "no hostile on a safe cell" can never be violated by `mark`. |
| E3 | **Hostile pathing that would require the safe door.** An **open** safe door is in the hostile's blocked set (§5.1) → A* routes **around** it (or returns `None` if it's the only route → `"no route — wall in the way"`). A **closed** safe door is a wall to everyone. No crash, no stuck entity. AC6. |
| E4 | **GM `set_team`s a party/neutral token to hostile while on an open safe door.** REJECTED `"cannot place a hostile on a safe room door"` (§5.4) — the last path to put a hostile on a safe cell; the invariant stays total. AC7. |
| E5 | **`safe_door` on a `floor`/`wall` cell.** → `"not a doorway"` (§4.3 step 4). AC3. |
| E6 | **`safe_door` out of bounds.** → `"destination out of bounds"` (before the cell check). AC3. |
| E7 | **`safe_door` with bad/missing `action`.** → `"action must be one of mark/unmark/open/close"`. AC3. |
| E8 | **Player sends any `safe_door` action.** → `"not allowed"` (GM-only, checked first). AC3. |
| E9 | **A `door` (normal) message on a safe-door cell.** → `"not a normal door"` (§4.4 guard); the safe record is untouched (mutual exclusion preserved). AC13b. |
| E10 | **`use_map` swap while safe doors are set.** `_on_use_map` swaps `self.grid` to the target `Grid` object (same object-identity as door-features E3); the new grid **carries its own `safe`** (from `maps_registry`), so **safe-door state resets with the new grid** (each map's safe doors are its own). No safe-door-specific code needed (the grid object is swapped wholesale). AC14. |
| E11 | **GM `place`/`override` puts a PARTY/NEUTRAL token ON a closed safe door.** ALLOWED (ignore-walls exception, like a closed normal door — door-features A3). The token sits on a non-walkable (closed) cell and cannot path away until the GM opens it — the same degenerate "token on a wall via override" case; no crash; the GM resolves it. Documented, not a normal flow. AC7 (contrast: a HOSTILE here is REJECTED). |
| E12 | **Two GM actions race on the same safe door.** All actions run under the session `RLock`; the second sees the first's new state (e.g. GM `mark` then GM `unmark` serialize; the second is either legal or the "already/not a …" error). Deterministic. (I7.) |
| E13 | **A safe door at a map border / a doorway with no wall neighbours.** Safe-door state and behavior are independent of the doorway *heuristic* — any `doorway` cell (however created) can be marked safe. A closed border safe door blocks sight/movement like any closed safe door. No special-casing. |
| E14 | **A hostile *sees* beyond an open safe door.** Yes — sight is team-agnostic: a player (or the GM) can **see** a hostile through an **open** safe door (FULL/LOS); the restriction is on *occupancy/movement*, not sight. A hostile beyond an open safe door renders normally in awareness; a hostile *would-be-on* the open safe cell simply never exists (it can't get there). §6.1, §6.2. |

> **Assumption A4 (pinning, with AC7):** "the GM opens/closes a safe door;
> players cannot" and "a hostile cannot occupy a safe-room door **even under
> override**" — the hostile override guard is the single deliberate difference
> from the normal closed-door override (door-features A3), because the safe
> room is a *deliberate safety rule* ("only party/neutral may step on it").

---

## 12. Explicit non-changes

- **Cell type vocabulary:** `floor`/`wall`/`doorway` **unchanged** — a safe
  door is a `doorway` cell + an additive `Grid.safe` record, **not** a fourth
  cell type (the `CELL_TYPES` tuple, `is_valid_step`'s base walkability,
  detection, generation, and the "doorway is a gap in a wall" geometry all keep
  their exact meaning).
- **Normal door feature (byte-for-byte):** the `Grid.doors` model, the
  `{type:"door", x, y, action}` wire frame and its `unlock/lock/open/close`
  actions and **error strings**, the `_on_door` state machine, and the normal
  door render are **unchanged** (the only additive line in `_on_door` is the
  safe-cell guard `"not a normal door"`, which never fires for non-safe cells).
- **Awareness (HARD CONSTRAINT):** `app/awareness.py` is **byte-identical**;
  the three-tier FULL/APPROXIMATE/INVISIBLE model, `build_awareness`'s logic,
  awareness items, the awareness ring, and the awareness sidebar are
  **unchanged**. Safe-door state affects awareness *only* through the
  (changed) line-of-sight predicate (AC8).
- **Explored-map algorithm:** `visible_cells`/`build_visibility_mask` S/E/H
  logic **unchanged** (byte-identical file). The D5 closed-door face rule
  already in `visible_cells` covers closed safe doors with no new code.
- **`players[]` / `Player.to_dict()`:** unchanged (safe-door state is map
  state, not per-player).
- **`fog`:** unchanged, still a wire-compat no-op.
- **`path` / `error` frame shapes:** unchanged (a `safe_door` action replies
  with the existing `error` shape; a success uses the existing `state`
  broadcast, not a new frame).
- **The sample dungeon geometry** (`app/grid.py`): byte-unchanged (no safe
  doors are pre-marked; a GM must author them).
- **GM:** never door-filtered (I3); tools gain a Safe door tool, nothing else
  changes.
- **Movement permission rules** (player self-move-only, `override` GM-only,
  `"no route — wall in the way"`, `"not allowed"`, bounds) unchanged, **plus**
  the additive hostile-on-safe-door guard.
- **Upload / generate endpoints' request shapes** unchanged (only the response
  *gains* the additive `safe` key, and only when safe doors were painted —
  fresh maps are unchanged).
- **`app/main.py` (registry/session), `app/imaging.py`, `app/ws.py`,
  `app/grid.py`, `app/detection.py`, `app/generation.py`,
  `app/awareness.py`, `app/visibility.py`:** unchanged (the latter two
  byte-identical).
- **All non-safe existing tests:** unchanged — the design is additive (§12 →
  §14: "0 existing assertions break").

---

## 13. Explicit assumptions (per PROJECT.md ambiguity convention)

Every ambiguous point in the requirement is resolved here and pinned by an AC.

- **A1 — "Safe room" is per-door, marked by the GM (not a zone).** The
  requirement says "add 'safe room' **doors** … only neutral npcs or player
  characters may **step onto the safe room door**." The restriction is
  **on the door cell itself** (who may stand on / step onto that `doorway`
  cell), applied by the GM marking individual doorways. It is **not** a new
  "safe zone" region and **not** a global flag. (The whole feature is
  door-scoped; AC5/AC6 pin the per-cell restriction.)
- **A2 — "neutral npcs or player characters" = `team` ∈ {`neutral`, `party`};
  "hostile" is excluded.** The three teams are `party`/`neutral`/`hostile`
  (PROJECT.md §4). "Player characters" are `party` (and GM-created player
  tokens), "neutral npcs" are `neutral`; the only team **not** allowed is
  `hostile`. The restriction is judged by the **entity's `team`** (not its
  `kind`), so it is invariant to GM `set_team` (E4/AC7). (Pinned by AC5.)
- **A3 — The restriction holds when the safe door is OPEN (walkable).** The
  task states this explicitly and it is the point of the feature: even open,
  only party/neutral may occupy the cell; a hostile is blocked (the open safe
  door is a wall *to the hostile*). (AC5.)
- **A4 — GM opens/closes the safe door; players cannot; hostile blocked even
  under override.** "The door will always be unlocked but can be closed" ⇒
  **no lock state**, always unlocked; "starts closed" ⇒ marked `C`. "Allow the
  GM to add" ⇒ **GM-only** control (mark/unmark/open/close). The hostile
  override guard is the deliberate difference from the normal closed-door
  override (door-features A3) — a safety rule. (Pinned by AC3, AC7.)
- **A5 — Marking a cell a safe door requires it to be empty (E1).** A token on
  the cell (any team) blocks `mark` — you cannot convert a cell someone is
  standing on, which also guarantees a hostile can never be *left* on a cell
  when it becomes a safe door. (AC9.)
- **A6 — Unmarking reverts to a normal door preserving open/closed.**
  `unmark` is not a "delete" — it converts the safe door back to a **normal**
  door (closed→`U`, open→`O`), so the GM can re-lock it afterward (a safe door
  has no lock, a normal door does). The cell stays a `doorway`. To fully remove
  the door, the GM paints `floor`/`wall`. (AC3, AC9.)
- **A7 — The green cross is distinct from the party-green token.** Chosen
  `#3ddc84` (bright mint) + a **cross** glyph vs. the party token's `#2f9e44`
  (darker forest) + a **circle** — distinct by both exact green and shape. The
  cross is the primary discriminator. (AC10.)
- **A8 — REST stays frozen apart from the additive `safe` field** (no new safe
  REST route; safe-door actions are WS-only). (AC10.)
- **A9 — `map.safe` is emitted in full whenever ≥ 1 safe door exists** (and
  `map.doors` excludes those cells, so the two partition the doorway cells); a
  missing `safe` ⇒ no safe doors; a missing `doors` ⇒ all normal doors locked.
  (AC1, AC10.)
- **A10 — `safe` is a separate `Grid` field, not a value in `doors`.** This
  keeps the normal-door feature byte-for-byte (its frozen state machine,
  `DOOR_STATES`, error strings, and tests) and makes safe doors a clean additive
  layer. The cost is a second sparse dict + mutual-exclusion invariant
  (I1). (AC1, AC12, AC13.)

---

## 14. Test plan

Mapped to the existing files + a new live script. All deterministic. New
behavior is added in **new test classes** (so it never perturbs unrelated
assertions). Because the design is **additive**, **no existing test's intent is
modified** (contrast with door-features A1): the `door` message and `DOOR_STATES`
are frozen, `Grid.safe` defaults to `None`, the `team` parameter defaults to
`None`, and `doors_for_wire`/`state_for`/REST are byte-identical for grids
without safe doors. The only additive line touching shared code — the
`_on_door` `"not a normal door"` guard — never fires for a non-safe cell.

- **`tests/test_models.py` (new class `TestSafeDoor*`):** safe-door model
  (AC1, AC12). `safe` round-trip via `to_dict`/`from_dict`; default `None` ⇒ no
  safe doors; `to_dict` omits `safe` when none and emits it when present;
  `__post_init__` rejects a `safe` key on a `floor`/`wall` cell, an out-of-bounds
  key, a bad state char, **and a key present in both `doors` and `safe`**
  (mutual exclusion, I1); `is_safe_door`/`safe_door_state_at`/
  `is_safe_door_closed`/`set_safe_door`/`unmark_safe_door` (reversion `C`→`U`,
  `O`→`O`); `doors_for_wire` **excludes** safe cells and is unchanged for grids
  without safe doors; `sync_doors_after_cell_set` deletes a `safe` key when the
  cell is repainted floor/wall. (AC1, AC10, AC12.)
- **`tests/test_pathfinding.py` (new `TestSafeDoor*`):** closed safe door is
  **not** `walkable` for **any** team (incl. the `team=None` default, so the
  existing door/LOS behavior is identical); an **open** safe door is `walkable`
  for **party/neutral** and **not** for **hostile** (`walkable(..., team=...)`);
  `is_valid_step` diagonal into/around an open safe door is blocked for a
  hostile (no slip-through) and allowed for party/neutral; `has_line_of_sight`
  **unchanged** (team-agnostic): a closed safe door blocks LOS exactly like a
  wall, an open safe door is transparent, identical for all teams;
  `find_path(team="hostile")` routes **around** an open safe door / `None` if
  it's the only route; `find_path(team="party"|"neutral")` routes **through** it;
  `find_path(team=None)` (regression) identical to today for safe-less grids.
  (AC4, AC5, AC6.)
- **`tests/test_visibility.py` (new `TestSafeDoor*`):** a closed safe door's far
  side is **H** (never explored) / **E** (explored before) exactly like a wall;
  a closed safe door's **face** is S (D5, revealed by the existing wall-face
  rule — no new code); an open safe door reveals the far side as S; S is
  monotonic (I9); and the **regression pin**: for the sample dungeon **with no
  safe doors** (and with all normal doors in a given state), the mask equals the
  pre-feature mask byte-for-byte. (AC6, AC7, AC8, AC16.)
- **`tests/test_door_session.py` (extend) + a new `TestSafeDoor*` class:** the
  full safe-door state machine + permission matrix (AC3) — every legal
  transition; every illegal `(state, action, role)` returns the **exact** error
  string in the §4.3 deterministic order; **non-GM → `"not allowed"` first**;
  `mark` occupancy rejection (E1); `unmark` reversion (E: `C`→`U`/`O`→`O`); the
  **normal `door` message on a safe cell → `"not a normal door"`** (AC13b);
  `map.safe` + `map.doors` present and disjoint in every `state`/`welcome`
  (AC1, I5); **the restriction** — GM moves a **hostile** through an open safe
  door → `"no route — wall in the way"` (position unchanged) while a **party/
  neutral** token routes through; **hostile override/place/create onto a safe
  cell → `"cannot place a hostile on a safe room door"`** (AC7); party/neutral
  override onto a **closed** safe door → allowed (E11); `set_team` to hostile
  on an open safe door → rejected (E4); awareness unchanged through safe-door
  state (AC8); explored unchanged (AC7); `use_map` resets safe doors with the
  grid (AC14, E10); GM unfiltered (I3).
- **`tests/test_ws.py` (new `TestSafeDoorWire`):** the `safe_door` message over a
  real WS (GM); error replies per-client; success → broadcast carries new
  `map.safe` (and updated `map.doors` for mark/unmark); a non-GM `safe_door` →
  `"not allowed"`; a `door` message on a safe cell → `"not a normal door"`; the
  existing `TestDoorWire` **unchanged** (its `door` frames still work on normal
  doors). (AC3, AC14.)
- **`tests/test_api.py` (new class + extend):** `GET /api/maps/{id}` carries the
  additive `safe` object (disjoint from `doors`) **only when safe doors exist**
  (set via a WS `mark` in a live-session setup, or by constructing a registered
  grid with `safe`), and is **byte-identical** (no `safe` key) for a map with
  none — so the existing **exact key-set** assertions (`test_detail_shape`,
  upload, generate) keep passing; no new REST route. (AC10.)
- **`tests/test_frontend.py` (new `TestSafeDoor*`):** static checks —
  `index.html` has the `data-tool="safeDoor"` button + the four `data-safe-
  action` sub-buttons + the `legend-safe` chip (visible to both roles — no
  `body.is-gm` gate); the `T` safe tokens present; harness — `drawGridOnCanvas`
  renders the **green cross** (open: cross only; closed: cross + bar) in the
  correct full-tier green `#3ddc84` and the explored-tier sage green
  `#8fae9c`, and a GM pass (no matrix) renders full-tier; `state.safe` is set
  from `msg.map.safe` (`{}` when absent), a malformed `safe` ⇒ `{}`; the GM Safe
  door tool: selecting an action + clicking a doorway cell sends
  `{type:"safe_door", x, y, action}`; a **player tap on a safe cell sends
  nothing** (no `door` frame, no move). (AC10, AC11.)
- **`tests/js/harness.js` (extend):** add `isSafeDoor`, `safeDoorStateAt`,
  `validateSafe`, `sendSafeDoor`, `state` (already exported) to `EXPORTS`; the
  stub `ctx` already records `strokeRect`/`moveTo`/`lineTo` — extend to record
  the cross strokes so the harness can assert the per-state safe-door color +
  cross/bar presence.
- **`scripts/e2e_proof.py` — new step 11 (safe-room doors, live server):**
  - (a) GM + player on the sample dungeon; welcome `map.safe` **absent** (no
    safe doors by default) and `map.doors` all `L` (regression: unchanged).
  - (b) GM `safe_door mark` the (5,5) doorway → `map.safe` = `{"5,5":"C"}` and
    `map.doors` **no longer has** `"5,5"` (disjoint); the cell renders a green
    cross (client). GM `open` it → `"5,5":"O"`.
  - (c) **Restriction:** GM creates a **hostile** enemy and a **neutral** npc;
    moves the **hostile** through the **open** safe door →
    `"no route — wall in the way"` (position unchanged); moves the **neutral**
    npc through it → legal A* path through the door cell.
  - (d) **Hostile override guard:** GM `override:true` move the hostile onto the
    safe cell → `"cannot place a hostile on a safe room door"` (NOT teleported);
    GM `place`/`create_entity` a hostile there → same rejection; a party/neutral
    override onto a **closed** safe door → allowed.
  - (e) **Awareness:** a hostile behind a **closed** safe door is APPROXIMATE
    (within radius) / INVISIBLE (beyond) to the player; behind an **open** safe
    door it is FULL (LOS is team-agnostic). (AC8.)
  - (f) **Explored:** behind a closed safe door the room is H then E; the safe
    door face is S; opening reveals it as S; S is monotonic. (AC7.)
  - (g) **Permissions over the wire:** a player `safe_door mark` →
    `"not allowed"`; a `door` message on the safe cell → `"not a normal door"`.
  - (h) GM `unmark` (5,5) (closed) → reverts to a normal door `"U"` in
    `map.doors`, `map.safe` empty/absent.
  - The independent S-set re-derivation helper is **already safe-aware** (it
    uses `has_line_of_sight`, which now sees safe doors via `_closed_doors`) —
    extend it to feed the `safe` object through `Grid.from_dict`.
- **`scripts/qa_safe_doors.py` (new live script):** a standalone, human-runnable
  safe-door QA script (mirrors `scripts/qa_doors.py`'s structure): boots the
  server, drives GM + player over WS, prints a check per safe-door behavior
  (default absent, mark → closed green cross, open/close, party/neutral walk
  through open, hostile blocked (no-route) even open, hostile override/
  place/create rejected, awareness tiers, explored H/E + face, unmark reversion,
  permissions, REST `safe` field present + disjoint, `door`-on-safe → "not a
  normal door"). Exits non-zero on any failure. This is the "live" companion to
  `e2e_proof.py`.

**Existing tests that need updates: NONE (0).** Every existing assertion is
preserved because the feature is additive (see §12). The only shared-code touch
points and why they are no-ops for existing tests:

- `_on_door`: +1 guard line `if self.grid.is_safe_door(x, y): return "not a
  normal door"` — **never fires** for a grid/cell without safe doors (every
  existing door test uses normal doors). The `test_bad_action` error strings
  are **untouched** (the `door` message still rejects `explode`/missing with
  `"action must be one of unlock/lock/open/close"`).
- `_closed_doors`: safe-aware rewrite — **byte-identical output** for a grid
  with `safe is None` (the `key in safe` branch never fires) → every existing
  pathfinding/visibility/awareness/session/WS test unchanged.
- `walkable`/`is_valid_step`/`find_path`: +optional `team=None` param —
  **identical** for all existing (no-team) call sites.
- `doors_for_wire`/`state_for`/REST `_with_doors`: emit `safe` **only** when
  `grid.safe` is non-empty and skip safe cells from `doors` — **identical**
  output for a grid with no safe doors → every existing REST key-set + `map.
  doors` payload assertion unchanged.
- `app/awareness.py`, `app/visibility.py`: **byte-identical.**

---

## 15. Acceptance criteria (for QA)

Individually testable, following `door-features.md` §15. The harness: in-process
`GameSession` + `FakeConn`/`drive` (`tests/test_session.py` /
`tests/test_door_session.py` idioms), pure `app.pathfinding`/`app.visibility`/
`app.models` unit tests, raw-socket WS (`tests/wsclient.py` + `make_server`),
the Node harness (`tests/js/harness.js` + `tests/test_frontend.py`), and
`scripts/e2e_proof.py` / `scripts/qa_safe_doors.py` over the live server. All
deterministic.

- **AC1 — Safe-door state model + round-trip.** `Grid.safe` (a) round-trips
  `to_dict`→`from_dict` preserving every state (and every `doors` state);
  (b) `safe=None` ⇒ no safe doors and `to_dict` omits the `safe` key; (c)
  `safe={"5,5":"C"}` round-trips; (d) `__post_init__` raises `ValueError` for a
  `safe` key on a `floor`/`wall` cell, an out-of-bounds key, a state char not in
  `{"C","O"}`, **and a key present in both `doors` and `safe`** (I1); (e)
  `is_safe_door` is False for non-doorway cells and normal doors;
  `safe_door_state_at` returns `None` for non-safe cells, `"C"`/`"O"` otherwise;
  `is_safe_door_closed` is True for `"C"`, False for `"O"`/non-safe.
- **AC2 — Default closed, no lock, GM-only creation.** (a) a freshly
  `mark`ed safe door is **`C`** (closed) and has **no** lock state (no
  `"L"`); (b) **no** REST route or detection/generation path ever *creates* a
  safe door (only a GM WS `safe_door mark` does); (c) `mark` is GM-only
  (player → `"not allowed"`).
- **AC3 — Safe-door state machine + permissions (exact).** For each of the 2
  states × 4 actions (+ `mark`/`unmark` on non-safe cells): the legal
  `(state, action)` combinations apply the transition and broadcast; every
  illegal combination returns the **exact** error string in the §4.3
  deterministic order. Specifically: non-GM any action → `"not allowed"`;
  GM `mark` normal doorway → safe `C`; GM `mark` a safe door → `"already a
  safe door"`; GM `unmark` a safe door → normal door (`C`→`U`, `O`→`O`);
  `unmark`/`open`/`close` on a non-safe doorway → `"not a safe door"`; GM
  `open` `C`→`O`; GM `close` `O`→`C` (occupancy-guarded); `open` on `O` →
  `"safe door is already open"`; `close` on `C` → `"safe door is already
  closed"`; non-doorway cell → `"not a doorway"`; OOB → `"destination out of
  bounds"`; bad action (incl. `lock`/`unlock`) → `"action must be one of
  mark/unmark/open/close"`.
- **AC4 — Closed safe door blocks LOS like a wall (incl. corner-cut).**
  `has_line_of_sight` with a **closed** safe door strictly between `a`,`b` →
  **False** (identical to the same geometry with a `wall`); with the same safe
  door **open** → **True** (transparent); a diagonal sight step whose both
  elbows are closed safe doors → blocked (corner-cut). **Identical for all
  teams** (LOS is team-agnostic — no `team` param). The GM is never filtered
  (I3).
- **AC5 — Movement + the entity restriction (the core rule).**
  `walkable(closed safe door, any team incl. None)==False`;
  `walkable(open safe door, "party"/"neutral")==True`;
  `walkable(open safe door, "hostile")==False`; `walkable(open safe door,
  None)==True` (entity-agnostic). `find_path(team="hostile")` through an open
  safe door routes **around** it (or `None` if sealed → `"no route — wall in
  the way"`); `find_path(team="party"/"neutral")` routes **through** it. A
  diagonal into/around an open safe door is blocked for a hostile (no
  slip-through) and allowed for party/neutral. **Regression:** for a grid with
  **no** safe doors, `find_path`/`walkable`/`is_valid_step` are byte-identical
  to today (team=None). (A3, A2.)
- **AC6 — Hostile cannot stand on an open safe door; party/neutral can.**
  Session: GM moves a **hostile** token to a destination **on** an open safe
  door → `"no route — wall in the way"` (position unchanged); GM moves a
  **party**/`neutral` token to the same destination → a `path` frame, token
  lands **on** the safe cell. A closed safe door → `"no route — wall in the
  way"` for **all** teams. (AC5's session-level proof; E3.)
- **AC7 — Hostile override/`place`/`create`/`set_team` guard (the safety rule,
  A4).** (a) GM `override:true` move a **hostile** onto a safe-door cell (open
  **or** closed) → `"cannot place a hostile on a safe room door"` (NOT
  teleported, token unchanged). (b) GM `place` a hostile onto a safe cell → same
  rejection. (c) GM `create_entity` a hostile at a safe cell → same rejection.
  (d) GM `set_team` a **party/neutral** token (standing on an **open** safe door)
  to **hostile** → same rejection (E4). (e) **Contrast:** a **party/neutral**
  `override`/`place` onto a **closed** safe door is **ALLOWED** (ignore-walls,
  E11). After every successful mutation, **no hostile** occupies a safe-door
  cell (I4b).
- **AC8 — Awareness UNCHANGED, safe-door-driven only via LOS (byte-identical
  `awareness.py`).** (a) `app/awareness.py` is **byte-unchanged** (no diff).
  (b) Scenarios: a hostile behind a **closed** safe door within the radius →
  **APPROXIMATE** (grey "?", no identity); beyond → **INVISIBLE** (absent);
  behind an **open** safe door with a clear line → **FULL** (named/labeled) —
  *even though* the hostile can't be *on* the door (sight is team-agnostic); a
  closed safe door with an open-elbow detour giving true LOS → FULL (LOS exact).
  (c) The GM's awareness is unfiltered regardless of safe-door state. (d) For
  the sample dungeon **with no safe doors and all normal doors open**, the
  `awareness` output is **byte-identical to the pre-feature build** (regression
  pin).
- **AC9 — Occupancy guards (E1, I8).** (a) `mark` with **any** entity on the
  cell → `"cannot mark a safe door with a token on it"`, no safe door created.
  (b) `close` with an entity on the safe cell → `"cannot close a door with a
  token on it"`, door stays open. (c) After any successful `mark`/`close`, **no**
  entity occupies a **closed** safe door cell.
- **AC10 — Wire + REST + rendering.** (a) Every GM & player `welcome`/`state`
  carries `map.safe` (every safe door's state) **and** `map.doors` (normal
  doors), **disjoint** and jointly covering all doorways (I5); a grid with no
  safe doors omits `safe` (and its `doors` is byte-identical to today). (b)
  `GET /api/maps/{id}` gains `safe` **only when** safe doors exist (disjoint
  from `doors`); **fresh** upload/generate responses are **unchanged** (no
  `safe` key); **no new REST route** (A8); existing REST keys unchanged. (c)
  The `T` palette has `safeOpen`/`safeClosed` = `#3ddc84` — **distinct from
  floor `#efe9dc`, wall `#3b4252`, and party-green `#2f9e44`** (A7) — plus the
  explored-tier sage variants; (d) `index.html` has the `data-tool="safeDoor"`
  button + four `data-safe-action` sub-buttons + the `legend-safe` chip;
  `#paint-group` unchanged otherwise; the safe legend chip is **visible to both
  GM and players** (not `body.is-gm`-gated). (e) The GM legend and player
  legend both show the safe-door chip.
- **AC11 — Frontend rendering + interaction (harness + static).** (a)
  `drawGridOnCanvas` renders a **safe door as a green cross**: open → green
  cross (no bar) `#3ddc84` (full) / `#8fae9c` (E); closed → green cross **+
  bar** (full) / desaturated (E); a GM/preview pass (no matrix) renders
  full-tier; a **normal** door still renders its red/amber arch/bar/padlock
  (byte-identical, regression). (b) `state.safe` is set from `msg.map.safe`
  (`{}` when absent); a malformed `safe` ⇒ `{}` (no safe doors), never crashes.
  (c) GM Safe door tool: selecting an action + clicking a doorway cell sends
  `{type:"safe_door", x, y, action}`; clicking a `floor`/`wall` cell sends
  nothing (server `"not a doorway"` on the next real attempt) — no optimistic
  mutation. (d) A **player tap on a safe-door cell sends no door frame and no
  move** (no-op / selection only); a player tap on a **normal** door cell still
  sends the inverse `door` action (regression). (e) Preview canvas untouched
  beyond the map's `safe` data.
- **AC12 — Backward compatibility (additive, A2/A10).** A `Grid.from_dict` of a
  payload with **no** `safe` key yields a grid with no safe doors and behaves
  **exactly as today**; the old `Grid(name, width, height, cells, image, doors)`
  constructor still works (`safe` defaults `None`); the old
  `walkable`/`is_valid_step`/`find_path`/`has_line_of_sight` calls (no `team`)
  still run and behave identically for safe-less grids; old
  `welcome`/`state`/REST consumers that ignore `safe` still work (safe doors
  render as normal doorways — the authoritative server is the source of truth
  for the restriction). The normal `door` message + `DOOR_STATES` + its error
  strings are **byte-for-byte** unchanged.
- **AC13 — The frozen normal-`door` surface + the safe guard.** (a) Every
  existing normal-door test passes **unmodified** (the `door` message,
  `DOOR_STATES`, and error strings are untouched). (b) A `door` message
  (any of unlock/lock/open/close) on a **safe-door cell** → `"not a normal
  door"`, and the safe record is **untouched** (mutual exclusion I1 preserved;
  no `doors` entry is created on a safe cell). (c) The `test_bad_action`
  normal-door error (`"action must be one of unlock/lock/open/close"`) still
  fires for a bad action on a **normal** door.
- **AC14 — e2e + live proof.** `scripts/e2e_proof.py` **step 11** (safe-room
  doors) is all-✓ (default absent, mark → closed, open/close, party/neutral
  walk-through of open, hostile blocked even open, hostile override/
  place/create rejected, awareness tiers, explored H/E + face, unmark
  reversion, permissions, `door`-on-safe → "not a normal door", re-derived S-
  set with safe-aware LOS); `scripts/qa_safe_doors.py` is all-✓ over the live
  server; `GET /health` ok.
- **AC15 — Performance budget.** On a 60×60 grid with a **mix of safe and
  normal doors** (several safe doors in mixed open/closed states) and 6 players
  + a GM attached (fake conns, measure `state_for` directly): one full recompute
  (all 6 `state_for`) completes in **< 250 ms** on the reference machine class;
  assert with `time.perf_counter` at a **500 ms** bound. The team-aware
  predicates must not degrade A* beyond a constant factor (assert a 60×60
  `find_path(team=...)` across many doors < 50 ms). The `_blocked_for` /
  `_open_safe_doors` / safe-aware `_closed_doors` set derivations are computed
  **once** per A* / per `visible_cells` call (not per step/cell).
- **AC16 — Full regression.** `python -m pytest` **and** `python -m unittest
  discover -s tests -t .` fully green **with NO existing test modified** (all
  new behavior in new test classes); `scripts/e2e_proof.py` all-✓ including the
  new step 11; `GET /health` ok; the sample dungeon geometry (`app/grid.py`)
  byte-identical; **`app/awareness.py` and `app/visibility.py` byte-identical**;
  the normal-door wire frame, `DOOR_STATES`, and error strings byte-identical.

---

## 16. Non-goals

- **No per-door ACLs beyond the team rule** (no "only player X may open this
  door", no per-door player permissions — the only gates are GM-control + the
  party/neutral-vs-hostile occupancy rule).
- **No "safe zone" region** (the restriction is on the **door cell**, not a
  surrounding area — A1). No multi-cell "safe room" interior.
- **No auto-open-on-approach / proximity auto-open** (a safe door only changes
  state on an explicit `safe_door` message or a GM paint that deletes it).
- **No client-predicted safe state** (the server is authoritative; the client
  renders the broadcast `map.safe`, no optimistic safe mutation).
- **No player open/close of a safe door** (GM-only, A4) — no player tap acts on
  a safe door.
- **No `lock`/`unlock` on a safe door** (no lock state, always unlocked, A4).
- **No new REST safe endpoint** (WS-only safe-door actions; REST only gains the
  additive `safe` field).
- **No persistence / save-load of safe-door state** (in-memory, like all
  session state — a restart is fresh).
- **No changes to the doorway *detection heuristic* or to generated/sample map
  geometry** — safe doors are GM-authored, never detected/generated.
- **No animation / sound** (safe doors are instant state changes; the client
  re-renders from the broadcast).

---

## 17. Implementation impact table (file by file)

| File | Change | Summary |
|---|---|---|
| `app/models.py` | **Modify (additive)** | Add `SAFE_DOOR_STATES = ("C","O")` and `SAFE_DOOR_TEAMS = frozenset({"party","neutral"})`. `Grid`: new trailing field `safe: dict[str,str] \| None = None`; `__post_init__` validates safe keys (doorway-only, in-bounds, valid state, **mutually exclusive with `doors`**); `to_dict`/`from_dict` carry `safe` additively; add `is_safe_door`, `safe_door_state_at`, `is_safe_door_closed`, `set_safe_door`, `unmark_safe_door`; extend `sync_doors_after_cell_set` to delete a `safe` key on floor/wall; extend `doors_for_wire` to **skip** safe cells. `Entity`/`Player`/`Session` **unchanged**. |
| `app/grid.py` | **Unchanged** | Sample dungeon geometry untouched (no safe doors pre-marked). |
| `app/pathfinding.py` | **Modify (additive)** | Make `_closed_doors` **safe-aware** (a closed safe door blocks like a wall; byte-identical for safe-less grids). Add `_open_safe_doors(grid)` and `_blocked_for(grid, doors, team)` (closed set ∪ open safe doors when team is hostile). `walkable`/`is_valid_step`/`find_path` gain an **optional `team=None`** param (default → identical to today). `has_line_of_sight` **unchanged** (team-agnostic; inherits the safe-aware `_closed_doors`). |
| `app/visibility.py` | **Unchanged (byte-identical, I6)** | S/E/H logic unchanged; the existing D5 closed-door face rule already covers closed safe doors via the safe-aware `_closed_doors`. |
| `app/awareness.py` | **Unchanged (byte-identical, I6)** | No safe-door/`team` special-casing; inherits safe-door LOS blocking (AC8/AC16). |
| `app/session.py` | **Modify** | Add `SAFE_DOOR_ACTIONS`, `mtype == "safe_door"` dispatch → `_on_safe_door` (the §4.4 state machine + permissions + occupancy). `_on_door`: **+1 guard line** `if self.grid.is_safe_door(x, y): return "not a normal door"` (additive; never fires for normal doors). `_on_move`: pass `team=entity.team` to `find_path`; add the **hostile-safe-door guard** to the override branch. `_on_place` / `_on_create_entity`: +hostile-safe-door guard. `_on_set_team`: +hostile-on-safe-cell guard (E4). `_on_paint`: unchanged in shape (calls the extended `sync_doors_after_cell_set`). `state_for`/`_visibility_for`: add additive `map.safe` to the payload (disjoint from `map.doors`). |
| `app/detection.py` | **Unchanged (behavior)** | Detection still produces `doorway` cells; it never produces safe doors. |
| `app/generation.py` | **Unchanged (behavior)** | BSP still carves `doorway` cells (all normal, all `L`); no safe doors. |
| `app/server.py` | **Modify (additive)** | Map detail, upload, and generate responses **gain** the additive `"safe"` key (from `grid.doors_for_wire()`-equivalent safe wire object) **only when** safe doors exist; `doors` excludes safe cells. **No new route.** |
| `app/static/index.html` | **Modify** | `#paint-group`: add `data-tool="safeDoor"` `🛡 Safe door` button + the four `data-safe-action` sub-buttons (Mark/Unmark/Open/Close). `#legend`: add the `legend-safe` chip. |
| `app/static/app.js` | **Modify** | `state.safe` + `isSafeDoor`/`safeDoorStateAt`/`validateSafe`; `applyState` sets `state.safe` from `msg.map.safe`; `T` safe tokens (§7.3); `drawGridOnCanvas` doorway pass: **if safe door → green cross (+bar if closed) per tier; else the existing normal-door art (unchanged)**; `state.tool` gains `"safeDoor"` + a safe-action selection; canvas click: GM safe-door-tool click → `sendSafeDoor`, **player tap on a safe cell → no-op**; safe-door control-hint copy (§7.7). |
| `app/static/style.css` | **Modify** | `:root` tokens `--safe-open`/`--explored-safe-open`; `.swatch.safe-door` (green cross swatch); `#paint-group` Safe door tool + sub-button styling; `mode-paint-safeDoor` cursor. |
| `tests/test_models.py` | **New class** | Safe-door model + round-trip + mutual exclusion + `unmark` reversion + `doors_for_wire` partition (AC1, AC10, AC12). |
| `tests/test_pathfinding.py` | **New class** | Safe-door walkable/step/LOS/corner-cut + team-aware `find_path` (hostile blocked even open) + no-safe regression (AC4, AC5, AC6). |
| `tests/test_visibility.py` | **New class** | Closed safe door H/E + face-reveal (D5, no new code) + open safe door S + monotonicity + no-safe regression pin (AC6, AC7, AC8, AC16). |
| `tests/test_door_session.py` | **New class** | `_on_safe_door` state machine / permissions / occupancy / `map.safe`+`map.doors` disjoint in payload / the **restriction** (hostile blocked open, party/neutral walk through) / **hostile override/place/create/set_team guards** / `door`-on-safe → "not a normal door" / `use_map` safe reset / awareness + explored unchanged (AC3, AC6–AC9, AC13, AC14). |
| `tests/test_ws.py` | **New class** | `safe_door` wire tests (GM, error replies, broadcast `map.safe`, non-GM `"not allowed"`, `door`-on-safe → "not a normal door"); existing `TestDoorWire` unchanged (AC3, AC14). |
| `tests/test_api.py` | **New class** | Additive `safe` in map objects (disjoint from `doors`) only when present; fresh maps unchanged; no new route; existing key-set assertions unchanged (AC10). |
| `tests/test_frontend.py` | **Extend + new** | Static checks (safe-door tool + 4 sub-buttons + legend chip, `T` tokens); harness render tests for the green cross (open/closed, full + explored tiers), `state.safe`, GM click→`safe_door`, player tap no-op (AC10, AC11). |
| `tests/js/harness.js` | **Extend** | Add `isSafeDoor`/`safeDoorStateAt`/`validateSafe`/`sendSafeDoor` to `EXPORTS`; extend the stub `ctx` to record the cross strokes if needed. |
| `scripts/e2e_proof.py` | **Add step 11** | Safe-door live proof (default absent, mark/open/close, party/neutral walk-through, hostile blocked + override/`place`/`create` rejected, awareness + explored tiers, unmark reversion, permissions, safe-aware S-set re-derivation) (AC14). |
| `scripts/qa_safe_doors.py` | **New** | Standalone live safe-door QA script (mirrors `qa_doors.py`): boots the server, drives GM + player over WS, prints a check per safe-door behavior, exits non-zero on failure (AC14). |
| `PROJECT.md` | **Doc note (optional)** | A short addendum pointing to this spec (safe doors = `doorway` cells + an additive `Grid.safe` record, GM-authored, always-unlocked, closed by default, party/neutral-only occupancy, green-cross icon). The frozen external surface is **extended additively** by the `safe_door` message + `map.safe` field. |
| `README.md` | **Doc note** | A short feature paragraph (safe-room doors: GM marks a doorway as a safe door — always unlocked, closed by default, GM open/close; only party/neutral may step on it, never a hostile, even under override; rendered as a green cross; awareness + explored map keep working). |

> **Do not touch:** `app/main.py`, `app/imaging.py`, `app/ws.py`,
> `app/awareness.py` (byte-identical), `app/visibility.py` (byte-identical),
> `app/grid.py` (byte-identical sample), `app/detection.py` /
> `app/generation.py` (behavior), the **normal-door** wire frame +
> `DOOR_STATES` + error strings (byte-identical), the `players[]` /
> `Player.to_dict()` shapes, the `fog` flag, and all **existing** tests.

---

## 18. Wire protocol recap (for the engineer)

- **One new client→server message:** `{type:"safe_door", x, y, action}`,
  `action ∈ {mark, unmark, open, close}`, **GM-only**. Validated in the §4.3
  deterministic order (role first → ints → bounds → cell-is-doorway → action
  valid → transition legal → mark-occupancy / close-occupancy); per-client
  `error` replies on any failure; on success, no per-client frame — the
  `state` broadcast carries the new state.
- **One additive server→client field:** `map.safe` (a JSON object
  `{"<x>,<y>": "C"|"O"}`) inside every `welcome`/`state`/REST map object for a
  grid that has ≥ 1 safe door (emitted in full, A9; a missing key ⇒ no safe
  doors). **`map.doors` now excludes safe-door cells** so `doors` ∪ `safe`
  partition the doorway cells. **No new server→client broadcast type.**
- **The normal `{type:"door", x, y, action}` frame is unchanged** — but on a
  **safe-door cell** it is rejected with `"not a normal door"`.
- **No changes** to `you`, `entities`, `players`, `awareness`, `fog`,
  `you_entity`, `visibility`, `path`, or `error` shapes.
- **Client contract:** store `state.safe` in `applyState` (`{}` default);
  render safe doors as a **green cross** (bar iff closed) per tier in
  `drawGridOnCanvas`; the GM sends `safe_door` messages from the Safe door tool;
  a **player tap on a safe cell is a no-op** (no door frame, no move). The
  client never trusts or predicts safe state — it renders the broadcast.
