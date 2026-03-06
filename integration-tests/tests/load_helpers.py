"""Shared async load-generation utilities for stability and performance tests.

Used by test_sustained_load.py, test_renderer_stress.py,
test_dynamic_loading.py, and test_benchmark.py.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class LoadResult:
    """Aggregated results from a load test run."""

    total_requests: int = 0
    errors: int = 0
    connection_errors: int = 0
    status_counts: dict[int, int] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)


def compute_percentiles(
    latencies: list[float],
    percentiles: list[int] | None = None,
) -> dict[int, float]:
    """Compute percentile values from a list of latencies in ms."""
    if percentiles is None:
        percentiles = [50, 95, 99]
    if not latencies:
        return {p: 0.0 for p in percentiles}
    sorted_lats = sorted(latencies)
    n = len(sorted_lats)
    return {
        p: sorted_lats[min(int(n * p / 100), n - 1)]
        for p in percentiles
    }


def format_stats_table(results: dict[str, LoadResult]) -> str:
    """Format a comparison table of load test results for terminal output."""
    lines = []
    header = f"{'Phase':<20} {'Reqs':>8} {'Errs':>8} {'ConnErr':>8} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for name, r in results.items():
        pcts = compute_percentiles(r.latencies)
        lines.append(
            f"{name:<20} {r.total_requests:>8} {r.errors:>8} "
            f"{r.connection_errors:>8} {pcts[50]:>8.1f} {pcts[95]:>8.1f} {pcts[99]:>8.1f}"
        )
    return "\n".join(lines)


async def run_load(
    base_url: str,
    paths: list[str],
    duration: int,
    concurrency: int,
    timeout: int = 30,
) -> LoadResult:
    """Run concurrent async workers firing requests at random paths.

    Args:
        base_url: The base URL of the tileserver.
        paths: Pre-built list of URL paths to request randomly.
        duration: How long to run in seconds.
        concurrency: Number of concurrent workers.
        timeout: Per-request timeout in seconds.

    Returns:
        LoadResult with aggregated stats.
    """
    result = LoadResult()
    lock = asyncio.Lock()
    stop_event = asyncio.Event()

    async def worker(client: httpx.AsyncClient):
        while not stop_event.is_set():
            path = random.choice(paths)
            start = time.monotonic()
            try:
                resp = await client.get(path, timeout=timeout)
                elapsed_ms = (time.monotonic() - start) * 1000
                async with lock:
                    result.total_requests += 1
                    result.latencies.append(elapsed_ms)
                    result.status_counts[resp.status_code] = (
                        result.status_counts.get(resp.status_code, 0) + 1
                    )
                    if resp.status_code >= 500:
                        result.errors += 1
                        result.anomalies.append({
                            "timestamp": time.time(),
                            "path": path,
                            "status": resp.status_code,
                        })
            except httpx.ConnectError:
                async with lock:
                    result.total_requests += 1
                    result.connection_errors += 1
                    result.errors += 1
                    result.anomalies.append({
                        "timestamp": time.time(),
                        "path": path,
                        "status": "connect_error",
                    })
            except httpx.TimeoutException:
                async with lock:
                    result.total_requests += 1
                    result.errors += 1
                    result.anomalies.append({
                        "timestamp": time.time(),
                        "path": path,
                        "status": "timeout",
                    })

    async with httpx.AsyncClient(
        base_url=base_url,
        limits=httpx.Limits(
            max_connections=concurrency * 2,
            max_keepalive_connections=concurrency,
        ),
    ) as client:
        workers = [asyncio.create_task(worker(client)) for _ in range(concurrency)]

        await asyncio.sleep(duration)
        stop_event.set()

        # Give workers a moment to finish in-flight requests
        await asyncio.gather(*workers, return_exceptions=True)

    return result
