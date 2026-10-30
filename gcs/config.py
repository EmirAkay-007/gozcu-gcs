"""Settings: the defaults below, overridden by a TOML file, overridden by the command line.

config.example.toml lists every setting with its default; a test keeps the two in sync.
"""

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """A settings file that cannot be used as it is."""


@dataclass(frozen=True)
class MavlinkConfig:
    connection: str = "tcp:127.0.0.1:5762"
    sysid: int = 250
    compid: int = 190
    target_compid: int = 1
    link_lost_s: float = 3.0
    request_rates: bool = True
    gozcu_compid: int = 191  # MAV_COMP_ID_ONBOARD_COMPUTER, see docs/message_contract.md

    def __post_init__(self) -> None:
        for name in ("sysid", "compid", "target_compid", "gozcu_compid"):
            if not 1 <= getattr(self, name) <= 255:
                raise ConfigError(f"[mavlink] {name} must be 1..255")
        if self.gozcu_compid == self.target_compid:
            raise ConfigError("[mavlink] gozcu_compid must differ from target_compid")
        if self.link_lost_s <= 0:
            raise ConfigError("[mavlink] link_lost_s must be positive")


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ConfigError("[server] port must be 1..65535")


@dataclass(frozen=True)
class RecordingConfig:
    enabled: bool = True
    directory: str = ""  # empty: a folder on the desktop (see gcs.recorder)
    values_hz: float = 2.0

    def __post_init__(self) -> None:
        if not 0 < self.values_hz <= 10:  # snapshots come at 10 Hz
            raise ConfigError("[recording] values_hz must be above 0 and at most 10")


@dataclass(frozen=True)
class MapConfig:
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"  # empty: no tiles
    attribution: str = (
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    )
    max_zoom: int = 19

    def __post_init__(self) -> None:
        if not 1 <= self.max_zoom <= 22:
            raise ConfigError("[map] max_zoom must be 1..22")


@dataclass(frozen=True)
class VideoConfig:
    enabled: bool = True
    host: str = "127.0.0.1"  # the address to receive on; "0.0.0.0" takes any sender
    port: int = 5600
    max_fps: float = 0.0  # frames a second sent to the browser; 0: every frame
    ffmpeg: str = "ffmpeg"  # the program that decodes the video

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ConfigError("[video] port must be 1..65535")
        if not 0 <= self.max_fps <= 60:
            raise ConfigError("[video] max_fps must be 0..60")


@dataclass(frozen=True)
class Config:
    mavlink: MavlinkConfig = field(default_factory=MavlinkConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    map: MapConfig = field(default_factory=MapConfig)
    video: VideoConfig = field(default_factory=VideoConfig)


SECTIONS = {
    "mavlink": MavlinkConfig,
    "server": ServerConfig,
    "recording": RecordingConfig,
    "map": MapConfig,
    "video": VideoConfig,
}


def load_config(path: Path) -> Config:
    """Read a TOML settings file; whatever it leaves out keeps its default."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ConfigError(f"{path}: unknown section(s): {', '.join(sorted(unknown))}")
    return Config(
        **{name: _section(name, cls(), data.get(name, {})) for name, cls in SECTIONS.items()}
    )


def _section(name: str, defaults: Any, values: Any) -> Any:
    """The defaults with values applied, after checking every name and type."""
    if not isinstance(values, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {f.name for f in fields(defaults)}
    changes = {}
    for key, value in values.items():
        if key not in known:
            raise ConfigError(f"[{name}] unknown setting: {key}")
        expected = type(getattr(defaults, key))
        if expected is float and type(value) is int:
            value = float(value)  # "link_lost_s = 3" means 3.0
        if type(value) is not expected:
            raise ConfigError(f"[{name}] {key} must be {expected.__name__}, not {value!r}")
        changes[key] = value
    return replace(defaults, **changes)
