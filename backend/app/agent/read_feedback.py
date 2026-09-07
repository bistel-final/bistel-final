"""V5-C-7.1 code-owned, same-run feedback from actual read reservations.

Only canonical Tool inputs and allowlisted outcome codes survive projection.
This module has no dependency on graph, ReAct or hypothesis models. Absence is a
fact about one request in this investigation, not a permanent production claim.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.common.tool_contracts import (
    REASON_PREFIXES,
    ChamberParameterHistoryToolInput,
    DocumentSearchToolInput,
    EquipmentContextToolInput,
    FdcSummaryToolInput,
    MetrologyResultToolInput,
)

ReadTool = Literal[
    "get_fdc_summary",
    "get_equipment_context",
    "search_documents",
    "get_chamber_parameter_history",
    "get_metrology_result",
]
ReadStatus = Literal["SUCCESS", "ERROR", "TIMEOUT"]
ReasonCode = Literal[
    "NOT_FOUND",
    "TIMEOUT",
    "MODEL_NOT_READY",
    "LLM_NOT_READY",
    "GRAPH_SHAPE_ERROR",
    "DEPENDENCY_ERROR",
    "POLICY_REJECTED",
    "IDEMPOTENCY_CONFLICT",
    "TOOL_RUNNER_SATURATED",
    "TOOL_DEADLINE_EXCEEDED",
    "TOOL_RESULT_INVALID",
    "TOOL_INVOCATION_ERROR",
]

_INPUTS = {
    "get_fdc_summary": FdcSummaryToolInput,
    "get_equipment_context": EquipmentContextToolInput,
    "search_documents": DocumentSearchToolInput,
    "get_chamber_parameter_history": ChamberParameterHistoryToolInput,
    "get_metrology_result": MetrologyResultToolInput,
}
_REASONS = frozenset(prefix.removesuffix(":") for prefix in REASON_PREFIXES) | {
    "TOOL_RUNNER_SATURATED",
    "TOOL_DEADLINE_EXCEEDED",
    "TOOL_RESULT_INVALID",
    "TOOL_INVOCATION_ERROR",
}
_NO_RETRY = frozenset({"NOT_FOUND", "POLICY_REJECTED"})
# The repository's reservation marker is recognized, never exported as failure.
_RESERVATION_SENTINEL = "CALL_RESERVED_NOT_COMPLETED"


def _canonical_request(tool: str, value: Any) -> dict[str, Any] | None:
    model = _INPUTS.get(tool) if isinstance(tool, str) else None
    if model is None or not isinstance(value, Mapping):
        return None
    try:
        return model.model_validate(dict(value)).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        return None


def _digest(request: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(request), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


class ReadFeedback(BaseModel):
    """Latest completed outcome plus all reserved attempts for one request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: ReadTool
    request: dict[str, Any]
    attempts: int = Field(ge=1, strict=True)
    failed_attempts: int = Field(default=0, ge=0, strict=True)
    last_status: ReadStatus
    reason_code: ReasonCode | None = None
    retryable: bool

    @model_validator(mode="after")
    def canonical_and_consistent(self) -> ReadFeedback:
        canonical = _canonical_request(self.tool, self.request)
        if canonical is None:
            raise ValueError("READ_FEEDBACK_REQUEST_INVALID")
        if self.last_status == "SUCCESS" and self.reason_code is not None:
            raise ValueError("READ_FEEDBACK_SUCCESS_REASON_INVALID")
        if self.failed_attempts > self.attempts or (
            self.last_status == "SUCCESS" and self.failed_attempts == self.attempts
        ):
            raise ValueError("READ_FEEDBACK_ATTEMPTS_INVALID")
        if self.last_status == "TIMEOUT" and self.reason_code not in {
            None,
            "TIMEOUT",
            "TOOL_RUNNER_SATURATED",
            "TOOL_DEADLINE_EXCEEDED",
        }:
            raise ValueError("READ_FEEDBACK_TIMEOUT_REASON_INVALID")
        expected = self.last_status != "SUCCESS" and self.reason_code not in _NO_RETRY
        if self.retryable is not expected:
            raise ValueError("READ_FEEDBACK_RETRY_INVALID")
        object.__setattr__(self, "request", canonical)
        return self

    @property
    def request_digest(self) -> str:
        return _digest(self.request)

    @property
    def last_outcome(self) -> str:
        return self.reason_code or self.last_status


def _value(row: Any, key: str, default: Any = None) -> Any:
    return (
        row.get(key, default)
        if isinstance(row, Mapping)
        else getattr(row, key, default)
    )


def _reason(row: Any, status: str) -> str | None:
    if status == "SUCCESS":
        return None
    if status == "TIMEOUT":
        code = _value(row, "error_msg")
        return (
            code
            if isinstance(code, str)
            and code in {"TOOL_RUNNER_SATURATED", "TOOL_DEADLINE_EXCEEDED"}
            else "TIMEOUT"
        )
    output = _value(row, "output")
    if isinstance(output, Mapping):
        # Never infer failure from a contradictory successful output or from a
        # string embedded in a returned document/payload.
        if output.get("ok") is not False:
            return None
        text = output.get("reason")
        if isinstance(text, str):
            prefix, separator, _ = text.partition(":")
            if separator and prefix in _REASONS:
                return prefix
    # The production wrapper also stores sanitized exact codes for failures
    # without a result DTO. Unknown exception details are discarded entirely.
    code = _value(row, "error_msg")
    return code if isinstance(code, str) and code in _REASONS else None


def normalize_read_reason(
    status: Any, *, output: Any = None, error_msg: Any = None
) -> ReasonCode | None:
    """Export only a known reason code, never dependency text or a payload body."""

    resolved = getattr(status, "value", status)
    if resolved not in {"SUCCESS", "ERROR", "TIMEOUT"}:
        return None
    return _reason({"output": output, "error_msg": error_msg}, resolved)


def summarize_read_history(
    history: Sequence[Any], *, run_id: str | None = None
) -> tuple[ReadFeedback, ...]:
    """Group a run's ordered reservations without trusting prompt/state copies.

    Input may be production ToolCallRow objects or normalized mappings. Missing
    run IDs are permitted for already-scoped U10/test histories. Explicitly mixed
    run histories require a run_id filter, so one run cannot blacklist another.
    Caller order is the authoritative ledger order (repository call_seq ASC).
    """

    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise ValueError("READ_FEEDBACK_RUN_SCOPE_INVALID")
    scoped = []
    known_runs = set()
    for row in history:
        row_run = _value(row, "agent_run_id", _value(row, "run_id"))
        if row_run is not None:
            if not isinstance(row_run, str) or not row_run:
                raise ValueError("READ_FEEDBACK_RUN_SCOPE_INVALID")
            if run_id is not None and row_run != run_id:
                continue
            known_runs.add(row_run)
        scoped.append(row)
    if run_id is None and len(known_runs) > 1:
        raise ValueError("READ_FEEDBACK_RUN_SCOPE_MIXED")

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in scoped:
        tool = _value(row, "tool_name", _value(row, "tool"))
        request = _canonical_request(tool, _value(row, "input", _value(row, "request")))
        if request is None:
            continue
        key = (tool, _digest(request))
        group = groups.setdefault(
            key,
            {"tool": tool, "request": request, "attempts": 0, "failed_attempts": 0},
        )
        group["attempts"] += 1
        raw_status = _value(row, "status")
        status = getattr(raw_status, "value", raw_status)
        pending = status == "PENDING" or (
            status == "ERROR"
            and _value(row, "output") is None
            and _value(row, "error_msg") == _RESERVATION_SENTINEL
        )
        if pending:
            continue
        if status not in {"SUCCESS", "ERROR", "TIMEOUT"}:
            status = "ERROR"
        if status != "SUCCESS":
            group["failed_attempts"] += 1
        reason = normalize_read_reason(
            status, output=_value(row, "output"), error_msg=_value(row, "error_msg")
        )
        group.update(
            last_status=status,
            reason_code=reason,
            retryable=status != "SUCCESS" and reason not in _NO_RETRY,
        )
    return tuple(
        ReadFeedback.model_validate(group)
        for group in groups.values()
        if "last_status" in group
    )


def feedback_for_request(
    feedback: Sequence[ReadFeedback], tool: str, request: Mapping[str, Any]
) -> ReadFeedback | None:
    """Match canonical defaults as well as key order; never match a nearby target."""

    canonical = _canonical_request(tool, request)
    if canonical is None:
        return None
    expected = _digest(canonical)
    for item in reversed(feedback):
        # Frozen Pydantic models may still contain mutable dictionaries.
        validated = ReadFeedback.model_validate(item.model_dump())
        if validated.tool == tool and validated.request_digest == expected:
            return validated
    return None
