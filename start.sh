#!/usr/bin/env bash
# Gözcü GCS in one command: the first run installs it into .venv, every run starts it.
#
#   ./start.sh                              the station, at http://127.0.0.1:8000
#   ./start.sh --conn /dev/ttyUSB0,57600     with any option of python -m gcs (--help)
#   ./start.sh --window                     and the page in a window of its own
#   ./start.sh --install-launcher           a "Gözcü GCS" launcher, in the menu and on
#                                           the desktop, that runs ./start.sh --window
#
# It installs again whenever pyproject.toml has changed, as after a git pull.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

say() {  # to the terminal, or as a notification when there is none (the launcher)
  echo "$1" >&2
  if [ ! -t 2 ] && command -v notify-send >/dev/null; then
    notify-send "Gözcü GCS" "$1"
  fi
}

install_launcher() {
  local here data apps desktop entry
  here=$(pwd)
  if [[ "$here" == *[\"\`\$\\%]* ]]; then  # a desktop entry would need them escaped twice
    say "No launcher for a folder whose path holds \" \` \$ \\ or %: move it first"
    exit 1
  fi
  # The user's data folder, which holds the menu and GIO's metadata. In a snap's
  # terminal (VS Code's), XDG_DATA_HOME and GIO's modules are the snap's own instead.
  if [ -n "${SNAP_REAL_HOME:-}" ]; then
    data="$SNAP_REAL_HOME/.local/share"
  else
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
  fi
  apps="$data/applications"
  desktop=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
  entry="$apps/gozcu-gcs.desktop"
  mkdir -p "$apps"
  cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=Gözcü GCS
GenericName=Ground station
Comment=Gözcü's ground station, in a window of its own
Exec="$here/start.sh" --window
Icon=$here/web/icon.svg
Terminal=false
Categories=Science;Engineering;
Keywords=gcs;mavlink;ardupilot;uav;gozcu;
StartupWMClass=gozcu-gcs
EOF
  chmod +x "$entry"
  update-desktop-database "$apps" 2>/dev/null || true
  echo "In the menu: $entry"
  if [ -d "$desktop" ]; then
    local launcher="$desktop/gozcu-gcs.desktop"
    cp "$entry" "$launcher"
    chmod +x "$launcher"
    echo "On the desktop: $launcher"
    # GNOME's desktop runs a launcher only once it is marked trusted in GIO's metadata;
    # touched after that, the desktop looks at it again and sees the mark.
    if env -u GIO_MODULE_DIR XDG_DATA_HOME="$data" \
      gio set "$launcher" metadata::trusted true 2>/dev/null; then
      touch "$launcher"
    else
      echo "To start it from the desktop, right-click it there and choose Allow Launching"
    fi
  fi
}

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
  say "Gözcü GCS needs Python 3.12 or newer; python3 here is $(python3 --version 2>&1)"
  exit 1
fi

installed=.venv/installed-pyproject.toml  # what the packages in .venv came from
if ! cmp -s pyproject.toml "$installed"; then
  say "Installing Gözcü GCS into .venv (the first time, this needs the internet)"
  if ! .venv/bin/python -E -m pip --version >/dev/null 2>&1; then
    rm -rf .venv  # none yet, or one left half-made
    if ! python3 -m venv .venv; then
      say "Cannot make .venv; on Ubuntu: sudo apt install python3-venv"
      exit 1
    fi
  fi
  if ! .venv/bin/python -E -m pip install --quiet --disable-pip-version-check -e ".[dev]"; then
    say "Installing failed: is there internet? (run ./start.sh in a terminal to see why)"
    exit 1
  fi
  cp pyproject.toml "$installed"
fi

if [ "${1:-}" = "--install-launcher" ]; then
  install_launcher
  exit 0
fi
if ! command -v ffmpeg >/dev/null; then
  echo "Note: without ffmpeg there is no video picture (sudo apt install ffmpeg)" >&2
fi
# -E: where ROS is sourced, PYTHONPATH would bring its packages in
exec .venv/bin/python -E -m gcs "$@"
