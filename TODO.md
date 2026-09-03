# LittleDungeons — Team TODO

_Kept current by the orchestrator. Branch: `feat/explored-map`._

## Branch baseline (verified this session)

| Item | Value |
|---|---|
| Branch / HEAD | `feat/explored-map` @ `b1ff47e` (tag **`working sight`**) |
| `main` | `5ad236f` (= `origin/main`; branch is 6 commits ahead, **NOT merged to main**) |
| `origin/feat/explored-map` | `94a7a9b` (branch ahead of remote, 0 behind) |

**Features enabled on this branch:** core v3.0, awareness ring + per-player radius,
GM-generated BSP maps, **explored map** (S/E/H fog with memory), and now
**Openable/Closable Doors** (shipped this session, QA PASS).

## Completed

- [x] Team familiarisation — branch, history, enabled features (orchestrator)
- [x] Tag `working sight` on `b1ff47e` (backend_engineer)
- [x] **Doors feature request spec** — `docs/design/door-features.md` (designer; AC1–AC16)
- [x] **Doors — backend build** (backend_engineer): `Grid.doors` (L/U/O, absent ⇒ all
      locked), door-aware movement + LOS (closed = wall, incl. corner-cut), visibility
      D5 face branch, WS `{type:"door",x,y,action}` state machine, REST additive
      `doors` field, paint sync, A1 test updates, ~100 new tests
- [x] **Doors — frontend build** (frontend_engineer): 3-state palette + glyphs
      (red padlock / amber bar / amber arch, + explored greys), GM Door tool,
      player tap-to-open/close, legend, +35 harness tests
- [x] **Doors — QA** (qa): pytest **472** / unittest **472** / e2e **105 ✓** /
      live `qa_doors.py` **74 ✓**; AC1–AC16 all PASS; sign-off
      `docs/qa/qa-signoff-doors.md` → **PASS**
- [x] BUG-DOORS-002 (P3, player-lock error order) — **FIXED** `69e87a2` + regression test
- [x] BUG-DOORS-001 (P2, shared sample-grid across sessions) — **DOCUMENTED** as
      accepted limitation (README + `docs/qa/BUG-DOORS-001.md`); no behaviour change
- [x] Contract docs: PROJECT.md §4/§5/§6/§8/§9 + README Doors section (additive)

## In progress

- [ ] Nothing in flight. **Server RUNNING** on `0.0.0.0:8000` (PID 42212,
      restarted Prompt 28; branch `feat/explored-map` @ `f0a4fe3`, doors
      feature live).
- [ ] Next owner decision: **merge** `feat/explored-map` → `main`
      (10 commits ahead) and/or **push** the branch (5 ahead of remote).

## Open items (backlog, no change)

- [ ] Merge `feat/explored-map` → `main` (owner's call; fast-forward safe)
- [ ] `docs/design/explored-map.md` §3.2 W4 literal erratum (superseded by
      corrected test fixture — spec-only fix)
- [ ] BUG-DOORS-001 structural option: per-session grid copy for unregistered
      session ids (only if cross-session door isolation is ever needed)
- [ ] Optional: room-density / loop-probability params for generated maps
