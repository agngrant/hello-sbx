"""WebSocket tests (stdlib unittest + tests/wsclient.py; Iteration 5).

The RFC 6455 handshake is still validated against the published
``Sec-WebSocket-Accept`` vectors and live (the test client re-derives the
accept value from its own key).

The message-loop tests drive the REAL session protocol
(``app.session.GameSession`` over the FastAPI/uvicorn server in
``app/server.py`` — the hand-rolled ``app.ws.ws_serve`` loop is gone,
uvicorn + ``websockets`` now own the frames): the Iteration-1
echo stub is gone — a non-join message now yields an error, the first
``join`` yields the per-viewer ``welcome``, and every mutation is
broadcast as per-viewer ``state`` (+ a ``path`` frame for successful
moves).
"""

from __future__ import annotations

import json
import os
import socket
import threading
import unittest

os.environ.setdefault("LITTLEDUNGEONS_QUIET_LOGS", "1")

from app.server import ThreadingHTTPServer
from app.ws import compute_accept
from tests.wsclient import WSClient, WSClientError

# Sample-dungeon coordinates (app/grid.py): the GM has NO token on the map
# (pure controller); the FIRST PLAYER spawns on the first free floor cell
# (1,1). (5,5) is a doorway (gap in the col-5 wall), (2,3) and (6,5) are in
# the left/upper rooms sealed off from the upper-left room.
SPAWN_PLAYER = (1, 1)


# ---------------------------------------------------------------------------
# RFC 6455 handshake accept vectors (kept from Iteration 1)
# ---------------------------------------------------------------------------


class TestWsAcceptVector(unittest.TestCase):
    def test_rfc6455_example_vector(self):
        # RFC 6455 §1.3 example.
        self.assertEqual(
            compute_accept("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_second_vector(self):
        # Second independent vector, pinned here (and cross-checked live by
        # the WS tests, which verify the server's accept against a fresh key).
        self.assertEqual(
            compute_accept("x3JJHMbDL1EzLkh9GBhXDw=="),
            "HSmrc0sMlYUkAGmm5OPpG2HaGWk=",
        )


# ---------------------------------------------------------------------------
# Live-server WebSocket tests
# ---------------------------------------------------------------------------


class TestWebSocketServer(unittest.TestCase):
    """Server on an ephemeral port; each test gets fresh clients on a
    UNIQUE session id (module-level sessions persist across tests)."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        cls.httpd.daemon_threads = True
        cls.httpd.handle_error = lambda *a, **k: None  # quiet test server
        cls.host, cls.port = cls.httpd.server_address[:2]
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True, name="littedungeons-ws-test"
        )
        cls.thread.start()
        cls._n = 0

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def session_id(self) -> str:
        TestWebSocketServer._n += 1
        return f"ws-test-{TestWebSocketServer._n}"

    def client(self, session: str) -> WSClient:
        return WSClient(self.host, self.port, path=f"/ws?session={session}",
                        timeout=10)

    # -- pre-join behaviour ---------------------------------------------------

    def test_no_welcome_until_join(self):
        """Iteration 5: the server sends nothing on connect. The client
        drives the protocol; a message other than ``join`` (or
        ``request_state``) is an error, not an echo."""
        with self.client(self.session_id()) as c:
            # nothing arrives before we say anything
            self.assertIsNone(c.recv_json_or_none(timeout=0.6))
            # a non-join message now yields an error (the old echo is gone);
            # a message without a known type is reported as unknown.
            c.send_json({"hello": "littedungeons", "n": 1})
            err = c.recv_json()
            self.assertEqual(err["type"], "error")
            self.assertEqual(err["message"], "unknown message type")
            # a known type sent before joining is a "join first" error
            c.send_json({"type": "request_state"})
            err = c.recv_json()
            self.assertEqual(err, {"type": "error", "message": "join first"})
            # and unknown types are reported as such (for a joined client)
            gm = self.client(self.session_id()).connect()
            welcome = gm.join("G", "gm")
            self.assertEqual(welcome["type"], "welcome")
            gm.send_json({"type": "teleport", "x": 1, "y": 1})
            err = gm.recv_json()
            self.assertEqual(err, {"type": "error", "message": "unknown message type"})
            gm.close()

    def test_request_state_before_join(self):
        with self.client(self.session_id()) as c:
            c.send_json({"type": "request_state"})
            err = c.recv_json()
            self.assertEqual(err, {"type": "error", "message": "join first"})

    def test_invalid_json_gets_error(self):
        with self.client(self.session_id()) as c:
            c.send_text("this is not json")
            err = c.recv_json()
            self.assertEqual(err["type"], "error")
            self.assertIn("message", err)
            # connection still usable: a join afterwards works
            welcome = c.join("G", "gm")
            self.assertEqual(welcome["type"], "welcome")

    # -- join / welcome --------------------------------------------------------

    def test_join_gm_welcome_shape(self):
        with self.client(self.session_id()) as c:
            w = c.join("Gamer", "gm")
            self.assertEqual(w["type"], "welcome")
            you = w["you"]
            self.assertEqual(you["name"], "Gamer")
            self.assertEqual(you["role"], "gm")
            # The GM is a pure controller: NO entity, no token on the map.
            self.assertIsNone(you["entity_id"])
            self.assertIsNone(w["you_entity"])
            # GM sees everything: the full entity list is present (empty —
            # the GM holds nothing yet).
            self.assertEqual(w["entities"], [])
            # welcome carries the map + players + fog + per-viewer awareness
            m = w["map"]
            self.assertEqual(m["name"], "Sample Dungeon")
            self.assertEqual((m["width"], m["height"]), (16, 12))
            self.assertEqual(len(m["cells"]), 12)
            self.assertTrue(all(len(row) == 16 for row in m["cells"]))
            self.assertEqual(m["cells"][5][5], "doorway")
            self.assertEqual(m["cells"][4][10], "doorway")
            self.assertEqual(m["cells"][7][9], "doorway")
            self.assertEqual(len(w["players"]), 1)
            self.assertIs(w["fog"], False)
            self.assertEqual(w["awareness"], [])  # no tokens yet

    def test_first_player_without_gm_becomes_gm(self):
        with self.client(self.session_id()) as c:
            w = c.join("Early", "player")
            self.assertEqual(w["type"], "welcome")
            # §8: the first client becomes the GM (UI trusts welcome.you.role)
            self.assertEqual(w["you"]["role"], "gm")

    def test_two_clients_get_their_own_welcome(self):
        sid = self.session_id()
        with self.client(sid) as c, self.client(sid) as d:
            wg = c.join("Gamer", "gm")
            wp = d.join("Alice", "player")
            self.assertEqual(wg["you"]["name"], "Gamer")
            self.assertEqual(wp["you"]["name"], "Alice")
            self.assertEqual(wp["you"]["role"], "player")
            # the join is announced: the GM received a state broadcast
            st = c.recv_json()
            self.assertEqual(st["type"], "state")
            self.assertEqual(len(st["players"]), 2)
            self.assertEqual(len(st["entities"]), 1)  # GM sees only Alice's token
            # Alice's welcome: no entity list, own entity as you_entity
            self.assertEqual(wp["entities"], [])
            self.assertEqual(wp["you_entity"]["id"], wp["you"]["entity_id"])

    def test_seventh_join_refused(self):
        sid = self.session_id()
        gm = self.client(sid).connect()
        gm.join("Gamer", "gm")
        players = [self.client(sid).connect() for _ in range(6)]
        try:
            for p in players:
                w = p.join(f"P{players.index(p) + 1}", "player")
                self.assertEqual(w["type"], "welcome")
                self.assertEqual(w["you"]["role"], "player")
            # 7th non-GM → "session full", no welcome sent
            seventh = self.client(sid).connect()
            seventh.send_json({"type": "join", "name": "P7", "role": "player"})
            err = seventh.recv_json()
            self.assertEqual(err, {"type": "error", "message": "session full"})
            # a 2nd GM is refused too
            eighth = self.client(sid).connect()
            eighth.send_json({"type": "join", "name": "G2", "role": "gm"})
            err = eighth.recv_json()
            self.assertEqual(err, {"type": "error", "message": "session full"})
            seventh.close()
            eighth.close()
            # the GM's view: exactly 1 GM + 6 players. (The GM has 6 pending
            # join-broadcast states queued ahead of the request_state reply;)
            # wait for the state that shows all 7.
            gm.send_json({"type": "request_state"})
            frames = gm.frames_until(
                lambda m: m["type"] == "state" and len(m["players"]) == 7
            )
            st = frames[-1]
            self.assertEqual(st["type"], "state")
            self.assertEqual(len(st["players"]), 7)
            # 6 player tokens + the GM's none: the GM sees exactly the
            # players' entities.
            self.assertEqual(len(st["entities"]), 6)
            roles = [p["role"] for p in st["players"]]
            self.assertEqual(roles.count("gm"), 1)
            self.assertEqual(roles.count("player"), 6)
        finally:
            gm.close()
            for p in players:
                p.close()

    # -- movement over the wire --------------------------------------------------

    def test_gm_moves_players_entity_player_sees_it(self):
        sid = self.session_id()
        gm = self.client(sid).connect()
        pl = self.client(sid).connect()
        try:
            gm.join("Gamer", "gm")
            wp = pl.join("Alice", "player")
            alice_ent = wp["you"]["entity_id"]
            # GM moves ALICE's entity (1,1) → (2,1), an adjacent floor cell.
            gm.send_json({"type": "move", "entity_id": alice_ent, "x": 2, "y": 1})
            # The PLAYER client must receive the path + a state showing the
            # moved position (its own you_entity is at (2,1)).
            frames = pl.frames_until(lambda m: m["type"] == "path")
            path_msg = frames[-1]
            self.assertEqual(path_msg["entity_id"], alice_ent)
            self.assertEqual(path_msg["path"][0], {"x": 1, "y": 1})
            self.assertEqual(path_msg["path"][-1], {"x": 2, "y": 1})
            state_msg = pl.recv_json()
            self.assertEqual(state_msg["type"], "state")
            self.assertEqual(
                (state_msg["you_entity"]["x"], state_msg["you_entity"]["y"]),
                (2, 1),
            )
            # The GM got its path reply + the broadcast state too.
            gm_frames = gm.frames_until(lambda m: m["type"] == "path")
            self.assertEqual(gm_frames[-1]["entity_id"], alice_ent)
            st_gm = gm.recv_json()
            self.assertEqual(st_gm["type"], "state")
            item = next(i for i in st_gm["awareness"] if i["entity_id"] == alice_ent)
            self.assertEqual((item["x"], item["y"]), (2, 1))
        finally:
            gm.close()
            pl.close()

    def test_player_moves_self_and_others_are_told(self):
        sid = self.session_id()
        gm = self.client(sid).connect()
        pl = self.client(sid).connect()
        try:
            gm.join("Gamer", "gm")
            wp = pl.join("Alice", "player")
            self.assertEqual(gm.recv_json()["type"], "state")  # join broadcast
            own = wp["you"]["entity_id"]
            # Alice moves herself (1,1) → (2,1).
            pl.send_json({"type": "move", "entity_id": own, "x": 2, "y": 1})
            reply = pl.recv_json()
            self.assertEqual(reply["type"], "path")
            self.assertEqual(reply["entity_id"], own)
            self.assertEqual(reply["path"][-1], {"x": 2, "y": 1})
            # the GM saw it too (state broadcast; its awareness shows the move)
            st = gm.frames_until(lambda m: m["type"] == "state")[-1]
            item = next(i for i in st["awareness"] if i["entity_id"] == own)
            self.assertEqual((item["x"], item["y"]), (2, 1))
        finally:
            gm.close()
            pl.close()

    def test_player_cannot_move_others_over_ws(self):
        sid = self.session_id()
        gm = self.client(sid).connect()
        alice = self.client(sid).connect()
        bob = self.client(sid).connect()
        try:
            wg = gm.join("Gamer", "gm")
            wa = alice.join("Alice", "player")
            wb = bob.join("Bob", "player")
            for _ in range(2):
                self.assertEqual(gm.recv_json()["type"], "state")
            self.assertEqual(alice.recv_json()["type"], "state")  # Bob's join
            bob_ent = wb["you"]["entity_id"]
            # Alice tries to move Bob → "not allowed"
            alice.send_json({"type": "move", "entity_id": bob_ent, "x": 3, "y": 1})
            err = alice.recv_json()
            self.assertEqual(err, {"type": "error", "message": "not allowed"})
            # Bob's position is unchanged in Alice's next state.
            alice.send_json({"type": "request_state"})
            st = alice.recv_json()
            item = next(i for i in st["awareness"] if i["entity_id"] == bob_ent)
            self.assertEqual((item["x"], item["y"]), (2, 1))
            # Alice with override → also "not allowed" (GM-only).
            alice.send_json({"type": "move", "entity_id": wa["you"]["entity_id"],
                             "x": 6, "y": 5, "override": True})
            err = alice.recv_json()
            self.assertEqual(err, {"type": "error", "message": "not allowed"})
        finally:
            gm.close()
            alice.close()
            bob.close()

    def test_no_route_without_override_and_gm_override(self):
        sid = self.session_id()
        gm = self.client(sid).connect()
        pl = self.client(sid).connect()
        try:
            gm.join("Gamer", "gm")
            pl.join("Alice", "player")
            self.assertEqual(gm.recv_json()["type"], "state")  # Alice joined
            # The GM has no own token: it creates a neutral npc on the open
            # floor (2,1) (Alice is at (1,1)) and drives it instead.
            gm.send_json({"type": "create_entity", "name": "Grom",
                          "kind": "npc", "team": "neutral", "x": 2, "y": 1})
            st = gm.frames_until(lambda m: m["type"] == "state")[-1]
            npc_ent_id = next(e["id"] for e in st["entities"]
                              if e["name"] == "Grom")
            pl.recv_json()  # the create broadcast reached the player too
            # GM → wall cell (5,3) (col-5 interior wall) with NO override:
            # walking through a wall is impossible →
            # "no route — wall in the way".
            gm.send_json({"type": "move", "entity_id": npc_ent_id, "x": 5, "y": 3})
            err = gm.recv_json()
            self.assertEqual(err, {"type": "error", "message": "no route — wall in the way"})
            # position unchanged
            gm.send_json({"type": "request_state"})
            st = gm.recv_json()
            ent = next(e for e in st["entities"] if e["id"] == npc_ent_id)
            self.assertEqual((ent["x"], ent["y"]), (2, 1))
            # now with override → teleports straight through the wall, and the
            # player client sees the new position as a FULL contact (clear
            # LOS from (1,1) down the open left room): labeled, named, white.
            gm.send_json({"type": "move", "entity_id": npc_ent_id, "x": 5, "y": 3,
                          "override": True})
            reply = gm.recv_json()
            self.assertEqual(reply["type"], "path")
            self.assertEqual(reply["path"], [{"x": 5, "y": 3}])
            frames = pl.frames_until(lambda m: m["type"] == "state")
            st2 = frames[-1]
            item = next(i for i in st2["awareness"] if i["entity_id"] == npc_ent_id)
            self.assertEqual((item["x"], item["y"]), (5, 3))
            self.assertEqual(item["color"], "white")  # neutral npc
            self.assertTrue(item["label"])
            self.assertEqual(item["name"], "Grom")
            self.assertIn("kind", item)
            gm.send_json({"type": "request_state"})
            st3 = gm.recv_json()
            ent = next(e for e in st3["entities"] if e["id"] == npc_ent_id)
            self.assertEqual((ent["x"], ent["y"]), (5, 3))
        finally:
            gm.close()
            pl.close()

    def test_awareness_differs_per_client(self):
        """GM: full labeled list of every token (the GM has none of its own)
        — with GM + 2 players + 1 enemy the GM sees all 3.  Player: the
        three-tier model — Alice (1,1) sees Bob (2,1) as a FULL, labeled,
        named item (clear LOS), while Vex (12,9) is neither within 4
        squares nor in sight, so it is ABSENT (invisible tier)."""
        sid = self.session_id()
        gm = self.client(sid).connect()
        alice = self.client(sid).connect()
        bob = self.client(sid).connect()
        try:
            gm.join("Gamer", "gm")
            wa = alice.join("Alice", "player")
            wb = bob.join("Bob", "player")
            # join broadcasts: GM saw Alice + Bob; Alice saw Bob.
            self.assertEqual(gm.recv_json()["type"], "state")
            self.assertEqual(gm.recv_json()["type"], "state")
            self.assertEqual(alice.recv_json()["type"], "state")
            # The GM creates a hostile enemy at (12,9) → 3 tokens total.
            gm.send_json({"type": "create_entity", "name": "Vex",
                          "kind": "enemy", "team": "hostile", "x": 12, "y": 9})
            frames = gm.frames_until(lambda m: m["type"] == "state")
            st_gm = frames[-1]
            alice.recv_json()  # the create broadcast reached Alice too
            alice.send_json({"type": "request_state"})
            st_al = alice.recv_json()
            # GM: every token listed + labeled awareness of every token —
            # NO distance/LOS filtering, even Vex 11 squares away.
            self.assertEqual(len(st_gm["entities"]), 3)
            self.assertEqual(len(st_gm["awareness"]), 3)
            for item in st_gm["awareness"]:
                self.assertTrue(item["label"])
                self.assertIn("name", item)
                self.assertIn("kind", item)
            self.assertNotIn(
                "gm_character",
                [e["kind"] for e in st_gm["entities"]],
            )
            # Alice: no entity list; awareness is EXACTLY one item — Bob,
            # FULL (line of sight): green, labeled, named.  Vex is beyond
            # 4 squares and has no LOS → it does NOT appear at all.
            self.assertEqual(st_al["entities"], [])
            self.assertEqual(len(st_al["awareness"]), 1)
            bob_item = st_al["awareness"][0]
            self.assertEqual(bob_item["entity_id"], wb["you"]["entity_id"])
            self.assertEqual(bob_item["color"], "green")
            self.assertTrue(bob_item["label"])
            self.assertEqual(bob_item["name"], "Bob")
            self.assertEqual((bob_item["x"], bob_item["y"]), (2, 1))
            self.assertNotIn("approximate", bob_item)
            # (Alice's OWN token e1 is excluded from her awareness.)
            self.assertNotIn(wa["you"]["entity_id"],
                             [i["entity_id"] for i in st_al["awareness"]])
            self.assertNotIn(
                next(e["id"] for e in st_gm["entities"] if e["kind"] == "enemy"),
                [i["entity_id"] for i in st_al["awareness"]],
            )
            # and the player's own character rides along as you_entity
            self.assertEqual(st_al["you_entity"]["id"], wa["you"]["entity_id"])
        finally:
            gm.close()
            alice.close()
            bob.close()

    # -- transport behaviour -----------------------------------------------------

    def test_close_frame_is_handled(self):
        """Closing from the client side must not raise and must not wedge
        the server (a second connection still works afterwards)."""
        with self.client(self.session_id()) as c:
            w = c.join("Gamer", "gm")
            self.assertEqual(w["type"], "welcome")
            # close() sends a close frame; the server tears down cleanly.
        # server is still healthy:
        with self.client(self.session_id()) as c2:
            self.assertEqual(c2.join("Other", "gm")["type"], "welcome")

    def test_concurrent_connections(self):
        """ThreadingHTTPServer: multiple concurrent /ws connections each
        get their own thread + connection (GM + 3 players racing)."""
        sid = self.session_id()
        clients = [self.client(sid).connect() for _ in range(4)]
        try:
            names = ["GM", "A", "B", "C"]
            roles = ["gm", "player", "player", "player"]
            welcomes = []
            for c, name, role in zip(clients, names, roles):
                w = c.join(name, role)
                self.assertEqual(w["type"], "welcome")
                welcomes.append(w)
            # every client saw the 4 joins (3 states each; the first client
            # only saw 3 — it was first)
            self.assertEqual(
                sorted(len(w["players"]) for w in welcomes), [1, 2, 3, 4]
            )
            self.assertEqual({w["you"]["name"] for w in welcomes}, set(names))
        finally:
            for c in clients:
                c.close()


class TestWsClientErrors(unittest.TestCase):
    def test_bad_path_rejected(self):
        """A non-upgrade GET must NOT be treated as a WebSocket."""
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        httpd.daemon_threads = True
        httpd.handle_error = lambda *a, **k: None
        host, port = httpd.server_address[:2]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            # /health answers plain HTTP, not a 101 — the client must reject
            # (and the leaked socket must be closed, not hung open).
            c = WSClient(host, port, path="/health", timeout=5)
            with self.assertRaises(WSClientError):
                c.connect()
            self.assertIsNone(c.sock)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# BUG-005 (re-architected for the async stack): a per-client REPLY must
# never interleave/corrupt a concurrent broadcast on the same connection.
#
# The old implementation instrumented the hand-rolled ws_serve loop and the
# per-connection send lock (``GameSession._send_lock_for``); that plumbing is
# gone. In the new architecture (FastAPI/uvicorn + websockets) serialisation
# is guaranteed BY CONSTRUCTION: every send on a connection — broadcast
# frames (scheduled by the session) and per-client replies (sent by the WS
# endpoint) — is an ``await websocket.send_text(...)`` on the SAME event
# loop, and each websockets connection has a single outbound writer, so two
# sends can never interleave bytes on the socket. This test pins the same
# OBSERVABLE behaviour at the wire level: a no-route per-client REPLY must
# arrive intact (complete, parseable JSON, correct content) while a flood of
# concurrent BROADCAST frames (paint mutations) is in flight on the very
# same connection.
# ---------------------------------------------------------------------------


class TestWsReplyVsBroadcastFrameIntegrity(unittest.TestCase):
    """BUG-005 (new architecture): race a per-client reply against concurrent
    broadcasts on the same connection; assert wire-level frame integrity.

    Runs the REAL app server (uvicorn/websockets via the ThreadingHTTPServer
    adapter), joins as GM, creates a token, then, in a tight loop:

      1. fires ``move`` into a wall with no override → the per-client REPLY
         ``{type:error, message:"no route — wall in the way"}`` (sent by the
         WS endpoint, not a broadcast), and
      2. immediately fires ``paint`` → a BROADCAST (path-free state snapshot
         to every connection, i.e. this one).

    while draining frames off the raw socket. Every frame in the window must
    parse as a COMPLETE JSON object (no torn/interleaved frames), the no-route
    reply must arrive in all 8 rounds, and at least one broadcast ``state``
    must be observed interleaved in the window (proving the broadcast was
    genuinely in flight while replies were being sent). Under the old
    unlocked-reply bug a reply could interleave with a broadcast mid-frame
    and corrupt one of them.
    """

    def test_reply_does_not_interleave_concurrent_broadcasts(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        httpd.daemon_threads = True
        httpd.handle_error = lambda *a, **k: None
        host, port = httpd.server_address[:2]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = WSClient(host, port, path="/ws?session=frame-integrity",
                         timeout=10).connect()
            welcome = c.join("Gamer", "gm")
            self.assertIsNone(welcome["you"]["entity_id"])  # GM has no token
            # The GM creates a token to drive (it has no own entity).
            c.send_json({"type": "create_entity", "name": "Grom",
                         "kind": "npc", "team": "neutral", "x": 1, "y": 1})
            state = c.frames_until(lambda m: m["type"] == "state")[-1]
            ent_id = next(e["id"] for e in state["entities"]
                          if e["name"] == "Grom")
            expected_reply = {"type": "error",
                              "message": "no route — wall in the way"}
            # (2,2)/(3,2) are open floor cells; (5,3) is a wall.
            paint_cells = [(2, 2), (3, 2)]
            seen_replies = 0
            seen_broadcast_states = 0
            parsed = 0
            window_frames = 0
            for round_no in range(8):
                # REPLY trigger: move into a wall, no override → no-route
                # error REPLY (per-client, not a broadcast).
                c.send_json({"type": "move", "entity_id": ent_id,
                             "x": 5, "y": 3})
                # BROADCAST trigger: a paint → a state snapshot broadcast
                # to this same connection while the reply is in flight.
                px, py = paint_cells[round_no % len(paint_cells)]
                c.send_json({"type": "paint", "x": px, "y": py,
                             "cell_type": "floor"})
                # Drain the window until this round's reply arrives.
                got_reply = False
                for _ in range(20):
                    frame = c.recv_json()  # raises (≠ torn) if bytes corrupt
                    parsed += 1
                    window_frames += 1
                    if frame == expected_reply:
                        got_reply = True
                        break
                    if frame.get("type") == "state":
                        seen_broadcast_states += 1
                self.assertTrue(
                    got_reply,
                    f"round {round_no}: the no-route per-client reply "
                    "never arrived in the window",
                )
                seen_replies += 1
            self.assertEqual(seen_replies, 8)
            self.assertGreater(
                parsed, 8,
                "expected concurrent broadcast frames in the window",
            )
            self.assertGreater(
                seen_broadcast_states, 0,
                "no broadcast state observed while replies were in flight — "
                "the race is not actually racing",
            )
            self.assertGreaterEqual(window_frames, parsed)
            c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
