"""V5-C-5.1 묶음 1 공개 목록 read model의 격리 PostgreSQL 회귀."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
for candidate in (BACKEND_ROOT, BACKEND_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import rehearsal_postgres as postgres  # noqa: E402

from app.agent.ask import AgentAskService, AskSynthesis  # noqa: E402
from app.agent.public_read_model import (  # noqa: E402
    list_public_agent_runs,
    list_public_approvals,
)
from app.agent.repository import (  # noqa: E402
    CreateAgentRunCommand,
    RepositoryContractError,
    create_agent_run,
)
from app.agent.runtime_composition import AgentRuntime  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from app.common.tool_contracts import (  # noqa: E402
    DocumentHit,
    DocumentSearchToolInput,
    DocumentSearchToolResult,
    EquipmentContextToolInput,
    EquipmentContextToolResult,
    FdcSummaryToolInput,
    FdcSummaryToolResult,
)

pytestmark = pytest.mark.container

TARGET_DATABASE = "kosa_agent_e2e"
SQL_001 = REPOSITORY_ROOT / "infra" / "bootstrap" / "001_base_schema.sql"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"

RUN_ID = "RUN-0000000000000001"
ACTION_ID = "ACT-0000000000000001"
APPROVAL_ID = "APR-0000000000000001"


@pytest.fixture(scope="module")
def runtime_engine() -> Any:
    with postgres.one_off_postgres(database=TARGET_DATABASE) as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=TARGET_DATABASE,
            user=endpoint.username,
            password=endpoint.password,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(SQL_001.read_text(encoding="utf-8"))
            cursor.execute(SQL_002.read_text(encoding="utf-8"))
            cursor.execute(SQL_003.read_text(encoding="utf-8"))
            raw.commit()
        engine = create_engine(
            f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{TARGET_DATABASE}"
        )
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def seeded_runtime(runtime_engine: Any) -> None:
    with runtime_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO lot_history (
                    lot_hist_id, lot_id, wafer_no, wafer_id,
                    equipment_id, chamber_id
                ) VALUES
                    ('LH-00001', 'LOT004', 1, 'LOT004W001', 'EQP04', 'EQP04-PM2'),
                    ('LH-00002', 'LOT004', 2, 'LOT004W002', 'EQP04', 'EQP04-PM2')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO action_history (
                    action_id, lot_id, chamber_id, action_code, reason,
                    approval_required, approval_status, created_at
                ) VALUES (
                    :action_id, 'LOT004', 'EQP04-PM2', 'EQP_HOLD',
                    'R03_CONSEC: consecutive OOS', 'Y', 'PENDING',
                    '2026-08-04 07:00:40+09'
                )
                """
            ),
            {"action_id": ACTION_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_run (
                    agent_run_id, thread_id, lot_id, chamber_id,
                    requested_alarm_source, requested_alarm_id,
                    representative_alarm_source, representative_alarm_id,
                    status, autonomy_level, action, severity,
                    llm_model, prompt_version, latency_ms, started_at
                ) VALUES (
                    :run_id, '11111111-2222-3333-4444-555555555555',
                    'LOT004', 'EQP04-PM2', 'R03', 'R03-REQUESTED',
                    'TRACE', 'TRACE-REPRESENTATIVE',
                    'WAITING_APPROVAL', 2, 'EQP_HOLD', 'HIGH',
                    'configured-model', 'prompt-v1', 920,
                    '2026-08-04 07:00:30+09'
                )
                """
            ),
            {"run_id": RUN_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_prediction (
                    agent_run_id, predicted_fault_code, confidence,
                    cause_summary, evidence, llm_model, prompt_version
                ) VALUES (
                    :run_id, 'RFM', 0.840, 'runtime hypothesis',
                    '{"safe": true}'::jsonb, 'configured-model', 'prompt-v1'
                )
                """
            ),
            {"run_id": RUN_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_run_action (
                    agent_run_id, action_id, link_role, lot_id, chamber_id,
                    trigger_alarm_source, trigger_alarm_id
                ) VALUES (
                    :run_id, :action_id, 'CREATED', 'LOT004', 'EQP04-PM2',
                    'R03', 'R03-REQUESTED'
                )
                """
            ),
            {"run_id": RUN_ID, "action_id": ACTION_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_tool_call (
                    tool_call_id, agent_run_id, call_seq, tool_name,
                    input, output, status, latency_ms, error_msg
                ) VALUES
                    ('TOOL-000000000000000000000001', :run_id, 1,
                     'get_fdc_summary', '{"private":"input"}'::jsonb,
                     '{"private":"output"}'::jsonb, 'SUCCESS', 100,
                     'private-error'),
                    ('TOOL-000000000000000000000002', :run_id, 2,
                     'search_documents', NULL, NULL, 'TIMEOUT', 800,
                     'dsn=private')
                """
            ),
            {"run_id": RUN_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO approval_request (
                    approval_id, action_id, agent_run_id, status, requested_at
                ) VALUES (
                    :approval_id, :action_id, :run_id, 'PENDING',
                    '2026-08-04 07:00:40+09'
                )
                """
            ),
            {
                "approval_id": APPROVAL_ID,
                "action_id": ACTION_ID,
                "run_id": RUN_ID,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO action_delivery (
                    action_id, channel, status, request_hash
                ) VALUES
                    (:action_id, 'EMAIL', 'SENT', repeat('a', 64)),
                    (:action_id, 'MES_MOCK', 'BLOCKED', repeat('b', 64))
                """
            ),
            {"action_id": ACTION_ID},
        )


def test_public_run_query_uses_requested_alarm_and_hides_internal_payloads(
    runtime_engine: Any,
) -> None:
    with runtime_engine.connect() as connection:
        unfiltered = list_public_agent_runs(
            connection,
            date_from=None,
            date_to=None,
        )
        items = list_public_agent_runs(
            connection,
            date_from=date(2026, 8, 4),
            date_to=date(2026, 8, 4),
        )

    assert [item.agent_run_id for item in unfiltered] == [RUN_ID]
    assert len(items) == 1
    payload = items[0].model_dump(mode="json")
    assert (payload["alarm_source"], payload["alarm_id"]) == (
        "R03",
        "R03-REQUESTED",
    )
    assert [item["n"] for item in payload["tools"]] == [
        "get_fdc_summary",
        "search_documents",
    ]
    assert [item["channel"] for item in payload["deliveries"]] == [
        "EMAIL",
        "MES",
    ]
    serialized = str(payload)
    for forbidden in (
        "TRACE-REPRESENTATIVE",
        "private",
        "dsn=private",
        "MES_MOCK",
        "request_hash",
    ):
        assert forbidden not in serialized


def test_newly_created_running_run_is_immediately_listable(
    runtime_engine: Any,
) -> None:
    """묶음 1 게시 Gate: 생성 직후 required latency/model이 null이 아니다."""

    alarm = AlarmRef(source="SUMMARY", alarm_id="SUMMARY-RUNNING")
    with runtime_engine.begin() as connection:
        created = create_agent_run(
            connection,
            CreateAgentRunCommand(
                thread_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                lot_id="LOT-RUNNING",
                chamber_id="EQP01-PM1",
                autonomy_level=2,
                requested_alarm=alarm,
                representative_alarm=alarm,
                member_alarms=(alarm,),
                llm_model="configured-model",
                prompt_version="prompt-v1",
            ),
        )
        assert created.latency_ms == 0

    with runtime_engine.connect() as connection:
        listed = list_public_agent_runs(connection, date_from=None, date_to=None)

    item = next(item for item in listed if item.agent_run_id == created.agent_run_id)
    assert item.status.value == "RUNNING"
    assert item.llm_model == "configured-model"
    assert item.latency_ms >= 0


def test_compensation_refuses_duplicate_thread_without_failing_either_run(
    runtime_engine: Any,
) -> None:
    """thread exact-one 손상에서 임의 한 run을 FAILED로 고르지 않는다."""

    thread_id = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
    with runtime_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO agent_run (
                    agent_run_id, thread_id, lot_id, chamber_id,
                    requested_alarm_source, requested_alarm_id,
                    representative_alarm_source, representative_alarm_id,
                    status, autonomy_level, llm_model, prompt_version, latency_ms
                ) VALUES
                    ('RUN-00000000000000a1', :thread_id, 'LOT-DUP-1', 'EQP01-PM1',
                     'TRACE', 'TRACE-DUP-1', 'TRACE', 'TRACE-DUP-1',
                     'RUNNING', 2, 'configured-model', 'prompt-v1', 0),
                    ('RUN-00000000000000a2', :thread_id, 'LOT-DUP-2', 'EQP02-PM1',
                     'TRACE', 'TRACE-DUP-2', 'TRACE', 'TRACE-DUP-2',
                     'RUNNING', 2, 'configured-model', 'prompt-v1', 0)
                """
            ),
            {"thread_id": thread_id},
        )

    @contextmanager
    def transactions() -> Any:
        with runtime_engine.begin() as connection:
            yield connection

    resources = SimpleNamespace(transactions=transactions)
    runtime = AgentRuntime(llm_preflight=lambda: "configured-model")
    runtime._compensate_thread(  # noqa: SLF001 - compensation contract regression
        resources,  # type: ignore[arg-type]
        thread_id,
        code="INITIAL_CHECKPOINT_FAILED",
    )

    with runtime_engine.connect() as connection:
        statuses = (
            connection.execute(
                text(
                    """
                SELECT status FROM agent_run
                WHERE thread_id = :thread_id
                ORDER BY agent_run_id
                """
                ),
                {"thread_id": thread_id},
            )
            .scalars()
            .all()
        )

    assert statuses == ["RUNNING", "RUNNING"]


def test_public_approval_uses_real_distinct_equipment_and_decision_aliases(
    runtime_engine: Any,
) -> None:
    with runtime_engine.connect() as connection:
        pending = list_public_approvals(connection)
    assert len(pending) == 1
    assert pending[0].equipment_id == "EQP04"
    assert pending[0].approved_by is None

    with runtime_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE approval_request
                SET status = 'REJECTED', decided_by = 'operator',
                    decided_at = '2026-08-04 07:01:00+09',
                    decision_comment = 'checked'
                WHERE approval_id = :approval_id
                """
            ),
            {"approval_id": APPROVAL_ID},
        )
    with runtime_engine.connect() as connection:
        rejected = list_public_approvals(connection)
    assert rejected[0].status.value == "REJECTED"
    assert rejected[0].approved_by == rejected[0].decided_by == "operator"
    assert rejected[0].approved_at == rejected[0].decided_at


def test_public_approval_fails_closed_when_incident_has_two_equipments(
    runtime_engine: Any,
) -> None:
    with runtime_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO lot_history (
                    lot_hist_id, lot_id, wafer_no, wafer_id,
                    equipment_id, chamber_id
                ) VALUES (
                    'LH-00003', 'LOT004', 3, 'LOT004W003',
                    'EQP99', 'EQP04-PM2'
                )
                """
            )
        )

    with runtime_engine.connect() as connection:
        with pytest.raises(RepositoryContractError) as caught:
            list_public_approvals(connection)
    assert caught.value.code == "PUBLIC_APPROVAL_EQUIPMENT_NOT_EXACTLY_ONE"


def test_agent_ask_changes_zero_rows_in_all_runtime_write_tables(
    runtime_engine: Any,
) -> None:
    """Chat facade는 run FK를 만들거나 감사·조치·승인을 기록하지 않는다."""

    class _ReadTools:
        def get_fdc_summary(
            self, _request: FdcSummaryToolInput
        ) -> FdcSummaryToolResult:
            raise AssertionError("model-only question must not select FDC")

        def get_equipment_context(
            self, _request: EquipmentContextToolInput
        ) -> EquipmentContextToolResult:
            raise AssertionError("model-only question must not select graph")

        def search_documents(
            self, request: DocumentSearchToolInput
        ) -> DocumentSearchToolResult:
            assert request.model_code == "ET-7500"
            return DocumentSearchToolResult(
                ok=True,
                hits=[
                    DocumentHit(
                        chunk_id="DOC-TROUBLE-FDC:cs1:0001",
                        document_id="DOC-TROUBLE-FDC",
                        title="FDC troubleshooting guide",
                        section=None,
                        score=0.91,
                        content="RFM diagnosis evidence",
                        model_code="ET-7500",
                    )
                ],
            )

    def synthesize(_question: str, evidence: tuple[Any, ...]) -> AskSynthesis:
        return AskSynthesis(
            title="ET-7500 analysis",
            answer="The cited document contains relevant diagnostic evidence.",
            predicted_fault_code="RFM",
            confidence=0.8,
            recommended_action="WARNING",
            evidence_source_ids=[evidence[0].source_id],
        )

    tables = (
        "agent_run",
        "agent_tool_call",
        "action_history",
        "approval_request",
        "action_delivery",
        "audit_log",
    )

    def counts() -> dict[str, int]:
        with runtime_engine.connect() as connection:
            return {
                table: int(
                    connection.execute(
                        text(f"SELECT count(*) FROM {table}")
                    ).scalar_one()
                )
                for table in tables
            }

    before = counts()
    response = AgentAskService(
        tools=_ReadTools(),
        synthesizer=synthesize,
    ).ask("Explain ET-7500")
    after = counts()

    assert response.evidence_items[0].source_id == "DOC-TROUBLE-FDC:cs1:0001"
    assert after == before
