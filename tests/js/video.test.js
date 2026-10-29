import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDuration, pictureProblem } from "../../web/js/video.js";

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
