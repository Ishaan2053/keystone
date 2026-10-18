"""``api.slack``: a one-attempt, secret-safe Slack incoming-webhook transport.

No live webhook: every client carries an ``httpx.MockTransport``. The sender
never retries and never follows redirects, and the secret webhook path must not
reach the logs at any level.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from api.slack import DELIVERY_FAILURE_WARNING, SlackSender, validate_webhook_url

WEBHOOK_URL = "https://hooks.slack.com/services/T00000000/B00000000/SECRETTOKEN"
PAYLOAD = {
    "text": "Keystone triage",
    "blocks": [{"type": "section", "text": {"type": "plain_text", "text": "hello"}}],
}


def _sender(handler):
    """Build a sender over a recording ``MockTransport``; return (sender, calls)."""
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return SlackSender(WEBHOOK_URL, client), calls


def test_send_success(caplog):
    sender, calls = _sender(lambda request: httpx.Response(200, text="ok\n"))

    with caplog.at_level(logging.DEBUG):
        assert sender.send(PAYLOAD) is True

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url == httpx.URL(WEBHOOK_URL)
    # The payload reaches Slack unchanged and as JSON.
    assert json.loads(request.content) == PAYLOAD
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in {name.lower() for name in request.headers}
    # An incoming webhook authenticates by its secret path, never a header.
    # httpx resolves the caller's timeout onto the request; 5.0 here proves the
    # bounded, per-network-operation timeout was actually passed.
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }
    assert not [record for record in caplog.records if record.name == "api.slack"]


def _redirect(request):
    return httpx.Response(
        301,
        headers={"location": "https://hooks.slack.com/services/other"},
        text="moved",
    )


_FAILURES = {
    "redirect_301": _redirect,
    "rate_limited_429": lambda request: httpx.Response(429, text="ratelimited"),
    "server_error_500": lambda request: httpx.Response(500, text="boom"),
    "ok_body_required": lambda request: httpx.Response(200, text="invalid_payload"),
}


@pytest.mark.parametrize("name", sorted(_FAILURES))
def test_send_failures_are_one_attempt_and_sanitized(caplog, name):
    sender, calls = _sender(_FAILURES[name])

    with caplog.at_level(logging.DEBUG):
        assert sender.send(PAYLOAD) is False

    # A 301 is returned to the caller, not followed: exactly one attempt.
    assert len(calls) == 1
    assert DELIVERY_FAILURE_WARNING in caplog.text
    assert WEBHOOK_URL not in caplog.text
    assert "hooks.slack.com" not in caplog.text
    for leaked in ("moved", "ratelimited", "boom", "invalid_payload", "services/other"):
        assert leaked not in caplog.text


_TRANSPORT_ERRORS = {
    "connect": httpx.ConnectError("SECRETTOKEN connect detail"),
    "timeout": httpx.ReadTimeout("SECRETTOKEN timeout detail"),
    "client": RuntimeError("SECRETTOKEN client detail"),
}


@pytest.mark.parametrize("name", sorted(_TRANSPORT_ERRORS))
def test_transport_failures_are_one_attempt_and_sanitized(caplog, name):
    error = _TRANSPORT_ERRORS[name]

    def handler(request):
        raise error

    sender, calls = _sender(handler)

    with caplog.at_level(logging.DEBUG):
        assert sender.send(PAYLOAD) is False

    assert len(calls) == 1
    assert DELIVERY_FAILURE_WARNING in caplog.text
    assert WEBHOOK_URL not in caplog.text
    assert "SECRETTOKEN" not in caplog.text
    assert str(error) not in caplog.text


def test_the_full_secret_url_never_appears_at_info_or_debug(caplog):
    sender, _ = _sender(lambda request: httpx.Response(200, text="ok"))

    # Turn the noisy libraries' own loggers on: httpx logs the request line at
    # INFO, and httpcore's per-connection loggers log at DEBUG. Even so, the
    # sender must not let the secret webhook path through.
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="httpcore")
    caplog.set_level(logging.DEBUG, logger="httpcore.connection")

    assert sender.send(PAYLOAD) is True

    assert WEBHOOK_URL not in caplog.text
    assert "hooks.slack.com" not in caplog.text
    assert "SECRETTOKEN" not in caplog.text


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_url_disables_slack_silently(caplog, blank):
    with caplog.at_level(logging.DEBUG, logger="api.slack"):
        assert validate_webhook_url(blank) is None

    assert not [record for record in caplog.records if record.name == "api.slack"]


def test_a_valid_url_is_returned_stripped():
    assert validate_webhook_url(f"  {WEBHOOK_URL}  ") == WEBHOOK_URL


def test_overlapping_sends_keep_secrets_quiet_until_both_finish(caplog):
    """Finishing A must not restore DEBUG while B is still logging its request."""
    a_entered = threading.Event()
    b_entered = threading.Event()
    a_finished = threading.Event()
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.connection"):
        caplog.set_level(logging.DEBUG, logger=name)

    def handle(request):
        if json.loads(request.content)["text"] == "A":
            a_entered.set()
            assert b_entered.wait(2)
        else:
            b_entered.set()
            assert a_finished.wait(2)
            logging.getLogger("httpcore.connection").debug("request %s", request.url)
        return httpx.Response(200, text="ok")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        sender = SlackSender(WEBHOOK_URL, client)

        def first():
            try:
                return sender.send({"text": "A"})
            finally:
                a_finished.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first)
            assert a_entered.wait(2)
            b = pool.submit(sender.send, {"text": "B"})
            assert a.result(timeout=3) is True
            assert b.result(timeout=3) is True

    assert WEBHOOK_URL not in caplog.text
    assert "SECRETTOKEN" not in caplog.text
    for name in ("httpx", "httpcore", "httpcore.connection"):
        assert logging.getLogger(name).level == logging.DEBUG


def test_an_invalid_url_returns_none_and_logs_one_fixed_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger="api.slack"):
        assert validate_webhook_url("not a webhook") is None

    warnings = [record for record in caplog.records if record.name == "api.slack"]
    assert len(warnings) == 1
    assert "not a webhook" not in caplog.text
