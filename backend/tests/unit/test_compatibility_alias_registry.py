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
from scripts.alias_registry_scan import (
    derived_feature_consumer_paths as _derived_feature_consumer_paths,
)
from scripts.alias_registry_scan import (
    resolve_python_symbol as _resolve_python_symbol,
)
from scripts.alias_registry_scan import (
    source_reads_any_alias as _source_reads_any_alias,
)

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
BASELINE_PATH = (
    REPOSITORY_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "v5_cm_4_4"
    / "api_contract_baseline.json"
)
FEATURE_ROOT = REPOSITORY_ROOT / "frontend" / "src" / "features"

_COMMON_REQUIRED = {
    "id",
    "kind",
    "owner",
    "symbol",
    "consumers",
    "consumer_scan_ignores",
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


def _assert_unique(values: list[Any], label: str) -> None:
    serialized = [
        json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values
    ]
    assert len(serialized) == len(set(serialized)), f"중복 목록: {label}"


def _path_part(reference: str) -> str:
    return reference.split("#", 1)[0]


def _assert_reference_symbol_exists(reference: str) -> None:
    path_text, separator, symbol = reference.partition("#")
    if not separator:
        return
    assert symbol, f"빈 consumer symbol: {reference}"
    path = REPOSITORY_ROOT / path_text
    if path_text.startswith("backend/"):
        module = path.with_suffix("").relative_to(REPOSITORY_ROOT).as_posix()
        _resolve_python_symbol(f"{module.replace('/', '.')}.{symbol}")
        return
    source = path.read_text(encoding="utf-8")
    assert re.search(
        rf"\b(?:function|class|const|let|var)\s+{re.escape(symbol)}\b",
        source,
    ) or re.search(
        rf"export\s*\{{[^}}]*\b{re.escape(symbol)}\b", source
    ), f"consumer symbol이 선언되지 않았습니다: {reference}"


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
        assert isinstance(entry["consumer_scan_ignores"], list)
        assert isinstance(entry["change_points"], list) and entry["change_points"]
        _assert_unique(entry["consumers"], f"{entry['id']}.consumers")
        _assert_unique(
            entry["consumer_scan_ignores"],
            f"{entry['id']}.consumer_scan_ignores",
        )
        _assert_unique(entry["change_points"], f"{entry['id']}.change_points")
        assert entry["consumers"] == sorted(entry["consumers"])
        assert entry["consumer_scan_ignores"] == sorted(
            entry["consumer_scan_ignores"],
            key=lambda item: item["reference"],
        )
        for ignored in entry["consumer_scan_ignores"]:
            assert set(ignored) == {"reference", "reason"}
            assert ignored["reference"].startswith("frontend/src/features/")
            assert (REPOSITORY_ROOT / ignored["reference"]).is_file()
            assert ignored["reason"].strip()
            assert (
                "DTO/transport가 아닌 로컬 또는 다른 도메인 객체"
                not in (ignored["reason"])
            ), f"구체적인 ignore 근거가 필요합니다: {entry['id']}"
        assert entry["change_points"] == sorted(entry["change_points"])
        assert [item["check"] for item in entry["removal_conditions"]] == (
            _REMOVAL_CHECKS
        )
        assert all(item["criteria"].strip() for item in entry["removal_conditions"])

        for reference in [*entry["consumers"], *entry["change_points"]]:
            path = REPOSITORY_ROOT / _path_part(reference)
            assert path.is_file(), f"선언 경로가 파일이 아닙니다: {reference}"
        for reference in entry["consumers"]:
            _assert_reference_symbol_exists(reference)

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


def test_registered_feature_consumers_match_conservative_source_scan() -> None:
    """`consumer_zero`를 산문이 아니라 features/** 실제 source에서 도출한다."""

    for entry in _load_registry()["entries"]:
        declared = {
            _path_part(reference)
            for reference in entry["consumers"]
            if _path_part(reference).startswith("frontend/src/features/")
        }
        ignored = {item["reference"] for item in entry["consumer_scan_ignores"]}
        assert declared.isdisjoint(ignored), entry["id"]
        assert declared | ignored == _derived_feature_consumer_paths(entry), entry["id"]


def test_consumer_scan_recognizes_dot_and_bracket_alias_access() -> None:
    assert _source_reads_any_alias("row.doc_id", ["doc_id"])
    assert _source_reads_any_alias("row?.doc_id", ["doc_id"])
    assert _source_reads_any_alias("row['doc_id']", ["doc_id"])
    assert not _source_reads_any_alias("const doc_id = 'local'", ["doc_id"])


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


def test_agent_run_placeholder_registry_matches_schema_and_frozen_fixture() -> None:
    entry = next(
        item
        for item in _load_registry()["entries"]
        if item["id"] == "c-dto-agent-run-placeholder"
    )
    schema = _resolve_python_symbol(entry["symbol"])
    fixture = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    fields = fixture["components"]["AgentRunItem"]["fields"]

    assert entry["projection"] == "placeholder"
    assert entry["canonical_fields"] == []
    for field in entry["compatibility_fields"]:
        assert schema.model_fields[field].annotation is type(None)
        assert fields[field] == {
            "nullable": False,
            "required": True,
            "type": "null",
        }


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
    optional_section = markdown.split("### 5.2 선택 확장 API", 1)[1].split(
        "### 5.3 팀 release 필수 확장 API",
        1,
    )[0]
    optional_allowlist = {
        f"{method} {path}"
        for method, path in re.findall(
            r"\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`\s*\|",
            optional_section,
        )
    }
    assert endpoint_replacements <= optional_allowlist
    for replacement in endpoint_replacements:
        method, path = replacement.split(" ", 1)
        assert re.search(
            rf"\|\s*{re.escape(method)}\s*\|\s*`{re.escape(path)}`\s*\|",
            markdown,
        )
