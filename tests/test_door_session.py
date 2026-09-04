"""Door state-machine + session tests (door-features spec §4, §6, §14).

In-process :class:`app.session.GameSession` with fake connections (no HTTP
server): the door state machine + permission matrix (AC3), the occupancy
guard (AC9), door-aware awareness (AC6 — unchanged, inherits LOS blocking),
door-aware explored-map S/E/H (AC7/AC8), the additive ``map.doors`` wire
field (I3/I5/AC10), and the ``use_map`` door reset (AC14/E3).

Reuses the ``FakeConn``/``attach``/``drive``/``make_grid`` helpers from
``tests/test_session.py``.
"""

from __future__ import annotations

import unittest

from app.grid import build_sample_map
from app.models import Grid
from app.session import GameSession
from tests.test_session import (
    FakeConn,
    attach,
    drive,
    make_grid,
    oracle_visible,
    s_cells,
)

from app.models import Grid
from app.session import GameSession, NO_ROUTE
from tests.test_session import (
    FakeConn,
    attach,
    drive,
    make_grid,
    oracle_visible,
    s_cells,
)

DOOR = {"type": "door"}


def door_msg(x: int, y: int, action: str) -> dict:
    return {**DOOR, "x": x, "y": y, "action": action}


SAFE_DOOR = {"type": "safe_door"}


def safe_msg(x: int, y: int, action: str) -> dict:
    return {**SAFE_DOOR, "x": x, "y": y, "action": action}


def safe(session, conn, x: int, y: int, action: str):
    """Drive one ``safe_door`` message through ``session`` on ``conn``."""
    return drive(session, conn, safe_msg(x, y, action))


class DoorSessionBase(unittest.TestCase):
    """A GameSession on the sample dungeon with a GM + one player, wired."""

    def setUp(self) -> None:
        self.session = GameSession("door", build_sample_map())
        self.gm_s = FakeConn()
        self.p1_s = FakeConn()
        self.gm, e0 = self.session.join(self.gm_s, "Gamer", "gm")
        self.p1, e1 = self.session.join(self.p1_s, "Alice", "player")
        self.assertIsNone(e0)
        self.assertIsNone(e1)
        attach(self.session, self.gm_s)
        attach(self.session, self.p1_s)
        self.p1_ent = self.session.players[self.p1.id].entity_id

    def gm_door(self, x, y, action):
        return drive(self.session, self.gm_s, door_msg(x, y, action))

    def p1_door(self, x, y, action):
        return drive(self.session, self.p1_s, door_msg(x, y, action))


class SafeDoorSessionBase(unittest.TestCase):
    """A GameSession on the sample dungeon with a GM + one player, wired —
    the safe-door counterpart of :class:`DoorSessionBase`, with GM and
    player ``safe_door`` helpers (the safe-door surface is GM-only, so the
    player helpers exist only to assert the role-gate rejection)."""

    def setUp(self) -> None:
        self.session = GameSession("safe-door", build_sample_map())
        self.gm_s = FakeConn()
        self.p1_s = FakeConn()
        self.gm, e0 = self.session.join(self.gm_s, "Gamer", "gm")
        self.p1, e1 = self.session.join(self.p1_s, "Alice", "player")
        self.assertIsNone(e0)
        self.assertIsNone(e1)
        attach(self.session, self.gm_s)
        attach(self.session, self.p1_s)
        self.p1_ent = self.session.players[self.p1.id].entity_id

    def gm_safe(self, x, y, action):
        return safe(self.session, self.gm_s, x, y, action)

    def p1_safe(self, x, y, action):
        return safe(self.session, self.p1_s, x, y, action)

    def gm_door(self, x, y, action):
        return drive(self.session, self.gm_s, door_msg(x, y, action))

    def p1_door(self, x, y, action):
        return drive(self.session, self.p1_s, door_msg(x, y, action))


class TestDoorStateMachine(DoorSessionBase):
    """AC3: legal transitions apply + broadcast; illegal return the exact
    error string in the deterministic validation order (§4.3)."""

    def test_gm_unlock_l_to_u(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "U")

    def test_open_u_to_o(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")

    def test_close_o_to_u(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertIsNone(self.gm_door(5, 5, "close"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "U")

    def test_gm_lock_u_to_l(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "lock"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "L")

    def test_gm_lock_o_force_closes_to_l(self):
        # A7: lock-while-open force-closes (no open-and-locked state).
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertIsNone(self.gm_door(5, 5, "lock"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "L")

    def test_player_open_unlocked(self):
        # A player may OPEN a door that is unlocked (not locked).
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.p1_door(5, 5, "open"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")

    def test_player_close_unlocked(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertIsNone(self.p1_door(5, 5, "close"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "U")

    def test_player_unlock_not_allowed(self):
        self.assertEqual(
            self.p1_door(5, 5, "unlock"),
            {"type": "error", "message": "not allowed"},
        )

    def test_player_lock_not_allowed(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertEqual(
            self.p1_door(5, 5, "lock"),
            {"type": "error", "message": "not allowed"},
        )

    def test_open_locked_door_is_locked(self):
        self.assertEqual(
            self.p1_door(5, 5, "open"),
            {"type": "error", "message": "door is locked"},
        )
        self.assertEqual(
            self.gm_door(5, 5, "open"),
            {"type": "error", "message": "door is locked"},
        )

    def test_close_locked_door_is_locked(self):
        self.assertEqual(
            self.p1_door(5, 5, "close"),
            {"type": "error", "message": "door is locked"},
        )

    def test_unlock_already_unlocked(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertEqual(
            self.gm_door(5, 5, "unlock"),
            {"type": "error", "message": "door is already unlocked"},
        )
        # unlock on an OPEN door is also "already unlocked".
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertEqual(
            self.gm_door(5, 5, "unlock"),
            {"type": "error", "message": "door is already unlocked"},
        )

    def test_lock_already_locked(self):
        self.assertEqual(
            self.gm_door(5, 5, "lock"),
            {"type": "error", "message": "door is already locked"},
        )

    def test_open_already_open(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertEqual(
            self.gm_door(5, 5, "open"),
            {"type": "error", "message": "door is already open"},
        )

    def test_close_already_closed(self):
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertEqual(
            self.gm_door(5, 5, "close"),
            {"type": "error", "message": "door is already closed"},
        )

    def test_not_a_doorway(self):
        # (1,1) is floor, not a doorway.
        self.assertEqual(
            self.gm_door(1, 1, "unlock"),
            {"type": "error", "message": "not a doorway"},
        )

    def test_out_of_bounds(self):
        self.assertEqual(
            self.gm_door(99, 1, "unlock"),
            {"type": "error", "message": "destination out of bounds"},
        )

    def test_bad_action(self):
        self.assertEqual(
            self.gm_door(5, 5, "explode"),
            {"type": "error",
             "message": "action must be one of unlock/lock/open/close"},
        )
        self.assertEqual(
            self.gm_door(5, 5, None),
            {"type": "error",
             "message": "action must be one of unlock/lock/open/close"},
        )

    def test_non_int_coords(self):
        self.assertEqual(
            self.gm_door(True, 5, "unlock"),
            {"type": "error", "message": "x and y must be integers"},
        )
        self.assertEqual(
            self.gm_door("5", 5, "unlock"),
            {"type": "error", "message": "x and y must be integers"},
        )

    def test_validation_order_non_doorway_before_action(self):
        # A non-doorway cell with a bad action: "not a doorway" wins (step 3
        # before step 4).
        self.assertEqual(
            self.gm_door(1, 1, "explode"),
            {"type": "error", "message": "not a doorway"},
        )

    def test_validation_order_state_before_role(self):
        # A player OPENing a LOCKED door: "door is locked" (state, step 5)
        # wins over the role gate (step 6).
        self.assertEqual(
            self.p1_door(5, 5, "open"),
            {"type": "error", "message": "door is locked"},
        )


class TestDoorOccupancy(DoorSessionBase):
    """AC9 / D3 / A5: the occupancy guard fires on transitions that close."""

    def test_close_with_token_rejected(self):
        # Open the door, place a token on it, then close → rejected.
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "place", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))
        self.assertEqual(
            self.gm_door(5, 5, "close"),
            {"type": "error",
             "message": "cannot close a door with a token on it"},
        )
        # The door is still open (the token was not left on a closed door).
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")

    def test_lock_while_open_with_token_rejected(self):
        # A5: lock from open force-closes → same occupancy guard.
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "place", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))
        self.assertEqual(
            self.gm_door(5, 5, "lock"),
            {"type": "error",
             "message": "cannot close a door with a token on it"},
        )
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")

    def test_lock_from_unlocked_not_guarded(self):
        # A5: lock from UNLOCKED (already closed) is NOT occupancy-guarded.
        # (A token on a closed door is the E11 GM-place degenerate case.)
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "place", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))  # GM places on closed door
        self.assertIsNone(self.gm_door(5, 5, "lock"))  # allowed (not guarded)
        self.assertEqual(self.session.grid.door_state_at(5, 5), "L")

    def test_player_lock_on_open_door_with_token_is_not_allowed(self):
        # BUG-DOORS-002: the §4.3 order is role (#6) BEFORE occupancy (#7).
        # A player `lock` on an open door with a token on it must get the
        # role rejection "not allowed" — never the occupancy string.
        # Regression test: the GM close path below is unchanged.
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "place", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))
        # Player `lock` on the open+token door: role check fires first.
        self.assertEqual(
            self.p1_door(5, 5, "lock"),
            {"type": "error", "message": "not allowed"},
        )
        # GM `close` on the same open+token door: still the occupancy error
        # (close is not GM-only, so the role gate passes and occupancy #7
        # fires) — unchanged by the reorder.
        self.assertEqual(
            self.gm_door(5, 5, "close"),
            {"type": "error",
             "message": "cannot close a door with a token on it"},
        )
        # And GM `lock` from open is still the occupancy error (force-close
        # guard, AC9) — the reorder only reorders the player-role path.
        self.assertEqual(
            self.gm_door(5, 5, "lock"),
            {"type": "error",
             "message": "cannot close a door with a token on it"},
        )
        # All three rejections left the door open.
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")


class TestDoorWireState(DoorSessionBase):
    """I3/I5/AC10: every welcome/state map object carries the FULL door set."""

    def test_gm_payload_carries_full_doors(self):
        st = self.session.state_for(self.gm)
        self.assertEqual(
            st["map"]["doors"],
            {"5,5": "L", "10,4": "L", "9,7": "L"},
        )
        self.assertNotIn("visibility", st)  # GM: no visibility key (I3)

    def test_player_payload_carries_full_doors(self):
        st = self.session.state_for(self.p1)
        self.assertEqual(
            st["map"]["doors"],
            {"5,5": "L", "10,4": "L", "9,7": "L"},
        )

    def test_welcome_carries_full_doors(self):
        w = self.session.welcome_for(self.gm)
        self.assertEqual(w["map"]["doors"],
                         {"5,5": "L", "10,4": "L", "9,7": "L"})

    def test_door_change_broadcasts_updated_doors(self):
        # A successful door action broadcasts state carrying the new map.doors
        # (no per-client reply — the broadcast is the source of truth).
        reply = self.gm_door(5, 5, "unlock")
        self.assertIsNone(reply)
        st = self.gm_s.last("state")
        self.assertEqual(st["map"]["doors"]["5,5"], "U")
        st_p = self.p1_s.last("state")
        self.assertEqual(st_p["map"]["doors"]["5,5"], "U")

    def test_no_doorways_omits_doors(self):
        # A grid with no doorways omits the doors key (client ⇒ all locked).
        s = GameSession("x", make_grid(
            [["wall", "wall"], ["wall", "wall"]]))
        s.join(FakeConn(), "G", "gm")
        st = s.state_for(s.players["p1"])
        self.assertNotIn("doors", st["map"])


class TestDoorAwarenessUnchanged(DoorSessionBase):
    """AC6: awareness unchanged in code; door blocking inherits via LOS."""

    def _room_session(self):
        # 5x3: player O at (1,1), enemy at (3,1) BEHIND the doorway (2,1).
        g = make_grid([
            ["wall", "wall", "wall", "wall", "wall"],
            ["wall", "floor", "doorway", "floor", "wall"],
            ["wall", "wall", "wall", "wall", "wall"],
        ])
        s = GameSession("aw", g)
        gm_s, p1_s = FakeConn(), FakeConn()
        s.join(gm_s, "G", "gm")
        p1, _ = s.join(p1_s, "Alice", "player")   # spawns at (1,1)
        s.join  # (no-op)
        attach(s, gm_s)
        attach(s, p1_s)
        # Enemy at (3,1) behind the door.
        drive(s, gm_s, {"type": "create_entity", "name": "E",
                        "kind": "enemy", "team": "hostile", "x": 3, "y": 1})
        ent = next(e for e in s.entities.values() if e.name == "E")
        return s, gm_s, p1_s, p1, p1.entity_id, ent

    def test_closed_door_within_radius_is_approximate(self):
        s, gm_s, p1_s, p1, p1_ent, enemy = self._room_session()
        st = s.state_for(p1)
        aw = st["awareness"]
        # No LOS (door closed) + within default radius (cheb 2) → APPROX.
        self.assertEqual(len(aw), 1)
        item = aw[0]
        self.assertTrue(item["approximate"])
        self.assertNotIn("name", item)
        self.assertNotIn("color", item)

    def test_closed_door_beyond_radius_is_invisible(self):
        s, gm_s, p1_s, p1, p1_ent, enemy = self._room_session()
        s.players[p1.id].awareness_radius = 0  # radius 0 → LOS-only
        st = s.state_for(p1)
        self.assertEqual(st["awareness"], [])  # no LOS, radius 0 → INVISIBLE

    def test_open_door_is_full(self):
        s, gm_s, p1_s, p1, p1_ent, enemy = self._room_session()
        drive(s, gm_s, door_msg(2, 1, "unlock"))
        drive(s, gm_s, door_msg(2, 1, "open"))
        st = s.state_for(p1)
        aw = st["awareness"]
        self.assertEqual(len(aw), 1)
        item = aw[0]
        self.assertFalse(item.get("approximate"))
        self.assertEqual(item["entity_id"], enemy.id)
        self.assertEqual(item["color"], "red")
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "E")

    def test_gm_never_filtered(self):
        s, gm_s, p1_s, p1, p1_ent, enemy = self._room_session()
        st = s.state_for(s.players["p1"])  # GM is p1 here (first joiner)
        # GM sees the enemy FULL regardless of the closed door.
        item = next(i for i in st["awareness"] if i["entity_id"] == enemy.id)
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "E")
        self.assertNotIn("approximate", item)


class TestDoorExploredMap(DoorSessionBase):
    """AC7/AC8: closed-door far side H→S→E; face is S (D5); monotonicity."""

    def test_closed_door_far_side_h_then_s_then_e(self):
        s = self.session
        # The player is at (1,1). (6,6) is the middle-room floor behind the
        # closed (5,5) door.
        w = s.welcome_for(self.p1)
        mask = w["visibility"]
        self.assertEqual(mask[6][6], "H")  # never seen (behind closed door)
        self.assertEqual(mask[5][5], "S")  # D5: the closed door's FACE is S
        seen = s_cells(mask)
        ever_SE = set(seen)

        # GM opens (5,5): (6,6) is now in sight → S, folded into explored.
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        st = self.session.state_for(self.p1)
        self.assertEqual(st["visibility"][6][6], "S")
        ever_SE |= s_cells(st["visibility"])

        # GM closes (5,5) again: (6,6) falls back to E (explored, not H).
        self.assertIsNone(self.gm_door(5, 5, "close"))
        st = self.session.state_for(self.p1)
        self.assertEqual(st["visibility"][6][6], "E")
        # Monotonicity (I9): nothing previously S/E became H.
        regressed = ever_SE & {(x, y) for y in range(12) for x in range(16)
                               if st["visibility"][y][x] == "H"}
        self.assertFalse(regressed)
        # The S-set re-derives from the door-aware LOS at the token position.
        self.assertEqual(s_cells(st["visibility"]),
                         oracle_visible(self.session.grid, (1, 1)))


class TestDoorPaintSync(DoorSessionBase):
    """§9 (D4) via the WS paint handler: doorway→locked, floor/wall→delete."""

    def test_paint_floor_to_doorway_creates_locked_door(self):
        # (4,8) is a floor. Paint it a doorway → a door in the L state.
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "paint", "x": 4, "y": 8,
                                 "cell_type": "doorway"}))
        self.assertEqual(self.session.grid.cells[8][4], "doorway")
        self.assertEqual(self.session.grid.door_state_at(4, 8), "L")
        self.assertTrue(self.session.grid.is_door_closed(4, 8))

    def test_paint_wall_over_door_deletes_state(self):
        # Open (5,5) then paint it a wall → the door state is deleted.
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "paint", "x": 5, "y": 5,
                                 "cell_type": "wall"}))
        self.assertEqual(self.session.grid.cells[5][5], "wall")
        self.assertIsNone(self.session.grid.door_state_at(5, 5))  # not a door


class TestDoorUseMap(DoorSessionBase):
    """AC14/E3: use_map swaps the grid and its door state comes with it."""

    def test_use_map_resets_doors_with_grid(self):
        from app.main import maps_registry
        # Open a door on the sample map...
        self.assertIsNone(self.gm_door(5, 5, "unlock"))
        self.assertIsNone(self.gm_door(5, 5, "open"))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")
        # ...register a different map and swap to it.
        target = make_grid(
            [["wall", "wall", "wall"],
             ["wall", "floor", "doorway"],
             ["wall", "wall", "wall"]])
        maps_registry["door-swap"] = {
            "grid": target, "entities": {}, "players": {}}
        reply = drive(self.session, self.gm_s,
                      {"type": "use_map", "map_id": "door-swap"})
        self.assertIsNone(reply)
        # The session now plays the target grid (with its own, all-locked
        # door state) — the sample's open door did not carry over.
        self.assertIs(self.session.grid, target)
        self.assertEqual(self.session.grid.door_state_at(2, 1), "L")


class TestDoorPerformance(unittest.TestCase):
    """AC15 — door-aware predicates stay within the explored-map budget:
    a 60x60 grid with every carved doorway in a mixed open/closed state, 6
    players + GM, one full recompute (state_for all 6) < 500 ms; a single
    find_path across many open doors < 50 ms."""

    def test_full_recompute_within_budget(self):
        import time
        from app.generation import generate_grid
        grid = generate_grid(60, 60, "door-perf", seed=1)
        grid.doors = {
            f"{x},{y}": ("O" if (x + y) % 2 == 0 else "L")
            for y in range(60) for x in range(60)
            if grid.cells[y][x] == "doorway"
        }
        s = GameSession("door-perf", grid)
        s.join(FakeConn(), "G", "gm")
        for i in range(6):
            s.join(FakeConn(), f"P{i}", "player")
        players = [p for p in s.players.values() if p.role == "player"]
        for p in players:
            s.state_for(p)  # warm-up
        t0 = time.perf_counter()
        for p in players:
            s.state_for(p)
        all_ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(all_ms, 500.0,
                        f"6-player door recompute took {all_ms:.1f} ms")

    def test_find_path_across_open_doors_within_budget(self):
        import time
        from app.generation import generate_grid
        from app.pathfinding import find_path
        grid = generate_grid(60, 60, "door-perf", seed=1)
        grid.doors = {
            f"{x},{y}": "O" for y in range(60) for x in range(60)
            if grid.cells[y][x] == "doorway"
        }
        floors = [(x, y) for y in range(60) for x in range(60)
                  if grid.cells[y][x] == "floor"]
        start, goal = floors[0], floors[len(floors) // 2]
        t0 = time.perf_counter()
        find_path(grid, start, goal)
        ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(ms, 50.0, f"find_path took {ms:.1f} ms")


class TestSafeDoorStateMachine(SafeDoorSessionBase):
    """AC3: the full safe-door state machine + permission matrix — every
    legal transition, every illegal (state, action, role) returns the EXACT
    error string in the §4.3 deterministic order (role first)."""

    # -- permissions (role gate FIRST) -----------------------------------
    def test_non_gm_any_action_not_allowed(self):
        # AC3 step 1: a player gets "not allowed" for EVERY safe action,
        # even on a non-doorway / OOB cell (the role gate runs first).
        for action in ("mark", "unmark", "open", "close"):
            with self.subTest(action=action):
                self.assertEqual(
                    self.p1_safe(5, 5, action),
                    {"type": "error", "message": "not allowed"},
                )
            with self.subTest(action=action, oob=True):
                self.assertEqual(
                    self.p1_safe(99, 1, action),
                    {"type": "error", "message": "not allowed"},
                )

    # -- validation order ---------------------------------------------------
    def test_ints_before_bounds(self):
        self.assertEqual(
            self.gm_safe(True, 5, "mark"),
            {"type": "error", "message": "x and y must be integers"},
        )
        self.assertEqual(
            self.gm_safe("5", 5, "mark"),
            {"type": "error", "message": "x and y must be integers"},
        )

    def test_bounds_before_doorway(self):
        self.assertEqual(
            self.gm_safe(99, 1, "mark"),
            {"type": "error", "message": "destination out of bounds"},
        )

    def test_doorway_before_action(self):
        # (1,1) is floor with a bad action → "not a doorway" wins (step 4
        # before step 5).
        self.assertEqual(
            self.gm_safe(1, 1, "explode"),
            {"type": "error", "message": "not a doorway"},
        )

    def test_bad_action_includes_lock_unlock(self):
        # A safe door has NO lock state → lock/unlock are bad actions.
        self.assertEqual(
            self.gm_safe(5, 5, "lock"),
            {"type": "error",
             "message": "action must be one of mark/unmark/open/close"},
        )
        self.assertEqual(
            self.gm_safe(5, 5, "unlock"),
            {"type": "error",
             "message": "action must be one of mark/unmark/open/close"},
        )
        self.assertEqual(
            self.gm_safe(5, 5, None),
            {"type": "error",
             "message": "action must be one of mark/unmark/open/close"},
        )

    # -- mark / unmark -----------------------------------------------------
    def test_mark_normal_doorway_starts_closed(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertTrue(self.session.grid.is_safe_door(5, 5))
        self.assertEqual(self.session.grid.safe_door_state_at(5, 5), "C")
        # a marked safe door has NO normal-door state (mutual exclusion):
        self.assertIsNone(self.session.grid.door_state_at(5, 5))
        self.assertNotIn("5,5", self.session.grid.doors or {})

    def test_mark_records_existing_normal_state(self):
        # A recorded normal door is DROPPED when it becomes a safe door.
        self.assertIsNone(self.gm_door(5, 5, "unlock"))  # 5,5 → U
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertEqual(self.session.grid.safe, {"5,5": "C"})
        self.assertNotIn("5,5", self.session.grid.doors or {})

    def test_mark_already_safe(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertEqual(
            self.gm_safe(5, 5, "mark"),
            {"type": "error", "message": "already a safe door"},
        )

    def test_unmark_closed_reverts_to_u(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "unmark"))
        self.assertFalse(self.session.grid.is_safe_door(5, 5))
        self.assertIsNone(self.session.grid.safe)
        self.assertEqual(self.session.grid.door_state_at(5, 5), "U")

    def test_unmark_open_reverts_to_o(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertIsNone(self.gm_safe(5, 5, "unmark"))
        self.assertFalse(self.session.grid.is_safe_door(5, 5))
        self.assertEqual(self.session.grid.door_state_at(5, 5), "O")

    def test_unmark_non_safe(self):
        self.assertEqual(
            self.gm_safe(5, 5, "unmark"),
            {"type": "error", "message": "not a safe door"},
        )

    # -- open / close ------------------------------------------------------
    def test_open_c_to_o(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertEqual(self.session.grid.safe_door_state_at(5, 5), "O")

    def test_close_o_to_c(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertIsNone(self.gm_safe(5, 5, "close"))
        self.assertEqual(self.session.grid.safe_door_state_at(5, 5), "C")

    def test_open_non_safe(self):
        self.assertEqual(
            self.gm_safe(5, 5, "open"),
            {"type": "error", "message": "not a safe door"},
        )

    def test_close_non_safe(self):
        self.assertEqual(
            self.gm_safe(5, 5, "close"),
            {"type": "error", "message": "not a safe door"},
        )

    def test_open_already_open(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertEqual(
            self.gm_safe(5, 5, "open"),
            {"type": "error", "message": "safe door is already open"},
        )

    def test_close_already_closed(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertEqual(
            self.gm_safe(5, 5, "close"),
            {"type": "error", "message": "safe door is already closed"},
        )

    # -- occupancy guards (E1 / AC9) ---------------------------------------
    def test_mark_with_token_on_it_rejected(self):
        # Move a token onto the doorway first (GM place on the closed door),
        # then mark → rejected; no safe door is created.
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "place", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))
        self.assertEqual(
            self.gm_safe(5, 5, "mark"),
            {"type": "error",
             "message": "cannot mark a safe door with a token on it"},
        )
        self.assertFalse(self.session.grid.is_safe_door(5, 5))

    def test_close_with_token_on_it_rejected(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "place", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))
        self.assertEqual(
            self.gm_safe(5, 5, "close"),
            {"type": "error",
             "message": "cannot close a door with a token on it"},
        )
        # the door stays open (no entity is left on a closed safe door):
        self.assertEqual(self.session.grid.safe_door_state_at(5, 5), "O")


class TestSafeDoorWireState(SafeDoorSessionBase):
    """I5 / AC1: every welcome/state map object carries map.safe (every safe
    door's state) AND map.doors (normal doors), disjoint and jointly covering
    all doorways; a grid with no safe doors omits map.safe."""

    def test_no_safe_omits_safe_key(self):
        st = self.session.state_for(self.gm)
        self.assertNotIn("safe", st["map"])
        self.assertEqual(st["map"]["doors"],
                         {"5,5": "L", "10,4": "L", "9,7": "L"})

    def test_mark_broadcasts_safe_and_shrinks_doors(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        st_gm = self.session.state_for(self.gm)
        st_p = self.session.state_for(self.p1)
        for st in (st_gm, st_p):
            self.assertEqual(st["map"]["safe"], {"5,5": "C"})
            # doors skips the safe cell → disjoint, jointly covering:
            self.assertEqual(st["map"]["doors"], {"10,4": "L", "9,7": "L"})
            self.assertEqual(
                set(st["map"]["doors"]) | set(st["map"]["safe"]),
                {"5,5", "10,4", "9,7"},
            )

    def test_welcome_carries_safe(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        w = self.session.welcome_for(self.p1)
        self.assertEqual(w["map"]["safe"], {"5,5": "C"})
        self.assertNotIn("5,5", w["map"]["doors"])

    def test_unmark_removes_safe_restores_doors(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "unmark"))
        st = self.session.state_for(self.gm)
        self.assertNotIn("safe", st["map"])  # no safe doors → key omitted
        self.assertEqual(st["map"]["doors"]["5,5"], "U")


class TestSafeDoorRestriction(SafeDoorSessionBase):
    """AC5/AC6: the entity restriction — a hostile cannot path onto/through
    an OPEN safe door (blocked like a wall); party/neutral can. A closed
    safe door blocks movement for ALL teams (like a wall)."""

    def test_hostile_blocked_by_open_safe_door(self):
        # The (5,5) doorway is the gap in the col-5 wall between Alice
        # (left room, at (1,1)) and the right room. Mark it a safe door and
        # open it: a hostile still cannot path through (the open safe door
        # is a wall to it), while a party token can.
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        # GM creates a hostile on the right side and tries to path it back
        # through the open safe door → no route.
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "create_entity", "name": "Vex",
                                 "kind": "enemy", "team": "hostile",
                                 "x": 6, "y": 5}))
        vex = next(e for e in self.session.entities.values() if e.name == "Vex")
        # (6,5) left room side is at (4,5); the only short crossing is the
        # safe door. A hostile crossing it → no route.
        reply = drive(self.session, self.gm_s,
                      {"type": "move", "entity_id": vex.id,
                       "x": 4, "y": 5})
        self.assertEqual(reply, {"type": "error", "message": NO_ROUTE})
        self.assertEqual((vex.x, vex.y), (6, 5))  # unchanged

    def test_party_walks_through_open_safe_door(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        # Alice (1,1) is on the left side; the safe door leads to (6,5).
        # But to keep the assertion deterministic, walk Alice to the other
        # side of the door (through it) — a party token CAN cross an open
        # safe door.
        reply = drive(self.session, self.p1_s,
                      {"type": "move", "entity_id": self.p1_ent, "x": 5, "y": 5})
        self.assertIsNone(reply)
        self.assertEqual(
            (self.session.entities[self.p1_ent].x,
             self.session.entities[self.p1_ent].y), (5, 5))

    def test_closed_safe_door_blocks_all_teams(self):
        # Marked (starts closed) and NOT opened: a party token cannot cross
        # either (a closed safe door is a wall for everyone).
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        reply = drive(self.session, self.p1_s,
                      {"type": "move", "entity_id": self.p1_ent, "x": 5, "y": 5})
        self.assertEqual(reply, {"type": "error", "message": NO_ROUTE})
        self.assertEqual(
            (self.session.entities[self.p1_ent].x,
             self.session.entities[self.p1_ent].y), (1, 1))


class TestSafeDoorHostileOverrideGuard(SafeDoorSessionBase):
    """AC7 / E4: the safety rule — a hostile is NEVER moved/override/
    placed/created/team-set onto a safe-door cell, in EITHER state; a
    party/neutral override onto a CLOSED safe door is allowed (E11)."""

    def test_hostile_override_move_rejected(self):
        # A hostile standing adjacent; override onto the safe cell (both
        # states) → rejected, NOT teleported.
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "create_entity", "name": "Vex",
                                 "kind": "enemy", "team": "hostile",
                                 "x": 6, "y": 5}))
        vex = next(e for e in self.session.entities.values() if e.name == "Vex")
        # (6,5) is adjacent to the safe cell (5,5) — override is the only
        # way to "move" it onto the safe cell (normally no route).
        reply = drive(self.session, self.gm_s,
                      {"type": "move", "entity_id": vex.id, "x": 5, "y": 5,
                       "override": True})
        self.assertEqual(reply, {"type": "error",
                                 "message": "cannot place a hostile on a safe room door"})
        self.assertEqual((vex.x, vex.y), (6, 5))  # NOT teleported

    def test_hostile_place_rejected(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "create_entity", "name": "Vex",
                                 "kind": "enemy", "team": "hostile",
                                 "x": 6, "y": 5}))
        vex = next(e for e in self.session.entities.values() if e.name == "Vex")
        reply = drive(self.session, self.gm_s,
                      {"type": "place", "entity_id": vex.id, "x": 5, "y": 5})
        self.assertEqual(reply, {"type": "error",
                                 "message": "cannot place a hostile on a safe room door"})
        self.assertEqual((vex.x, vex.y), (6, 5))

    def test_hostile_create_rejected(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        reply = drive(self.session, self.gm_s,
                      {"type": "create_entity", "name": "Vex",
                       "kind": "enemy", "team": "hostile", "x": 5, "y": 5})
        self.assertEqual(reply, {"type": "error",
                                 "message": "cannot place a hostile on a safe room door"})
        self.assertNotIn("Vex", [e.name for e in self.session.entities.values()])

    def test_set_team_to_hostile_on_open_safe_door_rejected(self):
        # E4: a party/neutral token standing on an OPEN safe door cannot be
        # re-teamed to hostile (the last path to put a hostile on a safe cell).
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertIsNone(self.gm_safe(5, 5, "open"))
        # Walk Alice onto the open safe door (legal for party).
        self.assertIsNone(drive(self.session, self.p1_s,
                                {"type": "move", "entity_id": self.p1_ent,
                                 "x": 5, "y": 5}))
        self.assertEqual(
            (self.session.entities[self.p1_ent].x,
             self.session.entities[self.p1_ent].y), (5, 5))
        reply = drive(self.session, self.gm_s,
                      {"type": "set_team", "entity_id": self.p1_ent,
                       "team": "hostile"})
        self.assertEqual(reply, {"type": "error",
                                 "message": "cannot place a hostile on a safe room door"})
        self.assertEqual(self.session.entities[self.p1_ent].team, "party")

    def test_party_override_onto_closed_safe_door_allowed(self):
        # E11 (contrast): a party/neutral token CAN be override-placed onto
        # a CLOSED safe door (the GM's ignore-walls ability, like a closed
        # normal door). Override is GM-only (the frozen move-permission rule),
        # so the GM drives it.
        self.assertIsNone(self.gm_safe(5, 5, "mark"))  # starts C, NOT opened
        reply = drive(self.session, self.gm_s,
                      {"type": "move", "entity_id": self.p1_ent, "x": 5, "y": 5,
                       "override": True})
        self.assertIsNone(reply)
        self.assertEqual(
            (self.session.entities[self.p1_ent].x,
             self.session.entities[self.p1_ent].y), (5, 5))
        self.assertEqual(self.session.entities[self.p1_ent].team, "party")

    def test_hostile_override_closed_safe_door_rejected(self):
        # The hostile guard holds even when the door is CLOSED.
        self.assertIsNone(self.gm_safe(5, 5, "mark"))  # C
        self.assertIsNone(drive(self.session, self.gm_s,
                                {"type": "create_entity", "name": "Vex",
                                 "kind": "enemy", "team": "hostile",
                                 "x": 6, "y": 5}))
        vex = next(e for e in self.session.entities.values() if e.name == "Vex")
        reply = drive(self.session, self.gm_s,
                      {"type": "move", "entity_id": vex.id, "x": 5, "y": 5,
                       "override": True})
        self.assertEqual(reply, {"type": "error",
                                 "message": "cannot place a hostile on a safe room door"})
        self.assertEqual((vex.x, vex.y), (6, 5))


class TestSafeDoorNormalDoorGuard(SafeDoorSessionBase):
    """AC13b / E9: a normal ``door`` message on a safe-door cell →
    "not a normal door", and the safe record is untouched (mutual
    exclusion I1 preserved)."""

    def test_door_msg_on_safe_cell_rejected(self):
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        before = dict(self.session.grid.safe or {})
        for action in ("unlock", "lock", "open", "close"):
            with self.subTest(action=action):
                self.assertEqual(
                    self.gm_door(5, 5, action),
                    {"type": "error", "message": "not a normal door"},
                )
        # the safe record is untouched; no doors entry was created:
        self.assertEqual(self.session.grid.safe, before)
        self.assertNotIn("5,5", self.session.grid.doors or {})

    def test_normal_door_messages_still_work_on_normal_door(self):
        # AC13a: the guard never fires for a non-safe cell — existing
        # normal-door behaviour is byte-identical.
        self.assertIsNone(self.gm_door(10, 4, "unlock"))
        self.assertEqual(self.session.grid.door_state_at(10, 4), "U")

    def test_bad_action_on_normal_door_unchanged(self):
        # AC13c: the normal-door bad-action error string is untouched.
        self.assertEqual(
            self.gm_door(10, 4, "explode"),
            {"type": "error",
             "message": "action must be one of unlock/lock/open/close"},
        )


class TestSafeDoorAwarenessUnchanged(SafeDoorSessionBase):
    """AC8: awareness is unchanged in code; safe-door blocking reaches it
    only through the (safe-aware) LOS predicate. A hostile behind a CLOSED
    safe door is APPROXIMATE within radius / INVISIBLE beyond; behind an
    OPEN safe door it is FULL (LOS is team-agnostic). The GM is unfiltered."""

    def _room_session(self):
        # 7x3: Alice at (1,1), the safe-door at (3,1) is the gap, hostile
        # E at (5,1) on the far side.
        g = make_grid([
            ["wall", "wall", "wall", "wall", "wall", "wall", "wall"],
            ["wall", "floor", "floor", "doorway", "floor", "floor", "wall"],
            ["wall", "wall", "wall", "wall", "wall", "wall", "wall"],
        ])
        s = GameSession("safe-aw", g)
        gm_s, p1_s = FakeConn(), FakeConn()
        s.join(gm_s, "G", "gm")
        p1, _ = s.join(p1_s, "Alice", "player")
        attach(s, gm_s)
        attach(s, p1_s)
        drive(s, gm_s, {"type": "create_entity", "name": "E",
                        "kind": "enemy", "team": "hostile", "x": 5, "y": 1})
        ent = next(e for e in s.entities.values() if e.name == "E")
        return s, gm_s, p1_s, p1, ent

    def test_closed_safe_door_within_radius_approximate(self):
        s, gm_s, p1_s, p1, enemy = self._room_session()
        # Alice at (1,1); hostile at (5,1): cheb 4 → within default radius 4.
        self.assertIsNone(safe(s, gm_s, 3, 1, "mark"))  # starts C (closed)
        st = s.state_for(p1)
        aw = st["awareness"]
        self.assertEqual(len(aw), 1)
        item = aw[0]
        self.assertTrue(item["approximate"])  # no LOS (closed) + within radius
        self.assertNotIn("name", item)

    def test_closed_safe_door_beyond_radius_invisible(self):
        s, gm_s, p1_s, p1, enemy = self._room_session()
        self.assertIsNone(safe(s, gm_s, 3, 1, "mark"))
        s.players[p1.id].awareness_radius = 0  # LOS-only → INVISIBLE
        st = s.state_for(p1)
        self.assertEqual(st["awareness"], [])

    def test_open_safe_door_is_full(self):
        s, gm_s, p1_s, p1, enemy = self._room_session()
        self.assertIsNone(safe(s, gm_s, 3, 1, "mark"))
        self.assertIsNone(safe(s, gm_s, 3, 1, "open"))
        st = s.state_for(p1)
        aw = st["awareness"]
        self.assertEqual(len(aw), 1)
        item = aw[0]
        self.assertFalse(item.get("approximate"))
        self.assertEqual(item["entity_id"], enemy.id)
        self.assertEqual(item["color"], "red")
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "E")

    def test_gm_never_filtered(self):
        s, gm_s, p1_s, p1, enemy = self._room_session()
        self.assertIsNone(safe(s, gm_s, 3, 1, "mark"))
        gm = s.players[next(pid for pid, p in s.players.items() if p.role == "gm")]
        st = s.state_for(gm)
        item = next(i for i in st["awareness"] if i["entity_id"] == enemy.id)
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "E")
        self.assertNotIn("approximate", item)


class TestSafeDoorUseMap(SafeDoorSessionBase):
    """AC14 / E10: use_map swaps the grid and its safe state comes with it —
    the new grid carries its own safe doors."""

    def test_use_map_resets_safe_with_grid(self):
        from app.main import maps_registry
        # Mark a safe door on the sample map...
        self.assertIsNone(self.gm_safe(5, 5, "mark"))
        self.assertTrue(self.session.grid.is_safe_door(5, 5))
        # ...register a different map (no safe doors) and swap to it.
        target = make_grid([
            ["wall", "wall", "wall"],
            ["wall", "floor", "doorway"],
            ["wall", "wall", "wall"],
        ])
        maps_registry["safe-swap"] = {
            "grid": target, "entities": {}, "players": {}}
        reply = drive(self.session, self.gm_s,
                      {"type": "use_map", "map_id": "safe-swap"})
        self.assertIsNone(reply)
        self.assertIs(self.session.grid, target)
        # the new grid's safe state is its own (empty) — the sample's safe
        # door did not carry over.
        self.assertIsNone(self.session.grid.safe)
        self.assertNotIn("safe", self.session.state_for(self.gm)["map"])


if __name__ == "__main__":
    unittest.main()
