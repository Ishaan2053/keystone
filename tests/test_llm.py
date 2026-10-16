"""Offline schema, cache and fallback regressions for the shared LLM interface."""
import copy
import hashlib
import json
import urllib.request

import pytest

from api import llm


SCHEMA = {
    "type": "object",
    "properties": {"decisions": {
        "type": "array", "items": {
            "type": "object",
            "properties": {"summary": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["summary", "quote"],
            "additionalProperties": False,
        },
    }},
    "required": ["decisions"],
    "additionalProperties": False,
}
EMPTY = {"decisions": []}
INVALID = [
    {"decisions": "not-an-array"},
    {},
    {"decisions": [{}]},
    {"decisions": [{"summary": "missing quote"}]},
    {"decisions": ["not-an-object"]},
    {"decisions": [{"summary": 7, "quote": "evidence"}]},
    {"decisions": [{"summary": "choice", "quote": None}]},
    {"decisions": [], "unexpected": "secret-response"},
]


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("LLM_CACHE", "on")
    monkeypatch.setattr(llm, "_exhausted", set())
    calls = []
    responses = {}

    def no_network(*args, **kwargs):
        pytest.fail("unexpected real network/schema retrieval")

    monkeypatch.setattr(llm.httpx, "post", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)

    def post(provider, model, body):
        calls.append((provider, model, body))
        response = responses.get(model, EMPTY)
        if isinstance(response, Exception):
            raise response
        text = json.dumps(response)
        if provider == "gemini":
            return {"candidates": [{"finishReason": "STOP", "content": {
                "role": "model", "parts": [{"text": text}],
            }}]}
        return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}

    monkeypatch.setattr(llm, "_post", post)
    return calls, responses


def seed_cache(model, response, schema=SCHEMA, prompt="extract", system=None):
    path = llm.CACHE_DIR / f"{llm.cache_key(model, prompt, schema, system)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "response": response}), encoding="utf-8")
    return path


@pytest.mark.parametrize("response", INVALID)
def test_invalid_fresh_json_is_never_returned_or_cached(offline, response, capsys):
    calls, responses = offline
    responses.update({model: response for _, model in llm.JSON_MODELS})
    with pytest.raises(llm.LLMError) as exc:
        llm.generate_json("extract", SCHEMA)
    assert [call[:2] for call in calls] == llm.JSON_MODELS
    assert not list(llm.CACHE_DIR.glob("*"))
    assert "secret-response" not in str(exc.value) + str(capsys.readouterr())


def test_invalid_candidates_fall_through_to_groq_and_only_valid_answer_is_cached(offline):
    calls, responses = offline
    for _, model in llm.JSON_MODELS[:-1]:
        responses[model] = {"decisions": "not-an-array"}
    assert llm.generate_json("extract", SCHEMA) == (EMPTY, llm.GROQ_JSON_MODEL)
    assert [call[:2] for call in calls] == llm.JSON_MODELS
    entries = list(llm.CACHE_DIR.glob("*.json"))
    assert len(entries) == 1
    assert json.loads(entries[0].read_text())["response"] == EMPTY


@pytest.mark.parametrize("response", [EMPTY, {"decisions": [{"summary": "choice", "quote": "evidence"}]}])
def test_valid_json_is_cached_and_reused(offline, response):
    calls, responses = offline
    model = llm.GEMINI_MODELS[0]
    responses[model] = response
    assert llm.generate_json("extract", SCHEMA) == (response, model)
    assert llm.generate_json("extract", SCHEMA) == (response, model)
    assert len(calls) == 1


@pytest.mark.parametrize("response", INVALID)
def test_schema_invalid_cache_is_a_miss(offline, response):
    calls, _ = offline
    path = seed_cache(llm.GEMINI_MODELS[0], response)
    assert llm.generate_json("extract", SCHEMA) == (EMPTY, llm.GEMINI_MODELS[0])
    assert len(calls) == 1
    assert json.loads(path.read_text())["response"] == EMPTY


@pytest.mark.parametrize("content", [b"broken-json", b"{}", b"null", b"[]", b'"string"', b"\xff"])
def test_corrupt_cache_is_a_miss(offline, content):
    calls, _ = offline
    path = seed_cache(llm.GEMINI_MODELS[0], EMPTY)
    path.write_bytes(content)
    assert llm.generate_json("extract", SCHEMA) == (EMPTY, llm.GEMINI_MODELS[0])
    assert len(calls) == 1


def test_valid_later_cache_candidate_wins_without_network(offline):
    calls, _ = offline
    seed_cache(llm.GEMINI_MODELS[0], {"decisions": "not-an-array"})
    seed_cache(llm.GROQ_JSON_MODEL, EMPTY)
    assert llm.generate_json("extract", SCHEMA) == (EMPTY, llm.GROQ_JSON_MODEL)
    assert calls == []


def test_cache_off_skips_reads_and_writes(offline, monkeypatch):
    calls, _ = offline
    cached = {"decisions": [{"summary": "cached", "quote": "old"}]}
    path = seed_cache(llm.GEMINI_MODELS[0], cached)
    before = path.read_bytes()
    monkeypatch.setenv("LLM_CACHE", " off ")
    assert llm.generate_json("extract", SCHEMA) == (EMPTY, llm.GEMINI_MODELS[0])
    assert len(calls) == 1
    assert path.read_bytes() == before
    assert len(list(llm.CACHE_DIR.glob("*"))) == 1


def test_cache_off_still_validates_fresh_output(offline, monkeypatch):
    _, responses = offline
    monkeypatch.setenv("LLM_CACHE", "off")
    responses.update({model: INVALID[0] for _, model in llm.JSON_MODELS})
    with pytest.raises(llm.LLMError):
        llm.generate_json("extract", SCHEMA)
    assert not llm.CACHE_DIR.exists()


@pytest.mark.parametrize("schema", [
    None,
    False,
    [],
    {"$schema": 17},
    {"type": "secret-invalid-type"},
    {"type": "object", "properties": {"nested": {"type": 123}}},
    {"type": "object", "required": "secret-invalid-required"},
    {"$schema": "https://secret.invalid/dialect", "type": "object"},
])
def test_invalid_caller_schema_fails_safely_before_cache_or_provider(offline, schema):
    calls, _ = offline
    seed_cache(llm.GEMINI_MODELS[0], EMPTY, schema=schema)
    with pytest.raises(llm.LLMError) as exc:
        llm.generate_json("extract", schema)
    assert calls == []
    assert "secret" not in str(exc.value)


def test_local_refs_and_schema_constraints_are_not_reduced_to_type_checks(offline):
    calls, responses = offline
    schema = {
        "type": "object", "$defs": {"label": {"type": "string", "enum": ["allowed"]}},
        "properties": {"label": {"$ref": "#/$defs/label"}}, "required": ["label"],
    }
    responses[llm.GEMINI_MODELS[0]] = {"label": "forbidden-secret"}
    responses[llm.GEMINI_MODELS[1]] = {"label": "allowed"}
    assert llm.generate_json("extract", schema) == ({"label": "allowed"}, llm.GEMINI_MODELS[1])
    assert len(calls) == 2
    assert len(list(llm.CACHE_DIR.glob("*.json"))) == 1


@pytest.mark.parametrize("ref", ["https://secret.invalid/schema", "file:///secret/schema.json"])
def test_external_refs_never_retrieve_remote_or_local_resources(offline, ref):
    with pytest.raises(llm.LLMError) as exc:
        llm.generate_json("extract", {"$ref": ref})
    assert "secret" not in str(exc.value)
    assert not list(llm.CACHE_DIR.glob("*"))


def test_declared_draft_is_honored(offline):
    _, responses = offline
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
              "properties": {"items": {"type": "array", "items": [{"const": "first"}]}}}
    responses[llm.GEMINI_MODELS[0]] = {"items": ["wrong"]}
    responses[llm.GEMINI_MODELS[1]] = {"items": ["first"]}
    assert llm.generate_json("extract", schema) == ({"items": ["first"]}, llm.GEMINI_MODELS[1])


def test_cache_identity_remains_model_prompt_schema_and_system(offline):
    calls, _ = offline
    model = llm.GEMINI_MODELS[0]
    request = {"model": model, "system": "system", "prompt": "extract", "schema": SCHEMA}
    expected = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert llm.cache_key(model, "extract", SCHEMA, "system") == expected
    seed_cache(model, EMPTY, system="system")
    assert llm.generate_json("extract", SCHEMA, "system") == (EMPTY, model)
    assert calls == []
    changed_schema = copy.deepcopy(SCHEMA)
    changed_schema["title"] = "different schema"
    for prompt, schema, system in [("other", SCHEMA, "system"), ("extract", changed_schema, "system"),
                                   ("extract", SCHEMA, "other")]:
        llm.generate_json(prompt, schema, system)
    assert len(calls) == 3


def test_chat_provider_failure_fallback_and_cache_remain_unchanged(offline):
    calls, responses = offline
    for model in llm.GEMINI_MODELS:
        responses[model] = llm.LLMError("offline unavailable")
    responses[llm.GROQ_CHAT_MODEL] = {"reply": "hello"}
    messages = [{"role": "user", "text": "hello"}]
    reply = llm.chat(messages, [])
    assert reply.provider == "groq" and reply.model == llm.GROQ_CHAT_MODEL
    assert reply.text == '{"reply": "hello"}' and reply.tool_calls == []
    assert llm.chat(messages, []) == reply
    assert [call[:2] for call in calls] == llm.CHAT_MODELS


def test_chat_groq_turn_still_skips_gemini(offline):
    calls, responses = offline
    responses[llm.GROQ_CHAT_MODEL] = {"reply": "continued"}
    messages = [{"role": "user", "text": "hi"},
                {"role": "model", "provider": "groq", "model": llm.GROQ_CHAT_MODEL,
                 "raw": None, "text": "hello", "tool_calls": []}]
    reply = llm.chat(messages, [])
    assert reply.provider == "groq"
    assert [call[:2] for call in calls] == [("groq", llm.GROQ_CHAT_MODEL)]


def test_chat_tool_calls_and_thought_signatures_survive_cache(offline, monkeypatch):
    calls, _ = offline
    raw = {"role": "model", "parts": [
        {"thoughtSignature": "opaque-signature", "functionCall": {
            "id": "call-1", "name": "lookup", "args": {"query": "example"},
        }},
    ]}

    def post(provider, model, body):
        calls.append((provider, model, body))
        return {"candidates": [{"finishReason": "STOP", "content": raw}]}

    monkeypatch.setattr(llm, "_post", post)
    messages = [{"role": "user", "text": "look it up"}]
    tools = [{"name": "lookup", "description": "Look up", "parameters": {"type": "object"}}]
    reply = llm.chat(messages, tools, tool_choice="any")
    assert reply.tool_calls == [{"id": "call-1", "name": "lookup", "args": {"query": "example"}}]
    assert reply.text == "" and reply.turn["raw"] == raw
    assert llm.chat(messages, tools, tool_choice="any") == reply
    assert len(calls) == 1


def test_chat_continues_only_the_gemini_model_that_signed_the_turn(offline):
    calls, _ = offline
    model = llm.GEMINI_MODELS[1]
    messages = [{"role": "user", "text": "hello"},
                {"role": "model", "provider": "gemini", "model": model,
                 "raw": {"role": "model", "parts": [{"text": "hi", "thoughtSignature": "sig"}]},
                 "text": "hi", "tool_calls": []},
                {"role": "user", "text": "continue"}]
    reply = llm.chat(messages, [])
    assert reply.provider == "gemini" and reply.model == model
    assert [call[:2] for call in calls] == [("gemini", model)]
