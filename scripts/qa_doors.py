#!/usr/bin/env python3
"""QA live verification — "Openable / Closable Doors" (door-features spec).

Spec:  docs/design/door-features.md (state machine §4, movement §5,
       awareness/explored §6, wire §8, ACs §15).
Wire:  every welcome/state ``map`` carries an additive ``doors`` object
       ``{"<x>,<y>": "L"|"U"|"O"}`` — the FULL door set (unrecorded doorways
       default to ``"L"``), emitted whenever the grid has >= 1 doorway cell.
       A successful door action broadcasts ``state`` (carrying the new
       ``map.doors``); a rejected one replies a per-client ``error``.

This script is DELIBERATELY INDEPENDENT of the feature's server code where the
spec pins it: it re-derives the player's in-sight S-set itself from the wire
``map.cells`` + token position + door state, using the door-aware
``has_line_of_sight`` (the frozen Bresenham reference the awareness/visibility
code shares), so the explored S/E/H tiers are checked against the spec, not
against the server's own visibility code. It does NOT import
``app.visibility``. It DOES import ``app.awareness.build_awareness`` (allowed:
that is the frozen pre-feature reference the "awareness unchanged" hard
constraint is checked against) and ``app.models.Grid`` (a pure data container,
only to carry cells + door state to the re-derivation helpers).

Against a LIVE server on 127.0.0.1:8000 it drives GM + player over WS and
checks, per door behaviour:

  [0]  server health + a PRECONDITION that re-locks the shared sample grid's
       doors to the all-locked default (the shared-grid root cause means a
       previous/crashed run can leave a door open; same reset as
       TestDoorWire.setUp in tests/test_ws.py).
  [1]  REST  GET /api/maps/sample-dungeon exposes the additive ``doors`` field
       (all three sample doors "L").
  [2]  welcome carries the FULL ``map.doors`` — all three sample doors "L".
  [3]  GM unlock (5,5) -> "U", GM open -> "O" (broadcast to GM + player); the
       other two doors stay "L"; the open door is walkable.
  [4]  the exact permission matrix + state-machine errors (AC3, §4.3 order):
       player unlock/lock -> "not allowed" / "door is already unlocked";
       open on locked -> "door is locked"; player open on unlocked -> "O";
       player close on open -> "U"; GM close on open -> "U"; GM lock from
       open -> "L" (force-close); the already-open/closed/locked errors.
       (The ACCEPTED player tap mapping L->open, U->open, O->close is what
       the frontend ships; a tap on a locked door therefore sends ``open``
       and the server replies "door is locked" — a deviation from the §7.6/
       AC11(d) literal, noted in the build report.)
  [5]  occupancy guard (D3/AC9): a token on the door cell -> "close" and
       "lock" (force-close) are rejected ("cannot close a door with a token
       on it"); the door stays open; with the token moved off, close +
       re-lock work.
  [6]  movement: a closed door is not walkable -> "no route — wall in the
       way" (position unchanged); an open door is -> a legal A* path through
       the door cell.
  [7]  GM ``override:true`` moves a token onto/through a closed door (A3).
  [8]  GM re-lock of an OPEN door (force-close to "L"); ``use_map`` keeps the
       current door states (no reset — the grid object is shared); REST
       reflects the LIVE shared door state (object identity, §8.2).
  [9]  awareness (door-driven only via LOS, AC6): an enemy behind a CLOSED
       door within the player's awareness radius is APPROXIMATE (no
       identity); at spawn (beyond the radius, no LOS) INVISIBLE; behind an
       OPEN door it is FULL (named/labeled). Byte-equal to build_awareness;
       the GM is never filtered.
  [10] explored map (door-driven, AC7/AC8): a cell seen through the door,
       then the door closed -> "E" (greyed), NOT "H"; a never-seen far cell
       -> "H"; the door FACE is "S". The S-set re-derives with door-aware LOS
       at every step (monotonic — no S/E -> H).

Run:  .venv/bin/python scripts/qa_doors.py
Exit: 0 all checks pass; 1 a check failed; 2 server not reachable.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import time

HOST, PORT = "127.0.0.1", 8000
TIMEOUT = 10.0
SAMPLE = "sample-dungeon"
DOORS_ALL_L = {"5,5": "L", "10,4": "L", "9,7": "L"}

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.awareness import build_awareness        # frozen reference (allowed)
from app.models import Entity, Grid, Player
from app.pathfinding import (_closed_doors, find_path, has_line_of_sight,
                             is_valid_step)
from tests.wsclient import WSClient

PASS, FAIL = "\u2713", "\u2717"
RESULTS: list[bool] = []


def check(label, cond, detail=""):
    ok = bool(cond)
    RESULTS.append(ok)
    suffix = f"  -> {detail}" if (detail and not ok) else ""
    print(f"  {PASS if ok else FAIL} {label}{suffix}")
    return ok


# ---------------------------------------------------------------------------
# Independent re-derivation (door-aware; NO app.visibility import)
# ---------------------------------------------------------------------------

def derive_visible(cells, w, h, doors, pos):
    """Re-derive the player's S-set at ``pos`` from the spec rules + the
    door-aware LOS (a CLOSED door = a blocker; an OPEN door = transparent).

    (S-B) the anchor ``pos`` is always S;
    (S1)  a walkable cell (floor / OPEN doorway) is S iff it has (door-aware)
          line of sight from ``pos``;
    (S2)  a wall cell — and a CLOSED door cell (the D5 face rule) — is S iff
          one of its four in-bounds orthogonal walkable neighbours has LOS.
    """
    g = Grid.from_dict({"width": w, "height": h, "cells": cells,
                        "doors": dict(doors or {})})
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


def tier_sets(mask):
    s, e, h = set(), set(), set()
    for y, row in enumerate(mask):
        for x, ch in enumerate(row):
            (s if ch == "S" else e if ch == "E" else h).add((x, y))
    return s, e, h


def wellformed(mask, w, h):
    return (isinstance(mask, list) and len(mask) == h
            and all(isinstance(r, str) and len(r) == w
                    and set(r) <= set("SEH") for r in mask))


def rest_map(map_id=SAMPLE):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    try:
        conn.request("GET", f"/api/maps/{map_id}")
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Frame helpers.  Deterministic broadcast order: every successful mutation
# sends each connected client, in registry order, a ``path`` (moves only)
# then its per-viewer ``state``; a per-client ``error`` reply goes ONLY to
# the sender (nothing is broadcast).
# ---------------------------------------------------------------------------

def get_state(c, limit=40):
    """Next ``state`` frame for ``c`` (skips anything else)."""
    for _ in range(limit):
        m = c.recv_json()
        if m["type"] == "state":
            return m
    raise RuntimeError("no state frame within limit")


def wait_doors(c, key, state, limit=40):
    """Next ``state`` frame whose map.doors[key] == ``state``."""
    for _ in range(limit):
        m = c.recv_json()
        if m["type"] == "state" and m["map"].get("doors", {}).get(key) == \
                state:
            return m
    raise RuntimeError(f"no state with doors[{key}]={state}")


def door(actor, other, x, y, action, expect=None):
    """A ``door`` action. Returns (error_dict, None) on rejection, or
    (None, (actor_state, other_state)) on success (both carry the doors)."""
    key = f"{x},{y}"
    actor.send_json({"type": "door", "x": x, "y": y, "action": action})
    a = actor.recv_json()
    if a["type"] == "error":
        return a, None
    if expect is not None:
        assert a["map"]["doors"][key] == expect, \
            f"doors[{key}]={a['map']['doors'][key]} want {expect}"
    o = wait_doors(other, key, expect) if expect else get_state(other)
    return None, (a, o)


def gm_move(gm, pl, ent, x, y, override=False):
    """GM move. Returns (frame, gm_state, pl_state); on the no-route error
    the two trailing values are None (only the sender gets a reply)."""
    gm.send_json({"type": "move", "entity_id": ent, "x": x, "y": y,
                  "override": override})
    m = gm.recv_json()
    if m["type"] == "error":
        return m, None, None
    gstate = get_state(gm)
    pl.recv_json()                # the player's copy of the path
    pstate = get_state(pl)
    return m, gstate, pstate


def gm_place(gm, pl, ent, x, y):
    gm.send_json({"type": "place", "entity_id": ent, "x": x, "y": y})
    return get_state(gm), get_state(pl)


def gm_create(gm, pl, name, kind, team, x, y):
    gm.send_json({"type": "create_entity", "name": name, "kind": kind,
                  "team": team, "x": x, "y": y})
    return get_state(gm), get_state(pl)


def gm_use_map(gm, pl, map_id):
    gm.send_json({"type": "use_map", "map_id": map_id})
    return get_state(gm), get_state(pl)


def ent_at(state, ent_id):
    for e in state.get("entities", []):
        if e["id"] == ent_id:
            return (e["x"], e["y"])
    return None


def player_entities(gstate):
    return {e["id"]: Entity.from_dict(e) for e in gstate["entities"]}


def _full_present(state, entity_id):
    return any(i.get("entity_id") == entity_id and not i.get("approximate")
               and i.get("label") is True and "name" in i and "color" in i
               and "kind" in i for i in state.get("awareness", []))


def _approx_present(state, pos):
    qx, qy = pos[0] // 2, pos[1] // 2
    return any(i.get("approximate") is True and (i["x"], i["y"]) == (qx, qy)
               and "name" not in i and "color" not in i and "kind" not in i
               for i in state.get("awareness", []))


def _all_steps_legal(ggrid, steps):
    return all(is_valid_step(ggrid, (steps[i]["x"], steps[i]["y"]),
                             (steps[i + 1]["x"], steps[i + 1]["y"]))
               for i in range(len(steps) - 1))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 0. health gate (exit 2 with a hint if the server is not up) ----------
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=3)
        conn.request("GET", "/health")
        health = json.loads(conn.getresponse().read())
        conn.close()
    except OSError as exc:
        print(f"{FAIL} server not reachable at {HOST}:{PORT}: {exc}")
        print("  hint: start it with")
        print("    cd /Users/agrant3/agentteam && PYTHONUNBUFFERED=1 nohup "
              "./.venv/bin/python -m app.main --host 0.0.0.0 --port 8000 "
              ">> .ld_server.log 2>&1 & echo $! > .ld_server.pid")
        return 2
    print(f"[0] server up (health={health})")
    check("GET /health -> {'status':'ok'}",
          isinstance(health, dict) and health.get("status") == "ok",
          json.dumps(health))

    # 0b. PRECONDITION: normalize the shared sample grid's door state to the
    # all-locked default. ``app.main.get_session`` hands unregistered session
    # ids the SHARED ``maps_registry["sample-dungeon"]["grid"]`` object, so a
    # PREVIOUS run of this script (or a crashed one) can leave a door open and
    # change the "fresh" defaults this script asserts. Drive a GM ``lock`` at
    # each sample doorway on a throwaway session: from ``O`` it force-closes
    # to ``L``, from ``U`` it locks to ``L``, from ``L`` it is the no-op
    # "door is already locked" reply (drained). This mirrors the TestDoorWire
    # reset in tests/test_ws.py (same shared-grid root cause, documented as a
    # known issue for QA).
    print("\n[0b] precondition: re-lock the shared sample grid's doors")
    norm = WSClient(HOST, PORT, path=f"/ws?session=qa-doors-norm-{int(time.time())}",
                    timeout=TIMEOUT).connect()
    try:
        norm.join("Norm-GM", "gm")
        # a known free floor cell adjacent to each door (for moving a
        # stray token off the door, if a crashed run left one there).
        fallback = {(5, 5): (4, 6), (10, 4): (9, 4), (9, 7): (8, 7)}
        for (x, y) in ((5, 5), (10, 4), (9, 7)):
            for attempt in range(3):
                norm.send_json({"type": "door", "x": x, "y": y,
                                "action": "lock"})
                r = norm.recv_json()
                if r["type"] != "error" or r["message"] != \
                        "cannot close a door with a token on it":
                    break  # locked (state broadcast) or the no-op error
                # a token sits on the door: move it off, then retry.
                norm.send_json({"type": "request_state"})
                st = get_state(norm)
                blocker = next((e for e in st["entities"]
                                if (e["x"], e["y"]) == (x, y)), None)
                if blocker is None:
                    break
                fx, fy = fallback[(x, y)]
                norm.send_json({"type": "place", "entity_id":
                                blocker["id"], "x": fx, "y": fy})
                get_state(norm)
    finally:
        norm.close()
    norm_check = rest_map().get("doors")
    check("precondition: all three sample doors are at the all-locked "
          "default",
          norm_check == DOORS_ALL_L, json.dumps(norm_check))

    # 1. REST exposes the additive doors field -----------------------------
    print("\n[1] REST: GET /api/maps/sample-dungeon exposes `doors`")
    rd = rest_map()
    check("REST map detail: id == sample-dungeon", rd.get("id") == SAMPLE,
          json.dumps(rd.get("id")))
    check("REST map detail: additive 'doors' key present", "doors" in rd,
          json.dumps(list(rd.keys())))
    check("REST doors: all three sample doors L (fresh map, AC2/CR1)",
          rd.get("doors") == DOORS_ALL_L, json.dumps(rd.get("doors")))

    # 2-8. one fresh session: GM + Alice on the sample map ------------------
    sid = f"qa-doors-{int(time.time())}"
    gm = WSClient(HOST, PORT, path=f"/ws?session={sid}", timeout=TIMEOUT).connect()
    al = WSClient(HOST, PORT, path=f"/ws?session={sid}", timeout=TIMEOUT).connect()
    try:
        wg = gm.join("QA-GM", "gm")
        wa = al.join("Alice", "player")
        check("GM welcome role=gm", wg.get("type") == "welcome"
              and wg.get("you", {}).get("role") == "gm")
        check("Alice welcome role=player", wa.get("type") == "welcome"
              and wa.get("you", {}).get("role") == "player")
        get_state(gm)               # GM's join broadcast (Alice joined)

        m = wa["map"]
        w, h, cells = m["width"], m["height"], m["cells"]
        check("sample dungeon 16x12 on the wire", (w, h) == (16, 12),
              f"got {w}x{h}")
        spawn = (wa["you_entity"]["x"], wa["you_entity"]["y"])
        check("Alice spawns at (1,1)", spawn == (1, 1), str(spawn))
        al_ent = wa["you"]["entity_id"]

        print("\n[2] welcome carries the FULL map.doors, all three L")
        check("GM welcome map.doors: FULL set, all three L (AC1/AC2/A9)",
              wg["map"].get("doors") == DOORS_ALL_L,
              json.dumps(wg["map"].get("doors")))
        check("player welcome map.doors: FULL set, all three L",
              wa["map"].get("doors") == DOORS_ALL_L,
              json.dumps(wa["map"].get("doors")))

        # 3. GM state machine: unlock -> U, open -> O ----------------------
        print("\n[3] GM unlock (5,5) -> U, GM open -> O (broadcast to all)")
        err, (stu, stu_pl) = door(gm, al, 5, 5, "unlock", "U")
        check("GM unlock -> 'U' broadcast to GM + player",
              err is None and stu["map"]["doors"]["5,5"] == "U"
              and stu_pl["map"]["doors"]["5,5"] == "U",
              json.dumps(err or stu["map"]["doors"]))
        check("unlock leaves the other two doors L",
              stu["map"]["doors"]["10,4"] == "L"
              and stu["map"]["doors"]["9,7"] == "L",
              json.dumps(stu["map"]["doors"]))
        err, (sto, sto_pl) = door(gm, al, 5, 5, "open", "O")
        check("GM open -> 'O' broadcast to GM + player",
              err is None and sto["map"]["doors"]["5,5"] == "O"
              and sto_pl["map"]["doors"]["5,5"] == "O",
              json.dumps(err or sto["map"]["doors"]))
        check("open door (5,5) is walkable (independent A* has a route)",
              find_path(Grid.from_dict(sto["map"]), (1, 1), (7, 2)) is not None)

        # 4. permission matrix + exact error strings (AC3, §4.3 order) ---
        print("\n[4] permission matrix + state-machine errors (AC3)")
        # the door is O (left open by [3]). GM closes it: O -> U.
        err, (s4, _) = door(gm, al, 5, 5, "close", "U")
        check("GM close 'O' -> 'U' (start the matrix at 'U')",
              err is None and s4["map"]["doors"]["5,5"] == "U",
              json.dumps(err or s4["map"]["doors"]))
        err, _ = door(al, gm, 5, 5, "unlock")
        check("player unlock on 'U' -> 'door is already unlocked' (the "
              "transition check precedes the role check, §4.3)",
              err == {"type": "error", "message": "door is already unlocked"},
              json.dumps(err))
        err, _ = door(al, gm, 5, 5, "lock")
        check("player lock on 'U' -> 'not allowed' (GM-only; the "
              "transition U->L is legal, then the role check rejects)",
              err == {"type": "error", "message": "not allowed"},
              json.dumps(err))
        err, (a1, g1) = door(al, gm, 5, 5, "open", "O")
        check("player open on UNLOCKED door -> 'O' (the accepted tap "
              "mapping U->open)",
              err is None and a1["map"]["doors"]["5,5"] == "O"
              and g1["map"]["doors"]["5,5"] == "O",
              json.dumps(err or a1["map"]["doors"]))
        err, (a2, g2) = door(al, gm, 5, 5, "close", "U")
        check("player close on OPEN door -> 'U' (the accepted tap "
              "mapping O->close)",
              err is None and a2["map"]["doors"]["5,5"] == "U"
              and g2["map"]["doors"]["5,5"] == "U",
              json.dumps(err or a2["map"]["doors"]))
        err, _ = door(gm, al, 5, 5, "close")
        check("GM close on 'U' (already closed) -> 'door is already "
              "closed'",
              err == {"type": "error", "message": "door is already closed"},
              json.dumps(err))
        err, (s6, _) = door(gm, al, 5, 5, "lock", "L")
        check("GM lock 'U' -> 'L' (door locked)",
              err is None and s6["map"]["doors"]["5,5"] == "L",
              json.dumps(err or s6["map"]["doors"]))
        err, _ = door(al, gm, 5, 5, "unlock")
        check("player unlock on LOCKED door -> 'not allowed' (the spec "
              "player-unlock rejection)",
              err == {"type": "error", "message": "not allowed"},
              json.dumps(err))
        err, _ = door(al, gm, 5, 5, "open")
        check("player open on LOCKED door -> 'door is locked' (the "
              "accepted tap mapping L->open; the server rejects)",
              err == {"type": "error", "message": "door is locked"},
              json.dumps(err))
        err, _ = door(gm, al, 5, 5, "close")
        check("GM close on 'L' -> 'door is locked'",
              err == {"type": "error", "message": "door is locked"},
              json.dumps(err))
        err, _ = door(gm, al, 5, 5, "lock")
        check("GM lock on 'L' -> 'door is already locked'",
              err == {"type": "error", "message": "door is already locked"},
              json.dumps(err))
        err, (s7, s7_pl) = door(gm, al, 5, 5, "unlock", "U")
        check("GM unlock 'L' -> 'U' (again)",
              err is None and s7["map"]["doors"]["5,5"] == "U"
              and s7_pl["map"]["doors"]["5,5"] == "U",
              json.dumps(err or s7["map"]["doors"]))
        err, _ = door(gm, al, 5, 5, "unlock")
        check("GM unlock on 'U' -> 'door is already unlocked'",
              err == {"type": "error", "message": "door is already unlocked"},
              json.dumps(err))
        err, (s8, s8_pl) = door(gm, al, 5, 5, "open", "O")
        check("GM open 'U' -> 'O' (door left open for the occupancy guard)",
              err is None and s8["map"]["doors"]["5,5"] == "O"
              and s8_pl["map"]["doors"]["5,5"] == "O",
              json.dumps(err or s8["map"]["doors"]))
        err, _ = door(gm, al, 5, 5, "open")
        check("GM open on 'O' -> 'door is already open'",
              err == {"type": "error", "message": "door is already open"},
              json.dumps(err))

        # 5. occupancy guard -------------------------------------------------
        print("\n[5] occupancy guard: token on the door cell (D3/AC9)")
        gpr, _ = gm_place(gm, al, al_ent, 5, 5)
        check("GM placed Alice ON the open door cell (5,5)",
              ent_at(gpr, al_ent) == (5, 5), json.dumps(ent_at(gpr, al_ent)))
        err, _ = door(gm, al, 5, 5, "close")
        check("GM close with token on the door -> 'cannot close a door "
              "with a token on it'",
              err == {"type": "error",
                      "message": "cannot close a door with a token on it"},
              json.dumps(err))
        err, _ = door(gm, al, 5, 5, "lock")
        check("GM lock (force-close) with token on the door -> same "
              "rejection (A5)",
              err == {"type": "error",
                      "message": "cannot close a door with a token on it"},
              json.dumps(err))
        gm.send_json({"type": "request_state"})
        st_after = get_state(gm)
        check("door stayed OPEN after the rejected close/lock",
              st_after["map"]["doors"]["5,5"] == "O",
              json.dumps(st_after["map"]["doors"]))
        gm_place(gm, al, al_ent, 4, 6)
        err, (s9, s9_pl) = door(gm, al, 5, 5, "close", "U")
        check("close succeeds once the token is off the door (-> 'U')",
              err is None and s9["map"]["doors"]["5,5"] == "U"
              and s9_pl["map"]["doors"]["5,5"] == "U",
              json.dumps(err or s9["map"]["doors"]))
        err, (s10, _) = door(gm, al, 5, 5, "lock", "L")
        check("GM re-lock 'U' -> 'L' (door closed for the movement test)",
              err is None and s10["map"]["doors"]["5,5"] == "L",
              json.dumps(err or s10["map"]["doors"]))

        # 6. movement: closed door blocks, open door routes ----------------
        print("\n[6] movement: closed door blocks A*; open door routes")
        frame, _, _ = gm_move(gm, al, al_ent, 7, 2)
        check("GM move Alice THROUGH the closed door -> 'no route — wall "
              "in the way'",
              frame == {"type": "error", "message": "no route — wall in the "
                        "way"},
              json.dumps(frame))
        gm.send_json({"type": "request_state"})
        st_nr = get_state(gm)
        check("Alice position unchanged after the no-route",
              ent_at(st_nr, al_ent) == (4, 6),
              json.dumps(ent_at(st_nr, al_ent)))
        door(gm, al, 5, 5, "unlock", "U")
        gopen = door(gm, al, 5, 5, "open", "O")[1][0]
        ggrid = Grid.from_dict(gopen["map"])
        check("independent A*: (4,6)->(7,2) routes through the OPEN door, "
              "and is NONE when the door is closed",
              find_path(ggrid, (4, 6), (7, 2)) is not None
              and find_path(Grid.from_dict(m), (4, 6), (7, 2)) is None)
        frame, gmv, _ = gm_move(gm, al, al_ent, 7, 2)
        steps = frame.get("path", []) if frame.get("type") == "path" else []
        check("GM got a path frame for the open-door move",
              frame.get("type") == "path" and frame.get("entity_id")
              == al_ent, json.dumps(frame))
        check("path starts (4,6), ends (7,2), through the door cell (5,5)",
              bool(steps) and (steps[0]["x"], steps[0]["y"]) == (4, 6)
              and (steps[-1]["x"], steps[-1]["y"]) == (7, 2)
              and (5, 5) in {(p["x"], p["y"]) for p in steps},
              json.dumps(steps))
        check("every path step is a legal A* step (open door walkable)",
              _all_steps_legal(ggrid, steps))
        check("Alice token reached (7,2) (walked through the open door)",
              ent_at(gmv, al_ent) == (7, 2), json.dumps(ent_at(gmv, al_ent)))

        # 7. GM override moves a token onto a closed door -----------------
        print("\n[7] GM override:true moves a token onto a closed door (A3)")
        gcr, _ = gm_create(gm, al, "Grom", "npc", "neutral", 11, 3)
        grom_ent = next(e["id"] for e in gcr["entities"]
                        if e["name"] == "Grom")
        check("GM created npc 'Grom' at (11,3) (right room)",
              grom_ent is not None and ent_at(gcr, grom_ent) == (11, 3),
              json.dumps(ent_at(gcr, grom_ent)))
        frame, _, _ = gm_move(gm, al, grom_ent, 9, 4)
        check("GM move Grom across the closed (10,4) door (no override) -> "
              "'no route — wall in the way'",
              frame == {"type": "error", "message": "no route — wall in the "
                        "way"},
              json.dumps(frame))
        frame, gmv2, _ = gm_move(gm, al, grom_ent, 10, 4, override=True)
        check("GM override:true -> path frame exactly the closed door cell "
              "(10,4)",
              frame.get("type") == "path"
              and frame.get("path") == [{"x": 10, "y": 4}],
              json.dumps(frame))
        check("Grom token is ON the closed door (10,4) after override "
              "(bypass)",
              ent_at(gmv2, grom_ent) == (10, 4),
              json.dumps(ent_at(gmv2, grom_ent)))
        gm_place(gm, al, grom_ent, 9, 4)     # off the door, for the rest

        # 8. force-close re-lock + use_map + REST --------------------------
        print("\n[8] GM re-lock of an OPEN door (force-close); use_map; REST")
        door(gm, al, 10, 4, "unlock", "U")
        door(gm, al, 10, 4, "open", "O")
        err, (sl, sl_pl) = door(gm, al, 10, 4, "lock", "L")
        check("GM re-lock of the OPEN (10,4) door -> 'L' (force-close, A7)",
              err is None and sl["map"]["doors"]["10,4"] == "L"
              and sl_pl["map"]["doors"]["10,4"] == "L",
              json.dumps(err or sl["map"]["doors"]))
        door(gm, al, 9, 7, "unlock", "U")
        g_um, _ = gm_use_map(gm, al, SAMPLE)
        check("use_map(sample) keeps the CURRENT door states (no reset — "
              "the grid object is shared; (5,5) still O from the walk)",
              g_um["map"].get("doors") == {"5,5": "O", "10,4": "L",
                                           "9,7": "U"},
              json.dumps(g_um["map"].get("doors")))
        door(gm, al, 9, 7, "lock", "L")
        rd2 = rest_map()
        check("REST reflects the live shared door state (5,5 O; same Grid "
              "object, §8.2)",
              rd2.get("doors") == {"5,5": "O", "10,4": "L", "9,7": "L"},
              json.dumps(rd2.get("doors")))
        door(gm, al, 9, 7, "unlock", "U")
        rd3 = rest_map()
        check("REST reflects a live WS unlock immediately (9,7 -> U)",
              rd3.get("doors") == {"5,5": "O", "10,4": "L", "9,7": "U"},
              json.dumps(rd3.get("doors")))
        door(gm, al, 9, 7, "lock", "L")
        door(gm, al, 5, 5, "lock", "L")
        rd4 = rest_map()
        check("REST back to all-locked after the full churn (clean "
              "hand-off to the next session)",
              rd4.get("doors") == DOORS_ALL_L, json.dumps(rd4.get("doors")))

    finally:
        for c in (al, gm):
            try:
                c.close()
            except Exception:
                pass

    # 9-10. awareness + explored, door-driven (fresh session) --------------
    print("\n[9-10] awareness + explored, door-driven (fresh session)")
    sid2 = f"qa-doors-aw-{int(time.time())}"
    gm2 = WSClient(HOST, PORT, path=f"/ws?session={sid2}",
                   timeout=TIMEOUT).connect()
    al2 = WSClient(HOST, PORT, path=f"/ws?session={sid2}",
                   timeout=TIMEOUT).connect()
    try:
        gm2.join("GM2", "gm")
        wal2 = al2.join("Alice2", "player")
        al2_ent = wal2["you"]["entity_id"]
        get_state(gm2)             # GM2's join broadcast (Alice2 joined)
        m2 = wal2["map"]
        w2, h2, cells2 = m2["width"], m2["height"], m2["cells"]
        check("fresh session: Alice2 spawns (1,1); doors all L again "
              "(shared grid restored)",
              (wal2["you_entity"]["x"], wal2["you_entity"]["y"]) == (1, 1)
              and wal2["map"]["doors"] == DOORS_ALL_L,
              json.dumps(wal2["map"]["doors"]))

        # enemy just past the (5,5) door (central room).
        st_vex, al_create = gm_create(gm2, al2, "Vex", "enemy", "hostile",
                                      6, 5)
        vex_ent = next(e["id"] for e in st_vex["entities"]
                       if e["name"] == "Vex")
        check("GM sees the enemy 'Vex' FULL (never filtered, I3)",
              any(i["entity_id"] == vex_ent and i["label"] is True
                  and i["name"] == "Vex" for i in st_vex["awareness"]),
              json.dumps(st_vex["awareness"]))
        check("CLOSED door + beyond the radius: enemy (6,5) is INVISIBLE "
              "to Alice2 at spawn (1,1) (cheb 5 > radius 4, no LOS)",
              vex_ent not in {i.get("entity_id")
                              for i in al_create.get("awareness", [])}
              and not any(i.get("approximate")
                          for i in al_create.get("awareness", [])),
              json.dumps(al_create.get("awareness")))
        _, al_place = gm_place(gm2, al2, al2_ent, 4, 5)
        check("CLOSED door, within the radius: enemy (6,5) is APPROXIMATE "
              "for Alice2 at (4,5) (block (3,2), no identity)",
              _approx_present(al_place, (6, 5)),
              json.dumps(al_place.get("awareness")))
        door(gm2, al2, 5, 5, "unlock", "U")
        err, (st_open, st_open_pl) = door(gm2, al2, 5, 5, "open", "O")
        check("OPEN door: enemy (6,5) is now FULL (named/labeled) for "
              "Alice2 (clear LOS through the door)",
              err is None and _full_present(st_open_pl, vex_ent),
              json.dumps(err or st_open_pl.get("awareness")))
        gview = Player(id="v", name="Alice2", role="player",
                       entity_id=al2_ent)
        exp_aw = build_awareness(gview, player_entities(st_open),
                                 Grid.from_dict(st_open["map"]))
        check("awareness (door open) == build_awareness (byte-equal, CR5)",
              st_open_pl["awareness"] == exp_aw,
              f"server={json.dumps(st_open_pl['awareness'])} "
              f"expected={json.dumps(exp_aw)}")

        # explored, door OPEN: S-set re-derives; the far room stays hidden.
        pmask = st_open_pl["visibility"]
        check("explored: visibility well-formed (h rows x w chars, SEH)",
              wellformed(pmask, w2, h2), f"type={type(pmask).__name__}")
        s0, e0, h0 = tier_sets(pmask)
        derived0 = derive_visible(cells2, w2, h2, st_open["map"]["doors"],
                                  (4, 5))
        check("explored (door open): S-set == door-aware re-derivation "
              "at (4,5)",
              s0 == derived0,
              f"server_only={sorted(s0 - derived0)} "
              f"derived_only={sorted(derived0 - s0)}")
        check("explored: (6,5) is S (seen through the open door)",
              (6, 5) in s0, pmask[5][6])
        check("explored: the (5,5) door FACE is S (D5)",
              (5, 5) in s0, pmask[5][5])
        check("explored: a far never-seen cell (14,10) is H",
              (14, 10) in h0, pmask[10][14])
        ever_se = s0 | e0

        # close the door: (6,5) drops out of sight -> E (greyed), NOT H.
        err, (st_cl, st_cl_pl) = door(gm2, al2, 5, 5, "close", "U")
        pmask2 = st_cl_pl["visibility"]
        s1, e1, h1 = tier_sets(pmask2)
        check("explored (door closed): (6,5) is E (greyed memory, NOT H)",
              (6, 5) in e1, pmask2[5][6])
        derived1 = derive_visible(cells2, w2, h2, st_cl["map"]["doors"],
                                  (4, 5))
        check("explored (door closed): S-set == door-aware re-derivation",
              s1 == derived1,
              f"server_only={sorted(s1 - derived1)} "
              f"derived_only={sorted(derived1 - s1)}")
        check("explored (door closed): monotonic — no S/E cell became H",
              not (ever_se & h1), f"regressed={sorted(ever_se & h1)}")

        # walk Alice2 through the open door to (7,2), back to (4,5), then
        # close: the seen cells grey to E, the never-seen far band stays H.
        door(gm2, al2, 5, 5, "unlock", "U")
        door(gm2, al2, 5, 5, "open", "O")
        frame, gmv3, al3 = gm_move(gm2, al2, al2_ent, 7, 2)
        steps = frame.get("path", []) if frame.get("type") == "path" else []
        check("GM path (4,5)->(7,2) through the open door; token at (7,2)",
              frame.get("type") == "path"
              and (steps[-1]["x"], steps[-1]["y"]) == (7, 2)
              and (5, 5) in {(p["x"], p["y"]) for p in steps}
              and ent_at(gmv3, al2_ent) == (7, 2),
              json.dumps(frame))
        s2, e2, h2_ = tier_sets(al3["visibility"])
        check("explored: at (7,2) the cell (7,2) is S (token region)",
              (7, 2) in s2, al3["visibility"][2][7])
        ever_se |= s2 | e2
        # walk back to (4,5) while the door is STILL open.
        frame, gmv4, al4 = gm_move(gm2, al2, al2_ent, 4, 5)
        steps = frame.get("path", []) if frame.get("type") == "path" else []
        check("GM moved Alice2 back to (4,5) (door still open on return)",
              frame.get("type") == "path"
              and (steps[-1]["x"], steps[-1]["y"]) == (4, 5)
              and ent_at(gmv4, al2_ent) == (4, 5),
              json.dumps(frame))
        # now close the door (Alice2 is in the left room) and read her mask.
        err, (stc, stc_pl) = door(gm2, al2, 5, 5, "close", "U")
        pmask3 = stc_pl["visibility"]
        s3, e3, h3 = tier_sets(pmask3)
        check("explored (door closed, back at (4,5)): (6,5) and (7,2) are "
              "E (greyed memory, NOT H)",
              (6, 5) in e3 and (7, 2) in e3,
              f"(6,5)={pmask3[5][6]} (7,2)={pmask3[2][7]}")
        check("explored: the (5,5) door FACE is S while the room beyond is "
              "E (greyed, distinct)",
              (5, 5) in s3 and (7, 2) in e3,
              f"face={pmask3[5][5]} (7,2)={pmask3[2][7]}")
        check("explored: the far never-seen band (14,10) is still H",
              (14, 10) in h3, pmask3[10][14])
        derived3 = derive_visible(cells2, w2, h2, stc["map"]["doors"],
                                  (4, 5))
        check("explored (final): S-set == door-aware re-derivation at (4,5)",
              s3 == derived3,
              f"server_only={sorted(s3 - derived3)} "
              f"derived_only={sorted(derived3 - s3)}")
        check("explored (final): monotonic — no S/E cell ever became H",
              not (ever_se & h3), f"regressed={sorted(ever_se & h3)}")
    finally:
        for c in (al2, gm2):
            try:
                c.close()
            except Exception:
                pass

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
