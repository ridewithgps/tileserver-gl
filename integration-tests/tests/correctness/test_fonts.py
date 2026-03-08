"""Font serving tests — font list and glyph PBF serving."""

import pytest


pytestmark = pytest.mark.correctness


class TestFontList:
    """Verify /fonts.json endpoint."""

    def test_fonts_json_returns_200(self, client):
        resp = client.get("/fonts.json")
        assert resp.status_code == 200

    def test_fonts_json_is_valid_array(self, client):
        resp = client.get("/fonts.json")
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_fonts_json_contains_known_font(self, client):
        resp = client.get("/fonts.json")
        data = resp.json()
        assert "Noto Sans Regular" in data


class TestGlyphFetch:
    """Verify glyph PBF serving for various fonts and ranges."""

    def test_noto_sans_regular_glyphs(self, client):
        resp = client.get("/fonts/Noto%20Sans%20Regular/0-255.pbf")
        assert resp.status_code == 200
        assert "application/x-protobuf" in resp.headers["content-type"]
        assert len(resp.content) > 0

    def test_open_sans_bold_glyphs(self, client):
        resp = client.get("/fonts/Open%20Sans%20Bold/0-255.pbf")
        assert resp.status_code == 200
        assert len(resp.content) > 0

    def test_non_ascii_glyph_range(self, client):
        resp = client.get("/fonts/Noto%20Sans%20Regular/256-511.pbf")
        assert resp.status_code == 200
        assert len(resp.content) > 0

    def test_unknown_font_still_returns_200(self, client):
        """Server falls back gracefully for unknown fonts (returns 200 with fallback glyphs)."""
        resp = client.get("/fonts/Nonexistent%20Font/0-255.pbf")
        assert resp.status_code == 200


class TestFontCaching:
    """Verify caching headers on font responses."""

    def test_last_modified_header_present(self, client):
        resp = client.get("/fonts/Noto%20Sans%20Regular/0-255.pbf")
        assert resp.status_code == 200
        assert "last-modified" in resp.headers
