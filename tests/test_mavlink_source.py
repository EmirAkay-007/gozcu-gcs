"""MavlinkSource end to end, against a stand-in autopilot on a local TCP port."""

import math
import socket
import threading
import time

import pytest

from gcs.config import MavlinkConfig
from gcs.fields import MESSAGE_RATES_HZ
from gcs.intervals import message_id
from gcs.mav import mavlink
from gcs.sources.mavlink_source import CONNECTED, CONNECTING, LINK_LOST, MavlinkSource, next_due
from gcs.state import TelemetryState


class SocketFile:
    """Lets a pymavlink MAVLink object write straight to a socket."""

    def __init__(self, sock):
        self.sock = sock

    def write(self, data):
        self.sock.sendall(data)


class FakeAutopilot:
    """Just enough of ArduPilot to drive MavlinkSource.

    Sends a HEARTBEAT (FBWA, disarmed) every second and ATTITUDE about ten times
    a second, and accepts every SET_MESSAGE_INTERVAL.
    """

    def __init__(self):
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(0.1)
        self.url = f"tcp:127.0.0.1:{self._server.getsockname()[1]}"
        self.requested = []  # message ids asked for, in order
        self.connections = 0
        self._hang_up = threading.Event()
        self._quiet = threading.Event()
        self._reboot = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def hang_up(self):
        """Close the current connection."""
        self._hang_up.set()

    def go_quiet(self):
        """Stop sending heartbeats but keep the connection."""
        self._quiet.set()

    def reboot(self):
        """Restart the vehicle clock from zero, as a rebooted autopilot does."""
        self._reboot.set()

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self._server.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            self.connections += 1
            self._hang_up.clear()
            with client:
                self._serve(client)

    def _serve(self, client):
        client.settimeout(0.1)
        mav = mavlink.MAVLink(SocketFile(client), srcSystem=1, srcComponent=1)
        parser = mavlink.MAVLink(None)
        boot_ms = 60_000  # up for a minute already
        next_heartbeat = 0.0
        while not (self._stop.is_set() or self._hang_up.is_set()):
            if self._reboot.is_set():
                self._reboot.clear()
                boot_ms = 0
            now = time.monotonic()
            try:
                if not self._quiet.is_set() and now >= next_heartbeat:
                    mav.heartbeat_send(
                        mavlink.MAV_TYPE_FIXED_WING,
                        mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                        mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                        5,  # FBWA
                        mavlink.MAV_STATE_STANDBY,
                    )
                    next_heartbeat = now + 1.0
                boot_ms += 100
                mav.attitude_send(boot_ms, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0)
                data = client.recv(4096)
                if not data:
                    return
                for msg in parser.parse_buffer(data) or []:
                    self._answer(mav, msg)
            except TimeoutError:
                continue
            except OSError:  # the GCS went away
                return

    def _answer(self, mav, msg):
        if msg.get_type() == "COMMAND_LONG" and msg.command == mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
            self.requested.append(int(msg.param1))
            mav.command_ack_send(
                msg.command,
                mavlink.MAV_RESULT_ACCEPTED,
                0,
                0,
                msg.get_srcSystem(),
                msg.get_srcComponent(),
            )


@pytest.fixture
def autopilot():
    fake = FakeAutopilot()
    yield fake
    fake.close()


@pytest.fixture
def run_source():
    """Start a MavlinkSource; returns the list it publishes into."""
    started = []

    def start(**settings):
        published = []
        source = MavlinkSource(MavlinkConfig(**settings), published.append)
        source.start()
        started.append(source)
        return published

    yield start
    for source in started:
        source.stop()


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def snapshots(published):
    return [item for item in list(published) if item["type"] == "snapshot"]


def events(published):
    return [item["text"] for item in list(published) if item["type"] == "event"]


def link_state(published):
    latest = snapshots(published)
    return latest[-1]["link"]["state"] if latest else None


ALL_REQUESTS = sorted(message_id(name) for name in MESSAGE_RATES_HZ)


def test_the_schedule_keeps_a_fixed_grid():
    assert next_due(10.0, 0.1, now=10.03) == pytest.approx(10.1)  # 30 ms late: stays on grid
    assert next_due(10.0, 0.1, now=10.35) == pytest.approx(10.45)  # a whole slot missed: no burst


def test_locks_on_requests_the_rates_and_reports_values(autopilot, run_source):
    published = run_source(connection=autopilot.url)
    wait_for(lambda: link_state(published) == CONNECTED)
    wait_for(lambda: len(autopilot.requested) == len(ALL_REQUESTS))
    wait_for(lambda: snapshots(published)[-1]["fields"]["roll"]["value"] is not None)

    latest = snapshots(published)[-1]
    assert latest["link"]["vehicle"] == {
        "sysid": 1,
        "compid": 1,
        "type": "FIXED_WING",
        "autopilot": "ARDUPILOTMEGA",
    }
    assert latest["fields"]["mode"]["value"] == "FBWA"
    assert latest["fields"]["armed"]["text"] == "DISARMED"
    assert latest["fields"]["roll"]["value"] == pytest.approx(math.degrees(0.1))
    assert sorted(autopilot.requested) == ALL_REQUESTS
    assert "vehicle 1/1: FIXED_WING, ARDUPILOTMEGA" in events(published)


def test_a_late_heartbeat_reports_link_lost_without_reconnecting(autopilot, run_source):
    published = run_source(connection=autopilot.url, link_lost_s=0.5)
    wait_for(lambda: link_state(published) == CONNECTED)
    autopilot.go_quiet()
    wait_for(lambda: link_state(published) == LINK_LOST)
    assert autopilot.connections == 1
    assert "no autopilot heartbeat for 0.5 s" in events(published)


def test_a_dropped_connection_is_opened_again(autopilot, run_source):
    published = run_source(connection=autopilot.url)
    wait_for(lambda: link_state(published) == CONNECTED)
    autopilot.hang_up()
    wait_for(lambda: autopilot.connections == 2)
    wait_for(lambda: link_state(published) == CONNECTED)
    assert any("closed by the other end" in text for text in events(published))


def test_rates_are_requested_again_after_a_vehicle_reboot(autopilot, run_source):
    published = run_source(connection=autopilot.url)
    wait_for(lambda: len(autopilot.requested) == len(ALL_REQUESTS))
    autopilot.reboot()
    wait_for(lambda: len(autopilot.requested) == 2 * len(ALL_REQUESTS))
    assert "vehicle rebooted; requesting message rates again" in events(published)


def test_a_message_that_cannot_be_handled_does_not_stop_the_telemetry(
    autopilot, run_source, monkeypatch
):
    update = TelemetryState.update

    def failing_update(self, msg, now):
        if msg.get_type() == "ATTITUDE":
            raise ValueError("boom")
        update(self, msg, now)

    monkeypatch.setattr(TelemetryState, "update", failing_update)
    published = run_source(connection=autopilot.url)
    wait_for(lambda: any("cannot handle ATTITUDE" in text for text in events(published)))
    time.sleep(0.5)  # several more ATTITUDE messages arrive and fail
    assert link_state(published) == CONNECTED
    assert sum("cannot handle ATTITUDE" in text for text in events(published)) == 1


def test_keeps_reporting_while_nothing_listens(run_source):
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]  # closed again: nobody listens there
    published = run_source(connection=f"tcp:127.0.0.1:{port}")
    wait_for(lambda: len(snapshots(published)) >= 15)  # 1.5 s: past the first retry
    assert link_state(published) == CONNECTING
    assert sum("cannot open" in text for text in events(published)) == 1  # not once per retry
