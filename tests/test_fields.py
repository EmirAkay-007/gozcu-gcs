import pytest

from gcs.fields import FIELDS, MESSAGE_RATES_HZ, Field, Limit
from gcs.mav import mavlink

BY_KEY = {field.key: field for field in FIELDS}


def message_class(name):
    return mavlink.mavlink_map[getattr(mavlink, f"MAVLINK_MSG_ID_{name}")]


def test_keys_are_unique():
    assert len(BY_KEY) == len(FIELDS)


@pytest.mark.parametrize("field", [f for f in FIELDS if f.source], ids=lambda f: f.key)
def test_every_source_exists_in_the_dialect(field):
    assert field.attribute in message_class(field.message).fieldnames


def test_rates_are_requested_for_exactly_the_messages_in_use():
    assert {field.message for field in FIELDS if field.message} == set(MESSAGE_RATES_HZ)


def test_seven_or_eight_widgets_are_pinned():
    assert 7 <= sum(field.pinned for field in FIELDS) <= 8


def test_stale_limit_follows_the_message_rate():
    assert BY_KEY["roll"].stale_limit() == 2.0  # 10 Hz: the 2 s floor
    assert BY_KEY["home_lat"].stale_limit() == 15.0  # 0.2 Hz: three intervals
    assert BY_KEY["mode"].stale_limit() == 3.0  # three missed heartbeats
    assert BY_KEY["link_loss"].stale_limit() is None  # worked out on every snapshot


def test_format_and_alert_level():
    field = Field("x", "X", "%", None, decimals=1, warn=Limit(below=30), crit=Limit(below=15))
    assert field.format(None) == "—"
    assert field.format(12.345) == "12.3"
    assert [field.level(value) for value in (50, 20, 10, None)] == [None, "warn", "crit", None]
    assert field.level(True) is None  # a yes/no is not a number here


def test_gps_fix_is_shown_as_text_and_alarms_below_3d():
    gps_fix = BY_KEY["gps_fix"]
    assert gps_fix.format(mavlink.GPS_FIX_TYPE_3D_FIX) == "3D FIX"
    assert gps_fix.level(mavlink.GPS_FIX_TYPE_2D_FIX) == "crit"
    assert gps_fix.level(mavlink.GPS_FIX_TYPE_RTK_FIXED) is None
