// Wires the page together: one transport, every panel listening to it.
//
// Each panel is updated on its own: one that fails must not stop the others, so
// its error is logged (once) and the rest of the page carries on.

import { AttitudeIndicator } from "./attitude.js";
import { buildNotice } from "./build.js";
import { ConnectionBox } from "./connection.js";
import { FlyHere } from "./flyto.js";
import { GozcuPanel } from "./gozcu.js";
import { setUpLayout } from "./layout.js";
import { EventLog } from "./log.js";
import { MapPanel } from "./map.js";
import { TopBar } from "./topbar.js";
import { Transport, stationUrl } from "./transport.js";
import { VehiclePanel } from "./vehicle.js";
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
const gozcu = new GozcuPanel(byId("gozcu"), (command) =>
  station.send({ type: "gozcu_command", ...command }),
);
const station = new Transport();
const sendVehicle = (request) => station.send({ type: "vehicle_command", ...request });
const vehicle = new VehiclePanel({
  mode: byId("control-mode"),
  set: byId("control-set"),
  arm: byId("control-arm"),
  disarm: byId("control-disarm"),
  line: byId("control-line"),
  send: sendVehicle,
});
const flyHere = new FlyHere({
  map,
  panel: byId("fly-here"),
  place: byId("fly-here-place"),
  altitude: byId("fly-here-alt"),
  button: byId("fly-here-go"),
  close: byId("fly-here-close"),
  line: byId("fly-here-line"),
  send: sendVehicle,
});
const connection = new ConnectionBox({
  input: byId("connection-input"),
  options: byId("connection-options"),
  button: byId("connection-button"),
  send: (request) => station.send(request),
});
const video = new VideoPanel({
  canvas: byId("video-canvas"),
  info: byId("video-info"),
  record: byId("video-rec"),
  overlay: byId("video-overlay"),
  onRecord: (on) => station.send({ type: "video_recording", on }),
});
setUpLayout(document);

const reported = new Set();

/** Run one panel's part; if it fails, say so in the log once, and go on. */
function guarded(panel, work) {
  try {
    work();
  } catch (error) {
    console.error(error);
    const text = `the ${panel} panel failed (${error.message}); reloading the page may help`;
    if (reported.has(text)) return;
    reported.add(text);
    eventLog.add({ time: Date.now() / 1000, source: "browser", severity: "ERROR", text });
  }
}

/** Run every panel's part of a message, each guarded. */
function each(parts) {
  for (const [panel, work] of parts) guarded(panel, work);
}

let wasConnected = false;
let firstBuild = null; // of the first station met up to date (build.js)

/** Say whether the page and the station belong together, above everything else. */
function checkBuild(hello) {
  const notice = byId("station-notice");
  notice.textContent = buildNotice(hello, firstBuild);
  notice.hidden = notice.textContent === "";
  if (hello.build !== undefined && !hello.updated) firstBuild ??= hello.build;
}

station
  .on("hello", (hello) => {
    eventLog.clear(); // the server replays its recent events next
    each([
      ["top bar", () => checkBuild(hello)],
      ["telemetry", () => telemetry.build(hello.fields)],
      ["vehicle", () => vehicle.configure(hello.control)],
      ["map", () => map.configure(hello.map)],
      ["map", () => flyHere.configure(hello.control)],
      ["video", () => video.configure(hello.video)],
    ]);
  })
  .on("snapshot", (snapshot) =>
    each([
      ["top bar", () => topBar.update(snapshot)],
      ["connection", () => connection.update(snapshot.link)],
      ["telemetry", () => telemetry.update(snapshot)],
      ["attitude", () => attitude.update(snapshot.fields)],
      ["vehicle", () => vehicle.update(snapshot)],
      ["map", () => map.update(snapshot.fields)],
      ["map", () => map.showTracks(snapshot.gozcu)],
      ["map", () => flyHere.update(snapshot)],
      ["Gözcü", () => gozcu.update(snapshot.gozcu)],
      ["video", () => video.setGozcu(snapshot.gozcu)],
    ]),
  )
  .on("video", (status) => guarded("video", () => video.update(status)))
  .on("ports", (message) => guarded("connection", () => connection.showPorts(message.ports)))
  .on("event", (event) => eventLog.add(event))
  .onStatus((connected) => {
    if (!connected) {
      each([
        ["top bar", () => topBar.showOffline()],
        ["connection", () => connection.showOffline()],
        ["telemetry", () => telemetry.showOffline()],
        ["attitude", () => attitude.showOffline()],
        ["vehicle", () => vehicle.showOffline()],
        ["map", () => map.showOffline()],
        ["map", () => flyHere.showOffline()],
        ["video", () => video.showOffline()],
        ["Gözcü", () => gozcu.showOffline()],
      ]);
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
