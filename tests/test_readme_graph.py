"""A8: the README's graph model and Cypher inventory stay honest.

These tests assert *structure and coverage*, not whole paragraphs or fixed live
counts (which would be brittle and would silently pass on fabricated numbers).
They fail when a query is added to ``cypher/`` without a README entry, when the
inventory names a file that does not exist, or when the Decision-grounding and
graph-preserving-reload guarantees are dropped from the docs.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CYPHER_DIR = REPO_ROOT / "cypher"
README = REPO_ROOT / "README.md"

# One backticked ``name.cypher`` reference, as the inventory writes them.
_CYPHER_REF = re.compile(r"`([A-Za-z0-9_]+\.cypher)`")


def _cypher_basenames() -> set[str]:
    return {path.name for path in CYPHER_DIR.glob("*.cypher")}


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _cypher_inventory_section(text: str) -> str:
    """Return the ``## Cypher`` section body (up to the next ``## `` heading)."""
    heading = re.search(r"^## Cypher\s*$", text, flags=re.MULTILINE)
    assert heading is not None, "README has no '## Cypher' section"
    body = text[heading.end():]
    next_heading = re.search(r"^## ", body, flags=re.MULTILINE)
    if next_heading is not None:
        body = body[: next_heading.start()]
    return body


def test_readme_lists_every_cypher_file():
    section = _cypher_inventory_section(_readme_text())
    listed = _CYPHER_REF.findall(section)
    counts: dict[str, int] = {}
    for name in listed:
        counts[name] = counts.get(name, 0) + 1

    discovered = _cypher_basenames()

    missing = sorted(discovered - set(counts))
    assert not missing, f"cypher files missing from the README inventory: {missing}"

    invented = sorted(set(counts) - discovered)
    assert not invented, f"README inventory names files that do not exist: {invented}"

    duplicated = sorted(name for name, n in counts.items() if n != 1)
    assert not duplicated, f"README inventory must list each query once: {duplicated}"


def test_readme_documents_decision_grounding_and_memory():
    text = _readme_text()
    lowered = text.lower()

    # Decision grounding: a Decision is kept only if its quote is verbatim.
    assert "verbatim" in lowered, "README must state the Decision quote check"
    assert "decision" in lowered

    # Stable keys: nodes are addressed by a stable key, never a FalkorDB id.
    assert "stable" in lowered and "key" in lowered, "README must document stable keys"

    # Replay provenance: the sandbox issue -> upstream issue edge.
    assert "REPLAY_OF" in text, "README must document the REPLAY_OF relationship"

    # Graph-preserving reload: a load never drops the graph or agent memory.
    assert "never drops the graph" in lowered, (
        "README must state that a load preserves the graph and agent memory"
    )
    assert "agent memory" in lowered
