"""Data source correctness tests — vector, elevation, and snow depth.

Each data source subclasses DataSourceBase/RasterDataSourceBase for common checks
(200 on known tile, content-type, wrong format 404, beyond maxzoom 404).
Source-specific tests are added as methods on each class.
"""

import gzip
import io

import pytest
from PIL import Image

from tests.tile_coords import (
    DATA_RWGPS, DATA_ELEVATION, DATA_SNOW,
    VECTOR_PORTLAND, VECTOR_NO_DATA, OUT_OF_RANGE,
    ELEV_WITH_DATA, ELEV_NO_DATA,
    SNOW_WITH_DATA, SNOW_NO_DATA,
    data_url,
)
from tests.correctness.test_data_base import DataSourceBase, RasterDataSourceBase, maybe_decompress


pytestmark = pytest.mark.correctness

# Terrain-RGB encoding for 0m elevation
BLANK_DEM_RGB = (1, 134, 160)


# --- Vector (PBF) -----------------------------------------------------------

class TestVectorTiles(DataSourceBase):
    """Vector tile tests — inherits common checks, adds PBF-specific validation."""

    SOURCE = DATA_RWGPS
    FORMAT = "pbf"
    TILE_WITH_DATA = VECTOR_PORTLAND
    WRONG_FORMAT = "png"
    OUT_OF_ZOOM = 15

    @pytest.mark.xfail(reason="Global dataset may have data at every coordinate")
    def test_tile_without_data_returns_204(self, client):
        resp = client.get(data_url(self.SOURCE, VECTOR_NO_DATA, self.FORMAT))
        assert resp.status_code == 204

    def test_out_of_range_returns_404(self, client):
        resp = client.get(data_url(self.SOURCE, OUT_OF_RANGE, self.FORMAT))
        assert resp.status_code == 404

    def test_unknown_source_returns_404(self, client):
        resp = client.get(data_url("nonexistent", VECTOR_PORTLAND, self.FORMAT))
        assert resp.status_code == 404

    def test_200_response_is_gzipped_protobuf(self, client):
        resp = client.get(data_url(self.SOURCE, VECTOR_PORTLAND, self.FORMAT))
        assert resp.status_code == 200
        raw = resp.content
        if resp.headers.get("content-encoding") == "gzip":
            assert len(raw) > 0
        else:
            assert raw[:2] == b"\x1f\x8b", "Expected gzip magic bytes"

    def test_200_decompressed_body_non_empty(self, client):
        resp = client.get(data_url(self.SOURCE, VECTOR_PORTLAND, self.FORMAT))
        assert resp.status_code == 200
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            decompressed = gzip.decompress(raw)
        else:
            decompressed = raw
        assert len(decompressed) > 0, "Decompressed vector tile body is empty"

    @pytest.mark.xfail(reason="Global dataset may have data at every coordinate")
    def test_204_response_has_empty_body(self, client):
        resp = client.get(data_url(self.SOURCE, VECTOR_NO_DATA, self.FORMAT))
        assert resp.status_code == 204
        assert len(resp.content) == 0


# --- Elevation (PNG, raster-dem) ---------------------------------------------

class TestElevationTiles(RasterDataSourceBase):
    """Elevation tile tests — inherits common checks, adds DEM-specific validation."""

    SOURCE = DATA_ELEVATION
    FORMAT = "png"
    TILE_WITH_DATA = ELEV_WITH_DATA
    TILE_NO_DATA = ELEV_NO_DATA
    WRONG_FORMAT = "pbf"
    OUT_OF_ZOOM = 15

    def test_elevation_tile_has_varying_data(self, client):
        """Real terrain (Mt Hood) should not be uniform."""
        resp = client.get(data_url(self.SOURCE, ELEV_WITH_DATA, self.FORMAT))
        raw = maybe_decompress(resp.content)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        corners = [img.getpixel(c) for c in [(0, 0), (128, 128), (255, 255)]]
        assert len(set(corners)) > 1, "Real elevation tile should have varying pixel values"


class TestBlankDEMTile:
    """Critical fork-specific test: missing elevation tiles return a valid blank DEM PNG."""

    def test_missing_elevation_returns_200(self, client):
        resp = client.get(data_url(DATA_ELEVATION, ELEV_NO_DATA, "png"))
        assert resp.status_code == 200, f"Expected 200 for missing elevation tile, got {resp.status_code}"

    def test_missing_elevation_content_type_png(self, client):
        resp = client.get(data_url(DATA_ELEVATION, ELEV_NO_DATA, "png"))
        assert "image/png" in resp.headers["content-type"]

    def test_blank_dem_is_256x256(self, client):
        resp = client.get(data_url(DATA_ELEVATION, ELEV_NO_DATA, "png"))
        raw = maybe_decompress(resp.content)
        img = Image.open(io.BytesIO(raw))
        assert img.size == (256, 256)

    def test_blank_dem_uniform_zero_elevation(self, client):
        """Verify all pixels are RGB(1, 134, 160) = 0m in Terrain-RGB encoding."""
        resp = client.get(data_url(DATA_ELEVATION, ELEV_NO_DATA, "png"))
        raw = maybe_decompress(resp.content)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        colors = img.getcolors()
        assert colors == [(256 * 256, BLANK_DEM_RGB)], (
            f"Expected all pixels to be {BLANK_DEM_RGB}, got {colors}"
        )


# --- Snow Depth (PNG, regular raster) ----------------------------------------

class TestSnowDepthTiles(RasterDataSourceBase):
    """Snow depth tests — inherits common checks."""

    SOURCE = DATA_SNOW
    FORMAT = "png"
    TILE_WITH_DATA = SNOW_WITH_DATA
    TILE_NO_DATA = SNOW_NO_DATA
    WRONG_FORMAT = "pbf"
    OUT_OF_ZOOM = 11


