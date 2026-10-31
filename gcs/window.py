"""The station as an application: python -m gcs --window, as the desktop launcher
(start.sh --install-launcher) runs it.

It starts the station unless one already answers at its address, then opens the
page in a window of its own: Chromium's application window, which has no address
bar, or else Firefox. When that window closes, the station it started stops with
it; one that was running already is left alone. There is no terminal to read, so
what the station says goes to a log file, and why it could not start comes as a
desktop notification.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

START_TIMEOUT_S = 30.0
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
LOG = STATE_HOME / "gozcu-gcs" / "station.log"
WM_CLASS = "gozcu-gcs"  # the window's class, which the launcher's StartupWMClass names
BROWSERS = (  # (program, the snap it may be): the first one found opens the window
    ("chromium", "chromium"),
    ("chromium-browser", "chromium"),
    ("google-chrome", None),
    ("firefox", "firefox"),
)


def is_station(url: str) -> bool:
    """Whether a Gözcü ground station answers at url."""
    try:
        with urllib.request.urlopen(url, timeout=1.0) as page:
            return "<title>Gözcü GCS</title>".encode() in page.read(4096)
    except OSError:
        return False


def page_url(host: str, port: int) -> str:
    """Where a browser on this computer finds a station listening on host:port."""
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"  # listening on every address, so on this one too
    return f"http://[{host}]:{port}/" if ":" in host else f"http://{host}:{port}/"


def exit_on_hangup() -> None:
    """Make the end of the session (SIGTERM, SIGHUP) an ordinary exit, so that a
    launcher stopped with it still stops the station it started."""
    for ending in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(ending, lambda *_: sys.exit(0))


def run(station: list[str], url: str) -> int:
    """Show the station at url, starting it with the command station if need be."""
    started = None
    if not is_station(url):
        started = start(station)
        if not wait_until_up(url, started):
            stop(started)
            notify(f"The station did not start; {LOG} says why.")
            return 1
    try:
        window = open_window(url)
        if window is None:
            notify("Install Chromium or Firefox to see the station in a window.")
            return 1
        window.wait()
        return 0
    finally:
        if started is not None:
            stop(started)


def start(station: list[str]) -> subprocess.Popen:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as log:  # the newest start's only
        return subprocess.Popen(
            station, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
        )


def wait_until_up(url: str, station: subprocess.Popen) -> bool:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if station.poll() is not None:
            return False  # it gave up; the log says why
        if is_station(url):
            return True
        time.sleep(0.3)
    return False


def stop(process: subprocess.Popen) -> None:
    process.terminate()  # the station closes its recording on the way out
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def open_window(url: str) -> subprocess.Popen | None:
    """The page in a browser process of its own, which ends when its window closes;
    None when there is no browser to open it in."""
    for name, snap in BROWSERS:
        program = shutil.which(name)
        if program is None:
            continue
        profile = profile_folder(program, name, snap)
        if name == "firefox":  # no window without an address bar, but a process of its own
            command = [program, "--no-remote", "--profile", str(profile), url]
        else:
            command = [program, f"--app={url}", f"--user-data-dir={profile}"]
            command += [f"--class={WM_CLASS}", "--no-first-run", "--no-default-browser-check"]
        return subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    return None


def profile_folder(program: str, name: str, snap: str | None) -> Path:
    """The window's own browser profile. A browser installed as a snap may only use
    folders of its own under ~/snap, so the profile goes there for one."""
    snaps = Path.home() / "snap"
    if snap is not None and (program.startswith("/snap/") or (snaps / snap).is_dir()):
        return snaps / snap / "common" / "gozcu-gcs"
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return data_home / "gozcu-gcs" / name


def notify(text: str) -> None:
    """Tell the operator, who may have no terminal: a desktop notification as well."""
    print(f"gcs: {text}", file=sys.stderr)
    program = shutil.which("notify-send")
    if program is not None:
        subprocess.run([program, "Gözcü GCS", text], check=False)
