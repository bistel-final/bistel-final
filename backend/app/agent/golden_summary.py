"""Golden-flow 7단 결과의 immutable summary 계약."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from app.agent.golden_flow import GoldenFlowResult, GoldenPhase, PhaseStatus

FORMAT_VERSION: Final = 1
ARTIFACT_TYPE: Final = "golden_flow_summary"
_HASH_KEYS: Final = ("source_manifest_sha256", "evidence_manifest_sha256")
_ROOT_KEYS: Final = {
    "format_version",
    "artifact_type",
    "dataset_epoch",
    *_HASH_KEYS,
    "status",
    "phases",
}
_PHASE_KEYS: Final = {"phase", "status", "reasons", "metrics"}
MOCK_PHASES: Final = (
    "PREFLIGHT",
    "BATCH_BASELINE",
    "MOCK_RESULTS",
    "NO_DECISIONS",
    "SECOND_BATCH",
    "UNKNOWN",
    "MANUAL_RETRY",
)


class GoldenSummaryContractError(ValueError):
    """Summary가 공개 전 exact 계약과 다르다."""


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def validate_golden_summary(payload: object) -> Mapping[str, Any]:
    if isinstance(payload, Mapping) and payload.get("protocol") == "MOCK-NOTIFY-V1":
        if set(payload) != _ROOT_KEYS | {"protocol"}:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_KEYS_INVALID")
        if type(payload["format_version"]) is not int or payload["format_version"] != 1:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_VERSION_INVALID")
        if (
            payload["artifact_type"] != ARTIFACT_TYPE
            or payload["dataset_epoch"] != "fdc_final_20260818"
        ):
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_TYPE_INVALID")
        if any(not _sha256(payload[key]) for key in _HASH_KEYS):
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_HASH_INVALID")
        validate_mock_phases(payload["phases"], payload["status"])
        return payload
    if not isinstance(payload, Mapping) or set(payload) != _ROOT_KEYS:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_KEYS_INVALID")
    if type(payload["format_version"]) is not int or payload["format_version"] != 1:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_VERSION_INVALID")
    if payload["artifact_type"] != ARTIFACT_TYPE:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_TYPE_INVALID")
    if (
        not isinstance(payload["dataset_epoch"], str)
        or not payload["dataset_epoch"].strip()
    ):
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_EPOCH_INVALID")
    if any(not _sha256(payload[key]) for key in _HASH_KEYS):
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_HASH_INVALID")
    try:
        overall = PhaseStatus(payload["status"])
    except (TypeError, ValueError) as exc:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_STATUS_INVALID") from exc

    phases = payload["phases"]
    expected = tuple(GoldenPhase)
    if not isinstance(phases, list) or len(phases) != len(expected):
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_PHASES_INVALID")
    statuses: list[PhaseStatus] = []
    for raw, phase in zip(phases, expected, strict=True):
        if not isinstance(raw, Mapping) or set(raw) != _PHASE_KEYS:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_PHASE_INVALID")
        if raw["phase"] != phase.value:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_PHASE_ORDER_INVALID")
        try:
            status = PhaseStatus(raw["status"])
        except (TypeError, ValueError) as exc:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_STATUS_INVALID") from exc
        reasons = raw["reasons"]
        if (
            not isinstance(reasons, list)
            or len(reasons) != len(set(reasons))
            or any(not isinstance(reason, str) or not reason for reason in reasons)
        ):
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_REASONS_INVALID")
        if not isinstance(raw["metrics"], Mapping):
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_METRICS_INVALID")
        try:
            json.dumps(raw["metrics"], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_METRICS_INVALID") from exc
        statuses.append(status)

    derived = (
        PhaseStatus.FAIL
        if PhaseStatus.FAIL in statuses
        else (
            PhaseStatus.EVIDENCE_INCOMPLETE
            if PhaseStatus.EVIDENCE_INCOMPLETE in statuses
            else PhaseStatus.PASS
        )
    )
    if overall is not derived:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_STATUS_MISMATCH")
    return payload


def validate_mock_phases(phases, status, *, private=True):
    """Five live observations, two explicitly unmeasured live scenarios."""
    if not isinstance(phases, list) or len(phases) != 7:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_PHASES_INVALID")
    live = []
    for index, (row, phase) in enumerate(zip(phases, MOCK_PHASES, strict=True)):
        keys = _PHASE_KEYS | ({"evidence"} if private and index >= 5 else set())
        if not isinstance(row, Mapping) or set(row) != keys or row["phase"] != phase:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_PHASE_ORDER_INVALID")
        if index >= 5:
            if row["status"] != "NOT_LIVE":
                raise GoldenSummaryContractError("GOLDEN_SUMMARY_NOT_LIVE_REQUIRED")
            if private:
                reference = row["evidence"]
                if (
                    not isinstance(reference, Mapping)
                    or set(reference) != {"test_ref", "revision"}
                    or not isinstance(reference["test_ref"], str)
                    or not reference["test_ref"].startswith("tests/")
                    or ".." in reference["test_ref"].split("/")
                    or not isinstance(reference["revision"], str)
                    or len(reference["revision"]) != 40
                    or any(c not in "0123456789abcdef" for c in reference["revision"])
                ):
                    raise GoldenSummaryContractError(
                        "GOLDEN_SUMMARY_TEST_EVIDENCE_INVALID"
                    )
        elif row["status"] not in {"PASS", "FAIL", "EVIDENCE_INCOMPLETE"}:
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_STATUS_INVALID")
        else:
            live.append(row["status"])
        reasons = row["reasons"]
        if (
            not isinstance(reasons, list)
            or any(not isinstance(v, str) or not v for v in reasons)
            or len(set(reasons)) != len(reasons)
            or not isinstance(row["metrics"], Mapping)
        ):
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_PHASE_INVALID")
        try:
            json.dumps(row["metrics"], allow_nan=False)
        except (TypeError, ValueError):
            raise GoldenSummaryContractError("GOLDEN_SUMMARY_METRICS_INVALID") from None
    expected = (
        "FAIL"
        if "FAIL" in live
        else "EVIDENCE_INCOMPLETE"
        if "EVIDENCE_INCOMPLETE" in live
        else "PASS"
    )
    if status != expected:
        raise GoldenSummaryContractError("GOLDEN_SUMMARY_STATUS_MISMATCH")


def build_golden_summary(
    result: GoldenFlowResult,
    *,
    dataset_epoch: str,
    source_manifest_sha256: str,
    evidence_manifest_sha256: str,
) -> dict[str, Any]:
    payload = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "dataset_epoch": dataset_epoch,
        "source_manifest_sha256": source_manifest_sha256,
        "evidence_manifest_sha256": evidence_manifest_sha256,
        "status": result.status.value,
        "phases": [
            {
                "phase": item.phase.value,
                "status": item.status.value,
                "reasons": list(item.reasons),
                "metrics": dict(item.metrics),
            }
            for item in result.phases
        ],
    }
    validate_golden_summary(payload)
    return payload


__all__ = [
    "ARTIFACT_TYPE",
    "FORMAT_VERSION",
    "GoldenSummaryContractError",
    "build_golden_summary",
    "validate_golden_summary",
]
