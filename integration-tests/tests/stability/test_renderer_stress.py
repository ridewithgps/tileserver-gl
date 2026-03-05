"""Renderer pool stress test — 3-phase ramping to detect segfaults and pool exhaustion."""

import asyncio
import time

import pytest

from tests.tile_coords import STYLE, tiles_for
from tests.load_helpers import run_load, format_stats_table


pytestmark = pytest.mark.stability

RENDERED_TILES = tiles_for("rwgps")


def build_renderer_paths() -> list[str]:
    """Build rendered-only path list targeting the renderer pool."""
    return [f"/styles/{STYLE}/{t['z']}/{t['x']}/{t['y']}.webp" for t in RENDERED_TILES]


def assert_phase_ok(name: str, result):
    """Assert no critical failures in a phase."""
    assert result.status_counts.get(503, 0) == 0, (
        f"{name}: Got {result.status_counts.get(503, 0)} 503 errors — renderer pool exhausted"
    )
    assert result.connection_errors == 0, (
        f"{name}: Got {result.connection_errors} connection errors — possible crash/segfault"
    )


class TestRendererStress:
    """3-phase ramping stress test targeting the renderer pool."""

    # Concurrency levels for each phase — add entries to extend the ramp
    PHASES = [5, 10, 20]

    @pytest.mark.timeout(0)  # Disable timeout — duration controls test length
    def test_renderer_pool_ramp(self, base_url, client, duration):
        # Warmup: single request to prime the renderer
        resp = client.get(f"/styles/{STYLE}/{RENDERED_TILES[0]['z']}/{RENDERED_TILES[0]['x']}/{RENDERED_TILES[0]['y']}.png")
        assert resp.status_code == 200, "Warmup request failed"

        paths = build_renderer_paths()
        phase_duration = max(duration // len(self.PHASES), 10)

        results = {}
        for i, phase_concurrency in enumerate(self.PHASES, 1):
            if i > 1:
                time.sleep(5)  # pause between phases to trigger idle cleanup paths
            name = f"Phase {i} c={phase_concurrency}"
            result = asyncio.run(run_load(base_url, paths, phase_duration, phase_concurrency))
            results[name] = result

        # Report
        print(f"\n{format_stats_table(results)}")

        # Assert per-phase
        for name, result in results.items():
            assert_phase_ok(name, result)

        # Aggregate check
        total_errors = sum(r.errors for r in results.values())
        total_requests = sum(r.total_requests for r in results.values())
        print(f"\nAggregate: {total_requests} requests, {total_errors} errors")

        assert total_errors == 0, f"Got {total_errors} total errors across all phases"

    @pytest.mark.timeout(60)
    def test_post_stress_health(self, assert_healthy):
        """Verify server responds to health check after stress test."""
        assert_healthy()

    @pytest.mark.timeout(60)
    def test_post_stress_no_crash(self, assert_no_crash):
        """Verify server did not crash (SIGSEGV) during stress test."""
        assert_no_crash()
