"""QA probe: proves the asyncio.to_thread off-load genuinely moves heavy work
OFF the event loop (the defect being fixed), vs the old sync path.

Why a watchdog, not an in-loop heartbeat:
  An in-loop "await asyncio.sleep" timer is itself a callback on the same
  loop, so a long synchronous block can coalesce/cancel its pending timer and
  FAIL to record the stall (measured: 500ms hard on-loop block -> max recorded
  gap 1.2ms). A cross-thread watchdog that pings the loop via
  call_soon_threadsafe and times the round-trip on real wall-clock is immune:
  it reports max latency ~ the block duration when the loop is blocked, and a
  few ms when the work runs in a worker thread. Validated against a known
  500ms on-loop block (max ~2000ms) and the same work off-loop (max ~2ms).

Workloads map to the changed handlers:
  * save_bundle 24x24 (json + fsync)      -> /api/saves* write path
  * detect_grid on a large PNG upload     -> WS image-upload decode
  * generate_grid 60x60 + thumbnail       -> /api/generate heavy compute

For each (op, mode) we report: op duration, the thread that ran the work,
watchdog max/avg loop latency, and a "loop blocked?" verdict.

  LEGACY (pre-fix): op called synchronously on the loop thread -> blocks.
  FIXED  (current): op via asyncio.to_thread -> loop keeps responding.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.saves as saves_mod  # noqa: E402
from app.detection import detect_grid, grid_to_thumbnail_png  # noqa: E402
from app.generation import generate_grid  # noqa: E402

# Redirect save writes to a throwaway dir so the probe is idempotent.
_TMP_SAVES = tempfile.mkdtemp(prefix="qa_offload_saves_")
saves_mod.SAVES_DIR = _TMP_SAVES

BLOCK_THRESHOLD_MS = 50.0  # a worker op should never stall the loop this long


def _make_image(w=2048, h=2048):
    import random
    from PIL import Image
    random.seed(7)
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (random.randrange(256), random.randrange(256),
                        random.randrange(256))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


IMAGE = _make_image(2048, 2048)


def _save_work():
    g = generate_grid(24, 24, "probe", seed=42)
    rec = {"name": "probe", "map_name": "probe", "created_at": "2025-01-01T00:00:00Z"}
    saves_mod.save_bundle(rec, g, [])


def _upload_work():
    detect_grid(IMAGE, name="big", cols=32, rows=32)


def _gen_work():
    g = generate_grid(60, 60, "gen", seed=42)
    grid_to_thumbnail_png(g)


async def _run_op(loop, op_coro):
    """Run `op_coro` on `loop` while a cross-thread watchdog measures loop
    responsiveness. Returns (op_ms, watchdog_max_ms, watchdog_avg_ms, n)."""
    lat = []
    stop = threading.Event()

    def watchdog():
        while not stop.is_set():
            ev = threading.Event()
            t0 = time.perf_counter()

            def cb():
                ev.set()
            loop.call_soon_threadsafe(cb)
            ev.wait(2.0)
            lat.append(time.perf_counter() - t0)
            time.sleep(0.005)

    th = threading.Thread(target=watchdog, daemon=True)
    th.start()
    t0 = time.perf_counter()
    await op_coro
    op_ms = 1000 * (time.perf_counter() - t0)
    stop.set()
    th.join()
    wmx = max(lat) * 1000 if lat else 0.0
    wavg = (sum(lat) / len(lat) * 1000) if lat else 0.0
    return op_ms, wmx, wavg, len(lat)


def build_case(name, work):
    state = {"thread": None}

    def run():
        state["thread"] = threading.get_ident()
        work()

    async def legacy():
        await asyncio.sleep(0.05)
        run()  # BLOCKS the loop (pre-fix behaviour)

    async def fixed():
        await asyncio.sleep(0.05)
        await asyncio.to_thread(run)  # OFF the loop (current behaviour)

    return name, legacy, fixed, state


async def main() -> int:
    cases = [
        build_case("save_bundle 24x24 (json+fsync)", _save_work),
        build_case(f"upload detect_grid ({len(IMAGE)/1e6:.1f}MB PNG)", _upload_work),
        build_case("generate_grid 60x60 + thumbnail", _gen_work),
    ]
    loop = asyncio.get_running_loop()
    loop_tid = threading.get_ident()
    print("=== OFFLOAD / EVENT-LOOP-BLOCKING PROBE (cross-thread watchdog) ===")
    print(f"loop thread id = {loop_tid}")
    hdr = f"{'case':<38} {'mode':<6} {'work':>8} {'loopmax':>9} {'loopavg':>9} {'n':>4}  {'ran':<9} {'verdict'}"
    print(hdr)
    print("-" * len(hdr))
    ok = True
    for name, legacy, fixed, state in cases:
        last_fixed = None
        for mode, factory in (("LEGACY", legacy), ("FIXED", fixed)):
            state["thread"] = None
            op_ms, wmx, wavg, n = await _run_op(loop, factory())
            onloop = (state["thread"] == loop_tid)
            blocked = wmx > BLOCK_THRESHOLD_MS
            ran = "on-loop" if onloop else "off-loop"
            verdict = "blocks-loop" if blocked else "loop-free"
            print(f"{name:<38} {mode:<6} {op_ms:>7.0f}ms {wmx:>8.1f}ms "
                  f"{wavg:>8.1f}ms {n:>4}  {ran:<9} {verdict}")
            if mode == "FIXED":
                last_fixed = (state["thread"] == loop_tid, wmx)

        f_onloop, fmx = last_fixed
        fixed_ok = (not f_onloop) and (fmx < BLOCK_THRESHOLD_MS)
        ok = ok and fixed_ok
        print(f"  -> FIXED off-loop={not f_onloop} loopmax={fmx:.1f}ms "
              f"{'(PASS)' if fixed_ok else '(FAIL)'}")
        print()

    print("VERDICT:", "ALL PASS — offload genuinely moves work OFF the event loop"
          if ok else "FAIL — see rows above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
