"""The camera picture: MPEG-TS carrying H.264 on a UDP port, shown in the browser as
JPEG frames, and recorded as it arrives when asked.

One thread owns the UDP socket. It passes every datagram to an ffmpeg process,
which turns the video into JPEG frames (a browser draws those with nothing but
its image decoder), and, while recording, appends it to a .ts file: the file is
the camera's own stream, so recording costs no CPU, works without ffmpeg, and
starting or stopping it never disturbs the picture. Two threads per ffmpeg
process read its frames and its error messages.
"""

import errno
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from gcs.config import RecordingConfig, VideoConfig
from gcs.recorder import create_new, file_stamp, log_directory

log = logging.getLogger("gcs.video")

Publish = Callable[[dict[str, Any]], None]
PublishFrame = Callable[[bytes], None]

NO_SIGNAL_S = 2.0  # no datagram, or no frame, for this long: not live
FROZEN_S = 5.0  # the stream comes but ffmpeg has made no frame for this long: restart it
RESTART_DELAY_S = 2.0  # between ffmpeg runs, so one that keeps failing does not spin
BIND_RETRY_S = 3.0  # how often to try again for a port that another program holds
REPORT_AGAIN_S = 60.0  # the same ffmpeg failure is reported at most this often
STATUS_PERIOD_S = 1.0
RECEIVE_BUFFER_BYTES = 4 * 1024 * 1024  # holds keyframe bursts while Python is busy

# What ffmpeg says while it waits for the first keyframe, or after a lost packet:
# expected, and over by the next keyframe.
FFMPEG_NOISE = (
    b"non-existing PPS",
    b"decode_slice_header error",
    b"no frame!",
    b"Last message repeated",
    b"deprecated pixel format",
)


def ffmpeg_command(program: str, max_fps: float) -> list[str]:
    """ffmpeg reading MPEG-TS on stdin and writing every frame as a JPEG part to stdout."""
    command = [program, "-hide_banner", "-nostdin", "-loglevel", "error"]
    # Start with the first frame that can be decoded and hold none back: look at the
    # stream only briefly, drop what was looked at instead of decoding it late, and
    # use one thread (with a thread per frame, each one holds a frame).
    command += ["-fflags", "nobuffer", "-flags", "low_delay"]
    command += ["-probesize", "500000", "-analyzeduration", "500000", "-threads", "1"]
    command += ["-f", "mpegts", "-i", "pipe:0", "-an"]
    if max_fps:
        # Keep a frame once enough time has passed since the last one kept. ffmpeg's
        # frame-rate options would also repeat frames when the camera is slower.
        gap = 0.95 / max_fps
        command += ["-vf", f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{gap:.4f})'"]
    # Every frame when its time stamp says: the Gazebo camera's H.264 header claims
    # 5 fps whatever the real rate is, and ffmpeg would otherwise go down to that.
    command += ["-fps_mode", "passthrough", "-enc_time_base", "demux"]
    command += ["-threads", "1", "-c:v", "mjpeg", "-q:v", "5", "-f", "mpjpeg", "pipe:1"]
    return command


def split_jpegs(buffer: bytes) -> tuple[list[bytes], bytes]:
    """The complete JPEG frames at the start of ffmpeg's MJPEG output, and what is left.

    Each part is a "--ffmpeg" line and headers up to a blank line, one of them
    Content-length, then the JPEG and CRLF. Raises ValueError for anything else.
    """
    frames = []
    while (head_end := buffer.find(b"\r\n\r\n")) >= 0:
        start = head_end + 4
        end = start + _content_length(buffer[:head_end])
        if len(buffer) < end + 2:
            break
        frames.append(buffer[start:end])
        buffer = buffer[end + 2 :]
    return frames, buffer


def _content_length(head: bytes) -> int:
    for line in head.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            return int(value)
    raise ValueError(f"an MJPEG part without Content-length: {head[:80]!r}")


def video_status(
    state: str,
    reason: str | None,
    source: str | None = None,
    fps: float | None = None,
    kbps: int | None = None,
    can_record: bool = False,
    recording: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The "video" message for the browser. state: LIVE, STARTING, NO SIGNAL, ERROR or OFF;
    reason says why it is not LIVE. recording: file, path, seconds and bytes, or None."""
    return {
        "type": "video",
        "time": time.time(),
        "state": state,
        "reason": reason,
        "source": source,
        "fps": fps,
        "kbps": kbps,
        "can_record": can_record,
        "recording": recording,
    }


@dataclass
class _Recording:
    file: BinaryIO
    path: Path
    started: float  # monotonic
    size: int = 0


class VideoSource:
    """Receives the camera stream, publishes its frames and status, records on request.

    publish(item) gets a "video" status about once a second and on every change,
    and events; publish_frame(jpeg) gets every frame. Both are called from this
    source's threads and must not raise.
    """

    def __init__(
        self,
        config: VideoConfig,
        recording: RecordingConfig,
        publish: Publish,
        publish_frame: PublishFrame,
    ) -> None:
        self._config = config
        self._publish = publish
        self._publish_frame = publish_frame
        self._folder = log_directory(recording.directory)  # next to the session logs
        self.address = f"udp {config.host}:{config.port}"
        self._program = shutil.which(config.ffmpeg)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="video", daemon=True)
        self._control = threading.Lock()  # one recording request at a time
        self._lock = threading.Lock()  # guards _recording, which the socket thread writes
        self._recording: _Recording | None = None
        self._bind_error: str | None = None
        self._next_bind = 0.0
        self._ffmpeg: subprocess.Popen[bytes] | None = None
        self._ffmpeg_started = 0.0
        self._ffmpeg_error: str | None = None  # its last message about a real problem
        self._next_ffmpeg = 0.0
        self._last_report = ("", 0.0)
        # Each written by one thread and read for the status.
        self._received = 0  # bytes
        self._frames = 0
        self._last_datagram: float | None = None
        self._receiving_since = 0.0
        self._last_frame: float | None = None
        self._state: str | None = None
        self._reason: str | None = None
        self._fps: float | None = None
        self._kbps: int | None = None
        self._counted = (0.0, 0, 0)  # (time, bytes, frames) when the rates were worked out

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop receiving, stop ffmpeg and close any recording."""
        self._stop.set()
        self._thread.join(timeout=10)

    def set_recording(self, on: bool) -> None:
        """Start or stop recording the stream. The outcome is published, never raised."""
        with self._control:
            if not on:
                with self._lock:
                    recording, self._recording = self._recording, None
                if recording is not None:
                    self._close_recording(recording, "INFO", "video recorded")
            elif self._bind_error:
                self._event("WARNING", f"cannot record video: {self._bind_error}")
            elif self._recording is None:
                self._start_recording()
            self._publish(self._status_message(time.monotonic()))

    def _run(self) -> None:
        sock = None
        next_tick = 0.0
        try:
            while not self._stop.is_set():
                if sock is None and time.monotonic() >= self._next_bind:
                    sock = self._bind()
                if sock is not None:
                    self._receive(sock)
                else:
                    self._stop.wait(0.2)
                now = time.monotonic()
                self._keep_ffmpeg(now)
                if now >= next_tick:
                    next_tick = now + STATUS_PERIOD_S
                    self._tick(now)
        except Exception:  # without this, a bug would end the video without a word
            log.exception("video stopped")
            self._event("ERROR", "video stopped: internal error, see the station's console")
        finally:
            if sock is not None:
                sock.close()
            self._stop_ffmpeg()
            self.set_recording(False)

    def _bind(self) -> socket.socket | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER_BYTES)
            sock.bind((self._config.host, self._config.port))
        except OSError as exc:
            sock.close()
            self._next_bind = time.monotonic() + BIND_RETRY_S
            hint = " (another program is receiving it)" if exc.errno == errno.EADDRINUSE else ""
            self._bind_error = f"cannot receive on {self.address}: {exc.strerror or exc}{hint}"
            return None
        sock.settimeout(0.2)
        self._bind_error = None
        return sock

    def _receive(self, sock: socket.socket) -> None:
        try:
            data = sock.recv(65536)
        except TimeoutError:
            return
        now = time.monotonic()
        if not self._receiving(now):
            self._receiving_since = now
        self._last_datagram = now
        self._received += len(data)
        self._feed_ffmpeg(data)
        self._record(data)

    def _receiving(self, now: float) -> bool:
        return self._last_datagram is not None and now - self._last_datagram <= NO_SIGNAL_S

    def _feed_ffmpeg(self, data: bytes) -> None:
        process = self._ffmpeg
        if process is None or process.stdin is None:
            return
        # Never wait for ffmpeg: while it is behind it misses datagrams, but the socket
        # keeps being read and the recording stays whole.
        with suppress(BlockingIOError, BrokenPipeError, ValueError):  # ValueError: closed
            os.write(process.stdin.fileno(), data)

    def _record(self, data: bytes) -> None:
        with self._lock:
            recording = self._recording
            if recording is None:
                return
            try:
                recording.file.write(data)
                recording.size += len(data)
                return
            except OSError as exc:  # e.g. the disk is full
                self._recording = None
                problem = str(exc)
        self._close_recording(recording, "WARNING", f"video recording stopped ({problem})")

    def _flush_recording(self) -> None:
        """Write out what is buffered, so at most a second is lost if the station stops."""
        with self._lock:
            recording = self._recording
            if recording is None:
                return
            try:
                recording.file.flush()
                return
            except OSError as exc:
                self._recording = None
                problem = str(exc)
        self._close_recording(recording, "WARNING", f"video recording stopped ({problem})")

    def _keep_ffmpeg(self, now: float) -> None:
        """Run ffmpeg while the stream comes; start it again if it ends or makes no frames."""
        process = self._ffmpeg
        if process is not None:
            if process.poll() is not None:
                self._ffmpeg = None
                self._next_ffmpeg = now + RESTART_DELAY_S
                self._report(f"video decoder ended: {self._ffmpeg_error or process.returncode}")
            elif self._receiving(now) and now - self._last_progress() > FROZEN_S:
                self._stop_ffmpeg()
                self._next_ffmpeg = now + RESTART_DELAY_S
                self._report(f"video decoder made no picture for {FROZEN_S:g} s; restarting it")
            return
        if self._program is None or now < self._next_ffmpeg or not self._receiving(now):
            return
        try:
            process = subprocess.Popen(
                ffmpeg_command(self._program, self._config.max_fps),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self._next_ffmpeg = now + RESTART_DELAY_S
            self._report(f"cannot start the video decoder: {exc}")
            return
        assert process.stdin is not None
        os.set_blocking(process.stdin.fileno(), False)
        self._ffmpeg, self._ffmpeg_started, self._ffmpeg_error = process, now, None
        for target, name in ((self._read_frames, "video-frames"), (self._read_errors, "ffmpeg")):
            threading.Thread(target=target, args=(process,), name=name, daemon=True).start()

    def _last_progress(self) -> float:
        """When ffmpeg last showed that it works: its last frame, its start, or the stream's."""
        return max(self._last_frame or 0.0, self._ffmpeg_started, self._receiving_since)

    def _stop_ffmpeg(self) -> None:
        process, self._ffmpeg = self._ffmpeg, None
        if process is None:
            return
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()  # ffmpeg finishes on the end of its input
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _read_frames(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        buffer = b""
        with process.stdout as output:
            try:
                while chunk := output.read1(65536):
                    frames, buffer = split_jpegs(buffer + chunk)
                    for frame in frames:
                        self._last_frame = time.monotonic()
                        self._frames += 1
                        self._publish_frame(frame)
            except ValueError as exc:
                log.error("unexpected output from ffmpeg: %s", exc)
                process.kill()

    def _read_errors(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stderr is not None
        with process.stderr as errors:
            for line in errors:
                text = line.decode(errors="replace").strip()
                if text and not any(noise in line for noise in FFMPEG_NOISE):
                    self._ffmpeg_error = text

    def _tick(self, now: float) -> None:
        """Once a second: the rates, the state (announced when it changes), the status."""
        then, received, frames = self._counted
        self._counted = (now, self._received, self._frames)
        if then and self._receiving(now):
            self._kbps = round((self._received - received) * 8 / (now - then) / 1000)
            self._fps = round((self._frames - frames) / (now - then), 1)
        else:
            self._kbps = self._fps = None
        state, reason = self._state_now(now)
        if state != self._state:
            self._announce(state, reason)
        self._state, self._reason = state, reason
        self._flush_recording()
        self._publish(self._status_message(now))

    def _state_now(self, now: float) -> tuple[str, str | None]:
        if self._bind_error:
            return "ERROR", self._bind_error
        if not self._receiving(now):
            return "NO SIGNAL", f"nothing arriving on {self.address}: is the camera streaming?"
        if self._program is None:
            return "ERROR", f"{self._config.ffmpeg} not found: no picture, but recording works"
        if self._last_frame is not None and now - self._last_frame <= NO_SIGNAL_S:
            return "LIVE", None
        return "STARTING", self._ffmpeg_error

    def _announce(self, state: str, reason: str | None) -> None:
        if state == "LIVE":
            self._event("INFO", f"video live from {self.address}")
        elif state == "ERROR":
            self._event("WARNING", f"video: {reason}")
        elif state == "NO SIGNAL" and self._state in (None, "ERROR"):
            self._event("INFO", f"video: waiting for the camera on {self.address}")
        elif state == "NO SIGNAL":
            self._event("WARNING", f"video lost: {reason}")

    def _status_message(self, now: float) -> dict[str, Any]:
        recording = self._recording
        return video_status(
            self._state or "NO SIGNAL",
            self._reason,
            self.address,
            fps=self._fps,
            kbps=self._kbps,
            can_record=self._bind_error is None,
            recording=None
            if recording is None
            else {
                "file": recording.path.name,
                "path": str(recording.path),
                "seconds": round(now - recording.started),
                "bytes": recording.size,
            },
        )

    def _start_recording(self) -> None:
        try:
            path = create_new(self._folder, f"{file_stamp(time.time())}_video", ".ts")
            file = path.open("wb")
        except OSError as exc:
            self._event("WARNING", f"cannot record video: {exc}")
            return
        with self._lock:
            self._recording = _Recording(file, path, time.monotonic())
        self._event("INFO", f"recording video to {path}")

    def _close_recording(self, recording: _Recording, severity: str, text: str) -> None:
        with suppress(OSError):
            recording.file.close()
        seconds = time.monotonic() - recording.started
        self._event(
            severity,
            f"{text}: {recording.path} ({recording.size / 1e6:.1f} MB, {_duration(seconds)})",
        )

    def _report(self, text: str) -> None:
        """An ffmpeg problem: said again only when it changes, or after a while."""
        last_text, last_time = self._last_report
        now = time.monotonic()
        if text != last_text or now - last_time > REPORT_AGAIN_S:
            self._last_report = (text, now)
            self._event("WARNING", text)

    def _event(self, severity: str, text: str) -> None:
        event = {"type": "event", "time": time.time(), "source": "video"}
        self._publish({**event, "severity": severity, "text": text})


def _duration(seconds: float) -> str:
    """e.g. 0:07, 12:34, 1:02:03."""
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"
