# Tactica — QA Test Plan & Independent Review (Pass 2)

**Scope:** Adversarial, independent review of the full stdlib stack + the
browser-only frontend (`app/static/*`), which the 168 green unit tests never
execute. Method: close code reading + data-flow tracing (no browser/shell
available). The 168 unit tests are green and were **not** the thing under test
here — they cover backend logic, not the frontend or the WS↔JS integration.

**Key finding of record:** the frontend was rewritten for the live WS protocol
and has **never run in a browser**. It does not execute in any unit test. The
highest-impact defects below are frontend and frontend↔backend integration.

---

## A. What I verified per hard requirement (§1)

Verdicts: **PASS** (meets spec), **AT-RISK** (works but has a real defect),
**FAIL** (does not meet spec in the shipped form).

| # | Hard requirement | Verdict | Why / evidence |
|---|------------------|---------|----------------|
| 1 | Exactly 1 GM + up to 6 players, one session | **AT-RISK** | Server enforces it correctly (`session.py:179-195`: 2nd GM refused, 7th non-GM refused, `"session full"`). Verified by `test_ws.py` (2nd GM + 7th join refused) and `e2e_proof.py`. **But** the only way a GM swaps in a custom map (`openUploadedMap`, `app.js:1089`) changes the WS `session` id, which **strands players** in the old session (BUG-002) — so the "one session" guarantee breaks exactly when the flagship flow is used. Also BUG-011: the cap is enforced but the 7th joiner gets **no feedback** (hidden toast). |
| 2 | Grid cells `floor`/`wall`/`doorway` | **PASS** | `models.py` validates cell types; `Grid.__post_init__` rejects invalid values. Frontend reads `map.cells[y][x]` correctly. |
| 3 | Walls block movement (no diagonal corner-cut) | **PASS** | `pathfinding.py:52-75` `is_valid_step` forbids a diagonal when **either** elbow cell is a wall; `find_path` uses it. Verified by `test_pathfinding.py` (blocked-by-wall, no corner-cut). Server is authoritative (`session.py:444-461` recomputes via `find_path`). Frontend never bypasses (always sends a `move` that the server validates). |
| 4 | Doorways/gaps walkable | **PASS** | `WALKABLE_CELLS = {"floor","doorway"}` (`pathfinding.py:32`); doorway cells are traversable by A*. Verified by tests + `e2e_proof.py` step 2 (Alice moves through doorway (5,5)). |
| 5 | GM override moves through walls | **PASS** (server) / **AT-RISK** (frontend) | Server: `session.py:452-456` `override` → direct set, walls ignored; override is GM-only (players sending `override:true` get `not allowed`). Frontend: GM move requests carry `override: els.overrideToggle.checked` (`app.js:794`) and the one-shot "Move anyway" sends `override:true` (`app.js:244-247`) — GM-only. **However** the "Move anyway" matcher does an **exact** string compare against `"no route — wall in the way"` (`app.js:241`), brittle to any wording change (fragile, not broken). Frontend never lets a *player* send `override:true` (players always send `false`, `app.js:806/809`). |
| 6 | Awareness: player = dots (no names) + own token; GM sees all | **AT-RISK** | Server side is correct: `awareness.py` `build_awareness` gives the GM every entity (name/kind/label) and gives a player every entity *except its own* as color-only dots; `session.py` sends players `entities: []` + `you_entity` + per-viewer `awareness`. **Frontend** renders correctly in the main case (player dots, GM full tokens+labels, blue "YOU" ring) **but** BUG-006 drops the GM's *own* token from the sidebar list, and the entire player-view render is currently unreachable because of BUG-001. Net: cannot be confirmed as fully PASS until BUG-001/006 are fixed. |
| 7 | Player may only move their own character | **PASS** | Server: `session.py:431-437` a non-GM may only move `entity.owner == player.id`, else `not allowed`. Frontend: player move requests always target `state.you.entity_id` (`app.js:806/809/794`). BUG-007 is a UX nuance (clicking own token moves rather than re-selects) but does not let a player move another entity. |
| 8 | Upload image → auto-detect walls/doorways + paint fallback | **AT-RISK** | Flow is wired: `app.js:1032 uploadMap` → `POST /api/maps/upload` (JSON `name/image_b64/dark_is_wall/cols/rows`) → `main.py:288 _handle_upload` → `detect_grid` → 200 with `id/name/width/height/cells/thumbnail`. Field names match; `data.thumbnail` is read by the client (`app.js:1056`). Paint fallback works (GM `paint` over WS + REST). **But** the detection pipeline has a real multi-channel-PNG decode bug (BUG-004) that silently corrupts walls/doorways for the common RGB PNG case, and the "open in session" step strands players (BUG-002). The doorway heuristic + auto-invert + Otsu are otherwise correct and test-covered. |

**Soft requirements:** fog-of-war toggle (GM-operated, player read-only) — implemented both sides, off by default, `has_line_of_sight` Bresenham correct (verified `test_session.py` fog tests); smooth movement — **broken** by BUG-003 (teleports instead of animating); session save/load to disk — **not implemented** (all state is in-memory `dict`s in `main.py`; no persistence). No zoom/pan (documented v1 trade-off).

---

## B. Defects found (see `BUG-NNN.md`)

Ranked by severity.

| ID | Sev | Area | One-line |
|----|-----|------|----------|
| BUG-001 | **P0** | `app.js:458,962` | `allEntities()` is called but **defined nowhere** → `ReferenceError` on the first map render for **every** client; the whole map view is dead until fixed. |
| BUG-002 | **P1** | `app.js:1089-1101`, `main.py:92` | "Open map in session" switches the WS **session id** to the new map's id, leaving players in the old session — breaks the 1-GM+players-together guarantee (reqs #1+#8). |
| BUG-004 | **P1** | `imaging.py:190,197-200` | PNG **Average/Paeth** filter reconstruction uses `prev_row[x]` instead of `prev_row[x+c]` per channel → corrupts RGB/RGBA images (grayscale-only tests never hit it) → wrong wall/doorway detection for typical PNGs. |
| BUG-003 | **P2** | `app.js:209-236` | `onPath` animates a reference that `applyState` immediately replaces (server sends `path` *before* `state`, opposite to the code comment) → token **teleports**; `state.animating` is written but never read (no input gating). |
| BUG-005 | **P2** | `ws.py:394`, `session.py:309` | Per-client `reply` (e.g. `no route` error) is sent via `ws_serve` **without** the per-connection send-lock → can interleave with a concurrent `_broadcast` on the same socket → frame corruption under concurrency. |
| BUG-006 | **P2** | `app.js:621-632` | GM's **own** token is skipped from the awareness list (the `continue` assumes it was rendered by the player-only own-row block) → GM can't see/select its own token in the sidebar; summary count and list disagree by one. |
| BUG-007 | **P2** | `app.js:700-706` | `entityAtCell()` only searches `state.entities` (empty for players) and never `state.youEntity` → the "click own token re-asserts selection" branch is **dead**; clicking your own token issues a move instead. |
| BUG-008 | **P3** | `app.js:135,1097-1101` | A deliberate `ws.close()` in `openUploadedMap` still fires `onclose` → `scheduleReconnect()`, arming a stray reconnect → transient double socket / leaked handler thread. |
| BUG-009 | **P3** | `index.html:50` | File picker `accept=".png,.bmp,.jpg,.jpeg,.webp"` offers formats the stdlib decoder cannot read → guaranteed 400; label says "png / bmp". |
| BUG-010 | **P3** | `index.html:160`, `session.py:57` | "New entity" offers kind `player`, which the server always rejects (`kind must be one of npc/enemy/gm_character`). |
| BUG-011 | **P3** | `app.js:238,256` | Join rejections (e.g. `session full`) go to `toast()` in the still-`hidden` `#map-view` → the lobby user sees **no feedback**; the dedicated `#lobby-status` is declared but never written. |

**Retracted (checked, not a bug):** I initially suspected `join()` silently re-binds a same-named joiner to a different role. On re-reading `session.py:171` the re-attach guard is `player.name == name and (role is None or player.role == role)`, and the client always re-sends the authoritative `welcome` role — so no role-mismatched re-attach is reachable. No report filed.

---

## C. Integration / protocol checks that **passed**

- **WS message shapes match.** `welcome`/`state` carry `map, players, entities, you_entity, awareness, fog`; `you:{id,name,role,entity_id}`; awareness items carry `entity_id,x,y,color,label`. Every field the frontend reads (`msg.map.name/width/height/cells`, `msg.entities`, `msg.you_entity`, `msg.awareness`, `msg.fog`, `msg.you.{id,name,role,entity_id}`) is present in `session.py` `state_for`/`welcome_for`. No field is read-but-unsent or sent-but-misnamed. Client→server sends (`join/request_state/move/place/create_entity/delete_entity/set_team/paint/set_fog`) all match `handle_message`.
- **Awareness colors** are consistent: `awareness.py` emits `green/white/red` (explicit `entity.color` wins, GM sees true colors + labels, player excludes self); `app.js` maps `green→tri, white→circle, else→square` and matches the legend/CSS.
- **Movement rules** (server authoritative, verified): player can only move own entity + `override:false`; `override` GM-only; in-bounds checked; doorway walkable; no-route → exact `no route — wall in the way` (em-dash U+2014 matches the JS matcher byte-for-byte, so "Move anyway" triggers — brittle but currently correct).
- **Upload field names** match exactly (`name/image_b64/dark_is_wall/cols/rows`; response `id/name/width/height/cells/thumbnail`), and `data.thumbnail` is consumed by the client.
- **DOM IDs:** every element in the `els` map **and** every `$("#…")` in `app.js` exists in `index.html` (checked all ~45: `#btn-back`, `#btn-back-top`, `#btn-new-map`, `#fog-toggle`, `#conn-status`, `#conn-label`, `#paint-group`, `#override-toggle`, `#map-thumbnail`, `#preview-thumbnail`, `#lobby-status`, `#no-map`, etc.). No missing ID that would throw at load. (Only `#map-thumbnail` and `#legend` are declared but never populated — cosmetic, not a throw.)
- **JS load-time safety:** the `els` const resolves all selectors to real nodes (no null-deref at boot); top-level code (`setConn/syncLobbyButtons/showView("lobby")/connectWs`) assumes only the lobby state. No top-level code assumes a joined state. So the script **loads** cleanly — the fatal error is runtime (BUG-001) at first render, not load.
- **Concurrency/safety (server):** `GameSession` uses an `RLock` for state, per-connection send-locks for outbound, and computes snapshots under the lock but sends outside it (a slow socket can't block a mutation). `_send_json` is best-effort (never raises on a dead socket); `detach()` cleans up on close; `ws_serve` catches `WSClose`/`OSError`/`ValueError` and closes cleanly. The only concurrency gap is the **unlocked reply** in `ws_serve` (BUG-005).
- **Pathfinding / LOS / awareness / models:** A* octile heuristic is consistent, no corner-cut, deterministic (tie-break counter); `has_line_of_sight` Bresenham excludes endpoints (entities on their own cells don't block), correct; `awareness` GM-sees-all + explicit-color-wins + player-excludes-self all correct; `models` validation is sound.
- **Detection (single-channel path):** `classify_doors` (opposite-orthogonal wall heuristic), Otsu (bimodal plateau→mid, flat→127), `resize_nearest` (half-center mapping), and `median3x3` (center-wins-tie) are correct **for the paths the tests exercise**. The one real defect in this area is the multi-channel PNG filter bug (BUG-004).

---

## D. What the green unit tests miss (why these slipped through)

1. **No browser.** None of the 168 tests loads `index.html`/`app.js`. BUG-001 (undefined `allEntities`), BUG-002 (session switch), BUG-003 (animation), BUG-006, BUG-007, BUG-008, BUG-011 are all **frontend-only** and were never executed.
2. **PNG fixture only uses filter 0.** `encode_png` writes `filter byte 0` on every scanline, so the Sub/Up/Average/Paeth reconstruction branches in `_decode_png` are untested → BUG-004 invisible.
3. **WS tests use a stdlib client, not the JS client.** Message shapes are asserted against a Python client; the actual browser's `onmessage`/render path is untested.
4. **Concurrency is exercised but not interleaved.** Tests are mostly single-operation-per-client; the unlocked-reply race (BUG-005) needs two simultaneous operations on one socket to surface.

## D2. Latent / minor observations (not filed as bugs — not reachable or non-blocking)

- **`GET /api/maps/{id}` (latent 500).** `main.py:283-284` serializes `list(entry["entities"].values())` / `list(entry["players"].values())` directly into `json.dumps`. Those registry dicts hold **dataclass** objects and would raise `TypeError: Object of type Entity is not JSON serializable` → 500. **But** they are initialized `{}` at `main.py:69` and **never populated** anywhere (the live entities live in the `GameSession`, not the registry entry), so the list is always empty and the 500 is unreachable today. The `main.py:69` comment ("populated live by GameSession") is inaccurate. **Fix if/when it is used:** serialize with `e.to_dict()`/`p.to_dict()`, or remove the fields. (Verified non-reachable by searching `app/` for writes to `entry["entities"]`/`entry["players"]` — none exist.)
- **`state.players` received but unused.** `app.js:192` stores `state.players = msg.players || []` but no code renders a player roster. The wireframes' entity list is the *awareness* list (entities, not players), so this is acceptable, just dead state. (No fix required; note for the polish pass.)
- **`#map-thumbnail` / `#legend` declared in `els` but never populated/toggled.** Cosmetic; the map never sets the top-bar thumbnail and the legend is static. Non-blocking.
- **"Move anyway" matcher is brittle.** `app.js:241` exact-compares against `"no route — wall in the way"`. The em-dash (U+2014) matches `session.py:47` byte-for-byte today, so it works — but any wording change on either side silently disables the one-shot. Consider matching on a stable flag/field instead of the message text.

## E. Suggested follow-up test harness
- A headless check (e.g. `node`/jsdom or a Playwright step) that loads `index.html`, joins as GM and as a player, and asserts: the canvas renders (no uncaught `ReferenceError`), a player's own token is the only full token + ring, players see only dots for others, GM sees labeled tokens. This single harness would have caught BUG-001 immediately and re-opened the rest.
- A unit test that hand-crafts IDAT scanlines with filter types 1/3/4 for an RGB image and asserts decoded pixels (catches BUG-004).
- A WS test with two concurrent operations (a no-route reply to a socket while a broadcast hits the same socket) asserting no interleaved bytes (catches BUG-005).
