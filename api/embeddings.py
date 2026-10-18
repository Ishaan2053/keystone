"""Local embeddings (Rule 7: never an API): all-MiniLM-L6-v2, 384-d, on CPU.

The loader, the tools and the webhook all embed through this module, so stored
vectors and query vectors come from the same model and the same text rules.
Store a vector with `vecf32($vec)`; a plain list is not picked up by the vector index.
"""

from __future__ import annotations

import threading
from functools import lru_cache

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIMENSION = 384
ISSUE_BODY_CHARS = 1000


_lock = threading.Lock()


def model():
    """The model, loaded once per process (first call downloads it to the Hugging Face cache).
    Thread-safe: the MCP server warms it in a background thread while tool calls may already embed."""
    with _lock:
        return _load()


@lru_cache(maxsize=1)
def _load():
    from sentence_transformers import (
        SentenceTransformer,  # slow import; only paid by callers that embed
    )

    return SentenceTransformer(MODEL, device="cpu")


def encode(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """One 384-d unit vector per text, encoded in batches."""
    if not texts:
        return []
    vectors = model().encode(
        texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True
    )
    return vectors.tolist()


def issue_text(title: str, body: str | None) -> str:
    """What an Issue's embedding is made from: the title plus the first 1,000 characters of the body."""
    return f"{title}\n\n{(body or '')[:ISSUE_BODY_CHARS]}".strip()
