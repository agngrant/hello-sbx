#!/usr/bin/env python3
"""End-to-end proof (Iteration 5): GM + 2 players over the live WebSocket.

Every successful move broadcasts a ``path`` frame to ALL clients (sender
included) followed by each client's per-viewer ``state`` snapshot. Joins
announce a ``state`` to everyone already present. This script drives the
real server + the test WS client through a full scenario and prints a check
per behaviour:

  1. GM + 2 players join (1 GM + 2 of the 6 allowed players).
  2. Alice moves herself (2,1)->(5,5) through the doorway: SUCCESS.
  3. GM moves the gm_character (1,1)->(5,3) (a wall) WITHOUT override:
     REJECTED ("no route — wall in the way"), position unchanged.
  4. GM retries with override:true: TELEPORTS through the wall; Alice sees it.
  5. GM paints a wall at (4,1) + turns fog ON: Bob (2,1) keeps the
     gm_character (clear diagonal LOS) but Alice (5,5) is hidden; the GM
     (never fogged) sees all. Per-player awareness differs.
  6. NEW MAP: GM uploads an RGB map and sends `use_map` — the SAME session
     swaps to the new grid and re-broadcasts; the players stay in the session
     (not stranded) and the GM's grid object is shared with the registry so
     a subsequent paint still works (BUG-002 regression).
  7. Permissions over the wire: Bob can't set_fog, can't move Alice; a 7th
     join is rejected with "session full".

Run:  .venv/bin/python scripts/e2e_proof.py   (starts its own server)
"""

import http.client
import json
import os
import sys
import threading

os.environ.setdefault("LITTLEDUNGEONS_QUIET_LOGS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import LittleDungeonsHandler, ThreadingHTTPServer
from tests.wsclient import WSClient

PASS = "\u2713"
FAIL = "\u2717"
failures = []


def check(label, cond, detail=""):
    ok = bool(cond)
    print(f"  {PASS if ok else FAIL} {label}"
          + (f"   -> {detail}" if (detail and not ok) else ""))
    if not ok:
        failures.append(label)


def state_until(client, limit=20):
    """Consume frames until a ``state`` arrives (skips ``path``); return it."""
    frames = client.frames_until(lambda m: m["type"] == "state", limit=limit)
    return frames[-1]


def state_until_n_players(client, n, limit=50):
    """Consume until a ``state`` listing exactly ``n`` players; return it.

    (Joins queue state broadcasts ahead of a ``request_state`` reply.)"""
    for _ in range(limit):
        m = client.recv_json()
        if m["type"] == "state" and len(m["players"]) == n:
            return m
    raise AssertionError(f"no state with {n} players within {limit} frames")


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), LittleDungeonsHandler)
    httpd.daemon_threads = True
    httpd.handle_error = lambda *a, **k: None
    host, port = httpd.server_address[:2]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # 0. Server serves the UI + health -----------------------------------
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", "/health")
    health = json.loads(conn.getresponse().read())
    conn.request("GET", "/")
    index = conn.getresponse().read().decode("utf-8")
    conn.close()
    check("GET /health -> ok", health == {"status": "ok"})
    check("GET / serves the live UI (map-canvas + lobby)",
          'id="map-canvas"' in index and 'id="lobby-view"' in index)

    gm = WSClient(host, port, path="/ws?session=default", timeout=10).connect()
    alice = WSClient(host, port, path="/ws?session=default", timeout=10).connect()
    bob = WSClient(host, port, path="/ws?session=default", timeout=10).connect()
    try:
        # 1. joins ---------------------------------------------------------
        print("\n[1] joins  (GM + 2 players)")
        wg = gm.join("Gamer", "gm")
        wa = alice.join("Alice", "player")
        wb = bob.join("Bob", "player")
        check("GM got welcome (role=gm)",
              wg["type"] == "welcome" and wg["you"]["role"] == "gm")
        check("Alice got welcome (role=player)",
              wa["type"] == "welcome" and wa["you"]["role"] == "player")
        check("Bob got welcome (role=player)",
              wb["type"] == "welcome" and wb["you"]["role"] == "player")
        check("GM welcome: 1 entity so far (only the GM has joined)",
              len(wg["entities"]) == 1)
        check("Alice entities == [] (players get none)", wa["entities"] == [])
        check("Alice carries you_entity",
              bool(wa["you_entity"])
              and wa["you_entity"]["id"] == wa["you"]["entity_id"])
        gm_ent = wg["you"]["entity_id"]
        al_ent = wa["you"]["entity_id"]
        bo_ent = wb["you"]["entity_id"]
        check("spawn order (1,1)/(2,1)/(3,1)",
              next(e for e in wg["entities"] if e["id"] == gm_ent)["x"] == 1
              and wa["you_entity"]["x"] == 2 and wb["you_entity"]["x"] == 3)
        # drain join broadcasts: GM saw Alice+Bob, Alice saw Bob.
        # The GM's 2nd join-state now lists all 3 entities (GM sees everything).
        gm_s1 = state_until(gm)
        check("GM got Alice's join broadcast",
              gm_s1["type"] == "state" and len(gm_s1["entities"]) == 2)
        gm_s2 = state_until(gm)
        check("GM got Bob's join broadcast; GM sees all 3 entities",
              gm_s2["type"] == "state" and len(gm_s2["entities"]) == 3)
        check("Alice got Bob's join broadcast",
              state_until(alice)["type"] == "state")

        # 2. Alice moves herself through the doorway -----------------------
        print("\n[2] Alice moves (2,1) -> (5,5)  [via the doorway (5,5)]")
        alice.send_json({"type": "move", "entity_id": al_ent, "x": 5, "y": 5})
        reply = alice.recv_json()                       # her path (broadcast)
        check("Alice got a path reply",
              reply["type"] == "path" and reply["entity_id"] == al_ent,
              json.dumps(reply))
        check("path starts (2,1), ends (5,5)",
              reply["path"][0] == {"x": 2, "y": 1}
              and reply["path"][-1] == {"x": 5, "y": 5})
        g1, g2 = gm.recv_json(), gm.recv_json()
        check("GM saw path + state", {g1["type"], g2["type"]} == {"path", "state"})
        check("GM awareness shows Alice at (5,5), labeled",
              any(i["entity_id"] == al_ent and (i["x"], i["y"]) == (5, 5)
                  and i["label"] is True for i in g2["awareness"]))
        bob.recv_json()                                  # bob's path
        f3 = bob.recv_json()                             # bob's state
        bitem = next(i for i in f3["awareness"] if i["entity_id"] == al_ent)
        check("Bob's awareness: Alice is a GREEN dot at (5,5), unlabeled",
              bitem["color"] == "green" and bitem["label"] is False
              and (bitem["x"], bitem["y"]) == (5, 5))
        check("Bob's awareness has NO names (dots only)",
              all("name" not in i for i in f3["awareness"]))
        f4 = alice.recv_json()                           # alice's state
        check("Alice's you_entity now at (5,5)",
              f4["you_entity"]["x"] == 5 and f4["you_entity"]["y"] == 5)
        check("Alice's awareness: Bob=green, GM=white, no self",
              {i["entity_id"]: i["color"] for i in f4["awareness"]}
              == {bo_ent: "green", gm_ent: "white"})

        # 3. GM move into a wall WITHOUT override -> rejected --------------
        print("\n[3] GM moves gm_character (1,1) -> (5,3) [WALL]  no override")
        gm.send_json({"type": "move", "entity_id": gm_ent, "x": 5, "y": 3})
        err = gm.recv_json()
        check("GM got the no-route error",
              err == {"type": "error", "message": "no route — wall in the way"},
              json.dumps(err))
        gm.send_json({"type": "request_state"})
        st = gm.recv_json()
        ent = next(e for e in st["entities"] if e["id"] == gm_ent)
        check("gm_character still at (1,1)", (ent["x"], ent["y"]) == (1, 1))

        # 4. GM override -> teleports through the wall ---------------------
        print("\n[4] GM retries with override:true")
        gm.send_json({"type": "move", "entity_id": gm_ent, "x": 5, "y": 3,
                      "override": True})
        reply = gm.recv_json()                           # gm's path
        check("GM got path reply",
              reply["type"] == "path" and reply["path"] == [{"x": 5, "y": 3}],
              json.dumps(reply))
        st = gm.recv_json()                              # gm's state
        ent = next(e for e in st["entities"] if e["id"] == gm_ent)
        check("gm_character teleported to (5,3)", (ent["x"], ent["y"]) == (5, 3))
        alice.recv_json()                                # alice's path
        f = alice.recv_json()                            # alice's state
        item = next(i for i in f["awareness"] if i["entity_id"] == gm_ent)
        check("Alice's awareness: gm_character white at (5,3)",
              item["color"] == "white" and (item["x"], item["y"]) == (5, 3))
        bob.recv_json(); bob.recv_json()                 # drain bob path+state

        # 5. fog of war ------------------------------------------------------
        print("\n[5] fog: GM paints wall (4,1) + fog ON")
        gm.send_json({"type": "paint", "x": 4, "y": 1, "cell_type": "wall"})
        for c in (gm, alice, bob):
            c.recv_json()                                # paint state
        gm.send_json({"type": "set_fog", "on": True})
        for c in (gm, alice, bob):
            c.recv_json()                                # fog state
        gm.send_json({"type": "request_state"})
        alice.send_json({"type": "request_state"})
        bob.send_json({"type": "request_state"})
        st_gm, st_al, st_bo = gm.recv_json(), alice.recv_json(), bob.recv_json()
        check("fog flag broadcast", st_gm["fog"] is True)
        gm_ids = {i["entity_id"] for i in st_gm["awareness"]}
        check("GM is NEVER fogged (sees all 3, labeled)",
              gm_ids == {gm_ent, al_ent, bo_ent}
              and all(i["label"] for i in st_gm["awareness"]))
        al_ids = {i["entity_id"] for i in st_al["awareness"]}
        check("Alice (5,5) fog: Bob (3,1) clear LOS -> visible",
              bo_ent in al_ids)
        check("Alice (5,5) fog: gm_character (5,3) behind (4,1) wall -> hidden",
              gm_ent not in al_ids)
        bo_ids = {i["entity_id"] for i in st_bo["awareness"]}
        check("Bob (2,1) fog: gm_character (5,3) clear LOS -> visible",
              gm_ent in bo_ids)
        check("Bob (2,1) fog: Alice (5,5) blocked by (4,1) wall -> hidden",
              al_ent not in bo_ids)
        check("Bob's fogged awareness still dots only",
              all(i["label"] is False for i in st_bo["awareness"]))

        # 6. Open an uploaded map in the SAME session (use_map) --------------
        # BUG-002: the GM uploads a map and switches the session to it with
        # `use_map` (no session-id change). Both players must stay in the
        # session, the new grid must reach everyone, and the grid object must
        # be shared with the registry (a later paint still mutates it).
        print("\n[6] GM uploads a map + 'Open map in session' (use_map)")
        from app.imaging import encode_png
        from app.main import maps_registry
        # 4x4 RGB (color type 2) map: dark 1px border wall, light interior.
        border = []
        for y in range(4):
            row = []
            for x in range(4):
                v = 0 if (x in (0, 3) or y in (0, 3)) else 255
                row.append((v, v, v, 255))
            border.append(row)
        import base64
        map_id = "e2e-crypt"
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/api/maps/upload", body=json.dumps({
            "name": map_id, "image_b64": base64.b64encode(
                encode_png(4, 4, border)).decode("ascii")}),
            headers={"Content-Type": "application/json"})
        up = json.loads(conn.getresponse().read())
        conn.close()
        upload_id = up["id"]
        check("upload registered a new map", up.get("width") == 4 and up.get("height") == 4)

        # GM switches the session to the uploaded map (same session id).
        gm.send_json({"type": "use_map", "map_id": upload_id})
        # Everyone receives a state with the NEW 4x4 grid.
        def grid_state(c, n_players=3):
            for _ in range(40):
                m = c.recv_json()
                if m["type"] == "state" and m["map"]["width"] == 4 \
                        and m["map"]["height"] == 4:
                    return m
            raise AssertionError("no 4x4 state received")
        st_gm6 = grid_state(gm)
        st_al6 = grid_state(alice)
        st_bo6 = grid_state(bob)
        check("GM state now plays the new 4x4 map",
              st_gm6["map"]["width"] == 4 and st_gm6["map"]["height"] == 4)
        check("Alice STAYS in the session (new map, not stranded)",
              st_al6["map"]["width"] == 4 and len(st_al6["players"]) == 3)
        check("Bob STAYS in the session (new map, not stranded)",
              st_bo6["map"]["width"] == 4 and len(st_bo6["players"]) == 3)
        # The session's grid IS the registry grid (shared object identity)
        # -> a GM paint mutates the grid everyone sees.
        from app.main import get_session
        sess = get_session("default")
        check("session grid is the registry grid (shared identity)",
              sess.grid is maps_registry[upload_id]["grid"])
        gm.send_json({"type": "paint", "x": 2, "y": 2, "cell_type": "wall"})
        # Wait for the paint broadcast (sent after the grid mutation is
        # committed) so the registry grid is synchronously visible.
        grid_state(gm)
        check("paint after use_map reflects in the shared registry grid",
              maps_registry[upload_id]["grid"].cells[2][2] == "wall")
        # drain the paint state from the other two
        for c in (alice, bob):
            grid_state(c)
        # Restore the session to the sample dungeon for the permission checks
        # below (they assume the 16x12 layout). use_map back to the sample id.
        gm.send_json({"type": "use_map", "map_id": "sample-dungeon"})
        for c in (gm, alice, bob):
            for _ in range(40):
                m = c.recv_json()
                if m["type"] == "state" and m["map"]["width"] == 16:
                    break

        # 6. permissions over the wire --------------------------------------
        print("\n[6] permissions + capacity (fill to 6 players, then a 7th)")
        bob.send_json({"type": "set_fog", "on": False})
        check("Bob set_fog -> not allowed",
              bob.recv_json() == {"type": "error", "message": "not allowed"})
        bob.send_json({"type": "move", "entity_id": al_ent, "x": 4, "y": 5})
        check("Bob moving Alice -> not allowed",
              bob.recv_json() == {"type": "error", "message": "not allowed"})
        bob.send_json({"type": "move", "entity_id": bo_ent, "x": 3, "y": 2,
                       "override": True})
        check("Bob override -> not allowed (GM-only)",
              bob.recv_json() == {"type": "error", "message": "not allowed"})
        # Bring the session to capacity (1 GM + 6 players), then the 7th is
        # refused with "session full".
        for name in ("Carl", "Dee", "Ed", "Fay"):
            extra = WSClient(host, port, path="/ws?session=default",
                             timeout=10).connect()
            w = extra.join(name, "player")
            check(f"{name} accepted as player", w["type"] == "welcome")
            # drain the join-state broadcasts the others already queued.
        p7 = WSClient(host, port, path="/ws?session=default", timeout=10).connect()
        p7.send_json({"type": "join", "name": "P7", "role": "player"})
        check("7th non-GM join -> session full",
              p7.recv_json() == {"type": "error", "message": "session full"})
        # GM's view: 1 GM + 6 players, 7 entities (drain queued join-states).
        gm.send_json({"type": "request_state"})
        st = state_until_n_players(gm, 7)
        check("GM view: 1 GM + 6 players, 7 entities",
              len(st["players"]) == 7 and len(st["entities"]) == 7)
        p7.close()
    finally:
        for c in (gm, alice, bob):
            c.close()
    httpd.shutdown()
    httpd.server_close()

    print()
    if failures:
        print(f"{FAIL} {len(failures)} check(s) FAILED: {failures}")
        return 1
    print(f"{PASS} ALL E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
