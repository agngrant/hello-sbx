"""LittleDungeons application entry point (PROJECT.md §2, §8, §9).

Stack: **FastAPI + uvicorn** (``app/server.py``) — the old
``http.server.ThreadingHTTPServer`` + ``BaseHTTPRequestHandler`` implementation
was deleted per ``docs/reviews/simplification-review.md`` rows 3+4 (RFC 6455
now served natively by uvicorn via the ``websockets`` library).

External surface (identical to the old stdlib server):

  * ``GET /health``       — liveness probe
  * ``GET /api/maps``     — list maps (summaries)
  * ``GET /api/maps/{id}``— map detail (grid + entities/players)
  * ``GET /ws``           — RFC 6455 upgrade; Iteration 5: get-or-create an
                           ``app.session.GameSession`` for ``?session=<id>``
                           (default ``"default"``) and run the session message
                           loop
  * ``POST /api/maps/upload`` — JSON body ``{"name", "image_b64",
    "cols"?, "rows"?, "dark_is_wall"?}`` → base64 →
    ``app.detection.detect_grid`` → register → grid + PNG data-URL thumbnail
    (PROJECT.md §7)
  * ``POST /api/maps/{id}/paint`` — ``{"x", "y", "cell_type"}`` GM cell edit
  * ``GET /`` + other     — static frontend from ``app/static`` (mounted last
                            so the API routes win)

This module is the **authoritative in-memory state** of the server process:
the maps registry, the live per-session ``GameSession`` registry, and the map
id helpers. ``app/server.py`` imports this state at module level, so the state
is defined in the first part of this file and the FastAPI app (``app.server``)
is imported only LAZILY, inside :func:`main` — which keeps the import graph
acyclic in both directions.

``app.server`` also provides the ``ThreadingHTTPServer``-shaped test adapter
and ``app`` (the FastAPI application); tests and ``scripts/e2e_proof.py``
import those from ``app.server`` directly.

Run:  ``python -m app.main --host 127.0.0.1 --port 8000``
"""

from __future__ import annotations

import argparse
import os
import re
import threading
import time
from typing import Any

from app.grid import SAMPLE_MAP_ID, build_sample_map
from app.models import Grid
from app.session import GameSession

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

MAX_BODY = 32 * 1024 * 1024  # upload ceiling (enforced by app/server.py)


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
# CLI entry point (``python -m app.main`` → main → app.server.run_server)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the FastAPI/uvicorn server in the foreground (blocking).

    The FastAPI app itself lives in ``app/server.py``; this is only the CLI
    wrapper so ``run.sh`` / ``python -m app.main`` keeps its entry point.
    ``app.server`` is imported here (lazily) so importing ``app.main`` for
    the state registry (``tests/test_session.py`` etc.) does not pay the
    FastAPI/uvicorn import cost — and because ``app.server`` imports this
    module's state at ITS module level, a module-level import here would be
    circular.
    """
    from app.server import run_server  # noqa: WPS433 — see docstring

    parser = argparse.ArgumentParser(
        description="LittleDungeons — FastAPI/uvicorn server"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
