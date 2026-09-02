/* ════════════════════════════════════════════════════════════════════
   LittleDungeons — frontend (Iteration 5: live multiplayer over WebSocket)
   Wireframes: docs/design/wireframes.md (tokens, IDs, screen flow).

   Everything is driven by the WebSocket (PROJECT.md §9):
     join (lobby) → welcome → live "state" / "path" / "error" frames.
   The server is authoritative; the client only sends intents
   (join / request_state / move / paint / create_entity /
   delete_entity / set_team / set_fog).
   ════════════════════════════════════════════════════════════════════ */

"use strict";

/* ───────────────────────────── DOM helpers ───────────────────────────── */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const els = {
  // views
  lobbyView: $("#lobby-view"),
  uploadView: $("#upload-view"),
  mapView: $("#map-view"),
  // lobby
  joinName: $("#join-name"),
  joinGm: $("#join-gm"),
  joinPlayer: $("#join-player"),
  lobbyStatus: $("#lobby-status"),
  // upload
  uploadName: $("#upload-name"),
  uploadFile: $("#upload-file"),
  uploadFileName: $("#upload-file-name"),
  uploadCols: $("#upload-cols"),
  uploadRows: $("#upload-rows"),
  darkIsWall: $("#dark-is-wall"),
  uploadForm: $("#upload-form"),
  uploadPreview: $("#upload-preview"),
  btnDetect: $("#btn-detect"),
  btnStartMap: $("#btn-start-map"),
  btnBackTop: $("#btn-back-top"),
  previewImage: $("#preview-image"),
  previewCanvas: $("#preview-canvas"),
  previewThumbnail: $("#preview-thumbnail"),
  uploadNote: $("#upload-note"),
  // map: top bar
  mapName: $("#map-name"),
  mapThumbnail: $("#map-thumbnail"),
  connStatus: $("#conn-status"),
  connLabel: $("#conn-label"),
  fogToggle: $("#fog-toggle"),
  sidebarToggle: $("#sidebar-toggle"),
  btnNewMap: $("#btn-new-map"),
  // map: canvas area
  canvasWrap: $("#canvas-wrap"),
  canvas: $("#map-canvas"),
  legend: $("#legend"),
  coordReadout: $("#coord-readout"),
  canvasHint: $("#canvas-hint"),
  noMap: $("#no-map"),
  toasts: $("#toasts"),
  // map: sidebar
  sidebar: $("#sidebar"),
  awarenessTitle: $("#awareness-title"),
  awarenessList: $("#awareness-list"),
  awarenessSummary: $("#awareness-summary"),
  selEntityName: $("#sel-entity-name"),
  teamSelect: $("#team-select"),
  btnDeleteEntity: $("#btn-delete-entity"),
  newEntityName: $("#new-entity-name"),
  newEntityKind: $("#new-entity-kind"),
  newEntityTeam: $("#new-entity-team"),
  btnNewEntity: $("#btn-new-entity"),
  // map: control bar
  overrideToggle: $("#override-toggle"),
  controlHint: $("#control-hint"),
  scrim: $("#scrim"),
};

/* ───────────────────────────── App state ───────────────────────────── */

const state = {
  joined: false,
  role: null,           // "gm" | "player" — authoritative: welcome.you.role
  you: null,            // {id, name, role, entity_id}
  name: null,
  mapName: null,
  grid: null,           // {width, height, cells} from welcome/state "map"
  cell: 0,              // computed canvas cell size (CSS px)
  offsetX: 0,           // grid origin on canvas (CSS px)
  offsetY: 0,
  entities: [],         // GM: full list; players: [] (server sends [] to players)
  youEntity: null,      // a player's own character (server "you_entity" field)
  awareness: [],        // per-player awareness items (always present)
  players: [],
  fog: false,
  selectedEntityId: null,
  expectCreatedToken: false, // GM "Add" armed: the next state auto-selects the new token
  tool: "select",       // "select" | "floor" | "wall" | "doorway"
  painting: false,
  lastHovered: null,    // last hovered cell (GM entity spawn target)
  animations: {},       // entity_id -> {path, i, timer} for in-flight move anims
  moveRetry: null,      // pending {entity_id, x, y} for the "Move anyway" toast
};

/* ───────────────────────────── WebSocket ───────────────────────────── */

let ws = null;
let wsSession = "default";
let reconnectTimer = null;
let reconnectDelay = 1000;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws?session=${encodeURIComponent(wsSession)}`;
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connectWs() {
  setConn("connecting", "Connecting…");
  // BUG-008: a new connection supersedes any pending reconnect (never two
  // live sockets, never a stray reconnect timer left armed).
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  ws = new WebSocket(wsUrl());
  ws.onopen = () => {
    reconnectDelay = 1000;
    setConn("connected", "Connected");
    // Re-join after a reconnect (the server re-attaches us by name+role).
    if (state.joined && state.you) {
      wsSend({ type: "join", name: state.you.name, role: state.you.role });
    }
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    onServerMessage(msg);
  };
  ws.onclose = () => {
    // Only an *unexpected* drop reconnects. The real BUG-008 fix lives in two
    // places, not in a close flag: (1) openUploadedMap() no longer closes /
    // reconnects the socket — it sends use_map on the SAME socket; and
    // (2) connectWs() clears any pending reconnect timer before opening a new
    // socket. Together they guarantee a deliberate close can never arm a
    // stray, leaked second socket. (The old intentionalClose flag was never
    // assigned true — dead code — and has been removed.)
    if (state.joined) scheduleReconnect();
  };
  ws.onerror = () => {
    // A transport error will fire onclose, which reconnects (an unexpected
    // drop). Closing here does not — and must not — suppress that reconnect.
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  setConn("offline", "Offline");
  const wait = reconnectDelay;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, 10000);
    connectWs();
  }, wait);
}

/* ───────────────────────────── Server → client ───────────────────────────── */

function onServerMessage(msg) {
  switch (msg.type) {
    case "welcome": onWelcome(msg); break;
    case "state": onState(msg); break;
    case "path": onPath(msg); break;
    case "error": onError(msg); break;
  }
}

function onWelcome(msg) {
  state.joined = true;
  state.you = msg.you;
  state.role = msg.you.role;          // the server's word is final
  state.name = msg.you.name;
  state.selectedEntityId = state.role === "player" ? msg.you.entity_id : null;
  els.lobbyStatus.textContent = "";   // BUG-011: clear any prior join error
  document.body.classList.toggle("is-gm", state.role === "gm");
  document.body.classList.toggle("is-player", state.role === "player");
  document.title = `LittleDungeons — ${msg.map ? msg.map.name : "no map"}`;
  els.lobbyView.hidden = true;
  applyState(msg);                     // welcome = state + "you"
  // Sync the selection UI with the assigned selection: players re-assert
  // their own token; the GM (no entity) ends on "None" with the tools
  // disabled.
  selectEntity(state.selectedEntityId);
  showView("map");
  // The GM is a pure controller: welcome copy says so; the player toast is
  // unchanged. (docs/design/gm-controller.md §3.2)
  if (state.role === "gm") {
    toast(`Welcome, ${state.name} — you're the GM. You have no token on the ` +
          `map: create and move tokens for everyone.`);
    // First-run canvas hint (one-time): only for a fresh session with no
    // tokens at all — for 5 s, or until the GM selects or creates a token.
    if ((msg.entities || []).length === 0) showGmFirstRunHint();
  } else {
    toast(`Welcome, ${state.name}.`);
  }
  updateControlHint();
}

function onState(msg) { applyState(msg); }

function applyState(msg) {
  const mapChanged = !state.grid ||
    JSON.stringify(state.grid) !== JSON.stringify(msg.map);
  state.grid = { width: msg.map.width, height: msg.map.height,
                 cells: msg.map.cells };
  state.mapName = msg.map.name;
  // GM "Add" auto-selects the freshly created token (gm-controller spec §3.2/
  // §3.5): diff the roster against what we had when the create was sent.
  const expectCreated = state.role === "gm" && state.expectCreatedToken;
  const prevEntityIds = expectCreated
    ? new Set(state.entities.map((e) => e.id)) : null;
  state.entities = msg.entities || [];       // [] for players
  state.youEntity = msg.you_entity || null;  // own character (players only; GM has none)
  if (prevEntityIds) {
    state.expectCreatedToken = false;
    const fresh = state.entities.find((e) => !prevEntityIds.has(e.id));
    if (fresh) selectEntity(fresh.id);
  }
  // BUG-003: the snapshot arrives with each entity at its FINAL position, but
  // if we are mid-animation we keep the token on the cell it's currently
  // showing (the anim advances it one cell per tick and lands on the goal).
  // This is what makes the token walk instead of teleport/jump-back.
  for (const [eid, a] of Object.entries(state.animations)) {
    const shown = a.path[Math.max(0, a.i - 1)];   // last cell we moved to
    const ent = findEntity(eid);
    if (ent) { ent.x = shown.x; ent.y = shown.y; }
  }
  state.awareness = msg.awareness || [];
  state.players = msg.players || [];
  const fogChanged = state.fog !== msg.fog;
  state.fog = !!msg.fog;
  // The fog toggle is GM-only and stays ENABLED for the GM: fog is applied
  // server-side per viewer, and the GM is role-exempt — so "on" is a no-op
  // render-wise for the GM (gm-controller spec §3.6). The title states the
  // semantics per role.
  els.fogToggle.checked = state.fog;
  els.fogToggle.disabled = state.role !== "gm";
  els.fogToggle.title = state.role === "gm"
    ? "Toggle fog of war for players. As GM you always see everything."
    : "GM controls fog of war";
  document.body.classList.toggle("fog-on", state.fog);
  if (!els.mapView.hidden || mapChanged) {
    if (els.mapView.hidden) els.mapView.hidden = false;
    els.mapName.textContent = state.mapName || "—";
    els.noMap.hidden = true;
    layoutCanvas();
    renderAll();
  } else if (fogChanged) {
    renderAll();
  }
}

function onPath(msg) {
  // Animate the token along the path (120 ms/cell; instant if reduced motion).
  // BUG-003: the server sends the ``path`` frame BEFORE the ``state`` snapshot
  // (whose entities are brand-new objects at the FINAL position). The old code
  // captured a reference to the entity, then ``applyState`` replaced it, so the
  // token snapped to the destination and the animation mutated a detached
  // object. Fix: keep the animation in state (keyed by entity id) and, on each
  // tick, re-look-up the CURRENT entity (via allEntities) and update THAT; the
  // matching ``applyState`` pins an animating entity to the cell it's currently
  // showing so the snapshot's final position never causes a jump-back. The
  // token therefore walks cell-by-cell and lands exactly on the final cell.
  if (reducedMotion) return;
  const pts = msg.path;
  if (!pts || pts.length < 2) return;   // no-op / override single-cell confirm
  const eid = msg.entity_id;
  stopAnim(eid);                          // restart any in-flight anim for it
  const anim = { path: pts, i: 1, timer: null };
  state.animations[eid] = anim;
  const step = () => {
    const a = state.animations[eid];
    if (!a) return;                        // cancelled / replaced
    if (a.i < a.path.length) {
      const ent = findEntity(eid);
      if (ent) { ent.x = a.path[a.i].x; ent.y = a.path[a.i].y; }
      a.i += 1;
      renderAll();
      a.timer = setTimeout(step, 120);
    } else {
      stopAnim(eid);                        // reached the final cell
    }
  };
  anim.timer = setTimeout(step, 120);
}

function stopAnim(eid) {
  const a = state.animations[eid];
  if (a && a.timer) clearTimeout(a.timer);
  delete state.animations[eid];
}

function findEntity(eid) {
  return allEntities().find((e) => e.id === eid) || null;
}

// BUG-003: while an entity is animating a path, further move requests for it
// are dropped (wireframes §4.5 "while animating, ignore further move clicks").
function isAnimating(eid) {
  return !!eid && Object.prototype.hasOwnProperty.call(state.animations, eid);
}

function onError(msg) {
  const m = msg.message || "error";
  // BUG-011: before a welcome we're still on the lobby — #toasts lives in the
  // hidden map view, so a join rejection (e.g. "session full") would be
  // invisible. Surface it in the lobby's status slot instead.
  if (!state.joined) {
    els.lobbyStatus.textContent = m;
    return;
  }
  // The §9 one-shot "Move anyway" (GM only, on a rejected wall-bound move).
  if (state.role === "gm" && m === "no route — wall in the way" && state.moveRetry) {
    const retry = state.moveRetry;
    state.moveRetry = null;
    toast(m, "error", "Move anyway", () => {
      wsSend({ type: "move", entity_id: retry.entity_id, x: retry.x, y: retry.y,
               override: true });
    });
    return;
  }
  state.moveRetry = null;
  toast(m, "error");
}

/* ───────────────────────────── Toasts / hints ───────────────────────────── */

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

function setConn(mode, label) {
  els.connStatus.classList.remove("is-connected", "is-connecting", "is-offline");
  els.connStatus.classList.add(`is-${mode}`);
  els.connLabel.textContent = label;
}

/* ───────────────────────────── View switching ───────────────────────────── */

function showView(view) {
  els.lobbyView.hidden = view !== "lobby";
  els.uploadView.hidden = view !== "upload";
  els.mapView.hidden = view !== "map";
}

/* ───────────────────────────── Lobby ───────────────────────────── */

function syncLobbyButtons() {
  const hasName = els.joinName.value.trim().length > 0;
  els.joinGm.disabled = !hasName;
  els.joinPlayer.disabled = !hasName;
}

function join(role) {
  const name = els.joinName.value.trim();
  if (!name || !ws) return;
  wsSend({ type: "join", name, role });   // the server's welcome drives the UI
}

/* ───────────────────────────── Design tokens (JS copy for canvas) ── */

const T = {
  floor: "#efe9dc",
  gridLine: "#d9d1bd",
  wallFill: "#3b4252",
  wallHatch: "#262b36",
  wallBorder: "#20242f",
  doorway: "#d97706",
  accent: "#4dabf7",
  danger: "#e03131",
  ownRing: "#1971c2",
  ally: "#2f9e44",
  neutralDot: "#f1f3f5",
  enemy: "#e03131",
  dotStroke: "#1c2130",
};

const reducedMotion =
  window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ───────────────────────────── Canvas: layout + shared cell renderer ── */

function layoutCanvas() {
  const wrap = els.canvasWrap;
  const availW = Math.max(0, wrap.clientWidth - 16);
  const availH = Math.max(0, wrap.clientHeight - 16);
  const dpr = window.devicePixelRatio || 1;
  const g = state.grid;
  if (!g || availW <= 0 || availH <= 0) return;

  state.cell = Math.max(8, Math.floor(Math.min(availW / g.width, availH / g.height)));
  const cw = state.cell * g.width;
  const ch = state.cell * g.height;
  state.offsetX = Math.floor((availW - cw) / 2);
  state.offsetY = Math.floor((availH - ch) / 2);

  const canvas = els.canvas;
  canvas.width = Math.round(availW * dpr);
  canvas.height = Math.round(availH * dpr);
  canvas.style.width = `${availW}px`;
  canvas.style.height = `${availH}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Background outside the grid
  ctx.fillStyle = "#171b26";
  ctx.fillRect(0, 0, availW, availH);
  drawGridOnCanvas(canvas, ctx);
}

/* Single cell-renderer shared by #map-canvas and #preview-canvas
   (wireframes §12.7) — floor / wall / doorway look must be identical.
   Self-contained: computes cell size + origin from the canvas itself. */

function drawGridOnCanvas(canvas, ctx) {
  const g = state.grid;
  if (!g) return;
  const dpr = window.devicePixelRatio || 1;
  const availW = Math.max(1, canvas.width / dpr);
  const availH = Math.max(1, canvas.height / dpr);
  const s = Math.max(4, Math.floor(Math.min(availW / g.width, availH / g.height)));
  const ox = Math.floor((availW - s * g.width) / 2);
  const oy = Math.floor((availH - s * g.height) / 2);

  // 1. Floor base + grid lines
  ctx.fillStyle = T.floor;
  ctx.fillRect(ox, oy, s * g.width, s * g.height);
  ctx.strokeStyle = T.gridLine;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x <= g.width; x++) {
    const px = Math.round(ox + x * s) + 0.5;
    ctx.moveTo(px, oy);
    ctx.lineTo(px, oy + g.height * s);
  }
  for (let y = 0; y <= g.height; y++) {
    const py = Math.round(oy + y * s) + 0.5;
    ctx.moveTo(ox, py);
    ctx.lineTo(ox + g.width * s, py);
  }
  ctx.stroke();

  // 2. Walls (fill + diagonal hatch) then doorways
  const hatches = [];
  for (let y = 0; y < g.height; y++) {
    for (let x = 0; x < g.width; x++) {
      if (g.cells[y][x] === "wall") hatches.push([ox + x * s, oy + y * s]);
    }
  }
  ctx.strokeStyle = T.wallHatch;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const [px, py] of hatches) {
    for (let d = -s; d < s; d += Math.max(4, s / 4)) {
      ctx.moveTo(px + d, py + s);
      ctx.lineTo(px + d + s, py);
    }
  }
  ctx.save();
  ctx.beginPath();
  for (const [px, py] of hatches) ctx.rect(px, py, s, s);
  ctx.clip();
  ctx.stroke();
  ctx.restore();

  ctx.strokeStyle = T.wallBorder;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const [px, py] of hatches) ctx.rect(px + 0.5, py + 0.5, s - 1, s - 1);
  ctx.stroke();

  // Doorways: amber border + arch glyph
  for (let y = 0; y < g.height; y++) {
    for (let x = 0; x < g.width; x++) {
      if (g.cells[y][x] !== "doorway") continue;
      const px = ox + x * s;
      const py = oy + y * s;
      ctx.strokeStyle = T.doorway;
      ctx.lineWidth = Math.max(2, Math.min(3, s / 8));
      ctx.strokeRect(px + 1.5, py + 1.5, s - 3, s - 3);
      const r = s * 0.28;
      const cx = px + s / 2;
      const cy = py + s / 2;
      ctx.lineWidth = Math.max(1.5, s / 24);
      ctx.beginPath();
      ctx.moveTo(cx - r, cy + r * 0.8);
      ctx.lineTo(cx - r, cy - r * 0.4);
      ctx.lineTo(cx + r, cy - r * 0.4);
      ctx.lineTo(cx + r, cy + r * 0.8);
      ctx.stroke();
    }
  }

  // 3. Entity tokens (GM / own character) — the #map-canvas pass only
  if (canvas.id === "map-canvas") drawEntitiesAndDots(ctx, s, ox, oy);
}

/* 4. + 5. Tokens, awareness dots, selection, hover, paint preview */
function drawEntitiesAndDots(ctx, s, ox, oy) {
  // Players keep their own entity in a local view so it stays renderable
  // even though the server sends players an empty "entities" list.
  const entities = allEntities();

  // Selection ring (under tokens)
  const sel = entities.find((e) => e.id === state.selectedEntityId);
  if (sel) {
    ctx.strokeStyle = T.accent;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(ox + sel.x * s + s / 2, oy + sel.y * s + s / 2, s * 0.55, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Full tokens for every entity the client controls (GM: all; player: self).
  for (const e of entities) {
    const isOwn = state.you && e.id === state.you.entity_id;
    drawToken(ctx, e, ox, oy, s, {
      ring: isOwn && state.role === "player", // blue "YOU" ring = players only
      label: state.role === "gm" || isOwn,
    });
  }

  // Awareness dots (on top). For the GM these mark team color on the tokens;
  // for players they ARE the view of every other entity (no names).
  const ownId = state.you ? state.you.entity_id : null;
  for (const item of state.awareness) {
    const shape = item.color === "green" ? "tri" : item.color === "white" ? "circle" : "square";
    if (state.role === "gm") {
      drawDot(ctx, ox + item.x * s + s * 0.78, oy + item.y * s + s * 0.22,
              s * 0.16, shape, item.color, 1);
    } else if (item.entity_id !== ownId) {
      drawDot(ctx, ox + item.x * s + s / 2, oy + item.y * s + s / 2,
              s * 0.28, shape, item.color, 1.5);
    }
    // (own entity's awareness item never appears — server excludes it)
  }

  // Hover ring + paint preview
  if (hoverCell) {
    const hx = ox + hoverCell.x * s;
    const hy = oy + hoverCell.y * s;
    if (state.tool !== "select") {
      const fill = state.tool === "wall" ? T.wallFill
                 : state.tool === "doorway" ? T.doorway : T.floor;
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
    let name = null, meta = null;
    if (gm) {
      const e = allEntities().find((x) => x.id === item.entity_id);
      name = item.name || (e ? e.name : null);
      meta = e ? `${e.kind}·${e.team}` : null;
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
  els.awarenessSummary.textContent =
    `${counts.green} ally · ${counts.white} neutral · ${counts.red} enemy`;
}

function awarenessRow(ent, color, own, name = null, meta = null) {
  const li = document.createElement("li");
  li.className = "awareness-row" + (own ? " is-own" : "");
  li.dataset.entityId = ent.id;
  li.tabIndex = 0;

  const dot = document.createElement("span");
  const shape = color === "green" ? "tri" : color === "white" ? "circle" : "square";
  dot.className = `dot dot-${shape} team-${color === "green" ? "party"
    : color === "white" ? "neutral" : "hostile"}` + (own ? " dot-own" : "");
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
function allEntities() {
  if (state.role === "gm") return state.entities;
  return state.youEntity ? [...state.entities, state.youEntity] : state.entities;
}

function entityAtCell(x, y) {
  const g = state.grid;
  if (!g) return null;
  // BUG-007: include the player's own token (youEntity) so "click own token
  // re-asserts selection" works — state.entities is empty for players.
  const all = allEntities();
  return all.find((e) => e && e.x === x && e.y === y) || null;
}

function selectEntity(id) {
  state.selectedEntityId = id;
  if (id) dismissGmFirstRunHint();  // GM chose a token — hint no longer needed
  els.canvasWrap.classList.toggle("has-selection", !!id);
  const e = state.entities.find((x) => x.id === id);
  els.selEntityName.textContent = e ? `${e.name} (${e.kind})` : (id || "None");
  if (e) els.teamSelect.value = e.team;
  syncGmTools();
  renderAll();
}

function sendMove(entityId, x, y, override) {
  // BUG-003: while an entity is mid-animation, ignore further move clicks for
  // it (wireframes §4.5). The in-flight path already ends on a chosen cell.
  if (isAnimating(entityId)) return;
  state.moveRetry = { entity_id: entityId, x, y };   // enables "Move anyway"
  wsSend({ type: "move", entity_id: entityId, x, y, override });
}

let hoverCell = null;

/* ───────────────────────────── Canvas interaction ───────────────────────────── */

function cellFromEvent(ev) {
  const g = state.grid;
  if (!g) return null;
  const rect = els.canvas.getBoundingClientRect();
  const x = Math.floor((ev.clientX - rect.left - state.offsetX) / state.cell);
  const y = Math.floor((ev.clientY - rect.top - state.offsetY) / state.cell);
  if (x < 0 || y < 0 || x >= g.width || y >= g.height) return null;
  return { x, y };
}

els.canvas.addEventListener("pointermove", (ev) => {
  const c = cellFromEvent(ev);
  const prev = hoverCell;
  hoverCell = c;
  state.lastHovered = c;
  els.coordReadout.textContent = c ? `(${c.x}, ${c.y})` : "";
  if (c && state.painting && state.tool !== "select" && state.role === "gm") {
    paintCell(c.x, c.y);
  }
  // Redraw (for hover ring / paint preview) only when something visible
  // changed — full re-renders on every pointermove would be wasteful.
  const paintingChanged = state.painting && state.tool !== "select" &&
    c && (!prev || prev.x !== c.x || prev.y !== c.y);
  if ((!prev !== !c) || (prev && c && (prev.x !== c.x || prev.y !== c.y)) ||
      paintingChanged) {
    requestAnimationFrame(() => { if (!els.mapView.hidden) renderAll(); });
  }
});

els.canvas.addEventListener("pointerleave", () => {
  hoverCell = null;
  els.coordReadout.textContent = "";
});

els.canvas.addEventListener("pointerdown", (ev) => {
  if (state.joined && state.tool !== "select" && state.role === "gm") {
    const c = cellFromEvent(ev);
    if (c) {
      state.painting = true;
      paintCell(c.x, c.y);
      els.canvas.setPointerCapture(ev.pointerId);
    }
  }
});

els.canvas.addEventListener("pointerup", () => { state.painting = false; });

els.canvas.addEventListener("click", (ev) => {
  const c = cellFromEvent(ev);
  if (!c || !state.joined || !state.grid) return;
  const t = state.grid.cells[c.y][c.x];

  // Paint mode (GM): handled on pointerdown; clicks don't move.
  if (state.tool !== "select") return;

  const gm = state.role === "gm";
  const hit = entityAtCell(c.x, c.y);

  // GM: clicking an entity selects it (first tap).
  if (gm && hit) { selectEntity(hit.id); return; }

  // Player clicking their own token: re-assert selection (no move).
  if (!gm && hit && hit.id === state.you.entity_id) { selectEntity(hit.id); return; }

  // Two-tap movement: a selected entity + a destination cell.
  if (state.selectedEntityId) {
    const override = gm ? els.overrideToggle.checked : false;
    if (t === "wall" && !override) {
      canvasHint("Walls block movement" + (gm ? " — enable “Ignore walls”" : ""));
      return;
    }
    sendMove(state.selectedEntityId, c.x, c.y, override);
    return;
  }

  // Player with no explicit selection: always moving their own character.
  if (!gm) {
    if (t === "wall") { canvasHint("Walls block movement"); return; }
    selectEntity(state.you.entity_id);   // select self, then move
    sendMove(state.you.entity_id, c.x, c.y, false);
    return;
  }
  canvasHint("Select an entity, then a tile");
});

/* GM paint (deduped: one message per cell per change). */
let lastPainted = null;
function paintCell(x, y) {
  const key = `${x},${y},${state.tool}`;
  if (lastPainted === key) return;
  lastPainted = key;
  wsSend({ type: "paint", x, y, cell_type: state.tool });
  // optimistic local update (server state reconciles for everyone)
  state.grid.cells[y][x] = state.tool;
}

/* ───────────────────────────── GM tools ───────────────────────────── */

function syncGmTools() {
  const gm = state.role === "gm";
  const sel = state.selectedEntityId
    ? state.entities.find((e) => e.id === state.selectedEntityId) : null;
  els.teamSelect.disabled = !gm || !sel;
  els.btnDeleteEntity.disabled = !gm || !sel;
  els.newEntityName.disabled = !gm;
  els.newEntityKind.disabled = !gm;
  els.newEntityTeam.disabled = !gm;
  els.btnNewEntity.disabled = !gm;
  els.overrideToggle.disabled = !gm;
  if (sel) els.teamSelect.value = sel.team;
}

function firstFreeFloor() {
  const g = state.grid;
  const occupied = new Set(state.entities.map((e) => `${e.x},${e.y}`));
  for (let y = 0; y < g.height; y++) {
    for (let x = 0; x < g.width; x++) {
      if (!occupied.has(`${x},${y}`) &&
          (g.cells[y][x] === "floor" || g.cells[y][x] === "doorway")) {
        return { x, y };
      }
    }
  }
  return { x: 1, y: 1 };
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
    } else {
      hint = `Drag on the map to paint ${state.tool}`;
    }
  } else {
    hint = "Tap a tile to move your character";
  }
  els.controlHint.textContent = hint;
}

function setTool(tool) {
  state.tool = tool;
  lastPainted = null;
  $$("#paint-group .tool-btn").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.tool === tool));
  });
  els.canvasWrap.classList.remove(
    "mode-select", "mode-paint-floor", "mode-paint-wall", "mode-paint-doorway"
  );
  els.canvasWrap.classList.add(
    tool === "select" ? "mode-select" : `mode-paint-${tool}`
  );
  updateControlHint();
}

$("#paint-group").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".tool-btn");
  if (btn) setTool(btn.dataset.tool);
});

els.newEntityName.addEventListener("input", () => {
  // keep the Add button enabled/disabled in sync
  syncGmTools();
});

els.btnNewEntity.addEventListener("click", () => createEntity());

// GM "Add": spawn a token at the last hovered walkable tile (else the first
// free floor) and arm auto-selection so the state broadcast selects it.
function createEntity() {
  if (state.role !== "gm") return;
  const name = els.newEntityName.value.trim() || "entity";
  const kind = els.newEntityKind.value;
  const team = els.newEntityTeam.value;
  const spot = (state.lastHovered &&
                state.grid &&
                (state.grid.cells[state.lastHovered.y][state.lastHovered.x] === "floor" ||
                 state.grid.cells[state.lastHovered.y][state.lastHovered.x] === "doorway"))
    ? state.lastHovered
    : firstFreeFloor();
  wsSend({ type: "create_entity", name, kind, team, x: spot.x, y: spot.y });
  els.newEntityName.value = "";
  state.expectCreatedToken = true;  // the next state selects the new token
  dismissGmFirstRunHint();
}

let deleteConfirming = false;
let deleteTimer = null;
els.btnDeleteEntity.addEventListener("click", () => {
  if (state.role !== "gm" || !state.selectedEntityId) return;
  if (!deleteConfirming) {
    deleteConfirming = true;
    els.btnDeleteEntity.textContent = "Really?";
    deleteTimer = setTimeout(() => {
      deleteConfirming = false;
      els.btnDeleteEntity.textContent = "Delete entity";
    }, 3000);
    return;
  }
  clearTimeout(deleteTimer);
  deleteConfirming = false;
  els.btnDeleteEntity.textContent = "Delete entity";
  const id = state.selectedEntityId;
  wsSend({ type: "delete_entity", entity_id: id });
  selectEntity(null);
});

els.teamSelect.addEventListener("change", () => {
  if (state.role !== "gm" || !state.selectedEntityId) return;
  wsSend({ type: "set_team", entity_id: state.selectedEntityId,
           team: els.teamSelect.value });
});

els.fogToggle.addEventListener("change", () => toggleFog());

// GM fog toggle: GM-only control; "on" filters PLAYERS' snapshots
// server-side. The GM is role-exempt and always sees everything, so this
// changes nothing in the GM's own rendered awareness (spec §3.6).
function toggleFog() {
  if (state.role !== "gm") { els.fogToggle.checked = state.fog; return; }
  wsSend({ type: "set_fog", on: els.fogToggle.checked });
}

/* Keyboard (wireframes §9): arrows move the selected entity one cell;
   Esc deselects / closes the drawer. */
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if (!els.mapView.hidden) selectEntity(null);
    setDrawer(false);
    if (!els.uploadView.hidden) showView("map");
    return;
  }
  if (ev.key === "Enter" || ev.key === " ") {
    const row = ev.target.closest && ev.target.closest(".awareness-row");
    if (row && row.dataset.entityId && state.role === "gm") {
      ev.preventDefault();
      selectEntity(row.dataset.entityId);
      return;
    }
  }
  const dirs = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
  if (dirs[ev.key] && state.joined && state.selectedEntityId &&
      !els.mapView.hidden) {
    ev.preventDefault();
    const e = allEntities().find((x) => x.id === state.selectedEntityId);
    if (!e) return;
    const [dx, dy] = dirs[ev.key];
    sendMove(e.id, e.x + dx, e.y + dy,
             state.role === "gm" ? els.overrideToggle.checked : false);
  }
});

/* ───────────────────────────── Drawer (tablet) ───────────────────────────── */

function setDrawer(open) {
  els.sidebar.classList.toggle("is-open", open);
  els.scrim.hidden = !open;
  els.sidebarToggle.setAttribute("aria-expanded", String(open));
}

els.sidebarToggle.addEventListener("click", () =>
  setDrawer(!els.sidebar.classList.contains("is-open"))
);
els.scrim.addEventListener("click", () => setDrawer(false));

/* ───────────────────────────── Upload view ─────────────────────────────
   FileReader.readAsDataURL(file) → base64 → POST /api/maps/upload (JSON).
   On success: preview the detected grid, then "Open map in session"
   switches back to the live map (the session picks up the latest state —
   the map view simply re-renders on the next state broadcast). */

els.btnNewMap.addEventListener("click", () => {
  showView("upload");
  resetUploadForm();
});
els.btnBackTop.addEventListener("click", () => showView("map"));
$("#btn-back").addEventListener("click", resetUploadForm);
els.btnStartMap.addEventListener("click", openUploadedMap);
els.btnDetect.addEventListener("click", uploadMap);
els.uploadFile.addEventListener("change", () => {
  els.uploadFileName.textContent = els.uploadFile.files.length
    ? els.uploadFile.files[0].name
    : "";
  syncUploadButton();
});

function syncUploadButton() {
  els.btnDetect.disabled = els.uploadFile.files.length === 0;
}

function resetUploadForm() {
  els.uploadView.dataset.state = "idle";
  els.uploadForm.hidden = false;
  els.uploadPreview.hidden = true;
  els.uploadNote.hidden = true;
  els.btnStartMap.disabled = true;
  setUploadBusy(false);
  syncUploadButton();
}

function setUploadBusy(busy, label = "Uploading & detecting…") {
  els.btnDetect.disabled = busy;
  els.btnDetect.textContent = busy ? label : "Upload & detect";
}

function readImageFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error || new Error("could not read the file"));
    reader.readAsDataURL(file);
  });
}

async function uploadMap() {
  const file = els.uploadFile.files[0];
  if (!file) return;
  setUploadBusy(true);
  try {
    const dataUrl = await readImageFile(file);
    const comma = dataUrl.indexOf(",");
    const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : "";
    const body = {
      name: els.uploadName.value.trim() || file.name.replace(/\.[^.]+$/, ""),
      image_b64: b64,
      dark_is_wall: els.darkIsWall.checked,
    };
    const colsVal = els.uploadCols.value;
    const rowsVal = els.uploadRows.value;
    if (colsVal !== "") body.cols = Number(colsVal);
    if (rowsVal !== "") body.rows = Number(rowsVal);

    const resp = await fetch("/api/maps/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `upload failed (HTTP ${resp.status})`);

    state.uploadedMap = {
      id: data.id, name: data.name, width: data.width, height: data.height,
      cells: data.cells, thumbnail: data.thumbnail || null, dataUrl,
    };
    showUploadPreview();
  } catch (err) {
    toast(`Upload failed: ${err.message}`, "error");
    setUploadBusy(false);
  }
}

function showUploadPreview() {
  els.uploadView.dataset.state = "preview";
  els.uploadForm.hidden = true;
  els.uploadPreview.hidden = false;
  const m = state.uploadedMap;
  els.previewImage.src = m.dataUrl;
  els.previewThumbnail.src = m.thumbnail || "";
  els.uploadNote.textContent = `Detected ${m.width}×${m.height} grid — map id “${m.id}”.`;
  els.uploadNote.hidden = false;
  // Render the detected grid on the preview canvas (same shared renderer).
  const saved = state.grid;
  state.grid = { width: m.width, height: m.height, cells: m.cells };
  els.previewCanvas.width = Math.max(120, m.width * 8);
  els.previewCanvas.height = Math.max(90, m.height * 8);
  drawGridOnCanvas(els.previewCanvas, els.previewCanvas.getContext("2d"));
  state.grid = saved;
  els.btnStartMap.disabled = false;
  toast(`Map “${m.name}” detected and registered.`);
}

function openUploadedMap() {
  // BUG-002: do NOT switch the WebSocket session id to the new map's id —
  // that used to strand the players in the old session (only the GM moved).
  // Instead the map is already registered server-side, so ask the CURRENT
  // session to play it. The session (GM-only) swaps its grid in place, re-
  // places any entities that no longer fit, and re-broadcasts; everyone
  // (GM + players, still all in this one session) sees the new map on the
  // next state frame. No reconnect, no wsSession change.
  const m = state.uploadedMap;
  if (!m) return;
  if (state.joined && state.role === "gm") {
    wsSend({ type: "use_map", map_id: m.id });
  }
  showView("map");
  els.mapName.textContent = m.name;
  document.title = `LittleDungeons — ${m.name}`;
  if (state.grid) renderAll();
}

/* ───────────────────────────── Resize (debounced 100 ms) ───────────────────────────── */

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state.grid && !els.mapView.hidden) renderAll();
  }, 100);
});

/* ───────────────────────────── Boot ───────────────────────────── */

els.joinName.addEventListener("input", syncLobbyButtons);
els.joinGm.addEventListener("click", () => join("gm"));
els.joinPlayer.addEventListener("click", () => join("player"));
els.joinName.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !els.joinPlayer.disabled) join("player");
});

setConn("offline", "Offline");
syncLobbyButtons();
showView("lobby");
connectWs();
