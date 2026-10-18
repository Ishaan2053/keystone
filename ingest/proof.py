"""Proof (spec section 14): Keystone against two baselines on held-out history.

    python -m ingest.proof --tenant falkordb

Takes up to 50 closed Issues with a merged, non-sweep fixing PR reviewed by someone other than the
Issue's author (`backtest_sample.cypher`) and asks each method, as of the Issue's created_at, for 3
reviewers (never the author), 3 modules and 2 labels. Truth is those fixing PRs' reviewers other than
the author and their touched modules, and the Issue's labels. Every key a method returns
as evidence is checked against `before` (`proof_evidence_times.cypher`); a leak stops the run.
Methods: graph (Keystone's tools), codeowners and similar_issues (the `baseline_*.cypher` queries).
The result is validated against `api.schemas.Proof` before it is written to data/proof/<tenant>.json.

Metrics, per Issue: reviewer hit@3 and module hit@1/@3 are 1 if any of the top k is in the truth;
label precision@2 is |top 2 & truth| / 2, and is None (left out of the rate) for unlabelled Issues.
"""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any

from api.embeddings import encode, issue_text
from api.schemas import Proof
from api.tools import affected_modules, rank_reviewers
from api.tools._run import DAY, rows
from ingest.fetch import ROOT, load_env

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
    repo = (
        issue["issue"].split(":", 1)[1].rsplit("#", 1)[0]
    )  # the issue's own repo, as the agent searches it
    am = affected_modules(
        tenant,
        issue["title"],
        issue["body"] or "",
        exclude_issues=[issue["issue"]],
        before=before,
        repo=repo,
    )
    modules = am.data["modules"]
    evidence = list(am.evidence)
    reviewers: list[str] = []
    if modules:
        rr = rank_reviewers(
            tenant, modules[0]["module"], k=3, before=before, exclude_logins=issue["exclude"]
        )
        reviewers = [p["login"] for p in rr.data["reviewers"]]
        evidence += rr.evidence
    weight: Counter[str] = Counter()
    for m in modules:
        for label in m["labels"]:
            weight[label["name"]] += m["evidence"]
    labels = [name for name, _ in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {
        "reviewers": reviewers,
        "modules": [m["module"] for m in modules],
        "labels": labels,
    }, evidence


def codeowners_method(tenant: str, issue: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Baseline 1: owners of the module the fix touched most, else the repo's top reviewers; the
    repo's 3 most-changed modules; its 2 most-used Issue labels. All as of `before`."""
    repo, before = issue["issue"].split(":", 1)[1].split("#")[0], issue["before"]
    (who,), _ = rows(
        tenant,
        "baseline_codeowners_reviewers",
        {
            "module": issue["modules"][0],
            "prs": f"pr:{repo}#",
            "before": before,
            "exclude": issue["exclude"],
        },
    )
    modules, _ = rows(tenant, "baseline_top_modules", {"repo": f"repo:{repo}", "before": before})
    labels, _ = rows(tenant, "baseline_top_labels", {"issues": f"issue:{repo}#", "before": before})
    evidence = [k for p in who["reviewers"] for k in (p["key"], *p["via"])]
    evidence += [k for m in modules for k in (m["module"], *m["newest"])]
    evidence += [k for entry in labels for k in (entry["key"], *entry["newest"])]
    pred = {
        "reviewers": [p["login"] for p in who["reviewers"]],
        "reviewer_source": who["source"],
        "modules": [m["module"] for m in modules],
        "labels": [entry["label"] for entry in labels],
    }
    return pred, evidence


def similar_method(tenant: str, issue: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    """Baseline 2, what a vector-only RAG agent would do with the same embeddings: people on the fixes
    of the 10 nearest earlier Issues, the modules of the nearest one's fix, labels of the 3 nearest."""
    common = {
        "q": encode([issue_text(issue["title"], issue["body"])])[0],
        "exclude_issues": [issue["issue"]],
        "before": issue["before"],
    }
    people, _ = rows(tenant, "baseline_similar_reviewers", {**common, "exclude": issue["exclude"]})
    modules, _ = rows(tenant, "baseline_similar_modules", common)
    labels, _ = rows(tenant, "baseline_similar_labels", common)
    evidence = [k for p in people for k in (p["person"], *p["via"], *p["similar"])]
    evidence += [k for m in modules for k in (m["module"], m["similar"], *m["prs"])]
    evidence += [k for entry in labels for k in (entry["key"], *entry["similar"])]
    pred = {
        "reviewers": [p["login"] for p in people],
        "modules": [m["module"] for m in modules],
        "labels": [entry["label"] for entry in labels],
    }
    return pred, evidence


METHODS = {"graph": graph_method, "codeowners": codeowners_method, "similar_issues": similar_method}


def check_no_leak(tenant: str, method: str, issue: dict[str, Any], evidence: list[str]) -> None:
    late, _ = rows(
        tenant,
        "proof_evidence_times",
        {"keys": list(dict.fromkeys(evidence)), "before": issue["before"]},
    )
    if late:
        raise SystemExit(
            f"{method} saw the future for {issue['issue']} (before={issue['before']}): {late}"
        )


def score(pred: dict[str, list[str]], issue: dict[str, Any]) -> dict[str, float | None]:
    reviewers, modules, labels = (
        set(issue["reviewers"]),
        set(issue["modules"]),
        set(issue["labels"]),
    )
    return {
        "reviewer_hit_at_3": float(any(r in reviewers for r in pred["reviewers"][:3])),
        "module_hit_at_1": float(pred["modules"][:1] != [] and pred["modules"][0] in modules),
        "module_hit_at_3": float(any(m in modules for m in pred["modules"][:3])),
        "label_precision_at_2": len(set(pred["labels"][:2]) & labels) / 2 if labels else None,
    }


def rates(rows_: list[dict[str, Any]], method: str) -> dict[str, float]:
    out = {}
    for metric in METRICS:
        values = [
            r[method]["scores"][metric] for r in rows_ if r[method]["scores"][metric] is not None
        ]
        out[metric] = round(sum(values) / len(values), 4) if values else 0.0
    return out


def outcome(row: dict[str, Any]) -> str:
    """win / loss / tie: the graph's summed scores against the better baseline's."""
    total = {m: sum(v or 0 for v in row[m]["scores"].values()) for m in METHODS}
    best = max(total["codeowners"], total["similar_issues"])
    return "win" if total["graph"] > best else "loss" if total["graph"] < best else "tie"


def run(tenant: str) -> Proof:
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
        row["outcome"] = outcome(row)
        out_rows.append(row)
    return Proof.model_validate(
        {"n": len(out_rows), "methods": {m: rates(out_rows, m) for m in METHODS}, "rows": out_rows}
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True)
    args = ap.parse_args(argv)
    load_env()
    proof = run(args.tenant)
    out = ROOT / "data" / "proof" / f"{args.tenant}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(proof.model_dump_json(indent=2), encoding="utf-8")
    labelled = sum(1 for r in proof.rows if r["truth"]["labels"])
    outcomes = Counter(r["outcome"] for r in proof.rows)
    print(f"n={proof.n} (labelled: {labelled}) -> {out.relative_to(ROOT)}")
    print(f"{'method':<16}" + "".join(f"{m:>22}" for m in METRICS))
    for method, r in proof.methods.items():
        print(f"{method:<16}" + "".join(f"{getattr(r, m):>22.2f}" for m in METRICS))
    print(
        "graph vs best baseline:", ", ".join(f"{k} {outcomes[k]}" for k in ("win", "tie", "loss"))
    )


if __name__ == "__main__":
    main()
