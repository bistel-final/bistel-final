"""U10 observation-backed context builder, without live reads or provider calls.

The caller supplies a verified incident route and actual Tool DTOs. This module
authorizes their scope and rebuilds selector observations with production code;
it does not attest to DB snapshot truth. Initial IDs use U10's code-owned mapping.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.u10_comparison import EvidenceIds

if TYPE_CHECKING:
    from app.agent.react import ReactContext
    from app.agent.routing import ResolvedIncidentRoute


class ObservationContext:
    def __init__(
        self,
        run_id: str,
        route: ResolvedIncidentRoute,
        current_lot_hist_ids: list[str],
        *,
        document_model_code: str,
    ) -> None:
        from app.agent import react

        self._route = deepcopy(route)
        self._model = document_model_code
        self._run_id = run_id
        if not route.route_consistency or not document_model_code:
            raise EvidenceError("U10_SNAPSHOT_SCOPE_INVALID")
        self._candidates = react.build_initial_candidates(
            run_id=run_id,
            route=self._route,
            current_lot_hist_ids=current_lot_hist_ids,
        )
        current = [c for c in self._candidates.fdc if c.relation == "CURRENT"]
        if (
            not current
            or len(set(current_lot_hist_ids)) != len(current_lot_hist_ids)
            or {c.lot_hist_id for c in current} != set(current_lot_hist_ids)
            or any(c.chamber_id != route.incident.chamber_id for c in current)
        ):
            raise EvidenceError("U10_SNAPSHOT_SCOPE_INVALID")
        graph = [
            g
            for g in self._route.graph_evidence
            if g.chamber_id == route.incident.chamber_id
        ]
        if len(graph) != 1 or graph[0].model_code != document_model_code:
            raise EvidenceError("U10_SNAPSHOT_SCOPE_INVALID")
        self._graph = graph[0]
        self._results: dict[str, list[Any]] = {
            "get_fdc_summary": [],
            "get_equipment_context": [],
            "search_documents": [],
            "get_chamber_parameter_history": [],
            "get_metrology_result": [],
        }

    def initial_evidence_ids(self) -> EvidenceIds:
        from app.agent.u10_evidence import project_initial_evidence

        return project_initial_evidence(self._route)

    def build_context(self) -> ReactContext:
        from app.agent import react

        equipment = self._results["get_equipment_context"]
        equipment = equipment[-1] if equipment else None
        self._candidates = react.refresh_history_candidates(
            self._candidates,
            fdc_results=self._results["get_fdc_summary"],
            equipment=equipment,
        )
        return react.build_context(
            run_id=self._run_id,
            lot_id=self._route.incident.lot_id,
            chamber_id=self._route.incident.chamber_id,
            representative_alarm=self._route.incident.representative_alarm,
            member_alarms=self._route.incident.member_alarms,
            route=self._route,
            candidates=self._candidates,
            fdc_results=self._results["get_fdc_summary"],
            equipment=equipment,
            documents=self._results["search_documents"],
            history_results=self._results["get_chamber_parameter_history"],
            metrology_results=self._results["get_metrology_result"],
            remaining_tool_calls=0,
            remaining_steps=0,
            guard_rejections=0,
        ).model_copy(deep=True)

    def authorize(
        self, tool: str, request: dict[str, Any], internal: dict[str, Any] | None = None
    ) -> None:
        """Call BEFORE a read; accept only exact server-resolved arguments."""
        from app.agent import react

        context = self.build_context()
        candidates = self._candidates
        arguments = None
        if tool == "get_fdc_summary":
            candidate = next(
                (
                    c
                    for c in candidates.fdc
                    if request == {"lot_hist_id": c.lot_hist_id}
                ),
                None,
            )
            if candidate:
                arguments = {"fdc_candidate_id": candidate.candidate_id}
        elif tool == "get_equipment_context":
            if request == {"chamber_id": context.chamber_id}:
                arguments = {}
        elif tool == "get_chamber_parameter_history":
            for candidate in candidates.history:
                selected = react.ReactSelection(
                    rationale_summary="scope check",
                    next=tool,
                    arguments=react.ReactArguments(
                        history_candidate_id=candidate.candidate_id
                    ),
                )
                resolved = react.resolve_call(selected, context)
                if canonical_json(request) == canonical_json(
                    resolved["request"]
                ) and canonical_json(internal) == canonical_json(
                    resolved["internal_context"]
                ):
                    arguments = {"history_candidate_id": candidate.candidate_id}
                    break
        elif tool == "get_metrology_result":
            candidate = next(
                (
                    c
                    for c in candidates.metrology
                    if c.relation == "CURRENT"
                    and request == {"lot_id": c.lot_id, "step_id": c.step_id}
                ),
                None,
            )
            if candidate:
                arguments = {"metrology_candidate_id": candidate.candidate_id}
        elif tool == "search_documents":
            if (
                set(request) <= {"query", "model_code"}
                and request.get("model_code", self._model) == self._model
            ):
                arguments = {"query": request.get("query")}
        if arguments is None or (
            tool != "get_chamber_parameter_history" and internal is not None
        ):
            raise EvidenceError("U10_READ_SCOPE_INVALID")
        selection = react.ReactSelection(
            rationale_summary="scope check",
            next=tool,
            arguments=react.ReactArguments(**arguments),
        )
        # Scope guard only: budgets and prior successes belong to the shared runner.
        # Permit re-reading fixed-policy initial evidence and common retries.
        scope_context = context.model_copy(
            update={
                "remaining_tool_calls": 8,
                "fetched_fdc_candidate_ids": (),
            }
        )
        guard = react.guard_selection(
            selection,
            scope_context,
            equipment_fetched=tool != "get_equipment_context"
            and context.equipment_observation is not None,
            document_model_code=self._model,
        )
        if guard:
            raise EvidenceError("U10_READ_SCOPE_INVALID")

    def validate_result(
        self,
        tool: str,
        request: dict[str, Any],
        result: Any,
        internal: dict[str, Any] | None = None,
    ) -> Any:
        """Validate and copy a result without publishing it to observations."""
        from app.common import tool_contracts as dto

        self.authorize(tool, request, internal)
        types = {
            "get_fdc_summary": dto.FdcSummaryToolResult,
            "get_equipment_context": dto.EquipmentContextToolResult,
            "search_documents": dto.DocumentSearchToolResult,
            "get_chamber_parameter_history": dto.ChamberParameterHistoryToolResult,
            "get_metrology_result": dto.MetrologyResultToolResult,
        }
        model = types[tool]
        if not isinstance(result, model):
            raise EvidenceError("U10_OBSERVATION_TYPE_INVALID")
        result = model.model_validate(result.model_dump()).model_copy(deep=True)
        if not result.ok:
            return result
        valid = True
        if tool == "get_fdc_summary":
            candidate = next(
                c
                for c in self._candidates.fdc
                if c.lot_hist_id == request["lot_hist_id"]
            )
            valid = (
                result.wafer.lot_hist_id == candidate.lot_hist_id
                and result.wafer.lot_id == self._route.incident.lot_id
                and result.wafer.chamber_id == candidate.chamber_id
                and result.wafer.step_id == candidate.step_id
            )
        elif tool == "get_equipment_context":
            valid = (
                result.chamber_id == self._graph.chamber_id
                and result.model_code == self._graph.model_code
                and result.equipment_id == self._graph.equipment_id
                and result.process_step_id == self._graph.process_step_id
                and result.graph_revision == self._graph.graph_revision
                and set(result.sibling_chamber_ids)
                == set(self._graph.sibling_chamber_ids)
            )
        elif tool == "search_documents":
            valid = all(hit.model_code in (None, self._model) for hit in result.hits)
        elif tool == "get_chamber_parameter_history":
            valid = (
                result.chamber_id == request["chamber_id"]
                and result.parameter_id == request["parameter_id"]
                and result.step_no == request["step_no"]
                and result.scope == result.comparison == internal["scope"]
                and result.current.lot_id == internal["current_lot_id"]
            )
        elif tool == "get_metrology_result":
            valid = (
                result.lot_id == request["lot_id"]
                and result.step_id == request["step_id"]
            )
        if not valid:
            raise EvidenceError("U10_OBSERVATION_SCOPE_INVALID")
        return result

    def record(
        self,
        tool: str,
        request: dict[str, Any],
        result: Any,
        internal: dict[str, Any] | None = None,
    ) -> None:
        """Only validated, identity-bound SUCCESS results enter selector context."""
        result = self.validate_result(tool, request, result, internal)
        if not result.ok:
            return
        # Equivalent repeated success does not inflate observations or candidates.
        if all(
            canonical_json(previous) != canonical_json(result)
            for previous in self._results[tool]
        ):
            self._results[tool].append(result)

    def results(self, tool: str) -> list[Any]:
        if tool not in self._results:
            raise EvidenceError("U10_READ_SCOPE_INVALID")
        return [item.model_copy(deep=True) for item in self._results[tool]]
