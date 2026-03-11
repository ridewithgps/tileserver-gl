"""Visual regression tests — pixel-diff comparison against reference images."""

import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from tests.tile_coords import STYLE, STYLE_FLAT, URBAN, HILLSHADE, FLAT, rendered_url


pytestmark = pytest.mark.correctness

REFERENCE_DIR = Path(__file__).parent.parent.parent / "reference"

# Channel difference threshold (out of 255)
CHANNEL_THRESHOLD = 10
# Maximum percentage of differing pixels allowed
MAX_DIFF_PERCENT = 0.05

VISUAL_TILES = [
    # Base style (with hillshade/DEM)
    pytest.param(STYLE, URBAN, "rendered_urban_z10.png", id="urban-portland"),
    pytest.param(STYLE, HILLSHADE, "rendered_hillshade_z10.png", id="hillshade-mthood"),
    pytest.param(STYLE, FLAT, "rendered_flat_z10.png", id="flat-kansas"),
    # Flat style (no hillshade/DEM)
    pytest.param(STYLE_FLAT, URBAN, "rendered_flat_style_urban_z10.png", id="flat-style-urban"),
    pytest.param(STYLE_FLAT, HILLSHADE, "rendered_flat_style_hillshade_z10.png", id="flat-style-mthood"),
    pytest.param(STYLE_FLAT, FLAT, "rendered_flat_style_flat_z10.png", id="flat-style-kansas"),
]


def pixel_diff_percent(actual_bytes: bytes, reference_path: Path) -> float:
    """Compute fraction of pixels that differ beyond threshold."""
    actual = Image.open(io.BytesIO(actual_bytes)).convert("RGBA")
    reference = Image.open(reference_path).convert("RGBA")
    assert actual.size == reference.size, f"Size mismatch: {actual.size} vs {reference.size}"
    diff = ImageChops.difference(actual, reference)
    # Count pixels where any channel exceeds threshold
    total = actual.size[0] * actual.size[1]
    diff_count = sum(1 for px in diff.get_flattened_data() if max(px) > CHANNEL_THRESHOLD)
    return diff_count / total


class TestVisualRegression:
    """Compare rendered tiles against reference images."""

    @pytest.mark.parametrize("style,coords,ref_name", VISUAL_TILES)
    def test_tile_matches_reference(self, client, update_references, style, coords, ref_name):
        resp = client.get(rendered_url(style, coords))
        assert resp.status_code == 200

        ref_path = REFERENCE_DIR / ref_name

        if update_references:
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_bytes(resp.content)
            pytest.skip(f"Updated reference: {ref_path}")

        if not ref_path.exists():
            pytest.fail(
                f"Reference image not found: {ref_path}. "
                "Run with --update-references to generate."
            )

        diff = pixel_diff_percent(resp.content, ref_path)
        assert diff < MAX_DIFF_PERCENT, (
            f"Visual diff {diff:.1%} exceeds {MAX_DIFF_PERCENT:.0%} threshold for {ref_name}"
        )
