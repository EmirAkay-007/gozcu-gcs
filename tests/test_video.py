"""The video source: the pure parts directly, the rest with a real ffmpeg and a short
H.264 stream it generates, sent over UDP as a camera would."""

import shutil
import socket
import subprocess
import threading
import time

import pytest

from gcs import video
from gcs.config import RecordingConfig, VideoConfig
from gcs.video import VideoSource, ffmpeg_command, split_jpegs

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def part(jpeg: bytes) -> bytes:
    """One part of ffmpeg's MJPEG output."""
    head = b"--ffmpeg\r\nContent-type: image/jpeg\r\nContent-length: %d\r\n\r\n" % len(jpeg)
    return head + jpeg + b"\r\n"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def send(port: int, data: bytes, seconds: float) -> None:
    """Send data as a camera does: 1316-byte datagrams, spread evenly over seconds."""
    chunks = [data[i : i + 1316] for i in range(0, len(data), 1316)]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        start = time.monotonic()
        for n, chunk in enumerate(chunks):
            delay = start + seconds * n / len(chunks) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            sock.sendto(chunk, ("127.0.0.1", port))


def wait_until(condition, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


class Watcher:
    """Collects what a VideoSource publishes."""

    def __init__(self):
        self.items = []
        self.frames = []

    def publish(self, item):
        self.items.append(item)

    def publish_frame(self, frame):
        self.frames.append(frame)

    def events(self):
        return [item["text"] for item in self.items if item["type"] == "event"]

    def statuses(self):
        return [item for item in self.items if item["type"] == "video"]


def start_source(watcher, tmp_path, **settings):
    source = VideoSource(
        VideoConfig(**settings),
        RecordingConfig(directory=str(tmp_path)),
        watcher.publish,
        watcher.publish_frame,
    )
    source.start()
    return source


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    """Four seconds of H.264 in MPEG-TS, 320x240 at 10 fps, a keyframe every half second."""
    if FFMPEG is None:
        pytest.skip("ffmpeg is not installed")
    path = tmp_path_factory.mktemp("video") / "camera.ts"
    run = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=320x240:rate=10", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "5", "-bf", "0", "-pix_fmt", "yuv420p", "-f", "mpegts", str(path)],
        capture_output=True,
    )  # fmt: skip
    if run.returncode != 0:
        pytest.skip(f"this ffmpeg cannot make an H.264 test stream: {run.stderr[-200:]!r}")
    return path.read_bytes()


def test_frames_are_taken_from_the_output_as_they_complete():
    one, two = b"\xff\xd8one\xff\xd9", b"\xff\xd8two\r\n\r\nstill two\xff\xd9"
    output = part(one) + part(two)
    for cut in range(len(output) + 1):  # however the pipe happens to hand it over
        first, rest = split_jpegs(output[:cut])
        second, rest = split_jpegs(rest + output[cut:])
        assert first + second == [one, two]
        assert rest == b""


def test_output_that_is_not_mjpeg_is_refused():
    with pytest.raises(ValueError):
        split_jpegs(b"--ffmpeg\r\nContent-type: image/jpeg\r\n\r\n\xff\xd8")


def test_the_decoder_keeps_every_frame_unless_told_a_rate():
    every = ffmpeg_command("ffmpeg", 0)
    assert every[every.index("-fps_mode") + 1] == "passthrough"
    assert "-vf" not in every
    limited = ffmpeg_command("ffmpeg", 10)
    assert "gte(t-prev_selected_t\\,0.0950)" in limited[limited.index("-vf") + 1]


@needs_ffmpeg
def test_a_rate_limit_thins_frames_out_without_repeating_any(stream):
    def frames_out(max_fps):
        command = ffmpeg_command(FFMPEG, max_fps)
        run = subprocess.run(command, input=stream, capture_output=True, timeout=60)
        frames, rest = split_jpegs(run.stdout)
        assert rest == b"--ffmpeg\r\n"  # the closing boundary, at the end of the input
        return len(frames)

    every, five = frames_out(0), frames_out(5)
    assert 30 <= every <= 40  # 4 s at 10 fps, less what ffmpeg drops while it looks at the start
    assert every / 2 - 2 <= five <= every / 2 + 2


@needs_ffmpeg
def test_the_stream_becomes_frames_and_a_recording(stream, tmp_path):
    port = free_port()
    watcher = Watcher()
    source = start_source(watcher, tmp_path, port=port)
    try:
        sender = threading.Thread(target=send, args=(port, stream, 4.0))
        sender.start()
        wait_until(lambda: len(watcher.frames) >= 10)
        source.set_recording(True)
        wait_until(lambda: any(s["state"] == "LIVE" and s["fps"] for s in watcher.statuses()))
        time.sleep(1.0)
        source.set_recording(False)
        sender.join()
    finally:
        source.stop()
    assert all(f[:2] == b"\xff\xd8" and f[-2:] == b"\xff\xd9" for f in watcher.frames)
    live = [s for s in watcher.statuses() if s["state"] == "LIVE" and s["fps"]]
    assert 6 <= live[-1]["fps"] <= 14  # the camera sends 10 frames a second
    (recording,) = tmp_path.glob("*_video.ts")
    assert recording.stat().st_size > 10_000
    events = watcher.events()
    assert f"video live from udp 127.0.0.1:{port}" in events
    assert f"recording video to {recording}" in events
    assert any(text.startswith(f"video recorded: {recording} (") for text in events)
    if shutil.which("ffprobe"):  # the recording plays: it is the camera's own stream
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
             recording],
            capture_output=True, text=True,
        )  # fmt: skip
        assert set(probe.stdout.split()) == {"h264"}  # once for the program, once for the stream


def test_with_nothing_arriving_the_station_says_where_it_listens(tmp_path):
    port = free_port()
    watcher = Watcher()
    source = start_source(watcher, tmp_path, port=port)
    try:
        wait_until(watcher.statuses)
    finally:
        source.stop()
    status = watcher.statuses()[0]
    assert status["state"] == "NO SIGNAL"
    assert status["can_record"]
    assert f"udp 127.0.0.1:{port}" in status["reason"]
    assert f"video: waiting for the camera on udp 127.0.0.1:{port}" in watcher.events()


def test_a_port_another_program_holds_is_reported(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
        other.bind(("127.0.0.1", 0))
        watcher = Watcher()
        source = start_source(watcher, tmp_path, port=other.getsockname()[1])
        try:
            wait_until(watcher.statuses)
            source.set_recording(True)
        finally:
            source.stop()
    status = watcher.statuses()[0]
    assert status["state"] == "ERROR"
    assert not status["can_record"]
    assert "(another program is receiving it)" in status["reason"]
    assert f"cannot record video: {status['reason']}" in watcher.events()
    assert list(tmp_path.iterdir()) == []


def test_without_ffmpeg_there_is_no_picture_but_recording_works(stream, tmp_path):
    port = free_port()
    watcher = Watcher()
    source = start_source(watcher, tmp_path, port=port, ffmpeg="no-such-ffmpeg")
    try:
        wait_until(watcher.statuses)  # its socket is open: nothing sent from now on is lost
        source.set_recording(True)
        send(port, stream, 1.5)
        time.sleep(0.5)  # the last datagrams reach the file
        source.set_recording(False)
    finally:
        source.stop()
    errors = [s for s in watcher.statuses() if s["state"] == "ERROR"]
    assert "no-such-ffmpeg not found: no picture, but recording works" in errors[0]["reason"]
    assert watcher.frames == []
    (recording,) = tmp_path.glob("*_video.ts")
    assert recording.read_bytes() == stream  # every byte, as it came


@needs_ffmpeg
def test_a_decoder_that_makes_no_picture_is_restarted_and_reported_once(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "FROZEN_S", 0.5)
    monkeypatch.setattr(video, "RESTART_DELAY_S", 0.1)
    started = []
    popen = subprocess.Popen

    def counting_popen(*args, **kwargs):
        started.append(args[0])
        return popen(*args, **kwargs)

    monkeypatch.setattr(video.subprocess, "Popen", counting_popen)
    port = free_port()
    watcher = Watcher()
    source = start_source(watcher, tmp_path, port=port)
    try:
        send(port, bytes(range(256)) * 300, 3.0)  # not MPEG-TS
    finally:
        source.stop()
    assert len(started) >= 3
    reports = [text for text in watcher.events() if text.startswith("video decoder")]
    assert len(reports) == 1, reports
    assert watcher.frames == []
