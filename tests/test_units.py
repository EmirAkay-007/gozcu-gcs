import math

import pytest

from gcs import units


@pytest.mark.parametrize(
    ("convert", "marker"),
    [
        (units.millivolts_to_volts, 65535),
        (units.centiamps_to_amps, -1),
        (units.percent, -1),
        (units.milliamp_hours, -1),
        (units.satellites, 255),
        (units.eph_to_hdop, 65535),
    ],
)
def test_unknown_markers_become_none(convert, marker):
    assert convert(marker) is None


def test_conversions():
    assert units.millivolts_to_volts(12600) == pytest.approx(12.6)
    assert units.centiamps_to_amps(1550) == pytest.approx(15.5)
    assert units.mm_to_m(-2500) == -2.5
    assert units.eph_to_hdop(121) == pytest.approx(1.21)
    assert units.deg_e7_to_deg(399035714) == pytest.approx(39.9035714)
    assert units.percent(0) == 0  # an empty battery, not an unknown one


def test_yaw_becomes_a_compass_heading():
    assert units.rad_to_heading(-math.pi / 2) == pytest.approx(270.0)
    assert units.rad_to_heading(math.pi / 2) == pytest.approx(90.0)


def test_one_degree_of_latitude():
    assert units.haversine_m(0, 0, 1, 0) == pytest.approx(111_195, rel=1e-4)


def test_distance_to_the_same_point_is_zero():
    assert units.haversine_m(39.9, 41.2, 39.9, 41.2) == 0.0
