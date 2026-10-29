"""Telling the vehicle's autopilot apart from the other MAVLink components.

ArduPilot forwards every HEARTBEAT it hears, so a GCS link also carries those
of MAVProxy (a GCS) and of the Gözcü companion computer (an onboard
controller). Mode, arm state and link health must come from the autopilot only.
"""

from gcs.mav import mavlink, mavutil

# HEARTBEAT types that are never the vehicle itself.
NON_VEHICLE_TYPES = frozenset(
    {
        mavlink.MAV_TYPE_GCS,
        mavlink.MAV_TYPE_ANTENNA_TRACKER,  # ArduPilot's tracker firmware looks like an autopilot
        mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
        mavlink.MAV_TYPE_GIMBAL,
        mavlink.MAV_TYPE_ADSB,
        mavlink.MAV_TYPE_CAMERA,
    }
)


def is_vehicle_heartbeat(msg: mavlink.MAVLink_heartbeat_message) -> bool:
    """True if the HEARTBEAT comes from a flight controller."""
    return msg.autopilot != mavlink.MAV_AUTOPILOT_INVALID and msg.type not in NON_VEHICLE_TYPES


def is_armed(msg: mavlink.MAVLink_heartbeat_message) -> bool:
    """Arm state reported in a vehicle HEARTBEAT."""
    return bool(msg.base_mode & mavlink.MAV_MODE_FLAG_SAFETY_ARMED)


def mode_name(msg: mavlink.MAVLink_heartbeat_message) -> str:
    """Flight mode of a vehicle HEARTBEAT, e.g. "FBWA"; "Mode(n)" if unknown.

    ArduPilot numbers its modes per vehicle family, so the name table is chosen
    from MAV_TYPE: fixed wings and quadplanes use Plane's, multirotors Copter's.
    """
    if msg.base_mode & mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED:
        names = mavutil.mode_mapping_bynumber(msg.type) or {}
        if msg.custom_mode in names:
            return names[msg.custom_mode]
    return f"Mode({msg.custom_mode})"
