"""HITL 승인 결정·중단 재개 경계 (`V5-C-3.3`).

업무 상태 변경은 짧은 ``TransactionFactory`` UoW가 소유하고, 같은 thread의 graph
재개 직렬화는 별도 AUTOCOMMIT session advisory lock이 소유한다. 외부 email·graph
invoke 동안 업무 transaction이나 row lock을 잡지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.agent.checkpoint import build_thread_config, normalize_thread_id
from app.agent.rehydration import (
    REHYDRATION_AUDIT_KEY,
    REHYDRATION_SNAPSHOT_SCHEMA,
    RehydrationError,
    build_rehydrated_state,
    canonical_payload,
)
from app.agent.repository import (
    AgentRepositoryError,
    ApprovalDecisionRow,
    RepositoryConflict,
    decide_approval,
    get_agent_run,
    get_approval_request,
    list_action_deliveries,
    merge_run_action_provenance,
    resume_from_approval,
)
from app.agent.state import DeliveryPlan
from app.agent.tools import TransactionFactory
from app.common.enums import ApprovalStatus, Decision, RunStatus

# 저장소의 기존 two-int namespace `VCM2`(0x56434D32)·`BFSB`(0x42465342)와
# 겹치지 않는다. incident lock은 단일 bigint signature라 key space도 다르다.
RESUME_LOCK_NAMESPACE: Final = 0x5643_3333  # "VC33"
_TRY_RESUME_LOCK = text("SELECT pg_try_advisory_lock(:namespace, :key) AS acquired")
_UNLOCK_RESUME = text("SELECT pg_advisory_unlock(:namespace, :key) AS released")
logger = logging.getLogger(__name__)


class ResumeLockConnectionFactory(Protocol):
    """호출마다 pool에 반납 가능한 새 SQLAlchemy connection context를 돌려준다."""

    def __call__(self) -> Any: ...


class HitlError(RuntimeError):
    """raw driver·transport 문자열을 노출하지 않는 HITL 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EmailTransportError(HitlError):
    """C-4.3 email adapter가 실패를 저장한 뒤 올리는 typed nonterminal 오류."""

    def __init__(self, code: str = "EMAIL_TRANSPORT_ERROR") -> None:
        super().__init__(code)


class HitlDeliveryError(HitlError):
    """approval email node를 재시도 가능 checkpoint에 남기는 sanitized 오류."""


class HitlResumeError(HitlError):
    """승인 재개 상태·mutex 계약 위반."""


def decision_port(
    transactions: TransactionFactory,
) -> Callable[[str, Decision, str, str | None], ApprovalDecisionRow]:
    """public adapter가 내부 ``Decision``으로 변환한 뒤 호출할 결정 UoW."""

    def decide(
        approval_id: str,
        decision: Decision,
        decided_by: str,
        decision_comment: str | None = None,
    ) -> ApprovalDecisionRow:
        with transactions() as connection:
            return decide_approval(
                connection,
                approval_id=approval_id,
                decision=decision,
                decided_by=decided_by,
                decision_comment=decision_comment,
            )

    return decide


def hitl_decision_port(
    transactions: TransactionFactory,
) -> Callable[[str], Decision]:
    """checkpoint 재개 node가 DB의 terminal 승인만 읽게 하는 순수-read port.

    checkpoint는 node 성공 뒤에 전진하므로 실행 중 crash 시 재호출될 수 있다. 이 port는
    DB 쓰기나 외부 효과를 절대 추가하지 않는다.
    """

    def read(approval_id: str) -> Decision:
        with transactions() as connection:
            approval = get_approval_request(connection, approval_id)
        if approval.status is ApprovalStatus.PENDING:
            raise RepositoryConflict("APPROVAL_STILL_PENDING")
        if approval.status is ApprovalStatus.APPROVED:
            return Decision.APPROVE
        if approval.status is ApprovalStatus.REJECTED:
            return Decision.REJECT
        raise RepositoryConflict("APPROVAL_NOT_RESUMABLE")

    return read


def cancel_mes_port(
    transactions: TransactionFactory,
) -> Callable[[str], tuple[DeliveryPlan, ...]]:
    """반려 UoW가 만든 delivery DB 정본을 graph State로 projection한다."""

    def read(action_id: str) -> tuple[DeliveryPlan, ...]:
        with transactions() as connection:
            deliveries = list_action_deliveries(connection, action_id)
        return tuple(
            DeliveryPlan(channel=item.channel, status=item.status)
            for item in deliveries
        )

    return read


def approval_email_port(
    transactions: TransactionFactory,
    sender: Callable[[str, str], None],
) -> Callable[[str, str, str], None]:
    """terminal 결정 뒤의 늦은 승인요청 email을 보내지 않는다.

    상태 조회 transaction은 sender 호출 전에 끝난다. 네트워크 I/O 동안 DB lock을
    보유하지 않는다. EMAIL 외부효과 멱등성 자체는 C-4.3~4.6이 완성한다.
    """

    def send(agent_run_id: str, action_id: str, approval_id: str) -> None:
        with transactions() as connection:
            approval = get_approval_request(connection, approval_id)
        if approval.agent_run_id != agent_run_id or approval.action_id != action_id:
            raise RepositoryConflict("APPROVAL_IDENTITY_MISMATCH")
        if approval.status is not ApprovalStatus.PENDING:
            return
        sender(action_id, approval_id)

    return send


def is_approval_interrupted(graph: Any, thread_id: str) -> bool:
    """approval_id 존재만이 아니라 durable checkpoint phase까지 확인한다."""

    snapshot = graph.get_state(build_thread_config(thread_id))
    values = getattr(snapshot, "values", {}) or {}
    next_nodes = tuple(getattr(snapshot, "next", ()) or ())
    return (
        "hitl_interrupt" in next_nodes
        and isinstance(values.get("approval_id"), str)
        and bool(values["approval_id"].strip())
        and values.get("terminal_error") is None
    )


def _resume_lock_key(thread_id: str) -> int:
    """UUID를 32bit key로 축약한다.

    충돌은 서로 다른 thread를 불필요하게 직렬화할 뿐 동시 실행을 허용하지 않는다.
    즉 드문 충돌도 안전한 과다 차단 방향이다.
    """

    canonical = normalize_thread_id(thread_id)
    raw = int.from_bytes(hashlib.sha256(canonical.encode("ascii")).digest()[:4], "big")
    return raw if raw < 2**31 else raw - 2**32


def _invalidate(connection: Any) -> None:
    """session lock을 확실히 버린다. close만으로 pool에 돌려보내지 않는다."""

    invalidator = getattr(connection, "invalidate", None)
    if callable(invalidator):
        try:
            invalidator()
            return
        except Exception:  # noqa: BLE001 - 아래 물리 분리 수단으로 내려간다
            pass
    detach = getattr(connection, "detach", None)
    if callable(detach):
        try:
            detach()
            return
        except Exception:  # noqa: BLE001 - sanitized 오류만 반환한다
            pass
    raise HitlResumeError("RESUME_CONNECTION_DISCARD_FAILED")


@contextmanager
def _resume_mutex(
    factory: ResumeLockConnectionFactory,
    thread_id: str,
) -> Iterator[Connection]:
    """같은 thread의 invoke를 session advisory lock으로 최대 한 실행자에게 준다."""

    key = _resume_lock_key(thread_id)
    with factory() as raw:
        try:
            connection = raw.execution_options(isolation_level="AUTOCOMMIT")
            acquired = bool(
                connection.execute(
                    _TRY_RESUME_LOCK,
                    {"namespace": RESUME_LOCK_NAMESPACE, "key": key},
                ).scalar_one()
            )
        except Exception as exc:
            # 응답이 유실됐다면 server는 session lock을 잡았을 수 있다. 불확실한
            # session을 pool로 돌려보내지 않는다.
            try:
                _invalidate(raw)
            except HitlResumeError as discard_error:
                raise discard_error from exc
            raise HitlResumeError("RESUME_LOCK_UNAVAILABLE") from exc
        if not acquired:
            raise HitlResumeError("RESUME_ALREADY_RUNNING")
        body_error: BaseException | None = None
        try:
            yield connection
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            try:
                released = bool(
                    connection.execute(
                        _UNLOCK_RESUME,
                        {"namespace": RESUME_LOCK_NAMESPACE, "key": key},
                    ).scalar_one()
                )
            except Exception as exc:
                try:
                    _invalidate(connection)
                except HitlResumeError:
                    if body_error is None:
                        raise
                    logger.error(
                        "resume lock connection discard failed after body error"
                    )
                if body_error is None:
                    raise HitlResumeError("RESUME_LOCK_RELEASE_UNCERTAIN") from exc
            else:
                if not released:
                    try:
                        _invalidate(connection)
                    except HitlResumeError:
                        if body_error is None:
                            raise
                        logger.error(
                            "resume lock connection discard failed after body error"
                        )
                    if body_error is None:
                        raise HitlResumeError("RESUME_LOCK_RELEASE_UNCERTAIN")


def _checkpoint(
    graph: Any, thread_id: str
) -> tuple[Any, dict[str, Any], tuple[str, ...]]:
    config = build_thread_config(thread_id)
    snapshot = graph.get_state(config)
    values = dict(getattr(snapshot, "values", {}) or {})
    next_nodes = tuple(getattr(snapshot, "next", ()) or ())
    if not values:
        raise HitlResumeError("CHECKPOINT_MISSING")
    if not next_nodes:
        raise HitlResumeError("CHECKPOINT_PHASE_INVALID")
    stored_thread = values.get("thread_id")
    if stored_thread != thread_id:
        raise HitlResumeError("CHECKPOINT_THREAD_MISMATCH")
    return config, values, next_nodes


def _record_delivery_error(
    transactions: TransactionFactory,
    run_id: str,
    code: str,
) -> None:
    """raw 예외 없이 운영 복구 근거만 보존한다. 원래 실패를 가리지 않는다."""

    try:
        with transactions() as connection:
            merge_run_action_provenance(
                connection,
                run_id,
                terminal_evidence={"hitl_delivery_error": code},
            )
    except AgentRepositoryError as exc:
        logger.error(
            "HITL delivery evidence persistence failed (code=%s)",
            exc.code,
        )
        return


def _record_rehydration_success(
    transactions: TransactionFactory,
    run_id: str,
) -> None:
    """checkpoint postcondition 뒤의 비차단 운영 증적."""

    try:
        with transactions() as connection:
            current = get_agent_run(connection, run_id)
            merge_run_action_provenance(
                connection,
                run_id,
                terminal_evidence={
                    REHYDRATION_AUDIT_KEY: _append_rehydration_audit(current.evidence)
                },
            )
    except AgentRepositoryError as exc:
        logger.error(
            "HITL rehydration evidence persistence failed (code=%s)",
            exc.code,
        )
    except Exception:  # noqa: BLE001 - best-effort 감사가 복구 성공을 뒤집지 않는다
        logger.error("HITL rehydration evidence persistence unavailable")


def _append_rehydration_audit(
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """같은 run을 여러 번 복구해도 최초 시각을 잃지 않는 append-only projection."""

    raw = None if evidence is None else evidence.get(REHYDRATION_AUDIT_KEY)
    events: list[dict[str, str]] = []
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid rehydration audit")
        raw_events = raw.get("events")
        legacy_timestamp = raw.get("rehydrated_at")
        if raw.get("schema_version") != REHYDRATION_SNAPSHOT_SCHEMA:
            raise ValueError("invalid rehydration audit schema")
        if raw_events is None and isinstance(legacy_timestamp, str):
            events.append({"rehydrated_at": legacy_timestamp})
        elif isinstance(raw_events, list) and all(
            isinstance(item, Mapping) and isinstance(item.get("rehydrated_at"), str)
            for item in raw_events
        ):
            events.extend(
                {"rehydrated_at": str(item["rehydrated_at"])} for item in raw_events
            )
        else:
            raise ValueError("invalid rehydration audit events")
    events.append({"rehydrated_at": datetime.now(UTC).isoformat()})
    return {
        "schema_version": REHYDRATION_SNAPSHOT_SCHEMA,
        "events": events,
    }


def _invoke(
    graph: Any,
    config: dict[str, Any],
    transactions: TransactionFactory,
    run_id: str,
) -> Any:
    try:
        return graph.invoke(None, config=config)
    except HitlDeliveryError as exc:
        _record_delivery_error(transactions, run_id, exc.code)
        raise


def _assert_run_identity(
    transactions: TransactionFactory,
    *,
    run_id: str,
    values: dict[str, Any],
) -> Any:
    if values.get("run_id") != run_id:
        raise HitlResumeError("CHECKPOINT_RUN_MISMATCH")
    with transactions() as connection:
        run = get_agent_run(connection, run_id)
    if run.thread_id != values.get("thread_id"):
        raise HitlResumeError("CHECKPOINT_THREAD_MISMATCH")
    if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
        raise HitlResumeError("RUN_NOT_ACTIVE")
    return run


def _resume_locked(
    graph: Any,
    transactions: TransactionFactory,
    thread_id: str,
    *,
    expected_run_id: str | None,
    require_terminal: bool,
) -> Any:
    config, values, next_nodes = _checkpoint(graph, thread_id)
    run_id = values.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise HitlResumeError("CHECKPOINT_RUN_MISSING")
    if expected_run_id is not None and run_id != expected_run_id:
        raise HitlResumeError("CHECKPOINT_RUN_MISMATCH")
    run = _assert_run_identity(transactions, run_id=run_id, values=values)

    phase = next_nodes[0] if len(next_nodes) == 1 else ""
    progressed = False
    if phase in {"persist_action", "approval_email"}:
        _invoke(graph, config, transactions, run_id)
        progressed = True
        config, values, next_nodes = _checkpoint(graph, thread_id)
        phase = next_nodes[0] if len(next_nodes) == 1 else ""
        run = _assert_run_identity(transactions, run_id=run_id, values=values)

    if phase != "hitl_interrupt":
        raise HitlResumeError("CHECKPOINT_PHASE_INVALID")
    approval_id = values.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id.strip():
        raise HitlResumeError("CHECKPOINT_APPROVAL_MISSING")
    with transactions() as connection:
        approval = get_approval_request(connection, approval_id)
    if approval.agent_run_id != run_id:
        raise HitlResumeError("APPROVAL_IDENTITY_MISMATCH")

    if approval.status is ApprovalStatus.PENDING:
        if progressed and not require_terminal:
            return values
        raise HitlResumeError("APPROVAL_STILL_PENDING")
    if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        raise HitlResumeError("APPROVAL_NOT_RESUMABLE")
    if run.status is RunStatus.WAITING_APPROVAL:
        with transactions() as connection:
            resume_from_approval(
                connection,
                agent_run_id=run_id,
                approval_id=approval_id,
            )
    elif run.status is not RunStatus.RUNNING:
        raise HitlResumeError("RUN_NOT_WAITING_APPROVAL")
    # RUNNING + terminal approval + 동일 durable checkpoint는 앞선 소유자가
    # WAITING→RUNNING CAS를 commit한 뒤 invoke 전에 죽은 crash window다. 새로
    # session lock을 얻은 복구자는 CAS를 반복하지 않고 같은 checkpoint를 이어간다.
    return _invoke(graph, config, transactions, run_id)


def _rehydrate_locked(
    graph: Any,
    transactions: TransactionFactory,
    *,
    run_id: str,
    thread_id: str,
) -> None:
    """mutex 안에서 checkpoint를 쓰고 semantic postcondition을 확인한다."""

    config = build_thread_config(thread_id)
    with transactions() as connection:
        try:
            payload = build_rehydrated_state(connection, run_id)
        except RehydrationError as exc:
            raise HitlResumeError(exc.code) from exc
    expected = canonical_payload(payload)

    write_error: Exception | None = None
    try:
        graph.update_state(config, payload, as_node="persist_action")
    except Exception as exc:  # noqa: BLE001 - 응답 유실 여부는 postcheck가 판정한다
        write_error = exc

    try:
        snapshot = graph.get_state(config)
    except Exception as exc:  # noqa: BLE001 - checkpoint driver 원문을 노출하지 않는다
        raise HitlResumeError("REHYDRATE_WRITE_UNCERTAIN") from exc
    values = dict(getattr(snapshot, "values", {}) or {})
    next_nodes = tuple(getattr(snapshot, "next", ()) or ())
    if not values:
        error = HitlResumeError("REHYDRATE_WRITE_UNCERTAIN")
        if write_error is not None:
            raise error from write_error
        raise error
    try:
        verified = (
            values.get("thread_id") == thread_id
            and values.get("run_id") == run_id
            and values.get("approval_id") == payload["approval_id"]
            and values.get("terminal_error") is None
            and next_nodes == ("approval_email",)
            and canonical_payload(values) == expected
        )
    except RehydrationError as exc:
        raise HitlResumeError("REHYDRATE_CHECKPOINT_UNVERIFIED") from exc
    if not verified:
        error = HitlResumeError("REHYDRATE_CHECKPOINT_UNVERIFIED")
        if write_error is not None:
            raise error from write_error
        raise error
    _record_rehydration_success(transactions, run_id)


def resume_after_approval(
    graph: Any,
    transactions: TransactionFactory,
    resume_connections: ResumeLockConnectionFactory,
    thread_id: str,
) -> Any:
    """결정이 끝난 같은 thread를 정확히 한 실행자만 재개한다."""

    canonical = normalize_thread_id(thread_id)
    with _resume_mutex(resume_connections, canonical):
        return _resume_locked(
            graph,
            transactions,
            canonical,
            expected_run_id=None,
            require_terminal=True,
        )


def recover_hitl_run(
    graph: Any,
    transactions: TransactionFactory,
    resume_connections: ResumeLockConnectionFactory,
    run_id: str,
) -> Any:
    """DB↔checkpoint 비원자 seam을 같은 run/thread에서 catch-up한다."""

    with transactions() as connection:
        run = get_agent_run(connection, run_id)
    if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
        raise HitlResumeError("RUN_NOT_ACTIVE")
    canonical = normalize_thread_id(run.thread_id)
    with _resume_mutex(resume_connections, canonical):
        # mutex 대기 중 status·checkpoint가 바뀔 수 있으므로 둘 다 다시 읽는다.
        with transactions() as connection:
            locked_view = get_agent_run(connection, run_id)
        if locked_view.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise HitlResumeError("RUN_NOT_ACTIVE")
        config = build_thread_config(canonical)
        snapshot = graph.get_state(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if values and not next_nodes:
            raise HitlResumeError("CHECKPOINT_PHASE_INVALID")
        if not values:
            if locked_view.status is not RunStatus.WAITING_APPROVAL:
                raise HitlResumeError("REHYDRATE_RUN_NOT_WAITING")
            _rehydrate_locked(
                graph,
                transactions,
                run_id=run_id,
                thread_id=canonical,
            )
        return _resume_locked(
            graph,
            transactions,
            canonical,
            expected_run_id=run_id,
            require_terminal=False,
        )


__all__ = [
    "RESUME_LOCK_NAMESPACE",
    "ResumeLockConnectionFactory",
    "EmailTransportError",
    "HitlDeliveryError",
    "HitlResumeError",
    "decision_port",
    "hitl_decision_port",
    "cancel_mes_port",
    "approval_email_port",
    "is_approval_interrupted",
    "resume_after_approval",
    "recover_hitl_run",
]
