/* ════════════════════════════════════════════════════════════════════
   LittleDungeons — frontend (Iteration 5: live multiplayer over WebSocket)
   Wireframes: docs/design/wireframes.md (tokens, IDs, screen flow).

   Everything is driven by the WebSocket (PROJECT.md §9):
     join (lobby) → welcome → live "state" / "path" / "error" frames.
   The server is authoritative; the client only sends intents
   (join / request_state / move / paint / create_entity /
   delete_entity / set_team / set_awareness / set_fog).
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
  // upload view: source tabs + generate form (generated-maps spec §6)
  mapSourceTabs: $("#map-source-tabs"),
  tabUpload: $("#tab-upload"),
  tabGenerate: $("#tab-generate"),
  genForm: $("#gen-form"),
  genName: $("#gen-name"),
  genCols: $("#gen-cols"),
  genRows: $("#gen-rows"),
  genSeed: $("#gen-seed"),
  genNote: $("#gen-note"),
  btnGenerate: $("#btn-generate"),
  previewTitle: $("#preview-title"),
  paneSource: $("#pane-source"),
  paneGridTitle: $("#pane-grid-title"),
  previewNote: $("#preview-note"),
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
  awarenessInput: $("#awareness-input"),
  btnDeleteEntity: $("#btn-delete-entity"),
  newEntityName: $("#new-entity-name"),
  newEntityKind: $("#new-entity-kind"),
  newEntityTeam: $("#new-entity-team"),
  btnNewEntity: $("#btn-new-entity"),
  // map: control bar
  overrideToggle: $("#override-toggle"),
  controlHint: $("#control-hint"),
  doorActionRow: $("#door-action-row"),
  safeActionRow: $("#safe-action-row"),
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
  visibility: null,     // explored map: player's S/E/H tier matrix (null for the
                        //   GM and for a malformed matrix → full-detail render)
  fog: false,
  selectedEntityId: null,
  expectCreatedToken: false, // GM "Add" armed: the next state auto-selects the new token
  tool: "select",       // "select" | "floor" | "wall" | "doorway" | "door" |
                        //   | "safeDoor" (safe-room doors spec §7.5)
  doors: {},            // door feature §7.3: client copy of map.doors,
                        //   "<x>,<y>" -> "L"|"U"|"O" ({} when absent ⇒ all
                        //   doors render locked, the safe default)
  doorAction: "unlock", // armed GM door action (door tool sub-button)
  safe: {},             // safe-room doors spec §7.3: client copy of map.safe,
                        //   "<x>,<y>" -> "C"|"O" ({} when absent ⇒ no safe
                        //   doors — every doorway is a normal door)
  safeAction: "mark",   // armed GM safe-door action (Safe door sub-button)
  painting: false,
  lastHovered: null,    // last hovered cell (GM entity spawn target)
  uploadSource: "upload", // "upload" | "generate" — client-side only (spec §6)
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
  // Explored map (explored-map spec §6.4): store the player's S/E/H tier
  // matrix. Players get it from the server; the GM's payload has no
  // "visibility" key at all, and a malformed matrix (wrong lengths/charset)
  // is treated as absent. The render branch (layoutCanvas) additionally
  // gates on state.role === "player" before drawing with it.
  // state.doors is set in applyState (door-features spec §7.3).
  // state.safe is set in applyState (safe-room doors spec §7.3).
  // Doors: wire the door states (door-features spec §7.3). The payload
  // field is additive: absent (or malformed — wrong type, bad keys,
  // bad state chars) ⇒ {} ⇒ every doorway renders locked. A validated
  // object replaces what we had wholesale, so a door painted away (its
  // key deleted server-side) can never linger in a stale client copy.
  state.doors = validateDoors(msg.map ? msg.map.doors : undefined);
  // Safe doors: wire the safe-door states (safe-room doors spec §7.3) —
  // the SAME additive/defensive pattern as doors: absent (no safe doors)
  // or malformed ⇒ {} ⇒ no safe doors. `map.safe` and `map.doors`
  // partition the doorway cells (the server never puts a cell in both),
  // so a validated replacement keeps the two client copies disjoint.
  state.safe = validateSafe(msg.map ? msg.map.safe : undefined);
  state.visibility = validateVisibilityMatrix(msg.visibility, state.grid);
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
  // Explored map — greyed ("E") palette (spec §6.1). Same art as the full-
  // detail tiers, recolored to a flat grey scale so memory reads as "known,
  // but not in front of me". `gridLineDim` is the full grid line at 30% alpha.
  exploredFloor: "#6b7280",
  exploredWall: "#4b5563",
  exploredWallHatch: "#3f4753",
  exploredWallBorder: "#3f4753",
  exploredDoor: "#8b94a3",   // deprecated alias: === exploredDoorOpen
  // Door states (door-features spec §7.1). A door is a `doorway` cell +
  // a state, rendered floor-based with a state-colored border + glyph
  // (arch = open, bar = closed-unlocked, padlock = closed-locked). All
  // three full-tier colors are distinct from floor (#efe9dc) and wall
  // (#3b4252); the explored variants are the same art desaturated into
  // the grey family (value-distinct from the explored floor #6b7280).
  doorOpen: "#d97706",         // open (O): today's doorway amber + arch
  doorUnlocked: "#f59f00",     // closed, unlocked (U): lighter amber + bar
  doorLocked: "#e03131",       // closed, locked (L): red + padlock glyph
  exploredDoorOpen: "#8b94a3",      // E tier: the explored-door grey
  exploredDoorUnlocked: "#9a8f7a",  // E tier: desaturated amber
  exploredDoorLocked: "#a06b6b",    // E tier: desaturated red
  // Safe-room doors (safe-room doors spec §7.1). A safe door is a
  // `doorway` cell rendered as a GREEN CROSS over the floor base: bright
  // mint green #3ddc84 — deliberately distinct from the party token
  // green #2f9e44 (darker forest; and a CIRCLE vs the CROSS glyph), from
  // the normal-door red/amber family, and from floor/wall. Open and
  // closed share the green — the BAR (present when closed) is the
  // state discriminator, mirroring the normal-door "bar = closed" idiom.
  safeOpen: "#3ddc84",         // open (O): green cross, no bar
  safeClosed: "#3ddc84",       // closed (C): green cross + bar
  exploredSafeOpen: "#8fae9c",     // E tier: desaturated sage green
  exploredSafeClosed: "#8fae9c",   // E tier: desaturated sage green
  gridLineDim: "rgba(217, 209, 189, 0.3)",
  accent: "#4dabf7",
  danger: "#e03131",
  ownRing: "#1971c2",
  ally: "#2f9e44",
  neutralDot: "#f1f3f5",
  enemy: "#e03131",
  unknownDot: "#9aa3b5", // approximate contact (no identity: gray "?")
  dotStroke: "#1c2130",
};

const reducedMotion =
  window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ───────────────────── Explored map: visibility tier matrix ─────────────────────
   docs/design/explored-map.md §4.1 / §6. The server sends every PLAYER a
   "visibility" matrix: `height` row-strings, each exactly `width` chars,
   over the alphabet "S" (seen now — full detail) / "E" (explored — greyed)
   / "H" (hidden — not drawn). The GM's payload has the key ABSENT. A
   well-formed matrix is validated before it is ever used to tier the render;
   anything malformed is treated as absent (null → full-detail), so a bad
   payload can never crash the render. */
const VIS_CHARS = "SEH";
function validateVisibilityMatrix(vis, grid) {
  if (vis == null) return null;
  if (!Array.isArray(vis) || !grid) return null;
  if (vis.length !== grid.height) return null;
  for (let y = 0; y < grid.height; y++) {
    const row = vis[y];
    if (typeof row !== "string" || row.length !== grid.width) return null;
    for (let x = 0; x < row.length; x++) {
      if (VIS_CHARS.indexOf(row[x]) === -1) return null;
    }
  }
  return vis;
}

/* ───────────────────── Doors: state object + per-cell lookup ─────────────
   docs/design/door-features.md §7.3. `map.doors` is an additive wire field:
   an object "<x>,<y>" -> "L" (closed+locked) | "U" (closed, unlocked) |
   "O" (open). A missing key — on a doorway cell — means the door is in the
   DEFAULT state, which is locked ("L"). A malformed payload (wrong type,
   bad keys, bad state chars) is treated as {} (all locked) — defensive,
   never crashes the render (cf. validateVisibilityMatrix). */
const DOOR_STATES = ["L", "U", "O"];
function validateDoors(doors) {
  if (doors == null) return {};
  if (typeof doors !== "object" || Array.isArray(doors)) return {};
  const clean = {};
  for (const key of Object.keys(doors)) {
    const m = /^(\d+),(\d+)$/.exec(key);
    if (!m) return {};
    if (DOOR_STATES.indexOf(doors[key]) === -1) return {};
    clean[key] = doors[key];
  }
  return clean;
}

/* The door state at (x,y): "L"|"U"|"O" for a `doorway` cell (default "L"),
   null for a non-doorway cell (no door to render/act on). Mirrors the
   server's Grid.door_state_at on the client. */
function doorStateAt(x, y) {
  const g = state.grid;
  if (!g || !g.cells) return null;
  const row = g.cells[y];
  if (!row || row[x] !== "doorway") return null;
  return state.doors ? state.doors[`${x},${y}`] || "L" : "L";
}

/* ─────────────────── Safe doors: state object + per-cell lookup ─────────
   safe-room doors spec §7.3. `map.safe` is an ADDITIVE wire field that
   rides inside `map` like `map.doors`: an object "<x>,<y>" -> "C" (closed)
   | "O" (open) covering EVERY safe-door cell (emitted in full whenever ≥ 1
   exists; a missing key ⇒ no safe doors ⇒ every doorway is a NORMAL door).
   `map.safe` and `map.doors` partition the doorway cells server-side
   (a doorway is one kind of door or the other, never both). A malformed
   payload (wrong type, bad keys, bad state chars) is treated as {} —
   defensive, never crashes the render (cf. validateDoors / 
   validateVisibilityMatrix). */
const SAFE_STATES = ["C", "O"];
function validateSafe(safe) {
  if (safe == null) return {};
  if (typeof safe !== "object" || Array.isArray(safe)) return {};
  const clean = {};
  const keyRe = /^[0-9]+,[0-9]+$/;
  for (const key of Object.keys(safe)) {
    if (!keyRe.test(key)) return {};
    if (SAFE_STATES.indexOf(safe[key]) === -1) return {};
    clean[key] = safe[key];
  }
  return clean;
}

/* True iff (x,y) is a `doorway` cell recorded as a safe-room door in
   state.safe (mirrors the server's Grid.is_safe_door). */
function isSafeDoor(x, y) {
  const g = state.grid;
  if (!g || !g.cells) return false;
  const row = g.cells[y];
  if (!row || row[x] !== "doorway") return false;
  return !!state.safe && Object.prototype.hasOwnProperty.call(
    state.safe, `${x},${y}`);
}

/* The safe-door state at (x,y): "C"|"O" for a safe-door cell (default "C"),
   null for a cell that is not a safe door. Mirrors the server's
   Grid.safe_door_state_at on the client. */
function safeDoorStateAt(x, y) {
  if (!isSafeDoor(x, y)) return null;
  return state.safe[`${x},${y}`] || "C";
}

/* The safe-door border/glyph color at visibility tier t (safe-room doors
   spec §7.1): the full-detail mint green #3ddc84 for "S" (GM + preview +
   in-sight — the GM and preview passes have no matrix, so they always take
   this branch), the desaturated sage green #8fae9c for "E" (explored
   memory). Open and closed share the tier's green — the BAR (present when
   closed, see drawSafeDoorGlyph) is the state discriminator, mirroring the
   normal-door "bar = closed" idiom but in green. */
function safeDoorColor(state, t) {
  if (t === "E") {
    if (state === "O") return T.exploredSafeOpen;
    return T.exploredSafeClosed;
  }
  if (state === "O") return T.safeOpen;
  return T.safeClosed;
}

/* The safe-door glyph over the floor base (safe-room doors spec §7.1): a
   centered green CROSS (plus sign) — the "safe room" mark — plus, when
   CLOSED, a horizontal bar across the middle (the "bar = closed" idiom a
   normal door already uses, here in green). The cross + optional bar makes
   open vs closed unmistakable, and the green cross is unmistakably a
   different glyph from a normal door's arch / bar / padlock. `s` = cell
   size in px, (px,py) = cell origin. */
function drawSafeDoorGlyph(ctx, state, px, py, s) {
  const cx = px + s / 2;
  const cy = py + s / 2;
  const r = s * 0.28;   // cross arm extent (same scale as the door glyphs)
  // Cross: two centered strokes.
  ctx.beginPath();
  ctx.moveTo(cx - r, cy);
  ctx.lineTo(cx + r, cy);
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx, cy + r);
  ctx.stroke();
  if (state === "C") {
    // Bar across the middle: the "closed" mark (the cross arms extend past
    // it, so the cell still reads as a cross, now shut — same position and
    // length as the normal-door "U" bar, but drawn over the cross in green).
    const by = cy + r * 0.8;
    ctx.beginPath();
    ctx.moveTo(cx - r, by);
    ctx.lineTo(cx + r, by);
    ctx.stroke();
  }
}

/* The border/glyph color for a NORMAL door state at visibility tier t
   (door-features spec §7.1): the full-detail amber/red family for "S"
   (GM + preview + in-sight), the desaturated grey family for "E".
   (Safe-room doors use safeDoorColor / drawSafeDoorGlyph instead.) */
function doorColor(state, t) {
  if (t === "E") {
    if (state === "O") return T.exploredDoorOpen;
    if (state === "U") return T.exploredDoorUnlocked;
    return T.exploredDoorLocked;
  }
  if (state === "O") return T.doorOpen;
  if (state === "U") return T.doorUnlocked;
  return T.doorLocked;
}

/* The door glyph over the floor base (§7.2): the open door keeps today's
   arch (byte-identical art); a closed-unlocked door draws a centered
   horizontal "bar"; a closed-locked door draws a padlock (bar + a small
   lock notch above it). `s` = cell size in px, (px,py) = cell origin. */
function drawDoorGlyph(ctx, state, px, py, s) {
  const r = s * 0.28;
  const cx = px + s / 2;
  const cy = py + s / 2;
  if (state === "O") {
    // Arch — identical geometry to the pre-feature doorway glyph.
    ctx.beginPath();
    ctx.moveTo(cx - r, cy + r * 0.8);
    ctx.lineTo(cx - r, cy - r * 0.4);
    ctx.lineTo(cx + r, cy - r * 0.4);
    ctx.lineTo(cx + r, cy + r * 0.8);
    ctx.stroke();
    return;
  }
  // Bar — the closed door's center beam.
  const by = cy + r * 0.8;
  ctx.beginPath();
  ctx.moveTo(cx - r, by);
  ctx.lineTo(cx + r, by);
  ctx.stroke();
  if (state === "L") {
    // Padlock notch — a small hook above the bar (distinct from the plain
    // "U" bar, and distinct from the "O" arch, at the 8px min cell size).
    const top = cy - r * 0.6;
    const h = Math.max(2, s * 0.13);
    ctx.beginPath();
    ctx.moveTo(cx, by);
    ctx.lineTo(cx, top + h);
    ctx.moveTo(cx - h, top + h);
    ctx.arc(cx, top + h, h, Math.PI, 0, true);
    ctx.lineTo(cx + h, top);
    ctx.stroke();
  }
}

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
  // Explored map (§6.3): the map-canvas pass tiers cells ONLY for a player
  // holding a well-formed visibility matrix. The GM (and any absent/malformed
  // matrix) renders full detail — `null` → today's renderer, byte-identical.
  const vis = (state.role === "player") ? state.visibility : null;
  drawGridOnCanvas(canvas, ctx, vis);
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
   untouched — it runs on top exactly as today. */

function drawGridOnCanvas(canvas, ctx, visibility = null) {
  const g = state.grid;
  if (!g) return;
  const dpr = window.devicePixelRatio || 1;
  const availW = Math.max(1, canvas.width / dpr);
  const availH = Math.max(1, canvas.height / dpr);
  const s = Math.max(4, Math.floor(Math.min(availW / g.width, availH / g.height)));
  const ox = Math.floor((availW - s * g.width) / 2);
  const oy = Math.floor((availH - s * g.height) / 2);

  // Re-validated here so a direct caller passing a raw matrix (rather than
  // the already-validated state.visibility) can never crash the render.
  const vis = validateVisibilityMatrix(visibility, g);
  const tier = (x, y) => (vis ? vis[y][x] : "S");
  const palette = (t) => (t === "E")
    ? { floor: T.exploredFloor, wallFill: T.exploredWall,
        hatch: T.exploredWallHatch, border: T.exploredWallBorder,
        door: T.exploredDoorOpen, line: T.gridLineDim }
    : { floor: T.floor, wallFill: T.wallFill,
        hatch: T.wallHatch, border: T.wallBorder,
        door: T.doorOpen, line: T.gridLine };

  // ── 1. Floor / floor-tinted base + grid lines ──
  if (!vis) {
    // No tiering (GM / preview): one fill for the whole grid + one grid-line
    // pass — byte-for-byte today's behavior.
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
    // line (an H|H edge is not drawn).
    for (let y = 0; y < g.height; y++) {
      for (let x = 0; x < g.width; x++) {
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
    for (let y = 0; y < g.height; y++) {
      for (let x = 0; x < g.width; x++) {
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
  for (let y = 0; y < g.height; y++) {
    for (let x = 0; x < g.width; x++) {
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

  // Doors (door-features spec §7.2): every `doorway` cell is a door in a
  // state ("L" locked / "U" unlocked / "O" open, §7.3 — absent entry ⇒
  // locked). A door cell is floor-based: it gets its tier's floor base +
  // grid line and NO wall hatch; the state decides the border/glyph color
  // and the glyph (arch = open, bar = closed-unlocked, padlock =
  // closed-locked). Both tiers are state-driven; the "H" tier is still
  // skipped (a hidden door is not drawn), so GM/preview (no matrix) and a
  // player's S cells render full detail while the player's E cells render
  // the desaturated grey variants.
  //
  // SAFE-ROOM DOORS (safe-room doors spec §7.2) — a `doorway` cell recorded
  // in map.safe is a SAFE door, not a normal door: it renders the GREEN
  // CROSS art (safeDoorColor/drawSafeDoorGlyph — green border + cross, plus
  // a bar when closed, per tier) and SKIPS the normal-door branch (which
  // stays byte-for-byte unchanged for every non-safe doorway). Safe and
  // normal doors partition the doorway cells (map.safe ∩ map.doors = ∅),
  // so the kind check first is total: a cell takes exactly one branch.
  for (let y = 0; y < g.height; y++) {
    for (let x = 0; x < g.width; x++) {
      if (g.cells[y][x] !== "doorway") continue;
      const t = tier(x, y);
      if (t === "H") continue;
      const px = ox + x * s;
      const py = oy + y * s;
      if (isSafeDoor(x, y)) {
        const sst = safeDoorStateAt(x, y) || "C";
        ctx.strokeStyle = safeDoorColor(sst, t);
        ctx.lineWidth = Math.max(2, Math.min(3, s / 8));
        ctx.strokeRect(px + 1.5, py + 1.5, s - 3, s - 3);
        ctx.lineWidth = Math.max(1.5, s / 24);
        drawSafeDoorGlyph(ctx, sst, px, py, s);
        continue;
      }
      const st = doorStateAt(x, y) || "L";
      ctx.strokeStyle = doorColor(st, t);
      ctx.lineWidth = Math.max(2, Math.min(3, s / 8));
      ctx.strokeRect(px + 1.5, py + 1.5, s - 3, s - 3);
      ctx.lineWidth = Math.max(1.5, s / 24);
      drawDoorGlyph(ctx, st, px, py, s);
    }
  }

  // 3. Entity tokens (GM / own character) — the #map-canvas pass only,
  //    UNCHANGED by the explored map (rings / own token / awareness items /
  //    hover / paint all render on top exactly as today).
  if (canvas.id === "map-canvas") drawEntitiesAndDots(ctx, s, ox, oy);
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

function drawAwarenessRings(ctx, s, ox, oy) {
  const players = state.players || [];
  if (state.role === "gm") {
    // GM: a ring around every player-owned token (the GM has no token).
    for (const e of state.entities) {
      if (!e.owner) continue;
      const p = players.find((pl) => pl.entity_id === e.id);
      const r = p && Number.isFinite(p.awareness_radius)
        ? p.awareness_radius : 4;
      drawAwarenessRing(ctx, e.x, e.y, r, s, ox, oy);
    }
  } else if (state.youEntity) {
    // Player: one ring around their own token, at their own radius.
    const p = players.find((pl) => pl.id === state.you.id);
    const r = p && Number.isFinite(p.awareness_radius)
      ? p.awareness_radius : 4;
    drawAwarenessRing(ctx, state.youEntity.x, state.youEntity.y, r, s, ox, oy);
  }
}

/* 4. + 5. Tokens, awareness dots, selection, hover, paint preview */
function drawEntitiesAndDots(ctx, s, ox, oy) {
  // Players keep their own entity in a local view so it stays renderable
  // even though the server sends players an empty "entities" list.
  const entities = allEntities();

  // Awareness rings (under the tokens; see drawAwarenessRings).
  drawAwarenessRings(ctx, s, ox, oy);

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
      const shape = item.color === "green" ? "tri" : item.color === "white" ? "circle" : "square";
      drawDot(ctx, ox + item.x * s + s * 0.78, oy + item.y * s + s * 0.22,
              s * 0.16, shape, item.color, 1);
    } else if (item.approximate) {
      // Unknown contact: a coarse block, no identity (name/color/team).
      // item.x/item.y is the block's ORIGIN cell; the marker sits at the
      // block's center.
      drawUnknownDot(ctx, ox + (item.x * 2) * s, oy + (item.y * 2) * s, s);
    } else if (item.entity_id !== ownId) {
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

  // Hover ring + paint preview
  if (hoverCell) {
    const hx = ox + hoverCell.x * s;
    const hy = oy + hoverCell.y * s;
    if (state.tool !== "select") {
      const fill = state.tool === "wall" ? T.wallFill
                 : state.tool === "doorway" ? T.doorway
                 : state.tool === "door" ? doorColor(state.doorAction, "S")
                 : state.tool === "safeDoor" ? T.safeOpen
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
      const e = allEntities().find((x) => x.id === item.entity_id);
      name = item.name || (e ? e.name : null);
      meta = e ? `${e.kind}·${e.team}` : null;
    } else {
      // Full contact (line of sight): the item itself now carries the
      // name + kind (the server sends them) — players see labeled entries.
      name = item.name || null;
      meta = item.kind ? item.kind : null;
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

  const gm = state.role === "gm";
  const hit = entityAtCell(c.x, c.y);

  // Paint mode (GM): floor/wall/doorway apply on pointerdown; the DOOR
  // and Safe door tools apply the armed action on click (doors never
  // optimistic-mutate — the server is authoritative, the state broadcast
  // reconciles).
  if (state.tool !== "select") {
    if (state.tool === "door") {
      if (!gm) return;                          // players have no door tool
      if (t !== "doorway") return;              // no client gating: a bad
                                                 // cell gets the server's
                                                 // "not a doorway" toast
      sendDoor(c.x, c.y, state.doorAction);
    } else if (state.tool === "safeDoor") {
      // GM Safe door tool (safe-room doors spec §7.5): apply the armed
      // action (Mark/Unmark/Open/Close) on click. GM-only (the button is
      // GM-only in the UI; the guard mirrors the Door tool). A non-doorway
      // cell gets the server's "not a doorway" toast — no client gating.
      if (!gm) return;
      if (t !== "doorway") return;
      sendSafeDoor(c.x, c.y, state.safeAction);
    }
    return;
  }

  // Safe doors are GM-controlled (safe-room doors spec §7.6): a PLAYER
  // can never act on one — the check below is gated INTO the normal-door
  // tap branch (the safe cell never emits a normal `door` frame), so a
  // safe-door cell with no entity on it is a pure no-op (no move: a closed
  // safe door is not walkable; an open one is a destination, not a
  // door-action target — the player walks onto it by clicking the floor
  // beyond), while a tap on their OWN token standing on an open safe door
  // still reaches the selection handling below (re-assert selection).

  // Player tapping a doorway cell acts on the DOOR, not movement (a door
  // is a doorway, never a floor, so there is no ambiguity — door-features
  // spec §7.6): the client sends the action, the server decides (errors
  // like "door is locked" surface as toasts via the normal error path).
  //   L (locked)   -> "open": the server replies "door is locked" — the
  //                    player cannot unlock, the toast is the feedback
  //   U (closed,   -> "open": the inverse action for a closed door
  //       unlocked)   (an unlocked door opens on tap)
  //   O (open)     -> "close": the inverse action for an open door
  // (A deviation from the §7.6 body's "U → close, O → open" letters is
  // noted in the build report: that mapping would make the player's tap
  // unable to EVER open a door — always "already closed" — contradicting
  // the requirement "doors can be opened and closed" and the task's
  // explicit "send the inverse action (open if closed, close if open)").
  // A tap on a cell occupied by an entity is NOT a door action (entity
  // selection/movement keeps priority).
  if (!gm && t === "doorway" && !hit) {
    if (isSafeDoor(c.x, c.y)) {
      // Safe door: no-op (GM controls it). A CLOSED one gets a
      // client-side hint (spec §7.7 — the client knows the state); an
      // OPEN one is walkable for the player, so no blocking hint.
      if (safeDoorStateAt(c.x, c.y) !== "O") {
        canvasHint("That safe door is closed — the GM controls it");
      }
      return;
    }
    const st = doorStateAt(c.x, c.y) || "L";
    sendDoor(c.x, c.y, st === "O" ? "close" : "open");
    return;
  }

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
  if (!state.grid || state.tool === "select" || state.tool === "door" ||
      state.tool === "safeDoor") return;
  const key = `${x},${y},${state.tool}`;
  if (lastPainted === key) return;
  lastPainted = key;
  wsSend({ type: "paint", x, y, cell_type: state.tool });
  // optimistic local update (server state reconciles for everyone)
  state.grid.cells[y][x] = state.tool;
}

/* Door actions (door-features spec §7.5/§7.6): the ONLY door wire frame.
   GM: any of unlock/lock/open/close (from the Door tool's armed action).
   Player: open/close only (from tapping a doorway cell). No optimistic
   local mutation — the server is authoritative and the next state
   broadcast (which carries map.doors) reconciles the render. */
function sendDoor(x, y, action) {
  wsSend({ type: "door", x, y, action });
}

/* Safe-door actions (safe-room doors spec §7.5/§8.3): the ONLY safe-door
   wire frame, GM-only (players have no Safe door tool and a player tap on
   a safe cell is a no-op — §7.6). action ∈ mark/unmark/open/close (the
   armed Safe door sub-button). No optimistic local mutation — the server
   is authoritative and the next state broadcast (which carries map.safe
   and the updated map.doors for mark/unmark) reconciles the render. A bad
   cell/state gets the server's error toast via the normal error path. */
function sendSafeDoor(x, y, action) {
  wsSend({ type: "safe_door", x, y, action });
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

function setTool(tool) {
  state.tool = tool;
  lastPainted = null;
  $$("#paint-group .tool-btn").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.tool === tool));
  });
  // The Door and Safe door tools' action sub-buttons are only visible while
  // their tool is armed (hidden via [hidden] otherwise — CSS).
  els.doorActionRow.hidden = tool !== "door";
  els.safeActionRow.hidden = tool !== "safeDoor";
  els.canvasWrap.classList.remove(
    "mode-select", "mode-paint-floor", "mode-paint-wall",
    "mode-paint-doorway", "mode-paint-door", "mode-paint-safeDoor"
  );
  els.canvasWrap.classList.add(
    tool === "select" ? "mode-select" : `mode-paint-${tool}`
  );
  updateControlHint();
}

// GM Door tool: pick the armed action (default "unlock"), then click a
// door cell to apply it (same click-to-apply ergonomics as paint).
function setDoorAction(action) {
  if (state.tool !== "door") return;
  state.doorAction = action;
  $$("#paint-group .door-action").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.doorAction === action));
  });
  updateControlHint();
}

// GM Safe door tool (safe-room doors spec §7.5): pick the armed action
// (default "mark"), then click a doorway cell to apply it — the exact Door
// tool idiom (tool button + revealed action sub-row + click-to-apply).
function setSafeAction(action) {
  if (state.tool !== "safeDoor") return;
  state.safeAction = action;
  $$("#paint-group .safe-action").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.safeAction === action));
  });
  updateControlHint();
}

$("#paint-group").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".tool-btn");
  if (btn) { setTool(btn.dataset.tool); return; }
  const act = ev.target.closest(".door-action");
  if (act) { setDoorAction(act.dataset.doorAction); return; }
  const safeAct = ev.target.closest(".safe-action");
  if (safeAct) setSafeAction(safeAct.dataset.safeAction);
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

// GM awareness radius (docs/design/awareness-ring.md §5): commit on change;
// the server enforces the 0–20 integer range ("awareness must be an
// integer 0–20" etc.) and server errors surface via the normal toast path.
// Invalid/empty input is a no-op (never send a non-int). Guarded: a stub
// DOM without #awareness-input (tests/js/harness.js) leaves it null.
if (els.awarenessInput) {
  els.awarenessInput.addEventListener("change", () => {
    if (state.role !== "gm" || !state.selectedEntityId) return;
    const sel = state.entities.find((e) => e.id === state.selectedEntityId);
    if (!sel || !sel.owner) return;  // only player tokens have a radius
    const n = parseInt(els.awarenessInput.value, 10);
    if (!Number.isInteger(n) || n < 0 || n > 20) return;
    wsSend({ type: "set_awareness", entity_id: state.selectedEntityId, value: n });
  });
}

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

/* ───────────────────────── Source tabs: Upload | Generate (spec §6.3) ── */

// Upload-side preview copy, restored by resetUploadForm() so "New map…"
// always reopens the view on the Upload tab, exactly like before.
const UPLOAD_PREVIEW_COPY = {
  title: "Detected map",
  gridTitle: "Detection",
  note: "Note: detection is a suggestion — you are the editor of record. " +
        "(Side-by-side before/after painting lands in Iteration 6.)",
};

let genBusyFlag = false;
const genBusy = () => genBusyFlag;

function setSourceTab(source) {           // "upload" | "generate"
  if (els.uploadView.dataset.state === "preview") return;  // locked in preview
  state.uploadSource = source;
  els.uploadForm.hidden = source !== "upload";
  els.genForm.hidden = source !== "generate";
  syncTabStyles();
  if (source === "generate") syncGenerateButton();
}

function syncTabStyles() {
  const gen = state.uploadSource === "generate";
  const inPreview = els.uploadView.dataset.state === "preview";
  els.tabUpload.classList.toggle("is-active", !gen);
  els.tabGenerate.classList.toggle("is-active", gen);
  els.tabUpload.setAttribute("aria-pressed", String(!gen));
  els.tabGenerate.setAttribute("aria-pressed", String(gen));
  // Tabs are the only way to switch forms; locked (no-op + disabled look)
  // while the preview is up.
  els.tabUpload.disabled = inPreview;
  els.tabGenerate.disabled = inPreview;
}

function syncGenerateButton() {
  const nameOk = els.genName.value.trim().length > 0;
  const cols = Number(els.genCols.value), rows = Number(els.genRows.value);
  const sizeOk = Number.isInteger(cols) && Number.isInteger(rows)
    && cols >= 8 && cols <= 60 && rows >= 8 && rows <= 60;
  els.btnGenerate.disabled = !(nameOk && sizeOk) || genBusy();
}

function setGenerateBusy(busy, label = "Generating…") {
  genBusyFlag = busy;
  els.btnGenerate.disabled = busy;
  els.btnGenerate.textContent = busy ? label : "Generate map";
}

function resetUploadForm() {
  els.uploadView.dataset.state = "idle";
  syncTabStyles();                        // unlock the source tabs
  els.uploadForm.hidden = false;
  els.uploadPreview.hidden = true;
  els.uploadNote.hidden = true;
  els.btnStartMap.disabled = true;
  setUploadBusy(false);
  setGenerateBusy(false);
  syncUploadButton();
  // Back on the Upload tab with a clean generate form ("New map…" reopens
  // on Upload exactly like before the tabs existed).
  setSourceTab("upload");
  els.genName.value = "";
  els.genSeed.value = "";
  syncGenerateButton();
  els.previewTitle.textContent = UPLOAD_PREVIEW_COPY.title;
  els.paneGridTitle.textContent = UPLOAD_PREVIEW_COPY.gridTitle;
  els.previewNote.textContent = UPLOAD_PREVIEW_COPY.note;
}

els.tabUpload.addEventListener("click", () => setSourceTab("upload"));
els.tabGenerate.addEventListener("click", () => setSourceTab("generate"));
els.genName.addEventListener("input", syncGenerateButton);
els.genCols.addEventListener("input", syncGenerateButton);
els.genRows.addEventListener("input", syncGenerateButton);
// Enter in any generate field triggers the generate (parity with lobby).
for (const el of [els.genName, els.genCols, els.genRows, els.genSeed]) {
  el.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !els.btnGenerate.disabled) generateMap();
  });
}
// Parity with btnDetect (wired above, upload flow): pressing the button
// must trigger generation. Double-submit is guarded by the same pattern —
// while a request is in flight setGenerateBusy(true) keeps the button
// disabled, so a second click is ignored by the browser.
els.btnGenerate.addEventListener("click", generateMap);

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

/* Generate flow (generated-maps spec §6.3): POST the cols×rows (+ optional
   seed) as JSON to /api/maps/generate; the response has the SAME shape as
   upload, so the shared preview + "Open map in session" (use_map) flow is
   reused unchanged — a generated map is already in the registry. */
async function generateMap() {
  setGenerateBusy(true);
  try {
    const body = {
      name: els.genName.value.trim(),
      cols: Number(els.genCols.value),
      rows: Number(els.genRows.value),
    };
    if (els.genSeed.value !== "") body.seed = Number(els.genSeed.value);
    const resp = await fetch("/api/maps/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error ||
      `generate failed (HTTP ${resp.status})`);
    state.uploadedMap = {
      id: data.id, name: data.name, width: data.width, height: data.height,
      cells: data.cells, thumbnail: data.thumbnail || null,
      dataUrl: null,           // no source image → #pane-source hidden
    };
    showUploadPreview();
  } catch (err) {
    toast(`Generate failed: ${err.message}`, "error");
    setGenerateBusy(false);
  }
}

function showUploadPreview() {
  els.uploadView.dataset.state = "preview";
  syncTabStyles();                           // lock the source tabs
  els.uploadForm.hidden = true;
  els.genForm.hidden = true;                 // only the preview shows
  els.uploadPreview.hidden = false;
  const m = state.uploadedMap;
  const gen = state.uploadSource === "generate";
  els.previewTitle.textContent = gen ? "Generated map" : "Detected map";
  els.paneSource.hidden = gen;               // no source image for generate
  if (m.dataUrl) els.previewImage.src = m.dataUrl;   // uploads only
  els.previewThumbnail.src = m.thumbnail || "";
  els.paneGridTitle.textContent = gen ? "Grid" : "Detection";
  els.previewNote.textContent = gen
    ? "Generation is a suggestion — you are the editor of record. " +
      "Paint to add rooms, walls, or extra doors."
    : UPLOAD_PREVIEW_COPY.note;
  els.uploadNote.textContent = gen
    ? `Generated ${m.width}×${m.height} grid — map id “${m.id}”.`
    : `Detected ${m.width}×${m.height} grid — map id “${m.id}”.`;
  els.uploadNote.hidden = false;
  // Render the detected grid on the preview canvas (same shared renderer).
  const saved = state.grid;
  state.grid = { width: m.width, height: m.height, cells: m.cells };
  els.previewCanvas.width = Math.max(120, m.width * 8);
  els.previewCanvas.height = Math.max(90, m.height * 8);
  drawGridOnCanvas(els.previewCanvas, els.previewCanvas.getContext("2d"));
  state.grid = saved;
  els.btnStartMap.disabled = false;
  toast(gen
    ? `Map “${m.name}” generated and registered.`
    : `Map “${m.name}” detected and registered.`);
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
