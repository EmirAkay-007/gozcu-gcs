import assert from "node:assert/strict";
import { test } from "node:test";

import { Trail, destination, metresBetween, metresPerPixel } from "../../web/js/geo.js";

test("a degree of latitude is about 111.2 km, as in gcs/units.py", () => {
  assert.ok(Math.abs(metresBetween(0, 0, 1, 0) - 111195.08) < 0.01);
  assert.ok(Math.abs(metresBetween(39.9, 41.2, 39.9, 41.2)) < 1e-9);
});

test("the trail takes a position only once the vehicle has moved a step", () => {
  const trail = new Trail(10, 1);
  assert.equal(trail.add(40, 41), true);
  assert.equal(trail.add(40.000005, 41), false); // 0.56 m away
  assert.equal(trail.add(40.00001, 41), true); // 1.1 m away
  assert.deepEqual(trail.points, [
    [40, 41],
    [40.00001, 41],
  ]);
});

test("the trail keeps only the newest points", () => {
  const trail = new Trail(3, 1);
  for (const lat of [0, 1, 2, 3, 4]) trail.add(lat, 0);
  assert.deepEqual(trail.points, [
    [2, 0],
    [3, 0],
    [4, 0],
  ]);
  trail.clear();
  assert.deepEqual(trail.points, []);
});

test("a point a given distance away along a bearing", () => {
  const [north, sameLon] = destination(0, 0, 0, 1000);
  assert.ok(Math.abs(north - 1000 / 111195.08) < 1e-9 && Math.abs(sameLon) < 1e-12);
  const [lat, lon] = destination(39.9, 41.2, 90, 500); // east
  assert.ok(Math.abs(metresBetween(39.9, 41.2, lat, lon) - 500) < 1e-6);
  assert.ok(lon > 41.2 && Math.abs(lat - 39.9) < 1e-5);
});

test("a screen pixel covers less ground as the map zooms in, and nearer the poles", () => {
  assert.ok(Math.abs(metresPerPixel(0, 0) - 156543.03) < 0.01);
  assert.ok(Math.abs(metresPerPixel(0, 16) * 2 ** 16 - metresPerPixel(0, 0)) < 1e-6);
  assert.ok(Math.abs(metresPerPixel(60, 10) - metresPerPixel(0, 10) / 2) < 1e-9);
});
