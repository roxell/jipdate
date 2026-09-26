import logging

import pytest
import yaml

from jipdate import cfg


@pytest.fixture
def config_dirs(tmp_path, monkeypatch):
    app, home, home_config = (tmp_path / d for d in ("app", "home", "config"))
    for d in (app, home):
        d.mkdir()
    monkeypatch.setattr(
        cfg, "config_locations", [str(app), str(home), str(home_config)]
    )
    monkeypatch.setattr(cfg, "config_path", str(home_config))
    monkeypatch.setattr(cfg, "config_file", None)
    monkeypatch.setattr(cfg, "yml_config", None)
    return app, home, home_config


def test_validate_accepts_default_config(config_dirs):
    cfg.create_default_config()
    config = yaml.safe_load((config_dirs[2] / ".jipdate.yml").read_text())

    assert cfg.validate_yaml_structure(config) == []


def test_validate_accepts_server_sections():
    config = {
        "server": {"url": "https://a", "token": "t"},
        "test_server": {"url": "https://b"},
    }
    assert cfg.validate_yaml_structure(config) == []


@pytest.mark.parametrize(
    "config, error",
    [
        (
            {"server": "https://a"},
            "'server' must be a dictionary/mapping with nested fields",
        ),
        (
            {"test_server": {"name": "x"}},
            "'test_server' section exists but contains no expected fields"
            " - they should be indented under 'test_server:'",
        ),
        (
            {"url": "https://a"},
            "Found 'url' at root level - it should be indented under"
            " 'server:' or 'test_server:'",
        ),
        (
            {"server": {"url": "https://a"}, "token": "t"},
            "Found 'token' at root level - it should be indented under 'server:'",
        ),
        ({"comments": "text"}, "'comments' should be a list"),
        ({"header": "text"}, "'header' should be a list"),
    ],
)
def test_validate_reports_errors(config, error):
    assert cfg.validate_yaml_structure(config) == [error]


def test_get_server_defaults(monkeypatch):
    monkeypatch.setattr(cfg, "yml_config", {})

    assert cfg.get_server() == cfg.PRODUCTION_SERVER
    assert cfg.get_server(use_test_server=True) == cfg.TEST_SERVER


def test_get_server_from_config(monkeypatch):
    server = {"url": "https://a", "token": "t"}
    test_server = {"url": "https://b"}
    monkeypatch.setattr(
        cfg, "yml_config", {"server": server, "test_server": test_server}
    )

    assert cfg.get_server() == server
    assert cfg.get_server(use_test_server=True) == test_server


def test_get_config_file_default_when_none_exists(config_dirs):
    assert cfg.get_config_file() == f"{config_dirs[2]}/.jipdate.yml"


def test_get_config_file_finds_legacy_name(config_dirs):
    home = config_dirs[1]
    (home / "config.yml").write_text("")

    assert cfg.get_config_file() == f"{home}/config.yml"


def test_get_config_file_prefers_first_location_and_new_name(config_dirs):
    app, home, _ = config_dirs
    (app / "config.yml").write_text("")
    (app / ".jipdate.yml").write_text("")
    (home / ".jipdate.yml").write_text("")

    assert cfg.get_config_file() == f"{app}/.jipdate.yml"


def test_initiate_config_creates_default_file(config_dirs):
    cfg.initiate_config()

    assert cfg.config_file == f"{config_dirs[2]}/.jipdate.yml"
    assert cfg.yml_config["version"] == 1
    assert cfg.yml_config["separator"] == " | "


def test_initiate_config_reads_existing_file(config_dirs):
    home = config_dirs[1]
    (home / ".jipdate.yml").write_text("server:\n  url: https://a\n")

    cfg.initiate_config()

    assert cfg.yml_config == {"server": {"url": "https://a"}}


def test_initiate_config_empty_file(config_dirs):
    (config_dirs[1] / ".jipdate.yml").write_text("")

    cfg.initiate_config()

    assert cfg.yml_config == {}


def test_initiate_config_invalid_file_exits(config_dirs, caplog):
    (config_dirs[1] / ".jipdate.yml").write_text("token: t\n")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as e:
        cfg.initiate_config()

    assert e.value.code == 1
    assert "Configuration validation failed" in caplog.text
    assert "Found 'token' at root level" in caplog.text
