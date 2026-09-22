"""LittleDungeons authoritative session (PROJECT.md §5, §6, §9) — pure stdlib.

:class:`GameSession` owns the live, authoritative state of one session:

* the map :class:`~app.models.Grid` (the SAME object as the
  ``maps_registry`` entry in ``app.main`` — ``paint`` mutates it in place so
  the REST and WS paths see the same grid),
* the entities and the connected players,
* permission enforcement (PROJECT.md §6: a player may only move their OWN
  entity and only with ``override`` falsy; ``override`` and all GM tools are
  GM-only; exactly 1 GM + up to 6 players),
* per-viewer state snapshots: the awareness overlay is computed per viewer
  (PROJECT.md §5 — for players the three-tier visibility model: line-of-sight
  entities are shown in FULL (name, kind, color, labeled); nearby entities
  without line of sight (within ``APPROX_RADIUS`` squares) are shown only as
  APPROXIMATE quantized blocks; everything else is invisible.  The GM sees
  everything, labeled, with no filtering).

Threading model: all state reads/writes run under the session's
:class:`~threading.RLock`; the session itself is SYNCHRONOUS and is called
from the uvicorn event-loop thread (``app/server.py`` bridges it: the WS
endpoint calls ``handle_message`` inline on the loop — deliberately NOT via
``to_thread`` — and only the blocking REST map I/O is offloaded off the loop
via ``asyncio.to_thread``). Outbound JSON for a given connection goes through
an ASYNC
SENDER coroutine bound to that connection (registered by the server via
:meth:`attach_async`): uvicorn serialises sends per WebSocket connection,
so no per-connection send lock is needed — a broadcast can never interleave
with the reply a handler sends to the requesting client.

Message handling is deliberately forgiving: a missing/bad field produces
``{"type": "error", "message": ...}`` addressed to the sender — it never
crashes the connection (the frontend drives this directly).
"""

from __future__ import annotations

import itertools
import threading
from typing import TYPE_CHECKING, Any

from app.models import Grid

if TYPE_CHECKING:  # annotation-only (lazy under `from __future__ import annotations`)
    from app.models import Entity, Player

from app.session_actions import SessionActionMixin
from app.session_common import (
    CREATABLE_KINDS, DOOR_ACTIONS, HOSTILE_ON_SAFE_DOOR, MAX_PLAYERS,
    NO_ROUTE, NOT_ALLOWED, SAFE_DOOR_ACTIONS, SESSION_FULL, UNKNOWN_TYPE,
    _as_int, _schedule,
)
from app.session_conn import SessionConnMixin
from app.session_doors import SessionDoorMixin
from app.session_map import SessionMapMixin
from app.session_state import SessionStateMixin


class GameSession(SessionConnMixin, SessionStateMixin, SessionDoorMixin,
                  SessionActionMixin, SessionMapMixin):
    """Authoritative, thread-safe state for one live session."""

    def __init__(self, session_id: str, grid: Grid) -> None:
        self.id = session_id
        self.grid = grid
        self.entities: dict[str, Entity] = {}
        self.players: dict[str, Player] = {}

        self._lock = threading.RLock()
        self._socks: dict[str, Any] = {}              # player id -> connection (WebSocket)
        self._cid_by_sock: dict[int, str] = {}        # id(connection) -> client id
        self._senders: dict[str, Any] = {}            # client id -> async send coroutine
        self._client_seq = itertools.count(1)         # reconnect-proof client ids
        # Explored-map (docs/design/explored-map.md §3.3): per-player memory
        # of cells ever in line of sight on the CURRENT map. Session-level on
        # purpose (NOT a Player field) so the ``players[]`` wire shape stays
        # byte-identical; keyed by player id, which is stable across
        # reconnects (memory survives disconnect/re-attach). Lifecycle:
        # created lazily on first sight; folded on every recompute; frozen
        # for token-less players; cleared on ``use_map`` (D3); pruned on
        # ``leave`` (D6). GMs never get an entry (D4).
        self._explored: dict[str, set[tuple[int, int]]] = {}
        # stage 5b: the grid wire-form cache — a (grid.revision, wire-dict)
        # pair, rebuilt only when the grid object or its revision changes
        # (every paint / door / safe mutation bumps grid.revision via
        # Grid.bump_revision; use_map installs a fresh object at revision 0),
        # so an unchanged grid serializes once and is reused per snapshot.
        self._grid_wire: tuple[int, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Message handling (§9) — returns a reply for THIS client or None
    # ------------------------------------------------------------------

    def handle_message(self, conn: Any, msg: Any) -> dict[str, Any] | None:
        """Handle one decoded client message. Never raises on bad input.

        STAYS SYNCHRONOUS on purpose: the in-process unit tests
        (``tests/test_session.py``) drive it directly with fake sockets. It
        is called from the uvicorn event-loop thread — the WS endpoint runs
        it inline on the loop (deliberately NOT via ``to_thread``: the
        broadcast scheduling needs the running loop, and the RLock is held
        only milliseconds), so the blocking RLock serialises all state
        access; the REST handlers offload only their blocking *I/O* (file
        reads/writes) to a worker thread via ``asyncio.to_thread``. For
        message types that
        broadcast (``join`` and all mutations), the async broadcast coroutine
        is scheduled on the running event loop (it is created on that loop,
        so awaiting the senders inside is valid); when no loop is running
        (the FakeSock unit-test context, which registers no senders) it is
        dropped. A per-client reply, when one exists, is returned for the
        caller to send.
        """
        if not isinstance(msg, dict):
            return {"type": "error", "message": UNKNOWN_TYPE}
        mtype = msg.get("type")
        if not isinstance(mtype, str):
            return {"type": "error", "message": UNKNOWN_TYPE}

        if mtype == "join":
            # Join validation + role assignment is SYNCHRONOUS: a refused
            # join (empty name, bad role, "session full", a 2nd GM) must
            # still produce a per-client ERROR reply for the caller to send,
            # so it cannot be hidden inside a fire-and-forget coroutine
            # (a scheduled _on_join would drop that reply — see BUG-005-era
            # join handling). Only the SUCCESSFUL welcome broadcast is async
            # (it awaits the per-connection senders), so it is scheduled on
            # the running event loop — or dropped when no loop is running
            # (the FakeSock unit-test context, which registers no senders).
            player, err_reply = self._on_join(conn, msg)
            if err_reply is not None:
                return err_reply
            if player is not None:
                _schedule(self._announce_join(conn, player))
            return None
        if mtype == "request_state":
            player = self.player_for_sock(conn)
            if player is None:
                return {"type": "error", "message": "join first"}
            with self._lock:
                return self.state_for(player)

        player = self.player_for_sock(conn)
        if player is None:
            return {"type": "error", "message": "join first"}
        is_gm = player.role == "gm"

        if mtype == "move":
            return self._on_move(player, is_gm, msg)
        if mtype == "place":
            return self._gm_only(is_gm, lambda: self._on_place(msg))
        if mtype == "create_entity":
            return self._gm_only(is_gm, lambda: self._on_create_entity(msg))
        if mtype == "delete_entity":
            return self._gm_only(is_gm, lambda: self._on_delete_entity(msg))
        if mtype == "set_team":
            return self._gm_only(is_gm, lambda: self._on_set_team(msg))
        if mtype == "set_awareness":
            return self._gm_only(is_gm, lambda: self._on_set_awareness(msg))
        if mtype == "paint":
            return self._gm_only(is_gm, lambda: self._on_paint(msg))
        if mtype == "use_map":
            return self._gm_only(is_gm, lambda: self._on_use_map(msg))
        if mtype == "door":
            return self._on_door(player, is_gm, msg)
        if mtype == "safe_door":
            return self._on_safe_door(player, is_gm, msg)
        return {"type": "error", "message": UNKNOWN_TYPE}

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _run_b(coro: Any) -> None:
        """Schedule an async broadcast from a sync handler.

        Runs on the uvicorn event-loop thread (the WS endpoint) → create a
        task there; no running loop (FakeSock unit tests, which register no
        senders) → drop the coroutine (it would yield zero frames anyway).
        """
        _schedule(coro)

    @staticmethod
    def _gm_only(is_gm: bool, action: Any) -> dict[str, Any] | None:
        """Run ``action()`` for a GM; answer ``not allowed`` to everyone else."""
        if not is_gm:
            return {"type": "error", "message": NOT_ALLOWED}
        return action()
