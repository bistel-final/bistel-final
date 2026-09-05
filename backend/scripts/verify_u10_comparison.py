"""Read-only U10 metric validation; never enables production or issues evidence.

The benchmark SHA must come from the pre-execution pinned inventory, not from
the artifact being checked. A negative research verdict still exits zero.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    digest,
    parse_json,
    read_private,
)
from app.agent.u10_comparison import validate_artifact  # noqa: E402


def sha256_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("SHA256_INVALID")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--benchmark-sha256", type=sha256_arg, required=True)
    args = parser.parse_args(argv)
    try:
        benchmark_bytes = read_private(args.benchmark.parent, args.benchmark.name)
        if digest(benchmark_bytes) != args.benchmark_sha256:
            raise EvidenceError("PINNED_BENCHMARK_SHA_MISMATCH")
        artifact_bytes = read_private(args.artifact.parent, args.artifact.name)
        result = validate_artifact(
            parse_json(artifact_bytes), parse_json(benchmark_bytes)
        )
    except (OSError, ValueError, RecursionError) as exc:
        print(
            json.dumps(
                {
                    "integrity": "FAIL",
                    "reason_code": str(exc)
                    if isinstance(exc, EvidenceError)
                    else "U10_INPUT_INVALID",
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "integrity": "PASS",
                "artifact_sha256": digest(artifact_bytes),
                "agent_verdict": result["agent_verdict"],
                "verdict_reason": result["verdict_reason"],
                "inspection_only": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
