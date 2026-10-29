"""Unit conversions for raw MAVLink fields.

MAVLink marks a value it does not have with a reserved number: UINT8_MAX or
UINT16_MAX for most unsigned fields, -1 for some signed ones. The converters
return None for those, so "unknown" can never show up as a measurement such as
65.535 V.
"""

import math

UINT8_UNKNOWN = 255
UINT16_UNKNOWN = 65535
EARTH_RADIUS_M = 6_371_008.8  # mean radius (IUGG)


def mm_to_m(value: int) -> float:
    return value / 1000


def rad_to_deg(value: float) -> float:
    return math.degrees(value)


def rad_to_heading(value: float) -> float:
    """A yaw angle in radians (-pi..pi) as a compass heading, 0..360 degrees."""
    return math.degrees(value) % 360


def deg_e7_to_deg(value: int) -> float:
    """Latitude/longitude sent as degrees x 10^7."""
    return value / 1e7


def millivolts_to_volts(value: int) -> float | None:
    return None if value == UINT16_UNKNOWN else value / 1000


def centiamps_to_amps(value: int) -> float | None:
    return None if value == -1 else value / 100


def percent(value: int) -> int | None:
    """A percentage where -1 means unknown, such as battery_remaining."""
    return None if value < 0 else value


def milliamp_hours(value: int) -> int | None:
    """Consumed charge in mAh; -1 means not measured."""
    return None if value < 0 else value


def satellites(value: int) -> int | None:
    return None if value == UINT8_UNKNOWN else value


def eph_to_hdop(value: int) -> float | None:
    """GPS_RAW_INT.eph carries the horizontal dilution of precision x 100."""
    return None if value == UINT16_UNKNOWN else value / 100


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two points given in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
