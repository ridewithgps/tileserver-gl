"""Visual regression tests — pixel-diff comparison against reference images."""

import io
from pathlib import Path

import pytest
from PIL import Image

from tests.tile_coords import STYLE, URBAN, HILLSHADE, FLAT


pytestmark = pytest.mark.correctness

REFERENCE_DIR = Path(__file__).parent.parent.parent / "reference"

# Channel difference threshold (out of 255)
CHANNEL_THRESHOLD = 10
# Maximum percentage of differing pixels allowed
MAX_DIFF_PERCENT = 0.05

VISUAL_TILES = [
    pytest.param(URBAN, "rendered_urban_z10.png", id="urban-portland"),
    pytest.param(HILLSHADE, "rendered_hillshade_z10.png", id="hillshade-mthood"),
    pytest.param(FLAT, "rendered_flat_z10.png", id="flat-kansas"),
]


def pixel_diff_percent(actual_bytes: bytes, reference_path: Path) -> float:
    """Compute fraction of pixels that differ beyond threshold."""
    actual = Image.open(io.BytesIO(actual_bytes)).convert("RGBA")
    reference = Image.open(reference_path).convert("RGBA")
    assert actual.size == reference.size, f"Size mismatch: {actual.size} vs {reference.size}"
    actual_px = actual.load()
    ref_px = reference.load()
    total = actual.size[0] * actual.size[1]
    diff_count = 0
    for y in range(actual.size[1]):
        for x in range(actual.size[0]):
            if max(abs(a - r) for a, r in zip(actual_px[x, y], ref_px[x, y])) > CHANNEL_THRESHOLD:
                diff_count += 1
    return diff_count / total


class TestVisualRegression:
    """Compare rendered tiles against reference images."""

    @pytest.mark.parametrize("coords,ref_name", VISUAL_TILES)
    def test_tile_matches_reference(self, client, update_references, coords, ref_name):
        resp = client.get(f"/styles/{STYLE}/{coords['z']}/{coords['x']}/{coords['y']}.png")
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
