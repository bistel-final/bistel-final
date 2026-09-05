"""Manual SMTP_SEND_GRANT writer. Never invoke automatically from prepare.

An approver must inspect the exact recipients in the private prepared artifact
before supplying the attempt/hash/count confirmation. No email is sent here.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.agent.release_artifacts import (  # noqa: E402
    EvidenceError,
    component_ref,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_lifecycle import (  # noqa: E402
    classify_state,
    lifecycle_lock,
    read_lifecycle,
)
from app.agent.release_prepared import (  # noqa: E402
    SMTP_APPROVERS,
    PreparedAttempt,
    SmtpGrant,
    validate_grant,
)


def issue_grant(
    prepared_path: Path,
    *,
    approver: str,
    approval_reference: str,
    confirmation: str,
    approved_at: str,
) -> dict[str, object]:
    if prepared_path.name != "prepared-attempt.json":
        raise EvidenceError("PREPARED_COMPONENT_INVALID")
    root = prepared_path.parent
    with lifecycle_lock(root):
        if classify_state(read_lifecycle(root)) != "PREPARED":
            raise EvidenceError("LIFECYCLE_TRANSITION_INVALID")
        prepared = PreparedAttempt.model_validate(
            parse_json(read_private(root, prepared_path.name))
        )
        expected = (
            f"SMTP_SEND_GRANT {prepared.attempt_id} "
            f"{prepared.recipient.canonical_hash} 7"
        )
        if confirmation != expected:
            raise EvidenceError("SMTP_EXPLICIT_CONFIRMATION_REQUIRED")
        grant = SmtpGrant(
            schema_version="smtp-send-grant-v1",
            grant_type="SMTP_SEND_GRANT",
            attempt_id=prepared.attempt_id,
            prepared_attempt=component_ref(root, prepared_path.name),
            approval_reference=approval_reference,
            approver=approver,
            recipient_canonical_addresses=prepared.recipient.canonical_addresses,
            recipient_canonical_hash=prepared.recipient.canonical_hash,
            recipient_hash_version=2,
            max_external_emails=7,
            approved_at=approved_at,
        )
        validate_grant(root, prepared, grant, resume_at=approved_at)
        reference = write_private(root, "smtp-approval-grant.json", grant)
    return {
        "status": "PASS",
        "grant_type": "SMTP_SEND_GRANT",
        "attempt_id": prepared.attempt_id,
        "recipient_hash": prepared.recipient.canonical_hash,
        "recipient_hash_version": 2,
        "recipient_count": prepared.recipient.count,
        "max_external_emails": 7,
        "approved_at": approved_at,
        "grant_sha256": reference.sha256,
        "warning": "production is DOWN",
        "next_modes": ["--resume-workload", "--abort-prepared"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-attempt", type=Path, required=True)
    parser.add_argument("--approver", choices=sorted(SMTP_APPROVERS), required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument(
        "--confirm",
        required=True,
        help="SMTP_SEND_GRANT <attempt_id> <recipient_hash> 7",
    )
    args = parser.parse_args(argv)
    try:
        result = issue_grant(
            args.prepared_attempt,
            approver=args.approver,
            approval_reference=args.approval_reference,
            confirmation=args.confirm,
            approved_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except (ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, EvidenceError) else "SMTP_GRANT_INVALID"
        if isinstance(exc, ValidationError):
            code = "SMTP_GRANT_INVALID"
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "code": code,
                    "warning": "production is DOWN if still PREPARED",
                    "next_mode": "--abort-prepared",
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
