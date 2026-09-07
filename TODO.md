# LittleDungeons — Team TODO

_Kept current by the orchestrator. Branch: **`main`** @ `753dfcb` (feat/safe-room-doors fast-forward-merged + pushed this session; QA re-run on main PASS)._ 

## IN PROGRESS — Pan & Zoom (map viewport navigation)

**Owner spec:** maps bigger than the viewport; pan via clickable arrows (right-hand control
panel) + cursor keys; zoom via control-panel buttons + `+`/`-` keys; max zoom = 6×5 grid
squares visible, min zoom = 60×50. Shared plan: `pan-zoom` (rev 1+).

| Item | Status |
|---|---|
| Branch `feat/pan-zoom` from main | ✅ done @ `61e30ac6` (main had 2 docs-only commits past the 753dfcb baseline) |
| Design spec `docs/design/pan-zoom.md` | ✅ done — 22 ACs (AC1–AC22), `#nav-panel` in right sidebar, 3 control states |
| Frontend build (view state, transform, controls, tests) | ✅ done — 8 files, 170 frontend / 680 pytest green. Flags for QA: **A2 arrow keys now pan (token nudge retired)**; `cellFromEvent` in-bounds = visible window (per ACs); spec AC1 example off-by-one corrected to pan(54,55) |
| QA verification + sign-off `docs/qa/qa-signoff-pan-zoom.md` | ✅ done — pytest **680 passed** / unittest **680 OK** / e2e **all ✓** / live smoke (ephemeral port 8771, released) **21/21 ✓** / `TestPanZoom` **31 OK**; AC1–AC22 all PASS; **0 bugs** (BUG-012/013 are retracted placeholders); **VERDICT PASS** |
| Commit on `feat/pan-zoom` | ✅ `7233e83` (12 files, +2164/−106) + fix `51200a6` (zoom buttons/keys were inverted — `−` now zooms OUT=see more, `+` zooms IN=more detail; regression test added). **Not merged, not pushed; awaiting owner decision** |

## Branch baseline (verified this session)

| Item | Value |
|---|---|
| Branch / HEAD | **`feat/pan-zoom`** @ `7233e83` (pan/zoom feature commit on top of `main` @ `61e30ac6`). `main` @ `61e30ac6` — 2 docs-only commits ahead of `753dfcb`; local + remote in sync |
| Tag | `safe-doors-v1` @ `7190b3f` (pre-door-iconography baseline; local + remote) |
| Branches deleted this session | `feat/safe-room-doors` (merged @ `753dfcb`), `feat/explored-map` (merged @ `d4a1cd5`) |
| Uncommitted (local only) | `TODO.md`, `docker-agent.yaml` (team config, deliberately uncommitted), `docs/websearch-investigation.md` (untracked) |

**Features on `main`:** core v3.0, awareness ring + per-player radius, GM-generated BSP maps,
explored map (S/E/H fog with memory), Openable/Closable Doors, **GM Safe-Room Doors with lock state
(L/U/O)**, and the **pictorial door iconography** (6 states) — all QA-verified on main.

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

- [ ] Server **RUNNING** on `feat/pan-zoom` @ `51200a6` (pan/zoom + zoom fix build): PID **64752**, bound **0.0.0.0:8000**, `/health` ok, log `/tmp/little-dungeons-server.log` (restarted after zoom fix). Stop with: `kill 64752`. Restart later with:
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
- [ ] `docs/design/explored-map.md` §3.2 W4 literal erratum (superseded by
      corrected test fixture — spec-only fix)
- [ ] BUG-DOORS-001 structural option: per-session grid copy for unregistered
      session ids (only if cross-session door isolation is ever needed)
- [ ] Optional: room-density / loop-probability params for generated maps
