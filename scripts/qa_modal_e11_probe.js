"use strict";
/* QA coverage-gap probes (NOT a product-bug probe) — run the REAL
   app/static/js module graph (entry js/main.js, via the shared harness'
   buildApi() in tests/js/harness.js) under Node to confirm the E1
   (idempotent same-id re-entry) and E11 (unknown-id membership guard) code
   paths behave as specified, so the sign-off can state whether they are
   code-correct-but-untested. Prints RESULT_JSON.

   The old single-file eval is gone with the module split; the fetch stub
   is installed AFTER buildApi() (the app resolves bare `fetch` against the
   global at call time) so the "no request fired" assertions observe real
   fetch traffic. (The original probe read api._fetchCalled while its stub
   wrote result._fetchCalled — a vacuous assertion — now fixed.)
 */
const path = require("path");
const ROOT = path.join(__dirname, "..");

process.env.APPJS_PATH = path.join(ROOT, "app", "static", "js", "main.js");
process.env.INDEX_HTML_PATH = path.join(ROOT, "app", "static", "index.html");
const { buildApi } = require(path.join(ROOT, "tests", "js", "harness.js"));

const result = { ok: false, steps: {} };
function step(n, c, d) { result.steps[n] = { ok: !!c, detail: d || "" }; if (!c) result.ok = false; }

(async () => {
  const api = await buildApi();

  // No-network contract: every fetch call is flagged + answered ok so a
  // request on these paths would be caught by the step below.
  result._fetchCalled = false;
  globalThis.fetch = (u, o) => { result._fetchCalled = true;
    return Promise.resolve({ ok: true, json: async () => ({}) }); };

  try {
    // Fresh GM session.
    api.state.role="gm"; api.state.saves=[{id:"act-1",name:"Act Three",map_name:"M",width:24,height:16,created_at:"2025-01-01T12:00:00",entity_count:4}];
    api.renderSaves(); api.renderSavesTab();

    // E11: unknown id -> error toast, NO modal open, NO fetch.
    result._fetchCalled=false;
    api.confirmDeleteSave("does-not-exist");
    const e11 = {
      hidden: api.els.saveDeleteModal.hidden,
      confirming: api.state.confirmingSaveId,
      fetched: result._fetchCalled,
      toasts: api.els.toasts.children.map(t=>(t.children&&t.children[0])?t.children[0].textContent:t.textContent),
    };
    step("E11_unknown_id_no_modal", e11.hidden===true && e11.confirming===null,
      "hidden="+e11.hidden+" confirming="+e11.confirming);
    step("E11_unknown_id_toast", e11.toasts.some(t=>String(t).indexOf("save not found: does-not-exist")>=0),
      JSON.stringify(e11.toasts));
    step("E11_unknown_id_no_fetch", e11.fetched===false, "fetched="+e11.fetched);

    // E1: programmatic same-id double-call is idempotent (one modal, no fetch,
    // no duplicate element, no extra toast).
    api.state.saves=[{id:"act-1",name:"Act Three",map_name:"M",width:24,height:16,created_at:"2025-01-01T12:00:00",entity_count:4}];
    api.renderSaves(); api.renderSavesTab();
    result._fetchCalled=false;
    api.confirmDeleteSave("act-1");
    api.confirmDeleteSave("act-1"); // double call
    // No-duplication check: the double re-entry must have left exactly ONE
    // rebuilt body (name + meta + note), not appended a second set.
    const bodyKids = api.els.saveDeleteModalBody.children.length;
    const e1 = {
      hidden: api.els.saveDeleteModal.hidden,
      confirming: api.state.confirmingSaveId,
      fetched: result._fetchCalled,
      bodyName: (api.els.saveDeleteModalBody.children[0]||{}).textContent,
      bodyKids: bodyKids,
    };
    step("E1_double_call_one_modal", e1.hidden===false && e1.confirming==="act-1"
      && bodyKids===3,
      "hidden="+e1.hidden+" confirming="+e1.confirming+" bodyKids="+bodyKids);
    step("E1_double_call_no_fetch", e1.fetched===false, "fetched="+e1.fetched);
    step("E1_double_call_names_correct", (e1.bodyName||"").indexOf('Delete "Act Three"?')>=0, e1.bodyName);

    result.ok = Object.values(result.steps).every(s=>s.ok);
    console.log("RESULT_JSON "+JSON.stringify(result));
    process.exit(result.ok?0:1);
  } catch(e){
    result.error=(e&&e.stack)||String(e);
    console.log("RESULT_JSON "+JSON.stringify(result));
    process.exit(1);
  }
})();
