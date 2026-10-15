"""B6 Task 2: guarded, recorded sandbox triage actions.

No real network: every GitHub client is an ``httpx.MockTransport``. The service
uses a fake :class:`ActionStore`; A's graph lookup and
``record_actions.cypher`` executor are recorded handoffs, not implemented here.
"""
from __future__ import annotations

import json

import httpx
import pytest

from api import schemas
from api.actions import (
    ActionContextNotFound,
    ActionService,
    ActionServiceUnavailable,
    ActionStoreError,
    ApplyContext,
    UnsafeCommentError,
    comment_marker,
    sanitize_comment,
)
from api.github import SandboxGitHub

SANDBOX = "acme/keystone-sandbox"
UPSTREAM = "FalkorDB/GraphRAG-SDK"
WRITE_TOKEN = "sandbox-write-token"
RUN = "run:11111111-1111-4111-8111-111111111111"
RUN_UUID = "11111111-1111-4111-8111-111111111111"
ISSUE = "issue:acme/keystone-sandbox#42"
NUMBER = 42


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def recorded(key, kind, value, status="proposed"):
    return schemas.RecordedAction(
        action_key=key,
        kind=kind,
        value=value,
        module=None,
        status=status,
        note=None,
        feedback=[],
    )


def make_context(
    *,
    run_key=RUN,
    issue_key=ISSUE,
    repo=SANDBOX,
    number=NUMBER,
    incoming=True,
    actions=(),
    comment_markdown="",
):
    return ApplyContext(
        run_key=run_key,
        issue_key=issue_key,
        repo=repo,
        issue_number=number,
        incoming=incoming,
        actions=list(actions),
        comment_markdown=comment_markdown,
    )


class FakeStore:
    """Adapter seam standing in for A's run-context lookup and record executor."""

    def __init__(self, contexts=None, *, fail_record=False):
        self.contexts = dict(contexts or {})
        self.fail_record = fail_record
        self.record_calls = []
        self.recorded = []

    def load_context(self, tenant, run_key):
        try:
            return self.contexts[(tenant, run_key)]
        except KeyError:
            raise ActionContextNotFound(f"no apply context for {tenant}/{run_key}") from None

    def record_results(self, tenant, run_key, results):
        self.record_calls.append((tenant, run_key, list(results)))
        if self.fail_record:
            raise RuntimeError("graph write failed: falkordb://admin:s3cret@db")
        self.recorded.extend(results)


class FakeGitHub:
    """Stateful mock GitHub: records requests and models sandbox responses."""

    def __init__(self, *, collaborators=(), labels=(), comments=None, fail=None):
        self.collaborators = set(collaborators)
        self.labels = set(labels)
        self.comments = list(comments or [])
        self.fail = dict(fail or {})
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        method = request.method
        path = request.url.path
        needle = f"{method} {path}"
        for fragment, status in self.fail.items():
            if fragment in needle:
                return httpx.Response(status, json={"message": "failure"})
        if method == "GET" and "/labels/" in path:
            name = path.rsplit("/labels/", 1)[1]
            if name in self.labels:
                return httpx.Response(200, json={"name": name})
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "POST" and "/issues/" in path and path.endswith("/labels"):
            return httpx.Response(200, json=[])
        if method == "POST" and path.endswith("/labels"):
            body = json.loads(request.content)
            if body.get("name") in self.labels:
                return httpx.Response(422, json={"message": "already_exists"})
            self.labels.add(body["name"])
            return httpx.Response(201, json={"name": body["name"]})
        if method == "GET" and "/collaborators/" in path:
            login = path.rsplit("/collaborators/", 1)[1]
            if login in self.collaborators:
                return httpx.Response(204)
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "POST" and path.endswith("/assignees"):
            return httpx.Response(201, json={})
        if method == "GET" and path.endswith("/comments"):
            return httpx.Response(200, json=[{"body": body} for body in self.comments])
        if method == "POST" and path.endswith("/comments"):
            self.comments.append(json.loads(request.content)["body"])
            return httpx.Response(201, json={"id": len(self.comments)})
        return httpx.Response(404, json={"message": "unexpected route"})

    def posts(self, suffix):
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith(suffix)]

    def methods_paths(self):
        return [(r.method, r.url.path) for r in self.requests]


def make_writer(fake):
    client = httpx.Client(
        transport=httpx.MockTransport(fake),
        base_url="https://api.github.com",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {WRITE_TOKEN}"},
    )
    return SandboxGitHub(SANDBOX, client)


def make_service(store, *, writer=None, sandbox_repo=SANDBOX, api_mode="live", public_base_url=None):
    return ActionService(
        store=store,
        writer=writer,
        sandbox_repo=sandbox_repo,
        api_mode=api_mode,
        public_base_url=public_base_url,
    )


# --------------------------------------------------------------------------
# Step 1: context guards
# --------------------------------------------------------------------------


def test_upstream_issue_refused_even_when_replay_target_is_sandbox():
    ctx = make_context(
        issue_key="issue:FalkorDB/GraphRAG-SDK#7",
        repo=UPSTREAM,
        number=7,
        comment_markdown=(
            "Replayed into https://github.com/acme/keystone-sandbox/issues/42\n\n"
            "REPLAY_OF: https://github.com/FalkorDB/GraphRAG-SDK/issues/7"
        ),
        actions=[
            recorded("action:l", "label", "graph"),
            recorded("action:c", "comment", "triage comment"),
        ],
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert result.actions and all(a.status == "refused" for a in result.actions)
    assert all(a.note for a in result.actions)
    assert fake.requests == []


def test_configured_sandbox_mismatch_is_refused():
    ctx = make_context(
        repo=SANDBOX, issue_key=ISSUE, actions=[recorded("action:l", "label", "graph")]
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake), sandbox_repo=UPSTREAM)

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.requests == []


def test_sandbox_requires_incoming_true():
    ctx = make_context(incoming=False, actions=[recorded("action:l", "label", "graph")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.requests == []


def test_issue_key_must_agree_with_context():
    ctx = make_context(
        issue_key="issue:acme/keystone-sandbox#99",
        number=NUMBER,
        actions=[recorded("action:l", "label", "graph")],
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.requests == []


def test_invalid_target_refused_even_in_dry_run():
    ctx = make_context(repo=UPSTREAM, issue_key="issue:FalkorDB/GraphRAG-SDK#7", number=7,
                       actions=[recorded("action:l", "label", "graph")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=True)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.requests == []


def test_dry_run_makes_zero_github_requests():
    ctx = make_context(
        actions=[
            recorded("action:l", "label", "graph"),
            recorded("action:a", "assign", "@falkordb-maintainer-a"),
            recorded("action:c", "comment", "triage comment"),
        ],
        comment_markdown="Suggested reviewer: @falkordb-maintainer-a",
    )
    store = FakeStore({("falkordb", RUN): ctx})

    def explode(request):
        raise AssertionError("dry run must not call GitHub")

    client = httpx.Client(
        transport=httpx.MockTransport(explode),
        base_url="https://api.github.com",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {WRITE_TOKEN}"},
    )
    service = make_service(store, writer=SandboxGitHub(SANDBOX, client))

    result = service.apply_triage("falkordb", RUN, dry_run=True)

    assert result.dry_run is True
    assert [a.status for a in result.actions] == ["dry_run", "dry_run", "dry_run"]
    notes = {a.kind: a.note.lower() for a in result.actions}
    assert "pending" in notes["assign"]

    # No write token is required to plan: a writer-less service still plans.
    planner = make_service(FakeStore({("falkordb", RUN): ctx}))
    assert [a.status for a in planner.apply_triage("falkordb", RUN, dry_run=True).actions] == [
        "dry_run",
        "dry_run",
        "dry_run",
    ]


def test_incoming_issue_uses_own_number():
    ctx = make_context(
        comment_markdown="Replayed from https://github.com/FalkorDB/GraphRAG-SDK/issues/7",
        actions=[recorded("action:c", "comment", "triage comment")],
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    comment_posts = fake.posts("/comments")
    assert len(comment_posts) == 1
    assert comment_posts[0].url.path == f"/repos/{SANDBOX}/issues/{NUMBER}/comments"


def test_cross_tenant_context_is_not_found():
    store = FakeStore({("falkordb", RUN): make_context()})
    service = make_service(store, writer=make_writer(FakeGitHub()))

    with pytest.raises(ActionContextNotFound):
        service.apply_triage("ghost", RUN, dry_run=True)
    with pytest.raises(ActionContextNotFound):
        service.apply_triage("falkordb", "run:22222222-2222-4222-8222-222222222222", dry_run=True)


def test_forged_store_context_run_key_is_rejected():
    store = FakeStore({("falkordb", RUN): make_context(run_key="run:99999999-9999-4999-8999-999999999999")})
    service = make_service(store, writer=make_writer(FakeGitHub()))

    with pytest.raises(ActionContextNotFound):
        service.apply_triage("falkordb", RUN, dry_run=True)


def test_fixture_mode_cannot_invoke_live_service():
    store = FakeStore({("falkordb", RUN): make_context()})
    service = make_service(store, api_mode="fixtures")

    with pytest.raises(ActionServiceUnavailable):
        service.apply_triage("falkordb", RUN, dry_run=True)


def test_missing_write_token_refuses_live_actions():
    ctx = make_context(actions=[recorded("action:l", "label", "graph")])
    store = FakeStore({("falkordb", RUN): ctx})
    service = make_service(store, writer=None)

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]


# --------------------------------------------------------------------------
# Step 5/6: fixed-target writer operations
# --------------------------------------------------------------------------


def test_missing_label_created_then_attached():
    ctx = make_context(actions=[recorded("action:l", "label", "graph")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert fake.methods_paths() == [
        ("GET", f"/repos/{SANDBOX}/labels/graph"),
        ("POST", f"/repos/{SANDBOX}/labels"),
        ("POST", f"/repos/{SANDBOX}/issues/{NUMBER}/labels"),
    ]
    create = fake.posts("/labels")[0]
    assert json.loads(create.content) == {"name": "graph", "color": "ededed"}


def test_existing_label_is_not_recreated():
    ctx = make_context(actions=[recorded("action:l", "label", "graph")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(labels={"graph"})
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert fake.methods_paths() == [
        ("GET", f"/repos/{SANDBOX}/labels/graph"),
        ("POST", f"/repos/{SANDBOX}/issues/{NUMBER}/labels"),
    ]


def test_only_collaborator_can_be_assigned():
    ctx = make_context(actions=[recorded("action:a", "assign", "@falkordb-maintainer-a")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(collaborators={"falkordb-maintainer-a"})
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert fake.methods_paths() == [
        ("GET", f"/repos/{SANDBOX}/collaborators/falkordb-maintainer-a"),
        ("POST", f"/repos/{SANDBOX}/issues/{NUMBER}/assignees"),
    ]
    assign = fake.posts("/assignees")[0]
    assert json.loads(assign.content) == {"assignees": ["falkordb-maintainer-a"]}


def test_noncollaborator_assign_is_dry_run_but_stays_in_comment():
    ctx = make_context(
        actions=[recorded("action:a", "assign", "@stranger")],
        comment_markdown="Suggested reviewer: @stranger",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(collaborators=set())
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["dry_run"]
    assert "stranger" in result.actions[0].note
    assert fake.posts("/assignees") == []
    # the suggestion is not dropped from the comment source
    assert "@stranger" in ctx.comment_markdown


def test_comment_has_no_mentions():
    ctx = make_context(
        actions=[
            recorded("action:a", "assign", "@falkordb-maintainer-a"),
            recorded("action:b", "assign", "@falkordb-maintainer-b"),
            recorded("action:c", "comment", "triage comment"),
        ],
        comment_markdown=(
            "Suggested reviewers: @falkordb-maintainer-a, @falkordb-maintainer-b.\n"
            "Sources: https://github.com/acme/keystone-sandbox/issues/42"
        ),
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(collaborators={"falkordb-maintainer-a", "falkordb-maintainer-b"})
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied", "applied", "applied"]
    body = fake.comments[0]
    assert "@" not in body
    assert "`falkordb-maintainer-a`" in body
    assert "`falkordb-maintainer-b`" in body
    assert "https://github.com/acme/keystone-sandbox/issues/42" in body
    assert comment_marker(RUN) in body


def test_comment_includes_configured_run_link():
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(
        store, writer=make_writer(fake), public_base_url="https://keystone.example/"
    )

    service.apply_triage("falkordb", RUN, dry_run=False)

    body = fake.comments[0]
    assert f"https://keystone.example/t/falkordb?run={RUN_UUID}" in body


def test_unsafe_comment_is_blocked_and_not_posted():
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Ping the team @acme/core and @here for visibility.",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.posts("/comments") == []


def test_write_token_never_used_for_upstream_read():
    ctx = make_context(
        actions=[recorded("action:l", "label", "graph"), recorded("action:a", "assign", "collaborator")],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(collaborators={"collaborator"})
    service = make_service(store, writer=make_writer(fake))

    service.apply_triage("falkordb", RUN, dry_run=False)

    assert fake.requests
    for request in fake.requests:
        assert request.url.path.startswith(f"/repos/{SANDBOX}/")
        assert request.headers["authorization"] == f"Bearer {WRITE_TOKEN}"
        assert UPSTREAM not in str(request.url)


@pytest.mark.parametrize(
    ("fail", "actions"),
    [
        ({f"POST /repos/{SANDBOX}/labels": 403}, [recorded("action:l", "label", "graph")]),
        (
            {f"POST /repos/{SANDBOX}/issues/{NUMBER}/comments": 404},
            [recorded("action:c", "comment", "triage comment")],
        ),
        (
            {f"POST /repos/{SANDBOX}/issues/{NUMBER}/assignees": 429},
            [recorded("action:a", "assign", "collaborator")],
        ),
    ],
)
def test_github_failure_never_marked_applied(fail, actions):
    ctx = make_context(actions=actions, comment_markdown="Summary")
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(collaborators={"collaborator"}, fail=fail)
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert "s3cret" not in (result.actions[0].note or "")


def test_collaborator_lookup_failure_is_refused_not_nonmember():
    ctx = make_context(actions=[recorded("action:a", "assign", "collaborator")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(
        collaborators={"collaborator"}, fail={f"GET /repos/{SANDBOX}/collaborators": 429}
    )
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.posts("/assignees") == []


# --------------------------------------------------------------------------
# Step 7: comment sanitisation
# --------------------------------------------------------------------------


def test_sanitize_known_login_becomes_bare_code_span():
    assert sanitize_comment("Thanks @alice", ["alice"]) == "Thanks `alice`"


def test_sanitize_existing_code_spans_are_normalised():
    assert sanitize_comment("Thanks `@alice` and `bob`", ["alice"]) == "Thanks `alice` and `bob`"


def test_sanitize_preserves_newlines_and_emails():
    text = "line one\nuser@example.com\n@alice"
    assert sanitize_comment(text, ["alice"]) == "line one\nuser@example.com\n`alice`"


@pytest.mark.parametrize(
    "markdown",
    [
        "team @acme/core",
        "broadcast @here",
        "unknown @stranger",
        'html <a href="https://github.com/evil">@evil</a>',
    ],
)
def test_sanitize_rejects_residual_mentions(markdown):
    with pytest.raises(UnsafeCommentError):
        sanitize_comment(markdown, ["alice"])


# --------------------------------------------------------------------------
# Step 8: reapply, retry and recording
# --------------------------------------------------------------------------


def test_reapply_skips_applied_and_rejected_actions():
    ctx = make_context(
        actions=[
            recorded("action:a", "label", "applied-label", status="applied"),
            recorded("action:r", "label", "rejected-label", status="rejected"),
        ]
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    statuses = {a.action_key: a.status for a in result.actions}
    assert statuses["action:a"] == "applied"
    assert statuses["action:r"] == "refused"
    assert fake.requests == []
    # neither skip is persisted, so a stored rejected state is never overwritten
    assert store.record_calls == []


def test_prior_dry_run_can_later_apply():
    ctx = make_context(actions=[recorded("action:l", "label", "graph", status="dry_run")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert fake.posts(f"/issues/{NUMBER}/labels")


def test_graph_recording_failure_is_503_and_does_not_report_success():
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx}, fail_record=True)
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    with pytest.raises(ActionStoreError) as excinfo:
        service.apply_triage("falkordb", RUN, dry_run=False)

    assert "s3cret" not in str(excinfo.value)
    assert len(fake.posts("/comments")) == 1


def test_retry_after_record_failure_does_not_repost_comment():
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    fake = FakeGitHub()

    first = make_service(FakeStore({("falkordb", RUN): ctx}, fail_record=True), writer=make_writer(fake))
    with pytest.raises(ActionStoreError):
        first.apply_triage("falkordb", RUN, dry_run=False)
    assert len(fake.posts("/comments")) == 1

    second = make_service(FakeStore({("falkordb", RUN): ctx}), writer=make_writer(fake))
    result = second.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert "already posted" in result.actions[0].note.lower()
    assert len(fake.posts("/comments")) == 1


def test_records_results_for_planned_actions():
    ctx = make_context(actions=[recorded("action:l", "label", "graph")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    service.apply_triage("falkordb", RUN, dry_run=False)

    assert len(store.record_calls) == 1
    tenant, run_key, results = store.record_calls[0]
    assert (tenant, run_key) == ("falkordb", RUN)
    assert [r.action_key for r in results] == ["action:l"]
    assert [r.status for r in results] == ["applied"]


# --------------------------------------------------------------------------
# Step 4: settings the service needs (dry-run and sandbox config)
# --------------------------------------------------------------------------


def test_load_settings_reads_apply_configuration(monkeypatch):
    from api.config import load_settings

    monkeypatch.setenv("API_MODE", "live")
    monkeypatch.setenv("SANDBOX_REPO", SANDBOX)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://keystone.example/")

    settings = load_settings()

    assert settings.sandbox_repo == SANDBOX
    assert settings.dry_run is False
    assert settings.public_base_url == "https://keystone.example"


def test_load_settings_defaults_to_dry_run(monkeypatch):
    from api.config import load_settings

    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("SANDBOX_REPO", raising=False)
    monkeypatch.setenv("API_MODE", "fixtures")

    settings = load_settings()

    assert settings.dry_run is True
    assert settings.sandbox_repo is None


@pytest.mark.parametrize("value", ["maybe", "2"])
def test_load_settings_rejects_non_boolean_dry_run(monkeypatch, value):
    from api.config import load_settings

    monkeypatch.setenv("DRY_RUN", value)
    with pytest.raises(ValueError):
        load_settings()


def test_load_settings_rejects_bad_sandbox_repo(monkeypatch):
    from api.config import load_settings

    monkeypatch.setenv("SANDBOX_REPO", "https://github.com/acme/keystone-sandbox")
    with pytest.raises(ValueError):
        load_settings()

