"""Starting the station: refused at once, with nothing started, when its address is taken."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gcs import __main__ as gcs_main


@pytest.fixture
def nothing_starts(monkeypatch, tmp_path):
    """Fail the test if the station would start; and no config.toml from elsewhere."""

    def run(*args, **kwargs):
        raise AssertionError("the station was started")

    monkeypatch.setattr(gcs_main.uvicorn, "run", run)
    monkeypatch.chdir(tmp_path)


def test_a_free_address_can_be_listened_on():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert gcs_main.address_taken("127.0.0.1", port) is None


def test_a_taken_address_is_refused_before_anything_starts(nothing_starts, capsys):
    with socket.create_server(("127.0.0.1", 0)) as other:
        port = other.getsockname()[1]
        assert gcs_main.main(["--port", str(port)]) == 1
    error = capsys.readouterr().err
    assert f"cannot listen on http://127.0.0.1:{port}:" in error
    assert "another --port" in error


class StationPage(BaseHTTPRequestHandler):
    def do_GET(self):
        page = "<!doctype html><title>Gözcü GCS</title>".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args):
        pass


def test_another_station_on_the_address_is_named(nothing_starts, capsys):
    with HTTPServer(("127.0.0.1", 0), StationPage) as other:
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            assert gcs_main.main(["--port", str(other.server_address[1])]) == 1
        finally:
            other.shutdown()
    assert "a Gözcü GCS is already running there" in capsys.readouterr().err
