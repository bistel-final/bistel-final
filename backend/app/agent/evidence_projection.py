"""저장·질의 Tool 결과를 공개 evidence DTO로 바꾸는 순수 projection.

이 모듈은 Tool을 실행하지 않는다. 호출자는 이미 검증된 구조화 결과만 넘기며, 원문
payload·오류·prompt 같은 Runtime 내부 값은 반환 타입으로 표현할 수 없다.
"""

from __future__ import annotations

from typing import Final

from app.agent.public_schemas import DocumentAskEvidence, TraceAskEvidence
from app.common.tool_contracts import DocumentSearchToolResult, FdcSummaryToolResult

MAX_EVIDENCE_EXCERPT_CHARS: Final[int] = 600


def compact_evidence_text(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= MAX_EVIDENCE_EXCERPT_CHARS:
        return compact
    return compact[: MAX_EVIDENCE_EXCERPT_CHARS - 1].rstrip() + "…"


def project_fdc_evidence(
    result: FdcSummaryToolResult,
) -> TraceAskEvidence | None:
    if not result.ok or result.wafer is None:
        return None
    ooc = sum(item.ooc_point_cnt for item in result.parameters)
    oos = sum(item.oos_point_cnt for item in result.parameters)
    return TraceAskEvidence(
        type="TRACE",
        source_id=result.wafer.lot_hist_id,
        title=f"{result.wafer.lot_hist_id} FDC summary",
        excerpt=(
            f"lot={result.wafer.lot_id}; chamber={result.wafer.chamber_id}; "
            f"parameters={len(result.parameters)}; OOC points={ooc}; OOS points={oos}"
        ),
    )


def project_document_evidence(
    result: DocumentSearchToolResult,
) -> tuple[DocumentAskEvidence, ...]:
    if not result.ok:
        return ()
    return tuple(
        DocumentAskEvidence(
            type="DOCUMENT",
            source_id=hit.chunk_id,
            title=hit.title,
            excerpt=compact_evidence_text(hit.content),
            document_id=hit.document_id,
            chunk_id=hit.chunk_id,
            section=hit.section,
        )
        for hit in result.hits
    )


__all__ = [
    "MAX_EVIDENCE_EXCERPT_CHARS",
    "compact_evidence_text",
    "project_document_evidence",
    "project_fdc_evidence",
]
