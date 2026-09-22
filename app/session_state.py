"""
Per-viewer state + broadcast mixin for :class:`app.session.GameSession`.

Moved verbatim from ``app/session.py`` (god-module split): the §5/§9 per-viewer
snapshots (awareness, visibility/explored map, grid wire-form cache,
welcome) and the per-connection broadcast fan-out.  Defines NO
``__init__`` — the facade owns all shared state (``self._lock`` et al.).
Also hosts :class:`SessionBase`, the type-only base that all five mixins
inherit from (shared attribute declarations + cross-mixin method stubs for
the type checker; see its docstring).
"""

from __future__ import annotations

import threading
from typing import Any, Iterator

from app.awareness import build_awareness
from app.models import BOSS_FOOTPRINTS, Entity, Grid, Player
from app.visibility import build_visibility_mask, visible_cells


class SessionBase:
    """Type-only base shared by the :class:`app.session.GameSession` mixins.

    The mixins reach across class boundaries (``self.grid``, ``self._lock``,
    ``self._broadcast``, …), but mypy checks each mixin in isolation, so
    every shared attribute is declared here (annotation only — no runtime
    effect) and every cross-mixin method is stubbed with its REAL signature.
    The stubs never execute: :class:`SessionBase` sits LAST in the
    ``GameSession`` MRO (after all concrete mixins) and is shadowed by the
    real implementations. ``GameSession.__init__`` remains the only place
    any of the declared state is constructed.
    """

    # -- authoritative state (constructed in GameSession.__init__) ---------
    id: str
    grid: Grid
    entities: dict[str, Entity]
    players: dict[str, Player]

    # -- threading + per-connection bookkeeping ----------------------------
    _lock: threading.RLock
    _socks: dict[str, Any]
    _cid_by_sock: dict[int, str]
    _senders: dict[str, Any]
    _client_seq: Iterator[int]

    # -- per-viewer state (explored map + grid wire-form cache) -------------
    _explored: dict[str, set[tuple[int, int]]]
    _grid_wire: tuple[int, dict[str, Any]] | None

    # -- cross-mixin methods (real implementations live in the sibling ----
    # -- mixins, or in the GameSession facade; the stubs exist only for the
    # -- type checker and are shadowed in the MRO) --------------------------
    @staticmethod
    def _run_b(coro: Any) -> None:  # real: GameSession (facade)
        raise NotImplementedError

    async def _broadcast(self, extra: dict[str, Any] | None = None) -> None:  # real: SessionStateMixin
        raise NotImplementedError

    def _sender_for(self, conn: Any) -> Any:  # real: SessionConnMixin
        raise NotImplementedError

    def _find_free_floor(self) -> tuple[int, int]:  # real: SessionMapMixin
        raise NotImplementedError

    def _any_entity_at(self, x: int, y: int) -> bool:  # real: SessionDoorMixin
        raise NotImplementedError

    def welcome_for(self, viewer: Player) -> dict[str, Any]:  # real: SessionStateMixin
        raise NotImplementedError

    def state_for(self, viewer: Player) -> dict[str, Any]:  # real: SessionStateMixin
        raise NotImplementedError


class SessionStateMixin(SessionBase):
    """Per-viewer state snapshots + broadcasts (PROJECT.md §5, §9)."""

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
        identity); anything else is invisible.          The **GM** is exempt:
        every entity, full info, labeled, no distance/LOS filtering.

        The model is always active for players; there is no
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
