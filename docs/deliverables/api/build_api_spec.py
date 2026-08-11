"""BISTel FDC API 명세서 v2.1 생성기.

Backend Pydantic DTO를 단일 기준으로 사용해 CSV·Markdown·PDF를 함께 만든다.
실행 위치와 무관하게 저장소의 ``backend/``를 import path에 추가한다.
"""

# ruff: noqa: E402, E501, I001

from __future__ import annotations

import csv
import html
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pydantic import BaseModel  # noqa: E402

from app.agent.schemas import (  # noqa: E402
    ActionDetailResponse,
    ActionPageResponse,
    AgentRunAcceptedResponse,
    AgentRunCreateRequest,
    AgentRunDetailResponse,
    AgentRunPageResponse,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalPageResponse,
)
from app.analytics.schemas import (  # noqa: E402
    AnalysisQueryRequest,
    AnalysisQueryResponse,
    AuditLogResponse,
    EvaluationListResponse,
    NlQueryHistoryResponse,
    SqlValidateRequest,
    SqlValidateResponse,
)
from app.detection.schemas import (  # noqa: E402
    AlarmItem,
    AlarmPageResponse,
    DashboardSummaryResponse,
    FdcSummaryResponse,
    TraceCatalogResponse,
    TraceSearchRequest,
    TraceSearchResponse,
)
from app.knowledge.schemas import (  # noqa: E402
    ChamberRelationResponse,
    DocumentDetailResponse,
    DocumentSearchRequest,
    DocumentSearchResponse,
    EquipmentRelationResponse,
)

OUT = Path(__file__).resolve().parent
VERSION = "v2.1"
CREATED = "2026.08.10"
MODIFIED = "2026.08.11"
TEAM = "PhotoEtch"
# 가나다순. 역할 표기(A/B/C/D)는 표지에 올리지 않는다.
AUTHORS = "강연권 · 방대혁 · 신동원 · 천승현"


@dataclass(frozen=True)
class Parameter:
    name: str
    location: str
    type: str
    required: bool = False
    default: str = "-"
    constraint: str = "-"
    description: str = ""


@dataclass(frozen=True)
class Endpoint:
    domain: str
    method: str
    path: str
    summary: str
    statuses: tuple[int, ...]
    response_model: type[BaseModel]
    request_model: type[BaseModel] | None = None
    parameters: tuple[Parameter, ...] = ()
    notes: tuple[str, ...] = ()


PAGE_PARAMS = (
    Parameter("page", "query", "integer", default="1", constraint=">= 1"),
    Parameter("size", "query", "integer", default="20", constraint="1..100"),
)


ENDPOINTS = (
    Endpoint(
        "A Detection",
        "GET",
        "/dashboard/summary",
        "알람 대시보드 요약",
        (200, 422),
        DashboardSummaryResponse,
        parameters=(
            Parameter(
                "date_from",
                "query",
                "date",
                description="생략 시 선택 계층의 최소 데이터 일자",
            ),
            Parameter(
                "date_to",
                "query",
                "date",
                description="생략 시 선택 계층의 최대 데이터 일자",
            ),
            Parameter("area", "query", "string"),
            Parameter("equipment_id", "query", "string"),
            Parameter("chamber_id", "query", "string"),
        ),
        notes=(
            "기간·계층 생략 시 date_range=2026-06-01..06-04, reference_date=06-04이며 알람/OOS/OOC는 51/37/14다.",
            "한쪽 기간 경계만 생략하면 선택 계층의 데이터 최소일 또는 최대일로 보완한다.",
            "pending_approvals는 날짜·계층 필터와 무관한 전체 PENDING 목록이다.",
            "top_sensors와 recent_alarms는 각각 5건이다.",
        ),
    ),
    Endpoint(
        "A Detection",
        "GET",
        "/summaries/{lot_hist_id}",
        "WAFER 센서 요약과 이상 점수",
        (200, 404, 503),
        FdcSummaryResponse,
        parameters=(
            Parameter("lot_hist_id", "path", "string", True, constraint="1..20"),
        ),
        notes=(
            "is_anomaly = anomaly_score >= anomaly_threshold 규칙을 서버가 보장한다.",
        ),
    ),
    Endpoint(
        "A Detection",
        "GET",
        "/alarms",
        "알람 목록",
        (200, 422),
        AlarmPageResponse,
        parameters=(
            Parameter("date_from", "query", "date"),
            Parameter("date_to", "query", "date"),
            Parameter("area", "query", "string"),
            Parameter("equipment_id", "query", "string"),
            Parameter("chamber_id", "query", "string"),
            Parameter("sensor_id", "query", "string"),
            Parameter("rule_id", "query", "enum(R01_OOS|R02_OOC|R03_CONSEC)"),
            Parameter("judgement", "query", "enum(IN_CONTROL|OOC|OOS)"),
            *PAGE_PARAMS,
        ),
        notes=("occurred_at DESC, alarm_id DESC로 정렬한다. 복수 필터는 AND다.",),
    ),
    Endpoint(
        "A Detection",
        "GET",
        "/alarms/{alarm_id}",
        "알람 상세",
        (200, 404),
        AlarmItem,
        parameters=(Parameter("alarm_id", "path", "string", True, constraint="1..20"),),
        notes=("목록과 상세는 동일 AlarmItem 계약을 사용한다.",),
    ),
    Endpoint(
        "A Detection",
        "GET",
        "/traces/catalog",
        "Trace 필터 선택지와 센서 한계선 조회",
        (200,),
        TraceCatalogResponse,
        notes=(
            "시계열 값은 포함하지 않는다. 실제 조회는 POST /traces/search를 사용한다.",
            "ET_REFL만 upper_only=true이며 하한값 null 여부로 추론하지 않는다.",
        ),
    ),
    Endpoint(
        "A Detection",
        "POST",
        "/traces/search",
        "파라미터·WAFER 다중 Trace 조회",
        (200, 422),
        TraceSearchResponse,
        TraceSearchRequest,
        notes=(
            "sensor_ids는 1개 이상이며 중복을 허용하지 않는다. wafer_nos도 중복 금지다.",
            "from과 to를 함께 주면 from < to여야 한다.",
            "total은 고유 WAFER 수가 아니라 (lot_hist_id, sensor_id) series 수다.",
        ),
    ),
    Endpoint(
        "B Knowledge",
        "GET",
        "/relations/chambers/{chamber_id}",
        "챔버 기준 관계 조회",
        (200, 404),
        ChamberRelationResponse,
        parameters=(Parameter("chamber_id", "path", "string", True),),
        notes=(
            "upstream/downstream은 equipment_id, sibling은 chamber_id 오름차순이다.",
        ),
    ),
    Endpoint(
        "B Knowledge",
        "GET",
        "/relations/equipment/{equipment_id}",
        "설비 기준 관계 조회",
        (200, 404),
        EquipmentRelationResponse,
        parameters=(Parameter("equipment_id", "path", "string", True),),
    ),
    Endpoint(
        "B Knowledge",
        "POST",
        "/documents/search",
        "장비 문서 벡터 검색",
        (200, 422, 503),
        DocumentSearchResponse,
        DocumentSearchRequest,
        notes=(
            "top_k 기본 4, 허용 1..10이다. 결과 0건은 200 + 빈 hits다.",
            "score DESC, document_id ASC, chunk_id ASC로 안정 정렬한다.",
        ),
    ),
    Endpoint(
        "B Knowledge",
        "GET",
        "/documents/{document_id}",
        "문서 메타데이터와 청크 목록",
        (200, 404),
        DocumentDetailResponse,
        parameters=(Parameter("document_id", "path", "string", True),),
        notes=(
            "document_id는 DB document.doc_id/document_chunk.doc_id에 대응한다.",
            "doc_type은 SPEC·MANUAL·TROUBLESHOOT 또는 null이다.",
        ),
    ),
    Endpoint(
        "C Agent",
        "POST",
        "/agent/runs",
        "알람 1건으로 incident Agent 실행 생성",
        (202, 404, 409, 422, 503),
        AgentRunAcceptedResponse,
        AgentRunCreateRequest,
        notes=(
            "run·incident 연결을 커밋한 뒤 background 실행하고 즉시 202를 반환한다.",
            "동일 incident가 진행 중이면 INCIDENT_ALREADY_RUNNING, 완료됐으면 INCIDENT_ALREADY_PROCESSED다.",
        ),
    ),
    Endpoint(
        "C Agent",
        "GET",
        "/agent/runs",
        "Agent 실행 목록",
        (200, 422),
        AgentRunPageResponse,
        parameters=(
            Parameter(
                "status", "query", "enum(RUNNING|WAITING_APPROVAL|COMPLETED|FAILED)"
            ),
            Parameter("equipment_id", "query", "string"),
            Parameter("chamber_id", "query", "string"),
            Parameter("date_from", "query", "datetime"),
            Parameter("date_to", "query", "datetime"),
            *PAGE_PARAMS,
        ),
        notes=("started_at DESC, agent_run_id DESC로 정렬한다.",),
    ),
    Endpoint(
        "C Agent",
        "GET",
        "/agent/runs/{run_id}",
        "Agent 실행 상세·근거·Tool·조치·승인 조회",
        (200, 404),
        AgentRunDetailResponse,
        parameters=(
            Parameter(
                "run_id",
                "path",
                "string",
                True,
                description="응답 필드명은 agent_run_id",
            ),
        ),
        notes=(
            "RUNNING 응답은 2초 polling하고 WAITING_APPROVAL·COMPLETED·FAILED에서 중지한다.",
            "fault_code는 FOC·RFM·MFD·TMD 또는 null이며 NRM은 런타임 계약에 없다.",
        ),
    ),
    Endpoint(
        "C Agent",
        "GET",
        "/approvals",
        "승인 요청 목록",
        (200, 422),
        ApprovalPageResponse,
        parameters=(
            Parameter(
                "status",
                "query",
                "enum(PENDING|APPROVED|REJECTED|EXPIRED)",
                default="PENDING",
            ),
            *PAGE_PARAMS,
        ),
        notes=("requested_at DESC, approval_id DESC로 정렬한다.",),
    ),
    Endpoint(
        "C Agent",
        "POST",
        "/approvals/{approval_id}/decision",
        "승인 또는 반려 결정",
        (200, 404, 409, 422),
        ApprovalDecisionResponse,
        ApprovalDecisionRequest,
        parameters=(Parameter("approval_id", "path", "string", True),),
        notes=(
            "decision은 APPROVE 또는 REJECT다. 성공 응답 status는 APPROVED 또는 REJECTED로 좁힌다.",
            "이미 처리됐거나 EXPIRED면 409 APPROVAL_ALREADY_DECIDED다.",
        ),
    ),
    Endpoint(
        "C Agent",
        "GET",
        "/actions",
        "조치 목록",
        (200, 422),
        ActionPageResponse,
        parameters=(
            Parameter(
                "approval_status", "query", "enum(AUTO|PENDING|APPROVED|REJECTED)"
            ),
            Parameter(
                "send_status", "query", "enum(WAITING|SENDING|SENT|FAILED|CANCELED)"
            ),
            Parameter("action_code", "query", "enum(MONITOR|NOTIFY|LOT_HOLD|EQP_HOLD)"),
            Parameter("equipment_id", "query", "string"),
            Parameter("chamber_id", "query", "string"),
            Parameter("date_from", "query", "datetime"),
            Parameter("date_to", "query", "datetime"),
            *PAGE_PARAMS,
        ),
        notes=("created_at DESC, action_id DESC로 정렬한다.",),
    ),
    Endpoint(
        "C Agent",
        "GET",
        "/actions/{action_id}",
        "조치 상세와 전송 상태",
        (200, 404),
        ActionDetailResponse,
        parameters=(Parameter("action_id", "path", "string", True),),
        notes=(
            "created_by_agent_run_id는 조치를 최초 생성한 run이며 재실행에서도 바꾸지 않는다. legacy 조치는 null이다.",
            "DB 컬럼은 원본 01_schema.sql이 아니라 승인된 migration으로 추가한다.",
        ),
    ),
    Endpoint(
        "D Analytics",
        "POST",
        "/analytics/query",
        "자연어 질의 실행",
        (200, 422, 503),
        AnalysisQueryResponse,
        AnalysisQueryRequest,
        notes=(
            "정책 거부는 HTTP 200 + is_rejected=true 구조화 응답이며 SQL을 실행하지 않는다.",
            "공백·1000자 초과 등 요청 형식 오류는 422다.",
            "프론트 요청 timeout은 최대 2회 LLM 시도를 포괄하도록 150초로 설정한다.",
        ),
    ),
    Endpoint(
        "D Analytics",
        "POST",
        "/analytics/validate",
        "SQL 검증만 수행",
        (200, 422),
        SqlValidateResponse,
        SqlValidateRequest,
        notes=("SQL은 1..20000자이며 실행하지 않는다.",),
    ),
    Endpoint(
        "D Analytics",
        "GET",
        "/analytics/history",
        "자연어 질의 이력 조회",
        (200, 422),
        NlQueryHistoryResponse,
        parameters=(
            Parameter("is_valid", "query", "boolean"),
            Parameter("is_rejected", "query", "boolean"),
            Parameter("date_from", "query", "datetime"),
            Parameter("date_to", "query", "datetime"),
            *PAGE_PARAMS,
        ),
        notes=("asked_at DESC, nl_query_log_id DESC로 정렬한다.",),
    ),
    Endpoint(
        "D Analytics",
        "GET",
        "/analytics/evaluations",
        "Text2SQL 골드·방어 평가 이력",
        (200, 422),
        EvaluationListResponse,
        parameters=(
            Parameter("latest", "query", "boolean", default="true"),
            *PAGE_PARAMS,
        ),
    ),
    Endpoint(
        "D Analytics",
        "GET",
        "/audit-logs",
        "감사로그 조회",
        (200, 422),
        AuditLogResponse,
        parameters=(
            Parameter("event_type", "query", "canonical audit event enum"),
            Parameter("actor_type", "query", "enum(SYSTEM|AGENT|HUMAN)"),
            Parameter("entity_type", "query", "string"),
            Parameter("entity_id", "query", "string"),
            Parameter("date_from", "query", "datetime"),
            Parameter("date_to", "query", "datetime"),
            *PAGE_PARAMS,
        ),
        notes=(
            "occurred_at DESC, audit_id DESC로 정렬한다.",
            "event_type_counts는 현재 페이지가 아니라 동일 필터의 전체 집계다.",
            "audit_log는 append-only이며 UPDATE·DELETE API를 제공하지 않는다.",
        ),
    ),
)


COMMON_RULES = (
    ("기준", "요구사항정의서 v1.9 · 시스템설계서 v1.10 · 역할분담 v9.6"),
    ("Base URL", "개발 http://localhost:8000 · 통합 배포 Nginx 상대 경로 /api"),
    (
        "인증",
        "초기 폐쇄형 개발 범위에는 사용자 JWT/RBAC를 도입하지 않는다. n8n Webhook secret은 내부 연동 전용이다.",
    ),
    ("Content-Type", "JSON Body는 application/json; charset=utf-8"),
    ("DTO", "Pydantic v2 · extra='forbid' · 공통 Enum과 NonEmptyId 재사용"),
    (
        "시간",
        "datetime은 timezone offset 포함 ISO 8601, 업무 기준 timezone은 Asia/Seoul",
    ),
    ("페이지", "page >= 1, size 1..100, 응답은 items·total·page·size"),
    (
        "오류 본문",
        "{code, message, details}; 500·로그에 비밀번호·DSN·API Key·내부 SQL 원문을 노출하지 않는다.",
    ),
    (
        "Text2SQL 거부",
        "/analytics/query 정책 거부는 200 구조화 응답; malformed request는 422. Tool POLICY_REJECTED: 계약은 유지.",
    ),
    ("Null", "`T | null`로 표기한 필드만 null 허용. Optional 파라미터는 생략 가능."),
)

ERROR_CODES = (
    ("RESOURCE_NOT_FOUND", 404, "ID로 조회한 리소스가 없음"),
    ("INCIDENT_ALREADY_RUNNING", 409, "같은 incident 실행이 RUNNING/WAITING_APPROVAL"),
    ("INCIDENT_ALREADY_PROCESSED", 409, "같은 incident 실행이 이미 COMPLETED"),
    ("APPROVAL_ALREADY_DECIDED", 409, "승인이 이미 결정됐거나 EXPIRED"),
    ("LEGACY_APPROVAL_NOT_LINKED", 409, "legacy 승인 행에 action 연결 없음"),
    ("IDEMPOTENCY_CONFLICT", 409, "같은 action_id의 효과 payload hash 충돌"),
    ("VALIDATION_ERROR", 422, "Body·path·query 형식 오류"),
    ("POLICY_REJECTED", 422, "Text2SQL 200 경로 밖의 명시적 HTTP 정책 예외"),
    ("DEPENDENCY_NOT_READY", 503, "PostgreSQL·Neo4j·n8n 준비 실패"),
    ("MODEL_NOT_READY", 503, "IsolationForest·Embedding 산출물 미준비"),
    ("LLM_NOT_READY", 503, "LLM credential·모델 미준비"),
    ("INTERNAL_ERROR", 500, "예기치 못한 서버 오류"),
)

AUDIT_EVENTS = (
    "DETECTION_COMPLETED",
    "AGENT_RUN_STARTED",
    "CLASSIFICATION_COMPLETED",
    "APPROVAL_REQUESTED",
    "APPROVAL_DECIDED",
    "ACTION_SENT",
    "ACTION_SEND_FAILED",
    "AGENT_RUN_COMPLETED",
    "AGENT_RUN_FAILED",
)


def json_schema(model: type[BaseModel] | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return model.model_json_schema(by_alias=True)


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def type_label(node: dict[str, Any] | bool) -> str:
    if node is True:
        return "any"
    if node is False:
        return "forbidden"
    if "$ref" in node:
        return ref_name(node["$ref"])
    if "const" in node:
        return repr(node["const"])
    if "enum" in node:
        return "enum(" + " | ".join(str(value) for value in node["enum"]) + ")"
    if "anyOf" in node:
        parts = [type_label(part) for part in node["anyOf"]]
        return " | ".join(dict.fromkeys(parts))
    node_type = node.get("type")
    if node_type == "array":
        return f"array<{type_label(node.get('items', {}))}>"
    if node_type == "object":
        if "additionalProperties" in node:
            additional = node["additionalProperties"]
            if additional is False:
                return "object"
            return f"map<string, {type_label(additional)}>"
        return "object"
    if node_type == "null":
        return "null"
    if node_type == "number":
        return "number"
    if node_type:
        return str(node_type)
    return "any"


def constraint_label(node: dict[str, Any]) -> str:
    parts: list[str] = []
    keys = (
        ("minLength", "minLength"),
        ("maxLength", "maxLength"),
        ("minimum", ">="),
        ("maximum", "<="),
        ("exclusiveMinimum", ">"),
        ("exclusiveMaximum", "<"),
        ("minItems", "minItems"),
        ("maxItems", "maxItems"),
        ("pattern", "pattern"),
    )
    for key, label in keys:
        if key in node:
            parts.append(f"{label} {node[key]}")
    if "default" in node:
        parts.append(f"default={node['default']}")
    return ", ".join(parts) or "-"


def field_rows(schema: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    if not schema:
        return []
    required = set(schema.get("required", []))
    rows: list[tuple[str, str, str, str]] = []
    for name, node in schema.get("properties", {}).items():
        rows.append(
            (
                name,
                type_label(node),
                "Y" if name in required else "N",
                constraint_label(node),
            )
        )
    return rows


def dto_registry() -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for endpoint in ENDPOINTS:
        for model in (endpoint.request_model, endpoint.response_model):
            schema = json_schema(model)
            if model is None or schema is None:
                continue
            main = {key: value for key, value in schema.items() if key != "$defs"}
            registry.setdefault(model.__name__, main)
            for name, definition in schema.get("$defs", {}).items():
                registry.setdefault(name, definition)
    return dict(sorted(registry.items()))


def compact_json(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_csv() -> Path:
    path = OUT / "API명세서.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "도메인",
                "메소드",
                "URI",
                "설명",
                "요청 파라미터",
                "요청 모델",
                "요청 JSON Schema",
                "응답 코드",
                "응답 모델",
                "응답 JSON Schema",
                "계약·정렬·예외",
            ]
        )
        for endpoint in ENDPOINTS:
            writer.writerow(
                [
                    endpoint.domain,
                    endpoint.method,
                    endpoint.path,
                    endpoint.summary,
                    compact_json(
                        [parameter.__dict__ for parameter in endpoint.parameters]
                    ),
                    endpoint.request_model.__name__ if endpoint.request_model else "",
                    compact_json(json_schema(endpoint.request_model)),
                    " | ".join(map(str, endpoint.statuses)),
                    endpoint.response_model.__name__,
                    compact_json(json_schema(endpoint.response_model)),
                    " / ".join(endpoint.notes),
                ]
            )
    return path


def markdown_field_table(schema: dict[str, Any] | None) -> list[str]:
    rows = field_rows(schema)
    if not rows:
        return ["없음"]
    lines = ["| 필드 | 타입 | 필수 | 제약·기본값 |", "|---|---|:---:|---|"]
    for name, field_type, required, constraint in rows:
        lines.append(f"| `{name}` | `{field_type}` | {required} | {constraint} |")
    return lines


def write_markdown() -> Path:
    lines = [
        "# BISTel FDC API 명세서",
        "",
        "| 항목 | 내용 |",
        "|---|---|",
        f"| 작성일 | {CREATED} |",
        f"| 최종 수정일 | {MODIFIED} |",
        f"| 팀명 | {TEAM} |",
        f"| 팀원 | {AUTHORS} |",
        "",
        "## 1. 공통 규약",
        "",
        "| 항목 | 내용 |",
        "|---|---|",
    ]
    lines.extend(f"| {name} | {value} |" for name, value in COMMON_RULES)
    lines.extend(
        [
            "",
            "### 1.1 공통 오류 코드",
            "",
            "| code | HTTP | 의미 |",
            "|---|---:|---|",
        ]
    )
    lines.extend(
        f"| `{code}` | {status} | {meaning} |" for code, status, meaning in ERROR_CODES
    )
    lines.extend(
        [
            "",
            "### 1.2 감사 이벤트 9종",
            "",
            "`" + "` · `".join(AUDIT_EVENTS) + "`",
            "",
            "## 2. 엔드포인트 목록",
            "",
            "| # | 도메인 | 메소드 | URI | 설명 | 응답 |",
            "|---:|---|:---:|---|---|---|",
        ]
    )
    for index, endpoint in enumerate(ENDPOINTS, 1):
        lines.append(
            f"| {index} | {endpoint.domain} | `{endpoint.method}` | `{endpoint.path}` | "
            f"{endpoint.summary} | {' · '.join(map(str, endpoint.statuses))} |"
        )

    current_domain = ""
    for index, endpoint in enumerate(ENDPOINTS, 1):
        if endpoint.domain != current_domain:
            current_domain = endpoint.domain
            lines.extend(
                [
                    "",
                    f"## 3.{len({e.domain for e in ENDPOINTS[:index]})} {current_domain}",
                    "",
                ]
            )
        lines.extend(
            [
                f"### {index}. `{endpoint.method} {endpoint.path}`",
                "",
                endpoint.summary,
                "",
                f"- 응답 코드: **{' · '.join(map(str, endpoint.statuses))}**",
                f"- 요청 모델: `{endpoint.request_model.__name__}`"
                if endpoint.request_model
                else "- 요청 Body: 없음",
                f"- 응답 모델: `{endpoint.response_model.__name__}`",
            ]
        )
        lines.extend(f"- {note}" for note in endpoint.notes)
        lines.extend(["", "**Path·Query 파라미터**", ""])
        if endpoint.parameters:
            lines.extend(
                [
                    "| 이름 | 위치 | 타입 | 필수 | 기본값 | 제약·설명 |",
                    "|---|---|---|:---:|---|---|",
                ]
            )
            for parameter in endpoint.parameters:
                constraint = parameter.constraint
                if parameter.description:
                    constraint = (
                        f"{constraint}; {parameter.description}"
                        if constraint != "-"
                        else parameter.description
                    )
                lines.append(
                    f"| `{parameter.name}` | {parameter.location} | `{parameter.type}` | "
                    f"{'Y' if parameter.required else 'N'} | {parameter.default} | {constraint} |"
                )
        else:
            lines.append("없음")
        lines.extend(["", "**요청 Body 필드**", ""])
        lines.extend(markdown_field_table(json_schema(endpoint.request_model)))
        lines.extend(["", "**응답 Body 필드**", ""])
        lines.extend(markdown_field_table(json_schema(endpoint.response_model)))

    lines.extend(["", "## 4. DTO 상세", ""])
    for name, schema in dto_registry().items():
        lines.extend([f"### `{name}`", ""])
        lines.extend(markdown_field_table(schema))
        lines.append("")

    lines.extend(
        [
            "## 5. DB/API 이름 대응과 구현 주의",
            "",
            "- `document_id` ↔ DB `document.doc_id`·`document_chunk.doc_id`",
            "- `nl_query_log_id` ↔ DB `nl_query_log.query_id`",
            "- `AlarmItem.detail`·`AuditLogItem.detail`은 text/string이며 감사 before/after만 JSON 객체다.",
            "- `ActionItem.created_by_agent_run_id`는 승인된 migration으로 추가할 nullable provenance 컬럼이다. 신규 조치 생성 시 한 번 기록하고 재실행에서 갱신하지 않는다.",
            "- `ET_REFL.upper_only=true`는 도메인 메타데이터 규칙이며 `spec_lower IS NULL`로 계산하지 않는다.",
            "- OpenAPI 경로는 개발 `/docs`·`/openapi.json`, Nginx 통합 배포 `/api/docs`·`/api/openapi.json`이다.",
            "",
        ]
    )

    path = OUT / "API명세서.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def paragraph(text: Any, style: Any) -> Any:
    from reportlab.platypus import Paragraph

    escaped = html.escape(str(text)).replace("\n", "<br/>")
    return Paragraph(escaped, style)


def write_pdf() -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        CondPageBreak,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font_candidates = [
        Path(value)
        for value in (
            os.getenv("API_SPEC_FONT"),
            "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
            str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"),
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        )
        if value
    ]
    font_path = next(
        (candidate for candidate in font_candidates if candidate.exists()), None
    )
    if font_path is None:
        raise RuntimeError(
            "한글 TTF 글꼴을 찾을 수 없습니다. API_SPEC_FONT에 글꼴 경로를 지정하세요."
        )
    pdfmetrics.registerFont(TTFont("AppleGothic", str(font_path)))

    navy = colors.HexColor("#17365D")
    blue = colors.HexColor("#2F75B5")
    pale_gray = colors.HexColor("#F3F5F7")
    dark = colors.HexColor("#1F2937")
    muted = colors.HexColor("#667085")
    line = colors.HexColor("#CBD5E1")

    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "KNormal",
        parent=styles["Normal"],
        fontName="AppleGothic",
        fontSize=8.2,
        leading=11.2,
        textColor=dark,
        wordWrap="CJK",
    )
    small = ParagraphStyle(
        "KSmall",
        parent=normal,
        fontSize=7.2,
        leading=9.2,
    )
    # 표 머리글 전용. 셀을 Paragraph 로 감싸면 TableStyle 의 TEXTCOLOR 가 적용되지 않아
    # 남색 배경 위에 남색 글자가 찍힌다. 머리글은 이 스타일로 직접 흰색을 준다.
    small_head = ParagraphStyle(
        "KSmallHead",
        parent=small,
        textColor=colors.white,
    )
    title = ParagraphStyle(
        "KTitle",
        parent=normal,
        fontSize=26,
        leading=34,
        alignment=TA_CENTER,
        textColor=navy,
        spaceAfter=8 * mm,
    )
    subtitle = ParagraphStyle(
        "KSubtitle",
        parent=normal,
        fontSize=12,
        leading=17,
        alignment=TA_CENTER,
        textColor=muted,
    )
    h1 = ParagraphStyle(
        "KH1",
        parent=normal,
        fontSize=15,
        leading=20,
        textColor=navy,
        spaceBefore=3 * mm,
        spaceAfter=3 * mm,
    )
    h2 = ParagraphStyle(
        "KH2",
        parent=normal,
        fontSize=11.5,
        leading=16,
        textColor=blue,
        spaceBefore=2 * mm,
        spaceAfter=2 * mm,
    )
    note_style = ParagraphStyle(
        "KNote",
        parent=small,
        leftIndent=4 * mm,
        bulletIndent=1 * mm,
        bulletFontName="AppleGothic",
        textColor=dark,
    )
    path = OUT / "API명세서.pdf"
    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=13 * mm,
        title="BISTel FDC API 명세서",
        author=AUTHORS,
        subject="요구사항 v1.9·시스템설계서 v1.10 기준 API 계약",
    )

    page_w, page_h = landscape(A4)
    # 제출용 세 문서의 표지를 같은 형태로 맞춘다. 세로 A4 기준 96mm 네이비 블록을
    # 가로 A4(210mm) 에 같은 비율로 옮기면 68mm 다.
    COVER_BAND = 68 * mm

    def on_page(canvas: Any, document: Any) -> None:
        canvas.saveState()
        if document.page == 1:
            canvas.setFillColor(navy)
            canvas.rect(0, page_h - COVER_BAND, page_w, COVER_BAND, stroke=0, fill=1)
            canvas.setFillColor(colors.HexColor("#7fb6dd"))
            canvas.setFont("AppleGothic", 10.5)
            canvas.drawString(12 * mm, page_h - 26 * mm, "BISTel FDC")
            canvas.setFillColor(colors.white)
            canvas.setFont("AppleGothic", 27)
            canvas.drawString(12 * mm, page_h - 39 * mm, "API 명세서")
            canvas.setFillColor(colors.HexColor("#e7edf3"))
            canvas.setFont("AppleGothic", 12.5)
            canvas.drawString(
                12 * mm, page_h - 49 * mm, "LangGraph 기반 반도체 FDC 이상감지 에이전트"
            )
            canvas.setFillColor(colors.HexColor("#cbd5e1"))
            canvas.setFont("AppleGothic", 11)
            canvas.drawString(12 * mm, page_h - 58 * mm, f"{MODIFIED} · {TEAM}")
        else:
            canvas.setFont("AppleGothic", 7.5)
            canvas.setFillColor(muted)
            canvas.drawString(12 * mm, 8 * mm, "BISTel FDC  |  API 명세서")
            canvas.drawRightString(page_w - 12 * mm, 8 * mm, f"{document.page}")
            canvas.setStrokeColor(line)
            canvas.line(12 * mm, 11 * mm, page_w - 12 * mm, 11 * mm)
        canvas.restoreState()

    def make_table(
        rows: list[list[Any]],
        widths: list[float],
        header: bool = True,
        font_size: float = 7.3,
    ) -> Table:
        formatted: list[list[Any]] = []
        for index, row in enumerate(rows):
            style = small_head if header and index == 0 else small
            formatted.append(
                [
                    cell if isinstance(cell, Paragraph) else paragraph(cell, style)
                    for cell in row
                ]
            )
        table = Table(
            formatted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT"
        )
        commands: list[tuple[Any, ...]] = [
            ("GRID", (0, 0), (-1, -1), 0.35, line),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3.2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
            ("FONTNAME", (0, 0), (-1, -1), "AppleGothic"),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ]
        if header:
            commands.extend(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), navy),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ]
            )
            if len(rows) > 1:
                commands.append(
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pale_gray])
                )
        table.setStyle(TableStyle(commands))
        return table

    # 표지 상단 네이비 블록은 on_page 가 그린다. 표는 그 아래에서 시작한다.
    story: list[Any] = [Spacer(1, COVER_BAND - 14 * mm)]
    cover_rows = [
        ["항목", "내용"],
        ["작성일", CREATED],
        ["최종 수정일", MODIFIED],
        ["팀명", TEAM],
        ["팀원", AUTHORS],
    ]
    story.extend(
        [
            # 표 폭은 본문 표와 같이 페이지 폭을 채운다(다른 두 제출 문서와 동일).
            make_table(cover_rows, [42 * mm, 231 * mm], font_size=9),
            PageBreak(),
            # 문서 목적은 표지가 아니라 본문 도입부에 둔다.
            Paragraph(
                "본 문서는 FastAPI·React 통합에 사용하는 요청·응답 계약과 정렬·예외 규칙을 정의한다. "
                "필드 타입은 Backend Pydantic DTO에서 생성하며 원본 배포 스키마는 수정하지 않는다.",
                normal,
            ),
            Spacer(1, 6 * mm),
            Paragraph("1. 공통 규약", h1),
        ]
    )
    story.append(make_table([["항목", "내용"], *COMMON_RULES], [42 * mm, 222 * mm]))
    story.extend([Spacer(1, 5 * mm), Paragraph("1.1 공통 오류 코드", h2)])
    story.append(
        make_table(
            [["code", "HTTP", "의미"], *ERROR_CODES],
            [58 * mm, 18 * mm, 188 * mm],
        )
    )
    story.extend([Spacer(1, 4 * mm), Paragraph("1.2 감사 이벤트 9종", h2)])
    story.append(paragraph(" · ".join(AUDIT_EVENTS), normal))
    story.extend([PageBreak(), Paragraph("2. 엔드포인트 목록", h1)])
    index_rows = [["#", "도메인", "메소드", "URI", "설명", "응답"]]
    for index, endpoint in enumerate(ENDPOINTS, 1):
        index_rows.append(
            [
                index,
                endpoint.domain,
                endpoint.method,
                endpoint.path,
                endpoint.summary,
                " · ".join(map(str, endpoint.statuses)),
            ]
        )
    story.append(
        make_table(index_rows, [10 * mm, 31 * mm, 18 * mm, 65 * mm, 118 * mm, 22 * mm])
    )

    current_domain = ""
    for index, endpoint in enumerate(ENDPOINTS, 1):
        # 각 API를 새 페이지에서 시작해 제목·계약이 앞 페이지의 남은 공간과
        # 분리되지 않게 한다. 큰 DTO 표만 자연스럽게 다음 페이지로 이어진다.
        story.append(PageBreak())
        if endpoint.domain != current_domain:
            current_domain = endpoint.domain
            story.append(Paragraph(current_domain, h1))
        heading = f"{index}. {endpoint.method} {endpoint.path}"
        story.append(Paragraph(html.escape(heading), h2))
        summary_rows = [
            ["설명", endpoint.summary],
            ["응답 코드", " · ".join(map(str, endpoint.statuses))],
            [
                "요청 모델",
                endpoint.request_model.__name__ if endpoint.request_model else "없음",
            ],
            ["응답 모델", endpoint.response_model.__name__],
        ]
        story.append(make_table(summary_rows, [28 * mm, 236 * mm], header=False))
        if endpoint.notes:
            story.append(Spacer(1, 2 * mm))
            for note in endpoint.notes:
                story.append(Paragraph("• " + html.escape(note), note_style))

        story.extend([Spacer(1, 2 * mm), Paragraph("Path·Query 파라미터", h2)])
        if endpoint.parameters:
            parameter_rows = [["이름", "위치", "타입", "필수", "기본", "제약·설명"]]
            for parameter in endpoint.parameters:
                constraint = parameter.constraint
                if parameter.description:
                    constraint = (
                        f"{constraint}; {parameter.description}"
                        if constraint != "-"
                        else parameter.description
                    )
                parameter_rows.append(
                    [
                        parameter.name,
                        parameter.location,
                        parameter.type,
                        "Y" if parameter.required else "N",
                        parameter.default,
                        constraint,
                    ]
                )
            story.append(
                make_table(
                    parameter_rows,
                    [34 * mm, 20 * mm, 66 * mm, 14 * mm, 22 * mm, 108 * mm],
                )
            )
        else:
            story.append(paragraph("없음", normal))

        for label, model in (
            ("요청 Body", endpoint.request_model),
            ("응답 Body", endpoint.response_model),
        ):
            story.extend([Spacer(1, 2 * mm), Paragraph(label, h2)])
            rows = field_rows(json_schema(model))
            if not rows:
                story.append(paragraph("없음", normal))
                continue
            body_rows = [["필드", "타입", "필수", "제약·기본값"], *rows]
            story.append(make_table(body_rows, [62 * mm, 112 * mm, 16 * mm, 74 * mm]))

    story.extend([PageBreak(), Paragraph("DTO 상세", h1)])
    for name, schema in dto_registry().items():
        rows = field_rows(schema)
        if not rows:
            continue
        block: list[Any] = [Paragraph(html.escape(name), h2)]
        block.append(
            make_table(
                [["필드", "타입", "필수", "제약·기본값"], *rows],
                [62 * mm, 112 * mm, 16 * mm, 74 * mm],
            )
        )
        story.append(CondPageBreak(45 * mm))
        story.extend(block)
        story.append(Spacer(1, 2 * mm))

    story.extend(
        [
            PageBreak(),
            Paragraph("DB/API 이름 대응과 구현 주의", h1),
            Paragraph(
                "• document_id ↔ DB document.doc_id·document_chunk.doc_id", note_style
            ),
            Paragraph("• nl_query_log_id ↔ DB nl_query_log.query_id", note_style),
            Paragraph(
                "• AlarmItem.detail·AuditLogItem.detail은 string이며 before/after만 JSON 객체다.",
                note_style,
            ),
            Paragraph(
                "• created_by_agent_run_id는 승인된 migration으로 추가하고 재실행에서 갱신하지 않는다.",
                note_style,
            ),
            Paragraph(
                "• ET_REFL.upper_only=true는 명시적 도메인 메타데이터이며 하한 null 여부로 계산하지 않는다.",
                note_style,
            ),
            Paragraph(
                "• OpenAPI: 개발 /docs·/openapi.json, 통합 배포 /api/docs·/api/openapi.json",
                note_style,
            ),
        ]
    )

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return path


def validate_outputs(paths: tuple[Path, Path, Path]) -> None:
    csv_path, md_path, pdf_path = paths
    with csv_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != len(ENDPOINTS):
        raise RuntimeError(
            f"CSV endpoint count mismatch: {len(rows)} != {len(ENDPOINTS)}"
        )
    markdown = md_path.read_text(encoding="utf-8")
    for endpoint in ENDPOINTS:
        if endpoint.path not in markdown:
            raise RuntimeError(f"Markdown에서 endpoint 누락: {endpoint.path}")
    if pdf_path.stat().st_size < 10_000:
        raise RuntimeError("PDF가 비정상적으로 작습니다")


def preflight() -> None:
    """세 형식을 한 번에 만들 수 있는지 먼저 확인한다.

    CSV·Markdown 을 먼저 쓰고 PDF 에서 실패하면 두 형식만 갱신되어 PDF 가 옛 계약을
    가리킨 채 남는다. 어느 하나라도 못 만들면 아무것도 쓰지 않는다.
    """
    try:
        import reportlab  # noqa: F401
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "reportlab 이 없어 PDF 를 만들 수 없습니다. 세 형식은 함께 생성해야 하므로 "
            "아무 파일도 쓰지 않았습니다.\n"
            "  pip install -r docs/deliverables/api/requirements.txt"
        ) from exc


def main() -> None:
    preflight()
    paths = (write_csv(), write_markdown(), write_pdf())
    validate_outputs(paths)
    for path in paths:
        print(f"{path.name}: {path.stat().st_size:,} bytes")
    print(f"endpoints: {len(ENDPOINTS)}")


if __name__ == "__main__":
    main()
