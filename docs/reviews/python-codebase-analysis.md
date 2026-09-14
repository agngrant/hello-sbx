# LittleDungeons — Static Analysis of the Python Codebase

> **Anchor:** branch `feat/backend-refactor`, commit `e47fde1888419ace63541b4abbb642d2fb4809b1`
> ("docs(reviews): backend refactor evaluation — efficiency, readability, PyPI reuse").
> Worktree: `TODO.md` + `docker-agent.yaml` modified (pre-existing churn; analysis covers the
> *committed* tree, i.e. HEAD — these two files are docs/config, not Python).
> **Read-only:** no tracked file modified, no commits, no branch changes, running server
> (port 8000) untouched. This report is the sole new file and is left **uncommitted**.
> **Date:** 2026-09-08. **Scope:** all Python in `app/`, `scripts/`, `tests/` (38 files;
> `.venv`/`node_modules` excluded).

---

## 1. Method & tooling

Everything ran with `.venv/bin/python` (Python 3.14.4) from the repo root. Raw outputs in
`.logs/` (untracked).

| Tool | Version | Command (summary) | Raw output |
|---|---|---|---|
| **pylsp** (python-lsp-server) | 1.15.0 | Custom LSP-over-stdio client (`/tmp/lsp_batch.py`, reusing the working `/tmp/lsp_inspect.py` pattern) — `initialize` + `didOpen` all 38 files, harvested `publishDiagnostics`. Also a second run (`/tmp/lsp_pylint.py`) with `pylint.enabled=true`. | `.logs/lsp_results_batch.json`, `.logs/lsp_batch.out`, `.logs/lsp_results_batch_pylint.json`, `.logs/lsp_pylint.out` |
| **ruff** | 0.16.6 | `ruff check --output-format=concise app scripts tests` (+ `--statistics`; targeted C901/BLE001/S/F811/F401 selects; C901 with `--config lint.mccabe.max-complexity=15`) | `.logs/ruff_concise.log` |
| **mypy** | 2.3.1 | `mypy app scripts tests` | `.logs/mypy.log` |
| **bandit** | 1.9.4 | `bandit -q -r app scripts tests -x .venv -f csv -o .logs/bandit.csv` | `.logs/bandit.csv`, `.logs/bandit_summary.txt` |
| **radon** | 6.0.1 | `radon cc -s -n C` (cyclomatic complexity) + `radon mi -s` (maintainability index) | `.logs/radon_cc.log` |
| **manual** | — | line-level reads of hot paths, cross-file reference greps, prior-eval cross-check | below |

**pylsp note (relevance):** pylsp's default `publishDiagnostics` returned **0 across all
38 files** — its default linter set here is a pyflakes subset, and it is per-file (no
cross-file dead-code analysis). With `pylint.enabled=true` the server reported
`NO-PYLINT-CAP` (no pylint capability / no pylint in this venv), so 0 again. Treat the
**ruff pyflakes-core results (F\*) as the superset** of what pylsp's pyflakes would flag,
and use the grep + ruff F811/F841/F401 cross-references for the dead-code questions.
pylsp was still used as instructed and confirms the import graph/structure parses clean.

**Cross-check basis:** every prior finding in
`docs/reviews/backend-refactor-evaluation.md` (the 2026-09-08 refactor evaluation at
`feat/backend-refactor`) is re-verified against this commit. See §3.

---

## 2. Executive summary — top findings ranked

**NEW** (not in the prior evaluation):

| # | Sev | Finding | Location |
|---|---|---|---|
| N1 | **HIGH** | **Save-bundle file I/O runs blocking on the event loop** — all four `/api/saves*` REST handlers are `async def` and call synchronous filesystem ops (`os.listdir`+`open().read()`, `makedirs`/`open`/`fsync`/`os.replace`, `open().read()`, `os.remove`) with **no `to_thread`/`run_in_executor` anywhere in `app/`**. Confirms + generalizes prior A.6 (upload/generate) to the saves surface. The prior eval's A.5 rationale — "REST routes run in Starlette's threadpool" — is **wrong for `async def` handlers**; they run on the loop thread. | `app/server.py:303,313,385,418` → `app/saves.py:124–220,326–349,370–379` |
| N2 | MED | **`GET /api/maps/{id}` returns `entry["entities"]`/`entry["players"]` (dicts of live `Entity`/`Player` dataclasses) straight into `JSONResponse`** — `server.py:512-513`. FastAPI's `JSONResponse` (jsonable_encoder) serializes dataclasses so it won't crash, but (a) this wire path is the *only* one that relies on dataclass-field reflection instead of the pinned `to_dict()` policy (inconsistent with every other route), and (b) **it is provably always empty** — see N5. Prior A.9 flagged "can't serialize" — the actual condition is milder but still a latent trap + dead path. | `app/server.py:500-519` |
| N3 | MED | **Zero authentication / transport hardening on any endpoint.** No `Authorization`/token check on REST or WS; role is *client-asserted* (`{type:"join", role:"gm"}` — any client can self-declare GM and drive paint/doors/save/delete/upload). No CORS (good, restrictive). Acceptable for a local/peer app but is the single biggest *security posture* gap; worth an explicit owner decision + doc note. | `app/server.py` (all routes), `app/session.py:join` |
| N4 | MED | **Complexity hotspots** (cyclomatic complexity; radon, corroborated by ruff C901). Code `app/`: `build_app` (server.py:145, CC38), `GameSession._on_safe_door` (session.py:966, CC28), `Grid.__post_init__` (models.py:116, CC26), `_on_door` (session.py:869, CC27), `join` (session.py:204, CC25), `_on_use_map` (session.py:1099, CC24), `_on_move` (session.py:649, CC16). `app/session.py` maintainability index **C (5.85)** — the only module below B. `derive_visible`/LOS oracles are **re-implemented 5×** (e2e_proof.py:117, qa_explored_map.py:79, qa_doors.py:106, qa_safe_doors.py:83, test_visibility.py:82). | see locations |
| N5 | LOW | **`maps_registry`'s `"entities"`/`"players"` sub-dicts are never written after `_register_map`** (session keeps its own `self.entities`/`self.players`). So `maps_detail`'s `list(entry["entities"].values())` / `list(entry["players"].values())` are **dead code that always yield `[]`**, and the detail route's "entities" claim is vestigial (tests only assert `[]`). Confirms N2's path is dead. | `app/main.py:66-72`, `app/server.py:512-513`, `app/session.py:113` |
| N6 | LOW | **Blind `except` in app code** — `server.py:575` (`except Exception` in `_parse_json_body`, after the specific JSON/Unicode catches → unreachable catch-all), `server.py:822/826` (`except BaseException`/`Exception` + `except: pass` in the uvicorn test adapter — intentional, keep), `session.py:1150` (`except Exception` around registry read → sets `grid=None`, intentional fallback). Only `server.py:575` is a real (albeit harmless) smell; the rest are documented fail-safes. | `app/server.py`, `app/session.py` |
| N7 | LOW | **66 mypy errors (2.3.1, default strict-ish flags) across 17 files.** Real code items (app/): `session.py:424` (`payload["visibility"]` assigned `list[str]` into a payload mypy infers as `dict[str, Any]`-ish union — annotation on `payload` is too narrow), `session.py:642/693/698/961/1081` (`Any`/`tuple[Any,str|None]` from untyped `msg.get` + `find_path`'s `tuple`→`dict` coercion), `session.py:1042` (`dict(self.grid.doors)` where `doors: dict|None`), `server.py:137` (annotated `-> PlainTextResponse` returns `_error_json` → `JSONResponse`), `server.py:358-363` (`session._lock`/`session.grid` on `GameSession|None`). The rest are **test-harness noise**: `Player | None` attribute access without `assertIsNotNone` (test_session/door_session), and classmethod-injected `host`/`port`/`_n`/`decode_image` redefinitions the checker can't see (test_ws/test_api/test_detection). | `.logs/mypy.log` |
| N8 | LOW | **Dependency hygiene is clean but `pydantic` is pinned-but-never-imported** (confirmed: zero `import pydantic` in `app/`; it's a FastAPI transitive pin). No unused *top-level* pins, no version conflicts (requirements.txt: fastapi 0.141.1, pillow 12.3.0, pydantic 2.13.5, pytest 9.1.1, pytest-timeout 2.4.0, uvicorn 0.52.4, websockets 17.1). orjson confirmed still absent from the venv. | `requirements.txt`, prior C.7 |
| N9 | LOW | **Test hygiene:** unused imports (test_saves.py `asyncio`/`ENTITY_KINDS`/`Player`/`TEAMS`; test_ws.py `json`/`socket`), unused locals (test_imaging.py:356 `rowbytes`, test_saves.py:87 `save_id`, test_ws.py:299 `wg`, e2e_proof.py:810/853), 19 unsorted-import blocks, 75 printf-style `%` strings. `tests/test_door_session.py:18-37` has a **duplicated import block** (8 symbols imported twice; the 2nd adds only `NO_ROUTE`) — F811 ×9. 158 ruff findings in tests/, 77 in scripts/, 33 in app/. `derive_visible`/`oracle_visible` duplication (N4). Per-test `timeout=30` (pytest-timeout, method=thread) already mitigates the "slow/hung test" risk; no global mutable state leaks across test classes (each REST/WS class boots its own server via `setUpClass`). | `tests/`, `scripts/` |
| N10 | INFO | **Structurally interesting / intentional (do not "fix"):** (a) the **lazy `run_server` import** in `main.main` (main.py:~159) keeps the import graph acyclic (`server` imports `main` at module level; `main` imports `server` only inside `main()`); (b) **shared-grid-per-session** — `get_session`'s grid is `maps_registry[session_id]`'s *same object*, so REST paint and WS paint hit one grid (main.py:92-111); (c) **module-level mutable state** (`maps_registry`, `sessions`) in `main.py`; (d) `server.py` **re-declares** `BASE_DIR`/`STATIC_DIR` at :80-81 shadowing the `main` imports at :66-68 (F811 — harmless but noisy); (e) the `ThreadingHTTPServer`/`_UvicornThread` adapter + `_make_maps_detail_route` throwaway-probe-app hack are load-bearing for ~3.1k lines of boot-shaped test code (prior B.7, reconfirmed); (f) `Player.fog`/`Grid`/`session.fog` are vestigial booleans still on the wire (`"fog": self.fog`) since explored-map replaced it. | `app/main.py`, `app/server.py`, `app/session.py` |

**ALREADY-KNOWN** (cross-check vs `docs/reviews/backend-refactor-evaluation.md`): **all 7
carried findings remain CONFIRMED-STILL-THERE at this commit** (no refactor has landed —
`feat/backend-refactor` still sits at the docs-only evaluation commit). Details in §3.

---

## 3. Cross-check against the prior evaluation (`docs/reviews/backend-refactor-evaluation.md`)

| Prior finding | Location cited | Status @ `e47fde18` | Evidence (this pass) |
|---|---|---|---|
| **A.1** Broadcast hot path ≈97% per-viewer recomputation | session.py:376 `state_for` | **CONFIRMED-STILL-THERE** | `state_for` (session.py:376) still calls `grid.to_dict()`, `doors_for_wire()`, `safe_for_wire()`, `_awareness_for`, `_visibility_for`→`visible_cells` **per viewer, per broadcast**. **No revision/dirty marker, no wire-map cache, no memoization** anywhere in `session.py`/`models.py`/`grid.py` (grep for `cache/revision/lru/memo` → only unrelated comments). |
| **A.2** `_closed_doors` full-grid rescan per entity in `build_awareness` | awareness.py:219 | **CONFIRMED-STILL-THERE** | `build_awareness` (awareness.py:144, player loop at ~209-238) still calls `has_line_of_sight(grid, ...)` **without** a precomputed door set → `pathfinding.py:365` does `closed = _closed_doors(grid) if doors is None else doors` → full-grid scan **per entity, per snapshot**. (Note: prior doc said line 219; it is now within the 144-def body — same code, shifted by docstring growth.) The intended shape (`visibility.py:93` computes `_closed_doors` once and passes it) still shows the inconsistency. |
| **B.2** Dead code: `models.Session` | models.py:554 | **CONFIRMED-STILL-THERE** | `@dataclass class Session` at models.py:554 still defined. **Zero** instantiation/`models.Session`/`asdict` references across `app/`+`tests/`+`scripts/` (grep). Live object is `GameSession`. |
| **B.2** Dead code: `models.asdict` | models.py:592 | **CONFIRMED-STILL-THERE** | `def asdict` at models.py:592 still defined; **zero** call sites (its only textual sibling is `dataclasses.asdict` which is a different symbol). |
| **B.2** Dead code: `GameSession._send_coro` | session.py:604 | **CONFIRMED-STILL-THERE** | `def _send_coro` at session.py:604 still defined; **zero** call sites (send path uses `_senders`/`attach_async`). |
| **B.2** Dead code: `server._route_get_404` | server.py:525 | **CONFIRMED-STILL-THERE** | `def _route_get_404` at server.py:525 still defined; **zero** call sites (404s go through the `HTTPException` handler at server.py:137). |
| **B.2** Dead code: `_map_doors` one-line wrapper | server.py:~447 | **CONFIRMED-STILL-THERE** | `def _map_doors` at server.py:448 still defined; its only caller is `_with_doors` (server.py:472) which could call `grid.doors_for_wire()` directly. |
| **B.3** Stale `to_thread` docstrings | server.py:249/282, session.py:23/533 | **CONFIRMED-STILL-THERE** | server.py:248-249 still says session handling "runs in a worker thread off the event loop (`to_thread`)"; the inline comment at server.py:282-283 says the opposite ("NOT via to_thread… deliberately… Runs SYNCHRONOUSLY on the event-loop thread"). session.py:22-23 and :533-535 still carry the `to_thread` phrasing. **The code has genuinely never used `to_thread`/`run_in_executor`** (grep of `app/` → zero real usages; only docstring mentions). |
| **A.6** upload + generate run on the event loop (no `to_thread`) | server.py:577/672 | **CONFIRMED-STILL-THERE** (and see **N1** for the saves extension) | `_handle_upload` (server.py:579) still does `base64.b64decode` + `detect_grid` (server.py:620) + `grid_to_thumbnail_png` (server.py:643) inline in an `async def`, no offload. `_handle_generate` (server.py:651) same. No `asyncio.to_thread`/`run_in_executor` anywhere in `app/`. |
| **C.1** orjson measured and rejected | — | **CONFIRMED (no change)** | orjson still **not** in the venv (`pip list` clean); JSON still stdlib at all send sites with `separators=(",",":")`. Decision stands. |
| **C.7** pydantic pinned but never imported | — | **CONFIRMED (no change)** | Still pinned (requirements.txt) with **zero** `import pydantic` in `app/`. |
| **B.5** magic `min(20, …)` in `Player.from_dict` duplicates `AWARENESS_MAX` | models.py:538 | **CONFIRMED-STILL-THERE** | `radius = max(0, min(20, radius))` at models.py:538 still hard-codes `20`; the constants `AWARENESS_MIN`/`AWARENESS_MAX` (awareness.py:75-76, `0`/`20`) exist but are not imported into `models.py`. Docstring at models.py:527-530 correctly states the 0–20 intent. |
| **A.9** `GET /api/maps/{id}` returns `entry["entities"]` (Entity objects) into JSONResponse | server.py:~505 | **CONFIRMED, refined (see N2/N5)** | Still present at server.py:512-513. Prior "JSONResponse cannot serialize Entity" is **imprecise**: it's *FastAPI's* `JSONResponse` (jsonable_encoder) which *does* serialize dataclasses, so it won't crash. The real issues are (a) inconsistent with the `to_dict()` policy used everywhere else, and (b) the lists are **always empty** (N5), so it's a dead path. |
| **B.3** grid.py "paint will route through here" docstring | grid.py | **CONFIRMED-STILL-THERE** | grid.py:92-93 still says the GM paint action "will route through here"; paint actually writes `grid.cells[y][x]` directly at server.py:755 and session.py:948 (`set_cell` is test-only). |

**Nothing from the prior evaluation is RESOLVED at this commit** — the branch still
contains only the evaluation document; no refactor batch has been implemented. This is as
expected (owner deferral pending GO).

---

## 4. New findings — detail

### N1 (HIGH) — Save-bundle file I/O blocks the event loop
All four saves REST routes are `async def` yet perform synchronous filesystem work with no
offload (there is **no `to_thread`/`run_in_executor` in the entire `app/` tree**):

* `saves_list` (server.py:303) → `save_store.list_saves()` (saves.py:124) → `os.listdir`
  (saves.py:136) + per-file `open(...).read()` + `json.loads` (saves.py:142-149).
* `saves_create` (server.py:313) → `save_store.save_bundle` (saves.py:181) →
  `os.makedirs` (saves.py:212) + `open(tmp,"w")` + `f.write` + **`os.fsync`** (saves.py:215-219)
  + `os.replace` (saves.py:220). **`fsync` is the worst offender** — a real disk-flush
  syscall on the loop thread.
* `saves_load` (server.py:385) → `save_store.load_bundle` (saves.py:326) → `open(...).read()`
  (saves.py:341-342) + full bundle validation (saves.py:349-…).
* `saves_delete` (server.py:418) → `save_store.delete_save` (saves.py:370) → `os.remove`
  (saves.py:379).

Impact: any concurrent WS broadcast/echo is stalled for the duration of the disk op
(worst case = `fsync`). On a slow/buffered FS this is the same class of stall prior A.6
documented for the 32 MB upload, and it is **not** mitigated by the "REST runs in the
threadpool" belief — that only applies to *sync `def`* ASGI handlers; these are `async def`.
**Fix (low risk, zero wire change):** wrap the `save_store.*` call bodies in
`await asyncio.to_thread(...)` in each of the four handlers (or make the four routes sync
`def`). This should be batched with prior Batch 3 (off-loop upload/generate).

### N2 (MED) — `maps_detail` dataclass-into-JSONResponse (refined A.9)
`server.py:500-519` builds `{"entities": list(entry["entities"].values()), "players":
list(entry["players"].values())}` and hands it to `JSONResponse`. Because this is
FastAPI's `JSONResponse` (jsonable_encoder), dataclass instances *are* serialized — so the
prior "cannot serialize / would crash" framing is too strong. But it is still the **one**
route whose entity/player wire shape is produced by dataclass-field reflection rather than
the pinned `Entity.to_dict()`/`Player.to_dict()` policy (which e.g. *excludes* the
`Player.rebound` flag — models.py:516-524 — and normalizes `awareness_radius`). If
`entities`/`players` were ever non-empty (see N5: they are not), this route would emit a
wire shape inconsistent with every other route. **Recommendation:** route through
`to_dict()` (or delete the fields, N5).

### N3 (MED) — No authentication / transport hardening
No endpoint checks credentials. Role is **client-asserted** at join
(`{type:"join", name, role:"gm"}`) — any WS client that connects can self-declare GM and
drive paint, door/safe-door state machines, entity GM tools, save/load/delete, and
upload/generate. `POST /api/maps/*` and `/api/saves*` require no join at all (saves only
gate on *whether a default GM session exists*, server.py:313-324 / `_save_role_state`).
No CORS is configured (restrictive — a positive). For a local/peer game this may be
acceptable, but it is the single largest *posture* gap and deserves an explicit owner
decision (LAN-only bind vs. a shared secret/ticket on the WS handshake). `docker-agent.yaml`
already documents the "env-var-names only, no secret values" convention, so a ticket/token
is easy to add later.

### N4 (MED) — Complexity hotspots & duplication
Radon cyclomatic complexity (CC), `app/` only (scripts/tests excluded from the "sane" bar):

| Function | File:Line | CC |
|---|---|---|
| `build_app` | app/server.py:145 | 38 |
| `GameSession._on_safe_door` | app/session.py:966 | 28 |
| `GameSession._on_door` | app/session.py:869 | 27 |
| `Grid.__post_init__` | app/models.py:116 | 26 |
| `GameSession.join` | app/session.py:204 | 25 |
| `GameSession._on_use_map` | app/session.py:1099 | 24 |
| `GameSession.handle_message` | app/session.py:527 | 20 |
| `_validated_entities` | app/saves.py:267 | 19 |
| `detect_grid` | app/detection.py:101 | 18 |
| `_handle_upload` | app/server.py:579 | 18 |
| `_handle_generate` | app/server.py:651 | 17 |
| `GameSession._on_move` | app/session.py:649 | 16 |

`radon mi`: `app/session.py` = **C (5.85)** — the only module below B (everything else
A). The two door state machines (`_on_door`/`_on_safe_door`, CC 27/28) are near-identical
in shape (prior B.1/B.4) — a shared transition table + guard list would halve both.
**Duplication:** the LOS/`derive_visible`/`oracle_visible` oracle is re-implemented in **5
places** (e2e_proof.py:117, qa_explored_map.py:79, qa_doors.py:106, qa_safe_doors.py:83,
test_visibility.py:82) — these are independent oracles by design (good), but they must be
kept in lock-step; a shared `tests/`-importable helper would reduce drift risk.
(scripts/ has `main()` CCs up to 191 in e2e_proof.py — a monolithic proof script, expected,
not a "sane CC" target.)

### N5 (LOW) — `maps_registry["entities"/"players"]` never populated (dead path)
`_register_map` (main.py:66-72) seeds `"entities": {}` / `"players": {}` and **nothing
ever writes to them** (grep: zero `entry["entities"] =` / `entry["players"] =` in `app/`;
`GameSession` keeps its own `self.entities`/`self.players` and never syncs them back to the
registry). Therefore `maps_detail`'s `list(entry["entities"].values())` / `list(entry["players"].values())`
(server.py:512-513) are **always `[]`** — dead code, and the reason the "entities" part of
N2 can never actually fire. `test_api.py:117` only ever asserts `data["entities"] == []`.
**Recommendation:** either populate the registry on join/leave (if the detail route is
meant to be live) or drop the two fields + the seeding.

### N6 (LOW) — Blind `except` in `app/`
`app/server.py:575` (`except Exception` in `_parse_json_body`, after
`(UnicodeDecodeError, json.JSONDecodeError)` → unreachable catch-all that returns the same
400; harmless but misleading), `app/server.py:822/826` (`except BaseException` +
`except Exception: pass` in `_UvicornThread` — intentional test-adapter error surfacing,
keep), `app/session.py:1150` (`except Exception` around the `maps_registry` read in
`_on_use_map` → sets `grid=None`, documented fallback, keep). Only `server.py:575` is a
genuine (cosmetic) smell.

### N7 (LOW) — mypy: 66 errors in 17 files
mypy 2.3.1 (default settings). App-code items worth acting on:

* `app/session.py:424` — `payload["visibility"] = self._visibility_for(...)`; `payload` is
  built as a dict literal whose inferred value-type union doesn't include the list
  (annotation-tightening candidate).
* `app/session.py:642` (`join` arg typed `Any|None` vs `str`), `:693/698` (`find_path`
  `list[tuple]|None` coerced into a `dict` comprehension), `:961/:1081` (tuple-index of
  transition table with `str|None`), `:1042` (`dict(self.grid.doors)` where
  `doors: dict|None`). All are consequences of loosely-typed `msg.get(...)` + the
  `Grid.doors: dict[str,str] | None` field — safe at runtime (validated upstream) but the
  types lie.
* `app/server.py:137` — `not_found_handler` annotated `-> PlainTextResponse` but returns
  `_error_json(...)` (a `JSONResponse`). Fix the annotation.
* `app/server.py:358-363` — `session._lock`/`session.grid`/`session.entities`/`session.players`
  accessed on `GameSession | None` in `saves_create` (the `"none"` case is checked via
  `_save_role_state()` but mypy can't see the correlation).

The remaining ~40 are **test-harness noise**: `Player | None` attribute access without a
narrowing assert (test_session.py, test_door_session.py), classmethod-injected
`host`/`port`/`_n` the checker can't model (test_ws.py:88-92, test_api.py:51), and
`decode_image` redefinition (test_detection.py:287). Recommend a `mypy.ini`/pyproject
section that excludes `tests/` (or `--ignore-missing-imports` + per-file overrides) so the
*app* signal is readable; then fix the ~9 app-code items.

### N8 (LOW) — Dependency hygiene
`requirements.txt` (7 pins) is clean: no unused *top-level* pins, no version conflicts,
all resolve on Python 3.14.4. `pydantic==2.13.5` is **pinned but never imported by app
code** (FastAPI transitive) — confirmed (prior C.7). orjson absent (confirmed). No
`*.lock`, no `pyproject`. The only action is the optional pydantic-SaveBundle decision
(prior C.7, owner sign-off) — otherwise no dependency work.

### N9 (LOW) — Test hygiene
(Counts: 158 ruff findings in `tests/`, 77 in `scripts/`, 33 in `app/`.)
* **Unused imports:** test_saves.py:23 `asyncio`, :31 `ENTITY_KINDS`/`Player`/`TEAMS`;
  test_ws.py:19 `json`, :21 `socket`; **app/server.py:34** `asyncio` (unused because
  `to_thread` was never adopted — a *symptom* of A.6/N1), :60 `Receive`/`Scope`/`Send`,
  :78 `GameSession`.
* **Unused locals:** test_imaging.py:356 `rowbytes`, test_saves.py:87 `save_id`,
  test_ws.py:299 `wg`, e2e_proof.py:810 `final_s` / :853 `pw11`, visibility.py:147 `row`.
* **Duplicated import block:** `tests/test_door_session.py:18-37` imports
  `Grid`/`GameSession`/`FakeConn`/`attach`/`drive`/`make_grid`/`oracle_visible`/`s_cells`
  **twice** (the second block adds only `NO_ROUTE`) — F811 ×9, delete the 8 redundant
  names.
* **`%`-format strings:** 75 sites (UP031) — mostly `print`/`assert` messages in
  tests/scripts; cosmetic.
* **Shared state:** each REST/WS test class boots its own server via `setUpClass`
  (test_api.py:27/181/332; test_ws.py) and WS tests use per-connection `?session=`
  isolation (per team convention) — no cross-class mutation leak observed. `pytest.ini`
  sets `timeout=30, timeout_method=thread` (the global per-test guard from
  simplification-review row 7) — the "slow/hung test" risk is already mitigated. The
  heaviest suites (test_session.py 1868 lines, test_door_session.py 1210, test_ws.py 1101,
  test_api.py 985) pin behavior to the *string* level, so any refactor is high-blast-radius
  (reconfirmed prior "Inventory / Tests as refactoring context").

### N10 (INFO) — Structurally interesting / intentional (do not "fix")
* **Lazy `run_server` import** in `app/main.py` `main()` (~line 159) — keeps the import
  graph acyclic: `server.py` imports `app.main` at module level (registries, `get_session`,
  map-id helpers); `main.py` imports `server.run_server` *only inside* `main()`. Correct
  and documented; reconfirmed.
* **Shared-grid-per-session** — `get_session` (main.py:92-111) uses
  `maps_registry[session_id]`'s **same `Grid` object** when it exists, so REST paint
  (server.py:755) and WS paint (session.py) mutate one grid. This is what makes
  A.1's revision/dirty-marker approach viable (centralized mutation points).
* **Module-level mutable state** — `maps_registry` (main.py:63) and `sessions` (main.py:88)
  are process-global; `_sessions_lock` guards `sessions`. Fine for a single-process server.
* **`BASE_DIR`/`STATIC_DIR` re-declared** in server.py:80-81 (F811) shadowing the imported
  names from main.py:66-68 — harmless, drop one of the two.
* **Load-bearing test-compat debt** — `ThreadingHTTPServer`/`_UvicornThread`
  (server.py:~779/873) and the `_make_maps_detail_route` throwaway-probe-app
  (server.py:481-523) exist purely so `test_api.py`/`test_ws.py`/`e2e_proof.py` boot the
  server exactly like the old stdlib server (prior B.7, reconfirmed — leave alone).
* **Vestigial `fog`** — `session.fog` / `Grid` still carry a `fog` boolean that is
  serialized on the wire (`"fog": self.fog`, session.py:423) but no longer gates anything
  (explored-map replaced it; see session.py:1092 comment "it no longer gates player
  visibility"). Candidate for a future wire-shape cleanup (owner sign-off — wire-visible).

---

## 5. Tool outputs (reference)

* **pylsp** (1.15.0): 38/38 files opened, `publishDiagnostics` = **0** total (default
  pyflakes-subset; no cross-file analysis). pylint-enabled run: `NO-PYLINT-CAP`, 0.
  Raw: `.logs/lsp_results_batch.json`, `.logs/lsp_results_batch_pylint.json`.
* **ruff** (0.16.6): **268** total — 33 in `app/`, 77 in `scripts/`, 158 in `tests/`.
  68 auto-fixable. App-code rule mix: 5 F401 (unused import), 4 BLE001 (blind except),
  4 UP037 (quoted annotation), 4 TRY004, 3 SIM103, 3 I001, 3 RUF100, 2 SIM102, 2 F811
  (BASE_DIR/STATIC_DIR), 1 UP035, 1 S110, 1 F841 (`visibility.py:147 row`).
  C901 >15 in `app/`: `build_app` 38, `_on_safe_door` 19, `handle_message` 20,
  `_on_use_map` 18, `generate_grid` 17, `_on_door` 15.
* **mypy** (2.3.1): **66** errors in 17 files (checked 38). App-code: session.py 8,
  server.py 6; rest test-harness (see N7).
* **bandit** (1.9.4): **32** findings — 1 HIGH (app/ws.py:96 `hashlib.sha1` in the
  **test-only** RFC 6455 client, `usedforsecurity` not set — the WS handshake *must* be
  SHA1 per RFC 6455 §4.2.2, so this is a **false positive for the protocol**, mark
  `nosec`/document), 5 MEDIUM (all in `scripts/`: B310 url-scheme ×4 on `http://`/`file:`
  probe URLs, B108 temp-dir prefix in test_api.py:614), 26 LOW (assert_used ×3 in
  generation.py + qa, B311 stdlib `random.Random(seed)` ×2 in generation.py:79 —
  **intentional** deterministic map seed, not crypto, `nosec`, "hardcoded password '✓'"
  ×4 = bandit misreading the `✓` checkmark status string in QA scripts).
* **radon** (6.0.1): CC table in N4; `radon mi` — only `app/session.py` below B (C 5.85).

---

## 6. What I'd do next (priority-ordered, read-only recommendations — no code changed)

1. **Batch the event-loop offload (prior A.6 + N1, one change).**
   `await asyncio.to_thread(...)` around: (a) `base64.b64decode`+`detect_grid`+
   `grid_to_thumbnail_png` in `_handle_upload`; (b) `generate_grid`+thumbnail in
   `_handle_generate`; (c) the four `save_store.*` calls in the saves routes (the
   `fsync` in `save_bundle` is the sleeper). Also **delete the now-unused `import asyncio`
   contradiction** — it becomes *used* by this batch (resolves server.py:34 F401).
   Zero wire change; gate: `e2e_proof.py` + full pytest.
2. **Land prior Batch 1 (dead code + docstring fixes, ~1-2 h, near-zero risk):** delete
   `models.Session`, `models.asdict`, `GameSession._send_coro`, `server._route_get_404`;
   inline `_map_doors`; fix the 4 stale `to_thread` docstrings (server.py:249/282,
   session.py:23/533) + grid.py:92-93 "will route through here"; replace the `min(20, …)`
   magic (models.py:538) with `AWARENESS_MIN/MAX`. Add 2-3 `from_dict`-clamp tests.
3. **Add a mypy gate scoped to `app/`** (pyproject/mypy.ini; `--exclude tests scripts`),
   then fix the ~9 app-code items (server.py:137 return annotation is the quick one;
   `Grid.doors: dict|None` → annotate the transition-table indexes). This converts a
   66-error haystack into a small, trackable list and prevents N2/N5-class drift.
4. **Owner decision on N3 (auth).** Document the trust model (LAN/peer) or add a WS
   handshake ticket + a shared secret for the REST saves/upload surface. Highest-leverage
   *security* item.
5. **Resolve the `maps_detail` entity/player path (N2+N5):** either wire the registry
   sub-dicts live or drop the two fields + the seeding in `_register_map`. Removes a
   dead, policy-inconsistent wire path.
6. **Complexity (N4), when touched:** (a) extract a shared door/safe-door transition
   table + guard list (`_on_door`/`_on_safe_door`, CC 27/28); (b) split
   `Grid.__post_init__`'s two near-identical door/safe validation blocks into a shared
   `_validate_door_map` (prior B.4); (c) add a shared LOS oracle helper for the 5×
   `derive_visible`/`oracle_visible` copies. Do **not** split the `GameSession` class
   itself (prior B.1 — the RLock boundary must stay one).
7. **Test hygiene (N9), low priority:** delete the duplicated `test_door_session.py:18-37`
   import block (F811 ×9) and the dead unused imports; optionally add a `ruff check`
   step to the suite so the 268 stay from growing.
8. **`nosec` the bandit protocol-required items** (ws.py:96 SHA1, generation.py:79
   `random.Random`) with a one-line rationale comment so future scans don't re-flag.

**Non-goals reconfirmed:** no orjson, no A*/pathfinding lib, no filelock/jsonschema on
saves, no typer/click/loguru, no save-file format change, no WS-transport change, no
rewrite of the `ThreadingHTTPServer` adapter / `_make_maps_detail_route` probe (all prior
Section D non-goals still hold).

---
*Report left uncommitted per task constraint. All raw tool outputs in `.logs/` (untracked).*
