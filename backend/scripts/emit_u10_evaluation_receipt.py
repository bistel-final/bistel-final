"""Publish one U10 evaluation receipt at clean main R (no-clobber, private)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.u10_cli import Parser, failure_code  # noqa: E402
from app.agent.u10_receipt import emit_evaluation_receipt  # noqa: E402


def main(argv=None) -> int:
    parser = Parser(description=__doc__)
    for name in ("repository", "artifact", "benchmark"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--benchmark-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        result = emit_evaluation_receipt(
            repository=args.repository,
            artifact=args.artifact,
            benchmark=args.benchmark,
            benchmark_sha256=args.benchmark_sha256,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "code": failure_code(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
