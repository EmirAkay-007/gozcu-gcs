"""MavlinkSource end to end, against a stand-in autopilot on a local TCP port
(tools/fake_autopilot.py)."""

import math
import socket
import time

import pytest
from fake_autopilot import FakeAutopilot

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


def control(published):
    return snapshots(published)[-1]["control"]


def vehicle_command_state(published):
    command = control(published)["command"]
    return None if command is None else (command["action"], command["state"])


def test_vehicle_commands_go_to_the_autopilot_as_arduplane_takes_them(autopilot):
    published = []
    source = MavlinkSource(MavlinkConfig(connection=autopilot.url), published.append)
    source.start()
    try:
        wait_for(lambda: link_state(published) == CONNECTED)
        assert control(published)["blocked"] is None
        source.vehicle_command("arm", {"arm": True})
        wait_for(lambda: vehicle_command_state(published) == ("arm", "ACCEPTED"))
        source.vehicle_command("mode", {"mode": "TAKEOFF"})
        wait_for(lambda: vehicle_command_state(published) == ("mode", "ACCEPTED"))
        source.vehicle_command("fly_to", {"lat": -35.3632621, "lon": 149.1652374, "alt": 120})
        wait_for(lambda: vehicle_command_state(published) == ("fly_to", "ACCEPTED"))
        wait_for(lambda: snapshots(published)[-1]["fields"]["mode"]["value"] == "GUIDED")
        assert snapshots(published)[-1]["fields"]["armed"]["value"] is True
    finally:
        source.stop()

    arm, mode, fly = autopilot.vehicle_commands
    for sent in (arm, mode, fly):
        assert (sent.target_system, sent.target_component) == (1, 1)
    assert (arm.command, arm.param1, arm.param2) == (mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1, 0)
    custom = mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
    assert (mode.command, mode.param1, mode.param2) == (mavlink.MAV_CMD_DO_SET_MODE, custom, 13)
    assert fly.get_type() == "COMMAND_INT"
    assert (fly.command, fly.frame) == (
        mavlink.MAV_CMD_DO_REPOSITION,
        mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
    )
    assert fly.param2 == mavlink.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE
    assert (fly.x, fly.y, fly.z) == (-353632621, 1491652374, 120)
    assert "the autopilot accepted: mode TAKEOFF" in events(published)


def test_vehicle_commands_are_held_while_gozcu_avoids(autopilot):
    def avoid(mav):
        gozcu_heartbeat(mav)
        mav.named_value_int_send(5000, b"G_STATE", 2)

    published = []
    source = MavlinkSource(MavlinkConfig(connection=autopilot.url), published.append)
    source.start()
    try:
        wait_for(lambda: link_state(published) == CONNECTED)
        autopilot.as_gozcu(avoid)
        wait_for(lambda: control(published)["blocked"] == "Gözcü is avoiding")
        source.vehicle_command("mode", {"mode": "RTL"})
        wait_for(lambda: vehicle_command_state(published) == ("mode", "NOT SENT"))
    finally:
        source.stop()
    assert autopilot.vehicle_commands == []
    assert "not sent: mode RTL (Gözcü is avoiding)" in events(published)


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
