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
