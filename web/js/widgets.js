// The telemetry panel: one widget per field in the registry (gcs/fields.py).
//
// Pinned widgets stay in view; the rest fold away under "More". The registry
// says which are pinned at first; "Customize" changes that, and this browser
// remembers the choice. Why a value is missing is written under it, since a
// tooltip is easy to miss on a cockpit screen; a value never received at all
// just shows "—", or the list would repeat "not received" all the way down.

import { WAITING, markStale, showField } from "./fields.js";

const STORAGE_KEY = "gozcu.pinned";

function loadPinned() {
  try {
    const keys = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(keys) ? keys : null;
  } catch {
    return null; // storage blocked, e.g. in a private window
  }
}

function savePinned(keys) {
  try {
    if (keys === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // not remembered this time
  }
}

export class TelemetryPanel {
  #root;
  #pinnedList;
  #moreList;
  #moreCount;
  #customize;
  #reset;
  #fields = []; // registry order: {key, label, unit, pinned}
  #widgets = new Map(); // key -> {item, value, reason, checkbox}
  #pinned = new Set();

  constructor(root) {
    this.#root = root;
    this.#pinnedList = root.querySelector("#pinned");
    this.#moreList = root.querySelector("#more-list");
    this.#moreCount = root.querySelector("#more-count");
    this.#customize = root.querySelector("#customize");
    this.#reset = root.querySelector("#reset-pins");
    this.#customize.addEventListener("click", () => {
      this.#setCustomizing(!this.#root.classList.contains("is-customizing"));
    });
    this.#reset.addEventListener("click", () => {
      savePinned(null);
      this.#pinned = this.#defaultPins();
      this.#arrange();
    });
  }

  /** Build the widgets from the field list the server sends on every connect. */
  build(fields) {
    this.#fields = fields;
    const known = new Set(fields.map((field) => field.key));
    const saved = loadPinned();
    this.#pinned = saved ? new Set(saved.filter((key) => known.has(key))) : this.#defaultPins();
    this.#widgets = new Map(fields.map((field) => [field.key, this.#makeWidget(field)]));
    this.#arrange();
  }

  update(snapshot) {
    for (const [key, widget] of this.#widgets) {
      const field = snapshot.fields[key];
      if (!field) continue;
      showField(widget.value, field);
      const received = field.age !== null;
      widget.reason.textContent = field.value === null && received ? (field.reason ?? "") : "";
    }
  }

  /** The ground station is unreachable: every value is of unknown age now. */
  showOffline() {
    for (const widget of this.#widgets.values()) markStale(widget.value);
  }

  #defaultPins() {
    return new Set(this.#fields.filter((field) => field.pinned).map((field) => field.key));
  }

  #makeWidget(field) {
    const item = element("li", "widget");
    const value = element("span", "w-value");
    value.dataset.unit = field.unit;
    const reason = element("span", "w-reason");
    const pin = element("label", "w-pin");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.addEventListener("change", () => this.#setPinned(field.key, checkbox.checked));
    pin.append(checkbox, " pinned");
    item.append(element("span", "w-label", field.label), value, reason, pin);
    showField(value, WAITING);
    return { item, value, reason, checkbox };
  }

  #setPinned(key, pinned) {
    if (pinned) this.#pinned.add(key);
    else this.#pinned.delete(key);
    savePinned(this.#fields.map((field) => field.key).filter((k) => this.#pinned.has(k)));
    this.#arrange();
  }

  #arrange() {
    const pinned = [];
    const more = [];
    for (const field of this.#fields) {
      const widget = this.#widgets.get(field.key);
      widget.checkbox.checked = this.#pinned.has(field.key);
      (widget.checkbox.checked ? pinned : more).push(widget.item);
    }
    this.#pinnedList.replaceChildren(...pinned);
    this.#moreList.replaceChildren(...more);
    this.#moreCount.textContent = `(${more.length})`;
  }

  #setCustomizing(on) {
    this.#root.classList.toggle("is-customizing", on);
    this.#customize.setAttribute("aria-pressed", String(on));
    this.#customize.textContent = on ? "Done" : "Customize";
    this.#reset.hidden = !on;
  }
}

function element(tag, className, text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
