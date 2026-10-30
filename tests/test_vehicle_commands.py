"""Commands to the autopilot: checked, sent one at a time, held while they must not go."""

import math
from types import SimpleNamespace

import pytest
from pymavlink import mavutil

from gcs.mav import mavlink
from gcs.vehicle_commands import COMMANDS, MODES, VehicleCommands

FLY = {"lat": 41.0123456, "lon": 29.0123456, "alt": 120}


class Link:
    """Records what VehicleCommands would put on the link, and what it logs."""

    def __init__(self):
        self.sent = []  # (action, args, confirmation)
        self.events = []
        self.commands = VehicleCommands(self._send, lambda *event: self.events.append(event))

    def _send(self, command):
        self.sent.append((command.action, command.args, command.sends - 1))

    def state(self, now=0.0):
        latest = self.commands.snapshot(now)
        return None if latest is None else (latest["state"], latest["detail"])

    def answer(self, action, result, now):
        self.commands.on_ack(SimpleNamespace(command=COMMANDS[action], result=result), now)


@pytest.fixture
def link():
    return Link()


def test_the_modes_offered_are_arduplanes():
    plane = mavutil.mode_mapping_byname(mavlink.MAV_TYPE_FIXED_WING)
    assert {name: plane[name] for name in MODES} == MODES
    assert "MANUAL" not in MODES  # the sticks' own mode, for the RC pilot


def test_a_mode_change_goes_out_once_and_is_accepted(link):
    link.commands.request("mode", {"mode": "RTL"})
    link.commands.poll(0.0, None)
    link.answer("mode", mavlink.MAV_RESULT_ACCEPTED, 0.1)
    link.commands.poll(5.0, None)
    assert link.sent == [("mode", {"mode": "RTL"}, 0)]
    assert link.state() == ("ACCEPTED", None)
    assert link.events == [
        ("INFO", "asking the autopilot: mode RTL"),
        ("INFO", "the autopilot accepted: mode RTL"),
    ]
    snapshot = link.commands.snapshot(0.5)
    assert (snapshot["action"], snapshot["mode"], snapshot["text"]) == ("mode", "RTL", "mode RTL")


def test_a_refused_arm_says_so(link):
    link.commands.request("arm", {"arm": True})
    link.commands.poll(0.0, None)
    link.answer("arm", mavlink.MAV_RESULT_FAILED, 0.1)  # a pre-arm check failed
    assert link.state() == ("FAILED", None)
    assert link.events[-1] == ("WARNING", "the autopilot refused: arm (FAILED)")


def test_an_answer_about_another_command_is_not_this_ones(link):
    link.commands.request("arm", {"arm": False})
    link.commands.poll(0.0, None)
    link.answer("mode", mavlink.MAV_RESULT_ACCEPTED, 0.1)
    assert link.state() == ("PENDING", None)
    link.answer("arm", mavlink.MAV_RESULT_ACCEPTED, 0.2)
    assert link.state() == ("ACCEPTED", None)


def test_an_unanswered_command_is_sent_again_then_given_up(link):
    link.commands.request("fly_to", FLY)
    for now in (0.0, 1.0, 2.0):
        link.commands.poll(now, None)
    assert [sent[2] for sent in link.sent] == [0, 1, 2]
    link.commands.poll(3.0, None)
    assert link.state() == ("NO ANSWER", None)
    text = "no answer from the autopilot: fly to 41.012346, 29.012346 at 120 m"
    assert link.events[-1] == ("WARNING", text)


def test_nothing_new_is_sent_while_held(link):
    link.commands.request("mode", {"mode": "LOITER"})
    link.commands.poll(0.0, None, "Gözcü is avoiding")
    assert link.sent == []
    assert link.state() == ("NOT SENT", "Gözcü is avoiding")
    assert link.events == [("WARNING", "not sent: mode LOITER (Gözcü is avoiding)")]


def test_a_command_in_flight_is_not_sent_again_once_held_but_its_answer_counts(link):
    link.commands.request("mode", {"mode": "AUTO"})
    link.commands.poll(0.0, None)
    link.commands.poll(1.0, None, "Gözcü is avoiding")  # due again, but held
    assert len(link.sent) == 1
    assert link.state() == ("NO ANSWER", "Gözcü is avoiding")
    link.commands.request("mode", {"mode": "AUTO"})
    link.commands.poll(1.1, None)
    link.commands.poll(1.5, None, "Gözcü's proposal 3 waits for an answer")
    link.answer("mode", mavlink.MAV_RESULT_ACCEPTED, 1.6)  # sent before the hold: it counts
    assert link.state() == ("ACCEPTED", None)


def test_losing_the_link_ends_the_command_in_flight(link):
    link.commands.request("arm", {"arm": True})
    link.commands.poll(0.0, None)
    link.commands.poll(0.4, "no link to the vehicle")
    assert link.state() == ("NO ANSWER", "no link to the vehicle")


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("mode", {"mode": "MANUAL"}),  # not offered
        ("mode", {"mode": ["RTL"]}),
        ("mode", {"mode": "RTL", "force": True}),
        ("arm", {"arm": 1}),  # a bool, nothing else
        ("arm", {}),
        ("fly_to", {**FLY, "alt": 5}),  # below the lowest altitude offered
        ("fly_to", {**FLY, "alt": 900}),
        ("fly_to", {**FLY, "lat": 91.0}),
        ("fly_to", {**FLY, "lon": math.nan}),
        ("fly_to", {**FLY, "alt": True}),
        ("fly_to", {"lat": 41.0, "lon": 29.0}),
        ("land", {}),
        (None, None),
    ],
)
def test_a_malformed_request_is_logged_and_never_sent(link, action, args):
    link.commands.request(action, args)
    link.commands.poll(0.0, None)
    assert link.sent == []
    assert link.state() is None
    assert link.events[0][0] == "WARNING"
    assert link.events[0][1].startswith("ignored a malformed command")
