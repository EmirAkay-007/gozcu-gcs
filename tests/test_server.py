import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gcs.config import Config, RecordingConfig, VideoConfig
from gcs.server import Client, Hub, create_app, encode, hello_message, make_publish

EVENT = {"type": "event", "time": 1.0, "source": "link", "severity": "INFO", "text": "hello"}
SNAPSHOT = {
    "type": "snapshot",
    "seq": 1,
    "time": 1.0,
    "link": {"state": "CONNECTED", "connection": "tcp:127.0.0.1:5762", "vehicle": None},
    "fields": {},
}
VIDEO = {"type": "video", "time": 1.0, "state": "LIVE", "reason": None}
NOT_RECORDING = RecordingConfig(enabled=False)
VIDEO_ON = VideoConfig()


class FakeSource:
    """Stands in for MavlinkSource: the test publishes through it."""

    def __init__(self, config, publish):
        self.publish = publish
        self.running = False
        self.commands = []

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def gozcu_command(self, action, value, proposal=0):
        self.commands.append((action, value, proposal))

    def vehicle_command(self, action, args):
        self.commands.append(("vehicle", action, args))

    def set_connection(self, connection):
        self.commands.append(("connection", connection))


class FakeVideo:
    """Stands in for VideoSource: the test publishes frames through it."""

    def __init__(self, config, recording, publish, publish_frame):
        self.publish = publish
        self.publish_frame = publish_frame
        self.running = False
        self.recording_requests = []

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def set_recording(self, on):
        self.recording_requests.append(on)


def make_app(recording=NOT_RECORDING, video=VIDEO_ON):
    sources, videos = [], []

    def source_factory(config, publish):
        sources.append(FakeSource(config, publish))
        return sources[-1]

    def video_factory(*args):
        videos.append(FakeVideo(*args))
        return videos[-1]

    config = Config(recording=recording, video=video)
    return create_app(config, source_factory, video_factory), sources, videos


def wait_until(condition, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_a_browser_gets_the_field_list_then_live_data():
    app, sources, _ = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        airspeed = {"key": "airspeed", "label": "Airspeed", "unit": "m/s", "pinned": True}
        assert airspeed in hello["fields"]
        assert hello["map"]["tile_url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        assert "OpenStreetMap" in hello["map"]["attribution"]
        assert hello["video"] == {"hfov_deg": 64.7}
        assert hello["control"]["modes"][:3] == ["FBWA", "CRUISE", "LOITER"]
        assert "MANUAL" not in hello["control"]["modes"]
        assert hello["control"]["fly_alt_m"] == [20, 500]
        sources[0].publish(EVENT)
        assert ws.receive_json() == EVENT
        sources[0].publish(SNAPSHOT)
        assert ws.receive_json() == SNAPSHOT


def test_a_late_browser_gets_the_recent_events_and_the_newest_states():
    app, sources, videos = make_app()
    with TestClient(app) as client:
        sources[0].publish(EVENT)
        sources[0].publish(SNAPSHOT)
        videos[0].publish({**VIDEO, "state": "STARTING"})
        videos[0].publish(VIDEO)  # replaces the one before
        client.get("/")  # a round trip through the event loop, which delivers them first
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert ws.receive_json() == EVENT
            assert ws.receive_json() == SNAPSHOT
            assert ws.receive_json() == VIDEO


def test_the_sources_run_as_long_as_the_server():
    app, sources, videos = make_app()
    with TestClient(app):
        assert sources[0].running
        assert videos[0].running
    assert not sources[0].running
    assert not videos[0].running


def test_a_browser_learns_the_build_and_whether_its_files_changed_since(monkeypatch):
    from gcs import server

    builds = iter(["one", "one", "two"])  # at the start, then as each browser connects
    monkeypatch.setattr(server, "code_fingerprint", lambda: next(builds))
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
        with client.websocket_connect("/ws") as ws:
            second = ws.receive_json()  # the files were updated meanwhile
    assert (first["build"], first["updated"]) == ("one", False)
    assert (second["build"], second["updated"]) == ("one", True)


def test_the_fingerprint_follows_the_files_but_not_caches_or_editors(tmp_path):
    from gcs.server import code_fingerprint

    code, web = tmp_path / "gcs", tmp_path / "web"
    (code / "__pycache__").mkdir(parents=True)
    web.mkdir()
    (code / "server.py").write_text("x = 1\n", encoding="utf-8")
    (web / "app.js").write_text("// one\n", encoding="utf-8")
    before = code_fingerprint((code, web))
    (code / "__pycache__" / "server.cpython-312.pyc").write_bytes(b"cache")
    (web / ".app.js.swp").write_bytes(b"editor")
    assert code_fingerprint((code, web)) == before
    (web / "app.js").write_text("// two!\n", encoding="utf-8")
    assert code_fingerprint((code, web)) != before


def test_the_page_is_served_and_does_not_hide_the_websocket():
    app, _, _ = make_app()
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/js/transport.js")
    assert page.status_code == 200
    assert "Gözcü GCS" in page.text
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]  # ES modules need this type


def test_the_browser_checks_its_copy_of_every_file_before_using_it():
    app, _, _ = make_app()
    with TestClient(app) as client:
        first = client.get("/js/app.js")
        again = client.get("/js/app.js", headers={"if-none-match": first.headers["etag"]})
        page = client.get("/")
    assert first.headers["cache-control"] == "no-cache"
    assert again.status_code == 304  # unchanged: nothing sent again
    assert again.headers["cache-control"] == "no-cache"
    assert page.headers["cache-control"] == "no-cache"


def test_a_session_is_recorded_to_a_file(tmp_path):
    app, sources, videos = make_app(RecordingConfig(directory=str(tmp_path)))
    with TestClient(app):
        sources[0].publish(EVENT)
        sources[0].publish(SNAPSHOT)
        videos[0].publish(VIDEO)  # a status, not kept: what changes in it is also an event
    (path,) = tmp_path.glob("*.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines] == ["session", "event", "event", "values"]
    assert lines[1]["text"] == f"recording to {path}"
    assert lines[2] == EVENT


def test_the_station_runs_on_when_it_cannot_record(tmp_path):
    blocker = tmp_path / "a file"
    blocker.write_text("", encoding="utf-8")
    app, _, _ = make_app(RecordingConfig(directory=str(blocker / "logs")))  # cannot be created
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        warning = ws.receive_json()
    assert warning["severity"] == "WARNING"
    assert warning["text"].startswith("not recording:")


def test_video_frames_reach_every_browser_watching():
    app, _, videos = make_app()
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/video") as first,
        client.websocket_connect("/ws/video") as second,
    ):
        client.get("/")  # both have joined
        videos[0].publish_frame(b"\xff\xd8frame\xff\xd9")
        assert first.receive_bytes() == b"\xff\xd8frame\xff\xd9"
        assert second.receive_bytes() == b"\xff\xd8frame\xff\xd9"


def test_a_late_viewer_starts_with_the_newest_frame():
    app, _, videos = make_app()
    with TestClient(app) as client:
        videos[0].publish_frame(b"old")
        videos[0].publish_frame(b"new")
        client.get("/")
        with client.websocket_connect("/ws/video") as ws:
            assert ws.receive_bytes() == b"new"


def test_a_browser_can_start_and_stop_recording_the_video():
    app, _, videos = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "video_recording", "on": True})
        ws.send_json({"type": "video_recording", "on": False})
        wait_until(lambda: len(videos[0].recording_requests) == 2)
    assert videos[0].recording_requests == [True, False]


def test_a_gozcu_command_from_a_browser_reaches_the_source():
    app, sources, _ = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "gozcu_command", "action": "decide", "value": 1, "proposal": 7})
        ws.send_json({"type": "gozcu_command", "action": "mode", "value": 2})
        wait_until(lambda: len(sources[0].commands) == 2)
    assert sources[0].commands == [("decide", 1, 7), ("mode", 2, 0)]  # checked by the source


def test_a_vehicle_command_from_a_browser_reaches_the_source():
    app, sources, _ = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        fly_to = {"lat": 1.5, "lon": 2, "alt": 90}
        ws.send_json({"type": "vehicle_command", "action": "mode", "mode": "RTL"})
        ws.send_json({"type": "vehicle_command", "action": "fly_to", **fly_to})
        wait_until(lambda: len(sources[0].commands) == 2)
    assert sources[0].commands == [  # checked by the source
        ("vehicle", "mode", {"mode": "RTL"}),
        ("vehicle", "fly_to", fly_to),
    ]


def test_a_browser_can_change_or_close_the_link():
    app, sources, _ = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "connect", "connection": " /dev/ttyUSB0,57600 "})
        ws.send_json({"type": "connect", "connection": "/etc/passwd"})  # not a link
        refused = ws.receive_json()
        ws.send_json({"type": "disconnect"})
        wait_until(lambda: len(sources[0].commands) == 2)
    assert sources[0].commands == [("connection", "/dev/ttyUSB0,57600"), ("connection", None)]
    assert refused["severity"] == "WARNING"
    assert refused["text"].startswith("cannot connect: '/etc/passwd' is not a connection")


def test_a_browser_can_ask_which_serial_ports_there_are(monkeypatch):
    from gcs import server

    ports = [{"device": "/dev/ttyUSB0", "description": "CP2102"}]
    monkeypatch.setattr(server, "serial_ports", lambda: ports)
    app, _, _ = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "list_ports"})
        answer = ws.receive_json()
    assert answer["type"] == "ports"
    assert answer["ports"] == ports


@pytest.mark.parametrize("message", ["not json", "[1, 2]", '{"no": "type"}', '{"type": "launch"}'])
def test_a_request_the_station_does_not_know_is_ignored(message):
    app, _, videos = make_app()
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(message)
        ws.send_json({"type": "video_recording", "on": True})  # still listened to
        wait_until(lambda: videos[0].recording_requests == [True])


@pytest.mark.parametrize("path", ["/ws", "/ws/video"])
def test_only_the_stations_own_page_may_connect(path):
    app, _, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect(path, headers={"origin": "http://testserver"}):
            pass  # the page the station served
        with client.websocket_connect(path):
            pass  # a program, not a browser: no Origin
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(path, headers={"origin": "http://evil.example"}),
        ):
            pass
    assert refused.value.code == 1008


def test_video_turned_off_is_reported_and_cannot_be_recorded():
    app, _, videos = make_app(video=VideoConfig(enabled=False))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        status = ws.receive_json()
        ws.send_json({"type": "video_recording", "on": True})
        warning = ws.receive_json()
    assert videos == []
    assert status["type"] == "video"
    assert status["state"] == "OFF"
    assert warning["severity"] == "WARNING"
    assert warning["text"] == "cannot record video: video is turned off"


def test_a_slow_browser_skips_states_but_keeps_every_event():
    async def scenario():
        client = Client()
        client.push_state("snapshot", "s1")
        client.push_event("e1")
        client.push_state("video", "v1")
        client.push_state("snapshot", "s2")
        client.push_event("e2")
        return await client.next_batch()

    assert asyncio.run(scenario()) == ["e1", "e2", "s2", "v1"]


def test_nan_is_refused_before_it_reaches_a_browser():
    with pytest.raises(ValueError):
        encode({"value": float("nan")})


def test_publishing_after_shutdown_is_harmless():
    loop = asyncio.new_event_loop()
    loop.close()
    make_publish(loop, Hub(hello_message(Config())))(EVENT)  # must not raise
