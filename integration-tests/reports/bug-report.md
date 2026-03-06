# SIGSEGV in `DEMData::backfillBorder` during idle after sustained load

## Status: Investigation complete — ready to file upstream

## Repository

maplibre/maplibre-native

## Summary

Node.js bindings segfault in `mbgl::DEMData::backfillBorder` when rendering styles that use `raster-dem` sources. The crash occurs during idle periods after sustained rendering load, when late-arriving DEM tiles trigger neighbor border backfill on data that has already been freed.

**Affects both maplibre-native 6.0.0 and 6.3.0** — confirmed via `addr2line` on both binaries.

## Environment

- **maplibre-native**: 6.0.0 and 6.3.0 (both affected, same function)
- **Platform**: Node.js (headless rendering via `@maplibre/maplibre-gl-native`)
- **OS**: Ubuntu Noble (Docker), Linux 6.18.7
- **Usage**: tileserver-gl server-side tile rendering

## Crash Details

```
node[PID]: segfault at <addr> ip <addr> error 4 in mbgl.node
```

- **error 4** = read access to unmapped memory (use-after-free)
- **addr2line on v5.3.0 binary (maplibre-native 6.0.0)**: offset `0x62e00f` → `mbgl::DEMData::backfillBorder`
- **addr2line on v5.5.0 binary (maplibre-native 6.3.0)**: offset `0x4ba07c` → `mbgl::DEMData::backfillBorder`

Same function in both versions, different binary offsets due to recompilation.

### Kernel dmesg — 11 crashes across both versions

maplibre-native 6.3.0 (tileserver-gl v5.5.0):
```
[241356.647784] node[2388142]: segfault at 74e8af40015c ip 000074e94b58e00f ... error 4 in mbgl.node[4ba07c,...]
[242374.304589] node[2399800]: segfault at 788853bc2220 ip 000078884ca4800f ... error 4 in mbgl.node[4ba07c,...]
[243155.515308] MainThread[2409969]: segfault at 76d9fd4003bc ip 000076da06e9807c ... error 4 in mbgl.node[4ba07c,...]
[245254.205215] MainThread[2436277]: segfault at 72d99f400150 ip 000072da14a2e07c ... error 4 in mbgl.node[4ba07c,...]
[247194.663168] MainThread[2488122]: segfault at 7beddce003b4 ip 00007bee3f58a07c ... error 4 in mbgl.node[4ba07c,...]
[248199.785872] MainThread[2503908]: segfault at 7629bda00380 ip 0000762a2e95607c ... error 4 in mbgl.node[4ba07c,...]
```

maplibre-native 6.0.0 (tileserver-gl v5.3.0):
```
[250943.432575] node[2550597]: segfault at 7cc84b4001c8 ip 00007cc888df500f ... error 4 in mbgl.node[62e00f,...]
[251057.158575] node[2557646]: segfault at 76dc4760018c ip 000076dceb61c00f ... error 4 in mbgl.node[62e00f,...]
[251276.228898] node[2561350]: segfault at 6ffc6bdc2328 ip 00006ffc6466f00f ... error 4 in mbgl.node[62e00f,...]
[253218.550440] node[2588917]: segfault at 79a38d200188 ip 000079a414c3700f ... error 4 in mbgl.node[62e00f,...]
[255230.940016] node[2623581]: segfault at 744a4aef31c8 ip 0000744ab6a5d00f ... error 4 in mbgl.node[62e00f,...]
```

## Key Finding: Crash is isolated to `raster-dem` source

- **With** `raster-dem` source in style: crashes reliably during load→idle→load
- **Without** `raster-dem` source (layer AND source removed): **no crashes** across multiple test runs

## Reproduction

1. Set up tileserver-gl with a style that includes a `raster-dem` source (e.g., elevation hillshade)
2. Send sustained rendering load (e.g., 5 concurrent workers for 20 seconds, ~1500 requests)
3. Stop load and wait 5 seconds (idle period)
4. Resume load — server crashes immediately or during the pause

The crash is non-deterministic but highly reproducible with the load→idle→load pattern. It triggers more reliably with:
- More concurrent workers (5-20)
- Longer sustained load phases (20+ seconds)
- A pause between load phases (5 seconds is enough)

Even a single renderer (pool size 1) crashes, ruling out cross-instance concurrency.

## Root Cause Analysis

The crash is in `DEMData::backfillBorder()` (`src/mbgl/geometry/dem_data.cpp`), which copies border pixel data from neighboring DEM tiles.

The call chain is:
1. `RenderRasterDEMSource::onTileChanged()` — iterates all 8 neighbors
2. `RasterDEMTile::backfillBorder()` — orchestrates the backfill
3. `DEMData::backfillBorder()` — reads directly from neighbor's `image->data` pointer

**The problem**: `backfillBorder` reads from the neighbor tile's DEM data without verifying it is still valid. During idle after sustained load:
- Tiles are evicted from cache or have their data freed
- A late-arriving tile triggers `onTileChanged`
- `backfillBorder` attempts to read from a neighbor whose DEM data has already been freed
- Use-after-free → SIGSEGV

There are **no null checks or validity guards** on the neighbor's data in `backfillBorder`. There is also **no synchronization** — the method directly accesses `image->data` without locks.

## Performance Impact

`backfillBorder` is also suspected to be a performance bottleneck. Every DEM tile load triggers border backfill on up to 8 neighbors, causing re-uploads that block the render pipeline. Removing the `raster-dem` source significantly improves rendering throughput.

## Historical Context

Related to (but distinct from) mapbox-gl-native PR [#16362](https://github.com/mapbox/mapbox-gl-native/pull/16362), which fixed a crash in `HillshadeBucket::upload()` where `std::move` emptied vertex/index buffers before a re-upload triggered by `backfillBorder`. That specific fix is not applicable to maplibre-native because the upload path was rewritten for the new renderer architecture. However, the underlying issue — `backfillBorder` operating on stale/freed data — was not addressed.

## Suggested Fix

Add validity checks in `RasterDEMTile::backfillBorder()` or `DEMData::backfillBorder()` to verify the neighbor tile's DEM data is still allocated before reading from it. For example:

```cpp
// In DEMData::backfillBorder, before accessing o.image->data:
if (!o.image || !o.image->data.get()) {
    return;
}
```

Or guard at the `RasterDEMTile` level by checking if the neighbor's bucket and DEM data are still valid before calling into `DEMData::backfillBorder`.

## Related Issues

- [maplibre-native #876](https://github.com/maplibre/maplibre-native/issues/876) — ActorRef dangling pointer (open since 2023)
- [maplibre-native #248](https://github.com/maplibre/maplibre-native/issues/248) — Memory leak in headless rendering
- [mapbox-gl-native #16362](https://github.com/mapbox/mapbox-gl-native/pull/16362) — Original HillshadeBucket crash fix
- [maplibre-native #2838](https://github.com/maplibre/maplibre-native/issues/2838) — Hillshade rendering artifacts regression
