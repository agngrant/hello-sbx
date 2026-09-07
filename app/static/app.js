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
  tabSaves: $("#tab-saves"),
  savesTab: $("#saves-tab"),
  savesTabList: $("#saves-tab-list"),
  savesTabEmpty: $("#saves-tab-empty"),
  savesTabRejoinNote: $("#saves-tab-rejoin-note"),
  btnSaveMapState: $("#btn-save-map-state"),
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
  // map: nav panel (pan-zoom spec §3) — "Map view", first sidebar section
  navPanel: $("#nav-panel"),
  navUp: $("#nav-up"), navDown: $("#nav-down"),
  navLeft: $("#nav-left"), navRight: $("#nav-right"),
  zoomIn: $("#zoom-in"), zoomOut: $("#zoom-out"),
  navReadout: $("#nav-readout"),
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
  // map: saves panel (save-load spec §7.2) — GM-only, between GM Tools and
  // Awareness in the right sidebar.
  savesPanel: $("#saves-panel"),
  saveName: $("#save-name"),
  btnSaveCurrentMap: $("#btn-save-current-map"),
  saveConfirm: $("#save-confirm"),
  savesList: $("#saves-list"),
  savesEmpty: $("#saves-empty"),
  savesRejoinNote: $("#saves-rejoin-note"),
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
  // Pan & zoom (pan-zoom spec §2.1): per-client, frontend-only view state.
  //   level ∈ 0 (max zoom, 6×5) … 10 (min zoom, 60×50); panX/panY are integer
  //   cell offsets from the west/north map edge, clamped to [0, size−visible].
  // NEVER serialized or sent to the server (AC21).
  view: { level: 0, panX: 0, panY: 0 },
  _view: null,          // computed {s,ox,oy,x0,x1,y0,y1,W,H} (applyView)
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
  uploadSource: "upload", // "upload" | "generate" | "saves" — client-side only
  animations: {},       // entity_id -> {path, i, timer} for in-flight move anims
  moveRetry: null,      // pending {entity_id, x, y} for the "Move anyway" toast
  // Save / Load menu (save-load spec §7): the shared save list (both
  // surfaces render from this one fetch) + the in-flight save preview.
  saves: null,          // latest GET /api/saves payload rows (null = unfetched)
  savesLoaded: false,   // list fetched at least once (refresh-on-first-show)
  lastLoadedSave: null, // {id (save id), name, entityCount} — the rejoin note
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
  // Rejoin by name (save-load spec §7.5, R4): after a restart the player's
  // ID is ephemeral — a name-matching saved entity is rebound on join
  // (§6.1). The frozen wire never tags that, so the server sets an
  // ADDITIVE welcome-only flag `you.rebound` on exactly that join — never on
  // a fresh spawn, a GM join, or a live same-name re-attach (BUG-014). Tell
  // the player their character came back.
  if (state.role === "player" && state.you && state.you.rebound &&
      state.youEntity) {
    toast(`Welcome back, ${state.name} — your character "${state.youEntity.name}" ` +
          `has been restored.`);
  }
  updateControlHint();
}

function onState(msg) { applyState(msg); }

function applyState(msg) {
  // Pan & zoom (pan-zoom spec §2.5 / E3): the view must RE-FIT only when the
  // map's DIMENSIONS change (join / a `use_map` swap to a different size). A
  // same-map broadcast (door open/close, a repaint, ...) keeps the current
  // level + pan (E2/A8: no re-fit on resize or content-only updates) — so
  // `mapChanged` (full grid-content diff) drives the RENDER, while `mapFits`
  // (dimension diff) drives the REFIT. The render below uses the FULL grid
  // diff (mapContentChanged) so content-only changes still re-render.
  const mapFits = state.grid
    ? (state.grid.width !== msg.map.width || state.grid.height !== msg.map.height)
    : true;   // first map ever (welcome) → treat as a (re)fit
  const mapContentChanged = !state.grid ||
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
  // Preview "Save map state" button: enabled while a map is open in a live
  // GM session (save-load spec §7.3 / E9). Kept in sync with every state
  // broadcast so it tracks join/role/grid changes.
  syncSaveMapStateButton();
  // Re-fit the view only on a map DIMENSION change (join / use_map swap,
  // spec E3); otherwise keep the current level+pan (defensively re-clamped
  // by layoutCanvas). Content-only updates just re-render.
  if (mapFits) fitToMap();
  if (!els.mapView.hidden || mapContentChanged) {
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
  if (view === "map") { renderLegendDoorSwatches(); syncNavControls(); }
  // Saves list refresh triggers (save-load spec §7.2): the GM's panel first
  // shown + after each save/load/delete + on a successful use_map (all wired
  // in the saves module). Cheap: one small GET.
  if (view === "map" && state.role === "gm" && !state.savesLoaded) refreshSaves();
  if (view === "upload" && state.role === "gm"
      && state.uploadSource === "saves") renderSavesTab();
}

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

/* `T` is declared here — after `renderLegendDoorSwatches()` (which reads
   `T.floor`) but before any boot-time statement can invoke it: the function
   is only called from showView("map"), i.e. after a join. Moving `T` back
   below the lobby/legend code, or re-adding a load-time legend call,
   re-creates the P1 TDZ crash (regression guard:
   tests/test_frontend.py::TestLobbyBootRegression). */
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
  // Pictorial wooden doors (door-iconography spec §5.3). A door is a
  // `doorway` cell drawn floor-based as a WOODEN DOOR: brown slab for a
  // normal door, green slab for a safe-room door (the wood hue is the
  // at-a-glance family signal); a top-right padlock marks L (locked,
  // closed), no padlock marks U (unlocked, closed), and O is an open leaf
  // with a soft light glow (yellow normal / green safe). The E (explored)
  // tier desaturates the slab to a flat grey family but keeps the padlock
  // mark (ePadlockMark) so locked stays distinguishable from
  // unlocked-closed in memory.
  woodBrown: "#9c6b3a", woodBrownDark: "#7a4f2a",   // normal slab + planks (S)
  woodGreen: "#4f9e6b", woodGreenDark: "#3c7d53",   // safe slab + planks (S)
  padlockBody: "#e6b422", padlockShackle: "#8a8f98",  // padlock (L) (S)
  lightYellow: "#ffe9a8",                            // normal open glow (O) (S)
  lightGreen: "#c9f2d4",                            // safe open glow (O) (S)
  frameBrown: "#5b4327", frameGreen: "#2f5c40",     // slab frames (S)
  doorShadow: "rgba(0,0,0,0.18)",                   // slab inner shadow (S)
  eWoodSlab: "#8a94a0",                            // both families' E slab (L/U)
  eSlabFrame: "#5f6874",                            // E slab frame (L/U)
  ePadlockMark: "#cfd4db",                          // E faint padlock (L) — the locked signal
  eLight: "#e8ecf0",                                // both families' E open glow (O)
  // Paint-preview fills for the door/safeDoor hover preview: the WOOD color
  // of each family (a preview says "this is a door of this kind", not "this
  // is a locked door") — door-iconography spec §7.2.
  doorWoodPreview: "#9c6b3a",   // == woodBrown
  safeWoodPreview: "#4f9e6b",   // == woodGreen
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
   safe-room doors spec §7.3 + door-iconography spec §7.1. `map.safe` is an
   ADDITIVE wire field that rides inside `map` like `map.doors`: an object
   "<x>,<y>" -> "L" (locked+closed) | "U" (unlocked, closed) | "O" (open) —
   the SAME three-state model the normal doors use — covering EVERY
   safe-door cell (emitted in full whenever ≥ 1 exists; a missing key ⇒ no
   safe doors ⇒ every doorway is a NORMAL door). `map.safe` and `map.doors`
   partition the doorway cells server-side (a doorway is one kind of door
   or the other, never both). A LEGACY "C" (from a stale pre-redesign
   server) is coerced to "U" (unlocked closed — the old closed state was
   always-unlocked), mirroring the server's from_dict migration, so the
   render never sees an unknown char. Any other malformed payload (wrong
   type, bad keys, bad state chars) is treated as {} — defensive, never
   crashes the render (cf. validateDoors / validateVisibilityMatrix). */
const SAFE_STATES = ["L", "U", "O"];
function validateSafe(safe) {
  if (safe == null) return {};
  if (typeof safe !== "object" || Array.isArray(safe)) return {};
  const clean = {};
  const keyRe = /^[0-9]+,[0-9]+$/;
  for (const key of Object.keys(safe)) {
    if (!keyRe.test(key)) return {};
    let v = safe[key];
    if (v === "C") v = "U";   // LEGACY migration (mirrors the server)
    if (SAFE_STATES.indexOf(v) === -1) return {};
    clean[key] = v;
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

/* The safe-door state at (x,y): "L"|"U"|"O" for a safe-door cell
   (DEFAULT "L" — a doorway recorded safe with no value is LOCKED, the
   safe/secure default, mirroring the normal door's locked default),
   null for a cell that is not a safe door. Mirrors the server's
   Grid.safe_door_state_at on the client. */
function safeDoorStateAt(x, y) {
  if (!isSafeDoor(x, y)) return null;
  return state.safe[`${x},${y}`] || "L";
}

/* ─────────────── Pictorial wooden doors (door-iconography spec §5/§6) ──
   One renderer draws ALL SIX door states: `kind` "normal" (brown wood)
   or "safe" (green wood); `state` "L" (locked+closed), "U" (unlocked,
   closed) or "O" (open); `t` "S" (full detail — GM view, player in-sight
   cells) or "E" (explored, greyed). A door is a PICTORIC wooden door
   drawn OVER the cell's floor base (the existing contract — the floor fill
   + grid line are already there; the art is a "sticker", never a cell
   replacement):
     L → slab + frame + planks + inner shadow + a CLOSED padlock in the
         TOP-RIGHT corner (the padlock is the SOLE L-vs-U discriminator);
     U → identical to L minus the padlock (the ABSENCE is the signal);
     O → the slab is gone: a soft radial LIGHT glow fills the opening
         (yellow normal / green safe) with an ajar leaf swung over it.
   The E tier desaturates everything to a flat grey family, but the padlock
   mark survives as a LIGHT grey (ePadlockMark) so locked stays readable
   in memory, and the open glow becomes near-white (eLight).
   Pure function of (ctx, kind, state, px, py, s, t) — no closure over the
   module state — so it is testable in the Node harness and reusable by the
   legend swatches. Shared by `drawGridOnCanvas` (doorway pass) and
   `renderLegendDoorSwatches`. */
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
  // also passes the VIEW so the renderer culls to the visible window (§6.1);
  // the preview canvas never passes a view (A10, self-fit whole-map).
  const vis = (state.role === "player") ? state.visibility : null;
  drawGridOnCanvas(canvas, ctx, vis, state._view);
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
  if (canvas.id === "map-canvas") {
    drawEntitiesAndDots(ctx, s, ox, oy, { x0, x1, y0, y1 });
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
    for (const e of state.entities) {
      if (!e.owner) continue;
      if (!inWin(e.x, e.y)) continue;   // §6.1 cull: off-window → skipped
      const p = players.find((pl) => pl.entity_id === e.id);
      const r = p && Number.isFinite(p.awareness_radius)
        ? p.awareness_radius : 4;
      drawAwarenessRing(ctx, e.x, e.y, r, s, ox, oy);
    }
  } else if (state.youEntity) {
    // Player: one ring around their own token, at their own radius.
    if (!inWin(state.youEntity.x, state.youEntity.y)) return;
    const p = players.find((pl) => pl.id === state.you.id);
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
  const inWin = (x, y) => !win || (x >= win.x0 && x < win.x1 && y >= win.y0 && y < win.y1);

  // Awareness rings (under the tokens; see drawAwarenessRings).
  drawAwarenessRings(ctx, s, ox, oy, win);

  // Selection ring (under tokens)
  const sel = entities.find((e) => e.id === state.selectedEntityId);
  if (sel && inWin(sel.x, sel.y)) {
    ctx.strokeStyle = T.accent;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(ox + sel.x * s + s / 2, oy + sel.y * s + s / 2, s * 0.55, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Full tokens for every entity the client controls (GM: all; player: self).
  for (const e of entities) {
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
  if (!state.joined || !state.grid) return;
  if (!c) {
    // UX guard (door-report post-mortem mitigation): a tap in the letterbox
    // bars or beyond the visible window resolves to NO cell. Previously a
    // silent no-op — exactly what read as "doors do nothing". Debounced in
    // tapHint so a mis-click flurry toasts at most once per 2.5 s.
    tapHint("Nothing there — outside the visible map");
    return;
  }
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
      if (t !== "doorway") {
        // UX guard: the armed door action has no target on a non-doorway
        // cell. No frame is sent here (the server toast can't fire), so the
        // miss would otherwise vanish silently — hint instead (debounced).
        tapHint("Nothing to select here — not a doorway");
        return;
      }
      sendDoor(c.x, c.y, state.doorAction);
    } else if (state.tool === "safeDoor") {
      // GM Safe door tool (safe-room doors spec §7.5): apply the armed
      // action (Mark/Unmark/Open/Close) on click. GM-only (the button is
      // GM-only in the UI; the guard mirrors the Door tool).
      if (!gm) return;
      if (t !== "doorway") {
        // UX guard: same as the Door tool — a non-doorway miss is a silent
        // no-op without a hint (debounced in tapHint).
        tapHint("Nothing to select here — not a doorway");
        return;
      }
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

/* Keyboard (pan-zoom spec §4; wireframes §9 updated):
   - Esc: deselect / close drawer / back to map (unchanged).
   - Enter/Space on an awareness row: select (GM, unchanged).
   - Arrows PAN the view one step (§2.4) — IDENTICAL delta to the matching
     #nav-panel arrow button (owner req 3). This RETIRES the old arrow-key
     "nudge the selected entity" behavior (spec A2: wireframes §9) — movement
     is click/tap only; the awareness list keeps its Tab/Enter/Space surface.
   - `+` / `=` zoom in one level, `-` zooms out (§4.1).
   Guards (§4.3/A4/A5): fully ignored while focus is in an input/textarea/
   select/contenteditable (native editing untouched — no preventDefault);
   ignored with ANY modifier (never fight browser zoom/shortcuts);
   only when the map view is visible, joined, and a map exists.
   No other keys are bound (no "0"=fit, no wheel zoom, no Home/End, A5).
   Guarded/capped presses are silent no-ops (no toast). */
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
  if (focusInField(ev.target)) return;   // §4.3 input focus guard (A3)
  if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.shiftKey) return; // A4
  if (els.mapView.hidden || !state.joined || !state.grid) return;
  switch (ev.key) {
    case "ArrowLeft":  ev.preventDefault(); panBy(-1, 0); return;
    case "ArrowRight": ev.preventDefault(); panBy(1, 0);  return;
    case "ArrowUp":    ev.preventDefault(); panBy(0, -1); return;
    case "ArrowDown":  ev.preventDefault(); panBy(0, 1);  return;
    case "+": case "=": ev.preventDefault(); zoomBy(1);  return; // zoom IN (level−1); main-row + numpad
    case "-":           ev.preventDefault(); zoomBy(-1); return; // zoom OUT (level+1)
  }
});

// Nav panel buttons (pan-zoom spec §3.2): real <button>s — one click = one
// step, the SAME delta as the matching arrow key (owner req 2/3). Native
// `disabled` (set by syncNavControls) blocks pointer + keyboard activation;
// Space/Enter on a FOCUSED enabled button activates it natively (the §4.3
// guard does not intercept buttons — a BUTTON is not a field).
els.navLeft.addEventListener("click", () => panBy(-1, 0));
els.navRight.addEventListener("click", () => panBy(1, 0));
els.navUp.addEventListener("click", () => panBy(0, -1));
els.navDown.addEventListener("click", () => panBy(0, 1));
// `−` zooms OUT (smaller cells, see MORE), `+` zooms IN (bigger cells, more
// detail) — the signs passed here are zoom-in steps (see zoomBy), so the
// buttons and the `−`/`+`/`=` keys are the same direction.
els.zoomOut.addEventListener("click", () => zoomBy(-1));
els.zoomIn.addEventListener("click", () => zoomBy(1));

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

function setSourceTab(source) {           // "upload" | "generate" | "saves"
  if (els.uploadView.dataset.state === "preview") return;  // locked in preview
  state.uploadSource = source;
  els.uploadForm.hidden = source !== "upload";
  els.genForm.hidden = source !== "generate";
  if (els.savesTab) els.savesTab.hidden = source !== "saves";
  syncTabStyles();
  if (source === "generate") syncGenerateButton();
  if (source === "saves") {
    // Lobby-reachable load menu (save-load spec §7.3): render the shared
    // save list into the tab (one fetch — the sidebar shares the same data).
    renderSavesTab();
    if (!state.savesLoaded) refreshSaves();
  }
}

function syncTabStyles() {
  const src = state.uploadSource;
  const inPreview = els.uploadView.dataset.state === "preview";
  els.tabUpload.classList.toggle("is-active", src === "upload");
  els.tabGenerate.classList.toggle("is-active", src === "generate");
  if (els.tabSaves) els.tabSaves.classList.toggle("is-active", src === "saves");
  els.tabUpload.setAttribute("aria-pressed", String(src === "upload"));
  els.tabGenerate.setAttribute("aria-pressed", String(src === "generate"));
  if (els.tabSaves) els.tabSaves.setAttribute("aria-pressed", String(src === "saves"));
  // Tabs are the only way to switch forms; locked (no-op + disabled look)
  // while the preview is up.
  els.tabUpload.disabled = inPreview;
  els.tabGenerate.disabled = inPreview;
  if (els.tabSaves) els.tabSaves.disabled = inPreview;
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
  if (els.btnSaveMapState) els.btnSaveMapState.disabled = true;
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
  if (els.saveName) els.saveName.value = "";
  hideSaveConfirm();
  syncSaveMapStateButton();
}

els.tabUpload.addEventListener("click", () => setSourceTab("upload"));
els.tabGenerate.addEventListener("click", () => setSourceTab("generate"));
if (els.tabSaves) els.tabSaves.addEventListener("click", () => setSourceTab("saves"));
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
    if (m.isLoadedSave) announceLoadedSaveRejoin();
    else hideRejoinNote();   // a non-save map is now open — the note is moot
  }
  showView("map");
  els.mapName.textContent = m.name;
  document.title = `LittleDungeons — ${m.name}`;
  if (state.grid) renderAll();
}

/* ───────────────────────────── Saves (save-load spec §7) ─────────────────────────────
   GM save/load menu — two surfaces, one shared list fetch:
     1. the map-view right-sidebar #saves-panel (GM-only; save the CURRENT
        map + load/delete rows); and
     2. the lobby-reachable "Saved maps" tab in the New map view
        (#saves-tab; load a save → the shared preview → "Open map in
        session" → use_map — the post-restart restore path, E8).
   All traffic is plain REST (no WS involvement in save/load management,
   spec §8): GET /api/saves (any role) · POST /api/saves (GM) ·
   POST /api/saves/{id}/load (GM — registers an independent copy; the GM
   then opens it via the EXISTING use_map flow on the SAME socket,
   BUG-002-safe) · DELETE /api/saves/{id} (GM). Server errors follow the
   frozen shape {"error": msg} and are surfaced as toasts verbatim. */

const SAVE_EMPTY_TAB_COPY = "No saves yet. Save one from the map view's Saves panel.";

/* Format a save's created_at (ISO-8601 local, second precision, A16) as a
   compact local date (e.g. "Jan 1 12:00"). Unparseable ⇒ raw string. */
function formatSaveDate(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso || "");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/* One row of the shared save list. ``surface`` "sidebar" (dense, stacked
   lines, spec §7.2) or "tab" (one line + inline Load/Delete, spec §7.3).
   A corrupt save (E3/A12) renders name + ⚠ corrupt and a Delete button
   ONLY — Load is absent (its load would 404 anyway). */
function buildSaveRow(save, surface) {
  const row = document.createElement("div");
  row.className = ("save-row" + (save.corrupt ? " is-corrupt" : "")).trim();
  row.dataset.id = save.id;

  const nameEl = document.createElement("span");
  nameEl.className = "save-row-name";
  nameEl.textContent = (save.name || "(unnamed)")
    + (save.corrupt ? " ⚠ corrupt" : "");

  const actions = document.createElement("span");
  actions.className = "save-row-actions";
  if (!save.corrupt) {
    // Load = POST /api/saves/<id>/load (sidebar: then use_map on the SAME
    // socket; tab: then the shared preview → "Open map in session").
    actions.appendChild(saveRowAction("Load", "save-row-load", () => {
      if (surface === "tab") loadSaveFromTab(save.id);
      else loadSave(save.id);
    }));
  }
  // Delete on every row (corrupt rows are Delete-only, E3/A12).
  actions.appendChild(saveRowAction("Delete", "save-row-del", () => {
    confirmDeleteSave(save.id);
  }));

  const header = document.createElement("div");
  header.className = "save-row-head";
  header.appendChild(nameEl);
  header.appendChild(actions);
  row.appendChild(header);

  if (!save.corrupt) {
    const meta = document.createElement("span");
    meta.className = "save-row-meta muted small";
    meta.textContent = `${save.map_name || "—"} · ${save.width}×${save.height} ` +
      `· ${save.entity_count} tokens · ${formatSaveDate(save.created_at)}`;
    row.appendChild(meta);
  }
  return row;
}

function saveRowAction(label, cls, onClick) {
  const btn = document.createElement("button");
  btn.className = `btn btn-small ${cls}`;
  btn.textContent = label;
  btn.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
  return btn;
}

/* Clear a list container. The harness stub models .children as a plain
   array (appendChild pushes), so splice it; a real DOM HTMLCollection is
   cleared with the standard removeChild loop. */
function clearContainer(el) {
  if (!el || !el.children) return;
  if (Array.isArray(el.children)) {
    el.children.splice(0);
  } else {
    while (el.children.length) el.removeChild(el.children[0]);
  }
}

/* Both list containers render from state.saves — the one shared fetch. */
function renderSaves() {
  const list = state.saves || [];
  clearContainer(els.savesList);
  for (const save of list) els.savesList.appendChild(buildSaveRow(save, "sidebar"));
  els.savesEmpty.hidden = list.length !== 0;
}

function renderSavesTab() {
  const list = state.saves || [];
  clearContainer(els.savesTabList);
  for (const save of list) els.savesTabList.appendChild(buildSaveRow(save, "tab"));
  els.savesTabEmpty.hidden = list.length !== 0;
  els.savesTabEmpty.textContent = SAVE_EMPTY_TAB_COPY;
}

/* GET /api/saves (any role) — the shared list fetch (spec §7.1). Newest
   first (API order); corrupt rows flagged. */
async function refreshSaves() {
  try {
    const resp = await fetch("/api/saves");
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `save list failed (HTTP ${resp.status})`);
    state.saves = data.saves || [];
    state.savesLoaded = true;
  } catch (err) {
    if (state.role === "gm") toast(err.message, "error");
    return;
  }
  renderSaves();
  renderSavesTab();
}

/* ── Save the current map (sidebar primary action + preview convenience) ──
   POST /api/saves {name?} — blank label ⇒ the current map's name (server
   default). On 200: success toast (4 s), clear the label, re-GET, and
   scroll the new row into view (spec §7.2). On 409 (no active session,
   E9) / 401: error toast with the server's message verbatim. */
async function saveCurrentMap(label = null, opts = {}) {
  const setBusy = (b) => {
    if (els.btnSaveCurrentMap) els.btnSaveCurrentMap.disabled = b;
    if (opts.busyElement) opts.busyElement.disabled = b;
  };
  const body = {};
  if (label != null && String(label).trim() !== "") body.name = String(label).trim();
  if (opts.overwriteId) body.id = opts.overwriteId;   // E2 "Overwrite"
  setBusy(true);
  try {
    const resp = await fetch("/api/saves", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `save failed (HTTP ${resp.status})`);
    if (els.saveName) els.saveName.value = "";
    hideSaveConfirm();
    toast(`Saved "${data.name}" (${data.entity_count} tokens).`, "info");
    await refreshSaves();
    // Scroll the new row into view (spec §7.2).
    const row = els.savesList.querySelector &&
      els.savesList.querySelector(`.save-row[data-id="${data.id}"]`);
    if (row && row.scrollIntoView) row.scrollIntoView();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

/* Name-conflict inline confirm (E2): the label is NOT a key (ids are
   always fresh), so a same-named save just becomes a second, distinct
   save — UNLESS the GM picks Overwrite, which sends the existing id in the
   POST body so the server replaces that file (list row updates in place).
   No window.confirm — inline rows are the existing app style. */
function findSaveByName(name) {
  return (state.saves || []).find((s) => !s.corrupt && s.name === name) || null;
}

function showSaveConfirm(existing) {
  els.saveConfirm.hidden = false;
  els.saveConfirm.textContent = `A save named "${existing.name}" exists.`;
  const asNew = document.createElement("button");
  asNew.className = "btn btn-small saves-confirm-asnew";
  asNew.textContent = "Save as new";
  asNew.addEventListener("click", () => {
    hideSaveConfirm();
    saveCurrentMap(els.saveName ? els.saveName.value : null);
  });
  const overwrite = document.createElement("button");
  overwrite.className = "btn btn-small btn-danger saves-confirm-overwrite";
  overwrite.textContent = "Overwrite";
  overwrite.addEventListener("click", () => {
    hideSaveConfirm();
    saveCurrentMap(els.saveName ? els.saveName.value : null, { overwriteId: existing.id });
  });
  els.saveConfirm.appendChild(asNew);
  els.saveConfirm.appendChild(overwrite);
}

function hideSaveConfirm() {
  if (!els.saveConfirm) return;
  els.saveConfirm.hidden = true;
  clearContainer(els.saveConfirm);
}

/* "Save current map" button handler (spec §7.2). The trimmed label is the
   save name (blank ⇒ the server defaults to the current map's name). If the
   label matches an EXISTING save, an inline confirm offers Save-as-new
   (default — a second, distinct save) vs Overwrite (replaces that file via
   the explicit id, E2). No window.confirm — inline rows are app style. */
function onSaveCurrentMapClick() {
  const label = els.saveName ? els.saveName.value.trim() : "";
  const existing = label ? findSaveByName(label) : null;
  if (existing) { showSaveConfirm(existing); return; }
  saveCurrentMap(label || null);
}

/* ── Load a save ──────────────────────────────────────────────────────────
   POST /api/saves/<id>/load (body {}) → registers an independent copy in
   the map registry and returns its FRESH registry id. The LIVE session is
   untouched until the GM opens it — the two surfaces do that two ways:
     * sidebar: immediately wsSend({type:"use_map", map_id}) on the SAME
       socket (exactly openUploadedMap's behavior, BUG-002) and stay in
       #map-view — the rejoin note toast fires now;
     * lobby tab: the registered map goes through the shared preview →
       "Open map in session" (openUploadedMap → use_map) — the rejoin note
       fires when that use_map is sent (openUploadedMap).
   On 404 (missing AND corrupt, E3): error toast with the server message
   and the row is dropped from the local list (re-GET). */
async function loadSave(saveId) {
  try {
    const resp = await fetch(`/api/saves/${encodeURIComponent(saveId)}/load`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `load failed (HTTP ${resp.status})`);
    // The loaded map's registry entry — remember it for the rejoin note
    // (the entity count = characters saved with owner_names).
    state.lastLoadedSave = {
      id: saveId,
      name: data.name,
      entityCount: data.entity_count,
    };
    // Open it the standard way — the SAME socket, no session switch. The
    // rejoin note (R4) toasts once, right after the use_map is sent.
    wsSend({ type: "use_map", map_id: data.id });
    announceLoadedSaveRejoin();
    showRejoinNote(data.entity_count);
    await refreshSaves();
  } catch (err) {
    toast(err.message, "error");
    // 404 (missing/corrupt): drop the row locally (re-GET per spec §7.2).
    if (/not found/.test(err.message)) await refreshSaves();
  }
}

/* Lobby tab Load: POST …/load, then show the registered map in the shared
   preview pane EXACTLY as after upload/generate (spec §7.3) — the GM
   clicks "Open map in session" to fire use_map. GET /api/maps/<id> feeds
   the grid + thumbnail (any role); the source pane is hidden (the source
   image is not persisted, A7). */
async function loadSaveFromTab(saveId) {
  try {
    const resp = await fetch(`/api/saves/${encodeURIComponent(saveId)}/load`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `load failed (HTTP ${resp.status})`);
    state.lastLoadedSave = {
      id: saveId,
      name: data.name,
      entityCount: data.entity_count,
    };
    // Fetch the registered map (grid + thumbnail) for the shared preview.
    const mapResp = await fetch(`/api/maps/${encodeURIComponent(data.id)}`);
    const map = await mapResp.json().catch(() => ({}));
    if (!mapResp.ok) throw new Error(map.error || `map fetch failed (HTTP ${mapResp.status})`);
    state.uploadedMap = {
      id: map.id,
      name: map.name,
      width: map.width,
      height: map.height,
      cells: map.cells,
      thumbnail: map.thumbnail || null,
      dataUrl: null,   // no source image → #pane-source hidden (A7)
      isLoadedSave: true,
    };
    showLoadedSavePreview(map);
    showRejoinNote(data.entity_count);
    await refreshSaves();
  } catch (err) {
    toast(err.message, "error");
    if (/not found/.test(err.message)) await refreshSaves();
  }
}

/* Preview a loaded save (spec §7.3): same pane layout as upload/generate,
   with the rejoin note as the preview note and the source pane hidden. */
function showLoadedSavePreview(map) {
  els.uploadView.dataset.state = "preview";
  syncTabStyles();
  els.uploadForm.hidden = true;
  els.genForm.hidden = true;
  els.savesTab.hidden = true;
  els.uploadPreview.hidden = false;
  const m = state.uploadedMap;
  els.previewTitle.textContent = "Loaded map";
  els.paneSource.hidden = true;      // image file not persisted (A7)
  els.previewThumbnail.src = "";     // no server thumbnail on the loaded map
  els.paneGridTitle.textContent = "Grid";
  const n = state.lastLoadedSave ? state.lastLoadedSave.entityCount : 0;
  els.previewNote.textContent = n > 0
    ? `Loaded from save — ${n} character(s) are GM-controlled until a player ` +
      `joins with the matching name. Players must join with the same names ` +
      `to reclaim their characters.`
    : "Loaded from save — no player characters were saved with this map.";
  els.uploadNote.textContent = `Loaded ${m.width}×${m.height} grid — map id “${m.id}”.`;
  els.uploadNote.hidden = false;
  const saved = state.grid;
  state.grid = { width: m.width, height: m.height, cells: m.cells };
  els.previewCanvas.width = Math.max(120, m.width * 8);
  els.previewCanvas.height = Math.max(90, m.height * 8);
  drawGridOnCanvas(els.previewCanvas, els.previewCanvas.getContext("2d"));
  state.grid = saved;
  els.btnStartMap.disabled = false;
  toast(`Save “${m.name}” loaded — open it in the session to play it.`);
}

/* ── Delete a save ────────────────────────────────────────────────────────
   Inline confirm in the row (Delete "<name>"? + [ Delete ] [ Cancel ]) →
   DELETE /api/saves/<id> → 200 {ok:true} → row removed + toast. 404 →
   error toast + re-GET. */
function confirmDeleteSave(saveId) {
  const save = (state.saves || []).find((s) => s.id === saveId);
  const name = save ? save.name : saveId;
  // Replace the row's buttons with the inline confirm (both surfaces).
  const row = [els.savesList, els.savesTabList]
    .find((c) => c && c.querySelector &&
      c.querySelector(`.save-row[data-id="${saveId}"]`));
  if (!row) {
    // No rendered row to confirm in — confirm via the sidebar empty area.
    deleteSave(saveId);
    return;
  }
  const rowEl = row.querySelector(`.save-row[data-id="${saveId}"]`);
  const actions = rowEl.querySelector(".save-row-actions");
  if (!actions) return;
  actions.textContent = `Delete "${name}"? `;
  const del = saveRowAction("Delete", "save-row-del-confirm", () => deleteSave(saveId));
  const cancel = saveRowAction("Cancel", "save-row-cancel", () => {
    // Re-render both lists from state to restore the original buttons.
    renderSaves();
    renderSavesTab();
  });
  actions.appendChild(del);
  actions.appendChild(cancel);
}

async function deleteSave(saveId) {
  const save = (state.saves || []).find((s) => s.id === saveId);
  const name = save ? save.name : saveId;
  try {
    const resp = await fetch(`/api/saves/${encodeURIComponent(saveId)}`, { method: "DELETE" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `delete failed (HTTP ${resp.status})`);
    toast(`Deleted "${name}".`);
    await refreshSaves();
  } catch (err) {
    toast(err.message, "error");
    if (/not found/.test(err.message)) await refreshSaves();
  }
}

/* Rejoin note (save-load spec §7.4, R4): after the GM opens a LOADED save
   via use_map, toast — once — that the saved characters are GM-controlled
   until players join with the SAME names to reclaim them. The GM knows the
   map came from a save because the load response tagged it
   (state.lastLoadedSave); this fires when that use_map is sent. */
function announceLoadedSaveRejoin() {
  const ls = state.lastLoadedSave;
  if (!ls) return;
  state.lastLoadedSave = null;   // fire once
  if (!ls.entityCount) return;
  toast(`Loaded "${ls.name}". ${ls.entityCount} character(s) are waiting ` +
    `for players to join with matching names.`, "info");
}

/* Persistent rejoin note (R4) — the durable affordance behind the one-shot
   toast: shown in the sidebar Saves panel AND the Saved maps tab after a
   load (and as the preview note), telling the GM players must join with the
   same names to reclaim their characters. Hidden for zero saved characters
   and once a non-save map is opened in the session. */
function showRejoinNote(entityCount) {
  const text = entityCount > 0
    ? `Players must join with the same names to reclaim their characters — ` +
      `${entityCount} character(s) are GM-controlled until then.`
    : "";
  for (const el of [els.savesRejoinNote, els.savesTabRejoinNote]) {
    if (!el) continue;
    el.textContent = text;
    el.hidden = !text;
  }
}

function hideRejoinNote() {
  for (const el of [els.savesRejoinNote, els.savesTabRejoinNote]) {
    if (el) el.hidden = true;
  }
}

/* Preview "Save map state" (save-load spec §7.3, E9): enabled only while a
   map is open in a live GM session (state.joined && role gm && grid) —
   POST /api/saves with the MAP NAME as label. A map never opened in a
   session 409s and the server's message toasts verbatim. */
function syncSaveMapStateButton() {
  if (!els.btnSaveMapState) return;
  els.btnSaveMapState.disabled =
    !(state.joined && state.role === "gm" && state.grid);
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

// Save / Load menu (save-load spec §7): the GM's "Save current map" action
// + the preview "Save map state" convenience button. The whole #saves-panel
// and #tab-saves are gm-only (CSS-gated); a player never reaches these.
if (els.btnSaveCurrentMap) {
  els.btnSaveCurrentMap.addEventListener("click", onSaveCurrentMapClick);
}
if (els.saveName) {
  els.saveName.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !els.btnSaveCurrentMap.disabled) onSaveCurrentMapClick();
  });
}
if (els.btnSaveMapState) {
  els.btnSaveMapState.addEventListener("click", () => {
    const label = state.mapName || (state.grid ? state.grid.name : null);
    saveCurrentMap(label, { busyElement: els.btnSaveMapState });
  });
}

setConn("offline", "Offline");
syncLobbyButtons();
syncNavControls();   // pre-welcome: no map → all six nav controls disabled
showView("lobby");
connectWs();
