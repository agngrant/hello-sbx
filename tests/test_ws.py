"""WebSocket tests (stdlib unittest + tests/wsclient.py; Iteration 5).

The RFC 6455 handshake is still validated against the published
``Sec-WebSocket-Accept`` vectors and live (the test client re-derives the
accept value from its own key).

The message-loop tests now drive the REAL session protocol
(``app.session.GameSession`` over ``app.ws.ws_serve``): the Iteration-1
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

from app.main import LittleDungeonsHandler, ThreadingHTTPServer
from app.ws import compute_accept, ws_serve, client_send_text, client_recv_text
from tests.wsclient import WSClient, WSClientError

# Sample-dungeon coordinates (app/grid.py): the GM spawns on the first free
# floor cell (1,1). (5,5) is a doorway (gap in the col-5 wall), (2,3) and
# (6,5) are in the left/upper rooms sealed off from the upper-left room.
SPAWN_GM = (1, 1)


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
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), LittleDungeonsHandler)
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
            self.assertIsNotNone(you["entity_id"])
            # GM sees everything: the full entity list is present.
            self.assertEqual(len(w["entities"]), 1)
            self.assertEqual(w["entities"][0]["id"], you["entity_id"])
            self.assertEqual(w["entities"][0]["kind"], "gm_character")
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
            aw = w["awareness"]
            self.assertEqual(len(aw), 1)
            self.assertTrue(aw[0]["label"])  # GM: labeled, sees its own entity

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
            self.assertEqual(len(st["entities"]), 2)  # GM sees both
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
            self.assertEqual(len(st["entities"]), 7)
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
            # GM moves ALICE's entity (2,1) → (3,1), an adjacent floor cell.
            gm.send_json({"type": "move", "entity_id": alice_ent, "x": 3, "y": 1})
            # The PLAYER client must receive the path + a state showing the
            # moved position (its own you_entity is at (3,1)).
            frames = pl.frames_until(lambda m: m["type"] == "path")
            path_msg = frames[-1]
            self.assertEqual(path_msg["entity_id"], alice_ent)
            self.assertEqual(path_msg["path"][0], {"x": 2, "y": 1})
            self.assertEqual(path_msg["path"][-1], {"x": 3, "y": 1})
            state_msg = pl.recv_json()
            self.assertEqual(state_msg["type"], "state")
            self.assertEqual(
                (state_msg["you_entity"]["x"], state_msg["you_entity"]["y"]),
                (3, 1),
            )
            # The GM got its path reply + the broadcast state too.
            gm_frames = gm.frames_until(lambda m: m["type"] == "path")
            self.assertEqual(gm_frames[-1]["entity_id"], alice_ent)
            st_gm = gm.recv_json()
            self.assertEqual(st_gm["type"], "state")
            item = next(i for i in st_gm["awareness"] if i["entity_id"] == alice_ent)
            self.assertEqual((item["x"], item["y"]), (3, 1))
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
            # Alice moves herself (2,1) → (3,1).
            pl.send_json({"type": "move", "entity_id": own, "x": 3, "y": 1})
            reply = pl.recv_json()
            self.assertEqual(reply["type"], "path")
            self.assertEqual(reply["entity_id"], own)
            self.assertEqual(reply["path"][-1], {"x": 3, "y": 1})
            # the GM saw it too (state broadcast; its awareness shows the move)
            st = gm.frames_until(lambda m: m["type"] == "state")[-1]
            item = next(i for i in st["awareness"] if i["entity_id"] == own)
            self.assertEqual((item["x"], item["y"]), (3, 1))
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
            alice.send_json({"type": "move", "entity_id": bob_ent, "x": 4, "y": 1})
            err = alice.recv_json()
            self.assertEqual(err, {"type": "error", "message": "not allowed"})
            # Bob's position is unchanged in Alice's next state.
            alice.send_json({"type": "request_state"})
            st = alice.recv_json()
            item = next(i for i in st["awareness"] if i["entity_id"] == bob_ent)
            self.assertEqual((item["x"], item["y"]), (3, 1))
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
            wg = gm.join("Gamer", "gm")
            pl.join("Alice", "player")
            self.assertEqual(gm.recv_json()["type"], "state")  # Alice joined
            gm_ent_id = wg["you"]["entity_id"]
            # GM → wall cell (5,3) (col-5 interior wall) with NO override:
            # walking through a wall is impossible →
            # "no route — wall in the way".
            gm.send_json({"type": "move", "entity_id": gm_ent_id, "x": 5, "y": 3})
            err = gm.recv_json()
            self.assertEqual(err, {"type": "error", "message": "no route — wall in the way"})
            # position unchanged
            gm.send_json({"type": "request_state"})
            st = gm.recv_json()
            ent = next(e for e in st["entities"] if e["id"] == gm_ent_id)
            self.assertEqual((ent["x"], ent["y"]), SPAWN_GM)
            # now with override → teleports straight through the wall, and the
            # player client sees the new position.
            gm.send_json({"type": "move", "entity_id": gm_ent_id, "x": 5, "y": 3,
                          "override": True})
            reply = gm.recv_json()
            self.assertEqual(reply["type"], "path")
            self.assertEqual(reply["path"], [{"x": 5, "y": 3}])
            frames = pl.frames_until(lambda m: m["type"] == "state")
            st2 = frames[-1]
            item = next(i for i in st2["awareness"] if i["entity_id"] == gm_ent_id)
            self.assertEqual((item["x"], item["y"]), (5, 3))
            gm.send_json({"type": "request_state"})
            st3 = gm.recv_json()
            ent = next(e for e in st3["entities"] if e["id"] == gm_ent_id)
            self.assertEqual((ent["x"], ent["y"]), (5, 3))
        finally:
            gm.close()
            pl.close()

    def test_awareness_differs_per_client(self):
        """GM: full labeled list, all entities. Player: dots only (no
        names), self excluded — the two streams genuinely differ."""
        sid = self.session_id()
        gm = self.client(sid).connect()
        pl = self.client(sid).connect()
        try:
            wg = gm.join("Gamer", "gm")
            wp = pl.join("Alice", "player")
            self.assertEqual(gm.recv_json()["type"], "state")  # join broadcast
            gm.send_json({"type": "request_state"})
            pl.send_json({"type": "request_state"})
            st_gm = gm.recv_json()
            st_pl = pl.recv_json()
            # GM: every entity listed + labeled awareness of every entity
            # (including the GM's own character).
            self.assertEqual(len(st_gm["entities"]), 2)
            self.assertEqual(len(st_gm["awareness"]), 2)
            for item in st_gm["awareness"]:
                self.assertTrue(item["label"])
                self.assertIn("name", item)
                self.assertIn("kind", item)
            # Player: no entity list, awareness excludes self, no labels.
            self.assertEqual(st_pl["entities"], [])
            self.assertEqual(len(st_pl["awareness"]), 1)
            (item,) = st_pl["awareness"]
            self.assertEqual(item["entity_id"], wg["you"]["entity_id"])
            self.assertFalse(item["label"])
            self.assertNotIn("name", item)
            self.assertEqual(item["color"], "white")  # neutral gm_character
            # and the player's own character rides along as you_entity
            self.assertEqual(st_pl["you_entity"]["id"], wp["you"]["entity_id"])
        finally:
            gm.close()
            pl.close()

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
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), LittleDungeonsHandler)
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
# BUG-005: the per-client reply must be sent under the per-connection send
# lock (the same one _broadcast uses) so it can never interleave with a
# concurrent broadcast to the same socket.
# ---------------------------------------------------------------------------


class TestWsServeReplyUnderSendLock(unittest.TestCase):
    """BUG-005: the per-client REPLY the ws_serve loop sends back must go out
    under the per-connection send lock (the same one _broadcast uses), so it
    can never interleave with a concurrent broadcast on the same socket.

    We run the REAL app server (main.py wires ws_serve with
    lock_for=session._send_lock_for), then instrument:
      * ``GameSession._send_lock_for`` returns a *recording* lock that tracks
        whether it is currently held;
      * ``app.ws.send_json`` records, for every frame it writes, whether that
        lock was held at that instant.
    After the client receives a per-client REPLY (a rejected move), we assert
    that the reply frame was written while the lock was held. Under the old
    unlocked-reply bug that frame would be written while the lock was free.
    """

    class RecordingLock:
        def __init__(self):
            self._l = threading.Lock()
            self.held = False

        def acquire(self, *a, **k):
            got = self._l.acquire(*a, **k)
            if got:
                self.held = True
            return got

        def release(self, *a, **k):
            self._l.release(*a, **k)
            self.held = False

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *exc):
            self.release()
            return False

    def test_reply_written_under_send_lock(self):
        from app.session import GameSession
        import app.ws as ws_mod

        rec = self.RecordingLock()
        frames = []  # (obj, held_flag)
        real_send_json = ws_mod.send_json
        real_send_lock_for = GameSession._send_lock_for

        def rec_send_json(sock, obj, mask=None):
            frames.append((obj, rec.held))
            return real_send_json(sock, obj, mask=mask)

        def rec_send_lock_for(self, sock):
            return rec  # every send for this session shares the recording lock

        ws_mod.send_json = rec_send_json
        GameSession._send_lock_for = rec_send_lock_for
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), LittleDungeonsHandler)
        httpd.daemon_threads = True
        httpd.handle_error = lambda *a, **k: None
        host, port = httpd.server_address[:2]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = WSClient(host, port, path="/ws?session=locktest", timeout=10).connect()
            welcome = c.join("Gamer", "gm")
            gm_ent = welcome["you"]["entity_id"]
            # Trigger a per-client REPLY: move the GM entity into a wall with
            # no override -> {type:error, message:"no route — wall in the way"}
            # sent back via the ws_serve reply path (NOT a broadcast).
            c.send_json({"type": "move", "entity_id": gm_ent, "x": 5, "y": 3})
            reply = c.recv_json()
            self.assertEqual(reply, {"type": "error",
                                     "message": "no route — wall in the way"})
            # Find the recorded frame matching the reply and assert it was
            # written while the per-connection send lock was HELD.
            reply_frames = [held for obj, held in frames
                            if isinstance(obj, dict)
                            and obj.get("type") == "error"
                            and obj.get("message") == "no route — wall in the way"]
            self.assertTrue(reply_frames,
                            "the reply frame was not observed on the socket")
            self.assertTrue(all(reply_frames),
                            "BUG-005: the per-client reply was written OUTSIDE "
                            "the per-connection send lock")
            c.close()
        finally:
            ws_mod.send_json = real_send_json
            GameSession._send_lock_for = real_send_lock_for
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
