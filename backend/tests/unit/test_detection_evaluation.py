"""V5-A-2.4 `app/detection/evaluation.py` 단위 테스트.

전부 fake 함수로 주입한다 — 이 모듈은 순수 계산만 하므로(모듈 docstring),
DB·model artifact 등 실제 의존성 없이 로직만 검증한다. DB에 실제로 연결하는
부분(`scripts/evaluate_detection_holdout.py`)은 이 파일의 책임이 아니다 —
다른 실행 스크립트들과 마찬가지로 container/integration 마커 테스트의
범위다(`tests/unit/test_common_db_evaluation.py`의 같은 원칙 참고).
"""

from __future__ import annotations

import json

from app.detection import evaluation


def _pred(lot_hist_id: str, *, lot_id: str = "LOT-1", score: float, is_anomaly: bool):
    return evaluation.PredictionRecord(
        lot_hist_id=lot_hist_id, lot_id=lot_id, score=score, is_anomaly=is_anomaly
    )


# ---------------------------------------------------------------------
# freeze_predictions
# ---------------------------------------------------------------------
def test_freeze_predictions_sorts_by_lot_hist_id() -> None:
    records = [
        _pred("H2", score=0.2, is_anomaly=False),
        _pred("H1", score=0.9, is_anomaly=True),
    ]

    frozen = evaluation.freeze_predictions("if-v1", records)

    assert [r.lot_hist_id for r in frozen.records] == ["H1", "H2"]


def test_freeze_predictions_hash_is_order_independent() -> None:
    a = [
        _pred("H1", score=0.9, is_anomaly=True),
        _pred("H2", score=0.2, is_anomaly=False),
    ]
    b = list(reversed(a))

    hash_a = evaluation.freeze_predictions("if-v1", a).prediction_hash
    hash_b = evaluation.freeze_predictions("if-v1", b).prediction_hash

    assert hash_a == hash_b


def test_freeze_predictions_hash_is_deterministic_across_calls() -> None:
    records = [_pred("H1", score=0.9, is_anomaly=True)]

    first = evaluation.freeze_predictions("if-v1", records).prediction_hash
    second = evaluation.freeze_predictions("if-v1", records).prediction_hash

    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_freeze_predictions_hash_changes_when_score_changes() -> None:
    base = [_pred("H1", score=0.9, is_anomaly=True)]
    changed = [_pred("H1", score=0.91, is_anomaly=True)]

    base_hash = evaluation.freeze_predictions("if-v1", base).prediction_hash
    changed_hash = evaluation.freeze_predictions("if-v1", changed).prediction_hash

    assert base_hash != changed_hash


def test_freeze_predictions_hash_changes_when_model_version_changes() -> None:
    records = [_pred("H1", score=0.9, is_anomaly=True)]

    v1 = evaluation.freeze_predictions("if-v1", records).prediction_hash
    v2 = evaluation.freeze_predictions("if-v2", records).prediction_hash

    assert v1 != v2


# ---------------------------------------------------------------------
# compute_confusion_metrics
# ---------------------------------------------------------------------
def test_compute_confusion_metrics_classifies_all_four_quadrants() -> None:
    predictions = [
        _pred("TP", score=0.9, is_anomaly=True),
        _pred("FP", score=0.8, is_anomaly=True),
        _pred("TN", score=0.1, is_anomaly=False),
        _pred("FN", score=0.2, is_anomaly=False),
    ]
    metrology = [
        evaluation.MetrologyRow(lot_hist_id="TP", alarm_result="FAIL"),
        evaluation.MetrologyRow(lot_hist_id="FP", alarm_result="PASS"),
        evaluation.MetrologyRow(lot_hist_id="TN", alarm_result="PASS"),
        evaluation.MetrologyRow(lot_hist_id="FN", alarm_result="FAIL"),
    ]

    metric = evaluation.compute_confusion_metrics(predictions, metrology)

    assert metric.true_positive == 1
    assert metric.false_positive == 1
    assert metric.true_negative == 1
    assert metric.false_negative == 1
    assert metric.precision == 0.5
    assert metric.recall == 0.5
    assert metric.metrology_coverage_numerator == 4
    assert metric.metrology_coverage_denominator == 4
    assert metric.metrology_pass_count == 2
    assert metric.metrology_fail_count == 2


def test_compute_confusion_metrics_excludes_wafers_without_metrology() -> None:
    predictions = [
        _pred("H1", score=0.9, is_anomaly=True),
        _pred("H2", score=0.1, is_anomaly=False),
    ]
    metrology = [evaluation.MetrologyRow(lot_hist_id="H1", alarm_result="FAIL")]

    metric = evaluation.compute_confusion_metrics(predictions, metrology)

    assert metric.metrology_coverage_numerator == 1
    assert metric.metrology_coverage_denominator == 2
    assert metric.true_positive == 1
    assert metric.false_negative == 0


def test_compute_confusion_metrics_precision_recall_none_when_denominator_zero() -> (
    None
):
    predictions = [_pred("H1", score=0.1, is_anomaly=False)]
    metrology = [evaluation.MetrologyRow(lot_hist_id="H1", alarm_result="PASS")]

    metric = evaluation.compute_confusion_metrics(predictions, metrology)

    assert metric.true_positive == 0
    assert metric.false_positive == 0
    assert metric.precision is None
    assert metric.false_negative == 0
    assert metric.recall is None


def test_compute_confusion_metrics_empty_metrology_yields_zero_coverage() -> None:
    predictions = [_pred("H1", score=0.9, is_anomaly=True)]

    metric = evaluation.compute_confusion_metrics(predictions, [])

    assert metric.metrology_coverage_numerator == 0
    assert metric.metrology_coverage_denominator == 1
    assert metric.precision is None
    assert metric.recall is None


# ---------------------------------------------------------------------
# compute_fault_label_distribution
# ---------------------------------------------------------------------
def test_compute_fault_label_distribution_counts_raw_labels() -> None:
    predictions = [
        _pred("H1", score=0.9, is_anomaly=True),
        _pred("H2", score=0.1, is_anomaly=False),
        _pred("H3", score=0.1, is_anomaly=False),
    ]
    labels = [
        evaluation.FaultLabelRow(lot_hist_id="H1", lot_id="LOT-1", fault_code="FOC"),
        evaluation.FaultLabelRow(lot_hist_id="H2", lot_id="LOT-1", fault_code="NRM"),
        evaluation.FaultLabelRow(lot_hist_id="H3", lot_id="LOT-1", fault_code="NRM"),
    ]

    distribution = evaluation.compute_fault_label_distribution(predictions, labels)

    assert dict(distribution.counts) == {"FOC": 1, "NRM": 2}
    assert distribution.holdout_wafer_count == 3
    assert distribution.labeled_wafer_count == 3


def test_compute_fault_label_distribution_ignores_labels_outside_holdout() -> None:
    predictions = [_pred("H1", score=0.9, is_anomaly=True)]
    labels = [
        evaluation.FaultLabelRow(lot_hist_id="H1", lot_id="LOT-1", fault_code="FOC"),
        # H2는 이 holdout(predictions)에 없다 — train LOT에 속했거나 채점이
        # 스킵된 wafer일 수 있다. 세면 안 된다.
        evaluation.FaultLabelRow(lot_hist_id="H2", lot_id="LOT-9", fault_code="RFM"),
    ]

    distribution = evaluation.compute_fault_label_distribution(predictions, labels)

    assert dict(distribution.counts) == {"FOC": 1}
    assert distribution.labeled_wafer_count == 1


def test_compute_fault_label_distribution_handles_missing_labels() -> None:
    predictions = [_pred("H1", score=0.9, is_anomaly=True)]

    distribution = evaluation.compute_fault_label_distribution(predictions, [])

    assert distribution.counts == ()
    assert distribution.holdout_wafer_count == 1
    assert distribution.labeled_wafer_count == 0


# ---------------------------------------------------------------------
# run_holdout_evaluation — 순서 강제
# ---------------------------------------------------------------------
def test_run_holdout_evaluation_reads_labels_only_after_freezing_predictions() -> (
    None
):
    """설계서 14.4 "prediction을 고정하기 전 label table을 읽으면 평가
    runner가 실패한다"를 fake I/O 호출 순서로 검증한다.
    """

    call_order: list[str] = []

    def predict_fn():
        call_order.append("predict")
        return [_pred("H1", score=0.9, is_anomaly=True)]

    def fetch_labels_fn(frozen: evaluation.FrozenPredictions):
        call_order.append("labels")
        # frozen이 이미 계산되어 있어야만(= predict 이후에만) 호출될 수
        # 있다 — 이 시점에 hash가 비어 있지 않다는 것 자체가 순서 증거다.
        assert frozen.prediction_hash
        return []

    def fetch_metrology_fn(frozen: evaluation.FrozenPredictions):
        call_order.append("metrology")
        assert frozen.prediction_hash
        return []

    evaluation.run_holdout_evaluation(
        model_version="if-v1",
        predict_fn=predict_fn,
        fetch_labels_fn=fetch_labels_fn,
        fetch_metrology_fn=fetch_metrology_fn,
        now_fn=lambda: "2026-08-28T00:00:00+00:00",
    )

    assert call_order == ["predict", "labels", "metrology"]


def test_run_holdout_evaluation_joins_frozen_predictions_with_fetched_labels() -> None:
    predictions = [
        _pred("H1", score=0.9, is_anomaly=True),
        _pred("H2", score=0.1, is_anomaly=False),
    ]

    artifact = evaluation.run_holdout_evaluation(
        model_version="if-v1",
        predict_fn=lambda: predictions,
        fetch_labels_fn=lambda frozen: [
            evaluation.FaultLabelRow(
                lot_hist_id=r.lot_hist_id, lot_id=r.lot_id, fault_code="NRM"
            )
            for r in frozen.records
        ],
        fetch_metrology_fn=lambda frozen: [
            evaluation.MetrologyRow(
                lot_hist_id=frozen.records[0].lot_hist_id, alarm_result="FAIL"
            )
        ],
        now_fn=lambda: "2026-08-28T00:00:00+00:00",
    )

    assert artifact.model_version == "if-v1"
    assert artifact.metric.metrology_coverage_numerator == 1
    assert artifact.metric.true_positive == 1
    assert artifact.fault_label_distribution.labeled_wafer_count == 2


def test_run_holdout_evaluation_artifact_carries_fixed_synthetic_metadata() -> None:
    artifact = evaluation.run_holdout_evaluation(
        model_version="if-v1",
        predict_fn=lambda: [_pred("H1", score=0.9, is_anomaly=True)],
        fetch_labels_fn=lambda frozen: [],
        fetch_metrology_fn=lambda frozen: [],
        now_fn=lambda: "2026-08-28T00:00:00+00:00",
    )

    assert artifact.label_source == "SYNTHETIC_GENERATOR"
    assert artifact.usage_scope == "EVALUATION_ONLY"
    assert artifact.public_fault_ground_truth_available is True
    assert artifact.production_ground_truth_available is False
    assert "48" in artifact.metrology_coverage_note
    assert "600" in artifact.metrology_coverage_note
    assert "production" in artifact.production_performance_disclaimer.lower()


def test_run_holdout_evaluation_generated_at_comes_from_now_fn() -> None:
    artifact = evaluation.run_holdout_evaluation(
        model_version="if-v1",
        predict_fn=lambda: [_pred("H1", score=0.9, is_anomaly=True)],
        fetch_labels_fn=lambda frozen: [],
        fetch_metrology_fn=lambda frozen: [],
        now_fn=lambda: "FIXED-TIMESTAMP",
    )

    assert artifact.generated_at == "FIXED-TIMESTAMP"


# ---------------------------------------------------------------------
# artifact_to_json_dict
# ---------------------------------------------------------------------
def test_artifact_to_json_dict_is_json_serializable_and_round_trips_metric() -> None:
    artifact = evaluation.run_holdout_evaluation(
        model_version="if-v1",
        predict_fn=lambda: [
            _pred("H1", score=0.9, is_anomaly=True),
            _pred("H2", score=0.1, is_anomaly=False),
        ],
        fetch_labels_fn=lambda frozen: [
            evaluation.FaultLabelRow(
                lot_hist_id="H1", lot_id="LOT-1", fault_code="FOC"
            )
        ],
        fetch_metrology_fn=lambda frozen: [
            evaluation.MetrologyRow(lot_hist_id="H1", alarm_result="FAIL")
        ],
        now_fn=lambda: "2026-08-28T00:00:00+00:00",
    )

    payload = evaluation.artifact_to_json_dict(artifact)
    serialized = json.dumps(payload, ensure_ascii=False)
    reloaded = json.loads(serialized)

    assert reloaded["model_version"] == "if-v1"
    assert reloaded["metric"]["true_positive"] == 1
    assert reloaded["fault_label_distribution"]["counts"] == [
        {"fault_code": "FOC", "count": 1}
    ]
    assert reloaded["production_ground_truth_available"] is False
