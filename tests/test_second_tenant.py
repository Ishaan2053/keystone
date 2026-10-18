"""B9 second-tenant regression coverage.

Task 1 proves the approved Pallets tenant config is exhaustive and that the
existing API dispatch already routes per-tenant. No new API implementation:
the routes select ``tenant_<slug>`` and 404 unknown tenants by construction.

Task 3 proves run isolation against the live FalkorDB: runs and their evidence
never leak between two tenant graphs, even when the same stable run key exists
in both. Skipped only when FalkorDB is unreachable (the config checks below
never need a database).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import yaml
from falkordb import FalkorDB
from fastapi.testclient import TestClient

from ingest.fetch import load_env

ROOT = Path(__file__).resolve().parents[1]

# Take only FALKORDB_* from .env, so other keys (DRY_RUN, API_MODE...) don't leak into
# later tests. tests/conftest.py already does this for every module; tests/test_tools.py
# uses the same belt-and-braces here because this module can also run standalone.
_before = set(os.environ)
load_env()
for _key in set(os.environ) - _before:
    if not _key.startswith("FALKORDB_"):
        del os.environ[_key]

from api import schemas  # noqa: E402
from api.config import Settings  # noqa: E402
from api.db import graph  # noqa: E402
from api.fixture_store import FixtureStore  # noqa: E402
from api.main import create_app  # noqa: E402
from api.memory import list_runs  # noqa: E402
from api.tools import recall_run  # noqa: E402


def _tenant_cfg(slug: str) -> dict:
    return yaml.safe_load((ROOT / "ingest" / "tenants" / f"{slug}.yml").read_text(encoding="utf-8"))


def test_pallets_config():
    cfg = _tenant_cfg("pallets")
    primary = _tenant_cfg("falkordb")

    assert cfg == dict(
        slug="pallets",
        org="pallets",
        repos=["pallets/flask"],
        since_days=365,
        docs_globs=["README.md", "docs/**/*.md", "*.md"],
        max_pr_files=300,
    )
    assert not ({r.lower() for r in cfg["repos"]} & {r.lower() for r in primary["repos"]})


# --------------------------------------------------------------------------
# B9 Task 3: graph-local run recall (live against FalkorDB)
# --------------------------------------------------------------------------

TESTS = Path(__file__).parent
RECALL_FIXTURE = (TESTS / "cypher/recall_fixture.cypher").read_text(encoding="utf-8")
DELETE_KEYS = (TESTS / "cypher/delete_keys.cypher").read_text(encoding="utf-8")

# Minimal Issue/Person/Module seeds per graph. The nodes carry the Node label so the
# reused tests/cypher/delete_keys.cypher (which matches n:Node by key) can remove them.
SEED_NODES = (TESTS / "cypher/seed_nodes.cypher").read_text(encoding="utf-8")


def _fdb():
    """A FalkorDB client built exactly as api.db builds one (FALKORDB_* env, like graph())."""
    return FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )


def _triage_json(run_key: str) -> str:
    """A valid stored TriageResult JSON for the run, dumped through the schema itself."""
    return schemas.TriageResult(
        run_key=run_key,
        labels=[],
        reviewers=[],
        risks=[],
        decisions=[],
        comment_markdown="B9 graph-local run isolation fixture",
        trace=[],
    ).model_dump_json()


def _run_fixture(
    *, issue_key: str, person_key: str, module_key: str, run_key: str, triage: str
) -> tuple[dict, list[str]]:
    """recall_fixture.cypher args with fresh action/feedback/rule keys, plus the created
    Node-labeled keys (actions, feedback, rule) for the teardown's key deletion."""
    keys = {
        k: f"{prefix}:{uuid.uuid4()}"
        for k, prefix in [
            ("assign", "action"),
            ("label", "action"),
            ("feedback", "feedback"),
            ("rule", "rule"),
        ]
    }
    params = {
        **keys,
        "run": run_key,
        "issue": issue_key,
        "person": person_key,
        "module": module_key,
        "at": 1792000000,
        "triage": triage,
    }
    return params, list(keys.values())


def _assert_owned_graphs_exist(names, owned):
    for name in owned:
        assert name in names, f"Owned graph missing: {name}"


def test_runs_stay_in_their_graph(monkeypatch):
    """The same stable run key in two graphs recalls only its own graph's run and evidence.

    Two disposable ``b9test_<uuid>_a/b`` slugs (graphs ``tenant_b9test_<uuid>_a/b``) make the
    run keys seeded via the existing tests/cypher/recall_fixture.cypher overlap on purpose;
    before teardown, both owned graphs exist, and only they are deleted. Other concurrent
    invocations may own additional b9test graphs.
    Never touches tenant_falkordb or tenant_pallets.
    """
    try:
        _fdb().list_graphs()  # reachability probe: touches no graph data
    except Exception as exc:
        pytest.skip(f"FalkorDB not reachable: {exc}")

    suffix = uuid.uuid4().hex
    a, b = f"b9test_{suffix}_a", f"b9test_{suffix}_b"
    monkeypatch.setenv("TENANTS", f"{a},{b}")  # tools read process TENANTS, routes read Settings
    settings = Settings(tenants=(a, b), cors_origins=("http://localhost:3001",), api_mode="live")

    shared = f"run:{uuid.uuid4()}"
    only_a = f"run:{uuid.uuid4()}"
    fixtures = {
        slug: {  # one stable-key shape per graph, distinct issue/evidence
            "issue": f"issue:b9test/{suffix}#{slug[-1]}",
            "person": f"person:b9test-{slug[-1]}",
            "module": f"module:b9test/{suffix}/{slug[-1]}",
            "login": f"alice-{slug[-1]}",
        }
        for slug in (a, b)
    }
    issue_a, issue_b = fixtures[a]["issue"], fixtures[b]["issue"]

    # Pre-initialized so the finally below never sees an unbound name; a slug missing
    # from `graphs` means its graph was never created, so there is nothing to clean.
    graphs: dict = {}
    deletions: dict = {a: [], b: []}
    try:
        # Everything that can create a graph is inside this try so the finally below
        # still deletes only the two owned graphs even if seeding aborts midway.
        graphs.update({slug: graph(slug) for slug in (a, b)})
        for slug, g in ((a, graphs[a]), (b, graphs[b])):
            f = fixtures[slug]
            g.query(SEED_NODES, f)
            deletions[slug] += [f["issue"], f["person"], f["module"]]

        # The same valid run UUID in both graphs; each graph's run cites its own evidence.
        for slug, g in ((a, graphs[a]), (b, graphs[b])):
            f = fixtures[slug]
            params, node_keys = _run_fixture(
                run_key=shared,
                issue_key=f["issue"],
                person_key=f["person"],
                module_key=f["module"],
                triage=_triage_json(shared),
            )
            g.query(RECALL_FIXTURE, params)
            deletions[slug] += node_keys

        # One A-only run in graph A, so B must 404/recall-empty it.
        f_a = fixtures[a]
        params, node_keys = _run_fixture(
            run_key=only_a,
            issue_key=f_a["issue"],
            person_key=f_a["person"],
            module_key=f_a["module"],
            triage=_triage_json(only_a),
        )
        graphs[a].query(RECALL_FIXTURE, params)
        deletions[a] += node_keys

        client = TestClient(create_app(settings=settings, fixture_store=FixtureStore.default()))
        assert recall_run(a, shared).data["run"]["issue_key"] == issue_a
        assert recall_run(b, shared).data["run"]["issue_key"] == issue_b
        assert issue_a not in recall_run(b, shared).evidence
        assert recall_run(b, only_a).empty is True
        assert {r.run_key for r in list_runs(b, 20)} == {shared}
        assert client.get(f"/tenants/{b}/runs/{only_a.removeprefix('run:')}").status_code == 404
        _assert_owned_graphs_exist(_fdb().list_graphs(), (f"tenant_{a}", f"tenant_{b}"))
    finally:
        # Only the two owned graphs are deleted: first their Node-labeled fixture nodes via
        # the reused tests/cypher/delete_keys.cypher, then the graphs themselves so no
        # tenant_b9test_* leftovers survive. tenant_falkordb / tenant_pallets are untouched.
        # A graph missing from `graphs` was never created, so it is skipped; an empty key
        # list simply matches no Node, so a partially-seeded graph is still cleaned.
        for slug, keys in ((a, deletions.get(a, [])), (b, deletions.get(b, []))):
            g = graphs.get(slug)
            if g is None:
                continue
            try:
                g.query(DELETE_KEYS, {"keys": keys})
            except Exception:  # noqa: BLE001 - teardown; the graph drop below covers it
                pass
            try:
                g.delete()
            except Exception:  # noqa: BLE001 - the server drop is best-effort in finally
                pass
