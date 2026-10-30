"""Gözcü as the ground station hears it, following docs/message_contract.md.

Gözcü (the MCU) is MAVLink component 191 of the vehicle's system. It reports
named values (NAMED_VALUE_INT/FLOAT) and texts, and a heartbeat. GozcuState
turns them into the "gozcu" part of a snapshot: every value in the same shape as
the telemetry fields (so the browser shows them alike), the tracks of the newest
complete track frame, and whether Gözcü is heard at all. Changes that matter
(state, mode, switch, faults) become events, so they are in the session log.

Fed by the MAVLink source's thread; not thread-safe.
"""

from collections.abc import Callable
from typing import Any

from gcs.fields import Field, Limit
from gcs.mav import enum_name
from gcs.state import TelemetryState
from gcs.statustext import StatusTextAssembler

CONTRACT_VERSION = 1
SILENT_AFTER_S = 3.0  # Gözcü's heartbeat older than this: silent, no commands
STATUS_STALE_S = 1.5
TRACKS_STALE_S = 1.0
MAX_TRACKS = 3

STATES = {0: "CLEAR", 1: "ADVISORY", 2: "AVOID", 3: "CLEAR OF CONFLICT"}
AVOID = 2
MODES = {0: "WARN", 1: "CONFIRM", 2: "AUTO"}
SWITCH = {0: "INHIBIT", 1: "ARM"}
PERCEPTION = {0: "PAUSED", 1: "RUNNING"}
FAULTS = {1: "MPU silent", 2: "perception stale"}  # bit -> meaning
MANOEUVRES = {0: "none", 1: "CLIMB", 2: "DESCEND", 3: "TURN LEFT", 4: "TURN RIGHT"}
DECISIONS = {0: "WAITING", 1: "APPROVED", 2: "REFUSED", 3: "REJECTED", 4: "EXPIRED"}
WAITING = 0
REASONS = {
    0: "",
    1: "outside the flight envelope",
    2: "below minimum altitude",
    3: "speed limit",
    4: "switch on INHIBIT",
}
CLASSES = {0: "unknown", 1: "aircraft", 2: "drone", 3: "bird"}


def _named(codes: dict[int, str]) -> Callable[[Any], str]:
    return lambda value: codes.get(value, f"unknown ({value})")


def _faults_text(value: int) -> str:
    if value == 0:
        return "none"
    names = [name for bit, name in FAULTS.items() if value & bit]
    unknown = value & ~sum(FAULTS)
    return ", ".join(names + ([f"unknown ({unknown})"] if unknown else []))


def _status(key: str, label: str, unit: str = "", **kwargs: Any) -> Field:
    return Field(key, label, unit, None, stale_after_s=STATUS_STALE_S, **kwargs)


# Every value the panel shows. The limits are the project's targets: perception at
# 8 Hz or more, camera to decision under 250 ms; and the MCU's own watchdog limits.
GOZCU_FIELDS: tuple[Field, ...] = (
    _status("version", "Contract", decimals=0),
    _status("state", "Threat", text=_named(STATES)),
    _status("mode", "Mode", text=_named(MODES)),
    _status("switch", "ARM/INHIBIT", text=_named(SWITCH), warn=Limit(below=1)),
    _status("perception", "Perception", text=_named(PERCEPTION), warn=Limit(below=1)),
    _status("faults", "Faults", text=_faults_text, crit=Limit(above=0)),
    _status("watchdog_trips", "Watchdog trips", decimals=0, warn=Limit(above=0)),
    _status("mpu_age", "MPU heartbeat age", "ms", decimals=0, crit=Limit(above=200)),
    _status("perception_age", "Perception age", "ms", decimals=0, crit=Limit(above=500)),
    _status("fps", "Perception rate", "Hz", warn=Limit(below=8)),
    _status("latency", "Camera to decision", "ms", decimals=0, warn=Limit(above=250)),
    _status("temperature", "CPU temperature", "°C", decimals=0, warn=Limit(above=80)),
    _status("target", "Target track", decimals=0),
    _status("proposal", "Proposal", decimals=0),
    _status("manoeuvre", "Manoeuvre", text=_named(MANOEUVRES)),
    _status("decision", "Decision", text=_named(DECISIONS)),
    _status("reason", "Refused because", text=_named(REASONS)),
    _status("time_left", "Time to decide", "ms", decimals=0),
    # Worked out here: how long ago Gözcü's own heartbeat came.
    Field("heartbeat_age", "Gözcü heartbeat", "s", None, crit=Limit(above=SILENT_AFTER_S)),
)

# NAMED_VALUE name -> field key
VALUES = {
    "G_VER": "version",
    "G_STATE": "state",
    "G_MODE": "mode",
    "G_ARM": "switch",
    "G_PERC": "perception",
    "G_FAULT": "faults",
    "G_WDT": "watchdog_trips",
    "G_MPU_MS": "mpu_age",
    "G_PAGE_MS": "perception_age",
    "G_FPS": "fps",
    "G_LAT_MS": "latency",
    "G_TEMP": "temperature",
    "G_TGT_ID": "target",
    "G_PROP": "proposal",
    "G_MNVR": "manoeuvre",
    "G_DEC": "decision",
    "G_WHY": "reason",
    "G_TTL_MS": "time_left",
}
CODES = {"state", "mode", "switch", "perception", "faults", "manoeuvre", "decision", "reason"}
TRACK_VALUES = {  # Tk_ suffix -> key in a track
    "ID": "id",
    "CLS": "class",
    "CONF": "conf",
    "AZ": "az",
    "EL": "el",
    "RATE": "rate",
    "PX": "px",
    "TAU": "tau",
}
FRAME_END = "G_NTRK"

Event = Callable[[str, str], None]  # (severity, text)


def track_value(name: str) -> tuple[int, str] | None:
    """(k, key) for a track value such as "T2_AZ", else None."""
    if len(name) > 3 and name[0] == "T" and name[1] in "123" and name[2] == "_":
        key = TRACK_VALUES.get(name[3:])
        if key is not None:
            return int(name[1]), key
    return None


class TrackFrames:
    """Assembles track frames: the values of one frame share a time_boot_ms, and
    G_NTRK, sent last, closes it. Only closed frames are shown."""

    def __init__(self) -> None:
        self._time: int | None = None  # time_boot_ms of the frame being received
        self._values: dict[tuple[int, str], float] = {}
        self.tracks: list[dict[str, Any]] = []
        self.received: float | None = None  # when the newest frame was closed

    def add(self, time_boot_ms: int, k: int, key: str, value: float) -> None:
        if time_boot_ms != self._time:  # a new frame: whatever came before is incomplete
            self._time, self._values = time_boot_ms, {}
        self._values[(k, key)] = value

    def close(self, time_boot_ms: int, count: float, now: float) -> None:
        values = self._values if time_boot_ms == self._time else {}
        n = max(0, min(MAX_TRACKS, round(count)))
        self.tracks = [
            _track({key: values.get((k, key)) for key in TRACK_VALUES.values()})
            for k in range(1, n + 1)
        ]
        self.received = now
        self._time, self._values = None, {}


def _track(values: dict[str, Any]) -> dict[str, Any]:
    """A track for the browser: codes as names, and no τ when it is not closing."""
    track = dict(values)
    if track["id"] is not None:
        track["id"] = round(track["id"])
    if track["class"] is not None:
        track["class"] = CLASSES.get(round(track["class"]), f"unknown ({track['class']:g})")
    if track["tau"] is not None and track["tau"] < 0:
        track["tau"] = None  # -1: not closing
    return track


class GozcuState:
    def __init__(self, event: Event) -> None:
        self._event = event
        self._values = TelemetryState(GOZCU_FIELDS)
        self._frames = TrackFrames()
        self._texts = StatusTextAssembler()
        self._heartbeat_at: float | None = None
        self._system_status: int | None = None
        self._silent = False
        self._unknown_names: set[str] = set()
        self._version_reported = False

    def handle(self, msg: Any, now: float) -> None:
        """A message from the Gözcü component."""
        kind = msg.get_type()
        if kind == "HEARTBEAT":
            self._on_heartbeat(msg, now)
        elif kind in ("NAMED_VALUE_INT", "NAMED_VALUE_FLOAT"):
            self._on_value(msg.name, msg.time_boot_ms, msg.value, now)
        elif kind == "STATUSTEXT":
            whole = self._texts.add(msg, now)  # long texts arrive in chunks
            if whole is not None:
                self._event(enum_name("MAV_SEVERITY", whole[0]), whole[1])

    def check(self, now: float) -> None:
        """Called often: notices Gözcü falling silent, and texts that never completed."""
        heard = self._heartbeat_at is not None
        if heard and not self._silent and now - self._heartbeat_at > SILENT_AFTER_S:
            self._silent = True
            self._event("WARNING", f"Gözcü silent: no heartbeat for {SILENT_AFTER_S:g} s")
        for severity, text in self._texts.expire(now):
            self._event(enum_name("MAV_SEVERITY", severity), text)

    def present(self, now: float) -> bool:
        """Whether Gözcü's heartbeat is recent: only then may commands be sent."""
        return self._heartbeat_at is not None and now - self._heartbeat_at <= SILENT_AFTER_S

    def holding(self, now: float) -> str | None:
        """Why the operator must leave the autopilot alone now, or None: while Gözcü
        avoids, or while one of its proposals waits for the operator's answer.
        A silent Gözcü holds nothing: its state is unknown."""
        if not self.present(now):
            return None
        state = self._values.fresh("state", now)
        if state is not None and state.value == AVOID:
            return "Gözcü is avoiding"
        proposal = self._values.fresh("proposal", now)
        decision = self._values.fresh("decision", now)
        if not (proposal and proposal.value and decision and decision.value == WAITING):
            return None
        left = self._values.fresh("time_left", now)
        if left is not None and left.value is not None and left.value / 1000 <= now - left.received:
            return None  # its time is up
        return f"Gözcü's proposal {proposal.value} waits for an answer"

    def snapshot(self, now: float) -> dict[str, Any]:
        heartbeat_age = None if self._heartbeat_at is None else now - self._heartbeat_at
        self._values.set("heartbeat_age", heartbeat_age, now, reason="not heard")
        received = self._frames.received
        tracks_age = None if received is None else round(now - received, 2)
        return {
            "heard": self._heartbeat_at is not None,
            "present": self.present(now),
            "system_status": None
            if self._system_status is None
            else enum_name("MAV_STATE", self._system_status),
            "fields": self._values.snapshot(now),
            "tracks": {
                "items": self._frames.tracks,
                "age": tracks_age,
                "stale": tracks_age is None or tracks_age > TRACKS_STALE_S,
            },
        }

    def _on_heartbeat(self, msg: Any, now: float) -> None:
        if self._heartbeat_at is None:
            self._event("INFO", "Gözcü heard")
        elif self._silent:
            self._event("INFO", "Gözcü heard again")
        self._heartbeat_at, self._silent = now, False
        self._system_status = msg.system_status

    def _on_value(self, name: str, time_boot_ms: int, value: float, now: float) -> None:
        if name == FRAME_END:
            self._frames.close(time_boot_ms, value, now)
            return
        place = track_value(name)
        if place is not None:
            self._frames.add(time_boot_ms, *place, value)
            return
        key = VALUES.get(name)
        if key is None:
            if name not in self._unknown_names:  # tell the integrator, once per name
                self._unknown_names.add(name)
                self._event("NOTICE", f"Gözcü sent {name}, which the contract does not define")
            return
        if key in CODES:
            value = round(value)
        before = self._values.value(key)
        self._values.set(key, value, now)
        if value != before:
            self._announce(key, value, before)

    def _announce(self, key: str, value: Any, before: Any) -> None:
        """Changes the operator should find in the log."""
        if key == "state":
            severity = {1: "WARNING", 2: "CRITICAL"}.get(value, "NOTICE")
            self._event(severity, f"threat {STATES.get(value, value)}")
        elif key == "mode":
            self._event("NOTICE", f"mode {MODES.get(value, value)}")
        elif key == "switch":
            self._event("NOTICE", f"switch {SWITCH.get(value, value)}")
        elif key == "perception":
            self._event("NOTICE", f"perception {PERCEPTION.get(value, value)}")
        elif key == "faults" and value:
            self._event("CRITICAL", f"safe mode: {_faults_text(value)}")
        elif key == "faults" and before:
            self._event("NOTICE", "safe mode over")
        elif key == "version" and value != CONTRACT_VERSION and not self._version_reported:
            self._version_reported = True
            self._event(
                "WARNING",
                f"Gözcü speaks contract version {value}; this station knows {CONTRACT_VERSION}",
            )
