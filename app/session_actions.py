"""
Movement + GM-tool mixin for :class:`app.session.GameSession`.

Moved verbatim from ``app/session.py`` (god-module split): the §6 movement
handler (``_on_move``) and the GM tools (``_on_place``, ``_on_create_entity``
+ ``_spawn_boss``, ``_on_delete_entity``, ``_on_set_team``, ``_on_set_awareness``,
``_on_paint``).  Defines NO ``__init__`` — the facade owns all shared state
(``self._lock`` et al.).
"""

from __future__ import annotations

from typing import Any

from app.awareness import AWARENESS_MAX, AWARENESS_MIN
from app.models import (
    BOSS_FOOTPRINTS, CELL_TYPES, TEAMS, Entity, Player,
    boss_footprint_cells, entity_cells, footprint_cells,
)
from app.pathfinding import find_path
from app.session_common import (
    CREATABLE_KINDS, HOSTILE_ON_SAFE_DOOR, NO_ROUTE, NOT_ALLOWED, _as_int,
)
from app.session_state import SessionBase


class SessionActionMixin(SessionBase):
    """Movement (§6) + GM tools."""

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
