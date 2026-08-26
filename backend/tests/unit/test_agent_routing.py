"""`V5-C-1.2` WAFER routing 결합 단위 회귀.

graph 근거는 **B의 실제 Pydantic DTO**로 만든다. 평행 계약을 새로 정의하면 B가 DTO를
바꿔도 이 회귀가 green으로 남는다. 실제 View·member resolve·WAFER 범위는
`test_agent_routing_container.py`가 소유한다.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import repository as repo  # noqa: E402
from app.agent import routing as rt  # noqa: E402
from app.agent import routing_repository as rt_repo  # noqa: E402
from app.agent.incident import ResolvedIncident  # noqa: E402
from app.common.enums import AlarmSource  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from app.common.tool_contracts import (  # noqa: E402
    REASON_PREFIXES,
    EquipmentContextToolResult,
)
from app.knowledge.schemas import (  # noqa: E402
    ChamberRelationResponse,
    GraphNode,
    GraphRelationship,
)

TRACE = AlarmSource.TRACE
SUMMARY = AlarmSource.SUMMARY

LOT = "LOT001"
PHOTO_CHAMBER = "EQP01-PM1"
ETCH_CHAMBER = "EQP04-PM2"
PHOTO_STEP = "CT-PHOTO"
ETCH_STEP = "CT-ETCH"
REVISION = "a" * 64

T0 = datetime(2026, 8, 1, 10, 0, 0)
T1 = datetime(2026, 8, 1, 11, 0, 0)
T2 = datetime(2026, 8, 1, 12, 0, 0)


#: B prefix → C code. **이 파일이 기댓값을 소유한다.**
#:
#: 생산 mapping을 읽어 비교하면 tautology가 된다 — `GRAPH_SHAPE_ERROR:`를
#: `GRAPH_DEPENDENCY_ERROR`로 바꾸는 변이가 red가 되지 않는다.
EXPECTED_GRAPH_CODES = {
    "NOT_FOUND:": "GRAPH_CONTEXT_NOT_FOUND",
    "TIMEOUT:": "GRAPH_CONTEXT_TIMEOUT",
    "MODEL_NOT_READY:": "GRAPH_MODEL_NOT_READY",
    "LLM_NOT_READY:": "GRAPH_LLM_NOT_READY",
    "GRAPH_SHAPE_ERROR:": "GRAPH_SHAPE_ERROR",
    "DEPENDENCY_ERROR:": "GRAPH_DEPENDENCY_ERROR",
    "POLICY_REJECTED:": "GRAPH_POLICY_REJECTED",
    "IDEMPOTENCY_CONFLICT:": "GRAPH_IDEMPOTENCY_CONFLICT",
}


def _ref(source: AlarmSource, alarm_id: str) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


def _incident(*members: AlarmRef) -> ResolvedIncident:
    chosen = members or (_ref(TRACE, "TA-01"),)
    return ResolvedIncident(
        lot_id=LOT,
        chamber_id=PHOTO_CHAMBER,
        requested_alarm=chosen[0],
        representative_alarm=chosen[0],
        member_alarms=tuple(chosen),
    )


def _step(
    lot_hist_id: str,
    *,
    wafer_id: str = "LOT001W001",
    step_id: str = PHOTO_STEP,
    equipment_id: str = "EQP01",
    chamber_id: str = PHOTO_CHAMBER,
    track_in_at: datetime = T0,
) -> rt_repo.RouteStep:
    return rt_repo.RouteStep(
        lot_hist_id=lot_hist_id,
        lot_id=LOT,
        wafer_id=wafer_id,
        wafer_no=1,
        step_id=step_id,
        area_id="etch",
        equipment_id=equipment_id,
        chamber_id=chamber_id,
        recipe_id="RECIPE01",
        track_in_at=track_in_at,
        track_out_at=None,
    )


def _context(
    chamber_id: str,
    *,
    equipment_id: str,
    step_id: str,
    upstream: tuple[str, ...] = (),
    downstream: tuple[str, ...] = (),
    revision: str = REVISION,
) -> EquipmentContextToolResult:
    return EquipmentContextToolResult(
        ok=True,
        chamber_id=chamber_id,
        equipment_id=equipment_id,
        area="etch",
        model_code="PH-9000",
        process_step_id=step_id,
        upstream_process_step_ids=list(upstream),
        downstream_process_step_ids=list(downstream),
        graph_revision=revision,
    )


#: B의 Cypher가 만드는 node id 표기. **fixture가 재현하고 생산 코드는 가정하지 않는다.**
#:
#: B `repository.py`의 projection이 `labels(node)[0] + ':' + business_id`로 만든다.
#: 이 파일이 그 형식을 아는 것은 괜찮다 — B가 실제로 주는 payload를 흉내 내는 것이
#: fixture의 일이다. 문제가 되는 것은 **생산 코드**가 같은 규칙을 복제해 드는 경우다.
def _node_id(label: str, business_id: str) -> str:
    return f"{label}:{business_id}"


def _relation(
    rel_id: str,
    kind: str,
    source: tuple[str, str],
    target: tuple[str, str],
) -> GraphRelationship:
    return GraphRelationship(
        id=rel_id,
        type=kind,
        source=_node_id(*source),
        target=_node_id(*target),
    )


def _graph_node(node_id: str) -> GraphNode:
    label, business_id = node_id.split(":", 1)
    return GraphNode(
        id=node_id,
        label=label,
        business_id=business_id,
        display_name=business_id,
        properties={},
    )


def _projection(
    chamber_id: str,
    *relations: GraphRelationship,
    revision: str = REVISION,
    nodes: list[GraphNode] | None = None,
) -> ChamberRelationResponse:
    """**node를 싣는다.** 생산 코드가 그 값으로 관계를 찾기 때문이다.

    초판 fixture는 `nodes=[]`였고, 그래서 projection의 node 목록이 한 번도 소비되지
    않았다. 생산 코드가 node id를 형식으로 재구성해도 회귀가 green이었다.
    """

    if nodes is None:
        endpoints = {r.source for r in relations} | {r.target for r in relations}
        nodes = [_graph_node(node_id) for node_id in sorted(endpoints)]
    return ChamberRelationResponse(
        root_node_id=_node_id("Chamber", chamber_id),
        nodes=nodes,
        relationships=list(relations),
        graph_revision=revision,
    )


def _photo_relations() -> tuple[GraphRelationship, ...]:
    return (
        _relation(
            "REL-part-photo",
            "PART_OF",
            ("Chamber", "EQP01-PM1"),
            ("Equipment", "EQP01"),
        ),
        _relation(
            "REL-perf-photo",
            "PERFORMS",
            ("Equipment", "EQP01"),
            ("ProcessStep", "CT-PHOTO"),
        ),
        _relation(
            "REL-next",
            "NEXT_STEP",
            ("ProcessStep", "CT-PHOTO"),
            ("ProcessStep", "CT-ETCH"),
        ),
    )


def _etch_relations() -> tuple[GraphRelationship, ...]:
    return (
        _relation(
            "REL-part-etch", "PART_OF", ("Chamber", "EQP04-PM2"), ("Equipment", "EQP04")
        ),
        _relation(
            "REL-perf-etch",
            "PERFORMS",
            ("Equipment", "EQP04"),
            ("ProcessStep", "CT-ETCH"),
        ),
        _relation(
            "REL-next",
            "NEXT_STEP",
            ("ProcessStep", "CT-PHOTO"),
            ("ProcessStep", "CT-ETCH"),
        ),
    )


class _Graph:
    """B service 두 경계를 흉내 낸다. **호출 횟수를 센다.**"""

    def __init__(
        self,
        contexts: dict[str, Any],
        projections: dict[str, Any],
    ) -> None:
        self.contexts = contexts
        self.projections = projections
        self.context_calls: list[str] = []
        self.projection_calls: list[str] = []

    def equipment_context(self, chamber_id: str) -> Any:
        self.context_calls.append(chamber_id)
        value = self.contexts.get(chamber_id)
        return value() if callable(value) else value

    def chamber_relations(self, chamber_id: str) -> Any:
        self.projection_calls.append(chamber_id)
        value = self.projections.get(chamber_id)
        return value() if callable(value) else value


def _two_step_graph(**overrides: Any) -> _Graph:
    contexts = {
        PHOTO_CHAMBER: _context(
            PHOTO_CHAMBER,
            equipment_id="EQP01",
            step_id=PHOTO_STEP,
            downstream=(ETCH_STEP,),
        ),
        ETCH_CHAMBER: _context(
            ETCH_CHAMBER,
            equipment_id="EQP04",
            step_id=ETCH_STEP,
            upstream=(PHOTO_STEP,),
        ),
    }
    projections = {
        PHOTO_CHAMBER: _projection(PHOTO_CHAMBER, *_photo_relations()),
        ETCH_CHAMBER: _projection(ETCH_CHAMBER, *_etch_relations()),
    }
    contexts.update(overrides.get("contexts", {}))
    projections.update(overrides.get("projections", {}))
    return _Graph(contexts, projections)


def _snapshot(
    wafer_of_member: dict[Any, str],
    steps: tuple[Any, ...],
    incident: ResolvedIncident | None = None,
) -> rt_repo.RouteSnapshot:
    """**incident와 일치하는** snapshot을 만든다. 어긋난 조합은 회귀가 따로 만든다."""

    target = incident or _incident()
    return rt_repo.RouteSnapshot(
        lot_id=target.lot_id,
        chamber_id=target.chamber_id,
        member_keys=tuple((a.source, a.alarm_id) for a in target.member_alarms),
        wafer_of_member=wafer_of_member,
        steps=steps,
    )


def _boundary(graph: _Graph) -> rt.GraphBoundary:
    return rt.GraphBoundary(
        equipment_context=graph.equipment_context,
        chamber_relations=graph.chamber_relations,
    )


def _resolve(
    snapshot: rt_repo.RouteSnapshot,
    graph: _Graph,
    incident: ResolvedIncident | None = None,
) -> rt.ResolvedIncidentRoute:
    """**`Connection`이 없다.** graph 단계는 DB scope 밖에서 돈다."""

    return rt.combine_route(
        rt.IncidentRoute(incident=incident or _incident(), snapshot=snapshot),
        graph=_boundary(graph),
    )


def _two_step_snapshot() -> rt_repo.RouteSnapshot:
    return _snapshot(
        {(TRACE, "TA-01"): "LOT001W001"},
        (
            _step("LH-1", track_in_at=T0),
            _step(
                "LH-2",
                step_id=ETCH_STEP,
                equipment_id="EQP04",
                chamber_id=ETCH_CHAMBER,
                track_in_at=T1,
            ),
        ),
    )


# --- 일치 경로 --------------------------------------------------------------


class TestAConsistentRoute:
    def test_two_steps_match_in_both_directions(self) -> None:
        result = _resolve(_two_step_snapshot(), _two_step_graph())
        assert result.route_consistency is True
        assert result.mismatches == ()
        assert [s.lot_hist_id for s in result.wafer_routes[0].steps] == ["LH-1", "LH-2"]

    def test_real_relation_ids_are_preserved(self) -> None:
        """`REL-*`를 만들지 않고 projection이 준 값을 그대로 남긴다."""

        result = _resolve(_two_step_snapshot(), _two_step_graph())
        by_chamber = {e.chamber_id: e.relation_ids for e in result.graph_evidence}
        assert by_chamber[PHOTO_CHAMBER] == (
            "REL-part-photo",
            "REL-perf-photo",
            "REL-next",
        )
        # NEXT_STEP 소유는 from chamber 한 곳이다 — etch에 중복 기록되지 않는다.
        assert "REL-next" not in by_chamber[ETCH_CHAMBER]

    def test_each_chamber_is_read_once(self) -> None:
        """요청 로컬 cache. member·step이 늘어도 chamber당 1회다."""

        graph = _two_step_graph()
        incident = _incident(_ref(TRACE, "TA-01"), _ref(SUMMARY, "SA-01"))
        snapshot = _snapshot(
            {
                (TRACE, "TA-01"): "LOT001W001",
                (SUMMARY, "SA-01"): "LOT001W001",
            },
            (
                _step("LH-1", track_in_at=T0),
                _step("LH-1b", track_in_at=T1),
                _step(
                    "LH-2",
                    step_id=ETCH_STEP,
                    equipment_id="EQP04",
                    chamber_id=ETCH_CHAMBER,
                    track_in_at=T2,
                ),
            ),
            incident,
        )
        _resolve(snapshot, graph, incident)
        assert graph.context_calls == [PHOTO_CHAMBER, ETCH_CHAMBER]
        assert graph.projection_calls == [PHOTO_CHAMBER, ETCH_CHAMBER]

    def test_time_orders_steps_even_when_ids_disagree(self) -> None:
        """**`lot_hist_id` 순서와 시간 순서를 어긋나게 둔다.**

        둘이 같은 fixture만 있으면 정렬 key에서 `track_in_at`을 빼도 통과한다.
        """

        snapshot = _snapshot(
            {(TRACE, "TA-01"): "LOT001W001"},
            (
                _step("LH-A", track_in_at=T1),
                _step(
                    "LH-B",
                    step_id=ETCH_STEP,
                    equipment_id="EQP04",
                    chamber_id=ETCH_CHAMBER,
                    track_in_at=T0,
                ),
            ),
        )
        graph = _two_step_graph(
            contexts={
                ETCH_CHAMBER: _context(
                    ETCH_CHAMBER,
                    equipment_id="EQP04",
                    step_id=ETCH_STEP,
                    downstream=(PHOTO_STEP,),
                ),
                PHOTO_CHAMBER: _context(
                    PHOTO_CHAMBER,
                    equipment_id="EQP01",
                    step_id=PHOTO_STEP,
                    upstream=(ETCH_STEP,),
                ),
            }
        )
        result = _resolve(snapshot, graph)
        assert [s.lot_hist_id for s in result.wafer_routes[0].steps] == ["LH-B", "LH-A"]

    def test_a_single_step_route_runs_no_adjacency_check(self) -> None:
        """연속 쌍이 없으면 인접 검사가 **0회**다. per-step만으로 true가 된다."""

        snapshot = _snapshot(
            {(TRACE, "TA-01"): "LOT001W001"},
            (_step("LH-1"),),
        )
        graph = _two_step_graph()
        result = _resolve(snapshot, graph)
        assert result.route_consistency is True
        # NEXT_STEP은 인접 검사에서만 수집된다.
        photo = next(e for e in result.graph_evidence if e.chamber_id == PHOTO_CHAMBER)
        assert "REL-next" not in photo.relation_ids

    def test_a_three_step_route_checks_every_consecutive_pair(self) -> None:
        """**첫 쌍만 보고 끝나지 않는다.**

        실제 graph는 ProcessStep 2개·NEXT_STEP 1개라 3-step 일치 route를 실데이터로
        만들 수 없다. 여기서 확인할 것은 데이터가 아니라 loop 동작이다.
        """

        third = "CT-CLEAN"
        clean_chamber = "EQP05-PM1"
        graph = _two_step_graph(
            contexts={
                ETCH_CHAMBER: _context(
                    ETCH_CHAMBER,
                    equipment_id="EQP04",
                    step_id=ETCH_STEP,
                    upstream=(PHOTO_STEP,),
                    downstream=(third,),
                ),
                clean_chamber: _context(
                    clean_chamber,
                    equipment_id="EQP05",
                    step_id=third,
                    upstream=(ETCH_STEP,),
                ),
            },
            projections={
                ETCH_CHAMBER: _projection(
                    ETCH_CHAMBER,
                    *_etch_relations(),
                    _relation(
                        "REL-next2",
                        "NEXT_STEP",
                        ("ProcessStep", ETCH_STEP),
                        ("ProcessStep", third),
                    ),
                ),
                clean_chamber: _projection(
                    clean_chamber,
                    _relation(
                        "REL-part-clean",
                        "PART_OF",
                        ("Chamber", clean_chamber),
                        ("Equipment", "EQP05"),
                    ),
                    _relation(
                        "REL-perf-clean",
                        "PERFORMS",
                        ("Equipment", "EQP05"),
                        ("ProcessStep", third),
                    ),
                ),
            },
        )
        snapshot = _snapshot(
            {(TRACE, "TA-01"): "LOT001W001"},
            (
                _step("LH-1", track_in_at=T0),
                _step(
                    "LH-2",
                    step_id=ETCH_STEP,
                    equipment_id="EQP04",
                    chamber_id=ETCH_CHAMBER,
                    track_in_at=T1,
                ),
                _step(
                    "LH-3",
                    step_id=third,
                    equipment_id="EQP05",
                    chamber_id=clean_chamber,
                    track_in_at=T2,
                ),
            ),
        )
        result = _resolve(snapshot, graph)
        assert result.route_consistency is True
        etch = next(e for e in result.graph_evidence if e.chamber_id == ETCH_CHAMBER)
        assert "REL-next2" in etch.relation_ids


# --- 불일치는 결과로 보존한다 -----------------------------------------------


class TestMismatchesArePreservedNotRaised:
    def test_a_reverse_only_graph_fails_both_directions(self) -> None:
        """**한 집합으로 합치면 역방향도 통과한다.** 그래서 방향별로 본다."""

        graph = _two_step_graph(
            contexts={
                PHOTO_CHAMBER: _context(
                    PHOTO_CHAMBER,
                    equipment_id="EQP01",
                    step_id=PHOTO_STEP,
                    upstream=(ETCH_STEP,),
                ),
                ETCH_CHAMBER: _context(
                    ETCH_CHAMBER,
                    equipment_id="EQP04",
                    step_id=ETCH_STEP,
                    downstream=(PHOTO_STEP,),
                ),
            },
            projections={
                PHOTO_CHAMBER: _projection(
                    PHOTO_CHAMBER,
                    *_photo_relations()[:2],
                    _relation(
                        "REL-rev",
                        "NEXT_STEP",
                        ("ProcessStep", ETCH_STEP),
                        ("ProcessStep", PHOTO_STEP),
                    ),
                ),
            },
        )
        result = _resolve(_two_step_snapshot(), graph)
        codes = {m.code for m in result.mismatches}
        assert codes == {
            rt.DOWNSTREAM_MISSING,
            rt.UPSTREAM_MISSING,
            rt.NEXT_STEP_MISSING,
        }
        assert result.route_consistency is False
        # PostgreSQL step 배열은 그대로 유지된다.
        assert [s.step_id for s in result.wafer_routes[0].steps] == [
            PHOTO_STEP,
            ETCH_STEP,
        ]

    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("equipment_id", "EQP02", "GRAPH_EQUIPMENT_MISMATCH"),
            ("step_id", "CT-ETCH", "GRAPH_PROCESS_STEP_MISMATCH"),
            ("chamber_id", "EQP09-PM9", "GRAPH_CHAMBER_MISMATCH"),
        ],
    )
    def test_graph_drift_keeps_both_ids(
        self, field: str, value: str, code: str
    ) -> None:
        base = {
            "equipment_id": "EQP01",
            "step_id": PHOTO_STEP,
            "chamber_id": PHOTO_CHAMBER,
        }
        base[field] = value
        graph = _two_step_graph(
            contexts={
                PHOTO_CHAMBER: _context(
                    base["chamber_id"],
                    equipment_id=base["equipment_id"],
                    step_id=base["step_id"],
                    downstream=(ETCH_STEP,),
                )
            }
        )
        result = _resolve(_two_step_snapshot(), graph)
        found = [m for m in result.mismatches if m.code == code]
        assert found, [m.code for m in result.mismatches]
        assert found[0].graph_ids == (value,)
        # **PostgreSQL 값이 graph 값으로 덮이지 않는다.** truthy 확인만 하면
        # 양쪽에 같은 값을 넣는 변이가 통과한다.
        expected_pg = {
            "equipment_id": "EQP01",
            "step_id": PHOTO_STEP,
            "chamber_id": PHOTO_CHAMBER,
        }[field]
        assert found[0].postgres_ids == (expected_pg,)
        assert found[0].postgres_ids != found[0].graph_ids

    def test_a_missing_relation_is_a_mismatch_not_an_error(self) -> None:
        graph = _two_step_graph(
            projections={PHOTO_CHAMBER: _projection(PHOTO_CHAMBER)},
        )
        result = _resolve(_two_step_snapshot(), graph)
        codes = {m.code for m in result.mismatches}
        assert rt.PART_OF_MISSING in codes
        assert rt.PERFORMS_MISSING in codes
        assert result.wafer_routes  # route를 버리지 않는다

    def test_revision_drift_keeps_both_revisions(self) -> None:
        graph = _two_step_graph(
            projections={
                PHOTO_CHAMBER: _projection(
                    PHOTO_CHAMBER, *_photo_relations(), revision="b" * 64
                )
            },
        )
        result = _resolve(_two_step_snapshot(), graph)
        found = [m for m in result.mismatches if m.code == rt.REVISION_MISMATCH]
        assert found
        assert found[0].graph_ids == ("a" * 64, "b" * 64)

    def test_mismatch_order_is_deterministic_and_deduplicated(self) -> None:
        graph = _two_step_graph(
            projections={
                PHOTO_CHAMBER: _projection(PHOTO_CHAMBER),
                ETCH_CHAMBER: _projection(ETCH_CHAMBER),
            },
        )
        first = _resolve(_two_step_snapshot(), graph)
        second = _resolve(
            _two_step_snapshot(),
            _two_step_graph(
                projections={
                    PHOTO_CHAMBER: _projection(PHOTO_CHAMBER),
                    ETCH_CHAMBER: _projection(ETCH_CHAMBER),
                },
            ),
        )
        assert first.mismatches == second.mismatches
        assert len(set(first.mismatches)) == len(first.mismatches)
        keys = [
            (m.wafer_id, m.from_lot_hist_id, m.to_lot_hist_id or "", m.code)
            for m in first.mismatches
        ]
        assert keys == sorted(keys)


# --- 의존성 실패는 결과가 아니다 --------------------------------------------


class TestDependencyFailureIsNotAFalseResult:
    def test_a_missing_context_is_a_failure(self) -> None:
        graph = _two_step_graph(contexts={PHOTO_CHAMBER: None})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == "GRAPH_CONTEXT_NOT_FOUND"

    def test_a_missing_projection_is_a_failure(self) -> None:
        graph = _two_step_graph(projections={PHOTO_CHAMBER: None})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == "GRAPH_CONTEXT_NOT_FOUND"

    def test_a_timeout_is_a_failure(self) -> None:
        def _boom() -> Any:
            raise TimeoutError("neo4j://user:pw@host:7687 timed out")

        graph = _two_step_graph(contexts={PHOTO_CHAMBER: _boom})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == "GRAPH_CONTEXT_TIMEOUT"
        assert "neo4j" not in str(exc.value)
        assert "pw" not in str(exc.value)

    def test_a_shape_error_is_a_failure(self) -> None:
        def _boom() -> Any:
            ChamberRelationResponse.model_validate({"nodes": []})

        graph = _two_step_graph(projections={PHOTO_CHAMBER: _boom})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == "GRAPH_SHAPE_ERROR"

    def test_an_unknown_exception_is_sanitized(self) -> None:
        def _boom() -> Any:
            raise RuntimeError("MATCH (c:Chamber) RETURN c")

        graph = _two_step_graph(contexts={PHOTO_CHAMBER: _boom})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == "GRAPH_DEPENDENCY_ERROR"
        assert "MATCH" not in str(exc.value)

    @pytest.mark.parametrize(
        ("prefix", "expected"), sorted(EXPECTED_GRAPH_CODES.items())
    )
    def test_every_common_prefix_keeps_its_own_code(
        self, prefix: str, expected: str
    ) -> None:
        """**8종을 구분해 옮긴다.** shape를 dependency로 뭉개지 않는다.

        기댓값을 `rt.GRAPH_REASON_CODES`에서 읽으면 mapping을 바꿔도 기대가 함께 바뀌어
        아무것도 증명하지 못한다. 그래서 이 파일이 표를 직접 들고 있는다.
        """

        failure = EquipmentContextToolResult(ok=False, reason=f"{prefix} detail")
        graph = _two_step_graph(contexts={PHOTO_CHAMBER: failure})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == expected

    def test_the_mapping_matches_the_shared_contract_exactly(self) -> None:
        assert rt.GRAPH_REASON_CODES == EXPECTED_GRAPH_CODES
        assert set(EXPECTED_GRAPH_CODES) == set(REASON_PREFIXES)

    def test_a_full_projection_has_no_failure_dto(self) -> None:
        """`ChamberRelationResponse`에는 `ok`·`reason` 필드가 없다 — 실패 DTO가 없다."""

        fields = set(ChamberRelationResponse.model_fields)
        assert "ok" not in fields
        assert "reason" not in fields


# --- Repository mapping -----------------------------------------------------


def _row(kind: str, **overrides: Any) -> Any:
    base = {
        "row_kind": kind,
        "missing_count": 0,
        "unresolved_count": 0,
        "drift_count": 0,
        "wafer_missing_count": 0,
        "duplicate_count": 0,
        "member_source": "TRACE" if kind == "member" else None,
        "member_alarm_id": "TA-01" if kind == "member" else None,
        "wafer_id": "LOT001W001",
        "lot_hist_id": "LH-1" if kind == "step" else None,
        "lot_id": LOT if kind == "step" else None,
        "wafer_no": 1 if kind == "step" else None,
        "step_id": PHOTO_STEP if kind == "step" else None,
        "area_id": "etch" if kind == "step" else None,
        "equipment_id": "EQP01" if kind == "step" else None,
        "chamber_id": PHOTO_CHAMBER if kind == "step" else None,
        "recipe_id": "RECIPE01" if kind == "step" else None,
        "track_in_at": T0 if kind == "step" else None,
        "track_out_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Connection:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.params: dict[str, Any] | None = None

    def execute(self, statement: Any, params: Any = None) -> Any:
        self.params = params
        return SimpleNamespace(all=lambda: self._rows)


def _fetch(rows: list[Any], members: tuple[AlarmRef, ...] | None = None) -> Any:
    return rt_repo.fetch_route_snapshot(
        _Connection(rows),
        lot_id=LOT,
        chamber_id=PHOTO_CHAMBER,
        member_alarms=members or (_ref(TRACE, "TA-01"),),
    )


class TestRouteSnapshotMappingIsFailClosed:
    def test_the_identity_pairs_are_bound_as_parallel_arrays(self) -> None:
        """구분자로 합치지 않는다 — ID 본문에 그 문자가 들어올 수 있다."""

        connection = _Connection([_row("member"), _row("step")])
        rt_repo.fetch_route_snapshot(
            connection,
            lot_id=LOT,
            chamber_id=PHOTO_CHAMBER,
            member_alarms=(_ref(TRACE, "TA-01"), _ref(SUMMARY, "SA-01")),
        )
        assert connection.params == {
            "sources": ["TRACE", "SUMMARY"],
            "alarm_ids": ["TA-01", "SA-01"],
            "lot_id": LOT,
            "chamber_id": PHOTO_CHAMBER,
        }

    @pytest.mark.parametrize(
        ("field", "code", "error"),
        [
            ("missing_count", "ROUTE_MEMBER_NOT_FOUND", repo.RepositoryNotFound),
            ("duplicate_count", "ROUTE_MEMBER_DUPLICATE", repo.RepositoryContractError),
            (
                "unresolved_count",
                "ROUTE_MEMBER_OWNER_UNRESOLVED",
                repo.RepositoryContractError,
            ),
            ("drift_count", "ROUTE_INCIDENT_MISMATCH", repo.RepositoryContractError),
            (
                "wafer_missing_count",
                "ROUTE_WAFER_ID_MISSING",
                repo.RepositoryContractError,
            ),
        ],
    )
    def test_each_status_count_maps_to_its_code(
        self, field: str, code: str, error: type[Exception]
    ) -> None:
        rows = [_row("member", **{field: 1}), _row("step", **{field: 1})]
        with pytest.raises(error) as exc:
            _fetch(rows)
        assert exc.value.code == code

    def test_no_route_step_is_a_contract_violation(self) -> None:
        """owner가 resolve됐다면 그 행 자체가 route의 최소 1행이다."""

        with pytest.raises(repo.RepositoryContractError) as exc:
            _fetch([_row("member")])
        assert exc.value.code == "WAFER_ROUTE_INCOMPLETE"

    @pytest.mark.parametrize(
        "field", ["lot_hist_id", "wafer_id", "step_id", "equipment_id", "track_in_at"]
    )
    def test_a_null_required_column_is_not_corrected(self, field: str) -> None:
        rows = [_row("member"), _row("step", **{field: None})]
        with pytest.raises(repo.RepositoryContractError) as exc:
            _fetch(rows)
        assert exc.value.code == "WAFER_ROUTE_INCOMPLETE"

    def test_empty_members_never_reach_sql(self) -> None:
        connection = _Connection([])
        with pytest.raises(repo.RepositoryContractError):
            rt_repo.fetch_route_snapshot(
                connection, lot_id=LOT, chamber_id=PHOTO_CHAMBER, member_alarms=()
            )
        assert connection.params is None

    def test_the_mapping_order_is_fixed(self) -> None:
        import inspect

        body = ast.unparse(
            ast.parse(inspect.getsource(rt_repo.fetch_route_snapshot).lstrip())
        )
        order = [
            body.index("_MEMBER_MISSING"),
            body.index("_MEMBER_DUPLICATE"),
            body.index("_OWNER_UNRESOLVED"),
            body.index("_INCIDENT_MISMATCH"),
            body.index("_WAFER_ID_MISSING"),
        ]
        assert order == sorted(order), order


# --- 경계·비누수 -----------------------------------------------------------


def _code_only(module_path: Path) -> str:
    """**docstring·주석을 뺀 본문 코드만** 낸다.

    문자열로 금지어를 찾으면 "`SELECT *`를 쓰면 `fault_code`가 딸려 온다"처럼 **금지를
    설명하는 문장**에 걸린다. 주석은 AST에 없고 docstring은 여기서 뺀다. SQL 상수는
    docstring이 아니므로 그대로 남아 검사 대상이 된다.
    """

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree)


class TestTheModulesStayInsideTheirBoundary:
    def test_no_write_sql_and_no_connection_ownership(self) -> None:
        body = _code_only(Path(rt_repo.__file__))
        upper = body.upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "SELECT *"):
            assert forbidden not in upper, forbidden
        for forbidden in ("create_engine", ".commit(", ".rollback(", ".begin("):
            assert forbidden not in body, forbidden

    def test_no_public_http_or_router_call(self) -> None:
        """public `/relations/chambers/*`를 부르지 않고 내부 service만 주입받는다."""

        body = _code_only(Path(rt.__file__)) + _code_only(Path(rt_repo.__file__))
        for forbidden in (
            "httpx",
            "requests",
            "TestClient",
            "/relations/",
            "app.knowledge.router",
            "APIRouter",
        ):
            assert forbidden not in body, forbidden

    def test_no_label_or_reference_names_leak(self) -> None:
        body = _code_only(Path(rt.__file__)) + _code_only(Path(rt_repo.__file__))
        for forbidden in (
            "fault_code",
            "ground_truth",
            "label_source",
            "action_history",
            "anomaly_score",
            "alarm_result",
            "password",
            "postgresql",
            "bolt://",
        ):
            assert forbidden not in body, forbidden

    def test_relation_ids_are_never_synthesized(self) -> None:
        """`REL-*`를 만들지 않는다 — projection 값을 읽기만 한다."""

        body = _code_only(Path(rt.__file__))
        for forbidden in ("REL-", "sha256", "hashlib", "uuid"):
            assert forbidden not in body, forbidden

    def test_route_consistency_is_one_incident_level_flag(self) -> None:
        """WAFER별 결과는 `mismatches`에서 파생한다. 같은 사실을 두 곳에 두지 않는다."""

        assert "route_consistency" in rt.ResolvedIncidentRoute.__dataclass_fields__
        assert "route_consistency" not in rt.WaferRoute.__dataclass_fields__
        result = _resolve(
            _two_step_snapshot(),
            _two_step_graph(projections={PHOTO_CHAMBER: _projection(PHOTO_CHAMBER)}),
        )
        by_wafer = {m.wafer_id for m in result.mismatches}
        assert by_wafer == {"LOT001W001"}


# ===========================================================================
# 구현리뷰 2차 필수 1 — node id는 B가 준 값이다
# ===========================================================================


def _renamed(projection: ChamberRelationResponse) -> ChamberRelationResponse:
    """**B가 node id 표기만 바꾼** 같은 graph.

    `label`·`business_id`는 그대로 두고 `id` 문자열만 다른 규칙(`n/<label>/<bid>`)으로
    바꾼다. 관계의 양끝도 같이 바꾼다. 즉 **의미는 동일하고 표기만 다르다.**
    """

    def rename(node_id: str) -> str:
        label, business_id = node_id.split(":", 1)
        return f"n/{label}/{business_id}"

    return ChamberRelationResponse(
        root_node_id=rename(projection.root_node_id),
        nodes=[
            GraphNode(
                id=rename(node.id),
                label=node.label,
                business_id=node.business_id,
                display_name=node.display_name,
                properties=node.properties,
            )
            for node in projection.nodes
        ],
        relationships=[
            GraphRelationship(
                id=relation.id,
                type=relation.type,
                source=rename(relation.source),
                target=rename(relation.target),
            )
            for relation in projection.relationships
        ],
        graph_revision=projection.graph_revision,
    )


class TestNodeIdsComeFromTheProjection:
    """C가 B의 node id **생성 규칙**을 복제해 들고 있으면 안 된다(2차 필수 1).

    초판은 `f"{label}:{business_id}"`로 id를 재구성했다. 그 경우 B가 표기를 바꾸면
    모든 관계 조회가 `None`이 되고, 결과는 예외가 아니라 **모든 step에서**
    `GRAPH_*_RELATION_MISSING`이다. `route_consistency`가 상시 false가 되고 운영은
    이를 "graph 데이터가 깨졌다"로 읽는다 — 실제로는 C와 B의 계약이 어긋난 것이다.
    """

    def test_a_changed_node_id_format_still_resolves(self) -> None:
        """표기가 바뀌어도 **일치는 일치다.** mismatch가 하나도 없어야 한다."""

        graph = _two_step_graph()
        renamed = _two_step_graph(
            projections={
                chamber: _renamed(projection)
                for chamber, projection in graph.projections.items()
            }
        )
        result = _resolve(_two_step_snapshot(), renamed)

        assert result.route_consistency is True
        assert result.mismatches == ()

    def test_the_relation_ids_are_unchanged_by_the_rename(self) -> None:
        """표기가 바뀌어도 **증거로 남는 relation id는 B가 준 그 값** 그대로다."""

        graph = _two_step_graph()
        renamed = _two_step_graph(
            projections={
                chamber: _renamed(projection)
                for chamber, projection in graph.projections.items()
            }
        )
        before = _resolve(_two_step_snapshot(), graph)
        after = _resolve(_two_step_snapshot(), renamed)

        assert [e.relation_ids for e in after.graph_evidence] == [
            e.relation_ids for e in before.graph_evidence
        ]
        assert any("REL-part-photo" in e.relation_ids for e in after.graph_evidence)

    def test_a_projection_without_nodes_finds_no_relation(self) -> None:
        """**node 목록을 실제로 읽는다**는 증거.

        관계는 그대로 두고 node만 비운 projection은 양끝을 특정할 수 없으므로 관계도
        찾을 수 없다. 초판 fixture가 전부 `nodes=[]`였던 탓에 이 경로가 한 번도
        실행되지 않았고, 그래서 id 재구성 결함이 green 뒤에 숨었다.
        """

        graph = _two_step_graph()
        stripped = _two_step_graph(
            projections={
                chamber: _projection(
                    chamber,
                    *projection.relationships,
                    revision=projection.graph_revision,
                    nodes=[],
                )
                for chamber, projection in graph.projections.items()
            }
        )
        result = _resolve(_two_step_snapshot(), stripped)

        assert result.route_consistency is False
        assert {m.code for m in result.mismatches} == {
            "GRAPH_PART_OF_RELATION_MISSING",
            "GRAPH_PERFORMS_RELATION_MISSING",
            "GRAPH_NEXT_STEP_RELATION_MISSING",
        }
        assert all(e.relation_ids == () for e in result.graph_evidence)

    def test_the_module_does_not_build_node_ids(self) -> None:
        """생산 코드에 node id **합성**이 남아 있지 않다.

        `_code_only()`는 `ast.unparse()` 결과라 따옴표 종류가 원본과 달라질 수 있다.
        그래서 따옴표를 포함한 문자열로 찾지 않고, **f-string 안에 `label`·`business_id`
        가 함께 들어간 표현식이 있는지**를 AST로 직접 본다.
        """

        tree = ast.parse(Path(rt.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            names = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
            assert not {"label", "business_id"} <= names, ast.unparse(node)

        body = _code_only(Path(rt.__file__))
        for forbidden in ("Chamber:", "ProcessStep:", "Equipment:"):
            assert forbidden not in body, forbidden

    def test_the_lookup_matches_on_both_label_and_business_id(self) -> None:
        """label만 같고 business_id가 다른 node를 잘못 집지 않는다."""

        assert (
            rt._node_id(
                _projection(
                    PHOTO_CHAMBER,
                    *_photo_relations(),
                ),
                "Equipment",
                "EQP99",
            )
            is None
        )


# ===========================================================================
# 구현리뷰 1차 필수 1·2·3
# ===========================================================================


class TestOneRequestUsesOneGraphRevision:
    """**chamber마다 쌍만 보면 marker 교체를 놓친다**(구현리뷰 1차 필수 1).

    `PHOTO(A/A) → ETCH(B/B)`는 chamber 내부 쌍이 각각 같아 통과한다. 그러나 marker
    revision은 chamber별 값이 아니라 graph 전체 bootstrap 값이다. 두 chamber 근거가 서로
    다른 revision에서 왔다면 adjacency를 같은 graph 상태에서 검증했다는 보장이 없다.
    """

    def _split_revision_graph(self) -> _Graph:
        other = "b" * 64
        return _two_step_graph(
            contexts={
                ETCH_CHAMBER: _context(
                    ETCH_CHAMBER,
                    equipment_id="EQP04",
                    step_id=ETCH_STEP,
                    upstream=(PHOTO_STEP,),
                    revision=other,
                )
            },
            projections={
                ETCH_CHAMBER: _projection(
                    ETCH_CHAMBER, *_etch_relations(), revision=other
                )
            },
        )

    def test_two_chambers_on_different_revisions_are_a_mismatch(self) -> None:
        result = _resolve(_two_step_snapshot(), self._split_revision_graph())
        assert result.route_consistency is False
        found = [m for m in result.mismatches if m.code == rt.REVISION_MISMATCH]
        assert found, [m.code for m in result.mismatches]

    def test_every_observed_revision_is_preserved(self) -> None:
        """어느 하나를 정답으로 골라 덮지 않는다."""

        result = _resolve(_two_step_snapshot(), self._split_revision_graph())
        found = next(m for m in result.mismatches if m.code == rt.REVISION_MISMATCH)
        assert found.graph_ids == ("a" * 64, "b" * 64)
        # evidence도 관측한 값을 그대로 들고 있다.
        assert {e.graph_revision for e in result.graph_evidence} == {
            "a" * 64,
            "b" * 64,
        }

    def test_the_revision_mismatch_is_request_scoped(self) -> None:
        """특정 WAFER·step의 잘못이 아니다 — scope를 표기로 구분한다."""

        result = _resolve(_two_step_snapshot(), self._split_revision_graph())
        found = next(m for m in result.mismatches if m.code == rt.REVISION_MISMATCH)
        assert found.wafer_id == rt.REQUEST_SCOPE
        assert found.from_lot_hist_id == rt.REQUEST_SCOPE
        assert found.to_lot_hist_id is None

    def test_one_revision_everywhere_stays_consistent(self) -> None:
        """음성 대조군. 전부 같은 revision이면 mismatch가 없다."""

        result = _resolve(_two_step_snapshot(), _two_step_graph())
        assert [m.code for m in result.mismatches] == []


class TestTheDbScopeClosesBeforeTheGraphCalls:
    """**DB 단계와 graph 단계가 분리돼 있다**(구현리뷰 1차 필수 2).

    한 함수가 connection을 받아 route를 읽고 이어서 Neo4j를 부르면 caller가 그 사이에
    DB scope를 빠져나갈 수 없다. Neo4j timeout 동안 PostgreSQL pool slot까지 잡힌다.
    """

    def test_the_graph_stage_cannot_take_a_connection(self) -> None:
        """**서명이 계약이다.** 인자에 connection이 없으면 그 구성을 만들 수 없다."""

        import inspect

        params = inspect.signature(rt.combine_route).parameters
        assert "connection" not in params
        annotations = {name: str(value.annotation) for name, value in params.items()}
        assert not any(
            "Connection" in text for text in annotations.values()
        ), annotations

    def test_the_db_stage_never_touches_the_graph(self) -> None:
        import inspect

        body = ast.unparse(
            ast.parse(inspect.getsource(rt.read_route_snapshot).lstrip())
        )
        for forbidden in ("equipment_context", "chamber_relations", "GraphBoundary"):
            assert forbidden not in body, forbidden

    def test_no_public_entry_point_takes_both(self) -> None:
        """connection과 graph를 함께 받는 공개 함수가 없다."""

        import inspect

        for name in rt.__all__:
            target = getattr(rt, name)
            if not inspect.isfunction(target):
                continue
            params = inspect.signature(target).parameters
            takes_connection = any(
                "Connection" in str(p.annotation) for p in params.values()
            )
            takes_graph = "graph" in params
            assert not (takes_connection and takes_graph), name


KNOWLEDGE_DIR = Path(rt.__file__).resolve().parents[1] / "knowledge"


def _module_ast(path: Path) -> ast.Module:
    """**import하지 않고** 읽는다.

    `app.knowledge.*`는 `app.common.config`를 끌어오고, 그 module은 import 시점에
    기본값 없는 환경변수 11개(`POSTGRES_*`·`READONLY_*`·`NEO4J_*`·`N8N_WEBHOOK_URL`)를
    읽는다. CI의 `Prove the routing and graph cross-check contract` step은 focused
    의존성만 설치하고 `.env`를 두지 않으므로, 이 파일이 B module을 import하면
    **collection 단계에서 죽는다**(실측: `RuntimeError: 필수 환경변수가 없습니다:
    POSTGRES_USER`).

    그 환경을 테스트에서 흉내 내려면 credential 모양 이름 11개를 이 파일에 적어야
    한다. 그것이 `GraphBoundary.production()`이 애초에 import를 지연시킨 이유이기도
    하다 — **route domain 단위 테스트는 B의 실행 환경을 요구하지 않는다.**

    그래서 identity 대신 **source 계약**을 고정한다. 한계는 분명하다: 같은 이름의
    다른 class로 옮겨 가면 여기서는 안 잡힌다. 그 축은 실제 wiring을 쓰는 통합
    경로(`V5-C-2.1` 이후)가 맡는다.
    """

    return ast.parse(path.read_text(encoding="utf-8"))


def _method(tree: ast.Module, class_name: str, method: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method:
                    return item
            raise AssertionError(f"{class_name}에 {method}가 없습니다")
    raise AssertionError(f"{class_name} class가 없습니다")


class TestTheProductionBoundaryIsWiredToB:
    """**기본 경로가 실제 B service로 간다**(구현리뷰 1차 필수 3).

    DI는 유지하되, override를 생략했을 때 어디로 가는지를 한 곳이 답해야 한다.
    """

    def test_production_binds_the_two_named_service_methods(self) -> None:
        """`production()`이 B의 어느 class·method로 가는지 코드가 답한다."""

        body = ast.unparse(
            _method(_module_ast(Path(rt.__file__)), "GraphBoundary", "production")
        )
        assert "from app.knowledge.service import" in body
        assert "EquipmentContextService" in body
        assert "GraphService" in body
        assert "EquipmentContextService().get_equipment_context" in body
        assert "GraphService().get_chamber_relations" in body

    def test_those_methods_exist_on_b_with_the_arity_c_uses(self) -> None:
        """**B 쪽 전제를 고정한다.** 이름이 바뀌거나 인자가 늘면 여기서 걸린다."""

        service = _module_ast(KNOWLEDGE_DIR / "service.py")
        for class_name, method in (
            ("EquipmentContextService", "get_equipment_context"),
            ("GraphService", "get_chamber_relations"),
        ):
            found = _method(service, class_name, method)
            args = found.args
            assert [a.arg for a in args.args] == ["self", "chamber_id"], method
            assert args.vararg is None and args.kwonlyargs == [], method

    def test_both_services_construct_with_no_argument(self) -> None:
        """`Service()`가 성립해야 `production()`이 성립한다.

        `__init__`의 `self` 뒤 인자가 전부 기본값을 가져야 한다.
        """

        service = _module_ast(KNOWLEDGE_DIR / "service.py")
        for class_name in ("EquipmentContextService", "GraphService"):
            init = _method(service, class_name, "__init__")
            required = len(init.args.args) - 1 - len(init.args.defaults)
            assert required == 0, class_name

    #: `production()`이 실제로 만드는 네 객체.
    #: **class마다 어느 module에 사는지 적는다.**
    #:
    #: 초판은 네 class를 전부 `repository.py`에서 찾고 없으면 `continue`했다.
    #: `GraphQueryRepository`는 `graph_query.py`에 있으므로 **조용히 건너뛰었다** —
    #: 검사한다고 적어 두고 실제로는 절반만 봤다.
    CONSTRUCTED = (
        ("service.py", "EquipmentContextService"),
        ("service.py", "GraphService"),
        ("repository.py", "ChamberGraphRepository"),
        ("graph_query.py", "GraphQueryRepository"),
    )

    def test_production_does_not_open_a_driver(self) -> None:
        """service·repository 생성만으로 Bolt를 열지 않는다.

        두 repository는 `driver_factory`를 **보관만** 한다. 그래서 금지 대상은
        "`driver_factory`가 등장하는가"가 아니라 **"호출되는가"**다. 문자열 검색으로는
        둘을 구분할 수 없어 `ast.Call`의 피호출자만 본다.

        실물 spy가 더 강하지만 그러려면 B module을 import해야 하고, 그건
        `test_this_file_never_imports_b_at_runtime`이 금지하는 바로 그것이다.
        실제 호출 0회는 별도 환경에서 확인한 뒤 구현보고에 적는다.
        """

        for filename, class_name in self.CONSTRUCTED:
            init = _method(
                _module_ast(KNOWLEDGE_DIR / filename), class_name, "__init__"
            )
            for call in (n for n in ast.walk(init) if isinstance(n, ast.Call)):
                callee = ast.unparse(call.func)
                assert "driver" not in callee, f"{class_name}: {callee}"
                assert "session" not in callee, f"{class_name}: {callee}"

    def test_every_constructed_class_is_actually_found(self) -> None:
        """**건너뛰기를 금지한다.** 위 표의 네 class가 모두 실재해야 한다."""

        for filename, class_name in self.CONSTRUCTED:
            _method(_module_ast(KNOWLEDGE_DIR / filename), class_name, "__init__")

    def test_the_knowledge_import_is_deferred(self) -> None:
        """module 최상단에서 knowledge를 끌어오지 않는다."""

        head = Path(rt.__file__).read_text(encoding="utf-8").split("__all__")[0]
        assert "app.knowledge" not in head

    def test_this_file_never_imports_b_at_runtime(self) -> None:
        """**CI가 이 파일을 수집할 수 있어야 한다.**

        `app.knowledge.*`를 import하는 순간 `app.common.config`가 딸려 오고, `.env`가
        없는 CI step에서 collection이 죽는다. 실제로 그렇게 죽었다. 회귀로 고정한다.
        `app.knowledge.schemas`·`exceptions`는 config를 끌어오지 않으므로 예외다.
        """

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        allowed = {"app.knowledge.schemas", "app.knowledge.exceptions"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app.knowledge"):
                    assert node.module in allowed, node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.knowledge"), alias.name


class TestTheRealProjectionShapeErrorIsNotDowngraded:
    """**B가 실제로 던지는 형상 예외를 잡는다**(구현리뷰 2차 필수 1).

    `ChamberGraphRepository`는 projection 행이 계약과 다르면 Pydantic `ValidationError`
    가 아니라 `GraphProjectionShapeError`를 던진다. 앞선 회귀는 Pydantic 오류만 던져
    `GraphBoundary.production()`이 실제로 타는 경로를 검증하지 못했다.
    """

    def test_the_repository_shape_error_maps_to_shape_not_dependency(self) -> None:
        from app.knowledge.exceptions import GraphProjectionShapeError

        def _boom() -> Any:
            raise GraphProjectionShapeError("MISSING_ROOT_NODE_ID")

        graph = _two_step_graph(projections={PHOTO_CHAMBER: _boom})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        assert exc.value.code == "GRAPH_SHAPE_ERROR"

    def test_the_repository_actually_raises_that_class(self) -> None:
        """**B 쪽 전제를 고정한다.** 그 예외가 사라지면 mapping이 죽은 코드가 된다.

        원래는 module을 import해 `__file__`을 얻었다. 그 한 줄 때문에
        `app.common.config`가 딸려 왔고 `.env` 없는 CI step에서 collection이 죽었다.
        파일 내용만 필요하므로 경로로 읽는다.
        """

        body = (KNOWLEDGE_DIR / "repository.py").read_text(encoding="utf-8")
        assert body.count("raise GraphProjectionShapeError(") >= 1

    def test_no_projection_detail_reaches_the_caller(self) -> None:
        from app.knowledge.exceptions import GraphProjectionShapeError

        def _boom() -> Any:
            raise GraphProjectionShapeError("MATCH (c:Chamber) bolt://user:pw@host")

        graph = _two_step_graph(projections={PHOTO_CHAMBER: _boom})
        with pytest.raises(rt.RoutingDependencyError) as exc:
            _resolve(_two_step_snapshot(), graph)
        rendered = str(exc.value)
        assert "MATCH" not in rendered
        assert "bolt://" not in rendered
        assert "pw" not in rendered

    def test_the_knowledge_import_stays_deferred(self) -> None:
        """형상 예외를 잡으려고 module 최상단 import를 들이지 않는다."""

        head = Path(rt.__file__).read_text(encoding="utf-8").split("__all__")[0]
        assert "app.knowledge" not in head


class TestTheSnapshotIsBoundToItsIncident:
    """**DB 단계 결과를 다른 incident에 붙일 수 없다**(구현리뷰 4차 필수 1).

    두 단계를 나눈 뒤(1차 필수 2) 두 값이 따로 다니면 caller가 지역 변수를 잘못
    조합할 수 있다. 실측 결과 `LOT-WRONG` incident에 `LOT001` route가 붙은 채
    `route_consistency=True`가 나왔다 — 오류보다 위험한 **일관된 성공 모양**이다.
    """

    def _wrong_key_incident(self) -> ResolvedIncident:
        ref = _ref(TRACE, "TA-01")
        return ResolvedIncident(
            lot_id="LOT-WRONG",
            chamber_id="EQP99-PM9",
            requested_alarm=ref,
            representative_alarm=ref,
            member_alarms=(ref,),
        )

    def test_a_different_incident_key_is_refused_before_any_graph_call(self) -> None:
        graph = _two_step_graph()
        bound = rt.IncidentRoute(
            incident=self._wrong_key_incident(), snapshot=_two_step_snapshot()
        )
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(bound, graph=_boundary(graph))
        assert exc.value.code == "ROUTE_INCIDENT_MISMATCH"
        assert graph.context_calls == [], "거부 전에 graph를 불렀다"
        assert graph.projection_calls == []

    def test_a_different_member_set_is_refused(self) -> None:
        """key가 같아도 member 구성이 다르면 그 snapshot이 아니다."""

        graph = _two_step_graph()
        other = _incident(_ref(TRACE, "TA-01"), _ref(SUMMARY, "SA-01"))
        bound = rt.IncidentRoute(incident=other, snapshot=_two_step_snapshot())
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(bound, graph=_boundary(graph))
        assert exc.value.code == "ROUTE_INCIDENT_MISMATCH"
        assert graph.context_calls == []

    def test_an_orphan_step_is_not_a_success(self) -> None:
        """member가 가리키지 않는 WAFER의 step은 이 incident의 route가 아니다."""

        graph = _two_step_graph()
        snapshot = _snapshot(
            {(TRACE, "TA-01"): "LOT001W001"},
            (
                _step("LH-1", track_in_at=T0),
                _step("LH-ORPHAN", wafer_id="LOT001W999", track_in_at=T1),
            ),
        )  # step에만 있는 WAFER
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(
                rt.IncidentRoute(incident=_incident(), snapshot=snapshot),
                graph=_boundary(graph),
            )
        assert exc.value.code == "WAFER_ROUTE_INCOMPLETE"
        assert graph.context_calls == []

    def test_the_member_mapping_cannot_be_mutated(self) -> None:
        """`frozen=True`는 속성 재대입만 막는다 — 안쪽 dict는 별도로 막아야 한다.

        초판에서는 `snapshot.wafer_of_member.clear()`가 성공했고 그 뒤 결합이 sanitized
        오류가 아니라 raw `KeyError`로 나갔다.

        **타입이 보장한다.** 이 fixture는 평범한 `dict`를 넘기는데도 읽기 전용이 된다 —
        생성 지점 하나에서만 감쌌다면 이 회귀가 red다.
        """

        snapshot = _two_step_snapshot()
        assert isinstance(snapshot.wafer_of_member, MappingProxyType)
        # `mappingproxy`에는 `clear`가 없다 — `TypeError`가 아니라 `AttributeError`다.
        with pytest.raises(AttributeError):
            snapshot.wafer_of_member.clear()  # type: ignore[attr-defined]
        with pytest.raises(TypeError):
            snapshot.wafer_of_member[(TRACE, "X")] = "W"  # type: ignore[index]
        # 읽기는 그대로 된다.
        assert snapshot.wafer_of_member[(TRACE, "TA-01")] == "LOT001W001"

    def test_the_snapshot_records_what_it_was_read_with(self) -> None:
        incident = _incident()
        snapshot = _two_step_snapshot()
        assert (snapshot.lot_id, snapshot.chamber_id) == (
            incident.lot_id,
            incident.chamber_id,
        )
        assert snapshot.member_keys == ((TRACE, "TA-01"),)

    def test_the_graph_stage_takes_one_bound_object(self) -> None:
        """**서명이 계약이다.** 두 값을 따로 받으면 어긋난 조합을 만들 수 있다."""

        import inspect

        params = list(inspect.signature(rt.combine_route).parameters)
        assert params == ["bound", "graph"], params


class TestTheThreeSetsMustAgree:
    """**provenance·mapping·step 세 집합을 서로 대조한다**(구현리뷰 5차 필수 1).

    한 방향만 보면 나머지 형상이 통과한다. 초판은 `step ⊆ mapping` 하나만 봐서
    mapping에만 있는 WAFER는 **빈 member route로 성공**했고, provenance에만 있는
    member는 결과에서 **조용히 사라졌다.**
    """

    def test_mutating_a_shared_alarm_ref_no_longer_corrupts_lookup(self) -> None:
        """**provenance가 객체가 아니라 key다.**

        `AlarmRef`는 frozen이 아니다. 초판은 incident의 객체를 snapshot이 그대로 들고
        있어 한쪽을 바꾸면 양쪽 tuple이 함께 바뀌었고, mapping key만 예전 값으로 남아
        결합이 sanitized 오류가 아니라 raw `KeyError`로 나갔다.
        """

        incident = _incident()
        snapshot = _two_step_snapshot()
        incident.member_alarms[0].alarm_id = "MUTATED"

        graph = _two_step_graph()
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(
                rt.IncidentRoute(incident=incident, snapshot=snapshot),
                graph=_boundary(graph),
            )
        assert exc.value.code == "ROUTE_INCIDENT_MISMATCH"
        assert graph.context_calls == []
        # snapshot의 provenance는 값이므로 함께 바뀌지 않았다.
        assert snapshot.member_keys == ((TRACE, "TA-01"),)

    def test_provenance_and_mapping_keys_must_match(self) -> None:
        graph = _two_step_graph()
        snapshot = rt_repo.RouteSnapshot(
            lot_id=LOT,
            chamber_id=PHOTO_CHAMBER,
            member_keys=((TRACE, "TA-01"),),
            wafer_of_member={(TRACE, "OTHER"): "LOT001W001"},
            steps=_two_step_snapshot().steps,
        )
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(
                rt.IncidentRoute(incident=_incident(), snapshot=snapshot),
                graph=_boundary(graph),
            )
        assert exc.value.code == "ROUTE_INCIDENT_MISMATCH"
        assert graph.context_calls == []

    def test_a_mapped_wafer_without_steps_is_refused(self) -> None:
        """**mapping에만 있는 WAFER.** 초판은 빈 member route를 만들며 성공했다."""

        graph = _two_step_graph()
        incident = _incident(_ref(TRACE, "TA-01"), _ref(SUMMARY, "SA-01"))
        snapshot = _snapshot(
            {
                (TRACE, "TA-01"): "LOT001W001",
                (SUMMARY, "SA-01"): "LOT001W002",  # step이 없는 WAFER
            },
            _two_step_snapshot().steps,
            incident,
        )
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(
                rt.IncidentRoute(incident=incident, snapshot=snapshot),
                graph=_boundary(graph),
            )
        assert exc.value.code == "WAFER_ROUTE_INCOMPLETE"
        assert graph.context_calls == []

    def test_a_duplicate_member_identity_is_not_collapsed(self) -> None:
        """손으로 만든 envelope의 중복 identity를 조용히 축약하지 않는다."""

        graph = _two_step_graph()
        ref = _ref(TRACE, "TA-01")
        incident = ResolvedIncident(
            lot_id=LOT,
            chamber_id=PHOTO_CHAMBER,
            requested_alarm=ref,
            representative_alarm=ref,
            member_alarms=(ref, _ref(TRACE, "TA-01")),
        )
        snapshot = rt_repo.RouteSnapshot(
            lot_id=LOT,
            chamber_id=PHOTO_CHAMBER,
            member_keys=((TRACE, "TA-01"), (TRACE, "TA-01")),
            wafer_of_member={(TRACE, "TA-01"): "LOT001W001"},
            steps=_two_step_snapshot().steps,
        )
        with pytest.raises(repo.RepositoryContractError) as exc:
            rt.combine_route(
                rt.IncidentRoute(incident=incident, snapshot=snapshot),
                graph=_boundary(graph),
            )
        assert exc.value.code == "ROUTE_INCIDENT_MISMATCH"
        assert graph.context_calls == []

    def test_the_provenance_is_plain_immutable_keys(self) -> None:
        """`AlarmRef` 객체를 보관하지 않는다 — 참조를 담으면 그것으로 계약이 깨진다."""

        snapshot = _two_step_snapshot()
        assert all(
            isinstance(key, tuple) and len(key) == 2 for key in snapshot.member_keys
        )
        assert not any(isinstance(key, AlarmRef) for key in snapshot.member_keys)
