"""Run approved real-LLM U10 at clean main R; never enables production.

For no-LLM rehearsal use prepare_u10_fixtures.py --dry-run instead.
This command requires a separately approved, execution-bound export grant.
"""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.u10_cli import Parser, failure_code  # noqa: E402
from app.agent.u10_runner import run_comparison  # noqa: E402


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("repository", "inputs", "llm-config", "export-grant"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "revision",
        "benchmark-sha256",
        "llm-config-sha256",
        "export-grant-sha256",
        "postgres-image-id",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    try:
        args = vars(parser.parse_args(argv))
        args.pop("execute")
        result = run_comparison(**args)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "code": failure_code(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
