"""Triage threads share one in-process FastMCP server (#68); no graph, network or LLM.

The live /triage route and the webhook worker each run the agent through ``asyncio.run`` in their
own thread, and every run opens ``fastmcp.Client(api.mcp_server.mcp)``. FastMCP guards the server's
lifespan with an ``asyncio.Lock`` and an unlocked ref count, so sessions from two event loops at
once can bind that lock to one loop and fail in the other, or hang. These tests drive the real
``agent._agent`` loop from two or more threads with ``llm.chat`` stubbed to fail after the session
is open, which ends each run right after ``list_tools`` (no tool ever reaches FalkorDB).
"""
import asyncio
import sys
import threading
import time

from api import agent, llm
from api.mcp_server import mcp

ISSUE = agent.Issue(key=None, title="t", body="b")


def _run_agent(errors: list) -> None:
    """One triage's MCP session on this thread's own event loop, as /triage and the worker run it."""
    try:
        asyncio.run(agent._agent("t", ISSUE, None, agent._Budget()))
    except llm.LLMError:
        pass  # the stub ends every run here, as a failed LLM call does before the fallback
    except BaseException as exc:  # noqa: BLE001 - any other error is the bug under test
        errors.append(repr(exc))


def test_sessions_from_two_threads_never_overlap(monkeypatch):
    """The first run holds its session open until a second run's session opens too, or 1 s passes."""
    lock, open_now, peak = threading.Lock(), [0], [0]
    first_in, second_in = threading.Event(), threading.Event()

    def chat(*_args, **_kwargs):
        with lock:
            open_now[0] += 1
            peak[0] = max(peak[0], open_now[0])
        if not first_in.is_set():
            first_in.set()
            second_in.wait(1.0)
        else:
            second_in.set()
        with lock:
            open_now[0] -= 1
        raise llm.LLMError("stub")

    monkeypatch.setattr(llm, "chat", chat)
    errors: list = []
    a = threading.Thread(target=_run_agent, args=(errors,), daemon=True)
    b = threading.Thread(target=_run_agent, args=(errors,), daemon=True)
    a.start()
    assert first_in.wait(10)
    b.start()
    a.join(10)
    b.join(10)
    assert not a.is_alive() and not b.is_alive()
    assert errors == []
    assert peak[0] == 1, "two in-process MCP sessions were open at once on different event loops"


def test_many_threads_open_and_close_sessions_without_errors(monkeypatch):
    """A tiny switch interval preempts threads inside FastMCP's lifespan bookkeeping, which is
    what makes its asyncio.Lock contended across loops."""
    def chat(*_args, **_kwargs):
        raise llm.LLMError("stub")

    monkeypatch.setattr(llm, "chat", chat)
    errors: list = []
    threads = [threading.Thread(target=_run_agent_n, args=(errors, 40), daemon=True) for _ in range(4)]
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for t in threads:
            t.start()
        deadline = time.monotonic() + 60
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
    finally:
        sys.setswitchinterval(interval)
    assert [t for t in threads if t.is_alive()] == [], f"a session hung; errors: {errors[:3]}"
    assert errors == []
    assert (mcp._lifespan_ref_count, mcp._lifespan_result_set) == (0, False)


def _run_agent_n(errors: list, n: int) -> None:
    for _ in range(n):
        _run_agent(errors)
        if errors:
            return
