/* LittleDungeons frontend — net.js
   WebSocket lifecycle: url, connect, reconnect, connection status.
   Split from app/static/app.js (bodies verbatim). */


import { els, setWs, state, ws, wsSend } from "./state.js";
import { onServerMessage } from "./game.js";

let wsSession = "default";
let reconnectTimer = null;
let reconnectDelay = 1000;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws?session=${encodeURIComponent(wsSession)}`;
}

function connectWs() {
  setConn("connecting", "Connecting…");
  // BUG-008: a new connection supersedes any pending reconnect (never two
  // live sockets, never a stray reconnect timer left armed).
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  setWs(new WebSocket(wsUrl()));
  ws.onopen = () => {
    reconnectDelay = 1000;
    setConn("connected", "Connected");
    // Re-join after a reconnect (the server re-attaches us by name+role).
    if (state.joined && state.you) {
      wsSend({ type: "join", name: state.you.name, role: state.you.role });
    }
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    onServerMessage(msg);
  };
  ws.onclose = () => {
    // Only an *unexpected* drop reconnects. The real BUG-008 fix lives in two
    // places, not in a close flag: (1) openUploadedMap() no longer closes /
    // reconnects the socket — it sends use_map on the SAME socket; and
    // (2) connectWs() clears any pending reconnect timer before opening a new
    // socket. Together they guarantee a deliberate close can never arm a
    // stray, leaked second socket. (The old intentionalClose flag was never
    // assigned true — dead code — and has been removed.)
    if (state.joined) scheduleReconnect();
  };
  ws.onerror = () => {
    // A transport error will fire onclose, which reconnects (an unexpected
    // drop). Closing here does not — and must not — suppress that reconnect.
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  setConn("offline", "Offline");
  const wait = reconnectDelay;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, 10000);
    connectWs();
  }, wait);
}

/* ───────────────────────────── Server → client ───────────────────────────── */

function setConn(mode, label) {
  els.connStatus.classList.remove("is-connected", "is-connecting", "is-offline");
  els.connStatus.classList.add(`is-${mode}`);
  els.connLabel.textContent = label;
}

export { wsUrl, connectWs, scheduleReconnect, setConn };
