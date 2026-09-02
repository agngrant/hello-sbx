# LittleDungeons

**A real-time, grid-based shared tactical map for a tabletop / TTRPG session** — built
entirely on the **Python standard library** (no third-party packages) with a plain
static HTML/CSS/JS frontend.

One **Game Master (GM)** runs the session and up to **6 players** join. The GM
uploads a map as an **image**, LittleDungeons **detects walls and doorways** into a grid
automatically, and everyone sees one live, shared map. Each player moves their own
character; the GM can move *anything*. Every player gets a personal
**awareness overlay** (colored dots for friends / neutrals / enemies); the GM sees
everything, labeled.

> Contract & design docs live in [`PROJECT.md`](PROJECT.md) and
> [`docs/design/wireframes.md`](docs/design/wireframes.md). QA sign-off and the
> full bug log are in [`docs/qa/`](docs/qa/).

---

## Requirements

- **Python 3.10+** (developed and tested on **Python 3.14**).
- **No third-party packages.** Pure standard library — no `pip install`, no
  `requirements.txt`. Image decode (PNG/BMP), detection, pathfinding, and the
  WebSocket server are all hand-rolled.
- A **browser** (any modern one) for the frontend.

That's it. If `python3` works, LittleDungeons runs.

---

## How to run

```sh
cd agentteam   # repo root
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
    the GM may move player characters, NPCs, enemies, or their own.
  - **Ignore walls:** tick the *Ignore walls* toggle (GM-only) to move a token
    straight to a target, wall or no wall (a deliberate "teleport" exception).
  - **Paint:** switch the bottom-bar tool to *Floor / Wall / Door* and paint to fix
    any detection mistakes — corrections apply to everyone live.
  - **Teams:** set a selected entity's team to *party* / *neutral* / *hostile*
    (this drives the awareness colors).
  - **Create / delete tokens:** add `npc`, `enemy`, or `gm_character` entities
    (spawned on the last hovered tile) and delete any entity.
  - **Fog of war:** toggle on (off by default) for line-of-sight-aware visibility.

### The awareness overlay

The overlay is computed **per player** server-side and is a **radar that passes
through walls** (by default, with fog of war off you can see around corners).

- **GM** sees **every** entity with its true color, name label, and kind — no masking.
- **Players** see a **dot** for every *other* entity, plus their own **"YOU"**
  token. A player's own entity is excluded from their dots (they already see it).

Colors (by the *target's* team): **green = friend** (party) · **white = neutral**
(player or NPC) · **red = enemy** (hostile). An explicit per-entity color override,
if set, wins.

For color-blind accessibility each color is **also encoded by shape**:
**triangle = friend**, **circle = neutral**, **square = enemy** (see the legend).

When **fog of war** is on, a player only sees entities with clear line of sight from
their own token (Bresenham LOS, blocked by walls); the **GM is never fogged**, and
entities that were seen while visible in the light stay visible ("previously seen").

---

## Upload & automatic wall/doorway detection

The GM uploads a **PNG or BMP** (the file picker offers only `.png, .bmp`). The
browser reads the file to base64 and `POST`s it as JSON to `/api/maps/upload`
(`{"name", "image_b64", "cols"?, "rows"?, "dark_is_wall"?}`) — a **JSON + base64**
body, *not* multipart, so no file-upload library is needed.

The pure-stdlib detection pipeline (in `app/detection.py` + `app/imaging.py`) is:

1. **Decode** PNG/BMP (hand-rolled: PNG via `zlib`+`struct`, BMP via `struct`;
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

- **`http.server.ThreadingHTTPServer`** + `BaseHTTPRequestHandler` (one thread per
  connection — a good fit for 1 GM + 6 players) serves the static frontend, the REST
  API, and the `/ws` WebSocket upgrade.
- **Hand-rolled RFC 6455 WebSocket** in `app/ws.py` (handshake + frame codec, server
  and a test-client side). No `websockets`/`fastapi`/`uvicorn`.
- **Authoritative `GameSession`** (`app/session.py`) owns the live state — the grid,
  the entities, the connected players, and per-connection bookkeeping — and enforces
  all permissions under a single lock. The server is the source of truth; the client
  only sends intents.
- **Per-viewer state snapshots:** on any mutation the server recomputes a state for
  *each* viewer (awareness colors, labels, and fog filtering differ per viewer) and
  broadcasts that viewer's snapshot. The same `Grid` object is shared with the REST
  registry so paints from either path hit the same grid.
- **A\* pathfinding** (`app/pathfinding.py`), 8-direction, **no wall-corner
  cutting** (a diagonal step is legal only when both of its elbow cells are
  walkable). Consistent octile heuristic, deterministic tie-break. `has_line_of_sight`
  (Bresenham) powers fog of war.
- **Image decode + detection** are pure Python (`app/imaging.py`, `app/detection.py`).

### File map

```
app/
├── main.py        # http.server app: REST routes, /ws upgrade, static mount, argparse
├── models.py      # dataclasses (Grid, Entity, Player, Session) + to_dict/from_dict
├── grid.py        # cell helpers + built-in sample dungeon + geometry
├── ws.py          # RFC 6455 handshake + frame codec (server + test client)
├── imaging.py     # PNG/BMP decode + PNG encode + gray/resize/Otsu/median (pure stdlib)
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
├── wsclient.py            # stdlib WebSocket client used by the tests
├── js/harness.js          # Node harness that runs the REAL app.js under a stub DOM
├── test_grid.py test_imaging.py test_detection.py test_pathfinding.py
├── test_awareness.py test_session.py test_api.py test_ws.py test_frontend.py
run.sh                     # convenience launcher (prefers ./.venv/bin/python)
```

---

## Tests

```sh
cd agentteam   # repo root
python -m unittest discover -s tests -t .
```

- **199 tests**, all green, pure stdlib `unittest` (no pytest). Coverage spans grid
  helpers, the PNG/BMP imager, detection, pathfinding (including no-corner-cut cases),
  the awareness rules, the session (permissions, fog, reconnects, `use_map`), the REST
  API (live `http.client`/`http.server`), and the WebSocket protocol.
- The **frontend is actually executed**, not just text-matched: `test_frontend.py`
  drives the real `app/static/app.js` in Node via `tests/js/harness.js` (a controllable
  timer + a stub DOM/WebSocket). (If Node isn't installed those tests are the ones that
  skip; everything else runs.)
- **Live end-to-end proof:** `python scripts/e2e_proof.py` starts its own server and
  walks a real GM + 2 players through joining, moving, a wall-override, fog-of-war
  per-player visibility, a live map swap (`use_map`), and permission rejections —
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
- **Fog of war is optional and off by default.** When off, the awareness radar passes
  through walls.
- **No authentication / no cross-host networking hardening.** It binds to a
  localhost host by default and trusts everyone who can reach it; it's a local
  tabletop tool, not a hardened public service.

---

*Pure standard library. 199 tests green. QA signed off — see
[`docs/qa/qa-signoff.md`](docs/qa/qa-signoff.md).*
