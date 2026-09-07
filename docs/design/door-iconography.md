# Design — Door Iconography Redesign + Safe-Door Lock State

**Status:** build-ready spec. Two coupled changes, one deliverable: (1) the
**door art is replaced** — the current abstract glyphs (amber arch / amber bar
/ red padlock-notch for normal doors; green cross / green cross + bar for safe
doors) are replaced by **pictorial wooden doors** that read as actual doors in
all six states; and (2) **safe-room doors gain a lock state** so the owner can
tell a *locked* safe door (padlock) from an *unlocked closed* safe door
(plain green slab). Safe doors move from a two-state `C`/`O` model to the
**same three-state `L`/`U`/`O` model the normal doors already use**, and the
GM `Safe door` tool gains **Lock / Unlock** next to the existing
Mark / Unmark / Open / Close.

The two changes are inseparable: the owner's icon spec (a locked safe door
with a padlock, and an unlocked closed safe door without one) **requires**
safe doors to carry a lock state. Section 1.1 flags this as the centerpiece
and pins it as assumption **A1** (owner confirmation requested — it is the
single deliberate behavioral change in this feature; the hostile restriction,
the closed=wall / open=sight-transparent behavior, and the pathfinding / LOS
code are all **unchanged** and byte-for-byte compatible).

**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins. Where the *owner's icon spec* (quoted in §1.1) is ambiguous,
the decision is pinned in the numbered assumptions (§11) and an AC. This spec
**builds directly on** `docs/design/door-features.md` and
`docs/design/safe-room-doors.md`; it supersedes specific assumptions there
(enumerated in §1.2) and leaves the rest of both features byte-for-byte
intact.

**Code referenced (read, not modified by the spec):** `app/models.py`
(`DOOR_STATES`, `SAFE_DOOR_STATES`, `Grid` + `doors`/`safe` accessors,
`set_safe_door` / `unmark_safe_door` / `doors_for_wire` / `safe_for_wire`),
`app/session.py` (`_on_door`, `_on_safe_door`, `DOOR_ACTIONS`,
`SAFE_DOOR_ACTIONS`, error-string house style, `HOSTILE_ON_SAFE_DOOR`),
`app/pathfinding.py` (`_closed_doors`, `_open_safe_doors`, `_blocked_for`,
`walkable`, `is_valid_step`, `has_line_of_sight`), `app/static/app.js`
(`T` tokens, `SAFE_STATES`, `validateSafe`, `safeDoorStateAt`,
`safeDoorColor`, `drawSafeDoorGlyph`, `doorColor`, `drawDoorGlyph`, the
doorway pass in `drawGridOnCanvas`, the `doorAction`/`safeAction` tool wiring,
the player-tap branch), `app/static/index.html` (legend + the `#paint-group`
Door / Safe door sub-rows), `app/static/style.css` (`:root` tokens + legend
swatches), `tests/`, `scripts/`.

---

## 1. What changes (summary)

### 1.1 The owner's exact icon spec (verbatim, source of truth)

> The current door iconography is obscure. I want intuitive **pictorial**
> icons — doors that look like doors. Six states:
>
> | Door | State | Icon |
> |---|---|---|
> | Normal | locked (closed) | brown wooden door + locked closed padlock in the TOP-RIGHT corner |
> | Normal | unlocked, closed | brown wooden door, no padlock |
> | Normal | unlocked, open | an open door with a soft yellow light |
> | Safe room | locked (closed) | green wooden door + locked closed padlock in the TOP-RIGHT corner |
> | Safe room | unlocked, closed | green wooden door, no padlock |
> | Safe room | unlocked, open | an open door with a soft green light |

### 1.2 The centerpiece decision — safe doors gain a lock state (A1, **owner confirmation requested**)

The icon spec is written for **six** states — three per door kind — and
distinguishes, for the **safe** door, a **locked (closed)** state (green slab
+ padlock) from an **unlocked (closed)** state (green slab, no padlock). The
shipped safe-door model has **only two** states, `C` (closed) and `O`
(open), and is documented as **always unlocked** (safe-room-doors.md §1.2,
I2, A4): there is no lock state at all, so a safe door can never show a
padlock and the GM has no Lock/Unlock control.

**The two distinct safe-door states "locked closed" vs "unlocked closed"
REQUIRE safe doors to gain a lock state.** This spec resolves it by putting
safe doors on the **same `L`/`U`/`O` model the normal doors already use**:

- **`L`** = locked + closed (green slab + padlock top-right)
- **`U`** = unlocked + closed (green slab, no padlock)
- **`O`** = open (open leaf + soft green light)

Normal doors use the identical semantics with **brown** wood and a **soft
yellow** light. Safe doors remain **GM-controlled end-to-end** (Lock/Unlock
are added to the existing Mark/Unmark/Open/Close GM actions). **Nothing else
about safe doors changes:** the hostile entity restriction (only `party`/
`neutral` may occupy an open safe door; a hostile is blocked even under GM
override), and the closed=wall / open=sight-transparent behavior, are
**UNCHANGED** — pathfinding and LOS only ever test *open vs not-open*, so both
`L` and `U` are "closed" and both render/have the same gameplay effect.

**Legacy migration:** a stored safe-door `"C"` (from the shipped build) ⇒
**`"U"`** (unlocked closed — the old closed state was always-unlocked). A
fresh GM `mark` ⇒ **`"L"`** (locked — the safe/secure default, mirroring how
a normal door defaults to locked). `unmark` reversion **preserves** the state
(`L`→`L`, `U`→`U`, `O`→`O`) instead of the old `C`→`U`/`O`→`O`.

> ⚠️ **This is the single behavioral change in the feature and it needs owner
> sign-off (A1, §11).** Concretely it means: a GM now has to *unlock* a safe
> door before anyone (including party/neutral) can walk through it (a locked
> safe door is a wall to everyone, like a locked normal door); and the wire
> value of an existing `map.safe` entry changes from `C` to `U` on load.
> Every gameplay invariant that was true before remains true (§12) — only the
> *lock* dimension is added.

**What this supersedes (explicit list):**

- safe-room-doors.md **§1.1** "the door will always be unlocked but can be
  closed, and starts closed" and the **green cross** icon requirement.
- safe-room-doors.md **I2** ("Default is closed, **no lock** … there is no
  lock").
- safe-room-doors.md **A4** ("**no lock state**, always unlocked; marked `C`
  ⇒ GM-only mark/unmark/open/close").
- safe-room-doors.md **A6** (unmark reversion `C`→`U`, `O`→`O`) — now
  L→L / U→U / O→O.
- safe-room-doors.md **§4.1** "No `lock`/`unlock` on a safe door" and the
  §4.3 bad-action string `"action must be one of mark/unmark/open/close"`.
- `docs/design/door-features.md` §7.1 / §7.2 / §7.4 door *art* and
  `style.css`/`app.js` door color tokens (the amber/red + green-cross art).
- The **old safe-door default state** `C` on a fresh mark (now `L`).

What is **NOT** superseded: the `Grid.safe` field, the `map.safe` wire field,
the GM-only nature of safe doors, the hostile restriction, the
closed=wall/open=sight-transparent behavior, mutual exclusion with
`Grid.doors`, and all of normal-door state machine + error strings.

### 1.3 Change table

| # | Change | Where |
|---|---|---|
| ICON-1 | **Safe doors gain a lock state.** `SAFE_DOOR_STATES` becomes `("L","U","O")` (mirrors `DOOR_STATES`). Migration: `from_dict` maps a legacy `"C"` ⇒ `"U"`. A fresh `mark` writes `"L"`. `unmark` reversion preserves state (L→L, U→U, O→O). | `app/models.py` (`Grid`), `app/session.py` (`_on_safe_door`) |
| ICON-2 | **Safe state machine** (§4): six GM-only actions `mark`/`unmark`/`unlock`/`lock`/`open`/`close`; exact transition + error table (§4.1/§4.3); a bad `safe_door` action now reports `"action must be one of mark/unmark/unlock/lock/open/close"`. | `app/session.py` (`_on_safe_door`, `SAFE_DOOR_ACTIONS`) |
| ICON-3 | **Wire/REST:** `map.safe` now carries `L`/`U`/`O` (was `C`/`O`); `map.doors` **unchanged** (already `L`/`U`/`O`). No new field, no new route. | `app/models.py`, `app/session.py`, `app/server.py` |
| ICON-4 | **Pathfinding / LOS / restriction: NO CHANGE.** `_closed_doors` (tests `!= "O"`), `_open_safe_doors` (tests `== "O"`), `_blocked_for`, `walkable`, `is_valid_step`, `has_line_of_sight`, and the hostile-on-safe guard all already treat `L` and `U` identically (closed) and `O` (open); a hostile is still blocked by an *open* safe door. Verified unchanged in §5. | `app/pathfinding.py`, `app/session.py` (no edits) |
| ICON-5 | **Pictorial canvas art** (§6): a new shared wooden-door renderer drawing all six states (brown vs green slab, 1–2 plank lines, a frame, a top-right padlock for `L`, and an ajar leaf + soft radial light — yellow normal / green safe — for `O`), with S (full) and E (greyed) palettes and legibility thresholds at the 8px minimum. Replaces `drawDoorGlyph` + `drawSafeDoorGlyph`. Art is drawn **over the floor base** (the existing contract). | `app/static/app.js` |
| ICON-6 | **Color palette** (§5): 18 new `T`/`:root` tokens (normal + safe, S + E tiers) replacing the amber/red + green-cross tokens. | `app/static/app.js`, `app/static/style.css` |
| ICON-7 | **Frontend state** (§7): `SAFE_STATES = ["L","U","O"]`, `validateSafe` (incl. legacy `"C"`→`"U"` coercion), `safeDoorStateAt` default `"L"`, `safeDoorColor`, and the paint-preview color. | `app/static/app.js` |
| ICON-8 | **Legend** (§8): six state chips — normal (locked / unlocked-closed / open) + safe (locked / unlocked-closed / open) — each chip a mini canvas swatch matching the canvas art. | `app/static/index.html`, `app/static/style.css`, `app/static/app.js` |
| ICON-9 | **GM Safe door tool** (§8.2): the sub-row gains **Lock** and **Unlock** (Mark/Unmark/Open/Close unchanged); the normal **Door** tool is confirmed (Unlock/Lock/Open/Close). | `app/static/index.html`, `app/static/app.js` |
| ICON-10 | **Tests + live proof** (§14): model + session (state machine, migration, wire) + frontend (six states at S/E) + e2e/qa steps. | `tests/*`, `scripts/*` |

**What does NOT change:** the cell vocabulary (`floor`/`wall`/`doorway`); the
**normal-door** state machine, its `DOOR_ACTIONS`, its `map.doors` wire
values, its error strings, and the `"not a normal door"` guard in `_on_door`
(byte-for-byte); the **hostile-on-safe-door** restriction and the
`HOSTILE_ON_SAFE_DOOR` guard (unchanged — §5); `_closed_doors` /
`_open_safe_doors` / `_blocked_for` / `walkable` / `is_valid_step` /
`has_line_of_sight` (unchanged — §5); the three-tier entity awareness model;
the explored-map S/E/H algorithm; the `players[]` shapes; the `fog` flag; the
`path`/`error` frame shapes; the sample-dungeon geometry.

---

## 2. Behavior statement

Given the owner's icon spec (§1.1):

1. **Every door is a pictorial wooden door**, drawn over its cell's floor
   base (the existing contract — a door is floor-based, not a wall). The
   **normal** door is **brown wood**; the **safe-room** door is **green
   wood**. Wood color is the at-a-glance "normal vs safe" discriminator; the
   state glyph (padlock / plain / light) is the "locked / closed / open"
   discriminator.

2. **Six renderable states, two per color family:**
   - **Normal** — `L`: brown slab + closed padlock, top-right. `U`: brown
     slab, no padlock. `O`: open brown leaf + **soft yellow** light filling
     the opening.
   - **Safe** — `L`: green slab + closed padlock, top-right. `U`: green slab,
     no padlock. `O`: open green leaf + **soft green** light.

3. **Safe doors now have a lock** (A1). A freshly marked safe door is **`L`**
   (locked+closed — the secure default). Only the GM can `lock`/`unlock`/
   `open`/`close`/`mark`/`unmark` a safe door; a **player can never act on
   one** (no player path, as today). A **locked or unlocked-closed** safe
   door is a wall to **everyone** (movement + LOS); an **open** safe door is
   walkable for `party`/`neutral` and still a wall to a `hostile` — the
   restriction is **unchanged** (§5).

4. **Legacy safe doors migrate** (`"C"`→`"U"`) so a map saved under the old
   build behaves as an *unlocked closed* safe door when loaded (AC7), never
   as locked (never "locked by surprise").

5. **The explored (E) tier** renders the same six states **greyed out**, but a
   **locked** door stays distinguishable from an **unlocked closed** door
   (a faint padlock mark / distinct value survives the desaturation — §5.3,
   AC14). The GM view and a player's in-sight (S) cells render full detail.

6. **The legend** names all six states with mini swatches that match the
   canvas art; the **GM Safe door tool** exposes Mark / Unmark / **Lock /
   Unlock** / Open / Close.

---

## 3. State model — the delta (ICON-1)

### 3.1 The `L`/`U`/`O` table for BOTH door kinds

Both `Grid.doors` (normal) and `Grid.safe` (safe) now use the **same** three
state chars. `L`/`U` are both **closed** (a wall for LOS + movement); `O` is
**open** (transparent for LOS, walkable — with the safe-door team
restriction). "Closed" is defined as *state ≠ `O`*; "locked" as *state ==
`L`* — exactly the normal-door definitions, now applied to safe doors too.

| Char | Normal door (`map.doors`) | Safe door (`map.safe`) | Closed? | Locked? | Render (S tier) |
|---|---|---|---|---|---|
| `L` | locked + closed (default) | locked + closed (**fresh-mark default**) | yes | yes | brown/green slab + padlock top-right |
| `U` | closed, unlocked | closed, unlocked (**legacy `"C"` migrates here**) | yes | no | brown/green slab, no padlock |
| `O` | open | open | no | no | open leaf + soft **yellow** (normal) / **green** (safe) light |

The **default for an unrecorded safe door is `"L"`**, mirroring the normal
door (`Grid.doors` unrecorded ⇒ `"L"`). The default for an unrecorded *normal*
door is unchanged (`"L"`).

### 3.2 `SAFE_DOOR_STATES` and the migration rule

```python
# app/models.py
DOOR_STATES = ("L", "U", "O")            # UNCHANGED
SAFE_DOOR_STATES = ("L", "U", "O")       # was ("C", "O")  -> now mirrors DOOR_STATES
SAFE_DOOR_LEGACY_STATE = "C"             # the pre-redesign closed char (migration source)
```

**Migration (the only backward-compat touch).** A `Grid` built **before** this
change stores `"C"` for a closed safe door. On load:

- **`Grid.from_dict`** (and the REST `map` parse) **coerces** any legacy
  `"C"` value in the `safe` object to `"U"` before validation:
  `safe = {k: ("U" if v == SAFE_DOOR_LEGACY_STATE else v) for k, v in raw_safe}`.
  After coercion the value is one of `L`/`U`/`O`, so `__post_init__`
  validation (which accepts `L`/`U`/`O`) passes. A `"C"` therefore **never**
  reaches the validated model or the wire — it is read as `"U"` and, on the
  next `to_dict`, is *emitted as `"U"`* (the migration is effectively
  self-healing on the first save/broadcast).
- **`to_dict`** is otherwise unchanged (emit `safe` as-is; it now only ever
  contains `L`/`U`/`O`).
- **Why `"U"` and not `"L"`:** the old `C` door was *always unlocked and
  closed*; migrating to `"U"` (unlocked closed) preserves the exact old
  gameplay (walkable-by-party/neutral once open, GM could open it). Migrating
  to `"L"` would silently **lock** a door the GM left open-unlocked — a
  gameplay regression. (A fresh *mark* is a different case: it has no prior
  behavior to preserve, and `"L"` is the intentional secure default, §3.4.)

**`__post_init__`** validation for `safe` is otherwise **unchanged** except it
now validates against `SAFE_DOOR_STATES = ("L","U","O")` (the key-form,
bounds, doorway-cell, and mutual-exclusion checks are all reused as-is).

### 3.3 The state-machine transition table for safe doors

Safe doors are **GM-only for all six actions** (there is no player path — same
as today, now including the lock). `mark`/`unmark` convert between a normal
door and a safe door; `unlock`/`lock`/`open`/`close` operate on an existing
safe door. The machine is the **normal-door machine (§4 of
door-features.md) plus the `mark`/`unmark` conversions**, with the safe
strings. `lock`-while-**open** **force-closes** (there is no "open and
locked" state — same as the normal door, A7 there).

The full `(state, action)` → outcome table. **Roles:** every action is GM-only
(a player sending any safe-door action gets `"not allowed"`, checked **first**
— the existing behavior). "→" = legal transition; a cell with a quoted string
= that exact error.

| Current | `mark` | `unmark` | `unlock` | `lock` | `open` | `close` |
|---|---|---|---|---|---|---|
| *(normal door)* | GM → safe, **`L`** | `"not a safe door"` | `"not a safe door"` | `"not a safe door"` | `"not a safe door"` | `"not a safe door"` |
| **`L`** (locked+closed) | `"already a safe door"` | GM → normal **`L`** | GM → **`U`** | `"safe door is already locked"` | `"safe door is locked"` | `"safe door is already closed"` |
| **`U`** (closed, unlocked) | `"already a safe door"` | GM → normal **`U`** | `"safe door is already unlocked"` | GM → **`L`** | GM → **`O`** | `"safe door is already closed"` |
| **`O`** (open) | `"already a safe door"` | GM → normal **`O`** | `"safe door is already unlocked"` | GM → **`L`** (force-closed) | `"safe door is already open"` | GM → **`U`** (occupancy-guarded) |

**`mark` details (the `mark`/`unmark` conversions):**
- **`mark`** a **normal** doorway → the cell becomes a safe door in state
  **`L`** (locked, the fresh default — §3.4). Any **recorded** normal-door
  state on that key is **dropped first** (mutual exclusion, I1 — the existing
  "drop the recorded normal state" line stays; only the *written* safe state
  changes from `"C"` to `"L"`). Occupancy-guarded: a token on the cell →
  `"cannot mark a safe door with a token on it"` (unchanged).
- **`unmark`** a safe door → the cell reverts to a **normal** door, **state
  preserved**: `L`→`"L"`, `U`→`"U"`, `O`→`"O"`. (Supersedes the old
  `C`→`"U"` / `O`→`"O"`. The preserved char is written to `Grid.doors`; the
  `safe` key is removed.)

### 3.4 The new fresh-mark default = `"L"` (rationale)

A freshly marked safe door is **locked+closed (`L`)**, not the old `C`.
Rationale: (a) the owner's spec lists "locked (closed)" as the *first/primary*
safe-door state and a padlock reads as "secure"; (b) it **mirrors the normal
door**, whose unrecorded/default state is locked — so a GM who marks a door
gets the same "it's shut until I open it" expectation on both kinds; (c) it
is *safe by default* in the security sense (a hostile or an unattended player
can't walk in until the GM unlocks). A **legacy** map's closed safe doors
still migrate to `"U"` (§3.2) because they had existing behavior; only the
*new* mark defaults to `L`.

### 3.5 Model-level accessor edits (on `Grid`)

Only the pieces that hard-code the old `"C"`/`"O"` or the reversion change;
everything else is reused:

```python
# app/models.py  (Grid)
def safe_door_state_at(self, x, y):
    # UNCHANGED logic; now returns "L"|"U"|"O" for a safe door, None else.
    ...

def is_safe_door_closed(self, x, y) -> bool:
    # UNCHANGED: st = safe_door_state_at(x,y); return st is not None and st != "O".
    # "L" and "U" are both closed (a wall) — pathfinding relies on this (§5).
    ...

def set_safe_door(self, x, y, state):
    # UNCHANGED; `state in SAFE_DOOR_STATES` now means L/U/O. The mutual-
    # exclusion check (a normal door recorded on the cell → ValueError) stays.
    ...

def unmark_safe_door(self, x, y):
    # CHANGED: preserve the safe state into the normal door (was C->"U"/O->"O"):
    st = self.safe[key]                 # "L" | "U" | "O"
    ... remove the safe key, materialize self.safe=None if empty ...
    self.doors = dict(self.doors or {})
    self.doors[key] = st                # L->"L", U->"U", O->"O" (preserved)
```

- **`door_state_at`, `is_door_closed`, `set_door`, `doors_for_wire`,
  `safe_for_wire`, `sync_doors_after_cell_set`: UNCHANGED.** `safe_for_wire`
  just emits `dict(self.safe)` (now `L`/`U`/`O`); `doors_for_wire` still skips
  safe cells (mutual exclusion) — `map.safe` and `map.doors` remain disjoint
  and jointly cover every doorway.
- **`set_safe_door`'s `mark`-conversion call site** (in `_on_safe_door`)
  passes `"L"` instead of `"C"` (§4).

### 3.6 Wire / REST — what changes on the wire

- **`map.safe`** is now an object `{"<x>,<y>": "L"|"U"|"O"}` (was
  `"C"|"O"`). It is emitted in full whenever ≥ 1 safe door exists, disjoint
  from `map.doors` (unchanged policy). **No new key.**
- **`map.doors`** is **byte-for-byte unchanged** (already `L`/`U`/`O`).
- **No new REST route.** The additive `safe` key in `GET /api/maps/{id}` /
  upload / generate simply now carries `L`/`U`/`O` for safe doors (fresh maps
  have no safe doors, so the `safe` key is absent — unchanged).
- A **client** (this frontend) also coerces a legacy `"C"` it might receive
  from a stale in-memory server (`validateSafe`, §7) so the render never sees
  an unknown char.

---

## 4. State machine + permissions (ICON-2)

### 4.1 The transition table

See **§3.3** — the full legal/illegal table is the source of truth. The two
state diagrams, for reference (safe door; GM-only on every edge):

```
  normal ──mark──▶ L ──unlock──▶ U ──open──▶ O
                    ▲             │           │
                    │             ▼           ▼
                    └──lock◀──────┘        lock(force-close)
  any safe ──unmark──▶ normal (state preserved: L→L, U→U, O→O)
```

- **`mark`**: normal doorway → safe door in **`L`** (GM-only, occupancy-guarded).
- **`unmark`**: safe door → normal door, **state preserved** (GM-only).
- **`unlock`**: `L` → `U` (GM-only). **`lock`**: `U` → `L`, `O` → `L`
  (force-closed, GM-only).
- **`open`**: `U` → `O` (GM-only). **`close`**: `O` → `U` (GM-only,
  occupancy-guarded).

Every `(state, action)` pair is **totally determined** (one legal transition
or a rejected error — no partial states).

### 4.2 Permission matrix (all GM-only)

| Action | GM | Player |
|---|---|---|
| `mark` | allowed (on a normal doorway, empty) | **`"not allowed"`** |
| `unmark` | allowed (on a safe door) | **`"not allowed"`** |
| `unlock` | allowed (from `L`) | **`"not allowed"`** |
| `lock` | allowed (from `U` or `O`) | **`"not allowed"`** |
| `open` | allowed (safe door in `U`) | **`"not allowed"`** |
| `close` | allowed (safe door in `O`, occupancy-guarded) | **`"not allowed"`** |

A player **cannot** do *anything* to a safe door — now including lock/unlock —
because the surface is **wholly GM-gated** (the role check runs **first**, as
today). There is no "unlocked ⇒ any client may open/close" rule for safe
doors (unlike normal doors); the GM manages the whole door.

### 4.3 Validation order (deterministic, pinned for tests)

`_on_safe_door` validates in this **exact** order (first failure wins), so the
error for any malformed/illegal request is deterministic and testable (AC3):

1. **GM-only gate first:** sender is not the GM → **`"not allowed"`**
   (unchanged; the safe-door surface has no player path).
2. `x`/`y` are ints (reject bools) → else **`"x and y must be integers"`**.
3. **Bounds** → else **`"destination out of bounds"`**.
4. **Cell must be a `doorway`** → else **`"not a doorway"`**.
5. `action` is one of `mark`/`unmark`/`unlock`/`lock`/`open`/`close` → else
   **`"action must be one of mark/unmark/unlock/lock/open/close"`**
   (**changed** — the old list was `mark/unmark/open/close`; a `lock`/`unlock`
   frame is now *valid*, and an unrecognized action gets the new list).
6. **Transition legality** (the §3.3 table) → else the state-specific error:
   `"already a safe door"`, `"not a safe door"`, `"safe door is locked"`,
   `"safe door is already unlocked"`, `"safe door is already locked"`,
   `"safe door is already open"`, `"safe door is already closed"`.
7. **Occupancy** → `mark` with a token on the cell →
   **`"cannot mark a safe door with a token on it"`** (unchanged); `close`
   with a token on the cell → **`"cannot close a door with a token on it"`**
   (unchanged string, reused). (`lock` from `open` force-closes but is **not**
   occupancy-guarded — the door was already open/walkable, matching the normal
   door's A5 nuance; a hostile/party on an open safe door is not affected
   because a hostile can't be *on* an open safe door anyway.)
8. Apply, broadcast, return `None` (the `state` broadcast carries the new
   `map.safe` and, for `mark`/`unmark`, the updated `map.doors`).

**Exact WS error strings (house style — lowercase, same vocabulary as the
normal-door strings, prefixed with "safe door is …" where the state is the
subject):**

| Case | `message` |
|---|---|
| non-GM sends any `safe_door` action | `not allowed` |
| `x`/`y` not int | `x and y must be integers` |
| out of bounds | `destination out of bounds` |
| cell not a doorway | `not a doorway` |
| bad/missing action | `action must be one of mark/unmark/unlock/lock/open/close` |
| `mark` on a cell already a safe door | `already a safe door` |
| `unmark`/`unlock`/`lock`/`open`/`close` on a **non-safe** doorway | `not a safe door` |
| `open` on a **`L`** (locked) safe door | `safe door is locked` |
| `unlock` when already unlocked (`U`/`O`) | `safe door is already unlocked` |
| `lock` when already locked (`L`) | `safe door is already locked` |
| `open` when already open (`O`) | `safe door is already open` |
| `close` when already closed (`L`/`U`) | `safe door is already closed` |
| `mark` with a token on the cell | `cannot mark a safe door with a token on it` |
| `close` with an entity on the cell | `cannot close a door with a token on it` |

> The **normal-`door` strings are byte-for-byte unchanged** (`"door is
> locked"`, `"door is already unlocked"`, `"door is already open"`, etc.). The
> safe strings intentionally add the `safe door is …` prefix so a GM can tell
> which door kind a toast refers to (and so the two handlers never collide).
> `"not a normal door"` (the `_on_door` guard for a safe cell) is unchanged.

### 4.4 The `_on_safe_door` handler (changed parts)

Only the state-branch logic changes; the role/bounds/doorway scaffolding is
reused. The `mark`/`unmark` conversions and the `unlock`/`lock`/`open`/`close`
state machine (pseudocode, mirroring `_on_door`):

```python
# app/session.py
SAFE_DOOR_ACTIONS = ("mark", "unmark", "unlock", "lock", "open", "close")  # was ("mark","unmark","open","close")
BAD_SAFE_ACTION = "action must be one of mark/unmark/unlock/lock/open/close"  # was "... mark/unmark/open/close"

# (role gate, x/y ints, bounds, doorway check — UNCHANGED — then:)
action = msg.get("action")
if action not in SAFE_DOOR_ACTIONS:
    return {"type": "error", "message": BAD_SAFE_ACTION}
is_safe = self.grid.is_safe_door(x, y)
key = f"{x},{y}"

if action == "mark":
    if is_safe:
        return err("already a safe door")
    if self._any_entity_at(x, y):
        return err("cannot mark a safe door with a token on it")
    if (self.grid.doors or {}).get(key) is not None:      # drop recorded normal state (I1)
        self.grid.doors = dict(self.grid.doors); del self.grid.doors[key]
    self.grid.set_safe_door(x, y, "L")                    # CHANGED: was "C" -> now "L"
elif action == "unmark":
    if not is_safe:
        return err("not a safe door")
    self.grid.unmark_safe_door(x, y)                       # CHANGED: now preserves L/U/O
else:
    if not is_safe:
        return err("not a safe door")
    cur = self.grid.safe_door_state_at(x, y)               # "L" | "U" | "O"
    if action == "open" and cur == "O":  return err("safe door is already open")
    if action == "open" and cur == "L":  return err("safe door is locked")
    if action == "close" and cur != "O": return err("safe door is already closed")
    if action == "close" and self._any_entity_at(x, y):
        return err("cannot close a door with a token on it")
    if action == "unlock" and cur != "L": return err("safe door is already unlocked")
    if action == "lock" and cur == "L":   return err("safe door is already locked")
    new_state = {("unlock","L"):"U", ("lock","U"):"L", ("lock","O"):"L",
                 ("open","U"):"O",   ("close","O"):"U"}[(action, cur)]
    self.grid.set_safe_door(x, y, new_state)
self._run_b(self._broadcast())
```

**The frozen `_on_door` guard is unchanged:** a `door` (normal) message on a
safe-door cell still returns `"not a normal door"` before the normal state
machine runs (mutual exclusion preserved; never fires for a non-safe cell).
The **hostile guard** (`HOSTILE_ON_SAFE_DOOR` = `"cannot place a hostile on a
safe room door"` in `_on_move`/`_on_place`/`_on_create_entity`/`_on_set_team`)
is **unchanged** — it keys off `is_safe_door` and `team == "hostile"`, never
off the safe *state*, so `L`/`U`/`O` don't affect it (§5).

### 4.5 The player-facing hint (unchanged semantics)

A **player** tapping a safe-door cell is still a **no-op** (GM-controlled).
The existing canvas hint "That safe door is closed — the GM controls it"
gates on `safeDoorStateAt(...) !== "O"` — which is now true for both `L` and
`U` (both closed) and false for `O`. **No logic change needed**; the string
is still correct (both locked and unlocked-closed safe doors are "closed —
GM controls it"). (A future nicety — distinguishing "locked" from "closed" in
the hint — is a non-goal.)

---

## 5. Color palette (ICON-6) — exact hex for BOTH visibility tiers

All hexes are chosen against the **floor `#efe9dc`** (the base every door is
drawn over) and the **wall `#3b4252`**. Constraints: (a) each family is
**distinct from floor, wall, and the other family**; (b) within a family the
three states are distinguishable by **shape** (padlock / plain / light) first,
color second; (c) the E tier is a flat grey family **value-distinct from the
explored floor `#6b7280`** and from one another, and a **locked** door stays
distinguishable from an **unlocked-closed** door.

### 5.1 S tier (full detail: GM view + in-sight cells)

| Token | Hex | Used by | Reason |
|---|---|---|---|
| `woodBrown` | `#9c6b3a` | normal slab fill (L/U) | Warm, clearly *wood* brown (relative luminance ~18% — a strong value contrast against the pale floor `#efe9dc` at ~82%). A doorway is a gap in a wall, so the slab abuts wall cells (`#3b4252`, ~5%); it stays legible there because the slab is **warm-brown vs cool blue-grey** (hue difference) and the floor-base inset (`margin`) leaves a **1px pale-floor frame** between slab and wall. Distinct from the old amber door `#d97706`/`#f59f00` (much more brown/less orange) and from party-green. |
| `woodBrownDark` | `#7a4f2a` | normal plank lines | One step darker than the slab (~11%); visible as grain lines at ≥12px without reading as a separate object at 8px. |
| `woodGreen` | `#4f9e6b` | safe slab fill (L/U) | A muted **moss/olive green** (rel. luminance ~27%; HSL ≈ 141° / 50% sat). Deliberately distinct from **both** references: the party-token green `#2f9e44` (≈131° / **70%** sat / ~25% lum — a vivid emerald) and the old safe-door mint `#3ddc84` (≈147° / 72% sat / ~**54%** lum — a bright light mint). The new slab is *less saturated* than the party token (50% vs 70%) and *far darker and less vivid* than the old mint, so it reads as "weathered wood", not a token and not the old cross. The decisive disambiguator from the party token is **shape + layer**: a safe door is a *square, framed slab in the map layer*, while a party token is a *round circle with a white ring rendered in the entity layer on top* — the project's "shape + color, never color alone" rule. So even a similar green value never reads as a token. |
| `woodGreenDark` | `#3c7d53` | safe plank lines | One step darker than the safe slab (grain). |
| `padlockBody` | `#e6b422` | padlock body fill (L, both kinds) | A **gold/brass** — reads instantly as a padlock, and is a warm value distinct from both wood slabs (so it pops on brown and green alike). Chosen over a steel grey (which would vanish into the grey E family and clash with the wood) and over red (which reserved "danger/locked" for the *old* art; here the *glyph* is the lock signal, the color is "metal"). |
| `padlockShackle` | `#8a8f98` | padlock shackle stroke (L) | A cool **steel grey** ring so the shackle reads as separate metal from the brass body; visible on both wood tones and on the floor. |
| `lightYellow` | `#ffe9a8` | normal open glow (O) | A **soft warm yellow** light (the "lit room" the owner asked for). Very high value on the pale floor (it glows, doesn't fight the base) and warm — clearly "daylight/lamplight", distinct from the green safe light. |
| `lightGreen` | `#c9f2d4` | safe open glow (O) | A **soft mint-green** light. High value, warm-biased green; clearly distinct from the `lightYellow` and from the slab green (this is a *glow*, low saturation + high value). |
| `frame` | `#5b4327` | normal door frame/outline | A dark warm brown ring framing the brown slab — anchors the slab to the cell edge and separates it from the floor; reads as the door frame. |
| `frameGreen` | `#2f5c40` | safe door frame/outline | A dark green ring framing the green slab (the "door frame" for the safe door), matching `woodGreen` but darker. |
| `doorShadow` | `rgba(0, 0, 0, 0.18)` | 1px inner shadow along slab bottom/right (L/U, both kinds) | A subtle ambient shadow that gives the slab depth against the flat floor; low alpha so it never darkens the slab into the wall range. Applied identically to both families. |

**Why not reuse the old tokens:** the old art was *abstract* (an amber arch, an
amber bar, a red padlock notch, a green cross). The owner explicitly found it
obscuse. The new tokens are all *new* and the old door tokens
(`doorOpen`/`doorUnlocked`/`doorLocked`/`safeOpen`/`safeClosed` and their
`explored*` variants) are **removed** from both `T` and `:root` and replaced
by the set below. (`--doorway` `#d97706` is **kept** — it still styles the
"Doorway" paint tool + the plain "doorway" legend chip + the preview
thumbnail; it is *not* a door-state color.)

### 5.2 E tier (explored/greyed memory)

The E palette is a **flat grey family** matching the existing explored ramp
(floor `#6b7280`, wall `#4b5563`). Same art, desaturated — but the
*discriminators survive*: **value + a faint padlock mark** carry "locked vs
unlocked-closed vs open" in grey. All E slab/leaf values are **lighter than
the explored floor `#6b7280`** so a greyed door still reads as "a door in a
gap" (lighter-than-floor, like the S-tier slab is mid-value-on-pale-floor).

| Token | Hex | Used by | Reason |
|---|---|---|---|
| `eWoodSlab` | `#8a94a0` | normal slab (L/U) E — a greyed brown | Mid-light cool grey (L≈58) — clearly lighter than `#6b7280` floor, darker than the open-leaf glow; reads as a solid closed door slab in memory. |
| `eWoodSlab` | `#8a94a0` | safe slab (L/U) E — a greyed green, **same token/value** as normal | **Same grey as normal** (greying intentionally removes the brown/green hue — in memory a safe door and a normal door are both "a door"; the GM and S cells keep the hue). Kept separate tokens so a future per-family E tint is a one-line change. |
| `eSlabFrame` | `#5f6874` | closed-slab frame (L/U) E | A darker grey frame ring (value between the explored floor and the slab) so the slab edge still frames in grey. |
| `ePadlockMark` | `#cfd4db` | the surviving **locked** mark (L) E | A **light** grey padlock glyph (value-distinct, *lighter*, than the `#8a94a0` slab) — this is the deliberate "locked ≠ unlocked-closed" signal in grey: a locked door keeps a **faint padlock** (light on the mid-grey slab), an unlocked-closed door has **no** mark. The lightness (not hue) is the discriminator, so it survives full desaturation. |
| `eLight` | `#e8ecf0` | normal open glow (O) E — a greyed yellow | A **very light** grey (near-white) fill — a greyed "lit opening". High value + the *open-leaf* geometry (vs a full slab) makes "open" unmistakable even in grey. |
| `eLight` | `#e8ecf0` | safe open glow (O) E — **same token/value as the normal glow**: greying merges the two open glows into one near-white; the open-leaf geometry + high value carry "open" in memory. | Same near-white glow (greying merges the two open glows — in memory both are "an open, lit door"; the leaf geometry + high value carry it). |

**E-tier distinguishability rule (pinned, AC14):** at any cell size, in the E
tier, a **locked** door (`L`) draws the `ePadlockMark` faint padlock and an
**unlocked-closed** door (`U`) draws **no** mark — both use the `#8a94a0`
slab. So **locked vs unlocked-closed** is distinguished by the **padlock mark
presence**, and **closed vs open** by **slab vs light-leaf**. This is
shape/value-based, not hue-based, so it holds after desaturation.

### 5.3 The full token set — mirror in `T` and `:root`

`app/static/app.js` `T` (canvas) and `style.css` `:root` (legend/UI) both get
exactly these, and **only** these, replacing the old door tokens:

```js
// app/static/app.js — T (canvas palette)
woodBrown:     "#9c6b3a", woodBrownDark: "#7a4f2a",   // normal slab + planks (S)
woodGreen:     "#4f9e6b", woodGreenDark: "#3c7d53",   // safe slab + planks (S)
padlockBody:   "#e6b422", padlockShackle: "#8a8f98",  // padlock (L) (S)
lightYellow:   "#ffe9a8",                            // normal open glow (O) (S)
lightGreen:    "#c9f2d4",                            // safe open glow (O) (S)
frameBrown:    "#5b4327", frameGreen: "#2f5c40",     // slab frames (S)
doorShadow:    "rgba(0,0,0,0.18)",                   // slab inner shadow (S)
eWoodSlab:     "#8a94a0",                            // both families' E slab (L/U)
eSlabFrame:    "#5f6874",                            // E slab frame (L/U)
ePadlockMark:  "#cfd4db",                            // E faint padlock (L) — the locked signal
eLight:        "#e8ecf0",                            // both families' E open glow (O)
```

```css
/* style.css :root — the same 14 as CSS custom properties (legend + UI); no
   CSS equivalent for doorShadow (canvas-only) or the two preview tokens. */
--door-wood-brown: #9c6b3a;    --door-wood-brown-dark: #7a4f2a;
--door-wood-green: #4f9e6b;    --door-wood-green-dark: #3c7d53;
--door-padlock: #e6b422;       --door-padlock-shackle: #8a8f98;
--door-light-yellow: #ffe9a8;  --door-light-green: #c9f2d4;
--door-frame-brown: #5b4327;   --door-frame-green: #2f5c40;
--door-e-slab: #8a94a0;        --door-e-slab-frame: #5f6874;
--door-e-padlock: #cfd4db;     --door-e-light: #e8ecf0;
```

(**Count:** the `T` object gains **15** core art tokens (the `T` entries
above — of which `eWoodSlab` and `eLight` are each shared across the normal/
safe families by design) + **2** preview tokens (`doorWoodPreview`/
`safeWoodPreview`, §7.2) = **17** `T` entries total. `style.css` gains the same
15 **minus** `doorShadow` (canvas-only) and the 2 preview tokens = **14** CSS
custom properties, all used by the `.door-swatch` legend / paint-tool cursor.

---

## 6. Per-state canvas geometry (ICON-5) — precise, implementable drawing

### 6.0 The contract and the cell coordinates (unchanged base)

- A door is drawn by the **doorway pass** in `drawGridOnCanvas`, for each cell
  with `g.cells[y][x] === "doorway"` and tier `t` = `"S"` or `"E"` (tier `"H"`
  is skipped — a hidden door is not drawn, as today). The cell origin is
  `(px, py)` and the cell size is `s` (CSS px; the preview floor allows `s`
  down to 4, the live map floors it at **8**).
- **Floor base contract (KEEP):** before drawing any door glyph, the cell has
  **already** been filled with its tier's floor color and its grid line
  (step 1 of `drawGridOnCanvas` does this for every S/E cell, including
  doorways). The door art is drawn **ON TOP of the floor base** — exactly as
  today's doors do (today: floor base + state border + glyph). A door is
  **floor-based** (no wall hatch, no wall fill). **Do not change this** — the
  door art is a "sticker" over the floor, not a replacement cell fill.
- The old code set `ctx.lineWidth = Math.max(2, Math.min(3, s/8))` and then
  `Math.max(1.5, s/24)` for the glyph, and `strokeRect(px+1.5, py+1.5, s-3,
  s-3)`. The new art uses its **own** line widths per element (§6.4) and draws
  within `[px, px+s]×[py, py+s]`. The **state color** is chosen by a
  single dispatcher (§6.5) that the render loop calls; the dispatcher returns
  a **palette object** for the tier + state, and one glyph function draws it.

**Shared geometry helpers (defined once, used by both families):**

```
cx = px + s/2 ; cy = py + s/2            // cell center
margin = max(1, s*0.10)                  // uniform inset from the cell edge
slabX = px + margin ; slabY = py + margin
slabW = s - 2*margin ; slabH = s - 2*margin   // the wooden slab rect
```

`margin` scales with `s` (10%) and is floored at 1px so at `s=8` the slab is
`[px+1, py+1, 6×6]` (a 6×6 slab in an 8×8 cell — 1px of floor shows as a
frame gap, keeping the "floor base under the door" contract visible).

### 6.1 State `L` — closed + locked (normal = brown, safe = green)

Draw a **wooden slab** with a **frame**, **plank lines**, an **inner shadow**,
and a **closed padlock in the top-right corner**.

```
1. Frame (draw first, as the slab's outline):
   ctx.lineWidth = frameW(s) = max(1, round(s*0.08))
   ctx.strokeStyle = (normal ? frameBrown : frameGreen)
   ctx.strokeRect(slabX+0.5, slabY+0.5, slabW-1, slabH-1)

2. Slab fill (the wood):
   ctx.fillStyle = (normal ? woodBrown : woodGreen)
   ctx.fillRect(slabX, slabY, slabW, slabH)

3. Plank lines — only when s >= 12 (see §6.4 "small-size" rule):
   ctx.lineWidth = max(1, s*0.05)
   ctx.strokeStyle = (normal ? woodBrownDark : woodGreenDark)
   // one vertical mid-seam (a door plank divider) + one horizontal seam:
   line at x = slabX + slabW/2      from slabY to slabY+slabH
   line at y = slabY + slabH*0.55   from slabX to slabX+slabW
   (two thin lines read as "wood grain / planks"; at s=8 they are dropped, §6.4)

4. Inner shadow (depth, S tier only; skip at E — greyed art has no shadow):
   ctx.strokeStyle = doorShadow
   ctx.lineWidth = 1
   // bottom edge and right edge of the slab (a light-from-top-left convention):
   line (slabX, slabY+slabH-1) -> (slabX+slabW, slabY+slabH-1)
   line (slabX+slabW-1, slabY) -> (slabX+slabW-1, slabY+slabH)

5. Padlock (top-right corner) — the LOCKED signal:
   pad = drawPadlock(ctx, slabX + slabW - padSize, slabY, padSize, palette)
   where padSize = s*0.42 (floored at 4)  // occupies the top-right quadrant
   (see §6.3 for the exact padlock geometry)
```

**Geometry invariants (pinned):** the padlock's bounding box is
`[slabX+slabW-padSize, slabY, slabX+slabW, slabY+padSize]` — its **right edge
coincides with the slab's right inner edge** and its **top edge with the slab's
top inner edge**, with a `padGap = max(0.5, s*0.05)` breathing gap so the
padlock body never clips the frame (§6.3). The padlock is entirely inside the
slab rect, so it never overlaps the frame stroke.

### 6.2 State `U` — closed, unlocked (brown / green slab, NO padlock)

**Identical to state `L` minus the padlock** (steps 1–4 of §6.1; skip step 5).
The **absence** of the padlock is the "unlocked, closed" signal — the slab is
the same wood, so the only difference from `L` is the missing top-right
padlock. (This is the whole point of A1: `U` and `L` must differ *only* by the
padlock, and the padlock must be the unambiguous tell.)

### 6.3 The padlock glyph (shared, used by both families' `L`)

A **closed padlock**: a rectangular **body** with a **shackle** arc rising out
of the top, drawn **closed** (the shackle's two legs are straight down into
the body — no gap). It is a **fill + stroke** glyph (not just an outline) so
it reads at 8px.

```
Given the padlock's bounding box (x0, y0) top-left, size p (= padSize):
  // proportions of p:
  bodyX   = x0 + p*0.22        bodyW = p*0.56     // body = lower ~55% width band
  bodyY   = y0 + p*0.42        bodyH = p*0.58     // body = lower ~58% height
  shW     = p*0.44             // shackle band width
  shX0    = x0 + (p - shW)/2   // centered over the body
  shTop   = y0 + p*0.12        // top of the arc

1. Shackle (draw first, BEHIND the body top): a thick rounded arc.
   ctx.lineWidth = max(1.5, p*0.18)
   ctx.strokeStyle = padlockShackle
   ctx.beginPath()
   ctx.moveTo(shX0, bodyY)                       // left leg, from body top up
   ctx.lineTo(shX0, shTop + shW/2)
   ctx.arc(shX0 + shW/2, shTop + shW/2, shW/2, Math.PI, 0, false)  // top arc
   ctx.lineTo(shX0 + shW, bodyY)                 // right leg, down to body top
   ctx.stroke()

2. Body (in front): a rounded rect.
   ctx.fillStyle = padlockBody
   roundRect(ctx, bodyX, bodyY, bodyW, bodyH, p*0.12)
   ctx.fill()

3. Keyhole (only when p >= 10, i.e. s >= ~24): a tiny darker dot + slit.
   ctx.fillStyle = "rgba(0,0,0,0.45)"
   small circle at (bodyX+bodyW/2, bodyY+bodyH*0.38) r=p*0.06
   + a 1px slit down to bodyY+bodyH*0.7
   (at p < 10 the body is a solid brass block — the keyhole is dropped, §6.4)
```

**Why "closed":** the shackle legs are drawn **straight down into the body top**
(no horizontal gap between leg and body) — a locked shackle. (An "open" padlock
would have the top arc lifted off the body; we never draw that — a door's
padlock is always the *closed/locked* variant because a locked door is, by
definition, closed.) The **brass body on a cool-grey shackle** is the visual
"padlock" signature; on the pale floor and both wood tones the brass `#e6b422`
is the highest-contrast element in the cell, so the eye lands on it.

**At the E tier** the padlock is drawn the **same way** but with a single flat
`ePadlockMark` color for **both** body and shackle (`padlockBody` and
`padlockShackle` both → `#cfd4db`), no keyhole, no inner shadow — a faint light
padlock mark (§5.2). This is the "locked vs unlocked-closed in grey" signal.

### 6.4 Small-size degradation (MUST read at 8px, look good to ~60px)

The art is a single function of `s`. The following **thresholds** drop detail
as `s` shrinks (all measured in CSS px); nothing is ever drawn off-cell:

| Element | Shown when | At 8px | At 12px | At 16px+ | At ~60px |
|---|---|---|---|---|---|
| Frame outline | always | 1px | 1px | 1–2px | 2–3px |
| Slab fill | always | 6×6 | 9×9 | 14×14 | 54×54 |
| Plank lines (2) | **`s >= 12`** | **dropped** | 1px (1 seam) | 1px (both seams) | 1–2px |
| Inner shadow (S) | **`s >= 14`** | dropped | dropped | 1px | 1–2px |
| Padlock (L) | always (min `p=4`) | 4×4 (solid brass + shackle) | 6×6 | ~8×8 | ~25×25 |
| Padlock keyhole | **`p >= 10`** (≈`s>=24`) | dropped | dropped | dropped | shown |
| Open glow (O) | always | radial dot | radial | radial | radial |
| Ajar leaf (O) | **`s >= 10`** | dropped (glow fills) | shown | shown | shown |

Consequences at the **8px minimum** (the common case for a 60-cell map on a
typical viewport): each closed door is a **slab color square (brown or green)
with a 1px frame** and, for `L`, a **4px brass padlock dot** in the top-right;
each open door is a **radial light glow** (yellow/green, or grey at E) filling
the slab with a thin darker frame. All six states are still **distinguishable
at 8px**: brown-square vs green-square (family), padlock-dot vs no-dot (L vs
U), glow vs solid-slab (O vs closed). At ~60px the same six states read as
full pictorial doors (slab + planks + frame + shadow, a proper padlock with a
keyhole, or a glowing open leaf). **This satisfies "reads at 8px AND looks
good up to ~60px"** by progressive enhancement (more detail at larger `s`),
never by re-coloring (the palette is fixed per state/tier regardless of `s`).

### 6.5 State `O` — open (normal = soft yellow light, safe = soft green light)

The slab is **gone** — the door is open, so the **floor base shows through**
and the opening is filled with a **soft light glow**, with an **ajar leaf**
suggesting a swung-open door. Draw order (over the floor base):

```
glowColor = normal ? lightYellow : lightGreen        // S tier
glowColor = eLight (both)                            // E tier (greyed near-white)

1. Light glow (fills the opening, radial — bright center, soft edge):
   ctx.save()
   ctx.beginPath(); ctx.rect(slabX, slabY, slabW, slabH); ctx.clip()
   g = ctx.createRadialGradient(cx, cy, s*0.05, cx, cy, s*0.62)
   g.addColorStop(0,   glowColor)                    // bright core
   g.addColorStop(0.55, glowColor)                   // held out (a soft disc)
   g.addColorStop(1,   "rgba(255,255,255,0)")        // fade to transparent at the slab edge
   ctx.fillStyle = g
   ctx.fillRect(slabX, slabY, slabW, slabH)
   ctx.restore()

2. Ajar leaf (only when s >= 10, §6.4): a small angled slab hinged on the LEFT
   edge, swung ~55° into the opening (so it reads "door swung open to the left",
   leaving the right ~60% as lit opening). Drawn as a thin parallelogram:
   ctx.lineWidth = max(1, s*0.08)
   ctx.strokeStyle = (normal ? frameBrown : frameGreen)
   // hinge at the slab's left edge, mid-height:
   hx = slabX ; hy = cy
   // leaf tip swung up-left into the opening:
   tipX = slabX + slabW*0.42 ; tipY = cy - slabH*0.30
   ctx.beginPath(); ctx.moveTo(hx, cy - slabH*0.42); ctx.lineTo(hx, cy + slabH*0.42);
   ctx.lineTo(tipX, tipY + slabH*0.20); ctx.lineTo(tipX, tipY - slabH*0.10); ctx.closePath()
   ctx.fillStyle = (normal ? woodBrown : woodGreen)  // a sliver of the slab, edge-on
   ctx.fill(); ctx.stroke()
   (at s < 10 the leaf is dropped and the glow alone reads "open / lit".)

3. Frame: the slab's frame outline is still drawn (steps in §6.1 step 1) so the
   opening has a door-frame boundary; the glow is clipped *inside* it, so the
   frame reads as the door's frame and the glow as the lit room beyond.
```

**Geometry invariants (pinned):** the glow is **clipped to the slab rect**, so
the light never bleeds past the door frame into the cell's floor or onto
neighbors (no halo overlap). The radial gradient's transparent outer stop
keeps the **floor base** visible at the slab's corners (the "soft" edge the
owner asked for — a soft light, not a hard rectangle of color). The ajar leaf
is drawn **over** the glow (the door in front of the light) and is a **sliver of
the same wood** as the closed slab, so the family color is preserved in the
open state. The **normal vs safe open** difference is the **glow hue**
(yellow vs green) — and the leaf wood (brown vs green) — both consistent with
the closed states.

### 6.6 The single dispatcher + render-loop wiring

`drawDoorGlyph` / `drawSafeDoorGlyph` are **replaced by one function**
`drawDoorCell(ctx, kind, state, px, py, s, t)` (kind ∈ `normal`|`safe`,
state ∈ `L`/`U`/`O`, t ∈ `S`/`E`). The doorway pass becomes:

```js
for (y..) for (x..) {
  if (g.cells[y][x] !== "doorway") continue;
  const t = tier(x, y);
  if (t === "H") continue;
  const px = ox + x*s, py = oy + y*s;
  const kind = isSafeDoor(x, y) ? "safe" : "normal";
  const state = (kind === "safe")
      ? (safeDoorStateAt(x, y) || "L")
      : (doorStateAt(x, y)      || "L");
  drawDoorCell(ctx, kind, state, px, py, s, t);
}
```

`drawDoorCell` selects the tier palette (S: the §5.1 tokens; E: the §5.2
tokens) and dispatches on `state`: `L` → slab + planks + shadow + padlock;
`U` → slab + planks + shadow (no padlock); `O` → glow + ajar leaf + frame. It
sets `ctx.fillStyle`/`ctx.strokeStyle`/`ctx.lineWidth` per element and **does
not** leave global state set (save/restore around the gradient clip). The old
`strokeRect(px+1.5, py+1.5, s-3, s-3)` border line is **replaced** by the
slab's frame (the frame *is* the door's edge now, at the slab inset — visually
equivalent to today's 1.5px-inset border, so the door still sits inside its
cell). **`doorColor`/`safeDoorColor` are removed** (their only other use — the
`mode-paint-door` hover preview — is re-pointed to a fixed preview color, §7.4).

---

## 7. Frontend state + validation (ICON-7)

### 7.1 `SAFE_STATES`, `validateSafe`, `safeDoorStateAt` (app.js)

```js
const SAFE_STATES = ["L", "U", "O"];          // was ["C", "O"]

function validateSafe(safe) {
  if (safe == null) return {};
  if (typeof safe !== "object" || Array.isArray(safe)) return {};
  const clean = {};
  for (const key of Object.keys(safe)) {
    if (!/^[0-9]+,[0-9]+$/.test(key)) return {};
    let v = safe[key];
    if (v === "C") v = "U";                    // LEGACY migration (mirrors server)
    if (SAFE_STATES.indexOf(v) === -1) return {}
    clean[key] = v;
  }
  return clean;
}

// safeDoorStateAt: UNCHANGED shape, but the DEFAULT flips "C" -> "L":
function safeDoorStateAt(x, y) {
  if (!isSafeDoor(x, y)) return null;
  return state.safe[`${x},${y}`] || "L";       // was || "C"
}
```

- **`isSafeDoor`** (membership in `state.safe`) is **unchanged**.
- **`doorStateAt` / `validateDoors` / `DOOR_STATES`** are **unchanged**
  (normal doors already use `L`/`U`/`O`; default `"L"`).
- The **legacy `"C"`→`"U"` coercion** in `validateSafe` mirrors the server's
  `from_dict` migration (§3.2), so even a **stale in-memory server** (still
  holding `C` from before the reload) sends a value the render understands;
  after the first `to_dict`/broadcast the server emits `U` and the wire is
  clean. A `C` is thus **never** rendered.

### 7.2 `safeDoorColor` / `doorColor` — removed

Both color functions are **deleted** (the art is now driven by
`drawDoorCell`'s tier palette, §6.6). Their **one other consumer** — the
`mode-paint-door` / `mode-paint-safeDoor` **hover preview** in
`drawEntitiesAndDots`:

```js
const fill = state.tool === "wall" ? T.wallFill
           : state.tool === "doorway" ? T.doorway
           : state.tool === "door" ? T.doorWoodPreview      // new: fixed brown
           : state.tool === "safeDoor" ? T.safeWoodPreview  // new: fixed green
           : T.floor;
```

gets **two fixed preview tokens** (the *wood* color of each family — not a
state color, since a paint preview is "this is a door of this kind", not "this
is a locked door"):

```js
doorWoodPreview: "#9c6b3a",   // == woodBrown
safeWoodPreview: "#4f9e6b",   // == woodGreen
```

(Reuses the wood tokens; declared as their own two entries for clarity.) This
removes the last state-dependent use of the old color functions, so the whole
old amber/red + green-cross color path is gone.

### 7.3 `applyState` — unchanged

`applyState` already does `state.safe = validateSafe(msg.map?.safe)` and
`state.doors = validateDoors(msg.map?.doors)`. With `validateSafe` now
accepting `L`/`U`/`O` (+ legacy `C`), **no `applyState` change is needed** —
the new wire values flow through unchanged. `state.safeAction` (the armed GM
safe action, default `"mark"`) is **unchanged** in shape; the sub-button set
grows (§8.2).

### 7.4 The click handler — safe tool sends the armed action (unchanged path)

`sendSafeDoor(x, y, action)` → `wsSend({type:"safe_door", x, y, action})` is
**unchanged**; the GM Safe door tool now offers six armed actions
(Mark/Unmark/Lock/Unlock/Open/Close) but the send shape is identical. The
**player-tap** branch (`if (!gm && t === "doorway" && !hit) { if (isSafeDoor..){
no-op + hint} ... }`) is **unchanged** (a player still can't act on a safe
door; the `!== "O"` hint condition is correct for `L`/`U`/`O`, §4.5). The
normal-door player tap (inverse `open`/`close`) is **unchanged**.

---

## 8. Legend + tool UI (ICON-8, ICON-9)

### 8.1 Legend chips — all six states, mini-canvas swatches

The door legend currently shows three normal chips (`door-open`/`door-unlocked`/
`door-locked`) + one safe chip (`safe-door` "green cross"). Replace with **six
chips** — three normal + three safe — each a **mini canvas swatch** that
renders the *actual* `drawDoorCell` art for that state at a fixed small size
(≈16px), so the legend is **pixel-identical to the map art** (the owner wants
intuitive icons; a colored rectangle would be exactly the "obscure" they're
escaping). **Visible to BOTH roles** (GM edits; a player must read the states)
— the chips are **not** gated by `body.is-gm` (as the current door chips
aren't).

`index.html` — replace the `legend-doors` + `legend-safe` chip blocks with:

```html
<span class="legend-sep legend-doors">|</span>
<span class="legend-chip legend-doors"><i class="door-swatch" data-kind="normal" data-state="L"></i>door · locked</span>
<span class="legend-chip legend-doors"><i class="door-swatch" data-kind="normal" data-state="U"></i>door · closed</span>
<span class="legend-chip legend-doors"><i class="door-swatch" data-kind="normal" data-state="O"></i>door · open</span>
<span class="legend-sep legend-safe">|</span>
<span class="legend-chip legend-safe"><i class="door-swatch" data-kind="safe" data-state="L"></i>safe · locked</span>
<span class="legend-chip legend-safe"><i class="door-swatch" data-kind="safe" data-state="U"></i>safe · closed</span>
<span class="legend-chip legend-safe"><i class="door-swatch" data-kind="safe" data-state="O"></i>safe · open</span>
```

**Rendering the swatches (app.js, new `renderLegendDoorSwatches()`):** after
`showView("map")` (and once on load), for each `.door-swatch` element:
create a child `<canvas width=16 height=16>`, draw the floor base
(`T.floor`, or `T.exploredFloor` — the S-tier swatch is drawn; we render the
**S tier** so the legend shows the true colors), then call
`drawDoorCell(ctx, kind, state, 0, 0, 16, "S")`. Cache nothing (6 tiny
draws, once). `data-kind` → `"normal"`/`"safe"`, `data-state` → `L`/`U`/`O`.
Because `drawDoorCell` is a pure function of `(kind, state, px, py, s, t)`,
the legend swatch is **guaranteed** to match the map.

**CSS:** `.door-swatch` is a `display:inline-block; width:16px; height:16px;
border-radius:2px;` box that holds the canvas (no `background` needed — the
canvas draws the floor). **Remove** the old `.swatch.door-open` /
`.swatch.door-unlocked` / `.swatch.door-locked` / `.swatch.safe-door` rules
(and their `::before`/`::after` cross) — they are replaced by the canvas
swatches. The plain `.swatch.doorway` chip (the "Doorway" *paint tool* chip)
**stays** (it references `--doorway`, still used by the paint tool).

> The old safe chip's **"green cross"** label + the `--safe-open`/`--explored-
> safe-open` tokens are removed (the cross no longer exists). The six chips
> are the full state vocabulary; the GM and player see the same six.

### 8.2 GM tools — sub-buttons

**Normal "Door" tool — CONFIRMED unchanged** (it already has the four state
actions the owner's spec implies for normal doors): `#door-action-row` keeps
**Unlock / Lock / Open / Close** (`data-door-action` = `unlock`/`lock`/`open`/
`close`). No change.

**Safe door tool — gains Lock + Unlock.** `#safe-action-row` is extended from
four to **six** sub-buttons, in this order (mark/unmark are the "kind"
conversions first, then the state actions):

```html
<span id="safe-action-row" class="safe-action-row" hidden>
  <button class="safe-action" data-safe-action="mark"   aria-pressed="true">Mark</button>
  <button class="safe-action" data-safe-action="unmark" aria-pressed="false">Unmark</button>
  <button class="safe-action" data-safe-action="unlock" aria-pressed="false">Unlock</button>
  <button class="safe-action" data-safe-action="lock"   aria-pressed="false">Lock</button>
  <button class="safe-action" data-safe-action="open"   aria-pressed="false">Open</button>
  <button class="safe-action" data-safe-action="close"  aria-pressed="false">Close</button>
</span>
```

- **Wiring unchanged:** `setSafeAction(action)` already reads
  `data-safe-action`, sets `state.safeAction`, and toggles `aria-pressed` over
  `$$("#paint-group .safe-action")` — the two new buttons need **no JS
  change** (they match the existing class + `data-safe-action` selector).
  `sendSafeDoor(x, y, state.safeAction)` sends whatever is armed. The default
  armed action stays `"mark"`.
- **CSS:** `.safe-action` styling is reused verbatim (the two new buttons
  inherit the exact compact-button look + the `--safe-open`-colored
  `aria-pressed` underline). The row may wrap on narrow widths (it already
  `inline-flex`es); six buttons fit the bottom bar at the existing `--s1`/`--s2`
  padding (the control bar is `flex-wrap: wrap`).
- **Control hint** (`updateControlHint`): `Click a doorway to ${state.safeAction}`
  already interpolates the armed action — now it can say "…to lock", "…to
  unlock" (no change needed).

---

## 9. File-by-file change table (ICON-10 — the build map)

| File | Change | Detail |
|---|---|---|
| `app/models.py` | **Modify** | `SAFE_DOOR_STATES = ("L","U","O")` (was `("C","O")`); add `SAFE_DOOR_LEGACY_STATE = "C"`. `Grid.from_dict`: coerce legacy `safe` values (`C`→`U`) **before** passing to the constructor (one dict-comprehension line); `__post_init__` then validates `L`/`U`/`O` (no logic change). `unmark_safe_door`: reversion **preserves** state (`L`→`L`,`U`→`U`,`O`→`O`) instead of `C`→`U`/`O`→`O` (one expression). `safe_door_state_at`, `is_safe_door_closed`, `set_safe_door`, `is_safe_door`, `doors_for_wire`, `safe_for_wire`, `sync_doors_after_cell_set`: **unchanged** (they already key off `!= "O"` / dict membership). `DOOR_STATES`, all `doors` accessors: **unchanged**. |
| `app/session.py` | **Modify** | `SAFE_DOOR_ACTIONS = ("mark","unmark","unlock","lock","open","close")` (was 4). `_on_safe_door`: the `mark` branch writes `"L"` (was `"C"`); add the `unlock`/`lock` state checks + the `(state,action)→new_state` map (`{"unlock":"U","lock":"L","open":"O","close":"U"}` keyed by the legal `cur`); the bad-action message is now `mark/unmark/unlock/lock/open/close`. The role gate (first), bounds, doorway check, `mark` occupancy, `close` occupancy, and the broadcast are **unchanged**. `_on_door`, its `"not a normal door"` guard, `DOOR_ACTIONS`, and all normal-door strings: **unchanged**. The `HOSTILE_ON_SAFE_DOOR` guards in `_on_move`/`_on_place`/`_on_create_entity`/`_on_set_team`: **unchanged** (key off `is_safe_door` + `team`, not state). |
| `app/pathfinding.py` | **No change** | `_closed_doors` (safe branch `safe[key] != "O"` → `L` and `U` both closed), `_open_safe_doors` (`== "O"`), `_blocked_for`, `walkable`, `is_valid_step`, `has_line_of_sight`: **byte-for-byte unchanged** and **correct as-is** for `L`/`U`/`O` — verified in §5. |
| `app/server.py` | **No change** | The REST `safe`/`doors` emission reads `grid.safe`/`grid.doors` (now `L`/`U`/`O`) — no code change; the wire values just flow through. No new route. |
| `app/static/app.js` | **Modify** | (a) `T`: **remove** `doorOpen`/`doorUnlocked`/`doorLocked`/`safeOpen`/`safeClosed` + their `explored*` variants; **add** the §5.3 tokens (`woodBrown`/`woodBrownDark`/`woodGreen`/`woodGreenDark`/`padlockBody`/`padlockShackle`/`lightYellow`/`lightGreen`/`frameBrown`/`frameGreen`/`doorShadow`/`eWoodSlab`/`eSlabFrame`/`ePadlockMark`/`eLight`) + `doorWoodPreview`/`safeWoodPreview`. (b) `SAFE_STATES = ["L","U","O"]`; `validateSafe` accepts `L/U/O` + coerces legacy `C`→`U`; `safeDoorStateAt` default `"L"`. (c) **Delete** `drawDoorGlyph`, `drawSafeDoorGlyph`, `doorColor`, `safeDoorColor`; **add** `drawDoorCell` (§6.6) + the shared padlock/slab/glow helpers; rewire the doorway pass to `drawDoorCell`; re-point the hover preview to `doorWoodPreview`/`safeWoodPreview`. (d) `renderLegendDoorSwatches()` (new, §8.1) called from `showView("map")`. `applyState`, `isSafeDoor`, `doorStateAt`, `validateDoors`, the player-tap branch, `sendSafeDoor`, `setSafeAction`: **unchanged**. |
| `app/static/index.html` | **Modify** | `#legend`: replace the 3 `legend-doors` + 1 `legend-safe` chips with the **six** `.door-swatch` chips (§8.1). `#safe-action-row`: add the **Lock** + **Unlock** buttons (§8.2). `#door-action-row`: **unchanged**. |
| `app/static/style.css` | **Modify** | `:root`: **remove** `--door-open`/`--door-unlocked`/`--door-locked`/`--safe-open`/`--explored-safe-open`; **add** the §5.3 CSS tokens. **Remove** `.swatch.door-open`/`.door-unlocked`/`.door-locked`/`.swatch.safe-door` (+ its `::before`/`::after`). **Add** `.door-swatch { display:inline-block; width:16px; height:16px; border-radius:2px; }` and `.door-swatch canvas { display:block; }`. Keep `--doorway` (paint tool + plain doorway chip). `.safe-action` styling: **unchanged** (six buttons reuse it). |
| `tests/test_models.py` (safe class) | **Modify/add** | `safe` round-trip now `L`/`U`/`O`; **legacy `C`→`U` migration** on `from_dict`; `__post_init__` rejects `C` after coercion (and a non-`L/U/O` char); `unmark_safe_door` reversion `L`→`L`/`U`→`U`/`O`→`O`; mutual exclusion preserved. (AC1, AC7.) |
| `tests/test_door_session.py` (safe class) | **Modify/add** | Full safe state machine: six GM-only actions; every legal transition; every illegal `(state,action)` → the **exact** §4.3 string (incl. the new bad-action list, `"safe door is locked"`, `"safe door is already unlocked"`, `"safe door is already locked"`); player any action → `"not allowed"`; `mark`→`L`; `unmark` state-preserved; `mark`/`close` occupancy; `door`-on-safe → `"not a normal door"` (unchanged). **Restriction regression:** hostile blocked by an open safe door + hostile override/place/create rejected — **unchanged** assertions, now with the door in `O`. (AC3, AC5, AC15.) |
| `tests/test_ws.py` (safe class) | **Modify/add** | `safe_door` with `lock`/`unlock` over a real WS; `map.safe` now carries `L`/`U`/`O` in the broadcast; a stale `C` is never emitted. (AC3, AC4, AC14.) |
| `tests/test_api.py` | **Modify/add** | `GET /api/maps/{id}` `safe` object carries `L`/`U`/`O` (disjoint from `doors`); no new route; key-set unchanged. (AC4, AC14.) |
| `tests/test_frontend.py` + `tests/js/harness.js` | **Modify/add** | Static: six `.door-swatch` chips + six `data-safe-action` buttons + the two preview tokens. Harness: export `drawDoorCell`, `SAFE_STATES`, `validateSafe`, `safeDoorStateAt`; assert `drawDoorCell` renders the six states with the §5.1/§5.2 colors at `s=8` and `s=60` (padlock present for `L` only, glow for `O` only, slab for `L`/`U`), the legacy `C`→`U` coercion, and the legend swatches are 16×16 canvases. (AC10–AC14.) |
| `scripts/e2e_proof.py` | **Add step** | Safe-door lock: GM `mark`→`L`; GM `unlock`→`U`; GM `open`→`O`; party/neutral walk through `O`; hostile still blocked by `O` (no-route) + override rejected; GM `close`→`U`; GM `lock`→`L`; legacy-map `C` migrates to `U` on load. (AC14.) |
| `scripts/qa_safe_doors.py` | **Extend** | Add the lock/unlock checks above to the live script. (AC14.) |

---

## 10. Acceptance criteria (for QA)

Individually testable. Harness: in-process `GameSession` + `FakeConn`/`drive`,
pure `app.models`/`app.pathfinding`/`app.visibility` unit tests, raw-socket WS
(`tests/wsclient.py` + `make_server`), the Node harness
(`tests/js/harness.js` + `tests/test_frontend.py`), and
`scripts/e2e_proof.py` / `scripts/qa_safe_doors.py` over the live server. All
deterministic.

- **AC1 — Safe-door state model + round-trip + migration.** `Grid.safe` (a)
  round-trips `to_dict`→`from_dict` preserving every `L`/`U`/`O` (and every
  `doors` state); (b) `safe=None` ⇒ no safe doors, `to_dict` omits `safe`;
  (c) `safe={"5,5":"L"}`, `{"5,5":"U"}`, `{"5,5":"O"}` each round-trip;
  (d) **`from_dict` of a legacy `safe={"5,5":"C"}` yields `{"5,5":"U"}`** (the
  migration) and the next `to_dict` emits `"U"`; (e) `__post_init__` raises
  `ValueError` for a safe key on a `floor`/`wall` cell, an out-of-bounds key, a
  state char not in `L`/`U`/`O` (e.g. a stray `"C"` that wasn't coerced), and a
  key in **both** `doors` and `safe`; (f) `safe_door_state_at` returns
  `None`/`L`/`U`/`O` as expected; `is_safe_door_closed` is True for `L` **and**
  `U`, False for `O`/non-safe.
- **AC2 — Default locked on mark (the fresh default).** A freshly `mark`ed
  safe door is **`L`** (locked+closed); a **legacy** closed safe door loads as
  **`U`** (unlocked closed). No REST/detection/generation path creates a safe
  door; only a GM WS `safe_door mark` does. `mark` is GM-only (player →
  `"not allowed"`).
- **AC3 — Safe-door state machine + permissions (exact).** For each of the 3
  states × 6 actions (+ `mark`/`unmark` on non-safe cells): the legal
  `(state, action)` combinations apply the transition and broadcast; every
  illegal combination returns the **exact** §4.3 error string. Specifically:
  non-GM any action → `"not allowed"`; GM `mark` normal doorway → safe `L`;
  GM `mark` a safe door → `"already a safe door"`; GM `unmark` → normal door
  (`L`→`L`, `U`→`U`, `O`→`O`); `unmark`/`unlock`/`lock`/`open`/`close` on a
  non-safe doorway → `"not a safe door"`; GM `unlock` `L`→`U`; GM `lock`
  `U`→`L`; GM `lock` `O`→`L` (**force-closed**); GM `open` `U`→`O`; GM `close`
  `O`→`U` (occupancy-guarded); `open` on `L` → `"safe door is locked"`;
  `open` on `O` → `"safe door is already open"`; `close` on `L`/`U` →
  `"safe door is already closed"`; `unlock` on `U`/`O` → `"safe door is
  already unlocked"`; `lock` on `L` → `"safe door is already locked"`;
  non-doorway cell → `"not a doorway"`; OOB → `"destination out of bounds"`;
  bad action (incl. `explode`/missing) → `"action must be one of
  mark/unmark/unlock/lock/open/close"`; `mark` with a token → `"cannot mark a
  safe door with a token on it"`; `close` with a token → `"cannot close a door
  with a token on it"`.
- **AC4 — Wire + REST.** (a) Every `welcome`/`state` for a grid with safe
  doors carries `map.safe` with **`L`/`U`/`O`** (disjoint from `map.doors`); a
  **`C` is never emitted** (a loaded legacy `C` is emitted as `U`); (b) `map.
  doors` is **byte-for-byte unchanged** (still `L`/`U`/`O`); (c) `GET
  /api/maps/{id}` / upload / generate carry the additive `safe` (now `L/U/O`)
  only when safe doors exist; no **new** REST route; existing key-set
  unchanged. (d) The `safe_door` client→server frame is `{type,x,y,action}`
  with `action` ∈ the six; no new message type.
- **AC5 — Pathfinding / LOS / restriction UNCHANGED (the A1 safety net).** With
  a safe door in each of `L`, `U`, `O`: `walkable(L, any team incl. None)==
  False`, `walkable(U, any team incl. None)==False` (both closed = a wall),
  `walkable(O, "party"/"neutral")==True`, `walkable(O, "hostile")==False`,
  `walkable(O, None)==True` (entity-agnostic). `has_line_of_sight` blocked by
  `L` and `U`, transparent for `O`, identical for all teams (LOS team-agnostic).
  `find_path(team="hostile")` routes **around** an `O` safe door / `None` if
  sealed; `find_path(team="party"/"neutral")` routes **through** it. **The
  hostile-on-safe-door guard is unchanged:** GM `override:true`/`place`/
  `create_entity`/`set_team` a **hostile** onto a safe cell (any state) →
  `"cannot place a hostile on a safe room door"`; a **party/neutral**
  `override`/`place` onto a **closed (`L` or `U`)** safe door → **allowed**
  (ignore-walls). `app/pathfinding.py` is **byte-identical** (no diff) — the
  AC is a *regression* proof that `L`/`U` behave exactly like the old `C`.
- **AC6 — Awareness + explored UNCHANGED.** `app/awareness.py` and
  `app/visibility.py` are **byte-identical**. A hostile behind a **`L`/`U`**
  safe door is APPROXIMATE (within radius) / INVISIBLE (beyond) to a player;
  behind an **`O`** safe door it is FULL (LOS team-agnostic). The closed-safe-
  door far side is H (never seen) / E (explored); the safe-door face is S;
  opening makes newly-visible cells S; S is monotonic. (Identical assertions to
  the shipped safe-room ACs, now with the door in `L`/`U`/`O`.)
- **AC7 — Legacy migration end-to-end.** A `Grid` **serialized by the
  pre-redesign build** (`map.safe` containing `"C"`) loaded by `from_dict`
  yields a grid whose safe doors are **`"U"`**; the same grid's next
  `state`/REST `map.safe` emits **`"U"`** (not `"C"`); a player sees that door
  as an **unlocked closed** (green slab, **no** padlock) safe door in the S
  tier; the GM's `Safe door` tool shows it as closed-and-unlocked (a subsequent
  `open` is legal; a subsequent `lock` is legal). No door that was
  walkable-when-open before the upgrade is **locked** by the upgrade.
- **AC8 — Normal doors byte-for-byte unchanged.** The **normal-door** state
  machine, `DOOR_ACTIONS`, `map.doors` wire values, `_on_door`, its error
  strings, and the `"not a normal door"` guard on a safe cell are
  **byte-for-byte unchanged**; `tests/test_door_session.py`'s normal-door class
  and `test_bad_action` pass **unmodified**; the normal-door player-tap
  (inverse open/close) is unchanged.
- **AC9 — Six states render correctly at the S tier (harness).** `drawDoorCell`
  renders, at `s=60`, tier `S`: **normal L** = brown slab (`#9c6b3a`) + a
  **brass padlock (`#e6b422`) in the top-right corner**; **normal U** = brown
  slab + **no** padlock; **normal O** = **soft yellow glow (`#ffe9a8`)** + an
  ajar brown leaf; **safe L** = green slab (`#4f9e6b`) + brass padlock TR;
  **safe U** = green slab + no padlock; **safe O** = **soft green glow
  (`#c9f2d4`)** + an ajar green leaf. Assertions: a padlock is drawn **iff**
  state == `L`; the glow (radial gradient) is drawn **iff** state == `O`; the
  slab fill is drawn for `L`/`U`; family is brown for normal and green for
  safe. (AC: "each of the 6 states renders the correct icon at S tier".)
- **AC10 — Locked vs unlocked-closed is the padlock; open is the light (S).**
  At `s=60`: `L` shows a padlock and `U` does not (same slab, same family); the
  **only** pixel difference between `L` and `U` is the padlock region. `O`
  shows a **light** (a radial gradient whose center is brighter than its edge)
  of the **family's** hue (yellow normal / green safe); `O` shows **no** slab
  fill and **no** padlock. (AC: "a locked door shows a padlock top-right and an
  unlocked closed door shows no padlock; open doors show the correct light
  color — yellow normal / green safe".)
- **AC11 — Legible at the 8px minimum (harness, `s=8`).** At `s=8`, all six
  states are drawn and pairwise distinguishable: (a) the closed slab is a
  6×6 colored square (brown normal / green safe); (b) `L` draws a padlock
  (`p>=4`) in the top-right and `U` does not; (c) `O` draws the radial glow
  (yellow/green) with **no** slab fill and **no** ajar leaf (dropped at `s<10`);
  (d) plank lines are **not** drawn (dropped at `s<12`), the inner shadow is
  not drawn (`s<14`), and the keyhole is not drawn (`p<10`). No element is
  drawn outside `[px,px+s]×[py,py+s]`. (AC: "reads at the 8px minimum cell
  size".)
- **AC12 — Looks good up to ~60px (harness, `s=60`).** At `s=60` the
  progressive detail is present: plank lines drawn (`s>=12`), inner shadow
  drawn (`s>=14`), padlock keyhole drawn (`p>=10`), ajar leaf drawn (`s>=10`);
  the slab has a frame, planks, and shadow; the padlock has a body + shackle +
  keyhole. (AC: "and look good up to ~60px".)
- **AC13 — E (explored/greyed) tier renders all six states greyed (harness).**
  With a visibility matrix tiering a door cell to `"E"` (tier `t="E"`),
  `drawDoorCell` renders the §5.2 palette: `L`/`U` = `#8a94a0` slab + `#5f6874`
  frame; `O` = `#e8ecf0` glow; the slab is a **flat grey** (no brown/green hue,
  no inner shadow). A hidden (`"H"`) door cell draws **nothing** (unchanged).
- **AC14 — E tier still distinguishes locked from unlocked-closed.** At tier
  `E`, `s>=12`: `L` draws the **`ePadlockMark` (`#cfd4db`) faint padlock** and
  `U` draws **no** mark (same `#8a94a0` slab); `O` draws the `#e8ecf0` glow. So
  **locked vs unlocked-closed** is distinguished by padlock-mark **presence**,
  and **closed vs open** by slab vs glow — by value/shape, not hue (holds after
  desaturation). (AC: "the explored (E) tier still distinguishes locked from
  unlocked-closed".)
- **AC15 — Safe doors GM-only for all six actions (wire + session).** Over WS
  and in-session: a **player** sending any of `mark`/`unmark`/`lock`/`unlock`/
  `open`/`close` on a safe door → `"not allowed"` (checked first); a **GM**
  succeeds on each legal `(state, action)`. A **player tap** on a safe-door
  cell sends **no** `safe_door` frame (no-op + hint). (AC: "safe doors are
  GM-only for all six actions".)
- **AC16 — Legend + tool UI (static + harness).** (a) `#legend` has exactly
  **six** `.door-swatch` chips: normal `L`/`U`/`O` + safe `L`/`U`/`O`, each
  holding a **16×16 canvas** rendered by `drawDoorCell` at tier `S` (pixel-
  identical to the map art); the old `.swatch.door-open`/`.door-unlocked`/
  `.door-locked`/`.swatch.safe-door` chips are gone. (b) `#safe-action-row`
  has **six** `data-safe-action` buttons (`mark`/`unmark`/`unlock`/`lock`/
  `open`/`close`) and `#door-action-row` **still** has four
  (`unlock`/`lock`/`open`/`close`). (c) The old amber/red + green-cross `T`
  tokens and `:root` tokens are **removed**; the §5.3 tokens are present in
  both `T` and `:root`. (d) Both roles see the six legend chips (no
  `body.is-gm` gate).
- **AC17 — Full regression.** `python -m pytest` **and** `python -m unittest
  discover -s tests -t .` fully green with **only** the safe-door test updates
  enumerated in §9 (normal-door, pathfinding, visibility, awareness tests
  **unmodified**); `app/pathfinding.py` + `app/awareness.py` +
  `app/visibility.py` **byte-identical**; the sample-dungeon geometry
  (`app/grid.py`) byte-identical; `scripts/e2e_proof.py` all-✓ including the
  new safe-door lock step; `scripts/qa_safe_doors.py` all-✓; `GET /health` ok.

---

## 11. Edge cases (E1…)

- **E1 — Small cell size (s=8).** The art degrades by the §6.4 thresholds:
  slab 6×6, 1px frame, a 4px brass padlock for `L` (no keyhole/planks/shadow),
  a radial glow for `O` (no ajar leaf). All six states stay distinguishable
  (family color, padlock presence, glow-vs-slab). At `s=4` (preview floor only)
  the slab is `max(1, s*0.10)=1px` inset → a ~2×2 sliver; acceptable for the
  preview canvas (the live map floors `s` at 8). (AC11.)
- **E2 — GM vs player view.** The **GM** (no visibility matrix) renders every
  safe/normal door at the **S tier** (full brown/green/wood, padlock, glow) —
  the GM always sees the true state. A **player** renders S cells at the S
  tier and **E** cells at the greyed E tier (§5.2); **H** cells draw nothing.
  A player cannot *act* on a safe door (no-op + hint) but **can** read all six
  states (the art is the same for both roles — only the tier differs). (AC9,
  AC13, AC14.)
- **E3 — Explored (E) tier door memory.** A safe door the player saw as `L`
  (locked, padlock) then walked away from renders **greyed** with the faint
  `ePadlockMark` padlock (still readable as "locked"); if the GM then unlocks
  it and the player re-sees it, the S tier shows the green slab with **no**
  padlock and the E memory (once seen) drops the mark. The E-tier padlock is
  value/lightness-based so it survives desaturation. (AC13, AC14.)
- **E4 — A door that is also hostile-restricted (safe door, any state).** The
  restriction is keyed off `is_safe_door` + `team`, **not** the state: a
  `L` or `U` safe door is a wall to **everyone** (incl. hostile) and blocks
  LOS; an `O` safe door is walkable for `party`/`neutral` but **still a wall
  to a hostile** (the open-safe term in `_blocked_for`) and a hostile override/
  place/create/set_team onto it → `"cannot place a hostile on a safe room
  door"`. **All unchanged** by the lock state — verified against
  `_closed_doors`/`_open_safe_doors`/`_blocked_for` (AC5). (This is the
  "behavior is UNCHANGED" guarantee for the centerpiece decision.)
- **E5 — Repaint-to-remove.** Painting `floor`/`wall` over a safe door deletes
  its `safe` key via `sync_doors_after_cell_set` (the **single** paint-sync
  point, unchanged) — the door is gone (no state to render) regardless of its
  `L`/`U`/`O`. Painting `doorway` over it is a no-op for state (keeps the
  current state) — unchanged. (Same as before; the state char is irrelevant to
  removal.)
- **E6 — An open safe door seen as a wall by hostiles (unchanged).** For a
  hostile, an `O` safe door is in the blocked set (`closed | open_safe`) → A*
  routes around it or returns `None` → `"no route — wall in the way"`; no
  crash, no stuck entity. For `party`/`neutral`, `O` is walkable. The
  **closed** (`L`/`U`) case is a wall to everyone. (Identical to the shipped
  safe-room E3; `L` and `U` are both closed, so the hostile's blocked set is
  unchanged whether the door is `L` or `U`.)
- **E7 — Unmark reversion preserving state.** GM `unmark` a safe door in `L`
  → a normal door in **`L`** (locked — the GM can then `unlock` it as a normal
  door); in `U` → normal **`U`**; in `O` → normal **`O`** (still open). The
  `map.safe` key is removed and the `map.doors` key is set to the preserved
  char (mutual exclusion restored — the cell is back in `doors`, not `safe`).
  (AC3, §3.5.)
- **E8 — A `door` (normal) message on a safe-door cell.** → `"not a normal
  door"` (the frozen `_on_door` guard, unchanged); the safe record is
  untouched. (AC8.)
- **E9 — Two GM actions race on the same safe door.** All six actions run under
  the session `RLock`; the second sees the first's state (e.g. `unlock` then
  `lock` serialize; the second is legal or the `"…already…"` error).
  Deterministic (I7, unchanged mechanism).
- **E10 — `use_map` swap while safe doors are `L`/`U`/`O`.** The grid object is
  swapped wholesale; the new grid carries **its own** `safe` (from
  `maps_registry`), so safe-door state resets with the new grid (each map's
  safe doors are its own). No safe-door-specific code. (Unchanged from
  safe-room E10; the state chars are irrelevant to the swap.)
- **E11 — A token on a safe door when it's `close`d or `mark`ed.** `close` (from
  `O`) with any entity on the cell → `"cannot close a door with a token on
  it"`; `mark` with a token → `"cannot mark a safe door with a token on it"`.
  `lock` from `O` force-closes but is **not** occupancy-guarded (the door was
  open/walkable; matches the normal door's A5 nuance) — a hostile can't be on
  an open safe door anyway (E4). (AC3.)
- **E12 — A stale in-memory server (pre-reload) still holds `C`.** The client's
  `validateSafe` coerces `C`→`U`, so the render never sees `C`; after the first
  `to_dict`/broadcast the server emits `U` and the wire is clean. No crash, no
  unknown-char render. (AC7.)
- **E13 — A safe door at a map border / a doorway with no wall neighbours.**
  State + behavior are independent of the doorway heuristic — any `doorway`
  can be marked safe; a `L`/`U` border safe door blocks sight/movement like any
  closed door. No special-casing. (Unchanged.)
- **E14 — Locking an OPEN safe door (force-close).** GM `lock` on an `O` safe
  door → **`L`** (force-closed, like the normal door A7). There is no "open and
  locked" state. If a `party`/`neutral` token is standing on the (open) door
  when the GM locks it, the token is left on a now-closed cell — the same
  degenerate "GM force-closed a door someone was on" case the normal door
  already has (it is **not** occupancy-guarded, by design, matching
  door-features A5); the GM resolves it by moving the token. Documented, not a
  normal flow. (AC3, consistent with the normal door.)

---

## 12. Invariants (all AC-tested)

- **I1' — Safe state only on doorway cells, valid char.** `Grid.safe` (when not
  `None`) has a key only for an in-bounds `doorway` cell, every value in
  `L`/`U`/`O`, and a key present in both `doors` and `safe` is rejected.
  `__post_init__` enforces; `from_dict` coerces legacy `C` first.
- **I2' — Default: fresh mark is `L`; legacy closed migrates to `U`.** A fresh
  safe door is `L` (locked, the secure default); a legacy `C` loads as `U`
  (unlocked closed — no door is locked *by surprise* on upgrade). A closed
  safe door (both `L` and `U`) is a wall for LOS + movement.
- **I3 — The GM view is never door-filtered.** The GM renders all six states
  at S tier; the GM payload carries full `map.safe`/`map.doors` regardless of
  state.
- **I4 — Closed safe door = wall; open safe door = restricted.** For every
  (grid, state, team): `walkable(L, any team incl None)==False`,
  `walkable(U, any team incl None)==False`, `walkable(O, party/neutral)==True`,
  `walkable(O, hostile)==False`; a hostile can **never** occupy a safe-door
  cell in **any** state (move/place/create/set_team guard, invariant under
  override). **Identical to the shipped safe-room I4** (a `C` was already
  "closed"; `L` and `U` are both closed, so the behavior set is unchanged).
- **I5 — Every `map` payload carries the full door partition.** Every
  `welcome`/`state`/REST map object for a grid with ≥ 1 doorway carries
  `map.doors` **and** `map.safe` (when any), disjoint and jointly covering all
  doorways; `map.safe` values are `L`/`U`/`O` (never `C`).
- **I6 — Awareness + explored logic unchanged.** `app/awareness.py` and
  `app/visibility.py` byte-identical; S/E/H and three-tier logic unchanged
  (they consume the unchanged `has_line_of_sight`/`visible_cells`).
- **I7 — Atomic state machine.** A safe-door action fully applies one legal
  transition or is fully rejected; the `(state, action, role)` → result
  mapping is total and deterministic.
- **I8 — No entity left on a closed safe door by `close`/`mark`.** `close` and
  `mark` are occupancy-guarded; a `closed` safe door never has an entity on it
  after a successful `close`/`mark` (an `open` safe door may be occupied by
  party/neutral; a hostile never).
- **I9 — Monotonic explored memory.** Within a map, no cell goes S/E → H
  (preserved; safe doors only shrink the S-set).
- **I10 — Server-authoritative.** The server never trusts a client-claimed safe
  state; safe state changes only via a validated `safe_door` message (or a GM
  paint that deletes it); the broadcast is the source of truth.
- **I11 — Pathfinding/LOS unchanged.** `app/pathfinding.py` is byte-identical;
  `L` and `U` behave exactly as the old `C` (closed); `O` as the old `O`
  (open, team-restricted). (AC5, AC17.)
- **I12 — Normal doors byte-for-byte unchanged.** `DOOR_ACTIONS`, `_on_door`,
  `map.doors`, and the normal error strings are unchanged; the `"not a normal
  door"` guard is unchanged. (AC8.)

---

## 13. Explicit assumptions (per PROJECT.md ambiguity convention)

Every ambiguous point is resolved here and pinned by an AC.

- **A1 — Safe doors gain a lock state (`L`/`U`/`O`), supersedng "always
  unlocked" (the centerpiece; owner confirmation requested).** The owner's
  six-state icon spec requires a *locked closed* safe door (padlock) distinct
  from an *unlocked closed* safe door (no padlock), which the shipped
  `C`/`O` (always-unlocked) model cannot express. **Resolution:** safe doors
  move to the **same `L`/`U`/`O` model as normal doors**; legacy `C`→`U`;
  fresh `mark`→`L`; `unmark` preserves state; Lock/Unlock are added as
  GM-only actions. **What is explicitly superseded:** safe-room-doors.md
  §1.1 (green cross + "always unlocked, starts closed"), I2, A4, A6, §4.1
  ("no lock/unlock on a safe door"), §4.3 bad-action list, and the door
  *art* tokens in both design docs. **What is preserved:** the hostile
  restriction, closed=wall/open=sight-transparent, mutual exclusion, GM-only
  control, the `Grid.safe`/`map.safe` structure, and all pathfinding/LOS
  (unchanged, I4/I11). **This is the one behavioral change** — a GM must now
  unlock a safe door before it can be walked through (a locked safe door is a
  wall, like a locked normal door). Pinned by AC1, AC2, AC3, AC5, AC15, AC17.
- **A2 — Legacy `"C"` migrates to `"U"` (not `"L"`).** The old `C` door was
  *always unlocked and closed*; `U` preserves that exact behavior (a `U` safe
  door is closed but the GM can open it; a party/neutral could already open a
  `C` door in the old model — now via the GM `open`). Migrating to `L` would
  silently lock doors the GM left open-unlocked. Pinned by AC1, AC7.
- **A3 — A fresh `mark` defaults to `"L"` (locked).** The secure default,
  mirroring the normal door's locked default; the owner lists "locked" as the
  primary safe state and the padlock reads as "secure". Pinned by AC2.
- **A4 — `unmark` preserves the state (L→L, U→U, O→O).** Reverting a safe door
  to a normal door keeps the door in the same lock/open state (the cell stays
  a `doorway` with a normal-door record); the GM can then treat it as a normal
  door (unlock/open/etc.). Supersedes the old `C`→`U`/`O`→`O`. Pinned by AC3.
- **A5 — Safe doors stay GM-only end-to-end (now six actions).** There is no
  "unlocked ⇒ any client may open/close" rule for safe doors (unlike normal
  doors); the GM manages lock/unlock/open/close. A player can never act on a
  safe door. Pinned by AC15.
- **A6 — The safe strings use the "safe door is …" prefix.** Safe-door state
  errors read `"safe door is locked"` / `"safe door is already unlocked"` etc.
  (vs the normal door's `"door is locked"`), so a GM can tell which door kind a
  toast refers to; the two handlers never collide. The shared strings
  (`"not a safe door"`, `"already a safe door"`, `"not a doorway"`, `"not
  allowed"`, `"destination out of bounds"`, `"cannot close a door with a token
  on it"`, `"cannot mark a safe door with a token on it"`) are kept verbatim.
  Pinned by AC3.
- **A7 — The padlock is the *sole* L-vs-U discriminator (top-right corner).**
  A locked door differs from an unlocked-closed door **only** by the padlock
  (both share the same wood slab + frame + planks). The padlock sits in the
  **top-right** corner inside the slab (never clipping the frame), per the
  owner's spec. In the E tier the discriminator is the **faint padlock mark
  (value/lightness)**, so it survives desaturation. Pinned by AC10, AC14.
- **A8 — The open state is a *soft light* filling the opening (radial),
  clipped to the frame.** Normal = soft **yellow**, safe = soft **green** (the
  owner's spec). The light is a radial gradient (bright center, transparent
  edge) clipped to the slab rect so it never bleeds onto neighbors; an **ajar
  leaf** (a sliver of the door's wood, swung to one side) is drawn over the
  glow to read "a door, swung open" — dropped at `s<10`. Pinned by AC9, AC10,
  AC11.
- **A9 — Normal = brown wood, safe = green wood (the family discriminator).**
  The wood hue is the at-a-glance "normal vs safe" signal (brown vs green),
  distinct from floor, wall, the party-token green, and the old mint. In the E
  tier the hue is dropped (both families grey) — in memory a safe door and a
  normal door are both "a door"; the S tier and the GM view keep the hue.
  Pinned by AC9, AC13.
- **A10 — The legend shows six mini-canvas swatches (pixel-identical to the
  map art), to both roles.** The legend chips are tiny `drawDoorCell` canvases
  (S tier), not colored rectangles — so the legend *is* the intuitive icon the
  owner wants. The GM Door tool (Unlock/Lock/Open/Close) is confirmed
  unchanged; the GM Safe door tool gains Lock/Unlock. Pinned by AC16.
- **A11 — Pathfinding/LOS need no change (verified, not assumed).** Because
  `_closed_doors` tests `safe[key] != "O"` and `_open_safe_doors` tests `==
  "O"`, `L` and `U` are *already* "closed" and `O` "open" — the new chars fall
  out of the existing open-vs-closed test. This is a **verifiable invariant**,
  pinned by AC5/AC17 (byte-identical `pathfinding.py`). If any future change
  makes pathfinding *lock*-aware (e.g. "an unlocked closed safe door is
  walkable"), this invariant must be re-examined — but that is out of scope
  here (the owner's spec and the task both state closed=wall is unchanged).

---

## 14. Non-goals

- **No door swing / open-close animation or sound** (doors are instant state
  changes; the client re-renders from the broadcast — the existing
  no-structural-animation stance). The "light" is a static radial gradient,
  not an animated glow.
- **No per-door ACLs beyond the lock state** (no "only player X may open
  this"; the gates are GM-unlock + the safe-door GM-only rule).
- **No "unlocked closed safe door is walkable" behavior** (an unlocked closed
  safe door is still a wall until the GM *opens* it — safe doors are GM-
  managed, so "unlocked" for a safe door means "the GM may open it", not
  "anyone may walk through it"). This is deliberate and is the reason A5 is
  GM-only.
- **No client-predicted door state** (server authoritative; no optimistic
  safe-door mutation).
- **No new REST door endpoints** (WS-only safe-door actions; REST only carries
  the additive `safe` field, now `L`/`U`/`O`).
- **No persistence / save-load of door state** (in-memory, like all session
  state — a restart is fresh; the legacy `C`→`U` migration applies to any
  loaded `from_dict` grid regardless).
- **No changes to the doorway *detection heuristic* or to generated/sample map
  geometry** (this changes door *state* + *art*, not where doorways are).
- **No animated / directional light** (no "the light points at the door"; a
  symmetric radial glow fills the opening).
- **No new padlock "unlocked-open" glyph** (a door's padlock is always the
  closed/locked variant; "unlocked" is expressed by *absence* of the padlock,
  not by an open padlock).
- **No colorblind-only path** (the states are already distinguishable by
  **shape** — padlock / plain-slab / light — not color alone; the hue is
  secondary, consistent with the project's "shape + color, never color alone"
  rule for tokens).

---

## 15. Implementation notes (build-ready pointers, non-normative)

- **`drawDoorCell` is a pure function** of `(ctx, kind, state, px, py, s, t)` —
  no closure over `state` (the module app state). This makes it trivially
  testable in the Node harness (call it with a stub `ctx` that records
  `fillStyle`/`strokeStyle`/`fillRect`/`strokeRect`/`beginPath`/`arc`/
  `createRadialGradient`) and reusable by the legend swatches.
- **`createRadialGradient`** is used for the `O` glow; the stub `ctx` in
  `tests/js/harness.js` must stub it (return an object with `addColorStop`).
  The S-tier glow core color is `lightYellow`/`lightGreen`; the E-tier is
  `eLight` for both.
- **Round the slab to integer px** at the fill (the frame stroke can use
  `.5` offsets for crisp 1px lines, as today's `strokeRect(px+1.5, …)` does).
  Keep the slab *inside* the cell (the `margin` inset) so the floor base
  shows as the frame gap (the "floor base under the door" contract, §6.0).
- **Keep the two `mode-paint-*` cursor classes** (`mode-paint-door`,
  `mode-paint-safeDoor`) and the hover-preview fill — only the *color* source
  changes (to `doorWoodPreview`/`safeWoodPreview`).
- **Server reload note (TODO):** after the backend change lands, the running
  server (branch `feat/safe-room-doors`, the one the team keeps on 0.0.0.0:8000)
  must be **restarted** so `from_dict`'s migration + the new state machine are
  live; in-memory safe doors from the old process are discarded on restart
  (fresh in-memory state, as always).
