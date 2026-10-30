"""Gözcü's reports, as docs/message_contract.md defines them, turned into the panel's data."""

import pytest

from gcs.gozcu import GozcuState, track_value
from gcs.mav import mavlink


class Gozcu:
    """Sends as the Gözcü component would; each message arrives parsed, as off the link."""

    def __init__(self, state):
        self.state = state
        self.now = 0.0
        self.mav = mavlink.MAVLink(self, srcSystem=1, srcComponent=191)
        self._parser = mavlink.MAVLink(None)

    def write(self, data):
        for msg in self._parser.parse_buffer(data) or []:
            self.state.handle(msg, self.now)

    def heartbeat(self, status=mavlink.MAV_STATE_ACTIVE):
        self.mav.heartbeat_send(
            mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavlink.MAV_AUTOPILOT_INVALID, 0, 0, status
        )

    def ints(self, time_boot_ms=1000, **values):
        for name, value in values.items():
            self.mav.named_value_int_send(time_boot_ms, name.encode(), value)

    def floats(self, time_boot_ms=1000, **values):
        for name, value in values.items():
            self.mav.named_value_float_send(time_boot_ms, name.encode(), value)

    def track(self, k, time_boot_ms, track_id, tau=8.5):
        """All values of track k: a drone 10° right, 2° down."""
        self.ints(time_boot_ms, **{f"T{k}_ID": track_id, f"T{k}_CLS": 2})
        floats = {"CONF": 0.9, "AZ": 10.0, "EL": -2.0, "RATE": 0.4, "PX": 14.0, "TAU": tau}
        self.floats(time_boot_ms, **{f"T{k}_{name}": value for name, value in floats.items()})


@pytest.fixture
def heard():
    """(gozcu sender, events, state) with the heartbeat already heard."""
    events = []
    state = GozcuState(lambda severity, text: events.append((severity, text)))
    gozcu = Gozcu(state)
    gozcu.heartbeat()
    events.clear()
    return gozcu, events, state


def test_values_are_shown_by_name_with_their_alert_levels(heard):
    gozcu, _, state = heard
    gozcu.ints(G_STATE=1, G_MODE=1, G_ARM=0, G_PERC=1, G_MPU_MS=230)
    gozcu.floats(G_FPS=7.5, G_LAT_MS=180.0)
    fields = state.snapshot(gozcu.now)["fields"]
    assert (fields["state"]["text"], fields["mode"]["text"]) == ("ADVISORY", "CONFIRM")
    assert (fields["switch"]["text"], fields["switch"]["level"]) == ("INHIBIT", "warn")
    assert fields["perception"]["text"] == "RUNNING"
    assert (fields["mpu_age"]["text"], fields["mpu_age"]["level"]) == ("230", "crit")
    assert fields["fps"]["level"] == "warn"  # below the 8 Hz target
    assert fields["latency"]["level"] is None  # under 250 ms


def test_values_go_stale_without_updates(heard):
    gozcu, _, state = heard
    gozcu.ints(G_STATE=0)
    gozcu.now = 1.6
    assert state.snapshot(gozcu.now)["fields"]["state"]["stale"]


def test_a_track_frame_is_shown_only_once_closed(heard):
    gozcu, _, state = heard
    gozcu.track(1, 2000, 4, tau=-1.0)
    assert state.snapshot(gozcu.now)["tracks"]["items"] == []
    gozcu.ints(2000, G_NTRK=1)
    (track,) = state.snapshot(gozcu.now)["tracks"]["items"]
    assert track["id"] == 4
    assert track["class"] == "drone"
    assert track["az"] == pytest.approx(10.0)
    assert track["tau"] is None  # -1: not closing


def test_status_values_between_track_values_do_not_break_a_frame(heard):
    gozcu, _, state = heard
    gozcu.ints(2000, T1_ID=7)
    gozcu.ints(2005, G_STATE=1)  # a status sending with a time of its own
    gozcu.floats(2000, T1_AZ=-3.5)
    gozcu.ints(2000, G_NTRK=1)
    (track,) = state.snapshot(gozcu.now)["tracks"]["items"]
    assert (track["id"], track["az"]) == (7, -3.5)


def test_a_frame_never_takes_values_from_the_one_before(heard):
    gozcu, _, state = heard
    gozcu.track(1, 1000, 5)
    gozcu.ints(1000, G_NTRK=1)
    gozcu.ints(1250, T1_ID=6)  # the rest of this frame was lost
    gozcu.ints(1250, G_NTRK=1)
    (track,) = state.snapshot(gozcu.now)["tracks"]["items"]
    assert track["id"] == 6
    assert track["az"] is None


def test_a_track_missing_from_the_newest_frame_disappears(heard):
    gozcu, _, state = heard
    gozcu.track(1, 1000, 1)
    gozcu.track(2, 1000, 2)
    gozcu.ints(1000, G_NTRK=2)
    assert [t["id"] for t in state.snapshot(gozcu.now)["tracks"]["items"]] == [1, 2]
    gozcu.ints(1250, G_NTRK=0)
    tracks = state.snapshot(gozcu.now)["tracks"]
    assert tracks["items"] == []
    assert not tracks["stale"]
    gozcu.now = 1.1
    assert state.snapshot(gozcu.now)["tracks"]["stale"]


def test_falling_silent_and_coming_back_are_reported(heard):
    gozcu, events, state = heard
    assert state.snapshot(0.0)["present"]
    state.check(3.5)
    assert not state.snapshot(3.5)["present"]
    gozcu.now = 4.0
    gozcu.heartbeat(mavlink.MAV_STATE_CRITICAL)
    snapshot = state.snapshot(4.0)
    assert snapshot["present"]
    assert snapshot["system_status"] == "CRITICAL"
    assert events == [
        ("WARNING", "Gözcü silent: no heartbeat for 3 s"),
        ("INFO", "Gözcü heard again"),
    ]


def test_changes_that_matter_reach_the_log(heard):
    gozcu, events, _ = heard
    gozcu.ints(G_STATE=0, G_MODE=0, G_FAULT=0)
    gozcu.ints(G_STATE=0)  # no change, no event
    gozcu.ints(G_STATE=1)
    gozcu.ints(G_STATE=2)
    gozcu.ints(G_ARM=0, G_PERC=0)
    gozcu.ints(G_FAULT=3)
    gozcu.ints(G_FAULT=0)
    assert events == [
        ("NOTICE", "threat CLEAR"),
        ("NOTICE", "mode WARN"),
        ("WARNING", "threat ADVISORY"),
        ("CRITICAL", "threat AVOID"),
        ("NOTICE", "switch INHIBIT"),
        ("NOTICE", "perception PAUSED"),
        ("CRITICAL", "safe mode: MPU silent, perception stale"),
        ("NOTICE", "safe mode over"),
    ]


def test_the_autopilot_is_held_while_gozcu_avoids(heard):
    gozcu, _, state = heard
    gozcu.ints(G_STATE=1)
    assert state.holding(0.0) is None  # an advisory holds nothing
    gozcu.ints(G_STATE=2)
    assert state.holding(0.0) == "Gözcü is avoiding"
    assert state.holding(1.6) is None  # an old state proves nothing
    gozcu.now = 3.5
    gozcu.ints(G_STATE=2)
    assert state.holding(3.5) is None  # nor does a Gözcü that has fallen silent


def test_the_autopilot_is_held_while_a_proposal_waits_for_the_operator(heard):
    gozcu, _, state = heard
    gozcu.ints(G_STATE=1, G_PROP=7, G_DEC=0, G_TTL_MS=4000)
    assert state.holding(0.5) == "Gözcü's proposal 7 waits for an answer"
    assert state.holding(1.0) == "Gözcü's proposal 7 waits for an answer"
    gozcu.now = 1.0
    gozcu.ints(G_PROP=7, G_DEC=0, G_TTL_MS=500)
    assert state.holding(1.4) is not None
    assert state.holding(1.6) is None  # its time is up
    gozcu.ints(G_TTL_MS=3000, G_DEC=1)
    assert state.holding(1.0) is None  # approved
    gozcu.ints(G_PROP=0, G_DEC=0)
    assert state.holding(1.0) is None  # no proposal


def test_texts_from_gozcu_reach_the_log(heard):
    gozcu, events, _ = heard
    text = "Proposal 7: climb, approve within 5 s"
    gozcu.mav.statustext_send(mavlink.MAV_SEVERITY_WARNING, text.encode())
    assert events == [("WARNING", text)]


def test_what_the_contract_does_not_know_is_reported_once(heard):
    gozcu, events, _ = heard
    gozcu.ints(G_FOO=1)
    gozcu.ints(G_FOO=2, G_VER=2)
    gozcu.ints(G_VER=2)
    assert events == [
        ("NOTICE", "Gözcü sent G_FOO, which the contract does not define"),
        ("WARNING", "Gözcü speaks contract version 2; this station knows 1"),
    ]


def test_track_value_names():
    assert track_value("T1_AZ") == (1, "az")
    assert track_value("T3_TAU") == (3, "tau")
    assert track_value("T4_AZ") is None  # at most three tracks
    assert track_value("T1_XYZ") is None
    assert track_value("G_STATE") is None
