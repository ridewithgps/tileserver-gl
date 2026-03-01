# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx",
# ]
# ///
"""
Stress test: HTTP request hammerer for tileserver-gl.

50 async workers fire requests as fast as possible against a fixed set
of data endpoints. Run alongside stress_churn.py which mutates the
underlying .mbtiles files for the stresstest_* sources.

Expected status codes:
  200 — tile served successfully
  204 — empty tile (sparse=false, valid)
  404 — source not loaded or tile out of bounds (normal during churn)
  5xx — BUG, should never happen

Any non-200/204/404 response is flagged as an anomaly with a timestamp
for correlation with the churn log and docker logs.

Usage:
  uv run scripts/stress_requests.py [base_url] [duration_seconds] [concurrency]

Example:
  uv run scripts/stress_requests.py http://localhost:8080 120 50

Then correlate anomalies with:
  docker compose logs --timestamps tileserver | grep <timestamp>
"""

import asyncio
import random
import sys
import time
from datetime import datetime, timezone

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 60
CONCURRENCY = int(sys.argv[3]) if len(sys.argv) > 3 else 50

# Must match stress_churn.py
SLOTS = [f"stresstest_{i:02d}" for i in range(1, 51)]

# Tile coordinates known to exist in the airnow/aqi source files (z0-z5, pbf)
TILES = [
    (0, 0, 0), (1, 0, 0), (1, 1, 1), (2, 1, 1), (2, 2, 2),
    (3, 2, 3), (3, 4, 2), (4, 3, 5), (4, 8, 4), (5, 9, 10),
]

# Static sources — always loaded, should always 200
STATIC = [
    "/data/rwgps/0/0/0.pbf",
    "/data/rwgps/2/1/1.pbf",
    "/data/elevation/0/0/0.png",
    "/data/elevation/2/1/1.png",
    "/health",
]

# Pre-built dynamic paths: 50 slots x 10 tiles = 500 paths, zero per-request work
DYNAMIC = [
    f"/data/{slot}/{z}/{x}/{y}.pbf"
    for slot in SLOTS
    for z, x, y in TILES
]


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
        if status not in (200, 204, 404):
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
        print(f"  Req/s:       {rps:,.0f}")
        if self.latencies:
            self.latencies.sort()
            n = len(self.latencies)
            print(f"  Avg:         {sum(self.latencies) / n * 1000:.1f}ms")
            print(f"  p50:         {self.latencies[n // 2] * 1000:.1f}ms")
            print(f"  p95:         {self.latencies[int(n * 0.95)] * 1000:.1f}ms")
            print(f"  p99:         {self.latencies[int(n * 0.99)] * 1000:.1f}ms")
            print(f"  Max:         {self.latencies[-1] * 1000:.1f}ms")
        print(f"  Conn errors: {self.errors}")
        print(f"  Status codes:")
        for code in sorted(self.by_status):
            pct = self.by_status[code] / self.total * 100 if self.total else 0
            print(f"    {code}: {self.by_status[code]:>8,}  ({pct:.1f}%)")
        if self.anomalies:
            print(f"  ANOMALIES:   {len(self.anomalies)}")


async def worker(client: httpx.AsyncClient, stats: Stats, paths: list[str], end: float):
    while time.time() < end:
        path = random.choice(paths)
        try:
            t0 = time.monotonic()
            resp = await client.get(f"{BASE}{path}")
            stats.record(resp.status_code, time.monotonic() - t0, path)
        except Exception as e:
            stats.record_error(path, type(e).__name__)


async def run_phase(label: str, paths: list[str], duration: int) -> Stats:
    stats = Stats(label)
    end = time.time() + duration
    limits = httpx.Limits(
        max_connections=CONCURRENCY * 2, max_keepalive_connections=CONCURRENCY
    )
    async with httpx.AsyncClient(limits=limits, timeout=10.0) as client:
        await asyncio.gather(*[
            worker(client, stats, paths, end) for _ in range(CONCURRENCY)
        ])
    return stats


async def main():
    phase = max(DURATION // 3, 10)

    print(f"[{ts()}] === Tileserver Stress Test ===")
    print(f"  Target:       {BASE}")
    print(f"  Duration:     {DURATION}s total ({phase}s per phase)")
    print(f"  Concurrency:  {CONCURRENCY} async workers")
    print(f"  Dynamic paths: {len(DYNAMIC)} (50 slots x 10 tiles)")
    print(f"  Static paths:  {len(STATIC)}")

    # Phase 1: static only — establishes a baseline
    print(f"\n[{ts()}] --- Phase 1: Static sources ({phase}s) ---")
    s1 = await run_phase("Phase 1: Static (baseline)", STATIC, phase)
    s1.report()

    # Phase 2: dynamic only — maximum race condition exposure
    print(f"\n[{ts()}] --- Phase 2: Dynamic sources ({phase}s) ---")
    s2 = await run_phase("Phase 2: Dynamic (churn targets)", DYNAMIC, phase)
    s2.report()

    # Phase 3: mixed — realistic load
    print(f"\n[{ts()}] --- Phase 3: Mixed ({phase}s) ---")
    s3 = await run_phase("Phase 3: Mixed", STATIC + DYNAMIC, phase)
    s3.report()

    # Health check
    print(f"\n[{ts()}] --- Health Check ---")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{BASE}/health")
            ok = r.status_code == 200
            print(f"  Server: {'PASS' if ok else 'FAIL'} ({r.status_code})")
        except Exception as e:
            print(f"  Server: DEAD ({e})")
            sys.exit(1)

    # Grand summary
    total = s1.total + s2.total + s3.total
    errs = s1.errors + s2.errors + s3.errors
    all_anomalies = s1.anomalies + s2.anomalies + s3.anomalies

    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {total:,} requests, {errs} errors, {len(all_anomalies)} anomalies")
    if not all_anomalies and not errs:
        print(f"  RESULT: ALL CLEAN — no crashes, no 5xx")
    else:
        print(f"  RESULT: ISSUES DETECTED")
    print(f"{'=' * 60}")

    if all_anomalies:
        print(f"\n--- All anomalies (correlate with churn + docker logs) ---")
        for a in all_anomalies:
            print(a)


if __name__ == "__main__":
    asyncio.run(main())
