"""The telemetry values the GCS shows: one row per value.

Each Field says where a value comes from (MAVLink message and field), how to
convert it, how it is labelled, and when it counts as stale or alarming. The
state model, the message rates requested from the autopilot and the browser's
widget list are all built from this table, so a new widget is a new row here.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gcs import units
from gcs.mav import enum_name, mavlink

# Rates asked of the autopilot, in Hz. The browser refreshes at 10 Hz, so faster
# streams would be wasted; slowly changing values get slower rates.
MESSAGE_RATES_HZ = {
    "ATTITUDE": 10.0,
    "GLOBAL_POSITION_INT": 5.0,
    "VFR_HUD": 5.0,
    "SYS_STATUS": 2.0,
    "GPS_RAW_INT": 2.0,
    "BATTERY_STATUS": 1.0,
    "HOME_POSITION": 0.2,
}

# A value is stale after this many intervals of its message without an update,
# but never sooner than MIN_STALE_S, so one late message does not grey it out.
STALE_INTERVALS = 3
MIN_STALE_S = 2.0


@dataclass(frozen=True)
class Limit:
    """An alert bound: the value is alarming below or above it."""

    below: float | None = None
    above: float | None = None

    def exceeded_by(self, value: float) -> bool:
        return (self.below is not None and value < self.below) or (
            self.above is not None and value > self.above
        )


@dataclass(frozen=True)
class Field:
    key: str  # name in the state and in the browser
    label: str  # shown on the widget
    unit: str
    source: str | None  # "MESSAGE.field", or None for values the GCS works out itself
    convert: Callable[[Any], Any] | None = None  # raw value -> value; None means unknown
    unavailable: Callable[[Any], str | None] | None = None  # message -> why it has no value
    text: Callable[[Any], str] | None = None  # how to show a value that is not a plain number
    decimals: int = 1
    warn: Limit | None = None
    crit: Limit | None = None
    stale_after_s: float | None = None  # None: from the message rate, or never for GCS values
    pinned: bool = False  # among the fixed widgets shown before "More"

    @property
    def message(self) -> str | None:
        return self.source.split(".")[0] if self.source else None

    @property
    def attribute(self) -> str | None:
        return self.source.split(".")[1] if self.source else None

    def stale_limit(self) -> float | None:
        """Seconds without an update after which the value is stale; None = never."""
        if self.stale_after_s is not None:
            return self.stale_after_s
        if self.message is None:
            return None
        return max(MIN_STALE_S, STALE_INTERVALS / MESSAGE_RATES_HZ[self.message])

    def read(self, msg: Any) -> tuple[Any, str | None]:
        """(value, None) from a message, or (None, reason) when it carries no usable value."""
        if self.unavailable is not None:
            reason = self.unavailable(msg)
            if reason:
                return None, reason
        raw = getattr(msg, self.attribute)
        value = self.convert(raw) if self.convert else raw
        if value is None:
            return None, "unknown"  # MAVLink's "no value" marker
        if isinstance(value, float) and not math.isfinite(value):
            return None, "not a number"
        return value, None

    def format(self, value: Any) -> str:
        if value is None:
            return "—"
        if self.text is not None:
            return self.text(value)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return f"{value:.{self.decimals}f}"
        return str(value)

    def level(self, value: Any) -> str | None:
        """ "crit", "warn" or None for a value."""
        if not isinstance(value, int | float) or isinstance(value, bool):
            return None
        if self.crit is not None and self.crit.exceeded_by(value):
            return "crit"
        if self.warn is not None and self.warn.exceeded_by(value):
            return "warn"
        return None


def _no_battery_monitor(msg: Any) -> str | None:
    # ArduPilot always sets the battery bit in "present"; "enabled" says whether a
    # monitor is configured (BATT_MONITOR > 0).
    if msg.onboard_control_sensors_enabled & mavlink.MAV_SYS_STATUS_SENSOR_BATTERY:
        return None
    return "no battery monitor"


def _no_position(msg: Any) -> str | None:
    return "no position" if msg.lat == 0 and msg.lon == 0 else None


def _gps_fix_text(value: int) -> str:
    return enum_name("GPS_FIX_TYPE", value).replace("_", " ")  # e.g. "3D FIX"


def _armed_text(value: bool) -> str:
    return "ARMED" if value else "DISARMED"


FIELDS: tuple[Field, ...] = (
    # Flight
    Field("airspeed", "Airspeed", "m/s", "VFR_HUD.airspeed", pinned=True),
    Field("groundspeed", "Groundspeed", "m/s", "VFR_HUD.groundspeed", pinned=True),
    Field(
        "alt_rel", "Rel Alt", "m", "GLOBAL_POSITION_INT.relative_alt", units.mm_to_m, pinned=True
    ),
    Field("alt_msl", "MSL Alt", "m", "GLOBAL_POSITION_INT.alt", units.mm_to_m),
    Field("climb", "Climb", "m/s", "VFR_HUD.climb", pinned=True),
    Field("heading", "Heading", "°", "VFR_HUD.heading", decimals=0, pinned=True),
    Field("roll", "Roll", "°", "ATTITUDE.roll", units.rad_to_deg),
    Field("pitch", "Pitch", "°", "ATTITUDE.pitch", units.rad_to_deg),
    Field("yaw", "Yaw", "°", "ATTITUDE.yaw", units.rad_to_heading, decimals=0),
    Field("throttle", "Throttle", "%", "VFR_HUD.throttle", decimals=0),
    # Position, for the map
    Field(
        "lat",
        "Latitude",
        "°",
        "GLOBAL_POSITION_INT.lat",
        units.deg_e7_to_deg,
        unavailable=_no_position,
        decimals=7,
    ),
    Field(
        "lon",
        "Longitude",
        "°",
        "GLOBAL_POSITION_INT.lon",
        units.deg_e7_to_deg,
        unavailable=_no_position,
        decimals=7,
    ),
    Field(
        "home_lat", "Home latitude", "°", "HOME_POSITION.latitude", units.deg_e7_to_deg, decimals=7
    ),
    Field(
        "home_lon",
        "Home longitude",
        "°",
        "HOME_POSITION.longitude",
        units.deg_e7_to_deg,
        decimals=7,
    ),
    Field(
        "home_distance", "Home dist", "m", None, decimals=0, stale_after_s=MIN_STALE_S, pinned=True
    ),
    # Power. The 30 % / 15 % margins are ours; the pack's own limits belong in the
    # vehicle's BATT_LOW_* / BATT_CRT_* parameters.
    Field(
        "battery_voltage",
        "Battery",
        "V",
        "SYS_STATUS.voltage_battery",
        units.millivolts_to_volts,
        unavailable=_no_battery_monitor,
        decimals=2,
        pinned=True,
    ),
    Field(
        "battery_current",
        "Current",
        "A",
        "SYS_STATUS.current_battery",
        units.centiamps_to_amps,
        unavailable=_no_battery_monitor,
    ),
    Field(
        "battery_remaining",
        "Battery left",
        "%",
        "SYS_STATUS.battery_remaining",
        units.percent,
        unavailable=_no_battery_monitor,
        decimals=0,
        warn=Limit(below=30),
        crit=Limit(below=15),
    ),
    Field(
        "battery_consumed",
        "Consumed",
        "mAh",
        "BATTERY_STATUS.current_consumed",
        units.milliamp_hours,
        decimals=0,
    ),
    # GPS. Without a 3D fix there is no usable position; ArduPilot's EKF3 rejects GPS
    # with fewer than 6 satellites or HDOP above 2.5 (AP_NavEKF3_VehicleStatus.cpp).
    Field(
        "gps_fix",
        "GPS",
        "",
        "GPS_RAW_INT.fix_type",
        text=_gps_fix_text,
        crit=Limit(below=mavlink.GPS_FIX_TYPE_3D_FIX),
        pinned=True,
    ),
    Field(
        "gps_sats",
        "Satellites",
        "",
        "GPS_RAW_INT.satellites_visible",
        units.satellites,
        decimals=0,
        crit=Limit(below=6),
    ),
    Field(
        "gps_hdop",
        "HDOP",
        "",
        "GPS_RAW_INT.eph",
        units.eph_to_hdop,
        decimals=2,
        crit=Limit(above=2.5),
    ),
    # Vehicle state, from its HEARTBEAT (1 Hz, so stale after three missed ones)
    Field("mode", "Mode", "", None, stale_after_s=3.0),
    Field("armed", "Armed", "", None, text=_armed_text, stale_after_s=3.0),
    # Link quality, worked out by the GCS on every snapshot. The loss margins are ours.
    Field("heartbeat_age", "Heartbeat age", "s", None),
    Field("link_loss", "Link loss", "%", None, warn=Limit(above=5), crit=Limit(above=20)),
    Field("link_rate", "Link rate", "msg/s", None, decimals=0),
)
