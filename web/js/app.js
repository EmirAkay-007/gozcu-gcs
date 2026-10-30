// Wires the page together: one transport, every panel listening to it.

import { AttitudeIndicator } from "./attitude.js";
import { GozcuPanel } from "./gozcu.js";
import { setUpLayout } from "./layout.js";
import { EventLog } from "./log.js";
import { MapPanel } from "./map.js";
import { TopBar } from "./topbar.js";
import { Transport, stationUrl } from "./transport.js";
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
const map = new MapPanel(byId("map"), byId("map-follow"), byId("map-notice"));
const gozcu = new GozcuPanel(byId("gozcu"));
const station = new Transport();
const video = new VideoPanel({
  canvas: byId("video-canvas"),
  info: byId("video-info"),
  record: byId("video-rec"),
  onRecord: (on) => station.send({ type: "video_recording", on }),
});
setUpLayout(document);

let wasConnected = false;

station
  .on("hello", (hello) => {
    eventLog.clear(); // the server replays its recent events next
    telemetry.build(hello.fields);
    map.configure(hello.map);
  })
  .on("snapshot", (snapshot) => {
    topBar.update(snapshot);
    telemetry.update(snapshot);
    attitude.update(snapshot.fields);
    map.update(snapshot.fields);
    gozcu.update(snapshot.gozcu);
  })
  .on("video", (status) => video.update(status))
  .on("event", (event) => eventLog.add(event))
  .onStatus((connected) => {
    if (!connected) {
      topBar.showOffline();
      telemetry.showOffline();
      attitude.showOffline();
      map.showOffline();
      video.showOffline();
      gozcu.showOffline();
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

// Frames come on a connection of their own, so a picture never delays telemetry.
new Transport(stationUrl("/ws/video")).onBinary((frame) => video.showFrame(frame)).start();
