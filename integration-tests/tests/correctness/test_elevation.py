"""Elevation tile tests — blank DEM tile and elevation correctness (fork-critical).

The blank DEM tile is fork-specific behavior that prevents MapLibre raster-dem
parse errors. When a tile is missing from the elevation source, the server returns
a valid 256x256 PNG with uniform RGB(1, 134, 160) representing 0m in Terrain-RGB
encoding, instead of a 204 No Content.
"""

import gzip
import io

import pytest
from PIL import Image

from tests.tile_coords import (
    DATA_ELEVATION,
    DATA_SNOW,
    ELEV_NO_DATA,
    ELEV_WITH_DATA,
)


pytestmark = pytest.mark.correctness

# Terrain-RGB encoding for 0m elevation
BLANK_DEM_RGB = (1, 134, 160)


class TestBlankDEMTile:
    """Critical fork-specific test: missing elevation tiles return a valid blank DEM PNG."""

    def test_missing_elevation_returns_200(self, client):
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_NO_DATA['z']}/{ELEV_NO_DATA['x']}/{ELEV_NO_DATA['y']}.png"
        )
        assert resp.status_code == 200, f"Expected 200 for missing elevation tile, got {resp.status_code}"

    def test_missing_elevation_content_type_png(self, client):
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_NO_DATA['z']}/{ELEV_NO_DATA['x']}/{ELEV_NO_DATA['y']}.png"
        )
        assert "image/png" in resp.headers["content-type"]

    def test_blank_dem_is_256x256(self, client):
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_NO_DATA['z']}/{ELEV_NO_DATA['x']}/{ELEV_NO_DATA['y']}.png"
        )
        raw = resp.content
        # Decompress if gzipped
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        img = Image.open(io.BytesIO(raw))
        assert img.size == (256, 256)

    def test_blank_dem_uniform_zero_elevation(self, client):
        """Verify all pixels are RGB(1, 134, 160) = 0m in Terrain-RGB encoding."""
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_NO_DATA['z']}/{ELEV_NO_DATA['x']}/{ELEV_NO_DATA['y']}.png"
        )
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        colors = img.getcolors()
        assert colors == [(256 * 256, BLANK_DEM_RGB)], (
            f"Expected all pixels to be {BLANK_DEM_RGB}, got {colors}"
        )


class TestElevationWithData:
    """Verify normal elevation tiles with real terrain data."""

    def test_elevation_tile_returns_200(self, client):
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_WITH_DATA['z']}/{ELEV_WITH_DATA['x']}/{ELEV_WITH_DATA['y']}.png"
        )
        assert resp.status_code == 200

    def test_elevation_tile_is_256x256(self, client):
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_WITH_DATA['z']}/{ELEV_WITH_DATA['x']}/{ELEV_WITH_DATA['y']}.png"
        )
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        img = Image.open(io.BytesIO(raw))
        assert img.size == (256, 256)

    def test_elevation_tile_has_varying_data(self, client):
        """Real terrain (Mt Hood) should not be uniform."""
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_WITH_DATA['z']}/{ELEV_WITH_DATA['x']}/{ELEV_WITH_DATA['y']}.png"
        )
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        # Check a few pixels — real terrain should have different values
        corners = [img.getpixel(c) for c in [(0, 0), (128, 128), (255, 255)]]
        assert len(set(corners)) > 1, "Real elevation tile should have varying pixel values"


class TestElevationEdgeCases:
    """Verify error handling for out-of-range and wrong format requests."""

    def test_out_of_zoom_returns_404(self, client):
        resp = client.get(f"/data/{DATA_ELEVATION}/15/0/0.png")
        assert resp.status_code == 404

    def test_wrong_format_returns_404(self, client):
        resp = client.get(
            f"/data/{DATA_ELEVATION}/{ELEV_WITH_DATA['z']}/{ELEV_WITH_DATA['x']}/{ELEV_WITH_DATA['y']}.pbf"
        )
        assert resp.status_code == 404


class TestSnowDepthNotBlankDEM:
    """Prove the blank DEM behavior is elevation-specific, not applied to all PNG sources."""

    def test_missing_snow_tile_returns_204(self, client):
        """Snow depth is also format=png, but missing tiles should return 204, not blank DEM."""
        resp = client.get(
            f"/data/{DATA_SNOW}/{ELEV_NO_DATA['z']}/{ELEV_NO_DATA['x']}/{ELEV_NO_DATA['y']}.png"
        )
        assert resp.status_code == 204, (
            f"Expected 204 for missing snow tile, got {resp.status_code}. "
            "Blank DEM behavior should only apply to elevation sources."
        )
