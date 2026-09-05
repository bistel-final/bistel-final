"""Issue one evaluation receipt after offline verification at a clean main R.

No LLM/DB/Docker calls. Does not assert that the caller's artifact was a live run.
The real offline verifier command is executed and recorded; never from input.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    parse_json,
    read_private,
    write_private,
)
from app.agent.u10_comparison import U10_VERDICT_RULES, validate_artifact
from app.agent.u10_deployment import _now, _stamp
from app.agent.u10_evaluation import EvaluationReceipt
from app.agent.u10_revision import verify_execution_revision


def _receipt_directory(repository: Path, revision: str) -> Path:
    path = repository
    for name in ("output", "v5-c-7.1", revision):
        path = path / name
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise EvidenceError("U10_RECEIPT_DIRECTORY_INVALID")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise EvidenceError("U10_RECEIPT_DIRECTORY_INVALID")
    return path


def emit_evaluation_receipt(
    *, repository: Path, artifact: Path, benchmark: Path, benchmark_sha256: str
) -> dict:
    benchmark_bytes = read_private(benchmark.parent, benchmark.name)
    if digest(benchmark_bytes) != benchmark_sha256:
        raise EvidenceError("PINNED_BENCHMARK_SHA_MISMATCH")
    artifact_bytes = read_private(artifact.parent, artifact.name)
    a, b = parse_json(artifact_bytes), parse_json(benchmark_bytes)
    result = validate_artifact(a, b)
    identity = verify_execution_revision(repository, a["evaluated_revision"])
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[2] / "scripts/verify_u10_comparison.py"),
        "--artifact",
        str(artifact.absolute()),
        "--benchmark",
        str(benchmark.absolute()),
        "--benchmark-sha256",
        benchmark_sha256,
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=30, check=False
        )
        if completed.returncode or len(completed.stdout) > 16384:
            raise EvidenceError("U10_RECEIPT_VALIDATOR_FAILED")
        output = parse_json(completed.stdout)
        if canonical_json(output) != canonical_json(
            {
                "integrity": "PASS",
                "artifact_sha256": digest(artifact_bytes),
                "agent_verdict": result["agent_verdict"],
                "verdict_reason": result["verdict_reason"],
                "inspection_only": True,
            }
        ):
            raise EvidenceError("U10_RECEIPT_VALIDATOR_FAILED")
    except (OSError, subprocess.TimeoutExpired, EvidenceError):
        raise EvidenceError("U10_RECEIPT_VALIDATOR_FAILED") from None
    if (
        read_private(artifact.parent, artifact.name) != artifact_bytes
        or read_private(benchmark.parent, benchmark.name) != benchmark_bytes
    ):
        raise EvidenceError("U10_RECEIPT_INPUT_DRIFT")
    if verify_execution_revision(repository, a["evaluated_revision"]) != identity:
        raise EvidenceError("U10_RECEIPT_REVISION_DRIFT")
    receipt = EvaluationReceipt(
        schema_version="u10-evaluation-receipt-v1",
        artifact_sha256=digest(artifact_bytes),
        **{
            key: b[key]
            for key in (
                "fixture_sha256",
                "oracle_sha256",
                "inventory_sha256",
                "tool_contract_sha256",
                "fixed_policy_sha256",
            )
        },
        verdict_rules_sha256=a["verdict_rules_sha256"],
        effective_budget_policy=U10_VERDICT_RULES["budget"],
        validator_command=command,
        validator_exit_code=0,
        agent_justification_verdict=result["agent_verdict"],
        evaluated_revision=identity.evaluated_revision,
        evaluated_tree_oid=identity.evaluated_tree_oid,
        git_object_format=identity.git_object_format,
        decided_at=_stamp(_now()),
    )
    directory = _receipt_directory(
        Path(identity.repository_root), identity.evaluated_revision
    )
    verify_execution_revision(repository, identity.evaluated_revision)
    component = write_private(directory, "u10-evaluation-receipt.json", receipt)
    return {
        "status": "PASS",
        "receipt_path": str(directory / component.relative_path),
        "receipt_sha256": component.sha256,
        "artifact_sha256": receipt.artifact_sha256,
        "evaluated_revision": identity.evaluated_revision,
        "agent_verdict": result["agent_verdict"],
    }
