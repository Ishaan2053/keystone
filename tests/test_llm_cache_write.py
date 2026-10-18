"""A failed LLM cache write never fails an answer the model already gave (issue #70)."""

import pytest

from api import llm
from tests.test_llm import SCHEMA, offline  # noqa: F401  (offline is a fixture)

SECRET = {"decisions": [{"summary": "secret-summary", "quote": "secret-quote"}]}


@pytest.fixture
def answered(offline):  # noqa: F811
    calls, responses = offline
    responses[llm.GEMINI_MODELS[0]] = SECRET
    return calls


def test_replace_failure_still_returns_answer_and_cleans_temp(answered, monkeypatch, capsys):
    def denied(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(llm.os, "replace", denied)
    answer, model = llm.generate_json("secret-prompt", SCHEMA)

    assert answer == SECRET and model == llm.GEMINI_MODELS[0]
    assert not list(llm.CACHE_DIR.glob("*"))  # no entry and no leftover temp file
    out = capsys.readouterr().out
    assert "cache write failed (PermissionError)" in out
    assert "secret" not in out


def test_unwritable_cache_dir_still_returns_answer(answered, capsys):
    llm.CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    llm.CACHE_DIR.write_text("a file where the cache directory should be", encoding="utf-8")

    answer, _ = llm.generate_json("secret-prompt", SCHEMA)

    assert answer == SECRET
    out = capsys.readouterr().out
    assert "cache write failed" in out and "secret" not in out


def test_concurrent_writers_use_distinct_temp_names(answered, monkeypatch):
    temps = []
    real_replace = llm.os.replace

    def spy(src, dst):
        temps.append(str(src))
        real_replace(src, dst)

    monkeypatch.setattr(llm.os, "replace", spy)
    llm.generate_json("secret-prompt", SCHEMA)
    (path,) = llm.CACHE_DIR.glob("*.json")
    path.unlink()  # a miss again, so the same key is written a second time
    llm.generate_json("secret-prompt", SCHEMA)

    assert len(temps) == 2 and temps[0] != temps[1]


def test_an_unreadable_cache_path_is_a_miss(answered, monkeypatch):
    # On Linux a file where the cache directory should be raises NotADirectoryError on read
    # (Windows raises FileNotFoundError), so any OSError on the lookup must count as a miss.
    def unreadable(self, *args, **kwargs):
        raise NotADirectoryError(20, "Not a directory")

    monkeypatch.setattr(llm.Path, "read_text", unreadable)
    answer, _ = llm.generate_json("secret-prompt", SCHEMA)

    assert answer == SECRET
