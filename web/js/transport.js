// The only module that knows how this page talks to the ground station.
//
// Today that is a plain WebSocket to /ws carrying JSON messages with a "type"
// field ("hello", "snapshot", "event"). On the UNO Q, the App Lab WebUI brick
// uses Socket.IO, whose "event name + payload" maps onto that type field, so
// moving there should only change this file.

const RETRY_MIN_MS = 500;
const RETRY_MAX_MS = 5000;

function defaultUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws`;
}

export class Transport {
  #url;
  #handlers = new Map();
  #statusHandlers = [];
  #retryMs = RETRY_MIN_MS;

  constructor(url = defaultUrl()) {
    this.#url = url;
  }

  /** Call handler(message) for every message of the given type. */
  on(type, handler) {
    if (!this.#handlers.has(type)) this.#handlers.set(type, []);
    this.#handlers.get(type).push(handler);
    return this;
  }

  /** Call handler(connected) whenever the connection opens (true) or drops (false). */
  onStatus(handler) {
    this.#statusHandlers.push(handler);
    return this;
  }

  start() {
    this.#open();
    return this;
  }

  #open() {
    const socket = new WebSocket(this.#url);
    socket.onopen = () => {
      this.#retryMs = RETRY_MIN_MS;
      this.#setStatus(true);
    };
    socket.onmessage = (event) => this.#dispatch(JSON.parse(event.data));
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
    for (const handler of this.#statusHandlers) handler(connected);
  }
}
