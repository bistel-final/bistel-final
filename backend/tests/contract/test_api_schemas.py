from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.schemas import (
    ActionItem,
    AgentRunItem,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ToolCallItem,
)
from app.analytics.schemas import (
    AnalysisQueryRequest,
    AnalysisQueryResponse,
    AuditLogItem,
    EvaluationItem,
)
from app.common.audit import AuditEvent
from app.common.enums import ApprovalStatus
from app.common.exceptions import PolicyRejectedError
from app.common.schemas import (
    ReadinessDependencies,
    ReadinessDependency,
    ReadinessResponse,
)
from app.common.tool_contracts import (
    REASON_PREFIXES,
    MetricPlan,
    SensorSummaryItem,
    VisualizationPlan,
)
from app.detection.schemas import (
    AlarmItem,
    SensorLimits,
    TraceSearchRequest,
)
from app.knowledge.schemas import (
    ChamberRelationResponse,
    DocumentDetailResponse,
    DocumentSearchRequest,
)

NOW = datetime(2026, 6, 4, tzinfo=UTC)
INCIDENT = {"lot_id": "LOT-260003", "chamber_id": "ETC-01-C2"}


def _equipment_payload() -> dict[str, object]:
    return {
        "equipment_id": "ETC-01",
        "equipment_name": "Dry Etcher #1",
        "model_code": "ET-7500",
        "area_id": "ETCH",
        "step_id": "CT-ETCH",
    }


class TestSharedContracts:
    def test_sensor_summary_has_required_nonnegative_counts(self) -> None:
        item = SensorSummaryItem(
            sensor_id="ET_REFL",
            sensor_name="Reflected Power",
            unit="W",
            recipe_step_no=1,
            point_cnt=3,
            ooc_point_cnt=0,
            oos_point_cnt=1,
            judgement="OOS",
        )

        assert item.unit == "W"
        assert item.point_cnt == 3

    @pytest.mark.parametrize(
        "field",
        ["point_cnt", "ooc_point_cnt", "oos_point_cnt"],
    )
    def test_sensor_summary_rejects_negative_counts(self, field: str) -> None:
        payload = {
            "sensor_id": "ET_REFL",
            "sensor_name": "Reflected Power",
            "recipe_step_no": 1,
            "point_cnt": 3,
            "ooc_point_cnt": 0,
            "oos_point_cnt": 1,
            "judgement": "OOS",
            field: -1,
        }

        with pytest.raises(ValidationError):
            SensorSummaryItem(**payload)

    def test_readiness_down_requires_normalized_reason(self) -> None:
        with pytest.raises(ValidationError, match="reason"):
            ReadinessDependency(status="down", latency_ms=2000)

        dependency = ReadinessDependency(
            status="down",
            latency_ms=2000,
            reason="TIMEOUT",
        )

        assert dependency.reason == "TIMEOUT"

    def test_readiness_aggregate_status_must_match_dependencies(self) -> None:
        dependencies = ReadinessDependencies(
            postgres=ReadinessDependency(status="up", latency_ms=2),
            neo4j=ReadinessDependency(status="up", latency_ms=3),
            n8n=ReadinessDependency(
                status="down",
                latency_ms=2000,
                reason="CONNECTION_FAILED",
            ),
        )

        with pytest.raises(ValidationError, match="일치"):
            ReadinessResponse(status="ready", dependencies=dependencies)


class TestDetectionSchemas:
    def test_alarm_detail_is_text_and_action_approval_accepts_auto(self) -> None:
        alarm = AlarmItem(
            alarm_id="ALM-0001",
            lot_hist_id="LH-00001",
            lot_id="LOT-260003",
            occurred_at=NOW,
            detail="OOC point count=2",
            incident=INCIDENT,
            approval_status="AUTO",
        )

        assert alarm.detail == "OOC point count=2"
        assert alarm.approval_status == "AUTO"

    def test_alarm_detail_rejects_object(self) -> None:
        with pytest.raises(ValidationError):
            AlarmItem(
                alarm_id="ALM-0001",
                lot_hist_id="LH-00001",
                lot_id="LOT-260003",
                occurred_at=NOW,
                detail={"message": "wrong"},
                incident=INCIDENT,
            )

    def test_alarm_action_approval_rejects_expired(self) -> None:
        with pytest.raises(ValidationError):
            AlarmItem(
                alarm_id="ALM-0001",
                lot_hist_id="LH-00001",
                lot_id="LOT-260003",
                occurred_at=NOW,
                incident=INCIDENT,
                approval_status="EXPIRED",
            )

    def test_trace_from_alias_and_time_order(self) -> None:
        request = TraceSearchRequest.model_validate(
            {
                "sensor_ids": ["ET_REFL"],
                "from": "2026-06-01T00:00:00+09:00",
                "to": "2026-06-02T00:00:00+09:00",
            }
        )

        assert "from" in request.model_dump(mode="json", by_alias=True)

        with pytest.raises(ValidationError, match="빨라야"):
            TraceSearchRequest.model_validate(
                {
                    "sensor_ids": ["ET_REFL"],
                    "from": "2026-06-03T00:00:00+09:00",
                    "to": "2026-06-02T00:00:00+09:00",
                }
            )

        with pytest.raises(ValidationError, match="빨라야"):
            TraceSearchRequest.model_validate(
                {
                    "sensor_ids": ["ET_REFL"],
                    "from": "2026-06-02T00:00:00+09:00",
                    "to": "2026-06-02T00:00:00+09:00",
                }
            )

    def test_trace_requires_unique_sensor_ids(self) -> None:
        with pytest.raises(ValidationError):
            TraceSearchRequest(sensor_ids=[])

        with pytest.raises(ValidationError, match="중복"):
            TraceSearchRequest(sensor_ids=["ET_REFL", "ET_REFL"])

        with pytest.raises(ValidationError, match="중복"):
            TraceSearchRequest(sensor_ids=["ET_REFL"], wafer_nos=[1, 1])

    def test_upper_only_does_not_depend_on_null_lower_limit(self) -> None:
        limits = SensorLimits(
            spec_lower=0.0,
            ctrl_lower=0.0,
            ctrl_upper=21.0,
            spec_upper=30.0,
            upper_only=True,
        )

        assert limits.upper_only is True
        assert limits.spec_lower == 0.0


class TestKnowledgeSchemas:
    def test_relation_reuses_equipment_node_for_upstream(self) -> None:
        response = ChamberRelationResponse(
            chamber={
                "chamber_id": "ETC-01-C2",
                "equipment_id": "ETC-01",
            },
            equipment=_equipment_payload(),
            sibling_chambers=[],
            upstream=[
                {
                    "equipment_id": "PHO-01",
                    "equipment_name": "Photo Scanner #1",
                    "model_code": "PH-9000",
                    "area_id": "PHOTO",
                }
            ],
            downstream=[],
        )

        assert response.upstream[0].equipment_id == "PHO-01"

    @pytest.mark.parametrize("doc_type", ["SPEC", "MANUAL", "TROUBLESHOOT", None])
    def test_document_type_matches_database_constraint(
        self,
        doc_type: str | None,
    ) -> None:
        document = DocumentDetailResponse(
            document_id="DOC-1",
            title="문서",
            doc_type=doc_type,
            chunks=[],
        )

        assert document.doc_type == doc_type

    def test_document_type_rejects_pdf_legacy_values(self) -> None:
        with pytest.raises(ValidationError):
            DocumentDetailResponse(
                document_id="DOC-1",
                title="문서",
                doc_type="guide",
                chunks=[],
            )

    @pytest.mark.parametrize("top_k", [0, 11])
    def test_document_search_top_k_constraint(self, top_k: int) -> None:
        with pytest.raises(ValidationError):
            DocumentSearchRequest(query="정비", top_k=top_k)


class TestAgentSchemas:
    def test_fault_code_is_four_types_or_null(self) -> None:
        run = AgentRunItem(
            agent_run_id="RUN-1",
            incident=INCIDENT,
            alarm_count=1,
            started_at=NOW,
            status="RUNNING",
            fault_code=None,
        )

        assert run.fault_code is None

        with pytest.raises(ValidationError):
            AgentRunItem(
                agent_run_id="RUN-1",
                incident=INCIDENT,
                alarm_count=1,
                started_at=NOW,
                status="COMPLETED",
                fault_code="NRM",
            )

    def test_tool_call_keeps_input_and_output(self) -> None:
        tool_call = ToolCallItem(
            tool_call_id="TOOL-1",
            call_seq=1,
            tool_name="get_fdc_summary",
            input={"lot_hist_id": "LH-00001"},
            output={"ok": True},
            status="SUCCESS",
            called_at=NOW,
        )

        assert tool_call.input == {"lot_hist_id": "LH-00001"}
        assert tool_call.output == {"ok": True}

    @pytest.mark.parametrize("status", ["APPROVED", "REJECTED"])
    def test_decision_response_has_narrow_success_status(self, status: str) -> None:
        response = ApprovalDecisionResponse(
            approval_id="APR-1",
            action_id="ACT-1",
            approval_status=status,
            send_status="WAITING",
            agent_run_status="RUNNING",
            decided_by="operator",
            decided_at=NOW,
        )

        assert response.approval_status == status

    @pytest.mark.parametrize("status", ["PENDING", "EXPIRED"])
    def test_decision_response_rejects_non_decision_status(self, status: str) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionResponse(
                approval_id="APR-1",
                action_id="ACT-1",
                approval_status=status,
                send_status="WAITING",
                agent_run_status="RUNNING",
                decided_by="operator",
                decided_at=NOW,
            )

    def test_decision_actor_is_required(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(decision="APPROVE", decided_by="   ")

    def test_action_uses_immutable_origin_field_name(self) -> None:
        action = ActionItem(
            action_id="ACT-1",
            created_by_agent_run_id="RUN-1",
            incident=INCIDENT,
            action_code="MONITOR",
            alarm_count=1,
        )

        assert action.created_by_agent_run_id == "RUN-1"
        assert "agent_run_id" not in ActionItem.model_fields


class TestAnalyticsSchemas:
    @staticmethod
    def _policy_client() -> TestClient:
        app = FastAPI()

        @app.post("/analytics/query", response_model=AnalysisQueryResponse)
        def _query(_: AnalysisQueryRequest) -> AnalysisQueryResponse:
            return AnalysisQueryResponse(
                question="데이터를 삭제해줘",
                sql=None,
                columns=[],
                rows=[],
                row_count=0,
                metric=None,
                group_by=[],
                visualization=None,
                is_valid=False,
                is_rejected=True,
                reject_reason="쓰기 요청은 허용되지 않습니다.",
                latency_ms=4,
                nl_query_log_id=1,
            )

        return TestClient(app)

    def test_policy_rejection_http_status_is_200(self) -> None:
        response = self._policy_client().post(
            "/analytics/query",
            json={"question": "데이터를 삭제해줘"},
        )

        assert response.status_code == 200
        assert response.json()["is_rejected"] is True

    def test_malformed_question_is_422(self) -> None:
        response = self._policy_client().post(
            "/analytics/query",
            json={"question": "   "},
        )

        assert response.status_code == 422

    def test_policy_rejection_is_structured_success_body(self) -> None:
        response = AnalysisQueryResponse(
            question="데이터를 삭제해줘",
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            metric=None,
            group_by=[],
            visualization=None,
            is_valid=False,
            is_rejected=True,
            reject_reason="쓰기 요청은 허용되지 않습니다.",
            latency_ms=4,
            nl_query_log_id=1,
        )

        assert response.is_rejected is True
        assert response.reject_reason

    def test_policy_rejection_cannot_be_valid(self) -> None:
        with pytest.raises(ValidationError, match="is_valid"):
            AnalysisQueryResponse(
                question="삭제",
                columns=[],
                rows=[],
                row_count=0,
                group_by=[],
                is_valid=True,
                is_rejected=True,
                reject_reason="정책 거부",
                latency_ms=1,
                nl_query_log_id=1,
            )

    @pytest.mark.parametrize(
        "field, value",
        [
            ("sql", "DELETE FROM fdc_alarm"),
            ("columns", ["alarm_id"]),
            ("rows", [{"alarm_id": "ALM-0001"}]),
            ("group_by", ["chamber_id"]),
            ("metric", MetricPlan(type="count")),
            ("visualization", VisualizationPlan(chart_type="table")),
        ],
    )
    def test_policy_rejection_clears_executable_payload(
        self,
        field: str,
        value: object,
    ) -> None:
        payload = {
            "question": "삭제",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "metric": None,
            "group_by": [],
            "visualization": None,
            "is_valid": False,
            "is_rejected": True,
            "reject_reason": "정책 거부",
            "latency_ms": 1,
            "nl_query_log_id": 1,
            field: value,
        }

        with pytest.raises(ValidationError):
            AnalysisQueryResponse(**payload)

    def test_valid_query_requires_plan_and_visualization(self) -> None:
        with pytest.raises(ValidationError, match="sql·metric·visualization"):
            AnalysisQueryResponse(
                question="알람 수",
                columns=[],
                rows=[],
                row_count=0,
                group_by=[],
                is_valid=True,
                is_rejected=False,
                latency_ms=1,
                nl_query_log_id=1,
            )

        response = AnalysisQueryResponse(
            question="알람 수",
            sql="SELECT count(*) AS count FROM fdc_alarm",
            columns=["count"],
            rows=[{"count": 51}],
            row_count=1,
            metric=MetricPlan(type="count"),
            metric_result=51,
            group_by=[],
            visualization=VisualizationPlan(chart_type="table"),
            is_valid=True,
            is_rejected=False,
            latency_ms=10,
            nl_query_log_id=1,
        )

        assert response.metric_result == 51

    def test_policy_exception_and_tool_prefix_are_preserved(self) -> None:
        assert PolicyRejectedError.status_code == 422
        assert "POLICY_REJECTED:" in REASON_PREFIXES

    def test_evaluation_item_is_explicit_model(self) -> None:
        item = EvaluationItem(
            case_type="GOLD",
            case_id="Q-01",
            question="알람 수",
            passed=True,
            attempt_count=1,
            expected_result=51,
            actual_result=51,
        )

        assert item.expected_result == item.actual_result

    def test_audit_detail_is_text_and_event_is_canonical(self) -> None:
        item = AuditLogItem(
            audit_id=1,
            occurred_at=NOW,
            actor_type="AGENT",
            event_type=AuditEvent.CLASSIFICATION_COMPLETED,
            detail="분류 완료",
        )

        assert item.detail == "분류 완료"
        assert item.event_type is AuditEvent.CLASSIFICATION_COMPLETED

    def test_approval_status_enum_remains_full_for_queue_rows(self) -> None:
        assert ApprovalStatus.EXPIRED.value == "EXPIRED"
