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
from the uvicorn event-loop thread (``app/server.py`` bridges it: WS I/O is
async, message handling runs in a worker thread via ``starlette.concurrency
to_thread``). Outbound JSON for a given connection goes through an ASYNC
SENDER coroutine bound to that connection (registered by the server via
:meth:`attach_async`): uvicorn serialises sends per WebSocket connection,
so no per-connection send lock is needed — a broadcast can never interleave
with the reply a handler sends to the requesting client.

Message handling is deliberately forgiving: a missing/bad field produces
``{"type": "error", "message": ...}`` addressed to the sender — it never
crashes the connection (the frontend drives this directly).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from typing import Any

from app.awareness import AWARENESS_MAX, AWARENESS_MIN, build_awareness
from app.models import CELL_TYPES, TEAMS, Entity, Grid, Player
from app.pathfinding import find_path
from app.visibility import build_visibility_mask, visible_cells

logger = logging.getLogger(__name__)

#: §6/§8: exactly one GM and up to six players per session.
MAX_PLAYERS = 6

#: The error message a joiner gets when the session is full (PROJECT.md §8:
#: "further joins get ``{type:"error", message:"session full"}``").
SESSION_FULL = "session full"

#: §6 rejection for an unreachable destination without override.
NO_ROUTE = "no route — wall in the way"

#: §9: sent for anything the server does not understand.
UNKNOWN_TYPE = "unknown message type"

#: §9: sent when a non-GM tries a GM-only operation, or a non-owner tries to
#: move somebody else's entity.
NOT_ALLOWED = "not allowed"

#: Entity kinds a GM may create (player characters are spawned by ``join``;
#: the GM itself has NO token — docs/design/gm-controller.md §2.3).
CREATABLE_KINDS = ("npc", "enemy")

#: Door actions (docs/design/door-features.md §4/§18): the client→server
#: ``{type:"door", x, y, action}`` message. ``unlock``/``lock`` are GM-only;
#: ``open``/``close`` are allowed for any client while the door is unlocked.
DOOR_ACTIONS = ("unlock", "lock", "open", "close")


def _as_int(value: Any) -> int | None:
    """Coerce a JSON int; reject bools and non-integers (→ ``None``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _schedule(frame_coro: Any) -> None:
    """Schedule an outbound frame from a SYNCHRONOUS context.

    Called from ``handle_message`` (which stays synchronous so the in-process
    unit tests can drive it directly) while running on the uvicorn event-loop
    thread: hand the coroutine off to that loop as a task. When no event loop
    is running (the ``tests/test_session.py`` FakeSock context, where the
    session has no async senders registered) the coroutine is simply dropped
    — the senders list is empty there, so there is nothing to send.
    """
    try:
        asyncio.get_running_loop().create_task(frame_coro)
    except RuntimeError:
        coro = frame_coro
        if asyncio.iscoroutine(coro):
            coro.close()


class GameSession:
    """Authoritative, thread-safe state for one live session."""

    def __init__(self, session_id: str, grid: Grid) -> None:
        self.id = session_id
        self.grid = grid
        self.entities: dict[str, Entity] = {}
        self.players: dict[str, Player] = {}
        self.fog: bool = False

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

    # ------------------------------------------------------------------
    # Connection bookkeeping
    # ------------------------------------------------------------------

    def _client_id_for(self, conn: Any) -> str:
        """Assign (or return) the client id for ``conn``. Lock held."""
        key = id(conn)
        cid = self._cid_by_sock.get(key)
        if cid is None:
            cid = f"c{next(self._client_seq)}"
            self._cid_by_sock[key] = cid
        return cid

    def _sender_for(self, conn: Any) -> Any:
        """The async sender coroutine for ``conn`` (lock held by caller)."""
        cid = self._cid_by_sock.get(id(conn))
        return self._senders.get(cid) if cid else None

    def attach_async(self, conn: Any, send_coro: Any) -> None:
        """Register the ASYNC SENDER for a live WebSocket connection.

        ``send_coro`` is an ``async def send(obj) -> None`` coroutine bound to
        this connection (the server wires it to ``websocket.send_text`` under
        the session lock). uvicorn serialises sends per WebSocket connection
        (one send task per connection), so no per-connection send lock is
        needed anymore: a broadcast to this client and the per-client reply
        can never interleave and corrupt a frame (BUG-005 by construction).

        ``conn`` is the stable per-connection identity (the starlette
        ``WebSocket`` object); the same identity flows through ``join`` /``handle_message`` / ``player_for_sock`` / ``detach``.
        """
        with self._lock:
            self._senders[self._client_id_for(conn)] = send_coro

    def detach(self, conn: Any) -> None:
        """Drop per-connection bookkeeping (called on connection teardown).

        The Player (and any entity it owns) stays in the session — this is a
        disconnect, not a leave; a reconnecting client re-attaches.
        """
        with self._lock:
            key = id(conn)
            if key not in self._cid_by_sock:
                return
            cid = self._cid_by_sock.pop(key)
            self._senders.pop(cid, None)
            for pid, c in list(self._socks.items()):
                if id(c) == key:
                    del self._socks[pid]
                    break

    def player_for_sock(self, conn: Any) -> Player | None:
        """The Player bound to ``conn`` (None when it has not joined yet)."""
        with self._lock:
            cid = self._cid_by_sock.get(id(conn))
            if cid is None:
                return None
            for pid, c in self._socks.items():
                if id(c) == id(conn):
                    return self.players.get(pid)
            return None

    # ------------------------------------------------------------------
    # Joins (PROJECT.md §8)
    # ------------------------------------------------------------------

    def join(self, sock: Any, name: str, role: str | None) -> tuple[Player | None, str | None]:
        """Register ``sock`` in the session.

        Returns ``(player, None)`` on success or ``(None, error)``. Rules:

        * The **first** client to send ``role:"gm"`` becomes the GM; the very
          first client of a fresh session (even without an explicit role)
          becomes the GM automatically.
        * A second GM is refused, as is a 7th non-GM player
          (``"session full"``) — refused clients are NOT added.
        * A player gets a starting Entity (kind ``"player"``, team
          ``"party"``, ``owner`` = the player id) on a free floor cell.
          The **GM is a pure controller: it gets NO entity** (``entity_id``
          stays ``None``, nothing is spawned, no floor is consumed) —
          docs/design/gm-controller.md §2.1/§2.2.
        * Reconnecting with the same name+role re-attaches the existing
          Player (stable id, keeps its entity and position). A reconnecting
          GM has no entity to preserve and none is re-spawned.
        """
        name = (name or "").strip()
        if not name:
            return None, "name required"
        role = (role or "").strip().lower() if isinstance(role, str) else None
        if role not in (None, "gm", "player"):
            return None, "role must be 'gm' or 'player'"

        with self._lock:
            # Reconnect: re-attach this socket to the same Player.
            for pid, player in self.players.items():
                if player.name == name and (role is None or player.role == role):
                    self._socks[pid] = sock
                    self._client_id_for(sock)
                    return player, None

            gm_exists = any(p.role == "gm" for p in self.players.values())
            n_players = sum(1 for p in self.players.values() if p.role == "player")

            # Decide this joiner's effective role (§8 role assignment).
            if not self.players:
                effective_role = "gm"  # first client of a fresh session
            elif role == "gm":
                effective_role = "gm"
            elif not gm_exists:
                effective_role = "gm"  # no GM yet: next joiner becomes GM
            else:
                effective_role = "player"

            if effective_role == "gm":
                if gm_exists:
                    return None, SESSION_FULL  # 2nd GM → refused
            elif n_players >= MAX_PLAYERS:
                return None, SESSION_FULL      # 7th non-GM → refused

            pid = f"p{len(self.players) + 1}"
            player = Player(id=pid, name=name, role=effective_role, entity_id=None)
            self.players[pid] = player

            if effective_role == "player":
                # Players only get a starting token (the GM is a pure
                # controller — it never gets one).
                x, y = self._find_free_floor()
                n = len(self.entities)
                eid = f"e{n + 1}"
                while eid in self.entities:
                    n += 1
                    eid = f"e{n + 1}"
                entity = Entity(
                    id=eid, name=name, kind="player", team="party",
                    x=x, y=y, owner=pid,
                )
                self.entities[eid] = entity
                player.entity_id = eid

            self._socks[pid] = sock
            self._client_id_for(sock)
            return player, None

    def leave(self, player_id: str) -> None:
        """Remove a player and their owned entity entirely (full exit).

        A GM has ``entity_id is None`` and simply drops its Player record —
        no entity is ever removed (there is nothing to remove).
        """
        with self._lock:
            player = self.players.get(player_id)
            if player is None:
                return
            if player.entity_id and player.entity_id in self.entities:
                del self.entities[player.entity_id]
            del self.players[player_id]
            self._socks.pop(player_id, None)
            self._explored.pop(player_id, None)

    def _find_free_floor(self) -> tuple[int, int]:
        """First free (row-major) floor/doorway cell, else the first in-bounds
        cell that is not a wall, else ``(1, 1)``.

        A spawn is better than a join failure, but we must never *deliberately*
        place an entity on a wall when some walkable cell exists. So when no
        free floor/doorway cell is available (e.g. every walkable cell is
        already occupied), fall back to the first in-bounds non-wall cell. Only
        if the grid is degenerate and has **no** non-wall cell at all (a fully
        walled map) do we return ``(1, 1)`` and log a note; downstream movement
        guards (``find_path`` start-walkable, ``_on_move`` bounds) then keep it
        safe — the entity simply cannot move until the GM paints a floor.
        """
        taken = {(e.x, e.y) for e in self.entities.values()}
        for y in range(self.grid.height):
            for x in range(self.grid.width):
                if (x, y) not in taken and self.grid.cells[y][x] in ("floor", "doorway"):
                    return x, y
        # No free floor/doorway (e.g. every walkable cell is occupied): take
        # the first in-bounds cell that is not a wall so we never land on a
        # wall while a walkable cell still exists.
        for y in range(self.grid.height):
            for x in range(self.grid.width):
                if self.grid.cells[y][x] in ("floor", "doorway"):
                    return x, y
        # Degenerate: the grid has no non-wall cell at all (fully walled).
        logger.warning(
            "session %s: grid %r has no non-wall cell; "
            "falling back to (1, 1)",
            self.id, self.grid.name,
        )
        return 1, 1

    # ------------------------------------------------------------------
    # Per-viewer state (PROJECT.md §5, §9)
    # ------------------------------------------------------------------

    def _awareness_for(self, viewer: Player) -> list[dict[str, Any]]:
        """Awareness items for ``viewer`` (three-tier model, §5).

        Computed by :func:`app.awareness.build_awareness` with the
        session's live grid: for a **player** visibility is purely a
        function of the current positions — direct line of sight → FULL
        item (name, kind, color, labeled); no line of sight but within
        ``APPROX_RADIUS`` squares → APPROXIMATE quantized block (no
        identity); anything else is invisible.  The **GM** is exempt:
        every entity, full info, labeled, no distance/LOS filtering.

        The ``fog`` flag (kept in the state payloads for wire
        compatibility) no longer gates visibility — the model above is
        always active for players and subsumes fog-on; there is no
        "previously seen" memory (that mechanism was removed).
        """
        return build_awareness(viewer, self.entities, self.grid)

    def state_for(self, viewer: Player) -> dict[str, Any]:
        """The §9 ``state`` payload *as seen by* ``viewer``.

        ``entities`` is the full list for a GM and ``[]`` for a player
        (players get only ``awareness``). ``you_entity`` (additive field)
        carries a player's own character dict — which ``build_awareness``
        excludes — so the client can render its own token; it is ``None``
        for the GM, which has no entity at all.

        Explored map (§3.5): for a **player** this is the single choke
        point where the additive ``"visibility"`` tier matrix is computed
        — the player's currently-seen cells are folded into their explored
        set (frozen when the player has no token) and the mask (S/E/H rows
        over the grid) is added to the payload. For the **GM** the key is
        simply never added (D4 — the GM payload is untouched by the
        feature). Every player snapshot (welcome, broadcast, request_state)
        routes through here, all under the session lock.

        Doors (door-features spec §8.1/A9/I5): the additive ``map.doors``
        field carries the FULL door object — every doorway's current state
        (unrecorded doorways default to ``"L"``) — whenever the grid has a
        doorway cell, so the wire is unambiguous (a door open/close
        broadcast reaches every viewer up to date); the key is absent when
        the grid has no doorways (the client ⇒ all locked).
        """
        is_gm = viewer.role == "gm"
        own = self.entities.get(viewer.entity_id) if viewer.entity_id else None
        map_dict = self.grid.to_dict()
        doors_wire = self.grid.doors_for_wire()
        if doors_wire is not None:
            map_dict["doors"] = doors_wire
        payload = {
            "type": "state",
            "map": map_dict,
            "players": [p.to_dict() for p in self.players.values()],
            "entities": [e.to_dict() for e in self.entities.values()] if is_gm else [],
            "you_entity": own.to_dict() if (own is not None and not is_gm) else None,
            "awareness": self._awareness_for(viewer),
            "fog": self.fog,
        }
        if not is_gm:
            payload["visibility"] = self._visibility_for(viewer, own)
        return payload

    def _visibility_for(self, viewer: Player, own: Entity | None) -> list[str]:
        """The additive ``visibility`` tier matrix for a PLAYER (spec §3.3–§3.5).

        ``own`` is the viewer's live entity (``None`` when they have no
        token: ``entity_id`` is ``None`` or the entity was deleted).

        * With a token: ``pos = (own.x, own.y)``; the visible set
          (``visible_cells(grid, pos)``) is folded into the player's
          explored set — memory is monotonic within a map, a cell never
          goes S/E → H — and the mask renders S around the token, E where
          memory reaches, H elsewhere.
        * Without a token: the explored set is FROZEN (nothing new folds in
          — the anchor is gone, so no new sight can be generated) and the
          mask is built with ``pos=None`` → E/H only, no S anywhere.

        Called from :meth:`state_for` (lock held); the fold is per-viewer
        only — no cross-viewer coupling.
        """
        pos = (own.x, own.y) if own is not None else None
        explored = self._explored.setdefault(viewer.id, set())
        if pos is not None:
            visible = visible_cells(self.grid, pos)
            explored |= visible  # idempotent, amortized O(1) per new cell
        else:
            visible = set()      # frozen memory: no new cells are revealed
        return build_visibility_mask(self.grid, explored, pos, visible)

    def welcome_for(self, viewer: Player) -> dict[str, Any]:
        """§9 ``welcome`` = :meth:`state_for` plus ``"you"``."""
        state = self.state_for(viewer)
        state["type"] = "welcome"
        state["you"] = {
            "id": viewer.id,
            "name": viewer.name,
            "role": viewer.role,
            "entity_id": viewer.entity_id,
        }
        return state

    # ------------------------------------------------------------------
    # Broadcasts (per-viewer snapshot; per-connection send lock)
    # ------------------------------------------------------------------

    async def _broadcast(self, extra: dict[str, Any] | None = None) -> None:
        """Send each connected player their ``state_for`` snapshot.

        Called AFTER a mutation has been applied (caller holds the lock).
        Each connection optionally receives ``extra`` frames first (e.g. the
        ``path`` message of a successful move), then their own snapshot —
        the awareness differs per player, so the snapshot is per-viewer.
        All payloads are computed under the session lock (snapshot) and sent
        after releasing it, so a slow socket can never block the state
        mutation. Sends go through each connection's ASYNC SENDER; uvicorn
        serialises sends per connection, so no per-connection send lock is
        needed (BUG-005 by construction).
        """
        with self._lock:
            targets = []
            for pid, conn in self._socks.items():
                viewer = self.players.get(pid)
                if viewer is None:
                    continue
                sender = self._sender_for(conn)
                if sender is None:
                    continue
                targets.append((sender, extra, self.state_for(viewer)))
        for sender, extra_frame, payload in targets:
            if extra_frame is not None:
                await sender(extra_frame)
            await sender(payload)

    async def _announce_join(self, sender_conn: Any, player: Player) -> None:
        """Welcome the joiner; give everyone else their own snapshot."""
        with self._lock:
            welcome = self.welcome_for(player)
            targets = []
            for pid, conn in self._socks.items():
                viewer = self.players.get(pid)
                if viewer is None:
                    continue
                sender = self._sender_for(conn)
                if sender is None:
                    continue
                payload = welcome if conn is sender_conn else self.state_for(viewer)
                targets.append((sender, payload))
        for sender, payload in targets:
            await sender(payload)

    # ------------------------------------------------------------------
    # Message handling (§9) — returns a reply for THIS client or None
    # ------------------------------------------------------------------

    def handle_message(self, conn: Any, msg: Any) -> dict[str, Any] | None:
        """Handle one decoded client message. Never raises on bad input.

        STAYS SYNCHRONOUS on purpose: the in-process unit tests
        (``tests/test_session.py``) drive it directly with fake sockets. It
        is called from the uvicorn event-loop thread (the WS endpoint runs
        it via ``starlette.concurrency.to_thread`` — off the loop, so the
        blocking RLock serialises all state access against the REST
        threadpool and any other session work). For message types that
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
        if mtype == "set_fog":
            return self._gm_only(is_gm, lambda: self._on_set_fog(msg))
        if mtype == "use_map":
            return self._gm_only(is_gm, lambda: self._on_use_map(msg))
        if mtype == "door":
            return self._on_door(player, is_gm, msg)
        return {"type": "error", "message": UNKNOWN_TYPE}

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _send_coro(sender: Any, payload: dict[str, Any]) -> Any:
        """One-shot async sender frame: awaits ``sender(payload)`` and
        swallows transport errors (a dead/broken connection is dropped —
        teardown detaches it), never affecting state."""
        return sender(payload)

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

    def _on_join(self, conn: Any, msg: dict[str, Any]) -> tuple[Player | None, dict[str, Any] | None]:
        """Synchronous join: validate, register, and report.

        Returns ``(player, None)`` on success (the caller then schedules the
        async welcome broadcast via ``_announce_join``) or ``(None, err)``
        where ``err`` is the per-client error dict the caller must send back
        (refused join: empty name, bad role, "session full", a 2nd GM).
        Kept synchronous so the refused-join reply is returned to the caller
        instead of being dropped by an unawaited coroutine.
        """
        name = msg.get("name")
        role = msg.get("role")
        if role is not None and not isinstance(role, str):
            return None, {"type": "error", "message": "role must be a string"}
        with self._lock:
            player, err = self.join(conn, name, role)
        if err is not None:
            return None, {"type": "error", "message": err}
        return player, None

    # -- movement (§6) -------------------------------------------------------

    def _on_move(self, player: Player, is_gm: bool, msg: dict[str, Any]) -> dict[str, Any] | None:
        entity_id = msg.get("entity_id")
        if not isinstance(entity_id, str):
            return {"type": "error", "message": "entity_id required"}
        x = _as_int(msg.get("x"))
        y = _as_int(msg.get("y"))
        if x is None or y is None:
            return {"type": "error", "message": "x and y must be integers"}
        override = bool(msg.get("override", False))

        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                return {"type": "error", "message": "no such entity"}
            if not is_gm:
                # A player may only move their OWN entity, and only with
                # override falsy (§6: "override:true is GM-only").
                if entity.owner != player.id:
                    return {"type": "error", "message": NOT_ALLOWED}
                if override:
                    return {"type": "error", "message": NOT_ALLOWED}
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error", "message": "destination out of bounds"}

            if (x, y) == (entity.x, entity.y):
                # Already there: nothing to do, but confirm to the sender.
                return {"type": "path", "entity_id": entity.id,
                        "path": [{"x": entity.x, "y": entity.y}]}

            if override:
                # GM "ignore walls": direct move to the target, walls ignored.
                entity.x, entity.y = x, y
                path = [{"x": x, "y": y}]
            else:
                path = find_path(self.grid, (entity.x, entity.y), (x, y))
                if path is None:
                    return {"type": "error", "message": NO_ROUTE}
                entity.x, entity.y = x, y
                path = [{"x": px, "y": py} for (px, py) in path]

            # The path frame + the per-viewer state snapshot go to EVERYONE
            # (including the sender, who gets them in this order so its
            # animation starts before the position reconciles). No separate
            # reply is returned for a successful move.
            frame = {"type": "path", "entity_id": entity.id, "path": path}
            self._run_b(self._broadcast(extra=frame))
            return None

    # -- GM tools -------------------------------------------------------------

    def _on_place(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        entity_id = msg.get("entity_id")
        x = _as_int(msg.get("x"))
        y = _as_int(msg.get("y"))
        if not isinstance(entity_id, str):
            return {"type": "error", "message": "entity_id required"}
        if x is None or y is None:
            return {"type": "error", "message": "x and y must be integers"}
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                return {"type": "error", "message": "no such entity"}
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error", "message": "destination out of bounds"}
            entity.x, entity.y = x, y  # GM direct place — walls allowed
            self._run_b(self._broadcast())
        return None

    def _on_create_entity(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        name = msg.get("name")
        kind = msg.get("kind")
        team = msg.get("team")
        x = _as_int(msg.get("x"))
        y = _as_int(msg.get("y"))
        if not isinstance(name, str) or not name.strip():
            return {"type": "error", "message": "name required"}
        if kind not in CREATABLE_KINDS:
            return {"type": "error",
                    "message": f"kind must be one of {'/'.join(CREATABLE_KINDS)}"}
        if team not in TEAMS:
            return {"type": "error",
                    "message": f"team must be one of {'/'.join(TEAMS)}"}
        if x is None or y is None:
            return {"type": "error", "message": "x and y must be integers"}
        with self._lock:
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error", "message": "destination out of bounds"}
            n = len(self.entities)
            eid = f"e{n + 1}"
            while eid in self.entities:
                n += 1
                eid = f"e{n + 1}"
            self.entities[eid] = Entity(
                id=eid, name=name.strip(), kind=kind, team=team,
                x=x, y=y, owner=None,
            )
            self._run_b(self._broadcast())
        return None

    def _on_delete_entity(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        entity_id = msg.get("entity_id")
        if not isinstance(entity_id, str):
            return {"type": "error", "message": "entity_id required"}
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                return {"type": "error", "message": "no such entity"}
            if entity.owner is not None:
                # Don't orphan a connected player: their controlling entity
                # is protected — block with an error (spec: "just block").
                return {"type": "error",
                        "message": "cannot delete a player's own entity"}
            del self.entities[entity_id]
            self._run_b(self._broadcast())
        return None

    def _on_set_team(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        entity_id = msg.get("entity_id")
        team = msg.get("team")
        if not isinstance(entity_id, str):
            return {"type": "error", "message": "entity_id required"}
        if team not in TEAMS:
            return {"type": "error",
                    "message": f"team must be one of {'/'.join(TEAMS)}"}
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                return {"type": "error", "message": "no such entity"}
            entity.team = team
            self._run_b(self._broadcast())
        return None

    def _on_set_awareness(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """GM: set a PLAYER's awareness radius (0–20).

        The GM points at a player icon — a token whose ``owner`` is a
        player id — and sends that token's ``entity_id``; the server
        resolves ``entity.owner`` → the owning :class:`Player` and updates
        its ``awareness_radius`` (the no-LOS approximate tier's range,
        docs/design/awareness-ring.md §3).  Like ``set_team``: no per-
        client reply on success — the ``state`` broadcast carries the new
        value.
        """
        entity_id = msg.get("entity_id")
        if not isinstance(entity_id, str):
            return {"type": "error", "message": "entity_id required"}
        value = _as_int(msg.get("value"))  # rejects bools and non-ints
        if value is None or not (AWARENESS_MIN <= value <= AWARENESS_MAX):
            return {"type": "error",
                    "message": "awareness must be an integer 0–20"}
        with self._lock:
            entity = self.entities.get(entity_id)
            if entity is None:
                return {"type": "error", "message": "no such entity"}
            if entity.owner is None:
                # An NPC/enemy/GM-controlled token has no owning player to
                # edit (docs/design/awareness-ring.md §3.2 step 3).
                return {"type": "error", "message": "not a player token"}
            player = self.players.get(entity.owner)
            if player is None:
                # A connected player's token is protected from deletion, so
                # this cannot actually happen — guard anyway.
                return {"type": "error", "message": "no such entity"}
            player.awareness_radius = value
            self._run_b(self._broadcast())
        return None

    def _on_paint(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        x = _as_int(msg.get("x"))
        y = _as_int(msg.get("y"))
        cell_type = msg.get("cell_type")
        if x is None or y is None:
            return {"type": "error", "message": "x and y must be integers"}
        if cell_type not in CELL_TYPES:
            return {"type": "error",
                    "message": f"cell_type must be one of {'/'.join(CELL_TYPES)}"}
        with self._lock:
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error", "message": "destination out of bounds"}
            # Same primitive as the REST paint route (app.grid.set_cell
            # semantics: bounds-checked, in-place mutation of the grid).
            self.grid.cells[y][x] = cell_type
            # D4 (door-features spec §9): keep door state in sync with the
            # cell type (paint a doorway → door created locked; paint
            # floor/wall over a door → state deleted).
            self.grid.sync_doors_after_cell_set(x, y)
            self._run_b(self._broadcast())
        return None

    def _on_door(self, player: Player, is_gm: bool, msg: dict[str, Any]) -> dict[str, Any] | None:
        """The door state machine + permissions (door-features spec §4).

        A client asks to ``unlock``/``lock``/``open``/``close`` the door on
        the ``doorway`` cell at ``(x, y)``. Validation is the spec's
        deterministic order (AC3), first failure wins:

          1. ``x``/``y`` are ints (bools rejected) → ``"x and y must be
             integers"``
          2. in bounds → ``"destination out of bounds"``
          3. the cell is a ``doorway`` → ``"not a doorway"``
          4. ``action`` is valid → ``"action must be one of unlock/lock/
             open/close"``
          5. the ``(state, action)`` transition is legal → the
             state-specific error
          6. the action is role-allowed (``unlock``/``lock`` are GM-only)
             → ``"not allowed"``
          7. occupancy: a transition that would make the door closed —
             ``close``, and ``lock`` from ``open`` (force-closes) — with a
             token on it is rejected → ``"cannot close a door with a
             token on it"``. This runs AFTER the role check, so a player
             ``lock`` on an open+token door reports ``"not allowed"``.

        On success the state is applied and the ``state`` broadcast carries
        the new ``map.doors`` (no per-client reply, cf. ``paint``). The
        state machine (spec §4.1): ``L ─GM unlock→ U ─open→ O ─close→ U``;
        GM ``lock`` from ``U`` or ``O`` (force-closes ``O``) → ``L``. Players
        may only ``open``/``close`` an UNLOCKED door.
        """
        x = _as_int(msg.get("x"))
        y = _as_int(msg.get("y"))
        if x is None or y is None:
            return {"type": "error", "message": "x and y must be integers"}
        with self._lock:
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error", "message": "destination out of bounds"}
            if self.grid.cells[y][x] != "doorway":
                return {"type": "error", "message": "not a doorway"}
            action = msg.get("action")
            if action not in DOOR_ACTIONS:
                return {"type": "error",
                        "message": "action must be one of unlock/lock/open/close"}
            cur = self.grid.door_state_at(x, y)  # "L" | "U" | "O"
            # Transition legality (before role, so a state failure is the
            # more informative one — spec §4.3). The occupancy guard runs
            # AFTER the role check (§4.3 orders role #6 before occupancy
            # #7): it fires on exactly the transitions that make the door
            # CLOSED (``close``, and GM ``lock`` from ``open`` — A5), never
            # on ``lock`` from ``unlocked`` (already closed).
            if action == "open" and cur == "O":
                return {"type": "error", "message": "door is already open"}
            if action == "open" and cur == "L":
                return {"type": "error", "message": "door is locked"}
            if action == "close" and cur == "L":
                return {"type": "error", "message": "door is locked"}
            if action == "close" and cur != "O":
                return {"type": "error", "message": "door is already closed"}
            if action == "close" and self._any_entity_at(x, y):
                return {"type": "error",
                        "message": "cannot close a door with a token on it"}
            if action == "unlock" and cur != "L":
                return {"type": "error", "message": "door is already unlocked"}
            if action == "lock" and cur == "L":
                return {"type": "error", "message": "door is already locked"}
            # Role: unlock/lock are GM-only (open/close already gated by the
            # locked/unlocked state above, so a locked door reports
            # "door is locked" even for a player). §4.3 orders this BEFORE
            # occupancy, so a player `lock` on an open+token door reports
            # "not allowed", never the occupancy string (BUG-DOORS-002).
            if action in ("unlock", "lock") and not is_gm:
                return {"type": "error", "message": NOT_ALLOWED}
            # Occupancy (A5, §4.3 #7): `lock` from `open` force-closes →
            # same occupancy guard as `close`. Only a GM can reach this —
            # the role check above runs first.
            if action == "lock" and cur == "O" and self._any_entity_at(x, y):
                return {"type": "error",
                        "message": "cannot close a door with a token on it"}
            new_state = {
                ("unlock", "L"): "U",
                ("open", "U"): "O",
                ("close", "O"): "U",
                ("lock", "U"): "L",
                ("lock", "O"): "L",
            }[(action, cur)]
            self.grid.set_door(x, y, new_state)
            self._run_b(self._broadcast())
        return None

    def _any_entity_at(self, x: int, y: int) -> bool:
        """True if any entity occupies ``(x, y)`` (D3 occupancy guard)."""
        return any(e.x == x and e.y == y for e in self.entities.values())

    def _on_set_fog(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        # Wire compatibility: the ``fog`` flag is stored and broadcast, but
        # it no longer gates player visibility — the three-tier model
        # (LOS full / proximity approximate / invisible) is always active.
        with self._lock:
            self.fog = bool(msg.get("on", False))
            self._run_b(self._broadcast())
        return None

    def _on_use_map(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """GM: switch this session to play the map registered as ``map_id``.

        BUG-002: the OLD client implementation switched the *WebSocket session
        id* to the new map's id, which silently created a brand-new session
        for the GM and stranded the players on the old one.  Instead, the GM
        stays in the current session and requests it to swap in the new map.

        Under the session lock this:

        * swaps ``self.grid`` to the target :class:`~app.models.Grid` object
          (the SAME object stored in ``app.main.maps_registry[map_id]`` —
          object identity is shared, so subsequent GM/REST paints still mutate
          the grid everyone sees, and ``paint`` bounds are re-checked against
          the new dimensions),
        * re-places every entity that no longer fits onto a free floor/doorway
          cell (entities, players and fog-of-war state are all kept — no one
          is stranded, the 1-GM + ≤6-players session is preserved), and
        * broadcasts the new per-viewer ``state`` to everyone already
          connected (a late joiner's ``welcome`` picks the grid up from the
          session, so it gets the same map).
        """
        map_id = msg.get("map_id")
        if not isinstance(map_id, str) or not map_id.strip():
            return {"type": "error", "message": "map_id required"}
        grid: Grid | None = None
        try:
            from app.main import maps_registry  # live registry (same process)
            entry = maps_registry.get(map_id.strip())
            if entry is not None:
                grid = entry["grid"]
        except Exception:
            grid = None  # registry unavailable — report below
        if grid is None:
            return {"type": "error", "message": f"unknown map: {map_id!s}"}
        with self._lock:
            self.grid = grid
            # The new grid can be smaller: park anything out of bounds (or on
            # a newly painted wall) on a free floor/doorway cell.
            for e in self.entities.values():
                fits = 0 <= e.x < grid.width and 0 <= e.y < grid.height
                if fits and grid.cells[e.y][e.x] in ("floor", "doorway"):
                    continue
                free = self._find_free_floor()
                e.x, e.y = free
            # Explored map (D3): old cells reference a different map (possibly
            # a different size) — clear EVERY player's memory BEFORE the
            # broadcast, so the post-swap snapshots re-seed from the NEW
            # positions and no stale-coordinate cell can ever render E.
            self._explored.clear()
            self._run_b(self._broadcast())
        return None
