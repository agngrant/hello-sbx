# LittleDungeons — Team TODO

_Kept current by the orchestrator. Branch: `feat/safe-room-doors`._

## Branch baseline (verified this session)

| Item | Value |
|---|---|
| Branch / HEAD | `feat/safe-room-doors` @ `7190b3f` (safe-room feature `fa0b4d6` + config/TODO commit), **pushed** to `origin/feat/safe-room-doors` |
| `main` | `d4a1cd5` — fast-forward-merged from `feat/explored-map` (local; **`origin/main` still `5ad236f` — push is owner's call**) |
| `feat/explored-map` | `d4a1cd5` (now equal to `main`) |
| `feat/safe-room-doors` | `7190b3f`, pushed, tracking `origin/feat/safe-room-doors` (**NOT merged to main** — more changes planned next session) |

**Features enabled on this branch:** core v3.0, awareness ring + per-player radius,
GM-generated BSP maps, explored map (S/E/H fog with memory), Openable/Closable
Doors, and now **GM Safe-Room Doors** (shipped this session, QA PASS).

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

## Door Iconography Redesign + Safe-Door Lock State — **QA PASS ✅ (uncommitted)**

**Why:** owner says current door iconography is obscure. Shared plan: `door-iconography` (status: done).

| Item | Value |
|---|---|
| Branch / HEAD | `feat/safe-room-doors` @ `7190b3f` (changes **uncommitted** in working tree) |
| Tag baseline | **`safe-doors-v1`** @ `7190b3f` (pushed this session; spaces are invalid in git refs) |
| Spec | `docs/design/door-iconography.md` (AC1–AC17, E1–E14) |
| Sign-off | `docs/qa/qa-signoff-door-iconography.md` → **PASS (17/17 AC, 0 bugs)** |

New owner spec (6 states, all render at S+E tiers): normal = brown wood door (+ padlock TR when locked) /
open w/ soft-yellow light; safe = green wood door (+ padlock TR when locked) / open w/ soft-green light.
**Flagged decision A1 (needs owner sign-off):** safe doors move from "always unlocked (C/O)" to a full
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

- [ ] Server **RUNNING** on **0.0.0.0:8000** (PID **101412**, **fixed code** with join-bug fix +
      door-iconography feature); log `/tmp/little-dungeons-server.log`; health ✓, UI 200 ✓,
      served `app.js` byte-identical to working tree (SHA-256 verified) and **no load-time
      `renderLegendDoorSwatches()` call** (join fix confirmed live). Live join smoke: GM +
      player both joined over real WS ✓. Stop with `kill 101412` (or `lsof -ti:8000 | xargs kill`).

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
- **Commit status:** this fix is in the working tree, **not committed** (same as the
  rest of the door-iconography work).
- [ ] **Git not requested:** all feature changes are **uncommitted** in the working tree on
      `feat/safe-room-doors`. `safe-doors-v1` tag is the last pushed commit (`7190b3f`). Commit/push/merge
      (→ `main`) is the owner's call — see backlog.

## Open items (backlog)

- [ ] **Owner sign-off on A1** (safe doors gain a lock state — the behavioral change in this feature);
      and a decision on **commit/push** the door-iconography work + whether to merge `feat/safe-room-doors` → `main`
      (merge is still explicitly deferred per earlier owner note; `origin/main` still `5ad236f`).
- [ ] Housekeeping: leftover broken loose tag `.git/refs/tags/working sight` (spaces → invalid
      ref; git warns on tag ops; points at `b1ff47e4`). Clean up: `rm '.git/refs/tags/working sight'`.
- [ ] **Merge `feat/safe-room-doors` → `main` is explicitly deferred** — owner said not yet; `main` and `origin/main` intentionally untouched this session.
- [ ] `origin/main` still at `5ad236f` — decide when (if) to push `main` (`d4a1cd5`) to remote.
- [ ] What to do with `feat/explored-map` (fully merged into `main`, could be deleted).
- [ ] `docs/design/explored-map.md` §3.2 W4 literal erratum (superseded by
      corrected test fixture — spec-only fix)
- [ ] BUG-DOORS-001 structural option: per-session grid copy for unregistered
      session ids (only if cross-session door isolation is ever needed)
- [ ] Optional: room-density / loop-probability params for generated maps
