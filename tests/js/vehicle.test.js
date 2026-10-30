import assert from "node:assert/strict";
import { test } from "node:test";

import { controlLine, vehicleControls } from "../../web/js/vehicle.js";

const armed = (value, stale = false) => ({ value, text: String(value), stale });
const command = (state, action = "mode", age = 1) => ({
  action,
  text: action === "mode" ? "mode RTL" : "arm",
  state,
  detail: null,
  age,
});

test("arm or disarm, whichever applies, while the autopilot is free", () => {
  const free = { blocked: null, command: null };
  assert.deepEqual(vehicleControls(free, armed(false)), {
    free: true,
    arm: true,
    disarm: false,
    pendingAction: null,
  });
  assert.equal(vehicleControls(free, armed(true)).disarm, true);
  assert.equal(vehicleControls(free, armed(true)).arm, false);
  const unknown = vehicleControls(free, armed(true, true)); // an old arm state proves nothing
  assert.equal(unknown.arm || unknown.disarm, false);
});

test("nothing while a command is in flight, or while the station holds them", () => {
  const busy = vehicleControls({ blocked: null, command: command("PENDING") }, armed(false));
  assert.equal(busy.free, false);
  assert.equal(busy.arm, false);
  assert.equal(busy.pendingAction, "mode");
  const held = vehicleControls({ blocked: "Gözcü is avoiding", command: null }, armed(false));
  assert.equal(held.free, false);
  assert.equal(held.arm, false);
});

test("the line says what became of the newest command, or why none can go", () => {
  assert.deepEqual(controlLine({ blocked: null, command: command("PENDING") }), {
    text: "mode RTL: waiting for the autopilot…",
    level: "",
  });
  assert.deepEqual(controlLine({ blocked: null, command: command("FAILED", "arm") }), {
    text: "arm: failed",
    level: "warn",
  });
  assert.equal(controlLine({ blocked: null, command: command("NO ANSWER") }).text,
    "mode RTL: no answer from the autopilot");
  const held = { blocked: "Gözcü is avoiding", command: command("ACCEPTED") };
  assert.deepEqual(controlLine(held), { text: "Commands held: Gözcü is avoiding", level: "" });
  const refused = { blocked: "Gözcü is avoiding", command: command("NOT SENT") };
  assert.equal(controlLine(refused).text, "mode RTL: not sent"); // what the operator just tried
  assert.deepEqual(controlLine({ blocked: null, command: null }), { text: "", level: "" });
});
