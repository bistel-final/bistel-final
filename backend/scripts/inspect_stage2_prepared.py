"""Private read-only Stage2 entry check; no lock/claim/runtime/cleanup action."""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_entry import MODES, inspect_entry  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("report-root", "repository", "prepared-attempt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    try:
        args = parser.parse_args(argv)
        result = inspect_entry(
            report_root=args.report_root,
            repository=args.repository,
            attempt_id=args.attempt_id,
            mode=args.mode,
            prepared_path=args.prepared_attempt,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "code": failure_code(exc),
                    "execution_authorized": False,
                    "cleanup_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
