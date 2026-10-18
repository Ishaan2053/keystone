"""B12: webhook intake fixes (spec section 13).

1. ``ingest/replay.py`` opens sandbox issues with the bot's token, so the bot's
   own ``issues.opened`` carrying replay.py's marker line must be admitted;
   every other bot event, its comments included, stays ignored (no loops).
2. GitHub's form content type (``payload=<urlencoded JSON>``) is accepted once
   the raw-body signature passes; the signature is never computed over anything
   but the raw body.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import httpx
import test_replay as replay_tests
from test_webhooks import (
    BOT,
    RecordingWorker,
    comment_event,
    issue_event,
    make_client,
    post,
    sign,
)

from api.github import SandboxGitHub
from api.github_text import code_span
from api.webhooks import INVALID_PAYLOAD, INVALID_SIGNATURE
from ingest import replay

FORM = "application/x-www-form-urlencoded"


def post_form(client, body: bytes, *, signature: str | None = "auto", event: str = "issues"):
    headers = {
        "Content-Type": FORM,
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-form",
    }
    if signature == "auto":
        headers["X-Hub-Signature-256"] = sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/github", content=body, headers=headers)


def form_body(payload: dict) -> bytes:
    return urlencode({"payload": json.dumps(payload)}).encode("ascii")


# --- bug 1: the bot's replayed issue is triaged ---------------------------------


UPSTREAM = "https://github.com/FalkorDB/FalkorDB/issues/123"
# Exactly the first line ingest/replay.py writes (same code_span helper).
MARKED_BODY = f"Replayed from {code_span(UPSTREAM)}\n\nIt hangs on large graphs."


def bot_issue_event(body: str = MARKED_BODY, **overrides) -> dict:
    payload = issue_event(sender={"login": BOT}, **overrides)
    payload["issue"]["user"] = {
        "login": BOT,
        "html_url": f"https://github.com/{BOT}",
        "type": "User",
    }
    payload["issue"]["body"] = body
    return payload


def test_bot_opened_replay_is_accepted_and_enqueued():
    worker = RecordingWorker()

    response = post(make_client(worker), bot_issue_event())

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    (job,) = worker.jobs
    assert (job.event, job.action, job.issue_number) == ("issues", "opened", 42)
    assert job.sender_login == BOT
    assert job.body == MARKED_BODY
    # The bot's PAT is user-owned (type "User"); it must still get no Person.
    assert job.issue_author_is_bot is True


def test_the_body_ingest_replay_writes_passes_the_bot_gate(tmp_path, monkeypatch):
    # Lockstep with the real writer: replay one issue through ingest.replay with
    # mock transports, then deliver the exact body it posted as a bot-sent open.
    monkeypatch.setattr(replay, "CONFIG_ROOT", replay_tests.write_config(tmp_path))
    source = replay_tests.issue_payload(body="Seen by @someone in #12")
    read, _ = replay_tests.make_read_client(lambda r: httpx.Response(200, json=source))
    write, writes = replay_tests.make_write_client()
    replay.replay_issue(
        "falkordb",
        7,
        "FalkorDB/GraphRAG-SDK",
        read_client=read,
        writer=SandboxGitHub(replay_tests.SANDBOX, write),
        tenant_root=replay_tests.tenant_root(tmp_path),
    )
    replayed_body = replay_tests.posted_body(writes)["body"]
    worker = RecordingWorker()

    response = post(make_client(worker), bot_issue_event(replayed_body))

    assert response.json() == {"status": "accepted"}
    assert [job.body for job in worker.jobs] == [replayed_body]


def test_bot_opened_issue_without_the_replay_marker_is_ignored():
    worker = RecordingWorker()
    client = make_client(worker)
    bodies = [
        "It hangs on large graphs.",  # no marker
        "",  # empty
        f"Some text\nReplayed from {code_span(UPSTREAM)}",  # marker not on the first line
        f"Replayed from {UPSTREAM}",  # not replay.py's code span
        "Replayed from `https://example.com/FalkorDB/FalkorDB/issues/123`",  # not GitHub
        "Replayed from `https://github.com/FalkorDB/FalkorDB/pull/123`",  # not an issue
        f"Replayed from {code_span(UPSTREAM)} and more",  # trailing text
    ]

    for i, body in enumerate(bodies):
        response = post(client, bot_issue_event(body), delivery=f"d-{i}")
        assert response.status_code == 202, body
        assert response.json() == {"status": "ignored"}, body
    no_body = bot_issue_event()
    no_body["issue"]["body"] = None
    assert post(client, no_body, delivery="d-none").json() == {"status": "ignored"}
    assert worker.jobs == []


def test_bot_comment_is_still_ignored():
    payload = comment_event(sender={"login": BOT.upper()})
    worker = RecordingWorker()

    response = post(make_client(worker), payload, event="issue_comment")

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert worker.jobs == []


def test_bot_edit_and_other_bot_issue_actions_stay_ignored():
    worker = RecordingWorker()
    client = make_client(worker)

    for i, action in enumerate(("edited", "labeled", "assigned", "closed", "reopened")):
        # Even with the replay marker, only the bot's ``opened`` is admitted.
        response = post(client, bot_issue_event(action=action), delivery=f"d-{i}")
        assert response.json() == {"status": "ignored"}, action

    assert worker.jobs == []


def test_human_paths_are_unchanged():
    worker = RecordingWorker()
    client = make_client(worker)

    opened = post(client, issue_event(), delivery="d-open")
    comment = post(client, comment_event(), event="issue_comment", delivery="d-comment")

    assert opened.json() == {"status": "accepted"}
    assert comment.json() == {"status": "accepted"}
    assert [(j.event, j.sender_login, j.issue_author_is_bot) for j in worker.jobs] == [
        ("issues", "octocat", False),
        ("issue_comment", "octocat", False),
    ]


# --- bug 2: form-encoded deliveries -------------------------------------------


def test_signed_form_delivery_is_accepted_like_json():
    form_worker, json_worker = RecordingWorker(), RecordingWorker()

    form = post_form(make_client(form_worker), form_body(issue_event()))
    raw_json = post(make_client(json_worker), issue_event(), delivery="delivery-form")

    assert raw_json.json() == {"status": "accepted"}
    assert form.status_code == 202
    assert form.json() == {"status": "accepted"}
    assert form_worker.jobs == json_worker.jobs


def test_form_content_type_with_charset_is_accepted():
    worker = RecordingWorker()
    body = form_body(issue_event())
    response = make_client(worker).post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": f"{FORM}; charset=utf-8",
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d-1",
            "X-Hub-Signature-256": sign(body),
        },
    )

    assert response.json() == {"status": "accepted"}
    assert len(worker.jobs) == 1


def test_form_delivery_with_bad_signature_is_401():
    worker = RecordingWorker()
    body = form_body(issue_event())

    bad = post_form(make_client(worker), body, signature="sha256=deadbeef")
    # Signing the decoded JSON instead of the raw form body must also fail.
    wrong_bytes = post_form(
        make_client(worker), body, signature=sign(json.dumps(issue_event()).encode())
    )
    missing = post_form(make_client(worker), body, signature=None)

    for response in (bad, wrong_bytes, missing):
        assert response.status_code == 401
        assert response.json() == {"detail": INVALID_SIGNATURE}
    assert worker.jobs == []


def test_malformed_form_bodies_are_400():
    worker = RecordingWorker()
    client = make_client(worker)
    payload_json = json.dumps(issue_event())
    bodies = [
        urlencode({"other": payload_json}).encode(),  # no payload field
        b"",  # empty form
        urlencode([("payload", payload_json), ("payload", payload_json)]).encode(),  # two payloads
        urlencode({"payload": "{not json"}).encode(),  # payload is not JSON
        urlencode({"payload": "[1, 2]"}).encode(),  # payload is not an object
        b"payload=%FF%FE",  # payload is not UTF-8
    ]

    for body in bodies:
        response = post_form(client, body)
        assert response.status_code == 400, body
        assert response.json() == {"detail": INVALID_PAYLOAD}
    assert worker.jobs == []
