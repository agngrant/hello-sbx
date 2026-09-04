# QA Sign-off — Safe-Room Doors

**Feature:** GM "Safe-Room" Doors (`doorway` cell + additive `Grid.safe` "C"/"O" record)
**Branch:** `feat/safe-room-doors` (uncommitted working tree — verified as-is)
**Spec:** `docs/design/safe-room-doors.md` (AC1–AC16 in §15, edge cases E1–E14, assumptions A1–A10)
**QA mode:** independent re-run of every suite + an original live WS/REST probe
(`qa_live_probe`, ephemeral server) + an original Node-harness render probe
(`qa_node_probe`, real `app.js` under `tests/js/harness.js`) + line-by-line
diff review. No code changes were made (docs only: this sign-off).

**Sign-off date:** 2026-09-04
**QA verdict:** **PASS** (16/16 AC; 4/4 original user requirements; 0 bugs found)

---

## 1. Scope & independence

The tasking report claimed "pytest 617 · unittest 617 · e2e all ✓ (11 steps) ·
qa_safe_doors 47/47 · frontend 131." I re-ran **all** of them myself and, on
top, ran **two probes I wrote from scratch** that do not reuse the engineers'
`e2e_proof.py` / `qa_safe_doors.py` scenario code:

- **Live WS/REST probe** (`/tmp/qa_live_probe.py`, in two phases on ephemeral
  servers on port 0, shut down afterward). Phase A drives a real GM + player
  over a real WebSocket through the full safe-door lifecycle; Phase B uses a
  **fresh** session + player so the awareness/explored assertions read a clean
  frame stream. Every check is independent of the engineer test logic.
- **Node-harness render probe** (`/tmp/qa_node_probe.js`, real `app/static/app.js`
  executed under `tests/js/harness.js`). Drives the *genuine* render path
  (`onWelcome` → `drawGridOnCanvas`) and the *genuine* interaction path
  (`paint-group` dispatch + canvas click), asserting the green-cross strokes,
  colors, bar, and the frames actually sent.

Line-by-line diff review of every changed file against the spec (models,
pathfinding, session, server, app.js, index.html, style.css, all test files,
e2e step 11, new `qa_safe_doors.py`).

Files the spec marks as **byte-identical** — verified `git diff HEAD -- <f>` is
**empty** for all of them: `app/awareness.py` (AC8(a)/AC16), `app/visibility.py`
(I6/AC16), `app/grid.py` (sample geometry — **byte-identical**), `app/main.py`,
`app/ws.py`, `app/detection.py`, `app/generation.py`, `app/imaging.py`.

Modified (diffs reviewed line-by-line): `app/models.py`, `app/pathfinding.py`,
`app/session.py`, `app/server.py`, `app/static/{app.js,index.html,style.css}`,
`scripts/e2e_proof.py` (step 11), `scripts/qa_safe_doors.py` (new),
`tests/{test_models,test_pathfinding,test_visibility,test_door_session,
test_ws,test_api,test_frontend}.py`, `tests/js/harness.js`.

---

## 2. Test-suite results (all re-run by me this pass)

| Suite | Command | Result |
|---|---|---|
| Unit (pytest) | `.venv/bin/python -m pytest` | **617 passed** (121 subtests) |
| Unit (unittest) | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 617 tests … OK** |
| E2E live (11 steps) | `.venv/bin/python scripts/e2e_proof.py` | **ALL CHECKS PASSED** incl. new step [11] safe-room doors; exit 0 |
| Safe-doors live | `.venv/bin/python scripts/qa_safe_doors.py` | **47/47 checks PASSED**; exit 0 |
| Frontend (Node harness) | `pytest tests/test_frontend.py` | **131 passed** (23 subtests) |
| `/health` | during both live probes | `{"status":"ok"}` |
| **My independent live probe** | `/tmp/qa_live_probe.py` | **ALL checks PASSED** (50+ assertions) |
| **My independent Node render probe** | `/tmp/qa_node_probe.js` | **ALL checks PASSED** (green cross / bar / palette / GM tool / player no-op) |

No linter is installed in the venv (no `pyflakes`/`ruff`); all changed modules
parse cleanly (`ast.parse`). The full green re-run above is the gate.

New safe-door test coverage added (per file): `test_models` 34, `test_pathfinding`
23, `test_visibility` 6, `test_door_session` 26, `test_ws` 5, `test_api` 5,
`test_frontend` 35. `git diff` confirms existing test files **only gain**
assertions (the sole 3 "removed" lines in `test_models.py` are an import
expansion + the `_grid` helper signature gaining `safe=` — no existing
assertion deleted), consistent with the spec's additive "0 existing assertions
break" (§12/§14) claim.

---

## 3. AC1–AC16 audit

| AC | Verdict | Evidence |
|---|---|---|
| **AC1** safe state model + round-trip | **PASS** | `test_models.py::TestSafeDoor*` (round-trip, `to_dict` emit/omit, `__post_init__` rejects floor/wall·OOB·bad-state·**both-doors-and-safe** mutual exclusion, `is_safe_door`/`safe_door_state_at`/`is_safe_door_closed`). Live: my probe's mark/REST/round-trip. |
| **AC2** default closed, no lock, GM-only creation | **PASS** | live: mark → `map.safe={"5,5":"C"}` (no lock char); player `mark` → `"not allowed"`. No REST route creates one (server diff is the additive `_with_doors` only); `generation.py`/`detection.py`/`grid.py` reference **no** `safe` (sample dungeon clean, byte-identical). |
| **AC3** state machine + permissions (exact) | **PASS** | `test_door_session.py::TestSafeDoor*` (2×4 + mark/unmark on non-safe; every illegal combo's exact string). Live (my probe): `not allowed` (non-GM, checked **first**), `x and y must be integers`, `destination out of bounds`, `not a doorway`, `action must be one of mark/unmark/open/close`, `already a safe door`, `not a safe door`, `safe door is already open/closed` — all exact, §4.3 order held. |
| **AC4** closed safe door blocks LOS like a wall (incl. corner-cut) | **PASS** | `test_pathfinding.py::TestSafeDoor*` (closed blocks LOS = wall, open transparent, both-elbows-blocked corner-cut, team-agnostic `has_line_of_sight` signature unchanged). Live explored checks (AC7) confirm far side H/E. |
| **AC5** movement + entity restriction (core rule) | **PASS** | In-process matrix I ran: closed safe `walkable`=False for **None/party/neutral/hostile**; open safe `walkable`=True for None/party/neutral, **False for hostile**; `find_path` open-safe: party/neutral route **via (2,1)**, hostile **None** (sealed); closed safe: **None for every team**. `test_pathfinding.py::TestSafeDoor*` covers the same + no-safe regression. |
| **AC6** hostile can't stand on open safe; party/neutral can | **PASS** | Live (my probe): hostile through **open** safe → `"no route — wall in the way"`, position **unchanged** (verified no state broadcast); hostile moved straight **onto** the open safe cell → same no-route; neutral NPC **walks through** to the far cell. `test_door_session.py` session-level equivalents. |
| **AC7** hostile override/place/create/set_team guard (safety rule) | **PASS** | Live (my probe): hostile **override**/`place`/`create_entity` onto the safe cell → `"cannot place a hostile on a safe room door"`, **not teleported** (no state broadcast in each case); **E4** `set_team`→hostile while standing on the safe cell → same rejection, team unchanged. **Contrast (E11):** a **party** override onto a **closed** safe door is **allowed** (token lands (5,5)). `test_door_session.py` covers all four paths + I4b invariant. |
| **AC8** awareness UNCHANGED, safe-driven only via LOS | **PASS** | (a) `app/awareness.py` **byte-unchanged** (diff 0). (b) Live (my probe, fresh session): hostile behind **closed** safe door within radius → **APPROXIMATE** (non-revealing `<approx-n>` surrogate, no name/label); behind **open** safe door → **FULL** (named/labeled — sight is team-agnostic). (c) GM never filtered (existing pin). |
| **AC9** occupancy guards (E1/I8) | **PASS** | Live (my probe): **E1** `mark` with a token on the cell → `"cannot mark a safe door with a token on it"`; `close` with a token on the cell → `"cannot close a door with a token on it"` (door stays open). `test_door_session.py::TestSafeDoor*` marks/closes with occupancy. |
| **AC10** wire + REST + rendering | **PASS** | (a) Live: every GM/player `welcome`/`state` carries `map.safe` + `map.doors`, **disjoint** and jointly covering doorways; grid with no safe doors omits `safe` (verified absent-by-default + after unmark). (b) `GET /api/maps/sample-dungeon` gains `safe` only when present (disjoint from `doors`); existing keys intact; **no new REST route** (server diff = additive `_with_doors` only). (c) `T.safeOpen`/`T.safeClosed`=`#3ddc84` (+ explored `#8fae9c`) distinct from floor/wall/party-green (my Node probe). (d/e) `index.html` has `data-tool="safeDoor"` + four `data-safe-action` sub-buttons + `legend-safe` chip, **not** `body.is-gm`-gated (my Node probe + static). |
| **AC11** frontend rendering + interaction | **PASS** | My independent Node probe (genuine `app.js`): **open** safe door = green cross `#3ddc84` (2 segments, **no bar**); **closed** = cross **+ bar** (3 segments, incl. the horizontal bar); **explored** tier = sage `#8fae9c`; GM `safeDoor` tool click sends `{type:"safe_door",x,y,action}` (mark **and** unmark), floor cell sends nothing; **player tap on a safe cell (C and O) sends NO frame** (no door, no move); regression: player tap on a **normal** door still sends inverse `door` action. `test_frontend.py::TestSafeDoor*` (35) pins the same. |
| **AC12** backward compatibility (additive) | **PASS** | In-process: old `Grid(name,width,height,cells,image,doors)` ctor works (`safe` defaults None); old 2/3-arg `walkable`/`is_valid_step`/`has_line_of_sight`/`find_path` run and behave identically for safe-less grids; `from_dict` (no `safe`) → no safe doors; `door` message + `DOOR_STATES=("L","U","O")` + error strings byte-for-byte (verified `DOOR_STATES` line + `"action must be one of unlock/lock/open/close"` still fires on a normal door). |
| **AC13** frozen normal-`door` surface + safe guard | **PASS** | (a) Every existing normal-door test passes unmodified (617 green). (b) Live (my probe): **all four** normal `door` actions (unlock/lock/open/close) on a safe cell → `"not a normal door"`; the safe record is **untouched** (re-triggered a state and confirmed `safe` present, `doors` still has no `5,5`); a normal `door` `unlock` on the (10,4) normal door **still works**. The only shared-code touch is the additive `if self.grid.is_safe_door(...)` guard line in `_on_door` (diff-confirmed — never fires for non-safe cells). (c) `test_bad_action` normal-door error intact. |
| **AC14** e2e + live proof | **PASS** | `e2e_proof.py` step 11 **all ✓** (default absent, mark→C, open/close, hostile blocked even open, override/place/create rejected, E11 party allowed, awareness tiers, explored H/E + face, unmark reversion, permissions, `door`-on-safe, safe-aware S-set re-derivation); `qa_safe_doors.py` **47/47**; my independent live probe (50+ checks) **all PASS**; `/health` ok. |
| **AC15** performance budget | **PASS** | `test_door_session.py::TestSafeDoor*` perf test (60×60 mixed safe/normal doors, 6 players + GM, full `state_for` recompute `< 500 ms`; 60×60 `find_path(team=...)` `< 50 ms`) — passes. Blocked set derived **once** per A*/`visible_cells` (`_blocked_for` / `_open_safe_doors` / safe-aware `_closed_doors`). |
| **AC16** full regression | **PASS** | `pytest` **617 passed** + `unittest` **617 OK** (no existing test modified — additive only, diff-confirmed); `e2e_proof.py` all-✓ incl. step 11; `/health` ok; `app/grid.py` **byte-identical**; `app/awareness.py` **and** `app/visibility.py` **byte-identical** (diff 0); normal-`door` wire frame + `DOOR_STATES` + error strings byte-identical. |

**AC audit summary:** **16 / 16 PASS.** No AC failed, no AC required a
caveat. The four design decisions to validate (A1–A10) were all confirmed:
safe door is GM-controlled end-to-end; hostile can **never** be placed on a
safe cell even under override (the one deliberate difference from the normal
closed-door override); unmark reverts to a normal door preserving
open/closed (`C`→`U`, `O`→`O`, verified live).

---

## 4. The four original user requirements (verbatim)

> "…allow the GM to add 'safe room' doors to the map — the safe room will
> allow only neutral npcs or player characters to step onto the safe room
> door. The door will always be unlocked but can be closed, and starts
> closed. The icon for the door should be a green cross."

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| **(a)** GM can **ADD** safe-room doors | **PASS** | GM `safe_door mark` on a `doorway` converts it to a safe door (live: `map.safe={"5,5":"C"}`, `map.doors` drops `5,5`). GM-only tool `🛡 Safe door` + Mark/Unmark/Open/Close (Node probe: real dispatch sends the frame). A safe door can only be created by a GM WS `mark` — no REST route, no generation/detection path. |
| **(b)** Only **neutral NPCs + player characters** may step onto it | **PASS** | team ∈ {`neutral`,`party`} may occupy; `hostile` is **blocked even when the door is OPEN** (live: hostile `no route`, position unchanged, never teleplanted via override/place/create/set_team). party + neutral both walk through the open safe door (live + in-process `find_path`). |
| **(c)** Always **unlocked** + **can be closed** + **starts closed** | **PASS** | No lock state exists (`SAFE_DOOR_STATES=("C","O")`, no `"L"`); mark **starts `C`** (closed) — live verified; GM `open`→`O`, `close`→`C` (live + exact error strings). "Always unlocked" ⇒ there is nothing for a player to unlock, consistent with the GM-only control surface. |
| **(d)** Icon is a **green cross** | **PASS** | `T.safeOpen`/`T.safeClosed` = **`#3ddc84`** (bright mint), explored **`#8fae9c`** — distinct from floor/wall/normal-door red-amber and the party token green. Node probe on the genuine render path: open = **green cross** (2 strokes, no bar); closed = **cross + bar** (the "closed" idiom); legend swatch shows the cross; chip visible to both roles. |

All four original requirements met.

---

## 5. UI smoke (genuine Node harness + static; no browser)

- **GM `🛡 Safe door` tool** present in the GM-only `#paint-group`
  (`index.html`), with `#safe-action-row` sub-buttons
  `data-safe-action=mark/unmark/open/close`, revealed only when armed.
- **Legend chip** `safe door (green cross)` present with a cross swatch,
  **not** `body.is-gm`-gated (visible to GM and players).
- **Interaction (real dispatch, my Node probe):** arming the tool + clicking a
  doorway sends `{type:"safe_door",x,y,action}` (mark and unmark both
  verified); clicking a floor/wall cell sends nothing; a **player** tapping a
  safe cell (closed *or* open) sends **no frame** (no `door`, no `move`); a
  player tapping a **normal** door still sends the inverse `door` action
  (regression intact).
- **Render (real `drawGridOnCanvas`):** green cross + bar-per-state + explored
  sage tier all confirmed by recorded stroke geometry.

UI smoke: **PASS**.

---

## 6. Bugs found

**None.** No `docs/qa/BUG-*.md` files were created.

The one spec-vs-implementation nuance I investigated: spec §4.4's reference
snippet calls `self.grid.set_safe_door(x, y, "C")` directly on a `mark`, which
would raise `ValueError` for a doorway that still has a **recorded** normal
door (e.g. already-unlocked `"U"`), because `set_safe_door` enforces mutual
exclusion. The **implementation is correct and better than the snippet**: the
`_on_safe_door` `mark` branch **drops the recorded normal state first**, then
calls `set_safe_door`. I confirmed this is live-correct and test-pinned
(`test_door_session.py::test_mark_records_existing_normal_state`: `unlock` →
`mark` → `safe={"5,5":"C"}`, `doors` drops `5,5`). This is a harmless spec
pseudo-code inaccuracy, not a defect — behavior matches spec intent (§3.5
"Marking a normal door converts it"). No action required.

Pre-existing (not introduced here, out of scope for this feature): the
cross-session shared-`Grid` identity on unregistered session ids was already
documented and accepted in `docs/qa/BUG-DOORS-001.md` (P2). Safe-door state
rides the same per-grid object, so it inherits that documented behavior; it is
not a regression and not a new bug.

---

## 7. Suggested new tests (gaps)

Coverage is already very strong (139 new safe-door test methods across 7 files
+ a 47-check live script + e2e step 11). Minor, non-blocking gaps I would add
only if desired:

1. **Live `mark` on a recorded-unlocked normal door.** Add a *live* (WS) check
   that `mark`-ing a doorway whose normal door is already `"U"` (not just
   default-locked) yields `safe={"5,5":"C"}` and drops `5,5` from `doors` —
   the in-process `test_mark_records_existing_normal_state` covers it, but a
   live wire check would close the last thread. (Low priority — covered.)
2. **`doors_for_wire`/`safe_for_wire` joint-coverage on a grid where EVERY
   doorway is a safe door.** The `I5` "jointly cover all doorways" claim on a
   100%-safe grid (where `map.doors` would be `{}` but still present because
   a doorway exists) is implied by the code comment but has no explicit test
   pinning the empty-`doors`-still-present edge. (Low priority.)
3. **AC8(d) no-safe-door awareness byte-pin.** A direct assertion that
   `build_awareness` over the sample dungeon with no safe doors is
   byte-identical to a pre-feature baseline (today it follows transitively
   from `awareness.py` byte-identical + no-safe `_closed_doors` identity).
   (Low priority.)

None of these block sign-off.

---

## 8. Working-tree / process state

- **Nothing committed or pushed by me.** `git log` shows no new commits; the
  latest commit predates my work.
- **`docker-agent.yaml` remains uncommitted** (` M`), untouched, as required.
- **No server left running** — both live probes used ephemeral servers
  (port 0) and shut down in `finally`; port 8000 confirmed free afterward.
- The sign-off (this file) is the only new file; it is left untracked, as
  permitted. `/tmp/qa_live_probe.py` and `/tmp/qa_node_probe.js` live outside
  the repo and did not touch the tree.

---

## 9. Final verdict

**PASS.**

**Reasoning.** I independently re-ran every suite (pytest 617, unittest 617,
e2e_proof 11-steps all-✓, qa_safe_doors 47/47, frontend 131) and, beyond the
engineers' own proof, wrote and ran two original probes — a live
WS/REST lifecycle probe and a Node-harness render/interaction probe — that
drive the *real* server and the *real* `app.js`. All green. The 16 acceptance
criteria and the 4 verbatim user requirements are all met. The
byte-identical hard constraints hold (`awareness.py`, `visibility.py`,
`grid.py` — diff 0); the only shared-code touch is the additive one-line
`_on_door` guard that never fires for non-safe cells; existing tests were
extended, not modified (diff-confirmed). The core entity restriction
(hostile blocked even on an **open** safe door; party/neutral pass), the
hostile override/place/create/`set_team` safety guard (the deliberate
difference from the normal closed-door override), the occupancy guards, the
`doors`/`safe` wire/REST disjoint partition, the awareness/explored
safe-aware behavior, the green-cross render, and the GM-only UX are all
correct and test-pinned.

**No P1–P4 bugs found.** No blocking or non-blocking conditions. The
safe-room-doors feature is **approved to ship** with no open QA items.

**Verdict: PASS.**
