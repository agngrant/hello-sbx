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
        # 16x12 grid on the 800x600 harness canvas → cell 50, origin (0,0).
        # FULL item (3,1):  token circle arc(175,75,r); name label "Bob".
        # APPROX item (2,1): block spans pixels (200..300, 100..200);
        # the "?" marker sits at the block CENTER → arc(250,150,r).
        expr = (
            self._player_state()
            + "api.els.mapView.hidden=false;"
            + "api.renderAll();"
            + "const c=api.els.canvas.getContext('2d');"
            + "return {arcs:c._arcs,texts:c._texts};})()"
        )
        out = js(expr)
        # Full contact (3,1) → token circle drawn at the EXACT cell center.
        self.assertIn('[176,76,18.24', out, out)
        self.assertIn('"Bob"', out, out)          # FULL name label drawn
        self.assertIn('"B"', out)                 # identity letter drawn
        self.assertIn('"?"', out, out)            # approximate marker glyph
        # The "?" marker is at the block CENTER (248,148) — NOT at the block
        # origin cell center (128,120) and NOT where an "item.x is a cell"
        # render would put it (128,76).
        self.assertIn('[248,148,14.39', out, out)
        self.assertNotIn('[128,120', out)
        self.assertNotIn('[128,76', out)

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
    full-detail for the GM and the preview (no third argument)."""

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

    def test_player_render_no_fill_over_hidden_cell(self):
        # A matrix whose ONLY S/E cells are (1,1) [S] and (3,2) [E]; every
        # other cell is H. No fill may cover an H cell. With s=146, ox=27,
        # oy=0 the two S/E floors land at known rects; any OTHER floor fill
        # would be a bug.
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
            "return c._fills.filter(f=>f.style==='#efe9dc'||f.style==='#6b7280')"
            ".map(f=>[f.x,f.y,f.w,f.h,f.style]);})()"
        )
        out = json.loads(js(expr))
        # S/E floor rects (s=146, ox=27, oy=0):
        #   (1,1) [S] -> (27+146, 0+146) = (173,146,146,146)
        #   (3,2) [E] -> (27+3*146, 0+2*146) = (465,292,146,146)
        expected = {(173, 146, 146, 146), (465, 292, 146, 146)}
        got = {(f[0], f[1], f[2], f[3]) for f in out}
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


if __name__ == "__main__":
    unittest.main()
