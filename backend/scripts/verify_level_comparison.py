#!/usr/bin/env python3
"""V5-C-7.1 deterministic comparison artifact offline verifier."""

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
    load_json,
    validate_level_comparison,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verdict = validate_level_comparison(load_json(args.artifact))
    except ComparisonArtifactError as exc:
        print(json.dumps({"reason_code": exc.code}), file=sys.stderr)
        return 1
    print(f"LEVEL_COMPARISON_OK verdict={verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
