"""Shared base tests for data source correctness.

Subclasses configure source-specific behavior via class attributes.
Common patterns (tile with data, content-type, edge cases) are tested here.
Source-specific tests (blank DEM, gzip handling) stay in their own files.
"""

import gzip
import io

import pytest
from PIL import Image

from tests.tile_coords import data_url


pytestmark = pytest.mark.correctness


def maybe_decompress(raw: bytes) -> bytes:
    """Decompress gzip if needed."""
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


# Content-type lookup by tile format
_DEFAULT_CT = {
    "png": "image/png",
    "pbf": "application/x-protobuf",
}


class DataSourceBase:
    """Base class for data source correctness tests.

    Subclasses MUST set:
        SOURCE: str           — data source name (e.g., "rwgps")
        FORMAT: str           — tile format (e.g., "pbf", "png")
        TILE_WITH_DATA: dict  — {z, x, y} known to have data
        WRONG_FORMAT: str     — format that should 404 (e.g., "pbf" for png sources)
        OUT_OF_ZOOM: int      — zoom level beyond maxzoom
    """

    SOURCE: str
    FORMAT: str
    TILE_WITH_DATA: dict
    WRONG_FORMAT: str
    OUT_OF_ZOOM: int

    _REQUIRED_ATTRS = ("SOURCE", "FORMAT", "TILE_WITH_DATA", "WRONG_FORMAT", "OUT_OF_ZOOM")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Skip validation for intermediate base classes (e.g., RasterDataSourceBase)
        if cls.__name__.startswith("Test"):
            missing = [a for a in cls._REQUIRED_ATTRS if not hasattr(cls, a)]
            if missing:
                raise TypeError(f"{cls.__name__} missing required attributes: {', '.join(missing)}")

    def test_tile_with_data_returns_200(self, client):
        resp = client.get(data_url(self.SOURCE, self.TILE_WITH_DATA, self.FORMAT))
        assert resp.status_code == 200

    def test_content_type(self, client):
        resp = client.get(data_url(self.SOURCE, self.TILE_WITH_DATA, self.FORMAT))
        assert resp.status_code == 200
        expected = _DEFAULT_CT[self.FORMAT]
        assert expected in resp.headers["content-type"]

    def test_beyond_maxzoom_returns_404(self, client):
        resp = client.get(f"/data/{self.SOURCE}/{self.OUT_OF_ZOOM}/0/0.{self.FORMAT}")
        assert resp.status_code == 404

    def test_wrong_format_returns_404(self, client):
        resp = client.get(data_url(self.SOURCE, self.TILE_WITH_DATA, self.WRONG_FORMAT))
        assert resp.status_code == 404


class RasterDataSourceBase(DataSourceBase):
    """Extended base for raster (PNG) sources — adds image validation.

    Subclasses MAY set:
        TILE_NO_DATA: dict  — {z, x, y} known to have no data (enables missing tile test)
    """

    TILE_NO_DATA: dict | None = None

    def test_valid_image_256x256(self, client):
        resp = client.get(data_url(self.SOURCE, self.TILE_WITH_DATA, self.FORMAT))
        raw = maybe_decompress(resp.content)
        img = Image.open(io.BytesIO(raw))
        assert img.size == (256, 256)

    def test_missing_tile_returns_no_data(self, client):
        """Missing raster tile returns either 204 or 200 with a transparent PNG."""
        if self.TILE_NO_DATA is None:
            pytest.skip("No TILE_NO_DATA coordinate defined")
        resp = client.get(data_url(self.SOURCE, self.TILE_NO_DATA, self.FORMAT))
        assert resp.status_code in (200, 204), (
            f"Expected 200 (blank PNG) or 204 (no content) for missing tile, got {resp.status_code}"
        )
        if resp.status_code == 200:
            raw = maybe_decompress(resp.content)
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
            assert img.size == (256, 256), f"Blank tile should be 256x256, got {img.size}"
