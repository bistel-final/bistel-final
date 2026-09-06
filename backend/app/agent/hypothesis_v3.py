"""V5-C-7.1 U8: 관측에서만 산술·조사 상태를 계산하고 원인 주장을 검증한다."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence

from app.agent.diagnostics import IncidentDiagnosticSnapshot
from app.agent.investigation_models import (
    ComparisonMatrix,
    InvestigationEvidence,
    OriginAssessment,
    ParameterFinding,
)
from app.agent.origin_diagnostics import (
    ORIGIN_REJECTION,
    OriginDiagnostics,
    capture_dropped,
)
from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import Hypothesis, HypothesisDraftV3
from app.common.tool_contracts import (
    DocumentSearchToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
)


def route_relations(route: ResolvedIncidentRoute) -> dict[str, str]:
    relations = {}
    for wafer in route.wafer_routes:
        anchors = [
            i
            for i, step in enumerate(wafer.steps)
            if step.chamber_id == route.incident.chamber_id
        ]
        if not anchors:
            continue
        for i, step in enumerate(wafer.steps):
            if i in anchors:
                relations[step.lot_hist_id] = "CURRENT"
            elif i < min(anchors):
                relations[step.lot_hist_id] = "UPSTREAM"
            elif i > max(anchors):
                relations[step.lot_hist_id] = "DOWNSTREAM"
    return relations


def comparison_matrix(
    route: ResolvedIncidentRoute,
    evidence: InvestigationEvidence,
) -> ComparisonMatrix:
    relations = route_relations(route)
    siblings = {
        sibling
        for item in route.graph_evidence
        if item.chamber_id == route.incident.chamber_id
        for sibling in item.sibling_chamber_ids
    }
    available = {
        "upstream": "UPSTREAM" in relations.values(),
        "downstream": "DOWNSTREAM" in relations.values(),
        "sibling": bool(siblings),
        "history": "CURRENT" in relations.values(),
        "metrology": bool(relations),
    }
    checked: set[str] = set()
    steps = {step.step_id for wafer in route.wafer_routes for step in wafer.steps}
    for call in evidence.successful_calls:
        args = call.input
        if call.tool_name == "get_fdc_summary":
            relation = relations.get(args.get("lot_hist_id"))
            if relation in {"UPSTREAM", "DOWNSTREAM"}:
                checked.add(relation.lower())
        elif call.tool_name == "get_chamber_parameter_history":
            chamber = args.get("chamber_id")
            if chamber == route.incident.chamber_id:
                checked.add("history")
            elif chamber in siblings:
                checked.add("sibling")
        elif call.tool_name == "get_metrology_result":
            if (
                args.get("lot_id") == route.incident.lot_id
                and args.get("step_id") in steps
            ):
                checked.add("metrology")
    return ComparisonMatrix(
        **{
            key: (
                "NOT_AVAILABLE"
                if not exists
                else "CHECKED"
                if key in checked
                else "NOT_CHECKED"
            )
            for key, exists in available.items()
        }
    )


def _excursion(parameter: ParameterSummaryItem) -> tuple[str, float] | None:
    target = parameter.target
    upper, lower = parameter.ctrl_upper, parameter.ctrl_lower
    high, low = parameter.value_max, parameter.value_min
    if target is None or not math.isfinite(target):
        return None
    above = upper is not None and high is not None and high > upper
    below = lower is not None and low is not None and low < lower
    ratios = []
    if above:
        if upper <= target:
            return None
        ratios.append((high - upper) / (upper - target))
    if below:
        if target <= lower:
            return None
        ratios.append((lower - low) / (target - lower))
    if not ratios or any(not math.isfinite(value) or value <= 0 for value in ratios):
        return None
    return "BOTH" if above and below else "ABOVE" if above else "BELOW", max(ratios)


_ORIGIN_MENTION = re.compile(
    r"(?P<UPSTREAM>상류|upstream)|(?P<DOWNSTREAM>하류|downstream)|"
    r"(?P<EQUIPMENT_COMMON>설비\s*공통|장비\s*공통|공통\s*(?:설비|장비|원인)|"
    r"equipment_common)",
    re.I,
)
_CLAUSE_BOUNDARY = re.compile(r"[.!?;\n]|지만|(?:이며|이고)\s+|그러나|반면")
_EXPLICIT_ASSERTION = re.compile(
    r"(?:확인|확정|입증|검증)(?:됨|되었|됐|되었습니다|되었다|했다|하였)|"
    r"(?:원인|영향)(?:이다|입니다)|(?:confirmed|established)",
    re.I,
)
_ORIGIN_LIMITATION = re.compile(
    r"미(?:조사|확인|검증)|"
    r"(?:조사|조회|검증|확인)(?:하지|되지|하지는|되지는)\s*않|"
    r"(?:확정|판단|단정|확인|배제)(?:할|하기|하기는)?\s*"
    r"(?:수\s*(?:없|없는)|불가|어렵|못)|"
    r"(?:정보|근거|자료)(?:가|는)?\s*(?:부족|없)|"
    r"(?:조사|조회|확인|검증)(?:이|가)?\s*(?:필요|예정|대상)|"
    r"가능성|가설|후보|의심",
    re.I,
)


def _narrative_origin_claims(draft: HypothesisDraftV3) -> set[str]:
    """Separate findings from proposed checks; a direction word is not a claim.

    This is a bounded consistency check, not general natural-language proof.
    A caveat applies only to its own clause/scope mention, never to another
    sentence or an explicit completed finding. Structured claims remain subject
    to the observation/citation checks even when prose contains a caveat.
    """
    findings = (
        draft.cause_summary,
        draft.evidence_synthesis,
        draft.impact_summary,
        *draft.observations,
    )
    proposals = (
        draft.uncertainty,
        *draft.verification_steps,
        *draft.limitations,
        *(
            text
            for alternative in draft.alternative_hypotheses
            for text in (alternative.summary, alternative.lower_rank_reason)
        ),
    )
    claimed: set[str] = set()
    for texts, finding_field in ((findings, True), (proposals, False)):
        for text in texts:
            for clause in _CLAUSE_BOUNDARY.split(text):
                mentions = tuple(_ORIGIN_MENTION.finditer(clause))
                for index, mention in enumerate(mentions):
                    end = (
                        mentions[index + 1].start()
                        if index + 1 < len(mentions)
                        else len(clause)
                    )
                    statement = clause[mention.start() : end]
                    explicit = _EXPLICIT_ASSERTION.search(statement) is not None
                    limitation = _ORIGIN_LIMITATION.search(statement) is not None
                    if explicit or (finding_field and not limitation):
                        assert mention.lastgroup is not None
                        claimed.add(mention.lastgroup)
    return claimed


def finalize_hypothesis(
    draft: HypothesisDraftV3,
    fdc_results: Sequence[FdcSummaryToolResult | None],
    route: ResolvedIncidentRoute,
    snapshot: IncidentDiagnosticSnapshot,
    documents: DocumentSearchToolResult | None,
    investigation: InvestigationEvidence,
    *,
    degrade_origin: bool = False,
    diagnostics: list[OriginDiagnostics] | None = None,
) -> Hypothesis:
    """잘못된 draft는 안전한 reason code ValueError로 거부한다."""

    fdc = {
        item.wafer.lot_hist_id: item
        for item in fdc_results
        if item is not None and item.ok and item.wafer is not None
    }
    relations = route_relations(route)
    current_wafers = {key for key in fdc if relations.get(key) == "CURRENT"}
    buckets = defaultdict(dict)
    for finding in draft.parameter_findings_draft:
        if not set(finding.lot_hist_ids) <= set(draft.supporting_lot_hist_ids):
            raise ValueError("LOT_HISTORY_CITATION_OUTSIDE_EVIDENCE")
        if finding.parameter_id not in draft.supporting_parameter_ids:
            raise ValueError("PARAMETER_CITATION_OUTSIDE_EVIDENCE")
        for key in finding.lot_hist_ids:
            if key not in fdc:
                raise ValueError("LOT_HISTORY_CITATION_OUTSIDE_EVIDENCE")
            for parameter in fdc[key].parameters:
                if parameter.parameter_id == finding.parameter_id:
                    excursion = _excursion(parameter)
                    if excursion is not None:
                        buckets[(parameter.parameter_id, parameter.recipe_step_no)][
                            key
                        ] = excursion
    findings = []
    for (parameter_id, step_no), values in sorted(buckets.items()):
        directions = {value[0] for value in values.values()}
        direction = next(iter(directions)) if len(directions) == 1 else "BOTH"
        ids = tuple(sorted(values))
        findings.append(
            ParameterFinding(
                parameter_id=parameter_id,
                step_no=step_no,
                direction=direction,
                excursion_ratio=max(value[1] for value in values.values()),
                wafer_scope=(
                    "SINGLE"
                    if len(ids) == 1
                    else "ALL"
                    if set(ids) == current_wafers
                    else "PARTIAL"
                ),
                lot_hist_ids=ids,
            )
        )
    if draft.predicted_fault_code.value != "OTH" and not findings:
        raise ValueError("PARAMETER_FINDING_REQUIRED")
    if any(item.parameter_id not in draft.cause_summary for item in findings):
        raise ValueError("CAUSE_SUMMARY_PARAMETER_MISSING")
    allowed = {
        "ALARM": set(snapshot.source_ids.alarm_refs),
        "CHUNK": {hit.chunk_id for hit in documents.hits}
        if documents is not None and documents.ok
        else set(),
        "RELATION": set(snapshot.source_ids.relation_ids),
        "LOT_HIST": set(snapshot.source_ids.lot_hist_ids),
        "PARAMETER": set(snapshot.source_ids.parameter_ids),
    }
    compared = comparison_matrix(route, investigation)
    narrative_claims = _narrative_origin_claims(draft)
    for dimension in ("UPSTREAM", "DOWNSTREAM"):
        if draft.origin_claim.scope == dimension or dimension in narrative_claims:
            if getattr(compared, dimension.lower()) != "CHECKED" or not any(
                relations.get(key) == dimension for key in draft.supporting_lot_hist_ids
            ):
                raise ValueError("ORIGIN_CLAIM_UNSUPPORTED")
    # Check ORIGINAL directional claims before any scope/basis mutation.
    dropped = [
        ref
        for ref in draft.origin_claim.basis_refs
        if ref.id not in allowed[ref.namespace]
    ]
    basis = tuple(
        ref for ref in draft.origin_claim.basis_refs if ref.id in allowed[ref.namespace]
    )
    scope = draft.origin_claim.scope
    if dropped:
        if not degrade_origin or (scope in {"UPSTREAM", "DOWNSTREAM"} and not basis):
            raise ValueError(ORIGIN_REJECTION)
        if not basis and scope in {"CURRENT_CHAMBER", "EQUIPMENT_COMMON"}:
            scope = "UNDETERMINED"
        if diagnostics is not None:
            diagnostics.append(capture_dropped(dropped))
    # Preserve the established all-dropped -> UNDETERMINED recovery, but do not
    # accept an originally empty basis or an unchecked cross-chamber claim.
    if scope == "EQUIPMENT_COMMON" or "EQUIPMENT_COMMON" in narrative_claims:
        if not basis or compared.sibling != "CHECKED":
            raise ValueError("ORIGIN_CLAIM_UNSUPPORTED")
    return Hypothesis(
        **draft.model_dump(exclude={"parameter_findings_draft", "origin_claim"}),
        parameter_findings=tuple(findings),
        origin_assessment=OriginAssessment(
            scope=scope,
            basis=basis,
            compared=compared,
            degraded=bool(dropped),
            degraded_reasons=(ORIGIN_REJECTION,) if dropped else (),
            dropped_basis_count=len(dropped),
        ),
    )
