"""Keep tests independent of the developer's .env.

``api.main.create_app`` reads the repo .env (``ingest.fetch.load_env``) so a local
live API can reach FalkorDB. Under pytest it takes only ``FALKORDB_*`` from it,
as tests/test_tools.py does, so other keys (API_MODE, DRY_RUN, tokens...) never
change a test's outcome. This runs before any test module imports ``api.main``.
"""
import os

import pytest

from ingest import fetch

_load_env = fetch.load_env


def _load_falkordb_env_only(path=fetch.ROOT / ".env"):
    before = set(os.environ)
    _load_env(path)
    for key in set(os.environ) - before:
        if not key.startswith("FALKORDB_"):
            del os.environ[key]


fetch.load_env = _load_falkordb_env_only


@pytest.fixture(autouse=True)
def _no_embedding_warm_up(monkeypatch):
    """A live app warms the embedding model at startup; tests never load or download it."""
    import api.main

    monkeypatch.setattr(api.main, "_warm_embeddings", lambda: None)
