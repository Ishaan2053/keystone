"""Triage threads share one in-process FastMCP server (#68); no graph, network or LLM.

The live /triage route and the webhook worker each run the agent through ``asyncio.run`` in their
own thread, and every run opens ``fastmcp.Client(api.mcp_server.mcp)``. FastMCP guards the server's
lifespan with an ``asyncio.Lock`` and an unlocked ref count, so sessions from two event loops at
once can bind that lock to one loop and fail in the other, or hang. These tests drive the real
``agent._agent`` loop with ``llm.chat`` stubbed to fail after the session is open, which ends each
run right after ``list_tools`` (no tool ever reaches FalkorDB). Every wait and join is bounded.
"""
import asyncio
import sys
import threading
import time

from api import agent, llm
from api.mcp_server import mcp

ISSUE = agent.Issue(key=None, title="t", body="b")
JOIN_SECONDS = 15


class _ProbeLock:
    """Wraps the agent's session lock; ``contended`` is set once an acquire finds it held."""

    def __init__(self, inner):
        self.inner, self.contended = inner, threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        got = self.inner.acquire(blocking, timeout) if blocking else self.inner.acquire(False)
        if not got:
            self.contended.set()
        return got

    def release(self):
        self.inner.release()


def _wait_any(*events: threading.Event, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not any(e.is_set() for e in events) and time.monotonic() < deadline:
        time.sleep(0.005)


def _assert_lock_free() -> None:
    assert agent._MCP_SESSION.acquire(blocking=False), "the MCP session lock leaked"
    agent._MCP_SESSION.release()


def _run_agent(errors: list) -> None:
    """One triage's MCP session on this thread's own event loop, as /triage and the worker run it."""
    try:
        asyncio.run(agent._agent("t", ISSUE, None, agent._Budget()))
    except llm.LLMError:
        pass  # the stub ends every run here, as a failed LLM call does before the fallback
    except BaseException as exc:  # noqa: BLE001 - any other error is the bug under test
        errors.append(repr(exc))


def _join(threads: list[threading.Thread]) -> list[threading.Thread]:
    """Join with one shared deadline; returns the threads still running (a hang)."""
    deadline = time.monotonic() + JOIN_SECONDS
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return [t for t in threads if t.is_alive()]


def test_sessions_from_two_threads_never_overlap(monkeypatch):
    """The first run holds its session open until a second run either opens one too or finds it busy."""
    probe = _ProbeLock(agent._MCP_SESSION)
    monkeypatch.setattr(agent, "_MCP_SESSION", probe)
    lock, open_now, peak = threading.Lock(), [0], [0]
    first_in, second_in = threading.Event(), threading.Event()

    def chat(*_args, **_kwargs):
        with lock:
            open_now[0] += 1
            peak[0] = max(peak[0], open_now[0])
        if not first_in.is_set():
            first_in.set()
            _wait_any(second_in, probe.contended)
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
    assert first_in.wait(JOIN_SECONDS)
    b.start()
    assert _join([a, b]) == []
    assert errors == []
    assert peak[0] == 1, "two in-process MCP sessions were open at once on different event loops"
    assert second_in.is_set()  # the second run got its session once the first closed
    _assert_lock_free()


def test_two_sessions_on_one_loop_take_turns(monkeypatch):
    """asyncio.gather of two runs on one loop: the second waits without freezing the loop."""
    probe = _ProbeLock(agent._MCP_SESSION)
    monkeypatch.setattr(agent, "_MCP_SESSION", probe)
    calls: list[int] = []

    def chat(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            _wait_any(probe.contended)  # hold the first session until the second is waiting for it
        raise llm.LLMError("stub")

    monkeypatch.setattr(llm, "chat", chat)
    out: list = []

    async def both():
        out.extend(await asyncio.gather(
            agent._agent("t", ISSUE, None, agent._Budget()),
            agent._agent("t", ISSUE, None, agent._Budget()),
            return_exceptions=True))

    t = threading.Thread(target=lambda: asyncio.run(both()), daemon=True)
    t.start()
    assert _join([t]) == [], "two sessions on one event loop deadlocked"
    assert probe.contended.is_set()
    assert [type(r) for r in out] == [llm.LLMError, llm.LLMError]
    assert len(calls) == 2
    _assert_lock_free()


def test_a_busy_session_falls_back_and_leaves_the_lock_free(monkeypatch, caplog):
    """Past MCP_SESSION_WAIT_SECONDS the triage takes the no-LLM fallback instead of waiting on."""
    calls: list[int] = []
    monkeypatch.setattr(agent, "MCP_SESSION_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(agent, "_fallback", lambda tenant, issue, before: (
        {s: [] for s in agent.SECTIONS}, "Fallback triage: stub.", []))
    assert agent._MCP_SESSION.acquire(blocking=False)  # another triage's session
    try:
        with caplog.at_level("WARNING", logger="api.agent"):
            t = asyncio.run(agent.triage("t", ISSUE))
    finally:
        agent._MCP_SESSION.release()
    assert (t.fallback, t.model, t.llm_calls, calls) == (True, "none", 0, [])
    assert "agent busy" in caplog.text
    _assert_lock_free()


def test_many_threads_open_and_close_sessions_without_errors(monkeypatch):
    """A tiny switch interval preempts threads inside FastMCP's lifespan bookkeeping, which is
    what makes its asyncio.Lock contended across loops."""
    def chat(*_args, **_kwargs):
        raise llm.LLMError("stub")

    monkeypatch.setattr(llm, "chat", chat)
    errors: list = []
    threads = [threading.Thread(target=_run_agent_n, args=(errors, 20), daemon=True) for _ in range(4)]
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for t in threads:
            t.start()
        hung = _join(threads)
    finally:
        sys.setswitchinterval(interval)
    assert hung == [], f"a session hung; errors: {errors[:3]}"
    assert errors == []
    assert (mcp._lifespan_ref_count, mcp._lifespan_result_set) == (0, False)
    _assert_lock_free()


def _run_agent_n(errors: list, n: int) -> None:
    for _ in range(n):
        _run_agent(errors)
        if errors:
            return
