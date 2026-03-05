"""Sprite serving tests — sprite JSON/PNG integrity and cross-validation."""

import io

import pytest
from PIL import Image

from tests.tile_coords import STYLE


pytestmark = pytest.mark.correctness


class TestSpriteJSON:
    """Verify sprite JSON endpoint."""

    def test_sprite_json_returns_200(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite.json")
        assert resp.status_code == 200

    def test_sprite_json_is_valid_object(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite.json")
        data = resp.json()
        assert isinstance(data, dict)
        assert len(data) > 0


class TestSpritePNG:
    """Verify sprite PNG endpoint."""

    def test_sprite_png_returns_200(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite.png")
        assert resp.status_code == 200

    def test_sprite_png_magic_bytes(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite.png")
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_sprite_png_valid_dimensions(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite.png")
        img = Image.open(io.BytesIO(resp.content))
        assert img.size[0] > 0 and img.size[1] > 0


class TestSpriteRetina:
    """Verify @2x retina sprite variants."""

    def test_retina_json_returns_200(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite@2x.json")
        assert resp.status_code == 200

    def test_retina_json_has_same_keys(self, client):
        resp_1x = client.get(f"/styles/{STYLE}/sprite.json")
        resp_2x = client.get(f"/styles/{STYLE}/sprite@2x.json")
        keys_1x = set(resp_1x.json().keys())
        keys_2x = set(resp_2x.json().keys())
        assert keys_1x == keys_2x, f"1x and 2x sprite JSON have different keys"

    def test_retina_png_returns_200(self, client):
        resp = client.get(f"/styles/{STYLE}/sprite@2x.png")
        assert resp.status_code == 200

    def test_retina_png_is_2x_dimensions(self, client):
        resp_1x = client.get(f"/styles/{STYLE}/sprite.png")
        resp_2x = client.get(f"/styles/{STYLE}/sprite@2x.png")
        img_1x = Image.open(io.BytesIO(resp_1x.content))
        img_2x = Image.open(io.BytesIO(resp_2x.content))
        assert img_2x.size[0] == img_1x.size[0] * 2
        assert img_2x.size[1] == img_1x.size[1] * 2


class TestSpriteCrossValidation:
    """Verify sprite JSON entries fit within PNG bounds."""

    def test_all_sprites_within_png_bounds(self, client):
        resp_json = client.get(f"/styles/{STYLE}/sprite.json")
        resp_png = client.get(f"/styles/{STYLE}/sprite.png")
        sprites = resp_json.json()
        img = Image.open(io.BytesIO(resp_png.content))
        png_w, png_h = img.size

        for name, entry in sprites.items():
            assert "x" in entry and "y" in entry, f"Sprite '{name}' missing x/y"
            assert "width" in entry and "height" in entry, f"Sprite '{name}' missing width/height"
            assert entry["x"] + entry["width"] <= png_w, (
                f"Sprite '{name}' exceeds PNG width: {entry['x']}+{entry['width']} > {png_w}"
            )
            assert entry["y"] + entry["height"] <= png_h, (
                f"Sprite '{name}' exceeds PNG height: {entry['y']}+{entry['height']} > {png_h}"
            )
