'use strict';

import fs from 'node:fs';
import path from 'path';
import zlib from 'zlib';

import clone from 'clone';
import express from 'express';
import MBTiles from '@mapbox/mbtiles';
import Pbf from 'pbf';
import { VectorTile } from '@mapbox/vector-tile';

import { getTileUrls, isValidHttpUrl, fixTileJSONCenter } from './utils.js';
import {
  openPMtiles,
  getPMtilesInfo,
  getPMtilesTile,
} from './pmtiles_adapter.js';

const EMPTY_PNG_TILE_256 = zlib.gzipSync(Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAQAAAD2e2DtAAABu0lEQVR42u3SQREAAAzCsOHf9F6oIJXQS07TxQIABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAgAACwAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAAsAEAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAAgAASAABIAAEAACQAAIAAEgAASAABAAAkAACAABIAAEgAAQAAJAAKg9kK0BATSHu+YAAAAASUVORK5CYII=',
  'base64',
));

export const serve_data = {
  init: (options, repo) => {
    const app = express().disable('x-powered-by');

    app.get(
      '/:id/:z(\\d+)/:x(\\d+)/:y(\\d+).:format([\\w.]+)',
      async (req, res, next) => {
        const item = repo[req.params.id];
        if (!item) {
          return res.sendStatus(404);
        }
        const tileJSONFormat = item.tileJSON.format;
        const z = req.params.z | 0;
        const x = req.params.x | 0;
        const y = req.params.y | 0;
        let format = req.params.format;
        if (format === options.pbfAlias) {
          format = 'pbf';
        }
        if (
          format !== tileJSONFormat &&
          !(format === 'geojson' && tileJSONFormat === 'pbf')
        ) {
          return res.status(404).send('Invalid format');
        }
        if (
          z < item.tileJSON.minzoom ||
          0 ||
          x < 0 ||
          y < 0 ||
          z > item.tileJSON.maxzoom ||
          x >= Math.pow(2, z) ||
          y >= Math.pow(2, z)
        ) {
          return res.status(404).send('Out of bounds');
        }
        if (item.sourceType === 'pmtiles') {
          let tileinfo = await getPMtilesTile(item.source, z, x, y);
          if (tileinfo == undefined || tileinfo.data == undefined) {
            return res.status(404).send('Not found');
          } else {
            let data = tileinfo.data;
            let headers = tileinfo.header;
            if (tileJSONFormat === 'pbf') {
              if (options.dataDecoratorFunc) {
                data = options.dataDecoratorFunc(id, 'data', data, z, x, y);
              }
            }
            if (format === 'pbf') {
              headers['Content-Type'] = 'application/x-protobuf';
            } else if (format === 'geojson') {
              headers['Content-Type'] = 'application/json';
              const tile = new VectorTile(new Pbf(data));
              const geojson = {
                type: 'FeatureCollection',
                features: [],
              };
              for (const layerName in tile.layers) {
                const layer = tile.layers[layerName];
                for (let i = 0; i < layer.length; i++) {
                  const feature = layer.feature(i);
                  const featureGeoJSON = feature.toGeoJSON(x, y, z);
                  featureGeoJSON.properties.layer = layerName;
                  geojson.features.push(featureGeoJSON);
                }
              }
              data = JSON.stringify(geojson);
            }
            delete headers['ETag']; // do not trust the tile ETag -- regenerate
            headers['Content-Encoding'] = 'gzip';
            res.set(headers);

            data = zlib.gzipSync(data);

            return res.status(200).send(data);
          }
        } else if (item.sourceType === 'mbtiles') {
          item.source.getTile(z, x, y, (err, data, headers) => {
            let isGzipped;
            if (err) {
              if (/does not exist/.test(err.message)) {
                if (format === 'png') {
                  headers = { 'Content-Type': 'image/png' };
                  headers['Content-Encoding'] = 'gzip';
                  res.set(headers);
                  return res.status(200).send(EMPTY_PNG_TILE_256);
                }
                return res.status(204).send();
              } else {
                return res
                  .status(500)
                  .header('Content-Type', 'text/plain')
                  .send(err.message);
              }
            } else {
              if (data == null) {
                return res.status(404).send('Not found');
              } else {
                if (tileJSONFormat === 'pbf') {
                  isGzipped =
                    data.slice(0, 2).indexOf(Buffer.from([0x1f, 0x8b])) === 0;
                  if (options.dataDecoratorFunc) {
                    if (isGzipped) {
                      data = zlib.unzipSync(data);
                      isGzipped = false;
                    }
                    data = options.dataDecoratorFunc(id, 'data', data, z, x, y);
                  }
                }
                if (format === 'pbf') {
                  headers['Content-Type'] = 'application/x-protobuf';
                } else if (format === 'geojson') {
                  headers['Content-Type'] = 'application/json';

                  if (isGzipped) {
                    data = zlib.unzipSync(data);
                    isGzipped = false;
                  }

                  const tile = new VectorTile(new Pbf(data));
                  const geojson = {
                    type: 'FeatureCollection',
                    features: [],
                  };
                  for (const layerName in tile.layers) {
                    const layer = tile.layers[layerName];
                    for (let i = 0; i < layer.length; i++) {
                      const feature = layer.feature(i);
                      const featureGeoJSON = feature.toGeoJSON(x, y, z);
                      featureGeoJSON.properties.layer = layerName;
                      geojson.features.push(featureGeoJSON);
                    }
                  }
                  data = JSON.stringify(geojson);
                }
                delete headers['ETag']; // do not trust the tile ETag -- regenerate
                headers['Content-Encoding'] = 'gzip';
                res.set(headers);

                if (!isGzipped) {
                  data = zlib.gzipSync(data);
                }

                return res.status(200).send(data);
              }
            }
          });
        }
      },
    );

    app.get('/:id.json', (req, res, next) => {
      const item = repo[req.params.id];
      if (!item) {
        return res.sendStatus(404);
      }
      const tileSize = undefined;
      const info = clone(item.tileJSON);
      info.tiles = getTileUrls(
        req,
        info.tiles,
        `data/${req.params.id}`,
        tileSize,
        info.format,
        item.publicUrl,
        {
          pbf: options.pbfAlias,
        },
      );
      return res.send(info);
    });

    return app;
  },
  remove: (repo, id) => {
    delete repo[id];
  },
  add: async (options, repo, params, id, publicUrl) => {
    let inputFile;
    let inputType;
    if (params.pmtiles) {
      inputType = 'pmtiles';
      if (isValidHttpUrl(params.pmtiles)) {
        inputFile = params.pmtiles;
      } else {
        inputFile = path.resolve(options.paths.pmtiles, params.pmtiles);
      }
    } else if (params.mbtiles) {
      inputType = 'mbtiles';
      if (isValidHttpUrl(params.mbtiles)) {
        console.log(
          `ERROR: MBTiles does not support web based files. "${params.mbtiles}" is not a valid data file.`,
        );
        process.exit(1);
      } else {
        inputFile = path.resolve(options.paths.mbtiles, params.mbtiles);
      }
    }

    let tileJSON = {
      tiles: params.domains || options.domains,
    };

    if (!isValidHttpUrl(inputFile)) {
      const inputFileStats = fs.statSync(inputFile);
      if (!inputFileStats.isFile() || inputFileStats.size === 0) {
        throw Error(`Not valid input file: "${inputFile}"`);
      }
    }

    let source;
    let sourceType;
    if (inputType === 'pmtiles') {
      source = openPMtiles(inputFile);
      sourceType = 'pmtiles';
      const metadata = await getPMtilesInfo(source);

      tileJSON['name'] = id;
      tileJSON['format'] = 'pbf';
      Object.assign(tileJSON, metadata);

      tileJSON['tilejson'] = '2.0.0';
      delete tileJSON['filesize'];
      delete tileJSON['mtime'];
      delete tileJSON['scheme'];

      Object.assign(tileJSON, params.tilejson || {});
      fixTileJSONCenter(tileJSON);

      if (options.dataDecoratorFunc) {
        tileJSON = options.dataDecoratorFunc(id, 'tilejson', tileJSON);
      }
    } else if (inputType === 'mbtiles') {
      sourceType = 'mbtiles';
      const sourceInfoPromise = new Promise((resolve, reject) => {
        source = new MBTiles(inputFile + '?mode=ro', (err) => {
          if (err) {
            reject(err);
            return;
          }
          source.getInfo((err, info) => {
            if (err) {
              reject(err);
              return;
            }
            tileJSON['name'] = id;
            tileJSON['format'] = 'pbf';

            Object.assign(tileJSON, info);

            tileJSON['tilejson'] = '2.0.0';
            delete tileJSON['filesize'];
            delete tileJSON['mtime'];
            delete tileJSON['scheme'];

            Object.assign(tileJSON, params.tilejson || {});
            fixTileJSONCenter(tileJSON);

            if (options.dataDecoratorFunc) {
              tileJSON = options.dataDecoratorFunc(id, 'tilejson', tileJSON);
            }
            resolve();
          });
        });
      });

      await sourceInfoPromise;
    }

    repo[id] = {
      tileJSON,
      publicUrl,
      source,
      sourceType,
    };
  },
};
