"""Rendered tile serving tests — status codes, content-types, and format validation."""

import io

import pytest
from PIL import Image

from tests.tile_coords import STYLE, URBAN, OUT_OF_RANGE


pytestmark = pytest.mark.correctness


class TestRenderedStatusCodes:
    """Verify HTTP status codes for various rendered tile requests."""

    def test_valid_tile_returns_200(self, client):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.png")
        assert resp.status_code == 200

    def test_negative_zoom_returns_400(self, client):
        resp = client.get(f"/styles/{STYLE}/-1/0/0.png")
        assert resp.status_code == 400

    def test_out_of_range_returns_400(self, client):
        resp = client.get(
            f"/styles/{STYLE}/{OUT_OF_RANGE['z']}/{OUT_OF_RANGE['x']}/{OUT_OF_RANGE['y']}.png"
        )
        assert resp.status_code == 400

    def test_unknown_style_returns_404(self, client):
        resp = client.get(f"/styles/nonexistent/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.png")
        assert resp.status_code == 404


class TestRenderedContentTypes:
    """Verify Content-Type headers for each supported format."""

    @pytest.mark.parametrize("ext,expected_ct", [
        ("png", "image/png"),
        ("webp", "image/webp"),
        ("jpg", "image/jpeg"),
    ])
    def test_content_type(self, client, ext, expected_ct):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.{ext}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(expected_ct)


class TestRenderedFormatValidation:
    """Verify response bodies contain valid image data."""

    def test_png_magic_bytes(self, client):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.png")
        assert resp.status_code == 200
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_webp_magic_bytes(self, client):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.webp")
        assert resp.status_code == 200
        assert resp.content[:4] == b"RIFF"
        assert resp.content[8:12] == b"WEBP"

    def test_response_body_non_empty(self, client):
        for ext in ("png", "webp", "jpg"):
            resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.{ext}")
            assert resp.status_code == 200
            assert len(resp.content) > 0, f"Empty body for .{ext}"


class TestRenderedScaleFactors:
    """Verify tile dimensions at different scale factors."""

    def test_default_tile_256x256(self, client):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}.png")
        img = Image.open(io.BytesIO(resp.content))
        assert img.size == (256, 256)

    def test_2x_tile_512x512(self, client):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}@2x.png")
        assert resp.status_code == 200
        img = Image.open(io.BytesIO(resp.content))
        assert img.size == (512, 512)

    def test_2x_content_type_unchanged(self, client):
        resp = client.get(f"/styles/{STYLE}/{URBAN['z']}/{URBAN['x']}/{URBAN['y']}@2x.png")
        assert resp.headers["content-type"].startswith("image/png")
