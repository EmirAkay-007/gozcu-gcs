import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from gcs.config import Config, RecordingConfig
from gcs.server import Client, Hub, create_app, encode, hello_message, make_publish

EVENT = {"type": "event", "time": 1.0, "source": "link", "severity": "INFO", "text": "hello"}
SNAPSHOT = {
    "type": "snapshot",
    "seq": 1,
    "time": 1.0,
    "link": {"state": "CONNECTED", "connection": "tcp:127.0.0.1:5762", "vehicle": None},
    "fields": {},
}
NOT_RECORDING = RecordingConfig(enabled=False)


class FakeSource:
    """Stands in for MavlinkSource: the test publishes through it."""

    def __init__(self, config, publish):
        self.publish = publish
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


def make_app(recording=NOT_RECORDING):
    sources = []

    def factory(config, publish):
        sources.append(FakeSource(config, publish))
        return sources[-1]

    return create_app(Config(recording=recording), source_factory=factory), sources


def test_a_browser_gets_the_field_list_then_live_data():
    app, sources = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        airspeed = {"key": "airspeed", "label": "Airspeed", "unit": "m/s", "pinned": True}
        assert airspeed in hello["fields"]
        assert hello["map"]["tile_url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        assert "OpenStreetMap" in hello["map"]["attribution"]
        sources[0].publish(EVENT)
        assert ws.receive_json() == EVENT
        sources[0].publish(SNAPSHOT)
        assert ws.receive_json() == SNAPSHOT


def test_a_late_browser_gets_the_recent_events_and_the_newest_snapshot():
    app, sources = make_app()
    with TestClient(app) as client:
        sources[0].publish(EVENT)
        sources[0].publish(SNAPSHOT)
        client.get("/")  # a round trip through the event loop, which delivers both first
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert ws.receive_json() == EVENT
            assert ws.receive_json() == SNAPSHOT


def test_the_source_runs_as_long_as_the_server():
    app, sources = make_app()
    with TestClient(app):
        assert sources[0].running
    assert not sources[0].running


def test_the_page_is_served_and_does_not_hide_the_websocket():
    app, _ = make_app()
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/js/transport.js")
    assert page.status_code == 200
    assert "Gözcü GCS" in page.text
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]  # ES modules need this type


def test_a_session_is_recorded_to_a_file(tmp_path):
    app, sources = make_app(RecordingConfig(directory=str(tmp_path)))
    with TestClient(app):
        sources[0].publish(EVENT)
        sources[0].publish(SNAPSHOT)
    (path,) = tmp_path.glob("*.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines] == ["session", "event", "event", "values"]
    assert lines[1]["text"] == f"recording to {path}"
    assert lines[2] == EVENT


def test_the_station_runs_on_when_it_cannot_record(tmp_path):
    blocker = tmp_path / "a file"
    blocker.write_text("", encoding="utf-8")
    app, _ = make_app(RecordingConfig(directory=str(blocker / "logs")))  # cannot be created
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        warning = ws.receive_json()
    assert warning["severity"] == "WARNING"
    assert warning["text"].startswith("not recording:")


def test_a_slow_browser_skips_snapshots_but_keeps_every_event():
    async def scenario():
        client = Client()
        for push, text in [
            (client.push_snapshot, "s1"),
            (client.push_event, "e1"),
            (client.push_snapshot, "s2"),
            (client.push_event, "e2"),
        ]:
            push(text)
        return await client.next_batch()

    assert asyncio.run(scenario()) == ["e1", "e2", "s2"]


def test_nan_is_refused_before_it_reaches_a_browser():
    with pytest.raises(ValueError):
        encode({"value": float("nan")})


def test_publishing_after_shutdown_is_harmless():
    loop = asyncio.new_event_loop()
    loop.close()
    make_publish(loop, Hub(hello_message(Config())))(EVENT)  # must not raise
