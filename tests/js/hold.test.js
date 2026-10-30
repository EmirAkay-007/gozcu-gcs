import assert from "node:assert/strict";
import { test } from "node:test";

import { holdToConfirm } from "../../web/js/hold.js";

/** Just enough of a button: events, the disabled flag, classes and a style. */
class FakeButton extends EventTarget {
  disabled = false;
  classes = new Set();
  classList = { add: (name) => this.classes.add(name), remove: (name) => this.classes.delete(name) };
  style = { setProperty() {} };
}

function fire(button, type, details = {}) {
  button.dispatchEvent(Object.assign(new Event(type, { cancelable: true }), details));
}

function setUp(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const button = new FakeButton();
  const sent = [];
  const cancel = holdToConfirm(button, () => sent.push("sent"), 600);
  return { button, sent, cancel };
}

test("a command goes once the button has been held long enough, and only once", (t) => {
  const { button, sent } = setUp(t);
  fire(button, "pointerdown", { button: 0 });
  assert.ok(button.classes.has("is-holding"));
  t.mock.timers.tick(599);
  assert.deepEqual(sent, []);
  t.mock.timers.tick(1);
  assert.deepEqual(sent, ["sent"]);
  assert.ok(!button.classes.has("is-holding"));
  t.mock.timers.tick(5000); // still held: nothing more
  fire(button, "pointerup");
  assert.deepEqual(sent, ["sent"]);
});

test("letting go early, or moving off the button, sends nothing", (t) => {
  const { button, sent } = setUp(t);
  fire(button, "pointerdown", { button: 0 });
  t.mock.timers.tick(300);
  fire(button, "pointerup");
  fire(button, "pointerdown", { button: 0 });
  t.mock.timers.tick(300);
  fire(button, "pointerleave");
  t.mock.timers.tick(2000);
  assert.deepEqual(sent, []);
  assert.ok(!button.classes.has("is-holding"));
});

test("a disabled button cannot be held, and cancel() ends a hold", (t) => {
  const { button, sent, cancel } = setUp(t);
  button.disabled = true;
  fire(button, "pointerdown", { button: 0 });
  assert.ok(!button.classes.has("is-holding"));
  button.disabled = false;
  fire(button, "pointerdown", { button: 0 });
  t.mock.timers.tick(300);
  cancel(); // e.g. the proposal expired meanwhile
  t.mock.timers.tick(2000);
  assert.deepEqual(sent, []);
});

test("Space or Enter held down works like the pointer; a right button does not", (t) => {
  const { button, sent } = setUp(t);
  fire(button, "pointerdown", { button: 2 });
  t.mock.timers.tick(1000);
  assert.deepEqual(sent, []);
  fire(button, "keydown", { key: " ", repeat: false });
  fire(button, "keydown", { key: " ", repeat: true }); // the key repeating does not restart it
  t.mock.timers.tick(600);
  fire(button, "keyup", { key: " " });
  fire(button, "keydown", { key: "Enter", repeat: false });
  t.mock.timers.tick(100);
  fire(button, "keyup", { key: "Enter" });
  t.mock.timers.tick(1000);
  assert.deepEqual(sent, ["sent"]);
});
