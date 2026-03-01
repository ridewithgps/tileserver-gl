# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx",
# ]
# ///
"""
Stress test: renderer pool for tileserver-gl.

Hammers the /styles/{style}/{z}/{x}/{y}.png endpoint with high concurrency
to stress the maplibre-gl-native renderer pool. This tests the fixes from:
  - PR #1798: null renderer crashes from failed fetches
  - PR #1825: pool.destroy() TypeError, double response, memory leak

The renderer pool is the most fragile part of tileserver-gl. Each PNG tile
request acquires a renderer from a pool, renders the tile using the native
C++ maplibre-gl library, and releases it back. Under load, renderers can
crash, hang, or leak — causing cascading failures.

Expected: all responses should be 200 (valid PNG) or 404 (out of bounds).
Any 500/503 or connection errors indicate a renderer pool problem.

Usage:
  uv run scripts/stress_renderer.py [base_url] [duration_seconds] [concurrency]

Example:
  uv run scripts/stress_renderer.py http://localhost:8080 120 30
"""

import asyncio
import random
import sys
import time
from datetime import datetime, timezone

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 60
CONCURRENCY = int(sys.argv[3]) if len(sys.argv) > 3 else 30

STYLE = "rwgpscycle"

# Mix of zoom levels to stress different renderer code paths:
# - Low zoom (0-4): large area, many features, heavy render
# - Mid zoom (5-10): typical usage
# - High zoom (11-14): detailed, lots of label placement
RENDER_TILES = [
    # Low zoom — whole world / continent scale
    (0, 0, 0),
    (1, 0, 0), (1, 1, 0), (1, 0, 1), (1, 1, 1),
    (2, 0, 1), (2, 1, 1), (2, 2, 1), (2, 3, 1),
    (3, 1, 2), (3, 2, 2), (3, 4, 2), (3, 4, 3),
    # Mid zoom — state / region scale (Portland, OR area)
    (5, 5, 11), (5, 5, 12),
    (6, 10, 22), (6, 10, 23),
    (7, 20, 45), (7, 21, 45),
    (8, 41, 90), (8, 41, 91),
    (9, 82, 181), (9, 83, 181),
    (10, 164, 362), (10, 165, 362),
    # High zoom — city / street scale
    (11, 329, 724), (11, 330, 724),
    (12, 658, 1449), (12, 659, 1449),
    (13, 1317, 2898), (13, 1317, 2899),
    (14, 2634, 5797), (14, 2635, 5797),
]

PATHS = [f"/styles/{STYLE}/{z}/{x}/{y}.png" for z, x, y in RENDER_TILES]


def ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Stats:
    def __init__(self, label: str):
        self.label = label
        self.total = 0
        self.by_status: dict[int, int] = {}
        self.errors = 0
        self.latencies: list[float] = []
        self.anomalies: list[str] = []
        self.start = time.monotonic()

    def record(self, status: int, latency: float, path: str):
        self.total += 1
        self.by_status[status] = self.by_status.get(status, 0) + 1
        self.latencies.append(latency)
        if status not in (200, 404):
            msg = f"[{ts()}] !! HTTP {status} on {path} ({latency * 1000:.0f}ms)"
            self.anomalies.append(msg)
            print(msg)

    def record_error(self, path: str, err: str):
        self.total += 1
        self.errors += 1
        msg = f"[{ts()}] !! CONN_ERR on {path}: {err}"
        self.anomalies.append(msg)
        print(msg)

    def report(self):
        elapsed = time.monotonic() - self.start
        rps = self.total / elapsed if elapsed > 0 else 0
        print(f"\n{'=' * 60}")
        print(f"  {self.label}")
        print(f"{'=' * 60}")
        print(f"  Requests:    {self.total:,}")
        print(f"  Req/s:       {rps:,.1f}")
        if self.latencies:
            self.latencies.sort()
            n = len(self.latencies)
            print(f"  Avg:         {sum(self.latencies) / n * 1000:.0f}ms")
            print(f"  p50:         {self.latencies[n // 2] * 1000:.0f}ms")
            print(f"  p95:         {self.latencies[int(n * 0.95)] * 1000:.0f}ms")
            print(f"  p99:         {self.latencies[int(n * 0.99)] * 1000:.0f}ms")
            print(f"  Max:         {self.latencies[-1] * 1000:.0f}ms")
        print(f"  Conn errors: {self.errors}")
        print(f"  Status codes:")
        for code in sorted(self.by_status):
            pct = self.by_status[code] / self.total * 100 if self.total else 0
            print(f"    {code}: {self.by_status[code]:>8,}  ({pct:.1f}%)")
        if self.anomalies:
            print(f"  ANOMALIES:   {len(self.anomalies)}")


async def worker(client: httpx.AsyncClient, stats: Stats, end: float):
    while time.time() < end:
        path = random.choice(PATHS)
        try:
            t0 = time.monotonic()
            resp = await client.get(f"{BASE}{path}")
            stats.record(resp.status_code, time.monotonic() - t0, path)
        except Exception as e:
            stats.record_error(path, type(e).__name__)


async def main():
    print(f"[{ts()}] === Renderer Pool Stress Test ===")
    print(f"  Target:      {BASE}")
    print(f"  Style:       {STYLE}")
    print(f"  Duration:    {DURATION}s")
    print(f"  Concurrency: {CONCURRENCY} workers")
    print(f"  Tile paths:  {len(PATHS)} ({len(RENDER_TILES)} tiles)")
    print(f"  Zoom range:  z0 - z{max(z for z, _, _ in RENDER_TILES)}")

    # Warmup — single request to ensure style is loaded
    print(f"\n[{ts()}] Warmup...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(f"{BASE}/styles/{STYLE}/0/0/0.png")
            print(f"  Warmup: {r.status_code} ({len(r.content)} bytes)")
            if r.status_code != 200:
                print(f"  WARNING: style may not be rendering correctly")
        except Exception as e:
            print(f"  Warmup FAILED: {e}")
            print(f"  Is the server running? Is '{STYLE}' a valid style?")
            sys.exit(1)

    # Ramp up concurrency in phases to stress the pool progressively
    phases = [
        (CONCURRENCY // 3 or 1, DURATION // 3),
        (CONCURRENCY * 2 // 3 or 2, DURATION // 3),
        (CONCURRENCY, DURATION // 3 or DURATION),
    ]

    all_stats = []
    for i, (conc, dur) in enumerate(phases, 1):
        dur = max(dur, 10)
        print(f"\n[{ts()}] --- Phase {i}: {conc} workers for {dur}s ---")
        stats = Stats(f"Phase {i}: {conc} concurrent workers")
        end = time.time() + dur
        limits = httpx.Limits(max_connections=conc * 2, max_keepalive_connections=conc)
        async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:
            await asyncio.gather(*[
                worker(client, stats, end) for _ in range(conc)
            ])
        stats.report()
        all_stats.append(stats)

    # Health check
    print(f"\n[{ts()}] --- Health Check ---")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{BASE}/health")
            print(f"  Server: {'PASS' if r.status_code == 200 else 'FAIL'} ({r.status_code})")
        except Exception as e:
            print(f"  Server: DEAD ({e})")
            sys.exit(1)

    # Summary
    total = sum(s.total for s in all_stats)
    errs = sum(s.errors for s in all_stats)
    anomalies = []
    for s in all_stats:
        anomalies.extend(s.anomalies)

    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {total:,} rendered tiles, {errs} errors, {len(anomalies)} anomalies")
    if not anomalies and not errs:
        print(f"  RESULT: ALL CLEAN — renderer pool is stable")
    else:
        print(f"  RESULT: ISSUES DETECTED")
    print(f"{'=' * 60}")

    if anomalies:
        print(f"\n--- All anomalies ---")
        for a in anomalies:
            print(a)


if __name__ == "__main__":
    asyncio.run(main())
