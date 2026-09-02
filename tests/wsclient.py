"""Minimal stdlib WebSocket client for tests (used by ``tests/test_ws.py``).

Wraps ``app.ws``'s client-side primitives (handshake, masked client→server
frames, frame reader) in a small, robust context-manager class:

    from tests.wsclient import WSClient
    with WSClient("127.0.0.1", port, path="/ws") as c:
        msg = json.loads(c.recv_text())   # auto-wait for the welcome
        c.send_text(json.dumps({"type": "ping-like"}))
        reply = json.loads(c.recv_text())

The client masks all outgoing frames (RFC 6455 requires it), verifies the
server's ``Sec-WebSocket-Accept``, and raises ``WSClientError`` on
handshake/protocol problems.
"""

from __future__ import annotations

import json
from typing import Any

from app.ws import (
    WSClose,
    client_handshake,
    client_recv_text,
    client_send_text,
    read_frame,
    send_frame,
)


class WSClientError(Exception):
    """Handshake or protocol error from the test client."""


class WSClient:
    """A tiny blocking WebSocket client (one socket, one connection)."""

    def __init__(
        self,
        host: str,
        port: int,
        path: str = "/ws",
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.sock = None
        self._pre = bytearray()

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> "WSClient":
        if self.sock is not None:
            return self  # idempotent: safe with `with self.client()`
        try:
            self.sock, leftover = client_handshake(
                self.host, self.port, self.path, timeout=self.timeout
            )
            # Payload that arrived in the same TCP segment as the 101 must
            # be fed back into the first frame read.
            self._pre = bytearray(leftover)
        except (OSError, ConnectionError) as exc:
            raise WSClientError(f"handshake failed: {exc}") from exc
        return self

    def close(self) -> None:
        """Send a close frame and drop the socket (best effort)."""
        sock, self.sock = self.sock, None
        if sock is None:
            return
        try:
            send_frame(sock, 0x8, b"")
            # Give the server a moment to echo the close; swallow everything.
            try:
                sock.settimeout(1.0)
                read_frame(sock)
            except Exception:
                pass
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self) -> "WSClient":
        return self.connect()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- data ------------------------------------------------------------------

    def send_text(self, text: str) -> None:
        self._require_sock()
        try:
            client_send_text(self.sock, text)
        except OSError as exc:
            raise WSClientError(f"send failed: {exc}") from exc

    def send_json(self, obj: Any) -> None:
        self.send_text(json.dumps(obj, separators=(",", ":")))

    def recv_text(self) -> str:
        """Read the next data frame (pings/pongs are handled transparently
        by the frame reader; a close frame raises ``WSClientError``)."""
        self._require_sock()
        try:
            text = client_recv_text(self.sock, self._pre)
        except WSClose as exc:
            raise WSClientError(f"peer closed: {exc}") from exc
        except (OSError, ValueError) as exc:
            raise WSClientError(f"recv failed: {exc}") from exc
        self.sock.settimeout(self.timeout)
        return text

    def recv_json(self) -> dict[str, Any]:
        raw = self.recv_text()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WSClientError(f"non-JSON text frame: {raw!r}") from exc
        if not isinstance(obj, dict):
            raise WSClientError(f"expected a JSON object, got {type(obj).__name__}")
        return obj

    def recv_json_or_none(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Wait up to ``timeout`` seconds for the next JSON frame; return
        ``None`` if nothing arrives (used to assert a message was NOT sent,
        e.g. a refused join)."""
        self._require_sock()
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            text = client_recv_text(self.sock, self._pre)
        except OSError:
            return None  # socket.timeout (a subclass of OSError) = no frame
        except WSClose:
            raise WSClientError("peer closed while waiting")
        finally:
            self.sock.settimeout(old if old is not None else self.timeout)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def frames_until(self, pred, limit: int = 50) -> list[dict[str, Any]]:
        """Receive frames (skipping none) until one satisfies ``pred``.

        Returns ALL frames received including the matching one. Raises
        ``WSClientError`` after ``limit`` frames without a match."""
        got: list[dict[str, Any]] = []
        for _ in range(limit):
            m = self.recv_json()
            got.append(m)
            if pred(m):
                return got
        raise WSClientError(f"no frame matched within {limit} frames")

    def join(self, name: str, role: str | None = None) -> dict[str, Any]:
        """Send {type:"join"} and return the welcome (or the error)."""
        msg: dict[str, Any] = {"type": "join", "name": name}
        if role is not None:
            msg["role"] = role
        self.send_json(msg)
        return self.recv_json()

    def _require_sock(self) -> None:
        if self.sock is None:
            raise WSClientError("not connected")
