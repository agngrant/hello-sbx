#!/usr/bin/env python3
"""QA live end-to-end verification of the per-player awareness radius +
awareness-ring feature (docs/design/awareness-ring.md §8) over a REAL server.

This is an INDEPENDENT wire-level check (QA's unique value vs the in-repo
unit tests). It drives the real uvicorn server + the stdlib WS client
(``tests.wsclient.WSClient``) through the acceptance scenario:

  GM + Alice (player) + one GM-created NPC on the sample dungeon, placed so
  the NPC has NO line of sight to Alice at Chebyshev distance 2:

      Alice (4,1)  [floor, left of the col-5 wall]
      wall  (5,1)  [col-5 interior wall, sample dungeon row 1]
      NPC   (6,1)  [floor, right of the col-5 wall]

  The straight Alice→NPC line crosses the col-5 wall at (5,1) → NO LOS,
  Chebyshev distance 2 → an APPROXIMATE contact at the default radius 4.

Checks (one line each, ✓/✗; exits non-zero on any failure):

  C  default players[] entry carries awareness_radius 4
  C  Alice's awareness = EXACTLY ONE approximate item (gray "?" surrogate)
  C  GM always sees the NPC in FULL (never distance/LOS filtered)
  C  set_awareness 0  → radius 0, Alice's awareness EMPTY (no approx)
  C  set_awareness 10 → radius 10, approximate item REAPPEARS
  C  invalid values 21 / -1 / "abc" / true → "awareness must be an integer 0–20"
      and the radius is UNCHANGED in the next state
  C  non-GM (Alice) set_awareness → "not allowed"
  C  GM set_awareness on the GM-created NPC (owner None) → "not a player token"
  C  GM set_awareness on an unknown entity id → "no such entity"
  C  LOS is radius-INDEPENDENT: NPC moved to (2,1) (same open row, clear LOS)
      → Alice sees it FULL (name+kind+label, not approximate) even at radius 0

The script does NOT modify any repo state beyond this file. It assumes the
server is already up on 127.0.0.1:8000 (see the run instructions below).

Run (from the repo root):

    # 1. start the server (if not already running)
    cd /Users/agrant3/agentteam
    PYTHONUNBUFFERED=1 nohup ./.venv/bin/python -m app.main \
        --host 127.0.0.1 --port 8000 >> .ld_server.log 2>&1 &
    echo $! > .ld_server.pid

    # 2. sanity: the server answers /health
    curl http://127.0.0.1:8000/health        # expect {"status": "ok"}

    # 3. run the QA live check
    ./.venv/bin/python scripts/qa_awareness_radius.py

    # 4. stop the server + confirm a clean shutdown
    kill "$(cat .ld_server.pid)"
    tail -n 5 .ld_server.log   # expect "Shutting down" + "Finished server process"
"""

from __future__ import annotations

import os
import sys
import time

# Make the repo root importable (app.*, tests.*) regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.wsclient import WSClient  # noqa: E402

HOST, PORT = "127.0.0.1", 8000
PASS, FAIL = "\u2713", "\u2717"
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    ok = bool(cond)
    print(f"  {PASS if ok else FAIL} {label}"
          + (f"   -> {detail}" if (detail and not ok) else ""))
    if not ok:
        failures.append(label)
    return ok


def wait_for(client: WSClient, pred, label: str = "", limit: int = 80):
    """Receive frames until one satisfies ``pred`` (returns it).

    Skips (but does not discard) intervening frames so we never block on a
    queued broadcast; the matching frame is returned for assertion."""
    for _ in range(limit):
        m = client.recv_json()
        if pred(m):
            return m
    raise AssertionError(f"timeout: no frame matching {label!r} within {limit}")


def main() -> int:
    # A unique session id per run so repeated runs never share state with a
    # previous QA run (sessions are in-memory, keyed by the ?session= param).
    sid = f"qa-awr-{int(time.time())}"
    gm = WSClient(HOST, PORT, path=f"/ws?session={sid}", timeout=10).connect()
    alice = WSClient(HOST, PORT, path=f"/ws?session={sid}", timeout=10).connect()

    try:
        # ---- joins ----------------------------------------------------------
        wg = gm.join("Gamer", "gm")
        wa = alice.join("Alice", "player")
        alice_pid = wa["you"]["id"]
        alice_ent = wa["you"]["entity_id"]
        check("GM + Alice join (welcome, role=gm / role=player)",
              wg["type"] == "welcome" and wg["you"]["role"] == "gm"
              and wa["type"] == "welcome" and wa["you"]["role"] == "player")

        # Welcome payload: every players[] entry carries awareness_radius.
        def alice_radius(m):
            p = next((p for p in m["players"] if p["id"] == alice_pid), None)
            return p.get("awareness_radius") if p else None

        def all_have_radius(m):
            return all(isinstance(p.get("awareness_radius"), int)
                       for p in m["players"])

        check("DEFAULT: welcome players[] entries all carry awareness_radius",
              all_have_radius(wg) and all_have_radius(wa),
              f"gm players={wg['players']} alice players={wa['players']}")
        check("DEFAULT: Alice's welcome awareness_radius == 4",
              alice_radius(wa) == 4, f"got {alice_radius(wa)}")

        # ---- GM parks Alice + creates the NPC (no LOS, cheb 2) --------------
        gm.send_json({"type": "place", "entity_id": alice_ent, "x": 4, "y": 1})
        gm.send_json({"type": "create_entity", "name": "Grom",
                      "kind": "npc", "team": "neutral", "x": 6, "y": 1})
        st_gm = wait_for(gm, lambda m: m["type"] == "state"
                         and any(e["name"] == "Grom" for e in m["entities"])
                         and any(e["id"] == alice_ent
                                 and (e["x"], e["y"]) == (4, 1)
                                 for e in m["entities"]),
                         "GM state: Alice@4,1 + Grom@6,1")
        npc_ent = next(e["id"] for e in st_gm["entities"] if e["name"] == "Grom")

        # GM is never distance/LOS filtered: sees the no-LOS npc in FULL.
        gm_npc = next((i for i in st_gm["awareness"]
                       if i["entity_id"] == npc_ent), None)
        check("GM (no radius): sees the no-LOS NPC in FULL "
              "(label+name+kind, not approximate)",
              gm_npc is not None and gm_npc.get("label") is True
              and gm_npc.get("name") == "Grom" and "kind" in gm_npc
              and not gm_npc.get("approximate"),
              f"gm npc item={gm_npc}")

        # Alice's awareness: EXACTLY ONE approximate item (the only other
        # token on the map is this no-LOS NPC, cheb 2 <= radius 4).
        st_al = wait_for(alice, lambda m: m["type"] == "state"
                         and any(i.get("approximate") for i in m["awareness"]),
                         "Alice state: one approximate item")
        approx = [i for i in st_al["awareness"] if i.get("approximate")]
        check("DEFAULT: Alice's awareness has EXACTLY ONE approximate item",
              len(st_al["awareness"]) == 1 and len(approx) == 1
              and alice_radius(st_al) == 4,
              f"items={st_al['awareness']} radius={alice_radius(st_al)}")
        a0 = approx[0]
        check("approx item is the gray '?' surrogate (no identity)",
              a0.get("approximate") is True and a0.get("label") is False
              and str(a0.get("entity_id", "")).startswith("<approx-")
              and "name" not in a0 and "color" not in a0 and "kind" not in a0,
              f"item={a0}")
        # Block ORIGIN of the 2x2 quantized block: (6,1)//2 == (3,0).
        check("approx item quantized to block origin (3,0) for NPC at (6,1)",
              (a0.get("x"), a0.get("y")) == (3, 0), f"item={a0}")

        # ---- GM sets radius 0 → no approximate tier -------------------------
        gm.send_json({"type": "set_awareness", "entity_id": alice_ent, "value": 0})
        # One broadcast: the GM and Alice each get one state with radius 0.
        st_gm0 = wait_for(gm, lambda m: m["type"] == "state"
                          and alice_radius(m) == 0, "GM state: Alice radius 0")
        st_al0 = wait_for(alice, lambda m: m["type"] == "state"
                          and alice_radius(m) == 0,
                          "Alice state: radius 0")
        check("RADIUS 0: players[] entry shows awareness_radius 0",
              alice_radius(st_al0) == 0, f"radius={alice_radius(st_al0)}")
        check("RADIUS 0: Alice's awareness is EMPTY (no approximate items)",
              st_al0["awareness"] == [], f"items={st_al0['awareness']}")
        # GM still unfiltered while Alice's radius is 0 (reuses st_gm0 above).
        g0 = next((i for i in st_gm0["awareness"] if i["entity_id"] == npc_ent),
                  None)
        check("RADIUS 0: GM STILL sees the NPC in FULL (GM never filtered)",
              g0 is not None and g0.get("label") is True
              and not g0.get("approximate"), f"gm npc item={g0}")

        # ---- GM sets radius 10 → approximate item reappears -----------------
        gm.send_json({"type": "set_awareness", "entity_id": alice_ent, "value": 10})
        st_al10 = wait_for(alice, lambda m: m["type"] == "state"
                           and alice_radius(m) == 10
                           and any(i.get("approximate") for i in m["awareness"]),
                           "Alice state: radius 10 + approx")
        check("RADIUS 10: players[] entry shows awareness_radius 10",
              alice_radius(st_al10) == 10, f"radius={alice_radius(st_al10)}")
        check("RADIUS 10: approximate item REAPPEARS (cheb 2 <= 10)",
              len(st_al10["awareness"]) == 1
              and any(i.get("approximate") for i in st_al10["awareness"]),
              f"items={st_al10['awareness']}")

        # ---- invalid values → exact error, radius unchanged -----------------
        for bad in (21, -1, "abc", True):
            gm.send_json({"type": "set_awareness", "entity_id": alice_ent,
                          "value": bad})
            err = wait_for(gm, lambda m: m["type"] == "error"
                           and m.get("message") == "awareness must be an integer 0–20",
                           f"invalid-value error for {bad!r}", limit=20)
            check(f"INVALID value {bad!r} -> "
                  f"'awareness must be an integer 0–20'",
                  err["type"] == "error"
                  and err["message"] == "awareness must be an integer 0–20",
                  f"got {err}")
            # No broadcast on a rejected set; request the next state to prove
            # the radius is UNCHANGED (still 10 from the last good value).
            gm.send_json({"type": "request_state"})
            st = gm.recv_json()
            check(f"INVALID value {bad!r} -> radius UNCHANGED in next state",
                  st["type"] == "state" and alice_radius(st) == 10,
                  f"state radius={alice_radius(st) if st.get('type') == 'state' else st}")

        # ---- non-GM (Alice) set_awareness → not allowed ---------------------
        alice.send_json({"type": "set_awareness", "entity_id": alice_ent,
                         "value": 9})
        err = wait_for(alice, lambda m: m["type"] == "error"
                       and m.get("message") == "not allowed",
                       "Alice not-allowed error", limit=20)
        check("NON-GM (Alice) set_awareness -> 'not allowed'",
              err == {"type": "error", "message": "not allowed"}, f"got {err}")

        # ---- GM set_awareness on an NPC (owner None) → not a player token ---
        gm.send_json({"type": "set_awareness", "entity_id": npc_ent, "value": 3})
        err = wait_for(gm, lambda m: m["type"] == "error"
                       and m.get("message") == "not a player token",
                       "npc not-a-player-token error", limit=20)
        check("GM set_awareness on GM-created NPC (owner None) "
              "-> 'not a player token'",
              err == {"type": "error", "message": "not a player token"}, f"got {err}")

        # ---- GM set_awareness on an unknown entity → no such entity ---------
        gm.send_json({"type": "set_awareness", "entity_id": "nope-404", "value": 5})
        err = wait_for(gm, lambda m: m["type"] == "error"
                       and m.get("message") == "no such entity",
                       "no-such-entity error", limit=20)
        check("GM set_awareness on unknown entity id -> 'no such entity'",
              err == {"type": "error", "message": "no such entity"}, f"got {err}")

        # ---- LOS is radius-INDEPENDENT --------------------------------------
        # First restore Alice's radius to 0 (the invalid-value loop above
        # left it at 10). The whole point: at radius 0 a clear-LOS contact
        # must STILL show FULL — the radius never grants/gates sight.
        gm.send_json({"type": "set_awareness", "entity_id": alice_ent, "value": 0})
        wait_for(gm, lambda m: m["type"] == "state" and alice_radius(m) == 0,
                 "GM state: Alice radius back to 0", limit=40)
        # Move the NPC to (2,1): same open row (y=1), Alice's side of the wall.
        # Alice (4,1) -> NPC (2,1): horizontal line through (3,1) (floor) →
        # CLEAR line of sight. Even at radius 0, LOS must show the entity FULL.
        gm.send_json({"type": "place", "entity_id": npc_ent, "x": 2, "y": 1})
        st_gm_los = wait_for(gm, lambda m: m["type"] == "state"
                             and any(e["id"] == npc_ent
                                     and (e["x"], e["y"]) == (2, 1)
                                     for e in m["entities"]),
                             "GM state: NPC moved to (2,1)")
        glos = next((i for i in st_gm_los["awareness"]
                     if i["entity_id"] == npc_ent), None)
        check("GM sees the relocated NPC at (2,1) in FULL",
              glos is not None and glos.get("label") is True
              and (glos.get("x"), glos.get("y")) == (2, 1), f"gm npc item={glos}")
        # Alice: even with radius still 0, a clear-LOS contact is FULL.
        st_al_los = wait_for(alice, lambda m: m["type"] == "state"
                             and any(i.get("entity_id") == npc_ent
                                     and not i.get("approximate")
                                     for i in m["awareness"]),
                             "Alice state: NPC FULL via LOS")
        alos = next((i for i in st_al_los["awareness"]
                     if i.get("entity_id") == npc_ent), None)
        check("LOS is RADIUS-INDEPENDENT: at radius "
              f"{alice_radius(st_al_los)}, clear-LOS NPC shows FULL "
              "(name+kind+label, not approximate)",
              alos is not None and alos.get("label") is True
              and alos.get("name") == "Grom" and "kind" in alos
              and not alos.get("approximate")
              and (alos.get("x"), alos.get("y")) == (2, 1)
              and alice_radius(st_al_los) == 0,
              f"item={alos} radius={alice_radius(st_al_los)}")

    finally:
        for c in (gm, alice):
            try:
                c.close()
            except Exception:
                pass

    print()
    if failures:
        print(f"{FAIL} {len(failures)} check(s) FAILED: {failures}")
        return 1
    print(f"{PASS} ALL LIVE E2E CHECKS PASSED "
          f"(session {sid}: GM + Alice + 1 NPC, per-player awareness radius)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
