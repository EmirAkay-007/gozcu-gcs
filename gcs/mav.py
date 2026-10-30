"""The only place where this project imports pymavlink.

Without MAVLINK20 in the environment pymavlink starts on MAVLink 1 and switches
a link to MAVLink 2 only if the first frame it receives there is MAVLink 2.
Until then our own frames go out as MAVLink 1, which has no extension fields and
no message ids above 255, and a link whose first frame looks like MAVLink 1
never switches. Setting MAVLINK20 before the first import avoids all of that,
so the rest of the project imports pymavlink only through here.
"""

import os
import re

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402  (MAVLINK20 must be set before this import)

if mavutil.mavlink.WIRE_PROTOCOL_VERSION != "2.0":
    # Something imported pymavlink before us without MAVLINK20: reload as MAVLink 2.
    mavutil.set_dialect(mavutil.current_dialect)

mavlink = mavutil.mavlink


def enum_name(enum: str, value: int) -> str:
    """Enum entry without its prefix, e.g. enum_name("MAV_RESULT", 0) == "ACCEPTED"."""
    entry = mavlink.enums[enum].get(value)
    return entry.name.removeprefix(enum + "_") if entry else str(value)


class LinkClosed(ConnectionError):
    """A stream (TCP) MAVLink connection is gone."""


class _TcpLink(mavutil.mavtcp):
    """pymavlink's TCP client, made to report a dead connection.

    Stock mavtcp only prints "EOF on TCP socket" when the peer goes away and
    then keeps returning empty reads, so a closed link looks exactly like a
    quiet one. Here a closed or broken connection raises LinkClosed. (pymavlink
    sets self.address to "tcp:host:port".)
    """

    def handle_eof(self) -> None:
        raise LinkClosed(f"{self.address}: closed by the other end")

    def handle_disconnect(self) -> None:
        raise LinkClosed(f"{self.address}: connection reset")

    def recv(self, n: int | None = None) -> bytes:
        try:
            return super().recv(n)
        except LinkClosed:
            raise
        except OSError as exc:
            raise LinkClosed(f"{self.address}: {exc}") from exc


# The connections an operator may choose from the browser: a network link, or a serial
# port with an optional baud rate. pymavlink would open more (a log file, for one).
_NETWORK = re.compile(r"(tcp|tcpin|udp|udpin|udpout|udpbcast):([A-Za-z0-9.-]+):(\d{1,5})")
_SERIAL = re.compile(r"(/dev/[\w.-]+(/[\w.-]+)*|COM\d{1,3})(,\d{3,7})?")


def check_connection(text: str) -> str:
    """text, stripped, if it names a network link or a serial port; else ValueError."""
    text = text.strip()
    network = _NETWORK.fullmatch(text)
    if network and 1 <= int(network[3]) <= 65535:
        return text
    if _SERIAL.fullmatch(text) and ".." not in text:
        return text
    raise ValueError(
        f"{text!r} is not a connection: try tcp:HOST:PORT, udpin:0.0.0.0:PORT "
        "or a serial port such as /dev/ttyUSB0,57600"
    )


_LEGACY_UART = re.compile(r"/dev/ttyS\d+")


def serial_ports() -> list[dict[str, str]]:
    """The serial ports present now (telemetry radios, USB adapters), leaving out the
    dozens of /dev/ttyS placeholders that Linux keeps with no hardware behind them."""
    from serial.tools import list_ports  # pyserial, as pymavlink uses for serial links

    ports = [
        port
        for port in list_ports.comports()
        if not (_LEGACY_UART.fullmatch(port.device) and port.hwid == "n/a")
    ]
    ports.sort(key=lambda port: port.device)
    return [{"device": port.device, "description": port.description} for port in ports]


def open_link(url: str, sysid: int, compid: int) -> mavutil.mavfile:
    """Open a MAVLink connection that identifies itself as (sysid, compid).

    Over TCP, a closed or broken connection raises LinkClosed: on receive for
    any socket error, on send for a reset or a broken pipe. pymavlink swallows
    other send errors, so those are caught on the receive side or by the
    heartbeat timeout, as is a peer that vanishes without closing. Use a
    connection from one thread only: pymavlink's send path is not thread-safe.
    """
    if url.startswith("tcp:"):
        # A single attempt: when and how often to reconnect is the caller's call.
        address = url.removeprefix("tcp:")
        return _TcpLink(address, source_system=sysid, source_component=compid, retries=1)
    return mavutil.mavlink_connection(url, source_system=sysid, source_component=compid)
