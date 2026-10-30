// "Fly here": a right-click on the map picks a point, and a small panel over the map
// sends the vehicle there in GUIDED mode, at the altitude given, once its button has
// been held (MAV_CMD_DO_REPOSITION). Like every command to the autopilot it waits
// while the station holds them (vehicle.js). The point sent stays marked on the map
// while the vehicle flies to it in GUIDED.

import { metresBetween } from "./geo.js";
import { holdToConfirm } from "./hold.js";
import { controlLine, heldLine, vehicleControls } from "./vehicle.js";

const LOWEST_SUGGESTED_M = 50; // on the ground or flying low, at least this is suggested
const ANSWER_WAIT_MS = 2000; // a request reaches the snapshot well within this
const GUIDED_WAIT_MS = 3000; // after acceptance, GUIDED shows in the next heartbeat (1 Hz)
const NO_LINE = { text: "", level: "" };

/** The altitude offered when the panel opens: the vehicle's own above home, but not
 * less than LOWEST_SUGGESTED_M, within the altitudes the station accepts. */
export function suggestedAltitude(altRel, [low, high]) {
  const current = altRel?.value != null && !altRel.stale ? Math.round(altRel.value) : 0;
  return Math.min(high, Math.max(low, LOWEST_SUGGESTED_M, current));
}

/** The request for the point and the altitude typed, or {problem} with the altitude. */
export function flyToRequest(point, typed, [low, high]) {
  const alt = Number(typed);
  if (typed.trim() === "" || !Number.isFinite(alt)) return { problem: "Type the altitude" };
  if (alt < low || alt > high) return { problem: `Altitude: ${low} to ${high} m above home` };
  return { request: { action: "fly_to", lat: point.lat, lon: point.lon, alt: Math.round(alt) } };
}

/** "41.012346, 29.012346 · 480 m from the vehicle", without the distance when the
 * vehicle's position is not current. */
export function placeText(point, lat, lon) {
  const place = `${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`;
  if (lat?.value == null || lon?.value == null || lat.stale || lon.stale) return place;
  const metres = metresBetween(lat.value, lon.value, point.lat, point.lon);
  const distance = metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`;
  return `${place} · ${distance} from the vehicle`;
}

/** The panel's line: what became of the newest fly-to, the command in flight that
 * keeps the button closed, or why none can go; other outcomes are not its business. */
export function flyLine(control) {
  const command = control.command;
  if (command?.action === "fly_to" || command?.state === "PENDING") return controlLine(control);
  return control.blocked === null ? NO_LINE : heldLine(control.blocked);
}

/** Whether a point sent, {lat, lon, sentAt, acceptedAt}, is still where the vehicle
 * goes at nowMs. Notes when the station first reports it accepted. */
export function stillTarget(target, control, mode, nowMs) {
  const command = control.command;
  const ours =
    command?.action === "fly_to" && command.lat === target.lat && command.lon === target.lon;
  if (ours && command.state === "ACCEPTED") target.acceptedAt ??= nowMs;
  if (target.acceptedAt === null) {
    if (!ours) return nowMs - target.sentAt < ANSWER_WAIT_MS; // on its way to the station
    return command.state === "PENDING";
  }
  if (command?.action === "fly_to" && !ours) return false; // another point, from elsewhere
  return (mode?.value === "GUIDED" && !mode.stale) || nowMs - target.acceptedAt < GUIDED_WAIT_MS;
}

export class FlyHere {
  #map;
  #panel;
  #place;
  #altitude;
  #button;
  #line;
  #cancel;
  #bounds = [20, 500]; // until the station's hello says
  #point = null; // the point picked, while the panel is open
  #target = null; // the point sent, while the vehicle goes there
  #snapshot = null;

  /** map is the MapPanel; send(request) passes {action, ...arguments} on to the station. */
  constructor({ map, panel, place, altitude, button, close, line, send }) {
    this.#map = map;
    this.#panel = panel;
    this.#place = place;
    this.#altitude = altitude;
    this.#button = button;
    this.#line = line;
    map.onPick((point) => this.#open(point));
    this.#cancel = holdToConfirm(button, () => this.#send(send));
    altitude.addEventListener("input", () => this.#showControls());
    close.addEventListener("click", () => this.#close());
    panel.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation(); // the map stays enlarged (layout.js)
      this.#close();
    });
  }

  /** The altitudes accepted, from the station's hello. */
  configure({ fly_alt_m: bounds }) {
    this.#bounds = bounds;
    [this.#altitude.min, this.#altitude.max] = bounds;
  }

  update(snapshot) {
    this.#snapshot = snapshot;
    const target = this.#target;
    if (target && !stillTarget(target, snapshot.control, snapshot.fields.mode, performance.now())) {
      this.#target = null;
    }
    this.#showMark();
    if (this.#point) this.#showControls();
  }

  /** The ground station is unreachable: nothing can be sent, and where the vehicle
   * goes is no longer known. */
  showOffline() {
    this.#snapshot = null;
    this.#target = null;
    this.#showMark();
    if (this.#point) this.#showControls();
  }

  #open(point) {
    this.#point = point;
    const fields = this.#snapshot?.fields;
    this.#place.textContent = placeText(point, fields?.lat, fields?.lon);
    if (this.#panel.hidden) this.#altitude.value = suggestedAltitude(fields?.alt_rel, this.#bounds);
    this.#panel.hidden = false; // open already: another point, the altitude typed stays
    this.#altitude.focus();
    this.#altitude.select(); // typing replaces the suggestion
    this.#showControls();
    this.#showMark();
  }

  #send(send) {
    const { request } = flyToRequest(this.#point, this.#altitude.value, this.#bounds);
    if (!request) return;
    send(request);
    const { lat, lon, alt } = request;
    this.#target = { lat, lon, alt, sentAt: performance.now(), acceptedAt: null };
    this.#close();
  }

  #close() {
    this.#cancel();
    this.#point = null;
    this.#panel.hidden = true;
    this.#showMark();
  }

  #showControls() {
    const control = this.#snapshot?.control;
    const { problem } = flyToRequest(this.#point, this.#altitude.value, this.#bounds);
    const enabled = control !== undefined && vehicleControls(control).free && !problem;
    if (!enabled) this.#cancel(); // a hold in progress ends with nothing sent
    this.#button.disabled = !enabled;
    let line = control ? flyLine(control) : NO_LINE;
    if (problem) line = { text: problem, level: "warn" };
    this.#line.textContent = line.text;
    this.#line.dataset.level = line.level;
  }

  #showMark() {
    const target = this.#target;
    if (this.#point) this.#map.showTarget({ ...this.#point, text: "", sent: false });
    else if (target) this.#map.showTarget({ ...target, text: `GUIDED ${target.alt} m`, sent: true });
    else this.#map.showTarget(null);
  }
}
