"""Proof (spec section 14): Keystone against two baselines on held-out history.

    python -m ingest.proof --tenant falkordb

Takes up to 50 closed Issues with a merged, non-sweep fixing PR reviewed by someone other than the
Issue's author (`backtest_sample.cypher`) and asks each method, as of the Issue's created_at, for 3
reviewers (never the author), 3 modules and 2 labels. Truth is those fixing PRs' reviewers other than
the author and their touched modules, and the Issue's labels. Every key a method returns
as evidence is checked against `before` (`proof_evidence_times.cypher`); a leak stops the run.

Metrics, per Issue: reviewer hit@3 and module hit@1/@3 are 1 if any of the top k is in the truth;
label precision@2 is |top 2 & truth| / 2, and is None (left out of the rate) for unlabelled Issues.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from api.tools import affected_modules, rank_reviewers
from api.tools._run import DAY, rows
from ingest.fetch import load_env

SAMPLE = 50
WARMUP = 90 * DAY  # skip Issues from the first 90 days of PR history, when no method has any
METRICS = ("reviewer_hit_at_3", "module_hit_at_1", "module_hit_at_3", "label_precision_at_2")


def sample(tenant: str) -> list[dict[str, Any]]:
    found, _ = rows(tenant, "backtest_sample", {"n": SAMPLE, "warmup": WARMUP})
    return found


def graph_method(tenant: str, issue: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    """Keystone: affected_modules, then rank_reviewers on its top module. Labels are those of the
    similar past issues behind each module, each weighted by that module's evidence count."""
    before = issue["before"]
    am = affected_modules(tenant, issue["title"], issue["body"] or "", exclude_issues=[issue["issue"]], before=before)
    modules = am.data["modules"]
    evidence = list(am.evidence)
    reviewers: list[str] = []
    if modules:
        rr = rank_reviewers(tenant, modules[0]["module"], k=3, before=before, exclude_logins=issue["exclude"])
        reviewers = [p["login"] for p in rr.data["reviewers"]]
        evidence += rr.evidence
    weight: Counter[str] = Counter()
    for m in modules:
        for label in m["labels"]:
            weight[label["name"]] += m["evidence"]
    labels = [name for name, _ in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"reviewers": reviewers, "modules": [m["module"] for m in modules], "labels": labels}, evidence


METHODS = {"graph": graph_method}


def check_no_leak(tenant: str, method: str, issue: dict[str, Any], evidence: list[str]) -> None:
    late, _ = rows(tenant, "proof_evidence_times", {"keys": list(dict.fromkeys(evidence)), "before": issue["before"]})
    if late:
        raise SystemExit(f"{method} saw the future for {issue['issue']} (before={issue['before']}): {late}")


def score(pred: dict[str, list[str]], issue: dict[str, Any]) -> dict[str, float | None]:
    reviewers, modules, labels = set(issue["reviewers"]), set(issue["modules"]), set(issue["labels"])
    return {
        "reviewer_hit_at_3": float(any(r in reviewers for r in pred["reviewers"][:3])),
        "module_hit_at_1": float(pred["modules"][:1] != [] and pred["modules"][0] in modules),
        "module_hit_at_3": float(any(m in modules for m in pred["modules"][:3])),
        "label_precision_at_2": len(set(pred["labels"][:2]) & labels) / 2 if labels else None,
    }


def rates(rows_: list[dict[str, Any]], method: str) -> dict[str, float]:
    out = {}
    for metric in METRICS:
        values = [r[method]["scores"][metric] for r in rows_ if r[method]["scores"][metric] is not None]
        out[metric] = round(sum(values) / len(values), 4) if values else 0.0
    return out


def run(tenant: str) -> dict[str, Any]:
    out_rows = []
    for issue in sample(tenant):
        row: dict[str, Any] = {
            "issue": issue["issue"],
            "url": issue["url"],
            "title": issue["title"],
            "before": issue["before"],
            "truth": {k: issue[k] for k in ("fixes", "reviewers", "modules", "labels")},
        }
        for method, fn in METHODS.items():
            pred, evidence = fn(tenant, issue)
            check_no_leak(tenant, method, issue, evidence)
            row[method] = {**pred, "scores": score(pred, issue)}
        out_rows.append(row)
    return {"n": len(out_rows), "methods": {m: rates(out_rows, m) for m in METHODS}, "rows": out_rows}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True)
    args = ap.parse_args(argv)
    load_env()
    proof = run(args.tenant)
    labelled = sum(1 for r in proof["rows"] if r["truth"]["labels"])
    print(f"n={proof['n']} (labelled: {labelled})")
    print(json.dumps(proof["methods"], indent=2))


if __name__ == "__main__":
    main()
