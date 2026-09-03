# Design — Per-Player Awareness Ring + GM-Controlled Awareness Range

**Status:** build-ready spec. Supersedes the fixed `APPROX_RADIUS = 4` in the
approximate tier with a **per-player, GM-adjustable** value, and adds a visible
**awareness ring** around each player icon.
**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins.
**Files referenced (read, not modified here):** `app/models.py`,
`app/awareness.py`, `app/session.py`, `app/static/app.js`,
`app/static/index.html`, `docs/design/wireframes.md`, `docs/design/gm-controller.md`.

---

## 1. What changes (summary)

| # | Change | Where |
|---|---|---|
| A1 | The non-line-of-sight (**approximate**) awareness tier uses a **per-player radius** instead of the fixed constant `4`. Default stays **4**. | `app/awareness.py`, `app/models.py` |
| A2 | Each **player** carries an `awareness_radius` (integer, **0–20**, default 4). | `app/models.py` (`Player`) |
| A3 | New **GM-only** WebSocket message `set_awareness` to change a player's radius. | `app/session.py` |
| A4 | An **awareness ring** is drawn on the map around each player icon, sized to that player's `awareness_radius`. | `app/static/app.js` (canvas) |
| A5 | **GM control**: GM clicks a player icon → an **Awareness** number field (0–20) appears in GM Tools. | `app/static/index.html`, `app/static/app.js` |
| A6 | Legend + hint copy updated to reflect the variable range. | `app/static/index.html`, `app/static/app.js` |

**What does NOT change:** the three-tier model (FULL on line of sight,
APPROXIMATE within range, INVISIBLE beyond), the GM-never-filtered rule, all
movement/permission rules, the wire shapes of existing messages, and the
`APPROX_RADIUS = 4` constant (it becomes the *default* value).

---

## 2. Data model

### 2.1 `Player.awareness_radius` (new field)

- Type: `int`. Range: **0 ≤ value ≤ 20**. Default: **4** (i.e. `APPROX_RADIUS`).
- Semantics: the Chebyshev distance (in squares) within which a **no-line-of-sight**
  entity is perceived as an *approximate* contact. Entities with line of sight are
  always FULL regardless of this value; entities beyond this value (and no LOS) are
  INVISIBLE.
- Lives on the **Player** (not the Entity), because awareness is anchored to the
  player's own token and the GM edits it via the player's icon. If the GM moves the
  token, the radius stays with the player and the ring follows.

`Player.to_dict()` → add `"awareness_radius": <int>`.
`Player.from_dict()` → `int(data.get("awareness_radius", 4))`, **clamped** to
`[0, 20]` (out-of-range/invalid input is silently clamped on read; the live setter
in §3 enforces the same range and errors).

### 2.2 Constants (new, exported from `app/awareness.py`)

```
AWARENESS_MIN = 0
AWARENESS_MAX = 20
```
`APPROX_RADIUS = 4` stays as the **default** radius.

---

## 3. Server behavior

### 3.1 Awareness computation

- The **approximate** tier in `build_awareness` uses the viewer's radius:
  `radius = viewer.awareness_radius` (player branch only). Fallback to
  `APPROX_RADIUS` if the value is somehow `None`/non-int.
- The GM branch is **unchanged** (never distance/LOS filtered, ignores radius).
- The **LOS/FULL** tier is unchanged (radius does not gate sight).
- No persisted state: in-memory only, so no migration. Old `Player` objects
  simply get the default 4 via the dataclass default.

### 3.2 New GM-only message: `set_awareness`

**Client → server:**
```json
{ "type": "set_awareness", "entity_id": "<player-token id>", "value": <int 0..20> }
```
The GM points at a **player icon** (a token whose `owner` is a player id), so the
client sends that token's `entity_id`; the server resolves `entity.owner` → player.

**Server handling (new `GameSession._on_set_awareness`, dispatched via
`_gm_only`):**
1. GM-only (non-GM → `"not allowed"`).
2. `entity_id` must be a string and name an existing entity, else error.
3. The entity must have an **owner** (be a player's token). If `owner is None`
   (an NPC/enemy/GM-controlled token) → error: `"not a player token"`.
4. `value` must be an integer (reject `bool` and non-int), within
   `[AWARENESS_MIN, AWARENESS_MAX]` → else error
   `"awareness must be an integer 0–20"`.
5. Set `self.players[owner].awareness_radius = value`, then **broadcast** the
   per-viewer `state` (so every client re-renders rings and the affected player's
   own overlay).

**Server → client (success):** no per-client reply — the change is applied and the
normal `state` broadcast carries the new value (consistent with `set_team`,
`paint`, etc.). **Error replies** are the usual `{"type":"error","message":...}`.

### 3.3 State payload

`Player.to_dict()` now includes `awareness_radius`, so every `welcome`/`state`
`players[]` entry carries it. The client reads the selected player's value to keep
the GM number field in sync after any broadcast.

---

## 4. Frontend — awareness ring (canvas)

### 4.1 Geometry (must match the Chebyshev model)

The approximate tier is a **Chebyshev** ball, which is an **axis-aligned square**
of cells. For a token on cell `(x, y)` with radius `r` and cell size `s`, origin
`(ox, oy)`:

- token center: `cx = ox + (x + 0.5) * s`, `cy = oy + (y + 0.5) * s`
- ring half-side: `half = (r + 0.5) * s`
- draw a **square** from `(cx - half, cy - half)` to `(cx + half, cy + half)`.

This exactly outlines the cells a player can *sense without line of sight*
(cells with `max(|dx|,|dy|) ≤ r`). Do **not** use a circle (that would misstate
the model).

### 4.2 Appearance

- **Style:** subtle, non-intrusive. Dashed stroke + very light fill so tokens and
  walls remain readable on top.
  - fill: accent blue `rgba(77, 171, 247, 0.10)` (`#4dabf7` @ ~10%)
  - stroke: `#4dabf7`, ~1.5px, `setLineDash([max(3, s*0.18), max(3, s*0.18)])`
- **Distinct from the "YOU" ring:** the own-ring is a small solid blue
  (`#1971c2`) circle hugging the token; the awareness ring is a large dashed
  **square**. Different shape (square vs circle), size, and dash → no confusion.
- **Draw order (in `drawEntitiesAndDots`):** awareness rings are drawn **before**
  tokens/dots (under the tokens, like the selection ring), after the grid, so they
  never cover token art.
- **Clipping:** let the canvas clip naturally (a radius-20 ring may extend past the
  grid; that is fine and communicates "bigger than the map").

### 4.3 Who sees which ring

| Viewer | Rings drawn |
|---|---|
| **GM** | A ring around **every player-owned token** (`entity.owner != null`), each sized to that player's `awareness_radius`. The GM has no token → no GM ring. |
| **Player** | A ring around **their own token only** (sized to their own radius). They never see other players' rings (other tokens are at best awareness items). |

The ring is a **render-only** visualization; it is computed from the same
`players[]`/`you_entity` data the server already sends. No new per-ring message.

### 4.4 Data the client already has

- GM: `state.entities` (each has `owner`) + `state.players` (each has
  `awareness_radius`). Map `owner` → player → radius.
- Player: `state.you_entity` (their token) + their own entry in `state.players`
  (or `welcome.you` + `players`). Use their own radius.

Recompute on every `state`/`welcome` (radii may change when the GM edits them).

---

## 5. Frontend — GM control (GM Tools sidebar)

### 5.1 Element (new, inside `#entity-tools`, GM-only section)

```
Selected: Alice (player)
Team   [ party ▾ ]  [ Delete entity ]
Awareness  [ 4 ]  (0–20)        ← NEW
```

- New `<input id="awareness-input" type="number" min="0" max="20" step="1">`
  with a label "Awareness" and a small "0–20" hint.
- **Enabled only when** the selected entity is a **player token** (`owner` is a
  player id). Disabled (grayed) for NPC/enemy/none-selected.
- Initial value = the selected player's current `awareness_radius`.
- On `change` (or `input` debounced to commit-on-change), send
  `{type:"set_awareness", entity_id: <selected>, value: <int>}`. Reconcile from
  the next `state` broadcast (authoritative).
- Invalid/empty field → treat as no-op (don't send a non-int).

### 5.2 Interaction flow

1. GM clicks a player icon → `selectEntity(id)` (existing) → GM Tools reflect it.
2. `syncGmTools()` now also enables `#awareness-input` when the selection's
   `owner` is a player, and sets it to that player's current radius.
3. GM types a new value → commit → server updates → broadcast → all rings + the
   field update.
4. Selecting a non-player token or nothing → input disabled.

### 5.3 Player view

No awareness control for players (GM-only). A player simply sees their own ring.

---

## 6. Legend + hint copy

- Legend: add a chip — dashed square swatch + **"awareness range"**.
- Update the existing unseen-contact note from
  `unseen contact (≤4 sq, sight blocked)` →
  `unseen contact (within awareness range, sight blocked)`.
- Keep the "in sight → named token … beyond range → hidden" note, replacing
  "4 squares" with "their awareness range".
- Optional GM hint (control bar / first-run): "Select a player and set their
  Awareness (0–20) to change how far they sense unseen things."

---

## 7. Edge cases

| Case | Behavior |
|---|---|
| `radius = 0` | No approximate tier — player sees only LOS (FULL) contacts. Ring shrinks to a small square hugging the token (1 cell). |
| `radius = 20` | Large ring (may extend beyond the map). Player senses unseen entities up to 20 squares. |
| GM token | No token → no ring, no awareness field (GM has `entity_id = null`). |
| Player token deleted | No token → no ring; awareness is empty (existing anchor-missing rule). |
| NPC/enemy selected by GM | Awareness field disabled (not a player token). `set_awareness` on them → error. |
| Reconnect | Radius is on the Player record and re-sent in `welcome`/`state`; ring re-renders. |
| Simultaneous GM edit + move | Independent; broadcast reconciles both. |

---

## 8. Acceptance criteria (for QA)

1. **Default unchanged:** a fresh player perceives no-LOS entities up to 4 squares
   (approximate) — identical to current behavior.
2. **Radius 0:** GM sets a player to 0 → that player's awareness contains **no**
   approximate items (only LOS FULL contacts). Ring is minimal.
3. **Radius 20:** GM sets a player to 20 → a no-LOS entity at Chebyshev ≤ 20 is
   approximate; at 21 (no LOS) is invisible.
4. **Boundary at N:** GM sets to 7 → no-LOS entity at exactly 7 is approximate, at
   8 is invisible.
5. **LOS is radius-independent:** a no-LOS-blocking line of sight shows FULL even
   beyond the radius (radius never grants sight).
6. **Permissions:** non-GM `set_awareness` → `"not allowed"`; on an NPC/enemy →
   error; `value` ∈ {`21`, `-1`, `"abc"`, `true`} → error; valid int 0–20 → applied.
7. **Payload:** every `players[]` entry in `welcome`/`state` carries
   `awareness_radius` (int 0–20).
8. **Ring (GM):** GM sees a dashed square ring around each player token, sized to
   that player's radius; rings update live when the GM edits a radius.
9. **Ring (player):** a player sees only their own ring, sized to their radius.
10. **GM unchanged:** GM awareness list is never filtered by radius.
11. **Regression:** full suite green under `pytest` **and** `unittest`;
    `scripts/e2e_proof.py` all-✓ (existing three-tier assertions still hold at the
    default radius 4).

---

## 9. Wire protocol additions (recap for the engineer)

- **`Player` dict** gains `"awareness_radius": int` (0–20, default 4) in all
  `welcome`/`state` `players[]`.
- **New client message:** `{type:"set_awareness", entity_id, value}` — GM-only.
- **New server errors:** `"not a player token"`,
  `"awareness must be an integer 0–20"` (plus existing `"not allowed"`,
  `"no such entity"`).
- **No change** to any existing message shape or to the GM/player tier rules.
