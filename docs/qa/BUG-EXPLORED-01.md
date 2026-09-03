# BUG-EXPLORED-01 — Explored-map tiered grid lines: frontier edges vs hidden cells and the outer canvas frame were never drawn

**Status:** **FIXED** (verified by independent read-only QA review — see
"Verdict / status" below). Fixed in commit `c9d9b83` on branch
`feat/explored-map` ("fix: explored-map grid frontier/frame lines
(BUG-EXPLORED-01) + live QA script; session log").

**Severity:** P2 (moderate — a user-visible, spec-inconsistent rendering
defect in the new explored-map feature; **no** data, movement, awareness, or
crash impact, which is why it is P2 and not P1)
**Component:** Frontend — `app/static/app.js` `drawGridOnCanvas()` tiered
grid-line pass
**Spec reference:** `docs/design/explored-map.md` §6.1 (palette), §6.2 (how
`drawGridOnCanvas` changes — the grid-line rule), §6.3 (call sites)

## Symptom
Observed by the owner in a live browser session on the explored-map feature.
In a **player's** tiered map view, the grid lines around the
explored/seen regions looked wrong:

- **Missing lines on the frontier** between drawn cells (S = in sight, E =
  explored) and hidden (H) cells — the boundary of the lit region was not
  outlined against the dark, so the seen/explored area visually "bleeds" into
  the unexplored background with no crisp edge.
- **Missing outer canvas frame** around the drawn region.

This is inconsistent with spec §6.2, which requires: *"for each cell edge
that has an S/E cell on at least one side, draw the segment at the tier's line
style … a frontier edge against a hidden cell in the drawn cell's own style …
and the outer canvas frame"* — i.e. the drawn region must outline its own
frontier against the dark (H) and keep a frame, while pure-H (H|H) edges stay
undrawn. The existing player-render tests only asserted that *some* full and
*some* dim grid lines were present; none pinned the exact segment set, so the
missing frontier/frame lines went uncaught.

## Root cause
`drawGridOnCanvas`'s tiered (player) mode originally drew grid lines **only
for shared edges** — it emitted a segment for a cell's RIGHT and BOTTOM edge,
and **only when the neighbor on that side was also drawn (S/E)**. Two
consequences:

- A drawn cell bordering a **hidden** cell (frontier) produced **no** line on
  that side — so the lit region had no boundary against the dark.
- A drawn cell on the **grid border** (off-grid neighbor) produced no line —
  so the outer canvas frame over the drawn region was absent.

In short: H cells (and off-grid) contributed nothing, and the only lines drawn
were shared S/E↔S/E internal edges. That contradicts §6.2, which keys the rule
to "an S/E cell on **at least one** side," not "S/E on **both** sides."

## File:line (fixed code, commit c9d9b83)
- `app/static/app.js:551` — `function drawGridOnCanvas(canvas, ctx, visibility = null)`
- `app/static/app.js:563` — `const vis = validateVisibilityMatrix(visibility, g);` (direct callers passing a raw matrix are re-validated; malformed → `null` → full detail)
- `app/static/app.js:573-590` — §1 **null/`!vis` path** (GM + preview): one whole-grid floor `fillRect` + one full-detail grid-line pass — **byte-for-byte the pre-feature behavior, unchanged**
- `app/static/app.js:591-674` — §1 **tiered branch (the fix)**: per-S/E-cell floor fill, then the all-four-edges line pass
- `app/static/app.js:624-670` — the four-edge loop (right / left / bottom / top) that emits every segment
- `app/static/app.js:625` — `lineStyle(full) => full ? T.gridLine : T.gridLineDim` (`#d9d1bd` vs `rgba(217,209,189,0.3)`)
- `app/static/app.js:529` — `layoutCanvas`: `const vis = (state.role === "player") ? state.visibility : null;` (GM gets `null`)
- `app/static/app.js:1694` — `showUploadPreview`: `drawGridOnCanvas(els.previewCanvas, els.previewCanvas.getContext("2d"))` — **no third argument** (preview never receives a matrix)
- `app/static/app.js:485` — `validateVisibilityMatrix` (length/charset guard; malformed → `null`)
- `app/static/app.js:448,461` — `T.gridLine` / `T.gridLineDim` tokens (spec §6.1 values)

## Expected vs actual
- **Expected (spec §6.2):** for every cell edge with an S/E cell on at least
  one side, a 1px segment is drawn — a **shared** edge between two drawn cells
  in that tier's style (a shared **S|E** edge uses the **S** side's full style;
  a shared **E|E** edge stays dim), a **frontier** edge against a hidden cell
  in the drawn cell's **own** style (S → full `#d9d1bd`, E → 30%-alpha dim),
  and the **outer canvas frame** over the drawn border cells. An **H|H** edge
  is never drawn (H contributes nothing). This outlines the seen/explored
  region against the dark and keeps it "pixel-identical to the empty margin"
  only *outside* the drawn cells.
- **Actual (pre-fix):** only shared RIGHT/BOTTOM S/E↔S/E edges were drawn;
  frontier edges vs H and the outer frame were missing.
- **Actual (post-fix):** all four edges of every S/E cell are drawn with the
  rule above — frontier/frame in the drawn cell's own style, shared edges
  S-wins. Verified below.

## Fix (commit `c9d9b83`)
The tiered line pass now draws **all four edges** of **every** S/E cell
(`app/static/app.js:624-670`), deciding the style per edge:

- **Shared edge** (neighbor in-bounds and drawn):
  `lineStyle(t === "S" || <neighbor> === "S")` → full `#d9d1bd` for **S|S** and
  **S|E** (S side wins), dim for **E|E**.
- **Frontier / frame edge** (neighbor is H, **or off-grid**): `own()` =
  `lineStyle(t === "S")` → the drawn cell's **own** style (S → full, E → dim).
- **H cells** `continue` early and contribute no fill and no line.
- The **GM pass** (`!vis`, `app/static/app.js:574-590`) and the **preview
  pass** (`visibility` left undefined → `null`, `app/static/app.js:1694`) are
  **unchanged** — full-detail, byte-identical to pre-feature.

The off-grid (frame) case falls out of the same guards used to detect an H
neighbor: `x+1 < g.width ? tier(x+1,y) : null` (east), `x > 0 ? tier(x-1,y) :
null` (west), `y+1 < g.height ? tier(x,y+1) : null` (south), `y > 0 ?
tier(x,y-1) : null` (north). A `null` neighbor means "frame," which takes the
`own()` branch. **No out-of-range `tier()` call is ever made** (the in-bounds
guard is evaluated before the `tier()` lookup).

## Regression tests
Both drive the **real** `app/static/app.js` under `tests/js/harness.js` with a
**direct** `drawGridOnCanvas(canvas, ctx, matrix)` call on a 5×4 grid on the
800×600 harness canvas (`s = floor(min(800/5, 600/4)) = 150`, origin
`ox = floor((800 - 750)/2) = 25`, `oy = floor((600 - 600)/2) = 0`; grid lines
therefore land on hairlines `gx = 25.5,175.5,325.5,475.5,625.5,775.5` and
`gy = 0.5,150.5,300.5,450.5,600.5`). The `_line_segments` helper
(`tests/test_frontend.py:1485`) records every stroked segment keyed by
geometry (`V<x>:<y>` vertical, `H<x>,<y>,<len>` horizontal) and flags any
segment stroked twice in **different** styles as `"duplicate"`.

- `tests/test_frontend.py::TestExploredMapRender::test_tiered_grid_line_frontier_and_outer_frame`
  (`:1521`) — matrix `["HHHHH","HSHHH","HHHEH","HHHHS"]` (isolated S at
  (1,1), E at (3,2), S at (4,3) — no adjacent drawn pair, so this pins
  **frontier + outer frame** only). Asserts the **exact complete 12-segment
  set** (coordinates **and** stroke style): all four frontier edges of S(1,1)
  full; all four of E(3,2) dim; S(4,3)'s two frontiers + **right/bottom
  frame** full; plus a 20-key list of H|H edges (incl. the all-H row-0 top at
  `y=0.5` and the col-0 left frame) asserted **absent**; and
  `assertNotIn("duplicate", …)`. I independently re-derived all 12 segments
  from the matrix (see Verdict) — the pinned set is exactly correct, and the
  frame correctly sits at `x=775.5`/`y=600.5` over the drawn border cell, not
  at the far grid edge.
- `tests/test_frontend.py::TestExploredMapRender::test_tiered_shared_s_e_edge_is_full`
  (`:1608`) — matrix `["SSSSS","SSSEE","HHHHH","HHHHH"]` (exercises **shared
  edges**). Asserts the **exact complete 27-segment set** (23 full + 4 dim):
  the shared **S|E** boundary at `V475.5:150` is **FULL** (S wins) while the
  shared **E|E** edge at `V625.5:150` stays **DIM**; the E|E bottom-frontier
  and right-frame edges are dim. I independently re-derived all 27 segments
  (15 horizontal + 12 vertical; 23 full + 4 dim) — exactly matches.

Supporting (pre-existing, unchanged by the fix and still green):
`test_player_render_tiers_cells` (`:1449`, S/E floor fills + presence of both
line styles), `test_player_render_no_fill_over_hidden_cell` (`:1662`),
`test_gm_render_full_detail_no_tiers` (`:1691`), `test_preview_render_full_detail`
(`:1712`), `test_draw_grid_on_canvas_with_null_visibility_matches_today`
(`:1736`, asserts the `null` path is one whole-grid 750×600 floor fill with no
grey), and the malformed-matrix guards at `:1377/:1382/:1387`.

## Verification evidence (all post-fix, as recorded for the commit)
- `pytest` → **347 passed**
- `unittest` → **347 OK**
- `scripts/e2e_proof.py` → **101/101** checks (incl. step 9 explored-map
  doorway walk)
- `scripts/qa_explored_map.py` → **39/39** live checks, exit 0 (independent
  S-set re-derivation against the frozen `has_line_of_sight`; asserts
  well-formed matrix, zero-E welcome, S == re-derivation at spawn and post-move,
  S/E→never-H monotonicity, E == explored−S, GM payload has **no** `visibility`
  key, and awareness byte-equal to `build_awareness`)

## Verdict / status
**FIXED — concur.** I independently assessed the fix against spec §6.2 and
traced every corner case the brief asked about. Findings:

1. **Consistent with §6.2.** The line pass implements the "at least one side"
   rule exactly: segment for every edge with an S/E cell on ≥1 side; shared
   S|E → full (S wins); shared E|E → dim; frontier vs H → the drawn cell's
   own style (S full / E dim); frame over the drawn border cells; H|H absent.
   Both pinned matrices re-derive exactly to the asserted sets (12 = 8 full +
   4 dim; 27 = 23 full + 4 dim), so the tests genuinely prove the rule rather
   than a weaker "some lines exist" property.
2. **Off-grid neighbors (border cell).** Each of the four edges uses a
   bounds-guarded lookup that yields `null` off-grid → the `own()` frame
   branch. Correct and index-safe; the S(4,3) right/bottom frame in test 1 is
   the live proof.
3. **Isolated single S cell.** Produces exactly 4 full frontier edges — this is
   precisely the S(1,1) subset pinned in test 1, so the degenerate single-cell
   case (spec E6/E16: token on an otherwise-fully-walled cell) renders as a
   fully-outlined square. Correct.
4. **S/E on the grid border.** Frame drawn in the cell's own style (S → full,
   E → dim); the S case is pinned, the E case is logically the same branch
   (see minor note 1).
5. **Degenerate GM (no matrix).** Confirmed at three levels: `layoutCanvas`
   (`:529`) passes `null` for any non-player; `showUploadPreview` (`:1694`)
   passes no third argument; and `drawGridOnCanvas` (`:563`) re-validates a
   raw matrix so any malformed/absent matrix collapses to `null`. The `!vis`
   branch (`:574-590`) is the untouched whole-grid path, pinned by
   `test_draw_grid_on_canvas_with_null_visibility_matches_today`. GM and
   preview are provably unaffected.
6. **Double-drawn shared edges.** Each drawn cell strokes its own four edges,
   so a shared S|S / S|E / E|E edge is stroked **twice** (once per adjacent
   cell). The style function is **symmetric** in the two tiers
   (`t==="S" || neighbor==="S"`), so both cells compute the **identical**
   style for the shared edge — the double-stroke can never disagree. It is
   therefore harmless (opaque `#d9d1bd` over itself is a no-op; the one
   cosmetic nuance is noted below). The test's invariant — *"no segment drawn
   twice in **different** styles"* — is the **right** one: it tolerates the
   legitimate benign same-style double-stroke while catching the real defect
   class (a style disagreement / a shared edge drawn in the wrong tier's
   style). It is also subsumed by the stronger exact-set `assertEqual(out,
   expected)` (a `"duplicate"` value would make the set mismatch anyway), so it
   is belt-and-suspenders rather than load-bearing. No style divergence is
   reachable.
7. **H|H edges never drawn.** Both cells `continue`; confirmed by the 20-key
   absent list in test 1 (all-H row-0 top at `y=0.5`, col-0 left frame,
   interior H|H boundaries). This is what keeps the unexplored region
   "pixel-identical to the empty margin" per §6.0/§6.2 — i.e. **no** spurious
   full canvas frame is drawn over pure-H spans (a literal full-rectangle frame
   would have violated the "avoids over-drawing H regions" clause). Correct
   interpretation.

### Minor observations (NOT blockers — recorded, not filed)
1. **E-cell outer frame in dim style** is not present in either pinned matrix
   (test 1's frame cell is S; test 2's E border cell's frame, `V775.5:150`, is
   in fact dim and IS covered — so only an E cell on the *top/left* frame in
   dim is unpinned). It follows from the identical `own()` branch the S frame
   uses; low risk.
2. **Isolated single E cell** (a lone explored cell with no token) is not
   pinned — it would draw 4 **dim** frontier edges by the same `own()` branch.
   Spec E6/E16 degenerate cases are S-only, so this is a genuine (small)
   coverage gap, not a defect.
3. **E|E shared-edge double stroke ≈ 51% effective alpha** (two 30% strokes)
   vs a single 30% — a pre-existing cosmetic nuance of the per-cell stroke
   model (the design §6.2 note explicitly allows either per-cell or batched
   single-stroke). Slightly bolder E|E internal lines; invisible on ≥8px
   cells and unchanged by this fix. Not a defect.
4. **No pinned tiered render of the W2 corner-cut grid** (spec AC5 is
   server-side / mask-only and covered by `test_visibility`); orthogonal to the
   line pass.

**Final:** the fix matches spec §6.2, the regression tests prove the exact
segment set + styles + no-style-conflict invariant, the GM/preview/null paths
are untouched, and all reported verification is consistent with the code.
**Status: FIXED.**
