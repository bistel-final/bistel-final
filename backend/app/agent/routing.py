"""실제 WAFER routing과 Process Step 교차검증 (`V5-C-1.2`).

## 무엇이 기준인가

**실제 route의 단일 기준은 PostgreSQL `lot_history`다.** Neo4j는 구조 교차검증
근거이며 PostgreSQL 경로를 보정하거나 대체하지 않는다. 두 근거가 다르면 route를
버리거나 graph 값으로 덮지 않고 `route_consistency=false`와 양쪽 ID를 함께 남긴다.

**불일치와 의존성 실패는 다르다.** 두 근거가 정상 반환됐는데 내용이 다른 것은 결과로
보존한다. graph 조회가 실패해 비교 근거를 얻지 못한 것은 false가 아니라 실패다.

## graph를 두 번 읽는 이유

`V5-B-3.2`가 compact Tool에서 relation payload를 의도적으로 제외했다. compact는
방향별 인접 Step 판정에, full projection은 **실제 relation ID** provenance에 쓴다.
C가 `REL-*`를 합성하거나 B DTO를 되돌리지 않는다. public HTTP·router를 부르지 않고
내부 service 두 경계를 주입받는다.

두 조회는 하나의 atomic snapshot이 아니다. `graph_revision` 일치 검사는 **두 조회
사이에 marker가 교체됐는지**만 잡는다 — compact·full repository가 모두 같은 marker
파일을 읽기 때문이다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy.engine import Connection

from app.agent.incident import ResolvedIncident
from app.agent.repository import AgentRepositoryError, RepositoryContractError
from app.agent.routing_repository import (
    RouteSnapshot,
    RouteStep,
    fetch_route_snapshot,
)
from app.common.enums import AlarmSource
from app.common.schemas import AlarmRef

__all__ = [
    "GRAPH_REASON_CODES",
    "REQUEST_SCOPE",
    "RoutingDependencyError",
    "GraphBoundary",
    "IncidentRoute",
    "GraphRouteEvidence",
    "RouteMismatch",
    "WaferRoute",
    "ResolvedIncidentRoute",
    "read_route_snapshot",
    "combine_route",
]

#: 요청 전체에 걸린 mismatch의 scope 표기. 특정 WAFER·step의 잘못이 아니다.
REQUEST_SCOPE: Final = ""


class RoutingDependencyError(AgentRepositoryError):
    """graph 근거를 얻지 못했다. **불일치가 아니라 실패다.**

    C-0.1의 `RepositoryUnavailable`을 쓰지 않는다. 그쪽 docstring은 접속·DB 가용성을
    뜻하는데 여기 원인은 Neo4j 의존성이다. 같은 뿌리(`AgentRepositoryError`)를 공유해
    caller가 한 번에 잡을 수 있고, DB 예외 계층을 복제하지도 않는다.

    reason 문자열·Neo4j URI·Cypher를 code에 복사하지 않는다.
    """


#: B 공통 실패 prefix → C routing code. **8개 전부를 옮긴다.**
#:
#: `GRAPH_SHAPE_ERROR:`는 계약 밖 값이 아니라 B가 graph 형상 결손을 구분하려고 쓰는
#: 정상 접두어다(`tool_contracts.REASON_PREFIXES`). `DEPENDENCY_ERROR`로 뭉개면 C의
#: 오류가 원천보다 덜 정확해진다.
#:
#: `ToolResult` validator가 prefix 밖 reason을 생성 단계에서 거부하므로 아홉 번째
#: 접두어가 도달할 경로는 없다.
GRAPH_REASON_CODES: Final[Mapping[str, str]] = {
    "NOT_FOUND:": "GRAPH_CONTEXT_NOT_FOUND",
    "TIMEOUT:": "GRAPH_CONTEXT_TIMEOUT",
    "MODEL_NOT_READY:": "GRAPH_MODEL_NOT_READY",
    "LLM_NOT_READY:": "GRAPH_LLM_NOT_READY",
    "GRAPH_SHAPE_ERROR:": "GRAPH_SHAPE_ERROR",
    "DEPENDENCY_ERROR:": "GRAPH_DEPENDENCY_ERROR",
    "POLICY_REJECTED:": "GRAPH_POLICY_REJECTED",
    "IDEMPOTENCY_CONFLICT:": "GRAPH_IDEMPOTENCY_CONFLICT",
}

_NOT_FOUND: Final = "GRAPH_CONTEXT_NOT_FOUND"
_TIMEOUT: Final = "GRAPH_CONTEXT_TIMEOUT"
_SHAPE: Final = "GRAPH_SHAPE_ERROR"
_DEPENDENCY: Final = "GRAPH_DEPENDENCY_ERROR"

#: mismatch code. 문자열 설명이 아니라 이 code를 테스트한다.
REVISION_MISMATCH: Final = "GRAPH_REVISION_MISMATCH"
CHAMBER_MISMATCH: Final = "GRAPH_CHAMBER_MISMATCH"
EQUIPMENT_MISMATCH: Final = "GRAPH_EQUIPMENT_MISMATCH"
PROCESS_STEP_MISMATCH: Final = "GRAPH_PROCESS_STEP_MISMATCH"
PART_OF_MISSING: Final = "GRAPH_PART_OF_RELATION_MISSING"
PERFORMS_MISSING: Final = "GRAPH_PERFORMS_RELATION_MISSING"
DOWNSTREAM_MISSING: Final = "GRAPH_DOWNSTREAM_MISSING"
UPSTREAM_MISSING: Final = "GRAPH_UPSTREAM_MISSING"
NEXT_STEP_MISSING: Final = "GRAPH_NEXT_STEP_RELATION_MISSING"

#: 결속 검증 실패. **C-1.2 Repository가 이미 쓰는 code를 그대로 쓴다.**
_INCIDENT_MISMATCH: Final = "ROUTE_INCIDENT_MISMATCH"
_ROUTE_INCOMPLETE: Final = "WAFER_ROUTE_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class GraphBoundary:
    """B의 내부 service 두 경계. **public HTTP·router를 거치지 않는다.**

    `production()`이 실제 bound method를 들고 온다. 테스트는 같은 Pydantic DTO를 주는
    stub을 주입하되, 기본 경로가 어디로 가는지는 `production()`이 한 곳에서 답한다.
    """

    equipment_context: Callable[[str], Any]
    chamber_relations: Callable[[str], Any]

    @classmethod
    def production(cls) -> GraphBoundary:
        """B service의 기본 구성. **import를 지연시킨다.**

        module import 시점에 knowledge 계층을 끌어오면 route domain 단위 테스트가 B의
        의존성까지 요구한다. 두 service의 repository는 생성 시 Neo4j에 붙지 않는다 —
        `driver_factory`가 기본 인자일 뿐 호출되지 않는다.
        """

        from app.knowledge.service import EquipmentContextService, GraphService

        return cls(
            equipment_context=EquipmentContextService().get_equipment_context,
            chamber_relations=GraphService().get_chamber_relations,
        )


@dataclass(frozen=True, slots=True)
class GraphRouteEvidence:
    """chamber 하나에 대한 graph 근거. `relation_ids`는 **실제 projection 값**이다."""

    chamber_id: str
    equipment_id: str | None
    process_step_id: str | None
    upstream_process_step_ids: tuple[str, ...]
    downstream_process_step_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    graph_revision: str | None


@dataclass(frozen=True, slots=True)
class RouteMismatch:
    """불일치 하나. 양쪽 ID를 **구조화 필드로** 남긴다."""

    code: str
    wafer_id: str
    from_lot_hist_id: str
    to_lot_hist_id: str | None
    postgres_ids: tuple[str, ...]
    graph_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WaferRoute:
    wafer_id: str
    member_alarms: tuple[AlarmRef, ...]
    steps: tuple[RouteStep, ...]


@dataclass(frozen=True, slots=True)
class ResolvedIncidentRoute:
    """C-2.1이 State에 넣을 결합 결과.

    `route_consistency`는 **incident 단위 단일 bool**이다. WAFER별 판정이 필요하면
    `mismatches`의 `wafer_id`로 파생한다 — 같은 사실을 두 곳에 저장하지 않는다.
    """

    incident: ResolvedIncident
    wafer_routes: tuple[WaferRoute, ...]
    graph_evidence: tuple[GraphRouteEvidence, ...]
    route_consistency: bool
    mismatches: tuple[RouteMismatch, ...]


@dataclass(frozen=True, slots=True)
class IncidentRoute:
    """DB 단계의 결과. **incident와 snapshot을 하나로 묶는다.**

    두 단계를 나눈 뒤(1차 필수 2) 두 값이 따로 다니면 caller가 지역 변수를 잘못
    조합할 수 있다. 실측으로 확인했다 — member만 같고 incident key가 다른 값을
    넣었더니 `LOT-WRONG` incident에 `LOT001` route가 붙은 채 `route_consistency=True`
    가 나왔다. 오류보다 위험한 **일관된 성공 모양**이다(구현리뷰 4차 필수 1).

    한 객체로 묶으면 정상 경로에서 그 조합이 **만들어지지 않는다.** 손으로 만든
    envelope는 `combine_route()`가 graph 호출 전에 거부한다.
    """

    incident: ResolvedIncident
    snapshot: RouteSnapshot


def _node_id(full: Any, label: str, business_id: str) -> str | None:
    """**B가 준 node id를 찾는다. 만들지 않는다.**

    초판은 `f"{label}:{business_id}"`로 재구성했다. 그건 B의 node id **생성 규칙**을 C가
    복제해 들고 있는 것이고, 이 모듈이 `relation_ids`에 대해 세운 원칙("합성하지 않고
    projection 값을 그대로 남긴다")과 정반대다.

    **어긋나면 조용히 틀린다.** B가 표기를 바꾸면 모든 관계 조회가 `None`이 되고,
    결과는 예외가 아니라 `GRAPH_*_RELATION_MISSING`이 **모든 step·모든 incident에서**
    나오는 것이다. `route_consistency=false`가 상시화되고 운영에서는 "graph 데이터가
    깨졌다"로 읽힌다 — 실제로는 계약 파손인데도(구현리뷰 PR #155 필수 1).

    `business_id`·`label`은 B가 node payload에 별도 필드로 실어 준다. 그것으로 찾는다.
    """

    for node in full.nodes:
        if node.label == label and node.business_id == business_id:
            return str(node.id)
    return None


def _graph_shape_error() -> type[BaseException]:
    """B가 실제로 던지는 형상 예외. **import를 지연시킨다.**

    `ChamberGraphRepository`는 projection 행이 계약과 다르면 Pydantic `ValidationError`
    가 아니라 `GraphProjectionShapeError`를 던진다(`repository.py` 4곳). 잡지 않으면
    "graph 형상이 깨졌다"가 일반 의존성 오류로 축소된다 — B가 만든 구분이 사라진다.
    """

    from app.knowledge.exceptions import GraphProjectionShapeError

    return GraphProjectionShapeError


def _call(load: Callable[[str], Any], chamber_id: str) -> Any:
    """B service 한 번 호출. **원인 상세를 code에 담지 않는다.**"""

    try:
        result = load(chamber_id)
    except TimeoutError as exc:
        raise RoutingDependencyError(
            _TIMEOUT, "graph context 조회가 만료됐습니다"
        ) from exc
    except ValidationError as exc:
        raise RoutingDependencyError(_SHAPE, "graph 형상이 계약과 다릅니다") from exc
    except RoutingDependencyError:
        raise
    except Exception as exc:
        # Pydantic 검증과 repository 형상 오류는 **같은 뜻**이다. 둘 다 shape로 옮긴다.
        if isinstance(exc, _graph_shape_error()):
            raise RoutingDependencyError(
                _SHAPE, "graph 형상이 계약과 다릅니다"
            ) from exc
        raise RoutingDependencyError(
            _DEPENDENCY, "graph 의존성 호출이 실패했습니다"
        ) from exc
    if result is None:
        raise RoutingDependencyError(_NOT_FOUND, "graph context가 없습니다")
    # `ok`는 compact Tool DTO에만 있다. full projection에는 그 필드가 없어 실패 DTO가
    # 존재할 수 없다(`ChamberRelationResponse`).
    if getattr(result, "ok", True) is False:
        reason = str(getattr(result, "reason", ""))
        for prefix, code in GRAPH_REASON_CODES.items():
            if reason.startswith(prefix):
                raise RoutingDependencyError(code, "graph 조회가 실패했습니다")
        raise RoutingDependencyError(  # pragma: no cover - validator가 먼저 거부한다
            _DEPENDENCY, "graph 조회가 실패했습니다"
        )
    return result


def _relation_id(
    full: Any,
    kind: str,
    source: tuple[str, str],
    target: tuple[str, str],
) -> str | None:
    """`(label, business_id)` 두 쌍으로 관계를 찾는다.

    양끝 node를 projection에서 먼저 찾고, 그 **id 값**으로 relation을 대조한다. node가
    없으면 관계도 없는 것이다.
    """

    source_id = _node_id(full, *source)
    target_id = _node_id(full, *target)
    if source_id is None or target_id is None:
        return None
    for relation in full.relationships:
        if relation.type == kind and relation.source == source_id:
            if relation.target == target_id:
                return str(relation.id)
    return None


def read_route_snapshot(
    connection: Connection, incident: ResolvedIncident
) -> IncidentRoute:
    """**DB 단계.** graph를 부르지 않는다.

    `combine_route()`와 나눈 이유는 connection 수명이다. 한 함수가 DB를 읽고 이어서
    Neo4j를 부르면 caller가 그 사이에 connection context를 빠져나갈 수 없다. Neo4j
    timeout 동안 PostgreSQL pool slot까지 함께 잡히고, 동시 실행에서 pool이 마른다.

    caller는 이 함수의 결과를 받고 **DB scope를 닫은 뒤** `combine_route()`를 부른다.
    """

    return IncidentRoute(
        incident=incident,
        snapshot=fetch_route_snapshot(
            connection,
            lot_id=incident.lot_id,
            chamber_id=incident.chamber_id,
            member_alarms=incident.member_alarms,
        ),
    )


def combine_route(
    bound: IncidentRoute,
    *,
    graph: GraphBoundary,
) -> ResolvedIncidentRoute:
    """**graph 단계.** `Connection`을 받지 않는다 — 받을 수 없어야 한다.

    이 서명이 계약이다. connection 인자가 없으므로 이 함수가 DB를 잡은 채 Neo4j를
    기다리는 구성 자체를 만들 수 없다. incident와 snapshot도 하나로 받으므로 서로 다른
    incident의 값을 조합할 수 없다.

    envelope를 손으로 만든 경우는 **graph 호출 전에** 거부한다.
    """

    incident = bound.incident
    snapshot = bound.snapshot
    _assert_bound(incident, snapshot)
    equipment_context = graph.equipment_context
    chamber_relations = graph.chamber_relations

    members_by_wafer: dict[str, list[AlarmRef]] = {}
    for alarm in incident.member_alarms:
        # C-1.1이 정한 순서를 그대로 보존한다.
        wafer = snapshot.wafer_of_member[(AlarmSource(alarm.source), alarm.alarm_id)]
        members_by_wafer.setdefault(wafer, []).append(alarm)

    steps_by_wafer: dict[str, list[RouteStep]] = {}
    for step in snapshot.steps:
        steps_by_wafer.setdefault(step.wafer_id, []).append(step)
    for steps in steps_by_wafer.values():
        steps.sort(key=lambda s: (s.track_in_at, s.lot_hist_id))

    routes = tuple(
        WaferRoute(
            wafer_id=wafer,
            # **직접 indexing한다.** `_assert_bound()`가 mapping WAFER 집합과 route
            # WAFER 집합의 일치를 이미 강제하므로 여기서 빠지는 WAFER는 없다.
            # `.get(wafer, ())`는 도달 불가 기본값이었고, guard가 미래에 되돌아가면
            # **member 없는 route를 조용히 만들어 준다**(구현리뷰 7차 권장 2).
            member_alarms=tuple(members_by_wafer[wafer]),
            steps=tuple(steps),
        )
        for wafer, steps in sorted(
            steps_by_wafer.items(),
            key=lambda item: (item[1][0].track_in_at, item[1][0].lot_hist_id, item[0]),
        )
    )

    # unique chamber를 먼저 안정 정렬하고 **요청 로컬**로만 1회씩 읽는다.
    # process·global cache를 두면 marker loader가 매번 live revision을 읽는 계약이
    # stale cache로 약해진다.
    chambers = sorted({step.chamber_id for route in routes for step in route.steps})
    compact: dict[str, Any] = {}
    full: dict[str, Any] = {}
    for chamber in chambers:
        compact[chamber] = _call(equipment_context, chamber)
        full[chamber] = _call(chamber_relations, chamber)

    mismatches: list[RouteMismatch] = []
    relation_ids: dict[str, list[str]] = {chamber: [] for chamber in chambers}

    # **요청 전체가 한 revision이어야 한다.**
    #
    # chamber마다 compact/full 쌍만 비교하면 `PHOTO(A/A) → ETCH(B/B)`가 통과한다. 쌍은
    # 각각 같지만 두 chamber 근거가 서로 다른 graph 상태에서 왔다는 뜻이고, 그러면
    # adjacency를 같은 graph에서 검증했다는 보장이 없다. marker revision은 chamber별이
    # 아니라 graph 전체 bootstrap 값이다.
    observed = {
        str(source.graph_revision)
        for chamber in chambers
        for source in (compact[chamber], full[chamber])
    }
    if len(observed) > 1:
        # 어느 하나를 정답으로 고르지 않는다 — 관측한 값을 전부 남긴다.
        mismatches.append(
            RouteMismatch(
                code=REVISION_MISMATCH,
                wafer_id=REQUEST_SCOPE,
                from_lot_hist_id=REQUEST_SCOPE,
                to_lot_hist_id=None,
                postgres_ids=(),
                graph_ids=tuple(sorted(observed)),
                relation_ids=(),
            )
        )

    for route in routes:
        for step in route.steps:
            _check_step(step, compact, full, relation_ids, mismatches)
        for first, second in zip(route.steps, route.steps[1:], strict=False):
            _check_adjacency(first, second, compact, full, relation_ids, mismatches)

    evidence = tuple(
        GraphRouteEvidence(
            chamber_id=chamber,
            equipment_id=compact[chamber].equipment_id,
            process_step_id=compact[chamber].process_step_id,
            upstream_process_step_ids=tuple(compact[chamber].upstream_process_step_ids),
            downstream_process_step_ids=tuple(
                compact[chamber].downstream_process_step_ids
            ),
            relation_ids=tuple(dict.fromkeys(relation_ids[chamber])),
            graph_revision=compact[chamber].graph_revision,
        )
        for chamber in chambers
    )

    unique = tuple(dict.fromkeys(mismatches))
    ordered = tuple(
        sorted(
            unique,
            key=lambda m: (
                m.wafer_id,
                m.from_lot_hist_id,
                m.to_lot_hist_id or "",
                m.code,
            ),
        )
    )
    return ResolvedIncidentRoute(
        incident=incident,
        wafer_routes=routes,
        graph_evidence=evidence,
        route_consistency=not ordered,
        mismatches=ordered,
    )


def _member_keys(incident: ResolvedIncident) -> tuple[tuple[AlarmSource, str], ...]:
    return tuple(
        (AlarmSource(alarm.source), alarm.alarm_id) for alarm in incident.member_alarms
    )


def _assert_bound(incident: ResolvedIncident, snapshot: RouteSnapshot) -> None:
    """snapshot이 **이 incident에서 읽힌 것**인지 확인한다. graph를 부르기 전이다.

    정상 경로(`read_route_snapshot()`)는 항상 일치하는 envelope를 만든다. 이 검사가 잡는
    것은 손으로 조립한 envelope와, 두 read 사이에 member 구성이 바뀐 경우다 — C-1.1의
    `INCIDENT_KEY_MISMATCH`와 같은 성격이다.

    **세 집합을 서로 대조한다.** 한 방향만 보면 나머지 형상이 통과한다 — 초판은
    `step ⊆ mapping` 하나만 봐서, mapping에만 있는 WAFER는 빈 member route로 성공했고
    provenance에만 있는 member는 결과에서 조용히 사라졌다(구현리뷰 5차 필수 1).
    """

    keys = _member_keys(incident)
    if (incident.lot_id, incident.chamber_id) != (snapshot.lot_id, snapshot.chamber_id):
        raise RepositoryContractError(_INCIDENT_MISMATCH)
    if keys != snapshot.member_keys:
        raise RepositoryContractError(_INCIDENT_MISMATCH)
    if len(set(keys)) != len(keys):
        # 손으로 만든 envelope의 중복 identity를 조용히 축약하지 않는다.
        raise RepositoryContractError(_INCIDENT_MISMATCH)
    if set(snapshot.member_keys) != set(snapshot.wafer_of_member):
        # provenance와 mapping key가 어긋나면 lookup이 raw KeyError로 나간다.
        raise RepositoryContractError(_INCIDENT_MISMATCH)
    if set(snapshot.member_keys) != set(snapshot.lot_hist_id_of_member):
        # RouteSnapshot.__post_init__은 mapping을 불변으로만 만들고 key 정합성은
        # 판정하지 않는다. 실제 incident까지 가진 이 결합 경계에서 wafer mapping과
        # 같은 계약으로 대조한다.
        raise RepositoryContractError(_INCIDENT_MISMATCH)
    mapped = set(snapshot.wafer_of_member.values())
    walked = {step.wafer_id for step in snapshot.steps}
    if mapped != walked:
        # 양방향이다. mapping에만 있는 WAFER도, step에만 있는 WAFER도 route가 아니다.
        raise RepositoryContractError(_ROUTE_INCOMPLETE)


def _add(
    mismatches: list[RouteMismatch],
    code: str,
    step: RouteStep,
    *,
    to_step: RouteStep | None = None,
    postgres_ids: Sequence[str] = (),
    graph_ids: Sequence[str] = (),
    relation_ids: Sequence[str] = (),
) -> None:
    mismatches.append(
        RouteMismatch(
            code=code,
            wafer_id=step.wafer_id,
            from_lot_hist_id=step.lot_hist_id,
            to_lot_hist_id=None if to_step is None else to_step.lot_hist_id,
            postgres_ids=tuple(postgres_ids),
            graph_ids=tuple(graph_ids),
            relation_ids=tuple(relation_ids),
        )
    )


def _check_step(
    step: RouteStep,
    compact: dict[str, Any],
    full: dict[str, Any],
    relation_ids: dict[str, list[str]],
    mismatches: list[RouteMismatch],
) -> None:
    context = compact[step.chamber_id]
    projection = full[step.chamber_id]

    if context.graph_revision != projection.graph_revision:
        _add(
            mismatches,
            REVISION_MISMATCH,
            step,
            graph_ids=(str(context.graph_revision), str(projection.graph_revision)),
        )
    if context.chamber_id != step.chamber_id:
        _add(
            mismatches,
            CHAMBER_MISMATCH,
            step,
            postgres_ids=(step.chamber_id,),
            graph_ids=(str(context.chamber_id),),
        )
    if context.equipment_id != step.equipment_id:
        _add(
            mismatches,
            EQUIPMENT_MISMATCH,
            step,
            postgres_ids=(step.equipment_id,),
            graph_ids=(str(context.equipment_id),),
        )
    if context.process_step_id != step.step_id:
        _add(
            mismatches,
            PROCESS_STEP_MISMATCH,
            step,
            postgres_ids=(step.step_id,),
            graph_ids=(str(context.process_step_id),),
        )

    part_of = _relation_id(
        projection,
        "PART_OF",
        ("Chamber", step.chamber_id),
        ("Equipment", step.equipment_id),
    )
    if part_of is None:
        _add(
            mismatches,
            PART_OF_MISSING,
            step,
            postgres_ids=(step.chamber_id, step.equipment_id),
        )
    else:
        relation_ids[step.chamber_id].append(part_of)

    performs = _relation_id(
        projection,
        "PERFORMS",
        ("Equipment", step.equipment_id),
        ("ProcessStep", step.step_id),
    )
    if performs is None:
        _add(
            mismatches,
            PERFORMS_MISSING,
            step,
            postgres_ids=(step.equipment_id, step.step_id),
        )
    else:
        relation_ids[step.chamber_id].append(performs)


def _check_adjacency(
    first: RouteStep,
    second: RouteStep,
    compact: dict[str, Any],
    full: dict[str, Any],
    relation_ids: dict[str, list[str]],
    mismatches: list[RouteMismatch],
) -> None:
    """연속 step을 **양방향**으로 확인한다.

    한 집합으로 합치면 역방향 graph(`B→A`만 존재)도 통과한다. `NEXT_STEP`의 판정과
    relation ID 소유는 **from step chamber의 projection 한 곳**으로 고정한다 — to
    chamber projection에도 같은 edge가 오므로 정하지 않으면 provenance가 중복된다.
    """

    from_context = compact[first.chamber_id]
    to_context = compact[second.chamber_id]

    if second.step_id not in from_context.downstream_process_step_ids:
        _add(
            mismatches,
            DOWNSTREAM_MISSING,
            first,
            to_step=second,
            postgres_ids=(first.step_id, second.step_id),
            graph_ids=tuple(from_context.downstream_process_step_ids),
        )
    if first.step_id not in to_context.upstream_process_step_ids:
        _add(
            mismatches,
            UPSTREAM_MISSING,
            first,
            to_step=second,
            postgres_ids=(first.step_id, second.step_id),
            graph_ids=tuple(to_context.upstream_process_step_ids),
        )

    next_step = _relation_id(
        full[first.chamber_id],
        "NEXT_STEP",
        ("ProcessStep", first.step_id),
        ("ProcessStep", second.step_id),
    )
    if next_step is None:
        _add(
            mismatches,
            NEXT_STEP_MISSING,
            first,
            to_step=second,
            postgres_ids=(first.step_id, second.step_id),
        )
    else:
        relation_ids[first.chamber_id].append(next_step)
