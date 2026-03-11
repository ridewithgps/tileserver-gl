# Integration Tests

End-to-end test suite for the tileserver-gl fork covering correctness, stability, and performance. Tests are pure HTTP — they work against any running tileserver instance (local Docker, remote staging, production).

## Setup

```bash
cd integration-tests
just sync                     # install test dependencies
cp .env.example .env          # configure for your environment (see below)
```

### Environment Configuration

All configuration lives in `.env`:

```bash
# Required: URL of the tileserver to test against
TILESERVER_URL=http://localhost:8080

# Local only: path to tileserver data directory
# Enables Docker Compose and dynamic loading tests.
# Omit for remote/production — those tests skip cleanly.
TEST_DATA_DIR=/path/to/tile-server

# Local only: port for Docker Compose port mapping
TILESERVER_PORT=8080
```

## Running Locally (Docker)

```bash
# Start tileserver in Docker
just up

# Run tests
just correctness               # ~3s — fast sanity check
just stability                 # DEM boundary + dynamic loading
just dem-boundary              # DEM boundary segfault test only
just performance               # ~2min — throughput benchmarks
just test                      # all of the above

# Stop
just down
```

Performance accepts duration and concurrency:

```bash
just performance 30 10         # 30s duration, 10 workers
```

## Running Against Production

Point `TILESERVER_URL` at your production server and remove `TEST_DATA_DIR`:

```bash
# .env
TILESERVER_URL=https://vector-test.ridewithgps.com
# TEST_DATA_DIR=               (commented out or removed)
```

Then run the same commands — no Docker needed:

```bash
just correctness               # all correctness tests run unchanged
just stability                 # DEM boundary (global tiles auto-selected)
just dem-boundary              # DEM boundary only
```

The DEM boundary test automatically uses `dem_boundary_tiles_global.json` (10k global tiles) when `--base-url` is not localhost, and `dem_boundary_tiles.json` (3.5k CONUS tiles) for local. This works regardless of whether you invoke via `just` or `uv run pytest` directly. Override with `--dem-tiles` if needed.

### What changes in production

| Test suite | Local Docker | Production |
|---|---|---|
| Correctness | All run | All run |
| Stability — DEM boundary | Runs (CONUS tiles) | Runs (global tiles auto-selected) |
| Stability — dynamic loading | Runs (filesystem access) | **Skips** (no `TEST_DATA_DIR`) |
| Performance benchmarks | All run | Baselines may need tuning for network latency |

### Safety

All tests are **read-only HTTP requests** — no writes, no mutations, no side effects. The only exception is dynamic loading (file copy/delete), which skips entirely without `TEST_DATA_DIR`.

## What's Tested

### Correctness

Fast functional checks that every asset type serves correctly.

- **Rendered tiles** — Status codes (200/400/404), PNG/WebP/JPEG format validation, 1x and @2x scale factors, both `rwgpscycle` and `rwgpscycle-flat` styles.

- **Vector tiles** — PBF serving. Tiles with data return 200, without data return 204, out of bounds return 404. Gzip-encoded protobuf body validation.

- **Elevation tiles (fork-critical)** — When elevation data is missing, our fork returns a valid 256x256 PNG with uniform RGB(1,134,160) representing 0m in Terrain-RGB encoding. Without this, MapLibre's raster-dem parser crashes on the client. The test verifies exact pixel values and confirms this behavior is elevation-specific (snow depth still returns 204 for missing tiles).

- **Fonts** — Font list JSON, glyph PBF ranges, caching headers, graceful fallback for unknown fonts.

- **Sprites** — JSON and PNG for 1x and @2x retina. Cross-validates that every sprite entry's coordinates fit within the PNG atlas dimensions.

- **Visual regression** — Pixel-diff comparison against reference images (Portland urban, Mt Hood hillshade, Kansas flat) with 5% tolerance for anti-aliasing variance.

### Stability

Targeted tests for known crash and corruption scenarios.

- **DEM boundary** — Renders tiles at every elevation data edge (tiles with missing neighbors). A connection error during rendering means the renderer segfaulted on a blank DEM tile dimension mismatch. Uses precomputed boundary tiles — CONUS (3.5k tiles) locally, global (10k tiles) against production. Auto-selected based on `--base-url`.

- **Dynamic loading** — Chokidar file watcher validation: copies, overwrites, and deletes mbtiles files under concurrent HTTP load. Requires `TEST_DATA_DIR` (skips without it).

### Performance

Throughput and latency gates against baselines in `tests/performance/baselines.json`.

- **PNG and WebP benchmarks** — Measures tiles/sec and p95 latency under concurrent load. Baselines are set ~20% below measured values. Adjust for your hardware after the first run.

## Generating Test Data

### Visual reference images

```bash
just update-references         # generates reference/ PNGs from current server
```

Commit these to git. Re-generate after intentional style changes.

### Tile sample coordinates

Deterministic samples from mbtiles metadata, used by load and performance tests:

```bash
just generate-samples          # reads mbtiles, writes tests/tile_samples.json
```

### DEM boundary tiles

Precomputed from elevation mbtiles — every tile with a missing neighbor:

```bash
# CONUS (test data)
just generate-dem-boundaries

# Production/global elevation (downsampled to 10k)
just generate-dem-boundaries /path/to/elevation-global.mbtiles
```

## File Structure

```
integration-tests/
├── docker-compose.yml            # local test server (builds from repo root)
├── .env.example                  # environment template
├── justfile                      # task runner
├── pyproject.toml                # pytest + deps
├── generate_tile_samples.py      # tile sample generator script
├── generate_dem_boundaries.py    # DEM boundary tile generator script
├── reference/                    # visual regression baseline PNGs
└── tests/
    ├── conftest.py               # CLI options, httpx client, health gate
    ├── tile_coords.py            # shared coordinates and URL builders
    ├── load_helpers.py           # async load runner, stats, percentiles
    ├── tile_samples.json         # generated tile coordinates (committed)
    ├── dem_boundary_tiles.json   # DEM boundary tiles, CONUS (committed)
    ├── correctness/
    │   ├── test_data_base.py     # base classes for data source tests
    │   ├── test_data_sources.py  # vector, elevation, snow depth
    │   ├── test_rendered.py      # rendered tile formats and status codes
    │   ├── test_visual.py        # pixel-diff visual regression
    │   ├── test_fonts.py         # font list and glyph serving
    │   └── test_sprites.py       # sprite atlas integrity
    ├── stability/
    │   ├── test_dem_boundary.py
    │   ├── test_dynamic_loading.py
    │   └── churn_worker.py       # subprocess for file churn operations
    └── performance/
        ├── conftest.py           # results collector and terminal summary
        ├── baselines.json        # throughput/latency thresholds
        └── test_benchmark.py
```
