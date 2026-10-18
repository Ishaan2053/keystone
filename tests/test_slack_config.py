"""``SLACK_WEBHOOK_URL`` is optional, secret and value-safe (Slack notifications, Task 1)."""

from __future__ import annotations

import logging

import pytest

from api.config import Settings, load_settings
from api.slack import INVALID_URL_WARNING

VALID = "https://hooks.slack.com/services/T00000000/B00000000/abcdefghijklmnop"
TOKEN = "SECRETTOKEN"


def test_secret_setting_is_optional_and_redacted(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert load_settings().slack_webhook_url is None
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/secret")
    settings = load_settings()
    assert settings.slack_webhook_url is not None
    assert "secret" not in repr(settings)


def test_setting_is_an_optional_non_repr_field():
    field = Settings.__dataclass_fields__["slack_webhook_url"]
    assert field.default is None
    assert field.repr is False
    assert Settings().slack_webhook_url is None


@pytest.mark.parametrize(
    "value",
    [
        "http://hooks.slack.com/services/T000/B000/SECRETTOKEN",  # not HTTPS
        "https://evil.example/services/T000/B000/SECRETTOKEN",  # foreign host
        "https://hooks.slack.com.evil.example/services/T000/B000/SECRETTOKEN",  # suffix
        "https://hooks.slack.com@evil.example/services/T000/B000/SECRETTOKEN",  # userinfo trick
        "https://user:pass@hooks.slack.com/services/T000/B000/SECRETTOKEN",  # userinfo
        "https://hooks.slack.com:8443/services/T000/B000/SECRETTOKEN",  # explicit port
        "https://hooks.slack.com/services/T000/B000/SECRETTOKEN?x=1",  # query
        "https://hooks.slack.com/services/T000/B000/SECRETTOKEN#frag",  # fragment
        "https://hooks.slack.com/services/T000/B000/SECRETTOKEN;x=1",  # params
        "https://hooks.slack.com/",  # empty path
        "https://hooks.slack.com/services/T000/B000",  # too few segments
        "https://hooks.slack.com/services/T000/B000/SECRETTOKEN/extra",  # too many segments
        "https://hooks.slack.com/services/T000/B000/SECRETTOKEN/",  # trailing separator
        "https://hooks.slack.com/services/T000/B000%2FSECRETTOKEN",  # encoded separator
        "https://hooks.slack.com/services/T000/B000/SECRETTOKEN%2f",  # encoded separator
        "hooks.slack.com/services/T000/B000/SECRETTOKEN",  # missing scheme
    ],
)
def test_invalid_url_disables_without_echoing_secret(monkeypatch, caplog, value):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", value)

    with caplog.at_level(logging.WARNING, logger="api.slack"):
        settings = load_settings()

    assert settings.slack_webhook_url is None
    assert INVALID_URL_WARNING in caplog.text
    assert TOKEN not in caplog.text
    assert value not in caplog.text


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_disables_slack_without_a_warning(monkeypatch, caplog, blank):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", blank)

    with caplog.at_level(logging.DEBUG, logger="api.slack"):
        settings = load_settings()

    assert settings.slack_webhook_url is None
    assert not [record for record in caplog.records if record.name == "api.slack"]


def test_valid_url_is_stripped_and_kept(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", f"  {VALID}  ")
    assert load_settings().slack_webhook_url == VALID
