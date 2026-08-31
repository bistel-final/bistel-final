"""V5-C-6.2 Runtime/Evaluation 물리 격리 PostgreSQL 회귀."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import rehearsal_postgres as postgres  # noqa: E402

from app.agent.golden_flow import load_expected_oracle  # noqa: E402
from app.detection.evaluation_loader import (  # noqa: E402
    fetch_incident_fault_labels,
)
from app.evaluation.fault_5class import (  # noqa: E402
    EXPECTED_CLASS_SUPPORT,
    IncidentKey,
    classify_incident_labels,
    freeze_predictions,
)
from app.evaluation.predictions_repository import (  # noqa: E402
    PredictionTargetMismatch,
    read_evaluation_label_snapshot,
    read_runtime_evaluation_snapshot,
)

pytestmark = pytest.mark.container

ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"
APP_PASSWORD = "container-app-password"
EVALUATION_PASSWORD = "container-evaluation-password"
FAULTS = ("FOC", "FOC", "RFM", "MFD", "TMD", "OTH", "OTH")


def _shared_rows() -> list[tuple[str, str, str, str]]:
    oracle = load_expected_oracle(json.loads(ORACLE_PATH.read_text(encoding="utf-8")))
    rows: list[tuple[str, str, str, str]] = []
    for index, incident in enumerate(oracle.incidents):
        rows.append(
            (
                f"LH-{index:03d}-A",
                incident.lot_id,
                incident.chamber_id,
                "NRM",
            )
        )
        rows.append(
            (
                f"LH-{index:03d}-B",
                incident.lot_id,
                incident.chamber_id,
                FAULTS[index] if index < len(FAULTS) else "NRM",
            )
        )
    for index in range(600 - len(rows)):
        rows.append(
            (
                f"LH-FILL-{index:04d}",
                f"LOT-FILL-{index:04d}",
                f"CH-FILL-{index:04d}",
                "NRM",
            )
        )
    assert len(rows) == 600
    return rows


def _create_runtime(connection: Any, shared: list[tuple[str, str, str, str]]) -> None:
    connection.execute(
        """
        CREATE TABLE lot_history (
            lot_hist_id text PRIMARY KEY,
            lot_id text NOT NULL,
            chamber_id text NOT NULL
        );
        CREATE TABLE agent_run (
            agent_run_id text PRIMARY KEY,
            lot_id text NOT NULL,
            chamber_id text NOT NULL,
            action text,
            retry_of_run_id text,
            evidence jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE TABLE agent_prediction (
            agent_run_id text PRIMARY KEY REFERENCES agent_run(agent_run_id),
            predicted_fault_code text NOT NULL,
            evidence jsonb NOT NULL,
            llm_model text NOT NULL,
            prompt_version text NOT NULL
        );
        CREATE TABLE agent_run_alarm (
            agent_run_id text NOT NULL REFERENCES agent_run(agent_run_id),
            alarm_source text NOT NULL,
            alarm_id text NOT NULL,
            PRIMARY KEY (agent_run_id, alarm_source, alarm_id)
        );
        CREATE TABLE agent_tool_call (
            agent_run_id text NOT NULL REFERENCES agent_run(agent_run_id),
            call_seq integer NOT NULL,
            tool_name text NOT NULL,
            status text NOT NULL,
            output jsonb,
            PRIMARY KEY (agent_run_id, call_seq)
        );
        """
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO lot_history "
            "(lot_hist_id, lot_id, chamber_id) VALUES (%s,%s,%s)",
            [
                (lot_hist_id, lot_id, chamber_id)
                for lot_hist_id, lot_id, chamber_id, _ in shared
            ],
        )
    oracle = load_expected_oracle(json.loads(ORACLE_PATH.read_text(encoding="utf-8")))
    for index, incident in enumerate(oracle.incidents):
        run_id = f"RUN-{index:04d}"
        source = incident.alarm_sources[0]
        alarm_id = f"ALARM-{index:04d}"
        connection.execute(
            "INSERT INTO agent_run "
            "(agent_run_id, lot_id, chamber_id, action, evidence) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                run_id,
                incident.lot_id,
                incident.chamber_id,
                incident.expected_action,
                json.dumps(
                    {
                        "action_provenance": {
                            "schema": "action-provenance-v1",
                            "action_policy_version": "ACTION-POLICY-V1",
                        }
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO agent_run_alarm "
            "(agent_run_id, alarm_source, alarm_id) VALUES (%s,%s,%s)",
            (run_id, source, alarm_id),
        )
        # 한 run의 prediction 행을 의도적으로 생략해 LEFT JOIN 보존을 실증한다.
        if index == 2:
            continue
        predicted = FAULTS[index] if index < len(FAULTS) else "FOC"
        connection.execute(
            "INSERT INTO agent_prediction "
            "(agent_run_id, predicted_fault_code, evidence, llm_model, prompt_version) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                run_id,
                predicted,
                json.dumps(
                    {
                        "schema_version": "agent-evidence-v1",
                        "supporting_alarms": [{"source": source, "alarm_id": alarm_id}],
                        "supporting_chunk_ids": [],
                        "supporting_relation_ids": [],
                    }
                ),
                "model-v1",
                "agent-hypothesis-v1",
            ),
        )


def _create_evaluation(
    connection: Any, shared: list[tuple[str, str, str, str]]
) -> None:
    connection.execute(
        """
        CREATE TABLE lot_history (
            lot_hist_id text PRIMARY KEY,
            lot_id text NOT NULL,
            chamber_id text NOT NULL,
            fault_code text NOT NULL
        )
        """
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO lot_history "
            "(lot_hist_id, lot_id, chamber_id, fault_code) VALUES (%s,%s,%s,%s)",
            shared,
        )


def _url(endpoint: Any, database: str, role: str, password: str) -> str:
    return (
        f"postgresql+psycopg://{role}:{password}@{endpoint.host}:"
        f"{endpoint.port}/{database}"
    )


@contextmanager
def _databases() -> Iterator[tuple[Any, Any, Any]]:
    with postgres.one_off_postgres(database="kosa_agent_e2e") as endpoint:
        shared = _shared_rows()
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname="postgres",
            user=endpoint.username,
            password=endpoint.password,
            autocommit=True,
        ) as admin:
            admin.execute(f"CREATE ROLE kosa_app LOGIN PASSWORD '{APP_PASSWORD}'")
            admin.execute(
                "CREATE ROLE kosa_evaluation LOGIN PASSWORD " f"'{EVALUATION_PASSWORD}'"
            )
            admin.execute("CREATE DATABASE kosa_text2sql")
            admin.execute("GRANT CONNECT ON DATABASE kosa_agent_e2e TO kosa_app")
            admin.execute("GRANT CONNECT ON DATABASE kosa_text2sql TO kosa_evaluation")

        for database, creator, role in (
            ("kosa_agent_e2e", _create_runtime, "kosa_app"),
            ("kosa_text2sql", _create_evaluation, "kosa_evaluation"),
        ):
            with psycopg.connect(
                host=endpoint.host,
                port=endpoint.port,
                dbname=database,
                user=endpoint.username,
                password=endpoint.password,
            ) as connection:
                creator(connection, shared)
                connection.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
                connection.execute(
                    f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}"
                )
                connection.commit()

        runtime_engine = create_engine(
            _url(endpoint, "kosa_agent_e2e", "kosa_app", APP_PASSWORD)
        )
        evaluation_engine = create_engine(
            _url(
                endpoint,
                "kosa_text2sql",
                "kosa_evaluation",
                EVALUATION_PASSWORD,
            )
        )
        admin_engine = create_engine(
            _url(endpoint, "kosa_text2sql", endpoint.username, endpoint.password)
        )
        try:
            yield runtime_engine, evaluation_engine, admin_engine
        finally:
            runtime_engine.dispose()
            evaluation_engine.dispose()
            admin_engine.dispose()


def test_two_database_identity_left_join_and_whole_member_labels() -> None:
    oracle = load_expected_oracle(json.loads(ORACLE_PATH.read_text(encoding="utf-8")))
    run_ids = tuple(f"RUN-{index:04d}" for index in range(12))
    with _databases() as (runtime_engine, evaluation_engine, admin_engine):
        runtime = read_runtime_evaluation_snapshot(
            runtime_engine,
            database="kosa_agent_e2e",
            run_ids=run_ids,
        )
        assert len(runtime.records) == 12
        assert sum(item.predicted_fault_code is None for item in runtime.records) == 1
        frozen = freeze_predictions(runtime.records)
        labels = read_evaluation_label_snapshot(
            evaluation_engine,
            frozen=frozen,
            expected_shared_key_sha256=runtime.shared_key_sha256,
            label_loader=fetch_incident_fault_labels,
        )
        classified = classify_incident_labels(
            tuple(
                IncidentKey(item.lot_id, item.chamber_id) for item in oracle.incidents
            ),
            labels,
        )
        assert sum(item.fault_code is not None for item in classified) == 7
        assert {
            fault: sum(item.fault_code == fault for item in classified)
            for fault in EXPECTED_CLASS_SUPPORT
        } == dict(EXPECTED_CLASS_SUPPORT)

        # 같은 600행이라도 key 하나가 달라지면 label loader 호출 전 차단된다.
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE lot_history SET chamber_id='DRIFT' "
                "WHERE lot_hist_id='LH-FILL-0000'"
            )
        called: list[bool] = []

        def forbidden(*_args: Any) -> list[Any]:
            called.append(True)
            return []

        with pytest.raises(PredictionTargetMismatch, match="TARGET_MISMATCH"):
            read_evaluation_label_snapshot(
                evaluation_engine,
                frozen=frozen,
                expected_shared_key_sha256=runtime.shared_key_sha256,
                label_loader=forbidden,
            )
        assert called == []
