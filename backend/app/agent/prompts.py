"""원인 가설 prompt 계약 (`V5-C-2.3`).

동적 근거를 결정론적 JSON으로 조립하고, 최종 messages 전체를
데이터셋 정답 label 노출 패턴으로 검사한다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from app.agent.routing import ResolvedIncidentRoute
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

PROMPT_VERSION: Final = "agent-hypothesis-v1"
MAX_PROMPT_CHARS: Final = 12_000
MAX_DOCUMENT_EXCERPT_CHARS: Final = 500
TRUNCATION_MARKER: Final = "…[truncated]"

# `_`는 regex word 문자이므로 predicted_fault_code 안의 부분 문자열은
# 매칭되지 않고, 독립 token만 막힌다.
_BLOCKED_TOKENS: Final[tuple[str, ...]] = (
    "fault_code",
    "FAULTCODE",
    "FAULTS",
    "is_fault",
    "fault_of",
    "faulty_lots",
    "NRM",
)
_BLOCKED_PATTERN: Final = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(value) for value in _BLOCKED_TOKENS)
    + r")(?!\w)",
    re.IGNORECASE,
)


class HypothesisPromptError(ValueError):
    """prompt 생성·검사 계약 위반. 동적 원문을 메시지에 담지 않는다."""

    _CODES: Final[frozenset[str]] = frozenset(
        {"HYPOTHESIS_PROMPT_BLOCKED", "HYPOTHESIS_PROMPT_TOO_LARGE"}
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "HYPOTHESIS_PROMPT_BLOCKED"
        super().__init__(code)
        self.code = code


def _excerpt(content: str) -> str:
    if len(content) <= MAX_DOCUMENT_EXCERPT_CHARS:
        return content
    return content[:MAX_DOCUMENT_EXCERPT_CHARS] + TRUNCATION_MARKER


def _route_payload(route: ResolvedIncidentRoute) -> dict[str, Any]:
    return {
        "incident": {
            "lot_id": route.incident.lot_id,
            "chamber_id": route.incident.chamber_id,
            "requested_alarm": route.incident.requested_alarm.model_dump(mode="json"),
            "representative_alarm": route.incident.representative_alarm.model_dump(
                mode="json"
            ),
            "member_alarms": [
                alarm.model_dump(mode="json") for alarm in route.incident.member_alarms
            ],
        },
        "route_consistency": route.route_consistency,
        "graph_evidence": [
            {
                "chamber_id": item.chamber_id,
                "equipment_id": item.equipment_id,
                "model_code": item.model_code,
                "process_step_id": item.process_step_id,
                "upstream_process_step_ids": list(item.upstream_process_step_ids),
                "downstream_process_step_ids": list(item.downstream_process_step_ids),
                "relation_ids": list(item.relation_ids),
                "graph_revision": item.graph_revision,
            }
            for item in route.graph_evidence
        ],
        "mismatches": [
            {
                "code": item.code,
                "wafer_id": item.wafer_id,
                "from_lot_hist_id": item.from_lot_hist_id,
                "to_lot_hist_id": item.to_lot_hist_id,
                "postgres_ids": list(item.postgres_ids),
                "graph_ids": list(item.graph_ids),
                "relation_ids": list(item.relation_ids),
            }
            for item in route.mismatches
        ],
    }


def _document_payload(result: DocumentSearchToolResult | None) -> Any:
    if result is None:
        return None
    if not result.ok:
        return result.model_dump(mode="json")
    return {
        "ok": True,
        "reason": "",
        "hits": [
            {
                **hit.model_dump(mode="json", exclude={"content"}),
                "content": _excerpt(hit.content),
            }
            for hit in sorted(result.hits, key=lambda value: value.chunk_id)
        ],
    }


def build_hypothesis_messages(
    fdc_evidence: FdcSummaryToolResult | None,
    graph_evidence: EquipmentContextToolResult | None,
    document_evidence: DocumentSearchToolResult | None,
    route: ResolvedIncidentRoute,
    *,
    correction_reason: str | None = None,
) -> list[dict[str, str]]:
    """초도·보정 시도가 같은 근거 builder를 쓰는 messages를 만든다."""

    evidence = {
        "document": _document_payload(document_evidence),
        "equipment": (
            None if graph_evidence is None else graph_evidence.model_dump(mode="json")
        ),
        "fdc": (None if fdc_evidence is None else fdc_evidence.model_dump(mode="json")),
        "route": _route_payload(route),
    }
    evidence_json = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    system = (
        "You generate one semiconductor FDC cause hypothesis from supplied evidence. "
        "Return one JSON object only with keys predicted_fault_code, confidence, "
        "cause_summary, supporting_alarms, supporting_chunk_ids, "
        "supporting_relation_ids, uncertainty. predicted_fault_code must be one of "
        "FOC, RFM, MFD, TMD, OTH. supporting_alarms entries use "
        '{"source":"TRACE|SUMMARY|R03","alarm_id":"..."}. '
        "Cite only supplied identifiers. Cite at least one member alarm; when document "
        "hits or relation identifiers exist, cite at least one of each."
    )
    user = f"Evidence JSON:\n{evidence_json}"
    if correction_reason is not None:
        user += (
            "\nThe previous output was rejected for sanitized reason "
            f"{correction_reason}. Rebuild the JSON from the same evidence."
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    scan_hypothesis_messages(messages)
    return messages


def scan_hypothesis_messages(messages: list[dict[str, str]]) -> None:
    """최종 전송 문자열 전체에서 금지 독립 token과 길이를 검사한다."""

    contents = [message.get("content") for message in messages]
    if any(not isinstance(content, str) for content in contents):
        raise HypothesisPromptError("HYPOTHESIS_PROMPT_BLOCKED")
    combined = "\n".join(contents)  # type: ignore[arg-type]
    if _BLOCKED_PATTERN.search(combined):
        raise HypothesisPromptError("HYPOTHESIS_PROMPT_BLOCKED")
    if len(combined) > MAX_PROMPT_CHARS:
        raise HypothesisPromptError("HYPOTHESIS_PROMPT_TOO_LARGE")


__all__ = [
    "MAX_DOCUMENT_EXCERPT_CHARS",
    "MAX_PROMPT_CHARS",
    "PROMPT_VERSION",
    "TRUNCATION_MARKER",
    "HypothesisPromptError",
    "build_hypothesis_messages",
    "scan_hypothesis_messages",
]
