"""Sustained load test — mixed-asset concurrent load with zero-failure assertion."""

import asyncio
import random

import pytest

from tests.tile_coords import STYLE, DATA_RWGPS, DATA_ELEVATION, tiles_for
from tests.load_helpers import run_load, compute_percentiles, format_stats_table


pytestmark = pytest.mark.stability


def build_mixed_paths() -> list[str]:
    """Build a weighted path list covering all asset types."""
    rwgps_tiles = tiles_for("rwgps")
    elevation_tiles = tiles_for("elevation")

    rendered_png = [
        f"/styles/{STYLE}/{t['z']}/{t['x']}/{t['y']}.png"
        for t in rwgps_tiles
    ]
    rendered_webp = [
        f"/styles/{STYLE}/{t['z']}/{t['x']}/{t['y']}.webp"
        for t in rwgps_tiles
    ]
    vector_pbf = [
        f"/data/{DATA_RWGPS}/{t['z']}/{t['x']}/{t['y']}.pbf"
        for t in rwgps_tiles
    ]
    elevation_png = [
        f"/data/{DATA_ELEVATION}/{t['z']}/{t['x']}/{t['y']}.png"
        for t in elevation_tiles
    ]
    fonts = ["/fonts/Noto%20Sans%20Regular/0-255.pbf"]

    # Weighted distribution: 40% rendered PNG, 20% WebP, 20% PBF, 10% elevation, 10% fonts
    paths = []
    paths.extend(rendered_png * 2)
    paths.extend(rendered_webp * 1)
    paths.extend(vector_pbf * 1)
    paths.extend(elevation_png * 1)
    paths.extend(fonts * max(1, len(rendered_png) // 10))
    random.shuffle(paths)
    return paths


class TestSustainedLoad:
    """Run sustained concurrent requests and assert zero critical failures."""

    @pytest.mark.timeout(0)  # Disable timeout — duration controls test length
    def test_sustained_mixed_load(self, base_url, duration, concurrency):
        paths = build_mixed_paths()
        result = asyncio.run(run_load(base_url, paths, duration, concurrency))

        # Report stats
        pcts = compute_percentiles(result.latencies)
        req_per_sec = result.total_requests / max(duration, 1)
        print(f"\n{'=' * 60}")
        print(f"Sustained load: {duration}s, {concurrency} workers")
        print(f"Total requests: {result.total_requests} ({req_per_sec:.1f} req/s)")
        print(f"Latency p50={pcts[50]:.0f}ms p95={pcts[95]:.0f}ms p99={pcts[99]:.0f}ms")
        print(f"Status codes: {dict(sorted(result.status_counts.items()))}")
        print(f"Errors: {result.errors}, Connection errors: {result.connection_errors}")
        if result.anomalies:
            print(f"Anomalies ({len(result.anomalies)}):")
            for a in result.anomalies[:10]:
                print(f"  {a['path']} → {a['status']}")
        print(f"{'=' * 60}")

        # Assertions
        assert result.connection_errors == 0, (
            f"Got {result.connection_errors} connection errors during sustained load"
        )
        assert result.status_counts.get(500, 0) == 0, (
            f"Got {result.status_counts.get(500, 0)} 500 errors"
        )
        assert result.status_counts.get(503, 0) == 0, (
            f"Got {result.status_counts.get(503, 0)} 503 errors — renderer pool may be exhausted"
        )

    @pytest.mark.timeout(60)
    def test_post_load_health(self, assert_healthy):
        """Verify server responds to health check after sustained load."""
        assert_healthy()

    @pytest.mark.timeout(60)
    def test_post_load_no_crash(self, assert_no_crash):
        """Verify server did not crash (SIGSEGV) during sustained load."""
        assert_no_crash()
