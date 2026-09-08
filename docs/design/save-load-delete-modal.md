# Design — Save-Delete Confirmation as a Full-Screen Modal

*Status: spec only (no code yet), branch `feat/save-load`. **Supersedes
`docs/design/save-load-delete-confirm.md` for the delete-confirmation UX** —
that document is kept as a superseded artifact (do not delete it). Files
touched by the eventual build: `app/static/app.js`, `app/static/style.css`,
`app/static/index.html` (**this spec does modify index.html**, unlike the
in-row spec), `tests/js/harness.js` (EXPORTS list only),
`tests/test_frontend.py`. The backend is **unchanged** —
`DELETE /api/saves/{id}` already exists and is QA-verified
(save-load spec §5.4: 200 `{"ok":true}` / 401 non-GM / 404
`{"error":"save not found: <id>"}`).*

## 1. Owner report (why this spec exists)

The shipped in-row confirmation bar (save-load-delete-confirm spec §2/§4) is
still reported as looking "mixed over the load/delete box": although the bar
was delineated, it remained *spatially attached to the row*, directly beneath
the Load/Delete controls it replaced, so the confirmation still reads as part
of the row's control cluster rather than a separate, deliberate step. The
owner wants the confirmation to be a **modal dialog overlaying the whole GM
screen** — a full-screen backdrop covering the map view + sidebars (and the
top bar / toasts / drawer), with a clearly centered dialog carrying the ask
and the Confirm/Cancel actions. The row's Load/Delete controls must be
visually irrelevant to the confirmation (they stay visible *under* the
backdrop — that is fine — but the confirmation UI itself is unmistakably
separate from the row).

## 2. Supersession & scope (explicit)

- **Superseded:** `docs/design/save-load-delete-confirm.md` — everything in
  it that describes the in-row confirm surface is replaced by this spec.
  Specifically removed: the `.save-row-confirm` bar and its CSS
  (`.save-row-confirm*` block in `style.css` §5 of the old spec), the
  `is-confirming` row modifier, the `hidden`-ing of the row's
  `.save-row-actions` while confirming, and `role="alert"` on the row bar.
  Rows render exactly as in "normal" state at all times (Load + Delete, or
  Delete-only for corrupt rows).
- **Kept (reused, not redesigned):** the single app-wide state field
  `state.confirmingSaveId`; the function names `confirmDeleteSave(saveId)`
  / `cancelSaveDelete()` / `deleteSave(saveId)`; `deleteSave`'s body
  (DELETE → success toast `Deleted "<name>".` + re-GET / error toast +
  re-GET on not-found); the membership guard ("never delete without the GM
  passing through the confirmation"); the static guard that `deleteSave(`
  has exactly **one** call site in `app.js`; the toast copy.
- **Also superseded:** save-load spec §7.2/§7.3 bullet "Delete: click →
  inline confirm in the row" — the confirmation *surface* changes to this
  modal; the endpoint, the row-removal-on-success behavior, and the toast
  strings are unchanged.
- **Unchanged:** the backend (routes, wire protocol, save format); players
  (they never see the Saves panel, hence never the modal); the rest of the
  Saves panel / Saved maps tab behavior (save/load/name-conflict).

## 3. Decision (ONE interaction)

**Full-screen modal, derived state.** Clicking Delete on a save row moves
the *app* (not the row) into a confirmation state: a single, body-level
`#save-delete-modal` element — a fixed, full-viewport backdrop with a
centered dialog — is shown, identifying the save by name + the same metadata
the row shows. **Confirm (Delete)** fires the existing DELETE request;
**Cancel / Escape / backdrop click** close without any request.

The modal is **derived state, not in-place DOM surgery**:

- `state.confirmingSaveId: string | null` is the single source of truth for
  "which save's confirmation is open" (one open app-wide, as today). A new
  transient boolean `state.savesDeleteBusy` marks the in-flight DELETE
  (sub-state *of* the open modal — the open/closed derivation stays purely
  `confirmingSaveId`).
- One static shell in `index.html` (a body-level sibling of the three view
  `<section>`s, like `#scrim` but covering the *entire* app — it is NOT
  inside `#map-view`, because the Saved maps **tab** in the upload view has
  Delete buttons too and must get the same modal). JS never creates or
  clones modal elements; it fills the body's text per open (textContent
  only — XSS-safe) and toggles `hidden`.
- `syncSaveModal()` renders the modal as a pure function of
  `state.confirmingSaveId` + `state.saves` + `state.savesDeleteBusy`; it is
  called from `renderSaves()` and `renderSavesTab()` (idempotent, cheap), so
  every existing re-render path (confirm open, cancel, refresh, delete
  result) keeps the modal consistent — one restore path, no stale DOM.

Why a modal and not a better bar: the owner's complaint is about *separation
from the row*, and only a viewport-covering layer guarantees it (backdrop
also physically blocks the map/panel/lobby, which a row bar cannot). Why
static shell + derived fill: one instance, zero per-row construction, real
parsed `<button>`s (best a11y + keyboard semantics for free), and every
element is reachable in the Node harness through `api.els.*` (the harness
stub for `document.body` has **no** `children`/`appendChild`, so a
JS-constructed body-appended modal would crash the harness — a static
`$("#id")` shell sidesteps that entirely, matching how `#scrim` and every
other app surface is handled).

## 4. Interaction rules

1. **Enter.** GM clicks a row's `[ Delete ]` (`.save-row-del`, both
   surfaces) → `confirmDeleteSave(saveId)`:
   - Role guard: `state.role !== "gm"` ⇒ no-op (defensive; the surfaces are
     `.gm-only`).
   - Membership check: id not in `state.saves` ⇒ error toast
     `save not found: <id>` and **stop — never delete without the GM
     passing through the modal** (kept from the old spec; also fixes the
     original silent-delete fallback).
   - Else: `state.confirmingSaveId = saveId; state.savesDeleteBusy = false;`
     → `renderSaves(); renderSavesTab(); syncSaveModal();` (the rows re-render
     in normal shape; `syncSaveModal` fills the dialog text and sets
     `hidden = false`) → record the return-focus target (the **freshly
     rendered** row's Delete button — rows are rebuilt on re-render, so the
     lookup walks the re-rendered list by `data-id`, harness-style) → focus
     the dialog's **Cancel** button (guarded: `if (btn.focus) btn.focus()` —
     the harness stub has no `focus()`).
2. **Confirm.** Click `[ Delete ]` in the dialog
   (`#save-delete-modal-confirm`):
   - No-op if `state.savesDeleteBusy` (double-click guard).
   - Else: set `state.savesDeleteBusy = true` → `syncSaveModal()` (dialog
     stays open; Confirm becomes `disabled`, label `Deleting…`; all dismissal
     paths — Cancel / Escape / backdrop — become no-ops while busy, rule 3).
   - `await deleteSave(id)` (**unchanged** function): 200 → toast
     `Deleted "<name>".` + re-GET (row gone from both lists); 404/other →
     error toast with the server's message verbatim + re-GET on not-found.
   - `finally`: `state.savesDeleteBusy = false; state.confirmingSaveId = null;`
     → `renderSaves(); renderSavesTab(); syncSaveModal();` (modal closed) →
     restore focus (rule 3b). **The modal closes on the request's
     resolution** (owner-specified: "on success close modal + …; on API
     error close modal + error toast") — NOT on the click.
3. **Dismissal (Cancel / Escape / backdrop click).**
   a. `cancelSaveDelete()` — guarded first line: `if (state.savesDeleteBusy)
      return;` (a committed DELETE is never aborted by dismissal) → else
      `state.confirmingSaveId = null; renderSaves(); renderSavesTab();
      syncSaveModal();` → restore focus. **No request is sent.**
   b. Focus restore: look up the confirmed save's row in the freshly
      re-rendered list; if the row still exists, focus its Delete button
      (guarded); if the row is gone (deleted, or ghost-closed), no focus
      change. Never call focus on a detached element.
   c. **Backdrop click = Cancel** (decision, see A4): the backdrop's click
      listener closes the modal only when `ev.target === backdrop element`
      — a click anywhere *inside* the dialog (buttons included, plus dialog
      padding) does not cancel. Justification: Cancel is the *safe*
      direction, so an accidental outside click can never delete; the
      destructive action remains a deliberate click on the danger button
      itself.
4. **Escape while open.** The existing document `keydown` handler's very
   first statement becomes: `if (state.confirmingSaveId) { if (ev.key ===
   "Escape" && !state.savesDeleteBusy) cancelSaveDelete(); return; }` — i.e.
   while the modal is open **every** key is swallowed by the guard: Escape
   closes (when not busy) and **only** closes (the rest of the current
   Escape behavior — `selectEntity(null)`, `setDrawer(false)`,
   `showView("map")` — must NOT run; the `return` prevents it), arrows do
   not pan, `+`/`-` do not zoom, Enter does not select an awareness row.
5. **Ghost save (confirmed row no longer exists).** Any re-render while
   `state.confirmingSaveId` is set but the id is no longer in `state.saves`
   (e.g. the list was refreshed and another GM's client deleted the save)
   → `syncSaveModal()`: error toast `save not found: <id>`, clear
   `confirmingSaveId`, hide the modal, **no DELETE is fired** (mirrors the
   old spec's "no unconfirmed delete" guarantee, now on the refresh path).
   A network-level ghost (row still locally listed, file gone server-side)
   instead surfaces as the 404 path of rule 2 (error toast + re-GET).
6. **Scope / single modal.** At most one open confirmation app-wide (one
   flag, one static element). Opening save B's confirmation while save A's
   is open (programmatic, or the GM dismissing-then-reopening) **re-targets
   the same modal** to B (dialog text + flag update; A's row was never
   special to begin with). In the real browser the backdrop prevents
   clicking another row while one modal is open; in the harness the
   re-target is directly callable and must not duplicate the element or
   double-toast.
7. **Non-GM.** Players: the Saves panel and the Saved maps tab are
   `.gm-only` (CSS `display:none` for non-GM — existing, unchanged), so no
   Delete trigger exists for them; `confirmDeleteSave` additionally no-ops
   for `state.role !== "gm"` (A9). The modal shell is body-level and
   technically present in a player's DOM (like `#scrim`), but it can never
   be opened and is `hidden` for their entire session.

## 5. Wireframes

The modal covers the **whole GM screen**, whichever view is active (map view
— the primary case — or the upload view's Saved maps tab). Rows underneath
are in their ordinary shape (Load + Delete visible under the backdrop).

**(a) Open (idle)** — the backdrop over the map view:

```
┌────────────────────────────── #save-delete-modal ─────────────────────────┐
│ backdrop — position:fixed; inset:0; z-index: var(--modal-z) = 100        │
│ (above: drawer sidebar z:50, #scrim z:40, #toasts (no z-index)).         │
│ Covers map canvas + legend + sidebar + topbar + control bar.             │
│ Click on the backdrop (outside the dialog) = Cancel.                     │
│                                                                          │
│             ┌────────── .save-modal-dialog ────────────────────┐          │
│             │ role="alertdialog" aria-modal="true"             │          │
│             │ aria-labelledby="save-delete-modal-title"        │          │
│             │ aria-describedby="save-delete-modal-body"        │          │
│             │                                                  │          │
│             │  Delete save?                     ← title (h2)   │          │
│             │                                                  │          │
│             │  Delete "Act Three"?           ← .save-modal-name (bold)   │
│             │  The Gilded Crypt · 24×16 · 4 tokens ·          │          │
│             │  Jan 1 12:00                       ← .save-modal-meta      │
│             │                                        (muted, = row meta) │
│             │  The save file will be removed. A map already    │          │
│             │  loaded from this save is not affected.  (note, muted)     │
│             │                                                  │          │
│             │                      [ Cancel ]   [ Delete ]    │          │
│             │                 (secondary)  (btn-danger, default focus=Cancel)
│             └──────────────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────────────────────┘
   Under the backdrop (visible but unreachable): the Saves panel rows still
   show their normal [ Load ] [ Delete ] — no is-confirming, no in-row bar.
```

**(b) Open (busy, DELETE in flight)** — same dialog, actions area dimmed
(`.save-modal-dialog.is-busy`): Cancel inert (click/Escape/backdrop all
no-ops), Confirm `disabled` with label `Deleting…`. Closes when the request
resolves (success → row removed + toast; error → error toast).

**(c) Corrupt save** — name line reads `Delete "Broken"? ⚠ corrupt` (name +
flag, exactly like the row's name); the meta line is **omitted** (a corrupt
record carries no width/height/entity_count/created_at); the note line is
kept.

Modal DOM order (static shell; body children built per open):
`#save-delete-modal` → `.save-modal-dialog#save-delete-modal-dialog` →
`h2#save-delete-modal-title`, `div#save-delete-modal-body` (per open:
`p.save-modal-name`, `p.save-modal-meta` [non-corrupt only],
`p.save-modal-note`), `div.save-modal-actions` →
`button#save-delete-modal-cancel.btn`, `button#save-delete-modal-confirm.btn.btn-danger`.

## 6. Elements, naming, and styling

Matches existing conventions (`#scrim`-style fixed layer, `.btn` /
`.btn-small` / `.btn-danger`, `:root` tokens, string-concat textContent).

| Item | Name | Notes |
|---|---|---|
| Modal root / backdrop | `div#save-delete-modal` (static in `index.html`, body-level sibling of the three view sections, `hidden` attribute initially) | fixed full-viewport; `z-index: var(--modal-z)`; flex-centered; `background: var(--modal-backdrop)`; click (target-check) = cancel; **starts hidden** both in HTML and by harness stub default |
| Dialog box | `div.save-modal-dialog#save-delete-modal-dialog` | `role="alertdialog"`, `aria-modal="true"`, `aria-labelledby`/`aria-describedby` (see §6.3); `--panel-bg`; `--r-panel`; 3px `--danger` top border; `is-busy` modifier dims actions |
| Title | `h2#save-delete-modal-title.save-modal-title` | static text `Delete save?` |
| Body | `div#save-delete-modal-body` | JS fills per open — textContent only: name line, meta line (skipped for corrupt), note line |
| Name line | `p.save-modal-name` (JS-built) | `Delete "Act Three"?` — the name verbatim, `font-weight: 600`; corrupt appends ` ⚠ corrupt` (name via textContent — XSS-safe) |
| Meta line | `p.save-modal-meta` (JS-built, non-corrupt) | same composition as the row meta: `map_name · W×H · N tokens · formatSaveDate(created_at)` |
| Note line | `p.save-modal-note` (JS-built) | `The save file will be removed. A map already loaded from this save is not affected.` |
| Cancel button | `button#save-delete-modal-cancel.btn` | label `Cancel`; **focused on open** (guarded); in real DOM left of Confirm (safe first in tab order) |
| Confirm button | `button#save-delete-modal-confirm.btn.btn-danger` | label `Delete`; busy ⇒ `disabled = true`, label `Deleting…` |
| State | `state.confirmingSaveId` (initial `null`, **kept**), `state.savesDeleteBusy` (initial `false`, **new**, transient) | one open confirmation app-wide; busy = the in-flight DELETE sub-state |
| Functions | `confirmDeleteSave(saveId)` (existing name, rewritten), `cancelSaveDelete()` (existing name, rewritten: busy guard + focus restore), `syncSaveModal()` (**new**), `deleteSave(saveId)` (**unchanged**) | plus internal helpers `findSaveRowDeleteButton(saveId)` (walks re-rendered list `children` by `data-id`, never `querySelector`) and the startup wiring below |

`els` registry additions: `saveDeleteModal`, `saveDeleteModalDialog`,
`saveDeleteModalBody`, `saveDeleteModalCancel`, `saveDeleteModalConfirm`
(plain `$("#…")` entries like every other surface).

Startup wiring (next to the other `els.…addEventListener` blocks): Cancel →
`cancelSaveDelete()`; Confirm → the rule-2 async sequence; backdrop root →
`ev.target === els.saveDeleteModal && cancelSaveDelete()`; dialog `keydown`
→ Tab-cycle trap (§6.3). The document `keydown` gets the rule-4 guard line.

### 6.1 index.html (static shell, added before `</body>`)

```html
<!-- Save-delete confirm (save-load-delete-modal spec): full-screen modal
     layer — a body-level sibling of the views, covering map view AND the
     upload view (Saved maps tab). Static shell: the app fills the body's
     text per open (textContent only) and toggles `hidden`. hidden by
     default; players never reach it (.gm-only triggers + role guard). -->
<div id="save-delete-modal" hidden>
  <div class="save-modal-dialog" id="save-delete-modal-dialog" role="alertdialog"
       aria-modal="true" aria-labelledby="save-delete-modal-title"
       aria-describedby="save-delete-modal-body">
    <h2 class="save-modal-title" id="save-delete-modal-title">Delete save?</h2>
    <div id="save-delete-modal-body"></div>
    <div class="save-modal-actions">
      <button id="save-delete-modal-cancel" class="btn">Cancel</button>
      <button id="save-delete-modal-confirm" class="btn btn-danger">Delete</button>
    </div>
  </div>
</div>
```

### 6.2 CSS (new block in `style.css`; **also remove** the old
`.save-row.is-confirming` + `.save-row-confirm*` rules)

Tokens: no backdrop/overlay token exists today (`#scrim` uses a literal), so
two new tokens are added to `:root` — everything else reuses existing tokens:

```css
:root {
  /* Save-delete modal (save-load-delete-modal spec): full-screen
     confirmation layer — above the drawer sidebar (z:50) and #scrim
     (z:40); #toasts has no z-index, so 100 covers it too. */
  --modal-backdrop: rgba(12, 15, 24, 0.72);
  --modal-z: 100;
}

#save-delete-modal {
  position: fixed;
  inset: 0;
  z-index: var(--modal-z);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--s4);
  background: var(--modal-backdrop);
}
.save-modal-dialog {
  width: min(440px, 100%);
  background: var(--panel-bg);
  border: 1px solid #39415a;          /* same border literal as .btn (no --border var) */
  border-top: 3px solid var(--danger);
  border-radius: var(--r-panel);
  padding: var(--s4);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55);
}
.save-modal-title { margin: 0 0 var(--s2); font-size: 15px; }
.save-modal-name { margin: 0; font-weight: 600; overflow-wrap: break-word; }
.save-modal-meta { margin: var(--s1) 0 0; font-size: 12px; color: var(--text-muted); }
.save-modal-note { margin: var(--s2) 0 0; font-size: 12px; line-height: 1.5; color: var(--text-muted); }
.save-modal-actions { display: flex; justify-content: flex-end; gap: var(--s1); margin-top: var(--s4); }
.save-modal-dialog.is-busy .save-modal-actions { opacity: 0.6; }
```

(Reuse: `--panel-bg`, `--danger`, `--text-muted`, `--s1/--s2/--s4`,
`--r-panel`; buttons keep `.btn` / `.btn-danger`. New tokens: only
`--modal-backdrop` + `--modal-z`. Removals: `.save-row.is-confirming`,
`.save-row-confirm`, `.save-row-confirm-name`, and their two `.btn` margin
rules — the in-row approach is gone.)

### 6.3 Accessibility — `role="alertdialog"` + `aria-modal="true"` (chosen & justified)

- **`role="alertdialog"`** (not `role="alert"`): this is a *blocking dialog
  that interrupts the GM and requires a decision* — exactly ARIA's
  alertdialog (per the ARIA Authoring Practices dialog pattern: modal
  container, labelled, described). `role="alert"` is a live-region
  *announcement* for non-interactive notifications; using it on a dialog
  with buttons would mislabel the container and is what the superseded
  in-row bar did.
- **`aria-modal="true"`**: tells assistive tech to ignore everything outside
  the dialog while it is open — the AT-level counterpart of the visual
  backdrop blocking the map/panel.
- **Labelled + described:** `aria-labelledby` → the title element,
  `aria-describedby` → the body (name + metadata + consequence note).
- **Focus model:** focus moves **into** the dialog on open (Cancel — the
  safe default, and the first stop in tab order); Tab / Shift+Tab are
  cycled between the two dialog buttons by the dialog's own `keydown`
  handler (two-element trap: each Tab press — in either direction — moves
  focus to the *other* button; focus cannot escape into the background
  while open); on close, focus returns to the row's Delete button if it
  still exists. The cycle is tracked with a module-level variable, never
  `document.activeElement` (the harness stub has none — A6).
- Both actions are real parsed `<button>`s: native Enter/Space activation
  works, and the app's global keydown guard (rule 4) ensures the document
  level never competes with them while the modal is open.

## 7. Implementation notes

- `confirmDeleteSave(saveId)`: role guard → membership check (else
  `save not found: <id>` error toast, return) → set
  `confirmingSaveId`/clear `savesDeleteBusy` → `renderSaves();
  renderSavesTab(); syncSaveModal();` → `saveModalReturnFocusId = saveId`
  (module-level; the *rendered* button is looked up later, because rows are
  rebuilt by the re-render) → guarded focus of `els.saveDeleteModalCancel`.
- `cancelSaveDelete()`: `if (state.savesDeleteBusy) return;` →
  `state.confirmingSaveId = null;` → `renderSaves(); renderSavesTab();
  syncSaveModal();` → `restoreSaveModalFocus()`.
- `syncSaveModal()`: if `confirmingSaveId === null` → ensure modal
  `hidden = true`, busy=false, confirm enabled/labelled `Delete`, clear
  busy modifier, return. Else find the save in `state.saves`: **not found**
  → error toast `save not found: <id>`, clear `confirmingSaveId`, hide,
  return (rule 5). Found → fill `#save-delete-modal-body` children
  (clearContainer first; `p.save-modal-name` = `Delete "<name>"?` [+
  ` ⚠ corrupt`], `p.save-modal-meta` = row-meta composition [non-corrupt],
  `p.save-modal-note` = the fixed note — all textContent/string concat),
  set `els.saveDeleteModal.hidden = false` (the harness stub starts hidden),
  `is-busy` modifier + confirm `disabled`/`Deleting…` iff
  `state.savesDeleteBusy`.
- Confirm click (startup wiring): `const id = state.confirmingSaveId;
  if (!id || state.savesDeleteBusy) return; state.savesDeleteBusy = true;
  syncSaveModal(); try { await deleteSave(id); } finally {
  state.savesDeleteBusy = false; state.confirmingSaveId = null;
  renderSaves(); renderSavesTab(); syncSaveModal(); restoreSaveModalFocus();
  }`. `deleteSave` is byte-for-byte unchanged (its success path already
  re-GETs and removes the row; the trailing re-render is an idempotent
  duplicate).
- `restoreSaveModalFocus()`: `const btn = saveModalReturnFocusId ?
  findSaveRowDeleteButton(saveModalReturnFocusId) : null;
  saveModalReturnFocusId = null; if (btn && btn.parentNode && btn.focus)
  btn.focus();` — the lookup walks `els.savesList.children` (then
  `els.savesTabList.children`) by `dataset.id`, then the head's actions'
  last child (the Delete button) — plain `.children` arrays, no
  `querySelector`.
- Document `keydown`: insert at the top of the existing handler:
  `if (state.confirmingSaveId) { if (ev.key === "Escape" &&
  !state.savesDeleteBusy) cancelSaveDelete(); return; }` — the `return`
  also fixes the current side behavior where Escape-with-confirmation-open
  additionally deselects / closes the drawer / bounces back from the upload
  view.
- Dialog Tab cycle: `els.saveDeleteModalDialog.addEventListener("keydown",
  (ev) => { if (ev.key !== "Tab" || state.savesDeleteBusy) return;
  ev.preventDefault(); const next = saveModalFocusBtn ===
  els.saveDeleteModalCancel ? els.saveDeleteModalConfirm :
  els.saveDeleteModalCancel; if (next.focus) next.focus();
  saveModalFocusBtn = next; })`; on open, set `saveModalFocusBtn =
  els.saveDeleteModalCancel` (and guarded-focus it).
- Harness: add `syncSaveModal` to the EXPORTS list in
  `tests/js/harness.js` (one line, next to `confirmDeleteSave`); no other
  harness change — the static shell is reachable via `api.els.*` exactly
  like `#scrim`.

## 8. Acceptance criteria

All runtime ACs are testable in the existing Node harness
(`tests/js/harness.js` + `tests/test_frontend.py`, `SavesBase` fixtures —
`_NORMAL` id `act-1` "Act Three" / `_NORMAL2` id `old-1` "Opening Night" /
`_CORRUPT` id `bad-1` "Broken"; `_fetch.responses` queue;
`api.document.dispatch("keydown", …)`; clicks via
`dispatchEvent({type:"click", stopPropagation: () => {}})`).

- **AC1 — Normal state: no modal, ordinary rows.** After a GM welcome,
  `state.saves` set, `renderSaves(); renderSavesTab();` with
  `state.confirmingSaveId === null`: `api.els.saveDeleteModal.hidden ===
  true`; every row shows exactly `Load` + `Delete` (corrupt: `Delete`
  only), no `is-confirming` class, no in-row confirm element anywhere (the
  in-row approach is fully removed).
- **AC2 — Row Delete click opens the modal.** With two saves rendered,
  dispatch a real click on the `act-1` row's `.save-row-del` button:
  `saveDeleteModal.hidden === false`; `state.confirmingSaveId === "act-1"`;
  title text `Delete save?`; body children: a `save-modal-name` whose
  text contains `Delete "Act Three"?`, a `save-modal-meta` reading
  `The Gilded Crypt · 24×16 · 4 tokens · Jan 1 12:00`, and the fixed note
  line; the dialog holds Cancel (`.btn`) + Delete (`.btn-danger`) buttons.
  Rows underneath remain in normal shape (Load + Delete visible, no
  `is-confirming`).
- **AC3 — Full-screen layer + stacking (static).** `index.html`: the
  `#save-delete-modal` shell exists as a body-level sibling of the three
  view sections (not nested in `#map-view`), `hidden` by default, with the
  §6.1 attributes. `style.css`: `#save-delete-modal` is `position: fixed;
  inset: 0` with `z-index: var(--modal-z)` and `:root` defines
  `--modal-z: 100` (above the drawer sidebar's `z-index: 50` and
  `#scrim`'s `z-index: 40`; `#toasts` has no z-index) and
  `--modal-backdrop`; the dialog is flex-centered, `background:
  var(--panel-bg)`, `border-radius: var(--r-panel)`, with the
  `var(--danger)` top border; the old `.save-row-confirm*` /
  `.save-row.is-confirming` rules are absent from `style.css`.
- **AC4 — Interaction lock while open (runtime).** With the modal open for
  `act-1`: (a) dispatch document `keydown {key:"ArrowLeft"}` →
  `state.view` unchanged and `api._send.sent` empty (the rule-4 guard
  early-returns); (b) pre-set `state.selectedEntityId` to a fake id, then
  dispatch `keydown {key:"Escape"}` → modal closes AND
  `state.selectedEntityId` is unchanged (the old handler's
  `selectEntity(null)` side effect must NOT fire); (c) dispatch
  `keydown {key:"ArrowLeft"}` again now that the modal is closed → pans as
  normal (guard released).
- **AC5 — Confirm fires the existing DELETE; success path.** Modal open
  (via `confirmDeleteSave("act-1")`), queue
  `_fetch.responses = [ 200 {"ok":true}, 200 {"saves":[]} ]`, dispatch click
  on `#save-delete-modal-confirm`: **immediately** after the click the
  modal is still open with `state.savesDeleteBusy === true` and the
  confirm button `disabled === true` (label `Deleting…`) — dismissal is
  locked; after the promise resolves: a `DELETE /api/saves/act-1` request is
  recorded, `saveDeleteModal.hidden === true`,
  `state.confirmingSaveId === null`, `savesDeleteBusy === false`, the row is
  gone from BOTH `savesList` and `savesTabList`, and a toast
  `Deleted "Act Three".` is in `els.toasts`.
- **AC6 — Cancel and Escape restore; no request.** (a) Modal open →
  dispatch click on `#save-delete-modal-cancel` → modal hidden, flag null,
  rows normal, and `api._fetch.sent` contains **no** request at all (no
  DELETE, no re-GET). (b) Repeat from scratch via
  `api.document.dispatch("keydown", {key:"Escape"})` → identical outcome.
- **AC7 — Backdrop click cancels; dialog click does not.** (a) Modal open →
  `saveDeleteModal.dispatchEvent({type:"click", target:
  api.els.saveDeleteModal})` → closed, no request. (b) Modal open →
  `saveDeleteModal.dispatchEvent({type:"click", target:
  api.els.saveDeleteModalDialog})` (a click inside the dialog, e.g. its
  padding) → **still open**, no request (the `ev.target` check).
- **AC8 — API error on Confirm.** Modal open, queue
  `[ 404 {"error":"save not found: act-1"}, 200 {"saves":[…act-1…]} ]`,
  click Confirm: while in flight the modal stays open (busy); after
  resolution: the DELETE is recorded, the modal is closed, an error toast
  carries the server's message verbatim, a re-GET `GET /api/saves` is
  recorded, the row is back in the list per the re-GET payload, flag null,
  busy false, confirm re-enabled with label `Delete`.
- **AC9 — Ghost save: confirmed row no longer exists.** Open the modal for
  `act-1` (listed), then simulate the list losing it: set
  `state.saves = [old-1]`, queue `[ 200 {"saves":[old-1]} ]`, run
  `refreshSaves()` → `syncSaveModal` (via the re-render path) closes the
  modal, clears the flag, and toasts error `save not found: act-1`;
  `api._fetch.sent` contains **no** DELETE (only the re-GET).
- **AC10 — One modal; re-targeting.** Modal open for `act-1` → call
  `confirmDeleteSave("old-1")` → the **same single** `#save-delete-modal`
  element is still the only one (no duplication), `hidden === false`,
  flag `=== "old-1"`, body now names `Opening Night` with its own meta; the
  `act-1` row is in normal shape; no double `save not found` toast.
- **AC11 — Accessible shell (static, index.html).** `#save-delete-modal`
  carries `hidden` initially; its dialog descendant has
  `role="alertdialog"`, `aria-modal="true"`,
  `aria-labelledby="save-delete-modal-title"`, and
  `aria-describedby="save-delete-modal-body"`; the title is the `h2` with
  that id; `#save-delete-modal-cancel` is a real `<button class="btn">`
  labelled `Cancel`; `#save-delete-modal-confirm` is a real
  `<button>` whose class includes `btn-danger` and whose label is `Delete`
  (justification for `alertdialog` over `alert`: §6.3).
- **AC12 — Focus in/out + Tab cycle.** (a) Opening the modal in the
  harness (whose stubs have **no** `focus()`) completes without throwing
  (the guarded call — a regression probe like the old spec's AC7 runtime
  test). (b) Set a spy `focus` on `api.els.saveDeleteModalCancel` before
  `confirmDeleteSave("act-1")` → the spy is called (focus moves into the
  dialog on open). (c) With spies on both dialog buttons, dispatch
  `keydown {key:"Tab"}` to `saveDeleteModalDialog` → the Confirm spy fires;
  a second Tab (shiftKey either way — two-element cycle) → the Cancel spy
  fires again. (d) After Cancel closes the modal, the restore path runs
  against the freshly re-rendered row's Delete button (lookup by
  `data-id`); with the row present the probe completes without throwing,
  and with the row removed (AC9 state) it likewise does not throw.
- **AC13 — Non-GM never reaches the modal.** (a) Static: the existing
  `.gm-only` gating tests for both surfaces still pass (the only Delete
  triggers remain inside GM-only containers). (b) Runtime: after a
  **player** welcome, `confirmDeleteSave("act-1")` is a no-op —
  `state.confirmingSaveId` stays `null`, `saveDeleteModal.hidden === true`,
  no request (defensive role guard, rule 1).
- **AC14 — No regressions; in-row approach removed.** (a) Static:
  `app.js` contains no `save-row-confirm` / `is-confirming` strings;
  `src.count("deleteSave(") === 2` (definition + the single modal Confirm
  call site — the "no unconfirmed delete" guard keeps its form); no
  `window.confirm`. (b) The existing `deleteSave`-direct tests
  (`test_delete_fires_delete`, `test_delete_404_toasts`) pass unmodified;
  the old in-row confirm tests (`test_ac1…test_ac7`, corrupt-row flow) are
  **replaced** by AC1–AC12 here; all other `TestSaves*` suites pass
  unmodified.

## 9. Edge cases

- **E1 — Double-click the row's Delete.** First click opens the modal; in
  the real browser the second click lands on the backdrop (at best cancels
  — safe direction) and never reaches the row button. Programmatic
  double-call of `confirmDeleteSave("act-1")` is idempotent: same flag,
  same single modal, no duplication, no request.
- **E2 — Escape / Cancel / backdrop click while the DELETE is in flight.**
  The request is committed, so every dismissal path no-ops (busy guard,
  rule 3a / rule 4): the modal stays open with the `Deleting…` state until
  the response resolves, then closes with the success or error toast. No
  aborted request, no orphaned modal state, no way for the GM to interact
  with the list while the outcome is pending.
- **E3 — Two GMs' clients are independent.** The modal is purely
  client-side; save-list sync between clients is REST-refresh only
  (existing behavior, no WS push). If GM2 deletes the save while GM1 has
  the modal open: GM1's Confirm hits the **404** path (AC8 — error toast +
  re-GET drops the row); if GM1's list is refreshed first (e.g. GM1
  performs another save/load later), the **ghost** path (AC9) closes it
  without deleting. Neither client can double-delete successfully.
- **E4 — Confirmed row removed locally while the modal is open.** Handled
  by the rule-5 ghost check on the shared re-render path: close without
  deleting + `save not found: <id>` error toast (AC9). While busy this
  path cannot be reached via the UI (all inputs locked); the check remains
  as a defensive invariant.
- **E5 — Focus.** Open ⇒ Cancel focused (safe default). Tab/Shift+Tab
  cycle only between Cancel and Confirm (dialog-level trap). Close ⇒ focus
  returns to the confirmed row's Delete button in the *freshly rendered*
  list (rows are rebuilt, so the target is looked up by `data-id`, never a
  stored detached reference); if the row is gone, focus is left where the
  browser puts it — never a focus call on a detached element. During busy,
  Confirm is `disabled` (native focus falls back to body); the post-close
  restore then re-anchors focus on the row button.
- **E6 — Corrupt save.** Identical modal flow (the row's only trigger is
  Delete, as today): the name line shows `⚠ corrupt`, the meta line is
  omitted (a corrupt record has no width/height/count/date), the note is
  kept. Confirm deletes the corrupt file via the same endpoint (server 404
  semantics unchanged).
- **E7 — Backdrop-click choice and its boundary.** Backdrop click =
  Cancel (safe direction — an accidental outside click can never delete;
  the destructive path is always the danger button itself). The boundary is
  strict: only `ev.target === backdrop` cancels; any click inside the
  dialog (buttons, padding) does not — verified by AC7(b).
- **E8 — Modal opened from the Saved maps tab (lobby / upload view).** The
  shell is body-level, so it covers the upload view identically to the map
  view; the same state drives both surfaces, so Confirm/Cancel remove or
  restore the row in the **tab list and the sidebar list** together
  (e.g. a successful delete from the tab also clears the row from the
  map-view panel).
- **E9 — Enter/Space on the focused dialog buttons.** Native button
  activation: Enter on Cancel cancels, Enter on Confirm deletes; while the
  modal is open the document-level Enter/Space handler (awareness-row
  selection) is unreachable (rule-4 early return), so there is no
  double-action.
- **E10 — Long names / metadata.** Save labels are ≤ 40 chars; the dialog
  is `width: min(440px, 100%)` with `overflow-wrap: break-word` on the
  name line and `padding: var(--s4)` on the backdrop, so worst-case copy
  wraps inside the dialog and the dialog never exceeds the viewport at
  any breakpoint (including the tablet drawer breakpoint, where the
  modal — z 100 — also sits above the open drawer).
- **E11 — Unknown id reaches `confirmDeleteSave`** (stale trigger, or a
  future code path): no modal opens; error toast `save not found: <id>`;
  no request — the old spec's "no unconfirmed delete" guarantee, kept.
- **E12 — Page reload / navigation with the modal open.** All modal state
  is client-only and ephemeral (no persistence, no wire involvement); a
  reload simply drops it. Nothing has been sent to the server unless the
  GM clicked Confirm.

## 10. Assumptions

- **A1 — Backend unchanged.** `DELETE /api/saves/{id}` (200/401/404, QA-
  verified per save-load spec §5.4/AC18) is used as-is; this spec is
  frontend-only. No wire-protocol change, no new WS messages, no new
  REST routes (F4 of the save-load spec stays intact).
- **A2 — Single confirmation app-wide.** `state.confirmingSaveId` keeps its
  field name and one-open-at-a-time semantics; it now renders the modal
  instead of an in-row bar. `state.savesDeleteBusy` is a transient
  sub-state of the open modal (cleared in the same `finally` as the flag),
  never an independent open condition.
- **A3 — Static shell, filled by JS.** One `#save-delete-modal` in
  `index.html`, `hidden` by default; the app only toggles `hidden`, sets
  text (textContent — XSS-safe), and flips `disabled`/labels per open.
  This spec **does** modify `index.html` (the superseded in-row spec
  deliberately did not) — justified by the app-wide (not per-row) nature
  of the element.
- **A4 — Backdrop click = Cancel** (choice specified per the requirement).
  Rationale: Cancel is the safe direction, so an outside click can never
  delete; the danger action always requires a deliberate click on the
  danger button. (Flagged as open question Q1 for owner ratification.)
- **A5 — Modal closes on request resolution, not on click** (owner
  wording: "on success close modal …; on API error close modal + error
  toast"). While the DELETE is in flight the dialog stays open in the busy
  state and all dismissal paths no-op (E2). Alternative (optimistic close
  on click) rejected: it would leave the row momentarily "normal" under a
  closed modal and make the in-flight state un-communicated.
- **A6 — Minimal focus model, no `document.activeElement` dependency.**
  Initial focus on Cancel + a two-button Tab cycle tracked in a module
  variable; no generic focus-trap utility (this is the app's first modal);
  restore targets the looked-up row button with guarded `focus` calls —
  every focus touchpoint is harness-safe (stubs have no `focus()` /
  `document.activeElement`).
- **A7 — Interaction lock = CSS backdrop + document keydown guard.**
  Pointer events are blocked by the fixed full-viewport backdrop's
  hit-testing in the real DOM (no per-handler guards on canvas/tool/button
  click paths); keyboard is blocked by the rule-4 early return in the
  single existing document keydown handler. Server-pushed `state`
  broadcasts may still re-render rows *behind* the modal (that is the
  server acting, not the GM interacting) — acceptable.
- **A8 — Toasts sit below the modal layer** (`--modal-z: 100` > toasts'
  implicit auto) — in practice they never overlap, because the modal
  closes (busy resolution) before any delete toast fires.
- **A9 — Defensive role guard.** `confirmDeleteSave` no-ops for
  `state.role !== "gm"` in addition to the CSS `.gm-only` gating (testable
  as AC13b; zero behavior change for the GM).
- **A10 — No `window.confirm`, no per-row DOM construction.** The app's
  existing idiom (inline surfaces for non-critical asks, one dedicated
  overlay for the destructive one) is preserved; the name-conflict inline
  confirm (`.saves-confirm`) is untouched.
- **A11 — Graceful focus-restore fallback.** If the trigger row no longer
  exists (deleted / ghost-closed), the restore is simply skipped — no
  `document.body.focus()` (the harness body stub has no focus), no throw.
- **A12 — Harness unchanged except EXPORTS.** One line added
  (`syncSaveModal`); every AC is implementable with the existing stub
  quirks: elements start `hidden = true` (the app sets `hidden = false`
  explicitly, assertable both ways), `children` are plain arrays (walk
  them; `querySelector` returns null — never used), no `el.focus()`
  (guarded calls), `api.document.dispatch("keydown", …)` reaches the real
  document handler, `api._fetch.responses` queues DELETE → re-GET
  sequences, static assertions read `index.html`/`style.css`/`app.js`
  strings as in `TestSavesStaticHtml`.

## 11. Testability in the existing Node harness (`tests/js/harness.js`)

New/rewritten tests live in `tests/test_frontend.py`, `TestSavesDelete`
(reusing `SavesBase` `_gm` / `_fetch_resp` / `_NORMAL` / `_NORMAL2` /
`_CORRUPT`). Harness-quirk notes the spec depends on:

- **Static shell reachability:** `api.els.saveDeleteModal` /
  `saveDeleteModalDialog` / `saveDeleteModalBody` / `saveDeleteModalCancel`
  / `saveDeleteModalConfirm` are registry stubs (auto-created by
  `$("#…")`) with `children` arrays, `hidden` property, and
  `dispatchEvent` — exactly like `els.scrim` today.
- **Opening the modal:** `api.state.saves = […]; api.renderSaves();
  api.confirmDeleteSave('act-1');` then assert `api.els.saveDeleteModal
  .hidden === false`, walk `api.els.saveDeleteModalBody.children` (p text
  nodes), `api.state.confirmingSaveId`. Row-trigger variant: find the row
  in `api.els.savesList.children` by `dataset.id`, take
  `row.children[0]` (head) → `head.children[1]` (actions) → last child
  (Delete button), `btn.dispatchEvent({type:"click", stopPropagation:
  () => {}})` — the real click handler runs (the row-action wrapper calls
  `ev.stopPropagation()`).
- **Dialog buttons:** `api.els.saveDeleteModalConfirm.dispatchEvent({type:
  "click", stopPropagation: () => {}})`; the handler is async — end the
  probe with `.then(...)` over the queued fetch responses (same pattern as
  `test_delete_fires_delete`).
- **Backdrop vs dialog click:** dispatch on
  `api.els.saveDeleteModal` with `target:` set to the backdrop stub vs the
  dialog stub (AC7).
- **Keys:** `api.document.dispatch("keydown", {key:"Escape"})` /
  `{key:"ArrowLeft"}` for the rule-4 guard (AC4/AC6); Tab cycle via
  `api.els.saveDeleteModalDialog.dispatchEvent({type:"keydown",
  key:"Tab"})` (AC12c).
- **Focus spies:** stub elements are plain objects — a test may set
  `api.els.saveDeleteModalCancel.focus = () => { called = true; }` *before*
  opening (the static shell exists pre-open), and assert the app's guarded
  call hit the spy; no-spy probes assert the guard prevents throws (AC12a).
- **Network:** `api._fetch.sent.find(s => s.url === "/api/saves/act-1" &&
  s.opts && s.opts.method === "DELETE")` for the delete; `s.url ===
  "/api/saves"` for the re-GETs (AC5/AC8/AC9); "no request at all" =
  `api._fetch.sent.length === 0` after a cancel (AC6, after
  `api._fetch.reset()`).
- **Toasts:** `api.els.toasts.children.map(t => t.textContent)` contains
  the expected strings (`Deleted "Act Three".`, `save not found: act-1`,
  the 404 server message).
- **Static ACs (AC3/AC11/AC14a):** string assertions over
  `app/static/index.html`, `app/static/style.css`, `app/static/app.js`
  (existing `TestSavesStaticHtml` pattern), including the absences
  (`save-row-confirm`, `.is-confirming`, `window.confirm`) and the
  `deleteSave(` count of 2.

---

*Scope guard: this spec touches `app/static/app.js` (confirm/delete modal
logic + keydown guard), `app/static/style.css` (modal block + new
`--modal-backdrop`/`--modal-z` tokens; in-row confirm rules removed),
`app/static/index.html` (static `#save-delete-modal` shell),
`tests/js/harness.js` (EXPORTS line), and `tests/test_frontend.py`
(confirm-UX tests rewritten to AC1–AC14). No backend module, no wire
surface, no other UI.*
