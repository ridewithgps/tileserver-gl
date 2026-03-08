"""Performance test fixtures and terminal summary."""

import pytest


@pytest.fixture(scope="session")
def perf_results():
    """Session-scoped collector for benchmark results."""
    return []


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print formatted performance comparison table."""
    # Access perf_results through the session fixture
    results = getattr(config, "_perf_results", None)
    if not results:
        return

    terminalreporter.section("Performance Summary")
    header = f"{'Style':<20} {'Format':<10} {'Tiles/s':>8} {'P50 ms':>8} {'P95 ms':>8} {'P99 ms':>8}"
    terminalreporter.write_line(header)
    terminalreporter.write_line("-" * len(header))
    for r in results:
        terminalreporter.write_line(
            f"{r.get('style', ''):<20} {r['format']:<10} {r['tiles_per_sec']:>8.1f} "
            f"{r['p50']:>8.0f} {r['p95']:>8.0f} {r['p99']:>8.0f}"
        )


@pytest.fixture(autouse=True, scope="session")
def _register_perf_results(request, perf_results):
    """Make perf_results accessible from terminal summary hook."""
    request.config._perf_results = perf_results
