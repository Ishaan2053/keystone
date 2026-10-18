"""Task 3: the semantic fingerprint and the bounded, inert Slack renderer.

The fingerprint decides whether a follow-up is meaningful; it reads identity only
from recorded label/assign actions joined to the triage claim citation sets, so
run ids, timings, trace order, prose, list order, duplicates and login case never
change it. The renderer is pure: it reads only its arguments, performs no graph,
network or LLM work, and renders untrusted content as ``plain_text``.

The autouse no-network guard proves these tests never reach the network.
"""
from __future__ import annotations

import json
import re
import socket

import pytest

from api import schemas
from api.notification_messages import (
    FALLBACK_TEXT,
    MAX_URL_CHARS,
    NotificationAttempt,
    NotificationMessage,
    RunSnapshot,
    recommendation_fingerprint,
    render_message,
)

TENANT = "falkordb"
SANDBOX = "acme/keystone-sandbox"
ISSUE_KEY = f"issue:{SANDBOX}#42"
RUN_KEY = "run:00000000-0000-4000-8000-000000000001"
PARENT_KEY = "run:00000000-0000-4000-8000-000000000009"
BASE_URL = "https://keystone.example"
MODULE = "module:acme/keystone-sandbox/src/graph"

LABEL_CITATIONS = [MODULE, "issue:acme/upstream#7"]
REVIEWER_CITATIONS = ["person:alice", MODULE]
RISK_TEXT = "alice authored most of src/graph"
DECISION_TEXT = "Keep the graph read synchronous"

# Untrusted strings that must never become live Slack markup.
NEEDLES = (
    "<!channel>",
    "<@U123>",
    "<!subteam^S123>",
    "@here",
    "@channel",
    "<https://evil.example|click>",
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Rendering and fingerprinting must never open a connection."""

    def blocked(*args, **kwargs):
        raise AssertionError("notification rendering must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", blocked)


# --- builders ---------------------------------------------------------------


def claim(text, citations=()):
    return schemas.Claim(text=text, citations=list(citations))


def action(key, kind, value, module=None, status="proposed"):
    return schemas.RecordedAction(
        action_key=key, kind=kind, value=value, module=module, status=status, note=None, feedback=[]
    )


def tool(name, ms):
    return schemas.ToolResult(
        tool=name, data={}, cypher=[], params={}, evidence=[], ms=ms, empty=True
    )


def snapshot(
    *,
    run_key=RUN_KEY,
    title="Seed issue",
    replay_of=None,
    summary="A short summary.",
    fallback=False,
    labels=(),
    reviewers=(),
    risks=(),
    decisions=(),
    actions=(),
    evidence=(),
    trace=(),
    comment_markdown="composed comment",
):
    triage = schemas.TriageResult(
        run_key=run_key,
        labels=list(labels),
        reviewers=list(reviewers),
        risks=list(risks),
        decisions=list(decisions),
        comment_markdown=comment_markdown,
        trace=list(trace),
    )
    data = schemas.RecallRunData(
        run=schemas.RunSummary(
            run_key=run_key,
            at=1,
            issue_key=ISSUE_KEY,
            summary=summary,
            fallback=fallback,
            model="test-model",
            llm_calls=0,
            ms=1,
        ),
        evidence=list(evidence),
        actions=list(actions),
        triage=triage,
    )
    return RunSnapshot(data=data, title=title, replay_of=replay_of)


def build(
    *,
    label_claim="urgent",
    label_value="urgent",
    label_citations=None,
    reviewer_claim="alice",
    reviewer_value="alice",
    reviewer_citations=None,
    reviewer_module=MODULE,
    risk_text=RISK_TEXT,
    risk_citations=None,
    decision_text=DECISION_TEXT,
    decision_citations=None,
    run_key=RUN_KEY,
    title="Seed issue",
    summary="A short summary.",
):
    """One snapshot whose recommendation identity is fully parameterized."""
    label_citations = LABEL_CITATIONS if label_citations is None else label_citations
    reviewer_citations = REVIEWER_CITATIONS if reviewer_citations is None else reviewer_citations
    risk_citations = [MODULE] if risk_citations is None else risk_citations
    decision_citations = ["decision:d1"] if decision_citations is None else decision_citations
    return snapshot(
        run_key=run_key,
        title=title,
        summary=summary,
        labels=[claim(label_claim, label_citations)],
        reviewers=[claim(reviewer_claim, reviewer_citations)],
        risks=[claim(risk_text, risk_citations)],
        decisions=[claim(decision_text, decision_citations)],
        actions=[
            action("action:l1", "label", label_value),
            action("action:a1", "assign", reviewer_value, module=reviewer_module),
            action("action:c1", "comment", "a composed comment"),
        ],
    )


@pytest.fixture
def base():
    return build()


@pytest.fixture
def reordered():
    """The same recommendations with every execution detail changed."""
    return snapshot(
        run_key="run:00000000-0000-4000-8000-0000000000ff",
        title="A totally different title",
        replay_of="issue:acme/upstream#7",
        summary="Different prose entirely.",
        labels=[
            claim("urgent", [*reversed(LABEL_CITATIONS), LABEL_CITATIONS[0]]),
            claim("urgent", [LABEL_CITATIONS[0]]),
        ],
        reviewers=[claim("ALICE", [*reversed(REVIEWER_CITATIONS)])],
        risks=[claim("alice  authored most of src/graph", [MODULE, MODULE])],
        decisions=[claim("Keep the graph read synchronous", ["decision:d1", "decision:d1"])],
        actions=[
            action("action:a9", "assign", "@ALICE", module=MODULE, status="refused"),
            action("action:c9", "comment", "different comment", status="applied"),
            action("action:l9", "label", "urgent ", status="dry_run"),
            action("action:l9", "label", "urgent", status="applied"),
            action("action:a0", "assign", "ALICE", module=MODULE, status="applied"),
        ],
        trace=[tool("find_decisions", 99), tool("rank_reviewers", 3)],
        comment_markdown="@@@ a completely different composed comment " + "x" * 50,
    )


CHANGES = {
    "label_value": {"label_value": "blocker"},
    "label_citations": {"label_citations": ["module:other"]},
    "reviewer_login": {"reviewer_claim": "bob", "reviewer_value": "bob"},
    "reviewer_module": {"reviewer_module": "module:other"},
    "reviewer_citations": {"reviewer_citations": ["person:bob"]},
    "risk_text": {"risk_text": "bob authored most of src/graph"},
    "risk_citations": {"risk_citations": ["module:other"]},
    "decision_text": {"decision_text": "Change the graph read"},
    "decision_citations": {"decision_citations": ["decision:d2"]},
}


# --- fingerprint ------------------------------------------------------------


def test_fingerprint_ignores_execution_metadata(base, reordered):
    assert recommendation_fingerprint(base) == recommendation_fingerprint(reordered)


@pytest.mark.parametrize("overrides", CHANGES.values(), ids=list(CHANGES))
def test_changed_reviewer_or_citations_changes_fingerprint(base, overrides):
    assert recommendation_fingerprint(base) != recommendation_fingerprint(build(**overrides))


def test_fingerprint_never_reads_comment_markdown(base, reordered):
    """Prose-only differences (and the composed comment) do not move the hash."""
    assert recommendation_fingerprint(base) == recommendation_fingerprint(reordered)
    assert base.data.triage.comment_markdown != reordered.data.triage.comment_markdown


def test_fingerprint_is_a_stable_sha256(base):
    digest = recommendation_fingerprint(base)
    assert len(digest) == 64
    assert digest == recommendation_fingerprint(base)


# --- renderer ---------------------------------------------------------------


def message(
    *,
    cause="issue_opened",
    event_id="delivery:d1",
    title="Seed issue",
    replay_of=None,
    parent=None,
    current=None,
    applied=None,
    failure_stage=None,
    recovered=False,
):
    attempt = NotificationAttempt(
        tenant=TENANT,
        issue_key=ISSUE_KEY,
        event_id=event_id,
        cause=cause,
        title=title,
        replay_of=replay_of,
        parent=parent,
    )
    return NotificationMessage(
        attempt=attempt,
        current=current,
        applied=applied,
        failure_stage=failure_stage,
        recovered=recovered,
    )


def apply_result(*, dry_run=False, statuses=("applied",)):
    return schemas.ApplyResult(
        run_key=RUN_KEY,
        dry_run=dry_run,
        actions=[
            schemas.ActionResult(action_key=f"action:{i}", kind="label", value=f"v{i}", status=s)
            for i, s in enumerate(statuses)
        ],
    )


def blocks_by_text_type(payload, kind):
    return [
        block["text"]["text"]
        for block in payload["blocks"]
        if "text" in block and block["text"].get("type") == kind
    ]


def block_starting(payload, prefix):
    for text in blocks_by_text_type(payload, "plain_text") + blocks_by_text_type(payload, "mrkdwn"):
        if text.startswith(prefix):
            return text
    raise AssertionError(f"no block starts with {prefix!r}")


def evidence_entries(payload):
    text = block_starting(payload, "Evidence: ")
    body = text.removeprefix("Evidence: ")
    if body.startswith("none recorded"):
        return []
    return body.split(" · ")


def rendered_json(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def test_render_caps_and_inert_content():
    long_text = "標" * 300
    citations = ["cite-0", "cite-evil", *[f"cite-{j}" for j in range(1, 10)]]
    labels = [claim(f"{i:03d}-{long_text}", citations) for i in range(100)]
    reviewers = [claim(f"{i:03d}-user{i}", citations) for i in range(100)]
    risks = [claim(f"{i:03d}-risk {long_text}", citations) for i in range(100)]
    decisions = [claim(f"{i:03d}-decision {long_text}", citations) for i in range(100)]
    actions = [action(f"action:l{i}", "label", f"{i:03d}-{long_text}") for i in range(100)]
    snap = snapshot(
        title=" ".join(NEEDLES) + " " + long_text * 4,
        summary=" ".join(NEEDLES) + " " + long_text * 3,
        labels=labels,
        reviewers=reviewers,
        risks=risks,
        decisions=decisions,
        actions=actions,
    )
    evidence_links = {
        f"cite-{j}": f"https://github.com/{SANDBOX}/issues/{j + 1}" for j in range(10)
    }
    evidence_links["cite-evil"] = "https://evil.example/<@U123>"

    payload = render_message(
        message(current=snap, applied=apply_result(dry_run=True)),
        public_base_url=BASE_URL,
        evidence_links=evidence_links,
    )

    # Fixed, application-authored fallback text; fixed hardening keys.
    assert payload["text"] == FALLBACK_TEXT
    assert payload["parse"] == "none"
    assert payload["link_names"] is False
    assert payload["unfurl_links"] is False
    assert payload["unfurl_media"] is False
    for needle in NEEDLES:
        assert needle not in payload["text"]

    # Untrusted content never appears in application-built mrkdwn.
    mrkdwn = "\n".join(blocks_by_text_type(payload, "mrkdwn"))
    for needle in NEEDLES:
        assert needle not in mrkdwn
    assert "003-" not in mrkdwn

    # At most three claims per category and five evidence entries.
    assert "000-" in block_starting(payload, "Suggested labels: ")
    assert "002-" in block_starting(payload, "Suggested labels: ")
    assert "003-" not in block_starting(payload, "Suggested labels: ")
    assert "003-" not in block_starting(payload, "Suggested reviewers: ")
    assert "003-" not in block_starting(payload, "Risks: ")
    assert "003-" not in block_starting(payload, "Related decisions: ")
    entries = evidence_entries(payload)
    assert 0 < len(entries) <= 5
    assert "cite-4" not in " ".join(entries)

    # Bounded payload with the full-run link retained.
    assert len(rendered_json(payload).encode("utf-8")) <= 24_000
    assert f"{BASE_URL}/t/{TENANT}?run=00000000-0000-4000-8000-000000000001" in rendered_json(payload)


def test_render_truncates_to_the_payload_cap_without_losing_the_run_link():
    long_text = "標" * 400
    citations = [f"cite-{j}" for j in range(20)]
    claims = [claim(f"{i:03d}-{long_text}", citations) for i in range(100)]
    snap = snapshot(
        title=long_text * 2,
        summary=long_text * 3,
        labels=claims,
        reviewers=claims,
        risks=claims,
        decisions=claims,
        actions=[action(f"action:l{i}", "label", f"{i:03d}-{long_text}") for i in range(100)],
    )
    evidence_links = {
        f"cite-{j}": f"https://github.com/{SANDBOX}/issues/{j + 1}?x={'y' * 1900}" for j in range(20)
    }

    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links=evidence_links,
    )

    assert len(rendered_json(payload).encode("utf-8")) <= 24_000
    assert f"{BASE_URL}/t/{TENANT}?run=00000000-0000-4000-8000-000000000001" in rendered_json(payload)
    assert any("truncated" in text for text in blocks_by_text_type(payload, "mrkdwn"))


def test_fallback_run_is_labelled_degraded_not_successful():
    snap = snapshot(fallback=True, labels=[claim("urgent", [MODULE])])
    payload = render_message(
        message(current=snap, applied=apply_result(dry_run=True)),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    rendered = rendered_json(payload).lower()
    assert "fallback" in rendered
    assert "degraded" in rendered
    assert "success" not in rendered


def test_missing_citations_are_reported_not_invented():
    snap = snapshot(labels=[claim("urgent", [])], reviewers=[claim("alice", [])])
    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    assert evidence_entries(payload) == []
    assert "Evidence: none recorded for this run." in block_starting(payload, "Evidence: ")
    # The only GitHub link is the trusted issue URL, never an invented source.
    assert "\n".join(blocks_by_text_type(payload, "mrkdwn")).count("github.com") == 1


def test_partial_apply_refusal_is_not_reported_as_full_success():
    snap = snapshot(labels=[claim("urgent", [MODULE])])
    payload = render_message(
        message(current=snap, applied=apply_result(statuses=("applied", "refused"))),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    rendered = rendered_json(payload).lower()
    assert "1 applied" in rendered
    assert "1 refused" in rendered
    assert "success" not in rendered


def test_ui_follow_up_says_no_github_actions_were_applied():
    parent = snapshot(run_key=PARENT_KEY, labels=[claim("urgent", [MODULE])])
    child = snapshot(labels=[claim("blocker", [MODULE])])
    payload = render_message(
        message(cause="ui_feedback", event_id="ui:x:y", parent=parent, current=child, applied=None),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    rendered = rendered_json(payload).lower()
    assert "no github actions were applied" in rendered
    assert "follow-up to" in rendered


def test_apply_failure_with_available_triage_is_sanitized():
    snap = snapshot(labels=[claim("urgent", [MODULE])], summary="Persisted summary.")
    payload = render_message(
        message(current=snap, applied=None, failure_stage="apply", recovered=False),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    rendered = rendered_json(payload)
    assert "applying GitHub actions failed" in rendered
    assert "Persisted summary." in rendered
    assert "Traceback" not in rendered
    assert "Exception" not in rendered


def test_failure_only_message_has_fixed_stage_copy_and_no_run_link():
    payload = render_message(
        message(current=None, failure_stage="triage"),
        public_base_url=BASE_URL,
        evidence_links={"issue:x": "https://github.com/acme/keystone-sandbox/issues/1"},
    )
    rendered = rendered_json(payload)
    assert "Processing failed during triage" in rendered
    assert "keystone.example/t/" not in rendered
    assert "Traceback" not in rendered


def test_recovery_message_marks_the_recovery():
    snap = snapshot(labels=[claim("urgent", [MODULE])])
    payload = render_message(
        message(current=snap, applied=apply_result(), recovered=True),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    assert "Recovered" in rendered_json(payload)


def test_unsafe_evidence_urls_are_rendered_as_inert_plain_keys():
    snap = snapshot(labels=[claim("urgent", ["issue:acme/upstream#7"])])
    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links={
            "issue:acme/upstream#7": "http://github.com/acme/upstream/issues/7",
        },
    )
    mrkdwn = "\n".join(blocks_by_text_type(payload, "mrkdwn"))
    assert "issue:acme/upstream#7" in block_starting(payload, "Evidence: ")
    assert "http://github.com" not in mrkdwn


def test_render_without_a_usable_base_url_keeps_the_plain_run_key():
    snap = snapshot(labels=[claim("urgent", [MODULE])])

    for base in ("", "javascript:alert(1)", "https://user:pass@keystone.example"):
        payload = render_message(
            message(current=snap, applied=apply_result()),
            public_base_url=base,
            evidence_links={},
        )
        assert any(RUN_KEY in text for text in blocks_by_text_type(payload, "plain_text"))
        assert "javascript:" not in rendered_json(payload)
        # The issue link survives, but there is no Keystone run link to fall back to.
        assert "|Keystone run>" not in "\n".join(blocks_by_text_type(payload, "mrkdwn"))


def test_long_public_base_url_is_rejected_like_an_evidence_url():
    snap = snapshot(labels=[claim("urgent", [MODULE])])

    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url="https://keystone.example/" + "a" * MAX_URL_CHARS,
        evidence_links={},
    )

    assert any(RUN_KEY in text for text in blocks_by_text_type(payload, "plain_text"))
    assert "|Keystone run>" not in "\n".join(blocks_by_text_type(payload, "mrkdwn"))


def test_issue_url_is_rendered_for_an_issue_key_attempt():
    snap = snapshot(labels=[claim("urgent", [MODULE])])

    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links={},
    )

    mrkdwn = "\n".join(blocks_by_text_type(payload, "mrkdwn"))
    assert f"<https://github.com/{SANDBOX}/issues/42|{SANDBOX}#42>" in mrkdwn


def test_issue_url_is_rendered_for_a_failure_only_message():
    payload = render_message(
        message(current=None, failure_stage="ingest"),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    mrkdwn = "\n".join(blocks_by_text_type(payload, "mrkdwn"))
    assert f"<https://github.com/{SANDBOX}/issues/42|{SANDBOX}#42>" in mrkdwn


@pytest.mark.parametrize(
    "issue_key",
    [
        "issue:acme/<@U123>#1",
        "issue:acme/<!channel>#1",
        "issue:acme/keystone-sandbox#0",
        "issue:acme/keystone-sandbox#abc",
        "issue:acme/keystone-sandbox#42?x=1",
        "issue:evil.example/x#1|click",
        "issue:acme/keystone-sandbox",
        "issue:acme/keystone-sandbox/extra#1",
        "not-an-issue",
    ],
)
def test_a_hostile_issue_key_cannot_inject_markup_or_a_link(issue_key):
    snap = snapshot(labels=[claim("urgent", [MODULE])])
    payload = render_message(
        NotificationMessage(
            attempt=NotificationAttempt(
                tenant=TENANT,
                issue_key=issue_key,
                event_id="delivery:d1",
                cause="issue_opened",
                title="t",
                replay_of=None,
                parent=None,
            ),
            current=snap,
            applied=apply_result(),
            failure_stage=None,
        ),
        public_base_url="",  # isolate the issue link from the run link
        evidence_links={},
    )

    mrkdwn = "\n".join(blocks_by_text_type(payload, "mrkdwn"))
    for needle in NEEDLES:
        assert needle not in mrkdwn
    assert "<https://github.com" not in mrkdwn


def test_capped_claims_emit_a_truncation_notice():
    claims = [claim(f"claim-{i}", [MODULE]) for i in range(4)]
    snap = snapshot(labels=claims)

    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links={},
    )

    labels = block_starting(payload, "Suggested labels: ")
    assert "claim-0" in labels and "claim-2" in labels and "claim-3" not in labels
    assert any("truncated" in text for text in blocks_by_text_type(payload, "mrkdwn"))


def test_capped_evidence_keys_emit_a_truncation_notice():
    citations = [f"cite-{i}" for i in range(6)]
    snap = snapshot(labels=[claim("urgent", citations)])

    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links={},
    )

    assert len(evidence_entries(payload)) == 5
    assert any("truncated" in text for text in blocks_by_text_type(payload, "mrkdwn"))


def test_uncapped_content_emits_no_truncation_notice():
    snap = snapshot(labels=[claim("urgent", [MODULE])])

    payload = render_message(
        message(current=snap, applied=apply_result()),
        public_base_url=BASE_URL,
        evidence_links={},
    )

    assert not any("truncated" in text for text in blocks_by_text_type(payload, "mrkdwn"))


@pytest.mark.parametrize("paths", [("a" * 1950, "b" * 1950), ("標" * 1000,)])
def test_evidence_byte_budget_preserves_whole_links_and_plain_keys(paths):
    keys = [f"cite-{i}" for i in range(len(paths))]
    links = {key: f"https://github.com/acme/repo/{path}" for key, path in zip(keys, paths)}
    payload = render_message(
        message(current=snapshot(labels=[claim("urgent", keys)])),
        public_base_url=BASE_URL,
        evidence_links=links,
    )

    evidence = block_starting(payload, "Evidence: ")
    tokens = re.findall(r"<([^<>|]+)\|([^<>|]+)>", evidence)
    assert evidence.count("<") == evidence.count(">") == len(tokens)
    assert all(links[label] == url for url, label in tokens)
    entries = evidence_entries(payload)
    assert len(entries) == len(keys) <= 5
    for key, entry in zip(keys, entries):
        assert entry in (key, f"<{links[key]}|{key}>")
    assert all(len(block["text"]["text"].encode("utf-8")) <= 2500 for block in payload["blocks"])
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= 24_000
    assert any("truncated" in text for text in blocks_by_text_type(payload, "mrkdwn"))
    assert block_starting(payload, "Full run: ") == (
        f"Full run: <{BASE_URL}/t/{TENANT}?run={RUN_KEY[4:]}|Keystone run>"
    )


@pytest.mark.parametrize(
    "current_replay, candidate, failure_stage",
    [
        (None, "issue:unverified/candidate#1", None),
        ("issue:confirmed/source#7", "issue:unverified/candidate#1", None),
        ("issue:confirmed/source#7", None, None),
        (None, "issue:unverified/candidate#1", "triage"),
    ],
)
def test_replay_label_uses_only_current_persisted_provenance(current_replay, candidate, failure_stage):
    current = None if failure_stage else snapshot(replay_of=current_replay)
    payload = render_message(
        message(current=current, replay_of=candidate, failure_stage=failure_stage),
        public_base_url=BASE_URL,
        evidence_links={},
    )
    identity = blocks_by_text_type(payload, "plain_text")[0]
    if current_replay:
        assert f"Replay of: {current_replay}" in identity
    else:
        assert "Replay of:" not in identity
    assert "unverified/candidate" not in rendered_json(payload)
