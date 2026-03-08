/*
  Minimal browser repro for MapLibre GL JS raster-dem handling with missing tiles.

  Intended use in JS Bin / JSFiddle:

  HTML:
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width">
      <title>DEM Mismatch Reproduction</title>
      <script src="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js"></script>
      <link href="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css" rel="stylesheet" />
    </head>
    <body>
    </body>
    </html>

  JavaScript:
    Paste this entire file into the JS panel.

  What it does:
  - monkey-patches fetch to intercept requests to a fake DEM endpoint (https://dem.invalid/)
  - generates synthetic 256x256 Terrain-RGB tiles (a cone-shaped hill at Mount Hood)
  - returns HTTP 204 for one chosen tile to simulate a missing DEM neighbor
  - no real tile server is needed — everything runs client-side

  Expected result on affected versions:
  - MapLibre loads the page and renders the synthetic hill with terrain + hillshade
  - exactly one DEM tile is forced missing (outlined in red on the map)
  - the console reports a raster-dem error such as "dem dimension mismatch"
*/

// .invalid is an IANA reserved TLD — this URL can never resolve to a real server.
// All requests to it are intercepted by the fetch patch below.
const DEM_ORIGIN = 'https://dem.invalid';
const CENTER = [-121.695, 45.374]; // Mount Hood
const MISSING_TILE_ZOOM = 12;

// Convert lng/lat to tile coordinates at a given zoom.
function lngLatToTile(lng, lat, z) {
  const n = 1 << z;
  const latRad = (lat * Math.PI) / 180;
  return {
    z,
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  };
}

// The tile we'll force-miss: offset slightly from center so it's a neighbor.
const MISSING_TILE = lngLatToTile(CENTER[0] + 0.12, CENTER[1] + 0.03, MISSING_TILE_ZOOM);

const originalFetch = window.fetch.bind(window);
window.__useDemFix = false;

// Synthetic radial cone centered on Mount Hood (22km radius, 3200m peak).
// Returns elevation in meters for any lng/lat.
function elevation(lng, lat) {
  const mPerDeg = 111320;
  const dx = (lng - CENTER[0]) * mPerDeg * Math.cos((CENTER[1] * Math.PI) / 180);
  const dy = (lat - CENTER[1]) * mPerDeg;
  const t = 1 - Math.sqrt(dx * dx + dy * dy) / 22000;
  return t > 0 ? 3200 * t * t : 0;
}

// Encode elevation as Terrain-RGB: value = (meters + 10000) * 10
function terrainRgb(meters) {
  const v = Math.round((meters + 10000) * 10);
  return [Math.floor(v / 65536) % 256, Math.floor(v / 256) % 256, v % 256];
}

// Generate a 256x256 Terrain-RGB tile with the synthetic hill.
async function makeDemTile(z, x, y) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(256, 256);
  const worldSize = 256 << z;

  for (let py = 0; py < 256; py++) {
    for (let px = 0; px < 256; px++) {
      const lng = ((x * 256 + px + 0.5) / worldSize) * 360 - 180;
      const mercY = Math.PI * (1 - (2 * (y * 256 + py + 0.5)) / worldSize);
      const lat = (Math.atan(Math.sinh(mercY)) * 180) / Math.PI;
      const [r, g, b] = terrainRgb(elevation(lng, lat));
      const i = (py * 256 + px) * 4;
      img.data[i] = r; img.data[i + 1] = g; img.data[i + 2] = b; img.data[i + 3] = 255;
    }
  }

  ctx.putImageData(img, 0, 0);
  const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
  return new Response(blob, {
    status: 200,
    headers: { 'Content-Type': 'image/png', 'Cache-Control': 'no-store' },
  });
}

// --- Fetch patch: the core of the repro ---
// Intercepts all requests to DEM_ORIGIN. For one specific tile, returns HTTP 204
// (missing). For all others, generates a synthetic Terrain-RGB PNG on the fly.
// Everything else passes through to the real network.
window.fetch = async function (input, init) {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);

  if (url.origin === DEM_ORIGIN && url.pathname.startsWith('/dem/')) {
    const m = url.pathname.match(/\/dem\/(\d+)\/(\d+)\/(\d+)\.png$/);
    if (m && Number(m[1]) === MISSING_TILE.z && Number(m[2]) === MISSING_TILE.x && Number(m[3]) === MISSING_TILE.y) {
      console.warn('Returning 204 for missing DEM tile:', url.pathname);
      return new Response(null, { status: 204, statusText: 'No Content' });
    }
    return makeDemTile(Number(m[1]), Number(m[2]), Number(m[3]));
  }

  return originalFetch(input, init);
};

// --- Experimental client-side fix (toggle via checkbox) ---

function installExperimentalDemFix() {
  const proto = maplibregl.RasterDEMTileSource?.prototype;
  if (!proto || proto.__demFixWrapped) return;

  const orig = proto.loadTile;
  proto.loadTile = async function (tile) {
    await orig.call(this, tile);
    if (!window.__useDemFix) return;
    if (tile?.dem && typeof tile.dem.dim === 'number' && tile.dem.dim !== this.tileSize) {
      console.warn('DEM fix: dropping mismatched tile', tile.tileID?.canonical);
      delete tile.dem;
      tile.state = 'loaded';
      tile.needsHillshadePrepare = tile.needsTerrainPrepare = false;
    }
  };
  proto.__demFixWrapped = true;
}

installExperimentalDemFix();

// --- Map setup ---

document.body.style.margin = '0';

const mapEl = document.getElementById('map') || (() => {
  const el = document.createElement('div');
  el.id = 'map';
  Object.assign(el.style, { position: 'fixed', inset: '0' });
  document.body.appendChild(el);
  return el;
})();

// Control panel
const panel = document.createElement('div');
Object.assign(panel.style, {
  position: 'fixed', top: '12px', left: '12px', zIndex: '10',
  background: 'rgba(255,255,255,0.96)', border: '1px solid rgba(0,0,0,0.15)',
  borderRadius: '8px', padding: '10px 12px', font: '12px/1.4 sans-serif',
  boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
});
panel.innerHTML = `
  <label style="display:flex;align-items:flex-start;gap:8px;max-width:280px">
    <input id="use-fix" type="checkbox" />
    <span>
      <strong>Use experimental client fix</strong><br>
      Drop mismatched DEM tiles after load instead of letting backfill use them.
    </span>
  </label>
  <div style="margin-top:8px;color:#444">
    Missing tile: z${MISSING_TILE.z}/${MISSING_TILE.x}/${MISSING_TILE.y}
  </div>
  <div style="margin-top:6px;color:#888;font-style:italic">
    Open the browser console to see DEM errors.
  </div>`;
document.body.appendChild(panel);

let map;

function buildMap() {
  console.clear();
  if (map) map.remove();

  map = new maplibregl.Map({
    container: mapEl,
    center: CENTER,
    zoom: 12,
    pitch: 35,
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          attribution: '&copy; OpenStreetMap contributors',
          maxzoom: 19,
        },
        dem: {
          type: 'raster-dem',
          encoding: 'mapbox',
          tileSize: 256,
          maxzoom: 12,
          tiles: [`${DEM_ORIGIN}/dem/{z}/{x}/{y}.png`],
        },
      },
      terrain: { source: 'dem', exaggeration: 1.2 },
      layers: [
        { id: 'osm', type: 'raster', source: 'osm' },
        {
          id: 'hillshade', type: 'hillshade', source: 'dem',
          paint: {
            'hillshade-shadow-color': '#32404d',
            'hillshade-highlight-color': '#fff',
            'hillshade-accent-color': '#70879e',
          },
        },
      ],
    },
  });

  map.addControl(new maplibregl.NavigationControl(), 'top-right');

  map.on('load', () => {
    // Red outline around the missing tile
    const n = 1 << MISSING_TILE.z;
    const w = (MISSING_TILE.x / n) * 360 - 180;
    const e = ((MISSING_TILE.x + 1) / n) * 360 - 180;
    const toLat = (y) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
    const s = toLat(MISSING_TILE.y + 1);
    const nn = toLat(MISSING_TILE.y);

    map.addSource('outline', {
      type: 'geojson',
      data: {
        type: 'Feature', properties: {},
        geometry: {
          type: 'Polygon',
          coordinates: [[[w, s], [e, s], [e, nn], [w, nn], [w, s]]],
        },
      },
    });
    map.addLayer({
      id: 'outline', type: 'line', source: 'outline',
      paint: { 'line-color': '#ff3b30', 'line-width': 3 },
    });

    new maplibregl.Marker({ color: '#111' })
      .setLngLat(CENTER)
      .setPopup(new maplibregl.Popup({ offset: 16 }).setText('Mount Hood'))
      .addTo(map);

    new maplibregl.Marker({ color: '#ff3b30' })
      .setLngLat([(w + e) / 2, (s + nn) / 2])
      .setPopup(new maplibregl.Popup({ offset: 16 }).setText(
        `Missing DEM tile z${MISSING_TILE.z}/${MISSING_TILE.x}/${MISSING_TILE.y}`,
      ))
      .addTo(map);

    console.log('Map loaded. Missing DEM tile:', MISSING_TILE);
  });

  map.on('error', (e) => console.error('MapLibre error:', e.error || e));
}

panel.querySelector('#use-fix').addEventListener('change', (e) => {
  window.__useDemFix = e.target.checked;
  buildMap();
});

buildMap();
