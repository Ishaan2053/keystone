"""The API container receives ``SLACK_WEBHOOK_URL``; the frontend never does.

Follows ``tests/test_compose_llm_cache.py`` / ``tests/test_infra.py``: render the
Compose config to JSON with a scrubbed environment and an empty ``--env-file``,
using a dummy webhook so no real secret is ever needed.
"""
from __future__ import annotations

import json

from tests.test_infra import compose_config

DUMMY_WEBHOOK = "https://hooks.slack.com/services/TDUMMY/BDUMMY/DUMMYTOKEN"
REQUIRED_ENV = {
    "FALKORDB_PASSWORD": "disposable-test-password",
    "API_DOMAIN": "api.example.invalid",
}


def _services(env):
    # An empty --env-file keeps a developer's real .env out of the render.
    result = compose_config(env, "--env-file", "/dev/null")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def test_the_api_container_maps_the_slack_webhook():
    services = _services({**REQUIRED_ENV, "SLACK_WEBHOOK_URL": DUMMY_WEBHOOK})

    assert services["api"]["environment"]["SLACK_WEBHOOK_URL"] == DUMMY_WEBHOOK


def test_the_slack_webhook_defaults_to_empty_on_the_api_only():
    services = _services(dict(REQUIRED_ENV))

    assert services["api"]["environment"]["SLACK_WEBHOOK_URL"] == ""
    for name, service in services.items():
        present = "SLACK_WEBHOOK_URL" in service.get("environment", {})
        assert present is (name == "api"), name


def test_the_slack_webhook_stays_out_of_frontend_settings():
    services = _services({**REQUIRED_ENV, "SLACK_WEBHOOK_URL": DUMMY_WEBHOOK})

    for name, service in services.items():
        for key, value in service.get("environment", {}).items():
            assert not key.startswith("NEXT_PUBLIC_"), f"{name}:{key}"
            if name != "api":
                assert DUMMY_WEBHOOK not in str(value), f"{name}:{key}"
