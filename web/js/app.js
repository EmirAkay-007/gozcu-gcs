// Wires the page together: one transport, every panel listening to it.

import { EventLog } from "./log.js";
import { setUpLayout } from "./layout.js";
import { TopBar } from "./topbar.js";
import { Transport } from "./transport.js";
import { VideoPanel } from "./video.js";

const topBar = new TopBar(document.getElementById("topbar"));
const eventLog = new EventLog(
  document.getElementById("log-list"),
  document.getElementById("log-count"),
);
new VideoPanel(document.getElementById("video-canvas"));
setUpLayout(document);

let wasConnected = false;

new Transport()
  .on("hello", () => eventLog.clear()) // the server replays its recent events next
  .on("snapshot", (snapshot) => topBar.update(snapshot))
  .on("event", (event) => eventLog.add(event))
  .onStatus((connected) => {
    if (!connected) topBar.showOffline();
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
