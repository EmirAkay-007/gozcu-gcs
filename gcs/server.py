"""The web side: serves the page and streams telemetry to every open browser.

The telemetry source runs in its own thread and publishes plain dicts; they are
handed to the event loop with call_soon_threadsafe and fanned out by the Hub.
A browser first gets a hello message with the field list, then the recent
events and the newest snapshot, then everything as it arrives. A slow browser
never holds up the others: it may skip snapshots, since only the newest one
matters, but it receives every event.
"""

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from gcs.config import Config
from gcs.fields import FIELDS
from gcs.recorder import Recorder, open_recorder, session_header
from gcs.sources.base import TelemetrySource
from gcs.sources.mavlink_source import MavlinkSource

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
RECENT_EVENTS = 200  # replayed to a browser that connects later
CLIENT_EVENT_BACKLOG = 1000  # events a stalled browser may fall behind before the oldest go

log = logging.getLogger("gcs.events")

Publish = Callable[[dict[str, Any]], None]
SourceFactory = Callable[..., TelemetrySource]


def encode(item: dict[str, Any]) -> str:
    """JSON text for the browser. NaN or Infinity would make JSON.parse reject the
    whole message, so they are refused here instead."""
    return json.dumps(item, allow_nan=False, ensure_ascii=False, separators=(",", ":"))


class Client:
    """What one browser still has to receive: every event, but only the newest snapshot."""

    def __init__(self) -> None:
        self._events: deque[str] = deque(maxlen=CLIENT_EVENT_BACKLOG)
        self._snapshot: str | None = None
        self._ready = asyncio.Event()

    def push_event(self, text: str) -> None:
        self._events.append(text)
        self._ready.set()

    def push_snapshot(self, text: str) -> None:
        self._snapshot = text
        self._ready.set()

    async def next_batch(self) -> list[str]:
        """Wait for news, then take all of it: pending events first, then the snapshot."""
        await self._ready.wait()
        self._ready.clear()
        batch = list(self._events)
        self._events.clear()
        if self._snapshot is not None:
            batch.append(self._snapshot)
            self._snapshot = None
        return batch


class Hub:
    """Fans out what the source publishes to every connected browser (event loop only)."""

    def __init__(self, hello: dict[str, Any]) -> None:
        self.hello = encode(hello)
        self._clients: set[Client] = set()
        self._recent_events: deque[str] = deque(maxlen=RECENT_EVENTS)
        self._snapshot: str | None = None

    def publish(self, item: dict[str, Any]) -> None:
        text = encode(item)  # once, however many browsers are connected
        if item["type"] == "snapshot":
            self._snapshot = text
            for client in self._clients:
                client.push_snapshot(text)
        else:
            self._recent_events.append(text)
            for client in self._clients:
                client.push_event(text)

    def join(self) -> Client:
        """A new browser, already holding the recent events and the newest snapshot."""
        client = Client()
        for text in self._recent_events:
            client.push_event(text)
        if self._snapshot is not None:
            client.push_snapshot(self._snapshot)
        self._clients.add(client)
        return client

    def leave(self, client: Client) -> None:
        self._clients.discard(client)


def hello_message() -> dict[str, Any]:
    """Sent first to every browser: the fields it can show, in registry order."""
    fields = [{"key": f.key, "label": f.label, "unit": f.unit, "pinned": f.pinned} for f in FIELDS]
    return {"type": "hello", "fields": fields}


def make_publish(loop: asyncio.AbstractEventLoop, hub: Hub) -> Publish:
    """The publish() handed to a source: safe to call from its thread; also logs events."""

    def publish(item: dict[str, Any]) -> None:
        if item["type"] == "event":
            log.info("%-8s %-7s %s", item["severity"], item["source"], item["text"])
        with suppress(RuntimeError):  # the event loop is closed: the server is shutting down
            loop.call_soon_threadsafe(hub.publish, item)

    return publish


def start_recording(config: Config, publish: Publish) -> tuple[Publish, Recorder | None]:
    """Add a recorder behind publish() when recording is on; tell the operator either way.

    Recording is secondary: if the file cannot be opened, the station still runs.
    """
    if not config.recording.enabled:
        return publish, None
    try:
        recorder = open_recorder(config.recording, session_header(config, time.time()))
    except OSError as exc:
        publish(server_event("WARNING", f"not recording: {exc}"))
        return publish, None

    def publish_and_record(item: dict[str, Any]) -> None:
        publish(item)
        recorder.record(item)

    publish_and_record(server_event("INFO", f"recording to {recorder.path}"))
    return publish_and_record, recorder


def server_event(severity: str, text: str) -> dict[str, Any]:
    return {
        "type": "event",
        "time": time.time(),
        "source": "server",
        "severity": severity,
        "text": text,
    }


def create_app(config: Config, source_factory: SourceFactory = MavlinkSource) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub = Hub(hello_message())
        app.state.hub = hub
        publish = make_publish(asyncio.get_running_loop(), hub)
        publish, recorder = start_recording(config, publish)
        source = source_factory(config.mavlink, publish)
        source.start()
        try:
            yield
        finally:
            await asyncio.to_thread(source.stop)  # no more items after this...
            if recorder is not None:
                await asyncio.to_thread(recorder.close)  # ...so nothing is lost here

    app = FastAPI(
        title="Gözcü GCS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.websocket("/ws")
    async def telemetry(websocket: WebSocket) -> None:
        await websocket.accept()
        hub: Hub = websocket.app.state.hub
        client = hub.join()
        sender = asyncio.create_task(_stream(websocket, hub.hello, client))
        try:
            # Nothing is expected from the browser yet (commands come in step 7), but
            # reading is how a closed tab is noticed.
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            hub.leave(client)

    # Mounted last: Starlette tries routes in order, and "/" would also match /ws.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


async def _stream(websocket: WebSocket, hello: str, client: Client) -> None:
    await websocket.send_text(hello)
    while True:
        for text in await client.next_batch():
            await websocket.send_text(text)
