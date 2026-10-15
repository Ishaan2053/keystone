"""The FalkorDB graph for a tenant (Rule 9: one graph per org, tenant_<slug>)."""
from __future__ import annotations

import os

from falkordb import FalkorDB


def graph(tenant: str):
    """The graph tenant_<tenant>; reads FALKORDB_* from the environment (the caller loads .env)."""
    db = FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )
    return db.select_graph(f"tenant_{tenant}")
