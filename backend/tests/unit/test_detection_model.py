"""V5-A-2.1 / V5-A-4.1 비지도 anomaly score 회귀 테스트.

DB 접속 없이도 항상 돌아가는 순수 테스트만 담는다. repository 함수의 라벨 격리는
DB에 실제로 붙는 대신 소스 텍스트 검사로 고정하고(`test_repository_fetch_
functions_never_touch_labels`), 재현성은 합성 데이터로 파이프라인을 두 번 돌려
bit-identical한지 확인한다(`test_reproducible_scores_end_to_end`) — 둘 다 DB
fixture 없이 CI에서 항상 실행된다.

[테스트를 처음 읽는 사람을 위한 안내]
각 테스트 이름은 "무엇을 보장하는지"를 그대로 문장처럼 담고 있다(pytest 관례).
예를 들어 `test_split_lots_is_lot_disjoint_and_deterministic`는 "split_lots가
만든 train/test는 서로 겹치지 않고(disjoint), 몇 번을 다시 돌려도 같은 결과가
나온다(deterministic)"를 확인한다는 뜻이다. `assert 조건`은 "조건이 거짓이면
이 테스트를 실패로 표시하고 멈춘다"는 파이썬 문법이다.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from app.detection import model as anomaly_model
from app.detection import repository
from app.detection.summarize import GroupKey, ParameterLimit


def _limit(parameter_id: str = "P1", upper_only: bool = False) -> ParameterLimit:
    """테스트에서 반복해서 필요한 "가짜 한계선"을 만들어주는 헬퍼.

    실제 dim_parameter 값이 아니라 테스트용으로 대충 정한 값(하한 0~1, 상한 9~10)
    이다 — 이 값 자체의 의미보다 "한계선이 있다"는 사실만 필요한 테스트들이
    이 함수를 재사용한다. 함수 이름 앞의 밑줄(_)은 "이 파일 밖에서는 쓰지 않는
    내부 전용 헬퍼"라는 파이썬 관례 표시다.
    """
    return ParameterLimit(
        parameter_id=parameter_id,
        spec_lower=None if upper_only else 0.0,
        ctrl_lower=None if upper_only else 1.0,
        ctrl_upper=9.0,
        spec_upper=10.0,
        upper_only=upper_only,
    )


def test_split_lots_is_lot_disjoint_and_deterministic() -> None:
    """LOT 단위 분리가 왜 중요한지는 model.py 모듈 docstring의 "데이터 누수"
    설명을 본다. 여기서는 그 요구사항 두 가지를 직접 확인한다.

      1) 결정론적(deterministic): 같은 lot_ids·같은 seed로 두 번 호출해도
         항상 완전히 같은 train/test 목록이 나와야 한다(재현성).
      2) 서로소(disjoint): train과 test에 같은 lot_id가 동시에 들어있으면
         안 된다 — 하나라도 겹치면 데이터 누수다.
    """
    lot_ids = [f"LOT{i:03d}" for i in range(20)]
    train_a, test_a = anomaly_model.split_lots(lot_ids)
    train_b, test_b = anomaly_model.split_lots(lot_ids)

    assert train_a == train_b  # 결정론적: 두 번 호출한 결과가 완전히 같아야 한다
    assert test_a == test_b
    assert set(train_a).isdisjoint(set(test_a))  # 서로소: 겹치는 lot_id가 0개
    # 합집합이 원본 전체와 같아야 한다 — train에도 test에도 안 들어가고
    # "사라진" lot_id가 있으면 안 된다는 뜻이다.
    assert set(train_a) | set(test_a) == set(lot_ids)


def test_compute_expected_point_counts_uses_observed_maximum() -> None:
    """ "만점"이 정말로 "관측된 값 중 최댓값"으로 계산되는지 확인한다.

    (P1, step=1) 조합은 point_cnt가 [3, 2] 두 번 관측됐으니 만점은 더 큰 값인
    3이어야 하고, (P2, step=1)은 딱 한 번(5)만 관측됐으니 그 값 그대로 5가
    만점이어야 한다. lot_hist_id(H1/H2)가 달라도 parameter·step만 같으면
    같은 "만점"을 공유한다는 점도 이 테스트가 함께 보여준다.
    """
    records = [
        (GroupKey(lot_hist_id="H1", parameter_id="P1", recipe_step_no=1), 3),
        (GroupKey(lot_hist_id="H2", parameter_id="P1", recipe_step_no=1), 2),
        (GroupKey(lot_hist_id="H1", parameter_id="P2", recipe_step_no=1), 5),
    ]
    result = anomaly_model.compute_expected_point_counts(records)

    assert result[("P1", 1)] == 3  # 관측된 point_cnt 중 최댓값(3, 2 중 3)
    assert result[("P2", 1)] == 5


def test_feature_schema_rejects_forbidden_tokens() -> None:
    """라벨 누수 방어선 1차: feature 이름 자체에 금지 토큰이 섞이면 즉시 예외가
    나야 한다.

    parameter_id를 일부러 "fault_code_like"로 지어서(실제로는 parameter_id가
    이런 이름일 리 없지만, "이름에 금지 토큰이 들어가면 어떻게 되나"를
    테스트하기 위한 가정이다) feature_schema를 호출했을 때 정말로
    `ValueError`가 나는지 확인한다. `pytest.raises(ValueError)` 블록 안의
    코드가 그 예외를 던지지 않으면 이 테스트 자체가 실패한다.
    """
    key = GroupKey(lot_hist_id="H1", parameter_id="fault_code_like", recipe_step_no=1)
    gf = anomaly_model.build_group_feature(
        key=key,
        lot_id="LOT001",
        value_mean=5.0,
        value_std=1.0,
        point_cnt=3,
        ooc_point_cnt=0,
        oos_point_cnt=0,
        limit=_limit(parameter_id="fault_code_like"),
        expected_point_cnt=3,
    )
    with pytest.raises(ValueError):
        anomaly_model.feature_schema([gf])


def test_aggregate_wafer_features_fills_missing_with_zero() -> None:
    """ "결측은 0.0으로 남긴다"는 model.py의 설계를 실제 데이터로 확인한다.

    wafer H1은 parameter P1만 지났고(step1), wafer H2는 parameter P2만 지났다
    (즉 서로 다른 parameter를 측정했다). 이 상태로 feature_schema를 만들면
    이름 목록에는 P1과 P2가 둘 다 들어가므로, H1의 벡터에서 "P2__step1__
    mean_dist" 자리는 H1이 실제로 측정한 적 없는 값이다 — 이 자리가 정확히
    0.0으로 채워지는지 확인한다.
    """
    key1 = GroupKey(lot_hist_id="H1", parameter_id="P1", recipe_step_no=1)
    key2 = GroupKey(lot_hist_id="H2", parameter_id="P2", recipe_step_no=1)
    gf1 = anomaly_model.build_group_feature(
        key=key1,
        lot_id="LOT001",
        value_mean=5.0,
        value_std=1.0,
        point_cnt=3,
        ooc_point_cnt=1,
        oos_point_cnt=0,
        limit=_limit("P1"),
        expected_point_cnt=3,
    )
    gf2 = anomaly_model.build_group_feature(
        key=key2,
        lot_id="LOT002",
        value_mean=5.0,
        value_std=1.0,
        point_cnt=3,
        ooc_point_cnt=0,
        oos_point_cnt=0,
        limit=_limit("P2"),
        expected_point_cnt=3,
    )
    names = anomaly_model.feature_schema([gf1, gf2])
    vectors = anomaly_model.aggregate_wafer_features([gf1, gf2], names)

    by_id = {v.lot_hist_id: v for v in vectors}
    # H1은 P2__step1 그룹을 지나지 않았으므로 해당 feature가 0.0으로 채워져야 한다
    p2_index = names.index("P2__step1__mean_dist")
    assert by_id["H1"].values[p2_index] == 0.0


def test_score_direction_and_bounds() -> None:
    """ "높을수록 이상"과 "점수는 항상 0~1 사이"라는 두 가지 계약을 확인한다.

    정상적인(평균 0, 표준편차 1짜리 정규분포) 데이터 50개로 모델을 학습시킨
    뒤, 값이 8.0으로 확 튀는 명백한 이상치 하나를 채점해본다. 이 이상치의
    점수가 정상 데이터 점수들의 상위 10% 지점(quantile 0.9)보다 높게
    나와야, "이상할수록 점수가 높다"는 방향이 실제로 맞다고 확인할 수 있다.
    (완전히 무작위 데이터라 이상치가 항상 1등을 하리라는 보장은 없지만, 최소
    상위권에는 들어야 정상이다.)
    """
    rng = np.random.default_rng(0)
    train_matrix = rng.normal(0, 1, size=(50, 4))  # "정상" 데이터 50개(4개 feature)
    outlier = np.array([[8.0, 8.0, 8.0, 8.0]])  # 누가 봐도 튀는 값 하나

    forest = anomaly_model.train_isolation_forest(train_matrix)
    train_raw = anomaly_model.raw_anomaly_scores(forest, train_matrix)
    scaling = anomaly_model.fit_score_scaling(train_raw)

    train_scores = anomaly_model.scale_scores(train_raw, scaling)
    outlier_score = anomaly_model.scale_scores(
        anomaly_model.raw_anomaly_scores(forest, outlier), scaling
    )

    assert (train_scores >= 0).all() and (train_scores <= 1).all()  # 항상 0~1 범위
    # 이상치가 정상 분포 상위 90%보다 더 높은 점수를 받아야 한다(방향 통일 확인)
    assert outlier_score[0] >= np.quantile(train_scores, 0.9)


def test_normalizer_uses_train_only() -> None:
    """정규화 기준이 "test를 보고 다시 계산되지 않는지"를 확인한다.

    train_matrix([0, 2] 두 값)로 평균·표준편차를 구해두고, 그 기준을 완전히
    다른 범위의 test_matrix(100)에 적용했을 때, apply_normalizer가 test 값을
    보고 기준을 다시 잡지 않고 "train에서 구한 그대로"의 평균·표준편차로만
    계산하는지를 수식으로 직접 비교해서 확인한다. 만약 apply_normalizer가
    실수로 test 데이터까지 포함해 평균·표준편차를 다시 계산하도록 바뀐다면
    이 테스트가 깨진다 — 이런 실수가 바로 정규화 단계의 데이터 누수다.
    """
    train_matrix = np.array([[0.0, 0.0], [2.0, 2.0]])
    normalizer = anomaly_model.fit_normalizer(train_matrix)

    test_matrix = np.array([[100.0, 100.0]])  # train에 없던 극단값
    normalized = anomaly_model.apply_normalizer(test_matrix, normalizer)

    # normalizer가 test_matrix를 보고 재산정되지 않고, train 평균·표준편차를
    # 그대로 적용했는지 확인한다.
    expected_mean = np.array(normalizer.mean)
    expected_std = np.array(normalizer.std)
    assert np.allclose(normalized, (test_matrix - expected_mean) / expected_std)


# ---------------------------------------------------------------------
# V5-A-2.1 완료 기준: "Fault·metrology·Generator 누수 0건을 검증한다"
#
# 왜 "DB에 직접 붙어서" 확인하지 않고 소스 텍스트를 검사하는지: 이 프로젝트의
# 공용 DB(kosa165.iptime.org)는 팀 밖에서(예: 채점 환경·CI) 항상 접근 가능한
# 게 아니라서, "DB가 있어야만 통과하는 테스트"는 DB가 없는 환경에서 실행 자체가
# 안 된다. 반면 "이 함수의 소스 코드에 금지 단어가 있는가"는 파이썬 코드만
# 있으면 언제 어디서든 100% 같은 결과로 판정할 수 있다 — 그래서 이 방식을 골랐다.
# ---------------------------------------------------------------------
_FORBIDDEN_SQL_TOKENS = ("metrology", "fault_code", "action_history", "alarm_result")
_LEAKAGE_CHECKED_FUNCTIONS = (
    repository.fetch_reference_summary,
    repository.fetch_reference_evaluation,
    repository.fetch_parameter_limits,
    repository.fetch_lot_history_rows,
)


def test_repository_fetch_functions_never_touch_labels() -> None:
    """`train_anomaly_score_model.py`가 실제로 호출하는 4개 조회 함수의 소스 전체
    (SQL 리터럴 포함)에 금지 토큰이 없는지 정적으로 검사한다.

    `inspect.getsource(fn)`은 파이썬 표준 라이브러리 기능으로, 함수 `fn`이
    정의된 소스 코드를 문자열 그대로 가져온다(docstring·주석·SQL 문자열
    리터럴까지 전부 포함). 이 문자열을 소문자로 바꾼 뒤(대소문자 차이로
    빠져나가지 못하게) `_FORBIDDEN_SQL_TOKENS`의 각 단어가 들어있는지 하나씩
    확인한다. 새 컬럼·JOIN을 추가하다 실수로 저 테이블 이름을 SQL에 끌어오면
    이 테스트가 즉시 깨진다 — "코드 리뷰에서 놓쳐도 테스트가 잡아준다"는
    안전망 역할이다.
    """
    for fn in _LEAKAGE_CHECKED_FUNCTIONS:
        source = inspect.getsource(fn).lower()
        for token in _FORBIDDEN_SQL_TOKENS:
            assert token not in source, (
                f"{fn.__name__}의 소스에서 금지된 토큰을 발견했습니다: {token!r}"
            )


def test_forbidden_feature_tokens_catch_all_labels() -> None:
    """두 개의 "금지 단어 목록"이 서로 어긋나지 않는지 확인한다.

    이 파일의 `_FORBIDDEN_SQL_TOKENS`(SQL 소스 검사용)와 `model.py`의
    `_FORBIDDEN_FEATURE_TOKENS`(feature 이름 검사용)는 서로 독립적으로
    관리되는 목록이다. 누군가 한쪽에만 새 금지어를 추가하고 다른 쪽을
    깜빡하면, 방어선 하나가 조용히 구멍이 뚫린 채로 남는다. 이 테스트는
    "SQL 쪽에서 막는 단어라면 feature 이름 쪽에서도 (부분적으로라도) 막고
    있는지"를 확인해서 그 어긋남을 미리 잡아낸다.
    """
    for token in _FORBIDDEN_SQL_TOKENS:
        # metrology의 실제 컬럼명은 alarm_result지만 두 목록이 어긋나지 않는지만
        # 확인하면 되므로, 토큰 자체가 서로의 부분 문자열인지만 본다.
        assert any(
            token in forbidden or forbidden in token
            for forbidden in anomaly_model._FORBIDDEN_FEATURE_TOKENS
        ), f"model.py의 feature allowlist가 {token!r}를 막지 못합니다"


# ---------------------------------------------------------------------
# V5-A-2.1 완료 기준: "재현 가능한 score를 만들고 ... 를 고정"
# ---------------------------------------------------------------------
def _synthetic_group_features() -> list[anomaly_model.WaferGroupFeature]:
    """DB 없이 파이프라인 전체를 돌리기 위한 합성(가짜) group feature를 만든다.

    실제 DB 데이터를 쓰지 않는 이유는 두 가지다. 첫째, 이 테스트 파일은 DB
    접속 없이 항상 통과해야 한다(위에서 설명한 이유와 같다). 둘째, "합성
    데이터"이기 때문에 오히려 매번 정확히 같은 데이터로 테스트할 수 있어서
    재현성 검증에 더 적합하다(실제 DB는 나중에 행이 추가되는 등 변할 수
    있지만, 이 함수가 만드는 데이터는 항상 똑같다 — `rng = np.random.default_rng(42)`로
    seed를 고정했기 때문이다).

    lot 30개 x wafer 2장 x parameter 3개 x step 2개로, 실제 스키마 비율(8
    parameter/2 area)보다 작지만 split·정규화·학습이 전부 의미 있게 동작할
    만큼은 된다(데이터가 너무 적으면 IsolationForest 학습 자체가 불안정해질
    수 있다).
    """
    rng = np.random.default_rng(42)
    features: list[anomaly_model.WaferGroupFeature] = []
    for lot_idx in range(30):
        lot_id = f"LOT{lot_idx:04d}"
        for wafer_idx in range(2):
            lot_hist_id = f"{lot_id}-W{wafer_idx}"
            for param_idx in range(3):
                parameter_id = f"P{param_idx}"
                for step in (1, 2):
                    key = GroupKey(
                        lot_hist_id=lot_hist_id,
                        parameter_id=parameter_id,
                        recipe_step_no=step,
                    )
                    features.append(
                        anomaly_model.build_group_feature(
                            key=key,
                            lot_id=lot_id,
                            value_mean=float(rng.normal(5.0, 1.0)),
                            value_std=float(abs(rng.normal(0.5, 0.1))),
                            point_cnt=3,
                            ooc_point_cnt=int(rng.integers(0, 2)),
                            oos_point_cnt=0,
                            limit=_limit(parameter_id),
                            expected_point_cnt=3,
                        )
                    )
    return features


def _run_pipeline(
    group_features: list[anomaly_model.WaferGroupFeature],
) -> tuple[tuple[str, ...], list[str], list[str], np.ndarray, float]:
    """`train_anomaly_score_model.py`의 `train()`과 같은 순서로 model.py 함수를
    엮어서, 실제 DB 스크립트와 똑같은 파이프라인을 테스트 안에서 재현한다.

    이 함수를 따로 뺀 이유는 아래 `test_reproducible_scores_end_to_end`가
    "완전히 같은 절차를 두 번 실행"해야 하기 때문이다 — 같은 코드를 두 번
    복사해 붙이는 대신, 함수 하나로 만들어 두 번 호출하면 "정말 같은 절차를
    실행했다"는 게 코드로도 명확해진다.
    """
    feature_names = anomaly_model.feature_schema(group_features)
    vectors = anomaly_model.aggregate_wafer_features(group_features, feature_names)

    lot_ids = [v.lot_id for v in vectors]
    train_lots, test_lots = anomaly_model.split_lots(lot_ids)
    train_set = set(train_lots)
    train_vectors = [v for v in vectors if v.lot_id in train_set]

    train_matrix = np.array([v.values for v in train_vectors])
    normalizer = anomaly_model.fit_normalizer(train_matrix)
    train_norm = anomaly_model.apply_normalizer(train_matrix, normalizer)

    forest = anomaly_model.train_isolation_forest(train_norm)
    train_raw = anomaly_model.raw_anomaly_scores(forest, train_norm)
    scaling = anomaly_model.fit_score_scaling(train_raw)
    train_scores = anomaly_model.scale_scores(train_raw, scaling)
    threshold = anomaly_model.compute_display_threshold(train_scores)

    return feature_names, train_lots, test_lots, train_scores, threshold


def test_reproducible_scores_end_to_end() -> None:
    """같은 데이터·같은 seed면 feature 이름·LOT 분할·score·threshold가 전부
    bit-identical해야 한다(V5-A-2.1 완료 기준: 재현 가능한 score).

    "재현 가능하다"는 게 정확히 뭘 뜻하는지 이 테스트가 실제로 보여준다:
    똑같은 `group_features`를 `_run_pipeline`에 두 번 통과시켰을 때 —
      - feature 이름 순서(names)가 완전히 같아야 하고
      - train/test로 나뉜 LOT 목록이 완전히 같아야 하고
      - 최종 score 배열이 (부동소수점 오차 하나 없이) 완전히 같아야 하고
      - display_threshold 값도 완전히 같아야 한다.
    이 넷 중 하나라도 실행마다 달라진다면, "누군가 다시 학습을 돌렸더니 전에
    보던 점수와 달라졌다"는 상황이 실제로 벌어질 수 있다는 뜻이라 이 테스트가
    실패해야 한다. `np.array_equal`은 두 numpy 배열의 모든 원소가 정확히
    같은지 비교하는 함수다(부동소수점이라도 계산 경로가 완전히 같으면 오차
    없이 똑같이 나온다 — RANDOM_SEED가 IsolationForest 내부 무작위성까지
    전부 고정하기 때문이다).
    """
    group_features = _synthetic_group_features()

    names_a, train_lots_a, test_lots_a, scores_a, threshold_a = _run_pipeline(
        group_features
    )
    names_b, train_lots_b, test_lots_b, scores_b, threshold_b = _run_pipeline(
        group_features
    )

    assert names_a == names_b
    assert train_lots_a == train_lots_b
    assert test_lots_a == test_lots_b
    assert np.array_equal(scores_a, scores_b)
    assert threshold_a == threshold_b


_TODO_ACTION_BOUNDARY = (
    "TODO(팀): ActionPolicy가 이 저장소에 아직 구현되지 않았다(V5-A-2.2/V5-C 영역). "
    "구현되는 즉시 그 decide() 시그니처에 score가 없음을 계약 테스트로 고정한다."
)


@pytest.mark.skip(reason=_TODO_ACTION_BOUNDARY)
def test_score_not_passed_to_action_policy() -> None:
    """[아직 작성 불가] score가 ActionPolicy.decide()의 입력에 절대 들어가지
    않는다는 것을 고정하려는 테스트의 자리표시자(placeholder)다.

    `@pytest.mark.skip`이 붙어 있으면 pytest는 이 테스트를 "실패"가 아니라
    "건너뜀(SKIPPED)"으로 표시한다 — 실행은 안 하지만 존재를 잊지 않도록
    남겨두는 용도다. `ActionPolicy`라는 클래스 자체가 이 저장소에 아직 없어서
    (V5-A-2.2/V5-C 영역에서 구현 예정) 지금은 무엇을 검사할지조차 정할 수
    없다. 그 클래스가 생기는 즉시 이 자리에 실제 검증 코드를 채워야 한다.
    """
