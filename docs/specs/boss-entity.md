# UI Spec — "Boss" Enemy Entity Rendering

**Status:** build-ready spec (atomic — Boss entity rendering only).
**Repo root:** `/Users/agrant3/agentteam`.
**Source of truth:** `PROJECT.md`; conventions from the door-iconography spec
(`docs/design/door-iconography.md`) — status line, assumptions, AC, and §-style
numbering follow its house format.

**Code referenced (read, not modified by the spec):** `app/static/app.js` —
`T` color tokens (`T.enemy`, `T.danger`, `T.dotStroke`, `T.ownRing`), the
`roundRect(ctx, x, y, w, h, r)` helper, entity/dot drawing passes in
`drawGridOnCanvas`, `renderAll()`, `reducedMotion` flag,
`layoutCanvas()` (tile metrics).

---

## 1. What this spec covers

A new enemy entity type: **Boss**. Red-bodied, scaled by total footprint in
grid squares, marked by a black skull icon. This spec defines:

1. The footprint table (size → W×H tiles).
2. The body treatment (color, shape, corner radius, stroke).
3. The skull icon (form, size, exact placement per footprint).
4. E-tier (explored/grayed) rendering.
5. Integration points and acceptance criteria.

---

## 2. Footprint table (concrete tile dimensions)

| Size (total tiles) | W × H (tiles) | Shape   |
|--------------------|---------------|---------|
| 2  | 2 × 1 | Rectangle (horizontal) |
| 4  | 2 × 2 | Square          |
| 6  | 2 × 3 | Rectangle (vertical)  |
| 8  | 2 × 4 | Rectangle (vertical)  |
| 10 | 2 × 5 | Rectangle (vertical)  |
| 12 | 3 × 4 | Rectangle (vertical)  |

**Anchoring & orientation.** The entity is anchored at its **top-left**
tile (W,H) with W = width, H = height as above. Horizontal (2×1) footprints
keep the skull in the left tile so it remains visible in tight corridors;
vertical footprints carry it centered in the top band.

**Grid validity.** A boss may only be placed where its full W×H footprint fits
on the map and on walkable floor (no walls, no partial off-map tiles). A tap or
paint that would result in an overhang is rejected with the house-style error
string (see door spec error style, e.g. `"Boss footprint does not fit"`).
This is a placement-rule note only — the wire-format for multi-tile entities
is out of scope for this atomic spec.

---

## 3. Body treatment

- **Fill:** solid `T.enemy` (`#e03131`) — same red as enemy dots, so identity
  reads at a glance; the footprint scale is the differentiator from mooks.
- **Shape:** a single **one rounded-rectangle blob** spanning the full
  W×H-tile footprint, **not** a grid of rounded cells. Corner radius
  `r = 0.14 × min(W,H)` in tile units (≈ 18.7% of the smaller side) — soft,
  "squircle-adjacent" corners as required ("rounded corners on the grid").
  Draw with the existing `roundRect()` helper (it already clamps via `arcTo`
  and degenerates to a plain rectangle for W or H = 1 tile).
- **Interior grid:** draw `gridLineDim` (30% alpha) 1-px lines across the
  blob interior so the tile division remains legible over the body.
- **Stroke:** 2 px `T.dotStroke` (`#1c2130`) around the blob (same ink the
  enemy dots use) — keeps the silhouette readable on both light and
  explored-tier floors.
- **Layering:** the boss blob is painted in the entity pass, **above** the
  floor/grid layer and **below** the skull icon (skull is topmost).

---

## 4. Skull icon

- **Form:** a black **line skull** drawn on canvas — dome + two circular eye
  sockets + a small nose triangle + two short jaw ticks (≈ 6–8 path
  primitives; no images/SVG assets).
- **Color:** pure black `#111111`, stroke width = 5% of icon size (cap `round`).
  A subtle 1-px `rgba(255,255,255,0.55)` inner highlight is **not** required —
  black-on-red already meets contrast; keep it flat black per the brief.
- **Icon size:** `0.35 × min(tileW, tileH)`, where tileW/tileH are the pixel
  tile metrics from `layoutCanvas()`.

### 4.1 Skull position per footprint (footprint-local coordinates)

Position is a point in the footprint's local tile space:
`(u, v)` where `u ∈ [0..W]`, `v ∈ [0..H]` measured in tiles from the
footprint's top-left corner; the skull is **centered** on that point.

| Size | W×H | Skull center (u, v) | Placement note |
|------|-----|---------------------|----------------|
| 2  | 2×1 | (0.5, 0.5)  | Left tile, vertically centered |
| 4  | 2×2 | (1.0, 0.75)  | Top band, horizontally centered |
| 6  | 2×3 | (1.0, 0.5)   | Top row, horizontally centered |
| 8  | 2×4 | (1.0, 0.5)   | Top row, horizontally centered |
| 10 | 2×5 | (1.0, 0.5)   | Top row, horizontally centered |
| 12 | 3×4 | (1.5, 0.75)  | Top band, horizontally centered |

**Rationale (pinned).** The skull is a *head*, so it rides the **top edge**
of the footprint and stays horizontally centered for all footprints ≥ 4
tiles. The 2×1 exception (left tile) keeps it clear of a right-edge wall in
corridor placements. This is assumption **A2** below.

---

## 5. E-tier (explored / grayed) rendering

Matches the explored-map palette convention (see `T.explored*` tokens):

- Body fill → `T.exploredWall`-gray equivalent: **`#8a5a5e`** (desaturated
  50% red family) — keeps the red *family* recognizable in memory without
  full saturation.
- Interior grid lines → `T.gridLineDim`.
- Skull → **`#6b7280`** (same grey as `T.exploredFloor`), same form/size/
  position as S-tier — the skull mark survives grey-out so a boss's footprint
  is still identifiable in the explored map.
- E-tier **does not** scale the footprint (greyed bosses keep their tiles).

---

## 6. Interaction notes (rendering-adjacent, no behavior change)

- **Selection:** the standard `ownRing`-style selection ring applies to the
  boss's full W×H blob (rounded-rect outline, offset 2 px), not per tile.
- **Motion:** on the `reducedMotion` flag, skip any idle bob/pulse; otherwise
  no animation is defined in this spec (static render) — do not add animation
  without sign-off.
- **Legend:** add one entry to `app/static/index.html` legend: a small red
  rounded square with a black skull + label "Boss (multi-tile)".

---

## 7. Acceptance criteria

- **AC1** Each of the six sizes renders a single rounded blob at exactly the
  §2 tile dimensions (2×1, 2×2, 2×3, 2×4, 2×5, 3×4), anchored top-left.
- **AC2** Corners are rounded at `r = 0.14 × min(W,H)` tiles; the blob is a
  single contour, not 6 individual rounded tiles.
- **AC3** Body fill is `#e03131` (S-tier) / `#8a5a5e` (E-tier) with a 2 px
  `#1c2130` stroke; interior tile grid lines visible at 30% alpha.
- **AC4** A black line skull (≈ 0.35 tile, stroke 5% of icon size) sits at the
  §4.1 local coordinates for **every** size; never clipped by a blob corner.
- **AC5** Explored-tier bosses render greyed body + grey skull at identical
  footprint and skull position.
- **AC6** Placement overhang (any footprint tile off-map or on a wall) is
  rejected; nothing partial is ever drawn.
- **AC7** Legend entry added and matches the in-canvas art.

---

## 8. Out of scope

Wire protocol for multi-tile entities, AI behavior/pathing, collision rules,
sound, and any animation. Those are separate specs.

## 9. Assumptions

- **A1.** Bosses always render on floor tiles; the footprint is always a
  single convex blob (no notched footprints).
- **A2.** Skull placement per §4.1 (top-band centered; 2×1 = left tile) —
  pinned by this spec.
- **A3.** E-tier greys (`#8a5a5e` body, `#6b7280` skull) are the boss
  equivalents of the explored-map palette; final hexes may shift to exact
  `T.explored*` tokens if the door/explored specs add boss-adjacent families.
