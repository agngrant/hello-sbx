"use strict";
/*
 * LittleDungeons frontend harness — executes the REAL app/static/app.js under Node
 * with a stub DOM / WebSocket so unit tests (tests/test_frontend.py) can
 * actually run the frontend logic instead of just inspecting it as a string.
 *
 * The JS source is eval'd into this function scope, so every top-level
 * `function`/`let` in app.js is reachable here and re-exported on the
 * returned API object.
 */

const fs = require("fs");

/* ---- controllable timer (BUG-003) --------------------------------------
   Mirrors the setTimeout/clearTimeout contract app.js relies on:
   - schedule(delay, fn) -> id
   - clear(id)
   - advance(ms) fires any timers whose deadline <= the virtual clock.
*/
function makeTimer() {
  let base = 0;      // virtual clock
  let next = 1;      // next timer id
  const byId = new Map();
  const order = [];  // scheduled entries, kept sorted by deadline
  const resort = () => order.sort((a, b) => a.t - b.t);
  const api = {
    base: () => base,
    schedule(delay, fn) {
      const id = next++;
      const e = { id, t: base + (delay || 0), fn };
      byId.set(id, e);
      order.push(e);
      resort();
      return id;
    },
    clear(id) {
      const e = byId.get(id);
      if (!e) return;
      byId.delete(id);
      const i = order.indexOf(e);
      if (i >= 0) order.splice(i, 1);
    },
    advance(ms) {
      base += ms;
      let guard = 0;
      while (order.length && order[0].t <= base) {
        const e = order.shift();
        byId.delete(e.id);
        e.fn();
        resort();
        if (++guard > 100000) break;
      }
    },
    pending() { return order.length; },
  };
  return api;
}

/* ---- send / WebSocket capture (BUG-002, 007, 008, 011) ----------------- */
function makeSend() {
  return {
    sent: [],      // parsed objects pushed through the live socket
    urls: [],      // every WebSocket(url) constructed
    wsObj: null,   // the most recent socket object
    reset() { this.sent.length = 0; this.urls.length = 0; this.wsObj = null; },
  };
}

/* No-op 2D canvas context: every method is a no-op, every prop settable,
   measureText returns a fixed width so label sizing never throws.
   ``arc`` and ``fillText`` are RECORDED (per canvas element, in draw order)
   so the Python tests can assert WHERE markers are drawn and WHICH labels
   are drawn (the awareness tier-rendering tests). */
function makeCtx(el) {
  const noop = function () { return undefined; };
  return {
    _el: el,
    _arcs: [],      // [cx, cy, r] in draw order
    _texts: [],     // fillText strings in draw order
    fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1,
    font: "", textAlign: "", textBaseline: "",
    fillRect: noop, strokeRect: noop, clearRect: noop, beginPath: noop,
    moveTo: noop, lineTo: noop,
    arc(cx, cy, r) { this._arcs.push([cx, cy, r]); },
    arcTo: noop, rect: noop, closePath: noop,
    fill: noop, stroke: noop, save: noop, restore: noop,
    clip: noop, setTransform: noop, transform: noop, setLineDash: noop,
    fillText(t, x, y) { this._texts.push(String(t)); },
    strokeText: noop,
    measureText() { return { width: 10 }; },
  };
}

function makeEl() {
  const el = {
    id: "", textContent: "", value: "", checked: false,
    disabled: false, tabIndex: 0, innerHTML: "", files: [],
    style: {}, dataset: {},
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
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      const l = this._listeners[type];
      if (l) {
        const i = l.indexOf(fn);
        if (i >= 0) l.splice(i, 1);
      }
    },
    // Lets tests trigger registered handlers (e.g. a button click) through
    // the REAL app.js code path instead of calling the handler directly.
    dispatchEvent(ev) {
      for (const fn of this._listeners[ev.type] || []) fn(ev);
      return true;
    },
    setAttribute() {}, getAttribute() { return null; },
    setAttribute() {}, getAttribute() { return null; },
    appendChild(c) { return c; }, removeChild() {}, remove() {},
    insertBefore() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 600 }; },
    setPointerCapture() {},
    closest() { return null; }, children: { length: 0 }, firstChild: null,
  };
  // Model the `hidden` attribute (HTML semantics: the attribute is present
  // for every stubbed id, i.e. the element starts hidden — JS explicitly
  // re-sets .hidden wherever visibility matters). Backed by the classList
  // set so the two stay consistent.
  Object.defineProperty(el, "hidden", {
    get() { return el.classList._s.has("hidden"); },
    set(v) {
      if (v) el.classList._s.add("hidden");
      else el.classList._s.delete("hidden");
    },
    configurable: true,
    enumerable: true,
  });
  el.classList._s.add("hidden");   // the attribute is present at creation
  // One shared context per canvas element (arc/fillText recordings persist
  // across the layout -> render passes within a single test expression).
  let _ctx = null;
  el.getContext = () => { if (!_ctx) _ctx = makeCtx(el); return _ctx; };
  return el;
}

function buildApi() {
  const APPJS_PATH = process.env.APPJS_PATH;
  const timer = makeTimer();
  const __SEND = makeSend();

  const registry = {};
  const document = {
    querySelector(sel) {
      const id = sel.replace("#", "");
      if (!registry[id]) { registry[id] = makeEl(); registry[id].id = id; }
      return registry[id];
    },
    querySelectorAll() { return []; },
    createElement() { return makeEl(); },
    addEventListener() {},
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    title: "",
  };
  const window = {
    matchMedia() { return { matches: false }; },
    addEventListener() {},
    devicePixelRatio: 1,
  };
  const location = { protocol: "http:", host: "127.0.0.1:8000" };

  const WebSocket = class {
    constructor(url) {
      this.url = url; this.readyState = 1; // OPEN
      this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
      __SEND.wsObj = this;
      __SEND.urls.push(url);
    }
    send(data) { __SEND.sent.push(JSON.parse(data)); }
    close() { if (this.onclose) this.onclose(); }
  };
  WebSocket.OPEN = 1;
  // Recorded fetch stub (generated-maps spec C12, optional): every call is
  // captured in __FETCH.sent; the Promise resolves with __FETCH.response so
  // tests can drive generateMap() end-to-end. The old behavior (hard reject
  // of "no network in harness") is restored by __FETCH.hardReject = true.
  const __FETCH = { sent: [], response: null, hardReject: false, reset() {
    this.sent.length = 0; this.response = null; this.hardReject = false; } };
  const fetch = (url, opts) => {
    __FETCH.sent.push({ url, opts });
    if (__FETCH.hardReject) {
      return Promise.reject(new Error("no network in harness"));
    }
    return Promise.resolve(__FETCH.response);
  };
  const FileReader = class { readAsDataURL() {} };
  // Shadow the globals so app.js drives the controllable timer.
  const setTimeout = (fn, ms) => timer.schedule(ms, fn);
  const clearTimeout = (id) => timer.clear(id);

  const src = fs.readFileSync(APPJS_PATH, "utf8");
  // Re-export the app's top-level functions/state for the Python tests.
  const EXPORTS =
    ";global.__TAPI__ = { state, els, document," +
    "allEntities, onPath, stopAnim, findEntity, isAnimating," +
    "applyState, onWelcome, onState, onServerMessage, onError," +
    "entityAtCell, drawSidebar, renderAll, drawDot, drawUnknownDot," +
    "openUploadedMap, sendMove, selectEntity," +
    "createEntity, toggleFog, canvasHint, showGmFirstRunHint, dismissGmFirstRunHint, updateControlHint," +
    "join, connectWs, setConn, scheduleReconnect, showView, wsSend, wsUrl," +
    "uploadMap, generateMap, showUploadPreview, resetUploadForm, setSourceTab, syncTabStyles, syncGenerateButton, setGenerateBusy, setUploadBusy, syncUploadButton," +
    "_timer: timer, _send: __SEND, _fetch: __FETCH }";
  // eslint-disable-next-line no-eval
  eval(src + EXPORTS);

  const api = global.__TAPI__;
  api._timer = timer;
  api._send = __SEND;
  api._ws = WebSocket;
  return api;
}

module.exports = { buildApi };
