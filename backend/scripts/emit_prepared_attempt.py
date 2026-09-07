"""Internal private Stage2 prepared writer. No live capture, SMTP grant or deployment.

Requires the Stage2 capture transport and A preflight output already written to
the attempt directory. This CLI is not a replacement for Stage2 preparation.
"""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.release_artifacts import Component  # noqa: E402
from app.agent.release_prepare import issue_prepared  # noqa: E402
from app.agent.u10_cli import Parser, failure_code, pins  # noqa: E402


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("report-root", "mounted-report-root", "repository"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "attempt-id",
        "expect-revision",
        "capture-sha256",
        "preflight-sha256",
        "reset-receipt",
        "reset-receipt-sha256",
        "reset-run-id",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--image-id", action="append", required=True)
    parser.add_argument("--lifecycle-lock-fd", type=int)
    try:
        args = parser.parse_args(argv)
        result = issue_prepared(
            report_root=args.report_root,
            mounted_report_root=args.mounted_report_root,
            repository=args.repository,
            attempt_id=args.attempt_id,
            revision=args.expect_revision,
            image_ids=pins(args.image_id, image=True),
            capture_sha256=args.capture_sha256,
            preflight_sha256=args.preflight_sha256,
            reset_receipt=Component(
                relative_path=args.reset_receipt, sha256=args.reset_receipt_sha256
            ),
            reset_run_id=args.reset_run_id,
            lifecycle_lock_fd=args.lifecycle_lock_fd,
        )
    except Exception as exc:
        result = {
            "status": "FAIL",
            "code": failure_code(exc),
            "smtp_send_authorized": False,
            "deployment_authorized": False,
            "warning": "production is DOWN if preparation has started",
            "next_mode": "--abort-prepared if PREPARED, otherwise Stage2 cleanup",
        }
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
