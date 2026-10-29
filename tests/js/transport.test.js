import assert from "node:assert/strict";
import { test } from "node:test";

import { Transport } from "../../web/js/transport.js";

/** Stands in for the browser's WebSocket; the test opens, feeds and closes it. */
class FakeSocket {
  onopen = null; // set by the transport, as on a real socket
  onmessage = null;
  onclose = null;

  constructor(url, sockets) {
    this.url = url;
    sockets.push(this);
  }

  open() {
    this.onopen();
  }

  receive(message) {
    this.onmessage({ data: JSON.stringify(message) });
  }

  close() {
    this.onclose();
  }
}

/** Every socket the transport opens during the test, oldest first; timers are faked. */
function fakeBrowser(t) {
  const sockets = [];
  const original = globalThis.WebSocket;
  globalThis.WebSocket = class extends FakeSocket {
    constructor(url) {
      super(url, sockets);
    }
  };
  t.after(() => {
    globalThis.WebSocket = original;
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  return sockets;
}

/** Milliseconds until the transport opens its next socket, to the nearest 100. */
function waitForRetry(t, sockets) {
  const before = sockets.length;
  let waited = 0;
  while (sockets.length === before && waited < 60_000) {
    t.mock.timers.tick(100);
    waited += 100;
  }
  return waited;
}

test("failed attempts are reported once and retried, more slowly each time", (t) => {
  const sockets = fakeBrowser(t);
  const statuses = [];
  new Transport("ws://gcs/ws").onStatus((connected) => statuses.push(connected)).start();
  assert.equal(sockets[0].url, "ws://gcs/ws");
  const waits = [];
  for (let attempt = 0; attempt < 6; attempt++) {
    sockets.at(-1).close(); // the server is not up
    waits.push(waitForRetry(t, sockets));
  }
  assert.deepEqual(waits, [500, 1000, 2000, 4000, 5000, 5000]);
  assert.deepEqual(statuses, [false]);
});

test("a connection that opens and drops is reported both times, and retried quickly", (t) => {
  const sockets = fakeBrowser(t);
  const statuses = [];
  new Transport("ws://gcs/ws").onStatus((connected) => statuses.push(connected)).start();
  sockets[0].close();
  waitForRetry(t, sockets);
  sockets[1].close();
  waitForRetry(t, sockets);
  sockets[2].open();
  sockets[2].close(); // the server went away
  assert.equal(waitForRetry(t, sockets), 500); // a success starts the waits over
  sockets[3].open();
  assert.deepEqual(statuses, [false, true, false, true]);
});

test("each message goes to the handlers of its type", (t) => {
  const sockets = fakeBrowser(t);
  const seen = [];
  new Transport("ws://gcs/ws")
    .on("event", (event) => seen.push(`event ${event.text}`))
    .on("snapshot", (snapshot) => seen.push(`snapshot ${snapshot.seq}`))
    .on("snapshot", () => seen.push("second handler"))
    .start();
  sockets[0].open();
  sockets[0].receive({ type: "event", text: "hello" });
  sockets[0].receive({ type: "snapshot", seq: 7 });
  sockets[0].receive({ type: "something newer" });
  assert.deepEqual(seen, ["event hello", "snapshot 7", "second handler"]);
});
