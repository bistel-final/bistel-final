"""V5-A-2.4 Detection 합성 holdout 평가 runner (FR-A-08, FR-A-09).

설계서 v2.1 4.5 "공개 합성 라벨 평가"의 5단계를 그대로 실행하는 조립
스크립트다. 계산 로직 자체는 `app/detection/evaluation.py`(순수 함수)가
갖고 있고, 이 스크립트는 DB 조회와 이어붙이는 역할만 한다 —
`train_anomaly_score_model.py`가 `model.py`의 순수 계산과 DB를 이어붙이는
것과 같은 분업이다.

~~~text
1. feature·model·prompt 입력을 구성한다.
     -> model_artifact.load_latest_model()로 이미 학습된 candidate를 읽는다.
        이 스크립트는 다시 학습하지 않는다 — "후보를 라벨을 읽기 전에
        고정한다"는 계약은 "이미 저장된 model_version을 그대로 쓰고, 이
        실행 도중에는 재학습·재튜닝하지 않는다"로 만족한다.
2. prediction 결과와 hash를 먼저 고정한다.
     -> evaluation.run_holdout_evaluation()의 predict_fn 단계.
        predict_fn은 `app.detection.service.FdcSummaryService`(V5-A-3.2-1
        Tool과 완전히 같은 채점 경로)를 그대로 재사용한다 — 평가와 운영이
        서로 다른 계산을 쓰면 "재현 가능한 score"라는 V5-A-2.1 완료 기준과
        어긋난다.
3. 평가 전용 role로 fault_code·metrology를 읽는다.
     -> get_evaluation_engine()(kosa_evaluation, kosa_text2sql)으로만
        연결한다. 이 스크립트 안에서 `get_readonly_engine()`(kosa_readonly,
        fdc_final DB)은 예측을 만드는 2단계에만 쓰고, 라벨을 읽는 이
        단계에는 쓰지 않는다.
4. 고정 prediction과 label을 join한다.
     -> run_holdout_evaluation() 내부에서 fetch_labels_fn·fetch_metrology_fn이
        frozen(고정된 예측)을 받아 join한다.
5. synthetic 결과임을 명시한 metric artifact를 생성한다.
     -> evaluation.artifact_to_json_dict()의 결과를 JSON으로 저장한다.
~~~

## held-out LOT을 어떻게 다시 찾는가

`ModelManifest`는 어느 LOT이 test였는지 literal 목록으로 저장하지 않는다
(`train_lot_count`·`test_lot_count` 정수만 저장한다 — `model.py`의
`ModelManifest` docstring 참고). 대신 `model.split_lots`를 학습 때와 같은
`manifest.random_seed`로 다시 호출해 결정론적으로 재구성한다
(`split_lots`의 재현성은 `tests/unit/test_detection_model.py::
test_reproducible_scores_end_to_end`가 이미 고정하고 있다). 재구성한
test LOT 수가 manifest에 저장된 `test_lot_count`와 다르면(학습 이후
`lot_history`가 바뀐 경우) 조용히 다른 holdout을 평가하지 않고 즉시 실패한다.

## 같은 revision 재튜닝 금지 (WBS V5-A-2.4)

`artifacts/detection_eval/result_{model_version}.json`이 이미 있으면 이
스크립트는 평가 자체를 거부한다(`--dry-run`이면 이 검사를 건너뛴다 — 아무
것도 저장하지 않으므로 재튜닝 위험이 없다). 다시 평가하고 싶다면
`train_anomaly_score_model.py`로 `model_version`을 올려 새 candidate를 학습해야
한다 — 같은 revision을 결과가 마음에 들 때까지 반복 평가하는 것 자체가
여기서 금지하려는 "재튜닝"이다.

사용 예 (backend/ 에서, `READONLY_PASSWORD`·`EVALUATION_DB_PASSWORD` 필요 —
각각 kosa_readonly·kosa_evaluation role):

    cd backend
    python scripts/evaluate_detection_holdout.py
    # 저장하지 않고 metric만 미리 보고 싶다면:
    python scripts/evaluate_detection_holdout.py --dry-run

결과: `backend/artifacts/detection_eval/result_{model_version}.json`
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.common.db import get_evaluation_engine, get_readonly_engine  # noqa: E402
from app.detection import evaluation, evaluation_loader, model_artifact  # noqa: E402
from app.detection import model as anomaly_model  # noqa: E402
from app.detection.repository import fetch_lot_history_rows  # noqa: E402
from app.detection.service import FdcSummaryService  # noqa: E402

RESULT_DIR = BACKEND_ROOT / "artifacts" / "detection_eval"


def _reconstruct_test_lot_hist_ids(
    connection, manifest: anomaly_model.ModelManifest
) -> list[str]:
    """학습 때와 같은 seed로 held-out LOT을 재구성해 그 LOT에 속한
    `lot_hist_id` 목록을 돌려준다.

    `fetch_lot_history_rows`는 `fault_code`를 SELECT하지 않는 Runtime
    repository 함수다(`tests/unit/test_detection_model.py::
    test_repository_fetch_functions_never_touch_labels`가 고정한다) — 이
    함수 호출은 라벨 격리 경계를 넘지 않는다.
    """

    rows = fetch_lot_history_rows(connection)
    lot_ids = [row.lot_id for row in rows]
    _train_lots, test_lots = anomaly_model.split_lots(
        lot_ids, seed=manifest.random_seed
    )
    if len(test_lots) != manifest.test_lot_count:
        raise RuntimeError(
            "재구성한 held-out LOT 수가 manifest와 다르다 "
            f"(manifest.test_lot_count={manifest.test_lot_count}, "
            f"재구성={len(test_lots)}) — 학습 이후 lot_history가 바뀐 것으로 "
            "보인다. 이 데이터셋으로는 학습 당시의 holdout을 재현할 수 없어 "
            "평가를 중단한다."
        )

    test_lots_set = set(test_lots)
    return sorted(row.lot_hist_id for row in rows if row.lot_id in test_lots_set)


def _predict_holdout(
    connection,
    loaded: model_artifact.LoadedModel,
    test_lot_hist_ids: list[str],
) -> list[evaluation.PredictionRecord]:
    """held-out wafer마다 `FdcSummaryService`(V5-A-3.2-1 Tool과 같은 채점
    경로)를 호출해 예측을 만든다.

    `model_loader`에 이미 로딩된 `loaded`를 그대로 돌려주는 람다를 넘긴다 —
    수백 건을 채점하는 동안 매번 artifact 파일을 다시 읽지 않기 위해서다
    (`FdcSummaryService.__init__`의 `model_loader` 매개변수 계약).
    """

    service = FdcSummaryService(connection, model_loader=lambda: loaded)

    predictions: list[evaluation.PredictionRecord] = []
    skipped = 0
    for lot_hist_id in test_lot_hist_ids:
        result = service.get_fdc_summary(lot_hist_id)
        if result is None or result.anomaly is None:
            # wafer 자체가 없거나(있을 수 없다 — lot_history에서 뽑은
            # id다), summary·evaluation 행이 0건이거나, 채점에 필요한
            # group feature가 하나도 안 남은 경우다. 셋 다 "이 wafer는
            # 채점하지 못했다"는 같은 결론이라 개별 사유를 구분하지 않는다.
            skipped += 1
            continue
        predictions.append(
            evaluation.PredictionRecord(
                lot_hist_id=result.wafer.lot_hist_id,
                lot_id=result.wafer.lot_id,
                score=result.anomaly.score,
                is_anomaly=result.anomaly.is_anomaly,
            )
        )

    if skipped:
        print(
            f"[evaluate_detection_holdout] 채점 실패/스킵: {skipped}건 "
            f"(대상 {len(test_lot_hist_ids)}건 중)",
            file=sys.stderr,
        )
    return predictions


def _fetch_labels(
    frozen: evaluation.FrozenPredictions,
) -> list[evaluation.FaultLabelRow]:
    """평가 전용 engine(kosa_evaluation, kosa_text2sql)으로만 연결한다 —
    `get_readonly_engine()`(fdc_final DB)은 이 함수 안에서 아예 열지 않는다.
    """

    lot_hist_ids = [record.lot_hist_id for record in frozen.records]
    with get_evaluation_engine().connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        rows = evaluation_loader.fetch_synthetic_fault_labels(
            connection, lot_hist_ids
        )
    return [
        evaluation.FaultLabelRow(
            lot_hist_id=row.lot_hist_id, lot_id=row.lot_id, fault_code=row.fault_code
        )
        for row in rows
    ]


def _fetch_metrology(
    frozen: evaluation.FrozenPredictions,
) -> list[evaluation.MetrologyRow]:
    lot_hist_ids = [record.lot_hist_id for record in frozen.records]
    with get_evaluation_engine().connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        rows = evaluation_loader.fetch_metrology_outcomes(connection, lot_hist_ids)
    return [
        evaluation.MetrologyRow(
            lot_hist_id=row.lot_hist_id, alarm_result=row.alarm_result
        )
        for row in rows
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "평가만 돌리고 결과를 저장하지 않는다(같은 revision 재튜닝 금지 "
            "검사도 함께 건너뛴다 — 아무 것도 저장하지 않으므로 재튜닝 "
            "위험이 없다)."
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=model_artifact.ARTIFACT_DIR,
        help=f"학습 artifact를 읽을 위치(기본값: {model_artifact.ARTIFACT_DIR})",
    )
    parser.add_argument(
        "--result-dir",
        type=Path,
        default=RESULT_DIR,
        help=f"평가 결과를 저장할 위치(기본값: {RESULT_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    loaded = model_artifact.load_latest_model(args.artifact_dir)
    if loaded is None:
        print(
            "[evaluate_detection_holdout] 학습된 model artifact가 없다 — "
            "먼저 scripts/train_anomaly_score_model.py를 실행한다",
            file=sys.stderr,
        )
        return 1

    result_path = args.result_dir / f"result_{loaded.manifest.model_version}.json"
    if not args.dry_run and result_path.exists():
        print(
            f"[evaluate_detection_holdout] 이미 평가된 revision이다: "
            f"{result_path} — 같은 revision 재튜닝·재평가는 금지된다(WBS "
            "V5-A-2.4). 다시 평가하려면 model_version을 올려 새로 학습한다"
            "(scripts/train_anomaly_score_model.py).",
            file=sys.stderr,
        )
        return 1

    # 2) prediction — fdc_final DB(kosa_readonly) 연결. `app/detection/
    # tools.py`의 운영 Tool(get_fdc_summary)이 FdcSummaryService를 여는
    # 것과 완전히 같은 연결 경로다 — 평가와 운영이 같은 채점 경로를 타야
    # "재현 가능한 score"라는 V5-A-2.1 완료 기준을 만족한다. fault_code·
    # metrology는 이 커넥션으로 절대 읽지 않는다(3) 단계가 별도 evaluation
    # engine을 연다). `get_readonly_engine()`은 import 시점이 아니라 첫
    # 사용 시점에 engine을 만드는 lazy 값이다(`get_evaluation_engine`과
    # 같은 패턴이라 모듈 top-level 상수로는 존재하지 않는다).
    with get_readonly_engine().connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        test_lot_hist_ids = _reconstruct_test_lot_hist_ids(
            connection, loaded.manifest
        )

        def predict_fn(
            _connection=connection,
            _loaded=loaded,
            _ids=test_lot_hist_ids,
        ) -> list[evaluation.PredictionRecord]:
            return _predict_holdout(_connection, _loaded, _ids)

        artifact = evaluation.run_holdout_evaluation(
            model_version=loaded.manifest.model_version,
            predict_fn=predict_fn,
            fetch_labels_fn=_fetch_labels,
            fetch_metrology_fn=_fetch_metrology,
            now_fn=lambda: datetime.now(UTC).isoformat(),
        )

    payload = evaluation.artifact_to_json_dict(artifact)
    # manifest 유래 split·feature 맥락 — evaluation.py는 순수 계산만 하므로
    # ModelManifest를 모른다(모듈 docstring 참고). 그래서 이 부가 맥락은
    # evaluation.py가 만든 payload 위에 이 스크립트가 덧붙인다.
    payload["split_manifest"] = {
        "random_seed": loaded.manifest.random_seed,
        "train_lot_count": loaded.manifest.train_lot_count,
        "test_lot_count": loaded.manifest.test_lot_count,
        "held_out_wafer_count": len(test_lot_hist_ids),
        "scored_wafer_count": artifact.metric.metrology_coverage_denominator,
    }
    payload["feature_names"] = list(loaded.manifest.feature_names)

    print(f"model_version={artifact.model_version}")
    print(f"prediction_hash={artifact.prediction_hash}")
    print(
        f"metrology coverage={artifact.metric.metrology_coverage_numerator}/"
        f"{artifact.metric.metrology_coverage_denominator} "
        f"(PASS {artifact.metric.metrology_pass_count} / "
        f"FAIL {artifact.metric.metrology_fail_count})"
    )
    print(f"precision={artifact.metric.precision} recall={artifact.metric.recall}")
    print(
        "fault_label_distribution="
        f"{dict(artifact.fault_label_distribution.counts)} "
        f"(labeled {artifact.fault_label_distribution.labeled_wafer_count}/"
        f"{artifact.fault_label_distribution.holdout_wafer_count})"
    )

    if args.dry_run:
        print("[evaluate_detection_holdout] --dry-run — 저장하지 않음")
        return 0

    args.result_dir.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[evaluate_detection_holdout] 저장 완료: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
