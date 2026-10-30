// The Gözcü panel: what the Gözcü component reports (docs/message_contract.md), as
// the station passes it on in snapshot.gozcu, and the commands the operator sends
// it. Every command is press-and-hold (hold.js), and only one is in flight at a
// time. Every colour comes with its text.

import { WAITING, formatAge, markStale, showField } from "./fields.js";
import { holdToConfirm } from "./hold.js";

// Amber for an advisory, red for avoid, green only once clear of conflict.
const THREAT_LEVELS = { CLEAR: "", ADVISORY: "warn", AVOID: "crit", "CLEAR OF CONFLICT": "ok" };
const SHOW_RESULT_S = 10; // how long the outcome of a command stays in view
// What became of a command that did not go through, for who it was meant for
const OUTCOMES = {
  DENIED: () => "refused",
  BUSY: (who) => `${who} is busy`,
  "NO ANSWER": (who) => `no answer from ${who}`,
  "NO ROUTE": () => "not delivered",
  "NOT SENT": () => "not sent",
};

/** Seconds left for the operator, counting down between updates; null if unknown. */
function secondsLeft(timeLeft) {
  return timeLeft.value === null ? null : Math.max(0, timeLeft.value / 1000 - timeLeft.age);
}

/** The threat badge as {text, level}. */
export function threatBadge(gozcu) {
  if (!gozcu?.heard) return { text: "NO DATA", level: "" };
  if (!gozcu.present) return { text: "GÖZCÜ SILENT", level: "crit" };
  const state = gozcu.fields.state;
  if (state.value === null || state.stale) return { text: "NO STATUS", level: "" };
  return { text: state.text, level: THREAT_LEVELS[state.text] ?? "" };
}

/** The manoeuvre line as {text, level}: the proposal, its decision and why. */
export function proposalLine(fields) {
  const { proposal, manoeuvre, decision, reason, time_left: timeLeft } = fields;
  if (!proposal.value || proposal.stale) return { text: "—", level: "" };
  let text = `#${proposal.value} ${manoeuvre.text}: ${decision.text}`;
  const left = secondsLeft(timeLeft);
  if (decision.text === "WAITING" && left !== null) {
    return { text: `${text}, ${left.toFixed(1)} s left`, level: "warn" };
  }
  if (decision.text === "REFUSED" && reason.value) text += ` (${reason.text})`;
  return { text, level: "" };
}

/** The cells of one track row. */
export function trackCells(track) {
  const fixed = (value, digits) => (value === null ? "—" : value.toFixed(digits));
  const signed = (value) => (value === null ? "—" : `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(1)}`);
  return [
    track.id === null ? "—" : String(track.id),
    track.class ?? "—",
    track.conf === null ? "—" : `${Math.round(track.conf * 100)} %`,
    `${signed(track.az)}/${signed(track.el)}`,
    fixed(track.rate, 1),
    fixed(track.px, 0),
    fixed(track.tau, 1),
  ];
}

/** The words next to a track's mark on the picture or the map, e.g. "TGT 3 drone τ 9.1". */
export function trackLabel(track, isTarget) {
  const tau = track.tau === null ? "" : ` τ ${track.tau.toFixed(1)}`;
  return `${isTarget ? "TGT " : ""}${track.id ?? "?"} ${track.class ?? ""}${tau}`.trim();
}

/** The tracks to mark, from the snapshot's gozcu part: {tracks, target, level}, or null
 * when there are none fresh enough to show. */
export function trackMarks(gozcu) {
  if (!gozcu.present || gozcu.tracks.stale || gozcu.tracks.items.length === 0) return null;
  const target = gozcu.fields.target;
  return {
    tracks: gozcu.tracks.items,
    target: target.stale ? null : target.value,
    level: threatBadge(gozcu).level,
  };
}

/** Which commands the operator can give now. */
export function controls(gozcu) {
  const command = gozcu.command;
  const pending = command?.state === "PENDING";
  const free = gozcu.present && !pending; // one command at a time
  const { proposal, decision, perception, time_left: timeLeft } = gozcu.fields;
  const waiting =
    gozcu.present &&
    !proposal.stale &&
    Boolean(proposal.value) &&
    decision.text === "WAITING" &&
    secondsLeft(timeLeft) !== 0;
  return {
    modes: free,
    pendingMode: pending && command.action === "mode" ? command.value : null,
    decide: waiting, // the Approve and Reject buttons are shown
    decideEnabled: waiting && free,
    proposal: proposal.value,
    perception: free && !perception.stale && perception.value !== null,
    perceptionRunning: perception.value === 1,
  };
}

/** The newest command to who and its outcome, as {text, level}; "" once it is old news. */
export function commandLine(command, who = "Gözcü") {
  if (!command) return { text: "", level: "" };
  if (command.state === "PENDING") return { text: `${command.text}: waiting for ${who}…`, level: "" };
  if (command.age > SHOW_RESULT_S) return { text: "", level: "" };
  if (command.state === "ACCEPTED") return { text: `${command.text}: accepted`, level: "" };
  const outcome = OUTCOMES[command.state]?.(who) ?? command.state.toLowerCase();
  const detail = command.detail ? ` (${command.detail})` : "";
  return { text: `${command.text}: ${outcome}${detail}`, level: "warn" };
}

export class GozcuPanel {
  #threat;
  #safeMode;
  #modes;
  #values;
  #tracks;
  #tracksAge;
  #proposal;
  #decision;
  #approve;
  #reject;
  #perception;
  #commandLine;
  #cancels = new Map(); // button -> cancel() of its hold
  #controls = null;

  /** send(command) passes {action, value, proposal} on to the station. */
  constructor(root, send) {
    const find = (selector) => root.querySelector(selector);
    this.#threat = find("#threat");
    this.#safeMode = find("#safe-mode");
    this.#modes = [...root.querySelectorAll("[data-mode]")];
    this.#values = [...root.querySelectorAll("[data-gozcu-key]")];
    this.#tracks = find("#tracks");
    this.#tracksAge = find("#tracks-age");
    this.#proposal = find("#proposal");
    this.#decision = find("#decision");
    this.#approve = find("#approve");
    this.#reject = find("#reject");
    this.#perception = find("#perception-toggle");
    this.#commandLine = find("#command-line");
    for (const element of this.#values) showField(element, WAITING);

    const hold = (button, command) =>
      this.#cancels.set(button, holdToConfirm(button, () => send(command())));
    for (const button of this.#modes) {
      hold(button, () => ({ action: "mode", value: Number(button.dataset.mode), proposal: 0 }));
    }
    // A decision names the proposal it is about, so a late one is refused, not misapplied.
    hold(this.#approve, () => ({ action: "decide", value: 1, proposal: this.#controls.proposal }));
    hold(this.#reject, () => ({ action: "decide", value: 0, proposal: this.#controls.proposal }));
    hold(this.#perception, () => ({
      action: "perception",
      value: this.#controls.perceptionRunning ? 0 : 1,
      proposal: 0,
    }));
  }

  update(gozcu) {
    const badge = threatBadge(gozcu);
    this.#threat.textContent = badge.text;
    this.#threat.dataset.level = badge.level;
    const fields = gozcu.fields;
    for (const element of this.#values) showField(element, fields[element.dataset.gozcuKey]);

    const faults = fields.faults;
    const safeMode = gozcu.present && !faults.stale && Boolean(faults.value);
    this.#safeMode.hidden = !safeMode;
    this.#safeMode.textContent = safeMode ? `SAFE MODE: ${faults.text}` : "";

    const current = !fields.target.stale && !gozcu.tracks.stale;
    this.#showTracks(gozcu.tracks, current ? fields.target.value : null, badge.level);
    const line = proposalLine(fields);
    this.#proposal.textContent = line.text;
    this.#proposal.dataset.level = line.level;
    this.#showControls(controls(gozcu), fields.mode);
    const outcome = commandLine(gozcu.command);
    this.#commandLine.textContent = outcome.text;
    this.#commandLine.dataset.level = outcome.level;
  }

  /** The ground station is unreachable: nothing shown here is current. */
  showOffline() {
    this.#threat.textContent = "NO DATA";
    this.#threat.dataset.level = "";
    this.#safeMode.hidden = true;
    for (const element of this.#values) markStale(element);
    this.#tracks.classList.add("is-stale");
    this.#proposal.dataset.level = "";
    this.#decision.hidden = true;
    for (const button of [...this.#modes, this.#approve, this.#reject, this.#perception]) {
      this.#enable(button, false);
      button.setAttribute("aria-pressed", "false");
    }
    this.#commandLine.textContent = "";
  }

  #showControls(state, mode) {
    this.#controls = state;
    for (const button of this.#modes) {
      const value = Number(button.dataset.mode);
      button.setAttribute("aria-pressed", String(!mode.stale && mode.value === value));
      button.classList.toggle("is-pending", state.pendingMode === value);
      this.#enable(button, state.modes);
    }
    this.#decision.hidden = !state.decide;
    this.#enable(this.#approve, state.decideEnabled);
    this.#enable(this.#reject, state.decideEnabled);
    this.#perception.textContent = state.perceptionRunning ? "PAUSE" : "RUN";
    this.#enable(this.#perception, state.perception);
  }

  #enable(button, enabled) {
    if (!enabled) this.#cancels.get(button)?.(); // a hold in progress ends with nothing sent
    button.disabled = !enabled;
  }

  #showTracks(tracks, target, level) {
    const rows = tracks.items.map((track) => {
      const row = document.createElement("tr");
      for (const text of trackCells(track)) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      if (target !== null && track.id === target) {
        // The track the threat state is about: marked in words as well as colour.
        row.classList.add("is-target");
        row.dataset.level = level;
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = "TGT";
        row.firstElementChild.append(tag);
      }
      return row;
    });
    if (rows.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.className = "empty";
      cell.colSpan = 7;
      cell.textContent = tracks.age === null ? "No tracks reported" : "No tracks";
      row.append(cell);
      rows.push(row);
    }
    this.#tracks.replaceChildren(...rows);
    this.#tracks.classList.toggle("is-stale", tracks.stale);
    this.#tracksAge.textContent =
      tracks.stale && tracks.age !== null ? `${formatAge(tracks.age)} old` : "";
  }
}
