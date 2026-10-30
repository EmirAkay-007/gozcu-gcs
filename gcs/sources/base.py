"""What every telemetry source provides to the server.

Today the only source is a MAVLink link (MavlinkSource); a Bridge source on the
UNO Q will follow. A source runs on its own and reports through the publish
callback it was given. publish() may be called from any thread, must return
quickly without raising, and receives plain, JSON-ready dicts of two kinds:

    {"type": "snapshot", "seq": int, "time": float,
     "link": {"state": str, "connection": str, "vehicle": dict | None},
     "fields": {key: {"value", "text", "reason", "age", "stale", "level"}},
     "gozcu": {"heard", "present", "system_status", "fields", "tracks", "command"}}

        about ten times a second: the whole state at once (see gcs.state, and
        gcs.gozcu and gcs.gozcu_commands for Gözcü's part);

    {"type": "event", "time": float, "source": str, "severity": str, "text": str}

        the moment something happens. "severity" uses MAVLink's MAV_SEVERITY
        names (CRITICAL, WARNING, NOTICE, INFO, ...); "time" is the wall clock in
        seconds since the epoch.
"""

from typing import Any, Protocol


class TelemetrySource(Protocol):
    def start(self) -> None:
        """Begin producing snapshots and events in the background."""

    def stop(self) -> None:
        """Stop producing and release the link."""

    def gozcu_command(self, action: Any, value: Any, proposal: Any = 0) -> None:
        """Ask Gözcü to change mode, decide on a proposal, or run or pause perception.

        Safe to call from any thread, and never raises: what becomes of the request
        is published, in the snapshot (gozcu.command) and as events.
        """
