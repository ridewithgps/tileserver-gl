import pytest
import httpx


def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        default="http://localhost:8080",
        help="Base URL of the tileserver-gl instance",
    )
    parser.addoption(
        "--concurrency",
        type=int,
        default=20,
        help="Number of concurrent workers for load tests",
    )
    parser.addoption(
        "--duration",
        type=int,
        default=60,
        help="Duration in seconds for sustained load tests",
    )
    parser.addoption(
        "--mbtiles-dir",
        default=None,
        help="Path to mbtiles directory for dynamic loading tests",
    )
    parser.addoption(
        "--update-references",
        action="store_true",
        default=False,
        help="Update reference images instead of comparing",
    )


@pytest.fixture(scope="session")
def base_url(request):
    return request.config.getoption("--base-url")


@pytest.fixture(scope="session")
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=30) as c:
        # Health check gate — wait for server to be ready
        for attempt in range(30):
            try:
                resp = c.get("/health")
                if resp.status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            import time
            time.sleep(1)
        else:
            pytest.fail(f"Tileserver at {base_url} did not become healthy within 30s")
        yield c


@pytest.fixture(scope="session")
def concurrency(request):
    return request.config.getoption("--concurrency")


@pytest.fixture(scope="session")
def duration(request):
    return request.config.getoption("--duration")


@pytest.fixture(scope="session")
def mbtiles_dir(request):
    return request.config.getoption("--mbtiles-dir")


@pytest.fixture(scope="session")
def update_references(request):
    return request.config.getoption("--update-references")
