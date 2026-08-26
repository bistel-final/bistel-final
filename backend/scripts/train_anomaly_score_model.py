"""V5-A-2.1 비지도 anomaly score 모델 학습 스크립트.

[이 파일이 하는 일 — 한 문장 요약]
`app/detection/model.py`에 있는 "순수 계산 부품"들을 실제 DB 조회와 이어붙여서,
진짜로 학습을 돌리고 그 결과(학습된 모델 + 설정값 영수증)를 파일로 저장하는
"조립 라인"이다. `model.py`는 재료 손질법만 알고 있고, 실제로 냉장고(DB)에서
재료를 꺼내와 순서대로 조리해 접시에 담아내는(파일로 저장하는) 역할은 이
스크립트가 한다.

읽기는 `app.common.db.engine`(팀 공용 `.env`의 `POSTGRES_USER`, 즉 `kosa` 계정)으로
한다. 원래는 읽기 전용 role(`READONLY_USER`="kosa_readonly", Text2SQL 기능이 처음
발급받은 계정)을 쓰는 편이 "학습 스크립트는 절대 아무것도 쓰지 않는다"는 계약을
role 수준에서도 보장할 수 있어(설령 코드에 실수로 INSERT문을 넣어도 그 계정은
권한이 없어 애초에 실행이 안 된다) 더 안전하지만, 그 계정 비밀번호가 각자
로컬 `.env`에 채워져 있지 않은 경우가 있어(팀 전체에 배포가 안 됐거나 Text2SQL
작업자만 받은 경우) 실행 자체가 막힐 수 있다. 이 스크립트는 실제로 INSERT·UPDATE를
전혀 하지 않으므로(아래 4개 함수는 전부 SELECT), 이미 각자 `.env`에 채워져 있는
일반 앱 계정(`engine`)으로 접속해도 실질적인 동작은 동일하다. 다만 "DB 권한
자체가 실수를 막아주는" 안전장치 한 겹이 빠지는 대신, `main()`에서 커넥션을 연
직후 `SET TRANSACTION READ ONLY`를 실행해 같은 효과를 코드 수준에서 되찾아뒀다
(권한 배포와 무관하게 이 트랜잭션 안에서는 어떤 쓰기 문도 DB가 거부한다). 팀
전체에 `READONLY_PASSWORD`가 배포되면 `engine` 대신 `readonly_engine`으로
되돌리는 편이 더 안전하다(계정 자체에 쓰기 권한이 없는 것과 "쓰지 않겠다고
트랜잭션에서 선언"하는 것은 다른 수준의 방어라서 둘 다 있으면 더 좋다).

사용 예 (backend venv 활성화 후):

    cd backend
    python scripts/train_anomaly_score_model.py
    # 저장하지 않고 점수 분포·display_threshold만 미리 보고 싶다면:
    python scripts/train_anomaly_score_model.py --dry-run

`.env`가 가리키는 DB(`POSTGRES_DB`)에서 읽으므로 팀 공용 서버가 아니라 각자
로컬 `.env`가 가리키는 DB(예: 개인 `fdc_final` 검증 DB)를 그대로 쓴다. 결과는
`backend/artifacts/detection_model/{model_version}.joblib`과 같은 이름의
`.manifest.json`으로 저장된다(`--dry-run`이면 저장하지 않는다).

[이 파일 안에서 함수들이 서로를 부르는 순서]
  main()
    ├─ _parse_args()         커맨드라인 옵션(--dry-run 등) 해석
    ├─ engine.connect()      DB 커넥션 열기 (POSTGRES_USER 계정)
    ├─ (커넥션에 SET TRANSACTION READ ONLY 실행 — 쓰기 방지)
    ├─ train(connection)
    │    ├─ fetch_joined_rows(connection)   DB 4개 테이블을 읽어 짝지은 행 목록
    │    ├─ model.split_lots                LOT train/test 분리 (feature보다 먼저!)
    │    ├─ model.compute_expected_point_counts   train 행만으로 coverage 분모
    │    ├─ model.build_group_feature (전체 행에 적용)
    │    ├─ model.feature_schema (train 그룹만)   test 전용 조합은 열로 안 만듦
    │    ├─ model.aggregate_wafer_features   wafer 단위 벡터로 pivot (전체)
    │    ├─ model.fit_normalizer / apply_normalizer   정규화
    │    ├─ model.train_isolation_forest        실제 학습
    │    └─ model.raw_anomaly_scores / scale_scores / compute_display_threshold
    ├─ save_artifacts(manifest, forest, ...)   (--dry-run이 아닐 때만) 파일로 저장
    └─ 결과 요약을 화면에 print

[코드 리뷰 반영 — train/test 분리 순서]
이전 버전은 "전체(train+test) 데이터에서 compute_expected_point_counts·
feature_schema를 먼저 계산한 뒤에" split_lots를 호출했다. 이러면 test LOT의
point_cnt·parameter 조합이 train wafer들의 coverage 분모나 feature 열 구성에
영향을 준다 — model.py 모듈 docstring이 여러 번 강조하는 "test를 보고 기준을
다시 잡으면 안 된다" 원칙을 이 두 곳만 어기고 있었다(정규화·스케일링은 이미
train 전용이었다). 지금 버전은 `split_lots`를 제일 먼저 부르고,
`compute_expected_point_counts`와 `feature_schema` 둘 다 train LOT의 행만
넘겨서 fit한다. test에만 있는 (parameter, step) 조합은 feature 열로 아예
안 만들어지고, coverage 분모가 없는 조합은 0으로 떨어진다 — 둘 다 이 파일이
이미 쓰고 있던 "결측은 0으로 남긴다" 원칙과 같은 처리다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sqlalchemy import text
from sqlalchemy.engine import Connection

# scripts/ 에서 바로 실행해도 `app.*` 를 import 할 수 있도록 backend/ 를
# sys.path 맨 앞에 추가한다. (backend/scripts/*.py 들이 공유하는 관례다. `python
# scripts/train_anomaly_score_model.py`처럼 backend/ 밖에서 실행해도 `import app...`이
# 되게 만들어주는 장치라고 보면 된다.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common.db import engine  # noqa: E402
from app.detection import model as anomaly_model  # noqa: E402
from app.detection.repository import (  # noqa: E402
    fetch_lot_history_rows,
    fetch_parameter_limits,
    fetch_reference_evaluation,
    fetch_reference_summary,
)

# 학습 결과(.joblib)와 그 설정값 영수증(.manifest.json)이 저장될 기본 폴더.
# backend/artifacts/detection_model/if-v1.joblib 같은 경로가 된다.
ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "detection_model"


def fetch_joined_rows(connection: Connection) -> list[tuple]:
    """DB 세 원천(summary_data·evaluation·dim_parameter)과 lot_history(id만)를
    읽어, summary_data의 각 행을 evaluation·limit·lot_history와 짝지은 목록을
    돌려준다.

    metrology·fault_code·action_history는 이 함수에서 절대 조회하지 않는다 — 이
    스크립트가 그 테이블에 접근하는 순간 라벨 격리(NFR-19)가 깨진다.
    `tests/unit/test_detection_model.py::test_repository_fetch_functions_never_touch_labels`가
    호출하는 4개 fetch 함수의 소스를 정적으로 검사해 이를 고정한다.

    반환값은 `(key, summary_row, eval_row, lot_row, limit)` 튜플의 목록이다.
    아직 `expected_point_cnt`(coverage 분모)는 계산하지 않는다 — 그건 train
    LOT의 행만으로 fit해야 하는데, "어느 LOT이 train인지"부터 이 join 결과로
    정해지는 lot_id 목록을 갖고 `split_lots`를 불러야 알 수 있기 때문이다
    (호출부인 `train()`이 이 순서를 담당한다).
    """
    summary = fetch_reference_summary(connection)
    evaluation = fetch_reference_evaluation(connection)
    limits = fetch_parameter_limits(connection)
    # {lot_hist_id: LotHistoryRow} 형태로 바꿔둔다 — summary 그룹을 순회하면서
    # "이 lot_hist_id는 어느 lot_id 소속인지"를 매번 빠르게 찾아보기 위해서다
    # (리스트를 매번 처음부터 뒤지는 것보다 dict 조회가 훨씬 빠르다).
    lot_history = {row.lot_hist_id: row for row in fetch_lot_history_rows(connection)}

    joined: list[tuple] = []
    skipped_unmatched = 0
    # spec_range가 0인 그룹(=규격 상·하한이 없거나 같은 parameter)도 별도로
    # 센다 — 이 그룹은 join은 성공하지만 relative_mean_distance·relative_std가
    # 무조건 0.0이 되어(model.py의 build_group_feature 방어 로직) 그 parameter에
    # 대해 아무 신호도 못 준다. skipped_unmatched(테이블끼리 안 맞는 경우)와는
    # 원인이 달라서 카운터를 분리했다 — 후자는 데이터 정합성 문제고, 전자는
    # "이 parameter는 애초에 이 feature가 의미 없다"는 신호다.
    zero_spec_range_count = 0
    for key, summary_row in summary.items():
        eval_row = evaluation.get(key)
        lot_row = lot_history.get(key.lot_hist_id)
        limit = limits.get(key.parameter_id)
        if eval_row is None or lot_row is None or limit is None:
            skipped_unmatched += 1
            continue
        if anomaly_model.compute_spec_range(limit) == 0.0:
            zero_spec_range_count += 1
        joined.append((key, summary_row, eval_row, lot_row, limit))

    if skipped_unmatched:
        # 표준 print가 아니라 stderr로 보낸다 — main()의 정상 출력(요약 리포트)과
        # 섞이지 않고, `python scripts/train_anomaly_score_model.py > out.txt` 처럼
        # 결과만 파일로 리다이렉트해도 이 경고는 화면에 그대로 보이게 하기 위해서다.
        print(
            f"[train_anomaly_score_model] summary_data·evaluation·dim_parameter·"
            f"lot_history 매칭 실패로 건너뜀: {skipped_unmatched}건",
            file=sys.stderr,
        )
    if zero_spec_range_count:
        print(
            f"[train_anomaly_score_model] spec_range=0(규격 상·하한 없음/동일)이라 "
            f"mean_dist·std가 항상 0으로 떨어지는 그룹: {zero_spec_range_count}건 "
            f"(학습에는 포함되지만 해당 parameter에 대해서는 신호를 주지 못함)",
            file=sys.stderr,
        )
    return joined


def train(connection: Connection) -> tuple[anomaly_model.ModelManifest, object, dict]:
    """학습을 끝까지 돌려 (manifest, fitted IsolationForest, 리포트 dict)를 반환한다.

    저장은 하지 않는다 — 저장은 `main`이 `--dry-run` 여부를 보고 결정한다(이렇게
    "계산"과 "저장"을 함수로 분리해두면, 나중에 단위 테스트에서 "저장 없이
    학습 로직만" 검증하고 싶을 때 이 함수 하나만 호출하면 된다).
    """
    # --- 1) DB -> join된 원본 행 -------------------------------------------
    joined = fetch_joined_rows(connection)
    if not joined:
        raise RuntimeError(
            "summary_data·evaluation·dim_parameter·lot_history 조인 결과가 비었다"
        )

    # --- 2) LOT 단위 train/test 분리 — feature 조립보다 먼저 한다 --------------
    # 코드 리뷰 필수1: compute_expected_point_counts·feature_schema가 test
    # LOT의 값을 보지 못하게 하려면, "어느 LOT이 train인지"를 이 시점에 먼저
    # 확정해야 한다.
    lot_ids = [lot_row.lot_id for _, _, _, lot_row, _ in joined]
    train_lots, test_lots = anomaly_model.split_lots(lot_ids)
    train_lots_set, test_lots_set = set(train_lots), set(test_lots)

    # --- 3) coverage 분모(expected_point_counts) — train 행만으로 fit -------
    train_records = [
        (key, summary_row.point_cnt)
        for key, summary_row, _, lot_row, _ in joined
        if lot_row.lot_id in train_lots_set
    ]
    expected_point_counts = anomaly_model.compute_expected_point_counts(train_records)

    # --- 4) group feature 조립 — 전체(train+test) 행에 적용 -------------------
    # expected_point_counts.get(combo, 0): train에 전혀 없던 (parameter, step)
    # 조합이 test에만 있으면 0을 넘긴다 — build_group_feature는 분모가 0이면
    # coverage=0.0으로 방어하므로(ZeroDivisionError 없음), 이는 "결측은 0으로
    # 남긴다"는 이 파일의 기존 원칙과 일치한다.
    group_features = [
        anomaly_model.build_group_feature(
            key=key,
            lot_id=lot_row.lot_id,
            value_mean=summary_row.value_mean,
            value_std=summary_row.value_std,
            point_cnt=summary_row.point_cnt,
            ooc_point_cnt=eval_row.ooc_point_cnt,
            oos_point_cnt=eval_row.oos_point_cnt,
            limit=limit,
            expected_point_cnt=expected_point_counts.get(
                (key.parameter_id, key.recipe_step_no), 0
            ),
        )
        for key, summary_row, eval_row, lot_row, limit in joined
    ]

    # --- 5) feature 열 이름 — train 그룹에서만 뽑는다 -------------------------
    # 코드 리뷰 필수1: test LOT에만 나타나는 (parameter, step) 조합이 feature
    # 열로 새로 생기면, 그 열은 train 쪽에서 항상 0인 채로 학습에 들어간다
    # (사실상 test 존재 자체가 열 구성에 영향을 준 것과 같다). train 그룹의
    # 조합만으로 열을 고정하면 이 문제가 사라진다.
    train_group_features = [gf for gf in group_features if gf.lot_id in train_lots_set]
    feature_names = anomaly_model.feature_schema(train_group_features)
    vectors = anomaly_model.aggregate_wafer_features(group_features, feature_names)

    train_vectors = [v for v in vectors if v.lot_id in train_lots_set]
    test_vectors = [v for v in vectors if v.lot_id in test_lots_set]
    if not train_vectors or not test_vectors:
        # 데이터가 너무 적거나 LOT 수가 1~2개뿐이면 train/test 중 하나가 통째로
        # 비어버릴 수 있다. 그 상태로 계속 진행하면 뒤에서 "빈 행렬로 학습"
        # 같은 훨씬 알아보기 힘든 오류가 나므로, 여기서 바로 원인을 알 수 있는
        # 메시지와 함께 멈춘다.
        raise RuntimeError(
            "train/test 어느 한쪽이 비었다 — LOT 수·TRAIN_LOT_RATIO를 확인한다 "
            f"(train_lots={len(train_lots)}, test_lots={len(test_lots)})"
        )

    # --- 6) 정규화(z-score) — 반드시 train으로만 기준을 잡는다 -------------------
    # np.array([...]): WaferFeatureVector.values(튜플)들을 세로로 쌓아
    # "wafer 수 x feature 수" 크기의 2차원 행렬로 만든다. IsolationForest를
    # 비롯해 scikit-learn 모델은 전부 이런 (표본 수, feature 수) 행렬을 입력으로 받는다.
    train_matrix = np.array([v.values for v in train_vectors])
    normalizer = anomaly_model.fit_normalizer(train_matrix)
    train_norm = anomaly_model.apply_normalizer(train_matrix, normalizer)

    # --- 7) 학습 ---------------------------------------------------------------
    forest = anomaly_model.train_isolation_forest(train_norm)

    # --- 8) train 점수 분포으로 스케일·표시 임계값 확정 ---------------------------
    train_raw = anomaly_model.raw_anomaly_scores(forest, train_norm)
    scaling = anomaly_model.fit_score_scaling(train_raw)
    train_scores = anomaly_model.scale_scores(train_raw, scaling)
    display_threshold = anomaly_model.compute_display_threshold(train_scores)

    # --- 9) manifest(설정값 영수증) 조립 -----------------------------------------
    # 코드 리뷰 필수2: expected_point_counts·sklearn_version·numpy_version을
    # 추가한다. 앞의 둘이 없으면 나중에 새 wafer를 채점할 때 "학습 때와 같은
    # 분모"를 재현할 수 없고(DB를 다시 훑으면 데이터가 는 만큼 값이 달라질 수
    # 있다), 뒤의 둘이 없으면 "같은 seed·같은 데이터인데 라이브러리 버전이
    # 달라 점수가 달라진" 경우를 이 파일만으로 구분할 수 없다.
    expected_point_counts_sorted = tuple(
        sorted(
            (parameter_id, step, cnt)
            for (parameter_id, step), cnt in expected_point_counts.items()
        )
    )
    manifest = anomaly_model.ModelManifest(
        model_version=anomaly_model.MODEL_VERSION,
        score_method=anomaly_model.SCORE_METHOD,
        random_seed=anomaly_model.RANDOM_SEED,
        feature_names=feature_names,
        normalizer=normalizer,
        scaling=scaling,
        display_threshold=display_threshold,
        n_estimators=anomaly_model.N_ESTIMATORS,
        contamination=str(anomaly_model.CONTAMINATION),
        train_lot_count=len(train_lots),
        test_lot_count=len(test_lots),
        train_wafer_count=len(train_vectors),
        expected_point_counts=expected_point_counts_sorted,
        sklearn_version=sklearn.__version__,
        numpy_version=np.__version__,
    )

    # --- 10) test 쪽도 채점해본다 — "본 적 없는 LOT에서도 점수가 말이 되는지"
    # 눈으로 확인하기 위한 참고용 리포트일 뿐, 이 결과로 모델을 다시 튜닝하지
    # 않는다(그러면 test가 더 이상 "본 적 없는 데이터"가 아니게 된다).
    test_matrix = np.array([v.values for v in test_vectors])
    test_norm = anomaly_model.apply_normalizer(test_matrix, normalizer)
    test_scores = anomaly_model.scale_scores(
        anomaly_model.raw_anomaly_scores(forest, test_norm), scaling
    )

    report = {
        "wafer_count": len(vectors),
        "feature_dim": len(feature_names),
        "train_score_mean": float(train_scores.mean()),
        "train_score_p95": float(np.quantile(train_scores, 0.95)),
        "test_score_mean": float(test_scores.mean()),
        "test_score_p95": float(np.quantile(test_scores, 0.95)),
    }
    return manifest, forest, report


def _manifest_to_json(manifest: anomaly_model.ModelManifest) -> dict:
    """`ModelManifest` dataclass를 그대로 `json.dumps`에 넣을 수 없어서(중첩된
    `Normalizer`/`ScoreScaling` dataclass가 JSON이 모르는 타입이라) 평범한
    dict·list로 한 번 풀어주는 변환 함수다.
    """
    return {
        "model_version": manifest.model_version,
        "score_method": manifest.score_method,
        "random_seed": manifest.random_seed,
        "feature_names": list(manifest.feature_names),
        "normalizer": {
            "mean": list(manifest.normalizer.mean),
            "std": list(manifest.normalizer.std),
        },
        "scaling": {
            "raw_min": manifest.scaling.raw_min,
            "raw_max": manifest.scaling.raw_max,
        },
        "display_threshold": manifest.display_threshold,
        "n_estimators": manifest.n_estimators,
        "contamination": manifest.contamination,
        "train_lot_count": manifest.train_lot_count,
        "test_lot_count": manifest.test_lot_count,
        "train_wafer_count": manifest.train_wafer_count,
        # {"parameter_id__step번호": 만점} 형태의 평평한 dict로 풀어준다 —
        # tuple의 tuple보다 JSON에서 사람이 읽고 찾기 쉽다. 열 이름 규칙
        # (feature_schema의 `f"{param}__step{step}__..."`)과 접두사를 맞췄다.
        "expected_point_counts": {
            f"{parameter_id}__step{step}": cnt
            for parameter_id, step, cnt in manifest.expected_point_counts
        },
        "sklearn_version": manifest.sklearn_version,
        "numpy_version": manifest.numpy_version,
        # 이 값 하나만 "언제 학습했는지" 기록하는 시점 정보이고, 나머지는 전부
        # "무엇으로 학습했는지"를 나타내는 재현성 정보다. generated_at이 달라도
        # 나머지가 전부 같다면 완전히 같은 모델이 다시 만들어졌다는 뜻이다.
        "generated_at": datetime.now(UTC).isoformat(),
    }


def save_artifacts(
    manifest: anomaly_model.ModelManifest, forest, artifact_dir: Path
) -> None:
    """학습된 모델(forest)과 manifest를 디스크에 저장한다.

    두 파일이 항상 짝을 이뤄야 한다 — `.joblib`만 있고 `.manifest.json`이 없으면
    "이 모델이 어떤 feature·정규화 기준으로 학습됐는지" 아무도 알 수 없어서
    운영 중 채점에 쓸 수 없다. 파일 이름을 둘 다 `model_version`으로 맞춰서
    항상 짝으로 관리되게 했다(예: `if-v1.joblib` + `if-v1.manifest.json`).
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # joblib: scikit-learn 모델을 파일로 저장·복원할 때 흔히 쓰는 라이브러리다
    # (pickle과 비슷하지만 numpy 배열이 많은 모델에 더 효율적이다).
    joblib.dump(forest, artifact_dir / f"{manifest.model_version}.joblib")
    (artifact_dir / f"{manifest.model_version}.manifest.json").write_text(
        json.dumps(_manifest_to_json(manifest), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 해석한다. `--help`를 붙여 실행하면 이 설명들이 그대로
    화면에 뜬다(예: `python scripts/train_anomaly_score_model.py --help`).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="학습만 돌리고 joblib·manifest를 저장하지 않는다(점수 분포만 확인할 때).",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ARTIFACT_DIR,
        help=f"저장 위치(기본값: {ARTIFACT_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    """실제 실행 진입점. `if __name__ == "__main__":`에서 호출된다.

    반환값(0)은 "정상 종료"를 뜻하는 관례적인 종료 코드다 — 이 스크립트가 셸
    스크립트나 CI 파이프라인에서 호출될 경우, 0이 아닌 값을 반환하면 "실패"로
    간주되게 하기 위한 관례다(지금은 실패 분기가 없어 항상 0을 반환한다).
    """
    args = _parse_args()

    # `with ... as connection:` 블록을 벗어나는 순간 커넥션이 자동으로 반납된다
    # (직접 close()를 호출하지 않아도 된다) — 파이썬의 컨텍스트 매니저 관례다.
    with engine.connect() as connection:
        # 코드 리뷰 권고4: engine이 쓰기 권한이 있는 계정(kosa)이라도, 이
        # 트랜잭션 안에서는 DB 자체가 쓰기 문을 거부하게 만든다. 이 스크립트가
        # 실제로 SELECT만 한다는 사실과 무관하게, "실수로 쓰기 코드가 섞여도
        # 실행 시점에 막힌다"는 안전장치를 되찾기 위해서다 — readonly_engine을
        # 못 쓰는 상황에서의 대체 수단이다(계정 자체의 권한 제한만큼 강하지는
        # 않다 — 트랜잭션이 끝나면 이 제한도 끝나므로, 다음 커넥션·다음 실행이
        # 자동으로 보호되는 건 아니다).
        connection.execute(text("SET TRANSACTION READ ONLY"))
        manifest, forest, report = train(connection)

    if not args.dry_run:
        save_artifacts(manifest, forest, args.artifact_dir)
        saved_to = args.artifact_dir / f"{manifest.model_version}.joblib"
        print(f"[train_anomaly_score_model] 저장 완료: {saved_to}")
    else:
        print("[train_anomaly_score_model] --dry-run — 저장하지 않음")

    # 아래 세 줄은 학습이 "말이 되게" 됐는지 사람이 눈으로 확인하기 위한
    # 요약이다. 예를 들어 train과 test의 점수 분포(mean·p95)가 서로 크게
    # 다르면 뭔가 잘못됐다는 신호일 수 있다(정상이라면 비슷한 범위여야 한다).
    print(
        f"wafers={report['wafer_count']} feature_dim={report['feature_dim']} "
        f"train_lots={manifest.train_lot_count} test_lots={manifest.test_lot_count}"
    )
    print(f"display_threshold={manifest.display_threshold:.4f}")
    print(
        f"train score mean={report['train_score_mean']:.4f} "
        f"p95={report['train_score_p95']:.4f} | "
        f"test score mean={report['test_score_mean']:.4f} "
        f"p95={report['test_score_p95']:.4f}"
    )
    return 0


if __name__ == "__main__":
    # main()의 반환값을 그대로 프로세스 종료 코드로 쓴다 — `raise SystemExit(n)`은
    # "프로그램을 종료 코드 n으로 끝낸다"는 뜻의 파이썬 관용구다.
    raise SystemExit(main())
