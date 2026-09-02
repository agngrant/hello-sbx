# Tactica — QA Pass 2: Regression Review & Final Sign-off

**Scope:** Verify the 11 fixes (BUG-001..011), hunt for regressions the fixes
introduced, and give a final verdict on all 8 §1 hard requirements.
**Method:** Code reading + data-flow trace (read-only; no browser/shell).
The suite reported **195 tests green** — I re-counted the test methods and the
count reconciles (16 api + 15 awareness + 16 detection + 16 frontend + 17 grid
+ 25 imaging + 32 pathfinding + 40 session + 18 ws = **195**). The 16
Node-based `test_frontend.py` tests are present and are NOT conditionally
skipped by a missing Node (they are included in the 195), so the frontend
harness (`tests/js/harness.js`) actually ran. I independently re-verified each
fix by reading the code it claims to prove.

**Verdicts:** every one of BUG-001..011 is **FIXED** with correct,
regression-tested evidence. I found **no new functional regressions** introduced
by the fixes. Two pre-existing, non-blocking notes are recorded (§D). Final:
**all 8 §1 hard requirements PASS → SHIP.**

---

## A. Per-bug verdicts

| ID | Area | Verdict | Evidence (file:line) |
|----|------|---------|----------------------|
| BUG-001 | `app.js` `allEntities` undefined | **FIXED** | `app.js:759` defines it; all 5 call sites resolve; welcome→render path traced clean |
| BUG-002 | "open map" stranded players | **FIXED** | `app.js:1156-1172` sends `use_map` on same socket; `session.py:575-623` `_on_use_map` GM-only, swaps shared grid, re-places, re-broadcasts |
| BUG-003 | path animation teleports / no gating | **FIXED** | `app.js:231-263` animates live copy; `app.js:208-211` pins to shown cell; `app.js:786` `isAnimating` gate |
| BUG-004 | PNG Average/Paeth multi-channel | **FIXED** | `app.js`→`imaging.py:190-211` per-byte loop, `bpp=channels*(2 if 16 else 1)`, correct left/up/up-left per channel |
| BUG-005 | reply sent w/o send-lock | **FIXED** | `ws.py:388-398` `_out()` locks every outbound frame; `main.py:448` passes same `_send_lock_for`; broadcast uses identical lock |
| BUG-006 | GM's own token missing from sidebar | **FIXED** | `app.js:685` `continue` only for a **player's** own item; GM falls through and renders the row |
| BUG-007 | `entityAtCell` misses player's token | **FIXED** | `app.js:766-770` uses `allEntities()` (includes `youEntity` for players) |
| BUG-008 | stray reconnect on intentional close | **FIXED** | `app.js:143-146` `intentionalClose` guard; `openUploadedMap` no longer closes/reconnects; `app.js:125` new `connectWs` clears pending timer |
| BUG-009 | file picker offers jpg/webp | **FIXED** | `index.html:49` `accept=".png,.bmp"` |
| BUG-010 | "New entity" offers `player` kind | **FIXED** | `index.html:165-169` offers only npc/enemy/gm_character (matches server `CREATABLE_KINDS`) |
| BUG-011 | join rejection invisible | **FIXED** | `app.js:286-289` routes to `#lobby-status` when `!state.joined`; `app.js:182` cleared on welcome |

### BUG-001 — `allEntities()` defined and correct for GM vs player
- `app.js:759-762`: `function allEntities() { if (state.role === "gm") return state.entities; return state.youEntity ? [...state.entities, state.youEntity] : state.entities; }`
- **GM** → returns `state.entities` (the full list; server sends the GM every entity, `state_for` `is_gm` branch, `session.py` state_for). Server sets `you_entity=None` for the GM, so `state.youEntity` is null — no double-list.
- **Player** → server sends `entities: []` + `you_entity` (own character), so returns `[...[], youEntity] = [youEntity]` — exactly the player's own token. Correct.
- **All call sites resolve** (was the P0 `ReferenceError`): `app.js:272` (`findEntity`), `app.js:508` (`drawEntitiesAndDots` draw path), `app.js:688` (GM sidebar lookup), `app.js:769` (`entityAtCell`), `app.js:1029` (keyboard handler). All defined-function calls.
- **First-render path traced clean:** `onWelcome` (`app.js:176`) → `applyState` (`:196`) → (`mapChanged`/`!mapView.hidden`) `layoutCanvas()` → `drawGridOnCanvas` → (canvas.id==="map-canvas") `drawEntitiesAndDots` → `allEntities()` now resolves. No throw for GM or player. Confirmed by `test_frontend.py:143-149` (`test_welcome_renders_for_gm/player`) driving the real `onWelcome`.

### BUG-002 — open uploaded map keeps everyone in one session
- **Client** `app.js:1156-1172` `openUploadedMap()`: does **not** change `wsSession`, does **not** `ws.close()`, does **not** `connectWs()`. It `wsSend({type:"use_map", map_id:m.id})` on the **same** socket (GM-only, `state.role === "gm"` guard). Players never reconnect → never stranded. Verified by `test_frontend.py:151-174` (asserts `use_map` + `wsUnchanged:true` + `newSockets:0`).
- **Server** `session.py:575-623` `_on_use_map`:
  - GM-only via `handle_message` → `_gm_only` (`session.py:363,571`).
  - Reads `maps_registry[map_id.strip()]` and **assigns `self.grid = grid`** — the **same** Grid object the registry holds (`main.py` `_register_map` stores the dataclass). So subsequent GM/REST `paint` and WS `paint` mutate the grid everyone sees (shared identity). Bounds for `paint` are re-checked against the **new** dims (`session.py:544`).
  - **Re-places** out-of-bounds/now-on-wall entities: for each entity, if not `(0<=x<grid.width, 0<=y<grid.height, cell in floor/doorway)` it calls `_find_free_floor()` and sets `e.x,e.y`. All entities/players/`_seen` kept — no one stranded.
  - `_broadcast()` re-sends per-viewer state to everyone already connected. A late joiner's `welcome` (`welcome_for`→`state_for`) reads the **swapped** grid.
  - **Bad/unknown `map_id`** → returns `{"type":"error","message":"unknown map: ..."}` (non-GM → `not allowed`; missing → `map_id required`). No crash; `grid` unchanged. Verified by `test_session.py:749-772` and `test_session.py:710-748` (grid identity `assertIs`, OOB player re-placed onto a free cell, late-joiner welcome picks up new grid).

### BUG-003 — path animation reconciles with final state + input gate
- `app.js:231-263` `onPath`: returns early if `reducedMotion` or `path.length<2`; calls `stopAnim(eid)` (restarts any in-flight); stores `state.animations[eid] = {path, i:1, timer}`. Each `step()` tick **re-looks-up the CURRENT entity** via `findEntity(eid)` (→ `allEntities()`), updates **that** object to `path[i]`, then `i+=1`. This mutates the live object, not a detached one.
- `app.js:208-211` in `applyState`: for each animating entity it **pins** it to `path[max(0, i-1)]` (the last cell actually shown). Because the server sends `path` **before** `state` (whose entities are fresh objects at the FINAL position), this pin means the snapshot's final position never yanks the token back — it stays on the in-flight cell and the animation walks it to the goal.
- `app.js:783-787` `sendMove`: `if (isAnimating(entityId)) return;` — **gates** further moves for an in-flight entity (wireframes §4.5).
- **Terminal correctness:** animation ends at `path[length-1]` == server final position → lands exactly on the goal. `isAnimating`/`stopAnim` are consistent: `stopAnim` deletes `state.animations[eid]` + clears timer; `isAnimating` reads the same map. No infinite gating (see §D regression hunt). Verified by `test_frontend.py:177-263` (token pinned to start after path+state, walks cell-by-cell, lands on goal, `animatingAfter:false`; gate drops a move mid-anim and sends after `stopAnim`).

### BUG-004 — PNG multi-channel Average/Paeth reconstruction
- `imaging.py:190-211`: the loop now runs **per byte** (`for b in range(stride)`), with `bpp = channels * (2 if bit_depth==16 else 1)` (`imaging.py:165` and re-asserted `:189`). References are at the **same byte offset `b`**:
  - `left  = cur[b - bpp] if b >= bpp else 0` — same channel, previous pixel ✓
  - `up    = prev_row[b] if prev_row is not None else 0` — same channel, previous row ✓
  - `upleft= prev_row[b - bpp] if (prev_row is not None and b >= bpp) else 0` — same channel, prev row prev pixel ✓
  - `cur[b] = (raw_b + f) & 0xFF` per filter.
- **RGB (ct=2, ch=3, 8-bit):** bpp=3 → green/blue use `prev_row[b]`/`prev_row[b-3]` = their own channel, not channel-0. Correct.
- **RGBA (ct=6, ch=4, 8-bit):** bpp=4 → all four channels index their own byte. Correct.
- **16-bit (ct=2/4/6, bit_depth=16):** bpp=channels*2 → each 16-bit sample's high/low bytes are reconstructed with the same-channel neighbors two bytes apart (low byte of prev pixel, up = same 16-bit byte above, up-left = same byte prev row). Symmetric and correct. After reconstruction the high byte is kept (`imaging.py:212-218`). (No 16-bit multi-channel *test* exists — see §D note; correctness is by inspection and the 8-bit multichannel tests are sensitive.)
- **Regression tests genuinely exercise Average/Paeth with non-trivial neighbors:**
  - `test_imaging.py:157-234` `TestMultiChannelPngFilters`: hand-builds RGB/RGBA PNGs (`_build_png`) using filters `[3,0,3,3]`/`[4,0,4,4]`/`[0,3,4]` with a per-channel gradient so R/G/B differ; asserts **exact** byte equality. `test_paeth_fixes_green_blue_not_red` uses a 2x2 RGB where green≠red specifically so the old `prev_row[x]` (channel-0) bug would corrupt green/blue but pass on red.
  - `test_detection.py:244-298` `TestMultiChannelDetection`: same Map-A pixels re-encoded as RGB with alternating Paeth/Average rows; asserts decoded gray == intended gray AND final grid == pinned `MAP_A_EXPECTED` with the doorway at (8,5). This is sensitive: dark→(8,4,0), light→(255,250,240), so the channel-offset bug would have corrupted gray and moved walls/doorway.

### BUG-005 — every outbound frame under the per-connection send lock
- `ws.py:388-398`: `ws_serve` now has an inner `_out(obj)` that does `lock = lock_for() if lock_for is not None else None; if lock is not None: with lock: send_json(...) else: send_json(...)`. **Both** outbound sites route through it: the reply (`ws.py:414`) **and** the invalid-JSON error (`ws.py:409`).
- `main.py:448`: `lock_for=lambda: session._send_lock_for(sock)` — the reply path and `_broadcast` use the **same** lock object for a given socket (`_send_locks[cid]`, `session.py:108-113`). So a reply and a broadcast to the same socket are truly serialized.
- **No unlocked per-connection send in the hot path.** Searched all `send_json`/`send_frame`/`send_text`: the only other unlocked writes are (a) the **close frame in `finally`** (`ws.py:421-424`) and (b) ping→pong/close-echo in `read_frame` (`ws.py:277-281,295-297`) — both **control frames during teardown**, which the brief explicitly permits. I agree they're acceptable (socket is being torn down / control-plane), not hot-path.
- **Deadlock check (see §D):** the send-lock is **never** nested under the session RLock, and the reply path is never taken while holding the session lock. No lock-order inversion → **no deadlock**. Verified by `test_ws.py:501-560` `test_reply_written_under_send_lock` (records `send_json` calls with a `RecordingLock` and asserts the `no route` **reply** frame was written while the lock was **held**).

### BUG-006 — GM's own character in the awareness list
- `app.js:680-685`: `const isOwn = state.you && state.you.entity_id ? item.entity_id === state.you.entity_id : false; if (isOwn && state.role === "player") continue;`. The `continue` now applies **only to a player's** own item (which the player-only "own row" block at `app.js:671` already rendered). For a **GM**, the own `gm_character` falls through and is rendered as a normal row (with name via `allEntities()` lookup + `kind·team` meta). The GM's own entity is thus listed, and the summary (`app.js:704`, counts all `state.awareness` items) now matches the list. Verified by `test_frontend.py:266-294` (`ids:["e1","e2"]`, `1 ally`, `1 neutral`).

### BUG-007 — `entityAtCell` finds the player's own token
- `app.js:766-770` `entityAtCell` now uses `allEntities()` (→ includes `youEntity` for players), so it returns the player's own token on its cell (was `[]`/null before). The "click own token re-asserts selection" branch (`app.js:846`) can now fire. Verified by `test_frontend.py:296-313` (`hit:"e2"`, `miss:null`).

### BUG-008 — intentional close no longer schedules a stray reconnect
- `app.js:143-146`: `ws.onclose = () => { if (intentionalClose) { intentionalClose = false; return; } if (state.joined) scheduleReconnect(); }` — a deliberate close is suppressed; an **unexpected** drop still reconnects.
- Root cause removed: the original stray-reconnect came from `openUploadedMap` doing `ws.close(); connectWs()`. `openUploadedMap` (`app.js:1156-1172`) **no longer closes or reconnects** (BUG-002 fix), so there is no longer an intentional `ws.close()` in the GM map-swap flow.
- Hardening: `connectWs()` (`app.js:125`) clears any pending `reconnectTimer` before opening a new socket → never two live sockets / never a stray armed timer.
- Verified by `test_frontend.py:316-366` (intentional close → `pending:0`,`sockets:0`; unexpected close → `pending:1`; a new `connectWs` supersedes a pending reconnect → `after:0`). **Note:** the `intentionalClose` flag is declared/read/commented but **never assigned `true`** anywhere (dead defensive code) — the behavioral fix actually comes from removing the intentional close + the timer-clearing. Not a functional defect (see §D).

### BUG-009 — upload picker only offers decodable formats
- `index.html:49`: `<input id="upload-file" type="file" accept=".png,.bmp" />`. Verified by `test_frontend.py:409-416` (asserts `accept=".png,.bmp"` and that `.jpg/.jpeg/.webp` are absent).

### BUG-010 — "New entity" no longer offers `player`
- `index.html:165-169`: `#new-entity-kind` options are exactly `npc`, `enemy`, `gm_character` (no `player`). This is consistent with the server, which **still** restricts creation to `CREATABLE_KINDS = ("npc","enemy","gm_character")` (`session.py:57`). UI is now honest and every offered option succeeds. Verified by `test_frontend.py:417-428`.

### BUG-011 — join rejection surfaced in the lobby
- `app.js:281-291` `onError`: `if (!state.joined) { els.lobbyStatus.textContent = m; return; }` — before a welcome the message goes to the visible `#lobby-status` (not the hidden `#toasts` in the hidden map view). `app.js:182` clears `lobbyStatus` on `welcome` so a stale error doesn't persist into the map view. Verified by `test_frontend.py:369-402` (`lobby:"session full"` before join; `lobby:""` after welcome).

---

## B. New regressions introduced by the fixes — NONE

I specifically hunted the failure modes called out in the brief plus a few more.
**No new functional regressions found.** Details of the negative results:

1. **Deadlock / send-lock nesting (BUG-005).** Every sender takes the per-connection
   send-lock **without** holding the session RLock: `_broadcast`/`_announce_join`
   compute targets+payloads under `with self._lock:` and then **release** it before
   `with lock:` per socket (`session.py:319-334, 337-351`); the `ws_serve` reply
   path (`_out`) runs **after** `handle_message` has returned (lock released), so
   it only ever takes the send-lock alone. The order is consistent everywhere
   (never send-lock-then-session-lock), so there is **no inversion and no
   deadlock**. The reply path is **never** called while holding the session RLock.
   The one residual is the `finally` close-frame sent without the send-lock
   (`ws.py:421-424`) — acceptable (teardown, socket being closed) and noted, not a
   defect. A teardown-only race remains where `detach()` can pop the lock and a
   stray reply could fall to the unlocked branch on a *dying* socket — connection
   is being torn down, no live-client corruption; noted, not a blocker.
2. **`allEntities()` double-list (BUG-001/007).** Impossible: for the GM
   `you_entity` is `null` (`state_for` sets it only for non-GM), so the GM returns
   `state.entities` with no appended `youEntity`; for a player `state.entities` is
   `[]`. An entity id can never be in both lists.
3. **`isAnimating` permanent gate (BUG-003).** The `step()` loop **self-terminates**
   (`stopAnim` at `path.length`) and `stopAnim`/a new `onPath` for the same id
   always clears it; `reducedMotion` skips `onPath` entirely. Even a deleted entity
   leaves only a self-terminating no-op tick (findEntity returns null). No entity
   can be left permanently gated.
4. **Per-byte PNG loop first row / first pixel (BUG-004).** `b < bpp` → `left=0`
   and `upleft=0` (both guarded); `prev_row is None` → `up=0` (guarded). No
   `IndexError` on row 0 or pixel 0 for any color type / bit depth. The 16-bit
   path is symmetric with `bpp=channels*2`.
5. **`use_map` re-placement on a fully-walled grid (BUG-002).** The loop is a
   single bounded pass over `self.entities` (no infinite loop). `_find_free_floor`
   falls back to `(1,1)` when no free floor exists (`session.py:230-238`). On a
   degenerate fully-walled (or 1×1) grid an entity is parked on a wall cell — but
   `find_path` guards `walkable(start)` and `_on_move` bounds-checks the
   destination, so the entity simply gets `no route` (no crash). This is the same
   pre-existing spawn-fallback already used by `join`; **not** a new regression
   (see §D).

---

## C. §1 hard-requirement final verdicts (through the fixed code)

| # | Requirement | Verdict | Rationale (evidence) |
|---|-------------|---------|----------------------|
| 1 | Exactly 1 GM + up to 6 players, one session | **PASS** | Server enforces 1 GM / ≤6 players (`session.py` join: 2nd GM & 7th non-GM → `session full`). BUG-002 fixed so a custom map no longer forks the session — `use_map` swaps the grid **in place** in the one live session; everyone stays connected. Join rejection now visible (BUG-011). |
| 2 | Grid cells `floor`/`wall`/`doorway` | **PASS** | `models.py` `Grid.__post_init__` validates every cell; `CELL_TYPES` enforced on paint/upload. |
| 3 | Walls block movement; no diagonal corner-cut | **PASS** | `pathfinding.py` `is_valid_step` forbids a diagonal unless **both** elbows walkable; `find_path` uses it; server authoritative (`session.py:_on_move`). `test_pathfinding` corner-cut cases green. |
| 4 | Doorways / gaps walkable | **PASS** | `WALKABLE_CELLS = {"floor","doorway"}` (`pathfinding.py`); doorways traversable by A*; `test_pathfinding` door-gap cases green. |
| 5 | GM override moves through walls (GM-only) | **PASS** | `session.py:_on_move` `override` → direct set, walls ignored; `override:true` from a non-GM → `not allowed`. Frontend: override only sent by GM (`els.overrideToggle` / "Move anyway" GM-gated, `app.js`). |
| 6 | Awareness per player (dots, GM sees all); colors green/white/red | **PASS** | `awareness.py` `build_awareness`: GM every entity (true color + name + kind, no masking); player every entity **except self** as color-only dots; explicit `color` wins. BUG-006 fixed so the GM's own token is in the sidebar and the count matches. |
| 7 | Player moves only their own character; GM any | **PASS** | `session.py:_on_move`: non-GM may only move `entity.owner == player.id` else `not allowed`; frontend always targets `you.entity_id`. BUG-007 fixed the re-select UX (does not weaken perms). |
| 8 | Upload image → auto-detect walls/doorways + paint fallback | **PASS** | `POST /api/maps/upload` → `detect_grid` (decode→gray→resize→Otsu→majority→classify→doorway→auto-invert). BUG-004 fixed so real multi-channel RGB/RGBA PNGs (Average/Paeth) decode correctly. Paint fallback via WS `paint` + REST, GM-only. BUG-002 fixed so the detected map is usable in the live session. |

**Soft/nice-to-have (non-blocking):** smooth animated movement — now works (BUG-003);
fog-of-war toggle — implemented, off by default, Bresenham LOS correct, GM never
fogged; session save/load to disk — **not** implemented (in-memory only, documented
v1 trade-off); no zoom/pan (documented).

---

## D. Notes (pre-existing / cosmetic — NOT blockers, NOT new regressions)

1. **`_find_free_floor` fallback can place on a wall / OOB cell** on a degenerate
   fully-walled or 1×1 grid (`session.py:230-238`). Pre-existing (also used by
   `join` for spawns); guarded downstream (`find_path` start-walkable check,
   `_on_move` bounds check) so it cannot crash — the entity just can't move until
   the GM paints a floor. Worth a polish follow-up (e.g. prefer the least-bad
   cell, or block the use_map with an error if no free floor exists).
2. **`intentionalClose` flag is dead code** (`app.js:110`): declared + read in
   `onclose` (`:144`) but never assigned `true`, because `openUploadedMap` no
   longer performs an intentional `ws.close()`. The behavioral fix is real
   (no intentional close + `connectWs` timer-clearing), so no functional impact —
   but the flag/branch is inert and could be removed or a test could exercise it
   to keep it meaningful.
3. **(Reaffirmed from pass 1, unchanged)** `GET /api/maps/{id}` serializes the
   registry's always-empty `entities`/`players` dicts; the comment claiming they
   are "populated live by GameSession" is inaccurate — they are never populated.
   Unreachable 500 (empty lists). Cosmetic/latent only.

---

## E. Final recommendation

**SHIP.** ✅

**Rationale:** all 11 reported bugs are correctly fixed and each is backed by a
targeted regression test (195 tests green, count reconciled); the regression hunt
found no new functional defects — the two residual items are pre-existing,
guarded, and cosmetic. All 8 §1 hard requirements pass end-to-end through the
fixed code, including the two formerly-breaking flagship flows (real multi-channel
PNG upload→detect→use, and 1 GM + players staying in one live session while the
GM swaps the map).
