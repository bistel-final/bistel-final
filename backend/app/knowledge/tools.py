"""Knowledge Agent Tools."""

from __future__ import annotations

from langchain_core.tools import tool

from app.analytics.db_pool import LogicalDb, PoolRole, pool_factory
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    fail,
)
from app.knowledge.document_search import DocumentSearchRepository
from app.knowledge.graph_query import GraphQueryRepository
from app.knowledge.service import DocumentSearchService, GraphService


# ==================
# 문서 RAG
# ==================
@tool
def search_documents(
    query: str,
    model_code: str | None = None,
    top_k: int = 4,
) -> DocumentSearchToolResult:
    """질문과 관련된 RAG 문서 chunk를 검색한다."""

    try:
        engine = pool_factory.get_engine(LogicalDb.RUNTIME, PoolRole.QUERY)
        service = DocumentSearchService(DocumentSearchRepository(engine))
        hits = service.search(query, top_k=top_k, model_code=model_code)
        return DocumentSearchToolResult(ok=True, hits=hits)
    except Exception as exc:
        return fail(DocumentSearchToolResult, f"DEPENDENCY_ERROR: {exc}")


# ==================
# Graph
# ==================


@tool
def get_equipment_context(chamber_id: str) -> EquipmentContextToolResult:
    """Chamber 기준 장비·공정 graph context를 조회한다."""

    try:
        service = GraphService(GraphQueryRepository())
        context = service.get_equipment_context(chamber_id)
        if context is None:
            return fail(
                EquipmentContextToolResult,
                f"NOT_FOUND: chamber_id={chamber_id}",
            )
        return EquipmentContextToolResult(
            ok=True,
            chamber_id=chamber_id,
            equipment_id=context.equipment.equipment_id,
            sibling_chamber_ids=sorted(
                chamber.chamber_id for chamber in context.sibling_chambers
            ),
            area=(
                context.area.area_id
                if context.area is not None
                else context.equipment.area_id
            ),
            model_code=context.equipment.model_code,
            process_step_id=(
                context.step.step_id
                if context.step is not None
                else context.equipment.step_id
            ),
            upstream_process_step_ids=_step_ids_before_current_step(context),
            downstream_process_step_ids=_step_ids_after_current_step(context),
            parameter_ids=sorted(
                parameter.parameter_id for parameter in context.parameters
            ),
            graph_revision=context.graph_revision,
        )
    except TimeoutError as exc:
        return fail(EquipmentContextToolResult, f"TIMEOUT: {exc}")
    except Exception as exc:
        return fail(EquipmentContextToolResult, f"DEPENDENCY_ERROR: {exc}")


def _step_ids_before_current_step(context: object) -> list[str]:
    related = _step_ids_from_next_step_relations(context, incoming=True)
    if related:
        return related

    current_seq = getattr(getattr(context, "step", None), "step_seq", None)
    if current_seq is None:
        return []

    return sorted(
        step.step_id
        for step in getattr(context, "adjacent_steps", [])
        if step.step_seq is not None and step.step_seq < current_seq
    )


def _step_ids_after_current_step(context: object) -> list[str]:
    related = _step_ids_from_next_step_relations(context, incoming=False)
    if related:
        return related

    current_seq = getattr(getattr(context, "step", None), "step_seq", None)
    if current_seq is None:
        return []

    return sorted(
        step.step_id
        for step in getattr(context, "adjacent_steps", [])
        if step.step_seq is not None and step.step_seq > current_seq
    )


def _step_ids_from_next_step_relations(
    context: object,
    *,
    incoming: bool,
) -> list[str]:
    current_step_id = getattr(getattr(context, "step", None), "step_id", None)
    if current_step_id is None:
        return []

    step_ids: set[str] = set()
    for relation in getattr(context, "relations", []):
        if relation.relation_type != "NEXT_STEP":
            continue
        from_step_id = _step_id_from_business_ref(
            relation.from_label,
            relation.from_business_id,
        )
        to_step_id = _step_id_from_business_ref(
            relation.to_label,
            relation.to_business_id,
        )
        if incoming and to_step_id == current_step_id and from_step_id is not None:
            step_ids.add(from_step_id)
        elif not incoming and from_step_id == current_step_id and to_step_id is not None:
            step_ids.add(to_step_id)

    return sorted(step_ids)


def _step_id_from_business_ref(label: str, business_id: str) -> str | None:
    if label != "ProcessStep":
        return None

    prefix = "step_id=s:"
    if not business_id.startswith(prefix):
        return None
    return business_id[len(prefix) :]
