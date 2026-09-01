"""검증 완료된 Agent 평가 artifact 두 건의 read-only projection."""

from __future__ import annotations

import json
import logging
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from app.agent.evaluation_schemas import (
    AgentEvaluationResponse,
    AgentFaultEvaluation,
    AgentGoldenEvaluation,
)
from app.agent.golden_summary import (
    GoldenSummaryContractError,
    validate_golden_summary,
)
from app.common import config
from app.evaluation.fault_5class import (
    FAULT_CLASSES,
    FaultEvaluationContractError,
    validate_artifact,
)

logger = logging.getLogger(__name__)
MAX_ARTIFACT_BYTES: Final = 5 * 1024 * 1024
_UNSET: Final = object()


class EvaluationArtifactError(ValueError):
    pass


def _read_json(path_value: str | None) -> tuple[Mapping[str, Any] | None, str | None]:
    if path_value is None:
        return None, "NOT_CONFIGURED"
    path = Path(path_value)
    if not path.is_absolute():
        return None, "CONTRACT_VIOLATION"
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return None, "CONTRACT_VIOLATION"
        if metadata.st_size > MAX_ARTIFACT_BYTES:
            return None, "CONTRACT_VIOLATION"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "NOT_FOUND"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "CONTRACT_VIOLATION"
    if not isinstance(payload, Mapping):
        return None, "CONTRACT_VIOLATION"
    return payload, None


def _fault_projection(payload: Mapping[str, Any]) -> AgentFaultEvaluation:
    validate_artifact(payload)
    classification = payload["classification"]
    return AgentFaultEvaluation(
        versions={
            "dataset_epoch": payload["dataset_epoch"],
            "model_version": payload["model_version"],
            "prompt_version": payload["prompt_version"],
            "policy_version": payload["policy_version"],
        },
        structured_prediction=payload["structured_prediction"],
        evidence_valid_run=payload["evidence_valid_run"],
        rule_action_agreement=payload["rule_action_agreement"],
        classification={
            "population_count": payload["classification_population_count"],
            "accuracy": classification["accuracy"],
            "unclassified_count": classification["unclassified_count"],
            "macro_f1_5class": classification["macro_f1_5class"],
            "observed_class_macro_f1": classification["observed_class_macro_f1"],
            "by_class": {
                name: classification["by_class"][name] for name in FAULT_CLASSES
            },
        },
        exclusions=[
            {
                "reason": "NO_INJECTED_FAULT",
                "count": payload["excluded_no_injected_fault_incident_count"],
                "meaning": "단일 non-NRM 합성 고장 라벨이 없어 5-class 분모에서 제외",
            },
            {
                "reason": "AMBIGUOUS_LABEL",
                "count": payload["ambiguous_label_incident_count"],
                "meaning": "서로 다른 non-NRM 합성 라벨이 둘 이상이라 분모에서 제외",
            },
        ],
        metrology_observed_count=payload["metrology_observed_count"],
        metrology_total_lot_hist_count=payload["metrology_total_lot_hist_count"],
        hard_gate_passed=payload["hard_gate_passed"],
        hard_gate_reasons=payload["hard_gate_reasons"],
        public_fault_ground_truth_available=payload[
            "public_fault_ground_truth_available"
        ],
        production_ground_truth_available=payload["production_ground_truth_available"],
        label_source=payload["label_source"],
        usage_scope=payload["usage_scope"],
        production_performance_disclaimer=payload["production_performance_disclaimer"],
    )


def _golden_projection(payload: Mapping[str, Any]) -> AgentGoldenEvaluation:
    validate_golden_summary(payload)
    return AgentGoldenEvaluation(
        dataset_epoch=payload["dataset_epoch"],
        status=payload["status"],
        phases=payload["phases"],
    )


def _load_one(path_value: str | None, projector: Any) -> tuple[Any, str | None]:
    payload, reason = _read_json(path_value)
    if payload is None:
        return None, reason
    try:
        return projector(payload), None
    except (
        FaultEvaluationContractError,
        GoldenSummaryContractError,
        ValidationError,
        KeyError,
        TypeError,
        ValueError,
    ):
        logger.warning("agent evaluation artifact omitted (code=CONTRACT_VIOLATION)")
        return None, "CONTRACT_VIOLATION"


def load_agent_evaluations(
    *,
    fault_path: object = _UNSET,
    golden_path: object = _UNSET,
) -> AgentEvaluationResponse:
    resolved_fault = (
        config.AGENT_FAULT_EVAL_ARTIFACT_PATH if fault_path is _UNSET else fault_path
    )
    resolved_golden = (
        config.AGENT_GOLDEN_FLOW_SUMMARY_PATH if golden_path is _UNSET else golden_path
    )
    fault, fault_reason = _load_one(resolved_fault, _fault_projection)
    golden, golden_reason = _load_one(resolved_golden, _golden_projection)
    return AgentEvaluationResponse(
        fault_5class=fault,
        golden_flow=golden,
        fault_5class_empty_reason=fault_reason,
        golden_flow_empty_reason=golden_reason,
    )


__all__ = ["load_agent_evaluations"]
