# Bug Reports: DEM Tile Handling Across the Stack

Three related bugs in the raster-dem tile pipeline:

1. `maplibre-native` does not guard `DEMData::backfillBorder()` against DEM tiles with mismatched dimensions.
2. Upstream `tileserver-gl` uses a 1x1 blank image on the internal renderer path (`serve_rendered.js`) instead of signaling "no content" to maplibre-gl-native.
3. `maplibre-gl-js` appears to mishandle missing raster-dem tiles returned as HTTP 204 / empty content.

Those bugs interact badly. A missing DEM tile can become a 1x1 placeholder on one path and an HTTP 204 on another path, and each client reacts differently. More detail for each case is described below.

---

## Bug 1: maplibre/maplibre-native — Missing dimension guard in `DEMData::backfillBorder`

### Repository

maplibre/maplibre-native

### Affected Versions

- `@maplibre/maplibre-gl-native` 5.2.0 through 6.3.0
- Likely all prior versions — the dimension check has never existed
- Confirmed via crash reproduction on 6.0.0 and 6.3.0

### Summary

`DEMData::backfillBorder()` in `src/mbgl/geometry/dem_data.cpp` reads pixel data from a neighboring DEM tile without checking that the neighbor has matching dimensions. When a tile server returns a 1x1 blank PNG for a missing DEM tile, `backfillBorder` can read past the end of the 1x1 tile's buffer as if it were 256x256.

This is not just a debug concern. The code path assumes matching tile dimensions, but release builds do not enforce that assumption. In production, this can read past the neighbor tile's allocated buffer and crash the renderer.

### Crash Evidence

The crash signature was captured from kernel `dmesg` output.

```
node[PID]: segfault at <addr> ip <addr> error 4 in mbgl.node
```

- **error 4** = read access to unmapped memory
- The relevant fault offsets we extracted from `dmesg` were `0x4ba07c` and `0x62e00f`.
- We ran:

```bash
addr2line -e ./mbgl-v550.node -f 0x4ba07c
addr2line -e ./mbgl.node -f 0x62e00f
```

- In both cases, the offset resolved to `mbgl::DEMData::backfillBorder`.

This shows that the observed fault offsets in the two tested binaries point to the same function.

### Root Cause

The call chain when a DEM tile loads:
1. `RenderRasterDEMSource::onTileChanged()` — iterates all 8 neighbors
2. `RasterDEMTile::backfillBorder()` — orchestrates the backfill
3. `DEMData::backfillBorder()` — reads directly from neighbor's `image->data` pointer

`backfillBorder` computes pixel offsets assuming `this->dim == o.dim` (both tiles are the same size). When they aren't — e.g., a real 256x256 tile backfills from a 1x1 blank neighbor — the computed offsets exceed the neighbor's buffer allocation:

```cpp
// dem_data.cpp — backfillBorder reads neighbor pixels at computed offsets
// When o.dim=0 (from 1x1 image) but this->dim=256, the offsets are wildly wrong
const int32_t ox = ((x < 0) ? o.dim + x : (x >= o.dim ? x - o.dim : x));
```

The intended invariant is already visible in the debug assertion:

```cpp
assert(dim == o.dim);
```

The core problem is that the function trusts that invariant instead of enforcing it before indexing into the neighbor buffer.

### Reproduction

1. Construct two neighboring DEM tiles with different dimensions
2. Call `DEMData::backfillBorder()` with one normal tile and one 1x1 tile
3. In debug builds, `assert(dim == o.dim)` should fire immediately
4. In release builds, the same mismatch can read out of bounds and *may* crash

In practice, tileserver-gl can trigger this when rendering near the edge of raster-dem coverage, where a missing neighbor is converted into a 1x1 placeholder on the internal renderer path.

### Suggested Fix

Add a dimension check at the top of `DEMData::backfillBorder()`:

```cpp
void DEMData::backfillBorder(const DEMData& o, int8_t dx, int8_t dy) {
    // Tiles must have matching dimensions for border backfill.
    // A dimension mismatch (e.g., 1x1 blank tile + 256x256 real tile)
    // would cause out-of-bounds memory access.
    if (dim != o.dim) {
        return;
    }
    // ... existing code
}
```

This is a one-line safety check that turns the debug-only invariant into a real runtime guard. The existing assert should remain, but release builds need explicit protection too.

### Security Consideration

This appears to be externally triggerable through tile content, so there is at least a technical security angle here. In many deployments that may be low-risk in practice, because tile inputs are tightly controlled, but the underlying issue is still an out-of-bounds read caused by untrusted tile dimensions.

---

## Bug 2: maptiler/tileserver-gl — `serve_rendered.js` sends 1x1 blank PNG for missing DEM tiles instead of using `callback()` (noContent)

### Repository

maptiler/tileserver-gl

### Affected Versions

- Confirmed on v5.3.0 and v5.5.0
- Likely affects any version that routes missing internal renderer tiles through `createEmptyResponse()` for `raster-dem` sources
- Confirmed crash reproduction on v5.3.0 (maplibre-native 6.0.0) and v5.5.0 (maplibre-native 6.3.0)

### Summary

When the internal renderer (`serve_rendered.js`) requests a tile from a local mbtiles source and the tile doesn't exist, the code falls through to `createEmptyResponse()` which generates a 1x1 pixel image:

```javascript
// serve_rendered.js — when fetchTile returns null
createEmptyResponse(sourceInfo.format, sourceInfo.color, callback);
```

For `raster-dem` sources, this 1x1 PNG is passed to maplibre-native as valid DEM tile data. When `backfillBorder` later runs on this tile and a real 256x256 neighbor, the dimension mismatch causes a buffer overrun → segfault (see Bug 1).

### Root Cause

The `createEmptyResponse` function was designed for regular raster/vector sources where a 1x1 transparent image or empty buffer is visually harmless. For `raster-dem` sources, however, the tile dimensions are critical — `backfillBorder` copies pixel rows between neighbors assuming identical dimensions.

### The Fix

For missing `raster-dem` tiles, call `callback()` with zero arguments instead of sending fake tile data:

```javascript
if (fetchTile == null) {
    if (sourceInfo.type === 'raster-dem') {
        // Signal "no content" — the tile loads successfully but is not
        // renderable, so backfillBorder is skipped entirely.
        callback();
        return;
    }
    createEmptyResponse(sourceInfo.format, sourceInfo.color, callback);
    return;
}
```

`callback()` (zero args) takes the native "no content" path instead of creating fake image data, which flows through:
1. `TileLoader` → `setData(nullptr)` (no error, just no data)
2. `raster_dem_tile_worker.cpp` → skips image decoding when `data == nullptr`
3. `raster_dem_tile.cpp` → `renderable = false`
4. `render_raster_dem_source.cpp` → `isRenderable()` returns false → backfill loop skipped entirely

This is maplibre-native's designed "no content" path, analogous to HTTP 204 semantics. The tile enters a loaded-but-empty state where the render continues normally, the empty area gets the background color, and all neighbor interactions are safely skipped.

### Why not `callback()` for all source types?

We considered using `callback()` for all missing tiles (not just `raster-dem`), but `createEmptyResponse` respects `sourceInfo.color` — a source definition can specify what color empty tiles should be. Changing this for non-DEM sources would be a breaking change for users who rely on that behavior. For `raster-dem` sources, `sourceInfo.color` is meaningless (DEM tiles are elevation data, not visual), so `callback()` is strictly correct.

### Verified

A change of this shape was tested against boundary cases involving missing DEM neighbors and tiles near the edge of DEM coverage. Those tests alleviated the segmentation faults.

### Related

- This is the **upstream cause** of Bug 1. Even with the maplibre-native dimension guard, tileserver-gl should not be sending fake DEM data. `callback()` is the semantically correct response.
- The HTTP `serve_data.js` path is a separate concern. External clients and internal native rendering do not necessarily want the same missing-tile behavior.

---

## Bug 3: maplibre/maplibre-gl-js — Raster-DEM source handling for HTTP 204 / empty content remains unclear

### Repository

maplibre/maplibre-gl-js

### Scope

- **Pre-v4.0.0**: Issue [#1551](https://github.com/maplibre/maplibre-gl-js/issues/1551) describes decode failures when raster-dem requests return HTTP 204 / empty bodies
- **v4.0.0+**: PR [#3428](https://github.com/maplibre/maplibre-gl-js/pull/3428) changed the empty-buffer path to return a 1x1 transparent ImageBitmap
- **v4.5.0+**: Issue [#5692](https://github.com/maplibre/maplibre-gl-js/issues/5692) reports missing-tile / fallback problems with uneven coverage

### Summary

This is the least certain of the three bugs. The issue and PR history strongly suggest that MapLibre GL JS has had trouble with missing raster-dem tiles returned as HTTP 204 / empty content, but the correct long-term behavior is not yet obvious from the upstream discussion.

At minimum:

- pre-v4.0.0 behavior appears to have failed to decode empty raster-dem responses, as discussed in [#1551](https://github.com/maplibre/maplibre-gl-js/issues/1551)
- PR [#3428](https://github.com/maplibre/maplibre-gl-js/pull/3428) changed that path by returning a 1x1 transparent ImageBitmap for empty buffers
- issue [#5692](https://github.com/maplibre/maplibre-gl-js/issues/5692) suggests missing raster-dem tiles and fallback semantics are still problematic in later versions

Because the semantics still seem unsettled, this section should document the observed behavior and point to the upstream discussion rather than prescribe a specific fix.

### Browser Reproduction

A standalone browser repro that monkey-patches `fetch` to return valid DEM tiles for most requests and HTTP 204 for a chosen tile is available at [jsbin.com/qofojec](https://jsbin.com/qofojec/edit?js,output). The source is also in [maplibre-gl-js-repo.js](./maplibre-gl-js-repo.js).

### Related Issues

- [#1551](https://github.com/maplibre/maplibre-gl-js/issues/1551) — raster-dem source 204 handling
- [#1579](https://github.com/maplibre/maplibre-gl-js/issues/1579) — Tile Handling of 204/404 results
- [#3428](https://github.com/maplibre/maplibre-gl-js/pull/3428) — Handle loading of empty raster tiles
- [#5392](https://github.com/maplibre/maplibre-gl-js/pull/5392) — more recent discussion of raster-dem empty tile handling
- [#5692](https://github.com/maplibre/maplibre-gl-js/issues/5692) — Incorrect handling of missing tiles with uneven depth coverage
- [mapbox#9304](https://github.com/mapbox/mapbox-gl-js/issues/9304) — Original Mapbox issue about 204 handling

---

## How the three bugs interact

```
Missing DEM tile in mbtiles
         │
         ├─── serve_rendered.js (internal renderer) ──► Bug 2: sends 1x1 PNG
         │         │
         │         ▼
         │    maplibre-native backfillBorder ──► Bug 1: buffer overrun → SIGSEGV
         │
         └─── serve_data.js (HTTP API) ──► may return HTTP 204 or another empty-tile fallback
                   │
                   ▼
              MapLibre GL JS ──► Bug 3: 204 / empty-tile semantics still appear problematic
```

**Defense in depth**: All three should be fixed independently.
- **maplibre-native** should guard against dimension mismatches in `backfillBorder` regardless of input source (prevents buffer overrun from any cause)
- **tileserver-gl** should use `callback()` for missing DEM tiles instead of sending a 1x1 image (correct API usage)
- **maplibre-gl-js** still needs clearer empty-tile semantics for raster-dem sources; the linked issues, PRs, and our JSBin repro suggest the behavior is not fully settled
