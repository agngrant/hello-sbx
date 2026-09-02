"""Hand-rolled RFC 6455 WebSocket layer (PROJECT.md §2, §9) — pure stdlib.

Server side (used by ``app/main.py`` on an ``http.server`` handler):

* :func:`compute_accept` — the ``Sec-WebSocket-Accept`` derivation.
* :func:`ws_accept` — run the 101 handshake on a ``BaseHTTPRequestHandler``,
  then return the underlying raw :class:`socket.socket` (with
  ``Nagle`` off). The ``request_handler`` attribute on the socket gives back
  access to the HTTP handler for logging / ``close_connection`` bookkeeping.
* :func:`read_frame` — read one frame (client→server frames are masked; the
  mask is applied as an XOR).
* :func:`send_frame` / :func:`send_text` — emit unmasked server frames.
* :func:`ws_serve` — the WebSocket message loop: read JSON text frames,
  decode them, hand them to ``on_message(sock, msg) -> dict | None``; if it
  returns a dict, that is sent back to the client as the reply, otherwise
  nothing extra is sent (the handler is expected to have broadcast already).
  ``on_close(sock)`` is called on teardown. Iteration 5's
  ``GameSession.handle_message`` is the ``on_message`` callback.

Client side (used by ``tests/wsclient.py``):

* :func:`client_handshake` — open a socket, send the 101 request, verify the
  response.
* :func:`client_send_text` / :func:`client_recv_text` — masked client→server
  text frames and receiving.

Opcodes handled: ``0x1`` text, ``0x8`` close, ``0x9`` ping (→ ``0xA`` pong
reply), ``0xA`` pong. Continuation frames (opcode ``0x0``) are supported on
both sides for large payloads (e.g. a full map snapshot > 64 KiB).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
from typing import Any

# ---------------------------------------------------------------------------
# Constants (RFC 6455)
# ---------------------------------------------------------------------------

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MASK_BIT = 0x80


class WSClose(Exception):
    """Raised by :func:`read_frame` when a close frame (or EOF) is seen.

    Sentinels the handler loop can catch to exit cleanly: ``code`` is the
    WebSocket close code (1005 when it was a plain EOF with no close frame).
    """

    def __init__(self, code: int = 1005, reason: str = "") -> None:
        super().__init__(reason or f"websocket closed (code {code})")
        self.code = code
        self.reason = reason


def _recv_exact(sock: socket.socket, n: int, pre: bytearray | None = None) -> bytes:
    """Receive exactly ``n`` bytes or raise ``WSClose`` on EOF.

    If ``pre`` is given, bytes already read off the socket (e.g. payload that
    arrived in the same TCP segment as the 101 handshake response) are
    consumed first; ``pre`` is updated in place so leftovers survive across
    calls.
    """
    if pre is not None:
        while len(pre) < n:
            chunk = sock.recv(n - len(pre))
            if not chunk:
                raise WSClose(1005, "connection closed by peer")
            pre += chunk
        out = bytes(pre[:n])
        del pre[:n]
        return out
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise WSClose(1005, "connection closed by peer")
        buf += chunk
    return bytes(buf)


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


def compute_accept(key: str) -> str:
    """``Sec-WebSocket-Accept`` = base64(sha1(key + magic)) (RFC 6455 §4.2.2)."""
    return base64.b64encode(
        hashlib.sha1((key.strip() + WS_MAGIC).encode("ascii")).digest()
    ).decode("ascii")


def ws_accept(handler: Any) -> socket.socket:
    """Perform the 101 handshake using an ``http.server`` BaseHTTPRequestHandler.

    ``handler`` must be positioned at a ``GET /ws`` request (``do_GET`` has
    run, headers parsed). Reads ``Sec-WebSocket-Key``, sends the 101 response
    directly onto the socket, and returns the raw :class:`socket.socket` with
    ``Nagle`` disabled. The handler is attached as ``sock.request_handler`` so
    the caller can bookkeep (``close_connection``, logging) afterwards.

    Raises ``ValueError`` for a malformed/missing upgrade request; the caller
    should respond with 400 and keep the connection closed.
    """
    key = handler.headers.get("Sec-WebSocket-Key")
    if not key:
        raise ValueError("missing Sec-WebSocket-Key header")
    # Upgrade check is case-insensitive per RFC 6455 §4.1.
    upgrade = (handler.headers.get("Upgrade") or "").lower()
    if "websocket" not in upgrade:
        raise ValueError("missing Upgrade: websocket header")

    accept = compute_accept(key)
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    sock = handler.connection
    sock.sendall(response.encode("ascii"))
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass  # non-TCP transport; harmless
    # Plain socket objects reject arbitrary attribute assignment, so attach
    # the handler via the instance dict (same trick ssl.SSLSocket uses).
    # ``getattr(sock, "request_handler", None)`` still resolves it later.
    try:
        vars(sock)["request_handler"] = handler
    except TypeError:  # transport without an instance dict — degrade gracefully
        pass
    return sock


def client_handshake(
    host: str,
    port: int,
    path: str = "/ws",
    timeout: float = 10.0,
) -> tuple[socket.socket, bytes]:
    """WebSocket client handshake (used by ``tests/wsclient.py``).

    Returns ``(sock, leftover)``: the socket plus any bytes already read from
    it (a server may send the first WS frame in the same TCP segment as the
    101 response — ``leftover`` must be fed back into :func:`read_frame` via
    its ``pre`` argument). The ``Sec-WebSocket-Accept`` value is verified
    against our own key so a broken server handshake fails loudly instead of
    silently.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))

    # Read the HTTP response head (status + headers) up to the blank line.
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise ConnectionError("server closed the connection during handshake")
        buf += chunk
        if len(buf) > 65536:
            sock.close()
            raise ConnectionError("malformed handshake response (too long)")
    head, _, leftover = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    if " 101" not in status_line:
        sock.close()
        raise ConnectionError(f"handshake rejected: {status_line!r}")
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            name, _, value = line.partition(b":")
            headers[name.decode("latin-1").strip().lower()] = value.strip().decode("latin-1")
    if headers.get("sec-websocket-accept") != compute_accept(key):
        sock.close()
        raise ConnectionError("bad Sec-WebSocket-Accept from server")
    sock.settimeout(timeout)
    return sock, leftover


# ---------------------------------------------------------------------------
# Frame I/O
# ---------------------------------------------------------------------------


def read_frame(sock: socket.socket, pre: bytearray | None = None) -> tuple[int, bytes]:
    """Read one (complete) frame from ``sock``.

    Handles 7-bit / 16-bit / 64-bit payload lengths and the mandatory
    client→server mask (XOR unmask). Continuation frames are reassembled into
    a single logical message.

    ``pre`` (optional): a ``bytearray`` of bytes already read off the socket
    (e.g. handshake leftovers) — consumed first, updated in place. Pass the
    same object on every call for a connection that had leftovers.

    Returns ``(opcode, payload)`` where opcode is one of ``OP_TEXT`` /
    ``OP_BINARY`` / ``OP_PING`` / ``OP_PONG`` / ``OP_CLOSE``. Raises
    :class:`WSClose` on a close frame or an EOF.
    """
    msg_opcode: int | None = None
    fragments: list[bytes] = []

    while True:
        hdr = _recv_exact(sock, 2, pre)
        b0, b1 = hdr[0], hdr[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & MASK_BIT)
        length = b1 & 0x7F

        if opcode in (OP_CONTINUATION, OP_TEXT, OP_BINARY):
            if opcode != OP_CONTINUATION:
                if msg_opcode is not None:
                    raise WSClose(1002, "unexpected data frame in fragmented message")
                msg_opcode = opcode
                fragments = []
        elif opcode in (OP_CLOSE, OP_PING, OP_PONG):
            if msg_opcode is not None:
                raise WSClose(1002, "control frame in fragmented message")
        else:
            raise WSClose(1002, f"unsupported opcode {opcode:#x}")

        if length == 126:
            (length,) = struct.unpack(">H", _recv_exact(sock, 2, pre))
        elif length == 127:
            (length,) = struct.unpack(">Q", _recv_exact(sock, 8, pre))

        mask = _recv_exact(sock, 4, pre) if masked else None
        payload = _recv_exact(sock, length, pre) if length else b""
        if masked and payload:
            mask4 = struct.pack("<I", int.from_bytes(mask, "little"))
            # XOR-unmask in 4-byte aligned chunks (fast on 3.11+; correct on all).
            payload = bytes(b ^ mask4[i & 3] for i, b in enumerate(payload))

        if opcode == OP_CLOSE:
            code = 1005
            if len(payload) >= 2:
                code = int.from_bytes(payload[:2], "big")
            reason = payload[2:].decode("utf-8", "replace") if len(payload) > 2 else ""
            try:  # echo the close so the peer can terminate cleanly
                send_frame(sock, OP_CLOSE, payload[:2])
            except OSError:
                pass
            raise WSClose(code, reason) from None
        if opcode == OP_PING:
            send_frame(sock, OP_PONG, payload)
            continue
        if opcode == OP_PONG:
            continue

        fragments.append(payload)
        if fin:
            return (msg_opcode or OP_TEXT), b"".join(fragments)
        # not fin: wait for continuation frames


def send_frame(
    sock: socket.socket,
    opcode: int,
    payload: bytes = b"",
    mask: bytes | None = None,
) -> None:
    """Send one frame.

    Server→client frames are unmasked (``mask=None``); pass a 4-byte ``mask``
    to emit a masked client→server frame (tests / :mod:`tests.wsclient`).
    Control frames (close/ping/pong) always carry payloads ≤ 125 bytes and
    are sent as single, non-fragmented frames.
    """
    if not payload:
        length_code: int = 0
        length_bytes = b""
    elif len(payload) < 126:
        length_code = len(payload)
        length_bytes = b""
    elif len(payload) < (1 << 16):
        length_code = 126
        length_bytes = struct.pack(">H", len(payload))
    else:
        length_code = 127
        length_bytes = struct.pack(">Q", len(payload))

    b0 = 0x80 | (opcode & 0x0F)  # FIN set; data & control frames are non-fragmented
    if mask is None:
        header = bytes([b0, length_code]) + length_bytes
        sock.sendall(header + payload)
    else:
        if len(mask) != 4:
            raise ValueError("mask must be 4 bytes")
        header = bytes([b0, length_code | MASK_BIT]) + length_bytes + mask
        mask4 = struct.pack("<I", int.from_bytes(mask, "little"))
        masked = bytes(b ^ mask4[i & 3] for i, b in enumerate(payload))
        sock.sendall(header + masked)


def send_text(sock: socket.socket, text: str, mask: bytes | None = None) -> None:
    """Send a UTF-8 text frame (unmasked on the server, masked for clients)."""
    send_frame(sock, OP_TEXT, text.encode("utf-8"), mask=mask)


def send_json(sock: socket.socket, obj: Any, mask: bytes | None = None) -> None:
    """Send a JSON text frame."""
    send_text(sock, json.dumps(obj, separators=(",", ":")), mask=mask)


def client_send_text(sock: socket.socket, text: str) -> None:
    """Send a masked client→server text frame (tests)."""
    send_text(sock, text, mask=os.urandom(4))


def client_recv_text(sock: socket.socket, pre: bytearray | None = None) -> str:
    """Read the next complete message; reply to pings; raise on close."""
    opcode, payload = read_frame(sock, pre)
    if opcode not in (OP_TEXT, OP_BINARY):
        raise WSClose(1002, f"expected data frame, got opcode {opcode:#x}")
    return payload.decode("utf-8")


# ---------------------------------------------------------------------------
# Iteration 5: the session message loop
# ---------------------------------------------------------------------------


def ws_serve(
    sock: socket.socket,
    on_message: Any | None = None,
    on_close: Any | None = None,
    lock_for: Any | None = None,
) -> None:
    """Run the WebSocket message loop for one connection.

    Iteration 5 behaviour: no initial frame is sent — the client drives the
    protocol by sending its first message (``{type:"join",...}``), whose
    reply (the welcome) comes back through the same reply path. Each decoded
    JSON text message is handed to ``on_message(sock, msg) -> dict | None``;
    if it returns a dict, that is sent back to the client (the session uses
    this for per-client replies such as errors and the sender's ``path``).

    BUG-005: those per-client replies used to be written with a bare
    ``send_json(sock, reply)``, bypassing the per-connection send lock that
    ``GameSession._broadcast`` uses — so a reply and a concurrent broadcast
    to the *same* socket could interleave and corrupt a frame. Callers can
    pass ``lock_for`` (a zero-arg callable returning the connection's
    per-socket ``threading.Lock``, or ``None`` before the client has a
    session connection); when provided, EVERY outbound write from this loop
    (the reply AND the invalid-JSON error) is issued under it, so all writes
    to a given socket are serialised by the very lock the session's
    broadcast uses.

    On close / error: send a close frame, call ``on_close(sock)`` (so the
    session can detach the connection), mark the HTTP handler's
    ``close_connection = True``, and clean up the socket.
    """
    def _out(obj: Any) -> None:
        # Serialise every outbound frame by this connection's send lock
        # (BUG-005) so it can never interleave with a broadcast. A pre-join
        # socket has no session lock yet — write directly.
        lock = lock_for() if lock_for is not None else None
        if lock is not None:
            with lock:
                send_json(sock, obj)
        else:
            send_json(sock, obj)

    handler = getattr(sock, "request_handler", None)
    try:
        while True:
            opcode, payload = read_frame(sock)
            if opcode == OP_CLOSE:  # defensive; read_frame normally raises
                break
            if opcode != OP_TEXT:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _out({"type": "error", "message": "invalid JSON"})
                continue
            reply = on_message(sock, msg) if on_message is not None else None
            if reply is not None:
                _out(reply)
    except WSClose:
        pass  # peer closed (or EOF) — normal teardown
    except (OSError, ValueError):
        pass  # malformed frame / transport error — drop the connection
    finally:
        try:
            send_frame(sock, OP_CLOSE, struct.pack(">H", 1000))
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        if on_close is not None:
            try:
                on_close(sock)
            except Exception:
                pass
        if handler is not None:
            handler.close_connection = True
