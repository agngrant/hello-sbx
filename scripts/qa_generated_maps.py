#!/usr/bin/env python3
"""QA live verification: the generated-maps feature (spec §9, C1–C10 live).

Run against a LIVE server on 127.0.0.1:8000:

    ./.venv/bin/python scripts/qa_generated_maps.py

Every check prints ✓/✗; the script exits non-zero if ANY check fails.

What it verifies (all independently re-implemented here — no test helpers
are imported, so a bug in the test helpers cannot hide an app bug):

  1.  GET /api/maps baseline
  2.  POST /api/maps/generate → 200, exact key set, dimensions, PNG
      thumbnail, registration (count +1), GET detail (image: null)
  3.  C1 dimensions · C3 cell vocabulary · C2 all-wall border
  4.  C5 connectivity: BFS over floor+doorway from the first floor cell
      reaches ALL floor cells
  5.  C6 sparseness: flood-fill rooms (doorways as walls) →
      doors == rooms − 1, doors >= 3
  6.  C7 detour: a wall cell with floor on both opposite sides whose two
      sides belong to DIFFERENT room components (door-less adjacency)
  7.  C4 doorway geometry: every doorway has walls on both opposite sides
  8.  I6 reproducibility: same (name,size,seed) → identical cells (id gets
      -2); different seed → different cells
  9.  C9 validation: the four 400 cases with the EXACT §5.1 strings
  10. C11-style pathfind: find_path from the first floor cell to the
      BFS-farthest floor cell; every step orthogonal-legal (the corner-cut
      rule is enforced by app.pathfinding.is_valid_step itself)

Stdlib only (+ the app package itself for the A* check, as the spec's e2e
proof does).
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import sys
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.models import Grid  # noqa: E402
from app.pathfinding import find_path, is_valid_step  # noqa: E402

HOST = os.environ.get("QA_HOST", "127.0.0.1")
PORT = int(os.environ.get("QA_PORT", "8000"))

PASS = "\u2713"
FAIL = "\u2717"
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    ok = bool(cond)
    print(f"  {PASS if ok else FAIL} {label}"
          + (f"   -> {detail}" if (detail and not ok) else ""))
    if not ok:
        failures.append(label)
    return ok


# ---------------------------------------------------------------------------
# REST helpers (http.client, same style as tests/test_api.py)
# ---------------------------------------------------------------------------


def request(method: str, path: str, obj=None, raw_body: bytes | None = None):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=30)
    try:
        body = raw_body if raw_body is not None else (
            json.dumps(obj).encode("utf-8") if obj is not None else None
        )
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, data
    finally:
        conn.close()


def post_json(path: str, obj):
    status, hdrs, data = request("POST", path, obj=obj)
    return status, hdrs, json.loads(data)


def get_json(path: str):
    status, hdrs, data = request("GET", path)
    return status, hdrs, json.loads(data)


# ---------------------------------------------------------------------------
# Independent grid analysis (re-implemented; no imports from tests/)
# ---------------------------------------------------------------------------

WALKABLE = ("floor", "doorway")


def first_floor(cells, w, h):
    for y in range(h):
        for x in range(w):
            if cells[y][x] == "floor":
                return (x, y)
    return None


def bfs_reach(cells, w, h, start):
    """4-dir BFS over walkable cells (floor + doorway) from ``start``."""
    seen = {start}
    q = deque([start])
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen
                    and cells[ny][nx] in WALKABLE):
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


def bfs_dist(cells, w, h, start):
    """BFS distances from ``start`` over walkable cells."""
    dist = {start: 0}
    q = deque([start])
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in dist
                    and cells[ny][nx] in WALKABLE):
                dist[(nx, ny)] = dist[(cx, cy)] + 1
                q.append((nx, ny))
    return dist


def room_components(cells, w, h):
    """4-dir flood fill over 'floor' ONLY (doorways treated as walls).

    Returns {comp_id: [cell, ...]}.
    """
    comps: dict[int, list] = {}
    seen: set = set()
    for y in range(h):
        for x in range(w):
            if cells[y][x] != "floor" or (x, y) in seen:
                continue
            cid = len(comps)
            comps[cid] = []
            seen.add((x, y))
            stack = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                comps[cid].append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (0 <= nx < w and 0 <= ny < h
                            and (nx, ny) not in seen
                            and cells[ny][nx] == "floor"):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
    return comps


def room_id_map(cells, w, h):
    rid = {}
    for cid, comp in room_components(cells, w, h).items():
        for cell in comp:
            rid[cell] = cid
    return rid


def count_doors(cells) -> int:
    return sum(row.count("doorway") for row in cells)


def border_all_wall(cells, w, h) -> bool:
    for x in range(w):
        if cells[0][x] != "wall" or cells[h - 1][x] != "wall":
            return False
    for y in range(h):
        if cells[y][0] != "wall" or cells[y][w - 1] != "wall":
            return False
    return True


def all_doorways_geometrically_solid(cells, w, h):
    """Spec C4 / I7 geometry for every doorway cell:

    (a) walls on BOTH opposite sides (up+down OR left+right), and
    (b) walkable (floor/doorway) on the OTHER pair of opposite sides —
        i.e. a 1-cell gap in a solid 1-cell wall, crossable in two
        orthogonal A* steps.

    Returns (ok, bad_cells).
    """
    bad = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if cells[y][x] != "doorway":
                continue
            up = cells[y - 1][x]
            down = cells[y + 1][x]
            left = cells[y][x - 1]
            right = cells[y][x + 1]
            vertical = up == "wall" and down == "wall"   # line runs up/down
            horizontal = left == "wall" and right == "wall"  # line runs left/right
            ok = ((vertical and left in WALKABLE and right in WALKABLE)
                  or (horizontal and up in WALKABLE and down in WALKABLE))
            if not ok:
                bad.append((x, y))
    return not bad, bad


def find_doorless_wall_adjacency(cells, w, h):
    """A wall cell with floor on BOTH opposite sides whose two floor sides
    belong to different room components — a door-less room adjacency.

    Returns the (wall_cell, room_a, room_b) or None.
    """
    rid = room_id_map(cells, w, h)
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if cells[y][x] != "wall":
                continue
            if cells[y][x - 1] == "floor" and cells[y][x + 1] == "floor":
                a, b = rid.get((x - 1, y)), rid.get((x + 1, y))
                if a is not None and b is not None and a != b:
                    return (x, y), a, b
            if cells[y - 1][x] == "floor" and cells[y + 1][x] == "floor":
                a, b = rid.get((x, y - 1)), rid.get((x, y + 1))
                if a is not None and b is not None and a != b:
                    return (x, y), a, b
    return None


def main() -> int:
    print(f"QA live verification: generated maps @ http://{HOST}:{PORT}")

    # -- 1. baseline ------------------------------------------------------
    status, hdrs, body = get_json("/api/maps")
    check("GET /api/maps → 200", status == 200, f"status {status}")
    check("GET /api/maps has 'maps' list", isinstance(body.get("maps"), list))
    baseline_ids = {m["id"] for m in body["maps"]}
    print(f"      baseline map count: {len(baseline_ids)}")

    # -- 2. generate ------------------------------------------------------
    status, hdrs, data = post_json("/api/maps/generate", {
        "name": "QA Crypt", "cols": 24, "rows": 16, "seed": 7,
    })
    check("POST /api/maps/generate → 200", status == 200, f"status {status} {data}")
    check("exact key set {id,name,width,height,cells,thumbnail}",
          set(data.keys()) == {"id", "name", "width", "height", "cells", "thumbnail"},
          f"keys={sorted(data.keys())}")
    check("Cache-Control: no-store on the response",
          hdrs.get("cache-control") == "no-store",
          f"cache-control={hdrs.get('cache-control')!r}")
    check("width == 24", data.get("width") == 24, str(data.get("width")))
    check("height == 16", data.get("height") == 16, str(data.get("height")))
    check("name == 'QA Crypt'", data.get("name") == "QA Crypt", str(data.get("name")))
    map_id = data.get("id")
    fresh = "qa-crypt" not in baseline_ids
    if fresh:
        check("id is the name slug 'qa-crypt'", map_id == "qa-crypt", f"id={map_id!r}")
    else:
        print("      (note: 'qa-crypt' already existed on this server — "
              "re-run mode; the -2 suffix check below is skipped)")
    cells = data.get("cells")
    check("cells is 16 rows × 24 cols",
          isinstance(cells, list) and len(cells) == 16
          and all(isinstance(r, list) and len(r) == 24 for r in cells),
          f"type={type(cells).__name__}")
    thumb = data.get("thumbnail", "")
    check("thumbnail is a PNG data URL",
          isinstance(thumb, str) and thumb.startswith("data:image/png;base64,"))
    try:
        png = base64.b64decode(thumb.split(",", 1)[1])
        check("thumbnail decodes to a PNG", png.startswith(b"\x89PNG"),
              repr(png[:8]))
    except Exception as exc:
        check("thumbnail decodes to a PNG", False, str(exc))

    status, _, body = get_json("/api/maps")
    ids = {m["id"] for m in body["maps"]}
    check("map id appears in GET /api/maps (count +1)",
          status == 200 and map_id in ids and len(ids) == len(baseline_ids) + 1,
          f"new={sorted(ids - baseline_ids)}")

    status, _, detail = get_json(f"/api/maps/{map_id}")
    check("GET /api/maps/{id} → 200 with the same cells",
          status == 200 and detail.get("cells") == cells,
          f"status {status}")
    check("GET /api/maps/{id} carries image: null",
          detail.get("image") is None, f"image={detail.get('image')!r}")

    # -- 3. structural invariants on the returned cells -------------------
    w, h = 24, 16
    check("C1: dimensions exact (re-asserted)",
          len(cells) == h and all(len(r) == w for r in cells))
    flat = [c for row in cells for c in row]
    check("C3: cell vocabulary ⊆ {floor,wall,doorway} and ≥1 floor",
          all(c in WALKABLE or c == "wall" for c in flat) and "floor" in flat)
    check("C2: outer border is all wall", border_all_wall(cells, w, h))

    # -- 4. C5 connectivity ------------------------------------------------
    start = first_floor(cells, w, h)
    check("there is at least one floor cell", start is not None)
    if start is not None:
        all_floor = {(x, y) for y in range(h) for x in range(w)
                     if cells[y][x] == "floor"}
        reached_floor = {c for c in bfs_reach(cells, w, h, start)
                         if cells[c[1]][c[0]] == "floor"}
        check("C5: BFS (floor+doorway) from first floor cell reaches ALL "
              f"floor cells ({len(all_floor)} floor, {len(reached_floor)} reached)",
              reached_floor == all_floor,
              f"unreached={sorted(all_floor - reached_floor)[:10]}")

    # -- 5. C6 sparseness ---------------------------------------------------
    rooms = room_components(cells, w, h)
    doors = count_doors(cells)
    check(f"C6: doors == rooms − 1 ({doors} doors, {len(rooms)} rooms)",
          doors == len(rooms) - 1)
    check("C6: doors >= 3", doors >= 3, f"doors={doors}")

    # -- 6. C7 detour property ---------------------------------------------
    adj = find_doorless_wall_adjacency(cells, w, h)
    check("C7: a door-less wall edge exists between two distinct rooms",
          adj is not None,
          f"wall cell {adj[0]} (rooms {adj[1]} / {adj[2]})" if adj else "none found")

    # -- 7. C4/I7 doorway geometry ------------------------------------------
    solid, bad = all_doorways_geometrically_solid(cells, w, h)
    check(f"C4/I7: all {doors} doorways are a 1-cell gap in a solid 1-cell "
          "wall (walls on both opposite sides, walkable on the other pair)",
          solid, f"bad={bad}")

    # -- 8. I6 seed reproducibility ------------------------------------------
    status, _, dup = post_json("/api/maps/generate", {
        "name": "QA Crypt", "cols": 24, "rows": 16, "seed": 7,
    })
    check("same (name,size,seed) again → 200 with a NEW unique id",
          status == 200 and dup.get("id") not in (baseline_ids | {map_id}),
          f"status {status} id={dup.get('id')!r}")
    if fresh and map_id == "qa-crypt":
        check("repeat id gets the -2 suffix ('qa-crypt-2')",
              dup.get("id") == "qa-crypt-2", f"id={dup.get('id')!r}")
    check("same seed → IDENTICAL cells", dup.get("cells") == cells)

    status, _, other = post_json("/api/maps/generate", {
        "name": "QA Crypt", "cols": 24, "rows": 16, "seed": 8,
    })
    check("different seed (8) → 200 and DIFFERENT cells",
          status == 200 and other.get("cells") != cells,
          f"status {status} id={other.get('id')!r}")

    # -- 9. C9 validation (exact §5.1 strings) --------------------------------
    bad_cases = [
        ({"name": "x", "cols": 7, "rows": 10},
         "'cols' must be an integer in 8-60"),
        ({"name": "x", "cols": 10, "rows": 61},
         "'rows' must be an integer in 8-60"),
        ({"name": "", "cols": 10, "rows": 10},
         "'name' must be a non-empty string"),
        ({"name": "x", "cols": 10, "rows": 10, "seed": "abc"},
         "'seed' must be an integer"),
    ]
    for obj, expected in bad_cases:
        status, _, body = post_json("/api/maps/generate", obj)
        check(f"400 for {obj} → {expected!r}",
              status == 400 and body == {"error": expected},
              f"status {status} body={body!r}")

    # -- 10. C11-style A* corner-to-corner pathfind ---------------------------
    dist = {}
    far = None
    if start is not None:
        dist = bfs_dist(cells, w, h, start)
        # Target: the BFS-farthest FLOOR cell (distance measured over
        # floor+doorway, per the task). Deterministic tie-break.
        far_cells = [c for c in dist if cells[c[1]][c[0]] == "floor"]
        far = (max(far_cells, key=lambda c: (dist[c], -c[1], -c[0]))
               if far_cells else None)
        check("there is at least one floor cell to pathfind toward",
              bool(far_cells))
    if start is not None and far is not None:
        grid = Grid.from_dict({"name": "QA Crypt", "width": w, "height": h,
                               "cells": cells})
        path = find_path(grid, start, far)
        check(f"C11: A* found a route {start} → {far} "
              f"(BFS distance {dist.get(far)} steps)", path is not None)
        if path:
            check("path length >= 2", len(path) >= 2, f"len={len(path)}")
            bad_steps = [
                (i, path[i], path[i + 1])
                for i in range(len(path) - 1)
                if not is_valid_step(grid, path[i], path[i + 1])
            ]
            check("every path step is a legal move (no wall-corner cuts)",
                  not bad_steps, f"bad_steps={bad_steps[:5]}")

    # -- summary ---------------------------------------------------------------
    print()
    if failures:
        print(f"{FAIL} {len(failures)} check(s) FAILED: {failures}")
        return 1
    print(f"{PASS} ALL LIVE QA CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConnectionRefusedError, OSError) as exc:
        print(f"{FAIL} cannot reach the server at http://{HOST}:{PORT}: {exc}")
        print("   Start it first (see the QA report's command list).")
        raise SystemExit(2)
