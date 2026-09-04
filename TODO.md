# LittleDungeons — Team TODO

_Kept current by the orchestrator. Branch: `feat/safe-room-doors`._

## Branch baseline (verified this session)

| Item | Value |
|---|---|
| Branch / HEAD | `feat/safe-room-doors` @ safe-room-doors feature commit (branched from `main` @ `d4a1cd5`) |
| `main` | `d4a1cd5` — fast-forward-merged from `feat/explored-map` **this session** (local; `origin/main` still `5ad236f` — push is owner's call) |
| `feat/explored-map` | `d4a1cd5` (now equal to `main`) |
| `feat/safe-room-doors` | feature commit on top of `d4a1cd5` (unpushed; no `origin/feat/safe-room-doors` yet) |

**Features enabled on this branch:** core v3.0, awareness ring + per-player radius,
GM-generated BSP maps, explored map (S/E/H fog with memory), Openable/Closable
Doors, and now **GM Safe-Room Doors** (shipped this session, QA PASS).

## Completed

- [x] **Git ops** (backend_engineer): `main` fast-forwarded `5ad236f → d4a1cd5`
      (feat/explored-map merged in); new branch `feat/safe-room-doors` cut from
      `main`. `docker-agent.yaml` (local team config) deliberately left
      uncommitted throughout.
- [x] **Safe-room doors spec** — `docs/design/safe-room-doors.md` (designer;
      AC1–AC16, edge cases E1–E14, assumptions A1–A10)
- [x] **Safe-room doors — backend build** (backend_engineer): `Grid.safe`
      ("x,y"→"C"/"O", additive, mutually exclusive with `doors`), team-aware
      pathfinding (hostile blocked by OPEN safe doors too; hostile override
      guard), `_on_safe_door` state machine (mark/unmark/open/close, GM-only),
      "not a normal door" guard on the frozen `_on_door`, additive `map.safe`
      on wire + REST, e2e_proof step 11, new `scripts/qa_safe_doors.py`
- [x] **Safe-room doors — frontend build** (frontend_engineer): green-cross
      icon (`#3ddc84`, closed = +bar, explored `#8fae9c`), GM 🛡 Safe door tool
      (Mark/Unmark/Open/Close), legend chip, player tap = no-op, `map.safe`
      handling, +32 harness tests
- [x] **Safe-room doors — QA** (qa): independent re-run pytest **617** /
      unittest **617** / e2e **11 steps all ✓** / `qa_safe_doors.py` **47/47 ✓**
      / frontend **131** / 2 original probes; AC1–AC16 all PASS; 0 bugs;
      sign-off `docs/qa/qa-signoff-safe-doors.md` → **PASS**
- [x] Contract docs: PROJECT.md §4/§5/§6/§8/§9 + README Safe room doors
      section (additive)

## In progress

- [ ] Nothing in flight.

## Open items (backlog)

- [ ] **Push is owner's call:** `main` (now at `d4a1cd5`) and
      `feat/safe-room-doors` are local-only; `origin/main` is still `5ad236f`
      and no remote tracking exists for `feat/safe-room-doors`. Also decide
      whether to fast-forward-merge `feat/safe-room-doors` → `main` once
      shipped, and what to do with `feat/explored-map` (fully merged, could be
      deleted).
- [ ] `docker-agent.yaml` working-tree change (team roster config) still
      uncommitted — owner to decide commit/discard.
- [ ] `docs/design/explored-map.md` §3.2 W4 literal erratum (superseded by
      corrected test fixture — spec-only fix)
- [ ] BUG-DOORS-001 structural option: per-session grid copy for unregistered
      session ids (only if cross-session door isolation is ever needed)
- [ ] Optional: room-density / loop-probability params for generated maps
