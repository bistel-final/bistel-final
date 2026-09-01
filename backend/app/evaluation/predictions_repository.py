"""C-6.2 Runtime prediction의 read-only repeatable snapshot repository.

합성 label column은 이 모듈의 SQL에 없다. Runtime DB에서는 baseline run·prediction·
같은 run의 저장 근거와 label 제외 ``lot_history`` 공통 key만 읽는다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text

from app.common.schemas import AlarmRef
from app.evaluation.fault_5class import (
    EXPECTED_POPULATION_COUNT,
    FrozenPredictions,
    IncidentFaultLabelRow,
    IncidentKey,
    PredictionRecord,
)

TARGET_DATABASE: Final = "kosa_agent_e2e"
TARGET_ROLE: Final = "kosa_app"
EVALUATION_DATABASE: Final = "kosa_text2sql"
EVALUATION_ROLE: Final = "kosa_evaluation"
EXPECTED_SHARED_KEY_COUNT: Final = 600


class PredictionRepositoryError(RuntimeError):
    """원문 SQL·driver·DSN을 노출하지 않는 안정 오류."""


class PredictionTargetMismatch(PredictionRepositoryError):
    """DB·role·shared-data identity가 고정 계약과 다르다."""


@dataclass(frozen=True, slots=True)
class RuntimeEvaluationSnapshot:
    records: tuple[PredictionRecord, ...]
    shared_key_sha256: str


IDENTITY_SQL: Final = text(
    "SELECT current_database() AS database_name, current_user AS role_name"
)

SHARED_KEYS_SQL: Final = text(
    """
    SELECT lot_hist_id, lot_id, chamber_id
    FROM lot_history
    ORDER BY lot_hist_id, lot_id, chamber_id
    """
)

PREDICTIONS_SQL: Final = text(
    """
    SELECT
        run.agent_run_id,
        run.lot_id,
        run.chamber_id,
        run.action,
        run.retry_of_run_id,
        run.evidence AS run_evidence,
        prediction.predicted_fault_code,
        prediction.evidence AS prediction_evidence,
        prediction.llm_model,
        prediction.prompt_version,
        COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'source', alarm.alarm_source,
                    'alarm_id', alarm.alarm_id
                ) ORDER BY alarm.alarm_source, alarm.alarm_id
            )
            FROM agent_run_alarm AS alarm
            WHERE alarm.agent_run_id = run.agent_run_id
        ), '[]'::jsonb) AS alarms,
        COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'tool_name', tool.tool_name,
                    'status', tool.status,
                    'output', tool.output
                ) ORDER BY tool.call_seq
            )
            FROM agent_tool_call AS tool
            WHERE tool.agent_run_id = run.agent_run_id
        ), '[]'::jsonb) AS tools
    FROM agent_run AS run
    LEFT JOIN agent_prediction AS prediction
      ON prediction.agent_run_id = run.agent_run_id
    WHERE run.agent_run_id = ANY(:run_ids)
    ORDER BY run.lot_id, run.chamber_id, run.agent_run_id
    """
)


def _mapping(value: object, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PredictionRepositoryError(reason)
    return value


def _sequence(value: object, reason: str) -> list[Any]:
    if not isinstance(value, list):
        raise PredictionRepositoryError(reason)
    return value


def shared_key_sha256(connection: Any) -> str:
    """label 제외 600개 key의 canonical hash를 한 DB snapshot에서 계산한다."""

    rows = connection.execute(SHARED_KEYS_SQL).mappings().all()
    if len(rows) != EXPECTED_SHARED_KEY_COUNT:
        raise PredictionTargetMismatch("TARGET_MISMATCH")
    payload = [
        {
            "lot_hist_id": row["lot_hist_id"],
            "lot_id": row["lot_id"],
            "chamber_id": row["chamber_id"],
        }
        for row in rows
    ]
    raw = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def assert_identity(
    connection: Any,
    *,
    database: str,
    role: str,
) -> None:
    identity = connection.execute(IDENTITY_SQL).one()
    if identity.database_name != database or identity.role_name != role:
        raise PredictionTargetMismatch("TARGET_MISMATCH")


def _alarm_token(raw: object) -> str:
    try:
        return AlarmRef.model_validate(raw).to_token()
    except (TypeError, ValueError) as exc:
        raise PredictionRepositoryError("PREDICTION_EVIDENCE_INVALID") from exc


def _string_tuple(raw: object, reason: str) -> tuple[str, ...]:
    values = _sequence(raw, reason)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise PredictionRepositoryError(reason)
    rendered = tuple(values)
    if len(rendered) != len(set(rendered)):
        raise PredictionRepositoryError(reason)
    return rendered


def _available_chunks(raw_tools: object) -> tuple[str, ...]:
    chunks: list[str] = []
    for raw in _sequence(raw_tools, "TOOL_EVIDENCE_INVALID"):
        tool = _mapping(raw, "TOOL_EVIDENCE_INVALID")
        if (
            tool.get("tool_name") != "search_documents"
            or tool.get("status") != "SUCCESS"
        ):
            continue
        output = tool.get("output")
        if not isinstance(output, dict):
            raise PredictionRepositoryError("TOOL_EVIDENCE_INVALID")
        for raw_hit in _sequence(output.get("hits", []), "TOOL_EVIDENCE_INVALID"):
            hit = _mapping(raw_hit, "TOOL_EVIDENCE_INVALID")
            chunk_id = hit.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                raise PredictionRepositoryError("TOOL_EVIDENCE_INVALID")
            chunks.append(chunk_id)
    return tuple(dict.fromkeys(chunks))


def _available_relations(run_evidence: object) -> tuple[str, ...]:
    if not isinstance(run_evidence, dict):
        return ()
    snapshot = run_evidence.get("rehydration_snapshot")
    if not isinstance(snapshot, dict):
        return ()
    if snapshot.get("schema_version") != "rehydration-snapshot-v1":
        raise PredictionRepositoryError("RUN_EVIDENCE_INVALID")
    route = snapshot.get("route")
    if not isinstance(route, dict):
        raise PredictionRepositoryError("RUN_EVIDENCE_INVALID")
    graph_evidence = _sequence(route.get("graph_evidence", []), "RUN_EVIDENCE_INVALID")
    relations: list[str] = []
    for raw_item in graph_evidence:
        item = _mapping(raw_item, "RUN_EVIDENCE_INVALID")
        for relation_id in _sequence(
            item.get("relation_ids", []), "RUN_EVIDENCE_INVALID"
        ):
            if not isinstance(relation_id, str) or not relation_id.strip():
                raise PredictionRepositoryError("RUN_EVIDENCE_INVALID")
            relations.append(relation_id)
    return tuple(dict.fromkeys(relations))


def _policy_version(run_evidence: object) -> str | None:
    if not isinstance(run_evidence, dict):
        return None
    provenance = run_evidence.get("action_provenance")
    if not isinstance(provenance, dict):
        return None
    if provenance.get("schema") != "action-provenance-v1":
        raise PredictionRepositoryError("RUN_EVIDENCE_INVALID")
    value = provenance.get("action_policy_version")
    return value if isinstance(value, str) and value.strip() else None


def _prediction_record(row: Any) -> PredictionRecord:
    raw_prediction = row.predicted_fault_code
    if raw_prediction is None:
        supporting_alarms: tuple[str, ...] = ()
        supporting_chunks: tuple[str, ...] = ()
        supporting_relations: tuple[str, ...] = ()
    else:
        evidence = _mapping(row.prediction_evidence, "PREDICTION_EVIDENCE_INVALID")
        if evidence.get("schema_version") not in {
            "agent-evidence-v1",
            "agent-evidence-v2",
        }:
            raise PredictionRepositoryError("PREDICTION_EVIDENCE_INVALID")
        supporting_alarms = tuple(
            _alarm_token(item)
            for item in _sequence(
                evidence.get("supporting_alarms", []),
                "PREDICTION_EVIDENCE_INVALID",
            )
        )
        supporting_chunks = _string_tuple(
            evidence.get("supporting_chunk_ids", []),
            "PREDICTION_EVIDENCE_INVALID",
        )
        supporting_relations = _string_tuple(
            evidence.get("supporting_relation_ids", []),
            "PREDICTION_EVIDENCE_INVALID",
        )
    available_alarms = tuple(
        _alarm_token(item) for item in _sequence(row.alarms, "RUN_ALARMS_INVALID")
    )
    return PredictionRecord(
        incident=IncidentKey(row.lot_id, row.chamber_id),
        agent_run_id=row.agent_run_id,
        predicted_fault_code=raw_prediction,
        supporting_alarm_tokens=supporting_alarms,
        supporting_chunk_ids=supporting_chunks,
        supporting_relation_ids=supporting_relations,
        available_alarm_tokens=available_alarms,
        available_chunk_ids=_available_chunks(row.tools),
        available_relation_ids=_available_relations(row.run_evidence),
        actual_action=row.action,
        model_version=row.llm_model,
        prompt_version=row.prompt_version,
        policy_version=_policy_version(row.run_evidence),
    )


def read_runtime_evaluation_snapshot(
    engine: Any,
    *,
    database: str,
    run_ids: tuple[str, ...],
) -> RuntimeEvaluationSnapshot:
    """identity → shared keys → exact baseline prediction을 한 snapshot에서 읽는다."""

    if (
        database != TARGET_DATABASE
        or len(run_ids) != EXPECTED_POPULATION_COUNT
        or len(set(run_ids)) != EXPECTED_POPULATION_COUNT
    ):
        raise PredictionTargetMismatch("TARGET_MISMATCH")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            assert_identity(connection, database=TARGET_DATABASE, role=TARGET_ROLE)
            key_hash = shared_key_sha256(connection)
            rows = connection.execute(PREDICTIONS_SQL, {"run_ids": list(run_ids)}).all()
            if len(rows) != EXPECTED_POPULATION_COUNT or {
                row.agent_run_id for row in rows
            } != set(run_ids):
                raise PredictionTargetMismatch("TARGET_MISMATCH")
            if any(row.retry_of_run_id is not None for row in rows):
                raise PredictionTargetMismatch("TARGET_MISMATCH")
            records = tuple(_prediction_record(row) for row in rows)
            return RuntimeEvaluationSnapshot(records, key_hash)
    except PredictionRepositoryError:
        raise
    except Exception as exc:  # noqa: BLE001 - driver/DSN을 외부로 노출하지 않는다.
        raise PredictionRepositoryError("PREDICTION_SNAPSHOT_FAILED") from exc


def read_evaluation_label_snapshot(
    engine: Any,
    *,
    frozen: FrozenPredictions,
    expected_shared_key_sha256: str,
    label_loader: Callable[[Any, Sequence[tuple[str, str]]], Sequence[Any]],
) -> tuple[IncidentFaultLabelRow, ...]:
    """freeze 이후 identity·공통 key 정렬을 통과해야만 label callback을 부른다."""

    if (
        len(frozen.records) != EXPECTED_POPULATION_COUNT
        or len(expected_shared_key_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_shared_key_sha256)
    ):
        raise PredictionTargetMismatch("TARGET_MISMATCH")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            assert_identity(
                connection,
                database=EVALUATION_DATABASE,
                role=EVALUATION_ROLE,
            )
            if shared_key_sha256(connection) != expected_shared_key_sha256:
                raise PredictionTargetMismatch("TARGET_MISMATCH")
            raw_rows = label_loader(
                connection,
                [
                    (item.incident.lot_id, item.incident.chamber_id)
                    for item in frozen.records
                ],
            )
            return tuple(
                IncidentFaultLabelRow(
                    incident=IncidentKey(row.lot_id, row.chamber_id),
                    fault_code=row.fault_code,
                )
                for row in raw_rows
            )
    except PredictionRepositoryError:
        raise
    except Exception as exc:  # noqa: BLE001 - driver/DSN을 외부로 노출하지 않는다.
        raise PredictionRepositoryError("LABEL_SNAPSHOT_FAILED") from exc


__all__ = [
    "EVALUATION_DATABASE",
    "EVALUATION_ROLE",
    "IDENTITY_SQL",
    "PREDICTIONS_SQL",
    "PredictionRepositoryError",
    "PredictionTargetMismatch",
    "RuntimeEvaluationSnapshot",
    "SHARED_KEYS_SQL",
    "TARGET_DATABASE",
    "TARGET_ROLE",
    "assert_identity",
    "read_evaluation_label_snapshot",
    "read_runtime_evaluation_snapshot",
    "shared_key_sha256",
]
