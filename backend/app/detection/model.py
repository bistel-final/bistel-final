"""비지도 anomaly score 모델 (V5-A-2.1).

시스템설계서 v2.1 4.4 anomaly score, 요구사항정의서 v2.1 FR-A-04·NFR-08·NFR-19 근거.
기준 원천: 멘토 최종 project.zip(2026-08-18) epoch `fdc_final_20260818`.

이 모듈은 Rules/Model 계층이다(summarize.py와 같은 위치). DB 조회·쓰기, 파일 IO는
하지 않는다. joblib 저장·DB 조회는 `scripts/train_anomaly_score_model.py`와
`repository.py`가 담당하고, 이 모듈은 순수 계산 함수만 제공한다("순수"란 같은
입력을 넣으면 언제·몇 번을 실행하든 항상 같은 출력이 나오고, DB나 파일 같은
외부 상태를 전혀 건드리지 않는다는 뜻이다 — summarize.py와 똑같은 설계 원칙이다).

[팀원용 요약 — ML을 잘 몰라도 이 문단만 읽으면 전체 그림이 잡힌다]
우리 목표는 "이 wafer가 평소와 얼마나 다르게 생겼는지"를 0~1 사이 숫자 하나로
표현하는 것이다. 이 숫자가 anomaly_score다. 규칙 기반 알람(OOS/OOC)은 "한계선을
넘었는지"만 보지만, anomaly_score는 여러 parameter의 통계치를 한꺼번에 보고
"이 조합 자체가 흔치 않다"는 걸 잡아내려는 보조 신호다. 그래서 이 파일은
"라벨(정답)이 없어도 이상치를 찾는" 비지도학습(unsupervised learning) 방식을
쓴다 — 실제로 이 wafer가 불량인지 정상인지 알려주는 정답표가 없기 때문이다
(NFR-19: fault_code는 전부 NRM placeholder라 정답으로 못 쓴다).

전체 흐름을 요리에 비유하면:
  1) 재료 손질(feature 조립) — summary_data·evaluation·dim_parameter에서 숫자를
     뽑아 "한계선에서 얼마나 벗어났나", "관리 이탈 비율이 얼마나 되나" 같은
     비교 가능한 형태로 바꾼다(build_group_feature).
  2) 접시에 담기(pivot) — 여러 parameter·step에 흩어진 숫자를 wafer 1장 = 벡터
     1개가 되도록 한 줄로 모은다(aggregate_wafer_features).
  3) 시식단 나누기(train/test split) — 모델이 "본 적 없는 데이터에서도 잘
     동작하는지" 확인하려고 일부 LOT은 학습에서 아예 빼둔다(split_lots).
  4) 기준 잡기(정규화) — parameter마다 값의 스케일이 다르므로(예: 온도는
     100~300, 압력은 0.1~0.9), 서로 비교 가능하도록 "평균 0, 표준편차 1"
     기준으로 맞춘다(fit_normalizer/apply_normalizer).
  5) 이상치 탐지기 학습(IsolationForest) — "적은 질문(랜덤한 기준값 비교)으로
     빨리 격리되는 표본일수록 이상치"라는 아이디어의 모델을 학습한다.
  6) 점수로 변환 — 모델이 뱉는 원점수는 부호와 스케일이 우리 목적과 안 맞아서
     "높을수록 이상"·"0~1 범위"가 되도록 다듬는다.
  7) 표시용 임계값 산정 — "이 정도 점수부터는 화면에 강조 표시하자"는 기준선을
     학습 데이터의 점수 분포에서 뽑아둔다(예: 상위 5%).
  8) DTO 변환 — 최종 점수를 이미 있는 AnomalySignal 규격에 담아 API/Tool이
     그대로 쓸 수 있게 한다.

[함수 실행 순서 — 코드에서 실제로 이 순서대로 호출된다]
  1) split_lots                 lot_id 목록을 train/test로 분리 (반드시 제일 먼저)
  2) compute_expected_point_counts  train LOT에 속한 행만 넘겨 (parameter, step)별
                                 관측 point_cnt 최댓값(coverage 분모) 산정
  3) build_group_feature      summary_data·evaluation·dim_parameter 한 그룹 -> feature
                               (train+test 전체 행에 적용, 분모는 2)의 결과를 그대로 씀)
  4) feature_schema            train LOT의 그룹만 넘겨 고정 feature 이름 생성
  5) aggregate_wafer_features   3)의 그룹 feature(전체) -> lot_hist_id 단위 고정 폭
                                 벡터로 pivot (열 이름은 4)의 결과를 그대로 씀)
  6) fit_normalizer/apply_normalizer   train 표본에서만 평균·표준편차 산정
  7) train_isolation_forest     IsolationForest 학습
  8) raw_anomaly_scores         점수 방향 통일(높을수록 이상)
  9) fit_score_scaling/scale_scores   train 분포로 [0,1] 스케일 고정
  10) compute_display_threshold  train 분포 quantile로 표시 임계값 산정
  11) to_anomaly_signal         app.common.tool_contracts.AnomalySignal로 변환

1)이 제일 먼저인 이유(코드 리뷰로 드러난 실수): 2)와 4)도 "train에서만 기준을
잡는다"는 이 모듈의 원칙을 따라야 하는 함수들인데, split_lots보다 먼저
불리면 test LOT의 값이 그 기준(coverage 분모·feature 열 구성)에 섞여
들어간다. fit_normalizer·fit_score_scaling이 이미 지키던 원칙을 2)·4)에도
똑같이 적용한 것뿐이다 — 예외가 아니라 원래 규칙이 뒤늦게 넓게 적용된
것이다. 실제로 이 순서를 엮어서 실행하는 코드는 이 파일이 아니라
`scripts/train_anomaly_score_model.py`에 있다(이 파일은 "부품"만 제공하고
"조립 순서"는 그 스크립트가 정한다).

[왜 "LOT 단위"로 나누는지 — 데이터 누수(data leakage)를 처음 접하는 사람을 위한 설명]
같은 LOT(작업 묶음) 안의 wafer들은 같은 레시피·같은 시점에 같은 설비를 지나가서
서로 값이 비슷하다(형제 관계라고 생각하면 된다). 만약 LOT을 쪼개서 wafer 1은
train에, wafer 2는 test에 넣으면, 모델은 test의 wafer 2를 채점할 때 "어! 이거
train에서 본 형제(wafer 1)랑 거의 똑같네" 하고 쉽게 맞혀버린다. 이러면 "모델이
처음 보는 데이터에서도 잘 동작하는지" 확인하는 게 아니라 "이미 본 것과 비슷한
걸 알아보는지"만 확인하는 셈이 되어 성능이 과장된다. 그래서 반드시 LOT
전체(그 안의 모든 wafer)를 통째로 train 아니면 test 한쪽에만 넣는다.

[왜 "정규화"가 필요한지]
parameter마다 값의 단위·범위가 다르다(예: ET_REFL은 수백 단위, 어떤 parameter는
0~1 단위). 이 상태로 그냥 모델에 넣으면 숫자가 큰 parameter가 "더 중요한 것처럼"
모델을 지배해버린다. 그래서 각 feature를 "train 데이터 기준 평균 0, 표준편차 1"이
되도록 맞춰서(z-score), 어떤 parameter든 동등한 스케일로 비교되게 만든다. 반드시
train 데이터의 평균·표준편차만 쓰고 test에도 그 값을 그대로 적용한다 — test
데이터를 보고 기준을 다시 잡으면 이것도 일종의 데이터 누수다("시험 문제를 보고
채점 기준을 정하는" 것과 같다).

[NFR-19 라벨 격리 — 이 모듈에 절대 들이지 않는 입력]
  - lot_history.fault_code       (전 행이 NRM placeholder, 공개 정답 라벨 아님)
  - metrology.alarm_result / PASS·FAIL  (제품 계측 결과, Fault 정답과 다른 것)
  - Generator injection 상수, action_history
feature는 summary_data·evaluation·dim_parameter·lot_history(lot_id·lot_hist_id만)로만
만든다. `_assert_feature_allowlist`가 feature 이름에 금지 토큰이 섞이면 즉시
실패하고(1차 방어선), `tests/unit/test_detection_model.py::
test_repository_fetch_functions_never_touch_labels`가 repository.py의 조회 SQL
텍스트를 정적으로 검사해 이를 다시 한 번 고정한다(2차 방어선 — 소스 코드 자체를
읽어서 금지 테이블 이름이 등장하는지 본다).

[V5-A-2.2 예고] score는 action_decision·incident·승인 게이트에 전달되지 않는다.
이 모듈은 그 경계를 스스로 강제하지 않는다 — ActionPolicy는 아직 이 저장소에
구현되지 않았고(V5-A-2.2/V5-C 영역), 구현되는 즉시 호출부(service.py/tools.py)가
score를 그 입력에 절대 넣지 않는다는 계약 테스트를 V5-A-2.2에서 추가해야 한다.

[결정 근거 — 팀 재검토 시 여기부터 본다]
  - MODEL_VERSION="if-v1": 이 파일의 feature·hyperparameter 조합 첫 버전. 아래
    상수·feature 정의를 바꾸면 이전 결과와 구분하기 위해 반드시 값을 올린다.
  - RANDOM_SEED=20260818: epoch 날짜(`fdc_final_20260818`)를 그대로 썼다. split·
    IsolationForest 양쪽에 이 값 하나만 흘려써서 "같은 seed = 같은 seed"를 보장한다.
    (seed는 "무작위처럼 보이지만 사실은 정해진 순서로 나오는 난수"를 만들 때 쓰는
    시작값이다 — 같은 seed를 쓰면 "무작위" 선택도 매번 똑같이 재현된다.)
  - center·spec_range를 dim_parameter의 spec_upper·spec_lower만으로 계산한다 —
    이 표에는 target 컬럼이 없다(schemas.py의 ParameterLimits도 동일). target이
    별도로 확보되면 _center를 target 우선으로 바꾸고 MODEL_VERSION을 올린다.
  - expected_point_cnt(=coverage 분모)를 상수로 고정하지 않고
    `compute_expected_point_counts`로 데이터에서 직접 구한다 — parameter·step마다
    seq_no 개수가 실제로 몇 개인지 이 모듈만으로는(DB 접근 없이는) 단정할 근거가
    없기 때문이다. 이후 팀이 고정 seq 개수를 확정하면 이 함수를 상수 조회로
    바꿔도 되지만, 지금은 관측 최댓값이 가장 방어적인 근사다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

from app.detection.summarize import GroupKey, ParameterLimit

__all__ = [
    "WaferGroupFeature",
    "WaferFeatureVector",
    "Normalizer",
    "ScoreScaling",
    "ModelManifest",
    "compute_expected_point_counts",
    "compute_spec_range",
    "build_group_feature",
    "feature_schema",
    "aggregate_wafer_features",
    "split_lots",
    "fit_normalizer",
    "apply_normalizer",
    "train_isolation_forest",
    "raw_anomaly_scores",
    "fit_score_scaling",
    "scale_scores",
    "compute_display_threshold",
    "to_anomaly_signal",
]

# ---------------------------------------------------------------------
# 고정 상수 — 값을 바꾸면 MODEL_VERSION도 함께 올린다 (재현성 계약).
# 근거는 모듈 docstring의 "결정 근거" 절을 본다.
#
# 왜 "상수를 코드에 직접 박아두는지" 궁금할 수 있다 — 설정 파일(.env 등)로
# 빼지 않는 이유는, 이 값들이 "운영 환경마다 달라져야 하는 설정"이 아니라
# "이 모델 버전을 정의하는 값" 그 자체이기 때문이다. 값이 바뀌면 그건 설정
# 변경이 아니라 새 모델(MODEL_VERSION이 달라짐)이 나온 것이다.
# ---------------------------------------------------------------------
MODEL_VERSION = "if-v1"
SCORE_METHOD = "isolation_forest_path_length"
RANDOM_SEED = 20260818
TRAIN_LOT_RATIO = 0.8  # LOT의 80%는 학습에, 20%는 "본 적 없는 데이터" 검증용으로 뺀다
N_ESTIMATORS = 200  # IsolationForest 안에 만들 무작위 이진 트리(격리 나무) 개수
CONTAMINATION: str | float = "auto"  # sklearn이 내부 기준선을 자동으로 잡게 둔다
DISPLAY_THRESHOLD_QUANTILE = 0.95  # train score 분포 상위 5%(95th percentile) 지점

# feature 이름에 아래 토큰이 섞이면 라벨 누수로 간주하고 즉시 실패한다.
# (예: "metrology_pass_rate" 같은 feature 이름을 실수로 만들면 이 토큰이 잡아낸다.
# 실제 컬럼 존재 여부가 아니라 "이름 자체"를 검사하는 방어선이라, feature 이름을
# 지을 때 이 토큰들을 절대 포함시키면 안 된다는 규칙이기도 하다.)
_FORBIDDEN_FEATURE_TOKENS = (
    "fault",
    "metrology",
    "alarm_result",
    "inject",
    "generator",
    "pass_fail",
    "action_history",
)


@dataclass(frozen=True, slots=True)
class WaferGroupFeature:
    """(lot_hist_id, parameter_id, recipe_step_no) 그룹 하나의 파생 feature.

    summary_data·evaluation·dim_parameter 세 원천에서만 계산한다. metrology·
    fault_code·action_history 컬럼은 이 dataclass에 절대 추가하지 않는다.

    "그룹 하나"가 뭘 뜻하는지 예를 들면: wafer 1장이 ET_REFL parameter를
    step 1에서 3번 측정했다면, 그 3번의 측정값 통계(평균·표준편차 등)를 요약한
    행 하나가 여기서 말하는 "그룹 feature" 하나다. wafer 1장은 보통 parameter
    여러 개 x step 여러 개만큼 이런 행을 여러 개 갖는다(아래 aggregate_wafer_
    features가 그것들을 wafer 1장 = 벡터 1개로 다시 모은다).
    """

    key: GroupKey
    lot_id: str
    # (value_mean - center) / spec_range — "이 그룹의 평균값이 규격 중심에서
    # 얼마나(규격 폭 대비 몇 %만큼) 벗어났는지"를 나타내는 상대 거리다.
    # 0에 가까우면 규격 중심 근처, 절대값이 크면 중심에서 많이 벗어난 것이다.
    # 예외: upper_only=True인 parameter(예: ET_REFL)는 _center()가 spec_lower
    # 없이 spec_upper 자체를 중심으로 쓰므로, 이 해석이 반대로 뒤집힌다 — 0에
    # 가까우면 "상한에 붙어있다"는 뜻이고, 음수 방향으로 클수록 상한에서 멀리
    # (즉 더 정상적인 방향으로) 떨어져 있다는 뜻이다. 모델 입력값 자체는 정규화
    # 단계에서 이 오프셋이 상쇄되어 학습·채점 결과에 영향이 없지만, 사람이 이
    # 숫자를 직접 읽거나 화면에 노출할 때는 upper_only parameter에 한해 방향이
    # 뒤집혀 있다는 걸 감안해야 한다.
    relative_mean_distance: float
    # value_std / spec_range — 이 그룹 안에서 값이 얼마나 들쭉날쭉했는지(변동성)를
    # 역시 규격 폭 대비 비율로 나타낸다. 값이 크면 같은 조건에서도 측정값이 안정적이지
    # 않았다는 뜻이라 이상 신호가 될 수 있다.
    relative_std: float
    # ooc_point_cnt / point_cnt — 관리한계(UCL/LCL)를 벗어난 point의 비율.
    # 이건 "규칙 판정 결과"를 요약한 값일 뿐 fault_code 같은 정답 라벨이 아니다.
    ooc_ratio: float
    oos_ratio: float  # oos_point_cnt / point_cnt — 규격한계(USL/LSL)를 벗어난 비율
    # point_cnt / expected_point_cnt — 이 그룹에서 "원래 측정됐어야 할 개수" 대비
    # "실제로 측정된 개수" 비율. 1.0이면 완전히 다 측정됨, 그보다 작으면 측정
    # 누락(coverage 부족)이 있었다는 뜻이라 이 자체도 이상 신호가 될 수 있다.
    coverage: float


@dataclass(frozen=True, slots=True)
class WaferFeatureVector:
    """WAFER(lot_hist_id) 단위로 pivot된 고정 폭 feature 벡터.

    `WaferGroupFeature`가 "parameter x step 하나짜리 조각"이라면, 이 클래스는
    한 wafer가 가진 조각들을 전부 이어붙인 "완성된 한 줄"이다. IsolationForest는
    표(행렬) 형태의 입력만 받을 수 있어서, 결국 학습·채점에 실제로 들어가는
    입력은 이 `values`를 세로로 쌓은 행렬이다.
    """

    lot_hist_id: str
    lot_id: str
    values: tuple[
        float, ...
    ]  # feature_names와 같은 순서 — 순서가 어긋나면 값이 뒤섞인다


@dataclass(frozen=True, slots=True)
class Normalizer:
    """train 표본에서만 산정한 평균·표준편차. test·운영 채점에는 그대로 적용한다
    (재계산 금지 — train/test 분리 완료 기준).

    "정규화(z-score)"란 각 feature 값에서 평균을 빼고 표준편차로 나눠서, 원래
    단위·스케일이 무엇이었든 "평균 0, 표준편차 1"짜리 숫자로 바꾸는 것이다.
    이 dataclass는 그 변환에 쓸 평균·표준편차 "기준값"만 담아둔다 — 실제 변환은
    `apply_normalizer`가 한다.
    """

    mean: tuple[float, ...]
    std: tuple[float, ...]  # 0이었던 항목은 1로 치환해 나눗셈 안전성을 보장한다


@dataclass(frozen=True, slots=True)
class ScoreScaling:
    """train raw score 분포에서 고정한 min-max 경계. 이후 채점은 이 값을 그대로 쓴다.

    IsolationForest가 내놓는 원점수는 범위가 정해져 있지 않다(음수도 나올 수
    있다). 그래서 "train에서 나온 점수들 중 최솟값·최댓값"을 기준으로 나중에
    모든 점수를 0~1 사이로 눌러 담는다(min-max scaling). 이 값도 정규화와
    마찬가지로 train에서만 정하고 test·운영 채점에는 그대로 재사용한다.
    """

    raw_min: float
    raw_max: float


@dataclass(frozen=True, slots=True)
class ModelManifest:
    """재현성에 필요한 모든 설정값. joblib 모델과 함께 JSON으로 저장한다
    (embedding_model_manifest.json과 같은 목적).

    "manifest"는 한마디로 "이 모델이 어떻게 만들어졌는지 적어둔 영수증"이다.
    나중에 누군가 "이 점수가 왜 이렇게 나왔지?"라고 물었을 때, 이 파일 하나만
    보면 feature 구성·seed·정규화 기준·threshold까지 전부 다시 확인할 수 있어야
    한다. 그래야 "재현 가능한 score"라는 완료 기준을 실제로 지킬 수 있다.

    코드 리뷰에서 지적된 두 가지를 이 버전에서 채웠다:
      - `expected_point_counts`가 빠져 있으면, 나중에 새 wafer를 채점할 때
        coverage 분모를 다시 DB에서 구해야 하고, 그 사이 데이터가 늘면 같은
        wafer가 다른 점수를 받을 수 있다(재현성 위반). 그래서 학습 시점에 쓴
        분모 표 자체를 영수증에 그대로 박아둔다.
      - `sklearn_version`·`numpy_version`이 빠져 있으면, "같은 seed·같은
        데이터인데 라이브러리 버전이 달라서 점수가 달라진" 경우를 이 파일
        하나로는 구분할 수 없다. `generated_at`과 같은 성격(생성 시점의
        환경 정보)이라, 그 옆에 나란히 둔다.
    """

    model_version: str
    score_method: str
    random_seed: int
    feature_names: tuple[str, ...]
    normalizer: Normalizer
    scaling: ScoreScaling
    display_threshold: float
    n_estimators: int
    contamination: str
    train_lot_count: int
    test_lot_count: int
    train_wafer_count: int
    # coverage 분모(=만점) 표. (parameter_id, recipe_step_no, expected_point_cnt)
    # 튜플들을 정렬해서 담는다 — dict는 dataclass(frozen=True)라도 내부 값이
    # 바뀔 수 있는 mutable 타입이라 여기 필드로 쓰지 않는다(다른 필드들도 전부
    # tuple을 쓰는 것과 같은 이유). 새 wafer를 채점할 때 DB를 다시 훑지 않고
    # 이 표를 그대로 재사용해야 학습 때와 같은 coverage가 나온다.
    expected_point_counts: tuple[tuple[str, int, int], ...]
    sklearn_version: str
    numpy_version: str


# ---------------------------------------------------------------------
# 1) feature 조립
# ---------------------------------------------------------------------
def compute_expected_point_counts(
    records: Sequence[tuple[GroupKey, int]],
) -> dict[tuple[str, int], int]:
    """(parameter_id, recipe_step_no) 조합별로 관측된 point_cnt의 최댓값을
    "이 그룹의 만점"으로 삼는다 — coverage feature의 분모다.

    예를 들어 (ET_REFL, step=1) 조합의 point_cnt가 여러 wafer에 걸쳐
    [3, 3, 2, 3]으로 나왔다면, 이 조합의 "만점"은 3이라고 본다(대부분 3점을
    채웠으니 3점이 정상이고, 2점만 나온 그 wafer는 측정이 하나 빠진 것으로
    해석한다). seq_no 개수가 parameter·step마다 실제로 몇 개인지는 데이터를
    봐야 알 수 있으므로 상수로 고정하지 않는다. `records`는 summary_data를
    읽어 만든 `(GroupKey, point_cnt)` 목록을 그대로 넘기면 된다.
    """
    maxima: dict[tuple[str, int], int] = {}
    for key, point_cnt in records:
        # (parameter_id, recipe_step_no)만 뽑아서 key로 쓴다. lot_hist_id는
        # "만점이 몇 점인지" 계산에서는 상관없다 — 같은 parameter·step이면
        # 어느 wafer든 같은 만점을 공유해야 하기 때문이다.
        combo = (key.parameter_id, key.recipe_step_no)
        if point_cnt > maxima.get(combo, 0):
            maxima[combo] = point_cnt
    return maxima


def build_group_feature(
    key: GroupKey,
    lot_id: str,
    value_mean: float,
    value_std: float | None,
    point_cnt: int,
    ooc_point_cnt: int,
    oos_point_cnt: int,
    limit: ParameterLimit,
    expected_point_cnt: int,
) -> WaferGroupFeature:
    """summary_data 한 행 + evaluation 한 행 + dim_parameter 한계선 -> feature 하나.

    `expected_point_cnt`는 `compute_expected_point_counts`가 `(parameter_id,
    recipe_step_no)`별로 미리 구해 둔 값을 그대로 받는다. center·spec_range
    산정 근거는 모듈 docstring을 본다.

    분모가 0이 될 수 있는 경우(spec_range가 0이거나 point_cnt·expected_point_cnt가
    0인 경우)는 나눗셈 오류(ZeroDivisionError) 대신 0.0을 반환하도록 각 줄마다
    `if ... else 0.0`으로 방어했다 — 데이터가 이상해도 학습 파이프라인 전체가
    죽지 않게 하기 위해서다(다만 이렇게 0.0으로 넘어간 행이 많다면 그 자체가
    데이터 이상 신호이니 학습 스크립트의 `skipped` 카운트를 같이 봐야 한다).
    """
    spec_range = compute_spec_range(limit)
    center = _center(limit)

    # 아래 5줄이 이 함수의 핵심이다 — "원본 통계값"을 "비교 가능한 비율"로
    # 바꾸는 부분이다. 값 자체(예: 250.3)는 parameter마다 의미가 다르지만,
    # "규격 폭 대비 몇 %"로 바꾸면 어떤 parameter든 같은 잣대로 비교할 수 있다.
    relative_mean_distance = (value_mean - center) / spec_range if spec_range else 0.0
    relative_std = (value_std or 0.0) / spec_range if spec_range else 0.0
    ooc_ratio = ooc_point_cnt / point_cnt if point_cnt else 0.0
    oos_ratio = oos_point_cnt / point_cnt if point_cnt else 0.0
    coverage = point_cnt / expected_point_cnt if expected_point_cnt else 0.0

    return WaferGroupFeature(
        key=key,
        lot_id=lot_id,
        relative_mean_distance=relative_mean_distance,
        relative_std=relative_std,
        ooc_ratio=ooc_ratio,
        oos_ratio=oos_ratio,
        coverage=coverage,
    )


def compute_spec_range(limit: ParameterLimit) -> float:
    """규격 상한·하한 사이의 폭(USL - LSL)을 구한다 — "이 parameter가 허용하는
    전체 범위"라고 보면 된다. `upper_only=True`인 parameter(예: ET_REFL)는
    하한이 아예 없으므로 상한값 자체를 기준 폭으로 대신 쓴다.

    (공개 함수다 — `build_group_feature` 내부 계산뿐 아니라, 학습 스크립트가
    "규격 폭이 0이라 이 그룹이 아무 신호도 못 주는" 경우를 별도로 집계할 때도
    이 함수를 그대로 재사용한다. 함수 안에서 이 값을 담는 지역 변수 이름은
    똑같이 `spec_range`이지만 이름이 겹쳐도 상관없다 — 지역 변수 할당은 그
    줄의 오른쪽(`compute_spec_range(limit)`)이 먼저 평가된 뒤에 이뤄지므로,
    만약 이름이 완전히 같은 `spec_range`라는 함수였다면 오히려
    `UnboundLocalError`가 났을 것이다. 그래서 함수 이름을 `compute_spec_range`로
    지어 지역 변수 `spec_range`와 겹치지 않게 피했다.)
    """
    if limit.upper_only:
        return abs(limit.spec_upper) if limit.spec_upper else 0.0
    if limit.spec_upper is None or limit.spec_lower is None:
        return 0.0
    return abs(limit.spec_upper - limit.spec_lower)


def _center(limit: ParameterLimit) -> float:
    """ "정상적으로 기대되는 중심값"을 구한다. dim_parameter에는 target(목표값)
    컬럼이 없으므로, 규격 상·하한의 정중앙((USL+LSL)/2)을 대신 쓴다.
    `upper_only=True`이거나 한쪽 한계가 없으면 상한값 자체를 중심으로 본다.
    """
    if limit.upper_only or limit.spec_lower is None or limit.spec_upper is None:
        return limit.spec_upper or 0.0
    return (limit.spec_upper + limit.spec_lower) / 2


def feature_schema(group_features: Sequence[WaferGroupFeature]) -> tuple[str, ...]:
    """관측된 (parameter_id, recipe_step_no) 조합을 정렬해 고정 feature 이름을 만든다.

    왜 "정렬"이 중요한가: 학습할 때 feature 순서가 [P1_mean, P2_mean, ...]였는데
    나중에 채점할 때 [P2_mean, P1_mean, ...] 순서로 바뀌면, 모델은 완전히 다른
    의미로 숫자를 해석해버린다(같은 숫자인데 "다른 parameter의 값"으로 잘못
    읽는 셈). 그래서 매번 정렬된 순서로 이름을 만들어 이 순서 자체를 manifest.
    feature_names에 고정해 저장한다 — 학습 때와 채점 때가 항상 같은 순서를
    쓰도록 강제하는 장치다. 새 parameter·step이 데이터에 추가되면 이 이름
    목록 자체가 바뀌므로, 그 경우 반드시 재학습(MODEL_VERSION도 올려서)해야
    한다 — 예전 모델에 새 순서의 벡터를 넣으면 안 된다.
    """
    # {(parameter_id, recipe_step_no), ...} — set으로 중복 조합을 제거한 뒤
    # sorted()로 항상 같은 순서가 나오게 만든다(set 자체는 순회 순서를
    # 보장하지 않는 파이썬 컬렉션이라 정렬이 꼭 필요하다).
    combos = sorted(
        {(gf.key.parameter_id, gf.key.recipe_step_no) for gf in group_features}
    )
    # 각 (parameter, step) 조합마다 5가지 통계를 feature로 쓴다.
    stats = ("mean_dist", "std", "ooc_ratio", "oos_ratio", "coverage")
    # 예: "ET_REFL__step1__mean_dist", "ET_REFL__step1__std", ...
    names = tuple(
        f"{param}__step{step}__{stat}" for param, step in combos for stat in stats
    )
    _assert_feature_allowlist(names)
    return names


def _assert_feature_allowlist(feature_names: Sequence[str]) -> None:
    """feature 이름 목록에 금지 토큰(_FORBIDDEN_FEATURE_TOKENS)이 섞여 있으면
    즉시 `ValueError`를 던진다 — "일단 만들고 나중에 걸러내기"가 아니라 "만드는
    시점에 바로 막기"다. parameter_id 자체가 실수로 "metrology_xxx"처럼 지어지는
    경우까지 잡아내는 마지막 방어선이라고 보면 된다.
    """
    lowered = [name.lower() for name in feature_names]
    for name in lowered:
        if any(token in name for token in _FORBIDDEN_FEATURE_TOKENS):
            raise ValueError(f"금지된 feature 발견 — 라벨 누수 의심: {name}")


def aggregate_wafer_features(
    group_features: Sequence[WaferGroupFeature],
    feature_names: Sequence[str],
) -> list[WaferFeatureVector]:
    """그룹 feature들을 lot_hist_id 단위로 모아 고정 폭 벡터로 pivot한다.

    "pivot"이란 표를 세로로 긴 형태(그룹 하나 = 행 하나)에서 가로로 넓은
    형태(wafer 하나 = 행 하나, parameter·step 조합마다 열 하나)로 바꾸는
    작업이다. 예를 들어 원래 데이터가
        (H1, P1, step1) -> mean_dist=0.1
        (H1, P2, step1) -> mean_dist=0.3
        (H2, P1, step1) -> mean_dist=0.2
    이런 식으로 흩어져 있었다면, pivot 후에는
        H1 -> [P1_mean_dist=0.1, P2_mean_dist=0.3, ...]
        H2 -> [P1_mean_dist=0.2, P2_mean_dist=0.0(결측), ...]
    처럼 wafer 하나가 한 줄짜리 벡터가 된다.

    관측되지 않은 (parameter, step) 조합은 0.0으로 채운다 — 결측을 train 평균으로
    채우는 imputation은 쓰지 않는다. 0.0은 "이 wafer가 해당 parameter·step을
    지나지 않았다"는 사실 자체를 feature로 보존하고, train 평균으로 메우면 그
    사실이 지워지고 fit_normalizer가 결측 여부와 무관하게 항상 같은 값을 보게
    되어 오히려 정보 손실이라고 판단했다.
    """
    # by_wafer: {lot_hist_id: {feature_이름: 값}} — wafer별로 "이름표 붙은 값 모음"을
    # 임시로 쌓아두는 dict다. 아직 feature_names 순서로 정렬되기 전 단계다.
    by_wafer: dict[str, dict[str, float]] = {}
    lot_of: dict[str, str] = {}  # lot_hist_id -> lot_id (나중에 LOT 단위 split에 쓴다)
    for gf in group_features:
        row = by_wafer.setdefault(gf.key.lot_hist_id, {})
        prefix = f"{gf.key.parameter_id}__step{gf.key.recipe_step_no}__"
        row[prefix + "mean_dist"] = gf.relative_mean_distance
        row[prefix + "std"] = gf.relative_std
        row[prefix + "ooc_ratio"] = gf.ooc_ratio
        row[prefix + "oos_ratio"] = gf.oos_ratio
        row[prefix + "coverage"] = gf.coverage
        lot_of[gf.key.lot_hist_id] = gf.lot_id

    vectors: list[WaferFeatureVector] = []
    # lot_hist_id 기준으로도 정렬한다 — feature_schema와 같은 이유로, 매번 같은
    # 순서의 결과 목록이 나와야 재현성이 보장된다.
    for lot_hist_id, row in sorted(by_wafer.items()):
        # row.get(name, 0.0): 이 wafer가 해당 (parameter, step)을 지나지 않아
        # row에 그 이름이 아예 없으면 0.0을 대신 넣는다 — 위 docstring에서 설명한
        # "결측은 0.0으로 남긴다"는 원칙이 실제로 구현되는 부분이 바로 여기다.
        values = tuple(row.get(name, 0.0) for name in feature_names)
        vectors.append(
            WaferFeatureVector(
                lot_hist_id=lot_hist_id, lot_id=lot_of[lot_hist_id], values=values
            )
        )
    return vectors


# ---------------------------------------------------------------------
# 2) LOT 단위 분할 — 결정론적 shuffle
# ---------------------------------------------------------------------
def split_lots(
    lot_ids: Sequence[str],
    seed: int = RANDOM_SEED,
    train_ratio: float = TRAIN_LOT_RATIO,
) -> tuple[list[str], list[str]]:
    """lot_id 목록을 고정 seed로 섞어 train/test로 나눈다.

    wafer(lot_hist_id) 단위가 아니라 LOT 단위 분할이라 같은 LOT의 서로 다른 wafer가
    train·test 양쪽에 나타나지 않는다 (V5-A-2.1 완료 기준: LOT 단위 분리). 왜
    이게 중요한지는 모듈 docstring의 "데이터 누수" 설명을 본다.
    """
    # set(lot_ids): 중복 lot_id 제거(같은 LOT에 wafer가 여러 개면 lot_id가
    # 여러 번 나오므로). sorted(): set은 매 실행마다 순회 순서가 달라질 수
    # 있는 파이썬 컬렉션이라, 반드시 정렬해서 "무작위 섞기 직전 순서"를
    # 고정해야 한다 — 그래야 이후 rng.permutation의 결과도 재현된다.
    unique_sorted = sorted(set(lot_ids))
    # np.random.default_rng(seed): "이 seed로 시작하는 난수 생성기"를 만든다.
    # 같은 seed로 만든 생성기는 항상 같은 순서로 "무작위" 값을 내놓는다.
    rng = np.random.default_rng(seed)
    # permutation: 리스트를 무작위로 섞은 새 리스트를 만든다(원본은 안 바뀜).
    shuffled = rng.permutation(unique_sorted).tolist()
    # 섞인 리스트를 train_ratio(기본 80%) 지점에서 잘라 앞은 train, 뒤는 test로 쓴다.
    cut = round(len(shuffled) * train_ratio)
    return shuffled[:cut], shuffled[cut:]


# ---------------------------------------------------------------------
# 3) 정규화 — train만으로 산정
# ---------------------------------------------------------------------
def fit_normalizer(train_matrix: np.ndarray) -> Normalizer:
    """train 행렬의 각 열(feature)마다 평균·표준편차를 구해 `Normalizer`로 담는다.

    "fit"이라는 이름은 scikit-learn 관례를 따른 것이다 — "데이터를 보고 기준을
    잡는다"는 뜻으로, 항상 train 데이터에만 호출한다(test에는 절대 호출하지 않고,
    train에서 만든 결과를 `apply_normalizer`로 그대로 적용만 한다).
    """
    mean = train_matrix.mean(
        axis=0
    )  # axis=0: 행 방향(여러 wafer)으로 평균 -> feature별 평균
    std = train_matrix.std(axis=0)
    # 표준편차가 0인 feature(모든 wafer에서 값이 완전히 똑같았던 경우)를 그대로
    # 나눗셈에 쓰면 0으로 나누기 오류가 난다. 그런 feature는 "구분력이 없다"는
    # 뜻이니 표준편차를 1로 바꿔서 나눠도 값이 그대로(0/1=0) 나오게 만든다.
    std_safe = np.where(std == 0, 1.0, std)
    return Normalizer(mean=tuple(mean.tolist()), std=tuple(std_safe.tolist()))


def apply_normalizer(matrix: np.ndarray, normalizer: Normalizer) -> np.ndarray:
    """`fit_normalizer`가 구해둔 평균·표준편차로 z-score 변환을 적용한다.

    z-score 공식은 (값 - 평균) / 표준편차다. 이 함수는 train·test 양쪽 모두에
    쓰이지만, `normalizer` 인자로 들어오는 평균·표준편차는 항상 train에서
    구해진 값이어야 한다(호출부인 학습 스크립트가 그 규칙을 지킨다).
    """
    mean = np.array(normalizer.mean)
    std = np.array(normalizer.std)
    return (matrix - mean) / std


# ---------------------------------------------------------------------
# 4) 학습·채점
# ---------------------------------------------------------------------
def train_isolation_forest(
    train_matrix: np.ndarray,
    seed: int = RANDOM_SEED,
    n_estimators: int = N_ESTIMATORS,
    contamination: str | float = CONTAMINATION,
) -> IsolationForest:
    """IsolationForest를 학습한다.

    IsolationForest가 하는 일을 직관적으로 설명하면: 데이터를 무작위 기준값으로
    반씩 잘라나가는 이진 트리를 여러 개(n_estimators개) 만든다. 정상 데이터는
    "다수"에 섞여 있어서 격리(고립)되기까지 여러 번 잘라야 하지만, 이상치는
    "튀는 값"이라 몇 번만 잘라도 금방 혼자 남는다(= 빨리 격리된다). 그래서
    "평균적으로 몇 번 만에 격리됐는지"가 짧을수록 이상치일 가능성이 높다고
    본다 — 이게 바로 `raw_anomaly_scores`가 다루는 원점수의 근거다.

    `random_state=seed`가 트리를 무작위로 만드는 과정 자체를 고정해서, 같은
    데이터·같은 seed면 항상 같은 트리 구조 -> 같은 점수가 나오게 한다
    (재현성 완료 기준의 핵심 장치).
    """
    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=seed,
    )
    model.fit(train_matrix)
    return model


def raw_anomaly_scores(model: IsolationForest, matrix: np.ndarray) -> np.ndarray:
    """sklearn은 낮을수록 이상(score_samples)이라 부호를 뒤집어 방향을 통일한다
    (설계서 4.4: 높은 값이 더 이상하도록 score 방향을 통일).

    `model.score_samples(matrix)`는 sklearn 관례상 "정상일수록 큰 값, 이상할수록
    작은(음수) 값"을 반환한다 — 우리가 원하는 것과 정반대 방향이다. 그래서 맨
    앞에 마이너스(-)를 붙여 부호를 뒤집는다: 이제 값이 클수록 더 이상하다는
    뜻이 되어, 이후 모든 계산·화면 표시와 방향이 일치한다.
    """
    return -model.score_samples(matrix)


def fit_score_scaling(train_raw_scores: np.ndarray) -> ScoreScaling:
    """train 원점수의 최솟값·최댓값을 구해 `ScoreScaling`으로 담는다.

    이 최소·최대값이 나중에 `scale_scores`가 쓰는 "0점과 1점의 기준선"이 된다.
    `fit_normalizer`와 마찬가지로 반드시 train 데이터에서만 호출한다.
    """
    return ScoreScaling(
        raw_min=float(train_raw_scores.min()),
        raw_max=float(train_raw_scores.max()),
    )


def scale_scores(raw_scores: np.ndarray, scaling: ScoreScaling) -> np.ndarray:
    """min-max 스케일링으로 원점수를 [0, 1] 범위로 눌러 담는다.

    공식은 (값 - 최솟값) / (최댓값 - 최솟값)이다. train에서 가장 낮았던 점수가
    0, 가장 높았던 점수가 1이 되도록 나머지 점수들을 그 사이 비율로 늘어놓는
    셈이다. test·운영 채점에서 train 범위를 벗어나는 값이 나올 수도 있으므로
    (train에서 못 본 더 심한 이상치일 수 있다), 마지막에 `np.clip`으로 0~1을
    벗어나지 않게 잘라낸다.
    """
    span = scaling.raw_max - scaling.raw_min
    if span <= 0:
        # 모든 train 점수가 완전히 똑같았던 극단적인 경우(사실상 있을 수 없지만
        # 나눗셈 오류를 막기 위한 방어 코드) — 전부 0으로 처리한다.
        return np.zeros_like(raw_scores)
    scaled = (raw_scores - scaling.raw_min) / span
    return np.clip(scaled, 0.0, 1.0)


def compute_display_threshold(
    train_scores: np.ndarray, quantile: float = DISPLAY_THRESHOLD_QUANTILE
) -> float:
    """train LOT score 분포에서만 산정한다 — 합성 라벨(evaluation loader)을 보지 않는다
    (NFR-19, V5-A-2.3 라벨 격리와의 경계).

    "quantile(분위수)"란 데이터를 작은 값부터 큰 값까지 줄 세웠을 때 특정
    비율 지점의 값을 말한다. `quantile=0.95`는 "점수를 오름차순으로 줄 세웠을
    때 하위 95% 지점의 값"을 뜻하며, 이 값보다 높은 점수는 train 데이터
    전체에서 상위 5%에 든다는 의미다. 이 지점을 "화면에서 강조 표시할
    기준선"으로 쓴다 — 정답이 있어서 정하는 게 아니라, "우리 데이터에서
    상대적으로 튀는 축에 속하는지"를 기준으로 잡는 방식이다.
    """
    return float(np.quantile(train_scores, quantile))


# ---------------------------------------------------------------------
# 5) AnomalySignal 변환 — app.common.tool_contracts와 연결
# ---------------------------------------------------------------------
def to_anomaly_signal(score: float, manifest: ModelManifest):
    """score 하나를 기존 AnomalySignal DTO로 감싼다.

    이 함수는 계산을 하지 않는다 — 이미 계산된 score와 manifest의 설정값들을
    API/Tool이 그대로 쓸 수 있는 `AnomalySignal` 모양(pydantic 모델)으로
    "포장"만 한다. `is_anomaly`는 여기서 `score >= display_threshold`로 그
    자리에서 계산하는데, `AnomalySignal` 안의 검증 로직(model_validator)이
    "is_anomaly가 score·display_threshold와 일치하는지"를 다시 확인하므로,
    여기서 계산을 틀리면 이 함수를 호출하는 즉시 예외가 난다(이중 안전장치).

    action_threshold·threshold_version은 의도적으로 비운다 — 시스템설계서 v2.1 4.4는
    score가 조치 게이트에 관여하지 않는다고 명시하므로 표시용 display_threshold·
    is_anomaly만 채우고 threshold_validation_status는 UNVERIFIED로 둔다.
    action_threshold를 채우는 것은 이 프로젝트 범위 밖이며(레거시 기획 전용),
    채운다면 V5-A-2.2 계약 테스트를 먼저 갱신해야 한다.
    """
    # 이 두 import를 함수 안에 둔 이유: 모듈 최상단에서 import하면 model.py를
    # 그냥 "학습 계산만" 쓰고 싶은 곳(예: 학습 스크립트)에서도 항상
    # app.common.tool_contracts·app.common.enums까지 로드해야 한다. 이 함수를
    # 실제로 쓸 때만(즉, DTO 변환이 필요할 때만) 그 무거운 의존성을 가져오도록
    # 미뤄둔 것이다(지연 import).
    from app.common.enums import ThresholdValidationStatus
    from app.common.tool_contracts import AnomalySignal

    return AnomalySignal(
        score=score,
        model_version=manifest.model_version,
        score_method=manifest.score_method,
        display_threshold=manifest.display_threshold,
        is_anomaly=score >= manifest.display_threshold,
        threshold_validation_status=ThresholdValidationStatus.UNVERIFIED,
    )
