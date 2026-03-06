"""Rendered tile throughput and latency benchmarks."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from tests.tile_coords import STYLE, tiles_for
from tests.load_helpers import run_load, compute_percentiles


pytestmark = pytest.mark.performance

BASELINES_PATH = Path(__file__).parent / "baselines.json"


def load_baselines():
    with open(BASELINES_PATH) as f:
        return json.load(f)


RENDERED_TILES = tiles_for("rwgps")


def build_paths(fmt: str) -> list[str]:
    return [f"/styles/{STYLE}/{t['z']}/{t['x']}/{t['y']}.{fmt}" for t in RENDERED_TILES]


def warmup(client, paths, count=3):
    """Prime the renderer pool with a few sequential requests."""
    for path in paths[:count]:
        resp = client.get(path)
        assert resp.status_code == 200, f"Warmup failed: {path} → {resp.status_code}"


class TestBenchmark:
    """Rendered tile throughput and latency benchmarks."""

    @pytest.mark.timeout(0)
    def test_benchmark_png(self, base_url, client, duration, concurrency, perf_results):
        baselines = load_baselines()["rendered_png"]
        paths = build_paths("png")
        warmup(client, paths)

        result = asyncio.run(run_load(base_url, paths, duration, concurrency))
        pcts = compute_percentiles(result.latencies)
        tiles_per_sec = result.total_requests / max(duration, 1)

        perf_results.append({
            "format": "PNG",
            "tiles_per_sec": tiles_per_sec,
            "p50": pcts[50], "p95": pcts[95], "p99": pcts[99],
        })

        print(f"\nPNG: {tiles_per_sec:.1f} tiles/s, p50={pcts[50]:.0f}ms p95={pcts[95]:.0f}ms p99={pcts[99]:.0f}ms")

        assert tiles_per_sec >= baselines["min_tiles_per_sec"], (
            f"PNG throughput {tiles_per_sec:.1f} < {baselines['min_tiles_per_sec']} tiles/s"
        )
        assert pcts[95] <= baselines["max_p95_ms"], (
            f"PNG p95 latency {pcts[95]:.0f}ms > {baselines['max_p95_ms']}ms"
        )

    @pytest.mark.timeout(0)
    def test_benchmark_webp(self, base_url, client, duration, concurrency, perf_results):
        baselines = load_baselines()["rendered_webp"]
        paths = build_paths("webp")
        warmup(client, paths)

        # Brief cooldown after PNG benchmark
        time.sleep(2)

        result = asyncio.run(run_load(base_url, paths, duration, concurrency))
        pcts = compute_percentiles(result.latencies)
        tiles_per_sec = result.total_requests / max(duration, 1)

        perf_results.append({
            "format": "WebP",
            "tiles_per_sec": tiles_per_sec,
            "p50": pcts[50], "p95": pcts[95], "p99": pcts[99],
        })

        print(f"\nWebP: {tiles_per_sec:.1f} tiles/s, p50={pcts[50]:.0f}ms p95={pcts[95]:.0f}ms p99={pcts[99]:.0f}ms")

        assert tiles_per_sec >= baselines["min_tiles_per_sec"], (
            f"WebP throughput {tiles_per_sec:.1f} < {baselines['min_tiles_per_sec']} tiles/s"
        )
        assert pcts[95] <= baselines["max_p95_ms"], (
            f"WebP p95 latency {pcts[95]:.0f}ms > {baselines['max_p95_ms']}ms"
        )
