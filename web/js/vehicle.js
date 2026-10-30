// Commands to the autopilot from the telemetry panel: set the flight mode, arm or
// disarm ("fly here" is on the map, flyto.js). As with Gözcü's, every command is
// press-and-hold (hold.js) and only one is in flight at a time. The station holds
// them all while it cannot reach the autopilot, or while Gözcü avoids or waits for
// the operator; snapshot.control.blocked says why.

import { commandLine } from "./gozcu.js";
import { holdToConfirm } from "./hold.js";

const WHO = "the autopilot";

/** Which commands the operator can give the autopilot now. */
export function vehicleControls(control, armed) {
  const pending = control.command?.state === "PENDING";
  const free = control.blocked === null && !pending; // one command at a time
  const known = armed !== undefined && armed.value !== null && !armed.stale;
  return {
    free,
    arm: free && known && armed.value === false,
    disarm: free && known && armed.value === true,
    pendingAction: pending ? control.command.action : null,
  };
}

/** Why no command can be given now, as {text, level}. */
export function heldLine(blocked) {
  return { text: `Commands held: ${blocked}`, level: "" };
}

/** The line under the commands, as {text, level}: what became of the newest one, or
 * why none can be given now. */
export function controlLine(control) {
  const line = commandLine(control.command, WHO);
  if (control.blocked !== null && (line.text === "" || control.command.state === "ACCEPTED")) {
    return heldLine(control.blocked);
  }
  return line;
}

export class VehiclePanel {
  #mode;
  #set;
  #arm;
  #disarm;
  #line;
  #cancels = new Map(); // button -> cancel() of its hold
  #controls = null;

  /** send(request) passes {action, ...arguments} on to the station. */
  constructor({ mode, set, arm, disarm, line, send }) {
    this.#mode = mode;
    this.#set = set;
    this.#arm = arm;
    this.#disarm = disarm;
    this.#line = line;
    const hold = (button, request) =>
      this.#cancels.set(button, holdToConfirm(button, () => send(request())));
    hold(set, () => ({ action: "mode", mode: mode.value }));
    hold(arm, () => ({ action: "arm", arm: true }));
    hold(disarm, () => ({ action: "arm", arm: false })); // never forced: refused in flight
    mode.addEventListener("change", () => this.#showSet());
  }

  /** The modes offered, from the station's hello; a choice already made stays. */
  configure({ modes }) {
    const chosen = this.#mode.value;
    const options = modes.map((name) => new Option(name, name));
    this.#mode.replaceChildren(new Option("Mode…", ""), ...options);
    this.#mode.value = modes.includes(chosen) ? chosen : "";
    this.#showSet();
  }

  update({ control, fields }) {
    const state = vehicleControls(control, fields.armed);
    this.#controls = state;
    this.#showSet();
    this.#set.classList.toggle("is-pending", state.pendingAction === "mode");
    this.#enable(this.#arm, state.arm);
    this.#enable(this.#disarm, state.disarm);
    const line = controlLine(control);
    this.#line.textContent = line.text;
    this.#line.dataset.level = line.level;
  }

  /** The ground station is unreachable: no command can go. */
  showOffline() {
    this.#controls = null;
    for (const button of [this.#set, this.#arm, this.#disarm]) this.#enable(button, false);
    this.#set.classList.remove("is-pending");
    this.#line.textContent = "";
  }

  #showSet() {
    this.#enable(this.#set, this.#controls?.free === true && this.#mode.value !== "");
  }

  #enable(button, enabled) {
    if (!enabled) this.#cancels.get(button)?.(); // a hold in progress ends with nothing sent
    button.disabled = !enabled;
  }
}
