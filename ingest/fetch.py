"""Fetch a tenant's public GitHub history into data/raw/<tenant>/ (spec section 4).

Every response is cached on disk, keyed by method + URL + params, so a second
run makes zero network requests. Commit endpoints are refused, and every
`email` key is stripped before anything is written (Rule 6).

    python -m ingest.fetch --tenant falkordb
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent
JSON_ACCEPT = "application/vnd.github+json"
RAW_ACCEPT = "application/vnd.github.raw+json"
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
COMMITS = re.compile(r"/commits(/|$)")


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into os.environ; the shell wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def scrub(obj):
    """Drop every `email` key at any depth (Rule 6)."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k != "email"}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(scrub(obj), ensure_ascii=False), encoding="utf-8")


def glob_re(pattern: str) -> re.Pattern:
    """Path glob: `**/` matches any number of directories, `*` stays within one."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


class GitHub:
    """GET-only GitHub REST client with an on-disk cache and a request counter."""

    def __init__(self, token: str, cache_dir: Path):
        self.client = httpx.Client(
            base_url=API,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "keystone-ingest",
            },
        )
        self.cache_dir = cache_dir
        self.network = 0
        self.hits = 0

    def get(self, path: str, params: dict | None = None, accept: str = JSON_ACCEPT):
        """Return (status, body) for one GET, from the cache when it's there."""
        if COMMITS.search(path.split("/contents/")[0]):
            raise ValueError(f"refusing commit endpoint: {path}")
        params = dict(sorted((params or {}).items()))
        key = hashlib.sha256(json.dumps(["GET", API + path, params, accept]).encode()).hexdigest()
        file = self.cache_dir / f"{key}.json"
        if file.exists():
            self.hits += 1
            entry = json.loads(file.read_text(encoding="utf-8"))
            return entry["status"], entry["body"]
        status, body = self._request(path, params, accept)
        entry = {"method": "GET", "url": API + path, "params": params, "accept": accept, "status": status, "body": body}
        write_json(file, entry)
        return status, scrub(body)

    def _request(self, path: str, params: dict, accept: str):
        for attempt in range(6):
            self.network += 1
            try:
                r = self.client.get(path, params=params, headers={"Accept": accept})
            except httpx.TransportError as e:  # dropped connection, timeout: retry
                print(f"  {type(e).__name__} on {path}, retrying", flush=True)
                time.sleep(2 * attempt + 1)
                continue
            if r.status_code in (403, 429) and "rate limit" in r.text.lower():
                self._wait(r)
                continue
            if r.status_code >= 500:
                time.sleep(2 * attempt + 1)
                continue
            if r.status_code == 404:
                return 404, None
            r.raise_for_status()
            if r.headers.get("x-ratelimit-remaining") == "0":
                self._wait(r)
            return r.status_code, r.text if accept == RAW_ACCEPT else r.json()
        raise RuntimeError(f"gave up after retries: GET {path} {params}")

    def _wait(self, r: httpx.Response) -> None:
        if "retry-after" in r.headers:
            secs = int(r.headers["retry-after"])
        elif r.headers.get("x-ratelimit-remaining") == "0":
            secs = max(0, int(r.headers["x-ratelimit-reset"]) - int(time.time())) + 1
        else:
            secs = 60  # secondary limit without a hint: GitHub says wait at least a minute
        print(f"  rate limited, sleeping {secs}s", flush=True)
        time.sleep(secs)

    def pages(self, path: str, params: dict | None = None, stop=None):
        """Yield items across pages of 100 until a short page, or until stop(item)."""
        page = 1
        while True:
            _, body = self.get(path, {**(params or {}), "per_page": 100, "page": page})
            items = body or []
            for item in items:
                if stop and stop(item):
                    return
                yield item
            if len(items) < 100:
                return
            page += 1


def fetch_repo(gh: GitHub, repo: str, since: str, docs_globs: list[str], out: Path) -> dict:
    base = f"/repos/{repo}"
    _, meta = gh.get(base)
    branch = meta["default_branch"]

    # PRs created inside the window, newest first; stop paging at the first older one.
    prs = []
    for pr in gh.pages(f"{base}/pulls", {"state": "all", "sort": "created", "direction": "desc"},
                       stop=lambda p: p["created_at"] < since):
        n = pr["number"]
        pr["files"] = list(gh.pages(f"{base}/pulls/{n}/files"))
        pr["reviews"] = list(gh.pages(f"{base}/pulls/{n}/reviews"))
        if pr["state"] != "open":  # requested reviewers are "load", so open PRs only
            pr["requested_reviewers"], pr["requested_teams"] = [], []
        prs.append(pr)
        if len(prs) % 100 == 0:
            print(f"  {len(prs)} PRs", flush=True)

    issues = [i for i in gh.pages(f"{base}/issues", {"state": "all", "since": since}) if "pull_request" not in i]
    issue_comments = list(gh.pages(f"{base}/issues/comments", {"since": since}))
    review_comments = list(gh.pages(f"{base}/pulls/comments", {"since": since}))
    labels = list(gh.pages(f"{base}/labels"))

    # One tree call finds CODEOWNERS and the docs without probing paths that may 404.
    _, tree = gh.get(f"{base}/git/trees/{quote(branch, safe='')}", {"recursive": "1"})
    if tree.get("truncated"):
        raise SystemExit(f"{repo}: git tree response is truncated; docs and CODEOWNERS would be incomplete")
    blobs = {t["path"] for t in tree["tree"] if t["type"] == "blob"}

    def contents(path: str) -> str:
        _, text = gh.get(f"{base}/contents/{quote(path)}", {"ref": branch}, accept=RAW_ACCEPT)
        return text

    owners_path = next((p for p in CODEOWNERS_PATHS if p in blobs), None)
    if owners_path:
        codeowners = {"path": owners_path, "text": contents(owners_path)}
    else:
        codeowners = {"path": None, "note": "no CODEOWNERS in .github/, the root or docs/"}

    patterns = [glob_re(g) for g in docs_globs]
    docs = [
        {"path": p, "url": f"https://github.com/{repo}/blob/{branch}/{p}", "text": contents(p)}
        for p in sorted(blobs)
        if any(r.fullmatch(p) for r in patterns)
    ]

    out.mkdir(parents=True, exist_ok=True)
    files = {
        "repo": meta, "prs": prs, "issues": issues, "issue_comments": issue_comments,
        "review_comments": review_comments, "labels": labels, "codeowners": codeowners, "docs": docs,
    }
    for name, obj in files.items():
        write_json(out / f"{name}.json", obj)

    return {
        "prs": len(prs),
        "pr_files": sum(len(p["files"]) for p in prs),
        "reviews": sum(len(p["reviews"]) for p in prs),
        "issues": len(issues),
        "issue_comments": len(issue_comments),
        "review_comments": len(review_comments),
        "labels": len(labels),
        "docs": len(docs),
        "codeowners": owners_path or "none",
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Fetch a tenant's GitHub history into data/raw/<tenant>/")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--refresh", action="store_true", help="recompute the since cutoff (new cache keys, refetches)")
    args = ap.parse_args(argv)

    load_env()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set (in .env or the shell)")

    config = yaml.safe_load((ROOT / "ingest" / "tenants" / f"{args.tenant}.yml").read_text(encoding="utf-8"))
    out = ROOT / "data" / "raw" / args.tenant
    (out / "_cache").mkdir(parents=True, exist_ok=True)

    # The cutoff is fixed in the manifest so cache keys stay stable across runs and days.
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    since = manifest.get("since")
    if args.refresh or not since or manifest.get("since_days") != config["since_days"]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=config["since_days"])
        since = cutoff.strftime("%Y-%m-%dT00:00:00Z")

    gh = GitHub(token, out / "_cache")
    counts = {}
    for repo in config["repos"]:
        print(repo, flush=True)
        counts[repo] = fetch_repo(gh, repo, since, config["docs_globs"], out / repo.replace("/", "__"))
        print("  " + " ".join(f"{k}={v}" for k, v in counts[repo].items()), flush=True)

    write_json(manifest_path, {"tenant": args.tenant, "since": since, "since_days": config["since_days"],
                               "repos": config["repos"], "counts": counts})
    print(f"since {since}")
    print(f"network requests: {gh.network}, cache hits: {gh.hits}")


if __name__ == "__main__":
    main()
