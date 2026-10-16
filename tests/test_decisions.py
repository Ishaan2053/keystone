"""Offline extraction regressions: real source/prompt/process/CLI, fake external boundaries."""
import importlib
from types import SimpleNamespace

import pytest

from ingest import decisions
from api.llm import LLMError

vector_tool = importlib.import_module("api.tools.find_decisions")


def pr(number=1, **changes):
    row = {
        "key": f"pr:org/repo#{number}", "url": f"https://example.test/{number}",
        "title": "Choose **local** storage", "body": "Keep `data` local.\nBecause privacy matters.",
        "comments": [], "modules": ["src"],
    }
    return row | changes


def decision(quote="Keep `data` local.\nBecause privacy matters.", **changes):
    return {"summary": "Store data locally.", "rationale": "Protect privacy.",
            "quote": quote, "about_paths": []} | changes


@pytest.mark.parametrize(("quote", "fields", "expected"), [
    ("**local**", ["Choose **local** storage"], True),
    ("`data` local.\nBecause privacy", ["Keep `data` local.\nBecause privacy matters."], True),
    ("Keep data local.", ["Keep `data` local."], False),
    ("Choose local storage", ["Choose **local** storage"], False),
    ("Keep data local", ["Keep  data local"], False),
    ("Keep data local", ["Keep data\nlocal"], False),
    ("", ["anything"], False),
    (" \n\t", ["anything \n\t else"], False),
    ("Keep data local", ["Keep data", "local"], False),
    ("Keep data\n\nlocal", ["Keep data", "local"], False),
])
def test_quote_requires_exact_nonblank_membership_in_one_field(quote, fields, expected):
    assert decisions.quote_found(quote, fields) is expected


@pytest.fixture
def cli(monkeypatch, tmp_path, capsys):
    tenant_dir = tmp_path / "ingest" / "tenants"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "offline.yml").write_text("slug: offline\n")
    state = SimpleNamespace(prs=[], docs=[], prompts=[], writes=[], encoded=[], checks=[],
                            stage=None, exception=RuntimeError("secret-token RAW_PAYLOAD"), selections=[])

    def generate(prompt, schema, system):
        state.prompts.append(prompt)
        assert schema is decisions.SCHEMA
        assert system == decisions.SYSTEM
        if state.stage == "provider" and "pr:org/repo#1\n" in prompt:
            raise state.exception
        return {"decisions": [decision(about_paths=["src/file.py"])]}, "offline-model"

    def ro_query(name, params):
        state.selections.append((name, params))
        if name == "module_keys":
            if state.stage == "mapping" and len(state.prompts) == 1:
                raise state.exception
            return SimpleNamespace(result_set=[["module:org/repo/src"]])
        data = state.prs if name == "decision_sources" else state.docs
        columns = list(data[0]) if data else []
        return SimpleNamespace(header=[[i, c] for i, c in enumerate(columns)],
                               result_set=[[row[c] for c in columns] for row in data])

    def write(name, params):
        assert name == "write_decisions"
        if state.stage == "write" and params["rows"][0]["source"] == "pr:org/repo#1":
            raise state.exception
        state.writes.extend(params["rows"])

    def encode(texts):
        state.encoded.append(texts)
        if state.stage == "embedding" and len(state.prompts) == 1:
            raise state.exception
        return [[0.25, 0.75] for _ in texts]

    def verify(tenant, summary):
        state.checks.append((tenant, summary))
        if state.stage == "verification" or getattr(state, "verify_fails", False):
            raise state.exception
        rows = [] if getattr(state, "verify_missing", False) else [
            {"decision": state.writes[0]["key"], "summary": summary, "score": 1.0}]
        return SimpleNamespace(data={"decisions": rows})

    monkeypatch.setattr(decisions, "ROOT", tmp_path)
    monkeypatch.setattr(decisions, "load_env", lambda: None)
    monkeypatch.setattr(decisions, "tenant_graph", lambda slug: SimpleNamespace(ro_query=ro_query, query=write))
    monkeypatch.setattr(decisions, "query", lambda name: name)
    monkeypatch.setattr(decisions, "generate_json", generate)
    monkeypatch.setattr(decisions, "encode", encode)
    monkeypatch.setattr(vector_tool, "find_decisions", verify)
    monkeypatch.setattr(decisions.sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    state.run = lambda *flags: decisions.main(["--tenant", "offline", *flags])
    state.output = lambda: capsys.readouterr().out
    return state


@pytest.mark.parametrize("quote", ["Keep data local.", "Keep `data` local. Because privacy matters.", "", " \n", "storage Keep"])
def test_invalid_quote_never_embeds_or_writes(cli, monkeypatch, quote):
    cli.prs = [pr()]
    monkeypatch.setattr(decisions, "generate_json", lambda *args: ({"decisions": [decision(quote)]}, "offline"))
    cli.run()
    assert cli.encoded == []
    assert cli.writes == []
    assert cli.checks == []
    assert "kept 0, dropped 1, failed sources 0" in cli.output()


@pytest.mark.parametrize("kind", ["pr_body", "pr_comment", "doc"])
def test_complete_long_source_is_sent_once_and_late_quote_is_eligible(monkeypatch, kind):
    late = "We chose **local** storage.\nFor privacy."
    body = "b" * 31000 + late
    comment = "c" * 31000 + (late if kind == "pr_comment" else "The comment is complete.")
    if kind == "pr_comment":
        body = "b" * 31000
    if kind.startswith("pr_"):
        source = decisions.pr_source(pr(body=body, comments=[
            {"login": "alice", "body": comment}, {"login": "bob", "body": "Final comment."}]))
        fields = [("TITLE", "Choose **local** storage"), ("BODY", body),
                  ("COMMENT 1 by alice", comment), ("COMMENT 2 by bob", "Final comment.")]
    else:
        source = decisions.doc_source({"key": "doc:org/repo/design.md", "url": "https://example.test/doc",
                                       "path": "design.md", "title": "Design", "modules": [],
                                       "chunks": [body, comment]})
        fields = [("DOCUMENT", body + "\n\n" + comment)]
    prompts = []

    def generate(text, schema, system):
        prompts.append(text)
        return {"decisions": [decision(late)]}, "offline"

    monkeypatch.setattr(decisions, "generate_json", generate)
    kept, dropped, model = decisions.process(SimpleNamespace(), source)
    assert len(prompts) == 1
    assert prompts[0].endswith("".join(f"\n\n=== {heading} ===\n{text}" for heading, text in fields))
    assert [row["quote"] for row in kept] == [late]
    assert dropped == []


@pytest.mark.parametrize("stage", ["provider", "embedding", "write", "mapping"])
def test_failed_source_continues_and_exits_nonzero_after_truthful_summary(cli, stage):
    cli.stage = stage
    cli.exception = LLMError("secret-token RAW_PAYLOAD") if stage == "provider" else cli.exception
    cli.prs = [pr(1), pr(2)]
    with pytest.raises(SystemExit) as error:
        cli.run("--prs", "2", "--no-docs")
    assert error.value.code == 1
    output = cli.output()
    assert "kept 1, dropped 0, failed sources 1" in output
    assert "secret-token" not in output and "RAW_PAYLOAD" not in output
    assert [row["source"] for row in cli.writes] == ["pr:org/repo#2"]
    assert output.count("  KEPT ") == 1
    assert len(cli.checks) == 1
    assert "decision_docs" not in [name for name, _ in cli.selections]
    assert ("decision_sources", {"limit": 2}) in cli.selections


def test_provider_context_rejection_is_failed_not_empty_success(cli):
    cli.prs = [pr(body="x" * 31000)]
    cli.stage = "provider"
    cli.exception = LLMError("context too large secret-token RAW_PAYLOAD")
    with pytest.raises(SystemExit) as error:
        cli.run()
    assert error.value.code == 1
    assert "x" * 31000 in cli.prompts[0]
    assert "kept 0, dropped 0, failed sources 1" in cli.output()


def test_failed_write_never_claims_kept_or_runs_vector_check(cli):
    cli.prs = [pr()]
    cli.stage = "write"
    with pytest.raises(SystemExit) as error:
        cli.run()
    assert error.value.code == 1
    output = cli.output()
    assert "kept 0, dropped 0, failed sources 1" in output
    assert "  KEPT " not in output
    assert "vector check with" not in output
    assert cli.checks == []


@pytest.mark.parametrize("source_failure", [False, True])
def test_verification_failure_is_sanitized_and_preserves_source_summary(cli, source_failure):
    cli.prs = [pr(1), pr(2)] if source_failure else [pr()]
    cli.stage = "provider" if source_failure else None
    cli.verify_fails = True
    with pytest.raises(SystemExit) as error:
        cli.run()
    assert error.value.code == 1
    output = cli.output()
    assert f"kept 1, dropped 0, failed sources {int(source_failure)}" in output
    assert "verification FAILED" in output
    assert "RAW_PAYLOAD" not in output and "secret-token" not in output


def test_missing_kept_decision_in_vector_results_is_verification_failure(cli):
    cli.prs = [pr()]
    cli.verify_missing = True
    with pytest.raises(SystemExit) as error:
        cli.run()
    assert error.value.code == 1
    assert "verification FAILED" in cli.output()


@pytest.mark.parametrize("count", [0, 1])
def test_successful_or_zero_source_run_exits_successfully(cli, count):
    cli.prs = [pr()] if count else []
    assert cli.run() is None
    assert f"kept {count}, dropped 0, failed sources 0" in cli.output()
    assert len(cli.writes) == count
    if count:
        assert cli.writes[0]["modules"] == ["module:org/repo/src"]
        assert cli.writes[0]["vec"] == [0.25, 0.75]
        assert cli.writes[0]["quote"] == "Keep `data` local.\nBecause privacy matters."


@pytest.mark.parametrize("exception", [KeyboardInterrupt(), SystemExit(7)])
@pytest.mark.parametrize("stage", ["provider", "embedding", "write", "verification"])
def test_control_flow_exceptions_are_not_swallowed(cli, exception, stage):
    cli.prs = [pr()]
    cli.stage, cli.exception = stage, exception
    with pytest.raises(type(exception)) as error:
        cli.run()
    assert error.value is exception
