"""MavlinkSource end to end, against a stand-in autopilot on a local TCP port."""

import math
import queue
import socket
import threading
import time

import pytest

from gcs.config import MavlinkConfig
from gcs.fields import MESSAGE_RATES_HZ
from gcs.intervals import message_id
from gcs.mav import mavlink
from gcs.sources import mavlink_source
from gcs.sources.mavlink_source import (
    CONNECTED,
    CONNECTING,
    DISCONNECTED,
    LINK_LOST,
    NO_VEHICLE,
    MavlinkSource,
    next_due,
)
from gcs.state import TelemetryState


class SocketFile:
    """Lets a pymavlink MAVLink object write straight to a socket."""

    def __init__(self, sock):
        self.sock = sock

    def write(self, data):
        self.sock.sendall(data)


def send_text(mav, severity, text):
    """Send a STATUSTEXT the way ArduPilot does: 50-byte chunks under one id when long."""
    raw = text.encode("utf-8")
    msg_id = 0 if len(raw) <= 50 else 9
    for seq in range(256):
        piece, raw = raw[:50], raw[50:]
        mav.statustext_send(severity, piece, msg_id, seq)
        if msg_id == 0 or len(piece) < 50:
            return


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
        self._texts = queue.SimpleQueue()  # STATUSTEXTs waiting to be sent
        self._gozcu_sends = queue.SimpleQueue()  # what to send as the Gözcü component
        self.gozcu_commands = []  # MAV_CMD_USER_1 commands received
        self.gozcu_answer = "accept"  # or "deny", "silent", "autopilot"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def as_gozcu(self, send):
        """Call send(mav) where mav sends as Gözcü, component 191 of this system."""
        self._gozcu_sends.put(send)

    def say(self, text, severity=mavlink.MAV_SEVERITY_WARNING):
        """Send a STATUSTEXT, in chunks if it is longer than one packet."""
        self._texts.put((severity, text))

    def hang_up(self):
        """Close the current connection."""
        self._hang_up.set()

    def go_quiet(self):
        """Stop sending heartbeats but keep the connection."""
        self._quiet.set()

    def resume(self):
        """Send heartbeats again."""
        self._quiet.clear()

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
        gozcu = mavlink.MAVLink(SocketFile(client), srcSystem=1, srcComponent=191)
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
                while not self._texts.empty():
                    send_text(mav, *self._texts.get())
                while not self._gozcu_sends.empty():
                    self._gozcu_sends.get()(gozcu)
                data = client.recv(4096)
                if not data:
                    return
                for msg in parser.parse_buffer(data) or []:
                    self._answer(mav, gozcu, msg)
            except TimeoutError:
                continue
            except OSError:  # the GCS went away
                return

    def _answer(self, mav, gozcu, msg):
        if msg.get_type() != "COMMAND_LONG":
            return
        if msg.command == mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
            self.requested.append(int(msg.param1))
            result, reason, answerer = mavlink.MAV_RESULT_ACCEPTED, 0, mav
        elif msg.command == mavlink.MAV_CMD_USER_1:
            self.gozcu_commands.append(msg)
            if self.gozcu_answer == "silent":
                return
            result, reason, answerer = {
                "accept": (mavlink.MAV_RESULT_ACCEPTED, 0, gozcu),
                "deny": (mavlink.MAV_RESULT_DENIED, 1, gozcu),  # switch on INHIBIT
                "autopilot": (mavlink.MAV_RESULT_UNSUPPORTED, 0, mav),  # no route to Gözcü
            }[self.gozcu_answer]
        else:
            return
        answerer.command_ack_send(
            msg.command, result, 0, reason, msg.get_srcSystem(), msg.get_srcComponent()
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


def test_gozcu_reports_reach_the_snapshot_apart_from_the_vehicle(autopilot, run_source):
    published = run_source(connection=autopilot.url)
    wait_for(lambda: link_state(published) == CONNECTED)

    def report(mav):
        mav.heartbeat_send(
            mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
            mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            mavlink.MAV_STATE_ACTIVE,
        )
        mav.named_value_int_send(5000, b"G_STATE", 1)
        mav.named_value_int_send(5100, b"T1_ID", 3)
        mav.named_value_int_send(5100, b"G_NTRK", 1)

    for _ in range(20):  # Gözcü's own sequence numbers must not look like lost vehicle packets
        autopilot.as_gozcu(report)
    wait_for(lambda: snapshots(published)[-1]["gozcu"]["tracks"]["items"])
    time.sleep(0.3)
    latest = snapshots(published)[-1]
    gozcu = latest["gozcu"]
    assert gozcu["present"]
    assert gozcu["fields"]["state"]["text"] == "ADVISORY"
    assert gozcu["tracks"]["items"][0]["id"] == 3
    assert latest["fields"]["link_loss"]["value"] == 0.0
    assert latest["fields"]["mode"]["value"] == "FBWA"  # the vehicle is still the autopilot
    assert "Gözcü heard" in events(published)


def gozcu_heartbeat(mav):
    mav.heartbeat_send(mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 4)


def command_state(published):
    command = snapshots(published)[-1]["gozcu"]["command"]
    return None if command is None else command["state"]


@pytest.mark.parametrize(
    ("answer", "state"),
    [("accept", "ACCEPTED"), ("deny", "DENIED"), ("autopilot", "NO ROUTE")],
)
def test_a_gozcu_command_goes_to_its_component_and_its_answer_is_shown(autopilot, answer, state):
    autopilot.gozcu_answer = answer
    published = []
    source = MavlinkSource(MavlinkConfig(connection=autopilot.url), published.append)
    source.start()
    try:
        wait_for(lambda: link_state(published) == CONNECTED)
        autopilot.as_gozcu(gozcu_heartbeat)
        wait_for(lambda: snapshots(published)[-1]["gozcu"]["present"])
        source.gozcu_command("mode", 2)
        wait_for(lambda: command_state(published) == state)
    finally:
        source.stop()
    (sent,) = autopilot.gozcu_commands
    assert (sent.target_system, sent.target_component) == (1, 191)
    assert (sent.param1, sent.param2, sent.param3, sent.confirmation) == (0, 2, 0, 0)


def test_a_gozcu_command_is_not_sent_before_gozcu_is_heard(autopilot):
    published = []
    source = MavlinkSource(MavlinkConfig(connection=autopilot.url), published.append)
    source.start()
    try:
        wait_for(lambda: link_state(published) == CONNECTED)
        source.gozcu_command("decide", 1, 4)
        wait_for(lambda: command_state(published) == "NOT SENT")
    finally:
        source.stop()
    assert autopilot.gozcu_commands == []
    assert "not sent: approve proposal 4 (Gözcü is silent)" in events(published)


def test_the_operator_can_change_and_close_the_link(autopilot):
    with socket.socket() as probe:  # a port nobody listens on
        probe.bind(("127.0.0.1", 0))
        dead = f"tcp:127.0.0.1:{probe.getsockname()[1]}"
    published = []
    source = MavlinkSource(MavlinkConfig(connection=dead), published.append)
    source.start()
    try:
        wait_for(lambda: any(text.startswith(f"cannot open {dead}") for text in events(published)))
        source.set_connection(autopilot.url)
        wait_for(lambda: link_state(published) == CONNECTED)
        assert snapshots(published)[-1]["link"]["connection"] == autopilot.url
        source.set_connection(None)
        wait_for(lambda: link_state(published) == DISCONNECTED)
        latest = snapshots(published)[-1]
        assert latest["link"]["connection"] is None
        assert latest["link"]["vehicle"] is None
        time.sleep(0.5)
        assert link_state(published) == DISCONNECTED  # and it does not try again by itself
        source.set_connection(autopilot.url)
        wait_for(lambda: link_state(published) == CONNECTED)
    finally:
        source.stop()
    assert autopilot.connections == 2
    texts = events(published)
    assert "link closed by the operator" in texts
    assert texts.count(f"connecting to {autopilot.url}") == 2


def test_a_serial_port_that_is_not_there_is_reported_and_tried_again(run_source):
    published = run_source(connection="/dev/ttyGOZCU_TEST_NONE,57600")  # a telemetry radio
    report = "cannot open /dev/ttyGOZCU_TEST_NONE,57600:"
    wait_for(lambda: any(text.startswith(report) for text in events(published)))
    count = len(snapshots(published))
    wait_for(lambda: len(snapshots(published)) > count + 3)  # the source keeps going
    assert link_state(published) == CONNECTING


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


def test_a_long_vehicle_message_reaches_the_log_in_one_piece(autopilot, run_source):
    text = "PreArm: Battery 1 below minimum arming voltage; check BATT_ARM_VOLT (güç)"
    published = run_source(connection=autopilot.url)
    wait_for(lambda: link_state(published) == CONNECTED)
    autopilot.say(text)
    wait_for(lambda: text in events(published))


def test_a_text_sent_before_the_first_heartbeat_is_not_lost(autopilot, run_source):
    autopilot.go_quiet()  # no heartbeat yet, so the vehicle is not known...
    published = run_source(connection=autopilot.url)
    wait_for(lambda: link_state(published) == NO_VEHICLE)
    autopilot.say("PreArm: Throttle below failsafe")
    time.sleep(0.3)  # ...while the text goes out
    autopilot.resume()
    wait_for(lambda: "PreArm: Throttle below failsafe" in events(published))


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


def test_a_port_that_stays_silent_is_explained(run_source, monkeypatch):
    monkeypatch.setattr(mavlink_source, "NO_VEHICLE_HINT_S", 0.3)
    # Listens but never accepts: the connection completes in the kernel's backlog and
    # then nothing arrives, just like SITL's port while another program holds it.
    with socket.create_server(("127.0.0.1", 0)) as server:
        published = run_source(connection=f"tcp:127.0.0.1:{server.getsockname()[1]}")
        wait_for(lambda: any("no MAVLink data" in text for text in events(published)))
        assert link_state(published) == NO_VEHICLE


def test_keeps_reporting_while_nothing_listens(run_source):
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]  # closed again: nobody listens there
    published = run_source(connection=f"tcp:127.0.0.1:{port}")
    wait_for(lambda: len(snapshots(published)) >= 15)  # 1.5 s: past the first retry
    assert link_state(published) == CONNECTING
    assert sum("cannot open" in text for text in events(published)) == 1  # not once per retry
