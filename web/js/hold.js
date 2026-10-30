// Press-and-hold buttons for commands to Gözcü: a command goes only once its button
// has been held down for a moment, so a stray click or tap sends nothing. While
// held, the button fills up (.hold in app.css); letting go early, or moving off
// it, cancels. Space and Enter work the same way when the button has the focus.

export const HOLD_MS = 600;
const KEYS = [" ", "Enter"];

/** Call onConfirm() after button has been held for holdMs. Returns cancel(), for when
 * the button is disabled while being held. */
export function holdToConfirm(button, onConfirm, holdMs = HOLD_MS) {
  let timer = null;
  const cancel = () => {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    button.classList.remove("is-holding");
  };
  const start = (event) => {
    if (button.disabled || timer !== null) return;
    event.preventDefault(); // no text selection, no scrolling on Space
    button.classList.add("is-holding");
    timer = setTimeout(() => {
      timer = null;
      button.classList.remove("is-holding");
      onConfirm();
    }, holdMs);
  };
  button.style.setProperty("--hold-ms", `${holdMs}ms`);
  button.addEventListener("pointerdown", (event) => {
    if (event.button === 0) start(event);
  });
  for (const type of ["pointerup", "pointerleave", "pointercancel", "blur"]) {
    button.addEventListener(type, cancel);
  }
  button.addEventListener("keydown", (event) => {
    if (KEYS.includes(event.key) && !event.repeat) start(event);
  });
  button.addEventListener("keyup", (event) => {
    if (KEYS.includes(event.key)) cancel();
  });
  button.addEventListener("contextmenu", (event) => event.preventDefault()); // long touch
  return cancel;
}
