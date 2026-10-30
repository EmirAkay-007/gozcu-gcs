import assert from "node:assert/strict";
import { test } from "node:test";

import { buttonAction, connectionChoices } from "../../web/js/connection.js";

const link = (state, connection) => ({ state, connection, vehicle: null });

test("the button closes the link shown, or opens the one typed", () => {
  const open = link("CONNECTED", "tcp:127.0.0.1:5762");
  assert.deepEqual(buttonAction(open, " tcp:127.0.0.1:5762 "), {
    label: "Disconnect",
    request: { type: "disconnect" },
  });
  assert.deepEqual(buttonAction(open, "/dev/ttyUSB0,57600"), {
    label: "Connect",
    request: { type: "connect", connection: "/dev/ttyUSB0,57600" },
  });
  const closed = link("DISCONNECTED", null);
  assert.deepEqual(buttonAction(closed, "tcp:127.0.0.1:5762").request, {
    type: "connect",
    connection: "tcp:127.0.0.1:5762",
  });
  assert.equal(buttonAction(closed, "  ").request, null); // nothing typed: nothing to do
  assert.equal(buttonAction(null, "").request, null); // before the first snapshot
});

test("suggestions: recent links, then serial ports as radios, then the usual ones", () => {
  const ports = [{ device: "/dev/ttyUSB0", description: "CP2102" }];
  assert.deepEqual(connectionChoices(["udpin:0.0.0.0:14551"], ports), [
    "udpin:0.0.0.0:14551",
    "/dev/ttyUSB0,57600",
    "tcp:127.0.0.1:5762",
    "tcp:127.0.0.1:5763",
  ]);
});
