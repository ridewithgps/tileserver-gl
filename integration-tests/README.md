# Integration Tests

End-to-end test suite for the tileserver-gl fork covering correctness, stability, and performance.

## Quick Start

```bash
cd integration-tests
just sync                       # install test dependencies
cp .env.example .env          # edit TEST_DATA_DIR if needed
just up                        # build + start tileserver
just test                      # run all tests
just down                      # stop tileserver
```

## Running Tests

```bash
just correctness               # ~3 seconds — fast sanity check
just stability                 # ~2 minutes — concurrent load + stress
just performance               # ~1 minute — throughput benchmarks
just test                      # all of the above
```

Stability and performance durations are configurable:

```bash
just stability 60 20           # 60s duration, 20 concurrent workers
just performance 30 10         # 30s duration, 10 concurrent workers
```

## What's Tested

### Correctness (53 tests)

Fast functional checks that every asset type serves correctly.

- **Rendered tiles** — Status codes (200/400/404), PNG/WebP/JPEG format validation, 1x and @2x scale factors. This is the primary code path through MapLibre GL Native that caused the production outage.

- **Vector tiles** — PBF serving from `serve_data.js`. Tiles with data→200, without data→204, out of bounds→404. Gzip-encoded protobuf body validation.

- **Elevation tiles (fork-critical)** — The blank DEM tile test is the most important test in the suite. When elevation data is missing, our fork returns a valid 256x256 PNG with uniform RGB(1,134,160) representing 0m in Terrain-RGB encoding. Without this, MapLibre's raster-dem parser crashes on the client. The test verifies the exact pixel values and proves this behavior is elevation-specific (snow_depth still returns 204 for missing tiles).

- **Fonts** — Font list JSON and glyph PBF ranges. Without working fonts, every text label on the map disappears.

- **Sprites** — JSON and PNG for 1x and @2x retina. Cross-validates that every sprite entry's coordinates fit within the PNG dimensions.

- **Visual regression** — Pixel-diff comparison against reference images (Portland urban, Mt Hood hillshade, Kansas flat). 5% tolerance handles anti-aliasing variance across Docker builds. Run `just update-references` to regenerate baselines.

### Stability (9 tests)

Sustained concurrent load to detect crashes, pool exhaustion, and memory issues.

- **Sustained load** — Mixed-asset concurrent requests (rendered, vector, elevation, fonts) asserting zero 500s, zero 503s, and zero connection drops. Connection drops catch renderer segfaults (process death).

- **Renderer stress** — 3-phase concurrency ramp (low→medium→high) targeting rendered tiles only. Designed to trigger the exact failure mode from the production outage: pool resize events during concurrent rendering. If `minRendererPoolSizes != maxRendererPoolSizes` in config, this produces 503s.

- **Dynamic loading** — Validates the chokidar file watcher by copying/deleting an mbtiles file while running background load. Proves file watcher events don't crash or stall the server. Requires `--mbtiles-dir` (skips cleanly without it).

### Performance (2 tests)

Throughput and latency gates against baselines in `tests/performance/baselines.json`.

- **PNG and WebP benchmarks** — Measures tiles/sec and p95 latency under concurrent load. Baselines are set ~20% below measured values. These would have caught the v5.5.0 regression immediately — PNG throughput dropped from ~37 to ~13 tiles/sec.

Adjust baselines for your hardware after the first run.

### Current Total: 64 tests

Counts can drift as tests are added/removed. Refresh them with:

```bash
just count-tests
```

## Reference Images

Visual regression tests compare against PNGs in `reference/`. Generate them once:

```bash
just update-references
```

Commit the reference images to git. Re-generate after intentional style changes.

## Architecture

```
integration-tests/
├── docker-compose.yml          # test server (builds from repo root)
├── .env.example                # TEST_DATA_DIR, TILESERVER_PORT
├── pyproject.toml              # pytest + deps (uv application, no build-system)
├── justfile
├── reference/                  # visual regression baselines
└── tests/
    ├── conftest.py             # CLI options, httpx client, health gate
    ├── tile_coords.py          # shared coordinates (Portland, Mt Hood, etc.)
    ├── load_helpers.py         # async load runner, stats, percentiles
    ├── correctness/            # fast functional tests
    ├── stability/              # sustained load + stress
    └── performance/            # throughput benchmarks + baselines.json
```
