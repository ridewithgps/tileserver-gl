# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx",
# ]
# ///
"""
Benchmark: PNG vs WebP vs PBF tile performance.

Runs the same tile requests against PNG, WebP, and PBF endpoints sequentially,
then compares latency and throughput. PNG and WebP go through the renderer pool
(/styles/), while PBF is served directly from mbtiles (/data/) — this gives a
baseline for raw tile serving without render overhead.

Usage:
  uv run scripts/benchmark_format.py [base_url] [duration_per_format] [concurrency]

Example:
  uv run scripts/benchmark_format.py http://localhost:8080 30 10
  uv run scripts/benchmark_format.py https://vector-test.ridewithgps.com 30 10
"""

import asyncio
import random
import sys
import time
from datetime import datetime, timezone

SEED = 42

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 30
CONCURRENCY = int(sys.argv[3]) if len(sys.argv) > 3 else 10

STYLE = "rwgpscycle"
DATA_SOURCE = "rwgps"  # vector tile source name for pbf

FORMATS = ["png", "webp", "pbf"]

# Bounding box defined at z5 in XYZ (slippy) coordinates.
# Covers the continental US: Pacific NW to East Coast.
#   top-left:  z5/x5/y11   (Portland, OR area)
#   bot-right: z5/x9/y12   (East Coast / Carolinas)
BBOX_Z = 5
BBOX_X_MIN, BBOX_X_MAX = 5, 9
BBOX_Y_MIN, BBOX_Y_MAX = 11, 12

# Zoom levels to test and how many tiles to sample at each.
# At low zooms the full bbox is small enough to enumerate; at high zooms we sample.
ZOOM_SAMPLES = [
    (5, None),   # 10 tiles — enumerate all
    (7, None),   # 160 tiles — enumerate all
    (9, 40),     # 2,560 possible — sample 40
    (11, 40),    # 40,960 possible — sample 40
    (13, 30),    # 655,360 possible — sample 30
    (14, 20),    # 2.6M possible — sample 20
]


def generate_tiles(rng: random.Random) -> list[tuple[int, int, int]]:
    """Generate benchmark tile coordinates from the bounding box."""
    tiles = []
    for z, sample_n in ZOOM_SAMPLES:
        scale = 1 << (z - BBOX_Z)
        x_min = BBOX_X_MIN * scale
        x_max = (BBOX_X_MAX + 1) * scale - 1
        y_min = BBOX_Y_MIN * scale
        y_max = (BBOX_Y_MAX + 1) * scale - 1

        if sample_n is None:
            # Enumerate every tile in the bbox at this zoom
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    tiles.append((z, x, y))
        else:
            # Randomly sample from the bbox
            for _ in range(sample_n):
                x = rng.randint(x_min, x_max)
                y = rng.randint(y_min, y_max)
                tiles.append((z, x, y))
    return tiles


RENDER_TILES = generate_tiles(random.Random(SEED))


def ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Stats:
    def __init__(self, label: str):
        self.label = label
        self.total = 0
        self.ok = 0
        self.by_status: dict[int, int] = {}
        self.errors = 0
        self.latencies: list[float] = []
        self.total_bytes = 0
        self.start = time.monotonic()
        self.end: float = 0

    def record(self, status: int, latency: float, size: int):
        self.total += 1
        self.by_status[status] = self.by_status.get(status, 0) + 1
        if status == 200:
            self.ok += 1
            self.latencies.append(latency)
            self.total_bytes += size

    def record_error(self, err: str):
        self.total += 1
        self.errors += 1

    def finish(self):
        self.end = time.monotonic()

    @property
    def elapsed(self) -> float:
        return (self.end or time.monotonic()) - self.start

    def percentile(self, p: float) -> float:
        if not self.latencies:
            return 0
        self.latencies.sort()
        idx = min(int(len(self.latencies) * p), len(self.latencies) - 1)
        return self.latencies[idx]

    def report(self):
        elapsed = self.elapsed
        rps = self.ok / elapsed if elapsed > 0 else 0
        avg_size = self.total_bytes / self.ok if self.ok else 0
        print(f"\n{'─' * 60}")
        print(f"  {self.label}")
        print(f"{'─' * 60}")
        print(f"  Duration:    {elapsed:.1f}s")
        print(f"  Requests:    {self.total:,} ({self.ok:,} ok, {self.errors} errors)")
        print(f"  Throughput:  {rps:,.1f} tiles/s")
        if self.latencies:
            n = len(self.latencies)
            print(f"  Avg latency: {sum(self.latencies) / n * 1000:.0f}ms")
            print(f"  p50:         {self.percentile(0.50) * 1000:.0f}ms")
            print(f"  p95:         {self.percentile(0.95) * 1000:.0f}ms")
            print(f"  p99:         {self.percentile(0.99) * 1000:.0f}ms")
            print(f"  Max:         {self.latencies[-1] * 1000:.0f}ms")
        print(f"  Avg size:    {avg_size:,.0f} bytes")
        print(f"  Total data:  {self.total_bytes / 1024 / 1024:.1f} MB")


async def worker(client: httpx.AsyncClient, paths: list[str], stats: Stats, end: float, rng: random.Random):
    while time.time() < end:
        path = rng.choice(paths)
        try:
            t0 = time.monotonic()
            resp = await client.get(f"{BASE}{path}")
            stats.record(resp.status_code, time.monotonic() - t0, len(resp.content))
        except Exception as e:
            stats.record_error(type(e).__name__)


def build_paths(fmt: str) -> list[str]:
    if fmt == "pbf":
        return [f"/data/{DATA_SOURCE}/{z}/{x}/{y}.{fmt}" for z, x, y in RENDER_TILES]
    return [f"/styles/{STYLE}/{z}/{x}/{y}.{fmt}" for z, x, y in RENDER_TILES]


async def run_format(fmt: str) -> Stats:
    paths = build_paths(fmt)
    stats = Stats(f"{fmt.upper()} — {CONCURRENCY} workers × {DURATION}s")

    print(f"\n[{ts()}] Running {fmt.upper()}...")

    # Warmup — prime the renderer and any caches
    async with httpx.AsyncClient(timeout=30.0) as client:
        for path in paths[:3]:
            try:
                await client.get(f"{BASE}{path}")
            except Exception:
                pass
    await asyncio.sleep(1)

    end = time.time() + DURATION
    limits = httpx.Limits(max_connections=CONCURRENCY * 2, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:
        await asyncio.gather(*[
            worker(client, paths, stats, end, random.Random(SEED + i))
            for i in range(CONCURRENCY)
        ])
    stats.finish()
    stats.report()
    return stats


async def main():
    print(f"[{ts()}] === PNG vs WebP vs PBF Benchmark ===")
    print(f"  Target:      {BASE}")
    print(f"  Style:       {STYLE}")
    print(f"  Duration:    {DURATION}s per format")
    print(f"  Concurrency: {CONCURRENCY} workers")
    print(f"  Bbox:        z{BBOX_Z} x[{BBOX_X_MIN}–{BBOX_X_MAX}] y[{BBOX_Y_MIN}–{BBOX_Y_MAX}] (continental US)")
    print(f"  Zooms:       {', '.join(f'z{z}' for z, _ in ZOOM_SAMPLES)}")
    print(f"  Tiles:       {len(RENDER_TILES)} ({', '.join(f'z{z}:{n or 'all'}' for z, n in ZOOM_SAMPLES)})")

    # Check server is up
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{BASE}/styles/{STYLE}/0/0/0.png")
            if r.status_code != 200:
                print(f"  WARNING: warmup returned {r.status_code}")
        except Exception as e:
            print(f"  FATAL: server not reachable — {e}")
            sys.exit(1)

    # Run each format sequentially with a cooldown between
    results: dict[str, Stats] = {}
    for fmt in FORMATS:
        results[fmt] = await run_format(fmt)
        # Cooldown — let the pool settle between runs
        print(f"\n[{ts()}] Cooldown (5s)...")
        await asyncio.sleep(5)

    # Comparison table
    print(f"\n{'=' * 60}")
    print(f"  COMPARISON")
    print(f"{'=' * 60}")

    print(f"  {'Format':<8} {'Tiles/s':>9} {'Avg ms':>9} {'p95 ms':>9} {'p99 ms':>9} {'Avg KB':>9}")
    print(f"  {'─' * 56}")

    for fmt in FORMATS:
        s = results[fmt]
        rps = s.ok / s.elapsed if s.elapsed else 0
        avg_ms = sum(s.latencies) / len(s.latencies) * 1000 if s.latencies else 0
        p95 = s.percentile(0.95) * 1000
        p99 = s.percentile(0.99) * 1000
        avg_kb = s.total_bytes / s.ok / 1024 if s.ok else 0

        print(f"  {fmt.upper():<8} {rps:>9.1f} {avg_ms:>9.0f} {p95:>9.0f} {p99:>9.0f} {avg_kb:>9.1f}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
