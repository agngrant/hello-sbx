/** LittleDungeons frontend — main.js (entry point) */

/* ════════════════════════════════════════════════════════════════════
   LittleDungeons — frontend (Iteration 5: live multiplayer over WebSocket)
   Wireframes: docs/design/wireframes.md (tokens, IDs, screen flow).

   Everything is driven by the WebSocket (PROJECT.md §9):
     join (lobby) → welcome → live "state" / "path" / "error" frames.
   The server is authoritative; the client only sends intents
    (join / request_state / move / paint / create_entity /
    delete_entity / set_team / set_awareness).
   ════════════════════════════════════════════════════════════════════ */

import { $, doorStateAt, els, entityAtCell, hoverCell, isSafeDoor, safeDoorStateAt, setHoverCell, state, wsSend } from "./state.js";
import { canvasHint, cellFromEvent, focusInField, panBy, renderAll, selectEntity, syncGmTools, syncNavControls, tapHint, zoomBy } from "./render.js";
import { createEntity, paintCell, sendDoor, sendMove, sendSafeDoor, setDoorAction, setSafeAction, setTool } from "./game.js";
import { connectWs, setConn } from "./net.js";
import { cancelSaveDelete, deleteSave, generateMap, join, onSaveCurrentMapClick, openUploadedMap, renderSaves, renderSavesTab, resetUploadForm, restoreSaveModalFocus, saveCurrentMap, saveModalFocusBtn, setDrawer, setSaveModalFocusBtn, setSourceTab, showView, syncGenerateButton, syncLobbyButtons, syncSaveModal, syncUploadButton, uploadMap } from "./ui.js";

let deleteConfirming = false;
let deleteTimer = null;
let resizeTimer = null;
function registerListeners() {
els.canvas.addEventListener("pointermove", (ev) => {
  const c = cellFromEvent(ev);
  const prev = hoverCell;
  setHoverCell(c);
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
  setHoverCell(null);
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

// Boss kind armed → the size selector enables (syncGmTools gates it).
els.newEntityKind.addEventListener("change", () => syncGmTools());

els.btnNewEntity.addEventListener("click", () => createEntity());

// GM "Add": spawn a token at the last hovered walkable tile (else the first
// free floor) and arm auto-selection so the state broadcast selects it.
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
   Guarded/capped presses are silent no-ops (no toast).
   SAVE-DELETE MODAL (save-load-delete-modal spec rule 4): while the
   confirmation is open, EVERY key is swallowed by the very first guard —
   Escape closes it (unless the DELETE is in flight) and ONLY closes it
   (the rest of the Escape behavior — selectEntity(null) / setDrawer(false)
   / showView("map") — must NOT run), arrows do not pan, +/- do not zoom,
   Enter does not select an awareness row. */
document.addEventListener("keydown", (ev) => {
  if (state.confirmingSaveId) {
    if (ev.key === "Escape" && !state.savesDeleteBusy) cancelSaveDelete();
    return;
  }
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

/* Save-delete modal (save-load-delete-modal spec §6 startup wiring):
   Cancel → cancelSaveDelete(); the backdrop root → Cancel ONLY when the
   click landed on the backdrop itself (ev.target check — a click anywhere
   inside the dialog, buttons included, never cancels, rule 3c/A4); the
   dialog's keydown → two-element Tab cycle (A6/E5); Confirm → the rule-2
   async sequence (busy guard → deleteSave → close on resolution, A5). */
els.saveDeleteModalCancel.addEventListener("click", () => cancelSaveDelete());
els.saveDeleteModal.addEventListener("click", (ev) => {
  if (ev.target === els.saveDeleteModal) cancelSaveDelete();
});
els.saveDeleteModalDialog.addEventListener("keydown", (ev) => {
  if (ev.key !== "Tab" || state.savesDeleteBusy) return;
  ev.preventDefault();
  const next = saveModalFocusBtn === els.saveDeleteModalCancel
    ? els.saveDeleteModalConfirm
    : els.saveDeleteModalCancel;
  if (next.focus) next.focus();
  setSaveModalFocusBtn(next);
});
els.saveDeleteModalConfirm.addEventListener("click", async () => {
  const id = state.confirmingSaveId;
  if (!id || state.savesDeleteBusy) return;   // double-click guard (E2)
  state.savesDeleteBusy = true;
  syncSaveModal();   // Confirm disabled + `Deleting…`; dismissals locked
  try {
    await deleteSave(id);
  } finally {
    // The modal closes on the request's RESOLUTION (A5), success or error.
    state.savesDeleteBusy = false;
    state.confirmingSaveId = null;
    renderSaves();
    renderSavesTab();
    syncSaveModal();
    restoreSaveModalFocus();
  }
});

}
if (typeof window !== "undefined" && typeof document !== "undefined") {
  registerListeners();
  setConn("offline", "Offline");
  syncLobbyButtons();
  syncNavControls();   // pre-welcome: no map → all six nav controls disabled
  showView("lobby");
  connectWs();
}
