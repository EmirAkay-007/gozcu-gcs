"""MAVLink's command protocol, for one destination: a command is sent, sent again
while no COMMAND_ACK comes, and given up after a few tries; only one is in flight,
the next waits its turn.

Gözcü (gcs.gozcu_commands) and the autopilot (gcs.vehicle_commands) each have a
queue of their own, so a command to one never waits for the other. request() may
be called from any thread; the rest runs in the MAVLink source's thread, which
owns the link.
"""

import queue
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gcs.mav import enum_name, mavlink

ACK_TIMEOUT_S = 1.0  # no answer this long: send the same command again
MAX_SENDS = 3

# What became of a command, as the snapshot shows it
PENDING = "PENDING"
ACCEPTED = "ACCEPTED"
DENIED = "DENIED"
BUSY = "BUSY"
NO_ANSWER = "NO ANSWER"
NO_ROUTE = "NO ROUTE"
NOT_SENT = "NOT SENT"

Event = Callable[[str, str], None]  # (severity, text)


@dataclass
class Command:
    action: str
    args: dict[str, Any]  # what the action needs, e.g. {"mode": "RTL"}
    text: str  # what the operator asked for, in words: "mode RTL"
    asked: float  # when the operator asked
    sends: int = 0
    deadline: float = 0.0  # when to send again, or give up
    state: str = PENDING
    detail: str | None = None
    finished: float | None = None


class CommandQueue:
    """Commands to one destination, called who in the events ("Gözcü").

    send(command) puts a command on the link; event(severity, text) logs. A
    subclass takes requests from any thread into self._requests, checks them when
    their turn comes (_make) and passes the answers on (_answered).
    """

    def __init__(self, who: str, send: Callable[[Command], None], event: Event) -> None:
        self._who = who
        self._send = send
        self._event = event
        self._requests: queue.SimpleQueue[tuple[Any, ...]] = queue.SimpleQueue()
        self._current: Command | None = None  # sent, waiting for its answer
        self._latest: Command | None = None  # the newest one, for the panel

    def poll(self, now: float, unreachable: str | None, held: str | None = None) -> None:
        """Send again or give up on the command in flight, then start the next one.

        unreachable is why the destination cannot be reached now: the command in
        flight is given up. held is why no command may go now, though it can be
        reached: the one in flight is not sent again, but its answer still counts.
        Both are None when commands may go.
        """
        command = self._current
        if command is not None and unreachable is not None:
            self._finish(command, NO_ANSWER, unreachable, now)
        elif command is not None and now >= command.deadline:
            if command.sends < MAX_SENDS and held is None:
                self._transmit(command, now)
            else:
                self._finish(command, NO_ANSWER, held, now)
        if self._current is not None:
            return
        try:
            request = self._requests.get_nowait()
        except queue.Empty:
            return
        command = self._make(request, now)
        if command is None:
            return
        self._latest = command
        if unreachable is not None or held is not None:
            self._finish(command, NOT_SENT, unreachable or held, now)
            return
        self._event("INFO", f"asking {self._who}: {command.text}")
        self._current = command
        self._transmit(command, now)

    def snapshot(self, now: float) -> dict[str, Any] | None:
        """The newest command and what became of it, for the browser."""
        command = self._latest
        if command is None:
            return None
        since = command.finished if command.finished is not None else command.asked
        return {
            "action": command.action,
            **command.args,
            "text": command.text,
            "state": command.state,
            "detail": command.detail,
            "sends": command.sends,
            "age": round(now - since, 2),
        }

    def _make(self, request: tuple[Any, ...], now: float) -> Command | None:
        """The command asked for, or None (and a warning) if the request is malformed."""
        raise NotImplementedError

    def _malformed(self, request: tuple[Any, ...]) -> None:
        self._event("WARNING", f"ignored a malformed command: {', '.join(map(repr, request))}")

    def _answered(self, command: Command, result: int, now: float) -> None:
        """The destination's COMMAND_ACK for the command in flight."""
        if result == mavlink.MAV_RESULT_IN_PROGRESS:
            command.deadline = now + ACK_TIMEOUT_S  # still working on it: wait, do not resend
        elif result == mavlink.MAV_RESULT_ACCEPTED:
            self._finish(command, ACCEPTED, None, now)
        elif result == mavlink.MAV_RESULT_TEMPORARILY_REJECTED:
            self._finish(command, BUSY, "busy, try again", now)
        else:
            self._finish(command, enum_name("MAV_RESULT", result), None, now)

    def _transmit(self, command: Command, now: float) -> None:
        command.sends += 1
        command.deadline = now + ACK_TIMEOUT_S
        self._send(command)

    def _finish(self, command: Command, state: str, detail: str | None, now: float) -> None:
        command.state, command.detail, command.finished = state, detail, now
        if command is self._current:
            self._current = None
        what, who = command.text, self._who
        if state == ACCEPTED:
            self._event("INFO", f"{who} accepted: {what}")
        elif state == NOT_SENT:
            self._event("WARNING", f"not sent: {what} ({detail})")
        elif state == NO_ANSWER:
            why = f" ({detail})" if detail else ""
            self._event("WARNING", f"no answer from {who}: {what}{why}")
        elif state == NO_ROUTE:
            self._event("WARNING", f"not delivered: {what} ({detail})")
        else:
            self._event("WARNING", f"{who} refused: {what} ({detail or state})")
