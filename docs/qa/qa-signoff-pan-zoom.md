# QA Sign-off — Pan & Zoom (map viewport navigation)

**Feature:** Pan & Zoom for the Tactical Map — `docs/design/pan-zoom.md` (AC1–AC22, E1–E9, A1–A3).
**Branch:** `feat/pan-zoom` @ `61e30ac6` (changes in working tree, **not committed**).
**Reviewer:** independent QA (orchestrator static review + `qa` sub-agent executable verification).
**Date:** session for the pan-zoom build.

**VERDICT: ✅ PASS** — all 22 acceptance criteria met; full suites green; e2e all-✓;
live smoke all-✓; **no bugs** (0 P1 / 0 P2 / 0 P3).

---

## 1. Scope of change (verified)

`git diff --name-only` (feature set):

| File | Role |
|---|---|
| `app/static/app.js` | view state + view math (V1–V2, V4–V7, V9–V11), keyboard retarget (V8), culling (§6) |
| `app/static/index.html` | `#nav-panel` (V3) |
| `app/static/style.css` | `.nav-btn` / cluster / zoom group + <1024px 44×44 targets |
| `tests/js/harness.js` | nav buttons, controllable wrap size, pointer→clientX/Y, captured draw calls, rAF stub |
| `tests/test_frontend.py` | new `TestPanZoom` (31 tests, AC1–AC22 + A2) |
| `README.md` | "Playing" + "Navigating the map" + Limitations updated (A11/V13) |
| `PROJECT.md` | §11 limitation mirror updated |
| `docs/design/wireframes.md` | §9 keyboard arrow-key nudge marked **RETIRED** (A2) |
| `docs/design/pan-zoom.md` | the spec (new, untracked) |

**No `app/*.py` server file is modified** (confirmed: `git diff --name-only | grep '^app/.*\.py$'` →
empty). No wire protocol / REST / server / data-model / awareness changes (AC21/A1). Non-feature
working-tree items (`TODO.md`, `docker-agent.yaml`, `docs/websearch-investigation.md`) are
pre-existing team config/notes, not part of the feature.

---

## 2. Test results (exact counts)

| Suite | Command | Result |
|---|---|---|
| Full (pytest) | `.venv/bin/python -m pytest` | **680 passed**, 78 warnings (pre-existing websockets/uvicorn deprecations), 140 subtests passed — 0 failed/skipped/error |
| Full (unittest) | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 680 tests → OK** |
| Frontend harness module | `tests.test_frontend` | **Ran 170 tests → OK** |
| Pan & Zoom class (isolated) | `tests.test_frontend.TestPanZoom` | **Ran 31 tests → OK** |

(The single `AssertionError` traceback printed during the unittest run is **log noise from an
intentional negative test** — a static-file path receiving a non-HTTP ASGI scope expects a 404 —
not a failure; the run ends `OK`.)

## 3. e2e proof

`.venv/bin/python scripts/e2e_proof.py` → **`✓ ALL E2E CHECKS PASSED`**, zero ✗.
This confirms the WS protocol is untouched (the script asserts byte-level frame shapes it has
always used). Consistent with a frontend-only change.

## 4. Live smoke (ephemeral port 8771; not 8000; released)

- Server: `.venv/bin/python -m app.main --host 127.0.0.1 --port 8771` (PID 45216);
  `GET /health` → `{"status":"ok"}`.
- Generated **60×60** (seed 7) and **8×8** (seed 7) via `POST /api/maps/generate` → both 200,
  exact dims. GM `use_map` over one live WS (real wire) → `state.map` reflects each dimension;
  session stays alive; a later `request_state` returns the **same, byte-identical** grid.
- **No `view`/`pan`/`zoom`/`level` key ever appears in any wire payload** → AC21 confirmed on the
  real wire, not just in the harness.
- Independent (re-implemented, not imported) fit math: 60×60→**L10**, 24×16→**L6**, 8×8→**L2**,
  8×7→L1, 16×12→L4; 60×60@L10 vertical pan range = **10** (E7). All match spec §5.1/§5.3.
- Frontend (real `app/static/app.js` via the Node harness): fit-on-join, pan clamp, small-map
  centered/pan-disabled, 60×60 vertical pan, **AC18** overlay alignment (markers at
  `ox+(x+0.5)·cell, oy+(y+0.5)·cell` = the grid's own transform) and **AC16** coordinate
  round-trip (paint at L4 pan(2,3) lands on `[(2,3),(5,3),(10,8),(17,15)]`, nothing from the
  letterbox bar) all PASS. Extra throwaway `node -e` probe: `cellFromEvent` at the pixel center of
  grid cell (10,8) under L4/pan(2,3) returns `{x:10,y:8}`; points past the window return `null`;
  readout `L4 · 16×13 · (2,3)–(17,15) of 60×60`. **Round-trip OK.**
- **Cleanup:** `kill 45216`; `lsof -i :8771` → no listener; **rebind to 127.0.0.1:8771 succeeded
  → port 8771 confirmed free.**

## 5. Static UI / A2-retirement checks

- `#nav-panel` is the **first `<section>` of `#sidebar`** (before `#entity-tools`, `#awareness`).
  Arrow cluster `#nav-up/#nav-down/#nav-left/#nav-right`, zoom group `#zoom-in/#zoom-out`, and
  `#nav-readout` all present; all six `disabled` in the HTML default (pre-welcome) state.
- `syncNavControls()` sets `disabled`+`title` on all six controls: pre-welcome "No map yet";
  per-axis lock "Map fits horizontally/vertically — no pan"; per-edge "Panned to the
  west/north/east/south edge"; zoom extremes "Maximum zoom (6×5)" / "Minimum zoom (60×50)";
  one-line readout. Matches spec §3.3.
- **A2 retirement:** the document `keydown` handler's arrow cases call `panBy(±1,0)/(0,±1)`;
  `ev.shiftKey` is inside the any-modifier ignore guard (`ctrlKey||metaKey||altKey||shiftKey`);
  there is **no** Shift+arrow handler and **no** arrow→`sendMove` path (movement is click/tap
  only). `wireframes.md` §9 marked RETIRED; README "Playing" updated. **No dead code / leftover
  listeners** for the old nudge behavior (searched `ev.code`, `farthest`, `nudge`, `Shift+arrow`).
- **`cellFromEvent` "in-bounds" = visible render window** (`state._view` `[x0,x1)×[y0,y1)`, which
  always lies inside the map); letterbox bars emit `null`. **Map bounds are still enforced
  server-side** for paint/move/door intents (the server is the authority); the client simply
  doesn't send out-of-window cells. Confirmed in `app.js` (cellFromEvent) + server validators.

---

## 6. Per-AC results (AC1–AC22)

Evidence column: harness test id (`TestPanZoom`) and/or live/code inspection.

| AC | Description (abridged) | Result | Evidence |
|---|---|---|---|
| AC1 | Map bigger than viewport is navigable; (59,59) visible at L10 pan(0,10) and L0 pan(54,55) | **PASS** | `test_ac1_navigable_cell5959`. **Off-by-one note:** the spec's literal example "L0, pan (54, 54)" is off by one (at panY 54 row 59 is out); the test correctly asserts **pan (54, 55)** (max panX = 60−6 = 54, max panY = 60−5 = 55) — the right thing. |
| AC2 | Clickable arrows in right-hand panel pan by the step | **PASS** | `test_ac2_arrow_buttons_pan_by_step` (←/→/↑/↓ each move one axis by the step, window follows) + `test_ac2_nav_panel_in_sidebar_first` (nav-panel is first sidebar section; all ids present). Live: real buttons present, disabled pre-welcome. |
| AC3 | Cursor keys pan identically to buttons | **PASS** | `test_ac3_cursor_keys_match_button_delta` (keydown ArrowLeft delta == navLeft click delta). |
| AC4 | Zoom in/out buttons | **PASS** | `test_ac4_ac6_zoom_buttons_step_and_extremes` (+ ×10 → L10, − ×10 → L0; readout/levels track). |
| AC5 | `+`/`=` zoom in, `-` zoom out | **PASS** | `test_ac5_cursor_keys_zoom` (`+`→+1, `=`→+1, `-`→−1; `ev.key`-based so numpad works). |
| AC6 | Zoom extremes (6×5 at L0, 60×50 at L10, buttons disabled) | **PASS** | `test_ac4_ac6_zoom_buttons_step_and_extremes` + `test_ac6_no_level_outside_range` (levels clamped to [0,10]; extreme titles "Maximum zoom (6×5)" / "Minimum zoom (60×50)"). |
| AC7 | Level-table conformance + cell formula | **PASS** | `test_ac7_level_table_and_cell_formula` (all L0…L10 W×H + `cell = floor(min(availW/W, availH/H))`). |
| AC8 | Square cells + letterboxing (exact numbers) | **PASS** | `test_ac8_square_cells_and_letterbox` (L0: cell 80, ox 160, oy 0; L10: cell 8, ox 160, oy 0). |
| AC9 | Pan step per level (min 1) | **PASS** | `test_ac9_pan_step_table` (step tables == §2.4) + `test_ac9_panby_moves_by_step` (L4 = (2,1)). |
| AC10 | Pan clamp + edge-disabled titles | **PASS** | `test_ac10_pan_clamp_and_edge_titles` (40×30@L0 clamps to [0,34]×[0,25]; ←/→/↓ disabled + correct titles; ↑ enabled mid-map). |
| AC11 | Map smaller than view on an axis: centered + pan disabled | **PASS** | `test_ac11_small_map_both_axes_locked` (60×10@L10 both axes locked, pan (0,0)) + `test_ac11_zoom_in_unlocks_horizontal_only` (L9 unlocks horizontal only). |
| AC12 | Initial view fits whole map | **PASS** | `test_ac12_initial_fit_per_table` (6×5→L0, 10×8→L2, 24×16→L6, 60×50→L10, 60×60→L10, 30×60→L10…) + `test_ac12_fitted_map_inside_window`. Live: server dims feed fit. **Note:** brief's "8×8→L1" is a typo; spec/code/tests agree 8×8→**L2**. |
| AC13 | Re-fit on map swap | **PASS** | `test_ac13_refit_on_map_swap` (old map L3 pan(5,5) → new 24×16 re-fits to L6 pan(0,0)). Live: `use_map` swap keeps session, re-fits on dims. |
| AC14 | Resize keeps level + clamped pan (no re-fit) | **PASS** | `test_ac14_resize_keeps_level_and_pan` (L6 pan(8,4) unchanged after resize; cell recomputed; stays in bounds; **no** re-fit). |
| AC15 | Input focus guard | **PASS** | `test_ac15_input_focus_guard` (INPUT/TEXTAREA/SELECT → no pan/zoom, no preventDefault) + `test_ac15_contenteditable_guard` (A3) + `test_ac15_non_field_pans`. |
| AC16 | Painting under transform | **PASS** | `test_ac16_paint_resolves_cell_under_transform` (L4 pan(2,3): painted cells `[(2,3),(5,3),(10,8),(17,15)]`, letterbox emits nothing) + `test_ac16_door_and_safedoor_tools_resolve_cell`. Live `node -e` round-trip confirms (10,8). |
| AC17 | Movement + spawn under transform | **PASS** | `test_ac17_player_tap_to_move_under_transform` (move to (15,10) under L5 pan(4,2)) + `test_ac17_gm_add_spawns_on_hovered_cell` (spawn at hovered (7,9); out-of-window click sends nothing). |
| AC18 | Awareness overlay alignment under transform | **PASS** | `test_ac18_awareness_overlay_alignment` — highest-risk visual item: own token, FULL contact, APPROX "?" block center, and ring anchor all drawn at `ox+(x+0.5)·cell, oy+(y+0.5)·cell`, i.e. **the same `(s,ox,oy)` transform the grid uses** → misalignment structurally impossible. Code inspection confirms no overlay-specific geometry. |
| AC19 | 60×60 (E7): horizontal locked, vertical pan 0/5/10 | **PASS** | `test_ac19_sixty_by_sixty_vertical_pan` (fit L10; ←/→ locked; ↓×2 → panY 10 then ↓ disabled/↑ enabled; ↑×2 → 0 then ↑ re-disabled). Live: 60×60@L10 vertical range = 10. |
| AC20 | Rapid key repeat (E8): exact steps + one rAF render | **PASS** | `test_ac20_rapid_key_repeat` (5×ArrowDown → panY 10; exactly 1 rAF render queued; 1 render per frame). The `scheduleRender` `_renderQueued` guard is present and correct. |
| AC21 | Per-client, frontend-only (no wire changes) | **PASS** | `test_ac21_view_ops_send_no_wire_frames` (pan/zoom/fit/resize send **zero** WS frames) + live: no view/pan/zoom/level key in any real wire payload; diff confirms no `app/*.py` change. |
| AC22 | Render culling | **PASS** | `test_ac22_render_culling` (L0 on 60×60: grid fill only the 6×5 window; entity at (59,59) not drawn, (5,4) drawn; panning to (54,55) flips both). |

**All 22 ACs: PASS.**

---

## 7. Edge cases & assumptions (E1–E9, A1–A3)

- **E1** tiny map centered + pan disabled → AC11 (PASS). **E2** resize keeps view → AC14 (PASS).
- **E3** map swap re-fit → AC13 (PASS, live). **E4** input guard → AC15 (PASS).
- **E5** painting under transform → AC16 (PASS, live round-trip). **E6** awareness alignment →
  AC18 (PASS). **E7** 60×60 vertical pan → AC19 (PASS, live). **E8** rapid repeat → AC20 (PASS).
- **A1** frontend-only / per-client (PASS, AC21). **A2** arrow-key nudge retired (PASS — §5).
  **A3** guard extended to `<select>` + contenteditable (PASS — AC15).

---

## 8. Bug list

**No bugs.** Zero defects in the shipped code or the shipped tests.

| ID | Severity | Component | Summary | Status |
|---|---|---|---|---|
| *(BUG-012)* | — | — | Withdrawn placeholder — an AC20 test-strength suspicion drafted during review, retracted after confirming the `test_ac20_rapid_key_repeat` `q === 1` assertion **does** detect a `scheduleRender` coalescing regression (queue would read 5 without the guard) and the implementation satisfies "at most one rAF render per frame". No bug. | withdrawn |
| *(BUG-013)* | — | — | Withdrawn placeholder — a suspected stale "No zoom/pan" line in `wireframes.md`; a stale file snapshot during review caused the false suspicion, and a re-read + `grep -n "No zoom"` (→ no matches) confirmed the build had already updated every such statement. No bug. | withdrawn |

Both placeholders are kept for numbering continuity only.

---

## 9. Final verdict

**✅ PASS — sign off.**

Pan & Zoom is implemented correctly and completely against `docs/design/pan-zoom.md` AC1–AC22 /
E1–E9 / A1–A3. It is frontend-only (no server/wire/REST changes, verified by diff + live wire
probe). Full suites green (**680 pytest / 680 unittest**, **170** frontend harness, **31**
pan-zoom tests), e2e all-✓, and a live ephemeral-port smoke (60×60 + 8×8) passes 21/21 with the
port cleanly released. Docs (README, PROJECT.md §11, wireframes.md §9 + all former "No zoom/pan"
statements) are consistent with the shipped feature, and the A2 arrow-key-nudge retirement leaves
no dead code. **No bugs were found** (the two BUG-012/013 placeholders were retracted during
review; neither is a real defect).

**The Pan & Zoom feature is ready to merge.**
