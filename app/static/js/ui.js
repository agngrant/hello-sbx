/* LittleDungeons frontend — ui.js
   Lobby, upload/generate, and the save/load menu + delete modal.
   Split from app/static/app.js (bodies verbatim). */


import { els, state, ws, wsSend } from "./state.js";
import { drawGridOnCanvas, renderAll, renderLegendBossSwatch, renderLegendDoorSwatches, syncNavControls, syncSaveMapStateButton, toast } from "./render.js";

/* ───────────────────────────── View switching ───────────────────────────── */

function showView(view) {
  els.lobbyView.hidden = view !== "lobby";
  els.uploadView.hidden = view !== "upload";
  els.mapView.hidden = view !== "map";
  if (view === "map") {
    renderLegendDoorSwatches();
    renderLegendBossSwatch();
    syncNavControls();
  }
  // Saves list refresh triggers (save-load spec §7.2): the GM's panel first
  // shown + after each save/load/delete + on a successful use_map (all wired
  // in the saves module). Cheap: one small GET.
  if (view === "map" && state.role === "gm" && !state.savesLoaded) refreshSaves();
  if (view === "upload" && state.role === "gm"
      && state.uploadSource === "saves") renderSavesTab();
}
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
function setDrawer(open) {
  els.sidebar.classList.toggle("is-open", open);
  els.scrim.hidden = !open;
  els.sidebarToggle.setAttribute("aria-expanded", String(open));
}

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

function setUploadBusy(busy, label = "Uploading & detecting…") {
  els.btnDetect.disabled = busy;
  els.btnDetect.textContent = busy ? label : "Upload & detect";
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
   ONLY — Load is absent (its load would 404 anyway).

   Rows render in NORMAL shape at ALL times (Load + Delete, or Delete-only
   for corrupt rows) — the delete confirmation is a FULL-SCREEN MODAL
   (save-load-delete-modal spec §3), not an in-row surface: there is no
   per-row confirm DOM and no confirming row modifier; state.confirmingSaveId
   drives only syncSaveModal(). */
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
  // Delete on every row (corrupt rows are Delete-only, E3/A12) — it opens
  // the full-screen confirmation modal (never deletes directly).
  actions.appendChild(saveRowAction("Delete", "save-row-del", () => {
    confirmDeleteSave(save.id);
  }));
  actions.hidden = false;   // the harness stub starts every element hidden

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

/* Both list containers render from state.saves — the one shared fetch.
   Every render path funnels through syncSaveModal() (the single restore
   path, save-load-delete-modal spec §3) so the modal — including the
   ghost-save check (rule 5) — stays consistent with the rows. */
function renderSaves() {
  const list = state.saves || [];
  clearContainer(els.savesList);
  for (const save of list) els.savesList.appendChild(buildSaveRow(save, "sidebar"));
  els.savesEmpty.hidden = list.length !== 0;
  syncSaveModal();
}

function renderSavesTab() {
  const list = state.saves || [];
  clearContainer(els.savesTabList);
  for (const save of list) els.savesTabList.appendChild(buildSaveRow(save, "tab"));
  els.savesTabEmpty.hidden = list.length !== 0;
  els.savesTabEmpty.textContent = SAVE_EMPTY_TAB_COPY;
  syncSaveModal();
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

/* ── Delete a save (save-load-delete-modal spec §3/§4) ─────────────────────────
   FULL-SCREEN MODAL, derived state: state.confirmingSaveId (one open
   app-wide) drives the static #save-delete-modal shell via syncSaveModal();
   state.savesDeleteBusy marks the in-flight DELETE (sub-state of the open
   modal). The rows stay in their normal shape at all times — the modal is
   the only confirmation surface. There is NO code path that calls
   deleteSave without the GM passing through the modal: an unknown id
   toasts `save not found: <id>` and stops. */
let saveModalReturnFocusId = null; // restore target (rule 3b); rows are
                                   // rebuilt on re-render, so the BUTTON is
                                   // looked up later, never stored
let saveModalFocusBtn = null;      // Tab-cycle tracker (A6 — never
                                   // document.activeElement)
function setSaveModalFocusBtn(btn) { saveModalFocusBtn = btn; }

/* Enter the confirmation for saveId (rule 1). Role guard (A9), membership
   check, open the modal, focus Cancel (guarded — the harness stub has no
   focus()). Programmatic re-entry for the same id is idempotent (E1). */
function confirmDeleteSave(saveId) {
  if (state.role !== "gm") return;   // A9 defensive guard (surfaces .gm-only)
  // Membership check FIRST — never delete without the GM passing through
  // the modal (E11: an unknown id toasts and opens nothing).
  const save = (state.saves || []).find((s) => s.id === saveId);
  if (!save) {
    toast(`save not found: ${saveId}`, "error");
    return;
  }
  state.confirmingSaveId = saveId;   // one open confirmation app-wide
  state.savesDeleteBusy = false;
  saveModalReturnFocusId = saveId;
  saveModalFocusBtn = els.saveDeleteModalCancel;
  renderSaves();      // rows re-render in normal shape (+ syncSaveModal)
  renderSavesTab();
  syncSaveModal();    // fills the dialog text + un-hides the shell
  if (els.saveDeleteModalCancel.focus) els.saveDeleteModalCancel.focus();
}

/* Dismiss the open modal (Cancel / Escape / backdrop) — rule 3: a DELETE
   already in flight is never aborted (busy guard); no request is sent. */
function cancelSaveDelete() {
  if (state.savesDeleteBusy) return;
  state.confirmingSaveId = null;
  renderSaves();
  renderSavesTab();
  syncSaveModal();
  restoreSaveModalFocus();
}

/* Render the modal as a pure function of
   state.confirmingSaveId + state.saves + state.savesDeleteBusy (idempotent;
   called from renderSaves/renderSavesTab and directly). */
function syncSaveModal() {
  const modal = els.saveDeleteModal;
  if (!modal) return;
  if (state.confirmingSaveId === null) {
    state.savesDeleteBusy = false;
    els.saveDeleteModalConfirm.disabled = false;
    els.saveDeleteModalConfirm.textContent = "Delete";
    els.saveDeleteModalDialog.classList.remove("is-busy");
    modal.hidden = true;
    return;
  }
  const save = (state.saves || []).find((s) => s.id === state.confirmingSaveId);
  if (!save) {
    // Rule 5 — ghost save: the confirmed row is gone (e.g. the list was
    // refreshed and another GM's client deleted it). Close WITHOUT
    // deleting; no DELETE is ever fired on this path.
    toast(`save not found: ${state.confirmingSaveId}`, "error");
    state.confirmingSaveId = null;
    state.savesDeleteBusy = false;
    els.saveDeleteModalConfirm.disabled = false;
    els.saveDeleteModalConfirm.textContent = "Delete";
    els.saveDeleteModalDialog.classList.remove("is-busy");
    modal.hidden = true;
    return;
  }
  // Body: textContent only (XSS-safe). Corrupt saves (E6): name + ⚠ flag,
  // NO meta line (a corrupt record carries no width/height/count/date),
  // note kept.
  clearContainer(els.saveDeleteModalBody);
  const nameP = document.createElement("p");
  nameP.className = "save-modal-name";
  nameP.textContent = 'Delete "' + (save.name || "(unnamed)") + '"?'
    + (save.corrupt ? " ⚠ corrupt" : "");
  els.saveDeleteModalBody.appendChild(nameP);
  if (!save.corrupt) {
    const metaP = document.createElement("p");
    metaP.className = "save-modal-meta";
    metaP.textContent = `${save.map_name || "—"} · ${save.width}×${save.height} ` +
      `· ${save.entity_count} tokens · ${formatSaveDate(save.created_at)}`;
    els.saveDeleteModalBody.appendChild(metaP);
  }
  const noteP = document.createElement("p");
  noteP.className = "save-modal-note";
  noteP.textContent = ("The save file will be removed. A map already loaded " +
    "from this save is not affected.");
  els.saveDeleteModalBody.appendChild(noteP);
  modal.hidden = false;   // the harness stub starts hidden=true
  // Busy sub-state (E2): the in-flight DELETE locks every dismissal path
  // (Cancel / Escape / backdrop) and disables Confirm with `Deleting…`.
  els.saveDeleteModalDialog.classList.toggle("is-busy", state.savesDeleteBusy);
  els.saveDeleteModalConfirm.disabled = state.savesDeleteBusy;
  els.saveDeleteModalConfirm.textContent = state.savesDeleteBusy
    ? "Deleting…" : "Delete";
}

/* Walk the freshly re-rendered lists (plain .children arrays — the harness
   querySelector returns null, so never query) for saveId's row and return
   its Delete button (the actions span's last child), or null. */
function findSaveRowDeleteButton(saveId) {
  for (const list of [els.savesList, els.savesTabList]) {
    if (!list || !list.children) continue;
    for (const row of list.children) {
      if (row.dataset.id !== saveId) continue;
      const head = row.children[0];
      if (!head || !head.children || head.children.length < 2) continue;
      const actions = head.children[1];
      return actions.children.length ? actions.children[actions.children.length - 1] : null;
    }
  }
  return null;
}

/* Rule 3b: focus returns to the confirmed row's Delete button in the
   freshly rendered list (A11/A6: guarded — if the row is gone (deleted /
   ghost-closed) the restore is simply skipped; never focus a detached
   element). */
function restoreSaveModalFocus() {
  const btn = saveModalReturnFocusId
    ? findSaveRowDeleteButton(saveModalReturnFocusId)
    : null;
  saveModalReturnFocusId = null;
  if (btn && btn.parentNode && btn.focus) btn.focus();
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

export { SAVE_EMPTY_TAB_COPY, UPLOAD_PREVIEW_COPY, announceLoadedSaveRejoin, buildSaveRow, cancelSaveDelete, clearContainer, confirmDeleteSave, deleteSave, findSaveByName, findSaveRowDeleteButton, formatSaveDate, genBusy, genBusyFlag, generateMap, hideRejoinNote, hideSaveConfirm, join, loadSave, loadSaveFromTab, onSaveCurrentMapClick, openUploadedMap, readImageFile, refreshSaves, renderSaves, renderSavesTab, resetUploadForm, restoreSaveModalFocus, saveCurrentMap, saveModalFocusBtn, saveModalReturnFocusId, saveRowAction, setDrawer, setGenerateBusy, setSaveModalFocusBtn, setSourceTab, setUploadBusy, showLoadedSavePreview, showRejoinNote, showSaveConfirm, showUploadPreview, showView, syncGenerateButton, syncLobbyButtons, syncSaveModal, syncTabStyles, syncUploadButton, uploadMap };
