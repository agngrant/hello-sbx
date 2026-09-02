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
   measureText returns a fixed width so label sizing never throws. */
function makeCtx() {
  const noop = function () { return undefined; };
  return {
    fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1,
    font: "", textAlign: "", textBaseline: "",
    fillRect: noop, strokeRect: noop, clearRect: noop, beginPath: noop,
    moveTo: noop, lineTo: noop, arc: noop, arcTo: noop, rect: noop,
    closePath: noop, fill: noop, stroke: noop, save: noop, restore: noop,
    clip: noop, setTransform: noop, transform: noop, fillText: noop,
    strokeText: noop, measureText() { return { width: 10 }; },
  };
}

function makeEl() {
  return {
    id: "", hidden: false, textContent: "", value: "", checked: false,
    disabled: false, tabIndex: 0, innerHTML: "", files: [],
    style: {}, dataset: {},
    clientWidth: 800, clientHeight: 600, width: 0, height: 0, src: "",
    classList: {
      _s: new Set(),
      add() {}, remove() {}, toggle() {}, contains() { return false; },
    },
    addEventListener() {}, removeEventListener() {},
    setAttribute() {}, getAttribute() { return null; },
    appendChild(c) { return c; }, removeChild() {}, remove() {},
    insertBefore() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 600 }; },
    setPointerCapture() {},
    getContext() { return makeCtx(); },
    closest() { return null; }, children: { length: 0 }, firstChild: null,
  };
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
  const fetch = () => Promise.reject(new Error("no network in harness"));
  const FileReader = class { readAsDataURL() {} };
  // Shadow the globals so app.js drives the controllable timer.
  const setTimeout = (fn, ms) => timer.schedule(ms, fn);
  const clearTimeout = (id) => timer.clear(id);

  const src = fs.readFileSync(APPJS_PATH, "utf8");
  // Re-export the app's top-level functions/state for the Python tests.
  const EXPORTS =
    ";global.__TAPI__ = { state, els," +
    "allEntities, onPath, stopAnim, findEntity, isAnimating," +
    "applyState, onWelcome, onState, onServerMessage, onError," +
    "entityAtCell, drawSidebar, openUploadedMap, sendMove, selectEntity," +
    "join, connectWs, setConn, scheduleReconnect, showView, wsSend, wsUrl," +
    "_timer: timer, _send: __SEND }";
  // eslint-disable-next-line no-eval
  eval(src + EXPORTS);

  const api = global.__TAPI__;
  api._timer = timer;
  api._send = __SEND;
  api._ws = WebSocket;
  return api;
}

module.exports = { buildApi };
