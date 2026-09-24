# QA Sign-off — v1.3 refinements (`refine/continued`)

**Scope:** the 5 commits on `refine/continued` since `v1.2` (`c410873`).
**Branch:** `refine/continued` (HEAD = `511c76a`)
**Base:** `c410873` (`v1.2-refinements`)
**QA date:** 2026-09-24
**QA verdict:** PASS with caveats (browser probe not executable in this environment) — see §6.

## 1. Commits under test
| # | Commit | Subject |
|---|---|---|
| 1 | `5d4aed7` | refactor: split session.py god-module into mixins (conn, state, doors, actions, map) + type-only SessionBase for mypy |
| 2 | `0e3cdb3` | refactor: split app.js into ES modules (state/render/game/net/ui/main); harness imports the real module graph |
| 3 | `36e18a7` | fix: save-bundle schema versioning + freeze grid snapshot under lock (off-lock data race) |
| 4 | `ebe75d3` | test: add Playwright real-browser smoke probe (implemented, NOT executed — no browser) |
| 5 | `511c76a` | chore: restore mypy gate hygiene (mypy in venv + stale baseline) |

## 2. Per-work-item verification
### Work item 1 — session.py facade + mixins
- `app/session.py:59-60` `GameSession(SessionConnMixin, SessionStateMixin, SessionDoorMixin, SessionActionMixin, SessionMapMixin)`.
- Mixin modules present: session_conn.py, session_state.py, session_doors.py, session_actions.py, session_map.py, session_common.py.
- Type-only `SessionBase` (annotation-only + `raise NotImplementedError` stubs, shadowed in MRO).
- Coverage: tests/test_session.py imports GameSession from app.session.

### Work item 2 — app.js split into 6 ES modules
- Six modules under app/static/js/: main/state/render/game/net/ui, real import/export graph (main.js imports all five).
- Harness imports the REAL module graph (tests/js/harness.js:437-446 dynamic import; test_frontend.py:106 buildApi()).

### Work item 3 — save-bundle schema versioning + frozen grid snapshot
- app/saves.py:58 SCHEMA_VERSION = 1; _check_schema_version at :348 (rejects too-new, treats legacy as v1).
- app/server.py:374-384 freezes grid_copy = copy.deepcopy(grid) under `with session._lock:` (off-lock data race fix).
- Coverage: tests/test_saves.py:218/231/240/253, :776; tests/test_api.py:816, :834.

### Work item 4 — Playwright browser smoke
- scripts/qa_browser_smoke.py (347 lines) boots server + drives headless Chromium; checks lobby render, boot WS handshake, six-module 200 fetch, zero console/page errors. **Implemented, NOT executed here (no browser).**

### Work item 5 — mypy gate hygiene
- .venv/bin/mypy present; mypy.ini:16-23 documents frozen gate 0 errors/20 files.

## 3. Automated-gate status (ACTUAL results from this QA run)
| Gate | Result |
|---|---|
| `pytest` (full) | **PASS** — 848 passed, 159 subtests passed, 78 warnings, 0 failed (30.64s). Ran after clearing the stale cache; fresh `.pytest_cache` written. |
| `mypy app/` (venv) | **PASS** — `Success: no issues found in 20 source files` (exit 0). |
| `python scripts/e2e_proof.py` | **PASS** — all checks pass; final line `✓ ALL E2E CHECKS PASSED` (exit 0). Covers join/spawn, door unlock/open path, three-tier awareness, map upload + use_map, permissions/capacity, generated 24x16 pathfind, explored-map monotonicity, safe-room doors. |
| `python scripts/qa_modal_smoke.py` | **PASS** — `RESULT: ALL LIVE SMOKE CHECKS PASSED` (exit 0). Static serve checks (index.html/js/css), live modal delete driver (all steps pass, save file deleted from disk, API 404s after), pan/drawer regression driver (all steps pass), port free after stop. |
| `node --check` (6 modules + harness) | **PASS** — 7/7 files OK: state.js, render.js, game.js, net.js, ui.js, main.js, tests/js/harness.js. |
| `scripts/qa_browser_smoke.py` | Implemented, NOT executed here (no browser) |

## 4. Caveats / out of scope
- Playwright probe implemented but not executed in this environment; must run in a browser-capable environment before release confidence is complete.
- GM "preview fog" feature and other known follow-ups are out of scope.
- All Python gates were run via `.venv/bin/python` — a bare `python` is not on PATH in this sandbox (see §5).
- pytest emitted 78 deprecation warnings (websockets.legacy / uvicorn websockets protocol, "remove second argument of ws_handler"); none affect pass/fail, but they are worth a future dependency-hygiene pass.

## 5. Findings
- Stale pytest cache cleared (referenced non-existent test_player_join.py/test_repro_gm_join_entity.py). Confirmed before deletion: `.pytest_cache/v/cache/lastfailed` listed `tests/test_player_join.py` and `tests/test_repro_gm_join_entity.py::…` (plus 9 test_boss.py entries), and both files were verified absent from `tests/`. Cache removed with `rm -rf .pytest_cache`; the full suite then ran clean from scratch.
- `python` is not on PATH in this sandbox (`python: command not found`); all Python gates were executed with `.venv/bin/python`.
- Line references in §2 spot-verified against source at HEAD `511c76a`: `app/session.py:59-60` (GameSession mixin base list), `app/saves.py:58` (`SCHEMA_VERSION = 1`) and `:348` (`_check_schema_version`), `app/server.py:374-384` (grid frozen via `copy.deepcopy` under `with session._lock:`), `mypy.ini:16-23` (frozen 0-error/20-file baseline). All match.
- qa_modal_smoke's live driver exercised the served ES-module frontend over a real WS session (deleteSave wiring, `window.confirm` absent, modal busy/settle state), giving partial runtime evidence for work item 2's module graph even without a browser.

## 6. Final verdict
**PASS with caveats.** All five executable automated gates pass (848/848 tests, 0 mypy errors in 20 files, e2e proof all checks, modal smoke all live checks, 7/7 JS syntax checks). The only gate not executed is the Playwright real-browser probe, which is environment-limited (no browser in this sandbox) — it is implemented and must be run in a browser-capable environment before final release confidence is claimed.
