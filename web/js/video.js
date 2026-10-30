// The video panel: the camera picture, sent by the station as JPEG frames and drawn
// on a canvas, with what is wrong written over it whenever it is not live. REC
// records the camera's stream on the station, into its logs folder.
//
// Decoding a frame takes a moment; frames that arrive meanwhile replace each
// other, so a slow browser shows the newest picture rather than falling behind.
// Gözcü's tracks are marked on the picture as it is drawn ("Overlay" turns that
// off in this browser): nothing is drawn more often than frames arrive.

import { formatAge } from "./fields.js";
import { trackLabel, trackMarks } from "./gozcu.js";

const FROZEN_AFTER_S = 2; // live, but no new frame here for this long: say so
const OVERLAY_KEY = "gozcu.overlay";
const LEVEL_COLOURS = { warn: "--caution", crit: "--alarm" };

/**
 * Where a track seen at azimuth and elevation (degrees, from the camera's optical
 * axis, + right and up) lies in a picture of width × height from a camera with a
 * horizontal field of view of hfovDeg; null when outside the picture or unknown.
 * The inverse of the contract's AZ = atan((u − cx) / fx), EL = atan((cy − v) / fy),
 * with square pixels (fy = fx).
 */
export function trackPoint(azDeg, elDeg, hfovDeg, width, height) {
  if (azDeg === null || elDeg === null) return null;
  const rad = Math.PI / 180;
  const focal = width / 2 / Math.tan((hfovDeg / 2) * rad); // in pixels
  const x = width / 2 + focal * Math.tan(azDeg * rad);
  const y = height / 2 - focal * Math.tan(elDeg * rad);
  return x < 0 || x > width || y < 0 || y > height ? null : { x, y };
}

function loadOverlay() {
  try {
    return localStorage.getItem(OVERLAY_KEY) !== "off";
  } catch {
    return true; // storage blocked, e.g. in a private window
  }
}

/** Seconds as a recording time, e.g. "0:07", "12:34", "1:02:03". */
export function formatDuration(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const [hours, minutes, secs] = [Math.floor(whole / 3600), Math.floor(whole / 60) % 60, whole % 60];
  const twoDigits = (n) => String(n).padStart(2, "0");
  return hours ? `${hours}:${twoDigits(minutes)}:${twoDigits(secs)}` : `${minutes}:${twoDigits(secs)}`;
}

/**
 * What to write over the picture, as {title, detail}; null while it is live.
 * status is the station's latest "video" message (null before the first one),
 * frameAgeS the time since this browser last got a frame (null if never).
 */
export function pictureProblem(status, frameAgeS, offline) {
  if (offline) return { title: "GCS OFFLINE", detail: "this is the last picture received" };
  if (!status) return { title: "NO VIDEO", detail: "no data from the ground station yet" };
  if (status.state !== "LIVE") return { title: status.state, detail: status.reason ?? "" };
  if (frameAgeS === null) return { title: "STARTING", detail: "waiting for the first picture" };
  if (frameAgeS > FROZEN_AFTER_S) {
    return { title: "FROZEN", detail: `no new picture for ${formatAge(frameAgeS)}` };
  }
  return null;
}

export class VideoPanel {
  #canvas;
  #info;
  #record;
  #colors;
  #status = null;
  #offline = false;
  #bitmap = null; // the newest decoded frame
  #pending = null; // the newest frame not decoded yet
  #decoding = false;
  #frameTime = null; // performance.now() when the last frame arrived
  #overlay;
  #overlayOn = loadOverlay();
  #hfov = 64.7; // until the station's hello says otherwise
  #marks = null; // Gözcü's fresh tracks to mark: {tracks, target, level}

  /** onRecord(on) asks the station to start (true) or stop (false) recording. */
  constructor({ canvas, info, record, overlay, onRecord }) {
    this.#canvas = canvas;
    this.#info = info;
    this.#record = record;
    this.#overlay = overlay;
    const style = getComputedStyle(document.documentElement);
    const names = ["--black", "--text", "--text-dim", "--font-ui", "--font-num", "--accent"];
    this.#colors = Object.fromEntries(
      [...names, ...Object.values(LEVEL_COLOURS)].map((name) => [name, style.getPropertyValue(name)]),
    );
    record.addEventListener("click", () => onRecord(!this.#status?.recording));
    overlay.setAttribute("aria-pressed", String(this.#overlayOn));
    overlay.addEventListener("click", () => {
      this.#overlayOn = !this.#overlayOn;
      overlay.setAttribute("aria-pressed", String(this.#overlayOn));
      try {
        localStorage.setItem(OVERLAY_KEY, this.#overlayOn ? "on" : "off");
      } catch {
        // not remembered this time
      }
      this.#draw();
    });
    // Redraw whenever the panel changes size (window, swap, enlarge), and every
    // second, so the age of a frozen picture keeps counting.
    new ResizeObserver(() => this.#draw()).observe(canvas.parentElement);
    setInterval(() => this.#draw(), 1000);
    this.#showRecording();
  }

  /** The station's camera settings, from its hello message. */
  configure({ hfov_deg: hfovDeg }) {
    this.#hfov = hfovDeg;
  }

  /** Gözcü's part of a snapshot: its fresh tracks are marked on the next frame drawn. */
  setGozcu(gozcu) {
    this.#marks = trackMarks(gozcu);
  }

  /** A "video" status message from the station. */
  update(status) {
    this.#status = status;
    this.#offline = false;
    this.#showRecording();
    this.#showInfo();
    this.#draw();
  }

  /** A frame (JPEG blob) from the station. */
  showFrame(blob) {
    this.#pending = blob;
    this.#frameTime = performance.now();
    if (!this.#decoding) this.#decodeAll();
  }

  /** The ground station is unreachable: the picture shown may be old. */
  showOffline() {
    this.#offline = true;
    this.#showRecording();
    this.#showInfo();
    this.#draw();
  }

  async #decodeAll() {
    this.#decoding = true;
    while (this.#pending) {
      const blob = this.#pending;
      this.#pending = null;
      try {
        const bitmap = await createImageBitmap(blob);
        this.#bitmap?.close();
        this.#bitmap = bitmap;
        this.#showInfo();
        this.#draw();
      } catch {
        // a frame that does not decode is skipped; the next one replaces it
      }
    }
    this.#decoding = false;
  }

  #showRecording() {
    const recording = this.#status?.recording ?? null;
    const button = this.#record;
    button.disabled = this.#offline || !this.#status?.can_record;
    button.setAttribute("aria-pressed", String(recording !== null));
    button.textContent = recording ? `REC ${formatDuration(recording.seconds)}` : "REC";
    button.title = recording
      ? `Recording to ${recording.path}; click to stop`
      : "Record the camera stream as it arrives, into the logs folder";
  }

  #showInfo() {
    const status = this.#status;
    const parts = [];
    if (this.#bitmap) parts.push(`${this.#bitmap.width}×${this.#bitmap.height}`);
    if (status?.fps != null) parts.push(`${status.fps.toFixed(1)} fps`);
    if (status?.kbps != null) parts.push(`${(status.kbps / 1000).toFixed(2)} Mbit/s`);
    this.#info.textContent = status?.state === "LIVE" && !this.#offline ? parts.join(" · ") : "";
  }

  #draw() {
    const canvas = this.#canvas;
    const { width, height } = canvas.parentElement.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1; // sharp text on high-density screens
    const [pixelWidth, pixelHeight] = [Math.round(width * ratio), Math.round(height * ratio)];
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    }
    const context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = this.#colors["--black"];
    context.fillRect(0, 0, width, height);
    const bitmap = this.#bitmap;
    let picture = null; // where the picture lies on the canvas
    if (bitmap) {
      const scale = Math.min(width / bitmap.width, height / bitmap.height); // whole picture
      const [w, h] = [bitmap.width * scale, bitmap.height * scale];
      picture = { x: (width - w) / 2, y: (height - h) / 2, w, h };
      context.drawImage(bitmap, picture.x, picture.y, w, h);
    }

    const age = this.#frameTime === null ? null : (performance.now() - this.#frameTime) / 1000;
    const problem = pictureProblem(this.#status, age, this.#offline);
    if (!problem) {
      // Marks only on a live picture: on an old one they would point at the wrong place.
      if (picture && this.#overlayOn && this.#marks) this.#drawMarks(context, picture);
      return;
    }
    if (bitmap) {
      context.fillStyle = "rgb(0 0 0 / 0.6)"; // the old picture stays, dimmed
      context.fillRect(0, 0, width, height);
    }
    const font = this.#colors["--font-ui"];
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillStyle = this.#colors["--text"];
    context.font = `600 15px ${font}`;
    context.fillText(problem.title, width / 2, height / 2 - 10);
    context.fillStyle = this.#colors["--text-dim"];
    context.font = `13px ${font}`;
    context.fillText(problem.detail, width / 2, height / 2 + 12, width - 24);
  }

  /** A reticle and a label for each track, the target in its threat colour. */
  #drawMarks(context, picture) {
    const { tracks, target, level } = this.#marks;
    context.lineWidth = 2;
    context.font = `11px ${this.#colors["--font-num"]}`;
    context.textAlign = "center";
    context.textBaseline = "top";
    for (const track of tracks) {
      const point = trackPoint(track.az, track.el, this.#hfov, picture.w, picture.h);
      if (!point) continue;
      const [x, y] = [picture.x + point.x, picture.y + point.y];
      const isTarget = target !== null && track.id === target;
      const colour = this.#colors[(isTarget && LEVEL_COLOURS[level]) || "--accent"];
      context.strokeStyle = colour;
      context.beginPath();
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        context.moveTo(x + dx * 4, y + dy * 4); // a gap in the middle leaves the object visible
        context.lineTo(x + dx * 12, y + dy * 12);
      }
      context.stroke();
      context.strokeRect(x - 10, y - 10, 20, 20);
      const label = trackLabel(track, isTarget);
      const labelWidth = context.measureText(label).width + 6;
      context.fillStyle = "rgb(0 0 0 / 0.7)"; // readable on sky and ground alike
      context.fillRect(x - labelWidth / 2, y + 14, labelWidth, 14);
      context.fillStyle = colour;
      context.fillText(label, x, y + 15);
    }
  }
}
