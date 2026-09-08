# QA Sign-off — Save-Delete Confirmation as a Full-Screen Modal

**Feature:** GM Saves panel delete-confirmation UX rebuilt as a full-screen
modal (supersedes the in-row confirm bar)
**Branch:** `feat/save-load` (uncommitted working tree — verified as-is, **not
committed**)
**Spec:** `docs/design/save-load-delete-modal.md` (AC1–AC14 in §8, edge cases
E1–E12, assumptions A1–A12). Supersedes
`docs/design/save-load-delete-confirm.md` (kept as a superseded artifact).
**QA mode:** independent re-run of every suite + an **original** live smoke I
wrote (`scripts/qa_modal_smoke.py`, real `app.main`→uvicorn subprocess on an
**ephemeral port**) that (a) asserts the *served* static assets, (b) drives a
real GM session over REST/WS, and (c) drives the **real `app/static/app.js`
modal path with a REAL `fetch`** (Node, no DOM stub for networking) so the
Confirm click issues a **genuine HTTP DELETE** to the live server — the
strongest harness-level modal drive possible (no headless browser exists in
this environment; the team's own convention — `harness.js`'s own comment —
substitutes the Node harness for a browser).

**QA date:** 2026-09-08
**QA verdict:** **PASS** — 14/14 AC; 0 product bugs; 1 non-blocking
test-coverage gap (BUG-016, P3, code verified correct by direct probe).
Backend confirmed byte-untouched. No regressions.

---

## 1. Scope & independence

I did **not** trust or reuse the engineer's runs. I:

1. Re-ran every suite myself (Section 2).
2. Reviewed the full diff (`git diff`): `app/static/app.js` +201/−28 (plus
   comment expansion), `app/static/index.html` +18, `app/static/style.css` +38,
   `tests/js/harness.js` ±1 (EXPORTS: `cancelSaveDelete, syncSaveModal`),
   `tests/test_frontend.py` +784/−2.
3. Wrote and ran my own live smoke (`scripts/qa_modal_smoke.py`) + two Node
   drivers (`qa_modal_live.js`, `qa_modal_panzoom.js`) + one coverage probe
   (`qa_modal_e11_probe.js`).
4. **Confirmed zero diff under `app/*.py`** — the backend
   (`DELETE /api/saves/<id>` 200/401/404) is exactly the QA-verified
   save-load build, per spec A1.

Changed files in the working tree (theirs): `TODO.md`,
`app/static/{app.js,index.html,style.css}`, `docker-agent.yaml`,
`tests/js/harness.js`, `tests/test_frontend.py` + the two spec docs
(untracked). My additions: 4 `scripts/qa_modal_*` files (QA artifacts,
kept alongside the other `scripts/qa_*.py` probes) and this sign-off +
`docs/qa/BUG-016.md`.

## 2. Test-suite results (all re-run by me, clean)

| Suite | Command | Result |
|---|---|---|
| pytest (full) | `.venv/bin/python -m pytest tests/` | **791 passed** (+153 subtests), 0 failed, ~24 s |
| unittest discover (full) | `.venv/bin/python -m unittest discover -s tests -t .` | **Ran 791 tests — OK** |
| Frontend harness (module) | `.venv/bin/python -m unittest tests.test_frontend` | **Ran 231 tests — OK** (incl. **23** `TestSavesDelete` tests) |
| Regression classes (spot) | `TestPanZoom` + `TestSaves{StaticHtml,Action,Load,GmGating,ListRendering,TabFlow,SaveMapState}` | **Ran 76 tests — OK** |
| **Live smoke (QA, mine)** | `.venv/bin/python scripts/qa_modal_smoke.py` | **ALL 29 CHECKS PASSED** (1 ephemeral server) |

Counts match the engineer's report (791 pytest / 791 unittest / 231
frontend) — reconciled: the frontend harness runs as 153 `subtests` inside
pytest, and 231 standalone `unittest` methods. The 14 old in-row
`TestSavesDelete` tests were **replaced** by 23 mapped to AC1–AC14 + E6
(count verified: exactly 23 `def test_` in `TestSavesDelete`; old
`save-row-del-confirm` / `save-row-cancel` class strings are gone from
`app.js`).

## 3. Live smoke outcome (ephemeral port, real GM session)

Ephemeral port allocated and **released** (verified free after stop); no
server left running; `saves/` left empty. 29/29 checks passed:

- **Served `index.html`**: `#save-delete-modal` shell present, `hidden` by
  default, **body-level sibling of the view sections** (after `#map-view`'s
  `</section>`, before `</body>` — NOT nested in `#map-view`, so it also
  covers the upload view's Saved maps tab, E8); `role="alertdialog"`,
  `aria-modal="true"`, `aria-labelledby`/`aria-describedby`; real
  `<button class="btn">Cancel</button>` +
  `<button class="btn btn-danger">Delete</button>`.
- **Served `app.js`**: `function syncSaveModal` present; modal startup
  wiring present; **NO** `save-row-confirm` / `is-confirming`;
  `deleteSave(` count **exactly 2** (definition + the single modal Confirm
  call site); **no `window.confirm` call** (the 2 raw occurrences are inside
  `/* */` prose comments only — verified comment-stripped).
- **Served `style.css`**: `--modal-z: 100` + `--modal-backdrop` tokens;
  `#save-delete-modal { position: fixed; inset: 0; z-index:
  var(--modal-z) }`; **NO** `.save-row-confirm*` / `.save-row.is-confirming`
  (the old in-row rules were already absent at HEAD — the superseded spec's
  bar was never committed; confirmed working tree also 0).
- **Real GM flow**: GM joined over the WS wire (`role gm`) →
  `POST /api/saves {"name":"QA Modal Smoke"}` → 200 record, file on disk.
- **The modal path, real app.js + REAL fetch → real DELETE**: the driver
  booted the actual `app.js`, listed the saves, opened the modal via
  `confirmDeleteSave`, clicked `#save-delete-modal-confirm`, and asserted,
  immediately (in flight) and after resolution:
  - in flight: modal **still open**, `savesDeleteBusy === true`, Confirm
    `disabled` + label `Deleting…` (E2 dismissal lock);
  - after resolution: `DELETE /api/saves/<id>` really hit the server,
    modal closed, `confirmingSaveId === null`, busy `false`, **row removed
    from BOTH the sidebar list and the tab list**, success toast
    `Deleted "QA Modal Smoke".` present, and the
    `saves/<id>.json` file **gone from disk**; `GET /api/saves` no longer
    lists it.
- **Error paths (the message the frontend toasts verbatim)**:
  `DELETE /api/saves/qa-does-not-exist` → **404
  `{"error":"save not found: qa-does-not-exist"}`**; delete-already-deleted
  → 404 again. (AC8's 404-verbatim-toast + re-GET + row-restored behaviour is
  additionally harness-covered by `test_ac8_api_error_on_confirm`.)
- **Pan / drawer / modal-closed regression** (real app.js keydown + drawer
  handlers): with the modal closed, ArrowLeft pans, `−`/`+` zoom
  out/in; the sidebar toggle opens the drawer (scrim un-hides), the scrim
  click closes it (scrim re-hides). Then the modal was re-opened: ArrowLeft
  was **swallowed** (lock, rule 4); Escape **only** closed the modal; the
  next ArrowLeft **panned again** (guard released).

## 4. Per-AC audit (AC1–AC14)

| AC | Verdict | Evidence |
|---|---|---|
| **AC1** normal state: no modal, ordinary rows | **PASS** | Harness `test_ac1_normal_state_modal_modal…_rows_ordinary` (hidden modal; rows exactly `Load`+`Delete`, corrupt `Delete`-only, no `is-confirming`, no in-row confirm element; tab surface matches). Code: `syncSaveModal` null-branch. |
| **AC2** row Delete click opens the modal | **PASS** | Harness `test_ac2_row_delete_click_opens_modal`: real click on the row's `.save-row-del` → shell un-hidden, `confirmingSaveId="act-1"`, body = name + meta (`The Gilded Crypt · 24×16 · 4 tokens · Jan 1 12:00`) + note; rows underneath stay normal. **Static** title/button-text assertions live in the AC11 test (documented engineer deviation — the harness stub parses no static children; the live smoke independently verified the exact title/buttons in the *served* `index.html`). |
| **AC3** full-screen layer + stacking (static) | **PASS** | Harness `test_ac3_full_screen_layer_and_stacking_static` + live-served `style.css`: `position: fixed; inset: 0; z-index: var(--modal-z)`; `--modal-z: 100` > drawer `z-index: 50` > `#scrim z-index: 40`; `#toasts` has no z-index; dialog `background: var(--panel-bg)`, `border-top: 3px solid var(--danger)`, `border-radius: var(--r-panel)`, flex-centered; old in-row rules absent. |
| **AC4** interaction lock while open | **PASS** | Harness `test_ac4_interaction_lock_while_open`: (a) ArrowLeft while open → view unchanged, 0 WS frames; (b) pre-set `selectedEntityId` + Escape → modal closes, **selection unchanged** (no `selectEntity(null)` fall-through); (c) ArrowLeft after close pans (L4 step 2). Rule-4 guard verified in diff (`document keydown` first statement) and live (pan/drawer driver). |
| **AC5** Confirm fires DELETE; success path | **PASS** | Harness `test_ac5_confirm_fires_delete_success_path`: **immediately** after the click the modal is open, busy, Confirm `disabled`/`Deleting…`, and the test fires Escape + Cancel + backdrop click **while in flight** — all no-ops (E2); after resolution: DELETE recorded, modal closed, flag/busy cleared, Confirm re-enabled, row gone from **both** lists, toast `Deleted "Act Three".`. Live: the same sequence against a real server (Section 3). |
| **AC6** Cancel and Escape restore; no request | **PASS** | Harness `test_ac6a_cancel_click_restores_no_request` / `test_ac6b_escape_closes_no_request`: modal hidden, flag null, rows normal, **`_fetch.sent.length === 0`** (no DELETE, no re-GET). |
| **AC7** backdrop click cancels; dialog click does not | **PASS** | Harness `test_ac7a_backdrop_click_cancels` / `test_ac7b_dialog_click_does_not`: `ev.target === backdrop` cancels; a dialog-padding click (target = dialog) leaves the modal open, no request. (PM-approved owner-silent default A4/E7.) |
| **AC8** API error on Confirm | **PASS** | Harness `test_ac8_api_error_on_confirm`: 404 queued → in flight modal stays open/busy; after: exactly 1 DELETE + 1 re-GET, modal closed, **server message verbatim** in an error toast, row restored per re-GET payload, Confirm re-enabled `Delete`. Live: the 404 message shape (`save not found: <id>`) verified against the real server. |
| **AC9** ghost save: confirmed row gone | **PASS** | Harness `test_ac9_ghost_save_no_delete`: list refreshed without the confirmed save → `syncSaveModal` closes the modal, clears the flag, toasts `save not found: act-1`, **0 DELETEs** (only the re-GET). |
| **AC10** one modal; re-targeting | **PASS** | Harness `test_ac10_one_modal_retargeting`: opening `old-1` while `act-1` is open re-targets the **same single** shell (no duplicate element), body names `Opening Night` with its own meta, `act-1` row normal, no double toast. |
| **AC11** accessible shell (static) | **PASS** | Harness `test_ac11_accessible_shell_static` + live-served HTML: `hidden` default; dialog `role="alertdialog"`, `aria-modal="true"`, labelled/described; `h2#save-delete-modal-title` = `Delete save?`; real `<button class="btn">Cancel</button>`; `<button>` with `btn-danger` labelled `Delete`. |
| **AC12** focus in/out + Tab cycle | **PASS** | Harness `test_ac12a` (open without `focus()` throws nothing — guarded), `ac12b` (Cancel spy fires once on open), `ac12c` (Tab cycles Cancel↔Confirm↔Cancel over 3 presses), `ac12d` (cancel-close restore focuses the **freshly re-rendered** row's `.save-row-del` button by `data-id` — createElement spy, engineer deviation (2)), `ac12e` (row-gone restore: no throw). |
| **AC13** non-GM never reaches the modal | **PASS** | (a) Static gating: `TestSavesGmGating` (both surfaces `.gm-only`) passes unmodified. (b) Runtime `test_ac13a_non_gm_role_guard_noop`: player welcome → `confirmDeleteSave` no-op, modal hidden, 0 requests (A9 defensive role guard, verified in diff). |
| **AC14** no regressions; in-row approach removed | **PASS** | (a) Static (me + harness `test_ac14a`): 0 `save-row-confirm` / 0 `is-confirming` in `app/static/*`; `deleteSave(` count **2**; 0 `window.confirm` outside comments. (b) `test_delete_fires_delete` + `test_delete_404_toasts` pass unmodified; the old in-row `ac1–ac7` tests are replaced by AC1–AC12; **all other `TestSaves*` / `TestPanZoom` / static classes pass** (76-test spot run OK; full 791 OK). (c) Live: pan + drawer + toasts + list/tab rendering verified with the modal closed (Section 3). |

### Edge cases (E1–E12)
Covered by tests: E2 (AC5/E2 in-flight dismissal no-ops), E5/E6 (AC12,
corrupt-save flow `test_corrupt_save_modal_flow`: `⚠ corrupt` name line,
**meta line omitted**, note kept, same endpoint), E7 (AC7), E8 (AC2 body-level
shell + tab/sidebar lists both updated on delete), E9 (rule-4 early return +
native button semantics), E10 (CSS tokens + dialog width), E12 (state is
client-only — no wire involvement, trivially true from the diff).
**E1 (idempotent programmatic same-id re-entry) and E11 (unknown id) are NOT
tested** — both probed directly by QA against the real app.js and **correct**
(see BUG-016).

### Assumptions (A1–A12)
All hold. A1 (backend unchanged) verified: **0 diff in `app/*.py`**.
A2/A3/A5/A6/A7/A9/A10/A11/A12 verified in diff + tests. A4 (backdrop=Cancel)
and the in-flight no-op default are the PM-approved owner-silent choices,
tested (AC5/AC7).

## 5. Diff-inspection notes (frozen-contract compliance)

- **`deleteSave(saveId)` is byte-for-byte unchanged** (diffed HEAD vs
  working tree) — success toast `Deleted "<name>".`, 404 → verbatim error
  toast + re-GET, exactly the QA-verified save-load behaviour.
- **`confirmDeleteSave` / `cancelSaveDelete` rewritten per spec §7** (role
  guard → membership guard → flag + render + guarded focus; busy guard on
  cancel); `syncSaveModal()` is a pure render of
  `confirmingSaveId` + `saves` + `savesDeleteBusy`, called from
  `renderSaves()`/`renderSavesTab()` (one restore path).
- **Document keydown guard** is the very first statement (rule 4): every key
  swallowed while open; Escape closes only when not busy.
- **Static shell only** (`index.html`): JS never creates modal elements;
  body text is `textContent`-only (XSS-safe); the `els` registry gains the
  five `saveDeleteModal*` entries. Harness EXPORTS gain
  `cancelSaveDelete, syncSaveModal` (one line) — no other harness change.
- `actions.hidden = false` added in `buildSaveRow` (harness stubs start
  hidden) — a test-harness-compat line, no browser effect.
- `docker-agent.yaml` / `TODO.md` — team config/docs only, not product code.

## 6. Bugs found

| Bug | Severity | Component | Status / impact |
|---|---|---|---|
| **BUG-016** — E1 (idempotent same-id re-entry) and E11 (unknown-id membership guard) have **no dedicated harness tests** | **P3** | Tests (`tests/test_frontend.py::TestSavesDelete`) | **Non-blocking coverage gap.** Both code paths probed directly against the real `app.js` and behave exactly per spec (unknown id → no modal, `save not found: <id>` toast, no request; double same-id call → one modal, no request). Two trivial tests proposed in `docs/qa/BUG-016.md`. |

No P1/P2 bugs. No product defects found: 0 `app/static` in-row artifacts,
backend untouched, all suites green, live smoke 29/29.

## 7. Final verdict

**PASS.** 14/14 acceptance criteria met with concrete evidence (static
source checks where the spec marks them static, harness tests for the rest,
live verification for served assets + the real modal-path DELETE + the
404/200 REST contract + pan/drawer regression). Full suites green:
**791 pytest / 791 unittest / 231 frontend harness**, all re-run
independently. The backend is byte-untouched per spec A1. The in-row confirm
approach is fully removed (no `save-row-confirm` / `is-confirming` /
`window.confirm` anywhere in `app/static`). The owner's complaint — a
confirmation *spatially and semantically separate* from the row — is met by a
full-viewport `z:100` backdrop with a labelled `alertdialog`.

**Recommend proceeding to commit + merge**, and adding the two BUG-016
coverage tests (trivial, no code change) in the same or a follow-up commit.

*Verification artifacts (kept, QA convention): `scripts/qa_modal_smoke.py`
(the 29-check live probe), `scripts/qa_modal_live.js` (real-app.js + real-fetch
modal drive), `scripts/qa_modal_panzoom.js` (pan/drawer regression drive),
`scripts/qa_modal_e11_probe.js` (E1/E11 coverage probe), `docs/qa/BUG-016.md`,
this sign-off. No code was committed; no server left running; ephemeral port
released; `saves/` clean.*

## 8. Addendum — BUG-016 closed (QA, `feat/save-load`)

**BUG-016 (P3, test-coverage only) is now CLOSED.** The two proposed
`TestSavesDelete` tests were added by frontend_engineer as a **test-only**
change — `tests/test_frontend.py` was the *only* file modified since the
original sign-off run (product `app/static/*` + `app/*.py` byte-untouched; the
`scripts/qa_modal_*` probes and `docs/qa/BUG-016.md` / this sign-off are QA
artifacts from the original pass).

* **`test_e1_same_id_reentry_idempotent`** — E1: `confirmDeleteSave('act-1')`
  twice → single `#save-delete-modal`, `confirmingSaveId == 'act-1'`, body
  built exactly once (`Delete "Act Three"?` not duplicated), **zero requests**
  (`api._fetch.sent.length === 0`), no `save not found` toast.
* **`test_e11_unknown_id_no_modal`** — E11: `confirmDeleteSave('ghost-99')`
  → no modal, `confirmingSaveId === null`, **zero DELETEs** (`api._fetch.sent.
length === 0`), dialog body never filled, `save not found: ghost-99` toast.

QA reviewed both bodies and independently re-ran the suites — **final counts:
793 pytest / 233 frontend, 0 failed** (`TestSavesDelete` now 25 tests). All
14/14 AC still hold; the two §9 edge cases QA had only probed by hand now
have regression guards. Verdict stands: **PASS** — recommend proceeding to
commit + merge. Closure detail in `docs/qa/BUG-016.md`.
