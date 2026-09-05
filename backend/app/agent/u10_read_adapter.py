"""U10 authorize/read/validate/project/record adapter for five read-only tools.

No connection or provider is created on import. A verified snapshot context,
caller-owned deadline runner and code-owned evidence projector are required.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import EvidenceIds
from app.agent.u10_observations import ObservationContext
from app.agent.u10_read_execution import ReadObservation
from app.common.tool_deadlines import READ_TOOL_CALLER_DEADLINE_SECONDS

ReadPort = Callable[[dict[str, Any]], Any]


class Deadline(Protocol):
    def call(self, fn: ReadPort, payload: dict[str, Any], *, seconds: float) -> Any: ...


@dataclass(frozen=True)
class ReadPorts:
    fdc_summary: ReadPort
    equipment_context: ReadPort
    document_search: ReadPort
    chamber_parameter_history: ReadPort
    metrology_result: ReadPort

    @classmethod
    def production(cls) -> ReadPorts:
        """Reuse existing read tools; never construct the send-action factory."""
        from app.agent.tools import ToolBoundary

        boundary = ToolBoundary.production()
        return cls(
            boundary.fdc_summary,
            boundary.equipment_context,
            boundary.document_search,
            boundary.chamber_parameter_history,
            boundary.metrology_result,
        )


class ReadAdapter:
    def __init__(
        self,
        context: ObservationContext,
        ports: ReadPorts,
        deadline: Deadline,
        project_evidence: Callable[[str, Any], EvidenceIds],
    ) -> None:
        self._context = context
        self._deadline = deadline
        self._project = project_evidence
        self._lock = Lock()
        self._ports = {
            "get_fdc_summary": ports.fdc_summary,
            "get_equipment_context": ports.equipment_context,
            "search_documents": ports.document_search,
            "get_chamber_parameter_history": ports.chamber_parameter_history,
            "get_metrology_result": ports.metrology_result,
        }

    def __call__(
        self,
        tool: str,
        request: dict[str, Any],
        internal: dict[str, Any] | None = None,
    ) -> ReadObservation:
        if not self._lock.acquire(blocking=False):
            raise EvidenceError("U10_READ_ADAPTER_BUSY")
        try:
            return self._call(tool, deepcopy(request), deepcopy(internal))
        finally:
            self._lock.release()

    @staticmethod
    def _failure(status: str) -> ReadObservation:
        return ReadObservation(
            status=status,
            evidence_ids=EvidenceIds(values=[], sha256=digest(canonical_json([]))),
        )

    def _call(self, tool, request, internal):
        from app.agent.tools import ToolRunnerSaturated

        if tool not in self._ports:
            raise EvidenceError("U10_READ_SCOPE_INVALID")
        # Both policies must specify the same snapshot model filter, not an
        # unconstrained search that happens to return a matching first result.
        if tool == "search_documents" and not request.get("model_code"):
            raise EvidenceError("U10_DOCUMENT_MODEL_REQUIRED")
        self._context.authorize(tool, request, internal)
        payload = deepcopy(request)
        if internal is not None:
            payload["_context"] = deepcopy(internal)
        try:
            # Worker executes ONLY the read port. A timed-out/late result cannot
            # publish observations or evidence from the worker thread.
            raw = self._deadline.call(
                self._ports[tool], payload, seconds=READ_TOOL_CALLER_DEADLINE_SECONDS
            )
        except ToolRunnerSaturated:
            return self._failure("ERROR")
        except TimeoutError:
            return self._failure("TIMEOUT")
        except Exception:
            return self._failure("ERROR")
        result = self._context.validate_result(tool, request, raw, internal)
        if not result.ok:
            return self._failure(
                "TIMEOUT" if result.reason.startswith("TIMEOUT:") else "ERROR"
            )
        projected = self._project(tool, result.model_copy(deep=True))
        if not isinstance(projected, EvidenceIds):
            raise EvidenceError("U10_EVIDENCE_PROJECTION_INVALID")
        projected = EvidenceIds.model_validate(projected.model_dump()).model_copy(
            deep=True
        )
        observation = ReadObservation(status="SUCCESS", evidence_ids=projected)
        # Commit last: invalid DTO/scope/projection must not create an observation.
        self._context.record(tool, request, result, internal)
        return observation
