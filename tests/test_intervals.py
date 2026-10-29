import io

import pytest

from gcs.intervals import (
    ACK_TIMEOUT_S,
    MAX_ATTEMPTS,
    NO_ACK,
    IntervalRequest,
    IntervalRequester,
    send_interval_request,
)
from gcs.mav import mavlink

ACCEPTED = mavlink.MAV_RESULT_ACCEPTED
DENIED = mavlink.MAV_RESULT_DENIED
RATES = {"ATTITUDE": 10.0, "VFR_HUD": 5.0, "HOME_POSITION": 0.2}


def make(rates=RATES):
    """A requester whose transmissions are recorded as (message, confirmation)."""
    sent = []
    requester = IntervalRequester(rates, send=lambda r: sent.append((r.name, r.attempts)))
    return requester, sent


def results(requester):
    return {request.name: request.result for request in requester.requests}


def time_out_every_attempt(requester):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        requester.poll(now=attempt * (ACK_TIMEOUT_S + 0.1))


def test_requests_go_out_one_at_a_time_in_order():
    requester, sent = make()
    requester.start(now=0.0)
    assert sent == [("ATTITUDE", 0)]
    requester.on_ack(ACCEPTED, now=0.1)
    requester.on_ack(DENIED, now=0.2)
    assert sent == [("ATTITUDE", 0), ("VFR_HUD", 0), ("HOME_POSITION", 0)]
    assert results(requester) == {
        "ATTITUDE": "ACCEPTED",
        "VFR_HUD": "DENIED",
        "HOME_POSITION": None,
    }


def test_an_unanswered_request_is_resent_then_given_up():
    requester, sent = make()
    requester.start(now=0.0)
    time_out_every_attempt(requester)
    assert sent == [("ATTITUDE", 0), ("ATTITUDE", 1), ("ATTITUDE", 2), ("VFR_HUD", 0)]
    assert results(requester)["ATTITUDE"] == NO_ACK


def test_the_answer_to_a_resent_copy_is_not_given_to_the_next_request():
    requester, sent = make()
    requester.start(now=0.0)
    requester.poll(now=ACK_TIMEOUT_S + 0.1)  # ATTITUDE resent: two copies in flight
    requester.on_ack(ACCEPTED, now=1.7)  # first copy: ATTITUDE done, VFR_HUD goes out
    requester.on_ack(ACCEPTED, now=1.8)  # second ATTITUDE copy
    requester.on_ack(DENIED, now=1.9)  # VFR_HUD's own answer
    assert results(requester) == {
        "ATTITUDE": "ACCEPTED",
        "VFR_HUD": "DENIED",
        "HOME_POSITION": None,
    }


def test_late_answers_after_giving_up_are_still_matched():
    requester, sent = make()
    requester.start(now=0.0)
    time_out_every_attempt(requester)  # the simulation was paused
    for t in (6.0, 6.1, 6.2, 6.3):  # resumed: three ATTITUDE answers, then VFR_HUD's
        requester.on_ack(ACCEPTED, now=t)
    assert results(requester)["ATTITUDE"] == "ACCEPTED"
    assert results(requester)["VFR_HUD"] == "ACCEPTED"
    assert sent[-1] == ("HOME_POSITION", 0)


def test_a_stray_answer_is_ignored():
    requester, sent = make()
    requester.on_ack(ACCEPTED, now=0.0)  # nothing was sent yet
    assert results(requester) == dict.fromkeys(RATES)


def test_start_again_resends_everything():
    requester, sent = make({"ATTITUDE": 10.0})
    requester.start(now=0.0)
    requester.on_ack(ACCEPTED, now=0.1)
    requester.start(now=10.0)  # e.g. the vehicle rebooted
    assert results(requester) == {"ATTITUDE": None}
    assert sent == [("ATTITUDE", 0), ("ATTITUDE", 0)]


def test_an_unknown_message_name_is_rejected():
    with pytest.raises(ValueError, match="ATTITUD"):
        IntervalRequester({"ATTITUD": 10.0}, send=lambda request: None)


def test_a_request_is_encoded_as_set_message_interval():
    out = io.BytesIO()
    mav = mavlink.MAVLink(out, srcSystem=250, srcComponent=190)
    send_interval_request(mav, (1, 1), IntervalRequest("ATTITUDE", 10.0, attempts=1))
    msg = mavlink.MAVLink(None).decode(bytearray(out.getvalue()))
    assert msg.get_type() == "COMMAND_LONG"
    assert (msg.get_srcSystem(), msg.get_srcComponent()) == (250, 190)
    assert (msg.target_system, msg.target_component) == (1, 1)
    assert msg.command == mavlink.MAV_CMD_SET_MESSAGE_INTERVAL
    assert msg.confirmation == 1
    assert msg.param1 == mavlink.MAVLINK_MSG_ID_ATTITUDE
    assert msg.param2 == 100_000  # microseconds
