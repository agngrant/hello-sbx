"""LittleDungeons data models (PROJECT.md §4) — stdlib ``dataclasses`` + plain dicts.

These models are shared between the server, the tests, and (via JSON) the
frontend. Cell types are stored implicitly as a 2D array of strings inside
:class:`Grid` — ``cells[y][x]`` — per the contract (there is no separate
``Cell`` object).

Conversion to/from plain dicts is manual (``to_dict`` / ``from_dict``) so the
REST/WS payloads are exactly the shapes the frontend already consumes:

* ``Grid``:      ``{"name", "width", "height", "cells", "image"}``
* ``Entity``:    ``{"id", "name", "kind", "team", "x", "y", "owner", "color"}``
* ``Player``:    ``{"id", "name", "role", "entity_id"}``
* ``Session``:   ``{"id", "map", "entities", "players", "fog"}`` — the full
  snapshot broadcast on any mutation (PROJECT.md §9).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Literal type strings (PROJECT.md §4) — plain strings for stdlib.
# ---------------------------------------------------------------------------

#: Valid cell types.
CELL_TYPES = ("floor", "wall", "doorway")
#: Valid teams.
TEAMS = ("party", "neutral", "hostile")
#: Valid roles.
ROLES = ("gm", "player")
#: Valid entity kinds. DEPRECATED legacy value: "gm_character" is kept ONLY
#: so `Entity.from_dict` (and any in-memory session from an older build) can
#: still load old data without crashing — the GM is now a pure controller
#: with no token, so it is never spawned and never creatable again
#: (docs/design/gm-controller.md §2.7; PROJECT.md §4 PM decision).
ENTITY_KINDS = ("player", "npc", "enemy", "gm_character")

#: Team → awareness color (base rule; an explicit ``Entity.color`` wins if
#: set). Iteration 5 (awareness.py) builds on this; the frontend mirrors it.
TEAM_COLORS: dict[str, str] = {
    "party": "green",    # friend/ally
    "neutral": "white",  # neutral (player or NPC)
    "hostile": "red",    # enemy
}


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


@dataclass
class Grid:
    """A grid map. ``cells[y][x]`` is one of :data:`CELL_TYPES`."""

    name: str = "Untitled map"
    width: int = 0
    height: int = 0
    cells: list[list[str]] = field(default_factory=list)  # cells[y][x]
    image: str | None = None  # filename of uploaded source image (optional)

    def __post_init__(self) -> None:
        if len(self.cells) != self.height:
            raise ValueError(
                f"grid has {len(self.cells)} rows but height={self.height}"
            )
        for y, row in enumerate(self.cells):
            if len(row) != self.width:
                raise ValueError(
                    f"row {y} has {len(row)} cells but width={self.width}"
                )
            for cell in row:
                if cell not in CELL_TYPES:
                    raise ValueError(f"invalid cell type {cell!r} (row {y})")

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form: ``{"name","width","height","cells","image"}``."""
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "cells": [list(row) for row in self.cells],
            "image": self.image,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Grid":
        """Rebuild a :class:`Grid` from its dict form (validated)."""
        return cls(
            name=data.get("name", "Untitled map"),
            width=int(data["width"]),
            height=int(data["height"]),
            cells=[list(row) for row in data["cells"]],
            image=data.get("image"),
        )


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A movable token on the grid (player character, NPC, enemy, ...)."""

    id: str
    name: str
    kind: str  # one of ENTITY_KINDS
    team: str  # one of TEAMS
    x: int
    y: int
    owner: str | None = None  # player id that controls it (GM-controlled = None)
    color: str | None = None  # explicit override; else derived from team

    def __post_init__(self) -> None:
        if self.kind not in ENTITY_KINDS:
            raise ValueError(f"invalid entity kind {self.kind!r}")
        if self.team not in TEAMS:
            raise ValueError(f"invalid team {self.team!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "team": self.team,
            "x": self.x,
            "y": self.y,
            "owner": self.owner,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        return cls(
            id=data["id"],
            name=data["name"],
            kind=data["kind"],
            team=data["team"],
            x=int(data["x"]),
            y=int(data["y"]),
            owner=data.get("owner"),
            color=data.get("color"),
        )


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------


@dataclass
class Player:
    """A connected human (GM or player)."""

    id: str
    name: str
    role: str  # one of ROLES
    entity_id: str | None = None  # the character this player controls

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"invalid role {self.role!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "entity_id": self.entity_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            entity_id=data.get("entity_id"),
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """Authoritative session state (Iteration 5 owns the live instance).

    ``to_dict`` produces the full snapshot broadcast on any mutation
    (PROJECT.md §9): ``{"id", "map", "entities", "players", "fog"}``.
    """

    id: str
    grid: Grid
    entities: dict[str, Entity] = field(default_factory=dict)
    players: dict[str, Player] = field(default_factory=dict)
    fog: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "map": self.grid.to_dict(),
            "entities": [e.to_dict() for e in self.entities.values()],
            "players": [p.to_dict() for p in self.players.values()],
            "fog": self.fog,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=data["id"],
            grid=Grid.from_dict(data["map"]),
            entities={eid: Entity.from_dict(d) for eid, d in data["entities"].items()},
            players={pid: Player.from_dict(d) for pid, d in data["players"].items()},
            fog=bool(data.get("fog", False)),
        )


# ---------------------------------------------------------------------------
# Generic asdict helper (plain-dict conversion for dataclasses).
# ---------------------------------------------------------------------------


def asdict(obj: Any) -> dict[str, Any]:
    """Shallow plain-dict form of a dataclass instance (values passed through)."""
    if not dataclasses.is_dataclass(obj):
        raise TypeError(f"{obj!r} is not a dataclass")
    return dataclasses.asdict(obj)
