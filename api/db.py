"""The FalkorDB graph for a tenant (Rule 9: one graph per org, tenant_<slug>)."""
from __future__ import annotations

import os
from functools import lru_cache

from falkordb import FalkorDB


@lru_cache(maxsize=4)
def _client(host: str, port: int, username: str | None, password: str | None) -> FalkorDB:
    # One client (and its connection pool) per process: on Windows a new connection to
    # `localhost` waits about 2 s on IPv6 before falling back, which every tool call would pay.
    return FalkorDB(host=host, port=port, username=username, password=password)


def graph(tenant: str):
    """The graph tenant_<tenant>; reads FALKORDB_* from the environment (the caller loads .env)."""
    db = _client(
        os.environ.get("FALKORDB_HOST", "localhost"),
        int(os.environ.get("FALKORDB_PORT", "6379")),
        os.environ.get("FALKORDB_USERNAME") or None,
        os.environ.get("FALKORDB_PASSWORD") or None,
    )
    return db.select_graph(f"tenant_{tenant}")
