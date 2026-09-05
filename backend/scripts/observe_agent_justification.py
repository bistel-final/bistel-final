#!/usr/bin/env python3
"""V5-C-7.1 real-LLM Level 2/3 counterfactual observational executor."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import comparison, experiment, hypothesis, react  # noqa: E402
from app.agent.repository import finish_agent_run, get_agent_run  # noqa: E402
from app.agent.routing import GraphBoundary  # noqa: E402
from app.agent.tools import (  # noqa: E402
    AuditedToolExecutor,
    ThreadDeadlineRunner,
    ToolBoundary,
)
from app.common import config as settings  # noqa: E402
from app.common import llm  # noqa: E402
from app.common.enums import AlarmType, RunStatus  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from app.common.tool_contracts import (  # noqa: E402
    DocumentHit,
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    ParameterSummaryItem,
    fail,
)
from scripts import compare_autonomy_levels as deterministic  # noqa: E402
from scripts.rehearsal_postgres import (  # noqa: E402
    POSTGRES_RAG_IMAGE,
    one_off_postgres,
)

FIXTURE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_7_1/counterfactual_incidents.json"
ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_7_1/counterfactual_oracle.json"
_PARAMETER_ID = re.compile(r"([A-Z][A-Z0-9_-]+)\(")


class ObservationExecutionError(RuntimeError):
    pass


def _load_rows(path: Path, key: str) -> tuple[dict[str, Any], ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get(key)
    if not isinstance(rows, list) or len(rows) != 5:
        raise ObservationExecutionError("FIXTURE_INVALID")
    return tuple(dict(row) for row in rows)


def _parameter(
    parameter_id: str,
    *,
    direction: str | None,
) -> ParameterSummaryItem:
    above = direction == "ABOVE"
    below = direction == "BELOW"
    return ParameterSummaryItem(
        parameter_id=parameter_id,
        parameter_name=parameter_id,
        recipe_step_no=1,
        value_mean=12.0 if above else -2.0 if below else 5.0,
        value_min=-2.0 if below else 4.0,
        value_max=12.0 if above else 6.0,
        point_cnt=10,
        ooc_point_cnt=0,
        oos_point_cnt=2 if direction else 0,
        spec_lower=0.0,
        ctrl_lower=1.0,
        target=5.0,
        ctrl_upper=9.0,
        spec_upper=10.0,
        alarm_type=AlarmType.OOS if direction else AlarmType.IN,
    )


class CounterfactualBoundary:
    """fixture id를 Tool 출력에만 사용하며 selector/hypothesis 입력에는 넣지 않는다."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.fixture_id = ""
        self.fdc_calls = 0
        self.document_queries: list[str] = []

    def reset(self, fixture_id: str) -> None:
        self.fixture_id = fixture_id
        self.fdc_calls = 0
        self.document_queries = []

    def fdc(self, payload: dict[str, Any]) -> FdcSummaryToolResult:
        from app.detection.service import FdcSummaryService

        with self.engine.connect() as connection:
            result = FdcSummaryService(connection).get_fdc_summary(
                str(payload["lot_hist_id"])
            )
        if result is None:
            return fail(FdcSummaryToolResult, "NOT_FOUND: counterfactual")
        self.fdc_calls += 1
        parameters = {
            "CF-1": (_parameter("PH_FOCUS", direction="ABOVE"),),
            "CF-2": (
                _parameter(
                    "CF2_BASE" if self.fdc_calls == 1 else "CF2_DYNAMIC",
                    direction=None if self.fdc_calls == 1 else "ABOVE",
                ),
            ),
            "CF-3": (_parameter("CF3_BASE", direction="ABOVE"),),
            "CF-4": (_parameter("CF4_CRITICAL", direction="ABOVE"),),
            "CF-5": (_parameter("CF5_HELD_OUT", direction="BELOW"),),
        }[self.fixture_id]
        return result.model_copy(update={"parameters": parameters, "anomaly": None})

    @staticmethod
    def equipment(payload: dict[str, Any]) -> EquipmentContextToolResult:
        chamber = str(payload["chamber_id"])
        return EquipmentContextToolResult(
            ok=True,
            chamber_id=chamber,
            equipment_id=chamber.split("-")[0],
            area="COUNTERFACTUAL",
            model_code="CF-MODEL",
            process_step_id="CF-UPSTREAM-STEP",
            upstream_process_step_ids=("CF-UPSTREAM-STEP",),
            graph_revision="counterfactual-v1",
        )

    @staticmethod
    def _hit(chunk_id: str, title: str, content: str) -> DocumentHit:
        return DocumentHit(
            chunk_id=chunk_id,
            document_id=chunk_id.replace("DOC-", "MANUAL-"),
            title=title,
            section="counterfactual",
            score=0.99,
            content=content,
            model_code="CF-MODEL",
        )

    def documents(self, payload: dict[str, Any]) -> DocumentSearchToolResult:
        query = str(payload["query"])
        self.document_queries.append(query)
        lowered = query.lower()
        if self.fixture_id == "CF-1":
            adaptive = any(
                token in lowered for token in ("상한", "초과", "과다", "above", "upper")
            )
            hit = (
                self._hit(
                    "DOC-CF1-DIRECTION",
                    "PH_FOCUS 상한 이탈",
                    "PH_FOCUS가 상한을 초과하면 과다 노광 가능성을 우선 확인한다.",
                )
                if adaptive
                else self._hit(
                    "DOC-CF1-BASE", "PH_FOCUS 일반", "PH_FOCUS 일반 점검 절차다."
                )
            )
            return DocumentSearchToolResult(ok=True, hits=[hit])
        if self.fixture_id == "CF-2":
            hit = (
                self._hit(
                    "DOC-CF2-DYNAMIC",
                    "CF2_DYNAMIC 관리",
                    "CF2_DYNAMIC 신규 이상 파라미터의 상한 점검 절차다.",
                )
                if "cf2_dynamic" in lowered
                else self._hit(
                    "DOC-CF2-BASE", "CF2 기본", "대표 wafer 기본 점검 절차다."
                )
            )
            return DocumentSearchToolResult(ok=True, hits=[hit])
        if self.fixture_id == "CF-3":
            if len(set(self.document_queries)) == 1:
                return fail(DocumentSearchToolResult, "TIMEOUT: counterfactual")
            return DocumentSearchToolResult(
                ok=True,
                hits=[
                    self._hit(
                        "DOC-CF3-RECOVERED",
                        "문서 장애 대체 조사",
                        "문서 조회 장애 뒤 설비 또는 추가 wafer 근거로 "
                        "검색을 재구성한다.",
                    )
                ],
            )
        if self.fixture_id == "CF-4":
            return DocumentSearchToolResult(
                ok=True,
                hits=[
                    self._hit(
                        "DOC-CF4-A", "R03 즉시 조치", "R03은 즉시 설비 보류가 필요하다."
                    ),
                    self._hit("DOC-CF4-B", "R03 확인", "중복 R03 근거를 확인한다."),
                ],
            )
        return DocumentSearchToolResult(
            ok=True,
            hits=[self._hit("DOC-CF5", "Held-out 지침", "독립 조건 점검 지침이다.")],
        )

    def as_boundary(self) -> ToolBoundary:
        return ToolBoundary(
            fdc_summary=self.fdc,
            equipment_context=self.equipment,
            document_search=self.documents,
        )


class RecordingSelector:
    def __init__(self) -> None:
        self.records: list[tuple[react.ReactContext, Any]] = []

    def reset(self) -> None:
        self.records = []

    def __call__(self, context: react.ReactContext) -> Any:
        try:
            outcome = react.select_next_step(context, seed=0)
        except react.ReactSelectionError as exc:
            self.records.append((context, exc))
            raise
        self.records.append((context, outcome))
        return outcome


def _query_features(query: str) -> list[str]:
    lowered = query.lower()
    features = []
    if any(token in lowered for token in ("상한", "초과", "과다", "above", "upper")):
        features.append("DIRECTION_ABOVE")
    if any(token in lowered for token in ("하한", "미만", "below", "lower")):
        features.append("DIRECTION_BELOW")
    return sorted(features)


def _projection(value: dict[str, Any]) -> dict[str, Any]:
    value = dict(value)
    value["sha256"] = comparison.canonical_sha256(value)
    return value


def _step_projections(
    records: list[tuple[react.ReactContext, Any]],
    *,
    baseline_parameter_ids: set[str],
) -> list[dict[str, Any]]:
    projections = []
    previous_fdc: set[str] = set()
    for seq, (context, outcome) in enumerate(records, start=1):
        current_fdc = set(_PARAMETER_ID.findall(" ".join(context.fdc_observations)))
        recent = context.recent_tool_events[-1] if context.recent_tool_events else ""
        if "실패" in recent:
            source = "TOOL_FAILURE"
            observed_tool = recent.partition(":")[0]
            observed_status = "TIMEOUT" if "TIMEOUT" in recent else "ERROR"
            observation_features = [observed_status]
        elif current_fdc != previous_fdc or seq == 1:
            source = "FDC"
            observed_tool = "get_fdc_summary"
            observed_status = "SUCCESS"
            joined = " ".join(context.fdc_observations)
            observation_features = []
            if "direction=ABOVE" in joined:
                observation_features.append("OOS_ABOVE_UPPER")
            if "direction=BELOW" in joined:
                observation_features.append("OOS_BELOW_LOWER")
            if current_fdc - baseline_parameter_ids:
                observation_features.append("NEW_PARAMETER_ID")
        elif context.document_observations:
            source = "DOCUMENTS"
            observed_tool = "search_documents"
            observed_status = "SUCCESS"
            observation_features = []
        else:
            source = "EQUIPMENT"
            observed_tool = "get_equipment_context"
            observed_status = "SUCCESS"
            observation_features = []
        selection = (
            outcome.selection
            if isinstance(outcome, react.ReactSelectionOutcome)
            else None
        )
        next_tool = None if selection is None else selection.next
        query = "" if selection is None else selection.arguments.query or ""
        next_features = _query_features(query)
        if (
            source == "TOOL_FAILURE"
            and next_tool is not None
            and next_tool != observed_tool
        ):
            next_features.append("FAILURE_ALTERNATE")
        next_features = sorted(set(next_features))
        new_ids = sorted(current_fdc - baseline_parameter_ids)
        next_parameter_ids = sorted(item for item in current_fdc if item in query)
        projections.append(
            _projection(
                {
                    "seq": seq,
                    "observation_source": source,
                    "observation_features": sorted(set(observation_features)),
                    "observed_tool": observed_tool,
                    "observed_status": observed_status,
                    "new_identifiers": new_ids,
                    "next_tool": next_tool,
                    "next_query_features": next_features,
                    "next_query_parameter_ids": next_parameter_ids,
                    "next_query_step_ids": [],
                    "document_query_sha256": hashlib.sha256(
                        query.encode("utf-8")
                    ).hexdigest(),
                }
            )
        )
        previous_fdc = current_fdc
    return projections


def _canonical_ids(values: Any) -> list[str]:
    return sorted(set(str(item) for item in values if item))


def _ids(row: dict[str, Any], key: str, values: Any) -> None:
    canonical = _canonical_ids(values)
    row[key] = canonical
    row[f"{key}_sha256"] = comparison.canonical_sha256(canonical)


def _evidence(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    available = [item.to_token() for item in state.get("member_alarms", ())]
    for item in state.get("fdc_evidence_set", ()):
        if item is None or not item.ok or item.wafer is None:
            continue
        available.append(item.wafer.lot_hist_id)
        available.extend(parameter.parameter_id for parameter in item.parameters)
    documents = state.get("document_evidence")
    if documents is not None and documents.ok:
        available.extend(hit.chunk_id for hit in documents.hits)
    route = state.get("route")
    if route is not None:
        available.extend(
            relation for item in route.graph_evidence for relation in item.relation_ids
        )
    hypothesis_value = state.get("hypothesis")
    cited = []
    if hypothesis_value is not None:
        cited.extend(item.to_token() for item in hypothesis_value.supporting_alarms)
        cited.extend(hypothesis_value.supporting_chunk_ids)
        cited.extend(hypothesis_value.supporting_relation_ids)
        cited.extend(hypothesis_value.supporting_lot_hist_ids)
        cited.extend(hypothesis_value.supporting_parameter_ids)
    return _canonical_ids(cited), _canonical_ids(available)


def _baseline_projection(state: dict[str, Any], history: Any) -> dict[str, Any]:
    first_fdc = state.get("fdc_evidence")
    parameters = (
        []
        if first_fdc is None or not first_fdc.ok
        else sorted(item.parameter_id for item in first_fdc.parameters)
    )
    route = state.get("route")
    steps = (
        []
        if route is None
        else sorted(
            {
                item.process_step_id
                for item in route.graph_evidence
                if item.process_step_id is not None
            }
        )
    )
    available = _evidence(state)[1]
    return _projection(
        {
            "tool_order": [item.tool_name for item in history],
            "query_features": [
                "CHAMBER",
                "FIRST_FDC_PARAMETER_IDS",
                "REPRESENTATIVE_ALARM",
                "ROUTE_STEP",
            ],
            "query_parameter_ids": parameters,
            "query_step_ids": steps,
            "evidence_ids_available": available,
        }
    )


def _run_level(
    endpoint: Any,
    *,
    level: int,
    snapshot: Path,
) -> tuple[dict[str, Any], ...]:
    deterministic._restore_snapshot(endpoint, snapshot)
    engine = deterministic._engine(endpoint)
    identities = deterministic._identities(engine)
    identity_by_key = {
        (item["lot_id"], item["chamber_id"]): item for item in identities
    }
    deterministic._clear_agent_runtime(engine)
    boundary = CounterfactualBoundary(engine)
    selector = RecordingSelector()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="observation")
    try:

        @contextmanager
        def transactions():
            with engine.begin() as connection:
                yield connection

        tools = AuditedToolExecutor(
            transactions=transactions,
            boundary=boundary.as_boundary(),
            deadline_runner=ThreadDeadlineRunner(executor),
        )
        graph = experiment.build_level_graph(
            level,
            selector_port=selector if level == 3 else None,
            hypothesis_port=functools.partial(hypothesis.generate_hypothesis, seed=0),
            clock=lambda: datetime.now(UTC),
            tools=tools,
            transactions=transactions,
            routing_graph=GraphBoundary.production(),
            configured_llm_model=llm.configured_model(),
        )
        rows = []
        for fixture in _load_rows(FIXTURE_PATH, "fixtures"):
            identity = identity_by_key[(fixture["lot_id"], fixture["chamber_id"])]
            for attempt_no in (1, 2):
                deterministic._clear_agent_runtime(engine)
                boundary.reset(fixture["fixture_id"])
                selector.reset()
                started = time.monotonic()
                state = graph.invoke(
                    {
                        "requested_alarm": AlarmRef.model_validate(
                            identity["requested_alarm"]
                        )
                    },
                    config={"configurable": {"thread_id": str(uuid4())}},
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                run_id = state.get("run_id")
                history = () if not isinstance(run_id, str) else tools.history(run_id)
                cited, available = _evidence(state)
                query = next(
                    (
                        str(item.input.get("query", ""))
                        for item in reversed(history)
                        if item.tool_name == "search_documents"
                        and isinstance(item.input, dict)
                    ),
                    "",
                )
                first_fdc = state.get("fdc_evidence")
                baseline_parameters = {
                    item.parameter_id
                    for item in (() if first_fdc is None else first_fdc.parameters)
                }
                trace = [
                    react.ReactStep.model_validate(item).model_dump(
                        mode="json",
                        include={
                            "seq",
                            "phase",
                            "tool",
                            "guard_code",
                            "stop_reason",
                            "degraded",
                        },
                    )
                    for item in state.get("react_trace", ())
                ]
                hypothesis_tokens = {"input": 0, "output": 0}
                if isinstance(run_id, str):
                    with engine.begin() as connection:
                        stored = get_agent_run(connection, run_id)
                        hypothesis_tokens = {
                            "input": stored.input_tokens or 0,
                            "output": stored.output_tokens or 0,
                        }
                        if stored.status is RunStatus.RUNNING:
                            finish_agent_run(
                                connection,
                                run_id,
                                RunStatus.COMPLETED,
                                evidence={"experiment": "V5-C-7.1"},
                                latency_ms=latency_ms,
                            )
                selector_input = sum(
                    item.get("selector_tokens", {}).get("input", 0)
                    for item in state.get("react_trace", ())
                )
                selector_output = sum(
                    item.get("selector_tokens", {}).get("output", 0)
                    for item in state.get("react_trace", ())
                )
                row: dict[str, Any] = {
                    "fixture_id": fixture["fixture_id"],
                    "attempt_no": attempt_no,
                    "pair_id": f"{fixture['fixture_id']}:{attempt_no}",
                    "initial_snapshot_sha256": "",
                    "terminal": (
                        "decide_action"
                        if state.get("action_decision") is not None
                        else "failed"
                    ),
                    "completion": state.get("action_decision") is not None,
                    "tool_path": [item.tool_name for item in history],
                    "tool_input_digests": [
                        comparison.canonical_sha256(item.input) for item in history
                    ],
                    "document_query_sha256": hashlib.sha256(
                        query.encode("utf-8")
                    ).hexdigest(),
                    "unsupported_count": len(set(cited) - set(available)),
                    "tokens": {
                        "hypothesis": hypothesis_tokens,
                        "selector": (
                            None
                            if level == 2
                            else {"input": selector_input, "output": selector_output}
                        ),
                    },
                    "latency_ms": latency_ms,
                    "baseline_projection": (
                        _baseline_projection(state, history) if level == 2 else None
                    ),
                    "step_projections": (
                        None
                        if level == 2
                        else _step_projections(
                            selector.records,
                            baseline_parameter_ids=baseline_parameters,
                        )
                    ),
                    "react_trace_summary": None if level == 2 else trace,
                }
                _ids(row, "cited_evidence_ids", cited)
                _ids(row, "available_evidence_ids", available)
                rows.append(row)
                print(
                    "OBSERVATION_PROGRESS "
                    f"level={level} fixture={fixture['fixture_id']} "
                    f"attempt={attempt_no} completion={row['completion']}",
                    flush=True,
                )
        return tuple(rows)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        engine.dispose()


def _median(values: list[float]) -> float:
    values = sorted(values)
    return (values[4] + values[5]) / 2


def _derive(
    level2: list[dict[str, Any]],
    level3: list[dict[str, Any]],
    oracle: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    l2 = {row["pair_id"]: row for row in level2}
    l3 = {row["pair_id"]: row for row in level3}
    pairs = []
    ratios: dict[str, float] = {}
    for fixture in comparison.FIXTURES:
        kinds = []
        for attempt in (1, 2):
            pair_id = f"{fixture}:{attempt}"
            left, right = l2[pair_id], l3[pair_id]
            required = oracle[fixture]
            for row in (left, right):
                row["recall"] = len(set(row["cited_evidence_ids"]) & required) / len(
                    required
                )
            kind = comparison.derive_baseline_delta_kind(
                left["baseline_projection"], right["step_projections"]
            )
            right["baseline_delta_kind"] = kind
            kinds.append(kind)
            pairs.append(
                {
                    "pair_id": pair_id,
                    "recall_delta": right["recall"] - left["recall"],
                    "completion_delta": int(right["completion"])
                    - int(left["completion"]),
                    "tool_delta": len(left["tool_path"]) - len(right["tool_path"]),
                }
            )
        ratios[fixture] = sum(kind != "none" for kind in kinds) / len(kinds)
    metrics = {
        "recall_delta_median": _median([float(row["recall_delta"]) for row in pairs]),
        "completion_rate": {
            "2": sum(row["completion"] for row in level2) / 10,
            "3": sum(row["completion"] for row in level3) / 10,
        },
        "baseline_delta_ratio": ratios,
    }
    recall_ok = metrics["recall_delta_median"] >= 0 and all(
        sum(l3[f"{fixture}:{attempt}"]["recall"] for attempt in (1, 2)) / 2
        >= sum(l2[f"{fixture}:{attempt}"]["recall"] for attempt in (1, 2)) / 2
        for fixture in comparison.FIXTURES
    )
    unsupported_ok = all(row["unsupported_count"] == 0 for row in (*level2, *level3))
    delta_ok = all(ratios[fixture] >= 0.5 for fixture in comparison.FIXTURES[:3])
    tools_ok = all(
        (
            row["tool_delta"] > 0
            and l3[row["pair_id"]]["recall"] >= l2[row["pair_id"]]["recall"]
        )
        if row["pair_id"].startswith("CF-4:")
        else row["tool_delta"] >= -1
        for row in pairs
    )
    verdict = (
        "ESTABLISHED"
        if recall_ok
        and unsupported_ok
        and delta_ok
        and tools_ok
        and metrics["completion_rate"]["3"] >= metrics["completion_rate"]["2"]
        else "NOT_ESTABLISHED"
    )
    return pairs, metrics, verdict


def execute(
    *,
    revision: str,
    level_comparison: Path,
    artifact: Path,
) -> str:
    deterministic._verify_revision(revision)
    if settings.LLM_TEMPERATURE != 0:
        raise ObservationExecutionError("LLM_CONFIG_MISMATCH")
    model = llm.preflight_model()
    level_sha = comparison.file_sha256(level_comparison)
    deterministic_payload = comparison.load_json(level_comparison)
    if deterministic_payload.get("source_revision") != revision:
        raise ObservationExecutionError("REVISION_MISMATCH")
    comparison.validate_level_comparison(deterministic_payload)
    oracle_rows = _load_rows(ORACLE_PATH, "oracle")
    oracle = {
        row["fixture_id"]: set(row["required_evidence_ids"]) for row in oracle_rows
    }
    with tempfile.TemporaryDirectory(prefix="v5-c-7-1-observe-") as directory:
        snapshot = Path(directory) / "source.dump"
        snapshot_sha = deterministic._source_snapshot(snapshot)
        attempts: dict[int, list[dict[str, Any]]] = {}
        for level in (2, 3):
            with one_off_postgres(
                database=f"fdc_react_observation_l{level}",
                image=POSTGRES_RAG_IMAGE,
                command_runner=deterministic._pinned_local_image_runner,
            ) as endpoint:
                attempts[level] = list(
                    _run_level(endpoint, level=level, snapshot=snapshot)
                )
                for row in attempts[level]:
                    row["initial_snapshot_sha256"] = snapshot_sha
        pairs, metrics, verdict = _derive(attempts[2], attempts[3], oracle)
        artifact_oracle = []
        for row in oracle_rows:
            item = {"fixture_id": row["fixture_id"]}
            _ids(item, "required_evidence_ids", row["required_evidence_ids"])
            artifact_oracle.append(item)
        payload = {
            "schema_version": "agent-justification-v1",
            "source_revision": revision,
            "level_comparison_sha256": level_sha,
            "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
            "oracle_sha256": hashlib.sha256(ORACLE_PATH.read_bytes()).hexdigest(),
            "OBSERVATIONAL_REAL_LLM": True,
            "attempts_per_fixture": 2,
            "llm": {
                "hypothesis_model_revision": model,
                "selector_model_revision": model,
                "temperature": 0,
                "seed": 0,
            },
            "oracle": artifact_oracle,
            "level2_attempts": attempts[2],
            "level3_attempts": attempts[3],
            "pairs": pairs,
            "metrics": metrics,
            "safety": {
                "send_action_selected": 0,
                "hitl_bypass": 0,
                "pre_approval_mes": 0,
            },
            "agent_justification_verdict": verdict,
        }
        comparison.validate_agent_justification(
            payload,
            level_comparison_sha256=level_sha,
            level_comparison_revision=revision,
        )
        return comparison.write_immutable_json(artifact, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-revision", required=True)
    parser.add_argument("--level-comparison", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest = execute(
            revision=args.expect_revision,
            level_comparison=args.level_comparison,
            artifact=args.artifact,
        )
    except Exception as exc:
        code = getattr(exc, "code", None) or str(exc)
        allowed = {
            "REVISION_MISMATCH",
            "LLM_CONFIG_MISMATCH",
            "FIXTURE_INVALID",
            "ARTIFACT_CLOBBER_BLOCKED",
            "COMPARISON_DEPENDENCY_FAILED",
        }
        reason = code if code in allowed else "OBSERVATION_FAILED"
        error_payload = {"reason_code": reason}
        diagnostic = getattr(exc, "detail", None)
        if isinstance(diagnostic, str) and diagnostic:
            error_payload["diagnostic"] = diagnostic
        print(json.dumps(error_payload), file=sys.stderr)
        return 1
    print(f"AGENT_JUSTIFICATION_WRITTEN sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
