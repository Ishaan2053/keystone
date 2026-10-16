"""B7 Task 1: the verified GitHub webhook receiver (spec section 13).

The receiver verifies the raw-body HMAC before anything else, validates the
signed payload, ignores events it must not act on, and admits a small validated
job into the serialized worker. It never embeds, calls GitHub or runs an agent
on the request path.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, load_settings
from api.fixture_store import FixtureStore
from api.main import create_app
from api.webhooks import (
    INVALID_PAYLOAD,
    INVALID_SIGNATURE,
    WEBHOOK_UNAVAILABLE,
    WebhookJob,
    WebhookWorker,
    verify_signature,
)

SECRET = "test-webhook-secret"
BOT = "keystone-bot"
SANDBOX = "FalkorDB/keystone-sandbox"
TENANT = "falkordb"

JOB_FIELDS = (
    "delivery_id",
    "tenant",
    "event",
    "action",
    "issue_number",
    "title",
    "body",
    "issue_state",
    "issue_author_login",
    "issue_author_url",
    "issue_author_is_bot",
    "issue_created_at",
    "sender_login",
    "comment_id",
    "comment_body",
)


def live_settings(**overrides) -> Settings:
    values = dict(
        tenants=(TENANT,),
        cors_origins=("http://localhost:3001",),
        api_mode="live",
        sandbox_repo=SANDBOX,
        bot_login=BOT,
        webhook_secret=SECRET,
    )
    values.update(overrides)
    return Settings(**values)


class RecordingWorker:
    """A stand-in worker that records admitted jobs without running them."""

    def __init__(self, admit: bool = True):
        self.admit = admit
        self.jobs: list[WebhookJob] = []
        self.started = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1

    def close(self) -> None:
        self.closed += 1

    def enqueue(self, job: WebhookJob) -> bool:
        if not self.admit:
            return False
        self.jobs.append(job)
        return True


def make_client(worker=None, settings: Settings | None = None) -> TestClient:
    app = create_app(
        settings=settings or live_settings(),
        fixture_store=FixtureStore.default(),
        webhook_worker=worker,
    )
    return TestClient(app)


def issue_event(**overrides) -> dict:
    payload = {
        "action": "opened",
        "repository": {"full_name": SANDBOX},
        "sender": {"login": "octocat"},
        "issue": {
            "number": 42,
            "title": "Graph traversal is slow",
            "body": "It hangs on large graphs.",
            "state": "open",
            "created_at": "2026-10-16T00:00:00Z",
            "user": {
                "login": "octocat",
                "html_url": "https://github.com/octocat",
                "type": "User",
            },
        },
    }
    payload.update(overrides)
    return payload


def comment_event(**overrides) -> dict:
    payload = issue_event(action="created")
    payload["comment"] = {
        "id": 9001,
        "body": "/keystone why octocat",
    }
    payload.update(overrides)
    return payload


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def post(
    client: TestClient,
    payload,
    *,
    event: str = "issues",
    delivery: str | None = "delivery-1",
    signature: str | None = "auto",
    raw: bytes | None = None,
):
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    headers = {"X-GitHub-Event": event}
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    if signature == "auto":
        headers["X-Hub-Signature-256"] = sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/github", content=body, headers=headers)


def block_job(delivery_id: str) -> WebhookJob:
    return WebhookJob(
        delivery_id=delivery_id,
        tenant=TENANT,
        event="issues",
        action="opened",
        issue_number=1,
        title="t",
        body="b",
        issue_state="open",
        issue_author_login="octocat",
        issue_author_url="https://github.com/octocat",
        issue_author_is_bot=False,
        issue_created_at="2026-10-16T00:00:00Z",
        sender_login="octocat",
        comment_id=None,
        comment_body=None,
    )


# --- signature verification -------------------------------------------------


def test_verify_signature_accepts_the_matching_digest():
    body = b'{"hello": "world"}'

    assert verify_signature(body, sign(body), SECRET) is True


def test_verify_signature_rejects_every_malformation():
    body = b"payload"

    assert verify_signature(body, sign(body), "other-secret") is False
    assert verify_signature(body, None, SECRET) is False
    assert verify_signature(body, "", SECRET) is False
    assert verify_signature(body, "sha1=" + hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest(), SECRET) is False
    assert verify_signature(body, hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(), SECRET) is False
    assert verify_signature(body + b"!", sign(body), SECRET) is False
    assert verify_signature(body, sign(body), "") is False


def test_verify_signature_rejects_a_non_ascii_header_without_raising():
    body = b"payload"

    # ``provided`` is attacker-controlled; a non-ASCII value must fail closed
    # (False → a 401), never raise a TypeError (a 500).
    assert verify_signature(body, "sha256=ünïcødé", SECRET) is False
    assert verify_signature(body, "sha256=\udcff", SECRET) is False


def test_receiver_rejects_bad_signatures_even_for_malformed_json():
    worker = RecordingWorker()
    client = make_client(worker)

    missing = post(client, None, signature=None, raw=b"?")
    malformed = post(client, None, signature="sha256=deadbeef", raw=b"{")
    mismatched = post(client, None, signature=sign(b"{"), raw=b"not-the-body")

    for response in (missing, malformed, mismatched):
        assert response.status_code == 401
        assert response.json() == {"detail": INVALID_SIGNATURE}
    assert worker.jobs == []


# --- happy path -------------------------------------------------------------


def test_valid_issue_event_is_admitted_once():
    worker = RecordingWorker()
    client = make_client(worker)

    response = post(client, issue_event())

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert len(worker.jobs) == 1
    job = worker.jobs[0]
    assert job.delivery_id == "delivery-1"
    assert job.tenant == TENANT
    assert job.event == "issues"
    assert job.action == "opened"
    assert job.issue_number == 42
    assert job.title == "Graph traversal is slow"
    assert job.body == "It hangs on large graphs."
    assert job.issue_state == "open"
    assert job.issue_author_login == "octocat"
    assert job.issue_author_url == "https://github.com/octocat"
    assert job.issue_author_is_bot is False
    assert job.issue_created_at == "2026-10-16T00:00:00Z"
    assert job.sender_login == "octocat"
    assert job.comment_id is None and job.comment_body is None


def test_issue_comment_created_is_admitted_with_its_comment():
    worker = RecordingWorker()

    response = post(make_client(worker), comment_event(), event="issue_comment")

    assert response.status_code == 202
    (job,) = worker.jobs
    assert (job.event, job.action) == ("issue_comment", "created")
    assert (job.comment_id, job.comment_body) == (9001, "/keystone why octocat")


def test_issue_edited_is_admitted():
    worker = RecordingWorker()

    response = post(make_client(worker), issue_event(action="edited"))

    assert response.status_code == 202
    assert [job.action for job in worker.jobs] == ["edited"]


def test_job_carries_exactly_the_contract_fields_and_is_frozen():
    worker = RecordingWorker()
    post(make_client(worker), issue_event())

    assert tuple(field.name for field in dataclasses.fields(WebhookJob)) == JOB_FIELDS
    assert WebhookJob.__dataclass_params__.frozen is True


# --- payload validation -----------------------------------------------------


def test_valid_signature_with_invalid_json_is_400():
    worker = RecordingWorker()
    response = post(make_client(worker), None, raw=b"{not json", signature="auto")

    assert response.status_code == 400
    assert worker.jobs == []


@pytest.mark.parametrize("field", ["repository", "sender", "issue"])
def test_valid_signature_with_a_missing_top_level_field_is_400(field):
    payload = issue_event()
    del payload[field]
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 400
    assert worker.jobs == []


def test_valid_signature_with_a_missing_issue_field_is_400():
    payload = issue_event()
    del payload["issue"]["number"]
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 400
    assert worker.jobs == []


@pytest.mark.parametrize("number", ["42", 0, -1, True])
def test_valid_signature_with_an_invalid_issue_number_is_400(number):
    payload = issue_event()
    payload["issue"]["number"] = number
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_PAYLOAD}
    assert worker.jobs == []


def test_missing_delivery_id_is_400():
    worker = RecordingWorker()

    response = post(make_client(worker), issue_event(), delivery=None)

    assert response.status_code == 400
    assert worker.jobs == []


# --- guards: ignore without enqueuing ---------------------------------------


def test_wrong_repository_is_ignored():
    payload = issue_event(repository={"full_name": "someone/other-repo"})
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_repository_comparison_is_case_insensitive():
    payload = issue_event(repository={"full_name": SANDBOX.lower()})
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert len(worker.jobs) == 1


def test_malformed_repository_name_is_ignored_not_a_server_error():
    payload = issue_event(repository={"full_name": "not a repo"})
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_event_from_the_configured_bot_is_ignored():
    payload = issue_event(sender={"login": BOT.upper()})
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_unsupported_action_is_ignored():
    worker = RecordingWorker()

    response = post(make_client(worker), issue_event(action="closed"))

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_unsupported_event_is_ignored():
    worker = RecordingWorker()

    response = post(make_client(worker), issue_event(), event="push")

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_pull_request_payload_is_ignored():
    payload = issue_event()
    payload["issue"]["pull_request"] = {"url": "https://api.github.com/repos/x/y/pulls/1"}
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_explicit_null_pull_request_key_is_still_an_issue():
    payload = issue_event()
    payload["issue"]["pull_request"] = None
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert len(worker.jobs) == 1


# --- configuration fail-closed ---------------------------------------------


def test_no_configured_secret_disables_admission():
    worker = RecordingWorker()
    client = make_client(worker, settings=live_settings(webhook_secret=None))

    response = post(client, issue_event())

    assert response.status_code == 503
    assert response.json() == {"detail": WEBHOOK_UNAVAILABLE}
    assert worker.jobs == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_mode": "fixtures"},
        {"tenants": (TENANT, "ghost")},
        {"tenants": ()},
        {"sandbox_repo": None},
        {"bot_login": None},
    ],
)
def test_disabled_configuration_is_503_only_after_signature_validation(overrides):
    worker = RecordingWorker()
    client = make_client(worker, settings=live_settings(**overrides))

    bad = post(client, issue_event(), signature="sha256=deadbeef")
    good = post(client, issue_event())

    assert bad.status_code == 401
    assert good.status_code == 503
    assert good.json() == {"detail": WEBHOOK_UNAVAILABLE}
    assert worker.jobs == []


def test_live_configuration_without_a_worker_is_503():
    response = post(make_client(worker=None), issue_event())

    assert response.status_code == 503
    assert response.json() == {"detail": WEBHOOK_UNAVAILABLE}


# --- queue admission --------------------------------------------------------


def test_full_queue_is_503_and_the_same_delivery_can_be_retried():
    started = threading.Event()
    release = threading.Event()
    processed: list[str] = []

    def runner(job: WebhookJob) -> None:
        started.set()
        release.wait(5)
        processed.append(job.delivery_id)

    worker = WebhookWorker(runner, maxsize=1)
    worker.start()
    try:
        assert worker.enqueue(block_job("busy")) is True
        assert started.wait(5)
        assert worker.enqueue(block_job("queued")) is True

        client = make_client(worker)
        full = post(client, issue_event(), delivery="retry-me")
        assert full.status_code == 503
        assert full.json() == {"detail": WEBHOOK_UNAVAILABLE}

        release.set()
        deadline = time.monotonic() + 5
        while len(processed) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(processed) >= 2

        retried = post(client, issue_event(), delivery="retry-me")
        assert retried.status_code == 202
        assert retried.json() == {"status": "accepted"}
    finally:
        release.set()
        worker.close()


def test_request_path_does_no_embedding_or_network_work(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the webhook request path must not embed or open a socket")

    import api.embeddings

    monkeypatch.setattr(api.embeddings, "encode", explode)
    monkeypatch.setattr("socket.socket.connect", explode)

    worker = RecordingWorker()
    response = post(make_client(worker), issue_event())

    assert response.status_code == 202
    assert len(worker.jobs) == 1


# --- lifecycle --------------------------------------------------------------


def test_lifespan_starts_and_closes_the_worker():
    worker = RecordingWorker()
    app = create_app(
        settings=live_settings(),
        fixture_store=FixtureStore.default(),
        webhook_worker=worker,
    )

    with TestClient(app):
        pass

    assert worker.started == 1
    assert worker.closed == 1


def test_default_fixture_app_has_no_webhook_worker_or_connection(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("fixture-mode creation must not open a socket")

    monkeypatch.setattr("socket.socket.connect", explode)
    settings = Settings(tenants=("falkordb",), cors_origins=("http://localhost:3001",))

    app = create_app(settings=settings, fixture_store=FixtureStore.default())

    assert app.state.webhook_worker is None


# --- configuration parsing --------------------------------------------------


def test_settings_repr_excludes_the_webhook_secret():
    settings = live_settings()

    assert settings.webhook_secret == SECRET
    assert SECRET not in repr(settings)


def test_load_settings_reads_the_webhook_identity(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "from-env")
    monkeypatch.setenv("KEYSTONE_BOT_LOGIN", "keystone-bot")

    settings = load_settings()

    assert settings.webhook_secret == "from-env"
    assert settings.bot_login == "keystone-bot"
    assert "from-env" not in repr(settings)


def test_load_settings_treats_blank_webhook_config_as_none(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setenv("KEYSTONE_BOT_LOGIN", "   ")

    settings = load_settings()

    assert settings.webhook_secret is None
    assert settings.bot_login is None
