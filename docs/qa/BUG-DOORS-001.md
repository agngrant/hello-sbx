# BUG-DOORS-001 — Cross-session door-state leak: every session on an unregistered id shares the same mutable sample-dungeon `Grid`, so a door opened in one session is seen open in another

**Status:** **CONFIRMED / OPEN** (verified by independent read-only QA review + a
live two-session repro — see "Reproduction"). **No fix was applied in this QA
pass** (per the task's constraint of no code changes outside docs). A recommended
disposition is given in "Verdict / status".

**Severity:** P2 (moderate — a user-visible cross-session **state-isolation** defect with **no** crash, data corruption, or per-player data loss; the shared sample grid is a documented, pre-existing design choice and the default single-browser UI does not trigger it, which keeps it P2 rather than P1, but a GM running two sample-map game sessions would see a door opened in one leak into the other — a real gameplay-correctness surprise, which is why it is not P3)
**Component:** Backend — `app/main.py` `get_session()` (shared-grid resolution)
interacting with the door feature's `Grid.doors` (map state on the shared
`Grid`)
**Spec reference:** `docs/design/door-features.md` §3.1 (D1 — door state is
**map state**, shared by every viewer, lives in `maps_registry`, "the session
grid is the *same object* as the registry entry, so REST and WS see the same
doors"), §8.2, §12 ("`use_map` swaps the whole `Grid` object")

## Symptom
Two (or more) live game sessions that are **not** backed by a registered map
id (i.e. every session id other than `sample-dungeon` and any uploaded/
generated map id — this includes the product's default browser session id
`"default"`) all play on the **same shared mutable sample-dungeon `Grid`
object**. Because door state now lives *on* that `Grid` (`Grid.doors`), a door
that is **opened in one session** is observed **open** by a brand-new,
unrelated session on the same shared grid. Door state (and any GM cell-paint)
leaks across otherwise-independent game sessions.

Reproduced live against a running server (see "Reproduction"): a door the GM
opened in session A re-appeared as `O` (open) in the **welcome** frame of a
freshly-joined, never-before-seen session B.

## Root cause
`app/main.py:103` — `get_session()` resolves a session's grid by
**reference**, not by copy:

```python
entry = maps_registry.get(session_id) or maps_registry[SAMPLE_MAP_ID]
session = GameSession(session_id, entry["grid"])   # same Grid object
```

and `app/session.py:106` stores it by reference: `self.grid = grid`. The docstring
of `get_session` documents this as *intentional* ("the grid object is SHARED
with the registry entry, so REST paints and WS paints hit the same grid"), and
the e2e suite **asserts** it (`scripts/e2e_proof.py`: "session grid is the
registry grid (shared identity)"). So sharing the *geometry* of the sample grid
is a deliberate, pre-existing design decision (the sample map is a single
built-in, and the whole process is a single in-memory store).

What **changed** with the door feature is that `Grid` now carries per-cell
*mutant* state (`doors`) that is **meant to be map state** (spec D1). Sharing
the `Grid` object therefore now shares door state (and, equivalently, a GM
paint of a cell in one session is visible in another — a pre-existing,
latent issue the door feature makes concrete for doors). A door is a `doorway`
cell + a state on the shared `Grid`, so the sharing is exactly the mechanism
that leaks it.

Note this is **not** a new defect introduced by the door *logic*: the door
state machine, LOS, movement, and visibility are all correct for a single
session. The issue is purely the pre-existing shared-grid identity now
carrying observable per-cell state.

## Reproduction (live, two sessions)
1. Start the server: `./.venv/bin/python -m app.main --host 0.0.0.0 --port 8000`.
2. Join a GM to an unregistered session id (e.g. `/ws?session=qa-aaa-1`) and a
   player to the same session. (Sample dungeon is used; all doors default `L`.)
3. The GM sends `{"type":"door","x":5,"y":5,"action":"unlock"}` then
   `{"action":"open"}`. The welcome/state frames show
   `map.doors = {"10,4":"L","5,5":"O","9,7":"L"}` — door (5,5) is now `O`.
4. Join a **brand-new** GM to a **different, never-before-used** unregistered
   id (e.g. `/ws?session=qa-bbb-3`).
5. **Observed:** its `welcome` frame carries
   `map.doors = {"10,4":"L","5,5":"O","9,7":"L"}` — door (5,5) is **`O`**,
   i.e. already open, in a session that never touched a door.

   Expected (isolated sessions): the fresh session should see
   `{"10,4":"L","5,5":"L","9,7":"L"}` (all locked, the all-locked default).

The leak is symmetric and applies to any cell-paint on the shared sample grid,
and to any registered map id that is reused by multiple concurrent sessions
(the same `entry["grid"]` reference is shared across every session that
resolves to that map id).

## Expected vs actual
- **Expected (per-session isolation, the natural reading of "door state is
  map state *for a session*"):** door state opened in one session does not
  propagate to an unrelated session playing the same (sample) map; a fresh
  session starts all-locked.
- **Actual (shared-grid identity):** every session on an unregistered id
  (incl. the default `"default"`) shares one `Grid`, so door state (and cell
  paint) is global to that map across all sessions in the process.

## Impact / blast radius
- No crash, no data corruption, no per-player data loss. Door *logic* is
  correct within a session.
- In a **default product deployment** the browser client always uses
  `wsSession = "default"` (`app/static/app.js:132`), so there is effectively
  **one** sample-dungeon session in normal use — the leak is *not* triggered
  by the shipped UI's normal flow.
- It **is** reachable by any client that joins distinct unregistered session
  ids (direct `/ws?session=X`), by GMs running parallel sample-map game
  instances, or by concurrent sessions on the same registered map id.
- The QA/e2e suites already compensate for this by re-locking/reading live
  door state (`tests/test_session.py` `setUp`, `scripts/e2e_proof.py` steps [2]
  and [9]); the compensation is correct and the suites are green — this is a
  *product* limitation surfaced by the shared-grid design, not a test bug.

## Verdict / status
**CONFIRMED / OPEN.** This is a real, product-relevant **state-leak**, and the
right disposition is one of:

- **Recommended (correct fix):** a session should not silently *alias* the
  shared sample-dungeon `Grid` for **per-cell mutable state**. Options, in
  order of preference: (a) in `get_session`, when falling back to the sample
  map, copy the grid (`Grid.from_dict(sample.to_dict())` / `copy.deepcopy`)
  so each session has its **own** door/paint state while still sharing the
  built-in geometry by value; or (b) explicitly document the limitation
  ("sessions that are not registered map ids share the sample dungeon's
  cell-level state") in `PROJECT.md`/`README`/the doors spec, **and** treat the
  default-session id as the single supported sample-game surface (so the leak
  is moot in the shipped UI).
- **Interim (no code change):** document it as a known limitation. The default
  browser flow is unaffected; only direct multi-session use of unregistered
  ids is impacted.

Because the shared-grid identity is a **deliberate, pre-existing,
e2e-pinned** design decision and the default UI does not trigger the leak,
this is filed and tracked here but is **not** a release blocker for the door
feature. It should be resolved (or explicitly accepted and documented) before
any product flow that runs concurrent sample-map sessions is introduced.

**Status: OPEN (recommend fix-or-document; non-blocking for this sign-off).**
