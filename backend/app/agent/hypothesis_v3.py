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


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _observed_parameter_departure(parameter: ParameterSummaryItem) -> bool:
    """An observed departure need not support a target-normalized ratio.

    Stored alarms, fault labels and the model's finding draft are not evidence
    for this test. A populated FDC sample must itself contain an excursion count
    or a finite value outside a usable control/specification bound.
    """

    if parameter.point_cnt <= 0:
        return False
    if parameter.ooc_point_cnt > 0 or parameter.oos_point_cnt > 0:
        return True
    values = (
        parameter.value_min,
        parameter.value_max,
        parameter.value_mean,
    )
    for lower, upper in (
        (parameter.ctrl_lower, parameter.ctrl_upper),
        (parameter.spec_lower, parameter.spec_upper),
    ):
        if _finite(lower) and _finite(upper) and lower > upper:
            continue
        if any(
            _finite(value)
            and (
                (_finite(lower) and value < lower) or (_finite(upper) and value > upper)
            )
            for value in values
        ):
            return True
    return False


def _current_origin_supported(
    draft: HypothesisDraftV3,
    fdc: dict[str, FdcSummaryToolResult],
    route: ResolvedIncidentRoute,
    relations: dict[str, str],
    investigation: InvestigationEvidence,
) -> bool:
    """Require a cited current disturbance, not merely a current observation.

    A genuine code-classified current-history shift can support a location
    hypothesis even inside absolute FDC limits. This consumes the existing
    trend contract; it neither changes its truth table nor asserts causation.
    """

    current_keys: set[tuple[str, int]] = set()
    for lot_hist_id in draft.supporting_lot_hist_ids:
        result = fdc.get(lot_hist_id)
        if (
            result is None
            or relations.get(lot_hist_id) != "CURRENT"
            or result.wafer.lot_id != route.incident.lot_id
            or result.wafer.chamber_id != route.incident.chamber_id
        ):
            continue
        for parameter in result.parameters:
            if parameter.parameter_id not in draft.supporting_parameter_ids:
                continue
            if _observed_parameter_departure(parameter):
                return True
            if parameter.point_cnt > 0:
                current_keys.add((parameter.parameter_id, parameter.recipe_step_no))

    for history in investigation.history:
        current = history.current
        if (
            not history.ok
            or history.scope != "CURRENT"
            or history.comparison != "CURRENT"
            or history.chamber_id != route.incident.chamber_id
            or (history.parameter_id, history.step_no) not in current_keys
            or history.trend not in {"DRIFT_UP", "DRIFT_DOWN", "SUDDEN"}
            or not history.sample_count
            or current is None
            or current.lot_id != route.incident.lot_id
            or current.wafer_count <= 0
            or not _finite(current.lot_mean)
            or history.baseline is None
            or history.baseline.prior_lot_count < 2
            or sum(
                prior.wafer_count > 0 and _finite(prior.lot_mean)
                for prior in history.prior
            )
            < 2
        ):
            continue
        if any(
            call.tool_name == "get_chamber_parameter_history"
            and call.input.get("chamber_id") == history.chamber_id
            and call.input.get("parameter_id") == history.parameter_id
            and call.input.get("step_no") == history.step_no
            for call in investigation.successful_calls
        ):
            return True
    return False


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
    """새 생성은 항상 현재 근거 요건을 적용하며 legacy 선택 입력을 받지 않는다."""

    return _finalize_hypothesis(
        draft,
        fdc_results,
        route,
        snapshot,
        documents,
        investigation,
        enforce_current_origin=True,
        degrade_origin=degrade_origin,
        diagnostics=diagnostics,
    )


def _recount_hypothesis(
    draft: HypothesisDraftV3,
    fdc_results: Sequence[FdcSummaryToolResult | None],
    route: ResolvedIncidentRoute,
    snapshot: IncidentDiagnosticSnapshot,
    documents: DocumentSearchToolResult | None,
    investigation: InvestigationEvidence,
    *,
    hypothesis_prompt_version: str,
) -> Hypothesis:
    """Private captured-artifact recount only; never a generation entry point.

    ko1/ko2 predate the current-origin grounding rule. Their persisted, bound
    prompt provenance selects the old rule without changing the saved answer.
    Every other citation/arithmetic/scope check remains common with generation.
    """

    if hypothesis_prompt_version not in {
        "agent-hypothesis-v3-ko1",
        "agent-hypothesis-v3-ko2",
        "agent-hypothesis-v3-ko3",
    }:
        raise ValueError("HYPOTHESIS_PROMPT_VERSION_INVALID")
    return _finalize_hypothesis(
        draft,
        fdc_results,
        route,
        snapshot,
        documents,
        investigation,
        enforce_current_origin=hypothesis_prompt_version == "agent-hypothesis-v3-ko3",
    )


def _finalize_hypothesis(
    draft: HypothesisDraftV3,
    fdc_results: Sequence[FdcSummaryToolResult | None],
    route: ResolvedIncidentRoute,
    snapshot: IncidentDiagnosticSnapshot,
    documents: DocumentSearchToolResult | None,
    investigation: InvestigationEvidence,
    *,
    enforce_current_origin: bool,
    degrade_origin: bool = False,
    diagnostics: list[OriginDiagnostics] | None = None,
) -> Hypothesis:
    """Shared arithmetic, citations and version-selected origin validation."""

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
    # CURRENT_CHAMBER is an origin/location hypothesis, not the location where
    # an otherwise normal sample was read. Apply this only to new generation;
    # historical Hypothesis/OriginAssessment readers remain unchanged. Preserve
    # U11's all-dropped -> UNDETERMINED recovery above.
    if (
        enforce_current_origin
        and scope == "CURRENT_CHAMBER"
        and not _current_origin_supported(draft, fdc, route, relations, investigation)
    ):
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
