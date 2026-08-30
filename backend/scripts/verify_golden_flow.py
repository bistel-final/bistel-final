"""V5-C-6.1 golden-flow evidence와 Runtime snapshot을 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.golden_flow import (  # noqa: E402
    DATASET_EPOCH,
    ArtifactKind,
    EvidenceArtifact,
    EvidenceBundle,
    ExecutionScope,
    GateKind,
    GoldenFlowContractError,
    GoldenPhase,
    PhaseStatus,
    evaluate_golden_flow,
    load_expected_oracle,
    snapshot_from_mapping,
    validate_expected_oracle_source,
)
from app.agent.golden_flow_repository import (  # noqa: E402
    GoldenFlowRepositoryError,
    GoldenFlowTargetMismatch,
    read_golden_flow_snapshot,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_EVIDENCE = 3
TARGET_DATABASE = "kosa_agent_e2e"
ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"
SOURCE_MANIFEST_PATH = REPOSITORY_ROOT / "infra/bootstrap/source-manifest-v4.json"

_TOP_LEVEL_KEYS = {
    "format_version",
    "dataset_epoch",
    "gate_kind",
    "level_round",
    "phases",
    "artifacts",
}
_PHASE_KEYS = {"execution_scope", "artifact_ids"}
_ARTIFACT_KEYS = {
    "artifact_id",
    "kind",
    "relative_path",
    "sha256",
    "phase",
    "level_round",
    "media_type",
}
_FINAL_KEYS = {
    "type",
    "attempted",
    "succeeded",
    "failed",
    "skipped",
    "new_runs_observed",
    "new_actions_observed",
    "new_deliveries_observed",
}
_OUTCOMES = {
    "STARTED_COMPLETED",
    "STARTED_WAITING_APPROVAL",
    "FAILED",
    "CONTRACT_FAILURE",
    "INCOMPLETE_RUN",
    "SKIPPED_RACE",
    "RESOLVER_REJECTED",
    "ALARM_OCCURRED_AT_MISSING",
}


class EvidenceInvalid(ValueError):
    pass


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _UsageError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__, add_help=False)
    parser.add_argument("--database", required=True, choices=(TARGET_DATABASE,))
    parser.add_argument("--evidence-file", required=True, type=Path)
    parser.add_argument("--phase", choices=tuple(item.value for item in GoldenPhase))
    return parser


def _emit(payload: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
    )


def _stderr(code: str) -> None:
    print(code, file=sys.stderr)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc


def _exact_object(value: object, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return value


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return value


def _sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return value


def _safe_artifact_path(root: Path, relative: object) -> tuple[str, Path]:
    raw = _text(relative)
    pure = PurePosixPath(raw)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    normalized = pure.as_posix()
    if normalized != raw:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise EvidenceInvalid("EVIDENCE_INVALID")
    try:
        mode = candidate.stat().st_mode
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc
    if not stat.S_ISREG(mode):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return normalized, resolved


def _artifact_payload(path: Path, kind: ArtifactKind, media_type: object) -> Any:
    expected_media = (
        "application/x-ndjson"
        if kind is ArtifactKind.BATCH_NDJSON
        else "application/json"
    )
    if media_type != expected_media:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    if kind is ArtifactKind.BATCH_NDJSON:
        return _parse_batch_ndjson(path)
    value = _load_json(path)
    if kind is ArtifactKind.DB_SNAPSHOT:
        snapshot_from_mapping(value)
        return value
    if kind is ArtifactKind.KAFKA_OFFSETS:
        return _parse_kafka(value)
    if kind is ArtifactKind.HTTP_RESULTS:
        return _parse_http(value)
    if kind is ArtifactKind.N8N_EXECUTIONS:
        return _parse_n8n(value)
    if kind is ArtifactKind.SMTP_RECEIPT:
        return _parse_smtp(value)
    raise EvidenceInvalid("EVIDENCE_INVALID")


def _parse_batch_ndjson(path: Path) -> list[dict[str, Any]]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc
    if not raw_lines or any(not line.strip() for line in raw_lines):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    lines: list[dict[str, Any]] = []
    for raw in raw_lines:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceInvalid("EVIDENCE_INVALID") from exc
        if not isinstance(value, dict):
            raise EvidenceInvalid("EVIDENCE_INVALID")
        line_type = value.get("type")
        if line_type == "plan":
            _validate_plan(value)
        elif line_type == "incident":
            _validate_incident(value)
        elif line_type == "final":
            _validate_final(value)
        else:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        lines.append(value)
    if sum(line["type"] == "plan" for line in lines) > 1:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    if sum(line["type"] == "final" for line in lines) > 1:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return lines


def _validate_plan(value: Mapping[str, Any]) -> None:
    _exact_object(
        value,
        {"type", "database", "selected", "rejected", "incomplete", "excluded"},
    )
    if value["type"] != "plan" or value["database"] != TARGET_DATABASE:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    for key in ("selected", "rejected", "incomplete"):
        if not isinstance(value[key], list):
            raise EvidenceInvalid("EVIDENCE_INVALID")
    for selected in value["selected"]:
        item = _exact_object(
            selected,
            {"lot_id", "chamber_id", "member_count", "representative"},
        )
        _text(item["lot_id"])
        _text(item["chamber_id"])
        if _nonnegative_int(item["member_count"]) < 1:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        representative = _exact_object(item["representative"], {"source", "alarm_id"})
        if representative["source"] not in {"TRACE", "SUMMARY", "R03"}:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        _text(representative["alarm_id"])
    for key in ("rejected", "incomplete"):
        for raw in value[key]:
            item = _exact_object(raw, {"lot_id", "chamber_id", "reason"})
            _text(item["lot_id"])
            _text(item["chamber_id"])
            _text(item["reason"])
    excluded = _exact_object(
        value["excluded"],
        {"canonical_null_rows", "canonical_null_by_source"},
    )
    _nonnegative_int(excluded["canonical_null_rows"])
    by_source = excluded["canonical_null_by_source"]
    if not isinstance(by_source, dict) or any(
        source not in {"TRACE", "SUMMARY", "R03"} or _nonnegative_int(count) < 1
        for source, count in by_source.items()
    ):
        raise EvidenceInvalid("EVIDENCE_INVALID")


def _validate_incident(value: Mapping[str, Any]) -> None:
    allowed = {"type", "lot_id", "chamber_id", "outcome"}
    if frozenset(value) not in {
        frozenset(allowed),
        frozenset({*allowed, "agent_run_id"}),
    }:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    if value["type"] != "incident" or value["outcome"] not in _OUTCOMES:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    _text(value["lot_id"])
    _text(value["chamber_id"])
    if "agent_run_id" in value:
        _text(value["agent_run_id"])


def _validate_final(value: Mapping[str, Any]) -> None:
    _exact_object(value, _FINAL_KEYS)
    if value["type"] != "final":
        raise EvidenceInvalid("EVIDENCE_INVALID")
    for key in _FINAL_KEYS - {"type"}:
        _nonnegative_int(value[key])


def _parse_kafka(value: object) -> Mapping[str, Any]:
    item = _exact_object(value, {"format_version", "topic", "before", "after"})
    if item["format_version"] != 1 or item["topic"] != "fdc.actions":
        raise EvidenceInvalid("EVIDENCE_INVALID")
    _nonnegative_int(item["before"])
    _nonnegative_int(item["after"])
    return item


def _parse_http(value: object) -> Mapping[str, Any]:
    root = _exact_object(value, {"format_version", "results"})
    if root["format_version"] != 1 or not isinstance(root["results"], list):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    allowed = {
        "case",
        "status_code",
        "action_id",
        "exit_code",
        "duration_ms",
        "before_status",
        "after_status",
    }
    for raw in root["results"]:
        if not isinstance(raw, Mapping) or not {"case"} <= set(raw) <= allowed:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        _text(raw["case"])
        for key in ("status_code", "exit_code", "duration_ms"):
            if key in raw:
                _nonnegative_int(raw[key])
        if "action_id" in raw and raw["action_id"] is not None:
            _text(raw["action_id"])
        for key in ("before_status", "after_status"):
            if key in raw:
                _text(raw[key])
    return root


def _parse_n8n(value: object) -> Mapping[str, Any]:
    root = _exact_object(value, {"format_version", "executions"})
    if root["format_version"] != 1 or not isinstance(root["executions"], list):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    for raw in root["executions"]:
        item = _exact_object(raw, {"workflow", "action_id", "status", "execution_id"})
        if item["workflow"] not in {"WF2", "WF3", "WF4"} or item["status"] not in {
            "SUCCESS",
            "FAILED",
        }:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        _text(item["action_id"])
        _text(item["execution_id"])
    return root


def _parse_smtp(value: object) -> Mapping[str, Any]:
    root = _exact_object(value, {"format_version", "receipts"})
    if root["format_version"] != 1 or not isinstance(root["receipts"], list):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    for raw in root["receipts"]:
        item = _exact_object(raw, {"action_id", "status", "receipt_id"})
        if item["status"] != "SENT":
            raise EvidenceInvalid("EVIDENCE_INVALID")
        _text(item["action_id"])
        _text(item["receipt_id"])
    return root


def load_evidence_bundle(path: Path) -> EvidenceBundle:
    """schema→path→hash→kind parser 순서로 bundle 전체를 검증한다."""

    if path.is_symlink():
        raise EvidenceInvalid("EVIDENCE_INVALID")
    try:
        manifest_path = path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc
    root = _exact_object(_load_json(manifest_path), _TOP_LEVEL_KEYS)
    if root["format_version"] != 1 or root["dataset_epoch"] != DATASET_EPOCH:
        raise EvidenceInvalid("EVIDENCE_INVALID")
    try:
        gate_kind = GateKind(root["gate_kind"])
    except (TypeError, ValueError) as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc
    if gate_kind is GateKind.PUBLIC_GOLDEN_FLOW:
        if root["level_round"] != 2:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        rounds = (2,)
    else:
        if root["level_round"] != [1, 2]:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        rounds = (1, 2)

    raw_phases = root["phases"]
    raw_artifacts = root["artifacts"]
    if not isinstance(raw_phases, Mapping) or not isinstance(raw_artifacts, list):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    scopes: dict[GoldenPhase, ExecutionScope] = {}
    references: dict[str, GoldenPhase] = {}
    for raw_phase, raw_config in raw_phases.items():
        try:
            phase = GoldenPhase(raw_phase)
            config = _exact_object(raw_config, _PHASE_KEYS)
            scope = ExecutionScope(config["execution_scope"])
        except (TypeError, ValueError) as exc:
            raise EvidenceInvalid("EVIDENCE_INVALID") from exc
        expected_scope = (
            ExecutionScope.ISOLATED_CONTAINER
            if gate_kind is GateKind.LEVEL_COMPARISON
            or phase is GoldenPhase.MANUAL_RETRY
            else ExecutionScope.PUBLIC_E2E
        )
        if scope is not expected_scope or not isinstance(config["artifact_ids"], list):
            raise EvidenceInvalid("EVIDENCE_INVALID")
        scopes[phase] = scope
        for raw_id in config["artifact_ids"]:
            artifact_id = _text(raw_id)
            if artifact_id in references:
                raise EvidenceInvalid("EVIDENCE_INVALID")
            references[artifact_id] = phase

    ids: set[str] = set()
    paths: set[str] = set()
    artifacts: list[EvidenceArtifact] = []
    artifact_root = manifest_path.parent
    for raw in raw_artifacts:
        item = _exact_object(raw, _ARTIFACT_KEYS)
        artifact_id = _text(item["artifact_id"])
        try:
            kind = ArtifactKind(item["kind"])
            phase = GoldenPhase(item["phase"])
        except (TypeError, ValueError) as exc:
            raise EvidenceInvalid("EVIDENCE_INVALID") from exc
        level_round = _nonnegative_int(item["level_round"])
        if level_round not in rounds:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        normalized, artifact_path = _safe_artifact_path(
            artifact_root, item["relative_path"]
        )
        if artifact_id in ids or normalized in paths:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        ids.add(artifact_id)
        paths.add(normalized)
        if references.get(artifact_id) is not phase:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        expected_hash = _sha256(item["sha256"])
        try:
            actual_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise EvidenceInvalid("EVIDENCE_INVALID") from exc
        if actual_hash != expected_hash:
            raise EvidenceInvalid("EVIDENCE_INVALID")
        payload = _artifact_payload(artifact_path, kind, item["media_type"])
        artifacts.append(
            EvidenceArtifact(artifact_id, kind, phase, level_round, payload)
        )
    if ids != set(references):
        raise EvidenceInvalid("EVIDENCE_INVALID")
    return EvidenceBundle(DATASET_EPOCH, gate_kind, rounds, scopes, tuple(artifacts))


def _load_oracle() -> Any:
    payload = _load_json(ORACLE_PATH)
    oracle = load_expected_oracle(payload)
    try:
        source_hash = hashlib.sha256(SOURCE_MANIFEST_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc
    try:
        validate_expected_oracle_source(oracle, source_hash)
    except GoldenFlowContractError as exc:
        raise EvidenceInvalid("EVIDENCE_INVALID") from exc
    return oracle


def main(
    argv: Sequence[str] | None = None,
    *,
    engine_factory: Callable[[], Any] | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except _UsageError:
        _stderr("USAGE_INVALID")
        return EXIT_USAGE
    try:
        evidence = load_evidence_bundle(args.evidence_file)
        oracle = _load_oracle()
        phase = None if args.phase is None else GoldenPhase(args.phase)
    except (EvidenceInvalid, GoldenFlowContractError):
        _stderr("EVIDENCE_INVALID")
        return EXIT_EVIDENCE

    try:
        if engine_factory is None:
            from app.common.db import get_app_engine

            engine_factory = get_app_engine
        snapshot = read_golden_flow_snapshot(engine_factory(), database=args.database)
    except GoldenFlowTargetMismatch:
        _stderr("TARGET_MISMATCH")
        return EXIT_EVIDENCE
    except GoldenFlowRepositoryError:
        _stderr("GOLDEN_FLOW_VERIFY_FAILED")
        return EXIT_FAILED

    try:
        result = evaluate_golden_flow(snapshot, evidence, oracle, phase=phase)
    except GoldenFlowContractError:
        _stderr("EVIDENCE_INVALID")
        return EXIT_EVIDENCE
    for item in result.phases:
        _emit(
            {
                "type": "phase",
                "phase": item.phase.value,
                "status": item.status.value,
                "reasons": list(item.reasons),
                "metrics": item.metrics,
            }
        )
    if result.status is PhaseStatus.FAIL:
        final_status = "PHASE_FAIL" if phase is not None else "GOLDEN_FLOW_FAIL"
        exit_code = EXIT_FAILED
    elif result.status is PhaseStatus.EVIDENCE_INCOMPLETE:
        final_status = "EVIDENCE_INCOMPLETE"
        exit_code = EXIT_EVIDENCE
    else:
        final_status = "PHASE_PASS" if phase is not None else "GOLDEN_FLOW_PASS"
        exit_code = EXIT_OK
    final: dict[str, Any] = {"type": "final", "status": final_status}
    if phase is not None:
        final["scope"] = "PHASE"
    _emit(final)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
