from pathlib import Path

import pytest

from simurg.config import load_config


def test_load_config_minimal(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
[directory]
dottorrents_dir = ".torrents"

[image]
cover_uploader = "ptscreens"
ptscreens_key = "abc"

[tracker.simurg]
session = "mysess"
announce_url = "https://example.com/announce"

[upload]
check_requests = true
"""
    )
    cfg = load_config(cfg_file)
    assert cfg.directory.get("dottorrents_dir") == ".torrents"
    assert cfg.image.get("cover_uploader") == "ptscreens"
    assert cfg.get_tracker_cfg("simurg").get("session") == "mysess"
    assert cfg.upload.get("check_requests") is True


def test_load_config_missing_file(tmp_path: Path):
    from simurg.errors import ConfigError

    missing = tmp_path / "nope.toml"
    with pytest.raises(ConfigError):
        load_config(missing)


def test_load_config_invalid_toml(tmp_path: Path):
    from simurg.errors import ConfigError

    bad = tmp_path / "bad.toml"
    bad.write_text("invalid = [")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_config_default_sections(tmp_path: Path):
    empty = tmp_path / "empty.toml"
    empty.write_text('[tracker.simurg]\nsession=""\n')
    cfg = load_config(empty)
    # missing sections should not raise, just return empty
    assert cfg.directory.get("dottorrents_dir", "fallback") == "fallback"
    assert isinstance(cfg.get_tracker_cfg("simurg"), object)
