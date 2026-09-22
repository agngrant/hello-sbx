/* LittleDungeons frontend — state.js
   Dependency-free shared state: DOM handles (els), the `state` object,
   the theme (T), the motion preference, the live socket handle (ws),
   the hovered cell, and the pure grid/door/safe validation + lookups.
   Split from app/static/app.js (bodies verbatim). */



/* ───────────────────────────── DOM helpers ───────────────────────────── */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const els = typeof document !== "undefined" ? {
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
  newEntitySize: $("#new-entity-size"),
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
  // save-delete modal (save-load-delete-modal spec §6): static full-screen
  // shell — #scrim-style, but body-level (covers map view AND the upload
  // view's Saved maps tab). JS never creates it; it fills the body's text
  // per open and toggles `hidden`.
  saveDeleteModal: $("#save-delete-modal"),
  saveDeleteModalDialog: $("#save-delete-modal-dialog"),
  saveDeleteModalBody: $("#save-delete-modal-body"),
  saveDeleteModalCancel: $("#save-delete-modal-cancel"),
  saveDeleteModalConfirm: $("#save-delete-modal-confirm"),
} : {};

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
  bossFootprints: null, // boss-entity spec: the server-provided size → [w, h]
                        //   footprint table (the additive `boss_footprints`
                        //   state field, validated in applyState; null until
                        //   a valid table arrives → the hardcoded
                        //   BOSS_FOOTPRINTS_FALLBACK is used instead)
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
  confirmingSaveId: null, // the save (app-wide; one open at a time) whose
                          // delete-confirmation MODAL is open — save-load-
                          // delete-modal spec §3: derived state rendered by
                          // syncSaveModal() (rows stay in normal shape)
  savesDeleteBusy: false, // the in-flight DELETE sub-state of the open modal
                          // (transient; cleared in the same finally as
                          // confirmingSaveId)
  lastLoadedSave: null, // {id (save id), name, entityCount} — the rejoin note
};

/* ───────────────────────────── WebSocket ───────────────────────────── */

let ws = null;
function setWs(w) { ws = w; }
function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

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
  typeof window !== "undefined" && !!window.matchMedia &&
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

let hoverCell = null;
function setHoverCell(c) { hoverCell = c; }

export { $, $$, DOOR_STATES, SAFE_STATES, T, VIS_CHARS, allEntities, doorStateAt, els, entityAtCell, hoverCell, isSafeDoor, reducedMotion, safeDoorStateAt, setHoverCell, setWs, state, validateDoors, validateSafe, validateVisibilityMatrix, ws, wsSend };
