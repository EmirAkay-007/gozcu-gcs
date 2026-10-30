// The top bar: link health, vehicle, mode, arm state, GPS, battery and the clock.
// Every element with data-key shows the snapshot field of that name.

import { WAITING, markStale, showField } from "./fields.js";

const LINK_STATES = {
  CONNECTED: { text: "CONNECTED", level: "ok" },
  NO_VEHICLE: { text: "NO VEHICLE", level: "warn" },
  CONNECTING: { text: "CONNECTING", level: "warn" },
  LINK_LOST: { text: "LINK LOST", level: "crit" },
  DISCONNECTED: { text: "DISCONNECTED", level: "" }, // closed by the operator
};
const SERVER_OFFLINE = { text: "GCS OFFLINE", level: "crit" };

const CLOCK = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export class TopBar {
  #fields;
  #linkState;
  #vehicle;
  #armed;
  #clock;

  constructor(root) {
    this.#fields = [...root.querySelectorAll("[data-key]")];
    this.#linkState = root.querySelector("#link-state");
    this.#vehicle = root.querySelector("#vehicle");
    this.#armed = root.querySelector("#armed");
    this.#clock = root.querySelector("#clock");
    for (const element of this.#fields) showField(element, WAITING);
    this.#showTime();
    setInterval(() => this.#showTime(), 1000);
  }

  update({ link, fields }) {
    this.#showLinkState(LINK_STATES[link.state] ?? { text: link.state, level: "" });
    this.#vehicle.textContent = link.vehicle
      ? `${link.vehicle.type} ${link.vehicle.sysid}/${link.vehicle.compid}`
      : "—";
    for (const element of this.#fields) {
      const field = fields[element.dataset.key];
      if (field) showField(element, field);
    }
    this.#armed.classList.toggle("is-armed", fields.armed?.value === true && !fields.armed.stale);
  }

  /** The browser lost the ground station: nothing on screen is current any more. */
  showOffline() {
    this.#showLinkState(SERVER_OFFLINE);
    for (const element of this.#fields) markStale(element);
    this.#armed.classList.remove("is-armed");
  }

  #showLinkState({ text, level }) {
    this.#linkState.textContent = text;
    this.#linkState.dataset.level = level;
  }

  #showTime() {
    this.#clock.textContent = CLOCK.format(new Date());
  }
}
