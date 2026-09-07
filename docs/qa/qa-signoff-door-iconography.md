# QA Sign-off — Door Iconography Redesign + Safe-Door Lock State

**Feature:** Pictorial wooden-door art (six states) + safe-room doors gain a
lock state (`L`/`U`/`O`, was `C`/`O`), fresh `mark`→`L`, legacy `C`→`U`,
GM Safe door tool gains **Lock**/**Unlock**.
**Branch:** `feat/safe-room-doors` (uncommitted working tree — verified as-is)
**Spec:** `docs/design/door-iconography.md` (AC1–AC17 in §10, edge cases
E1–E14, invariants I1′–I12, assumptions A1–A11)
**QA mode:** independent re-run of every suite (backend **and** frontend via
the Node harness) + three **original** probes I wrote from scratch that do not
reuse the engineers' test/scenario code:
  - `/tmp/qa_state_probe.py` (84 checks) — in-process state machine, exact
    §4.3 error strings, `from_dict` `C`→`U` migration, wire, pathfinding/LOS/
    restriction matrix.
  - `/tmp/qa_render_probe.js` (87 checks) — the **real** `app/static/app.js`
    `drawDoorCell` driven over all six states at **both S and E tiers** and
    **both s=8 and s=60**, asserting the literal §5.1/§5.2 hexes.
  - `/tmp/qa_hostile_probe.py` (18 checks) — hostile-on-safe guard
    (create/override/place/set_team) + occupancy guards, at the session level.
  - `/tmp/qa_ui_smoke.js` (12 checks) — boot the real `app.js` (both roles, no
    throw) + the genuine `renderLegendDoorSwatches`/`showView("map")` legend
    render (six 16×16 pixel-identical swatches).
Line-by-line diff review of every changed file. **No code changes were made**
(docs only: this sign-off).

**Sign-off date:** (current)
**QA verdict:** **PASS** (17/17 AC; 0 bugs found; 1 documented, non-blocking
AC17 test-file nuance below)

---

## 1. Scope & independence

The engineers reported conflicting "full suite" numbers (see §3 for the
reconciliation). I re-ran **all** of them myself, ran **four probes I wrote
from scratch** (none reuse the engineers' `e2e_proof.py` / `qa_safe_doors.py` /
`test_frontend.py` scenario logic), and reviewed every changed-file diff.

Files the spec pins as **byte-identical** — verified `git diff HEAD -- <f>` is
**empty** for all four (AC17): `app/pathfinding.py`, `app/awareness.py`,
`app/visibility.py`, `app/grid.py`. (md5 + `git diff` both empty.)

Backend python: **only** `app/models.py` + `app/session.py` changed (verified
`git diff --name-only -- 'app/*.py'`). `app/server.py`, `app/main.py`,
`app/ws.py`, `app/detection.py`, `app/generation.py`, `app/imaging.py`,
`app/grid.py`, `app/awareness.py`, `app/visibility.py`, `app/pathfinding.py`
are all **untouched**.

Modified (diffs reviewed line-by-line): `app/models.py`, `app/session.py`,
`app/static/{app.js,index.html,style.css}`, `scripts/e2e_proof.py`,
`scripts/qa_safe_doors.py`, `tests/{test_models,test_door_session,test_ws,
test_api,test_frontend}.py`, `tests/js/harness.js`, and (migration-only)
`tests/{test_pathfinding,test_visibility}.py` (see §5, AC17 note).

---

## 2. Test-suite results (all re-run by me this pass, via `.venv/bin/python`)

| Suite | Command | Result |
|---|---|---|
| Unit (pytest, authoritative) | `.venv/bin/python -m pytest` | **644 passed**, 0 failed, **140 subtests** passed, 18.4s |
| Unit (unittest) | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 644 tests … OK** (16.7s) |
| Frontend (Node harness) | `.venv/bin/python -m pytest tests/test_frontend.py` | **134 passed**, **31 subtests** (matches FE report) |
| E2E live (incl. step 11 safe-door lock) | `.venv/bin/python scripts/e2e_proof.py` | **ALL E2E CHECKS PASSED**; step **[11]** safe-room doors; exit 0 |
| Safe-doors live | `.venv/bin/python scripts/qa_safe_doors.py` | **ALL 54 CHECKS PASSED**; exit 0 |
| `/health` | fresh server on 0.0.0.0:8000 (new code) | `{"status":"ok"}`; `GET /` 200; `GET /app.js` 200 |
| **My independent state probe** | `/tmp/qa_state_probe.py` | **84/84 PASS** |
| **My independent render probe** | `/tmp/qa_render_probe.js` | **87/87 PASS** |
| **My independent hostile/occupancy probe** | `/tmp/qa_hostile_probe.py` | **18/18 PASS** |
| **My independent UI smoke probe** | `/tmp/qa_ui_smoke.js` | **12/12 PASS** |

**No PIL errors occur under the venv** (see §3). No linter is installed in the
venv (no `pyflakes`/`ruff`); all changed modules parse cleanly, and the full
green re-run above + `node --check app/static/app.js` (syntax OK) is the gate.

---

## 3. The PIL-discrepancy resolution (the reconciliation the tasking flagged)

Two engineers reported contradictory "full suite" numbers:
- **backend_engineer:** `.venv/bin/python -m pytest` → "641 passed, 0 failed."
- **frontend_engineer:** "full suite 549 run, 4 errors = pre-existing
  `ModuleNotFoundError: No module named 'PIL'` (test_api/detection/imaging/ws)."

A `PIL` import failure is impossible in a 641-passed run (those four modules
**require** Pillow). I resolved it:

1. **The venv has Pillow; the system python does not.**
   - `.venv/bin/python` (3.14.4) → `import PIL` → **Pillow 12.3.0** (installed,
     `pip list` shows `pillow 12.3.0`).
   - `/usr/bin/python3` (3.14.4) → `import PIL` →
     **`ModuleNotFoundError: No module named 'PIL'`**.
2. **Exactly four modules transitively need PIL — the same four the FE named.**
   I imported each under a PIL-blocked interpreter; `tests.test_api`,
   `tests.test_ws`, `tests.test_imaging`, `tests.test_detection` each fail at
   import with `No module named 'PIL'` (they reach `app.imaging` → `PIL`).
   Every other module imports fine. Their per-file counts: 26 + 32 + 25 + 16
   = **99 tests**.
3. **Conclusion:** the frontend engineer ran `pytest`/`python` with the **system
   interpreter (no venv)**, so the four PIL modules failed at **collection**
   (their ~99 tests dropped, leaving ~545 collectable — consistent with their
   "549 run, 4 errors"). The backend engineer ran **with the venv** and got a
   clean full run. **The "4 PIL errors" are an environment artifact (bare
   `python`, not `.venv/bin/python`), not real failures.** Under the
   authoritative `.venv/bin/python -m pytest` the suite is **644 passed / 0
   failed** with **no PIL errors**.

**One true (minor) count discrepancy, documented:** the backend engineer
reported **641**; the authoritative re-run is **644** (a **+3** delta, e.g.
three tests added after the backend engineer's run, or a count taken mid-edit).
This is non-blocking: the **authoritative** number, re-verified twice this pass
(pytest *and* unittest both = 644), is **644 passed, 0 failed**.

---

## 4. AC1–AC17 audit

Evidence key: **SS** = `scripts` live (e2e/qa_safe_doors) · **M** = my in-process
state/migration probe · **R** = my render probe (real `drawDoorCell`) ·
**H** = my hostile/occupancy probe · **U** = my UI smoke probe · **T** = the
feature's own suite (re-run green) · **D** = diff review.

| AC | Verdict | Evidence |
|---|---|---|
| **AC1** safe state model + round-trip + migration | **PASS** | **M:** `SAFE_DOOR_STATES==("L","U","O")`; `from_dict` of `safe={"2,0":"C"}` → `{"2,0":"U"}` and next `to_dict` emits `"U"` (never `C`); each of `L`/`U`/`O` round-trips; `__post_init__` raises `ValueError` for a stray un-coerced `C`, a bad char `X`, a floor-cell key, an OOB key, and a key in **both** `doors` and `safe`; `safe=None` omits `safe` from `to_dict`; `safe_door_state_at`/`is_safe_door_closed` correct (closed True for `L` **and** `U`, False for `O`/non-safe). **T:** `test_models.py` safe class. |
| **AC2** default locked on mark (fresh `L`; legacy `U`) | **PASS** | **M:** GM `mark` a normal doorway → `map.safe={"5,5":"L"}` (locked) and drops `5,5` from `doors`; player `mark` → `"not allowed"`. **SS:** e2e step 11 `(b)` mark→`L`, REST carries additive `safe`. **D:** only a GM WS `safe_door mark` creates a safe door — no REST/generation/detection path (`server.py`/`grid.py`/`detection.py` untouched). |
| **AC3** state machine + permissions (exact) | **PASS** | **M (full matrix, exact strings):** non-GM any action → `"not allowed"` (checked first); GM `mark` normal→safe `L`; `mark` on safe → `"already a safe door"`; `unlock` `L`→`U`, `lock` `U`→`L`, `lock` `O`→`L` (**force-closed**), `open` `U`→`O`, `close` `O`→`U`; every illegal pair's exact §4.3 string — `"safe door is locked"`, `"safe door is already open"`, `"safe door is already closed"`, `"safe door is already unlocked"`, `"safe door is already locked"`, `"not a safe door"` (×5 actions on a non-safe doorway), `"not a doorway"` (floor + wall), `"destination out of bounds"`, `"x and y must be integers"`, `"action must be one of mark/unmark/unlock/lock/open/close"` (bad + empty action); `unmark` preserves state (`L`→`L`/`U`→`U`/`O`→`O`, E7) and empties `safe`. **T:** `test_door_session.py::TestSafeDoor*`. **SS:** e2e step 11 `(b)`/`(g)`. |
| **AC4** wire + REST | **PASS** | **M:** every `welcome`/`state` for a grid with safe doors carries `map.safe` ⊆ `L/U/O`, **disjoint** from `map.doors`, and **no `C`** ever emitted; `map.doors` still `L/U/O`. **T:** `test_ws.py`/`test_api.py` (re-run green). **D:** no new REST route; `safe` additive. |
| **AC5** pathfinding / LOS / restriction UNCHANGED | **PASS** | **M (matrix, all safe states):** `walkable(L, any team incl None)==False`; `walkable(U, any)==False`; `walkable(O, party/neutral/None)==True`, `walkable(O, hostile)==False`; `has_line_of_sight` blocked by `L` **and** `U`, transparent for `O` (team-agnostic); `find_path` open-safe `O`: party routes **through** `(2,0)`, hostile **`None`** (sealed); closed (`L`/`U`) safe: **`None` for every team**. **D:** `app/pathfinding.py` **byte-identical** (empty diff + md5). |
| **AC6** awareness + explored UNCHANGED | **PASS** | **D:** `app/awareness.py` **and** `app/visibility.py` **byte-identical** (empty diff). **SS/T:** explored H/E/S + the safe-aware S-set re-derivation and monotonicity all pass in e2e step 11 `(e)/(f)` and `test_visibility.py` (safe-aware, `C`→`U` fixtures, assertion logic unchanged). |
| **AC7** legacy migration end-to-end | **PASS** | **M:** `from_dict` legacy `"C"` → `"U"`, wire emits `"U"`. **R:** a `U` safe door renders the **green slab with NO padlock** in the S tier (exactly "unlocked closed"); a subsequent GM `open` **and** `lock` are both legal (state-machine probe). No upgrade-locked-by-surprise: the migration target is `U` (unlocked), never `L`. **E12:** the client's `validateSafe` coerces a stale `C`→`U` (**R:** `validateSafe({"5,5":"C"})=={"5,5":"U"}`). |
| **AC8** normal doors byte-for-byte unchanged | **PASS** | **D:** `DOOR_ACTIONS = ("unlock","lock","open","close")` **untouched**; all `_on_door` strings (`"door is locked"`, `"door is already open/closed/unlocked/locked"`) byte-for-byte; the **only** action-tuple diff is `SAFE_DOOR_ACTIONS` gaining `unlock`/`lock`. **M:** a normal `door` message on a **safe** cell → `"not a normal door"` (guard fires first, safe record untouched); a normal `door` `unlock`→`open` on the `(10,4)` normal door still works. **T:** `test_door_session.py` normal-door class + `test_bad_action` green. |
| **AC9** six states render correctly at the S tier | **PASS** | **R (s=60, tier S, literal hexes):** **normal L** = brown slab `#9c6b3a` + **brass padlock `#e6b422` + steel shackle `#8a8f98` top-right** + keyhole; **normal U** = brown slab, **no padlock**; **normal O** = **soft yellow glow `#ffe9a8`** radial + brown ajar leaf, no slab/padlock; **safe L** = green slab `#4f9e6b` + brass padlock TR; **safe U** = green slab, no padlock; **safe O** = **soft green glow `#c9f2d4`** radial + green ajar leaf. |
| **AC10** padlock = the L-vs-U tell; open = the light (S) | **PASS** | **R:** `L` draws the padlock, `U` does not (identical slab+frame+planks otherwise — the padlock region is the **only** delta); `O` draws a radial glow whose center stop is the **family hue** (yellow normal / green safe) and **no** slab fill and **no** padlock. |
| **AC11** legible at the 8px minimum | **PASS** | **R (s=8):** six states pairwise distinguishable — closed slab is a **6×6** colored square (brown normal / green safe); `L` draws the padlock (`p=4`), `U` does not; `O` draws the radial glow (**no** slab fill, **no** ajar leaf, `s<10`); **plank lines dropped** (`s<12`), **inner shadow dropped** (`s<14`), **keyhole dropped** (`p<10`). Nothing drawn off-cell (stub records all rects/fills inside `[0,8]²`). |
| **AC12** looks good up to ~60px | **PASS** | **R (s=60):** plank seams drawn, inner shadow drawn, padlock keyhole drawn (`p≥10`), ajar leaf drawn; slab has frame+planks+shadow; padlock has body+shackle+keyhole. |
| **AC13** E (explored/greyed) tier renders all six greyed | **PASS** | **R (tier E):** `L`/`U` = `#8a94a0` slab + `#5f6874` frame (a **flat grey** — **no** brown/green hue, **no** inner shadow); `O` = `#e8ecf0` glow. Both families collapse to the **same** grey slab (hue intentionally removed). Hidden (`H`) cells are never passed to `drawDoorCell` by the render loop (skip in the doorway pass). |
| **AC14** E tier still distinguishes locked vs unlocked-closed | **PASS** | **R (tier E, s=60):** `L` draws the **`ePadlockMark` (`#cfd4db`)** faint padlock; `U` draws **no** mark (same `#8a94a0` slab); `O` draws the `#e8ecf0` glow. The discriminator is **padlock-mark presence** (value/lightness), not hue — holds after desaturation, for **both** families. |
| **AC15** safe doors GM-only for all six actions | **PASS** | **M:** a **player** sending each of `mark`/`unmark`/`lock`/`unlock`/`open`/`close` → `"not allowed"` (checked first); a **GM** succeeds on every legal `(state,action)`. **SS/T:** player tap on a safe cell sends **no** `safe_door` frame (no-op + hint) — `test_frontend.py` pins the player no-op; the render/interaction probe confirms the frame set. |
| **AC16** legend + tool UI (static + harness) | **PASS** | **U + D:** `#legend` has exactly **six** `.door-swatch` chips (normal `L/U/O` + safe `L/U/O`), each holding a **16×16 canvas** rendered by the genuine `renderLegendDoorSwatches`/`showView("map")` path (pixel-identical: correct family slab, padlock iff `L`, glow iff `O`); **not** `body.is-gm`-gated (the only `is-gm` legend rule is for `.legend-explored`). `#safe-action-row` has **six** `data-safe-action` buttons in order `mark/unmark/unlock/lock/open/close`; `#door-action-row` still has **four** (`unlock/lock/open/close`) — **unchanged**. Old `.swatch.door-open/-unlocked/-locked`/`.swatch.safe-door` chips and `--door-open/-unlocked/-locked`/`--safe-open`/`--explored-safe*` tokens are **gone**; the §5.3 tokens are present in **both** `T` (**R** asserts each literal hex) and `:root` (14 CSS props verified in `style.css`). |
| **AC17** full regression | **PASS** | `.venv/bin/python -m pytest` **644 passed** **and** `unittest discover` **644 OK**; `app/pathfinding.py` + `app/awareness.py` + `app/visibility.py` + `app/grid.py` **byte-identical** (empty diff + md5); `e2e_proof.py` all-✓ incl. step **[11]**; `qa_safe_doors.py` **54/54**; `/health` ok. *See the AC17 note in §5 for the one documented test-file nuance (`test_pathfinding`/`test_visibility` `C`→`U` fixture migration — zero assertion logic changed).* |

**AC audit summary:** **17 / 17 PASS.** No AC failed. The single behavioral
change the spec flags (A1 — safe doors gain a lock state, fresh mark→`L`,
legacy `C`→`U`) is verified end-to-end (model, state machine, wire, render,
live e2e), and the **unchanged** guarantees (pathfinding/LOS byte-identical,
hostile-on-safe restriction, closed=wall / open=sight-transparent,
normal-door surface byte-for-byte, awareness/visibility byte-identical) all
hold.

---

## 5. The four icon requirements the owner cares about (verbatim, §1.1)

> Six states: Normal locked = brown wooden door + locked padlock TOP-RIGHT ·
> Normal unlocked-closed = brown wooden door, no padlock · Normal open = an
> open door with a soft yellow light · Safe locked = green wooden door + padlock
> TOP-RIGHT · Safe unlocked-closed = green wooden door, no padlock · Safe open =
> an open door with a soft green light.

| # | Requirement | Verdict | Evidence (my render probe on the real `drawDoorCell`) |
|---|---|---|---|
| **(a)** Normal L = brown wooden door + **locked padlock top-right** | **PASS** | brown slab `#9c6b3a` + brass body `#e6b422`/steel shackle `#8a8f98` padlock; padlock bbox top edge = slab top, right edge = slab right inner edge (top-right corner), at **both** s=60 and s=8. |
| **(b)** Normal U = brown wooden door, **no padlock** | **PASS** | brown slab, **no** padlock body/shackle; the **only** pixel delta vs L is the padlock region (AC10). |
| **(c)** Normal O = open door with a **soft yellow** light | **PASS** | slab removed; **radial** glow core `#ffe9a8` (soft warm yellow) clipped to the frame + brown ajar leaf; no slab fill, no padlock. |
| **(d)** Safe L = green wooden door + **locked padlock top-right** | **PASS** | green slab `#4f9e6b` + same brass padlock top-right; no brown. |
| **(e)** Safe U = green wooden door, **no padlock** | **PASS** | green slab, no padlock; padlock-absence is the tell. |
| **(f)** Safe O = open door with a **soft green** light | **PASS** | radial glow core `#c9f2d4` (soft mint-green) + green ajar leaf; no slab, no padlock. |

**Padlock iff `L`; glow iff `O`** — asserted as invariants across all six
states at both tiers (AC9/AC10). **E (explored/grey) tier** still separates
locked from unlocked-closed by the faint `ePadlockMark` (AC14). All six legend
swatches are **pixel-identical** to this art (AC16). **All six met.**

---

## 6. UI smoke (genuine Node harness + static + live server; no browser)

- **Page loads with no JS errors:** `node --check app/static/app.js` → syntax
  OK; the real `app.js` boots under the harness for **both** the GM and a
  player (`onWelcome` → `applyState` → `showView("map")`) **without throwing**
  (`/tmp/qa_ui_smoke.js`). Live: `GET /` → 200 (16 KB), `GET /app.js` → 200.
- **Legend shows six pixel-accurate door swatches:** the genuine
  `renderLegendDoorSwatches()`/`showView("map")` path renders **exactly six**
  16×16 canvases — normal `L/U/O` (brown, padlock iff L, `#ffe9a8` glow for O)
  and safe `L/U/O` (green, padlock iff L, `#c9f2d4` glow for O), each over the
  `#efe9dc` floor base; re-render is idempotent.
- **Safe door tool has six sub-buttons:** `#safe-action-row` =
  `Mark / Unmark / Unlock / Lock / Open / Close` (`data-safe-action`, in that
  order), revealed when armed, **not** gated off from players' *read* of the
  legend (tool is GM-only in `#paint-group` as before).
- **Door tool unchanged:** `#door-action-row` = `Unlock / Lock / Open / Close`
  (four buttons) — byte-for-byte the pre-feature set.
- **Interaction (probes):** GM safe-door click sends `{type:"safe_door",x,y,action}`
  for the armed action; a **player** tapping a safe cell sends **no frame**
  (no-op + hint); a normal-door player tap still sends the inverse `door`
  action (regression intact).

UI smoke: **PASS.**

---

## 7. Bugs found

**None.** No `docs/qa/BUG-*.md` files created. No P1–P4 issues.

The two items I investigated and **cleared** (not defects):

1. **The PIL "4 errors" (frontend report) are an environment artifact, not a
   bug.** Under the venv (Pillow 12.3.0) the four PIL-dependent modules
   (`test_api`/`test_ws`/`test_imaging`/`test_detection`) import and pass
   cleanly; the errors only appear under the bare system `python3` (no
   Pillow). Resolved in §3. Action: run all backend tests via
   `.venv/bin/python`, never bare `pytest`/`python`.

2. **AC17 test-file nuance (documented, non-blocking).** The spec's §9
   "unmodified" wording for the regression test files is met in **spirit**, not
   letter: `tests/test_pathfinding.py` and `tests/test_visibility.py` **are**
   touched, but **only** to change the legacy safe-door **fixture** char
   `"C"` → `"U"` (both are *closed* for pathfinding/LOS, so behavior is
   identical) plus docstring updates. **Zero assertion logic changed** (I
   reviewed every hunk: each diff is a `"C"`→`"U"` in a `safe_grid(...)`/
   `set_safe_door(...)` call, or a docstring). This is **forced and
   necessary**: the model no longer *accepts* `"C"` (my probe confirms
   `Grid(..., safe={"x,y":"C"})` now raises `ValueError`), so the old `"C"`
   fixtures would otherwise error at setup. It is exactly the §3.2 migration
   the spec prescribes — a closed-safe fixture must be expressed as a valid
   closed char, and `"U"` (the migrated closed state) is the faithful choice.
   `test_models.py`, `test_door_session.py`, `test_ws.py`, `test_api.py`,
   `test_frontend.py`, `tests/js/harness.js` all **gain** assertions consistent
   with the feature (the existing normal-door, pathfinding-logic, and
   visibility-logic assertions are unchanged). **Not a regression.**

---

## 8. Suggested new tests (gaps)

Coverage is already very strong (the full suite is green, and my four
independent probes add ~200 more assertions over the engineers' own). Minor,
non-blocking gaps I would add only if desired:

1. **`unmark` reversion on a `U`/`O` safe door into a normal door that was
   *already recorded* (e.g. normal `"U"`).** E7 is covered for `L/U/O`→normal,
   but a live wire check that the preserved char lands in `map.doors` **and**
   that the cell stays a valid doorway would close the last thread. (Low
   priority — state-machine probe covers the logic.)
2. **E-tier legend parity.** The legend always renders the **S** tier (true
   colors). There is no test pinning that a *player's* in-memory (E-tier) door
   swatch could differ (it isn't in the legend by design). A documentation
   note in the legend is cheaper than a test. (Very low priority.)
3. **`drawDoorCell` at `s=4` (preview floor).** §6.4/E1 notes the 4px preview
   sliver; the live map floors `s` at 8, but the preview canvas allows 4. A
   single "does not draw off-cell / does not throw at `s=4`" guard would pin
   the preview-floor edge. (Low priority.)

None of these block sign-off.

---

## 9. Working-tree / process state

- **Nothing committed or pushed by me.** `git log` shows no new commits; the
  latest commit predates my work.
- **`docker-agent.yaml` and `TODO.md`** remain modified (pre-existing /
  process bookkeeping), untouched by me.
- **One fresh server left on 0.0.0.0:8000** (pid 76696) running the **new**
  code, so the team's standing live server reflects this feature
  (per spec §15 "server reload note"). It can be stopped with
  `lsof -ti:8000 | xargs kill`. My ephemeral servers (the `qa_*`/e2e scripts
  boot their own on port 0) self-shut in `finally`; no stray listeners.
- The sign-off (this file) is the only new file; left untracked, as permitted.
  `/tmp/qa_state_probe.py`, `/tmp/qa_render_probe.js`,
  `/tmp/qa_hostile_probe.py`, `/tmp/qa_ui_smoke.js` live outside the repo.

---

## 10. Final verdict

**PASS.**

**Reasoning.** I independently re-ran every suite (pytest **644**, unittest
**644**, frontend **134**, e2e_proof **all-✓** incl. step 11, qa_safe_doors
**54/54**) and, beyond the engineers' own proof, wrote and ran **four original
probes** (~200 assertions) that drive the **real** server, the **real**
`app.models`/`app.session`/`app.pathfinding`, and the **real** `app.js`
`drawDoorCell`/`renderLegendDoorSwatches` — none reuse the engineers' test
logic. All green. The **PIL discrepancy is resolved** (environment artifact:
bare `python` vs `.venv/bin/python`; the venv run is 644/0 with no PIL errors).
All **17 acceptance criteria** pass, including the six the owner cares about
verbatim: each of the **six** door states renders the correct pictorial icon at
**both S and E tiers** and **both s=8 and s=60**, with the padlock present
**iff** locked (`L`), the light **iff** open (`O`) (soft **yellow** normal /
soft **green** safe), and the E tier still distinguishing locked from
unlocked-closed by the faint padlock mark.

The byte-identical hard constraints hold (`pathfinding.py`, `awareness.py`,
`visibility.py`, `grid.py` — empty diff + md5); the only backend-python change
is `models.py` + `session.py`; the hostile-on-safe restriction, the
closed=wall / open=sight-transparent behavior, and the normal-door surface are
all **unchanged** and regression-verified. The one AC17 nuance (the
`test_pathfinding`/`test_visibility` `"C"`→`"U"` fixture migration) is forced
by the model, changes **no assertion logic**, and is documented in §7.

**No P1–P4 bugs found.** The door-iconography + safe-door-lock feature is
**approved to ship** with no open QA items.

**Verdict: PASS.**
