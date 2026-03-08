"""DEM boundary segfault test — renders tiles at elevation data edges.

When the native renderer processes a tile near the edge of the elevation dataset,
it requests neighboring DEM tiles for interpolation.  If a neighbor doesn't exist,
the server must return a properly-sized blank terrain tile (256x256).  Returning a
1x1 pixel PNG causes a dimension mismatch that segfaults the renderer.

Boundary tiles are precomputed from elevation.mbtiles — every tile that has at
least one missing neighbor (N/S/E/W).  This captures the real irregular boundary
(coastlines, internal gaps) not just the rectangular envelope.

Regenerate with:
    uv run generate_dem_boundaries.py /path/to/elevation.mbtiles
"""

import json
from pathlib import Path

import pytest

from tests.tile_coords import STYLE, rendered_url


pytestmark = pytest.mark.stability

_TILES_DIR = Path(__file__).parent.parent
_LOCAL_TILES = _TILES_DIR / "dem_boundary_tiles.json"
_GLOBAL_TILES = _TILES_DIR / "dem_boundary_tiles_global.json"


def _resolve_tiles_path(config) -> Path:
    """Pick the right DEM boundary tiles based on context.

    Priority:
    1. Explicit --dem-tiles CLI flag (always wins)
    2. Global tiles if base URL is not localhost (production)
    3. Local CONUS tiles (default)
    """
    custom = config.getoption("--dem-tiles", default=None)
    if custom:
        return Path(custom)

    base_url = config.getoption("--base-url", default="http://localhost:8080")
    if "localhost" not in base_url and "127.0.0.1" not in base_url:
        if _GLOBAL_TILES.exists():
            return _GLOBAL_TILES

    return _LOCAL_TILES


def pytest_generate_tests(metafunc):
    """Load boundary tiles from the resolved path."""
    if "tile" in metafunc.fixturenames:
        tiles_path = _resolve_tiles_path(metafunc.config)
        tiles = json.loads(tiles_path.read_text())
        metafunc.parametrize(
            "tile",
            tiles,
            ids=[f"z{t['z']}-{t['x']}_{t['y']}" for t in tiles],
        )


def test_dem_boundary_no_segfault(client, tile):
    """Rendering at DEM data boundary must not crash the server.

    A connection error here means the renderer segfaulted — the process died
    mid-request.  A 200 means the tile rendered successfully with blank
    sea-level elevation where neighbor data was missing.
    """
    resp = client.get(rendered_url(STYLE, tile))
    assert resp.status_code == 200, (
        f"z={tile['z']}/x={tile['x']}/y={tile['y']}: expected 200, got {resp.status_code}. "
        "A connection error indicates a renderer segfault."
    )


def test_server_healthy_after_sweep(client):
    """Verify the server is still alive after the full boundary sweep.

    Runs after all parametrized boundary tiles.  If any segfault killed the
    renderer, this health check will fail with a connection error.
    """
    resp = client.get("/health")
    assert resp.status_code == 200, "Server health check failed after DEM boundary sweep"
