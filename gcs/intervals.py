"""Asking the autopilot for message rates with MAV_CMD_SET_MESSAGE_INTERVAL.

COMMAND_ACK does not say which message an interval request was about. So only
one request is outstanding at a time, and every ACK is matched to the oldest
copy we sent: ArduPilot answers each copy, in order, on the link it came in on.
A lost ACK only costs a resend; the results stay right because every copy of a
request gets the same answer.

ArduPilot keeps these rates in RAM, per link, so call start() again after a
reconnect or a vehicle reboot.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gcs.mav import enum_name, mavlink

ACK_TIMEOUT_S = 1.5
MAX_ATTEMPTS = 3
NO_ACK = "NO ACK"


def message_id(name: str) -> int:
    """MAVLink message id for a name such as "ATTITUDE"."""
    try:
        return getattr(mavlink, f"MAVLINK_MSG_ID_{name}")
    except AttributeError:
        raise ValueError(f"unknown MAVLink message: {name}") from None


@dataclass
class IntervalRequest:
    """One message-rate request and what became of it."""

    name: str  # MAVLink message name, e.g. "ATTITUDE"
    rate_hz: float
    attempts: int = 0
    sent_at: float = 0.0
    result: str | None = None  # MAV_RESULT name such as "ACCEPTED", or NO_ACK


def send_interval_request(mav: Any, target: tuple[int, int], request: IntervalRequest) -> None:
    """Transmit one copy of a request through a pymavlink MAVLink object."""
    mav.command_long_send(
        target[0],
        target[1],
        mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
        request.attempts,  # "confirmation": 0 the first time, +1 on each resend
        message_id(request.name),
        1e6 / request.rate_hz,  # interval in microseconds
        0,
        0,
        0,
        0,
        0,
    )


class IntervalRequester:
    """Sends the requests one by one, resends unanswered ones and records the answers."""

    def __init__(self, rates_hz: dict[str, float], send: Callable[[IntervalRequest], None]) -> None:
        for name in rates_hz:
            message_id(name)  # fail early on a typo
        self.requests = [IntervalRequest(name, hz) for name, hz in rates_hz.items()]
        self._send = send
        self._queued: deque[IntervalRequest] = deque()
        self._in_flight: deque[IntervalRequest] = deque()  # one entry per copy sent
        self._current: IntervalRequest | None = None

    def start(self, now: float) -> None:
        """(Re)send every request, e.g. after locking onto a vehicle or after it rebooted."""
        for request in self.requests:
            request.attempts = 0
            request.result = None
        self._queued = deque(self.requests)
        self._in_flight.clear()
        self._current = None
        self._send_next(now)

    def poll(self, now: float) -> None:
        """Resend an unanswered request, or give up on it after MAX_ATTEMPTS."""
        request = self._current
        if request is None or now - request.sent_at < ACK_TIMEOUT_S:
            return
        if request.attempts < MAX_ATTEMPTS:
            self._transmit(request, now)
        else:
            request.result = NO_ACK
            self._current = None
            self._send_next(now)

    def on_ack(self, result: int, now: float) -> None:
        """Record the MAV_RESULT of a SET_MESSAGE_INTERVAL COMMAND_ACK addressed to us."""
        if not self._in_flight:
            return  # nothing outstanding: a stray answer
        request = self._in_flight.popleft()
        if request.result not in (None, NO_ACK):
            return  # the answer to a resent copy; the first answer already counted
        request.result = enum_name("MAV_RESULT", result)
        if request is self._current:
            self._current = None
            self._send_next(now)

    def _send_next(self, now: float) -> None:
        if self._queued:
            self._current = self._queued.popleft()
            self._transmit(self._current, now)

    def _transmit(self, request: IntervalRequest, now: float) -> None:
        self._send(request)
        request.attempts += 1
        request.sent_at = now
        self._in_flight.append(request)
