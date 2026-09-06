"""Offline v2 golden-flow recount and no-clobber summary emission."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    from app.agent.release_artifacts import component_ref, write_private
    from app.agent.release_golden import verify_mock_golden
    from app.agent.u10_cli import Parser, failure_code

    parser = Parser(description=__doc__)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--revision", required=True)
    try:
        args = parser.parse_args(argv)
        summary, _, _ = verify_mock_golden(
            root=args.attempt_root,
            evidence=component_ref(
                args.attempt_root, "evidence/mock-notify-evidence.json"
            ),
            expected_attempt_id=args.attempt_id,
            expected_revision=args.revision,
        )
        ref = write_private(args.attempt_root, "golden-flow.json", summary)
        print(json.dumps(dict(status="PASS", sha256=ref.sha256)))
        return 0
    except Exception as exc:
        print(json.dumps(dict(status="FAIL", code=failure_code(exc))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
