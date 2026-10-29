import assert from "node:assert/strict";
import { test } from "node:test";

import { Trail, metresBetween } from "../../web/js/geo.js";

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
