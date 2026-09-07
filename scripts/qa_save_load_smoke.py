#!/usr/bin/env python3
"""Independent live-restart smoke for Save/Load Map State (feat/save-load).

Drives the REAL server as a foreground subprocess (``app.main`` -> uvicorn)
on EPHEMERAL ports, across genuine process kills + restarts, through the full
  save -> restart -> load -> rejoin-by-name
lifecycle over the real WS wire + REST. Mirrors scripts/e2e_proof.py but is
written by QA to independently exercise the acceptance criteria (AC1-AC18),
especially the headline restart-persistence scenario (AC6/E8) and
rejoin-by-name (AC4/AC5/AC8/AC9). Exit 0 iff every check passes.

Design notes:
* Each WS connection is read by a dedicated reader THREAD that always reads
  COMPLETE frames (blocking recv). A timed-out recv can consume partial frame
  bytes and corrupt the client stream (the server then answers 1002
  "incorrect masking"), so we never time out a read on the socket directly.
* ``state_now()`` uses the existing ``request_state`` message (sender-only
  reply, NO broadcast) to read the live session without triggering one.

Server logs are preserved on any failure so the server-side behaviour is
inspectable. NOTE: QA verification artifact (kept alongside the other
scripts/qa_*.py probes); not imported by the test suite.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.wsclient import WSClient  # noqa: E402

SAVE_NAME = "Checkpoint 1"
PASS = "\u2713"
FAIL = "\u2717"
FAILURES: list[str] = []
LOG1 = os.path.join(ROOT, "qa_qlq_run1.log")
LOG2 = os.path.join(ROOT, "qa_qlq_run2.log")
LOG3 = os.path.join(ROOT, "qa_qlq_run3.log")
SAVES_DIR = os.path.join(ROOT, "saves")
LOGS = (LOG1, LOG2, LOG3)
MANAGED: list = []


def check(label, cond, detail=""):
    ok = bool(cond)
    print(f"  {PASS if ok else FAIL} {label}"
          + (f"   -> {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def ent_by_name(st, name):
    if not st:
        return None
    return next((e for e in st.get("entities", []) if e["name"] == name), None)


# --- REST helpers --------------------------------------------------------
def rest(port, method, path, body=None, timeout=8):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"raw": (e.read().decode() if hasattr(e, "read") else "")}


# --- managed WS client (reader-thread model) -----------------------------
class MClient:
    def __init__(self, port, tag):
        self.tag = tag
        self.c = WSClient("127.0.0.1", port, path="/ws", timeout=10)
        self.c.connect()
        self.q = queue.Queue()
        self.t = threading.Thread(target=self._reader, daemon=True)
        self.t.start()
        MANAGED.append(self)

    def _reader(self):
        while True:
            try:
                self.q.put(self.c.recv_json())
            except Exception:
                return

    def send(self, obj):
        self.c.send_json(obj)

    def drain(self, settle_ms=300, max_ms=6000):
        """Wait for the socket to go quiet; return the LATEST state frame.

        A broadcast is a finite batch ending in a per-viewer ``state``; we
        return once the queue is quiet for one settle period after seeing a
        state, or after two quiet periods if none ever arrived.
        """
        latest = None
        saw = False
        quiet = 0
        end = time.time() + max_ms / 1000.0
        while time.time() < end:
            got = False
            while True:
                try:
                    m = self.q.get_nowait()
                except queue.Empty:
                    break
                if m.get("type") == "state":
                    latest = m
                    saw = True
                got = True
            quiet = 0 if got else quiet + 1
            time.sleep(settle_ms / 1000.0)
            if (saw and quiet >= 1) or (not saw and quiet >= 2):
                return latest
        return latest

    def state_now(self, timeout=6.0):
        """Read the live session via ``request_state`` (NO broadcast)."""
        self.send({"type": "request_state"})
        return self.drain(settle_ms=200, max_ms=timeout * 1000)

    def wait_msg(self, pred, timeout=6.0):
        deadline = time.time() + timeout
        held = []
        while time.time() < deadline:
            try:
                m = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            if pred(m):
                for h in held:
                    self.q.put(h)
                return m
            held.append(m)
        for h in held:
            self.q.put(h)
        return None

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass


def ws_join(port, name, role=None):
    m = MClient(port, name)
    m.send({"type": "join", "name": name, "role": role})
    w = m.wait_msg(lambda x: x.get("type") in ("welcome", "error"))
    if not w or w.get("type") != "welcome":
        raise RuntimeError(f"expected welcome for {name}, got {w!r}")
    return m, w


def close_all():
    for mc in list(MANAGED):
        mc.close()


# --- server lifecycle ----------------------------------------------------
def alloc_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(logpath, port):
    log = open(logpath, "ab")
    return subprocess.Popen(
        [sys.executable, "-m", "app.main", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, stdout=log, stderr=log)


def wait_health(port, tries=80):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                        timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def stop_server(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
    except Exception as e:
        print(f"  (stop note: {e})")


def port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return False
    except Exception:
        return True
    finally:
        s.close()


def dump_server_logs():
    print("\n--- server logs (tail) ---")
    for p in LOGS:
        if os.path.exists(p):
            try:
                lines = open(p).read().splitlines()
                print(f"[{os.path.basename(p)}]")
                print("\n".join(lines[-30:]))
            except Exception as e:
                print(f"  (could not read {p}: {e})")
    print("-" * 40)


def body():
    for p in LOGS:
        if os.path.exists(p):
            os.remove(p)

    procs = {}
    bundle = None
    try:
        # =================================================================
        # RUN 1 — build state, save, corrupt + conflict + delete + AC17.
        # =================================================================
        print("=== RUN 1: start server (ephemeral port) ===")
        port = alloc_free_port()
        print(f"  (run 1 port = {port})")
        procs[1] = start_server(LOG1, port)
        check("server 1 up (health)", wait_health(port))

        print("\n[build] GM + doors + safe + Alice + Bob + Goblin NPC")
        gm, gw = ws_join(port, "GMaster", "gm")
        check("GM joined, role gm, no token",
              gw["you"]["role"] == "gm" and gw["you"]["entity_id"] is None)
        check("GM on sample dungeon 16x12",
              gw["map"]["width"] == 16 and gw["map"]["height"] == 12)

        gm.send({"type": "door", "x": 5, "y": 5, "action": "unlock"})
        gm.send({"type": "door", "x": 5, "y": 5, "action": "open"})
        st = gm.drain()
        check("(5,5) normal door OPEN (O)",
              st and st["map"].get("doors", {}).get("5,5") == "O",
              json.dumps(st and st["map"].get("doors")))
        gm.send({"type": "safe_door", "x": 10, "y": 4, "action": "mark"})
        gm.send({"type": "safe_door", "x": 10, "y": 4, "action": "unlock"})
        st = gm.drain()
        check("(10,4) safe door recorded (U), disjoint from doors",
              st and st["map"].get("safe", {}).get("10,4") == "U"
              and "10,4" not in st["map"].get("doors", {}),
              json.dumps(st and st["map"].get("safe")))

        alice, aw = ws_join(port, "Alice", "player")
        bob, bw = ws_join(port, "Bob", "player")
        gm.drain(); alice.drain(); bob.drain()
        check("Alice + Bob player tokens present",
              aw["you"]["entity_id"] and bw["you"]["entity_id"]
              and aw["you_entity"] and bw["you_entity"])
        al_id, bo_id = aw["you"]["entity_id"], bw["you"]["entity_id"]

        gm.send({"type": "create_entity", "name": "Goblin",
                 "kind": "enemy", "team": "hostile", "x": 6, "y": 2})
        gm.drain(); alice.drain(); bob.drain()
        st = gm.state_now()
        gob = ent_by_name(st, "Goblin")
        check("GM NPC Goblin created at (6,2)",
              gob is not None and (gob["x"], gob["y"]) == (6, 2), json.dumps(gob))

        # Reposition to known distinct saved cells (GM place onto party tokens).
        gm.send({"type": "place", "entity_id": al_id, "x": 2, "y": 1})
        gm.drain(); alice.drain(); bob.drain()
        gm.send({"type": "place", "entity_id": bo_id, "x": 3, "y": 2})
        gm.drain(); alice.drain(); bob.drain()
        st = gm.state_now()
        a = ent_by_name(st, "Alice"); b = ent_by_name(st, "Bob")
        check("Alice at (2,1) before save",
              a and (a["x"], a["y"]) == (2, 1), f"{a and (a['x'], a['y'])}")
        check("Bob at (3,2) before save",
              b and (b["x"], b["y"]) == (3, 2), f"{b and (b['x'], b['y'])}")

        print("\n[save] POST /api/saves {name:'Checkpoint 1'}")
        status, bdy = rest(port, "POST", "/api/saves", {"name": SAVE_NAME})
        check("save POST -> 200 with full record",
              status == 200 and all(
                  k in bdy for k in ("ok", "id", "name", "map_name", "width",
                                     "height", "created_at", "entity_count")),
              json.dumps((status, bdy)))
        save_id = bdy.get("id")
        check("save name == 'Checkpoint 1'", bdy.get("name") == SAVE_NAME)
        check("save entity_count == 3", bdy.get("entity_count") == 3,
              str(bdy.get("entity_count")))
        fpath = os.path.join(SAVES_DIR, f"{save_id}.json")
        check("AC1 saves/<id>.json exists on disk", os.path.exists(fpath), fpath)
        with open(fpath) as f:
            bundle = json.load(f)
        check("AC1 bundle grid cells == live cells",
              bundle["grid"]["cells"] == [list(r) for r in gw["map"]["cells"]])
        bund = {e["name"]: e for e in bundle["entities"]}
        check("AC1 bundle Alice owner_name=='Alice'",
              bund.get("Alice", {}).get("owner_name") == "Alice", json.dumps(bund))
        check("AC1 bundle Bob owner_name=='Bob'",
              bund.get("Bob", {}).get("owner_name") == "Bob")
        check("AC1 bundle Goblin owner_name null (GM-controlled)",
              bund.get("Goblin", {}).get("owner_name") is None)
        check("AC1 bundle carries doors(5,5=O) + safe(10,4=U)",
              bundle["grid"].get("doors", {}).get("5,5") == "O"
              and bundle["grid"].get("safe", {}).get("10,4") == "U",
              json.dumps(bundle["grid"].get("doors"))
              + " / " + json.dumps(bundle["grid"].get("safe")))
        status, lbody = rest(port, "GET", "/api/saves")
        check("AC1 GET /api/saves lists the save",
              status == 200 and any(s["id"] == save_id for s in lbody["saves"]),
              json.dumps([s["id"] for s in lbody["saves"]]))

        print("\n[corrupt] malformed bundle -> corrupt:true + load 404 + delete")
        bad_id = "qlq-corrupt-bad"
        with open(os.path.join(SAVES_DIR, f"{bad_id}.json"), "w") as f:
            f.write("{not json at all")
        status, lbody = rest(port, "GET", "/api/saves")
        row = next((s for s in lbody["saves"] if s["id"] == bad_id), None)
        check("E3 corrupt file listed with corrupt:true",
              row is not None and row.get("corrupt") is True, json.dumps(row))
        status, lbody = rest(port, "POST", f"/api/saves/{bad_id}/load")
        check("E3 load of corrupt -> 404 'save not found'",
              status == 404 and lbody.get("error") == f"save not found: {bad_id}",
              json.dumps((status, lbody)))
        check("E3 server healthy after corrupt load (REST ok)",
              rest(port, "GET", "/health")[0] == 200)
        status, lbody = rest(port, "DELETE", f"/api/saves/{bad_id}")
        check("E3/AC18 DELETE corrupt -> 200 + file gone",
              status == 200
              and not os.path.exists(os.path.join(SAVES_DIR, f"{bad_id}.json")),
              json.dumps((status, lbody)))

        print("\n[AC15] same-name save -> distinct id; both loadable; delete; 404 again")
        status, d2 = rest(port, "POST", "/api/saves", {"name": SAVE_NAME})
        check("AC15 same-name save -> 200 with DISTINCT id",
              status == 200 and d2.get("id") and d2.get("id") != save_id,
              json.dumps((save_id, d2.get("id"))))
        dup_id = d2.get("id")
        s1, b1 = rest(port, "POST", f"/api/saves/{save_id}/load")
        s2, b2 = rest(port, "POST", f"/api/saves/{dup_id}/load")
        check("AC15 both loadable -> distinct registry map ids",
              s1 == 200 and s2 == 200 and b1.get("id") != b2.get("id"),
              json.dumps((b1.get("id"), b2.get("id"))))
        status, lbody = rest(port, "DELETE", f"/api/saves/{dup_id}")
        check("AC18 DELETE duplicate -> 200 + file gone",
              status == 200 and not os.path.exists(os.path.join(SAVES_DIR, f"{dup_id}.json")))
        status, lbody = rest(port, "DELETE", f"/api/saves/{dup_id}")
        check("AC18 DELETE again -> 404", status == 404, json.dumps((status, lbody)))

        print("\n[AC17] load (no use_map) did not mutate the live session")
        st = gm.state_now()
        check("AC17 live session intact after loads (16x12, Goblin present)",
              st and st["map"]["width"] == 16 and ent_by_name(st, "Goblin") is not None)

        print("\n=== RUN 1 stop (kill server) ===")
        close_all()
        stop_server(procs[1]); del procs[1]
        time.sleep(0.5)
        check("port free after stop (process killed)", port_free(port), f"port={port}")

        # =================================================================
        # RUN 2 — persistence across restart + rejoin-by-name (headline).
        # =================================================================
        print("\n=== RUN 2: start server AGAIN (fresh in-memory state) ===")
        port2 = alloc_free_port()
        print(f"  (run 2 port = {port2}, distinct from run 1: {port})")
        procs[2] = start_server(LOG2, port2)
        check("server 2 up (health) after restart", wait_health(port2))

        status, lbody = rest(port2, "GET", "/api/saves")
        ids = [s["id"] for s in lbody["saves"]]
        check("AC6 save persists across restart (GET /api/saves)",
              save_id in ids, str(ids))
        check("AC6 saves/<id>.json still on disk after restart",
              os.path.exists(os.path.join(SAVES_DIR, f"{save_id}.json")))

        gm2, gw2 = ws_join(port2, "GMaster", "gm")
        check("AC6 fresh GM joined after restart (role gm)",
              gw2["you"]["role"] == "gm")

        print("\n[load] POST /api/saves/<id>/load  (no live swap yet)")
        status, bdy = rest(port2, "POST", f"/api/saves/{save_id}/load")
        check("load -> 200 with fresh registry id",
              status == 200 and bdy.get("id"), json.dumps((status, bdy)))
        new_map_id = bdy.get("id")
        check("load yields a fresh smap- id (independent copy, A10)",
              str(new_map_id).startswith("smap-"), str(new_map_id))
        status, m = rest(port2, "GET", f"/api/maps/{new_map_id}")
        check("AC2 GET /api/maps/<id> 200", status == 200, str(status))
        check("AC2 restored cells == saved cells",
              m.get("cells") == bundle["grid"]["cells"])
        check("AC2 restored normal door (5,5)=O",
              m.get("doors", {}).get("5,5") == "O", json.dumps(m.get("doors")))
        check("AC2 restored safe door (10,4)=U",
              m.get("safe", {}).get("10,4") == "U", json.dumps(m.get("safe")))

        gm2.send({"type": "use_map", "map_id": new_map_id})
        st = gm2.drain()
        check("use_map (same socket) swapped session to loaded 16x12 map",
              st and st["map"]["width"] == 16 and st["map"]["height"] == 12)
        a = ent_by_name(st, "Alice"); b = ent_by_name(st, "Bob"); g = ent_by_name(st, "Goblin")
        check("AC5/12 Alice restored GM-controlled (owner null)",
              a and a.get("owner") is None, json.dumps(a))
        check("AC5/12 Bob restored GM-controlled (owner null)",
              b and b.get("owner") is None, json.dumps(b))
        check("AC12 Goblin (GM NPC) restored", g is not None, json.dumps(g))
        check("AC12 Alice at saved pos (2,1)",
              a and (a["x"], a["y"]) == (2, 1), f"{a and (a['x'], a['y'])}")
        check("AC12 Bob at saved pos (3,2)",
              b and (b["x"], b["y"]) == (3, 2), f"{b and (b['x'], b['y'])}")
        # Per-entity "saved as <name>" badge = optional additive wire field
        # (spec A5/§8). The team intentionally did NOT add it (the frozen wire
        # stays as-is); rebind is driven by the in-memory Entity.owner_name
        # (proven by the AC4 rebind checks below). The correct contract check
        # here is the FROZEN WIRE (A5/AC16): owner_name must NOT leak onto
        # the GM wire, while the rebind target is retained server-side.
        check("AC16 owner_name NOT on the wire (frozen wire, A5)",
              a and g and "owner_name" not in a and "owner_name" not in g,
              json.dumps((a, g)))

        print("\n[AC3] round-trip: save the loaded session again (pre-rejoin)")
        status, rt = rest(port2, "POST", "/api/saves", {"name": "Roundtrip"})
        check("AC3 round-trip save -> 200 with distinct id",
              status == 200 and rt.get("id") and rt["id"] != save_id,
              json.dumps((save_id, rt.get("id"))))
        with open(os.path.join(SAVES_DIR, f"{rt['id']}.json")) as f:
            rt_bundle = json.load(f)
        check("AC3 round-trip grid identical (cells+doors+safe)",
              rt_bundle["grid"] == bundle["grid"],
              json.dumps(rt_bundle["grid"]["doors"]))
        rmap = {e["name"]: e for e in rt_bundle["entities"]}
        omap = {e["name"]: e for e in bundle["entities"]}
        check("AC3 round-trip entity positions/kind/team/color unchanged",
              all(name in rmap
                  and rmap[name]["x"] == omap[name]["x"]
                  and rmap[name]["y"] == omap[name]["y"]
                  and rmap[name]["kind"] == omap[name]["kind"]
                  and rmap[name]["team"] == omap[name]["team"]
                  for name in omap),
              json.dumps(rmap))
        # Spec §4.3 is explicit: at save time a GM-controlled entity stores
        # owner_name=null (verified by tests.test_saves.test_owner_name_is_
        # controlling_player_name). After use_map the loaded tokens are
        # GM-controlled (rebind pending), so a re-save BEFORE players rejoin
        # stores them with owner_name=null. Grid/positions still round-trip
        # exactly (AC3); only the rebind TAG is dropped for this secondary
        # save (see BUG-015 — spec-compliant P3 edge case).
        tags = {n: rmap.get(n, {}).get("owner_name") for n in omap}
        check("AC3 round-trip re-saves GM-controlled tokens as null (spec §4.3)",
              all(v is None for v in tags.values()), json.dumps(tags))
        status, _ = rest(port2, "DELETE", f"/api/saves/{rt['id']}")
        check("AC3 round-trip save cleaned up (deleted)", status == 200)

        print("\n[rebind] fresh players join by name (new ephemeral ids)")
        alice2, aw2 = ws_join(port2, "Alice", "player")
        gm2.drain()
        check("AC4 Alice rebound to SAVED entity at saved pos (2,1)",
              aw2["you"]["entity_id"] is not None
              and aw2["you_entity"]["x"] == 2 and aw2["you_entity"]["y"] == 1
              and aw2["you"]["entity_id"] == a["id"],
              json.dumps(aw2["you_entity"]))
        st = gm2.state_now()
        a = ent_by_name(st, "Alice")
        check("AC4 GM snapshot: Alice owner == her NEW player id",
              a and a.get("owner") == aw2["you"]["id"],
              json.dumps((a and a.get("owner"), aw2["you"]["id"])))
        check("AC4 Alice keeps party team + player kind (rebind keeps fields)",
              a and a.get("team") == "party" and a.get("kind") == "player", json.dumps(a))
        check("AC4 Alice YOU-ring data present (you_entity == you.entity_id)",
              aw2["you_entity"] and aw2["you_entity"]["id"] == aw2["you"]["entity_id"])

        bob2, bw2 = ws_join(port2, "Bob", "player")
        gm2.drain()
        check("AC8 Bob rebound to HIS saved entity at (3,2)",
              bw2["you_entity"] and bw2["you_entity"]["x"] == 3
              and bw2["you_entity"]["y"] == 2 and bw2["you"]["entity_id"] == b["id"],
              json.dumps(bw2["you_entity"]))

        carol, cw = ws_join(port2, "Carol", "player")
        gm2.drain()
        check("AC8 Carol (NOT in save) gets a FRESH party token (no rebind)",
              cw["you_entity"] and cw["you_entity"]["kind"] == "player"
              and cw["you_entity"]["team"] == "party"
              and cw["you"]["entity_id"] not in (a["id"], b["id"]),
              json.dumps(cw["you_entity"]))
        st = gm2.state_now()
        check("AC8 Carol join did not steal saved tokens (Alice/Bob owners intact)",
              ent_by_name(st, "Alice")["owner"] == aw2["you"]["id"]
              and ent_by_name(st, "Bob")["owner"] == bw2["you"]["id"])

        # AC9: a re-join with the SAME live name re-attaches (no 2nd Alice).
        alice_again, _ = ws_join(port2, "Alice", "player")
        st = gm2.state_now()
        named = [p for p in st["players"] if p["name"] == "Alice"]
        check("AC9 same-name live re-join RE-ATTACHES (still one Alice player)",
              len(named) == 1, json.dumps(st["players"]))

        print("\n[AC2] door behaviour post-load: open door walkable (via (5,5))")
        gm2.send({"type": "move", "entity_id": a["id"], "x": 6, "y": 5})
        st = gm2.drain()
        a = ent_by_name(st, "Alice")
        check("AC2 Alice walks THROUGH the open door (5,5) to (6,5)",
              a and (a["x"], a["y"]) == (6, 5), f"{a and (a['x'], a['y'])}")

        print("\n=== RUN 2 stop ===")
        close_all()
        stop_server(procs[2]); del procs[2]
        time.sleep(0.5)
        check("port free after run 2 stop", port_free(port2))

        # =================================================================
        # RUN 3 — orphan management: Bob never rejoins -> GM-controlled,
        # GM can still move/delete it (nothing lost). Fresh server, GM only.
        # =================================================================
        print("\n=== RUN 3: fresh server, GM only, load -> orphans ===")
        port3 = alloc_free_port()
        print(f"  (run 3 port = {port3})")
        procs[3] = start_server(LOG3, port3)
        check("server 3 up (health)", wait_health(port3))

        status, lbody = rest(port3, "GET", "/api/saves")
        check("AC6 save still listed (disk-backed, run 3)",
              save_id in [s["id"] for s in lbody["saves"]],
              json.dumps([s["id"] for s in lbody["saves"]]))

        gm3, _ = ws_join(port3, "GMaster", "gm")
        # AC14/E10 (BUG-002 guard): a player already connected when the GM
        # use_map's a loaded save must NOT be stranded — keeps their token,
        # sees the new map.
        dave, dw = ws_join(port3, "Dave", "player")
        gm3.drain(); dave.drain()
        check("run 3 Dave (connected player) has a fresh token pre-load",
              dw["you_entity"] is not None and dw["you"]["entity_id"] is not None)
        status, bdy = rest(port3, "POST", f"/api/saves/{save_id}/load")
        third_map_id = bdy.get("id")
        check("run 3 load -> fresh smap id", status == 200 and bdy.get("id"),
              json.dumps(bdy))
        gm3.send({"type": "use_map", "map_id": third_map_id})
        st = gm3.drain()
        st_dave = dave.drain()
        check("AC14/E10 connected Dave survives use_map (sees loaded 16x12 map)",
              st_dave and st_dave["map"]["width"] == 16
              and st_dave["map"]["height"] == 12,
              json.dumps(st_dave and st_dave["map"]["width"]))
        check("AC14/E10 Dave keeps his token (not stranded, BUG-002 guard)",
              st_dave and st_dave.get("you_entity") is not None
              and st_dave["you_entity"]["id"] == dw["you"]["entity_id"],
              json.dumps(st_dave and st_dave.get("you_entity")))
        a = ent_by_name(st, "Alice"); b = ent_by_name(st, "Bob"); g = ent_by_name(st, "Goblin")
        check("AC5 orphans (no matching rejoin): Alice/Bob/Goblin GM-controlled (owner null)",
              a and b and g
              and a.get("owner") is None and b.get("owner") is None
              and g.get("owner") is None,
              json.dumps((a and a.get("owner"), b and b.get("owner"),
                          g and g.get("owner"))))
        # Frozen-wire check (A5/AC16): the GM wire carries NO owner_name
        # (the "saved as" badge is intentionally not implemented); the
        # in-memory rebind target is proven by the rebind checks in run 2.
        check("AC16 orphans: owner_name NOT on the wire (frozen wire, A5)",
              a and b and g
              and "owner_name" not in a and "owner_name" not in b
              and "owner_name" not in g,
              json.dumps((a, b, g)))
        # GM can MOVE the orphan Bob (nothing stuck):
        gm3.send({"type": "move", "entity_id": b["id"], "x": 3, "y": 1})
        st = gm3.drain()
        bb = ent_by_name(st, "Bob")
        check("AC5 GM can move the orphan Bob to (3,1)",
              bb is not None and (bb["x"], bb["y"]) == (3, 1), json.dumps(bb))
        # GM can DELETE the orphan:
        gm3.send({"type": "delete_entity", "entity_id": b["id"]})
        st = gm3.drain()
        check("AC5 GM can delete the orphan Bob (no stuck state)",
              ent_by_name(st, "Bob") is None,
              json.dumps([e["name"] for e in st.get("entities", [])]))
        # GM can CREATE a new entity (rebuild path):
        gm3.send({"type": "create_entity", "name": "Rat", "kind": "enemy",
                  "team": "hostile", "x": 1, "y": 1})
        st = gm3.state_now()
        check("AC5 GM can create a new entity after deleting the orphan",
              ent_by_name(st, "Rat") is not None,
              json.dumps([e["name"] for e in st.get("entities", [])]))

        check("health still ok after run 3", wait_health(port3))

        print("\n=== RUN 3 stop ===")
        close_all()
        stop_server(procs[3]); del procs[3]
        time.sleep(0.5)
        check("port free after run 3 stop", port_free(port3))
    finally:
        close_all()
        for p in procs.values():
            stop_server(p)
    return None


def main():
    try:
        body()
    except Exception as exc:
        print(f"\n!!! SMOKE CRASHED: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        dump_server_logs()
        print("Server logs preserved for inspection.")
        sys.exit(2)

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} CHECK(S) FAILED:")
        for f_ in FAILURES:
            print(f"  {FAIL} {f_}")
        sys.exit(1)

    # Success: clean up QA artifacts (saves created during the run + logs).
    for p in LOGS:
        if os.path.exists(p):
            os.remove(p)
    if os.path.isdir(SAVES_DIR):
        for fn in os.listdir(SAVES_DIR):
            fp = os.path.join(SAVES_DIR, fn)
            if fn.startswith("checkpoint-1-") and fn.endswith(".json"):
                try:
                    os.remove(fp)
                except OSError:
                    pass
    print("RESULT: ALL LIVE SMOKE CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
