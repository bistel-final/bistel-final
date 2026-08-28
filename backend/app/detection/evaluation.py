"""V5-A-2.4 Detection 평가 artifact·holdout — 순수 계산 모듈.

시스템설계서 v2.1 4.5 "공개 합성 라벨 평가"의 순서를 코드 구조로 강제한다.

~~~text
1. feature·model·prompt 입력을 구성한다.       (호출자 — 실행 스크립트)
2. prediction 결과와 hash를 먼저 고정한다.       freeze_predictions()
3. 평가 전용 role로 fault_code·metrology를 읽는다. (호출자가 주입하는 콜백)
4. 고정 prediction과 label을 join한다.           run_holdout_evaluation()
5. synthetic 결과임을 명시한 metric artifact를 생성한다. artifact_to_json_dict()
~~~

## 순서 계약 — "prediction을 고정하기 전 label table을 읽으면 평가 runner가
## 실패한다"(설계서 14.4)

`run_holdout_evaluation()`은 `predict_fn()`을 호출해 얻은 예측을
`freeze_predictions()`로 고정한 뒤에만 label·metrology 조회 콜백을 호출할 수
있다 — 그 두 콜백이 `FrozenPredictions`를 유일한 인자로 받기 때문이다. 아직
존재하지 않는 `frozen` 없이는 두 콜백을 아예 호출할 수 없으므로, "순서를
지키지 않으면 predict 전에는 아무 label도 읽을 수 없다"가 실행이 아니라
함수 시그니처 자체로 강제된다.

## 평가 전용 라벨 조회 모듈과의 경계

이 모듈은 V5-A-2.3의 평가 전용 라벨 조회 모듈(`app/detection/` 아래, DB에서
`fault_code`·`metrology.alarm_result`를 직접 읽는 그 모듈)을 import하지
않는다 — 그 모듈을 import해도 되는 곳은 아직 아무 데도 없다는 계약을
`tests/contract/test_detection_label_isolation.py`의 import allowlist
테스트가 고정하고 있고, 이 파일이 새 참조자가 되면 그 테스트가 깨진다.
대신 이 모듈은 자신만의 로컬 타입(`FaultLabelRow`·`MetrologyRow`)을 정의한다.
DB에 실제로 연결하는 실행 스크립트(`scripts/evaluate_detection_holdout.py`,
이 저장소의 `app` 패키지 밖이라 위 allowlist 대상이 아니다)가 그 loader의
반환행을 이 모듈의 로컬 타입으로 변환해 `fetch_labels_fn`·`fetch_metrology_fn`
콜백으로 주입한다. 타입을 일부러 분리해 둔 이유는 그 평가 전용 loader
모듈의 docstring이 `SyntheticFaultLabelRow`에 대해 설명하는 것과 같다 — 타입이
같으면 실수로 그 결과를 다른 곳에 그대로 넘겨도 타입 검사가 못 잡는다.

## 5-class Fault 분류와의 경계

`fault_code`(FOC|RFM|MFD|TMD|OTH|NRM) 5-class 분류·채점은 Agent(C)의 출력
도메인이다(기준표 "Fault 라벨과 평가 경계" 3절: "알람 incident를 분류하는
Agent 출력 도메인은 FOC|RFM|MFD|TMD|OTH"). 이 모듈(비지도 anomaly score,
연속값 하나만 내놓는 모델)은 5-class 분류를 하지 않는다 —
`compute_fault_label_distribution()`은 holdout 표본의 raw label 분포를
그대로 보고할 뿐, 어떤 정답 매칭·채점에도 쓰지 않는다.

## 같은 revision 재튜닝 금지 — 이 모듈이 하지 않는 것

"같은 revision 재튜닝을 금지한다"(WBS V5-A-2.4)는 이 모듈이 아니라 호출자인
실행 스크립트의 책임이다: 이미 평가된 `model_version`의 결과 artifact가
있으면 스크립트가 재실행 자체를 거부한다. 이 모듈은 순수 계산만 하므로 "몇
번 호출됐는지"를 알 방법도, 알아야 할 이유도 없다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "LABEL_SOURCE",
    "USAGE_SCOPE",
    "PUBLIC_FAULT_GROUND_TRUTH_AVAILABLE",
    "PRODUCTION_GROUND_TRUTH_AVAILABLE",
    "METROLOGY_COVERAGE_NOTE",
    "PRODUCTION_PERFORMANCE_DISCLAIMER",
    "PredictionRecord",
    "FrozenPredictions",
    "FaultLabelRow",
    "MetrologyRow",
    "HoldoutMetric",
    "FaultLabelDistribution",
    "HoldoutArtifact",
    "freeze_predictions",
    "compute_confusion_metrics",
    "compute_fault_label_distribution",
    "run_holdout_evaluation",
    "artifact_to_json_dict",
]

# --- 평가 artifact 고정 메타데이터 (기준표 3절·설계서 4.5·FR-A-08) ----------
LABEL_SOURCE = "SYNTHETIC_GENERATOR"
USAGE_SCOPE = "EVALUATION_ONLY"
PUBLIC_FAULT_GROUND_TRUTH_AVAILABLE = True
PRODUCTION_GROUND_TRUTH_AVAILABLE = False

# metrology는 lot_history 600건 중 48건(PASS 39 / FAIL 9)에만 존재한다
# (기준표 3절, README "metrology 48/600 lot_history 표본"). 이 holdout
# 평가의 confusion metric은 그 48건 가운데 이번에 held-out으로 뽑힌 LOT과
# 겹치는 부분집합에서만 계산된다 — HoldoutMetric의
# metrology_coverage_numerator/denominator가 "이번 실행에서 실제로 몇 건이
# 겹쳤는지"를 담고, 이 상수는 "그 48/600이라는 전체 표본 자체가 이미
# 제한적"이라는 고정 사실을 알려주는 캡션이다. 어느 쪽도 서로를 대신하지
# 않으며, 둘 다 전체 600건 Detection 성능으로 외삽하는 근거로 쓰지 않는다.
METROLOGY_COVERAGE_NOTE = (
    "metrology는 lot_history 600건 중 48건(PASS 39 / FAIL 9)에만 존재한다. "
    "이 metric은 그 48건 가운데 이번 holdout LOT과 겹치는 부분집합에서만 "
    "계산되며(metrology_coverage_numerator/denominator 참고), 겹치지 않는 "
    "나머지 wafer·전체 600건 Detection 성능으로 외삽하지 않는다."
)

PRODUCTION_PERFORMANCE_DISCLAIMER = (
    "이 metric은 Generator가 주입한 공개 합성 라벨/metrology 기반이며 "
    "실제 생산 공정(production) 성능을 나타내지 않는다"
    "(production_ground_truth_available=false)."
)

_METROLOGY_FAIL_RESULT = "FAIL"


# ---------------------------------------------------------------------
# 1) prediction 고정
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PredictionRecord:
    """holdout wafer 한 장의 고정 예측 — 평가 전용 라벨을 아직 보지 않은
    시점에 이미 결정된 값이다. `score`·`is_anomaly`는 운영 Tool과 완전히
    같은 채점 경로(`FdcSummaryService`)가 만든 값을 그대로 옮겨 담는다 —
    이 모듈 자신은 채점을 다시 하지 않는다(호출자가 `predict_fn`으로 넘긴다).
    """

    lot_hist_id: str
    lot_id: str
    score: float
    is_anomaly: bool


@dataclass(frozen=True, slots=True)
class FrozenPredictions:
    """`freeze_predictions()`의 결과. `prediction_hash` 하나만 비교해도 "이번
    실행이 정말 같은 예측 집합을 썼는지"를 재현·감사할 수 있다.
    """

    model_version: str
    records: tuple[PredictionRecord, ...]
    prediction_hash: str


def freeze_predictions(
    model_version: str, records: Sequence[PredictionRecord]
) -> FrozenPredictions:
    """예측 목록을 `lot_hist_id` 오름차순으로 정렬해 고정하고 SHA-256 해시를
    계산한다.

    해시는 `rules.py`의 R03 alarm_id 해시(`_compute_r03_alarm_id`)와 같은
    canonical-JSON 패턴을 쓴다 — key 오름차순·UTF-8·공백 없는 JSON으로
    직렬화한 뒤 SHA-256을 취한다. 재실행해도 같은 입력이면 항상 같은 해시가
    나와야 "prediction을 고정했다"는 주장 자체를 검증할 수 있다. 정렬은
    `lot_hist_id`만 기준으로 한다 — 호출자가 넘긴 순서(예: DB 조회 순서)가
    해시에 영향을 주면, 순서만 바뀌어도 "다른 prediction을 고정했다"는
    잘못된 신호를 준다.
    """

    ordered = tuple(sorted(records, key=lambda r: r.lot_hist_id))
    payload = {
        "model_version": model_version,
        "records": [
            {
                "is_anomaly": r.is_anomaly,
                "lot_hist_id": r.lot_hist_id,
                "lot_id": r.lot_id,
                "score": r.score,
            }
            for r in ordered
        ],
    }
    serialized = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    return FrozenPredictions(
        model_version=model_version,
        records=ordered,
        prediction_hash=f"sha256:{digest}",
    )


# ---------------------------------------------------------------------
# 2) 평가 전용 라벨 — 이 모듈만의 로컬 타입 (모듈 docstring "경계" 참고)
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FaultLabelRow:
    """holdout wafer 한 장의 공개 합성 Fault 라벨. 평가 전용이며 5-class
    분류·채점에는 쓰지 않는다(모듈 docstring 참고) — 정보 제공용 분포
    집계(`compute_fault_label_distribution`)에만 쓴다.
    """

    lot_hist_id: str
    lot_id: str
    fault_code: str


@dataclass(frozen=True, slots=True)
class MetrologyRow:
    """holdout wafer 한 장의 metrology PASS/FAIL 결과. 48/600 표본에만
    존재하므로 모든 예측이 이 타입의 대응 행을 갖는 것은 아니다.
    """

    lot_hist_id: str
    alarm_result: str


# ---------------------------------------------------------------------
# 3) confusion metric — metrology를 참조 정답으로 쓴다
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class HoldoutMetric:
    """metrology PASS/FAIL을 참조 정답으로 한 confusion metric.

    "positive"는 `is_anomaly=True`(모델이 이상으로 판정) ×
    metrology `FAIL`(실제로 품질 이상이 확인됨)의 교집합을 뜻한다. metrology가
    없는 wafer(48/600 표본 밖)는 이 metric 계산에서 제외한다 —
    `metrology_coverage_numerator`/`_denominator`가 그 제외 비율을 그대로
    보여준다(설계서 4.5 "전체 데이터로 외삽하지 않는다").
    """

    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float | None
    recall: float | None
    metrology_coverage_numerator: int
    metrology_coverage_denominator: int
    metrology_pass_count: int
    metrology_fail_count: int


def compute_confusion_metrics(
    predictions: Sequence[PredictionRecord], metrology: Sequence[MetrologyRow]
) -> HoldoutMetric:
    """holdout `predictions`와 `metrology`를 `lot_hist_id`로 join해 confusion
    metric을 계산한다.

    `metrology`에 없는 `lot_hist_id`는 분모에서 제외한다(추정하지 않는다).
    `precision`·`recall`은 분모가 0이면(예: 이번 holdout이 우연히 metrology
    표본과 전혀 겹치지 않으면) `None`을 반환한다 — 0으로 나누는 대신 "계산할
    수 없다"는 사실 자체를 그대로 드러낸다.
    """

    metrology_by_id = {row.lot_hist_id: row for row in metrology}

    true_positive = false_positive = true_negative = false_negative = 0
    pass_count = fail_count = 0
    for prediction in predictions:
        metrology_row = metrology_by_id.get(prediction.lot_hist_id)
        if metrology_row is None:
            continue
        is_fail = metrology_row.alarm_result == _METROLOGY_FAIL_RESULT
        if is_fail:
            fail_count += 1
        else:
            pass_count += 1

        if prediction.is_anomaly and is_fail:
            true_positive += 1
        elif prediction.is_anomaly and not is_fail:
            false_positive += 1
        elif not prediction.is_anomaly and is_fail:
            false_negative += 1
        else:
            true_negative += 1

    coverage_numerator = true_positive + false_positive + true_negative + false_negative
    precision = (
        true_positive / (true_positive + false_positive)
        if (true_positive + false_positive) > 0
        else None
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if (true_positive + false_negative) > 0
        else None
    )

    return HoldoutMetric(
        true_positive=true_positive,
        false_positive=false_positive,
        true_negative=true_negative,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        metrology_coverage_numerator=coverage_numerator,
        metrology_coverage_denominator=len(predictions),
        metrology_pass_count=pass_count,
        metrology_fail_count=fail_count,
    )


# ---------------------------------------------------------------------
# 4) 합성 라벨 분포 — 정보 제공용, 채점에 쓰지 않는다
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FaultLabelDistribution:
    """holdout 표본의 raw 합성 라벨 분포 — 정보 제공용 메타데이터일 뿐,
    어떤 metric 계산의 입력으로도 쓰이지 않는다(모듈 docstring "5-class Fault
    분류와의 경계" 참고).
    """

    counts: tuple[tuple[str, int], ...]  # (fault_code, count), fault_code 오름차순
    holdout_wafer_count: int
    labeled_wafer_count: int


def compute_fault_label_distribution(
    predictions: Sequence[PredictionRecord], labels: Sequence[FaultLabelRow]
) -> FaultLabelDistribution:
    """holdout `predictions`에 대응하는 `labels`의 raw `fault_code` 분포를
    센다. `predictions`에 없는 `lot_hist_id`의 라벨은(있을 수 없지만 방어적으로)
    세지 않는다 — holdout 표본 밖의 라벨까지 포함하면 "이번 holdout의 라벨
    분포"라는 의미가 깨진다.
    """

    holdout_ids = {p.lot_hist_id for p in predictions}
    counts: dict[str, int] = {}
    labeled = 0
    for label in labels:
        if label.lot_hist_id not in holdout_ids:
            continue
        labeled += 1
        counts[label.fault_code] = counts.get(label.fault_code, 0) + 1

    return FaultLabelDistribution(
        counts=tuple(sorted(counts.items())),
        holdout_wafer_count=len(predictions),
        labeled_wafer_count=labeled,
    )


# ---------------------------------------------------------------------
# 5) artifact 조립 — 순서 강제의 실제 진입점
# ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class HoldoutArtifact:
    """공개 합성 라벨 evaluation artifact 한 벌. `artifact_to_json_dict()`가
    이 값을 파일로 저장할 평평한 dict로 변환한다.
    """

    model_version: str
    prediction_hash: str
    generated_at: str
    metric: HoldoutMetric
    fault_label_distribution: FaultLabelDistribution
    label_source: str = LABEL_SOURCE
    usage_scope: str = USAGE_SCOPE
    public_fault_ground_truth_available: bool = PUBLIC_FAULT_GROUND_TRUTH_AVAILABLE
    production_ground_truth_available: bool = PRODUCTION_GROUND_TRUTH_AVAILABLE
    metrology_coverage_note: str = METROLOGY_COVERAGE_NOTE
    production_performance_disclaimer: str = PRODUCTION_PERFORMANCE_DISCLAIMER


def run_holdout_evaluation(
    *,
    model_version: str,
    predict_fn: Callable[[], Sequence[PredictionRecord]],
    fetch_labels_fn: Callable[[FrozenPredictions], Sequence[FaultLabelRow]],
    fetch_metrology_fn: Callable[[FrozenPredictions], Sequence[MetrologyRow]],
    now_fn: Callable[[], str],
) -> HoldoutArtifact:
    """설계서 4.5 1~4단계를 이 순서 그대로 실행한다.

    `fetch_labels_fn`·`fetch_metrology_fn`은 둘 다 `frozen`(이 함수 안에서
    `predict_fn()` 다음에 계산된 값)을 유일한 인자로 받는다 — 그래서 이
    함수 본문을 어떻게 고쳐도, `freeze_predictions()`가 끝나기 전에는 두
    콜백을 호출할 방법이 없다(모듈 docstring "순서 계약" 참고). 테스트는
    fake 콜백을 주입해 "실제로 이 순서로 호출됐는지"를 호출 기록으로
    검증한다(`tests/unit/test_detection_evaluation.py`).
    """

    predictions = predict_fn()
    frozen = freeze_predictions(model_version, predictions)

    labels = fetch_labels_fn(frozen)
    metrology = fetch_metrology_fn(frozen)

    metric = compute_confusion_metrics(frozen.records, metrology)
    distribution = compute_fault_label_distribution(frozen.records, labels)

    return HoldoutArtifact(
        model_version=frozen.model_version,
        prediction_hash=frozen.prediction_hash,
        generated_at=now_fn(),
        metric=metric,
        fault_label_distribution=distribution,
    )


def artifact_to_json_dict(artifact: HoldoutArtifact) -> dict:
    """`HoldoutArtifact`를 `json.dumps`에 바로 넣을 수 있는 평평한 dict로
    변환한다(학습 스크립트의 manifest JSON 변환 함수와 같은 목적).
    """

    return {
        "model_version": artifact.model_version,
        "prediction_hash": artifact.prediction_hash,
        "generated_at": artifact.generated_at,
        "label_source": artifact.label_source,
        "usage_scope": artifact.usage_scope,
        "public_fault_ground_truth_available": (
            artifact.public_fault_ground_truth_available
        ),
        "production_ground_truth_available": (
            artifact.production_ground_truth_available
        ),
        "metrology_coverage_note": artifact.metrology_coverage_note,
        "production_performance_disclaimer": (
            artifact.production_performance_disclaimer
        ),
        "metric": {
            "true_positive": artifact.metric.true_positive,
            "false_positive": artifact.metric.false_positive,
            "true_negative": artifact.metric.true_negative,
            "false_negative": artifact.metric.false_negative,
            "precision": artifact.metric.precision,
            "recall": artifact.metric.recall,
            "metrology_coverage_numerator": (
                artifact.metric.metrology_coverage_numerator
            ),
            "metrology_coverage_denominator": (
                artifact.metric.metrology_coverage_denominator
            ),
            "metrology_pass_count": artifact.metric.metrology_pass_count,
            "metrology_fail_count": artifact.metric.metrology_fail_count,
        },
        "fault_label_distribution": {
            "counts": [
                {"fault_code": code, "count": count}
                for code, count in artifact.fault_label_distribution.counts
            ],
            "holdout_wafer_count": (
                artifact.fault_label_distribution.holdout_wafer_count
            ),
            "labeled_wafer_count": (
                artifact.fault_label_distribution.labeled_wafer_count
            ),
        },
    }
