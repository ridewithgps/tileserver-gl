"""Vector tile serving tests — PBF status codes, content-type, and body validation."""

import gzip

import pytest

from tests.tile_coords import DATA_RWGPS, VECTOR_PORTLAND, VECTOR_NO_DATA, OUT_OF_RANGE


pytestmark = pytest.mark.correctness


class TestVectorStatusCodes:
    """Verify HTTP status codes for vector tile requests."""

    def test_tile_with_data_returns_200(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{VECTOR_PORTLAND['z']}/{VECTOR_PORTLAND['x']}/{VECTOR_PORTLAND['y']}.pbf"
        )
        assert resp.status_code == 200

    def test_tile_without_data_returns_204(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{VECTOR_NO_DATA['z']}/{VECTOR_NO_DATA['x']}/{VECTOR_NO_DATA['y']}.pbf"
        )
        assert resp.status_code == 204

    def test_outside_zoom_range_returns_404(self, client):
        resp = client.get(f"/data/{DATA_RWGPS}/15/0/0.pbf")
        assert resp.status_code == 404

    def test_out_of_range_returns_404(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{OUT_OF_RANGE['z']}/{OUT_OF_RANGE['x']}/{OUT_OF_RANGE['y']}.pbf"
        )
        assert resp.status_code == 404

    def test_unknown_source_returns_404(self, client):
        resp = client.get(
            f"/data/nonexistent/{VECTOR_PORTLAND['z']}/{VECTOR_PORTLAND['x']}/{VECTOR_PORTLAND['y']}.pbf"
        )
        assert resp.status_code == 404


class TestVectorContentType:
    """Verify Content-Type for PBF responses."""

    def test_pbf_content_type(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{VECTOR_PORTLAND['z']}/{VECTOR_PORTLAND['x']}/{VECTOR_PORTLAND['y']}.pbf"
        )
        assert resp.status_code == 200
        assert "application/x-protobuf" in resp.headers["content-type"]


class TestVectorBodyValidation:
    """Verify response bodies for vector tiles."""

    def test_200_response_is_gzipped_protobuf(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{VECTOR_PORTLAND['z']}/{VECTOR_PORTLAND['x']}/{VECTOR_PORTLAND['y']}.pbf"
        )
        assert resp.status_code == 200
        # Body should be gzip-compressed (either via Content-Encoding or raw)
        raw = resp.content
        if resp.headers.get("content-encoding") == "gzip":
            # httpx may auto-decompress, but raw content should be non-empty
            assert len(raw) > 0
        else:
            # Raw body starts with gzip magic bytes
            assert raw[:2] == b"\x1f\x8b", "Expected gzip magic bytes"

    def test_200_decompressed_body_non_empty(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{VECTOR_PORTLAND['z']}/{VECTOR_PORTLAND['x']}/{VECTOR_PORTLAND['y']}.pbf"
        )
        assert resp.status_code == 200
        raw = resp.content
        # Try to decompress if gzipped
        if raw[:2] == b"\x1f\x8b":
            decompressed = gzip.decompress(raw)
        else:
            decompressed = raw
        assert len(decompressed) > 0, "Decompressed vector tile body is empty"

    def test_204_response_has_empty_body(self, client):
        resp = client.get(
            f"/data/{DATA_RWGPS}/{VECTOR_NO_DATA['z']}/{VECTOR_NO_DATA['x']}/{VECTOR_NO_DATA['y']}.pbf"
        )
        assert resp.status_code == 204
        assert len(resp.content) == 0
