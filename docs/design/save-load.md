# Design — Save / Load Map State (GM saves, rejoin by name)

**Status:** build-ready spec, branch `feat/save-load`. **QA-verified and
committed** (`feat/save-load` @ `b49a373`; 18/18 ACs, live restart smoke
PASS — see `docs/qa/qa-signoff-save-load.md`).
**Source of truth:** `PROJECT.md`. Where this doc and `PROJECT.md` diverge,
`PROJECT.md` wins. This spec resolves the v1 limitation *"No session
save/load to disk"* (README §Limitations) and promotes it from
PROJECT.md §1 *soft/nice-to-have* to a shipped feature. *"No zoom/pan"* is
already resolved (pan-zoom feature) and is out of scope here.

**Consistency note (superseded sub-specs).** Two follow-up sub-specs amend
this one and **win over the text below** where they differ:
* `docs/design/save-load-delete-modal.md` (AC1–AC14) replaces the
delete-confirmation UX — delete confirm is now a **full-screen modal**,
not an in-row confirm. The in-row bar (`save-load-delete-confirm.md`) is
a superseded artifact; both are kept for the record. Where this doc says
"inline confirm in the row" (e.g. the §7.2/§7.3 Delete bullets), read it
as *the full-screen modal* described by that spec.
* The GM "saved as <name>" marker is driven by the server-issued
**`owner`** field on GM entity items (not a new `owner_name` field on the
wire); the wire stays frozen (see §8).

**Final UI↔API contract** (fields per action, for backend + frontend —
full reference in §12): `GET /api/saves` (any) · `POST /api/saves` (GM,
`{name?, id?}`) · `POST /api/saves/{id}/load` (GM, `{}`) ·
`DELETE /api/saves/{id}` (GM). All error bodies are `{"error": "<msg>"}`.
No WebSocket messages are added (§8).

**Code referenced (read, not modified by the spec):** `app/server.py`
(REST routes + error shapes), `app/session.py` (authoritative state, `join`,
`use_map`), `app/models.py` (`Grid`/`Entity`/`Player` dataclasses),
`app/main.py` (`maps_registry`, `get_session`, id helpers),
`app/static/index.html` / `app.js` / `style.css` (lobby, New map view, map
view, sidebar `#nav-panel`/`#entity-tools`, bottom bar), `docs/design/
generated-maps.md` (preview → "Open map in session" / `use_map` flow it
mirrors), `docs/design/door-features.md` + `docs/design/door-iconography.md`
(door + safe-door state shapes stored in a save).

---

## 1. Owner requirements (frozen)

| # | Requirement |
|---|---|
| R1 | Save the current map state to disk. |
| R2 | Reload a map **with its existing state** (grid + doors + safe doors + entities), not blank. |
| R3 | The GM needs a **load menu** — a save/load menu in the UI. |
| R4 | Players rejoin **with the same names** to get their characters back. Ownership rebinds by player **name** (player ids are ephemeral; the name is the stable identity). |

## 2. Frozen design decisions (detailed here, not redesigned)

| # | Decision |
|---|---|
| F1 | **Snapshot model.** "Save" writes **one self-contained JSON bundle** (grid cells + width/height + name + image name + doors + safe + entities, each entity carrying an `owner_name`) keyed by a save id. "Load" reads a bundle, registers it as a map, and the GM opens it via the **existing `use_map` flow**. A save is a complete state, **independent of any live session** — creating, loading, or deleting a save never mutates a live session's grid or entities. |
| F2 | **Ownership by name.** On save, each entity stores the controlling player's **name** (`owner_name`; `null` for GM-controlled entities). On load, entities carry `owner_name`. When a player joins with a matching name, the server binds that entity to the player (`owner` = new player id, `player.entity_id` assigned, the "YOU" ring is restored because it is driven purely by `you.entity_id`). **First-join wins a name**; unmatched / orphaned entities stay **GM-controlled** — nothing is ever lost. |
| F3 | **Persistence.** One JSON **file per save** in a **`saves/` directory under the repo root**. The save *record* (listed in the menu) is: `id` (short slug/timestamped), `name` (user label), `map_name`, `width`, `height`, `created_at`, `entity_count`. |
| F4 | **REST (additive only, NO wire-protocol change).** `GET /api/saves` (list, any role) · `POST /api/saves` (GM — save current map) · `POST /api/saves/{id}/load` (GM — load → register map) · `DELETE /api/saves/{id}` (GM, nice-to-have). No new WS message types; existing `welcome`/`state`/`path`/`error` frames are byte-identical in shape (see §8, AC16). |
| F5 | **NOT captured:** player connections, awareness radii, fog, WS/visibility state — all recomputed live from the surviving state (players dict, per-viewer awareness, per-player explored memory, fog flag). |

### 2.1 Derived consequences (fixed by F1–F5)

* **What IS captured** (everything a save must reconstruct, R2): the full
  `Grid` — `name`, `width`, `height`, `cells`, `image` (source image name,
  optional; the image **file itself** is NOT copied, see A7), **`doors`**
  (normal-door `"<x>,<y>" → "L"|"U"|"O"`), **`safe`** (safe-door states) —
  and every entity's `id`, `name`, `kind`, `team`, `x`, `y`, `color`, plus
  `owner_name`.
* **`saves/` is data, not code.** Add `saves/` to `.gitignore`.
* **The save menu is GM-visible** (F4); players see no save UI at all.
  `GET /api/saves` is callable by any role (frozen) but has **no UI surface
  for players**.

## 3. What changes (summary)

| # | Change | Where |
|---|---|---|
| S1 | New persistence module: save record + bundle read/write/validate (stdlib `json`/`os`). | new module `app/saves.py` |
| S2 | New additive REST routes `GET /api/saves`, `POST /api/saves`, `POST /api/saves/{id}/load`, `DELETE /api/saves/{id}`. | `app/server.py` |
| S3 | `GameSession.join` rebind: a name-matching unclaimed entity is bound to the joiner instead of spawning a fresh token. | `app/session.py` |
| S4 | GM **Saves panel** in the map-view sidebar (`gm-only`) + **Saved maps tab** in the New map view (lobby-reachable load menu) + save-name prompt + rejoin toasts/badges. | `app/static/index.html`, `app/static/app.js`, `app/static/style.css` |
| S5 | `saves/` dir + `.gitignore` entry. | repo root |
| S6 | Tests: save-module unit tests, REST tests, WS join-rebind tests, e2e QA script. | `tests/`, `scripts/qa_save_load.py` |

**What does NOT change:** the WS protocol (no new message types, existing
payload shapes byte-identical), the data model wire shapes (`Entity` keeps
its current fields; `owner_name` is a **disk-bundle-only** field), the
1-GM + 6-player rules, movement/awareness/pathfinding rules, door and
safe-door state machines, pan/zoom, upload/generate flows.

## 4. Snapshot format

### 4.1 Directory + files

```
agentteam/
└── saves/                      ← created on first save (missing dir ⇒ empty list)
    ├── <id>.json               ← one bundle per save
    └── ...
```

* One bundle per save: `saves/<id>.json`. No manifest file, no subfolders.
  `GET /api/saves` derives the list by scanning `saves/*.json` (each file's
  top-level keys *are* the record; a file that fails to parse is listed with
  `"corrupt": true` metadata — see E3 — or skipped per A12).
* Directory: `REPO_ROOT/saves`, i.e. the repo root's sibling of `app/`
  (`os.path.dirname(app/main.py)` → `BASE_DIR` → parent → `saves`). Created
  lazily on first write; never deleted.

### 4.2 Save id

`id = "<slug>-<ts>"`:

* `slug` = the save **name** lowercased, non-alphanumerics → `-` (same
  convention as `app.main.slug_map_id`), max 32 chars, min 1 char (fallback
  slug `save`);
* `ts` = local epoch seconds, e.g. `1735600000`.
* Example: `act-three-crypt-1735600000.json`.
* Ids are unique by timestamp; overwriting an *existing id* replaces that
  file (E2). Deleting removes only that file.

### 4.3 Bundle schema (the whole file)

```jsonc
{
  // ── record (what the list/menu shows; top-level so the scan can list
  //    without parsing entities) ──
  "id": "act-three-crypt-1735600000",
  "name": "Act Three — Crypt",        // user label; "" ⇒ GM saves with the map name
  "map_name": "The Gilded Crypt",
  "width": 24,
  "height": 16,
  "created_at": "2025-01-01T12:00:00",   // ISO-8601 local, second precision
  "entity_count": 4,

  // ── the complete map state (F1) ──
  "grid": {
    "name": "The Gilded Crypt",
    "width": 24, "height": 16,
    "cells": [["wall", "wall", "floor", "doorway", ...], ...],   // cells[y][x]
    "image": "crypt.png",            // source image NAME only (A7)
    "doors": {"12,3": "O", "7,9": "L"},     // normal doors; absent/null ⇒ none recorded
    "safe":  {"20,12": "U"}                     // safe doors; absent/null ⇒ none
  },

  // ── entities with name-based ownership (F2) ──
  "entities": [
    {"id": "e1", "name": "Alice", "kind": "player", "team": "party",
     "x": 4, "y": 3, "color": null, "owner_name": "Alice"},
    {"id": "e2", "name": "Bob",   "kind": "player", "team": "party",
     "x": 5, "y": 3, "color": null, "owner_name": "Bob"},
    {"id": "e3", "name": "Goblin", "kind": "enemy",  "team": "hostile",
     "x": 10, "y": 8, "color": null, "owner_name": null},        // GM-controlled
    {"id": "e4", "name": "Scribe", "kind": "npc", "team": "neutral",
     "x": 14, "y": 11, "color": "#f76707", "owner_name": null}
  ]
}
```

**`owner_name` semantics (F2, precisely):** at save time, for each live
entity: if `entity.owner` is a player id of a player P in the current
session, `owner_name = P.name`; otherwise (`owner is None` — GM-controlled,
or the player id no longer exists) `owner_name = null`. It is the **saved
player name**, not the entity's display name (a player character's display
name usually equals the player name, but the binding key is `owner_name`
regardless).

**Validation on read (load path):** the bundle must be a JSON object;
`width`/`height` ints in 1–60; `cells` a `height`-row × `width`-col matrix
over `floor|wall|doorway`; every `doors`/`safe` key a well-formed in-bounds
`"<x>,<y>"` on a `doorway` cell with a valid state char (exactly the
`Grid.__post_init__` rules — legacy safe `"C"` → `"U"` coercion applies,
same as `Grid.from_dict`); `entities` a list, each with int in-bounds
`x`/`y`, `kind` in `player|npc|enemy|gm_character` (legacy tolerated),
`team` in `party|neutral|hostile`, `name` a string ≤ 24 chars, `color`
string or null, `owner_name` string or null. **Any violation ⇒ the whole
load fails** with a clear error (E3; atomicity, A12). On load, an entity
whose stored position is a wall or out of bounds is **re-placed to the first
free floor/doorway cell** (same `_find_free_floor` fallback chain as
`use_map`), so a hand-edited bundle never strands a token.

### 4.4 `app/saves.py` public API (for the engineer)

```python
SAVES_DIR = <repo_root>/saves

def list_saves() -> list[dict]            # sorted created_at desc (then id); corrupt files flagged
def save_bundle(record: dict, grid: Grid, entities: list[dict]) -> str   # returns id; overwrites same id
def load_bundle(save_id: str) -> tuple[Grid, list[dict]]                 # (grid, entities-with-owner_name); ValueError on corrupt
def delete_save(save_id: str) -> None                                          # FileNotFoundError on missing
def id_for_name(name: str) -> str       # "<slug>-<ts>"
```

All functions are synchronous, stdlib-only, and safe to call from the
FastAPI route threadpool (file I/O is tiny; no locking beyond
"write-then-replace" via a temp file + `os.replace` so a crashed write can
never leave a half-written `.json`).

## 5. REST contract (additive only — F4)

Error shape follows the existing server convention: `{"error": "<message>"}`.

### 5.1 `GET /api/saves` — any role (including not-joined clients)

```
200  {"saves": [
        {"id","name","map_name","width","height","created_at","entity_count"},
        ...
     ]}          # sorted by created_at desc, then id desc
```

### 5.2 `POST /api/saves` — **GM only**

Body: `{"name": str?, "id": str?}` — `name` is the **user label**
(absent/blank ⇒ the current map's name); `id` is an **optional explicit
save id to overwrite** an existing save in place (E2 "Overwrite"; when
present it must be a valid id string and the server replaces that file;
absent ⇒ a fresh `<slug>-<ts>` id is minted). Validation order: role →
session exists → body is a JSON object (if present) → name string (≤ 40
chars if present) → id string (valid id charset if present).

| Outcome | Status | Body |
|---|---|---|
| OK — bundle written from the live "default" session | 200 | `{"ok": true, "id": ..., "name": ..., "map_name": ..., "width": ..., "height": ..., "created_at": ..., "entity_count": ...}` |
| Non-GM (a player is joined; or no GM exists — the role comes from the current session; see A8) | 401 | `{"error": "only the GM can save"}` |
| **No live session exists yet** (GM hasn't joined / opened a map in this server run) | 409 | `{"error": "no active map session — join as GM and open a map first"}` |
| Body not an object / bad name | 400 | `{"error": "'name' must be a string"}` |

The session snapshot is taken under the session lock:
`Grid.to_dict()` (which carries `doors`/`safe` exactly as on the wire) +
each entity's dict **plus** its computed `owner_name` (§4.3).

### 5.3 `POST /api/saves/{id}/load` — **GM only**

Body: none (or `{}`).

1. Read + validate `saves/<id>.json` (§4.3). **Corrupt/missing ⇒ 404**
   `{"error": "save not found: <id>"}` (both cases — the menu shows the
   same message; no crash, A12).
2. Build the `Grid` from the bundle (validated constructor) and register it
   in `maps_registry` under a **FRESH** id — `smap-<ts>` via a
   `_unique_map_id`-style helper (A13: a reload must be an **independent
   copy**; never reusing the id a previous load of the same save produced,
   so loading save X twice — or loading X after playing its first load —
   never mutates the map from the first load).
3. Reconstruct every entity (fresh `Entity` objects; positions re-placed per
   §4.3 if invalid) as **GM-controlled** (`owner = None`), tagged with
   their `owner_name` for the rebind step (§6.1).
4. **No session swap happens here** — the GM opens it via `use_map`
   (client-side, existing flow, §7.4). This keeps `POST …/load`
   side-effect-free on the live session, matching F1 ("independent of any
   live session").

| Outcome | Status | Body |
|---|---|---|
| OK | 200 | `{"ok": true, "id": <registry map id>, "save_id": <id>, "name": <map_name>, "width": ..., "height": ..., "entity_count": ...}` |
| Non-GM | 401 | `{"error": "only the GM can load"}` |
| Missing/corrupt | 404 | `{"error": "save not found: <id>"}` |

### 5.4 `DELETE /api/saves/{id}` — **GM only** (nice-to-have)

200 `{"ok": true}` · 401 non-GM · 404 `{"error": "save not found: <id>"}`.

### 5.5 Session/role note (A8)

REST has no per-request identity today; the "GM" for these routes is
**the GM of the current in-memory session** (`sessions.get("default")`):
GM exists ⇒ the caller may act GM; a session exists with a GM ⇒ non-GM
401s; **no session at all ⇒ the save-creation case is 409 (§5.2), while
load/delete — which need no live session — are permitted** (the GM may load
before players arrive; "GM-only" there is enforced as "no *player*-only
session exists"). This is consistent with the app's existing trust model
(no authentication — README Limitations) and with the tests, which drive
roles through WS joins before calling REST.

## 6. Server behavior

### 6.1 Rebind on join (F2 — the only `session.py` change)

In `GameSession.join`, **after** the existing same-name **re-attach**
passes (a name already in `self.players` re-attaches exactly as today —
that is the live "name already taken" path, §7.3) and **before** spawning a
fresh token for a new player:

```
if effective_role == "player":
    match = first e in self.entities, dict order, such that
             e.owner is None and e.owner_name == name   # owner_name is the
             # new optional Entity field (default None); never sent on the wire
    if match:
        match.owner = pid
        player.entity_id = match.id        # no fresh spawn; no floor consumed
    else:
        <existing fresh-token spawn, unchanged>
```

* **First-join wins (E7):** the match is taken once; a second entity with
  the same `owner_name` keeps `owner = None` and stays GM-controlled.
* **No match ⇒ exactly today's behavior** (fresh token at a free floor,
  E6).
* The bound entity keeps its **saved** position, `kind`, `team`, `color`,
  and `name` (the GM can still delete/rename/move it afterwards; the GM may
  also reassign it via the existing delete+create flow if the GM prefers).
* The rebind happens inside the existing join lock; the usual `_announce_join`
  welcome broadcast follows, so every client's next snapshot already shows
  the new `owner` (the GM's `entities` list and the joiner's `you.entity_id`).
* **Load-side tagging:** the loaded map's registry entry carries the
  per-entity `owner_name` list (stored with the entry, not on the `Grid`
  object); when the GM `use_map`s it, the session copies those values onto
  the reconstructed entities (new optional `Entity.owner_name` field,
  default `None` — additive, never serialized in `to_dict`, A5).
* **Reconnection after a restart:** player ids are ephemeral (new `p1, p2,
  …` sequence) — the rebind is the *only* re-attachment path across a
  restart, which is exactly why name is the identity (R4).
* Name comparison: **exact, case-sensitive, after the existing
  `.strip()`** (A9). No normalization.

### 6.2 Save-time snapshot (REST `POST /api/saves`)

* Source of truth = the live `default` session's `grid` (shared with
  `maps_registry`, same object paints mutate) + its `entities` + `players`.
* Door/safe state comes from `Grid.to_dict()` — the **same** `doors`/`safe`
  objects the wire emits (`to_dict` emits recorded state; absent ⇒ all
  locked defaults, which is the correct round-trip since unrecorded ⇒ `L`).
* `created_at` = save time; `entity_count` = `len(entities)`; the record's
  `name` = the label from the body or the map name.

### 6.3 What load deliberately does NOT do

* Does not touch any existing `GameSession` (no grid swap, no re-place, no
  broadcast). The swap happens when the GM sends `use_map` (§7.4) — the
  existing `use_map` handler then re-places out-of-bounds entities for
  **connected** players (its current behavior) and clears per-player
  explored memory (existing D3 behavior).
* Does not delete the save file. A loaded save stays in the list forever
  until the GM deletes it.
* Does not restore fog, awareness radii, explored cells, or connections (F5
  — recomputed live; A1).

## 7. UI — GM save/load menu

### 7.1 Placement decision (and why)

**Two surfaces, one pattern, both GM-only, sharing one list fetch:**

1. **Primary — a `Saves` panel in the map-view right-hand sidebar**
   (`#sidebar`), **GM-only** (`.gm-only`, i.e. visible iff
   `body.is-gm`), slotted **between `#entity-tools` (GM Tools) and
   `#awareness`**, after `#nav-panel` ("Map view") remains the first
   section. Rationale:
   * "Save **current** map" is only meaningful mid-session, and mid-session
     the GM's eye is on the right sidebar (that's where every other
     GM-only control already lives — GM Tools, team select, awareness
     slider). Bottom bar is taken by paint tools; top bar is crowded and
     holds view-level toggles — a save menu is a *session* control, not a
     *view* control.
   * It sits next to GM Tools (the other entity/map-state surface) and
     above Awareness (the viewer-only surface), keeping the sidebar's
     existing order: *map → editing → session state → awareness*.
2. **Lobby-reachable — a "Saved maps" tab in the New map view**
   (`#upload-view`, alongside **Upload map** | **Generate map** |
   **Saved maps**). Rationale:
   * A loaded map must be **opened in the session** — and the existing
     "Open map in session" button (`#btn-start-map`, which sends `use_map`)
     lives in `#upload-view`'s preview pane. Putting load here lets the GM
     load a save **from the lobby** (before players join, or after a
     restart) and open it through the *exact* preview → "Open map in
     session" flow the upload/generate tabs already use (BUG-002-safe:
     `use_map` on the same socket, no session-id switch).
   * This satisfies R3 ("a load menu in the UI") for the GM's natural
     entry point (lobby) and doubles as the pre-join restore path after a
     server restart (the headline scenario, E8).

Shared styling contract: sidebar panel = existing `.panel` +
`.section-label`; rows use the same dense-list type as `#awareness-list`;
buttons use `.btn` / `.btn-small` / `.btn-danger`; the tab bar reuses
`.map-source-tabs` / `.tab-btn`; the list empty state is a muted line
(`.saves-empty`) and the rejoin note a distinct muted/`--accent` line
(`.saves-rejoin-note`); error toasts reuse `toast(msg, "error")`.
**No new design tokens for the list/panels** — only the existing palette
(`--panel-bg`, `--accent`, `--danger`, `--text-muted`).

### 7.2 Wireframe — `Saves` panel (map view, GM sidebar)

```
 #sidebar (GM, map view)                      │
 ┌─────────────────────────────┐               │
 │ MAP VIEW                    │  #nav-panel (first, unchanged, both roles)
 │   [↑]  [−][+]               │
 │ [←][→]  zoom   L2 · 10×8    │
 ├─────────────────────────────┤               │
 │ GM TOOLS                    │  #entity-tools (unchanged, GM-only)
 │   Selected: Goblin          │
 │   …(team / awareness / new) │
 ├─────────────────────────────┤               │
 │ SAVES                       │  #saves-panel (NEW, GM-only)
 │  [Act Three — Crypt____]    │  #save-name   (save-name prompt; placeholder
 │ [  Save current map  ]      │               │   "Save name (optional)")
 │ ─────────────────────────── │               │
 │ Act Three — Crypt            │  .save-row    (name — user label)
 │   The Gilded Crypt · 24×16  │               │   map name · W×H
 │   4 tokens · Jan 1 12:00    │               │   entity_count · created_at
 │   [ Load ]  [ Delete ]      │               │
 │ ─────────────────────────── │               │
 │ Opening Night                │
 │   The Gilded Crypt · 24×16  │
 │   2 tokens · Dec 30 22:14   │
 │   [ Load ]  [ Delete ]      │
 │ ─────────────────────────── │
 │                             │  #saves-empty (only when zero saves)
 │  No saves yet.              │               │
 ├─────────────────────────────┤               │
 │ AWARENESS                   │  #awareness (unchanged, both roles)
 └─────────────────────────────┘               │
```

* List state: **`#saves-empty`** ("No saves yet.") shows iff the list is
  empty (E1); otherwise the rows render, most recent first (API order).
* Row layout: the header line = save **name** (`.save-row-name`, bold)
  on the left + `[ Load ]` (`.btn-small`) + `[ Delete ]` (`.btn-small
  .btn-danger`) on the right; a muted meta line = `map_name · W×H ·
  N tokens · <date>` (e.g. `The Gilded Crypt · 24×16 · 4 tokens ·
  Jan 1 12:00`). Corrupt rows (A12) show `name ⚠ corrupt` and **Delete
  only** (no Load, no meta line).
* Row for a corrupt save (if listed, A12): name + `⚠ corrupt` in
  `--danger`, **Delete only** (Load hidden/disabled).
* "Save current map" (`.btn` in the panel, not small — the primary save
  action): click → `POST /api/saves` with the trimmed `#save-name` value
  (blank ⇒ map name) → on 200: success toast `Saved "<name>" (N tokens).`
  (4 s), clear `#save-name`, re-`GET /api/saves`, refresh the list, scroll
  the new row into view. On 409 (no active session — can only happen if
  the GM opened the map via a different session id; A8): error toast with
  the server message. On 401: error toast (should not happen in-UI: the
  panel is GM-only).
* **Name-conflict (E2):** the label is *not* a key — the id is always
  fresh, so a same-named save simply becomes a second, distinct save.
  When the trimmed label equals an existing save's name, the button first
  shows an inline confirm row under it: `A save named "<name>" exists.` +
  `[ Save as new ]` (default) + `[ Overwrite ]`. "Overwrite" issues the
  save with `overwrite_id` = that save's id (server replaces the file;
  list row's `created_at`/state update). No `window.confirm` (existing app
  style is inline toasts/rows).
* **Load (sidebar):** click → `POST /api/saves/<id>/load` → on 200:
  `wsSend({type:"use_map", map_id: <response.id>})` on the **same socket**
  (exactly `openUploadedMap`'s behavior, BUG-002), keep `#map-view`, toast
  `Loaded "<name>" — N characters are waiting for players to join with
  matching names.` (only append the tail when N > 0, §7.4). The
  `state` broadcast then re-fits the view if dimensions changed (existing
  `applyState` behavior). On 404 → error toast `Save not found: <id>` and
  the row is dropped from the local list (re-GET).
* **Delete:** click → the **full-screen delete-confirmation modal**
  (centered `role="alertdialog"` over a backdrop: title `Delete save?`,
  the save name + meta, `[ Cancel ]` + `[ Delete ]` danger) → Confirm →
  `DELETE /api/saves/<id>` → row removed, toast `Deleted "<name>".` See
  `docs/design/save-load-delete-modal.md` (backdrop-click = Cancel,
  Escape = Cancel, focus trap + restore, in-flight `Deleting…` no-op
  dismissal, ghost-save guard). This replaces the original in-row confirm
  (`save-load-delete-confirm.md`, superseded artifact).
* List refresh triggers: panel first shown, after each save/load/delete,
  and on successful `use_map` (cheap: one small `GET`).
* Players: the whole panel is absent (`.gm-only` — display:none for
  non-GM, same as `#entity-tools`).

### 7.3 Wireframe — Saved maps tab (New map view / lobby-reachable)

```
 #upload-view (GM)
┌──────────────────────────────────────────────────────────────────────┐
│ LITTLEDUNGEONS ▸ New map                              [Back (Esc)]   │
├──────────────────────────────────────────────────────────────────────┤
│ (  Upload map  |  Generate map  |  Saved maps  )   ← #map-source-tabs│
│                                        + third .tab-btn "Saved maps" │
├──────────────────────────────────────────────────────────────────────┤
│ #saves-tab (panel, NEW)                                              │
│  Saved maps                                                          │
│  ────────────────────────────────────────────────────────────────   │
│  Act Three — Crypt                [ Load ]   [ Delete ]              │
│    The Gilded Crypt · 24×16 · 4 tokens · Jan 1 12:00                 │
│  ─────────────────────────────────────────────────────────────────── │
│  Opening Night                    [ Load ]   [ Delete ]              │
│    The Gilded Crypt · 24×16 · 2 tokens · Dec 30 22:14                │
│  ─────────────────────────────────────────────────────────────────── │
│  (No saves yet. Save one from the map view's Saves panel.)           │
└──────────────────────────────────────────────────────────────────────┘

   Load  ──► POST /api/saves/<id>/load (200 {id})
           ──► GET /api/maps/<id>  (grid + thumbnail, any role)
           ──► show #upload-preview EXACTLY as after upload/generate:
                #preview-title = "Loaded map", grid drawn on
                #preview-canvas, #pane-source hidden when grid.image is
                null (image file not persisted, A7) else shown as
                placeholder note "source image not saved";
                #preview-note = the rejoin note (§7.5) when the save
                carries characters, else the standard preview note,
                #btn-start-map enabled ("Open map in session").
           ──► GM clicks "Open map in session" → openUploadedMap() →
                wsSend({type:"use_map", map_id}) → #map-view.
                (The registered map already carries its entities, §5.3.)

   Delete ──► same full-screen confirm modal + DELETE as the sidebar panel.
```

* The tab is **GM-reachable**: the New map view is only entered by the GM
  (top-bar `New map…` or the lobby→upload flow before players join);
  there is no player path into `#upload-view`.
* **Save in the preview pane (covers E9, saving a never-opened map):** the
  preview's action row gains one left-side button `[ Save map state ]`
  (`.btn`, next to `Start over`), enabled only while a map is open in a
  live session (`state.joined && state.role === "gm" && state.grid`), with
  `title` = "Save this map's current session state from the map view".
  Click → `POST /api/saves` with the **map name** as label. On 409
  (map was never opened in a session) → error toast `This map isn't open
  in a session — open it first (map view's Saves panel).` The primary
  in-game save remains the sidebar panel; this button is a convenience for
  the preview flow, not a second save UI.

### 7.4 Post-Load flow (frozen: register → GM opens via existing `use_map`)

1. `POST /api/saves/<id>/load` registers the map + entities (§5.3) —
   **no** live session change yet.
2. GM opens it the standard way:
   * **sidebar Load** → client immediately sends `use_map` (same socket),
     stays in `#map-view`;
   * **lobby Load** → preview pane → "Open map in session" → `use_map`.
3. The existing `use_map` handler swaps the session's grid, re-places any
   **already-connected** players' tokens that no longer fit, clears
   explored memory, and broadcasts — all unchanged.
4. Server-side, the newly-bound session's entities are the loaded ones,
   each GM-controlled with its `owner_name` tag (§6.1). GM's next snapshot
   shows all of them labeled; the sidebar **Saves panel + GM Tools** rows
   show the unclaimed ones (step below).
5. **GM rejoin note (R4):** when `use_map` of a loaded save completes, the
   GM client (which knows the map came from a save because the load
   response said so) toasts, once, for 6 s:
   `Loaded "<name>". <N> character(s) saved as <Name1>, <Name2>, … are GM-
   controlled until a player joins with that exact name.` (N = count of
   entities with non-null `owner_name`; cap the name list at 4 + "…".)
      Plus **persistent affordances** so the GM always sees who each orphan
   is waiting on: (a) a **muted `saved as <name>` marker** on the GM
token/entity row for every GM-controlled entity the server tags with a
   saved player name (an additive, GM-only `owner` field on the GM's
   `entities` items — §8/A5), which disappears in the next snapshot once a
   player claims it; and (b) the **persistent rejoin note** in the Saves
   panel and the Saved maps tab — `Players must join with the same names
   to reclaim their characters — N character(s) are GM-controlled until
   then.` (hidden when N = 0 or when a non-save map is opened).

### 7.5 Rejoin UX (R4)

**GM's side** — covered in §7.4 (load toast + `saved as <name>` badges +
no other special state; orphans are ordinary GM-controlled tokens the GM
may move/delete as today — *nothing is lost*).

**Rejoining player's side:**

| Case | What happens | What the player sees |
|---|---|---|
| Joins with a name matching exactly one unclaimed entity | rebind (§6.1); `you.entity_id` = that entity; welcome carries it | token auto-assigned at its **saved position**, blue **YOU** ring (rendered from `you_entity`, unchanged render path), plus toast: `Welcome back, <name> — your character "<entity name>" has been restored.` |
| Joins with a name matching **no** saved entity (E6) | fresh token at a free floor (today's behavior) | normal `Welcome, <name>.` — the player is told nothing special; indistinguishable from a fresh join (least surprise) |
| **Empty name** | server rejects with `name required` (existing) before any rebind logic runs | lobby status line shows `name required` (existing `#lobby-status` path); no join, no rebind |
| **Name already taken** by a *connected* player | existing same-name **re-attach** wins (the joiner re-attaches to that player's connection exactly as today — a second live "Alice" cannot exist in one session) | the late joiner's screen behaves as the existing reconnect/reattach behavior; no rebind of any entity (the owner entity is already claimed by the connected player) |
| Second entity shares the winner's `owner_name` (E7) | first-join wins; the other stays `owner = null` | the joiner's token is the one matched first (dict order = bundle order); the orphan keeps its badge, stays GM-controlled |

Reconnected players on the **same server run** (no restart) already re-
attach by name+role (existing behavior) — the new rebind path only fires
for **new** player records (e.g. after a restart), so the two paths can
never both run for one join.

## 8. Wire protocol (frozen — NO change)

* **No new WS message types**, client→server or server→client. `join`,
  `use_map`, `move`, … are byte-identical.
* `welcome`/`state` payloads keep their exact shapes. Two **additive**
  points, both optional fields existing clients ignore:
  * GM `entities` items may carry an additive `owner` (string|null) — the
    saved controlling player's NAME (set only on a save-loaded map;
    cleared once a player claims the token) — used only for the `saved as
    …` GM marker (A5). Players' `entities` is `[]` as today;
    `you_entity` shape unchanged.
  * Nothing new on the `map` object.
* All new UI traffic is plain **REST** (§5), fetched via `fetch` like the
  upload/generate flows; no WebSocket involvement in save/load management.
* `Entity.to_dict()` is unchanged (no `owner_name` key) — the rebind tag
  lives on the in-memory `Entity` (new optional dataclass field, default
  `None`, excluded from `to_dict`) so the wire stays frozen.

## 9. States & edge cases

* **E1 — Empty save list.** `GET /api/saves` → `{"saves": []}`. Sidebar
  panel: "No saves yet." (no rows, Save action still enabled). Saved maps
  tab: "No saves yet. Save one from the map view's Saves panel." — still
  fully usable for Upload/Generate tabs. `saves/` missing entirely ⇒ same
  empty list (dir is created lazily on first save).
* **E2 — Save name already exists.** Labels never collide at the id level
  (fresh `<slug>-<ts>`). Same label ⇒ inline **overwrite-or-new** confirm
  (both surfaces, §7.2): *Save as new* (default — second distinct save) or
  *Overwrite* (same id, file replaced, list updated in place). No data loss
  from "Save as new"; overwrite is explicit.
* **E3 — Corrupt or missing save on load.** Missing id → 404 `save not
  found: <id>`. Unparseable JSON or any schema violation (§4.3) → the
  server answers the same 404 shape (the menu cannot distinguish and
  should not — it just shows `Save not found: <id>`), the file is left on
  disk untouched, no exception escapes (route try/except), **no crash**,
  no partial registration. The list marks it `corrupt: true`
  (row shows `⚠ corrupt` + Delete only, A12) so the GM can see and
  delete it.
* **E4 — Map with doors + safe doors + mixed entities.** Save captures the
  full `doors` + `safe` objects plus every entity (player-owned with
  `owner_name`, GM-owned with `null`). Load reproduces byte-for-byte:
  closed/locked normal doors still block LOS+movement; safe doors keep
  their `L/U/O` and the hostile-occupancy rule; player-owned entities await
  their names, GM-owned stay GM-controlled. Verified by AC2/AC3/AC4.
* **E5 — Two saved entities, same `owner_name`** (e.g. GM created two
  party tokens both bound to "Alice"). First join of "Alice" binds the
  **first** match (bundle order); the second stays `owner = null`,
  GM-controlled, badged `saved as Alice` (E7's rule, AC7).
* **E6 — Player joins with a name matching NO saved entity.** Fresh token
  at a free floor — **exactly today's behavior** (AC8). No toast beyond
  the normal welcome; orphans are unaffected.
* **E7 — First-join-wins detail.** Matching is `e.owner is None &&
  e.owner_name == name`, first in dict order. The winner's `owner_name`
  tag is cleared on bind (so the badge disappears and a later second join
  with the same name — only possible after the winner leaves — rebinds to
  the next unclaimed match, which is the desired semantics: a departed
  player's *exact* name is reusable; while connected, it re-attaches).
* **E8 — Server restart between save and load.** The save file survives on
  disk; the `saves/` scan needs no in-memory index. After restart the GM
  joins fresh (becomes GM), opens the Saved maps tab from the lobby,
  Loads, opens in session; players rejoin by name and rebind. This is the
  feature's headline scenario (AC15).
* **E9 — Saving a map that has never been opened in a session.** The
  registry grid exists but no session plays it: `POST /api/saves` → 409
  `no active map session — join as GM and open a map first` (clear,
  actionable). The preview's "Save map state" button shows this toast.
  (Conversely, a GM *can* load + open + then save a map — that map's
  session now exists — and E9 self-resolves.)
* **E10 — Load while players are connected (mid-session swap).** The
  load itself is inert (§5.3); the subsequent `use_map` behaves exactly as
  today's mid-session map swap: connected players keep their (fresh)
  tokens, re-placed onto the new grid if out of bounds (existing
  `use_map` rule) — the GM may then delete the leftover tokens; saved
  tokens await names. Documented, not prevented (A4).
* **E11 — Save while players are connected.** Fully supported and normal:
  the snapshot is taken under the lock; the save is a frozen copy
  (subsequent moves never affect it). Re-saving overwrites per E2.
* **E12 — Degenerate bundles (hand-edited).** Entity on a wall / out of
  bounds → re-placed to first free floor on load (§4.3); zero floor cells
  → bundle still valid, tokens fall back per the existing
  `_find_free_floor` chain. A grid dimension change can never happen (the
  bundle's cells ARE the grid).

## 10. Acceptance criteria (for QA)

Every AC is testable with the existing harness: `tests/` (REST via
`ThreadingHTTPServer` boot + raw-socket `tests/wsclient.py`),
`tests/test_session.py` (in-process `FakeSock`), and a new
`scripts/qa_save_load.py` e2e in the style of `scripts/qa_doors.py`.

* **AC1 — Save to disk (R1).** `GET /api/saves` → `{"saves":[]}`. GM joins
  WS, opens a map, `POST /api/saves {"name":"T1"}` → 200 record with
  `id`/`name`/`map_name`/`width`/`height`/`created_at`/`entity_count` all
  correct. On disk: `saves/<id>.json` exists and parses; its `grid`
  equals the session grid (`cells`, `doors`, `safe`) and every entity
  appears with `x,y,kind,team` equal to the live state. (E1, E9)
* **AC2 — Load restores full state, not blank (R2).** After AC1, restart
  the in-memory state (new process / cleared registry): `POST
  /api/saves/<id>/load` → 200 with a fresh registry id; `GET
  /api/maps/<new_id>` shows the exact `cells`, and `doors`/`safe` equal
  the saved objects (a closed door at the saved coordinate blocks a WS
  move, an open one allows it; a safe door refuses hostile occupancy).
  The map appears in `GET /api/maps`. (E4)
* **AC3 — Save→load→save round trip.** Save the loaded session again (new
  label) and `GET /api/saves` shows both rows with independent ids; file
  contents match except `id`/`name`/`created_at`. (F1 independence)
* **AC4 — Ownership rebind by name (R4).** Session with loaded entities
  (owner_names "Alice", "Bob"; one GM-owned "Goblin"). WS: player joins as
  "Alice" → her `welcome.you.entity_id` is Alice's saved entity **at its
  saved position** (not a fresh spawn cell); GM's snapshot shows that
  entity's `owner` = Alice's new player id; Bob's and Goblin's are still
  `null`. A second, fresh process join of "Bob" binds Bob's token.
* **AC5 — Orphan handling.** From AC4's state: GM can move the unclaimed
  "Bob" token (GM move allowed), delete it, and create a new one — no
  error paths, no stuck state. Unclaimed entities keep `owner: null` in
  every GM snapshot and carry `owner_name` (badge data) until claimed.
  (F2 "nothing lost")
* **AC6 — Restart persistence (R4/E8).** Full server stop → start (fresh
  in-memory state): `GET /api/saves` still lists the save (list comes
  from disk); load + open works; rebind per AC4 succeeds with the new
  ephemeral player ids.
* **AC7 — Duplicate owner_name: first-join wins (E5/E7).** Two saved
  entities with `owner_name "Alice"`. "Alice" joins → binds the FIRST
  (bundle order); the second still `owner: null` with the tag intact; a
  second GM snapshot confirms; if "Alice" leaves and rejoins, the re-
  attach/rebind rules of §7.3 apply (no double-bind ever: exactly one
  entity has `owner` = that player id at all times).
* **AC8 — Non-matching join gets a fresh token (E6).** Player joins as
  "Zed" (no saved entity): fresh `kind:"player" team:"party"` token on a
  free floor cell, `you.entity_id` set, saved entities untouched. Identical
  behavior to today's join (regression).
* **AC9 — Name edge cases (E6 cases).** Empty/whitespace name → `name
  required` (lobby status; no rebind attempted). Case-sensitive: "alice"
  does NOT bind "Alice". A second live join of an already-connected
  player's name re-attaches (existing behavior) and does not rebind
  anything.
* **AC10 — Corrupt / missing save (E3).** (a) `POST
  /api/saves/nope/load` → 404 `{"error":"save not found: nope"}`.
  (b) Write `saves/bad.json` containing `{not json` → load of `bad` → 404
  (same shape), server still healthy (`/health` ok, `GET /api/saves` ok),
  file untouched. (c) Truncated bundle (valid JSON, `height` mismatch) →
  404, no map registered.
* **AC11 — GM-only perms (F4).** While a player is joined (GM exists):
  player-driven `POST /api/saves` → 401 `only the GM can save`; `POST
  /api/saves/<id>/load` → 401; `DELETE /api/saves/<id>` → 401. With a
  GM-only session: all three succeed. With no session at all: save → 409;
  load/delete → allowed (§5.5). `GET /api/saves` succeeds in all three
  cases (any role, no join required).
* **AC12 — Doors + safe doors + mixed entities survive (E4).** A crafted
  session: one normal door `O`, one `L`, one safe door `U`, entities
  {Alice-player, Goblin-enemy, Scribe-npc}. Save → load (fresh process) →
  open → assert: door `O` walkable + LOS-transparent, door `L` blocks
  both, safe door `U` walkable for party/neutral and rejects a hostile
  `place` even with `override:true` (`cannot place a hostile on a safe
  room door`), token count and positions exactly as saved.
* **AC13 — Load menu present and role-gated (R3).** DOM (Node harness
  `tests/js/harness.js`): after a GM welcome, `#saves-panel` is visible
  (not `display:none`) in the map sidebar between `#entity-tools` and
  `#awareness`; `#save-name` + Save button enabled. After a player
  welcome, `#saves-panel` is hidden (`.gm-only`). `#map-source-tabs`
  contains a third tab; selecting it shows the save rows; rows render
  name / map name / `W×H` / token count / date + Load + Delete buttons;
  Delete opens the full-screen confirm modal (save-load-delete-modal
  spec) and a corrupt row shows `⚠ corrupt` + Delete only.
* **AC14 — Post-Load flow reuses `use_map` (F1/BUG-002).** Lobby Load →
  preview → "Open map in session" and sidebar Load both result in exactly
  one `use_map` on the GM's **existing** socket (no new WS, no
  `wsSession` change); the `state` broadcast then shows the loaded map to
  GM *and* any connected players (no one stranded — BUG-002 regression
  guard). The GM sees the rejoin toast with the correct N/name list.
* **AC15 — Save-name conflict (E2).** Two saves, labels "T1" and "T1":
  "Save as new" ⇒ list has two rows, distinct ids, both loadable, both
  byte-correct for their moments. "Overwrite" ⇒ the old id's file is
  replaced (created_at/`entity_count` update; old id no longer loads the
  old state; no duplicate row).
* **AC16 — No wire change (F4/§8).** Run the pre-existing suite: every
  `tests/test_ws.py` + `test_session.py` + `test_api.py` + `test_frontend.py`
  snapshot/shape assertion still passes; `scripts/e2e_proof.py` is all-✓.
  Explicit check: a `welcome`/`state` payload to a non-GM build (client
  that never knew `owner_name`) parses and renders (additive fields
  ignored); `Entity.to_dict()` output has no `owner_name` key.
* **AC17 — Independent-of-session semantics (F1).** Creating a save does
  not mutate the live session (a subsequent `state` broadcast is identical
  to the pre-save one). Loading a save while *another* map is open does
  not change the session until `use_map`. Loading the same save twice
  yields two distinct registry maps; mutating (painting) one does not
  touch the other or the save file.
* **AC18 — Delete.** `DELETE /api/saves/<id>` → 200; file gone from disk;
  `GET /api/saves` omits it; deleting again → 404. Deleting a save that
  is currently loaded does not affect the loaded (in-memory) map.

## 11. Assumptions

* **A1 — Nothing live is persisted.** Player connections, awareness
  radii, fog, explored-map memory, per-viewer WS state are NOT in the
  bundle; after load they are recomputed (fog `false` until the GM
  toggles it; radii default until the GM sets them; explored memory
  rebuilt from scratch). (F5)
* **A2 — Default session.** The save routes operate on the in-memory
  `"default"` session (the app's normal single-session tabletop use).
  Multi-session ids are out of scope.
* **A3 — Name is the identity.** Player ids (`p1, p2, …`) reset on every
  server start; case-sensitive stripped names are the stable identity for
  rebind. Same-named humans are assumed to be the same player (local
  trust model; no auth — README Limitations).
* **A4 — Mid-session loads are the GM's choice.** A load + `use_map` with
  players connected replaces the map for everyone (existing `use_map`
  semantics: tokens kept, re-placed). The rejoin toast + `saved as …`
  badges make the mixed state legible; the GM manages leftovers.
* **A5 — Additive wire fields only.** `owner_name` on GM `entities` items
  (badge) is additive/optional; every pre-existing client ignores it
  safely. `Entity` gains an optional `owner_name` dataclass field that
  `to_dict` does NOT emit (wire shape frozen).
* **A6 — One file per save, repo-root `saves/`.** No database, no
  compression, no rotation; GM-managed file count. `saves/` added to
  `.gitignore`; writes are temp-file + `os.replace` (atomic).
* **A7 — Image file not persisted.** The bundle stores `image` (source
  name) for display only; the PNG/BMP bytes are not copied into
  `saves/`. Loaded maps preview with grid + server-style thumbnail only
  (the preview's source pane shows a "source image not saved" note).
* **A8 — Role source for REST.** "GM" for the new routes = the GM of the
  current `default` session; with no session, save→409, load/delete
  permitted (players can't reach these UIs pre-join anyway). Matches the
  app's existing no-auth model and the test boot sequence (join GM over
  WS first).
* **A9 — Name matching is exact + case-sensitive after trim** (24-char
  lobby limit applies; the lobby's `syncLobbyButtons` already blocks empty
  names in-UI, and the server re-checks).
* **A10 — Loaded maps are independent copies** with fresh `smap-<ts>`
  registry ids; a save file is the single source of truth for what a load
  produces (AC17).
* **A11 — Bounds.** Grids up to 60×60 (upload/generate caps); bundles with
  out-of-range dimensions are rejected as corrupt (E3).
* **A12 — Corrupt files are surfaced, not fatal.** The list marks them
  `corrupt: true` (row: ⚠ + Delete only); a missing dir ⇒ empty list;
  load errors are always clean 404s (never a 500, never a crash).
* **A13 — Rebind never double-assigns.** Invariant, asserted by AC7: at
  all times at most one entity has `owner == pid`; at most one entity has
  a given non-null `owner_name` among unclaimed entities.
* **A14 — Existing behaviors untouched.** `use_map` re-placement of
  connected players' tokens, explored-memory clearing on map swap,
  same-name re-attach, fresh-token spawn, and the lobby flow all keep
  their current semantics (regression guard: AC16 + AC8).
* **A15 — UI copy and placement.** Copy strings in §7 are the shipped
  strings; the sidebar slot (between GM Tools and Awareness) and the
  third-tab lobby placement in §7.1 are the decisions QA checks in AC13.
* **A16 — Date formatting.** `created_at` stored ISO-8601 (local, second
  precision); UI renders a compact local form (e.g. `Jan 1 12:00`) in the
  list rows.

---

## 12. Final UI↔API contract (fields per action — build reference)

This is the single reference for backend_engineer + frontend_engineer. It
mirrors §5/§7 and is **frozen**: additive-only, NO WebSocket changes, GM is
the only actor with save/load/delete rights. All bodies/responses are JSON;
every error body is exactly `{"error": "<message>"}` (the server's verbatim
string is what the UI toasts).

### 12.1 `GET /api/saves` — any role (no join required)
* **Request:** none.
* **200 →** `{ "saves": [ <save-record>, ... ] }` — sorted `created_at`
  desc, then `id` desc.
* **`save-record` fields:**
  | field | type | meaning |
  |---|---|---|
  | `id` | str | save id (`<slug>-<ts>`), the load/delete key |
  | `name` | str | user label (blank ⇒ the map name was stored) |
  | `map_name` | str | the map's name at save time |
  | `width` | int | grid width (cells) |
  | `height` | int | grid height (cells) |
  | `created_at` | str | ISO-8601 local, second precision |
  | `entity_count` | int | number of saved entities |
  | `corrupt` | bool? | **present & true only** for a file that failed to parse (A12/E3) — UI then shows `⚠ corrupt` + Delete only |
* **Empty/missing dir →** `{"saves": []}` (E1).

### 12.2 `POST /api/saves` — **GM only**
* **Request body:** `{ "name"?: str, "id"?: str }`
  * `name` — the user label; **absent/blank ⇒ the current map's name**; a
    string of **≤ 40 chars** when present.
  * `id` — **optional explicit save id to overwrite** (E2 "Overwrite").
    When present it must be a valid id string; the server **replaces that
    file** in place. **Absent ⇒ a fresh `<slug>-<ts>` id is minted**
    (collision-bumped so two saves in the same second stay distinct).
* **200 →** `{ "ok": true, "id": <actual id written>, "name": <label or map
  name>, "map_name": <map name>, "width": int, "height": int,
  "created_at": <new>, "entity_count": int }` — the record that now exists
  on disk. `id` is the id actually written (the explicit `id` if given,
  else the freshly minted one).
* **Errors (validation order: role → session → body → name → id → write):**
  | status | condition | `error` string |
  |---|---|---|
  | 401 | a player is joined (non-GM caller) | `only the GM can save` |
  | 409 | no live (default) session yet | `no active map session — join as GM and open a map first` |
  | 400 | body not a JSON object | `request body must be a JSON object` |
  | 400 | `name` present, not a string / > 40 chars | `'name' must be a string` |
  | 400 | `id` present, empty / invalid charset | `'id' must be a valid save id` |
* **Snapshot semantics (F1/E11):** taken under the session lock; `owner_name`
  on each entity = the controlling player's **name** (null for
  GM-controlled / stale ids); frozen copy — later moves don't touch it.

### 12.3 `POST /api/saves/{id}/load` — **GM only**
* **Request body:** none, or `{}`.
* **Path param `id`:** the save id; must be a bare, safe filename stem
  (charset `[A-Za-z0-9._-]`, never `.`/`..`, no separators — no traversal).
* **Behavior:** reads + validates the bundle; registers it in the map
  registry under a **FRESH** `smap-<ts>` id (every load is an independent
  copy — A10); the registry entry carries the per-entity `owner_name` list
  for the `use_map` rebind. **No live-session change** (F1) — the GM opens
  it via the existing `use_map` flow.
* **200 →** `{ "ok": true, "id": <fresh registry map id>, "save_id": <id>,
  "name": <map_name>, "width": int, "height": int, "entity_count": int }`
  * `id` = the **new** map id the client then sends as
    `use_map.map_id` (or previews via `GET /api/maps/<id>`).
  * `entity_count` = the number of loaded entities the client uses for the
    rejoin note/toast.
* **Errors:**
  | status | condition | `error` string |
  |---|---|---|
  | 401 | a player is joined (non-GM caller) | `only the GM can load` |
  | 404 | **missing OR corrupt** file (one shape for both, E3) | `save not found: <id>` |
  * 404 leaves the file untouched, registers nothing, never 500s, never
    crashes. A loaded save stays listed until the GM deletes it.

### 12.4 `DELETE /api/saves/{id}` — **GM only**
* **Request body:** none. **Path param `id`:** as in §12.3.
* **200 →** `{ "ok": true }` — file removed; `GET /api/saves` then omits it.
* **Errors:** 401 non-GM (`only the GM can delete` / same 401 family); 404
  `save not found: <id>` (deleting an already-deleted id). Deleting a save
  that is **currently loaded in the session** does NOT affect the
  in-memory map (AC18).

### 12.5 Client-side flow (what the UI does with the above)
* **List/refresh:** `GET /api/saves` → `state.saves`; render both surfaces
  from this one fetch (panel first shown, after each save/load/delete).
* **Save:** `onSaveCurrentMapClick()` — trim `#save-name`; if it matches an
  existing save's `name`, show the inline **Save as new / Overwrite**
  confirm (E2); otherwise `POST /api/saves` with `name` (blank ⇒ omit, server
  defaults to map name). "Overwrite" ⇒ `POST /api/saves` with `id: <that
  save's id>`. On 200: toast `Saved "<name>" (N tokens).`, clear the input,
  re-`GET`, scroll the new row into view.
* **Load (sidebar):** `POST /api/saves/<id>/load` → on 200, on the **same**
  WS socket `wsSend({type:"use_map", map_id: <resp.id>})` (BUG-002-safe),
  stay in the map view; fire the rejoin note (§7.4/§7.5). On 404 → toast +
  re-`GET` (row drops).
* **Load (lobby tab):** `POST /api/saves/<id>/load` → on 200 `GET
  /api/maps/<resp.id>` → the shared preview pane → **Open map in session**
  → `use_map` (same socket). The preview shows grid + thumbnail, a "source
  image not saved" note, and the rejoin note.
* **Delete:** open the full-screen modal → Confirm → `DELETE
  /api/saves/<id>` → close on resolution, re-`GET`, toast
  `Deleted "<name>".` (modal specifics in save-load-delete-modal spec).
* **Wire is frozen (§8):** `use_map`/`state`/`welcome` are unchanged;
  the only additive surface is the GM `entities` item's `owner` field
  (saved-name marker) and `you.rebound` on a rebind welcome (BUG-014).

---

*Scope guard: this spec touches `app/saves.py` (new), `app/server.py`
(routes), `app/session.py` (rebind in `join`), `app/models.py` (optional
`Entity.owner_name`, excluded from wire), `app/static/*` (menu + toasts),
`saves/` + `.gitignore`, and tests. No other module, no other wire
surface.*
