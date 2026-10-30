import os
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gcs.mav import LinkClosed, check_connection, enum_name, mavlink, open_link, serial_ports


def test_mavlink2_is_selected():
    assert mavlink.WIRE_PROTOCOL_VERSION == "2.0"


def test_mavlink2_even_when_pymavlink_was_imported_first():
    env = {key: value for key, value in os.environ.items() if key != "MAVLINK20"}
    code = (
        "from pymavlink import mavutil\n"
        "assert mavutil.mavlink.WIRE_PROTOCOL_VERSION == '1.0'\n"
        "import gcs.mav\n"
        "assert gcs.mav.mavlink.WIRE_PROTOCOL_VERSION == '2.0'\n"
        "assert gcs.mav.mavlink is mavutil.mavlink\n"
    )
    subprocess.run([sys.executable, "-E", "-c", code], env=env, check=True)


def test_enum_names_lose_their_prefix():
    assert enum_name("MAV_RESULT", mavlink.MAV_RESULT_DENIED) == "DENIED"
    assert enum_name("MAV_RESULT", 250) == "250"  # unknown values stay numbers


def test_a_tcp_peer_closing_raises_link_closed():
    with socket.create_server(("127.0.0.1", 0)) as server:
        conn = open_link(f"tcp:127.0.0.1:{server.getsockname()[1]}", 250, 190)
        peer, _ = server.accept()
        peer.close()
        with pytest.raises(LinkClosed):
            conn.recv_match(blocking=True, timeout=2.0)
        conn.close()


def test_tcp_connect_fails_at_once_when_nobody_listens():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    with pytest.raises(OSError):
        open_link(f"tcp:127.0.0.1:{port}", 250, 190)


@pytest.mark.parametrize(
    "text",
    [
        "tcp:127.0.0.1:5762",
        " udpin:0.0.0.0:14551 ",
        "/dev/ttyUSB0,57600",
        "/dev/serial/by-id/usb-Silicon_Labs_CP2102-if00-port0,115200",
        "COM3,57600",
    ],
)
def test_links_an_operator_may_choose(text):
    assert check_connection(text) == text.strip()


@pytest.mark.parametrize(
    "text",
    ["", "/etc/passwd", "/dev/../etc/passwd", "tcp:127.0.0.1:70000", "flight.tlog", "mcast:"],
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError):
        check_connection(text)


def test_serial_ports_leave_out_the_placeholders(monkeypatch):
    from serial.tools import list_ports

    present = [
        SimpleNamespace(device="/dev/ttyS4", description="n/a", hwid="n/a"),
        SimpleNamespace(device="/dev/ttyUSB0", description="CP2102", hwid="USB VID:PID=10C4:EA60"),
        SimpleNamespace(device="/dev/ttyACM0", description="Pixhawk", hwid="USB VID:PID=26AC:0011"),
    ]
    monkeypatch.setattr(list_ports, "comports", lambda: present)
    assert serial_ports() == [
        {"device": "/dev/ttyACM0", "description": "Pixhawk"},
        {"device": "/dev/ttyUSB0", "description": "CP2102"},
    ]
