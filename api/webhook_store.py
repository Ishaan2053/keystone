"""Incoming sandbox issue upsert and replay provenance (B7 Task 2, spec section 13).

A verified ``issues.opened``/``issues.edited`` job becomes one incoming Issue in
the tenant graph, keyed by the configured ``SANDBOX_REPO`` and the issue's own
number. The write is one named, parameterized query whose params are scalars
only: the raw webhook payload never reaches Cypher.

The issue's human author gets a ``Person`` and an ``AUTHORED`` edge exactly as
the loader builds them (a bot author gets neither); a "Replayed from <url>" first
line adds a ``REPLAY_OF`` edge, but only to an upstream Issue that is already
loaded — the store never fabricates an upstream node with an empty embedding. The
``REPLAY_OF`` target never influences the write identity or the tenant.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

from api.cypher import query
from api.db import graph as tenant_graph
from api.embeddings import encode, issue_text
from api.github import validate_repo
from api.webhooks import WebhookJob

log = logging.getLogger(__name__)

_ISSUE_PREFIX = "issue:"
_GITHUB_HOST = "github.com"
# "Replayed from <url>" (ingest/replay.py) with the URL optionally in a code span.
_REPLAY_LINE = re.compile(
    r"^Replayed from\s+(?:`(?P<backticked>[^`\s]+)`|(?P<plain>\S+))\s*$"
)


def parse_replay(body: str, allowed_repos: set[str]) -> str | None:
    """Return the canonical upstream issue key of a replay, or ``None``.

    Only a first line ``Replayed from <url>`` (the URL plain or in a code span)
    counts. The URL must be a plain ``https://github.com/<owner>/<name>/issues/<n>``
    for one of ``allowed_repos`` (compared case-insensitively; the configured
    casing is returned): a malformed host, credentials, a pull-request URL, a
    query/fragment, the sandbox itself or an unknown repo is rejected.
    """
    if not isinstance(body, str) or not allowed_repos:
        return None
    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    match = _REPLAY_LINE.match(first_line)
    if match is None:
        return None
    url = match.group("backticked") or match.group("plain")
    return _replay_key(url, allowed_repos)


def _replay_key(url: str, allowed_repos: set[str]) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != _GITHUB_HOST:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.params:
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 4 or parts[2] != "issues":
        return None
    owner, name, _, number = parts
    if not number.isdigit() or int(number) <= 0:
        return None
    canonical = {repo.casefold(): repo for repo in allowed_repos}.get(f"{owner}/{name}".casefold())
    if canonical is None:
        return None
    return f"{_ISSUE_PREFIX}{canonical}#{int(number)}"


def incoming_issue_vector(job: WebhookJob) -> list[float]:
    """The 384-d vector for an incoming issue: the loader's text rule (Rule 7)."""
    return encode([issue_text(job.title, job.body)])[0]


def _epoch(value: str) -> int | None:
    """ISO 8601 from GitHub -> epoch seconds, like ``ingest.load.ts``."""
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class WebhookStore:
    """The tenant graph writes B7 needs, through named parameterized queries.

    ``graph_factory`` returns the tenant graph (``api.db.graph`` by default) and
    is injectable so the upsert can be unit-tested without a live FalkorDB.
    """

    def __init__(
        self,
        *,
        sandbox_repo: str,
        graph_factory: Callable[[str], object] = tenant_graph,
    ):
        self._sandbox_repo = validate_repo(sandbox_repo)
        self._graph_factory = graph_factory

    def upsert_issue(
        self,
        tenant: str,
        job: WebhookJob,
        vector: list[float],
        replay_key: str | None,
    ) -> str:
        """Upsert the incoming Issue, its author and optional ``REPLAY_OF``; return its key."""
        key = f"{_ISSUE_PREFIX}{self._sandbox_repo}#{job.issue_number}"
        author_login = None if job.issue_author_is_bot else job.issue_author_login
        author_key = None if author_login is None else f"person:{author_login}"
        params = {
            "key": key,
            "number": job.issue_number,
            "title": job.title,
            "body": job.body,
            "state": job.issue_state,
            "url": f"https://{_GITHUB_HOST}/{self._sandbox_repo}/issues/{job.issue_number}",
            "created_at": _epoch(job.issue_created_at),
            "incoming": True,
            "vec": vector,
            "author_login": author_login,
            "author_key": author_key,
            "author_url": None if author_login is None else job.issue_author_url,
            "replay_key": replay_key,
        }
        rows = self._graph_factory(tenant).query(query("upsert_incoming_issue"), params).result_set
        linked = bool(rows) and bool(rows[0][1])
        if replay_key is not None and not linked:
            log.warning(
                "replay target %s is not loaded in tenant %s; no REPLAY_OF edge for %s",
                replay_key,
                tenant,
                key,
            )
        return key
