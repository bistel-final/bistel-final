import pytest
from pydantic import ValidationError

from app.common.enums import Judgement
from app.common.tool_contracts import (
    AGENT_TOOL_NAMES,
    REASON_PREFIXES,
    TOOL_RESULT_MODELS,
    AnalysisPlanToolInput,
    AnalysisPlanToolResult,
    DocumentHit,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    EquipmentNode,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
    MetricPlan,
    SendActionToolInput,
    SendActionToolResult,
    VisualizationPlan,
    WaferContext,
    fail,
)

WAFER = {
    "lot_hist_id": "LH-00101",
    "lot_id": "LOT-260007",
    "wafer_no": 5,
    "chamber_id": "PHO-01-C1",
    "equipment_id": "PHO-01",
    "step_id": "CT-PHOTO",
}

SENSOR = {
    "sensor_id": "PH_FOCUS",
    "sensor_name": "Focus Offset",
    "recipe_step_no": 1,
    "judgement": Judgement.OOS,
}


def _fdc_success(**overrides: object) -> FdcSummaryToolResult:
    payload = {
        "ok": True,
        "wafer": WaferContext(**WAFER),
        "sensors": [SENSOR],
        "anomaly_score": 0.71,
        "anomaly_threshold": 0.62,
        "is_anomaly": True,
    }
    payload.update(overrides)
    return FdcSummaryToolResult(**payload)


class TestSuccessContract:
    def test_success_reason_is_empty(self) -> None:
        result = _fdc_success(is_anomaly=True)

        assert result.ok is True
        assert result.reason == ""

    def test_success_with_nonempty_reason_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="빈 문자열"):
            DocumentSearchToolResult(ok=True, reason="조회 성공")

    def test_wafer_has_exactly_one_anomaly_score(self) -> None:
        result = _fdc_success(sensors=[SENSOR, {**SENSOR, "recipe_step_no": 2}])

        assert isinstance(result.anomaly_score, float)
        assert len(result.sensors) == 2


class TestSuccessRequiresResult:
    def test_empty_fdc_success_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="필수 값이 없습니다"):
            FdcSummaryToolResult(ok=True)

    def test_empty_analysis_plan_success_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="필수 값이 없습니다"):
            AnalysisPlanToolResult(ok=True)

    def test_empty_equipment_context_success_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="필수 값이 없습니다"):
            EquipmentContextToolResult(ok=True)

    @pytest.mark.parametrize(
        "missing",
        ["wafer", "sensors", "anomaly_score", "anomaly_threshold"],
    )
    def test_each_required_field_is_enforced(self, missing: str) -> None:
        empty = [] if missing == "sensors" else None

        with pytest.raises(ValidationError, match=missing):
            _fdc_success(**{missing: empty})

    def test_send_action_success_requires_sent_true(self) -> None:
        with pytest.raises(ValidationError, match="sent"):
            SendActionToolResult(ok=True, action_id="ACT-0001", sent=False)

    def test_send_action_success_requires_action_id(self) -> None:
        with pytest.raises(ValidationError, match="action_id"):
            SendActionToolResult(ok=True, sent=True)

    def test_send_action_success(self) -> None:
        result = SendActionToolResult(ok=True, action_id="ACT-0001", sent=True)

        assert result.sent is True

    def test_document_search_allows_zero_hits(self) -> None:
        # 검색 결과 0건은 오류가 아니다.
        assert DocumentSearchToolResult(ok=True).hits == []

    def test_success_rejects_empty_string_value(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisPlanToolResult(
                ok=True,
                sql="",
                metric=MetricPlan(type="count"),
                visualization=VisualizationPlan(chart_type="table"),
            )

    def test_success_rejects_whitespace_only_value(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisPlanToolResult(
                ok=True,
                sql="   ",
                metric=MetricPlan(type="count"),
                visualization=VisualizationPlan(chart_type="table"),
            )

    def test_send_action_success_rejects_blank_action_id(self) -> None:
        with pytest.raises(ValidationError):
            SendActionToolResult(ok=True, action_id="   ", sent=True)

    def test_send_action_success_rejects_empty_action_id(self) -> None:
        with pytest.raises(ValidationError):
            SendActionToolResult(ok=True, action_id="", sent=True)

    def test_analysis_plan_success(self) -> None:
        result = AnalysisPlanToolResult(
            ok=True,
            sql="SELECT count(*) FROM fdc_alarm",
            metric=MetricPlan(type="count"),
            visualization=VisualizationPlan(chart_type="table"),
        )

        assert result.group_by == []


class TestFailureContract:
    @pytest.mark.parametrize("prefix", REASON_PREFIXES)
    def test_allowed_prefixes(self, prefix: str) -> None:
        result = DocumentSearchToolResult(ok=False, reason=f"{prefix} 상세 사유")

        assert result.ok is False
        assert result.hits == []

    @pytest.mark.parametrize(
        "reason",
        ["알 수 없는 오류", "ERROR: 실패", "not_found: 없음", ""],
    )
    def test_rejected_prefixes(self, reason: str) -> None:
        with pytest.raises(ValidationError, match="접두어"):
            DocumentSearchToolResult(ok=False, reason=reason)

    def test_failure_object_field_must_be_none(self) -> None:
        with pytest.raises(ValidationError, match="None 이어야"):
            FdcSummaryToolResult(
                ok=False,
                reason="NOT_FOUND: 없는 lot_hist_id",
                wafer=WaferContext(**WAFER),
            )

    @pytest.mark.parametrize("field", ["anomaly_score", "anomaly_threshold"])
    def test_failure_numeric_field_must_be_none(self, field: str) -> None:
        with pytest.raises(ValidationError, match="None 이어야"):
            FdcSummaryToolResult(
                ok=False,
                reason="NOT_FOUND: 없음",
                **{field: 0.9},
            )

    @pytest.mark.parametrize("sql", ["", "   "])
    def test_failure_rejects_empty_string_instead_of_none(self, sql: str) -> None:
        # "" 는 falsy 지만 None 이 아니므로 거부한다.
        # 필드의 min_length 가 모델 validator 보다 먼저 걸리는 이중 방어다.
        with pytest.raises(ValidationError):
            AnalysisPlanToolResult(ok=False, reason="LLM_NOT_READY: 미준비", sql=sql)

    def test_failure_rejects_non_none_string_at_model_level(self) -> None:
        with pytest.raises(ValidationError, match="None 이어야"):
            AnalysisPlanToolResult(
                ok=False,
                reason="LLM_NOT_READY: 미준비",
                sql="SELECT 1",
            )

    def test_failure_list_field_must_be_empty(self) -> None:
        with pytest.raises(ValidationError, match="목록 필드는 비어야"):
            AnalysisPlanToolResult(
                ok=False,
                reason="POLICY_REJECTED: 쓰기 시도",
                group_by=["chamber_id"],
            )

    def test_failure_drops_action_id(self) -> None:
        # 호출자는 Input 과 agent_tool_call.input_json 으로 action_id 를 이미 안다.
        result = SendActionToolResult(ok=False, reason="TIMEOUT: n8n 응답 없음")

        assert result.action_id is None
        assert result.sent is False

    def test_failure_cannot_echo_action_id(self) -> None:
        with pytest.raises(ValidationError, match="None 이어야"):
            SendActionToolResult(
                ok=False,
                reason="TIMEOUT: n8n 응답 없음",
                action_id="ACT-0001",
            )

    def test_failure_cannot_report_sent(self) -> None:
        with pytest.raises(ValidationError, match="False 여야"):
            SendActionToolResult(
                ok=False,
                reason="TIMEOUT: n8n 응답 없음",
                sent=True,
            )

    def test_failure_cannot_report_is_anomaly(self) -> None:
        with pytest.raises(ValidationError, match="False 여야"):
            FdcSummaryToolResult(
                ok=False,
                reason="MODEL_NOT_READY: 모델 없음",
                is_anomaly=True,
            )

    def test_fail_helper_produces_empty_result(self) -> None:
        result = fail(FdcSummaryToolResult, "DEPENDENCY_ERROR: DB 연결 실패")

        assert result.ok is False
        assert result.wafer is None
        assert result.sensors == []


class TestNoRuntimeMetadata:
    @pytest.mark.parametrize("model", TOOL_RESULT_MODELS.values())
    def test_latency_and_status_are_not_in_result(self, model: type) -> None:
        fields = set(model.model_fields)

        assert "latency_ms" not in fields
        assert "status" not in fields
        assert "called_at" not in fields

    @pytest.mark.parametrize("model", TOOL_RESULT_MODELS.values())
    def test_extra_field_is_rejected(self, model: type) -> None:
        with pytest.raises(ValidationError):
            model(ok=False, reason="NOT_FOUND: 없음", latency_ms=12)


class TestInputBoundaries:
    @pytest.mark.parametrize("top_k", [1, 4, 10])
    def test_top_k_allowed_range(self, top_k: int) -> None:
        assert DocumentSearchToolInput(query="반사파", top_k=top_k).top_k == top_k

    @pytest.mark.parametrize("top_k", [0, 11, -1])
    def test_top_k_out_of_range(self, top_k: int) -> None:
        with pytest.raises(ValidationError):
            DocumentSearchToolInput(query="반사파", top_k=top_k)

    def test_top_k_defaults_to_four(self) -> None:
        assert DocumentSearchToolInput(query="반사파").top_k == 4

    @pytest.mark.parametrize(
        "model, field",
        [
            (FdcSummaryToolInput, "lot_hist_id"),
            (EquipmentContextToolInput, "chamber_id"),
        ],
    )
    def test_identifier_length(self, model: type, field: str) -> None:
        assert model(**{field: "A" * 20})

        with pytest.raises(ValidationError):
            model(**{field: ""})

        with pytest.raises(ValidationError):
            model(**{field: "A" * 21})

    @pytest.mark.parametrize(
        "model, field",
        [
            (FdcSummaryToolInput, "lot_hist_id"),
            (EquipmentContextToolInput, "chamber_id"),
        ],
    )
    def test_identifier_rejects_whitespace_only(self, model: type, field: str) -> None:
        with pytest.raises(ValidationError):
            model(**{field: "   "})

    def test_identifier_is_stripped(self) -> None:
        assert FdcSummaryToolInput(lot_hist_id="  LH-00101  ").lot_hist_id == "LH-00101"

    def test_query_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValidationError):
            DocumentSearchToolInput(query="   ")

    @pytest.mark.parametrize("length", [1, 1000])
    def test_question_allowed_length(self, length: int) -> None:
        assert AnalysisPlanToolInput(question="질" * length)

    @pytest.mark.parametrize("length", [0, 1001])
    def test_question_rejected_length(self, length: int) -> None:
        with pytest.raises(ValidationError):
            AnalysisPlanToolInput(question="질" * length)

    def test_send_action_requires_both_identifiers(self) -> None:
        assert SendActionToolInput(action_id="ACT-0001", agent_run_id="RUN-0001")

        with pytest.raises(ValidationError):
            SendActionToolInput(action_id="ACT-0001")


class TestToolOwnership:
    def test_analytics_tool_is_outside_agent_budget(self) -> None:
        assert "generate_analysis_plan" not in AGENT_TOOL_NAMES
        assert len(AGENT_TOOL_NAMES) == 4

    def test_all_five_tools_have_result_models(self) -> None:
        assert set(TOOL_RESULT_MODELS) == AGENT_TOOL_NAMES | {"generate_analysis_plan"}

    def test_analysis_plan_has_no_agent_budget_fields(self) -> None:
        fields = set(AnalysisPlanToolResult.model_fields)

        assert "agent_run_id" not in fields
        assert "tool_call_count" not in fields


class TestSharedDtoIdentifiers:
    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    @pytest.mark.parametrize(
        "field",
        ["lot_hist_id", "lot_id", "chamber_id", "equipment_id", "step_id"],
    )
    def test_wafer_context_rejects_blank_id(self, field: str, blank: str) -> None:
        with pytest.raises(ValidationError):
            WaferContext(**{**WAFER, field: blank})

    def test_wafer_context_strips_id(self) -> None:
        wafer = WaferContext(**{**WAFER, "lot_hist_id": "  LH-00101  "})

        assert wafer.lot_hist_id == "LH-00101"

    @pytest.mark.parametrize("field", ["equipment_id", "model_code", "area_id"])
    def test_equipment_node_rejects_blank_id(self, field: str) -> None:
        payload = {
            "equipment_id": "PHO-01",
            "equipment_name": "Photo Scanner #1",
            "model_code": "PH-9000",
            "area_id": "PHOTO",
        }

        with pytest.raises(ValidationError):
            EquipmentNode(**{**payload, field: "   "})

    @pytest.mark.parametrize("field", ["chunk_id", "document_id"])
    def test_document_hit_rejects_blank_id(self, field: str) -> None:
        payload = {
            "chunk_id": "CHK-1",
            "document_id": "DOC-SPEC-ET7500",
            "title": "ET-7500",
            "score": 0.8,
            "content": "본문",
        }

        with pytest.raises(ValidationError):
            DocumentHit(**{**payload, field: ""})

    def test_optional_id_rejects_blank_but_allows_none(self) -> None:
        assert WaferContext(**WAFER, recipe_id=None).recipe_id is None

        with pytest.raises(ValidationError):
            WaferContext(**WAFER, recipe_id="   ")

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_document_hit_rejects_blank_model_code(self, blank: str) -> None:
        payload = {
            "chunk_id": "CHK-1",
            "document_id": "DOC-SPEC-ET7500",
            "title": "ET-7500",
            "score": 0.8,
            "content": "본문",
        }

        with pytest.raises(ValidationError):
            DocumentHit(**payload, model_code=blank)

        assert DocumentHit(**payload, model_code=None).model_code is None

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_search_input_rejects_blank_model_code(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            DocumentSearchToolInput(query="반사파", model_code=blank)

    def test_search_input_strips_model_code(self) -> None:
        assert (
            DocumentSearchToolInput(query="반사파", model_code=" ET-7500 ").model_code
            == "ET-7500"
        )


class TestAnomalyFlagConsistency:
    def test_flag_matches_threshold(self) -> None:
        result = _fdc_success(
            anomaly_score=0.71, anomaly_threshold=0.62, is_anomaly=True
        )

        assert result.is_anomaly is True

    def test_boundary_equal_is_anomaly(self) -> None:
        # score == threshold 는 이상으로 판정한다.
        result = _fdc_success(
            anomaly_score=0.62, anomaly_threshold=0.62, is_anomaly=True
        )

        assert result.is_anomaly is True

    @pytest.mark.parametrize(
        "score, threshold, flag",
        [
            (0.71, 0.62, False),
            (0.10, 0.62, True),
            (0.62, 0.62, False),
        ],
    )
    def test_inconsistent_flag_is_rejected(
        self,
        score: float,
        threshold: float,
        flag: bool,
    ) -> None:
        with pytest.raises(ValidationError, match="일치하지 않습니다"):
            _fdc_success(
                anomaly_score=score,
                anomaly_threshold=threshold,
                is_anomaly=flag,
            )

    def test_failure_result_skips_consistency_check(self) -> None:
        result = fail(FdcSummaryToolResult, "MODEL_NOT_READY: 모델 없음")

        assert result.is_anomaly is False
        assert result.anomaly_score is None


class TestRelationContract:
    def test_empty_relations_are_valid_success(self) -> None:
        # ETC-01-C1 은 downstream 이 없다. 관계가 비어도 성공이다.
        result = EquipmentContextToolResult(
            ok=True,
            equipment=EquipmentNode(
                equipment_id="ETC-01",
                equipment_name="Dry Etcher #1",
                model_code="ET-7500",
                area_id="ETCH",
            ),
        )

        assert result.upstream == []
        assert result.downstream == []
        assert result.sibling_chambers == []
