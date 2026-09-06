"""Record three separately observed WF2 execution IDs; never send or approve.

The operator reads the actual n8n execution detail for each approval-request
action. This writer preserves the existing golden-flow N8N_EXECUTIONS format.
It never queries the seven-acceptance inventory or manufactures inbox receipts.
"""

import json
import os
import socket
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agent.release_approval_evidence import (  # noqa: E402
    APPROVAL_EXECUTIONS,
    APPROVAL_READY,
    ApprovalExecutions,
    _present,
)
from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    component_parent,
    digest,
    parse_json,
    validate_report_root,
    write_private,
)
from app.agent.release_lifecycle import (  # noqa: E402
    LifecycleClaim,
    claim_filename,
    classify_state,
    owner_identity,
    read_lifecycle,
)
from app.agent.release_prepared import parse_prepared  # noqa: E402
from app.agent.u10_cli import Parser, failure_code  # noqa: E402


def record(*, report_root, repository, attempt_id, resume_claim_sha256, executions):
    import re

    if not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", attempt_id
    ) or not re.fullmatch(r"[0-9a-f]{64}", resume_claim_sha256):
        raise EvidenceError("APPROVAL_EVIDENCE_BINDING_INVALID")
    rows = []
    for value in executions:
        action, separator, execution = value.partition("=")
        if not separator or "=" in execution:
            raise EvidenceError("APPROVAL_EVIDENCE_ARGUMENT_INVALID")
        rows.append(
            dict(
                workflow="WF2",
                action_id=action,
                status="SUCCESS",
                execution_id=execution,
            )
        )
    try:
        evidence = ApprovalExecutions(format_version=1, executions=rows)
    except Exception:
        raise EvidenceError("APPROVAL_EVIDENCE_ARGUMENT_INVALID") from None
    if repository.resolve(strict=True) != BACKEND.parent.resolve(strict=True):
        raise EvidenceError("APPROVAL_EVIDENCE_REPOSITORY_INVALID")
    root = validate_report_root(report_root, report_root, repository)
    relative = f"cm-5.2/{attempt_id}/robustness"

    def snapshot():
        files = read_lifecycle(root, relative_directory=relative)
        if (
            classify_state(files) != "UNRESOLVED"
            or claim_filename("RESUME_WORKLOAD") not in files
        ):
            raise EvidenceError("APPROVAL_EVIDENCE_OWNER_STATE_INVALID")
        raw = files[claim_filename("RESUME_WORKLOAD")]
        if digest(raw) != resume_claim_sha256:
            raise EvidenceError("APPROVAL_EVIDENCE_CLAIM_MISMATCH")
        prepared = parse_prepared(
            parse_json(files["prepared-attempt.json"])
        )
        claim = LifecycleClaim.model_validate(parse_json(raw))
        if prepared.attempt_id != attempt_id or (
            claim.host != socket.gethostname()
            or owner_identity(claim.pid) != (claim.boot_id, claim.process_start_time)
        ):
            raise EvidenceError("APPROVAL_EVIDENCE_OWNER_NOT_LIVE")
        return files

    before = snapshot()
    attempt = root / "cm-5.2" / attempt_id
    if _present(attempt):
        raise EvidenceError("ARTIFACT_EXISTS")
    # This independent writer must not acquire Stage2's exclusive lock: its
    # owner is waiting for this external file. Only the noncanonical attempt
    # evidence is written, O_EXCL. All lifecycle files remain read-only.
    reference = write_private(attempt, APPROVAL_EXECUTIONS, evidence)
    if snapshot() != before:
        # Preserve a raced/failed write as evidence; never overwrite or retry it.
        raise EvidenceError("APPROVAL_EVIDENCE_OWNER_DRIFT")
    # Empty marker: the atomic O_EXCL create is itself the complete payload.
    # No fchmod/data write follows publication, so a waiter cannot see a partial
    # JSON marker. Source bytes and directory were fsynced by write_private.
    with component_parent(attempt, APPROVAL_READY) as (parent, name):
        previous_mask = os.umask(0o077)
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
        finally:
            os.umask(previous_mask)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    return {
        "status": "RECORDED",
        "approval_evidence": reference.model_dump(),
        "smtp_send_authorized": False,
        "qualification_authorized": False,
    }


def main(argv=None):
    parser = Parser(description=__doc__)
    for name in ("report-root", "repository"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--resume-claim-sha256", required=True)
    parser.add_argument("--execution", action="append", required=True)
    try:
        args = parser.parse_args(argv)
        value = record(
            report_root=args.report_root,
            repository=args.repository,
            attempt_id=args.attempt_id,
            resume_claim_sha256=args.resume_claim_sha256,
            executions=args.execution,
        )
    except KeyboardInterrupt:
        print('{"status":"FAIL","code":"APPROVAL_EVIDENCE_INTERRUPTED"}')
        return 130
    except Exception as error:
        print(
            json.dumps({"status": "FAIL", "code": failure_code(error)}, sort_keys=True)
        )
        return 1
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
