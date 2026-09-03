"""LittleDungeons ASGI server (FastAPI + uvicorn, RFC 6455 via ``websockets``).

Row 4 of ``docs/reviews/simplification-review.md`` (folded in row 3, the
hand-rolled WS layer): the ``http.server.ThreadingHTTPServer`` +
``BaseHTTPRequestHandler`` stack is replaced by **FastAPI** serving the same
routes over **uvicorn**, which natively speaks RFC 6455 through the
``websockets`` library — so the hand-rolled server handshake + frame codec
in the old ``app/ws.py`` is deleted (only the *client* helpers remain there,
for the raw-socket test client).

External surface preserved (the test suite + ``scripts/e2e_proof.py`` boot
the server through the ``ThreadingHTTPServer``-shaped adapter below and talk
plain HTTP / raw RFC 6455 over the wire):

  * ``GET /health``          → ``{"status":"ok"}``
  * ``GET /api/maps``        → ``{"maps":[{id,name,width,height}...]}``
  * ``GET /api/maps/{id}``   → map detail (grid + entities/players), or the
                               legacy ``404 {"error":"not found"}``
  * ``POST /api/maps/upload`` → JSON body ``{"name","image_b64","cols"?,
    "rows"?, "dark_is_wall"?}`` (32 MB body cap) → detect + register
  * ``POST /api/maps/{id}/paint`` → ``{"x","y","cell_type"}`` GM cell edit
  * ``GET /ws?session=<id>`` → RFC 6455 upgrade → the live GameSession (the
    synchronous ``GameSession.handle_message`` is bridged from the loop
    thread; its broadcast scheduling needs the running event loop).
  * static frontend from ``app/static`` (mounted LAST so API/WS win)
  * unknown ``/api/*`` and unknown static files → ``404 {"error":"not found"}``

The FastAPI ``app`` object is also served directly by ``uvicorn`` in
``app.main.main`` (``python -m app.main``).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import warnings
from typing import Any

# The legacy "websockets" implementation warns once that it is deprecated in
# favour of "websockets-sansio" — we explicitly select the legacy one (its
# 101/4xx handshake response headers match the raw-socket test client best),
# so the warning is expected noise: silence it process-wide.
warnings.filterwarnings(
    "ignore",
    module=r"uvicorn\.protocols\.websockets\..*",
)

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from app.detection import detect_grid, grid_to_thumbnail_png
from app.generation import GEN_MAX_EDGE, GEN_MIN_EDGE, generate_grid
from app.main import (
    BASE_DIR,
    MAX_BODY,
    STATIC_DIR,
    get_map_entry,
    get_session,
    maps_registry,
    slug_map_id,
    _register_map,
    _timestamp_map_id,
    _unique_map_id,
)
from app.models import CELL_TYPES, Grid
from app.session import GameSession

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


# ---------------------------------------------------------------------------
# Error shapes — preserve the EXACT JSON bodies the old handler emitted
# ---------------------------------------------------------------------------


def _error_json(status: int, message: str) -> JSONResponse:
    """The old handler's error body: ``{"error": message}`` (NOT FastAPI's
    ``{"detail": ...}``)."""
    return JSONResponse(
        status_code=status,
        content={"error": message},
        headers={"Cache-Control": "no-store"},
    )


def not_found_handler(request: Any, exc: HTTPException) -> PlainTextResponse:
    """404 in the legacy shape for EVERYTHING: unknown ``/api/*`` paths,
    unknown map ids, and missing static files (``test_api.py`` pins both
    the ``{"error": "not found"}`` body and the 404 status for
    ``GET /nope.html`` and ``GET /../PROJECT.md``)."""
    return _error_json(404, "not found")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def build_app() -> FastAPI:
    """Build the FastAPI application (routes + WebSocket + static)."""
    app = FastAPI(
        title="LittleDungeons",
        version="0.2.0",
        docs_url=None,  # keep the external surface identical to the old server
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Any, exc: HTTPException):
        # Starlette raises 404 from route matching; the old server answered
        # EVERY unknown path with {"error":"not found"} — keep that.
        if exc.status_code == 404:
            return not_found_handler(request, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail or "error"},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Any, exc: RequestValidationError):
        # Never used by our routes (they validate manually, below) — but if
        # it ever fires, keep the legacy {"error": ...} shape.
        return _error_json(400, "bad request")

    # -- health --------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # -- maps -----------------------------------------------------------------

    @app.get("/api/maps")
    async def maps_list() -> JSONResponse:
        body = {
            "maps": [
                {
                    "id": map_id,
                    "name": entry["grid"].name,
                    "width": entry["grid"].width,
                    "height": entry["grid"].height,
                }
                for map_id, entry in maps_registry.items()
            ]
        }
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    # NOTE: ``{map_id}`` is a PATH PARAM (not a decorator path template) so
    # the route is registered as ``GET /api/maps/{map_id}`` — Starlette's
    # ``:str`` convertor is ``[^/]+`` and therefore matches nested
    # ``/api/maps/a/b`` too; the handler re-checks and 404s, mirroring the
    # old ``_route_get`` classification ("maps_detail" only for a single
    # non-empty segment, else "api_404").
    @app.api_route("/api/maps/{map_id}", methods=["GET", "POST"])
    async def maps_detail_or_404(map_id: str) -> Any:
        # The handler inspects the RAW path to classify (identical to the
        # old _route_get): detail only for a single non-empty segment.
        raise HTTPException(status_code=404)  # replaced below via endpoint

    # Re-register with a closure-based endpoint so the raw path is visible
    # (FastAPI's path-params API would URL-decode and re-segment for us).
    app.routes[-1] = _make_maps_detail_route()

    # Registered AFTER the ``{map_id:path}`` detail route on purpose: the
    # permissive detail route matches any single-segment path under
    # /api/maps/ for GET and POST, so these concrete routes must sit after
    # it to win for their own paths (Starlette walks routes in registration
    # order; a POST to /api/maps/upload must hit THIS handler, not the
    # GET-detail route which would 405 it).
    @app.post("/api/maps/upload")
    async def maps_upload(request: Request) -> JSONResponse:
        """``POST /api/maps/upload`` — JSON body (NOT multipart), same
        validation + status codes as the old handler."""
        return await _handle_upload(request)

    @app.post("/api/maps/generate")
    async def maps_generate(request: Request) -> JSONResponse:
        """``POST /api/maps/generate`` — generate a dungeon of the exact
        requested cols x rows (generated-maps spec §5)."""
        return await _handle_generate(request)

    @app.post("/api/maps/{map_id}/paint")
    async def maps_paint(map_id: str, request: Request) -> JSONResponse:
        """``POST /api/maps/{id}/paint`` — GM cell edit (same body + codes)."""
        return await _handle_paint(map_id, request)

    # Legacy API-404s for unknown /api/* paths (the Starlette 404 handler
    # above turns them into {"error":"not found"}).

    # -- websocket -------------------------------------------------------------

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        """RFC 6455 upgrade → live :class:`GameSession` message loop.

        ``/ws?session=<id>`` (default ``"default"``) resolves to a
        get-or-create session (module-level registry in ``app.main``). The
        client drives the protocol: it sends ``{type:"join",name,role}``
        first; ``session.handle_message`` (SYNCHRONOUS — the RLock still
        serialises all state) runs in a worker thread off the event loop
        (``to_thread``) and returns a per-client reply, or ``None`` when the
        session itself broadcast the frames via the connection's async
        sender (registered below — uvicorn serialises sends per connection,
        so a reply can never interleave with a broadcast on the same socket).
        """
        await websocket.accept()
        session_id = websocket.query_params.get("session") or "default"
        session = get_session(session_id)

        async def send(obj: dict[str, Any]) -> None:
            # The async sender this connection's broadcast frames go out
            # through. Awaiting websocket.send_text yields (backpressure),
            # so the session's broadcasts never block the event loop.
            await websocket.send_text(json.dumps(obj, separators=(",", ":")))

        # Register BEFORE the loop so _broadcast reaches this client; the
        # session stores the *WebSocket* object as the stable per-connection
        # identity (same object flows through join/handle_message/detach).
        session.attach_async(websocket, send)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    await websocket.send_text(
                        json.dumps(
                            {"type": "error", "message": "invalid JSON"},
                            separators=(",", ":"),
                        )
                    )
                    continue
                # Runs SYNCHRONOUSLY on the event-loop thread — deliberately
                # NOT via to_thread: the session's broadcast scheduling
                # (app.session._schedule) needs the running loop to create
                # its tasks, and it only ever holds the RLock for a few
                # milliseconds (a 16x12 A* is <1ms), so doing it inline
                # keeps joins/moves/paints broadcast-correct without adding
                # a thread hop. (REST does not take the session lock, so
                # there is no cross-thread lock contention to hide behind.)
                reply = session.handle_message(websocket, msg)
                if reply is not None:
                    await websocket.send_text(
                        json.dumps(reply, separators=(",", ":"))
                    )
        except WebSocketDisconnect:
            pass
        finally:
            session.detach(websocket)

    # -- static frontend (mounted LAST: /api, /health, /ws win) -----------------

    # "/" → index.html (StaticFiles(html=False) 404s on a bare "/").
    @app.get("/", include_in_schema=False)
    async def index() -> Any:
        from starlette.responses import FileResponse

        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=False), name="static")
    return app


def _make_maps_detail_route() -> Any:
    """GET /api/maps/{id} with EXACT old classification.

    The old ``_route_get`` returned "maps_detail" for any path starting with
    ``/api/maps/`` where the remainder is non-empty and contains no ``/``;
    everything else under ``/api/maps/`` was "api_404" (e.g. nested
    ``/api/maps/a/b``). We register the route with a permissive regex
    ``{map_id:path}`` (matches across slashes) and re-classify on the raw
    path, raising the legacy 404 for the nested case.

    Built on a throwaway FastAPI with the auto /openapi.json//docs routes
    DISABLED (openapi_url/docs_url/redoc_url=None) and take the LAST route —
    FastAPI prepends the auto routes, so ``routes[0]`` would be wrong.
    """
    from fastapi import FastAPI as _F

    probe = _F(openapi_url=None, docs_url=None, redoc_url=None)

    @probe.get("/api/maps/{map_id:path}")
    async def maps_detail(map_id: str) -> JSONResponse:
        entry = get_map_entry(map_id)
        if entry is None:
            raise HTTPException(status_code=404)
        grid: Grid = entry["grid"]
        return JSONResponse(
            {
                "id": map_id,
                "name": grid.name,
                "width": grid.width,
                "height": grid.height,
                "image": grid.image,
                "cells": grid.cells,
                "entities": list(entry["entities"].values()),
                "players": list(entry["players"].values()),
            },
            headers={"Cache-Control": "no-store"},
        )

    # Take just the single route we registered so it can be spliced into the
    # main app in registration order (before the static mount).
    return probe.routes[-1]


def _route_get_404(path: str) -> bool:
    """Mirror the old ``_route_get`` "api_404" classification."""
    if path.startswith("/api/maps/"):
        rest = path[len("/api/maps/"):]
        if rest and "/" not in rest:
            return False  # this is a maps_detail (valid single segment)
        return True  # empty or nested → api_404
    if path.startswith("/api/"):
        return True
    return False


# ---------------------------------------------------------------------------
# POST body handling — 32 MB cap + manual JSON validation (exact old codes)
# ---------------------------------------------------------------------------


async def _read_body_checked(request: Any) -> tuple[bytes, JSONResponse | None]:
    """Read the body enforcing the 32 MB cap like the old handler.

    Returns ``(body_bytes, None)`` on success, or ``(b"", error_response)``
    when the body is too large (400 ``request body too large``) or the
    client used chunked transfer-encoding (400, as the old handler did via
    ``ValueError``).
    """
    # Starlette's Request.body() does NOT read chunked bodies (it reads
    # Content-Length framed data). The old handler explicitly rejected
    # Transfer-Encoding: chunked — preserve that.
    headers = request.headers
    if "transfer-encoding" in headers:
        return b"", _error_json(400, "request body too large")
    length = headers.get("content-length")
    if length is not None:
        try:
            n = int(length)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            return b"", _error_json(400, "request body too large")
    body = await request.body()
    if len(body) > MAX_BODY:
        return b"", _error_json(400, "request body too large")
    return body, None


async def _parse_json_body(body: bytes) -> tuple[Any, JSONResponse | None]:
    try:
        return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error_json(400, "request body must be JSON")
    except Exception:
        return None, _error_json(400, "request body must be JSON")


async def _handle_upload(request: Any) -> JSONResponse:
    """``POST /api/maps/upload`` (PROJECT.md §7/§8).

    JSON body: ``{"name": str, "image_b64": str, "cols"?: int,
    "rows"?: int, "dark_is_wall"?: bool}``. base64 → bytes →
    :func:`app.detection.detect_grid` → register → 200 with the grid and a
    PNG data-URL thumbnail. Decode/detect errors → 400 {"error": msg}.
    """
    import base64

    body, err = await _read_body_checked(request)
    if err is not None:
        return err
    payload, err = await _parse_json_body(body)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return _error_json(400, "request body must be a JSON object")

    name = payload.get("name")
    image_b64 = payload.get("image_b64")
    if not isinstance(name, str) or not name.strip():
        return _error_json(400, "'name' must be a non-empty string")
    if not isinstance(image_b64, str) or not image_b64.strip():
        return _error_json(400, "'image_b64' must be a non-empty string")
    cols = payload.get("cols")
    rows = payload.get("rows")
    if cols is not None and (isinstance(cols, bool) or not isinstance(cols, int)):
        return _error_json(400, "'cols' must be an integer")
    if rows is not None and (isinstance(rows, bool) or not isinstance(rows, int)):
        return _error_json(400, "'rows' must be an integer")
    dark_is_wall = payload.get("dark_is_wall", True)
    if not isinstance(dark_is_wall, bool):
        return _error_json(400, "'dark_is_wall' must be a boolean")

    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError
        return _error_json(400, f"bad base64: {exc}")

    try:
        grid = detect_grid(
            image_bytes,
            name=name.strip(),
            cols=cols,
            rows=rows,
            dark_is_wall=dark_is_wall,
        )
    except ValueError as exc:
        return _error_json(400, str(exc))

    slug = slug_map_id(name.strip())
    map_id = _unique_map_id(slug) if slug else _timestamp_map_id()
    grid.image = name.strip()
    _register_map(map_id, grid)

    return JSONResponse(
        {
            "id": map_id,
            "name": grid.name,
            "width": grid.width,
            "height": grid.height,
            "cells": grid.cells,
            "thumbnail": grid_to_thumbnail_png(grid),
        },
        headers={"Cache-Control": "no-store"},
    )


async def _handle_generate(request: Any) -> JSONResponse:
    """``POST /api/maps/generate`` (generated-maps spec §5).

    JSON body: ``{"name": str, "cols": int 8-60, "rows": int 8-60,
    "seed"?: int}``. :func:`app.generation.generate_grid` → register →
    200 with the SAME key set as upload (``{"id","name","width","height",
    "cells","thumbnail"}``). Validation order per §5.1 (body-object →
    name → cols → rows → seed) with the exact §5.1 error strings; bools are
    rejected for every int field (same style as the upload route).
    """
    body, err = await _read_body_checked(request)
    if err is not None:
        return err
    payload, err = await _parse_json_body(body)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return _error_json(400, "request body must be a JSON object")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error_json(400, "'name' must be a non-empty string")
    cols = payload.get("cols")
    if (
        isinstance(cols, bool)
        or not isinstance(cols, int)
        or not (GEN_MIN_EDGE <= cols <= GEN_MAX_EDGE)
    ):
        return _error_json(400, "'cols' must be an integer in 8-60")
    rows = payload.get("rows")
    if (
        isinstance(rows, bool)
        or not isinstance(rows, int)
        or not (GEN_MIN_EDGE <= rows <= GEN_MAX_EDGE)
    ):
        return _error_json(400, "'rows' must be an integer in 8-60")
    seed = payload.get("seed")  # optional: omitted or null → unseeded
    if seed is not None and (
        isinstance(seed, bool) or not isinstance(seed, int)
    ):
        return _error_json(400, "'seed' must be an integer")

    try:
        grid = generate_grid(cols, rows, name.strip(), seed)
    except ValueError as exc:
        # Belt-and-braces: the endpoint checks first, but a defensive
        # ValueError from the generator maps to 400 like upload's does.
        return _error_json(400, str(exc))

    slug = slug_map_id(name.strip())
    map_id = _unique_map_id(slug) if slug else _timestamp_map_id()
    _register_map(map_id, grid)

    return JSONResponse(
        {
            "id": map_id,
            "name": grid.name,
            "width": grid.width,
            "height": grid.height,
            "cells": grid.cells,
            "thumbnail": grid_to_thumbnail_png(grid),
        },
        headers={"Cache-Control": "no-store"},
    )


async def _handle_paint(map_id: str, request: Any) -> JSONResponse:
    """``POST /api/maps/{id}/paint`` (PROJECT.md §8) — GM cell edit.

    JSON body: ``{"x": int, "y": int, "cell_type": "floor"|"wall"|
    "doorway"}``. 404 unknown map; 400 bad body / out of bounds / bad cell
    type; 200 ``{"ok": true, "x", "y", "cell_type"}``.
    """
    body, err = await _read_body_checked(request)
    if err is not None:
        return err
    payload, err = await _parse_json_body(body)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return _error_json(400, "request body must be a JSON object")
    x = payload.get("x")
    y = payload.get("y")
    cell_type = payload.get("cell_type")
    if isinstance(x, bool) or not isinstance(x, int):
        return _error_json(400, "'x' must be an integer")
    if isinstance(y, bool) or not isinstance(y, int):
        return _error_json(400, "'y' must be an integer")
    if cell_type not in CELL_TYPES:
        return _error_json(
            400,
            f"'cell_type' must be one of {'/'.join(CELL_TYPES)}",
        )

    entry = get_map_entry(map_id)
    if entry is None:
        return _error_json(404, "not found")
    grid: Grid = entry["grid"]
    if not (0 <= x < grid.width and 0 <= y < grid.height):
        return _error_json(
            400,
            f"({x}, {y}) out of bounds for {grid.width}x{grid.height} grid",
        )

    grid.cells[y][x] = cell_type
    return JSONResponse(
        {"ok": True, "x": x, "y": y, "cell_type": cell_type},
        headers={"Cache-Control": "no-store"},
    )


# Build the app once at import time (the test adapter + CLI share it).
app = build_app()


# ---------------------------------------------------------------------------
# The ThreadingHTTPServer-shaped compatibility adapter
# ---------------------------------------------------------------------------


class _UvicornThread:
    """Runs a uvicorn Server on a pre-bound socket in a dedicated
    background thread with its own asyncio event loop.

    The sync GameSession is NOT ported to asyncio — it is called from this
    loop thread (WS I/O) and from the starlette threadpool (REST), and the
    session's RLock still serialises all state access.
    """

    def __init__(
        self,
        sock: socket.socket,
        handle_error: Any = None,
        quiet: bool = False,
    ) -> None:
        self._sock = sock
        self._handle_error = handle_error
        self._quiet = quiet
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopped = threading.Event()

    # -- uvicorn lifecycle (runs in the background thread) -------------------

    def _run(self) -> None:
        try:
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self._sock.getsockname()[1],
                ws="websockets",  # native RFC 6455 via the websockets lib
                ws_per_message_deflate=False,  # raw-socket client: no permsg deflate
                access_log=not self._quiet,
                log_level="warning" if self._quiet else "info",
                lifespan="off",
                loop="asyncio",
                timeout_graceful_shutdown=2,
            )
            self._server = uvicorn.Server(config)
            # The pre-bound socket goes to run() (uvicorn.Config takes no
            # sockets argument): uvicorn wraps it in its own asyncio loop.
            self._server.run(sockets=[self._sock])  # blocks on its own loop
        except BaseException as exc:  # surfaced to serve_forever's caller
            if self._handle_error is not None:
                try:
                    self._handle_error(exc)
                except Exception:
                    pass
            else:
                print(f"uvicorn thread: {exc}", file=sys.stderr)
        finally:
            self._stopped.set()

    # -- the ThreadingHTTPServer-shaped lifecycle -----------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="littedungeons-uvicorn"
        )
        self._thread.start()
        # Wait until the loop is accepting (uvicorn.run returns once serve()
        # completes, so a live server here means startup succeeded).
        for _ in range(500):  # up to 5s
            if self._server is not None and self._server.started:
                return
            if self._stopped.is_set():
                return
            import time
            time.sleep(0.01)

    def shutdown(self) -> None:
        srv = self._server
        if srv is not None and self._thread is not None and self._thread.is_alive():
            # Set on the loop thread's Server: the main loop wakes within
            # 0.1s and runs its graceful shutdown (open WS connections are
            # closed with 1012; the loop finishes and the thread exits).
            srv.should_exit = True
            self._thread.join(timeout=10)

    def stop(self) -> None:
        """Hard stop: also close the listening socket (server_close)."""
        self.shutdown()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)


class ThreadingHTTPServer:
    """Drop-in replacement for ``http.server.ThreadingHTTPServer``.

    Tests (``tests/test_api.py``, ``tests/test_ws.py``) and
    ``scripts/e2e_proof.py`` boot the server exactly like the old
    stdlib server:

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), LittleDungeonsHandler)
        httpd.daemon_threads = True
        httpd.handle_error = lambda *a, **k: None
        host, port = httpd.server_address[:2]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        ...
        httpd.shutdown(); httpd.server_close()

    This adapter keeps that exact external surface: it binds a real
    listening socket in the constructor (so ``server_address`` is a real
    ``(host, port)`` tuple immediately), and ``serve_forever``/``shutdown``/
    ``server_close`` map onto a uvicorn Server running the FastAPI app in a
    dedicated background thread with its own asyncio event loop. The handler
    argument is accepted and ignored (the FastAPI app now owns the routes);
    ``daemon_threads`` / ``handle_error`` are accepted-and-ignored attributes
    (settable by the tests; ``handle_error`` is honoured as a best-effort
    error sink).
    """

    daemon_threads: bool = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: Any = None,
    ) -> None:
        host, port = server_address
        # Bind for real so .server_address works exactly like the stdlib
        # server (port 0 → kernel picks a free port, readable immediately).
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen()
        self.server_address: tuple[str, int] = self._sock.getsockname()[:2]
        self._handler = RequestHandlerClass  # accepted & ignored
        self.handle_error = None
        self._runner: _UvicornThread | None = None

    # -- ThreadingHTTPServer lifecycle ----------------------------------------

    def serve_forever(self, poll_interval: float = 0.5) -> None:  # noqa: N802
        if self._runner is None:
            self._runner = _UvicornThread(
                self._sock,
                handle_error=self.handle_error,
                quiet=os.environ.get("LITTLEDUNGEONS_QUIET_LOGS") is not None,
            )
            self._runner.start()
        # Block (like the stdlib serve_forever loop) until shutdown() is
        # called from another thread.
        while self._runner is not None and not self._runner._stopped.is_set():
            import time
            time.sleep(min(poll_interval, 0.2))

    def shutdown(self) -> None:  # noqa: N802
        if self._runner is not None:
            self._runner.shutdown()

    def server_close(self) -> None:  # noqa: N802
        if self._runner is not None:
            self._runner.stop()
        else:
            try:
                self._sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CLI entry point (``python -m app.main`` → app.main.main → here)
# ---------------------------------------------------------------------------


def run_server(host: str, port: int) -> None:
    """Run uvicorn programmatically in the FOREGROUND (blocking).

    Honours ``LITTLEDUNGEONS_QUIET_LOGS`` (silences uvicorn access logs,
    mapping the old env var that silenced ``BaseHTTPRequestHandler`` request
    logs). Keeps the old startup banner (the banner is KEPT, not dropped).
    """
    quiet = os.environ.get("LITTLEDUNGEONS_QUIET_LOGS") is not None
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        ws="websockets",
        ws_per_message_deflate=False,
        access_log=not quiet,
        log_level="warning" if quiet else "info",
        lifespan="off",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    print(f"LittleDungeons listening on http://{host}:{port}")
    print(f"  UI:     http://{host}:{port}/")
    print(f"  Health: http://{host}:{port}/health")
    try:
        server.run()  # blocks until shutdown (Ctrl-C → should_exit)
    except KeyboardInterrupt:
        print("\nshutting down")
