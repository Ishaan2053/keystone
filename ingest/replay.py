"""Replay an upstream GitHub issue into the sandbox (B6, spec sections 5 and 9).

    python -m ingest.replay --tenant falkordb --repo FalkorDB/GraphRAG-SDK --issue 42

This CLI performs exactly one deliberate GitHub write: it creates one issue in
``SANDBOX_REPO``. It is not the apply dry run, which never writes. Upstream
reads use ``GITHUB_TOKEN`` (read-only); the single write uses
``GITHUB_WRITE_TOKEN`` through :class:`api.github.SandboxGitHub`. Mentions and
cross-references in the copied body are rewritten into code spans first, so the
copy never pings an upstream maintainer or links back to an upstream issue.

Environment is read from the process by a small local reader that also honours a
repo ``.env`` (the shell wins). ``ingest/fetch.py``'s client and env handling are
deliberately not reused for writes, so the read-only fetcher keeps its own
credentials and this CLI owns the sandbox write credential.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

import httpx
import yaml

from api.github import API, JSON_ACCEPT, GitHubWriteError, SandboxGitHub
from api.github_text import AmbiguousMarkdown, code_span, neutralize

ROOT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = ROOT / "ingest" / "tenants"
ENV_FILE = ROOT / ".env"
USER_AGENT = "keystone-replay"
TIMEOUT = 30.0


class ReplayError(RuntimeError):
    """A replay was refused; the message never contains a token."""


def load_env(path: Path = ENV_FILE) -> None:
    """Small local reader: KEY=VALUE lines into ``os.environ``; the shell wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_tenant_config(tenant: str, config_root: Path | None = None) -> dict:
    """Return the tenant's YAML config, or refuse an unknown/malformed tenant."""
    config_root = CONFIG_ROOT if config_root is None else config_root
    path = config_root / f"{tenant}.yml"
    if not path.is_file():
        raise ReplayError(f"unknown tenant '{tenant}': no {path.name} under {config_root}")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    repos = config.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ReplayError(f"tenant '{tenant}' has no configured repos")
    return config


def cached_issue(tenant_root: Path, repo: str, number: int) -> dict | None:
    """Return a cached upstream issue from ``<tenant_root>/<owner__name>/issues.json``."""
    path = tenant_root / repo.replace("/", "__") / "issues.json"
    if not path.is_file():
        return None
    try:
        issues = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(issues, list):
        return None
    return next((i for i in issues if isinstance(i, dict) and i.get("number") == number), None)


def select_repo(tenant: str, repos: list[str], number: int, tenant_root: Path) -> str:
    """Pick the source repo only when it is unambiguous; never guess."""
    if len(repos) == 1:
        return repos[0]
    matches = [repo for repo in repos if cached_issue(tenant_root, repo, number) is not None]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ReplayError(
            f"issue #{number} is not cached for any repo of tenant '{tenant}' ({', '.join(repos)}); "
            "pass --repo owner/name to name the source"
        )
    raise ReplayError(
        f"issue #{number} matches multiple repos of tenant '{tenant}' ({', '.join(matches)}); "
        "pass --repo owner/name to disambiguate"
    )


def fetch_source_issue(read_client: httpx.Client, repo: str, number: int) -> dict:
    """GET one explicit source issue with the read-only client."""
    response = read_client.get(f"/repos/{repo}/issues/{number}")
    if response.status_code == 404:
        raise ReplayError(f"source issue {repo}#{number} was not found")
    if response.status_code != 200:
        raise ReplayError(f"reading source issue {repo}#{number} failed with status {response.status_code}")
    return response.json()


def canonical_source_url(issue: dict, repo: str, number: int, sandbox_repo: str) -> str:
    """Validate the payload and return its canonical GitHub URL."""
    if "pull_request" in issue:
        raise ReplayError(f"{repo}#{number} is a pull request; replay only issues")
    if issue.get("number") != number:
        raise ReplayError(f"source issue number {issue.get('number')!r} does not match {number}")
    url = issue.get("html_url")
    if not isinstance(url, str):
        raise ReplayError(f"source issue {repo}#{number} has no canonical URL")
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or parsed.netloc != "github.com" or len(parts) != 4 or parts[2] != "issues":
        raise ReplayError(f"source issue {repo}#{number} has an unexpected canonical URL")
    if f"{parts[0]}/{parts[1]}".lower() != repo.lower() or parts[3] != str(number):
        raise ReplayError(f"canonical URL does not match source {repo}#{number}")
    if repo.lower() == sandbox_repo.lower():
        raise ReplayError(f"source {repo} is already the sandbox; refusing to replay it")
    return url


def inert_body(body: str) -> str:
    """The upstream body with no live mention or cross-reference (``api.github_text``).

    When its code delimiters are too ambiguous to rewrite safely, the whole body
    is copied as one fenced code block instead, which GitHub never links.
    """
    try:
        return neutralize(body)
    except AmbiguousMarkdown:
        longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}\n{body}\n{fence}"


def replay_issue(
    tenant: str,
    issue_number: int,
    repo: str | None,
    *,
    read_client: httpx.Client,
    writer: SandboxGitHub,
    tenant_root: Path,
) -> str:
    """Copy one upstream issue into the sandbox and return the new issue URL.

    Reads a cached upstream issue when available, otherwise GETs the explicit
    source issue. The write always goes through ``writer``, whose target repo is
    fixed to ``SANDBOX_REPO``.
    """
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number <= 0:
        raise ReplayError("issue number must be a positive integer")

    config = load_tenant_config(tenant)
    repos = list(config["repos"])
    if repo is not None:
        if repo not in repos:
            raise ReplayError(f"repo '{repo}' is not configured for tenant '{tenant}' ({', '.join(repos)})")
        source_repo = repo
    else:
        source_repo = select_repo(tenant, repos, issue_number, tenant_root)

    issue = cached_issue(tenant_root, source_repo, issue_number)
    if issue is None:
        issue = fetch_source_issue(read_client, source_repo, issue_number)

    url = canonical_source_url(issue, source_repo, issue_number, writer.repo)

    title = issue.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ReplayError(f"source issue {source_repo}#{issue_number} has no title")

    # The source URL goes in a code span and the body is neutralized, so the copy
    # pings no upstream maintainer and adds no backlink to the upstream issue.
    body = f"Replayed from {code_span(url)}\n\n{inert_body(issue.get('body') or '')}"
    return writer.create_issue(title, body)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ingest.replay",
        description=(
            "Copy one upstream issue into SANDBOX_REPO as a new sandbox issue. "
            "This is a deliberate single GitHub write, not the apply dry run."
        ),
    )
    parser.add_argument("--tenant", required=True, help="configured tenant slug, e.g. falkordb")
    parser.add_argument("--repo", default=None, help="source repo owner/name; required when the issue number is ambiguous")
    parser.add_argument("--issue", required=True, type=int, help="upstream issue number")
    return parser.parse_args(argv)


def _read_client(token: str | None) -> httpx.Client:
    headers = {"Accept": JSON_ACCEPT, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API, timeout=TIMEOUT, follow_redirects=False, headers=headers)


def _write_client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API,
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={
            "Accept": JSON_ACCEPT,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        },
    )


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> None:
    args = _parse_args(argv)
    if env is None:
        load_env()
        env = os.environ

    write_token = env.get("GITHUB_WRITE_TOKEN")
    if not write_token:
        raise SystemExit("GITHUB_WRITE_TOKEN is not set (in .env or the shell); replay creates a sandbox issue")
    sandbox_repo = env.get("SANDBOX_REPO")
    if not sandbox_repo:
        raise SystemExit("SANDBOX_REPO is not set (in .env or the shell); replay needs the sandbox target")

    read_client = _read_client(env.get("GITHUB_TOKEN"))
    write_client = _write_client(write_token)
    tenant_root = ROOT / "data" / "raw" / args.tenant

    try:
        writer = SandboxGitHub(sandbox_repo, write_client)
        url = replay_issue(
            args.tenant, args.issue, args.repo,
            read_client=read_client, writer=writer, tenant_root=tenant_root,
        )
    except (ReplayError, GitHubWriteError) as exc:
        raise SystemExit(f"replay refused: {exc}") from exc

    print(url)


if __name__ == "__main__":
    main()
