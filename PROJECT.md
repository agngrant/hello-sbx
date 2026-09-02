# LittleDungeons — Multi-User Tactical Map for TTRPGs

**Version:** 2.0 — **stdlib pivot** (team contract). The v1 FastAPI/uvicorn/
pillow/numpy/pytest stack is **not installable** in this sandbox (egress proxy
blocks `files.pythonhosted.org`; all mirrors 403; no cached wheels; empty
system python). We therefore build on the **Python standard library only** with
a **static frontend**. All product requirements, the data model, the REST API,
the WebSocket protocol, and the awareness/movement rules are **unchanged**.

**Owner:** PM (team lead). Contributors: designer, awesome_engineer, qa.
**Workspace:** `agentteam/` (the repository root)

This document is the single source of truth. If a requirement is ambiguous,
prefer the interpretation stated here and note the assumption in your
deliverable.

---

## 1. Product goal

A real-time, grid-based shared map for a tabletop/role-playing game session.
A **Game Master (GM)** runs the session and up to **6 players** join. The GM
uploads a map as an **image**, the system **detects walls and doorways** into a
grid, and everyone sees a live, shared map. Each player moves their own
character; the GM can move anything. Each player has a personal **awareness
overlay** (colored dots for friends/NPCs/enemies); the GM sees everything.

### Hard requirements (must all be met)
1. Exactly 1 GM and up to 6 players, simultaneously, in one session.
2. Grid-based map. Cells are `floor`, `wall`, or `doorway`.
3. **Walls block movement** — a character cannot walk through a wall cell
   (diagonals must not "cut" around a wall corner).
4. **Doorways / gaps** are walkable.
5. **GM override**: the GM may move a character through walls ("ignore walls")
   as a deliberate exception.
6. **Awareness overlay** is unique per player and **passes through walls**
   (radar-style). Colors: **green** = friend, **white** = neutral (player or
   NPC), **red** = enemy. The **GM sees all** entities with true colors +
   labels, no masking.
7. A player may **only move their own character**. The GM may move any entity.
8. Maps are **uploaded as image files**; the system **detects and marks walls
   and doorways** automatically, with a manual paint/edit fallback for the GM.

### Soft / nice-to-have (do not block delivery)
- Fog-of-war toggle (line-of-sight based), off by default.
- Smooth animated movement along the path.
- Session save/load to disk.

---

## 2. Tech stack (FINAL — standard library only)

- **Runtime:** Python 3.14 (`.venv/bin/python` at `<repo-root>/.venv`).
  **No third-party packages. No pip installs. No requirements.txt needed.**
- **HTTP server:** `http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler`.
  Serves the static frontend and the REST API, and upgrades the `/ws` route to
  a WebSocket.
- **WebSocket:** hand-rolled **RFC 6455** server in `app/ws.py` (handshake +
  frame codec). One handler thread per connection (ThreadingHTTPServer), which
  fits the 1-GM + 6-player scale.
- **Image decode (PIL-free):** `zlib` + `struct` in `app/imaging.py`. Decodes
  non-interlaced **PNG** (grayscale/RGB/RGBA/palette, 8-bit; 16-bit → high byte)
  and **BMP** (24/32-bit BI_RGB), plus a PNG *encoder* (used by tests to make
  synthetic fixtures) and grayscale/resize/Otsu/median operations in pure
  Python.
- **Detection:** `app/detection.py` (pure Python) — gray → resize → Otsu
  threshold → 3×3 majority → classify walls → doorway heuristic.
- **Pathfinding:** `app/pathfinding.py` — A* (8-dir, no wall-corner cutting),
  `has_line_of_sight` (Bresenham) for optional fog.
- **Models:** `app/models.py` — **`dataclasses`** + plain dicts for JSON. Field
  names and shapes are identical to v1 so the frontend and tests match.
- **Frontend:** plain static HTML/CSS/JS (no build step) in `app/static/`.
  Canvas-based map rendering. Responsive.
- **Tests:** stdlib **`unittest`** (no pytest). `tests/` with a stdlib WS
  client helper. Run with `python -m unittest discover -s tests -t .`.

### How to run
```
cd agentteam   # repo root
.venv/bin/python -m app.main --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
# run tests:
.venv/bin/python -m unittest discover -s tests -t .
```

> **Iteration 1 gate:** no install step is needed. The gate is instead:
> `python -m app.main` boots, `GET /health` → `{"status":"ok"}`, and
> `python -m unittest discover` is green. Because there are zero deps this gate
> is trivially satisfiable offline.

---

## 3. Directory layout

```
agentteam/
├── PROJECT.md                # this file (contract v2)
├── README.md                 # how to run, features, limitations (final)
├── run.sh                    # convenience launcher
├── app/
│   ├── __init__.py
│   ├── main.py               # http.server app, REST routes, /ws upgrade, static
│   ├── models.py             # dataclasses (Grid, Entity, Player, Session) + JSON
│   ├── grid.py               # cell helpers + sample map + geometry
│   ├── ws.py                 # RFC6455 handshake + frame codec (server + test client)
│   ├── imaging.py            # PNG/BMP decode + PNG encode + gray/resize/otsu/median
│   ├── detection.py          # image -> grid wall/doorway detection
│   ├── pathfinding.py        # A* (8-dir, no corner cut), has_line_of_sight
│   ├── awareness.py          # per-player overlay color computation
│   ├── session.py            # GameSession: authoritative state, perms, broadcast
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
└── tests/
    ├── wsclient.py           # stdlib WebSocket client for tests
    ├── test_grid.py
    ├── test_imaging.py       # PNG encode->decode roundtrip, Otsu, resize, median
    ├── test_detection.py
    ├── test_pathfinding.py
    ├── test_awareness.py
    ├── test_api.py           # REST via http.client / HTTPServer on a real port
    └── test_ws.py            # websocket join/move/override/awareness
```

---

## 4. Data model (dataclasses, in `app/models.py`)

```python
CellType = "floor" | "wall" | "doorway"
Team     = "party" | "neutral" | "hostile"
Role     = "gm" | "player"
EntityKind = "player" | "npc" | "enemy" | "gm_character"

TEAM_COLORS = {"party": "green", "neutral": "white", "hostile": "red"}

@dataclass
class Grid:
    name: str
    width: int
    height: int
    cells: list[list[str]]          # cells[y][x] -> CellType
    image: str | None = None        # stored source image name (optional)
    def to_dict(self) -> dict: ...   # {"name","width","height","cells","image"}
    @classmethod
    def from_dict(cls, d: dict) -> "Grid": ...

@dataclass
class Entity:
    id: str
    name: str
    kind: EntityKind
    team: Team
    x: int
    y: int
    owner: str | None = None        # controlling player id; None = GM-controlled
    color: str | None = None        # explicit override; else derived from team
    def to_dict(self) -> dict: ...

@dataclass
class Player:
    id: str
    name: str
    role: Role
    entity_id: str | None = None    # character this player controls
    def to_dict(self) -> dict: ...

@dataclass
class Session:
    id: str
    grid: Grid
    entities: dict[str, Entity]
    players: dict[str, Player]
    fog: bool = False
    def to_dict(self) -> dict: ...  # full snapshot for broadcasts
```

Team → awareness color (base rule; explicit `color` wins if set):
`party` → **green** (friend) · `neutral` → **white** · `hostile` → **red** (enemy).

---

## 5. Awareness overlay (the core feature)

The overlay is **per player** and is a **radar that ignores walls** (spec:
"the awareness can pass through walls"). For a connected player `P`:

```
for each entity E (E != P's own entity):
    if P.role == "gm":
        show E with true color + label + all info
    else:
        relation = relation_of(P, E)
        color = green if relation=="friend"
                else white if relation=="neutral"
                else red
        show a dot at (E.x, E.y) with that color
```

`relation_of(P, E)`:
- `E.team == "party" and P.team == "party"` → `friend`
- `E.team == "neutral"` → `neutral`
- `E.team == "hostile"` → `enemy`
- (A party member marked hostile by the GM then shows red to the party.)
- The **GM always sees everything** (no masking, no color reduction).

**Line of sight** (`has_line_of_sight`) is implemented (Bresenham over the
grid, blocked by `wall`) and powers the *optional* fog-of-war toggle. It is
**off by default** so the radar passes through walls per spec. When fog is on,
a player only sees entities with clear LOS (or previously seen); the GM is
never fogged. This satisfies "walls block sight" (fog mode) while keeping
"awareness passes through walls" (default radar mode).

---

## 6. Movement, permissions, pathfinding

- **Client request:** player clicks a destination cell → sends
  `{type:"move", entity_id, x, y, override:false}`.
- **Server validation (authoritative):**
  - Sender must be the `owner` of `entity_id`, or the GM.
  - A **player** may only send `override:false` and only for their own entity.
  - `override:true` is **GM-only** (moves through walls / "ignore walls").
  - Destination in-bounds.
- **Pathfinding (`pathfinding.py`):** A* on 8 directions. A diagonal step is
  forbidden if *either* of the two adjacent orthogonal cells is a wall
  (prevents squeezing through a corner). `wall` is blocked; `floor` and
  `doorway` are walkable. If a path exists, the entity moves along it (server
  stores/records the path; client animates). If no path and no override →
  reject with `{type:"error", message:"no route — wall in the way"}`. With
  `override:true`, the entity is moved directly to the target (walls ignored).
- **GM may move any entity** (players, NPCs, enemies) and may create/delete
  entities and set their `team`.

---

## 7. Image upload & wall/doorway detection

> **No-multipart change (v2):** the sandbox has no `python-multipart`, so
> uploads are **JSON with a base64-encoded image** (not a multipart form). The
> frontend reads the file with `FileReader` → base64 → `POST` JSON. This is a
> deliberate, documented deviation to stay dependency-free; it costs us
> multipart streaming only (fine for a small map image).

Flow:
1. `POST /api/maps/upload` (JSON): `{"name": str, "image_b64": str,
   "cols"?: int, "rows"?: int, "dark_is_wall"?: bool}`. Decode base64 → bytes.
   (Optional `image_name` field carried for display.)
2. Decode with `app/imaging.py` (PNG or BMP) → pixel buffer.
3. Grayscale → resize to target grid (default: scale so `max(width,height) <=
   60`, preserving aspect; or use provided `cols/rows`) via nearest neighbor.
4. Binarize with **Otsu** threshold (pure-Python histogram; fallback 127).
   Dark pixels = walls when `dark_is_wall` (default true: dark ink walls on
   light paper).
5. 3×3 majority (median) filter to remove noise.
6. Classify each cell: `wall` if binarized-dark else `floor`.
7. **Doorway heuristic:** a `floor` cell is a `doorway` if it is a *gap in a
   wall*: walls on at least two **opposite** orthogonal neighbors (up+down) or
   (left+right). (Secondary: a floor cell with 3+ orthogonal wall neighbors.)
8. Ensure some `floor` exists — if >~60% of cells became walls, auto-invert and
   note it in the response.
9. Register the grid as a new map; return `map_id`, `width`, `height`, the grid
   (for preview), and a small data-URL thumbnail (PNG-encoded) for the UI.

**Manual fallback:** the GM can paint cells (`floor`/`wall`/`doorway`) in the
UI after upload to fix detection. Detection is a *suggestion*; the GM is the
editor of record.

---

## 8. REST API (JSON)

| Method | Path | Auth/role | Description |
|--------|------|-----------|-------------|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/api/maps` | any | `{"maps":[{"id","name","width","height"}]}` |
| GET | `/api/maps/{id}` | any | `{"id","name","width","height","cells","entities":[],"players":[]}` |
| POST | `/api/maps/upload` | GM | JSON body §7 → create map, return map + thumbnail |
| POST | `/api/maps/{id}/paint` | GM | `{"x","y","cell_type"}` set one cell |

**Role assignment:** the first client to send `role:"gm"` over WebSocket (or
the first client when no GM exists) becomes the GM. Server enforces max 1 GM
and max 6 players; further joins get `{type:"error", message:"session full"}`.

---

## 9. WebSocket protocol

Endpoint: `ws://host/ws` (upgrade from `GET /ws` with `Upgrade: websocket`).

**Client → server** (JSON text frames):
- `{type:"join", name, role?}` — join; server assigns id + a starting entity if
  a player.
- `{type:"request_state"}` — ask for full state.
- `{type:"move", entity_id, x, y, override?}` — request movement.
- `{type:"place", entity_id, x, y}` — GM direct place.
- `{type:"create_entity", name, kind, team, x, y}` — GM.
- `{type:"delete_entity", entity_id}` — GM.
- `{type:"set_team", entity_id, team}` — GM.
- `{type:"paint", x, y, cell_type}` — GM edit grid.
- `{type:"set_fog", on}` — GM toggle fog of war.

**Server → client** (JSON text frames):
- `{type:"welcome", you:{id,name,role,entity_id}, map, entities, players, fog}`
- `{type:"state", map, entities, players, fog}` — full snapshot broadcast to
  everyone on any mutation (small game → snapshot is simplest + testable).
- `{type:"path", entity_id, path:[{x,y}...]}` — for client animation.
- `{type:"error", message}` — sent to the offending client.

The server is **authoritative**: it never trusts a client's claimed position;
it recomputes and broadcasts.

### WebSocket server notes (`app/ws.py`)
- Handshake: read the `Sec-WebSocket-Key`, respond `101` with
  `Sec-WebSocket-Accept = base64(sha1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))`.
- Frame parse: FIN/opcode byte; length 7-bit / 126→16-bit / 127→64-bit;
  client→server frames are **masked** (MASK bit 0x80, XOR with 4-byte mask);
  server→client frames are **unmasked**. Opcodes: `0x1` text, `0x8` close,
  `0x9` ping, `0xA` pong. Send a pong for pings; handle close.
- After a session handler returns, close the socket and
  `self.close_connection = True`.

---

## 10. Iteration plan (small, verifiable steps)

Each iteration ends with a **definition of done**: code present,
`python -m unittest discover -s tests -t .` green, and a short note in the
iteration's deliverable.

- **Iter 0 — Design.** (designer) ✅ Done: `docs/design/wireframes.md`.
- **Iter 1 — Scaffold.** (engineer) stdlib `http.server` app: `models.py`,
  `grid.py` (sample map), `main.py` with `/health`, `GET /api/maps[/{id}]`,
  static mount, `/ws` **stub** (handshake + echo/welcome placeholder), static
  shell. **Gate:** boots offline, `/health` ok, sample map listed, unit tests
  green.
- **Iter 2 — Grid + render.** (engineer) Frontend renders the sample grid on
  canvas (floor/wall/doorway), legend, lobby. **DoD:** renders sample map.
- **Iter 3 — Imaging + detection.** (engineer) `imaging.py` (PNG encode/decode
  + BMP + Otsu/resize/median), `detection.py`, `POST /api/maps/upload`.
  **DoD:** a synthetic PNG (drawn with our own encoder) yields correct walls +
  a detected doorway; tests pass.
- **Iter 4 — Entities + movement.** (engineer) Entities, A* pathfinding,
  movement perms, doorway walking, GM override. **DoD:** pathfinding unit
  tests (blocked by wall, via doorway, override-through-wall) pass.
- **Iter 5 — Multiplayer + awareness.** (engineer) real `ws.py` + `session.py`:
  join/GM enforcement (1 GM + 6 players), per-player awareness overlay
  (green/white/red), GM sees all, broadcast state, path + error messages.
  **DoD:** WS tests for join/move/override/awareness colors pass.
- **QA pass 1** after Iter 1–3. **QA pass 2** after Iter 4–5. QA files
  `docs/qa/BUG-NNN.md` per issue; engineer fixes and re-runs tests.
- **Iter 6 — Polish + docs.** (engineer+designer) Responsive UI, README with
  run steps + feature list + limitations, `run.sh`, final `unittest` green.

---

## 11. Definition of done (overall)

- `python -m unittest discover -s tests -t .` fully green.
- `python -m app.main` starts the app offline; `http://127.0.0.1:8000` serves
  the UI; upload → detect → move → awareness works end to end.
- README explains roles, how to start (stdlib only), controls, and limitations
  (e.g. PNG/BMP only, no-interlace PNG; JSON-base64 upload; no zoom/pan in v1).
- All 8 hard requirements in §1 are demonstrably met (QA signs off).
