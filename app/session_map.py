"""
Map-switching + placement mixin for :class:`app.session.GameSession`.

Moved verbatim from ``app/session.py`` (god-module split): free-floor placement
(``_find_free_floor`` / ``_find_free_floor_for`` /
``_reposition_out_of_bounds``) and the GM map switch (``_on_use_map`` /
``_lookup_map`` / ``_rebuild_saved_roster``).  Defines NO ``__init__`` — the
facade owns all shared state (``self._lock`` et al.).

NOTE: ``_lookup_map`` keeps its DEFERRED ``from app.main import
maps_registry`` inside the method body — importing ``app.main`` at module
level would create a cycle (``app.main`` imports ``GameSession`` from
``app.session``).
"""

from __future__ import annotations

import logging
from typing import Any

from app.models import Entity, Grid, footprint_cells
from app.session_state import SessionBase

logger = logging.getLogger(__name__)


class SessionMapMixin(SessionBase):
    """Map switching, spawn placement, save-roster rebuild."""

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
