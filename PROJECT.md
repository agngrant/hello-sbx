# LittleDungeons — Multi-User Tactical Map for TTRPGs

**Version:** 3.0 — **package stack** (FastAPI / uvicorn / websockets / Pillow /
pytest), adopted per the recommendation in
`docs/reviews/simplification-review.md`. v2.0 was a **stdlib pivot** (team
contract) because the egress proxy blocked `files.pythonhosted.org`. The
sandbox now allows the pinned packages in `requirements.txt` (probed
installable), so the hand-rolled `http.server` + raw RFC 6455 server +
`zlib`/`struct` image codec are replaced by their package equivalents. All
product requirements, the data model, the REST API, the WebSocket protocol,
and the awareness/movement rules are **unchanged** — only the plumbing
underneath them changed. The external surface (routes, JSON error shapes,
`/ws?session=<id>` protocol, static files, startup banner) is frozen and
pinned by the test suite + `scripts/e2e_proof.py`.

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
6. **Awareness overlay** is unique per player and is a **three-tier visibility
   model**: a player gets **full information** (color + name + kind + label) about
   an entity they can **see** (line of sight), an **approximate (coarse, identity-
   free) location** for a sight-blocked entity within 4 squares, and **nothing**
   beyond that. Colors: **green** = friend, **white** = neutral (player or NPC),
   **red** = enemy. The **GM sees all** entities with true colors + labels, no
   masking, no distance/LOS filter.
7. A player may **only move their own character**. The GM may move any entity.
8. Maps are **uploaded as image files**; the system **detects and marks walls
   and doorways** automatically, with a manual paint/edit fallback for the GM.

### Soft / nice-to-have (do not block delivery)
- Smooth animated movement along the path.
- Session save/load to disk.

---

## 2. Tech stack (FINAL — pinned packages, see `requirements.txt`)

- **Runtime:** Python 3.14 (`.venv/bin/python` at `<repo-root>/.venv`).
  Install the pinned packages once: `.venv/bin/pip install -r requirements.txt`
  (fastapi, uvicorn, websockets, pillow, pydantic, pytest, pytest-timeout).
- **HTTP server / ASGI:** **FastAPI** app in `app/server.py` served by
  **uvicorn** (`run_server` in `app/server.py`). Serves the static frontend
  and the REST API and speaks RFC 6455 on `/ws` natively via the
  **websockets** library. A `ThreadingHTTPServer`-shaped adapter
  (`app.server.ThreadingHTTPServer`) wraps a uvicorn Server on a pre-bound
  socket so the existing tests / `scripts/e2e_proof.py` boot code works
  unchanged.
- **WebSocket:** uvicorn + **websockets** (hand-rolled server codec deleted;
  `app/ws.py` now holds ONLY the client-side test helpers the raw-socket
  test client uses).
- **Image decode:** **Pillow** in `app/imaging.py` (`Image.open` / `Image.save`).
  Decodes non-interlaced **PNG** and **BMP** (24/32-bit) and rejects the same
  inputs as the old codec (non-PNG/BMP, interlaced PNG, other BMP bit
  depths). The *algorithmic* pixel ops (BT.601 `to_gray`, nearest `resize`,
  Otsu, 3×3 majority) are kept in `app/imaging.py` because the app pins their
  exact behavior in tests (Pillow's `MedianFilter` differs on tie/OOB rules).
- **Detection:** `app/detection.py` — gray → resize → Otsu threshold → 3×3
  majority → classify walls → doorway heuristic (unchanged).
- **Pathfinding:** `app/pathfinding.py` — A* (8-dir, no wall-corner cutting),
  `has_line_of_sight` (Bresenham) for optional fog. (Kept as-is per the
  review: it's domain logic and no package wins here.)
- **Models:** `app/models.py` — **`dataclasses`** + plain dicts for JSON.
  Field names and shapes are identical to v1 so the frontend and tests match.
  (The review's Pydantic row was intentionally NOT adopted — manual
  `to_dict`/`from_dict` stay, by contract.)
- **Frontend:** plain static HTML/CSS/JS (no build step) in `app/static/`.
  Canvas-based map rendering. Responsive.
- **Tests:** **pytest** (primary; `pytest.ini`, per-test `timeout = 30` via
  pytest-timeout) — the suite is written in `unittest` style so the stdlib
  runner still works too. `tests/` with a raw-socket WS client helper.

### How to run
```
cd agentteam   # repo root
.venv/bin/pip install -r requirements.txt   # once
.venv/bin/python -m app.main --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
# run tests:
.venv/bin/python -m pytest                    # primary
.venv/bin/python -m unittest discover -s tests -t .   # also supported
```

> **Gate:** `python -m app.main` boots, `GET /health` → `{"status":"ok"}`,
> and the test suite is green under **both** runners, plus
> `python scripts/e2e_proof.py` is all-✓.

---

## 3. Directory layout

```
agentteam/
├── PROJECT.md                # this file (contract v3)
├── README.md                 # how to run, features, limitations (final)
├── requirements.txt          # pinned deps (fastapi, uvicorn, websockets, pillow, ...)
├── pytest.ini                # pytest config (timeout=30, thread method)
├── run.sh                    # convenience launcher
├── app/
│   ├── __init__.py
│   ├── main.py               # CLI entry + in-memory state (registry, sessions, id helpers)
│   ├── server.py             # FastAPI app: REST routes, /ws, static mount; uvicorn runner + ThreadingHTTPServer-shaped test adapter
│   ├── models.py             # dataclasses (Grid, Entity, Player, Session) + JSON
│   ├── grid.py               # cell helpers + sample map + geometry
│   ├── ws.py                 # RFC6455 CLIENT helpers only (test client; server side deleted)
│   ├── imaging.py            # Pillow-backed PNG/BMP decode + encode + gray/resize/otsu/median
│   ├── detection.py          # image -> grid wall/doorway detection
│   ├── pathfinding.py        # A* (8-dir, no corner cut), has_line_of_sight
│   ├── awareness.py          # per-player overlay color computation
│   ├── session.py            # GameSession: authoritative state, perms, broadcast
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
└── tests/
    ├── wsclient.py           # raw-socket WebSocket client for tests
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

The overlay is **per viewer**, computed server-side on every mutation. It is a
**three-tier visibility model** for players (anchored at the player's own
token `O`); the **GM is exempt** and always sees everything, unmasked.

```
for each entity E (E != the viewer's own token):
    if viewer.role == "gm":
        show E with true color + name + kind + label   # always, no filter
    else:  # player — anchored at own token O
        if has_line_of_sight(grid, O, E):            # FULL
            show E with exact position, color (green/white/red or override),
                  name + kind + label   # full information
        elif chebyshev(O, E) <= APPROX_RADIUS (4):   # APPROXIMATE
            show a generic "unseen contact" marker at a COARSE position
                  (block origin (E.x//2, E.y//2)) — NO color, NO name, NO kind,
                  NO real id (a non-revealing "<approx-N>" surrogate)
        else:                                        # INVISIBLE
            show nothing (E is absent from the awareness list)
```

**Tiers (player):**
- **FULL** — direct line of sight to `E`: exact position + color + **name, kind,
  and label** (identical shape to the GM item). Color is the explicit `entity.color`
  override, else the team color.
- **APPROXIMATE** — **no** line of sight, but `E` within the player's
  **awareness range**: a coarse **2×2-block-quantized** position (`x//2, y//2`) with **no identity at all** (no color/name/kind/team/real
  id). The client renders a muted gray "?" marker.
- **INVISIBLE** — no line of sight **and** beyond the awareness range: `E`
  does not appear in the player's awareness list at all.

The APPROXIMATE tier's range is **per-player** (`Player.awareness_radius`,
int, default `APPROX_RADIUS = 4`); the GM sets it 0–20 per player via the
`set_awareness` WS message (GM Tools), which the client draws as a dashed
awareness ring around each player token.

`relation_of` / `overlay_color` (used by the FULL tier and the GM) — judged by the
**target's** team:
- `E.team == "party"` → `friend` → **green**
- `E.team == "neutral"` → `neutral` → **white**
- `E.team == "hostile"` → `enemy` → **red**
- (An explicit `entity.color` override wins. A party member marked hostile by the
  GM shows red, because the target's team is what matters.)
- The **own token** is always excluded from awareness (rendered via `you_entity`).
- A player whose own token was deleted has no anchor and sees nothing.
- The **GM always sees everything** (no masking, no color reduction, no distance or
  LOS filter).

**Line of sight** (`has_line_of_sight`) is implemented (Bresenham over the
grid, blocked by `wall`) and drives the player **three-tier** awareness
model (FULL on LOS / APPROXIMATE within 4 squares without LOS / INVISIBLE
beyond). It is blocked by any wall cell strictly on the line, AND by a
diagonal step that squeezes between two wall corners (both orthogonal "elbow"
cells walls) — the same no-corner-cut rule as movement. Endpoints never
block (``a == b`` → True). The ``fog`` flag is retained in the payloads for
wire compatibility but no longer gates visibility. The GM is never fogged or
filtered.

> **Map tiers (explored map, additive):** the *grid* is now also tiered per
> player — seen (line of sight, full detail) / explored (greyed) / hidden
> (undrawn) — but the **entity awareness model above is unchanged**.

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

**Procedural generation (no image):** the New map view also offers a
**Generate map** tab (`POST /api/maps/generate`, `app/generation.py`):
`{"name", "cols" (8–60), "rows" (8–60), "seed"?}` → a BSP dungeon of that
exact size — rooms (≥ 3×3 floor rectangles) separated by solid 1-cell walls
with one doorway per split, so the room–door graph is a **tree** (connected,
no loops: `doors = rooms − 1`), every floor cell reachable from any other,
and some geometrically adjacent rooms deliberately **door-less** (detours).
Same `cols`/`rows`/`seed` ⇒ identical grid (reproducible); no seed ⇒ fresh
random. The response has the same key set as upload
(`{"id","name","width","height","cells","thumbnail"}`) and the map is
registered like an upload, so the frontend preview → `use_map` flow and GM
paint work unchanged (generation is a suggestion; the GM is the editor of
record). Full spec: `docs/design/generated-maps.md`.

---

## 8. REST API (JSON)

| Method | Path | Auth/role | Description |
|--------|------|-----------|-------------|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/api/maps` | any | `{"maps":[{"id","name","width","height"}]}` |
| GET | `/api/maps/{id}` | any | `{"id","name","width","height","cells","entities":[],"players":[]}` |
| POST | `/api/maps/upload` | GM | JSON body §7 → create map, return map + thumbnail |
| POST | `/api/maps/generate` | GM | `{"name","cols","rows","seed"?}` → procedurally generated map (same response shape as upload, §7) |
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
- `{type:"set_awareness", entity_id, value}` — GM. Set a player token's
  awareness radius (`value`: integer 0–20; errors: `"not a player token"`,
  `"awareness must be an integer 0–20"`).
- `{type:"paint", x, y, cell_type}` — GM edit grid.
- `{type:"set_fog", on}` — GM toggle fog of war.

**Server → client** (JSON text frames):
- `{type:"welcome", you:{id,name,role,entity_id}, map, entities, players, fog}`
- `{type:"state", map, entities, players, fog}` — full snapshot broadcast to
  everyone on any mutation (small game → snapshot is simplest + testable).
- **`visibility`** (additive, explored map) — an extra field on player
  `welcome`/`state` payloads only: a `height`×`width` matrix of `S`/`E`/`H`
  tier rows. **Absent for the GM** (no key) — the GM's payload is unchanged.
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

- `python -m pytest` **and** `python -m unittest discover -s tests -t .` fully green
  (the suite is unittest-style, so both runners pass).
- `python -m app.main` starts the FastAPI/uvicorn server; `http://127.0.0.1:8000`
  serves the UI; upload → detect → move → awareness works end to end
  (`python scripts/e2e_proof.py` is all-✓).
- README explains roles, how to start (pinned packages via `requirements.txt`),
  controls, and limitations (e.g. PNG/BMP only, no-interlace PNG; JSON-base64 upload;
  no zoom/pan in v1).
- All 8 hard requirements in §1 are demonstrably met (QA signs off).
