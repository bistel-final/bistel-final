"""Golden-flow 검증용 단일 read-only repeatable snapshot repository."""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import text

from app.agent.golden_flow import GoldenFlowSnapshot, snapshot_from_mapping

TARGET_DATABASE: Final = "kosa_agent_e2e"
TARGET_ROLE: Final = "kosa_app"


class GoldenFlowRepositoryError(RuntimeError):
    """Driver·SQL·DSN을 노출하지 않는 안정 오류."""


class GoldenFlowTargetMismatch(GoldenFlowRepositoryError):
    """검증 대상 DB 또는 role이 고정 계약과 다르다."""


IDENTITY_SQL: Final = text(
    "SELECT current_database() AS database_name, current_user AS role_name"
)

# phase별 historical DB_SNAPSHOT도 이 query 결과와 같은 exact JSON 구조를 쓴다.
# 한 statement로 읽어 statement 간 snapshot 해석 차이를 없애고, 모든 하위 SELECT는
# explicit REPEATABLE READ READ ONLY transaction 안에서 실행된다.
GOLDEN_SNAPSHOT_SQL: Final = text(
    """
    SELECT jsonb_build_object(
        'runs', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'agent_run_id', r.agent_run_id,
                'lot_id', r.lot_id,
                'chamber_id', r.chamber_id,
                'status', r.status,
                'autonomy_level', r.autonomy_level,
                'action', r.action,
                'retry_of_run_id', r.retry_of_run_id,
                'latency_ms', r.latency_ms,
                'input_tokens', r.input_tokens,
                'output_tokens', r.output_tokens,
                'rehydration_snapshot_bytes', CASE
                    WHEN r.evidence ? 'rehydration_snapshot'
                    THEN pg_column_size(r.evidence -> 'rehydration_snapshot')
                    ELSE NULL
                END
            ) ORDER BY r.started_at, r.agent_run_id)
            FROM agent_run AS r
        ), '[]'::jsonb),
        'actions', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'agent_run_id', link.agent_run_id,
                'action_id', link.action_id,
                'link_role', link.link_role,
                'lot_id', link.lot_id,
                'chamber_id', link.chamber_id,
                'action_code', action.action_code
            ) ORDER BY link.linked_at, link.agent_run_id)
            FROM agent_run_action AS link
            JOIN action_history AS action ON action.action_id = link.action_id
        ), '[]'::jsonb),
        'approvals', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'approval_id', approval.approval_id,
                'action_id', approval.action_id,
                'agent_run_id', approval.agent_run_id,
                'status', approval.status
            ) ORDER BY approval.requested_at, approval.approval_id)
            FROM approval_request AS approval
        ), '[]'::jsonb),
        'deliveries', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'action_id', delivery.action_id,
                'channel', delivery.channel,
                'status', delivery.status,
                'attempt_count', delivery.attempt_count
            ) ORDER BY delivery.action_id, delivery.channel)
            FROM action_delivery AS delivery
        ), '[]'::jsonb),
        'tools', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'agent_run_id', tool.agent_run_id,
                'tool_name', tool.tool_name,
                'status', tool.status
            ) ORDER BY tool.agent_run_id, tool.call_seq)
            FROM agent_tool_call AS tool
        ), '[]'::jsonb),
        'audits', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'event_type', audit.event_type,
                'entity_id', audit.entity_id,
                'channel', audit.after_json ->> 'channel'
            ) ORDER BY audit.audit_id)
            FROM audit_log AS audit
        ), '[]'::jsonb),
        'r03_incidents', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'lot_id', incident.lot_id,
                'chamber_id', incident.chamber_id
            ) ORDER BY incident.lot_id, incident.chamber_id)
            FROM (
                SELECT DISTINCT lot_id, chamber_id
                FROM r03_alarm_history
            ) AS incident
        ), '[]'::jsonb)
    ) AS snapshot
    """
)


def read_golden_flow_snapshot(
    engine: Any,
    *,
    database: str,
) -> GoldenFlowSnapshot:
    """BEGIN/SET → identity → business 순서를 고정해 snapshot을 읽는다."""

    if database != TARGET_DATABASE:
        raise GoldenFlowTargetMismatch("TARGET_MISMATCH")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            identity = connection.execute(IDENTITY_SQL).one()
            if (
                identity.database_name != TARGET_DATABASE
                or identity.role_name != TARGET_ROLE
            ):
                raise GoldenFlowTargetMismatch("TARGET_MISMATCH")
            row = connection.execute(GOLDEN_SNAPSHOT_SQL).one()
            return snapshot_from_mapping(row.snapshot)
    except GoldenFlowTargetMismatch:
        raise
    except Exception as exc:  # noqa: BLE001 - 원문·DSN을 상위로 흘리지 않는다.
        raise GoldenFlowRepositoryError("SNAPSHOT_READ_FAILED") from exc


__all__ = [
    "GOLDEN_SNAPSHOT_SQL",
    "GoldenFlowRepositoryError",
    "GoldenFlowTargetMismatch",
    "IDENTITY_SQL",
    "TARGET_DATABASE",
    "TARGET_ROLE",
    "read_golden_flow_snapshot",
]
