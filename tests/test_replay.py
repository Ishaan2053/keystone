"""B6 Task 1: sandbox issue replay.

No real network is used anywhere in this module: every GitHub client is an
``httpx.MockTransport``. The write credential never appears in assertions that
inspect errors, only in the request the mock transport records.
"""

import json

import httpx
import pytest

from api.github import GitHubWriteError, SandboxGitHub
from ingest import replay

CONFIG = """\
slug: falkordb
org: FalkorDB
repos:
  - FalkorDB/FalkorDB
  - FalkorDB/GraphRAG-SDK
since_days: 365
docs_globs: ["README.md"]
max_pr_files: 300
"""

SINGLE = """\
slug: solo
org: Solo
repos:
  - Solo/One
since_days: 365
docs_globs: ["README.md"]
max_pr_files: 300
"""

# A tenant that (unusually) also lists the sandbox repo, so the
# "source already in the sandbox" guard is reached instead of being shadowed
# by the "repo is not configured" check.
SANDBOX_IN_CONFIG = """\
slug: falkordb
org: FalkorDB
repos:
  - FalkorDB/GraphRAG-SDK
  - acme/keystone-sandbox
since_days: 365
docs_globs: ["README.md"]
max_pr_files: 300
"""

SANDBOX = "acme/keystone-sandbox"
SANDBOX_URL = "https://github.com/acme/keystone-sandbox/issues/42"
WRITE_TOKEN = "sandbox-write-token"
READ_TOKEN = "read-only-token"


def write_config(tmp_path, text=CONFIG, name="falkordb"):
    root = tmp_path / "tenants"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yml").write_text(text, encoding="utf-8")
    return root


def tenant_root(tmp_path):
    return tmp_path / "data" / "raw" / "falkordb"


def issue_payload(
    number=7,
    repo="FalkorDB/GraphRAG-SDK",
    title="Crash on empty graph",
    body="Steps to reproduce",
    **extra,
):
    payload = {
        "number": number,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/{repo}/issues/{number}",
    }
    payload.update(extra)
    return payload


class Recorder:
    """Records every request and delegates the response to ``responder``."""

    def __init__(self, responder):
        self.requests = []
        self._responder = responder

    def __call__(self, request):
        self.requests.append(request)
        return self._responder(request)


def created(request):
    return httpx.Response(201, json={"html_url": SANDBOX_URL, "number": 42})


def make_read_client(responder):
    recorder = Recorder(responder)
    client = httpx.Client(
        transport=httpx.MockTransport(recorder),
        base_url="https://api.github.com",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {READ_TOKEN}"},
    )
    return client, recorder


def make_write_client(responder=created):
    recorder = Recorder(responder)
    client = httpx.Client(
        transport=httpx.MockTransport(recorder),
        base_url="https://api.github.com",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {WRITE_TOKEN}"},
    )
    return client, recorder


def posted_body(recorder, index=0):
    return json.loads(recorder.requests[index].content)


# --- Step 1: core replay behaviour -------------------------------------------


def test_replay_preserves_title_body_and_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    source = issue_payload()
    read, _ = make_read_client(lambda r: httpx.Response(200, json=source))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    url = replay.replay_issue(
        "falkordb",
        7,
        "FalkorDB/GraphRAG-SDK",
        read_client=read,
        writer=writer,
        tenant_root=tenant_root(tmp_path),
    )

    assert url == SANDBOX_URL
    assert len(writes.requests) == 1
    payload = posted_body(writes)
    assert payload["title"] == source["title"]
    assert payload["body"] == f"Replayed from `{source['html_url']}`\n\n{source['body']}"


def test_replay_writes_only_configured_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    source = issue_payload()
    read, reads = make_read_client(lambda r: httpx.Response(200, json=source))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    replay.replay_issue(
        "falkordb",
        7,
        "FalkorDB/GraphRAG-SDK",
        read_client=read,
        writer=writer,
        tenant_root=tenant_root(tmp_path),
    )

    request = writes.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.github.com/repos/{SANDBOX}/issues"
    assert request.headers["authorization"] == f"Bearer {WRITE_TOKEN}"
    # the read-only credential is never used for the write, and the write token
    # never touches the upstream read.
    assert all(r.headers.get("authorization") == f"Bearer {READ_TOKEN}" for r in reads.requests)
    for r in reads.requests:
        assert "api.github.com/repos/" + SANDBOX not in str(r.url)

    for bad in ("", "sandbox", "a/b/c", "https://github.com/a/b", "a/b ", " /b"):
        with pytest.raises(GitHubWriteError):
            SandboxGitHub(bad, write)


def test_ambiguous_issue_requires_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    read, _ = make_read_client(lambda r: pytest.fail("ambiguous replay must not read"))
    write, writes = make_write_client()
    monkeypatch.setattr(replay, "_read_client", lambda token: read)
    monkeypatch.setattr(replay, "_write_client", lambda token: write)
    env = {"GITHUB_TOKEN": READ_TOKEN, "GITHUB_WRITE_TOKEN": WRITE_TOKEN, "SANDBOX_REPO": SANDBOX}

    with pytest.raises(SystemExit) as excinfo:
        replay.main(["--tenant", "falkordb", "--issue", "7"], env=env)

    assert "--repo" in str(excinfo.value)
    assert writes.requests == []


def test_pr_payload_and_unconfigured_repo_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    tenant = tenant_root(tmp_path)
    pull = issue_payload()
    pull["pull_request"] = {"url": "https://api.github.com/repos/FalkorDB/GraphRAG-SDK/pulls/7"}
    read, _ = make_read_client(lambda r: httpx.Response(200, json=pull))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    with pytest.raises(replay.ReplayError):
        replay.replay_issue(
            "falkordb",
            7,
            "FalkorDB/GraphRAG-SDK",
            read_client=read,
            writer=writer,
            tenant_root=tenant,
        )

    with pytest.raises(replay.ReplayError):
        replay.replay_issue(
            "falkordb", 7, "evil/repo", read_client=read, writer=writer, tenant_root=tenant
        )

    assert writes.requests == []


# --- Step 1: refusal and edge cases ------------------------------------------


def test_missing_source_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    read, _ = make_read_client(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    with pytest.raises(replay.ReplayError):
        replay.replay_issue(
            "falkordb",
            7,
            "FalkorDB/GraphRAG-SDK",
            read_client=read,
            writer=writer,
            tenant_root=tenant_root(tmp_path),
        )

    assert writes.requests == []


def test_null_body_keeps_prefix_and_empty_body(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    source = issue_payload(body=None)
    read, _ = make_read_client(lambda r: httpx.Response(200, json=source))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    replay.replay_issue(
        "falkordb",
        7,
        "FalkorDB/GraphRAG-SDK",
        read_client=read,
        writer=writer,
        tenant_root=tenant_root(tmp_path),
    )

    assert posted_body(writes)["body"] == f"Replayed from `{source['html_url']}`\n\n"


def test_missing_sandbox_repo_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    read, _ = make_read_client(lambda r: pytest.fail("must refuse before reading"))
    write, writes = make_write_client()
    monkeypatch.setattr(replay, "_read_client", lambda token: read)
    monkeypatch.setattr(replay, "_write_client", lambda token: write)
    env = {"GITHUB_TOKEN": READ_TOKEN, "GITHUB_WRITE_TOKEN": WRITE_TOKEN}

    with pytest.raises(SystemExit) as excinfo:
        replay.main(
            ["--tenant", "falkordb", "--repo", "FalkorDB/GraphRAG-SDK", "--issue", "7"], env=env
        )

    assert "SANDBOX_REPO" in str(excinfo.value)
    assert writes.requests == []


def test_missing_write_token_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    read, _ = make_read_client(lambda r: pytest.fail("must refuse before reading"))
    write, writes = make_write_client()
    monkeypatch.setattr(replay, "_read_client", lambda token: read)
    monkeypatch.setattr(replay, "_write_client", lambda token: write)
    env = {"GITHUB_TOKEN": READ_TOKEN, "SANDBOX_REPO": SANDBOX}

    with pytest.raises(SystemExit) as excinfo:
        replay.main(
            ["--tenant", "falkordb", "--repo", "FalkorDB/GraphRAG-SDK", "--issue", "7"], env=env
        )

    assert "GITHUB_WRITE_TOKEN" in str(excinfo.value)
    assert writes.requests == []


def test_writer_requires_write_credential():
    recorder = Recorder(created)
    anonymous = httpx.Client(
        transport=httpx.MockTransport(recorder), base_url="https://api.github.com"
    )
    writer = SandboxGitHub(SANDBOX, anonymous)

    with pytest.raises(GitHubWriteError):
        writer.create_issue("title", "body")

    assert recorder.requests == []


def test_source_already_in_sandbox_is_refused(tmp_path, monkeypatch):
    # The sandbox repo is explicitly configured for the tenant, so the refusal
    # must come from the sandbox guard, not the "repo not configured" check.
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path, SANDBOX_IN_CONFIG))
    source = issue_payload(number=9, repo=SANDBOX)
    read, _ = make_read_client(lambda r: httpx.Response(200, json=source))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    with pytest.raises(replay.ReplayError, match="already the sandbox"):
        replay.replay_issue(
            "falkordb",
            9,
            SANDBOX,
            read_client=read,
            writer=writer,
            tenant_root=tenant_root(tmp_path),
        )

    assert writes.requests == []


def test_unknown_tenant_and_nonpositive_issue_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    read, _ = make_read_client(lambda r: pytest.fail("must refuse before reading"))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)
    tenant = tenant_root(tmp_path)

    with pytest.raises(replay.ReplayError):
        replay.replay_issue(
            "ghost", 7, "FalkorDB/GraphRAG-SDK", read_client=read, writer=writer, tenant_root=tenant
        )
    for number in (0, -3):
        with pytest.raises(replay.ReplayError):
            replay.replay_issue(
                "falkordb",
                number,
                "FalkorDB/GraphRAG-SDK",
                read_client=read,
                writer=writer,
                tenant_root=tenant,
            )

    assert writes.requests == []


# --- Step 1: source selection ------------------------------------------------


def test_cached_issue_is_used_without_network_and_disambiguates(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    tenant = tenant_root(tmp_path)
    repo_dir = tenant / "FalkorDB__GraphRAG-SDK"
    repo_dir.mkdir(parents=True)
    source = issue_payload()
    (repo_dir / "issues.json").write_text(json.dumps([source]), encoding="utf-8")
    read, reads = make_read_client(lambda r: pytest.fail("cached source must not hit the network"))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    url = replay.replay_issue(
        "falkordb", 7, None, read_client=read, writer=writer, tenant_root=tenant
    )

    assert url == SANDBOX_URL
    assert reads.requests == []
    assert len(writes.requests) == 1


def test_single_repo_tenant_autoselects(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path, SINGLE, name="solo"))
    source = issue_payload(number=1, repo="Solo/One")
    read, reads = make_read_client(lambda r: httpx.Response(200, json=source))
    write, writes = make_write_client()
    writer = SandboxGitHub(SANDBOX, write)

    replay.replay_issue(
        "solo",
        1,
        None,
        read_client=read,
        writer=writer,
        tenant_root=tmp_path / "data" / "raw" / "solo",
    )

    assert str(reads.requests[0].url) == "https://api.github.com/repos/Solo/One/issues/1"
    assert len(writes.requests) == 1


# --- Step 2: the copy never pings or links upstream --------------------------


def replayed_body(tmp_path, monkeypatch, body):
    monkeypatch.setattr(replay, "CONFIG_ROOT", write_config(tmp_path))
    read, _ = make_read_client(lambda r: httpx.Response(200, json=issue_payload(body=body)))
    write, writes = make_write_client()
    replay.replay_issue(
        "falkordb",
        7,
        "FalkorDB/GraphRAG-SDK",
        read_client=read,
        writer=SandboxGitHub(SANDBOX, write),
        tenant_root=tenant_root(tmp_path),
    )
    prefix = "Replayed from `https://github.com/FalkorDB/GraphRAG-SDK/issues/7`\n\n"
    posted = posted_body(writes)["body"]
    assert posted.startswith(prefix)
    return posted[len(prefix) :]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("cc @swilly22 and @gkorland", "cc `swilly22` and `gkorland`"),
        ("@first at the start", "`first` at the start"),
        ("ping @FalkorDB/core please", "ping `FalkorDB/core` please"),
        ("odd -@dash .@dot +@plus %@pct (@paren", "odd -`dash` .`dot` +`plus` %`pct` (`paren`"),
        (
            "entities &#64;one &#x40;two &#X40;three &#064;four &commat;five",
            "entities `one` `two` `three` `four` `five`",
        ),
        (
            "mail user@example.com or first.last@falkordb.com",
            "mail user@example.com or first.last@falkordb.com",
        ),
        ("> reported by @reporter", "> reported by `reporter`"),
    ],
)
def test_replay_rewrites_mentions_into_code_spans(tmp_path, monkeypatch, body, expected):
    assert replayed_body(tmp_path, monkeypatch, body) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("dup of FalkorDB/FalkorDB#1333.", "dup of `FalkorDB/FalkorDB#1333`."),
        (
            "see https://github.com/FalkorDB/FalkorDB/issues/1333.",
            "see `https://github.com/FalkorDB/FalkorDB/issues/1333`.",
        ),
        (
            "fixed by https://github.com/FalkorDB/FalkorDB/pull/12/files#diff-1 maybe",
            "fixed by `https://github.com/FalkorDB/FalkorDB/pull/12/files#diff-1` maybe",
        ),
        (
            "asked in https://github.com/o/r/discussions/9",
            "asked in `https://github.com/o/r/discussions/9`",
        ),
        (
            "autolink <https://github.com/o/r/issues/2>",
            "autolink <`https://github.com/o/r/issues/2`>",
        ),
        (
            "[the crash](https://github.com/o/r/issues/3) again",
            "the crash (`https://github.com/o/r/issues/3`) again",
        ),
        (
            '[@author\'s PR](<https://github.com/o/r/pull/4> "title")',
            "`author`'s PR (`https://github.com/o/r/pull/4`)",
        ),
        (
            "[docs](https://docs.falkordb.com) stay links",
            "[docs](https://docs.falkordb.com) stay links",
        ),
    ],
)
def test_replay_rewrites_cross_references_and_issue_urls(tmp_path, monkeypatch, body, expected):
    assert replayed_body(tmp_path, monkeypatch, body) == expected


def test_replay_leaves_code_alone(tmp_path, monkeypatch):
    body = (
        "Run `MATCH (n) RETURN n` as @tester\n"
        "\n"
        "```python\n"
        "@app.route('/x')  # see FalkorDB/FalkorDB#1\n"
        "def x(): ...\n"
        "```\n"
        "\n"
        "~~~\n"
        "https://github.com/o/r/issues/5\n"
        "~~~\n"
        "and ``@inline`` code"
    )
    expected = body.replace("as @tester", "as `tester`")

    assert replayed_body(tmp_path, monkeypatch, body) == expected


def test_replay_span_delimiter_cannot_merge_with_adjacent_code(tmp_path, monkeypatch):
    assert replayed_body(tmp_path, monkeypatch, "@a`code`@b") == "`a` `code` `b`"


@pytest.mark.parametrize(
    ("body", "fence"),
    [
        ("Unmatched ` tick then @maintainer", "```"),
        (r"Escaped \`@maintainer\`", "```"),
        ("`one\n\n@maintainer`", "```"),
        ("- ```\n@maintainer\n```", "````"),
    ],
)
def test_replay_fences_the_whole_body_when_code_is_ambiguous(tmp_path, monkeypatch, body, fence):
    # The fence is longer than any backtick run in the body, so nothing inside can close it.
    assert replayed_body(tmp_path, monkeypatch, body) == f"{fence}\n{body}\n{fence}"
