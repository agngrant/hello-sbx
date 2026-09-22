"use strict";
/* QA live probe — drives the REAL app/static/js module graph (entry
   js/main.js; it imports state/render/game/net/ui) save-delete MODAL
   path against a LIVE backend on an ephemeral port.

   Unlike tests/js/harness.js's test default (fetch stubbed with a
   response queue), this driver does NOT keep the fetch stub: right after
   boot it swaps in Node's REAL fetch (prefixed with the live origin), so
   every fetch the app makes (the DELETE and the follow-up re-GET) is a
   REAL HTTP call to the running server. This is the "real DELETE via the
   modal path" evidence for the sign-off: no headless browser is available
   in this environment (team convention — the harness IS the team's browser
   substitute), so this is the strongest harness-level modal drive
   possible.

   The app now runs as ES modules, so instead of eval'ing the old
   single-file source this probe reuses the shared harness' buildApi()
   (tests/js/harness.js): the same stub DOM/WebSocket contract, with the
   real module    graph imported under Node. Three globals are captured before
   buildApi() swaps them: Node's native fetch (the app's relative
   "/api/saves/..." calls need the live origin prefix) and the real
   setTimeout/clearTimeout pair (the probe's own polling loop must not
   land on the harness' virtual clock, which only fires when advanced by
   a test; and undici's internal keep-alive timers need the real pair's
   .unref()-bearing handles — see below).

   argv: <port>
   Steps:
     1. GET /api/saves (real) -> pick the first non-corrupt save.
     2. Set app state to GM with that list; renderSaves/renderSavesTab.
     3. confirmDeleteSave(id) -> the modal must open (hidden=false).
     4. Dispatch a real click on #save-delete-modal-confirm -> the app's
        async Confirm handler runs deleteSave(id) -> REAL DELETE over HTTP.
     5. Await the settled modal, then assert: modal closed, confirming null,
        busy false, row removed from BOTH lists, success toast present, and
        (separately) the file is gone server-side (python re-checks REST).
   Prints a JSON result object and exits 0/1.
 */

const path = require("path");

const PORT = process.argv[2];
if (!PORT) { console.error("usage: node qa_modal_live.js <port>"); process.exit(2); }
const ROOT = path.join(__dirname, "..");

// Captured BEFORE buildApi() replaces the globals (see header).
const nodeFetch = globalThis.fetch;
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;

process.env.APPJS_PATH = path.join(ROOT, "app", "static", "js", "main.js");
process.env.INDEX_HTML_PATH = path.join(ROOT, "app", "static", "index.html");
const { buildApi } = require(path.join(ROOT, "tests", "js", "harness.js"));

const result = { ok: false, steps: {} };
function step(name, cond, detail) {
  result.steps[name] = { ok: !!cond, detail: detail || "" };
  if (!cond) result.ok = false;
}

const BASE = "http://127.0.0.1:" + PORT;

(async () => {
  try {
    const api = await buildApi();

    // Drop the harness' recorded fetch stub: every app fetch from here on
    // is a REAL HTTP call to the running server on PORT.
    globalThis.fetch = (url, opts) => nodeFetch(
      String(url).startsWith("http") ? url : BASE + url, opts);

    // undici (Node's fetch engine) uses the GLOBAL setTimeout/clearTimeout
    // for its internal keep-alive timers and calls .unref() on the returned
    // handle — the harness' virtual clock returns a plain number, so real
    // HTTP would crash it. Restore the real pair now that boot (the only
    // phase that needs the virtual clock) is done; any app timer scheduled
    // afterwards (e.g. toast auto-dismiss) just runs on the real clock,
    // which is harmless for this probe (it exits before they fire).
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;

    function poll(n) {
      if (api.els.saveDeleteModal.hidden) return Promise.resolve(true);
      if (n <= 0) return Promise.resolve(false);
      return new Promise((r) => realSetTimeout(() => r(poll(n - 1)), 15));
    }

    // 1. Real GET /api/saves.
    const lr = await fetch("http://127.0.0.1:" + PORT + "/api/saves");
    const ldata = await lr.json();
    const saves = ldata.saves || [];
    const target = saves.find((s) => !s.corrupt) || saves[0];
    step("live_get_saves", !!target && lr.ok, "count=" + saves.length +
      " target=" + (target && target.id));
    if (!target) throw new Error("no save to delete; create one first");

    // 2. Put the app in a GM state with the live list.
    api.state.role = "gm";
    api.state.saves = saves.slice();
    api.renderSaves();
    api.renderSavesTab();

    const listBefore = {
      sidebar: api.els.savesList.children.filter((r) => r.dataset.id === target.id).length,
      tab: api.els.savesTabList.children.filter((r) => r.dataset.id === target.id).length,
    };
    step("row_rendered_before", listBefore.sidebar === 1 && listBefore.tab === 1,
      JSON.stringify(listBefore));

    // 3. Open the modal for the target.
    api.confirmDeleteSave(target.id);
    step("modal_open", api.els.saveDeleteModal.hidden === false
      && api.state.confirmingSaveId === target.id,
      "hidden=" + api.els.saveDeleteModal.hidden +
      " confirming=" + api.state.confirmingSaveId);
    const nameTxt = (api.els.saveDeleteModalBody.children[0] || {}).textContent || "";
    step("modal_names_save", nameTxt.indexOf('Delete "' + target.name + '"?') >= 0,
      nameTxt);

    // 4. Click the real Confirm button -> app's async handler -> REAL DELETE.
    api.els.saveDeleteModalConfirm.dispatchEvent({ type: "click", stopPropagation() {} });

    // Busy immediately after the click (E2): modal still open, confirm disabled.
    const busy = {
      hidden: api.els.saveDeleteModal.hidden,
      busy: api.state.savesDeleteBusy,
      disabled: api.els.saveDeleteModalConfirm.disabled,
      label: api.els.saveDeleteModalConfirm.textContent,
    };
    step("busy_while_inflight", busy.hidden === false && busy.busy === true
      && busy.disabled === true && busy.label === "Deleting…", JSON.stringify(busy));

    // 5. Wait for the modal to settle (resolution-driven close).
    const settled = await poll(80);
    step("modal_settled", settled, "settled=" + settled);

    const sidebarAfter = api.els.savesList.children.filter((r) => r.dataset.id === target.id).length;
    const tabAfter = api.els.savesTabList.children.filter((r) => r.dataset.id === target.id).length;
    const toasts = api.els.toasts.children.map((t) =>
      (t.children && t.children[0]) ? t.children[0].textContent : t.textContent);
    step("row_removed_both_lists", sidebarAfter === 0 && tabAfter === 0,
      "sidebar=" + sidebarAfter + " tab=" + tabAfter);
    step("success_toast", toasts.some((t) => String(t).indexOf('Deleted "' + target.name + '"') >= 0),
      JSON.stringify(toasts));
    step("state_cleared", api.state.confirmingSaveId === null && api.state.savesDeleteBusy === false,
      "confirming=" + api.state.confirmingSaveId + " busy=" + api.state.savesDeleteBusy);

    result.deletedId = target.id;
    result.deletedName = target.name;
    result.ok = Object.values(result.steps).every((s) => s.ok);
    console.log("RESULT_JSON " + JSON.stringify(result));
    process.exit(result.ok ? 0 : 1);
  } catch (e) {
    result.error = (e && e.stack) || String(e);
    console.log("RESULT_JSON " + JSON.stringify(result));
    process.exit(1);
  }
})();
