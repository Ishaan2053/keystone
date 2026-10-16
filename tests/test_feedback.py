"""record_feedback against a throwaway tenant graph; skipped when FalkorDB isn't reachable."""
import os

import pytest

from ingest.fetch import load_env

# Take only FALKORDB_* from .env, so other keys (DRY_RUN, API_MODE...) don't leak into later tests.
_before = set(os.environ)
load_env()
for _key in set(os.environ) - _before:
    if not _key.startswith("FALKORDB_"):
        del os.environ[_key]

from api.db import graph  # noqa: E402
from api.memory import record_feedback  # noqa: E402
from api.schemas import FeedbackRequest  # noqa: E402
from api.tools import rank_reviewers  # noqa: E402

TENANT = "a11test"
MODULE = "module:o/r/src/x"

try:
    graph(TENANT).query("RETURN 1")  # ro_query fails on a graph that does not exist yet
except Exception as exc:
    pytest.skip(f"FalkorDB not reachable: {exc}", allow_module_level=True)


@pytest.fixture
def g(monkeypatch):
    """Two experts on MODULE and a run that proposed assigning one of them, plus a label action."""
    monkeypatch.setenv("TENANTS", TENANT)
    g = graph(TENANT)
    g.query("MATCH (n) DETACH DELETE n")
    g.query(
        "CREATE (m:Module:Node {key: $m, path: 'src/x'})<-[:IN]-(f:File:Node {key: 'file:o/r/src/x/a.c'}),"
        " (pr:PR:Node {key: 'pr:o/r#1', merged_at: 100})-[:TOUCHES]->(f),"
        " (alice:Person:Node {key: 'person:alice', login: 'alice'})-[:AUTHORED]->(pr),"
        " (:Person:Node {key: 'person:bob', login: 'bob'})-[:REVIEWED]->(pr),"
        " (run:AgentRun:Node {key: 'run:1'})-[:PROPOSED]->(:Action:Node {key: 'action:assign', kind: 'assign',"
        "   value: 'alice', module: $m, status: 'proposed'})-[:TARGETS]->(alice),"
        " (run)-[:PROPOSED]->(:Action:Node {key: 'action:label', kind: 'label', value: 'bug', status: 'proposed'})",
        {"m": MODULE},
    )
    yield g
    g.delete()


def reject(action="action:assign", scope="module"):
    req = FeedbackRequest(action_key=action, verdict="reject", reason="on leave", scope=scope)
    return record_feedback(TENANT, "run:1", req, given_by="carol")


def scores():
    res = rank_reviewers(TENANT, MODULE)
    return {r["login"]: (r["score"], r["rules"]) for r in res.data["reviewers"]}, res


def test_reject_zeroes_score_inside_rank_reviewers(g):
    assert scores()[0]["alice"][0] > 0
    fb = reject()
    assert fb.rule_key and fb.verdict == "reject"
    by_login, res = scores()
    assert by_login["alice"] == (0.0, [fb.rule_key])
    assert by_login["bob"][0] > 0 and fb.rule_key in res.evidence
    assert g.ro_query("MATCH (a:Action {key: 'action:assign'}) RETURN a.status").result_set == [["rejected"]]
    rule = g.ro_query(
        "MATCH (p:Person)<-[:APPLIES_TO]-(r:Rule {key: $r})-[:DERIVED_FROM]->(f:Feedback)-[:ON]->(a:Action) "
        "RETURN p.login, r.scope, r.kind, f.key, f.given_by, a.key", {"r": fb.rule_key}).result_set
    assert rule == [["alice", MODULE, "avoid", fb.feedback_key, "carol", "action:assign"]]


def test_repeat_reject_reuses_rule(g):
    first = reject()
    assert reject() == first
    assert g.ro_query("MATCH (r:Rule) RETURN count(r)").result_set == [[1]]


def test_global_rule_has_no_scope(g):
    fb = reject(scope="global")
    assert g.ro_query("MATCH (r:Rule {key: $r}) RETURN r.scope", {"r": fb.rule_key}).result_set == [[None]]
    assert scores()[0]["alice"][0] == 0.0


def test_label_reject_and_accept_make_no_rule(g):
    assert reject("action:label").rule_key is None
    ok = record_feedback(TENANT, "run:1", FeedbackRequest(action_key="action:assign", verdict="accept"), given_by="carol")
    assert ok.rule_key is None and ok.verdict == "accept"
    assert g.ro_query("MATCH (r:Rule) RETURN count(r)").result_set == [[0]]
    assert scores()[0]["alice"][0] > 0


def test_unknown_run_or_action(g):
    req = FeedbackRequest(action_key="action:assign", verdict="accept")
    with pytest.raises(LookupError):
        record_feedback(TENANT, "run:missing", req, given_by="carol")
    with pytest.raises(LookupError):
        record_feedback(TENANT, "run:1", req.model_copy(update={"action_key": "action:missing"}), given_by="carol")
