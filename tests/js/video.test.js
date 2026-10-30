import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDuration, pictureProblem, trackPoint } from "../../web/js/video.js";

const LIVE = { state: "LIVE", reason: null };

test("a recording time reads as minutes and seconds, hours when needed", () => {
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(7.4), "0:07");
  assert.equal(formatDuration(754), "12:34");
  assert.equal(formatDuration(3723), "1:02:03");
});

test("a live picture with fresh frames needs no words over it", () => {
  assert.equal(pictureProblem(LIVE, 0.1, false), null);
});

test("what the station says is wrong is written over the picture", () => {
  const status = { state: "NO SIGNAL", reason: "nothing arriving on udp 127.0.0.1:5600" };
  assert.deepEqual(pictureProblem(status, 0.1, false), {
    title: "NO SIGNAL",
    detail: "nothing arriving on udp 127.0.0.1:5600",
  });
});

test("frames that stop coming here are noticed even while the station says live", () => {
  assert.equal(pictureProblem(LIVE, null, false).title, "STARTING");
  assert.deepEqual(pictureProblem(LIVE, 3.2, false), {
    title: "FROZEN",
    detail: "no new picture for 3 s",
  });
});

test("without the station the picture is marked as old", () => {
  assert.equal(pictureProblem(LIVE, 0.1, true).title, "GCS OFFLINE");
  assert.equal(pictureProblem(null, null, false).title, "NO VIDEO");
});

test("a track is marked where the camera saw it", () => {
  const near = (point, x, y) => Math.abs(point.x - x) < 1e-9 && Math.abs(point.y - y) < 1e-9;
  assert.ok(near(trackPoint(0, 0, 64.7, 640, 480), 320, 240)); // on the optical axis
  assert.ok(near(trackPoint(64.7 / 2, 0, 64.7, 640, 480), 640, 240)); // the edge of the view
  const above = trackPoint(10, 5, 64.7, 640, 480);
  assert.ok(above.x > 320 && above.y < 240); // right and up
  // Square pixels: 5 degrees up moves as far as 5 degrees right does.
  assert.ok(Math.abs(240 - above.y - (trackPoint(5, 0, 64.7, 640, 480).x - 320)) < 1e-9);
});

test("a track outside the picture, or without a bearing, is not marked", () => {
  assert.equal(trackPoint(40, 0, 64.7, 640, 480), null);
  assert.equal(trackPoint(0, -30, 64.7, 640, 480), null); // the view is narrower upright
  assert.equal(trackPoint(null, 0, 64.7, 640, 480), null);
});
