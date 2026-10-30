"""tools/fake_gozcu.py against the station's own code. Both are written from
docs/message_contract.md, so what the fake sends must be what the station
understands, and its answers must be what the station's commands expect."""

from types import SimpleNamespace

import pytest
from fake_gozcu import AUTO, CONFIRM, WARN, FakeGozcu, run

from gcs.gozcu import GozcuState
from gcs.gozcu_commands import ACTIONS, GozcuCommands
from gcs.mav import mavlink


class Station:
    """The station's side: GozcuState, fed with whatever the fake sends."""

    def __init__(self):
        self.events = []
        self.state = GozcuState(lambda severity, text: self.events.append((severity, text)))
        self.now = 0.0
        self.received = []  # (time, message)
        self.mav = mavlink.MAVLink(self, srcSystem=1, srcComponent=191)  # the fake's link
        self._parser = mavlink.MAVLink(None)

    def write(self, data):
        for msg in self._parser.parse_buffer(data) or []:
            self.received.append((self.now, msg))
            self.state.handle(msg, self.now)

    def play(self, fake, until, step=0.05):
        """Run the fake from where it was to until, as run() would; the station's view then."""
        while self.now + step <= until + 1e-9:
            self.now = round(self.now + step, 6)
            fake.step(self.mav, self.now)
            self.state.check(self.now)
        return self.state.snapshot(self.now)


def command(action, value, proposal=0):
    return SimpleNamespace(param1=float(action), param2=float(value), param3=float(proposal))


def fields(snapshot, *keys):
    return tuple(snapshot["fields"][key]["text"] for key in keys)


@pytest.fixture
def station():
    return Station()


def test_everything_it_sends_is_in_the_contract_the_station_knows(station):
    fake = FakeGozcu("cycle", start=0.0)
    station.play(fake, 120.0)  # every scenario, twice
    texts = [text for _, text in station.events]
    assert not [text for text in texts if "contract" in text]
    assert station.state.snapshot(station.now)["heard"]


def test_an_encounter_as_the_panel_sees_it(station):
    fake = FakeGozcu("encounter", start=0.0)
    snapshot = station.play(fake, 7.0)
    assert fields(snapshot, "state", "mode", "switch") == ("ADVISORY", "CONFIRM", "ARM")
    assert [track["class"] for track in snapshot["tracks"]["items"]] == ["drone"]
    snapshot = station.play(fake, 12.0)
    assert [track["id"] for track in snapshot["tracks"]["items"]] == [3, 5]  # and the bird
    assert snapshot["fields"]["target"]["value"] == 3
    assert fields(snapshot, "proposal", "manoeuvre", "decision") == ("7", "CLIMB", "WAITING")
    assert 2500 <= snapshot["fields"]["time_left"]["value"] <= 3500  # 5 s from tau 10 s
    assert ("WARNING", "Proposal 7: climb, approve within 5 s") in station.events


def test_a_proposal_left_unanswered_expires(station):
    fake = FakeGozcu("encounter", start=0.0)
    snapshot = station.play(fake, 16.0)
    assert fields(snapshot, "state", "decision") == ("ADVISORY", "EXPIRED")


def test_approving_the_proposal_starts_the_avoidance(station):
    fake = FakeGozcu("encounter", start=0.0)
    station.play(fake, 11.0)
    assert fake.on_command(command(1, 1, 7), 11.0) == (mavlink.MAV_RESULT_ACCEPTED, 0)
    assert fields(station.play(fake, 12.0), "state", "decision") == ("AVOID", "APPROVED")
    assert fields(station.play(fake, 21.0), "state") == ("CLEAR OF CONFLICT",)
    assert fields(station.play(fake, 24.0), "state") == ("CLEAR",)


@pytest.mark.parametrize(
    ("mode", "state", "decision"),
    [(AUTO, "AVOID", "APPROVED"), (WARN, "AVOID", None)],  # AUTO acts; WARN only warns
)
def test_auto_avoids_without_asking_and_warn_only_warns(station, mode, state, decision):
    fake = FakeGozcu("encounter", start=0.0, mode=mode)
    snapshot = station.play(fake, 11.0)
    assert snapshot["fields"]["state"]["text"] == state
    if decision is None:
        assert snapshot["fields"]["proposal"]["value"] == 0
    else:
        assert snapshot["fields"]["decision"]["text"] == decision


@pytest.mark.parametrize(
    ("setup", "request_", "answer"),
    [
        ({"inhibit": True}, (0, AUTO), (mavlink.MAV_RESULT_DENIED, 1)),
        ({"mode": WARN}, (1, 1, 7), (mavlink.MAV_RESULT_DENIED, 3)),
        ({}, (1, 1, 99), (mavlink.MAV_RESULT_DENIED, 2)),  # not the waiting proposal
        ({}, (0, 5), (mavlink.MAV_RESULT_DENIED, 4)),
        ({}, (9, 1), (mavlink.MAV_RESULT_UNSUPPORTED, 0)),
        ({}, (2, 0), (mavlink.MAV_RESULT_ACCEPTED, 0)),
    ],
)
def test_commands_are_answered_as_the_contract_says(station, setup, request_, answer):
    fake = FakeGozcu("encounter", start=0.0, **setup)
    station.play(fake, 11.0)
    assert fake.on_command(command(*request_), 11.0) == answer


def test_auto_is_refused_in_safe_mode(station):
    fake = FakeGozcu("mpu-silent", start=0.0)
    snapshot = station.play(fake, 2.0)
    assert fields(snapshot, "faults") == ("MPU silent",)
    assert snapshot["fields"]["mpu_age"]["level"] == "crit"
    assert snapshot["fields"]["watchdog_trips"]["value"] == 1
    assert snapshot["system_status"] == "CRITICAL"
    assert fake.on_command(command(0, AUTO), 2.0) == (mavlink.MAV_RESULT_DENIED, 5)


def test_stale_perception_is_its_own_fault(station):
    snapshot = station.play(FakeGozcu("stale", start=0.0), 2.0)
    assert fields(snapshot, "faults") == ("perception stale",)
    assert snapshot["fields"]["perception_age"]["level"] == "crit"


def test_a_silent_mcu_sends_and_answers_nothing(station):
    fake = FakeGozcu("silent", start=0.0)
    station.play(fake, 5.0)
    assert station.received == []
    assert not fake.answers(5.0)


def test_rates_stay_within_the_contract(station):
    fake = FakeGozcu("encounter", start=0.0)
    station.play(fake, 13.0)
    window = [msg for time, msg in station.received if 9.0 < time <= 13.0]
    named = [msg.name for msg in window if msg.get_type().startswith("NAMED_VALUE")]
    heartbeats = [msg for msg in window if msg.get_type() == "HEARTBEAT"]
    assert 15 <= named.count("G_NTRK") <= 17  # track frames at 4 Hz
    assert 8 <= named.count("G_VER") <= 12  # status at 2 Hz, and at once on a change
    assert len(heartbeats) == 4


def test_the_stations_commands_and_the_fake_agree(station):
    fake = FakeGozcu("encounter", start=0.0)
    station.play(fake, 11.0)
    events = []

    def send(sent):  # the link between them, both ways
        msg = command(ACTIONS[sent.action], sent.args["value"], sent.args["proposal"])
        result, reason = fake.on_command(msg, 11.0)
        commands.on_ack(SimpleNamespace(result=result, result_param2=reason), True, 11.1)

    commands = GozcuCommands(send, lambda *event: events.append(event))
    commands.request("decide", 1, 7)
    commands.poll(11.0, None)
    assert commands.snapshot(11.2)["state"] == "ACCEPTED"
    commands.request("mode", AUTO)
    commands.poll(11.3, None)
    assert commands.snapshot(11.4)["state"] == "ACCEPTED"
    assert fake.mode == AUTO


class FakeConn:
    """A link that delivers the given messages, then fails."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.mav = mavlink.MAVLink(self, srcSystem=1, srcComponent=191)
        self._parser = mavlink.MAVLink(None)

    def recv_match(self, blocking, timeout):
        if not self._messages:
            raise OSError("link closed")
        return self._messages.pop(0)

    def write(self, data):
        self.sent += self._parser.parse_buffer(data) or []


def test_run_answers_commands_for_gozcu_only(capsys):
    sender = mavlink.MAVLink(None, srcSystem=250, srcComponent=190)
    parser = mavlink.MAVLink(None)

    def long(target_component):
        packed = sender.command_long_encode(
            1, target_component, mavlink.MAV_CMD_USER_1, 0, 0, CONFIRM, 0, 0, 0, 0, 0
        ).pack(sender)
        return parser.parse_buffer(packed)[0]

    conn = FakeConn([long(191), long(1)])  # the second is the autopilot's, not ours
    with pytest.raises(OSError):
        run(conn, FakeGozcu("clear", start=0.0), sysid=1)
    acks = [msg for msg in conn.sent if msg.get_type() == "COMMAND_ACK"]
    assert len(acks) == 1
    assert (acks[0].result, acks[0].target_system, acks[0].target_component) == (0, 250, 190)
    assert "command: mode CONFIRM -> ACCEPTED" in capsys.readouterr().out
