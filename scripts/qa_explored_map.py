#!/usr/bin/env python3
"""QA live verification — "explored map" (player fog-of-war with memory).

Spec:  docs/design/explored-map.md §12 (AC1, AC2, AC3, AC4, AC6, AC12)
Wire:  player welcome/state carry ``visibility`` (list of ``height``
       row-strings of ``width`` chars, alphabet ``"S"``/``"E"``/``"H"``);
       the GM payload has the key ABSENT.  S = in sight now, E = explored
       (greyed memory), H = never seen.

This script is DELIBERATELY INDEPENDENT of the feature's server code: it
does NOT import ``app.visibility``.  It re-derives the S-set itself from the
wire ``map.cells`` + the token position, using the spec's S1/S2 rules and
the real ``app.pathfinding.has_line_of_sight`` (frozen reference — the same
Bresenham the awareness system uses).  It DOES import
``app.awareness.build_awareness`` (allowed: that is the frozen pre-feature
reference the awareness-unchanged hard constraint is checked against).

Against a LIVE server on 127.0.0.1:8000 it:
  1. joins a FRESH session (unique id) with GM + Alice (player) on the
     sample dungeon;
  2. asserts the GM's welcome/join-state have NO ``visibility`` key;
  3. asserts Alice's welcome ``visibility`` is well-formed (height rows ×
     width, SEH), has ZERO ``"E"`` (nothing explored yet), and her ``"S"``
     set EXACTLY equals the script's independent re-derivation at spawn;
  4. asserts Alice's awareness equals ``build_awareness`` (unchanged) and
     the ``players[]`` entries keep their exact pre-feature shape;
  5. the GM creates two npcs — one in the target room with LOS (FULL) and
     one wall-blocked but within awareness range (APPROXIMATE);
  6. the GM moves Alice (legal A* path) to a floor cell in a DIFFERENT
     room (a distinct FLOOR region reached through a doorway); asserts the
     next player state's ``"S"`` set == re-derivation at the new position,
     at least one previously-``"S"`` cell is now ``"E"`` (not ``"H"``),
     NO previously-``"S"/"E"`` cell became ``"H"`` (monotonicity), and the
     ``"E"`` set == explored-so-far minus ``"S"``;
  7. asserts a specific far room (the bottom band, behind the row-7 wall)
     still has many ``"H"`` cells in the final mask (walls hold);
  8. asserts the final awareness is non-empty, three-tier (a FULL LOS item
     and an APPROXIMATE no-identity item), and byte-equal to
     ``build_awareness`` (awareness unchanged, the hard constraint).

Run:  .venv/bin/python scripts/qa_explored_map.py
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.awareness import build_awareness          # frozen reference (allowed)
from app.models import Entity, Grid, Player
from app.pathfinding import has_line_of_sight, is_valid_step
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
# Independent re-derivation of the S-set (spec §3.2 S1/S2 — NO app.visibility)
# ---------------------------------------------------------------------------

def derive_visible(cells, w, h, pos):
    """Re-derive the S-set at ``pos`` from the spec rules + real LOS.

    (S-B) the anchor ``pos`` is always S (walkability waived);
    (S1)  a walkable cell is S iff it has line of sight from ``pos``;
    (S2)  a wall cell is S iff one of its four in-bounds orthogonal walkable
          neighbours has line of sight from ``pos`` (4-adjacency only — NO
          8-neighbourhood reveal).
    """
    g = Grid.from_dict({"width": w, "height": h, "cells": cells})
    px, py = int(pos[0]), int(pos[1])
    seen = {(px, py)}
    for y in range(h):
        for x in range(w):
            c = cells[y][x]
            if c == "wall":
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and cells[ny][nx] in (
                            "floor", "doorway") \
                            and has_line_of_sight(g, (px, py), (nx, ny)):
                        seen.add((x, y))
                        break
            elif (x, y) != (px, py) and has_line_of_sight(g, (px, py), (x, y)):
                seen.add((x, y))
    return seen


def room_floor(cells, w, h, origin):
    """FLOOR-ONLY (no doorway) 4-neighbour flood from ``origin`` — the room
    the origin stands in.  Doorways are the boundaries BETWEEN rooms, so a
    floor-only flood isolates one room; this is what makes 'a different
    room' meaningful (a walkable flood would connect every room)."""
    ox, oy = int(origin[0]), int(origin[1])
    if not (0 <= ox < w and 0 <= oy < h) or cells[oy][ox] != "floor":
        return set()
    seen, stack = {(ox, oy)}, [(ox, oy)]
    while stack:
        cx, cy = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen \
                    and cells[ny][nx] == "floor":
                seen.add((nx, ny))
                stack.append((nx, ny))
    return seen


def tier_sets(mask):
    """``(S set, E set, H set)`` of a wire matrix (row y, char x)."""
    s, e, h = set(), set(), set()
    for y, row in enumerate(mask):
        for x, ch in enumerate(row):
            (s if ch == "S" else e if ch == "E" else h).add((x, y))
    return s, e, h


def wellformed(mask, w, h):
    return (
        isinstance(mask, list) and len(mask) == h
        and all(isinstance(r, str) and len(r) == w and set(r) <= set("SEH")
                for r in mask)
    )


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------

def get_state(c):
    """Next ``state`` frame for ``c`` (skips anything else)."""
    for _ in range(40):
        m = c.recv_json()
        if m["type"] == "state":
            return m
    raise RuntimeError("no state frame within 40 frames")


def gm_move(gm, pl, ent_id, x, y):
    """GM moves ``ent_id`` to (x, y) (A*, no override).  Returns the frames
    (gpath, gstate, ppath, pstate) for the GM and the player."""
    gm.send_json({"type": "move", "entity_id": ent_id, "x": x, "y": y})
    gpath = gm.recv_json()          # GM path
    gstate = gm.recv_json()         # GM state
    ppath = pl.recv_json()          # player path
    pstate = get_state(pl)          # player state
    return gpath, gstate, ppath, pstate


def player_entities(gstate):
    """Entity dict from a GM state payload (all entities, for awareness)."""
    return {e["id"]: Entity.from_dict(e) for e in gstate["entities"]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 0. health gate (exit 2 with a hint if the server is not up) --------
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=3)
        conn.request("GET", "/health")
        health = json.loads(conn.getresponse().read())
        conn.close()
    except OSError as exc:
        print(f"{FAIL} server not reachable at {HOST}:{PORT}: {exc}")
        print("  hint: start it with")
        print("    cd /Users/agrant3/agentteam && PYTHONUNBUFFERED=1 nohup "
              "./.venv/bin/python -m app.main --host 127.0.0.1 --port 8000 "
              ">> .ld_server.log 2>&1 & echo $! > .ld_server.pid")
        return 2
    print(f"[0] server up (health={health})")
    check("GET /health -> {'status':'ok'}",
          isinstance(health, dict) and health.get("status") == "ok",
          json.dumps(health))

    sid = f"qa-explored-{int(time.time())}"
    gm = WSClient(HOST, PORT, path=f"/ws?session={sid}", timeout=TIMEOUT).connect()
    al = WSClient(HOST, PORT, path=f"/ws?session={sid}", timeout=TIMEOUT).connect()
    try:
        # 1. joins on a FRESH session ------------------------------------
        print("\n[1] fresh session: GM + Alice (player) on the sample map")
        wg = gm.join("QA-GM", "gm")
        wa = al.join("Alice", "player")
        check("GM welcome role=gm", wg.get("type") == "welcome"
              and wg.get("you", {}).get("role") == "gm")
        check("GM welcome: NO 'visibility' key", "visibility" not in wg)
        check("Alice welcome role=player", wa.get("type") == "welcome"
              and wa.get("you", {}).get("role") == "player")
        gjs = get_state(gm)          # GM's join broadcast (Alice joined)
        check("GM join-state: NO 'visibility' key", "visibility" not in gjs)

        m = wa["map"]
        w, h, cells = m["width"], m["height"], m["cells"]
        check("sample dungeon 16x12 on the wire", (w, h) == (16, 12),
              f"got {w}x{h}")
        spawn = (wa["you_entity"]["x"], wa["you_entity"]["y"])
        check("Alice spawns at (1,1)", spawn == (1, 1), str(spawn))
        al_ent = wa["you"]["entity_id"]

        # 2. Alice's welcome visibility ----------------------------------
        print("\n[2] Alice's welcome: well-formed, zero E, S == re-derived")
        vis = wa.get("visibility")
        check("player welcome: 'visibility' present + well-formed (h rows x w, SEH)",
              wellformed(vis, w, h), f"type={type(vis).__name__}")
        ws_, we, wh = tier_sets(vis)
        check("welcome has ZERO 'E' (nothing explored yet)", we == set(),
              f"E={sorted(we)}")
        derived_spawn = derive_visible(cells, w, h, spawn)
        check("welcome S-set == independent re-derivation at spawn",
              ws_ == derived_spawn,
              f"server_only={sorted(ws_ - derived_spawn)} "
              f"derived_only={sorted(derived_spawn - ws_)}")
        check("welcome: spawn cell is S", spawn in ws_)
        check("welcome S-set is a real room (floor + wall faces)",
              30 < len(ws_) < 100, f"|S|={len(ws_)}")

        # 3. awareness + players[] shape at welcome ------------------------
        print("\n[3] awareness unchanged + players[] shape (welcome)")
        viewer = Player(id="v", name="Alice", role="player", entity_id=al_ent)
        expected_aw = build_awareness(viewer, player_entities(gjs),
                                      Grid.from_dict(m))
        check("welcome awareness == build_awareness (unchanged)",
              wa["awareness"] == expected_aw, json.dumps(wa["awareness"]))
        shape_ok = all(
            sorted(entry) == ["awareness_radius", "entity_id", "id", "name",
                              "role"]
            for entry in wa["players"])
        check("players[] entries keep exact pre-feature shape",
              shape_ok and len(wa["players"]) == 2, json.dumps(wa["players"]))

        # 4. GM creates two npcs (a FULL LOS contact + an APPROX one) -----
        print("\n[4] GM creates npc 'Far' (6,1) + npc 'Cloak' (11,3)")
        gm.send_json({"type": "create_entity", "name": "Far", "kind": "npc",
                      "team": "neutral", "x": 6, "y": 1})
        st_f = get_state(gm)
        al.recv_json()               # Alice's create broadcast (state)
        gm.send_json({"type": "create_entity", "name": "Cloak", "kind": "npc",
                      "team": "neutral", "x": 11, "y": 3})
        st_c = get_state(gm)
        al.recv_json()               # Alice's create broadcast (state)
        check("GM now sees 3 entities (Alice + Far + Cloak)",
              len(st_c["entities"]) == 3, json.dumps(st_c["entities"]))

        # 5. move target: a floor cell in a DIFFERENT room ----------------
        print("\n[5] GM moves Alice into a different room (legal A* path)")
        grid = Grid.from_dict(m)
        left_room = room_floor(cells, w, h, spawn)
        # (7,2) is a canonical middle-room target: floor, NOT in Alice's
        # left room, reachable through the (5,5) doorway, and it keeps the
        # bottom band hidden.  Fallback: any floor cell outside left_room.
        target = (7, 2) if (cells[2][7] == "floor"
                            and (7, 2) not in left_room) \
            else next((p for p in
                       {(x, y) for y in range(h) for x in range(w)
                        if cells[y][x] == "floor" and (x, y) not in left_room}),
                       None)
        check("move target is a floor cell in a different room",
              target is not None and cells[target[1]][target[0]] == "floor"
              and target not in left_room, f"target={target}")
        tx, ty = target

        gpath, gstate, ppath, pstate = gm_move(gm, al, al_ent, tx, ty)
        check("GM got a path frame for the move",
              gpath.get("type") == "path" and gpath.get("entity_id") == al_ent,
              json.dumps(gpath))
        steps = gpath.get("path", [])
        check("path starts at spawn, ends at target",
              bool(steps) and (steps[0]["x"], steps[0]["y"]) == spawn
              and (steps[-1]["x"], steps[-1]["y"]) == target,
              json.dumps(steps))
        check("path has >= 2 steps (a real cross-room move)", len(steps) >= 2,
              f"len={len(steps)}")
        check("every path step is a legal A* step (no corner cuts)",
              all(is_valid_step(grid, (steps[i]["x"], steps[i]["y"]),
                                (steps[i + 1]["x"], steps[i + 1]["y"]))
                  for i in range(len(steps) - 1)))
        check("GM post-move state: NO 'visibility' key",
              "visibility" not in gstate)
        check("player post-move state has 'visibility'",
              "visibility" in pstate)
        pos = (pstate["you_entity"]["x"], pstate["you_entity"]["y"])
        check("token is at the target position", pos == target,
              f"got {pos} want {target}")

        # 6. tier transitions + monotonicity at the new position ----------
        print("\n[6] tier flips, monotonicity, explored-so-far invariant")
        pmask = pstate["visibility"]
        check("post-move visibility well-formed", wellformed(pmask, w, h))
        s_now, e_now, h_now = tier_sets(pmask)
        derived_now = derive_visible(pstate["map"]["cells"], w, h, pos)
        check("post-move S-set == independent re-derivation at new position",
              s_now == derived_now,
              f"server_only={sorted(s_now - derived_now)} "
              f"derived_only={sorted(derived_now - s_now)}")
        check(">=1 previously-S cell is now E (greyed memory, not dark)",
              bool(ws_ - s_now), f"count={len(ws_ - s_now)}")
        regressed = (ws_ | we) & h_now
        check("monotonicity: no S/E cell became H", not regressed,
              f"regressed={sorted(regressed)}")
        check("'E' set == explored-so-far minus current 'S'",
              e_now == (ws_ | we) - s_now,
              f"E={len(e_now)} want {len((ws_ | we) - s_now)}")
        check("'S' wins over 'E' (no cell in both)", not (s_now & e_now))

        # 7. far room behind the row-7 wall (bottom band) stays hidden ----
        print("\n[7] far room (bottom band, y>=8) stays hidden")
        far_band = {(x, y) for y in range(8, h) for x in range(6, w)
                    if cells[y][x] == "floor"}
        hidden_far_wire = {p for p in far_band if pmask[p[1]][p[0]] == "H"}
        hidden_far_derived = far_band - derived_now
        check("far band is a real region", len(far_band) > 10,
              f"|far|={len(far_band)}")
        check("far band majority is hidden (re-derived, no LOS)",
              len(hidden_far_derived) > len(far_band) // 2,
              f"hidden={len(hidden_far_derived)}/{len(far_band)}")
        check("far band majority is H in the wire mask",
              len(hidden_far_wire) > len(far_band) // 2,
              f"H={len(hidden_far_wire)}/{len(far_band)}")
        # a specific far corner, re-derived to be out of sight:
        far_cell = (14, 10)
        check("specific far cell (14,10) is H in the mask",
              pmask[10][14] == "H", pmask[10][14])
        check("specific far cell (14,10) is NOT in the re-derived S-set",
              far_cell not in derived_now, str(sorted(derived_now))[:80])

        # 8. awareness still three-tier at the final state ----------------
        print("\n[8] awareness unchanged + three-tier at final state")
        viewer2 = Player(id="v", name="Alice", role="player", entity_id=al_ent)
        expected_final = build_awareness(viewer2, player_entities(gstate),
                                         Grid.from_dict(pstate["map"]))
        aw = pstate["awareness"]
        check("final awareness NON-EMPTY", bool(aw), json.dumps(aw))
        check("final awareness == build_awareness (byte-equal, unchanged)",
              aw == expected_final,
              f"server={json.dumps(aw)} expected={json.dumps(expected_final)}")
        full_items = [i for i in aw if not i.get("approximate")
                      and i.get("label") is True and "name" in i
                      and "color" in i and "kind" in i]
        approx_items = [i for i in aw if i.get("approximate") is True]
        check("three-tier: at least one FULL item (LOS entity, Far)",
              len(full_items) >= 1, json.dumps(aw))
        check("three-tier: at least one APPROXIMATE item (no LOS, Cloak)",
              len(approx_items) >= 1, json.dumps(aw))
        check("APPROXIMATE item(s) carry no identity (name/color/kind)",
              all("name" not in i and "color" not in i and "kind" not in i
                  for i in approx_items), json.dumps(approx_items))
    except Exception as exc:  # noqa: BLE001 — report, don't crash silently
        import traceback
        traceback.print_exc()
        check("script completed without exception", False, repr(exc))
    finally:
        for c in (al, gm):
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
