// The event log along the bottom: newest first, severity as text and colour,
// filtered by lowest severity, by source and by text.

const MAX_ENTRIES = 500;
// MAV_SEVERITY order: 0 is the most severe.
const RANKS = {
  EMERGENCY: 0,
  ALERT: 1,
  CRITICAL: 2,
  ERROR: 3,
  WARNING: 4,
  NOTICE: 5,
  INFO: 6,
  DEBUG: 7,
};
const TIME = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function level(rank) {
  if (rank <= RANKS.ERROR) return "crit";
  return rank === RANKS.WARNING ? "warn" : "";
}

export class EventLog {
  #list;
  #count;
  #sourceSelect;
  #sources = new Set();
  #filter = { lowestRank: RANKS.DEBUG, source: "", text: "" };

  constructor({ list, count, severity, source, search }) {
    this.#list = list;
    this.#count = count;
    this.#sourceSelect = source;
    severity.addEventListener("change", () => {
      this.#filter.lowestRank = Number(severity.value);
      this.#applyFilter();
    });
    source.addEventListener("change", () => {
      this.#filter.source = source.value;
      this.#applyFilter();
    });
    search.addEventListener("input", () => {
      this.#filter.text = search.value.trim().toLowerCase();
      this.#applyFilter();
    });
  }

  clear() {
    this.#list.replaceChildren();
    this.#showCount();
  }

  /** Add an event as published by the server (see gcs/sources/base.py). */
  add(event) {
    const rank = RANKS[event.severity] ?? RANKS.DEBUG;
    const item = document.createElement("li");
    item.dataset.level = level(rank);
    item.dataset.rank = String(rank);
    item.dataset.source = event.source;
    item.dataset.search = `${event.severity} ${event.source} ${event.text}`.toLowerCase();
    item.append(
      cell("time", TIME.format(new Date(event.time * 1000))),
      cell("sev", event.severity),
      cell("src", event.source),
      cell("text", event.text), // vehicle text: never parsed as HTML
    );
    item.hidden = !this.#matches(item);
    this.#list.prepend(item);
    while (this.#list.childElementCount > MAX_ENTRIES) this.#list.lastElementChild.remove();
    this.#addSource(event.source);
    this.#showCount();
  }

  #matches(item) {
    const { lowestRank, source, text } = this.#filter;
    return (
      Number(item.dataset.rank) <= lowestRank &&
      (!source || item.dataset.source === source) &&
      (!text || item.dataset.search.includes(text))
    );
  }

  #applyFilter() {
    for (const item of this.#list.children) item.hidden = !this.#matches(item);
    this.#showCount();
  }

  #addSource(source) {
    if (this.#sources.has(source)) return;
    this.#sources.add(source);
    this.#sourceSelect.append(new Option(source, source));
  }

  #showCount() {
    const total = this.#list.childElementCount;
    let shown = 0;
    for (const item of this.#list.children) if (!item.hidden) shown += 1;
    const noun = total === 1 ? "event" : "events";
    this.#count.textContent = shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`;
  }
}

function cell(className, text) {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  return element;
}
