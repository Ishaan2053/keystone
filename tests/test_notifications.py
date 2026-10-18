"""Task 3: the best-effort Slack notification coordinator.

The coordinator decides whether an event is notifiable, reserves one immutable
parent per trusted event, deduplicates delivery attempts in the same process and
sanitizes every notification-only failure. These tests use a recording store and
a recording sender: no graph, no LLM, no tool and no network.

Deduplication is best effort and same-process only: the bounded FIFO caches may
evict an event, after which a repeated event can send again.
"""
from __future__ import annotations

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from api import schemas
from api.notification_messages import STAGE_FAILURE_COPY, RECOVERY_NOTE, RunSnapshot
from api.notifications import SlackNotifications

TENANT = "falkordb"
SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
PARENT_KEY = "run:00000000-0000-4000-8000-000000000009"
CHILD_KEY = "run:00000000-0000-4000-8000-000000000002"
BASE_URL = "https://keystone.example"
MODULE = "module:acme/keystone-sandbox/src/graph"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("the notification coordinator must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", blocked)


# --- doubles ----------------------------------------------------------------


class FakeStore:
    """A recording stand-in for ``NotificationStore`` with injectable failures."""

    def __init__(self, *, latest=None, parent=None, runs=None, links=None, mark=True):
        self.latest = latest
        self.parent = parent
        self.runs = dict(runs or {})
        self.links = dict(links or {})
        self.mark_result = mark
        self.errors: dict[str, Exception] = {}
        self.calls: list[tuple] = []

    def _fail(self, name):
        error = self.errors.get(name)
        if error is not None:
            raise error

    def latest_eligible(self, tenant, issue_key):
        self.calls.append(("latest_eligible", tenant, issue_key))
        self._fail("latest")
        return self.latest

    def eligible_parent(self, tenant, issue_key, parent_run_key, *, ui_feedback_key=None):
        self.calls.append(("eligible_parent", tenant, issue_key, parent_run_key, ui_feedback_key))
        self._fail("parent")
        return self.parent

    def load_run(self, tenant, run_key):
        self.calls.append(("load_run", tenant, run_key))
        self._fail("load")
        return self.runs.get(run_key)

    def mark_run(self, tenant, run_key, *, issue_key, cause, event_id, parent_run_key):
        self.calls.append(("mark_run", tenant, run_key, issue_key, cause, event_id, parent_run_key))
        self._fail("mark")
        return self.mark_result

    def node_links(self, tenant, keys):
        self.calls.append(("node_links", tenant, tuple(keys)))
        self._fail("links")
        return dict(self.links)

    def kinds(self):
        return [call[0] for call in self.calls]


class RecordingSender:
    """Records every attempted payload, even when it raises (best-effort sender)."""

    def __init__(self, result=True, error=None):
        self.payloads: list[dict] = []
        self.result = result
        self.error = error

    def send(self, payload):
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


def run_snapshot(*, run_key, label="urgent", summary="A short summary.", parent=None):
    return RunSnapshot(
        data=schemas.RecallRunData(
            run=schemas.RunSummary(
                run_key=run_key,
                at=1,
                issue_key=ISSUE_KEY,
                summary=summary,
                fallback=False,
                model="test-model",
                llm_calls=0,
                ms=1,
            ),
            evidence=[],
            actions=[
                schemas.RecordedAction(
                    action_key=f"action:{label}",
                    kind="label",
                    value=label,
                    module=None,
                    status="proposed",
                    note=None,
                    feedback=[],
                )
            ],
            triage=schemas.TriageResult(
                run_key=run_key,
                labels=[schemas.Claim(text=label, citations=[MODULE])],
                reviewers=[],
                risks=[],
                decisions=[],
                comment_markdown="composed comment",
                trace=[],
            ),
        ),
        title="Seed issue",
        replay_of=None,
    )


def apply_result(*, dry_run=False, statuses=("applied",)):
    return schemas.ApplyResult(
        run_key=CHILD_KEY,
        dry_run=dry_run,
        actions=[
            schemas.ActionResult(action_key=f"action:{i}", kind="label", value=f"v{i}", status=s)
            for i, s in enumerate(statuses)
        ],
    )


def coordinator(store, sender):
    return SlackNotifications(store, sender, public_base_url=BASE_URL)


def opened(notifier, delivery_id="d1", **extra):
    return notifier.begin_issue(
        tenant=TENANT,
        issue_key=ISSUE_KEY,
        delivery_id=delivery_id,
        action="opened",
        title="Seed issue",
        replay_of=None,
        **extra,
    )


def blob(payload):
    return json.dumps(payload, ensure_ascii=False)


# --- begin_issue ------------------------------------------------------------


def test_begin_issue_reserves_the_latest_eligible_parent():
    store = FakeStore(latest=run_snapshot(run_key=PARENT_KEY))
    notifier = coordinator(store, RecordingSender())

    attempt = opened(notifier, "d1")

    assert attempt.event_id == "delivery:d1"
    assert attempt.cause == "issue_opened"
    assert attempt.tenant == TENANT
    assert attempt.issue_key == ISSUE_KEY
    assert attempt.parent.data.run.run_key == PARENT_KEY
    assert ("latest_eligible", TENANT, ISSUE_KEY) in store.calls


def test_begin_issue_edited_uses_the_edit_cause():
    notifier = coordinator(FakeStore(), RecordingSender())
    attempt = notifier.begin_issue(
        tenant=TENANT,
        issue_key=ISSUE_KEY,
        delivery_id="d7",
        action="edited",
        title="Seed issue",
        replay_of=PARENT_KEY,
    )
    assert attempt.event_id == "delivery:d7"
    assert attempt.cause == "issue_edited"
    assert attempt.replay_of == PARENT_KEY


def test_begin_issue_without_an_eligible_parent_is_initial():
    notifier = coordinator(FakeStore(latest=None), RecordingSender())
    assert opened(notifier).parent is None


def test_begin_issue_rejects_unknown_action_and_blank_identity():
    store = FakeStore()
    notifier = coordinator(store, RecordingSender())
    assert (
        notifier.begin_issue(
            tenant=TENANT, issue_key=ISSUE_KEY, delivery_id="d1", action="reopened",
            title="t", replay_of=None,
        )
        is None
    )
    assert notifier.begin_issue(
        tenant=TENANT, issue_key=ISSUE_KEY, delivery_id="", action="opened", title="t", replay_of=None
    ) is None
    assert store.calls == []


def test_parent_is_reserved_before_concurrent_completion():
    store = FakeStore(latest=run_snapshot(run_key=PARENT_KEY))
    notifier = coordinator(store, RecordingSender())

    first = opened(notifier, "d1")
    # A competing UI re-triage completes a new eligible run for the same issue.
    store.latest = run_snapshot(run_key=CHILD_KEY, label="blocker")
    second = opened(notifier, "d1")

    assert first.parent == second.parent
    assert second.parent.data.run.run_key == PARENT_KEY
    assert store.kinds().count("latest_eligible") == 1


# --- feedback beginnings ----------------------------------------------------


def test_begin_github_feedback_validates_the_exact_parent():
    store = FakeStore(parent=run_snapshot(run_key=PARENT_KEY))
    notifier = coordinator(store, RecordingSender())

    attempt = notifier.begin_github_feedback(
        tenant=TENANT, issue_key=ISSUE_KEY, parent_run_key=PARENT_KEY, comment_id=7
    )

    assert attempt.event_id == "comment:7"
    assert attempt.cause == "github_feedback"
    assert attempt.parent.data.run.run_key == PARENT_KEY
    assert ("eligible_parent", TENANT, ISSUE_KEY, PARENT_KEY, None) in store.calls


def test_begin_github_feedback_without_a_proven_parent_is_not_eligible():
    store = FakeStore(parent=None)
    notifier = coordinator(store, RecordingSender())
    assert (
        notifier.begin_github_feedback(
            tenant=TENANT, issue_key=ISSUE_KEY, parent_run_key=PARENT_KEY, comment_id=7
        )
        is None
    )


def test_begin_ui_feedback_requires_the_exact_persisted_feedback_key():
    store = FakeStore(parent=run_snapshot(run_key=PARENT_KEY))
    notifier = coordinator(store, RecordingSender())

    attempt = notifier.begin_ui_feedback(
        tenant=TENANT, issue_key=ISSUE_KEY, parent_run_key=PARENT_KEY, feedback_key="feedback:f1"
    )

    assert attempt.event_id == f"ui:{PARENT_KEY}:feedback:f1"
    assert attempt.cause == "ui_feedback"
    assert ("eligible_parent", TENANT, ISSUE_KEY, PARENT_KEY, "feedback:f1") in store.calls


def test_begin_ui_feedback_rejects_a_blank_feedback_key():
    store = FakeStore(parent=run_snapshot(run_key=PARENT_KEY))
    notifier = coordinator(store, RecordingSender())
    assert (
        notifier.begin_ui_feedback(
            tenant=TENANT, issue_key=ISSUE_KEY, parent_run_key=PARENT_KEY, feedback_key="  "
        )
        is None
    )
    assert store.calls == []


# --- complete ---------------------------------------------------------------


def test_complete_marks_lineage_before_looking_up_evidence_and_sending():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    kinds = store.kinds()
    assert "mark_run" in kinds
    assert kinds.index("mark_run") < kinds.index("node_links")
    assert len(sender.payloads) == 1
    mark = next(call for call in store.calls if call[0] == "mark_run")
    assert mark[1:] == (TENANT, CHILD_KEY, ISSUE_KEY, "issue_opened", "delivery:d1", None)


def test_complete_records_lineage_even_when_slack_is_disabled():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    notifier = SlackNotifications(store, None, public_base_url=BASE_URL)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert store.kinds().count("mark_run") == 1


def test_complete_suppresses_when_lineage_cannot_be_recorded():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)}, mark=False)
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert sender.payloads == []


def test_complete_suppresses_when_the_run_was_not_persisted():
    store = FakeStore(runs={})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert sender.payloads == []


def test_unchanged_follow_up_is_silent_but_still_marks_lineage():
    store = FakeStore(
        latest=run_snapshot(run_key=PARENT_KEY),
        runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)},
    )
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = notifier.begin_issue(
        tenant=TENANT, issue_key=ISSUE_KEY, delivery_id="d2", action="edited", title="t", replay_of=None
    )
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert sender.payloads == []
    assert store.kinds().count("mark_run") == 1


def test_changed_follow_up_notifies_with_the_parent_and_change_entries():
    store = FakeStore(
        latest=run_snapshot(run_key=PARENT_KEY),
        runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY, label="blocker")},
    )
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = notifier.begin_issue(
        tenant=TENANT, issue_key=ISSUE_KEY, delivery_id="d3", action="edited", title="t", replay_of=None
    )
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert len(sender.payloads) == 1
    rendered = blob(sender.payloads[0])
    assert "Changed since the parent run" in rendered
    assert PARENT_KEY in rendered


# --- failures and recovery --------------------------------------------------


def test_processing_failure_then_recovery_permits_one_failure_and_one_recovery():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.failed(attempt, stage="triage")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert len(sender.payloads) == 2
    assert STAGE_FAILURE_COPY["triage"] in blob(sender.payloads[0])
    assert RECOVERY_NOTE in blob(sender.payloads[1])


def test_unchanged_follow_up_after_a_core_failure_still_recovers():
    store = FakeStore(
        latest=run_snapshot(run_key=PARENT_KEY),
        runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)},
    )
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = notifier.begin_issue(
        tenant=TENANT, issue_key=ISSUE_KEY, delivery_id="d4", action="edited", title="t", replay_of=None
    )
    notifier.failed(attempt, stage="apply")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert len(sender.payloads) == 2
    assert RECOVERY_NOTE in blob(sender.payloads[1])


def test_repeated_processing_failure_is_deduplicated():
    store = FakeStore()
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.failed(attempt, stage="ingest")
    notifier.failed(attempt, stage="ingest")

    assert len(sender.payloads) == 1


def test_apply_failure_uses_the_available_triage_and_only_the_graph_read():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    reservation_calls = store.kinds()
    notifier.failed(attempt, stage="apply", run_key=CHILD_KEY)

    assert len(sender.payloads) == 1
    rendered = blob(sender.payloads[0])
    assert STAGE_FAILURE_COPY["apply"] in rendered
    assert "A short summary." in rendered
    # The failure path itself only reads the persisted run and its evidence URLs.
    assert store.kinds()[len(reservation_calls):] == ["load_run", "node_links"]


def test_failure_without_a_persisted_run_reads_no_graph():
    store = FakeStore()
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    reservation_calls = store.kinds()
    notifier.failed(attempt, stage="ingest")

    assert len(sender.payloads) == 1
    assert store.kinds()[len(reservation_calls):] == []
    assert "mark_run" not in store.kinds()


def test_failure_records_no_lineage_even_with_a_run_key():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.failed(attempt, stage="apply", run_key=CHILD_KEY)

    assert "mark_run" not in store.kinds()


# --- deduplication and reservations ----------------------------------------


def test_same_process_attempt_is_atomic():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        attempt = opened(notifier, "d1")
        notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures:
            future.result()

    assert len(sender.payloads) == 1


def test_failed_send_is_not_retried():
    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender(error=RuntimeError("boom"))
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())

    assert len(sender.payloads) == 1


def test_second_run_of_a_completed_event_still_gets_lineage_but_no_message():
    other_key = "run:00000000-0000-4000-8000-000000000003"
    store = FakeStore(
        runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY), other_key: run_snapshot(run_key=other_key)}
    )
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    # A repeated event (e.g. a double-clicked UI Re-triage) persisted a second run.
    notifier.complete(attempt, run_key=other_key, applied=apply_result())

    marked = [call[2] for call in store.calls if call[0] == "mark_run"]
    assert marked == [CHILD_KEY, other_key]
    assert len(sender.payloads) == 1


def test_failed_processing_keeps_its_reservation_and_completion_releases_it():
    store = FakeStore(latest=None, runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.failed(attempt, stage="triage")
    # A retry of the failed event keeps the reserved parent: no second lookup.
    assert opened(notifier, "d1").parent == attempt.parent
    assert store.kinds().count("latest_eligible") == 1

    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    # A successfully completed event released its reservation, so it is looked up again.
    assert opened(notifier, "d1") is not None
    assert store.kinds().count("latest_eligible") == 2


def test_none_attempts_are_noops():
    store = FakeStore()
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    notifier.complete(None, run_key=CHILD_KEY, applied=None)
    notifier.failed(None, stage="triage")

    assert store.calls == []
    assert sender.payloads == []


def test_bounded_caches_evict_fifo_and_may_resend():
    """limit=2: reservations and attempted outcomes each hold at most two entries.

    Eviction is FIFO, so the oldest event is forgotten and a repeated event can
    send again: same-process deduplication is explicitly best effort.
    """
    store = FakeStore(latest=None, runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = SlackNotifications(store, sender, public_base_url=BASE_URL, limit=2)

    attempts = [opened(notifier, f"d{i}") for i in range(3)]
    assert store.kinds().count("latest_eligible") == 3

    # d0's reservation was evicted by d2, so it is looked up again.
    opened(notifier, "d0")
    assert store.kinds().count("latest_eligible") == 4

    for attempt in attempts:
        notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    assert len(sender.payloads) == 3

    # d0's outcome was evicted by d2 as well, so this repeated completion resends.
    notifier.complete(attempts[0], run_key=CHILD_KEY, applied=apply_result())
    assert len(sender.payloads) == 4


# --- fail-closed boundaries -------------------------------------------------


def test_no_llm_or_tool_entry_point_is_invoked_during_a_full_cycle(monkeypatch):
    import api.agent
    import api.llm

    invoked: list[str] = []
    monkeypatch.setattr(api.llm, "generate_json", lambda *args, **kwargs: invoked.append("llm"))
    monkeypatch.setattr(api.agent, "run_triage", lambda *args, **kwargs: invoked.append("triage"))

    store = FakeStore(runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)})
    sender = RecordingSender()
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    notifier.failed(attempt, stage="triage")

    assert invoked == []
    assert len(sender.payloads) == 2


def _boom(*args, **kwargs):
    raise RuntimeError("boom")


@pytest.mark.parametrize("broken", ["latest", "parent", "mark", "load", "links", "render", "send"])
def test_notification_dependencies_cannot_break_core(broken, monkeypatch):
    store = FakeStore(
        latest=run_snapshot(run_key=PARENT_KEY),
        parent=run_snapshot(run_key=PARENT_KEY),
        runs={CHILD_KEY: run_snapshot(run_key=CHILD_KEY)},
    )
    sender = RecordingSender()
    if broken == "render":
        monkeypatch.setattr("api.notifications.render_message", _boom)
    elif broken == "send":
        sender = RecordingSender(error=RuntimeError("boom"))
    else:
        store.errors[broken] = RuntimeError("boom")
    notifier = coordinator(store, sender)

    attempt = opened(notifier, "d1")
    if attempt is not None:
        notifier.complete(attempt, run_key=CHILD_KEY, applied=apply_result())
    feedback = notifier.begin_github_feedback(
        tenant=TENANT, issue_key=ISSUE_KEY, parent_run_key=PARENT_KEY, comment_id=1
    )
    if feedback is not None:
        notifier.failed(feedback, stage="apply", run_key=CHILD_KEY)

    if broken == "latest":
        assert attempt is None
    if broken == "parent":
        assert feedback is None
    if broken == "links":
        # An evidence-lookup failure still delivers its payload without links.
        assert sender.payloads
