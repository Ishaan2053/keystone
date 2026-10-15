"""Load data/raw/<tenant>/ into the FalkorDB graph tenant_<slug> (spec sections 3 and 4).

Every node is MERGEd by `key` with its kind label plus `Node`, and properties are
set with `+=`, so a re-run updates in place and keeps what later jobs added
(A3's scores, A4's embeddings, A7's decisions).

Bots (GitHub user type "Bot": dependabot, Copilot, coderabbitai, ...) are not
people here: no Person, AUTHORED, REVIEWED, REVIEW_REQUESTED or WROTE for them,
and their comments are neither loaded nor counted. Their PRs still load. About
half of all reviews come from review bots, which would otherwise top every
reviewer ranking.

    python -m ingest.load --tenant falkordb
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml
from falkordb import FalkorDB

from api.cypher import query, statements
from ingest.fetch import ROOT, load_env

BATCH = 2000
MODULE_ROOTS = {"src", "lib", "packages", "pkg"}
REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
# GitHub's closing keywords, followed by #n, owner/repo#n or an issue URL.
FIXES = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+"
    r"(?:(?P<repo>[\w.-]+/[\w.-]+)#(?P<n1>\d+)|#(?P<n2>\d+)"
    r"|https://github\.com/(?P<urepo>[\w.-]+/[\w.-]+)/issues/(?P<n3>\d+))",
    re.IGNORECASE,
)
LANGUAGES = {
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".rs": "Rust",
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".go": "Go", ".java": "Java", ".sh": "Shell",
    ".md": "Markdown", ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".toml": "TOML",
    ".css": "CSS", ".html": "HTML", ".cmake": "CMake", ".feature": "Gherkin",
}
NAMED_FILES = {"Makefile": "Make", "Dockerfile": "Dockerfile", "CMakeLists.txt": "CMake"}


def ts(value: str | None) -> int | None:
    """ISO 8601 from GitHub -> epoch seconds."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()) if value else None


def module_path(path: str) -> str:
    """First path segment, or the first two under src/lib/packages/pkg; root files -> (root)."""
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] in MODULE_ROOTS and len(parts) > 2:  # src/foo.c stays in module src
        return "/".join(parts[:2])
    return parts[0]


def language(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    return NAMED_FILES.get(name) or LANGUAGES.get(Path(name).suffix.lower())


def is_human(user: dict | None) -> bool:
    return bool(user) and user.get("type") != "Bot"


class Rows:
    """Nodes by label and edges by (type, from label, to label), deduplicated by key."""

    def __init__(self):
        self.nodes: dict[str, dict[str, dict]] = defaultdict(dict)
        self.edges: dict[tuple[str, str, str], dict[tuple[str, str], dict]] = defaultdict(dict)

    def node(self, label: str, key: str, **props) -> str:
        self.nodes[label].setdefault(key, {}).update(props)
        return key

    def edge(self, rel: str, a_label: str, a: str, b_label: str, b: str, **props) -> None:
        self.edges[(rel, a_label, b_label)][(a, b)] = props

    def person(self, user: dict) -> str:
        login = user["login"]
        return self.node("Person", f"person:{login}", login=login, name=login, url=user["html_url"])


def parse_repo(rows: Rows, d: Path, org: str, max_pr_files: int) -> dict:
    """Add one repo's raw JSON to `rows`; return what the summary prints."""
    def load(name: str):
        return json.loads((d / f"{name}.json").read_text(encoding="utf-8"))

    meta = load("repo")
    repo = meta["full_name"]
    repo_key = rows.node("Repo", f"repo:{repo}", name=repo, url=meta["html_url"])
    skipped: Counter = Counter()

    def label(l: dict) -> str:
        return rows.node("Label", f"label:{repo}/{l['name']}", name=l["name"], color=l.get("color"))

    for l in load("labels"):
        label(l)

    prs = load("prs")
    comments = load("issue_comments") + load("review_comments")
    # Issue and PR numbers share one sequence per repo, so one counter covers both.
    comment_count = Counter(
        int((c.get("pull_request_url") or c["issue_url"]).rsplit("/", 1)[1]) for c in comments if is_human(c["user"])
    )

    # Sweeps (spec section 4): too many files, or the head is a same-repo branch 3+ PRs merge into.
    base_use = Counter(p["base"]["ref"] for p in prs)
    sweeps = Counter()
    for p in prs:
        key = f"pr:{repo}#{p['number']}"
        head_repo = (p["head"].get("repo") or {}).get("full_name")
        by_size = len(p["files"]) > max_pr_files
        by_branch = head_repo == repo and base_use[p["head"]["ref"]] >= 3
        sweeps["by size"] += by_size
        sweeps["by branch only"] += by_branch and not by_size
        rows.node(
            "PR", key, number=p["number"], title=p["title"], body=p["body"] or "",
            state="merged" if p["merged_at"] else p["state"], created_at=ts(p["created_at"]),
            merged_at=ts(p["merged_at"]), url=p["html_url"], comment_count=comment_count[p["number"]],
            sweep=by_size or by_branch,
        )
        author = p["user"]
        if is_human(author):
            rows.edge("AUTHORED", "Person", rows.person(author), "PR", key)
        else:
            skipped["bot-authored PRs (no AUTHORED)"] += 1
        for l in p["labels"]:
            rows.edge("HAS_LABEL", "PR", key, "Label", label(l))

        # One REVIEWED per person: their latest review, not counting the author's own replies.
        latest = {}
        for r in sorted(p["reviews"], key=lambda r: r.get("submitted_at") or ""):
            if not is_human(r["user"]):
                skipped["bot reviews"] += 1
            elif r["user"]["login"] != (author or {}).get("login") and r["state"] in REVIEW_STATES:
                latest[r["user"]["login"]] = (r["user"], r["state"])
        for user, state in latest.values():
            rows.edge("REVIEWED", "Person", rows.person(user), "PR", key, state=state)

        if p["state"] == "open":
            for user in p["requested_reviewers"]:
                if is_human(user):
                    rows.edge("REVIEW_REQUESTED", "PR", key, "Person", rows.person(user))

        for m in FIXES.finditer(p["body"] or ""):
            target = m["repo"] or m["urepo"] or repo
            rows.edge("FIXES", "PR", key, "Issue", f"issue:{target}#{m['n1'] or m['n2'] or m['n3']}")

        if by_size or by_branch:  # a sweep keeps every edge except TOUCHES
            continue
        for f in p["files"]:
            path = f["filename"]
            mpath = module_path(path)
            file_key = rows.node("File", f"file:{repo}/{path}", path=path, language=language(path))
            module_key = rows.node("Module", f"module:{repo}/{mpath}", path=mpath, repo=repo)
            rows.edge("TOUCHES", "PR", key, "File", file_key)
            rows.edge("IN", "File", file_key, "Module", module_key)
            rows.edge("IN", "Module", module_key, "Repo", repo_key)

    for i in load("issues"):
        key = f"issue:{repo}#{i['number']}"
        rows.node(
            "Issue", key, number=i["number"], title=i["title"], body=i["body"] or "", state=i["state"],
            created_at=ts(i["created_at"]), closed_at=ts(i["closed_at"]), url=i["html_url"], incoming=False,
        )
        if is_human(i["user"]):
            rows.edge("AUTHORED", "Person", rows.person(i["user"]), "Issue", key)
        for l in i["labels"]:
            rows.edge("HAS_LABEL", "Issue", key, "Label", label(l))

    for c in comments:
        if not is_human(c["user"]):
            skipped["bot comments"] += 1
            continue
        n = (c.get("pull_request_url") or c["issue_url"]).rsplit("/", 1)[1]
        target, target_label = f"pr:{repo}#{n}", "PR"
        if target not in rows.nodes["PR"]:
            target, target_label = f"issue:{repo}#{n}", "Issue"
        if target not in rows.nodes[target_label]:
            skipped["comments on PRs or issues outside the window"] += 1
            continue
        key = rows.node("Comment", f"comment:{c['id']}", body=c["body"] or "",
                        created_at=ts(c["created_at"]), url=c["html_url"])
        rows.edge("ON", "Comment", key, target_label, target)
        rows.edge("WROTE", "Person", rows.person(c["user"]), "Comment", key)

    # CODEOWNERS: a directory pattern owns the modules it equals or contains; file and
    # wildcard patterns own nothing. Email owners are never read (Rule 6).
    modules = [k.split("/", 2)[2] for k in rows.nodes["Module"] if k.startswith(f"module:{repo}/")]
    owners_text = load("codeowners").get("text") or ""
    rules = []
    for line in owners_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        p = pattern.strip("/")
        owned = [] if re.search(r"[*?\[]", pattern) else [m for m in modules if m == p or m.startswith(p + "/")]
        handles = [o[1:] for o in owners if o.startswith("@")]
        for handle in handles:
            if "/" in handle:  # @org/team: membership isn't public, so no MEMBER_OF
                team_key = rows.node("Team", f"team:{handle}", name=handle, inferred=False)
            else:  # @user: a one-person team
                team_key = rows.node("Team", f"team:{org}/{handle}", name=handle, inferred=False)
                user = {"login": handle, "html_url": f"https://github.com/{handle}"}
                rows.edge("MEMBER_OF", "Person", rows.person(user), "Team", team_key)
            for m in owned:
                rows.edge("OWNS", "Team", team_key, "Module", f"module:{repo}/{m}")
        rules.append(f"{pattern} {' '.join('@' + h for h in handles)} -> {', '.join(owned) or 'nothing'}")

    for doc in load("docs"):
        path = doc["path"]
        text = doc.get("text") or ""
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), path.rsplit("/", 1)[-1])
        doc_key = rows.node("Doc", f"doc:{repo}/{path}", path=path, title=title, url=doc["url"])
        directory = path.rsplit("/", 1)[0] if "/" in path else "(root)"
        if f"module:{repo}/{directory}" in rows.nodes["Module"]:
            rows.edge("DESCRIBES", "Doc", doc_key, "Module", f"module:{repo}/{directory}")

    return {"repo": repo, "prs": len(prs), "sweeps": sweeps, "skipped": skipped, "codeowners": rules}


def batches(items: list, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def write(graph, rows: Rows) -> Counter:
    """MERGE every node, then every edge whose endpoints were loaded; return edges dropped per type."""
    for label, items in rows.nodes.items():
        q = f"UNWIND $rows AS r MERGE (n:{label}:Node {{key: r.key}}) SET n += r.p"
        for chunk in batches([{"key": k, "p": p} for k, p in items.items()]):
            graph.query(q, {"rows": chunk})
    dropped: Counter = Counter()
    for (rel, a, b), items in rows.edges.items():
        keep = [{"a": x, "b": y, "p": p} for (x, y), p in items.items() if x in rows.nodes[a] and y in rows.nodes[b]]
        dropped[rel] += len(items) - len(keep)
        q = (f"UNWIND $rows AS r MATCH (a:{a} {{key: r.a}}) MATCH (b:{b} {{key: r.b}}) "
             f"MERGE (a)-[e:{rel}]->(b) SET e += r.p")
        for chunk in batches(keep):
            graph.query(q, {"rows": chunk})
    return dropped


def create_indexes(graph) -> int:
    created = 0
    for s in statements("indexes"):
        try:
            created += int(graph.query(s).indices_created)
        except Exception as e:  # redis ResponseError; an index that exists is fine
            if "already indexed" not in str(e):
                raise
    return created


def connect(slug: str):
    db = FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )
    return db.select_graph(f"tenant_{slug}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Load data/raw/<tenant>/ into the graph tenant_<slug>")
    ap.add_argument("--tenant", required=True)
    args = ap.parse_args(argv)

    load_env()
    config = yaml.safe_load((ROOT / "ingest" / "tenants" / f"{args.tenant}.yml").read_text(encoding="utf-8"))
    raw = ROOT / "data" / "raw" / args.tenant
    graph = connect(config["slug"])
    started = time.time()

    print(f"indexes created: {create_indexes(graph)}")
    rows = Rows()
    summaries = [parse_repo(rows, raw / repo.replace("/", "__"), config["org"], config["max_pr_files"])
                 for repo in config["repos"]]
    for s in statements("load_reset"):
        graph.query(s)
    dropped = write(graph, rows)
    for s in statements("load_cleanup"):
        graph.query(s)

    for s in summaries:
        sw = s["sweeps"]
        print(f"{s['repo']}: {s['prs']} PRs, {sw['by size'] + sw['by branch only']} sweeps "
              f"({sw['by size']} by size, {sw['by branch only']} by branch)")
        for what, n in sorted(s["skipped"].items()):
            print(f"  skipped {n} {what}")
        for rule in s["codeowners"]:
            print(f"  CODEOWNERS {rule}")
    if any(dropped.values()):
        print("edges dropped (endpoint not in the data): "
              + ", ".join(f"{rel} {n}" for rel, n in sorted(dropped.items()) if n))

    counts = {"node": {}, "edge": {}}
    for kind, name, n in graph.query(query("stats")).result_set:
        counts[kind][name] = n
    for kind in ("node", "edge"):
        print(f"{kind}s:")
        for name, n in sorted(counts[kind].items()):
            print(f"  {name:<18}{n:>8}")
    print(f"sweeps: {sum(s['sweeps']['by size'] + s['sweeps']['by branch only'] for s in summaries)}")
    print(f"loaded tenant_{config['slug']} in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
