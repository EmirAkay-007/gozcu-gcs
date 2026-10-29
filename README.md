# Gözcü GCS

Browser-based ground station for **Gözcü**, a visual detect-and-avoid (DAA) system for small UAVs built on the Arduino UNO Q.
It shows live telemetry, the camera feed, a map and the Gözcü threat state, and records sessions for later analysis.

> **Status:** early development. Milestone 1 targets live telemetry and video from ArduPlane SITL + Gazebo.

## Development

Requires Python 3.12; the simulator side uses ArduPilot SITL with Gazebo.

```bash
python3 -m venv .venv
.venv/bin/python -E -m pip install -e ".[dev]"
.venv/bin/python -E -m pytest                  # unit tests
.venv/bin/python -E -m ruff check .            # lint
.venv/bin/python -E tools/link_check.py        # check the MAVLink link to SITL (tcp:127.0.0.1:5762)
```

`-E` makes Python ignore `PYTHONPATH`. Where ROS is sourced, that variable points at ROS's
Python packages, which would otherwise leak into this project's environment.

The browser code needs no build step. With Node.js 22.7 or newer installed (on `PATH` or
through nvm), pytest also parses every script in `web/js` and runs the unit tests in
`tests/js`; without Node.js those two tests are skipped. To run only the browser tests:
`node --test tests/js/*.test.js`. The video tests run a real ffmpeg and are skipped without it.

## Running

```bash
.venv/bin/python -E -m gcs                     # then open http://127.0.0.1:8000
```

Settings come from `config.toml` when it exists (start from a copy of `config.example.toml`);
`--conn` and `--port` override it, e.g. `--conn udpin:0.0.0.0:14551`.

## Map

The map draws OpenStreetMap tiles, so it needs internet. Without it, or with
`[map] tile_url = ""`, the vehicle, its track and home are drawn on a plain grid. For
offline use, point `tile_url` at a tile server of your own: OpenStreetMap's servers must
not be bulk-downloaded or used offline. Licenses of everything from elsewhere are listed
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Video

The camera stream, H.264 in MPEG-TS, arrives on UDP `127.0.0.1:5600`, where Gazebo's
camera plugin sends it once its streaming has been enabled (`enable_streaming` on the
camera's topic). ffmpeg (`sudo apt install ffmpeg`) turns it into JPEG frames for the
video panel; without ffmpeg there is no picture, but recording still works. `[video]` sets
the address, a frame-rate limit for the browser (`max_fps`), or turns video off.

**REC** records the stream exactly as it arrives, without re-encoding, into the logs
folder as `<start time>_video.ts` (plays in VLC, mpv or ffplay; the first half second can
be grey until the next keyframe).

Measured with the Gazebo camera (640x480, about 19 fps, 0.9 Mbit/s) on an i7-13650HX:
ffmpeg needs about 12 % of one core for every frame (8 % with `max_fps = 10`), and a
frame reaches the browser about 100 ms after its first packet, all but 4 ms of that
because MPEG-TS completes a frame only when the next one begins. Recording costs no
measurable CPU.

## Recordings

Every run is written to a JSON Lines file named after its start time
(`2026-10-29_13-36-03.jsonl`) in a `Gözcü GCS logs` folder on the desktop, or in the folder
set as `[recording] directory`. The first line describes the session; then come all events
and, twice a second, the value of every field (`null` when unknown or stale): about 4 MB an hour.
