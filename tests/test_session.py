"""Session unit tests (stdlib unittest; Iteration 5).

Covers PROJECT.md §6 (movement permissions, no-route rejection, GM
override), §8 (1 GM + up to 6 players, "session full"), §5 (per-viewer
awareness: the GM sees all labeled; the player three-tier model — FULL on
line of sight, APPROXIMATE within 4 squares without LOS, INVISIBLE
beyond), and §9 message handling — all on an in-process
:class:`app.session.GameSession` with fake connections (no HTTP server
involved).

Architecture note (FastAPI migration): the session's outbound frames no
longer go through hand-written raw-socket frames. The session now calls
``attach_async(conn, send_coro)`` to register an ASYNC SENDER per
connection, and every broadcast coroutine is scheduled by
``app.session._schedule`` onto the RUN event loop (when none is running the
broadcast coroutine is dropped — an in-process unit test would then observe
zero frames). These tests therefore drive ``handle_message`` through
:func:`drive`, which spins up a throwaway event loop for the single message
and waits until its broadcast work finishes; the fakes below are plain
objects whose ``send`` is an ``async def`` that appends to ``out``.
"""

from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------------------
# Fake connection: the session registers it with attach_async(conn, conn.send)
# and its broadcast frames (``async def send(obj)``) land in ``out`` (a list
# of decoded JSON dicts).
# ---------------------------------------------------------------------------


class FakeConn:
    """In-memory stand-in for a live WebSocket connection.

    In the real stack the server registers ``websocket.send_text`` as the
    async sender; here :meth:`send` appends the payload dict to :attr:`out`
    (the frame is "written" in the order the event loop executes the
    broadcasts, which matches wire order).
    """

    def __init__(self) -> None:
        self.out: list[dict] = []

    async def send(self, obj: dict) -> None:
        self.out.append(obj)

    # -- test conveniences ---------------------------------------------------

    def last(self, mtype: str | None = None) -> dict:
        """Last sent frame (optionally filtered by ``type``)."""
        frames = [m for m in self.out if mtype is None or m.get("type") == mtype]
        return frames[-1]

    def sent(self, mtype: str) -> list[dict]:
        return [m for m in self.out if m.get("type") == mtype]


def attach(session: GameSession, conn: FakeConn) -> None:
    """Register ``conn``'s async sender with the session (the server does
    this at connection time; unit tests do it right after construction)."""
    session.attach_async(conn, conn.send)


def drive(session: GameSession, conn: FakeConn, msg) -> dict | None:
    """Drive one message through the session and wait for its broadcasts.

    ``handle_message`` itself stays synchronous (unchanged), but it schedules
    its broadcast coroutines via ``app.session._schedule`` onto the RUN event
    loop; with no loop running they are dropped, so this helper spins up a
    throwaway loop for the single message and pumps it until the broadcast
    tasks (join announcements, path+state snapshots) have finished. The
    returned reply is whatever ``handle_message`` returned (``None`` for
    broadcast-driven messages, an error/state dict otherwise).
    """

    async def _run() -> dict | None:
        reply = session.handle_message(conn, msg)
        # Let the scheduled broadcast tasks run to completion (they await the
        # fake sender, which never blocks).
        await asyncio.sleep(0)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return reply

    return asyncio.run(_run())


def make_grid(rows: list[list[str]]) -> Grid:
    height = len(rows)
    width = len(rows[0])
    return Grid(name="test", width=width, height=height, cells=[list(r) for r in rows])


class SessionTestCase(unittest.TestCase):
    """A GameSession on the 16x12 sample dungeon, fake connections."""

    def setUp(self) -> None:
        self.session = GameSession("test", build_sample_map())
        self.gm_s = FakeConn()
        self.p1_s = FakeConn()
        self.p2_s = FakeConn()
        self.gm, err = self.session.join(self.gm_s, "Gamer", "gm")
        self.p1, err1 = self.session.join(self.p1_s, "Alice", "player")
        self.p2, err2 = self.session.join(self.p2_s, "Bob", "player")
        self.assertIsNone(err)
        self.assertIsNone(err1)
        self.assertIsNone(err2)
        # The fakes are the live connections: register their async senders
        # (the real server does this at /ws connect time via attach_async),
        # so broadcasts are observable on conn.out.
        attach(self.session, self.gm_s)
        attach(self.session, self.p1_s)
        attach(self.session, self.p2_s)
        # The GM is a pure controller: it has NO entity on the map.
        self.gm_ent = self.session.players[self.gm.id].entity_id
        self.assertIsNone(self.gm_ent)
        self.p1_ent = self.session.players[self.p1.id].entity_id
        self.p2_ent = self.session.players[self.p2.id].entity_id


# ---------------------------------------------------------------------------
# Joins / role assignment (§8)
# ---------------------------------------------------------------------------


class TestJoins(unittest.TestCase):
    def test_first_join_role_gm_becomes_gm(self):
        # A1: an explicit-GM join on a fresh session gets NO entity — the GM
        # is a pure controller (docs/design/gm-controller.md §2.1).
        s = GameSession("t", build_sample_map())
        sock = FakeConn()
        player, err = s.join(sock, "G", "gm")
        self.assertIsNone(err)
        self.assertEqual(player.role, "gm")
        self.assertIsNone(player.entity_id)
        self.assertEqual(len(s.entities), 0)
        self.assertNotIn(
            "gm_character", [e.kind for e in s.entities.values()]
        )

    def test_first_client_without_role_becomes_gm(self):
        s = GameSession("t", build_sample_map())
        player, err = s.join(FakeConn(), "First", None)
        self.assertIsNone(err)
        self.assertEqual(player.role, "gm")
        # A2: the first-client GM also gets no entity.
        self.assertIsNone(player.entity_id)
        self.assertEqual(len(s.entities), 0)

    def test_first_player_before_gm_becomes_gm(self):
        s = GameSession("t", build_sample_map())
        player, err = s.join(FakeConn(), "Early", "player")
        self.assertIsNone(err)
        self.assertEqual(player.role, "gm")
        # A2: ...and gets no entity.
        self.assertIsNone(player.entity_id)
        self.assertEqual(len(s.entities), 0)

    def test_player_gets_owned_party_entity_on_free_floor(self):
        s = GameSession("t", build_sample_map())
        s.join(FakeConn(), "G", "gm")
        p, err = s.join(FakeConn(), "Alice", "player")
        self.assertIsNone(err)
        self.assertEqual(p.role, "player")
        ent = s.entities[p.entity_id]
        self.assertEqual(ent.kind, "player")
        self.assertEqual(ent.team, "party")
        self.assertEqual(ent.owner, p.id)
        cell = s.grid.cells[ent.y][ent.x]
        self.assertIn(cell, ("floor", "doorway"))
        # The GM holds no token, so the first player takes the first free
        # floor (row-major) at (1,1).
        self.assertEqual((ent.x, ent.y), (1, 1))

    def test_seventh_player_refused_session_full(self):
        s = GameSession("t", build_sample_map())
        s.join(FakeConn(), "G", "gm")
        for i in range(1, MAX_PLAYERS + 1):  # 6 players — all accepted
            p, err = s.join(FakeConn(), f"P{i}", "player")
            self.assertIsNone(err, f"player {i} should be accepted")
            self.assertEqual(p.role, "player")
        self.assertEqual(len(s.players), 1 + MAX_PLAYERS)
        p7, err = s.join(FakeConn(), "P7", "player")
        self.assertIsNone(p7)
        self.assertEqual(err, SESSION_FULL)
        self.assertNotIn("P7", [p.name for p in s.players.values()])
        self.assertEqual(len(s.players), 1 + MAX_PLAYERS)

    def test_second_gm_refused(self):
        s = GameSession("t", build_sample_map())
        s.join(FakeConn(), "G1", "gm")
        gm2, err = s.join(FakeConn(), "G2", "gm")
        self.assertIsNone(gm2)
        self.assertEqual(err, SESSION_FULL)
        self.assertEqual(
            [p.name for p in s.players.values() if p.role == "gm"], ["G1"]
        )

    def test_empty_name_refused(self):
        s = GameSession("t", build_sample_map())
        p, err = s.join(FakeConn(), "   ", "gm")
        self.assertIsNone(p)
        self.assertEqual(err, "name required")

    def test_reconnect_reattaches_same_player(self):
        s = GameSession("t", build_sample_map())
        sock1 = FakeConn()
        # First client of a fresh session: even asking for "player", the
        # role assigned is GM (§8 first-client rule). The client must trust
        # the assigned role — so it reconnects without claiming a role.
        p1, _ = s.join(sock1, "Alice", "player")
        self.assertEqual(p1.role, "gm")
        # The re-attached GM has no entity at all — nothing to preserve.
        self.assertIsNone(p1.entity_id)
        n_entities_before = len(s.entities)
        sock2 = FakeConn()
        p2, err = s.join(sock2, "Alice", None)
        self.assertIsNone(err)
        self.assertEqual(p2.id, p1.id)
        self.assertEqual(p2.entity_id, p1.entity_id)
        self.assertIsNone(p2.entity_id)  # still no GM entity (A4)
        # A4: token positions are unchanged across the GM re-attach — join a
        # player first so there is a token whose position must be preserved.
        p4, err4 = s.join(FakeConn(), "Bob", "player")
        self.assertIsNone(err4)
        bob_pos = (s.entities[p4.entity_id].x, s.entities[p4.entity_id].y)
        self.assertEqual(bob_pos, (1, 1))
        sock3 = FakeConn()
        p5, err5 = s.join(sock3, "Alice", None)
        self.assertIsNone(err5)
        self.assertEqual(p5.id, p1.id)  # re-attached again, idempotent
        # Entity count/roster is unchanged across the GM re-attach.
        self.assertEqual(len(s.entities), n_entities_before + 1)
        b = s.entities[p4.entity_id]
        self.assertEqual((b.x, b.y), bob_pos)  # token position kept
        # old socket is detached, new socket owns the slot.
        self.assertIsNone(s.player_for_sock(sock1))
        self.assertIsNone(s.player_for_sock(sock2))
        self.assertEqual(s.player_for_sock(sock3).id, p1.id)
        # A same-name join with a CONFLICTING role is a different person:
        # accepted as a fresh player (no re-attach onto the GM slot).
        p3, err3 = s.join(FakeConn(), "Alice", "player")
        self.assertIsNone(err3)
        self.assertNotEqual(p3.id, p1.id)
        self.assertEqual(p3.role, "player")

    def test_detached_socket_cannot_act(self):
        s = GameSession("t", build_sample_map())
        sock = FakeConn()
        p, _ = s.join(sock, "Alice", "player")
        s.detach(sock)
        self.assertIsNone(s.player_for_sock(sock))
        reply = drive(s, sock, {"type": "request_state"})
        self.assertEqual(reply, {"type": "error", "message": "join first"})
        # the Player slot (and its entity) survived the disconnect.
        self.assertIn(p.id, s.players)

    def test_gm_leave_removes_no_entity(self):
        # A5: a GM is a pure controller — leave() drops only its Player
        # record; nothing is removed from the entity roster.
        s = GameSession("t", build_sample_map())
        gm_s, p1_s = FakeConn(), FakeConn()
        gm, _ = s.join(gm_s, "G", "gm")
        self.assertEqual(len(s.entities), 0)  # GM-only session: no entities
        p, _ = s.join(p1_s, "A", "player")
        self.assertEqual(len(s.entities), 1)
        s.leave(gm.id)
        self.assertNotIn(gm.id, s.players)
        self.assertEqual(len(s.entities), 1)  # no entity removed
        self.assertIn(p.id, s.players)        # remaining player unaffected
        # ...and in a GM-only session, the count is 0 before and after.
        s2 = GameSession("t2", build_sample_map())
        gm2, _ = s2.join(FakeConn(), "G", "gm")
        self.assertEqual(len(s2.entities), 0)
        s2.leave(gm2.id)
        self.assertEqual(len(s2.entities), 0)


# ---------------------------------------------------------------------------
# Movement & permissions (§6)
# ---------------------------------------------------------------------------


class TestMovement(SessionTestCase):
    def test_player_moves_own_entity_adjacent_floor(self):
        # Alice's entity spawns at (1,1); (2,1) is a free floor cell.
        reply = drive(self.session, 
            self.p1_s, {"type": "move", "entity_id": self.p1_ent, "x": 2, "y": 1}
        )
        # Successful move: no separate reply — the path frame is broadcast
        # to everyone (sender included) ahead of the state snapshot.
        self.assertIsNone(reply)
        paths = self.p1_s.sent("path")
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["entity_id"], self.p1_ent)
        self.assertEqual(paths[0]["path"][0], {"x": 1, "y": 1})
        self.assertEqual(paths[0]["path"][-1], {"x": 2, "y": 1})
        ent = self.session.entities[self.p1_ent]
        self.assertEqual((ent.x, ent.y), (2, 1))
        # every client got its per-viewer state snapshot for the mutation.
        for sock in (self.gm_s, self.p1_s, self.p2_s):
            self.assertTrue(sock.sent("state"))

    def test_player_cannot_move_another_players_entity(self):
        reply = drive(self.session, 
            self.p1_s, {"type": "move", "entity_id": self.p2_ent, "x": 3, "y": 1}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        ent = self.session.entities[self.p2_ent]
        self.assertEqual((ent.x, ent.y), (2, 1))  # unchanged

    def test_player_cannot_move_gm_created_npc(self):
        # A19-semantics (the "move GM entity" case no longer exists: the GM
        # has no entity — players may not move non-owned tokens, e.g. the
        # GM's created npc).
        reply = drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "Grom", "kind": "npc",
             "team": "neutral", "x": 6, "y": 5},
        )
        self.assertIsNone(reply)
        npc = next(e for e in self.session.entities.values() if e.name == "Grom")
        reply = drive(self.session, 
            self.p1_s, {"type": "move", "entity_id": npc.id, "x": 2, "y": 2}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_player_override_is_not_allowed(self):
        reply = drive(self.session, 
            self.p1_s,
            {"type": "move", "entity_id": self.p1_ent, "x": 1, "y": 3,
             "override": True},
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_gm_can_move_any_entity(self):
        # GM moves ALICE's (p1's) entity — allowed even though it's not
        # owned by the GM (the GM has no own entity at all). (1,1) -> (2,1),
        # an adjacent free floor.
        reply = drive(self.session, 
            self.gm_s, {"type": "move", "entity_id": self.p1_ent, "x": 2, "y": 1}
        )
        self.assertIsNone(reply)
        self.assertTrue(self.gm_s.sent("path"))
        ent = self.session.entities[self.p1_ent]
        self.assertEqual((ent.x, ent.y), (2, 1))

    def test_gm_override_moves_through_wall(self):
        # (2,1) [Bob] -> (4,2): straight line crosses the wall col 5? No —
        # (2,1)->(4,2) is open floor... use a real wall destination: (5,1) is
        # a wall (col 5). Override must teleport straight through it.
        reply = drive(self.session, 
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
        gm_s, p1_s = FakeConn(), FakeConn()
        s.join(gm_s, "G", "gm")
        p, _ = s.join(p1_s, "Alice", "player")  # (1,1)
        attach(s, gm_s)  # the override broadcast lands on the GM's conn
        ent_id = s.players[p.id].entity_id
        # destination (6,1) is in the sealed region: no path exists.
        reply = drive(s, 
            p1_s, {"type": "move", "entity_id": ent_id, "x": 6, "y": 1}
        )
        self.assertEqual(reply, {"type": "error", "message": NO_ROUTE})
        self.assertEqual(NO_ROUTE, "no route — wall in the way")
        e = s.entities[ent_id]
        self.assertEqual((e.x, e.y), (1, 1))  # unchanged
        # GM override into the same sealed region works.
        reply = drive(s, 
            gm_s, {"type": "move", "entity_id": ent_id, "x": 6, "y": 1,
                   "override": True}
        )
        self.assertIsNone(reply)
        self.assertEqual(gm_s.sent("path")[-1]["path"], [{"x": 6, "y": 1}])
        self.assertEqual((e.x, e.y), (6, 1))

    def test_move_out_of_bounds_rejected(self):
        reply = drive(self.session, 
            self.p1_s, {"type": "move", "entity_id": self.p1_ent, "x": 99, "y": 1}
        )
        self.assertEqual(reply["type"], "error")
        self.assertIn("out of bounds", reply["message"])

    def test_move_to_same_cell_is_a_noop_confirmation(self):
        ent = self.session.entities[self.p1_ent]
        reply = drive(self.session, 
            self.p1_s,
            {"type": "move", "entity_id": self.p1_ent, "x": ent.x, "y": ent.y},
        )
        self.assertEqual(reply["type"], "path")
        self.assertEqual(len(reply["path"]), 1)

    def test_move_to_doorway_is_walkable(self):
        # Alice (1,1) → doorway (5,5): path must exist through the gap.
        reply = drive(self.session, 
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
        reply = drive(self.session, 
            self.gm_s, {"type": "place", "entity_id": self.p2_ent, "x": 14, "y": 10}
        )
        self.assertIsNone(reply)
        e = self.session.entities[self.p2_ent]
        self.assertEqual((e.x, e.y), (14, 10))

    def test_place_by_player_not_allowed(self):
        reply = drive(self.session, 
            self.p1_s, {"type": "place", "entity_id": self.p1_ent, "x": 14, "y": 10}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_create_entity_by_gm(self):
        reply = drive(self.session, 
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
        reply = drive(self.session, 
            self.p1_s,
            {"type": "create_entity", "name": "X", "kind": "enemy",
             "team": "hostile", "x": 1, "y": 1},
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_delete_gm_created_npc_allowed_player_entity_blocked(self):
        # A GM-created token (owner None) can be deleted...
        reply = drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "Grom", "kind": "npc",
             "team": "neutral", "x": 6, "y": 5},
        )
        self.assertIsNone(reply)
        npc = next(e for e in self.session.entities.values() if e.name == "Grom")
        reply = drive(self.session, 
            self.gm_s, {"type": "delete_entity", "entity_id": npc.id}
        )
        self.assertIsNone(reply)
        self.assertNotIn(npc.id, self.session.entities)
        # ...but a connected player's controlling entity cannot (no orphan).
        reply = drive(self.session, 
            self.gm_s, {"type": "delete_entity", "entity_id": self.p1_ent}
        )
        self.assertEqual(reply["type"], "error")
        self.assertIn("cannot delete", reply["message"])
        self.assertIn(self.p1_ent, self.session.entities)

    def test_create_entity_rejects_gm_character_and_player_kinds(self):
        # A1: gm_character is no longer creatable; player stays server-only.
        for kind in ("gm_character", "player"):
            before = len(self.session.entities)
            reply = drive(self.session, 
                self.gm_s,
                {"type": "create_entity", "name": "X", "kind": kind,
                 "team": "neutral", "x": 6, "y": 5},
            )
            self.assertEqual(
                reply,
                {"type": "error", "message": "kind must be one of npc/enemy"},
            )
            self.assertEqual(len(self.session.entities), before)

    def test_create_entity_allows_npc_and_enemy(self):
        # A6: creation succeeds at a walkable cell AND on a wall cell (the
        # GM may place anywhere; only the in-bounds check applies).
        for kind, team in (("npc", "neutral"), ("enemy", "hostile")):
            before = len(self.session.entities)
            for x, y in ((12, 9), (5, 1)):  # floor and wall cells
                reply = drive(self.session, 
                    self.gm_s,
                    {"type": "create_entity", "name": f"c-{kind}-{x}{y}",
                     "kind": kind, "team": team, "x": x, "y": y},
                )
                self.assertIsNone(reply)
            self.assertEqual(len(self.session.entities), before + 2)
            # out-of-bounds is still rejected
            reply = drive(self.session, 
                self.gm_s,
                {"type": "create_entity", "name": "oob", "kind": kind,
                 "team": team, "x": 99, "y": 99},
            )
            self.assertIn("out of bounds", reply["message"])
            for e in list(self.session.entities.values()):
                if e.name.startswith(f"c-{kind}-"):
                    drive(self.session, 
                        self.gm_s,
                        {"type": "delete_entity", "entity_id": e.id},
                    )

    def test_delete_by_player_not_allowed(self):
        reply = drive(self.session, 
            self.p1_s, {"type": "delete_entity", "entity_id": self.p2_ent}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_set_team_by_gm_and_rejection_for_player(self):
        reply = drive(self.session, 
            self.p1_s, {"type": "set_team", "entity_id": self.p2_ent,
                        "team": "hostile"}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        reply = drive(self.session, 
            self.gm_s, {"type": "set_team", "entity_id": self.p2_ent,
                        "team": "hostile"}
        )
        self.assertIsNone(reply)
        self.assertEqual(self.session.entities[self.p2_ent].team, "hostile")

    def test_paint_by_gm_changes_cell_not_by_player(self):
        reply = drive(self.session, 
            self.p1_s, {"type": "paint", "x": 2, "y": 2, "cell_type": "wall"}
        )
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        self.assertEqual(self.session.grid.cells[2][2], "floor")
        reply = drive(self.session, 
            self.gm_s, {"type": "paint", "x": 2, "y": 2, "cell_type": "wall"}
        )
        self.assertIsNone(reply)
        self.assertEqual(self.session.grid.cells[2][2], "wall")
        # and it's reflected in the state payload broadcast.
        st = self.session.state_for(self.gm)
        self.assertEqual(st["map"]["cells"][2][2], "wall")

    def test_set_fog_by_gm_and_rejection_for_player(self):
        reply = drive(self.session, self.p1_s, {"type": "set_fog", "on": True})
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        self.assertFalse(self.session.fog)
        reply = drive(self.session, self.gm_s, {"type": "set_fog", "on": True})
        self.assertIsNone(reply)
        self.assertTrue(self.session.fog)


# ---------------------------------------------------------------------------
# Per-viewer state (§5)
# ---------------------------------------------------------------------------


class TestStateFor(SessionTestCase):
    def test_gm_state_has_full_entities_and_labeled_awareness(self):
        st = self.session.state_for(self.gm)
        self.assertEqual(st["type"], "state")
        self.assertEqual(st["you_entity"], None)  # GM has no own token
        self.assertEqual(len(st["entities"]), 2)  # Alice + Bob (no GM entity)
        self.assertEqual(len(st["players"]), 3)
        self.assertFalse(st["fog"])
        aw = st["awareness"]
        self.assertEqual(len(aw), 2)  # GM sees ALL tokens (no own item exists)
        for item in aw:
            self.assertTrue(item["label"])
            self.assertIn("name", item)
            self.assertIn("kind", item)
            self.assertNotEqual(item["kind"], "gm_character")
        colors = {i["entity_id"]: i["color"] for i in aw}
        self.assertEqual(colors[self.p1_ent], "green")  # party
        self.assertEqual(colors[self.p2_ent], "green")  # party

    def test_player_state_has_no_entities_and_correct_awareness(self):
        st = self.session.state_for(self.p1)
        self.assertEqual(st["entities"], [])  # players get awareness only
        # own entity rides along (additive field) so the client can render it
        self.assertEqual(st["you_entity"]["id"], self.p1_ent)
        aw = st["awareness"]
        self.assertNotIn(self.p1_ent, [i["entity_id"] for i in aw])  # self excluded
        by_id = {i["entity_id"]: i for i in aw}
        # Alice sees only Bob — the GM has no token. Bob is on the same
        # open floor row (clear LOS from (1,1)) → FULL item: colored,
        # labeled, with name + kind (the player three-tier model).
        self.assertEqual(set(by_id), {self.p2_ent})
        self.assertEqual(by_id[self.p2_ent]["color"], "green")  # friend (party)
        self.assertTrue(by_id[self.p2_ent]["label"])
        self.assertEqual(by_id[self.p2_ent]["name"], "Bob")
        self.assertIn("kind", by_id[self.p2_ent])
        self.assertNotIn("approximate", by_id[self.p2_ent])

    def test_red_enemy_in_player_awareness(self):
        # Goblin spawned on clear LOS from Alice (1,1) → FULL, red, labeled.
        drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "Goblin", "kind": "enemy",
             "team": "hostile", "x": 2, "y": 2},
        )
        st = self.session.state_for(self.p1)
        goblin = [e for e in self.session.entities.values()
                  if e.name == "Goblin"][0]
        item = next(i for i in st["awareness"] if i["entity_id"] == goblin.id)
        self.assertEqual(item["color"], "red")
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "Goblin")
        # Far behind the col-5 wall, no LOS and chebyshev > 4 → INVISIBLE
        # (absent from the player's awareness), while the GM still sees it.
        drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "FarFoe", "kind": "enemy",
             "team": "hostile", "x": 12, "y": 9},
        )
        st = self.session.state_for(self.p1)
        far = next(e for e in self.session.entities.values() if e.name == "FarFoe")
        self.assertNotIn(far.id, [i["entity_id"] for i in st["awareness"]])
        st_gm = self.session.state_for(self.gm)
        self.assertIn(far.id, [i["entity_id"] for i in st_gm["awareness"]])

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
        sock = FakeConn()
        attach(self.session, sock)  # the joiner's welcome arrives on this conn
        reply = drive(self.session, 
            sock, {"type": "join", "name": "Carol", "role": "player"}
        )
        self.assertIsNone(reply)  # the welcome went out directly
        # joiner received its welcome; the others received their own state.
        self.assertEqual(sock.last("welcome")["you"]["name"], "Carol")
        self.assertTrue(self.gm_s.sent("state"))
        self.assertTrue(self.p1_s.sent("state"))
        # Carol's state: players get no entities; awareness excludes self.
        # Carol spawns at (1,3) with clear LOS down the open column: both
        # Alice and Bob are FULL items (labeled, named) — no approx items.
        st_carol = sock.last("welcome")
        self.assertEqual(st_carol["entities"], [])
        self.assertEqual(len(st_carol["awareness"]), 2)  # Alice + Bob
        self.assertNotIn(st_carol["you_entity"]["id"],
                         [i["entity_id"] for i in st_carol["awareness"]])
        self.assertTrue(all(i["label"] and "name" in i for i in st_carol["awareness"]))
        self.assertFalse(any(i.get("approximate") for i in st_carol["awareness"]))
        # and a rejected join sends NO welcome at all.
        s2 = GameSession("t2", build_sample_map())
        s2.join(FakeConn(), "G", "gm")
        for i in range(MAX_PLAYERS):
            s2.join(FakeConn(), f"P{i}", "player")
        bad = FakeConn()
        err_reply = drive(s2, bad, {"type": "join", "name": "P7", "role": "player"})
        self.assertEqual(err_reply, {"type": "error", "message": SESSION_FULL})
        self.assertEqual(bad.out, [])  # nothing was sent on the refused join


# ---------------------------------------------------------------------------
# Player three-tier visibility model (§5): FULL (LOS) / APPROXIMATE (≤4 sq,
# no LOS) / INVISIBLE (no LOS, >4 sq). The GM is never filtered.
# ---------------------------------------------------------------------------


class TestPlayerVisibilityTiers(unittest.TestCase):
    def setUp(self) -> None:
        # 7×10: open floor column x=1 and x=4, a solid wall COLUMN x=2
        # separating them, floor rows otherwise; borders walled.
        rows = [["wall"] * 7 for _ in range(10)]
        for y in range(1, 9):
            rows[y] = ["wall", "floor", "wall", "floor", "floor", "floor", "wall"]
        self.session = GameSession("t", make_grid(rows))
        self.gm_s = FakeConn()
        self.p1_s = FakeConn()
        gm, _ = self.session.join(self.gm_s, "G", "gm")     # no entity (GM)
        p1, _ = self.session.join(self.p1_s, "Alice", "player")  # (1,1)
        self.gm = gm
        self.p1 = p1
        # Alice at (1,1). Friendly Bob placed at (4,1): no LOS (wall column
        # x=2) but Chebyshev 3 → APPROXIMATE (block (2,0)).
        p2, _ = self.session.join(FakeConn(), "Bob", "player")
        self.bob_ent = self.session.players[p2.id].entity_id
        drive(self.session, 
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 4, "y": 1}
        )
        # Shade at (4,3): no LOS, Chebyshev 3 → APPROXIMATE (block (2,1)).
        drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "Shade", "kind": "enemy",
             "team": "hostile", "x": 4, "y": 3},
        )
        self.shade = [e for e in self.session.entities.values()
                      if e.name == "Shade"][0]

    def _p1_ids(self):
        return {i["entity_id"] for i in self.session.state_for(self.p1)["awareness"]}

    def test_model_is_always_active_fog_on_or_off(self):
        # The old pass-through-wall radar is gone: with fog OFF the wall
        # still separates the columns — Bob/Shade are approximate, not full.
        self.assertFalse(self.session.fog)
        aw = self.session.state_for(self.p1)["awareness"]
        ids = {i["entity_id"] for i in aw}
        self.assertNotIn(self.bob_ent, ids)
        self.assertNotIn(self.shade.id, ids)
        self.assertEqual(sorted(i["entity_id"] for i in aw), ["<approx-1>", "<approx-2>"])
        self.assertTrue(all(i["approximate"] for i in aw))
        self.assertTrue(all("name" not in i and "color" not in i for i in aw))
        # And toggling fog on/off changes NOTHING (the flag is retained for
        # wire compatibility but no longer gates visibility).
        drive(self.session, self.gm_s, {"type": "set_fog", "on": True})
        self.assertTrue(self.session.fog)
        on_aw = self.session.state_for(self.p1)["awareness"]
        drive(self.session, self.gm_s, {"type": "set_fog", "on": False})
        off_aw = self.session.state_for(self.p1)["awareness"]
        self.assertEqual(on_aw, off_aw)
        self.assertTrue(all(i["approximate"] for i in on_aw))

    def test_full_tier_on_line_of_sight(self):
        # Bob is moved into the same open column as Alice (1,4): clear LOS
        # → FULL item (exact position, green, labeled, name + kind).
        drive(self.session, 
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 4, "y": 4}
        )
        drive(self.session, 
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 1, "y": 4}
        )
        aw = self.session.state_for(self.p1)["awareness"]
        item = next(i for i in aw if i["entity_id"] == self.bob_ent)
        self.assertFalse(item.get("approximate"))
        self.assertEqual((item["x"], item["y"]), (1, 4))
        self.assertEqual(item["color"], "green")
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "Bob")
        self.assertIn("kind", item)

    def test_approximate_tier_within_radius(self):
        # No LOS, Chebyshev 3 ≤ 4 → quantized block positions, no identity.
        aw = self.session.state_for(self.p1)["awareness"]
        by_pos = {(i["x"], i["y"]): i for i in aw}
        self.assertEqual(len(aw), 2)
        bob = by_pos[(4 // 2, 1 // 2)]   # block (2, 0)
        shade = by_pos[(4 // 2, 3 // 2)]  # block (2, 1)
        self.assertEqual(bob["entity_id"], "<approx-1>")
        self.assertEqual(shade["entity_id"], "<approx-2>")
        for i in (bob, shade):
            self.assertTrue(i["approximate"])
            self.assertFalse(i["label"])
            for k in ("name", "kind", "color", "team"):
                self.assertNotIn(k, i)

    def test_invisible_tier_beyond_radius_is_absent(self):
        # Deep at (4,8): behind the wall column and Chebyshev max(3,7)=7 > 4
        # from Alice (1,1) → no item at all.
        drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "Deep", "kind": "npc",
             "team": "neutral", "x": 4, "y": 8},
        )
        deep = [e for e in self.session.entities.values() if e.name == "Deep"][0]
        aw = self.session.state_for(self.p1)["awareness"]
        self.assertNotIn(deep.id, [i["entity_id"] for i in aw])
        # Alice's awareness is still exactly the two approx blocks (Bob, Shade).
        self.assertEqual(
            sorted(i["entity_id"] for i in aw), ["<approx-1>", "<approx-2>"])
        # The GM still sees it, labeled, alongside everyone else.
        g_aw = self.session.state_for(self.gm)["awareness"]
        self.assertIn(deep.id, [i["entity_id"] for i in g_aw])
        self.assertEqual(len(g_aw), 4)  # Alice, Bob, Shade, Deep
        for item in g_aw:
            self.assertTrue(item["label"])

    def test_tier_demotes_as_distance_grows_no_memory(self):
        # There is no "previously seen" memory: as Bob moves out of range
        # his item simply vanishes (no stale/ghost entry is kept).
        aw = self.session.state_for(self.p1)["awareness"]
        self.assertEqual(len(aw), 2)  # two approx items first
        drive(self.session, 
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 4, "y": 4}
        )
        drive(self.session, 
            self.gm_s, {"type": "place", "entity_id": self.bob_ent, "x": 5, "y": 8}
        )
        aw = self.session.state_for(self.p1)["awareness"]
        # Bob is now Chebyshev 7 from Alice → absent; only Shade remains.
        self.assertEqual(len(aw), 1)
        self.assertEqual(aw[0]["entity_id"], "<approx-1>")

    def test_own_anchor_deleted_sees_nothing(self):
        # A player whose own entity was deleted has no anchor: sees nothing.
        p3, _ = self.session.join(FakeConn(), "Carol", "player")  # (1,2)
        carol_ent = self.session.players[p3.id].entity_id
        self.assertIsNotNone(carol_ent)
        del self.session.entities[carol_ent]
        self.assertEqual(self.session.state_for(p3)["awareness"], [])

    def test_gm_unaffected_by_tiers(self):
        # GM: every entity, full info, labeled — no LOS/distance filtering,
        # including the far and walled ones.
        drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": "Far", "kind": "enemy",
             "team": "hostile", "x": 5, "y": 8},  # chebyshev 7 from Alice
        )
        g_aw = self.session.state_for(self.gm)["awareness"]
        ids = {i["entity_id"] for i in g_aw}
        self.assertEqual(
            ids,
            {self.p1.entity_id, self.bob_ent, self.shade.id, "e4"},
        )
        self.assertNotIn("<approx-1>", ids)
        self.assertTrue(all(i["label"] for i in g_aw))


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
                reply = drive(self.session, self.p1_s, msg)
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
        reply = drive(self.session, self.p1_s, {"type": "create_entity"})
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})

    def test_concurrent_joins_are_consistent(self):
        s = GameSession("t", build_sample_map())
        results = []
        lock = threading.Lock()

        def do_join(i: int) -> None:
            p, err = s.join(FakeConn(), f"P{i}", "player")
            with lock:
                results.append((i, p.role if p else None, err))

        # First join (GM) serialised, then 8 racing player joins: at most 6
        # may win.
        s.join(FakeConn(), "G", "gm")
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
        # 2x2 target with (1,1) — the first player's spawn cell — painted a
        # wall, so the entity must be re-placed onto the free (0,0).
        target = Grid(
            name="Small", width=2, height=2,
            cells=[
                ["floor", "wall"],
                ["wall", "wall"],
            ],
        )
        maps_registry[sid] = {"grid": target, "entities": {}, "players": {}}
        s = GameSession("use", build_sample_map())  # starts on sample map
        self.assertEqual((s.grid.width, s.grid.height), (16, 12))
        gm_s, p_s = FakeConn(), FakeConn()
        gm, _ = s.join(gm_s, "G", "gm")
        p, _ = s.join(p_s, "A", "player")
        attach(s, p_s)  # the use_map broadcast lands on the player's conn
        p_ent = p.entity_id
        self.assertIsNone(gm.entity_id)  # GM has no entity to re-park
        # Player A spawns at (1,1) — ON A WALL of the 2x2 target grid.
        self.assertEqual((s.entities[p_ent].x, s.entities[p_ent].y), (1, 1))

        # GM requests the CURRENT session play the uploaded map (same session).
        reply = drive(s, gm_s, {"type": "use_map", "map_id": sid})
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
        late_p, err = s.join(FakeConn(), "Z", "player")
        self.assertIsNone(err)
        welcome = s.welcome_for(late_p)
        self.assertEqual((welcome["map"]["width"], welcome["map"]["height"]),
                         (target.width, target.height))

    def test_use_map_is_gm_only_and_validates(self):
        from app.main import maps_registry
        maps_registry["usemap-2"] = {"grid": self._small_grid(), "entities": {}, "players": {}}
        s = GameSession("u2", build_sample_map())
        gm_s, p_s = FakeConn(), FakeConn()
        s.join(gm_s, "G", "gm")
        s.join(p_s, "A", "player")
        # A player cannot switch the map (GM-only).
        reply = drive(s, p_s, {"type": "use_map", "map_id": "usemap-2"})
        self.assertEqual(reply, {"type": "error", "message": "not allowed"})
        # Unknown map -> error, grid unchanged.
        before = s.grid
        reply = drive(s, gm_s, {"type": "use_map", "map_id": "nope"})
        self.assertEqual(reply["type"], "error")
        self.assertIs(s.grid, before)
        # Missing map_id -> error.
        reply = drive(s, gm_s, {"type": "use_map"})
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


# ---------------------------------------------------------------------------
# BUG-QA-001: a GAP in the e-ids (from a GM delete) must not wedge the
# id allocator. The buggy probe recomputed the SAME value
# (``eid = f"e{len(self.entities)+1}"``) on every loop pass and spun forever
# the moment the probe landed on an occupied id. The fix increments a counter
# so the probe always advances. The functional test below would hang on a
# regression, so it runs the wedging create on a daemon watchdog thread with a
# bounded join; a second cheap static guard checks the source increments.
# ---------------------------------------------------------------------------


class TestIdAllocationGap(unittest.TestCase):
    def setUp(self) -> None:
        self.session = GameSession("t", build_sample_map())
        self.gm_s = FakeConn()
        self.p1_s = FakeConn()
        self.gm, err = self.session.join(self.gm_s, "Gamer", "gm")
        self.p1, err1 = self.session.join(self.p1_s, "Alice", "player")
        self.assertIsNone(err)
        self.assertIsNone(err1)

    def _create(self, name: str, kind: str, team: str, x: int, y: int):
        return drive(self.session, 
            self.gm_s,
            {"type": "create_entity", "name": name, "kind": kind,
             "team": team, "x": x, "y": y},
        )

    def test_create_delete_create_does_not_wedge_id_allocation(self):
        # Cheap static guard: the allocator in BOTH id-allocation sites
        # increments a counter (the old bug recomputed the same value).
        import inspect

        self.assertIn("n += 1", inspect.getsource(self.session.join))
        self.assertIn("n += 1", inspect.getsource(self.session._on_create_entity))

        # e1 = the player's starting token (GM is a pure controller: no token).
        p1_ent = self.session.players[self.p1.id].entity_id
        self.assertEqual(p1_ent, "e1")

        # GM creates A (npc) -> e2, B (enemy) -> e3.
        self.assertIsNone(self._create("A", "npc", "neutral", 5, 5))
        self.assertIsNone(self._create("B", "enemy", "hostile", 6, 6))
        self.assertEqual(set(self.session.entities), {"e1", "e2", "e3"})
        a_id = next(e.id for e in self.session.entities.values()
                    if e.name == "A")
        self.assertEqual(a_id, "e2")

        # GM deletes A -> {e1, e3}: a GAP at e2, and len==2, so the next
        # probe is e3 (occupied). This is exactly the wedge condition.
        self.assertIsNone(drive(self.session, 
            self.gm_s, {"type": "delete_entity", "entity_id": a_id}
        ))
        self.assertEqual(set(self.session.entities), {"e1", "e3"})

        # GM creates C -> must return promptly (not loop forever).
        # Watchdog: run on a daemon thread with a bounded join so a regression
        # fails the suite instead of hanging it.
        result: dict = {}

        def do_create() -> None:
            result["reply"] = self._create("C", "npc", "neutral", 7, 7)

        worker = threading.Thread(target=do_create, daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(
            worker.is_alive(),
            "id allocation wedged on the e-id gap (BUG-QA-001 regression)",
        )
        self.assertIsNone(result.get("reply"))

        c = next((e for e in self.session.entities.values()
                  if e.name == "C"), None)
        self.assertIsNotNone(c, "entity C was not created")
        # Fresh, unique id: none of the pre-existing ids, present, and no
        # duplicate ids anywhere in the session.
        self.assertNotIn(c.id, {"e1", "e2", "e3"})
        self.assertIn(c.id, self.session.entities)
        ids = [e.id for e in self.session.entities.values()]
        self.assertEqual(len(ids), len(set(ids)), "duplicate entity id")
        self.assertEqual(c.id, "e4")


if __name__ == "__main__":
    unittest.main()
