// The only module that knows how this page talks to the ground station.
//
// Today that is plain WebSockets: /ws carries JSON messages with a "type" field
// ("hello", "snapshot", "video", "event") both ways, and /ws/video carries the
// camera's frames as binary JPEG messages. On the UNO Q, the App Lab WebUI brick
// uses Socket.IO, whose "event name + payload" maps onto that type field, so
// moving there should only change this file.

const RETRY_MIN_MS = 500;
const RETRY_MAX_MS = 5000;

/** The WebSocket address of a path on the station that served this page. */
export function stationUrl(path) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}`;
}

export class Transport {
  #url;
  #socket = null;
  #handlers = new Map();
  #binaryHandlers = [];
  #statusHandlers = [];
  #retryMs = RETRY_MIN_MS;
  #connected = null; // unknown until the first attempt succeeds or fails

  constructor(url = stationUrl("/ws")) {
    this.#url = url;
  }

  /** Call handler(message) for every JSON message of the given type. */
  on(type, handler) {
    if (!this.#handlers.has(type)) this.#handlers.set(type, []);
    this.#handlers.get(type).push(handler);
    return this;
  }

  /** Call handler(blob) for every binary message. */
  onBinary(handler) {
    this.#binaryHandlers.push(handler);
    return this;
  }

  /** Call handler(connected) when the connection opens (true) or drops (false); a failed
   * retry while already disconnected is not reported again. */
  onStatus(handler) {
    this.#statusHandlers.push(handler);
    return this;
  }

  /** Send a request to the station; false when not connected, since it is not queued. */
  send(message) {
    if (this.#socket?.readyState !== WebSocket.OPEN) return false;
    this.#socket.send(JSON.stringify(message));
    return true;
  }

  start() {
    this.#open();
    return this;
  }

  #open() {
    const socket = new WebSocket(this.#url);
    socket.binaryType = "blob";
    this.#socket = socket;
    socket.onopen = () => {
      this.#retryMs = RETRY_MIN_MS;
      this.#setStatus(true);
    };
    socket.onmessage = (event) => {
      if (typeof event.data === "string") this.#dispatch(JSON.parse(event.data));
      else for (const handler of this.#binaryHandlers) handler(event.data);
    };
    socket.onclose = () => {
      // Also follows a failed attempt, so this is the one place that retries.
      this.#setStatus(false);
      setTimeout(() => this.#open(), this.#retryMs);
      this.#retryMs = Math.min(this.#retryMs * 2, RETRY_MAX_MS);
    };
  }

  #dispatch(message) {
    for (const handler of this.#handlers.get(message.type) ?? []) handler(message);
  }

  #setStatus(connected) {
    if (connected === this.#connected) return;
    this.#connected = connected;
    for (const handler of this.#statusHandlers) handler(connected);
  }
}
