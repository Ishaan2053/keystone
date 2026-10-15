from __future__ import annotations

import time

from api.embeddings import encode, issue_text
from api.schemas import ToolResult
from api.tools._run import now, result, rows


def affected_modules(
    tenant: str,
    title: str,
    body: str = "",
    exclude_issues: list[str] | None = None,
    before: int | None = None,
) -> ToolResult:
    """The top 3 modules an issue likely touches, from merged PRs that fixed the most similar past
    issues, with those issues and the labels used on them."""
    started = time.perf_counter()
    params = {
        "q": encode([issue_text(title, body)])[0],
        "exclude_issues": exclude_issues or [],
        "before": before if before is not None else now(),
    }
    found, text = rows(tenant, "affected_modules", params)
    evidence = []
    for m in found:
        evidence += [m["module"], *m["prs"], *m["similar"], *(l["key"] for l in m["labels"])]
    return result("affected_modules", tenant, started, {"modules": found}, [text], params, evidence, not found)
