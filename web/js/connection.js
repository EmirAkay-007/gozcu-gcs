// The connection box in the top bar: which MAVLink link the station uses, and the
// button that changes or closes it. It suggests the links used before in this
// browser, the serial ports the station has now (a telemetry radio shows up as
// /dev/ttyUSB0 or similar) and the usual SITL ports. Changing or closing a working
// link drops the telemetry, so the button has to be held (hold.js).

import { holdToConfirm } from "./hold.js";

const RECENT_KEY = "gozcu.connections";
const RECENT_KEPT = 6;
const RADIO_BAUD = 57600; // what SiK telemetry radios run at out of the box
const USUAL = ["tcp:127.0.0.1:5762", "udpin:0.0.0.0:14551", "tcp:127.0.0.1:5763"];

/** The suggestions: recent links first, then serial ports, then the usual ones, no repeats. */
export function connectionChoices(recent, ports) {
  const radios = ports.map((port) => `${port.device},${RADIO_BAUD}`);
  return [...new Set([...recent, ...radios, ...USUAL])];
}

/** What the button would do for the link in the snapshot and the text typed:
 * {label, request}, request null when there is nothing to do. */
export function buttonAction(link, typed) {
  const text = typed.trim();
  const open = link !== null && link.state !== "DISCONNECTED";
  if (open && text === (link.connection ?? "")) {
    return { label: "Disconnect", request: { type: "disconnect" } };
  }
  return { label: "Connect", request: text ? { type: "connect", connection: text } : null };
}

function loadRecent() {
  try {
    const recent = JSON.parse(localStorage.getItem(RECENT_KEY));
    return Array.isArray(recent) ? recent.filter((item) => typeof item === "string") : [];
  } catch {
    return []; // storage blocked, e.g. in a private window
  }
}

function saveRecent(recent) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recent));
  } catch {
    // not remembered this time
  }
}

export class ConnectionBox {
  #input;
  #options;
  #button;
  #link = null; // the snapshot's link part
  #last = ""; // the latest link the station used, still offered once closed
  #ports = [];
  #online = false;

  /** send(request) passes a connect or disconnect request on to the station. */
  constructor({ input, options, button, send }) {
    this.#input = input;
    this.#options = options;
    this.#button = button;
    input.addEventListener("focus", () => send({ type: "list_ports" })); // radios come and go
    input.addEventListener("input", () => this.#showButton());
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      input.value = this.#last; // never mind
      input.blur();
      this.#showButton();
    });
    holdToConfirm(button, () => {
      const { request } = buttonAction(this.#link, input.value);
      if (request === null) return;
      send(request);
      input.blur();
    });
    this.#showChoices();
  }

  update(link) {
    this.#link = link;
    this.#online = true;
    // Remembered once the station uses it, so a string it refused is never suggested.
    if (link.connection && link.connection !== this.#last) this.#remember(link.connection);
    this.#last = link.connection ?? this.#last; // after Disconnect, one hold connects it again
    if (document.activeElement !== this.#input) this.#input.value = this.#last;
    this.#showButton();
  }

  /** The station's serial ports, from its "ports" message. */
  showPorts(ports) {
    this.#ports = ports;
    this.#showChoices();
  }

  showOffline() {
    this.#online = false;
    this.#showButton();
  }

  #showButton() {
    const { label, request } = buttonAction(this.#link, this.#input.value);
    this.#button.textContent = label;
    this.#button.disabled = !this.#online || request === null;
  }

  #showChoices() {
    const descriptions = new Map(
      this.#ports.map((port) => [`${port.device},${RADIO_BAUD}`, port.description]),
    );
    const options = connectionChoices(loadRecent(), this.#ports).map((choice) => {
      const option = document.createElement("option");
      option.value = choice;
      const description = descriptions.get(choice);
      if (description && description !== "n/a") option.label = `${choice} (${description})`;
      return option;
    });
    this.#options.replaceChildren(...options);
  }

  #remember(connection) {
    const recent = [connection, ...loadRecent().filter((item) => item !== connection)];
    saveRecent(recent.slice(0, RECENT_KEPT));
    this.#showChoices();
  }
}
