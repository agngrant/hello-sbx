# GM as Pure Controller — UX Design Spec

**Status:** build-ready UX spec for the behavior change "the GM has no token on
the map". Supersedes, for GM-related screens only, the sections of
`docs/design/wireframes.md` called out in §9. Everything not covered here
stays as specified in `wireframes.md` and `PROJECT.md`.
**Source of truth:** `PROJECT.md`; where this doc and PROJECT.md diverge,
PROJECT.md wins and the divergence is flagged in §10.
**Code referenced (read, not modified):** `app/static/index.html`,
`app/static/app.js`, `app/static/style.css`, `app/session.py`,
`app/awareness.py`, `tests/`.

---

## 1. Behavior change statement

**Old:** On GM join, the server spawned a `gm_character` entity
(team `neutral`, `owner=None`) at the first free floor and set
`player.entity_id` to it. The GM saw its own white token (lettered, labeled),
could move it, it appeared in the GM's awareness list, in the GM's summary
counts, and — as an unlabeled **white dot** — in every player's radar. The
create-token dropdown offered `npc | enemy | gm_character`.

**New:** **The GM is a pure controller/spectator.** The GM has **no entity, no
token, no `entity_id`** — ever: not on join, not on reconnect, not after any
mutation. The GM's only map presence is the **controller view**: the full
map, every token with true colors + shape + name label, radar-style
awareness markers, and **never fogged**. The GM creates, selects, moves
(with the Ignore-walls override), retags, and deletes **other** tokens.
Players are completely unchanged: each player still joins with their own
`player` entity, still moves only it, and their radar simply has one fewer
white dot (the GM's) than before.

The change has three layers, all covered here:

| Layer | Delta |
|---|---|
| Server contract (what the UX depends on) | §2 — `welcome.you.entity_id = null` for GMs, no `gm_character` spawn, `create_entity` accepts only `npc`/`enemy`. |
| Frontend UX (this spec) | §3–§7. |
| Verification | §8 (new acceptance checklist) + §9 (old tests/behaviors to change or remove). |

---

## 2. Protocol expectations the UX relies on (for the engineer)

These are the server-side facts the frontend design assumes. They are stated
here so the UX spec is verifiable end-to-end; the implementing engineer owns
them.

1. **GM join** (first client, or explicit `role:"gm"`): the `Player` record
   is created with `entity_id = None`. **No `Entity` is created.** No call
   to `_find_free_floor()` for the GM.
2. **GM reconnect** (same name, matching role — the existing re-attach path
   in `GameSession.join`): re-attach the socket to the existing `Player`.
   Idempotent by construction: there is no GM entity to preserve and none to
   re-spawn. **Entity count is unchanged across a GM disconnect/reconnect.**
3. **`create_entity`:** `CREATABLE_KINDS = ("npc", "enemy")`.
   `kind:"gm_character"` → `{"type":"error","message":"kind must be one of npc/enemy"}`.
   `kind:"player"` stays rejected (server-only, spawned by `join`) — already
   the case (BUG-010); keep it.
4. **`state_for(gm)` / `welcome`:** `you.entity_id = null`,
   `you_entity = null`, `entities` = every non-player-owned GM token **plus
   all players' tokens** (full list, as today), `awareness` = one labeled
   item per entity that exists.
5. **`state_for(player)` / `you_entity`:** unchanged (players still get their
   own token via `you_entity`). Player `awareness` items now naturally exclude
   any GM token because none exists.
6. **Fog:** `GameSession._awareness_for` already bypasses the LOS filter for
   `role == "gm"`. Unchanged. (Note: fog filtering in v1 is **server-side**
   in `_awareness_for` + the per-player `_seen` set; `wireframes.md §12.3`
   described a client-side Bresenham — the shipped implementation does the
   filtering server-side, so the GM client renders exactly the items it is
   sent and does zero LOS math. This spec relies on that.)
7. **`EntityKind` union (`app/models.py`):** keep the `"gm_character"` string
   in `ENTITY_KINDS` as a **deprecated legacy value** (in-memory sessions from
   older server builds may still hold one; `Entity.from_dict` must not crash).
   It is simply never spawned and never creatable. Removing the string from
   the union is a follow-up for PROJECT.md §4 (PM-owned, §10).
8. **Spawn coordinates shift:** with the GM no longer occupying the first
   free floor, the **first player** now spawns at the first free floor
   (sample map: `(1,1)` instead of `(2,1)`). Purely a test-expectation change.

---

## 3. UX by surface — before / after

### 3.1 Lobby (`#lobby-view`)

**Before:** `#lobby-note` reads *"1 GM + up to 6 players per session. First
connection to a new session is the GM automatically."* A GM had no way to
know, before joining, that they would get a token.

**After:** same card, same fields, same buttons. `#lobby-note` gains one
sentence setting the controller expectation:

```
┌──────────────────────────────────────────────────────────────┐
│                                    LITTLEDUNGEONS            │
│         ┌──────────────────────────────────────────┐         │
│         │ Join a session                           │         │
│         │ Name                                     │         │
│         │ [  Your name______________ ]  #join-name │         │
│         │   [  Join as GM  ]     [  Join as Player ]         │
│         │ 1 GM + up to 6 players per session.      │ #lobby-note
│         │ First connection to a new session is the │
│         │ GM automatically. The GM has no token on │
│         │ the map — the GM creates and controls    │
│         │ all tokens.                              │
│         └──────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
```

- Copy change only; no new IDs. 12px muted text, wraps fine at 460px card
  width; on <480px it simply wraps a line higher (no layout change).
- The role shown is still whatever the server assigns
  (`welcome.you.role` is authoritative, wireframes §12.1) — the note is a
  general description, not a promise about the joiner's own role.

### 3.2 The "you are GM" moment (welcome)

**Before:** toast `Welcome, {name} — you are the GM.` — no mention that the
GM is a controller; the GM's own token appeared on the map and in the list.

**After:**

1. **Welcome toast (GM only):**
   `Welcome, {name} — you're the GM. You have no token on the map: create and
   move tokens for everyone.` (player toast unchanged: `Welcome, {name}.`)
2. **First-run canvas hint (GM only, one-time):** when the GM's welcome
   arrives with `entities.length === 0` (fresh session, no players yet), show
   `#canvas-hint` (top-center pill, existing element) for **5 s**, or until the
   GM selects or creates a token, whichever comes first:

   ```
   ┌───────────────────────────────────────────────────────┐
   │  You're the GM — no token of your own. Add tokens in  │
   │  GM Tools, then select one and click a tile to move.  │
   └───────────────────────────────────────────────────────┘
   ```

   If the GM joins a session that already has tokens, no hint — the tokens
   are visible and the control-bar hint (§3.7) is enough.
3. **No map view token for the GM:** the canvas paints grid + all entities;
   there is no token, no blue ring, no "YOU" pill anywhere on the GM's screen.

### 3.3 Map view / canvas — GM (the "controller view")

**Before:** GM canvas = full map + a full token for **every** entity
including the GM's own `gm_character` (white circle, letter, name pill) +
awareness shape-marker overlaid top-right of each token.

**After:** identical rendering pipeline, one input difference — the GM
receives no own entity, so there is simply nothing own-shaped to render.
Explicit guarantees (these are the "no own-token assumption" audit, §3.7):

- **No own ring, no "YOU" pill:** `--own-ring`/`dot-own`/"YOU" exist only for
  the player view (`ring: isOwn && role==="player"`). With
  `you.entity_id = null`, `isOwn` is false for every GM token. Confirmed: no
  code change, no visual leak.
- **All tokens rendered:** full token (circle + initial + white outline),
  name label pill under it, awareness shape marker (▲/●/□ in team color)
  overlaid top-right — exactly the wireframes §4.1 GM treatment, now for the
  NPC/enemy tokens the GM creates plus every connected player's token.
- **Never fogged:** `body.fog-on` still toggles on the GM client (state
  fidelity), but the GM's rendered awareness is byte-identical with fog on or
  off, because the server sends the GM the complete, unlabeled-filter-free
  list either way (§3.6).
- **Radar-style awareness, GM variant:** GM markers are the small overlaid
  shape glyphs (true colors, no masking) + labels + the sidebar list — the
  same "sees all" vocabulary as wireframes §5.
- **0 tokens (GM alone, no players):** canvas is just the bare map. **Do not
  reuse `#no-map`** (that overlay is the *no map* condition). No overlay, no
  "empty state" card on the canvas — the empty state lives in the sidebar
  (§3.4) and the control-bar hint (§3.7) points the GM at GM Tools.

```
GM map view, desktop ≥1024px — 0 tokens (fresh session)
┌────────────────────────────────────────────────────────────────────────────────┐
│ LITTLEDUNGEONS ▸ The Gilded Crypt ● Connected  [✓] Fog of war  [☰]        #topbar
├──────────────────────────────────────────────────────────────────────────┬─────┤
│ #canvas-wrap                                                             │SIDE │
│ ┌──────────────────────────────────────────────────────────────────────┐ │BAR  │
│ │  (hint pill, 5 s)  "You're the GM — no token of your own…"           │      │
│ │                                                                      │      │
│ │   ▓▓▓▓▓▓▓▓   ▓▓▓▓▓▓▓▓        (bare map — grid/walls/doorways only,  │      │
│ │   ▓              no tokens, no rings, no "YOU")                      │      │
│ │   ▓▓▓▓▓▓▓▓▓▓   ▓▓▓ ▓▓▓                                             │      │
│ │  ▢ floor ▩ wall ▣ doorway │ ▲●■ dots        (12,7)  #legend/readout │      │
│ └──────────────────────────────────────────────────────────────────────┘      │
├────────────────────────────────────────────────────────────────────────────────┤
│ [◉ Select][▢ Floor][▨ Wall][▣ Door]  [ ] Ignore walls  "No tokens yet — add   │
│                                                one in GM Tools." #control-hint │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Sidebar — GM Tools + entity list

**Before:** the `#awareness` section for the GM ("Awareness — GM (sees all)")
listed **every** entity *including the GM's own `gm_character`* (the BUG-006
behavior: the own row "falls through and renders normally"), and the summary
count included it (the extra `· 1 neutral`).

**After:** the list is the GM's **token roster**: every entity that exists,
players' tokens included, in stable `entity_id` order. There is no own row —
the GM has none. Title copy changes (id `#awareness-title` unchanged):

- GM: `Awareness — GM (sees all)` → **`Tokens — all (GM sees all)`**
- Player: `Awareness — {name}` unchanged.

Row anatomy unchanged (wireframes §5.2): `[shape dot] [name] [kind·team]
(coords)`, clickable/keyboard-selectable, selection synced with canvas and
`#entity-tools`.

**Defined states:**

**(a) GM, 0 tokens** (GM alone; or after deleting every npc/enemy while
players are still connected the list is still non-empty — 0 tokens strictly
means no players joined *and* no tokens created):

```
┌───────────────────────────────┐
│ TOKENS — ALL (GM SEES ALL)    │
│ ┌───────────────────────────┐ │
│ │ No tokens on the map yet. │ │  empty-state row (muted, 12px)
│ │ Add the first one in      │ │
│ │ GM Tools above.           │ │
│ └───────────────────────────┘ │
│ 0 ally · 0 neutral · 0 enemy  │  #awareness-summary
└───────────────────────────────┘
```

- Empty-state row reuses the existing "no rows" pattern
  (`li.awareness-row.muted.small`), new copy for GM:
  `"No tokens on the map yet — add the first one in GM Tools."`
  (The existing player-facing copy `"No one else is out there yet."` stays
  for players with no *other* visible entities.)
- `#sel-entity-name` = `None`; `#team-select` and `#btn-delete-entity`
  disabled (already the case with no selection).
- `#control-hint` (GM, select tool, 0 tokens): **`No tokens yet — add one in
  GM Tools.`** (new branch ahead of the existing
  `"Click an entity to select it, then a tile"`).

**(b) GM, 1 token:**

```
┌───────────────────────────────┐
│ TOKENS — ALL (GM SEES ALL)    │
│ ┌───────────────────────────┐ │
│ │ ● Bram       player·party │ │  ← connected player's token (exists at
│ │   (1, 1)          [team ▾]│ │    join time, not GM-created)
│ └───────────────────────────┘ │
│ 1 ally · 0 neutral · 0 enemy  │
└───────────────────────────────┘
```

GM-only example with zero players (1 GM-created token):

```
│ ● Grom       npc·neutral      │
│   (3, 4)          [team ▾]    │
│ 0 ally · 1 neutral · 0 enemy  │
```

**(c) GM, N tokens** (players + created npcs/enemies):

```
┌───────────────────────────────┐
│ TOKENS — ALL (GM SEES ALL)    │
│ ┌───────────────────────────┐ │
│ │ ▲ Bram       player·party │ │
│ │   (1, 1)          [team ▾]│ │
│ │ ● Grom       npc·neutral  │ │
│ │   (3, 4)          [team ▾]│ │
│ │ ■ Vex        enemy·hostile│ │
│ │   (5, 6)          [team ▾]│ │
│ └───────────────────────────┘ │
│ 1 ally · 1 neutral · 1 enemy  │
└───────────────────────────────┘
```

- Stable order by `entity_id` (no sort UI — wireframes §5.2, unchanged).
- The summary line counts **only** the rows shown — the ghost
  `+1 neutral` for a GM token no longer exists. `n ally · n neutral ·
  n enemy` with zeros allowed.
- Rows are selectable (click / Enter / Space) exactly as before; the
  selected row gets the accent left border and `#sel-entity-name` +
  `#team-select` follow it.
- Rows for **players' tokens** are GM-editable exactly like any other
  (select → team/delete allowed; delete is still server-blocked with
  `cannot delete a player's own entity` — surfaced as the existing error
  toast). No visual distinction needed in v1; `kind` in the meta
  (`player·party`) already tells the story.

### 3.5 Create-token control (`#entity-tools` "New entity")

**Decision: the kind dropdown is exactly `npc | enemy`.**

- `player` stays server-only (joined players get it; no UI option) —
  already shipped (BUG-010), keep.
- `gm_character` is **removed as an option and as a creatable kind**.
  Reasoning: with no GM token, the kind has no meaning left; keeping it as a
  dead option would re-introduce exactly the confusion this change removes
  (a "GM character" the GM must then chase and delete). Default selection:
  `npc`.

**Before:**

```
│ New entity                    │
│ Name [ ____________ ]         │  #new-entity-name
│ Kind [ npc ▾ ]                │  #new-entity-kind
│        options: npc | enemy | gm_character
│ Team [ neutral ▾ ]  [ Add ]   │  #new-entity-team  #btn-new-entity
└───────────────────────────────┘
```

**After:**

```
│ New entity                    │
│ Name [ ____________ ]         │  #new-entity-name
│ Kind [ npc ▾ ]                │  #new-entity-kind  options: npc | enemy
│ Team [ neutral ▾ ]  [ Add ]   │  #new-entity-team  #btn-new-entity
│ Spawns on the last hovered tile (first free tile if none).   (unchanged)
└───────────────────────────────┘
```

- Only the `<option value="gm_character">` line in
  `index.html #new-entity-kind` is deleted; IDs, layout, and the
  create flow (hover-cell placement → `create_entity` → entity appears
  **selected**) are unchanged.
- If a stale client or a scripted message sends `kind:"gm_character"`, the
  server rejects it (§2.3) and the client surfaces the standard error toast
  (`kind must be one of npc/enemy`). No special UI needed.
- Note for a11y: native `<select>` — removing an option is
  screen-reader-transparent.

### 3.6 Fog-of-war toggle (`#fog-toggle`)

**Confirmed: the toggle stays, GM-enabled, no behavior change to the
control.** What "fog on" now means per viewer:

| Viewer | Fog off | Fog on |
|---|---|---|
| **GM** | Sees every entity, labeled, true colors. | **Identical.** The GM is never fogged and no longer needs an anchor entity — the server's LOS filter is simply not applied to the GM's snapshot. "Fog on" for the GM is a no-op render-wise; the GM keeps full vision at all times. |
| Player | Full radar (passes through walls), dots for every entity except self. | LOS-anchored to **the player's own token**, with "previously seen" memory (server-side, `_seen`). |

Concrete spec:

- GM: checkbox **enabled** (`.is-gm`), sends `{type:"set_fog", on}` — unchanged.
  Tooltip copy updated to state the semantics:
  `title="Toggle fog of war for players. As GM you always see everything."`
  (was: no tooltip on the GM side).
- Player: checkbox **disabled**, reflects broadcast `fog`,
  `title="GM controls fog of war"` — unchanged.
- Rendering: the client renders exactly the awareness items it receives;
  with fog on, players receive the filtered set and the GM receives the full
  set. **No client-side LOS code is required** (this supersedes the
  client-side Bresenham note in `wireframes.md §12.3`, which the
  implementation never needed — server filters in `_awareness_for`).
- `body.fog-on` class still toggles for everyone (state fidelity for any
  future styling); it must not gate GM rendering.
- Why the GM needs no anchor: the old client-side design anchored LOS on
  "own entity"; the GM has none and would have had no valid anchor. That
  question no longer arises — the rule is "GM is exempt", evaluated by role,
  not by geometry.

### 3.7 Selection & movement UX — GM (confirmed unchanged, audited)

Interaction model unchanged (wireframes §4.5): GM clicks a token → accent
selection ring; clicks a tile → `move` with `override` = `#override-toggle`
state; wall target without override → hint/toast + one-shot `Move anyway`;
arrow keys nudge the selected token; `Esc` deselects; paint tools
unchanged.

**Own-token assumption audit** (every code path that once had a GM
`gm_character` to lean on — what must hold now, and why it already does):

| Code path (app.js) | Old dependency on GM own-token | New behavior | Action |
|---|---|---|---|
| `onWelcome` → `state.selectedEntityId = role==="player" ? you.entity_id : null` | GM got `null` already | `you.entity_id` is now always null for GMs; `null` selection on join — same as before | none |
| `drawEntitiesAndDots` → `ring: isOwn && state.role==="player"` | `isOwn` was true for the GM's own token, but the ring was already player-gated | `isOwn` false for all GM tokens (`you.entity_id === null`) — no ring, no "YOU" pill | none (verify) |
| `drawEntitiesAndDots` → label `role==="gm" \|\| isOwn` | GM labeled all tokens anyway | GM still labels all tokens; `isOwn` branch never fires for GM | none |
| `drawSidebar` (BUG-006 block) | "For the GM, fall through and render the row normally so the GM sees its own `gm_character`" | The own row no longer exists; the BUG-006 comment is now misleading — update comment to "GM rows: every entity, no own row (GM has no entity)" | comment-only |
| `drawSidebar` summary counts | Counted the GM's own white item (extra `· 1 neutral`) | Counts exactly the rows rendered; no ghost count | none (tests change, §9) |
| `allEntities()` (GM → `state.entities`) | Included the GM's own entity | Now only real tokens; empty array is the valid fresh-session state | none |
| `entityAtCell` / GM click-select | GM could click-and-select its own token | Selects any real token; with 0 tokens, canvas clicks fall through to the hint (`No tokens yet…` / `Select an entity, then a tile`) | none |
| `selectEntity` (looks up name/team in `state.entities`) | — | Same; `None` when nothing selected | none |
| `sendMove` / "Move anyway" toast | GM sometimes retried moving its *own* token through walls | Works identically on created/players' tokens; `override` path unchanged | none |
| Fog class + rendering | — | GM never fogged server-side; client renders what it's sent (§3.6) | none |
| `firstFreeFloor()` (spawn spot) | GM's own token occupied `(1,1)` on the sample map | Spawn spot picks the first free floor for *new tokens* — unaffected; note player spawns shift (§2.8) | none |

**New micro-copy branch (only functional change):** `#control-hint` for
GM + select tool + `entities.length === 0`: `No tokens yet — add one in GM
Tools.` (see §3.4a). All other hint strings unchanged.

### 3.8 Player-side UX (confirmed unchanged — one dot fewer)

- Players still join with their own `player` token: full token + blue ring +
  "YOU" pill, self row first in their awareness list, two-tap movement of
  self only. **Nothing changes in the player UI code.**
- The only visible difference: the player's radar previously contained the
  GM's `gm_character` as an **unlabeled white dot** (`TEAM_COLORS["neutral"]`,
  `label: false`). That dot no longer exists. So:
  - A player in a session with the GM + 1 other player + 1 GM-created enemy
    now sees **2 dots** (green friend, red enemy) instead of 3 (the white
    ghost is gone).
  - No "missing GM" treatment, no placeholder dot, no explanatory UI — the
    GM is simply invisible on the radar, as a controller should be. (Their
    name is not leaked either: awareness items never carried names for
    players.)
  - Fog: player LOS anchoring is unchanged (own token). The removed white dot
    could have been hidden/shown by fog before; now it's just absent.
- Edge: a player whose *only* previously-visible entity was the GM token
  (GM alone + this player) now sees the player empty state
  `"No one else is out there yet."` — correct and intentional.

---

## 4. Edge cases

| # | Case | Expected UX |
|---|---|---|
| 1 | **GM joins an empty session (no map entities, no players)** | Welcome: `you.entity_id=null`, `entities=[]`. Bare map, no token, no ring. Sidebar 0-token state (§3.4a), summary `0 · 0 · 0`, hint `No tokens yet…`, 5 s first-run hint (§3.2). Toast: controller welcome copy. |
| 2 | **GM creates the first token** | GM sets name (or leaves blank → `"entity"`), kind `npc`, team, `[Add]` → spawns at last-hovered walkable tile (else first free floor). Broadcast `state` arrives: token renders selected (accent ring), its row appears in the list, summary updates (`0 ally · 1 neutral · 0 enemy` for a neutral npc), name input clears, first-run hint (if still up) dismisses. |
| 3 | **GM deletes all created tokens** | Select token → `Delete entity` → inline confirm (`Really? [3 s]`) → `delete_entity` → token + row vanish; `#sel-entity-name` = `None`; tools disabled; list falls to the 0-token state **only if** no players are connected; if players are connected, their rows remain (and are protected: `cannot delete a player's own entity` toast). Summary recomputes. No error, no crash, no residual selection ring on the canvas. |
| 4 | **GM reconnects** (tab closed, WS dropped, auto-reconnect re-sends `{type:"join", name, role:"gm"}`) | Server re-attaches the existing Player (name+role match). **No entity is re-spawned; entity count is identical before/after.** The GM's welcome reflects the current roster (`entities` = real tokens, `you.entity_id=null`). Any tokens the GM created persist at their positions; the GM's selection resets to `None` (fresh page state) — acceptable, matches today's reconnect behavior. |
| 5 | **GM fully leaves the session** (`leave()`) | The GM's Player record is removed; since `entity_id` is `null`, no entity is deleted and **no stray token remains** (old behavior removed the GM's `gm_character`). Remaining players are unaffected; roster unchanged minus the GM's row in each other's rosters — there is no GM row. |
| 6 | **Fog on, GM present** | §3.6: GM sees everything (labeled, unfiltered); players see LOS-filtered sets with previously-seen memory. Toggle stays enabled for the GM. Toggling fog on/off produces zero pixel change for the GM and only player-side changes. |
| 7 | **GM joins a session where players already exist** | GM immediately sees all player tokens labeled + selectable + movable; sidebar lists them; `you.entity_id` null; no own token anywhere. First-run hint does **not** fire (roster non-empty). |
| 8 | **Second "GM" attempts to join** | Unchanged: refused, `session full` toast in lobby. (Documented here so the no-token change isn't mistaken for a multi-GM feature.) |
| 9 | **Stale/garbage `kind:"gm_character"` sent via `create_entity`** | Server error `kind must be one of npc/enemy` → standard error toast. (Acceptance item, §8.) |
| 10 | **GM switches maps (`use_map`)** | Unchanged re-placement logic; with no GM entity there is one fewer entity to re-park. GM sees the roster on the new grid. |

---

## 5. Responsive behavior (delta only)

No new breakpoints, panels, or layout. Specifics that must keep holding:

- **Desktop ≥1024px:** docked 320px sidebar shows GM Tools + token list;
  the 0-token empty row and the `Tokens — all (GM sees all)` title fit the
  same 12px row rhythm (title is one line: `TOKENS — ALL (GM SEES ALL)`).
- **Tablet 768–1023px / <768px:** sidebar drawer unchanged; rows keep
  `[dot] [name] (coords)` with `kind·team` in the tap-expand per
  wireframes §8 — no change; the GM has no `is-own` row, so the drawer's
  "own row first" rule trivially holds (GM list starts at row 1 = entity
  `e1`).
- **Touch:** create flow unchanged (tap `[Add]` spawns at last-hovered =
  last-tapped cell on touch); 44px targets unchanged.
- **Legend / fog icon-collapse / control-bar wrap:** unchanged.
- **<480px lobby:** the added lobby sentence wraps; card height grows ~1
  line; no other effect.

---

## 6. Accessibility delta

- No new interactive elements → no new focus targets. The only new UI is
  copy (lobby sentence, empty-row text, hint string, tooltip) and the
  transient first-run hint (same `#canvas-hint` element as existing hints;
  it is decorative — the same information is persistently available in
  GM Tools + `#control-hint`, so it needs no live-region status of its own;
  keep it outside `aria-live` to avoid double-announcing, matching current
  hint behavior).
- Color/shape encoding unchanged (▲ green / ● white / ■ red; white dot keeps
  its dark stroke). Removing the GM token removes one *white* dot from
  players — no a11y regression (no information loss: the GM's token was
  never named for players).
- Keyboard path (list rows `tabindex=0`, Enter/Space select, arrows move the
  selected token, Esc deselect) works identically on the GM token list;
  with 0 rows there is simply nothing to tab to in `#awareness-list`.

---

## 7. Wireframe update log (for a later `wireframes.md` revision)

Do **not** edit `wireframes.md` as part of this task. When it is revised,
change exactly these spots:

1. **§3 Lobby** — `#lobby-note` copy gains the "GM has no token" sentence (§3.1).
2. **§4.1** — GM bullet: "every entity full token + label" → "every token
   (GM has no token of its own)".
3. **§4.5** — table unchanged; add hint row for GM/0-tokens.
4. **§5.2** — GM list title → `Tokens — all (GM sees all)`; delete the
   implicit own-row; add the 0-token empty-row copy; note summary counts
   only rendered rows.
5. **§6** — `#new-entity-kind (player|npc|enemy|gm_character)` →
   **`(npc|enemy)`**; add "create_entity rejects any other kind".
6. **§4.2** — `#fog-toggle` GM tooltip copy (§3.6).
7. **§10.1/§10.3** — no ID or state-class changes; panel-state table: GM
   selection state "none → any entity (never self — GM has no self entity)".
8. **§11** — `welcome` row: "select own entity (player)" — add "(GM: no
   entity, selection stays null)".
9. **§12.3** — replace "client replicates Bresenham for fog" with
   "fog filtering is server-side in `_awareness_for`; GM is role-exempt,
   needs no anchor".
10. **§12.2** — `.is-own` note: players only.

---

## 8. Testable acceptance checklist (engineer-verifiable)

Verifiable with the existing harness: `FakeSock`/`handle_message` (unit),
`tests/wsclient.py` + `make_server` (WS), and the Node static/JS checks in
`tests/test_frontend.py`.

**Server — join & roster**
- [ ] A1. GM join (explicit `role:"gm"`, fresh session) → `welcome.you.entity_id is None`, `welcome.you_entity is None`, `welcome.entities == []`, `welcome.awareness == []`; no entity with `kind=="gm_character"` exists in the session.
- [ ] A2. First client with no role / with `role:"player"` still becomes GM (first-client rule) and **also** gets no entity.
- [ ] A3. First *player* (after a GM) spawns at the first free floor — sample map `(1,1)` — with `kind=="player"`, `team=="party"`, `owner==<player id>` (was `(2,1)` when the GM occupied `(1,1)`).
- [ ] A4. GM disconnect + reconnect (same name+role) → re-attached to the same `Player` id; `len(session.entities)` identical before/after; positions of all tokens unchanged.
- [ ] A5. GM `leave()` → no entity removed beyond what belongs to leaving players; in a GM-only session, entity count is 0 before and after.

**Server — GM tools**
- [ ] A6. `create_entity` with `kind:"npc"` and `kind:"enemy"` succeeds at any walkable or wall cell (server allows GM placement; in-bounds check only).
- [ ] A7. `create_entity` with `kind:"gm_character"` → `{"type":"error","message":"kind must be one of npc/enemy"}`; same for `kind:"player"`; entity count unchanged.
- [ ] A8. GM deletes every npc/enemy → all succeed, `state_for(gm)["entities"] == []`, no error. Deleting a connected player's token → `cannot delete a player's own entity` (unchanged).
- [ ] A9. GM moves a created npc: A* path without override (wall → `no route — wall in the way`), `override:true` teleports through a wall, `place` still works. GM can move a **player's** token (existing `test_gm_can_move_any_entity` semantics); a player moving any non-owned token is `not allowed` (replaces the old "move GM entity" negative test).
- [ ] A10. `set_fog on` → GM snapshot: `awareness` contains **all** entities, every item `label=True`, including entities behind walls the GM cannot see by LOS. Player snapshot: LOS-filtered with previously-seen retention (existing behavior, must still pass with no GM token present).

**Frontend — GM view**
- [ ] A11. Static HTML: `#new-entity-kind` contains exactly `<option value="npc">` and `<option value="enemy">` — no `player`, no `gm_character`.
- [ ] A12. Feeding `onWelcome` a GM welcome with `you.entity_id:null`, `entities:[]` renders: no "YOU" pill, no `--own-ring` token, sidebar title `Tokens — all (GM sees all)`, one muted empty-row with the 0-token copy, summary `0 ally · 0 neutral · 0 enemy`, `#sel-entity-name` `None`, `#control-hint` = `No tokens yet — add one in GM Tools.`
- [ ] A13. After a `state` broadcast containing one created npc: token renders with label + overlaid team marker; list row appears (`npc·neutral`, coords); summary `0 ally · 1 neutral · 0 enemy`; the new token is auto-selected (accent ring on canvas, accent border on row); clicking the tile selects/moves exactly as A9; the first-run hint is gone.
- [ ] A14. Fog checkbox is **enabled** under `body.is-gm` and sends `{type:"set_fog", on}`; toggling on/off changes nothing in the GM's rendered awareness (same items, same pixels); checkbox state follows broadcast `fog` on `state`.
- [ ] A15. GM welcome toast text contains the no-token controller sentence; player welcome toast is byte-identical to today's.
- [ ] A16. Lobby `#lobby-note` contains the "GM has no token on the map" sentence.

**Frontend — player view**
- [ ] A17. Player welcome/`state` with GM in session: `you_entity` = own token; `awareness` items = other entities only; **no** item corresponds to the GM (verify by entity-id set: it equals the set of non-self entity ids, and no `gm_character` kind ever appears).
- [ ] A18. Player radar with GM + 1 other player + 1 enemy shows exactly 2 dots (green + red) — the white GM dot is gone; fog-on filtering and previously-seen behavior unchanged for players.
- [ ] A19. Player empty state: a player whose only other was the old GM token now sees `No one else is out there yet.` (GM-only session + 1 player).

**Regression guard**
- [ ] A20. `python -m unittest discover -s tests -t .` green after the §9 test updates; `GET /health` → `{"status":"ok"}`; sample map flow (upload → detect → move → awareness) still works end-to-end in `scripts/e2e_proof.py` after its re-targeting (§9).

---

## 9. Old tests / behaviors that must change or be removed

Concrete inventory (file-qualified; keep the *spirit* of each test, invert
the GM-entity assumption):

**`tests/test_session.py`**
- `TestJoins::test_first_join_role_gm_becomes_gm` — **invert:** GM join
  creates the Player with `entity_id is None`; `session.entities` is empty;
  no `gm_character` exists.
- `SessionTestCase.setUp` — `self.gm_ent = …entity_id` becomes `None`;
  every test touching `self.gm_ent` re-targets (see below).
- `TestJoins::test_player_gets_owned_party_entity_on_free_floor` — spawn
  expectation `(2,1)` → `(1,1)` (GM no longer occupies `(1,1)`); drop the
  "GM took (1,1)" comment.
- `TestJoins::test_reconnect_reattaches_same_player` — keep; add assertion
  that entity count/roster is unchanged across the GM re-attach.
- `TestGmTools::test_delete_gm_character_allowed_player_entity_blocked` —
  **re-target:** "GM deletes a created npc (allowed) vs a player's entity
  (blocked)"; the first half no longer needs a GM character — create an npc
  and delete it.
- `TestMovement::test_player_cannot_move_gm_entity` — **replace:** player
  cannot move a non-owned **npc** (`not allowed`). (The "move GM entity"
  concept ceases to exist.)
- `TestStateFor::test_gm_state_has_full_entities_and_labeled_awareness` —
  counts 3 → 2 (Alice, Bob); drop the `colors[gm_ent] == "white"` assertion;
  add assertion that no awareness item has kind `gm_character`.
- `TestStateFor::test_player_state_has_no_entities_and_correct_awareness` —
  Alice sees **only Bob** (green); drop `by_id[gm_ent]["color"]=="white"`.
- `TestFog::test_fog_on_filters_player_by_los_but_not_gm` — GM awareness
  length 4 → 3 (Alice, Bob, Shade); the "never fogged" assertion survives.
- `TestFog` spawn comments referencing the GM at `(1,1)` / Bob at `(3,1)`
  — update coordinates (Alice `(1,1)`, Bob `(2,1)`; re-check the
  place-to-`(4,1)`/`place` steps still express the same geometry).

**`tests/test_ws.py`**
- `test_join_gm_welcome_shape` — **invert:** `you.entity_id` is `None`;
  `entities == []`; `awareness == []`; `you_entity` absent/None.
- `test_two_clients_get_their_own_welcome` — GM `entities` length 2 → 1
  (Alice only).
- The awareness-color test asserting the GM token as a **white** dot in a
  player's overlay (the `item["color"] == "white" # neutral gm_character`
  assertion) — drop that item from the expected set.
- Any test that reads `self.gm_ent` from `welcome["you"]["entity_id"]` to
  move the GM's own character (e.g., the GM-override-through-wall scenario
  driven by the GM token) — re-target the override move onto a GM-created
  npc or a player token.

**`tests/test_frontend.py`**
- `TestBug001…` GM welcome fixtures — swap the GM payload to
  `you.entity_id:null`, `entities:[]` (the "no throw on welcome" property is
  what's under test; the payload must reflect the new contract).
- The GM-state fixture that asserts **both** the GM's own `e1` and the other
  `e2` render — now only `e2` (the "GM sees own gm_character" assertion is
  retired; keep a positive "GM renders all real tokens" case).
- `TestIndexHtml::test_bug010_no_player_kind_option` — tighten: options are
  exactly `npc` and `enemy`; **assert `gm_character` is absent** (rename to
  e.g. `test_kind_options_are_exactly_npc_and_enemy`).

**`tests/test_awareness.py`**
- `TestBuildAwarenessGmSeesAll::test_gm_gets_all_labeled_items` uses a GM
  with `entity_id="e1"` and asserts the own item — change to
  `entity_id=None` (the `test_gm_without_own_entity_still_sees_all` case
  becomes *the* case; merge).
- Otherwise this module is green as-is (it already models a GM with
  `entity_id=None`).

**`scripts/e2e_proof.py`**
- Steps 3–4 ("GM moves **gm_character** (1,1) → (5,3)" / override) —
  re-target to a GM-created npc placed at (1,1); update the printed labels
  and the Alice-visibility step (the white-dot check at (5,3) becomes a
  check that Alice sees the **npc** white/whatever-team dot — set the npc's
  team to `neutral` to preserve the white-dot assertion).
- The final Alice-awareness equality
  (`{bo_ent:"green", gm_ent:"white"}`) — becomes `{bo_ent:"green"}` plus the
  npc's item.

**Docs (follow-up, flagged — do not block the code change)**
- `PROJECT.md §4` — `EntityKind` union: mark `gm_character` deprecated/legacy
  (PM decision; §2.7 here recommends keeping the string as a non-creatable
  legacy value).
- `README.md` — "add `npc`, `enemy`, or `gm_character` entities" →
  "add `npc` or `enemy` tokens"; add a line that the GM has no token.
- `docs/qa/test-plan.md`, `docs/qa/qa-signoff.md` — update the
  `npc/enemy/gm_character` references; BUG-006/BUG-010 files stay as
  historical records.
- `docs/design/wireframes.md` — the §7 update log above.

---

## 10. Explicit non-changes & open items

- **No** change to: player join/spawn/movement UX, lobby flow & IDs, upload
  flow, paint tools, override/"Move anyway" mechanics, connection
  states, drawer behavior, legend, toast system.
- **No** new WS message types; no protocol field additions or removals
  (`you.entity_id` and `you_entity` already exist and simply carry `null`
  for GMs).
- **Open item (PM):** decide whether to drop `"gm_character"` from
  `EntityKind` in PROJECT.md §4 after one release cycle — this spec assumes
  "keep as legacy, never spawned, never creatable" (§2.7).
- **Open item (optional polish, not required):** a permanent subtle "GM"
  badge next to `#conn-status` in the top bar. Rejected for v1 of this
  change — the welcome toast, lobby note, and `Tokens — all (GM sees all)`
  title already cover role awareness; a badge is a cosmetic add-on that can
  ship independently.
