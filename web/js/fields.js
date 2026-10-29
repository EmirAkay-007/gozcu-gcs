// How one telemetry value from a snapshot is shown, the same way everywhere.

const TAGS = { warn: "WARN", crit: "CRIT" };

/** Seconds as a short age, e.g. "12 s", "3 min". */
export function formatAge(seconds) {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h`;
}

/**
 * Show a snapshot field in an element: the value and the element's data-unit,
 * a WARN/CRIT tag next to a coloured value, or grey with its age once stale.
 * A stale value shows no alert colour: its alert may no longer be true.
 * Why a value is missing ("no battery monitor") is in the tooltip; an element
 * marked data-optional is hidden instead, so "— —" never appears.
 */
export function showField(element, field) {
  if (!element.firstElementChild) {
    element.classList.add("field");
    element.append(span("v"), span("tag"), span("age"));
  }
  const [value, tag, age] = element.children;
  const unit = element.dataset.unit;
  value.textContent = unit && field.value !== null ? `${field.text} ${unit}` : field.text;
  element.classList.toggle("is-empty", field.value === null);
  const level = field.stale ? "" : (field.level ?? "");
  element.dataset.level = level;
  tag.textContent = TAGS[level] ?? "";
  element.classList.toggle("is-stale", field.stale);
  age.textContent = field.stale && field.age !== null ? formatAge(field.age) : "";
  element.title = field.reason ?? "";
}

/** Grey out a value whose freshness is unknown, e.g. while the server is unreachable. */
export function markStale(element) {
  element.classList.add("is-stale");
  element.dataset.level = "";
  const tag = element.children[1];
  if (tag) tag.textContent = "";
}

function span(className) {
  const element = document.createElement("span");
  element.className = className;
  return element;
}
