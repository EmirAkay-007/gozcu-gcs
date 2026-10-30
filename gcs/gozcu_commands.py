"""Commands to Gözcü, as docs/message_contract.md defines them: COMMAND_LONG with
MAV_CMD_USER_1, one at a time, sent again while no answer comes, and followed to
Gözcü's COMMAND_ACK.

A command only ever goes to Gözcü's component, never to the autopilot: the ground
station asks, the MCU decides. request() may be called from any thread; the rest
runs in the MAVLink source's thread, which owns the link.
"""

import queue
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gcs.gozcu import MODES
from gcs.mav import enum_name, mavlink

ACK_TIMEOUT_S = 1.0  # no answer this long: send the same command again
MAX_SENDS = 3

ACTIONS = {"mode": 0, "decide": 1, "perception": 2}  # -> COMMAND_LONG param1
VALUES = {"mode": (0, 1, 2), "decide": (0, 1), "perception": (0, 1)}  # allowed param2
DENIALS = {  # COMMAND_ACK.result_param2 when DENIED
    1: "switch on INHIBIT",
    2: "no such proposal waiting",
    3: "not in CONFIRM mode",
    4: "value out of range",
    5: "in safe mode",
}

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
    value: int
    proposal: int  # the proposal a decision is about; 0 otherwise
    asked: float  # when the operator asked
    sends: int = 0
    deadline: float = 0.0  # when to send again, or give up
    state: str = PENDING
    detail: str | None = None
    finished: float | None = None

    def describe(self) -> str:
        if self.action == "mode":
            return f"mode {MODES[self.value]}"
        if self.action == "decide":
            return f"{'approve' if self.value else 'reject'} proposal {self.proposal}"
        return f"perception {'RUN' if self.value else 'PAUSE'}"


class GozcuCommands:
    """send(command) puts a command on the link; event(severity, text) logs."""

    def __init__(self, send: Callable[[Command], None], event: Event) -> None:
        self._send = send
        self._event = event
        self._requests: queue.SimpleQueue[tuple[Any, Any, Any]] = queue.SimpleQueue()
        self._current: Command | None = None  # sent, waiting for its answer
        self._latest: Command | None = None  # the newest one, for the panel

    def request(self, action: Any, value: Any, proposal: Any = 0) -> None:
        """Ask for a command, from any thread. Checked when its turn comes; the outcome
        is published, never raised."""
        self._requests.put((action, value, proposal))

    def poll(self, now: float, unreachable: str | None) -> None:
        """Send again or give up on the command in flight, then start the next one.

        unreachable is why Gözcü cannot be reached now, or None when it can.
        """
        command = self._current
        if command is not None and unreachable is not None:
            self._finish(command, NO_ANSWER, unreachable, now)
        elif command is not None and now >= command.deadline:
            if command.sends < MAX_SENDS:
                self._transmit(command, now)
            else:
                self._finish(command, NO_ANSWER, None, now)
        if self._current is not None:
            return
        try:
            action, value, proposal = self._requests.get_nowait()
        except queue.Empty:
            return
        command = self._make(action, value, proposal, now)
        if command is None:
            return
        self._latest = command
        if unreachable is not None:
            self._finish(command, NOT_SENT, unreachable, now)
            return
        self._event("INFO", f"asking Gözcü: {command.describe()}")
        self._current = command
        self._transmit(command, now)

    def on_ack(self, msg: Any, from_gozcu: bool, now: float) -> None:
        """A COMMAND_ACK for MAV_CMD_USER_1 addressed to us, from Gözcü or the autopilot."""
        command = self._current
        if command is None:
            return  # a late answer to a command already finished
        if not from_gozcu:
            # With no route to Gözcü, the autopilot handles the command itself.
            self._finish(command, NO_ROUTE, "the autopilot answered: no route to Gözcü", now)
        elif msg.result == mavlink.MAV_RESULT_IN_PROGRESS:
            command.deadline = now + ACK_TIMEOUT_S  # still working on it: wait, do not resend
        elif msg.result == mavlink.MAV_RESULT_ACCEPTED:
            self._finish(command, ACCEPTED, None, now)
        elif msg.result == mavlink.MAV_RESULT_DENIED:
            reason = DENIALS.get(msg.result_param2, f"reason {msg.result_param2}")
            self._finish(command, DENIED, reason, now)
        elif msg.result == mavlink.MAV_RESULT_TEMPORARILY_REJECTED:
            self._finish(command, BUSY, "busy, try again", now)
        else:
            self._finish(command, enum_name("MAV_RESULT", msg.result), None, now)

    def snapshot(self, now: float) -> dict[str, Any] | None:
        """The newest command and what became of it, for the browser."""
        command = self._latest
        if command is None:
            return None
        since = command.finished if command.finished is not None else command.asked
        return {
            "action": command.action,
            "value": command.value,
            "proposal": command.proposal,
            "text": command.describe(),
            "state": command.state,
            "detail": command.detail,
            "sends": command.sends,
            "age": round(now - since, 2),
        }

    def _make(self, action: Any, value: Any, proposal: Any, now: float) -> Command | None:
        allowed = VALUES.get(action) if isinstance(action, str) else None
        valid = (
            allowed is not None
            and type(value) is int  # not bool, not float
            and value in allowed
            and type(proposal) is int
            and (proposal > 0 if action == "decide" else proposal == 0)
        )
        if not valid:
            self._event(
                "WARNING", f"ignored a malformed command: {action!r}, {value!r}, {proposal!r}"
            )
            return None
        return Command(action, value, proposal, asked=now)

    def _transmit(self, command: Command, now: float) -> None:
        command.sends += 1
        command.deadline = now + ACK_TIMEOUT_S
        self._send(command)

    def _finish(self, command: Command, state: str, detail: str | None, now: float) -> None:
        command.state, command.detail, command.finished = state, detail, now
        if command is self._current:
            self._current = None
        what = command.describe()
        if state == ACCEPTED:
            self._event("INFO", f"Gözcü accepted: {what}")
        elif state == NOT_SENT:
            self._event("WARNING", f"not sent: {what} ({detail})")
        elif state == NO_ANSWER:
            self._event(
                "WARNING", f"no answer from Gözcü: {what}" + (f" ({detail})" if detail else "")
            )
        elif state == NO_ROUTE:
            self._event("WARNING", f"not delivered: {what} ({detail})")
        else:
            self._event("WARNING", f"Gözcü refused: {what} ({detail or state})")
