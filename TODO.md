# LittleDungeons — Team TODO

_Kept current by the orchestrator._

## Active
- [ ] Commit decision for the green feat/boss-entity working tree (backend + frontend + docs all in one interlocking unit — no green committed state achievable in slices)

## Done
- 2026-09-21: boss feature made fully green on feat/boss-entity (uncommitted): BUG-024 test-gate repaired (WIP test files deleted, dead-sender test restored, `_announce_join` fan-out made robust); BUG-025 `occupied_by=` dropped from `find_path` (boss spec §8 — routing entity-unaware, occupancy at stop cell); saves `size` round-trip + footprint-aware `use_map` repositioning; frontend boss rendering per spec §2/§4.1/§4/§6 (footprint/skull tables, double-draw fix, rounded-rect selection, GM spawn size UI, legend swatch); pre-existing e2e_proof step-11(c) scenario bug (HEAD regression from 4acafcb occupancy check) fixed. pytest 821 / unittest 805 / frontend 240 / e2e all-✓ / mypy 0 errors (below the frozen 25 baseline; the WIP tree's single pre-existing `models.py` error cleared by a behavior-preserving `None`-narrowing in `boss_footprint_cells`). Docs: BUG-024.md + BUG-025.md → Fixed, qa-signoff-boss.md, README boss section.
- [x] QA gate re-run 2026-09-16: pytest 1501 run / 6 failed (all known), unittest 795 run / 2 err, node --check PASS; BUG-017..023 closed
- 2026-09-14: ruff safe auto-fixes committed (09b7b41), feat/backend-refactor pushed & in sync with remote
- [x] feat/boss-entity branch created; current branch (backend_engineer)
- [x] Boss entity design spec → docs/specs/boss-entity.md (designer)
- [x] Backend: boss data model (Entity.size, BOSS_FOOTPRINTS, boss_footprint_cells/footprint_cells/entity_cells/is_enemy) + footprint-aware session occupancy (backend_engineer)
- [x] Frontend: boss rendering per spec, incl. §4.1 NW-anchor skull centering — app.js bf5f4dc, daeff44 (frontend_engineer)
- [x] BUG-017 tests/test_boss.py real contract tests @ 708f91b; BUG-018 skull anchor @ daeff44; BUG-019 test_session.py @ f18979f; BUG-021/022/023 models.py (footprint_cells/entity_cells, size rename)
