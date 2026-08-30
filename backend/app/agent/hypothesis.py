"""원인 가설 production port (`V5-C-2.3`)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Final

from pydantic import ValidationError

from app.agent.prompts import (
    PROMPT_VERSION,
    HypothesisPromptError,
    build_hypothesis_messages,
)
from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import Hypothesis, HypothesisOutcome, LlmUsage
from app.common import llm
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
)

MAX_GENERATION_ROUNDS: Final = 2
logger = logging.getLogger(__name__)
HYPOTHESIS_RESPONSE_SCHEMA: Final[dict[str, object]] = {
    "name": "agent_hypothesis",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "predicted_fault_code": {
                "type": "string",
                "enum": ["FOC", "RFM", "MFD", "TMD", "OTH"],
            },
            "confidence": {"type": "number"},
            "cause_summary": {"type": "string"},
            "supporting_alarms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["TRACE", "SUMMARY", "R03"],
                        },
                        "alarm_id": {"type": "string"},
                    },
                    "required": ["source", "alarm_id"],
                },
            },
            "supporting_chunk_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "supporting_relation_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "uncertainty": {"type": "string"},
        },
        "required": [
            "predicted_fault_code",
            "confidence",
            "cause_summary",
            "supporting_alarms",
            "supporting_chunk_ids",
            "supporting_relation_ids",
            "uncertainty",
        ],
    },
}
ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "LLM_NOT_READY",
        "LLM_TIMEOUT",
        "LLM_DEPENDENCY",
        "HYPOTHESIS_STRUCTURE_INVALID",
        "HYPOTHESIS_PROMPT_BLOCKED",
        "HYPOTHESIS_PROMPT_TOO_LARGE",
        "PREDICTION_CONFLICT",
    }
)


class HypothesisGenerationError(RuntimeError):
    """원문 응답·URL·key·provider 예외를 노출하지 않는 가설 오류."""

    def __init__(self, code: str, *, usage: LlmUsage | None = None) -> None:
        if code not in ERROR_CODES:
            code = "LLM_DEPENDENCY"
        super().__init__(code)
        self.code = code
        self._usage = usage

    @property
    def usage_or_none(self) -> LlmUsage | None:
        return self._usage


def _map_llm_error(exc: Exception, usage: LlmUsage | None) -> HypothesisGenerationError:
    if isinstance(exc, llm.LlmNotReadyError):
        code = "LLM_NOT_READY"
    elif isinstance(exc, llm.LlmTimeoutError):
        code = "LLM_TIMEOUT"
    else:
        code = "LLM_DEPENDENCY"
        if isinstance(exc, llm.LlmDependencyError):
            response_usage = exc.usage_or_none
            if response_usage is not None:
                try:
                    current = LlmUsage(
                        model=response_usage.model,
                        prompt_version=PROMPT_VERSION,
                        input_tokens=response_usage.prompt_tokens,
                        output_tokens=response_usage.completion_tokens,
                    )
                    if usage is None:
                        usage = current
                    elif usage.model == current.model:
                        usage = usage.plus(current)
                except (ValidationError, ValueError):
                    pass
    return HypothesisGenerationError(code, usage=usage)


_JSON_FENCE_PATTERN: Final = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _json_content(content: str) -> str:
    """응답 전체를 감싼 JSON fence 하나만 허용하고 벗긴다."""

    stripped = content.strip()
    match = _JSON_FENCE_PATTERN.fullmatch(stripped)
    if match is None:
        return stripped
    return match.group("body").strip()


def _completion_usage(completion: llm.ChatCompletion) -> LlmUsage:
    try:
        return LlmUsage(
            model=completion.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=completion.prompt_tokens,
            output_tokens=completion.completion_tokens,
        )
    except ValidationError as exc:
        raise HypothesisGenerationError("LLM_DEPENDENCY") from exc


def _citation_reason(
    hypothesis: Hypothesis,
    document_evidence: DocumentSearchToolResult | None,
    route: ResolvedIncidentRoute,
) -> str | None:
    allowed_alarms = {alarm.to_token() for alarm in route.incident.member_alarms}
    cited_alarms = {alarm.to_token() for alarm in hypothesis.supporting_alarms}
    if not cited_alarms:
        return "ALARM_CITATION_REQUIRED"
    if not cited_alarms <= allowed_alarms:
        return "ALARM_CITATION_OUTSIDE_EVIDENCE"

    allowed_chunks = (
        {hit.chunk_id for hit in document_evidence.hits}
        if document_evidence is not None and document_evidence.ok
        else set()
    )
    cited_chunks = set(hypothesis.supporting_chunk_ids)
    if allowed_chunks and not cited_chunks:
        return "DOCUMENT_CITATION_REQUIRED"
    if not cited_chunks <= allowed_chunks:
        return "DOCUMENT_CITATION_OUTSIDE_EVIDENCE"

    allowed_relations = {
        relation_id
        for item in route.graph_evidence
        for relation_id in item.relation_ids
    }
    cited_relations = set(hypothesis.supporting_relation_ids)
    if allowed_relations and not cited_relations:
        return "RELATION_CITATION_REQUIRED"
    if not cited_relations <= allowed_relations:
        return "RELATION_CITATION_OUTSIDE_EVIDENCE"
    return None


def generate_hypothesis(
    fdc_evidence: FdcSummaryToolResult | None,
    graph_evidence: EquipmentContextToolResult | None,
    document_evidence: DocumentSearchToolResult | None,
    route: ResolvedIncidentRoute,
) -> HypothesisOutcome:
    """최대 2회 생성하고 구조·실제 근거 인용을 fail-closed 검증한다."""

    accumulated: LlmUsage | None = None
    correction_reason: str | None = None
    for _round in range(MAX_GENERATION_ROUNDS):
        try:
            messages = build_hypothesis_messages(
                fdc_evidence,
                graph_evidence,
                document_evidence,
                route,
                correction_reason=correction_reason,
            )
        except HypothesisPromptError as exc:
            raise HypothesisGenerationError(exc.code, usage=accumulated) from exc

        try:
            completion = llm.chat_with_usage(
                messages,
                json_schema=HYPOTHESIS_RESPONSE_SCHEMA,
            )
        except (
            llm.LlmNotReadyError,
            llm.LlmTimeoutError,
            llm.LlmDependencyError,
        ) as exc:
            raise _map_llm_error(exc, accumulated) from exc

        usage = _completion_usage(completion)
        if accumulated is None:
            accumulated = usage
        else:
            if accumulated.model != usage.model:
                raise HypothesisGenerationError("LLM_DEPENDENCY", usage=accumulated)
            try:
                accumulated = accumulated.plus(usage)
            except (ValidationError, ValueError) as exc:
                raise HypothesisGenerationError(
                    "LLM_DEPENDENCY", usage=accumulated
                ) from exc

        try:
            hypothesis = Hypothesis.model_validate_json(
                _json_content(completion.content)
            )
        except ValidationError as exc:
            fields = sorted(
                {
                    ".".join(str(part) for part in error["loc"])
                    for error in exc.errors(include_input=False, include_url=False)
                }
            )
            correction_reason = "STRUCTURE_INVALID:" + ",".join(fields)
            continue
        except ValueError:
            correction_reason = "JSON_INVALID"
            continue

        correction_reason = _citation_reason(hypothesis, document_evidence, route)
        if correction_reason is None:
            return HypothesisOutcome(hypothesis=hypothesis, llm_usage=accumulated)

    logger.warning(
        "hypothesis output rejected after correction (reason=%s)",
        correction_reason or "STRUCTURE_INVALID",
    )
    raise HypothesisGenerationError("HYPOTHESIS_STRUCTURE_INVALID", usage=accumulated)


def production_port() -> (
    Callable[
        [
            FdcSummaryToolResult | None,
            EquipmentContextToolResult | None,
            DocumentSearchToolResult | None,
            ResolvedIncidentRoute,
        ],
        HypothesisOutcome,
    ]
):
    """AgentNodePorts에 주입할 실제 원인 가설 callable."""

    return generate_hypothesis


__all__ = [
    "ERROR_CODES",
    "MAX_GENERATION_ROUNDS",
    "HypothesisGenerationError",
    "generate_hypothesis",
    "production_port",
]
