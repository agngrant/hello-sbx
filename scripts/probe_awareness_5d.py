"""Task 5d (read-only): latency of the awareness hot-path (build_awareness,
the tier-view hot path; get_tier_view is its per-viewer entry) after the
per-viewer memoization. Hit path = repeat calls, same viewer/pos, grid
untouched (memoization hit). Miss path = fresh viewer at a fresh pos (cache
miss, visible_cells recomputed). N=200 each, time.perf_counter.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.awareness import build_awareness, _build_awareness_uncached
from app.models import Entity, Grid, Player

W = H = 40
cells = [["floor"] * W for _ in range(H)]
for x in range(0, W, 8):  # light wall structure
    for y in range(0, H, 6):
        cells[y][x] = "wall"
grid = Grid(name="probe", width=W, height=H, cells=cells)

players = {
    "p1": Player(id="p1", name="P1", role="player", entity_id="e1", awareness_radius=4),
    "p2": Player(id="p2", name="P2", role="player", entity_id="e2", awareness_radius=4),
    "p3": Player(id="p3", name="P3", role="player", entity_id="e3", awareness_radius=4),
}
entities = {
    "e1": Entity(id="e1", name="E1", kind="npc", team="party", x=5, y=5),
    "e2": Entity(id="e2", name="E2", kind="npc", team="party", x=10, y=12),
    "e3": Entity(id="e3", name="E3", kind="npc", team="party", x=20, y=25),
}


def timeit(fn, n: int) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return ts


# Warm up: prime the (viewer, entities, grid) memoization + interpreter.
for _ in range(5):
    build_awareness(players["p1"], entities, grid)

N = 200
hits = timeit(lambda: build_awareness(players["p1"], entities, grid), N)
misses = timeit(lambda: _build_awareness_uncached(players["p3"], entities, grid), N)


def report(name: str, ts: list[float]) -> None:
    ts.sort()
    print(
        f"{name}: N={len(ts)} p50={ts[len(ts)//2]:.3f}ms "
        f"p95={ts[int(len(ts)*0.95)]:.3f}ms mean={statistics.fmean(ts):.3f}ms"
    )


report("hit ", hits)
report("miss", misses)
