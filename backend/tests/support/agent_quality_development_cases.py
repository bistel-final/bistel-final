"""Pure-synthetic development cases for C/Common V5-C-7.1.

This is not CF8, the final-data 12 incidents, or production evidence. All values,
identities and short documents below are authored here; no archive, DB, network
or reference corpus is read. Case names and acceptance notes belong to the
evaluator only, never to selector/hypothesis inputs. No fault-class answer or
required tool order is specified.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.agent.incident import ResolvedIncident
from app.agent.investigation import classify_history_trend
from app.agent.routing import GraphRouteEvidence, ResolvedIncidentRoute, WaferRoute
from app.agent.routing_repository import RouteStep
from app.agent.state import ToolBudget
from app.common.enums import AlarmSource, AlarmType, ToolCallStatus
from app.common.schemas import AlarmRef
from app.common.tool_contracts import (
    ChamberParameterHistoryToolResult,
    DocumentHit,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    HistoryBaseline,
    LotAggregate,
    MetrologyResultItem,
    MetrologyResultToolResult,
    ParameterSummaryItem,
    WaferContext,
)

NOW = datetime(2026, 1, 10, 12)
ALARM = AlarmRef(source=AlarmSource.TRACE, alarm_id="TA-01")
LOT = "LOT001"
CHAMBER = "EQP01-PM1"
STEP = "STEP-CURRENT"
MODEL = "MODEL-P1"
SIBLING = "EQP01-PM2"


@dataclass(frozen=True)
class DevelopmentCase:
    """Evaluator metadata; runtime receives only route and Tool DTOs."""

    case_id: str
    title: str
    acceptance: tuple[str, ...]
    current_means: tuple[float, ...]
    upstream_mean: float | None = None
    sibling_mean: float = 5.0
    prior_means: tuple[float, ...] = (5.0, 5.0, 5.0)
    document_timeout_once: bool = False
    repeated_document: bool = False

    @property
    def current_ids(self) -> tuple[str, ...]:
        return tuple(
            "LH-REP" if ordinal == 1 else f"LH-CURRENT-{ordinal}"
            for ordinal in range(1, len(self.current_means) + 1)
        )

    @property
    def diagnostic_wafer_refs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (identity, f"LOT001W{ordinal:03d}")
            for ordinal, identity in enumerate(self.current_ids, 1)
        )

    def route(self) -> ResolvedIncidentRoute:
        """A factual route within this invented dataset, not a source claim."""
        wafers = []
        for ordinal, _ in enumerate(self.current_means, 1):
            wafer = f"LOT001W{ordinal:03d}"
            current = RouteStep(
                lot_hist_id="LH-REP" if ordinal == 1 else f"LH-CURRENT-{ordinal}",
                lot_id=LOT,
                wafer_id=wafer,
                wafer_no=ordinal,
                step_id=STEP,
                area_id="AREA-CURRENT",
                equipment_id="EQP01",
                chamber_id=CHAMBER,
                recipe_id="RECIPE-P1",
                track_in_at=NOW + timedelta(minutes=ordinal),
                track_out_at=NOW + timedelta(minutes=ordinal + 1),
            )
            steps = (current,)
            if self.upstream_mean is not None:
                upstream = RouteStep(
                    lot_hist_id=f"LH-UPSTREAM-{ordinal}",
                    lot_id=LOT,
                    wafer_id=wafer,
                    wafer_no=ordinal,
                    step_id="STEP-UPSTREAM",
                    area_id="AREA-UPSTREAM",
                    equipment_id="EQP02",
                    chamber_id="EQP02-PM1",
                    recipe_id="RECIPE-P1-UP",
                    track_in_at=NOW - timedelta(minutes=10 - ordinal),
                    track_out_at=NOW - timedelta(minutes=9 - ordinal),
                )
                steps = (upstream, current)
            wafers.append(
                WaferRoute(
                    wafer_id=wafer,
                    member_alarms=(ALARM,) if ordinal == 1 else (),
                    steps=steps,
                )
            )
        return ResolvedIncidentRoute(
            incident=ResolvedIncident(
                lot_id=LOT,
                chamber_id=CHAMBER,
                requested_alarm=ALARM,
                representative_alarm=ALARM,
                member_alarms=(ALARM,),
            ),
            wafer_routes=tuple(wafers),
            graph_evidence=(
                GraphRouteEvidence(
                    chamber_id=CHAMBER,
                    equipment_id="EQP01",
                    model_code=MODEL,
                    process_step_id=STEP,
                    upstream_process_step_ids=("STEP-UPSTREAM",)
                    if self.upstream_mean is not None
                    else (),
                    downstream_process_step_ids=(),
                    relation_ids=("REL-P1-PART-OF", "REL-P1-PERFORMS")
                    + (("REL-P1-NEXT",) if self.upstream_mean is not None else ()),
                    graph_revision="synthetic-p1-graph-v1",
                    sibling_chamber_ids=(SIBLING,),
                ),
            ),
            route_consistency=True,
            mismatches=(),
        )


CASES = (
    DevelopmentCase(
        "SYN-01",
        "알람과 현재 정상 관측의 불일치",
        (
            "현재 정상값과 저장 알람의 불일치를 숨기지 않는다",
            "현재값만으로 고장을 단정하지 않는다",
        ),
        (5.0, 5.0),
    ),
    DevelopmentCase(
        "SYN-02",
        "단일 현재 이탈",
        (
            "실제 상한 이탈값을 근거로 설명한다",
            "문서의 점검 절차와 확인된 관측을 구분한다",
        ),
        (12.0,),
    ),
    DevelopmentCase(
        "SYN-03",
        "상류 이탈과 현재 정상의 대비",
        (
            "상류를 주장할 때 실제 상류 관측을 인용한다",
            "현재 정상과 상류 이탈을 구분하며 전파를 단정하지 않는다",
        ),
        (5.0,),
        upstream_mean=12.0,
    ),
    DevelopmentCase(
        "SYN-04",
        "현재 chamber 이탈과 형제 정상",
        (
            "현재 chamber 특이성을 주장할 때 동일 설비 형제 관측과 비교한다",
            "계측 결과를 고장 종류 정답으로 쓰지 않는다",
        ),
        (12.0, 12.0),
    ),
    DevelopmentCase(
        "SYN-05",
        "과거 증가 추세",
        (
            "시간순 과거 집계와 현재값을 비교한다",
            "관측된 추세와 그 원인의 확정을 구분한다",
        ),
        (12.0,),
        prior_means=(8.0, 7.0, 6.0),
    ),
    DevelopmentCase(
        "SYN-06",
        "문서 일시 실패 뒤 회복",
        (
            "실패 관측을 성공 근거로 사용하지 않는다",
            "회복된 문서 또는 다른 확인 근거로 답변하고 남은 한계를 기록한다",
        ),
        (12.0,),
        document_timeout_once=True,
    ),
    DevelopmentCase(
        "SYN-07",
        "두 번째 wafer에서만 이탈",
        (
            "대표 wafer 정상만으로 전체 정상이라고 일반화하지 않는다",
            "두 번째 wafer의 이탈과 표본 차이를 구분한다",
        ),
        (5.0, 12.0),
    ),
    DevelopmentCase(
        "SYN-08",
        "같은 문서가 반복 반환됨",
        (
            "질의 문자열이 달라도 같은 chunk는 새 관측으로 세지 않는다",
            "신규 정보가 없을 때 예산 소진만을 목표로 조사하지 않는다",
        ),
        (12.0,),
        repeated_document=True,
    ),
)


def _parameter(value: float) -> ParameterSummaryItem:
    oos = value < 0 or value > 10
    ooc = value < 1 or value > 9
    return ParameterSummaryItem(
        parameter_id="P1",
        parameter_name="P1",
        unit="a.u.",
        recipe_step_no=1,
        value_mean=value,
        value_std=0.0,
        value_min=value,
        value_max=value,
        point_cnt=6,
        ooc_point_cnt=6 if ooc else 0,
        oos_point_cnt=6 if oos else 0,
        spec_lower=0.0,
        ctrl_lower=1.0,
        target=5.0,
        ctrl_upper=9.0,
        spec_upper=10.0,
        alarm_type=AlarmType.OOS if oos else AlarmType.OOC if ooc else AlarmType.IN,
    )


class SyntheticInvestigationTools:
    """In-memory read ports with an ordered, per-run attempt ledger.

    The ledger reports actual results, including a deliberate document timeout.
    It is not a DB attestation. ToolBudget uses the production DTO for graph
    compatibility only. Delivery is unreachable rather than a fake success.
    """

    def __init__(self, case: DevelopmentCase, *, budget_limit=26, read_limit=24):
        if budget_limit - read_limit != 2 or read_limit < 1:
            raise ValueError("SYNTHETIC_BUDGET_INVALID")
        self.case = case
        self.route = case.route()
        self.budget_limit, self.read_limit = budget_limit, read_limit
        self.calls: list[tuple[str, Any]] = []
        self._ledger: list[SimpleNamespace] = []
        self.budget_connections, self.finish_connections, self.usage_connections = (
            [],
            [],
            [],
        )
        self.llm_usage: list[tuple[int, int]] = []
        self.action = None
        self.send_count = 0

    def history(self, run_id):
        return tuple(deepcopy(row) for row in self._ledger if row.run_id == run_id)

    def budget(self, run_id):
        rows = self.history(run_id)
        return ToolBudget(
            max_calls=self.budget_limit,
            used=len(rows),
            by_tool=dict(Counter(row.tool_name for row in rows)),
            send_budget=2,
            send_used=0,
            pending_reservations=sum(row.output is None for row in rows),
        )

    def budget_from_connection(self, connection, run_id):
        self.budget_connections.append(connection)
        return self.budget(run_id)

    def _call(self, run_id, name, alias, request, operation):
        history = self.history(run_id)
        if len(history) >= self.read_limit:
            raise RuntimeError("SYNTHETIC_READ_BUDGET_EXHAUSTED")
        if sum(row.tool_name == name for row in history) >= 8:
            raise RuntimeError("SYNTHETIC_SAME_TOOL_BUDGET_EXHAUSTED")
        row = SimpleNamespace(
            run_id=run_id,
            tool_name=name,
            input=request.model_dump(mode="json"),
            output=None,
            # Production uses an ERROR/empty-output reservation sentinel, not
            # a PENDING enum. Count it until an actual result is recorded.
            status=ToolCallStatus.ERROR,
        )
        self._ledger.append(row)
        self.calls.append((alias, request.model_copy(deep=True)))
        try:
            result = operation()
        except BaseException:
            row.status = ToolCallStatus.ERROR
            raise
        row.output = result.model_dump(mode="json")
        row.status = (
            ToolCallStatus.SUCCESS
            if result.ok
            else ToolCallStatus.TIMEOUT
            if result.reason.startswith("TIMEOUT:")
            else ToolCallStatus.ERROR
        )
        return result

    def fdc_summary(self, run_id, request):
        def read():
            steps = {s.lot_hist_id: s for w in self.route.wafer_routes for s in w.steps}
            step = steps.get(request.lot_hist_id)
            if step is None:
                return FdcSummaryToolResult(ok=False, reason="NOT_FOUND: target")
            value = (
                self.case.upstream_mean
                if step.step_id == "STEP-UPSTREAM"
                else self.case.current_means[step.wafer_no - 1]
            )
            return FdcSummaryToolResult(
                ok=True,
                wafer=WaferContext(
                    lot_hist_id=step.lot_hist_id,
                    lot_id=step.lot_id,
                    wafer_no=step.wafer_no,
                    chamber_id=step.chamber_id,
                    equipment_id=step.equipment_id,
                    step_id=step.step_id,
                    recipe_id=step.recipe_id,
                ),
                parameters=[_parameter(value)],
            )

        return self._call(run_id, "get_fdc_summary", "fdc", request, read)

    def equipment_context(self, run_id, request):
        def read():
            if request.chamber_id != CHAMBER:
                return EquipmentContextToolResult(ok=False, reason="NOT_FOUND: chamber")
            graph = self.route.graph_evidence[0]
            return EquipmentContextToolResult(
                ok=True,
                chamber_id=CHAMBER,
                equipment_id="EQP01",
                area="AREA-CURRENT",
                model_code=MODEL,
                process_step_id=STEP,
                upstream_process_step_ids=list(graph.upstream_process_step_ids),
                downstream_process_step_ids=[],
                sibling_chamber_ids=[SIBLING],
                parameter_ids=["P1"],
                graph_revision=graph.graph_revision,
            )

        return self._call(run_id, "get_equipment_context", "equipment", request, read)

    def chamber_parameter_history(self, run_id, request, **context):
        def read():
            scope = context.get("scope")
            expected = CHAMBER if scope == "CURRENT" else SIBLING
            if (
                scope not in {"CURRENT", "SIBLING"}
                or request.chamber_id != expected
                or request.parameter_id != "P1"
                or request.step_no != 1
                or context.get("current_lot_id") != LOT
                or context.get("incident_step_id") != STEP
            ):
                return ChamberParameterHistoryToolResult(
                    ok=False, reason="POLICY_REJECTED: history scope"
                )
            values = (
                self.case.current_means
                if scope == "CURRENT"
                else (self.case.sibling_mean,) * len(self.case.current_means)
            )
            current = _aggregate(LOT, values, NOW)
            prior_values = (
                self.case.prior_means[: request.n_lots] if scope == "CURRENT" else ()
            )
            prior = [
                _aggregate(
                    f"LOT-PRIOR-{index}",
                    (value,) * len(values),
                    NOW - timedelta(days=index),
                )
                for index, value in enumerate(prior_values, 1)
            ]
            trend, mean, sd = classify_history_trend(current.lot_mean, prior_values)
            return ChamberParameterHistoryToolResult(
                ok=True,
                scope=scope,
                chamber_id=request.chamber_id,
                parameter_id="P1",
                step_no=1,
                current=current,
                prior=prior,
                baseline=HistoryBaseline(
                    mean_hist=mean, sd_hist=sd, prior_lot_count=len(prior)
                ),
                trend=trend,
                comparison=scope,
                sample_count=len(values),
            )

        return self._call(
            run_id, "get_chamber_parameter_history", "history", request, read
        )

    def metrology_result(self, run_id, request):
        def read():
            if request.lot_id != LOT or request.step_id != STEP:
                return MetrologyResultToolResult(
                    ok=False, reason="NOT_FOUND: metrology target"
                )
            rows = [
                MetrologyResultItem(
                    wafer_id=f"LOT001W{ordinal:03d}",
                    measure_type="CD-P1",
                    measured_value=5.0,
                    spec_lower=4.0,
                    spec_upper=6.0,
                    alarm_result="PASS",
                    measured_at=NOW + timedelta(hours=1),
                )
                for ordinal in range(1, len(self.case.current_means) + 1)
            ]
            return MetrologyResultToolResult(
                ok=True,
                lot_id=LOT,
                step_id=STEP,
                results=rows,
                fail_count=0,
                disclaimer=(
                    "정적 표본의 계측 결과이며 "
                    "고장 종류 정답이나 조치 후 효과가 아니다."
                ),
            )

        return self._call(run_id, "get_metrology_result", "metrology", request, read)

    def document_search(self, run_id, request):
        def read():
            attempts = sum(
                row.tool_name == "search_documents" for row in self.history(run_id)
            )
            if self.case.document_timeout_once and attempts == 1:
                return DocumentSearchToolResult(
                    ok=False, reason="TIMEOUT: temporary document read"
                )
            if request.model_code not in {None, MODEL}:
                return DocumentSearchToolResult(ok=True, hits=[])
            topic = _document_topic(request.query)
            if self.case.repeated_document:
                topic = "general"
            title, text = DOCUMENTS[topic]
            return DocumentSearchToolResult(
                ok=True,
                hits=[
                    DocumentHit(
                        chunk_id=f"DOC-P1-{topic.upper()}",
                        document_id="DOC-P1",
                        title=title,
                        section="점검",
                        score=0.8,
                        content=text,
                        model_code=MODEL,
                    )
                ],
            )

        return self._call(run_id, "search_documents", "documents", request, read)

    def send_action(self, *_args, **_kwargs):
        self.send_count += 1
        raise RuntimeError("SYNTHETIC_EXTERNAL_EFFECT_FORBIDDEN")


def _aggregate(lot, values, when):
    mean = sum(values) / len(values)
    return LotAggregate(
        lot_id=lot,
        lot_mean=mean,
        lot_std=(
            (sum((value - mean) ** 2 for value in values) / (len(values) - 1)) ** 0.5
            if len(values) > 1
            else None
        ),
        lot_min=min(values),
        lot_max=max(values),
        wafer_count=len(values),
        ooc_wafers=sum(value < 1 or value > 9 for value in values),
        oos_wafers=sum(value < 0 or value > 10 for value in values),
        evaluation_missing=0,
        track_in_from=when + timedelta(minutes=1),
        track_in_to=when + timedelta(minutes=len(values)),
    )


DOCUMENTS = {
    "general": (
        "P1 점검 범위",
        "P1는 목표 5, 관리범위 1~9, 규격범위 0~10이다. "
        "저장 알람과 현재 관측이 다르면 시각과 wafer별 표본을 대조한다. "
        "관측되지 않은 원인을 확정하지 않는다.",
    ),
    "direction": (
        "P1 상한 이탈 점검",
        "P1 상한 이탈 시 설정값과 센서 보정 이력, wafer별 편차를 점검한다. "
        "현재 수치 이탈만으로 장비 고장 종류를 확정할 수 없다.",
    ),
    "history": (
        "P1 추세 점검",
        "과거 LOT 집계는 시간순으로 현재값과 비교한다. "
        "지속 증가와 갑작스런 변화는 구분하되 "
        "보정·정비 기록 확인 전 원인을 확정하지 않는다.",
    ),
    "routing": (
        "P1 공정 간 비교",
        "동일 wafer의 실제 상류와 현재 공정 관측을 비교한다. "
        "상류 이탈과 현재 정상은 서로 다른 사실이며 "
        "공정 관계만으로 전파를 확정하지 않는다.",
    ),
    "sibling": (
        "P1 chamber 비교",
        "동일 설비의 형제 chamber를 같은 LOT·공정·파라미터로 비교한다. "
        "현재만 이탈하고 형제가 정상이면 chamber별 설정과 센서를 "
        "우선 점검하되 원인을 확정하지 않는다.",
    ),
}


def _document_topic(query):
    text = query.lower()
    for topic, terms in (
        ("routing", ("상류", "전파", "upstream", "routing")),
        ("sibling", ("형제", "sibling", "동일 설비")),
        ("history", ("과거", "이력", "추세", "drift", "history")),
        ("direction", ("상한", "이탈", "초과", "upper", "above")),
    ):
        if any(term in text for term in terms):
            return topic
    return "general"
