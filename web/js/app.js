// Wires the page together: one transport, every panel listening to it.

import { AttitudeIndicator } from "./attitude.js";
import { setUpLayout } from "./layout.js";
import { EventLog } from "./log.js";
import { TopBar } from "./topbar.js";
import { Transport } from "./transport.js";
import { VideoPanel } from "./video.js";
import { TelemetryPanel } from "./widgets.js";

const byId = (id) => document.getElementById(id);

const topBar = new TopBar(byId("topbar"));
const telemetry = new TelemetryPanel(byId("telemetry"));
const attitude = new AttitudeIndicator(byId("attitude-canvas"));
const eventLog = new EventLog({
  list: byId("log-list"),
  count: byId("log-count"),
  severity: byId("log-severity"),
  source: byId("log-source"),
  search: byId("log-search"),
});
new VideoPanel(byId("video-canvas"));
setUpLayout(document);

let wasConnected = false;

new Transport()
  .on("hello", (hello) => {
    eventLog.clear(); // the server replays its recent events next
    telemetry.build(hello.fields);
  })
  .on("snapshot", (snapshot) => {
    topBar.update(snapshot);
    telemetry.update(snapshot);
    attitude.update(snapshot.fields);
  })
  .on("event", (event) => eventLog.add(event))
  .onStatus((connected) => {
    if (!connected) {
      topBar.showOffline();
      telemetry.showOffline();
      attitude.showOffline();
    }
    if (wasConnected && !connected) {
      eventLog.add({
        time: Date.now() / 1000,
        source: "browser",
        severity: "WARNING",
        text: "lost the connection to the ground station; retrying",
      });
    }
    wasConnected = connected;
  })
  .start();
