"""Decisions job (spec section 4): one LLM call per source, kept only when its quote is verbatim.

Sources are the merged PRs with the most comments and every Doc whose path contains adr, rfc,
design, decision or plan. Each call returns decisions as {summary, rationale, quote, about_paths}.
A decision is kept only if its quote appears in one field of the source (the PR title, the PR body,
one comment, or the doc text) after collapsing whitespace and ignoring * and ` marks; that is the guard against invented
decisions. about_paths map to existing Module keys; the rest are dropped. Kept decisions are
written with MADE_IN, ABOUT and a local vecf32 embedding. Responses are cached (api/llm.py), so a
re-run or a run after a trial costs only the calls not made yet.

    python -m ingest.decisions --tenant falkordb --prs 10 --no-docs   # trial
    python -m ingest.decisions --tenant falkordb                      # full run
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time

import yaml

from api.cypher import query
from api.db import graph as tenant_graph
from api.embeddings import encode
from api.llm import generate_json
from ingest.fetch import ROOT, load_env
from ingest.load import module_path

DOC_WORDS = ["adr", "rfc", "design", "decision", "plan"]
SOURCE_CHARS = 30000  # text sent per source; the quote check runs on exactly what was sent

SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "rationale": {"type": "string"},
                    "quote": {"type": "string"},
                    "about_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "rationale", "quote", "about_paths"],
            },
        }
    },
    "required": ["decisions"],
}

SYSTEM = """You extract engineering decisions from one GitHub pull request or design document.
A decision is a choice the maintainers made about design, behaviour, an API, a dependency or process, together with the reason for it.
Return at most 3 decisions. Return an empty list when the source records none; most bug fixes and routine changes record none.
For each decision:
- summary: one sentence stating what was decided.
- rationale: one sentence giving the reason, as the source states it.
- quote: one or two sentences copied exactly, character for character, from ONE section of the source (the title, the body, a single comment, or the document) that show the decision. Do not paraphrase, shorten with ellipses, fix typos or combine sections.
- about_paths: repository paths the decision is about, from the listed module paths or paths named in the source. Use an empty list if none apply.
Use only the source. Do not invent decisions."""


def norm(text: str) -> str:
    """Markdown emphasis and code marks (* and `) removed, whitespace runs collapsed to one space;
    nothing else changes. The model often drops those marks while copying the words exactly."""
    return " ".join(text.replace("*", "").replace("`", "").split())


def quote_found(quote: str, fields: list[str]) -> bool:
    """The quote check: non-empty, and verbatim in one field after `norm` on both sides."""
    q = norm(quote)
    return bool(q) and any(q in norm(f) for f in fields)


def clip(fields: list[tuple[str, str]], budget: int = SOURCE_CHARS) -> list[tuple[str, str]]:
    """(heading, text) fields in order, cut so their text totals at most `budget` characters."""
    out = []
    for heading, text in fields:
        if budget <= 0:
            break
        out.append((heading, text[:budget]))
        budget -= len(out[-1][1])
    return out


def pr_source(row: dict) -> dict:
    repo = row["key"][len("pr:"):].split("#")[0]
    fields = clip([("TITLE", row["title"]), ("BODY", row["body"] or "")]
                  + [(f"COMMENT {i} by {c['login']}", c["body"] or "") for i, c in enumerate(row["comments"], 1)])
    head = (f"Repository: {repo}\nMerged pull request {row['key']}\n"
            f"Module paths it touches: {', '.join(row['modules']) or 'none listed'}")
    return {"key": row["key"], "url": row["url"], "repo": repo, "head": head, "fields": fields}


def doc_source(row: dict) -> dict:
    repo = "/".join(row["key"][len("doc:"):].split("/")[:2])
    fields = clip([("DOCUMENT", "\n\n".join(row["chunks"]))])
    head = (f"Repository: {repo}\nDocument {row['path']}: {row['title']}\n"
            f"Module it describes: {', '.join(row['modules']) or 'none listed'}")
    return {"key": row["key"], "url": row["url"], "repo": repo, "head": head, "fields": fields}


def prompt(source: dict) -> str:
    return source["head"] + "".join(f"\n\n=== {h} ===\n{t}" for h, t in source["fields"])


def module_keys(graph, repo: str, paths: list[str]) -> dict[str, str]:
    """about_path -> Module key, for paths that name an existing module directly or contain a file in one.
    A single word that is not a module is dropped, not mapped to (root) by the module rule."""
    candidates = {}
    for path in paths:
        p = path.strip().removeprefix("./").strip("/")
        candidates[path] = [f"module:{repo}/{p}"] + ([f"module:{repo}/{module_path(p)}"] if "/" in p else []) if p else []
    keys = sorted({k for ks in candidates.values() for k in ks})
    found = {r[0] for r in graph.ro_query(query("module_keys"), {"keys": keys}).result_set} if keys else set()
    return {path: next(k for k in ks if k in found) for path, ks in candidates.items() if any(k in found for k in ks)}


def process(graph, source: dict) -> tuple[list[dict], list[tuple[dict, str]], str]:
    """(kept rows to write, (decision, reason) dropped, model) for one source.

    Provider and module lookup errors propagate to the per-source CLI failure boundary.
    """
    answer, model = generate_json(prompt(source), SCHEMA, SYSTEM)
    texts = [t for _, t in source["fields"]]
    kept, dropped = [], []
    for raw in (v if isinstance(v := answer.get("decisions"), list) else []):
        if not isinstance(raw, dict):  # main prints summary and quote, so the item's text goes in the quote
            dropped.append(({"summary": "", "quote": repr(raw)}, "malformed decision"))
            continue
        # A field of the wrong type reads as empty, so the checks below drop it instead of raising.
        summary, rationale, quote = (v if isinstance(v := raw.get(f), str) else ""
                                     for f in ("summary", "rationale", "quote"))
        paths = raw.get("about_paths") if isinstance(raw.get("about_paths"), list) else []
        paths = [p for p in paths if isinstance(p, str)]
        d = {"summary": summary, "rationale": rationale, "quote": quote, "about_paths": paths}
        if not norm(summary):
            dropped.append((d, "empty summary"))
            continue
        if not quote_found(quote, texts):
            dropped.append((d, "quote not found verbatim in any one field"))
            continue
        key = "decision:" + hashlib.sha1((summary + source["key"]).encode("utf-8")).hexdigest()
        if any(k["key"] == key for k in kept):  # same key, one node: a second row would undo the first's ABOUT edges
            dropped.append((d, "duplicate summary"))
            continue
        mapped = module_keys(graph, source["repo"], paths)
        kept.append({
            "key": key, "source": source["key"], "summary": summary, "rationale": rationale,
            "quote": quote, "url": source["url"], "modules": sorted(set(mapped.values())),
            "unmatched": [p for p in paths if p not in mapped],
        })
    return kept, dropped, model


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Extract Decisions from PRs and design docs into tenant_<slug>")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--prs", type=int, default=150, help="merged PRs with the most comments (default 150)")
    ap.add_argument("--docs", action=argparse.BooleanOptionalAction, default=True,
                    help="include Docs whose path contains " + ", ".join(DOC_WORDS))
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # source text may hold characters the console can't show

    load_env()
    config = yaml.safe_load((ROOT / "ingest" / "tenants" / f"{args.tenant}.yml").read_text(encoding="utf-8"))
    graph = tenant_graph(config["slug"])
    started = time.time()
    sources = [pr_source(r) for r in _rows(graph, "decision_sources", {"limit": args.prs})]
    if args.docs:
        sources += [doc_source(r) for r in _rows(graph, "decision_docs", {"words": DOC_WORDS})]
    print(f"{len(sources)} sources")

    totals = {"kept": 0, "dropped": 0, "failed": 0}
    first_kept = None
    for n, source in enumerate(sources, 1):
        stage = "extraction"
        try:
            kept, dropped, model = process(graph, source)
            totals["dropped"] += len(dropped)
            if kept:
                stage = "embedding"
                vectors = encode([f"{d['summary']}\n{d['rationale']}" for d in kept])
                rows = [{k: v for k, v in d.items() if k != "unmatched"} | {"vec": vec} for d, vec in zip(kept, vectors)]
                stage = "graph write"
                graph.query(query("write_decisions"), {"rows": rows})
        except Exception:
            # Boundary exceptions may contain credentials or raw provider/graph payloads.
            # Catch Exception, not BaseException: cancellation and explicit exits still propagate.
            totals["failed"] += 1
            print(f"[{n}/{len(sources)}] {source['key']}: {stage} FAILED")
            continue
        totals["kept"] += len(kept)
        if kept:
            first_kept = first_kept or kept[0]
        print(f"[{n}/{len(sources)}] {source['key']} ({model}): {len(kept)} kept, {len(dropped)} dropped")
        for d in kept:
            print(f"  KEPT {d['key']}\n    summary: {d['summary']}\n    quote:   {d['quote'][:200]!r}")
            print(f"    about:   {d['modules'] or 'no module'}"
                  + (f"  (unmatched paths dropped: {d['unmatched']})" if d["unmatched"] else ""))
        for d, reason in dropped:
            print(f"  DROPPED ({reason})\n    summary: {d.get('summary', '')}\n    quote:   {d.get('quote', '')[:200]!r}")

    print(f"kept {totals['kept']}, dropped {totals['dropped']}, failed sources {totals['failed']} "
          f"in {time.time() - started:.0f}s")
    verification_failed = False
    if first_kept:  # Done-when: a vector query on a successfully written summary returns that decision
        try:
            from api.tools.find_decisions import find_decisions

            res = find_decisions(config["slug"], first_kept["summary"])
            if not any(d["decision"] == first_kept["key"] for d in res.data["decisions"]):
                raise RuntimeError("written decision absent from vector results")
            print(f"vector check with the summary of {first_kept['key']}:")
            for d in res.data["decisions"]:
                mark = "  <- this one" if d["decision"] == first_kept["key"] else ""
                print(f"  {d['score']:8.4f}  {d['decision']}  {d['summary'][:70]}{mark}")
        except Exception:
            verification_failed = True
            print("vector verification FAILED")
    if totals["failed"] or verification_failed:
        raise SystemExit(1)


def _rows(graph, name: str, params: dict) -> list[dict]:
    res = graph.ro_query(query(name), params)
    cols = [h[1] for h in res.header]
    return [dict(zip(cols, r)) for r in res.result_set]


if __name__ == "__main__":
    main()
