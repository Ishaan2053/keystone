"""The one LLM interface (CLAUDE.md stack): Gemini over plain REST, Groq as the fallback, cached on disk.

Two entry points, each tried on Gemini first and then on Groq's OpenAI-compatible endpoint:
- `chat` sends a conversation and a tool list (function calling) and returns an `LLMReply`: tool calls
  as {id, name, args}, the final text, and the provider and model that answered. The agent (A10) uses it.
- `generate_json` sends one prompt and returns the JSON object the model wrote against a schema.

Every response is cached in data/llm_cache/<sha256>.json, keyed by the model and the whole request
(Rule 7), so a re-run costs no calls. LLM_CACHE=off skips the cache both ways, to test the no-LLM
fallback. `network_calls()` counts the HTTP requests this process sent. Calls are throttled per provider.

Free-tier limits are not published; Google shows them per project in AI Studio. `RPM` is a
conservative guess, and the first 429 prints the real quota from the error's QuotaFailure.

A conversation is a list of turns:
    {"role": "user", "text": str}
    {"role": "model", ...}                      # an LLMReply's `turn`, appended as is
    {"role": "tool", "results": [{"id", "name", "result"}]}
A tool is {"name", "description", "parameters"} with `parameters` as JSON Schema (an MCP inputSchema).
Gemini 3 rejects a follow-up whose model turns lack their thought signatures, so a model turn keeps
Gemini's content verbatim in `raw`. A turn without `raw` (Groq's) makes chat skip Gemini from then on,
and signatures are per model, so only the Gemini model that wrote the turns may continue them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "llm_cache"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Tried in order: the next takes over when one is overloaded (503), times out or is out of quota.
# gemini-3.6-flash allows only 20 requests a day on the free tier, so the lighter model goes first.
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash")
# llama-3.3-70b supports parallel tool calls but only json_object mode; gpt-oss-120b has json_schema
# but no parallel tool calls. Groq does not allow tools and response_format in one request.
GROQ_CHAT_MODEL = "llama-3.3-70b-versatile"
GROQ_JSON_MODEL = "openai/gpt-oss-120b"
CHAT_MODELS = [("gemini", m) for m in GEMINI_MODELS] + [("groq", GROQ_CHAT_MODEL)]
JSON_MODELS = [("gemini", m) for m in GEMINI_MODELS] + [("groq", GROQ_JSON_MODEL)]
KEYS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}
RPM = {"gemini": 10, "groq": 30}
TIMEOUT = 120
RETRIES = 4

_lock = threading.Lock()
_last_call: dict[str, float] = {}
_network_calls = 0
_exhausted: set[str] = set()  # models whose daily quota ran out; skipped until the process restarts


class LLMError(Exception):
    """No model returned a usable answer; callers fall back (spec section 7)."""


@dataclass
class LLMReply:
    tool_calls: list[dict]  # [{"id", "name", "args"}], in the order the model wrote them
    text: str               # the model's text, empty when it only called tools
    provider: str           # "gemini" or "groq"
    model: str
    raw: dict | None = None  # Gemini's content, sent back verbatim for its thought signatures

    @property
    def turn(self) -> dict:
        """This reply as a conversation turn, to append before sending the tool results."""
        return {"role": "model", "text": self.text, "tool_calls": self.tool_calls,
                "provider": self.provider, "model": self.model, "raw": self.raw}


def network_calls() -> int:
    """HTTP requests sent to any provider in this process, retries included."""
    return _network_calls


def cache_on() -> bool:
    return os.environ.get("LLM_CACHE", "on").strip().lower() != "off"


def cache_key(model: str, prompt: str, schema: dict, system: str | None) -> str:
    """The key for a generate_json request (unchanged since A7, so its cache still hits)."""
    return _hash({"model": model, "system": system, "prompt": prompt, "schema": schema})


def chat_key(model: str, messages: list[dict], tools: list[dict], system: str | None, tool_choice: str) -> str:
    """The key for a chat request: what the model sees, without Gemini's opaque signatures."""
    seen = [{k: v for k, v in t.items() if k not in ("raw", "provider", "model")} for t in messages]
    return _hash({"model": model, "system": system, "messages": seen, "tools": tools, "tool_choice": tool_choice})


def _hash(request: dict) -> str:
    blob = json.dumps(request, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def chat(messages: list[dict], tools: list[dict], system: str | None = None,
         tool_choice: str = "auto") -> LLMReply:
    """One model turn: tool calls, text or both. tool_choice is "auto", "any" (must call) or "none".
    Raises LLMError.

    Gemini does not honour "none" (seen 2026-10-16 on gemini-3.5-flash-lite): after tool results it still
    returned a functionCall, to a tool that was never declared, even with no tools sent. Callers check
    every call's name against their tool list, and ask for the final answer with generate_json."""
    models = [(p, m) for p, m in CHAT_MODELS if p != "gemini" or all(  # the Gemini model that wrote every turn
        t.get("provider") == p and t.get("model") == m and isinstance(t.get("raw"), dict)
        for t in messages if t["role"] == "model")]

    def call(provider: str, model: str) -> dict:
        if provider == "gemini":
            return _gemini_chat(model, messages, tools, system, tool_choice)
        return _groq_chat(model, messages, tools, system, tool_choice)

    try:  # the key is computed before any request, so this is the one place it can fail
        candidates = [(p, m, chat_key(m, messages, tools, system, tool_choice)) for p, m in models]
    except TypeError as e:
        raise LLMError(f"messages or tools are not JSON-serializable: {e}")
    answer, provider, model = _first_answer(candidates, call, _is_reply)
    return LLMReply(answer["tool_calls"], answer["text"], provider, model, answer.get("raw"))


def generate_json(prompt: str, schema: dict, system: str | None = None) -> tuple[dict, str]:
    """(the JSON object the model returned, the model that answered). Raises LLMError."""
    def call(provider: str, model: str) -> dict:
        if provider == "gemini":
            return _gemini_json(model, prompt, schema, system)
        return _groq_json(model, prompt, schema, system)

    answer, _, model = _first_answer(
        [(p, m, cache_key(m, prompt, schema, system)) for p, m in JSON_MODELS], call,
        lambda r: isinstance(r, dict))
    return answer, model


def _is_reply(r) -> bool:
    return (isinstance(r, dict) and isinstance(r.get("text"), str) and isinstance(r.get("tool_calls"), list)
            and all(isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("args"), dict)
                    for c in r["tool_calls"]))


def _first_answer(candidates: list[tuple[str, str, str]], call: Callable[[str, str], dict],
                  valid: Callable[[object], bool]) -> tuple[dict, str, str]:
    """(answer, provider, model): a cache hit from any candidate first, then each candidate over the network."""
    use_cache = cache_on()
    if use_cache:
        for provider, model, key in candidates:
            try:
                cached = json.loads((CACHE_DIR / f"{key}.json").read_text(encoding="utf-8"))["response"]
            except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):  # missing or damaged: a miss
                continue
            if valid(cached):  # a wrongly shaped response is damaged too: a miss
                return cached, provider, model
    errors = []
    for provider, model, key in candidates:
        if model in _exhausted:
            errors.append(f"{provider}/{model}: daily quota exhausted earlier in this process")
            continue
        try:
            answer = call(provider, model)
        except LLMError as e:
            errors.append(f"{provider}/{model}: {e}")
            continue
        if use_cache:
            path = CACHE_DIR / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")  # write then rename, so a crash never leaves half an entry
            tmp.write_text(json.dumps({"model": model, "response": answer}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        return answer, provider, model
    raise LLMError("; ".join(errors))


# --- Gemini (generateContent) ---

def _gemini_json(model: str, prompt: str, schema: dict, system: str | None) -> dict:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    parts = _gemini_parts(_post("gemini", model, body))
    return _json_object("".join(p.get("text", "") for p in parts if not p.get("thought")))


def _gemini_chat(model: str, messages: list[dict], tools: list[dict], system: str | None,
                 tool_choice: str) -> dict:
    body = {"contents": _gemini_contents(messages)}
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parametersJsonSchema": t["parameters"]}
            for t in tools]}]
        body["toolConfig"] = {"functionCallingConfig": {"mode": tool_choice.upper()}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    parts = _gemini_parts(_post("gemini", model, body))
    calls = []
    for p in parts:
        fc = p.get("functionCall")
        if fc is None:
            continue
        if not isinstance(fc.get("name"), str) or not isinstance(fc.get("args", {}), dict):
            raise LLMError("malformed functionCall")
        calls.append({"id": fc.get("id") or f"call_{uuid.uuid4().hex[:12]}", "name": fc["name"],
                      "args": fc.get("args") or {}})
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not calls and not text:
        raise LLMError("empty response")
    return {"tool_calls": calls, "text": text, "raw": {"role": "model", "parts": parts}}


def _gemini_contents(messages: list[dict]) -> list[dict]:
    contents = []
    ids: set[str] = set()  # call ids Gemini itself issued; only those are echoed back
    for t in messages:
        if t["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": t["text"]}]})
        elif t["role"] == "model":
            contents.append(t["raw"])
            ids |= {p["functionCall"]["id"] for p in t["raw"].get("parts", [])
                    if isinstance(p.get("functionCall"), dict) and p["functionCall"].get("id")}
        else:
            parts = []
            for r in t["results"]:
                fr = {"name": r["name"],
                      "response": r["result"] if isinstance(r["result"], dict) else {"result": r["result"]}}
                if r.get("id") in ids:
                    fr["id"] = r["id"]
                parts.append({"functionResponse": fr})
            contents.append({"role": "user", "parts": parts})
    return contents


def _gemini_parts(payload: dict) -> list[dict]:
    """The first candidate's parts, thought signatures included."""
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"no candidates ({payload.get('promptFeedback', {}).get('blockReason', 'no reason given')})")
    cand = candidates[0]
    if cand.get("finishReason") not in (None, "STOP"):  # e.g. MALFORMED_FUNCTION_CALL, MISSING_THOUGHT_SIGNATURE
        raise LLMError(f"finishReason {cand.get('finishReason')}")
    return (cand.get("content") or {}).get("parts") or []


# --- Groq (OpenAI-compatible chat completions) ---

def _groq_json(model: str, prompt: str, schema: dict, system: str | None) -> dict:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages,  # no "strict": best-effort, so any schema is accepted
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}}
    message = _groq_message(_post("groq", model, body))
    return _json_object(message.get("content") or "")


def _groq_chat(model: str, messages: list[dict], tools: list[dict], system: str | None,
               tool_choice: str) -> dict:
    body = {"model": model, "messages": _groq_messages(messages, system)}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        body["tool_choice"] = {"any": "required"}.get(tool_choice, tool_choice)
    message = _groq_message(_post("groq", model, body))
    calls = []
    for c in message.get("tool_calls") or []:
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}") or {}
        except json.JSONDecodeError:
            raise LLMError("tool call arguments were not valid JSON")
        if not isinstance(fn.get("name"), str) or not isinstance(args, dict):
            raise LLMError("malformed tool call")
        calls.append({"id": c.get("id") or f"call_{uuid.uuid4().hex[:12]}", "name": fn["name"], "args": args})
    text = message.get("content") or ""
    if not calls and not text:
        raise LLMError("empty response")
    return {"tool_calls": calls, "text": text}


def _groq_messages(messages: list[dict], system: str | None) -> list[dict]:
    # Tool messages carry no "name": Groq answers 400 to messages[].name.
    out = [{"role": "system", "content": system}] if system else []
    for t in messages:
        if t["role"] == "user":
            out.append({"role": "user", "content": t["text"]})
        elif t["role"] == "model":
            m = {"role": "assistant"}
            if t["text"]:
                m["content"] = t["text"]
            if t["tool_calls"]:
                m["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                   for c in t["tool_calls"]]
            out.append(m)
        else:
            out += [{"role": "tool", "tool_call_id": r["id"], "content": json.dumps(r["result"], ensure_ascii=False)}
                    for r in t["results"]]
    return out


def _groq_message(payload: dict) -> dict:
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError("no choices")
    finish = choices[0].get("finish_reason")
    if finish not in ("stop", "tool_calls"):
        raise LLMError(f"finish_reason {finish}")
    return choices[0].get("message") or {}


# --- HTTP ---

def _throttle(provider: str) -> None:
    """At most RPM[provider] calls a minute, across threads; counts every request sent."""
    global _network_calls
    with _lock:  # reserve the next slot, then wait outside the lock so one provider never blocks the other
        now = time.monotonic()
        slot = max(now, _last_call.get(provider, 0.0) + 60 / RPM[provider])
        _last_call[provider] = slot
        _network_calls += 1
    time.sleep(slot - now)


def _post(provider: str, model: str, body: dict) -> dict:
    """The 200 response body. Retries a 5xx or Groq's tool_use_failed once, and short rate limits;
    a daily quota marks the model exhausted."""
    key = os.environ.get(KEYS[provider])
    if not key:
        raise LLMError(f"{KEYS[provider]} is not set")
    if provider == "gemini":
        url, headers = GEMINI_URL.format(model=model), {"x-goog-api-key": key}
    else:
        url, headers = GROQ_URL, {"Authorization": f"Bearer {key}"}
    for attempt in range(RETRIES):
        _throttle(provider)
        try:
            res = httpx.post(url, json=body, timeout=TIMEOUT, headers=headers)
        except httpx.TimeoutException:
            raise LLMError(f"timed out after {TIMEOUT}s")
        except httpx.HTTPError as e:
            raise LLMError(f"request failed: {type(e).__name__}")
        try:
            payload = res.json()
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        if res.status_code == 200:
            return payload
        error = payload.get("error") or {}
        error = error if isinstance(error, dict) else {"message": str(error)}
        if res.status_code == 429:
            quotas, delay, daily = _rate_limit(provider, error, res.headers)
            retry = f", retry in {delay:.0f}s" if not daily and attempt < RETRIES - 1 else ""
            print(f"  [llm] {provider}/{model} 429, quota {quotas or 'unknown'}{retry}")
            if daily:
                _exhausted.add(model)
                raise LLMError(f"daily quota exhausted ({', '.join(quotas) or error.get('message', '')})")
            if attempt == RETRIES - 1:
                break
            time.sleep(delay)
            continue
        if res.status_code in (500, 502, 503) and attempt == 0:  # once only: an overloaded model hands over fast
            time.sleep(5)
            continue
        if res.status_code == 400 and error.get("code") == "tool_use_failed" and attempt == 0:  # a malformed tool call
            continue  # from llama is transient: once more on the same model
        raise LLMError(f"HTTP {res.status_code}: {error.get('message', res.text[:200])}")
    raise LLMError(f"still rate-limited after {RETRIES} tries")


def _rate_limit(provider: str, error: dict, headers: httpx.Headers) -> tuple[list[str], float, bool]:
    """(the violated quotas, the suggested retry delay in seconds, whether a daily quota ran out)."""
    if provider == "groq":  # the message names the limit, e.g. "... on requests per day (RPD): Limit 1000"
        message = error.get("message", "")
        try:
            delay = float(headers.get("retry-after", "")) + 1
        except ValueError:
            delay = 30.0
        return [message] if message else [], delay, "per day" in message
    quotas, delay = [], 30.0
    for detail in error.get("details", []):
        kind = detail.get("@type", "")
        if kind.endswith("QuotaFailure"):
            quotas += [f"{v.get('quotaId')}={v.get('quotaValue')}" for v in detail.get("violations", [])]
        elif kind.endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", detail.get("retryDelay", ""))
            if m:
                delay = float(m[1]) + 1
    return quotas, delay, any("PerDay" in q for q in quotas)


def _json_object(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError("response was not valid JSON")
    if not isinstance(data, dict):
        raise LLMError("response was not a JSON object")
    return data
