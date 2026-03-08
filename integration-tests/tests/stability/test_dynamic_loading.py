"""Dynamic mbtiles loading tests — chokidar file watcher under concurrent load.

Tests the tileserver's chokidar-based file watcher by copying, overwriting,
and deleting mbtiles files while hammering the server with HTTP requests.
The churn test uses a separate subprocess for file operations to achieve
true OS-level parallelism with the HTTP load workers.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from tests.tile_coords import DATA_RWGPS, DATA_ELEVATION, tiles_for
from tests.load_helpers import run_load


pytestmark = pytest.mark.stability

FIXTURE_FILE = "aqi_example.mbtiles"
TEST_SOURCE_NAME = "test_dynamic_source"
CHURN_WORKER = Path(__file__).parent / "churn_worker.py"
NUM_CHURN_SLOTS = 5
CHURN_DURATION = 45
POLL_INTERVAL = 1
POLL_TIMEOUT = 15


def poll_for_status(client, url, expected_status, timeout=POLL_TIMEOUT):
    """Poll a URL until the expected status code is returned."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = client.get(url)
            if resp.status_code == expected_status:
                return resp
        except httpx.ConnectError:
            pass
        time.sleep(POLL_INTERVAL)
    return None


@pytest.fixture
def dynamic_source_path(mbtiles_dir):
    """Provides the target path for the copied mbtiles and ensures cleanup."""
    if not mbtiles_dir:
        pytest.skip("--mbtiles-dir not set (no filesystem access)")
    path = Path(mbtiles_dir) / f"{TEST_SOURCE_NAME}.mbtiles"
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
def fixture_file(mbtiles_dir):
    """Resolve the aqi_example.mbtiles fixture file."""
    if not mbtiles_dir:
        pytest.skip("--mbtiles-dir not set (no filesystem access)")
    path = Path(mbtiles_dir) / FIXTURE_FILE
    if not path.exists():
        pytest.fail(
            f"Fixture file {FIXTURE_FILE} not found in {mbtiles_dir}. "
            "Create it with: cp some_aqi_file.mbtiles aqi_example.mbtiles"
        )
    return path


class TestDynamicLoading:
    """Chokidar mbtiles file watcher tests."""

    @pytest.mark.timeout(120)
    def test_dynamic_add_and_remove(self, client, fixture_file, dynamic_source_path):
        """Copy a file in, verify detection, delete it, verify removal."""

        print(f"\nCopying {fixture_file.name} → {dynamic_source_path.name}")
        shutil.copy2(fixture_file, dynamic_source_path)

        resp = poll_for_status(client, f"/data/{TEST_SOURCE_NAME}.json", 200)
        assert resp is not None, (
            f"Dynamic source '{TEST_SOURCE_NAME}' not detected within {POLL_TIMEOUT}s"
        )
        print(f"Source detected! TileJSON response: {resp.status_code}")

        tilejson = resp.json()
        assert "tiles" in tilejson

        print(f"Deleting {dynamic_source_path.name}")
        dynamic_source_path.unlink()

        resp = poll_for_status(client, f"/data/{TEST_SOURCE_NAME}.json", 404)
        assert resp is not None, (
            f"Dynamic source '{TEST_SOURCE_NAME}' not removed within {POLL_TIMEOUT}s"
        )
        print(f"Source removed! Status: {resp.status_code}")

        health = client.get("/health")
        assert health.status_code == 200

    @pytest.mark.timeout(120)
    def test_dynamic_update(self, client, fixture_file, dynamic_source_path):
        """Overwrite an existing mbtiles file and verify the source reloads."""

        # Add the source
        print(f"\nCopying {fixture_file.name} → {dynamic_source_path.name}")
        shutil.copy2(fixture_file, dynamic_source_path)

        resp = poll_for_status(client, f"/data/{TEST_SOURCE_NAME}.json", 200)
        assert resp is not None, (
            f"Dynamic source '{TEST_SOURCE_NAME}' not detected within {POLL_TIMEOUT}s"
        )
        print("Source detected, now overwriting with fresh copy...")

        # Overwrite — triggers chokidar 'change' event
        shutil.copy2(fixture_file, dynamic_source_path)

        # Give the server time to process the change event
        time.sleep(3)

        # Source should still be available (not lost on overwrite)
        resp = poll_for_status(client, f"/data/{TEST_SOURCE_NAME}.json", 200)
        assert resp is not None, (
            "Source lost after overwrite — server failed to reload"
        )
        tilejson = resp.json()
        assert "tiles" in tilejson
        print("Source survived overwrite, TileJSON still valid")

        # Clean up: delete and verify removal
        print(f"Deleting {dynamic_source_path.name}")
        dynamic_source_path.unlink()

        resp = poll_for_status(client, f"/data/{TEST_SOURCE_NAME}.json", 404)
        assert resp is not None, (
            f"Dynamic source '{TEST_SOURCE_NAME}' not removed within {POLL_TIMEOUT}s"
        )

        health = client.get("/health")
        assert health.status_code == 200

    @pytest.mark.timeout(120)
    def test_dynamic_churn_under_load(self, base_url, mbtiles_dir, fixture_file):
        """File churn subprocess + concurrent HTTP load in separate OS processes.

        The churn_worker.py subprocess rapidly adds, updates, and deletes
        mbtiles files while async HTTP workers hammer the server. This tests
        that chokidar events firing during request handling don't crash the
        server or corrupt state.
        """
        if not mbtiles_dir:
            pytest.skip("--mbtiles-dir not set (no filesystem access)")

        # Build request paths: static sources + churn slot sources
        churn_slots = [f"test_churn_{i:02d}" for i in range(1, NUM_CHURN_SLOTS + 1)]
        aqi_tiles = tiles_for("aqi_example")

        rwgps_tiles = tiles_for("rwgps")
        elevation_tiles = tiles_for("elevation")

        bg_paths = (
            # Static sources — should always return 200
            [
                f"/data/{DATA_RWGPS}/{t['z']}/{t['x']}/{t['y']}.pbf"
                for t in rwgps_tiles
            ]
            + [
                f"/data/{DATA_ELEVATION}/{t['z']}/{t['x']}/{t['y']}.png"
                for t in elevation_tiles
            ]
            # Churn slot sources — will cycle through 200/404 as files appear/disappear
            # Uses aqi_example tile coords + png format to match the fixture file
            + [
                f"/data/{slot}/{t['z']}/{t['x']}/{t['y']}.png"
                for slot in churn_slots
                for t in aqi_tiles
            ]
        )

        # Launch churn subprocess — write to temp files instead of pipes
        # to avoid blocking on a full pipe buffer while the load test runs.
        stdout_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
        stderr_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
        churn_cmd = [
            sys.executable, str(CHURN_WORKER),
            mbtiles_dir, FIXTURE_FILE,
            "--duration", str(CHURN_DURATION),
            "--num-slots", str(NUM_CHURN_SLOTS),
        ]
        proc = subprocess.Popen(
            churn_cmd,
            stdout=stdout_file,
            stderr=stderr_file,
        )

        # Give the worker a moment to start, then verify it's alive
        time.sleep(1)
        if proc.poll() is not None:
            stdout_file.seek(0)
            stderr_file.seek(0)
            pytest.fail(
                f"Churn worker died on startup (code {proc.returncode}).\n"
                f"stdout: {stdout_file.read()[:500]}\nstderr: {stderr_file.read()[:500]}"
            )

        try:
            # Run HTTP load concurrently with the churning subprocess
            result = asyncio.run(
                run_load(base_url, bg_paths, duration=CHURN_DURATION, concurrency=5)
            )
        finally:
            # Terminate if still running
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

            # Read output from temp files
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
            stdout_file.close()
            stderr_file.close()
            Path(stdout_file.name).unlink(missing_ok=True)
            Path(stderr_file.name).unlink(missing_ok=True)

        # Surface errors immediately
        if stderr.strip():
            print(f"\nChurn worker stderr: {stderr.strip()}")

        # Parse churn worker output
        op_counts = {"add": 0, "delete": 0, "update": 0}
        for line in stdout.strip().splitlines():
            try:
                entry = json.loads(line)
                if entry.get("op") == "summary":
                    op_counts = entry.get("counts", op_counts)
                    break
            except json.JSONDecodeError:
                continue

        # Report
        print(f"\nChurn worker ops: {op_counts}")
        print(f"  Total churn ops: {sum(op_counts.values())}")
        print(f"HTTP load results:")
        print(f"  Requests: {result.total_requests}")
        print(f"  Connection errors: {result.connection_errors}")
        print(f"  Status codes: {dict(sorted(result.status_counts.items()))}")

        # Assertions
        assert result.connection_errors == 0, (
            f"Got {result.connection_errors} connection errors — server may have crashed"
        )
        assert result.status_counts.get(500, 0) == 0, (
            f"Got {result.status_counts[500]} HTTP 500 responses"
        )
        assert result.status_counts.get(503, 0) == 0, (
            f"Got {result.status_counts[503]} HTTP 503 responses"
        )
        # SIGTERM (-15) is expected — we terminate the worker after load completes
        assert proc.returncode in (0, -15), (
            f"Churn worker exited with code {proc.returncode}: {stderr.strip()}"
        )

        # Verify server is still healthy
        with httpx.Client(base_url=base_url) as client:
            health = client.get("/health")
            assert health.status_code == 200, "Server health check failed after churn test"

        # Verify no test_churn files remain
        remaining = list(Path(mbtiles_dir).glob("test_churn_*.mbtiles"))
        assert len(remaining) == 0, (
            f"Churn worker left behind files: {[p.name for p in remaining]}"
        )
