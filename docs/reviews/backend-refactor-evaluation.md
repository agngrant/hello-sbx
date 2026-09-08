# LittleDungeons — Backend Refactor Evaluation

> **Status: EVALUATION ONLY.** No code changed by this document.
> **Baseline:** `feat/backend-refactor` cut from `main` @ `130efc4c` (2026-09-08).
> **Safety net:** full suite green at review time — pytest exit 0 (793 tests,
> run via `.venv/bin/python -m pytest tests/`), plus the standing gates
> (unittest discover 793, frontend harness 233, `scripts/e2e_proof.py` all-✓).
> **Method:** full read of every backend module, line-level dead-code and
> duplication grep, live micro-benchmarks of the hot paths (60×60
> generated map, 7 viewers, dense doors — same worst case the perf tests
> pin), and PyPI verification via `pip index versions` /
> `pip download --no-deps` (sandbox egress: pypi.org only). All benchmark
> numbers below were measured on this machine with `.venv/bin/python`
> (Python 3.14.4); treat them as order-of-magnitude, not absolutes.

---

## Executive summary — top 5

1. **Kill the per-viewer recomputation on the broadcast hot path (internals,
   low risk, biggest payoff).** A single `state_for` costs ~8.6 ms on a
   60×60 grid with dense doors (measured); a 7-viewer broadcast is ~44 ms,
   and **~97 % of that is recomputation, ~2 % is JSON serialization**.
   `visible_cells` (7.7 ms/player), `doors_for_wire` (0.07 ms ×
   every call), and the full-cell `Grid.to_dict` copy (session.py:403)
   re-run for *every viewer on every broadcast* even though they are pure
   functions of (grid, positions), none of which changed. Cache the grid's
   wire form (with a revision/dirty marker from the paint/door
   mutation points) and memoize `visible_cells`/`build_awareness` per
   (grid-rev, pos) / (grid-rev, viewer, positions-fingerprint). Broadcast
   cost drops to near-zero for the steady state (no moves, one door
   toggle) and the pinned 500 ms perf budget (tests/test_door_session.py
   `test_full_recompute_within_budget`) gains ~10× headroom.
2. **Hoist `_closed_doors(grid)` out of `build_awareness` (internals, low
   risk, trivial payoff + clarity).** awareness.py:219 calls
   `has_line_of_sight(grid, …)` with `doors=None`, which *re-derives the
   closed-door frozenset by scanning the whole grid* (pathfinding.py:365)
   for **every entity, per snapshot**. The session already holds the
   locked state; compute the set once per handler call and pass it down.
3. **Delete the dead code (internals, near-zero risk).**
   `models.Session` (models.py:554 — never instantiated anywhere),
   `models.asdict` (models.py:592 — never called),
   `GameSession._send_coro` (session.py:604 — never called),
   `server._route_get_404` (server.py:525 — never called), and the
   one-line `_map_doors` indirection (server.py:~447, only caller is
   `_with_doors`, which could call `grid.doors_for_wire()` directly).
4. **Do NOT adopt orjson (measured and rejected).** JSON serialization of
   7 player snapshots is ~0.64 ms; orjson 3.12.0 is 4.3× faster
   (~0.15 ms) — a sub-millisecond saving against a 44 ms hot path, while
   the unicode-escape semantics differ from stdlib `json` (orjson emits
   raw UTF-8; stdlib with `ensure_ascii=True` emits `\uXXXX`) — a
   *wire-visible* change for non-ASCII map/entity names, i.e. owner
   sign-off class for a 0.5 ms gain. The win in row 1 is 100× bigger.
5. **The PyPI bar is high in this codebase, and mostly already met.**
   The previous review (docs/reviews/simplification-review.md) already
   landed Pillow / FastAPI+uvicorn / websockets-native RFC 6455 / pytest.
   Of 9 fresh candidates re-verified today: **0 adopted, 2 considered
   (numpy for the image-preprocessing math; pydantic as an *optional*
   save-schema layer — and pydantic is already in requirements.txt as a
   FastAPI transitive pin), 7 rejected**. The remaining hand-rolled
   pieces (A* + Bresenham LOS, BSP dungeon gen, the custom 3×3-majority /
   Otsu plateaus, the raw-socket WS test client) are domain-specific and
   spec-pinned by the 793-test suite; every off-the-shelf replacement was
   checked and fails on behavior, maintenance, or payoff.

---

## Inventory

| Module | Lines | Role | Condition |
|---|---|---|---|
| `app/server.py` | 979 | FastAPI app: REST routes (maps/saves/paint/upload/generate), `/ws` endpoint, legacy 404/error shapes, `ThreadingHTTPServer`-shaped test adapter, `run_server` CLI. | **Good, slightly overwired.** Route bodies are long but each is a faithful port of pinned behavior with excellent comments. Carries test-compat debt (adapter + `_make_maps_detail_route` probe-app hack) that is *load-bearing for the suite*. 2 dead functions. 3 stale `to_thread` docstrings. |
| `app/session.py` | 1217 | Authoritative per-session state: joins, movement, GM tools, door + safe-door state machines, per-viewer snapshots, broadcast fan-out, explored-map memory. | **Good but a god-object** (36 methods, 6+ responsibilities). Heavy but *intentional* docstrings; the door/safe-door machines duplicate each other's shape. Dead `_send_coro`. Hot-path recompute (top-5 #1/#2). |
| `app/models.py` | 596 | `Grid`/`Entity`/`Player`/`Session` dataclasses, cell/door/safe-door vocabularies, door/safe accessors, wire policies (`doors_for_wire`, `safe_for_wire`). | **Good.** Long `__post_init__` does two near-identical validation blocks (doors vs safe). Dead `Session` dataclass + `asdict` helper. One magic `20` (Player.from_dict clamp duplicates awareness bounds). |
| `app/main.py` | 173 | Process state: `maps_registry`, `sessions`, get-or-create session, map-id slug helpers, CLI (`argparse`). | **Good.** The module doubles as a state holder that `server.py` imports *and* a CLI entry; this drives the lazy-import dance noted in B. |
| `app/grid.py` | 116 | Sample map literal + `in_bounds`/`get_cell`/`set_cell` + `to_dict`/`from_dict` thin wrappers around `Grid`. | **Good, thin.** The two `Grid` wrappers are never used (call sites use `grid.to_dict()` / `Grid.from_dict()` directly); `get_cell`/`set_cell` are test-fixture helpers only. |
| `app/pathfinding.py` | 379 | 8-dir A* (octile, deterministic), no-corner-cut step rule, door/safe-door-aware blocked sets, Bresenham LOS with corner-cut sight rule. | **Excellent — keep verbatim.** Core domain logic; spec-invariant-pinned (I1–I7, AC15). A 2024+ PyPI audit found no maintained equivalent that covers team-aware blocked sets + corner-cut LOS (see C). |
| `app/visibility.py` | 158 | `visible_cells` (S1/S2 wall-face sight) + `build_visibility_mask` (S/E/H rows) for the explored-map feature. | **Good, pure.** The per-call cost (7.7 ms @ 60×60) is the main efficiency item; the algorithm (per-cell Bresenham) is fine, memoization is the lever. |
| `app/detection.py` | 172 | Upload pipeline: decode → gray → resize → Otsu → 3×3 majority → wall/floor → doorway heuristic → auto-invert; thumbnail renderer. | **Good, pure.** Orchestration is clear; the math lives in `imaging.py`. Synchronous CPU work runs on the event loop (A.6). |
| `app/imaging.py` | 245 | Pillow wrapper (decode/encode/nearest-resize) + kept-verbatim `to_gray` (BT.601), `otsu_threshold` (plateau-mid), `median3x3` (center-wins-tie majority). | **Good.** Docstring correctly explains why the 3 ops stay hand-rolled (Pillow's `convert("L")` floors; `MedianFilter(3)` is a plain median, not the pinned center-wins rule). `decode_image` builds per-pixel tuples (slow, but upload-only). |
| `app/saves.py` | 379 | Save bundles: atomic write (tmp+`os.replace`+fsync), full-bundle validation on load, dir-scan list with corrupt tolerance, id validation. | **Excellent.** Single-writer process + atomic replace means no external lock is needed (filelock rejected, C). Manual validation produces the exact error strings the REST layer maps to 404s. |
| `app/ws.py` | 298 | **Test-only** RFC 6455 client: handshake, frame codec, masking, ping/close — used by `tests/wsclient.py` to drive the real server over raw TCP. | **Keep.** Its docstring correctly says app code must not import it. The server side of RFC 6455 is uvicorn's. (One line uses `json.dumps` for the test client — leave it.) |
| `requirements.txt` | 7 pins | fastapi, pillow, pydantic, pytest, pytest-timeout, uvicorn, websockets. | **Clean.** Note: `pydantic` is pinned but **never imported** by app code (FastAPI pulls it in anyway) — see C.7. |

**Tests as refactoring context:** ~22k lines total; `tests/test_session.py`
(1868), `test_door_session.py` (1210), `test_ws.py` (1101), `test_api.py`
(985) pin behavior to the *string level* (exact error messages, exact wire
keys). `tests/test_ws.py` exercises the real server over raw sockets with a
hand-rolled codec, so the WS *transport* surface (101 response, frame
encoding) is frozen. `tests/test_visibility.py` pins a 12-row S/E/H literal
(W4). Anything touching payload bytes, error strings, or save-file JSON is
high-risk; anything inside a pure function's internals is low-risk.

---

## Section A — Efficiency findings

Benchmarks: 60×60 generated map (seed 1), half the doorway cells locked,
7 joined viewers (1 GM + 6 players), single thread, 50–200 iterations:

| Operation | Measured | Share of 7-viewer broadcast |
|---|---|---|
| `visible_cells` (1 player, dense doors) | **7.7 ms** | ~55 % |
| `build_visibility_mask` | 7.9 ms (incl. its own `visible_cells` when not passed) | — |
| `state_for` (1 player) | **8.6 ms** | — |
| `json.dumps` of one snapshot (34.9 KB) | 0.09 ms | — |
| Full 7-viewer broadcast (compute + dumps) | **44.5 ms** | 100 % |
| `doors_for_wire` (1 call) | 0.07 ms | ~2 % (× 7 viewers) |
| `Grid.to_dict` cell copy inside `state_for` | ~0.3 ms (derived) | ~5 % |
| `build_awareness` (6 entities) | 0.32 ms | ~2 % (but see A.2 for the hidden cost) |
| `find_path` 60×60 | 0.15 ms | negligible |
| `_closed_doors` (1 full-grid scan) | 0.06 ms | — |

**A.1 — Per-viewer snapshot recomputation is the hot path. (Payoff: HIGH,
Risk: LOW — internals only.)**
`GameSession.state_for` (session.py:376) recomputes, per viewer, per
broadcast: `grid.to_dict()` (full cell-matrix copy, session.py:403),
`doors_for_wire()` (full cell scan), `safe_for_wire()`, `build_awareness`
(LOS per entity), and `_visibility_for` → `visible_cells` (full grid ×
Bresenham). The payload differs per viewer **only** in `awareness`,
`visibility`, `you_entity`, and (GM vs player) `entities` — the `map`
object is identical for all 7 viewers. Suggested approach:

* Add a monotonically increasing `Grid` revision/dirty flag; bump it in the
  three mutation choke points (`set_cell` paths, `sync_doors_after_cell_set`,
  door/safe state setters) — they are already centralized by design.
* Cache the wire map object (`{name,width,height,cells-image,doors,safe}`)
  per grid, invalidated by revision; all viewers of one broadcast share one
  cached dict (sent 7× as serialized bytes — fine, `json.dumps` is cheap).
* Memoize `visible_cells` in a small per-session dict keyed by
  `(grid_rev, pos)` — players rarely change cell, and the same position is
  asked for on welcome + every broadcast. Same trick for `build_awareness`
  keyed by `(grid_rev, viewer, frozenset of (entity_id→pos) pairs)`.
* Expected effect: first snapshot after a mutation unchanged; **every
  subsequent unchanged-position snapshot ≈ 0.5 ms** (dict assembly +
  awareness only). The 500 ms budget in `test_full_recompute_within_budget`
  (6 × 8.6 ms today ≈ 52 ms) gains ~10× headroom for future features.
* Caveat: the mask (`build_visibility_mask`) still costs ~0.2 ms even with a
  precomputed visible set (pure row construction) — negligible.

**A.2 — `build_awareness` re-derives the closed-door set per entity.
(Payoff: MEDIUM (clarity + ≤0.4 ms @ 60×60), Risk: LOW.)**
`build_awareness` (awareness.py:219) calls
`has_line_of_sight(grid, …, doors=None)`; with `doors=None`,
`has_line_of_sight` (pathfinding.py:365) **scans the entire grid to build
`_closed_doors`** for every entity, per snapshot. The signature already
supports passing a precomputed set. Suggested approach: session computes
the blocked set once per handler invocation (it changes only on door
mutations, so it could ride the A.1 cache) and pass it to both
`build_awareness` and `visible_cells` (the latter already does this —
visibility.py:100 — a nice inconsistency that shows the intended shape).

**A.3 — Broadcast serialization: fine; do not micro-optimize.
(Payoff: LOW, Risk: N/A.)**
Measured: 7 × `json.dumps` ≈ 0.64 ms — ~2 % of the broadcast. orjson is
4.3× faster (0.15 ms) but saves 0.5 ms; see C.1 (rejected on wire-safety
grounds, not speed). Compact separators (`separators=(",", ":")`) are
already used at all send sites.

**A.4 — `find_path`: no change. (Verified: 0.15 ms @ 60×60; pinned
budget 50 ms.)** The homegrown A* (pathfinding.py:179) is deterministic
(counter tie-break), team-aware, and spec-pinned (I7, AC15). Optional
micro-optimization if ever needed: encode cells as `y*w+x` ints for
`g_score`/`closed` keys. Not worth the readability cost at current sizes
(gen/upload caps: 60×60).

**A.5 — Save I/O: fine. (Verified by inspection.)** Single-writer process,
atomic tmp+`os.replace`+fsync (saves.py:181–221), whole-bundle validation on
read. Files are ≤ tens of KB (60×60 cells + ≤ dozens of entities). No
blocking concern (REST routes run in Starlette's threadpool); no filelock
needed (C.6).

**A.6 — Synchronous CPU/IO on the event loop (upload + generate).
(Payoff: MEDIUM for robustness, Risk: LOW.)** `_handle_upload`
(server.py:577) runs `base64.b64decode` (up to ~24 MB of binary) +
`detect_grid` (Pillow decode + full pixel pipeline) + `grid_to_thumbnail_png`
*inline on the event loop* (async def, no `to_thread`). A 32 MB upload
stalls every WS connection (including broadcasts) for the pipeline's
duration. `_handle_generate` (server.py:672) is similar but cheaper (no
image decode; 60×60 BSP + thumbnail ≈ tens of ms). Suggested approach:
`await asyncio.to_thread(...)` around the decode/detect/thumbnail block —
purely internal, zero wire change. (Note: the WS message path is
deliberately inline for a documented reason — loop needed for broadcast
scheduling; keep it inline, A.1 is its lever instead.)

**A.7 — Small O(n) scans (cosmetic at current scale, cheap to fix if
touched).** `player_for_sock` (session.py:191) linear-scans `_socks` for
every message (fine: ≤ 7 entries); `_any_entity_at` (session.py:1150)
scans all entities per door close/mark (≤ dozens); `_find_free_floor`
scans the grid (≤ 3600 cells, join/use_map only). An
`{pos: entity_id}` index maintained on move/place/delete is a 10-line
addition but touches the mutation choke points — bundle with A.1 or
skip.

**A.8 — `encode_png`/`decode_image` pixel-list churn (upload-only, skip).**
`decode_image` builds ~`w*h` per-pixel tuples; `encode_png` builds a
flat list for `putdata`. On 60×60 thumbnails and ≤-a-few-MB uploads this
is single-digit ms. Pillow array APIs would shave it; not worth the
change against pinned decode behavior (interlace/BMP-depth rejections).

**A.9 — No N+1 / redundant I/O patterns in marshaling.** The one true
redundancy is A.1's per-viewer recompute; REST map detail (`GET
/api/maps/{id}`) returns `entry["entities"]` (a **dict of Entity
objects**, server.py:~505) straight into `JSONResponse` — Starlette's
`JSONResponse` cannot serialize `Entity` dataclasses and this route's
response body is pinned by `test_api.py`; leave as-is but *note* it as a
latent trap for anyone refactoring the body assembly.

---

## Section B — Readability / maintainability

**B.1 — `session.py` is a god-object (1217 lines, 36 methods).**
Joins, permissions, movement, 7 GM tools, *two* near-identical door state
machines (`_on_door` at :869 and `_on_safe_door` at :966), snapshot
building, broadcast fan-out, and save-load rebind all live on one class.
The docstrings carry so much spec history that reading *behavior* requires
skimming ~300 lines of comments. Suggested (optional, medium risk):
extract a `DoorsController`-style module (pure state machine over
`Grid`, returning the same error dicts) — both machines become tables
`[(action, state) → new_state]` + guards, and the spec's "deterministic
validation order" becomes data. Do **not** split the class itself:
tests construct `GameSession` directly and the lock boundary must stay
one RLock.

**B.2 — Dead code (delete, near-zero risk):**
* `models.Session` dataclass (models.py:554) — defined, documented as
  "authoritative session state", **never instantiated** (the live object is
  `GameSession`; grep across app/tests/scripts confirms zero uses).
* `models.asdict` (models.py:592) — zero call sites.
* `GameSession._send_coro` (session.py:604) — zero call sites (its
  docstring even describes behavior the live code never does).
* `server._route_get_404` (server.py:525) — zero call sites (superseded by
  the HTTPException 404 handler).
* `server._map_doors` (server.py:~447) — one-line wrapper whose only caller
  (`_with_doors`) could call `grid.doors_for_wire()` directly; its
  docstring duplicates `doors_for_wire`'s.

**B.3 — Stale docstrings (fix in the same commit as the dead code):**
* server.py:249 & :282 and session.py:23/533 all claim session handling
  runs via `to_thread` / "a worker thread" — the shipped code runs
  **synchronously on the event-loop thread** (the inline comment at
  server.py:282 says exactly the opposite of the docstring above it).
  This misdocuments the threading model, which is the single most
  important correctness property of the module.
* session.py:23 "called from this loop thread (WS I/O) and from the
  starlette threadpool (REST)" — correct in substance, but the
  `to_thread` phrasing makes it read like the old design.
* `grid.py` module docstring says the GM "paint" action "will route through
  here" — it doesn't (paint writes `grid.cells[y][x]` directly at
  session.py:948 and server.py:~660; `set_cell` is now test-only).

**B.4 — Duplicated code worth consolidating (when touched):**
* `Grid.__post_init__` door vs safe-door validation (models.py:~120–180):
  two ~25-line near-identical blocks; a shared `_validate_door_map(...)`
  would halve them.
* `main.slug_map_id` and `saves._slug`: same regex, different caps
  (48 vs 32, `""` vs `"save"` fallback). Fine to leave; one shared helper
  with parameters if either moves.
* `server.py` `_handle_upload`/`_handle_generate`/`_handle_paint`:
  triple-repeated body-read → JSON-parse → dict-check → per-field
  bool-vs-int validation. A small `_validated_int(payload, key, error)`
  helper removes ~20 lines of identical `isinstance(x, bool) or not
  isinstance(x, int)` runs. The error strings must stay byte-identical.
* `join`/`_on_create_entity`/`_on_use_map` each roll an
  `eid = f"e{n+1}"; while eid in …: n += 1` loop (session.py:~300/710/1180)
  — a 4-line `_next_entity_id()` helper.

**B.5 — Magic numbers:** `min(20, …)` in `Player.from_dict` (models.py:538)
duplicates `AWARENESS_MIN/MAX` (awareness.py) — import or move the
constants to `models.py`. `MAX_PLAYERS = 6` (session.py) is the right
pattern; the entity-id `e{n}` prefix, thumbnail palette hexes (detection.py
`_THUMB_*`), `WALL_FRACTION_AUTO_INVERT = 0.6`, `APPROX_BLOCK = 2` are all
named constants — good. No other unexplained literals found.

**B.6 — `app/main.py` is a two-persona module.** It holds (a) the
process-state registries + `get_session` and (b) the CLI entry, and the
docstring documents the acyclic-import dance that forces `server.py` to
import it at module level while `main.main` imports `server` lazily.
`server.py` also *re-declares* `BASE_DIR`/`STATIC_DIR` (server.py:80 vs
main.py:52) shadowing the imported names. Splitting main.py into
`app/state.py` (registries) + `app/cli.py` would make the import graph
honest; low value today (it works, and is documented), high churn.
**Consider only if server.py must grow.**

**B.7 — `server.py` test-compat debt is load-bearing — protect it, don't
hide it.** `_make_maps_detail_route` (server.py:481) builds a *throwaway
FastAPI app just to splice one route in* (because the legacy path
classification needs the raw path); `ThreadingHTTPServer`/`_UvicornThread`
(server.py:779/873) exist purely so tests and `e2e_proof.py` boot the
server exactly like the old stdlib server. Both are ugly *on purpose* and
are the reason the suite is green. Refactoring them = rewriting
`test_api.py`/`test_ws.py` boot code (985 + 1101 lines) — classify as
non-goal (D.4).

**B.8 — Positives (do not "fix"):** type hints are consistent across all
modules; error-string constants (`SESSION_FULL`, `NO_ROUTE`, …) at the top
of session.py; pure-function modules (`pathfinding`, `visibility`,
`awareness`, `generation`, `detection`, `imaging`) have zero server state
and are trivially unit-testable; every non-obvious invariant has a spec
citation + AC in its docstring. The codebase's comment density is high and
*accurate* except for the `to_thread` cases in B.3.

---

## Section C — PyPI reuse table

Verification method (sandbox egress: pypi.org + files.pythonhosted.org
only): `pip index versions <pkg>` for existence/cadence,
`pip download --no-deps` for wheel/python-version checks, and an actual
install+uninstall benchmark for orjson (venv left clean).

| # | Hand-rolled item | Candidate (latest verified) | Covers? | Risk | Verdict |
|---|---|---|---|---|---|
| 1 | `json.dumps/loads` on the WS/REST hot path (server.py:262/272/277/292, 572) | **orjson 3.12.0** (cp314 wheel verified; active) | partial — faster dumps/loads, but **byte output differs**: stdlib `json.dumps` escapes non-ASCII (`\uXXXX`) while orjson emits raw UTF-8 by default | MEDIUM — wire-visible for non-ASCII names; 3 send sites + test client parse | **REJECT.** Measured 4.3× but saves ~0.5 ms on a 44 ms path (~1 %). A/B.1 is 100× the gain. If ever revisited, it is owner-sign-off class (wire format). |
| 2 | A* + no-corner-cut + Bresenham LOS (pathfinding.py, 379 LOC) | `astar 1.1`, `pathfinding 1.0.22`, `gridpathfinder` (**not on PyPI** — `No matching distribution`) | no — none support the team-aware blocked-set (hostiles vs open safe doors), deterministic tie-break, or the corner-cut *sight* rule; the maintained ones are graph-generic | HIGH if forced | **REJECT.** Confirms the previous review's row 2. This is the game's anti-cheat core; 815 tests pin it. |
| 3 | `decode_image`/`encode_png` correctness | **Pillow 12.3.0** (already in use) | yes | — | **ADOPTED (already)** — verified the wrapper is correct: format guards, interlace/BMP-depth rejections, `convert("RGBA")` default alpha. No further action; the remaining hand-rolled ops are C.4. |
| 4 | `otsu_threshold` (histogram + plateau-mid), `median3x3` (center-wins-tie majority), `to_gray` (imaging.py) | **numpy 2.5.3** (active; no `skimage` on the sanctioned set) | partial — Otsu via 256-bin `np.bincount` is straightforward; the 3×3 majority is a `np.convolve` of the 0/1 grid; **neither library provides the pinned plateau-mid / center-wins-tie semantics — the rules stay hand-rolled** | MEDIUM — detection tests pin fixtures byte-for-byte; only touches upload path | **CONSIDER (upload-path only).** Real but small: the pipeline is O(60×60) at the grid cap; measured decode+pipeline is tens of ms. Payoff is code clarity for the histogram, not speed. Rejected if we value the "stdlib-only math" consistency; adopt only with the full detection fixture suite as gate. |
| 5 | WS framing / handshake | **websockets 17.1** (already in use via uvicorn) | yes (server side) | — | **ADOPTED (already).** No `ajson`/`asgi-websockets`/framework swap warranted: uvicorn's legacy `websockets` impl is *pinned deliberately* (server.py:56 comment — 101 headers match the raw-socket test client). `ajson 0.12.0` (exists but micro-framework) **REJECTED**: the app's server is already a WS framework; ajson adds nothing. The raw-socket *client* in ws.py stays (it IS the test oracle). |
| 6 | Save-bundle locking / robustness (saves.py) | `filelock 3.32.5` | no need — single-writer process + atomic `os.replace`+fsync already gives the guarantee filelock would | LOW (but zero benefit) | **REJECT.** filelock protects cross-process writers; there is exactly one server process and tests redirect `SAVES_DIR` to temp dirs. |
| 7 | Save-bundle schema validation (saves.py `_validated_grid`/`_validated_entities`) | `jsonschema 4.26.0` or **pydantic 2.13.5 (already pinned)** | partial — both validate *shape*; the app's validator also enforces cross-field rules (doors on doorway cells, dim agreement, unique ids) with **exact error semantics** that map to the pinned `404 save not found` | MEDIUM — validator refactor changes `test_saves.py` (670 lines) expectations | **REJECT (jsonschema); CONSIDER (pydantic, long-term).** pydantic v2 is already a dependency (FastAPI transitive, pinned at 2.13.5) so marginal cost is zero; a `SaveBundle` model could own `_validated_*`. But the previous review explicitly kept models.py manual (its row 6, "by contract"), and the win is validation *deduplication* against `Grid.__post_init__`, not speed. Optional follow-up with owner sign-off. |
| 8 | CLI entry (`argparse`, main.py `main()`) | `typer 0.27.2` / `click 8.5.0` (click already present as a FastAPI dep) | yes, trivially | LOW | **REJECT (as a dependency).** The CLI is two flags (`--host`, `--port`) with a 3-line banner. `argparse` is stdlib and `main(argv)` is already cleanly separated for tests. Importing click/typer buys nothing; if the CLI ever grows (subcommands), revisit — click is already installed. |
| 9 | Logging (one `logging.getLogger` in session.py:47) | `loguru 0.7.3` (note: **0.7.3 is the final release; project is in maintenance mode**) | yes | LOW | **REJECT.** Stdlib logging is used in exactly one place (a `logger.warning` for degenerate grids); uvicorn already owns server logs. loguru's stagnation makes it a bad long-term bet for a one-call-site gain. |
| — | Image *I/O* convenience | `imageio 2.37.4` | yes (decode/encode) | LOW | **REJECT.** Pillow is already the I/O layer and `imageio` is a higher-level multiplexer (ffmpeg plugins, GIFs) the app never needs; adding it beside Pillow would be a second opinion on the same files. |
| — | Dungeon generation | nothing found | — | — | **REJECT (none applicable).** BSP + tree-doorway invariants (I1–I7) are spec-pinned and byte-stable per seed; no PyPI dungeon generator reproduces that contract. |

**PyPI candidate tally: 9 candidates → 0 adopted, 2 considered (numpy,
pydantic-as-save-layer), 7 rejected** (orjson, pathfinding libs, filelock,
jsonschema, typer/click, loguru, ajson/imageio — Pillow and websockets are
counted as already-adopted, not fresh decisions).

---

## Section D — Prioritized work plan

All batches must keep the standing gates green: pytest 793,
unittest discover 793, frontend 233, `scripts/e2e_proof.py` all-✓.

### Batch 1 — Dead code + doc fixes (LOW risk, quick win, ~1–2 h)
Delete `models.Session`, `models.asdict`, `GameSession._send_coro`,
`server._route_get_404`; inline `_map_doors` into `_with_doors`; fix the
stale `to_thread` docstrings (server.py:249/282, session.py:23/533) and the
grid.py "will route through here" line; replace the `min(20, …)` magic
number with the awareness constants (behavior-identical: the live setter
already enforces the same range).
**Expected test impact:** none (zero call sites verified). Optionally add
2–3 assertive tests that `from_dict` clamps, to lock the constant move.

### Batch 2 — Grid wire-form cache + broadcast sharing (LOW-MED risk, HIGH payoff)
Add the revision/dirty marker to `Grid` (bumped at the existing mutation
choke points); cache the wire map dict; have `state_for`/`welcome_for`
reuse one cached map object per broadcast; hoist the closed-door set out of
`build_awareness` (A.2). No wire bytes change — same keys, same values,
same field order (dicts are built in the same order as today).
**Expected test impact:** none expected; the door-perf budget test should
show ~10× headroom (verify with a before/after `time` print). This batch is
the only one where a subtle stale-cache bug could corrupt snapshots — the
per-viewer snapshot tests (test_session.py, test_door_session.py) are the
guard; add one explicit "snapshot after paint != snapshot before" test if
not already present (test_api/test_door_session cover REST + WS paints).

### Batch 3 — Off-loop upload/generate (LOW risk, robustness payoff)
`await asyncio.to_thread(...)` around `base64.b64decode` + `detect_grid` +
`grid_to_thumbnail_png` in `_handle_upload`, and `generate_grid` +
thumbnail in `_handle_generate`. Zero wire change.
**Expected test impact:** none (tests are serial clients; timing-sensitive
tests use generous budgets). Verify `e2e_proof.py` upload step unchanged.

### Batch 4 — Optional medium-risk items (only with owner interest)
* (a) Session god-object: extract the door/safe-door state machines into a
  pure module (B.1) — error strings must stay byte-identical;
  test_door_session.py (1210 lines) is the gate.
* (b) numpy for Otsu/median3x3 internals (C.4) — detection fixture suite
  (test_detection.py, 411 lines) is the gate; keep the pinned rules
  (plateau-mid, center-wins-tie) as the acceptance criteria.
* (c) pydantic `SaveBundle` validation layer (C.7) — test_saves.py gate;
  requires owner sign-off since it revisits the previous review's
  "row 6 not adopted by contract" decision.
* (d) `{pos: entity}` occupancy index for `_any_entity_at` (A.7) — pair
  with Batch 2's mutation choke points.

### Non-goals / explicitly rejected
* **No WS-framework or transport change** (uvicorn+websockets is final;
  the legacy-impl pin and the raw-socket test oracle are intentional).
* **No orjson** (C.1 — measured sub-1 % gain, wire-visible unicode
  semantics).
* **No A*/pathfinding library** (C.2 — nothing maintained covers the
  domain rules; previous review agreed).
* **No filelock/jsonschema** on saves (C.6/C.7 — single-writer + atomic
  replace + manual validation are exactly right; jsonschema can't express
  the cross-field rules without losing error semantics).
* **No typer/click, no loguru** (C.8/C.9 — one-flag CLI; one logger call).
* **No rewrite of the `ThreadingHTTPServer` adapter /
  `_make_maps_detail_route`** (B.7 — load-bearing for ~3.1k lines of
  boot-shaped test code; pure churn risk).
* **No save-file format change** (any change is owner-sign-off class;
  the current bundle shape, atomic-write, and corrupt-tolerant list are
  spec §4-pinned).
* **No `.gitignore` change for `saves/`** (already ignored — spec S5).
  Note: `.logs/` is **not** gitignored and currently shows as untracked
  (`?? .logs/*.log`); per the house rule that agent test logs stay out of
  git, **adding `.logs/` to `.gitignore` is recommended in the commit that
  lands this report** (docs-only + gitignore, zero code impact).

---

### Appendix — evidence trail

* Benchmarks: `/tmp/ld_profile.py`, `/tmp/ld_orjson.py` (run with
  `.venv/bin/python`; orjson installed for the measurement only and
  uninstalled afterwards — `pip list` matches requirements.txt exactly).
* Dead-code greps: `Session(`, `asdict(`, `_send_coro(`,
  `_route_get_404(` → zero non-definition hits across `app/`, `tests/`,
  `scripts/`.
* `pydantic` import check: zero `import pydantic` in `app/` (pinned in
  requirements.txt line 6; imported only by fastapi internals).
* `gridpathfinder`: `pip index versions` → `No matching distribution found`
  (confirmed the name from the task does not exist on PyPI).
* orjson wheel: `orjson-3.12.0-cp314-cp314-manylinux…aarch64.whl`,
  `Requires-Python: >=3.10` — Python 3.14 support confirmed (benchmark was
  on the same interpreter as the app).
* Baseline: `pytest tests/` exit 0 at HEAD of `feat/backend-refactor`
  (pre-evaluation, no app/ or tests/ files touched by this review).
