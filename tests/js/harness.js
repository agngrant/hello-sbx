"use strict";
/*
 * LittleDungeons frontend harness — executes the REAL app/static/js/* ES
 * module graph (main.js is the entry point; it imports state/render/game/
 * net/ui) under Node with a stub DOM / WebSocket so unit tests
 * (tests/test_frontend.py) can actually run the frontend logic instead of
 * just inspecting it as a string.
 *
 * buildApi() is async: it installs the stubs on globalThis (ES modules
 * resolve bare identifiers against the global scope — there is no enclosing
 * function scope to hide them in), then dynamically imports the module graph.
 * Importing main.js runs the app's boot (registerListeners + the five boot
 * calls), exactly as the classic script did at load time. Each buildApi()
 * call runs in its own fresh Node process (the Python runner spawns one per
 * expression), so the ESM cache never leaks state between tests.
 *
 * The returned API object merges the five modules' export namespaces with
 * LIVE getters (a named export is a live binding — e.g. `ws` and `hoverCell`
 * keep tracking the values set through setWs/setHoverCell) plus the harness
 * handles (_timer, _send, _fetch, _rfr, _window, _ws) and the document stub.
 */

const fs = require("fs");
const path = require("path");

/* ---- controllable timer (BUG-003) --------------------------------------
    Mirrors the setTimeout/clearTimeout contract the app relies on:
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
  const path = [];       // {m:[x,y], l:[x,y]} | {r:[x,y,w,h]} — stroke() snapshot
  let m = null;
  return {
    _el: el,
    _arcs: [],      // [cx, cy, r] in draw order
    _texts: [],     // fillText strings in draw order
    _fills: [],     // {x,y,w,h,style} fillRect calls in draw order
    _strokes: [],   // {style, path:[...]} per stroke() call, in draw order
    _rects: [],     // {x,y,w,h,style} strokeRect calls in draw order (door border)
    _fillPaths: [], // {style, path} fill() calls in draw order (round-rect / leaf fills)
    _gradients: [], // {x0,y0,r0,x1,y1,r1, stops:[[offset,color],...]} in draw order
    fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1,
    font: "", textAlign: "", textBaseline: "",
    fillRect(x, y, w, h) { this._fills.push({ x: x, y: y, w: w, h: h,
                                               style: this.fillStyle }); },
    strokeRect(x, y, w, h) { this._rects.push({ x: x, y: y, w: w, h: h,
                                                style: this.strokeStyle }); },
    clearRect: noop, beginPath() { path.length = 0; m = null; },
    moveTo(x, y) { m = [x, y]; },
    lineTo(x, y) {
      if (m) { path.push({ m: m.slice(), l: [x, y] }); m = null; }
      else { path.push({ m: [x, y], l: [x, y] }); }
    },
    rect(x, y, w, h) { path.push({ r: [x, y, w, h] }); },
    arc(cx, cy, r) { this._arcs.push([cx, cy, r]); },
    createRadialGradient(x0, y0, r0, x1, y1, r1) {
      // Real stub gradient object (addColorStop recorded per object) so the
      // pictorial door glow (door-iconography spec §6.5) is testable.
      const g = { x0, y0, r0, x1, y1, r1, stops: [], addColorStop(off, col) {
        this.stops.push([off, col]); } };
      this._gradients.push(g);
      return g;
    },
    arcTo(x1, y1, x2, y2, r) { path.push({ a: [x1, y1, x2, y2, r] }); },
    closePath: noop,
    fill() {
      this._fillPaths.push({ style: this.fillStyle, path: path.slice() });
      path.length = 0; m = null;
    },
    stroke() {
      this._strokes.push({ style: this.strokeStyle, path: path.slice() });
      path.length = 0; m = null;
    },
    // Compositing model for the offscreen static grid layer (the app's
    // cached canvas, refined render path): blitting a harness canvas element
    // REPLAYS that layer's recorded draws into this context's recordings —
    // per category, in the layer's draw order — so assertions on the main
    // canvas stay meaningful: the main ctx ends up with exactly the entries
    // the pre-cache world produced (grid art drawn straight onto it), in the
    // same relative position (right after the background fill, before the
    // dynamic entity pass). Blitting anything with no recordings is a
    // no-op, as drawing an image the stub holds no pixels for would be.
    drawImage(img) {
      const src = (img && typeof img.getContext === "function")
        ? img.getContext("2d") : null;
      if (!src || !src._fills) return;
      for (const f of src._fills) this._fills.push(f);
      for (const s of src._strokes) this._strokes.push(s);
      for (const a of src._arcs) this._arcs.push(a.slice());
      for (const t of src._texts) this._texts.push(t);
      for (const r of src._rects) this._rects.push(r);
      for (const fp of src._fillPaths) this._fillPaths.push(fp);
      for (const g of src._gradients) this._gradients.push(g);
    },
    save: noop, restore: noop,
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
    title: "",
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
    // the REAL app code path instead of calling the handler directly.
    dispatchEvent(ev) {
      for (const fn of this._listeners[ev.type] || []) fn(ev);
      return true;
    },
    setAttribute() {}, getAttribute() { return null; },
    children: [],
    parentNode: null,
    appendChild(c) {
      // Real-DOM modeling: re-parent, track parent, keep firstChild fresh
      // (the app's toast capper loops on `firstChild.remove()`, which only
      // terminates when remove() actually splices the child out).
      if (c.parentNode && c.parentNode.children) {
        const i = c.parentNode.children.indexOf(c);
        if (i >= 0) c.parentNode.children.splice(i, 1);
      }
      this.children.push(c);
      c.parentNode = this;
      this.firstChild = this.children[0];
      return c;
    },
    removeChild(c) {
      const i = this.children.indexOf(c);
      if (i >= 0) this.children.splice(i, 1);
      if (c.parentNode === this) c.parentNode = null;
      this.firstChild = this.children[0] || null;
      return c;
    },
    remove() {
      if (this.parentNode && this.parentNode.children) {
        this.parentNode.removeChild(this);
      }
    },
    insertBefore() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 600 }; },
    setPointerCapture() {},
    closest() { return null; },
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

/* P1 join-blocking bug guard: extract the .door-swatch chips from the REAL
   index.html (the exact <i ...> tags the browser parses) as stub elements.
   When buildApi() is asked to (INDEX_HTML_PATH + LEGEND_SWATCHES set), it
   attaches them to the #legend stub so
   document.querySelector("#legend").querySelectorAll(".door-swatch")
   returns the ACTUAL chips and renderLegendDoorSwatches' loop body really
   runs at boot — the pre-fix code read T.floor there, before `const T` was
   initialized (a TDZ ReferenceError that locked every real browser out of
   the lobby, masked by this stub's ever-empty querySelectorAll).
   (tests/test_frontend.py::TestLobbyBootRegression is the regression
   guard. This is the team's substitute for a real-browser probe: no
   headless browser is available in this environment — the team's own
   qa_ui_smoke probe is a Node harness probe of the same kind.) */
function loadLegendSwatches(indexPath) {
   const html = fs.readFileSync(indexPath, "utf8");
   const chips = [];
   const re = /<i\s+class="door-swatch"([^>]*)><\/i>/g;
   let m;
   while ((m = re.exec(html))) {
     const attrs = m[1];
     const get = (name) => {
       const am = attrs.match(new RegExp(name + '="([^"]*)"'));
       return am ? am[1] : null;
     };
     const el = makeEl();
     el.tag = "i";
     el.dataset = { kind: get("data-kind"), state: get("data-state") };
     chips.push(el);
   }
   // Boss chip (boss-entity spec §6): the real index.html carries exactly
   // one bare <i class="boss-swatch"></i>; renderLegendBossSwatch must find
   // it via #legend.querySelector(".boss-swatch").
   const boss = [];
   if (/<i\s+class="boss-swatch"><\/i>/.test(html)) {
     const el = makeEl();
     el.tag = "i";
     boss.push(el);
   }
   return { door: chips, boss };
}

function buildApi() {
  const MAIN_JS_PATH = process.env.APPJS_PATH; // js/main.js (the entry point)
  const INDEX_HTML_PATH = process.env.INDEX_HTML_PATH;
  // LEGEND_SWATCHES defaults ON when INDEX_HTML_PATH is provided (opt out
  // with "0"): the suite wants the real lobby DOM by default.
   const chips = INDEX_HTML_PATH && process.env.LEGEND_SWATCHES !== "0"
    ? loadLegendSwatches(INDEX_HTML_PATH)
    : { door: [], boss: [] };
  const timer = makeTimer();
  const __SEND = makeSend();

  // Listener registries for the document / window stubs (pan-zoom §4/§E8):
  // the app registers a document keydown + a window resize handler; the tests
  // dispatch events into these so the REAL handler code runs.
  const __DOC_LISTENERS = {};
  const __WIN_LISTENERS = {};
  const __RFR = { queue: [], renderCalls: 0, dispatch() {
    const q = this.queue; this.queue = [];
    for (const fn of q) { this.renderCalls += 1; fn(); }
  } };

  const registry = {};
  const document = {
    querySelector(sel) {
      const id = sel.replace("#", "");
      if (!registry[id]) { registry[id] = makeEl(); registry[id].id = id; }
      const el = registry[id];
      if (el.id === "legend") {
        // P1 join-blocking bug guard: #legend reports the REAL index.html
        // .door-swatch chips (chips above) for ".door-swatch" so the
        // swatch loop body executes at boot.
        el.querySelectorAll = (s) =>
          s === ".door-swatch" ? chips.door.slice()
            : s === ".boss-swatch" ? chips.boss.slice() : [];
        // Boss swatch (boss-entity spec §6): renderLegendBossSwatch looks
        // the chip up with querySelector (single element).
        el.querySelector = (s) =>
          s === ".boss-swatch" ? (chips.boss[0] || null) : null;
      }
      return el;
    },
    querySelectorAll(sel) {
      if (sel === ".door-swatch") return chips.door.slice();
      if (sel === ".boss-swatch") return chips.boss.slice();
      return [];
    },
    createElement() { return makeEl(); },
    addEventListener(type, fn) {
      (__DOC_LISTENERS[type] = __DOC_LISTENERS[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      const l = __DOC_LISTENERS[type];
      if (l) { const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
    },
    // Test helper: dispatch a keydown (or other) event to the registered
    // document listeners (the real app keydown handler).
    dispatch(type, ev) {
      for (const fn of __DOC_LISTENERS[type] || []) fn(ev);
    },
    // document.body carries a REAL classList (state tracked) so the role
    // flags onWelcome toggles (is-gm / is-player) are assertable — the CSS
    // GM-only gating keys on body.is-gm.
    body: { classList: { _s: new Set(),
      add(...c) { for (const x of c) this._s.add(x); },
      remove(...c) { for (const x of c) this._s.delete(x); },
      toggle(c, force) {
        const on = force === undefined ? !this._s.has(c) : !!force;
        if (on) this._s.add(c); else this._s.delete(c);
        return on;
      },
      contains(c) { return this._s.has(c); } } },
    title: "",
  };
  const window = {
    matchMedia() { return { matches: false }; },
    addEventListener(type, fn) {
      (__WIN_LISTENERS[type] = __WIN_LISTENERS[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      const l = __WIN_LISTENERS[type];
      if (l) { const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
    },
    // Test helper: dispatch a window event (e.g. resize) to the registered
    // window listeners (the real app's debounced resize handler).
    dispatch(type, ev) {
      for (const fn of __WIN_LISTENERS[type] || []) fn(ev);
    },
    devicePixelRatio: 1,
  };
  // requestAnimationFrame (pan-zoom §E8): schedules render callbacks; the
  // tests flush with __RFR.dispatch() and count renderCalls to assert at most
  // one render per frame (no unbounded render queue under key-repeat).
  const requestAnimationFrame = (fn) => { __RFR.queue.push(fn); return 1; };
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
  const __FETCH = { sent: [], response: null, responses: null, hardReject: false,
    reset() {
      this.sent.length = 0; this.response = null; this.responses = null;
      this.hardReject = false; },
    // json() helper: if a `responses` queue is set it pops the NEXT queued
    // response (load→refresh sequences); else falls back to `response`.
    next() { if (this.responses) return this.responses.shift(); return this.response; } };
  const fetch = (url, opts) => {
    __FETCH.sent.push({ url, opts });
    if (__FETCH.hardReject) {
      return Promise.reject(new Error("no network in harness"));
    }
    return Promise.resolve(__FETCH.next());
  };
  const FileReader = class { readAsDataURL() {} };
  // The controllable timer as the GLOBAL setTimeout/clearTimeout: ES modules
  // resolve bare identifiers against the global scope, so the app's timer
  // calls (toast capping, GM first-run hint, reconnect backoff, resize
  // debounce) must land on this virtual clock.
  const setTimeout = (fn, ms) => timer.schedule(ms, fn);
  const clearTimeout = (id) => timer.clear(id);

  globalThis.document = document;
  globalThis.window = window;
  globalThis.WebSocket = WebSocket;
  globalThis.fetch = fetch;
  globalThis.FileReader = FileReader;
  globalThis.requestAnimationFrame = requestAnimationFrame;
  globalThis.location = location;
  globalThis.setTimeout = setTimeout;
  globalThis.clearTimeout = clearTimeout;

  // Import the REAL module graph. main.js's top-level boot (guarded by
  // window/document presence, which the stubs satisfy) registers every
  // listener and runs the five boot calls — the same load-time behavior the
  // classic script had. The five library namespaces are then read back from
  // the module cache (already loaded by main.js's import chain).
  const dir = path.dirname(MAIN_JS_PATH);
  const fileUrl = (name) => "file://" + path.join(dir, name);
  return import(fileUrl("main.js")).then(() =>
    Promise.all([
      import(fileUrl("state.js")),
      import(fileUrl("render.js")),
      import(fileUrl("game.js")),
      import(fileUrl("net.js")),
      import(fileUrl("ui.js")),
    ])
  ).then(([stateNs, renderNs, gameNs, netNs, uiNs]) => {
    const api = {};
    for (const ns of [stateNs, renderNs, gameNs, netNs, uiNs]) {
      for (const name of Object.getOwnPropertyNames(ns)) {
        if (name === "default" || name === "__proto__") continue;
        if (name in api) {
          throw new Error("duplicate export name across modules: " + name);
        }
        // LIVE getter: a named export is a live binding, so e.g. `ws` and
        // `hoverCell` keep tracking setWs/setHoverCell after boot.
        Object.defineProperty(api, name, {
          get: () => ns[name],
          enumerable: true,
          configurable: true,
        });
      }
    }
    api.document = document;
    api._timer = timer;
    api._send = __SEND;
    api._fetch = __FETCH;
    api._rfr = __RFR;
    api._window = window;
    api._ws = WebSocket;
    return api;
  });
}

module.exports = { buildApi };
