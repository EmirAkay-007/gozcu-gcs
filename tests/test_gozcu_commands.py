"""Commands to Gözcü: sent one at a time, sent again while unanswered, followed to the ACK."""

from types import SimpleNamespace

import pytest

from gcs.gozcu_commands import GozcuCommands
from gcs.mav import mavlink


class Link:
    """Records what GozcuCommands would put on the link, and what it logs."""

    def __init__(self):
        self.sent = []  # (action, value, proposal, confirmation)
        self.events = []
        self.commands = GozcuCommands(self._send, lambda *event: self.events.append(event))

    def _send(self, command):
        self.sent.append((command.action, command.value, command.proposal, command.sends - 1))

    def state(self, now=0.0):
        latest = self.commands.snapshot(now)
        return None if latest is None else (latest["state"], latest["detail"])


def ack(result, reason=0):
    return SimpleNamespace(command=mavlink.MAV_CMD_USER_1, result=result, result_param2=reason)


@pytest.fixture
def link():
    return Link()


def test_a_command_goes_out_once_and_is_accepted(link):
    link.commands.request("mode", 1)
    link.commands.poll(0.0, None)
    link.commands.on_ack(ack(mavlink.MAV_RESULT_ACCEPTED), True, 0.2)
    link.commands.poll(5.0, None)
    assert link.sent == [("mode", 1, 0, 0)]
    assert link.state() == ("ACCEPTED", None)
    assert link.events == [
        ("INFO", "asking Gözcü: mode CONFIRM"),
        ("INFO", "Gözcü accepted: mode CONFIRM"),
    ]


def test_an_unanswered_command_is_sent_again_then_given_up(link):
    link.commands.request("decide", 1, 7)
    for now in (0.0, 0.5, 1.0, 1.5, 2.0, 2.9):
        link.commands.poll(now, None)
    assert link.sent == [("decide", 1, 7, 0), ("decide", 1, 7, 1), ("decide", 1, 7, 2)]
    assert link.state() == ("PENDING", None)
    link.commands.poll(3.0, None)
    assert link.state() == ("NO ANSWER", None)
    assert link.events[-1] == ("WARNING", "no answer from Gözcü: approve proposal 7")


def test_a_refusal_says_why(link):
    link.commands.request("mode", 2)
    link.commands.poll(0.0, None)
    link.commands.on_ack(ack(mavlink.MAV_RESULT_DENIED, reason=1), True, 0.1)
    assert link.state() == ("DENIED", "switch on INHIBIT")
    assert link.events[-1] == ("WARNING", "Gözcü refused: mode AUTO (switch on INHIBIT)")


def test_an_answer_from_the_autopilot_means_gozcu_never_got_it(link):
    link.commands.request("perception", 0)
    link.commands.poll(0.0, None)
    link.commands.on_ack(ack(mavlink.MAV_RESULT_UNSUPPORTED), False, 0.1)
    assert link.state() == ("NO ROUTE", "the autopilot answered: no route to Gözcü")
    assert link.events[-1][1].startswith("not delivered: perception PAUSE")


def test_in_progress_means_wait_without_sending_again(link):
    link.commands.request("mode", 0)
    link.commands.poll(0.0, None)
    link.commands.on_ack(ack(mavlink.MAV_RESULT_IN_PROGRESS), True, 0.9)
    link.commands.poll(1.5, None)
    assert len(link.sent) == 1
    link.commands.on_ack(ack(mavlink.MAV_RESULT_ACCEPTED), True, 1.6)
    assert link.state() == ("ACCEPTED", None)


def test_only_one_command_is_in_flight(link):
    link.commands.request("mode", 1)
    link.commands.request("perception", 1)
    link.commands.poll(0.0, None)
    link.commands.poll(0.1, None)
    assert [sent[0] for sent in link.sent] == ["mode"]
    link.commands.on_ack(ack(mavlink.MAV_RESULT_ACCEPTED), True, 0.2)
    link.commands.poll(0.3, None)
    assert [sent[0] for sent in link.sent] == ["mode", "perception"]


def test_nothing_is_sent_while_gozcu_cannot_be_reached(link):
    link.commands.request("mode", 0)
    link.commands.poll(0.0, "Gözcü is silent")
    assert link.sent == []
    assert link.state() == ("NOT SENT", "Gözcü is silent")
    assert link.events == [("WARNING", "not sent: mode WARN (Gözcü is silent)")]


def test_losing_the_link_ends_the_command_in_flight(link):
    link.commands.request("mode", 1)
    link.commands.poll(0.0, None)
    link.commands.poll(0.4, "no link to the vehicle")
    assert link.state() == ("NO ANSWER", "no link to the vehicle")


def test_a_late_answer_changes_nothing(link):
    link.commands.request("mode", 1)
    for now in (0.0, 1.0, 2.0, 3.0):
        link.commands.poll(now, None)
    link.commands.on_ack(ack(mavlink.MAV_RESULT_ACCEPTED), True, 3.1)
    assert link.state() == ("NO ANSWER", None)


@pytest.mark.parametrize(
    "request_",
    [
        ("jump", 1, 0),
        ("mode", 3, 0),
        ("mode", True, 0),  # a bool is not a mode
        ("mode", 1.0, 0),
        ("decide", 1, 0),  # a decision needs the proposal it is about
        ("perception", 1, 5),
        (None, None, None),
    ],
)
def test_a_malformed_request_is_logged_and_never_sent(link, request_):
    link.commands.request(*request_)
    link.commands.poll(0.0, None)
    assert link.sent == []
    assert link.state() is None
    assert link.events[0][0] == "WARNING"
    assert link.events[0][1].startswith("ignored a malformed command")
