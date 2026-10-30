"""Telemetry from a MAVLink link, handled by a single thread.

pymavlink's send path is not thread-safe, so one thread owns the connection: it
reads, sends our heartbeat and the rate requests, watches the link and opens it
again when it breaks. What it learns leaves the thread only as plain data,
through publish() (see gcs.sources.base).

Only the autopilot's messages count as the vehicle's; those of Gözcü, another
component of the same system, go to GozcuState (gcs.gozcu).

The link state keeps "transport open" apart from "telemetry fresh". In SITL
lockstep the vehicle's clock follows Gazebo, so a slow or paused simulation
delays heartbeats without breaking anything: a late heartbeat only reports
LINK_LOST, and the connection is reopened only when the transport itself fails.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from gcs.config import MavlinkConfig
from gcs.fields import MESSAGE_RATES_HZ
from gcs.gozcu import GozcuState
from gcs.heartbeat import is_armed, is_vehicle_heartbeat, mode_name
from gcs.intervals import IntervalRequest, IntervalRequester, send_interval_request
from gcs.link_stats import PacketLossMeter, RateMeter
from gcs.mav import enum_name, mavlink, open_link
from gcs.state import TelemetryState
from gcs.statustext import StatusTextAssembler

# Link states, as published in snapshot["link"]["state"]
CONNECTING = "CONNECTING"  # no transport: opening it, or waiting to try again
NO_VEHICLE = "NO_VEHICLE"  # transport open, no autopilot heartbeat yet
CONNECTED = "CONNECTED"  # autopilot heartbeat younger than link_lost_s
LINK_LOST = "LINK_LOST"  # autopilot seen, but its last heartbeat is older than link_lost_s

HEARTBEAT_PERIOD_S = 1.0
SNAPSHOT_PERIOD_S = 0.1  # the browser refreshes at 10 Hz
RECV_TIMEOUT_S = 0.05
RETRY_MIN_S = 1.0  # first wait before reopening a failed link, doubled up to RETRY_MAX_S
RETRY_MAX_S = 5.0
REBOOT_JUMP_MS = 1000  # the vehicle clock going back by more than this means a reboot
NO_VEHICLE_HINT_S = 3.0  # how long a new link may stay without an autopilot before we say why
EARLY_TEXTS_KEPT = 20  # STATUSTEXTs held while the vehicle is not known yet
VEHICLE = "vehicle"  # RateMeter key for all traffic from the vehicle

Publish = Callable[[dict[str, Any]], None]


def next_due(due: float, period: float, now: float) -> float:
    """The next time on a fixed grid of `period`, so a late loop does not slow the rate.

    A slot that is already over is skipped rather than caught up with a burst.
    """
    due += period
    return due if due > now else now + period


class MavlinkSource:
    def __init__(
        self,
        config: MavlinkConfig,
        publish: Publish,
        *,
        connect: Callable[[str, int, int], Any] = open_link,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._publish = publish
        self._connect = connect
        self._clock = clock
        self._wall_clock = wall_clock
        self._state = TelemetryState()
        self._gozcu = GozcuState(lambda severity, text: self._event("gozcu", severity, text))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mavlink", daemon=True)
        self._link_state: str | None = None
        self._last_error: str | None = None
        self._seq = 0
        self._next_snapshot = 0.0
        self._begin_session(conn=None)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    # The thread's main loops

    def _run(self) -> None:
        retry_s = RETRY_MIN_S
        while not self._stop.is_set():
            self._set_link_state(CONNECTING)
            try:
                conn = self._connect(self.config.connection, self.config.sysid, self.config.compid)
            except (OSError, ValueError) as exc:
                self._report_error(f"cannot open {self.config.connection}: {exc}")
                self._idle(retry_s)
                retry_s = min(retry_s * 2, RETRY_MAX_S)
                continue
            retry_s = RETRY_MIN_S
            self._last_error = None
            try:
                self._serve(conn)
            except OSError as exc:  # LinkClosed, or another transport error
                self._report_error(str(exc))
            except Exception as exc:  # a bug: report it and start a fresh session
                self._report_error(f"internal error: {exc!r}")
                self._idle(RETRY_MAX_S)
            finally:
                conn.close()
                self._begin_session(conn=None)

    def _serve(self, conn: Any) -> None:
        self._begin_session(conn)
        self._set_link_state(NO_VEHICLE)
        next_heartbeat = self._clock()
        while not self._stop.is_set():
            now = self._clock()
            if now >= next_heartbeat:
                self._send_heartbeat()
                next_heartbeat = next_due(next_heartbeat, HEARTBEAT_PERIOD_S, now)
            self._requests.poll(now)
            self._report_rejected_requests()
            self._update_link_state(now)
            self._explain_missing_vehicle(now)
            for severity, text in self._statustexts.expire(now):
                self._event("vehicle", enum_name("MAV_SEVERITY", severity), text)
            self._gozcu.check(now)
            self._tick(now)
            msg = conn.recv_match(blocking=True, timeout=RECV_TIMEOUT_S)
            if msg is None:
                continue
            try:
                self._handle(msg, self._clock())
            except OSError:
                raise  # the link itself failed: reopen it
            except Exception as exc:  # one bad message must not stop the telemetry
                self._report_error(f"cannot handle {msg.get_type()}: {exc!r}")

    def _idle(self, seconds: float) -> None:
        """Wait before retrying, but keep publishing snapshots so the browser sees why."""
        deadline = self._clock() + seconds
        while not self._stop.is_set():
            now = self._clock()
            if now >= deadline:
                return
            self._tick(now)
            self._stop.wait(max(0.0, min(self._next_snapshot, deadline) - self._clock()))

    def _tick(self, now: float) -> None:
        if now >= self._next_snapshot:
            self._next_snapshot = next_due(self._next_snapshot, SNAPSHOT_PERIOD_S, now)
            self._publish(self._snapshot(now))

    def _begin_session(self, conn: Any) -> None:
        """Forget everything tied to the previous connection; field values stay and age."""
        self._conn = conn
        self._vehicle: tuple[int, int] | None = None
        self._last_heartbeat: Any = None
        self._last_heartbeat_at: float | None = None
        self._last_boot_ms: int | None = None
        self._loss = PacketLossMeter()
        self._rates = RateMeter()
        rates = MESSAGE_RATES_HZ if self.config.request_rates else {}
        self._requests = IntervalRequester(rates, send=self._send_interval_request)
        self._reported_requests: set[str] = set()
        self._session_started = self._clock()
        self._messages = 0  # readable MAVLink messages from anyone
        self._unreadable = 0  # chunks the parser rejected (CRC or framing errors)
        self._hint_given = False
        self._statustexts = StatusTextAssembler()
        # (source, message, time) of texts that arrived before the first vehicle heartbeat
        self._early_texts: deque[tuple[tuple[int, int], Any, float]] = deque(
            maxlen=EARLY_TEXTS_KEPT
        )

    # Sending

    def _send_heartbeat(self) -> None:
        self._conn.mav.heartbeat_send(
            mavlink.MAV_TYPE_GCS, mavlink.MAV_AUTOPILOT_INVALID, 0, 0, mavlink.MAV_STATE_ACTIVE
        )

    def _send_interval_request(self, request: IntervalRequest) -> None:
        send_interval_request(self._conn.mav, self._vehicle, request)

    # Receiving

    def _handle(self, msg: Any, now: float) -> None:
        kind = msg.get_type()
        if kind == "BAD_DATA":
            self._unreadable += 1
            return
        self._messages += 1
        src = (msg.get_srcSystem(), msg.get_srcComponent())
        if self._vehicle is None and kind == "HEARTBEAT" and self._is_autopilot(msg, src):
            self._lock_on(msg, src, now)
        if self._vehicle is not None and src == (self._vehicle[0], self.config.gozcu_compid):
            self._gozcu.handle(msg, now)  # Gözcü: the same system as the autopilot
            return
        if src != self._vehicle:
            if self._vehicle is None and kind == "STATUSTEXT":
                # The autopilot may speak before its first heartbeat: keep the text
                # until we know whether it came from the vehicle.
                self._early_texts.append((src, msg, now))
            return  # MAVProxy, other ground stations, ...: not the vehicle
        self._loss.add(msg.get_seq(), now)
        self._rates.add(VEHICLE, now)
        self._state.update(msg, now)
        if kind == "HEARTBEAT":
            self._on_heartbeat(msg, now)
        elif kind == "ATTITUDE":
            self._watch_vehicle_clock(msg.time_boot_ms, now)
        elif kind == "COMMAND_ACK":
            self._on_command_ack(msg, now)
        elif kind == "STATUSTEXT":
            self._on_statustext(msg, now)

    def _is_autopilot(self, msg: Any, src: tuple[int, int]) -> bool:
        return src[1] == self.config.target_compid and is_vehicle_heartbeat(msg)

    def _lock_on(self, msg: Any, src: tuple[int, int], now: float) -> None:
        self._vehicle = src
        self._event(
            "link",
            "INFO",
            f"vehicle {src[0]}/{src[1]}: {enum_name('MAV_TYPE', msg.type)}, "
            f"{enum_name('MAV_AUTOPILOT', msg.autopilot)}",
        )
        self._requests.start(now)
        for early_src, early_msg, received in self._early_texts:
            if early_src == src:
                self._on_statustext(early_msg, received)
        self._early_texts.clear()

    def _on_statustext(self, msg: Any, now: float) -> None:
        whole = self._statustexts.add(msg, now)  # long texts arrive in chunks
        if whole is not None:
            severity, text = whole
            self._event("vehicle", enum_name("MAV_SEVERITY", severity), text)

    def _on_heartbeat(self, msg: Any, now: float) -> None:
        mode, armed = mode_name(msg), is_armed(msg)
        before = self._last_heartbeat
        if before is None or mode_name(before) != mode:
            self._event("vehicle", "INFO", f"mode {mode}")
        if before is None or is_armed(before) != armed:
            self._event("vehicle", "NOTICE", "ARMED" if armed else "DISARMED")
        self._last_heartbeat, self._last_heartbeat_at = msg, now
        self._state.set("mode", mode, now)
        self._state.set("armed", armed, now)

    def _watch_vehicle_clock(self, boot_ms: int, now: float) -> None:
        """A clock that jumps back means the autopilot rebooted and forgot our rates."""
        if self._last_boot_ms is not None and boot_ms + REBOOT_JUMP_MS < self._last_boot_ms:
            self._event("vehicle", "WARNING", "vehicle rebooted; requesting message rates again")
            self._loss.reset()
            self._reported_requests.clear()
            self._requests.start(now)
        self._last_boot_ms = boot_ms

    def _on_command_ack(self, msg: Any, now: float) -> None:
        if msg.command != mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
            return
        # Answers to another ground station carry its ids (0 means "not filled in").
        if msg.target_system not in (0, self.config.sysid):
            return
        if msg.target_component not in (0, self.config.compid):
            return
        self._requests.on_ack(msg.result, now)
        self._report_rejected_requests()

    def _report_rejected_requests(self) -> None:
        """Tell the operator once about every rate request the vehicle did not accept."""
        for request in self._requests.requests:
            if request.result in (None, "ACCEPTED") or request.name in self._reported_requests:
                continue
            self._reported_requests.add(request.name)
            self._event("link", "WARNING", f"rate request for {request.name}: {request.result}")

    # Link state and output

    def _explain_missing_vehicle(self, now: float) -> None:
        """Say once per connection why no autopilot shows up, instead of waiting silently."""
        if self._vehicle is not None or self._hint_given:
            return
        if now - self._session_started < NO_VEHICLE_HINT_S:
            return
        self._hint_given = True
        where = self.config.connection
        if self._messages:
            text = f"MAVLink traffic on {where}, but no heartbeat from autopilot component "
            text += str(self.config.target_compid)
        elif self._unreadable:
            text = f"only unreadable data on {where}: wrong baud rate, or not MAVLink?"
        else:
            text = (
                f"no MAVLink data on {where} yet: another program may hold this port "
                "(SITL serves one client per port), or the simulation is paused"
            )
        self._event("link", "WARNING", text)

    def _update_link_state(self, now: float) -> None:
        if self._vehicle is None:
            self._set_link_state(NO_VEHICLE)
        elif now - self._last_heartbeat_at > self.config.link_lost_s:
            self._set_link_state(LINK_LOST)
        else:
            self._set_link_state(CONNECTED)

    def _set_link_state(self, state: str) -> None:
        if state == self._link_state:
            return
        previous, self._link_state = self._link_state, state
        if state == CONNECTING:
            self._event("link", "INFO", f"connecting to {self.config.connection}")
        elif state == NO_VEHICLE:
            self._event(
                "link", "INFO", f"connected to {self.config.connection}, waiting for the autopilot"
            )
        elif state == LINK_LOST:
            self._event(
                "link", "WARNING", f"no autopilot heartbeat for {self.config.link_lost_s:g} s"
            )
        elif previous == LINK_LOST:
            self._event("link", "INFO", "autopilot heartbeat back")

    def _report_error(self, text: str) -> None:
        """Log a link error once, not again on every retry."""
        if text != self._last_error:
            self._last_error = text
            self._event("link", "WARNING", text)

    def _event(self, source: str, severity: str, text: str) -> None:
        self._publish(
            {
                "type": "event",
                "time": self._wall_clock(),
                "source": source,
                "severity": severity,
                "text": text,
            }
        )

    def _snapshot(self, now: float) -> dict[str, Any]:
        self._seq += 1
        heartbeat_age = None
        if self._last_heartbeat_at is not None:
            heartbeat_age = now - self._last_heartbeat_at
        self._state.set("heartbeat_age", heartbeat_age, now)
        self._state.set("link_loss", self._loss.loss_percent(now), now)
        self._state.set("link_rate", self._rates.rate_hz(VEHICLE, now), now)
        vehicle = None
        if self._vehicle is not None:
            vehicle = {
                "sysid": self._vehicle[0],
                "compid": self._vehicle[1],
                "type": enum_name("MAV_TYPE", self._last_heartbeat.type),
                "autopilot": enum_name("MAV_AUTOPILOT", self._last_heartbeat.autopilot),
            }
        return {
            "type": "snapshot",
            "seq": self._seq,
            "time": self._wall_clock(),
            "link": {
                "state": self._link_state,
                "connection": self.config.connection,
                "vehicle": vehicle,
            },
            "fields": self._state.snapshot(now),
            "gozcu": self._gozcu.snapshot(now),
        }
