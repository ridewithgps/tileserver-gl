import { projectToTilePixel } from '../src/utils.js';

// The elevation route fetches the tile and getTerrainElevation samples the
// pixel both via projectToTilePixel, so they can't disagree. Pin a known point:
// pixel (189,206) of 12/663/1467 decodes to ~3411 m at Mt Hood's summit. An
// earlier `[x, y, x + 0.1, y + 0.1]` box picked a tile ~2 rows north and read
// ~1058 m instead.
describe('Elevation query tile selection', function () {
  it('resolves a point to the tile/pixel containing it', function () {
    expect(projectToTilePixel(-121.6959, 45.3736, 12, 512)).to.deep.equal({
      xTile: 663,
      yTile: 1467,
      xPixel: 189,
      yPixel: 206,
    });
  });
});
