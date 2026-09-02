"""LittleDungeons awareness overlay (PROJECT.md §4, §5) — pure standard library.

The overlay is **per player** and is a radar that ignores walls.  For each
connected player, the server computes which entities that player sees, in
what color, and with or without a name label:

* **GM** sees EVERY entity (including its own), unmasked: true color,
  entity kind, and a name label.
* **Player** sees every entity EXCEPT its own as a colored dot — no name,
  no label.  The color is the *relation* to the entity's team:

      party   → friend  → green
      neutral → neutral → white
      hostile → enemy   → red

  (equivalently ``TEAM_COLORS[entity.team]``).  An explicit
  ``entity.color``, if set, always overrides the team-derived color.
"""

from __future__ import annotations

from typing import Any

from app.models import Entity, Player, TEAM_COLORS

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


# ---------------------------------------------------------------------------
# Per-viewer overlay
# ---------------------------------------------------------------------------


def build_awareness(
    viewer: Player, entities: dict[str, Entity]
) -> list[dict[str, Any]]:
    """Build the awareness overlay items for ``viewer``.

    * ``viewer.role == "gm"``: one item for EVERY entity — including the
      GM's own — each ``{"entity_id", "x", "y", "color", "name", "kind",
      "label": True}``.  Color is the explicit override or the team color
      (true colors, no masking), and every item carries the name + kind.
    * ``viewer.role == "player"``: one item for every entity EXCEPT the
      viewer's own (``viewer.entity_id``), each
      ``{"entity_id", "x", "y", "color", "label": False}`` — colored dots
      only, no names.

    The list is sorted by entity_id so the output is deterministic.
    """
    items: list[dict[str, Any]] = []
    for entity_id in sorted(entities):
        entity = entities[entity_id]
        if viewer.role == "gm":
            items.append(
                {
                    "entity_id": entity_id,
                    "x": entity.x,
                    "y": entity.y,
                    "color": entity.color or TEAM_COLORS[entity.team],
                    "name": entity.name,
                    "kind": entity.kind,
                    "label": True,
                }
            )
        else:
            if entity_id == viewer.entity_id:
                continue
            relation = relation_of(entity)
            items.append(
                {
                    "entity_id": entity_id,
                    "x": entity.x,
                    "y": entity.y,
                    "color": overlay_color(relation, entity),
                    "label": False,
                }
            )
    return items
