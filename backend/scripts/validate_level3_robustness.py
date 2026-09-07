"""Recompute a private Level 3 aggregate offline. Never enables production."""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_aggregate import verify_aggregate  # noqa: E402
from app.agent.release_artifacts import EvidenceError  # noqa: E402
from app.agent.release_seal import verify_public, verify_seal  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--published-root", type=Path, required=True)
    parser.add_argument("--expect-revision", required=True)
    parser.add_argument("--expect-attempt-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sealed", action="store_true")
    mode.add_argument("--public-bundle-root", type=Path)
    try:
        args = parser.parse_args(argv)
        kwargs = dict(
            published_root=args.published_root,
            expected_revision=args.expect_revision,
            expected_attempt_id=args.expect_attempt_id,
        )
        if args.public_bundle_root is not None:
            result = verify_public(
                path=args.artifact, root=args.public_bundle_root, **kwargs
            )
        elif args.sealed:
            if args.artifact.name != "aggregate.json":
                raise EvidenceError("AGGREGATE_COMPONENT_INVALID")
            result, _ = verify_seal(root=args.artifact.parent, **kwargs)
        else:
            result = verify_aggregate(args.artifact, **kwargs)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "code": failure_code(exc),
                    "deployment_authorized": False,
                }
            )
        )
        return 1
    passed = result.robustness_verdict == result.delivery_integrity == "PASS"
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "R": result.R,
                "attempt_id": result.attempt_id,
                "qualification_scope": result.qualification_scope,
                "round_count": result.round_count,
                "run_count": result.run_count,
                "repeatability": result.repeatability,
                "robustness_verdict": result.robustness_verdict,
                "delivery_integrity": result.delivery_integrity,
                "deployment_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
