# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate deterministic tile sample sets from mbtiles metadata.

Reads minzoom/maxzoom/format from each mbtiles file's metadata table,
then samples ~20 tiles per zoom level within CONUS bounds using a fixed
random seed. Output is a JSON file consumed by integration tests.

Usage:
    uv run scripts/generate_tile_samples.py /path/to/mbtiles/dir
"""

import json
import math
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# CONUS bounding box — guarantees tiles land on real data regardless of
# what the mbtiles metadata says (output.mbtiles has global bounds but
# data is CONUS-focused).
CONUS_BOUNDS = (-125.0, 24.0, -66.0, 50.0)

# Source definitions: mbtiles filename → source name
SOURCES = {
    "output.mbtiles": "rwgps",
    "elevation.mbtiles": "elevation",
    "aqi_example.mbtiles": "aqi_example",
}

TILES_PER_ZOOM = 20
SEED = 42
MIN_ZOOM_FLOOR = 5

# Production traffic proportions for rwgps rendered tiles.
# Based on observed traffic: z10-z13 dominant, z12 peak.
# Sums to 1.0 — scaled to total tile budget in generate_source().
RWGPS_ZOOM_WEIGHTS: dict[int, float] = {
    5: 0.02, 6: 0.02, 7: 0.03, 8: 0.03,
    9: 0.05, 10: 0.14, 11: 0.14,
    12: 0.24, 13: 0.19, 14: 0.14,
}


def lon_lat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Convert lon/lat to XYZ tile coordinates at a given zoom."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    # Clamp to valid range
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def sample_tiles_for_zoom(
    zoom: int, bounds: tuple[float, float, float, float], rng: random.Random, count: int
) -> list[dict]:
    """Sample `count` random tiles within bounds at a given zoom level."""
    west, south, east, north = bounds
    x_min, y_min = lon_lat_to_tile(west, north, zoom)  # north → smaller y
    x_max, y_max = lon_lat_to_tile(east, south, zoom)   # south → larger y

    # All possible tiles in this bounding box
    all_tiles = [
        {"z": zoom, "x": x, "y": y}
        for x in range(x_min, x_max + 1)
        for y in range(y_min, y_max + 1)
    ]

    if len(all_tiles) <= count:
        return all_tiles

    return rng.sample(all_tiles, count)


def read_metadata(db_path: Path) -> dict:
    """Read minzoom, maxzoom, format from mbtiles metadata table."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute("SELECT name, value FROM metadata")
        meta = {row[0]: row[1] for row in cur.fetchall()}

        fmt = meta.get("format", "pbf")
        minzoom = meta.get("minzoom")
        maxzoom = meta.get("maxzoom")

        # Some sources (like snow_depth) may not have zoom in metadata
        if minzoom is not None and maxzoom is not None:
            return {"format": fmt, "minzoom": int(minzoom), "maxzoom": int(maxzoom)}

        # Fall back to scanning the tiles table
        # Try tiles_shallow first (used by some mbtiles), then tiles
        for table in ("tiles_shallow", "tiles"):
            try:
                cur = conn.execute(
                    f"SELECT MIN(zoom_level), MAX(zoom_level) FROM {table}"
                )
                break
            except sqlite3.OperationalError:
                continue
        else:
            raise ValueError(f"No tiles table found in {db_path.name}")
        row = cur.fetchone()
        if row and row[0] is not None:
            return {"format": fmt, "minzoom": int(row[0]), "maxzoom": int(row[1])}

        raise ValueError(f"Cannot determine zoom range for {db_path.name}")
    finally:
        conn.close()


def generate_source(
    db_path: Path, rng: random.Random, zoom_weights: dict[int, float] | None = None
) -> dict:
    """Generate tile samples for a single mbtiles source."""
    meta = read_metadata(db_path)
    # Use zoom floor unless source maxzoom is below it (e.g. aqi_example z1-z5)
    effective_min = max(meta["minzoom"], min(MIN_ZOOM_FLOOR, meta["maxzoom"]))

    num_zooms = meta["maxzoom"] - effective_min + 1
    total_budget = TILES_PER_ZOOM * num_zooms

    tiles = []
    for z in range(effective_min, meta["maxzoom"] + 1):
        if zoom_weights and z in zoom_weights:
            count = max(1, round(zoom_weights[z] * total_budget))
        else:
            count = TILES_PER_ZOOM
        tiles.extend(sample_tiles_for_zoom(z, CONUS_BOUNDS, rng, count))

    # Sort for stable output
    tiles.sort(key=lambda t: (t["z"], t["x"], t["y"]))

    return {
        "format": meta["format"],
        "minzoom": effective_min,
        "maxzoom": meta["maxzoom"],
        "tiles": tiles,
    }


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <mbtiles_dir>", file=sys.stderr)
        sys.exit(1)

    mbtiles_dir = Path(sys.argv[1])
    if not mbtiles_dir.is_dir():
        print(f"Error: {mbtiles_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    rng = random.Random(SEED)
    sources = {}

    for filename, source_name in SOURCES.items():
        db_path = mbtiles_dir / filename
        if not db_path.exists():
            print(f"Warning: {db_path} not found, skipping {source_name}", file=sys.stderr)
            continue

        weights = RWGPS_ZOOM_WEIGHTS if source_name == "rwgps" else None
        sources[source_name] = generate_source(db_path, rng, weights)
        tile_count = len(sources[source_name]["tiles"])
        zoom_range = f"z{sources[source_name]['minzoom']}-z{sources[source_name]['maxzoom']}"
        print(f"{source_name}: {tile_count} tiles ({zoom_range}, {sources[source_name]['format']})")

    output = {
        "seed": SEED,
        "generated": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }

    out_path = Path(__file__).resolve().parent / "tests" / "tile_samples.json"
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
