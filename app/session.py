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
  (PROJECT.md §5 — radar passes through walls; the GM sees everything,
  labeled) and, when fog-of-war is on, filtered by line of sight (the GM is
  never fogged; once-seen entities stay visible — §5 "previously seen").

Threading model (PROJECT.md §2: ``ThreadingHTTPServer`` gives one read
thread per connection): all state reads/writes run under the session's
:class:`~threading.RLock`; outbound JSON for a given connection is protected
by a per-connection :class:`~threading.Lock`, so at most one send is in
flight to a given socket at a time and a broadcast can never interleave with
the reply a handler sends to the requesting client.

Message handling is deliberately forgiving: a missing/bad field produces
``{"type": "error", "message": ...}`` addressed to the sender — it never
crashes the connection (the frontend drives this directly).
"""

from __future__ import annotations

import itertools
import logging
import threading
from typing import Any

from app.awareness import build_awareness
from app.models import CELL_TYPES, TEAMS, Entity, Grid, Player
from app.pathfinding import find_path, has_line_of_sight

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


def _as_int(value: Any) -> int | None:
    """Coerce a JSON int; reject bools and non-integers (→ ``None``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _send_json(sock: Any, obj: dict[str, Any]) -> None:
    """Best-effort JSON text frame on ``sock`` (never raises)."""
    from app.ws import send_json

    try:
        send_json(sock, obj)
    except Exception:
        pass  # dead/broken socket — teardown detaches the connection


class GameSession:
    """Authoritative, thread-safe state for one live session."""

    def __init__(self, session_id: str, grid: Grid) -> None:
        self.id = session_id
        self.grid = grid
        self.entities: dict[str, Entity] = {}
        self.players: dict[str, Player] = {}
        self.fog: bool = False

        self._lock = threading.RLock()
        self._socks: dict[str, Any] = {}              # player id -> socket
        self._cid_by_sock: dict[int, str] = {}        # id(sock) -> client id
        self._send_locks: dict[str, threading.Lock] = {}  # client id -> lock
        self._client_seq = itertools.count(1)         # reconnect-proof client ids
        self._seen: dict[str, set[str]] = {}          # player id -> entity ids seen while LOS held

    # ------------------------------------------------------------------
    # Connection bookkeeping
    # ------------------------------------------------------------------

    def _client_id_for(self, sock: Any) -> str:
        """Assign (or return) the client id for ``sock``. Lock held."""
        key = id(sock)
        cid = self._cid_by_sock.get(key)
        if cid is None:
            cid = f"c{next(self._client_seq)}"
            self._cid_by_sock[key] = cid
            self._send_locks[cid] = threading.Lock()
        return cid

    def _send_lock_for(self, sock: Any) -> threading.Lock | None:
        """The per-connection send lock for ``sock`` (lock held by caller)."""
        cid = self._cid_by_sock.get(id(sock))
        return self._send_locks.get(cid) if cid else None

    def detach(self, sock: Any) -> None:
        """Drop per-connection bookkeeping (called on socket teardown).

        The Player (and any entity it owns) stays in the session — this is a
        disconnect, not a leave; a reconnecting client re-attaches.
        """
        with self._lock:
            key = id(sock)
            if key not in self._cid_by_sock:
                return
            cid = self._cid_by_sock.pop(key)
            for pid, s in list(self._socks.items()):
                if id(s) == key:
                    del self._socks[pid]
                    break
            self._send_locks.pop(cid, None)

    def player_for_sock(self, sock: Any) -> Player | None:
        """The Player bound to ``sock`` (None when it has not joined yet)."""
        with self._lock:
            cid = self._cid_by_sock.get(id(sock))
            if cid is None:
                return None
            for pid, s in self._socks.items():
                if id(s) == id(sock):
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
                    self._seen.setdefault(pid, set())
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
                eid = f"e{len(self.entities) + 1}"
                while eid in self.entities:
                    eid = f"e{len(self.entities) + 1}"
                entity = Entity(
                    id=eid, name=name, kind="player", team="party",
                    x=x, y=y, owner=pid,
                )
                self.entities[eid] = entity
                player.entity_id = eid

            self._seen.setdefault(pid, set())

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
            self._seen.pop(player_id, None)

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
        """Awareness items for ``viewer`` with the fog filter applied.

        Fog off (default): the full radar — awareness passes through walls.
        Fog on: a *player* only sees entities with clear line of sight from
        their own entity; the **GM is never fogged** (and has no own entity
        to anchor LOS at — the role-exempt early return below applies). A
        per-player "previously seen" set keeps once-seen entities visible.
        """
        items = build_awareness(viewer, self.entities)
        if not self.fog or viewer.role == "gm":
            return items
        own = self.entities.get(viewer.entity_id) if viewer.entity_id else None
        if own is None:
            # A player whose entity was deleted cannot anchor LOS: they see
            # nothing (and nothing is newly marked as seen).
            return []
        seen = self._seen.setdefault(viewer.id, set())
        visible: list[dict[str, Any]] = []
        for item in items:
            entity = self.entities.get(item["entity_id"])
            if entity is None:
                continue
            has_los = has_line_of_sight(self.grid, (own.x, own.y), (entity.x, entity.y))
            if has_los:
                seen.add(entity.id)
            if has_los or entity.id in seen:
                visible.append(item)
        return visible

    def state_for(self, viewer: Player) -> dict[str, Any]:
        """The §9 ``state`` payload *as seen by* ``viewer``.

        ``entities`` is the full list for a GM and ``[]`` for a player
        (players get only ``awareness``). ``you_entity`` (additive field)
        carries a player's own character dict — which ``build_awareness``
        excludes — so the client can render its own token; it is ``None``
        for the GM, which has no entity at all.
        """
        is_gm = viewer.role == "gm"
        own = self.entities.get(viewer.entity_id) if viewer.entity_id else None
        return {
            "type": "state",
            "map": self.grid.to_dict(),
            "players": [p.to_dict() for p in self.players.values()],
            "entities": [e.to_dict() for e in self.entities.values()] if is_gm else [],
            "you_entity": own.to_dict() if (own is not None and not is_gm) else None,
            "awareness": self._awareness_for(viewer),
            "fog": self.fog,
        }

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

    def _broadcast(self, extra: dict[str, Any] | None = None) -> None:
        """Send each connected player their ``state_for`` snapshot.

        Called AFTER a mutation has been applied (caller holds the lock).
        Each connection optionally receives ``extra`` frames first (e.g. the
        ``path`` message of a successful move), then their own snapshot —
        the awareness differs per player, so the snapshot is per-viewer.
        All payloads are computed under the session lock (snapshot) and sent
        outside it, so a slow socket can never block the state mutation.
        """
        with self._lock:
            targets = []
            for pid, sock in self._socks.items():
                viewer = self.players.get(pid)
                if viewer is None:
                    continue
                lock = self._send_lock_for(sock)
                if lock is None:
                    continue
                targets.append((sock, lock, extra, self.state_for(viewer)))
        for sock, lock, extra_frame, payload in targets:
            with lock:
                if extra_frame is not None:
                    _send_json(sock, extra_frame)
                _send_json(sock, payload)

    def _announce_join(self, sender_sock: Any, player: Player) -> None:
        """Welcome the joiner; give everyone else their own snapshot."""
        with self._lock:
            welcome = self.welcome_for(player)
            targets = []
            for pid, sock in self._socks.items():
                viewer = self.players.get(pid)
                if viewer is None:
                    continue
                lock = self._send_lock_for(sock)
                if lock is None:
                    continue
                payload = welcome if sock is sender_sock else self.state_for(viewer)
                targets.append((sock, lock, payload))
        for sock, lock, payload in targets:
            with lock:
                _send_json(sock, payload)

    # ------------------------------------------------------------------
    # Message handling (§9) — returns a reply for THIS client or None
    # ------------------------------------------------------------------

    def handle_message(self, sock: Any, msg: Any) -> dict[str, Any] | None:
        """Handle one decoded client message. Never raises on bad input."""
        if not isinstance(msg, dict):
            return {"type": "error", "message": UNKNOWN_TYPE}
        mtype = msg.get("type")
        if not isinstance(mtype, str):
            return {"type": "error", "message": UNKNOWN_TYPE}

        if mtype == "join":
            return self._on_join(sock, msg)
        if mtype == "request_state":
            player = self.player_for_sock(sock)
            if player is None:
                return {"type": "error", "message": "join first"}
            with self._lock:
                return self.state_for(player)

        player = self.player_for_sock(sock)
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
        if mtype == "paint":
            return self._gm_only(is_gm, lambda: self._on_paint(msg))
        if mtype == "set_fog":
            return self._gm_only(is_gm, lambda: self._on_set_fog(msg))
        if mtype == "use_map":
            return self._gm_only(is_gm, lambda: self._on_use_map(msg))
        return {"type": "error", "message": UNKNOWN_TYPE}

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _gm_only(is_gm: bool, action: Any) -> dict[str, Any] | None:
        """Run ``action()`` for a GM; answer ``not allowed`` to everyone else."""
        if not is_gm:
            return {"type": "error", "message": NOT_ALLOWED}
        return action()

    def _on_join(self, sock: Any, msg: dict[str, Any]) -> dict[str, Any] | None:
        name = msg.get("name")
        role = msg.get("role")
        if role is not None and not isinstance(role, str):
            return {"type": "error", "message": "role must be a string"}
        with self._lock:
            player, err = self.join(sock, name, role)
            if err is None and player is not None:
                self._announce_join(sock, player)
        if err is not None:
            return {"type": "error", "message": err}
        return None  # the welcome is already on the wire (sent directly)

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
            self._broadcast(extra=frame)
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
            self._broadcast()
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
            eid = f"e{len(self.entities) + 1}"
            while eid in self.entities:
                eid = f"e{len(self.entities) + 1}"
            self.entities[eid] = Entity(
                id=eid, name=name.strip(), kind=kind, team=team,
                x=x, y=y, owner=None,
            )
            self._broadcast()
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
            self._broadcast()
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
            self._broadcast()
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
            self._broadcast()
        return None

    def _on_set_fog(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self.fog = bool(msg.get("on", False))
            self._broadcast()
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
            self._broadcast()
        return None
