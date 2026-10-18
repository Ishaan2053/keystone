"""Immutable Slack-notification contracts, the semantic fingerprint and renderer.

Task 2 created the shared contracts: the ``Cause`` of an attempt, an immutable
``RunSnapshot`` of one persisted run (the store deep-copies the recalled data so a
later feedback/action mutation cannot change a reserved parent), and the attempt
and message models the coordinator passes to the renderer.

Task 3 adds two pure functions over those contracts:

``recommendation_fingerprint`` reads identity only from the recorded label/assign
actions joined to the triage claim citation sets, so run ids, timestamps, tool
timings, trace order, comment/summary prose, list order, duplicates and login case
never move the hash while a changed reviewer, label, risk or decision does.

``render_message`` turns one attempt into a bounded Slack payload. It reads only
its arguments — never the graph, the network or an LLM — renders untrusted content
as ``plain_text`` (never ``mrkdwn``), and keeps application-built ``mrkdwn`` for
validated links only. The coordinator lives in ``api.notifications``.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, urlparse

from api import schemas


# Why an eligible attempt exists; a parentless root is always an issue event.
Cause = Literal["issue_opened", "issue_edited", "github_feedback", "ui_feedback"]
FailureStage = Literal["ingest", "triage", "apply"]

# Fixed application copy: the top-level fallback text is never built from
# untrusted titles, summaries or claims, and is the only thing a client that
# ignores blocks ever shows.
FALLBACK_TEXT = "Keystone triage notification. Open the full run in Keystone for details."

# Fixed stage copy: a failure message never echoes an exception, traceback, token,
# raw body or response. It is the complete failure description a maintainer sees.
STAGE_FAILURE_COPY: dict[str, str] = {
    "ingest": "Processing failed while ingesting this issue; no triage result is available.",
    "triage": "Processing failed during triage; no triage result is available.",
    "apply": "Triage succeeded but applying GitHub actions failed; the persisted triage is below.",
}
RECOVERY_NOTE = "Recovered: an earlier attempt for this event failed; this is the recovery attempt."
TRUNCATION_NOTICE = (
    "Some details were truncated to stay within Slack limits; open the full run for everything."
)

# Fixed application caps (characters unless stated) and the Slack-safe byte caps.
MAX_TITLE_CHARS = 160
MAX_SUMMARY_CHARS = 500
MAX_CLAIM_CHARS = 240
MAX_CITATION_CHARS = 160
MAX_CHANGE_CHARS = 180
MAX_URL_CHARS = 2048
MAX_CHANGES = 3
MAX_CLAIMS_PER_CATEGORY = 3
MAX_EVIDENCE_ENTRIES = 5
MAX_EVIDENCE_KEYS = 25
MAX_BLOCK_BYTES = 2500
MAX_PAYLOAD_BYTES = 24_000

GITHUB_HOST = "github.com"
# The ``owner/name`` shape of a canonical issue key (as ``api.github.REPO``), so a
# hostile key cannot smuggle markup or extra path segments into the issue URL.
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
# A zero-width separator: neutralizes an untrusted ``@`` without changing how the
# text reads, so untrusted content can never become an ``@here``-style mention.
ZWSP = "\u200b"

CAUSE_COPY = {
    "issue_opened": "New issue triage",
    "issue_edited": "Issue edited",
    "github_feedback": "GitHub feedback",
    "ui_feedback": "UI feedback (read-only)",
}

# The claim sections, the prefix that introduces each rendered block, and the noun
# a change entry uses. Order is stable and drives evidence-key order.
_CLAIM_SECTIONS = (
    ("labels", "Suggested labels: ", "label"),
    ("reviewers", "Suggested reviewers: ", "reviewer"),
    ("risks", "Risks: ", "risk"),
    ("decisions", "Related decisions: ", "decision"),
)


@dataclass(frozen=True)
class RunSnapshot:
    """One persisted run's recalled data plus the issue title and real replay key.

    Frozen, and the recalled ``data`` is deep-copied on construction: reserving a
    parent must be immune to later mutations of the recalled actions or feedback.
    """

    data: schemas.RecallRunData
    title: str
    replay_of: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", deepcopy(self.data))


@dataclass(frozen=True)
class NotificationAttempt:
    """Server-constructed identity of one notification attempt (never client-supplied)."""

    tenant: str
    issue_key: str
    event_id: str
    cause: Cause
    title: str
    replay_of: str | None
    parent: RunSnapshot | None


@dataclass(frozen=True)
class NotificationMessage:
    """One attempt plus its outcome: the current run, the apply result and any failure stage."""

    attempt: NotificationAttempt
    current: RunSnapshot | None
    applied: schemas.ApplyResult | None
    failure_stage: FailureStage | None
    recovered: bool = False


# --- canonical identity and the fingerprint ---------------------------------


def _ws(text: object) -> str:
    """Collapse every run of whitespace; the one normalization all identity uses."""
    return " ".join(str(text).split())


def _label_identity(value: str) -> str:
    """A label value keeps its case; only surrounding/interior whitespace is normalized."""
    return _ws(value)


def _reviewer_identity(value: str) -> str:
    """A reviewer login is case-folded and loses one leading ``@``."""
    return _ws(value).lstrip("@").casefold()


def _module_identity(module: str | None) -> str:
    """A module key keeps its case; a missing module is the empty identity."""
    return _ws(module or "")


def _claim_citations(claims, identity) -> dict[str, set[str]]:
    """Map each claim's identity to the union of its citations (duplicates merge)."""
    found: dict[str, set[str]] = {}
    for claim in claims:
        key = identity(claim.text)
        if key:
            found.setdefault(key, set()).update(claim.citations)
    return found


def _claim_rows(claims) -> list[list]:
    """Sorted, unique risk/decision rows of ``[normalized text, sorted citations]``."""
    found: dict[str, set[str]] = {}
    for claim in claims:
        text = _ws(claim.text)
        if text:
            found.setdefault(text, set()).update(claim.citations)
    return [[text, sorted(found[text])] for text in sorted(found)]


def canonical_identity(run: RunSnapshot) -> dict[str, list]:
    """The semantic identity of one run: label/assign identities joined to citations.

    Labels and reviewers come from the *recorded actions* regardless of their
    later status, matched to the triage claims only to attach that identity's
    citation set. Risks and decisions come from their own claims. Nothing here
    reads ``comment_markdown`` or any prose.
    """
    triage = run.data.triage
    label_cites = _claim_citations(triage.labels, _label_identity)
    reviewer_cites = _claim_citations(triage.reviewers, _reviewer_identity)

    labels: set[tuple] = set()
    reviewers: set[tuple] = set()
    for action in run.data.actions:
        if action.kind == "label":
            value = _label_identity(action.value)
            if value:
                labels.add((value, tuple(sorted(label_cites.get(value, ())))))
        elif action.kind == "assign":
            login = _reviewer_identity(action.value)
            if login:
                reviewers.add(
                    (login, _module_identity(action.module), tuple(sorted(reviewer_cites.get(login, ()))))
                )
    return {
        "labels": [list(row) for row in sorted(labels)],
        "reviewers": [list(row) for row in sorted(reviewers)],
        "risks": _claim_rows(triage.risks),
        "decisions": _claim_rows(triage.decisions),
    }


def recommendation_fingerprint(run: RunSnapshot) -> str:
    """SHA-256 of the run's canonical identity, stable across execution metadata."""
    canonical = canonical_identity(run)
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _describe(section: str, row: list, noun: str) -> str:
    """A short, untrusted description of one changed identity."""
    if section == "reviewers":
        login, module = row[0], row[1]
        return f"reviewer {login}" + (f" on {module}" if module else "")
    if section == "labels":
        return f"label {row[0]}"
    return f'{noun} "{row[0]}"'


def change_entries(parent: RunSnapshot, current: RunSnapshot) -> list[str]:
    """A compact, deterministic before/after list of changed recommendations."""
    before = canonical_identity(parent)
    after = canonical_identity(current)
    entries: list[str] = []
    for section, _, noun in _CLAIM_SECTIONS:
        old = {json.dumps(row, sort_keys=True): row for row in before[section]}
        new = {json.dumps(row, sort_keys=True): row for row in after[section]}
        for key in sorted(new.keys() - old.keys()):
            entries.append(f"Added {_describe(section, new[key], noun)}")
        for key in sorted(old.keys() - new.keys()):
            entries.append(f"Removed {_describe(section, old[key], noun)}")
    return entries


# --- bounded, inert rendering -----------------------------------------------


def _clip_chars(text: str, limit: int) -> str:
    """Trim to at most ``limit`` characters, marking the trim with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def _clip_bytes(text: str, limit: int) -> str:
    """Trim to at most ``limit`` UTF-8 bytes, never splitting a character."""
    if len(text.encode("utf-8")) <= limit:
        return text
    ellipsis = "…"
    budget = max(limit - len(ellipsis.encode("utf-8")), 0)
    kept: list[str] = []
    used = 0
    for char in text:
        size = len(char.encode("utf-8"))
        if used + size > budget:
            break
        kept.append(char)
        used += size
    return "".join(kept) + ellipsis


def _inert(text: str) -> str:
    """Neutralize every ``@`` so untrusted plain text can never form a mention."""
    return text.replace("@", "@" + ZWSP)


_MRKDWN_META = str.maketrans({char: None for char in "<>|&"})


def _mrkdwn_inert(text: str) -> str:
    """A link label or plain entry inside mrkdwn: drop markup characters, neutralize ``@``."""
    return _inert(text.translate(_MRKDWN_META))


def _plain_section(text: str) -> dict:
    return {"type": "section", "text": {"type": "plain_text", "text": text, "emoji": False}}


def _mrkdwn_section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _finalize_copy(block: dict) -> tuple[dict, bool]:
    """Return a byte-capped copy of a block and whether its text had to be clipped."""
    text = block["text"]["text"]
    clipped = _clip_bytes(text, MAX_BLOCK_BYTES)
    finalized = {"type": block["type"], "text": dict(block["text"])}
    finalized["text"]["text"] = clipped
    return finalized, clipped != text


def _issue_parts(issue_key: str) -> tuple[str, str]:
    """Split ``issue:<owner>/<name>#<n>`` into its repo and number, tolerating anything."""
    text = issue_key[6:] if issue_key.startswith("issue:") else str(issue_key)
    repo, marker, number = text.rpartition("#")
    if marker and repo:
        return repo, number
    return text, "?"


def _run_url(base: str | None, tenant: str, run_key: str) -> str | None:
    """The encoded ``/t/<tenant>?run=<uuid>`` link, or ``None`` without a usable base."""
    text = (base or "").strip().rstrip("/")
    if not text or any(char in text for char in "<>|&") or any(char.isspace() for char in text):
        return None
    try:
        parsed = urlparse(text)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
    except ValueError:
        return None
    uuid = run_key[4:] if run_key.startswith("run:") else run_key
    url = f"{text}/t/{quote(tenant, safe='')}?run={quote(uuid, safe='')}"
    return url if len(url) <= MAX_URL_CHARS else None


def _safe_github_url(url: object) -> str | None:
    """A stored GitHub URL that is safe to render as an application-built link.

    Shared by the issue link and the evidence links: only a plain ``https``
    ``github.com`` URL, with no userinfo, explicit port, Slack markup characters,
    whitespace or more than ``MAX_URL_CHARS`` characters, is ever linkable.
    """
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or len(text) > MAX_URL_CHARS:
        return None
    if any(char in text for char in "<>|&") or any(char.isspace() for char in text):
        return None
    try:
        parsed = urlparse(text)
        if parsed.scheme != "https" or parsed.hostname != GITHUB_HOST:
            return None
        if parsed.username or parsed.password or parsed.port is not None:
            return None
    except ValueError:
        return None
    return text


def _issue_url(issue_key: str) -> str | None:
    """The canonical ``https://github.com/<owner>/<name>/issues/<n>`` link, or ``None``.

    Built only from a well-formed canonical issue key; anything else (a malformed
    key, a foreign shape, markup, over-long text) yields no link rather than a
    guess.
    """
    if not isinstance(issue_key, str) or not issue_key.startswith("issue:"):
        return None
    repo, marker, number = issue_key[len("issue:"):].rpartition("#")
    if not marker or not number.isdigit() or int(number) <= 0 or not _REPO.fullmatch(repo):
        return None
    return _safe_github_url(f"https://{GITHUB_HOST}/{repo}/issues/{number}")


def citation_keys(run: RunSnapshot) -> list[str]:
    """The distinct citation keys of the retained claims, bounded and in display order."""
    keys: list[str] = []
    for section, _, _ in _CLAIM_SECTIONS:
        claims = getattr(run.data.triage, section)[:MAX_CLAIMS_PER_CATEGORY]
        for claim in claims:
            for citation in claim.citations:
                key = _ws(citation)
                if key and key not in keys:
                    keys.append(key)
                    if len(keys) >= MAX_EVIDENCE_KEYS:
                        return keys
    return keys


def _identity_block(message: NotificationMessage) -> dict:
    attempt = message.attempt
    repo, number = _issue_parts(attempt.issue_key)
    title = _clip_chars(_ws(attempt.title), MAX_TITLE_CHARS) or "(untitled)"
    lines = [
        f"{repo}#{number} · {CAUSE_COPY.get(attempt.cause, 'Triage')}",
        f"Title: {title}",
        f"Tenant: {attempt.tenant}",
    ]
    if message.current is not None and message.current.replay_of:
        lines.append(f"Replay of: {message.current.replay_of}")
    if attempt.parent is not None:
        lines.append(f"Follow-up to: {attempt.parent.data.run.run_key}")
    return _plain_section(_inert("\n".join(lines)))


def _status_text(message: NotificationMessage) -> str:
    if message.failure_stage is not None:
        text = STAGE_FAILURE_COPY.get(message.failure_stage, "Processing failed.")
    elif message.applied is None:
        if message.attempt.cause == "ui_feedback":
            text = "No GitHub actions were applied by this re-triage."
        else:
            text = "No GitHub action outcome was recorded for this run."
    else:
        text = _apply_summary(message.applied)
    if message.recovered:
        text = f"{RECOVERY_NOTE} {text}"
    return text


def _apply_summary(applied: schemas.ApplyResult) -> str:
    counts: dict[str, int] = {}
    for action in applied.actions:
        counts[action.status] = counts.get(action.status, 0) + 1
    detail = ", ".join(f"{count} {status.replace('_', ' ')}" for status, count in sorted(counts.items()))
    detail = detail or "no actions"
    if applied.dry_run:
        return f"Dry-run preview: {detail}; nothing was written to GitHub."
    return f"GitHub apply outcome: {detail}."


def _summary_block(message: NotificationMessage) -> dict | None:
    if message.current is None:
        return None
    run = message.current.data.run
    summary = _clip_chars(_ws(run.summary), MAX_SUMMARY_CHARS) or "(no summary)"
    prefix = "Fallback (degraded) triage. " if run.fallback else ""
    return _plain_section("Summary: " + _inert(prefix + summary))


def _change_group(message: NotificationMessage) -> list[dict]:
    parent, current = message.attempt.parent, message.current
    if parent is None or current is None:
        return []
    entries = change_entries(parent, current)
    if not entries:
        return []
    shown = [_clip_chars(_ws(entry), MAX_CHANGE_CHARS) for entry in entries[:MAX_CHANGES]]
    return [_plain_section(_inert("Changed since the parent run: " + "; ".join(shown)))]


def _claim_groups(message: NotificationMessage) -> list[list[dict]]:
    if message.current is None:
        return []
    groups: list[list[dict]] = []
    for section, prefix, _ in _CLAIM_SECTIONS:
        claims = getattr(message.current.data.triage, section)
        shown = [_clip_chars(_ws(claim.text), MAX_CLAIM_CHARS) for claim in claims[:MAX_CLAIMS_PER_CATEGORY]]
        shown = [text for text in shown if text]
        if shown:
            groups.append([_plain_section(_inert(prefix + "; ".join(shown)))])
    return groups


def _evidence_group(message: NotificationMessage, evidence_links: dict[str, str]) -> tuple[list[dict], bool]:
    if message.current is None:
        return [], False
    keys = citation_keys(message.current)[:MAX_EVIDENCE_ENTRIES]
    if not keys:
        return [_plain_section("Evidence: none recorded for this run.")], False
    prefix, separator = "Evidence: ", " · "
    budget = MAX_BLOCK_BYTES - len((prefix + separator * (len(keys) - 1)).encode("utf-8"))
    # Reserve every inert citation key before spending remaining bytes on links.
    # Never byte-clip assembled mrkdwn: a URL token must survive whole or not at all.
    plain = [_evidence_entry(key, None) for key in keys]
    entries = [_clip_bytes(entry, budget // len(keys)) for entry in plain]
    truncated = entries != plain
    remaining = budget - sum(len(entry.encode("utf-8")) for entry in entries)
    for index, key in enumerate(keys):
        linked = _evidence_entry(key, evidence_links.get(key))
        extra = len(linked.encode("utf-8")) - len(entries[index].encode("utf-8"))
        if extra <= remaining:
            entries[index] = linked
            remaining -= extra
        elif linked != plain[index]:
            truncated = True
    return [_mrkdwn_section(prefix + separator.join(entries))], truncated


def _evidence_entry(key: str, url: object) -> str:
    display = _mrkdwn_inert(_clip_chars(key, MAX_CITATION_CHARS))
    safe = _safe_github_url(url)
    return display if safe is None else f"<{safe}|{display}>"


def _link_group(message: NotificationMessage, public_base_url: str) -> list[dict]:
    """The core link blocks: the real GitHub issue, the run and its parent.

    Always emitted from trusted identity, never from evidence, so the issue URL and
    the full-run link survive every trim.
    """
    current, parent = message.current, message.attempt.parent
    blocks: list[dict] = []
    issue_url = _issue_url(message.attempt.issue_key)
    if issue_url is not None:
        repo, number = _issue_parts(message.attempt.issue_key)
        blocks.append(_mrkdwn_section(f"Issue: <{issue_url}|{_mrkdwn_inert(f'{repo}#{number}')}>"))
    if current is not None:
        run_key = current.data.run.run_key
        url = _run_url(public_base_url, message.attempt.tenant, run_key)
        if url is None:
            blocks.append(_plain_section(f"Full run: {_clip_chars(run_key, MAX_CITATION_CHARS)}"))
        else:
            blocks.append(_mrkdwn_section(f"Full run: <{url}|Keystone run>"))
    if parent is not None:
        parent_url = _run_url(public_base_url, message.attempt.tenant, parent.data.run.run_key)
        if parent_url is not None:
            blocks.append(_mrkdwn_section(f"Parent run: <{parent_url}|Keystone run>"))
    return blocks


def _fixed_caps_hid_content(message: NotificationMessage) -> bool:
    """Whether a fixed per-category cap actually hides content from the message.

    A hidden claim, change entry or evidence key must never be silent: the caller
    appends the explicit truncation/full-run notice when this is true.
    """
    if message.current is None:
        return False
    triage = message.current.data.triage
    if any(
        len(getattr(triage, section)) > MAX_CLAIMS_PER_CATEGORY for section, _, _ in _CLAIM_SECTIONS
    ):
        return True
    if len(citation_keys(message.current)) > MAX_EVIDENCE_ENTRIES:
        return True
    if message.attempt.parent is not None:
        return len(change_entries(message.attempt.parent, message.current)) > MAX_CHANGES
    return False


def _payload_bytes(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def render_message(
    message: NotificationMessage,
    *,
    public_base_url: str,
    evidence_links: dict[str, str],
) -> dict:
    """Render one attempt into a bounded, inert Slack incoming-webhook payload.

    Pure: it reads only ``message``, ``public_base_url`` and ``evidence_links``.
    Optional evidence, claim and change blocks are dropped (evidence first, then
    claims, then changes) before the core identity, status, issue link and run
    link, and an explicit truncation notice is appended whenever anything was
    dropped — or was hidden by a fixed per-category cap, or by a block byte cap.
    """
    head = [_identity_block(message), _plain_section(_status_text(message))]
    summary = _summary_block(message)
    if summary is not None:
        head.append(summary)

    evidence, evidence_truncated = _evidence_group(message, evidence_links)
    optional: list[list[dict]] = [
        evidence,
        *_claim_groups(message),
        _change_group(message),
    ]
    optional = [group for group in optional if group]
    tail = _link_group(message, public_base_url)
    capped = _fixed_caps_hid_content(message) or evidence_truncated

    payload: dict = {}
    for dropped in range(len(optional) + 1):
        blocks: list[dict] = []
        clipped = False
        for block in [*head, *[b for group in optional[dropped:] for b in group], *tail]:
            finalized, was_clipped = _finalize_copy(block)
            blocks.append(finalized)
            clipped = clipped or was_clipped
        if dropped or clipped or capped:
            blocks.append(_finalize_copy(_mrkdwn_section(TRUNCATION_NOTICE))[0])
        payload = {
            "text": FALLBACK_TEXT,
            "parse": "none",
            "link_names": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "blocks": blocks,
        }
        if _payload_bytes(payload) <= MAX_PAYLOAD_BYTES:
            return payload
    return payload
