"""Immutable Slack-notification messages shared by the lineage store and coordinator.

Task 2 creates the shared contracts: the ``Cause`` of an attempt, an immutable
``RunSnapshot`` of one persisted run (the store deep-copies the recalled data so a
later feedback/action mutation cannot change a reserved parent), and the attempt
and message models the coordinator passes to the renderer. The renderer and
coordinator land in later tasks; nothing here calls Slack.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from api import schemas


# Why an eligible attempt exists; a parentless root is always an issue event.
Cause = Literal["issue_opened", "issue_edited", "github_feedback", "ui_feedback"]
FailureStage = Literal["ingest", "triage", "apply"]


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
