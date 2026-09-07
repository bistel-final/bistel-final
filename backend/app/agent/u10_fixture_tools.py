"""Deterministic, experiment-only read ports; no network or public DB handles.

Each instance belongs to one attempt. Values below are deliberate CF treatments,
not production measurements. Candidate identities and sample counts are source
derived. No oracle, research verdict or fault ground truth enters this object.
"""

from __future__ import annotations

from app.agent.release_artifacts import EvidenceError
from app.agent.u10_counterfactual import SCENARIOS, _time, build_route


class FixtureTools:
    def __init__(self, source, fixture_id):
        from app.agent.u10_observations import ObservationContext

        self.source = source
        self.route, self.current_ids = build_route(source, fixture_id)
        self.scenario = next(s[3] for s in SCENARIOS if s[0] == fixture_id)
        self.graph = self.route.graph_evidence[0]
        self.rows = source["tables"]["lot_history"]
        self.by_id = {r["lot_hist_id"]: r for r in self.rows}
        self.context = ObservationContext(
            "U10-FIXTURE",
            self.route,
            self.current_ids,
            document_model_code=self.graph.model_code,
        )
        self.candidates = self.context.build_context().candidates
        alarms = [
            a
            for a in source["tables"]["trace_alarm_history"]
            if (a["lot"], a["chamber"])
            == (self.route.incident.lot_id, self.route.incident.chamber_id)
        ]
        self.parameter = sorted({a["parameter"] for a in alarms})[0]
        parameters = sorted(
            {
                e["parameter"]
                for e in source["tables"]["evaluation"]
                if e["lot_hist_id"] == self.current_ids[0]
                and e["parameter"] != self.parameter
            }
        )
        if not parameters:
            raise EvidenceError("U10_SOURCE_PARAMETERS_INVALID")
        self.extra_parameter = parameters[0]
        self.document_calls = 0

    def fdc(self, request):
        from app.common import tool_contracts as dto
        from app.common.enums import AlarmType

        c = next(
            (c for c in self.candidates.fdc if c.lot_hist_id == request["lot_hist_id"]),
            None,
        )
        if c is None:
            raise EvidenceError("U10_READ_SCOPE_INVALID")
        row = self.by_id[c.lot_hist_id]
        parameter = self.parameter
        if c.relation != "CURRENT":
            parameter = sorted(
                {
                    e["parameter"]
                    for e in self.source["tables"]["evaluation"]
                    if e["lot_hist_id"] == c.lot_hist_id
                }
            )[0]
        above, below = c.relation == "CURRENT", False
        if self.scenario == "BELOW":
            above, below = False, c.relation == "CURRENT"
        elif self.scenario == "UPSTREAM":
            above = c.relation == "UPSTREAM"
        elif self.scenario == "EXTRA_FDC":
            above = c.relation == "CURRENT" and c.lot_hist_id != self.current_ids[0]
            parameter = self.extra_parameter if above else parameter
        return dto.FdcSummaryToolResult(
            ok=True,
            wafer=dto.WaferContext(
                lot_hist_id=c.lot_hist_id,
                lot_id=row["lot_id"],
                wafer_no=int(row["wafer_no"]),
                chamber_id=row["chamber_id"],
                equipment_id=row["equipment_id"],
                step_id=row["step_id"],
                recipe_id=row["recipe_id"],
            ),
            parameters=[
                dto.ParameterSummaryItem(
                    parameter_id=parameter,
                    parameter_name=parameter,
                    recipe_step_no=1,
                    value_mean=12.0 if above else -2.0 if below else 5.0,
                    value_min=-3.0 if below else 11.0 if above else 4.0,
                    value_max=13.0 if above else -1.0 if below else 6.0,
                    point_cnt=6,
                    ooc_point_cnt=6 if above or below else 0,
                    oos_point_cnt=6 if above or below else 0,
                    spec_lower=0.0,
                    ctrl_lower=1.0,
                    target=5.0,
                    ctrl_upper=9.0,
                    spec_upper=10.0,
                    alarm_type=AlarmType.OOS if above or below else AlarmType.IN,
                )
            ],
        )

    def equipment(self, request):
        from app.common.tool_contracts import EquipmentContextToolResult

        g = self.graph
        if request != {"chamber_id": g.chamber_id}:
            raise EvidenceError("U10_READ_SCOPE_INVALID")
        return EquipmentContextToolResult(
            ok=True,
            chamber_id=g.chamber_id,
            equipment_id=g.equipment_id,
            model_code=g.model_code,
            area=self.by_id[self.current_ids[0]]["area_id"],
            process_step_id=g.process_step_id,
            graph_revision=g.graph_revision,
            sibling_chamber_ids=list(g.sibling_chamber_ids),
            upstream_process_step_ids=list(g.upstream_process_step_ids),
            downstream_process_step_ids=list(g.downstream_process_step_ids),
        )

    def prior_lots(self):
        grouped = {}
        for row in self.rows:
            if (row["chamber_id"], row["step_id"]) == (
                self.graph.chamber_id,
                self.graph.process_step_id,
            ):
                grouped.setdefault(row["lot_id"], []).append(_time(row["track_in_at"]))
        current = self.route.incident.lot_id
        before = min(grouped[current])
        return sorted(
            (
                lot
                for lot, dates in grouped.items()
                if lot != current and max(dates) < before
            ),
            key=lambda lot: (max(grouped[lot]), lot),
            reverse=True,
        )[:3]

    def history(self, payload):
        from app.agent.investigation import classify_history_trend
        from app.common import tool_contracts as dto

        internal = payload["_context"]
        scope = internal["scope"]
        chamber, lot = payload["chamber_id"], self.route.incident.lot_id
        expected_chambers = (
            (self.graph.chamber_id,)
            if scope == "CURRENT"
            else self.graph.sibling_chamber_ids
        )
        if chamber not in expected_chambers or internal["current_lot_id"] != lot:
            raise EvidenceError("U10_READ_SCOPE_INVALID")

        def aggregate(lot_id, value):
            rows = [
                r
                for r in self.rows
                if r["lot_id"] == lot_id
                and r["chamber_id"] == chamber
                and r["step_id"] == self.graph.process_step_id
            ]
            if not rows:
                raise EvidenceError("U10_FIXTURE_HISTORY_UNAVAILABLE")
            return dto.LotAggregate(
                lot_id=lot_id,
                lot_mean=value,
                lot_std=0.0,
                lot_min=value,
                lot_max=value,
                wafer_count=len(rows),
                ooc_wafers=len(rows) if value > 9 or value < 1 else 0,
                oos_wafers=len(rows) if value > 10 or value < 0 else 0,
                evaluation_missing=0,
                track_in_from=min(_time(r["track_in_at"]) for r in rows),
                track_in_to=max(_time(r["track_in_at"]) for r in rows),
            )

        value = 12.0
        if scope == "SIBLING" or self.scenario == "UPSTREAM":
            value = 5.0
        elif self.scenario == "BELOW":
            value = -2.0
        elif (
            self.scenario == "EXTRA_FDC"
            and payload["parameter_id"] != self.extra_parameter
        ):
            value = 5.0
        current = aggregate(lot, value)
        prior_ids = self.prior_lots() if scope == "CURRENT" else []
        prior = [
            aggregate(
                p,
                float(len(prior_ids) - i) if self.scenario == "HISTORY_DRIFT" else 5.0,
            )
            for i, p in enumerate(prior_ids)
        ]
        trend, mean, sd = classify_history_trend(
            current.lot_mean, [p.lot_mean for p in prior]
        )
        return dto.ChamberParameterHistoryToolResult(
            ok=True,
            scope=scope,
            chamber_id=chamber,
            parameter_id=payload["parameter_id"],
            step_no=payload["step_no"],
            current=current,
            prior=prior,
            baseline=dto.HistoryBaseline(
                mean_hist=mean, sd_hist=sd, prior_lot_count=len(prior)
            ),
            trend=trend,
            comparison=scope,
            sample_count=current.wafer_count,
        )

    def metrology(self, payload):
        from app.agent.investigation import METROLOGY_DISCLAIMER
        from app.common import tool_contracts as dto

        if payload != {
            "lot_id": self.route.incident.lot_id,
            "step_id": self.graph.process_step_id,
        }:
            raise EvidenceError("U10_READ_SCOPE_INVALID")
        rows = [
            r
            for r in self.source["tables"]["metrology"]
            if (r["lot_id"], r["step_id"]) == (payload["lot_id"], payload["step_id"])
        ]
        results = [
            dto.MetrologyResultItem(
                wafer_id=r["wafer_id"],
                measure_type=r["measure_type"],
                measured_value=float(r["measured_value"]),
                spec_lower=float(r["spec_lower"]),
                spec_upper=float(r["spec_upper"]),
                alarm_result=r["alarm_result"],
                measured_at=_time(r["measured_at"]),
            )
            for r in rows
        ]
        return dto.MetrologyResultToolResult(
            ok=True,
            lot_id=payload["lot_id"],
            step_id=payload["step_id"],
            results=results,
            fail_count=sum(r.alarm_result == "FAIL" for r in results),
            disclaimer=METROLOGY_DISCLAIMER,
        )

    def documents(self, payload):
        from app.common.tool_contracts import (
            DocumentHit,
            DocumentSearchToolResult,
            fail,
        )

        if payload.get("model_code") != self.graph.model_code:
            raise EvidenceError("U10_DOCUMENT_MODEL_REQUIRED")
        self.document_calls += 1
        if self.scenario == "DOCUMENT_RECOVERY" and self.document_calls == 1:
            return fail(
                DocumentSearchToolResult, "TIMEOUT: controlled document recovery"
            )
        query = payload["query"].lower()
        relevant = {
            "DIRECTION": any(k in query for k in ("상한", "초과", "above", "upper")),
            "EXTRA_FDC": self.extra_parameter.lower() in query,
            "BELOW": any(k in query for k in ("하한", "미달", "below", "lower")),
        }.get(self.scenario, True)
        key = self.scenario.lower() if relevant else "general"
        explanations = {
            "DIRECTION": "상한 이탈의 방향을 확인하고 관련 설정을 점검한다.",
            "EXTRA_FDC": (
                f"{self.extra_parameter} 추가 파라미터 이탈을 다른 wafer와 비교한다."
            ),
            "DOCUMENT_RECOVERY": (
                "문서 조회 장애가 복구된 뒤 관찰 근거를 다시 확인한다."
            ),
            "EARLY_STOP": "수집한 파라미터와 문서가 충분하면 조사를 종료할 수 있다.",
            "BELOW": "하한 이탈의 방향을 확인하고 관련 설정을 점검한다.",
            "UPSTREAM": "동일 wafer의 이전 공정 관측을 비교해 발생 위치를 판단한다.",
            "SIBLING_NORMAL": "같은 설비의 다른 chamber가 정상인지 대조한다.",
            "HISTORY_DRIFT": (
                "현재와 이전 lot의 시간순 추세를 비교해 점진적 변화를 확인한다."
            ),
        }
        content = (
            explanations[self.scenario]
            if relevant
            else "FDC 파라미터와 규격을 확인한다."
        )
        return DocumentSearchToolResult(
            ok=True,
            hits=[
                DocumentHit(
                    chunk_id="u10-" + key,
                    document_id="u10-experiment-guide",
                    title="실험용 FDC 점검 근거",
                    section="점검",
                    score=0.99,
                    content=content,
                    model_code=self.graph.model_code,
                )
            ],
        )

    def ports(self):
        from app.agent.u10_read_adapter import ReadPorts

        return ReadPorts(
            self.fdc, self.equipment, self.documents, self.history, self.metrology
        )

    def fixed_inputs(self):
        from app.agent import react
        from app.agent.u10_observations import ObservationContext
        from app.agent.u10_read_execution import DocumentContext

        bound = {
            "CURRENT_FDC": {"lot_hist_id": self.current_ids[0]},
            "EQUIPMENT": {"chamber_id": self.graph.chamber_id},
            "METROLOGY": {
                "lot_id": self.route.incident.lot_id,
                "step_id": self.graph.process_step_id,
            },
        }
        adjacent = [c for c in self.candidates.fdc if c.relation == "UPSTREAM"]
        adjacent = adjacent or [
            c for c in self.candidates.fdc if c.relation == "DOWNSTREAM"
        ]
        if adjacent:
            bound["ADJACENT_FDC"] = {"lot_hist_id": adjacent[0].lot_hist_id}
        # Detached planning context; this must not prepopulate the run context.
        state = ObservationContext(
            "U10-FIXED-PLAN",
            self.route,
            self.current_ids,
            document_model_code=self.graph.model_code,
        )
        state.record(
            "get_fdc_summary", bound["CURRENT_FDC"], self.fdc(bound["CURRENT_FDC"])
        )
        state.record(
            "get_equipment_context",
            bound["EQUIPMENT"],
            self.equipment(bound["EQUIPMENT"]),
        )
        ctx = state.build_context()
        for scope, slot in (("CURRENT", "HISTORY"), ("SIBLING", "SIBLING")):
            candidates = [c for c in ctx.candidates.history if c.scope == scope]
            if candidates:
                selection = react.ReactSelection(
                    rationale_summary="fixed scope",
                    next="get_chamber_parameter_history",
                    arguments=react.ReactArguments(
                        history_candidate_id=candidates[0].candidate_id
                    ),
                )
                bound[slot] = react.resolve_call(selection, ctx)["request"]
        return bound, DocumentContext(
            model_code=self.graph.model_code, parameter_ids=[self.parameter]
        )
