"""Commands to the autopilot: set the flight mode, arm or disarm, fly to a point
(gcs.commands sends them and follows them to the COMMAND_ACK).

The autopilot checks each one as it checks any ground station's: its pre-arm
checks before arming, and ArduPlane refuses a disarm from the ground while it is
flying. Nothing is ever forced. The MAVLink source holds them all while Gözcü
avoids or waits for the operator (gcs.gozcu.GozcuState.holding), so nothing sent
from here works against an avoidance manoeuvre.
"""

import math
from collections.abc import Callable
from typing import Any

from gcs.commands import Command, CommandQueue, Event
from gcs.mav import mavlink

# The modes offered, with ArduPlane's numbers for them (ArduPlane/mode.h). MANUAL is
# left out: it hands the control surfaces straight to the sticks, the RC pilot's job.
MODES = {
    "FBWA": 5,
    "CRUISE": 7,
    "LOITER": 12,
    "RTL": 11,
    "AUTO": 10,
    "TAKEOFF": 13,
    "GUIDED": 15,
    "AUTOLAND": 26,
}
FLY_ALT_M = (20, 500)  # the altitudes above home "fly here" accepts, against typing slips

COMMANDS = {  # action -> MAV_CMD
    "mode": mavlink.MAV_CMD_DO_SET_MODE,
    "arm": mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
    "fly_to": mavlink.MAV_CMD_DO_REPOSITION,
}


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)  # not bool


def valid(action: Any, args: Any) -> bool:
    """Whether a request is a command this module knows, with the arguments it needs."""
    if not isinstance(args, dict):
        return False
    if action == "mode":
        return args.keys() == {"mode"} and isinstance(args["mode"], str) and args["mode"] in MODES
    if action == "arm":
        return args.keys() == {"arm"} and type(args["arm"]) is bool
    if action == "fly_to":
        if args.keys() != {"lat", "lon", "alt"} or not all(map(_is_number, args.values())):
            return False
        low, high = FLY_ALT_M
        return abs(args["lat"]) <= 90 and abs(args["lon"]) <= 180 and low <= args["alt"] <= high
    return False


def describe(action: str, args: dict[str, Any]) -> str:
    if action == "mode":
        return f"mode {args['mode']}"
    if action == "arm":
        return "arm" if args["arm"] else "disarm"
    return f"fly to {args['lat']:.6f}, {args['lon']:.6f} at {args['alt']:g} m"


class VehicleCommands(CommandQueue):
    """send(command) puts a command on the link; event(severity, text) logs."""

    def __init__(self, send: Callable[[Command], None], event: Event) -> None:
        super().__init__("the autopilot", send, event)

    def request(self, action: Any, args: Any) -> None:
        """Ask for a command, from any thread: ("mode", {"mode": "RTL"}), ("arm",
        {"arm": True}) or ("fly_to", {"lat", "lon", "alt": metres above home}).
        Checked when its turn comes; the outcome is published, never raised."""
        self._requests.put((action, args))

    def on_ack(self, msg: Any, now: float) -> None:
        """A COMMAND_ACK from the autopilot, addressed to us."""
        command = self._current
        if command is None or msg.command != COMMANDS[command.action]:
            return  # a late answer, or one about another command
        self._answered(command, msg.result, now)

    def _make(self, request: tuple[Any, ...], now: float) -> Command | None:
        action, args = request
        if not valid(action, args):
            self._malformed(request)
            return None
        return Command(action, dict(args), describe(action, args), asked=now)
