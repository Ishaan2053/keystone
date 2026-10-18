"""Replay parsing is monitored ingest, not trusted notification provenance."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from test_notifications import FakeStore as NotificationStore
from test_notifications import RecordingSender
from test_webhook_pipeline import (
    ISSUE_KEY,
    TENANT,
    FakeApply,
    FakeStore,
    make_job,
    make_triage,
)

from api.config import Settings
from api.notification_messages import STAGE_FAILURE_COPY
from api.notifications import SlackNotifications
from api.webhook_pipeline import WebhookPipeline
from api.webhook_store import parse_replay
from api.webhooks import build_job

ALLOWED = {"FalkorDB/FalkorDB"}
MALFORMED = "Replayed from https://[bad"


def pipeline_dependencies(monkeypatch, notifications, *, store=None):
    store = store if store is not None else FakeStore()
    triage_calls = []
    apply = FakeApply()
    vector = Mock(return_value=[0.0] * 384)
    monkeypatch.setattr("api.webhook_pipeline.incoming_issue_vector", vector)
    pipeline = WebhookPipeline(
        store=store,
        triage=make_triage(calls=triage_calls),
        apply_service=apply,
        tenant=TENANT,
        allowed_repos=ALLOWED,
        notifications=notifications,
    )
    return pipeline, store, triage_calls, apply, vector


@pytest.mark.parametrize("throws", [None, "begin", "failed"])
def test_parse_exception_is_reported_without_replacement(monkeypatch, throws):
    boom = ValueError("exact parser failure")
    attempt = object()
    events = []

    def begin(**kwargs):
        events.append("begin")
        if throws == "begin":
            raise RuntimeError("notification preparation failed")
        return attempt

    def failed(*args, **kwargs):
        events.append("failed")
        if throws == "failed":
            raise RuntimeError("notification delivery failed")

    notifier = Mock()
    notifier.begin_issue.side_effect = begin
    notifier.failed.side_effect = failed
    pipeline, store, triage_calls, apply, vector = pipeline_dependencies(monkeypatch, notifier)

    def broken_parser(body, allowed):
        events.append("parse")
        assert allowed == ALLOWED
        raise boom

    monkeypatch.setattr("api.webhook_pipeline.parse_replay", broken_parser)
    job = make_job(body=MALFORMED, tenant="untrusted")
    with pytest.raises(ValueError) as raised:
        pipeline(job)

    assert raised.value is boom
    assert events == (["begin", "parse"] if throws == "begin" else ["begin", "parse", "failed"])
    notifier.begin_issue.assert_called_once_with(
        tenant=TENANT,
        issue_key=ISSUE_KEY,
        delivery_id="d1",
        action="opened",
        title=job.title,
        replay_of=None,
    )
    if throws == "begin":
        notifier.failed.assert_not_called()
    else:
        notifier.failed.assert_called_once_with(attempt, stage="ingest", run_key=None)
    notifier.complete.assert_not_called()
    vector.assert_not_called()
    assert store.calls == [] and triage_calls == [] and apply.calls == []


@pytest.mark.parametrize("action", ["opened", "edited"])
def test_admitted_malformed_replay_sends_one_ingest_alert(monkeypatch, action):
    settings = Settings(
        tenants=(TENANT,),
        api_mode="live",
        sandbox_repo="acme/keystone-sandbox",
        bot_login="keystone-bot",
        webhook_secret="offline-secret",
    )
    payload = {
        "action": action,
        "repository": {"full_name": "acme/keystone-sandbox"},
        "sender": {"login": "octocat"},
        "issue": {
            "number": 42,
            "title": "Malformed replay",
            "body": MALFORMED,
            "state": "open",
            "created_at": "2026-10-16T00:00:00Z",
            "user": {"login": "octocat", "html_url": "https://github.com/octocat", "type": "User"},
        },
    }
    job = build_job(settings, payload, event="issues", delivery_id="malformed")
    assert job is not None and job.triage
    notification_store = NotificationStore(latest=None)
    sender = RecordingSender()
    notifier = SlackNotifications(
        notification_store, sender, public_base_url="https://keystone.example"
    )
    pipeline, store, triage_calls, apply, vector = pipeline_dependencies(monkeypatch, notifier)
    parser_errors = []

    def observed_parser(body, allowed):
        try:
            return parse_replay(body, allowed)
        except ValueError as exc:
            parser_errors.append(exc)
            raise

    monkeypatch.setattr("api.webhook_pipeline.parse_replay", observed_parser)
    with pytest.raises(ValueError, match="Invalid IPv6 URL") as raised:
        pipeline(job)

    assert parser_errors == [raised.value]
    assert parser_errors[0] is raised.value
    assert len(sender.payloads) == 1
    rendered = json.dumps(sender.payloads[0])
    assert STAGE_FAILURE_COPY["ingest"] in rendered
    assert "https://github.com/acme/keystone-sandbox/issues/42" in rendered
    assert "[bad" not in rendered
    assert not any(call[0] == "mark_run" for call in notification_store.calls)
    vector.assert_not_called()
    assert store.calls == [] and triage_calls == [] and apply.calls == []


@pytest.mark.parametrize("injected", [False, True])
def test_upsert_only_parse_failure_stays_silent(monkeypatch, injected):
    notifier = Mock()
    pipeline, store, triage_calls, apply, vector = pipeline_dependencies(monkeypatch, notifier)
    boom = ValueError("exact parser failure")
    if injected:
        monkeypatch.setattr("api.webhook_pipeline.parse_replay", Mock(side_effect=boom))

    with pytest.raises(ValueError) as raised:
        pipeline(make_job(action="edited", triage=False, body=MALFORMED))

    if injected:
        assert raised.value is boom
    assert notifier.mock_calls == []
    vector.assert_not_called()
    assert store.calls == [] and triage_calls == [] and apply.calls == []


def test_unpersisted_replay_candidate_is_not_notification_provenance(monkeypatch):
    boom = RuntimeError("upsert failed before persistence")
    notifier = Mock()
    pipeline, store, triage_calls, apply, _ = pipeline_dependencies(
        monkeypatch, notifier, store=FakeStore(error=boom)
    )

    with pytest.raises(RuntimeError) as raised:
        pipeline(make_job(body="Replayed from https://github.com/FalkorDB/FalkorDB/issues/7"))

    assert raised.value is boom
    assert store.calls[0]["replay"] == "issue:FalkorDB/FalkorDB#7"
    assert notifier.begin_issue.call_args.kwargs["issue_key"] == ISSUE_KEY
    assert notifier.begin_issue.call_args.kwargs["replay_of"] is None
    assert triage_calls == [] and apply.calls == []
