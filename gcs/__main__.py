"""Start the ground station: python -E -m gcs [--config FILE] [--conn URL] [--port N].

Settings come from the defaults, then config.toml (or the file given with
--config), then the command line.
"""

import argparse
import logging
import socket
import sys
import urllib.request
from dataclasses import replace
from pathlib import Path

import uvicorn

from gcs.config import Config, ConfigError, load_config
from gcs.server import create_app

DEFAULT_CONFIG = Path("config.toml")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m gcs", description="Gözcü ground station")
    parser.add_argument(
        "--config", type=Path, help=f"settings file (default: {DEFAULT_CONFIG}, if present)"
    )
    parser.add_argument("--conn", help="MAVLink connection string, e.g. tcp:127.0.0.1:5762")
    parser.add_argument("--port", type=int, help="HTTP port for the browser")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    if args.config is not None:
        config = load_config(args.config)  # a file asked for by name must exist
    elif DEFAULT_CONFIG.exists():
        config = load_config(DEFAULT_CONFIG)
    else:
        config = Config()
    if args.conn:
        config = replace(config, mavlink=replace(config.mavlink, connection=args.conn))
    if args.port:
        config = replace(config, server=replace(config.server, port=args.port))
    return config


def address_taken(host: str, port: int) -> str | None:
    """Why the station cannot listen on host:port, or None if it can.

    Asked before starting: uvicorn starts the application (the link, the video,
    the recording) before it listens, so a station that cannot listen would first
    take the simulator's port and leave an empty recording behind.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        # As uvicorn does, so that a station stopped a moment ago does not count.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            return exc.strerror or str(exc)
    return None


def is_station(url: str) -> bool:
    """Whether a Gözcü ground station answers at url."""
    try:
        with urllib.request.urlopen(url, timeout=1.0) as page:
            return "<title>Gözcü GCS</title>".encode() in page.read(4096)
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = build_config(args)
    except (ConfigError, OSError) as exc:
        print(f"gcs: {exc}", file=sys.stderr)
        return 2
    url = f"http://{config.server.host}:{config.server.port}"
    problem = address_taken(config.server.host, config.server.port)
    if problem is not None:
        if is_station(url):
            problem = "a Gözcü GCS is already running there"
        print(f"gcs: cannot listen on {url}: {problem}", file=sys.stderr)
        print("gcs: stop that one first, or give this one another --port", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print(f"Gözcü GCS at {url}  (MAVLink {config.mavlink.connection})", flush=True)
    uvicorn.run(
        create_app(config), host=config.server.host, port=config.server.port, log_level="warning"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
