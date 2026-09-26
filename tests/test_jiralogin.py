import logging
import os

import pytest
import yaml
from jira import JIRAError
from requests.exceptions import JSONDecodeError

from jipdate import cfg, jiralogin


@pytest.fixture
def config(monkeypatch, tmp_path):
    path = tmp_path / ".jipdate.yml"
    path.write_text("version: 1\n")
    monkeypatch.setattr(cfg, "config_file", str(path))
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.delenv("JIRA_PASSWORD", raising=False)

    def config(values):
        monkeypatch.setattr(cfg, "yml_config", values)
        return path

    config({})
    return config


@pytest.fixture
def answers(monkeypatch):
    def answers(*replies):
        replies = iter(replies)
        monkeypatch.setattr("builtins.input", lambda prompt: next(replies))

    return answers


@pytest.fixture
def fake_jira(monkeypatch):
    calls = []

    def fake_jira(error=None):
        def jira(url, basic_auth, options):
            calls.append((url, basic_auth, options))
            if error:
                raise error
            return "jira"

        monkeypatch.setattr(jiralogin, "JIRA", jira)
        return calls

    return fake_jira


@pytest.mark.parametrize(
    "text, hint",
    [
        ("", "Empty response - check network connectivity"),
        ("<html>Please Sign In</html>", "Authentication required"),
        ("captcha", "Authentication required"),
        ("403 Forbidden", "Access denied"),
        ("Down for maintenance", "Server maintenance"),
        ("<html>other</html>", "Received HTML instead of JSON"),
    ],
)
def test_auth_error_message(text, hint):
    message = jiralogin._get_auth_error_message(text, "https://a")

    assert message.startswith(f"Authentication failed: {hint}")
    assert "Open https://a in browser" in message


def test_username_from_env_wins_over_config(config, monkeypatch):
    config({"username": "config.user"})
    monkeypatch.setenv("JIRA_USERNAME", "env.user")

    assert jiralogin.get_username() == "env.user"


def test_username_from_config(config):
    config({"username": "config.user"})

    assert jiralogin.get_username() == "config.user"


def test_username_from_input_is_stored(config, answers):
    path = config({})
    answers("  First.Last@Linaro.org ", "y")

    assert jiralogin.get_username() == "first.last@linaro.org"
    assert yaml.safe_load(path.read_text()) == {
        "version": 1,
        "username": "first.last@linaro.org",
    }


def test_username_from_input_not_stored(config, answers):
    path = config({})
    answers("first.last@linaro.org", "n")

    assert jiralogin.get_username() == "first.last@linaro.org"
    assert path.read_text() == "version: 1\n"


def test_empty_username_exits(config, answers):
    answers("  ")

    with pytest.raises(SystemExit) as e:
        jiralogin.get_username()

    assert e.value.code == os.EX_NOUSER


def test_password_from_env(config, monkeypatch):
    monkeypatch.setenv("JIRA_PASSWORD", "secret")

    assert jiralogin.get_password() == "secret"


def test_password_from_prompt(config, monkeypatch):
    monkeypatch.setattr(jiralogin.getpass, "getpass", lambda: "typed")

    assert jiralogin.get_password() == "typed"


def test_empty_password_exits(config, monkeypatch):
    monkeypatch.setattr(jiralogin.getpass, "getpass", lambda: "")

    with pytest.raises(SystemExit) as e:
        jiralogin.get_password()

    assert e.value.code == os.EX_NOPERM


def test_jira_instance_with_token(config, fake_jira):
    config({"username": "me", "server": {"url": "https://a", "token": "t"}})
    calls = fake_jira()

    assert jiralogin.get_jira_instance(False) == ("jira", "me")
    url, auth, options = calls[0]
    assert (url, auth) == ("https://a", ("me", "t"))
    assert options["rest_api_version"] == "3"


def test_jira_instance_with_password(config, fake_jira, monkeypatch):
    config({"username": "me", "test_server": {"url": "https://test"}})
    monkeypatch.setenv("JIRA_PASSWORD", "secret")
    calls = fake_jira()

    assert jiralogin.get_jira_instance(True) == ("jira", "me")
    url, auth, options = calls[0]
    assert (url, auth) == ("https://test", ("me", "secret"))
    assert options["rest_api_version"] == "3"


def test_jira_instance_html_response_exits(config, fake_jira, caplog):
    config({"username": "me", "server": {"url": "https://a", "token": "t"}})
    fake_jira(JSONDecodeError("Expecting value", "<html>Sign in</html>", 0))

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as e:
        jiralogin.get_jira_instance(False)

    assert e.value.code == os.EX_NOPERM
    assert "Authentication required" in caplog.text


def test_jira_instance_captcha_exits(config, fake_jira, caplog):
    config({"username": "me", "server": {"url": "https://a", "token": "t"}})
    fake_jira(JIRAError(status_code=403, text="CAPTCHA_CHALLENGE"))

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as e:
        jiralogin.get_jira_instance(False)

    assert e.value.code == os.EX_NOPERM
    assert "complete web login/CAPTCHA first" in caplog.text


@pytest.mark.parametrize("text", ["Unauthorized", None])
def test_jira_instance_other_error_is_raised(config, fake_jira, text):
    config({"username": "me", "server": {"url": "https://a", "token": "t"}})
    error = JIRAError(status_code=401, text=text)
    fake_jira(error)

    with pytest.raises(JIRAError) as e:
        jiralogin.get_jira_instance(False)

    assert e.value is error
