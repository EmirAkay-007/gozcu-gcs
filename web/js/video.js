// The video panel: the camera picture, sent by the station as JPEG frames and drawn
// on a canvas, with what is wrong written over it whenever it is not live. REC
// records the camera's stream on the station, into its logs folder.
//
// Decoding a frame takes a moment; frames that arrive meanwhile replace each
// other, so a slow browser shows the newest picture rather than falling behind.

import { formatAge } from "./fields.js";

const FROZEN_AFTER_S = 2; // live, but no new frame here for this long: say so

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

  /** onRecord(on) asks the station to start (true) or stop (false) recording. */
  constructor({ canvas, info, record, onRecord }) {
    this.#canvas = canvas;
    this.#info = info;
    this.#record = record;
    const style = getComputedStyle(document.documentElement);
    this.#colors = Object.fromEntries(
      ["--black", "--text", "--text-dim", "--font-ui"].map((name) => [name, style.getPropertyValue(name)]),
    );
    record.addEventListener("click", () => onRecord(!this.#status?.recording));
    // Redraw whenever the panel changes size (window, swap, enlarge), and every
    // second, so the age of a frozen picture keeps counting.
    new ResizeObserver(() => this.#draw()).observe(canvas.parentElement);
    setInterval(() => this.#draw(), 1000);
    this.#showRecording();
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
    if (bitmap) {
      const scale = Math.min(width / bitmap.width, height / bitmap.height); // whole picture
      const [w, h] = [bitmap.width * scale, bitmap.height * scale];
      context.drawImage(bitmap, (width - w) / 2, (height - h) / 2, w, h);
    }

    const age = this.#frameTime === null ? null : (performance.now() - this.#frameTime) / 1000;
    const problem = pictureProblem(this.#status, age, this.#offline);
    if (!problem) return;
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
}
