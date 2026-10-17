"""Hosted MCP admission: the six read-only graph tools only, public tenants only.

Deterministic and dependency-free. Every test injects a local FastMCP server with
stub tools and inspectable counters, and monkeypatches the environment, so nothing
here reaches FalkorDB, embeddings, GitHub, an LLM, the network or the A-owned
api.mcp_server singletons (imported read-only for the regression check).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from api.config import Settings, load_settings
from api.main import create_app
from api.mcp_http import (
    _AdmissionApp,
    READ_ONLY_TOOL_NAMES,
    ToolCallLimiter,
    build_mcp_http,
)
from api.schemas import ToolResult

READ_ONLY = {"readOnlyHint": True}
ACCEPT = {"Accept": "application/json, text/event-stream"}
CLIENT_IP_HEADER = "X-Keystone-Client-IP"
DEFAULT_IP = "203.0.113.7"
SIX = {
    "affected_modules",
    "rank_reviewers",
    "bus_factor",
    "find_decisions",
    "explain_path",
    "recall_run",
}
SEVEN = SIX | {"apply_triage"}

# Argument sets that satisfy each read-only tool's real signature.
VALID_ARGS = {
    "affected_modules": {"tenant": "falkordb", "title": "Crash with OPTIONAL MATCH"},
    "rank_reviewers": {"tenant": "falkordb", "module_key": "module:a/b/c", "k": 3},
    "bus_factor": {"tenant": "falkordb", "module_key": "module:a/b/c"},
    "find_decisions": {"tenant": "falkordb", "query": "query timeouts"},
    "explain_path": {"tenant": "falkordb", "from_key": "issue:a/b#1", "to_key": "person:x"},
    "recall_run": {"tenant": "falkordb", "run_key": "run:1"},
}


class StubServer:
    """A local six-tool FastMCP server, optionally exposing action/query extras.

    Every stub returns a valid ToolResult and records its call, so a test can prove
    what executed without a graph, embeddings or the shared singletons.
    """

    def __init__(self, *, extras: bool = False, explode: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str]] = []
        self.actions: list[tuple[str, str, bool]] = []
        self.queries: list[tuple[str, str]] = []
        self.adapter_calls = 0
        self._explode = explode
        self.server = FastMCP("stub-hosted-mcp")
        self._register_read_only()
        if extras:
            self._register_extras()

    def _record(self, tool: str, tenant: str) -> ToolResult:
        self.adapter_calls += 1
        self.calls.append((tool, tenant))
        if tool in self._explode:
            raise RuntimeError(f"{tool} exploded")
        return ToolResult(
            tool=tool,
            data={"tool": tool},
            cypher=["RETURN 1"],
            params={"tenant": tenant},
            evidence=[f"module:stub/{tool}"],
            ms=1,
            empty=False,
        )

    def _register_read_only(self) -> None:
        server = self.server

        @server.tool(annotations=READ_ONLY)
        def affected_modules(tenant: str, title: str, body: str = "") -> ToolResult:
            return self._record("affected_modules", tenant)

        @server.tool(annotations=READ_ONLY)
        def rank_reviewers(
            tenant: str,
            module_key: str,
            k: int = 5,
            before: int | None = None,
            exclude_logins: list[str] | None = None,
        ) -> ToolResult:
            return self._record("rank_reviewers", tenant)

        @server.tool(annotations=READ_ONLY)
        def bus_factor(
            tenant: str, module_key: str, since: int | None = None, before: int | None = None
        ) -> ToolResult:
            return self._record("bus_factor", tenant)

        @server.tool(annotations=READ_ONLY)
        def find_decisions(tenant: str, query: str) -> ToolResult:
            return self._record("find_decisions", tenant)

        @server.tool(annotations=READ_ONLY)
        def explain_path(tenant: str, from_key: str, to_key: str) -> ToolResult:
            return self._record("explain_path", tenant)

        @server.tool(annotations=READ_ONLY)
        def recall_run(tenant: str, run_key: str) -> ToolResult:
            return self._record("recall_run", tenant)

    def _register_extras(self) -> None:
        server = self.server

        @server.tool
        def apply_triage(tenant: str, run_key: str, dry_run: bool = True) -> ToolResult:
            self.actions.append((tenant, run_key, dry_run))
            return self._record("apply_triage", tenant)

        @server.tool
        def query(tenant: str, text: str) -> ToolResult:
            self.queries.append((tenant, text))
            return self._record("query", tenant)


def _app(server=None, *, tenants=("falkordb",), public=("falkordb",), clock=None):
    settings = Settings(tenants=tenants, public_mcp_tenants=public)
    return build_mcp_http(settings, server=server, clock=clock or time.monotonic)


def _headers(ip: str = DEFAULT_IP, **extra: str) -> dict[str, str]:
    """The Accept header plus the single trusted client-IP header Caddy sets."""
    headers = {**ACCEPT, **extra}
    if ip is not None:
        headers[CLIENT_IP_HEADER] = ip
    return headers


def _post(client: TestClient, payload, *, ip: str = DEFAULT_IP, headers=None):
    return client.post("/mcp", json=payload, headers=headers or _headers(ip))


def _post_raw(client: TestClient, content: str, *, ip: str = DEFAULT_IP, headers=None):
    return client.post(
        "/mcp",
        content=content,
        headers=headers or _headers(ip, **{"Content-Type": "application/json"}),
    )


def _call(rid, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _notify(name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _initialize(rid: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


def _list_tools(rid: int) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {}}


class FakeClock:
    """A settable monotonic clock: the only source of time in the limiter."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _bus_factor(rid, tenant: str = "falkordb") -> dict:
    return _call(rid, "bus_factor", {"tenant": tenant, "module_key": "module:a/b/c"})


def _explode(*args, **kwargs):
    raise AssertionError("hosted MCP reached a graph/embedding adapter")


@pytest.mark.parametrize("name", [[], {}, None, True, False, 42, 1.5])
@pytest.mark.parametrize("rid", [0, "request-1"])
def test_invalid_tool_names_are_controlled_and_charged(monkeypatch, name, rid):
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    with TestClient(_app(stubs.server), raise_server_exceptions=False) as client:
        for _ in range(30):
            response = _post(client, _call(rid, name, {"tenant": "falkordb"}))
            assert response.status_code == 200
            assert response.json() == {
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": "Unknown tool"},
            }
        assert _post(client, _bus_factor(31)).status_code == 429
    assert stubs.calls == []


async def _upload(messages, *, headers=(), exhausted=False, timeout=None, tool_delay=0):
    """Exercise admission with real ASGI chunks, isolating only downstream execution."""
    forwarded = []
    responses = []
    reads = 0

    async def transport(scope, receive, send):
        forwarded.append((await receive())["body"])
        await asyncio.sleep(tool_delay)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        nonlocal reads
        delay, message = messages[reads]
        reads += 1
        await asyncio.sleep(delay)
        return message

    async def send(message):
        responses.append(message)

    limiter = ToolCallLimiter(FakeClock())
    if exhausted:
        for _ in range(30):
            limiter.admit(DEFAULT_IP)
    kwargs = {} if timeout is None else {"body_upload_timeout": timeout}
    app = _AdmissionApp(transport, public_tenants=frozenset({"falkordb"}), limiter=limiter,
                        **kwargs)
    await app({"type": "http", "method": "POST", "path": "/mcp",
               "headers": [(b"x-keystone-client-ip", DEFAULT_IP.encode()), *headers]},
              receive, send)
    statuses = [m["status"] for m in responses if m["type"] == "http.response.start"]
    return statuses, forwarded, reads


def _chunk(body, more=False, delay=0):
    return delay, {"type": "http.request", "body": body, "more_body": more}


@pytest.mark.parametrize("headers", [(), ((b"content-length", b"1"),),
                                     ((b"content-length", b"1048577"),)])
@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("single_chunk", [False, True])
def test_upload_overflow_stops_receiving_before_dispatch(headers, exhausted, single_chunk):
    body = json.dumps(_bus_factor(1)).encode()
    if single_chunk:
        messages = [_chunk(body + b" " * (1048577 - len(body)), True), _chunk(b" ")]
    else:
        messages = [_chunk(body + b" " * (1048576 - len(body)), True),
                    _chunk(b" ", True), _chunk(b" ")]
    statuses, forwarded, reads = asyncio.run(_upload(messages, headers=headers,
                                                    exhausted=exhausted))
    assert statuses == [413]
    assert forwarded == []
    assert reads == (1 if single_chunk else 2)


@pytest.mark.parametrize("split", [False, True])
def test_exact_upload_bound_replays_bytes(split):
    body = json.dumps(_bus_factor(1)).encode()
    body += b" " * (1048576 - len(body))
    messages = [_chunk(body[:500000], True), _chunk(body[500000:])] if split else [_chunk(body)]
    statuses, forwarded, _ = asyncio.run(_upload(messages))
    assert statuses == [200]
    assert forwarded == [body]


@pytest.mark.parametrize("exhausted", [False, True])
def test_upload_deadline_is_total_not_reset_by_incremental_chunks(exhausted):
    # Every interval is shorter than the deadline, but the whole upload is not.
    body = json.dumps(_bus_factor(1)).encode()
    messages = [_chunk(body, True, .02), _chunk(b" ", True, .02),
                _chunk(b" ", True, .02), _chunk(b" ", False, .02)]
    statuses, forwarded, reads = asyncio.run(_upload(messages, timeout=.05,
                                                    exhausted=exhausted))
    assert statuses == [408]
    assert forwarded == []
    assert reads < 4


def test_upload_deadline_bounds_wait_for_first_chunk():
    statuses, forwarded, reads = asyncio.run(_upload([_chunk(b"", delay=1)], timeout=.01))
    assert statuses == [408]
    assert forwarded == []
    assert reads == 1


def test_factory_exposes_injectable_upload_deadline(monkeypatch):
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    app = build_mcp_http(
        Settings(tenants=("falkordb",), public_mcp_tenants=("falkordb",)),
        server=stubs.server, body_upload_timeout=0,
    )
    with TestClient(app) as client:
        assert _post(client, _bus_factor(1)).status_code == 408
    assert stubs.calls == []


def test_upload_timeout_does_not_limit_tool_execution():
    body = json.dumps(_bus_factor(1)).encode()
    statuses, forwarded, _ = asyncio.run(_upload([_chunk(body)], timeout=.01, tool_delay=.03))
    assert statuses == [200]
    assert forwarded == [body]


def test_upload_disconnect_never_dispatches_even_parseable_partial_body():
    body = json.dumps(_bus_factor(1)).encode()
    statuses, forwarded, reads = asyncio.run(_upload([
        _chunk(body, True), (0, {"type": "http.disconnect"})]))
    assert forwarded == []
    assert statuses == []
    assert reads == 2


def test_http_lists_exactly_six_tools(monkeypatch):
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()

    with TestClient(_app(stubs.server)) as client:
        response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == SIX
    assert READ_ONLY_TOOL_NAMES == frozenset(SIX)
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)


@pytest.mark.parametrize("name", sorted(SIX))
def test_http_admits_each_read_only_tool(monkeypatch, name):
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()

    with TestClient(_app(stubs.server)) as client:
        response = _post(client, _call(7, name, VALID_ARGS[name]))

    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    result = ToolResult.model_validate(body["result"]["structuredContent"])
    assert result.tool == name
    assert result.evidence == [f"module:stub/{name}"]
    assert result.params["tenant"] == "falkordb"
    assert stubs.calls == [(name, "falkordb")]


def test_http_never_calls_apply_or_arbitrary_query(monkeypatch):
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer(extras=True)
    monkeypatch.setattr("api.tools._run.graph", _explode)
    monkeypatch.setattr("api.embeddings.model", _explode)

    attempts = [
        (1, "apply_triage", {"tenant": "falkordb", "run_key": "run:1", "dry_run": True}),
        (2, "apply_triage", {"tenant": "falkordb", "run_key": "run:1", "dry_run": False}),
        (3, "query", {"tenant": "falkordb", "text": "MATCH (n) RETURN n"}),
        (4, "recall_the_run_or_anything_else", {"tenant": "falkordb"}),
    ]
    with TestClient(_app(stubs.server)) as client:
        for rid, name, arguments in attempts:
            response = _post(client, _call(rid, name, arguments))

            assert response.status_code == 200
            body = response.json()
            assert body["id"] == rid
            assert "result" not in body
            assert body["error"]["code"] == -32601

    assert stubs.calls == []
    assert stubs.actions == []
    assert stubs.queries == []
    assert stubs.adapter_calls == 0


def test_http_default_server_rejects_the_action_tool(monkeypatch):
    """The production default (graph_tools) still refuses apply_triage over HTTP."""
    monkeypatch.setenv("TENANTS", "falkordb")
    monkeypatch.setattr("api.tools._run.graph", _explode)
    monkeypatch.setattr("api.embeddings.model", _explode)

    with TestClient(_app()) as client:
        response = _post(
            client,
            _call(1, "apply_triage", {"tenant": "falkordb", "run_key": "run:1", "dry_run": True}),
        )

    body = response.json()
    assert "result" not in body
    assert body["error"]["code"] == -32601


def test_http_tenant_admission(monkeypatch):
    monkeypatch.setenv("TENANTS", "falkordb,private")
    stubs = StubServer()
    monkeypatch.setattr("api.tools._run.graph", _explode)
    monkeypatch.setattr("api.embeddings.model", _explode)

    settings = Settings(tenants=("falkordb", "private"), public_mcp_tenants=("falkordb",))
    app = build_mcp_http(settings, server=stubs.server)

    rejected = [
        {"module_key": "module:a/b/c"},  # missing
        {"tenant": None, "module_key": "module:a/b/c"},  # null
        {"tenant": ["falkordb"], "module_key": "module:a/b/c"},  # list
        {"tenant": {"slug": "falkordb"}, "module_key": "module:a/b/c"},  # object
        {"tenant": True, "module_key": "module:a/b/c"},  # non-string
        {"tenant": "private", "module_key": "module:a/b/c"},  # configured but private
        {"tenant": "pallets", "module_key": "module:a/b/c"},  # unconfigured
        {"tenant": "../private", "module_key": "module:a/b/c"},  # path-like
        {"tenant": "falkordb/../private", "module_key": "module:a/b/c"},  # path-like
    ]

    with TestClient(app) as client:
        admitted = _post(
            client, _call(1, "bus_factor", {"tenant": "falkordb", "module_key": "module:a/b/c"})
        )
        assert admitted.status_code == 200 and "error" not in admitted.json()
        assert stubs.calls == [("bus_factor", "falkordb")]
        executed = list(stubs.calls)
        adapters = stubs.adapter_calls

        for rid, arguments in enumerate(rejected, start=2):
            response = _post(client, _call(rid, "bus_factor", arguments))

            body = response.json()
            assert body["id"] == rid
            assert "result" not in body
            assert body["error"]["code"] == -32602
            assert body["error"]["message"] == "Public tenant unavailable"
            assert stubs.calls == executed
            assert stubs.adapter_calls == adapters


def test_public_tenant_configuration(monkeypatch):
    # Absent or blank PUBLIC_MCP_TENANTS means every configured tenant (all public orgs).
    monkeypatch.delenv("PUBLIC_MCP_TENANTS", raising=False)
    monkeypatch.setenv("TENANTS", "falkordb,pallets")
    assert load_settings().public_mcp_tenants == ("falkordb", "pallets")
    monkeypatch.setenv("PUBLIC_MCP_TENANTS", " ")
    assert load_settings().public_mcp_tenants == ("falkordb", "pallets")

    # CSV is trimmed and deduplicated like the other allowlists.
    monkeypatch.setenv("TENANTS", "falkordb,private")
    monkeypatch.setenv("PUBLIC_MCP_TENANTS", " falkordb , falkordb ,")
    assert load_settings().public_mcp_tenants == ("falkordb",)

    # A public slug outside TENANTS is a startup configuration error.
    monkeypatch.setenv("TENANTS", "falkordb")
    monkeypatch.setenv("PUBLIC_MCP_TENANTS", "falkordb,pallets")
    with pytest.raises(ValueError, match="PUBLIC_MCP_TENANTS"):
        load_settings()


def test_public_tenants_come_from_settings_and_tools_recheck_tenant(monkeypatch):
    """The factory trusts Settings; a public slug missing from TENANTS fails at call time."""
    monkeypatch.setenv("TENANTS", "falkordb")
    monkeypatch.setattr("api.tools._run.graph", _explode)
    settings = Settings(tenants=("falkordb", "pallets"), public_mcp_tenants=("pallets",))

    with TestClient(build_mcp_http(settings, clock=FakeClock())) as client:
        response = _post(client, _bus_factor(1, tenant="pallets"))

    assert response.json()["result"]["isError"] is True
    assert "unknown tenant" in response.json()["result"]["content"][0]["text"]


def test_blank_public_allowlist_serves_configured_tenants(monkeypatch):
    monkeypatch.setenv("TENANTS", "falkordb")
    monkeypatch.setenv("PUBLIC_MCP_TENANTS", "")
    stubs = StubServer()

    with TestClient(build_mcp_http(load_settings(), server=stubs.server)) as client:
        response = _post(
            client, _call(1, "bus_factor", {"tenant": "falkordb", "module_key": "module:a/b/c"})
        )
        unknown = _post(client, _bus_factor(2, tenant="pallets"))

    assert "error" not in response.json()
    assert unknown.json()["error"]["code"] == -32602
    assert stubs.calls == [("bus_factor", "falkordb")]


def test_stdio_in_memory_server_unchanged(monkeypatch):
    """Building the HTTP app never changes the shared stdio singletons."""
    monkeypatch.setenv("TENANTS", "falkordb")
    from api.mcp_server import graph_tools, mcp

    async def list_tools(server):
        async with Client(server) as client:
            return await client.list_tools()

    before = asyncio.run(list_tools(mcp))
    assert {tool.name for tool in before} == SEVEN
    assert "apply_triage" in {tool.name for tool in before}

    app = build_mcp_http(Settings(tenants=("falkordb",), public_mcp_tenants=("falkordb",)))

    after = asyncio.run(list_tools(mcp))
    assert {tool.name for tool in after} == SEVEN
    assert "apply_triage" in {tool.name for tool in after}

    six = asyncio.run(list_tools(graph_tools))
    assert {tool.name for tool in six} == SIX
    # read_only_hint is the MCP 2.3.0 field name (readOnlyHint is the deprecated alias).
    assert all(tool.annotations.read_only_hint is True for tool in six)

    with TestClient(app) as client:
        response = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == SIX


def test_30_tool_calls_not_http_requests(monkeypatch):
    """The budget counts tools/call attempts, never initialize/list HTTP requests."""
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    executed = stubs.calls

    with TestClient(_app(stubs.server, clock=FakeClock())) as client:
        # More than 30 initialize and list exchanges consume no tool budget.
        for rid in range(1, 36):
            assert _post(client, _initialize(rid)).status_code == 200
            assert _post(client, _list_tools(100 + rid)).status_code == 200

        for rid in range(1, 31):
            response = _post(client, _bus_factor(rid))
            assert response.status_code == 200
            assert "error" not in response.json()

        response = _post(client, _bus_factor(31))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json()["id"] == 31
    assert response.json()["error"]["code"] == -32000
    assert response.json()["error"]["message"] == "Tool call rate limit exceeded"
    assert len(executed) == 30


def test_one_budget_across_sessions_tools_and_tenants(monkeypatch):
    """One budget spans reconnects, all six tools and both public tenants."""
    monkeypatch.setenv("TENANTS", "falkordb,pallets")
    stubs = StubServer()
    app = _app(
        stubs.server,
        tenants=("falkordb", "pallets"),
        public=("falkordb", "pallets"),
        clock=FakeClock(),
    )
    ip_a, ip_b = "203.0.113.10", "203.0.113.11"
    names = sorted(SIX)
    tenants = ("falkordb", "pallets")
    planned = []
    for i in range(30):
        name = names[i % len(names)]
        tenant = tenants[i % len(tenants)]
        arguments = {**VALID_ARGS[name], "tenant": tenant}
        planned.append(_call(i + 1, name, arguments))

    # First "session": 15 attempts.
    with TestClient(app) as client:
        for payload in planned[:15]:
            response = _post(client, payload, ip=ip_a)
            assert response.status_code == 200 and "error" not in response.json()

    # Reconnect (a fresh session/transport) does not reset the same IP's budget.
    with TestClient(app) as client:
        for payload in planned[15:]:
            response = _post(client, payload, ip=ip_a)
            assert response.status_code == 200 and "error" not in response.json()

        # Both IPs share one rolling window per IP; the reconnect did not reset ip_a.
        spent = _post(client, _bus_factor(99), ip=ip_a)
        assert spent.status_code == 429
        assert spent.json()["error"]["code"] == -32000
        assert _post(client, _bus_factor(1), ip=ip_b).status_code == 200

    # 30 executions for ip_a plus the one legitimate call from ip_b.
    assert len(stubs.calls) == 31
    assert {name for name, _ in stubs.calls} == SIX
    assert {tenant for _, tenant in stubs.calls} == {"falkordb", "pallets"}


def test_atomic_boundary_and_expiry(monkeypatch):
    """Locking is atomic, expiry is `<= now - 60`, and IPs are canonicalized."""
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    clock = FakeClock()
    app = _app(stubs.server, clock=clock)
    ip = "203.0.113.20"

    with TestClient(app) as client:
        def attempt(i: int):
            return _post(client, _bus_factor(i), ip=ip).status_code

        with ThreadPoolExecutor(max_workers=16) as pool:
            statuses = list(pool.map(attempt, range(1, 32)))

        assert sorted(statuses).count(429) == 1
        assert sorted(statuses).count(200) == 30
        assert len(stubs.calls) == 30

        # The 30 retained attempts sit exactly at clock 0.
        clock.now = 59.999
        boundary = _post(client, _bus_factor(40), ip=ip)
        assert boundary.status_code == 429
        assert boundary.headers["Retry-After"] == "1"

        clock.now = 60.0
        assert _post(client, _bus_factor(41), ip=ip).status_code == 200

    # Equivalent IPv6 spellings normalize to one quota key.
    stubs6 = StubServer()
    clock6 = FakeClock()
    app6 = _app(stubs6.server, clock=clock6)
    expanded = "2001:0db8:0000:0000:0000:0000:0000:0001"
    compressed = "2001:db8::1"
    with TestClient(app6) as client:
        for rid in range(1, 31):
            assert _post(client, _bus_factor(rid), ip=expanded).status_code == 200
        assert _post(client, _bus_factor(31), ip=compressed).status_code == 429
        # A different /64 is a different client (the same /64 is one key, see below).
        assert _post(client, _bus_factor(32), ip="2001:db8:0:1::2").status_code == 200
    assert len(stubs6.calls) == 31


def test_failed_calls_consume_budget(monkeypatch):
    """Denied tools, private tenants, argument errors and exceptions all count."""
    monkeypatch.setenv("TENANTS", "falkordb,private")
    stubs = StubServer(extras=True, explode=frozenset({"recall_run"}))
    settings = Settings(tenants=("falkordb", "private"), public_mcp_tenants=("falkordb",))
    app = build_mcp_http(settings, server=stubs.server, clock=FakeClock())

    def denied_tool(_):
        response = _post(client, _call(1, "apply_triage", {"tenant": "falkordb", "run_key": "run:1"}))
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32601

    def unknown_tool(_):
        response = _post(client, _call(2, "query", {"tenant": "falkordb", "text": "MATCH (n) RETURN n"}))
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32601

    def private_tenant(_):
        response = _post(client, _bus_factor(3, tenant="private"))
        assert response.json()["error"]["code"] == -32602

    def missing_tenant(_):
        response = _post(client, _call(4, "bus_factor", {"module_key": "module:a/b/c"}))
        assert response.json()["error"]["code"] == -32602

    def argument_error(_):
        response = _post(client, _call(5, "rank_reviewers", {"tenant": "falkordb"}))
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is True

    def execution_error(_):
        response = _post(client, _call(6, "recall_run", {"tenant": "falkordb", "run_key": "run:1"}))
        assert response.status_code == 200
        assert response.json()["result"]["isError"] is True

    kinds = (denied_tool, unknown_tool, private_tenant, missing_tenant, argument_error, execution_error)
    with TestClient(app) as client:
        for cycle in range(5):
            for kind in kinds:
                kind(cycle)

        # All 30 failed attempts consumed the budget: the valid 31st executes nothing.
        response = _post(client, _bus_factor(31))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json()["error"]["code"] == -32000
    assert {name for name, _ in stubs.calls} == {"recall_run"}  # only the exploding stub was reached
    assert stubs.actions == [] and stubs.queries == []


def test_batch_and_notification_semantics(monkeypatch):
    """Arrays and notifications never execute; notifications still consume quota."""
    monkeypatch.setenv("TENANTS", "falkordb")

    # Every JSON array is unsupported batching: 400, -32600, id null, nothing runs.
    stubs = StubServer()
    app = _app(stubs.server, clock=FakeClock())
    arrays = [
        [_bus_factor(1), _initialize(2)],
        [],
        [_bus_factor(rid) for rid in range(31)],
    ]
    with TestClient(app) as client:
        for payload in arrays:
            response = _post(client, payload)
            assert response.status_code == 400
            assert response.json()["id"] is None
            assert response.json()["error"]["code"] == -32600
        assert stubs.calls == []

        # The arrays charged nothing: 30 valid calls still admit.
        for rid in range(1, 31):
            assert _post(client, _bus_factor(rid)).status_code == 200
        assert len(stubs.calls) == 30
        assert _post(client, _bus_factor(31)).status_code == 429

    # A tool-call notification consumes one attempt but never executes.
    stubs = StubServer()
    app = _app(stubs.server, clock=FakeClock())
    with TestClient(app) as client:
        notified = _post(client, _notify("bus_factor", {"tenant": "falkordb", "module_key": "module:a/b/c"}))
        assert notified.status_code == 202
        assert notified.content == b""
        assert stubs.calls == []

        for rid in range(1, 30):
            assert _post(client, _bus_factor(rid)).status_code == 200
        assert len(stubs.calls) == 29  # 1 notification + 29 calls = 30 attempts

        exhausted = _post(client, _notify("bus_factor", {"tenant": "falkordb", "module_key": "module:a/b/c"}))
        assert exhausted.status_code == 429
        assert exhausted.content == b""
        assert exhausted.headers["Retry-After"] == "60"
        assert len(stubs.calls) == 29

    # An unknown-tool notification is still charged and still never executes.
    stubs = StubServer(extras=True)
    app = _app(stubs.server, clock=FakeClock())
    with TestClient(app) as client:
        response = _post(client, _notify("apply_triage", {"tenant": "falkordb", "run_key": "run:1"}))
        assert response.status_code == 202
        assert response.content == b""
        assert stubs.calls == [] and stubs.actions == []

    # ID 0 and string IDs are real requests and are echoed back unchanged.
    stubs = StubServer()
    app = _app(stubs.server, clock=FakeClock())
    with TestClient(app) as client:
        zero = _post(client, _bus_factor(0))
        assert zero.status_code == 200
        assert zero.json()["id"] == 0
        stringy = _post(client, _bus_factor("abc-123"))
        assert stringy.status_code == 200
        assert stringy.json()["id"] == "abc-123"
    assert len(stubs.calls) == 2

    # Normal initialize/initialized/list exchanges still work and consume no quota.
    stubs = StubServer()
    app = _app(stubs.server, clock=FakeClock())
    with TestClient(app) as client:
        initialize = _post(client, _initialize(1))
        assert initialize.status_code == 200
        assert initialize.json()["result"]["serverInfo"]["name"]
        assert _post(client, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}).status_code == 202
        listed = _post(client, _list_tools(2))
        assert listed.status_code == 200
        assert {tool["name"] for tool in listed.json()["result"]["tools"]} == SIX
        for rid in range(1, 31):
            assert _post(client, _bus_factor(rid)).status_code == 200

    # Invalid JSON and invalid envelopes execute nothing and are charged nothing.
    stubs = StubServer()
    app = _app(stubs.server, clock=FakeClock())
    with TestClient(app) as client:
        parse_error = _post_raw(client, "{not json")
        assert parse_error.status_code == 400
        assert parse_error.json()["error"]["code"] == -32700

        for rid in (None, True, False):
            response = _post(
                client,
                _call(rid, "bus_factor", {"tenant": "falkordb", "module_key": "module:a/b/c"}),
            )
            assert response.status_code == 400
            assert response.json()["id"] is None
            assert response.json()["error"]["code"] == -32600

        assert _post(client, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": []}).status_code == 400
        assert stubs.calls == []

        for rid in range(1, 31):
            assert _post(client, _bus_factor(rid)).status_code == 200


def test_untrusted_ip_headers_fail_closed(monkeypatch):
    """Only one valid X-Keystone-Client-IP is trusted; nothing else keys the quota."""
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    app = _app(stubs.server, clock=FakeClock())
    call = _bus_factor(1)

    with TestClient(app) as client:
        assert client.post("/mcp", json=call, headers=ACCEPT).status_code == 503

        duplicate = [
            ("Accept", "application/json, text/event-stream"),
            (CLIENT_IP_HEADER, "203.0.113.1"),
            (CLIENT_IP_HEADER, "203.0.113.2"),
        ]
        assert client.post("/mcp", json=call, headers=duplicate).status_code == 503

        for value in (
            "203.0.113.1,203.0.113.2",
            "not-an-ip",
            "",
            "203.0.113.1 ",
            "localhost",
            "999.1.1.1",
            "203.0.113.1/24",
        ):
            assert client.post("/mcp", json=call, headers=_headers(value)).status_code == 503

        assert stubs.calls == []

        # Only the trusted header keys the budget. Varying every other forwarding
        # or session header never resets the one IP's 30 attempts.
        for rid in range(1, 31):
            varied = _headers(
                "203.0.113.50",
                **{
                    "Forwarded": f"for=10.0.0.{rid}",
                    "X-Forwarded-For": f"10.0.0.{rid}",
                    "X-Real-IP": f"10.0.0.{rid}",
                    "MCP-Session-Id": f"session-{rid}",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )
            assert client.post("/mcp", json=_bus_factor(rid), headers=varied).status_code == 200

        response = client.post(
            "/mcp",
            json=_bus_factor(31),
            headers=_headers("203.0.113.50", **{"X-Forwarded-For": "10.0.0.99"}),
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"

        # A different trusted IP has its own allowance.
        assert client.post("/mcp", json=_bus_factor(1), headers=_headers("203.0.113.51")).status_code == 200

    assert len(stubs.calls) == 31


def test_limiter_prunes_expired_ip_buckets():
    """Admissions drop expired timestamps and historical, empty IP buckets."""
    clock = FakeClock()
    limiter = ToolCallLimiter(clock)
    for i in range(100):
        assert limiter.admit(f"10.0.0.{i}") is None

    clock.advance(61.0)
    assert limiter.admit("10.0.0.200") is None
    assert set(limiter._attempts) == {"10.0.0.200"}  # nothing historical survives


def test_ipv6_budget_is_per_64_and_mapped_ipv4_is_ipv4(monkeypatch):
    """Rotating addresses inside one /64 mints no budget; ::ffff:a.b.c.d is a.b.c.d."""
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    with TestClient(_app(stubs.server, clock=FakeClock())) as client:
        for rid in range(1, 31):
            assert _post(client, _bus_factor(rid), ip=f"2001:db8:0:7::{rid:x}").status_code == 200
        assert _post(client, _bus_factor(31), ip="2001:db8:0:7:ffff::1").status_code == 429
        assert _post(client, _bus_factor(32), ip="2001:db8:0:8::1").status_code == 200

        for rid in range(1, 31):
            assert _post(client, _bus_factor(rid), ip="198.51.100.9").status_code == 200
        assert _post(client, _bus_factor(31), ip="::ffff:198.51.100.9").status_code == 429
        assert _post(client, _bus_factor(32), ip="198.51.100.10").status_code == 200
    assert len(stubs.calls) == 62


def test_deeply_nested_json_is_a_parse_error_not_a_crash(monkeypatch):
    """100 KB of nesting (under the 1 MiB cap) must not crash admission with a 500."""
    monkeypatch.setenv("TENANTS", "falkordb")
    stubs = StubServer()
    with TestClient(_app(stubs.server, clock=FakeClock()), raise_server_exceptions=False) as client:
        response = _post_raw(client, "[" * 50000 + "]" * 50000)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32700
        assert _post(client, _bus_factor(1)).status_code == 200  # nothing was charged
    assert stubs.calls == [("bus_factor", "falkordb")]


# --- Task 3: mount, lifecycle and fixture mode -------------------------------
