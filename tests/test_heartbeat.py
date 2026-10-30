import pytest

from gcs.heartbeat import is_armed, is_plane, is_vehicle_heartbeat, mode_name
from gcs.mav import mavlink

CUSTOM = mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
ARMED = mavlink.MAV_MODE_FLAG_SAFETY_ARMED


def heartbeat(mav_type, autopilot=mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA, base_mode=CUSTOM, mode=0):
    return mavlink.MAVLink_heartbeat_message(
        mav_type, autopilot, base_mode, mode, mavlink.MAV_STATE_STANDBY, 3
    )


def test_arduplane_autopilot_is_the_vehicle():
    assert is_vehicle_heartbeat(heartbeat(mavlink.MAV_TYPE_FIXED_WING))


@pytest.mark.parametrize(
    ("mav_type", "autopilot"),
    [
        # MAVProxy / QGroundControl
        (mavlink.MAV_TYPE_GCS, mavlink.MAV_AUTOPILOT_INVALID),
        # Gözcü companion computer
        (mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavlink.MAV_AUTOPILOT_INVALID),
        # A companion that copies the autopilot field is still not the vehicle
        (mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA),
        # No autopilot at all
        (mavlink.MAV_TYPE_FIXED_WING, mavlink.MAV_AUTOPILOT_INVALID),
        # ArduPilot's AntennaTracker firmware
        (mavlink.MAV_TYPE_ANTENNA_TRACKER, mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA),
    ],
)
def test_other_components_are_not_the_vehicle(mav_type, autopilot):
    assert not is_vehicle_heartbeat(heartbeat(mav_type, autopilot))


def test_arm_state_comes_from_the_safety_flag():
    assert is_armed(heartbeat(mavlink.MAV_TYPE_FIXED_WING, base_mode=CUSTOM | ARMED))
    assert not is_armed(heartbeat(mavlink.MAV_TYPE_FIXED_WING))


@pytest.mark.parametrize(
    ("mav_type", "mode", "expected"),
    [
        (mavlink.MAV_TYPE_FIXED_WING, 5, "FBWA"),
        (mavlink.MAV_TYPE_FIXED_WING, 10, "AUTO"),
        # Quadplanes report a VTOL type but use Plane's mode numbers
        (mavlink.MAV_TYPE_VTOL_TILTROTOR, 19, "QLOITER"),
        # Multirotors use Copter's mode numbers
        (mavlink.MAV_TYPE_QUADROTOR, 5, "LOITER"),
        (mavlink.MAV_TYPE_FIXED_WING, 99, "Mode(99)"),
    ],
)
def test_mode_names_follow_the_vehicle_type(mav_type, mode, expected):
    assert mode_name(heartbeat(mav_type, mode=mode)) == expected


def test_planes_and_quadplanes_number_their_modes_as_arduplane():
    assert is_plane(heartbeat(mavlink.MAV_TYPE_FIXED_WING))
    assert is_plane(heartbeat(mavlink.MAV_TYPE_VTOL_TILTROTOR))
    assert not is_plane(heartbeat(mavlink.MAV_TYPE_QUADROTOR))  # Copter: RTL is 6, not 11


def test_mode_is_unknown_without_the_custom_mode_flag():
    assert mode_name(heartbeat(mavlink.MAV_TYPE_FIXED_WING, base_mode=0, mode=10)) == "Mode(10)"
