# QA Sign-off — Openable / Closable Doors

**Feature:** Openable / Closable Doors (`doorway` cell + L/U/O state)
**Branch:** `feat/explored-map` · feature commit `a05a013` on tagged baseline
`b1ff47e` ("working sight")
**Spec:** `docs/design/door-features.md` (AC1–AC16 in §15)
**QA mode:** independent read-only review + automated re-run + live two-session
repro. No code changes were made except docs (this sign-off, the bug docs, and
the spec errata in `docs/design/door-features.md`).

**Sign-off date:** (this pass)
**QA verdict:** **PASS-WITH-CONDITIONS** (see "Final verdict")

---

## 1. Scope

Verification of the door feature against spec AC1–AC16: independent code review
of `app/pathfinding.py`, `app/visibility.py`, `app/awareness.py`,
`app/session.py` (`_on_door`), `app/models.py` (`Grid` door field),
`app/static/app.js` / `index.html` / `style.css`; re-run of the unit / WS /
e2e / live test suites; diff of every touched file against the `b1ff47e`
baseline; a live cross-session repro of the shared-sample-grid behavior; and a
static UI smoke pass.

Files byte-identical to baseline (verified `git diff b1ff47e -- <f>` is empty):
`app/awareness.py` (AC6(a)/AC16 — **must be unchanged** ✓), `app/grid.py`
(sample dungeon geometry — **byte-identical** ✓), `app/detection.py`,
`app/generation.py`, `app/main.py`, `app/ws.py`, `app/imaging.py`.

Files modified (diffs reviewed line-by-line): `app/models.py`,
`app/pathfinding.py`, `app/visibility.py`, `app/session.py`, `app/server.py`,
`app/static/{app.js,index.html,style.css}`, `scripts/e2e_proof.py`,
`scripts/qa_doors.py` (new), and the test files.

---

## 2. Verification matrix (re-run results, this pass)

| Suite | Command | Result | Notes |
|---|---|---|---|
| Unit (pytest) | `.venv/bin/python -m pytest` | **471 passed** (71 subtests) | re-run green |
| Unit (unittest) | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 471 tests … OK** | re-run green |
| E2E live | `.venv/bin/python scripts/e2e_proof.py` | **105 checks — ALL PASSED** | door coverage in steps [2], [8], [9]; exit 0 |
| Doors live | `scripts/qa_doors.py` over a started server | **74 checks — ALL PASSED** | exit 0; independent door-aware S-set re-derivation |
| `/health` | `curl /health` | **`{"status":"ok"}`** | during live run |

Live-wire loop followed the repo convention (start server → run `qa_doors.py` →
kill → remove `.ld_server.log` / `.ld_server.pid`). **Server stopped and
artifacts removed; the working tree was left clean of server files.**

Independent in-process checks I ran (not part of the suites, to not trust the
engineers' claims):
- `app/grid.py` sample `build_sample_map()` doors == `None` ⇒ all 3 doorways `L`;
  `door_state_at`/`is_door_closed`/`set_door`/`sync_doors_after_cell_set` behave
  per §3.5; `from_dict` (no `doors`) → all locked; round-trip preserves `O`;
  `to_dict` omits `doors` when `None`.
- Bare grid (`doors=None`): `walkable(closed door)=False`,
  `is_valid_step` onto it `False`, `has_line_of_sight` across it `False`,
  `find_path` across it `None` — i.e. all-locked default (A2/AC12).
- **AC6/AC16:** `git diff b1ff47e -- app/awareness.py` = **0 lines** (byte-unchanged);
  `app/grid.py` = **0 lines** (byte-identical sample).
- **Cross-session door leak (BUG-DOORS-001):** live repro — a door opened in
  session `qa-aaa-1` re-appears `O` in the **welcome** of fresh session
  `qa-bbb-3` (shared sample `Grid`).
- **AC3 exact-string deviation (BUG-DOORS-002):** in-process repro — a **player**
  `lock` on an **open** door **with a token** returns
  `"cannot close a door with a token on it"` where the §4.3 order (role before
  occupancy) pins `"not allowed"`.

---

## 3. AC1–AC16 audit

| AC | Verdict | Evidence (named tests / live checks) |
|---|---|---|
| **AC1** door state model + round-trip | **PASS** | `test_models.py::TestDoorRoundTrip` (round-trip, `to_dict` emits/omits `doors`), `TestDoorDefaultLocked` (None ⇒ all-locked, `{}`≡None), `TestDoorPostInitValidation` (floor/wall/out-of-bounds/bad-state `ValueError`), `TestDoorAccessors` (`door_state_at` None/L/recorded, `is_door_closed`). live: `qa_doors.py` default-locked checks. |
| **AC2** default locked on every creation path | **PASS** | `test_models.py::TestDoorDefaultLocked::test_sample_map_doors_all_locked`; `test_api.py::TestMapDetail::test_detail_shape` (sample `doors` all `L`); `test_api.py::TestUploadPaint` (upload all `L`); `test_api.py::TestGenerateMap` (generate all `L`); `test_door_session.py::TestDoorPaintSync::test_paint_floor_to_doorway_creates_locked_door` (WS paint → `L`). |
| **AC3** state machine + permissions (exact) | **PASS-WITH-CAVEAT** | `test_door_session.py::TestDoorStateMachine` (all 3×4 legal/illegal + exact strings: `not a doorway`, `destination out of bounds`, `action must be one of unlock/lock/open/close`, `door is locked`, `door is already unlocked/locked/open/closed`, `not allowed`), `test_validation_order_non_doorway_before_action`, `test_validation_order_state_before_role`. **Caveat:** one combination — a **player** `lock` on an **open** door **with a token** — returns the occupancy string instead of the §4.3-order `"not allowed"` (role-then-occupancy). See **BUG-DOORS-002 (P3)**. All 11 other error strings are exact. |
| **AC4** closed door blocks LOS like a wall (incl. corner-cut) | **PASS** | `test_pathfinding.py::TestDoorLineOfSight::test_closed_door_blocks_sight_open_transparent`, `test_closed_door_blocks_like_a_wall`, `test_diagonal_both_elbows_closed_doors_blocked` (both elbows closed → blocked; one elbow open → passes), `test_endpoint_never_blocks`. Code: `has_line_of_sight`/`_blocks_sight` treat a closed door as a wall on-line and in the elbow test. |
| **AC5** closed door blocks movement; open walkable; override bypass | **PASS** | `TestDoorWalkable` (`closed not walkable`, `floor unaffected`, `doors=None ⇒ all locked`); `TestDoorStep` (`onto closed illegal`, `diagonal elbows closed ⇒ corner-cut`); `TestDoorFindPath` (`sealed by closed ⇒ None`, `routes around closed`, `doors=None blocks bare doorway`); `test_session.py::TestMovement::test_move_to_doorway_is_walkable` (asserts `no route — wall in the way` first, then walk after open); override bypass via existing `test_ws.py::test_no_route_without_override_and_gm_override` (override ignores walkability; a closed door is a wall to A*). |
| **AC6** awareness UNCHANGED, door-driven only via LOS | **PASS** | (a) `app/awareness.py` **byte-unchanged** (diff = 0). (b) `test_door_session.py::TestDoorAwarenessUnchanged` (closed-within-radius → APPROX, closed-beyond-radius → INVISIBLE, open → FULL, GM unfiltered) + `test_awareness.py::TestPlayerTierInvisible::test_doorway_passes_line_of_sight` (closed → APPROX, open → FULL). (c) GM unfiltered: `test_gm_never_filtered`. (d) *all-doors-open ⇒ pre-feature awareness:* established transitively — awareness code byte-unchanged + open door is sight-transparent (AC4/AC5 pins + `W4_MASK_ALL_OPEN`), and `qa_doors.py` asserts `awareness(door open) == build_awareness` byte-equal. **No dedicated all-doors-open-vs-baseline awareness test exists** (minor gap; see §5 suggestions). |
| **AC7** explored map UNCHANGED; closed-door far side H/E; face S (D5) | **PASS** | `test_visibility.py::TestWorkedExampleW4` (closed-door far side (6,6) = **H**, face (5,5) = **S** via D5, 68 S/124 H), `test_spot_cells_and_independent_rederivation`; `test_door_session.py::TestDoorExploredMap::test_closed_door_far_side_h_then_s_then_e` (H→S→E across open/close); `TestWorkedExampleW4AllOpen::test_all_open_reproduces_legacy_mask` (all-open ⇒ legacy 70 S/122 H — regression pin); live `qa_doors.py` (far side H→E, face S, open ⇒ S). |
| **AC8** monotonicity + freeze preserved | **PASS** | `TestDoorExploredMap` monotonicity assert; `e2e_proof.py` step [9] `monotonicity: no S/E cell became H`; `qa_doors.py` `explored: monotonic — no S/E cell ever became H`. |
| **AC9** occupancy guard (D3, A5) | **PASS** | `test_door_session.py::TestDoorOccupancy` (`test_close_with_token_rejected`, `test_lock_while_open_with_token_rejected`, `test_lock_from_unlocked_not_guarded`). Code: `_any_entity_at` guard on `close` and `lock`-from-`O`; `lock`-from-`U` unguarded. |
| **AC10** wire + REST + rendering | **PASS** | (a) `test_door_session.py::TestDoorWireState` (GM/player welcome/state carry full `doors`, no-doorways grid omits) + `test_ws.py::TestDoorWire`; (b) `test_api.py` GET/upload/generate all carry additive `doors` (no new route); (c) `test_frontend.py::TestDoorPaletteTokens::test_full_and_explored_door_colors` (#d97706/#f59f00/#e03131 distinct from floor #efe9dc + wall #3b4252, plus explored variants); (d) `test_frontend.py::TestDoorStatic` (GM Door tool + 4 sub-buttons, 3 `legend-doors` chips) + **static check of `index.html`** (see §4); (e) `test_legend_doors_chips_are_not_gm_gated` (chips shown to both roles). |
| **AC11** frontend rendering + interaction | **PASS** (after spec errata) | (a) `TestDoorRender` (3 states/3 full-tier colors, explored greys, default-locked renders red, S/E mixed, H not drawn, floor base + no wall hatch); (b) `TestDoorStateModel` (absent/null ⇒ `{}`, valid stored, **malformed ⇒ `{}`**, broadcast replaces); (c) `TestDoorGmTool` (select+action+dispatch, sub-row hidden off-tool, default `unlock`, player has no tool); (d) `TestPlayerDoorTap` (**L→open, U→open, O→close**, default-locked→open, locked→"door is locked" toast, own-token cell not a door action, floor cell still moves); (e) preview canvas unaffected (null-matrix path). **The original spec AC11(d)/§7.6 letters were inverted and incoherent; the shipped inverse-action mapping is coherent and is what the tests pin — the spec was corrected (errata, see §5).** |
| **AC12** backward compatibility (A2) | **PASS** | `TestDoorDefaultLocked::test_from_dict_no_doors_key_all_locked`; in-process: 2/3-arg `walkable`/`is_valid_step`/`has_line_of_sight`/`find_path` still run and behave all-locked on a bare grid's doorway; `from_dict` (no `doors`) ⇒ `doors=None` ⇒ all locked. |
| **AC13** A1 test-impact audit | **PASS** (minor note) | Every §13(A1)-enumerated test was updated with the "open the door first / assert closed-default" pattern and its intent preserved: `test_floor_and_doorway_walkable`, `test_elbows_may_be_doorways`, `test_routes_through_the_door_gap`, `test_door_diagonal_elbow_is_walkable`, `test_doorway_does_not_block_sight`, `test_doorway_passes_line_of_sight`, `test_move_to_doorway_is_walkable`, the **W4 mask literal** (regenerated 68 S/124 H + `W4_MASK_ALL_OPEN` legacy pin), `test_generation.py::TestC7Detour` (opens all doors). **Note:** `test_sd_doorways_seen_only_via_s1` was listed as A1-affected in §13 but was **not** modified — it still passes (its `in_vis == LOS` assertion holds under closed-default doors); its name is now slightly stale (closed doors are revealed via D5/S2, not S1) but the assertion is correct. |
| **AC14** e2e + live proof | **PASS** | `e2e_proof.py` **all 105 checks PASS** with door coverage woven into step [2] (closed blocks → "no route", unlock→U, open→O, walk through), [8] (generated doors all-L, door-aware pathfind), [9] (door-aware explored walk, S/E/H re-derivation); `qa_doors.py` **74/74 PASS** (default locked, GM unlock/open, walk-through, closed-door awareness APPROX/INVISIBLE/FULL, explored H/E + face, occupancy rejection, player permissions, REST `doors`, door-aware S-set re-derivation). `/health` ok. |
| **AC15** performance budget | **PASS** | `test_door_session.py::TestDoorPerformance::test_full_recompute_within_budget` (60×60, all carved doorways mixed open/closed, 6 players + GM, full `state_for` loop `< 500 ms`) and `test_find_path_across_open_doors_within_budget` (60×60 `find_path` across many open doors `< 50 ms`). Both pass; the door-aware predicates add only a constant-factor lookup (verified in the `_closed_doors` derive-once + `closed`-set threading in `pathfinding.py`/`visibility.py`). |
| **AC16** full regression | **PASS** | `pytest` **471 passed** + `unittest` **471 OK**; `e2e_proof.py` all-✓; `/health` ok; `app/grid.py` **byte-identical** (diff 0); `app/awareness.py` **byte-identical** (diff 0). |

**AC audit summary:** 15 PASS, 1 PASS-WITH-CAVEAT (AC3 — one narrow exact-error-string
deviation, **BUG-DOORS-002**, P3). No AC is failed. AC11 passes only after the
§7.6/AC11(d) spec errata (the shipped mapping is coherent; the old spec text was
not).

---

## 4. UI smoke (static analysis; no browser)

Verified against `app/static/index.html` and `app/static/app.js`:

- **GM Door tool present:** `<button class="tool-btn" data-tool="door">🚪 Door</button>`
  (`index.html:249`), inside the GM-only `#paint-group` (`index.html:241`).
- **4 sub-buttons present:** `#door-action-row` with `data-door-action`
  `unlock / lock / open / close` (`index.html:251-254`), revealed only while the
  Door tool is armed (`app.js` `setTool` → `doorActionRow.hidden`).
- **3 legend chips present + both roles:** `.swatch.door-open / .door-unlocked /
  .door-locked` chips (`index.html:165-167`), in `#legend` **not** wrapped by any
  `.gm-only` (confirmed — the only `.gm-only` elements are `btn-new-map`,
  `#no-map`, `#entity-tools`, `#paint-group`, `#override-wrap`); the HTML comment
  at `index.html:161-163` states they are "intentionally NOT gated by body.is-gm."
- **Control-hint copy for both roles:** GM `Click a door to ${state.doorAction}`
  (`app.js:1477`); player `Tap a tile to move your character · tap a door to
  open/close it` (`app.js:1482`).
- **No leftover duplicate "Door" paint button:** the old `data-tool="doorway"`
  button was **renamed** `▣ Doorway` (`index.html:245`); the new `data-tool="door"`
  `🚪 Door` tool is distinct (`index.html:249`). Only these two — no stray
  duplicate.
- **Palette distinctness:** `T.doorOpen #d97706` / `T.doorUnlocked #f59f00` /
  `T.doorLocked #e03131` (`app.js:479-481`) — all distinct from floor `#efe9dc`
  and wall `#3b4252`; explored variants `#8b94a3`/`#9a8f7a`/`#a06b6b`
  (`app.js:482-484`). `doorStateAt` defaults unrecorded doorways to `"L"`
  (`app.js` `doorStateAt`); `validateDoors` treats a malformed `doors` as `{}`
  (all locked). GM door tool + player tap wiring confirmed in `app.js` canvas
  `click` handler (`sendDoor` for GM door tool; inverse-action `sendDoor` for a
  player tapping an unoccupied doorway cell).

UI smoke: **PASS** (all required elements present and correctly role-gated).

---

## 5. Spec corrections (errata) made to `docs/design/door-features.md`

1. **§7.6 player-tap mapping (coherence fix).** The shipped mapping is the
   **inverse action** — a tap sends the action that *changes* the door:
   `L → open` (server: "door is locked" toast), `U → open`, `O → close`. The
   original §7.6 body read `U → close, O → open`, which is **logically
   incoherent** (a `U` door is closed, so `U→close` is always "already closed";
   an `O` door is open, so `O→open` is always "already open" — a player could
   **never** open a door, contradicting the user requirement "doors can be
   opened and closed," and contradicting §7.6's own second bullet "tapping an
   open door toggles it closed"). I **verified the shipped inverse mapping is
   the only coherent one** (confirmed by the code and by `TestPlayerDoorTap`).
   Added an **ERRATUM** block above §7.6's first bullet and rewrote the bullet
   to pin `U→open, O→close, L→open`.
2. **AC11(d) letters.** Corrected to match the shipped inverse mapping
   (`U→open, O→close, L→open`), with an erratum cross-reference to §7.6.
3. **§7.1 explored-tier palette (finalize note resolved).** The frontend report
   flagged a "§7.1 vs §7.3 explored-palette hex conflict." On audit, the
   committed spec's §7.1 table and §7.3 `T` token list **agree**
   (`#8b94a3`/`#9a8f7a`/`#a06b6b` explored; `#d97706`/`#f59f00`/`#e03131`
   full-tier) and match `app.js`/`style.css` byte-for-byte — so there was no
   live value conflict to resolve. The one soft spot was the §7.1 parenthetical
   "(Exact hex … the engineer's to finalize at build time …)." I replaced it
   with a build note **resolving the finalize step to the shipped (==§7.3)
   values**, so the palette table no longer reads as "TBD."

No other spec text was changed. (AC11's "pass" verdict is against the corrected
spec.)

---

## 6. Bug docs created

| ID | Title (short) | Severity | Status |
|---|---|---|---|
| **BUG-DOORS-001** | Cross-session door-state leak: all sessions on an unregistered id (incl. `"default"`) share the same mutable sample-dungeon `Grid`, so a door opened in one session is seen open in another | **P2** | **CONFIRMED / OPEN** (live repro); recommended fix-or-document; **non-blocking** (default single-browser UI unaffected) |
| **BUG-DOORS-002** | `_on_door` returns the occupancy error for a **player** `lock` on an **open** door that has a token, where the §4.3 pinned order (role before occupancy) returns `"not allowed"` | **P3** | **CONFIRMED / OPEN** (in-process repro); recommend reorder + regression test; **non-blocking** (error-only path, action rejected either way) |

Both are filed per the house format of `docs/qa/BUG-EXPLORED-01.md` (status,
severity, component, spec reference, symptom, root cause, file:line,
reproduction, expected-vs-actual, verdict).

---

## 7. Known items disposition (task item 3)

- **(a) Shared sample-grid identity** → **BUG-DOORS-001 (P2).** Confirmed a real
  cross-session state leak (live: a door opened in session A appears `O` in
  fresh session B's welcome). Root cause is the **pre-existing, intentional,
  e2e-pinned** shared `Grid` reference in `app/main.py get_session()`; door
  state (and GM cell-paint) is the first per-cell state that makes it
  observable. Disposition: **fix-or-document, non-blocking** — recommend
  (a) copy the sample grid into each session that falls back to it, or (b)
  document the limitation + treat the default session as the single supported
  sample-game surface. The default browser flow (`wsSession="default"`) does
  not trigger it.
- **(b) Spec text drift** → **resolved via errata** (§5). Verified the shipped
  player-tap mapping (L→open, U→open, O→close) is the only coherent one;
  corrected §7.6 + AC11(d); resolved the §7.1 explored-hex "TBD" note. The
  reported "§7.1 vs §7.3 hex conflict" did **not** exist in the committed doc
  (all hexes agree) — only the finalize phrasing needed closing.
- **(c) e2e step [9] coupling to step [2] door state on the shared grid** →
  **test hygiene only, mitigated.** Step [9] reads the **live** door state
  (`door_live`) and opens whatever isn't already `O` rather than assuming a
  fixed state, so it is robust to the shared grid. It is a *symptom* of
  BUG-DOORS-001 but is handled correctly; no separate bug.

---

## 8. Suggested new tests (gaps)

1. **Player `lock` on open+token door (BUG-DOORS-002):** `test_door_session.py`
   — assert a **player** `lock` on an **open** door **with a token** returns
   `{"type":"error","message":"not allowed"}` (role before occupancy).
2. **Cross-session door isolation (BUG-DOORS-001):** an in-process `GameSession`
   test that two sessions resolving to the sample map do **not** share door
   state — or, if the shared identity is kept, a test that pins the intended
   isolation contract (currently absent).
3. **AC6(d) all-doors-open awareness regression pin:** a test that
   `build_awareness` over the sample dungeon **with all three doors open** is
   byte-identical to the pre-feature baseline (today it is only implied by
   "awareness.py byte-unchanged" + open-door transparency; a direct pin would
   close the gap).
4. **A1 audit naming:** `test_sd_doorways_seen_only_via_s1` — either update the
   name/comment to reflect that closed doors are revealed via the D5/S2 face
   rule (not S1), or drop it from the §13(A1) "must update" list to keep the
   audit honest.

---

## 9. Final verdict

**PASS-WITH-CONDITIONS.**

**Reasoning.** The door feature is implemented correctly and completely across
all 16 acceptance criteria: 15 PASS and 1 PASS-WITH-CAVEAT (AC3). The
closed-door-blocks-LOS/movement-with-corner-cut, open-door-transparent
semantics are right by construction (`pathfinding.py` `_blocks_sight` /
`walkable` / `is_valid_step` + D5 face branch in `visibility.py`);
`app/awareness.py` and `app/grid.py` are byte-identical to baseline (the
hard "unchanged" constraints); the state machine, permissions, occupancy
guard, wire/REST additive `doors`, and the frontend render/interaction all
match spec, with full test + live coverage (471/471 unit, 105/105 e2e,
74/74 doors-live, all green on re-run). The two confirmed defects are both
**low-severity and non-blocking**: **BUG-DOORS-001 (P2)** is a real
cross-session door-state leak rooted in a pre-existing, intentional, e2e-pinned
shared-grid design that the default UI does not trigger; **BUG-DOORS-002 (P3)**
is a single wrong error string on an illegal player action that is rejected
either way. The spec's incoherent §7.6/AC11(d) tap-mapping text was corrected
by errata to the coherent shipped behavior.

**Conditions for a clean PASS:** (1) fix-or-document **BUG-DOORS-001** (copy the
sample grid per session, or document + limit the default-session flow); (2)
reorder the `lock`-from-open occupancy check to run after the role gate (or
amend §4.3) and add the player-role regression test — **BUG-DOORS-002**.

Because neither condition blocks the core feature (all door behavior is correct
and covered), this sign-off is **PASS-WITH-CONDITIONS**: the doors feature is
approved to ship, with the two P2/P3 items tracked for follow-up.
