from pathlib import Path

import pytest

from gcs.config import Config, ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_example_file_matches_the_defaults():
    assert load_config(ROOT / "config.example.toml") == Config()


def test_settings_left_out_keep_their_defaults(tmp_path):
    config = load_config(write(tmp_path, '[mavlink]\nconnection = "udpin:0.0.0.0:14551"\n'))
    assert config.mavlink.connection == "udpin:0.0.0.0:14551"
    assert config.mavlink.sysid == 250
    assert config.server == Config().server


def test_a_whole_number_is_accepted_for_seconds(tmp_path):
    assert load_config(write(tmp_path, "[mavlink]\nlink_lost_s = 5\n")).mavlink.link_lost_s == 5.0


@pytest.mark.parametrize(
    "text",
    [
        '[mavlink]\nsysid = "250"\n',  # wrong type
        "[mavlink]\nsysid = 0\n",  # not a MAVLink id
        "[mavlink]\nsys_id = 250\n",  # misspelt name
        "[mavlnk]\nsysid = 250\n",  # misspelt section
        "[mavlink]\nrequest_rates = 1\n",  # a number is not a yes/no
        "[server]\nport = 70000\n",
        "mavlink = 5\n",  # not a table
        "[mavlink\n",  # not TOML at all
    ],
)
def test_bad_settings_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, text))
