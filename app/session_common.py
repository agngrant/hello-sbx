"""
Shared constants and helpers for the split ``app.session`` mixins.

Leaf module: it imports nothing from ``app.models`` or ``app.session`` — the five
mixin modules (conn / state / doors / actions / map) all import their shared wire
constants and the two small pure helpers from here, which is what breaks the
would-be circular import between the mixins and ``app.session``.

Moved verbatim from ``app/session.py`` (god-module split).
"""

from __future__ import annotations

import asyncio
from typing import Any


#: §6/§8: exactly one GM and up to six players per session.
MAX_PLAYERS = 6

#: The error message a joiner gets when the session is full (PROJECT.md §8:
#: "further joins get ``{type:"error", message:"session full"}``").
SESSION_FULL = "session full"

#: §6 rejection for an unreachable destination without override.
NO_ROUTE = "no route — wall in the way"

#: §9: sent for anything the server does not understand.
UNKNOWN_TYPE = "unknown message type"

#: §9: sent when a non-GM tries a GM-only operation, or a non-owner tries to
#: move somebody else's entity.
NOT_ALLOWED = "not allowed"

#: Entity kinds a GM may create (player characters are spawned by ``join``;
#: the GM itself has NO token — docs/design/gm-controller.md §2.3).
CREATABLE_KINDS = ("npc", "enemy", "boss")

#: Door actions (docs/design/door-features.md §4/§18): the client→server
#: ``{type:"door", x, y, action}`` message. ``unlock``/``lock`` are GM-only;
#: ``open``/``close`` are allowed for any client while the door is unlocked.
DOOR_ACTIONS = ("unlock", "lock", "open", "close")

#: Safe-room door actions (door-iconography spec §4/§18): the client→server
#: ``{type:"safe_door", x, y, action}`` message. WHOLLY GM-only: a safe door
#: is GM-controlled end-to-end (mark/unmark/unlock/lock/open/close) — there
#: is no player path, and safe doors now carry a lock state (``L``/``U``/``O``,
#: same model as normal doors — door-iconography §3, A1).
SAFE_DOOR_ACTIONS = ("mark", "unmark", "unlock", "lock", "open", "close")


#: Safe-room spec §5.2 (D4): the safety-rule rejection — a hostile is never
#: moved/placed/created/team-changed onto a safe-room door cell, even under
#: GM override.
HOSTILE_ON_SAFE_DOOR = "cannot place a hostile on a safe room door"


def _as_int(value: Any) -> int | None:
    """Coerce a JSON int; reject bools and non-integers (→ ``None``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _schedule(frame_coro: Any) -> None:
    """Schedule an outbound frame from a SYNCHRONOUS context.

    Called from ``handle_message`` (which stays synchronous so the in-process
    unit tests can drive it directly) while running on the uvicorn event-loop
    thread: hand the coroutine off to that loop as a task. When no event loop
    is running (the ``tests/test_session.py`` FakeSock context, where the
    session has no async senders registered) the coroutine is simply dropped
    — the senders list is empty there, so there is nothing to send.
    """
    try:
        asyncio.get_running_loop().create_task(frame_coro)
    except RuntimeError:
        coro = frame_coro
        if asyncio.iscoroutine(coro):
            coro.close()
