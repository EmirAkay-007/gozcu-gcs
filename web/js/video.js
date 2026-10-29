// The video panel. Until the stream arrives (roadmap step 6) it shows a message
// drawn on the canvas, so the panel never shows a broken image.

export class VideoPanel {
  #canvas;
  #message = "NO VIDEO";

  constructor(canvas) {
    this.#canvas = canvas;
    // Redraw whenever the panel changes size: window resize, swap, enlarge.
    new ResizeObserver(() => this.#draw()).observe(canvas.parentElement);
  }

  #draw() {
    const canvas = this.#canvas;
    const { width, height } = canvas.parentElement.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1; // sharp text on high-density screens
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const style = getComputedStyle(document.documentElement);
    const context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = style.getPropertyValue("--black");
    context.fillRect(0, 0, width, height);
    context.fillStyle = style.getPropertyValue("--text-stale");
    context.font = `600 15px ${style.getPropertyValue("--font-ui")}`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(this.#message, width / 2, height / 2);
  }
}
