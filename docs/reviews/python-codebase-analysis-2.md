# LittleDungeons — Static Analysis Pass 2 (LSP + lint/type/security)

**Date:** 2026-07-10
**Analyzed tree:** `e47fde1` (branch `feat/backend-refactor`) — working tree identical to HEAD (git status clean for app/scripts/tests)
**Scope:** all 38 `.py` files under `app/`, `scripts/`, `tests/` (14 in app/)
**Raw outputs:** `.logs/lsp_analysis_2/` (lsp_diag_all.json, ruff_root.log, mypy_app.log, mypy_scripts_tests.log, pylint_app.log, radon_cc_app.log, bandit_app.log)

---

## 0. PRE-CONDITION DISCREPANCY (read first)

The task brief states *"the backend refactor was completed and merged to main, so this is a FRESH pass on the current tree."*

**That is not true of this repository.** Verified via git:

- `main` = `130efc4` (a docs commit, 2026-09-08). `origin/main` is the same.
- `git log --all --oneline --grep=refactor` shows **only two** refactor-related commits, both docs-only:
  - `e47fde1` `docs(reviews): backend refactor evaluation — efficiency, readability, PyPI reuse` (HEAD of `feat/backend-refactor`)
  - `7cee59f` `Add simplification review: package-based refactor roadmap (proposal only)`
- `git diff main..HEAD --stat` → **only 2 files, both docs**: `docs/reviews/backend-refactor-evaluation.md` (+423 lines) and `.gitignore` (+3). **Zero `.py` files differ between main and HEAD.**
- Last `app/*.py` change on the current lineage: `bb265c5` (save/load, 2026-09-07) — *pre-dates* the refactor-evaluation commit.
- No other branch, remote, stash, or commit contains any refactor code.

**Consequence:** the "current tree" is byte-identical to the tree analyzed in `python-codebase-analysis.md` (which was itself anchored at `e47fde1`). The prior pass already noted: *"Nothing from the prior evaluation is RESOLVED at this commit — the branch still sits at the docs-only evaluation commit."*

This pass was nonetheless run as a **full fresh analysis of the current tree** (no reuse of prior raw outputs), so the results below are independently re-verified and serve as the standing baseline. **No refactor findings can be marked "fixed" because no refactor code exists in the tree.** All 7 prior findings are re-confirmed at their current line numbers.

---

## 1. Toolchain & counts

| Tool | Version/config | Target | Result |
|---|---|---|---|
| pylsp (LSP diagnostics) | 1.15.0, stdio batch client (`.logs/lsp_analysis_2/` pattern), `textDocument/publishDiagnostics` per-file | all 38 py files, app+scripts+tests | clean parse; pyflakes subset captured in `lsp_diag_all.json` |
| ruff | repo-root config | whole repo | **268 errors** (app/ 33, scripts/ 77, tests/ 158) |
| mypy | strict-ish, app-only per brief | app/ | **29 errors in 7 files** (14 checked) — *down from the prior "66" because that count included tests/scripts* |
| mypy (supplement) | — | scripts + tests | 66 errors in 17 files (24 checked) — matches prior total |
| pylint | 3.3.x, default rcfile | app/ | **324 messages, rated 9.27/10** |
| radon | cc, min C | app/ | top: `generate_grid` D(30), `_on_safe_door` D(28), `build_app` region C(18) `_handle_upload`, `read_frame` D(22) |
| bandit | default profile | app/ | **6 issues** (2× B311 `random` in security-adjacent contexts, 1× B101 assert, 1× B110 try/except/pass, **1× B324 SHA1 `usedforsecurity` in `ws.py:96`**) |

Top ruff rule counts (whole repo): UP031 (75, `str.__add__` f-strings), RUF059 (36), BLE001 (23, blind except), I001 (19, unsorted imports), **F401 (13, unused imports)**, **F811 (11, redefinition/duplicate imports)**, S110 (10, try-except-pass), RUF100 (9), F841 (8, unused locals), EXE001 (8).

---

## 2. Findings (new/refreshed, sorted by severity)

| # | Sev | Tool | Location | Finding | Suggested refinement |
|---|---|---|---|---|---|
| 1 | **HIGH** | static+radon | `app/server.py:303-357` (saves routes) | Save-bundle file I/O (`save_store.list_saves()`, `save_bundle`, `load_bundle` disk reads) executed **synchronously inside `async def` handlers** — blocks the event loop for every save/load/delete/list call. `_handle_upload` (:579, C18) / `_handle_generate` (:651, C17) do the same for image decode/detection. | Wrap `save_store.*` and `detect_grid`/`grid_to_thumbnail_png` calls in `await asyncio.to_thread(...)`; then `build_app`'s sync handlers need no `to_thread` but saves/upload/generate do. |
| 2 | **HIGH** | mypy | `app/saves.py:89,91,93` (+248,250) | `fresh_save_id`: `ts` is inferred as `str` (from `rpartition`), then `int(ts)` reassigned → 3 assignment/operator type errors; the collision-bump loop is also type-incoherent. | Annotate `ts: int` after the try/except and compute the base from `id_for_name(name)` pieces explicitly; drop the `rpartition` re-parse. |
| 3 | **MED** | static | `app/server.py:355-363` (`_with_entities`/maps detail) | Live `Entity`/`Player` dataclasses (or `None`-session) passed straight into `JSONResponse`; mypy flags `None`-session union access at server.py:358-363. FastAPI coerces dataclasses so it "works", but it's the only route not pinned to `to_dict()`. | Route through `grid.to_dict()`/`entity.to_dict()` (or `dataclasses.asdict` policy) and add an explicit 404/409 when session is `None`. |
| 4 | **MED** | ruff F811 | `tests/test_door_session.py:18-37` | Duplicated import block — 8 symbols imported twice (2nd block adds only `NO_ROUTE`) → 9× F811. | Merge into one import statement; add `I001` autofix to pre-commit. |
| 5 | **MED** | pylint/ruff | `app/models.py:554,592`; `app/session.py:604`; `app/server.py:448,525` | Cross-file dead code confirmed by pylint: `models.Session` (0 refs), `models.asdict` (0 call sites), `GameSession._send_coro` (0 calls), `server._map_doors` (redundant wrapper), `server._route_get_404` (0 calls). | Delete all five; `visibility._closed_doors`-style precompute already shows the intended shape. |
| 6 | **MED** | bandit B324 | `app/ws.py:96` | `hashlib.sha1(key + WS_MAGIC)` — **this is the RFC 6455 `Sec-WebSocket-Accept` computation, a false positive**, but bandit cannot know that. | Add `hashlib.sha1(..., usedforsecurity=False)` (stdlib ≥3.9) + a `# noqa: S324`-style comment citing RFC 6455. |
| 7 | **MED** | static | `app/server.py` (all routes, no middleware) | **Zero authentication**: any client can `POST /api/maps`, paint cells, save/delete bundles, send GM WS commands. No token/secret/credential handling anywhere in app/server.py, app/ws.py, app/main.py (grep-verified). | Introduce a single shared-secret (query/header or WS subprotocol param) check middleware for GM routes; document it in the API contract. |
| 8 | **LOW** | mypy | `app/session.py` (8 errs), `app/server.py:137`, `app/imaging.py:121,123`, `app/detection.py:182-183`, `app/ws.py:212` | Remaining 29 app/ mypy errors concentrate in `session.py` (`tuple[Any, str|None]` dict indices at :961/:1081, dict-entry int/str mismatches at :698) and an `int`/`list`-shaped annotation drift in `imaging.py`. | Fix `session.py` :698/:961/:1081 with explicit `tuple[str,str]` keys and `int`-cast dict entries; correct `imaging.py:121` `len()` on an int. |
| 9 | **LOW** | radon | `app/session.py:966` | `GameSession._on_safe_door` complexity **D(28)**; also `generate_grid` D(30), `read_frame` D(22), `_on_door` D(27) — unchanged from prior pass. | Extract per-entity door-permission checks into `_door_actionable_for(entity, door)` and return early on hostile. |
| 10 | **LOW** | pylint | `app/` (324 msgs, 9.27/10) | Dominant pylint noise: missing docstrings on private helpers + `too-many-branches` on the same functions as #9; no `fixme`/`dangerous-default-value` high-severity pylint hits in app/. | Raise `pylint --fail-under=9.5` only after docstrings land; keep as hygiene, not blocking. |

Notable non-findings (re-confirmed clean): no `to_thread`/`run_in_executor` anywhere in app/ (so the stale `to_thread` docstrings at server.py:249/282 are misleading in *both* directions); orjson still absent (decision stands); no global mutable-state leaks across test classes.

---

## 3. Prior findings (pass 1 / refactor-evaluation) — status in this tree

| # | Prior finding | Status @ e47fde1 | Location (current line) |
|---|---|---|---|
| 1 | Async handlers doing sync file I/O (saves routes / upload / generate) | **still-present** | `app/server.py:305` (saves_list), `:579` (upload), `:651` (generate) |
| 2 | Entity/Player dataclasses serialized in maps_detail route | **still-present** | `app/server.py:355-363` |
| 3 | Zero authentication | **still-present** | `app/server.py` (no auth middleware anywhere) |
| 4 | High complexity `build_app`/`_on_safe_door` | **still-present** | `app/server.py:145`, `app/session.py:966` (D28) |
| 5 | ~66 mypy errors | **still-present** (now precisely 29 app/ + 66 scripts+tests = 95 total) | `app/session.py:698,961,1081` etc. |
| 6 | Duplicate imports (F811) | **still-present** | `tests/test_door_session.py:18-37` |
| 7 | bandit false-positive SHA1 on ws.py | **still-present** | `app/ws.py:96` |

**All 7: still-present.** No refactor code exists in the tree (see §0), so nothing was fixed by a refactor.

---

## 4. Top-3 recommended refinements (priority order)

1. **Offload sync I/O out of the event loop** — wrap `save_store.list_saves/save_bundle/load_bundle/delete_save` and the upload/generate image pipeline in `asyncio.to_thread`. Highest user-visible impact (blocked loop on every GM save/load), lowest risk, and it finally makes the `to_thread` docstrings at server.py:249/282 true.
2. **Merge (or re-execute) the backend refactor with auth + dead-code removal in one pass** — the refactor only ever existed as docs (`e47fde1`, `7cee59f`); land the package split together with deleting the 5 dead symbols (§2.5), adding a shared-secret GM auth check (§2.7), and pinning every route to `to_dict()` serialization (§2.3). Re-running this static pass after the merge gives a meaningful diff.
3. **Clear the type/lint debt mechanically** — fix the 11 F811 duplicate imports, 13 F401 unused imports, 23 BLE001 blind-except, and the 29 app/ mypy errors (start with `session.py:698/961/1081` and `saves.py:89-93`); add `usedforsecurity=False` to the ws.py SHA1 to silence the bandit B324 false positive. All are small, mechanical, and verifiable by this same toolchain.
