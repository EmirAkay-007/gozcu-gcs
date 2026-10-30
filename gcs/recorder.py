"""Session recording: one JSON Lines file per run of the ground station.

The file starts with a "session" line (start time, versions, link, the unit of
every field). After it comes every event as it was published and, at most
values_hz times a second, a "values" line with the value of every field: null
where it is unknown or stale, so an old value never passes for a fresh one.
Once Gözcü has been heard, the line also has its values and its current tracks.
That is enough to replay a session or measure separations afterwards, at a few
MB an hour. A thread of its own does the writing, so a slow disk (the UNO Q's
flash, a USB stick) never holds up the telemetry.
"""

import json
import logging
import queue
import threading
from contextlib import suppress
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, TextIO

from gcs.config import Config, RecordingConfig
from gcs.fields import FIELDS
from gcs.gozcu import GOZCU_FIELDS

FOLDER_NAME = "Gözcü GCS logs"
log = logging.getLogger("gcs.recorder")
_CLOSE = object()  # tells the writer thread to finish


def desktop_dir(home: Path | None = None) -> Path:
    """The desktop folder named in ~/.config/user-dirs.dirs, or the home folder without one."""
    home = home or Path.home()
    try:
        lines = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return home
    for line in lines:
        if line.startswith("XDG_DESKTOP_DIR="):
            value = line.split("=", 1)[1].strip().strip('"')
            path = Path(value.replace("$HOME", str(home)))
            if path.is_dir():
                return path
    return home


def log_directory(setting: str, home: Path | None = None) -> Path:
    """Where recordings go: the configured folder, or one on the desktop."""
    if setting:
        return Path(setting).expanduser()
    return desktop_dir(home) / FOLDER_NAME


def session_header(config: Config, now: float) -> dict[str, Any]:
    return {
        "type": "session",
        "time": now,
        "started": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "versions": {"gozcu-gcs": version("gozcu-gcs"), "pymavlink": version("pymavlink")},
        "connection": config.mavlink.connection,
        "units": {field.key: field.unit for field in FIELDS},
        "gozcu_units": {field.key: field.unit for field in GOZCU_FIELDS},
    }


def encode_line(item: dict[str, Any]) -> str:
    """One line of the file; a snapshot is reduced to the values of its fields."""
    if item["type"] == "snapshot":
        line = {
            "type": "values",
            "time": item["time"],
            "seq": item["seq"],
            "link": item["link"]["state"],
            "values": _fresh_values(item["fields"]),
        }
        gozcu = item.get("gozcu")
        if gozcu is not None and gozcu["heard"]:
            line["gozcu"] = _fresh_values(gozcu["fields"])
            tracks = gozcu["tracks"]
            line["tracks"] = [] if tracks["stale"] else tracks["items"]
        item = line
    return json.dumps(item, allow_nan=False, ensure_ascii=False, separators=(",", ":")) + "\n"


def _fresh_values(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {key: None if field["stale"] else field["value"] for key, field in fields.items()}


def file_stamp(now: float) -> str:
    """A start time as recordings are named, e.g. 2026-10-29_13-36-03."""
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H-%M-%S")


def create_new(folder: Path, stem: str, suffix: str) -> Path:
    """Create an empty file folder/stem+suffix, or stem_2+suffix and so on if that is
    taken: a recording never overwrites another. Raises OSError if it cannot."""
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        path = folder / (f"{stem}{suffix}" if n == 1 else f"{stem}_{n}{suffix}")
        try:
            path.open("x").close()
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"{folder}: too many recordings named {stem}")


def open_recorder(settings: RecordingConfig, header: dict[str, Any]) -> "Recorder":
    """Start a new file named after the start time; raises OSError if that is not possible."""
    path = create_new(log_directory(settings.directory), file_stamp(header["time"]), ".jsonl")
    return Recorder(path.open("w", encoding="utf-8"), path, header, settings.values_hz)


class Recorder:
    """Writes one session file. record() may be called from any thread and never blocks."""

    def __init__(self, file: TextIO, path: Path, header: dict[str, Any], values_hz: float) -> None:
        self.path = path
        self._file = file
        self._period = 1.0 / values_hz
        self._next_values = float("-inf")  # time the next values line is due
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._closed = False
        self._queue.put(header)
        self._thread = threading.Thread(target=self._write_all, name="recorder", daemon=True)
        self._thread.start()

    def record(self, item: dict[str, Any]) -> None:
        """Queue an event, or a snapshot when a values line is due. Other items, such as
        the video status, are not kept: what changes in them is also an event."""
        if self._closed or item["type"] not in ("event", "snapshot"):
            return
        if item["type"] == "snapshot":
            if item["time"] < self._next_values:
                return
            # Stay on a fixed grid, so a slightly early snapshot does not slow the rate.
            self._next_values += self._period
            if self._next_values <= item["time"]:
                self._next_values = item["time"] + self._period
        self._queue.put(item)

    def close(self) -> None:
        """Write whatever is queued, then close the file."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(_CLOSE)
        self._thread.join(timeout=5)

    def _write_all(self) -> None:
        try:
            while (item := self._queue.get()) is not _CLOSE:
                try:
                    line = encode_line(item)
                except (TypeError, ValueError) as exc:  # one bad item must not end the file
                    log.warning("not recorded: %s", exc)
                    continue
                self._file.write(line)
                if self._queue.empty():
                    self._file.flush()  # written lines survive a crash of this process
        except OSError as exc:  # e.g. the disk is full
            log.error("recording stopped: %s", exc)
        finally:
            self._closed = True
            with suppress(OSError):
                self._file.close()
