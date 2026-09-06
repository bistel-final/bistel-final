"""Stage2-owned pre-HELD file barrier, never a new lifecycle controller.

Requires an inherited exclusive lock and the live parent Bash resume claim.
No sends, HTTP, DB, artifacts, new claim, cleanup or deployment are performed.
Only missing evidence is waited for (at most 300s); failures return to the owner.
"""

import json
import os
import socket
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agent.release_approval_evidence import wait_approval_evidence  # noqa: E402
from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    parse_json,
    validate_report_root,
)
from app.agent.release_lifecycle import (  # noqa: E402
    LifecycleClaim,
    claim_filename,
    classify_state,
    lifecycle_lock,
    owner_identity,
    read_lifecycle,
    verify_lifecycle_lock,
)
from app.agent.release_prepared import parse_prepared  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def barrier(*, report_root, repository, attempt_id, lock_fd, timeout_seconds):
    import re

    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", attempt_id):
        raise EvidenceError("ATTEMPT_ID_MISMATCH")
    if repository.resolve(strict=True) != BACKEND.parent.resolve(strict=True):
        raise EvidenceError("APPROVAL_EVIDENCE_REPOSITORY_INVALID")
    root = validate_report_root(report_root, report_root, repository)
    relative = f"cm-5.2/{attempt_id}/robustness"
    with lifecycle_lock(root, relative_directory=relative, inherited_fd=lock_fd):
        original = read_lifecycle(root, relative_directory=relative)

        def check_owner():
            verify_lifecycle_lock(root, lock_fd, relative_directory=relative)
            files = read_lifecycle(root, relative_directory=relative)
            if (
                files != original
                or classify_state(files) != "UNRESOLVED"
                or claim_filename("RESUME_WORKLOAD") not in files
            ):
                raise EvidenceError("APPROVAL_EVIDENCE_OWNER_STATE_INVALID")
            prepared = parse_prepared(
                parse_json(files["prepared-attempt.json"])
            )
            claim = LifecycleClaim.model_validate(
                parse_json(files[claim_filename("RESUME_WORKLOAD")])
            )
            if prepared.attempt_id != attempt_id or (
                claim.pid != os.getppid()
                or claim.host != socket.gethostname()
                or owner_identity(claim.pid)
                != (claim.boot_id, claim.process_start_time)
            ):
                raise EvidenceError("LIFECYCLE_PARENT_REQUIRED")

        reference, evidence = wait_approval_evidence(
            root / "cm-5.2" / attempt_id,
            check_owner=check_owner,
            timeout_seconds=timeout_seconds,
        )
        return {
            "status": "OBSERVED",
            "attempt_id": attempt_id,
            "approval_evidence": reference.model_dump(),
            "execution_count": len(evidence.executions),
            "smtp_send_authorized": False,
            "qualification_authorized": False,
        }


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("report-root", "repository"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--lifecycle-lock-fd", required=True, type=int)
    parser.add_argument("--timeout-seconds", default=300, type=int)
    try:
        args = parser.parse_args(argv)
        result = barrier(
            report_root=args.report_root,
            repository=args.repository,
            attempt_id=args.attempt_id,
            lock_fd=args.lifecycle_lock_fd,
            timeout_seconds=args.timeout_seconds,
        )
    except KeyboardInterrupt:
        print('{"status":"FAIL","code":"APPROVAL_EVIDENCE_INTERRUPTED"}')
        return 130
    except Exception as error:
        print(
            json.dumps({"status": "FAIL", "code": failure_code(error)}, sort_keys=True)
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
