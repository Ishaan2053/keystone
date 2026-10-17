from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypeAliasType


JsonValue = TypeAliasType(
    "JsonValue",
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"],
)

GRAPH_KINDS = {
    "Person",
    "Team",
    "Repo",
    "Module",
    "File",
    "Issue",
    "PR",
    "Comment",
    "Label",
    "Doc",
    "Chunk",
    "Decision",
    "AgentRun",
    "Action",
    "Feedback",
    "Rule",
}

PROOF_METHODS = {"graph", "codeowners", "similar_issues"}


def _validate_nonblank_string_items(values: list[str], field_name: str) -> list[str]:
    for value in values:
        if not value.strip():
            raise ValueError(f"{field_name} entries must be nonblank")
    return values


def _validate_json_compatible(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must not contain nonfinite floats")
        return value
    if isinstance(value, list):
        for item in value:
            _validate_json_compatible(item, field_name)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} object keys must be strings")
            _validate_json_compatible(item, field_name)
        return value
    raise ValueError(f"{field_name} must be JSON-compatible")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NonblankModel(ContractModel):
    @field_validator("*", mode="after")
    @classmethod
    def _nonblank_required_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("string fields must be nonblank")
        return value


class ToolResult(NonblankModel):
    tool: str
    data: Any
    cypher: list[str]
    params: dict[str, Any]
    evidence: list[str]
    ms: int = Field(ge=0)
    empty: bool

    @field_validator("data", "params", mode="after")
    @classmethod
    def _json_compatible_payloads(cls, value: Any, info: Any) -> Any:
        return _validate_json_compatible(value, info.field_name)

    @field_validator("cypher", "evidence", mode="after")
    @classmethod
    def _nonblank_list_items(cls, value: list[str], info: Any) -> list[str]:
        return _validate_nonblank_string_items(value, info.field_name)


class Claim(NonblankModel):
    text: str
    citations: list[str]

    @field_validator("citations", mode="after")
    @classmethod
    def _nonblank_citations(cls, value: list[str]) -> list[str]:
        return _validate_nonblank_string_items(value, "citations")


class TriageResult(NonblankModel):
    run_key: str
    labels: list[Claim]
    reviewers: list[Claim]
    risks: list[Claim]
    decisions: list[Claim]
    comment_markdown: str
    trace: list[ToolResult]


class HealthResponse(ContractModel):
    status: Literal["ok"]


class TenantSummary(NonblankModel):
    slug: str
    name: str


class TenantsResponse(ContractModel):
    tenants: list[TenantSummary]


class TriageRequest(ContractModel):
    issue_number: int | None = None
    title: str | None = None
    body: str = ""

    @model_validator(mode="after")
    def _validate_mode(self) -> "TriageRequest":
        supplied = self.model_fields_set
        has_number = self.issue_number is not None
        has_title = self.title is not None and bool(self.title.strip())

        if has_number:
            if self.issue_number <= 0:
                raise ValueError("issue_number must be positive")
            if "title" in supplied:
                raise ValueError("title cannot be supplied with issue_number")
            if "body" in supplied:
                raise ValueError("body cannot be supplied with issue_number")
            return self

        if has_title:
            return self

        raise ValueError("provide exactly one mode: issue_number or nonblank title")


class ApplyRequest(ContractModel):
    dry_run: bool = True


class ActionResult(NonblankModel):
    action_key: str
    kind: Literal["label", "assign", "comment"]
    value: str
    status: Literal["applied", "dry_run", "refused"]
    note: str | None = None


class ApplyResult(NonblankModel):
    run_key: str
    dry_run: bool
    actions: list[ActionResult]


class FeedbackRequest(ContractModel):
    action_key: str
    verdict: Literal["accept", "reject"]
    reason: str = ""
    scope: Literal["module", "global"] = "module"

    @field_validator("action_key")
    @classmethod
    def _action_key_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("action_key must be nonblank")
        return value

    @model_validator(mode="after")
    def _reject_requires_reason(self) -> "FeedbackRequest":
        if self.verdict == "reject" and not self.reason.strip():
            raise ValueError("reject feedback requires a nonblank reason")
        return self


class FeedbackResult(NonblankModel):
    feedback_key: str
    action_key: str
    verdict: Literal["accept", "reject"]
    rule_key: str | None


class AskRequest(ContractModel):
    question: str

    @field_validator("question")
    @classmethod
    def _question_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must be nonblank")
        return value


class AskResult(ContractModel):
    answer: list[Claim]
    trace: list[ToolResult]


class RunSummary(NonblankModel):
    run_key: str
    at: int
    issue_key: str
    summary: str
    fallback: bool
    model: str
    llm_calls: int = Field(ge=0)
    ms: int = Field(ge=0)


class RunsResponse(ContractModel):
    runs: list[RunSummary]


class RecordedAction(NonblankModel):
    action_key: str
    kind: Literal["label", "assign", "comment"]
    value: str
    module: str | None
    status: Literal["proposed", "applied", "dry_run", "refused", "rejected"]
    note: str | None
    feedback: list[FeedbackResult]


class RecallRunData(ContractModel):
    run: RunSummary
    evidence: list[str]
    actions: list[RecordedAction]
    triage: TriageResult


class PathData(ContractModel):
    keys: list[str]
    rels: list[str]

    @model_validator(mode="after")
    def _validate_path(self) -> "PathData":
        if len(self.rels) > 4:
            raise ValueError("paths are capped at four hops")
        if self.keys or self.rels:
            if len(self.rels) != len(self.keys) - 1:
                raise ValueError("path rel count must equal key count minus one")
            if any(not key.strip() for key in self.keys):
                raise ValueError("path keys must be nonblank")
            if any(not rel.strip() for rel in self.rels):
                raise ValueError("path rels must be nonblank")
        return self


class GraphNode(NonblankModel):
    key: str
    kind: str
    label: str
    properties: dict[str, JsonValue]

    @field_validator("properties", mode="after")
    @classmethod
    def _validate_properties(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_compatible(value, "properties")

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str) -> str:
        if value not in GRAPH_KINDS:
            raise ValueError("invalid graph node kind")
        return value


class GraphEdge(NonblankModel):
    source: str
    target: str
    type: str


class Subgraph(ContractModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @model_validator(mode="after")
    def _validate_subgraph(self) -> "Subgraph":
        if len(self.nodes) > 150:
            raise ValueError("subgraph node cap is 150")
        keys = {node.key for node in self.nodes}
        for edge in self.edges:
            if edge.source not in keys or edge.target not in keys:
                raise ValueError("subgraph edges cannot have dangling endpoints")
        return self


class ToolLatency(NonblankModel):
    tool: str
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)


class ProofRates(ContractModel):
    reviewer_hit_at_3: float = Field(ge=0, le=1)
    module_hit_at_1: float = Field(ge=0, le=1)
    module_hit_at_3: float = Field(ge=0, le=1)
    label_precision_at_2: float = Field(ge=0, le=1)


ProofMethod = Literal["graph", "codeowners", "similar_issues"]


class Proof(ContractModel):
    n: int = Field(ge=0)
    methods: dict[ProofMethod, ProofRates]
    rows: list[dict[str, JsonValue]]

    @field_validator("rows", mode="after")
    @classmethod
    def _validate_rows(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _validate_json_compatible(value, "rows")

    @model_validator(mode="after")
    def _exact_methods(self) -> "Proof":
        if set(self.methods) != PROOF_METHODS:
            raise ValueError("proof methods must be exactly graph, codeowners, and similar_issues")
        return self


# A metric artifact whose file exists but could not be read or validated.
MetricArtifact = Literal["proof", "latencies"]


class Stats(ContractModel):
    node_counts: dict[str, int]
    edge_counts: dict[str, int]
    last_ingest_at: int | None
    proof: Proof | None
    latencies: list[ToolLatency]
    # A missing file is "not measured" and is not listed here.
    unavailable: list[MetricArtifact] = Field(default_factory=list)
