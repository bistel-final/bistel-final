"""V5-A-3.2-1 `get_fdc_summary(lot_hist_id)` Tool 단위 테스트.

DB·model artifact 의존성은 모두 fake/monkeypatch로 대체한다 — 실제 Postgres가
필요한 조회 SQL 자체(`repository.fetch_wafer_lot_context`·
`fetch_wafer_parameter_rows`)의 검증은 이 파일의 책임이 아니다(다른 detection
repository 함수들과 마찬가지로 이 모듈도 container/integration 마커 테스트의
범위다). 여기서는 세 계층의 계약을 검증한다.
  1) `model_artifact.load_latest_model` — artifact 없음/손상/정상 3갈래.
  2) `service._build_anomaly_signal`·`FdcSummaryService` — group feature 조립·
     채점 파이프라인 재사용·anomaly 실패 흡수·NOT_FOUND 분기.
  3) `tools.get_fdc_summary` — 공통 ok/reason 계약(TIMEOUT·DEPENDENCY_ERROR·
     NOT_FOUND·성공) wiring.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.common.enums import AlarmType, ThresholdValidationStatus
from app.common.tool_contracts import (
    FdcSummaryToolResult,
    ParameterSummaryItem,
    WaferContext,
)
from app.detection import model_artifact, repository
from app.detection.model import ModelManifest, Normalizer, ScoreScaling
from app.detection.model_artifact import LoadedModel
from app.detection.service import FdcSummaryService, _build_anomaly_signal
from app.detection.tools import get_fdc_summary as get_fdc_summary_tool

WAFER_ROW = repository.WaferLotContextRow(
    lot_hist_id="LH-00181",
    lot_id="LOT004",
    wafer_no=6,
    chamber_id="EQP04-PM2",
    equipment_id="EQP04",
    step_id="CT-ETCH",
    recipe_id="RECIPE02",
)


def _parameter_row(**overrides: object) -> repository.WaferParameterRow:
    payload: dict[str, object] = {
        "parameter_id": "P1",
        "parameter_name": "Focus Offset",
        "unit": "nm",
        "recipe_step_no": 1,
        "value_mean": 100.0,
        "value_std": 0.0,
        "value_min": 100.0,
        "value_max": 100.0,
        "point_cnt": 3,
        "ooc_point_cnt": 0,
        "oos_point_cnt": 0,
        "alarm_type": AlarmType.IN,
        "spec_lower": 90.0,
        "ctrl_lower": 95.0,
        "target": 100.0,
        "ctrl_upper": 105.0,
        "spec_upper": 110.0,
        "upper_only": False,
    }
    payload.update(overrides)
    return repository.WaferParameterRow(**payload)


def _manifest(**overrides: object) -> ModelManifest:
    payload: dict[str, object] = {
        "model_version": "IFOREST-TEST",
        "score_method": "MINMAX-V1",
        "random_seed": 42,
        "feature_names": (
            "P1__step1__mean_dist",
            "P1__step1__std",
            "P1__step1__ooc_ratio",
            "P1__step1__oos_ratio",
            "P1__step1__coverage",
        ),
        "normalizer": Normalizer(mean=(0.0,) * 5, std=(1.0,) * 5),
        "scaling": ScoreScaling(raw_min=0.0, raw_max=1.0),
        "display_threshold": 0.5,
        "n_estimators": 200,
        "contamination": "auto",
        "train_lot_count": 4,
        "test_lot_count": 1,
        "train_wafer_count": 20,
        "expected_point_counts": (("P1", 1, 3),),
        "sklearn_version": "1.5.2",
        "numpy_version": "2.1.3",
    }
    payload.update(overrides)
    return ModelManifest(**payload)


class _FakeForest:
    """`score_samples`가 항상 -0.3을 돌려주는 IsolationForest 대역."""

    def __init__(self, value: float = -0.3, *, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    def score_samples(self, matrix: np.ndarray) -> np.ndarray:
        if self._error is not None:
            raise self._error
        return np.full(matrix.shape[0], self._value)


class _CapturingForest:
    """`score_samples`에 실제로 들어온 feature 행렬을 그대로 저장해 두는 대역.

    _center()가 target을 실제로 쓰는지(리뷰 V5-A-3.2-1 필수 2)는 최종 anomaly
    score만 봐서는 정규화·scaling을 거치며 가려질 수 있다 — 채점 파이프라인에
    실제로 들어간 원본 feature 값(mean_dist)을 직접 검사해 고정한다.
    """

    def __init__(self, value: float = -0.3) -> None:
        self._value = value
        self.received_matrix: np.ndarray | None = None

    def score_samples(self, matrix: np.ndarray) -> np.ndarray:
        self.received_matrix = matrix
        return np.full(matrix.shape[0], self._value)


# ---------------------------------------------------------------------
# 1) model_artifact.load_latest_model
# ---------------------------------------------------------------------
def _manifest_json_payload(model_version: str = "IFOREST-TEST") -> dict[str, Any]:
    return {
        "model_version": model_version,
        "score_method": "MINMAX-V1",
        "random_seed": 42,
        "feature_names": ["P1__step1__mean_dist"],
        "normalizer": {"mean": [0.0], "std": [1.0]},
        "scaling": {"raw_min": 0.0, "raw_max": 1.0},
        "display_threshold": 0.5,
        "n_estimators": 200,
        "contamination": "auto",
        "train_lot_count": 4,
        "test_lot_count": 1,
        "train_wafer_count": 20,
        "expected_point_counts": {"P1__step1": 3},
        "sklearn_version": "1.5.2",
        "numpy_version": "2.1.3",
        "generated_at": "2026-08-20T00:00:00+00:00",
    }


class TestLoadLatestModel:
    def test_returns_none_when_directory_missing(self, tmp_path: Path) -> None:
        assert model_artifact.load_latest_model(tmp_path / "missing") is None

    def test_returns_none_when_no_manifest_files(self, tmp_path: Path) -> None:
        assert model_artifact.load_latest_model(tmp_path) is None

    def test_returns_none_when_joblib_missing(self, tmp_path: Path) -> None:
        (tmp_path / "IFOREST-TEST.manifest.json").write_text(
            json.dumps(_manifest_json_payload()), encoding="utf-8"
        )
        assert model_artifact.load_latest_model(tmp_path) is None

    def test_returns_none_when_manifest_is_malformed(self, tmp_path: Path) -> None:
        (tmp_path / "IFOREST-TEST.manifest.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        (tmp_path / "IFOREST-TEST.joblib").write_bytes(b"not a real joblib file")
        assert model_artifact.load_latest_model(tmp_path) is None

    def test_loads_matching_manifest_and_joblib_pair(self, tmp_path: Path) -> None:
        import joblib

        (tmp_path / "IFOREST-TEST.manifest.json").write_text(
            json.dumps(_manifest_json_payload()), encoding="utf-8"
        )
        joblib.dump({"kind": "fake-forest"}, tmp_path / "IFOREST-TEST.joblib")

        loaded = model_artifact.load_latest_model(tmp_path)

        assert loaded is not None
        assert loaded.manifest.model_version == "IFOREST-TEST"
        assert loaded.manifest.expected_point_counts == (("P1", 1, 3),)
        assert loaded.manifest.normalizer.mean == (0.0,)
        assert loaded.forest == {"kind": "fake-forest"}

    def test_picks_most_recently_modified_manifest(self, tmp_path: Path) -> None:
        import os
        import time

        import joblib

        older = _manifest_json_payload("IFOREST-OLD")
        newer = _manifest_json_payload("IFOREST-NEW")
        (tmp_path / "IFOREST-OLD.manifest.json").write_text(
            json.dumps(older), encoding="utf-8"
        )
        joblib.dump({"v": "old"}, tmp_path / "IFOREST-OLD.joblib")
        time.sleep(0.01)
        (tmp_path / "IFOREST-NEW.manifest.json").write_text(
            json.dumps(newer), encoding="utf-8"
        )
        joblib.dump({"v": "new"}, tmp_path / "IFOREST-NEW.joblib")
        # 파일시스템 mtime 해상도가 낮은 환경 대비 명시적으로 시차를 준다.
        newer_path = tmp_path / "IFOREST-NEW.manifest.json"
        os.utime(newer_path, None)

        loaded = model_artifact.load_latest_model(tmp_path)

        assert loaded is not None
        assert loaded.manifest.model_version == "IFOREST-NEW"


# ---------------------------------------------------------------------
# 2) service._build_anomaly_signal / FdcSummaryService
# ---------------------------------------------------------------------
class TestBuildAnomalySignal:
    def test_scores_single_wafer_with_identity_normalizer(self) -> None:
        loaded = LoadedModel(manifest=_manifest(), forest=_FakeForest(-0.3))

        signal = _build_anomaly_signal(
            loaded, "LH-00181", "LOT004", [_parameter_row()]
        )

        assert signal is not None
        assert signal.score == pytest.approx(0.3)
        assert signal.model_version == "IFOREST-TEST"
        assert signal.display_threshold == 0.5
        assert signal.is_anomaly is False
        assert (
            signal.threshold_validation_status == ThresholdValidationStatus.UNVERIFIED
        )

    def test_skips_parameter_rows_with_missing_value_mean(self) -> None:
        loaded = LoadedModel(manifest=_manifest(), forest=_FakeForest(-0.3))
        rows = [
            _parameter_row(),
            _parameter_row(recipe_step_no=2, value_mean=None, value_std=None),
        ]

        signal = _build_anomaly_signal(loaded, "LH-00181", "LOT004", rows)

        # step2 행은 feature_names에도 없고 value_mean=None이라 건너뛰지만,
        # 살아남은 step1 행만으로도 채점은 그대로 성공해야 한다.
        assert signal is not None
        assert signal.score == pytest.approx(0.3)

    def test_returns_none_when_every_row_is_missing_value_mean(self) -> None:
        loaded = LoadedModel(manifest=_manifest(), forest=_FakeForest(-0.3))
        rows = [_parameter_row(value_mean=None, value_std=None)]

        assert _build_anomaly_signal(loaded, "LH-00181", "LOT004", rows) is None

    def test_uses_target_as_center_not_spec_midpoint(self) -> None:
        """리뷰 V5-A-3.2-1 필수 2 회귀.

        `parameters[].target`(응답에 그대로 노출되는 `row.target`)과
        `anomaly.score`의 계산 근거가 서로 다른 중심을 쓰던 불일치를 없앴는지
        확인한다. target(100)과 spec 중앙((90+130)/2=110)을 일부러 다르게 잡아,
        target이 실제로 쓰이지 않았다면(=옛 spec 중앙 기준이었다면) 이 assert가
        실패하도록 만든다.
        """
        forest = _CapturingForest()
        loaded = LoadedModel(manifest=_manifest(), forest=forest)
        row = _parameter_row(
            value_mean=100.0, target=100.0, spec_lower=90.0, spec_upper=130.0
        )

        _build_anomaly_signal(loaded, "LH-00181", "LOT004", [row])

        assert forest.received_matrix is not None
        # feature_names[0]는 "P1__step1__mean_dist" — target(100) 우선이면
        # (value_mean-target)/spec_range = (100-100)/40 = 0.0.
        # spec 중앙(110) 기준이었다면 (100-110)/40 = -0.25가 나왔을 것이다.
        assert forest.received_matrix[0][0] == pytest.approx(0.0)


class TestFdcSummaryService:
    def _install_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        lot_row: repository.WaferLotContextRow | None,
        parameter_rows: list[repository.WaferParameterRow],
    ) -> None:
        monkeypatch.setattr(
            "app.detection.service.repository.fetch_wafer_lot_context",
            lambda connection, lot_hist_id: lot_row,
        )
        monkeypatch.setattr(
            "app.detection.service.repository.fetch_wafer_parameter_rows",
            lambda connection, lot_hist_id: parameter_rows,
        )

    def test_returns_none_when_wafer_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_repository(monkeypatch, lot_row=None, parameter_rows=[])

        service = FdcSummaryService(connection=object(), model_loader=lambda: None)

        assert service.get_fdc_summary("missing") is None

    def test_returns_none_when_parameter_rows_are_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_repository(
            monkeypatch, lot_row=WAFER_ROW, parameter_rows=[]
        )

        service = FdcSummaryService(connection=object(), model_loader=lambda: None)

        assert service.get_fdc_summary("LH-00181") is None

    def test_succeeds_without_anomaly_when_model_artifact_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_repository(
            monkeypatch, lot_row=WAFER_ROW, parameter_rows=[_parameter_row()]
        )

        service = FdcSummaryService(connection=object(), model_loader=lambda: None)
        result = service.get_fdc_summary("LH-00181")

        assert result is not None
        assert isinstance(result, FdcSummaryToolResult)
        assert result.ok is True
        assert result.reason == ""
        assert result.anomaly is None
        assert result.wafer == WaferContext(
            lot_hist_id="LH-00181",
            lot_id="LOT004",
            wafer_no=6,
            chamber_id="EQP04-PM2",
            equipment_id="EQP04",
            step_id="CT-ETCH",
            recipe_id="RECIPE02",
        )
        assert result.parameters == [
            ParameterSummaryItem(
                parameter_id="P1",
                parameter_name="Focus Offset",
                unit="nm",
                recipe_step_no=1,
                recipe_step_name=None,
                value_mean=100.0,
                value_std=0.0,
                value_min=100.0,
                value_max=100.0,
                point_cnt=3,
                ooc_point_cnt=0,
                oos_point_cnt=0,
                spec_lower=90.0,
                ctrl_lower=95.0,
                target=100.0,
                ctrl_upper=105.0,
                spec_upper=110.0,
                alarm_type=AlarmType.IN,
            )
        ]

    def test_succeeds_with_anomaly_when_model_artifact_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_repository(
            monkeypatch, lot_row=WAFER_ROW, parameter_rows=[_parameter_row()]
        )
        loaded = LoadedModel(manifest=_manifest(), forest=_FakeForest(-0.3))

        service = FdcSummaryService(connection=object(), model_loader=lambda: loaded)
        result = service.get_fdc_summary("LH-00181")

        assert result is not None
        assert result.anomaly is not None
        assert result.anomaly.score == pytest.approx(0.3)

    def test_anomaly_scoring_failure_is_absorbed_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_repository(
            monkeypatch, lot_row=WAFER_ROW, parameter_rows=[_parameter_row()]
        )
        broken = LoadedModel(
            manifest=_manifest(),
            forest=_FakeForest(error=RuntimeError("corrupt model state")),
        )

        service = FdcSummaryService(connection=object(), model_loader=lambda: broken)
        result = service.get_fdc_summary("LH-00181")

        assert result is not None
        assert result.ok is True
        assert result.anomaly is None

    def test_model_loader_exception_is_absorbed_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_repository(
            monkeypatch, lot_row=WAFER_ROW, parameter_rows=[_parameter_row()]
        )

        def _raise() -> None:
            raise RuntimeError("artifact directory unreadable")

        service = FdcSummaryService(connection=object(), model_loader=_raise)
        result = service.get_fdc_summary("LH-00181")

        assert result is not None
        assert result.ok is True
        assert result.anomaly is None


# ---------------------------------------------------------------------
# 3) tools.get_fdc_summary — 공통 ok/reason 계약 wiring
# ---------------------------------------------------------------------
class _FakeConnection:
    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _FakeEngine:
    def connect(self) -> _FakeConnection:
        return _FakeConnection()


def _install_fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # tools.py가 module-level `readonly_engine` 대신 `get_readonly_engine()`
    # accessor를 쓰도록 바뀌었다(리뷰 V5-A-3.2-1 필수 1 — CM-3.5와의 import 충돌
    # 회피). 대역도 같은 이름을 patch해야 한다.
    monkeypatch.setattr(
        "app.detection.tools.get_readonly_engine", lambda: _FakeEngine()
    )


class TestGetFdcSummaryTool:
    def test_returns_common_success_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_engine(monkeypatch)
        expected = FdcSummaryToolResult(
            ok=True,
            wafer=WaferContext(**dataclasses.asdict(WAFER_ROW)),
            parameters=[
                ParameterSummaryItem(
                    parameter_id="P1",
                    parameter_name="Focus Offset",
                    recipe_step_no=1,
                    point_cnt=3,
                    ooc_point_cnt=0,
                    oos_point_cnt=0,
                    alarm_type=AlarmType.IN,
                )
            ],
        )

        class FakeService:
            def __init__(self, connection: object) -> None:
                self.connection = connection

            def get_fdc_summary(self, lot_hist_id: str) -> FdcSummaryToolResult:
                assert lot_hist_id == "LH-00181"
                return expected

        monkeypatch.setattr("app.detection.tools.FdcSummaryService", FakeService)

        result = get_fdc_summary_tool.invoke({"lot_hist_id": "LH-00181"})

        assert result == expected

    def test_returns_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_engine(monkeypatch)

        class FakeService:
            def __init__(self, connection: object) -> None:
                pass

            def get_fdc_summary(self, lot_hist_id: str) -> None:
                return None

        monkeypatch.setattr("app.detection.tools.FdcSummaryService", FakeService)

        result = get_fdc_summary_tool.invoke({"lot_hist_id": "missing"})

        assert result.ok is False
        assert result.reason == "NOT_FOUND: lot_hist_id=missing"

    def test_returns_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_engine(monkeypatch)

        class FakeService:
            def __init__(self, connection: object) -> None:
                pass

            def get_fdc_summary(self, lot_hist_id: str) -> None:
                raise TimeoutError("db read timed out")

        monkeypatch.setattr("app.detection.tools.FdcSummaryService", FakeService)

        result = get_fdc_summary_tool.invoke({"lot_hist_id": "LH-00181"})

        assert result.ok is False
        assert result.reason.startswith("TIMEOUT:")

    def test_returns_dependency_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_engine(monkeypatch)

        class FakeService:
            def __init__(self, connection: object) -> None:
                pass

            def get_fdc_summary(self, lot_hist_id: str) -> None:
                raise RuntimeError("connection refused")

        monkeypatch.setattr("app.detection.tools.FdcSummaryService", FakeService)

        result = get_fdc_summary_tool.invoke({"lot_hist_id": "LH-00181"})

        assert result.ok is False
        assert result.reason == "DEPENDENCY_ERROR: FDC summary 조회 의존성 오류"
