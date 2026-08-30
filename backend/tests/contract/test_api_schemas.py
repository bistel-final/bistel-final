from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.schemas import (
    ActionDeliveryItem,
    ActionItem,
    AgentRunCreateRequest,
    AgentRunItem,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalItem,
    ToolCallItem,
)
from app.analytics.schemas import (
    AnalysisQueryRequest,
    AnalysisQueryResponse,
    AuditLogItem,
    EvaluationItem,
)
from app.common.audit import AuditEvent
from app.common.enums import (
    ApprovalStatus,
    Decision,
    DeliveryChannel,
)
from app.common.exceptions import PolicyRejectedError
from app.common.schemas import (
    ReadinessDependencies,
    ReadinessDependency,
    ReadinessResponse,
)
from app.common.tool_contracts import (
    REASON_PREFIXES,
    AnomalySignal,
    MetricPlan,
    ParameterSummaryItem,
    VisualizationPlan,
)
from app.detection.public_schemas import AlarmItem
from app.detection.schemas import (
    DashboardSummaryResponse,
    ParameterLimits,
    TraceCatalogResponse,
    TraceSearchRequest,
)
from app.knowledge.schemas import (
    ChamberRelationResponse,
    DocumentDetailResponse,
    DocumentHit,
    DocumentSearchRequest,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)
INCIDENT = {"lot_id": "LOT004", "chamber_id": "EQP04-PM2"}
ALARM_REF = {"source": "TRACE", "alarm_id": "TAL-0001"}


def _alarm_payload() -> dict[str, object]:
    return {
        "source": "TRACE",
        "alarm_id": "TAL-0001",
        "occurred_at": NOW,
        "area": "Etch",
        "equipment_id": "EQP04",
        "equipment": "EQP04",
        "chamber_id": "EQP04-PM2",
        "chamber": "EQP04-PM2",
        "recipe_id": "RECIPE04",
        "recipe": "RECIPE04",
        "lot_id": "LOT004",
        "lot": "LOT004",
        "wafer_id": "LOT004W001",
        "wafer": "LOT004W001",
        "parameter_id": "ET_REFL",
        "parameter": "ET_REFL",
        "recipe_step_no": 1,
        "step_no": 1,
        "seq_no": 0,
        "alarm_type": "OOS",
        "value": 61.0,
        "rule_code": "TRACE_OOS",
        "predicted_fault_code": None,
        "fault": None,
        "action_code": None,
        "notify_status": None,
        "notify": False,
        "mes_status": None,
        "mes": "",
        "statistic_type": None,
        "cl": None,
        "ucl": None,
        "lcl": None,
    }


def _parameter_summary() -> dict[str, object]:
    return {
        "parameter_id": "ET_REFL",
        "parameter_name": "Reflected Power",
        "unit": "W",
        "recipe_step_no": 1,
        "point_cnt": 3,
        "ooc_point_cnt": 0,
        "oos_point_cnt": 1,
        "alarm_type": "OOS",
    }


def _delivery(
    channel: str,
    status: str,
    *,
    action_id: str = "ACT-1",
) -> ActionDeliveryItem:
    return ActionDeliveryItem(
        action_id=action_id,
        channel=channel,
        status=status,
        request_hash="a" * 64,
        attempt_count=0,
    )


def _equipment_payload() -> dict[str, object]:
    return {
        "equipment_id": "EQP04",
        "equipment_name": "Dry Etcher #1",
        "model_code": "ET-7500",
        "area_id": "ETCH",
        "step_id": "CT-ETCH",
    }


class TestSharedContracts:
    def test_parameter_summary_has_required_nonnegative_counts(self) -> None:
        item = ParameterSummaryItem(**_parameter_summary())

        assert item.unit == "W"
        assert item.point_cnt == 3
        assert item.alarm_type == "OOS"

    @pytest.mark.parametrize(
        "field",
        ["point_cnt", "ooc_point_cnt", "oos_point_cnt"],
    )
    def test_parameter_summary_rejects_negative_counts(self, field: str) -> None:
        payload = {**_parameter_summary(), field: -1}

        with pytest.raises(ValidationError):
            ParameterSummaryItem(**payload)

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
    def test_alarm_is_source_aware_and_uses_canonical_terms(self) -> None:
        alarm = AlarmItem(**_alarm_payload())

        assert f"{alarm.source}:{alarm.alarm_id}" == "TRACE:TAL-0001"
        assert alarm.parameter_id == "ET_REFL"
        assert alarm.alarm_type == "OOS"
        assert alarm.parameter == alarm.parameter_id

    def test_alarm_rejects_divergent_alias(self) -> None:
        payload = {**_alarm_payload(), "parameter": "PH_FOCUS"}

        with pytest.raises(ValidationError, match="alias"):
            AlarmItem(**payload)

    def test_alarm_rejects_rule_source_mismatch(self) -> None:
        payload = {**_alarm_payload(), "rule_code": "SUMMARY_OOC"}

        with pytest.raises(ValidationError, match="rule_code"):
            AlarmItem(**payload)

    def test_dashboard_empty_result_keeps_explicit_request_range(self) -> None:
        requested_range = [date(2026, 8, 8), date(2026, 8, 9)]
        payload: dict[str, object] = {
            "date_range": requested_range,
            "area": "ALL",
            "hierarchy": [],
            "parameter_catalog": [],
            "alarm_count": 0,
            "trace_oos_count": 0,
            "summary_ooc_count": 0,
            "r03_count": 0,
            "source_counts": {},
            "daily_trend": [],
            "top_parameters": [],
            "equipment_counts": [],
            "recent_alarms": [],
        }

        response = DashboardSummaryResponse(**payload)

        assert response.date_range == requested_range
        assert response.alarm_count == 0
        assert response.daily_trend == []
        assert response.recent_alarms == []

        with pytest.raises(ValidationError, match="date_range"):
            DashboardSummaryResponse(**{**payload, "date_range": []})

    def test_trace_from_alias_and_time_order(self) -> None:
        request = TraceSearchRequest.model_validate(
            {
                "parameter_ids": ["ET_REFL"],
                "from": "2026-08-01T00:00:00+09:00",
                "to": "2026-08-02T00:00:00+09:00",
            }
        )

        assert "from" in request.model_dump(mode="json", by_alias=True)

        with pytest.raises(ValidationError, match="빨라야"):
            TraceSearchRequest.model_validate(
                {
                    "parameter_ids": ["ET_REFL"],
                    "from": "2026-08-03T00:00:00+09:00",
                    "to": "2026-08-02T00:00:00+09:00",
                }
            )

        with pytest.raises(ValidationError, match="빨라야"):
            TraceSearchRequest.model_validate(
                {
                    "parameter_ids": ["ET_REFL"],
                    "from": "2026-08-02T00:00:00+09:00",
                    "to": "2026-08-02T00:00:00+09:00",
                }
            )

    def test_trace_requires_unique_parameter_ids(self) -> None:
        with pytest.raises(ValidationError):
            TraceSearchRequest(parameter_ids=[])

        with pytest.raises(ValidationError, match="중복"):
            TraceSearchRequest(parameter_ids=["ET_REFL", "ET_REFL"])

        with pytest.raises(ValidationError, match="중복"):
            TraceSearchRequest(parameter_ids=["ET_REFL"], wafer_nos=[1, 1])

    def test_upper_only_does_not_depend_on_null_lower_limit(self) -> None:
        limits = ParameterLimits(
            spec_lower=0.0,
            ctrl_lower=0.0,
            ctrl_upper=21.0,
            spec_upper=30.0,
            upper_only=True,
        )

        assert limits.upper_only is True
        assert limits.spec_lower == 0.0

    def test_trace_catalog_anomaly_is_optional_adapter_data(self) -> None:
        catalog = TraceCatalogResponse(
            areas=[],
            equipments=[],
            parameters=[],
            recipes=[],
            lots=[],
        )

        assert catalog.anomaly is None

        with_signal = TraceCatalogResponse(
            areas=[],
            equipments=[],
            parameters=[],
            recipes=[],
            lots=[],
            anomaly={
                "score": 0.71,
                "model_version": "IFOREST-20260814",
                "score_method": "MINMAX-V1",
                "threshold_validation_status": "UNVERIFIED",
            },
        )
        assert isinstance(with_signal.anomaly, AnomalySignal)
        schema = TraceCatalogResponse.model_json_schema()
        assert schema["$defs"]["ThresholdValidationStatus"]["enum"] == [
            "VERIFIED",
            "UNVERIFIED",
        ]


class TestKnowledgeSchemas:
    def test_relation_returns_graph_projection_with_provenance(self) -> None:
        response = ChamberRelationResponse(
            root_node_id="Chamber:EQP04-PM2",
            nodes=[
                {
                    "id": "Chamber:EQP04-PM2",
                    "label": "Chamber",
                    "business_id": "EQP04-PM2",
                    "display_name": "EQP04-PM2",
                    "properties": {
                        "chamber_id": "EQP04-PM2",
                        "equipment_id": "EQP04",
                    },
                },
                {
                    "id": "Equipment:EQP04",
                    "label": "Equipment",
                    "business_id": "EQP04",
                    "display_name": "Dry Etcher #1",
                    "properties": _equipment_payload(),
                },
            ],
            relationships=[
                {
                    "id": "REL-1",
                    "type": "PART_OF",
                    "source": "Chamber:EQP04-PM2",
                    "target": "Equipment:EQP04",
                }
            ],
            graph_revision="graph-r1",
        )

        assert response.root_node_id == "Chamber:EQP04-PM2"
        assert response.nodes[0].id == "Chamber:EQP04-PM2"
        assert response.relationships[0].id == "REL-1"
        assert "adjacent_steps" not in ChamberRelationResponse.model_fields
        assert "relations" not in ChamberRelationResponse.model_fields

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

    def test_document_search_hit_exposes_doc_id_compat_alias(self) -> None:
        response = DocumentHit(
            chunk_id="DOC-SPEC-ET7500:cs2:0001",
            document_id="DOC-SPEC-ET7500",
            doc_id="DOC-SPEC-ET7500",
            title="ET Guide",
            score=0.82,
            content="content",
            model_code="ET-7500",
        )

        assert response.doc_id == response.document_id


class TestAgentSchemas:
    def test_create_run_requires_source_aware_alarm(self) -> None:
        request = AgentRunCreateRequest(alarm=ALARM_REF)

        assert request.alarm.to_token() == "TRACE:TAL-0001"
        with pytest.raises(ValidationError):
            AgentRunCreateRequest(alarm_id="TAL-0001")

    def test_prediction_is_not_exposed_as_ground_truth(self) -> None:
        run = AgentRunItem(
            agent_run_id="RUN-1",
            incident=INCIDENT,
            requested_alarm=ALARM_REF,
            representative_alarm=ALARM_REF,
            alarm_count=1,
            started_at=NOW,
            status="RUNNING",
            predicted_fault_code="OTH",
        )

        assert run.predicted_fault_code == "OTH"
        assert run.reviewed_fault_code is None
        assert run.ground_truth_available is False
        assert "fault_code" not in AgentRunItem.model_fields
        assert "synthetic_label" not in AgentRunItem.model_fields

        with pytest.raises(ValidationError):
            AgentRunItem(
                agent_run_id="RUN-1",
                incident=INCIDENT,
                requested_alarm=ALARM_REF,
                representative_alarm=ALARM_REF,
                alarm_count=1,
                started_at=NOW,
                status="COMPLETED",
                predicted_fault_code="NRM",
            )

    def test_review_requires_disposition_and_label_source(self) -> None:
        base = {
            "agent_run_id": "RUN-1",
            "incident": INCIDENT,
            "requested_alarm": ALARM_REF,
            "representative_alarm": ALARM_REF,
            "alarm_count": 1,
            "started_at": NOW,
            "status": "COMPLETED",
            "predicted_fault_code": "FOC",
        }
        with pytest.raises(ValidationError, match="label_source"):
            AgentRunItem(**base, review_disposition="ACCEPTED")
        with pytest.raises(ValidationError, match="reviewed_fault_code"):
            AgentRunItem(
                **base,
                review_disposition="CORRECTED",
                label_source="HUMAN_REVIEW",
            )

        reviewed = AgentRunItem(
            **base,
            reviewed_fault_code="RFM",
            review_disposition="CORRECTED",
            label_source="HUMAN_REVIEW",
        )
        assert reviewed.reviewed_fault_code == "RFM"

    def test_approval_queue_rejects_action_auto_status(self) -> None:
        base = {
            "approval_id": "APR-1",
            "agent_run_id": "RUN-1",
            "action_id": "ACT-1",
            "trigger_alarm": ALARM_REF,
            "incident": INCIDENT,
            "action_code": "EQP_HOLD",
            "severity": "HIGH",
            "requested_at": NOW,
        }
        assert ApprovalItem(**base, status="PENDING").status == "PENDING"
        with pytest.raises(ValidationError):
            ApprovalItem(**base, status="AUTO")

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
            agent_run_status="RUNNING",
            deliveries=[_delivery("MES", "WAITING")],
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
                agent_run_status="RUNNING",
                deliveries=[_delivery("MES", "WAITING")],
                decided_by="operator",
                decided_at=NOW,
            )

    def test_decision_actor_is_required(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecisionRequest(decision="APPROVE", decided_by="   ")

    @pytest.mark.parametrize(
        ("action_code", "deliveries"),
        [
            ("MONITORING", []),
            ("WARNING", [_delivery("EMAIL", "WAITING")]),
            (
                "EQP_HOLD",
                [
                    _delivery("EMAIL", "WAITING"),
                    _delivery("MES", "BLOCKED"),
                ],
            ),
        ],
    )
    def test_action_uses_three_stage_channel_plan(
        self,
        action_code: str,
        deliveries: list[ActionDeliveryItem],
    ) -> None:
        action = ActionItem(
            action_id="ACT-1",
            incident=INCIDENT,
            trigger_alarm=ALARM_REF,
            action_code=action_code,
            severity={
                "MONITORING": "LOW",
                "WARNING": "MEDIUM",
                "EQP_HOLD": "HIGH",
            }[action_code],
            approval_status="PENDING" if action_code == "EQP_HOLD" else "AUTO",
            alarm_count=1,
            created_at=NOW,
            deliveries=deliveries,
        )

        assert action.action_code == action_code
        assert "send_status" not in ActionItem.model_fields
        assert "send_channel" not in ActionItem.model_fields

    def test_action_rejects_old_code_and_wrong_channel_plan(self) -> None:
        base = {
            "action_id": "ACT-1",
            "incident": INCIDENT,
            "trigger_alarm": ALARM_REF,
            "severity": "MEDIUM",
            "approval_status": "AUTO",
            "alarm_count": 1,
            "created_at": NOW,
        }
        with pytest.raises(ValidationError):
            ActionItem(**base, action_code="NOTIFY", deliveries=[])
        with pytest.raises(ValidationError, match="channel"):
            ActionItem(**base, action_code="WARNING", deliveries=[])


class TestAnalyticsSchemas:
    @staticmethod
    def _policy_client() -> TestClient:
        app = FastAPI()

        @app.post("/analytics/query", response_model=AnalysisQueryResponse)
        def _query(_: AnalysisQueryRequest) -> AnalysisQueryResponse:
            return AnalysisQueryResponse(
                question="데이터를 삭제해줘",
                generated_sql=None,
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
            generated_sql=None,
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
            ("generated_sql", "DELETE FROM summary_data"),
            ("columns", ["alarm_id"]),
            ("rows", [{"alarm_id": "TAL-0001"}]),
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
            "generated_sql": None,
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
        with pytest.raises(ValidationError, match="generated_sql·metric·visualization"):
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
            generated_sql=(
                "SELECT count(*) AS count FROM v_alarm_event WHERE source != 'R03'"
            ),
            columns=["count"],
            rows=[{"count": 173}],
            row_count=1,
            metric=MetricPlan(type="count"),
            metric_result=173,
            group_by=[],
            visualization=VisualizationPlan(chart_type="table"),
            is_valid=True,
            is_rejected=False,
            latency_ms=10,
            nl_query_log_id=1,
        )

        assert response.metric_result == 173

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
            expected_result=173,
            actual_result=173,
        )

        assert item.expected_result == item.actual_result

    def test_audit_detail_is_text_and_event_is_canonical(self) -> None:
        item = AuditLogItem(
            audit_id=1,
            occurred_at=NOW,
            actor_type="AGENT",
            event_type=AuditEvent.HYPOTHESIS_GENERATED,
            detail="분류 완료",
        )

        assert item.detail == "분류 완료"
        assert item.event_type is AuditEvent.HYPOTHESIS_GENERATED

    def test_approval_status_enum_remains_full_for_queue_rows(self) -> None:
        assert ApprovalStatus.EXPIRED.value == "EXPIRED"


class TestPublicInternalBoundary:
    """공개 DTO가 내부 Runtime 값을 노출하지 않는다 (`V5-CM-4.1` 묶음 2)."""

    def test_approval_request_rejects_the_internal_command(self) -> None:
        """public 요청은 `APPROVED|REJECTED`만 받는다(API v3 §2.4)."""

        from app.agent.schemas import ApprovalDecisionRequest

        assert (
            ApprovalDecisionRequest(
                decision="APPROVED", decided_by="daehyuk"
            ).decision.value
            == "APPROVED"
        )
        for internal in Decision:
            with pytest.raises(ValidationError):
                ApprovalDecisionRequest(decision=internal.value, decided_by="daehyuk")

    def test_approval_item_rejects_internal_only_statuses(self) -> None:
        """`AUTO`·`EXPIRED`는 공개 목록에 오르지 않는다."""

        from app.agent.schemas import ApprovalItem

        base = {
            "approval_id": "APR-1",
            "agent_run_id": "RUN-1",
            "action_id": "ACT-1",
            "trigger_alarm": ALARM_REF,
            "incident": INCIDENT,
            "action_code": "EQP_HOLD",
            "severity": "HIGH",
            "requested_at": NOW,
        }
        assert ApprovalItem(**base, status="PENDING").status.value == "PENDING"
        for internal in (ApprovalStatus.AUTO, ApprovalStatus.EXPIRED):
            with pytest.raises(ValidationError):
                ApprovalItem(**base, status=internal.value)

    @pytest.mark.parametrize(
        ("model_path", "field", "public_enum"),
        [
            ("ApprovalDecisionRequest", "decision", "PublicApprovalDecision"),
            ("ApprovalItem", "status", "PublicApprovalStatus"),
            ("ActionDeliveryItem", "channel", "PublicDeliveryChannel"),
        ],
    )
    def test_public_fields_are_annotated_with_the_public_enum(
        self, model_path: str, field: str, public_enum: str
    ) -> None:
        """**값이 같아도 타입이 내부 Enum이면 경계가 회귀한 것이다.**

        `Literal[ApprovalStatus.PENDING, …]`처럼 같은 3값을 내부 Enum 멤버로 구성하면
        JSON은 똑같지만 field는 다시 내부 Enum에 묶인다. 동작 테스트로는 구분되지
        않아 annotation identity로 고정한다(구현리뷰 1차 필수 1).
        """

        from app.agent import schemas as agent_schemas
        from app.common import enums

        model = getattr(agent_schemas, model_path)
        expected = getattr(enums, public_enum)
        annotation = model.model_fields[field].annotation
        assert (
            annotation is expected
        ), f"{model_path}.{field}는 {public_enum}이어야 한다 (현재 {annotation})"
        # 내부 Enum이 annotation 어디에도 섞이지 않는다.
        internal = {enums.Decision, enums.ApprovalStatus, enums.DeliveryChannel}
        assert not (set(getattr(annotation, "__args__", ())) & internal)
        assert annotation not in internal

    def test_public_enums_expose_exactly_the_documented_values(self) -> None:
        """API v3가 정의한 공개 값 집합."""

        from app.common import enums

        assert {v.value for v in enums.PublicApprovalDecision} == {
            "APPROVED",
            "REJECTED",
        }
        assert {v.value for v in enums.PublicApprovalStatus} == {
            "PENDING",
            "APPROVED",
            "REJECTED",
        }
        assert {v.value for v in enums.PublicDeliveryChannel} == {"EMAIL", "MES"}

    def test_delivery_item_rejects_the_internal_channel(self) -> None:
        """공개 channel은 `EMAIL|MES`다. `MES_MOCK`은 내부 값이다."""

        assert _delivery("MES", "WAITING").channel.value == "MES"
        with pytest.raises(ValidationError):
            _delivery(DeliveryChannel.MES_MOCK.value, "WAITING")

    def test_accepted_run_is_the_minimum_public_body(self) -> None:
        """accepted 응답이 내부 실행 문맥을 노출하지 않는다."""

        from app.agent.schemas import AgentRunAcceptedResponse

        accepted = AgentRunAcceptedResponse(
            agent_run_id="RUN-1", status="RUNNING", alarm=ALARM_REF
        )
        assert set(AgentRunAcceptedResponse.model_fields) == {
            "agent_run_id",
            "status",
            "alarm",
        }
        assert accepted.status.value == "RUNNING"
        # 내부 실행 문맥은 여기 오지 않는다.
        for leaked in ("thread_id", "incident", "representative_alarm"):
            assert leaked not in AgentRunAcceptedResponse.model_fields

    def test_accepted_run_refuses_a_status_the_database_cannot_store(self) -> None:
        """`PENDING`은 `002_agent_runtime_clean.sql` CHECK가 저장할 수 없다."""

        from app.agent.schemas import AgentRunAcceptedResponse

        for refused in ("PENDING", "WAITING_APPROVAL", "COMPLETED", "FAILED"):
            with pytest.raises(ValidationError):
                AgentRunAcceptedResponse(
                    agent_run_id="RUN-1", status=refused, alarm=ALARM_REF
                )

    def test_run_status_matches_the_migration_check(self) -> None:
        """공통 `RunStatus`는 migration CHECK 4종과 exact하다."""

        from app.common.enums import RunStatus

        assert {status.value for status in RunStatus} == {
            "RUNNING",
            "WAITING_APPROVAL",
            "COMPLETED",
            "FAILED",
        }
