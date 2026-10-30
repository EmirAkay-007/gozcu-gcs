import json

import pytest

from gcs.mav import mavlink
from gcs.state import NOT_RECEIVED, TelemetryState

BATTERY = mavlink.MAV_SYS_STATUS_SENSOR_BATTERY


def message(name, **values):
    """A MAVLink message whose fields are all 0 except the ones given."""
    cls = mavlink.mavlink_map[getattr(mavlink, f"MAVLINK_MSG_ID_{name}")]
    unknown = set(values) - set(cls.fieldnames)
    assert not unknown, f"{name} has no field(s) {unknown}"
    return cls(**{field: values.get(field, 0) for field in cls.fieldnames})


def test_a_field_never_received_is_stale_and_says_why():
    snap = TelemetryState().snapshot(now=0.0)["airspeed"]
    assert snap["value"] is None
    assert snap["reason"] == NOT_RECEIVED
    assert snap["stale"]


def test_values_are_converted_formatted_and_age():
    state = TelemetryState()
    state.update(message("VFR_HUD", airspeed=12.34, heading=270), now=10.0)
    fresh = state.snapshot(now=10.5)
    assert fresh["airspeed"]["value"] == pytest.approx(12.34)
    assert fresh["airspeed"]["text"] == "12.3"
    assert fresh["heading"]["text"] == "270"
    assert fresh["airspeed"]["age"] == 0.5
    assert not fresh["airspeed"]["stale"]
    assert state.snapshot(now=12.6)["airspeed"]["stale"]  # past the 2 s floor


def test_battery_values_without_a_monitor_are_unknown_and_say_why():
    state = TelemetryState()
    sys_status = message(
        "SYS_STATUS", onboard_control_sensors_present=BATTERY, battery_remaining=-1
    )
    state.update(sys_status, now=0.0)
    snap = state.snapshot(now=0.0)
    assert snap["battery_voltage"]["value"] is None
    assert snap["battery_voltage"]["reason"] == "no battery monitor"


def test_battery_values_with_a_monitor():
    state = TelemetryState()
    sys_status = message(
        "SYS_STATUS",
        onboard_control_sensors_enabled=BATTERY,
        voltage_battery=12600,
        current_battery=-1,
        battery_remaining=12,
    )
    state.update(sys_status, now=0.0)
    snap = state.snapshot(now=0.0)
    assert snap["battery_voltage"]["text"] == "12.60"
    assert snap["battery_current"]["reason"] == "unknown"
    assert snap["battery_remaining"]["level"] == "crit"


def test_a_nan_never_reaches_the_snapshot():
    state = TelemetryState()
    state.update(message("VFR_HUD", airspeed=float("nan")), now=0.0)
    snap = state.snapshot(now=0.0)
    assert snap["airspeed"]["value"] is None
    assert snap["airspeed"]["reason"] == "not a number"
    json.dumps(snap, allow_nan=False)  # raises if a NaN slipped through


def test_home_distance_needs_both_position_and_home():
    state = TelemetryState()
    state.update(message("GLOBAL_POSITION_INT", lat=399035714, lon=412375758), now=0.0)
    assert state.snapshot(now=0.0)["home_distance"]["reason"] == "home not set"
    # home 0.00009 degrees further south: about 10 m
    state.update(message("HOME_POSITION", latitude=399034814, longitude=412375758), now=1.0)
    assert state.snapshot(now=1.0)["home_distance"]["value"] == pytest.approx(10.0, abs=0.1)


def test_zero_latitude_and_longitude_mean_no_position():
    state = TelemetryState()
    state.update(message("GLOBAL_POSITION_INT"), now=0.0)
    snap = state.snapshot(now=0.0)
    assert snap["lat"]["reason"] == "no position"
    assert snap["home_distance"]["reason"] == "no position"


def test_a_fresh_value_is_one_known_and_not_stale():
    state = TelemetryState()
    assert state.fresh("mode", now=0.0) is None  # never received
    state.set("mode", "RTL", now=10.0)
    assert state.fresh("mode", now=12.0).value == "RTL"
    assert state.fresh("mode", now=13.5) is None  # stale after 3 s


def test_only_registered_values_can_be_set():
    with pytest.raises(KeyError):
        TelemetryState().set("airpseed", 1.0, now=0.0)


def test_the_snapshot_is_json_ready():
    state = TelemetryState()
    state.set("mode", "FBWA", now=0.0)
    state.set("armed", False, now=0.0)
    assert json.loads(json.dumps(state.snapshot(now=1.0), allow_nan=False))["armed"]["text"] == (
        "DISARMED"
    )
