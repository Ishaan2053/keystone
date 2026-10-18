import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from api.schemas import (
    ActionResult,
    ApplyResult,
    AskRequest,
    AskResult,
    Claim,
    FeedbackRequest,
    FeedbackResult,
    GraphEdge,
    GraphNode,
    PathData,
    Proof,
    RecallRunData,
    RunSummary,
    RunsResponse,
    Stats,
    Subgraph,
    TenantsResponse,
    ToolResult,
    TriageRequest,
    TriageResult,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "api" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def assert_round_trips(model, fixture_name: str):
    raw = load_fixture(fixture_name)
    parsed = model.model_validate(raw)
    assert parsed.model_dump(mode="json") == raw
    return parsed


@pytest.mark.parametrize(
    ("fixture_name", "model"),
    [
        ("tool_result.json", ToolResult),
        ("claim.json", Claim),
        ("triage_result.json", TriageResult),
        ("run_summary.json", RunSummary),
        ("subgraph.json", Subgraph),
        ("stats.json", Stats),
        ("proof.json", Proof),
        ("tenants.json", TenantsResponse),
        ("runs.json", RunsResponse),
        ("apply_dry_run.json", ApplyResult),
        ("apply_refused.json", ApplyResult),
        ("feedback_accept.json", FeedbackResult),
        ("feedback_reject.json", FeedbackResult),
        ("ask.json", AskResult),
    ],
)
def test_inventory_fixtures_validate_and_preserve_raw_json(fixture_name, model):
    assert_round_trips(model, fixture_name)


def test_recall_run_fixture_is_tool_result_wrapping_recall_run_data():
    raw = load_fixture("recall_run.json")
    result = ToolResult.model_validate(raw)
    assert result.tool == "recall_run"
    recall = RecallRunData.model_validate(result.data)
    assert recall.run.run_key == "run:00000000-0000-4000-8000-000000000001"
    assert recall.triage.run_key == recall.run.run_key
    assert set(recall.triage.trace[0].evidence).issubset(set(recall.evidence))


def test_path_fixture_is_tool_result_wrapping_path_data():
    raw = load_fixture("path.json")
    result = ToolResult.model_validate(raw)
    assert result.tool == "explain_path"
    path = PathData.model_validate(result.data)
    assert len(path.rels) == len(path.keys) - 1


def test_triage_request_accepts_number_mode_with_omitted_default_body():
    parsed = TriageRequest.model_validate({"issue_number": 17})
    assert parsed.issue_number == 17
    assert parsed.title is None
    assert parsed.body == ""


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"issue_number": 0},
        {"issue_number": -1},
        {"title": "   "},
        {"issue_number": 17, "title": "synthetic demo"},
        {"issue_number": 17, "body": "supplied body is not allowed with number mode"},
        {"title": "synthetic demo", "issue_number": 17},
        {"title": "synthetic demo", "unexpected": True},
    ],
)
def test_triage_request_rejects_invalid_or_ambiguous_modes(payload):
    with pytest.raises(ValidationError):
        TriageRequest.model_validate(payload)


def test_triage_request_accepts_title_mode_and_preserves_default_body():
    parsed = TriageRequest.model_validate({"title": "Synthetic fixture demonstration"})
    assert parsed.issue_number is None
    assert parsed.title == "Synthetic fixture demonstration"
    assert parsed.body == ""


# A prefix-free run UUID, exactly as the wire carries it; the route expands it to `run:<uuid>`.
FEEDBACK_PARENT_RUN = "00000000-0000-4000-8000-000000000009"
FEEDBACK_PAIR = {"feedback_parent_run": FEEDBACK_PARENT_RUN, "feedback_key": "feedback:ui:1"}


def test_feedback_context_requires_pair_and_full_issue_key():
    """The additive UI feedback lineage: one pair, a bare UUID, and a full issue key only.

    A full issue key names exactly one issue, so only it can anchor a re-triage to a
    proven parent run; a number matches several repos and a raw title has no issue at
    all. Both fields travel together or not at all.
    """
    parsed = TriageRequest.model_validate({"issue_key": "issue:acme/widgets#1", **FEEDBACK_PAIR})
    assert parsed.feedback_parent_run == UUID(FEEDBACK_PARENT_RUN)
    assert parsed.feedback_key == "feedback:ui:1"
    # The wire form stays prefix-free: the backend, not the client, expands `run:<uuid>`.
    assert parsed.model_dump(mode="json")["feedback_parent_run"] == FEEDBACK_PARENT_RUN

    # The existing modes stay valid without any context, and default to no lineage.
    key_only = TriageRequest.model_validate({"issue_key": "issue:acme/widgets#1"})
    number_only = TriageRequest.model_validate({"issue_number": 17})
    title_only = TriageRequest.model_validate({"title": "Widget crashes on resize"})
    assert (key_only.feedback_parent_run, key_only.feedback_key) == (None, None)
    assert (number_only.feedback_parent_run, number_only.feedback_key) == (None, None)
    assert (title_only.feedback_parent_run, title_only.feedback_key) == (None, None)
    assert title_only.body == ""

    for payload in (
        # A single half of the pair.
        {"issue_key": "issue:acme/widgets#1", "feedback_key": "feedback:ui:1"},
        {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": FEEDBACK_PARENT_RUN},
        # A blank feedback key is never a lineage reference.
        {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": FEEDBACK_PARENT_RUN, "feedback_key": "   "},
        # A prefixed or malformed run reference is not a bare UUID.
        {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": f"run:{FEEDBACK_PARENT_RUN}", "feedback_key": "feedback:ui:1"},
        {"issue_key": "issue:acme/widgets#1", "feedback_parent_run": "not-a-uuid", "feedback_key": "feedback:ui:1"},
        # Context outside the canonical issue_key mode.
        {"issue_number": 17, **FEEDBACK_PAIR},
        {"title": "Widget crashes on resize", **FEEDBACK_PAIR},
        {"title": "Widget crashes on resize", "body": "steps", **FEEDBACK_PAIR},
        # Context with no mode at all.
        FEEDBACK_PAIR,
    ):
        with pytest.raises(ValidationError):
            TriageRequest.model_validate(payload)


def test_request_models_reject_unexpected_fields_and_blank_strings():
    with pytest.raises(ValidationError):
        AskRequest.model_validate({"question": "   "})
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {"action_key": "action:1", "verdict": "reject", "scope": "module"}
        )
    with pytest.raises(ValidationError):
        AskRequest.model_validate({"question": "why?", "extra": "nope"})


def test_graph_models_enforce_kinds_endpoints_and_node_cap():
    valid_node = GraphNode(key="person:alice", kind="Person", label="@alice", properties={"login": "alice"})
    assert valid_node.kind == "Person"

    with pytest.raises(ValidationError):
        GraphNode(key="secret:1", kind="Secret", label="secret", properties={})

    with pytest.raises(ValidationError):
        GraphNode(key="person:alice", kind="Person", label="@alice", properties={"bad": object()})

    with pytest.raises(ValidationError):
        Subgraph(nodes=[valid_node], edges=[GraphEdge(source="person:alice", target="person:bob", type="KNOWS")])

    too_many = [
        GraphNode(key=f"person:{idx}", kind="Person", label=f"person {idx}", properties={})
        for idx in range(151)
    ]
    with pytest.raises(ValidationError):
        Subgraph(nodes=too_many, edges=[])


def test_graph_node_and_proof_reject_nonfinite_floats_in_recursive_json():
    with pytest.raises(ValidationError):
        GraphNode(key="person:alice", kind="Person", label="@alice", properties={"x": float("nan")})

    with pytest.raises(ValidationError):
        GraphNode(key="person:alice", kind="Person", label="@alice", properties={"nested": [float("inf")]})

    proof_data = load_fixture("proof.json")
    with pytest.raises(ValidationError):
        Proof.model_validate(proof_data | {"rows": [{"score": float("nan")}]})

    with pytest.raises(ValidationError):
        Proof.model_validate(proof_data | {"rows": [{"nested": {"score": float("-inf")}}]})


def test_path_data_rejects_mismatched_lengths():
    with pytest.raises(ValidationError):
        PathData(keys=["issue:1", "person:alice"], rels=[])


def test_action_and_proof_semantics():
    dry_run = assert_round_trips(ApplyResult, "apply_dry_run.json")
    refused = assert_round_trips(ApplyResult, "apply_refused.json")
    assert dry_run.actions
    assert all(action.status == "dry_run" for action in dry_run.actions)
    assert all(action.status == "refused" and action.note for action in refused.actions)

    proof = assert_round_trips(Proof, "proof.json")
    for rates in proof.methods.values():
        assert 0 <= rates.reviewer_hit_at_3 <= 1
        assert 0 <= rates.module_hit_at_1 <= 1
        assert 0 <= rates.module_hit_at_3 <= 1
        assert 0 <= rates.label_precision_at_2 <= 1


def test_proof_requires_exactly_the_three_frontend_methods():
    proof = load_fixture("proof.json")

    missing = proof | {"methods": {"graph": proof["methods"]["graph"]}}
    with pytest.raises(ValidationError):
        Proof.model_validate(missing)

    unknown = proof | {"methods": proof["methods"] | {"other": proof["methods"]["graph"]}}
    with pytest.raises(ValidationError):
        Proof.model_validate(unknown)


def test_tool_result_data_and_params_must_be_json_compatible_without_per_tool_schema():
    base = load_fixture("tool_result.json")

    for field, bad_value in [
        ("data", {"bad": object()}),
        ("data", {"bad": b"bytes"}),
        ("data", {"bad": float("nan")}),
        ("params", {"bad": object()}),
        ("params", {"bad": b"bytes"}),
        ("params", {"bad": float("inf")}),
    ]:
        with pytest.raises(ValidationError):
            ToolResult.model_validate(base | {field: bad_value})


def test_evidence_citation_and_cypher_entries_reject_blank_strings_but_allow_empty_lists():
    base_tool = load_fixture("tool_result.json")
    with pytest.raises(ValidationError):
        ToolResult.model_validate(base_tool | {"evidence": ["repo:FalkorDB/FalkorDB", "  "]})
    with pytest.raises(ValidationError):
        ToolResult.model_validate(base_tool | {"cypher": ["affected_modules.cypher", ""]})

    empty_lists = ToolResult.model_validate(base_tool | {"evidence": [], "cypher": []})
    assert empty_lists.evidence == []
    assert empty_lists.cypher == []

    with pytest.raises(ValidationError):
        Claim.model_validate({"text": "graph", "citations": ["label:FalkorDB/FalkorDB/graph", "\t"]})

    empty_citations = Claim.model_validate({"text": "no evidence found", "citations": []})
    assert empty_citations.citations == []


def test_stats_fixture_keeps_proof_null_for_b1_b3():
    stats = assert_round_trips(Stats, "stats.json")
    assert stats.proof is None
    assert stats.unavailable == []
    raw = load_fixture("stats.json")
    del raw["unavailable"]
    assert Stats.model_validate(raw).unavailable == []
    with pytest.raises(ValidationError):
        Stats.model_validate({**raw, "unavailable": ["graph"]})
