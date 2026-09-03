# LittleDungeons — Team TODO

_Kept current by the orchestrator. Branch: `feat/explored-map`._

## Branch baseline (verified this session)

| Item | Value |
|---|---|
| Branch / HEAD | `feat/explored-map` @ `b1ff47e` |
| Baseline tag | **`working sight`** → `b1ff47e` (local, unpushed) |
| `main` | `5ad236f` (= `origin/main`; branch is 6 commits ahead, **NOT merged to main**) |
| `origin/feat/explored-map` | `94a7a9b` (branch 1 commit ahead of remote, 0 behind) |
| Working tree | clean |

**Features enabled on this branch:** core v3.0 (upload → detection, A* movement, GM
powers, three-tier awareness), awareness ring + per-player radius (0–20, default 4),
GM-generated X×Y BSP dungeon maps, **explored map** (per-player S/E/H fog with
memory — branch-only, not on `main`) + BUG-EXPLORED-01 fix (QA signed off).

## Completed

- [x] Team familiarisation — branch, history, enabled features (orchestrator)
- [x] Tag `working sight` on `b1ff47e` — created + verified (backend_engineer)
- [x] **Feature request + build-ready spec: Openable/Closable Doors** —
  `docs/design/door-features.md` (designer; 18 sections, AC1–AC16, A1–A10
  assumptions, file-by-file impact table)
- [x] Spec + session log committed (see git log)

## In progress / next — Openable/Closable Doors

Workflow: spec ✅ → build → test → evaluate. Spec: `docs/design/door-features.md`.

- [ ] **Backend** (backend_engineer): `Grid.doors` model + sync helper; door-aware
      `walkable`/`is_valid_step`/`has_line_of_sight`; `visible_cells` closed-door
      face branch (D5); WS `{type:"door",x,y,action}` + `_on_door` (GM-only
      unlock/lock; players open/close while unlocked; occupancy guard);
      REST additive `doors` field; tests (pathfinding/visibility/session/ws);
      e2e_proof step; `scripts/qa_doors.py`. **A1:** update the enumerated
      existing tests that assume open doorways (requirement wins).
- [ ] **Frontend** (frontend_engineer): three-state door palette (red locked /
      amber-unlocked / amber open; explored-grey variants), GM Door tool +
      sub-buttons, player tap-to-open/close, legend chips, Node harness tests.
- [ ] **QA** (qa): pytest + unittest green, e2e_proof all ✓, live `qa_doors.py`,
      bug docs, sign-off.
- [ ] **Docs** (with QA pass): PROJECT.md §4/§5/§6/§9 + README doors section.

## Open items (backlog, no change)

- [ ] Merge `feat/explored-map` → `main` (owner's call; fast-forward is safe)
- [ ] `docs/design/explored-map.md` §3.2 W4 literal erratum (superseded by
      corrected test fixture — spec-only fix)
- [ ] README limitations list is stale on fog-of-war wording (predates the
      explored map)
- [ ] Optional: room-density / loop-probability params for generated maps
