"""`V5-C-0.1` Runtime Repository 격리 PostgreSQL 16 회귀 — 묶음 1·2.

## 왜 mock으로 닫히지 않나

`V5-CM-4.2`가 `append_audit_log()` docstring에서 이 Task에 명시적으로 넘긴 것이 있다.

> 같은 Connection·caller commit·rollback 원자성 증명은 실제 DB fixture를 쓰는
> `V5-C-0.1` 통합 테스트가 소유한다.

`in_transaction()` 가드가 증명하는 범위는 "앞서 statement가 한 번 실행됐다"까지다.
업무 rollback이 감사까지 되돌리는지는 실제 transaction으로만 알 수 있다.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
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

from app.agent import repository as repo  # noqa: E402
from app.common.enums import (  # noqa: E402
    ActionCode,
    ActionLinkRole,
    AlarmSource,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
)
from app.common.ids import new_thread_id  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402

pytestmark = pytest.mark.container

TARGET_DATABASE = "kosa_agent_e2e"
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"


def _action_history_ddl() -> str:
    """FK 대상만 최소로 만든다. base 9 전체는 이 Task의 검증 대상이 아니다."""

    body = (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
    start = body.index("CREATE TABLE action_history (")
    return body[start : body.index(");", start) + 2]


@pytest.fixture(scope="module")
def runtime_engine() -> Any:
    """001→002→003 형상을 세우고 SQLAlchemy engine을 준다.

    module scope다 — 회귀마다 컨테이너를 새로 띄우면 얻는 것 없이 분 단위가 늘어난다.
    격리는 각 테스트가 자기 run id를 쓰고 `TRUNCATE`로 되돌려 유지한다.
    """

    with postgres.one_off_postgres(database=TARGET_DATABASE) as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=TARGET_DATABASE,
            user=endpoint.username,
            password=endpoint.password,
        ) as raw:
            cursor = raw.cursor()
            cursor.execute(_action_history_ddl())
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


#: run 하나가 건드리는 table. 매 테스트가 자기 흔적만 지운다.
_RUNTIME_TABLES = (
    "action_delivery",
    "approval_request",
    "agent_tool_call",
    "agent_run_action",
    "agent_prediction_review",
    "agent_prediction",
    "agent_run_alarm",
    "agent_run",
    "action_history",
    "audit_log",
)

#: link·approval·delivery가 FK로 요구하는 action. 값 자체는 이 Task의 검증 대상이
#: 아니므로 최소만 넣는다 — action 생성 규칙은 `V5-C-3.2` 소관이다.
ACTION_ID = "ACT-c010000000000001"


def _seed_action(engine: Any, action_id: str = ACTION_ID) -> str:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO action_history (action_id, lot_id, chamber_id, "
                " action_code) VALUES (:id, 'LOT-C01', 'EQP01-PM-C01', 'WARNING') "
                "ON CONFLICT (action_id) DO NOTHING"
            ),
            {"id": action_id},
        )
    return action_id


@pytest.fixture
def engine(runtime_engine: Any) -> Any:
    with runtime_engine.begin() as connection:
        for table in _RUNTIME_TABLES:
            connection.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
    return runtime_engine


def _ref(alarm_id: str, source: AlarmSource = AlarmSource.TRACE) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


def _command(**overrides: Any) -> repo.CreateAgentRunCommand:
    members = (_ref("TA-01"), _ref("SA-01", AlarmSource.SUMMARY))
    payload: dict[str, Any] = {
        "thread_id": new_thread_id(),
        "lot_id": "LOT-C01",
        "chamber_id": "EQP01-PM-C01",
        "autonomy_level": 2,
        "requested_alarm": members[0],
        "representative_alarm": members[0],
        "member_alarms": members,
    }
    payload.update(overrides)
    return repo.CreateAgentRunCommand(**payload)


def _counts(engine: Any, agent_run_id: str | None = None) -> dict[str, int]:
    with engine.connect() as connection:
        run_filter = "" if agent_run_id is None else " WHERE agent_run_id = :run"
        params = {} if agent_run_id is None else {"run": agent_run_id}
        result = {}
        for table in ("agent_run", "agent_run_alarm", "agent_prediction"):
            result[table] = connection.execute(
                text(f"SELECT count(*) FROM {table}{run_filter}"), params
            ).scalar_one()
        result["audit_log"] = connection.execute(
            text("SELECT count(*) FROM audit_log")
        ).scalar_one()
        return result


# --- 감사 원자성 ------------------------------------------------------------


def test_run_and_audit_commit_together(engine: Any) -> None:
    """업무 row와 감사 row가 **같은 transaction**에서 함께 보인다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())

    counts = _counts(engine, run.agent_run_id)
    assert counts["agent_run"] == 1
    assert counts["agent_run_alarm"] == 2
    assert counts["audit_log"] == 1

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT event_type, entity_type, entity_id FROM audit_log "
                "ORDER BY audit_id DESC LIMIT 1"
            )
        ).one()
    assert row.event_type == "AGENT_RUN_STARTED"
    assert row.entity_type == "AGENT_RUN"
    assert row.entity_id == run.agent_run_id


def test_business_rollback_takes_the_audit_with_it(engine: Any) -> None:
    """**이 Task가 소유하기로 한 증명이다**(CM-4.2 위임).

    감사만 남고 업무가 사라지면 감사가 거짓을 말한다. 그 반대도 마찬가지다.
    """

    with pytest.raises(RuntimeError, match="rollback"):
        with engine.begin() as connection:
            repo.create_agent_run(connection, _command())
            raise RuntimeError("업무 실패로 rollback")

    assert _counts(engine) == {
        "agent_run": 0,
        "agent_run_alarm": 0,
        "agent_prediction": 0,
        "audit_log": 0,
    }


def test_an_audit_failure_stays_inside_the_repository_boundary(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """감사 INSERT가 실패해도 **sanitized 오류**로 나가고 업무 row가 남지 않는다.

    이전 판은 raw `DataError`를 기대했다. 그건 green이면서 **잘못된 계약을 고정**한
    것이었다 — SQLAlchemy DBAPI 예외 문자열은 statement와 parameter를 싣고 나올 수
    있어 계획 §1.1·§6과 충돌한다(구현리뷰 묶음 1 필수 1).
    """

    original = repo.append_audit_log

    def _too_long(connection: Any, record: Any) -> int:
        # `audit_log.entity_id`는 varchar(20)이다. DB가 거부한다.
        return original(connection, record.model_copy(update={"entity_id": "R" * 21}))

    monkeypatch.setattr(repo, "append_audit_log", _too_long)
    with pytest.raises(repo.AgentRepositoryError) as exc:
        with engine.begin() as connection:
            repo.create_agent_run(connection, _command())
    monkeypatch.setattr(repo, "append_audit_log", original)

    # sanitized — SQL·parameter·DSN이 새지 않는다.
    message = str(exc.value)
    for leak in ("INSERT", "SELECT", "postgresql", "audit_log", "psycopg"):
        assert leak not in message, leak
    assert exc.value.code == "DATA_CONTRACT_VIOLATION"

    counts = _counts(engine)
    assert counts["agent_run"] == 0
    assert counts["agent_run_alarm"] == 0
    assert counts["audit_log"] == 0


def test_an_invalid_audit_record_is_refused_before_any_dml(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """감사 record 구성 실패는 **업무 DML 전에** 나온다.

    검증이 DML 뒤에 있으면 그 예외는 transaction을 abort하지 않는다. caller가
    transaction block 안에서 잡고 계속하면 업무 row만 commit될 여지가 생긴다.
    """

    executed: list[Any] = []
    original_execute = None

    with engine.begin() as connection:
        original_execute = type(connection).execute

        def _spy(self: Any, statement: Any, *args: Any, **kwargs: Any) -> Any:
            executed.append(statement)
            return original_execute(self, statement, *args, **kwargs)

        monkeypatch.setattr(type(connection), "execute", _spy)
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.create_agent_run(
                connection,
                _command(),
                # `audit_log.actor_id`는 varchar(40)이다.
                actor_id="A" * 41,
            )
        monkeypatch.setattr(type(connection), "execute", original_execute)

    assert exc.value.code == "AUDIT_RECORD_INVALID"
    assert executed == [], "감사 검증 전에 SQL이 실행됐다"
    assert _counts(engine)["agent_run"] == 0


def test_prediction_and_audit_are_one_unit(engine: Any) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())

    before = _counts(engine)["audit_log"]
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            repo.insert_prediction(
                connection,
                agent_run_id=run.agent_run_id,
                predicted_fault_code=FaultHypothesis.FOC,
                confidence=0.87,
                cause_summary="원인 요약",
                evidence={"alarm": "TA-01"},
                llm_model="claude",
                prompt_version="v1",
            )
            raise RuntimeError("보류")
    assert _counts(engine)["agent_prediction"] == 0
    assert _counts(engine)["audit_log"] == before

    with engine.begin() as connection:
        prediction = repo.insert_prediction(
            connection,
            agent_run_id=run.agent_run_id,
            predicted_fault_code=FaultHypothesis.FOC,
            confidence=0.87,
            cause_summary="원인 요약",
            evidence={"alarm": "TA-01"},
            llm_model="claude",
            prompt_version="v1",
        )
    assert prediction.confidence == pytest.approx(0.87)
    assert _counts(engine)["audit_log"] == before + 1
    with engine.connect() as connection:
        assert repo.get_prediction(connection, run.agent_run_id).evidence == {
            "alarm": "TA-01"
        }


# --- 대표 알람 불변 ---------------------------------------------------------


def test_the_representative_row_matches_the_scalar(engine: Any) -> None:
    """두 저장 위치가 같은 알람을 가리킨다 — public API로 어긋나게 할 수 없다."""

    members = (
        _ref("TA-01"),
        _ref("SA-01", AlarmSource.SUMMARY),
        _ref("R3-01", AlarmSource.R03),
    )
    with engine.begin() as connection:
        run = repo.create_agent_run(
            connection,
            _command(
                member_alarms=members,
                requested_alarm=members[0],
                representative_alarm=members[2],
            ),
        )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT alarm_source, alarm_id, is_representative FROM agent_run_alarm "
                "WHERE agent_run_id = :run ORDER BY alarm_source"
            ),
            {"run": run.agent_run_id},
        ).all()
        alarms = repo.list_run_alarms(connection, run.agent_run_id)

    flagged = [(r.alarm_source, r.alarm_id) for r in rows if r.is_representative]
    assert flagged == [("R03", "R3-01")]
    assert run.representative_alarm == members[2]
    # 안정 정렬 — 대표가 항상 먼저다.
    assert alarms[0] == members[2]
    assert len(alarms) == 3


def test_a_second_representative_is_refused_by_the_index(engine: Any) -> None:
    """`ux_agent_run_alarm_representative`가 최종 방어선이다.

    Repository로는 만들 수 없으므로 direct mutation으로 index를 확인한다.
    """

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as exc:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_run_alarm "
                    "(agent_run_id, alarm_source, alarm_id, is_representative) "
                    "VALUES (:run, 'R03', 'R3-99', true)"
                ),
                {"run": run.agent_run_id},
            )
    assert repo._constraint_name(exc.value) == "ux_agent_run_alarm_representative"
    assert repo.CONFLICT_CODES["ux_agent_run_alarm_representative"] == (
        "REPRESENTATIVE_ALARM_EXISTS"
    )


def test_an_active_incident_conflict_becomes_a_stable_code(engine: Any) -> None:
    """같은 incident에 활성 run 2건은 `ux_agent_run_incident_active`가 막는다."""

    with engine.begin() as connection:
        repo.create_agent_run(connection, _command())

    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.create_agent_run(connection, _command())
    assert exc.value.code == "ACTIVE_RUN_EXISTS"
    assert _counts(engine)["agent_run"] == 1


# --- action·severity --------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "severity"),
    [
        (None, None),
        (ActionCode.MONITORING, Severity.LOW),
        (ActionCode.WARNING, Severity.MEDIUM),
        (ActionCode.EQP_HOLD, Severity.HIGH),
    ],
)
def test_every_allowed_pair_round_trips(
    engine: Any, action: ActionCode | None, severity: Severity | None
) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
    with engine.begin() as connection:
        updated = repo.set_run_action(connection, run.agent_run_id, action)
    assert (updated.action, updated.severity) == (action, severity)


@pytest.mark.parametrize(
    ("action", "severity"),
    [
        ("MONITORING", None),
        (None, "LOW"),
        ("MONITORING", "HIGH"),
        ("EQP_HOLD", "LOW"),
    ],
)
def test_the_named_check_refuses_a_broken_pair(
    engine: Any, action: str | None, severity: str | None
) -> None:
    """Repository로는 만들 수 없는 조합을 direct mutation으로 확인한다.

    003의 named CHECK가 최종 방어선이라는 계획 §3의 주장이 실제로 참인지 본다.
    """

    from sqlalchemy.exc import IntegrityError

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())

    with pytest.raises(IntegrityError) as exc:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE agent_run SET action = :a, severity = :s "
                    "WHERE agent_run_id = :run"
                ),
                {"a": action, "s": severity, "run": run.agent_run_id},
            )
    assert repo._constraint_name(exc.value) == "ck_agent_run_action_severity_pair"


def test_action_bundle_repository_seams_round_trip_on_postgres(engine: Any) -> None:
    """C-3.2의 action·optional link·bundle seam을 실제 schema에서 확인한다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
        assert repo.find_run_action(connection, run.agent_run_id) is None
        action = repo.insert_action_history(
            connection,
            action_id=ACTION_ID,
            lot_id=run.lot_id,
            chamber_id=run.chamber_id,
            action_code=ActionCode.WARNING,
            reason="TRACE OOS 알람이 존재해 경고 조치를 생성했습니다.",
            created_at=datetime.now(UTC),
        )
        repo.link_run_action(
            connection,
            agent_run_id=run.agent_run_id,
            action_id=action.action_id,
            link_role=ActionLinkRole.CREATED,
            lot_id=run.lot_id,
            chamber_id=run.chamber_id,
            trigger_alarm=run.representative_alarm,
        )
        repo.insert_action_delivery(
            connection,
            action_id=action.action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash="a" * 64,
        )
        linked = repo.find_run_action(connection, run.agent_run_id)
        bundle = repo.get_action_bundle(connection, action.action_id)

    assert linked is not None and linked.action_id == ACTION_ID
    assert action.approval_status is ApprovalStatus.AUTO
    assert action.approved_by == "system"
    assert bundle == repo.ActionBundle(
        action_id=ACTION_ID,
        action_code=ActionCode.WARNING,
        approval_id=None,
        approval_status=None,
        approval_agent_run_id=None,
        delivery_channels=(DeliveryChannel.EMAIL,),
    )


def test_action_provenance_merge_survives_terminal_fields(engine: Any) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
        repo.merge_run_action_provenance(
            connection,
            run.agent_run_id,
            action_policy_version="ACTION-POLICY-V1",
            member_alarms=(run.requested_alarm,),
        )

    with engine.begin() as connection:
        merged = repo.merge_run_action_provenance(
            connection,
            run.agent_run_id,
            terminal_evidence={"code": "FIXTURE"},
        )

    assert merged.evidence == {
        "action_provenance": {
            "schema": "action-provenance-v1",
            "action_policy_version": "ACTION-POLICY-V1",
            "member_alarms": [{"source": AlarmSource.TRACE.value, "alarm_id": "TA-01"}],
        },
        "code": "FIXTURE",
    }


@pytest.mark.parametrize("terminal", [RunStatus.COMPLETED, RunStatus.FAILED])
def test_action_provenance_merge_refuses_a_terminal_run(
    engine: Any,
    terminal: RunStatus,
) -> None:
    provenance = {
        "schema": "action-provenance-v1",
        "action_policy_version": "ACTION-POLICY-V1",
        "member_alarms": [{"source": AlarmSource.TRACE.value, "alarm_id": "TA-01"}],
    }
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
        repo.merge_run_action_provenance(
            connection,
            run.agent_run_id,
            action_policy_version="ACTION-POLICY-V1",
            member_alarms=(run.requested_alarm,),
        )
        repo.finish_agent_run(
            connection,
            run.agent_run_id,
            terminal,
            evidence={"action_provenance": provenance, "code": "TERMINAL"},
        )

    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.merge_run_action_provenance(
                connection,
                run.agent_run_id,
                terminal_evidence={"code": "LATE_WRITE"},
            )

    with engine.connect() as connection:
        stored = repo.get_agent_run(connection, run.agent_run_id)

    assert exc.value.code == "RUN_NOT_ACTIVE"
    assert stored.evidence == {
        "action_provenance": provenance,
        "code": "TERMINAL",
    }


# --- 상태 전이 --------------------------------------------------------------


def test_finishing_twice_is_refused(engine: Any) -> None:
    """이미 terminal인 run을 다시 끝내면 `ended_at`이 덮이고 감사가 두 번 남는다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
    with engine.begin() as connection:
        finished = repo.finish_agent_run(
            connection, run.agent_run_id, RunStatus.COMPLETED
        )
    assert finished.status is RunStatus.COMPLETED
    assert finished.ended_at is not None

    audits = _counts(engine)["audit_log"]
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.finish_agent_run(connection, run.agent_run_id, RunStatus.FAILED)
    assert exc.value.code == "RUN_NOT_ACTIVE"
    # 감사가 두 번 남지 않는다 — 재전이가 0행이므로 append 자체에 닿지 않는다.
    assert _counts(engine)["audit_log"] == audits


def test_active_lookup_ignores_finished_runs(engine: Any) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
    with engine.connect() as connection:
        found = repo.find_active_run(
            connection, lot_id="LOT-C01", chamber_id="EQP01-PM-C01"
        )
    assert found is not None and found.agent_run_id == run.agent_run_id

    with engine.begin() as connection:
        repo.finish_agent_run(connection, run.agent_run_id, RunStatus.COMPLETED)
    with engine.connect() as connection:
        assert (
            repo.find_active_run(
                connection, lot_id="LOT-C01", chamber_id="EQP01-PM-C01"
            )
            is None
        )
    # 같은 incident에 새 run을 열 수 있어야 한다 — 부분 index가 terminal을 제외한다.
    with engine.begin() as connection:
        repo.create_agent_run(connection, _command())


def test_a_missing_run_is_not_found(engine: Any) -> None:
    with engine.connect() as connection:
        with pytest.raises(repo.RepositoryNotFound) as exc:
            repo.get_agent_run(connection, "RUN-0000000000000000")
    assert exc.value.code == "RUN_NOT_FOUND"


# --- label 격리 -------------------------------------------------------------


def test_hidden_gold_rows_are_invisible_to_the_runtime_read(engine: Any) -> None:
    """DB에 있어도 Runtime 조회가 돌려주지 않는다.

    `V5-C-6.2`가 격리 adapter로 읽기 전까지 Runtime은 정답을 모른다.
    """

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
        repo.insert_human_prediction_review(
            connection,
            agent_run_id=run.agent_run_id,
            disposition="ACCEPTED",
            label_source="HUMAN_REVIEW",
            reviewer="reviewer-1",
        )
    with engine.begin() as connection:
        # 평가 label은 다른 경로가 넣는다 — direct mutation으로 그 상황을 만든다.
        connection.execute(
            text(
                "INSERT INTO agent_prediction_review "
                "(agent_run_id, reviewed_fault_code, disposition, "
                " label_source, reviewer) "
                "VALUES (:run, 'RFM', 'CORRECTED', 'HIDDEN_GOLD', 'gold')"
            ),
            {"run": run.agent_run_id},
        )

    with engine.connect() as connection:
        reviews = repo.list_human_prediction_reviews(connection, run.agent_run_id)
    assert [r.label_source for r in reviews] == ["HUMAN_REVIEW"]
    with engine.connect() as connection:
        total = connection.execute(
            text(
                "SELECT count(*) FROM agent_prediction_review WHERE agent_run_id = :r"
            ),
            {"r": run.agent_run_id},
        ).scalar_one()
    assert total == 2, "DB에는 있는데 Runtime이 안 볼 뿐이다"


def test_a_corrected_review_needs_a_fault_code(engine: Any) -> None:
    """`002`의 CHECK가 강제한다 — Repository는 그것을 sanitized로 옮긴다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())

    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.insert_human_prediction_review(
                connection,
                agent_run_id=run.agent_run_id,
                disposition="CORRECTED",
                label_source="MENTOR_REVIEW",
                reviewer="mentor",
            )
    assert exc.value.code == "CONSTRAINT_VIOLATION"

    with engine.begin() as connection:
        review = repo.insert_human_prediction_review(
            connection,
            agent_run_id=run.agent_run_id,
            disposition="CORRECTED",
            label_source="MENTOR_REVIEW",
            reviewer="mentor",
            reviewed_fault_code=FaultHypothesis.RFM,
        )
    assert review.reviewed_fault_code is FaultHypothesis.RFM


# --- 저장한 것은 되읽는다 (구현리뷰 묶음 1 필수 3) ---------------------------


def test_evidence_and_metrics_round_trip(engine: Any) -> None:
    """`finish_agent_run()`이 저장한 값을 반환값과 이후 조회가 **모두** 갖는다.

    이전 판은 세 메트릭을 UPDATE하고도 row 계약에 없어 방금 저장한 값을 버렸다.
    `evidence`는 저장 경로 자체가 없었다.
    """

    evidence = {"alarms": ["TA-01"], "tool_calls": 3, "note": "요약"}
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
    assert run.evidence is None
    # public 목록은 202 직후에도 required latency를 반환하므로 생성 subtotal은 0이다.
    assert (run.input_tokens, run.output_tokens, run.latency_ms) == (None, None, 0)

    with engine.begin() as connection:
        finished = repo.finish_agent_run(
            connection,
            run.agent_run_id,
            RunStatus.COMPLETED,
            evidence=evidence,
            input_tokens=1200,
            output_tokens=340,
            latency_ms=8700,
        )
    assert finished.evidence == evidence
    assert (finished.input_tokens, finished.output_tokens, finished.latency_ms) == (
        1200,
        340,
        8700,
    )

    with engine.connect() as connection:
        reloaded = repo.get_agent_run(connection, run.agent_run_id)
    assert reloaded.evidence == evidence
    assert reloaded.latency_ms == 8700


def test_run_llm_usage_adds_actual_cost_and_refuses_provenance_change(
    engine: Any,
) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(
            connection,
            _command(prompt_version="agent-hypothesis-v1"),
        )
    with engine.begin() as connection:
        repo.record_run_llm_usage(
            connection,
            run.agent_run_id,
            llm_model="actual-model",
            prompt_version="agent-hypothesis-v1",
            input_tokens=10,
            output_tokens=4,
        )
        updated = repo.record_run_llm_usage(
            connection,
            run.agent_run_id,
            llm_model="actual-model",
            prompt_version="agent-hypothesis-v1",
            input_tokens=20,
            output_tokens=8,
        )
    assert (updated.input_tokens, updated.output_tokens) == (30, 12)
    assert updated.llm_model == "actual-model"

    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.record_run_llm_usage(
                connection,
                run.agent_run_id,
                llm_model="different-model",
                prompt_version="agent-hypothesis-v1",
                input_tokens=1,
                output_tokens=1,
            )
    assert exc.value.code == "PREDICTION_CONFLICT"

    with engine.connect() as connection:
        unchanged = repo.get_agent_run(connection, run.agent_run_id)
    assert (unchanged.input_tokens, unchanged.output_tokens) == (30, 12)


def test_run_llm_usage_aggregate_overflow_and_terminal_run_are_rejected(
    engine: Any,
) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(
            connection,
            _command(prompt_version="agent-hypothesis-v1"),
        )
        connection.execute(
            text(
                "UPDATE agent_run SET llm_model='actual-model', "
                "input_tokens=2147483647, output_tokens=0 "
                "WHERE agent_run_id=:run_id"
            ),
            {"run_id": run.agent_run_id},
        )
    with pytest.raises(repo.RepositoryContractError) as overflow:
        with engine.begin() as connection:
            repo.record_run_llm_usage(
                connection,
                run.agent_run_id,
                llm_model="actual-model",
                prompt_version="agent-hypothesis-v1",
                input_tokens=0,
                output_tokens=1,
            )
    assert overflow.value.code == "RUN_TOKEN_OVERFLOW"

    with engine.begin() as connection:
        repo.finish_agent_run(connection, run.agent_run_id, RunStatus.FAILED)
    with pytest.raises(repo.RepositoryConflict) as terminal:
        with engine.begin() as connection:
            repo.record_run_llm_usage(
                connection,
                run.agent_run_id,
                llm_model="actual-model",
                prompt_version="agent-hypothesis-v1",
                input_tokens=0,
                output_tokens=0,
            )
    assert terminal.value.code == "RUN_NOT_ACTIVE"


def test_concurrent_hypothesis_save_keeps_one_prediction_and_both_usage_costs(
    engine: Any,
) -> None:
    with engine.begin() as connection:
        run = repo.create_agent_run(
            connection,
            _command(prompt_version="agent-hypothesis-v1"),
        )
    barrier = Barrier(2)

    def save() -> None:
        barrier.wait(timeout=10)
        with engine.begin() as connection:
            current = repo.lock_agent_run(connection, run.agent_run_id)
            prediction = repo.get_prediction_or_none(connection, run.agent_run_id)
            if prediction is None:
                repo.insert_prediction(
                    connection,
                    agent_run_id=run.agent_run_id,
                    predicted_fault_code=FaultHypothesis.OTH,
                    confidence=0.5,
                    cause_summary="fixture",
                    evidence={
                        "schema_version": "agent-evidence-v1",
                        "supporting_alarms": [{"source": "TRACE", "alarm_id": "TA-01"}],
                        "supporting_chunk_ids": [],
                        "supporting_relation_ids": [],
                        "uncertainty": "",
                    },
                    llm_model="actual-model",
                    prompt_version="agent-hypothesis-v1",
                )
            else:
                assert current.llm_model == prediction.llm_model
            repo.record_run_llm_usage(
                connection,
                run.agent_run_id,
                llm_model="actual-model",
                prompt_version="agent-hypothesis-v1",
                input_tokens=10,
                output_tokens=5,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save) for _ in range(2)]
        for future in futures:
            future.result(timeout=20)

    with engine.connect() as connection:
        prediction_count = connection.execute(
            text("SELECT count(*) FROM agent_prediction " "WHERE agent_run_id=:run_id"),
            {"run_id": run.agent_run_id},
        ).scalar_one()
        stored = repo.get_agent_run(connection, run.agent_run_id)
    assert prediction_count == 1
    assert (stored.input_tokens, stored.output_tokens) == (20, 10)


def test_a_rolled_back_finish_preserves_the_previous_values(engine: Any) -> None:
    """전이가 rollback되면 기존 값이 그대로다 — 부분 저장이 없다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
    with engine.begin() as connection:
        repo.finish_agent_run(
            connection,
            run.agent_run_id,
            RunStatus.COMPLETED,
            evidence={"v": 1},
            latency_ms=100,
        )

    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE agent_run SET evidence = CAST('{\"v\": 2}' AS jsonb), "
                    "latency_ms = 999 WHERE agent_run_id = :run"
                ),
                {"run": run.agent_run_id},
            )
            raise RuntimeError("보류")

    with engine.connect() as connection:
        reloaded = repo.get_agent_run(connection, run.agent_run_id)
    assert reloaded.evidence == {"v": 1}
    assert reloaded.latency_ms == 100


def test_finishing_a_missing_run_differs_from_a_finished_one(engine: Any) -> None:
    """없는 run과 이미 끝난 run을 구분한다(구현리뷰 묶음 1 권장 1).

    둘 다 NotFound면 상위가 404로 일괄 mapping할 때 상태 충돌까지 404가 된다.
    """

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
    with engine.begin() as connection:
        repo.finish_agent_run(connection, run.agent_run_id, RunStatus.COMPLETED)

    with pytest.raises(repo.RepositoryConflict) as conflict:
        with engine.begin() as connection:
            repo.finish_agent_run(connection, run.agent_run_id, RunStatus.FAILED)
    assert conflict.value.code == "RUN_NOT_ACTIVE"

    with pytest.raises(repo.RepositoryNotFound) as missing:
        with engine.begin() as connection:
            repo.finish_agent_run(connection, "RUN-0000000000000000", RunStatus.FAILED)
    assert missing.value.code == "RUN_NOT_FOUND"


# --- 문자열 경계 (구현리뷰 묶음 1 필수 2) -----------------------------------


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("thread_id", "T" * 37, "THREAD_ID_TOO_LONG"),
        ("thread_id", "   ", "EMPTY_THREAD_ID"),
        ("lot_id", "L" * 21, "LOT_ID_TOO_LONG"),
        ("chamber_id", "C" * 25, "CHAMBER_ID_TOO_LONG"),
        ("retry_of_run_id", "R" * 21, "RETRY_OF_RUN_ID_TOO_LONG"),
        ("llm_model", "M" * 65, "LLM_MODEL_TOO_LONG"),
        ("prompt_version", "P" * 41, "PROMPT_VERSION_TOO_LONG"),
    ],
)
def test_a_boundary_violation_never_reaches_the_database(
    engine: Any, field: str, value: str, code: str
) -> None:
    """`max+1`은 SQL 실행 0건으로 끝난다.

    DB까지 보내면 `DataError`가 되고, 그것은 caller 입력 오류인데도 이전 판에서
    `RepositoryUnavailable`(=503 후보)로 분류됐다.
    """

    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.create_agent_run(connection, _command(**{field: value}))
    assert exc.value.code == code
    assert _counts(engine)["agent_run"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thread_id", "T" * 36),
        ("lot_id", "L" * 20),
        ("chamber_id", "C" * 24),
        ("llm_model", "M" * 64),
        ("prompt_version", "P" * 40),
    ],
)
def test_the_boundary_value_itself_is_accepted(
    engine: Any, field: str, value: str
) -> None:
    """**양성 대조군.** `max`는 통과해야 한다 — 경계를 하나 좁게 잡지 않았는지 본다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command(**{field: value}))
    assert getattr(run, field) == value


def test_an_over_long_alarm_id_is_refused_before_sql(engine: Any) -> None:
    long_alarm = _ref("A" * 25)
    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.create_agent_run(
                connection,
                _command(
                    member_alarms=(long_alarm,),
                    requested_alarm=long_alarm,
                    representative_alarm=long_alarm,
                ),
            )
    assert exc.value.code == "ALARM_ID_TOO_LONG"
    assert _counts(engine)["agent_run"] == 0


# --- 정규화·직렬화가 실제 write까지 이어진다 (2차 필수 1) --------------------


@pytest.mark.parametrize(
    ("field", "core"),
    [
        ("thread_id", "T" * 36),
        ("lot_id", "L" * 20),
        ("chamber_id", "C" * 24),
        ("llm_model", "M" * 64),
        ("prompt_version", "P" * 40),
    ],
)
def test_a_padded_max_value_is_stored_trimmed(
    engine: Any, field: str, core: str
) -> None:
    """**검증한 값이 실제로 저장된다**(구현리뷰 묶음 1 2차 필수 1-A).

    이전 판은 trim한 값을 만들어 놓고 버린 뒤 원문을 bind했다. 그래서
    `" " + "x"*36 + " "`가 검증에서 길이 36으로 통과하고 실제로는 38자가 DB로 갔다 —
    varchar 경계 초과다. 단위 테스트가 증명한다고 믿은 계약이 write 경로에 없었다.
    """

    padded = f"  {core}  "
    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command(**{field: padded}))
    assert getattr(run, field) == core

    with engine.connect() as connection:
        stored = connection.execute(
            text(f"SELECT {field} FROM agent_run WHERE agent_run_id = :run"),
            {"run": run.agent_run_id},
        ).scalar_one()
    assert stored == core, "DB에 원문이 저장됐다"


def test_padded_prediction_and_review_text_is_stored_trimmed(engine: Any) -> None:
    """prediction·review도 같은 구조였다."""

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())
        prediction = repo.insert_prediction(
            connection,
            agent_run_id=run.agent_run_id,
            predicted_fault_code=FaultHypothesis.TMD,
            confidence=0.5,
            cause_summary="  원인  ",
            llm_model=f"  {'M' * 64}  ",
            prompt_version="  v1  ",
            evidence={},
        )
        review = repo.insert_human_prediction_review(
            connection,
            agent_run_id=run.agent_run_id,
            disposition="  ACCEPTED  ",
            label_source="HUMAN_REVIEW",
            reviewer="  reviewer-1  ",
        )
    assert prediction.cause_summary == "원인"
    assert prediction.llm_model == "M" * 64
    assert prediction.prompt_version == "v1"
    assert review.disposition == "ACCEPTED"
    assert review.reviewer == "reviewer-1"


@pytest.mark.parametrize(
    "bad",
    [
        {"bad": object()},
        # PostgreSQL `::jsonb`가 거부하는 값. Python은 직렬화에 성공하므로
        # `allow_nan=False`가 없으면 SQL까지 도달했다(구현리뷰 묶음 1 3차 필수 1).
        {"v": float("nan")},
        {"v": float("inf")},
        {"v": float("-inf")},
    ],
    ids=["object", "nan", "inf", "-inf"],
)
@pytest.mark.parametrize("target", ["finish", "prediction"])
def test_unserialisable_evidence_is_sanitized_with_no_sql(
    engine: Any, target: str, bad: dict[str, Any]
) -> None:
    """**invalid JSON도 sanitized다**(구현리뷰 묶음 1 2차 필수 1-B).

    SQL 실행 전이라 원자성은 멀쩡했지만 `TypeError`가 그대로 올라가면서 직렬화 못 한
    객체의 타입 이름이 예외 문자열에 실렸다.
    """

    with engine.begin() as connection:
        run = repo.create_agent_run(connection, _command())

    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            if target == "finish":
                repo.finish_agent_run(
                    connection,
                    run.agent_run_id,
                    RunStatus.COMPLETED,
                    evidence=bad,
                )
            else:
                repo.insert_prediction(
                    connection,
                    agent_run_id=run.agent_run_id,
                    predicted_fault_code=FaultHypothesis.OTH,
                    confidence=0.1,
                    cause_summary="x",
                    llm_model="m",
                    prompt_version="v",
                    evidence=bad,
                )
    assert exc.value.code == "INVALID_JSON_EVIDENCE"
    message = str(exc.value)
    for leak in ("object", "TypeError", "ValueError", "NaN", "INSERT", "UPDATE"):
        assert leak not in message, leak

    # SQL 0건 — run은 여전히 활성이고 prediction도 없다.
    with engine.connect() as connection:
        reloaded = repo.get_agent_run(connection, run.agent_run_id)
    assert reloaded.status is RunStatus.RUNNING
    assert reloaded.evidence is None
    assert _counts(engine)["agent_prediction"] == 0


# ===========================================================================
# 묶음 2 — action link · ToolCall · approval · delivery
# ===========================================================================


def _run_id(engine: Any, **overrides: Any) -> str:
    with engine.begin() as connection:
        return repo.create_agent_run(connection, _command(**overrides)).agent_run_id


# --- agent_run_action -------------------------------------------------------


def test_link_run_action_round_trips(engine: Any) -> None:
    action_id = _seed_action(engine)
    run = _run_id(engine)
    with engine.begin() as connection:
        linked = repo.link_run_action(
            connection,
            agent_run_id=run,
            action_id=action_id,
            link_role=ActionLinkRole.CREATED,
            lot_id="LOT-C01",
            chamber_id="EQP01-PM-C01",
            trigger_alarm=_ref("TA-01"),
        )
    assert linked.link_role is ActionLinkRole.CREATED
    with engine.connect() as connection:
        assert repo.get_run_action(connection, run) == linked
        found = repo.find_created_action(
            connection, lot_id="LOT-C01", chamber_id="EQP01-PM-C01"
        )
    assert found is not None and found.action_id == action_id


def test_a_second_link_on_the_same_run_is_a_conflict(engine: Any) -> None:
    """`agent_run_action_pkey` — run당 link는 1건이다.

    1차 계획리뷰 필수 3이 요구한 PK 3종 중 하나이며, **여기가 첫 실측 지점**이다.
    """

    action_id = _seed_action(engine)
    other = _seed_action(engine, "ACT-c010000000000002")
    run = _run_id(engine)
    with engine.begin() as connection:
        repo.link_run_action(
            connection,
            agent_run_id=run,
            action_id=action_id,
            link_role=ActionLinkRole.CREATED,
            lot_id="LOT-C01",
            chamber_id="EQP01-PM-C01",
            trigger_alarm=_ref("TA-01"),
        )
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.link_run_action(
                connection,
                agent_run_id=run,
                action_id=other,
                link_role=ActionLinkRole.REUSED,
                lot_id="LOT-C01",
                chamber_id="EQP01-PM-C01",
                trigger_alarm=_ref("TA-01"),
            )
    assert exc.value.code == "RUN_ACTION_ALREADY_LINKED"


def test_a_second_created_action_for_one_incident_is_a_conflict(engine: Any) -> None:
    """`ux_agent_run_action_incident` — incident당 `CREATED`는 1건이다."""

    action_id = _seed_action(engine)
    other = _seed_action(engine, "ACT-c010000000000002")
    first = _run_id(engine)
    with engine.begin() as connection:
        repo.link_run_action(
            connection,
            agent_run_id=first,
            action_id=action_id,
            link_role=ActionLinkRole.CREATED,
            lot_id="LOT-C01",
            chamber_id="EQP01-PM-C01",
            trigger_alarm=_ref("TA-01"),
        )
        repo.finish_agent_run(connection, first, RunStatus.COMPLETED)
    second = _run_id(engine)
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.link_run_action(
                connection,
                agent_run_id=second,
                action_id=other,
                link_role=ActionLinkRole.CREATED,
                lot_id="LOT-C01",
                chamber_id="EQP01-PM-C01",
                trigger_alarm=_ref("TA-01"),
            )
    assert exc.value.code == "CREATED_ACTION_EXISTS"


# --- agent_tool_call --------------------------------------------------------


def test_reserve_then_finalize_across_two_units_of_work(engine: Any) -> None:
    """계획 §4의 UoW 1/UoW 2 경계를 그대로 돈다."""

    run = _run_id(engine)
    with engine.begin() as connection:  # UoW 1
        reserved = repo.reserve_tool_call(
            connection, agent_run_id=run, tool_name="get_fdc_summary", input={"a": 1}
        )
    assert reserved.call_seq == 1
    assert reserved.status is ToolCallStatus.ERROR
    assert reserved.error_msg == repo.RESERVED_ERROR_MSG
    assert reserved.output is None and reserved.latency_ms is None

    with engine.begin() as connection:  # UoW 2
        done = repo.finalize_tool_call(
            connection,
            tool_call_id=reserved.tool_call_id,
            agent_run_id=run,
            status=ToolCallStatus.SUCCESS,
            latency_ms=1234,
            output={"rows": 3},
        )
    assert done.status is ToolCallStatus.SUCCESS
    assert done.output == {"rows": 3}
    assert done.latency_ms == 1234
    assert done.error_msg is None
    assert done.input == {"a": 1}


def test_a_reserved_call_survives_a_lost_process(engine: Any) -> None:
    """**예약 commit 뒤 finalize가 없어도 시도가 남는다**(계획 §4.1).

    HITL 재개 시 예산이 늘어나지 않는다는 주장의 근거다. 예약이 commit되지 않으면
    이 성질은 성립하지 않으므로 rollback 대조군을 함께 둔다.
    """

    run = _run_id(engine)
    with engine.begin() as connection:
        repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t1")

    # 새 connection에서도 보인다.
    with engine.connect() as connection:
        assert repo.count_tool_calls(connection, run) == 1
        [only] = repo.list_tool_calls(connection, run)
    assert only.error_msg == repo.RESERVED_ERROR_MSG

    # 반대로 예약을 rollback하면 아무것도 남지 않는다.
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t2")
            raise RuntimeError("중단")
    with engine.connect() as connection:
        assert repo.count_tool_calls(connection, run) == 1


def test_finalizing_twice_is_a_conflict(engine: Any) -> None:
    run = _run_id(engine)
    with engine.begin() as connection:
        reserved = repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t")
    with engine.begin() as connection:
        repo.finalize_tool_call(
            connection,
            tool_call_id=reserved.tool_call_id,
            agent_run_id=run,
            status=ToolCallStatus.TIMEOUT,
            latency_ms=30000,
            error_msg="시간 초과",
        )
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.finalize_tool_call(
                connection,
                tool_call_id=reserved.tool_call_id,
                agent_run_id=run,
                status=ToolCallStatus.SUCCESS,
                latency_ms=1,
            )
    assert exc.value.code == "TOOL_CALL_ALREADY_FINALIZED"


def test_finalizing_another_runs_call_is_not_found(engine: Any) -> None:
    """다른 run의 ID로는 닫을 수 없다."""

    run = _run_id(engine)
    other = _run_id(engine, lot_id="LOT-C02", chamber_id="EQP01-PM-C02")
    with engine.begin() as connection:
        reserved = repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t")
    with pytest.raises(repo.RepositoryNotFound) as exc:
        with engine.begin() as connection:
            repo.finalize_tool_call(
                connection,
                tool_call_id=reserved.tool_call_id,
                agent_run_id=other,
                status=ToolCallStatus.SUCCESS,
                latency_ms=1,
            )
    assert exc.value.code == "TOOL_CALL_NOT_FOUND"


def test_successful_send_action_no_calls_remain_audited_but_are_budget_free(
    engine: Any,
) -> None:
    run = _run_id(engine)
    outputs = [
        {
            "ok": True,
            "action_id": "ACT-1",
            "deliveries": [],
            "effect_attempted": True,
            "effect_channel": "EMAIL",
        },
        {
            "ok": True,
            "action_id": "ACT-1",
            "deliveries": [],
            "effect_attempted": False,
            "effect_channel": None,
        },
        {
            "ok": True,
            "action_id": "ACT-1",
            "deliveries": [],
            "effect_attempted": False,
            "effect_channel": None,
        },
    ]
    for output in outputs:
        with engine.begin() as connection:
            reserved = repo.reserve_tool_call(
                connection,
                agent_run_id=run,
                tool_name="send_action",
            )
        with engine.begin() as connection:
            repo.finalize_tool_call(
                connection,
                tool_call_id=reserved.tool_call_id,
                agent_run_id=run,
                status=ToolCallStatus.SUCCESS,
                latency_ms=1,
                output=output,
            )

    with engine.begin() as connection:
        physical = repo.count_tool_calls(connection, run)
        budget = repo.count_tool_calls_for_budget(connection, run)

    assert physical == 3
    assert budget.total == 1
    assert budget.by_tool == {"send_action": 1}


def test_concurrent_reservations_get_distinct_sequences(runtime_engine: Any) -> None:
    """**두 connection이 동시에 예약해도 `call_seq`가 `[1, 2]`다**(계획 §4.3).

    `agent_run` row lock이 없으면 둘 다 `max+1 = 1`을 읽어 하나가 unique 제약에
    걸린다. mock으로는 증명되지 않는다.
    """

    import threading

    with runtime_engine.begin() as connection:
        for table in _RUNTIME_TABLES:
            connection.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
    run = _run_id(runtime_engine)

    barrier = threading.Barrier(2)
    seqs: list[int] = []
    errors: list[BaseException] = []

    def _reserve() -> None:
        try:
            with runtime_engine.begin() as connection:
                barrier.wait(timeout=20)
                row = repo.reserve_tool_call(
                    connection, agent_run_id=run, tool_name="concurrent"
                )
                seqs.append(row.call_seq)
        except BaseException as exc:  # pragma: no cover - 진단용
            errors.append(exc)

    threads = [threading.Thread(target=_reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    assert errors == [], errors
    assert sorted(seqs) == [1, 2]
    with runtime_engine.connect() as connection:
        calls = repo.list_tool_calls(connection, run)
    assert [c.call_seq for c in calls] == [1, 2]
    assert len({c.tool_call_id for c in calls}) == 2


def test_a_different_run_is_not_blocked(engine: Any) -> None:
    """다른 run은 서로 막지 않는다 — lock 대상이 run row이기 때문이다."""

    first = _run_id(engine)
    second = _run_id(engine, lot_id="LOT-C02", chamber_id="EQP01-PM-C02")
    with engine.begin() as connection:
        a = repo.reserve_tool_call(connection, agent_run_id=first, tool_name="t")
        b = repo.reserve_tool_call(connection, agent_run_id=second, tool_name="t")
    assert (a.call_seq, b.call_seq) == (1, 1)


@pytest.mark.parametrize("key", repo.RESERVED_TOOL_OUTPUT_KEYS)
def test_reserved_metadata_keys_are_refused_before_sql(engine: Any, key: str) -> None:
    """실행 metadata와 domain payload를 섞지 않는다."""

    run = _run_id(engine)
    with engine.begin() as connection:
        reserved = repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t")
    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.finalize_tool_call(
                connection,
                tool_call_id=reserved.tool_call_id,
                agent_run_id=run,
                status=ToolCallStatus.SUCCESS,
                latency_ms=1,
                output={key: "x"},
            )
    assert exc.value.code == "RESERVED_OUTPUT_KEY"
    with engine.connect() as connection:
        [still] = repo.list_tool_calls(connection, run)
    assert still.error_msg == repo.RESERVED_ERROR_MSG, "예약 row가 바뀌었다"


# --- approval_request -------------------------------------------------------


def test_approval_request_and_audit_are_one_unit(engine: Any) -> None:
    action_id = _seed_action(engine)
    run = _run_id(engine)
    before = _counts(engine)["audit_log"]
    with engine.begin() as connection:
        approval = repo.create_approval_request(
            connection, action_id=action_id, agent_run_id=run
        )
    assert approval.status is ApprovalStatus.PENDING
    assert approval.decided_by is None
    assert _counts(engine)["audit_log"] == before + 1

    with engine.connect() as connection:
        assert repo.get_approval_request(connection, approval.approval_id) == approval
        entity = connection.execute(
            text(
                "SELECT entity_type, entity_id FROM audit_log "
                "ORDER BY audit_id DESC LIMIT 1"
            )
        ).one()
    assert (entity.entity_type, entity.entity_id) == ("APPROVAL", approval.approval_id)


def test_a_second_approval_for_one_action_is_a_conflict(engine: Any) -> None:
    """`approval_request_action_id_key` — action당 승인 요청 1건."""

    action_id = _seed_action(engine)
    run = _run_id(engine)
    with engine.begin() as connection:
        repo.create_approval_request(connection, action_id=action_id, agent_run_id=run)
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.create_approval_request(
                connection, action_id=action_id, agent_run_id=run
            )
    assert exc.value.code == "APPROVAL_ALREADY_EXISTS"


def test_approval_rollback_takes_the_audit_with_it(engine: Any) -> None:
    action_id = _seed_action(engine)
    run = _run_id(engine)
    before = _counts(engine)["audit_log"]
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            repo.create_approval_request(
                connection, action_id=action_id, agent_run_id=run
            )
            raise RuntimeError("보류")
    assert _counts(engine)["audit_log"] == before
    with engine.connect() as connection:
        total = connection.execute(
            text("SELECT count(*) FROM approval_request")
        ).scalar_one()
    assert total == 0


# --- action_delivery --------------------------------------------------------


HASH64 = "a" * 64


def test_delivery_rows_round_trip(engine: Any) -> None:
    action_id = _seed_action(engine)
    with engine.begin() as connection:
        email = repo.insert_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash=HASH64,
        )
        repo.insert_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.MES_MOCK,
            status=DeliveryStatus.BLOCKED,
            request_hash=HASH64,
        )
    assert email.attempt_count == 0
    assert email.started_at is None and email.completed_at is None
    with engine.connect() as connection:
        rows = repo.list_action_deliveries(connection, action_id)
        one = repo.get_action_delivery(
            connection, action_id=action_id, channel=DeliveryChannel.EMAIL
        )
    # 설계 §7.1의 EQP_HOLD 초기값 2행 그대로다.
    assert [r.channel for r in rows] == [
        DeliveryChannel.EMAIL,
        DeliveryChannel.MES_MOCK,
    ]
    assert [r.status for r in rows] == [
        DeliveryStatus.WAITING,
        DeliveryStatus.BLOCKED,
    ]
    assert one == email


def test_a_duplicate_delivery_is_a_conflict(engine: Any) -> None:
    """`action_delivery_pkey` — `(action_id, channel)` 재생성."""

    action_id = _seed_action(engine)
    with engine.begin() as connection:
        repo.insert_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash=HASH64,
        )
    with pytest.raises(repo.RepositoryConflict) as exc:
        with engine.begin() as connection:
            repo.insert_action_delivery(
                connection,
                action_id=action_id,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.WAITING,
                request_hash=HASH64,
            )
    assert exc.value.code == "DELIVERY_ALREADY_EXISTS"


@pytest.mark.parametrize("channel", list(DeliveryChannel))
@pytest.mark.parametrize("status", list(DeliveryStatus))
def test_only_the_two_designed_pairs_are_creatable(
    engine: Any, channel: DeliveryChannel, status: DeliveryStatus
) -> None:
    """**2 channel × 7 status를 전부 쓸어 본다**(구현리뷰 묶음 2 필수 1).

    설계 §7.1은 초기 조합을 `(EMAIL, WAITING)`·`(MES_MOCK, BLOCKED)` 둘로 고정한다.
    이전 판은 상태 목록만 보고 channel과 독립 검증해서 `MES_MOCK=WAITING`처럼
    **승인 전 전송 가능 상태**를 초기 INSERT로 만들 수 있었다.

    이전 단위 회귀가 `CANCELED`·`UNKNOWN`을 초기값으로 명시하고 있어 green이면서
    잘못된 계약을 고정했다. sweep으로 바꿔 양성 2조합만 통과하게 한다.
    """

    action_id = _seed_action(engine)
    expected = repo.INITIAL_DELIVERY_PAIRS[channel]

    if status is expected:
        with engine.begin() as connection:
            row = repo.insert_action_delivery(
                connection,
                action_id=action_id,
                channel=channel,
                status=status,
                request_hash=HASH64,
            )
        assert (row.channel, row.status) == (channel, status)
        return

    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.insert_action_delivery(
                connection,
                action_id=action_id,
                channel=channel,
                status=status,
                request_hash=HASH64,
            )
    assert exc.value.code == "NOT_INITIAL_DELIVERY_PAIR"
    with engine.connect() as connection:
        assert repo.list_action_deliveries(connection, action_id) == []


@pytest.mark.parametrize(
    ("bad_hash", "code"),
    [
        ("A" * 64, "INVALID_REQUEST_HASH"),
        ("a" * 63, "INVALID_REQUEST_HASH"),
        ("a" * 65, "REQUEST_HASH_TOO_LONG"),
        ("  ", "EMPTY_REQUEST_HASH"),
    ],
)
def test_bad_request_hash_never_reaches_the_database(
    engine: Any, bad_hash: str, code: str
) -> None:
    action_id = _seed_action(engine)
    with pytest.raises(repo.RepositoryContractError) as exc:
        with engine.begin() as connection:
            repo.insert_action_delivery(
                connection,
                action_id=action_id,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.WAITING,
                request_hash=bad_hash,
            )
    assert exc.value.code == code
    with engine.connect() as connection:
        assert repo.list_action_deliveries(connection, action_id) == []


# --- 9 table coverage -------------------------------------------------------


def test_every_runtime_table_is_exercised(engine: Any) -> None:
    """설계 §3.4의 9 table이 **전부** insert/select 경로를 지난다.

    계획 §10의 마지막 항목이다. 하나라도 안 지나면 그 table의 Repository 책임이
    실제로 도는지 알 수 없다.
    """

    action_id = _seed_action(engine)
    run = _run_id(engine)
    with engine.begin() as connection:
        repo.insert_prediction(
            connection,
            agent_run_id=run,
            predicted_fault_code=FaultHypothesis.FOC,
            confidence=0.9,
            cause_summary="원인",
            evidence={},
            llm_model="m",
            prompt_version="v",
        )
        repo.insert_human_prediction_review(
            connection,
            agent_run_id=run,
            disposition="ACCEPTED",
            label_source="HUMAN_REVIEW",
            reviewer="r",
        )
        repo.link_run_action(
            connection,
            agent_run_id=run,
            action_id=action_id,
            link_role=ActionLinkRole.CREATED,
            lot_id="LOT-C01",
            chamber_id="EQP01-PM-C01",
            trigger_alarm=_ref("TA-01"),
        )
        repo.create_approval_request(connection, action_id=action_id, agent_run_id=run)
        repo.insert_action_delivery(
            connection,
            action_id=action_id,
            channel=DeliveryChannel.EMAIL,
            status=DeliveryStatus.WAITING,
            request_hash=HASH64,
        )
        reserved = repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t")
    with engine.begin() as connection:
        repo.finalize_tool_call(
            connection,
            tool_call_id=reserved.tool_call_id,
            agent_run_id=run,
            status=ToolCallStatus.SUCCESS,
            latency_ms=10,
            output={"ok": True},
        )

    with engine.connect() as connection:
        rows = {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            for table in (
                "agent_run",
                "agent_run_alarm",
                "agent_prediction",
                "agent_prediction_review",
                "agent_run_action",
                "agent_tool_call",
                "approval_request",
                "action_delivery",
                "audit_log",
            )
        }
    assert all(count >= 1 for count in rows.values()), rows


# --- CONFLICT_CODES 전체 실측 (묶음 2 필수 2 · 권장 1) -----------------------


def test_alarm_pkey_violation_maps_to_a_stable_code(engine: Any) -> None:
    """**`agent_run_alarm_pkey`를 실제 위반으로 확인한다**(구현리뷰 묶음 2 필수 2).

    구현보고 §14.2가 "묶음 1에서 확인"이라 적었지만 묶음 1은
    `ux_agent_run_alarm_representative`만 봤다. `ALARM_ALREADY_LINKED`를 assert하는
    곳이 **0개**였다.

    대표 index와 겹치지 않게 **비대표** 행을 중복 INSERT해 PK만 위반시킨다.
    """

    from sqlalchemy.exc import IntegrityError

    run = _run_id(engine)
    insert = (
        "INSERT INTO agent_run_alarm "
        "(agent_run_id, alarm_source, alarm_id, is_representative) "
        "VALUES (:run, 'R03', 'R3-DUP', false)"
    )
    with engine.begin() as connection:
        connection.execute(text(insert), {"run": run})

    with pytest.raises(IntegrityError) as exc:
        with engine.begin() as connection:
            connection.execute(text(insert), {"run": run})

    name = repo._constraint_name(exc.value)
    assert name == "agent_run_alarm_pkey"
    translated = repo._translate(exc.value)
    assert isinstance(translated, repo.RepositoryConflict)
    assert translated.code == "ALARM_ALREADY_LINKED"


def test_tool_call_sequence_conflict_maps_to_a_stable_code(engine: Any) -> None:
    """`agent_tool_call_agent_run_id_call_seq_key` — 이름이 자동 명명이라 실측한다."""

    from sqlalchemy.exc import IntegrityError

    run = _run_id(engine)
    with engine.begin() as connection:
        repo.reserve_tool_call(connection, agent_run_id=run, tool_name="t")

    with pytest.raises(IntegrityError) as exc:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_tool_call "
                    "(tool_call_id, agent_run_id, call_seq, tool_name, status) "
                    "VALUES (:id, :run, 1, 'dup', 'SUCCESS')"
                ),
                {"id": "TOOL-" + "1" * 24, "run": run},
            )
    assert repo._constraint_name(exc.value) == (
        "agent_tool_call_agent_run_id_call_seq_key"
    )
    assert repo._translate(exc.value).code == "TOOL_CALL_SEQUENCE_CONFLICT"


def test_action_already_created_maps_to_a_stable_code(engine: Any) -> None:
    """`ux_agent_run_action_created` — action당 `CREATED` link 1건."""

    from sqlalchemy.exc import IntegrityError

    action_id = _seed_action(engine)
    first = _run_id(engine)
    second = _run_id(engine, lot_id="LOT-C02", chamber_id="EQP01-PM-C02")
    with engine.begin() as connection:
        repo.link_run_action(
            connection,
            agent_run_id=first,
            action_id=action_id,
            link_role=ActionLinkRole.CREATED,
            lot_id="LOT-C01",
            chamber_id="EQP01-PM-C01",
            trigger_alarm=_ref("TA-01"),
        )
    # 다른 incident의 run이 **같은 action**을 CREATED로 다시 link한다.
    with pytest.raises(IntegrityError) as exc:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agent_run_action (agent_run_id, action_id, "
                    " link_role, lot_id, chamber_id, trigger_alarm_source, "
                    " trigger_alarm_id) VALUES (:run, :action, 'CREATED', "
                    " 'LOT-C02', 'EQP01-PM-C02', 'TRACE', 'TA-02')"
                ),
                {"run": second, "action": action_id},
            )
    assert repo._constraint_name(exc.value) == "ux_agent_run_action_created"
    assert repo._translate(exc.value).code == "ACTION_ALREADY_CREATED"


def test_every_declared_conflict_name_exists_in_the_catalog(engine: Any) -> None:
    """**선언한 이름 9종이 전부 실재한다**(구현리뷰 묶음 2 권장 1).

    이름 하나가 오타여도 `_translate()`는 조용히 `CONSTRAINT_VIOLATION`으로 떨어진다 —
    그 상태는 어떤 테스트도 red로 만들지 않는다. catalog에서 직접 확인한다.
    """

    with engine.connect() as connection:
        known = {
            row.conname
            for row in connection.execute(
                text("SELECT conname FROM pg_constraint")
            ).all()
        } | {
            row.indexname
            for row in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            ).all()
        }
    missing = sorted(set(repo.CONFLICT_CODES) - known)
    assert missing == [], missing


# ===========================================================================
# 구현리뷰 필수 1·2 — 실제 DB에서만 성립하는 축
# ===========================================================================

MISSING_RUN = "RUN-c01ffffffffffff"
MISSING_ACTION = "ACT-c01ffffffffffff"


def test_every_declared_foreign_key_name_exists_in_the_catalog(engine: Any) -> None:
    """**선언한 FK 이름이 전부 실재한다.**

    `002`가 FK에 이름을 주지 않아 PostgreSQL 자동 명명에 의존한다. 이름 하나가
    어긋나면 `_insert_one()`은 조용히 `CONSTRAINT_VIOLATION`으로 떨어지고, 그
    상태는 어떤 성공 경로 테스트도 red로 만들지 못한다.
    """

    with engine.connect() as connection:
        known = {
            row.conname
            for row in connection.execute(
                text("SELECT conname FROM pg_constraint WHERE contype = 'f'")
            ).all()
        }
    missing = sorted(set(repo.FOREIGN_KEY_CODES) - known)
    assert missing == [], missing


def test_a_review_for_a_missing_run_says_the_run_is_missing(engine: Any) -> None:
    """이전에는 `CONSTRAINT_VIOLATION`이었다 — CHECK 위반과 같은 코드다."""

    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as exc:
            repo.insert_human_prediction_review(
                connection,
                agent_run_id=MISSING_RUN,
                reviewed_fault_code=FaultHypothesis.RFM,
                disposition="CORRECTED",
                label_source="HUMAN_REVIEW",
                reviewer="qa",
            )
    assert exc.value.code == "RUN_NOT_FOUND"


def test_a_check_violation_and_a_missing_parent_are_now_different(
    engine: Any,
) -> None:
    """**같은 table의 두 실패가 서로 다른 code다.**

    이 대조가 없으면 "FK를 NotFound로 올린다"가 CHECK까지 삼켰는지 알 수 없다.
    """

    run = _run_id(engine)
    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryContractError) as check:
            repo.insert_human_prediction_review(
                connection,
                agent_run_id=run,
                reviewed_fault_code=None,
                disposition="CORRECTED",
                label_source="HUMAN_REVIEW",
                reviewer="qa",
            )
    assert check.value.code == "CONSTRAINT_VIOLATION"

    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as missing:
            repo.insert_human_prediction_review(
                connection,
                agent_run_id=MISSING_RUN,
                reviewed_fault_code=FaultHypothesis.RFM,
                disposition="CORRECTED",
                label_source="HUMAN_REVIEW",
                reviewer="qa",
            )
    assert missing.value.code == "RUN_NOT_FOUND"
    assert check.value.code != missing.value.code


def test_a_link_names_which_parent_is_missing(engine: Any) -> None:
    """**단일 `missing_code`로는 표현할 수 없던 구분이다.**

    `agent_run_action`은 FK가 둘이다. 없는 것이 run인지 action인지에 따라 상위가
    다른 응답을 내야 한다.
    """

    action_id = _seed_action(engine)
    run = _run_id(engine)

    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as no_action:
            repo.link_run_action(
                connection,
                agent_run_id=run,
                action_id=MISSING_ACTION,
                link_role=ActionLinkRole.CREATED,
                lot_id="LOT-C01",
                chamber_id="EQP01-PM-C01",
                trigger_alarm=_ref("TA-01"),
            )
    assert no_action.value.code == "ACTION_NOT_FOUND"

    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as no_run:
            repo.link_run_action(
                connection,
                agent_run_id=MISSING_RUN,
                action_id=action_id,
                link_role=ActionLinkRole.CREATED,
                lot_id="LOT-C01",
                chamber_id="EQP01-PM-C01",
                trigger_alarm=_ref("TA-01"),
            )
    assert no_run.value.code == "RUN_NOT_FOUND"


def test_a_delivery_for_a_missing_action_says_so(engine: Any) -> None:
    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as exc:
            repo.insert_action_delivery(
                connection,
                action_id=MISSING_ACTION,
                channel=DeliveryChannel.EMAIL,
                status=DeliveryStatus.WAITING,
                request_hash="a" * 64,
            )
    assert exc.value.code == "ACTION_NOT_FOUND"


def test_an_approval_for_a_missing_action_says_so(engine: Any) -> None:
    """리뷰가 지목하지 않았지만 같은 결함이 있던 자리다."""

    run = _run_id(engine)
    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as exc:
            repo.create_approval_request(
                connection, action_id=MISSING_ACTION, agent_run_id=run
            )
    assert exc.value.code == "ACTION_NOT_FOUND"


def test_a_prediction_for_a_missing_run_says_so(engine: Any) -> None:
    """`# pragma: no cover`로 표시만 해 뒀던 자리다 — 이제 실제로 도달한다."""

    with engine.begin() as connection:
        with pytest.raises(repo.RepositoryNotFound) as exc:
            repo.insert_prediction(
                connection,
                agent_run_id=MISSING_RUN,
                predicted_fault_code=FaultHypothesis.RFM,
                confidence=0.5,
                cause_summary="missing run",
                evidence={"alarm": "TA-01"},
                llm_model="claude",
                prompt_version="v1",
            )
    assert exc.value.code == "RUN_NOT_FOUND"


def test_lock_expiry_is_retryable_not_an_outage(runtime_engine: Any) -> None:
    """**이 Task가 만든 상황을 그대로 재현한다**(구현리뷰 필수 2).

    `reserve_tool_call()`의 `FOR UPDATE`가 같은 run의 예약을 직렬화한다. 그래서
    `lock_timeout`이 걸린 DB에서는 두 번째 예약이 `55P03`으로 만료된다. psycopg는
    그것을 `OperationalError` 아래 두므로 이전에는 `DATABASE_UNAVAILABLE`이었다 —
    상위가 503으로 올리면 재시도하면 될 것을 장애로 보고한다.
    """

    with runtime_engine.begin() as connection:
        for table in _RUNTIME_TABLES:
            connection.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
    run = _run_id(runtime_engine)

    with runtime_engine.begin() as holder:
        repo.reserve_tool_call(holder, agent_run_id=run, tool_name="holder")
        with runtime_engine.connect() as waiter:
            with waiter.begin():
                waiter.execute(text("SET LOCAL lock_timeout = '200ms'"))
                with pytest.raises(repo.RepositoryRetryable) as exc:
                    repo.reserve_tool_call(waiter, agent_run_id=run, tool_name="wait")

    assert exc.value.code == "LOCK_NOT_AVAILABLE"
    assert not isinstance(exc.value, repo.RepositoryUnavailable)


def test_a_deadlock_is_retryable_not_an_outage(runtime_engine: Any) -> None:
    """서로 반대 순서로 두 run을 잠가 실제 deadlock을 만든다.

    PostgreSQL이 한쪽을 `40P01`로 중단한다. 나머지 한쪽은 성공해야 한다 — 둘 다
    실패하면 그것은 경합이 아니라 다른 문제다.
    """

    import threading

    with runtime_engine.begin() as connection:
        for table in _RUNTIME_TABLES:
            connection.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
    # **incident key가 서로 달라야 한다.** 같은 lot·chamber·대표 알람이면 두 번째
    # run이 `ux_agent_run_incident_active`에 걸려 deadlock까지 가지 못한다.
    first = _run_id(runtime_engine)
    second = _run_id(
        runtime_engine,
        lot_id="LOT-C01-B",
        requested_alarm=_ref("TA-02"),
        representative_alarm=_ref("TA-02"),
        member_alarms=(_ref("TA-02"),),
    )

    barrier = threading.Barrier(2)
    outcomes: list[BaseException | None] = []

    def _cross(a: str, b: str) -> None:
        try:
            with runtime_engine.begin() as connection:
                repo.reserve_tool_call(connection, agent_run_id=a, tool_name="cross-1")
                barrier.wait(timeout=20)
                repo.reserve_tool_call(connection, agent_run_id=b, tool_name="cross-2")
            outcomes.append(None)
        except BaseException as exc:  # noqa: BLE001 - 진단용
            outcomes.append(exc)

    threads = [
        threading.Thread(target=_cross, args=(first, second)),
        threading.Thread(target=_cross, args=(second, first)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    failures = [item for item in outcomes if item is not None]
    assert len(outcomes) == 2
    assert len(failures) == 1, outcomes
    assert isinstance(failures[0], repo.RepositoryRetryable), failures[0]
    assert failures[0].code == "DEADLOCK_DETECTED"
    assert not isinstance(failures[0], repo.RepositoryUnavailable)
