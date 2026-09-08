"use strict";
/* QA regression probe — pan / zoom / drawer behavior with the save-delete
   modal CLOSED (save-load-delete-modal spec AC14 "no regressions").

   Drives the REAL app/static/app.js document keydown handler + drawer
   handlers under Node (DOM stub contract from tests/js/harness.js) and
   asserts the normal pan/zoom/drawer behavior is intact when the modal is
   closed: ArrowLeft pans, +/- zoom, Escape closes the drawer, and the
   sidebar toggle / scrim click still open and close the drawer. A final
   step re-opens the modal to prove the interaction lock still applies (the
   guard from rule 4), then closes it and re-pans to prove the guard
   releases. Prints a JSON result; exits 0/1.
*/

const fs = require("fs");
const path = require("path");

const PORT = process.argv[2] || "8000";
const ROOT = path.join(__dirname, "..");
const APPJS = path.join(ROOT, "app", "static", "app.js");

const result = { ok: false, steps: {} };
function step(name, cond, detail) {
  result.steps[name] = { ok: !!cond, detail: detail || "" };
  if (!cond) result.ok = false;
}

function makeEl() {
  const el = {
    id: "", textContent: "", value: "", checked: false, disabled: false,
    tabIndex: 0, innerHTML: "", files: [], title: "", tagName: "DIV",
    style: {}, dataset: {}, clientWidth: 800, clientHeight: 600,
    width: 0, height: 0, src: "",
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
    setPointerCapture() {}, closest() { return null; }, focus() {},
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
  el.getContext = () => { if (!_ctx) _ctx = ctx; return _ctx; };
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
const WebSocket = class {
  constructor(url) { this.url = url; this.readyState = 0; }
  send() {}
  close() {}
};
WebSocket.OPEN = 1;

const src = fs.readFileSync(APPJS, "utf8");
const EXPORTS =
  ";global.__TAPI__ = { state, els, document," +
  "confirmDeleteSave, cancelSaveDelete, renderSaves, renderSavesTab, panBy, zoomBy }";
// eslint-disable-next-line no-eval
eval(src + EXPORTS);
const api = global.__TAPI__;

const key = (k) => ({ key: k, target: { tagName: "BODY" }, preventDefault() {} });
const view = () => ({ level: api.state.view.level,
                      panX: api.state.view.panX, panY: api.state.view.panY });

function main() {
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
}

try { main(); } catch (e) {
  result.error = (e && e.stack) || String(e);
  console.log("RESULT_JSON " + JSON.stringify(result));
  process.exit(1);
}
