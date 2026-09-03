"""LittleDungeons data models (PROJECT.md §4) — stdlib ``dataclasses`` + plain dicts.

These models are shared between the server, the tests, and (via JSON) the
frontend. Cell types are stored implicitly as a 2D array of strings inside
:class:`Grid` — ``cells[y][x]`` — per the contract (there is no separate
``Cell`` object).

Conversion to/from plain dicts is manual (``to_dict`` / ``from_dict``) so the
REST/WS payloads are exactly the shapes the frontend already consumes:

* ``Grid``:      ``{"name", "width", "height", "cells", "image"}``
* ``Entity``:    ``{"id", "name", "kind", "team", "x", "y", "owner", "color"}``
* ``Player``:    ``{"id", "name", "role", "entity_id", "awareness_radius"}``
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
# Door states (docs/design/door-features.md §3) — an additive feature on
# top of the frozen cell vocabulary: a door is a ``doorway`` CELL plus one
# of these state chars (there is no fourth cell type).
# ---------------------------------------------------------------------------

#: The three door states, as single-letter wire chars (spec §3.1):
#: ``"L"`` = closed + locked (the DEFAULT: absent/None ⇒ every door is L),
#: ``"U"`` = closed, unlocked, ``"O"`` = open. A door is CLOSED iff its
#: state is not ``"O"``; LOCKED iff it is ``"L"``.
DOOR_STATES = ("L", "U", "O")


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


@dataclass
class Grid:
    """A grid map. ``cells[y][x]`` is one of :data:`CELL_TYPES`.

    ``doors`` (additive, docs/design/door-features.md §3) is a sparse
    ``{"<x>,<y>": "L"|"U"|"O"}`` map of door state over the grid's
    ``doorway`` cells. ``None`` means "no door state recorded" — every door
    is then ``"L"`` (closed + locked, the default). A missing key for a
    doorway cell is likewise ``"L"``.
    """

    name: str = "Untitled map"
    width: int = 0
    height: int = 0
    cells: list[list[str]] = field(default_factory=list)  # cells[y][x]
    image: str | None = None  # filename of uploaded source image (optional)
    doors: dict[str, str] | None = None  # NEW (D1): door state per doorway

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
        # Door state (spec §3.3): ``None`` stays ``None`` (all doors locked);
        # otherwise every key must be a well-formed in-bounds ``"<x>,<y>``
        # over a ``doorway`` cell with a valid state char.
        if self.doors is None:
            return
        clean: dict[str, str] = {}
        for key, st in self.doors.items():
            if not isinstance(key, str) or "," not in key:
                raise ValueError(f"invalid door key {key!r}")
            xs, ys = key.split(",")
            try:
                x, y = int(xs), int(ys)
            except ValueError:
                raise ValueError(f"invalid door key {key!r}") from None
            if st not in DOOR_STATES:
                raise ValueError(f"invalid door state {st!r} at {key!r}")
            if not (0 <= x < self.width and 0 <= y < self.height):
                raise ValueError(f"door key {key!r} out of bounds")
            if self.cells[y][x] != "doorway":
                raise ValueError(f"door at {key!r} is not on a doorway cell")
            clean[key] = st
        self.doors = clean

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form: ``{"name","width","height","cells","image"}``
        plus the additive ``"doors"`` object (spec §8.1: emitted whenever any
        door state is recorded — i.e. whenever the grid has a door; omitted
        entirely for an all-default/locked grid so legacy payloads parse
        unchanged and round-trip to ``doors=None``)."""
        d = {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "cells": [list(row) for row in self.cells],
            "image": self.image,
        }
        if self.doors:
            d["doors"] = dict(self.doors)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Grid":
        """Rebuild a :class:`Grid` from its dict form (validated).

        The ``doors`` key is optional: absent/``None`` ⇒ every door locked
        (spec §3.4, A2 — the safe backward-compat default).
        """
        return cls(
            name=data.get("name", "Untitled map"),
            width=int(data["width"]),
            height=int(data["height"]),
            cells=[list(row) for row in data["cells"]],
            image=data.get("image"),
            doors=data.get("doors"),
        )

    # -- door state accessors (spec §3.5) ---------------------------------

    def door_state_at(self, x: int, y: int) -> str | None:
        """The door state char at ``(x, y)``.

        ``None`` when the cell is not a ``doorway``; ``"L"`` for a doorway
        with no recorded state (the closed + locked default); the recorded
        ``"L"|"U"|"O"`` otherwise.
        """
        if self.cells[y][x] != "doorway":
            return None
        if self.doors is None:
            return "L"
        return self.doors.get(f"{x},{y}", "L")

    def is_door_closed(self, x: int, y: int) -> bool:
        """True iff ``(x, y)`` is a doorway whose state is not open (``L``
        or ``U``). A closed door is a wall for LOS + movement."""
        st = self.door_state_at(x, y)
        return st is not None and st != "O"

    def set_door(self, x: int, y: int, state: str) -> None:
        """Set ``(x, y)``'s door to ``state``; materializes ``self.doors``.

        Raises ``ValueError`` if ``(x, y)`` is not a doorway or ``state`` is
        not in :data:`DOOR_STATES` (the WS handler validates first).
        """
        if self.cells[y][x] != "doorway":
            raise ValueError(f"no door at ({x},{y})")
        if state not in DOOR_STATES:
            raise ValueError(f"invalid door state {state!r}")
        self.doors = dict(self.doors or {})
        self.doors[f"{x},{y}"] = state

    def sync_doors_after_cell_set(self, x: int, y: int) -> None:
        """D4: keep ``doors`` consistent after a cell is (re)typed by paint.

        Painted to ``doorway`` → the door exists in the DEFAULT ``L`` state
        (only added if not already recorded, so a repainted door keeps its
        current state — painting is a no-op for door state on an existing
        doorway). Painted to ``floor``/``wall`` → the door state is DELETED
        (a cell that is no longer a doorway has no door).

        This is the single paint-sync point (spec §9): the WS ``paint``
        handler and the REST paint route both call it right after setting
        ``grid.cells[y][x]``, so door state can never desync from the cell
        type at runtime (``__post_init__`` only runs at construction).
        """
        if self.doors is None:
            if self.cells[y][x] == "doorway":
                self.doors = {}  # materialize; the door is L by default
            return
        key = f"{x},{y}"
        if self.cells[y][x] != "doorway":
            self.doors.pop(key, None)
        # if still a doorway: leave the entry (or its absence) untouched.

    def doors_for_wire(self) -> dict[str, str] | None:
        """The full door object for the WIRE/REST (spec §8.1/§8.2, A9, I5).

        Whenever the grid has >= 1 doorway cell, return an object holding
        EVERY door's current state (unrecorded doorways default to ``"L"``),
        so the wire is unambiguous ("is this door open?") — the client
        otherwise cannot tell an open door from an unrecorded one. A grid
        with NO doorway cells returns ``None`` (the key is omitted; the
        client ⇒ all locked). This is the wire/REST policy and differs from
        :meth:`to_dict`, which only emits ``doors`` when recorded state
        exists (AC1 round-trip). The state machine (spec §4.3/§15 AC10) pins
        this: every welcome/state/REST map object carries it.
        """
        full: dict[str, str] = {}
        recorded = self.doors or {}
        found = False
        for y in range(self.height):
            for x in range(self.width):
                if self.cells[y][x] != "doorway":
                    continue
                found = True
                full[f"{x},{y}"] = recorded.get(f"{x},{y}", "L")
        return full if found else None


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
    # Chebyshev squares of the APPROXIMATE awareness tier (the no-line-of-
    # sight range): GM-adjustable via "set_awareness" within 0–20
    # (app.awareness.AWARENESS_MIN/MAX); the default is the legacy fixed
    # APPROX_RADIUS (4). docs/design/awareness-ring.md §2.
    awareness_radius: int = 4

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"invalid role {self.role!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "entity_id": self.entity_id,
            "awareness_radius": self.awareness_radius,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        # awareness_radius (0–20, default 4): out-of-range or invalid
        # values are silently clamped on read — the live "set_awareness"
        # setter enforces the same range and errors.
        radius = data.get("awareness_radius")
        if radius is None:
            radius = 4
        else:
            try:
                radius = int(radius)
            except (TypeError, ValueError):
                radius = 4
        radius = max(0, min(20, radius))
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            entity_id=data.get("entity_id"),
            awareness_radius=radius,
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
