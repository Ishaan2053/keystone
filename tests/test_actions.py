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
    ActionError,
    ActionService,
    ActionServiceUnavailable,
    ActionStoreError,
    ApplyContext,
    UnsafeCommentError,
    comment_marker,
    sanitize_comment,
)
from api.github import COMMENTS_PER_PAGE, MAX_COMMENT_PAGES, GitHubWriteError, SandboxGitHub

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
    """Stateful mock GitHub: records requests and models sandbox responses.

    ``fail`` maps a ``"<METHOD> <path>"`` fragment to either an HTTP status code
    (returned as JSON) or an exception instance (raised), so tests can exercise
    transport-level failures as well as error statuses. ``non_json`` lists
    fragments that return ``200`` with an unreadable body.
    """

    def __init__(self, *, collaborators=(), labels=(), comments=None, fail=None, non_json=(),
                 assignment_response=None):
        self.collaborators = set(collaborators)
        self.labels = set(labels)
        self.comments = list(comments or [])
        self.fail = dict(fail or {})
        self.non_json = set(non_json)
        self.assignment_response = assignment_response
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        method = request.method
        path = request.url.path
        needle = f"{method} {path}"
        for fragment, failure in self.fail.items():
            if fragment in needle:
                if isinstance(failure, Exception):
                    raise failure
                return httpx.Response(failure, json={"message": "failure"})
        if needle in self.non_json:
            return httpx.Response(200, content=b"not json at all")
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
            if self.assignment_response is not None:
                return self.assignment_response
            logins = json.loads(request.content)["assignees"]
            return httpx.Response(201, json={
                "number": NUMBER,
                "assignees": [{"login": login} for login in logins],
            })
        if method == "GET" and path.endswith("/comments"):
            per_page = int(request.url.params["per_page"])
            start = (int(request.url.params["page"]) - 1) * per_page
            return httpx.Response(200, json=[
                {"body": body} for body in self.comments[start:start + per_page]
            ])
        if method == "POST" and path.endswith("/comments"):
            self.comments.append(json.loads(request.content)["body"])
            return httpx.Response(201, json={"id": len(self.comments)})
        return httpx.Response(404, json={"message": "unexpected route"})

    def posts(self, suffix):
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith(suffix)]

    def methods_paths(self):
        return [(r.method, r.url.path) for r in self.requests]


def make_writer(fake, repo=SANDBOX):
    client = httpx.Client(
        transport=httpx.MockTransport(fake),
        base_url="https://api.github.com",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {WRITE_TOKEN}"},
    )
    return SandboxGitHub(repo, client)


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
    # The writer agrees with the configured sandbox (UPSTREAM); the *context*
    # names a different repo, so validation must refuse before any write.
    service = make_service(store, writer=make_writer(fake, repo=UPSTREAM), sandbox_repo=UPSTREAM)

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


def test_writer_repo_must_match_configured_sandbox():
    """A writer bound to a different repo than the configured sandbox fails closed."""
    store = FakeStore(
        {("falkordb", RUN): make_context(actions=[recorded("action:l", "label", "graph")])}
    )
    fake = FakeGitHub()

    with pytest.raises(ActionError):
        make_service(store, writer=make_writer(fake), sandbox_repo=UPSTREAM)

    assert fake.requests == []


def test_writer_repo_match_is_case_insensitive():
    store = FakeStore(
        {("falkordb", RUN): make_context(actions=[recorded("action:l", "label", "graph")])}
    )
    fake = FakeGitHub()
    service = make_service(
        store, writer=make_writer(fake, repo="Acme/Keystone-Sandbox"), sandbox_repo=SANDBOX
    )

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]


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


UNCONFIRMED_ASSIGNMENT_BODIES = [
    pytest.param(b'{"assignees": []}', id="empty"),
    pytest.param(b'{}', id="missing"),
    pytest.param(b'{"assignees": null}', id="null-list"),
    pytest.param(b'{"assignees": {"login": "collaborator"}}', id="not-list"),
    pytest.param(b'{"assignees": [{"login": "other"}]}', id="different-login"),
    pytest.param(b'{"assignees": [null, "collaborator", {}, {"login": 7}]}', id="malformed-entries"),
    pytest.param(b'{"assignees": [{"login": ""}]}', id="blank-login"),
    pytest.param(b'{"assignees": [{"login": "collaborator "}]}', id="not-exact-login"),
    pytest.param(b'[]', id="not-object"),
    pytest.param(b'null', id="null-object"),
    pytest.param(b'"sandbox-write-token"', id="scalar"),
    pytest.param(b'not JSON: sandbox-write-token', id="invalid-json"),
]


@pytest.mark.parametrize("body", UNCONFIRMED_ASSIGNMENT_BODIES)
@pytest.mark.parametrize("status", [200, 201])
def test_writer_rejects_unconfirmed_assignment(status, body):
    fake = FakeGitHub(assignment_response=httpx.Response(status, content=body))

    with pytest.raises(GitHubWriteError) as excinfo:
        make_writer(fake).assign(NUMBER, "collaborator")

    assert WRITE_TOKEN not in str(excinfo.value)
    assert "assignment" in str(excinfo.value)


@pytest.mark.parametrize("body", UNCONFIRMED_ASSIGNMENT_BODIES)
@pytest.mark.parametrize("status", [200, 201])
def test_unconfirmed_assignment_is_refused_recorded_and_retryable(status, body):
    ctx = make_context(actions=[recorded("action:a", "assign", "@collaborator")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(
        collaborators={"collaborator"},
        assignment_response=httpx.Response(status, content=body),
    )
    service = make_service(store, writer=make_writer(fake))

    first = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in first.actions] == ["refused"]
    assert [a.status for a in store.recorded] == ["refused"]
    assert first.actions[0].note.startswith("refused: sandbox assignment failed")
    assert WRITE_TOKEN not in first.actions[0].note
    assert store.recorded[0].note == first.actions[0].note
    assert fake.methods_paths() == [
        ("GET", f"/repos/{SANDBOX}/collaborators/collaborator"),
        ("POST", f"/repos/{SANDBOX}/issues/{NUMBER}/assignees"),
    ]

    # Reload the persisted outcome, rather than retrying the original proposal.
    ctx.actions[0] = recorded("action:a", "assign", "@collaborator", status=store.recorded[0].status)
    fake.assignment_response = httpx.Response(201, json={
        "number": NUMBER, "assignees": [{"login": "Collaborator"}],
    })
    second = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in second.actions] == ["applied"]
    assert [a.status for a in store.recorded] == ["refused", "applied"]
    assert len(fake.posts("/assignees")) == 2
    assert fake.methods_paths()[2:] == fake.methods_paths()[:2]


@pytest.mark.parametrize("status", [200, 201])
@pytest.mark.parametrize("confirmed_login", ["collaborator", "CoLlAbOrAtOr"])
def test_assignment_accepts_confirmation_among_unrelated_malformed_entries(status, confirmed_login):
    ctx = make_context(actions=[recorded("action:a", "assign", "@collaborator")])
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(
        collaborators={"collaborator"},
        assignment_response=httpx.Response(status, json={
            "number": NUMBER,
            "assignees": [None, {}, {"login": 7}, {"login": "other"}, {"login": confirmed_login}],
        }),
    )

    result = make_service(store, writer=make_writer(fake)).apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert [a.status for a in store.recorded] == ["applied"]


def test_assignment_http_failure_is_not_overridden_by_confirmation():
    fake = FakeGitHub(assignment_response=httpx.Response(403, json={
        "assignees": [{"login": "collaborator"}],
    }))

    with pytest.raises(GitHubWriteError, match="sandbox assignment failed with status 403"):
        make_writer(fake).assign(NUMBER, "collaborator")


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


@pytest.mark.parametrize("mention", ["@victim", "@acme/core"])
def test_escaped_backtick_comment_is_refused_before_github_post(mention):
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown=rf"Literal backticks: \`{mention}\`",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert fake.posts("/comments") == []
    assert fake.comments == []
    assert [a.status for a in result.actions] == ["refused"]
    assert [a.status for a in store.recorded] == ["refused"]


@pytest.mark.parametrize("line_ending", ["\r", "\r\n", "\n"], ids=["CR", "CRLF", "LF"])
@pytest.mark.parametrize("span", ["`code{blank}@victim`", "``code{blank}@acme/core``"])
def test_multiline_mention_comment_is_refused_before_github_post(line_ending, span):
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown=span.format(blank=line_ending * 2),
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub()
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert fake.posts("/comments") == []
    assert fake.comments == []
    assert [a.status for a in result.actions] == ["refused"]
    assert [a.status for a in store.recorded] == ["refused"]


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
# Transport failures become per-action refusals (never abort the apply)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("exc_type", [httpx.TimeoutException, httpx.ConnectError])
@pytest.mark.parametrize(
    ("fragment", "action"),
    [
        (f"GET /repos/{SANDBOX}/labels/graph", recorded("action:l", "label", "graph")),
        (
            f"GET /repos/{SANDBOX}/collaborators/collaborator",
            recorded("action:a", "assign", "collaborator"),
        ),
        (
            f"GET /repos/{SANDBOX}/issues/{NUMBER}/comments",
            recorded("action:c", "comment", "triage comment"),
        ),
    ],
)
def test_transport_error_per_op_is_refused(fragment, action, exc_type):
    """A timeout/connection error per writer op yields ``refused``, never ``applied``."""
    ctx = make_context(actions=[action], comment_markdown="Summary")
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(collaborators={"collaborator"}, fail={fragment: exc_type("transport down")})
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert result.actions[0].note and result.actions[0].note.startswith("refused:")
    assert "s3cret" not in (result.actions[0].note or "")


def test_transport_failure_does_not_abandon_or_drop_recorded_actions():
    """A transport failure mid-run is refused; later actions still run and all are recorded."""
    ctx = make_context(
        actions=[
            recorded("action:l", "label", "graph"),
            recorded("action:a", "assign", "collaborator"),
            recorded("action:c", "comment", "triage comment"),
        ],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(
        collaborators={"collaborator"},
        fail={f"GET /repos/{SANDBOX}/labels/graph": httpx.TimeoutException("timeout")},
    )
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    statuses = {a.action_key: a.status for a in result.actions}
    assert statuses == {"action:l": "refused", "action:a": "applied", "action:c": "applied"}
    # the failure did not abort the run, and every outcome was handed to A
    assert len(store.record_calls) == 1
    _, _, recorded_results = store.record_calls[0]
    assert {r.action_key: r.status for r in recorded_results} == statuses
    assert fake.posts(f"/issues/{NUMBER}/comments")


def test_unreadable_comment_lookup_body_is_refused():
    """A non-JSON comment-list body is a refusal, not an unhandled decode error."""
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(non_json={f"GET /repos/{SANDBOX}/issues/{NUMBER}/comments"})
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert fake.posts("/comments") == []


# --------------------------------------------------------------------------
# Step 7: comment sanitisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("line_ending", ["\r", "\r\n", "\n"], ids=["CR", "CRLF", "LF"])
@pytest.mark.parametrize("span", ["`code{blank}@alice`", "``code{blank}@acme/core``"])
def test_sanitize_rejects_mentions_across_markdown_line_endings(line_ending, span):
    with pytest.raises(UnsafeCommentError):
        sanitize_comment(span.format(blank=line_ending * 2), ["alice"])


@pytest.mark.parametrize("line_ending", ["\r", "\r\n", "\n"], ids=["CR", "CRLF", "LF"])
def test_sanitize_preserves_safe_code_across_markdown_line_endings(line_ending):
    text = f"Code `line one{line_ending}line two` and `@victim`"
    assert sanitize_comment(text, ["alice"]) == text


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


@pytest.mark.parametrize(
    "markdown",
    [
        r"Literal backticks: \`@victim\`",
        r"\`@victim`",
        r"\`@acme/core\`",
        r"\\\`@victim\\\`",
        r"`code\` @victim`",
        "``@victim`",
        "`@victim``",
        "`@victim",
        "@acme/core`",
        r"\`@alice\` and `@victim`",
        "```@victim\ncode\n```",
        "`code\n\n@victim`",
        "@alice``@victim``",
        "``@victim``@alice",
    ],
)
def test_sanitize_rejects_ambiguous_mention_delimiters(markdown):
    with pytest.raises(UnsafeCommentError):
        sanitize_comment(markdown, ["alice"])


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("Safe `@victim`", "Safe `@victim`"),
        ("Safe ``@acme/core``", "Safe ``@acme/core``"),
        ("Safe `` `@victim` ``", "Safe `` `@victim` ``"),
        (r"Even \\`@victim`", r"Even \\`@victim`"),
        (r"Code `@victim\`", r"Code `@victim\`"),
        ("Thanks ``@alice``", "Thanks `alice`"),
        (r"Literal \`backticks\`", r"Literal \`backticks\`"),
        ("Unmatched `ordinary text", "Unmatched `ordinary text"),
        ("Code `line one\nline two`", "Code `line one\nline two`"),
        ("@alice`@victim`", "`alice``@victim`"),
        ("`@victim`@alice", "`@victim``alice`"),
    ],
)
def test_sanitize_preserves_safe_backtick_syntax(markdown, expected):
    # Literal expectations retain mentions only inside genuine, matched code spans.
    assert sanitize_comment(markdown, ["alice"]) == expected


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


@pytest.mark.parametrize("prior_comments", [0, MAX_COMMENT_PAGES * COMMENTS_PER_PAGE - 1])
def test_retry_after_record_failure_does_not_repost_comment(prior_comments):
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    fake = FakeGitHub(comments=["Earlier comment"] * prior_comments)

    first = make_service(FakeStore({("falkordb", RUN): ctx}, fail_record=True), writer=make_writer(fake))
    with pytest.raises(ActionStoreError):
        first.apply_triage("falkordb", RUN, dry_run=False)
    assert len(fake.posts("/comments")) == 1

    second = make_service(FakeStore({("falkordb", RUN): ctx}), writer=make_writer(fake))
    result = second.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["applied"]
    assert "already posted" in result.actions[0].note.lower()
    assert len(fake.posts("/comments")) == 1


@pytest.mark.parametrize("final_page", ["full", "marker", "short", "empty"])
def test_writer_comment_lookup_requires_complete_search(final_page):
    comments = ["Earlier comment"] * ((MAX_COMMENT_PAGES - 1) * COMMENTS_PER_PAGE)
    comments += {
        "full": ["Final page comment"] * COMMENTS_PER_PAGE,
        "marker": ["Final page comment"] * (COMMENTS_PER_PAGE - 1) + [comment_marker(RUN)],
        "short": ["Final page comment"] * (COMMENTS_PER_PAGE - 1),
        "empty": [],
    }[final_page]
    fake = FakeGitHub(comments=comments)
    writer = make_writer(fake)

    if final_page == "full":
        with pytest.raises(GitHubWriteError) as excinfo:
            writer.find_comment(NUMBER, comment_marker(RUN))
        assert WRITE_TOKEN not in str(excinfo.value)
    else:
        assert writer.find_comment(NUMBER, comment_marker(RUN)) is (final_page == "marker")

    assert [int(r.url.params["page"]) for r in fake.requests] == list(range(1, MAX_COMMENT_PAGES + 1))
    assert all(r.url.params["per_page"] == str(COMMENTS_PER_PAGE) for r in fake.requests)
    assert fake.posts("/comments") == []


@pytest.mark.parametrize(
    ("final_page", "status", "post_count"),
    [("full", "refused", 0), ("marker", "applied", 0),
     ("short", "applied", 1), ("empty", "applied", 1)],
)
def test_comment_service_requires_complete_dedupe_search(final_page, status, post_count):
    comments = ["Earlier comment"] * ((MAX_COMMENT_PAGES - 1) * COMMENTS_PER_PAGE)
    comments += {
        "full": ["Final page comment"] * COMMENTS_PER_PAGE,
        "marker": ["Final page comment"] * (COMMENTS_PER_PAGE - 1) + [comment_marker(RUN)],
        "short": ["Final page comment"] * (COMMENTS_PER_PAGE - 1),
        "empty": [],
    }[final_page]
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(comments=comments)
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == [status]
    assert [a.status for a in store.recorded] == [status]
    assert len(fake.posts("/comments")) == post_count
    lookups = [r for r in fake.requests if r.method == "GET"]
    assert len(lookups) == MAX_COMMENT_PAGES
    if final_page == "full":
        assert result.actions[0].note.startswith("refused:")
        assert WRITE_TOKEN not in result.actions[0].note
    elif post_count:
        assert fake.requests[-1].method == "POST"
        assert comment_marker(RUN) in json.loads(fake.requests[-1].content)["body"]


@pytest.mark.parametrize("failure", [403, 429, httpx.TimeoutException(WRITE_TOKEN), httpx.ConnectError(WRITE_TOKEN)])
def test_comment_lookup_failure_never_posts_or_records_applied(failure):
    ctx = make_context(
        actions=[recorded("action:c", "comment", "triage comment")],
        comment_markdown="Summary",
    )
    store = FakeStore({("falkordb", RUN): ctx})
    fake = FakeGitHub(fail={f"GET /repos/{SANDBOX}/issues/{NUMBER}/comments": failure})
    service = make_service(store, writer=make_writer(fake))

    result = service.apply_triage("falkordb", RUN, dry_run=False)

    assert [a.status for a in result.actions] == ["refused"]
    assert [a.status for a in store.recorded] == ["refused"]
    assert result.actions[0].note.startswith("refused:")
    assert WRITE_TOKEN not in result.actions[0].note
    assert fake.posts("/comments") == []


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
