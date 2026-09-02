# LittleDungeons — Wireframes & UI Design Package (Iter 0)

**Status:** build-ready spec for `app/static/index.html`, `app/static/app.js`, `app/static/style.css`.
**Source of truth:** `PROJECT.md` §1, §4–§7, §9. Where this document and PROJECT.md
could diverge, PROJECT.md wins.

All screens are single-page (no routing); views are shown/hidden via a
`[hidden]` attribute on `#lobby-view`, `#upload-view`, `#map-view`.

---

## 0. Design decisions at a glance

1. **One page, three views:** Lobby (join) → Upload (GM, when no map exists) → Map (shared).
2. **Single `<canvas>`** renders grid + entities + awareness in one paint pass
   (no layered canvases). Fit-to-viewport sizing, devicePixelRatio-aware.
3. **Shape + color** encode ally/neutral/enemy (a11y): `▲` green friend, `●` white neutral,
   `■` red enemy. Players see shapes+colors **only** (no names); GM sees shapes+colors **and**
   name labels.
4. **Movement = two taps:** select entity → click destination cell. GM "Ignore walls" is a
   persistent toggle; a rejected move additionally offers a one-shot **"Move anyway"** action
   (both send `override:true`, GM-only).
5. **Paint tools live in the bottom control bar** (GM only), shared by the upload-preview
   and the live map.
6. **Fog-of-war toggle in the top bar** is GM-operated; players see it as a read-only state.
7. **No zoom/pan** in v1 — the grid always fits the available space (cell size is computed).

---

## 1. Design tokens (style.css)

```
--font:        system-ui, -apple-system, "Segoe UI", Roboto, sans-serif   (base 14px)
--chrome-bg:   #1c2130   (top bar, sidebar, control bar)
--panel-bg:    #252b3d   (cards/sections)
--text:        #eef0f6
--text-muted:  #9aa3b5
--accent:      #4dabf7   (buttons, links)
--own-ring:    #1971c2   (own character highlight)
--focus-ring:  #ffd43b   (keyboard focus outline, 2px)
--ok:          #2f9e44   --warn: #f59f00   --danger: #e03131

Floor cell:      #efe9dc   grid line: #d9d1bd (1px, DPR-scaled)
Wall cell:       fill #3b4252 + diagonal hatch lines #262b36 (25°), 1px inner border #20242f
Doorway cell:    floor base + amber #d97706 3px border + small arch glyph (⌐¬ shape) center
Path (animated): dashed line #4dabf7, 2px
Hover target:    ring #4dabf7 (valid) / #e03131 (blocked, shown only to GM with override on)
```

- **Spacing:** 4 / 8 / 12 / 16 / 24 px. **Radii:** 6px (controls), 8px (panels).
- **Type:** 14px UI, 12px dense lists, 16px titles, uppercase 11px letter-spaced section labels.
- **Contrast:** all text ≥ 4.5:1 on its background; the white awareness dot always gets a
  1.5px `#1c2130` stroke so it reads on both floor and wall.
- **Motion:** path animation 120 ms/cell, ease-linear; toasts slide up 150 ms.
  `@media (prefers-reduced-motion: reduce)` → path is instant, pulses removed.
- **Focus:** every interactive element gets a 2px `--focus-ring` outline on `:focus-visible`.
- **Touch targets:** ≥ 44 × 44 px below the 1024px breakpoint.

---

## 2. Screen flow

```
 load page
     │
     ▼
 #lobby-view ──── join ──┬── role=gm ──┬─ no map in session ──► #upload-view ──(start)──► #map-view
                         │              └─ map exists ─────────────────────────────────►  #map-view
                         └── role=player ┬─ no map ──► #map-view (empty state: "waiting for GM")
                                         └─ map    ──► #map-view (player mode)

 #map-view: GM may click "New map…" (GM tools) → #upload-view again; on start, return to #map-view.
```

---

## 3. Lobby / join screen (`#lobby-view`)

```
┌──────────────────────────────────────────────────────────────┐
│                                    LITTLEDUNGEONS            │
│                                                              │
│         ┌──────────────────────────────────────────┐         │
│         │ Join a session                           │         │
│         │                                          │         │
│         │ Name                                     │         │
│         │ [  Your name______________ ]  #join-name │         │
│         │                                          │         │
│         │   [  Join as GM  ]     [  Join as Player ]         │
│         │        #join-gm          #join-player    │         │
│         │                                          │         │
│         │ 1 GM + up to 6 players per session.      │ #lobby-note
│         │ First connection to a new session is the │
│         │ GM automatically.                        │
│         └──────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
```

- Name required (trim; max 24 chars; disabled button while empty).
- Both buttons send `{type:"join", name, role}`; if the server assigns a role
  different from what was asked (GM already exists → "player"), the welcome
  drives the UI — the UI trusts `welcome.you.role`, never its own request.
- On `welcome`: hide lobby, show `#map-view` or `#upload-view` per flow above.
  If `you.entity_id` exists → that entity is pre-selected (players).

---

## 4. Main map view — shared (`#map-view`, desktop ≥ 1024px)

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ LITTLEDUNGEONS ▸ The Gilded Crypt ● Connected  [ ] Fog of war  [☰]              │  #topbar
│  #session-title #map-name          #conn-status        #fog-toggle          #sidebar-
│                                                                     (GM only: [New map…]) toggle
├─────────────────────────────────────────────────────────────────────────────┬────────────┤
│ #canvas-wrap (flex:1)                                                       │ #sidebar   │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │ 320px     │
│ │  floor: light parchment + 1px grid   wall: dark hatched   doorway: ▣▣  │ │            │
│ │  ▓▓▓▓▓▓▓▓   ▓▓▓▓▓▓▓▓                 ┌─┐                         ▓▓▓  │ │ ┌────────┐ │
│ │  ▓              ▲                   │▄│  ▲  (dot=awareness)          │ │ │ AWARE- │ │
│ │  ▓  ●(3,4)  ◉ YOU (blue ring)       └─┘                         ▓▓▓▓▓▓│ │ │ NESS   │ │
│ │  ▓▓▓▓▓▓▓▓▓▓   ▓▓▓ ▓▓▓  ■ (5,6)      ▓▓▓▓▓▓  ┌──────────────┐        │ │ │ (GM:    │ │
│ │  ▓        (7,1)●                      ■       │  label text  │        │ │ │ labels)│ │
│ │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │ │ │ …rows… │ │
│ │                                         ┌─────────────────┐       │ │ │ …rows… │ │
│ │  #legend (bottom-left overlay):        │ #no-map (players)│       │ │ │ …rows… │ │
│ │  ▢ floor ▩ wall ▣ doorway  │  ▲●■ dots │  Waiting for the │       │ │ └────────┘ │ │
│ │  #coord-readout (bottom-right): (12,7) │  GM to upload a  │       │ │ ┌────────┐ │
│ │  #canvas-hint (top-center, transient)  │  map…            │       │ │ │ GM     │ │
│ │                                        └─────────────────┘       │ │ │ TOOLS  │ │
│ └─────────────────────────────────────────────────────────────────┘ │ │ │ (GM)   │ │
├─────────────────────────────────────────────────────────────────────┴────────────────┤ │
│ #controls-bar:  [Select][Floor][Wall][Door]   │  [ ] Ignore walls   │  hint text     │ │
│                #paint-group (GM only)         │  #override-toggle   │  #control-hint │ │
│                                               │  (GM only)                      │ │
└─────────────────────────────────────────────────────────────────────────────────────┴┴───┘
```

### 4.1 Canvas rendering (single `<canvas id="map-canvas">`)

Draw order:

1. Floor cells (`--floor` fill), 1px grid lines.
2. Wall cells (fill + hatch). Doorway cells (floor + amber border + arch glyph).
3. Active path (dashed `--accent` line through current `path` message).
4. Entity tokens (radius ≈ 0.38 × cell): circle, team-derived or `entity.color`
   fill, 2px white outline; 1 letter of name centered (10px).
   - **GM:** every entity full token + name label under it (11px, `--text` on a
     60%-opacity dark pill) + awareness shape marker overlaid (small, top-right of token).
   - **Player:** own entity = full token + `--own-ring` 3px ring + "YOU" pill;
     every **other** entity renders as an **awareness dot only** (§5) — no token art,
     no name.
5. Awareness dots (§5) on top.

- **Fit:** `cell = floor(min(availW / grid.width, availH / grid.height))`; canvas is
  centered in `#canvas-wrap`, DPR-scaled for crisp lines.
- **Hit-testing:** mouse → cell `(x, y)`; entity hit = token bounding circle.
- **Hover (desktop):** ring on the hovered cell (valid target `--accent`; if GM and
  target is a wall, `--danger` ring meaning "only with override").
- **Paint mode:** cursor becomes crosshair; hovering shows the tool's swatch
  (floor/wall/doorway) inside the hovered cell at 50% alpha.

### 4.2 Top bar states

| Element | States |
|---|---|
| `#conn-status` | `● Connected` (green) · `● Connecting…` (amber, pulsing) · `● Reconnecting (n)…` (amber) · `● Offline` (red). Dot 10px + 12px label. |
| `#fog-toggle` | Checkbox. **GM:** enabled, sends `{type:"set_fog", on}`. **Player:** disabled, reflects broadcast `fog` value, `title="GM controls fog of war"`. |
| `#sidebar-toggle` | Hidden ≥ 1024px. Opens/closes sidebar drawer (§8). |
| `[New map…]` | GM only, opens `#upload-view`. |
| `#map-name` | From `welcome.map.name`; truncates with ellipsis (min-width 0 flex). |

### 4.3 Control bar (`#controls-bar`)

```
│ [◉ Select] [▢ Floor] [▨ Wall] [▣ Door] │  [ ] Ignore walls  │  "Select a tile to move" │
│        #paint-group (GM only, .tool-btn[aria-pressed])      │      #override-toggle    │
```

- **Players:** only `#control-hint` visible (paint group + override hidden).
- **GM:** tools + override visible. Tools are a radio group: exactly one active
  (`aria-pressed="true"`, accent underline). `Select` is default.
- `#override-toggle` ("Ignore walls"): while **on**, all GM move requests carry
  `override:true`. Label stays literal; tooltip: "GM only. Move directly to the
  target, ignoring walls."
- `#control-hint` text by mode/state:
  - player, no selection: `Select your character, then a tile to move`
  - player, selected: `Pick a destination for {name}`
  - GM, select tool: `Click an entity to select it, then a tile` / `Pick a destination for {name}`
  - GM, paint tool: `Drag on the map to paint {floor|wall|doorway}`

### 4.4 Legend (`#legend`, small overlay bottom-left of canvas)

```
▢ floor   ▨ wall   ▣ doorway   │   ▲ friend   ● neutral   ■ enemy
```
Single row, 11px, on a 70%-opacity dark pill; wraps to two rows below 480px.

### 4.5 Movement interaction (click/tap)

| Actor | Click on… | Behavior |
|---|---|---|
| Player | own character | selects (already the only selectable; re-asserts selection) |
| Player | other entity | nothing (dot is not interactive) |
| Player | floor/doorway cell | `{type:"move", entity_id: own, x, y, override:false}` |
| Player | wall cell | nothing (cell not walkable); if target is a wall, show transient hint `Walls block movement` |
| GM | any entity token | select that entity |
| GM | floor/doorway with selection | `move` with `override` = `#override-toggle` state |
| GM | wall cell with selection | if override on → move with `override:true`; else toast `Walls block movement — enable "Ignore walls"` |
| GM | with paint tool active | paints the cell (§7.2) — no selection/move happens |

- **Server `path` message:** animate token along `path` at 120 ms/cell (instant if
  reduced-motion). While animating, ignore further move clicks for that entity.
- **Server `error` message:** toast (see 4.6). For `no route` errors addressed to a
  GM, the toast carries a one-shot action button **`[Move anyway]`** (id
  `#toast-action`) that re-sends the move with `override:true` — without changing
  the `#override-toggle` state. Players never get that button.
- **`#toasts`:** top-center of `#canvas-wrap`, max 3 stacked, auto-dismiss 4 s
  (error 6 s). `role="status"`, container `aria-live="polite"`.
  Variants: `.toast` (info/success, accent left border), `.toast-error` (danger
  border, optional action button).

---

## 5. Awareness overlay panel + on-map dots

### 5.1 Dot / token vocabulary (shared by canvas and list)

| Relation | Team | Color | Shape | On map (player view) | Label |
|---|---|---|---|---|---|
| friend | `party` | `#2f9e44` green | **triangle ▲** | small filled dot, dark 1.5px stroke | — (players) / name (GM) |
| neutral | `neutral` | `#f1f3f5` white | **circle ●** | same | — / name |
| enemy | `hostile` | `#e03131` red | **square ■** | same | — / name |

- Dot size ≈ 0.28 × cell, centered in the entity's cell; shape is clipped inside
  the circle. **Color is never the only cue** (shape + GM labels + list rows).
- Own character (player view): full token + blue ring + `YOU` pill — deliberately
  a different family (ring + letter) so it cannot be confused with a dot.
- GM view: awareness markers are overlaid on full tokens (top-right, 0.16 × cell)
  so "true identity" (name, kind, token) and "team color" are both visible.

### 5.2 Sidebar — awareness section (`#awareness`)

**Player view — "Awareness — {your name}"** (dots only, no names, per spec §5):

```
┌───────────────────────────────┐
│ AWARENESS — BRAM              │
│ ┌───────────────────────────┐ │
│ │  ▲                (3, 4)  │ │  friend  · no name (players)
│ │  ●                (7, 1)  │ │  neutral
│ │  ■                (5, 6)  │ │  enemy
│ └───────────────────────────┘ │
│ 1 ally · 1 neutral · 1 enemy  │  #awareness-summary
└───────────────────────────────┘
```

**GM view — "Awareness — GM (sees all)"** (name, kind, team, coords, controls):

```
┌───────────────────────────────┐
│ AWARENESS — GM (SEES ALL)     │
│ ┌───────────────────────────┐ │
│ │ ▲ Vex        npc·party    │ │  dot + name + kind/team + coords
│ │   (3, 4)          [team ▾]│ │  #team-select (GM)
│ │ ● Bram       player·party │ │
│ │   (7, 1)          [team ▾]│ │
│ │ ■ Grom       player·hostile│ │ ← GM-marked hostile player: red
│ │   (5, 6)          [team ▾]│ │
│ └───────────────────────────┘ │
│ 2 allies · 1 neutral · 1 enemy│
└───────────────────────────────┘
```

- Row layout: `[shape-color dot 16px]  [name (GM only)]  …  [kind·team (GM only)]
  [coords] [team select + delete: GM tools section, §6]`.
- GM rows for players show their entity; rows are sortable? **No** — stable order
  by entity id. Selection state: selected row has `--panel-bg` highlight + accent
  left border (synced with canvas selection).
- Clicking a GM row selects that entity (same as clicking its token).
- `#awareness-summary`: `n ally · n neutral · n enemy` (12px, muted).
- List doubles as the **screen-reader/keyboard path** to movement (§9 a11y).
- Own entity (player): shown as the first row `● YOU  (x, y)` with blue ring.

---

## 6. GM controls (entity management + paint)

`#entity-tools` section in the sidebar (GM only; hidden entirely for players),
above the awareness list or below — order in sidebar: **GM Tools → Awareness**.

```
┌───────────────────────────────┐
│ GM TOOLS                      │
│                               │
│ Selected: Vex (npc)           │  #sel-entity-name ("None" when nothing)
│ Team   [ party ▾ ]            │  #team-select  (party|neutral|hostile)
│        [ Delete entity ]      │  #btn-delete-entity (danger outline)
│                               │
│ New entity                    │
│ Name [ ____________ ]         │  #new-entity-name
│ Kind [ npc ▾ ]                │  #new-entity-kind (player|npc|enemy|gm_character)
│ Team [ neutral ▾ ]  [ Add ]   │  #new-entity-team  #btn-new-entity
└───────────────────────────────┘
```

- **Create:** `[Add]` → `{type:"create_entity", name, kind, team, x, y}`.
  Target cell = last hovered cell if walkable, else first walkable cell from
  (0,0); entity appears selected.
- **Set team:** changing `#team-select` → `{type:"set_team", entity_id, team}`
  for the currently selected entity.
- **Delete:** confirm via inline confirm (button morphs to `Really delete? [Yes][No]`
  for 3 s) → `{type:"delete_entity", entity_id}`.
- **Move any entity / override:** covered by §4.5 (select any entity + click,
  `#override-toggle`, one-shot "Move anyway"). GM **does not** have `place`
  clicks in v1 — placement of a *new* entity only; existing moves always go
  through `move` (keeps one authoritative path).
- **Paint cells:** §7.2 (bottom-bar tools, shared with upload preview).

**Permission rendering:** every GM-only element is marked
`data-role-gm` and hidden with `.gm-only { display:none }` unless
`body.is-gm`; likewise `body.is-player` hides nothing of the player UI.

---

## 7. Image upload flow (GM)

### 7.1 Upload form (`#upload-view`)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ LITTLEDUNGEONS ▸ New map                                                          │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ┌ UPLOAD MAP ─────────────────────────────────────────────────────────────────┐  │
│ │                                                                             │  │
│ │ Map name      [ The Gilded Crypt____________ ]                              │  │
│ │                #upload-name                                                  │  │
│ │                                                                             │  │
│ │ Image       [ Choose file…  ▸ crypt.png (312 KB) ]      (accept: png/jpg)   │  │
│ │                #upload-file (shows chosen filename after pick)              │  │
│ │                                                                             │  │
│ │ Grid size     Cols [____]   Rows [____]                                     │  │
│ │                #upload-cols #upload-rows                                    │  │
│ │                (leave blank = auto, longest side ≤ 60, aspect preserved)    │  │
│ │                                                                             │  │
│ │ [x] Dark pixels are walls      #dark-is-wall (default checked;              │  │
│ │     (typical: dark ink on light paper) typical maps are dark ink on light   │  │
│ │                                             paper; uncheck for inverted)    │  │
│ │                                                    [ Upload & detect ]       │  │
│ │                                                    #btn-detect (spinner      │  │
│ │                                                    state: "Detecting…" +     │  │
│ │                                                    disabled)                 │  │
│ └──────────────────────────────────────────────────────────────────────────────┘  │
│ #upload-preview (hidden until upload succeeds)                                   │
│ ┌ DETECTION PREVIEW — fix anything before starting ───────────────────────────┐  │
│ │                                                                             │  │
│ │  Source image            Detected grid (grid: 48 × 32)                      │  │
│ │  ┌─────────────────┐     ┌────────────────────────────────────────────┐     │  │
│ │  │                 │     │ ▓▓▓▓▓▓▓▓   ▓▓▓▓▓▓▓▓                        │     │  │
│ │  │   [original     │     │ ▓                 ▓  ▓▓▓▓▓▓                │     │  │
│ │  │    image,       │     │ ▓  ▓  ▓  ▓  ▓    ▓ ▓▓▓▓ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓    │     │  │
│ │  │    fitted]      │     │ ▓ ▓▓▓▓▓▓▓▓▓▓ ▓    ▓          ▓  (paintable)  │     │  │
│ │  │                 │     │ ▓▓▓▓ ▓▓▓▓▓▓ ▓▓▓ ▓ ▓▓▓▓▓▓▓▓▓ ▓               │     │  │
│ │  └─────────────────┘     └────────────────────────────────────────────┘     │  │
│ │  #preview-image            #preview-canvas (same cell renderer as map)       │  │
│ │                                                                             │  │
│ │  Paint to fix:   [▢ Floor] [▨ Wall] [▣ Door]     (drag to paint)            │  │
│ │                #paint-tools (same 3 tools, radio behavior, Floor active)    │  │
│ │                                                                             │  │
│ │  Note: detection is a suggestion — you are the editor of record.            │  │
│ │                                                                             │  │
│ │                              [ Back ]   [ Start session with this map ]     │  │
│ │                              #btn-back  #btn-start-map (accent, large)       │  │
│ └──────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Flow & states (`#upload-view`: idle → detecting → preview):**

1. **idle:** file + name required; `#btn-detect` disabled until both set. Cols/rows
   optional (validated 8–60 int if given; `dark_is_wall` default true).
2. **detecting:** button shows spinner + `Detecting…`, disabled; form fields locked.
3. **preview:** `#upload-preview` revealed, original `<img>` set to the uploaded
   file (object-URL, fitted, `image-rendering: pixelated` off), `#preview-canvas`
   renders the returned grid with the **identical cell renderer** used on the map.
   `#paint-tools` active — dragging paints cells (same paint behavior as §7.2,
   but on the preview canvas; sends `{type:"paint"}` per cell via the open WS,
   which updates all clients, or `POST /api/maps/{id}/paint` if the session's
   map id is already known from the upload response — implementation may pick;
   recommend WS `paint` since the GM already joined).
   - **Back** (`#btn-back`): returns to `#map-view` **without starting a session
     focus** — the map already exists server-side (upload creates it); the GM can
     re-open upload via `New map…`. Confirm if edits were made: `Discard edits?`
     (visual only — server map remains as last painted; note this in code comment).
   - **Start session** (`#btn-start-map`): switch to `#map-view`, toast
     `Map "{name}" is live`.
4. **error:** failed upload → `#toast` (`Upload failed: {message}`), return to idle.

> **Assumption (noted per contract):** `POST /api/maps/upload` creates the map
> immediately (no draft state in the API). The preview is therefore a
> review/edit step on an already-created map; players who connect during preview
> simply see it live. No delete-map endpoint exists; re-uploading supersedes the
> session map.

### 7.2 Paint behavior (shared: preview canvas + live map, GM only)

- Tool active (`Floor` / `Wall` / `Door`), pointer down → paint the cell under
  pointer, then on every cell entered while down (`pointermove`), send
  `{type:"paint", x, y, cell_type}` **once per cell** (dedupe re-painting the
  same cell with the same tool; max one message per cell per frame).
- Local optimistic paint on the GM's canvas for zero-lag feel; server `state`
  snapshots reconcile for everyone.
- Painting over an entity cell: allowed; the entity stays (server's problem —
  note as known limitation; recommend server keep entities in place).
- `Door` paints `doorway` (no doorway detection at paint time — GM places gaps).

---

## 8. Responsive behavior

| | Desktop ≥ 1024px | Tablet 768–1023px | < 768px (graceful bonus) |
|---|---|---|---|
| Layout | Sidebar **docked** right, 320px, full height | Sidebar **drawer**: hidden; `#sidebar-toggle` (☰) in top bar slides it over the canvas from the right with a `#scrim`; tap scrim or ☰ to close | Same drawer; `#session-title` ("LITTLEDUNGEONS") hidden, `#map-name` takes the left |
| Top bar | All items | `#fog-toggle` collapses to icon-button with state dot (full label in `title` + tooltip) | Same as tablet |
| Control bar | Single row | Single row, buttons 44px min | Wraps to 2 rows: tools row / hint row |
| Legend | Overlay pill bottom-left of canvas | Overlay pill, wraps 2 lines | Collapses behind a `?` chip in top bar (expandable popover) |
| Upload preview | Two panes side-by-side | Stacked (source image max-height 40vh, grid below) | Same stacked |
| Awareness list | All rows | Same | Rows: dot + coords only (kind/team moves into a tap-expand) |
| Touch targets | 32px ok | **≥ 44px** on all buttons/toggles | **≥ 44px** |
| Input | Mouse + keyboard | Tap (= click): select → tap destination; drag paints | Same |

- **No zoom/pan** — canvas refits on `resize` (debounced 100 ms) and on
  drawer open/close. This is the stated v1 trade-off (§0.7).
- Drawer is not modal (canvas still visible behind scrim at 50% dim); `Esc` closes.
- Portrait tablet: the fit-to-viewport math handles it; cell size shrinks,
  labels (GM name pills) hide below cell < 24px (dots/shape remain).

---

## 9. Accessibility checklist

- **Never color-alone:** ally/neutral/enemy always **shape + color** (▲ green,
  ● white, ■ red); GM view adds text labels; the list rows carry the same shape
  icons so a colorblind or grayscale user can distinguish all three.
- **White dot** has a dark stroke; on dark walls all dots keep their dark stroke.
- **Text contrast** ≥ 4.5:1 (chrome is dark; canvas labels get dark pills).
- **Keyboard path** (canvas can't be tabbed, so the list is the a11y surface):
  - Awareness rows `tabindex="0"`; `Enter`/`Space` selects the entity
    (players: own row only selectable).
  - With an entity selected, **arrow keys** send a one-cell `move` in that
    direction (`{x±1|y±1}` with the other coordinate unchanged — server's A*
    validates; one cell is always a legal move or a clean error).
    `Shift+arrow` = move to the farthest reachable cell in that direction.
  - `Esc` deselects / closes drawer / dismisses toast action.
- **Focus:** 2px `#ffd43b` `:focus-visible` outline on every control.
- **Live regions:** `#toasts` is `aria-live="polite"`; connection label changes
  are announced (it's inside a `role="status"` span).
- **Reduced motion:** `prefers-reduced-motion` → instant path, no pulse.
- **Canvas fallback:** `<canvas aria-label="Tactical map grid">` + the awareness
  list provides the textual equivalent of what's drawn.
- Toggle/checkbox labels are real `<label>`s; override toggle reads
  `aria-pressed` not needed (native checkbox).

---

## 10. Build-ready element reference

### 10.1 IDs

| ID | Type | View | Notes / states |
|---|---|---|---|
| `#lobby-view` | section | — | hidden after join |
| `#join-name` | input | lobby | maxlength 24 |
| `#join-gm`, `#join-player` | button | lobby | disabled while name empty |
| `#lobby-note` | p | lobby | static copy |
| `#upload-view` | section | — | `data-state="idle|detecting|preview"` |
| `#upload-name` | input | upload | maxlength 40 |
| `#upload-file` | input[file] | upload | accept `.png,.jpg,.jpeg,.webp` |
| `#upload-file-name` | span | upload | chosen filename |
| `#upload-cols`, `#upload-rows` | input[number] | upload | optional, 8–60 |
| `#dark-is-wall` | input[checkbox] | upload | default checked |
| `#btn-detect` | button | upload | `.is-loading` state |
| `#upload-preview` | div | upload | hidden until preview |
| `#preview-image` | img | upload | uploaded source, fitted |
| `#preview-canvas` | canvas | upload | detection grid, paintable |
| `#paint-tools` | div>buttons | upload | `[data-tool="floor|wall|doorway"]` |
| `#btn-back`, `#btn-start-map` | button | upload | — |
| `#map-view` | section | — | shown from `welcome` |
| `#topbar` | header | map | — |
| `#session-title` | span | topbar | "LITTLEDUNGEONS" |
| `#map-name` | span | topbar | from `map.name` |
| `#btn-new-map` | button | topbar | `.gm-only` |
| `#conn-status` | span | topbar | `role="status"`; child `#conn-dot`, `#conn-label`; classes `.is-connected/.is-connecting/.is-offline` |
| `#fog-toggle` | input[checkbox] | topbar | disabled unless `.is-gm` |
| `#sidebar-toggle` | button | topbar | hidden ≥1024px |
| `#scrim` | div | map | drawer backdrop |
| `#canvas-wrap` | div | map | `position:relative` (holds canvas + overlays); class `.mode-paint` when painting |
| `#map-canvas` | canvas | map | main renderer |
| `#legend` | div | map | static chips |
| `#coord-readout` | span | map | `(x, y)` under cursor |
| `#canvas-hint` | div | map | transient hint, auto-hide 2 s |
| `#no-map` | div | map | players' empty state; `.gm-only`-hidden |
| `#toasts` | div | map | `aria-live="polite"`; children `.toast` / `.toast-error`; optional `#toast-action` button |
| `#sidebar` | aside | map | `.is-open` when drawer open |
| `#entity-tools` | section | sidebar | `.gm-only` |
| `#sel-entity-name` | span | sidebar | selected entity or "None" |
| `#team-select` | select | sidebar | party/neutral/hostile; sends `set_team` |
| `#btn-delete-entity` | button | sidebar | inline confirm state |
| `#new-entity-name`, `#new-entity-kind`, `#new-entity-team` | inputs | sidebar | create form |
| `#btn-new-entity` | button | sidebar | sends `create_entity` |
| `#awareness` | section | sidebar | — |
| `#awareness-title` | h2 | sidebar | `Awareness — {name}` / `Awareness — GM (sees all)` |
| `#awareness-list` | ul | sidebar | rows `.awareness-row` (+`.is-own`); each row: `.dot` (`.shape-tri/.shape-circle/.shape-square` + `.team-party/.team-neutral/.team-hostile`), `.awareness-name` (GM), `.awareness-meta` (kind·team, GM), `.awareness-coords`; GM rows also carry `.row-select` (select) |
| `#awareness-summary` | span | sidebar | `n ally · n neutral · n enemy` |
| `#controls-bar` | footer | map | — |
| `#paint-group` | div | controls | `.gm-only`; buttons `.tool-btn[data-tool]` incl. `data-tool="select"` |
| `#override-toggle` | input[checkbox] | controls | `.gm-only`, "Ignore walls" |
| `#control-hint` | span | controls | per-mode hint text |

### 10.2 Body / state classes (driven by JS)

| Class | On | Meaning |
|---|---|---|
| `.is-gm` / `.is-player` | `<body>` | from `welcome.you.role`; gates all `.gm-only` elements |
| `.mode-select` / `.mode-paint-floor` / `.mode-paint-wall` / `.mode-paint-doorway` | `#canvas-wrap` | active tool |
| `.has-selection` | `#canvas-wrap` | an entity is selected (crosshair cursor off) |
| `.fog-on` | `<body>` | fog state (affects render only; also styles `#fog-toggle` area) |
| `.is-open` | `#sidebar` | drawer open (tablet) |
| `.is-connected` / `.is-connecting` / `.is-offline` | `#conn-status` | WS state |
| `.is-animating` | n/a (per-entity flag in JS) | path in flight |

### 10.3 Panel state summary (what JS must keep in sync)

| Panel | States |
|---|---|
| Views | lobby → upload(`idle`/`detecting`/`preview`) → map; map ↔ upload via `New map…` |
| WS connection | connecting → connected → (reconnecting →) connected; offline shows toast + `#conn-status` red; auto-reconnect with backoff, re-`request_state` on open |
| Map availability | `no map` (#no-map, players) vs `has map`; upload in progress (GM) |
| Selection | none → entity (own for player; any for GM); synced between canvas, awareness list rows, `#entity-tools` fields |
| Tool mode | select (default) → paint floor/wall/doorway (GM); one at a time |
| Override | off/on (GM only); affects move payloads + one-shot "Move anyway" |
| Fog | off (default) / on; GM sets, all render; player toggle disabled |
| Toasts | empty / info / error / error + `#toast-action` ("Move anyway", GM only, one-shot) |

---

## 11. Message → UI mapping (for app.js)

| Server message | UI action |
|---|---|
| `welcome` | set `body.is-gm/.is-player`, fill top bar, build entities, select own entity (player), show upload vs map view |
| `state` | reconcile grid (re-render if map changed), reconcile entities/players (list + canvas), update `#fog-toggle`, `#map-name` |
| `path` | start animation for `entity_id` (120 ms/cell) |
| `error` | `#toasts` toast; if `no route` + GM + original had no override → attach `#toast-action` "Move anyway" |
| client `move` rejected silently? | N/A — server always replies `error` to the sender on rejection |

Client message triggers (recap): join (lobby), `request_state` (on connect +
reconnect), `move` (click/keys), `paint` (drag), `create_entity` /
`delete_entity` / `set_team` (GM tools), `set_fog` (GM toggle).

---

## 12. Assumptions & notes for the engineer

1. **Role assignment:** lobby sends the *requested* role, but `welcome.you.role`
   is authoritative (first connection may become GM even if they pressed
   Player). Render from welcome only.
2. **Upload creates the map immediately** (API has no draft/delete-map); the
   preview screen edits the live map, "Start" is a UX transition only.
3. **Fog rendering is client-side** for players: on `fog`, each player client
   runs Bresenham LOS (same rules as §5) over the shared grid to hide entities
   without clear sight, with a "previously seen" memory set; GM never fogged.
   (Server keeps the canonical rule in `awareness.py`; client replicates for
   rendering since `state` sends full `entities` to all clients.)
4. **Paint over an occupied cell** is allowed; server keeps the entity in place
   (known limitation, fine for v1).
5. **No zoom/pan** in v1 (fit-to-viewport only) — acceptable per the soft
   requirements; grid caps at 60 cells so cells stay ≥ ~14px on tablet.
6. `#toast-action` re-sends `override:true` **without** flipping
   `#override-toggle`.
7. Keep a single cell-renderer function shared by `#map-canvas` and
   `#preview-canvas` (floor/wall/doorway look must be identical).
