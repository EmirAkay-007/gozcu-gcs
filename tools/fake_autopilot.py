"""A stand-in for the autopilot, for the tests and tools/ui_check.py: just enough of
ArduPilot to drive the ground station, on a local TCP port of its own.

It sends a HEARTBEAT (a fixed wing in FBWA, disarmed at first) every second,
ATTITUDE about ten times a second and, given a position, GLOBAL_POSITION_INT as
often. It accepts every SET_MESSAGE_INTERVAL, and mode, arm and reposition
commands as ArduPlane would when all is well. Gözcü's traffic, component 191 of
the same system, comes over the same connection, as ArduPilot routes it: sent by
the caller (as_gozcu), or by a FakeGozcu from tools/fake_gozcu.py playing the MCU.
"""

import queue
import socket
import threading
import time

from gcs.mav import mavlink

GUIDED = 15  # ArduPlane's mode number


class SocketFile:
    """Lets a pymavlink MAVLink object write straight to a socket."""

    def __init__(self, sock):
        self.sock = sock

    def write(self, data):
        self.sock.sendall(data)


def send_text(mav, severity, text):
    """Send a STATUSTEXT the way ArduPilot does: 50-byte chunks under one id when long."""
    raw = text.encode("utf-8")
    msg_id = 0 if len(raw) <= 50 else 9
    for seq in range(256):
        piece, raw = raw[:50], raw[50:]
        mav.statustext_send(severity, piece, msg_id, seq)
        if msg_id == 0 or len(piece) < 50:
            return


class FakeAutopilot:
    """position is (lat, lon, metres above home), or None for no position at all."""

    def __init__(self, position=None):
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(0.1)
        self.url = f"tcp:127.0.0.1:{self._server.getsockname()[1]}"
        self.position = position
        self.mode = 5  # FBWA
        self.armed = False
        self.vehicle_commands = []  # mode, arm and reposition commands received
        self.requested = []  # message ids asked for, in order
        self.connections = 0
        self._hang_up = threading.Event()
        self._quiet = threading.Event()
        self._reboot = threading.Event()
        self._texts = queue.SimpleQueue()  # STATUSTEXTs waiting to be sent
        self._gozcu_sends = queue.SimpleQueue()  # what to send as the Gözcü component
        self.gozcu_commands = []  # MAV_CMD_USER_1 commands received
        self.gozcu_answer = "accept"  # or "deny", "silent", "autopilot"
        self.mcu = None  # a FakeGozcu playing Gözcü's MCU: it sends, and it answers
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def as_gozcu(self, send):
        """Call send(mav) where mav sends as Gözcü, component 191 of this system."""
        self._gozcu_sends.put(send)

    def say(self, text, severity=mavlink.MAV_SEVERITY_WARNING):
        """Send a STATUSTEXT, in chunks if it is longer than one packet."""
        self._texts.put((severity, text))

    def hang_up(self):
        """Close the current connection."""
        self._hang_up.set()

    def go_quiet(self):
        """Stop sending heartbeats but keep the connection."""
        self._quiet.set()

    def resume(self):
        """Send heartbeats again."""
        self._quiet.clear()

    def reboot(self):
        """Restart the vehicle clock from zero, as a rebooted autopilot does."""
        self._reboot.set()

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self._server.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            self.connections += 1
            self._hang_up.clear()
            with client:
                self._serve(client)

    def _serve(self, client):
        client.settimeout(0.1)
        mav = mavlink.MAVLink(SocketFile(client), srcSystem=1, srcComponent=1)
        gozcu = mavlink.MAVLink(SocketFile(client), srcSystem=1, srcComponent=191)
        parser = mavlink.MAVLink(None)
        boot_ms = 60_000  # up for a minute already
        next_heartbeat = 0.0
        while not (self._stop.is_set() or self._hang_up.is_set()):
            if self._reboot.is_set():
                self._reboot.clear()
                boot_ms = 0
            now = time.monotonic()
            try:
                if not self._quiet.is_set() and now >= next_heartbeat:
                    armed = mavlink.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0
                    mav.heartbeat_send(
                        mavlink.MAV_TYPE_FIXED_WING,
                        mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                        mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | armed,
                        self.mode,
                        mavlink.MAV_STATE_STANDBY,
                    )
                    next_heartbeat = now + 1.0
                boot_ms += 100
                mav.attitude_send(boot_ms, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0)
                if self.position is not None:
                    lat, lon, alt = self.position
                    mav.global_position_int_send(
                        boot_ms, round(lat * 1e7), round(lon * 1e7), 0, round(alt * 1e3), 0, 0, 0, 0
                    )
                while not self._texts.empty():
                    send_text(mav, *self._texts.get())
                while not self._gozcu_sends.empty():
                    self._gozcu_sends.get()(gozcu)
                if self.mcu is not None:
                    self.mcu.step(gozcu, now)
                data = client.recv(4096)
                if not data:
                    return
                for msg in parser.parse_buffer(data) or []:
                    self._answer(mav, gozcu, msg)
            except TimeoutError:
                continue
            except OSError:  # the GCS went away
                return

    def _answer(self, mav, gozcu, msg):
        kind = msg.get_type()
        if kind == "COMMAND_INT" and msg.command == mavlink.MAV_CMD_DO_REPOSITION:
            self.vehicle_commands.append(msg)
            if int(msg.param2) & mavlink.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE:
                self.mode = GUIDED
            result, reason, answerer = mavlink.MAV_RESULT_ACCEPTED, 0, mav
        elif kind != "COMMAND_LONG":
            return
        elif msg.command == mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
            self.requested.append(int(msg.param1))
            result, reason, answerer = mavlink.MAV_RESULT_ACCEPTED, 0, mav
        elif msg.command == mavlink.MAV_CMD_DO_SET_MODE:
            self.vehicle_commands.append(msg)
            self.mode = int(msg.param2)
            result, reason, answerer = mavlink.MAV_RESULT_ACCEPTED, 0, mav
        elif msg.command == mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
            self.vehicle_commands.append(msg)
            self.armed = msg.param1 == 1
            result, reason, answerer = mavlink.MAV_RESULT_ACCEPTED, 0, mav
        elif msg.command == mavlink.MAV_CMD_USER_1:
            self.gozcu_commands.append(msg)
            mcu, now = self.mcu, time.monotonic()
            if mcu is not None:
                if not mcu.answers(now):
                    return
                (result, reason), answerer = mcu.on_command(msg, now), gozcu
            elif self.gozcu_answer == "silent":
                return
            else:
                result, reason, answerer = {
                    "accept": (mavlink.MAV_RESULT_ACCEPTED, 0, gozcu),
                    "deny": (mavlink.MAV_RESULT_DENIED, 1, gozcu),  # switch on INHIBIT
                    "autopilot": (mavlink.MAV_RESULT_UNSUPPORTED, 0, mav),  # no route to Gözcü
                }[self.gozcu_answer]
        else:
            return
        answerer.command_ack_send(
            msg.command, result, 0, reason, msg.get_srcSystem(), msg.get_srcComponent()
        )
