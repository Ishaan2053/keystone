"""Hosted MCP: serve api.mcp_server.graph_tools over stateless Streamable HTTP (B10).

This module is the HTTP-only admission layer. It serves exactly the six read-only graph
tools at POST /mcp; apply_triage, an arbitrary query tool, unknown names and non-public
tenants are refused here, before the transport or any tool runs. Policy lives entirely on
the app this factory returns: the shared graph_tools/mcp singletons are never edited, and
no middleware is attached to them.

The request body is limited to 1 MiB and a 10-second total upload deadline, then
replayed byte-for-byte to the SDK, so admitted calls
keep the SDK-native ToolResult (schemas, evidence, Cypher) without translation. One
rolling 60-second budget of 30 tool-call attempts is shared per trusted client IP across
sessions, tools and public tenants; the injected `clock` is its only time source.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount

from api.config import Settings, _split_csv

# The six read-only tools graph_tools exports (api/mcp_server.py:53-88). This is the
# allowlist, not a hint: any other name — including apply_triage — is refused over HTTP.
READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "affected_modules",
        "rank_reviewers",
        "bus_factor",
        "find_decisions",
        "explain_path",
        "recall_run",
    }
)

MCP_PATH = "/mcp"
MAX_BODY_BYTES = 1024 * 1024
BODY_UPLOAD_TIMEOUT = 10.0
# Caddy overwrites this on every upstream request with the real socket peer. It is the
# only header trusted for quota keys; nothing else (Forwarded, X-Forwarded-For,
# X-Real-IP, MCP session IDs, tenant values) may select a bucket.
CLIENT_IP_HEADER = b"x-keystone-client-ip"
_UNKNOWN_TOOL = "Unknown tool"
_TENANT_UNAVAILABLE = "Public tenant unavailable"
_RATE_LIMITED = "Tool call rate limit exceeded"
_BATCH_UNSUPPORTED = "Unsupported batch request"
_INVALID_REQUEST = "Invalid Request"


class ToolCallLimiter:
    """A per-IP rolling-window limit on admitted tool-call attempts.

    ``limit`` accepted attempts are allowed per ``window_seconds`` for each IP. The
    lock makes prune/check/append atomic, so a concurrent burst cannot exceed the
    limit. Expired timestamps (``<= now - window_seconds``) and the now-empty IP
    buckets are dropped on every admission, so historical clients do not accumulate.
    """

    def __init__(
        self,
        clock: Callable[[], float],
        limit: int = 30,
        window_seconds: float = 60.0,
    ) -> None:
        self._clock = clock
        self._limit = limit
        self._window = window_seconds
        self._lock = threading.Lock()
        self._attempts: dict[str, deque[float]] = {}

    def admit(self, ip: str) -> int | None:
        """Record one attempt; return ``None`` if admitted, else the retry-after seconds."""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            for key in list(self._attempts):
                bucket = self._attempts[key]
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if not bucket:
                    del self._attempts[key]

            bucket = self._attempts.get(ip)
            if bucket is not None and len(bucket) >= self._limit:
                return max(1, math.ceil(bucket[0] + self._window - now))

            if bucket is None:
                bucket = deque()
                self._attempts[ip] = bucket
            bucket.append(now)
            return None


def validate_public_tenants(settings: Settings) -> frozenset[str]:
    """The public tenant slugs, checked against both Settings and the environment.

    `api.tools._run.check_tenant` reads the process environment's ``TENANTS`` at call
    time, so a public slug must be present there as well as in ``Settings.tenants``.
    Disagreement is a startup configuration error, never an environment mutation.
    """
    public = frozenset(settings.public_mcp_tenants)
    if not public:
        return public

    missing_settings = public - set(settings.tenants)
    if missing_settings:
        raise ValueError(
            f"PUBLIC_MCP_TENANTS must be a subset of Settings.tenants: {sorted(missing_settings)}"
        )

    env_tenants = set(_split_csv(os.getenv("TENANTS", "falkordb")) or ("falkordb",))
    missing_env = public - env_tenants
    if missing_env:
        raise ValueError(
            f"PUBLIC_MCP_TENANTS must also be configured in the TENANTS environment: "
            f"{sorted(missing_env)}"
        )
    return public


class _LifespanStarlette(Starlette):
    """A Starlette that exposes the lifespan it was built with, like the SDK transport."""

    @property
    def lifespan(self):  # StarletteWithLifespan parity for Task 3's composed lifespan.
        return self.router.lifespan_context


class _AdmissionApp:
    """ASGI admission in front of the MCP transport.

    It only inspects POSTs to /mcp. Every other request is passed through untouched. The
    trusted client IP is required and canonicalized first, then the body is classified:
    JSON arrays are rejected as unsupported batching, ``tools/call`` envelopes consume
    the per-IP budget (including notifications, unknown tools, private tenants and
    argument errors), and everything else is passed through. An admitted POST replays
    the exact request bytes to the transport, which still owns JSON-RPC validation, tool
    execution and serialization after admission.
    """

    def __init__(
        self,
        transport: Callable[..., Awaitable[None]],
        *,
        public_tenants: frozenset[str],
        limiter: ToolCallLimiter,
        body_upload_timeout: float = BODY_UPLOAD_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._public_tenants = public_tenants
        self._limiter = limiter
        self._body_upload_timeout = body_upload_timeout

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or not self._is_mcp_post(scope):
            await self._transport(scope, receive, send)
            return

        client_ip = _client_ip(scope)
        if client_ip is None:
            await Response("Client IP unavailable", status_code=503)(scope, receive, send)
            return

        try:
            # One deadline spans the entire upload, not each chunk or tool execution.
            body = await asyncio.wait_for(_read_body(receive), self._body_upload_timeout)
        except _BodyTooLarge:
            await Response("Request body too large", status_code=413)(scope, receive, send)
            return
        except asyncio.TimeoutError:
            await Response("Request body upload timed out", status_code=408)(scope, receive, send)
            return
        except _ClientDisconnected:
            # There is no connected client to answer, and partial JSON must never run.
            return
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            await self._forward(scope, body, send)  # The SDK reports the parse error.
            return

        # Batch execution is not supported: no array executes and none consumes quota.
        if isinstance(payload, list):
            await _json_error(None, 400, -32600, _BATCH_UNSUPPORTED)(scope, receive, send)
            return

        if not _is_tool_call(payload):
            await self._forward(scope, body, send)
            return

        # A structurally valid tools/call envelope. Absent ID = notification; null,
        # boolean and other IDs are invalid envelopes that never execute and never charge.
        if "id" not in payload:
            kind = "notification"
        elif _valid_id(payload["id"]):
            kind = "request"
        else:
            await _json_error(None, 400, -32600, _INVALID_REQUEST)(scope, receive, send)
            return

        retry_after = self._limiter.admit(client_ip)
        if kind == "notification":
            # A notification still consumes one attempt but never executes and never
            # produces a JSON-RPC body.
            status = 202 if retry_after is None else 429
            await Response(status_code=status)(scope, receive, send)
            return

        if retry_after is not None:
            await _rate_limited(payload["id"], retry_after)(scope, receive, send)
            return

        rejection = self._tool_rejection(payload)
        if rejection is not None:
            await rejection(scope, receive, send)
            return

        await self._forward(scope, body, send)

    async def _forward(self, scope, body: bytes, send) -> None:
        child = dict(scope)
        child["path"] = MCP_PATH
        child["raw_path"] = MCP_PATH.encode()
        await self._transport(child, _replay(body), send)

    @staticmethod
    def _is_mcp_post(scope) -> bool:
        if scope.get("method") != "POST":
            return False
        path = scope.get("path", "")
        return path == MCP_PATH or path == MCP_PATH + "/"

    def _tool_rejection(self, payload: dict[str, Any]) -> Callable[..., Awaitable[None]] | None:
        """Return an ASGI response that refuses this admitted tool call, or None."""
        params = payload.get("params")
        params = params if isinstance(params, dict) else {}
        name = params.get("name")
        if not isinstance(name, str) or name not in READ_ONLY_TOOL_NAMES:
            return _json_error(payload["id"], 200, -32601, _UNKNOWN_TOOL)
        if not self._tenant_is_public(params):
            return _json_error(payload["id"], 200, -32602, _TENANT_UNAVAILABLE)
        return None

    def _tenant_is_public(self, params: dict[str, Any]) -> bool:
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            return False
        tenant = arguments.get("tenant")
        # isinstance(str) also rejects None, bool, list, dict and numbers.
        return isinstance(tenant, str) and tenant in self._public_tenants


def _is_tool_call(payload: Any) -> bool:
    """A structurally valid ``tools/call`` JSON-RPC envelope (the SDK still validates)."""
    return (
        isinstance(payload, dict)
        and payload.get("jsonrpc") == "2.0"
        and payload.get("method") == "tools/call"
        and ("params" not in payload or isinstance(payload["params"], dict))
    )


def _valid_id(value: Any) -> bool:
    """A JSON-RPC request ID: a string or an integer, but never a bool (a bool is int)."""
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _client_ip(scope) -> str | None:
    """Canonicalize the one trusted Caddy client-IP header, or None to fail closed."""
    values = [value for name, value in scope.get("headers", ()) if name.lower() == CLIENT_IP_HEADER]
    if len(values) != 1:
        return None
    try:
        return str(ipaddress.ip_address(values[0].decode("latin-1")))
    except (ValueError, UnicodeDecodeError):
        return None


def _json_error(rid: Any, status: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}},
        status_code=status,
    )


def _rate_limited(rid: Any, retry_after: int) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32000, "message": _RATE_LIMITED},
        },
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


class _BodyTooLarge(Exception):
    pass


class _ClientDisconnected(Exception):
    pass


async def _read_body(receive) -> bytes:
    # Count actual ASGI bytes: Content-Length can be absent or dishonest. Never retain
    # an overflowing chunk or drain the remaining upload, even if quota is exhausted.
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected
        chunk = message.get("body", b"")
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise _BodyTooLarge
        body.extend(chunk)
        if not message.get("more_body", False):
            return bytes(body)


def _replay(body: bytes) -> Callable[[], Awaitable[dict[str, Any]]]:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def build_mcp_http(
    settings: Settings,
    *,
    server: FastMCP | None = None,
    clock: Callable[[], float] = time.monotonic,
    body_upload_timeout: float = BODY_UPLOAD_TIMEOUT,
) -> Starlette:
    """The guarded Streamable HTTP app for the six read-only tools.

    Production defaults to the shared ``graph_tools``; tests inject a local server with
    matching names and inspectable counters. The returned app owns both the admission
    wrapper and the transport lifespan. ``clock`` is the only time source for the one
    per-app ``ToolCallLimiter``. ``body_upload_timeout`` bounds total upload time only
    (10 seconds by default); tool execution has no deadline imposed here.
    """
    public_tenants = validate_public_tenants(settings)
    if server is None:
        from api.mcp_server import graph_tools

        server = graph_tools

    transport = server.http_app(
        path=MCP_PATH,
        transport="http",
        stateless_http=True,
        json_response=True,
    )
    limiter = ToolCallLimiter(clock)
    wrapper = _AdmissionApp(
        transport, public_tenants=public_tenants, limiter=limiter,
        body_upload_timeout=body_upload_timeout,
    )
    return _LifespanStarlette(routes=[Mount("/", app=wrapper)], lifespan=transport.lifespan)
