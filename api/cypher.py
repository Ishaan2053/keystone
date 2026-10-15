"""Named Cypher from cypher/ (Rule 3: query text lives there, not in Python)."""
from __future__ import annotations

from pathlib import Path

CYPHER_DIR = Path(__file__).resolve().parent.parent / "cypher"


def query(name: str) -> str:
    """The text of cypher/<name>.cypher, one statement."""
    return (CYPHER_DIR / f"{name}.cypher").read_text(encoding="utf-8").strip()


def statements(name: str) -> list[str]:
    """The `;`-separated statements of cypher/<name>.cypher, without `//` comment lines."""
    lines = [l for l in query(name).splitlines() if not l.strip().startswith("//")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
