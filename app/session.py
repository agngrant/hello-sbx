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

import asyncio
import itertools
import logging
import threading
from typing import Any

from app.awareness import AWARENESS_MAX, AWARENESS_MIN, build_awareness
from app.models import (
    BOSS_FOOTPRINTS, CELL_TYPES, TEAMS, Entity, Grid, Player,
    boss_footprint_cells, entity_cells, footprint_cells,
)
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
CREATABLE_KINDS = ("npc", "enemy", "boss")

#: Door actions (docs/design/door-features.md §4/§18): the client→server
#: ``{type:"door", x, y, action}`` message. ``unlock``/``lock`` are GM-only;
#: ``open``/``close`` are allowed for any client while the door is unlocked.
DOOR_ACTIONS = ("unlock", "lock", "open", "close")

#: Safe-room door actions (door-iconography spec §4/§18): the client→server
#: ``{type:"safe_door", x, y, action}`` message. WHOLLY GM-only: a safe door
#: is GM-controlled end-to-end (mark/unmark/unlock/lock/open/close) — there
#: is no player path, and safe doors now carry a lock state (``L``/``U``/``O``,
#: same model as normal doors — door-iconography §3, A1).
SAFE_DOOR_ACTIONS = ("mark", "unmark", "unlock", "lock", "open", "close")


class _DoorTransition:
    """One row of the door transition table (door-features spec §4.1).

    ``error``: deterministic first-failure message (``None`` = the transition
    is legal). ``to_state``: the post-transition door state (``None`` when an
    error pre-empts it). ``occupancy_guard``: the transition force-closes the
    door, so a token on it is rejected (A5) — set for ``close`` and GM
    ``lock`` from ``open`` only.
    """

    __slots__ = ("error", "occupancy_guard", "to_state")

    def __init__(
        self,
        error: str | None = None,
        to_state: str | None = None,
        occupancy_guard: bool = False,
    ) -> None:
        self.error = error
        self.to_state = to_state
        self.occupancy_guard = occupancy_guard


#: Door transition table, keyed ``(action, current_state)`` — extracted from
#: the inline branches of ``_on_door`` (stage 4a) so that method becomes a
#: thin dispatcher. The spec's §4.3 evaluation order is preserved by the
#: dispatcher, not the table: state-legality error first, then the GM-only
#: role gate, then the occupancy guard.
DOOR_TRANSITIONS: dict[tuple[str, str], _DoorTransition] = {
    ("unlock", "L"): _DoorTransition(to_state="U"),
    ("unlock", "U"): _DoorTransition(error="door is already unlocked"),
    ("unlock", "O"): _DoorTransition(error="door is already unlocked"),
    ("lock", "L"): _DoorTransition(error="door is already locked"),
    ("lock", "U"): _DoorTransition(to_state="L"),
    ("lock", "O"): _DoorTransition(to_state="L", occupancy_guard=True),
    ("open", "L"): _DoorTransition(error="door is locked"),
    ("open", "U"): _DoorTransition(to_state="O"),
    ("open", "O"): _DoorTransition(error="door is already open"),
    ("close", "L"): _DoorTransition(error="door is locked"),
    ("close", "U"): _DoorTransition(error="door is already closed"),
    ("close", "O"): _DoorTransition(to_state="U", occupancy_guard=True),
}


class _SafeDoorTransition:
    """One row of the safe-door transition table (door-iconography spec §4).

    Mirrors ``_DoorTransition``: ``error`` is the deterministic
    first-failure message (``None`` = legal), ``to_state`` the post-
    transition state (``None`` when an error pre-empts it), and
    ``occupancy_guard`` the force-close token check (``close`` only — a
    GM ``lock`` from ``open`` force-closes unguarded, A14/E11).
    """

    __slots__ = ("error", "occupancy_guard", "to_state")

    def __init__(
        self,
        error: str | None = None,
        to_state: str | None = None,
        occupancy_guard: bool = False,
    ) -> None:
        self.error = error
        self.to_state = to_state
        self.occupancy_guard = occupancy_guard


#: Safe-door (stateful) transition table, keyed ``(action, current_state)``
#: — extracted from the inline branches of ``_on_safe_door`` (stage 4b) so
#: that method becomes a thin dispatcher. ``mark``/``unmark`` are stateless
#: conversions (no ``current_state``), so they are handled in the dispatcher
#: before the table lookup. The spec's §4.3 evaluation order is preserved by
#: the dispatcher, not the table: state-legality error first, then the
#: occupancy guard. (Note: ``(close, "L")`` reports "already closed", not
#: "locked" — the original inline order checked ``close`` against non-``O``
#: states before the lock-specific errors; behavior is preserved verbatim.)
SAFE_DOOR_TRANSITIONS: dict[tuple[str, str], _SafeDoorTransition] = {
    ("unlock", "L"): _SafeDoorTransition(to_state="U"),
    ("unlock", "U"): _SafeDoorTransition(
        error="safe door is already unlocked"),
    ("unlock", "O"): _SafeDoorTransition(
        error="safe door is already unlocked"),
    ("lock", "L"): _SafeDoorTransition(error="safe door is already locked"),
    ("lock", "U"): _SafeDoorTransition(to_state="L"),
    ("lock", "O"): _SafeDoorTransition(to_state="L"),
    ("open", "L"): _SafeDoorTransition(error="safe door is locked"),
    ("open", "U"): _SafeDoorTransition(to_state="O"),
    ("open", "O"): _SafeDoorTransition(error="safe door is already open"),
    ("close", "L"): _SafeDoorTransition(
        error="safe door is already closed"),
    ("close", "U"): _SafeDoorTransition(
        error="safe door is already closed"),
    ("close", "O"): _SafeDoorTransition(
        to_state="U", occupancy_guard=True),
}

#: Safe-room spec §5.2 (D4): the safety-rule rejection — a hostile is never
#: moved/placed/created/team-changed onto a safe-room door cell, even under
#: GM override.
HOSTILE_ON_SAFE_DOOR = "cannot place a hostile on a safe room door"


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
        # stage 5b: the grid wire-form cache — a (grid.revision, wire-dict)
        # pair, rebuilt only when the grid object or its revision changes
        # (every paint / door / safe mutation bumps grid.revision via
        # Grid.bump_revision; use_map installs a fresh object at revision 0),
        # so an unchanged grid serializes once and is reused per snapshot.
        self._grid_wire: tuple[int, dict[str, Any]] | None = None

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

    def join(self, sock: Any, name: str | None, role: str | None) -> tuple[Player | None, str | None]:
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
            reattach = self._reattach_reconnect(sock, name, role)
            if reattach is not None:
                return reattach
            decision = self._decide_join_role(role)
            if decision is None:
                return None, SESSION_FULL  # 2nd GM or 7th non-GM → refused
            pid, effective_role = decision
            player = Player(id=pid, name=name, role=effective_role, entity_id=None)
            self.players[pid] = player

            if effective_role == "player":
                player.entity_id = self._assign_player_token(pid, name)

            self._socks[pid] = sock
            self._client_id_for(sock)
            return player, None

    def _reattach_reconnect(
        self, sock: Any, name: str, role: str | None
    ) -> tuple[Player, None] | None:
        """Reconnect pass: re-attach ``sock`` to an existing Player matching
        ``name`` (+ optional ``role``). Caller must hold ``_lock``.
        Returns the attached player, or ``None`` when no match exists.
        """
        for pid, player in self.players.items():
            if player.name == name and (role is None or player.role == role):
                self._socks[pid] = sock
                self._client_id_for(sock)
                player.rebound = False  # live re-attach, not a save rebind
                return player, None
        return None

    def _decide_join_role(self, role: str | None) -> tuple[str, str] | None:
        """§8 role assignment for a NEW joiner. Caller must hold ``_lock``.

        Returns ``(pid, effective_role)``, or ``None`` when the session must
        refuse the join (2nd GM, or 7th non-GM) — the joiner is NOT added.
        """
        gm_exists = any(p.role == "gm" for p in self.players.values())
        n_players = sum(1 for p in self.players.values() if p.role == "player")
        if not self.players:
            effective_role = "gm"  # first client of a fresh session
        elif role == "gm" or not gm_exists:
            effective_role = "gm"  # explicit GM, or no GM yet: next joiner is GM
        else:
            effective_role = "player"
        if (effective_role == "gm" and gm_exists) or (
            effective_role == "player" and n_players >= MAX_PLAYERS
        ):
            return None
        return f"p{len(self.players) + 1}", effective_role

    def _assign_player_token(self, pid: str, name: str) -> str:
        """Entity for a newly joined PLAYER. Caller must hold ``_lock``.

        Save/load (save-load spec §6.1, F2): a name-matching UNCLAIMED saved
        token is rebound to the joiner INSTEAD of spawning a fresh one. The
        re-attach pass in :meth:`join` already returned for a name a
        CONNECTED player holds (so the two paths can never both run for one
        join), and this can only match an entity with owner=None — a
        GM-controlled token tagged with the saved player name when a save
        bundle was opened via use_map. First-join wins (bundle/dict order);
        the winner's owner_name tag is cleared (E7 — the badge disappears,
        and a later rejoin with the same name rebinds to the next unclaimed
        match, if any). The bound entity keeps its saved position, kind,
        team, color and name.
        """
        match = next(
            (e for e in self.entities.values()
             if e.owner is None and e.owner_name == name),
            None,
        )
        if match is not None:
            match.owner = pid
            match.owner_name = None
            self.players[pid].rebound = True  # save-load name rebind (BUG-014)
            return match.id
        # No match ⇒ exactly today's behavior (E6/AC8): a fresh party token
        # on a free floor cell.
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
        return eid

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

        Boss footprints (boss-entity spec, additive): the
        ``boss_footprints`` field carries the canonical ``size -> (w, h)``
        tile table (:data:`app.models.BOSS_FOOTPRINTS`) as a fresh shallow
        copy on every frame, so clients derive boss dimensions from the
        server instead of a hardcoded table. Values are tuples (JSON
        renders them as ``[w, h]`` arrays under stringified size keys);
        the table is static, so the key is present on GM and player
        frames alike and needs no role gating. Old clients ignore the
        unknown key.
        """
        is_gm = viewer.role == "gm"
        own = self.entities.get(viewer.entity_id) if viewer.entity_id else None
        map_dict = self._grid_wire_form()
        payload: dict[str, Any] = {
            "type": "state",
            "map": map_dict,
            "players": [p.to_dict() for p in self.players.values()],
            "entities": [e.to_dict() for e in self.entities.values()] if is_gm else [],
            "you_entity": own.to_dict() if (own is not None and not is_gm) else None,
            "awareness": self._awareness_for(viewer),
            "fog": self.fog,
            "boss_footprints": dict(BOSS_FOOTPRINTS),
        }
        if not is_gm:
            payload["visibility"] = self._visibility_for(viewer, own)
        return payload

    def _grid_wire_form(self) -> dict[str, Any]:
        """The grid's wire form, cached on (grid, revision) — stage 5b.

        ``to_dict`` is O(w*h) and used to run on EVERY per-viewer snapshot
        even when the grid was untouched; the wire dict is now built once
        per grid revision and reused until the grid mutates (every mutation
        — WS paint, REST paint, doors, safe — bumps ``grid.revision`` via
        :meth:`~app.models.Grid.bump_revision`). The ``doors``/``safe``
        objects keep their per-call fresh-copy semantics (spec §8.1): the
        cached dict is shallow-copied and the additive keys swapped with
        fresh dicts here, so cached references never leak. Wire shape is
        unchanged: ``to_dict`` + the identical ``doors_for_wire`` /
        ``safe_for_wire`` additive-key logic. Caller holds ``self._lock``.
        """
        cached = self._grid_wire
        if cached is None or cached[0] != self.grid.revision:
            wire = self.grid.to_dict()
            self._grid_wire = (self.grid.revision, wire)
            cached = self._grid_wire
        wire = dict(cached[1])  # shallow copy: the additive keys below swap
                                # in fresh dicts, so the cached one is never
                                # mutated by per-viewer assembly
        doors = self.grid.doors_for_wire()
        if doors is not None:
            wire["doors"] = doors
        safe_wire = self.grid.safe_for_wire()
        if safe_wire is not None:
            wire["safe"] = safe_wire
        return wire

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
        # BUG-014 / save-load spec §7.5: an ADDITIVE, welcome-only flag — set
        # only on a save-load name REBIND (GameSession.join), never on a fresh
        # spawn, a GM join, or a live same-name re-attach. It is carried ONLY
        # on this welcome (not on `state` snapshots, so `you` on `state` stays
        # frozen) so the client can fire the "your character has been
        # restored." toast for a reclaimed character and nothing else; old
        # clients ignore the unknown key.
        state["you"]["rebound"] = bool(viewer.rebound)
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
        """Welcome the joiner; give everyone else their own snapshot.

        Snapshots are built under the lock; the per-connection sends run
        AFTER the lock is released (BUG-011: sending while holding
        self._lock meant a slow or never-awaited sender wedged every
        joiner, and a dead sender's exception aborted the rest of the
        fan-out).  One broken connection must not affect any other.
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
                # The joiner gets its own welcome (state + `you`); every
                # OTHER viewer gets their own per-viewer snapshot — a GM
                # included in the fan-out must see the full entity list,
                # which a player's welcome never carries (BUG-025).
                if conn is sender_conn:
                    payload = self.welcome_for(player)
                else:
                    payload = self.state_for(viewer)
                targets.append((sender, payload))
        for sender, payload in targets:
            try:
                await sender(payload)
            except Exception:
                continue

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
        if mtype == "set_fog":
            return self._gm_only(is_gm, lambda: self._on_set_fog(msg))
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
        if name is not None and not isinstance(name, str):
            return None, {"type": "error", "message": "name must be a string"}
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

            # Boss-entity spec: the mover may only STOP where its FULL
            # footprint is in-bounds and free of every OTHER entity
            # (footprint-aware destination check, models.entity_cells).
            # Routing itself is entity-unaware by design — boss spec §8
            # scopes AI/collision routing OUT, so find_path keeps its
            # signature unchanged (BUG-025: an ``occupied_by=`` keyword
            # was passed here for a parameter that does not exist).
            other_cells = set()
            for o in self.entities.values():
                if o is not entity:
                    other_cells.update(entity_cells(o))

            w, h = (boss_footprint_cells(entity.size)
                    if entity.kind == "boss" else (1, 1))
            for cx, cy in footprint_cells(x, y, w, h):
                if not (0 <= cx < self.grid.width
                        and 0 <= cy < self.grid.height):
                    return {"type": "error",
                            "message": "destination out of bounds"}
                if (cx, cy) in other_cells:
                    return {"type": "error",
                            "message": "destination occupied"}

            if override:
                # GM "ignore walls": direct move to the target, walls
                # ignored — EXCEPT the safe-room safety rule (safe-room
                # spec §5.2, D4): a HOSTILE is never moved onto a
                # safe-room door cell (open or closed); party/neutral keep
                # the normal ignore-walls ability (E11).
                if self.grid.is_safe_door(x, y) and entity.team == "hostile":
                    return {"type": "error",
                            "message": HOSTILE_ON_SAFE_DOOR}
                entity.x, entity.y = x, y
                path = [{"x": x, "y": y}]
            else:
                # Team-aware A* (safe-room spec §5.3): the restriction is
                # judged by the MOVING entity's team — a hostile treats an
                # open safe door as a wall, party/neutral walk through it.
                # (Occupancy is enforced at the STOP above, not in the
                # route — boss spec §8 keeps find_path entity-unaware.)
                coords = find_path(self.grid, (entity.x, entity.y), (x, y),
                                   team=entity.team)
                if coords is None:
                    return {"type": "error", "message": NO_ROUTE}
                entity.x, entity.y = x, y
                path = [{"x": px, "y": py} for (px, py) in coords]

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
            # Boss-entity spec: a direct place is also footprint-aware —
            # the entity's FULL footprint must be in-bounds and free of
            # every OTHER entity (e.g. a GM may not place a mover onto a
            # cell inside a boss's own footprint).
            w, h = (boss_footprint_cells(entity.size)
                    if entity.kind == "boss" else (1, 1))
            for cx, cy in footprint_cells(x, y, w, h):
                if not (0 <= cx < self.grid.width
                        and 0 <= cy < self.grid.height):
                    return {"type": "error",
                            "message": "destination out of bounds"}
                for o in self.entities.values():
                    if o is entity:
                        continue
                    if (cx, cy) in entity_cells(o):
                        return {"type": "error",
                                "message": "destination occupied"}
            # Safe-room spec §5.2 (D4): a hostile is never placed on a
            # safe-room door cell (open or closed); party/neutral keep the
            # normal ignore-walls place (E11).
            if self.grid.is_safe_door(x, y) and entity.team == "hostile":
                return {"type": "error",
                        "message": HOSTILE_ON_SAFE_DOOR}
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
            # Boss (boss-entity spec §Grid validity): a boss may only spawn
            # where its FULL W×H footprint is in-bounds and free of other
            # entities; any other cell fails with the spec's exact message.
            if kind == "boss":
                # Boss tokens default to size 2 (boss-entity spec); an
                # explicit size overrides it — but ONLY when the key is
                # present, since ``msg.get("size", 2)`` would clobber an
                # explicit size 0/1 before validation.
                size = 2 if "size" not in msg else _as_int(msg.get("size"))
                err = self._spawn_boss(name.strip(), team, x, y, size=size)
                if err:
                    return err
                self._run_b(self._broadcast())
                return None
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error", "message": "destination out of bounds"}
            # Safe-room spec §5.2 (D4): a hostile is never CREATED on a
            # safe-room door cell (open or closed).
            if self.grid.is_safe_door(x, y) and team == "hostile":
                return {"type": "error",
                        "message": HOSTILE_ON_SAFE_DOOR}
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

    def _spawn_boss(self, name: str, team: str, x: int, y: int,
                    size: int | None = 2) -> dict[str, Any] | None:
        """Create a boss token whose FULL footprint fits; None on success.

        Boss-entity spec §Grid validity: the boss's anchor is its top-left
        corner and every cell of its W×H footprint must be in-bounds and
        unoccupied by another entity, else fail with the exact message
        ``"Boss footprint does not fit"``.
        """
        if size not in BOSS_FOOTPRINTS:
            return {"type": "error", "message": "Boss footprint does not fit"}
        w, h = BOSS_FOOTPRINTS[size]
        for cx, cy in footprint_cells(x, y, w, h):
            if not (0 <= cx < self.grid.width
                    and 0 <= cy < self.grid.height):
                return {"type": "error",
                        "message": "Boss footprint does not fit"}
            if self.grid.cells[cy][cx] == "wall":
                return {"type": "error",
                        "message": "Boss footprint does not fit"}
            if self._any_entity_at(cx, cy):
                return {"type": "error",
                        "message": "Boss footprint does not fit"}
        n = len(self.entities)
        eid = f"e{n + 1}"
        while eid in self.entities:
            n += 1
            eid = f"e{n + 1}"
        self.entities[eid] = Entity(
            id=eid, name=name, kind="boss", team=team,
            x=x, y=y, owner=None, size=size,
        )
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
            # Safe-room spec §5.4 (E4): the last path by which a hostile
            # could end up on a safe-door cell — a token standing on an
            # open safe door (legal for party/neutral, I4) whose team is
            # switched to hostile. Rejected so "no hostile on a safe cell"
            # stays invariant under every mutation (I4b).
            if team == "hostile" and self.grid.is_safe_door(
                    entity.x, entity.y):
                return {"type": "error",
                        "message": HOSTILE_ON_SAFE_DOOR}
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

        Safe-room guard (safe-room spec §4.4, AC13b): a SAFE-room door cell
        is NOT a normal door (mutual exclusion, I1) — any ``door`` message on
        it is rejected with ``"not a normal door"`` before the state machine
        runs, so a stray normal-door message can never write a ``doors``
        entry on a safe cell. The guard never fires for a cell without a
        safe record, so every existing normal-door path is byte-identical.
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
            if self.grid.is_safe_door(x, y):
                return {"type": "error", "message": "not a normal door"}
            action = msg.get("action")
            if action not in DOOR_ACTIONS:
                return {"type": "error",
                        "message": "action must be one of unlock/lock/open/close"}
            cur = self.grid.door_state_at(x, y)  # "L" | "U" | "O"
            # Non-None here: the doorway + non-safe-door guards above are the
            # only two cases door_state_at returns None for (type narrowing).
            assert cur is not None
            # Table dispatch: state-legality first (§4.3), then the GM-only
            # role gate (BEFORE occupancy, BUG-DOORS-002), then the
            # occupancy guard for force-closing transitions (A5).
            row = DOOR_TRANSITIONS.get((str(action), cur))
            if row is None:
                return {"type": "error", "message": "illegal door transition"}
            if row.error is not None:
                return {"type": "error", "message": row.error}
            if action in ("unlock", "lock") and not is_gm:
                return {"type": "error", "message": NOT_ALLOWED}
            if row.occupancy_guard and self._any_entity_at(x, y):
                return {"type": "error",
                        "message": "cannot close a door with a token on it"}
            # Legal rows always carry a to_state (error/to_state are mutually
            # exclusive in the table); this guard is for mypy only.
            if row.to_state is None:
                return {"type": "error", "message": "illegal door transition"}
            self.grid.set_door(x, y, row.to_state)
            self._run_b(self._broadcast())
        return None

    def _on_safe_door(self, player: Player, is_gm: bool,
                      msg: dict[str, Any]) -> dict[str, Any] | None:
        """The safe-room door state machine + permissions (door-iconography
        spec §4) — WHOLLY GM-controlled (mark/unmark/unlock/lock/open/close;
        there is NO player path at all).

        Validation is the spec's deterministic order (§4.3, AC3), first
        failure wins:

          1. role: the sender must be the GM → ``"not allowed"``
             (the safe-door surface has NO player path, so the role gate
             runs FIRST — unlike the normal door handler)
          2. ``x``/``y`` are ints (bools rejected) → ``"x and y must be
             integers"``
          3. in bounds → ``"destination out of bounds"``
          4. the cell is a ``doorway`` → ``"not a doorway"``
          5. ``action`` is valid → ``"action must be one of
             mark/unmark/unlock/lock/open/close"``
          6. the ``(state, action)`` transition is legal → the
             state-specific error (``"already a safe door"``,
             ``"not a safe door"``, ``"safe door is locked"``,
             ``"safe door is already unlocked"``,
             ``"safe door is already locked"``,
             ``"safe door is already open"``,
             ``"safe door is already closed"``)
          7. occupancy: ``mark`` with a token on the cell → ``"cannot mark
             a safe door with a token on it"``; ``close`` with a token on
             the cell → ``"cannot close a door with a token on it"``
             (``lock`` from ``open`` force-closes but is NOT guarded — the
             door was already open/walkable, matching the normal door's
             A5 nuance; a hostile can't be on an open safe door anyway).

        ``mark`` turns a (normal) doorway into a safe door in state ``L``
        (locked — the secure fresh default, §3.4; the recorded normal-door
        state, if any, is dropped — the two records are mutually exclusive,
        I1). ``unmark`` reverts a safe door to a NORMAL door preserving the
        state (``L``→``L``, ``U``→``U``, ``O``→``O``). The state machine
        (the normal-door machine + the mark/unmark conversions) is driven
        by the ``SAFE_DOOR_TRANSITIONS`` table: ``L ─unlock→ U ─open→ O
        ─close→ U``; GM ``lock`` from ``U`` or ``O`` (force-closes ``O``)
        → ``L``. On success there is NO per-client reply — the ``state``
        broadcast carries the new ``map.safe`` (and updated ``map.doors``
        for mark/unmark).
        """
        # GM-only, FIRST (the safe-door surface has NO player path — §4.2).
        if not is_gm:
            return {"type": "error", "message": NOT_ALLOWED}
        x = _as_int(msg.get("x"))
        y = _as_int(msg.get("y"))
        if x is None or y is None:
            return {"type": "error", "message": "x and y must be integers"}
        with self._lock:
            if not (0 <= x < self.grid.width and 0 <= y < self.grid.height):
                return {"type": "error",
                        "message": "destination out of bounds"}
            if self.grid.cells[y][x] != "doorway":
                return {"type": "error", "message": "not a doorway"}
            action = msg.get("action")
            if action not in SAFE_DOOR_ACTIONS:
                return {"type": "error",
                        "message": (
                            "action must be one of "
                            "mark/unmark/unlock/lock/open/close")}
            is_safe = self.grid.is_safe_door(x, y)
            if action == "mark":
                err = self._safe_door_mark(x, y, is_safe)
            elif action == "unmark":
                err = self._safe_door_unmark(x, y, is_safe)
            else:  # unlock / lock / open / close — table-driven (§4.3)
                err = self._safe_door_set(x, y, action)
            if err is not None:
                return err
            self._run_b(self._broadcast())
        return None

    def _safe_door_mark(
        self, x: int, y: int, is_safe: bool
    ) -> dict[str, Any] | None:
        """``mark``: record the doorway as a safe door, STARTING LOCKED
        ("L", the secure default, §3.4); returns an error dict or None.

        A recorded NORMAL door is dropped first (mutual exclusion, I1) —
        the two records are mutually exclusive.
        """
        key = f"{x},{y}"
        if is_safe:
            return {"type": "error", "message": "already a safe door"}
        if self._any_entity_at(x, y):
            return {"type": "error",
                    "message": "cannot mark a safe door with a token on it"}
        if (self.grid.doors or {}).get(key) is not None:
            assert self.grid.doors is not None
            self.grid.doors = dict(self.grid.doors)
            del self.grid.doors[key]  # type: ignore[index]
        self.grid.set_safe_door(x, y, "L")
        return None

    def _safe_door_unmark(
        self, x: int, y: int, is_safe: bool
    ) -> dict[str, Any] | None:
        """``unmark``: revert the safe door to a NORMAL door, preserving the
        state (``L``→``L``, ``U``→``U``, ``O``→``O``); returns an error dict
        or None.
        """
        if not is_safe:
            return {"type": "error", "message": "not a safe door"}
        self.grid.unmark_safe_door(x, y)  # preserves L/U/O (§3.5)
        return None

    def _safe_door_set(self, x: int, y: int, action: Any) -> dict[str, Any] | None:
        """Table-driven unlock/lock/open/close on the safe door at
        ``(x, y)`` (state machine, spec §4.3); returns an error dict or None.
        """
        cur = self.grid.safe_door_state_at(x, y)  # "L" | "U" | "O"
        if cur is None:
            return {"type": "error", "message": "not a safe door"}
        # The table is exhaustive: ``safe_door_state_at`` only returns
        # ``"L" | "U" | "O" | None`` and the action is one of the four
        # set-actions, so every ``(action, cur)`` has a row — a KeyError
        # here would signal a corrupt table, not a reachable state.
        row = SAFE_DOOR_TRANSITIONS[(action, cur)]
        if row.error is not None:
            return {"type": "error", "message": row.error}
        if row.occupancy_guard and self._any_entity_at(x, y):
            return {"type": "error",
                    "message": "cannot close a door with a token on it"}
        # ``to_state`` is always a str on a non-error row (``error`` and
        # ``to_state`` are mutually exclusive per row) — type-checker only.
        assert row.to_state is not None
        # NOTE: ``lock`` from ``O`` force-closes and is NOT occupancy-guarded
        # (A14 / E11 — the door was already open/walkable; a hostile can't
        # be on it anyway).
        self.grid.set_safe_door(x, y, row.to_state)
        return None

    def _any_entity_at(self, x: int, y: int) -> bool:
        """True if any entity's footprint covers cell (x, y).

        Boss-entity spec: a boss occupies every cell of its W×H footprint
        (models.entity_cells), not just its anchor cell.
        """
        return any(cell == (x, y) for e in self.entities.values()
                   for cell in entity_cells(e))

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

        Save/load (save-load spec §5.3/§6.1): when the registry entry carries
        a ``loaded_entities`` list (the marker of a map loaded from a save
        bundle — deliberately a dedicated key so the ``GET /api/maps/{id}``
        ``entities`` dict wire shape stays frozen), those entities REBUILD
        the session's roster — fresh :class:`~app.models.Entity` objects,
        every one GM-controlled (``owner=None``) and tagged with its saved
        ``owner_name`` so a later join by that name rebinds it (§6.1; the
        tag is cleared on the rebind). Tokens of still-connected players are
        NOT dropped: they are re-placed onto the new grid below (existing
        ``use_map`` semantics, A4/E10) — the GM manages the leftover/claimed
        mix (R4).
        """
        map_id = msg.get("map_id")
        if not isinstance(map_id, str) or not map_id.strip():
            return {"type": "error", "message": "map_id required"}
        grid, saved_entities = self._lookup_map(map_id.strip())
        if grid is None:
            return {"type": "error", "message": f"unknown map: {map_id!s}"}
        with self._lock:
            self.grid = grid
            self._grid_wire = None  # stage 5b: new grid object — invalidate
            if saved_entities is not None:
                self.entities = self._rebuild_saved_roster(saved_entities)
            self._reposition_out_of_bounds()
            # Explored map (D3): old cells reference a different map (possibly
            # a different size) — clear EVERY player's memory BEFORE the
            # broadcast, so the post-swap snapshots re-seed from the NEW
            # positions and no stale-coordinate cell can ever render E.
            self._explored.clear()
            self._run_b(self._broadcast())
        return None

    def _lookup_map(
        self, map_id: str
    ) -> tuple[Grid | None, list[dict[str, Any]] | None]:
        """Resolve ``map_id`` in the live registry (same process).

        Returns ``(grid, saved_entities)``; ``grid is None`` when the map is
        unknown or the registry is unavailable (reported by the caller).
        """
        try:
            from app.main import maps_registry  # live registry (same process)
            entry = maps_registry.get(map_id)
        except Exception:
            return None, None  # registry unavailable — report below
        if entry is None:
            return None, None
        # Load-side tagging (spec §6.1): the registry entry carries the
        # per-entity owner_name list in ``loaded_entities`` (stored with the
        # entry, not on the Grid object) — the marker of a save-loaded map.
        raw_saved = entry.get("loaded_entities")
        if isinstance(raw_saved, list) and raw_saved:
            return entry["grid"], raw_saved
        return entry["grid"], None

    def _rebuild_saved_roster(
        self, saved_entities: list[dict[str, Any]]
    ) -> dict[str, Entity]:
        """Rebuild the session's entity roster from a save bundle's entity
        list (spec 5.3 step 3). Caller must hold ``_lock``.

        Tokens of still-connected (or detached-but-not-left) players are
        kept FIRST, with their live ids (existing use_map semantics, A4/E10);
        every loaded entity is a fresh GM-controlled object (``owner=None``)
        tagged with its saved ``owner_name`` for the rebind step. A loaded
        entity whose id collides with a kept token is re-id'd (base,
        base-2, base-3, ...) — rebind is by NAME, never by id, so the
        cosmetic offset is safe (E10: the GM manages the leftover/claimed
        mix).
        """
        rebuilt: dict[str, Entity] = {}
        taken: set[str] = set()
        for p in self.players.values():
            if p.entity_id:
                kept = self.entities.get(p.entity_id)
                if kept is not None and kept.id not in taken:
                    rebuilt[kept.id] = kept
                    taken.add(kept.id)

        def _next_eid(base: str) -> str:
            if base not in taken:
                return base
            n = 2
            while f"{base}-{n}" in taken:
                n += 1
            return f"{base}-{n}"

        for d in saved_entities:
            if not isinstance(d, dict):
                continue
            base_id = d.get("id")
            if not isinstance(base_id, str) or not base_id:
                continue
            eid = _next_eid(base_id)
            rebuilt[eid] = Entity(
                id=eid,
                name=str(d.get("name") or "token"),
                kind=str(d.get("kind") or "npc"),
                team=str(d.get("team") or "neutral"),
                x=int(d.get("x", 0)),
                y=int(d.get("y", 0)),
                owner=None,
                color=d.get("color"),
                owner_name=d.get("owner_name"),
                # Boss-entity spec: the validated size variant (saves.py
                # guarantees a boss always carries one); None for the
                # other kinds, which footprint 1×1.
                size=d.get("size"),
            )
            taken.add(eid)
        return rebuilt

    def _find_free_floor_for(self, w: int, h: int) -> tuple[int, int]:
        """First row-major anchor whose FULL w×h footprint is in-bounds and
        on floor/doorway cells (boss-entity spec: a boss must not be parked
        with its tail over the wall). Falls back to
        :meth:`_find_free_floor` (anchor-only) when no full-fit anchor
        exists — e.g. a 3×4 boss on a grid shorter than 4 rows.
        """
        for y in range(self.grid.height - h + 1):
            for x in range(self.grid.width - w + 1):
                if all(self.grid.cells[cy][cx] in ("floor", "doorway")
                       for cy in range(y, y + h)
                       for cx in range(x, x + w)):
                    return x, y
        return self._find_free_floor()

    def _reposition_out_of_bounds(self) -> None:
        """The new grid can be smaller: park anything out of bounds (or on a
        newly painted wall) on a free floor/doorway cell.
        Boss-entity spec: the fit check is FOOTPRINT-anchored — a boss is
        out of place when ANY of its cells is out of bounds or on a wall
        (not just its top-left anchor).
        Caller must hold ``_lock``.
        """
        for e in self.entities.values():
            w, h = e.footprint_cells
            fits = all(
                0 <= cx < self.grid.width and 0 <= cy < self.grid.height
                and self.grid.cells[cy][cx] in ("floor", "doorway")
                for cx, cy in footprint_cells(e.x, e.y, w, h)
            )
            if fits:
                continue
            x, y = self._find_free_floor_for(w, h)
            e.x, e.y = x, y
