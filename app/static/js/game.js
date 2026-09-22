/* LittleDungeons frontend — game.js
   Wire handlers (welcome / state / path / error) and player/GM intents
   (move, paint, door/safe-door, entity tools).
   Split from app/static/app.js (bodies verbatim). */


import { $$, allEntities, els, reducedMotion, state, validateDoors, validateSafe, validateVisibilityMatrix, wsSend } from "./state.js";
import { dismissGmFirstRunHint, fitToMap, layoutCanvas, renderAll, selectEntity, showGmFirstRunHint, syncSaveMapStateButton, toast, updateControlHint, validateBossFootprints } from "./render.js";
import { showView } from "./ui.js";

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
  // Boss footprints (boss-entity spec, additive): the canonical size →
  // [w, h] tile table (app.models.BOSS_FOOTPRINTS) the server emits on
  // EVERY state/welcome frame, so the client derives boss dimensions from
  // the server instead of a hardcoded copy. Same additive/defensive
  // pattern as doors/safe/visibility: absent (an old server that predates
  // the field) or malformed (validateBossFootprints ⇒ null) ⇒ the
  // hardcoded BOSS_FOOTPRINTS_FALLBACK keeps rendering exactly as before.
  // bossFootprintsTable() is the single read point for the rest of the
  // file — the server value takes precedence whenever it is present.
  state.bossFootprints = validateBossFootprints(msg.boss_footprints);
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

function sendMove(entityId, x, y, override) {
  // BUG-003: while an entity is mid-animation, ignore further move clicks for
  // it (wireframes §4.5). The in-flight path already ends on a chosen cell.
  if (isAnimating(entityId)) return;
  state.moveRetry = { entity_id: entityId, x, y };   // enables "Move anyway"
  wsSend({ type: "move", entity_id: entityId, x, y, override });
}

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

function createEntity() {
  if (state.role !== "gm") return;
  const name = els.newEntityName.value.trim() || "entity";
  const kind = els.newEntityKind.value;
  const team = els.newEntityTeam.value;
  const msg = { type: "create_entity", name, kind, team };
  if (kind === "boss" && els.newEntitySize) {
    // Boss footprint size (spec §2 table); the server defaults to size 2
    // when the key is absent, so send it explicitly for a chosen variant.
    msg.size = Number(els.newEntitySize.value);
  }
  const spot = (state.lastHovered &&
                state.grid &&
                (state.grid.cells[state.lastHovered.y][state.lastHovered.x] === "floor" ||
                 state.grid.cells[state.lastHovered.y][state.lastHovered.x] === "doorway"))
    ? state.lastHovered
    : firstFreeFloor();
  msg.x = spot.x;
  msg.y = spot.y;
  wsSend(msg);
  els.newEntityName.value = "";
  state.expectCreatedToken = true;  // the next state selects the new token
  dismissGmFirstRunHint();
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

export { applyState, createEntity, findEntity, firstFreeFloor, isAnimating, lastPainted, onError, onPath, onServerMessage, onState, onWelcome, paintCell, sendDoor, sendMove, sendSafeDoor, setDoorAction, setSafeAction, setTool, stopAnim };
