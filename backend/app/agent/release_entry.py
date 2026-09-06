"""V5-C-7.1 read-only prepared entry inspection; never authorizes execution.

This pre-initialization check creates no lock or claim. The Stage2 owner must
repeat state/byte checks under its lifecycle lock before any future transition.
TTL/grant/live-runtime validation and stale-owner proof are NOT performed here.
"""

from pathlib import Path

from app.agent.release_artifacts import (
    EvidenceError,
    digest,
    parse_json,
    validate_report_root,
)
from app.agent.release_lifecycle import (
    authorize_transition,
    classify_state,
    read_lifecycle,
)
from app.agent.release_prepared import parse_prepared

MODES = {
    "resume_workload": "RESUME_WORKLOAD",
    "publish": "PUBLISH",
    "abort": "ABORT",
    "recover": "RECOVER",
}


def inspect_entry(
    *,
    report_root: Path,
    repository: Path,
    attempt_id: str,
    mode: str,
    prepared_path: Path,
) -> dict:
    """Observe twice, expose only safe pins/status, never restore shell variables."""
    if mode not in MODES:
        raise EvidenceError("STAGE2_ENTRY_MODE_INVALID")
    # Parse path syntax before filesystem I/O, and never infer a different
    # attempt/report root from caller-controlled path contents.
    import re

    if type(attempt_id) is not str or not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", attempt_id
    ):
        raise EvidenceError("ATTEMPT_ID_MISMATCH")
    root = validate_report_root(report_root, report_root, repository)
    relative = f"cm-5.2/{attempt_id}/robustness"
    expected = root / relative / "prepared-attempt.json"
    if not prepared_path.is_absolute() or prepared_path != expected:
        raise EvidenceError("STAGE2_PREPARED_PATH_MISMATCH")
    before = read_lifecycle(root, relative_directory=relative)
    state = classify_state(before)
    prepared = parse_prepared(parse_json(before["prepared-attempt.json"]))
    if prepared.attempt_id != attempt_id:
        raise EvidenceError("ATTEMPT_ID_MISMATCH")
    authorize_transition(state, MODES[mode])
    if read_lifecycle(root, relative_directory=relative) != before:
        raise EvidenceError("STAGE2_ENTRY_DRIFT")
    return {
        "status": "INSPECTED",
        "mode": mode,
        "observed_state": state,
        "attempt_id": attempt_id,
        "R": prepared.R,
        "prepared_sha256": digest(before["prepared-attempt.json"]),
        "expires_at": prepared.expires_at,
        "execution_authorized": False,
        "smtp_send_authorized": False,
        "deployment_authorized": False,
        "cleanup_performed": False,
        "warning": "production is DOWN"
        if state == "PREPARED"
        else "runtime state must be verified; prior effects may be INDETERMINATE",
    }
