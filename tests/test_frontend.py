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
"""

from __future__ import annotations

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
    quote-escaping is involved.
    """
    program = (
        'const {buildApi}=require(process.env.HARNESS);\n'
        'const api=buildApi();\n'
        'const out=eval(process.env.EXPR);\n'
        'process.stdout.write(JSON.stringify(out));\n'
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
            expr = (
                "(()=>{api.onWelcome({type:'welcome',"
                "you:{id:'p1',name:'Gamer',role:'gm',entity_id:'e1'},"
                "map:%s,entities:[{id:'e1',name:'Gamer',kind:'gm_character',"
                "team:'neutral',x:1,y:1}],players:[],"
                "awareness:[{entity_id:'e1',x:1,y:1,color:'white',label:true}],"
                "fog:false});"
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


class TestBug006GmOwnTokenInSidebar(FrontendBase):
    def test_gm_sidebar_lists_own_entity(self):
        expr = (
            "(()=>{api.state.role='gm';api.state.name='Gamer';"
            "api.state.entities=[{id:'e1',name:'Gamer',kind:'gm_character',"
            "team:'neutral',x:1,y:1,owner:null},{id:'e2',name:'Alice',"
            "kind:'player',team:'party',x:2,y:1,owner:'p2'}];"
            "api.state.you={id:'p1',name:'Gamer',role:'gm',entity_id:'e1'};"
            "api.state.awareness=[{entity_id:'e1',x:1,y:1,color:'white',"
            "name:'Gamer',kind:'gm_character',label:true},{entity_id:'e2',"
            "x:2,y:1,color:'green',name:'Alice',kind:'player',label:true}];"
            "const kids=[];"
            "api.els.awarenessList.innerHTML='';"
            "api.els.awarenessList.appendChild=(c)=>{kids.push(c);return c};"
            "api.drawSidebar();"
            "const ids=kids.map(k=>k.dataset.entityId).filter(Boolean);"
            "return {ids,summary:api.els.awarenessSummary.textContent};})()"
        )
        out = js(expr)
        # The GM's own gm_character (e1) AND the other entity (e2) are both
        # listed. Before the fix e1 was dropped, so the list disagreed with
        # the summary by one.
        self.assertIn('"e1"', out, f"GM own entity missing from sidebar: {out}")
        self.assertIn('"e2"', out)
        self.assertIn('"ids":["e1","e2"]', out)
        # The summary counts all awareness items (1 neutral + 1 ally).
        self.assertIn("1 ally", out)
        self.assertIn("1 neutral", out)


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
            "entity_id:'e1'},map,entities:[{id:'e1',name:'G',kind:"
            "'gm_character',team:'neutral',x:1,y:1}],players:[],"
            "awareness:[{entity_id:'e1',x:1,y:1,color:'white',label:true}],"
            "fog:false});"
            "return {lobby:api.els.lobbyStatus.textContent, joined:"
            "api.state.joined};})()"
        )
        out = js(expr)
        # Once a welcome arrives the stale join error is cleared.
        self.assertIn('"lobby":""', out, out)
        self.assertIn('"joined":true', out)


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

    def test_bug010_no_player_kind_option(self):
        # The server only creates npc/enemy/gm_character; the UI must not
        # offer a "player" kind. We assert the #new-entity-kind select has no
        # player option while keeping the other three.
        import re
        m = re.search(r'<select id="new-entity-kind">.*?</select>',
                      self.html, re.DOTALL)
        self.assertIsNotNone(m, "could not find #new-entity-kind")
        block = m.group(0)
        self.assertNotIn('value="player"', block,
                         "BUG-010: 'player' kind still offered")
        for kind in ("npc", "enemy", "gm_character"):
            self.assertIn(f'value="{kind}"', block, f"missing kind {kind}")


if __name__ == "__main__":
    unittest.main()
