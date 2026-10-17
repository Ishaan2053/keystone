"""Read optional tenant metrics without allowing one bad artifact to hide another."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from api import schemas


logger = logging.getLogger(__name__)
_SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_LATENCIES = TypeAdapter(list[schemas.ToolLatency])


def read_metric_artifacts(
    tenant: str, data_dir: Path
) -> tuple[schemas.Proof | None, list[schemas.ToolLatency]]:
    """Read fresh Proof and benchmark files; expected failures are independent.

    Benchmark metadata is intentionally ignored: graph counts come from the live
    query, not the benchmark snapshot. Logs never include artifact contents.
    """
    if not _SAFE_SLUG.fullmatch(tenant):
        raise ValueError("tenant must be a safe slug")

    proof = None
    latencies = []
    try:
        proof = schemas.Proof.model_validate_json(
            (data_dir / "proof" / f"{tenant}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        logger.warning("Proof artifact unavailable for %s (%s)", tenant, type(exc).__name__)

    try:
        benchmark = json.loads(
            (data_dir / "bench" / f"{tenant}.json").read_text(encoding="utf-8")
        )
        # Validate the envelope too, rather than letting indexing/type errors escape.
        if not isinstance(benchmark, dict) or "latencies" not in benchmark:
            raise ValueError("benchmark must contain latencies")
        latencies = _LATENCIES.validate_python(benchmark["latencies"])
    except (OSError, ValueError, ValidationError) as exc:
        logger.warning("Benchmark artifact unavailable for %s (%s)", tenant, type(exc).__name__)

    return proof, latencies
