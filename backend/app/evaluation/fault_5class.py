"""V5-C-6.2 합성 Fault 5-class 격리 평가의 순수 계산 core.

이 모듈은 DB·파일·환경변수에 접근하지 않고 evaluation label loader도 import하지
않는다. 호출자는 Runtime prediction을 먼저 :func:`freeze_predictions`로 고정한 뒤
평가 전용 role에서 읽은 label row를 :func:`evaluate_fault_5class`에 전달해야 한다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

FAULT_CLASSES: Final = ("FOC", "RFM", "MFD", "TMD", "OTH")
FAULT_CLASS_SET: Final = frozenset(FAULT_CLASSES)
RAW_LABELS: Final = frozenset({*FAULT_CLASSES, "NRM"})
EXPECTED_POPULATION_COUNT: Final = 12
EXPECTED_CLASSIFICATION_COUNT: Final = 7
EXPECTED_NO_INJECTED_COUNT: Final = 5
EXPECTED_AMBIGUOUS_COUNT: Final = 0
EXPECTED_CLASS_SUPPORT: Final[Mapping[str, int]] = {
    "FOC": 2,
    "RFM": 1,
    "MFD": 1,
    "TMD": 1,
    "OTH": 2,
}

PUBLIC_FAULT_GROUND_TRUTH_AVAILABLE: Final = True
PRODUCTION_GROUND_TRUTH_AVAILABLE: Final = False
LABEL_SOURCE: Final = "SYNTHETIC_GENERATOR"
USAGE_SCOPE: Final = "EVALUATION_ONLY"
CLASSIFICATION_POPULATION: Final = "NON_NRM_SINGLE_LABEL_INCIDENTS"
DATASET_EPOCH: Final = "fdc_final_20260818"
SOURCE_ZIP_SHA256: Final = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)
PRODUCTION_PERFORMANCE_DISCLAIMER: Final = (
    "이 결과는 Generator 공개 합성 라벨 benchmark이며 실제 생산 공정 성능을 "
    "나타내지 않는다. 분류 모집단은 7건이고 클래스별 support는 1~2건이므로 "
    "개별 클래스 지표를 성능 추정치로 해석하지 않는다."
)


class FaultEvaluationContractError(ValueError):
    """입력 모집단·라벨·artifact가 고정 계약과 다르다."""


class LabelDisposition(StrEnum):
    EVAL_TARGET = "EVAL_TARGET"
    NO_INJECTED_FAULT = "NO_INJECTED_FAULT"
    AMBIGUOUS_LABEL = "AMBIGUOUS_LABEL"


@dataclass(frozen=True, order=True, slots=True)
class IncidentKey:
    lot_id: str
    chamber_id: str

    def __post_init__(self) -> None:
        if not self.lot_id.strip() or not self.chamber_id.strip():
            raise FaultEvaluationContractError("INCIDENT_KEY_INVALID")

    def as_dict(self) -> dict[str, str]:
        return {"lot_id": self.lot_id, "chamber_id": self.chamber_id}


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    incident: IncidentKey
    agent_run_id: str
    predicted_fault_code: str | None
    supporting_alarm_tokens: tuple[str, ...] = ()
    supporting_chunk_ids: tuple[str, ...] = ()
    supporting_relation_ids: tuple[str, ...] = ()
    available_alarm_tokens: tuple[str, ...] = ()
    available_chunk_ids: tuple[str, ...] = ()
    available_relation_ids: tuple[str, ...] = ()
    actual_action: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_run_id.strip():
            raise FaultEvaluationContractError("RUN_ID_INVALID")
        if (
            self.predicted_fault_code is not None
            and self.predicted_fault_code not in FAULT_CLASS_SET
        ):
            raise FaultEvaluationContractError("PREDICTION_CLASS_INVALID")
        for values in (
            self.supporting_alarm_tokens,
            self.supporting_chunk_ids,
            self.supporting_relation_ids,
            self.available_alarm_tokens,
            self.available_chunk_ids,
            self.available_relation_ids,
        ):
            if any(not value.strip() for value in values) or len(values) != len(
                set(values)
            ):
                raise FaultEvaluationContractError("CITATION_SET_INVALID")


@dataclass(frozen=True, slots=True)
class FrozenPredictions:
    records: tuple[PredictionRecord, ...]
    prediction_hash: str


@dataclass(frozen=True, slots=True)
class IncidentFaultLabelRow:
    incident: IncidentKey
    fault_code: str

    def __post_init__(self) -> None:
        if self.fault_code not in RAW_LABELS:
            raise FaultEvaluationContractError("FAULT_LABEL_INVALID")


@dataclass(frozen=True, slots=True)
class IncidentLabel:
    incident: IncidentKey
    disposition: LabelDisposition
    fault_code: str | None


@dataclass(frozen=True, slots=True)
class CountMetric:
    numerator: int
    denominator: int

    @property
    def rate(self) -> float:
        return 0.0 if self.denominator == 0 else self.numerator / self.denominator

    def as_dict(self) -> dict[str, int | float]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rate": self.rate,
        }


@dataclass(frozen=True, slots=True)
class ClassMetric:
    support: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "support": self.support,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


@dataclass(frozen=True, slots=True)
class ClassificationMetric:
    accuracy: CountMetric
    unclassified_count: int
    by_class: Mapping[str, ClassMetric]
    macro_f1_5class: float
    observed_class_macro_f1: float


@dataclass(frozen=True, slots=True)
class VersionProvenance:
    model_version: str | None
    prompt_version: str | None
    policy_version: str | None


@dataclass(frozen=True, slots=True)
class FaultEvaluationResult:
    labels: tuple[IncidentLabel, ...]
    structured_prediction: CountMetric
    evidence_valid_run: CountMetric
    rule_action_agreement: CountMetric
    classification: ClassificationMetric
    versions: VersionProvenance
    hard_gate_reasons: tuple[str, ...]

    @property
    def hard_gate_passed(self) -> bool:
        return not self.hard_gate_reasons


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    golden_evidence_sha256: str
    baseline_snapshot_artifact_sha256: str
    oracle_sha256: str
    population_sha256: str
    prediction_hash: str
    runtime_provenance_sha256: str
    evaluation_provenance_sha256: str
    shared_key_sha256: str
    code_revision: str


def _canonical_sha256(payload: object) -> str:
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def freeze_predictions(records: Sequence[PredictionRecord]) -> FrozenPredictions:
    """Runtime prediction·citation을 label 접근 전에 canonical hash로 고정한다."""

    ordered = tuple(
        sorted(
            records,
            key=lambda item: (
                item.incident.lot_id,
                item.incident.chamber_id,
                item.agent_run_id,
            ),
        )
    )
    if len(ordered) != EXPECTED_POPULATION_COUNT:
        raise FaultEvaluationContractError("PREDICTION_POPULATION_NOT_EXACT")
    if len({item.incident for item in ordered}) != EXPECTED_POPULATION_COUNT:
        raise FaultEvaluationContractError("PREDICTION_INCIDENT_DUPLICATE")
    if len({item.agent_run_id for item in ordered}) != EXPECTED_POPULATION_COUNT:
        raise FaultEvaluationContractError("PREDICTION_RUN_DUPLICATE")
    payload = [
        {
            "agent_run_id": item.agent_run_id,
            "chamber_id": item.incident.chamber_id,
            "lot_id": item.incident.lot_id,
            "predicted_fault_code": item.predicted_fault_code,
            "supporting_alarm_tokens": sorted(item.supporting_alarm_tokens),
            "supporting_chunk_ids": sorted(item.supporting_chunk_ids),
            "supporting_relation_ids": sorted(item.supporting_relation_ids),
        }
        for item in ordered
    ]
    return FrozenPredictions(ordered, _canonical_sha256(payload))


def classify_incident_labels(
    incident_keys: Sequence[IncidentKey],
    rows: Sequence[IncidentFaultLabelRow],
) -> tuple[IncidentLabel, ...]:
    """incident 전체 member의 distinct non-NRM 집합으로 평가 대상을 정한다."""

    keys = tuple(sorted(incident_keys))
    if len(keys) != EXPECTED_POPULATION_COUNT or len(set(keys)) != len(keys):
        raise FaultEvaluationContractError("LABEL_POPULATION_NOT_EXACT")
    allowed = set(keys)
    grouped: dict[IncidentKey, set[str]] = defaultdict(set)
    seen: set[IncidentKey] = set()
    for row in rows:
        if row.incident not in allowed:
            raise FaultEvaluationContractError("LABEL_OUTSIDE_POPULATION")
        seen.add(row.incident)
        if row.fault_code != "NRM":
            grouped[row.incident].add(row.fault_code)
    if seen != allowed:
        raise FaultEvaluationContractError("LABEL_MEMBER_MISSING")

    labels: list[IncidentLabel] = []
    for key in keys:
        injected = grouped[key]
        if not injected:
            labels.append(IncidentLabel(key, LabelDisposition.NO_INJECTED_FAULT, None))
        elif len(injected) == 1:
            labels.append(
                IncidentLabel(key, LabelDisposition.EVAL_TARGET, next(iter(injected)))
            )
        else:
            labels.append(IncidentLabel(key, LabelDisposition.AMBIGUOUS_LABEL, None))

    dispositions = Counter(item.disposition for item in labels)
    expected_dispositions = Counter(
        {
            LabelDisposition.EVAL_TARGET: EXPECTED_CLASSIFICATION_COUNT,
            LabelDisposition.NO_INJECTED_FAULT: EXPECTED_NO_INJECTED_COUNT,
            LabelDisposition.AMBIGUOUS_LABEL: EXPECTED_AMBIGUOUS_COUNT,
        }
    )
    # Counter는 0 value key를 보존하지 않으므로 값 비교로 고정한다.
    if any(
        dispositions[disposition] != count
        for disposition, count in expected_dispositions.items()
    ):
        raise FaultEvaluationContractError("LABEL_DISTRIBUTION_NOT_EXACT")
    support = Counter(
        item.fault_code
        for item in labels
        if item.disposition is LabelDisposition.EVAL_TARGET
    )
    if dict(support) != dict(EXPECTED_CLASS_SUPPORT):
        raise FaultEvaluationContractError("LABEL_SUPPORT_NOT_EXACT")
    return tuple(labels)


def _evidence_is_valid(record: PredictionRecord) -> bool:
    cited_alarms = set(record.supporting_alarm_tokens)
    cited_chunks = set(record.supporting_chunk_ids)
    cited_relations = set(record.supporting_relation_ids)
    # Runtime Hypothesis 계약도 alarm citation을 최소 하나 요구한다.
    return (
        record.predicted_fault_code is not None
        and bool(cited_alarms)
        and cited_alarms <= set(record.available_alarm_tokens)
        and cited_chunks <= set(record.available_chunk_ids)
        and cited_relations <= set(record.available_relation_ids)
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _classification_metrics(
    records_by_key: Mapping[IncidentKey, PredictionRecord],
    labels: Sequence[IncidentLabel],
) -> ClassificationMetric:
    targets = [
        item for item in labels if item.disposition is LabelDisposition.EVAL_TARGET
    ]
    correct = 0
    unclassified = 0
    by_class: dict[str, ClassMetric] = {}
    for fault_class in FAULT_CLASSES:
        tp = fp = fn = support = 0
        for label in targets:
            predicted = records_by_key[label.incident].predicted_fault_code
            actual = label.fault_code
            if actual == fault_class:
                support += 1
                if predicted == fault_class:
                    tp += 1
                else:
                    fn += 1
            elif predicted == fault_class:
                fp += 1
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        f1 = _safe_ratio(2 * precision * recall, precision + recall)
        by_class[fault_class] = ClassMetric(support, tp, fp, fn, precision, recall, f1)
    for label in targets:
        predicted = records_by_key[label.incident].predicted_fault_code
        if predicted is None:
            unclassified += 1
        if predicted == label.fault_code:
            correct += 1
    fixed_macro = sum(item.f1 for item in by_class.values()) / len(FAULT_CLASSES)
    observed = [item.f1 for item in by_class.values() if item.support > 0]
    return ClassificationMetric(
        accuracy=CountMetric(correct, len(targets)),
        unclassified_count=unclassified,
        by_class=by_class,
        macro_f1_5class=fixed_macro,
        observed_class_macro_f1=_safe_ratio(sum(observed), len(observed)),
    )


def _single_version(records: Sequence[PredictionRecord], field: str) -> str | None:
    values = {getattr(item, field) for item in records}
    if len(values) != 1:
        return None
    value = next(iter(values))
    return value if isinstance(value, str) and value.strip() else None


def evaluate_fault_5class(
    frozen: FrozenPredictions,
    label_rows: Sequence[IncidentFaultLabelRow],
    expected_actions: Mapping[IncidentKey, str],
) -> FaultEvaluationResult:
    """고정 prediction과 나중에 읽은 label을 join해 12건/7건 지표를 계산한다."""

    records = frozen.records
    keys = {item.incident for item in records}
    if set(expected_actions) != keys:
        raise FaultEvaluationContractError("ORACLE_POPULATION_NOT_EXACT")
    labels = classify_incident_labels(tuple(keys), label_rows)
    by_key = {item.incident: item for item in records}

    structured = CountMetric(
        sum(item.predicted_fault_code is not None for item in records), len(records)
    )
    evidence = CountMetric(
        sum(_evidence_is_valid(item) for item in records), len(records)
    )
    agreement = CountMetric(
        sum(item.actual_action == expected_actions[item.incident] for item in records),
        len(records),
    )
    versions = VersionProvenance(
        model_version=_single_version(records, "model_version"),
        prompt_version=_single_version(records, "prompt_version"),
        policy_version=_single_version(records, "policy_version"),
    )
    reasons: list[str] = []
    if structured.numerator != EXPECTED_POPULATION_COUNT:
        reasons.append("STRUCTURED_PREDICTION_NOT_100_PERCENT")
    if evidence.numerator != EXPECTED_POPULATION_COUNT:
        reasons.append("EVIDENCE_ID_NOT_100_PERCENT")
    if agreement.numerator != EXPECTED_POPULATION_COUNT:
        reasons.append("RULE_ACTION_NOT_100_PERCENT")
    if versions.model_version is None:
        reasons.append("MODEL_VERSION_NOT_SINGLE")
    if versions.prompt_version is None:
        reasons.append("PROMPT_VERSION_NOT_SINGLE")
    if versions.policy_version is None:
        reasons.append("POLICY_VERSION_NOT_SINGLE")

    return FaultEvaluationResult(
        labels=labels,
        structured_prediction=structured,
        evidence_valid_run=evidence,
        rule_action_agreement=agreement,
        classification=_classification_metrics(by_key, labels),
        versions=versions,
        hard_gate_reasons=tuple(reasons),
    )


def artifact_to_dict(
    result: FaultEvaluationResult,
    provenance: ArtifactProvenance,
) -> dict[str, Any]:
    """요구사항 §9의 exact metadata를 포함한 JSON-safe artifact를 만든다."""

    excluded = [
        item.incident.as_dict()
        for item in result.labels
        if item.disposition is LabelDisposition.NO_INJECTED_FAULT
    ]
    ambiguous = [
        item.incident.as_dict()
        for item in result.labels
        if item.disposition is LabelDisposition.AMBIGUOUS_LABEL
    ]
    artifact: dict[str, Any] = {
        "format_version": 1,
        "artifact_type": "fault_5class_evaluation",
        "public_fault_ground_truth_available": PUBLIC_FAULT_GROUND_TRUTH_AVAILABLE,
        "production_ground_truth_available": PRODUCTION_GROUND_TRUTH_AVAILABLE,
        "label_source": LABEL_SOURCE,
        "usage_scope": USAGE_SCOPE,
        "classification_population": CLASSIFICATION_POPULATION,
        "classification_population_count": EXPECTED_CLASSIFICATION_COUNT,
        "excluded_no_injected_fault_incident_count": EXPECTED_NO_INJECTED_COUNT,
        "excluded_no_injected_fault_incidents": excluded,
        "ambiguous_label_incident_count": len(ambiguous),
        "ambiguous_label_incidents": ambiguous,
        "metrology_observed_count": 48,
        "metrology_total_lot_hist_count": 600,
        "dataset_epoch": DATASET_EPOCH,
        "source_zip_sha256": SOURCE_ZIP_SHA256,
        "model_version": result.versions.model_version,
        "prompt_version": result.versions.prompt_version,
        "policy_version": result.versions.policy_version,
        "golden_evidence_sha256": provenance.golden_evidence_sha256,
        "baseline_snapshot_artifact_sha256": (
            provenance.baseline_snapshot_artifact_sha256
        ),
        "oracle_sha256": provenance.oracle_sha256,
        "population_sha256": provenance.population_sha256,
        "prediction_hash": provenance.prediction_hash,
        "runtime_provenance_sha256": provenance.runtime_provenance_sha256,
        "evaluation_provenance_sha256": provenance.evaluation_provenance_sha256,
        "shared_key_sha256": provenance.shared_key_sha256,
        "code_revision": provenance.code_revision,
        "structured_prediction": result.structured_prediction.as_dict(),
        "evidence_valid_run": result.evidence_valid_run.as_dict(),
        "rule_action_agreement": result.rule_action_agreement.as_dict(),
        "classification": {
            "accuracy": result.classification.accuracy.as_dict(),
            "unclassified_count": result.classification.unclassified_count,
            "macro_f1_5class": result.classification.macro_f1_5class,
            "observed_class_macro_f1": result.classification.observed_class_macro_f1,
            "by_class": {
                fault_class: result.classification.by_class[fault_class].as_dict()
                for fault_class in FAULT_CLASSES
            },
        },
        "hard_gate_passed": result.hard_gate_passed,
        "hard_gate_reasons": list(result.hard_gate_reasons),
        "production_performance_disclaimer": PRODUCTION_PERFORMANCE_DISCLAIMER,
    }
    validate_artifact(artifact)
    return artifact


_ARTIFACT_KEYS: Final = {
    "format_version",
    "artifact_type",
    "public_fault_ground_truth_available",
    "production_ground_truth_available",
    "label_source",
    "usage_scope",
    "classification_population",
    "classification_population_count",
    "excluded_no_injected_fault_incident_count",
    "excluded_no_injected_fault_incidents",
    "ambiguous_label_incident_count",
    "ambiguous_label_incidents",
    "metrology_observed_count",
    "metrology_total_lot_hist_count",
    "dataset_epoch",
    "source_zip_sha256",
    "model_version",
    "prompt_version",
    "policy_version",
    "golden_evidence_sha256",
    "baseline_snapshot_artifact_sha256",
    "oracle_sha256",
    "population_sha256",
    "prediction_hash",
    "runtime_provenance_sha256",
    "evaluation_provenance_sha256",
    "shared_key_sha256",
    "code_revision",
    "structured_prediction",
    "evidence_valid_run",
    "rule_action_agreement",
    "classification",
    "hard_gate_passed",
    "hard_gate_reasons",
    "production_performance_disclaimer",
}


def validate_artifact(artifact: Mapping[str, Any]) -> None:
    """누락·추가 key와 고정 metadata drift를 fail-closed한다."""

    if set(artifact) != _ARTIFACT_KEYS:
        raise FaultEvaluationContractError("ARTIFACT_KEYS_INVALID")
    fixed = {
        "format_version": 1,
        "artifact_type": "fault_5class_evaluation",
        "public_fault_ground_truth_available": True,
        "production_ground_truth_available": False,
        "label_source": LABEL_SOURCE,
        "usage_scope": USAGE_SCOPE,
        "classification_population": CLASSIFICATION_POPULATION,
        "classification_population_count": 7,
        "excluded_no_injected_fault_incident_count": 5,
        "ambiguous_label_incident_count": 0,
        "metrology_observed_count": 48,
        "metrology_total_lot_hist_count": 600,
        "dataset_epoch": DATASET_EPOCH,
        "source_zip_sha256": SOURCE_ZIP_SHA256,
        "production_performance_disclaimer": PRODUCTION_PERFORMANCE_DISCLAIMER,
    }
    if any(artifact[key] != value for key, value in fixed.items()):
        raise FaultEvaluationContractError("ARTIFACT_METADATA_INVALID")
    hash_keys = (
        "golden_evidence_sha256",
        "baseline_snapshot_artifact_sha256",
        "oracle_sha256",
        "population_sha256",
        "prediction_hash",
        "runtime_provenance_sha256",
        "evaluation_provenance_sha256",
        "shared_key_sha256",
    )
    if any(
        not isinstance(artifact[key], str)
        or len(artifact[key]) != 64
        or any(char not in "0123456789abcdef" for char in artifact[key])
        for key in hash_keys
    ):
        raise FaultEvaluationContractError("ARTIFACT_HASH_INVALID")
    if len(artifact["excluded_no_injected_fault_incidents"]) != 5:
        raise FaultEvaluationContractError("ARTIFACT_EXCLUSION_INVALID")
    _validate_incident_list(
        artifact["excluded_no_injected_fault_incidents"], expected_count=5
    )
    _validate_incident_list(artifact["ambiguous_label_incidents"], expected_count=0)

    versions = ("model_version", "prompt_version", "policy_version")
    if any(
        artifact[key] is not None
        and (not isinstance(artifact[key], str) or not artifact[key].strip())
        for key in versions
    ):
        raise FaultEvaluationContractError("ARTIFACT_VERSION_INVALID")
    revision = artifact["code_revision"]
    if (
        not isinstance(revision, str)
        or len(revision) != 40
        or any(char not in "0123456789abcdef" for char in revision)
    ):
        raise FaultEvaluationContractError("ARTIFACT_REVISION_INVALID")

    structured = _validate_count_metric(
        artifact["structured_prediction"], denominator=EXPECTED_POPULATION_COUNT
    )
    evidence = _validate_count_metric(
        artifact["evidence_valid_run"], denominator=EXPECTED_POPULATION_COUNT
    )
    agreement = _validate_count_metric(
        artifact["rule_action_agreement"], denominator=EXPECTED_POPULATION_COUNT
    )
    classification = artifact["classification"]
    if not isinstance(classification, Mapping) or set(classification) != {
        "accuracy",
        "unclassified_count",
        "macro_f1_5class",
        "observed_class_macro_f1",
        "by_class",
    }:
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    accuracy_numerator = _validate_count_metric(
        classification["accuracy"], denominator=7
    )
    unclassified = classification["unclassified_count"]
    if not _nonnegative_int(unclassified) or unclassified > 7:
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    by_class = classification["by_class"]
    # Evidence receipts use canonical ``sort_keys=True`` serialization, so mapping
    # insertion order is not durable. The exact five-key set remains mandatory.
    if not isinstance(by_class, Mapping) or set(by_class) != FAULT_CLASS_SET:
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    f1_values: list[float] = []
    for fault_class in FAULT_CLASSES:
        f1_values.append(
            _validate_class_metric(
                by_class[fault_class],
                support=EXPECTED_CLASS_SUPPORT[fault_class],
            )
        )
    if sum(by_class[item]["true_positive"] for item in FAULT_CLASSES) != (
        accuracy_numerator
    ):
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    expected_macro = sum(f1_values) / len(FAULT_CLASSES)
    for key in ("macro_f1_5class", "observed_class_macro_f1"):
        value = classification[key]
        if not _rate(value) or value != expected_macro:
            raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")

    reasons = artifact["hard_gate_reasons"]
    allowed_reasons = {
        "STRUCTURED_PREDICTION_NOT_100_PERCENT",
        "EVIDENCE_ID_NOT_100_PERCENT",
        "RULE_ACTION_NOT_100_PERCENT",
        "MODEL_VERSION_NOT_SINGLE",
        "PROMPT_VERSION_NOT_SINGLE",
        "POLICY_VERSION_NOT_SINGLE",
    }
    if (
        not isinstance(reasons, list)
        or len(reasons) != len(set(reasons))
        or any(reason not in allowed_reasons for reason in reasons)
    ):
        raise FaultEvaluationContractError("ARTIFACT_GATE_INVALID")
    expected_reasons: list[str] = []
    if structured != EXPECTED_POPULATION_COUNT:
        expected_reasons.append("STRUCTURED_PREDICTION_NOT_100_PERCENT")
    if evidence != EXPECTED_POPULATION_COUNT:
        expected_reasons.append("EVIDENCE_ID_NOT_100_PERCENT")
    if agreement != EXPECTED_POPULATION_COUNT:
        expected_reasons.append("RULE_ACTION_NOT_100_PERCENT")
    for key, reason in zip(
        versions,
        (
            "MODEL_VERSION_NOT_SINGLE",
            "PROMPT_VERSION_NOT_SINGLE",
            "POLICY_VERSION_NOT_SINGLE",
        ),
        strict=True,
    ):
        if artifact[key] is None:
            expected_reasons.append(reason)
    if reasons != expected_reasons or artifact["hard_gate_passed"] is not (
        not expected_reasons
    ):
        raise FaultEvaluationContractError("ARTIFACT_GATE_INVALID")


def _nonnegative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _rate(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _validate_incident_list(value: object, *, expected_count: int) -> None:
    if not isinstance(value, list) or len(value) != expected_count:
        raise FaultEvaluationContractError("ARTIFACT_EXCLUSION_INVALID")
    keys: list[tuple[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"lot_id", "chamber_id"}:
            raise FaultEvaluationContractError("ARTIFACT_EXCLUSION_INVALID")
        lot_id = raw["lot_id"]
        chamber_id = raw["chamber_id"]
        if (
            not isinstance(lot_id, str)
            or not lot_id.strip()
            or not isinstance(chamber_id, str)
            or not chamber_id.strip()
        ):
            raise FaultEvaluationContractError("ARTIFACT_EXCLUSION_INVALID")
        keys.append((lot_id, chamber_id))
    if keys != sorted(set(keys)):
        raise FaultEvaluationContractError("ARTIFACT_EXCLUSION_INVALID")


def _validate_count_metric(value: object, *, denominator: int) -> int:
    if not isinstance(value, Mapping) or set(value) != {
        "numerator",
        "denominator",
        "rate",
    }:
        raise FaultEvaluationContractError("ARTIFACT_COUNT_INVALID")
    numerator = value["numerator"]
    if (
        not _nonnegative_int(numerator)
        or numerator > denominator
        or value["denominator"] != denominator
        or not _rate(value["rate"])
        or value["rate"] != numerator / denominator
    ):
        raise FaultEvaluationContractError("ARTIFACT_COUNT_INVALID")
    return numerator


def _validate_class_metric(value: object, *, support: int) -> float:
    keys = {
        "support",
        "true_positive",
        "false_positive",
        "false_negative",
        "precision",
        "recall",
        "f1",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    tp = value["true_positive"]
    fp = value["false_positive"]
    fn = value["false_negative"]
    if (
        value["support"] != support
        or any(not _nonnegative_int(item) for item in (tp, fp, fn))
        or tp + fn != support
        or any(not _rate(value[key]) for key in ("precision", "recall", "f1"))
    ):
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * precision * recall, precision + recall)
    if (
        value["precision"] != precision
        or value["recall"] != recall
        or value["f1"] != f1
    ):
        raise FaultEvaluationContractError("ARTIFACT_CLASSIFICATION_INVALID")
    return value["f1"]


__all__ = [
    "ArtifactProvenance",
    "CLASSIFICATION_POPULATION",
    "DATASET_EPOCH",
    "EXPECTED_CLASS_SUPPORT",
    "FAULT_CLASSES",
    "FaultEvaluationContractError",
    "FaultEvaluationResult",
    "FrozenPredictions",
    "IncidentFaultLabelRow",
    "IncidentKey",
    "IncidentLabel",
    "LabelDisposition",
    "PredictionRecord",
    "artifact_to_dict",
    "classify_incident_labels",
    "evaluate_fault_5class",
    "freeze_predictions",
    "validate_artifact",
]
