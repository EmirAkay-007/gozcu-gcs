import json

from gcs.config import Config, RecordingConfig
from gcs.recorder import FOLDER_NAME, desktop_dir, log_directory, open_recorder, session_header

EVENT = {"type": "event", "time": 1.0, "source": "link", "severity": "INFO", "text": "hello"}


def snapshot(time, seq):
    """A snapshot with one fresh and one stale field."""
    fresh = {"value": 12.5, "text": "12.5", "reason": None, "age": 0.1, "stale": False}
    stale = {"value": 39.9, "text": "39.9", "reason": None, "age": 20.0, "stale": True}
    return {
        "type": "snapshot",
        "seq": seq,
        "time": time,
        "link": {"state": "CONNECTED", "connection": "tcp:127.0.0.1:5762", "vehicle": None},
        "fields": {"airspeed": fresh | {"level": None}, "home_lat": stale | {"level": None}},
    }


def record_session(tmp_path, items, values_hz=2.0):
    """Record items into a fresh file under tmp_path; returns its lines as dicts."""
    settings = RecordingConfig(directory=str(tmp_path), values_hz=values_hz)
    recorder = open_recorder(settings, session_header(Config(), now=1_000_000.0))
    for item in items:
        recorder.record(item)
    recorder.close()
    return [json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()]


def write_user_dirs(home, line):
    (home / ".config").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text(f"# xdg-user-dirs\n{line}\n", encoding="utf-8")


def test_the_desktop_comes_from_user_dirs(tmp_path):
    (tmp_path / "Masaüstü").mkdir()
    write_user_dirs(tmp_path, 'XDG_DESKTOP_DIR="$HOME/Masaüstü"')
    assert desktop_dir(tmp_path) == tmp_path / "Masaüstü"


def test_without_a_desktop_the_home_folder_is_used(tmp_path):
    assert desktop_dir(tmp_path) == tmp_path  # no user-dirs.dirs at all
    write_user_dirs(tmp_path, 'XDG_DESKTOP_DIR="$HOME/Desktop"')  # named, but missing
    assert desktop_dir(tmp_path) == tmp_path


def test_the_log_directory_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert log_directory("~/logs") == tmp_path / "logs"
    assert log_directory("", home=tmp_path) == tmp_path / FOLDER_NAME


def test_the_file_starts_with_the_session_header(tmp_path):
    (header,) = record_session(tmp_path, [])
    assert header["type"] == "session"
    assert header["connection"] == "tcp:127.0.0.1:5762"
    assert header["units"]["airspeed"] == "m/s"
    assert set(header["versions"]) == {"gozcu-gcs", "pymavlink"}


def test_every_event_is_kept_but_values_are_thinned_out(tmp_path):
    items = [snapshot(n / 10, seq=n) for n in range(10)] + [EVENT]  # 10 Hz for 1 s
    lines = record_session(tmp_path, items, values_hz=2.0)
    assert [line["seq"] for line in lines if line["type"] == "values"] == [0, 5]
    assert lines[-1] == EVENT


def test_a_stale_value_is_recorded_as_null(tmp_path):
    _, values = record_session(tmp_path, [snapshot(0.0, seq=1)])
    assert values["values"] == {"airspeed": 12.5, "home_lat": None}
    assert values["link"] == "CONNECTED"


def test_gozcu_values_and_tracks_are_recorded_once_it_is_heard(tmp_path):
    fresh = {"value": 1, "text": "ADVISORY", "reason": None, "age": 0.2, "stale": False}
    stale = {"value": 9.5, "text": "9.5", "reason": None, "age": 3.0, "stale": True}
    track = {"id": 3, "class": "drone", "conf": 0.9, "az": 10.0, "el": -2.0}
    tracks = {"items": [track], "age": 0.1, "stale": False}
    silent = {"heard": False, "present": False, "fields": {}, "tracks": tracks}
    heard = {
        "heard": True,
        "present": True,
        "fields": {"state": fresh | {"level": None}, "fps": stale | {"level": None}},
        "tracks": tracks,
    }
    _, before, after = record_session(
        tmp_path,
        [snapshot(0.0, seq=1) | {"gozcu": silent}, snapshot(1.0, seq=2) | {"gozcu": heard}],
    )
    assert "gozcu" not in before
    assert after["gozcu"] == {"state": 1, "fps": None}
    assert after["tracks"] == [track]


def test_sessions_never_overwrite_each_other(tmp_path):
    header = session_header(Config(), now=1_000_000.0)
    settings = RecordingConfig(directory=str(tmp_path))
    first = open_recorder(settings, header)
    second = open_recorder(settings, header)  # same start second
    first.close()
    second.close()
    assert first.path != second.path
    assert second.path.name.endswith("_2.jsonl")


def test_recording_after_close_is_ignored(tmp_path):
    settings = RecordingConfig(directory=str(tmp_path))
    recorder = open_recorder(settings, session_header(Config(), now=0.0))
    recorder.close()
    recorder.record(EVENT)  # must neither raise nor write
    assert len(recorder.path.read_text(encoding="utf-8").splitlines()) == 1


def test_an_item_that_cannot_be_encoded_is_skipped(tmp_path):
    lines = record_session(tmp_path, [{"type": "event", "text": float("nan")}, EVENT])
    assert lines[-1] == EVENT
