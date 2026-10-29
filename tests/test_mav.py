import os
import socket
import subprocess
import sys

import pytest

from gcs.mav import LinkClosed, enum_name, mavlink, open_link


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
