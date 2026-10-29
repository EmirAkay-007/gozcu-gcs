"""The only place where this project imports pymavlink.

Without MAVLINK20 in the environment pymavlink starts on MAVLink 1 and switches
a link to MAVLink 2 only if the first frame it receives there is MAVLink 2.
Until then our own frames go out as MAVLink 1, which has no extension fields and
no message ids above 255, and a link whose first frame looks like MAVLink 1
never switches. Setting MAVLINK20 before the first import avoids all of that,
so the rest of the project imports pymavlink only through here.
"""

import os

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
