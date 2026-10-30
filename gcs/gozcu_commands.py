"""Commands to Gözcü, as docs/message_contract.md defines them: COMMAND_LONG with
MAV_CMD_USER_1, one at a time, sent again while no answer comes, and followed to
Gözcü's COMMAND_ACK (gcs.commands).

A command only ever goes to Gözcü's component, never to the autopilot: the ground
station asks, the MCU decides.
"""

from collections.abc import Callable
from typing import Any

from gcs.commands import DENIED, NO_ROUTE, Command, CommandQueue, Event
from gcs.gozcu import MODES
from gcs.mav import mavlink

ACTIONS = {"mode": 0, "decide": 1, "perception": 2}  # -> COMMAND_LONG param1
VALUES = {"mode": (0, 1, 2), "decide": (0, 1), "perception": (0, 1)}  # allowed param2
DENIALS = {  # COMMAND_ACK.result_param2 when DENIED
    1: "switch on INHIBIT",
    2: "no such proposal waiting",
    3: "not in CONFIRM mode",
    4: "value out of range",
    5: "in safe mode",
}


def describe(action: str, value: int, proposal: int) -> str:
    if action == "mode":
        return f"mode {MODES[value]}"
    if action == "decide":
        return f"{'approve' if value else 'reject'} proposal {proposal}"
    return f"perception {'RUN' if value else 'PAUSE'}"


class GozcuCommands(CommandQueue):
    """send(command) puts a command on the link; event(severity, text) logs. A
    command's args are its value and the proposal a decision is about (0 otherwise)."""

    def __init__(self, send: Callable[[Command], None], event: Event) -> None:
        super().__init__("Gözcü", send, event)

    def request(self, action: Any, value: Any, proposal: Any = 0) -> None:
        """Ask for a command, from any thread. Checked when its turn comes; the outcome
        is published, never raised."""
        self._requests.put((action, value, proposal))

    def on_ack(self, msg: Any, from_gozcu: bool, now: float) -> None:
        """A COMMAND_ACK for MAV_CMD_USER_1 addressed to us, from Gözcü or the autopilot."""
        command = self._current
        if command is None:
            return  # a late answer to a command already finished
        if not from_gozcu:
            # With no route to Gözcü, the autopilot handles the command itself.
            self._finish(command, NO_ROUTE, "the autopilot answered: no route to Gözcü", now)
        elif msg.result == mavlink.MAV_RESULT_DENIED:
            reason = DENIALS.get(msg.result_param2, f"reason {msg.result_param2}")
            self._finish(command, DENIED, reason, now)
        else:
            self._answered(command, msg.result, now)

    def _make(self, request: tuple[Any, ...], now: float) -> Command | None:
        action, value, proposal = request
        allowed = VALUES.get(action) if isinstance(action, str) else None
        valid = (
            allowed is not None
            and type(value) is int  # not bool, not float
            and value in allowed
            and type(proposal) is int
            and (proposal > 0 if action == "decide" else proposal == 0)
        )
        if not valid:
            self._malformed(request)
            return None
        args = {"value": value, "proposal": proposal}
        return Command(action, args, describe(action, value, proposal), asked=now)
