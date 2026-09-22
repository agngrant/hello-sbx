/* LittleDungeons frontend — render.js
   Canvas + view rendering, pan/zoom, toasts/hints, legend, sidebar,
   GM-tools / control-hint sync.
   Split from app/static/app.js (bodies verbatim). */


import { T, allEntities, doorStateAt, els, hoverCell, isSafeDoor, safeDoorStateAt, state, validateVisibilityMatrix } from "./state.js";

function toast(message, variant = "info", actionLabel = null, onAction = null) {
  const el = document.createElement("div");
  el.className = variant === "error" ? "toast-error" : "toast";
  const span = document.createElement("span");
  span.textContent = message;
  el.appendChild(span);
  if (actionLabel) {
    const btn = document.createElement("button");
    btn.className = "btn btn-small toast-action";
    btn.textContent = actionLabel;
    btn.addEventListener("click", () => {
      el.remove();
      if (onAction) onAction();
    });
    el.appendChild(btn);
  }
  els.toasts.appendChild(el);
  while (els.toasts.children.length > 3) els.toasts.firstChild.remove();
  setTimeout(() => el.remove(), variant === "error" ? 6000 : 4000);
}

let hintTimer = null;
function canvasHint(message) {
  els.canvasHint.textContent = message;
  els.canvasHint.hidden = false;
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => { els.canvasHint.hidden = true; }, 2000);
}

/* UX guard (post-mortem, door-report mitigation): a map-canvas tap that
   resolves to NOTHING — a letterbox / out-of-window pixel, or a no-op
   target cell — previously vanished silently ("doors do nothing"). These
   hints toast via the standard #toasts path (same mechanism as the
   join/rebind toasts; the container is pointer-events:none, so the toast
   can never swallow a follow-up click). DEBOUNCED: at most one hint toast
   per 2.5 s, so a rapid-fire of mis-clicks (or a held-drag's final click)
   never stacks them. Only called from the click handler's NO-OP exit
   paths — a click that performed its action (door open/close, GM door /
   safe-door tool applied, entity selected, move/spawn, paint) never toasts. */
let _tapHintAt = 0;
function tapHint(message) {
  const now = Date.now();
  if (now - _tapHintAt < 2500) return;
  _tapHintAt = now;
  toast(message);
}

// GM first-run hint (one-time, gm-controller spec §3.2): reuses #canvas-hint
// for 5 s — or until the GM selects or creates a token, whichever comes
// first. Decorative (kept outside aria-live to avoid double-announcing).
let gmFirstRunHintShown = false;
let gmHintTimer = null;
function showGmFirstRunHint() {
  if (gmFirstRunHintShown) return;
  gmFirstRunHintShown = true;
  els.canvasHint.textContent =
    "You're the GM — no token of your own. Add tokens in GM Tools, then " +
    "select one and click a tile to move.";
  els.canvasHint.hidden = false;
  clearTimeout(gmHintTimer);
  gmHintTimer = setTimeout(() => { els.canvasHint.hidden = true; }, 5000);
}
function dismissGmFirstRunHint() {
  if (!gmFirstRunHintShown) return;
  clearTimeout(gmHintTimer);
  els.canvasHint.hidden = true;
}

/* ───────────────────────────── Connection status ───────────────────────────── */

/* Legend door swatches (door-iconography spec §8.1): each `.door-swatch`
   chip in #legend holds a 16×16 <canvas> rendering the ACTUAL map art —
   the S-tier floor base + drawDoorCell(ctx, kind, state, 0, 0, 16, "S") —
   so the legend is pixel-identical to the door it documents. Idempotent
   (one canvas per chip, drawn once); the ONLY production call site is
   showView("map") (P1 join-blocking bug fix: this body reads the `T`
   design tokens declared further down this file, so it must never run
   before `T` exists — a load-time call threw a TDZ ReferenceError that
   aborted boot and left the Join buttons disabled forever). */
function renderLegendDoorSwatches() {
  const legend = els.legend;
  if (!legend) return;
  for (const el of legend.querySelectorAll(".door-swatch")) {
    // Idempotent: skip chips that already hold a swatch canvas (the stub
    // DOM's querySelector is a no-op, so fall back to the children list).
    const hasCanvas = (el.querySelector && el.querySelector("canvas")) ||
      (Array.isArray(el.children) && el.children.length > 0);
    if (hasCanvas) continue;
    const kind = el.dataset.kind;                 // "normal" | "safe"
    const dstate = el.dataset.state;              // "L" | "U" | "O"
    const c = document.createElement("canvas");
    c.width = 16;
    c.height = 16;
    const c2d = c.getContext && c.getContext("2d");
    if (c2d) {
      c2d.fillStyle = T.floor;                    // floor base (S tier)
      c2d.fillRect(0, 0, 16, 16);
      drawDoorCell(c2d, kind, dstate, 0, 0, 16, "S");
    }
    el.appendChild(c);
  }
}

/* Legend boss swatch (boss-entity spec §6/AC7): the `.boss-swatch` chip in
   #legend holds a 20×10 <canvas> rendering the ACTUAL map art at 10px/tile —
   a size-2 boss (2×1 footprint): drawBoss blob + drawSkull at the §4.1
   center — so the legend matches the in-canvas art. Idempotent; the ONLY
   production call site is showView("map") (same T-ordering constraint as
   renderLegendDoorSwatches). */
function renderLegendBossSwatch() {
  const legend = els.legend;
  if (!legend || !legend.querySelector) return;
  const el = legend.querySelector(".boss-swatch");
  if (!el) return;
  const hasCanvas = (el.querySelector && el.querySelector("canvas")) ||
    (Array.isArray(el.children) && el.children.length > 0);
  if (hasCanvas) return;
  const s = 10;   // 2×1 footprint → 20×10 px
  const c = document.createElement("canvas");
  c.width = 2 * s;
  c.height = 1 * s;
  const c2d = c.getContext && c.getContext("2d");
  if (c2d) {
    const fake = { kind: "boss", size: 2, x: 0, y: 0 };
    drawBoss(c2d, fake, s, 0, 0, false);
    const [u, v] = BOSS_SKULL_POS[2];
    drawSkull(c2d, "#111111", u * s, v * s, s * 0.35);
  }
  el.appendChild(c);
}

/* P1 join-blocking bug: `renderLegendDoorSwatches()` must never run before
   the `const T` token table is initialized (its loop reads `T.floor`). The
   old load-time call here threw a TDZ ReferenceError ("Cannot access 'T'
   before initialization") in every real browser, aborting app.js before
   the Join-button listeners further down in this file were ever wired —
   the buttons stayed disabled forever and users could not join. There is
   now NO load-time call: the legend is hidden until a join, and
   showView("map") — the welcome / production call site — draws it at the
   right time, when `T` is long since initialized. ORDERING CONSTRAINT for
   future edits: no top-level statement in this file may read tokens/legend
   before `T` is declared — a throw anywhere in this boot path silently
   strands the user in a lobby whose Join buttons never enable (regression
   guard: tests/test_frontend.py::TestLobbyBootRegression). */

/* ───────────────────────────── Lobby ───────────────────────────── */

function drawDoorCell(ctx, kind, state, px, py, s, t) {
  const E = t === "E";
  const cx = px + s / 2;
  const cy = py + s / 2;
  const margin = Math.max(1, s * 0.10);   // floor-gap frame (floor base shows)
  const slabX = px + margin, slabY = py + margin;
  const slabW = s - 2 * margin, slabH = s - 2 * margin;
  // Tier + family palette (§5.1 S / §5.2 E). The E slab is the SAME grey
  // for both families (greying removes the hue — in memory both are
  // "a door"); the padlock mark + open glow keep the state signals.
  const p = E
    ? { slab: T.eWoodSlab, plank: T.eSlabFrame, frame: T.eSlabFrame,
        glow: T.eLight, lock: T.ePadlockMark, shadow: null, keyhole: false }
    : (kind === "safe")
      ? { slab: T.woodGreen, plank: T.woodGreenDark, frame: T.frameGreen,
          glow: T.lightGreen, lock: null, shadow: T.doorShadow, keyhole: true }
      : { slab: T.woodBrown, plank: T.woodBrownDark, frame: T.frameBrown,
          glow: T.lightYellow, lock: null, shadow: T.doorShadow, keyhole: true };
  if (state === "O") {
    drawDoorOpen(ctx, cx, cy, s, slabX, slabY, slabW, slabH, p);
    return;
  }
  drawDoorClosed(ctx, s, slabX, slabY, slabW, slabH, p, state === "L");
}

/* Closed door (state "L" or "U", both kinds, both tiers, §6.1/§6.2):
   frame + wooden slab + plank seams (s≥12) + inner shadow (S tier, s≥14)
   + the top-right padlock (L only, always — min 4px). */
function drawDoorClosed(ctx, s, slabX, slabY, slabW, slabH, p, locked) {
  // 1. Frame (drawn FIRST, as the slab's outline; the floor inset around
  //    the slab keeps the "floor base under the door" contract visible).
  ctx.lineWidth = Math.max(1, Math.round(s * 0.08));
  ctx.strokeStyle = p.frame;
  ctx.strokeRect(slabX + 0.5, slabY + 0.5, slabW - 1, slabH - 1);
  // 2. Slab fill (the wood). Kept inside the cell (the margin inset).
  ctx.fillStyle = p.slab;
  ctx.fillRect(slabX, slabY, slabW, slabH);
  // 3. Plank seams — dropped at s < 12 (§6.4): one vertical mid-seam +
  //    one horizontal seam read as "wood planks" at ≥12px.
  if (s >= 12) {
    ctx.lineWidth = Math.max(1, s * 0.05);
    ctx.strokeStyle = p.plank;
    ctx.beginPath();
    ctx.moveTo(slabX + slabW / 2, slabY);
    ctx.lineTo(slabX + slabW / 2, slabY + slabH);
    ctx.moveTo(slabX, slabY + slabH * 0.55);
    ctx.lineTo(slabX + slabW, slabY + slabH * 0.55);
    ctx.stroke();
  }
  // 4. Inner shadow (depth; S tier only, s >= 14 — greyed E art has no
  //    shadow). Light-from-top-left convention: bottom + right edges.
  if (p.shadow && s >= 14) {
    ctx.lineWidth = 1;
    ctx.strokeStyle = p.shadow;
    ctx.beginPath();
    ctx.moveTo(slabX, slabY + slabH - 1);
    ctx.lineTo(slabX + slabW, slabY + slabH - 1);
    ctx.moveTo(slabX + slabW - 1, slabY);
    ctx.lineTo(slabX + slabW - 1, slabY + slabH);
    ctx.stroke();
  }
  // 5. Padlock — the LOCKED signal, top-right corner of the slab (§6.1/
  //    §6.3). L and U differ ONLY by this padlock (A7). Its bounding box's
  //    right edge coincides with the slab's right inner edge and its top
  //    edge with the slab's top inner edge, with a breathing gap so the
  //    body never clips the frame.
  if (locked) {
    const padSize = Math.max(4, s * 0.42);
    const padX = slabX + slabW - padSize;
    drawPadlock(ctx, padX, slabY, padSize, p.lock, p.keyhole);
  }
}

/* The CLOSED padlock glyph (shared by both families' "L"; §6.3): a
   brass body with a steel shackle arc whose legs go STRAIGHT DOWN into
   the body top (a locked shackle — no gap). Fill + stroke so it reads at
   8px. `lock` (non-null only in the E tier) recolors body AND shackle to
   one flat mark color (the greyed "locked" signal, §5.2); the keyhole
   appears only when p >= 10 (≈ s >= 24, §6.4). */
function drawPadlock(ctx, x0, y0, psize, lockColor, keyhole) {
  const p = psize;
  const bodyX = x0 + p * 0.22, bodyW = p * 0.56;
  const bodyY = y0 + p * 0.42, bodyH = p * 0.58;
  const shW = p * 0.44, shX0 = x0 + (p - shW) / 2;
  const shTop = y0 + p * 0.12;
  // 1. Shackle first (behind the body top): a thick rounded arc — the two
  //    legs run straight down into the body top (a locked shackle).
  ctx.lineWidth = Math.max(1.5, p * 0.18);
  ctx.strokeStyle = lockColor || T.padlockShackle;
  ctx.beginPath();
  ctx.moveTo(shX0, bodyY);
  ctx.lineTo(shX0, shTop + shW / 2);
  ctx.arc(shX0 + shW / 2, shTop + shW / 2, shW / 2, Math.PI, 0, false);
  ctx.lineTo(shX0 + shW, bodyY);
  ctx.stroke();
  // 2. Body (in front): a rounded brass rect.
  ctx.fillStyle = lockColor || T.padlockBody;
  roundRect(ctx, bodyX, bodyY, bodyW, bodyH, p * 0.12);
  ctx.fill();
  // 3. Keyhole — only when p >= 10 AND the tier allows it (S only; the E
  //    tier keeps the padlock shape but drops the keyhole, §6.3/§6.4).
  if (keyhole && p >= 10) {
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.beginPath();
    ctx.arc(bodyX + bodyW / 2, bodyY + bodyH * 0.38, p * 0.06, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillRect(bodyX + bodyW / 2 - 0.5, bodyY + bodyH * 0.38, 1,
                 bodyH * 0.7 - bodyH * 0.38);
  }
}

/* Open door (state "O", both kinds, both tiers, §6.5): the slab is gone —
   the floor base shows through and the opening is filled with a SOFT
   radial light glow (yellow normal / green safe; greyed near-white at E),
   with an ajar leaf (a sliver of the door's own wood, swung up-left)
   drawn over the glow at s >= 10. The frame is still drawn, so the glow
   reads as "the lit room beyond the door frame" and never bleeds past the
   cell. */
function drawDoorOpen(ctx, cx, cy, s, slabX, slabY, slabW, slabH, p) {
  // 1. Frame boundary (same geometry as the closed slab's frame).
  ctx.lineWidth = Math.max(1, Math.round(s * 0.08));
  ctx.strokeStyle = p.frame;
  ctx.strokeRect(slabX + 0.5, slabY + 0.5, slabW - 1, slabH - 1);
  // 2. Radial light glow, CLIPPED to the slab rect so the light never
  //    bleeds onto neighbors; the transparent outer stop keeps the floor
  //    base visible at the slab's corners (a soft edge, not a hard fill).
  ctx.save();
  ctx.beginPath();
  ctx.rect(slabX, slabY, slabW, slabH);
  ctx.clip();
  const g = ctx.createRadialGradient(cx, cy, s * 0.05, cx, cy, s * 0.62);
  g.addColorStop(0, p.glow);
  g.addColorStop(0.55, p.glow);
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(slabX, slabY, slabW, slabH);
  // 3. Ajar leaf (s >= 10 only, §6.4): a thin parallelogram hinged on the
  //    slab's left edge, swung ~55° up-left into the opening — a sliver of
  //    the slab's own wood, edge-on, so the family color is preserved in
  //    the open state. (At s < 10 the glow alone reads "open / lit".)
  if (s >= 10) {
    const hx = slabX, hy = cy;
    const tipX = slabX + slabW * 0.42, tipY = cy - slabH * 0.30;
    ctx.beginPath();
    ctx.moveTo(hx, hy - slabH * 0.42);
    ctx.lineTo(hx, hy + slabH * 0.42);
    ctx.lineTo(tipX, tipY + slabH * 0.20);
    ctx.lineTo(tipX, tipY - slabH * 0.10);
    ctx.closePath();
    ctx.fillStyle = p.slab;
    ctx.fill();
    ctx.lineWidth = Math.max(1, s * 0.08);
    ctx.strokeStyle = p.frame;
    ctx.stroke();
  }
  ctx.restore();
}

/* ───────────────────────────── Canvas: layout + shared cell renderer ── */

/* ════════════════ Pan & Zoom — view math (pan-zoom spec §2, §5) ════════════════
   A per-client, discrete-zoom, cell-panned viewport: the map can be bigger
   than the viewport, navigated via the sidebar #nav-panel (arrow + zoom
   buttons) and the keyboard. Frontend-only — the view state is NEVER
   serialized or sent to the server (AC21).
   - Discrete levels L0…L10 (§5.1): L0 = 6×5 (max zoom) … L10 = 60×50 (min).
   - Uniform SQUARE cells, letterboxed (§2.3): cell = max(1, floor(min(availW/W, availH/H))).
   - Integer-cell pan, clamped to [0, size − visible] (§2.4); step = 10% of
     the visible axis (min 1, half-up, A6).
   - fit = smallest level whose window covers the map, else L10 + pan (§5.3).
   - One click→cell transform (cellFromEvent, §2.6) and one pixel→draw
     origin (s, ox, oy) — every interaction and every drawn thing shares them.
   The upload PREVIEW canvas keeps its own self-fit math (A10): it calls
   drawGridOnCanvas with NO `view`, which is byte-identical to pre-feature. */

// §5.1 frozen level table: {w: visible cols, h: visible rows} per level.
const LEVELS = [
  { w: 6, h: 5 },    // L0 — max zoom (owner req 6)
  { w: 8, h: 7 },    // L1
  { w: 10, h: 8 },   // L2
  { w: 13, h: 11 },  // L3
  { w: 16, h: 13 },  // L4
  { w: 20, h: 17 },  // L5
  { w: 25, h: 21 },  // L6
  { w: 30, h: 25 },  // L7
  { w: 40, h: 33 },  // L8
  { w: 50, h: 42 },  // L9
  { w: 60, h: 50 },  // L10 — min zoom (owner req 6)
];
const LEVEL_COUNT = LEVELS.length;   // 11 → levels 0..10

const _clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// §5.3 fit rule: smallest L with W(L) ≥ mw AND H(L) ≥ mh, else 10 (E7).
function fitLevel(mw, mh) {
  for (let l = 0; l < LEVEL_COUNT; l++) {
    if (LEVELS[l].w >= mw && LEVELS[l].h >= mh) return l;
  }
  return LEVEL_COUNT - 1;
}

// §2.4 pan step (A6, half-up): max(1, round(0.1 × visible axis cells)).
function viewStep(level, axis) {
  const dim = (axis === "x") ? LEVELS[level].w : LEVELS[level].h;
  return Math.max(1, Math.round(0.1 * dim));
}

// §5.2 pan range: [0, max(0, mapSize − visible)] per axis.
function viewBounds(level, mw, mh) {
  return {
    x: Math.max(0, mw - LEVELS[level].w),
    y: Math.max(0, mh - LEVELS[level].h),
  };
}

// §2.3 + render window: compute the uniform square cell, the letterbox
// offsets (which go NEGATIVE under pan) and the visible [x0,x1)×[y0,y1)
// window, and store them on state (state.cell/offsetX/offsetY + state._view).
// `availW/H` are the wrap's inner pixels (wrap.clientWidth/Height − 16, the
// same margin as today's layout). Guards a degenerate (≤0) area by leaving
// the prior view untouched.
function applyView(availW, availH) {
  const g = state.grid;
  if (!g || availW <= 0 || availH <= 0) return;
  const v = state.view;
  const L = LEVELS[v.level];
  const mw = g.width, mh = g.height;
  // Defensive re-clamp (E2): a prior pan may be out of range at this level.
  const b = viewBounds(v.level, mw, mh);
  v.panX = _clamp(v.panX, 0, b.x);
  v.panY = _clamp(v.panY, 0, b.y);
  const W = L.w, H = L.h;
  const s = Math.max(1, Math.floor(Math.min(availW / W, availH / H)));
  const ox = Math.floor((availW - W * s) / 2) - v.panX * s;
  const oy = Math.floor((availH - H * s) / 2) - v.panY * s;
  state.cell = s;
  state.offsetX = ox;
  state.offsetY = oy;
  state._view = {
    s, ox, oy, W, H,
    x0: v.panX, x1: Math.min(mw, v.panX + W),
    y0: v.panY, y1: Math.min(mh, v.panY + H),
  };
}

// Apply the view math to the CURRENT wrap size (for pan/zoom that run
// outside a full layoutCanvas pass). Reads the live wrap dimensions.
function applyViewNow() {
  const wrap = els.canvasWrap;
  applyView(Math.max(0, wrap.clientWidth - 16),
            Math.max(0, wrap.clientHeight - 16));
}

// §2.5 "fit whole map": level = fitLevel, pan = (0,0) (E1, E3, E7). The
// pixel layout is (re)computed by the layoutCanvas the caller runs next; this
// only fixes the level + pan (and keeps the nav controls in sync).
function fitToMap() {
  const g = state.grid;
  if (!g) return;
  state.view.level = fitLevel(g.width, g.height);
  state.view.panX = 0;
  state.view.panY = 0;
  applyViewNow();
  syncNavControls();
}

// §2.5: pan one step on one axis (clamped, §2.4). The matching arrow key and
// arrow button both call this with the SAME (±1,0)/(0,±1) delta, so their
// effect is identical (owner req 3 / AC3).
function panBy(dx, dy) {
  const g = state.grid;
  if (!g) return;
  const v = state.view;
  const b = viewBounds(v.level, g.width, g.height);
  const nx = _clamp(v.panX + dx * viewStep(v.level, "x"), 0, b.x);
  const ny = _clamp(v.panY + dy * viewStep(v.level, "y"), 0, b.y);
  if (nx === v.panX && ny === v.panY) { syncNavControls(); return; } // silent no-op (E8)
  v.panX = nx;
  v.panY = ny;
  applyViewNow();
  syncNavControls();
  scheduleRender();
}

// §2.5: zoom in/out one level (clamped to [0,10]); the pan is re-clamped for
// the new (possibly smaller) pan range.
// SIGN CONVENTION (bug fix — owner report): `delta` is ZOOM-IN steps. +1 =
// zoom IN one level (bigger cells, FEWER squares visible, MORE detail) and
// decrements the level toward 0; −1 = zoom OUT one level (smaller cells,
// MORE squares visible) and increments the level toward 10. The level model
// is zoom-increasing (L0 = 6×5 most zoomed in … L10 = 60×50 most zoomed
// out), so a zoom-IN moves to a LOWER level index. The `−` button, the `+`
// button, and the `−`/`+`/`=` keys all pass the matching sign through here.
function zoomBy(delta) {
  const g = state.grid;
  if (!g) return;
  const v = state.view;
  const nl = _clamp(v.level - delta, 0, LEVEL_COUNT - 1);
  if (nl === v.level) { syncNavControls(); return; } // silent no-op (E8)
  v.level = nl;
  const b = viewBounds(nl, g.width, g.height);
  v.panX = _clamp(v.panX, 0, b.x);
  v.panY = _clamp(v.panY, 0, b.y);
  applyViewNow();
  syncNavControls();
  scheduleRender();
}

// §3.3 (frozen states): one function sets all six nav buttons' disabled +
// title from the view state. Called after any view mutation, fit, state
// change, or resize. No map → all six disabled.
function syncNavControls() {
  const g = state.grid;
  const btn = (el) => el || { disabled: false, title: "" };
  if (!g) {
    const t = "No map yet";
    for (const el of [els.navUp, els.navDown, els.navLeft, els.navRight,
                      els.zoomIn, els.zoomOut]) {
      const b = btn(el); b.disabled = true; b.title = t;
    }
    if (els.navReadout) els.navReadout.textContent = "";
    return;
  }
  const v = state.view;
  const L = LEVELS[v.level];
  const bx = viewBounds(v.level, g.width, g.height).x;
  const by = viewBounds(v.level, g.width, g.height).y;
  const lockX = g.width <= L.w;    // axis locked: map fits horizontally
  const lockY = g.height <= L.h;
  const set = (el, disabled, title) => { const b = btn(el); b.disabled = disabled; b.title = title; };
  const lockXT = "Map fits horizontally — no pan";
  const lockYT = "Map fits vertically — no pan";
  set(els.navLeft,  lockX || v.panX <= 0,   lockX ? lockXT : "Panned to the west edge");
  set(els.navRight, lockX || v.panX >= bx,  lockX ? lockXT : "Panned to the east edge");
  set(els.navUp,    lockY || v.panY <= 0,   lockY ? lockYT : "Panned to the north edge");
  set(els.navDown,  lockY || v.panY >= by,  lockY ? lockYT : "Panned to the south edge");
  // Disabled at the matching extreme: `−` (zoom out) is dead at L10 — already
  // zoomed out as far as possible (60×50); `+` (zoom in) is dead at L0 —
  // already zoomed in as far as possible (6×5).
  set(els.zoomOut,  v.level >= LEVEL_COUNT - 1, "Fully zoomed out (60×50)");
  set(els.zoomIn,   v.level <= 0, "Fully zoomed in (6×5)");
  // One-line readout: `L{level} · {W}×{H} · ({x0},{y0})–({x1−1},{y1−1}) of {mw}×{mh}`.
  if (els.navReadout) {
    els.navReadout.textContent =
      `L${v.level} · ${L.w}×${L.h} · (${v.panX},${v.panY})–`
      + `(${Math.min(g.width, v.panX + L.w) - 1},${Math.min(g.height, v.panY + L.h) - 1})`
      + ` of ${g.width}×${g.height}`;
  }
}

// §4.3 input focus guard (frozen set INPUT/TEXTAREA, extended A3 to SELECT
// + contenteditable): arrows / + / − over an open field must NOT pan/zoom
// (and must not preventDefault, so native editing is untouched). A real
// BUTTON is never a "field" — native Space/Enter activation is preserved.
function focusInField(t) {
  if (!t) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return t.isContentEditable === true;
}

/* Coalesced render (E8): view mutations mark the frame dirty; at most ONE
   requestAnimationFrame re-render runs per frame, so a 30 Hz key-repeat
   stream never queues unbounded renders. */
let _renderQueued = false;
function scheduleRender() {
  if (_renderQueued) return;
  _renderQueued = true;
  requestAnimationFrame(() => {
    _renderQueued = false;
    if (!els.mapView.hidden) renderAll();
  });
}

/* ───────────────────── Static grid layer (offscreen cache) ─────────────────────
   The STATIC map art — floor / grid lines / walls / doors — depends only on
   the map CONTENT (grid, doors, safe doors), the player's tier matrix
   (state.visibility) and the VIEWPORT (level / pan + canvas size). It does
   NOT depend on entities, awareness, selection or hover — those make up the
   DYNAMIC layer, drawn on top every frame. Painting the static art is the
   expensive part of a frame (O(cells) fills + strokes), so it is cached in
   an offscreen canvas that is rebuilt ONLY when its key changes; each
   layoutCanvas blits the cached layer with a single drawImage and then
   repaints just the dynamic layer. On-screen output is unchanged: the
   blitted layer carries exactly the old drawGridOnCanvas steps 1–2 (same
   draws, same order), and the dynamic pass below is the old step 3.
   Animation ticks (which only move entities) therefore skip the O(cells)
   grid repaint entirely.

   The layer canvas never carries id "map-canvas", so drawGridOnCanvas'
   step 3 (the entity pass) is skipped on it by its own gate. */
let _gridLayer = null;    // { canvas, ctx } — the cached offscreen layer
let _gridLayerKey = null;  // the key string the cached layer was painted for

function gridLayerKey(availW, availH, dpr, vis, view) {
  // Every input the static art depends on. Being sensitive to more than is
  // strictly required can only cost a spurious rebuild (identical art);
  // a stale hit would be a rendering bug, so err on the rebuild side.
  return [availW, availH, dpr, state.role,
          JSON.stringify(state.grid),
          JSON.stringify(state.doors),
          JSON.stringify(state.safe),
          JSON.stringify(vis),
          JSON.stringify(view)].join("|");
}

function ensureGridLayer(availW, availH, dpr, vis, view) {
  const key = gridLayerKey(availW, availH, dpr, vis, view);
  if (_gridLayer && _gridLayerKey === key) return _gridLayer;
  // A fresh canvas per invalidation (never clear + repaint in place) so a
  // stale frame can never leak into the blit.
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(availW * dpr);
  canvas.height = Math.round(availH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawGridOnCanvas(canvas, ctx, vis, view);
  _gridLayer = { canvas, ctx };
  _gridLayerKey = key;
  return _gridLayer;
}

/* The DYNAMIC layer of the map-canvas pass — the old step 3 of
   drawGridOnCanvas, factored out so the map-canvas pass can run it ON TOP
   of the blitted static layer. Identical draws, identical order: one
   footprint blob + skull per visible boss (culled by overlap), then the
   tokens / awareness dots / selection / hover pass. `tier` is the cell-tier
   lookup (the validated matrix's char, or "S" for the GM/no-tier pass). */
function drawEntityLayers(ctx, s, ox, oy, win, tier) {
  // Boss entities (boss-entity spec): one footprint blob + one skull per
  // visible boss — above floor/grid (and walls/doors, which may share the
  // footprint cells), below the awareness dots/tokens that follow. Cull by
  // OVERLAP with the render window (§6.1): a blob whose anchor is just
  // off-window but whose footprint straddles the edge still draws and
  // clips at the canvas edge. Static draw only (no animation), so
  // reducedMotion is honored trivially.
  for (const e of allEntities()) {
    if (e.kind !== "boss") continue;
    const [bw, bh] = bossDims(e);   // spec §2 size → W×H table
    const overlaps = (e.x < win.x1 && e.x + bw > win.x0 &&
                      e.y < win.y1 && e.y + bh > win.y0);
    if (!overlaps) continue;
    const eTier = tier(e.x, e.y) === "E";   // §4.1 colors from the anchor tier
    drawBoss(ctx, e, s, ox, oy, eTier);
    // §4.1 skull center (u, v), footprint-local from the top-left anchor
    // tile — the 6-entry spec table (left tile / top band / top row).
    const [u, v] = BOSS_SKULL_POS[e.size] || [0.5, 0.5];
    drawSkull(ctx, eTier ? "#6b7280" : "#111111",
      ox + (e.x + u) * s, oy + (e.y + v) * s, s * 0.35);
  }
  drawEntitiesAndDots(ctx, s, ox, oy, win);
}

function layoutCanvas() {
  const wrap = els.canvasWrap;
  const availW = Math.max(0, wrap.clientWidth - 16);
  const availH = Math.max(0, wrap.clientHeight - 16);
  const dpr = window.devicePixelRatio || 1;
  const g = state.grid;
  if (!g || availW <= 0 || availH <= 0) return;

  // Pan & zoom (§2.3): the cell size + origin now come from the VIEW state
  // (level/pan), not a fit-to-map. Cells are square; the window is centered
  // with letterboxing and shifted by the integer-cell pan (offsets may go
  // negative under pan). The old Math.max(8,…) floor is dropped (A9).
  applyView(availW, availH);

  const canvas = els.canvas;
  canvas.width = Math.round(availW * dpr);
  canvas.height = Math.round(availH * dpr);
  canvas.style.width = `${availW}px`;
  canvas.style.height = `${availH}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Background outside the window (covers letterbox bars + off-window area)
  ctx.fillStyle = "#171b26";
  ctx.fillRect(0, 0, availW, availH);
  // Explored map (§6.3): the map-canvas pass tiers cells ONLY for a player
  // holding a well-formed visibility matrix. The GM (and any absent/malformed
  // matrix) renders full detail — `null` → the no-tier renderer. The map pass
  // passes the VIEW so the renderer culls to the visible window (§6.1);
  // the preview canvas never passes a view (A10, self-fit whole-map).
  const vis = (state.role === "player") ? state.visibility : null;
  const view = state._view;

  // Static grid layer: rebuild the offscreen cache ONLY when map content /
  // tiering / viewport changed, then blit it — one drawImage instead of
  // O(cells) fills + strokes on every frame (see ensureGridLayer).
  const layer = ensureGridLayer(availW, availH, dpr, vis, view);
  ctx.drawImage(layer.canvas, 0, 0, availW, availH);

  // Dynamic layer: bosses + tokens / awareness / selection / hover, on top
  // of the static art in the exact order the old map-canvas pass (step 3)
  // used. The tier lookup reads the validated state.visibility directly
  // (applyState stores it only validated or null), so boss anchor colors
  // match the static pass.
  const tier = (x, y) => {
    const row = vis ? vis[y] : null;
    return (row ? row[x] : "S");
  };
  drawEntityLayers(ctx, view.s, view.ox, view.oy,
    { x0: view.x0, x1: view.x1, y0: view.y0, y1: view.y1 }, tier);
}

/* Single cell-renderer shared by #map-canvas and #preview-canvas
   (wireframes §12.7) — floor / wall / doorway look must be identical.
   Self-contained: computes cell size + origin from the canvas itself.

   `visibility` (explored map, §6.2) — an OPTIONAL `height×width` matrix of
   "S" / "E" / "H" chars. When it is `null`/`undefined` (the GM pass and the
   upload-preview pass — see §6.3) the renderer runs EXACTLY as before: every
   cell full detail. When a matrix is present (a player's live map), each cell
   is tiered at `visibility[y][x]`:
     "S"  → rendered exactly as today (full detail palette);
     "E"  → greyed (same geometry, desaturated §6.1 palette);
     "H"  → nothing drawn (no fill, no grid line, no wall/door art — the
              canvas background shows through).
   A cell's tier is decided once up-front and honored in EVERY pass, so a
   hidden cell contributes no fill AND no grid line (its grid lines would
   otherwise outline the dark region). The entity/token pass (step 3) is
   untouched — it runs on top exactly as today.

   `view` (pan-zoom, §6) — an OPTIONAL computed view `{s, ox, oy, x0, x1,
   y0, y1}` (from applyView). When passed (the #map-canvas pass ONLY), the
   renderer draws from that origin + cell size and EVERY cell/wall/door loop
   iterates the visible window [x0,x1)×[y0,y1) instead of the full grid —
   culling to the visible cells while the per-cell tier + frontier rules are
   UNCHANGED (`tier(x,y)` is looked up in the full-map matrix regardless).
   When ABSENT (the upload-preview pass), the function is byte-identical to
   pre-feature: it self-fits the WHOLE map with its own s/ox/oy (A10). */

function drawGridOnCanvas(canvas, ctx, visibility = null, view = null) {
  const g = state.grid;
  if (!g) return;
  let s, ox, oy, x0, x1, y0, y1;
  if (view) {
    // Map-canvas pass: geometry + render window come from the view state.
    s = view.s; ox = view.ox; oy = view.oy;
    x0 = view.x0; x1 = view.x1; y0 = view.y0; y1 = view.y1;
  } else {
    // Preview / self-fit pass (A10): whole map, byte-identical to today.
    const dpr = window.devicePixelRatio || 1;
    const availW = Math.max(1, canvas.width / dpr);
    const availH = Math.max(1, canvas.height / dpr);
    s = Math.max(4, Math.floor(Math.min(availW / g.width, availH / g.height)));
    ox = Math.floor((availW - s * g.width) / 2);
    oy = Math.floor((availH - s * g.height) / 2);
    x0 = 0; x1 = g.width; y0 = 0; y1 = g.height;
  }

  // Re-validated here so a direct caller passing a raw matrix (rather than
  // the already-validated state.visibility) can never crash the render.
  const vis = validateVisibilityMatrix(visibility, g);
  const tier = (x, y) => (vis ? vis[y][x] : "S");
  const palette = (t) => (t === "E")
    ? { floor: T.exploredFloor, wallFill: T.exploredWall,
        hatch: T.exploredWallHatch, border: T.exploredWallBorder,
        line: T.gridLineDim }
    : { floor: T.floor, wallFill: T.wallFill,
        hatch: T.wallHatch, border: T.wallBorder,
        line: T.gridLine };

  // ── 1. Floor / floor-tinted base + grid lines ──
  if (!vis) {
    // No tiering (GM / preview): one fill for the rendered region + one
    // grid-line pass. In the map (view) pass the region is the visible
    // window; in the preview pass (no view) it is the whole grid (A10).
    ctx.fillStyle = T.floor;
    ctx.fillRect(ox + x0 * s, oy + y0 * s, (x1 - x0) * s, (y1 - y0) * s);
    ctx.strokeStyle = T.gridLine;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = x0; x <= x1; x++) {
      const px = Math.round(ox + x * s) + 0.5;
      ctx.moveTo(px, oy + y0 * s);
      ctx.lineTo(px, oy + y1 * s);
    }
    for (let y = y0; y <= y1; y++) {
      const py = Math.round(oy + y * s) + 0.5;
      ctx.moveTo(ox + x0 * s, py);
      ctx.lineTo(ox + x1 * s, py);
    }
    ctx.stroke();
  } else {
    // Tiered (player): fill each S/E cell with its tier's floor color (a wall
    // or doorway cell gets the floor base too — its own art overpaints it in
    // step 2). §6.2: EVERY cell edge with a drawn (S/E) cell on at least one
    // side gets its 1px segment — so each drawn cell's four edges are all
    // drawn: a shared edge with a drawn neighbor in that tier's line style
    // (shared S|E edge: the full "S" style wins), a frontier edge against a
    // hidden cell in the drawn cell's OWN style (S edge → #d9d1bd full, E
    // edge → 30%-alpha dimmed), and the outer canvas frame (the top of row
    // 0, the left of col 0, the right of the last col, the bottom of the last
    // row). The explored/seen region thus outlines its frontier against the
    // dark and keeps its frame; an H cell of its own never contributes a
    // line (an H|H edge is not drawn). In the map (view) pass the loops run
    // only over the visible window; a drawn cell at a window edge still sees
    // its OUT-of-window neighbour via tier(x,y) in the full-map matrix, so
    // the 1px frontier edge at the window boundary resolves correctly (§6.1).
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const t = tier(x, y);
        if (t === "H") continue;
        ctx.fillStyle = palette(t).floor;
        ctx.fillRect(ox + x * s, oy + y * s, s, s);
      }
    }
    const gx = (x) => Math.round(ox + x * s) + 0.5;
    const gy = (y) => Math.round(oy + y * s) + 0.5;
    ctx.lineWidth = 1;
    // Each drawn cell's four edges are all drawn — an edge with a drawn
    // neighbor is a SHARED edge (that tier's style, S side wins over E);
    // an edge against a hidden cell or off the grid is a FRONTIER/frame
    // edge and uses the drawn cell's own style.
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const t = tier(x, y);
        if (t === "H") continue;
        const lineStyle = (full) => (full ? T.gridLine : T.gridLineDim);
        const own = () => lineStyle(t === "S");
        // Right edge: against the cell to the east or the right frame.
        {
          const px = gx(x + 1);
          const te = x + 1 < g.width ? tier(x + 1, y) : null;
          ctx.strokeStyle = (te ? lineStyle(t === "S" || te === "S")
                                : own());
          ctx.beginPath();
          ctx.moveTo(px, oy + y * s);
          ctx.lineTo(px, oy + (y + 1) * s);
          ctx.stroke();
        }
        // Left edge: against the cell to the west or the left frame.
        {
          const px = gx(x);
          const tw = x > 0 ? tier(x - 1, y) : null;
          ctx.strokeStyle = (tw ? lineStyle(t === "S" || tw === "S")
                                : own());
          ctx.beginPath();
          ctx.moveTo(px, oy + y * s);
          ctx.lineTo(px, oy + (y + 1) * s);
          ctx.stroke();
        }
        // Bottom edge: against the cell to the south or the bottom frame.
        {
          const py = gy(y + 1);
          const ts = y + 1 < g.height ? tier(x, y + 1) : null;
          ctx.strokeStyle = (ts ? lineStyle(t === "S" || ts === "S")
                                : own());
          ctx.beginPath();
          ctx.moveTo(ox + x * s, py);
          ctx.lineTo(ox + (x + 1) * s, py);
          ctx.stroke();
        }
        // Top edge: against the cell to the north or the top frame.
        {
          const py = gy(y);
          const tn = y > 0 ? tier(x, y - 1) : null;
          ctx.strokeStyle = (tn ? lineStyle(t === "S" || tn === "S")
                                : own());
          ctx.beginPath();
          ctx.moveTo(ox + x * s, py);
          ctx.lineTo(ox + (x + 1) * s, py);
          ctx.stroke();
        }
      }
    }
  }

  // ── 2. Walls (fill + diagonal hatch) then doorways ──
  // Only S/E wall/doorway cells are drawn, each with its tier's palette. We
  // record each visible wall's [px, py, tier] once, then batch the fill, the
  // diagonal hatch, and the border per tier so each tier uses its own colors.
  const walls = [];
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      if (g.cells[y][x] !== "wall") continue;
      const t = tier(x, y);
      if (t === "H") continue;
      walls.push([ox + x * s, oy + y * s, t]);
    }
  }
  for (const wantTier of ["S", "E"]) {
    const sel = walls.filter((w) => w[2] === wantTier);
    if (!sel.length) continue;
    const pal = palette(wantTier);
    // Wall fill (tier's flat grey for "E", the full-detail blue-grey for "S").
    ctx.fillStyle = pal.wallFill;
    for (const [px, py] of sel) ctx.fillRect(px, py, s, s);
    // Diagonal hatch (same texture; the tier's dimmed hatch color for "E").
    ctx.strokeStyle = pal.hatch;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const [px, py] of sel) {
      for (let d = -s; d < s; d += Math.max(4, s / 4)) {
        ctx.moveTo(px + d, py + s);
        ctx.lineTo(px + d + s, py);
      }
    }
    ctx.save();
    ctx.beginPath();
    for (const [px, py] of sel) ctx.rect(px, py, s, s);
    ctx.clip();
    ctx.stroke();
    ctx.restore();
    // Border (the tier's border color; "E" is a flatter, low-contrast grey).
    ctx.strokeStyle = pal.border;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const [px, py] of sel) ctx.rect(px + 0.5, py + 0.5, s - 1, s - 1);
    ctx.stroke();
  }

  // Doors (door-iconography spec §6.6, superseding door-features §7.2 and
  // safe-room doors §7.2): every `doorway` cell is a PICTORIAL wooden door
  // in a state ("L" locked / "U" unlocked / "O" open) — the single
  // drawDoorCell dispatcher replaces the old border+glyph (normal) and
  // border+cross (safe) art. A safe-door cell (recorded in map.safe) takes
  // kind "safe" + safeDoorStateAt (default "L"); every other doorway takes
  // kind "normal" + doorStateAt (default "L") — the partition is total.
  // A door cell is floor-based: it gets its tier's floor base + grid line
  // and NO wall hatch; the door art is drawn OVER that base. Both tiers are
  // state-driven (S: brown/green wood, E: greyed — but the padlock mark
  // survives at E so locked stays readable); the "H" tier is still skipped
  // (a hidden door is not drawn), so GM/preview (no matrix) and a player's
  // S cells render full detail while the player's E cells render greyed.
  // In the map (view) pass only the in-window doorway cells are drawn.
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      if (g.cells[y][x] !== "doorway") continue;
      const t = tier(x, y);
      if (t === "H") continue;
      const px = ox + x * s;
      const py = oy + y * s;
      const kind = isSafeDoor(x, y) ? "safe" : "normal";
      const st = (kind === "safe")
        ? (safeDoorStateAt(x, y) || "L")
        : (doorStateAt(x, y) || "L");
      drawDoorCell(ctx, kind, st, px, py, s, t);
    }
  }

  // 3. Entity tokens (GM / own character) — the #map-canvas pass only.
  //    Unchanged by the explored map. Under pan/zoom (§6.1) the entities are
  //    culled to the visible render window (the single (s, ox, oy) origin).
  //    Factored into drawEntityLayers so the map-canvas pass can run the
  //    SAME draws (same order) on top of its blitted static grid layer.
  if (canvas.id === "map-canvas") {
    drawEntityLayers(ctx, s, ox, oy, { x0, x1, y0, y1 }, tier);
  }
}

/* ───────────────────────────── Awareness rings (canvas, §4) ─────────────────────────────
   docs/design/awareness-ring.md: a subtle dashed square around each player
   token, sized to that player's awareness_radius — the Chebyshev "ball" the
   server uses for the APPROXIMATE (no-line-of-sight) tier. Drawn UNDER the
   tokens (inside drawEntitiesAndDots, before the selection ring) so it never
   covers token art. Render-only: the data is the same players[] / you_entity
   the server already sends, so the normal renderAll path (every state
   broadcast) keeps the rings live. #preview-canvas never calls
   drawEntitiesAndDots (map-canvas only), so the preview stays clean. */
function drawAwarenessRing(ctx, x, y, radius, s, ox, oy) {
  const cx = ox + (x + 0.5) * s;
  const cy = oy + (y + 0.5) * s;
  const half = (radius + 0.5) * s;
  const dash = Math.max(3, s * 0.18);
  ctx.save();
  ctx.fillStyle = "rgba(77, 171, 247, 0.10)";
  ctx.fillRect(cx - half, cy - half, half * 2, half * 2);
  ctx.strokeStyle = T.accent;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([dash, dash]);
  ctx.strokeRect(cx - half, cy - half, half * 2, half * 2);
  ctx.restore();
}

function drawAwarenessRings(ctx, s, ox, oy, win) {
  const players = state.players || [];
  const inWin = (x, y) => !win || (x >= win.x0 && x < win.x1 && y >= win.y0 && y < win.y1);
  if (state.role === "gm") {
    // GM: a ring around every player-owned token (the GM has no token).
    // Per-frame index (entity_id → player row): ONE Map build + O(1) .get()
    // per entity instead of an O(n) players.find scan per entity
    // (O(n²) → O(n)).
    const byEntity = new Map(players.map((pl) => [pl.entity_id, pl]));
    for (const e of state.entities) {
      if (!e.owner) continue;
      if (!inWin(e.x, e.y)) continue;   // §6.1 cull: off-window → skipped
      const p = byEntity.get(e.id);
      const r = p && Number.isFinite(p.awareness_radius)
        ? p.awareness_radius : 4;
      drawAwarenessRing(ctx, e.x, e.y, r, s, ox, oy);
    }
  } else if (state.youEntity) {
    // Player: one ring around their own token, at their own radius.
    if (!inWin(state.youEntity.x, state.youEntity.y)) return;
    const byId = new Map(players.map((pl) => [pl.id, pl]));
    const p = byId.get(state.you.id);
    const r = p && Number.isFinite(p.awareness_radius)
      ? p.awareness_radius : 4;
    drawAwarenessRing(ctx, state.youEntity.x, state.youEntity.y, r, s, ox, oy);
  }
}

/* 4. + 5. Tokens, awareness dots, selection, hover, paint preview.
   `win` (pan-zoom §6.1) — an OPTIONAL render window `{x0,x1,y0,y1}` in MAP
   cells (from state._view). When present (the map-canvas pass), entities /
   awareness items OUTSIDE it are SKIPPED ENTIRELY (no draw calls — AC22):
   a token at (59,59) is not drawn while the L0 window shows (0..5,0..4).
   Everything drawn uses the SAME (s, ox, oy) origin as the grid (§6.2), so
   a marker for (x,y) is centered at (ox + (x+0.5)s, oy + (y+0.5)s) —
   alignment is structurally guaranteed at every level/pan. */
function drawEntitiesAndDots(ctx, s, ox, oy, win) {
  // Players keep their own entity in a local view so it stays renderable
  // even though the server sends players an empty "entities" list.
  const entities = allEntities();
  // Per-frame index (id → entity): O(1) selection lookup instead of an
  // O(n) scan.
  const byId = new Map(entities.map((e) => [e.id, e]));
  const inWin = (x, y) => !win || (x >= win.x0 && x < win.x1 && y >= win.y0 && y < win.y1);

  // Awareness rings (under the tokens; see drawAwarenessRings).
  drawAwarenessRings(ctx, s, ox, oy, win);

  // Selection ring (under tokens). Boss (spec §6): a rounded-rect outline
  // around the FULL W×H blob, offset 2 px — not a per-tile circle.
  const sel = byId.get(state.selectedEntityId);
  if (sel && inWin(sel.x, sel.y)) {
    ctx.strokeStyle = T.accent;
    ctx.lineWidth = 2.5;
    if (sel.kind === "boss") {
      const [bw, bh] = bossDims(sel);
      const r = 0.14 * Math.min(bw, bh) * s + 2;
      roundRect(ctx, ox + sel.x * s - 2, oy + sel.y * s - 2,
                bw * s + 4, bh * s + 4, r);
    } else {
      ctx.beginPath();
      ctx.arc(ox + sel.x * s + s / 2, oy + sel.y * s + s / 2, s * 0.55, 0, Math.PI * 2);
    }
    ctx.stroke();
  }

  // Full tokens for every entity the client controls (GM: all; player:
  // self). Bosses are skipped: they already render as blob + skull in the
  // boss pass above (spec §3 layering) — a token circle here would
  // double-draw the anchor tile.
  for (const e of entities) {
    if (e.kind === "boss") continue;
    if (!inWin(e.x, e.y)) continue;   // §6.1 cull
    const isOwn = state.you && e.id === state.you.entity_id;
    drawToken(ctx, e, ox, oy, s, {
      ring: isOwn && state.role === "player", // blue "YOU" ring = players only
      label: state.role === "gm" || isOwn,
    });
  }

  // Awareness items (on top). For the GM these mark team color on the
  // tokens; for players they ARE the view of every other entity, in three
  // states (server decides per tier):
  //   FULL — line of sight: colored token + NAME LABEL + shape marker
  //          (players now see labels, reusing the GM label rendering);
  //   APPROXIMATE — no line of sight, within 4 squares: a faint gray "?"
  //          at the coarse 2×2 block (no name, no color, no identity);
  //   INVISIBLE — beyond that: the item is simply absent; render nothing.
  const ownId = state.you ? state.you.entity_id : null;
  for (const item of state.awareness) {
    if (state.role === "gm") {
      if (!inWin(item.x, item.y)) continue;   // §6.1 cull
      const shape = item.color === "green" ? "tri" : item.color === "white" ? "circle" : "square";
      drawDot(ctx, ox + item.x * s + s * 0.78, oy + item.y * s + s * 0.22,
              s * 0.16, shape, item.color, 1);
    } else if (item.approximate) {
      // Unknown contact: a coarse 2×2 block, no identity. item.x/item.y is
      // the block's ORIGIN cell (in the server's 2×2-quantized coords), so
      // the block spans map cells [item.x*2, item.x*2+2) × [item.y*2,
      // item.y*2+2). Cull by OVERLAP (§6.1/A12): a block that straddles the
      // window is still drawn and clips at the canvas edge; a fully
      // off-window block is skipped entirely.
      const bx = item.x * 2, by = item.y * 2;
      const overlaps = !win || (bx < win.x1 && bx + 2 > win.x0 &&
                                by < win.y1 && by + 2 > win.y0);
      if (!overlaps) continue;
      drawUnknownDot(ctx, ox + bx * s, oy + by * s, s);
    } else if (item.entity_id !== ownId) {
      if (item.kind === "boss" && item.size != null) {
        // Full-contact BOSS (boss-entity spec): render the true W×H
        // footprint blob + skull EXACTLY like the GM boss pass above —
        // the awareness item now carries the additive `size` field and
        // the client derives W×H from the §2 table. Cull by OVERLAP
        // with the render window (§6.1): an anchor just off-window whose
        // footprint straddles the edge still draws, clipped at the canvas
        // edge. The tier (S full color / E greyed) is read from the
        // player's visibility matrix at the anchor — the same rule the
        // map renderer uses (a boss never draws here without line of
        // sight, so the anchor is normally "S").
        const [bw, bh] = bossDims(item);
        const overlaps = !win || (item.x < win.x1 && item.x + bw > win.x0 &&
                                  item.y < win.y1 && item.y + bh > win.y0);
        if (!overlaps) continue;
        const vrow = state.visibility && state.visibility[item.y];
        const eTier = !!vrow && vrow[item.x] === "E";
        drawBoss(ctx, item, s, ox, oy, eTier);
        const [u, v] = BOSS_SKULL_POS[item.size] || [0.5, 0.5];
        drawSkull(ctx, eTier ? "#6b7280" : "#111111",
          ox + (item.x + u) * s, oy + (item.y + v) * s, s * 0.35);
        continue;
      }
      if (!inWin(item.x, item.y)) continue;   // §6.1 cull
      // Full contact (line of sight): colored token + name label +
      // colorblind shape marker (triangle friend / circle neutral /
      // square enemy), reusing the GM label rendering.
      const colorCss = item.color === "green" ? T.ally
        : item.color === "white" ? T.neutralDot : T.enemy;
      drawToken(ctx,
        { x: item.x, y: item.y, name: item.name || "?", color: colorCss },
        ox, oy, s, { label: item.label !== false });
      const shape = item.color === "green" ? "tri" : item.color === "white" ? "circle" : "square";
      drawDot(ctx, ox + item.x * s + s * 0.78, oy + item.y * s + s * 0.22,
              s * 0.16, shape, colorCss, 1);
    }
    // (own entity's awareness item never appears — server excludes it)
  }

  // Hover ring + paint preview (hoverCell is always an in-window cell —
  // cellFromEvent only returns in-window, in-bounds cells, so no cull here).
  if (hoverCell) {
    const hx = ox + hoverCell.x * s;
    const hy = oy + hoverCell.y * s;
    if (state.tool !== "select") {
      const fill = state.tool === "wall" ? T.wallFill
                 : state.tool === "doorway" ? T.doorway
                 : state.tool === "door" ? T.doorWoodPreview
                 : state.tool === "safeDoor" ? T.safeWoodPreview
                 : T.floor;
      ctx.globalAlpha = 0.5;
      ctx.fillStyle = fill;
      ctx.fillRect(hx, hy, s, s);
      ctx.globalAlpha = 1;
    } else if (state.joined && state.selectedEntityId) {
      const blocked = state.grid.cells[hoverCell.y][hoverCell.x] === "wall";
      ctx.strokeStyle = (!els.overrideToggle.checked && blocked && state.role === "gm")
        ? T.danger : T.accent;
      ctx.lineWidth = 2;
      ctx.strokeRect(hx + 1, hy + 1, s - 2, s - 2);
    }
  }
}

/* ───────────────────────────── Boss entity (boss-entity spec §2/§4) ─────────────────────────────
   The WIRE carries a boss's `size` (total tiles: 2/4/6/8/10/12) and its
   top-left ANCHOR cell only — never W/H. The blob dimensions derive from
   the size → [w, h] tile table (Entity.to_dict omits W/H); the skull
   position from the §4.1 offsets below.

   Footprint table: the SERVER is the single source of truth. Every
   state/welcome frame carries the additive `boss_footprints` field
   (app.models.BOSS_FOOTPRINTS as JSON — stringified size keys → [w, h]
   arrays); applyState stores the validated copy in state.bossFootprints.
   BOSS_FOOTPRINTS_FALLBACK below is kept ONLY for old servers that never
   send the field, and is used whenever the wire value is absent or
   malformed (validateBossFootprints ⇒ null). Every consumer reads the
   table through bossFootprintsTable() — never the constant. */
const BOSS_FOOTPRINTS_FALLBACK = {
  2: [2, 1], 4: [2, 2], 6: [2, 3], 8: [2, 4], 10: [2, 5], 12: [3, 4],
};
/* Defensive validation of the wire's `boss_footprints` field (cf.
   validateDoors / validateSafe / validateVisibilityMatrix): it must be a
   plain object mapping integer size keys ("2", "4", …) to [w, h] arrays
   of positive integers (JSON renders the server's tuples exactly that
   way). Anything else — wrong type, an array, a bad key, a bad value, or
   an empty object — is rejected (null ⇒ the caller keeps the hardcoded
   fallback), so a malformed payload can never crash the render. The key
   set is deliberately NOT pinned to the six known sizes: the server stays
   authoritative, so a NEW size it ever adds must arrive and render, not
   be silently dropped. */
function validateBossFootprints(fp) {
  if (fp == null) return null;
  if (typeof fp !== "object" || Array.isArray(fp)) return null;
  const clean = {};
  for (const key of Object.keys(fp)) {
    if (!/^\d+$/.test(key) || Number(key) < 1) return null;
    const v = fp[key];
    if (!Array.isArray(v) || v.length !== 2) return null;
    const [w, h] = v;
    if (!Number.isInteger(w) || !Number.isInteger(h) ||
        w <= 0 || h <= 0) return null;
    clean[Number(key)] = [w, h];
  }
  return Object.keys(clean).length ? clean : null;
}
/* The live size → [w, h] table: the server-provided state.bossFootprints
   when present (new servers), else the hardcoded fallback (old servers —
   backward compat: an old server never sends the field, so this is a
   no-op for them). */
function bossFootprintsTable() {
  return state.bossFootprints || BOSS_FOOTPRINTS_FALLBACK;
}
/* Spec §4.1: skull center (u, v) in footprint-local tile space from the
   top-left anchor tile — 2×1 rides the LEFT tile; 4/12 the top BAND
   (v=0.75); 6/8/10 the top ROW (v=0.5). */
const BOSS_SKULL_POS = {
  2: [0.5, 0.5], 4: [1.0, 0.75], 6: [1.0, 0.5], 8: [1.0, 0.5],
  10: [1.0, 0.5], 12: [1.5, 0.75],
};
function bossDims(e) {
  return bossFootprintsTable()[e.size] || [1, 1];
}
/* Sidebar / selection readout for a boss's `size` (total tiles): the
   "W×H" footprint text from the §2 table (e.g. size 8 → "2×4"). null
   for a size that is not in the table (graceful degrade: no size text,
   exactly as before this feature). */
function bossFootprintLabel(size) {
  const fp = bossFootprintsTable()[size];
  return fp ? `${fp[0]}×${fp[1]}` : null;
}

/* One boss = one rounded footprint blob + one skull, both pure canvas (no
   assets). drawBoss: a roundRect over the boss's W×H footprint (W/H from
   the §2 size table via bossDims), fill T.enemy (E tier: #8a5a5e),
   2px T.dotStroke stroke, corner radius 0.14×min(W,H) tiles, interior grid
   lines dimmed to 30% alpha (T.gridLineDim) inside the blob. drawSkull:
   a line-skull (dome, two eye sockets, nose triangle, two jaw ticks) in
   #111111 (E tier: #6b7280), size 0.35×min(tileW,tileH) — tiles are square
   (s from the view), so 0.35×s — centered at spec §4.1 (offsets in anchor
   cells). Static draw only (no animation), so reducedMotion needs no
   special handling. */
 function drawBoss(ctx, e, s, ox, oy, eTier) {
   const [W, H] = bossDims(e);
  const px = ox + e.x * s, py = oy + e.y * s;
  const r = 0.14 * Math.min(W, H) * s;
  roundRect(ctx, px, py, W * s, H * s, r);
  ctx.fillStyle = eTier ? "#8a5a5e" : T.enemy;
  ctx.fill();
  // Dim the interior grid lines to 30% alpha (boss spec §2).
  ctx.save();
  roundRect(ctx, px, py, W * s, H * s, r);
  ctx.clip();
  ctx.strokeStyle = T.gridLineDim;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 1; i < W; i++) {
    const gx = Math.round(px + i * s) + 0.5;
    ctx.moveTo(gx, py); ctx.lineTo(gx, py + H * s);
  }
  for (let j = 1; j < H; j++) {
    const gy = Math.round(py + j * s) + 0.5;
    ctx.moveTo(px, gy); ctx.lineTo(px + W * s, gy);
  }
  ctx.stroke();
  ctx.restore();
  ctx.strokeStyle = T.dotStroke;
  ctx.lineWidth = 2;
  roundRect(ctx, px, py, W * s, H * s, r);
  ctx.stroke();
}

function drawSkull(ctx, color, cx, cy, size) {
  const u = size / 16;          // 16-unit grid; ~14.5 units tall
  ctx.save();
   ctx.strokeStyle = color;
   ctx.fillStyle = color;
   ctx.lineWidth = size * 0.05;   // spec §4: stroke = 5% of icon size
   ctx.lineCap = "round";
  ctx.lineJoin = "round";
  // Dome: open-bottom U
  ctx.beginPath();
  ctx.arc(cx, cy - u, 6.5 * u, Math.PI, 0, false);
  ctx.lineTo(cx + 6.5 * u, cy + 5 * u);
  ctx.lineTo(cx - 6.5 * u, cy + 5 * u);
  ctx.closePath();
  ctx.stroke();
  // Two eye sockets
  ctx.beginPath();
  ctx.arc(cx - 3 * u, cy + u, 1.8 * u, 0, Math.PI * 2);
  ctx.arc(cx + 3 * u, cy + u, 1.8 * u, 0, Math.PI * 2);
  ctx.fill();
  // Nose: filled inverted triangle
  ctx.beginPath();
  ctx.moveTo(cx, cy + 2.4 * u);
  ctx.lineTo(cx - 1.4 * u, cy + 4.4 * u);
  ctx.lineTo(cx + 1.4 * u, cy + 4.4 * u);
  ctx.closePath();
  ctx.fill();
  // Two jaw ticks
  ctx.beginPath();
  ctx.moveTo(cx - 2.2 * u, cy + 5 * u);
  ctx.lineTo(cx - 2.2 * u, cy + 7 * u);
  ctx.moveTo(cx + 2.2 * u, cy + 5 * u);
  ctx.lineTo(cx + 2.2 * u, cy + 7 * u);
  ctx.stroke();
  ctx.restore();
}

/* A full entity token: circle + name letter + optional blue ring + label. */
function drawToken(ctx, e, ox, oy, s, opts = {}) {
  const cx = ox + e.x * s + s / 2;
  const cy = oy + e.y * s + s / 2;
  const r = Math.max(4, s * 0.38);
  const color = e.color || (e.team === "party" ? T.ally
                : e.team === "neutral" ? T.neutralDot : T.enemy);

  if (opts.ring) {
    ctx.strokeStyle = T.ownRing;
    ctx.lineWidth = Math.max(2, s * 0.09);
    ctx.beginPath();
    ctx.arc(cx, cy, r + Math.max(3, s * 0.12), 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = color;
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = Math.max(1.5, s * 0.05);
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();

  // One letter of the name
  ctx.fillStyle = color === T.neutralDot ? "#1c2130" : "#10202e";
  ctx.font = `600 ${Math.max(8, s * 0.34)}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const letter = (e.name || "?").trim().charAt(0).toUpperCase();
  ctx.fillText(letter, cx, cy + 0.5);

  if (opts.label) {
    const text = opts.ring ? "YOU" : (e.name || e.id);
    ctx.font = `600 ${Math.max(9, s * 0.28)}px system-ui, sans-serif`;
    const w = ctx.measureText(text).width + 8;
    const ly = cy + r + Math.max(6, s * 0.22);
    ctx.fillStyle = "rgba(23, 27, 38, 0.8)";
    roundRect(ctx, cx - w / 2, ly - s * 0.16, w, s * 0.34, 3);
    ctx.fill();
    ctx.fillStyle = "#eef0f6";
    ctx.fillText(text, cx, ly + 0.5);
  }
}

/* Shape+color awareness marker (never color alone). */
function drawDot(ctx, cx, cy, size, shape, colorCss, strokeW) {
  const r = size;
  ctx.save();
  ctx.strokeStyle = T.dotStroke;
  ctx.lineWidth = Math.max(1, strokeW);
  if (shape === "circle") {
    ctx.fillStyle = colorCss;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  } else if (shape === "square") {
    ctx.fillStyle = colorCss;
    ctx.beginPath();
    ctx.rect(cx - r, cy - r, r * 2, r * 2);
    ctx.fill();
    ctx.stroke();
  } else { // triangle (friend)
    ctx.fillStyle = colorCss;
    ctx.beginPath();
    ctx.moveTo(cx, cy - r * 1.1);
    ctx.lineTo(cx + r, cy + r * 0.85);
    ctx.lineTo(cx - r, cy + r * 0.85);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

/* An APPROXIMATE awareness contact: NO identity — a faint gray "?" circle
   at the CENTER of the coarse 2×2 block the server reports (plus a subtle
   dashed outline of that block).  (qx, qy) is the block's ORIGIN cell, so
   the block spans (qx*2..qx*2+1, qy*2..qy*2+1). */
function drawUnknownDot(ctx, bx, by, s) {
  const cx = bx + s; // center of the 2x2 block
  const cy = by + s;
  const r = Math.max(4, s * 0.3);
  ctx.save();
  // Subtle dashed outline: the reported AREA, not an exact cell.
  ctx.strokeStyle = "rgba(154, 163, 181, 0.45)";
  ctx.setLineDash([Math.max(3, s * 0.15), Math.max(3, s * 0.15)]);
  ctx.lineWidth = 1;
  ctx.strokeRect(bx + 1, by + 1, s * 2 - 2, s * 2 - 2);
  ctx.setLineDash([]);
  // Faint muted marker.
  ctx.globalAlpha = 0.85;
  ctx.fillStyle = T.unknownDot;
  ctx.strokeStyle = T.dotStroke;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = T.dotStroke;
  ctx.font = `700 ${Math.max(9, s * 0.42)}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("?", cx, cy + 0.5);
  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function renderAll() {
  const g = state.grid;
  if (!g || els.mapView.hidden) return;
  layoutCanvas();
  drawSidebar();
  updateControlHint();
  syncGmTools();
  syncNavControls();   // pan-zoom §3.3: keep the six nav states in sync
}

/* ───────────────────────────── Sidebar ───────────────────────────── */

function drawSidebar() {
  const gm = state.role === "gm";
  els.awarenessTitle.textContent = gm
    ? "Tokens — all (GM sees all)"
    : `Awareness — ${state.name}`;

  els.awarenessList.innerHTML = "";
  const counts = { green: 0, white: 0, red: 0 };

  // Players: own character row first (blue-ringed dot, "YOU").
  if (state.role === "player" && state.you && state.youEntity) {
    const li = awarenessRow(state.youEntity, "own", true);
    els.awarenessList.appendChild(li);
  }

  // Per-frame entity index (id → entity): the GM rows used to rescan
  // allEntities() with .find for EVERY awareness item (and re-allocate the
  // array on each item); one Map build + O(1) .get() per row replaces it.
  const eIndex = gm ? new Map(allEntities().map((e) => [e.id, e])) : null;

  for (const item of state.awareness) {
    if (item.color === "green") counts.green++;
    else if (item.color === "white") counts.white++;
    else if (item.color === "red") counts.red++;
    const isOwn = state.you && state.you.entity_id
      ? item.entity_id === state.you.entity_id : false;
    // BUG-006 (historical): only a PLAYER's own item is already rendered by
    // the "own" row block above. GM rows: every entity, no own row (the GM
    // has no entity) — each rendered exactly once.
    if (isOwn && state.role === "player") continue;
    if (item.approximate) {
      // Unknown contact: no name, no color, no kind — a coarse block only.
      const li = awarenessRow(
        { id: item.entity_id, x: item.x, y: item.y },
        "approximate", false, "Unknown", "approximate");
      els.awarenessList.appendChild(li);
      continue;
    }
    let name = null, meta = null;
    if (gm) {
      const e = eIndex.get(item.entity_id);
      name = item.name || (e ? e.name : null);
      meta = e ? `${e.kind}·${e.team}` : null;
      // Boss: append the footprint size (e.g. "boss·hostile · 2×4").
      // The entity's own `size` (the state wire) is authoritative; the
      // awareness item's additive `size` is the fallback.
      if (e && e.kind === "boss" && meta) {
        const fp = bossFootprintLabel(e.size != null ? e.size : item.size);
        if (fp) meta = `${meta} · ${fp}`;
      }
    } else {
      // Full contact (line of sight): the item itself now carries the
      // name + kind (the server sends them) — players see labeled entries.
      name = item.name || null;
      meta = item.kind ? item.kind : null;
      // Boss: append the footprint size (e.g. "boss · 2×4") from the
      // additive awareness `size` field.
      if (item.kind === "boss" && meta) {
        const fp = bossFootprintLabel(item.size);
        if (fp) meta = `${meta} · ${fp}`;
      }
    }
    const li = awarenessRow(
      { id: item.entity_id, x: item.x, y: item.y },
      item.color, false, name, meta);
    els.awarenessList.appendChild(li);
  }

  if (gm && state.awareness.length === 0) {
    // GM token roster is empty (0 tokens: no players, none created).
    const li = document.createElement("li");
    li.className = "awareness-row muted small";
    li.textContent = "No tokens on the map yet — add the first one in GM Tools.";
    els.awarenessList.appendChild(li);
  } else if (!gm && state.awareness.length === 0) {
    // A player whose radar is empty (their own row, if any, is NOT an "other").
    const li = document.createElement("li");
    li.className = "awareness-row muted small";
    li.textContent = "No one else is out there yet.";
    els.awarenessList.appendChild(li);
  }
  const unseen = state.awareness.filter((i) => i.approximate).length;
  els.awarenessSummary.textContent =
    `${counts.green} ally · ${counts.white} neutral · ${counts.red} enemy` +
    (unseen ? ` · ${unseen} unseen` : "");
}

function awarenessRow(ent, color, own, name = null, meta = null) {
  const li = document.createElement("li");
  li.className = "awareness-row" + (own ? " is-own" : "");
  li.dataset.entityId = ent.id;
  li.tabIndex = 0;

  const dot = document.createElement("span");
  if (color === "approximate") {
    // Unknown contact: a distinct muted "?" chip (no team color/shape).
    dot.className = "dot dot-approx";
  } else {
    const shape = color === "green" ? "tri" : color === "white" ? "circle" : "square";
    dot.className = `dot dot-${shape} team-${color === "green" ? "party"
      : color === "white" ? "neutral" : "hostile"}` + (own ? " dot-own" : "");
  }
  li.appendChild(dot);

  const nameEl = document.createElement("span");
  nameEl.className = "awareness-name";
  nameEl.textContent = own ? "YOU" : (name || "");
  li.appendChild(nameEl);

  if (meta) {
    const metaEl = document.createElement("span");
    metaEl.className = "awareness-meta";
    metaEl.textContent = meta;
    li.appendChild(metaEl);
  }

  const coords = document.createElement("span");
  coords.className = "awareness-coords";
  coords.textContent = `(${ent.x}, ${ent.y})`;
  li.appendChild(coords);

  // GM: rows select the entity (same as clicking the token).
  if (state.role === "gm" && !own) {
    li.addEventListener("click", () => selectEntity(ent.id));
    li.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        selectEntity(ent.id);
      }
    });
  }
  if (ent.id === state.selectedEntityId) li.classList.add("is-selected");
  return li;
}

/* ───────────────────────────── Selection / movement ───────────────────────────── */

/* Single source of truth for "which entities can this client render?"
   GM: every entity in state.entities. Player: state.entities (always [] for
   a player, since the server only sends [] ) PLUS their own character
   (state.youEntity), which arrives via you_entity and is a live reference.
   BUG-001: this was referenced but never defined → ReferenceError on the
   first render. */
function selectEntity(id) {
  state.selectedEntityId = id;
  if (id) dismissGmFirstRunHint();  // GM chose a token — hint no longer needed
  els.canvasWrap.classList.toggle("has-selection", !!id);
  const e = state.entities.find((x) => x.id === id);
  if (e) {
    // Boss: include the footprint size (e.g. "Gore (boss · 2×4)").
    const fp = e.kind === "boss" ? bossFootprintLabel(e.size) : null;
    els.selEntityName.textContent =
      `${e.name} (${e.kind}${fp ? ` · ${fp}` : ""})`;
  } else {
    els.selEntityName.textContent = id || "None";
  }
  if (e) els.teamSelect.value = e.team;
  syncGmTools();
  renderAll();
}

/* ───────────────────────────── Canvas interaction ───────────────────────────── */

// The SINGLE click→cell transform (pan-zoom §2.6, frozen floor-divide).
// Every interaction (hover + coord readout, GM lastHovered spawn target, GM
// paint, GM Door/Safe-door tools, player door tap / click-to-move, entity
// hit-testing) resolves through this one function. Under pan/zoom,
// state.offsetX/Y are the view's origin (NEGATIVE when panned) and
// state.cell the view cell size, so this automatically yields the correct
// map cell at every level/pan.
//
// "In-bounds" is the VISIBLE RENDER WINDOW (state._view: [x0,x1)×[y0,y1)),
// which always lies inside the map, so map-bounds are enforced too. A pixel
// in the letterbox bars, in the dark area beyond a small map, or otherwise
// off-window/off-map returns null → all existing no-op guards apply
// unchanged (§6.3/E5: dragging off the window is "no paint", exactly like
// dragging off the map pre-feature — no phantom paints).
function cellFromEvent(ev) {
  const g = state.grid;
  if (!g) return null;
  const w = state._view || { x0: 0, x1: g.width, y0: 0, y1: g.height };
  const rect = els.canvas.getBoundingClientRect();
  const x = Math.floor((ev.clientX - rect.left - state.offsetX) / state.cell);
  const y = Math.floor((ev.clientY - rect.top - state.offsetY) / state.cell);
  if (x < w.x0 || y < w.y0 || x >= w.x1 || y >= w.y1) return null;
  return { x, y };
}

function syncGmTools() {
  const gm = state.role === "gm";
  const sel = state.selectedEntityId
    ? state.entities.find((e) => e.id === state.selectedEntityId) : null;
  els.teamSelect.disabled = !gm || !sel;
  els.btnDeleteEntity.disabled = !gm || !sel;
  els.newEntityName.disabled = !gm;
  els.newEntityKind.disabled = !gm;
  els.newEntityTeam.disabled = !gm;
  // Boss size selector (boss-entity spec §2): live only for a GM with the
  // boss kind armed — the other kinds have no footprint.
  if (els.newEntitySize) {
    els.newEntitySize.disabled = !gm || els.newEntityKind.value !== "boss";
  }
  els.btnNewEntity.disabled = !gm;
  els.overrideToggle.disabled = !gm;
  if (sel) els.teamSelect.value = sel.team;
  // Awareness radius (docs/design/awareness-ring.md §5): GM-only, and only
  // for a selected PLAYER token (owner = the controlling player id). The
  // value reconciles from every state broadcast (authoritative). Guarded: a
  // stub DOM without #awareness-input leaves els.awarenessInput null.
  if (els.awarenessInput) {
    const owner = sel ? sel.owner : null;
    if (gm && owner) {
      const p = state.players.find((pl) => pl.entity_id === sel.id);
      els.awarenessInput.disabled = false;
      els.awarenessInput.value = p && Number.isFinite(p.awareness_radius)
        ? p.awareness_radius : 4;
    } else {
      els.awarenessInput.disabled = true;
    }
  }
}

function updateControlHint() {
  if (!state.joined) return;
  let hint;
  if (state.role === "gm") {
    if (state.tool === "select") {
      if (state.selectedEntityId) {
        hint = `Pick a destination for ${els.selEntityName.textContent.split(" ")[0]}`;
      } else if (state.entities.length === 0) {
        // 0-token roster: point the GM at GM Tools (gm-controller spec §3.4a).
        hint = "No tokens yet — add one in GM Tools.";
      } else {
        hint = "Click an entity to select it, then a tile";
      }
    } else if (state.tool === "door") {
      hint = `Click a door to ${state.doorAction}`;
    } else if (state.tool === "safeDoor") {
      hint = `Click a doorway to ${state.safeAction}`;
    } else {
      hint = `Drag on the map to paint ${state.tool}`;
    }
  } else {
    hint = "Tap a tile to move your character · tap a door to open/close it";
  }
  els.controlHint.textContent = hint;
}

function syncSaveMapStateButton() {
  if (!els.btnSaveMapState) return;
  els.btnSaveMapState.disabled =
    !(state.joined && state.role === "gm" && state.grid);
}

/* ───────────────────────────── Resize (debounced 100 ms) ───────────────────────────── */

export { BOSS_FOOTPRINTS_FALLBACK, BOSS_SKULL_POS, LEVELS, LEVEL_COUNT, _clamp, _gridLayer, _gridLayerKey, _renderQueued, _tapHintAt, applyView, applyViewNow, awarenessRow, bossDims, bossFootprintLabel, bossFootprintsTable, canvasHint, cellFromEvent, dismissGmFirstRunHint, drawAwarenessRing, drawAwarenessRings, drawBoss, drawDoorCell, drawDoorClosed, drawDoorOpen, drawDot, drawEntitiesAndDots, drawEntityLayers, drawGridOnCanvas, drawPadlock, drawSidebar, drawSkull, drawToken, drawUnknownDot, ensureGridLayer, fitLevel, fitToMap, focusInField, gmFirstRunHintShown, gmHintTimer, gridLayerKey, hintTimer, layoutCanvas, panBy, renderAll, renderLegendBossSwatch, renderLegendDoorSwatches, roundRect, scheduleRender, selectEntity, showGmFirstRunHint, syncGmTools, syncNavControls, syncSaveMapStateButton, tapHint, toast, updateControlHint, validateBossFootprints, viewBounds, viewStep, zoomBy };
