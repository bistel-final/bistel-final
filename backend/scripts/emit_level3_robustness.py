"""Issue aggregate, seal, or public projection; no live capture yet.

This command never grants SMTP approval, sends email, resets data, changes
containers, or enables production. Round/completion capture remains a separate
pending Stage2 integration. Public projection is never a deployment input.
"""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_aggregate import emit_aggregate  # noqa: E402
from app.agent.release_artifacts import EvidenceError  # noqa: E402
from app.agent.release_seal import (  # noqa: E402
    copy_sealed_bundle,
    emit_public,
    seal_bundle,
)
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def main(argv=None):
    parser = Parser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ("aggregate", "seal", "public", "copy-seal"):
        modes.add_argument("--" + name, action="store_true")
    for name in ("bundle-root", "published-root", "repository"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expect-revision", required=True)
    parser.add_argument("--expect-attempt-id", required=True)
    parser.add_argument("--public-root", type=Path)
    parser.add_argument("--destination", type=Path)
    try:
        args = parser.parse_args(argv)
        if args.public != (args.public_root is not None):
            raise EvidenceError("PUBLIC_OUTPUT_ARGUMENT_INVALID")
        if args.copy_seal != (args.destination is not None):
            raise EvidenceError("SEAL_COPY_ARGUMENT_INVALID")
        kwargs = dict(
            root=args.bundle_root,
            repository=args.repository,
            published_root=args.published_root,
            expected_revision=args.expect_revision,
            expected_attempt_id=args.expect_attempt_id,
        )
        if args.seal or args.copy_seal:
            reference = (
                copy_sealed_bundle(destination=args.destination, **kwargs)
                if args.copy_seal
                else seal_bundle(**kwargs)
            )
            print(
                json.dumps(
                    {
                        "status": "COPIED_SEAL" if args.copy_seal else "SEALED",
                        "private_manifest_sha256": reference.sha256,
                        "R": args.expect_revision,
                        "attempt_id": args.expect_attempt_id,
                        "deployment_authorized": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.public:
            reference, result = emit_public(public_root=args.public_root, **kwargs)
        else:
            reference, result = emit_aggregate(**kwargs)
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
                "public_report_sha256"
                if args.public
                else "aggregate_sha256": reference.sha256,
                "R": result.R,
                "attempt_id": result.attempt_id,
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
