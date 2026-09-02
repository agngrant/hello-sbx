# LittleDungeons — Code-Simplification Review (package-based)

**Scope:** review only. No code changed by this document.
**Context:** the codebase is stdlib-only **by explicit contract** (PROJECT.md §2 "stdlib pivot"):
the sandbox has no egress to PyPI, so the app was deliberately built without third-party
packages. That constraint is the reason much of the code is hand-rolled. This review maps
every hand-rolled component to the Python package that would replace it, ranked by payoff.
If the dependency constraint is lifted (or the project is deployed outside the sandbox),
the recommended target stack is at the bottom.

## Inventory of hand-rolled components

| # | Component | File (approx. LOC) | What it does by hand | Package that replaces it | Savings / risk |
|---|-----------|--------------------|----------------------|--------------------------|----------------|
| 1 | PNG decode/encode, BMP decode, gray, resize (nearest), Otsu, 3×3 median | `app/imaging.py` (~520 LOC) | zlib+struct PNG codec, predictors, chunk I/O, BMP reader | **Pillow** (`PIL`): `Image.open`, `.convert("L")`, `.resize`, `ImageFilter.MedianFilter`, `ImageStat`; Otsu via `numpy.histogram` or `skimage.filters.threshold_otsu` | **~500 LOC → ~30**. Highest single payoff. Risk: near zero — behavior already mirrors PIL; tests pin fixtures. |
| 2 | A* (8-dir, no corner cut) + Bresenham line-of-sight | `app/pathfinding.py` (~210 LOC) | heapq-based A*, heuristic, `came_from` chain, manual Bresenham | **No good package** for grid A* (libraries target graphs with arbitrary weights); `scipy.sparse.csgraph` gives Dijkstra, not A*/no-corner-cut. Bresenham has no stdlib/lib equivalent at this size. | Keep as-is (it's the app's core domain logic). `math` import could go (heuristic uses abs-only). **Lowest payoff — recommend NO change.** |
| 3 | RFC 6455 server + client (handshake, frame codec, masking, close) | `app/ws.py` (~380 LOC) | SHA-1 accept key, frame encode/decode, length encoding, WSClose control-flow | **`websockets`** (pyproject extra): `websockets.serve` / `connect`; or `python-socketio` if going the `flask-socketio` route. | **~350 LOC → ~20** and removes the entire class of frame-parsing edge cases. Risk: the WS endpoint is currently bolted onto `ThreadingHTTPServer` via raw-socket upgrade; with `websockets` the server runs its own asyncio loop (easy: thread + `asyncio.run`, or a `werkzeug`-free `aiohttp` app). Client helper in tests simplifies too. |
| 4 | HTTP server, routing, static file serving, body reading, upload endpoint | `app/main.py` (~500 LOC) | `BaseHTTPRequestHandler`, path classification, static mount, base64 image upload, JSON helpers | **FastAPI** (async, auto JSON, static mount via `StaticFiles`, `UploadFile`) **or** minimal `flask` + `flask-sock` alternative. FastAPI is the better fit (typed models map 1:1 to the dataclasses, `/health` and REST come nearly free). | **~300 LOC → ~80**. Risk: the stdlib handler is simple and well-tested; switching buys DX + docs (Swagger) but the biggest structural change. Pair with #3 (FastAPI + `websockets` or `python-engineio`). |
| 5 | Session lifecycle, locking, fan-out | `app/session.py` (~680 LOC) | RLock, per-connection send locks, reconnect bookkeeping, message dispatch | **No package replaces session state** (it's the domain core). Partial: if the server becomes async (FastAPI), `asyncio.Lock` + `asyncio.Queue` per connection replaces the hand-rolled send-lock plumbing (~60 LOC). | Moderate. Only worth it *because of* #4; not worth it standalone. |
| 6 | Dataclass → dict serialization | `app/models.py` | Hand-written `to_dict`/`from_dict` for 4 types | **`dataclasses.asdict`** (stdlib!) + explicit `from_dict` where defaults/validation matter; or **Pydantic** models (if #4 picks FastAPI) — validation, JSON, and the frontend contract all in one place. | Small for pure stdlib; **large if #4 lands** (Pydantic models become the API schema and test oracle). |
| 7 | Test runner + Node-based JS frontend tests | `tests/` | `unittest` + a hand-rolled Node harness (`tests/js/harness.js`) to execute the real `app.js` | **pytest** (fixtures, `pytest-timeout` — which also eliminates the hand-rolled watchdog pattern from the id-gap regression test), **playwright** (real browser, replaces the stub DOM harness entirely) or **selenium**. | `pytest` = pure DX win, ~0 risk. Playwright is heavy but deletes the entire stub-DOM layer; worth it once the frontend grows past one app.js. |
| 8 | E2E proof script | `scripts/e2e_proof.py` | Manual server boot + hand-checked prints | **pytest** + `httpx` (REST) + `websockets` (WS) as a proper `tests/e2e/` module, or keep the script and just make it `unittest`. | Small; do only if #7 lands. |

## Cross-cutting notes

- **Nothing in the app is "wrong" — it's all deliberate.** The stdlib code is clean, tested
  (212 tests), and documented. Simplification here is *LOC reduction + fewer hand-maintained
  edge cases*, not bug fixing.
- **The dependency constraint is the real decision.** PROJECT.md §2 records that the sandbox
  blocks PyPI. Every row above is a *conditional* recommendation: if/when installs are
  allowed (or the project moves to a real deploy target), rows 1+3+4+7 deliver the bulk of
  the payoff.
- **Thread → async shift:** rows 3–5 compound. The current `ThreadingHTTPServer` +
  per-socket send locks exist because of the raw-socket WS upgrade. An async stack
  (FastAPI + websockets) makes the per-connection lock plumbing disappear entirely.
- **Things to keep regardless:** `pathfinding.py` (domain logic, no package wins),
  `awareness.py` (tiny, pure, perfect), and the `wsclient`-style test client (or its
  `websockets` replacement).

## Recommended target stack (if packages are allowed)

```
fastapi            # replaces main.py HTTP/routing/static (row 4)
uvicorn[standard]  # ASGI server (replaces ThreadingHTTPServer)
websockets         # replaces ws.py (row 3)
pillow             # replaces imaging.py (row 1)
pydantic           # replaces models.py to_dict/from_dict + API schema (row 6)
pytest             # replaces unittest harness (row 7)
pytest-timeout     # replaces hand-rolled watchdogs
```

Suggested order of adoption (each step independently shippable behind the test suite):
1. **pillow** (isolated, largest LOC cut, zero protocol impact)
2. **pytest** + **pytest-timeout** (test-only, zero runtime impact)
3. **websockets** (swap the /ws endpoint; REST untouched)
4. **fastapi + uvicorn** (REST + static; session code moves to async locks)
5. **pydantic** (models become the shared contract)
6. **playwright** (optional, when the frontend outgrows the stub-DOM harness)

## Verdict

The codebase is lean for a zero-dependency system, and the hand-rolled parts that *look*
wasteful (WS codec, PNG codec) are the parts a package stack removes with the least
domain risk. **Recommended: keep stdlib for now (contract + sandbox), adopt in the order
above the moment installs are possible.** Highest value per unit of change: **pillow,
then pytest, then websockets.**
