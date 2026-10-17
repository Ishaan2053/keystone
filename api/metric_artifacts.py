"""Read optional tenant metrics without allowing one bad artifact to hide another."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from api import schemas


logger = logging.getLogger(__name__)
_SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_LATENCIES = TypeAdapter(list[schemas.ToolLatency])


def read_metric_artifacts(
    tenant: str, data_dir: Path
) -> tuple[schemas.Proof | None, list[schemas.ToolLatency], list[schemas.MetricArtifact]]:
    """Read fresh Proof and benchmark files; expected failures are independent.

    Benchmark metadata is intentionally ignored: graph counts come from the live
    query, not the benchmark snapshot. Logs never include artifact contents.
    The third value names artifacts whose file exists but could not be read or
    validated; a missing file is simply not measured and is not listed.
    """
    if not _SAFE_SLUG.fullmatch(tenant):
        raise ValueError("tenant must be a safe slug")

    proof = None
    latencies = []
    unavailable: list[schemas.MetricArtifact] = []
    try:
        proof = schemas.Proof.model_validate_json(
            (data_dir / "proof" / f"{tenant}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        if not isinstance(exc, FileNotFoundError):
            unavailable.append("proof")
        logger.warning("Proof artifact unavailable for %s (%s)", tenant, type(exc).__name__)

    try:
        benchmark = json.loads(
            (data_dir / "bench" / f"{tenant}.json").read_text(encoding="utf-8")
        )
        # Validate the envelope too, rather than letting indexing/type errors escape.
        if not isinstance(benchmark, dict) or "latencies" not in benchmark:
            raise ValueError("benchmark must contain latencies")
        validated = _LATENCIES.validate_python(benchmark["latencies"])
        if any(not math.isfinite(value) for row in validated for value in (row.p50_ms, row.p95_ms)):
            raise ValueError("benchmark latencies must be finite")
        latencies = validated
    # json.loads raises RecursionError on deeply nested input; pydantic's parser
    # (the Proof branch) already reports that as a ValidationError.
    except (OSError, ValueError, ValidationError, RecursionError) as exc:
        if not isinstance(exc, FileNotFoundError):
            unavailable.append("latencies")
        logger.warning("Benchmark artifact unavailable for %s (%s)", tenant, type(exc).__name__)

    return proof, latencies, unavailable
