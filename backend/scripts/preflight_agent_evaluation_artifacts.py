"""게시 전 C-6.1/C-6.2 평가 artifact 두 건의 결속을 fail-closed 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.agent.golden_flow import DATASET_EPOCH
from app.agent.golden_summary import GoldenSummaryContractError, validate_golden_summary
from app.agent.prompts import PROMPT_VERSION
from app.evaluation.fault_5class import FaultEvaluationContractError, validate_artifact

SUPPORTED_PROMPT_VERSIONS = frozenset({"agent-hypothesis-v2-ko1", PROMPT_VERSION})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")


class ArtifactPreflightError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _read_regular_json(path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ArtifactPreflightError("ARTIFACT_NOT_REGULAR") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ArtifactPreflightError("ARTIFACT_NOT_REGULAR")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactPreflightError("ARTIFACT_NOT_JSON") from exc
    if not isinstance(payload, Mapping):
        raise ArtifactPreflightError("ARTIFACT_NOT_JSON")
    return payload, hashlib.sha256(raw).hexdigest()


def preflight(
    *,
    fault_path: Path,
    golden_path: Path,
    expect_fault_sha: str,
    expect_golden_sha: str,
    expect_revision: str,
    expect_container_revision: str | None = None,
    attempt_id: str,
    environ: Mapping[str, str],
) -> None:
    fault, actual_fault_sha = _read_regular_json(fault_path)
    golden, actual_golden_sha = _read_regular_json(golden_path)
    try:
        validate_artifact(fault)
        validate_golden_summary(golden)
    except (FaultEvaluationContractError, GoldenSummaryContractError) as exc:
        raise ArtifactPreflightError("ARTIFACT_INVALID") from exc

    if (
        not SHA256_PATTERN.fullmatch(expect_fault_sha)
        or not SHA256_PATTERN.fullmatch(expect_golden_sha)
        or actual_fault_sha != expect_fault_sha
        or actual_golden_sha != expect_golden_sha
    ):
        raise ArtifactPreflightError("ARTIFACT_SHA_MISMATCH")
    if environ.get("AGENT_FAULT_EVAL_ARTIFACT_PATH") != str(fault_path) or environ.get(
        "AGENT_GOLDEN_FLOW_SUMMARY_PATH"
    ) != str(golden_path):
        raise ArtifactPreflightError("ENV_MISMATCH")
    if fault.get("prompt_version") not in SUPPORTED_PROMPT_VERSIONS:
        raise ArtifactPreflightError("PROMPT_VERSION_MISMATCH")
    if fault.get("golden_evidence_sha256") != golden.get("evidence_manifest_sha256"):
        raise ArtifactPreflightError("EVIDENCE_PAIR_MISMATCH")
    if (
        fault.get("dataset_epoch") != DATASET_EPOCH
        or golden.get("dataset_epoch") != DATASET_EPOCH
    ):
        raise ArtifactPreflightError("EPOCH_MISMATCH")
    container_revision = environ.get("BISTEL_SOURCE_REVISION")
    required_container_revision = (
        expect_revision
        if expect_container_revision is None
        else expect_container_revision
    )
    if (
        not REVISION_PATTERN.fullmatch(expect_revision)
        or not REVISION_PATTERN.fullmatch(required_container_revision)
        or fault.get("code_revision") != expect_revision
        or container_revision != required_container_revision
    ):
        raise ArtifactPreflightError("REVISION_MISMATCH")
    if (
        not ATTEMPT_PATTERN.fullmatch(attempt_id)
        or fault_path.name != "fault-5class.json"
        or golden_path.name != "golden-flow.json"
        or fault_path.parent != golden_path.parent
        or fault_path.parent.name != attempt_id
        or fault_path.parent.parent.name != "cm-5.2"
        or not fault_path.is_absolute()
        or not golden_path.is_absolute()
    ):
        raise ArtifactPreflightError("ATTEMPT_ID_MISMATCH")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--expect-fault-sha", required=True)
    parser.add_argument("--expect-golden-sha", required=True)
    parser.add_argument("--expect-revision", required=True)
    parser.add_argument("--expect-container-revision")
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args(argv)
    try:
        preflight(
            fault_path=args.fault,
            golden_path=args.golden,
            expect_fault_sha=args.expect_fault_sha,
            expect_golden_sha=args.expect_golden_sha,
            expect_revision=args.expect_revision,
            expect_container_revision=args.expect_container_revision,
            attempt_id=args.attempt_id,
            environ=os.environ,
        )
    except ArtifactPreflightError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - 경로·환경 값은 출력하지 않는다.
        print("ARTIFACT_INVALID", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
