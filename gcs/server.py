"""The web side: serves the page and streams telemetry and video to every open browser.

The telemetry and video sources run in threads of their own and publish plain
dicts; they are handed to the event loop with call_soon_threadsafe and fanned
out by the Hub. A browser first gets a hello message with the field list, then
the recent events and the newest snapshot and video status, then everything as
it arrives. A slow browser never holds up the others: it may skip snapshots and
statuses, since only the newest one matters, but it receives every event.

Video frames go over a WebSocket of their own (/ws/video), so a picture of tens
of kilobytes never delays telemetry; there too a slow browser skips frames.
"""

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
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
from gcs.video import VideoSource, video_status

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
RECENT_EVENTS = 200  # replayed to a browser that connects later
CLIENT_EVENT_BACKLOG = 1000  # events a stalled browser may fall behind before the oldest go
STATES = ("snapshot", "video")  # replaced by newer ones of their kind; the rest are events

log = logging.getLogger("gcs.events")
server_log = logging.getLogger("gcs.server")

Publish = Callable[[dict[str, Any]], None]
SourceFactory = Callable[..., TelemetrySource]
VideoFactory = Callable[..., VideoSource]


def encode(item: dict[str, Any]) -> str:
    """JSON text for the browser. NaN or Infinity would make JSON.parse reject the
    whole message, so they are refused here instead."""
    return json.dumps(item, allow_nan=False, ensure_ascii=False, separators=(",", ":"))


class Client:
    """What one browser still has to receive: every event, but of the snapshots and
    video statuses only the newest."""

    def __init__(self) -> None:
        self._events: deque[str] = deque(maxlen=CLIENT_EVENT_BACKLOG)
        self._states: dict[str, str] = {}  # type -> newest message of that type
        self._ready = asyncio.Event()

    def push_event(self, text: str) -> None:
        self._events.append(text)
        self._ready.set()

    def push_state(self, kind: str, text: str) -> None:
        self._states[kind] = text
        self._ready.set()

    async def next_batch(self) -> list[str]:
        """Wait for news, then take all of it: pending events first, then the states."""
        await self._ready.wait()
        self._ready.clear()
        batch = [*self._events, *self._states.values()]
        self._events.clear()
        self._states.clear()
        return batch


class Hub:
    """Fans out what the sources publish to every connected browser (event loop only)."""

    def __init__(self, hello: dict[str, Any]) -> None:
        self.hello = encode(hello)
        self._clients: set[Client] = set()
        self._recent_events: deque[str] = deque(maxlen=RECENT_EVENTS)
        self._states: dict[str, str] = {}

    def publish(self, item: dict[str, Any]) -> None:
        text = encode(item)  # once, however many browsers are connected
        kind = item["type"]
        if kind in STATES:
            self._states[kind] = text
            for client in self._clients:
                client.push_state(kind, text)
        else:
            self._recent_events.append(text)
            for client in self._clients:
                client.push_event(text)

    def join(self) -> Client:
        """A new browser, already holding the recent events and the newest states."""
        client = Client()
        for text in self._recent_events:
            client.push_event(text)
        for kind, text in self._states.items():
            client.push_state(kind, text)
        self._clients.add(client)
        return client

    def leave(self, client: Client) -> None:
        self._clients.discard(client)


class Viewer:
    """One browser's video: only the newest frame waits, so a slow browser skips frames."""

    def __init__(self) -> None:
        self._frame: bytes | None = None
        self._ready = asyncio.Event()

    def push(self, frame: bytes) -> None:
        self._frame = frame
        self._ready.set()

    async def next_frame(self) -> bytes:
        while self._frame is None:
            await self._ready.wait()
            self._ready.clear()
        frame, self._frame = self._frame, None
        return frame


class Frames:
    """Fans out the camera's frames to every browser watching (event loop only)."""

    def __init__(self) -> None:
        self._viewers: set[Viewer] = set()
        self._newest: bytes | None = None

    def publish(self, frame: bytes) -> None:
        self._newest = frame
        for viewer in self._viewers:
            viewer.push(frame)

    def join(self) -> Viewer:
        """A new viewer, starting with the newest frame, so the panel is not black meanwhile."""
        viewer = Viewer()
        if self._newest is not None:
            viewer.push(self._newest)
        self._viewers.add(viewer)
        return viewer

    def leave(self, viewer: Viewer) -> None:
        self._viewers.discard(viewer)


def hello_message(config: Config) -> dict[str, Any]:
    """Sent first to every browser: the fields it can show, in registry order, and the map."""
    fields = [{"key": f.key, "label": f.label, "unit": f.unit, "pinned": f.pinned} for f in FIELDS]
    map_settings = {
        "tile_url": config.map.tile_url,
        "attribution": config.map.attribution,
        "max_zoom": config.map.max_zoom,
    }
    return {"type": "hello", "fields": fields, "map": map_settings}


def make_publish(loop: asyncio.AbstractEventLoop, hub: Hub) -> Publish:
    """The publish() handed to a source: safe to call from its thread; also logs events."""

    def publish(item: dict[str, Any]) -> None:
        if item["type"] == "event":
            log.info("%-8s %-7s %s", item["severity"], item["source"], item["text"])
        with suppress(RuntimeError):  # the event loop is closed: the server is shutting down
            loop.call_soon_threadsafe(hub.publish, item)

    return publish


def make_frame_publish(loop: asyncio.AbstractEventLoop, frames: Frames) -> Callable[[bytes], None]:
    """The publish_frame() handed to the video source: safe to call from its thread."""

    def publish_frame(frame: bytes) -> None:
        with suppress(RuntimeError):  # the event loop is closed: the server is shutting down
            loop.call_soon_threadsafe(frames.publish, frame)

    return publish_frame


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


def create_app(
    config: Config,
    source_factory: SourceFactory = MavlinkSource,
    video_factory: VideoFactory = VideoSource,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        hub, frames = Hub(hello_message(config)), Frames()
        app.state.hub, app.state.frames = hub, frames
        publish = make_publish(loop, hub)
        publish, recorder = start_recording(config, publish)
        app.state.publish = publish
        source = source_factory(config.mavlink, publish)
        video = None
        if config.video.enabled:
            video = video_factory(
                config.video, config.recording, publish, make_frame_publish(loop, frames)
            )
        else:
            publish(video_status("OFF", "turned off in the settings ([video] enabled)"))
        app.state.video = video
        source.start()
        if video is not None:
            video.start()
        try:
            yield
        finally:
            await asyncio.to_thread(source.stop)  # no more items after these...
            if video is not None:
                await asyncio.to_thread(video.stop)
            if recorder is not None:
                await asyncio.to_thread(recorder.close)  # ...so nothing is lost here

    app = FastAPI(
        title="Gözcü GCS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.websocket("/ws")
    async def telemetry(websocket: WebSocket) -> None:
        if not _same_origin(websocket):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        hub: Hub = websocket.app.state.hub
        client = hub.join()

        async def handle(text: str) -> None:
            await _request(websocket.app, text)

        try:
            await _until_closed(websocket, _stream(websocket, hub.hello, client), handle)
        finally:
            hub.leave(client)

    @app.websocket("/ws/video")
    async def video_frames(websocket: WebSocket) -> None:
        if not _same_origin(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        frames: Frames = websocket.app.state.frames
        viewer = frames.join()
        try:
            await _until_closed(websocket, _send_frames(websocket, viewer))
        finally:
            frames.leave(viewer)

    # Mounted last: Starlette tries routes in order, and "/" would also match /ws.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _same_origin(websocket: WebSocket) -> bool:
    """Whether the page asking is the station's own. A browser lets any page open a
    WebSocket to any address, so a site visited in another tab could otherwise use the
    station; browsers say which page asks (Origin), other programs send none."""
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    return origin is None or origin in (f"http://{host}", f"https://{host}")


async def _until_closed(
    websocket: WebSocket,
    sending: Awaitable[None],
    handle: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Keep sending until the browser goes away; what it sends goes to handle().

    Reading is also how a closed tab is noticed, even where nothing is expected.
    """
    sender = asyncio.ensure_future(sending)
    try:
        while (message := await websocket.receive())["type"] != "websocket.disconnect":
            if handle is not None and message.get("text") is not None:
                await handle(message["text"])
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def _request(app: FastAPI, text: str) -> None:
    """A request from a browser. The only one so far: {"type": "video_recording", "on": bool}.

    The outcome reaches every browser through the events and the video status.
    """
    try:
        request = json.loads(text)
        kind = request["type"]
    except (ValueError, TypeError, KeyError):
        server_log.warning("ignored a malformed message from a browser: %.80s", text)
        return
    if kind != "video_recording":
        server_log.warning("ignored an unknown request from a browser: %.40s", kind)
    elif app.state.video is None:
        app.state.publish(server_event("WARNING", "cannot record video: video is turned off"))
    else:
        await asyncio.to_thread(app.state.video.set_recording, request.get("on") is True)


async def _stream(websocket: WebSocket, hello: str, client: Client) -> None:
    await websocket.send_text(hello)
    while True:
        for text in await client.next_batch():
            await websocket.send_text(text)


async def _send_frames(websocket: WebSocket, viewer: Viewer) -> None:
    while True:
        await websocket.send_bytes(await viewer.next_frame())
