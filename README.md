# LittleDungeons

**A real-time, grid-based shared tactical map for a tabletop / TTRPG session** — a
**FastAPI / uvicorn / websockets / Pillow** server (pinned in `requirements.txt`) with a
plain static HTML/CSS/JS frontend.

> **v3.0:** the v2.0 stdlib-only build has been replaced with the pinned package
> stack recommended in [`docs/reviews/simplification-review.md`](docs/reviews/simplification-review.md)
> (Pillow for the image codec, FastAPI+uvicorn+websockets for HTTP/WebSocket, pytest
> for the runner). **All product requirements, the data model, the REST API, the
> WebSocket protocol, and the awareness/movement rules are unchanged** — only the
> plumbing changed, and the external surface (routes, JSON error shapes, the
> `/ws?session=<id>` protocol, static files, startup banner) is frozen and pinned by
> the test suite + `scripts/e2e_proof.py`.

One **Game Master (GM)** runs the session and up to **6 players** join. The GM
uploads a map as an **image**, LittleDungeons **detects walls and doorways** into a grid
automatically, and everyone sees one live, shared map. Each player moves their own
character; the GM can move *anything*. **The GM has no token on the map** — the GM is
a pure controller/spectator who creates, moves, and deletes the other tokens and
sees everything, unmasked. Every player gets a personal **awareness overlay**:
**full** info on entities they can see, an **approximate** (gray "?") marker for
nearby entities hidden by walls, and **nothing** beyond that. The GM sees
everything, labeled.

> Contract & design docs live in [`PROJECT.md`](PROJECT.md) and
> [`docs/design/wireframes.md`](docs/design/wireframes.md). QA sign-off and the
> full bug log are in [`docs/qa/`](docs/qa/).

---

## Requirements

- **Python 3.10+** (developed and tested on **Python 3.14**).
- **Pinned third-party packages** (in `requirements.txt`): `fastapi`, `uvicorn`,
  `websockets`, `pillow`, `pydantic`, `pytest`, `pytest-timeout`. Install once with
  `pip install -r requirements.txt`. Image decode (Pillow), the HTTP/WebSocket stack
  (FastAPI + uvicorn + websockets), and the test runner (pytest) are now package-backed;
  detection and pathfinding remain in-repo domain logic.
- A **browser** (any modern one) for the frontend.

That's it. If `python3` works and the pinned packages are installed, LittleDungeons runs.

---

## How to run

```sh
cd agentteam   # repo root
pip install -r requirements.txt   # once
python -m app.main --host 127.0.0.1 --port 8000
# or the convenience launcher:
./run.sh
```

Then open **http://127.0.0.1:8000** in a browser (same origin on every device that
can reach the host).

The launcher `run.sh` uses `./.venv/bin/python` if it exists, otherwise `python3`,
and binds `127.0.0.1:8000`.

### Joining a session

1. **First** client to open the lobby becomes the **GM** (the lobby note says so,
   and the server makes the first connection the GM automatically).
2. The GM uploads a map in the **Upload** view — the server detects walls and
   doorways and shows a side-by-side preview of the source, the detected grid, and
   a server thumbnail. (See *Upload & detection* below.)
3. **Players** then join as *Player* (up to 6). The server auto-joins them into the
   same live session; a fresh 7th non-GM / 2nd GM is rejected with "session full".
4. Everyone is now on the shared map.

### Playing

- **Players:** tap / click a tile to move your own character. If the path is blocked
  by a wall you'll be told "Walls block movement." Arrow keys nudge a selected token.
  Your character is the one with the blue **YOU** ring.
- **GM:** the GM is the editor and referee of record:
  - **Select any entity** (click a token or a sidebar row) and **move it anywhere** —
    the GM may move player characters, NPCs, and enemies.
  - **Ignore walls:** tick the *Ignore walls* toggle (GM-only) to move a token
    straight to a target, wall or no wall (a deliberate "teleport" exception).
  - **Paint:** switch the bottom-bar tool to *Floor / Wall / Door* and paint to fix
    any detection mistakes — corrections apply to everyone live.
  - **Teams:** set a selected entity's team to *party* / *neutral* / *hostile*
    (this drives the awareness colors).
  - **Create / delete tokens:** add `npc` or `enemy` tokens
    (spawned on the last hovered tile) and delete any entity. The GM itself
    has no token on the map — there is nothing to select for the GM.
  - **Fog of war:** the old fog-of-war toggle is retained on the wire for
    compatibility but no longer changes what players see — visibility is now
    always the line-of-sight + proximity model below.

### The awareness overlay

The overlay is computed **per viewer** server-side and is a **three-tier
visibility model** for players (anchored at the player's own token). It no longer
"passes through walls": a player only gets **full** information about an entity
they can actually **see**.

- **GM** sees **every** token with its true color, name label, and kind — no
  masking, no distance or line-of-sight filter (the GM has no token of its own to
  anchor sight to).
- **Players** see other entities in one of three states, based on **line of sight**
  (Bresenham, blocked by walls — diagonal sight cannot cut a wall corner) and
  **distance** (Chebyshev, "squares"):
  - **Full (in sight):** a token with its true color, **name, kind, and label** —
    the full picture.
  - **Approximate (sight blocked, within 4 squares):** a muted gray **"?"** marker at
    a *coarse* (2×2-block) position — **no color, name, or kind** (just "something
    is roughly here").
  - **Invisible (sight blocked, beyond 4 squares):** not shown at all.

A player's own entity is rendered as their **"YOU"** token and is excluded from the
overlay. A player whose token was deleted has nothing to anchor sight to and sees
nothing.

Colors (by the *target's* team, on full/visible contacts): **green = friend**
(party) · **white = neutral** (player or NPC) · **red = enemy** (hostile). An
explicit per-entity color override, if set, wins.

For color-blind accessibility each full contact is **also encoded by shape**:
**triangle = friend**, **circle = neutral**, **square = enemy** (see the legend).

---

## Upload & automatic wall/doorway detection

The GM uploads a **PNG or BMP** (the file picker offers only `.png, .bmp`). The
browser reads the file to base64 and `POST`s it as JSON to `/api/maps/upload`
(`{"name", "image_b64", "cols"?, "rows"?, "dark_is_wall"?}`) — a **JSON + base64**
body, *not* multipart, so no file-upload library is needed.

The detection pipeline (in `app/detection.py` + `app/imaging.py`) is:

1. **Decode** PNG/BMP via **Pillow** (`Image.open`, PNG or BMP; 24/32-bit BMP;
   grayscale/RGB/RGBA/palette, 8-bit; 16-bit → high byte).
2. **Grayscale** the image.
3. **Resize** to the target grid (given `cols`/`rows`, or auto-scale so the longest
   edge ≤ 60, min edge 4, aspect preserved).
4. **Otsu threshold** to split dark/light.
5. **3×3 median** (majority) filter to denoise the binary wall/floor mask.
6. **Classify** each cell as `wall` (dark, when *dark-is-walls*) or `floor`.
7. **Doorway = a floor gap between two opposite walls** — a floor cell that has
   walls on up+*and* down, *or* left+*and* right, becomes `doorway`.
8. **Auto-invert** if > 60% of cells came out as walls (light walls on a dark
   background) — flips wall↔floor and re-derives doorways.

Detection is a **suggestion**: the GM stays the editor of record and paints
corrections through the UI.

**Format constraints:** PNG and **BMP only** (JPEG/WebP are not decoded and are not
offered by the picker). **Interlaced PNG is not supported** — the decoder
deliberately rejects `interlace != 0`. There is no lossy-compression decoding at all.

---

## Architecture overview

- **FastAPI + uvicorn** serves the static frontend, the REST API, and the `/ws`
  WebSocket upgrade (RFC 6455 natively via the `websockets` library). A
  `ThreadingHTTPServer`-shaped adapter (`app/server.py`) wraps a uvicorn Server on a
  pre-bound socket so the existing tests / `scripts/e2e_proof.py` boot code works
  unchanged.
- **RFC 6455** is handled by the `websockets` library (the old hand-rolled server codec
  is gone). `app/ws.py` now holds **only** the client-side test helpers used by the
  raw-socket test client.
- **Authoritative `GameSession`** (`app/session.py`) owns the live state — the grid,
  the entities, the connected players, and per-connection bookkeeping — and enforces
  all permissions under a single lock. The server is the source of truth; the client
  only sends intents.
- **Per-viewer state snapshots:** on any mutation the server recomputes a state for
  *each* viewer (awareness tiers, colors, and labels differ per viewer) and
  broadcasts that viewer's snapshot. The same `Grid` object is shared with the REST
  registry so paints from either path hit the same grid.
- **A\* pathfinding** (`app/pathfinding.py`), 8-direction, **no wall-corner
  cutting** (a diagonal step is legal only when both of its elbow cells are
  walkable). Consistent octile heuristic, deterministic tie-break. `has_line_of_sight`
  (Bresenham, no corner-cut) powers the three-tier awareness visibility.
- **Image decode + detection:** Pillow-backed decode (`app/imaging.py`) + pure-Python
  detection (`app/detection.py`). The algorithmic pixel ops (BT.601 gray, nearest
  resize, Otsu, 3×3 majority) stay in-repo because the app pins their exact behavior in
  tests.

### File map

```
app/
├── main.py        # CLI entry + in-memory state (maps registry, sessions, id helpers)
├── server.py      # FastAPI app: REST routes, /ws, static mount; uvicorn runner + test adapter
├── models.py      # dataclasses (Grid, Entity, Player, Session) + to_dict/from_dict
├── grid.py        # cell helpers + built-in sample dungeon + geometry
├── ws.py          # RFC 6455 CLIENT helpers only (raw-socket test client; server side deleted)
├── imaging.py     # Pillow PNG/BMP decode + encode + gray/resize/Otsu/median
├── detection.py   # image -> grid: Otsu -> median -> walls -> doorway -> auto-invert
├── pathfinding.py # A* (8-dir, no corner cut), has_line_of_sight
├── awareness.py   # per-player overlay: colors, labels, relation rules
├── session.py     # GameSession: authoritative state, permissions, broadcast
└── static/
    ├── index.html # lobby / upload / map views + controls (no build step)
    ├── app.js     # WebSocket client, state-driven render, awareness, GM tools
    └── style.css
scripts/
└── e2e_proof.py   # live-server end-to-end proof (GM + 2 players over the real WS)
tests/
├── wsclient.py            # raw-socket WebSocket client used by the tests
├── js/harness.js          # Node harness that runs the REAL app.js under a stub DOM
├── test_grid.py test_imaging.py test_detection.py test_pathfinding.py
├── test_awareness.py test_session.py test_api.py test_ws.py test_frontend.py
run.sh                     # convenience launcher (prefers ./.venv/bin/python)
```

---

## Tests

```sh
cd agentteam   # repo root
python -m pytest                    # primary runner
python -m unittest discover -s tests -t .   # also supported (suite is unittest-style)
```

- **The full test suite** is green under **both** runners — **pytest** (primary;
  `pytest.ini` sets a per-test `timeout = 30` via pytest-timeout, replacing the old
  hand-rolled thread watchdogs) and stdlib `unittest` (compat). Coverage spans grid
  helpers, the PNG/BMP imager, detection, pathfinding (including no-corner-cut cases),
  the awareness rules (three-tier player visibility), the session (permissions,
  visibility tiers, reconnects, `use_map`), the REST
  API (live `http.client` against a real port), and the WebSocket protocol.
- The **frontend is actually executed**, not just text-matched: `test_frontend.py`
  drives the real `app/static/app.js` in Node via `tests/js/harness.js` (a controllable
  timer + a stub DOM/WebSocket). (If Node isn't installed those tests are the ones that
  skip; everything else runs.)
- **Live end-to-end proof:** `python scripts/e2e_proof.py` starts its own server and
  walks a real GM + 2 players through joining, moving, a wall-override, three-tier
  visibility (full / approximate / invisible), a live map swap (`use_map`), and permission rejections —
  printing a ✓/✗ check per behaviour.

---

## Limitations (v1)

- **In-memory sessions.** Restarting the server starts fresh — there is **no
  save/load of sessions or maps to disk** yet (the sample dungeon is re-registered at
  startup; uploaded maps live in memory only).
- **No zoom / pan.** The map is fit to the viewport (cell size is computed from the
  canvas). Large grids simply use smaller cells.
- **Image decode is narrow.** No **interlaced PNG**, and no **JPEG / WebP** (and no
  16-bit multi-channel *test coverage* — 8-bit multi-channel is fully tested).
- **Visibility is a three-tier model, not a wall-passing radar.** A player sees
  full info only on entities with clear line of sight, an approximate (identity-free)
  marker within 4 squares when sight is blocked, and nothing beyond. The old
  fog-of-war toggle no longer affects visibility (kept on the wire for compat).
- **No authentication / no cross-host networking hardening.** It binds to a
  localhost host by default and trusts everyone who can reach it; it's a local
  tabletop tool, not a hardened public service.

---

*FastAPI / uvicorn / websockets / Pillow stack, pinned in `requirements.txt`.
Full test suite green under pytest and unittest; QA signed off — see
[`docs/qa/qa-signoff.md`](docs/qa/qa-signoff.md).*
