"""LittleDungeons awareness overlay (PROJECT.md §4, §5) — pure standard library.

The overlay is **per player** and answers one question per (viewer, entity)
pair: *what does this viewer perceive about that entity?*

* **GM** sees EVERY entity, unmasked: true color, entity kind, and a name
  label. The GM is a pure controller with ``entity_id = None`` — there is
  no own token to exclude, its view never references ``viewer.entity_id``,
  and NO distance / line-of-sight filtering ever applies to it (even when a
  ``grid`` is passed).
* **Player** — when a ``grid`` is supplied — sees every other entity
  through a **three-tier visibility model**, anchored at the player's own
  token ``O``:

  1. **FULL** — direct line of sight to ``E`` (Bresenham,
     :func:`app.pathfinding.has_line_of_sight`; walls block, floor/doorway
     pass): the item carries the entity's **full information** — exact
     position, display color (explicit ``entity.color`` override or team
     color green/white/red), ``name``, ``kind`` and ``label: True`` —
     identical in shape to the GM item.
  2. **APPROXIMATE** — no line of sight, but ``E`` within
     :data:`APPROX_RADIUS` squares of ``O`` (Chebyshev distance
     ``max(|dx|, |dy|) <= APPROX_RADIUS``): only a **coarse, quantized
     position** is revealed — the origin ``(qx, qy) = (E.x // APPROX_BLOCK,
     E.y // APPROX_BLOCK)`` of the 2×2 block containing ``E``.  **No
     identity at all**: no color, no name, no kind, no label, and no
     entity id — the ``entity_id`` field is a non-revealing surrogate
     (``"<approx-1>"``, ``"<approx-2>"``, … in deterministic item order).
  3. **INVISIBLE** — no line of sight AND farther than
     :data:`APPROX_RADIUS` squares: the entity does **not** appear in the
     player's awareness list at all.

  The model is *purely a function of the current positions* — there is no
  memory of previously seen entities (the old fog-of-war "previously seen"
  mechanism is gone).  A player whose own entity no longer exists has no
  anchor and sees **nothing** (empty awareness).

  When NO ``grid`` is supplied (legacy callers), the player falls back to
  the old pass-through-wall radar: every other entity as an unlabeled
  colored dot.

Color semantics (all tiers that carry a color): the relation to the
entity's *team* —

      party   → friend  → green
      neutral → neutral → white
      hostile → enemy   → red

(equivalently ``TEAM_COLORS[entity.team]``); an explicit ``entity.color``,
if set, always overrides the team-derived color.
"""

from __future__ import annotations

from typing import Any

from app.models import Entity, Grid, Player, TEAM_COLORS
from app.pathfinding import has_line_of_sight

# ---------------------------------------------------------------------------
# Player visibility model constants (§5)
# ---------------------------------------------------------------------------

#: Chebyshev distance (squares) within which a no-LOS entity is still
#: perceived as an *approximate* contact; farther, it is invisible.
APPROX_RADIUS = 4

#: Edge length (squares) of the quantization block used to report an
#: approximate position: ``qx = x // APPROX_BLOCK``, ``qy = y // APPROX_BLOCK``.
APPROX_BLOCK = 2


def _chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    """Chebyshev (chessboard / king-move) distance between two cells."""
    return max(abs(ax - bx), abs(ay - by))


# ---------------------------------------------------------------------------
# Relation + color rules
# ---------------------------------------------------------------------------

_RELATION_TO_COLOR: dict[str, str] = {
    "friend": "green",
    "neutral": "white",
    "enemy": "red",
}


def relation_of(target: Entity) -> str:
    """Relation to ``target``, judged by the TARGET's team (§5).

    ``party`` → ``"friend"``, ``neutral`` → ``"neutral"``,
    ``hostile`` → ``"enemy"``.  (A party member marked hostile by the GM
    shows red to everyone, because the target's team is what matters.)
    """
    if target.team == "party":
        return "friend"
    if target.team == "neutral":
        return "neutral"
    return "enemy"


def overlay_color(relation: str, entity: Entity) -> str:
    """Display color for ``entity`` given its ``relation`` to the viewer.

    An explicit ``entity.color`` (GM-painted override) wins; otherwise the
    relation maps friend→green, neutral→white, enemy→red.
    """
    if entity.color:
        return entity.color
    return _RELATION_TO_COLOR.get(relation, TEAM_COLORS.get(entity.team, "white"))


def _full_item(entity: Entity) -> dict[str, Any]:
    """A **FULL**-visibility awareness item (exact position, color,
    name, kind, ``label: True``) — the identical shape for GM and for
    a player with line of sight."""
    return {
        "entity_id": entity.id,
        "x": entity.x,
        "y": entity.y,
        "color": overlay_color(relation_of(entity), entity),
        "name": entity.name,
        "kind": entity.kind,
        "label": True,
    }


# ---------------------------------------------------------------------------
# Per-viewer overlay
# ---------------------------------------------------------------------------


def build_awareness(
    viewer: Player,
    entities: dict[str, Entity],
    grid: Grid | None = None,
) -> list[dict[str, Any]]:
    """Build the awareness overlay items for ``viewer``.

    * ``viewer.role == "gm"``: one item for EVERY entity — the GM is a
      pure controller with no own token (``viewer.entity_id`` is never
      consulted), each ``{"entity_id", "x", "y", "color", "name", "kind",
      "label": True}``.  Color is the explicit override or the team color
      (true colors, no masking), and every item carries the name + kind.
      A ``grid``, when given, changes NOTHING for the GM: no LOS and no
      distance filtering is ever applied.
    * ``viewer.role == "player"`` (see module docstring for the model):

      * with ``grid`` — the three-tier model anchored at the viewer's own
        entity ``O``: FULL (line of sight → item identical in shape to the
        GM item), APPROXIMATE (no LOS, within :data:`APPROX_RADIUS`
        squares → ``{"entity_id": "<approx-n>", "x": E.x // APPROX_BLOCK,
        "y": E.y // APPROX_BLOCK, "approximate": True, "label": False}``),
        INVISIBLE (no LOS, beyond the radius → omitted).  The viewer's own
        token is always excluded.  A player whose own entity is missing
        from ``entities`` (deleted) has no anchor: awareness is ``[]``.
      * without ``grid`` — legacy pass-through-wall radar: every other
        entity as ``{"entity_id", "x", "y", "color", "label": False}``
        (colored dot, no name/kind/label).

    The list is sorted by entity_id so the output (including the
    ``"<approx-n>"`` surrogate numbering) is deterministic.
    """
    items: list[dict[str, Any]] = []
    if viewer.role == "gm":
        for entity_id in sorted(entities):
            items.append(_full_item(entities[entity_id]))
        return items

    # Player.
    if grid is None:
        # Legacy radar (no grid supplied): colored dots through walls.
        for entity_id in sorted(entities):
            if entity_id == viewer.entity_id:
                continue
            entity = entities[entity_id]
            items.append(
                {
                    "entity_id": entity_id,
                    "x": entity.x,
                    "y": entity.y,
                    "color": overlay_color(relation_of(entity), entity),
                    "label": False,
                }
            )
        return items

    own = entities.get(viewer.entity_id) if viewer.entity_id else None
    if own is None:
        # No anchor (the player's own entity was deleted): sees nothing.
        return []

    approx_count = 0
    for entity_id in sorted(entities):
        if entity_id == viewer.entity_id:
            continue
        entity = entities[entity_id]
        if has_line_of_sight(grid, (own.x, own.y), (entity.x, entity.y)):
            items.append(_full_item(entity))
        elif _chebyshev(own.x, own.y, entity.x, entity.y) <= APPROX_RADIUS:
            approx_count += 1
            items.append(
                {
                    # Non-revealing surrogate: no real id, no color, no
                    # name/kind — the client only ever sees a block.
                    "entity_id": f"<approx-{approx_count}>",
                    # Block ORIGIN (top-left cell of the APPROX_BLOCK×
                    # APPROX_BLOCK block containing the entity).
                    "x": entity.x // APPROX_BLOCK,
                    "y": entity.y // APPROX_BLOCK,
                    "approximate": True,
                    "label": False,
                }
            )
        # else: INVISIBLE — not listed at all.
    return items
