# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate DEM boundary tile coordinates from an mbtiles file.

Finds every tile that has at least one missing neighbor (N/S/E/W) — these
are the tiles most likely to trigger renderer segfaults when the server
fails to return a properly-sized blank DEM tile for the missing neighbor.

Works with any mbtiles file (test or production). The tiles table name and
zoom levels are configurable.

Usage:
    uv run generate_dem_boundaries.py /path/to/elevation.mbtiles
    uv run generate_dem_boundaries.py /path/to/elevation.mbtiles --zooms 6,8,10,12
    uv run generate_dem_boundaries.py /path/to/elevation.mbtiles --table tiles --output out.json
"""

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

SEED = 42


def detect_tiles_table(conn: sqlite3.Connection) -> str:
    """Find the tiles table — prefer tiles_shallow, fall back to tiles."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tiles_shallow', 'tiles')"
    )
    tables = {row[0] for row in cur.fetchall()}
    if "tiles_shallow" in tables:
        return "tiles_shallow"
    if "tiles" in tables:
        return "tiles"
    raise ValueError("No tiles or tiles_shallow table found in mbtiles file")


def detect_zoom_levels(conn: sqlite3.Connection, table: str) -> list[int]:
    """Get all distinct zoom levels present in the tiles table."""
    cur = conn.execute(f"SELECT DISTINCT zoom_level FROM {table} ORDER BY zoom_level")
    return [row[0] for row in cur.fetchall()]


def find_boundary_tiles(
    conn: sqlite3.Connection, table: str, zooms: list[int]
) -> list[dict]:
    """Find tiles with at least one missing N/S/E/W neighbor.

    Uses TMS tile_row internally (as stored in mbtiles), converts to XYZ y
    in the output for direct use in URL paths.
    """
    placeholders = ",".join(str(z) for z in zooms)

    query = f"""
        SELECT t.zoom_level, t.tile_column,
               (1 << t.zoom_level) - 1 - t.tile_row AS y_xyz
        FROM {table} t
        WHERE t.zoom_level IN ({placeholders})
          AND (
            NOT EXISTS (
              SELECT 1 FROM {table} n
              WHERE n.zoom_level = t.zoom_level
                AND n.tile_column = t.tile_column - 1
                AND n.tile_row = t.tile_row
            )
            OR NOT EXISTS (
              SELECT 1 FROM {table} n
              WHERE n.zoom_level = t.zoom_level
                AND n.tile_column = t.tile_column + 1
                AND n.tile_row = t.tile_row
            )
            OR NOT EXISTS (
              SELECT 1 FROM {table} n
              WHERE n.zoom_level = t.zoom_level
                AND n.tile_column = t.tile_column
                AND n.tile_row = t.tile_row - 1
            )
            OR NOT EXISTS (
              SELECT 1 FROM {table} n
              WHERE n.zoom_level = t.zoom_level
                AND n.tile_column = t.tile_column
                AND n.tile_row = t.tile_row + 1
            )
          )
        ORDER BY t.zoom_level, t.tile_column, t.tile_row
    """

    cur = conn.execute(query)
    return [{"z": row[0], "x": row[1], "y": row[2]} for row in cur.fetchall()]


def downsample(tiles: list[dict], max_tiles: int) -> list[dict]:
    """Proportionally downsample tiles, preserving the ratio per zoom level.

    Each zoom keeps at least 1 tile. Remaining budget is distributed
    proportionally to original counts. Deterministic via fixed seed.
    """
    rng = random.Random(SEED)

    # Group by zoom
    by_zoom: dict[int, list[dict]] = {}
    for t in tiles:
        by_zoom.setdefault(t["z"], []).append(t)

    # Allocate budget proportionally — guarantee at least 1 per zoom
    zooms = sorted(by_zoom.keys())
    remaining = max_tiles - len(zooms)  # reserve 1 per zoom
    total = len(tiles)
    result = []

    for z in zooms:
        group = by_zoom[z]
        # Proportional share of remaining budget + the 1 guaranteed
        share = max(1, round(len(group) / total * remaining) + 1)
        share = min(share, len(group))  # can't take more than exist
        if share < len(group):
            result.extend(rng.sample(group, share))
        else:
            result.extend(group)

    result.sort(key=lambda t: (t["z"], t["x"], t["y"]))
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Generate DEM boundary tile coordinates from mbtiles"
    )
    parser.add_argument("mbtiles", type=Path, help="Path to the mbtiles file")
    parser.add_argument(
        "--zooms",
        default=None,
        help="Comma-separated zoom levels (default: auto-detect from tiles table)",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Tiles table name (default: auto-detect tiles_shallow or tiles)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output JSON path (default: integration-tests/tests/dem_boundary_tiles.json)",
    )
    parser.add_argument(
        "--max-tiles",
        type=int,
        default=10000,
        help="Downsample to at most N tiles (proportional per zoom, deterministic seed=42; 0 to disable)",
    )
    args = parser.parse_args()

    if not args.mbtiles.exists():
        print(f"Error: {args.mbtiles} not found", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(f"file:{args.mbtiles}?mode=ro", uri=True)
    try:
        table = args.table or detect_tiles_table(conn)
        print(f"Using table: {table}")

        if args.zooms:
            zooms = [int(z.strip()) for z in args.zooms.split(",")]
        else:
            zooms = detect_zoom_levels(conn, table)
        print(f"Zoom levels: {zooms}")

        tiles = find_boundary_tiles(conn, table, zooms)
        print(f"Found {len(tiles)} boundary tiles")

        for z in zooms:
            count = sum(1 for t in tiles if t["z"] == z)
            print(f"  z{z}: {count} tiles")
    finally:
        conn.close()

    if args.max_tiles > 0 and len(tiles) > args.max_tiles:
        tiles = downsample(tiles, args.max_tiles)
        print(f"\nDownsampled to {len(tiles)} tiles (proportional per zoom, seed={SEED})")
        for z in zooms:
            count = sum(1 for t in tiles if t["z"] == z)
            if count:
                print(f"  z{z}: {count} tiles")

    output_path = args.output or (
        Path(__file__).resolve().parent / "tests" / "dem_boundary_tiles.json"
    )
    output_path.write_text(json.dumps(tiles, indent=2) + "\n")
    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    main()
