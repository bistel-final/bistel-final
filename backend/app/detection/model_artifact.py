"""anomaly score 모델 artifact 로딩 (V5-A-3.2-1 `get_fdc_summary` Tool 전용).

`model.py`는 순수 계산만 하고 파일 IO를 하지 않는다(그 모듈 docstring 1문단).
`scripts/train_anomaly_score_model.py`는 학습·저장(쓰기)을 담당한다.
`repository.py`는 PostgreSQL 조회만 담당한다는 계약이 있다(그 모듈 docstring
1절). 그래서 "이미 학습되어 저장된 artifact를 추론 시점에 읽기만" 하는 이
관심사는 이 셋 어디에도 속하지 않아 별도 모듈로 둔다.

핵심 계약: artifact가 아예 없거나(학습 스크립트를 한 번도 안 돌렸거나) 손상돼
있어도 이 모듈은 예외를 던지지 않고 `None`을 반환한다. `get_fdc_summary`는
score를 "준비된 경우에만" 반환하는 선택적 근거로 취급하므로(설계서 v2.1 4.4:
"artifact가 없어도 규칙 흐름은 정상 동작한다"), 이 모듈의 실패는 Tool 전체의
실패가 아니라 `anomaly=None`으로 흡수된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.detection.model import ModelManifest, Normalizer, ScoreScaling

__all__ = ["ARTIFACT_DIR", "LoadedModel", "load_latest_model"]

# scripts/train_anomaly_score_model.py의 ARTIFACT_DIR과 같은 물리 경로다.
# (`backend/artifacts/detection_model/`). 두 값이 갈라지면 학습 스크립트가
# 저장한 곳과 Tool이 읽는 곳이 달라지므로, 상수를 별도로 import하지 않고
# 여기서도 같은 상대 경로 계산식을 그대로 쓴다 — 스크립트 모듈을 앱 코드에서
# import하면(스크립트는 argparse 등 CLI 전용 코드를 최상위에 갖고 있어)
# 원치 않는 부작용이 생길 수 있어 피한다.
ARTIFACT_DIR = (
    Path(__file__).resolve().parent.parent.parent / "artifacts" / "detection_model"
)


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """추론에 필요한 학습 결과 한 벌. manifest(설정값)와 forest(모델 객체)."""

    manifest: ModelManifest
    forest: Any


def _manifest_from_json(data: dict[str, Any]) -> ModelManifest:
    """`train_anomaly_score_model._manifest_to_json`의 역변환.

    `expected_point_counts`는 저장 시 `f"{parameter_id}__step{step}"` 형태의
    평평한 dict로 풀렸었다(그 파일의 `_manifest_to_json` docstring 참고).
    parameter_id 자체에 `__step`이 포함될 일이 없으므로(dim_parameter 8개
    parameter_id 명명 규칙과 `_assert_feature_allowlist`가 이미 금지 토큰을
    걸러낸다) `rsplit("__step", 1)`로 안전하게 되돌릴 수 있다.
    """

    expected_point_counts = []
    for combo_key, cnt in data["expected_point_counts"].items():
        parameter_id, step_part = combo_key.rsplit("__step", 1)
        expected_point_counts.append((parameter_id, int(step_part), int(cnt)))

    return ModelManifest(
        model_version=data["model_version"],
        score_method=data["score_method"],
        random_seed=int(data["random_seed"]),
        feature_names=tuple(data["feature_names"]),
        normalizer=Normalizer(
            mean=tuple(data["normalizer"]["mean"]),
            std=tuple(data["normalizer"]["std"]),
        ),
        scaling=ScoreScaling(
            raw_min=float(data["scaling"]["raw_min"]),
            raw_max=float(data["scaling"]["raw_max"]),
        ),
        display_threshold=float(data["display_threshold"]),
        n_estimators=int(data["n_estimators"]),
        contamination=str(data["contamination"]),
        train_lot_count=int(data["train_lot_count"]),
        test_lot_count=int(data["test_lot_count"]),
        train_wafer_count=int(data["train_wafer_count"]),
        expected_point_counts=tuple(sorted(expected_point_counts)),
        sklearn_version=data["sklearn_version"],
        numpy_version=data["numpy_version"],
    )


def load_latest_model(artifact_dir: Path | None = None) -> LoadedModel | None:
    """`artifact_dir`(기본 `ARTIFACT_DIR`)에서 가장 최근 학습된 model+manifest
    쌍을 읽는다.

    "가장 최근"은 `.manifest.json` 파일의 수정 시각(mtime) 기준이다 — 여러
    `model_version`이 쌓여 있어도(재학습을 여러 번 돌렸어도) 가장 최근 것
    하나만 쓴다. 다음 중 하나라도 해당하면 예외를 던지지 않고 `None`을
    반환한다(위 모듈 docstring의 핵심 계약):
      - `artifact_dir`가 아예 없다(학습 스크립트를 한 번도 안 돌렸다).
      - `*.manifest.json`이 하나도 없다.
      - manifest는 있는데 짝이 되는 `.joblib`이 없다.
      - manifest JSON 파싱·필드 검증에 실패했다(손상된 파일).
      - `.joblib` 로딩(`joblib.load`)에 실패했다(손상되거나 버전 불일치).
    """

    directory = artifact_dir or ARTIFACT_DIR
    if not directory.exists():
        return None

    manifest_paths = sorted(directory.glob("*.manifest.json"))
    if not manifest_paths:
        return None

    latest_manifest_path = max(manifest_paths, key=lambda path: path.stat().st_mtime)

    try:
        data = json.loads(latest_manifest_path.read_text(encoding="utf-8"))
        manifest = _manifest_from_json(data)

        joblib_path = directory / f"{manifest.model_version}.joblib"
        if not joblib_path.exists():
            return None

        # 무거운 의존성(joblib)은 실제로 로딩이 필요할 때만 import한다 —
        # model.py가 `to_anomaly_signal`에서 지연 import하는 것과 같은 이유다.
        import joblib

        forest = joblib.load(joblib_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None

    return LoadedModel(manifest=manifest, forest=forest)
