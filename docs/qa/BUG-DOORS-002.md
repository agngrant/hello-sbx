# BUG-DOORS-002 — `_on_door` returns the occupancy error for a player `lock` on an open door that has a token, where the spec §4.3 pinned order returns `"not allowed"` (role check before occupancy)

**Status:** **CONFIRMED / OPEN** (verified by independent read-only code
review + an in-process repro against the real `GameSession` — see
"Reproduction"). **No fix was applied in this QA pass** (no code changes
outside docs).

**Severity:** P3 (low — the message is returned on an **error** path, the
state machine is not corrupted, the door is not mutated, and a player can
**never** legally `lock` a door anyway (`lock` is GM-only), so no player can
ever *succeed* with the action in question; the only observable difference is
which of two correct-rejection error strings a GM/player sees on one specific
illegal `(state, action, role, occupancy)` combination — which is why it is P3)
**Component:** Backend — `app/session.py` `GameSession._on_door()`
**Spec reference:** `docs/design/door-features.md` §4.3 (the deterministic
validation order, "first failure wins", AC3), §4.1/§4.2 (the permission
matrix — `lock` is GM-only), §15 AC3 (exact error strings in the §4.3 order)

## Symptom
The spec §4.3 pins `_on_door`'s validation as a **first-failure-wins** sequence
with an explicit order: (4) action valid, (5) transition legality, **(6) role
(GM-only for `unlock`/`lock`) → else `"not allowed"`**, **(7) occupancy
(close only) → else `"cannot close a door with a token on it"`**. The spec is
unambiguous that the **role check (#6) runs before the occupancy check (#7)**,
so the error for a **player** performing a GM-only `lock` is `"not allowed"`
*regardless of occupancy*.

The shipped `_on_door` **folds the lock-from-open occupancy check into the
transition block (step 5)**, *before* the role check (step 6). Consequence: a
**player** who sends `lock` on an **open** door that **has an entity on it**
gets `"cannot close a door with a token on it"` instead of the spec-pinned
`"not allowed"`. For every other combination the shipped output matches the
spec's §4.3 table exactly.

This is a genuine (if narrow) deviation from the **exact** error-string
contract that AC3 pins ("every illegal combination returns the **exact** error
string in the §4.3 deterministic order").

## Root cause
`app/session.py` `_on_door()`: the occupancy guard for the **lock-while-open**
(force-closing) transition is evaluated at **line ~845–848**, i.e. *before* the
role gate at **line ~852**:

```python
        if action == "lock" and cur == "O" and self._any_entity_at(x, y):
            # lock-while-open force-closes → same occupancy guard as close.
            return {"type": "error",
                    "message": "cannot close a door with a token on it"}
        # Role: unlock/lock are GM-only (open/close already gated by the
        # locked/unlocked state above, so a locked door reports
        # "door is locked" even for a player).
        if action in ("unlock", "lock") and not is_gm:
            return {"type": "error", "message": NOT_ALLOWED}
```

The code comment on the `close` occupancy guard (a few lines above, ~840) and
the A5 spec text both justify *folding* the occupancy check into the
transition for the GM — and the GM path is correct (a GM `lock`-ing an open
door with a token must get the occupancy error, which the spec also pins, see
§4.3 table row "open (O) / lock → GM → locked (force-closed)" + A5/AC9). But
folding it *ahead of the role check* changes the **player**-role outcome for
the one illegal combination where a player's `lock` collides with occupancy:
the spec order says the role failure (`"not allowed"`) should win first, while
the code returns the occupancy failure.

For the **`close`** action the shipped order *does* match the spec (the `close`
occupancy check at ~840 runs after the role gate would run — `close` is not
GM-only, so it reaches role only as a pass-through and then occupancy; the
net strings agree). The mismatch is isolated to **`lock`-from-`open` with a
non-GM role**.

## File:line (current code)
- `app/session.py:~839-840` — `close` occupancy guard (correctly after the
  transition state checks; `close` is not GM-only, so the role gate is a
  no-op for it — net behavior matches the spec).
- `app/session.py:~845-848` — **`lock`-from-`O` occupancy guard — runs BEFORE
  the role gate (the deviation).**
- `app/session.py:~852` — `if action in ("unlock","lock") and not is_gm:` →
  `"not allowed"` (the role gate that the spec orders *before* occupancy).

## Reproduction (in-process, real `GameSession`)
```python
from app.models import Grid
from app.session import GameSession
from tests.test_door_session import FakeConn, drive, attach

g = Grid(width=5, height=3, cells=[
    ["wall","wall","wall","wall","wall"],
    ["wall","floor","doorway","floor","wall"],
    ["wall","wall","wall","wall","wall"],
])
s = GameSession("t", g)
gm_s, p1_s = FakeConn(), FakeConn()
gm,_ = s.join(gm_s,"G","gm"); p1,_ = s.join(p1_s,"Alice","player")
attach(s,gm_s); attach(s,p1_s)
drive(s,gm_s,{"type":"door","x":2,"y":1,"action":"unlock"})
drive(s,gm_s,{"type":"door","x":2,"y":1,"action":"open"})
drive(s,gm_s,{"type":"place","entity_id":p1.entity_id,"x":2,"y":1})  # token on the open door
res = drive(s,p1_s,{"type":"door","x":2,"y":1,"action":"lock"})      # PLAYER lock
print(res)
# ACTUAL:  {'type':'error','message':'cannot close a door with a token on it'}
# SPEC §4.3 (role #6 before occupancy #7):
#          {'type':'error','message':'not allowed'}
```

Control (same setup, `lock` on the open door **without** a token) correctly
returns `{'type':'error','message':'not allowed'}` — confirming the deviation
is specific to the occupancy-collision case. The GM path is unaffected (a GM
`lock`-ing an open+token door returns the occupancy error, which the spec also
pins).

## Expected vs actual
- **Expected (spec §4.3 order, first-failure-wins):** a **player** `lock` on an
  **open** door **with a token** → role fails first → `"not allowed"`.
- **Actual (shipped):** `"cannot close a door with a token on it"`.
- All 11 other `(state, action, role, occupancy)` error strings in the spec's
  §4.3 table are returned exactly as pinned (verified by `tests/test_door_session.py`
  `TestDoorStateMachine` + `TestDoorOccupancy`, all green).

## Why it is not a correctness defect
- The action is **rejected** either way; the door state is unchanged; no
  partial mutation. Both returned messages are "correct rejections."
- A player can **never** `lock` (GM-only), so no player can complete the
  action; the only observable difference is the message text on one illegal
  combination.
- The GM (the only role that can `lock`) gets the spec-pinned occupancy error
  in the force-closing case (verified: `test_lock_while_open_with_token_rejected`).

## Suggested fix (not applied — no code changes this pass)
Move the **`lock`-from-`open`** occupancy check to run **after** the role gate
(consistent with the spec's role-then-occupancy ordering for GM-only actions),
i.e. evaluate `action == "lock" and cur == "O"` occupancy only for a GM (or
after the `not is_gm` check). A player would then get `"not allowed"` (role
fails first) and a GM would still get the occupancy error — matching §4.3
exactly. An alternative (also spec-valid) is to keep the code as-is and amend
spec §4.3 to state that for `lock`-from-`open` the occupancy guard is
evaluated *within* the transition step for all roles; but the current §4.3
table and A5 text read as role-before-occupancy, so the code is the side that
diverges. Add a regression test for the player-role case:
`tests/test_door_session.py::TestDoorStateMachine` — *a player `lock` on an
open+token door returns `"not allowed"` (role before occupancy), not the
occupancy string.*

## Suggested new test (gap)
- `tests/test_door_session.py` — a new case under `TestDoorStateMachine`
  (or `TestDoorOccupancy`) asserting that a **player** `lock` on an **open**
  door **with a token** returns exactly `{"type":"error",
  "message":"not allowed"}` per the spec §4.3 order. This combination is
  currently **not** pinned by any test (the existing occupancy tests use the
  GM role only), which is why the deviation shipped uncaught.

**Status: OPEN (recommend reorder + regression test; non-blocking — P3).**
