// Panel arrangement: swap video and map, enlarge one of them, fold the event log.
// The arrangement is remembered by this browser.
//
// Swapping changes CSS order rather than moving elements, so a map inside never
// loses its container. Double-click enlarges from a panel's title bar, and from
// the video itself; on the map, double-click stays the map's zoom.

const STORAGE_KEY = "gozcu.layout";
const VIEWS = ["video", "map"];

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? {};
  } catch {
    return {}; // storage blocked, e.g. in a private window
  }
}

function save(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // not remembered this time
  }
}

export function setUpLayout(document) {
  const center = document.getElementById("center");
  const log = document.getElementById("log");
  const logToggle = document.getElementById("log-toggle");
  const saved = load(); // may come from an older version: take only what is valid
  const state = {
    swapped: saved.swapped === true,
    enlarged: VIEWS.includes(saved.enlarged) ? saved.enlarged : "",
    logFolded: saved.logFolded === true,
  };

  function apply() {
    center.classList.toggle("is-swapped", state.swapped);
    center.dataset.enlarged = state.enlarged;
    for (const button of center.querySelectorAll('[data-action="enlarge"]')) {
      const view = button.closest(".view").dataset.view;
      button.textContent = state.enlarged === view ? "Restore" : "Enlarge";
    }
    log.classList.toggle("is-folded", state.logFolded);
    logToggle.setAttribute("aria-expanded", String(!state.logFolded));
    save(state);
  }

  function toggleEnlarged(view) {
    state.enlarged = state.enlarged === view ? "" : view;
    apply();
  }

  center.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    if (button.dataset.action === "swap") {
      state.swapped = !state.swapped;
      apply();
    } else if (button.dataset.action === "enlarge") {
      toggleEnlarged(button.closest(".view").dataset.view);
    }
  });

  center.addEventListener("dblclick", (event) => {
    if (event.target.closest("button")) return;
    const view = event.target.closest(".view");
    const onTitle = event.target.closest(".view-head");
    if (view && (onTitle || view.dataset.view === "video")) toggleEnlarged(view.dataset.view);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.enlarged) toggleEnlarged(state.enlarged);
  });

  logToggle.addEventListener("click", () => {
    state.logFolded = !state.logFolded;
    apply();
  });

  apply();
}
