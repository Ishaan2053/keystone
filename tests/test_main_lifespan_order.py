"""The webhook worker runs only inside the hosted MCP lifespan (#68); no FalkorDB, worker thread or network.

The worker's triages enter graph_tools' lifespan through the agent's in-process client, and the hosted
/mcp transport enters and exits that same lifespan on uvicorn's loop. So the app starts the worker after
entering the transport's lifespan and drains it before leaving, then closes the graph reader.
"""
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

import api.main
from api.config import Settings


def test_worker_starts_inside_and_drains_before_the_mcp_lifespan_exits(monkeypatch):
    events: list[str] = []

    class FakeMcpApp:
        def lifespan(self, _app):
            @asynccontextmanager
            async def cm():
                events.append("mcp enter")
                yield
                events.append("mcp exit")
            return cm()

        async def __call__(self, scope, receive, send):  # never routed in this test
            raise AssertionError("unexpected /mcp request")

    class FakeWorker:
        def start(self):
            events.append("worker start")

        def close(self):
            events.append("worker close")

    class FakeReads:
        def close(self):
            events.append("reads close")

    monkeypatch.setattr(api.main, "build_mcp_http", lambda settings: FakeMcpApp())
    app = api.main.create_app(Settings(api_mode="live"), graph_reads=FakeReads(), webhook_worker=FakeWorker())
    with TestClient(app):
        assert events == ["mcp enter", "worker start"]
    assert events == ["mcp enter", "worker start", "worker close", "mcp exit", "reads close"]
