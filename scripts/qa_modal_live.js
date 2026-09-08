"use strict";
/* QA live probe — drives the REAL app/static/app.js save-delete MODAL path
   against a LIVE backend on an ephemeral port.

   Unlike tests/js/harness.js (which stubs fetch with a response queue), this
   driver does NOT shadow `fetch` — so every fetch the app makes (the DELETE
   and the follow-up re-GET) is a REAL HTTP call to the running server. This
   is the "real DELETE via the modal path" evidence for the sign-off: no
   headless browser is available in this environment (team convention — the
   harness IS the team's browser substitute), so this is the strongest
   harness-level modal drive possible.

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

const fs = require("fs");
const path = require("path");

const PORT = process.argv[2];
if (!PORT) { console.error("usage: node qa_modal_live.js <port>"); process.exit(2); }
const ROOT = path.join(__dirname, "..");
const APPJS = path.join(ROOT, "app", "static", "app.js");

const result = { ok: false, steps: {} };
function step(name, cond, detail) {
  result.steps[name] = { ok: !!cond, detail: detail || "" };
  if (!cond) result.ok = false;
}

/* ---- DOM stub (copied contract from tests/js/harness.js) ---------------- */
function makeEl() {
  const el = {
    id: "", textContent: "", value: "", checked: false, disabled: false,
    tabIndex: 0, innerHTML: "", files: [], title: "", style: {}, dataset: {},
    clientWidth: 800, clientHeight: 600, width: 0, height: 0, src: "",
    classList: {
      _s: new Set(),
      add(...cs) { for (const c of cs) this._s.add(c); },
      remove(...cs) { for (const c of cs) this._s.delete(c); },
      toggle(c, force) {
        const on = force === undefined ? !this._s.has(c) : !!force;
        if (on) this._s.add(c); else this._s.delete(c);
        return on;
      },
      contains(c) { return this._s.has(c); },
    },
    _listeners: {},
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      const l = this._listeners[type]; if (!l) return;
      const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1);
    },
    dispatchEvent(ev) { for (const fn of this._listeners[ev.type] || []) fn(ev); return true; },
    setAttribute() {}, getAttribute() { return null; },
    children: [], parentNode: null,
    appendChild(c) {
      if (c.parentNode && c.parentNode.children) {
        const i = c.parentNode.children.indexOf(c);
        if (i >= 0) c.parentNode.children.splice(i, 1);
      }
      this.children.push(c); c.parentNode = this; this.firstChild = this.children[0];
      return c;
    },
    removeChild(c) {
      const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1);
      if (c.parentNode === this) c.parentNode = null;
      this.firstChild = this.children[0] || null; return c;
    },
    remove() { if (this.parentNode && this.parentNode.children) this.parentNode.removeChild(this); },
    insertBefore() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 600 }; },
    setPointerCapture() {}, closest() { return null; },
    focus() {},
  };
  Object.defineProperty(el, "hidden", {
    get() { return el.classList._s.has("hidden"); },
    set(v) { if (v) el.classList._s.add("hidden"); else el.classList._s.delete("hidden"); },
    configurable: true, enumerable: true,
  });
  el.classList._s.add("hidden");
  let _ctx = null;
  const noop = () => undefined;
  const ctx = {
    fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1, font: "",
    textAlign: "", textBaseline: "",
    fillRect: noop, strokeRect: noop, clearRect: noop, beginPath: noop,
    moveTo: noop, lineTo: noop, rect: noop, arc: noop, arcTo: noop, closePath: noop,
    fill: noop, stroke: noop, save: noop, restore: noop, clip: noop,
    setTransform: noop, transform: noop, setLineDash: noop, fillText: noop,
    strokeText: noop, measureText: () => ({ width: 10 }),
    createRadialGradient: () => ({ addColorStop: noop }),
  };
  el.getContext = () => ctx;
  return el;
}

const registry = {};
const document = {
  querySelector(sel) {
    const id = sel.replace("#", "");
    if (!registry[id]) { registry[id] = makeEl(); registry[id].id = id; }
    return registry[id];
  },
  querySelectorAll() { return []; },
  createElement() { return makeEl(); },
  _listeners: {},
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
  removeEventListener(type, fn) {
    const l = this._listeners[type]; if (!l) return;
    const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1);
  },
  dispatch(type, ev) { for (const fn of this._listeners[type] || []) fn(ev); },
  body: makeEl(),
  title: "",
};
const window = {
  matchMedia: () => ({ matches: false }),
  _listeners: {},
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
  removeEventListener(type, fn) {
    const l = this._listeners[type]; if (!l) return;
    const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1);
  },
  dispatch(type, ev) { for (const fn of this._listeners[type] || []) fn(ev); },
  devicePixelRatio: 1,
};
const requestAnimationFrame = (fn) => { return setTimeout(fn, 0); };
const location = { protocol: "http:", host: "127.0.0.1:" + PORT };

// WebSocket stub: never opens, so wsSend() no-ops and no reconnect is armed.
// (We are driving the REST delete path, not the WS path, in this probe.)
const WebSocket = class {
  constructor(url) { this.url = url; this.readyState = 0; }
  send() {}
  close() {}
};
WebSocket.OPEN = 1;

// NOTE: Node's global fetch cannot parse RELATIVE urls (the app issues
// "/api/saves/..."), so wrap it to prefix the live origin. Every app fetch
// still becomes a REAL HTTP call to the running server on PORT.
const BASE = "http://127.0.0.1:" + PORT;
const _realFetch = global.fetch;
global.fetch = (url, opts) => _realFetch(
  String(url).startsWith("http") ? url : BASE + url, opts);

const src = fs.readFileSync(APPJS, "utf8");
const EXPORTS =
  ";global.__TAPI__ = { state, els, document," +
  "onWelcome, onState, onServerMessage, applyState, showView," +
  "refreshSaves, renderSaves, renderSavesTab, buildSaveRow," +
  "confirmDeleteSave, cancelSaveDelete, syncSaveModal, deleteSave }";

// eslint-disable-next-line no-eval
eval(src + EXPORTS);
const api = global.__TAPI__;

function poll(n) {
  if (api.els.saveDeleteModal.hidden) return Promise.resolve(true);
  if (n <= 0) return Promise.resolve(false);
  return new Promise((r) => setTimeout(() => r(poll(n - 1)), 15));
}

(async () => {
  try {
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
