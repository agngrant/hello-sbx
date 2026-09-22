"""
Door state-machine mixin for :class:`app.session.GameSession`.

Moved verbatim from ``app/session.py`` (god-module split): the door/safe-door
transition tables (door-features spec §4.1, door-iconography spec §4) and
the door handlers (``_on_door`` / ``_on_safe_door`` + helpers).  The mixin
itself defines NO ``__init__`` — the facade owns all shared state
(``self._lock`` et al.).
"""

from __future__ import annotations

from typing import Any

from app.models import Player, entity_cells
from app.session_common import (
    DOOR_ACTIONS, NOT_ALLOWED, SAFE_DOOR_ACTIONS, _as_int,
)
from app.session_state import SessionBase


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


class SessionDoorMixin(SessionBase):
    """Normal + safe door state machines (door-features spec §4)."""

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
