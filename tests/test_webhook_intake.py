"""B12: webhook intake fixes (spec section 13).

1. ``ingest/replay.py`` opens sandbox issues with the bot's token, so the bot's
   own ``issues.opened`` must be admitted; its comments stay ignored (no loops).
2. GitHub's form content type (``payload=<urlencoded JSON>``) is accepted once
   the raw-body signature passes; the signature is never computed over anything
   but the raw body.
"""
from __future__ import annotations

from test_webhooks import (
    BOT,
    RecordingWorker,
    comment_event,
    issue_event,
    make_client,
    post,
)


# --- bug 1: the bot's replayed issue is triaged ---------------------------------


def test_bot_opened_issue_is_accepted_and_enqueued():
    bot_user = {"login": BOT, "html_url": f"https://github.com/{BOT}", "type": "User"}
    payload = issue_event(sender={"login": BOT})
    payload["issue"]["user"] = bot_user
    worker = RecordingWorker()

    response = post(make_client(worker), payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    (job,) = worker.jobs
    assert (job.event, job.action, job.issue_number) == ("issues", "opened", 42)
    assert job.sender_login == BOT
    # The bot's PAT is user-owned (type "User"); it must still get no Person.
    assert job.issue_author_is_bot is True


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
        response = post(client, issue_event(action=action, sender={"login": BOT}), delivery=f"d-{i}")
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

