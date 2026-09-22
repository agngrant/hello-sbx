"""
Connection + join mixin for :class:`app.session.GameSession`.

Moved verbatim from ``app/session.py`` (god-module split): the per-connection
bookkeeping (client ids, async senders, sockets), the PROJECT.md §8 join
flow (role assignment, player-token allocation, reconnect re-attach,
leave) and the join fan-out (``_announce_join`` / ``_on_join``).  Defines NO
``__init__`` — the facade owns all shared state (``self._lock`` et al.).
"""

from __future__ import annotations

from typing import Any

from app.models import Entity, Player
from app.session_common import MAX_PLAYERS, SESSION_FULL
from app.session_state import SessionBase


class SessionConnMixin(SessionBase):
    """Connection bookkeeping + joins (PROJECT.md §8)."""

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
