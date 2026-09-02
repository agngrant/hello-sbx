"""RFC 6455 WebSocket *client* helpers — TEST-ONLY.

The server side of the old hand-rolled RFC 6455 layer (``ws_accept``,
``ws_serve``, the server frame codec) is gone: the FastAPI/uvicorn server
now serves RFC 6455 natively via the ``websockets`` library
(``@app.websocket("/ws")`` in ``app/server.py``).

What remains is the blocking raw-socket CLIENT used by ``tests/wsclient.py``
(and the RFC 6455 §1.3 accept vectors pinned by ``tests/test_ws.py``):

* :func:`compute_accept` — the ``Sec-WebSocket-Accept`` derivation.
* :func:`client_handshake` — open a socket, send the 101 request, verify
  the response (including a live re-derivation of the accept value).
* :func:`client_send_text` / :func:`client_recv_text` — masked
  client→server text frames and receiving.
* :func:`send_frame` / :func:`read_frame` — one (complete) frame, with
  continuation reassembly and ping/pong handling.

Do not import this module from application code — it exists only so the
test suite can drive the real server over plain TCP with its own codec.
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

    Client→server frames are masked (pass a 4-byte ``mask``); ``mask=None``
    emits an unmasked server frame (tests only — the application itself has
    no raw-socket server code anymore). Control frames (close/ping/pong)
    always carry payloads ≤ 125 bytes and are sent as single, non-fragmented
    frames.
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
