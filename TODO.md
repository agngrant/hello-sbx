# LittleDungeons — Team TODO

_Kept current by the orchestrator. Branch: **`feat/save-load`** @ `5b8e6de` (W4 erratum spec-only commit; save/load work + door-tap UX guard `c1ac353c` on top of `1bb8326`, unpushed, awaiting owner's go for push + merge). Previous mainline: `main` @ `4462f62` (feat/pan-zoom merged)._ 

## IN PROGRESS — Save / Load Map State (session persistence)

**Owner spec:** save the current map state to disk + reload a map with its existing state; GM load menu;
players rejoin with the **same names** to get their characters back (ownership rebound by name).
Shared plan: `save-load` (rev 1+).

| Item | Status |
|---|---|
| Branch `feat/save-load` from main @ `1bb8326` | ✅ done |
| Design spec `docs/design/save-load.md` | ✅ done — 18 ACs (AC1–AC18); GM menu = sidebar "Saves" panel + "Saved maps" lobby tab |
| Backend: saves.py + REST routes + name rebinding + tests | ✅ done — 4 additive REST routes, `owner_name` rebind, 730 green, e2e all-✓ |
| Frontend: GM save/load menu + tests | ✅ done — 2 GM surfaces, rejoin note, GM gating, 763 green |
| QA verification + sign-off `docs/qa/qa-signoff-save-load.md` | ✅ PASS — 18/18 AC, live restart smoke 74/74 (3 server runs), 0 P1; BUG-014 (P2 dead toast) fixed via additive `you.rebound` welcome flag + e2e re-verified all-✓; BUG-015 (P3) documented as spec-compliant. Final suites: **766 pytest / 766 unittest / 206 frontend / e2e all-✓** |
| Commit + merge cycle | ⏳ **pushed** — `feat/save-load` @ **`b49a373`** on `origin/feat/save-load` (save/load feature + door-tap guard `c1ac353c` + modal rework all committed); merge to main still pending owner's go |
| **Door-manipulation regression report** (owner: pan-zoom + save-load broke door UI) | ⚠️ **investigated (frontend_engineer) — no code defect found.** Door interaction chain, `cellFromEvent` pan/zoom math, save-load state re-application, and DOM/event layering are **byte-identical to QA-signed-off main @4462f62**; save-load app.js hunks all live in saves/lobby/rejoin code, none touch canvas events. Live harness probe under a real transform (L4 16×13, pan(2,3)): player tap, GM door tool, GM safe-door tool, paint-over-doorway all emit correct wire frames; letterbox tap correctly no-ops. Full suite green. **Leading hypotheses:** (a) stale deployment / browser cache (old app.js vs new server or vice versa), (b) taps landing in the letterbox / out-of-window region (spec-conformant no-op, easy to mistake for "doors do nothing"). **Resolved: owner confirmed doors work on the clean run** (server `feat/save-load` @ `bb3e4b0` on 0.0.0.0:8000) → stale client cache / environment, no code defect. Both approved mitigations **done + QA PASS**: (1) UX guard — `tapHint()` in app.js: debounced (2.5s) hint toast on letterbox/out-of-window taps ("Nothing there — outside the visible map") and GM door/safe-door tool misses on non-doorway cells ("Nothing to select here — not a doorway"); success paths and paint tools never toast; (2) 4 new `TestPanZoom` tests in tests/test_frontend.py driving the real `cellFromEvent` under a real view transform (player U→open / O→close / L→open-attempt, letterbox double-tap → zero frames + one debounced toast, GM tool non-doorway miss). **QA verdict PASS** (independent re-run: 770 pytest / 210 frontend+31 subtests / e2e 142✓ / live smoke 8791 with `tapHint` served; 0 bugs). Uncommitted — part of the pending `feat/save-load` commit cycle. |

## Map-Delete Confirmation → Full-Screen Modal (owner-reported UI bug) — **QA PASS, committed + pushed** ✅

**Owner report (this session):** the delete confirmation still appears "mixed over the load/delete box" — the
in-row confirmation bar from the rework below is not visually separated enough. New requirement: make the
delete confirmation a **modal dialog over the whole GM screen** (full-screen backdrop + centered dialog box),
then test.

| Item | Status |
|---|---|
| Design spec `docs/design/save-load-delete-modal.md` (designer) | ✅ done — AC1–AC14 + E1–E12 + A1–A12: body-level modal shell (small additive `index.html` change), fixed full-screen backdrop `--modal-z:100` above drawer/scrim/toasts, centered `role="alertdialog"` dialog (title, save name + meta line, Cancel + danger Delete), interaction lock (map/panel blocked, Escape superseded by modal), backdrop click = Cancel, focus trap + restore, in-flight dismissal = no-op until response lands (busy "Deleting…"), ghost-save guard, re-targeting isolation, in-row approach fully removed. **PM-approved designer defaults (owner silent):** backdrop click = Cancel (safe direction), in-flight Escape/Cancel/backdrop are no-ops until DELETE response | 
| Backend | N/A ✅ — spec is frontend-only; `DELETE /api/saves/<id>` already exists and is QA-verified from the save/load build (no backend changes expected; QA re-runs full suites anyway) |
| Frontend build (frontend_engineer) | ✅ done — `app/static/app.js` +201/−28 (derived `syncSaveModal()` render, `confirmDeleteSave`/`cancelSaveDelete` rewritten w/ role+membership+busy guards, interaction lock in document keydown, focus trap 2-button cycle + restore to row Delete btn, backdrop `ev.target` check, in-flight busy `Deleting…`), `app/static/index.html` +17 (body-level `#save-delete-modal` shell, `role="alertdialog"`/`aria-modal`/labelled, real `<button>`s), `app/static/style.css` +36 (`--modal-backdrop`, `--modal-z:100` above drawer 50/#scrim 40/toasts; old `.save-row.is-confirming`/`.save-row-confirm*` removed), `tests/js/harness.js` ±1 (`syncSaveModal` export), `tests/test_frontend.py` +753/−2 (`TestSavesDelete` rewritten: 14 old in-row tests → 23 mapped to AC1–AC14 + E6). In-row artifacts 0; `deleteSave(` = 2; `window.confirm` 0. Suites: **791 pytest / 791 unittest / 231 frontend, 0 failed**. Deviations: AC2 static title/buttons asserted in AC11 (harness doesn't parse index.html — no coverage lost); AC12d hooks `createElement` to spy focus restore |
| QA verification (qa) | ✅ **PASS** — independent re-run: **793 pytest / 793 unittest / 233 frontend** (after BUG-016 tests; all 0 failed); regression spot (PanZoom + all TestSaves*) 76 OK; live smoke `scripts/qa_modal_smoke.py` + 3 Node drivers **29/29** (ephemeral port, released): real GM session → real `app.js` modal Confirm → genuine HTTP DELETE 200, file gone, row removed both lists, toast `Deleted "QA Modal Smoke".`; 404/error paths return verbatim server message; pan/zoom/drawer regression OK; modal re-open key lock + Escape-closes-only verified. AC1–AC14 all PASS. **BUG-016 (P3, test-coverage only): CLOSED** — 2 new tests added (`test_e1_same_id_reentry_idempotent`, `test_e11_unknown_id_no_modal`, test-only diff, QA re-verified: 793/233 green). Sign-off: `docs/qa/qa-signoff-delete-modal.md` (incl. addendum) + `docs/qa/BUG-016.md` (closed) + `scripts/qa_modal_*` probes |

Uncommitted — joins the pending `feat/save-load` commit cycle (previous in-row rework also still uncommitted).

**Commit + push (owner go, this session):** commit **`b49a373`** `feat(ui): full-screen modal delete confirmation for GM saves (supersedes in-row bar)` — 14 files (+3302/−36), incl. app/static, tests, specs, QA sign-off/BUG-016, qa_modal_* probes, TODO.md. Pushed `bb3e4b0..b49a373 → origin/feat/save-load` (tracking set). Server for owner testing (PID 122028, 0.0.0.0:8000) stopped after testing; port 8000 free.

## Save-Row Delete-Confirmation Rework (owner-reported UI issue) — **QA PASS, uncommitted** ✅

**Owner report (this session):** in the GM Saves panel, the delete-confirmation text/controls
bled into the same area as the row's Load/Delete controls — confusing. Fix: redo the confirmation
so it is a clearly separate region. Spec: `docs/design/save-load-delete-confirm.md` (designer; AC1–AC8).

| Item | Status |
|---|---|
| Spec `docs/design/save-load-delete-confirm.md` | ✅ done — in-row **confirmation bar as derived state** (`state.confirmingSaveId`): separate danger-colored `.save-row-confirm` bar below the row head; `.save-row-actions` (Load/Delete) hidden while confirming; Confirm fires DELETE, Cancel/Escape restore via shared re-render; per-row isolation (one confirmation open at a time) |
| Frontend build (frontend_engineer) | ✅ done — `app/static/app.js` +111 (net +86): derived state, separate confirm bar (no more in-place mutation of `.save-row-actions`), `confirmDeleteSave` rewritten with membership check + `save not found` toast (old silent-delete-when-row-not-found fallback **removed**), new `cancelSaveDelete()`, Escape hook, guarded focus, real `<button>`s + `role="alert"`; `app/static/style.css` +19 (`.save-row.is-confirming` + `.save-row-confirm*`, existing tokens `--danger`/`--s2`/`--s3`/`--r-control`); `tests/js/harness.js` ±1 (`cancelSaveDelete` in EXPORTS); `index.html` unchanged. 12 new tests in `TestSavesDelete` (14 total) mapped to AC1–AC8 |
| QA verification (qa) | ✅ **PASS** — independent re-run: **782 pytest / 153 subtests / 0 failed**, frontend **222 passed**, `TestSavesDelete` **14/14**; live smoke (ephemeral 8799, freed): served app.js/style.css byte-identical to working tree with new markers present; AC1–AC8 all PASS incl. AC6 static proof (`deleteSave(` exactly 2× — definition + confirm-bar call site, no unconfirmed-delete path); reported deviation (`actions.hidden` set in both states) judged harmless/harness-required. 0 bugs. Sign-off: this table (short enough to skip a separate sign-off file; say the word if you want `docs/qa/qa-signoff-delete-confirm.md`) |

Uncommitted — joins the pending `feat/save-load` commit cycle.

## Completed — Pan & Zoom (map viewport navigation) — **shipped on `main`** ✅

**Owner spec:** maps bigger than the viewport; pan via clickable arrows (right-hand control
panel) + cursor keys; zoom via control-panel buttons + `+`/`-` keys; max zoom = 6×5 grid
squares visible, min zoom = 60×50. Shared plan: `pan-zoom` (rev 1+).

| Item | Status |
|---|---|
| Branch `feat/pan-zoom` from main | ✅ done @ `61e30ac6` (main had 2 docs-only commits past the 753dfcb baseline) |
| Design spec `docs/design/pan-zoom.md` | ✅ done — 22 ACs (AC1–AC22), `#nav-panel` in right sidebar, 3 control states |
| Frontend build (view state, transform, controls, tests) | ✅ done — 8 files, 170 frontend / 680 pytest green. Flags for QA: **A2 arrow keys now pan (token nudge retired)**; `cellFromEvent` in-bounds = visible window (per ACs); spec AC1 example off-by-one corrected to pan(54,55) |
| QA verification + sign-off `docs/qa/qa-signoff-pan-zoom.md` | ✅ done — pytest **680 passed** / unittest **680 OK** / e2e **all ✓** / live smoke (ephemeral port 8771, released) **21/21 ✓** / `TestPanZoom` **31 OK**; AC1–AC22 all PASS; **0 bugs** (BUG-012/013 are retracted placeholders); **VERDICT PASS** |
| Release | ✅ **`main` @ `4462f62`** (ff-merged `feat/pan-zoom`, pushed local + remote; full suite re-run on main: **681 pytest / 681 unittest / e2e all ✓**); feature branch deleted local + remote; tag `safe-doors-v1` intact |

## Branch baseline (verified this session)

| Item | Value |
|---|---|
| Branch / HEAD | **`main`** @ `4462f62` (local + `origin/main` in sync) — the only branch |
| Tag | `safe-doors-v1` @ `7190b3f` (pre-door-iconography baseline; local + remote) |
| Branches deleted this session | `feat/safe-room-doors` (merged @ `753dfcb`), `feat/explored-map` (merged @ `d4a1cd5`), **`feat/pan-zoom` (merged @ `4462f62`)** |
| Uncommitted (local only) | `TODO.md` (this doc), `docker-agent.yaml` (team config, deliberately uncommitted), `docs/websearch-investigation.md` (untracked) |

**Features on `main`:** core v3.0, awareness ring + per-player radius, GM-generated BSP maps,
explored map (S/E/H fog with memory), Openable/Closable Doors, **GM Safe-Room Doors with lock state
(L/U/O)**, the **pictorial door iconography** (6 states), and **Pan & Zoom** (discrete zoom 6×5→60×50,
right-hand Map view panel, keyboard pan/zoom, retired arrow-key nudge) — all QA-verified on main.

## Completed

- [x] **Git ops** (backend_engineer): `main` fast-forwarded `5ad236f → d4a1cd5`
      (feat/explored-map merged in); new branch `feat/safe-room-doors` cut from
      `main`. `docker-agent.yaml` (local team config) deliberately left
      uncommitted throughout.
- [x] **Safe-room doors spec** — `docs/design/safe-room-doors.md` (designer;
      AC1–AC16, edge cases E1–E14, assumptions A1–A10)
- [x] **Safe-room doors — backend build** (backend_engineer): `Grid.safe`
      ("x,y"→"C"/"O", additive, mutually exclusive with `doors`), team-aware
      pathfinding (hostile blocked by OPEN safe doors too; hostile override
      guard), `_on_safe_door` state machine (mark/unmark/open/close, GM-only),
      "not a normal door" guard on the frozen `_on_door`, additive `map.safe`
      on wire + REST, e2e_proof step 11, new `scripts/qa_safe_doors.py`
- [x] **Safe-room doors — frontend build** (frontend_engineer): green-cross
      icon (`#3ddc84`, closed = +bar, explored `#8fae9c`), GM 🛡 Safe door tool
      (Mark/Unmark/Open/Close), legend chip, player tap = no-op, `map.safe`
      handling, +32 harness tests
- [x] **Safe-room doors — QA** (qa): independent re-run pytest **617** /
      unittest **617** / e2e **11 steps all ✓** / `qa_safe_doors.py` **47/47 ✓**
      / frontend **131** / 2 original probes; AC1–AC16 all PASS; 0 bugs;
      sign-off `docs/qa/qa-signoff-safe-doors.md` → **PASS**
- [x] Contract docs: PROJECT.md §4/§5/§6/§8/§9 + README Safe room doors
      section (additive)

## Door Iconography Redesign + Safe-Door Lock State — **shipped on `main`** ✅ (committed `753dfcb`, merged, QA re-run PASS)

**Why:** owner says current door iconography is obscure. Shared plan: `door-iconography` (status: done).

| Item | Value |
|---|---|
| Branch / HEAD | **`main`** @ `753dfcb` (fast-forward-merged from `feat/safe-room-doors`, pushed; branch since deleted) |
| Tag baseline | **`safe-doors-v1`** @ `7190b3f` (pre-feature baseline, local + remote; spaces are invalid in git refs) |
| Spec | `docs/design/door-iconography.md` (AC1–AC17, E1–E14) |
| Sign-off | `docs/qa/qa-signoff-door-iconography.md` → **PASS (17/17 AC, 0 bugs)** |

New owner spec (6 states, all render at S+E tiers): normal = brown wood door (+ padlock TR when locked) /
open w/ soft-yellow light; safe = green wood door (+ padlock TR when locked) / open w/ soft-green light.
**Flagged decision A1 (SIGNED OFF AND ACCEPTED BY THE OWNER — see backlog):** safe doors move from "always unlocked (C/O)" to a full
**L/U/O** model (GM-only Lock/Unlock); legacy `"C"`→`"U"`, fresh `mark`→`"L"`. Hostile-restriction +
closed=wall / open=transparent behavior **unchanged**; `app/pathfinding.py`+`awareness.py`+`visibility.py`+`grid.py`
byte-identical (AC17).

- [x] **Tag baseline** (backend_engineer): `safe-doors-v1` @ `7190b3f`, pushed + verified on remote.
- [x] **Design** (designer): `docs/design/door-iconography.md` — palette (S+E), per-state canvas geometry
      (wood/padlock/light, legible at 8px→60px), legend + tool-UI, AC, change table.
- [x] **Backend** (backend_engineer): `app/models.py` + `app/session.py` only — safe-door L/U/O + `_on_safe_door`
      6 GM-only actions (mark/unmark/unlock/lock/open/close) + `from_dict` C→U migration + wire/REST L/U/O;
      updated model/session/api/ws tests + e2e_proof step 11 + qa_safe_doors.py.
- [x] **Frontend** (frontend_engineer): `app.js` `drawDoorCell` (6 states, S+E, single dispatcher) +
      `validateSafe` C→U + legend `renderLegendDoorSwatches` + index.html 6 swatches + 6 safe-tool buttons
      (added Lock/Unlock) + style.css tokens; harness + test_frontend updates.
- [x] **QA** (qa): **644 pytest / 644 unittest / 134 frontend / e2e all ✓ / qa_safe_doors 54 ✓** + 4 original
      probes (state 84, render 87, hostile 18, UI 12); PIL-discrepancy resolved (venv vs system python, not a
      real failure); all 17 ACs PASS. Zero code changes by QA.

### Carried over (still true this session)

- [x] Server **STOPPED** this session (SIGTERM to PID 19065; clean "Shutting down" in log; port 8000 free, no `app.main` processes) — stopped on owner's request after the delete-confirmation rework. Working branch: `feat/save-load` (uncommitted working tree incl. the delete-confirm rework, pending commit cycle). Restart with:
      `cd /Users/agrant3/agentteam && nohup .venv/bin/python -m app.main --host 0.0.0.0 --port 8000 > /tmp/little-dungeons-server.log 2>&1 &`

### Committed + pushed this session

- [x] **Commit `753dfcb`** on `feat/safe-room-doors` — `feat: door iconography redesign + safe-door lock state (L/U/O); fix P1 join-blocking TDZ` — 18 files (+3751/−1170): `app/models.py`, `app/session.py`, `app/static/{app.js,index.html,style.css}`, `docs/design/door-iconography.md` (new), `docs/qa/qa-signoff-door-iconography.md` (new), `scripts/{e2e_proof.py,qa_safe_doors.py}`, `tests/js/harness.js`, 7 test files, `TODO.md`. **`docker-agent.yaml` deliberately left uncommitted** (local team config) — the only remaining working-tree change (plus the `TODO.md` status line updated after the
push — a trivial doc tweak, not yet committed).
- [x] **Pushed** `7190b3f..753dfcb` → `origin/feat/safe-room-doors`; `git ls-remote` matches local HEAD. Tag `safe-doors-v1` untouched at `7190b3f`.
- [x] **Housekeeping:** removed the broken loose tag file `.git/refs/tags/working sight` (invalid ref, spaces; pointed at `b1ff47e4`); `git tag -l` now shows only `safe-doors-v1`; `git fsck` clean.

### P1 join-blocking bug (fixed + regression-guarded this session)

- **Bug:** user report — Join-as-GM / Join-as-Player buttons stay greyed; users can't join.
  Root cause (frontend): the new door-iconography change added a load-time call
  `renderLegendDoorSwatches();` that dereferenced `T.floor` **before** `const T = {...}`
  was declared. Every real browser threw `ReferenceError: Cannot access 'T' before
  initialization`, which aborted `app.js` before the join-button listeners were wired
  at the end of the file → buttons stayed `disabled` forever.
  The Node test harness stubbed `querySelectorAll() → []` so the swatch loop body never
  executed, and 134/134 of the old frontend tests stayed green while every real user was
  locked out at the lobby. (Classic stub-fidelity gap.)
- **Fix** (frontend_engineer): removed the load-time call (`app/static/app.js`); the
  legend is now drawn only from `showView("map")` — after join, when `T` is long since
  initialized. Added an explicit ORDERING-CONSTRAINT comment at the removed call site
  and above `const T` so a future edit can't reintroduce it.
- **Regression guard** (frontend_engineer): extended `tests/js/harness.js` so its stub
  DOM returns the **real** six `.door-swatch` chips parsed from `index.html`, making the
  loop body (and any TDZ read) actually execute under the harness. Added
  `tests/test_frontend.py::TestLobbyBootRegression` (5 tests: boot completes + socket
  opened; typing enables both Join buttons; Join clicks send `join` intents; blank name
  keeps both disabled; `showView("map")` still draws all six swatches). Verified: the
  OLD code FAILS all 5 with the exact real-browser error
  `ReferenceError: Cannot access 'T' before initialization` at
  `c2d.fillStyle = T.floor` in `renderLegendDoorSwatches`; the FIXED code passes all 5.
- **Verification:** full frontend `tests/test_frontend.py` → **139 passed / 31 subtests**
  (134 + 5 new). Full pytest → **649 passed / 140 subtests**, 0 failures. Live check
  (temporary server on an ephemeral port, started/stopped within the run): served
  `app.js` + real `index.html` booted with **no console error**, typing a name enabled
  both Join buttons, and a GM + a player actually joined a live session (role=gm /
  role=player with token; GM saw 2 players / 1 token / map + doors). Port 8000 free at
  end of the run. No door-art / state-model changes (bootstrap fix only).
- **Commit status:** committed in **`753dfcb`** and **merged into `main`** (see below); `main`
      (local + `origin/main`) @ `753dfcb`.
- [x] **Git — done:** feature committed (`753dfcb`), fast-forward-merged → `main`, pushed, branch
      `feat/safe-room-doors` + `feat/explored-map` both deleted. Tag `safe-doors-v1` retained.
      Only `TODO.md` + `docker-agent.yaml` remain uncommitted (local-only).

## Open items (backlog)

- [x] **Merged `feat/safe-room-doors` → `main`** (fast-forward `d4a1cd5` → `753dfcb`, pushed —
      `origin/main` now `753dfcb`). Checkout is on `main` @ `753dfcb`. **QA re-run on main: PASS**
      (649 pytest / 649 unittest / 139 frontend incl. TestLobbyBootRegression / e2e 11 steps / qa_safe_doors
      54/54 / live GM+player join smoke). Working tree: only `TODO.md` + `docker-agent.yaml` uncommitted (local).
- [x] **Deleted `feat/safe-room-doors`** (local + remote) after the merge — nothing lost (branch tip ==
      `main` @ `753dfcb`). Tag `safe-doors-v1` (@ `7190b3f`) survived, local + remote.
- [x] **`feat/explored-map` deleted** (local + remote, @ `d4a1cd5`) — confirmed fully merged into `main`
      before the safe `git branch -d`; nothing lost. Remote now has only `refs/heads/main`.
      Tag `safe-doors-v1` (@ `7190b3f`) intact local + remote. **Repo is now single-branch `main`.**
- [x] **Owner sign-off on A1 (safe doors gain a lock state)** — **SIGNED OFF AND
      ACCEPTED BY THE OWNER** (this session). The behavioral change is confirmed:
      GM must unlock a safe door before it can be walked through; fresh `mark`→`L`;
      legacy `"C"`→`"U"` on load. Recorded in `docs/design/door-iconography.md` §1.2/§11, and
      committed + pushed as `2b81059` on `main` (`docs: A1 owner sign-off...`).
- [x] `docs/design/explored-map.md` §3.2 W4 literal erratum — **fixed + committed**
      **`5b8e6de`** on `feat/save-load` (spec-only, 1 file 18+/12−): row y=6 cell
      (6,6) S→H, counts 69 S/123 H → 68 S/124 H, breakdown + qualitative-facts line
      + AC2 count updated, one-line erratum note; authoritative source
      `tests/test_visibility.py::TestWorkedExampleW4::test_spawn_mask_literal_exact`
      (W4_MASK) — doors closed+locked by default don't transmit sight.
- [ ] BUG-DOORS-001 structural option: per-session grid copy for unregistered
      session ids (only if cross-session door isolation is ever needed)
- [ ] Optional: room-density / loop-probability params for generated maps
