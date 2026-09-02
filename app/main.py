"""LittleDungeons application (PROJECT.md §2, §8, §9) — pure standard library.

Stack: ``http.server.ThreadingHTTPServer`` + ``BaseHTTPRequestHandler``.

  * ``GET /health``       — liveness probe
  * ``GET /api/maps``     — list maps (summaries)
  * ``GET /api/maps/{id}``— map detail (grid + entities/players)
  * ``GET /ws``           — RFC 6455 upgrade (``app/ws.py``); Iteration 5:
                           get-or-create an ``app.session.GameSession`` for
                           ``?session=<id>`` (default ``"default"``) and run
                           the session message loop
  * ``GET /`` + other     — static frontend from ``app/static`` (mounted last
                            so the API routes win)

Iteration 3 implements both:

  * ``POST /api/maps/upload`` — JSON body ``{"name", "image_b64",
    "cols"?, "rows"?, "dark_is_wall"?}`` → base64 → ``app.detection.detect_grid``
    → register → grid + PNG data-URL thumbnail (PROJECT.md §7).
  * ``POST /api/maps/{id}/paint`` — ``{"x", "y", "cell_type"}`` GM cell edit.

Run:  ``python -m app.main --host 127.0.0.1 --port 8000``
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.detection import detect_grid, grid_to_thumbnail_png
from app.grid import SAMPLE_MAP_ID, build_sample_map
from app.models import CELL_TYPES, Grid
from app.session import GameSession
from app.ws import ws_accept, ws_serve

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Ensure static Content-Types are registered even on minimal installs.
mimetypes.add_type("text/css; charset=utf-8", ".css")
mimetypes.add_type("application/javascript; charset=utf-8", ".js")
mimetypes.add_type("text/html; charset=utf-8", ".html")
mimetypes.add_type("application/json; charset=utf-8", ".json")
mimetypes.add_type("image/png", ".png")

MAX_BODY = 32 * 1024 * 1024  # upload ceiling (Iteration 3 uses this)


# ---------------------------------------------------------------------------
# In-memory maps registry (authoritative source of truth; single server
# process).
# ---------------------------------------------------------------------------

maps_registry: dict[str, dict[str, Any]] = {}


def _register_map(map_id: str, grid: Grid) -> None:
    maps_registry[map_id] = {
        "grid": grid,      # Grid dataclass
        "entities": {},    # dict[str, Entity] — populated live by GameSession
        "players": {},     # dict[str, Player] — populated live by GameSession
    }


# Built-in sample map, registered at startup (Iteration 1).
_register_map(SAMPLE_MAP_ID, build_sample_map())


def get_map_entry(map_id: str) -> dict[str, Any] | None:
    return maps_registry.get(map_id)


# ---------------------------------------------------------------------------
# Live sessions (Iteration 5). Module-level so the ``?session=<id>`` query
# param is stable across connections: the same session object is shared by
# every client of that session id.
# ---------------------------------------------------------------------------

sessions: dict[str, GameSession] = {}
_sessions_lock = threading.Lock()


def get_session(session_id: str) -> GameSession:
    """Get-or-create the :class:`GameSession` for ``session_id``.

    The session's grid is ``maps_registry[session_id]`` when that map exists
    (e.g. an uploaded map id — the session then plays on the latest uploaded
    grid), otherwise the built-in sample dungeon. The grid object is SHARED
    with the registry entry, so REST paints and WS paints hit the same grid.
    """
    with _sessions_lock:
        session = sessions.get(session_id)
        if session is None:
            entry = maps_registry.get(session_id) or maps_registry[SAMPLE_MAP_ID]
            session = GameSession(session_id, entry["grid"])
            sessions[session_id] = session
        return session


# ---------------------------------------------------------------------------
# Map id helpers (Iteration 3: upload)
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug_map_id(name: str) -> str:
    """Slug for a map id: lowercase, ASCII, non-alphanumerics → ``-``.

    Returns ``""`` when nothing usable remains (caller falls back to a
    timestamp id).
    """
    slug = _SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return slug[:48]


def _unique_map_id(base: str) -> str:
    """First ``base`` not in the registry, else ``base-2``, ``base-3``, …."""
    if base not in maps_registry:
        return base
    n = 2
    while f"{base}-{n}" in maps_registry:
        n += 1
    return f"{base}-{n}"


def _timestamp_map_id() -> str:
    """``map-<timestamp>ms`` unique map id (fallback when the name has no
    usable slug, e.g. non-ASCII names)."""
    base = f"map-{int(time.time() * 1000)}"
    return _unique_map_id(base)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class LittleDungeonsHandler(BaseHTTPRequestHandler):
    """LittleDungeons HTTP + WebSocket request handler.

    Routing (GET): API routes first, static last, so ``/api/...``, ``/health``
    and ``/ws`` always win over the static mount.
    """

    protocol_version = "HTTP/1.1"
    server_version = "LittleDungeons/0.2"

    # -- routing helpers ----------------------------------------------------

    def _route_get(self, path: str) -> str:
        """Classify a GET path: 'health' | 'maps_list' | 'maps_detail' |
        'ws' | 'static'."""
        if path == "/health":
            return "health"
        if path == "/api/maps":
            return "maps_list"
        if path.startswith("/api/maps/"):
            rest = path[len("/api/maps/"):]
            if rest and "/" not in rest:
                return "maps_detail"
            return "api_404"
        if path == "/ws":
            return "ws"
        return "static"

    # -- response helpers ----------------------------------------------------

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _read_body(self) -> bytes:
        """Read a request body up to ``MAX_BODY`` (Iteration 3 upload hook)."""
        if "Transfer-Encoding" in (self.headers.get("Transfer-Encoding") or ""):
            raise ValueError("chunked transfer-encoding is not supported")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        return self.rfile.read(length) if length else b""

    # -- request dispatch ------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self._send_error_json(400, "bad request")
            return
        try:
            kind = self._route_get(path)
            if kind == "health":
                self._send_json(200, {"status": "ok"})
            elif kind == "maps_list":
                self._send_json(200, {"maps": self._maps_summary()})
            elif kind == "maps_detail":
                self._handle_map_detail(unquote(path[len("/api/maps/"):]))
            elif kind == "api_404":
                self._send_error_json(404, "not found")
            elif kind == "ws":
                self._handle_websocket()
            else:
                self._serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            # Never let one bad request take down the server thread.
            try:
                self._send_error_json(500, "internal server error")
            except Exception:
                self.close_connection = True

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlsplit(self.path).path
            if path == "/api/maps/upload":
                self._handle_upload()
            elif path.startswith("/api/maps/") and path.endswith("/paint"):
                map_id = unquote(path[len("/api/maps/") : -len("/paint")])
                if map_id and "/" not in map_id:
                    self._handle_paint(map_id)
                else:
                    self._read_body()
                    self._send_error_json(404, "not found")
            else:
                self._read_body()
                self._send_error_json(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except ValueError:
            self._send_error_json(400, "bad request")
        except Exception:
            try:
                self._send_error_json(500, "internal server error")
            except Exception:
                self.close_connection = True

    # -- route handlers --------------------------------------------------------

    def _maps_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "id": map_id,
                "name": entry["grid"].name,
                "width": entry["grid"].width,
                "height": entry["grid"].height,
            }
            for map_id, entry in maps_registry.items()
        ]

    def _handle_map_detail(self, map_id: str) -> None:
        entry = get_map_entry(map_id)
        if entry is None:
            self._send_error_json(404, "not found")
            return
        grid: Grid = entry["grid"]
        self._send_json(
            200,
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
        )

    def _handle_upload(self) -> None:
        """``POST /api/maps/upload`` (PROJECT.md §7/§8).

        JSON body: ``{"name": str, "image_b64": str, "cols"?: int,
        "rows"?: int, "dark_is_wall"?: bool}``. base64 → bytes →
        :func:`app.detection.detect_grid` → register → 200 with the grid and a
        PNG data-URL thumbnail. Decode/detect errors → 400 {"error": msg}.
        """
        body = self._read_body()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(400, "request body must be JSON")
            return
        if not isinstance(payload, dict):
            self._send_error_json(400, "request body must be a JSON object")
            return

        name = payload.get("name")
        image_b64 = payload.get("image_b64")
        if not isinstance(name, str) or not name.strip():
            self._send_error_json(400, "'name' must be a non-empty string")
            return
        if not isinstance(image_b64, str) or not image_b64.strip():
            self._send_error_json(400, "'image_b64' must be a non-empty string")
            return
        cols = payload.get("cols")
        rows = payload.get("rows")
        if cols is not None and (isinstance(cols, bool) or not isinstance(cols, int)):
            self._send_error_json(400, "'cols' must be an integer")
            return
        if rows is not None and (isinstance(rows, bool) or not isinstance(rows, int)):
            self._send_error_json(400, "'rows' must be an integer")
            return
        dark_is_wall = payload.get("dark_is_wall", True)
        if not isinstance(dark_is_wall, bool):
            self._send_error_json(400, "'dark_is_wall' must be a boolean")
            return

        try:
            image_bytes = base64.b64decode(
                image_b64, validate=True
            )  # raises binascii.Error (a ValueError) on bad base64
        except ValueError as exc:
            self._send_error_json(400, f"bad base64: {exc}")
            return

        try:
            grid = detect_grid(
                image_bytes,
                name=name.strip(),
                cols=cols,
                rows=rows,
                dark_is_wall=dark_is_wall,
            )
        except ValueError as exc:
            # decode_image / decode / detect failures (bad signature, bad
            # PNG/BMP structure, bad grid dimensions) — not an image.
            self._send_error_json(400, str(exc))
            return

        slug = slug_map_id(name.strip())
        map_id = _unique_map_id(slug) if slug else _timestamp_map_id()
        grid.image = name.strip()
        _register_map(map_id, grid)

        self._send_json(
            200,
            {
                "id": map_id,
                "name": grid.name,
                "width": grid.width,
                "height": grid.height,
                "cells": grid.cells,
                "thumbnail": grid_to_thumbnail_png(grid),
            },
        )

    def _handle_paint(self, map_id: str) -> None:
        """``POST /api/maps/{id}/paint`` (PROJECT.md §8) — GM cell edit.

        JSON body: ``{"x": int, "y": int, "cell_type": "floor"|"wall"|
        "doorway"}``. 404 unknown map; 400 bad body / out of bounds / bad
        cell type; 200 ``{"ok": true, "x", "y", "cell_type"}``.
        """
        body = self._read_body()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(400, "request body must be JSON")
            return
        if not isinstance(payload, dict):
            self._send_error_json(400, "request body must be a JSON object")
            return
        x = payload.get("x")
        y = payload.get("y")
        cell_type = payload.get("cell_type")
        if isinstance(x, bool) or not isinstance(x, int):
            self._send_error_json(400, "'x' must be an integer")
            return
        if isinstance(y, bool) or not isinstance(y, int):
            self._send_error_json(400, "'y' must be an integer")
            return
        if cell_type not in CELL_TYPES:
            self._send_error_json(
                400,
                f"'cell_type' must be one of {'/'.join(CELL_TYPES)}",
            )
            return

        entry = get_map_entry(map_id)
        if entry is None:
            self._send_error_json(404, "not found")
            return
        grid: Grid = entry["grid"]
        if not (0 <= x < grid.width and 0 <= y < grid.height):
            self._send_error_json(
                400,
                f"({x}, {y}) out of bounds for {grid.width}x{grid.height} grid",
            )
            return

        grid.cells[y][x] = cell_type
        self._send_json(200, {"ok": True, "x": x, "y": y, "cell_type": cell_type})

    def _handle_websocket(self) -> None:
        """Upgrade to a WebSocket, then run the live session message loop.

        ``/ws?session=<id>`` (default ``"default"``) resolves to a
        :class:`app.session.GameSession` (get-or-create, module-level dict).
        The client drives the protocol: it sends ``{type:"join",name,role}``
        first, and ``session.handle_message`` replies with the per-viewer
        welcome / errors and broadcasts per-viewer state on every mutation.
        """
        try:
            query = urlsplit(self.path).query
            params = parse_qs(query)
            session_id = (params.get("session") or ["default"])[0] or "default"
        except ValueError:
            session_id = "default"
        try:
            sock = ws_accept(self)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        session = get_session(session_id)

        def _on_close(closed_sock) -> None:
            # Teardown: detach this socket from the session (the Player slot
            # is kept for reconnects).
            session.detach(closed_sock)

        # BUG-005: hand the connection's per-socket send lock to ws_serve so
        # the per-client reply it sends is serialised by the SAME lock the
        # session's _broadcast uses — a reply and a broadcast to the same
        # socket can never interleave and corrupt a frame.
        ws_serve(
            sock,
            on_message=session.handle_message,
            on_close=_on_close,
            lock_for=lambda: session._send_lock_for(sock),
        )
        self.close_connection = True

    def _serve_static(self, path: str) -> None:
        """Serve a file from ``app/static`` (mounted last: API routes win)."""
        rel = path.lstrip("/")
        if not rel or rel == "/":
            rel = "index.html"
        # Resolve and confine to STATIC_DIR (no path traversal).
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not (full == os.path.realpath(STATIC_DIR)
                or full.startswith(os.path.realpath(STATIC_DIR) + os.sep)):
            self._send_error_json(404, "not found")
            return
        if not os.path.isfile(full):
            self._send_error_json(404, "not found")
            return
        content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # Quieter logs in tests; keep them in production.
    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("LITTLEDUNGEONS_QUIET_LOGS"):
            return
        super().log_message(fmt, *args)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Create the ThreadingHTTPServer (port 0 → pick a free port; tests use
    this to spin up an isolated instance on an ephemeral port)."""
    httpd = ThreadingHTTPServer((host, port), LittleDungeonsHandler)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LittleDungeons — stdlib-only server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    httpd = make_server(args.host, args.port)
    print(f"LittleDungeons listening on http://{args.host}:{httpd.server_address[1]}")
    print("  UI:     http://%s:%d/" % (args.host, httpd.server_address[1]))
    print("  Health: http://%s:%d/health" % (args.host, httpd.server_address[1]))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
