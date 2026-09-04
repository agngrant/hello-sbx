#!/usr/bin/env python3
"""QA live verification — "GM Safe-Room Doors" (safe-room-doors spec).

Spec:  docs/design/safe-room-doors.md (model §3, state machine §4,
       entity restriction + hostile override guard §5, awareness/explored
       §6, wire/REST §8, ACs §15).

Wire:  the safe state rides inside the existing ``map`` payload as an
       additive ``map.safe`` object ``{"<x>,<y>": "C"|"O"}`` — emitted in
       FULL whenever the grid has >= 1 safe door, and ``map.doors`` EXCLUDES
       the safe cells (disjoint, jointly covering every doorway). A new
       client->server ``{type:"safe_door", x, y, action}`` (action in
       mark/unmark/open/close) — WHOLLY GM-only. A ``door`` message on a safe
       cell -> "not a normal door".

Entity restriction (SAFE-3): only ``party`` / ``neutral`` may step onto /
stand on a safe-room door cell. A ``hostile`` can NEVER path onto, stand on,
or be placed/created/override-moved/team-set on it — in EITHER state (open
or closed). The safety rule holds even under a GM ``override`` (D4).

Rendering (a green cross, distinct from a normal door's red/amber) is
FRONTEND (app/static/*, a separate workstream) — this script verifies the
authoritative SERVER state (the ``map.safe`` object) that the client renders.

This script is DELIBERATELY INDEPENDENT of the feature's visibility code where
the spec pins it: it re-derives the player's in-sight S-set itself from the
wire ``map.cells`` + token position + the wire ``map.safe``/``map.doors``,
using the safe-aware ``has_line_of_sight`` (the frozen Bresenham reference the
awareness/visibility code shares — a CLOSED safe door blocks exactly like a
wall, an OPEN one is sight-transparent), so the explored S/E/H tiers are
checked against the spec, not against the server's own visibility code. It
does NOT import ``app.visibility``.

The script boots its OWN live server on an ephemeral port (like
``e2e_proof.py``) and drives a GM + player over WS.

Run:  .venv/bin/python scripts/qa_safe_doors.py
Exit: 0 all checks pass; 1 a check failed.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading

os.environ.setdefault("LITTLEDUNGEONS_QUIET_LOGS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Grid
from app.pathfinding import _closed_doors, has_line_of_sight
from app.server import ThreadingHTTPServer
from tests.wsclient import WSClient

PASS = "\u2713"
FAIL = "\u2717"
TIMEOUT = 10.0
DOORS_ALL_L = {"5,5": "L", "10,4": "L", "9,7": "L"}
NO_ROUTE = "no route \u2014 wall in the way"
HOSTILE_SAFE = "cannot place a hostile on a safe room door"

RESULTS: list[bool] = []


def check(label, cond, detail=""):
    ok = bool(cond)
    RESULTS.append(ok)
    suffix = f"  -> {detail}" if (detail and not ok) else ""
    print(f"  {PASS if ok else FAIL} {label}{suffix}")
    return ok


# ---------------------------------------------------------------------------
# Independent re-derivation (SAFE-aware; NO app.visibility import)
# ---------------------------------------------------------------------------

def derive_visible(cells, w, h, pos, doors=None, safe=None):
    """Re-derive the player's S-set at ``pos`` from the spec rules + the
    SAFE-aware LOS.

    A CLOSED safe door blocks LOS exactly like a wall (it joins the closed
    set); an OPEN safe door is sight-transparent. (S1) a walkable cell is S
    iff it has line of sight from ``pos``; (S2) a wall cell — and a CLOSED
    door/safe-door cell (the D5 face rule) — is S iff one of its four
    in-bounds orthogonal walkable neighbours has LOS. The anchor is always S.
    """
    g = Grid.from_dict({"width": w, "height": h, "cells": cells,
                        "doors": dict(doors or {}),
                        "safe": dict(safe or {})})
    closed = _closed_doors(g)
    px, py = int(pos[0]), int(pos[1])
    seen = {(px, py)}
    for y in range(h):
        for x in range(w):
            c = cells[y][x]
            if c == "wall" or (x, y) in closed:
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in closed \
                            and cells[ny][nx] in ("floor", "doorway") \
                            and has_line_of_sight(g, (px, py), (nx, ny)):
                        seen.add((x, y))
                        break
            elif (x, y) != (px, py) and has_line_of_sight(
                    g, (px, py), (x, y)):
                seen.add((x, y))
    return seen


def s_set(mask, w, h):
    return {(x, y) for y in range(h) for x in range(w) if mask[y][x] == "S"}


def rest_map(host, port, map_id="sample-dungeon"):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", f"/api/maps/{map_id}")
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Frame helpers.
#
# Broadcast discipline (deterministic): a SUCCESSFUL mutation sends each
# connected client a per-viewer ``state`` (plus a leading ``path`` for a
# successful move); a REJECTION sends a per-client ``error`` ONLY to the
# sender (nothing is broadcast). ``safe()`` below therefore returns the
# sender's SINGLE reply frame — an ``error`` on rejection or the sender's
# copy of the ``state`` broadcast on success — and the OTHER clients each
# still owe one ``state`` frame to drain.
# ---------------------------------------------------------------------------

def get_state(c, limit=40):
    for _ in range(limit):
        m = c.recv_json()
        if m["type"] == "state":
            return m
    raise RuntimeError("no state frame within limit")


def wait_safe(c, val, limit=40):
    """Next ``state`` frame whose map.safe == ``val`` (None => absent)."""
    for _ in range(limit):
        m = c.recv_json()
        if m["type"] == "state" and m["map"].get("safe") == val:
            return m
    raise RuntimeError(f"no state with map.safe={val!r}")


def safe(gm, x, y, action):
    """A GM ``safe_door`` action. Returns the GM's single reply frame: a
    per-client ``error`` on rejection (nothing broadcast), or the ``state``
    broadcast on success (the GM's copy; other clients still owe one)."""
    gm.send_json({"type": "safe_door", "x": x, "y": y, "action": action})
    return gm.recv_json()


def safe_ok(frame):
    return isinstance(frame, dict) and frame.get("type") == "state"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 0. boot its own live server on an ephemeral port -----------------------
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    httpd.daemon_threads = True
    httpd.handle_error = lambda *a, **k: None
    host, port = httpd.server_address[:2]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[0] server up on {host}:{port}")

    try:
        # [1] default absent (regression): a fresh session's welcome map has
        # NO map.safe (safe doors are GM-authored) and map.doors all L.
        print("\n[1] default: no safe doors, doors all locked")
        gm = WSClient(host, port, path="/ws?session=qa-safe-main",
                      timeout=TIMEOUT).connect()
        pl = WSClient(host, port, path="/ws?session=qa-safe-main",
                      timeout=TIMEOUT).connect()
        try:
            wg = gm.join("QA-GM", "gm")
            # Precondition: clear any stale safe record + re-lock all doors on
            # the shared sample grid (a prior run may have left state). Only
            # the GM is connected, so it alone receives each broadcast.
            gm.send_json({"type": "safe_door", "x": 5, "y": 5,
                          "action": "unmark"})
            gm.recv_json()  # state (was safe) or "not a safe door"
            for (x, y) in ((5, 5), (10, 4), (9, 7)):
                gm.send_json({"type": "door", "x": x, "y": y,
                              "action": "lock"})
                gm.recv_json()  # state or "door is already locked"
            wa = pl.join("Alice", "player")
            gm.recv_json()  # GM's join broadcast (player joined)

            check("GM welcome role=gm",
                  wg.get("type") == "welcome"
                  and wg.get("you", {}).get("role") == "gm")
            check("Alice welcome role=player",
                  wa.get("type") == "welcome"
                  and wa.get("you", {}).get("role") == "player")
            m = wa["map"]
            w, h = m["width"], m["height"]
            check("sample dungeon 16x12 on the wire", (w, h) == (16, 12),
                  f"got {w}x{h}")
            check("welcome map has NO safe key (absent by default)",
                  "safe" not in m, json.dumps(m.get("safe")))
            check("welcome map.doors all L (regression)",
                  m["doors"] == DOORS_ALL_L, json.dumps(m["doors"]))
            rest0 = rest_map(host, port)
            check("REST: no safe key, doors all L (baseline)",
                  "safe" not in rest0 and rest0["doors"] == DOORS_ALL_L,
                  json.dumps(rest0.get("doors")))

            # [2] mark -> C, then open -> O ----------------------------------
            print("\n[2] mark -> closed (C), then open (O)")
            st = safe(gm, 5, 5, "mark")
            check("GM mark -> state broadcast carrying the safe door",
                  safe_ok(st), json.dumps(st))
            check("map.safe == {'5,5':'C'} (closed, no lock state)",
                  st["map"]["safe"] == {"5,5": "C"},
                  json.dumps(st["map"].get("safe")))
            check("map.doors no longer has '5,5' (disjoint partition)",
                  "5,5" not in st["map"].get("doors", {}),
                  json.dumps(st["map"].get("doors")))
            check("safe and doors are disjoint + jointly cover all doorways",
                  (set(st["map"]["doors"]) & set(st["map"]["safe"])) == set()
                  and (set(st["map"]["doors"]) | set(st["map"]["safe"]))
                  == {"5,5", "10,4", "9,7"})
            wait_safe(pl, {"5,5": "C"})  # the player's copy too
            rest1 = rest_map(host, port)
            check("REST carries the additive safe (disjoint from doors)",
                  rest1.get("safe") == {"5,5": "C"}
                  and "5,5" not in rest1.get("doors", {}),
                  json.dumps(rest1.get("safe")))
            st = safe(gm, 5, 5, "open")
            check("GM open -> map.safe == {'5,5':'O'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "O"},
                  json.dumps(st))
            wait_safe(pl, {"5,5": "O"})

            # [3] the state machine + permissions (exact error strings) ------
            print("\n[3] state machine + permissions (exact errors)")
            check("open on an open safe door -> 'safe door is already open'",
                  safe(gm, 5, 5, "open") ==
                  {"type": "error", "message": "safe door is already open"})
            st = safe(gm, 5, 5, "close")
            check("GM close -> map.safe back to {'5,5':'C'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "C"},
                  json.dumps(st))
            wait_safe(pl, {"5,5": "C"})
            check("close on a closed safe door -> 'safe door is already "
                  "closed'",
                  safe(gm, 5, 5, "close") ==
                  {"type": "error", "message": "safe door is already closed"})
            check("mark on an already-safe door -> 'already a safe door'",
                  safe(gm, 5, 5, "mark") ==
                  {"type": "error", "message": "already a safe door"})
            # non-doorway cell -> "not a doorway" (before the action check)
            check("safe_door on a floor cell -> 'not a doorway'",
                  safe(gm, 1, 1, "mark") ==
                  {"type": "error", "message": "not a doorway"})
            # bad action (incl. lock/unlock — a safe door has NO lock state)
            check("safe_door action 'lock' -> 'action must be one of "
                  "mark/unmark/open/close'",
                  safe(gm, 5, 5, "lock") == {"type": "error",
                                             "message":
                                             "action must be one of "
                                             "mark/unmark/open/close"})
            check("safe_door out-of-bounds -> 'destination out of bounds'",
                  safe(gm, 99, 1, "mark") ==
                  {"type": "error", "message": "destination out of bounds"})
            # unmark on a NON-safe doorway -> "not a safe door":
            check("unmark on a normal doorway -> 'not a safe door'",
                  safe(gm, 10, 4, "unmark") ==
                  {"type": "error", "message": "not a safe door"})
            # open it again for the movement section:
            st = safe(gm, 5, 5, "open")
            check("GM re-open (for the movement checks) -> {'5,5':'O'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "O"},
                  json.dumps(st))
            wait_safe(pl, {"5,5": "O"})

            # [4] the entity restriction: hostile blocked even when OPEN ----
            print("\n[4] entity restriction: hostile blocked even when OPEN")
            gm.send_json({"type": "create_entity", "name": "Vex",
                          "kind": "enemy", "team": "hostile",
                          "x": 6, "y": 5})
            st = get_state(gm)
            pl.recv_json()  # the create broadcast (player copy)
            vex = next(e for e in st["entities"] if e["name"] == "Vex")
            check("hostile Vex created at (6,5)",
                  (vex["x"], vex["y"]) == (6, 5))
            # the OPEN safe door is a wall to a hostile: no route.
            gm.send_json({"type": "move", "entity_id": vex["id"],
                          "x": 4, "y": 5})
            err = gm.recv_json()
            check("hostile through the OPEN safe door -> 'no route'",
                  err == {"type": "error", "message": NO_ROUTE},
                  json.dumps(err))
            # a NEUTRAL npc walks through the open safe door (legal A*):
            gm.send_json({"type": "create_entity", "name": "Npc",
                          "kind": "npc", "team": "neutral", "x": 1, "y": 5})
            st = get_state(gm)
            pl.recv_json()  # the create broadcast (player copy)
            npc = next(e for e in st["entities"] if e["name"] == "Npc")
            gm.send_json({"type": "move", "entity_id": npc["id"],
                          "x": 6, "y": 5})
            m = gm.recv_json()          # GM's path frame
            steps = m.get("path", []) if m.get("type") == "path" else []
            check("neutral npc walks THROUGH the open safe door (via (5,5))",
                  m.get("type") == "path"
                  and (5, 5) in {(p["x"], p["y"]) for p in steps},
                  json.dumps(m))
            get_state(gm)               # GM's move state
            pl.recv_json()              # player's path frame
            pl.recv_json()              # player's move state
            gm.send_json({"type": "delete_entity", "entity_id": npc["id"]})
            get_state(gm)
            pl.recv_json()              # the delete broadcast

            # [5] the hostile override guard (D4) + party E11 contrast ------
            print("\n[5] hostile override guard (D4) + party E11 contrast")
            gm.send_json({"type": "move", "entity_id": vex["id"],
                          "x": 5, "y": 5, "override": True})
            err = gm.recv_json()
            check("hostile OVERRIDE onto the safe cell -> 'cannot place a "
                  "hostile on a safe room door' (NOT teleported)",
                  err == {"type": "error", "message": HOSTILE_SAFE},
                  json.dumps(err))
            gm.send_json({"type": "place", "entity_id": vex["id"],
                          "x": 5, "y": 5})
            err = gm.recv_json()
            check("hostile PLACE onto the safe cell -> same rejection",
                  err == {"type": "error", "message": HOSTILE_SAFE},
                  json.dumps(err))
            gm.send_json({"type": "create_entity", "name": "Vex2",
                          "kind": "enemy", "team": "hostile",
                          "x": 5, "y": 5})
            err = gm.recv_json()
            check("hostile CREATE on the safe cell -> same rejection",
                  err == {"type": "error", "message": HOSTILE_SAFE},
                  json.dumps(err))
            # the hostile is still at (6,5) — nothing was teleported:
            gm.send_json({"type": "request_state"})
            st = get_state(gm)
            vex_now = next(e for e in st["entities"] if e["name"] == "Vex")
            check("no hostile on the safe cell (Vex still at (6,5), Vex2 "
                  "was never created)",
                  (vex_now["x"], vex_now["y"]) == (6, 5)
                  and not any(e["name"] == "Vex2" for e in st["entities"]))
            # E11 contrast: a PARTY override onto a CLOSED safe door is
            # ALLOWED (ignore-walls, like a closed normal door).
            st = safe(gm, 5, 5, "close")
            check("GM close (to test the E11 contrast) -> {'5,5':'C'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "C"},
                  json.dumps(st))
            pl.recv_json()              # the close broadcast (player copy)
            al_ent = wa["you"]["entity_id"]
            gm.send_json({"type": "move", "entity_id": al_ent,
                          "x": 5, "y": 5, "override": True})
            m = gm.recv_json()          # GM's path frame
            check("party OVERRIDE onto a CLOSED safe door is ALLOWED (E11)",
                  m.get("type") == "path"
                  and m.get("path") == [{"x": 5, "y": 5}],
                  json.dumps(m))
            get_state(gm)
            pl.recv_json()              # player's path frame
            pl.recv_json()              # player's move state
            # restore: move Alice off the door, open it, delete the hostile.
            gm.send_json({"type": "place", "entity_id": al_ent,
                          "x": 1, "y": 1})
            get_state(gm)
            pl.recv_json()
            st = safe(gm, 5, 5, "open")
            check("GM re-open (restore) -> {'5,5':'O'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "O"},
                  json.dumps(st))
            pl.recv_json()
            gm.send_json({"type": "delete_entity", "entity_id": vex["id"]})
            get_state(gm)
            pl.recv_json()

            # [6] a normal door message on the safe cell -> guard -----------
            print("\n[6] frozen normal-door guard on a safe cell")
            gm.send_json({"type": "door", "x": 5, "y": 5, "action": "unlock"})
            err = gm.recv_json()
            check("a normal door message on the safe cell -> 'not a normal "
                  "door'",
                  err == {"type": "error", "message": "not a normal door"},
                  json.dumps(err))
            # the safe record is untouched (mutual exclusion preserved):
            gm.send_json({"type": "request_state"})
            st = get_state(gm)
            check("safe record untouched after the rejected door message",
                  st["map"].get("safe") == {"5,5": "O"}
                  and "5,5" not in st["map"].get("doors", {}),
                  json.dumps(st["map"].get("safe")))
            # a normal door on a NON-safe door still works (regression):
            gm.send_json({"type": "door", "x": 10, "y": 4, "action": "unlock"})
            r = gm.recv_json()
            check("unlock on the normal (10,4) door succeeds (regression)",
                  r.get("type") == "state"
                  and r["map"]["doors"].get("10,4") == "U",
                  json.dumps(r))
            pl.recv_json()              # the broadcast (player copy)
            gm.send_json({"type": "door", "x": 10, "y": 4, "action": "lock"})
            gm.recv_json()
            pl.recv_json()

        finally:
            gm.close()
            pl.close()

        # [7] awareness (team-agnostic sight) + explored H/E + monotonic ----
        # A FRESH session + FRESH player (pristine explored memory) re-marks
        # the (shared-grid) safe door closed and places the player at (1,5)
        # — the only position with a direct horizontal LOS through the door
        # to (6,5). Behind a CLOSED safe door (6,5) is H and the face S;
        # opening reveals (6,5) as S; closing greys it to E (monotonic). The
        # S-set re-derives via the SAFE-AWARE independent LOS helper.
        print("\n[7] awareness tiers + explored H/E + monotonicity")
        gm7 = WSClient(host, port, path="/ws?session=qa-safe-exp",
                       timeout=TIMEOUT).connect()
        pl7 = WSClient(host, port, path="/ws?session=qa-safe-exp",
                       timeout=TIMEOUT).connect()
        try:
            gm7.join("QA-GM7", "gm")
            gm7.send_json({"type": "safe_door", "x": 5, "y": 5,
                           "action": "unmark"})
            gm7.recv_json()
            gm7.send_json({"type": "safe_door", "x": 5, "y": 5,
                           "action": "mark"})
            gm7.recv_json()  # safe now "C"
            wa7 = pl7.join("Alice7", "player")
            gm7.recv_json()  # GM join broadcast
            al7 = wa7["you"]["entity_id"]
            w7, h7 = wa7["map"]["width"], wa7["map"]["height"]
            # place the player at (1,5) for a direct LOS through the door.
            gm7.send_json({"type": "place", "entity_id": al7,
                           "x": 1, "y": 5})
            gm7.recv_json()          # GM's place state
            st7 = get_state(pl7)     # player's place state
            check("player re-anchored at (1,5) (direct LOS to (6,5))",
                  (st7["you_entity"]["x"], st7["you_entity"]["y"]) == (1, 5))
            # hostile behind the closed safe door:
            gm7.send_json({"type": "create_entity", "name": "Vex7",
                           "kind": "enemy", "team": "hostile", "x": 6, "y": 5})
            get_state(gm7)
            st7 = get_state(pl7)  # the create broadcast (player state)
            check("behind a CLOSED safe door: (6,5) is H (never explored) "
                  "and the face (5,5) is S (D5)",
                  st7["visibility"][5][6] == "H"
                  and st7["visibility"][5][5] == "S",
                  f"(6,5)={st7['visibility'][5][6]} "
                  f"(5,5)={st7['visibility'][5][5]}")
            ever_se = s_set(st7["visibility"], w7, h7)
            check("S-set == SAFE-AWARE re-derivation (closed safe door "
                  "blocks, like a wall)",
                  s_set(st7["visibility"], w7, h7) == derive_visible(
                      st7["map"]["cells"], w7, h7, (1, 5),
                      doors=st7["map"].get("doors"),
                      safe=st7["map"].get("safe")))
            # hostile behind a CLOSED safe door: cheb(1,5)->(6,5)=5 is beyond
            # the default radius 4 (and no LOS) -> INVISIBLE (absent):
            check("hostile behind a CLOSED safe door beyond the radius -> "
                  "INVISIBLE (awareness is empty)",
                  st7["awareness"] == [],
                  json.dumps(st7["awareness"]))
            gm7.send_json({"type": "set_awareness", "entity_id": al7,
                           "value": 6})
            gm7.recv_json()
            st7 = get_state(pl7)
            check("hostile behind a CLOSED safe door within radius -> "
                  "APPROXIMATE (grey '?', no identity)",
                  any(i.get("approximate") and "name" not in i
                      for i in st7["awareness"]),
                  json.dumps(st7["awareness"]))
            # open the safe door: LOS through it -> FULL (team-agnostic).
            st = safe(gm7, 5, 5, "open")
            check("GM open (for the awareness FULL check) -> {'5,5':'O'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "O"},
                  json.dumps(st))
            st7 = get_state(pl7)
            check("behind the OPEN safe door the hostile is FULL (LOS is "
                  "team-agnostic)",
                  any((not i.get("approximate"))
                      and i.get("label") is True
                      and i.get("name") == "Vex7"
                      for i in st7["awareness"]),
                  json.dumps(st7["awareness"]))
            # opening reveals (6,5) as S (seen through the open safe door):
            check("opening reveals (6,5) as S (seen through the open safe "
                  "door)", st7["visibility"][5][6] == "S",
                  st7["visibility"][5][6])
            check("S-set re-derives after opening (safe-aware helper)",
                  s_set(st7["visibility"], w7, h7) == derive_visible(
                      st7["map"]["cells"], w7, h7, (1, 5),
                      doors=st7["map"].get("doors"),
                      safe=st7["map"].get("safe")))
            ever_se |= s_set(st7["visibility"], w7, h7)
            # close again: (6,5) greys to E (memory, NOT H), monotonic.
            st = safe(gm7, 5, 5, "close")
            check("GM close (for the monotonicity check) -> {'5,5':'C'}",
                  safe_ok(st) and st["map"]["safe"] == {"5,5": "C"},
                  json.dumps(st))
            st7 = get_state(pl7)
            h_now = {(x, y) for y in range(h7) for x in range(w7)
                     if st7["visibility"][y][x] == "H"}
            check("closing greys (6,5) to E (memory, NOT H) and the S-set "
                  "is monotonic (no S/E -> H)",
                  st7["visibility"][5][6] == "E" and not (ever_se & h_now),
                  f"(6,5)={st7['visibility'][5][6]} "
                  f"regressed={sorted(ever_se & h_now)}")
            # clean hand-off: delete the hostile, unmark to a normal door,
            # lock it back to the all-locked default.
            gm7.send_json({"type": "request_state"})
            stg7 = get_state(gm7)
            vex7 = next((e for e in stg7["entities"]
                         if e["name"] == "Vex7"), None)
            if vex7:
                gm7.send_json({"type": "delete_entity",
                               "entity_id": vex7["id"]})
                get_state(gm7)
                pl7.recv_json_or_none(timeout=1)
            stg7 = safe(gm7, 5, 5, "unmark")
            check("GM unmark (closed) reverts (5,5) to a normal door 'U'",
                  safe_ok(stg7)
                  and "safe" not in stg7["map"]
                  and stg7["map"]["doors"].get("5,5") == "U",
                  json.dumps({"safe": stg7["map"].get("safe"),
                              "doors": stg7["map"]["doors"]}))
            pl7.recv_json()             # the unmark broadcast (player copy)
            rest2 = rest_map(host, port)
            check("REST: back to the no-safe shape after the unmark",
                  "safe" not in rest2
                  and rest2["doors"].get("5,5") == "U",
                  json.dumps(rest2.get("doors")))
            gm7.send_json({"type": "door", "x": 5, "y": 5, "action": "lock"})
            gm7.recv_json()
            pl7.recv_json_or_none(timeout=1)
        finally:
            gm7.close()
            pl7.close()

    finally:
        httpd.shutdown()
        httpd.server_close()

    total = len(RESULTS)
    failed = total - sum(RESULTS)
    print()
    if failed:
        print(f"{FAIL} {failed}/{total} check(s) FAILED")
        return 1
    print(f"{PASS} ALL {total} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
