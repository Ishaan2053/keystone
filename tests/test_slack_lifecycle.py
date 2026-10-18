"""Task 4: application lifecycle for the shared Slack notification coordinator.

Live single-tenant sandbox composition builds one ``NotificationStore`` and one
coordinator, with a sender (and client) only for a valid nonblank webhook URL. The
application closes only a client it created itself, after the webhook worker
drains. Fixture mode never builds a store, sender or client, regardless of what is
injected, and a builder failure disables Slack without disabling the worker.
"""

from __future__ import annotations

import logging
import threading

import httpx
from fastapi.testclient import TestClient
from test_webhook_pipeline import make_job, wait_until

import api.main
from api.config import Settings
from api.fixture_store import FixtureStore
from api.main import create_app
from api.webhooks import WebhookWorker

SANDBOX = "acme/keystone-sandbox"
TENANT = "falkordb"
BOT = "keystone-bot"
SECRET = "webhook-secret"
VALID_URL = "https://hooks.slack.com/services/T000/B000/abc123"


def live_settings(**overrides) -> Settings:
    values = dict(
        tenants=(TENANT,),
        api_mode="live",
        sandbox_repo=SANDBOX,
        bot_login=BOT,
        webhook_secret=SECRET,
        public_base_url="https://keystone.example",
    )
    values.update(overrides)
    return Settings(**values)


async def _unused_triage(tenant, issue_key):
    raise AssertionError("triage must not run in a lifecycle test")


class _Writer:
    repo = SANDBOX


class _Service:
    writer = _Writer()


def _forbidden(*_args, **_kwargs):
    raise AssertionError("this dependency must not be constructed")


def test_fixture_mode_constructs_no_notification_dependencies(monkeypatch):
    monkeypatch.setattr(api.main, "NotificationStore", _forbidden)
    monkeypatch.setattr(api.main, "SlackSender", _forbidden)
    monkeypatch.setattr(api.main, "_build_slack_client", _forbidden)

    app = create_app(Settings(api_mode="fixtures"), fixture_store=FixtureStore.default())

    assert app.state.notifications is None


def test_fixture_mode_ignores_an_injected_notifier_and_client(monkeypatch):
    monkeypatch.setattr(api.main, "NotificationStore", _forbidden)
    monkeypatch.setattr(api.main, "_build_slack_client", _forbidden)
    injected_client = httpx.Client()

    app = create_app(
        Settings(api_mode="fixtures"),
        fixture_store=FixtureStore.default(),
        notifications=object(),
        slack_client=injected_client,
    )

    assert app.state.notifications is None
    # An injected client is not ours to close.
    assert injected_client.is_closed is False
    injected_client.close()


def test_live_without_a_valid_slack_url_opens_no_client(monkeypatch):
    monkeypatch.setattr(api.main, "_build_slack_client", _forbidden)

    for url in (None, "", "http://hooks.slack.com/services/T/B/C", "https://evil.example/x"):
        app = create_app(live_settings(slack_webhook_url=url))
        notifications = app.state.notifications
        assert notifications is not None
        assert notifications._sender is None


def test_owned_client_is_closed_after_the_worker_drains(monkeypatch):
    events: list[str] = []

    class SpyClient(httpx.Client):
        def close(self):
            events.append("slack close")
            super().close()

    class FakeWorker:
        def start(self):
            events.append("worker start")

        def close(self):
            events.append("worker close")

    monkeypatch.setattr(api.main, "_build_slack_client", lambda: SpyClient())
    app = create_app(
        live_settings(slack_webhook_url=VALID_URL),
        webhook_worker=FakeWorker(),
    )
    notifications = app.state.notifications
    assert notifications is not None and notifications._sender is not None

    with TestClient(app):
        assert events == ["worker start"]

    assert events == ["worker start", "worker close", "slack close"]


def test_an_injected_slack_client_remains_open(monkeypatch):
    injected = httpx.Client()
    app = create_app(live_settings(slack_webhook_url=VALID_URL), slack_client=injected)

    notifications = app.state.notifications
    assert notifications is not None
    assert notifications._sender.client is injected

    with TestClient(app):
        pass

    assert injected.is_closed is False
    injected.close()


def test_a_notification_builder_failure_disables_slack_only(monkeypatch, caplog):
    def boom(*_args, **_kwargs):
        raise RuntimeError("probe builder boom")

    monkeypatch.setattr(api.main, "NotificationStore", boom)

    app = create_app(
        live_settings(),
        agent_triage=_unused_triage,
        apply_service=_Service(),
    )

    assert app.state.notifications is None
    assert app.state.webhook_worker is not None
    assert not any(SECRET in record.getMessage() for record in caplog.records)


def test_shutdown_drains_before_slack_client_close(monkeypatch):
    """The worker's accepted backlog drains before the app closes its Slack client.

    The runner is held in flight on a gate so shutdown really has to drain an
    ``in-flight`` job, not merely observe one that finished before it started.
    """
    events: list[str] = []
    started = threading.Event()
    gate = threading.Event()

    class SpyClient(httpx.Client):
        def close(self):
            events.append("slack close")
            super().close()

    def runner(job):
        events.append("job start")
        started.set()
        assert gate.wait(5), "shutdown never released the in-flight job"
        events.append("job end")

    monkeypatch.setattr(api.main, "_build_slack_client", lambda: SpyClient())
    worker = WebhookWorker(runner)
    app = create_app(live_settings(slack_webhook_url=VALID_URL), webhook_worker=worker)
    notifications = app.state.notifications
    assert notifications is not None and notifications._sender is not None

    with TestClient(app):
        assert worker.enqueue(make_job()) is True
        wait_until(started.is_set)
        # Release the held job only while shutdown is draining it.
        threading.Timer(0.2, gate.set).start()

    # The drained job finishes before the only Slack client this app owns closes.
    assert events == ["job start", "job end", "slack close"]


def test_no_secret_in_logs_at_debug(caplog):
    """A successful and a failing composed Slack request leak no URL, even at DEBUG."""
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(200, text="ok")
        return httpx.Response(500, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    app = create_app(live_settings(slack_webhook_url=VALID_URL), slack_client=client)
    notifications = app.state.notifications
    assert notifications is not None and notifications._sender is not None

    # The loggers that would otherwise print the full request line are at DEBUG.
    for name in ("httpx", "httpcore", "api.slack"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    with caplog.at_level(logging.DEBUG):
        assert notifications._sender.send({"text": "hello"}) is True
        assert notifications._sender.send({"text": "hello"}) is False

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert VALID_URL not in messages
    assert "T000" not in messages and "abc123" not in messages
    # The quiet window restored the previous levels rather than leaving them lowered.
    assert logging.getLogger("httpx").level == logging.DEBUG
    assert logging.getLogger("httpcore").level == logging.DEBUG
    # Both requests really hit the transport, so the leak check covered success and failure.
    assert seen == [VALID_URL, VALID_URL]
    client.close()
