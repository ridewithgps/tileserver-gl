"""Shared fixtures for stability tests."""

import os
import subprocess
import time

import httpx
import pytest


# Match production K8s liveness probe settings
HEALTH_TIMEOUT = 10  # seconds per attempt
HEALTH_FAILURES = 2  # consecutive failures before considered dead
HEALTH_PERIOD = 5    # seconds between attempts


def is_local_env() -> bool:
    """Check if running against a local docker compose environment."""
    return os.environ.get("TEST_ENV", "local") == "local"


@pytest.fixture
def assert_healthy(base_url):
    """Assert the server responds to /health within production liveness probe limits.

    Mimics K8s liveness behavior: 10s timeout, 2 consecutive failures = dead.
    Includes a brief drain period to let the renderer queue settle after load.
    """

    def _check(drain_seconds: int = 5):
        time.sleep(drain_seconds)

        failures = 0
        for attempt in range(HEALTH_FAILURES):
            try:
                with httpx.Client(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
                    resp = c.get("/health")
                if resp.status_code == 200:
                    return  # healthy
                failures += 1
            except (httpx.TimeoutException, httpx.ConnectError):
                failures += 1

            if failures < HEALTH_FAILURES:
                time.sleep(HEALTH_PERIOD)

        pytest.fail(
            f"Health check failed {HEALTH_FAILURES} consecutive times "
            f"(timeout={HEALTH_TIMEOUT}s) — in production K8s would restart the pod"
        )

    return _check


@pytest.fixture
def assert_no_crash():
    """Assert the tileserver container is still running and hasn't crashed.

    Checks:
    1. Container still exists and is running
    2. Exit code — 139 (SIGSEGV), 137 (SIGKILL/OOM), or other

    Only runs in local (docker compose) environments.
    """

    def _check():
        if not is_local_env():
            return

        # Find container ID (ps -q = running only, ps -aq = all)
        container_id = None
        for flag in ["-q", "-aq"]:
            result = subprocess.run(
                ["docker", "compose", "ps", flag, "tileserver"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            container_id = result.stdout.strip()
            if container_id:
                break

        if not container_id:
            pytest.fail("No tileserver container found")

        # Inspect container state
        result = subprocess.run(
            [
                "docker", "inspect", container_id,
                "--format", "{{.State.Status}} {{.State.ExitCode}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        parts = result.stdout.strip().split()
        if len(parts) != 2:
            pytest.fail(f"Could not inspect container: {result.stderr.strip()}")

        state, exit_code = parts[0], int(parts[1])

        if state == "running":
            return  # healthy

        if exit_code == 139:
            pytest.fail("Container crashed with SIGSEGV (exit code 139)")
        elif exit_code == 137:
            pytest.fail("Container killed by SIGKILL/OOM (exit code 137)")
        else:
            pytest.fail(
                f"Container exited unexpectedly (state={state}, exit_code={exit_code})"
            )

    return _check
