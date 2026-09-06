#!/usr/bin/env python3
"""V5-C-7.1 real-LLM observational artifact offline verifier."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.comparison import (  # noqa: E402
    ComparisonArtifactError,
    file_sha256,
    load_json,
    validate_agent_justification,
    validate_agent_justification_v2,
    validate_level_comparison,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--level-comparison", type=Path, required=True)
    parser.add_argument("--derived-from", type=Path)
    args = parser.parse_args(argv)
    try:
        level_payload = load_json(args.level_comparison)
        validate_level_comparison(level_payload)
        payload = load_json(args.artifact)
        schema_version = payload.get("schema_version")
        if schema_version == "agent-justification-v1":
            if args.derived_from is not None:
                raise ComparisonArtifactError("DERIVED_FROM_NOT_ALLOWED")
            verdict = validate_agent_justification(
                payload,
                level_comparison_sha256=file_sha256(args.level_comparison),
                level_comparison_revision=str(level_payload.get("source_revision", "")),
            )
        elif schema_version == "agent-justification-v2":
            if args.derived_from is None:
                raise ComparisonArtifactError("DERIVED_FROM_REQUIRED")
            verdict = validate_agent_justification_v2(
                payload,
                derived_from=load_json(args.derived_from),
                derived_from_sha256=file_sha256(args.derived_from),
                level_comparison_sha256=file_sha256(args.level_comparison),
                level_comparison_revision=str(level_payload.get("source_revision", "")),
            )
        else:
            raise ComparisonArtifactError("ARTIFACT_SCHEMA_MISMATCH")
    except ComparisonArtifactError as exc:
        print(json.dumps({"reason_code": exc.code}), file=sys.stderr)
        return 1
    print(f"AGENT_JUSTIFICATION_OK verdict={verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
