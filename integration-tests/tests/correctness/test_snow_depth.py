"""Snow depth raster tile tests — standard PNG raster behavior (not elevation).

Snow depth is a regular PNG raster source. Unlike elevation, missing tiles
should return 204 No Content (not a blank DEM). Tiles outside the tileset
bounds should return 404.
"""

import gzip
import io

import pytest
from PIL import Image

from tests.tile_coords import DATA_SNOW, SNOW_WITH_DATA, SNOW_NO_DATA


pytestmark = pytest.mark.correctness


class TestSnowDepthWithData:
    """Verify tiles with actual snow depth data."""

    def test_returns_200(self, client):
        resp = client.get(
            f"/data/{DATA_SNOW}/{SNOW_WITH_DATA['z']}/{SNOW_WITH_DATA['x']}/{SNOW_WITH_DATA['y']}.png"
        )
        assert resp.status_code == 200

    def test_content_type_png(self, client):
        resp = client.get(
            f"/data/{DATA_SNOW}/{SNOW_WITH_DATA['z']}/{SNOW_WITH_DATA['x']}/{SNOW_WITH_DATA['y']}.png"
        )
        assert "image/png" in resp.headers["content-type"]

    def test_valid_png_image(self, client):
        resp = client.get(
            f"/data/{DATA_SNOW}/{SNOW_WITH_DATA['z']}/{SNOW_WITH_DATA['x']}/{SNOW_WITH_DATA['y']}.png"
        )
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        img = Image.open(io.BytesIO(raw))
        assert img.size == (256, 256)


class TestSnowDepthNoData:
    """Verify missing tiles return 204 No Content."""

    def test_missing_tile_returns_204(self, client):
        resp = client.get(
            f"/data/{DATA_SNOW}/{SNOW_NO_DATA['z']}/{SNOW_NO_DATA['x']}/{SNOW_NO_DATA['y']}.png"
        )
        assert resp.status_code == 204, (
            f"Expected 204 for missing snow tile, got {resp.status_code}"
        )


class TestSnowDepthEdgeCases:
    """Verify error handling for out-of-range requests."""

    def test_beyond_maxzoom_returns_404(self, client):
        resp = client.get(f"/data/{DATA_SNOW}/11/0/0.png")
        assert resp.status_code == 404

    def test_wrong_format_returns_404(self, client):
        resp = client.get(
            f"/data/{DATA_SNOW}/{SNOW_WITH_DATA['z']}/{SNOW_WITH_DATA['x']}/{SNOW_WITH_DATA['y']}.pbf"
        )
        assert resp.status_code == 404
