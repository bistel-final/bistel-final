"""U10-only, namespace-preserving evidence projection (no IO or oracle input).

These IDs measure citations, not investigation completeness. History/metrology
DTOs have no independently citable ID in hypothesis v3; never invent one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import EvidenceIds

if TYPE_CHECKING:
    from app.agent.routing import ResolvedIncidentRoute
    from app.agent.state import Hypothesis

NAMESPACES = ("ALARM", "CHUNK", "RELATION", "LOT_HIST", "PARAMETER")


def projection_spec() -> dict[str, Any]:
    """Fresh code-owned contract; future runner must bind this in tool contract."""
    return {
        "version": "u10-evidence-v1",
        "encoding": "namespace:exact_id",
        "namespaces": list(NAMESPACES),
        "initial": ["member_alarms.to_token", "graph_evidence.relation_ids"],
        "reads": {
            "get_fdc_summary": [
                "LOT_HIST:wafer.lot_hist_id",
                "PARAMETER:parameters.parameter_id",
            ],
            "search_documents": ["CHUNK:hits.chunk_id"],
            "get_equipment_context": [],
            "get_chamber_parameter_history": [],
            "get_metrology_result": [],
        },
        "citations": ["supporting_*", "origin_assessment.basis", "parameter_findings"],
    }


def projection_sha256() -> str:
    return digest(canonical_json(projection_spec()))


def evidence_id(namespace: str, value: str) -> str:
    if (
        namespace not in NAMESPACES
        or type(value) is not str
        or not value
        or value != value.strip()
        or len(namespace) + 1 + len(value) > 200
    ):
        raise EvidenceError("U10_EVIDENCE_ID_INVALID")
    return f"{namespace}:{value}"


def _ids(refs: Iterable[tuple[str, str]]) -> EvidenceIds:
    values = sorted({evidence_id(namespace, value) for namespace, value in refs})
    return EvidenceIds(values=values, sha256=digest(canonical_json(values)))


def project_initial_evidence(route: ResolvedIncidentRoute) -> EvidenceIds:
    """Project a caller-verified route; candidate presence is NOT an FDC read."""
    from app.agent.routing import ResolvedIncidentRoute

    if not isinstance(route, ResolvedIncidentRoute) or not route.route_consistency:
        raise EvidenceError("U10_SNAPSHOT_SCOPE_INVALID")
    refs = [("ALARM", alarm.to_token()) for alarm in route.incident.member_alarms]
    refs.extend(
        ("RELATION", relation)
        for graph in route.graph_evidence
        for relation in graph.relation_ids
    )
    return _ids(refs)


def project_read_evidence(tool: str, result: Any) -> EvidenceIds:
    """Revalidate real DTOs. Scope is checked by ReadAdapter before this call."""
    from app.common import tool_contracts as dto

    expected = {
        "get_fdc_summary": dto.FdcSummaryToolResult,
        "get_equipment_context": dto.EquipmentContextToolResult,
        "search_documents": dto.DocumentSearchToolResult,
        "get_chamber_parameter_history": dto.ChamberParameterHistoryToolResult,
        "get_metrology_result": dto.MetrologyResultToolResult,
    }.get(tool)
    if expected is None or not isinstance(result, expected):
        raise EvidenceError("U10_EVIDENCE_PROJECTION_INVALID")
    result = expected.model_validate(result.model_dump()).model_copy(deep=True)
    if not result.ok:
        return _ids(())
    if tool == "get_fdc_summary":
        return _ids(
            [("LOT_HIST", result.wafer.lot_hist_id)]
            + [("PARAMETER", p.parameter_id) for p in result.parameters]
        )
    if tool == "search_documents":
        return _ids(("CHUNK", hit.chunk_id) for hit in result.hits)
    # Compact equipment DTO lacks relation IDs. Parameter metadata is not an
    # FDC measurement. Aggregates/wafer IDs are not hypothesis-v3 citations.
    return _ids(())


def project_hypothesis_citations(hypothesis: Hypothesis) -> EvidenceIds:
    """Keep unsupported citations for the evaluator; never intersect available.

    This checks shape, NOT truth or generation provenance. Production hypothesis
    validation and later available/required set comparisons remain mandatory.
    """
    from app.agent.state import Hypothesis

    if not isinstance(hypothesis, Hypothesis):
        raise EvidenceError("U10_HYPOTHESIS_PROJECTION_INVALID")
    value = Hypothesis.model_validate(hypothesis.model_dump()).model_copy(deep=True)
    refs = [("ALARM", alarm.to_token()) for alarm in value.supporting_alarms]
    for namespace, values in (
        ("CHUNK", value.supporting_chunk_ids),
        ("RELATION", value.supporting_relation_ids),
        ("LOT_HIST", value.supporting_lot_hist_ids),
        ("PARAMETER", value.supporting_parameter_ids),
    ):
        refs.extend((namespace, item) for item in values)
    if value.origin_assessment is not None:
        refs.extend((ref.namespace, ref.id) for ref in value.origin_assessment.basis)
    for finding in value.parameter_findings:
        refs.append(("PARAMETER", finding.parameter_id))
        refs.extend(("LOT_HIST", item) for item in finding.lot_hist_ids)
    return _ids(refs)
