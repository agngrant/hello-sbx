# QA Sign-off — Boss Entity (multi-tile enemy token)

**Feature:** Boss enemy entity — data model, footprint-aware session
occupancy, save/load round-trip, and canvas rendering of the six boss
sizes per `docs/specs/boss-entity.md`.
**Branch:** `feat/boss-entity` (uncommitted working tree — verified as-is,
**not committed**)
**Spec:** `docs/specs/boss-entity.md` (footprint table §2, body §3, skull
§4/§4.1, E-tier §5, interaction §6, **AC1–AC7 §7**, out-of-scope §8)
**QA mode:** independent re-run of every suite + the Node-harness frontend
suite (real `app.js` execution) + the live `e2e_proof.py` run + line-by-line
diff review against the spec tables. No new live smoke was written this
round (see §7, recommended follow-up).

**QA date:** 2026-09-21
**QA verdict:** **PASS** — 7/7 AC; the boss feature is complete end to end
(model → wire → saves → canvas → legend → GM spawn UI). Full suites green,
e2e green, no new mypy errors. Two findings: one **pre-existing** e2e
scenario bug (caused by committed boss work, fixed — §6.1) and one
documented wire-format design note (size-only, §6.2).

---

## 1. Scope & independence

This sign-off covers the whole boss feature as it exists in the working
tree, including the BUG-024 test-gate repair and the BUG-025
`occupied_by` drop that were blocking the branch:

- **Model** (`app/models.py`): `Entity.size`, `BOSS_FOOTPRINTS`
  (`{2:(2,1), 4:(2,2), 6:(2,3), 8:(2,4), 10:(2,5), 12:(3,4)}` — exactly
  spec §2), `footprint_cells` / `entity_cells` / `boss_footprint_cells`.
- **Session** (`app/session.py`): `_spawn_boss` (full-footprint
  in-bounds + free check, exact message `Boss footprint does not fit`),
  footprint-aware **move** destination validation (stop-cell contract,
  spec §8 — routing stays entity-unaware), footprint-aware **place**
  validation, `create_entity` boss size (default 2, explicit honored),
  `_rebuild_saved_roster` (passes `size`), `_find_free_floor_for(w,h)`,
  footprint-anchored `_reposition_out_of_bounds`.
- **Saves** (`app/saves.py`): `_validated_entities` validates and carries
  `size` through the bundle round-trip (boss without valid size → rejected).
- **Frontend** (`app/static/`): `BOSS_FOOTPRINTS` / `BOSS_SKULL_POS` tables
  + `bossDims()` (the wire sends `size` only — see §6.2), `drawBoss`
  (blob from table dims, r = 0.14×min(W,H) tiles, 2px outline, dimmed
  interior grid), skull center from the §4.1 table, `drawSkull` stroke =
  5% of icon size, rounded-rect selection ring (2px offset, spec §6),
  token-path double-draw removed, GM spawn UI (boss kind + 6-variant size
  selector, role/kind-gated), legend chip with real 20×10 art.
- **Tests:** `tests/test_boss.py`, boss cases in `tests/test_saves.py` /
  `tests/test_session.py`, `tests/test_frontend.py::TestBossEntityFrontend`
  (7 new tests), harness boss-chip + export support.

## 2. Test-suite results (all re-run for this sign-off)

| Suite | Command | Result |
|---|---|---|
| pytest (full) | `.venv/bin/python -m pytest` | **821 passed**, 0 failed, **153 subtests**, 29.19 s |
| unittest discover | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 805 tests — OK** (27.09 s) |
| Frontend harness | `pytest tests/test_frontend.py` | **240 passed**, 31 subtests (incl. 7 new `TestBossEntityFrontend`) |
| e2e proof | `.venv/bin/python scripts/e2e_proof.py` | **ALL E2E CHECKS PASSED** (all ✓) |
| JS syntax | `node --check app/static/app.js` | PASS |
| mypy | `mypy --python-executable .venv/bin/python app/` (system mypy 2.3.1; the venv has no mypy) | **0 errors** — the WIP tree's single pre-existing `models.py` index error (also at HEAD) was cleared by a behavior-preserving `None`-narrowing in `boss_footprint_cells`; frozen baseline is 25, HEAD tree was 6 (gate: only shrink) |
| Collection | `pytest --collect-only` | exit 0 (BUG-024 gate repaired) |

## 3. Per-AC audit (spec §7, AC1–AC7)

| AC | Verdict | Evidence |
|---|---|---|
| **AC1** each of the six sizes renders one rounded blob at exactly W×H (table §2) | **PASS** | `bossDims(e)` resolves `size` → `BOSS_FOOTPRINTS[size]` (`[1,1]` fallback); `drawBoss` sizes the `roundRect` to `W*s × H*s` from those dims. `test_boss.py::test_footprint_table_exact_values` pins the server table to spec §2; `test_frontend.py::TestBossEntityFrontend::test_footprint_table_matches_spec_section_2` pins the client table to the same values, and `test_boss_dims_resolves_size_and_falls_back_to_single_tile` covers the fallback. |
| **AC2** corners rounded at `r = 0.14 × min(W,H)` tiles | **PASS** | `drawBoss`: `const r = 0.14 * Math.min(W, H) * s` (app.js:1708) — single blob, one `roundRect` fill per tier. |
| **AC3** body fill `#e03131` (S-tier) / `#8a5a5e` (E-tier), 2px outline | **PASS** | `ctx.fillStyle = eTier ? "#8a5a5e" : T.enemy` with `T.enemy === "#e03131"` (app.js:704); `ctx.lineWidth = 2` outline stroke; interior grid lines dimmed to 30% alpha per §2/§3. |
| **AC4** black line skull (≈0.35 tile, stroke 5% of icon size) at the §4.1 position per footprint | **PASS** | Skull center from `BOSS_SKULL_POS[e.size]` in the boss render loop; `drawSkull` stroke `size * 0.05` (spec §4). `test_skull_table_matches_spec_section_4_1` pins the client table to §4.1 exactly ({2:(0.5,0.5), 4:(1.0,0.75), 6:(1.0,0.5), 8:(1.0,0.5), 10:(1.0,0.5), 12:(1.5,0.75)}). |
| **AC5** explored-tier boss: greyed body + grey skull, identical position | **PASS** | The `eTier` flag drives **only** the fill/stroke color (`#8a5a5e` body, greyed skull); position, dims, and r are computed identically — no per-tier branch in geometry. |
| **AC6** placement overhang (any footprint tile off-map or on a wall, or onto another entity) rejected | **PASS** | Server: `_spawn_boss` checks **every** footprint cell (in-bounds, not wall, not occupied) → exact message `Boss footprint does not fit`; `_on_move`/`_on_place` destination validation walks the mover's full footprint → `destination out of bounds` / `destination occupied`. Tests: `test_spawn_boss_out_of_bounds_is_rejected`, `test_spawn_boss_on_an_occupied_cell_is_rejected`, `test_move_onto_any_boss_footprint_cell_is_blocked` (moving a single tile onto **any** cell of a boss's footprint is blocked), `test_gm_cannot_move_the_boss_onto_its_own_cell`, `test_invalid_boss_size_is_rejected`. |
| **AC7** legend entry matches the in-canvas art | **PASS** | `renderLegendBossSwatch()` draws the **real** `drawBoss` + `drawSkull` (size-2 fake entity at `BOSS_SKULL_POS[2]`) into the 20×10 `.boss-swatch` canvas — same code path as the map, not a copy. Legend chip present in `index.html`. `test_legend_boss_swatch_renders_actual_art` (canvas 20×10, one paint) + `test_legend_boss_swatch_is_idempotent`. |

## 4. Save/load round-trip (the size column)

| Check | Verdict | Evidence |
|---|---|---|
| Boss `size` survives save → load byte-exact | **PASS** | `saves._validated_entities` validates (`size in BOSS_FOOTPRINTS` for bosses) and carries it; `test_saves.py::test_load_bundle_with_boss_size_round_trip`. |
| Invalid boss size rejected at validation | **PASS** | `test_saves.py::test_load_bundle_boss_with_invalid_size_fails`. |
| `use_map` rebuild restores boss **with** size; out-of-place boss repositioned **footprint-aware** | **PASS** | `session._rebuild_saved_roster` passes `size`; `_find_free_floor_for(w,h)` requires the **full** footprint on floor/doorway (anchor-only fallback only when nothing fits); `_reposition_out_of_bounds` is footprint-anchored (any cell OOB/on-wall ⇒ reposition). `test_session.py::test_use_map_rebuilds_boss_with_size_and_repositions_footprint`. |
| `create_entity` boss size contract | **PASS** | Default 2; explicit `size` honored only when the key is present (`msg.get("size", 2)` would clobber explicit 0/1 — guarded). `test_gm_spawns_a_boss_with_size_on_the_entity`. |

## 5. Test-gate repairs this round (BUG-024 / BUG-025)

- **BUG-024** (collection blocked by untracked WIP tests importing a
  non-existent `W4`): the WIP refactor was abandoned — the three broken
  untracked files deleted, `tests/test_session.py` restored to the
  committed helper contract (explicit-`sender` `FakeConn`, synchronous
  dead-sender test — no `asyncio.get_event_loop()` on the main thread),
  rejoin contract pinned by a new test, and the paired `_announce_join`
  fan-out made robust (per-viewer payloads, send-after-lock, per-send
  guard). → `--collect-only` exit 0; full suite green. Details:
  `docs/qa/BUG-024.md` (now Fixed).
- **BUG-025** (3 boss tests red via a `TypeError` from a non-existent
  `find_path` keyword): the bogus `occupied_by=` was dropped — boss spec
  §8 keeps routing entity-unaware; occupancy is enforced at the **stop**
  cell. `tests/test_boss.py` drift corrected to the committed contract
  (welcome-frame spawn assertion; exact `"destination occupied"` error
  frames). Details: `docs/qa/BUG-025.md` (now Fixed).

## 6. Findings

### 6.1 PRE-EXISTING e2e scenario bug (fixed this round)
`scripts/e2e_proof.py` step 11(c) ("neutral npc walks THROUGH the open
safe door") was **broken at HEAD** — reproduced on a clean detached
worktree of `31b859c`. Root cause: the scenario was written in `fa0b4d6`
(pre-boss), when `_on_move` had **no destination-occupancy check** and
cells could be shared; the boss commit `4acafcb` added the footprint-aware
stop check, so the NPC's destination `(6,5)` — where the hostile `Vex11`
stands (and which section (d) asserts still does) — is now correctly
rejected with `destination occupied`. Fix (minimal, intent-preserving):
the NPC's **stop cell** is now `(6,6)` (free right-room floor); the route
still transits the safe door `(5,5)` — column x=5 is all wall except the
doorway, so every left→right route transits `(6,5)` regardless, and
`find_path` is entity-unaware by design (§8), so the transit is legal.
All other assertions (path via (5,5), hostile still at (6,5), override
guards) unchanged. Result: **ALL E2E CHECKS PASSED**.

### 6.2 Wire format (documented design decision — acceptable)
`Entity.to_dict()` emits **`size` only** for bosses (additive; no
`w`/`h`). The client therefore derives W×H and the skull center from the
spec §2/§4.1 tables (`BOSS_FOOTPRINTS` / `BOSS_SKULL_POS`), which the
frontend tests pin to the spec exactly. Keeping the wire minimal is the
right call (the tables are a frozen spec constant); if a seventh size is
ever added, both tables must be extended in lockstep.

### 6.3 Test-coverage gaps (not bugs; recommend)
1. **Live boss smoke:** no end-to-end script spawns a boss over a real WS
   connection and drives a save→restart→load of a map containing one
   (covered at unit level: spawn/move/place/rebuild/round-trip). Optional
   follow-up: extend `scripts/e2e_proof.py` with a boss step (GM spawns
   size-8, player sees the entity in state, save→load round-trips size).
2. **E-tier boss pixel check:** AC5 is verified by code inspection +
   harness geometry; a dedicated harness assertion that an eTier boss's
   fill color is `#8a5a5e` while its geometry equals the S-tier one would
   pin it (the harness `makeCtx` already records fill styles).

## 7. Final verdict

**PASS.** 7/7 acceptance criteria met. Full suites green: **pytest 821
(+153 subtests), unittest 805 OK, frontend harness 240, e2e_proof all-✓,
node --check PASS, mypy 0 errors** (below the frozen 25-error baseline —
gate "only shrink, never grow" satisfied).
The boss feature is complete across model, wire, saves, session occupancy,
rendering, GM spawn UI, and legend. The one red gate found during final
verification (the e2e safe-door scenario) was proven **pre-existing at
HEAD** and fixed with a minimal, intent-preserving scenario correction,
documented above. No regressions. **Nothing was committed**; the working
tree is left green and ready for the commit decision.

*Verification artifacts: this sign-off, updated `docs/qa/BUG-024.md` /
`docs/qa/BUG-025.md` (both now Fixed), `tests/test_frontend.py::
TestBossEntityFrontend`, boss cases in `tests/test_boss.py` /
`tests/test_saves.py` / `tests/test_session.py`. No server left running.*
