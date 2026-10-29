// A small attitude indicator: the horizon from roll and pitch, a pitch ladder
// every 10° and a roll scale with a sky pointer, in the page's navy and black.
// It is redrawn only when a snapshot arrives, ten times a second at most.

const VISIBLE_PITCH_DEG = 25; // pitch angle at the top and bottom edge
const LADDER_DEG = 20; // ladder lines up to ±20°: further out they would cross the roll scale
const ROLL_MARKS_DEG = [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60];
const LONG_ROLL_MARKS_DEG = new Set([-60, -30, 0, 30, 60]);
const DEG = Math.PI / 180;

export class AttitudeIndicator {
  #canvas;
  #colours;
  #roll = null; // {value, text} of the roll and pitch fields
  #pitch = null;
  #stale = true;
  #size = { width: 0, height: 0, ratio: 1 };

  constructor(canvas) {
    this.#canvas = canvas;
    const style = getComputedStyle(document.documentElement);
    const read = (name) => style.getPropertyValue(name).trim();
    this.#colours = {
      sky: read("--line-strong"),
      ground: read("--black"),
      line: read("--text"),
      dim: read("--text-dim"),
      numbers: read("--font-num"),
      words: read("--font-ui"),
    };
    new ResizeObserver(() => this.#resize()).observe(canvas.parentElement);
  }

  update(fields) {
    const { roll, pitch } = fields;
    const known = roll?.value != null && pitch?.value != null;
    this.#roll = known ? roll : null;
    this.#pitch = known ? pitch : null;
    this.#stale = !known || roll.stale || pitch.stale;
    this.#draw();
  }

  /** The ground station is unreachable: whatever is shown may be old. */
  showOffline() {
    this.#stale = true;
    this.#draw();
  }

  #resize() {
    const { width, height } = this.#canvas.parentElement.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1; // sharp lines on high-density screens
    this.#canvas.width = Math.round(width * ratio);
    this.#canvas.height = Math.round(height * ratio);
    this.#canvas.style.width = `${width}px`;
    this.#canvas.style.height = `${height}px`;
    this.#size = { width, height, ratio };
    this.#draw();
  }

  #draw() {
    const { width: w, height: h, ratio } = this.#size;
    if (!w || !h) return;
    const c = this.#colours;
    const context = this.#canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const cx = w / 2;
    const cy = h / 2;
    const radius = h * 0.42;

    context.fillStyle = c.ground;
    context.fillRect(0, 0, w, h);
    if (this.#roll) {
      this.#drawHorizon(context, cx, cy, w, h);
      this.#drawReadouts(context, w, h);
    }
    this.#drawRollScale(context, cx, cy, radius);
    drawAircraft(context, cx, cy, w, c.line);

    if (this.#stale) {
      context.fillStyle = "rgba(0, 0, 0, 0.6)";
      context.fillRect(0, 0, w, h);
      context.fillStyle = c.dim;
      context.font = `600 12px ${c.words}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      // Below the wings, so the aircraft symbol does not strike the words through.
      context.fillText(this.#roll ? "ATTITUDE STALE" : "NO ATTITUDE", cx, cy + h * 0.27);
    }
  }

  #drawHorizon(context, cx, cy, w, h) {
    const c = this.#colours;
    const pxPerDeg = h / 2 / VISIBLE_PITCH_DEG;
    const reach = w + h + 90 * pxPerDeg; // covers the canvas at any roll and pitch
    context.save();
    context.translate(cx, cy);
    context.rotate(-this.#roll.value * DEG); // banking right turns the world left
    context.translate(0, this.#pitch.value * pxPerDeg); // nose up moves the horizon down
    context.fillStyle = c.sky;
    context.fillRect(-reach, -reach, 2 * reach, reach);
    context.strokeStyle = c.line;
    context.lineWidth = 1.5;
    segment(context, -reach, 0, reach, 0);

    context.lineWidth = 1;
    context.fillStyle = c.line;
    context.font = `10px ${c.numbers}`;
    context.textAlign = "left";
    context.textBaseline = "middle";
    for (let deg = -LADDER_DEG; deg <= LADDER_DEG; deg += 10) {
      if (deg === 0) continue;
      const y = -deg * pxPerDeg;
      const half = deg % 20 === 0 ? w * 0.14 : w * 0.08;
      segment(context, -half, y, half, y);
      context.fillText(String(Math.abs(deg)), half + 3, y);
    }
    context.restore();
  }

  #drawRollScale(context, cx, cy, radius) {
    context.strokeStyle = this.#colours.line;
    context.lineWidth = 1;
    for (const deg of ROLL_MARKS_DEG) {
      const length = LONG_ROLL_MARKS_DEG.has(deg) ? 8 : 5;
      const sin = Math.sin(deg * DEG);
      const cos = Math.cos(deg * DEG);
      segment(
        context,
        cx + radius * sin,
        cy - radius * cos,
        cx + (radius - length) * sin,
        cy - (radius - length) * cos,
      );
    }
    if (!this.#roll) return;
    // The sky pointer turns with the horizon and points at the bank on the fixed scale.
    context.save();
    context.translate(cx, cy);
    context.rotate(-this.#roll.value * DEG);
    context.fillStyle = this.#colours.line;
    context.beginPath();
    context.moveTo(0, -(radius - 9));
    context.lineTo(-5, -(radius - 17));
    context.lineTo(5, -(radius - 17));
    context.closePath();
    context.fill();
    context.restore();
  }

  #drawReadouts(context, w, h) {
    context.fillStyle = this.#colours.dim;
    context.font = `11px ${this.#colours.numbers}`;
    context.textBaseline = "bottom";
    context.textAlign = "left";
    context.fillText(`R ${this.#roll.text}°`, 6, h - 4);
    context.textAlign = "right";
    context.fillText(`P ${this.#pitch.text}°`, w - 6, h - 4);
  }
}

/** The fixed aircraft symbol: two wing bars and a centre dot, outlined in black. */
function drawAircraft(context, cx, cy, w, colour) {
  const outer = w * 0.22;
  const inner = w * 0.07;
  const wings = () => {
    context.beginPath();
    context.moveTo(cx - outer, cy);
    context.lineTo(cx - inner, cy);
    context.lineTo(cx - inner, cy + 5);
    context.moveTo(cx + outer, cy);
    context.lineTo(cx + inner, cy);
    context.lineTo(cx + inner, cy + 5);
  };
  context.lineCap = "square";
  wings();
  context.strokeStyle = "#000000";
  context.lineWidth = 5;
  context.stroke();
  wings();
  context.strokeStyle = colour;
  context.lineWidth = 2;
  context.stroke();
  for (const [radius, fill] of [
    [3.5, "#000000"],
    [2, colour],
  ]) {
    context.fillStyle = fill;
    context.beginPath();
    context.arc(cx, cy, radius, 0, 2 * Math.PI);
    context.fill();
  }
}

function segment(context, x1, y1, x2, y2) {
  context.beginPath();
  context.moveTo(x1, y1);
  context.lineTo(x2, y2);
  context.stroke();
}
