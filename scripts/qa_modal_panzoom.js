"use strict";
/* QA regression probe — pan / zoom / drawer behavior with the save-delete
   modal CLOSED (save-load-delete-modal spec AC14 "no regressions").

   Drives the REAL app/static/js module graph (entry js/main.js, via the
   shared harness' buildApi() in tests/js/harness.js) document keydown
   handler + drawer handlers under Node and asserts the normal
   pan/zoom/drawer behavior is intact when the modal is closed:
   ArrowLeft pans, +/- zoom, Escape closes the drawer, and the sidebar
   toggle / scrim click still open and close the drawer. A final step
   re-opens the modal to prove the interaction lock still applies (the
   guard from rule 4), then closes it and re-pans to prove the guard
   releases. Prints a JSON result; exits 0/1.
 */

const path = require("path");

const ROOT = path.join(__dirname, "..");

process.env.APPJS_PATH = path.join(ROOT, "app", "static", "js", "main.js");
process.env.INDEX_HTML_PATH = path.join(ROOT, "app", "static", "index.html");
const { buildApi } = require(path.join(ROOT, "tests", "js", "harness.js"));

const result = { ok: false, steps: {} };
function step(name, cond, detail) {
  result.steps[name] = { ok: !!cond, detail: detail || "" };
  if (!cond) result.ok = false;
}

const key = (k) => ({ key: k, target: { tagName: "BODY" }, preventDefault() {} });

(async () => {
  const api = await buildApi();
  const view = () => ({ level: api.state.view.level,
                        panX: api.state.view.panX, panY: api.state.view.panY });

  try {
    // ---- set up a joined GM on a 20x17 map at level 4, panned (2,3) ----
    api.state.joined = true;
    api.state.role = "gm";
    api.state.grid = { width: 20, height: 17, cells: [] };
    api.state.view = { level: 4, panX: 2, panY: 3 };
    api.els.canvasWrap.clientWidth = 816;
    api.els.canvasWrap.clientHeight = 416;
    api.els.mapView.hidden = false;
    api.els.uploadView.hidden = true;
    api.state.confirmingSaveId = null;
    api.state.savesDeleteBusy = false;
    step("modal_closed_initial", api.els.saveDeleteModal.hidden === true
      && api.state.confirmingSaveId === null,
      "hidden=" + api.els.saveDeleteModal.hidden);

    // ---- pan with ArrowLeft (modal closed) ----
    const v0 = view();
    api.document.dispatch("keydown", key("ArrowLeft"));
    const v1 = view();
    step("arrow_left_pans_when_closed",
      v1.panX === 0 && v1.panY === 3, "before=" + JSON.stringify(v0) + " after=" + JSON.stringify(v1));

    // ---- zoom out with '-' (level 4 -> 5) ----
    const lv0 = api.state.view.level;
    api.document.dispatch("keydown", key("-"));
    step("minus_zooms_out_when_closed", api.state.view.level === lv0 + 1,
      "before=" + lv0 + " after=" + api.state.view.level);
    api.document.dispatch("keydown", key("+"));
    step("plus_zooms_in_when_closed", api.state.view.level === lv0,
      "after=" + api.state.view.level);

    // ---- drawer: toggle open -> scrim shows; scrim click closes ----
    api.els.sidebarToggle.dispatchEvent({ type: "click", stopPropagation() {} });
    step("toggle_opens_drawer",
      api.els.sidebar.classList.contains("is-open") === true
      && api.els.scrim.hidden === false,
      "isOpen=" + api.els.sidebar.classList.contains("is-open") + " scrimHidden=" + api.els.scrim.hidden);
    api.els.scrim.dispatchEvent({ type: "click", stopPropagation() {} });
    step("scrim_click_closes_drawer",
      api.els.sidebar.classList.contains("is-open") === false
      && api.els.scrim.hidden === true,
      "isOpen=" + api.els.sidebar.classList.contains("is-open") + " scrimHidden=" + api.els.scrim.hidden);

    // ---- re-open the modal, confirm the lock, close it, re-pan ----
    // Reset the view mid-map (the zoom round-trip re-clamped pan to the 0,0
    // left edge) so BOTH the lock and the release are meaningful (a Left pan
    // is possible in both cases, so it can only be blocked by the modal).
    api.state.view = { level: 4, panX: 2, panY: 3 };
    api.state.saves = [{ id: "zz", name: "ZZ", map_name: "m", width: 8, height: 6,
                         created_at: "2025-01-01T00:00:00", entity_count: 1 }];
    api.renderSaves(); api.renderSavesTab();
    api.confirmDeleteSave("zz");
    step("modal_reopens", api.els.saveDeleteModal.hidden === false
      && api.state.confirmingSaveId === "zz",
      "hidden=" + api.els.saveDeleteModal.hidden);
    const vl0 = view();
    api.document.dispatch("keydown", key("ArrowLeft"));
    const vl1 = view();
    step("arrow_locked_while_open",
      JSON.stringify(vl0) === JSON.stringify(vl1) && vl0.panX === 2,
      "before=" + JSON.stringify(vl0) + " after=" + JSON.stringify(vl1));
    api.document.dispatch("keydown", key("Escape"));
    step("escape_closes_modal", api.els.saveDeleteModal.hidden === true
      && api.state.confirmingSaveId === null,
      "hidden=" + api.els.saveDeleteModal.hidden + " confirming=" + api.state.confirmingSaveId);
    const v2 = view();
    api.document.dispatch("keydown", key("ArrowLeft"));
    const v3 = view();
    step("arrow_pans_again_after_close",
      v2.panX === 2 && v3.panX === 0 && v3.panY === 3,
      "before=" + JSON.stringify(v2) + " after=" + JSON.stringify(v3));

    result.ok = Object.values(result.steps).every((s) => s.ok);
    console.log("RESULT_JSON " + JSON.stringify(result));
    process.exit(result.ok ? 0 : 1);
  } catch (e) {
    result.error = (e && e.stack) || String(e);
    console.log("RESULT_JSON " + JSON.stringify(result));
    process.exit(1);
  }
})();
