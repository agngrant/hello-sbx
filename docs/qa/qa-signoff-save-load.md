# QA Sign-off — Save / Load Map State

**Feature:** GM Save/Load Map State + rejoin-by-name ownership rebind
**Branch:** `feat/save-load` (uncommitted working tree — verified as-is, **not committed**)
**Spec:** `docs/design/save-load.md` (AC1–AC18 in §10, edge cases E1–E12, assumptions A1–A16)
**QA mode:** independent re-run of every suite + an **original** 3-server live
restart smoke I wrote from scratch (`scripts/qa_save_load_smoke.py`, real
`app.main`→uvicorn subprocesses on **ephemeral ports**, genuine process
kills + restarts, real WS wire + REST) + a Node-harness probe for the
unreachable-in-browser toast + line-by-line diff review.

**QA date:** 2025-09-07
**QA verdict:** **PASS** — 18/18 AC; 0 P1 bugs; the live restart headline
scenario (save → kill → restart → load → rejoin-by-name) works end-to-end.
Two non-blocking findings (BUG-014 P2, BUG-015 P3), both pre-cleared as
acceptable (frozen-wire / spec-compliant). No regressions.

---

## 1. Scope & independence

I re-ran **all** suites myself and wrote my own live-restart probe that does
**not** reuse the engineers' `e2e_proof.py` scenario code. The live smoke
drives three separate server subprocesses through the full lifecycle and
asserts the wire/REST/disk behaviour independently:

- **RUN 1** — GM builds a map with a normal door OPEN (5,5), a safe door
  (10,4)=U, two player tokens (Alice (2,1), Bob (3,2)) and a GM NPC
  (Goblin (6,2)); saves as "Checkpoint 1"; verifies the on-disk bundle
  (grid + doors + safe + per-entity `owner_name`); corrupt-file handling;
  same-name-conflict (distinct id); delete + delete-again 404; AC17
  (load-without-`use_map` does not mutate the live session).
- **RUN 2** — **kill the server (port confirmed free), start a NEW process.**
  Confirms the save persisted (disk-backed list), loads it → fresh `smap-`
  id, `use_map` on the same socket → full state restored (grid + doors + safe
  + entities GM-controlled), **rejoin-by-name** (Alice → saved pos + owner
  rebound to her new id, Bob → saved pos, Carol → fresh token, same-name
  re-attach), open-door walkable through (5,5), AC3 round-trip, AC16
  (owner_name NOT on the wire).
- **RUN 3** — fresh GM-only server: orphans stay GM-controlled, GM can
  move/delete/create; AC14/E10 BUG-002 guard (a connected player is **not**
  stranded when the GM `use_map`s a loaded save); owner_name not on the wire.

74/74 live checks passed. Server logs preserved on crash; ports freed; no
server left running; no `saves/` dir or logs left behind.

## 2. Test-suite results (all re-run by me)

| Suite | Command | Result |
|---|---|---|
| pytest (full) | `.venv/bin/python -m pytest` | **763 passed** (+153 subtests), 0 failed, 22.08 s |
| unittest discover | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 763 tests — OK** |
| Frontend harness | `.venv/bin/python -m unittest tests.test_frontend` | **Ran 204 tests — OK** (incl. **33 saves** tests) |
| e2e proof | `.venv/bin/python scripts/e2e_proof.py` | **ALL E2E CHECKS PASSED** (all ✓, 11 steps) |
| **Live restart smoke (QA, mine)** | `.venv/bin/python scripts/qa_save_load_smoke.py` | **74/74 checks passed** (3 server runs, real kills+restarts) |

The backend added `tests/test_saves.py` (33 tests: bundle I/O + session
rebind) and `TestSaves` in `tests/test_api.py` (28 tests: the 4 REST routes).
The frontend added 33 saves tests in `tests/test_frontend.py` (static-HTML,
GM-gating, list rendering, save/load/delete/tab-flow, save-map-state) plus
harness support (`buildApi` exports the saves module; a real `document.body`
classList for the `is-gm` gate; a `fetch` responses-queue for load→refresh).

## 3. Diff inspection (frozen-contract compliance)

- **No WS wire-protocol change.** `Entity.to_dict()` is **unchanged** — it
  emits only `id/name/kind/team/x/y/owner/color`, **no `owner_name`**
  (verified in `app/models.py`; the new `Entity.owner_name` field is
  excluded from `to_dict`, so the wire stays frozen per A5/§8). The GM
  `entities` wire items and `you_entity` go through `to_dict()`
  (`app/session.py:416-417`), so `owner_name` never leaks — confirmed live
  (AC16 checks in RUN 2/RUN 3 assert `"owner_name" not in` every GM entity).
- **Only additive REST routes** in `app/server.py`: `GET /api/saves`,
  `POST /api/saves`, `POST /api/saves/{id}/load`, `DELETE /api/saves/{id}`.
  Error shape follows the existing `{"error": msg}` convention. No new WS
  message types.
- **`app/session.py`** — only the `join` rebind (name-matching unclaimed
  entity, first-join-wins, tag cleared) + `use_map` roster rebuild from a
  registry entry's `loaded_entities` (deliberately a dedicated key so the
  `GET /api/maps/{id}` `entities` wire shape stays frozen).
- **`app/models.py`** — only the additive optional `Entity.owner_name` field
  (default `None`, not serialized).
- **Frontend** — `index.html` (Saves sidebar panel + Saved maps tab + Save
  map state button, all `.gm-only`), `app.js` (saves module + rejoin toast +
  rejoin note), `style.css` (GM gating via `body.is-gm`). Static only.
- **`saves/`** added to `.gitignore` (data, not code — A6). Confirmed
  `SAVES_DIR = <repo_root>/saves`.
- **`app/main.py`, `app/ws.py`, `app/detection.py`, `app/generation.py`,
  `app/imaging.py`, `app/pathfinding.py` — byte-identical** (no diff).
- `README.md`/`TODO.md` — docs only (save/load section + the "no save/load"
  limitation rewritten to reflect the feature).

## 4. Per-AC audit (AC1–AC18)

| AC | Verdict | Evidence |
|---|---|---|
| **AC1** save to disk | **PASS** | Live RUN 1: `POST /api/saves {"name":"Checkpoint 1"}` → 200 full record; `saves/<id>.json` on disk; bundle grid == live cells; Alice/Bob `owner_name` set, Goblin null; `doors(5,5=O)` + `safe(10,4=U)` present; `GET /api/saves` lists it. `test_api.py::TestSaves::test_gm_save_writes_bundle_to_disk`, `test_saves.py::test_save_bundle_writes_file_with_record_and_state`. |
| **AC2** load restores full state | **PASS** | Live RUN 2 (post-restart): load → `GET /api/maps/<id>` shows exact cells, `doors(5,5=O)`, `safe(10,4=U)`; `use_map` restores grid+doors+safe+entities; **Alice walks THROUGH the open door (5,5) to (6,5)** (a closed door blocks per the frozen door rules). `test_saves.py::test_load_bundle_with_doors_and_safe_round_trip`, `test_api.py::test_load_registers_fresh_independent_map`. |
| **AC3** save→load→save round trip | **PASS** | Live RUN 2: saving the loaded session → new distinct id; grid + entity positions/kind/team/color round-trip byte-exact; re-saved GM-controlled tokens stored `owner_name=null` (spec §4.3 — see BUG-015 note). `test_saves.py::test_load_bundle_round_trip`, `test_same_name_save_is_distinct_save`. |
| **AC4** ownership rebind by name | **PASS** | Live RUN 2: after restart, "Alice" joins → `you.entity_id` = her saved entity **at saved pos (2,1)**; GM snapshot shows `owner` = Alice's NEW player id; team/kind (party/player) preserved; "YOU" data present. "Bob" likewise. `test_saves.py::test_matching_name_rebinds_at_saved_position`, `test_rebind_survives_snapshot_and_welcome`, `test_use_map_registers_loaded_entities_and_rebinds_on_join`. |
| **AC5** orphan handling (nothing lost) | **PASS** | Live RUN 3 (GM only): Alice/Bob/Goblin all `owner:null` GM-controlled; GM **moves** orphan Bob (3,1), **deletes** it, **creates** a new entity — no stuck state. Unclaimed entities keep their data. `test_saves.py::test_orphan_stays_gm_controlled`. |
| **AC6** restart persistence | **PASS** | Live RUN 1→2→3: **server killed (port confirmed free), new process started**; `GET /api/saves` still lists "Checkpoint 1"; `saves/<id>.json` still on disk; load+`use_map` works; rebind succeeds with the fresh ephemeral player ids. Headline E8 scenario verified. `test_saves.py::test_file_survives_and_is_loadable_without_memory`. |
| **AC7** duplicate owner_name: first-join wins | **PASS** | `test_saves.py::test_duplicate_owner_name_first_join_wins` (first match bound, second stays `owner:null` with tag intact; no double-bind). Not exercised live (single-entity-per-name in the smoke), but unit-covered per spec E5/E7. |
| **AC8** non-matching join → fresh token | **PASS** | Live RUN 2: "Carol" (not in save) → fresh `kind:player team:party` token, `you.entity_id` set, saved tokens untouched. `test_saves.py::test_no_match_gets_fresh_token_exactly_as_today`. |
| **AC9** name edge cases | **PASS** | Live RUN 2: same-name **live re-join re-attaches** (still one "Alice" player, no double-bind). Empty-name `name required` + case-sensitivity unit-covered (`test_name_matching_is_case_sensitive`, `test_reattach_beats_rebind`). Reattach-before-rebind invariant holds. |
| **AC10** corrupt / missing save | **PASS** | Live RUN 1: `saves/bad.json` (malformed) → `GET` lists `corrupt:true`; `POST load` → **404 `save not found`**; server still healthy; DELETE works. Missing id → 404. `test_api.py::test_load_missing_404`/`test_load_corrupt_404_and_healthy`, `test_saves.py::test_corrupt_json_raises_and_file_untouched`/`test_truncated_bundle_height_mismatch_raises`. |
| **AC11** GM-only perms | **PASS** | Unit: `test_api.py::TestSaves` — player-only session → 401 for save/load/delete; GM-only session → all succeed; no session → save 409, load/delete allowed; `GET /api/saves` allowed in all cases. (The in-run "player vs GM" REST split is session-role-based per A8 with no per-request identity — the established no-auth model — so it is exercised via the unit suite's direct session control; the live smoke exercises the GM-path and the no-join `GET`.) |
| **AC12** doors + safe + mixed entities survive | **PASS** | Live RUN 2: one normal door O, one safe U, entities {Alice-player, Bob-player, Goblin-enemy} — all restored byte-for-byte (positions, kind, team, color); open door walkable; hostile-on-safe-door guard is the frozen door rule (e2e proof step 11, all ✓). `test_saves.py::test_load_bundle_with_doors_and_safe_round_trip`. |
| **AC13** load menu present + role-gated | **PASS** | 33 frontend saves tests: `#saves-panel` is `<section>…gm-only`, **slotted between `#entity-tools` and `#awareness`**; `#save-name` + Save button; Saved maps is the **3rd** source tab (`[upload,generate,saves]`), `gm-only`; rows render name / map name / `W×H` / token count / date + Load + Delete; empty-state copy; CSS keys the gate on `body.is-gm` (player welcome → not `is-gm`). Static: verified in `index.html`/`style.css`. |
| **AC14** post-load reuses `use_map` | **PASS** | Live RUN 2/RUN 3: `use_map` on the GM's **same socket** swaps the session to the loaded map; **connected Dave is NOT stranded** (sees the new map, keeps his token — BUG-002 guard). Frontend: `test_load_fires_post_then_use_map`, `test_tab_open_in_session_sends_use_map_same_socket` (exactly one `use_map`, no session switch); persistent **GM rejoin note** present (`test_load_shows_rejoin_note`). |
| **AC15** save-name conflict (E2) | **PASS** | Live RUN 1: two saves labelled "Checkpoint 1" → **distinct ids**, both loadable to distinct registry maps; delete removes the file. `test_api.py::test_save_as_new_same_name_is_distinct`/`test_save_overwrite_by_explicit_id`, `test_saves.py::test_same_name_save_is_distinct_save`. |
| **AC16** no wire change | **PASS** | `Entity.to_dict()` unchanged (no `owner_name` key) — verified in code + live (RUN 2/RUN 3 assert `owner_name` **absent** from every GM wire entity). Full pre-existing suite green (pytest/unittest 763) + **e2e_proof.py all-✓**. `test_models.py` shape assertions pass. |
| **AC17** independent-of-session | **PASS** | Live RUN 1: `load` (no `use_map`) does **not** mutate the live session (grid intact, Goblin present). Each load mints a fresh `smap-` id (distinct, verified RUN 1/2/3). `test_api.py::test_load_registers_fresh_independent_map`, `test_saves.py` session-isolation tests. |
| **AC18** delete | **PASS** | Live RUN 1: `DELETE` → 200, file gone from disk, list omits it, delete-again → **404**; deleting a loaded save does not affect the in-memory map (independent copy — AC17). `test_api.py::test_delete_removes_save`/`test_delete_missing_404`, `test_saves.py::test_delete_removes_only_that_file`. |

**AC18 (delete-a-loaded-save):** confirmed by the AC17 independent-copy
property (RUN 1 loads two saves to distinct `smap-` ids, then deletes one;
the live session — which plays a *different* map — is unaffected).

## 5. Live-restart smoke outcome (the whole point)

**save → process kill (port free) → restart → save persists on disk + in
list → load → `use_map` (same socket) → map opens with grid + doors + safe +
all entities GM-controlled → rejoin-by-name (Alice/Bob reclaim at saved
position with new ephemeral ids; Carol fresh; same-name re-attaches) →
orphan (Bob absent) stays GM-controlled and GM-manageable → corrupt save
handled (corrupt:true + 404, no crash) → perms (GM-only + no-join GET) →
round-trip → no server left running, ports freed, tree clean. 74/74 checks,
3 server runs.**

## 6. UI / static checks (task step 4)

- GM **"Saves" panel** in the map-view right sidebar, **`.gm-only`**, between
  GM Tools (`#entity-tools`) and Awareness — verified in `index.html` +
  `style.css` (block-level, `body.is-gm`-gated) + the 33 frontend tests.
- **"Saved maps" tab** is the 3rd source tab in the New map view, `.gm-only`.
- **List rows** show name / map name / `W×H` / token count / date + Load +
  Delete (corrupt rows → ⚠ + Delete only). **Empty state** present. **GM
  rejoin note** present (`#saves-rejoin-note` / `#saves-tab-rejoin-note`).
- **Preview "Save map state"** button in the preview action row, disabled
  until a map is open in a live GM session (E9/AC13 tested).

## 7. Frontend notes reviewed (frozen-contract decisions — acceptable)

- **Lobby "Load preview" thumbnail:** `GET /api/maps/{id}` has no thumbnail
  field; the preview clears the server thumbnail and hides the source pane
  (image not persisted — A7). **Acceptable** (documented). The grid IS
  drawn on `#preview-canvas`.
- **Per-entity "saved as <name>" badges + 4-name list cap: NOT implemented**
  (would require the frozen wire to carry `owner_name`). The required
  **GM rejoin note** (persistent) + rejoin toast plumbing **are** present.
  **Acceptable** — the owner's core requirement (rejoin-by-name to reclaim
  the character) is **not** undermined (verified live, AC4).

## 8. Bugs found

| Bug | Severity | Component | Status / impact |
|---|---|---|---|
| **BUG-014** — player "character has been restored" rejoin toast is dead code (the `!state.youEntity` guard is always false after `applyState` sets `you_entity`; `state.entities` is `[]` for players) | **P2** | Frontend (`app/static/app.js:241-279`) | **Non-blocking / pre-cleared.** The core R4 reclaim works and is verified live (rebind + YOU ring + saved position); only the spec'd *confirming toast* never fires. One-line fix proposed. |
| **BUG-015** — re-saving a freshly-loaded (pre-rejoin) map drops the rebind tags (stores GM-controlled tokens as `owner_name:null`) | **P3** | Backend (`app/server.py`, `app/saves.py`) | **Non-blocking / spec-compliant** (§4.3 + `test_owner_name_is_controlling_player_name`). Headline save-while-connected → restart → rejoin flow is unaffected. Recommend documenting (no code change) or a deliberate spec change. |

### Test-coverage gaps (not bugs; recommend adding)
1. **GM one-shot rejoin toast untested:** `announceLoadedSaveRejoin()`
   (`app/static/app.js:2975`, "N character(s) are waiting for players to
   join with matching names") is wired into both load paths (sidebar
   `loadSave` at `:2841`, lobby `openUploadedMap` at `:2593`) but **no test**
   asserts it fires (only the *persistent* rejoin note is tested). Add a
   harness test that stubs toasts and asserts the toast after `loadSave`.
2. **AC7 (duplicate owner_name) not exercised live** — unit-covered only.
   Optional: add a 2-entity-same-`owner_name` save to the live smoke.

## 9. Final verdict

**PASS.** 18/18 acceptance criteria met. Full suites green (pytest 763,
unittest 763, frontend harness 204, e2e_proof all-✓). The feature's headline
scenario — **save → real server restart → load → players rejoin by name to
reclaim their characters** — works end-to-end, verified by an independent
74-check live probe with genuine process kills/restarts. No P1 bugs. No
regressions. The frozen WS wire is intact (`owner_name` never serialized).

The two findings are non-blocking: **BUG-014 (P2, frontend)** — a dead
one-line toast (the core reclaim works and is independently verified);
**BUG-015 (P3, backend)** — a spec-compliant consequence of the deliberate
"null for GM-controlled at save time" rule. Both are documented with proposed
resolutions. **Recommend proceeding to commit + merge**, fixing BUG-014
(trivial) in the same or a follow-up commit, and adding the two coverage
tests above.

*Verification artifacts (kept, gitignored where applicable):
`scripts/qa_save_load_smoke.py` (the 74-check live restart probe),
`docs/qa/BUG-014.md`, `docs/qa/BUG-015.md`, this sign-off. No code was
committed; no server left running; `saves/` and run logs cleaned.*
