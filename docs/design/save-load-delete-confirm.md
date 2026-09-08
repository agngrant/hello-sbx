# Design — Save-Row Delete Confirmation (separated confirm surface)

*Status: spec only (no code yet). Supersedes the one-line "Delete" interaction in
`docs/design/save-load.md` §7.2 ("inline confirm in the row"). Files touched:
`app/static/app.js`, `app/static/style.css`, `tests/js/harness.js` (EXPORTS
list only), `tests/test_frontend.py`. `index.html` is **unchanged** — the
confirm surface is built per-row by JS, exactly like today's row content.*

## 1. Current (broken) interaction

Clicking **Delete** rewrites the row's existing `.save-row-actions` span in
place — `actions.textContent = 'Delete "…"? '` with new Delete/Cancel buttons
appended into the *same span* that held Load/Delete — so the confirmation text
and buttons bleed into the same on-screen area as the row controls, with no
delineated confirm surface. (A second defect: if the row isn't found via
`querySelector`, the code deletes **without any confirmation**.)

## 2. Decision (ONE interaction)

**In-row confirmation bar, derived state.** Clicking Delete moves *that row*
into a confirmation state: the row's Load/Delete actions are hidden and a
new, clearly delineated danger-colored **confirm bar** appears as its own
full-width element *below* the row header (and meta line) — the same visual
idiom as the existing E2 name-conflict `.saves-confirm` surface, but in the
`--danger` palette and attached to the row. **Confirm** issues the delete;
**Cancel** (or `Escape`) returns the row to normal.

The confirmation is **derived state, not in-place DOM surgery**:

- New state field `state.confirmingSaveId: string | null` (one open
  confirmation app-wide; rendering is a pure function of `state.saves` +
  `state.confirmingSaveId`).
- `buildSaveRow(save, surface)` renders the confirm bar when
  `save.id === state.confirmingSaveId` (same place `is-corrupt` is handled
  today). Entering/exiting the state = set the field + re-render both lists —
  one restore path, no stale half-mutated rows.
- Both surfaces (sidebar `#saves-list`, tab `#saves-tab-list`) render from
  the same state, so a save's rows stay consistent in both.

Why: matches the app's existing inline-confirm idiom (no `window.confirm`),
keeps the confirmation spatially separate from Load/Delete, and is trivially
testable in the Node harness (render-driven, no `querySelector` dependence —
the harness stub's `querySelector` returns null, which is also why the
current in-place confirm has no frontend test coverage).

## 3. Interaction rules

1. **Enter:** click a row's `[ Delete ]` (`.save-row-del`) →
   `confirmDeleteSave(saveId)`:
   - If the id is not in `state.saves` (already deleted server-side): error
     toast `save not found: <id>` and **stop — never delete without the
     GM passing through the confirm bar** (fixes the silent-delete fallback).
   - Else set `state.confirmingSaveId = saveId`; `renderSaves();
     renderSavesTab();` (if a *different* row was confirming, it reverts to
     normal automatically). After rendering, focus the row's **Cancel**
     button (guard: `if (cancelBtn.focus) cancelBtn.focus()` — the harness
     stub has no `focus()`).
2. **Confirm:** click `[ Delete ]` in the confirm bar (`.save-row-confirm-delete`)
   → `state.confirmingSaveId = null` → `deleteSave(saveId)` (unchanged):
   `DELETE /api/saves/<id>` → 200 → toast `Deleted "<name>".` +
   `refreshSaves()` (row gone from both lists). 404 → error toast + re-GET.
3. **Cancel:** click `[ Cancel ]` (`.save-row-confirm-cancel`) or press
   `Escape` while a confirmation is open → `cancelSaveDelete()`:
   `state.confirmingSaveId = null` + re-render both lists. No request is sent.
4. **Scope:** a confirmation affects only rows whose `data-id` equals
   `state.confirmingSaveId`; all other rows render exactly as in normal state.
   Corrupt rows get the identical confirm flow (their only trigger is Delete).
5. Any list refresh from another action (save/load) re-renders rows and thus
   closes an open confirmation — acceptable; note it in the code comment.

## 4. Wireframes

Row DOM order in both states: `.save-row-head`, then `.save-row-meta`
(sidebar surface, non-corrupt rows), then — only while confirming —
`.save-row-confirm`.

**(a) Normal state** (sidebar row, non-corrupt):

```
┌ .save-row[data-id="act-1"] ──────────────────────────────┐
│  .save-row-head                                          │
│    .save-row-name   "Act Three — Crypt"                  │
│    .save-row-actions   [ Load ]  [ Delete ]              │   ← .btn-small /
│  .save-row-meta   The Gilded Crypt · 24×16 · 4 tokens ·  │     .btn-small.btn-danger
│                   Jan 1 12:00                            │
└──────────────────────────────────────────────────────────┘
  (.save-row-confirm: not rendered)
```

**(b) Confirmation state** (after clicking Delete on the row):

```
┌ .save-row.is-confirming[data-id="act-1"] ─────────────────────────┐
│  .save-row-head                                                   │
│    .save-row-name   "Act Three — Crypt"                           │
│    .save-row-actions   (hidden attribute — Load/Delete NOT shown) │
│  .save-row-meta   The Gilded Crypt · 24×16 · 4 tokens · Jan 1 12:00│
│  ┌ .save-row-confirm  role="alert"  (separate delineated region) ┐│
│  │ .save-row-confirm-text: Delete "Act Three — Crypt"?           ││
│  │        [ Delete ]   [ Cancel ]                                 ││
│  └────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────────┘
```

The confirm bar is the ONLY visible control area of the row while confirming:
`.save-row-actions` carries the `hidden` attribute, so Load/Delete and the
Confirm/Cancel pair are never on screen simultaneously (the overlap that
caused the report). Tab-surface rows are identical — the bar simply spans the
row's full width under the stacked header (the tab already stacks
`.save-row-actions` under the name).

## 5. Elements, naming, and styling

Matches existing conventions (`.save-row*` classes, `is-*` row modifiers,
`btn btn-small` / `btn-danger`, the `.saves-confirm` block shape, `:root`
tokens).

| Item | Name | Notes |
|---|---|---|
| Row modifier (confirming) | `save-row is-confirming` (on the existing `div.save-row[data-id]`) | mirrors the `is-corrupt` modifier |
| Confirm surface | `div.save-row-confirm` (JS-built, last child of `.save-row`; rendered **only** when `state.confirmingSaveId === save.id`) | add `role="alert"` so the ask is announced |
| Prompt text | `span.save-row-confirm-text` | `textContent` = `Delete ` + name + `?` (string concat, XSS-safe; name via `.save-row-confirm-name` span, `font-weight: 600`) |
| Name span | `span.save-row-confirm-name` | save name, verbatim |
| Confirm button | `button.btn.btn-small.btn-danger.save-row-confirm-delete` | label `Delete` |
| Cancel button | `button.btn.btn-small.save-row-confirm-cancel` | label `Cancel`; focused on open |
| State | `state.confirmingSaveId` (initial `null`) | one open confirmation app-wide |
| Functions | `confirmDeleteSave(saveId)` (existing name, rewritten), `cancelSaveDelete()` (new, add to harness EXPORTS), `deleteSave(saveId)` (unchanged) | row rendering via existing `buildSaveRow` |

CSS (new block next to the existing Saves rules in `style.css`, ~after
`.save-row.is-corrupt`):

```css
/* Delete-confirmation state (save-load-delete-confirm spec): the row's
   actions are hidden; a separate delineated danger bar carries the ask. */
.save-row.is-confirming { border-left-color: var(--danger); }
.save-row-confirm {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--s2);
  margin-top: var(--s2);
  padding: var(--s2) var(--s3);
  font-size: 12px;
  background: rgba(224, 49, 49, 0.10);
  border: 1px solid rgba(224, 49, 49, 0.45);
  border-left: 3px solid var(--danger);
  border-radius: var(--r-control);
}
.save-row-confirm-name { font-weight: 600; }
.save-row-confirm .btn { margin-left: auto; }
.save-row-confirm .btn + .btn { margin-left: var(--s1); }
```

(Shape mirrors the existing `.saves-confirm` block; palette is the `--danger`
token, same as `.save-row.is-corrupt` and `.btn-danger` — no new color tokens.)

## 6. Implementation notes

- `confirmDeleteSave(saveId)`: membership check in `state.saves` (else
  `save not found` error toast, no delete) → set `state.confirmingSaveId` →
  `renderSaves(); renderSavesTab();` → find the rendered row's
  `.save-row-confirm-cancel` button by walking the row's `.children` (the
  harness `children` is a plain array; do **not** rely on `querySelector`) →
  guarded focus.
- `cancelSaveDelete()`: `state.confirmingSaveId = null; renderSaves();
  renderSavesTab();`
- `buildSaveRow`: when confirming, append `is-confirming` to the row class,
  set `actions.hidden = true` (keep the Load/Delete buttons as children —
  `hidden` only hides them), then append the confirm bar with the two
  buttons built by the existing `saveRowAction` helper pattern
  (keep its `ev.stopPropagation()` click wrapper).
- Escape: one `document` keydown listener (app.js already registers a
  document keydown for pan/zoom; a second is fine) — if `ev.key ===
  "Escape" && state.confirmingSaveId`, call `cancelSaveDelete()`.
- Harness: add `cancelSaveDelete` to the EXPORTS list in
  `tests/js/harness.js` (one line, next to `confirmDeleteSave`); no other
  harness change needed.

## 7. Acceptance criteria

- **AC1 — Normal state.** After `renderSaves()` with non-corrupt saves, each
  row's `.save-row-actions` shows exactly `Load` + `Delete` (corrupt rows:
  `Delete` only), the row class has no `is-confirming`, and no
  `.save-row-confirm` element exists in the row.
- **AC2 — Enter confirmation.** Clicking a row's Delete (or
  `confirmDeleteSave(id)`) makes that row: class contains `is-confirming`;
  `.save-row-actions` has `hidden === true`; a `.save-row-confirm` child is
  present, `hidden === false`, its text contains the save's name, and it
  holds a `save-row-confirm-delete` (btn-danger) and a
  `save-row-confirm-cancel` button; `state.confirmingSaveId === id`. Load and
  Delete are **not** visible alongside Confirm/Cancel.
- **AC3 — Confirm deletes.** Clicking the confirm bar's Delete button fires
  `DELETE /api/saves/<id>`; on 200 the row is removed from both rendered
  lists, `state.confirmingSaveId === null`, toast `Deleted "<name>".`.
- **AC4 — Cancel restores.** Clicking Cancel (or dispatching
  `keydown Escape` with a confirmation open) returns the row to normal state
  (Load + Delete visible, confirm bar gone, no `is-confirming`) with **no**
  DELETE request sent.
- **AC5 — Row isolation.** Confirming one save does not change any other row
  (others keep `Load`+`Delete` visible, no `is-confirming`, no confirm bar);
  starting a confirmation on save B while save A is confirming reverts A to
  normal — at most one open confirmation.
- **AC6 — No unconfirmed delete.** If the id is not in `state.saves`,
  `confirmDeleteSave` toasts `save not found: <id>` and fires no request;
  there is no code path that calls `deleteSave` without a rendered confirm
  bar.
- **AC7 — Accessible/keyboard.** All confirm controls are real
  `<button>` elements created via `document.createElement("button")`,
  keyboard operable (focus + Enter/Space); on open, focus is on Cancel
  (guardedly); Escape closes the confirmation without deleting.
- **AC8 — No regressions.** All existing `tests/test_frontend.py` saves tests
  (incl. `TestSavesDelete`, which calls `deleteSave` directly) pass unmodified;
  the harness probes run without uncaught exceptions (no console errors).

## 8. Testability in the existing Node harness (`tests/js/harness.js`)

New tests go in `tests/test_frontend.py`, `TestSavesDelete` (reuse `SavesBase`
`_gm` / `_fetch_resp` / `_NORMAL` fixtures). Harness quirks the spec depends
on:

- `document.createElement` stubs start **`hidden = true`** and model
  `hidden` via classList — the app must set `confirmEl.hidden = false`
  explicitly (as `showSaveConfirm` already does), which makes `hidden`
  assertable both ways.
- Stub `children` is a plain array and `querySelector` returns null — so the
  interaction is **render-driven** (set `api.state.saves`, call
  `api.confirmDeleteSave('act-1')`, inspect `api.els.savesList.children`).
  The implementation must locate rows/buttons by walking `.children` +
  `.dataset.id`, never via `querySelector` (this is also what makes the
  interaction testable at all — today's in-place version is unreachable in
  the harness).
- Element stubs support `dispatchEvent({type})` and a `className` string once
  the app sets it; the app's click wrappers call `ev.stopPropagation()`, so
  tests dispatch `{ type: "click", stopPropagation: () => {} }`.
- There is no `el.focus()` on stubs — the focus call in `confirmDeleteSave`
  must be guarded (`if (btn.focus) btn.focus()`), else every enter-confirm
  probe throws in the harness.
- Escape: `api.document.dispatch("keydown", { key: "Escape" })` reaches the
  app's document listener.

DOM elements tests query (all reachable through the API surface, no CSS):

- Rows: `api.els.savesList.children` / `api.els.savesTabList.children` →
  `row.dataset.id`, `row.className` (assert `is-confirming` presence, same
  style as the existing `is-corrupt` assertion).
- Header/actions: `row.children[0].children[1]` is `.save-row-actions` →
  `actions.hidden` (false normal / true confirming) and
  `actions.children[i].textContent` (`Load`, `Delete`).
- Confirm bar: `row.children[row.children.length - 1]` →
  `className` contains `save-row-confirm`, `hidden === false`,
  `children[0].textContent` contains the save name;
  `children[1]` / `children[2]` are the two `<button>`s (assert
  `className` contains `save-row-confirm-delete` / `save-row-confirm-cancel`,
  then drive them via `dispatchEvent`).
- State: `api.state.confirmingSaveId`.
- Network: `api._fetch.responses = [ ok200({ok:true}), ok200({saves:[]}) ]`
  then assert
  `api._fetch.sent.find(s => s.url === "/api/saves/act-1" &&
  s.opts && s.opts.method === "DELETE")` (same pattern as the existing
  `test_delete_fires_delete`).
- Tab-surface parity: same assertions against `api.els.savesTabList.children`
  after `api.renderSavesTab()`.
