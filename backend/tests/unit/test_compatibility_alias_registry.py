"""V5-CM-4.4-3 compatibility alias registry contract."""

from __future__ import annotations

import importlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.common.tool_contracts import DocumentHit as ToolDocumentHit

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "deliverables"
    / "api"
    / "compatibility_alias_registry.json"
)
API_MARKDOWN = (
    REPOSITORY_ROOT / "docs" / "deliverables" / "api" / "API명세서_v3_작업본.md"
)

_COMMON_REQUIRED = {
    "id",
    "kind",
    "owner",
    "symbol",
    "consumers",
    "removal_conditions",
    "change_points",
    "gate",
}
_DTO_REQUIRED = _COMMON_REQUIRED | {
    "projection",
    "canonical_fields",
    "compatibility_fields",
    "enforcement",
}
_DTO_ALLOWED = _DTO_REQUIRED | {"producer", "derivation"}
_TRANSPORT_REQUIRED = _COMMON_REQUIRED | {
    "replacement",
    "replacement_kind",
}
_TRANSPORT_ALLOWED = _TRANSPORT_REQUIRED | {"projection", "export_alias_of"}
_REMOVAL_CHECKS = [
    "consumer_zero",
    "canonical_contract_green",
    "baseline_revision_ready",
]
_PYTHON_SYMBOL = re.compile(r"^backend(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_FRONTEND_SYMBOL = re.compile(
    r"^frontend/src/shared/api/[a-z-]+\.js#[A-Za-z_$][A-Za-z0-9_$]*$"
)
_ENDPOINT = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /[A-Za-z0-9_/{}/.-]+$")


def _load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _resolve_python_symbol(symbol: str) -> Any:
    parts = symbol.split(".")
    assert parts.pop(0) == "backend"
    for split_at in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:split_at])
        try:
            value: Any = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        for attribute in parts[split_at:]:
            value = getattr(value, attribute)
        return value
    raise AssertionError(f"import 가능한 module을 찾지 못했습니다: {symbol}")


def _assert_unique(values: list[Any], label: str) -> None:
    serialized = [
        json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values
    ]
    assert len(serialized) == len(set(serialized)), f"중복 목록: {label}"


def _path_part(reference: str) -> str:
    return reference.split("#", 1)[0]


def test_registry_root_and_entries_are_well_formed() -> None:
    registry = _load_registry()

    assert set(registry) == {"format_version", "gate", "entries"}
    assert registry["format_version"] == 1
    assert registry["gate"] == "V5-CM-5.1"
    entries = registry["entries"]
    assert entries
    ids = [entry["id"] for entry in entries]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))

    for entry in entries:
        assert re.fullmatch(r"[a-d]-[a-z0-9-]+", entry["id"])
        assert entry["owner"] in {"A", "B", "C", "D", "CM"}
        assert entry["gate"] == registry["gate"]
        assert entry["kind"] in {"dto_field", "transport"}
        assert entry.get("projection") in {"copy", "derived", "placeholder"}
        assert isinstance(entry["consumers"], list)
        assert isinstance(entry["change_points"], list) and entry["change_points"]
        _assert_unique(entry["consumers"], f"{entry['id']}.consumers")
        _assert_unique(entry["change_points"], f"{entry['id']}.change_points")
        assert entry["consumers"] == sorted(entry["consumers"])
        assert entry["change_points"] == sorted(entry["change_points"])
        assert [item["check"] for item in entry["removal_conditions"]] == (
            _REMOVAL_CHECKS
        )
        assert all(item["criteria"].strip() for item in entry["removal_conditions"])

        for reference in [*entry["consumers"], *entry["change_points"]]:
            path = REPOSITORY_ROOT / _path_part(reference)
            assert path.is_file(), f"선언 경로가 파일이 아닙니다: {reference}"

        if entry["kind"] == "dto_field":
            assert set(entry) <= _DTO_ALLOWED
            assert _DTO_REQUIRED <= set(entry)
            assert _PYTHON_SYMBOL.fullmatch(entry["symbol"])
            assert entry["enforcement"] in {"validator", "producer"}
            assert entry["compatibility_fields"]
            _assert_unique(entry["canonical_fields"], f"{entry['id']}.canonical_fields")
            _assert_unique(
                entry["compatibility_fields"],
                f"{entry['id']}.compatibility_fields",
            )
            if entry["projection"] == "placeholder":
                assert entry["canonical_fields"] == []
            else:
                assert entry["canonical_fields"]
            if entry["projection"] in {"derived", "placeholder"}:
                assert entry.get("derivation", "").strip()
            if entry["enforcement"] == "producer":
                assert _PYTHON_SYMBOL.fullmatch(entry["producer"])
                assert callable(_resolve_python_symbol(entry["producer"]))
            else:
                assert "producer" not in entry
        else:
            assert set(entry) <= _TRANSPORT_ALLOWED
            assert _TRANSPORT_REQUIRED <= set(entry)
            assert _FRONTEND_SYMBOL.fullmatch(entry["symbol"])
            assert entry["replacement_kind"] in {"export", "endpoint"}
            if entry["replacement_kind"] == "export":
                assert _FRONTEND_SYMBOL.fullmatch(entry["replacement"])
            else:
                assert _ENDPOINT.fullmatch(entry["replacement"])
            if "export_alias_of" in entry:
                assert _FRONTEND_SYMBOL.fullmatch(entry["export_alias_of"])


def test_registered_dto_symbols_and_fields_exist() -> None:
    entries = _load_registry()["entries"]
    for entry in entries:
        if entry["kind"] != "dto_field":
            continue
        schema = _resolve_python_symbol(entry["symbol"])
        assert isinstance(schema, type) and issubclass(schema, BaseModel)
        registered_fields = {
            *entry["canonical_fields"],
            *entry["compatibility_fields"],
        }
        assert registered_fields <= set(schema.model_fields), entry["id"]


def _alarm_payload() -> dict[str, Any]:
    return {
        "source": "SUMMARY",
        "alarm_id": "SAL-0001",
        "occurred_at": datetime(2026, 8, 1, tzinfo=UTC),
        "area": "Etch",
        "equipment_id": "EQP01",
        "equipment": "EQP01",
        "chamber_id": "EQP01-PM1",
        "chamber": "EQP01-PM1",
        "recipe_id": "RECIPE01",
        "recipe": "RECIPE01",
        "lot_id": "LOT001",
        "lot": "LOT001",
        "wafer_id": "LOT001W001",
        "wafer": "LOT001W001",
        "parameter_id": "ET_CF4",
        "parameter": "ET_CF4",
        "recipe_step_no": 1,
        "step_no": 1,
        "seq_no": None,
        "alarm_type": "OOC",
        "value": 81.0,
        "rule_code": "SUMMARY_OOC",
        "predicted_fault_code": None,
        "fault": None,
        "action_code": None,
        "notify_status": None,
        "notify": False,
        "mes_status": None,
        "mes": "",
        "statistic_type": "mean",
        "cl": 80.0,
        "ucl": 83.0,
        "lcl": 77.0,
    }


def _parameter_payload() -> dict[str, Any]:
    return {
        "parameter_id": "ET_CF4",
        "parameter_name": "CF4 Flow",
        "name": "CF4 Flow",
        "area": "Etch",
        "unit": "sccm",
        "spec_lower": 74.0,
        "LSL": 74.0,
        "ctrl_lower": 76.4,
        "LCL": 76.4,
        "target_value": 80.0,
        "TARGET": 80.0,
        "ctrl_upper": 83.6,
        "UCL": 83.6,
        "spec_upper": 86.0,
        "USL": 86.0,
        "upper_only": False,
    }


def _run_payload() -> dict[str, Any]:
    return {
        "agent_run_id": "RUN-0001",
        "created_at": datetime(2026, 8, 1, tzinfo=UTC),
        "alarm_source": "TRACE",
        "alarm_id": "TAL-0001",
        "chamber_id": "EQP01-PM1",
        "chamber": "EQP01-PM1",
        "predicted_fault_code": "RFM",
        "fault_code": "RFM",
        "fault_name": None,
        "fault_color": None,
        "confidence": 0.8,
        "recommended_action": "WARNING",
        "status": "COMPLETED",
        "action_id": "ACT-0001",
        "approval_id": None,
        "tools": [],
        "deliveries": [{"channel": "EMAIL", "status": "SENT"}],
        "latency_ms": 10,
        "llm_model": "test-model",
    }


def _approval_payload() -> dict[str, Any]:
    decided_at = datetime(2026, 8, 1, tzinfo=UTC)
    return {
        "approval_id": "APR-0001",
        "agent_run_id": "RUN-0001",
        "action_id": "ACT-0001",
        "created_at": decided_at,
        "lot_id": "LOT001",
        "lot": "LOT001",
        "equipment_id": "EQP01",
        "equipment": "EQP01",
        "chamber_id": "EQP01-PM1",
        "chamber": "EQP01-PM1",
        "predicted_fault_code": "RFM",
        "fault_code": "RFM",
        "action_code": "EQP_HOLD",
        "reason": "controlled test",
        "status": "APPROVED",
        "decided_by": "operator",
        "decided_at": decided_at,
        "decision_comment": "approved",
        "approved_by": "operator",
        "approved_at": decided_at,
    }


def _action_payload() -> dict[str, Any]:
    return {
        "action_id": "ACT-0001",
        "agent_run_id": "RUN-0001",
        "created_by_agent_run_id": "RUN-0001",
        "action_code": "MONITORING",
        "lot_id": "LOT001",
        "lot": "LOT001",
        "equipment_id": "EQP01",
        "equipment": "EQP01",
        "chamber_id": "EQP01-PM1",
        "chamber": "EQP01-PM1",
        "reason": "controlled test",
        "approval_status": None,
        "deliveries": [],
        "created_at": datetime(2026, 8, 1, tzinfo=UTC),
    }


def _ask_payload() -> dict[str, Any]:
    return {
        "title": "analysis",
        "answer": "answer",
        "tools": [],
        "predicted_fault_code": "RFM",
        "confidence": 0.8,
        "recommended_action": "WARNING",
        "evidence_items": [
            {
                "type": "DOCUMENT",
                "source_id": "CHK-0001",
                "title": "guide",
                "excerpt": "evidence",
                "document_id": "DOC-0001",
                "chunk_id": "CHK-0001",
                "section": None,
            }
        ],
        "limitations": ["pilot"],
        "evidence": {
            "doc_id": "DOC-0001",
            "document_id": "DOC-0001",
            "chunk_id": "CHK-0001",
            "section": None,
        },
        "limit": "pilot",
    }


def _validator_cases() -> dict[str, tuple[Any, dict[str, Any], str, Any]]:
    agent = importlib.import_module("app.agent.public_schemas")
    detection = importlib.import_module("app.detection.public_schemas")
    return {
        "a-dto-alarm-copy": (
            detection.AlarmItem,
            _alarm_payload(),
            "equipment",
            "OTHER",
        ),
        "a-dto-alarm-delivery-derived": (
            detection.AlarmItem,
            _alarm_payload(),
            "notify",
            True,
        ),
        "a-dto-parameter-copy": (
            detection.ParameterItem,
            _parameter_payload(),
            "name",
            "OTHER",
        ),
        "c-dto-action-copy": (
            agent.ActionItem,
            _action_payload(),
            "created_by_agent_run_id",
            "RUN-OTHER",
        ),
        "c-dto-agent-ask-derived": (
            agent.AgentAskResponse,
            _ask_payload(),
            "limit",
            "OTHER",
        ),
        "c-dto-agent-run-copy": (
            agent.PublicAgentRunItem,
            _run_payload(),
            "chamber",
            "OTHER",
        ),
        "c-dto-agent-run-placeholder": (
            agent.PublicAgentRunItem,
            _run_payload(),
            "fault_name",
            "invented",
        ),
        "c-dto-approval-decision-copy": (
            agent.PublicApprovalItem,
            _approval_payload(),
            "approved_by",
            "OTHER",
        ),
        "c-dto-approval-identity-copy": (
            agent.PublicApprovalItem,
            _approval_payload(),
            "lot",
            "OTHER",
        ),
        "c-dto-ask-document-copy": (
            agent.AskDocumentEvidenceAlias,
            _ask_payload()["evidence"],
            "doc_id",
            "OTHER",
        ),
        "c-dto-chat-tool-copy": (
            agent.AskToolItem,
            {
                "tool_name": "search_documents",
                "status": "SUCCESS",
                "result_summary": "ok",
                "name": "search_documents",
                "result": "ok",
            },
            "name",
            "OTHER",
        ),
        "c-dto-public-tool-copy": (
            agent.PublicToolCallItem,
            {
                "tool_name": "get_fdc_summary",
                "status": "SUCCESS",
                "result_summary": "ok",
                "n": "get_fdc_summary",
                "s": "SUCCESS",
            },
            "n",
            "OTHER",
        ),
    }


def test_validator_backed_entries_reject_mismatched_projection() -> None:
    entries = {
        entry["id"]: entry
        for entry in _load_registry()["entries"]
        if entry["kind"] == "dto_field" and entry["enforcement"] == "validator"
    }
    cases = _validator_cases()
    assert set(cases) == set(entries)

    for _entry_id, (schema, payload, field, wrong_value) in cases.items():
        schema.model_validate(payload)
        mutated = deepcopy(payload)
        mutated[field] = wrong_value
        with pytest.raises(ValidationError):
            schema.model_validate(mutated)


def test_document_hit_producer_copies_document_id() -> None:
    from app.knowledge.schemas import DocumentHit

    hit = ToolDocumentHit(
        chunk_id="CHK-0001",
        document_id="DOC-0001",
        title="guide",
        section=None,
        score=0.9,
        content="evidence",
        model_code=None,
    )
    projected = DocumentHit.from_tool_hit(hit)
    assert projected.doc_id == projected.document_id == "DOC-0001"

    router_source = (
        REPOSITORY_ROOT / "backend" / "app" / "knowledge" / "router.py"
    ).read_text(encoding="utf-8")
    assert "DocumentHit.from_tool_hit(hit)" in router_source


def test_audit_producer_builds_copy_and_derived_aliases() -> None:
    from app.analytics.audit import _to_item

    projected = _to_item(
        {
            "audit_id": 1,
            "occurred_at": datetime(2026, 8, 1, tzinfo=UTC),
            "actor_type": "HUMAN",
            "actor_id": "operator",
            "event_type": "APPROVAL_DECIDED",
            "entity_type": "APPROVAL",
            "entity_id": "APR-0001",
            "before_json": None,
            "after_json": {"status": "APPROVED"},
            "detail": None,
        }
    )

    assert projected.at == projected.occurred_at
    assert projected.actor == projected.actor_type == "HUMAN"
    assert projected.event == "APPROVE"
    assert projected.entity == "APPROVAL:APR-0001"

    audit_source = (
        REPOSITORY_ROOT / "backend" / "app" / "analytics" / "audit.py"
    ).read_text(encoding="utf-8")
    assert "return [_to_item(row) for row in rows]" in audit_source


def test_api_markdown_declares_registry_authority_and_endpoint_replacements() -> None:
    markdown = API_MARKDOWN.read_text(encoding="utf-8")
    assert markdown.count("compatibility_alias_registry.json") == 2
    assert "외부 계약 정본은 이\nAPI 명세서" in markdown
    assert "ActionItem·ActionDetailResponse" in markdown
    assert "`created_by_agent_run_id`, `lot`, `equipment`, `chamber`" in markdown

    endpoint_replacements = {
        entry["replacement"]
        for entry in _load_registry()["entries"]
        if entry["kind"] == "transport" and entry["replacement_kind"] == "endpoint"
    }
    for replacement in endpoint_replacements:
        method, path = replacement.split(" ", 1)
        assert re.search(
            rf"\|\s*{re.escape(method)}\s*\|\s*`{re.escape(path)}`\s*\|",
            markdown,
        )
