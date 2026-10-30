#!/usr/bin/env bash
# Gözcü GCS in one command: the first run installs it into .venv, every run starts it.
#
#   ./start.sh                              the station, at http://127.0.0.1:8000
#   ./start.sh --conn /dev/ttyUSB0,57600     with any option of python -m gcs (--help)
#
# It installs again whenever pyproject.toml has changed, as after a git pull.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
  echo "Gözcü GCS needs Python 3.12 or newer; python3 here is $(python3 --version 2>&1)" >&2
  exit 1
fi

installed=.venv/installed-pyproject.toml  # what the packages in .venv came from
if ! cmp -s pyproject.toml "$installed"; then
  echo ">>> Installing Gözcü GCS into .venv (the first time, this needs the internet)"
  if ! .venv/bin/python -E -m pip --version >/dev/null 2>&1; then
    rm -rf .venv  # none yet, or one left half-made
    if ! python3 -m venv .venv; then
      echo "Cannot make .venv; on Ubuntu: sudo apt install python3-venv" >&2
      exit 1
    fi
  fi
  .venv/bin/python -E -m pip install --quiet --disable-pip-version-check -e ".[dev]"
  cp pyproject.toml "$installed"
fi

if ! command -v ffmpeg >/dev/null; then
  echo "Note: without ffmpeg there is no video picture (sudo apt install ffmpeg)" >&2
fi
# -E: where ROS is sourced, PYTHONPATH would bring its packages in
exec .venv/bin/python -E -m gcs "$@"
