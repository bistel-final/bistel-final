import pytest
from pydantic import ValidationError

from app.common.enums import (
    AlarmType,
    DeliveryChannel,
    DeliveryStatus,
    IncidentModelSignalStatus,
    ThresholdValidationStatus,
)
from app.common.tool_contracts import (
    AGENT_TOOL_NAMES,
    REASON_PREFIXES,
    TOOL_RESULT_MODELS,
    AnalysisPlanToolInput,
    AnalysisPlanToolResult,
    AnomalySignal,
    ChannelDeliveryResult,
    DocumentHit,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
    IncidentModelSignal,
    MetricPlan,
    ParameterSummaryItem,
    SendActionToolInput,
    SendActionToolResult,
    VisualizationPlan,
    WaferContext,
    fail,
)

WAFER = {
    "lot_hist_id": "LH-00181",
    "lot_id": "LOT004",
    "wafer_no": 6,
    "chamber_id": "EQP04-PM2",
    "equipment_id": "EQP04",
    "step_id": "CT-ETCH",
    "recipe_id": "RECIPE02",
}

PARAMETER = {
    "parameter_id": "PH_FOCUS",
    "parameter_name": "Focus Offset",
    "unit": "nm",
    "recipe_step_no": 1,
    "point_cnt": 3,
    "ooc_point_cnt": 0,
    "oos_point_cnt": 1,
    "alarm_type": AlarmType.OOS,
}

EQUIPMENT_CONTEXT = {
    "chamber_id": "EQP04-PM2",
    "equipment_id": "EQP04",
    "sibling_chamber_ids": ["EQP04-PM1"],
    "area": "Etch",
    "model_code": "ET-7500",
    "process_step_id": "CT-ETCH",
    "upstream_process_step_ids": ["CT-PHOTO"],
    "downstream_process_step_ids": [],
    "parameter_ids": ["ET_CF4", "ET_ESC", "ET_PRES", "ET_REFL"],
}

DOCUMENT = {
    "chunk_id": "CHK-001",
    "document_id": "DOC-001",
    "title": "Fault Guide",
    "score": 0.82,
    "content": "점검 절차",
}

SCORE_ONLY_SIGNAL = {
    "score": 0.71,
    "model_version": "IFOREST-20260814",
    "score_method": "MINMAX-V1",
    "threshold_validation_status": ThresholdValidationStatus.UNVERIFIED,
}

VERIFIED_SIGNAL = {
    **SCORE_ONLY_SIGNAL,
    # train q95 구현 전 계약 불변식만 검증하는 합성 예시이며 운영 threshold가 아니다.
    "display_threshold": 0.65,
    "is_anomaly": True,
    "action_threshold": 0.68,
    "threshold_version": "ACTION-THRESHOLD-V1",
    "threshold_validation_status": ThresholdValidationStatus.VERIFIED,
}


def _fdc_success(**overrides: object) -> FdcSummaryToolResult:
    payload = {
        "ok": True,
        "wafer": WaferContext(**WAFER),
        "parameters": [ParameterSummaryItem(**PARAMETER)],
    }
    payload.update(overrides)
    return FdcSummaryToolResult(**payload)


class TestFdcSummaryContract:
    def test_success_allows_model_not_ready_as_nullable_evidence(self) -> None:
        result = _fdc_success()

        assert result.anomaly is None

    def test_success_accepts_structured_verified_anomaly_signal(self) -> None:
        result = _fdc_success(anomaly=AnomalySignal(**VERIFIED_SIGNAL))

        assert result.anomaly is not None
        assert result.anomaly.is_anomaly is True
        assert result.anomaly.threshold_validation_status == "VERIFIED"

    def test_score_only_signal_is_evidence_but_not_action_gate(self) -> None:
        result = _fdc_success(anomaly=AnomalySignal(**SCORE_ONLY_SIGNAL))

        assert result.anomaly is not None
        assert result.anomaly.score == 0.71
        assert result.anomaly.is_anomaly is None
        assert result.anomaly.threshold_validation_status == "UNVERIFIED"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("display_threshold", 0.65),
            ("is_anomaly", True),
        ],
    )
    def test_display_threshold_and_flag_are_a_consistent_pair(
        self,
        field: str,
        value: object,
    ) -> None:
        with pytest.raises(ValidationError, match="함께 제공"):
            AnomalySignal(**SCORE_ONLY_SIGNAL, **{field: value})

        with pytest.raises(ValidationError, match="일치"):
            AnomalySignal(
                **SCORE_ONLY_SIGNAL,
                display_threshold=0.65,
                is_anomaly=False,
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("action_threshold", 0.68),
            ("threshold_version", "ACTION-THRESHOLD-V1"),
        ],
    )
    def test_action_threshold_requires_versioned_pair(
        self,
        field: str,
        value: object,
    ) -> None:
        with pytest.raises(ValidationError, match="함께 제공"):
            AnomalySignal(**SCORE_ONLY_SIGNAL, **{field: value})

    def test_verified_threshold_requires_complete_action_bundle(self) -> None:
        with pytest.raises(ValidationError, match="VERIFIED"):
            AnomalySignal(
                **{
                    **SCORE_ONLY_SIGNAL,
                    "threshold_validation_status": "VERIFIED",
                }
            )

    def test_action_threshold_must_not_be_below_display_threshold(self) -> None:
        with pytest.raises(ValidationError, match="작을 수 없습니다"):
            AnomalySignal(
                **{
                    **VERIFIED_SIGNAL,
                    "action_threshold": 0.64,
                }
            )

        boundary = AnomalySignal(
            **{
                **VERIFIED_SIGNAL,
                "action_threshold": 0.65,
            }
        )
        assert boundary.action_threshold == boundary.display_threshold

    def test_unverified_candidate_threshold_cannot_gate_action(self) -> None:
        signal = AnomalySignal(
            **SCORE_ONLY_SIGNAL,
            action_threshold=0.68,
            threshold_version="CANDIDATE-V1",
        )

        assert signal.threshold_validation_status == "UNVERIFIED"

    @pytest.mark.parametrize("missing", ["model_version", "score_method"])
    def test_action_gate_signal_requires_model_provenance(self, missing: str) -> None:
        payload = {
            key: value for key, value in VERIFIED_SIGNAL.items() if key != missing
        }

        with pytest.raises(ValidationError):
            AnomalySignal(**payload)

    def test_runtime_signal_rejects_synthetic_label(self) -> None:
        with pytest.raises(ValidationError):
            AnomalySignal(**SCORE_ONLY_SIGNAL, synthetic_label="ANOMALY")
        assert "synthetic_label" not in AnomalySignal.model_fields

    def test_threshold_status_is_exported_in_json_schema(self) -> None:
        schema = AnomalySignal.model_json_schema()

        assert schema["$defs"]["ThresholdValidationStatus"]["enum"] == [
            "VERIFIED",
            "UNVERIFIED",
        ]

    def test_legacy_scalar_anomaly_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _fdc_success(anomaly_score=0.71)

    @pytest.mark.parametrize("missing", ["wafer", "parameters"])
    def test_rule_data_is_required_on_success(self, missing: str) -> None:
        with pytest.raises(ValidationError, match=missing):
            _fdc_success(**{missing: None if missing == "wafer" else []})

    def test_parameter_terminology_is_canonical(self) -> None:
        item = ParameterSummaryItem(**PARAMETER)

        assert item.parameter_id == "PH_FOCUS"
        assert item.alarm_type is AlarmType.OOS
        assert "sensor_id" not in item.model_fields
        assert "judgement" not in item.model_fields

    @pytest.mark.parametrize("field", ["point_cnt", "ooc_point_cnt", "oos_point_cnt"])
    def test_parameter_counts_are_nonnegative(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ParameterSummaryItem(**{**PARAMETER, field: -1})


READY_INCIDENT_SIGNAL = {
    "enabled": True,
    "status": IncidentModelSignalStatus.READY,
    "incident_score": 0.83,
    # 임의 계약 fixture이며 운영 threshold가 아니다.
    "display_threshold": 0.65,
    "action_threshold": 0.68,
    "expected_member_count": 3,
    "valid_member_count": 3,
    "max_score_lot_hist_id": "LH-00183",
    "model_version": "IFOREST-20260814",
    "score_method": "MINMAX-V1",
    "threshold_version": "ACTION-THRESHOLD-V1",
    "action_policy_version": "ACTION-POLICY-V1",
    "reason": "",
}


class TestIncidentModelSignalContract:
    def test_ready_requires_complete_full_coverage_bundle(self) -> None:
        signal = IncidentModelSignal(**READY_INCIDENT_SIGNAL)

        assert signal.status is IncidentModelSignalStatus.READY
        assert signal.expected_member_count == signal.valid_member_count == 3

    @pytest.mark.parametrize(
        "missing",
        [
            "incident_score",
            "display_threshold",
            "action_threshold",
            "max_score_lot_hist_id",
            "model_version",
            "score_method",
            "threshold_version",
        ],
    )
    def test_ready_requires_complete_provenance_and_threshold(
        self,
        missing: str,
    ) -> None:
        payload = {key: value for key, value in READY_INCIDENT_SIGNAL.items()}
        payload[missing] = None

        with pytest.raises(ValidationError, match="완전한"):
            IncidentModelSignal(**payload)

    @pytest.mark.parametrize(
        ("expected", "valid"),
        [(3, 0), (3, 2)],
    )
    def test_ready_requires_positive_full_coverage(
        self,
        expected: int,
        valid: int,
    ) -> None:
        with pytest.raises(ValidationError, match="full coverage"):
            IncidentModelSignal(
                **{
                    **READY_INCIDENT_SIGNAL,
                    "expected_member_count": expected,
                    "valid_member_count": valid,
                }
            )

    def test_ready_requires_enabled_and_empty_reason(self) -> None:
        with pytest.raises(ValidationError, match="enabled=true"):
            IncidentModelSignal(**{**READY_INCIDENT_SIGNAL, "enabled": False})
        with pytest.raises(ValidationError, match="reason은 빈"):
            IncidentModelSignal(
                **{**READY_INCIDENT_SIGNAL, "reason": "unexpected fallback"}
            )

    def test_ready_action_threshold_is_not_below_display_threshold(self) -> None:
        with pytest.raises(ValidationError, match="작을 수 없습니다"):
            IncidentModelSignal(**{**READY_INCIDENT_SIGNAL, "action_threshold": 0.64})

        boundary = IncidentModelSignal(
            **{**READY_INCIDENT_SIGNAL, "action_threshold": 0.65}
        )
        assert boundary.action_threshold == boundary.display_threshold

    @pytest.mark.parametrize(
        ("status", "enabled"),
        [
            (IncidentModelSignalStatus.DISABLED, False),
            (IncidentModelSignalStatus.UNAVAILABLE, True),
        ],
    )
    def test_non_ready_is_explicit_and_carries_no_action_bundle(
        self,
        status: IncidentModelSignalStatus,
        enabled: bool,
    ) -> None:
        signal = IncidentModelSignal(
            enabled=enabled,
            status=status,
            expected_member_count=3,
            valid_member_count=0,
            action_policy_version="ACTION-POLICY-V1",
            reason=f"{status}: action 적용 불가",
        )

        assert signal.incident_score is None
        assert signal.action_threshold is None

    @pytest.mark.parametrize(
        ("status", "enabled"),
        [
            (IncidentModelSignalStatus.DISABLED, True),
            (IncidentModelSignalStatus.UNAVAILABLE, False),
        ],
    )
    def test_non_ready_enabled_flag_matches_status(
        self,
        status: IncidentModelSignalStatus,
        enabled: bool,
    ) -> None:
        with pytest.raises(ValidationError, match="enabled"):
            IncidentModelSignal(
                enabled=enabled,
                status=status,
                expected_member_count=3,
                valid_member_count=0,
                action_policy_version="ACTION-POLICY-V1",
                reason="fallback",
            )

    @pytest.mark.parametrize(
        "status, enabled",
        [
            (IncidentModelSignalStatus.DISABLED, False),
            (IncidentModelSignalStatus.UNAVAILABLE, True),
        ],
    )
    def test_non_ready_requires_reason(
        self,
        status: IncidentModelSignalStatus,
        enabled: bool,
    ) -> None:
        with pytest.raises(ValidationError, match="reason"):
            IncidentModelSignal(
                enabled=enabled,
                status=status,
                expected_member_count=3,
                valid_member_count=0,
                action_policy_version="ACTION-POLICY-V1",
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("incident_score", 0.71),
            ("display_threshold", 0.65),
            ("action_threshold", 0.68),
            ("max_score_lot_hist_id", "LH-00181"),
            ("model_version", "IFOREST-V1"),
            ("score_method", "MINMAX-V1"),
            ("threshold_version", "THRESHOLD-V1"),
        ],
    )
    def test_non_ready_rejects_action_bundle_fields(
        self,
        field: str,
        value: object,
    ) -> None:
        with pytest.raises(ValidationError, match="action 적용 가능한"):
            IncidentModelSignal(
                enabled=True,
                status="UNAVAILABLE",
                expected_member_count=3,
                valid_member_count=2,
                action_policy_version="ACTION-POLICY-V1",
                reason="coverage incomplete",
                **{field: value},
            )

    def test_valid_member_count_cannot_exceed_expected(self) -> None:
        with pytest.raises(ValidationError, match="초과"):
            IncidentModelSignal(
                enabled=True,
                status="UNAVAILABLE",
                expected_member_count=2,
                valid_member_count=3,
                action_policy_version="ACTION-POLICY-V1",
                reason="invalid aggregation",
            )

    def test_wafer_signal_cannot_be_reused_as_ready_incident_payload(self) -> None:
        wafer_payload = AnomalySignal(**VERIFIED_SIGNAL).model_dump()

        with pytest.raises(ValidationError):
            IncidentModelSignal(
                enabled=True,
                status="READY",
                expected_member_count=1,
                valid_member_count=1,
                max_score_lot_hist_id="LH-00181",
                action_policy_version="ACTION-POLICY-V1",
                **wafer_payload,
            )

    @pytest.mark.parametrize("field", ["synthetic_label", "member_signal"])
    def test_runtime_incident_signal_rejects_non_contract_inputs(
        self,
        field: str,
    ) -> None:
        with pytest.raises(ValidationError):
            IncidentModelSignal(**READY_INCIDENT_SIGNAL, **{field: "forbidden"})

    def test_action_policy_version_is_required_for_every_status(self) -> None:
        with pytest.raises(ValidationError):
            IncidentModelSignal(
                enabled=False,
                status="DISABLED",
                expected_member_count=1,
                valid_member_count=0,
                reason="model signal gate disabled",
            )

    def test_status_is_exported_in_json_schema(self) -> None:
        schema = IncidentModelSignal.model_json_schema()

        assert schema["$defs"]["IncidentModelSignalStatus"]["enum"] == [
            "READY",
            "DISABLED",
            "UNAVAILABLE",
        ]


class TestEquipmentContextContract:
    def test_success_returns_compact_context_and_graph_provenance(self) -> None:
        result = EquipmentContextToolResult(
            ok=True,
            **EQUIPMENT_CONTEXT,
            graph_revision="GRAPH-20260813",
        )

        assert result.process_step_id == "CT-ETCH"
        assert result.upstream_process_step_ids == ["CT-PHOTO"]
        assert result.downstream_process_step_ids == []
        assert result.graph_revision == "GRAPH-20260813"

    @pytest.mark.parametrize(
        "missing",
        [
            "chamber_id",
            "equipment_id",
            "area",
            "model_code",
            "process_step_id",
            "graph_revision",
        ],
    )
    def test_core_context_fields_are_required_on_success(
        self,
        missing: str,
    ) -> None:
        payload = {
            "ok": True,
            **EQUIPMENT_CONTEXT,
            "graph_revision": "GRAPH-20260813",
        }
        payload[missing] = None

        with pytest.raises(ValidationError, match=missing):
            EquipmentContextToolResult(
                **payload,
            )

    def test_tool_context_excludes_graph_projection_and_api_only_fields(self) -> None:
        fields = set(EquipmentContextToolResult.model_fields)

        assert {
            "chamber_id",
            "equipment_id",
            "area",
            "model_code",
            "process_step_id",
            "graph_revision",
        } <= fields
        assert {
            "equipment",
            "step",
            "sibling_chambers",
            "adjacent_steps",
            "parameters",
            "relations",
            "relation_ids",
            "nodes",
            "relationships",
            "context",
            "provenance",
        }.isdisjoint(fields)
        assert {
            "relation_ids",
            "nodes",
            "relationships",
            "recipe_id",
            "recipe_step_no",
            "area_name",
            "model_name",
            "process_step_seq",
        }.isdisjoint(fields)
        assert {
            "upstream_process_step_ids",
            "downstream_process_step_ids",
            "sibling_chamber_ids",
        } <= fields


class TestDocumentContract:
    def test_hit_uses_stable_document_and_chunk_ids_without_revision(self) -> None:
        hit = DocumentHit(**DOCUMENT)

        assert hit.document_id == "DOC-001"
        assert hit.chunk_id == "CHK-001"
        assert "corpus_revision" not in DocumentHit.model_fields

        missing_document_id = {
            key: value for key, value in DOCUMENT.items() if key != "document_id"
        }
        with pytest.raises(ValidationError):
            DocumentHit(**missing_document_id)

    def test_zero_hits_is_success(self) -> None:
        assert DocumentSearchToolResult(ok=True).hits == []


class TestSendActionContract:
    def test_input_accepts_only_action_id(self) -> None:
        request = SendActionToolInput(action_id="ACT-001")

        assert request.action_id == "ACT-001"
        with pytest.raises(ValidationError):
            SendActionToolInput(action_id="ACT-001", agent_run_id="RUN-001")

    def test_success_returns_channel_wise_state(self) -> None:
        result = SendActionToolResult(
            ok=True,
            action_id="ACT-001",
            deliveries=[
                ChannelDeliveryResult(
                    channel=DeliveryChannel.EMAIL,
                    status=DeliveryStatus.SENT,
                    sent=True,
                    duplicate=False,
                ),
                ChannelDeliveryResult(
                    channel=DeliveryChannel.MES_MOCK,
                    status=DeliveryStatus.BLOCKED,
                    sent=False,
                    duplicate=False,
                ),
            ],
        )

        assert result.deliveries[0].sent is True
        assert result.deliveries[1].status is DeliveryStatus.BLOCKED

    def test_duplicate_reports_no_new_external_effect(self) -> None:
        item = ChannelDeliveryResult(
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.SENT,
            sent=False,
            duplicate=True,
        )

        assert item.duplicate is True

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": DeliveryStatus.WAITING, "sent": True, "duplicate": False},
            {"status": DeliveryStatus.SENT, "sent": True, "duplicate": True},
        ],
    )
    def test_inconsistent_effect_flags_are_rejected(
        self,
        payload: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            ChannelDeliveryResult(channel=DeliveryChannel.EMAIL, **payload)

    def test_success_requires_nonempty_unique_channels(self) -> None:
        with pytest.raises(ValidationError, match="deliveries"):
            SendActionToolResult(ok=True, action_id="ACT-001")

        delivery = {
            "channel": DeliveryChannel.EMAIL,
            "status": DeliveryStatus.SENT,
            "sent": True,
            "duplicate": False,
        }
        with pytest.raises(ValidationError, match="중복"):
            SendActionToolResult(
                ok=True,
                action_id="ACT-001",
                deliveries=[delivery, delivery],
            )

    def test_unknown_delivery_state_is_supported(self) -> None:
        item = ChannelDeliveryResult(
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.UNKNOWN,
            sent=False,
            duplicate=False,
        )

        assert item.status is DeliveryStatus.UNKNOWN


class TestToolFailureInvariant:
    @pytest.mark.parametrize("prefix", REASON_PREFIXES)
    def test_allowed_reason_prefixes(self, prefix: str) -> None:
        result = DocumentSearchToolResult(ok=False, reason=f"{prefix} 상세")

        assert result.hits == []

    @pytest.mark.parametrize("reason", ["", "ERROR: 실패", "not_found: 없음"])
    def test_unknown_reason_prefix_is_rejected(self, reason: str) -> None:
        with pytest.raises(ValidationError, match="접두어"):
            DocumentSearchToolResult(ok=False, reason=reason)

    def test_failure_rejects_nonempty_object_and_list_payload(self) -> None:
        with pytest.raises(ValidationError, match="None"):
            FdcSummaryToolResult(
                ok=False,
                reason="NOT_FOUND: 없음",
                wafer=WaferContext(**WAFER),
            )

        with pytest.raises(ValidationError, match="비어야"):
            SendActionToolResult(
                ok=False,
                reason="TIMEOUT: 응답 유실",
                deliveries=[
                    {
                        "channel": "EMAIL",
                        "status": "UNKNOWN",
                        "sent": False,
                        "duplicate": False,
                    }
                ],
            )

    def test_fail_helper_builds_empty_payload(self) -> None:
        result = fail(FdcSummaryToolResult, "MODEL_NOT_READY: 모델 없음")

        assert result.ok is False
        assert result.wafer is None
        assert result.parameters == []
        assert result.anomaly is None


class TestSharedToolRules:
    def test_all_success_results_require_empty_reason(self) -> None:
        with pytest.raises(ValidationError, match="빈 문자열"):
            DocumentSearchToolResult(ok=True, reason="성공")

    @pytest.mark.parametrize("model", TOOL_RESULT_MODELS.values())
    def test_result_does_not_contain_runtime_metadata(self, model: type) -> None:
        assert {"latency_ms", "called_at"}.isdisjoint(model.model_fields)

    @pytest.mark.parametrize("model", TOOL_RESULT_MODELS.values())
    def test_extra_fields_are_forbidden(self, model: type) -> None:
        with pytest.raises(ValidationError):
            model(ok=False, reason="NOT_FOUND: 없음", latency_ms=1)

    def test_tool_registry_and_agent_budget_scope(self) -> None:
        assert set(TOOL_RESULT_MODELS) == {
            "get_fdc_summary",
            "get_equipment_context",
            "search_documents",
            "send_action",
            "generate_analysis_plan",
        }
        assert "generate_analysis_plan" not in AGENT_TOOL_NAMES


class TestInputBoundaries:
    @pytest.mark.parametrize(
        "model, field",
        [
            (FdcSummaryToolInput, "lot_hist_id"),
            (EquipmentContextToolInput, "chamber_id"),
        ],
    )
    def test_tool_identifier_limit(self, model: type, field: str) -> None:
        assert model(**{field: "A" * 20})
        with pytest.raises(ValidationError):
            model(**{field: "A" * 21})

    @pytest.mark.parametrize("top_k", [1, 4, 10])
    def test_document_top_k_range(self, top_k: int) -> None:
        assert DocumentSearchToolInput(query="정비", top_k=top_k).top_k == top_k

    @pytest.mark.parametrize("top_k", [0, 11])
    def test_document_top_k_rejects_out_of_range(self, top_k: int) -> None:
        with pytest.raises(ValidationError):
            DocumentSearchToolInput(query="정비", top_k=top_k)

    def test_analysis_plan_contract_is_unchanged(self) -> None:
        result = AnalysisPlanToolResult(
            ok=True,
            sql="SELECT count(*) FROM v_alarm_event",
            metric=MetricPlan(type="count"),
            visualization=VisualizationPlan(chart_type="table"),
        )

        assert result.group_by == []
        assert AnalysisPlanToolInput(question="알람 수").question == "알람 수"


class TestToolEnvelopeExactness:
    """Tool 5종 signature와 envelope 고정 (`V5-CM-4.1` 묶음 3)."""

    def test_tool_inputs_have_the_exact_signature(self) -> None:
        """API v3 §2의 5종 signature와 field가 exact하게 같다."""

        from app.common import tool_contracts as contracts

        expected = {
            "FdcSummaryToolInput": {"lot_hist_id"},
            "EquipmentContextToolInput": {"chamber_id"},
            "DocumentSearchToolInput": {"query", "model_code", "top_k"},
            "SendActionToolInput": {"action_id"},
            "AnalysisPlanToolInput": {"question"},
        }
        for name, fields in expected.items():
            assert set(getattr(contracts, name).model_fields) == fields, name

    def test_document_search_defaults_match_the_signature(self) -> None:
        """`search_documents(query, model_code=None, top_k=4)`."""

        from app.common.tool_contracts import DocumentSearchToolInput

        parsed = DocumentSearchToolInput(query="ET_CF4")
        assert parsed.model_code is None
        assert parsed.top_k == 4

    def test_no_tool_result_carries_runtime_metadata(self) -> None:
        """`latency_ms`·`status`·`called_at`은 Tool payload가 아니다.

        실행 wrapper가 `agent_tool_call` metadata로 한 번 기록한다(API v3 §2).
        wrapper 구현은 `V5-C-0.1` 소유다.
        """

        from app.common import tool_contracts as contracts

        results = [
            contracts.FdcSummaryToolResult,
            contracts.EquipmentContextToolResult,
            contracts.DocumentSearchToolResult,
            contracts.SendActionToolResult,
            contracts.AnalysisPlanToolResult,
        ]
        for model in results:
            leaked = {"latency_ms", "status", "called_at"} & set(model.model_fields)
            assert not leaked, f"{model.__name__}에 runtime metadata: {leaked}"

    def test_runtime_metadata_lives_on_the_tool_call_item(self) -> None:
        """분리 계약의 반대편 — 기록 DTO에는 있어야 한다."""

        from app.agent.schemas import ToolCallItem

        assert {"latency_ms", "status", "called_at"} <= set(ToolCallItem.model_fields)

    def test_a_runtime_metadata_field_is_refused_by_the_envelope(self) -> None:
        """`extra="forbid"`가 Tool 결과에 metadata를 붙이지 못하게 한다."""

        from app.common.tool_contracts import AnalysisPlanToolResult

        with pytest.raises(ValidationError):
            AnalysisPlanToolResult(ok=False, reason="TIMEOUT: x", latency_ms=12)

    def test_reason_prefixes_match_the_api_contract(self) -> None:
        """API v3 §2의 reason prefix 목록과 exact하게 같다."""

        from app.common.tool_contracts import REASON_PREFIXES

        assert REASON_PREFIXES == (
            "NOT_FOUND:",
            "TIMEOUT:",
            "MODEL_NOT_READY:",
            "LLM_NOT_READY:",
            "GRAPH_SHAPE_ERROR:",
            "DEPENDENCY_ERROR:",
            "POLICY_REJECTED:",
            "IDEMPOTENCY_CONFLICT:",
        )

    def test_an_unlisted_reason_prefix_is_refused(self) -> None:
        from app.common.tool_contracts import AnalysisPlanToolResult

        with pytest.raises(ValidationError):
            AnalysisPlanToolResult(ok=False, reason="UNKNOWN: 무언가")
