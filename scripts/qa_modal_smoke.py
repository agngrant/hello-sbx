#!/usr/bin/env python3
"""Live smoke for the save-delete full-screen modal (feat/save-load).

Boots the REAL server on an ephemeral port, then:
  * checks the SERVED index.html / app.js / style.css carry the modal shell +
    markers (and none of the removed in-row artifacts);
  * drives a real GM session over REST: create a save, delete it through the
    modal path (real app.js under Node with REAL fetch -> real DELETE),
    verify success + 404 error paths (the exact message the frontend toasts);
  * a second Node driver checks pan/drawer/modal-closed regression behavior.
Exit 0 iff every check passes. QA artifact (scripts/qa_*.py convention).
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
NODE = os.environ.get("NODE", "node")

from tests.wsclient import WSClient  # noqa: E402

FAILURES: list[str] = []
LOG = os.path.join(ROOT, "qa_modal_smoke.log")


def check(label, cond, detail=""):
    ok = bool(cond)
    print(f"  {'PASS' if ok else 'FAIL'} {label}"
          + (f"   -> {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def rest(port, method, path, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def get_text(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                 timeout=10) as r:
        return r.status, r.read().decode("utf-8")


def alloc_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(port):
    log = open(LOG, "ab")
    return subprocess.Popen(
        [sys.executable, "-m", "app.main", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, stdout=log, stderr=log)


def wait_health(port, tries=80):
    for _ in range(tries):
        try:
            if rest(port, "GET", "/health")[0] == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def stop_server(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=8)
    except Exception as e:
        print(f"  (stop note: {e})")


def port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return False
    except Exception:
        return True
    finally:
        s.close()


def run_node(script, *args, timeout=60):
    try:
        p = subprocess.run([NODE, script, *args], cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout)
        out = p.stdout.strip().splitlines()
        res = None
        for ln in out:
            if ln.startswith("RESULT_JSON "):
                res = json.loads(ln[len("RESULT_JSON "):])
        return p.returncode, res, (p.stdout.strip() + "\n" + p.stderr.strip())
    except subprocess.TimeoutExpired:
        return 124, None, "node driver timed out"


def main():
    port = alloc_free_port()
    print(f"ephemeral port = {port}")
    proc = start_server(port)
    ws = None
    try:
        check("server up (health)", wait_health(port))

        # ---- served static assets ---------------------------------------
        _, html = get_text(port, "/")
        check("served index.html: #save-delete-modal shell present",
              'id="save-delete-modal"' in html and "hidden" in html.split('id="save-delete-modal"')[1][:40])
        check("served index.html: dialog role/aria",
              'role="alertdialog"' in html and 'aria-modal="true"' in html
              and 'aria-labelledby="save-delete-modal-title"' in html
              and 'aria-describedby="save-delete-modal-body"' in html)
        check("served index.html: body-level (after #map-view close)",
              html.rfind('</section>') < html.find('id="save-delete-modal"')
              < html.rfind('</body>'))
        check("served index.html: real buttons",
              '<button id="save-delete-modal-cancel" class="btn">Cancel</button>' in html
              and '<button id="save-delete-modal-confirm" class="btn btn-danger">Delete</button>' in html)

        _, js = get_text(port, "/app.js")
        js_nc = re.sub(r"/\*.*?\*/", "", js, flags=re.S)      # strip block comments
        js_nc = re.sub(r"^\s*//.*$", "", js_nc, flags=re.M)   # then line comments
        check("served app.js: syncSaveModal present", "function syncSaveModal" in js)
        check("served app.js: modal wiring present",
              "els.saveDeleteModalConfirm.addEventListener" in js
              and "saveModalReturnFocusId" in js)
        check("served app.js: NO in-row artifacts",
              "save-row-confirm" not in js and "is-confirming" not in js)
        check("served app.js: deleteSave( exactly 2", js.count("deleteSave(") == 2,
              str(js.count("deleteSave(")))
        check("served app.js: no window.confirm call",
              "window.confirm" not in js_nc)

        _, css = get_text(port, "/style.css")
        check("served style.css: --modal-z: 100", "--modal-z: 100" in css)
        check("served style.css: --modal-backdrop token", "--modal-backdrop:" in css)
        check("served style.css: fixed inset:0 z-index var(--modal-z)",
              bool(re.search(r"#save-delete-modal\s*\{[^}]*position: fixed;[^}]*inset: 0;[^}]*z-index: var\(--modal-z\);", css)))
        check("served style.css: NO .save-row-confirm / .save-row.is-confirming",
              "save-row-confirm" not in css and "is-confirming" not in css)

        # ---- real GM REST flow ------------------------------------------
        # Join a GM over WS so a real session exists (save POST needs one).
        ws = WSClient("127.0.0.1", port, path="/ws", timeout=10)
        ws.connect()
        welcome = ws.join("QA-GM", "gm")
        check("GM joined over WS (role gm)",
              welcome.get("type") == "welcome"
              and welcome["you"]["role"] == "gm",
              json.dumps((welcome.get("type"), welcome.get("you", {}).get("role"))))
        status, bdy = rest(port, "POST", "/api/saves", {"name": "QA Modal Smoke"})
        check("GM create save -> 200 record", status == 200 and bdy.get("ok") and bdy.get("id"),
              json.dumps((status, bdy)))
        sid = bdy.get("id")
        ondisk = os.path.exists(os.path.join(ROOT, "saves", f"{sid}.json"))
        check("save file on disk", ondisk, f"{sid}.json")

        # ---- modal path: real app.js + REAL fetch -> real DELETE --------
        rc, res, out = run_node(os.path.join(ROOT, "scripts", "qa_modal_live.js"), str(port))
        print("  [modal live driver]")
        print("   " + (out or "").replace("\n", "\n   "))
        ok_steps = res and all(s["ok"] for s in res.get("steps", {}).values())
        check("modal driver all steps pass", rc == 0 and ok_steps,
              json.dumps(res) if res else f"rc={rc}")
        if res and res.get("deletedId"):
            check("modal driver deleted the created save",
                  res["deletedId"] == sid, f"{res.get('deletedId')} vs {sid}")
        check("save file GONE after modal delete",
              not os.path.exists(os.path.join(ROOT, "saves", f"{sid}.json")))
        status, bdy = rest(port, "GET", "/api/saves")
        check("GET /api/saves no longer lists it",
              status == 200 and all(s["id"] != sid for s in bdy.get("saves", [])),
              json.dumps([s["id"] for s in bdy.get("saves", [])]))

        # ---- error path: 404 message verbatim (what the frontend toasts)
        status, bdy = rest(port, "DELETE", "/api/saves/qa-does-not-exist")
        check("DELETE unknown -> 404 with server message",
              status == 404 and bdy.get("error") == "save not found: qa-does-not-exist",
              json.dumps((status, bdy)))
        status, bdy = rest(port, "DELETE", f"/api/saves/{sid}")
        check("DELETE already-deleted -> 404 again",
              status == 404 and bdy.get("error") == f"save not found: {sid}",
              json.dumps((status, bdy)))

        # ---- pan / drawer / modal-closed regression (real app.js) --------
        rc, res, out = run_node(os.path.join(ROOT, "scripts", "qa_modal_panzoom.js"), str(port))
        print("  [pan/drawer regression driver]")
        print("   " + (out or "").replace("\n", "\n   "))
        ok_steps = res and all(s["ok"] for s in res.get("steps", {}).values())
        check("pan/drawer regression driver all steps pass", rc == 0 and ok_steps,
              json.dumps(res) if res else f"rc={rc}")
    finally:
        try:
            ws.close()
        except Exception:
            pass
        stop_server(proc)
        time.sleep(0.5)
        check("port free after stop", port_free(port), f"port={port}")
        if os.path.exists(LOG):
            os.remove(LOG)

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} CHECK(S) FAILED:")
        for f in FAILURES:
            print(f"  FAIL {f}")
        sys.exit(1)
    print("RESULT: ALL LIVE SMOKE CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
