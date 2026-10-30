// The Gözcü panel: what the Gözcü component reports (docs/message_contract.md), as
// the station passes it on in snapshot.gozcu. Every colour comes with its text.

import { WAITING, formatAge, markStale, showField } from "./fields.js";

// Amber for an advisory, red for avoid, green only once clear of conflict.
const THREAT_LEVELS = { CLEAR: "", ADVISORY: "warn", AVOID: "crit", "CLEAR OF CONFLICT": "ok" };

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
  const { proposal, manoeuvre, decision, reason, time_left: left } = fields;
  if (!proposal.value || proposal.stale) return { text: "—", level: "" };
  let text = `#${proposal.value} ${manoeuvre.text}: ${decision.text}`;
  if (decision.text === "WAITING" && left.value !== null) {
    const seconds = Math.max(0, left.value / 1000 - left.age); // counts down between updates
    return { text: `${text}, ${seconds.toFixed(1)} s left`, level: "warn" };
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

export class GozcuPanel {
  #threat;
  #safeMode;
  #modes;
  #values;
  #tracks;
  #tracksAge;
  #proposal;

  constructor(root) {
    this.#threat = root.querySelector("#threat");
    this.#safeMode = root.querySelector("#safe-mode");
    this.#modes = [...root.querySelectorAll("[data-mode]")];
    this.#values = [...root.querySelectorAll("[data-gozcu-key]")];
    this.#tracks = root.querySelector("#tracks");
    this.#tracksAge = root.querySelector("#tracks-age");
    this.#proposal = root.querySelector("#proposal");
    for (const element of this.#values) showField(element, WAITING);
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

    for (const button of this.#modes) {
      const current = !fields.mode.stale && fields.mode.value === Number(button.dataset.mode);
      button.setAttribute("aria-pressed", String(current));
    }

    const current = !fields.target.stale && !gozcu.tracks.stale;
    this.#showTracks(gozcu.tracks, current ? fields.target.value : null, badge.level);
    const line = proposalLine(fields);
    this.#proposal.textContent = line.text;
    this.#proposal.dataset.level = line.level;
  }

  /** The ground station is unreachable: nothing shown here is current. */
  showOffline() {
    this.#threat.textContent = "NO DATA";
    this.#threat.dataset.level = "";
    this.#safeMode.hidden = true;
    for (const element of this.#values) markStale(element);
    for (const button of this.#modes) button.setAttribute("aria-pressed", "false");
    this.#tracks.classList.add("is-stale");
    this.#proposal.dataset.level = "";
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
