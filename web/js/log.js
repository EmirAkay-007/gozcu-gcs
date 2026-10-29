// The event log along the bottom: newest first, severity as text and colour.

const MAX_ENTRIES = 500;
const LEVELS = { EMERGENCY: "crit", ALERT: "crit", CRITICAL: "crit", ERROR: "crit", WARNING: "warn" };
const TIME = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export class EventLog {
  #list;
  #count;

  constructor(list, count) {
    this.#list = list;
    this.#count = count;
  }

  clear() {
    this.#list.replaceChildren();
    this.#showCount();
  }

  /** Add an event as published by the server (see gcs/sources/base.py). */
  add(event) {
    const item = document.createElement("li");
    item.dataset.level = LEVELS[event.severity] ?? "";
    item.append(
      cell("time", TIME.format(new Date(event.time * 1000))),
      cell("sev", event.severity),
      cell("src", event.source),
      cell("text", event.text), // vehicle text: never parsed as HTML
    );
    this.#list.prepend(item);
    while (this.#list.childElementCount > MAX_ENTRIES) this.#list.lastElementChild.remove();
    this.#showCount();
  }

  #showCount() {
    const count = this.#list.childElementCount;
    this.#count.textContent = `${count} ${count === 1 ? "event" : "events"}`;
  }
}

function cell(className, text) {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  return element;
}
