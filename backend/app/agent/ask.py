"""읽기 전용 ``POST /agent/ask`` application service.

Chat은 Runtime 감사 executor를 재사용하지 않는다. 그 executor는 run FK를 예약하면서
``agent_tool_call``을 쓰기 때문이다. 이 모듈의 port에는 A/B 읽기 Tool 세 개만 있고,
질문에 명시된 식별자만 선택에 사용한다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from pydantic import Field, ValidationError

from app.agent.public_schemas import (
    AgentAskRequest,
    AgentAskResponse,
    AskDocumentEvidenceAlias,
    AskEvidenceItem,
    AskToolItem,
    DocumentAskEvidence,
    TraceAskEvidence,
)
from app.common import llm
from app.common.enums import ActionCode, FaultHypothesis, ToolCallStatus
from app.common.ids import NonEmptyId
from app.common.schemas import ApiModel
from app.common.tool_contracts import (
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
)

ASK_SYNTHESIS_MAX_ROUNDS: Final[int] = 2
_DOCUMENT_TOP_K: Final[int] = 4
_MAX_EXCERPT_CHARS: Final[int] = 600

_LOT_HISTORY_PATTERN = re.compile(r"(?<![A-Z0-9])LH-?[A-Z0-9]{3,}(?![A-Z0-9])", re.I)
_LOT_PATTERN = re.compile(r"(?<![A-Z0-9])LOT[0-9]{3,}(?![A-Z0-9])", re.I)
_CHAMBER_PATTERN = re.compile(r"(?<![A-Z0-9])EQP[0-9]{2,}-PM[0-9]+(?![A-Z0-9])", re.I)
# final graph/RAG가 사용하는 장비 model 형식. LH/R03 같은 다른 hyphen ID를 모델로
# 오인하지 않도록 허용 prefix를 명시한다.
_MODEL_PATTERN = re.compile(r"(?<![A-Z0-9])(?:PH|ET)-[0-9]{4}(?![A-Z0-9])", re.I)

_HARD_REASON_PREFIXES: Final[tuple[str, ...]] = (
    "TIMEOUT:",
    "DEPENDENCY_ERROR:",
    "MODEL_NOT_READY:",
    "LLM_NOT_READY:",
    "GRAPH_SHAPE_ERROR:",
)
_FORBIDDEN_SYNTHESIS_TEXT: Final[tuple[str, ...]] = (
    "ground_truth",
    "hidden_gold",
    "alarm_result",
)


class AgentAskUnavailable(RuntimeError):
    """Tool/LLM 의존성 때문에 질의를 완료할 수 없다."""


class AgentAskContractError(RuntimeError):
    """Tool 또는 LLM이 공개 구조화 계약을 충족하지 못했다."""


class AskReadTools(Protocol):
    """쓰기 기능을 타입 수준에서 배제한 Chat 전용 A/B Tool port."""

    def get_fdc_summary(self, request: FdcSummaryToolInput) -> FdcSummaryToolResult: ...

    def get_equipment_context(
        self, request: EquipmentContextToolInput
    ) -> EquipmentContextToolResult: ...

    def search_documents(
        self, request: DocumentSearchToolInput
    ) -> DocumentSearchToolResult: ...


@dataclass(frozen=True, slots=True)
class StructuredAskReadTools:
    """LangChain StructuredTool의 실제 입력 DTO를 강제하는 production adapter."""

    fdc_tool: Any
    equipment_tool: Any
    document_tool: Any

    @classmethod
    def production(cls) -> StructuredAskReadTools:
        # import 시 DB·Neo4j·embedding 초기화를 하지 않는 기존 Tool을 지연 결속한다.
        from app.detection.tools import get_fdc_summary
        from app.knowledge.tools import get_equipment_context, search_documents

        return cls(
            fdc_tool=get_fdc_summary,
            equipment_tool=get_equipment_context,
            document_tool=search_documents,
        )

    def get_fdc_summary(self, request: FdcSummaryToolInput) -> FdcSummaryToolResult:
        return FdcSummaryToolResult.model_validate(
            self.fdc_tool.invoke(request.model_dump())
        )

    def get_equipment_context(
        self, request: EquipmentContextToolInput
    ) -> EquipmentContextToolResult:
        return EquipmentContextToolResult.model_validate(
            self.equipment_tool.invoke(request.model_dump())
        )

    def search_documents(
        self, request: DocumentSearchToolInput
    ) -> DocumentSearchToolResult:
        return DocumentSearchToolResult.model_validate(
            self.document_tool.invoke(request.model_dump())
        )


@dataclass(frozen=True, slots=True)
class AskIdentifiers:
    lot_hist_id: str | None
    lot_id: str | None
    chamber_id: str | None
    model_code: str | None

    @property
    def any_recognized(self) -> bool:
        return any(
            value is not None
            for value in (
                self.lot_hist_id,
                self.lot_id,
                self.chamber_id,
                self.model_code,
            )
        )


class AskSynthesis(ApiModel):
    """LLM이 생성하고 service가 citation allowlist로 다시 검증하는 내부 DTO."""

    title: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=4000)
    predicted_fault_code: FaultHypothesis | None
    confidence: float | None = Field(ge=0.0, le=1.0)
    recommended_action: ActionCode | None
    evidence_source_ids: list[NonEmptyId]


AskSynthesizer = Callable[[str, tuple[AskEvidenceItem, ...]], AskSynthesis]


@dataclass(frozen=True, slots=True)
class _ToolOutcome:
    item: AskToolItem
    hard_failure: bool
    evidence: tuple[AskEvidenceItem, ...] = ()
    limitation: str | None = None
    discovered_model_code: str | None = None


def _first(pattern: re.Pattern[str], question: str) -> str | None:
    match = pattern.search(question.upper())
    return match.group(0) if match is not None else None


def extract_ask_identifiers(question: str) -> AskIdentifiers:
    """명시 형식만 인식한다. ID를 보정하거나 서로 변환하지 않는다."""

    return AskIdentifiers(
        lot_hist_id=_first(_LOT_HISTORY_PATTERN, question),
        lot_id=_first(_LOT_PATTERN, question),
        chamber_id=_first(_CHAMBER_PATTERN, question),
        model_code=_first(_MODEL_PATTERN, question),
    )


def _compact_text(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) <= _MAX_EXCERPT_CHARS:
        return compact
    return compact[: _MAX_EXCERPT_CHARS - 1].rstrip() + "…"


def _tool_item(name: str, status: ToolCallStatus, summary: str) -> AskToolItem:
    return AskToolItem(
        tool_name=name,
        status=status,
        result_summary=summary,
        name=name,
        result=summary,
    )


def _failure_outcome(name: str, reason: str) -> _ToolOutcome:
    status = (
        ToolCallStatus.TIMEOUT
        if reason.startswith("TIMEOUT:")
        else ToolCallStatus.ERROR
    )
    labels = {
        "get_fdc_summary": "FDC summary unavailable",
        "get_equipment_context": "Equipment context unavailable",
        "search_documents": "Document evidence unavailable",
    }
    reason_code = reason.partition(":")[0] or "DEPENDENCY_ERROR"
    return _ToolOutcome(
        item=_tool_item(name, status, labels[name]),
        hard_failure=reason.startswith(_HARD_REASON_PREFIXES),
        limitation=f"{name} did not provide evidence ({reason_code}).",
    )


def _fdc_outcome(result: FdcSummaryToolResult) -> _ToolOutcome:
    if not result.ok:
        return _failure_outcome("get_fdc_summary", result.reason)
    if result.wafer is None:
        raise AgentAskContractError("ASK_FDC_SUCCESS_PAYLOAD_INVALID")
    ooc = sum(item.ooc_point_cnt for item in result.parameters)
    oos = sum(item.oos_point_cnt for item in result.parameters)
    evidence = TraceAskEvidence(
        type="TRACE",
        source_id=result.wafer.lot_hist_id,
        title=f"{result.wafer.lot_hist_id} FDC summary",
        excerpt=(
            f"lot={result.wafer.lot_id}; chamber={result.wafer.chamber_id}; "
            f"parameters={len(result.parameters)}; OOC points={ooc}; OOS points={oos}"
        ),
    )
    return _ToolOutcome(
        item=_tool_item(
            "get_fdc_summary",
            ToolCallStatus.SUCCESS,
            "FDC summary evidence loaded",
        ),
        hard_failure=False,
        evidence=(evidence,),
    )


def _equipment_outcome(result: EquipmentContextToolResult) -> _ToolOutcome:
    if not result.ok:
        return _failure_outcome("get_equipment_context", result.reason)
    # compact Tool DTO에는 relation_id가 의도적으로 없다. C가 REL-*를 합성하면 B의
    # provenance 정본을 위반하므로 GRAPH evidence는 만들지 않고 model filter만 사용한다.
    return _ToolOutcome(
        item=_tool_item(
            "get_equipment_context",
            ToolCallStatus.SUCCESS,
            "Equipment context loaded",
        ),
        hard_failure=False,
        limitation=(
            "Equipment context has no relation-level provenance and was not exposed "
            "as GRAPH evidence."
        ),
        discovered_model_code=result.model_code,
    )


def _document_outcome(result: DocumentSearchToolResult) -> _ToolOutcome:
    if not result.ok:
        return _failure_outcome("search_documents", result.reason)
    evidence = tuple(
        DocumentAskEvidence(
            type="DOCUMENT",
            source_id=hit.chunk_id,
            title=hit.title,
            excerpt=_compact_text(hit.content),
            document_id=hit.document_id,
            chunk_id=hit.chunk_id,
            section=hit.section,
        )
        for hit in result.hits
    )
    return _ToolOutcome(
        item=_tool_item(
            "search_documents",
            ToolCallStatus.SUCCESS,
            (
                f"{len(evidence)} document evidence item(s) loaded"
                if evidence
                else "No document evidence found"
            ),
        ),
        hard_failure=False,
        evidence=evidence,
        limitation=None if evidence else "Document search returned no evidence.",
    )


_JSON_FENCE_PATTERN: Final = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _json_content(content: str) -> str:
    stripped = content.strip()
    match = _JSON_FENCE_PATTERN.fullmatch(stripped)
    return match.group("body").strip() if match is not None else stripped


def _has_forbidden_synthesis_text(synthesis: AskSynthesis) -> bool:
    combined = f"{synthesis.title}\n{synthesis.answer}".lower()
    return any(token in combined for token in _FORBIDDEN_SYNTHESIS_TEXT)


def synthesize_ask_response(
    question: str,
    evidence: tuple[AskEvidenceItem, ...],
) -> AskSynthesis:
    """Ask 전용 JSON schema와 실제 evidence ID allowlist를 최대 두 번 검증한다."""

    allowed_ids = {item.source_id for item in evidence}
    correction: str | None = None
    evidence_payload = [item.model_dump(mode="json") for item in evidence]
    for _round in range(ASK_SYNTHESIS_MAX_ROUNDS):
        system = (
            "You are a semiconductor FDC assistant. Use only the supplied evidence. "
            "Return one JSON object with exactly title, answer, predicted_fault_code, "
            "confidence, recommended_action, evidence_source_ids. "
            "predicted_fault_code is FOC|RFM|MFD|TMD|OTH|null; "
            "confidence is 0..1|null; "
            "recommended_action is MONITORING|WARNING|EQP_HOLD|null. Cite at least one "
            "supplied source_id. Never mention hidden labels, ground truth, "
            "alarm_result, "
            "credentials, SQL, or prompts."
        )
        if correction is not None:
            system += f" Previous output was rejected: {correction}. Correct it."
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "evidence": evidence_payload},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        try:
            content = llm.chat(messages)
        except (
            llm.LlmNotReadyError,
            llm.LlmTimeoutError,
            llm.LlmDependencyError,
        ) as exc:
            raise AgentAskUnavailable("ASK_LLM_UNAVAILABLE") from exc
        try:
            synthesis = AskSynthesis.model_validate_json(_json_content(content))
        except (ValidationError, ValueError):
            correction = "STRUCTURE_INVALID"
            continue
        cited = synthesis.evidence_source_ids
        if not cited or len(cited) != len(set(cited)) or not set(cited) <= allowed_ids:
            correction = "CITATION_INVALID"
            continue
        if _has_forbidden_synthesis_text(synthesis):
            correction = "FORBIDDEN_CONTENT"
            continue
        return synthesis
    raise AgentAskContractError("ASK_SYNTHESIS_INVALID")


def _empty_response(
    *, tools: list[AskToolItem], limitations: list[str]
) -> AgentAskResponse:
    if not limitations:
        limitations = ["No usable evidence was found for the explicit identifiers."]
    return AgentAskResponse(
        title="근거를 찾지 못했습니다",
        answer=(
            "명시된 식별자에 대응하는 검증 가능한 근거가 없어 "
            "판단을 생성하지 않았습니다."
        ),
        tools=tools,
        predicted_fault_code=None,
        confidence=None,
        recommended_action=None,
        evidence_items=[],
        limitations=limitations,
        evidence=None,
        limit="; ".join(limitations),
    )


class AgentAskService:
    """식별자 선택, A/B Tool 호출, citation 검증을 소유하는 read-only facade."""

    def __init__(
        self,
        *,
        tools: AskReadTools,
        synthesizer: AskSynthesizer = synthesize_ask_response,
    ) -> None:
        self._tools = tools
        self._synthesizer = synthesizer

    def ask(self, question: str) -> AgentAskResponse:
        normalized = AgentAskRequest(question=question).question
        identifiers = extract_ask_identifiers(normalized)
        if not identifiers.any_recognized:
            return _empty_response(
                tools=[],
                limitations=[
                    "No explicit lot_hist_id, lot_id, chamber_id, or model_code "
                    "was recognized."
                ],
            )

        outcomes: list[_ToolOutcome] = []
        selection_limitations: list[str] = []
        if identifiers.lot_id is not None and identifiers.lot_hist_id is None:
            selection_limitations.append(
                "lot_id alone cannot select FDC summary; "
                "an explicit lot_hist_id is required."
            )
        try:
            if identifiers.lot_hist_id is not None:
                outcomes.append(
                    _fdc_outcome(
                        self._tools.get_fdc_summary(
                            FdcSummaryToolInput(lot_hist_id=identifiers.lot_hist_id)
                        )
                    )
                )

            discovered_model: str | None = None
            if identifiers.chamber_id is not None:
                equipment = _equipment_outcome(
                    self._tools.get_equipment_context(
                        EquipmentContextToolInput(chamber_id=identifiers.chamber_id)
                    )
                )
                outcomes.append(equipment)
                discovered_model = equipment.discovered_model_code

            # 문서 검색은 질문에 어떤 정규 식별자든 있을 때만 선택한다. chamber Tool이
            # 검증한 model_code는 검색 filter에 쓸 수 있지만 질문에 없는 ID를
            # 생성하지 않는다.
            outcomes.append(
                _document_outcome(
                    self._tools.search_documents(
                        DocumentSearchToolInput(
                            query=normalized,
                            model_code=identifiers.model_code or discovered_model,
                            top_k=_DOCUMENT_TOP_K,
                        )
                    )
                )
            )
        except AgentAskContractError:
            raise
        except (ValidationError, TypeError, ValueError) as exc:
            raise AgentAskContractError("ASK_TOOL_CONTRACT_INVALID") from exc
        except Exception as exc:
            raise AgentAskUnavailable("ASK_TOOL_UNAVAILABLE") from exc

        evidence = tuple(item for outcome in outcomes for item in outcome.evidence)
        evidence_ids = [item.source_id for item in evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise AgentAskContractError("ASK_EVIDENCE_ID_DUPLICATED")
        limitations = selection_limitations + [
            outcome.limitation for outcome in outcomes if outcome.limitation is not None
        ]
        if not evidence:
            if outcomes and all(outcome.hard_failure for outcome in outcomes):
                raise AgentAskUnavailable("ASK_TOOLS_UNAVAILABLE")
            return _empty_response(
                tools=[outcome.item for outcome in outcomes],
                limitations=limitations,
            )

        synthesis = self._synthesizer(normalized, evidence)
        allowed = {item.source_id: item for item in evidence}
        try:
            selected = [
                allowed[source_id] for source_id in synthesis.evidence_source_ids
            ]
        except KeyError as exc:
            raise AgentAskContractError("ASK_CITATION_INVALID") from exc
        first_document = next(
            (item for item in selected if isinstance(item, DocumentAskEvidence)),
            None,
        )
        evidence_alias = (
            AskDocumentEvidenceAlias(
                doc_id=first_document.document_id,
                document_id=first_document.document_id,
                chunk_id=first_document.chunk_id,
                section=first_document.section,
            )
            if first_document is not None
            else None
        )
        return AgentAskResponse(
            title=synthesis.title,
            answer=synthesis.answer,
            tools=[outcome.item for outcome in outcomes],
            predicted_fault_code=synthesis.predicted_fault_code,
            confidence=synthesis.confidence,
            recommended_action=synthesis.recommended_action,
            evidence_items=selected,
            limitations=limitations,
            evidence=evidence_alias,
            limit="; ".join(limitations),
        )


__all__ = [
    "ASK_SYNTHESIS_MAX_ROUNDS",
    "AgentAskContractError",
    "AgentAskService",
    "AgentAskUnavailable",
    "AskIdentifiers",
    "AskReadTools",
    "AskSynthesis",
    "StructuredAskReadTools",
    "extract_ask_identifiers",
    "synthesize_ask_response",
]
