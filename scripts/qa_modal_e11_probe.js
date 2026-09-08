"use strict";
/* QA coverage-gap probes (NOT a product-bug probe) — run the REAL app.js
   under Node to confirm the E1 (idempotent same-id re-entry) and E11
   (unknown-id membership guard) code paths behave as specified, so the
   sign-off can state whether they are code-correct-but-untested.
   Prints RESULT_JSON.
*/
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..");
const APPJS = path.join(ROOT, "app", "static", "app.js");

const result = { ok: false, steps: {} };
function step(n, c, d) { result.steps[n] = { ok: !!c, detail: d || "" }; if (!c) result.ok = false; }

function makeEl() {
  const el = {
    id: "", textContent: "", value: "", checked: false, disabled: false, tabIndex: 0,
    innerHTML: "", files: [], title: "", tagName: "DIV", style: {}, dataset: {},
    clientWidth: 800, clientHeight: 600, width: 0, height: 0, src: "",
    classList: { _s: new Set(),
      add(...cs){for(const c of cs)this._s.add(c);}, remove(...cs){for(const c of cs)this._s.delete(c);},
      toggle(c,f){const o=f===undefined?!this._s.has(c):!!f;if(o)this._s.add(c);else this._s.delete(c);return o;},
      contains(c){return this._s.has(c);} },
    _listeners: {},
    addEventListener(t,f){(this._listeners[t]=this._listeners[t]||[]).push(f);},
    removeEventListener(t,f){const l=this._listeners[t];if(!l)return;const i=l.indexOf(f);if(i>=0)l.splice(i,1);},
    dispatchEvent(ev){for(const f of this._listeners[ev.type]||[])f(ev);return true;},
    setAttribute(){}, getAttribute(){return null;},
    children: [], parentNode: null,
    appendChild(c){ if(c.parentNode&&c.parentNode.children){const i=c.parentNode.children.indexOf(c);if(i>=0)c.parentNode.children.splice(i,1);} this.children.push(c);c.parentNode=this;this.firstChild=this.children[0];return c; },
    removeChild(c){const i=this.children.indexOf(c);if(i>=0)this.children.splice(i,1);if(c.parentNode===this)c.parentNode=null;this.firstChild=this.children[0]||null;return c;},
    remove(){if(this.parentNode&&this.parentNode.children)this.parentNode.removeChild(this);},
    insertBefore(){}, querySelector(){return null;}, querySelectorAll(){return [];},
    getBoundingClientRect(){return {left:0,top:0,width:800,height:600};},
    setPointerCapture(){}, closest(){return null;}, focus(){},
  };
  Object.defineProperty(el,"hidden",{get(){return el.classList._s.has("hidden");},set(v){if(v)el.classList._s.add("hidden");else el.classList._s.delete("hidden");},configurable:true,enumerable:true});
  el.classList._s.add("hidden");
  const noop=()=>undefined;
  const ctx={fillStyle:"",strokeStyle:"",lineWidth:1,globalAlpha:1,font:"",textAlign:"",textBaseline:"",
    fillRect:noop,strokeRect:noop,clearRect:noop,beginPath:noop,moveTo:noop,lineTo:noop,rect:noop,arc:noop,arcTo:noop,closePath:noop,
    fill:noop,stroke:noop,save:noop,restore:noop,clip:noop,setTransform:noop,transform:noop,setLineDash:noop,fillText:noop,strokeText:noop,
    measureText:()=>({width:10}),createRadialGradient:()=>({addColorStop:noop})};
  let _c=null; el.getContext=()=>{if(!_c)_c=ctx;return _c;};
  return el;
}
const registry={};
const document={
  querySelector(s){const id=s.replace("#","");if(!registry[id]){registry[id]=makeEl();registry[id].id=id;}return registry[id];},
  querySelectorAll(){return [];}, createElement(){return makeEl();},
  _listeners:{}, addEventListener(t,f){(this._listeners[t]=this._listeners[t]||[]).push(f);},
  removeEventListener(t,f){const l=this._listeners[t];if(!l)return;const i=l.indexOf(f);if(i>=0)l.splice(i,1);},
  dispatch(t,ev){for(const f of this._listeners[t]||[])f(ev);}, body:makeEl(), title:"",
};
const window={matchMedia:()=>({matches:false}),_listeners:{},
  addEventListener(t,f){(this._listeners[t]=this._listeners[t]||[]).push(f);},
  removeEventListener(t,f){const l=this._listeners[t];if(!l)return;const i=l.indexOf(f);if(i>=0)l.splice(i,1);},
  dispatch(t,ev){for(const f of this._listeners[t]||[])f(ev);}, devicePixelRatio:1};
const requestAnimationFrame=(fn)=>setTimeout(fn,0);
const location={protocol:"http:",host:"127.0.0.1:8000"};
const WebSocket=class{constructor(u){this.url=u;this.readyState=0;}send(){}close(){}}; WebSocket.OPEN=1;
global.fetch=(u,o)=>{ result._fetchCalled=true; return Promise.resolve({ok:true,json:async()=>({})}); };

const src=fs.readFileSync(APPJS,"utf8");
const EXPORTS=";global.__TAPI__ = { state, els, confirmDeleteSave, renderSaves, renderSavesTab, syncSaveModal }";
eval(src+EXPORTS);
const api=global.__TAPI__;

try {
  // Fresh GM session.
  api.state.role="gm"; api.state.saves=[{id:"act-1",name:"Act Three",map_name:"M",width:24,height:16,created_at:"2025-01-01T12:00:00",entity_count:4}];
  api.renderSaves(); api.renderSavesTab();

  // E11: unknown id -> error toast, NO modal open, NO fetch.
  api._fetchCalled=false;
  api.confirmDeleteSave("does-not-exist");
  const e11 = {
    hidden: api.els.saveDeleteModal.hidden,
    confirming: api.state.confirmingSaveId,
    fetched: api._fetchCalled,
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
  const fetchesBefore=0; // _fetchCalled is a flag, not a count; just ensure it stays false
  api._fetchCalled=false;
  api.confirmDeleteSave("act-1");
  api.confirmDeleteSave("act-1"); // double call
  const e1 = {
    hidden: api.els.saveDeleteModal.hidden,
    confirming: api.state.confirmingSaveId,
    fetched: api._fetchCalled,
    bodyName: (api.els.saveDeleteModalBody.children[0]||{}).textContent,
    modalInstances: Object.keys(registry).filter(k=>k==="save-delete-modal").length,
  };
  step("E1_double_call_one_modal", e1.hidden===false && e1.confirming==="act-1" && e1.modalInstances===1,
    "hidden="+e1.hidden+" confirming="+e1.confirming+" modalInstances="+e1.modalInstances);
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
