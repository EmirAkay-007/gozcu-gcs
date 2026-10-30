"""A stand-in for Gözcü (the MCU) that speaks docs/message_contract.md, for testing the
ground station without the board, and the board's output against it.

It connects where the MCU would, to a serial port of the autopilot (in SITL the
second one, tcp:127.0.0.1:5763), as component 191 of the vehicle's system, so the
autopilot routes its traffic just as it will the MCU's. It sends the heartbeat,
status, track frames, proposals and texts a scenario calls for, and answers
commands as the MCU should: it refuses AUTO while the switch is on INHIBIT or in
safe mode, a decision outside CONFIRM mode, and a decision about any proposal but
the one waiting. It is written from the contract rather than from the ground
station's code, so that each can catch the other's mistakes.

Run it from the repository root, while SITL runs:

    .venv/bin/python -E tools/fake_gozcu.py --scenario encounter

Scenarios:
  encounter   a drone closes in; at tau 10 s a climb is proposed (in CONFIRM mode it
              waits 5 s for the operator); the drone passes; repeats every 30 s
  clear       nothing in sight
  mpu-silent  safe mode: the MPU stopped answering the MCU
  stale       safe mode: the newest perception result is too old
  silent      the MCU sends nothing at all, and answers nothing
  cycle       encounter, mpu-silent, stale and silent in turn
"""

import argparse
import math
import sys
import time
from dataclasses import dataclass
from typing import Any

from gcs.mav import enum_name, mavlink, open_link

COMPONENT = 191  # MAV_COMP_ID_ONBOARD_COMPUTER

# Codes, as docs/message_contract.md defines them
CLEAR, ADVISORY, AVOID, CLEAR_OF_CONFLICT = 0, 1, 2, 3
WARN, CONFIRM, AUTO = 0, 1, 2
MODE_NAMES = {"warn": WARN, "confirm": CONFIRM, "auto": AUTO}
INHIBIT, ARM = 0, 1
CLIMB = 1
WAITING, APPROVED, REFUSED, REJECTED, EXPIRED = 0, 1, 2, 3, 4
WHY_INHIBIT = 4
DENY_INHIBIT, DENY_NO_PROPOSAL, DENY_NOT_CONFIRM, DENY_RANGE, DENY_SAFE_MODE = 1, 2, 3, 4, 5
FAULT_MPU, FAULT_STALE = 1, 2
DRONE, BIRD = 2, 3

ENCOUNTER_S = 30.0  # one encounter, then it starts again
ADVISORY_TAU_S = 15.0
AVOID_TAU_S = 10.0
DECIDE_S = 5.0  # the operator's time to answer a proposal
KEEP_S = 5.0  # a decided proposal stays in the reports this long
CLEAR_OF_CONFLICT_S = 3.0
CYCLE = (("encounter", ENCOUNTER_S), ("mpu-silent", 10.0), ("stale", 10.0), ("silent", 8.0))
SCENARIOS = ("encounter", "clear", "mpu-silent", "stale", "silent", "cycle")

HEARTBEAT_S = 1.0
STATUS_S = 0.5  # 2 Hz
TRACKS_S = 0.25  # 4 Hz while there are tracks
NO_TRACKS_S = 1.0
STEP_S = 0.05  # how often run() calls step()


def encounter_tracks(t: float) -> list[dict[str, float]]:
    """The tracks t seconds into an encounter, the most threatening first: a drone
    closing in from 5 s on (tau 0 at 20 s), and a bird crossing, not closing."""
    tracks = []
    if 5.0 <= t < 20.0:
        p = (t - 5.0) / 15.0
        tracks.append(
            {
                "ID": 3,
                "CLS": DRONE,
                "CONF": 0.80 + 0.1 * p,
                "AZ": 14.0 - 12.0 * p,
                "EL": 2.0,
                "RATE": 0.3,
                "PX": 6.0 + 34.0 * p * p,
                "TAU": 20.0 - t,
            }
        )
    if 8.0 <= t < 16.0:
        tracks.append(
            {
                "ID": 5,
                "CLS": BIRD,
                "CONF": 0.64,
                "AZ": -25.0 + (t - 8.0),
                "EL": 7.0,
                "RATE": 1.0,
                "PX": 8.0,
                "TAU": -1.0,
            }
        )
    return tracks


def describe(action: int, value: int, proposal: int) -> str:
    if action == 0:
        modes = {WARN: "WARN", CONFIRM: "CONFIRM", AUTO: "AUTO"}
        return f"mode {modes.get(value, value)}"
    if action == 1:
        return f"{'approve' if value else 'reject'} proposal {proposal}"
    if action == 2:
        return f"perception {'RUN' if value else 'PAUSE'}"
    return f"action {action}"


@dataclass
class Proposal:
    id: int
    decision: int
    deadline: float  # for the operator's answer
    decided: float | None = None
    why: int = 0
    manoeuvre: int = CLIMB

    def waiting(self) -> bool:
        return self.decision == WAITING

    def over(self, now: float) -> bool:
        """Decided long enough ago to leave the reports."""
        return self.decided is not None and now - self.decided > KEEP_S


class FakeGozcu:
    """Gözcü's side of the contract. step() and on_command() are given the time."""

    def __init__(self, scenario: str, start: float, mode: int = CONFIRM, inhibit: bool = False):
        self.scenario = scenario
        self.mode = mode
        self.switch = INHIBIT if inhibit else ARM
        self.perception = 1
        self.watchdog_trips = 0
        self._start = start
        self._log: list[str] = []  # what to tell the person running the tool
        self._proposal: Proposal | None = None
        self._next_proposal = 7
        self._proposed_in = -1  # the encounter that has had its proposal
        self._avoided_in = -1  # the encounter in which the drone was avoided
        self._texts: list[tuple[int, str]] = []
        self._shown: tuple[Any, ...] | None = None  # what the latest status sending said
        self._fault = 0
        self._fault_since = start
        self._next = {"heartbeat": start, "status": start, "tracks": start}

    def step(self, mav: Any, now: float) -> None:
        """Send whatever is due at now; call it every STEP_S or so."""
        scenario, t, round_ = self._current(now)
        if scenario == "silent":
            return
        self._set_fault({"mpu-silent": FAULT_MPU, "stale": FAULT_STALE}.get(scenario, 0), now)
        tracks = encounter_tracks(t) if scenario == "encounter" and self.perception else []
        state, target = self._assess(tracks, t, round_, now)
        boot_ms = int((now - self._start) * 1000) % 2**32

        if now >= self._next["heartbeat"]:
            self._next["heartbeat"] = now + HEARTBEAT_S
            self._send_heartbeat(mav)
        proposal = self._proposal
        shown = (state, self.mode, self.switch, self.perception, self._fault, target)
        shown += (None,) if proposal is None else ((proposal.id, proposal.decision),)
        if now >= self._next["status"] or shown != self._shown:  # at once on a change
            self._next["status"] = now + STATUS_S
            self._shown = shown
            self._send_status(mav, boot_ms, state, target, now)
        if now >= self._next["tracks"]:
            self._next["tracks"] = now + (TRACKS_S if tracks else NO_TRACKS_S)
            self._send_tracks(mav, boot_ms, tracks)
        for severity, text in self._texts:
            mav.statustext_send(severity, text.encode())
        self._texts.clear()

    def answers(self, now: float) -> bool:
        """Whether the MCU answers commands now: not while it is silent."""
        return self._current(now)[0] != "silent"

    def on_command(self, msg: Any, now: float) -> tuple[int, int]:
        """(result, result_param2) for a MAV_CMD_USER_1 command, carried out if accepted."""
        action, value, proposal = round(msg.param1), round(msg.param2), round(msg.param3)
        result, reason = self._decide(action, value, proposal, now)
        outcome = enum_name("MAV_RESULT", result) + (f", reason {reason}" if reason else "")
        self._log.append(f"command: {describe(action, value, proposal)} -> {outcome}")
        return result, reason

    def take_log(self) -> list[str]:
        """What happened since last asked, for the person running the tool."""
        log, self._log = self._log, []
        return log

    # The scenario

    def _current(self, now: float) -> tuple[str, float, int]:
        """(the scenario now, seconds into the encounter, which encounter)."""
        elapsed = now - self._start
        scenario = self.scenario
        if scenario == "cycle":
            elapsed %= sum(seconds for _, seconds in CYCLE)
            for name, seconds in CYCLE:
                if elapsed < seconds:
                    scenario = name
                    break
                elapsed -= seconds
        return scenario, elapsed % ENCOUNTER_S, int(elapsed // ENCOUNTER_S)

    def _assess(
        self, tracks: list[dict[str, float]], t: float, round_: int, now: float
    ) -> tuple[int, int]:
        """The threat state and the track it is about, proposing as the MCU would."""
        closing = [track for track in tracks if track["TAU"] > 0]
        tau = closing[0]["TAU"] if closing else None
        proposal = self._proposal
        if proposal is not None and proposal.waiting() and now >= proposal.deadline:
            self._decide_proposal(proposal, EXPIRED, now, f"Proposal {proposal.id} expired")
        if proposal is not None and proposal.over(now):
            self._proposal = None
        if tau is not None and tau <= AVOID_TAU_S and self._proposed_in != round_:
            self._proposed_in = round_
            self._propose(now)
        executing = self._proposal is not None and self._proposal.decision == APPROVED
        if tau is not None and (executing or (tau <= AVOID_TAU_S and self.mode == WARN)):
            self._avoided_in = round_
            state = AVOID
        elif tau is not None and tau <= ADVISORY_TAU_S:
            state = ADVISORY
        elif self._avoided_in == round_ and 20.0 <= t < 20.0 + CLEAR_OF_CONFLICT_S:
            state = CLEAR_OF_CONFLICT
        else:
            state = CLEAR
        if self._shown is None or state != self._shown[0]:
            self._announce(state, closing)
        return state, int(closing[0]["ID"]) if closing else 0

    def _propose(self, now: float) -> None:
        if self.mode == WARN:
            return  # WARN only warns
        proposal = Proposal(self._next_proposal, WAITING, now + DECIDE_S)
        self._next_proposal += 1
        self._proposal = proposal
        if self.switch == INHIBIT:
            proposal.why = WHY_INHIBIT
            text = f"Proposal {proposal.id} refused: switch on INHIBIT"
            self._decide_proposal(proposal, REFUSED, now, text)
        elif self.mode == AUTO:
            text = f"Proposal {proposal.id}: climb, executing"
            self._decide_proposal(proposal, APPROVED, now, text)
        else:
            text = f"Proposal {proposal.id}: climb, approve within {DECIDE_S:g} s"
            self._text(mavlink.MAV_SEVERITY_WARNING, text)

    def _decide_proposal(self, proposal: Proposal, decision: int, now: float, text: str) -> None:
        proposal.decision, proposal.decided = decision, now
        self._text(mavlink.MAV_SEVERITY_WARNING, text)

    def _announce(self, state: int, closing: list[dict[str, float]]) -> None:
        if state == ADVISORY:
            track = closing[0]
            kind = {DRONE: "drone", BIRD: "bird"}.get(int(track["CLS"]), "object")
            text = f"ADVISORY: track {track['ID']:g} {kind}, tau {track['TAU']:.1f} s"
            self._text(mavlink.MAV_SEVERITY_WARNING, text)
        elif state == AVOID:
            proposal = self._proposal
            executing = proposal is not None and proposal.decision == APPROVED
            what = f"climbing (proposal {proposal.id})" if executing else "warning only"
            self._text(mavlink.MAV_SEVERITY_CRITICAL, f"AVOID: {what}")
        elif state == CLEAR_OF_CONFLICT:
            self._text(mavlink.MAV_SEVERITY_NOTICE, "Clear of conflict")

    def _set_fault(self, fault: int, now: float) -> None:
        if fault == self._fault:
            return
        if fault:
            self.watchdog_trips += 1
            self._fault_since = now
            what = "MPU silent" if fault == FAULT_MPU else "perception stale"
            self._text(mavlink.MAV_SEVERITY_CRITICAL, f"Safe mode: {what}")
        else:
            self._text(mavlink.MAV_SEVERITY_NOTICE, "Safe mode over")
        self._fault = fault

    # Commands

    def _decide(self, action: int, value: int, proposal_id: int, now: float) -> tuple[int, int]:
        accepted, denied = mavlink.MAV_RESULT_ACCEPTED, mavlink.MAV_RESULT_DENIED
        if action == 0:  # mode
            if value not in (WARN, CONFIRM, AUTO):
                return denied, DENY_RANGE
            if value == AUTO and self.switch == INHIBIT:
                return denied, DENY_INHIBIT
            if value == AUTO and self._fault:
                return denied, DENY_SAFE_MODE
            self.mode = value
            proposal = self._proposal
            if proposal is not None and proposal.waiting() and value == AUTO:
                self._decide_proposal(proposal, APPROVED, now, f"Proposal {proposal.id}: now AUTO")
            elif proposal is not None and proposal.waiting() and value == WARN:
                self._decide_proposal(proposal, EXPIRED, now, f"Proposal {proposal.id}: now WARN")
            return accepted, 0
        if action == 1:  # decide
            if value not in (0, 1):
                return denied, DENY_RANGE
            if self.mode != CONFIRM:
                return denied, DENY_NOT_CONFIRM
            proposal = self._proposal
            if proposal is None or not proposal.waiting() or proposal.id != proposal_id:
                return denied, DENY_NO_PROPOSAL
            decision, word = (APPROVED, "approved") if value else (REJECTED, "rejected")
            self._decide_proposal(proposal, decision, now, f"Proposal {proposal.id} {word}")
            return accepted, 0
        if action == 2:  # perception
            if value not in (0, 1):
                return denied, DENY_RANGE
            self.perception = value
            return accepted, 0
        return mavlink.MAV_RESULT_UNSUPPORTED, 0

    # Sending

    def _send_heartbeat(self, mav: Any) -> None:
        if self._fault:
            status = mavlink.MAV_STATE_CRITICAL
        elif self.switch == INHIBIT:
            status = mavlink.MAV_STATE_STANDBY
        else:
            status = mavlink.MAV_STATE_ACTIVE
        mav.heartbeat_send(
            mavlink.MAV_TYPE_ONBOARD_CONTROLLER, mavlink.MAV_AUTOPILOT_INVALID, 0, 0, status
        )

    def _send_status(self, mav: Any, boot_ms: int, state: int, target: int, now: float) -> None:
        elapsed, in_fault = now - self._start, (now - self._fault_since) * 1000
        mpu_ms = 20 + 15 * abs(math.sin(2.1 * elapsed))
        page_ms = 110 + 15 * abs(math.sin(1.7 * elapsed))
        if self._fault == FAULT_MPU:
            mpu_ms = 220 + in_fault  # the MPU has not been heard since the fault began
        if self._fault:
            page_ms = 600 + in_fault  # nor any perception result
        ints = {
            "G_VER": 1,
            "G_STATE": state,
            "G_MODE": self.mode,
            "G_ARM": self.switch,
            "G_PERC": self.perception,
            "G_FAULT": self._fault,
            "G_WDT": self.watchdog_trips,
            "G_MPU_MS": round(mpu_ms),
            "G_PAGE_MS": round(page_ms),
            "G_TGT_ID": target,
        }
        perceiving = not self._fault and self.perception
        floats = {
            "G_FPS": 9.0 + 0.4 * math.sin(0.7 * elapsed) if perceiving else 0.0,
            "G_LAT_MS": 180.0 + 25.0 * math.sin(1.3 * elapsed),
            "G_TEMP": 58.0 + 2.0 * math.sin(0.05 * elapsed),
        }
        proposal = self._proposal
        if proposal is None:
            ints["G_PROP"] = 0
        else:
            left = max(0.0, proposal.deadline - now) if proposal.waiting() else 0.0
            ints["G_MNVR"] = proposal.manoeuvre
            ints["G_DEC"] = proposal.decision
            ints["G_WHY"] = proposal.why
            ints["G_TTL_MS"] = round(left * 1000)
            ints["G_PROP"] = proposal.id
        for name, value in ints.items():
            mav.named_value_int_send(boot_ms, name.encode(), value)
        for name, value in floats.items():
            mav.named_value_float_send(boot_ms, name.encode(), value)

    def _send_tracks(self, mav: Any, boot_ms: int, tracks: list[dict[str, float]]) -> None:
        for k, track in enumerate(tracks[:3], start=1):
            for key, value in track.items():
                name = f"T{k}_{key}".encode()
                if key in ("ID", "CLS"):
                    mav.named_value_int_send(boot_ms, name, int(value))
                else:
                    mav.named_value_float_send(boot_ms, name, value)
        mav.named_value_int_send(boot_ms, b"G_NTRK", min(len(tracks), 3))  # closes the frame

    def _text(self, severity: int, text: str) -> None:
        self._texts.append((severity, text))
        self._log.append(text)


def run(conn: Any, gozcu: FakeGozcu, sysid: int, out: Any = None) -> None:
    """Serve one connection until it fails: answer commands, send what is due, and
    print what happens to out (standard output by default)."""
    out = out or sys.stdout
    next_step = time.monotonic()
    while True:
        msg = conn.recv_match(blocking=True, timeout=STEP_S / 2)
        now = time.monotonic()
        if msg is not None and _is_for_gozcu(msg, sysid) and gozcu.answers(now):
            result, reason = gozcu.on_command(msg, now)
            conn.mav.command_ack_send(
                msg.command, result, 0, reason, msg.get_srcSystem(), msg.get_srcComponent()
            )
        if now >= next_step:
            next_step = now + STEP_S
            gozcu.step(conn.mav, now)
        for line in gozcu.take_log():
            print(time.strftime("%H:%M:%S"), line, file=out, flush=True)


def _is_for_gozcu(msg: Any, sysid: int) -> bool:
    return (
        msg.get_type() == "COMMAND_LONG"
        and msg.command == mavlink.MAV_CMD_USER_1
        and msg.target_system == sysid
        and msg.target_component == COMPONENT
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stand-in for Gözcü's MCU (docs/message_contract.md)."
    )
    parser.add_argument(
        "--conn",
        default="tcp:127.0.0.1:5763",
        help="where the MCU would be: a serial port of the autopilot (default: SITL's second)",
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="encounter")
    parser.add_argument("--mode", choices=list(MODE_NAMES), default="confirm", help="to start in")
    parser.add_argument("--inhibit", action="store_true", help="the switch starts on INHIBIT")
    parser.add_argument("--sysid", type=int, default=1, help="the vehicle's system id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gozcu = FakeGozcu(args.scenario, time.monotonic(), MODE_NAMES[args.mode], args.inhibit)
    print(f"fake Gözcü {args.sysid}/{COMPONENT}, scenario {args.scenario}, on {args.conn}")
    try:
        while True:
            try:
                conn = open_link(args.conn, args.sysid, COMPONENT)
            except OSError as exc:
                print(f"cannot open {args.conn}: {exc}; trying again", flush=True)
                time.sleep(2.0)
                continue
            try:
                run(conn, gozcu, args.sysid)
            except OSError as exc:  # the link failed: open it again
                print(f"link lost: {exc}", flush=True)
                time.sleep(1.0)
            finally:
                conn.close()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
