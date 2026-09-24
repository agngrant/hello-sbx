# Session State — LittleDungeons

**Captured:** 2026-09-24
**Purpose:** Store current state for pickup in a future session.

## Current git state
- **Active branch:** `refine/continued` @ `dd64bb2` (in sync with origin, clean tree)
- **`main`:** @ `dd64bb2` = `origin/main`
- **Tags:** `v1.3-refinements` → `dd64bb2` (pushed); `v1.2-refinements` → `c410873`; `v1.1-boss` → `34be5d1`
- **All branches fully pushed and in sync.** No uncommitted work (working tree clean).
- Remote: `origin` = https://github.com/agngrant/hello-sbx.git

## Version history
- `v1.1-boss` — boss entity feature (multi-tile bosses, spawn UI, size display, save/load, QA sign-off)
- `v1.2-refinements` — perf refinements (REST paint data race fix, boss_footprints wire, entity index, offscreen grid cache) + legacy fog-toggle removal + client-side boss_footprints adoption
- `v1.3-refinements` — session.py mixin split, app.js ES-module split, save schema versioning + frozen-grid fix, Playwright probe, mypy hygiene, QA sign-off

## What was completed in the v1.3 line
1. `5d4aed7` — session.py (1,511-line god-module) split into 5 mixins + facade (conn/state/doors/actions/map) + type-only SessionBase for mypy. Zero behavior change, no test edits.
2. `0e3cdb3` — app.js (3,622-line monolith) split into 6 ES modules (state/render/game/net/ui/main). "Test what ships" approach: the Node harness dynamically imports the REAL module graph (no bundler). 3 latent QA probe breakages found & fixed.
3. `36e18a7` — save-bundle schema versioning (SCHEMA_VERSION=1, tolerant validator) + off-lock save data-race fix (grid deep-copied under session lock).
4. `ebe75d3` — Playwright real-browser smoke probe (scripts/qa_browser_smoke.py). Implemented but NOT executable in this sandbox (no browser).
5. `511c76a` — mypy gate hygiene (mypy==2.3.1 added to .venv via uv; stale mypy.ini baseline fixed).
6. `dd64bb2` — v1.3 QA sign-off (docs/qa/qa-signoff-v1.3-refinements.md).

## QA gates (all PASS as of dd64bb2)
- pytest: 848 passed, 159 subtests
- mypy app/: 0 errors, 20 files
- scripts/e2e_proof.py: ALL CHECKS PASSED
- scripts/qa_modal_smoke.py: ALL LIVE SMOKE CHECKS PASSED
- node --check: 7/7 OK (6 ES modules + harness)
- scripts/qa_browser_smoke.py: NOT executed (no browser in sandbox)

## Environment notes
- Python is NOT on PATH; use `.venv/bin/python` for all Python.
- The venv has NO pip and NO ensurepip. Use `uv pip install --python .venv/bin/python <pkg>` to add deps. uv is at /usr/local/bin/uv.
- mypy is now installed in the venv (mypy==2.3.1). Run as `.venv/bin/mypy app/` or `.venv/bin/python -m mypy app/`.
- Node v22.22.1 is available. The frontend ES modules and harness use native ESM.
- NO real browser can run in this sandbox (network policy blocks browser CDNs; Chrome needs 20 missing system libs). The Playwright probe cannot run here.

## Server operations (recurring)
- Start: `setsid -f .venv/bin/python -m app.main --host 0.0.0.0 --port 8000 > .logs/server-<ts>.log 2>&1` (setsid -f to survive shell timeout)
- Verify: `curl -s localhost:8000/health` → {"status":"ok"}; `lsof -i :8000 -P -n` shows `*:8000 (LISTEN)`
- Stop: `kill <pid>` (SIGTERM)
- Frontend is ES modules now: `/js/main.js` must serve 200 + text/javascript; `/app.js` is deleted (404).

## Architecture notes (for future work)
- Backend: `app/session.py` is a facade over `session_conn/state/doors/actions/map.py` mixins + `session_common.py`. `app/saves.py`, `app/server.py`, `app/main.py`, `app/models.py`, `app/pathfinding.py`, `app/awareness.py`, `app/visibility.py`, `app/generation.py`, `app/imaging.py`, `app/detection.py`, `app/grid.py`, `app/ws.py`.
- Frontend: `app/static/js/` — state.js (singletons), render.js, game.js, net.js, ui.js, main.js (entry). Tested via `tests/js/harness.js` (imports real modules) + `tests/test_frontend.py`.
- ~1500 tests across tests/. Boss footprint table source of truth: `app/models.py` BOSS_FOOTPRINTS, emitted on the wire as `boss_footprints`, client validates.

## Known follow-ups / next work
1. **Run the Playwright probe** (`scripts/qa_browser_smoke.py`) in a browser-capable environment (CI or local with `playwright install chromium`). Requires network to cdn.playwright.dev.
2. **GM "preview fog" feature** (design gap from fog analysis): GM has no way to preview what a player sees. New feature — design doc first (extend docs/design/gm-controller.md), then research + implement.
3. **Dependency hygiene**: 78 pytest deprecation warnings (websockets.legacy, uvicorn ws protocol) — consider updating pinned deps.
4. **Off-lock save issue** — resolved for the grid (deepcopy under lock). Verify no remaining save-serialization races.
5. **mypy.ini** now documents 0 errors/20 files (fixed). Keep the count from growing.
6. **qa_modal_*.js probes** now reuse buildApi() from the harness — keep them in sync if the harness API changes.

## Branch structure
- `main` — released code (v1.1-boss, v1.2-refinements, v1.3-refinements)
- `feat/boss-entity` — merged into main (v1.1-boss)
- `analysis/refinements` — merged into main (v1.2-refinements)
- `refine/continued` — merged into main (v1.3-refinements); active working branch, in sync with main
- `feat/save-load`, `feat/backend-refactor` — older branches (status unknown, likely superseded)
