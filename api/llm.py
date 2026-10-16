"""The one LLM interface (CLAUDE.md stack): Gemini JSON mode over plain REST, cached on disk.

`generate_json` sends one prompt and returns the JSON object the model wrote against a schema.
Every response is cached in data/llm_cache/<sha256>.json, keyed by the model, system prompt,
prompt and schema (Rule 7), so a re-run costs no calls. Calls are throttled to the free tier.

Free-tier limits are not published; Google shows them per project in AI Studio. `RPM` is a
conservative guess, and the first 429 prints the real quota from the error's QuotaFailure.
Groq and function calling come in A9.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "llm_cache"
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Tried in order: the second takes over when the first is overloaded (503), times out or is out of quota.
# gemini-3.6-flash allows only 20 requests a day on the free tier, so the lighter model goes first.
MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash")
RPM = 10
TIMEOUT = 120
RETRIES = 4

_lock = threading.Lock()
_last_call = 0.0
_exhausted: set[str] = set()  # models whose daily quota ran out; skipped until the process restarts


class LLMError(Exception):
    """No model returned a usable answer; callers fall back (spec section 7)."""


def cache_key(model: str, prompt: str, schema: dict, system: str | None) -> str:
    blob = json.dumps({"model": model, "system": system, "prompt": prompt, "schema": schema},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def generate_json(prompt: str, schema: dict, system: str | None = None) -> tuple[dict, str]:
    """(the JSON object the model returned, the model that answered). Raises LLMError."""
    for model in MODELS:
        path = CACHE_DIR / f"{cache_key(model, prompt, schema, system)}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))["response"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):  # missing or damaged: a miss
            continue
        if isinstance(cached, dict):  # a non-object response is damaged too: a miss
            return cached, model
    errors = []
    for model in MODELS:
        if model in _exhausted:
            errors.append(f"{model}: daily quota exhausted earlier in this process")
            continue
        try:
            data = _call(model, prompt, schema, system)
        except LLMError as e:
            errors.append(f"{model}: {e}")
            continue
        path = CACHE_DIR / f"{cache_key(model, prompt, schema, system)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")  # write then rename, so a crash never leaves half an entry
        tmp.write_text(json.dumps({"model": model, "response": data}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return data, model
    raise LLMError("; ".join(errors))


def _throttle() -> None:
    """At most RPM calls a minute, across threads."""
    global _last_call
    with _lock:
        wait = _last_call + 60 / RPM - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _call(model: str, prompt: str, schema: dict, system: str | None) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY is not set")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    for attempt in range(RETRIES):
        _throttle()
        try:
            res = httpx.post(URL.format(model=model), json=body, timeout=TIMEOUT,
                             headers={"x-goog-api-key": key})
        except httpx.TimeoutException:
            raise LLMError(f"timed out after {TIMEOUT}s")
        except httpx.HTTPError as e:
            raise LLMError(f"request failed: {type(e).__name__}")
        try:
            payload = res.json()
        except ValueError:
            payload = {}
        if res.status_code == 200:
            return _parse(payload if isinstance(payload, dict) else {})
        error = (payload.get("error") if isinstance(payload, dict) else None) or {}
        if res.status_code == 429:
            quotas, delay = _quota(error)
            print(f"  [llm] {model} 429, quota {quotas or 'unknown'}, retry in {delay:.0f}s")
            if any("PerDay" in q for q in quotas):
                _exhausted.add(model)
                raise LLMError(f"daily quota exhausted ({', '.join(quotas)})")
            time.sleep(delay)
            continue
        if res.status_code in (500, 503) and attempt < RETRIES - 1:
            time.sleep(10 * (attempt + 1))
            continue
        raise LLMError(f"HTTP {res.status_code}: {error.get('message', res.text[:200])}")
    raise LLMError(f"still rate-limited after {RETRIES} tries")


def _quota(error: dict) -> tuple[list[str], float]:
    """The violated quotas ("<quotaId>=<quotaValue>") and the suggested retry delay in seconds."""
    quotas, delay = [], 30.0
    for detail in error.get("details", []):
        kind = detail.get("@type", "")
        if kind.endswith("QuotaFailure"):
            quotas += [f"{v.get('quotaId')}={v.get('quotaValue')}" for v in detail.get("violations", [])]
        elif kind.endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", detail.get("retryDelay", ""))
            if m:
                delay = float(m[1]) + 1
    return quotas, delay


def _parse(payload: dict) -> dict:
    """The JSON object in the first candidate's text parts (thought parts skipped)."""
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"no candidates ({payload.get('promptFeedback', {}).get('blockReason', 'no reason given')})")
    cand = candidates[0]
    if cand.get("finishReason") not in (None, "STOP"):
        raise LLMError(f"finishReason {cand.get('finishReason')}")
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError("response was not valid JSON")
    if not isinstance(data, dict):
        raise LLMError("response was not a JSON object")
    return data
