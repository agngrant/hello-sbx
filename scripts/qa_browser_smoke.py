#!/usr/bin/env python3
"""Playwright real-browser smoke probe for the LittleDungeons frontend.

Why this exists (docs/qa/test-plan.md §D): the 168 green unit tests + the
Node harness never execute the frontend in a real browser. This probe
boots the REAL server, loads the app root in headless Chromium, and proves
the ES-module boot survives a real DOM + real network stack:

  1. the lobby view renders (#lobby-view visible, #map-view still hidden);
  2. the boot-time connectWs() (js/net.js) fires — a WebSocket is
     constructed AND its handshake completes with the real server
     (#conn-status gains is-connected, #conn-label flips to "Connected");
  3. all six js/ ES modules (main/state/render/game/net/ui) are fetched
     with HTTP 200 — the module graph actually loaded, not just parsed;
  4. zero console.error messages and zero uncaught page exceptions —
     the "silent killer" guard: an ES-module boot that throws only in a
     real browser must fail this probe.

Server model: by default the probe starts its OWN server on a test port
(8765) via subprocess, waits for /health, and tears it down afterwards
(SIGTERM, then a port-free check). Pass --url to attach to an already
running server instead (no start/teardown).

Requires a browser-capable environment: the Playwright-managed Chromium
(`.venv/bin/python -m playwright install chromium`, needs network access
to the Playwright CDN) or a system Chrome/Chromium binary passed via
--executable. --no-sandbox/--disable-dev-shm-usage are always passed (CI
containers). See docs/qa/test-plan.md §D for the environment note.

Run:
  .venv/bin/python -m playwright install chromium   # once
  .venv/bin/python scripts/qa_browser_smoke.py                  # own server, port 8765
  .venv/bin/python scripts/qa_browser_smoke.py --port 9001      # own server, other port
  .venv/bin/python scripts/qa_browser_smoke.py --url http://127.0.0.1:8000
  .venv/bin/python scripts/qa_browser_smoke.py --executable /usr/bin/google-chrome

Exit 0 iff every check passes (1 = assertion failure, 2 = environment
problem: busy port, playwright/browser missing). QA artifact
(scripts/qa_*.py convention).
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "qa_browser_smoke.log")
DEFAULT_PORT = 8765
MODULES = ("main.js", "state.js", "render.js", "game.js", "net.js", "ui.js")

FAILURES: list[str] = []

try:
    from playwright.sync_api import Page
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_IMPORT_ERROR: str | None = None
except ImportError as e:  # environment guard — message, don't crash
    Page = object  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_IMPORT_ERROR = str(e)


class BrowserUnavailableError(RuntimeError):
    """Browser binary missing or won't launch (environment, not an app bug)."""


def check(label: str, cond: bool, detail: str = "") -> bool:
    """Record + print one PASS/FAIL line (qa_*.py convention)."""
    ok = bool(cond)
    print(f"  {'PASS' if ok else 'FAIL'} {label}"
          + (f"   -> {detail}" if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(label)
    return ok


# ---------------------------------------------------------------------------
# Server lifecycle (same shape as qa_modal_smoke.py)
# ---------------------------------------------------------------------------

def port_free(port: int) -> bool:
    """True when nothing on 127.0.0.1:port accepts a TCP connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return False
    except Exception:
        return True
    finally:
        s.close()


def start_server(port: int) -> subprocess.Popen:
    """Start the real FastAPI/uvicorn server on the probe port."""
    log = open(LOG, "ab")
    return subprocess.Popen(
        [sys.executable, "-m", "app.main", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, stdout=log, stderr=log)


def wait_health(base_url: str, tries: int = 80) -> bool:
    """Poll GET {base_url}/health until 200 (~20 s budget)."""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def stop_server(proc: subprocess.Popen | None) -> None:
    """SIGTERM the probe server (kill fallback); no-op if already dead."""
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


# ---------------------------------------------------------------------------
# Browser probe
# ---------------------------------------------------------------------------

def instrument_page(page: Page) -> dict:
    """Attach console / pageerror / module-response collectors.

    Args:
        page: The Playwright page to instrument (before navigation).

    Returns:
        Dict with the collector buckets: console_errors, page_errors,
        module_status (module filename -> HTTP status).
    """
    buckets: dict = {
        "console_errors": [],
        "page_errors": [],
        "module_status": {},
    }

    def on_console(msg: object) -> None:
        if getattr(msg, "type", None) == "error":
            buckets["console_errors"].append(str(getattr(msg, "text", "")))

    def on_pageerror(err: object) -> None:
        buckets["page_errors"].append(str(err))

    def on_response(resp: object) -> None:
        name = str(getattr(resp, "url", "")).rsplit("/", 1)[-1].split("?", 1)[0]
        if name in MODULES:
            buckets["module_status"][name] = getattr(resp, "status", None)

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)
    return buckets


def run_browser_probe(base_url: str, timeout_s: int,
                      executable: str | None, headed: bool) -> None:
    """Load the app root in Chromium and run every assertion.

    Args:
        base_url: App root URL (e.g. http://127.0.0.1:8765).
        timeout_s: Per-wait timeout in seconds.
        executable: Optional system Chrome/Chromium binary to launch.
        headed: Run headed (debug only).

    Raises:
        BrowserUnavailableError: the browser won't launch (environment).
        Exception: any navigation/wait timeout (assertion-side failure).
    """
    assert sync_playwright is not None, "caller checks PLAYWRIGHT_IMPORT_ERROR"
    timeout_ms = timeout_s * 1000
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=not headed,
                executable_path=executable,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as e:
            raise BrowserUnavailableError(str(e)) from e
        page = browser.new_page()
        try:
            buckets = instrument_page(page)

            # -- navigate; the boot-time WebSocket MUST be constructed -----
            # The expect_websocket listener arms BEFORE goto runs, so the
            # constructor cannot fire and be missed.
            with page.expect_websocket(timeout=timeout_ms) as ws_info:
                resp = page.goto(base_url, wait_until="load", timeout=timeout_ms)
            ws = ws_info.value
            check("app root: HTTP 200",
                  resp is not None and resp.status == 200,
                  f"status={getattr(resp, 'status', None)}")
            check("app root: title is 'LittleDungeons'",
                  page.title() == "LittleDungeons", page.title())
            check("ws: connectWs() constructed a WebSocket at boot",
                  ws is not None)
            check("ws: URL targets /ws?session=…",
                  ws is not None and "/ws?session=" in ws.url,
                  ws.url if ws is not None else "n/a")

            # -- lobby view rendered ---------------------------------------
            page.wait_for_selector("#lobby-view", state="visible",
                                   timeout=timeout_ms)
            check("lobby: #lobby-view is visible", True)
            check("lobby: #map-view stays hidden pre-join",
                  page.evaluate("document.getElementById('map-view').hidden")
                  is True)
            check("lobby: join heading rendered",
                  (page.text_content("#lobby-view h1") or "").strip()
                  == "Join a session")

            # -- handshake completed with the real server -------------------
            # onopen -> setConn("connected") -> #conn-status.is-connected
            # + #conn-label "Connected". Proves the full round-trip.
            page.wait_for_selector("#conn-status.is-connected",
                                   timeout=timeout_ms)
            check("ws: handshake done (#conn-status.is-connected)", True)
            label = (page.text_content("#conn-label") or "").strip()
            check("ws: #conn-label reads 'Connected'",
                  label == "Connected", repr(label))

            # -- ES module graph fetched ------------------------------------
            for m in MODULES:
                check(f"modules: /js/{m} fetched 200",
                      buckets["module_status"].get(m) == 200,
                      f"got {buckets['module_status'].get(m)}")

            # -- silent-killer guard -----------------------------------------
            # Small settle so a late error handler (setTimeout) can't slip
            # past the snapshot.
            page.wait_for_timeout(500)
            check("console: zero console.error messages",
                  not buckets["console_errors"],
                  " | ".join(buckets["console_errors"][:5]))
            check("console: zero uncaught page exceptions",
                  not buckets["page_errors"],
                  " | ".join(buckets["page_errors"][:5]))
        finally:
            page.close()
            browser.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse CLI args (qa_*.py convention: explicit knobs, sensible defaults)."""
    ap = argparse.ArgumentParser(
        description="Playwright real-browser smoke probe for LittleDungeons "
                    "(see module docstring).")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"port for the probe's own server "
                         f"(default {DEFAULT_PORT})")
    ap.add_argument("--url", default=None,
                    help="attach to an existing server (e.g. "
                         "http://127.0.0.1:8000) instead of starting one; "
                         "--port is then ignored")
    ap.add_argument("--executable", default=None,
                    help="launch this Chrome/Chromium binary instead of the "
                         "Playwright-managed build")
    ap.add_argument("--timeout", type=int, default=15,
                    help="per-wait timeout in seconds (default 15)")
    ap.add_argument("--headed", action="store_true",
                    help="run headed (debug only)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the probe; return 0 only when every check passes."""
    args = parse_args(argv)

    if sync_playwright is None:
        print(f"ERROR: playwright is not importable: {PLAYWRIGHT_IMPORT_ERROR}")
        print("  hint: uv pip install --python .venv/bin/python playwright "
              "&& .venv/bin/python -m playwright install chromium")
        return 2

    base_url = args.url.rstrip("/") if args.url \
        else f"http://127.0.0.1:{args.port}"
    print(f"target = {base_url}")

    proc: subprocess.Popen | None = None
    if not args.url:
        if not port_free(args.port):
            print(f"ERROR: port {args.port} is busy — pass another --port or "
                  "--url pointing at a running server")
            return 2
        proc = start_server(args.port)
        print(f"(own server pid {proc.pid} on 127.0.0.1:{args.port})")

    rc = 0
    try:
        check("server: /health answers 200", wait_health(base_url))
        run_browser_probe(base_url, args.timeout, args.executable,
                          args.headed)
    except BrowserUnavailableError as e:
        print(f"  FAIL browser unavailable: {e}")
        print("  hint: .venv/bin/python -m playwright install chromium "
              "(needs network access to the Playwright CDN), or pass "
              "--executable pointing at a system Chrome/Chromium binary")
        FAILURES.append("browser unavailable (environment, not app)")
        rc = 2
    except Exception as e:
        check("browser probe ran to completion", False, repr(e))
        rc = 1
    finally:
        if proc is not None:
            stop_server(proc)
            time.sleep(0.5)
            check("teardown: port free after stop", port_free(args.port),
                  f"port={args.port}")
        if os.path.exists(LOG):
            os.remove(LOG)

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} CHECK(S) FAILED:")
        for f in FAILURES:
            print(f"  FAIL {f}")
        return rc if rc == 2 else 1
    print("RESULT: ALL BROWSER SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
