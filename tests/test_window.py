"""The station in a window of its own: started for the window, and stopped with it."""

import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gcs import window


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    """The log in tmp_path, and notifications collected instead of shown."""
    notes = []
    monkeypatch.setattr(window, "LOG", tmp_path / "station.log")
    monkeypatch.setattr(window, "notify", notes.append)
    return notes


def a_window(seconds, seen=None, url=None):
    """Stands in for open_window: a process that ends after seconds, as a window
    closed then would; first notes whether a station answered at url."""

    def open_window(_url):
        if seen is not None:
            seen.append(window.is_station(url))
        return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])

    return open_window


def test_the_browser_goes_to_this_computer_for_a_station_listening_everywhere():
    assert window.page_url("0.0.0.0", 8000) == "http://127.0.0.1:8000/"
    assert window.page_url("127.0.0.1", 8001) == "http://127.0.0.1:8001/"
    assert window.page_url("::1", 8000) == "http://[::1]:8000/"


def test_a_station_started_for_the_window_stops_with_it(quiet, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[recording]\nenabled = false\n[video]\nenabled = false\n", encoding="utf-8")
    port = free_port()
    url = window.page_url("127.0.0.1", port)
    station = [sys.executable, "-E", "-m", "gcs", "--config", str(config), "--port", str(port)]
    station += ["--conn", "tcp:127.0.0.1:9"]
    seen = []
    monkeypatch.setattr(window, "open_window", a_window(0.5, seen, url))
    assert window.run(station, url) == 0
    assert seen == [True]  # it was up while the window was open
    assert not window.is_station(url)  # and gone with it
    assert "Gözcü GCS at" in window.LOG.read_text(encoding="utf-8")
    assert quiet == []


class StationPage(BaseHTTPRequestHandler):
    def do_GET(self):
        page = "<!doctype html><title>Gözcü GCS</title>".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args):
        pass


def test_a_station_running_already_is_left_alone(quiet, monkeypatch):
    def start(_station):
        raise AssertionError("a second station was started")

    monkeypatch.setattr(window, "start", start)
    monkeypatch.setattr(window, "open_window", a_window(0.2))
    with HTTPServer(("127.0.0.1", 0), StationPage) as other:
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            url = window.page_url("127.0.0.1", other.server_address[1])
            assert window.run(["unused"], url) == 0
            assert window.is_station(url)  # still there
        finally:
            other.shutdown()


def test_a_station_that_cannot_start_is_reported_and_no_window_opens(quiet, monkeypatch):
    def open_window(_url):
        raise AssertionError("a window was opened")

    monkeypatch.setattr(window, "open_window", open_window)
    failing = [sys.executable, "-c", "import sys; print('no such port'); sys.exit(3)"]
    assert window.run(failing, window.page_url("127.0.0.1", free_port())) == 1
    assert quiet == [f"The station did not start; {window.LOG} says why."]
    assert "no such port" in window.LOG.read_text(encoding="utf-8")


def test_a_window_profile_goes_where_its_browser_may_write(monkeypatch, tmp_path):
    monkeypatch.setattr(window.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    chromium = window.profile_folder("/snap/bin/chromium", "chromium", "chromium")
    assert chromium == tmp_path / "snap/chromium/common/gozcu-gcs"
    chrome = window.profile_folder("/usr/bin/google-chrome", "google-chrome", None)
    assert chrome == tmp_path / ".local/share/gozcu-gcs/google-chrome"
    (tmp_path / "snap/firefox").mkdir(parents=True)  # Ubuntu's /usr/bin/firefox runs a snap
    firefox = window.profile_folder("/usr/bin/firefox", "firefox", "firefox")
    assert firefox == tmp_path / "snap/firefox/common/gozcu-gcs"
