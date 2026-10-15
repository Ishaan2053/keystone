"""Sandbox-bound GitHub writer (B6, spec section 7).

The only repository this module can write to is the one fixed at construction:
``create_issue`` has no target-repo parameter, so a token scoped to the sandbox
cannot be pointed at another repo. The write base is always
``https://api.github.com``, the timeout is finite and redirects are disabled.

``GITHUB_TOKEN`` (read-only) must never reach this writer. The injected client
must carry the sandbox-scoped ``GITHUB_WRITE_TOKEN``; creation without a
credential is refused. Tokens never appear in errors or output.
"""
from __future__ import annotations

import re

import httpx

API = "https://api.github.com"
JSON_ACCEPT = "application/vnd.github+json"
TIMEOUT = 30.0
# Strict owner/name: no scheme, no extra slash, no whitespace, no empty part.
REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


class GitHubWriteError(RuntimeError):
    """A sandbox write was refused or failed; the message never contains a token."""


def validate_repo(repo: str) -> str:
    """Return ``owner/name`` or raise; rejects schemes, paths and extra slashes."""
    if not isinstance(repo, str) or not REPO.fullmatch(repo):
        raise GitHubWriteError("sandbox repo must be a strict owner/name (no scheme or extra path)")
    return repo


class SandboxGitHub:
    """Create issues in exactly one fixed sandbox repo.

    The target repo is bound at construction and cannot be overridden per call,
    so replay and apply can only ever write to ``SANDBOX_REPO``.
    """

    def __init__(self, sandbox_repo: str, client: httpx.Client):
        self.repo = validate_repo(sandbox_repo)
        self.client = client

    def create_issue(self, title: str, body: str) -> str:
        if not self.client.headers.get("authorization"):
            raise GitHubWriteError("GITHUB_WRITE_TOKEN is required to create sandbox issues")
        response = self.client.post(
            f"{API}/repos/{self.repo}/issues",
            json={"title": title, "body": body},
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise GitHubWriteError(f"sandbox issue creation failed with status {response.status_code}")
        url = response.json().get("html_url")
        prefix = f"https://github.com/{self.repo}/issues/"
        if not isinstance(url, str) or not url.lower().startswith(prefix.lower()):
            raise GitHubWriteError("sandbox issue creation returned no canonical URL")
        return url
