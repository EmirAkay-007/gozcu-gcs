import assert from "node:assert/strict";
import { test } from "node:test";

import {
  flyLine,
  flyToRequest,
  placeText,
  stillTarget,
  suggestedAltitude,
} from "../../web/js/flyto.js";

const BOUNDS = [20, 500];
const field = (value, stale = false) => ({ value, stale });
const POINT = { lat: 41.0123456, lon: 29.0123456 };

test("the altitude offered is the vehicle's own, but at least 50 m", () => {
  assert.equal(suggestedAltitude(field(118.6), BOUNDS), 119);
  assert.equal(suggestedAltitude(field(0.4), BOUNDS), 50); // on the ground
  assert.equal(suggestedAltitude(field(30), BOUNDS), 50); // flying low
  assert.equal(suggestedAltitude(field(118.6, true), BOUNDS), 50); // not current
  assert.equal(suggestedAltitude(undefined, BOUNDS), 50);
  assert.equal(suggestedAltitude(field(900), BOUNDS), 500);
});

test("the request carries the point and a whole number of metres", () => {
  assert.deepEqual(flyToRequest(POINT, "120.4", BOUNDS), {
    request: { action: "fly_to", lat: 41.0123456, lon: 29.0123456, alt: 120 },
  });
  assert.deepEqual(flyToRequest(POINT, " ", BOUNDS), { problem: "Type the altitude" });
  assert.deepEqual(flyToRequest(POINT, "abc", BOUNDS), { problem: "Type the altitude" });
  assert.deepEqual(flyToRequest(POINT, "19", BOUNDS), {
    problem: "Altitude: 20 to 500 m above home",
  });
  assert.equal(flyToRequest(POINT, "501", BOUNDS).request, undefined);
});

test("the place says how far it is from the vehicle when that is known", () => {
  const north = { lat: 41.0213456, lon: 29.0123456 }; // 0.009° north: about 1 km
  assert.equal(placeText(north, field(41.0123456), field(29.0123456)),
    "41.021346, 29.012346 · 1.0 km from the vehicle");
  assert.equal(placeText(POINT, field(41.0118456), field(29.0123456)),
    "41.012346, 29.012346 · 56 m from the vehicle");
  assert.equal(placeText(POINT, field(41.0, true), field(29.0)), "41.012346, 29.012346");
  assert.equal(placeText(POINT, undefined, undefined), "41.012346, 29.012346");
});

function target() {
  return { lat: POINT.lat, lon: POINT.lon, alt: 120, sentAt: 1000, acceptedAt: null };
}

const flyTo = (state) => ({ blocked: null, command: { action: "fly_to", ...POINT, alt: 120, state } });
const MODE = { value: "LOITER", stale: false };
const GUIDED = { value: "GUIDED", stale: false };

test("a point sent stays marked while the vehicle goes there", () => {
  const sent = target();
  assert.equal(stillTarget(sent, { blocked: null, command: null }, MODE, 1500), true); // on its way
  assert.equal(stillTarget(sent, { blocked: null, command: null }, MODE, 3500), false);
  assert.equal(stillTarget(sent, flyTo("PENDING"), MODE, 1600), true);
  assert.equal(stillTarget(sent, flyTo("ACCEPTED"), MODE, 1700), true);
  assert.equal(sent.acceptedAt, 1700);
  assert.equal(stillTarget(sent, flyTo("ACCEPTED"), MODE, 2500), true); // GUIDED not reported yet
  assert.equal(stillTarget(sent, flyTo("ACCEPTED"), GUIDED, 9000), true);
  assert.equal(stillTarget(sent, { blocked: null, command: null }, GUIDED, 9500), true);
  assert.equal(stillTarget(sent, flyTo("ACCEPTED"), MODE, 9600), false); // left GUIDED
});

test("the panel's line is about flying there, not the other commands", () => {
  const arm = (state) => ({ action: "arm", text: "arm", state, detail: null, age: 1 });
  assert.equal(flyLine({ blocked: null, command: arm("ACCEPTED") }).text, "");
  assert.equal(flyLine({ blocked: null, command: arm("PENDING") }).text,
    "arm: waiting for the autopilot…"); // why the button is closed
  assert.equal(flyLine({ blocked: "Gözcü is avoiding", command: arm("NOT SENT") }).text,
    "Commands held: Gözcü is avoiding");
  const refused = { action: "fly_to", text: "fly to 1.000000, 2.000000 at 90 m", state: "FAILED", detail: null, age: 1 };
  assert.deepEqual(flyLine({ blocked: null, command: refused }), {
    text: "fly to 1.000000, 2.000000 at 90 m: failed",
    level: "warn",
  });
});

test("a point the autopilot refused, or that was not sent, is not marked", () => {
  assert.equal(stillTarget(target(), flyTo("FAILED"), MODE, 1500), false);
  assert.equal(stillTarget(target(), flyTo("NOT SENT"), MODE, 1500), false);
  const other = { blocked: null, command: { action: "fly_to", lat: 1, lon: 2, state: "PENDING" } };
  assert.equal(stillTarget(target(), other, MODE, 3500), false);
});

test("a point is no longer marked once another has been sent", () => {
  const sent = target();
  assert.equal(stillTarget(sent, flyTo("ACCEPTED"), GUIDED, 1500), true);
  const other = { blocked: null, command: { action: "fly_to", lat: 1, lon: 2, state: "PENDING" } };
  assert.equal(stillTarget(sent, other, GUIDED, 5000), false);
  const next = target(); // sent while an earlier point is still the newest command
  assert.equal(stillTarget(next, { blocked: null, command: other.command }, GUIDED, 1500), true);
});
