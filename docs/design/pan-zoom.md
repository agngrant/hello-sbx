# Design — Pan & Zoom for the Tactical Map

**Status:** build-ready spec. New feature: the map can be **bigger than the
viewport**; every client navigates a **discrete-zoom, cell-panned viewport**
of the grid via **clickable arrow buttons + zoom buttons in the right-hand
control panel** and via **keyboard**. **Source of truth:** `PROJECT.md` +
the owner requirements below (frozen). Where this doc and `PROJECT.md`
diverge, `PROJECT.md` wins. (No divergence expected: this feature is
frontend-only — no wire protocol, REST, server, data-model, or awareness
changes.)

**Owner requirement (frozen):**
1. Map can be bigger than the viewport; navigate via pan + zoom.
2. Clickable arrows (left / right / up / down) in the **right-hand control
   panel** to pan.
3. Cursor keys pan the same way.
4. Zoom in / out buttons in the control panel.
5. `+` / `=` and `-` keys zoom in / out.
6. Max zoom: **6×5** grid squares visible; min zoom: **60×50** grid squares
   visible.

**Frozen design decisions (this spec details them, does not redesign them):**
- Discrete zoom levels, 6:5 aspect, L0…L10 (§5 table).
- Uniform square cells: `cell = floor(min(canvasW/viewCols, canvasH/viewRows))`,
  viewport centered with letterboxing (§2.3).
- Pan step: 10% of visible cells per press (min 1), clamped to
  `[0, max(0, mapSize − visible)]` (§2.4).
- Maps smaller than the view on an axis: centered, pan disabled that axis
  (arrows dimmed/disabled) (§3.3, E1).
- Initial view = fit whole map; re-fit on map swap (`use_map`) and on window
  resize (keep clamped) (§2.5, E2, E3).
- View state is **per-client, frontend-only**: no wire protocol / REST /
  server changes (AC21).
- Keyboard guard: arrow / `+` / `−` ignored while focus is in an
  input/textarea (§4.3).
- **One click→cell transform** used by ALL interactions (move, paint,
  door tap, safe-door tool, awareness overlay, GM tools) (§2.6, E5, E6).
- Render only visible cells + entities in the window (§6).

**Code referenced (read, not modified by the spec):** `app/static/index.html`
(`#sidebar`, `#canvas-wrap`, `#map-canvas`, `#controls-bar`),
`app/static/app.js` (`state`, `layoutCanvas`, `drawGridOnCanvas`,
`drawEntitiesAndDots`, `cellFromEvent`, the pointer/click handlers, the
`document` `keydown` handler, `applyState`, `openUploadedMap`, the resize
handler), `app/static/style.css` (design tokens, `.tool-btn`, `#sidebar`,
the <1024px media query), `tests/js/harness.js`,
`docs/design/wireframes.md` (§4 map view, §8 responsive, §9 keyboard),
`README.md` ("Limitations (v1)" — currently lists "No zoom / pan").

---

## 1. What changes (summary)

| # | Change | Where |
|---|---|---|
| V1 | **Per-client view state** `state.view = {level, panX, panY}` — discrete zoom level `0…10`, integer cell pan. Never sent to the server. | `app/static/app.js` |
| V2 | **View math module** (pure functions): `LEVELS`, `fitLevel(mw, mh)`, `viewStep(level, axis)`, `viewBounds(level, mw, mh)`, `applyView()` (cell size, letterbox offsets, render window), `panBy(dx, dy)`, `zoomBy(±1)`. | `app/static/app.js` |
| V3 | **New `#nav-panel`** — "Map view" panel, first section of the right-hand `#sidebar`: 3×3 arrow cluster (←↑→↓), zoom `−`/`+` buttons, one-line readout. Both roles. States: normal / disabled-at-clamp / disabled-at-extreme-zoom / disabled-axis-locked (§3). | `app/static/index.html`, `app/static/style.css` |
| V4 | **`layoutCanvas`** stops fit-to-map for `#map-canvas`; it computes `cell` / `offsetX` / `offsetY` from the view state (§2.3). The upload-preview canvas keeps its old self-fit math (A10). | `app/static/app.js` |
| V5 | **`drawGridOnCanvas(canvas, ctx, visibility, view)`** — gains an optional `view` argument `{s, ox, oy, x0, x1, y0, y1}`. When passed (map canvas only), all cell/wall/door/line loops run over the window bounds with unchanged tier/edge rules; when absent (preview canvas) the function is byte-identical to today (§6). | `app/static/app.js` |
| V6 | **`drawEntitiesAndDots`** skips entities (tokens, dots, labels, rings, hover/paint preview for them) outside the render window; everything that is drawn uses the same `(s, ox, oy)` as the grid (§6). | `app/static/app.js` |
| V7 | **`cellFromEvent` unchanged in formula** — it is the single click→cell transform; all existing consumers (hover, coord readout, `lastHovered` spawn target, paint, door/safeDoor tools, move/door-tap click) keep working under the transform (§2.6). | `app/static/app.js` |
| V8 | **Keyboard retarget:** arrow keys now **pan** (owner req 3) instead of moving the selected entity (wireframes §9 behavior retired — A2); `+`/`=` zoom in, `-` zoom out; input/textarea focus guard (§4). | `app/static/app.js` |
| V9 | **Fit triggers:** `onWelcome` / `applyState` detect a map change (same `mapChanged` signal already computed) → `fitToMap()`; initial welcome → `fitToMap()` (E3). | `app/static/app.js` |
| V10 | **Resize handler** (existing, debounced 100 ms): re-runs `layoutCanvas` at the SAME level/pan — pan is measured in cells, so bounds are unchanged; clamp is re-applied defensively (E2). | `app/static/app.js` |
| V11 | **`syncNavControls()`** — one function sets the six buttons' `disabled` + `title` from view state; called after any view mutation, state change, fit, resize (§3.3). | `app/static/app.js` |
| V12 | **Tests:** new `TestPanZoom` class in `tests/test_frontend.py` driven by the Node harness; `tests/js/harness.js` stubs gain the nav buttons + measured canvas/pointer geometry. `scripts/e2e_proof.py` is **unchanged** (no wire changes). | `tests/` |
| V13 | **Docs:** README "Limitations (v1)" — the "No zoom / pan" bullet is replaced by a short pan/zoom description (discrete levels L0–L10, arrows/keys); the matching phrase in `PROJECT.md` §11 ("no zoom/pan in v1") is updated in the same build. | `README.md`, `PROJECT.md` |

**What does NOT change:** the WS protocol (no new frames in either
direction), REST API, data model, server code, the three-tier awareness
model, movement rules / A*, upload/generate flows, the upload-preview
renderer, the legend, toasts, hints, drawer behavior, reduced-motion
behavior, and the bottom `#controls-bar` (paint tools stay there — the nav
panel lives in the sidebar per owner req 2).

---

## 2. The view model

### 2.1 State

```js
state.view = {
  level: 0,   // 0 (max zoom, 6×5) … 10 (min zoom, 60×50)
  panX: 0,    // integer cell offset from the map's west edge  ∈ [0, max(0, mw − W)]
  panY: 0,    // integer cell offset from the map's north edge ∈ [0, max(0, mh − H)]
};
```

`W` / `H` = the level's visible window in cells (§5). Pan is always in
**whole cells** (integer) — no sub-cell offsets, so cells stay pixel-aligned
and the existing 0.5-pixel grid-line crispness technique is unchanged.

Per-client only: each browser tab holds its own `state.view`; nothing about
it is ever serialized (A1, AC21).

### 2.2 Fit ("fit whole map")

```
fitLevel(mw, mh) = smallest L ∈ 0…10 with LEVELS[L].w ≥ mw AND LEVELS[L].h ≥ mh
                   else 10            // no level covers it → min zoom + pan
```

Full table in §5.3. `fitToMap()` sets `level = fitLevel(...)`, `panX = 0`,
`panY = 0`. Called on: join/welcome (initial view), and map swap (E3).

### 2.3 Cell size, letterboxing, offsets

For the current level's window `W × H` and the canvas wrap's available
area `availW × availH` (today's `wrap.clientWidth − 16`,
`wrap.clientHeight − 16`):

```
cell    = max(1, floor(min(availW / W, availH / H)))      // uniform SQUARE cells
winW    = W * cell
winH    = H * cell
offsetX = floor((availW − winW) / 2) − panX * cell        // letterbox-centered − pan
offsetY = floor((availH − winH) / 2) − panY * cell
```

- **Square cells:** one `cell` for both axes (frozen). Because the levels
  keep the 6:5 ratio, when the canvas ratio differs from 6:5 the window is
  strictly smaller than the canvas on one axis → **letterbox bars** of the
  canvas background (`#171b26`) remain on that axis, exactly like today's
  centered fit. (A9: no `min(8, …)` cell floor — under zoom the cell may be
  < 8 px in tiny windows; that is visible feedback that the map is large.)
- `offsetX` / `offsetY` may be **negative** (panned) and are integers, so
  grid lines stay on the same 0.5-pixel crispness path as today.
- The canvas element sizing (DPR transform, `canvas.width/height`) is
  unchanged from today's `layoutCanvas`.

**Render window** (what gets drawn, §6):

```
x0 = panX,  x1 = min(mw, panX + W)
y0 = panY,  y1 = min(mh, panY + H)
```

When the map is smaller than the window on an axis, `panX`/`panY` are
clamped to 0 and the map's cells sit centered *inside* the letterboxed
window (E1) — i.e. the map is centered in the canvas on that axis.

### 2.4 Pan step and clamp (frozen values)

```
stepX(level) = max(1, round(0.1 * W))     // 10% of visible cells, min 1
stepY(level) = max(1, round(0.1 * H))
panX = clamp(panX ± stepX, 0, max(0, mw − W))
panY = clamp(panY ± stepY, 0, max(0, mh − H))
```

Per-level step (A6 — `Math.round`, half-up):

| L | W×H | stepX | stepY |
|---|-----|-------|-------|
| 0  | 6×5   | 1 | 1 |
| 1  | 8×7   | 1 | 1 |
| 2  | 10×8  | 1 | 1 |
| 3  | 13×11 | 1 | 1 |
| 4  | 16×13 | 2 | 1 |
| 5  | 20×17 | 2 | 2 |
| 6  | 25×21 | 3 | 2 |
| 7  | 30×25 | 3 | 3 |
| 8  | 40×33 | 4 | 3 |
| 9  | 50×42 | 5 | 4 |
| 10 | 60×50 | 6 | 5 |

One press (or one keydown, or one button click) = exactly **one** step on
one axis. Pan is clamped per axis independently; a map axis with
`size ≤ visible` has range `[0,0]` → that axis is **locked** (both arrows
permanently disabled, §3.3).

### 2.5 When the view is set / kept

| Event | Level | Pan |
|---|---|---|
| Join / welcome (first map) | `fitLevel(mw, mh)` (§5.3) | `(0, 0)` |
| Map swap — `use_map` → new `state` frame with different map dims (the `mapChanged` check already in `applyState`) | `fitLevel` of the NEW map | `(0, 0)` (E3) |
| Window resize (existing debounced handler) | **unchanged** | **unchanged**, re-clamped defensively (E2) |
| `+` (zoom in) / `−` (zoom out) button or key | `+` → `level−1` (toward L0), `−` → `level+1` (toward L10), clamped to `[0, 10]` | unchanged, re-clamped (a zoom-in can shrink the pan range) |
| Arrow button / key | unchanged | one axis ± step, clamped |

Note: because the levels are fixed in **cell** units, resize changes only
the pixel cell size and the letterbox — it never changes the visible window
or the pan range, so "keep clamped" is trivially true; the re-clamp is
defensive.

### 2.6 The single click→cell transform (frozen)

**All** pointer→cell conversion goes through the existing
`cellFromEvent(ev)`, whose formula is **unchanged**:

```
px = ev.clientX − rect.left;  py = ev.clientY − rect.top
x  = floor((px − offsetX) / cell)
y  = floor((py − offsetY) / cell)
return in-bounds (0 ≤ x < mw, 0 ≤ y < mh) ? {x, y} : null
```

Because `offsetX`/`offsetY`/`cell` are now the **view's** origin (which may
be negative under pan), this one function automatically gives correct cells
for: hover ring + coord readout, GM `lastHovered` spawn target, GM paint
(pointerdown/drag), GM Door tool, GM Safe-door tool, player door tap,
player/GM click-to-move, entity hit-testing (`entityAtCell` — already
cell-based). Pixels in letterbox bars, in the dark area beyond a small map,
or otherwise off-map return `null` → all existing no-op guards apply
unchanged.

The **pixel→draw** direction is equally single-sourced: everything drawn on
`#map-canvas` is placed at `px = offsetX + x * cell`, `py = offsetY + y *
cell` (the same `s, ox, oy` pair the grid, doors, tokens, awareness dots,
rings, hover ring, and paint preview already share — §6). There is exactly
one transform in each direction; no interaction may re-derive geometry.

---

## 3. Control panel layout (right-hand `#sidebar`)

### 3.1 Placement

A new `<section id="nav-panel" class="panel">` is the **first** child of
`#sidebar` (above GM Tools, above Awareness), so it is always visible for
**both roles** (players get exactly one panel in the sidebar). On
< 1024 px the whole sidebar is the drawer, so the nav panel is reachable
via the existing ☰ toggle (A13); nothing is duplicated over the canvas.

### 3.2 Wireframe (320 px panel, inner ≈ 288 px)

```
┌──────────────────────────────────────┐
│ MAP VIEW                             │  .section-label (uppercase, muted)
│                                      │
│  ┌────────────┐    ┌───────────────┐ │
│  │     [↑]    │    │   [−]  [+]    │ │  ← 32×32 buttons (44×44 < 1024 px),
│  │ [←]    [→] │    └───────────────┘ │    .nav-btn, .tool-btn family styling
│  │     [↓]    │         zoom         │  (32 px gap between the two groups)
│  └────────────┘    (10px caption)    │
│                                      │
│  L4 · 16×13 · (3,2)–(18,14) of 40×30 │  #nav-readout, .muted.small
└──────────────────────────────────────┘
│                                    │  (below, unchanged:)
│ ┌ GM TOOLS (GM only) ────────────┐ │  #entity-tools
│ ┌ AWARENESS — NAME ─────────────┐ │  #awareness
```

- Arrow cluster: CSS grid, `grid-template-areas: ". u ." "l . r" ". d ."`;
  each `.nav-btn` 32×32 (≥ 44×44 under the existing <1024 px media query,
  added to the same selector list as `.tool-btn`). Center cell empty
  (spatial anchor, matches the on-map direction vocabulary).
- Glyphs: `↑ ↓ ← →` (text, like the existing `☰` icon-button idiom) and
  `−` / `+`. `aria-label`s: "Pan up (ArrowUp)" … "Zoom in (+)" /
  "Zoom out (-)". Grouped `role="group" aria-label="Pan the map"` /
  `"Zoom the map"`.
- Buttons use the existing control vocabulary: `--panel-bg` fill on
  `--chrome-bg`, `1px solid #39415a`, `--r-control` (6 px) radius, hover
  accent tint, 2 px `#ffd43b` `:focus-visible` ring, `:disabled {
  opacity: .45; cursor: not-allowed }` — identical to `.tool-btn`.
- `#nav-readout`: `L{level} · {W}×{H} · ({x0},{y0})–({x1−1},{y1−1}) of
  {mw}×{mh}` — updates with every view change (cheap text node).
- HTML skeleton:

```html
<section id="nav-panel" class="panel">
  <h2 class="section-label">Map view</h2>
  <div class="nav-layout">
    <div class="nav-arrows" role="group" aria-label="Pan the map">
      <button id="nav-up"    class="nav-btn nav-up"    aria-label="Pan up (ArrowUp)">↑</button>
      <button id="nav-left"  class="nav-btn nav-left"  aria-label="Pan left (ArrowLeft)">←</button>
      <button id="nav-right" class="nav-btn nav-right" aria-label="Pan right (ArrowRight)">→</button>
      <button id="nav-down"  class="nav-btn nav-down"  aria-label="Pan down (ArrowDown)">↓</button>
    </div>
    <div class="nav-zoom" role="group" aria-label="Zoom the map">
      <button id="zoom-out" class="nav-btn" aria-label="Zoom out (-)">−</button>
      <button id="zoom-in"  class="nav-btn" aria-label="Zoom in (+)">+</button>
    </div>
  </div>
  <p id="nav-readout" class="muted small">…</p>
</section>
```

### 3.3 Visual states (frozen: normal / disabled-at-clamp / disabled-at-extreme-zoom)

One function, `syncNavControls()`, recomputes all six states after any
view mutation, state change, fit, or resize:

| Control | Normal (enabled) | Disabled — disabled-at-clamp | Disabled — axis locked | Disabled — extreme zoom |
|---|---|---|---|---|
| `←` | `0 < panX < max(0, mw−W)` | `panX = 0` — `title="Panned to the west edge"` | `mw ≤ W` — `title="Map fits horizontally — no pan"` | — |
| `→` | `0 < panX < max(0, mw−W)` | `panX = max(0, mw−W)` — `title="Panned to the east edge"` | same as `←` | — |
| `↑` | `0 < panY < max(0, mh−H)` | `panY = 0` — `title="Panned to the north edge"` | `mh ≤ H` — `title="Map fits vertically — no pan"` | — |
| `↓` | `0 < panY < max(0, mh−H)` | `panY = max(0, mh−H)` — `title="Panned to the south edge"` | same as `↑` | — |
| `−` | `level < 10` | — | — | `level = 10` (L10, 60×50) — `title="Fully zoomed out (60×50)"` |
| `+` | `level > 0` | — | — | `level = 0` (L0, 6×5) — `title="Fully zoomed in (6×5)"` |

Disabled = native `disabled` attribute (so no pointer, no keyboard
activation, `opacity .45`, `cursor: not-allowed` — the existing
`.tool-btn:disabled` look). While no map exists yet (pre-`welcome`), all six
are disabled. Disabled buttons keep `title` text explaining *why*, so the
dimmed state is self-explanatory. Keyboard presses of guarded/capped
actions are silent no-ops (no toast — matches how e.g. painting off-map is
handled today).

---

## 4. Keybindings

### 4.1 Table

| Key (`ev.key`) | Action | Modifier notes | Guard behavior |
|---|---|---|---|
| `ArrowLeft` / `ArrowRight` / `ArrowUp` / `ArrowDown` | Pan one step on that axis (§2.4) — **identical delta to the matching arrow button** (owner req 3) | Ignored with **any** modifier (Ctrl/Alt/Shift/Meta) — no Shift+arrow variant exists | Guard §4.3; only when `#map-view` is visible, joined, and a map exists; `preventDefault()` (stops page scroll) |
| `+` or `=` | Zoom in one level (`level−1`, floor 0 — bigger cells, more detail) | Ignored with any modifier (Ctrl+`=` is browser zoom — never fight it) | Guard §4.3; same visibility conditions; `preventDefault()` |
| `-` | Zoom out one level (`level+1`, cap 10 — smaller cells, see more) | Same as above | Same as above |

Notes:
- `ev.key` is used (not `ev.code`), so **main-row and numpad** `+` / `-`
  both work (numpad `+` reports `"+"`), and `=` covers Shift+`+` on
  main-row keyboards (A4).
- Native activation of the focused nav button (Space/Enter) keeps working
  normally — the buttons are real `<button>`s; the guard (§4.3) does not
  intercept buttons.
- **Retired:** with an entity selected, arrow keys used to send a
  one-cell `move` (wireframes §9) and `Shift+arrow` sent farthest-reachable
  — both behaviors are removed; arrow keys pan now, per owner req 3 (A2).
  Movement remains fully available by cell click/tap (wireframes §4.5),
  and the keyboard a11y surface (Tab to awareness rows, Enter/Space to
  select) is unchanged.
- No other keys are bound: no "0"=fit, no wheel zoom, no drag-pan, no
  `Home`/`End` (A5).
- `Esc`, Enter/Space-on-awareness-row, and upload-view keys are untouched.

### 4.2 Handler shape

```js
document.addEventListener("keydown", (ev) => {
  // …existing Esc / awareness-row handling…
  if (focusInField(ev.target)) return;                 // §4.3 guard
  if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.shiftKey) return;
  if (els.mapView.hidden || !state.joined || !state.grid) return;
  switch (ev.key) {
    case "ArrowLeft":  ev.preventDefault(); panBy(-1, 0);  return;
    case "ArrowRight": ev.preventDefault(); panBy( 1, 0);  return;
    case "ArrowUp":    ev.preventDefault(); panBy(0, -1);  return;
    case "ArrowDown":  ev.preventDefault(); panBy(0,  1);  return;
    case "+": case "=": ev.preventDefault(); zoomBy( 1);   return;
    case "-":           ev.preventDefault(); zoomBy(-1);   return;
  }
});
```

### 4.3 Input focus guard (frozen + A3)

`focusInField(t)` is true when `t` is (or `t.tagName` is) an `INPUT` or
`TEXTAREA` — the frozen set — **extended** to `SELECT` and
`contenteditable` (A3: arrow keys over an open `<select>` must change the
option, not pan; this matches the guard's intent, e.g. the GM's
`#team-select` / `#new-entity-kind`). Effect: typing `-` in
`#awareness-input` or `#gen-seed` enters a minus sign; typing `+` in a name
field enters `+`; arrows inside a text field move the caret. (Covers
`#join-name`, all upload/generate inputs, `#new-entity-name`,
`#awareness-input`, and the selects.)

---

## 5. Zoom levels (frozen table) + fit rule

### 5.1 Levels

| L | W×H (visible cells) | ~aspect | stepX | stepY | Meaning |
|---|---------------------|---------|-------|-------|---------|
| **0** | **6×5**  | 6:5 | 1 | 1 | **max zoom** |
| 1 | 8×7 | 6:5 (rounded) | 1 | 1 | |
| 2 | 10×8 | | 1 | 1 | |
| 3 | 13×11 | | 1 | 1 | |
| 4 | 16×13 | | 2 | 1 | |
| 5 | 20×17 | | 2 | 2 | |
| 6 | 25×21 | | 3 | 2 | |
| 7 | 30×25 | | 3 | 3 | |
| 8 | 40×33 | | 4 | 3 | |
| 9 | 50×42 | | 5 | 4 | |
| **10** | **60×50** | 6:5 | 6 | 5 | **min zoom** |

`LEVELS = [{w:6,h:5},{w:8,h:7},{w:10,h:8},{w:13,h:11},{w:16,h:13},
{w:20,h:17},{w:25,h:21},{w:30,h:25},{w:40,h:33},{w:50,h:42},
{w:60,h:50}]`

Cells at level L are `floor(min(availW/W, availH/H))` px (square,
letterboxed) — so "max zoom" is *at least* 6×5 cells visible (exactly 6×5
when the canvas ratio is ≥ 6:5; a very tall/narrow window shows 6 cols ×
5 rows of cells plus dark bars — the window is defined in cells, never
pixels).

### 5.2 Pan range per level (given a `mw × mh` map)

```
panX ∈ [0, max(0, mw − W(L))]     panY ∈ [0, max(0, mh − H(L))]
```

### 5.3 Which level is chosen for "fit whole map"

`fitLevel(mw, mh)` = the **smallest** L (most zoomed) with
`W(L) ≥ mw` **and** `H(L) ≥ mh`; if none exists (only possible for
`mh > 50`, since map dims cap at 60×60), **L10** with panning enabled on
the overflowing axis (E7).

| Map size `cols × rows` (fits when both ≤ level) | Fit level |
|---|---|
| ≤ 6×5 (e.g. 6×5, 4×4, 2×3) | **L0** |
| ≤ 8×7 (e.g. 7×6, 8×6, 6×6) | **L1** |
| ≤ 10×8 (e.g. 9×8, 10×7) | **L2** |
| ≤ 13×11 (e.g. 12×10) | **L3** |
| ≤ 16×13 | **L4** |
| ≤ 20×17 (e.g. 15×15) | **L5** |
| ≤ 25×21 (e.g. **24×16** — the default generated map) | **L6** |
| ≤ 30×25 | **L7** |
| ≤ 40×33 | **L8** |
| ≤ 50×42 | **L9** |
| ≤ 60×50 (e.g. 55×45, 60×50) | **L10** |
| rows 51–60 (e.g. 30×60, **60×60**) — no level covers >50 rows | **L10** + vertical pan (range `mh−50`) |

Examples: 10×8 map → L2 exactly; 30×10 → L7 (both axes locked, centered);
24×16 generated default → L6 (25×21 window, map centered); 60×60 → L10
(60×50 window, vertical pan 0…10, E7).

---

## 6. Rendering under the transform

### 6.1 Culling (frozen: "render only visible cells + entities in window")

- `layoutCanvas()` → `applyView()` stores `{s: cell, ox: offsetX,
  oy: offsetY, x0, x1, y0, y1}` in state; `renderAll()` draws with it.
- **Grid pass (`drawGridOnCanvas`):** every loop (floor fill, grid lines,
  wall fill/hatch/border, door cells) iterates `x ∈ [x0, x1)`,
  `y ∈ [y0, y1)` instead of the full grid. **The per-cell tier decision and
  the explored-map edge-style rules are unchanged** — `tier(x, y)` is
  looked up in the full-map visibility matrix regardless of whether the
  neighbor is in-window, exactly as the full-grid renderer does today.
  Consequence: the visible region is **pixel-identical** to what today's
  full-grid renderer produces for the same cells (including the 1 px
  frontier edges at the window boundary, which resolve correctly when you
  pan). The no-tier GM/preview fast path (single `fillRect` + one grid-line
  stroke) becomes: one `fillRect` for the window rect + grid lines for
  `x ∈ [x0, x1]`, `y ∈ [y0, y1]` clipped to the window.
- **Door pass:** only in-window doorway cells are drawn (same loop bounds).
- **Entity pass (`drawEntitiesAndDots`):** entities with
  `x ∈ [x0, x1)` and `y ∈ [y0, y1)` are drawn (tokens, awareness dots,
  gray-"?" markers, own-token ring, GM name pills, awareness rings,
  hover/selection rings, paint-preview highlight). Off-window entities are
  **skipped entirely** (no draw calls at all — AC22). The in-flight
  **path polyline** is drawn in full and simply clipped by the canvas
  edges (its cost is negligible; per-segment culling is not worth it, A12).
- Canvas background (`#171b26`) fills the whole canvas first, as today —
  it covers letterbox bars and the area beyond a small map.
- **Preview canvas is untouched** (A10): `drawGridOnCanvas(els.previewCanvas,
  …)` (upload detection preview, the `wireframes §12.7` shared-renderer
  call site) passes **no** `view` and keeps its internal self-fit math, so
  previews render the whole map byte-identically to today.

### 6.2 Awareness overlay alignment (E6)

The overlay is not a separate layer — awareness dots, "?" markers, rings,
and tokens are drawn in the same pass with the same `(s, ox, oy)` origin as
the grid they sit on. Because of the single transform (§2.6), a marker for
an entity at `(x, y)` is centered at
`(offsetX + (x + 0.5) * cell, offsetY + (y + 0.5) * cell)` at **every**
level and pan — structurally impossible to misalign (verified by AC18).
Pan never changes *which* entities are in the awareness data — the server
payload is untouched; the client only skips drawing off-window ones (and
the sidebar list is never affected). Rings/path lines may be clipped at the
canvas edge — acceptable (A12).

### 6.3 Interaction under the transform (E5)

No interaction changes: they all resolve through `cellFromEvent` (§2.6) and
the existing `state.tool` / selection / `painting` machinery, which are
cell-coordinate based:

- **GM paint** (floor/wall/doorway drag): `pointerdown`/`pointermove` →
  `cellFromEvent` → `paintCell(x, y)` — paints the cell under the cursor at
  any zoom/pan; `lastPainted` dedup and the wire message are unchanged.
  Dragging off the window returns `null` → no paint (same as dragging off
  the map today).
- **GM Door / Safe-door tools:** click → `cellFromEvent` → same
  `sendDoor` / `sendSafeDoor` with the resolved cell.
- **Player door tap / move; GM select + click-to-move; GM
  `lastHovered` spawn ("Add"):** unchanged paths, correct cells via the
  transform.
- **Hover ring + coord readout** follow the cursor under the transform;
  the readout shows map coordinates (not window coordinates), as today.

---

## 7. Edge cases

| # | Case | Required behavior |
|---|------|-------------------|
| **E1** | **Tiny map (≤ 6×5)**, e.g. 6×5 or 4×4 | `fitLevel` → L0 (6×5 window ≥ map). Both axes locked: `panX = panY = 0`, all four arrows permanently disabled ("Map fits … — no pan"), `−` disabled (already at L0). The map is **centered** in the letterboxed window. Zooming in past the map (L1…L10) keeps it centered and locked — only the buttons change; no pan is ever possible. |
| **E2** | **Window resize mid-session** (dragging the browser edge, tablet rotate, drawer open/close) | Existing debounced (100 ms) handler → `layoutCanvas` recomputes `cell` + letterbox for the **same** `level` and **same** `panX/panY`; pan bounds are in cells and do not change with pixel size, so the view is stable; re-clamp is a defensive no-op. **No re-fit to the map** on resize (re-fit is join + map swap only, §2.5). If `availW/H` shrink so much that `cell` would be < 1 px, cell floors at 1 and the window overflows the canvas centered (degenerate but coherent). |
| **E3** | **Map swap mid-view** (GM `use_map` → `state` frame with new dims; also covers a player receiving it) | The existing `mapChanged` detection in `applyState` → `fitToMap()`: `level = fitLevel(new mw, mh)`, `pan = (0, 0)`. Any in-flight pan/zoom of the old map is discarded with the old grid. Entities re-placed server-side render in the new window. The `state` broadcast is the only signal — no extra frames. |
| **E4** | **Input focus guard** | Focus in `#join-name`, `#new-entity-name`, `#awareness-input`, any upload/generate input, any `<select>`, or a contenteditable → arrows/`+`/`-` are **fully ignored** (no pan, no zoom, no `preventDefault` — native editing is untouched: typing `-`/`+` works, arrows move the caret / change the select). Guard re-checks on every keydown (focus can move at any time). Buttons remain clickable regardless of focus. |
| **E5** | **Painting a cell under transform** | At, e.g., L4 (16×13) with pan (2,3): pointer at pixel `p` paints exactly the cell whose rectangle contains `p` per §2.6 — the wire `paint.x/y` equals the rendered cell at `p`. No off-by-one at window edges (cells are integer-cell aligned; the 0.5 px grid-line offset does not shift the floor-divide because `offsetX` stays integer). Dragging into letterbox bars → `null` → stroke ends, no phantom paints. The GM's `lastHovered` (spawn target) uses the same transform, so "Add" spawns where the GM was hovering. |
| **E6** | **Awareness overlay alignment under transform** | Player view: FULL dots, APPROXIMATE gray-"?" blocks (2×2-quantized **map** positions), awareness rings (radius in **cells** around the token), and the own token all draw from `(s, ox, oy)` — aligned to their map cells at every level/pan (AC18). No overlay-specific code path; misalignment is structurally impossible. Approx "?" blocks near the window edge clip at the canvas boundary (A12). The sidebar list and summary counts are never window-filtered. |
| **E7** | **60×60 map** (taller than 50 at min zoom → vertical pan required) | `fitLevel`: no level has `H ≥ 60` → **L10** (60×50). Horizontal: `60 ≤ 60` → locked (←/→ permanently disabled). Vertical: range `[0, 10]`, `stepY = 5` → pan positions 0 / 5 / 10. From fit, `↑` is at clamp (disabled), `↓` steps 0→5→10; at 10, `↓` becomes disabled. Zoom-in from here (e.g. to L9, 50×42) unlocks horizontal (range 10, step 5) and extends vertical (range 18, step 4); the previous pan is re-clamped. |
| **E8** | **Rapid key repeat** | OS key-repeat delivers a stream of `keydown`s; each is exactly one step (one `panBy`), clamped as usual — holding `↓` at L10 on a 60×60 map walks 5/5/5 → clamped at 10. No coalescing/debounce on the *input* side (a repeated press at the clamp is a silent no-op with the button already disabled). The *render* side coalesces: view mutations mark the frame dirty and at most one `requestAnimationFrame` re-render runs per frame (the same rAF pattern the pointer handlers already use), so even 30 Hz repeat never queues unbounded renders. A focused button does **not** auto-repeat on keydown (native button semantics), so click-repeats cannot bypass the clamp either. |

---

## 8. Acceptance criteria

Each is testable in the Node frontend harness (`tests/js/harness.js` +
`tests/test_frontend.py`) and/or live. Harness note: the stub DOM gains the
six nav buttons (clickable, with `disabled`/`title`), a controllable
`#canvas-wrap` client size, and a pointer-position → `clientX/Y` helper;
canvas 2D calls are capturable for culling/alignment checks. "View"
asserts read `state.view` / the computed `cell` / `offsetX` / `offsetY`.

- **AC1 — Map bigger than the viewport is navigable** (owner req 1).
  With a 60×60 map, L0 shows exactly the 6×5 window `x ∈ [0,6)`,
  `y ∈ [0,5)`; by a sequence of `panBy` / `zoomBy` operations every map
  cell becomes visible at some (level, pan) — e.g. cell (59, 59) is
  visible at L10, pan (0, 10), and at L0, pan (54, 54). *(harness + live)*
- **AC2 — Clickable arrows in the right-hand control panel pan** (owner req 2).
  `#nav-panel` exists inside `#sidebar`; clicking `←`/`→`/`↑`/`↓` changes
  `panX`/`panY` by exactly `−/+ step` on that axis (and only that axis);
  the rendered window follows (grid pass bounds + offsets per §2.3).
  *(harness + live)*
- **AC3 — Cursor keys pan the same way** (owner req 3).
  Dispatching `keydown` ArrowLeft/Right/Up/Down with map view visible and
  no field focused produces a delta **identical** to clicking the matching
  arrow button (same sign, same magnitude per §2.4, clamped). *(harness)*
- **AC4 — Zoom in / out buttons** (owner req 4).
  Clicking `+` zooms IN (DECREMENTS `level` toward 0: window W×H per §5.1
  shrinks, cell grows per §2.3, readout updates); `−` zooms OUT (INCREMENTS
  `level` toward 10); a full `+` ×10 from L10 lands on L0 and a full `−` ×10
  from L0 lands on L10. *(harness)*
- **AC5 — `+`/`=` zoom in, `-` zooms out** (owner req 5).
  `keydown` `+`, `=`, and `-` (unmodified, no field focus) change the
  level by +1, +1, −1 respectively; numpad variants (same `ev.key`) do too.
  *(harness)*
- **AC6 — Zoom extremes** (owner req 6).
  At L0 the visible window is exactly 6×5 cells and `+`/`+`/`=` are no-ops
  with `#zoom-in` **disabled** ("Fully zoomed in (6×5)"); at L10 the window
  is exactly 60×50 and `−`/`-` are no-ops with `#zoom-out` **disabled**
  ("Fully zoomed out (60×50)"). No level exists outside `[0, 10]`. *(harness)*
- **AC7 — Level table conformance.** On a 60×60 map, for every L0…L10 the
  rendered window equals the frozen W×H of §5.1 and `cell` obeys
  `floor(min(availW/W, availH/H))`. *(harness)*
- **AC8 — Square cells + letterboxing.** With `availW=800`, `availH=400`,
  L0: `cell = 80` (400/5 = 80 < 800/6), `offsetX = (800 − 480)/2 = 160`,
  `offsetY = 0` — the 6×5 window is centered with dark bars on both sides;
  at L10 with the same canvas: `cell = floor(min(800/60, 400/50)) =
  floor(min(13.3, 8)) = 8`, `offsetX = (800 − 60·8)/2 = 160`,
  `offsetY = (400 − 50·8)/2 = 0`. Cells are square at all
  levels (one `cell` for both axes). *(harness — exact numbers asserted)*
- **AC9 — Pan step.** For every level, `panBy(+1,0)` / `panBy(0,+1)` move
  by the §2.4 table values (spot: L0 = (1,1), L4 = (2,1), L6 = (3,2),
  L10 = (6,5)), minimum 1 on every level. *(harness)*
- **AC10 — Pan clamp.** On a 40×30 map at L0: `panBy` attempts past the
  edges clamp to `[0, 34] × [0, 25]`; at `panX = 0` the `←` button is
  disabled and at `panX = 34` the `→` button is disabled (titles per
  §3.3); pressing a clamped key is a no-op. *(harness)*
- **AC11 — Map smaller than the view on an axis: centered + pan disabled.**
  A 60×10 map fitted at L10 has both axes locked; zoomed in to L9
  (50×42) it has `mw > W` (horizontal pan 0…10, step 5, `↑`/`↓`
  permanently disabled "Map fits vertically — no pan") and the map is
  vertically centered: `offsetY = (availH − 42*cell)/2 − 0`. *(harness)*
- **AC12 — Initial view fits the whole map.** Joining (`welcome`) with a
  map of `mw × mh` sets `level = fitLevel(mw, mh)` per the §5.3 table
  (spot: 6×5→L0, 10×8→L2, 24×16→L6, 60×50→L10, 60×60→L10) and
  `pan = (0,0)`, with the full map inside the window when a covering
  level exists. *(harness)*
- **AC13 — Re-fit on map swap.** With the old map panned/zoomed
  (level 3, pan (5,5)), a `state` frame for a new 24×16 map (via the
  `use_map` flow) re-fits to L6, pan (0,0), and renders the new grid.
  The old view state is never applied to the new grid. *(harness + live)*
- **AC14 — Resize keeps level + clamped pan.** Simulate `resize` with the
  same map at level 6, pan (8,4), new wrap size: `level` and `panX/panY`
  are unchanged, `cell`/offsets recompute per §2.3, and pan stays within
  bounds. **No** re-fit to map on resize. *(harness + live)*
- **AC15 — Input focus guard.** With `#new-entity-name` (input) and
  `#awareness-input` (input) and `#team-select` (select) focused in turn,
  ArrowUp/Down and `+`/`-` keydowns cause **no** pan/zoom change and are
  not `preventDefault`ed (typing `-` updates the input value). *(harness + live)*
- **AC16 — Painting under transform.** GM at L4, pan (2,3): a
  `pointerdown`+`pointermove` drag across the window emits `paint` frames
  whose `(x, y)` equal the cells actually rendered under each sampled
  pixel (≥ 3 sampled points, including a window-edge cell); entering a
  letterbox bar emits nothing. GM Door and Safe-door tool clicks resolve
  to the correct cell the same way. *(harness)*
- **AC17 — Movement under transform.** Player tap-to-move and GM
  select→click-to-destination send `move` with the coordinates of the
  cell rendered under the click at L5, pan (4,2); GM "Add" with the cursor
  over cell (7,9) (hovered at L3, pan (2,2)) spawns at (7,9).
  Out-of-window/off-map clicks send nothing. *(harness)*
- **AC18 — Awareness overlay alignment under transform.** For a player
  view at L4, pan (3,4): the drawn center of each FULL dot, APPROX "?"
  block, own token, and ring anchor equals
  `(offsetX + (x + 0.5)·cell, offsetY + (y + 0.5)·cell)` of its map
  coordinates (assert via captured draw calls / pixel sampling); the
  sidebar list is unaffected by the window. *(harness + live)*
- **AC19 — 60×60 map (E7).** Fit → L10; `←`/`→` permanently disabled;
  vertical range 0…10, step 5: from fit `↑` disabled; `↓` ×2 → `panY =
  10`, then `↓` disabled and `↑` enabled; `↑` ×2 returns to 0 and
  re-disables `↑`. *(harness)*
- **AC20 — Rapid key repeat (E8).** N dispatched ArrowDown keydowns at
  L10 on a 60×60 map yield `panY = min(N·5, 10)` (exact; no dropped or
  doubled steps), and at most one rAF-scheduled render per frame is
  observed (no unbounded render queue). *(harness)*
- **AC21 — Per-client, frontend-only (no wire changes).** Performing any
  pan/zoom/resize operation sends **zero** WebSocket frames (`wsSend`
  uncalled on the view path; `welcome`/`state`/`path`/`error` payloads
  byte-identical to pre-feature); a second harness client with its own
  `state.view` is unaffected by the first client's view changes.
  *(harness)*
- **AC22 — Render culling.** At L0 on a 60×60 map: the grid pass performs
  fill/stroke work only for cells in the 6×5 window (no draw call touches
  a cell with `x ≥ 6` or `y ≥ 5`); an entity at (59, 59) produces no draw
  calls, while an entity at (5, 4) does; panning to (54, 54) flips both.
  *(harness)*

---

## 9. Assumptions

- **A1.** View state is 100% per-client and frontend-only (frozen): no WS
  frame, REST field, or server change; the server payload and the GM's
  view are unrelated to any player's view.
- **A2.** Arrow-key *entity movement* (wireframes §9: arrows move the
  selected entity; `Shift+arrow` = farthest-reachable) is **retired** —
  owner req 3 assigns arrow keys to panning. Movement remains via
  click/tap (wireframes §4.5, unchanged) and the awareness list keeps its
  keyboard surface (Tab / Enter / Space). This is a deliberate,
  documented contract change; `wireframes.md` §9 is updated in the build
  (V13 scope).
- **A3.** The frozen input guard (input/textarea) is **extended** to
  `<select>` and contenteditable elements, so arrow keys over an open
  select change the option instead of panning. Intent-preserving; no
  guard case from the frozen text is removed.
- **A4.** Key handling uses `ev.key`, so numpad `+`/`-` and `=` all work;
  any key with Ctrl/Alt/Meta/Shift held is ignored (the app never fights
  browser zoom or shortcuts).
- **A5.** No controls beyond the frozen set are added: no "0"=fit, no
  wheel/pinch zoom, no drag-to-pan, no `Home`/`End`/`PageUp`/`PageDown`,
  no double-click-to-center.
- **A6.** Pan-step rounding: `max(1, Math.round(0.1 × visible axis cells))`
  (half-up) — the §2.4 table is normative.
- **A7.** Map swap ⇒ `level = fitLevel(new map)`, `pan = (0, 0)` (the
  frozen "re-fit on map swap"); no attempt to preserve a relative
  position between maps.
- **A8.** Resize ⇒ keep level + pan (re-clamped defensively); **no**
  re-fit-to-map on resize (frozen "keep clamped"). Pan ranges are
  cell-based, so they are resize-invariant.
- **A9.** The old fit's `Math.max(8, …)` cell floor is dropped for the map
  canvas under zoom (cell floors at 1 px); at min zoom in very small
  windows cells can be sub-8 px — visible feedback that the map is large,
  consistent with pre-zoom v1 behavior on big maps.
- **A10.** The upload **preview** canvas (`#preview-canvas`, shared
  renderer call site) is out of scope: it keeps self-fit whole-map
  rendering, byte-identical to today.
- **A11.** Docs: README "Limitations (v1)" ("No zoom / pan") and the
  `PROJECT.md` §11 mirror phrase are updated in the build to describe the
  new discrete pan/zoom (V13).
- **A12.** Clipping at the canvas edge is acceptable for awareness rings,
  path polylines, and approximate "?" blocks whose geometry extends
  beyond the window; no special halo-drawing is done.
- **A13.** On < 1024 px the nav panel lives in the sidebar **drawer**
  (opened via the existing ☰ toggle) — no second copy over the canvas;
  the owner's "right-hand control panel" requirement is met by the
  sidebar in its docked (desktop) and drawer (tablet) forms, matching
  wireframes §8.

---

*End of spec — `docs/design/pan-zoom.md`. Build per §1 (V1–V13); verify per
§8 (AC1–AC22).*
