"""Session unit tests (stdlib unittest; Iteration 5).

Covers PROJECT.md §6 (movement permissions, no-route rejection, GM
override), §8 (1 GM + up to 6 players, "session full"), §5 (per-viewer
awareness, GM sees all, fog-of-war LOS filter), and §9 message handling —
all on an in-process :class:`app.session.GameSession` with fake sockets
(no HTTP server involved).
"""

from __future__ import annotations

import json
import threading
import unittest

from app.grid import build_sample_map
from app.models import Entity, Grid
from app.session import (
    GameSession,
    MAX_PLAYERS,
    NO_ROUTE,
    SESSION_FULL,
)
from app.ws import OP_TEXT, WSClose, read_frame


# ---------------------------------------------------------------------------
# Fake socket: parses the unmasked server frames ``GameSession`` writes via
# ``app.ws.send_json`` into ``out`` (a list of decoded JSON dicts).
# ---------------------------------------------------------------------------


class _Recv:
    def __init__(self, buf: bytearray) -> None:
        self.buf = buf

    def recv(self, n: int) -> bytes:
        if not self.buf:
            raise WSClose(1005, "eof")
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data


class FakeSock:
    def __init__(self) -> None:
        self.buf = bytearray()
        self._recv = _Recv(self.buf)
        self.out: list[dict] = []

    def sendall(self, data: bytes) -> None:
        self.buf += data
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                op, payload = read_frame(self._recv, None)
            except (WSClose, OSError):
                return
            if op == OP_TEXT:
                self.out.append(json.loads(payload.decode("utf-8")))

    # -- test conveniences ---------------------------------------------------

    def last(self, mtype: str | None = None) -> dict:
        """Last sent frame (optionally filtered by ``type``)."""
        frames = [m for m in self.out if mtype is None or m.get("type") == mtype]
        return frames[-1]

    def sent(self, mtype: str) -> list[dict]:
        return [m for m in self.out if m.get("type") == mtype]


def make_grid(rows: list[list[str]]) -> Grid:
    height = len(rows)
    width = len(rows[0])
    return Grid(name="test", width=width, height=height, cells=[list(r) for r in rows])


class SessionTestCase(unittest.TestCase):
    """A GameSession on the 16x12 sample dungeon, fake sockets."""

    def setUp(self) -> None:
        self.session = GameSession("test", build_sample_map())
        self.gm_s = FakeSock()
        self.p1_s = FakeSock()
        self.p2_s = FakeSock()
        self.gm, err = self.session.join(self.gm_s, "Gamer", "gm")
        self.p1, err1 = self.session.join(self.p1_s, "Alice", "player")
        self.p2, err2 = self.session.join(self.p2_s, "Bob", "player")
        self.assertIsNone(err)
        self.assertIsNone(err1)
        self.assertIsNone(err2)
        self.gm_ent = self.session.players[self.gm.id].entity_id
        self.p1_ent = self.session.players[self.p1.id].entity_id
        self.p2_ent = self.session.players[self.p2.id].entity_id


# ---------------------------------------------------------------------------
# Joins / role assignment (§8)
# ---------------------------------------------------------------------------


class TestJoins(unittest.TestCase):
    def test_first_join_role_gm_becomes_gm(self):
        s = GameSession("t", build_sample_map())
        sock = FakeSock()
        player, err = s.join(sock, "G", "gm")
        self.assertIsNone(err)
        self.assertEqual(player.role, "gm")
        ent = s.entities[player.entity_id]
        self.assertEqual(ent.kind, "gm_character")
        self.assertEqual(ent.team, "neutral")
        self.assertIsNone(ent.owner)

    def test_first_client_without_role_becomes_gm(self):
        s = GameSession("t", build_sample_map())
        player, err = s.join(FakeSock(), "First", None)
        self.assertIsNone(err)
        self.assertEqual(player.role, "gm")

    def test_first_player_before_gm_becomes_gm(self):
        s = GameSession("t", build_sample_map())
        player, err = s.join(FakeSock(), "Early", "player")
        self.assertIsNone(err)
        self.assertEqual(player.role, "gm")

    def test_player_gets_owned_party_entity_on_free_floor(self):
        s = GameSession("t", build_sample_map())
        s.join(FakeSock(), "G", "gm")
        p, err = s.join(FakeSock(), "Alice", "player")
        self.assertIsNone(err)
        self.assertEqual(p.role, "player")
        ent = s.entities[p.entity_id]
        self.assertEqual(ent.kind, "player")
        self.assertEqual(ent.team, "party")
        self.assertEqual(ent.owner, p.id)
        cell = s.grid.cells[ent.y][ent.x]
        self.assertIn(cell, ("floor", "doorway"))
        # spawn is on a free cell (row-major first free: GM took (1,1)).
        self.assertEqual((ent.x, ent.y), (2, 1))

    def test_seventh_player_refused_session_full(self):
        s = GameSession("t", build_sample_map())
        s.join(FakeSock(), "G", "gm")
        for i in range(1, MAX_PLAYERS + 1):  # 6 players — all accepted
            p, err = s.join(FakeSock(), f"P{i}", "player")
            self.assertIsNone(err, f"player {i} should be accepted")
            self.assertEqual(p.role, "player")
        self.assertEqual(len(s.players), 1 + MAX_PLAYERS)
        p7, err = s.join(FakeSock(), "P7", "player")
        self.assertIsNone(p7)
        self.assertEqual(err, SESSION_FULL)
        self.assertNotIn("P7", [p.name for p in s.players.values()])
        self.assertEqual(len(s.players), 1 + MAX_PLAYERS)

    def test_second_gm_refused(self):
        s = GameSession("t", build_sample_map())
        s.join(FakeSock(), "G1", "gm")
        gm2, err = s.join(FakeSock(), "G2", "gm")
        self.assertIsNone(gm2)
        self.assertEqual(err, SESSION_FULL)
        self.assertEqual(
            [p.name for p in s.players.values() if p.role == "gm"], ["G1"]
        )

    def test_empty_name_refused(self):
        s = GameSession("t", build_sample_map())
        p, err = s.join(FakeSock(), "   ", "gm")
        self.assertIsNone(p)
        self.assertEqual(err, "name required")

    def test_reconnect_reattaches_same_player(self):
        s = GameSession("t", build_sample_map())
        sock1 = FakeSock()
        # First client of a fresh session: even asking for "player", the
        # role assigned is GM (§8 first-client rule). The client must trust
        # the assigned role — so it reconnects without claiming a role.
        p1, _ = s.join(sock1, "Alice", "player")
        self.assertEqual(p1.role, "gm")
        e_pos = (s.entities[p1.entity_id].x, s.entities[p1.entity_id].y)
        sock2 = FakeSock()
        p2, err = s.join(sock2, "Alice", None)
        self.assertIsNone(err)
        self.assertEqual(p2.id, p1.id)
        self.assertEqual(p2.entity_id, p1.entity_id)
        pos = (s.entities[p2.entity_id].x, s.entities[p2.entity_id].y)
        self.assertEqual(pos, e_pos)  # entity kept
        # old socket is detached, new socket owns the slot.
        self.assertIsNone(s.player_for_sock(sock1))
        self.assertEqual(s.player_for_sock(sock2).id, p1.id)
        # A same-name join with a CONFLICTING role is a different person:
        # accepted as a fresh player (no re-attach onto the GM slot).
        p3, err3 = s.join(FakeSock(), "Alice", "player")
        self.assertIsNone(err3)
        self.assertNotEqual(p3.id, p1.id)
        self.assertEqual(p3.role, "player")

    def test_detached_socket_cannot_act(self):
        s = GameSession("t", build_sample_map())
        sock = FakeSock()
        p, _ = s.join(sock, "Alice", "player")
        s.detach(sock)
        self.assertIsNone(s.player_for_sock(sock))
        reply = s.handle_message(sock, {"type": "request_state"})
        self.assertEqual(reply, {"type": "error", "message": "join first"})
        # the Player slot (and its entity) survived the disconnect.
        self.assertIn(p.id, s.players)


# ---------------------------------------------------------------------------
# Movement & permissions (§6)
# ---------------------------------------------------------------------------


class TestMovement(SessionTestCase):
    def test_player_moves_own_entity_adjacent_floor(self):
        # Alice's entity spawns at (2,1); (3,1) is a free floor cell.
        reply = self.session.handle_message(
            self.p1_s, {"type": "move", "entity_id": self.p1_ent, "x": 3, "y": 1}
        )
        # Successful move: no separate reply — the path frame is broadcast
        # to everyone (sender included) ahead of the state snapshot.
        self.assertIsNone(reply)
        paths = self.p1_s.sent("path")
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["entity_id"], self.p1_ent)
        self.assertEqual(paths[0]["path"][0], {"x": 2, "y": 1})
        self.assertEqual(paths[0]["path"][-1], {"x": 3, "y": 1})
        ent = self.session.entities[self.p1_ent]
        self.assertEqual((ent.x, ent.y), (3, 1))
        # every client got its per-viewer state snapshot for the mutation.
        for sock in (self.gm_s, self.p1_s, self.p2_s):
            self.assertTrue(sock.sent("state"))

    def test_player_cannot_move_another_players_entity(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "move", "entity_id": self.p2_ent, "x": 4, "y": 1}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        ent = self.session.entities[self.p2_ent]
        self.assertEqual((ent.x, ent.y), (3, 1))  # unchanged

    def test_player_cannot_move_gm_entity(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "move", "entity_id": self.gm_ent, "x": 2, "y": 2}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_player_override_is_not_allowed(self):
        reply = self.session.handle_message(
            self.p1_s,
            {"type": "move", "entity_id": self.p1_ent, "x": 2, "y": 3,
             "override": True},
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_gm_can_move_any_entity(self):
        # GM moves ALICE's (p1's) entity — allowed even though it's not
        # the GM's own entity.
        reply = self.session.handle_message(
            self.gm_s, {"type": "move", "entity_id": self.p1_ent, "x": 1, "y": 1}
        )
        self.assertIsNone(reply)
        self.assertTrue(self.gm_s.sent("path"))
        ent = self.session.entities[self.p1_ent]
        self.assertEqual((ent.x, ent.y), (1, 1))

    def test_gm_override_moves_through_wall(self):
        # (2,1) [Alice] -> (4,2): straight line crosses the wall col 5? No —
        # (2,1)->(4,2) is open floor... use a real wall destination: (5,1) is
        # a wall (col 5). Override must teleport straight through it.
        reply = self.session.handle_message(
            self.gm_s,
            {"type": "move", "entity_id": self.p2_ent, "x": 5, "y": 1,
             "override": True},
        )
        self.assertIsNone(reply)
        (path_frame,) = self.gm_s.sent("path")
        self.assertEqual(path_frame["path"], [{"x": 5, "y": 1}])
        ent = self.session.entities[self.p2_ent]
        self.assertEqual((ent.x, ent.y), (5, 1))  # standing in the wall
        self.assertEqual(self.session.grid.cells[1][5], "wall")

    def test_move_into_sealed_region_without_override_is_no_route(self):
        # 9x5 grid: vertical wall col 4 with NO gap → right region sealed.
        grid = make_grid([
            ["wall"] * 9,
            ["wall", "floor", "floor", "floor", "wall", "floor", "floor", "floor", "wall"],
            ["wall", "floor", "floor", "floor", "wall", "floor", "floor", "floor", "wall"],
            ["wall", "floor", "floor", "floor", "wall", "floor", "floor", "floor", "wall"],
            ["wall"] * 9,
        ])
        s = GameSession("t", grid)
        gm_s, p1_s = FakeSock(), FakeSock()
        s.join(gm_s, "G", "gm")
        p, _ = s.join(p1_s, "Alice", "player")  # (2,1)
        ent_id = s.players[p.id].entity_id
        # destination (6,1) is in the sealed region: no path exists.
        reply = s.handle_message(
            p1_s, {"type": "move", "entity_id": ent_id, "x": 6, "y": 1}
        )
        self.assertEqual(reply, {"type": "error", "message": NO_ROUTE})
        self.assertEqual(NO_ROUTE, "no route — wall in the way")
        e = s.entities[ent_id]
        self.assertEqual((e.x, e.y), (2, 1))  # unchanged
        # GM override into the same sealed region works.
        reply = s.handle_message(
            gm_s, {"type": "move", "entity_id": ent_id, "x": 6, "y": 1,
                   "override": True}
        )
        self.assertIsNone(reply)
        self.assertEqual(gm_s.sent("path")[-1]["path"], [{"x": 6, "y": 1}])
        self.assertEqual((e.x, e.y), (6, 1))

    def test_move_out_of_bounds_rejected(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "move", "entity_id": self.p1_ent, "x": 99, "y": 1}
        )
        self.assertEqual(reply["type"], "error")
        self.assertIn("out of bounds", reply["message"])

    def test_move_to_same_cell_is_a_noop_confirmation(self):
        ent = self.session.entities[self.p1_ent]
        reply = self.session.handle_message(
            self.p1_s,
            {"type": "move", "entity_id": self.p1_ent, "x": ent.x, "y": ent.y},
        )
        self.assertEqual(reply["type"], "path")
        self.assertEqual(len(reply["path"]), 1)

    def test_move_to_doorway_is_walkable(self):
        # Alice (2,1) → doorway (5,5): path must exist through the gap.
        reply = self.session.handle_message(
            self.p1_s, {"type": "move", "entity_id": self.p1_ent, "x": 5, "y": 5}
        )
        self.assertIsNone(reply)
        self.assertTrue(self.p1_s.sent("path"))
        self.assertEqual(self.session.entities[self.p1_ent].x, 5)
        self.assertEqual(self.session.entities[self.p1_ent].y, 5)


# ---------------------------------------------------------------------------
# GM tools (place / create / delete / set_team / paint / fog)
# ---------------------------------------------------------------------------


class TestGmTools(SessionTestCase):
    def test_place_by_gm(self):
        reply = self.session.handle_message(
            self.gm_s, {"type": "place", "entity_id": self.p2_ent, "x": 14, "y": 10}
        )
        self.assertIsNone(reply)
        e = self.session.entities[self.p2_ent]
        self.assertEqual((e.x, e.y), (14, 10))

    def test_place_by_player_not_allowed(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "place", "entity_id": self.p1_ent, "x": 14, "y": 10}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_create_entity_by_gm(self):
        reply = self.session.handle_message(
            self.gm_s,
            {"type": "create_entity", "name": "Goblin", "kind": "enemy",
             "team": "hostile", "x": 12, "y": 9},
        )
        self.assertIsNone(reply)
        goblin = [e for e in self.session.entities.values()
                  if e.name == "Goblin"]
        self.assertEqual(len(goblin), 1)
        self.assertEqual(goblin[0].owner, None)
        self.assertEqual(goblin[0].team, "hostile")

    def test_create_entity_by_player_not_allowed(self):
        reply = self.session.handle_message(
            self.p1_s,
            {"type": "create_entity", "name": "X", "kind": "enemy",
             "team": "hostile", "x": 1, "y": 1},
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_delete_gm_character_allowed_player_entity_blocked(self):
        # The GM's own entity (owner None) can be deleted...
        reply = self.session.handle_message(
            self.gm_s, {"type": "delete_entity", "entity_id": self.gm_ent}
        )
        self.assertIsNone(reply)
        self.assertNotIn(self.gm_ent, self.session.entities)
        # ...but a connected player's controlling entity cannot (no orphan).
        reply = self.session.handle_message(
            self.gm_s, {"type": "delete_entity", "entity_id": self.p1_ent}
        )
        self.assertEqual(reply["type"], "error")
        self.assertIn("cannot delete", reply["message"])
        self.assertIn(self.p1_ent, self.session.entities)

    def test_delete_by_player_not_allowed(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "delete_entity", "entity_id": self.p2_ent}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_set_team_by_gm_and_rejection_for_player(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "set_team", "entity_id": self.p2_ent,
                        "team": "hostile"}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        reply = self.session.handle_message(
            self.gm_s, {"type": "set_team", "entity_id": self.p2_ent,
                        "team": "hostile"}
        )
        self.assertIsNone(reply)
        self.assertEqual(self.session.entities[self.p2_ent].team, "hostile")

    def test_paint_by_gm_changes_cell_not_by_player(self):
        reply = self.session.handle_message(
            self.p1_s, {"type": "paint", "x": 2, "y": 2, "cell_type": "wall"}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        self.assertEqual(self.session.grid.cells[2][2], "floor")
        reply = self.session.handle_message(
            self.gm_s, {"type": "paint", "x": 2, "y": 2, "cell_type": "wall"}
        )
        self.assertIsNone(reply)
        self.assertEqual(self.session.grid.cells[2][2], "wall")
        # and it's reflected in the state payload broadcast.
        st = self.session.state_for(self.gm)
        self.assertEqual(st["map"]["cells"][2][2], "wall")

    def test_set_fog_by_gm_and_rejection_for_player(self):
        reply = self.session.handle_message(self.p1_s, {"type": "set_fog", "on": True})
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        self.assertFalse(self.session.fog)
        reply = self.session.handle_message(self.gm_s, {"type": "set_fog", "on": True})
        self.assertIsNone(reply)
        self.assertTrue(self.session.fog)


# ---------------------------------------------------------------------------
# Per-viewer state (§5)
# ---------------------------------------------------------------------------


class TestStateFor(SessionTestCase):
    def test_gm_state_has_full_entities_and_labeled_awareness(self):
        st = self.session.state_for(self.gm)
        self.assertEqual(st["type"], "state")
        self.assertEqual(len(st["entities"]), 3)  # all three
        self.assertEqual(len(st["players"]), 3)
        self.assertFalse(st["fog"])
        aw = st["awareness"]
        self.assertEqual(len(aw), 3)  # GM sees ALL, incl. own gm_character
        for item in aw:
            self.assertTrue(item["label"])
            self.assertIn("name", item)
            self.assertIn("kind", item)
        colors = {i["entity_id"]: i["color"] for i in aw}
        self.assertEqual(colors[self.gm_ent], "white")     # neutral gm_character
        self.assertEqual(colors[self.p1_ent], "green")     # party
        self.assertEqual(colors[self.p2_ent], "green")

    def test_player_state_has_no_entities_and_correct_awareness(self):
        st = self.session.state_for(self.p1)
        self.assertEqual(st["entities"], [])  # players get awareness only
        # own entity rides along (additive field) so the client can render it
        self.assertEqual(st["you_entity"]["id"], self.p1_ent)
        aw = st["awareness"]
        self.assertNotIn(self.p1_ent, [i["entity_id"] for i in aw])  # self excluded
        by_id = {i["entity_id"]: i for i in aw}
        self.assertEqual(set(by_id), {self.gm_ent, self.p2_ent})
        # green friend (Bob's party entity), white neutral (GM character)
        self.assertEqual(by_id[self.p2_ent]["color"], "green")
        self.assertEqual(by_id[self.gm_ent]["color"], "white")
        for item in aw:
            self.assertFalse(item["label"])  # dots only, no names
            self.assertNotIn("name", item)

    def test_red_enemy_in_player_awareness(self):
        self.session.handle_message(
            self.gm_s,
            {"type": "create_entity", "name": "Goblin", "kind": "enemy",
             "team": "hostile", "x": 12, "y": 9},
        )
        st = self.session.state_for(self.p1)
        goblin = [e for e in self.session.entities.values()
                  if e.name == "Goblin"][0]
        item = next(i for i in st["awareness"] if i["entity_id"] == goblin.id)
        self.assertEqual(item["color"], "red")

    def test_welcome_is_state_plus_you(self):
        w = self.session.welcome_for(self.p1)
        self.assertEqual(w["type"], "welcome")
        self.assertEqual(w["you"], {
            "id": self.p1.id, "name": "Alice", "role": "player",
            "entity_id": self.p1_ent,
        })
        self.assertIn("map", w)
        self.assertIn("entities", w)
        self.assertIn("awareness", w)
        self.assertIn("players", w)

    def test_welcome_broadcast_on_join(self):
        sock = FakeSock()
        reply = self.session.handle_message(
            sock, {"type": "join", "name": "Carol", "role": "player"}
        )
        self.assertIsNone(reply)  # the welcome went out directly
        # joiner received its welcome; the others received their own state.
        self.assertEqual(sock.last("welcome")["you"]["name"], "Carol")
        self.assertTrue(self.gm_s.sent("state"))
        self.assertTrue(self.p1_s.sent("state"))
        # Carol's state: players get no entities; awareness excludes self.
        st_carol = sock.last("welcome")
        self.assertEqual(st_carol["entities"], [])
        self.assertEqual(len(st_carol["awareness"]), 3)
        # and a rejected join sends NO welcome at all.
        s2 = GameSession("t2", build_sample_map())
        s2.join(FakeSock(), "G", "gm")
        for i in range(MAX_PLAYERS):
            s2.join(FakeSock(), f"P{i}", "player")
        bad = FakeSock()
        err_reply = s2.handle_message(bad, {"type": "join", "name": "P7", "role": "player"})
        self.assertEqual(err_reply, {"type": "error", "message": SESSION_FULL})
        self.assertEqual(bad.out, [])  # nothing was sent on the refused join


# ---------------------------------------------------------------------------
# Fog of war (§5, §1 soft): LOS filter for players, never for the GM
# ---------------------------------------------------------------------------


class TestFog(unittest.TestCase):
    def setUp(self) -> None:
        # 7x5: floor row y=1 and y=3, fully separated by wall row y=2.
        grid = make_grid([
            ["wall"] * 7,
            ["wall", "floor", "floor", "floor", "floor", "floor", "wall"],
            ["wall", "wall", "wall", "wall", "wall", "wall", "wall"],
            ["wall", "floor", "floor", "floor", "floor", "floor", "wall"],
            ["wall"] * 7,
        ])
        self.session = GameSession("t", grid)
        self.gm_s = FakeSock()
        self.p1_s = FakeSock()
        gm, _ = self.session.join(self.gm_s, "G", "gm")     # (1,1)
        p1, _ = self.session.join(self.p1_s, "Alice", "player")  # (2,1)
        self.gm = gm
        self.p1 = p1
        # Alice at (2,1); friendly Bob at (4,1) — LOS over floor row y=1.
        # Enemy at (4,3) — behind the wall row: NO LOS from (2,1).
        p2, _ = self.session.join(FakeSock(), "Bob", "player")  # (3,1)
        self.bob_ent = self.session.players[p2.id].entity_id
        self.session.handle_message(
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 4, "y": 1}
        )
        self.session.handle_message(
            self.gm_s,
            {"type": "create_entity", "name": "Shade", "kind": "enemy",
             "team": "hostile", "x": 4, "y": 3},
        )
        self.shade = [e for e in self.session.entities.values()
                      if e.name == "Shade"][0]

    def test_fog_off_radar_passes_through_walls(self):
        self.assertFalse(self.session.fog)
        aw = self.session.state_for(self.p1)["awareness"]
        ids = {i["entity_id"] for i in aw}
        # Alice sees everything, including the shade behind the wall.
        self.assertIn(self.shade.id, ids)
        self.assertIn(self.bob_ent, ids)

    def test_fog_on_filters_player_by_los_but_not_gm(self):
        self.session.handle_message(self.gm_s, {"type": "set_fog", "on": True})
        self.assertTrue(self.session.fog)

        p_aw = self.session.state_for(self.p1)["awareness"]
        p_ids = {i["entity_id"] for i in p_aw}
        self.assertIn(self.bob_ent, p_ids)      # clear LOS (same floor row)
        self.assertNotIn(self.shade.id, p_ids)  # wall blocks LOS
        # Alice's own entity never appears (excluded by build_awareness).
        self.assertNotIn(self.p1.entity_id, p_ids)

        g_aw = self.session.state_for(self.gm)["awareness"]
        g_ids = {i["entity_id"] for i in g_aw}
        # The GM is NEVER fogged: sees the shade too, labeled (4 entities:
        # gm_character, Alice, Bob, Shade).
        self.assertIn(self.shade.id, g_ids)
        self.assertEqual(len(g_aw), 4)
        for item in g_aw:
            self.assertTrue(item["label"])

    def test_previously_seen_stays_visible(self):
        self.session.handle_message(self.gm_s, {"type": "set_fog", "on": True})
        # Bob is visible first (clear LOS) → marked as seen...
        aw = self.session.state_for(self.p1)["awareness"]
        self.assertIn(self.bob_ent, {i["entity_id"] for i in aw})
        # ...then the GM relocates Bob behind the wall: no LOS now, but Bob
        # stays visible because he was previously seen.
        self.session.handle_message(
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 4, "y": 3}
        )
        aw = self.session.state_for(self.p1)["awareness"]
        bob = next(i for i in aw if i["entity_id"] == self.bob_ent)
        self.assertEqual((bob["x"], bob["y"]), (4, 3))  # still shown, moved
        # A never-seen entity in the dark stays hidden.
        self.assertNotIn(self.shade.id, {i["entity_id"] for i in aw})


# ---------------------------------------------------------------------------
# Robustness: the frontend drives this directly — missing fields must be
# errors, never crashes (§9, task "robust").
# ---------------------------------------------------------------------------


class TestRobustness(SessionTestCase):
    def test_missing_fields_yield_errors_not_crashes(self):
        cases = [
            {"type": "move"},
            {"type": "move", "entity_id": self.p1_ent},
            {"type": "move", "entity_id": self.p1_ent, "x": 1.5, "y": 1},
            {"type": "move", "entity_id": None, "x": 1, "y": 1},
            {"type": "move", "entity_id": "ghost", "x": 1, "y": 1},
            {"type": "place"},
            {"type": "create_entity"},
            {"type": "create_entity", "name": "", "kind": "npc", "team": "party",
             "x": 1, "y": 1},
            {"type": "create_entity", "name": "x", "kind": "spaceship",
             "team": "party", "x": 1, "y": 1},
            {"type": "create_entity", "name": "x", "kind": "npc",
             "team": "evil", "x": 1, "y": 1},
            {"type": "delete_entity"},
            {"type": "set_team", "entity_id": self.p1_ent},
            {"type": "set_team", "entity_id": self.p1_ent, "team": "evil"},
            {"type": "paint"},
            {"type": "paint", "x": "2", "y": 2, "cell_type": "wall"},
            {"type": "paint", "x": 99, "y": 2, "cell_type": "wall"},
            {"type": "paint", "x": 2, "y": 2, "cell_type": "lava"},
            {"type": "set_fog"},
            {"type": "request_state"},
            {"type": "teleport_to", "x": 1, "y": 1},
            {"type": None},
            "just a string",
            42,
            None,
            ["a", "list"],
        ]
        for msg in cases:
            with self.subTest(msg=msg):
                reply = self.session.handle_message(self.p1_s, msg)
                self.assertIsInstance(reply, dict)
                if (
                    isinstance(msg, dict)
                    and msg.get("type") == "request_state"
                ):
                    self.assertEqual(reply["type"], "state")  # valid ask
                else:
                    self.assertEqual(reply["type"], "error")
                    self.assertIsInstance(reply["message"], str)
                    self.assertTrue(reply["message"])

    def test_gm_only_tools_rejected_before_validation(self):
        # A player sending a totally malformed GM tool still gets "not
        # allowed" (the role gate fires first) — no crash, no mutation.
        reply = self.session.handle_message(self.p1_s, {"type": "create_entity"})
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_concurrent_joins_are_consistent(self):
        s = GameSession("t", build_sample_map())
        results = []
        lock = threading.Lock()

        def do_join(i: int) -> None:
            p, err = s.join(FakeSock(), f"P{i}", "player")
            with lock:
                results.append((i, p.role if p else None, err))

        # First join (GM) serialised, then 8 racing player joins: at most 6
        # may win.
        s.join(FakeSock(), "G", "gm")
        threads = [threading.Thread(target=do_join, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        accepted = [r for r in results if r[2] is None]
        rejected = [r for r in results if r[2] == SESSION_FULL]
        self.assertEqual(len(accepted), MAX_PLAYERS)
        self.assertEqual(len(rejected), 8 - MAX_PLAYERS)
        self.assertEqual(len(s.players), 1 + MAX_PLAYERS)


# ---------------------------------------------------------------------------
# BUG-002: "use_map" — GM swaps the session's grid to an uploaded map
# WITHOUT changing the session id (players stay in the same session).
# ---------------------------------------------------------------------------


class TestUseMap(unittest.TestCase):
    @staticmethod
    def _small_grid(w: int = 2, h: int = 2) -> Grid:
        return Grid(
            name="Small", width=w, height=h,
            cells=[["floor"] * w for _ in range(h)],
        )

    def test_use_map_swaps_grid_keeps_players(self):
        from app.main import maps_registry
        sid = "usemap-test"
        target = self._small_grid(2, 2)
        maps_registry[sid] = {"grid": target, "entities": {}, "players": {}}
        s = GameSession("use", build_sample_map())  # starts on sample map
        self.assertEqual((s.grid.width, s.grid.height), (16, 12))
        gm_s, p_s = FakeSock(), FakeSock()
        gm, _ = s.join(gm_s, "G", "gm")
        p, _ = s.join(p_s, "A", "player")
        gm_ent, p_ent = gm.entity_id, p.entity_id
        # Player A spawns at (2,1) — OUT OF BOUNDS for the 2x2 target grid.
        self.assertEqual((s.entities[p_ent].x, s.entities[p_ent].y), (2, 1))

        # GM requests the CURRENT session play the uploaded map (same session).
        reply = s.handle_message(gm_s, {"type": "use_map", "map_id": sid})
        self.assertIsNone(reply)
        # The session's grid IS the uploaded grid — shared object identity, so
        # a later GM/REST paint still mutates the grid everyone sees.
        self.assertIs(s.grid, target)
        # Out-of-bounds entities were re-placed onto a free cell of the new map.
        ea = s.entities[p_ent]
        self.assertNotEqual((ea.x, ea.y), (2, 1))
        for e in s.entities.values():
            self.assertGreaterEqual(e.x, 0)
            self.assertLess(e.x, target.width)
            self.assertLess(e.y, target.height)
            self.assertIn(target.cells[e.y][e.x], ("floor", "doorway"))
        # The player is NOT stranded: it got the new state on its own socket
        # (same session) and its entity still exists.
        self.assertTrue(p_s.sent("state"))
        self.assertIn(p_ent, s.entities)
        # A late joiner's welcome picks up the new grid.
        late_p, err = s.join(FakeSock(), "Z", "player")
        self.assertIsNone(err)
        welcome = s.welcome_for(late_p)
        self.assertEqual((welcome["map"]["width"], welcome["map"]["height"]),
                         (target.width, target.height))

    def test_use_map_is_gm_only_and_validates(self):
        from app.main import maps_registry
        maps_registry["usemap-2"] = {"grid": self._small_grid(), "entities": {}, "players": {}}
        s = GameSession("u2", build_sample_map())
        gm_s, p_s = FakeSock(), FakeSock()
        s.join(gm_s, "G", "gm")
        s.join(p_s, "A", "player")
        # A player cannot switch the map (GM-only).
        reply = s.handle_message(p_s, {"type": "use_map", "map_id": "usemap-2"})
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        # Unknown map -> error, grid unchanged.
        before = s.grid
        reply = s.handle_message(gm_s, {"type": "use_map", "map_id": "nope"})
        self.assertEqual(reply["type"], "error")
        self.assertIs(s.grid, before)
        # Missing map_id -> error.
        reply = s.handle_message(gm_s, {"type": "use_map"})
        self.assertEqual(reply, {"type": "error", "message": "map_id required"})


# ---------------------------------------------------------------------------
# Spawn fallback: _find_free_floor must never deliberately place an entity on
# a wall when a walkable cell exists (QA cosmetic note).
# ---------------------------------------------------------------------------


class TestFindFreeFloorFallback(unittest.TestCase):
    def test_falls_back_to_first_non_wall_cell_not_1_1(self):
        # 3x3 with (1,1) as a WALL and exactly two floor cells, both occupied.
        # Old fallback returned (1,1) == a wall. New fallback must return the
        # first in-bounds NON-wall cell, (1,0), even though it is occupied.
        g = make_grid([
            ["wall", "floor", "wall"],
            ["wall", "wall", "wall"],
            ["wall", "floor", "wall"],
        ])
        s = GameSession("t", g)
        for (x, y) in [(1, 0), (1, 2)]:
            s.entities[f"e{x},{y}"] = Entity(
                id=f"e{x},{y}", name=f"o{x},{y}", kind="npc",
                team="neutral", x=x, y=y,
            )
        self.assertEqual(s._find_free_floor(), (1, 0))

    def test_falls_back_to_single_occupied_non_wall_not_1_1(self):
        # 2x2: one floor (0,0), rest walls incl. (1,1). The only non-wall cell
        # is occupied, so no free floor exists. Fallback returns the first
        # non-wall cell (0,0), NOT the (1,1) wall.
        g = make_grid([
            ["floor", "wall"],
            ["wall", "wall"],
        ])
        s = GameSession("t", g)
        s.entities["e1"] = Entity(
            id="e1", name="o", kind="npc", team="neutral", x=0, y=0,
        )
        self.assertEqual(s._find_free_floor(), (0, 0))

    def test_fully_walled_grid_returns_1_1(self):
        # No non-wall cell at all -> the (1,1) fallback (and a logged note).
        g = make_grid([["wall"] * 3 for _ in range(3)])
        s = GameSession("t", g)
        self.assertEqual(s._find_free_floor(), (1, 1))

    def test_free_floor_wins_over_fallback(self):
        # Regression guard: when a free floor exists it is chosen (the fallback
        # must not shadow a real floor). (1,0) is occupied; the first row-major
        # FREE floor is (1,1).
        g = make_grid([
            ["wall", "floor", "wall"],
            ["wall", "floor", "wall"],
        ])
        s = GameSession("t", g)
        s.entities["e1"] = Entity(
            id="e1", name="o", kind="npc", team="neutral", x=1, y=0,
        )
        self.assertEqual(s._find_free_floor(), (1, 1))



if __name__ == "__main__":
    unittest.main()
