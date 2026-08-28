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

## 두 물리 DB의 dataset epoch 정합성 (코드 리뷰 필수 1)

예측은 `get_readonly_engine()`(kosa_readonly, fdc_final DB)의 `lot_history`에서
만들고, 라벨은 `get_evaluation_engine()`(kosa_evaluation, kosa_text2sql DB)의
`lot_history.fault_code`에서 읽어 `lot_hist_id`로 join한다. 이 join은 두 DB가
같은 dataset epoch(현재 `fdc_final_20260818`)의 사본이라는 전제 위에 있는데,
스크립트 어디에도 이 전제를 확인하는 코드가 없었다. 공용 서버에서 한쪽 DB만
재적재되는 사고는 가상이 아니다 — Neo4j에서 실제로 있었던 일이고, CM-3.5
마커가 DB별 `row_fingerprint_sha256`을 남기는 이유도 이것이다. epoch이 갈린
상태에서 id 체계가 우연히 유지되면 틀린 라벨과 조용히 join되고, 결과
artifact는 "재현 가능"한 얼굴로 잘못된 confusion metric을 기록한다.

그래서 예측을 만들기 전에 두 connection에서 같은 기준값(`lot_history` 행 수 +
`lot_hist_id` min/max)을 읽어 비교하고, 불일치하면 즉시 실패한다
(`_verify_dataset_epoch_alignment`). 완전한 증명은 아니다 — 행 수·min/max까지
우연히 같은 재적재는 이론상 가능하며, 그런 경우까지 잡으려면 CM-3.5 마커의
`row_fingerprint_sha256` 재계산이 필요하다. 지금은 가장 흔한 사고(한쪽만
재적재됨)를 값싼 쿼리 두 줄로 잡는 첫 방어선이다.

## held-out LOT을 어떻게 다시 찾는가 (코드 리뷰 필수 2)

`ModelManifest`는 어느 LOT이 test였는지 literal 목록으로 저장하지 않는다
(`train_lot_count`·`test_lot_count` 정수만 저장한다 — `model.py`의
`ModelManifest` docstring 참고). 대신 `model.split_lots`를 학습 때와 같은
`manifest.random_seed`로 다시 호출해 결정론적으로 재구성한다
(`split_lots`의 재현성은 `tests/unit/test_detection_model.py::
test_reproducible_scores_end_to_end`가 이미 고정하고 있다).

**이 guard가 실제로 잡는 것은 개수 변화뿐이다.** 재구성한 held-out LOT
**개수**가 manifest에 저장된 `test_lot_count`와 다르면(학습 이후
`lot_history`가 바뀐 경우) 즉시 실패한다 — 하지만 LOT **구성**이 바뀌어도
총수가 그대로면(예: LOT007이 빠지고 LOT013이 새로 들어와도 12개 그대로)
개수만 보는 이 guard는 감지하지 못하고, 학습 때와 다른 LOT을 같은 이름의
holdout으로 평가해버린다. 그래서 재구성 시점의 train+test lot_id 정렬
목록의 SHA-256(`reconstructed_lot_id_hash`)을 함께 계산해 result artifact의
`split_manifest`에 남긴다 — 지금 당장 "몰래 통과"를 막지는 못해도, 이번
실행이 실제로 어떤 LOT들을 재구성했는지 사후 감사는 가능해진다. 다음
model_version부터 학습 시점에 이 hash를 manifest 자체에 저장해 재구성 시
대조하면, 이 guard가 LOT 구성 변경까지 실제로 강제할 수 있게 된다.

`fetch_lot_history_rows`는 `ORDER BY` 없이 `lot_history`를 읽지만(순서가
DB 조회마다 달라질 수 있다), 재구성 결과는 그래도 순서에 안정적이다 —
`split_lots`가 내부에서 `sorted(set(lot_ids))` 후에 셔플하므로(`model.py`
참고), 입력 목록의 순서가 아니라 lot_id 집합 자체만 결과를 결정한다.

## 같은 revision 재튜닝 금지 (WBS V5-A-2.4)

`artifacts/detection_eval/result_{model_version}.json`이 이미 있으면 이
스크립트는 평가 자체를 거부한다(`--dry-run`이면 이 검사를 건너뛴다 — 아무
것도 저장하지 않으므로 재튜닝 위험이 없다). 다시 평가하고 싶다면
`train_anomaly_score_model.py`로 `model_version`을 올려 새 candidate를 학습해야
한다 — 같은 revision을 결과가 마음에 들 때까지 반복 평가하는 것 자체가
여기서 금지하려는 "재튜닝"이다.

**`--dry-run`이 막지 못하는 것(코드 리뷰 권고 5)**: 이 검사는 파일 저장만
막는다 — `--dry-run`을 반복 실행해 콘솔에 찍히는 metric을 훔쳐보고 마음에
들 때만 `--dry-run` 없이 다시 실행해 저장하는 사용까지는 막지 못한다. 이건
사람의 규율 문제라 코드로 완전히 막을 수 없다 — 그래서 `--dry-run` 실행의
마지막 출력 줄에 경고를 남긴다.

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
import hashlib
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


def _fetch_lot_history_epoch_fingerprint(
    connection,
) -> tuple[int, str | None, str | None]:
    """`lot_history`의 행 수 + `lot_hist_id` min/max를 dataset epoch 정합성
    확인용 기준값으로 읽는다.

    COUNT/MIN/MAX만 쓰고 `fault_code` 등 라벨 column은 전혀 건드리지 않으므로,
    column 단위 권한이 서로 다른 두 role(kosa_readonly, kosa_evaluation)
    양쪽에서 안전하게 실행된다(`app/common/db.py`의 role 경계 참고).
    """

    row = connection.execute(
        text(
            "SELECT COUNT(*) AS cnt, MIN(lot_hist_id) AS min_id, "
            "MAX(lot_hist_id) AS max_id FROM lot_history"
        )
    ).one()
    return row.cnt, row.min_id, row.max_id


def _verify_dataset_epoch_alignment(readonly_connection, evaluation_connection) -> None:
    """예측을 만드는 fdc_final(kosa_readonly)과 라벨을 읽는 kosa_text2sql
    (kosa_evaluation)이 같은 dataset epoch의 사본인지 확인한다(코드 리뷰
    필수 1 — 모듈 docstring "두 물리 DB의 dataset epoch 정합성" 참고).

    `lot_history` 행 수 + `lot_hist_id` min/max가 다르면 두 DB가 서로 다른
    세대라는 뜻이므로, 예측을 만들기도 전에 즉시 실패한다 — `_reconstruct_
    test_lot_hist_ids`가 개수 불일치에서 즉시 실패하는 것과 같은 방식이다.
    """

    readonly_fingerprint = _fetch_lot_history_epoch_fingerprint(readonly_connection)
    evaluation_fingerprint = _fetch_lot_history_epoch_fingerprint(evaluation_connection)
    if readonly_fingerprint != evaluation_fingerprint:
        r_count, r_min, r_max = readonly_fingerprint
        e_count, e_min, e_max = evaluation_fingerprint
        raise RuntimeError(
            "dataset epoch 정합성 검증 실패 — get_readonly_engine()(fdc_final)과 "
            "get_evaluation_engine()(kosa_text2sql)의 lot_history가 서로 다른 "
            "세대로 보인다 "
            f"(fdc_final: count={r_count} min={r_min} max={r_max}, "
            f"kosa_text2sql: count={e_count} min={e_min} max={e_max}). "
            "두 DB가 같은 dataset epoch의 사본인지 확인한 뒤 다시 실행한다."
        )


def _hash_lot_id_list(train_lots: list[str], test_lots: list[str]) -> str:
    """재구성한 train+test lot_id 정렬 목록의 canonical SHA-256(코드 리뷰
    필수 2).

    `freeze_predictions`(evaluation.py)와 같은 canonical-JSON 패턴을 쓴다 —
    key 오름차순·UTF-8·공백 없는 JSON. "재구성한 held-out LOT **개수**만
    같으면 통과"하는 지금 guard의 약점(LOT 구성이 바뀌어도 총수가 그대로면
    감지하지 못한다)을 완전히 막지는 못하지만, 이 값을 result artifact에
    남겨두면 최소한 사후 감사는 가능하다.
    """

    payload = {"train": sorted(train_lots), "test": sorted(test_lots)}
    serialized = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def _reconstruct_test_lot_hist_ids(
    connection, manifest: anomaly_model.ModelManifest
) -> tuple[list[str], str]:
    """학습 때와 같은 seed로 held-out LOT을 재구성해 그 LOT에 속한
    `lot_hist_id` 목록과, 재구성한 train+test lot_id 목록의 감사용 hash를
    돌려준다(코드 리뷰 필수 2 — 모듈 docstring "held-out LOT을 어떻게 다시
    찾는가" 참고).

    `fetch_lot_history_rows`는 `fault_code`를 SELECT하지 않는 Runtime
    repository 함수다(`tests/unit/test_detection_model.py::
    test_repository_fetch_functions_never_touch_labels`가 고정한다) — 이
    함수 호출은 라벨 격리 경계를 넘지 않는다.
    """

    rows = fetch_lot_history_rows(connection)
    lot_ids = [row.lot_id for row in rows]
    train_lots, test_lots = anomaly_model.split_lots(lot_ids, seed=manifest.random_seed)
    if len(test_lots) != manifest.test_lot_count:
        raise RuntimeError(
            "재구성한 held-out LOT 수가 manifest와 다르다 "
            f"(manifest.test_lot_count={manifest.test_lot_count}, "
            f"재구성={len(test_lots)}) — 학습 이후 lot_history가 바뀐 것으로 "
            "보인다. 이 데이터셋으로는 학습 당시의 holdout을 재현할 수 없어 "
            "평가를 중단한다."
        )

    reconstructed_lot_id_hash = _hash_lot_id_list(train_lots, test_lots)
    test_lots_set = set(test_lots)
    test_lot_hist_ids = sorted(
        row.lot_hist_id for row in rows if row.lot_id in test_lots_set
    )
    return test_lot_hist_ids, reconstructed_lot_id_hash


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
        rows = evaluation_loader.fetch_synthetic_fault_labels(connection, lot_hist_ids)
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

        # dataset epoch 정합성 확인(코드 리뷰 필수 1) — fault_code·metrology는
        # 절대 읽지 않는다. COUNT(*)/MIN/MAX만 쓰는 이 확인용 connection은
        # 순서 계약(라벨은 predict_fn 이후에만 읽는다)과 무관하다 — 라벨을
        # 읽는 게 아니라 두 DB가 같은 세대인지만 확인한다.
        with get_evaluation_engine().connect() as epoch_check_connection:
            epoch_check_connection.execute(text("SET TRANSACTION READ ONLY"))
            _verify_dataset_epoch_alignment(connection, epoch_check_connection)

        test_lot_hist_ids, reconstructed_lot_id_hash = _reconstruct_test_lot_hist_ids(
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
        "reconstructed_lot_id_hash": reconstructed_lot_id_hash,
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
        f"predicted_anomaly_count={artifact.metric.predicted_anomaly_count}"
        f"/{artifact.fault_label_distribution.holdout_wafer_count} (holdout 전체 기준)"
    )
    print(
        "fault_label_distribution="
        f"{dict(artifact.fault_label_distribution.counts)} "
        f"(labeled {artifact.fault_label_distribution.labeled_wafer_count}/"
        f"{artifact.fault_label_distribution.holdout_wafer_count})"
    )

    if args.dry_run:
        print(
            "[evaluate_detection_holdout] --dry-run — 저장하지 않음. 경고: 이 "
            "출력의 수치를 근거로 같은 데이터로 재학습·재튜닝한 뒤 다시 평가하면 "
            "같은 revision 재튜닝 금지(WBS V5-A-2.4)를 위반하는 것과 같은 "
            "효과를 낸다 — dry-run 반복 관찰 자체가 그 규율을 코드로 막을 수는 "
            "없다(모듈 docstring 참고)."
        )
        return 0

    args.result_dir.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[evaluate_detection_holdout] 저장 완료: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
