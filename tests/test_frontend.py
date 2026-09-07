"""Frontend regression tests (BUG-001, 002, 003, 006, 007, 008, 009, 010, 011).

The QA pass (docs/qa/test-plan.md §D) noted the 168 backend tests never
executed the browser-only frontend. These tests DO execute the real
``app/static/app.js`` (and read the real ``app/static/index.html``) by running
it under Node with a stub DOM/WebSocket (``tests/js/harness.js``). No
third-party package is required; the tests simply skip if Node is not on
``PATH`` (they are pure-stdlib otherwise).

Each test maps to the specific QA report it proves:

* BUG-001  allEntities() was referenced but never defined -> ReferenceError
           on the very first render. Here ``onWelcome`` must drive the whole
           welcome->applyState->layout->drawEntitiesAndDots path without
           throwing, for both a GM and a player.
* BUG-002  "Open map in session" used to switch the WS *session id* to the
           new map (stranding players). openUploadedMap must instead send
           ``{type:"use_map", map_id}`` on the SAME session, without closing
           or re-opening the socket.
* BUG-003  the token teleported because the path animation mutated a
           reference the state snapshot replaced. onPath + onState must now
           animate a LOCAL copy (via allEntities), pinning the entity to the
           cell it is currently showing, so the token walks cell-by-cell to
           the goal instead of jumping. ``state.animations``/``isAnimating``
           must also gate further move sends.
* BUG-006  the GM's own ``gm_character`` was dropped from the awareness
           sidebar (the ``continue`` assumed the player-only own-row block).
           drawSidebar must list the GM's own entity in addition to the rest.
* BUG-007  ``entityAtCell`` only searched ``state.entities`` (empty for a
           player) so clicking the player's own token never hit. It must now
           find the token via ``state.youEntity``.
* BUG-008  a deliberate ``ws.close()`` fired ``onclose`` which armed a stray
           reconnect (double socket). The intentional close must NOT schedule
           a reconnect, while an unexpected close still does.
* BUG-009  the upload file picker advertised formats the decoder cannot read.
           The real index.html must now restrict ``accept`` to ``.png,.bmp``.
* BUG-010  "New entity" offered kind ``player`` which the server rejects. The
           real index.html must not offer a ``player`` kind option.
* BUG-011  a join rejection (e.g. "session full") went to a hidden toast.
           onError, while not yet joined, must surface the message on the
           lobby status slot instead.

Generated maps (generated-maps spec §6 / C12 — frontend):
* ``index.html`` carries the new source-tab bar + generate form ids
  (``#map-source-tabs``, ``#tab-upload``, ``#tab-generate``, ``#gen-form``,
  ``#gen-name``, ``#gen-cols``/``#gen-rows`` with min="8" max="60",
  ``#gen-seed``, ``#btn-generate``, ``#pane-source``, ``#preview-title``)
  AND every pre-existing upload id is still present (regression guard).
* The real app.js under the stub DOM: booting doesn't throw;
  ``setSourceTab("generate")`` hides ``#upload-form``, shows ``#gen-form``
  and sets ``state.uploadSource === "generate"`` (and is a no-op while the
  preview is up); ``syncGenerateButton`` gates ``#btn-generate`` on a
  non-empty name + integer 8–60 size; ``generateMap()`` driven end-to-end
  against the harness' recorded fetch stub (body, success preview branch,
  "Generated…" copy, source pane hidden) and its error path (toast + busy
  cleared, no crash).

Awareness tier rendering (player three-tier model, §5):
* the canvas must render the three states: FULL contacts (line of sight)
  as a colored token WITH a name label + colorblind shape marker (players
  now see labels, reusing the GM label rendering); APPROXIMATE contacts
  (no line of sight, within 4 squares) as a faint gray "?" circle at the
  CENTER of the reported 2×2 block (no identity drawn); ABSENT contacts
  render nothing (the item never arrives).
* the sidebar must list approximate contacts as "Unknown" rows with a
  muted dot-approx chip and an "unseen" count in the summary.
* the legend (index.html) documents all three states.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPJS = os.path.join(ROOT, "app", "static", "app.js")
INDEX = os.path.join(ROOT, "app", "static", "index.html")
HARNESS = os.path.join(ROOT, "tests", "js", "harness.js")


def _node() -> str | None:
    return shutil.which("node")


def js(expr: str) -> str:
    """Load the app (harness re-exports it as ``api``) and evaluate the JS
    expression ``expr``, returning the result as a JSON string.

    The expression is passed via an environment variable so no shell/JS
    quote-escaping is involved. The result may be a thenable (e.g. the
    promise from ``generateMap()``) — the node program awaits it; sync
    results pass through ``Promise.resolve`` unchanged.
    """
    program = (
        'const {buildApi}=require(process.env.HARNESS);\n'
        'const api=buildApi();\n'
        'const out=eval(process.env.EXPR);\n'
        'Promise.resolve(out).then(o=>{process.stdout.write(JSON.stringify(o));});\n'
    )
    env = dict(os.environ)
    env["APPJS_PATH"] = APPJS
    env["HARNESS"] = HARNESS
    # P1 join-blocking regression: the real index.html goes to the harness
    # so the REAL .door-swatch legend chips are attached to #legend at
    # boot (see TestLobbyBootRegression and the P1 comment in
    # tests/js/harness.js — that is what makes renderLegendDoorSwatches'
    # loop body, and the pre-fix TDZ read of `T.floor`, actually execute).
    env["INDEX_HTML_PATH"] = INDEX
    env["EXPR"] = expr
    env["NODE_OPTIONS"] = ""
    proc = subprocess.run(
        [_node(), "-e", program],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node exited {proc.returncode}\n{proc.stderr}")
    return proc.stdout


def js_map_literal(mapobj: dict) -> str:
    """A JS object literal for the small test maps (dict of str/list)."""
    return json.dumps(mapobj)


class FrontendBase(unittest.TestCase):
    """Skip the whole class if Node isn't available (pure-stdlib project)."""

    @classmethod
    def setUpClass(cls):
        if _node() is None:
            raise unittest.SkipTest("Node.js not found on PATH; skipping JS regression tests")


class TestBug001AllEntitiesDefined(FrontendBase):
    def _welcome(self, role: str) -> None:
        """Drive the real onWelcome (welcome->applyState->render) and assert
        it does not throw. This is the exact path BUG-001 crashed on."""
        mapobj = (
            '{"name":"m","width":6,"height":4,'
            '"cells":[["floor","floor","floor","floor","floor","floor"]'
            ",[\"floor\",\"floor\",\"floor\",\"floor\",\"floor\",\"floor\"]"
            ",[\"floor\",\"floor\",\"wall\",\"wall\",\"floor\",\"floor\"]"
            ",[\"floor\",\"floor\",\"floor\",\"floor\",\"floor\",\"floor\"]]}"
        )
        if role == "gm":
            # New contract: the GM welcome carries you.entity_id = null and
            # an empty entities list (the GM is a pure controller).
            expr = (
                "(()=>{api.onWelcome({type:'welcome',"
                "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
                "map:%s,entities:[],players:[],"
                "awareness:[],fog:false});"
                "return {joined:api.state.joined,role:api.state.role,"
                "entities:api.allEntities().length};})()"
            ) % mapobj
        else:
            expr = (
                "(()=>{api.onWelcome({type:'welcome',"
                "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
                "map:%s,entities:[],you_entity:{id:'e2',name:'Alice',"
                "kind:'player',team:'party',x:2,y:1},players:[],"
                "awareness:[{entity_id:'e1',x:1,y:1,color:'white'}],"
                "fog:false});"
                "return {joined:api.state.joined,role:api.state.role,"
                "entities:api.allEntities().length};})()"
            ) % mapobj
        out = js(expr)
        self.assertIn("joined", out)
        self.assertIn("true", out)  # state.joined became true -> no throw

    def test_all_entities_is_defined_function(self):
        out = js("typeof api.allEntities")
        self.assertEqual(out, '"function"',
                         "allEntities must be defined (BUG-001)")

    def test_welcome_renders_for_gm(self):
        self._welcome("gm")

    def test_welcome_renders_for_player(self):
        self._welcome("player")


class TestBug002OpenMapInSession(FrontendBase):
    def test_open_uploaded_map_sends_use_map_same_session(self):
        expr = (
            "(()=>{api.state.joined=true;api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:'e1'};"
            "api.state.grid={width:4,height:4,cells:Array.from({length:4},"
            "()=>Array(4).fill('floor'))};"
            "api.state.uploadedMap={id:'crypt',name:'Crypt',width:4,height:4,"
            "cells:Array.from({length:4},()=>Array(4).fill('floor'))};"
            "api._send.reset();"
            "const before=api._send.wsObj;"
            "api.openUploadedMap();"
            "const use=api._send.sent.find(m=>m.type==='use_map');"
            "return {use:use||null, wsUnchanged:api._send.wsObj===before,"
            " newSockets:api._send.urls.length};})()"
        )
        out = js(expr)
        # use_map is sent with the uploaded map id, on the same session.
        self.assertIn('"use_map"', out)
        self.assertIn('"crypt"', out)
        # no ws.close + reconnect: the same socket object is still current and
        # no new WebSocket(url) was constructed -> players are never stranded.
        self.assertIn('"wsUnchanged":true', out)
        self.assertIn('"newSockets":0', out)


class TestBug003PathAnimation(FrontendBase):
    def test_token_walks_cell_by_cell_not_teleport(self):
        # Player owns youEntity at (2,1). Server sends path (2,1)->(5,1) then
        # the state snapshot (entity at final (5,1)). The token must be PINNED
        # to the start cell immediately after path+state (no teleport to 5,1),
        # then walk one cell per 120ms tick and land exactly on (5,1).
        expr = (
            "(()=>{const map={name:'m',width:8,height:2,cells:Array.from("
            "{length:2},()=>Array(8).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e2'},map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:2,y:1},players:[],awareness:[],fog:false});"
            "api.onPath({type:'path',entity_id:'e2',path:[{x:2,y:1},{x:3,y:1},"
            "{x:4,y:1},{x:5,y:1}]});"
            "api.onState({type:'state',map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:5,y:1},players:[],awareness:[],fog:false});"
            "const after=[api.state.youEntity.x,api.state.youEntity.y];"
            "const animating=api.isAnimating('e2');"
            "const trace=[];"
            "for(let k=0;k<6;k++){api._timer.advance(120);"
            "trace.push([api.state.youEntity.x,api.state.youEntity.y]);}"
            "const final=[api.state.youEntity.x,api.state.youEntity.y];"
            "return {after,animating,trace,final,"
            "animatingAfter:api.isAnimating('e2')};})()"
        )
        out = js(expr)
        # Immediately after path+state the token is still at the START cell
        # (2,1) — it did NOT teleport to the destination (5,1).
        self.assertIn('"after":[2,1]', out,
                      f"token teleported instead of walking: {out}")
        self.assertIn('"animating":true', out)
        # After the animation completes it lands exactly on the final cell
        # (5,1) and the animation is cleared.
        self.assertIn('"final":[5,1]', out,
                      f"token did not reach the goal: {out}")
        self.assertIn('"animatingAfter":false', out)

    def test_animate_only_mutates_the_live_entity_via_all_entities(self):
        # The animation must move the CURRENT entity object (the one
        # allEntities() returns), not a detached reference. After a step the
        # object found via allEntities() carries the intermediate position.
        expr = (
            "(()=>{const map={name:'m',width:8,height:2,cells:Array.from("
            "{length:2},()=>Array(8).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e2'},map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:2,y:1},players:[],awareness:[],fog:false});"
            "api.onPath({type:'path',entity_id:'e2',path:[{x:2,y:1},{x:3,y:1},"
            "{x:4,y:1},{x:5,y:1}]});"
            "api._timer.advance(120);"
            "const live=api.findEntity('e2');"
            "return {liveX:live.x,liveY:live.y,sameAsYouEntity:"
            "live===api.state.youEntity};})()"
        )
        out = js(expr)
        self.assertIn('"liveX":3', out, out)
        self.assertIn('"liveY":1', out, out)
        # The animated object is the same live object allEntities() exposes.
        self.assertIn('"sameAsYouEntity":true', out)

    def test_animation_gates_further_moves(self):
        expr = (
            "(()=>{const map={name:'m',width:8,height:2,cells:Array.from("
            "{length:2},()=>Array(8).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e2'},map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:2,y:1},players:[],awareness:[],fog:false});"
            "api.onPath({type:'path',entity_id:'e2',path:[{x:2,y:1},{x:3,y:1},"
            "{x:4,y:1}]});"
            "api._send.reset();"
            "api.sendMove('e2',0,0,false);"
            "const droppedDuring=api._send.sent.length;"
            "api.stopAnim('e2');"
            "api._send.reset();"
            "api.sendMove('e2',0,0,false);"
            "return {droppedDuring, sentAfter:[api._send.sent.map(m=>m.type)]};"
            "})()"
        )
        out = js(expr)
        # While animating, the move is gated (nothing sent)...
        self.assertIn('"droppedDuring":0', out, out)
        # ...and once the animation stops, the same move goes through.
        self.assertIn('"move"', out, out)


class TestAwarenessTiersPlayer(FrontendBase):
    """Player three-tier awareness rendering (the server decides the tiers
    and sends the items; the client must render the three states)."""

    def _player_state(self):
        return (
            "(()=>{const map={name:'m',width:16,height:12,cells:Array.from("
            "{length:12},()=>Array(16).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e2'},map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:"
            "[{entity_id:'e1',x:3,y:1,color:'green',name:'Bob',kind:'player',"
            "label:true},"
            "{entity_id:'<approx-1>',x:2,y:1,approximate:true,label:false}],"
            "fog:false});")

    def test_canvas_renders_full_token_with_label_and_gray_approx_question(self):
        # 16x12 grid, harness canvas 800x584 → the view auto-fits to the
        # smallest covering level (L4, 16x13) → cell 50, origin (0,0).
        # Under pan/zoom the single (s, ox, oy) origin is the contract (AC18):
        # the full-token center and the approx "?" block center must equal
        # (offset + (cell + 0.5)*cell) of their map coords. We read the LIVE
        # cell/offsets so the test holds at any view level, then assert
        # alignment and rule out the two classic misalignments.
        expr = (
            self._player_state()
            + "api.els.mapView.hidden=false;"
            + "api.renderAll();"
            + "const c=api.els.canvas.getContext('2d');"
            + "const s=api.state.cell, ox=api.state.offsetX, oy=api.state.offsetY;"
            + "return {arcs:c._arcs,texts:c._texts,s,ox,oy};})()"
        )
        out = js(expr)
        d = json.loads(out)
        s, ox, oy = d["s"], d["ox"], d["oy"]
        # arcs are [cx, cy, r] — compare (cx, cy) pairs only.
        centers = {(a[0], a[1]) for a in d["arcs"]}

        def at(cx, cy):
            return (round(cx, 2), round(cy, 2))

        # FULL token (3,1) → circle drawn at the EXACT cell center.
        self.assertIn(at(ox + (3 + 0.5) * s, oy + (1 + 0.5) * s), centers,
                      "full token center must equal offset + (cell+0.5)*cell")
        self.assertIn('"Bob"', out, out)          # FULL name label drawn
        self.assertIn('"B"', out)                 # identity letter drawn
        self.assertIn('"?"', out, out)            # approximate marker glyph
        # The "?" marker is at the 2x2 block CENTER — NOT at the block
        # ORIGIN cell center (a misalignment bug) and NOT on the block corner.
        self.assertIn(at(ox + (2 * 2 + 1) * s, oy + (1 * 2 + 1) * s), centers)
        self.assertNotIn(at(ox + (2 + 0.5) * s, oy + (1 + 0.5) * s), centers,
                         "approx marker must NOT sit on the origin cell")
        self.assertNotIn(at(ox + (2 * 2) * s, oy + (1 * 2) * s), centers,
                         "approx marker must NOT sit on the block corner")


    def test_approx_item_renders_no_identity(self):
        # An approximate item must NOT leak the entity's name/id anywhere in
        # the drawn output: only the full contact's text and the "?" glyph.
        expr = (
            self._player_state()
            + "api.els.mapView.hidden=false;"
            + "api.renderAll();"
            + "const c=api.els.canvas.getContext('2d');"
            + "return {texts:c._texts};})()"
        )
        out = js(expr)
        texts = json.loads(out)["texts"]
        self.assertIn("Bob", texts)        # the FULL contact IS named
        self.assertIn("?", texts)          # the approximate marker glyph
        self.assertNotIn("e1", texts)      # no entity id leak

    def test_canvas_renders_nothing_for_absent_contacts(self):
        # A player state with NO awareness items (everything out of sight)
        # must not draw any token/label/"?" beyond the own character.
        expr = (
            "(()=>{const map={name:'m',width:16,height:12,cells:Array.from("
            "{length:12},()=>Array(16).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e2'},map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "api.els.mapView.hidden=false;"
            "api.renderAll();"
            "const c=api.els.canvas.getContext('2d');"
            "return {arcs:c._arcs,texts:c._texts};})()"
        )
        out = js(expr)
        texts = json.loads(out)["texts"]
        self.assertIn("A", texts)    # own token letter only
        self.assertIn("YOU", texts)  # own label
        self.assertNotIn("?", texts)  # no approximate markers

    def test_player_sidebar_rows_for_full_and_approx(self):
        # The stub DOM's appendChild/innerHTML don't maintain live child
        # lists, so rows are captured at creation time (the established
        # harness pattern) and the span children are tracked per row.
        expr = (
            self._player_state()
            + "api.els.mapView.hidden=false;"
            + "const doc=api.document;const rows=[];const realCreate=doc.createElement;"
            + "doc.createElement=(t)=>{const el=realCreate(t);"
            + "if(t==='li'){const row={el,spans:[],texts:[]};rows.push(row);"
            + "el.appendChild=(c)=>{row.spans.push(c.className);"
            + "row.texts.push(c.textContent||'');return c};}"
            + "return el};"
            + "api.drawSidebar();"
            + "doc.createElement=realCreate;"
            + "return {rows:rows.map(r=>({cls:r.el.className,spans:r.spans,"
            + "texts:r.texts})),summary:api.els.awarenessSummary.textContent};})()"
        )
        out = js(expr)
        data = json.loads(out)
        rows = data["rows"]
        # Row 1: own character (YOU, blue-ringed dot).
        self.assertIn("is-own", rows[0]["cls"])
        self.assertIn("YOU", " ".join(rows[0]["texts"]))
        self.assertIn("dot-own", " ".join(rows[0]["spans"]))
        # Row 2: FULL contact — named, team-colored shape dot, exact coords.
        self.assertIn("dot-tri team-party", " ".join(rows[1]["spans"]))
        self.assertIn("Bob", " ".join(rows[1]["texts"]))
        self.assertIn("(3, 1)", " ".join(rows[1]["texts"]))
        # Row 3: approximate contact — muted "Unknown" chip, NO name,
        # block coordinates.
        self.assertIn("dot-approx", " ".join(rows[2]["spans"]))
        self.assertIn("Unknown", " ".join(rows[2]["texts"]))
        self.assertNotIn("Bob", " ".join(rows[2]["texts"]))
        self.assertIn("(2, 1)", " ".join(rows[2]["texts"]))
        # Summary counts the 1 ally + the 1 unseen approximate contact.
        self.assertIn("1 ally", data["summary"])
        self.assertIn("1 unseen", data["summary"])


class TestBug006GmRosterInSidebar(FrontendBase):
    def test_gm_sidebar_lists_all_tokens_no_own_row(self):
        # The GM is a pure controller: it has no own row — the sidebar lists
        # every token that exists (the original BUG-006 concern: the list
        # must agree with the awareness it renders; every awareness row is
        # rendered exactly once, and the summary counts exactly those rows).
        expr = (
            "(()=>{api.state.role='gm';api.state.name='Gamer';"
            "api.state.entities=[{id:'e2',name:'Alice',kind:'player',"
            "team:'party',x:2,y:1,owner:'p2'}];"
            "api.state.you={id:'p1',name:'Gamer',role:'gm',entity_id:null};"
            "api.state.awareness=[{entity_id:'e2',x:2,y:1,color:'green',"
            "name:'Alice',kind:'player',label:true}];"
            "const kids=[];"
            "api.els.awarenessList.innerHTML='';"
            "api.els.awarenessList.appendChild=(c)=>{kids.push(c);return c};"
            "api.drawSidebar();"
            "const ids=kids.map(k=>k.dataset.entityId).filter(Boolean);"
            "return {ids,summary:api.els.awarenessSummary.textContent};})()"
        )
        out = js(expr)
        # The roster lists exactly the real tokens (e2) — no own row.
        self.assertIn('"ids":["e2"]', out, out)
        # The summary counts exactly the rows rendered (1 ally, 0 else).
        self.assertIn("1 ally", out)
        self.assertIn("0 neutral", out)
        self.assertIn("0 enemy", out)

    def test_gm_sidebar_zero_tokens_empty_row_and_zero_summary(self):
        # A12 (sidebar part): GM alone, no tokens → empty-state row with the
        # GM copy and a 0·0·0 summary.
        expr = (
            "(()=>{api.state.role='gm';api.state.name='Gamer';"
            "api.state.entities=[];"
            "api.state.you={id:'p1',name:'Gamer',role:'gm',entity_id:null};"
            "api.state.awareness=[];"
            "const kids=[];"
            "api.els.awarenessList.innerHTML='';"
            "api.els.awarenessList.appendChild=(c)=>{kids.push(c);return c};"
            "api.drawSidebar();"
            "const row=kids[0];"
            "return {text:row?row.textContent:null,"
            "cls:row?row.className:'',"
            "summary:api.els.awarenessSummary.textContent};})()"
        )
        out = js(expr)
        self.assertIn("No tokens on the map yet — add the first one in GM Tools.",
                      out)
        self.assertIn("muted", out)
        self.assertIn("small", out)
        self.assertIn("0 ally", out)
        self.assertIn("0 neutral", out)
        self.assertIn("0 enemy", out)


class TestBug007EntityAtCell(FrontendBase):
    def test_player_own_token_is_found(self):
        expr = (
            "(()=>{const map={name:'m',width:6,height:4,cells:Array.from("
            "{length:4},()=>Array(6).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e2'},map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:2,y:1},players:[],awareness:[],fog:false});"
            "const hit=api.entityAtCell(2,1);"
            "const miss=api.entityAtCell(5,3);"
            "return {hit:hit&&hit.id,miss:miss};})()"
        )
        out = js(expr)
        # Clicking the player's own token cell returns that token (so the
        # "re-assert selection" branch can fire), other cells return null.
        self.assertIn('"hit":"e2"', out, out)
        self.assertIn('"miss":null', out)


class TestBug008IntentionalCloseNoReconnect(FrontendBase):
    def test_intentional_close_does_not_reconnect(self):
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:'e1'};"
            "api.state.grid={width:4,height:4,cells:Array.from({length:4},"
            "()=>Array(4).fill('floor'))};"
            "api.state.uploadedMap={id:'crypt',name:'Crypt',width:4,height:4,"
            "cells:Array.from({length:4},()=>Array(4).fill('floor'))};"
            "api._send.reset();"
            "api.openUploadedMap();"
            "return {pending:api._timer.pending(),sockets:api._send.urls.length};"
            "})()"
        )
        out = js(expr)
        # No stray reconnect timer is armed and no second socket is opened.
        self.assertIn('"pending":0', out, out)
        self.assertIn('"sockets":0', out)

    def test_unexpected_close_still_reconnects(self):
        expr = (
            "(()=>{api.connectWs();"
            "api.state.joined=true;"
            "const sock=api._send.wsObj;"
            "sock.onclose();"
            "return {pending:api._timer.pending()};})()"
        )
        out = js(expr)
        # A genuine (non-deliberate) drop schedules exactly one reconnect.
        self.assertIn('"pending":1', out, out)

    def test_new_connection_supersedes_pending_reconnect(self):
        # Hardening: if a reconnect is already pending and a NEW connection is
        # opened, connectWs() must clear the stray timer so there is never a
        # second (leaked) socket racing the reconnect.
        expr = (
            "(()=>{api.state.joined=true;"
            "const before=api._send.urls.length;"
            "api.connectWs();"
            "const sock=api._send.wsObj;"
            "sock.onclose();"
            "const armed=api._timer.pending();"
            "api.connectWs();"
            "return {armed,after:api._timer.pending(),"
            "sockets:api._send.urls.length-before};})()"
        )
        out = js(expr)
        self.assertIn('"armed":1', out, out)
        # After the second connectWs() the pending reconnect timer is cleared
        # (no stray timer -> no second socket).
        self.assertIn('"after":0', out, out)


class TestBug011JoinRejectionVisible(FrontendBase):
    def test_error_before_join_routes_to_lobby_status(self):
        expr = (
            "(()=>{api.state.joined=false;"
            "api.onError({type:'error',message:'session full'});"
            "return {lobby:api.els.lobbyStatus.textContent, joined:"
            "api.state.joined};})()"
        )
        out = js(expr)
        # The rejection is surfaced on the (visible) lobby status slot, not
        # only in the hidden map-view toasts.
        self.assertIn('"lobby":"session full"', out, out)
        self.assertIn('"joined":false', out)

    def test_join_error_cleared_on_welcome(self):
        expr = (
            "(()=>{api.state.joined=false;"
            "api.onError({type:'error',message:'session full'});"
            "const map={name:'m',width:4,height:4,cells:Array.from({length:4},"
            "()=>Array(4).fill('floor'))};"
            "api.onWelcome({type:'welcome',you:{id:'p1',name:'G',role:'gm',"
            "entity_id:null},map,entities:[],players:[],"
            "awareness:[],fog:false});"
            "return {lobby:api.els.lobbyStatus.textContent, joined:"
            "api.state.joined};})()"
        )
        out = js(expr)
        # Once a welcome arrives the stale join error is cleared.
        self.assertIn('"lobby":""', out, out)
        self.assertIn('"joined":true', out)


class TestLobbyBootRegression(FrontendBase):
    """P1 regression: the door-iconography change added a load-time call to
    ``renderLegendDoorSwatches()`` while that function reads ``T.floor``
    — and the ``const T`` token table was declared AFTER the call. In a
    real browser, reading a ``const`` before its declaration throws a TDZ
    ``ReferenceError`` ("Cannot access 'T' before initialization") that
    aborts app.js at load: the Join-button listeners wired at the END of
    the file never attach, ``syncLobbyButtons`` never runs, and
    ``#join-gm``/``#join-player`` keep their initial ``disabled`` state
    forever — users cannot join.

    Why the previous 134 tests all missed it: the stub DOM's
    ``querySelectorAll()`` used to return ``[]`` always, so the swatch
    loop body never executed and the TDZ read never happened. This class
    forces ``harness.js`` to attach the REAL ``.door-swatch`` chips
    parsed from the ACTUAL ``index.html`` to ``#legend``, so the loop
    body really executes at boot. That is the team's substitute for the
    real-browser qa_ui_smoke-style check (no headless browser exists in
    this environment — the team's own qa_ui_smoke probe is a Node
    harness probe of the same kind): with the chips present, the old
    code makes ``buildApi()`` itself throw the TDZ ReferenceError
    (node exits non-zero → ``js()`` raises AssertionError), while the
    fixed code boots clean. The test set additionally proves the join
    flow the TDZ crash orphaned: listeners attached, name typing enables
    BOTH buttons, the click sends the join intent.
    """

    def test_boot_completes_with_real_swatch_chips(self):
        # buildApi() evaluates the REAL app.js end-to-end. With the six
        # real index.html .door-swatch chips attached to #legend this is
        # the same statement sequence a real browser executes at load.
        # Old code: TDZ ReferenceError inside the eval -> node exits
        # non-zero -> AssertionError (this test FAILS on the old code).
        # Fixed code: boot completes, both Join buttons still disabled
        # (correct — the name field is empty) and the socket was opened.
        out = js(
            "({boot:true,"
            "gmDisabled:api.els.joinGm.disabled,"
            "playerDisabled:api.els.joinPlayer.disabled,"
            "wsConnected:api._send.wsObj!==null})"
        )
        self.assertIn('"boot":true', out)
        self.assertIn('"gmDisabled":true', out)
        self.assertIn('"playerDisabled":true', out)
        self.assertIn('"wsConnected":true', out)

    def test_typing_a_name_enables_both_join_buttons(self):
        # The exact user journey that was broken: type a name into
        # #join-name and BOTH "Join as GM" / "Join as Player" become
        # clickable (input listener -> syncLobbyButtons). The old code's
        # TDZ crash left the listener unattached, so both stayed
        # disabled.
        out = js(
            "(()=>{api.els.joinName.value='Zed';"
            "api.els.joinName.dispatchEvent({type:'input'});"
            "return {gm:api.els.joinGm.disabled,"
            "player:api.els.joinPlayer.disabled};})()"
        )
        self.assertIn('"gm":false', out, out)
        self.assertIn('"player":false', out, out)

    def test_join_button_clicks_send_the_join_intent(self):
        # The click listeners must be attached and must send the join
        # intent over the live socket (harness records every frame).
        out = js(
            "(()=>{api.els.joinName.value='Zed';"
            "api.els.joinName.dispatchEvent({type:'input'});"
            "api.els.joinGm.dispatchEvent({type:'click'});"
            "api.els.joinPlayer.dispatchEvent({type:'click'});"
            "return api._send.sent;})()"
        )
        self.assertIn('{"type":"join","name":"Zed","role":"gm"}', out, out)
        self.assertIn('{"type":"join","name":"Zed","role":"player"}', out, out)

    def test_blank_name_keeps_both_join_buttons_disabled(self):
        # syncLobbyButtons trims: a whitespace-only name keeps both
        # buttons disabled (the existing behavior the TDZ crash orphaned).
        out = js(
            "(()=>{api.els.joinName.value='Zed';"
            "api.els.joinName.dispatchEvent({type:'input'});"
            "api.els.joinName.value='   ';"
            "api.els.joinName.dispatchEvent({type:'input'});"
            "return {gm:api.els.joinGm.disabled,"
            "player:api.els.joinPlayer.disabled};})()"
        )
        self.assertIn('"gm":true', out, out)
        self.assertIn('"player":true', out, out)

    def test_legend_six_swatches_still_render_in_map_view(self):
        # The fix must not have broken the legend itself: entering the
        # map view (the production call site, showView("map")) renders
        # all six 16x16 swatch canvases on the real index.html chips.
        out = js(
            "(()=>{api.showView('map');"
            "const legend=api.document.querySelector('#legend');"
            "return legend.querySelectorAll('.door-swatch').length"
            "===6 && legend.querySelectorAll('.door-swatch').every"
            "(el=>el.children.length===1 && el.children[0].width===16"
            "&& el.children[0].height===16);})()"
        )
        self.assertIn("true", out, out)


class TestGmControllerView(FrontendBase):
    """Acceptance for "GM is a pure controller" (docs/design/gm-controller.md
    §8, A12–A15/A19): a GM welcome with you.entity_id = null and an empty
    roster renders the controller UI end-to-end.

    The toast assertions capture the span textContent right after creation
    (toasts are only removed by a later timer tick, so the harness clock
    keeps them alive)."""

    MAP_JS = js_map_literal({
        "name": "m", "width": 6, "height": 4,
        "cells": [["floor"] * 6 for _ in range(4)],
    })

    _GM_WELCOME_HEAD = (
        "(()=>{"
        "const doc=api.document;const toasts=[];"
        "const realCreate=doc.createElement;"
        "doc.createElement=(t)=>{const el=realCreate(t);"
        "if(t==='span')toasts.push(()=>el.textContent);return el};"
        "api.onWelcome({type:'welcome',"
        "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
        f"map:{MAP_JS},entities:[],players:[],awareness:[],"
        "fog:false});"
        "doc.createElement=realCreate;"
    )

    def _toasts(self, expr_after: str = "") -> str:
        expr = self._GM_WELCOME_HEAD + expr_after + \
            "return {toasts:toasts.map(f=>f())};})()"
        return js(expr)

    def test_a12_gm_welcome_zero_tokens_controller_ui(self):
        expr = (
            "(()=>{"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            f"map:{self.MAP_JS},entities:[],players:[],awareness:[],"
            "fog:false});"
            "return {title:api.els.awarenessTitle.textContent,"
            "sel:api.els.selEntityName.textContent,"
            "hint:api.els.controlHint.textContent,"
            "canvasHint:api.els.canvasHint.textContent,"
            "canvasHintHidden:api.els.canvasHint.hidden,"
            "fogEnabled:!api.els.fogToggle.disabled,"
            "fogTitle:api.els.fogToggle.title,"
            "teamDisabled:api.els.teamSelect.disabled,"
            "deleteDisabled:api.els.btnDeleteEntity.disabled,"
            "entities:api.allEntities().length};})()"
        )
        out = js(expr)
        # A12: controller sidebar title, None selection, 0-token hint.
        # (you.entity_id is null, so isOwn is false for every token — no
        # own ring / "YOU" pill can render.)
        self.assertIn('"title":"Tokens — all (GM sees all)"', out, out)
        self.assertIn('"sel":"None"', out)
        self.assertIn("No tokens yet — add one in GM Tools.", out)
        # §3.2 first-run canvas hint is up for a fresh session (5 s window).
        self.assertIn("You're the GM — no token of your own.", out)
        self.assertIn('"canvasHintHidden":false', out)
        # A14: the fog toggle stays ENABLED for the GM, controller tooltip.
        self.assertIn('"fogEnabled":true', out)
        self.assertIn("Toggle fog of war for players. As GM you always see "
                      "everything.", out)
        # No selection → team/delete disabled; zero tokens.
        self.assertIn('"teamDisabled":true', out)
        self.assertIn('"deleteDisabled":true', out)
        self.assertIn('"entities":0', out)

    def test_a12_empty_row_and_zero_summary(self):
        expr = (
            "(()=>{"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            f"map:{self.MAP_JS},entities:[],players:[],awareness:[],"
            "fog:false});"
            "const kids=[];"
            "api.els.awarenessList.appendChild="
            "(c)=>{kids.push(c);return c};"
            "api.drawSidebar();"
            "return {row:kids[0]?kids[0].textContent:null,"
            "summary:api.els.awarenessSummary.textContent};})()"
        )
        out = js(expr)
        self.assertIn(
            "No tokens on the map yet — add the first one in GM Tools.", out)
        self.assertIn("0 ally · 0 neutral · 0 enemy", out)

    def test_a15_gm_welcome_toast_controller_copy(self):
        out = self._toasts()
        # A15: the GM welcome toast carries the no-token controller sentence.
        self.assertIn("you're the GM", out)
        self.assertIn("no token on the map", out)
        self.assertIn("create and move tokens for everyone", out)

    def test_a15_player_welcome_toast_unchanged(self):
        # Toast spans are captured in creation order: the own-row spans
        # ("YOU", "(1, 1)") are made by the render pass, then the toast span
        # (whose textContent is set directly by toast()).
        expr = (
            "(()=>{"
            "const doc=api.document;const toasts=[];"
            "const realCreate=doc.createElement;"
            "doc.createElement=(t)=>{const el=realCreate(t);"
            "if(t==='span')toasts.push(()=>el.textContent);return el};"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e1'},"
            f"map:{self.MAP_JS},entities:[],"
            "you_entity:{id:'e1',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "doc.createElement=realCreate;"
            "return {toasts:toasts.map(f=>f()),"
            "sel:api.state.selectedEntityId};})()"
        )
        out = js(expr)
        # Two own-row render passes (applyState, then selectEntity) each
        # create [dot "", "YOU", "(1, 1)"] spans, then the toast span —
        # byte-identical to the pre-change player toast; the player keeps
        # their own-token selection.
        self.assertIn(
            '"toasts":["","YOU","(1, 1)","","YOU","(1, 1)",' +
            '"Welcome, Alice."]', out, out)
        self.assertIn('"sel":"e1"', out)

    def test_a13_created_token_selected_row_and_summary(self):
        expr = (
            "(()=>{"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            f"map:{self.MAP_JS},entities:[],players:[],awareness:[],"
            "fog:false});"
            "api.els.newEntityName.value='Grom';"
            "api.els.newEntityKind.value='npc';"
            "api.els.newEntityTeam.value='neutral';"
            "api._send.reset();"
            "api.createEntity();"
            "const create=api._send.sent.find(m=>m.type==='create_entity');"
            "const kids=[];"
            "api.els.awarenessList.appendChild="
            "(c)=>{kids.push(c);return c};"
            "const doc=api.document;const spans=[];"
            "const realCreate=doc.createElement;"
            "doc.createElement=(t)=>{const el=realCreate(t);"
            "if(t==='span')spans.push(()=>el.textContent);return el};"
            "api.onState({type:'state',"
            f"map:{self.MAP_JS},"
            "entities:[{id:'e5',name:'Grom',kind:'npc',team:'neutral',"
            "x:1,y:1,owner:null}],players:[],"
            "awareness:[{entity_id:'e5',x:1,y:1,color:'white',name:'Grom',"
            "kind:'npc',label:true}],fog:false});"
            "doc.createElement=realCreate;"
            "return {create:!!create,kind:create?create.kind:null,"
            "sel:api.state.selectedEntityId,"
            "selName:api.els.selEntityName.textContent,"
            "team:api.els.teamSelect.value,"
            "rows:kids.map(k=>k.dataset.entityId).filter(Boolean),"
            "rowTexts:spans.map(f=>f()),"
            "summary:api.els.awarenessSummary.textContent,"
            "canvasHintHidden:api.els.canvasHint.hidden,"
            "hint:api.els.controlHint.textContent,"
            "nameCleared:api.els.newEntityName.value};})()"
        )
        out = js(expr)
        # A13: create went out as npc/neutral; the state broadcast
        # auto-selected the new token; the row carries name + kind·team meta +
        # coords; the summary follows; the first-run hint is gone; the name
        # input cleared.
        self.assertIn('"create":true', out, out)
        self.assertIn('"kind":"npc"', out)
        self.assertIn('"sel":"e5"', out)
        self.assertIn('"selName":"Grom (npc)"', out)
        self.assertIn('"team":"neutral"', out)
        self.assertIn('"rows":["e5"]', out)
        self.assertIn("\"Grom\"", out)
        self.assertIn("npc·neutral", out)
        self.assertIn("(1, 1)", out)
        self.assertIn("0 ally · 1 neutral · 0 enemy", out)
        self.assertIn('"canvasHintHidden":true', out)
        self.assertIn("Pick a destination for Grom", out)
        self.assertIn('"nameCleared":""', out)

    def test_a14_fog_toggle_gm_send_and_no_rendered_change(self):
        expr = (
            "(()=>{"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            f"map:{self.MAP_JS},"
            "entities:[{id:'e1',name:'Grom',kind:'npc',team:'neutral',"
            "x:1,y:1,owner:null}],players:[],"
            "awareness:[{entity_id:'e1',x:1,y:1,color:'white',name:'Grom',"
            "kind:'npc',label:true}],fog:false});"
            "const before=JSON.stringify(api.state.awareness);"
            "api._send.reset();"
            "api.els.fogToggle.checked=true;"
            "api.toggleFog();"
            "const sent=api._send.sent[0]||null;"
            "api.onState({type:'state',"
            f"map:{self.MAP_JS},"
            "entities:[{id:'e1',name:'Grom',kind:'npc',team:'neutral',"
            "x:1,y:1,owner:null}],players:[],"
            "awareness:[{entity_id:'e1',x:1,y:1,color:'white',name:'Grom',"
            "kind:'npc',label:true}],fog:true});"
            "const after=JSON.stringify(api.state.awareness);"
            "return {sent,checked:api.els.fogToggle.checked,"
            "disabled:api.els.fogToggle.disabled,same:before===after};})()"
        )
        out = js(expr)
        # A14: the GM toggle sends {type:"set_fog", on:true}; the state
        # broadcast drives the checkbox; the GM's rendered awareness items
        # are identical before/after (same items, same pixels).
        self.assertIn('"sent":{"type":"set_fog","on":true}', out, out)
        self.assertIn('"checked":true', out)
        self.assertIn('"disabled":false', out)
        self.assertIn('"same":true', out)

    def test_a19_player_empty_state_when_only_other_was_gm(self):
        expr = (
            "(()=>{"
            "api.onWelcome({type:'welcome',you:{id:'p2',name:'Alice',"
            "role:'player',entity_id:'e1'},"
            f"map:{self.MAP_JS},entities:[],"
            "you_entity:{id:'e1',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "const kids=[];"
            "api.els.awarenessList.appendChild="
            "(c)=>{kids.push(c);return c};"
            "api.drawSidebar();"
            "return {rows:kids.map(k=>({t:k.textContent,"
            "cls:k.className})),"
            "summary:api.els.awarenessSummary.textContent};})()"
        )
        out = js(expr)
        # A19: a GM-only session + 1 player → the radar is empty (the GM
        # has no token to show), so the player gets the empty-state row
        # below their own row.
        self.assertIn("No one else is out there yet.", out, out)
        self.assertIn("0 ally · 0 neutral · 0 enemy", out)


@unittest.skipUnless(shutil.which("node") is not None,
                     "Node.js not found; skipping HTML static checks")
class TestIndexHtml(FrontendBase):
    def setUp(self):
        with open(INDEX, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_bug009_file_picker_only_png_bmp(self):
        # The picker must NOT advertise formats the stdlib decoder can't read
        # (jpg/jpeg/webp), and must offer the supported ones.
        self.assertIn('accept=".png,.bmp"', self.html)
        for ext in (".jpg", ".jpeg", ".webp"):
            self.assertNotIn(ext, self.html,
                             f"file picker still advertises {ext} (BUG-009)")

    def test_kind_options_are_exactly_npc_and_enemy(self):
        # The GM is a pure controller: the kind dropdown offers exactly
        # npc | enemy (npc default). No "player" (server-only, BUG-010) and
        # no "gm_character" (deprecated, never creatable).
        import re
        m = re.search(r'<select id="new-entity-kind">.*?</select>',
                      self.html, re.DOTALL)
        self.assertIsNotNone(m, "could not find #new-entity-kind")
        block = m.group(0)
        options = re.findall(r'value="([^"]+)"', block)
        self.assertEqual(options[:2], ["npc", "enemy"])
        self.assertNotIn('value="player"', block,
                         "BUG-010: 'player' kind still offered")
        self.assertNotIn('gm_character', block,
                         "'gm_character' kind must not be offered")
        self.assertIn('<option value="npc">', block)
        self.assertIn('<option value="enemy">', block)

    def test_lobby_note_mentions_gm_has_no_token(self):
        # A16: the lobby note sets the controller expectation.
        import re
        m = re.search(r'<p id="lobby-note">(.*?)</p>', self.html, re.DOTALL)
        self.assertIsNotNone(m, "could not find #lobby-note")
        note = re.sub(r"\s+", " ", m.group(1))
        self.assertIn("The GM has no token on the map", note)
        self.assertIn("creates and controls", note)

    def test_fog_toggle_has_player_tooltip_in_html(self):
        # The player-facing default title is in the markup; the GM title is
        # applied per role by applyState (checked in TestGmControllerView).
        self.assertIn('title="GM controls fog of war"', self.html)

    def test_legend_documents_approximate_and_hidden_contacts(self):
        # The canvas legend must document the three player visibility
        # states: full (labeled) contacts, the gray "?" approximate contact
        # (within 4 squares, sight blocked), and that anything beyond 4
        # squares or blocked farther away is hidden.
        self.assertIn('dot-approx', self.html)
        self.assertIn("unseen contact", self.html)
        import re
        m = re.search(r'<div id="legend">(.*?)</div>', self.html, re.DOTALL)
        self.assertIsNotNone(m, "could not find #legend")
        legend = re.sub(r"\s+", " ", m.group(1)).lower()
        self.assertIn("4 square", legend)
        self.assertIn("hidden", legend)


@unittest.skipUnless(shutil.which("node") is not None,
                     "Node.js not found; skipping HTML static checks")
class TestIndexHtmlGeneratedMaps(FrontendBase):
    """C12 (static half): the generate UI ids exist in the real index.html,
    the generate number inputs are range-bounded, and every PRE-EXISTING
    upload-view id is still present (regression guard)."""

    def setUp(self):
        with open(INDEX, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_new_generate_ids_present(self):
        # ids the C12 acceptance pins, each checked in context
        for attr in (
            'id="map-source-tabs"',
            'id="tab-upload"',
            'id="tab-generate"',
            'id="gen-form"',
            'id="gen-name"',
            'id="gen-cols"',
            'id="gen-rows"',
            'id="gen-seed"',
            'id="btn-generate"',
            'id="pane-source"',
            'id="preview-title"',
            'id="pane-grid-title"',
            'id="preview-note"',
            'id="gen-note"',
        ):
            self.assertIn(attr, self.html, f"missing {attr}")

    def test_gen_number_inputs_bounded_8_60(self):
        # The server hard-validates cols/rows as integers in 8-60; the
        # inputs must advertise the same range.
        for attr in ('id="gen-cols"', 'id="gen-rows"'):
            i = self.html.index(attr)
            block = self.html[i:i + 160]
            self.assertIn('type="number"', block, attr)
            self.assertIn('min="8"', block, attr)
            self.assertIn('max="60"', block, attr)
            self.assertIn('step="1"', block, attr)

    def test_generate_defaults(self):
        i = self.html.index('id="gen-cols"')
        self.assertIn('value="24"', self.html[i:i + 160])
        i = self.html.index('id="gen-rows"')
        self.assertIn('value="16"', self.html[i:i + 160])
        i = self.html.index('id="gen-seed"')
        self.assertIn('placeholder="random"', self.html[i:i + 160])
        i = self.html.index('id="gen-name"')
        self.assertIn('maxlength="40"', self.html[i:i + 160])
        self.assertIn('placeholder="The Deep Warrens"', self.html[i:i + 200])

    def test_tab_buttons_and_default_active(self):
        i = self.html.index('id="tab-upload"')
        self.assertIn("Upload map", self.html[i:i + 200])
        self.assertIn("is-active", self.html[i:i + 200])   # default tab
        i = self.html.index('id="tab-generate"')
        self.assertIn("Generate map", self.html[i:i + 200])

    def test_generate_button_starts_disabled(self):
        i = self.html.index('id="btn-generate"')
        self.assertIn("disabled", self.html[i:i + 200])

    # Regression guard: every id the pre-existing upload flow + tests rely
    # on must still be present and unchanged.
    def test_preexisting_upload_ids_still_present(self):
        for attr in (
            'id="upload-view"', 'id="upload-form"', 'id="upload-name"',
            'id="upload-file"', 'id="upload-file-name"', 'id="upload-cols"',
            'id="upload-rows"', 'id="dark-is-wall"', 'id="dark-is-wall-wrap"',
            'id="upload-preview"', 'id="upload-note"', 'id="btn-detect"',
            'id="btn-start-map"', 'id="btn-back"', 'id="btn-back-top"',
            'id="preview-image"', 'id="preview-canvas"',
            'id="preview-thumbnail"', 'id="new-entity-kind"',
            'id="join-name"', 'id="join-gm"', 'id="join-player"',
        ):
            self.assertIn(attr, self.html, f"regression: {attr} missing")


class TestGeneratedMapsFrontend(FrontendBase):
    """C12 (JS half): the real app.js under the stub DOM. Booting must not
    throw; the source tabs switch forms + state; generate is gated; and
    generateMap() runs end-to-end against the harness' recorded fetch stub."""

    def test_boot_with_stubbed_dom_does_not_throw(self):
        # buildApi() evals the real app.js (including all the new
        # generate-form listeners); reaching here means boot is clean.
        out = js(
            "({state:typeof api.state,src:api.state.uploadSource,"
            "timers:api._timer.pending()})"
        )
        self.assertIn('"state":"object"', out)
        self.assertIn('"src":"upload"', out)   # default source is upload

    def test_set_source_tab_generate(self):
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='idle';"
            "api.els.genCols.value='24';"
            "api.els.genRows.value='16';"
            "api.setSourceTab('generate');"
            "return {"
            "uploadFormHidden:api.els.uploadForm.hidden,"
            "genFormHidden:api.els.genForm.hidden,"
            "uploadSource:api.state.uploadSource,"
            "genActive:api.els.tabGenerate.classList._s.has('is-active'),"
            "uploadActive:api.els.tabUpload.classList._s.has('is-active'),"
            "btnDisabled:api.els.btnGenerate.disabled};})()"
        )
        out = js(expr)
        # Upload form hidden, generate form shown, state updated, active tab
        # styled; button disabled because #gen-name is empty.
        self.assertIn('"uploadFormHidden":true', out, out)
        self.assertIn('"genFormHidden":false', out, out)
        self.assertIn('"uploadSource":"generate"', out, out)
        self.assertIn('"genActive":true', out)
        self.assertIn('"uploadActive":false', out)
        self.assertIn('"btnDisabled":true', out)

    def test_set_source_tab_back_to_upload(self):
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='idle';"
            "api.setSourceTab('generate');"
            "api.setSourceTab('upload');"
            "return {"
            "uploadFormHidden:api.els.uploadForm.hidden,"
            "genFormHidden:api.els.genForm.hidden,"
            "uploadSource:api.state.uploadSource};})()"
        )
        out = js(expr)
        self.assertIn('"uploadFormHidden":false', out, out)
        self.assertIn('"genFormHidden":true', out, out)
        self.assertIn('"uploadSource":"upload"', out, out)

    def test_tabs_locked_during_preview(self):
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='preview';"
            "api.syncTabStyles();"
            "api.setSourceTab('generate');"
            "return {"
            "uploadSource:api.state.uploadSource,"
            "genFormHidden:api.els.genForm.hidden,"
            "tabsDisabled:api.els.tabUpload.disabled&&"
            "api.els.tabGenerate.disabled};})()"
        )
        out = js(expr)
        # Locked in preview: state unchanged, generate form still hidden.
        self.assertIn('"uploadSource":"upload"', out, out)
        self.assertIn('"genFormHidden":true', out)
        self.assertIn('"tabsDisabled":true', out)

    def test_reset_upload_form_reopens_on_upload_tab(self):
        # C12: "New map…" reopens on the Upload tab, generate fields reset,
        # upload preview copy restored.
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='idle';"
            "api.setSourceTab('generate');"
            "api.els.genName.value='Keep me?';"
            "api.els.genSeed.value='42';"
            "api.els.btnGenerate.disabled=false;"
            "api.els.previewTitle.textContent='Generated map';"
            "api.els.paneGridTitle.textContent='Grid';"
            "api.els.previewNote.textContent='gen note';"
            "api.resetUploadForm();"
            "return {"
            "uploadSource:api.state.uploadSource,"
            "genFormHidden:api.els.genForm.hidden,"
            "genName:api.els.genName.value,"
            "genSeed:api.els.genSeed.value,"
            "btnDisabled:api.els.btnGenerate.disabled,"
            "title:api.els.previewTitle.textContent,"
            "gridTitle:api.els.paneGridTitle.textContent,"
            "noteRestored:api.els.previewNote.textContent.indexOf('detection')" +
            ">=0};})()"
        )
        out = js(expr)
        self.assertIn('"uploadSource":"upload"', out, out)
        self.assertIn('"genFormHidden":true', out)
        self.assertIn('"genName":""', out)
        self.assertIn('"genSeed":""', out)
        self.assertIn('"btnDisabled":true', out)
        self.assertIn('"title":"Detected map"', out)
        self.assertIn('"gridTitle":"Detection"', out)
        self.assertIn('"noteRestored":true', out)

    def test_sync_generate_button_gating(self):
        # disabled: no name / out-of-range / non-integer size;
        # enabled: name + integers in 8-60.
        expr = (
            "(()=>{const run=(n,c,r)=>{api.els.genName.value=n;"
            "api.els.genCols.value=c;api.els.genRows.value=r;"
            "api.syncGenerateButton();"
            "return api.els.btnGenerate.disabled};"
            "return {noName:run('', '24','16'),"
            "low:run('x','7','16'),high:run('x','24','61'),"
            "nonInt:run('x','24.5','16'),"
            "edgeOk:!run('x','8','8'),"
            "midOk:!run('x','24','16'),"
            "maxOk:!run('x','60','60')};})()"
        )
        out = js(expr)
        for key in ("noName", "low", "high", "nonInt", "edgeOk", "midOk",
                    "maxOk"):
            self.assertIn(f'"{key}":true', out, out)

    def test_generate_map_success_end_to_end(self):
        # Drives the real generateMap() against the recorded fetch stub:
        # body {name, cols, rows, seed} -> 200 response -> preview state
        # with the generate copy, source pane hidden, start button enabled.
        gen_cells = (
            "Array.from({length:8},(_,y)=>Array.from({length:10},(x)=>"
            "(x===0||y===0||x===9||y===7)?'wall':'floor'))"
        )
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='idle';"
            "api.setSourceTab('generate');"
            "api.els.genName.value='  The Deep Warrens  ';"
            "api.els.genCols.value='10';"
            "api.els.genRows.value='8';"
            "api.els.genSeed.value='42';"
            "api._fetch.reset();"
            "api._fetch.response={ok:true,status:200,json:async()=>({"
            "id:'the-deep-warrens',name:'The Deep Warrens',"
            "width:10,height:8,cells:" + gen_cells +
            ",thumbnail:'data:image/png;base64,x'})};"
            "return api.generateMap().then(()=>({"
            "posted:api._fetch.sent.length===1,"
            "body:api._fetch.sent[0]?JSON.parse(api._fetch.sent[0].opts.body)" +
            ":null,"
            "map:api.state.uploadedMap,"
            "state:api.els.uploadView.dataset.state,"
            "title:api.els.previewTitle.textContent,"
            "paneSourceHidden:api.els.paneSource.hidden,"
            "paneGridTitle:api.els.paneGridTitle.textContent,"
            "previewNote:api.els.previewNote.textContent.indexOf('Generation')" +
            ">=0,"
            "uploadNote:api.els.uploadNote.textContent,"
            "noteHidden:api.els.uploadNote.hidden,"
            "startEnabled:!api.els.btnStartMap.disabled,"
            "genLabel:api.els.btnGenerate.textContent," +
            "genDisabled:api.els.btnGenerate.disabled" +
            "}));})()"
        )
        out = js(expr)
        self.assertIn('"posted":true', out, out)
        self.assertIn(
            '"body":{"name":"The Deep Warrens","cols":10,"rows":8,' +
            '"seed":42}', out, out)
        self.assertIn('"id":"the-deep-warrens"', out)
        self.assertIn('"dataUrl":null', out)
        self.assertIn('"state":"preview"', out)
        self.assertIn('"title":"Generated map"', out)
        self.assertIn('"paneSourceHidden":true', out)
        self.assertIn('"paneGridTitle":"Grid"', out)
        self.assertIn('"previewNote":true', out)
        self.assertIn('Generated 10\u00d78 grid', out)
        self.assertIn('"noteHidden":false', out)
        self.assertIn('"startEnabled":true', out)
        self.assertIn('"genLabel":"Generating…"', out)
        # busy held (same parity as the upload flow: the button stays busy
        # until "Start over" → resetUploadForm() clears it); and the button
        # stays disabled — the (unchanged) empty name field fails the gate.
        self.assertIn('"genDisabled":true', out)

    def test_generate_map_success_omits_blank_seed(self):
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='idle';"
            "api.setSourceTab('generate');"
            "api.els.genName.value='No Seed';"
            "api.els.genCols.value='8';"
            "api.els.genRows.value='8';"
            "api.els.genSeed.value='';"
            "api._fetch.reset();"
            "api._fetch.response={ok:true,status:200,json:async()=>({"
            "id:'no-seed',name:'No Seed',width:8,height:8,"
            "cells:Array.from({length:8},()=>Array(8).fill('floor')),"
            "thumbnail:null})};"
            "return api.generateMap().then(()=>({"
            "body:JSON.parse(api._fetch.sent[0].opts.body),"
            "thumb:api.state.uploadedMap.thumbnail" +
            "}));})()"
        )
        out = js(expr)
        # Blank seed -> no seed key on the wire; null thumbnail tolerated.
        self.assertIn(
            '"body":{"name":"No Seed","cols":8,"rows":8}', out, out)
        self.assertIn('"thumb":null', out)

    def test_generate_button_click_triggers_generate(self):
        # Regression (the QA coverage gap): the "Generate map" BUTTON was
        # a no-op because app.js never registered a click listener on
        # #btn-generate — only the Enter-key handler and direct
        # generateMap() calls triggered it (the earlier harness tests
        # called generateMap() directly, so the gap passed QA). This
        # simulates the user flow through the REAL addEventListener wiring:
        # switch to the generate tab, fill the fields (dispatching the
        # "input" events that enable the button), assert it is enabled,
        # then CLICK the button via dispatchEvent — never calling
        # generateMap() directly — and assert the fetch stub received
        # POST /api/maps/generate with the right body and the preview comes up.
        expr = (
            "(()=>{api.state.joined=true;"
            "api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.els.uploadView.dataset.state='idle';"
            "api.setSourceTab('generate');"
            "api._fetch.reset();"
            "api._fetch.response={ok:true,status:200,json:async()=>({"
            "id:'deep-warrens',name:'Deep Warrens',width:10,height:8,"
            "cells:Array.from({length:8},(_,y)=>Array.from({length:10},"
            "(x)=>(x===0||y===0||x===9)?'wall':'floor')),"
            "thumbnail:null})};"
            "const out={posted:false,enabledBefore:false,"
            "busyDisabledRightAfterClick:true,"
            "url:'',method:'',body:null,state:'',title:''};"
            "api.els.genName.value='Deep Warrens';"
            "api.els.genCols.value='10';"
            "api.els.genRows.value='8';"
            "for(const el of [api.els.genName,api.els.genCols,"
            "api.els.genRows]){el.dispatchEvent({type:'input'})};"
            "out.enabledBefore=!api.els.btnGenerate.disabled;"
            "api.els.btnGenerate.dispatchEvent({type:'click'});"
            "out.posted=api._fetch.sent.length===1;"
            "out.url=api._fetch.sent[0]?api._fetch.sent[0].url:null;"
            "out.method=api._fetch.sent[0]?"
            "api._fetch.sent[0].opts.method:null;"
            "out.body=api._fetch.sent[0]?"
            "JSON.parse(api._fetch.sent[0].opts.body):null;"
            "out.busyDisabledRightAfterClick=api.els.btnGenerate.disabled;"
            "const pump=(n)=>n>0?Promise.resolve().then(()=>pump(n-1)):"
            "Promise.resolve();"
            "return pump(12).then(()=>{"
            "out.state=api.els.uploadView.dataset.state;"
            "out.title=api.els.previewTitle.textContent;"
            "return out;});})()"
        )
        out = js(expr)
        self.assertIn('"enabledBefore":true', out, out)
        self.assertIn('"posted":true', out)
        self.assertIn('"url":"/api/maps/generate"', out)
        self.assertIn('"method":"POST"', out)
        self.assertIn(
            '"body":{"name":"Deep Warrens","cols":10,"rows":8}', out, out)
        self.assertIn('"busyDisabledRightAfterClick":true', out)
        # user flow completed: preview up with the generated map
        self.assertIn('"state":"preview"', out)
        self.assertIn('"title":"Generated map"', out)

    def test_generate_map_error_toasts_and_no_crash(self):
        # 400 -> error toast "Generate failed: ...", busy released, no
        # crash, preview untouched. The second subtest covers the old
        # hard-reject fetch behavior via the stub's hardReject flag.
        for reject_mode in ("http", "hard"):
            with self.subTest(reject_mode=reject_mode):
                expr = (
                    "(()=>{api.state.joined=true;"
                    "api.state.role='gm';"
                    "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
                    "api.els.uploadView.dataset.state='idle';"
                    "api.setSourceTab('generate');"
                    "api.els.genName.value='Boom';"
                    "api.els.genCols.value='10';"
                    "api.els.genRows.value='8';"
                    "api._fetch.reset();"
                    + ("api._fetch.hardReject=true;"
                       if reject_mode == "hard" else
                       "api._fetch.response={ok:false,status:400,"
                       "json:async()=>({error:\"'cols' must be an integer "
                       "in 8-60\"})};")
                    + "api.els.btnGenerate.disabled=false;"
                    "const doc=api.document;const spans=[];"
                    "const realCreate=doc.createElement;"
                    "doc.createElement=(t)=>{const el=realCreate(t);"
                    "if(t==='span')spans.push(()=>el.textContent);return el};"
                    "return api.generateMap().then(()=>{"
                    "doc.createElement=realCreate;"
                    "const texts=spans.map(f=>f());"
                    "return {toastText:texts[texts.length-1]||null,"
                    "state:api.els.uploadView.dataset.state,"
                    "genLabel:api.els.btnGenerate.textContent,"
                    "mapped:!!api.state.uploadedMap};});})()"
                )
                out = js(expr)
                self.assertIn("Generate failed:", out, out)
                # no crash, no preview switch, busy released (label back).
                self.assertIn('"state":"idle"', out)
                self.assertIn('"genLabel":"Generate map"', out)
                self.assertIn('"mapped":false', out)


# ══════════════════════════════════════════════════════════════════════
# Explored map (docs/design/explored-map.md §6/§7 — AC13 frontend)
# ══════════════════════════════════════════════════════════════════════
# The player's map is tiered by the server's "visibility" matrix:
#   S  → full detail (floor #efe9dc / wall #3b4252)   [in sight now]
#   E  → greyed     (floor #6b7280 / wall #4b5563)   [explored]
#   H  → nothing drawn (the #171b26 canvas bg shows)  [hidden]
# The GM and the upload-preview canvas NEVER receive a matrix → full detail.

class TestIndexHtmlExploredLegend(FrontendBase):
    """AC13a — the real index.html carries the three PLAYER legend chips and
    the pre-existing chips are all still present (regression guard)."""

    def setUp(self):
        with open(INDEX, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_three_player_legend_chips_present(self):
        # The three new chips are `legend-chip legend-explored` with their
        # swatch + copy, plus a `legend-sep legend-explored` separator. Assert
        # each chip's swatch+copy and that exactly 3 chips carry the class.
        self.assertIn('<i class="swatch floor"></i>in sight', self.html)
        self.assertIn('<i class="swatch explored"></i>explored', self.html)
        self.assertIn('<i class="swatch hidden"></i>hidden (not shown)', self.html)
        self.assertEqual(self.html.count("legend-chip legend-explored"), 3,
                         "expected exactly 3 legend-explored chips")

    def test_preexisting_legend_chips_still_present(self):
        for chip in (
            '<i class="swatch floor"></i>floor',
            '<i class="swatch wall"></i>wall',
            '<i class="swatch doorway"></i>doorway',
            '<i class="dot dot-tri team-party"></i>friend',
            '<i class="dot dot-circle team-neutral"></i>neutral',
            '<i class="dot dot-square team-hostile"></i>enemy',
            '<i class="dot dot-approx"></i>unseen contact',
            '<i class="ring-swatch"></i>awareness range',
        ):
            self.assertIn(chip, self.html, f"regression: {chip} missing")


@unittest.skipUnless(shutil.which("node") is not None,
                     "Node.js not found; skipping CSS static checks")
class TestStyleCssExplored(FrontendBase):
    """AC13a — the CSS defines the greyed/hidden swatches + tokens and the
    body.is-gm gating that hides the chips from the GM."""

    def setUp(self):
        css_path = os.path.join(os.path.dirname(INDEX), "style.css")
        with open(css_path, encoding="utf-8") as fh:
            self.css = fh.read()

    def test_explored_and_hidden_swatch_styles(self):
        self.assertIn(".swatch.explored", self.css)
        self.assertIn(".swatch.hidden", self.css)
        self.assertIn("#6b7280", self.css)      # explored floor
        self.assertIn("#4b5563", self.css)      # explored wall

    def test_gm_gating_hides_explored_chips(self):
        self.assertIn("body.is-gm .legend-explored { display: none; }", self.css)


class TestExploredMapStateAndValidate(FrontendBase):
    """AC13b (state half) — applyState stores a well-formed player matrix,
    treats a MALFORMED matrix (wrong row length) as null, and stores null for
    a GM payload (no "visibility" key)."""

    _MAP = ({"name": "m", "width": 5, "height": 4,
             "cells": [["floor"] * 5 for _ in range(4)]})

    def _welcome(self, extra: str = "") -> str:
        return (
            "(()=>{const map=" + json.dumps(self._MAP) + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false" + extra + "});"
            "return api.state.visibility;})()"
        )

    def test_player_welcome_stores_wellformed_matrix(self):
        # A well-formed 4x5 matrix (S/E/H) is stored verbatim (NOT null).
        vis = json.dumps(["SESSH", "SESSS", "SSSHS", "HHHSS"])
        out = js(self._welcome(",visibility:" + vis))
        # Stored verbatim and NOT collapsed to null.
        self.assertIn('"SESSH"', out)
        self.assertIn('"SSSHS"', out)
        self.assertNotIn("null", out)

    def test_malformed_matrix_wrong_row_length_is_null(self):
        # Row 0 is 6 chars on a 5-wide grid → malformed → treated as null.
        out = js(self._welcome(",visibility:['SESSSS','SESSS','SESSS','SESSS']"))
        self.assertIn("null", out, out)

    def test_malformed_matrix_wrong_row_count_is_null(self):
        # Only 3 rows for a 4-row grid → malformed → treated as null.
        out = js(self._welcome(",visibility:['SESSS','SESSS','SESSS']"))
        self.assertIn("null", out, out)

    def test_malformed_matrix_bad_char_is_null(self):
        # Row 1 ('SESXS') has a char ('X') outside SEH → malformed → null.
        out = js(self._welcome(",visibility:['SESSS','SESXS','SESSS','SESSS']"))
        self.assertIn("null", out, out)

    def test_gm_state_stores_null(self):
        # GM welcome: no "visibility" key at all → state.visibility stays null
        # even though the harness canvas renders (layoutCanvas gates on role).
        out = js(
            "(()=>{const map=" + json.dumps(self._MAP) + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            "map,entities:[],players:[],awareness:[],fog:false});"
            "return api.state.visibility;})()"
        )
        self.assertIn("null", out, out)

    def test_validate_visibility_matrix_direct(self):
        out = js(
            "(()=>{const g={width:5,height:4};"
            "return {good:!!api.validateVisibilityMatrix("
            "['SESSS','SESSS','SESSS','SESSS'],g),"
            "badLen:api.validateVisibilityMatrix("
            "['SESSS','SESS','SESSS','SESSS'],g),"
            "badChar:api.validateVisibilityMatrix("
            "['SESSS','SESXS','SESSS','SESSS'],g),"
            "nullIn:api.validateVisibilityMatrix(null,g),"
            "absent:api.validateVisibilityMatrix(undefined,g)};})()"
        )
        self.assertIn('"good":true', out, out)
        self.assertIn('"badLen":null', out)
        self.assertIn('"badChar":null', out)
        self.assertIn('"nullIn":null', out)
        self.assertIn('"absent":null', out)


class TestExploredMapRender(FrontendBase):
    """AC13b/c — the real drawGridOnCanvas tiers cells for a player and stays
    full-detail for the GM and the preview (no third argument).

    Grid-line pass (BUG-EXPLORED-01, spec §6.2): in tiered mode EVERY cell
    edge that has a drawn (S/E) cell on at least one side gets its 1px
    segment — the region's frontier against hidden cells (frontier edge of
    an S cell = full #d9d1bd, of an E cell = 30%-alpha dimmed), the outer
    canvas frame, and the shared edges between two drawn cells (S-side
    style wins over E-side). H cells never contribute their own lines."""

    # 5x4 map: a 3x2 open floor block (x1-3, y1-2) inside a wall ring.
    #   y0: all wall ; y1: wall floor floor floor wall ; y2: same ; y3: all wall
    _GRID = [
        ["wall"] * 5,
        ["wall", "floor", "floor", "floor", "wall"],
        ["wall", "floor", "floor", "floor", "wall"],
        ["wall"] * 5,
    ]
    _MAP_JS = json.dumps({"name": "m", "width": 5, "height": 4, "cells": _GRID})
    _GRID_JS = json.dumps(_GRID)
    # Row y=1 floors in sight (S), row y=2 floors explored (E): the two floor
    # rows are adjacent, so S-S edges draw full lines and the E-E edges between
    # the three E floors draw the 30%-alpha dimmed line.
    _VIS_JS = json.dumps(["SSSSS", "SSSSS", "SEEES", "SSSSS"])

    def test_player_render_tiers_cells(self):
        # Render a player whose S cell is (1,1) and E cell is (3,1); every
        # other cell is H (nothing drawn). Assert via the recorded fillRect
        # calls: S floor filled #efe9dc, E floor filled #6b7280, no H-cell
        # fill at all, and the grid-line dim (30% alpha) is used for E edges.
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false,"
            "visibility:" + self._VIS_JS + "});"
            "api.els.mapView.hidden=false;api.renderAll();"
            "const c=api.els.canvas.getContext('2d');"
            "return {fills:c._fills.map(f=>[f.x,f.y,f.w,f.h,f.style]),"
            "strokes:c._strokes.map(s=>s.style)};})()"
        )
        out = json.loads(js(expr))
        fills = out["fills"]
        # The player path must have filled the two floor cells with their
        # tiers' floor colors.
        self.assertTrue(any(f[4] == "#efe9dc" for f in fills),
                        "no full-detail (S) floor fill: %s" % fills)
        self.assertTrue(any(f[4] == "#6b7280" for f in fills),
                        "no greyed (E) floor fill: %s" % fills)
        # No hidden cell may be filled with either floor color: every fill
        # besides the background + the two tiered floors must be absent. We
        # check specifically that the H floor cells (only (1,1) and (3,1) are
        # floor; the S/E are those two, so there are no H floor cells here).
        # But the grid-line dim must be present (E cell edges use 30% alpha).
        self.assertIn("rgba(217, 209, 189, 0.3)", out["strokes"] + [""]
                      , "no dimmed grid line for E cells: %s" % out["strokes"])
        # The full grid line is also present (S cell edges).
        self.assertIn("#d9d1bd", out["strokes"])

    def _line_segments(self, vis):
        """Render ``map``+``vis`` on the 800x600 harness canvas via a direct
        ``drawGridOnCanvas`` call and return ``{key: style}`` for every drawn
        *line* segment (the wall-hatch/border ``rect`` segments are skipped).

        Key format (``s=150, ox=25, oy=0`` so grid lines land at the +0.5px
        hairline positions ``gx=25.5,175.5,325.5,475.5,625.5,775.5`` and
        ``gy=0.5,150.5,300.5,450.5,600.5``):
          vertical   ``V<x>:<y>``            (x, y = the +0.5 grid line + min end)
          horizontal ``H<x>,<y>,<len>``      (x, y = min end, len = 150)
        If a segment is drawn MORE THAN ONCE in DIFFERENT styles its value is
        ``"duplicate"`` (spec §6.2: a shared edge may legitimately be stroked
        by both drawn cells, but only ever in the SAME style — so any
        double-stroke must agree, which this still catches)."""
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.state.role='player';api.state.grid=map;"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "const c=api.els.canvas.getContext('2d');"
            "c._strokes.length=0;"
            "api.drawGridOnCanvas(api.els.canvas,c," + vis + ");"
            "const v=Object.create(null);"
            "for(const s of c._strokes){"
            "for(const seg of s.path){"
            "if(!seg.m||!seg.l)continue;"
            "const a=[seg.m[0],seg.m[1]],b=[seg.l[0],seg.l[1]];"
            "const dx=Math.abs(a[0]-b[0]),dy=Math.abs(a[1]-b[1]);"
            "if(dx>0&&dy>0)continue;"
            "const x=Math.min(a[0],b[0]),y=Math.min(a[1],b[1]);"
            "const key=(dx===0?'V'+x+':'+y:'H'+x+','+y+','+dx);"
            "if(v[key]===undefined)v[key]=s.style;"
            "else if(v[key]!==s.style)v[key]='duplicate';}}"
            "return v;})()"
        )
        return json.loads(js(expr))

    def test_tiered_grid_line_frontier_and_outer_frame(self):
        # BUG-EXPLORED-01 (spec §6.2): in tiered mode EVERY cell edge that has
        # an S/E cell on at least one side gets its 1px segment — a frontier
        # edge against a hidden cell in the drawn cell's OWN style (S edge ->
        # full #d9d1bd, E edge -> 30%-alpha dim), the outer canvas frame, and
        # a shared edge between two drawn cells in the full "S" style when
        # either side is S. An H cell contributes no line of its own (an H|H
        # edge is never drawn).
        #
        # Direct-call drawGridOnCanvas on the 5x4 grid. Harness canvas is
        # 800x600 at dpr 1, so s = floor(min(800/5, 600/4)) = 150 and the
        # origin is centered: ox = floor((800 - 5*150)/2) = 25,
        # oy = floor((600 - 4*150)/2) = 0.
        #
        # Matrix (each drawn cell is diagonal to the others, so this phase
        # exercises frontier + frame only — the shared S|E case is covered by
        # test_tiered_shared_s_e_edge_is_full below):
        #   y0: HHHHH   (all H)
        #   y1: HSHHH   S at (1,1)
        #   y2: HHHEH   E at (3,2)
        #   y3: HHHHS   S at (4,3)
        #
        # The complete expected set is 12 segments, hand-derived cell by cell:
        #   S(1,1) [4 frontiers, all full]:
        #     top    H175,150.5,150   left  V175.5:150
        #     right  V325.5:150       bottom H175,300.5,150
        #   E(3,2) [4 frontiers, all dim]:
        #     left   V475.5:300       right V625.5:300
        #     top    H475,300.5,150   bottom H475,450.5,150
        #   S(4,3) [frontier + frame, all full]:
        #     left   V625.5:450       top  H625,450.5,150
        #     right frame   V775.5:450
        #     bottom frame  H625,600.5,150
        vis = json.dumps(["HHHHH", "HSHHH", "HHHEH", "HHHHS"])
        full = "#d9d1bd"
        dim = "rgba(217, 209, 189, 0.3)"
        out = self._line_segments(vis)

        # (i) outer-frame segments over the drawn border cells: the frame is
        # drawn only over the cell that is actually drawn (the right frame
        # beside S(4,3) and the bottom frame under S(4,3) — note the frame
        # sits at x=775.5 = gx(5), NOT 875, and the top of S(1,1) is at
        # y=150.5, not the row-0 frame at y=0.5).
        # (ii) frontier segments against H neighbours, in the drawn cell's
        # OWN style (S frontier -> full, E frontier -> 30% dim).
        expected = {
            # -- S(1,1): four frontier edges, full (its own style) --
            "H175,150.5,150": full,   # top (vs H(1,0))
            "V175.5:150": full,       # left (vs H(0,1))
            "V325.5:150": full,       # right (vs H(2,1))
            "H175,300.5,150": full,   # bottom (vs H(1,2))
            # -- E(3,2): four frontier edges, dim (its own style) --
            "V475.5:300": dim,        # left (vs H(2,2))
            "V625.5:300": dim,        # right (vs H(4,2))
            "H475,300.5,150": dim,    # top (vs H(3,1))
            "H475,450.5,150": dim,    # bottom (vs H(3,3))
            # -- S(4,3): two frontiers + right/bottom frame, full --
            "V625.5:450": full,       # left (vs H(3,3))
            "H625,450.5,150": full,   # top (vs H(4,2))
            "V775.5:450": full,       # right FRAME (off-grid, S style)
            "H625,600.5,150": full,   # bottom FRAME (off-grid, S style)
        }
        # (e) the COMPLETE segment set: exact count + every key at the
        # exact style (regression guard against spurious segments in either
        # style AND against a missing/mis-styled one).
        self.assertEqual(out, expected,
                         "drawn segment set != expected 12 segments: %s"
                         % json.dumps(out, sort_keys=True))
        self.assertEqual(len(out), 12)
        # (v) no segment drawn twice in DIFFERENT styles.
        self.assertNotIn("duplicate", out.values(),
                         "a segment was overpainted in a different style: %s"
                         % json.dumps(out))
        # (iv) a representative H|H edge set is ABSENT: the row-0 frame over
        # the all-H row (incl. the top edge of H(0,0) at y=0.5), the left
        # frame beside the H column 0, and the interior H|H boundaries.
        for key in ("H25,0.5,150", "H175,0.5,150", "H325,0.5,150",
                    "H475,0.5,150", "H625,0.5,150",
                    "V25.5:0", "V25.5:150", "V25.5:300", "V25.5:450",
                    "H25,150.5,150", "H25,300.5,150", "H25,450.5,150",
                    "H325,450.5,150", "V475.5:450", "V775.5:0",
                    "V775.5:150", "V775.5:300", "V325.5:300",
                    "V325.5:450", "V475.5:150", "V625.5:150",
                    "V175.5:300", "V175.5:450", "V775.5:600"):
            self.assertNotIn(key, out,
                             "H|H edge must not be drawn: %s" % key)

    def test_tiered_shared_s_e_edge_is_full(self):
        # spec §6.2 (property iii): a shared edge between two drawn cells is
        # drawn at FULL ("S wins") when either side is S; a shared edge whose
        # two sides are both E stays dim. The Phase-1 matrix has no adjacent
        # S/E pair, so cover it here.
        #   y0: SSSSS   (all S)
        #   y1: SSSEE   S at (0,1)(1,1)(2,1); E at (3,1) and (4,1)
        #   y2: HHHHH   (all H)
        #   y3: HHHHH   (all H)
        # The S|E boundary is the vertical edge at gx(3)=475.5 between S(2,1)
        # and E(3,1) -> key V475.5:150, drawn FULL (S wins). The E|E boundary
        # at gx(4)=625.5 between E(3,1) and E(4,1) -> key V625.5:150, stays
        # DIM. Complete set: 27 unique segments, 23 full + 4 dim, no
        # conflicting overpaint.
        vis = json.dumps(["SSSSS", "SSSEE", "HHHHH", "HHHHH"])
        full = "#d9d1bd"
        dim = "rgba(217, 209, 189, 0.3)"
        out = self._line_segments(vis)

        # the two boundary edges, explicitly:
        self.assertEqual(out.get("V475.5:150"), full,
                         "shared S|E edge (S(2,1)|E(3,1)) must be FULL "
                         "(S wins): %s" % json.dumps(out))
        self.assertEqual(out.get("V625.5:150"), dim,
                         "shared E|E edge (E(3,1)|E(4,1)) must stay DIM: %s"
                         % json.dumps(out))

        # Complete set: exactly 27 segments (23 full + 4 dim).
        expected = {
            # row 0 top frame + internal verticals (all S)
            "H25,0.5,150": full, "H175,0.5,150": full, "H325,0.5,150": full,
            "H475,0.5,150": full, "H625,0.5,150": full,
            "V25.5:0": full, "V175.5:0": full, "V325.5:0": full,
            "V475.5:0": full, "V625.5:0": full, "V775.5:0": full,
            # row 0 / row 1 shared horizontal (all S)
            "H25,150.5,150": full, "H175,150.5,150": full,
            "H325,150.5,150": full, "H475,150.5,150": full,
            "H625,150.5,150": full,
            # row 1 verticals: S|S full, the S|E boundary full (S wins)
            "V25.5:150": full, "V175.5:150": full, "V325.5:150": full,
            "V475.5:150": full, "V625.5:150": dim, "V775.5:150": dim,
            # row 1 / row 2 boundary: S cells -> full, E cells -> dim
            "H25,300.5,150": full, "H175,300.5,150": full,
            "H325,300.5,150": full, "H475,300.5,150": dim,
            "H625,300.5,150": dim,
        }
        self.assertEqual(out, expected,
                         "drawn segment set != expected 27 segments: %s"
                         % json.dumps(out, sort_keys=True))
        self.assertEqual(len(out), 27)
        self.assertNotIn("duplicate", out.values(),
                         "a segment was overpainted in a different style: %s"
                         % json.dumps(out))

    def test_player_render_no_fill_over_hidden_cell(self):
        # A matrix whose ONLY S/E cells are (1,1) [S] and (3,2) [E]; every
        # other cell is H. No fill may cover an H cell. The two S/E floors
        # land at (offset + cell*size, ...) per the live view; any OTHER
        # floor fill would be a bug. The geometry is read from state so the
        # test holds under the pan/zoom view model (the 5x4 map auto-fits to
        # L0's 6x5 window, so cell/offsets differ from the old fit-to-map).
        vis = json.dumps(["HHHHH", "HSHHH", "HHHEH", "HHHHH"])
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false,"
            "visibility:" + vis + "});"
            "api.els.mapView.hidden=false;api.renderAll();"
            "const c=api.els.canvas.getContext('2d');"
            "const s=api.state.cell, ox=api.state.offsetX, oy=api.state.offsetY;"
            "return {s,ox,oy,fills:c._fills"
            ".filter(f=>f.style==='#efe9dc'||f.style==='#6b7280')"
            ".map(f=>[f.x,f.y,f.w,f.h,f.style])};})()"
        )
        out = json.loads(js(expr))
        s, ox, oy = out["s"], out["ox"], out["oy"]
        # S/E floor rects: (1,1) [S] and (3,2) [E].
        expected = {(ox + 1 * s, oy + 1 * s, s, s),
                    (ox + 3 * s, oy + 2 * s, s, s)}
        got = {(f[0], f[1], f[2], f[3]) for f in out["fills"]}
        self.assertEqual(got, expected,
                         "floor fills must land EXACTLY on the S and E cells, "
                         "never on an H cell: got %s expected %s" % (got, expected))

    def test_gm_render_full_detail_no_tiers(self):
        # A GM welcome (no visibility key) must render the whole grid with
        # the full floor fill in ONE rect (the no-tier path) — no greyed
        # floor color anywhere.
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            "map,entities:[],players:[],awareness:[],fog:false});"
            "api.els.mapView.hidden=false;api.renderAll();"
            "const c=api.els.canvas.getContext('2d');"
            "const styles=c._fills.map(f=>f.style);"
            "return {styles:styles, hasGrey:styles.includes('#6b7280'),"
            "hasFull:styles.includes('#efe9dc')};})()"
        )
        out = json.loads(js(expr))
        self.assertTrue(out["hasFull"], "GM path must use the full floor: %s"
                        % out["styles"])
        self.assertFalse(out["hasGrey"], "GM path must NOT grey any cell: %s"
                         % out["styles"])

    def test_preview_render_full_detail(self):
        # showUploadPreview() draws the grid on #preview-canvas with NO third
        # argument → full detail. Drive it directly with a small uploadedMap
        # and assert no greyed floor fill appears.
        expr = (
            "(()=>{api.state.joined=true;api.state.role='gm';"
            "api.state.you={id:'p1',name:'Gamer',role:'gm',entity_id:null};"
            "api.state.grid=null;"
            "api.state.uploadedMap={id:'x',name:'x',width:5,height:4,"
            "cells:" + self._GRID_JS + ",thumbnail:null,dataUrl:null};"
            "api.state.uploadSource='upload';"
            "api.showUploadPreview();"
            "const c=api.els.previewCanvas.getContext('2d');"
            "const styles=c._fills.map(f=>f.style);"
            "return {styles:styles, hasGrey:styles.includes('#6b7280'),"
            "hasFull:styles.includes('#efe9dc')};})()"
        )
        out = json.loads(js(expr))
        self.assertTrue(out["hasFull"], "preview must use the full floor: %s"
                        % out["styles"])
        self.assertFalse(out["hasGrey"],
                         "preview must NEVER receive a tier matrix: %s"
                         % out["styles"])

    def test_draw_grid_on_canvas_with_null_visibility_matches_today(self):
        # drawGridOnCanvas(canvas, ctx, null) is byte-for-byte today's
        # behavior — one WHOLE-GRID floor base fill (not per-cell) + wall
        # fills per cell. Call it directly and assert the single whole-grid
        # floor base fill (width = s*g.width, height = s*g.height) in the full
        # floor color, and that NO cell is greyed.
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.state.role='gm';api.state.grid=map;"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "const c=api.els.canvas.getContext('2d');"
            "c._fills.length=0;c._strokes.length=0;"
            "api.drawGridOnCanvas(api.els.canvas,c,null);"
            "const whole=c._fills.find(f=>f.style==='#efe9dc'&&"
            "f.w===750&&f.h===600);"
            "const grey=c._fills.some(f=>f.style==='#6b7280');"
            "return {hasWhole:!!whole,grey:grey};})()"
        )
        out = json.loads(js(expr))
        self.assertTrue(out["hasWhole"],
                        "null path must draw a single whole-grid floor base: %s"
                        % out)
        self.assertFalse(out["grey"],
                         "null path must NOT grey any cell: %s" % out)

# ══════════════════════════════════════════════════════════════════════
# Door iconography (docs/design/door-iconography.md §5–§8 — AC9–AC16)
# ══════════════════════════════════════════════════════════════════════
# Pictorial wooden doors: every `doorway` cell renders as an actual DOOR —
# a normal door is BROWN wood, a safe-room door is GREEN wood (kind), and
# the state is: "L" locked+closed (slab + top-right padlock), "U" unlocked
# closed (slab, no padlock), "O" open (radial light glow + ajar leaf —
# yellow normal / green safe). One dispatcher draws all six states:
# drawDoorCell(ctx, kind, state, px, py, s, t), t ∈ S (full) / E (greyed,
# the ePadlockMark keeps locked readable). The GM Door tool
# (Unlock/Lock/Open/Close) is unchanged; the GM Safe door tool gains
# Unlock + Lock (Mark/Unmark/Open/Close unchanged); the legend shows six
# mini-canvas swatches pixel-identical to the map art. Safe doors use the
# SAME L/U/O model as normal doors (legacy "C" coerces to "U" in
# validateSafe; safeDoorStateAt defaults to "L").

# s=8 geometry (used for tier assertions): margin=1 -> slab [1,1,6,6],
# frameW 1, frame stroke [1.5,1.5,5,5]; padlock p=4 (x0=5, y0=1), shackle
# arc center (6.98,2.24, r0.88), body (5.88,2.68,2.24,2.32).
# s=60: margin=6 -> slab [6,6,48,48], frame stroke [6.5,6.5,47,47];
# padlock p=25.2 (x0=28.8, y0=6), body (34.344,16.56,14.112,14.616).

GRID5 = [
    ["wall", "wall", "wall", "wall", "wall"],
    ["wall", "floor", "doorway", "floor", "wall"],
    ["wall", "floor", "doorway", "floor", "wall"],
    ["wall", "floor", "doorway", "floor", "wall"],
    ["wall", "wall", "wall", "wall", "wall"],
]
GRID5_JS = json.dumps({"name": "m", "width": 5, "height": 5, "cells": GRID5})

DOOR_GRID_CTX = (
    "(()=>{const map=" + GRID5_JS + ";"
    "api.state.role='gm';api.state.grid=map;"
)
DOOR_CANVAS = (
    "api.els.canvas.width=800;api.els.canvas.height=600;"
    "const c=api.els.canvas.getContext('2d');"
    "c._rects.length=0;c._strokes.length=0;c._fills.length=0;"
    "c._gradients.length=0;"
)

class TestDoorIconographyTokens(FrontendBase):
    """AC16c — the T palette carries the §5.3 pictorial-door tokens (exact
    hexes), all distinct from floor #efe9dc / wall #3b4252 / the explored
    floor #6b7280, and the two hover-preview tokens equal the wood tokens.
    The old amber/red + green-cross tokens are REMOVED."""

    def test_t_carries_the_pictorial_tokens(self):
        out = js(
            "(()=>({woodBrown:api.T.woodBrown,woodBrownDark:api.T.woodBrownDark,"
            "woodGreen:api.T.woodGreen,woodGreenDark:api.T.woodGreenDark,"
            "padlockBody:api.T.padlockBody,padlockShackle:api.T.padlockShackle,"
            "lightYellow:api.T.lightYellow,lightGreen:api.T.lightGreen,"
            "frameBrown:api.T.frameBrown,frameGreen:api.T.frameGreen,"
            "doorShadow:api.T.doorShadow,eWoodSlab:api.T.eWoodSlab,"
            "eSlabFrame:api.T.eSlabFrame,ePadlockMark:api.T.ePadlockMark,"
            "eLight:api.T.eLight,doorWoodPreview:api.T.doorWoodPreview,"
            "safeWoodPreview:api.T.safeWoodPreview,"
            "floor:api.T.floor,wall:api.T.wallFill,eFloor:api.T.exploredFloor,"
            "old:api.T.doorOpen||api.T.safeOpen||api.T.exploredDoorOpen||null}))()"
        )
        d = json.loads(out)
        self.assertEqual(d["woodBrown"], "#9c6b3a")
        self.assertEqual(d["woodBrownDark"], "#7a4f2a")
        self.assertEqual(d["woodGreen"], "#4f9e6b")
        self.assertEqual(d["woodGreenDark"], "#3c7d53")
        self.assertEqual(d["padlockBody"], "#e6b422")
        self.assertEqual(d["padlockShackle"], "#8a8f98")
        self.assertEqual(d["lightYellow"], "#ffe9a8")
        self.assertEqual(d["lightGreen"], "#c9f2d4")
        self.assertEqual(d["frameBrown"], "#5b4327")
        self.assertEqual(d["frameGreen"], "#2f5c40")
        self.assertEqual(d["doorShadow"], "rgba(0,0,0,0.18)")
        self.assertEqual(d["eWoodSlab"], "#8a94a0")
        self.assertEqual(d["eSlabFrame"], "#5f6874")
        self.assertEqual(d["ePadlockMark"], "#cfd4db")
        self.assertEqual(d["eLight"], "#e8ecf0")
        # the preview tokens are the wood colors (§7.2)
        self.assertEqual(d["doorWoodPreview"], d["woodBrown"])
        self.assertEqual(d["safeWoodPreview"], d["woodGreen"])
        # every art color is distinct from floor, wall, and explored floor
        for k in ("woodBrown", "woodBrownDark", "woodGreen", "woodGreenDark",
                  "padlockBody", "padlockShackle", "lightYellow", "lightGreen",
                  "frameBrown", "frameGreen", "eWoodSlab", "eSlabFrame",
                  "ePadlockMark", "eLight"):
            c = d[k].lower()
            self.assertNotEqual(c, d["floor"].lower(), k)
            self.assertNotEqual(c, d["wall"].lower(), k)
        for k in ("eWoodSlab", "eSlabFrame", "ePadlockMark", "eLight"):
            self.assertNotEqual(d[k].lower(), d["eFloor"].lower(), k)
        # the old amber/red + green-cross tokens are gone
        self.assertIsNone(d["old"])

    def test_safe_states_model_constant(self):
        # AC16: safe doors use the same L/U/O model as normal doors.
        self.assertEqual(json.loads(js("api.SAFE_STATES")), ["L", "U", "O"])


class TestDoorStatic(FrontendBase):
    """AC16 (static half) — index.html carries the GM Door tool (four
    actions, UNCHANGED) and SIX `.door-swatch` legend chips (three normal +
    three safe, visible to BOTH roles — no body.is-gm gate); style.css
    carries the §5.3 tokens and the .door-swatch canvas-swatch styles, and
    the old amber/red + green-cross chips/tokens are GONE."""

    def setUp(self):
        with open(INDEX, encoding="utf-8") as fh:
            self.html = fh.read()
        css_path = os.path.join(os.path.dirname(INDEX), "style.css")
        with open(css_path, encoding="utf-8") as fh:
            self.css = fh.read()

    def test_paint_group_has_door_tool_and_four_sub_buttons(self):
        self.assertIn(
            '<button class="tool-btn" data-tool="door" aria-pressed="false">',
            self.html)
        for action in ("unlock", "lock", "open", "close"):
            self.assertIn(f'data-door-action="{action}"', self.html,
                          f"missing door-action {action}")
        self.assertIn('id="door-action-row"', self.html)
        for tool in ('data-tool="select"', 'data-tool="floor"',
                     'data-tool="wall"', 'data-tool="doorway"'):
            self.assertIn(tool, self.html)

    def test_six_door_swatch_legend_chips_present_and_ungated(self):
        # Three normal + three safe chips, each a mini-canvas swatch with
        # data-kind + data-state (the six states of the pictorial door art).
        self.assertEqual(self.html.count('class="door-swatch"'), 6)
        for kind, label in (("normal", "door"), ("safe", "safe")):
            for st in ("L", "U", "O"):
                self.assertIn(
                    f'<i class="door-swatch" data-kind="{kind}" '
                    f'data-state="{st}"></i>', self.html)
                self.assertIn(f"{label} ·", self.html)
        self.assertEqual(self.html.count("legend-chip legend-doors"), 3)
        # the pre-existing chips are unchanged
        for chip in (
            '<i class="swatch floor"></i>floor',
            '<i class="swatch wall"></i>wall',
            '<i class="swatch doorway"></i>doorway',
            '<i class="swatch explored"></i>explored',
        ):
            self.assertIn(chip, self.html)

    def test_legend_door_chips_are_not_gm_gated(self):
        self.assertIn("body.is-gm .legend-explored { display: none; }",
                      self.css)
        self.assertNotIn(".legend-doors", self.css)
        self.assertIn(".legend-explored", self.css)

    def test_css_door_tokens_and_swatch_styles(self):
        for token in ("--door-wood-brown: #9c6b3a",
                      "--door-wood-brown-dark: #7a4f2a",
                      "--door-wood-green: #4f9e6b",
                      "--door-wood-green-dark: #3c7d53",
                      "--door-padlock: #e6b422",
                      "--door-padlock-shackle: #8a8f98",
                      "--door-light-yellow: #ffe9a8",
                      "--door-light-green: #c9f2d4",
                      "--door-frame-brown: #5b4327",
                      "--door-frame-green: #2f5c40",
                      "--door-e-slab: #8a94a0",
                      "--door-e-slab-frame: #5f6874",
                      "--door-e-padlock: #cfd4db",
                      "--door-e-light: #e8ecf0"):
            self.assertIn(token, self.css)
        self.assertIn(".door-swatch { display: inline-block; width: 16px; "
                      "height: 16px; border-radius: 2px; }", self.css)
        self.assertIn(".door-swatch canvas { display: block; }", self.css)
        # the door cursor mode + the plain doorway token are kept
        self.assertIn("mode-paint-door", self.css)
        self.assertIn("--doorway: #d97706", self.css)
        # the old amber/red + green-cross tokens are REMOVED
        for gone in ("--door-open", "--door-unlocked", "--door-locked",
                     "--safe-open", "--explored-safe-open",
                     ".swatch.door-open", ".swatch.safe-door"):
            self.assertNotIn(gone, self.css, f"{gone} must be removed")


class TestDoorStateModel(FrontendBase):
    """AC11b — state.doors is set from msg.map.doors in applyState ({} when
    absent), and MALFORMED doors (wrong type / bad keys / bad state chars)
    are treated as {} (all locked) — never crash, following the
    validateVisibilityMatrix defensive pattern. UNCHANGED by the redesign:
    normal doors already use L/U/O."""

    _MAP = ({"name": "m", "width": 5, "height": 4,
             "cells": [["floor"] * 5 for _ in range(4)]})

    def _welcome_doors(self, doors_js: str) -> str:
        return (
            "(()=>{const map=" + json.dumps(self._MAP) + ";"
            "map.doors=" + doors_js + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "return api.state.doors;})()"
        )

    def test_absent_doors_defaults_to_empty_object(self):
        out = js(self._welcome_doors("undefined"))
        self.assertEqual(json.loads(out), {})

    def test_null_doors_defaults_to_empty_object(self):
        self.assertEqual(json.loads(js(self._welcome_doors("null"))), {})

    def test_valid_doors_object_stored(self):
        out = js(self._welcome_doors("{'1,2':'U','3,0':'O','0,3':'L'}"))
        self.assertEqual(json.loads(out), {"1,2": "U", "3,0": "O", "0,3": "L"})

    def test_malformed_doors_treated_as_empty(self):
        for bad in ("[]", "'L'", "5", "{'1x':'L'}", "{'1,2':'X'}", "true"):
            with self.subTest(bad=bad):
                self.assertEqual(json.loads(js(self._welcome_doors(bad))),
                                 {}, bad)

    def test_state_broadcast_replaces_doors(self):
        out = js(
            "(()=>{const map=" + json.dumps(self._MAP) + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "map.doors={'1,1':'O'};"
            "api.onState({type:'state',map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "api.state.grid.cells[1][1]='doorway';"
            "const hadOpen=api.doorStateAt(1,1);"
            "const m2=Object.assign({},map);m2.doors={};"
            "api.onState({type:'state',map:m2,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "return {hadOpen, now:api.doorStateAt(1,1),"
            "all:api.state.doors};})()"
        )
        d = json.loads(out)
        self.assertEqual(d["hadOpen"], "O")
        self.assertEqual(d["now"], "L")     # key gone => default locked
        self.assertEqual(d["all"], {})


class TestDoorStateAt(FrontendBase):
    """AC11b — doorStateAt(x, y) returns the recorded state for a doorway
    cell, DEFAULTS to "L" when the key is absent, and returns null for a
    non-doorway cell (no door there). UNCHANGED by the redesign."""

    _SETUP = (
        "api.state.grid={width:4,height:3,cells:["
        "['floor','doorway','floor','floor'],"
        "['wall','doorway','wall','floor'],"
        "['floor','floor','doorway','floor']]}"
    )

    def test_default_is_locked_on_unrecorded_doorway(self):
        out = js("(()=>{" + self._SETUP + ";"
                 "api.state.doors={};"
                 "return {a:api.doorStateAt(1,0), b:api.doorStateAt(1,1)};})()"
        )
        d = json.loads(out)
        self.assertEqual(d["a"], "L")
        self.assertEqual(d["b"], "L")

    def test_recorded_states_win(self):
        out = js("(()=>{" + self._SETUP + ";"
                 ';api.state.doors={"1,0":"O","2,2":"U"};'
                 'return {o:api.doorStateAt(1,0), u:api.doorStateAt(2,2),'
                 'l:api.doorStateAt(1,1)};})()'
        )
        d = json.loads(out)
        self.assertEqual(d["o"], "O")
        self.assertEqual(d["u"], "U")
        self.assertEqual(d["l"], "L")

    def test_non_doorway_cell_has_no_door(self):
        out = js("(()=>{" + self._SETUP + ";"
                 ';api.state.doors={"0,0":"O"};'
                 'return {f:api.doorStateAt(0,0), w:api.doorStateAt(0,1),'
                 'oob:api.doorStateAt(9,9)};})()'
        )
        d = json.loads(out)
        self.assertIsNone(d["f"])
        self.assertIsNone(d["w"])
        self.assertIsNone(d["oob"])


class TestDoorCellRender(FrontendBase):
    """AC9/AC10/AC11/AC12/AC13/AC14 — drawDoorCell renders the SIX states
    (normal/safe × L/U/O) at BOTH tiers with the §5.1/§5.2 palette, called
    directly (pure function) on the recorded stub ctx. Padlock present iff
    L; glow iff O; slab iff L/U; family brown for normal, green for safe;
    E tier greyed with the ePadlockMark keeping locked readable."""

    def _render(self, kind, st, s, t):
        expr = (
            "(()=>{const el=api.document.createElement('canvas');"
            "el.width=" + str(s) + ";el.height=" + str(s) + ";"
            "const c=el.getContext('2d');"
            "c._rects.length=0;c._strokes.length=0;c._fills.length=0;"
            "c._gradients.length=0;c._fillPaths.length=0;"
            "api.drawDoorCell(c,'" + kind + "','" + st + "',0,0," + str(s) + ",'" + t + "');"
            "const fills=c._fills.map(f=>f.style);"
            "const grads=c._gradients.map(g=>({cx:g.x1,cy:g.y1,"
            "stops:g.stops.map(s=>s[1])}));"
            "const frameRects=c._rects.filter(r=>r.w>=5&&r.h>=5);"
            "const planks=c._strokes.some(x=>(x.style===api.T.woodBrownDark"
            "||x.style===api.T.woodGreenDark||x.style===api.T.eSlabFrame));"
            "return {fills,grads,"
            "shackle:c._strokes.some(x=>x.style===api.T.padlockShackle),"
            "body:c._fillPaths.some(fp=>fp.style===api.T.padlockBody),"
            "mark:c._strokes.some(x=>x.style===api.T.ePadlockMark),"
            "markFill:c._fillPaths.some(fp=>fp.style===api.T.ePadlockMark),"
            "planks,shadow:c._strokes.some(x=>x.style===api.T.doorShadow),"
            "keyhole:c._fillPaths.some(fp=>fp.style==='rgba(0,0,0,0.45)'),"
            "leafFill:c._fillPaths.some(fp=>fp.style===api.T.woodBrown"
            "||fp.style===api.T.woodGreen)};})()"
        )
        return json.loads(js(expr))

    def test_s_tier_all_six_states(self):
        # s=60 (AC12: full detail — planks, shadow, keyhole, ajar leaf).
        for kind, slab, frame, glow in (
                ("normal", "#9c6b3a", "#5b4327", "#ffe9a8"),
                ("safe", "#4f9e6b", "#2f5c40", "#c9f2d4")):
            with self.subTest(kind=kind):
                # L — slab + padlock (brass body, steel shackle, keyhole)
                d = self._render(kind, "L", 60, "S")
                self.assertIn(slab, d["fills"])
                self.assertTrue(d["body"], "padlock brass body fill")
                self.assertTrue(d["shackle"], "padlock steel shackle")
                self.assertTrue(d["keyhole"], "keyhole at s=60")
                self.assertTrue(d["planks"], "plank seams at s=60")
                self.assertTrue(d["shadow"], "inner shadow at s=60")
                self.assertEqual(d["grads"], [], "closed door has no glow")
                # U — identical minus the padlock (ABSENCE is the signal)
                d = self._render(kind, "U", 60, "S")
                self.assertIn(slab, d["fills"])
                self.assertFalse(d["body"])
                self.assertFalse(d["shackle"])
                self.assertFalse(d["keyhole"])
                self.assertTrue(d["planks"])
                self.assertTrue(d["shadow"])
                self.assertEqual(d["grads"], [])
                # O — glow (family hue) + ajar leaf, no slab fill, no lock
                d = self._render(kind, "O", 60, "S")
                self.assertEqual(len(d["grads"]), 1, "one radial glow")
                self.assertEqual(d["grads"][0]["stops"], [glow, glow,
                                                          "rgba(255,255,255,0)"])
                self.assertFalse(d["body"])
                self.assertFalse(d["shackle"])
                self.assertNotIn(slab, d["fills"],
                                 "O has no slab fill (only the leaf sliver)")
                self.assertTrue(d["leafFill"], "ajar leaf sliver")

    def test_s_tier_legible_at_8px(self):
        # AC11: s=8 — no planks (<12), no shadow (<14), no keyhole
        # (p=4 <10); slab 6x6, padlock p=4 for L only, glow for O only.
        for kind, slab in (("normal", "#9c6b3a"), ("safe", "#4f9e6b")):
            with self.subTest(kind=kind):
                dL = self._render(kind, "L", 8, "S")
                dU = self._render(kind, "U", 8, "S")
                dO = self._render(kind, "O", 8, "S")
                # slab present for L/U as the 6x6 fill
                for d in (dL, dU):
                    self.assertIn(slab, d["fills"])
                    self.assertFalse(d["planks"], "planks dropped at s=8")
                    self.assertFalse(d["shadow"], "shadow dropped at s=8")
                self.assertTrue(dL["body"] and dL["shackle"],
                                "4px padlock at s=8")
                self.assertFalse(dL["keyhole"], "keyhole dropped at s=8")
                self.assertFalse(dU["body"] and dU["shackle"])
                self.assertEqual(dL["fills"].count(slab),
                                 dU["fills"].count(slab),
                                 "L and U identical apart from the padlock")
                self.assertEqual(len(dO["grads"]), 1)
                self.assertEqual(dO["grads"][0]["stops"], [
                    "#ffe9a8" if kind == "normal" else "#c9f2d4",
                    "#ffe9a8" if kind == "normal" else "#c9f2d4",
                    "rgba(255,255,255,0)"])
                self.assertFalse(dO["body"])
                self.assertFalse(dO["leafFill"], "leaf dropped at s=8")

    def test_e_tier_all_six_states_greyed(self):
        # AC13/AC14: the E tier is a flat grey family — same grey slab for
        # BOTH families; the ePadlockMark (light grey, stroke AND fill) is
        # the locked signal; the glow is the near-white eLight; no brown,
        # green, brass, or shadow survives.
        for kind in ("normal", "safe"):
            with self.subTest(kind=kind):
                dL = self._render(kind, "L", 60, "E")
                dU = self._render(kind, "U", 60, "E")
                dO = self._render(kind, "O", 60, "E")
                for d in (dL, dU):
                    self.assertIn("#8a94a0", d["fills"], "grey slab")
                    self.assertTrue(d["planks"], "E planks use eSlabFrame")
                    self.assertFalse(d["shadow"], "no shadow at E")
                    self.assertNotIn("#9c6b3a", d["fills"], kind)
                    self.assertNotIn("#4f9e6b", d["fills"], kind)
                    self.assertNotIn("#e6b422", d["fills"], kind)
                # L keeps the faint padlock mark; U does not
                self.assertTrue(dL["mark"] and dL["markFill"])
                self.assertFalse(dU["mark"])
                self.assertFalse(dU["markFill"])
                self.assertFalse(dL["keyhole"], "no keyhole at E")
                # O — the near-white glow, no slab
                self.assertEqual(dO["grads"][0]["stops"], ["#e8ecf0",
                                                           "#e8ecf0",
                                                           "rgba(255,255,255,0)"])
                self.assertNotIn("#8a94a0", dO["fills"])

    def test_no_color_bleed_across_families(self):
        # normal art must never use the green tokens and vice versa.
        d = self._render("normal", "L", 40, "S")
        for bad in ("#4f9e6b", "#3c7d53", "#2f5c40", "#c9f2d4"):
            self.assertNotIn(bad, d["fills"])
        d2 = self._render("safe", "L", 40, "S")
        for bad in ("#9c6b3a", "#7a4f2a", "#5b4327", "#ffe9a8"):
            self.assertNotIn(bad, d2["fills"])


class TestDoorGridRender(FrontendBase):
    """AC9/AC13 — the doorway pass of drawGridOnCanvas routes to
    drawDoorCell: default-locked normal doors + a safe partition render the
    §5.1 palette (S / GM) and the §5.2 palette (E tier); the door cell keeps
    its floor base with NO wall hatch; hidden (H) doorways draw nothing."""

    def _normal_grid(self, doors_js, vis_js="null", safe_js="{}"):
        expr = (
            DOOR_GRID_CTX + "api.state.doors=" + doors_js + ";"
            "api.state.safe=" + safe_js + ";" + DOOR_CANVAS +
            "api.drawGridOnCanvas(api.els.canvas,c," + vis_js + ");"
            "const fills=c._fills.map(f=>f.style);"
            "const grads=c._gradients.map(g=>({cx:g.x1,cy:g.y1,"
            "stops:g.stops.map(s=>s[1])}));"
            "const slabRects=c._rects.filter(r=>r.w>=5&&r.h>=5);"
            "return {fills,grads,slabRects,"
            "mark:c._strokes.some(x=>x.style===api.T.ePadlockMark),"
            "shackle:c._strokes.some(x=>x.style===api.T.padlockShackle),"
            "body:c._fillPaths.some(fp=>fp.style===api.T.padlockBody)};})()"
        )
        return json.loads(js(expr))

    def test_s_tier_all_three_states_three_families(self):
        # (2,1)=O, (2,2)=U, (2,3)=L normal doors; the GM pass (no matrix)
        # renders full detail: slab fills for the two closed doors, a
        # yellow radial glow at (2,1), a padlock at (2,3), none at (2,2).
        d = self._normal_grid('{"2,1":"O","2,2":"U","2,3":"L"}')
        self.assertEqual(d["fills"].count("#9c6b3a"), 2,
                         "brown slabs at U and L")
        self.assertEqual(len(d["grads"]), 1, "one glow (the open door)")
        self.assertEqual(d["grads"][0]["stops"], ["#ffe9a8", "#ffe9a8",
                                                  "rgba(255,255,255,0)"])
        self.assertTrue(d["shackle"] and d["body"], "padlock on the L door")

    def test_default_locked_doors_render_locked_brown(self):
        # No map.doors at all: every door renders LOCKED brown (the safe
        # default) with padlocks.
        d = self._normal_grid("{}")
        self.assertEqual(d["fills"].count("#9c6b3a"), 3)
        self.assertEqual(len(d["grads"]), 0)
        self.assertTrue(d["body"] and d["shackle"])

    def test_safe_partition_renders_green(self):
        # (2,2) SAFE L (green slab + padlock); (2,1) and (2,3) are NORMAL
        # and unrecorded/locked -> brown slabs. The kind partition is total:
        # the safe cell renders green, the normal cells brown.
        d = self._normal_grid('{"2,3":"L"}', safe_js='{"2,2":"L"}')
        self.assertEqual(d["fills"].count("#4f9e6b"), 1,
                         "the safe door is the only green slab")
        self.assertEqual(d["fills"].count("#9c6b3a"), 2,
                         "the two normal doors are brown")
        self.assertEqual(len(d["grads"]), 0,
                         "all three doors are closed: no glow")
        self.assertTrue(d["body"] and d["shackle"],
                        "the padlocks (brown family brass) are drawn")

    def test_e_tier_renders_greyed(self):
        # A player matrix tiering the doors "E": (2,1)=U is in an S row
        # (full brown slab), (2,2)=O and safe (2,3)=L are in E rows: grey
        # slabs, the eLight glow on the open door, the ePadlockMark on the
        # locked one. NO S-tier wood/glow color may appear on the E cells
        # (only the S-row brown slab at (2,1) uses it).
        vis = json.dumps(["SSSSS", "SSSSS", "EEEEE", "EEEEE", "EEEEE"])
        d = self._normal_grid('{"2,1":"U","2,2":"O"}', vis,
                              safe_js='{"2,3":"L"}')
        # two grey slabs: (2,2) is open (no slab) ... so exactly ONE closed
        # E door -> the safe L at (2,3):
        self.assertEqual(d["fills"].count("#8a94a0"), 1,
                         "the safe L door is a grey slab at E")
        self.assertTrue(d["mark"], "E padlock mark on the safe L door")
        # the E-tier open glow is the near-white eLight (not the S yellow)
        self.assertEqual(len(d["grads"]), 1)
        self.assertEqual(d["grads"][0]["stops"], ["#e8ecf0", "#e8ecf0",
                                                  "rgba(255,255,255,0)"])
        # the S-row door (2,1) is the ONLY S-tier art: exactly one brown
        # slab. No safe/green wood, no brass, no S-tier yellow glow may
        # appear anywhere (the safe door is E-tier, the open door glows
        # eLight).
        self.assertEqual(d["fills"].count("#9c6b3a"), 1,
                         "only the S-row normal door is brown")
        for gone in ("#4f9e6b", "#3c7d53", "#2f5c40", "#c9f2d4",
                     "#e6b422", "#8a8f98"):
            self.assertNotIn(gone, d["fills"])

    def test_hidden_door_not_drawn(self):
        # An all-H matrix: nothing at all is drawn (no fills, no strokes,
        # no rects, no glow).
        vis = json.dumps(["HHHHH"] * 5)
        expr = (
            DOOR_GRID_CTX + 'api.state.doors={"2,1":"O"};'
            "api.state.role='player';" + DOOR_CANVAS +
            "api.drawGridOnCanvas(api.els.canvas,c," + vis + ");"
            "return {doorRects:c._rects.length, strokes:c._strokes.length,"
            "fills:c._fills.length, grads:c._gradients.length};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d, {"doorRects": 0, "strokes": 0, "fills": 0,
                             "grads": 0})

    def test_door_cell_keeps_floor_base_and_no_wall_hatch(self):
        # The door cell (2,1) is floor-based: the whole-grid floor base
        # fill covers it and NO wall hatch (a diagonal inside a wall rect)
        # falls on it.
        expr = (
            DOOR_GRID_CTX + 'api.state.doors={"2,1":"O"};' + DOOR_CANVAS +
            "api.drawGridOnCanvas(api.els.canvas,c,null);"
            "const inDoor=(p)=>p&&p[0]>=340&&p[0]<=460&&p[1]>=120&&"
            "p[1]<=240;"
            "const hatch=c._strokes.some(s=>s.path.some(seg=>{"
            "if(!seg.m||!seg.l)return false;"
            "const dx=Math.abs(seg.m[0]-seg.l[0]);"
            "const dy=Math.abs(seg.m[1]-seg.l[1]);"
            "return dx>0&&dy>0&&(inDoor(seg.m)||inDoor(seg.l));}));"
            "const floorBase=c._fills.find(f=>f.style==='#efe9dc'&&"
            "f.w===600&&f.h===600);"
            "return {hatch, floorBase:!!floorBase};})()"
        )
        d = json.loads(js(expr))
        self.assertTrue(d["floorBase"], "door cell sits on the floor base")
        self.assertFalse(d["hatch"], "a door cell must not get a wall hatch")


class TestLegendDoorSwatches(FrontendBase):
    """AC16a — renderLegendDoorSwatches turns every `.door-swatch` chip
    into a 16x16 canvas rendering the ACTUAL map art (floor base +
    drawDoorCell at tier S); the swatches are pixel-identical to the
    dispatcher (asserted per family/state), the function is idempotent,
    and showView("map") re-draws them."""

    _SETUP = (
        "(()=>{const legend=api.document.createElement('div');"
        "for(const [k,s] of [['normal','L'],['normal','U'],['normal','O'],"
        "['safe','L'],['safe','U'],['safe','O']]){const i=api.document"
        ".createElement('i');i.dataset={kind:k,state:s};"
        "legend.appendChild(i);}legend.querySelectorAll=(sel)=>sel==="
        "'.door-swatch'?Array.from(legend.children):[];"
        "api.els.legend=legend;"
    )

    def _render_all(self, extra=""):
        return json.loads(js(
            self._SETUP + "api.renderLegendDoorSwatches();" + extra +
            "const kids=[];"
            "for(const el of legend.children){const cnv=el.children[0];"
            "const cc=cnv.getContext('2d');"
            "kids.push({kind:el.dataset.kind,state:el.dataset.state,"
            "w:cnv.width,h:cnv.height,fills:cc._fills.map(f=>f.style),"
            "grads:cc._gradients.map(g=>g.stops.map(s=>s[1])),"
            "shackle:cc._strokes.some(x=>x.style===api.T.padlockShackle),"
            "body:cc._fillPaths.some(fp=>fp.style===api.T.padlockBody),"
            "leaf:cc._fillPaths.some(fp=>fp.style===api.T.woodBrown"
            "||fp.style===api.T.woodGreen)});}"
            "return kids;})()"
        ))

    def test_six_swatches_are_16x16_canvases(self):
        kids = self._render_all()
        self.assertEqual(len(kids), 6)
        for k in kids:
            self.assertEqual(k["w"], 16, (k["kind"], k["state"]))
            self.assertEqual(k["h"], 16, (k["kind"], k["state"]))
            # the floor base is drawn under the door art
            self.assertEqual(k["fills"][0], "#efe9dc")

    def test_swatch_art_matches_the_states(self):
        by = {(k["kind"], k["state"]): k for k in self._render_all()}
        # normal (brown family)
        self.assertIn("#9c6b3a", by[("normal", "L")]["fills"])
        self.assertTrue(by[("normal", "L")]["shackle"], "L padlock shackle")
        self.assertTrue(by[("normal", "L")]["body"], "L padlock body")
        self.assertIn("#9c6b3a", by[("normal", "U")]["fills"])
        self.assertFalse(by[("normal", "U")]["shackle"], "U no padlock")
        self.assertFalse(by[("normal", "U")]["body"])
        self.assertEqual(by[("normal", "O")]["grads"],
                         [["#ffe9a8", "#ffe9a8", "rgba(255,255,255,0)"]])
        self.assertTrue(by[("normal", "O")]["leaf"], "O ajar leaf")
        self.assertFalse(by[("normal", "O")]["body"])
        # safe (green family)
        self.assertIn("#4f9e6b", by[("safe", "L")]["fills"])
        self.assertTrue(by[("safe", "L")]["shackle"], "safe L padlock")
        self.assertTrue(by[("safe", "L")]["body"])
        self.assertIn("#4f9e6b", by[("safe", "U")]["fills"])
        self.assertFalse(by[("safe", "U")]["shackle"])
        self.assertFalse(by[("safe", "U")]["body"])
        self.assertEqual(by[("safe", "O")]["grads"],
                         [["#c9f2d4", "#c9f2d4", "rgba(255,255,255,0)"]])
        self.assertTrue(by[("safe", "O")]["leaf"])

    def test_idempotent_and_redrawn_by_show_view_map(self):
        # A second render adds no duplicate canvases; showView("map") is
        # the production call site and is idempotent too.
        out = self._render_all(
            "api.renderLegendDoorSwatches();api.showView('map');")
        for k in out:
            self.assertEqual(k["w"], 16)
        # each chip holds exactly ONE canvas (idempotency)
        out2 = json.loads(js(self._SETUP +
                             "api.renderLegendDoorSwatches();"
                             "api.renderLegendDoorSwatches();"
                             "api.showView('map');"
                             "return Array.from(legend.children)"
                             ".map(el=>el.children.length);})()"))
        self.assertEqual(out2, [1, 1, 1, 1, 1, 1])


class TestDoorGmTool(FrontendBase):
    """AC11c — GM Door tool (UNCHANGED by the redesign), driven through the
    REAL #paint-group click listener: selecting the tool + an action, then
    clicking a door cell, sends {type:"door", x, y, action}. Clicking a
    non-door cell sends nothing; the sub-row is only visible while armed;
    the control hint follows the armed action."""

    _MAP_JS = GRID5_JS

    def _gm_ctx(self):
        return (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            "map,entities:[],players:[],awareness:[],fog:false});"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "api.state.cell=120;api.state.offsetX=100;api.state.offsetY=0;"
        )

    def test_door_tool_select_action_and_dispatch(self):
        for action in ("unlock", "lock", "open", "close"):
            with self.subTest(action=action):
                expr = (
                    self._gm_ctx() +
                    "const pg=api.document.querySelector('#paint-group');"
                    "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
                    "s==='.tool-btn'?{dataset:{tool:'door'}}:null}});"
                    "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
                    "s==='.door-action'?{dataset:{doorAction:'"
                    + action + "'}}:null}});"
                    "const tool=api.state.tool, act=api.state.doorAction,"
                    "hint=api.els.controlHint.textContent,"
                    "rowHidden=api.els.doorActionRow.hidden;"
                    "api._send.reset();"
                    "api.els.canvas.dispatchEvent({type:'click',"
                    "clientX:400,clientY:180});"           # door (2,1)
                    "const sent=api._send.sent.slice();"
                    "api._send.reset();"
                    "api.els.canvas.dispatchEvent({type:'click',"
                    "clientX:220,clientY:180});"           # floor (1,1)
                    "const sentFloor=api._send.sent;"
                    "return {tool,act,hint,rowHidden,sent,"
                    "sentFloor};})()"
                )
                d = json.loads(js(expr))
                self.assertEqual(d["tool"], "door")
                self.assertEqual(d["act"], action)
                self.assertEqual(d["hint"], f"Click a door to {action}")
                self.assertFalse(d["rowHidden"])
                self.assertEqual(d["sent"],
                                 [{"type": "door", "x": 2, "y": 1,
                                   "action": action}])
                self.assertEqual(d["sentFloor"], [])

    def test_sub_row_hidden_when_not_on_door_tool(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'door'}}:null}});"
            "const on=api.els.doorActionRow.hidden;"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'wall'}}:null}});"
            "const off=api.els.doorActionRow.hidden;"
            "const hint=api.els.controlHint.textContent;"
            "return {on,off,hint};})()"
        )
        d = json.loads(js(expr))
        self.assertFalse(d["on"])
        self.assertTrue(d["off"])
        self.assertEqual(d["hint"], "Drag on the map to paint wall")

    def test_default_action_is_unlock(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'door'}}:null}});"
            "return {act:api.state.doorAction,"
            "hint:api.els.controlHint.textContent};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["act"], "unlock")
        self.assertEqual(d["hint"], "Click a door to unlock")

    def test_player_has_no_door_tool(self):
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "api.state.cell=120;api.state.offsetX=100;api.state.offsetY=0;"
            "api.state.tool='door';"
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:400,clientY:180});"
            "return {sent:api._send.sent};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"], [])


class TestPlayerDoorTap(FrontendBase):
    """AC11d — a player (select tool) taps a doorway cell and the client
    sends the inverse action: L -> open (the server's "door is locked"
    error surfaces via the existing {type:'error'} toast path), U -> open,
    O -> close. A tap on a cell with an entity is NOT a door action, and a
    floor tap still moves. UNCHANGED by the redesign."""

    _MAP_JS = GRID5_JS

    def _player_ctx(self):
        return (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:2},players:[],awareness:[],fog:false});"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "api.state.cell=120;api.state.offsetX=100;api.state.offsetY=0;"
        )

    def _tap_door(self, st: str) -> str:
        return (
            self._player_ctx() +
            "api.state.doors={'2,2':'" + st + "'};"
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:400,clientY:300});"
            "return {sent:api._send.sent};})()"
        )

    def test_tap_locked_door_sends_open(self):
        d = json.loads(js(self._tap_door("L")))
        self.assertEqual(d["sent"],
                         [{"type": "door", "x": 2, "y": 2, "action": "open"}])

    def test_tap_closed_unlocked_door_sends_open(self):
        d = json.loads(js(self._tap_door("U")))
        self.assertEqual(d["sent"],
                         [{"type": "door", "x": 2, "y": 2, "action": "open"}])

    def test_tap_open_door_sends_close(self):
        d = json.loads(js(self._tap_door("O")))
        self.assertEqual(d["sent"],
                         [{"type": "door", "x": 2, "y": 2, "action": "close"}])

    def test_tap_default_locked_door(self):
        expr = (
            self._player_ctx() +
            "api.state.doors={};"
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:400,clientY:300});"
            "return {sent:api._send.sent};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"],
                         [{"type": "door", "x": 2, "y": 2, "action": "open"}])

    def test_locked_door_error_toast_path(self):
        expr = (
            self._player_ctx() +
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:400,clientY:300});"
            "const spans=[];"
            "const doc=api.document;const realCreate=doc.createElement;"
            "doc.createElement=(t)=>{const el=realCreate(t);"
            "if(t==='span')spans.push(()=>el.textContent);"
            "if(t==='div')spans.push(()=>el.className);return el};"
            "api.onError({type:'error',message:'door is locked'});"
            "doc.createElement=realCreate;"
            "return {sent:api._send.sent, toasts:spans.map(f=>f())};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"],
                         [{"type": "door", "x": 2, "y": 2, "action": "open"}])
        self.assertIn("toast-error", d["toasts"])
        self.assertIn("door is locked", d["toasts"])

    def test_tap_own_token_cell_does_not_act_on_door(self):
        expr = (
            self._player_ctx() +
            'api.state.doors={"2,2":"O"};'
            "api.state.youEntity.x=2;api.state.youEntity.y=2;"
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:400,clientY:300});"
            "return {sent:api._send.sent,"
            "sel:api.state.selectedEntityId};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"], [])
        self.assertEqual(d["sel"], "e2")

    def test_tap_floor_cell_still_moves(self):
        expr = (
            self._player_ctx() +
            'api.state.doors={"2,2":"O"};'
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:520,clientY:300});"
            "return {sent:api._send.sent.map(m=>m.type)};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"], ["move"])


class TestDoorPaintInteraction(FrontendBase):
    """§9 — painting a doorway cell still sends a paint and re-types the
    cell; the DOOR tool never emits paint frames; painting floor over a
    door removes the door art via the broadcast. UNCHANGED."""

    _MAP_JS = json.dumps({"name": "m", "width": 4, "height": 3,
                          "cells": [["floor"] * 4 for _ in range(3)]})

    def _gm_ctx(self):
        return (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            "map,entities:[],players:[],awareness:[],fog:false});"
        )

    def test_paint_doorway_still_sends_paint(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'doorway'}}:null}});"
            "api._send.reset();"
            "api.paintCell(1,1);"
            "const sent=api._send.sent;"
            "const cell=api.state.grid.cells[1][1];"
            "api.paintCell(1,1);"
            "const dup=api._send.sent.length;"
            "return {sent,cell,dup};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"],
                         [{"type": "paint", "x": 1, "y": 1,
                           "cell_type": "doorway"}])
        self.assertEqual(d["cell"], "doorway")
        self.assertEqual(d["dup"], 1)

    def test_door_tool_does_not_emit_paint_frames(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'door'}}:null}});"
            "api.state.grid.cells[1][1]='doorway';"
            "api.state.doors={'1,1':'O'};"
            "api._send.reset();"
            "api.paintCell(1,1);"
            "return {sent:api._send.sent,"
            "cell:api.state.grid.cells[1][1],"
            "state:api.doorStateAt(1,1)};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"], [])
        self.assertEqual(d["cell"], "doorway")
        self.assertEqual(d["state"], "O")

    def test_floor_paint_over_door_removes_door_art(self):
        expr = (
            self._gm_ctx() +
            "api.state.grid.cells[1][1]='doorway';"
            "api.state.doors={'1,1':'O'};"
            "const had=api.doorStateAt(1,1);"
            "api.onState({type:'state',map:" + self._MAP_JS + "});"
            "api.state.grid.cells[1][1]='floor';"
            "return {had, door:api.doorStateAt(1,1),"
            "doors:api.state.doors};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["had"], "O")
        self.assertIsNone(d["door"])
        self.assertEqual(d["doors"], {})


class TestDoorHints(FrontendBase):
    """§7.7 — control-hint copy for the Door tool (UNCHANGED)."""

    def test_gm_door_tool_hint(self):
        out = js(
            "(()=>{api.state.joined=true;api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.state.selectedEntityId=null;api.state.entities=[];"
            "api.state.tool='door';api.state.doorAction='lock';"
            "api.updateControlHint();"
            "return api.els.controlHint.textContent;})()"
        )
        self.assertIn("Click a door to lock", out)

    def test_player_hint_mentions_doors(self):
        out = js(
            "(()=>{api.state.joined=true;api.state.role='player';"
            "api.state.you={id:'p2',name:'Alice',role:'player',"
            "entity_id:'e2'};api.state.tool='select';"
            "api.updateControlHint();"
            "return api.els.controlHint.textContent;})()"
        )
        self.assertIn("tap a door", out)
        self.assertIn("open/close", out)
        self.assertIn("Tap a tile to move", out)


# ══════════════════════════════════════════════════════════════════════
# Safe-room doors (safe-room doors spec §7 + door-iconography spec §7–§8)
# ══════════════════════════════════════════════════════════════════════
# A safe-room door is a `doorway` cell recorded in `map.safe` (an additive
# wire object "<x>,<y>" -> "L"|"U"|"O" — the SAME three-state model as
# normal doors — that partitions the doorway cells with `map.doors`). It
# renders as a GREEN WOODEN DOOR over the floor base (drawDoorCell kind
# "safe"); a legacy "C" from a stale server coerces to "U" in validateSafe,
# and an unrecorded safe-door cell defaults to "L" (locked) in
# safeDoorStateAt. The GM gets a 🛡 Safe door tool with SIX sub-buttons —
# Mark/Unmark/Unlock/Lock/Open/Close — and clicking a doorway cell sends
# {type:"safe_door", x, y, action} (GM-only). A PLAYER tap on a safe-door
# cell is a NO-OP (safe doors are GM-controlled; the !== "O" hint is
# correct for L and U).

class TestSafeDoorStatic(FrontendBase):
    """AC16b — the real index.html / style.css carry the GM Safe door tool
    with SIX `data-safe-action` sub-buttons (mark/unmark/unlock/lock/open/
    close) and the §5.3 CSS tokens; the six `.door-swatch` legend chips are
    visible to BOTH roles (no body.is-gm gate); the old green-cross chip is
    gone and the .safe-action underline now uses the wood green."""

    def setUp(self):
        with open(INDEX, encoding="utf-8") as fh:
            self.html = fh.read()
        css_path = os.path.join(os.path.dirname(INDEX), "style.css")
        with open(css_path, encoding="utf-8") as fh:
            self.css = fh.read()

    def test_paint_group_has_safe_door_tool_and_six_sub_buttons(self):
        self.assertIn(
            '<button class="tool-btn" data-tool="safeDoor" '
            'aria-pressed="false">🛡 Safe door</button>',
            self.html)
        self.assertIn('id="safe-action-row"', self.html)
        for action in ("mark", "unmark", "unlock", "lock", "open", "close"):
            self.assertIn(f'data-safe-action="{action}"', self.html,
                          f"missing safe-action {action}")
        # exactly six sub-buttons, in the spec order
        self.assertEqual(self.html.count('class="safe-action"'), 6)
        self.assertLess(self.html.index('data-safe-action="unmark"'),
                        self.html.index('data-safe-action="unlock"'))
        self.assertLess(self.html.index('data-safe-action="unlock"'),
                        self.html.index('data-safe-action="lock"'))
        # the existing tools are unchanged (regression guard)
        for tool in ('data-tool="select"', 'data-tool="floor"',
                     'data-tool="wall"', 'data-tool="doorway"',
                     'data-tool="door"'):
            self.assertIn(tool, self.html)

    def test_legend_six_door_swatches_present_and_ungated(self):
        # The six state chips (three normal + three safe) replace the old
        # amber/red + green-cross chips.
        self.assertIn('<span class="legend-sep legend-safe">|</span>',
                      self.html)
        for kind, cls, label in (("normal", "doors", "door"),
                                 ("safe", "safe", "safe")):
            for st, stlabel in (("L", "locked"), ("U", "closed"),
                                ("O", "open")):
                self.assertIn(
                    f'<span class="legend-chip legend-{cls}">'
                    f'<i class="door-swatch" data-kind="{kind}" '
                    f'data-state="{st}"></i>{label} · {stlabel}</span>',
                    self.html)
        self.assertEqual(self.html.count("legend-chip legend-safe"), 3)
        self.assertEqual(self.html.count("legend-chip legend-doors"), 3)
        self.assertEqual(self.html.count('class="door-swatch"'), 6)
        # the old chips are GONE
        for gone in ('<i class="swatch door-open"></i>',
                     '<i class="swatch door-unlocked"></i>',
                     '<i class="swatch door-locked"></i>',
                     '<i class="swatch safe-door"></i>'):
            self.assertNotIn(gone, self.html)
        # the pre-existing chips are unchanged
        for chip in (
            '<i class="swatch floor"></i>floor',
            '<i class="swatch wall"></i>wall',
            '<i class="swatch doorway"></i>doorway',
        ):
            self.assertIn(chip, self.html)

    def test_legend_safe_chip_is_not_gm_gated(self):
        self.assertIn("body.is-gm .legend-explored { display: none; }",
                      self.css)
        self.assertNotIn(".legend-safe", self.css)

    def test_css_safe_tokens_and_swatch_styles(self):
        for token in ("--door-wood-green: #4f9e6b",
                      "--door-wood-green-dark: #3c7d53",
                      "--door-light-green: #c9f2d4",
                      "--door-frame-green: #2f5c40"):
            self.assertIn(token, self.css)
        # the safe-door cursor mode is wired, the pressed .safe-action
        # underline uses the wood green (the old --safe-open token is gone)
        self.assertIn("mode-paint-safeDoor", self.css)
        self.assertIn("border-bottom-color: var(--door-wood-green);",
                      self.css)
        for gone in ("--safe-open", "--explored-safe-open",
                     ".swatch.safe-door"):
            self.assertNotIn(gone, self.css)


class TestSafeDoorStateModel(FrontendBase):
    """AC11b + §7.1 — state.safe is set from msg.map.safe in applyState
    ({} when absent), accepts L/U/O, coerces the LEGACY "C" to "U", and
    treats every other malformed payload as {} — never crash."""

    _MAP = ({"name": "m", "width": 5, "height": 4,
             "cells": [["floor"] * 5 for _ in range(4)]})

    def _welcome_safe(self, safe_js: str) -> str:
        return (
            "(()=>{const map=" + json.dumps(self._MAP) + ";"
            "map.safe=" + safe_js + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "return api.state.safe;})()"
        )

    def test_absent_safe_defaults_to_empty_object(self):
        self.assertEqual(json.loads(js(self._welcome_safe("undefined"))), {})

    def test_null_safe_defaults_to_empty_object(self):
        self.assertEqual(json.loads(js(self._welcome_safe("null"))), {})

    def test_valid_safe_object_stored(self):
        out = js(self._welcome_safe("{'1,2':'L','3,0':'O','0,3':'U'}"))
        self.assertEqual(json.loads(out),
                         {"1,2": "L", "3,0": "O", "0,3": "U"})

    def test_legacy_c_coerced_to_u(self):
        # A stale pre-redesign server may still send "C" for a closed safe
        # door: validateSafe migrates it to "U" (never renders "C").
        out = js(self._welcome_safe("{'1,2':'C','3,0':'O'}"))
        self.assertEqual(json.loads(out), {"1,2": "U", "3,0": "O"})

    def test_malformed_safe_treated_as_empty(self):
        for bad in ("[]", "'C'", "5", "{'1x':'L'}", "{'1,2':'X'}",
                    "{'1,2':'L','0,0':'Z'}", "true"):
            with self.subTest(bad=bad):
                self.assertEqual(json.loads(js(self._welcome_safe(bad))),
                                 {}, bad)

    def test_state_broadcast_replaces_safe(self):
        out = js(
            "(()=>{const map=" + json.dumps(self._MAP) + ";"
            "map.safe={'2,2':'O'};"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "api.state.grid.cells[2][2]='doorway';"
            "const hadOpen=api.isSafeDoor(2,2);"
            "const m2=Object.assign({},map);m2.safe={};"
            "api.onState({type:'state',map:m2,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "return {hadOpen, now:api.isSafeDoor(2,2),"
            "all:api.state.safe};})()"
        )
        d = json.loads(out)
        self.assertTrue(d["hadOpen"])
        self.assertFalse(d["now"])
        self.assertEqual(d["all"], {})


class TestSafeDoorStateAt(FrontendBase):
    """AC11b + §7.1 — isSafeDoor / safeDoorStateAt: a safe door is a
    `doorway` cell recorded in state.safe; the state is "L"|"U"|"O" and the
    DEFAULT for a recorded key with no value is now "L" (locked — the
    secure default). Non-doorway and non-recorded cells are not safe
    doors (null), even with a stale key."""

    _SETUP = (
        "api.state.grid={width:4,height:3,cells:["
        "['floor','doorway','floor','floor'],"
        "['wall','doorway','wall','floor'],"
        "['floor','floor','doorway','floor']]}"
    )

    def test_recorded_states_win(self):
        out = js("(()=>{" + self._SETUP + ";"
                 ';api.state.safe={"1,0":"O","2,2":"L"};'
                 'return {o:api.safeDoorStateAt(1,0), l:api.safeDoorStateAt(2,2),'
                 'is10:api.isSafeDoor(1,0), is22:api.isSafeDoor(2,2)};})()')
        d = json.loads(out)
        self.assertEqual(d["o"], "O")
        self.assertEqual(d["l"], "L")
        self.assertTrue(d["is10"])
        self.assertTrue(d["is22"])

    def test_unrecorded_value_defaults_to_locked(self):
        # A safe-door cell present in state.safe but with no recorded value
        # (the server omits default entries) defaults to "L" — NOT "C".
        out = js("(()=>{" + self._SETUP + ";"
                 ';api.state.safe={"1,0":null,"2,2":undefined};'
                 'return {a:api.safeDoorStateAt(1,0),'
                 'b:api.safeDoorStateAt(2,2)};})()')
        d = json.loads(out)
        self.assertEqual(d["a"], "L")
        self.assertEqual(d["b"], "L")

    def test_non_recorded_doorway_is_not_safe(self):
        out = js("(()=>{" + self._SETUP + ";"
                 ';api.state.safe={"1,0":"O"};'
                 'return {st:api.safeDoorStateAt(1,1), is:api.isSafeDoor(1,1)};})()')
        d = json.loads(out)
        self.assertIsNone(d["st"])
        self.assertFalse(d["is"])

    def test_non_doorway_cell_has_no_safe_door(self):
        out = js("(()=>{" + self._SETUP + ";"
                 ';api.state.safe={"0,0":"O","0,1":"L","3,9":"L"};'
                 'return {f:api.isSafeDoor(0,0), w:api.isSafeDoor(0,1),'
                 'oob:api.isSafeDoor(3,9)};})()')
        d = json.loads(out)
        self.assertFalse(d["f"])
        self.assertFalse(d["w"])
        self.assertFalse(d["oob"])


class TestSafeDoorGmTool(FrontendBase):
    """AC11c — GM Safe door tool, driven through the REAL #paint-group
    click listener: all SIX actions (Mark/Unmark/Unlock/Lock/Open/Close)
    arm and dispatch {type:"safe_door", x, y, action}. Clicking a
    non-doorway cell sends nothing; the render state is never
    optimistic-mutated; the sub-row is only visible while armed; the
    control hint follows the armed action (now incl. lock/unlock)."""

    _MAP_JS = GRID5_JS

    def _gm_ctx(self):
        return (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            "map,entities:[],players:[],awareness:[],fog:false});"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "api.state.cell=120;api.state.offsetX=100;api.state.offsetY=0;"
        )

    def test_safe_tool_select_action_and_dispatch(self):
        for action in ("mark", "unmark", "unlock", "lock", "open", "close"):
            with self.subTest(action=action):
                expr = (
                    self._gm_ctx() +
                    "const pg=api.document.querySelector('#paint-group');"
                    "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
                    "s==='.tool-btn'?{dataset:{tool:'safeDoor'}}:null}});"
                    "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
                    "s==='.safe-action'?{dataset:{safeAction:'" + action + "'}}"
                    ":null}});"
                    "const tool=api.state.tool, act=api.state.safeAction,"
                    "hint=api.els.controlHint.textContent,"
                    "rowHidden=api.els.safeActionRow.hidden,"
                    "doorRowHidden=api.els.doorActionRow.hidden;"
                    "api._send.reset();"
                    "api.els.canvas.dispatchEvent({type:'click',"
                    "clientX:400,clientY:180});"           # door (2,1)
                    "const sent=api._send.sent.slice();"
                    "api._send.reset();"
                    "api.els.canvas.dispatchEvent({type:'click',"
                    "clientX:220,clientY:180});"           # floor (1,1)
                    "const sentFloor=api._send.sent;"
                    "return {tool,act,hint,rowHidden,doorRowHidden,sent,"
                    "sentFloor, safe:api.state.safe};})()"
                )
                d = json.loads(js(expr))
                self.assertEqual(d["tool"], "safeDoor")
                self.assertEqual(d["act"], action)
                self.assertEqual(d["hint"], f"Click a doorway to {action}")
                self.assertFalse(d["rowHidden"])
                self.assertTrue(d["doorRowHidden"])
                self.assertEqual(
                    d["sent"],
                    [{"type": "safe_door", "x": 2, "y": 1,
                      "action": action}])
                self.assertEqual(d["sentFloor"], [])
                # no optimistic safe-door mutation
                self.assertEqual(d["safe"], {})

    def test_sub_row_hidden_when_not_on_safe_tool(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'safeDoor'}}:null}});"
            "const on=api.els.safeActionRow.hidden;"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'wall'}}:null}});"
            "const off=api.els.safeActionRow.hidden;"
            "const hint=api.els.controlHint.textContent;"
            "return {on,off,hint};})()"
        )
        d = json.loads(js(expr))
        self.assertFalse(d["on"])
        self.assertTrue(d["off"])
        self.assertEqual(d["hint"], "Drag on the map to paint wall")

    def test_default_action_is_mark(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'safeDoor'}}:null}});"
            "return {act:api.state.safeAction,"
            "hint:api.els.controlHint.textContent};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["act"], "mark")
        self.assertEqual(d["hint"], "Click a doorway to mark")

    def test_player_has_no_safe_tool(self):
        expr = (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:1},players:[],awareness:[],fog:false});"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "api.state.cell=120;api.state.offsetX=100;api.state.offsetY=0;"
            "api.state.tool='safeDoor';"
            "api._send.reset();"
            "api.els.canvas.dispatchEvent({type:'click',"
            "clientX:400,clientY:180});"
            "return {sent:api._send.sent};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"], [])


class TestPlayerSafeDoorTap(FrontendBase):
    """AC11d — a player (select tool) taps a SAFE-door cell: NO-OP (no
    door frame, no move, no safe_door frame — safe doors are GM-
    controlled) for BOTH closed states (L locked / U unlocked-closed); an
    open safe door tap is still a no-op door-wise (walkable, not an action
    target). Tapping their OWN token standing on an open safe door still
    re-asserts selection; tapping a NORMAL door still sends the inverse
    `door` action (regression)."""

    _MAP_JS = GRID5_JS

    def _player_ctx(self):
        return (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            "map,entities:[],"
            "you_entity:{id:'e2',name:'Alice',kind:'player',team:'party',"
            "x:1,y:2},players:[],awareness:[],fog:false});"
            "api.els.canvas.width=800;api.els.canvas.height=600;"
            "api.state.cell=120;api.state.offsetX=100;api.state.offsetY=0;"
        )

    def test_tap_locked_safe_door_sends_nothing(self):
        d = json.loads(js(self._player_ctx() +
                          'api.state.safe={"2,2":"L"};'
                          "api._send.reset();"
                          "api.els.canvas.dispatchEvent({type:'click',"
                          "clientX:400,clientY:300});"      # safe (2,2)
                          "return {sent:api._send.sent};})()"))
        self.assertEqual(d["sent"], [])

    def test_tap_closed_unlocked_safe_door_sends_nothing(self):
        d = json.loads(js(self._player_ctx() +
                          'api.state.safe={"2,2":"U"};'
                          "api._send.reset();"
                          "api.els.canvas.dispatchEvent({type:'click',"
                          "clientX:400,clientY:300});"      # safe (2,2)
                          "return {sent:api._send.sent};})()"))
        self.assertEqual(d["sent"], [])

    def test_tap_open_safe_door_sends_nothing(self):
        d = json.loads(js(self._player_ctx() +
                          'api.state.safe={"2,2":"O"};'
                          "api._send.reset();"
                          "api.els.canvas.dispatchEvent({type:'click',"
                          "clientX:400,clientY:300});"      # safe (2,2)
                          "return {sent:api._send.sent};})()"))
        self.assertEqual(d["sent"], [])

    def test_tap_safe_cell_with_own_token_reasserts_selection(self):
        d = json.loads(js(self._player_ctx() +
                          'api.state.safe={"2,2":"O"};'
                          "api.state.youEntity.x=2;api.state.youEntity.y=2;"
                          "api._send.reset();"
                          "api.els.canvas.dispatchEvent({type:'click',"
                          "clientX:400,clientY:300});"
                          "return {sent:api._send.sent,"
                          "sel:api.state.selectedEntityId};})()"))
        self.assertEqual(d["sent"], [])
        self.assertEqual(d["sel"], "e2")

    def test_tap_normal_door_still_sends_inverse_action(self):
        d = json.loads(js(self._player_ctx() +
                          'api.state.safe={};'
                          'api.state.doors={"2,2":"O"};'
                          "api._send.reset();"
                          "api.els.canvas.dispatchEvent({type:'click',"
                          "clientX:400,clientY:300});"
                          "return {sent:api._send.sent};})()"))
        self.assertEqual(d["sent"],
                         [{"type": "door", "x": 2, "y": 2,
                           "action": "close"}])


class TestSafeDoorPaintInteraction(FrontendBase):
    """§9 — the Safe door tool is a paint-MODE tool like the Door tool:
    paintCell under tool="safeDoor" is a no-op (safe state is only ever
    changed by {type:"safe_door"} frames or the broadcast), and painting
    floor/wall over a safe door removes the safe art once the broadcast
    lands. UNCHANGED."""

    _MAP_JS = json.dumps({"name": "m", "width": 4, "height": 3,
                          "cells": [["floor"] * 4 for _ in range(3)]})

    def _gm_ctx(self):
        return (
            "(()=>{const map=" + self._MAP_JS + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'Gamer',role:'gm',entity_id:null},"
            "map,entities:[],players:[],awareness:[],fog:false});"
        )

    def test_safe_tool_does_not_emit_paint_frames(self):
        expr = (
            self._gm_ctx() +
            "const pg=api.document.querySelector('#paint-group');"
            "pg.dispatchEvent({type:'click',target:{closest:(s)=>"
            "s==='.tool-btn'?{dataset:{tool:'safeDoor'}}:null}});"
            "api.state.grid.cells[1][1]='doorway';"
            "api.state.safe={'1,1':'O'};"
            "api._send.reset();"
            "api.paintCell(1,1);"
            "return {sent:api._send.sent,"
            "cell:api.state.grid.cells[1][1],"
            "isSafe:api.isSafeDoor(1,1)};})()"
        )
        d = json.loads(js(expr))
        self.assertEqual(d["sent"], [])
        self.assertEqual(d["cell"], "doorway")
        self.assertTrue(d["isSafe"])

    def test_floor_paint_over_safe_door_removes_safe_art(self):
        expr = (
            self._gm_ctx() +
            "api.state.grid.cells[1][1]='doorway';"
            "api.state.safe={'1,1':'O'};"
            "const had=api.isSafeDoor(1,1);"
            "api.onState({type:'state',map:" + self._MAP_JS + "});"
            "api.state.grid.cells[1][1]='floor';"
            "return {had, safe:api.isSafeDoor(1,1),"
            "all:api.state.safe};})()"
        )
        d = json.loads(js(expr))
        self.assertTrue(d["had"])
        self.assertFalse(d["safe"])
        self.assertEqual(d["all"], {})


class TestSafeDoorHints(FrontendBase):
    """§7.7 — control-hint copy for the Safe door tool (now also
    interpolates lock/unlock)."""

    def test_gm_safe_tool_hint(self):
        out = js(
            "(()=>{api.state.joined=true;api.state.role='gm';"
            "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
            "api.state.selectedEntityId=null;api.state.entities=[];"
            "api.state.tool='safeDoor';api.state.safeAction='unmark';"
            "api.updateControlHint();"
            "return api.els.controlHint.textContent;})()"
        )
        self.assertIn("Click a doorway to unmark", out)

    def test_gm_safe_tool_hint_lock_unlock(self):
        for action in ("lock", "unlock"):
            out = js(
                "(()=>{api.state.joined=true;api.state.role='gm';"
                "api.state.you={id:'p1',name:'G',role:'gm',entity_id:null};"
                "api.state.selectedEntityId=null;api.state.entities=[];"
                "api.state.tool='safeDoor';api.state.safeAction='"
                + action + "';"
                "api.updateControlHint();"
                "return api.els.controlHint.textContent;})()"
            )
            self.assertIn(f"Click a doorway to {action}", out)



# ═══════════════════════════════════════════════════════════════════════════
# Pan & Zoom (docs/design/pan-zoom.md) — AC1..AC22 driven by the Node harness.
# The stub DOM carries the six nav buttons (clickable, disabled/title), a
# controllable #canvas-wrap size, pointer→clientX/Y events, document/window
# listener dispatch (keyboard + resize) and a flushable requestAnimationFrame.
# ═══════════════════════════════════════════════════════════════════════════

# Frozen §5.1 level table + §2.4 step table (W×H, stepX, stepY) per level.
PZ_LEVELS = [(6, 5), (8, 7), (10, 8), (13, 11), (16, 13), (20, 17),
             (25, 21), (30, 25), (40, 33), (50, 42), (60, 50)]
PZ_STEP_X = [1, 1, 1, 1, 2, 2, 3, 3, 4, 5, 6]
PZ_STEP_Y = [1, 1, 1, 1, 1, 2, 2, 3, 3, 4, 5]


def _floor_map_js(w, h):
    """A compact JS object literal for a w×h all-floor map."""
    return ("{name:'m',width:%d,height:%d,cells:Array.from({length:%d},"
            "()=>Array(%d).fill('floor'))}" % (w, h, h, w))


class TestPanZoom(FrontendBase):
    """Pan & Zoom (docs/design/pan-zoom.md) AC1..AC22.

    The harness re-exports the real view-math (LEVELS, fitLevel, viewStep,
    viewBounds, applyView, panBy, zoomBy, fitToMap, syncNavControls,
    focusInField, cellFromEvent) and carries the six nav buttons plus a
    controllable canvas-wrap size, so these tests run the REAL app.js code.
    """

    # Shared JS fragments.
    _BTN = (
        "({L:api.els.navLeft.disabled,lT:api.els.navLeft.title,"
        "R:api.els.navRight.disabled,rT:api.els.navRight.title,"
        "U:api.els.navUp.disabled,uT:api.els.navUp.title,"
        "D:api.els.navDown.disabled,dT:api.els.navDown.title,"
        "Zi:api.els.zoomIn.disabled,zit:api.els.zoomIn.title,"
        "Zo:api.els.zoomOut.disabled,zot:api.els.zoomOut.title,"
        "ro:api.els.navReadout.textContent})")

    def _geo(self):
        return ("({cell:api.state.cell,ox:api.state.offsetX,"
                "oy:api.state.offsetY,v:api.state._view,view:api.state.view})")

    def _gm_welcome(self, w, h, entities_js="[]"):
        return (
            "(()=>{const map=" + _floor_map_js(w, h) + ";"
            "api.onWelcome({type:'welcome',"
            "you:{id:'p1',name:'G',role:'gm',entity_id:null},"
            "map,entities:" + entities_js + ",players:[],"
            "awareness:[],fog:false});")

    def _vset(self, level, px, py, avail=(800, 400)):
        """JS to set the view level+pan and the canvas-wrap size, then
        re-layout (applyView) + sync the nav controls."""
        cw, ch = avail[0] + 16, avail[1] + 16
        return (
            "api.state.view.level=%d;api.state.view.panX=%d;api.state.view.panY=%d;"
            "api.els.canvasWrap.clientWidth=%d;api.els.canvasWrap.clientHeight=%d;"
            "api.layoutCanvas();api.syncNavControls();" % (level, px, py, cw, ch))

    # ── AC7: level table conformance + cell formula ───────────────────────
    def test_ac7_level_table_and_cell_formula(self):
        # On a 60×60 map, for every L0..L10 the visible window equals the
        # frozen W×H and cell obeys max(1, floor(min(availW/W, availH/H))).
        expr = (
            self._gm_welcome(60, 60)
            + "api.els.canvasWrap.clientWidth=816;"
            + "api.els.canvasWrap.clientHeight=416;"
            + "const out=[];"
            + "for(let L=0;L<11;L++){"
            + "api.state.view.level=L;api.state.view.panX=0;"
            + "api.state.view.panY=0;api.layoutCanvas();"
            + "const v=api.state._view;"
            + "out.push({L,W:v.W,H:v.H,cell:v.s});}"
            + "return out;})()")
        got = json.loads(js(expr))
        self.assertEqual(len(got), 11)
        for i, row in enumerate(got):
            w, h = PZ_LEVELS[i]
            self.assertEqual((row["W"], row["H"]), (w, h), "level %d" % i)
            # cell = max(1, floor(min(800/W, 400/H)))
            self.assertEqual(row["cell"],
                             max(1, int(min(800 / w, 400 / h))),
                             "level %d" % i)

    # ── AC8: square cells + letterboxing (exact numbers) ──────────────────
    def test_ac8_square_cells_and_letterbox(self):
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(0, 0, 0, (800, 400))
            + "const a=" + self._geo() + ";"
            + self._vset(10, 0, 0, (800, 400))
            + "const b=" + self._geo() + ";"
            + "return {a,b};})()"))
        a, b = out["a"], out["b"]
        # L0 (6×5): cell 80 (400/5 < 800/6), offsetX (800-480)/2=160, oy 0.
        self.assertEqual(a["cell"], 80)
        self.assertEqual(a["ox"], 160)
        self.assertEqual(a["oy"], 0)
        self.assertEqual((a["v"]["W"], a["v"]["H"]), (6, 5))
        # L10 (60×50): cell 8 (floor(min(800/60, 400/50))), offsets 160/0.
        self.assertEqual(b["cell"], 8)
        self.assertEqual(b["ox"], 160)
        self.assertEqual(b["oy"], 0)
        self.assertEqual((b["v"]["W"], b["v"]["H"]), (60, 50))
        # one cell for both axes (square cells).
        self.assertEqual(a["v"]["s"], a["cell"])
        self.assertEqual(b["v"]["s"], b["cell"])

    # ── AC4/AC6: zoom stepping both directions + extremes ─────────────────
    # `+` zooms IN (level→0, bigger cells, fewer squares); `−` zooms OUT
    # (level→10, smaller cells, more squares).
    def test_ac4_ac6_zoom_buttons_step_and_extremes(self):
        out = json.loads(js(
            self._gm_welcome(60, 60)
            + self._vset(10, 0, 0, (800, 400))
            + "const at10=" + self._BTN + ";"
            + "for(let i=0;i<10;i++)api.els.zoomIn.dispatchEvent({type:'click'});"
            + "const lvl0=api.state.view.level;const at0=" + self._BTN + ";"
            + "for(let i=0;i<10;i++)api.els.zoomOut.dispatchEvent({type:'click'});"
            + "const lvl10=api.state.view.level;"
            + "return {at0,at10,lvl10,lvl0};})()"))
        self.assertEqual(out["lvl0"], 0, "`+`×10 from L10 lands on L0")
        self.assertEqual(out["lvl10"], 10, "`−`×10 from L0 lands on L10")
        at0, at10 = out["at0"], out["at10"]
        # L0: zoom-in (`+`) disabled "Fully zoomed in (6×5)"; zoom-out enabled.
        self.assertTrue(at0["Zi"])
        self.assertEqual(at0["zit"], "Fully zoomed in (6×5)")
        self.assertFalse(at0["Zo"])
        # L10: zoom-out (`−`) disabled "Fully zoomed out (60×50)"; zoom-in enabled.
        self.assertTrue(at10["Zo"])
        self.assertEqual(at10["zot"], "Fully zoomed out (60×50)")
        self.assertFalse(at10["Zi"])

    def test_ac6_no_level_outside_range(self):
        # zoomBy(+1) zooms IN toward L0; zoomBy(−1) zooms OUT toward L10.
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(10, 0, 0, (800, 400))
            + "for(let i=0;i<20;i++)api.zoomBy(1);const bottom=api.state.view.level;"
            + "for(let i=0;i<40;i++)api.zoomBy(-1);const top=api.state.view.level;"
            + "return {bottom,top};})()"))
        self.assertEqual(out["bottom"], 0)
        self.assertEqual(out["top"], 10)

    # ── BUG REGRESSION (owner report): `+` zooms IN, `−` zooms OUT ────────
    # Before the fix the buttons were wired to the wrong direction: `+`
    # took the level toward 10 (smaller cells, MORE squares) and `−` toward
    # 0. This asserts the conventional behavior end-to-end: clicking `+`
    # decreases the level (fewer squares / bigger cells = more detail) and
    # clicking `−` increases it (more squares / smaller cells), plus the
    # disabled state at each extreme. Run on a 24×16 map at L6 (window 25×21
    # covers 450×378 < 800×400 so a level step is visible).
    def test_zoom_buttons_increase_decrease_level_and_disable_at_extremes(self):
        # Start mid-range (L6). A single `+` must DECREASE the level
        # (zoom IN: fewer squares, bigger cells) and a single `−` must
        # INCREASE it (zoom OUT: more squares, smaller cells).
        out = json.loads(js(
            self._gm_welcome(24, 16) + self._vset(6, 0, 0, (800, 400))
            + "const start={level:api.state.view.level,cell:api.state.cell};"
            + "api.els.zoomIn.dispatchEvent({type:'click'});"
            + "const afterPlus={level:api.state.view.level,cell:api.state.cell,"
            + "w:api.state._view.W,h:api.state._view.H};"
            + "api.els.zoomOut.dispatchEvent({type:'click'});"
            + "const afterMinus={level:api.state.view.level,cell:api.state.cell,"
            + "w:api.state._view.W,h:api.state._view.H};"
            + "return {start,afterPlus,afterMinus};})()"))
        self.assertEqual(out["start"]["level"], 6)
        # `+` → zoom IN: level 6 → 5 (L5 window 20×17, cell 20 → bigger).
        self.assertEqual(out["afterPlus"]["level"], 5,
                         "`+` must DECREASE the level (zoom in)")
        self.assertEqual((out["afterPlus"]["w"], out["afterPlus"]["h"]),
                         (20, 17))
        self.assertGreater(out["afterPlus"]["cell"], out["start"]["cell"],
                           "zooming in makes cells bigger")
        # `−` → zoom OUT: level 5 → 6 (L6 window 25×21, cell 18 → smaller).
        self.assertEqual(out["afterMinus"]["level"], 6,
                         "`−` must INCREASE the level (zoom out)")
        self.assertEqual((out["afterMinus"]["w"], out["afterMinus"]["h"]),
                         (25, 21))
        self.assertLess(out["afterMinus"]["cell"], out["afterPlus"]["cell"],
                        "zooming out makes cells smaller")

        # Driven to each extreme: at L10 the `−` (zoom out) button is
        # disabled — already zoomed out as far as possible; at L0 the `+`
        # (zoom in) button is disabled — already zoomed in as far as possible.
        ext = json.loads(js(
            self._gm_welcome(24, 16) + self._vset(6, 0, 0, (800, 400))
            + "for(let i=0;i<10;i++)api.els.zoomOut.dispatchEvent({type:'click'});"
            + "const at10=" + self._BTN + ";const lvl10=api.state.view.level;"
            + "for(let i=0;i<10;i++)api.els.zoomIn.dispatchEvent({type:'click'});"
            + "const at0=" + self._BTN + ";const lvl0=api.state.view.level;"
            + "return {at0,at10,lvl10,lvl0};})()"))
        self.assertEqual(ext["lvl10"], 10, "`−`×10 reaches L10")
        self.assertEqual(ext["lvl0"], 0, "`+`×10 reaches L0")
        # At L10: zoom-out (`−`) disabled, zoom-in (`+`) enabled.
        self.assertTrue(ext["at10"]["Zo"], "`−` disabled at L10")
        self.assertEqual(ext["at10"]["zot"], "Fully zoomed out (60×50)")
        self.assertFalse(ext["at10"]["Zi"], "`+` enabled at L10")
        # At L0: zoom-in (`+`) disabled, zoom-out (`−`) enabled.
        self.assertTrue(ext["at0"]["Zi"], "`+` disabled at L0")
        self.assertEqual(ext["at0"]["zit"], "Fully zoomed in (6×5)")
        self.assertFalse(ext["at0"]["Zo"], "`−` enabled at L0")

    # ── AC5: `+` / `=` zoom IN, `−` zooms OUT (level moves the right way) ──

    # ── AC9: pan step table (viewStep) + movement ─────────────────────────
    def test_ac9_pan_step_table(self):
        out = json.loads(js(
            "(()=>{const sx=[],sy=[];"
            "for(let L=0;L<11;L++){sx.push(api.viewStep(L,'x'));"
            "sy.push(api.viewStep(L,'y'));}"
            "return {sx,sy};})()"))
        self.assertEqual(out["sx"], PZ_STEP_X)
        self.assertEqual(out["sy"], PZ_STEP_Y)

    def test_ac9_panby_moves_by_step(self):
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(4, 0, 0, (800, 400))
            + "api.panBy(1,0);const ax=api.state.view.panX;"
            + "api.panBy(0,1);const ay=api.state.view.panY;"
            + "return {ax,ay};})()"))
        self.assertEqual(out["ax"], PZ_STEP_X[4])   # L4 stepX = 2
        self.assertEqual(out["ay"], PZ_STEP_Y[4])   # L4 stepY = 1

    # ── AC10: pan clamp + edge disabled states ────────────────────────────
    def test_ac10_pan_clamp_and_edge_titles(self):
        # 40×30 map at L0 (6×5): panX ∈ [0,34], panY ∈ [0,25].
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(0, 0, 0, (800, 400))
            + "const atWest=" + self._BTN + ";"
            + "for(let i=0;i<200;i++)api.panBy(1,0);"
            + "for(let i=0;i<200;i++)api.panBy(0,1);"
            + "const px=api.state.view.panX,py=api.state.view.panY;"
            + "const atEast=" + self._BTN + ";"
            + "return {atWest,atEast,px,py};})()"))
        self.assertEqual(out["px"], 34)   # clamped to 40-6
        self.assertEqual(out["py"], 25)   # clamped to 30-5
        self.assertTrue(out["atWest"]["L"], "← disabled at west edge")
        self.assertEqual(out["atWest"]["lT"], "Panned to the west edge")
        self.assertTrue(out["atEast"]["R"], "→ disabled at east edge")
        self.assertEqual(out["atEast"]["rT"], "Panned to the east edge")
        self.assertTrue(out["atEast"]["D"], "↓ disabled at south edge")
        self.assertFalse(out["atEast"]["U"], "↑ enabled mid-map")

    # ── AC11: map smaller than the view on an axis → centered + locked ────
    def test_ac11_small_map_both_axes_locked(self):
        # A 60×10 map at L10 (60×50 window): 60≤60 AND 10≤50 → both axes
        # locked, pan (0,0), map centered.
        out = json.loads(js(
            self._gm_welcome(60, 10) + self._vset(10, 0, 0, (800, 400))
            + "const at=" + self._BTN + ";const v=api.state.view;"
            + "return {at,v};})()"))
        at, v = out["at"], out["v"]
        for k in ("L", "R", "U", "D"):
            self.assertTrue(at[k], "arrow %s must be axis-locked" % k)
        self.assertEqual(at["lT"], "Map fits horizontally — no pan")
        self.assertEqual(at["uT"], "Map fits vertically — no pan")
        self.assertEqual((v["panX"], v["panY"]), (0, 0))

    def test_ac11_zoom_in_unlocks_horizontal_only(self):
        # 60×10 at L9 (50×42): mw=60>50 → horizontal unlocks (range 10,
        # step 5); vertical 10≤42 stays locked; panY pinned at 0.
        out = json.loads(js(
            self._gm_welcome(60, 10) + self._vset(9, 0, 0, (800, 400))
            + "const at=" + self._BTN + ";"
            + "api.panBy(1,0);const px=api.state.view.panX;"
            + "api.panBy(0,1);const py=api.state.view.panY;"
            + "return {at,px,py};})()"))
        self.assertFalse(out["at"]["R"], "→ enabled once horizontal unlocks")
        self.assertTrue(out["at"]["U"], "↑ still axis-locked (vertical)")
        self.assertEqual(out["at"]["uT"], "Map fits vertically — no pan")
        self.assertEqual(out["px"], 5, "horizontal step at L9 = 5")
        self.assertEqual(out["py"], 0, "vertical stays locked")

    # ── AC12: initial view fits the whole map (§5.3 table) ────────────────
    def test_ac12_initial_fit_per_table(self):
        cases = [(6, 5, 0), (4, 4, 0), (2, 3, 0), (8, 6, 1), (10, 8, 2),
                 (12, 10, 3), (15, 15, 5), (24, 16, 6), (40, 30, 8),
                 (55, 45, 10), (60, 50, 10), (60, 60, 10), (30, 60, 10)]
        for w, h, expect in cases:
            out = json.loads(js(
                self._gm_welcome(w, h) + "return api.state.view;})()"))
            self.assertEqual(out["level"], expect,
                             "fit %dx%d -> L%d" % (w, h, expect))
            self.assertEqual(out["panX"], 0)
            self.assertEqual(out["panY"], 0)

    def test_ac12_fitted_map_inside_window(self):
        # 24×16 → L6 (25×21 window) covers the whole map: window == map.
        out = json.loads(js(
            self._gm_welcome(24, 16) + self._vset(6, 0, 0, (800, 400))
            + "return api.state._view;})()"))
        v = out
        self.assertEqual((v["x0"], v["y0"]), (0, 0))
        self.assertEqual((v["x1"], v["y1"]), (24, 16))

    # ── AC13: re-fit on map swap (use_map) ────────────────────────────────
    def test_ac13_refit_on_map_swap(self):
        # Old 60×60 map at (L3, 5,5); a state frame for a 24×16 map re-fits
        # to L6, pan (0,0), and renders the new grid.
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(3, 5, 5, (800, 400))
            + "const before={level:api.state.view.level,"
            + "pan:[api.state.view.panX,api.state.view.panY],"
            + "gw:api.state.grid.width};"
            + "const newmap=" + _floor_map_js(24, 16) + ";"
            + "api.onState({type:'state',map:newmap,entities:[],players:[],"
            + "awareness:[],fog:false});"
            + "return {before,"
            + "after:{level:api.state.view.level,"
            + "pan:[api.state.view.panX,api.state.view.panY],"
            + "gw:api.state.grid.width,gh:api.state.grid.height}};})()"))
        self.assertEqual(out["before"]["level"], 3)
        self.assertEqual(out["before"]["pan"], [5, 5])
        self.assertEqual(out["before"]["gw"], 60)
        self.assertEqual(out["after"]["gw"], 24)
        self.assertEqual(out["after"]["gh"], 16)
        self.assertEqual(out["after"]["level"], 6)
        self.assertEqual(out["after"]["pan"], [0, 0])

    # ── AC14: resize keeps level + clamped pan (no re-fit) ────────────────
    def test_ac14_resize_keeps_level_and_pan(self):
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(6, 8, 4, (800, 400))
            + "const before=" + self._geo() + ";"
            + "api.els.canvasWrap.clientWidth=616;"
            + "api.els.canvasWrap.clientHeight=516;"
            + "api._window.dispatch('resize',{});"
            + "api._timer.advance(100);"
            + "const after=" + self._geo() + ";"
            + "return {before,after};})()"))
        b, a = out["before"], out["after"]
        # level + pan unchanged (fit of 60×60 is L10; staying at L6 proves
        # there was NO re-fit on resize).
        self.assertEqual(a["view"]["level"], 6)
        self.assertEqual((a["view"]["panX"], a["view"]["panY"]), (8, 4))
        # cell recomputed for the new avail (600×500) at L6 (25×21):
        # floor(min(600/25, 500/21)) = floor(min(24, 23.8)) = 23.
        self.assertEqual(a["cell"], 23)
        self.assertNotEqual(a["cell"], b["cell"], "cell changes with size")
        self.assertGreaterEqual(a["view"]["panX"], 0)
        self.assertLessEqual(a["view"]["panX"], 60 - 25)

    # ── AC2: arrow buttons pan by the step; the window follows ────────────
    def test_ac2_arrow_buttons_pan_by_step(self):
        # 40×30 at L4 (16×13), pan (5,3). Each button pans its axis by the
        # step (L4: stepX=2, stepY=1) and the rendered window follows.
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(4, 5, 3, (800, 400))
            + "const base={x0:api.state._view.x0,y0:api.state._view.y0};"
            + "api.els.navLeft.dispatchEvent({type:'click'});"
            + "const l={px:api.state.view.panX,py:api.state.view.panY,"
            + "x0:api.state._view.x0,y0:api.state._view.y0};"
            + "api.els.navRight.dispatchEvent({type:'click'});"
            + "const r={px:api.state.view.panX,py:api.state.view.panY};"
            + "api.els.navUp.dispatchEvent({type:'click'});"
            + "const u={px:api.state.view.panX,py:api.state.view.panY};"
            + "api.els.navDown.dispatchEvent({type:'click'});"
            + "const d={px:api.state.view.panX,py:api.state.view.panY};"
            + "return {base,l,r,u,d};})()"))
        self.assertEqual((out["base"]["x0"], out["base"]["y0"]), (5, 3))
        self.assertEqual(out["l"]["px"], 3)    # ← : panX −2
        self.assertEqual(out["l"]["py"], 3)    # …panY unchanged
        self.assertEqual(out["l"]["x0"], 3)    # window x0 follows
        self.assertEqual(out["l"]["y0"], 3)
        self.assertEqual(out["r"]["px"], 5)    # → : back to 5
        self.assertEqual(out["u"]["px"], 5)    # ↑ : panY −1, panX unchanged
        self.assertEqual(out["u"]["py"], 2)
        self.assertEqual(out["d"]["py"], 3)    # ↓ : back to 3
        self.assertEqual(out["d"]["px"], 5)

    # ── AC3: cursor keys produce an identical delta to the buttons ────────
    def test_ac3_cursor_keys_match_button_delta(self):
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(4, 10, 5, (800, 400))
            + "const base={px:api.state.view.panX,py:api.state.view.panY};"
            + "api.document.dispatch('keydown',"
            + "{key:'ArrowLeft',target:{},preventDefault(){}});"
            + "const key={px:api.state.view.panX,py:api.state.view.panY};"
            + "api.state.view.panX=base.px;api.state.view.panY=base.py;"
            + "api.syncNavControls();"
            + "api.els.navLeft.dispatchEvent({type:'click'});"
            + "const btn={px:api.state.view.panX,py:api.state.view.panY};"
            + "return {base,key,btn};})()"))
        self.assertEqual(out["key"]["px"], out["base"]["px"] - PZ_STEP_X[4])
        self.assertEqual(out["key"]["py"], out["base"]["py"])
        self.assertEqual(out["key"], out["btn"],
                         "cursor key delta must equal the button delta")

    # ── AC5: `+` / `=` zoom IN (level−1), `−` zooms OUT (level+1) ────────
    def test_ac5_cursor_keys_zoom(self):
        # From L5: `+` → L4 (zoom in), `=` → L3 (zoom in), `−` → L4 (zoom out).
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(5, 0, 0, (800, 400))
            + "api.document.dispatch('keydown',"
            + "{key:'+',target:{},preventDefault(){}});"
            + "const plus=api.state.view.level;"
            + "api.document.dispatch('keydown',"
            + "{key:'=',target:{},preventDefault(){}});"
            + "const eq=api.state.view.level;"
            + "api.document.dispatch('keydown',"
            + "{key:'-',target:{},preventDefault(){}});"
            + "const minus=api.state.view.level;"
            + "return {plus,eq,minus};})()"))
        self.assertEqual(out["plus"], 4)
        self.assertEqual(out["eq"], 3)
        self.assertEqual(out["minus"], 4)

    # ── A2: arrows PAN (retired arrow-key entity move) ─────────────────────
    def test_a2_arrows_pan_not_move(self):
        # With an entity selected, ArrowUp must PAN the view and send NO
        # move frame (the old arrow-key nudge behavior is retired).
        ents = ("[{id:'e1',name:'N',kind:'npc',team:'neutral',x:10,y:10}]")
        out = json.loads(js(
            self._gm_welcome(40, 30, entities_js=ents)
            + self._vset(4, 10, 5, (800, 400))
            + "api.selectEntity('e1');"
            + "api._send.reset();"
            + "api.document.dispatch('keydown',"
            + "{key:'ArrowUp',target:{},preventDefault(){}});"
            + "const py=api.state.view.panY;"
            + "return {py,sent:api._send.sent.map(m=>m.type)};})()"))
        self.assertEqual(out["py"], 4)   # panned up by stepY (L4 = 1)
        self.assertEqual(out["sent"], [], "arrows must not move the entity")

    # ── AC15: input focus guard (field → fully ignored) ───────────────────
    def test_ac15_input_focus_guard(self):
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(4, 10, 5, (800, 400))
            + "const before={px:api.state.view.panX,py:api.state.view.panY,"
            + "level:api.state.view.level};"
            + "for(const tag of ['INPUT','TEXTAREA','SELECT']){"
            + "const ev1={key:'ArrowUp',target:{tagName:tag},"
            + "preventDefault(){this.pd=true}};"
            + "api.document.dispatch('keydown',ev1);"
            + "const ev2={key:'-',target:{tagName:tag},"
            + "preventDefault(){this.pd=true}};"
            + "api.document.dispatch('keydown',ev2);"
            + "if(ev1.pd||ev2.pd)return{err:'preventDefault over a field'};"
            + "}"
            + "const after={px:api.state.view.panX,py:api.state.view.panY,"
            + "level:api.state.view.level};"
            + "return {before,after};})()"))
        self.assertNotIn("err", out)
        self.assertEqual(out["before"], out["after"],
                         "no pan/zoom while focus is in a field")

    def test_ac15_contenteditable_guard(self):
        # A contenteditable target is also guarded (A3).
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(4, 10, 5, (800, 400))
            + "api.document.dispatch('keydown',"
            + "{key:'ArrowDown',target:{tagName:'DIV',isContentEditable:true},"
            + "preventDefault(){}});"
            + "return {py:api.state.view.panY};})()"))
        self.assertEqual(out["py"], 5, "contenteditable must not pan")

    def test_ac15_non_field_pans(self):
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(4, 10, 5, (800, 400))
            + "api.document.dispatch('keydown',"
            + "{key:'ArrowUp',target:{},preventDefault(){}});"
            + "return {py:api.state.view.panY};})()"))
        self.assertEqual(out["py"], 4)

    # ── AC16: painting / door / safe-door under the transform ─────────────
    def _gm_wall_ctx(self, level, px, py, avail=(800, 400)):
        return (self._gm_welcome(40, 30) + self._vset(level, px, py, avail)
                + "api.setTool('wall');")

    def test_ac16_paint_resolves_cell_under_transform(self):
        # 40×30 GM at L4 (16×13), pan (2,3), avail 800×400 → cell 30,
        # offset (100,-85), window x[2,18) y[3,16). A drag across the
        # window emits paint frames for the cells rendered under each
        # sampled pixel (including the window-edge cell (17,15)); entering
        # the letterbox bar (x≥18) emits nothing.
        out = json.loads(js(
            self._gm_wall_ctx(4, 2, 3)
            + "const c=api.state.cell,ox=api.state.offsetX,"
            + "oy=api.state.offsetY;"
            + "const P=(x,y)=>({clientX:ox+x*c+c/2,clientY:oy+y*c+c/2});"
            + "api._send.reset();"
            + "api.els.canvas.dispatchEvent(Object.assign("
            + "{type:'pointerdown',pointerId:1},P(2,3)));"
            + "api.els.canvas.dispatchEvent(Object.assign("
            + "{type:'pointermove'},P(5,3)));"
            + "api.els.canvas.dispatchEvent(Object.assign("
            + "{type:'pointermove'},P(10,8)));"
            + "api.els.canvas.dispatchEvent(Object.assign("
            + "{type:'pointermove'},P(17,15)));"
            + "api.els.canvas.dispatchEvent({type:'pointermove',"
            + "clientX:ox+18*c+5,clientY:oy+3*c+c/2});"
            + "api.els.canvas.dispatchEvent({type:'pointerup'});"
            + "return {sent:api._send.sent,"
            + "win:[api.state._view.x0,api.state._view.x1,"
            + "api.state._view.y0,api.state._view.y1]};})()"))
        self.assertEqual(out["win"], [2, 18, 3, 16])
        painted = [(m["x"], m["y"], m["cell_type"]) for m in out["sent"]]
        self.assertEqual(painted,
                         [(2, 3, "wall"), (5, 3, "wall"),
                          (10, 8, "wall"), (17, 15, "wall")])

    def test_ac16_door_and_safedoor_tools_resolve_cell(self):
        out = json.loads(js(
            self._gm_welcome(40, 30)
            + "api.state.grid.cells[8][10]='doorway';"
            + self._vset(4, 2, 3)
            + "api.setTool('door');"
            + "api._send.reset();"
            + "const c=api.state.cell,ox=api.state.offsetX,"
            + "oy=api.state.offsetY;"
            + "api.els.canvas.dispatchEvent({type:'click',"
            + "clientX:ox+10*c+c/2,clientY:oy+8*c+c/2});"
            + "const door=api._send.sent.slice();"
            + "api.state.safe={'10,8':'L'};"
            + "api.setTool('safeDoor');api.setSafeAction('unlock');"
            + "api._send.reset();"
            + "api.els.canvas.dispatchEvent({type:'click',"
            + "clientX:ox+10*c+c/2,clientY:oy+8*c+c/2});"
            + "return {door,safe:api._send.sent};})()"))
        self.assertEqual(out["door"],
                         [{"type": "door", "x": 10, "y": 8,
                           "action": "unlock"}])
        self.assertEqual(out["safe"],
                         [{"type": "safe_door", "x": 10, "y": 8,
                           "action": "unlock"}])

    # ── AC17: movement + spawn under the transform ─────────────────────────
    def test_ac17_player_tap_to_move_under_transform(self):
        out = json.loads(js(
            "(()=>{const map=" + _floor_map_js(40, 30) + ";"
            + "api.onWelcome({type:'welcome',"
            + "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            + "map,entities:[],you_entity:{id:'e2',name:'Alice',"
            + "kind:'player',team:'party',x:6,y:6},"
            + "players:[{id:'p2',entity_id:'e2',awareness_radius:4}],"
            + "awareness:[],fog:false});"
            + self._vset(5, 4, 2)
            + "const c=api.state.cell,ox=api.state.offsetX,"
            + "oy=api.state.offsetY;"
            + "api._send.reset();"
            + "api.els.canvas.dispatchEvent({type:'click',"
            + "clientX:ox+15*c+c/2,clientY:oy+10*c+c/2});"
            + "return {sent:api._send.sent,"
            + "win:[api.state._view.x0,api.state._view.y0,"
            + "api.state._view.x1,api.state._view.y1]};})()"))
        self.assertEqual(out["win"], [4, 2, 24, 19])
        self.assertEqual(out["sent"],
                         [{"type": "move", "entity_id": "e2", "x": 15,
                           "y": 10, "override": False}])

    def test_ac17_gm_add_spawns_on_hovered_cell(self):
        out = json.loads(js(
            self._gm_welcome(40, 30) + self._vset(3, 2, 2)
            + "const c=api.state.cell,ox=api.state.offsetX,"
            + "oy=api.state.offsetY;"
            + "api.els.canvas.dispatchEvent({type:'pointermove',"
            + "clientX:ox+7*c+c/2,clientY:oy+9*c+c/2});"
            + "api.els.newEntityKind.value='npc';"
            + "api.els.newEntityTeam.value='party';"
            + "api.els.newEntityName.value='Sentry';"
            + "api._send.reset();"
            + "api.els.btnNewEntity.dispatchEvent({type:'click'});"
            + "const spawn=api._send.sent.slice();"
            + "api.state.selectedEntityId='eX';"
            + "api._send.reset();"
            + "api.els.canvas.dispatchEvent({type:'click',"
            + "clientX:ox+20*c+c/2,clientY:oy+2*c+c/2});"
            + "return {spawn,offWin:api._send.sent};})()"))
        self.assertEqual(out["spawn"],
                         [{"type": "create_entity", "name": "Sentry",
                           "kind": "npc", "team": "party", "x": 7, "y": 9}])
        self.assertEqual(out["offWin"], [],
                         "an out-of-window click must send nothing")

    # ── AC18: awareness overlay alignment under the transform ─────────────
    def test_ac18_awareness_overlay_alignment(self):
        out = json.loads(js(
            "(()=>{const map=" + _floor_map_js(40, 30) + ";"
            + "api.onWelcome({type:'welcome',"
            + "you:{id:'p2',name:'Alice',role:'player',entity_id:'e2'},"
            + "map,entities:[],you_entity:{id:'e2',name:'Alice',"
            + "kind:'player',team:'party',x:6,y:6},"
            + "players:[{id:'p2',entity_id:'e2',awareness_radius:4}],"
            + "awareness:[{entity_id:'e1',x:9,y:7,color:'green',"
            + "name:'Bob',kind:'player',label:true},"
            + "{entity_id:'<a>',x:3,y:2,approximate:true,label:false}],"
            + "fog:false});"
            + self._vset(4, 3, 4)
            + "const c=api.state.cell,ox=api.state.offsetX,"
            + "oy=api.state.offsetY;"
            + "api.els.canvas.getContext('2d')._arcs.length=0;"
            + "api.els.canvas.getContext('2d')._fills.length=0;"
            + "api.els.canvas.getContext('2d')._texts.length=0;"
            + "api.renderAll();"
            + "const ctx=api.els.canvas.getContext('2d');"
            + "const arcs=ctx._arcs.map(a=>[a[0],a[1]]);"
            + "const rf=ctx._fills.find(f=>"
            + "f.style==='rgba(77, 171, 247, 0.10)');"
            + "return {c,ox,oy,arcs,ring:rf?[rf.x,rf.y]:null};})()"))
        c, ox, oy = out["c"], out["ox"], out["oy"]
        centers = {(round(x, 2), round(y, 2)) for x, y in out["arcs"]}

        def at(x, y):
            return (round(ox + (x + 0.5) * c, 2),
                    round(oy + (y + 0.5) * c, 2))

        self.assertIn(at(6, 6), centers, "own token misaligned")
        self.assertIn(at(9, 7), centers, "full contact misaligned")
        # approx item (3,2) → 2×2 block origin cell (6,4); the marker sits
        # at the block CENTER = origin + 1 cell = pixel (ox+(3*2+1)c, oy+(2*2+1)c).
        approx_center = (round(ox + (3 * 2 + 1) * c, 2),
                         round(oy + (2 * 2 + 1) * c, 2))
        self.assertIn(approx_center, centers, "approx block misaligned")
        # the awareness ring (own token, radius 4) is centered on the token.
        self.assertIsNotNone(out["ring"], "awareness ring not drawn")
        half = (4 + 0.5) * c
        ring_cx = out["ring"][0] + half
        ring_cy = out["ring"][1] + half
        self.assertEqual((round(ring_cx, 2), round(ring_cy, 2)), at(6, 6),
                         "ring anchor misaligned")

    # ── AC22: render culling (no draw call for off-window entities) ───────
    def test_ac22_render_culling(self):
        ents = ("[{id:'en',name:'Near',kind:'npc',team:'neutral',x:5,y:4},"
                "{id:'ef',name:'Far',kind:'npc',team:'neutral',x:59,y:59}]")
        out = json.loads(js(
            self._gm_welcome(60, 60, entities_js=ents)
            + self._vset(0, 0, 0, (800, 400))
            + "const C=api.els.canvas.getContext('2d');"
            + "C._arcs.length=0;C._fills.length=0;C._texts.length=0;"
            + "api.renderAll();"
            + "const t0={N:C._texts.includes('N'),"
            + "F:C._texts.includes('F')};"
            + "const fills0=C._fills.filter(f=>f.style==='#efe9dc');"
            + "api.state.view.panX=54;api.state.view.panY=55;"
            + "api.layoutCanvas();api.syncNavControls();"
            + "C._arcs.length=0;C._fills.length=0;C._texts.length=0;"
            + "api.renderAll();"
            + "const t1={N:C._texts.includes('N'),"
            + "F:C._texts.includes('F')};"
            + "const fills1=C._fills.filter(f=>f.style==='#efe9dc');"
            + "return {t0,t1,s:api.state.cell,fills0:"
            + "fills0.map(f=>[f.w,f.h]),fills1:"
            + "fills1.map(f=>[f.w,f.h])};})()"))
        # the grid pass fills only the 6×5 window (one rect), not the map.
        self.assertEqual(out["fills0"], [[6 * out["s"], 5 * out["s"]]])
        self.assertEqual(out["fills1"], [[6 * out["s"], 5 * out["s"]]])
        # near entity (5,4) drawn at the NW corner, far (59,59) culled;
        # after panning to the SE corner the far entity is drawn, near gone.
        self.assertTrue(out["t0"]["N"])
        self.assertFalse(out["t0"]["F"])
        self.assertTrue(out["t1"]["F"])
        self.assertFalse(out["t1"]["N"])

    # ── AC1: every cell becomes reachable / visible ───────────────────────
    def test_ac1_navigable_cell5959(self):
        # cell (59,59) is visible at L10/pan(0,10); the L0 SE corner of a
        # 60×60 map is pan(54,55) (max panX=60-6=54, max panY=60-5=55),
        # which shows window x[54,60) y[55,60) ⊇ (59,59). (The spec's "L0,
        # pan (54, 54)" example is off-by-one: at panY 54 row 59 is 1px out.)
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(10, 0, 10, (800, 400))
            + "const v1=api.state._view;"
            + self._vset(0, 54, 55, (800, 400))
            + "const v2=api.state._view;"
            + "const inW=(v,x,y)=>x>=v.x0&&x<v.x1&&y>=v.y0&&y<v.y1;"
            + "return {a:inW(v1,59,59),b:inW(v2,59,59)};})()"))
        self.assertTrue(out["a"], "visible at L10 pan(0,10)")
        self.assertTrue(out["b"], "visible at L0 pan(54,55)")

    # ── AC19: 60×60 (E7) — horizontal locked, vertical pan 0/5/10 ────────
    def test_ac19_sixty_by_sixty_vertical_pan(self):
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(10, 0, 0, (800, 400))
            + "const fit=Object.assign({level:api.state.view.level},"
            + self._BTN + ");"
            + "api.panBy(0,1);api.panBy(0,1);"
            + "const down2=Object.assign({py:api.state.view.panY},"
            + self._BTN + ");"
            + "api.panBy(0,-1);api.panBy(0,-1);"
            + "const up2=Object.assign({py:api.state.view.panY},"
            + self._BTN + ");"
            + "return {fit,down2,up2};})()"))
        fit = out["fit"]
        self.assertEqual(fit["level"], 10, "60×60 fits to L10")
        self.assertEqual(fit["lT"], "Map fits horizontally — no pan")
        self.assertTrue(fit["L"] and fit["R"], "←/→ permanently disabled")
        self.assertTrue(fit["U"], "↑ disabled at fit (panY=0)")
        self.assertFalse(fit["D"])
        self.assertEqual(out["down2"]["py"], 10, "↓×2 → 0→5→10")
        self.assertTrue(out["down2"]["D"], "↓ disabled at south edge")
        self.assertFalse(out["down2"]["U"], "↑ enabled mid-map")
        self.assertEqual(out["up2"]["py"], 0, "↑×2 → 10→5→0")
        self.assertTrue(out["up2"]["U"], "↑ re-disabled at north edge")

    # ── AC20: rapid key repeat (E8) — exact steps + one rAF render ────────
    def test_ac20_rapid_key_repeat(self):
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(10, 0, 0, (800, 400))
            + "for(let i=0;i<5;i++)api.document.dispatch('keydown',"
            + "{key:'ArrowDown',target:{},preventDefault(){}});"
            + "const q=api._rfr.queue.length;"
            + "api._rfr.dispatch();"
            + "const renders=api._rfr.renderCalls;"
            + "return {py:api.state.view.panY,q,renders};})()"))
        self.assertEqual(out["py"], 10, "5 steps of 5 clamp at 10")
        self.assertEqual(out["q"], 1, "at most one rAF render queued")
        self.assertEqual(out["renders"], 1, "one render per frame")

    # ── AC21: per-client, frontend-only (no wire frames) ──────────────────
    def test_ac21_view_ops_send_no_wire_frames(self):
        out = json.loads(js(
            self._gm_welcome(60, 60) + self._vset(4, 0, 0, (800, 400))
            + "api._send.reset();"
            + "for(let i=0;i<5;i++)api.panBy(1,0);"
            + "for(let i=0;i<5;i++)api.zoomBy(1);"
            + "api.fitToMap();"
            + "api.els.canvasWrap.clientWidth=616;"
            + "api._window.dispatch('resize',{});"
            + "api._timer.advance(100);"
            + "return {sent:api._send.sent};})()"))
        self.assertEqual(out["sent"], [],
                         "view ops must not send any WS frame")

    # ── AC2 (HTML): #nav-panel is the first sidebar section ───────────────
    def test_ac2_nav_panel_in_sidebar_first(self):
        with open(INDEX) as fh:
            html = fh.read()
        self.assertIn('id="nav-panel"', html)
        nav = html.index('id="nav-panel"')
        gm = html.index('id="entity-tools"')
        aw = html.index('id="awareness"')
        sidebar = html.index('id="sidebar"')
        self.assertTrue(sidebar < nav < gm < aw,
                        "nav-panel must be the first sidebar section")
        for b in ("nav-up", "nav-down", "nav-left", "nav-right",
                  "zoom-in", "zoom-out", "nav-readout"):
            self.assertIn('id="%s"' % b, html)



if __name__ == '__main__':
    unittest.main()
