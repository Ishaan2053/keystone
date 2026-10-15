"""Sandbox-bound GitHub writer (B6, spec section 7).

The only repository this module can write to is the one fixed at construction:
``create_issue``, ``ensure_label``, ``add_labels``, ``is_collaborator``,
``assign`` and ``comment`` have no target-repo parameter, so a token scoped to
the sandbox cannot be pointed at another repo. The write base is always
``https://api.github.com``, the timeout is finite and redirects are disabled.

``GITHUB_TOKEN`` (read-only) must never reach this writer. The injected client
must carry the sandbox-scoped ``GITHUB_WRITE_TOKEN``; any operation without a
credential is refused. Tokens never appear in errors or output.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

API = "https://api.github.com"
JSON_ACCEPT = "application/vnd.github+json"
TIMEOUT = 30.0
# Default colour for a label this writer has to create (implementation choice).
LABEL_COLOR = "ededed"
COMMENTS_PER_PAGE = 100
# Bound the comment pagination so a huge thread cannot loop forever.
MAX_COMMENT_PAGES = 20
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
    """Operate on exactly one fixed sandbox repo.

    The target repo is bound at construction and cannot be overridden per call,
    so replay and apply can only ever write to ``SANDBOX_REPO``.
    """

    def __init__(self, sandbox_repo: str, client: httpx.Client):
        self.repo = validate_repo(sandbox_repo)
        self.client = client

    def _require_auth(self) -> None:
        if not self.client.headers.get("authorization"):
            raise GitHubWriteError("GITHUB_WRITE_TOKEN is required for sandbox writes")

    def create_issue(self, title: str, body: str) -> str:
        self._require_auth()
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

    def ensure_label(self, name: str) -> None:
        """Create the label in the sandbox if it is missing; otherwise no-op.

        Uses an exact encoded-name lookup rather than paginating every label.
        An "already exists" race is only tolerated after re-confirming the label
        exists, so a genuine failure is never silently treated as success.
        """
        self._require_auth()
        if not isinstance(name, str) or not name.strip():
            raise GitHubWriteError("sandbox label name must be nonblank")
        name = name.strip()
        encoded = quote(name, safe="")
        lookup = self.client.get(
            f"{API}/repos/{self.repo}/labels/{encoded}",
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if lookup.status_code == 200:
            return
        if lookup.status_code != 404:
            raise GitHubWriteError(f"sandbox label lookup failed with status {lookup.status_code}")
        created = self.client.post(
            f"{API}/repos/{self.repo}/labels",
            json={"name": name, "color": LABEL_COLOR},
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if created.status_code in (200, 201):
            return
        if created.status_code == 422:
            # Lost a create race: only succeed once the label is confirmed to exist.
            confirm = self.client.get(
                f"{API}/repos/{self.repo}/labels/{encoded}",
                headers={"Accept": JSON_ACCEPT},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
            if confirm.status_code == 200:
                return
        raise GitHubWriteError(f"sandbox label creation failed with status {created.status_code}")

    def add_labels(self, number: int, labels: list[str]) -> None:
        """Attach one or more existing labels to a sandbox issue."""
        self._require_auth()
        if not labels:
            raise GitHubWriteError("no sandbox labels to add")
        response = self.client.post(
            f"{API}/repos/{self.repo}/issues/{number}/labels",
            json={"labels": list(labels)},
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise GitHubWriteError(f"sandbox label attach failed with status {response.status_code}")

    def is_collaborator(self, login: str) -> bool:
        """Return whether ``login`` is a sandbox collaborator.

        ``204`` means eligible and ``404`` means not a collaborator. ``403`` and
        ``429`` are lookup failures, not proof of non-membership, so they raise.
        """
        self._require_auth()
        if not isinstance(login, str) or not login.strip():
            raise GitHubWriteError("sandbox collaborator login must be nonblank")
        response = self.client.get(
            f"{API}/repos/{self.repo}/collaborators/{quote(login.strip(), safe='')}",
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if response.status_code == 204:
            return True
        if response.status_code == 404:
            return False
        raise GitHubWriteError(f"sandbox collaborator lookup failed with status {response.status_code}")

    def assign(self, number: int, login: str) -> None:
        """Assign one sandbox collaborator and confirm it in the issue response."""
        self._require_auth()
        if not isinstance(login, str) or not login.strip():
            raise GitHubWriteError("sandbox assignee login must be nonblank")
        response = self.client.post(
            f"{API}/repos/{self.repo}/issues/{number}/assignees",
            json={"assignees": [login.strip()]},
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise GitHubWriteError(f"sandbox assignment failed with status {response.status_code}")
        try:
            issue = response.json()
        except ValueError:
            raise GitHubWriteError("sandbox assignment returned an unreadable response") from None
        assignees = issue.get("assignees") if isinstance(issue, dict) else None
        if not isinstance(assignees, list) or not any(
            isinstance(assignee, dict)
            and isinstance(assignee.get("login"), str)
            and assignee["login"].casefold() == login.strip().casefold()
            for assignee in assignees
        ):
            raise GitHubWriteError("sandbox assignment response did not confirm the requested assignee")

    def comment(self, number: int, body: str) -> None:
        """Post one comment on a sandbox issue."""
        self._require_auth()
        if not isinstance(body, str) or not body.strip():
            raise GitHubWriteError("sandbox comment body must be nonblank")
        response = self.client.post(
            f"{API}/repos/{self.repo}/issues/{number}/comments",
            json={"body": body},
            headers={"Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise GitHubWriteError(f"sandbox comment failed with status {response.status_code}")

    def find_comment(self, number: int, marker: str) -> bool:
        """Return whether a sandbox issue already has a comment containing ``marker``.

        Used before posting the run comment so a retry after a GitHub success but
        a graph-recording failure does not blindly repost.
        """
        self._require_auth()
        if not isinstance(marker, str) or not marker:
            raise GitHubWriteError("comment marker must be nonblank")
        for page in range(1, MAX_COMMENT_PAGES + 1):
            response = self.client.get(
                f"{API}/repos/{self.repo}/issues/{number}/comments",
                params={"per_page": COMMENTS_PER_PAGE, "page": page},
                headers={"Accept": JSON_ACCEPT},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise GitHubWriteError(f"sandbox comment lookup failed with status {response.status_code}")
            items = response.json()
            if not isinstance(items, list):
                raise GitHubWriteError("sandbox comment lookup returned an unexpected payload")
            for item in items:
                body = item.get("body") if isinstance(item, dict) else None
                if isinstance(body, str) and marker in body:
                    return True
            if len(items) < COMMENTS_PER_PAGE:
                return False
        return False
