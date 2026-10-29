"""Console link check for a MAVLink endpoint (roadmap step 1).

Connects as a ground station, locks onto the vehicle's autopilot, asks for the
message rates the GCS will use and, after --duration seconds or Ctrl+C,
reports what actually arrived. Run it from the repository root:

    .venv/bin/python -E tools/link_check.py --conn tcp:127.0.0.1:5762

(-E keeps the ROS PYTHONPATH out of the interpreter.)
"""

import argparse
import math
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any

from gcs.heartbeat import is_armed, is_vehicle_heartbeat, mode_name
from gcs.intervals import NO_ACK, IntervalRequester, send_interval_request
from gcs.link_stats import PacketLossMeter, RateMeter
from gcs.mav import enum_name, mavlink, mavutil, open_link

# What the GCS asks the autopilot for, in Hz. The UI refreshes at 10 Hz, so
# faster streams would be wasted; slowly changing values get slower rates.
REQUESTED_RATES_HZ = {
    "ATTITUDE": 10.0,
    "GLOBAL_POSITION_INT": 5.0,
    "VFR_HUD": 5.0,
    "SYS_STATUS": 2.0,
    "GPS_RAW_INT": 2.0,
    "BATTERY_STATUS": 1.0,
    "HOME_POSITION": 0.2,
}

HEARTBEAT_PERIOD_S = 1.0
STATUS_PERIOD_S = 2.0
# Long enough to hold three messages of the slowest stream.
RATE_WINDOW_S = max(10.0, 3 / min(REQUESTED_RATES_HZ.values()))
UINT16_UNKNOWN = 65535  # MAVLink's "value unknown" for uint16 fields
UINT8_UNKNOWN = 255
BATTERY_SENSOR = mavlink.MAV_SYS_STATUS_SENSOR_BATTERY


@dataclass
class Component:
    """What arrived from one (sysid, compid) on the link."""

    messages: int = 0
    heartbeat: Any = None


def fmt(value: float | None, spec: str, unit: str = "") -> str:
    return "—" if value is None else f"{value:{spec}}{unit}"


class LinkCheck:
    def __init__(self, conn: mavutil.mavfile, args: argparse.Namespace) -> None:
        self.conn = conn
        self.args = args
        self.started = time.monotonic()
        self.vehicle: tuple[int, int] | None = None
        self.mode = "?"
        self.armed = False
        self.components: dict[tuple[int, int], Component] = {}
        self.latest: dict[str, Any] = {}
        self.rates = RateMeter(window_s=RATE_WINDOW_S)
        # The window spans the whole run, so the report covers all of it.
        self.loss = PacketLossMeter(window_s=args.duration + RATE_WINDOW_S)
        self.intervals = IntervalRequester(
            REQUESTED_RATES_HZ,
            send=lambda request: send_interval_request(self.conn.mav, self.vehicle, request),
        )
        self.last_vehicle_heartbeat: float | None = None
        self.heartbeat_intervals: list[float] = []
        self.clock_first: tuple[int, float] | None = None  # (time_boot_ms, wall time)
        self.clock_last: tuple[int, float] | None = None
        self.total_messages = 0

    def run(self) -> int:
        deadline = self.started + self.args.duration
        next_heartbeat = self.started
        next_status = self.started + STATUS_PERIOD_S
        try:
            while (now := time.monotonic()) < deadline:
                if now >= next_heartbeat:
                    self.send_heartbeat()
                    next_heartbeat += HEARTBEAT_PERIOD_S
                if now >= next_status:
                    self.print_status(now)
                    next_status += STATUS_PERIOD_S
                self.intervals.poll(now)
                msg = self.conn.recv_match(blocking=True, timeout=0.05)
                if msg is not None:
                    self.handle(msg, time.monotonic())
        except KeyboardInterrupt:
            print("\nInterrupted.")
        except OSError as exc:  # LinkClosed or another socket error
            print(f"\nLink lost: {exc}")
        self.report(time.monotonic())
        return 0 if self.vehicle else 1

    def log(self, now: float, text: str) -> None:
        print(f"[{now - self.started:5.1f}s] {text}")

    def send_heartbeat(self) -> None:
        self.conn.mav.heartbeat_send(
            mavlink.MAV_TYPE_GCS, mavlink.MAV_AUTOPILOT_INVALID, 0, 0, mavlink.MAV_STATE_ACTIVE
        )

    def handle(self, msg: Any, now: float) -> None:
        kind = msg.get_type()
        if kind == "BAD_DATA":
            return  # counted by the parser, see report()
        self.total_messages += 1
        src = (msg.get_srcSystem(), msg.get_srcComponent())
        component = self.components.setdefault(src, Component())
        component.messages += 1
        if kind == "HEARTBEAT":
            component.heartbeat = msg
            self.on_heartbeat(msg, src, now)
        if src != self.vehicle:
            return
        self.rates.add(kind, now)
        self.loss.add(msg.get_seq(), now)
        self.latest[kind] = msg
        if kind == "ATTITUDE":
            self.track_vehicle_clock(msg.time_boot_ms, now)
        elif kind == "COMMAND_ACK":
            self.on_command_ack(msg, now)
        elif kind == "STATUSTEXT":
            # Texts over 50 characters arrive in chunks; reassembly comes with the event log.
            self.log(now, f"STATUSTEXT {enum_name('MAV_SEVERITY', msg.severity)}: {msg.text}")

    def on_heartbeat(self, msg: Any, src: tuple[int, int], now: float) -> None:
        if self.vehicle is None:
            if src[1] != self.args.target_compid or not is_vehicle_heartbeat(msg):
                return
            self.vehicle = src
            self.log(
                now,
                f"vehicle {src[0]}/{src[1]} found: {enum_name('MAV_TYPE', msg.type)}, "
                f"{enum_name('MAV_AUTOPILOT', msg.autopilot)}",
            )
            self.intervals.start(now)
        if src != self.vehicle:
            return
        if self.last_vehicle_heartbeat is not None:
            self.heartbeat_intervals.append(now - self.last_vehicle_heartbeat)
        self.last_vehicle_heartbeat = now
        mode, armed = mode_name(msg), is_armed(msg)
        if (mode, armed) != (self.mode, self.armed):
            self.mode, self.armed = mode, armed
            self.log(now, f"mode {mode}, {'ARMED' if armed else 'DISARMED'}")

    def on_command_ack(self, msg: Any, now: float) -> None:
        if msg.command != mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
            return
        # Answers to another ground station carry its ids (0 means "not filled in").
        if msg.target_system not in (0, self.args.sysid):
            return
        if msg.target_component not in (0, self.args.compid):
            return
        self.intervals.on_ack(msg.result, now)

    def track_vehicle_clock(self, boot_ms: int, now: float) -> None:
        """Follow the autopilot's clock to measure the real-time factor and spot reboots."""
        if self.clock_last and boot_ms + 1000 < self.clock_last[0]:
            self.log(now, "vehicle clock jumped back: autopilot rebooted, requesting rates again")
            self.clock_first = None
            self.loss.reset()
            self.intervals.start(now)
        if self.clock_first is None:
            self.clock_first = (boot_ms, now)
        self.clock_last = (boot_ms, now)

    def print_status(self, now: float) -> None:
        if self.vehicle is None:
            hint = ""
            if not self.total_messages:
                hint = " (no MAVLink data at all: port taken by another client, or sim paused?)"
            self.log(now, f"waiting for a vehicle heartbeat{hint}")
            return
        att = self.latest.get("ATTITUDE")
        pos = self.latest.get("GLOBAL_POSITION_INT")
        hud = self.latest.get("VFR_HUD")
        gps = self.latest.get("GPS_RAW_INT")
        power = self.latest.get("SYS_STATUS")

        alt = pos.relative_alt / 1000 if pos else None  # mm -> m
        airspeed = hud.airspeed if hud else None
        groundspeed = hud.groundspeed if hud else None
        roll = math.degrees(att.roll) if att else None
        pitch = math.degrees(att.pitch) if att else None
        gps_text = "—"
        if gps:
            sats = "?" if gps.satellites_visible == UINT8_UNKNOWN else gps.satellites_visible
            gps_text = f"{enum_name('GPS_FIX_TYPE', gps.fix_type)} {sats} sats"
        # ArduPilot always sets the battery bit in "present"; only "enabled" says
        # whether a monitor is configured (BATT_MONITOR > 0).
        battery = "—"
        if power and not power.onboard_control_sensors_enabled & BATTERY_SENSOR:
            battery = "no monitor"
        elif power:
            volts = None
            if power.voltage_battery != UINT16_UNKNOWN:
                volts = power.voltage_battery / 1000  # mV -> V
            remaining = power.battery_remaining if power.battery_remaining >= 0 else None
            battery = f"{fmt(volts, '.2f', ' V')} {fmt(remaining, 'd', ' %')}"

        parts = [
            f"{self.mode} {'ARMED' if self.armed else 'DISARMED'}",
            f"alt {fmt(alt, '.1f', ' m')}",
            f"AS {fmt(airspeed, '.1f')} GS {fmt(groundspeed, '.1f', ' m/s')}",
            f"roll {fmt(roll, '.1f', '°')} pitch {fmt(pitch, '.1f', '°')}",
            f"GPS {gps_text}",
            f"batt {battery}",
            f"hb {fmt(now - self.last_vehicle_heartbeat, '.1f', ' s ago')}",
            f"loss {fmt(self.loss.loss_percent(now), '.1f', ' %')}",
        ]
        self.log(now, " | ".join(parts))

    def report(self, now: float) -> None:
        print(f"\n=== Link check report: {now - self.started:.1f} s on {self.args.conn} ===")
        if self.vehicle is None:
            print("No vehicle heartbeat received.")
        else:
            hb = self.components[self.vehicle].heartbeat
            print(
                f"Vehicle       : {self.vehicle[0]}/{self.vehicle[1]}  "
                f"{enum_name('MAV_TYPE', hb.type)}  {enum_name('MAV_AUTOPILOT', hb.autopilot)}  "
                f"mode {self.mode}  {'ARMED' if self.armed else 'DISARMED'}"
            )
            intervals = self.heartbeat_intervals
            if intervals:
                print(
                    f"Heartbeats    : {len(intervals) + 1} received, interval mean "
                    f"{statistics.fmean(intervals):.2f} s "
                    f"(min {min(intervals):.2f}, max {max(intervals):.2f})"
                )
            if self.clock_first and self.clock_last and self.clock_last[1] > self.clock_first[1]:
                vehicle_s = (self.clock_last[0] - self.clock_first[0]) / 1000
                wall_s = self.clock_last[1] - self.clock_first[1]
                print(
                    f"Vehicle clock : {vehicle_s:.1f} s of vehicle time in {wall_s:.1f} s "
                    f"-> real-time factor {vehicle_s / wall_s:.2f}"
                )
            print(f"Packet loss   : {fmt(self.loss.loss_percent(now), '.1f', ' %')}")
        print(f"Corrupt data  : {self.conn.mav.total_receive_errors} (CRC/framing errors)")

        print(f"\nRequested rates (measured over the last {RATE_WINDOW_S:.0f} s):")
        print(f"  {'message':<21}{'requested':>11}   {'ack':<10}{'measured':>10}")
        for req in self.intervals.requests:
            measured = fmt(self.rates.rate_hz(req.name, now), "6.1f", " Hz")
            print(f"  {req.name:<21}{req.rate_hz:>8.1f} Hz   {req.result or '—':<10}{measured:>10}")
        answered = [r for r in self.intervals.requests if r.result not in (None, NO_ACK)]
        if (
            self.vehicle
            and not answered
            and any(r.result == NO_ACK for r in self.intervals.requests)
        ):
            print(
                "  Telemetry arrives but no request was answered: the vehicle may accept\n"
                "  commands only from its GCS ids (MAV_OPTIONS bit 0, MAV_GCS_SYSID)."
            )

        others = {
            name: self.rates.rate_hz(name, now)
            for name in self.rates.names()
            if name not in REQUESTED_RATES_HZ
        }
        if others:
            print("\nOther messages from the vehicle:")
            for name, rate in sorted(others.items(), key=lambda item: -(item[1] or 0)):
                print(f"  {name:<32}{fmt(rate, '6.1f', ' Hz')}")

        print("\nOther components on this link:")
        for src, component in sorted(self.components.items()):
            if src == self.vehicle:
                continue
            hb = component.heartbeat
            kind = (
                f"{enum_name('MAV_TYPE', hb.type)} / {enum_name('MAV_AUTOPILOT', hb.autopilot)}"
                if hb
                else "no heartbeat"
            )
            print(f"  {src[0]}/{src[1]:<6}{kind:<28}{component.messages:>6} messages")


def mavlink_id(text: str) -> int:
    """argparse type for a MAVLink system or component id."""
    value = int(text)
    if not 1 <= value <= 255:
        raise argparse.ArgumentTypeError(f"{value} is not a MAVLink id (1..255)")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check a MAVLink link the way the Gözcü GCS uses it."
    )
    parser.add_argument("--conn", default="tcp:127.0.0.1:5762", help="pymavlink connection string")
    parser.add_argument(
        "--sysid",
        type=mavlink_id,
        default=250,
        help="our system id; outside MAV_GCS_SYSID (255) on purpose, so we never "
        "count as the vehicle's failsafe GCS",
    )
    parser.add_argument(
        "--compid",
        type=mavlink_id,
        default=mavlink.MAV_COMP_ID_MISSIONPLANNER,
        help="our component id",
    )
    parser.add_argument(
        "--target-compid",
        type=mavlink_id,
        default=mavlink.MAV_COMP_ID_AUTOPILOT1,
        help="component id of the autopilot to lock onto",
    )
    parser.add_argument("--duration", type=float, default=30.0, help="seconds to listen")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Connecting to {args.conn} as {args.sysid}/{args.compid} ...")
    try:
        conn = open_link(args.conn, args.sysid, args.compid)
    except (OSError, ValueError) as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 2
    try:
        return LinkCheck(conn, args).run()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
