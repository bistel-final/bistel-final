"""감사 쓰기 계약 (`V5-CM-4.2`).

Common이 소유하는 것은 **append 계약 하나**다. 실제 event 기록은 A·C가, 조회는 D가
소유한다. 그래서 이 파일은 "무엇을 기록하는가"가 아니라 **"어떻게 기록해야만 하는가"**를
잠근다.

정본: API 명세 §6·§3.8 · 시스템설계 §11·§12.1 · `002_agent_runtime_clean.sql`의
`audit_log` CHECK.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import count
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import event, text

from app.common import audit as audit_module
from app.common.audit import (
    EVENT_ENTITY_TYPE,
    AuditContractError,
    AuditEntityType,
    AuditEvent,
    AuditRecord,
    append_audit_log,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = REPO_ROOT / "backend/migrations/002_agent_runtime_clean.sql"
API_SPEC = REPO_ROOT / "docs/deliverables/api/API명세서_v3_작업본.md"

#: 정본 9쌍. 세 곳(코드·migration·API 명세)이 모두 이것과 같아야 한다.
CANONICAL_MAPPING = {
    "DETECTION_COMPLETED": "LOT_HIST",
    "AGENT_RUN_STARTED": "AGENT_RUN",
    "HYPOTHESIS_GENERATED": "AGENT_RUN",
    "APPROVAL_REQUESTED": "APPROVAL",
    "APPROVAL_DECIDED": "APPROVAL",
    "ACTION_SENT": "ACTION",
    "ACTION_SEND_FAILED": "ACTION",
    "AGENT_RUN_COMPLETED": "AGENT_RUN",
    "AGENT_RUN_FAILED": "AGENT_RUN",
}


#: `audit_log` CHECK의 `(event_type, entity_type)` 쌍만 격리해 센다.
#:
#: **파일 전체에 regex를 걸면 안 된다.** `002_agent_runtime_clean.sql`에는 감사와 무관한
#: 2문자열 CHECK가 더 있어(`CREATED→REUSED`·`APPROVED→REJECTED`·`EMAIL→MES_MOCK`·
#: `RUNNING→WAITING_APPROVAL`) 13쌍이 나온다.
_AUDIT_CHECK_START = re.compile(r"\(event_type,\s*entity_type\)\s*IN\s*\(")
_SQL_PAIR = re.compile(r"\(\s*'([A-Z_]+)'\s*,\s*'([A-Z_]+)'\s*\)")


def _balanced_body(text: str, open_index: int) -> str:
    """`(`가 열린 위치에서 짝이 맞는 `)`까지를 돌려준다.

    비탐욕 regex로 자르면 마지막 쌍의 닫는 괄호를 잘라먹어 9쌍이 8쌍이 된다.
    """

    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index]
    raise AssertionError("괄호가 닫히지 않았다")


_API_AUDIT_SECTION = re.compile(
    r"^## 6\. 감사 이벤트\s*$(?P<body>.*?)^## 7\.", re.DOTALL | re.MULTILINE
)
_API_PAIR = re.compile(r"^\|\s*`([A-Z_]+)`\s*\|\s*`([A-Z_]+)`\s*\|", re.MULTILINE)


def _migration_audit_pairs() -> Counter[tuple[str, str]]:
    """중복도 세도록 `Counter`로 돌려준다 — dict로 축약하면 중복 row가 사라진다."""

    body = MIGRATION.read_text(encoding="utf-8")
    match = _AUDIT_CHECK_START.search(body)
    assert match, "audit_log의 (event_type, entity_type) CHECK를 찾지 못했다"

    return Counter(_SQL_PAIR.findall(_balanced_body(body, match.end() - 1)))


def _api_spec_audit_pairs() -> Counter[tuple[str, str]]:
    match = _API_AUDIT_SECTION.search(API_SPEC.read_text(encoding="utf-8"))
    assert match, "API 명세 §6 절을 찾지 못했다"

    return Counter(_API_PAIR.findall(match.group("body")))


def _action_record(**overrides: Any) -> AuditRecord:
    payload: dict[str, Any] = {
        "event_type": AuditEvent.ACTION_SENT,
        "actor_type": "AGENT",
        "entity_id": "ACT-0001",
        "after": {"channel": "MES_MOCK", "transport": "KAFKA"},
    }
    payload.update(overrides)
    return AuditRecord(**payload)


# ─────────────────────────────────────────────────────────────────────────────
# 1. event ↔ entity mapping은 세 곳이 같아야 한다
# ─────────────────────────────────────────────────────────────────────────────


class TestEventEntityMapping:
    def test_exactly_nine_events_in_declared_order(self) -> None:
        assert [event.value for event in AuditEvent] == list(CANONICAL_MAPPING)

    def test_python_mapping_matches_canonical(self) -> None:
        actual = {e.value: t.value for e, t in EVENT_ENTITY_TYPE.items()}

        assert actual == CANONICAL_MAPPING

    def test_migration_check_pairs_match_canonical(self) -> None:
        """**migration CHECK가 정확히 이 9쌍이다 — 그 이상도 이하도 아니다.**

        canonical key를 하나씩 조회하면 10번째 조합이 CHECK에 추가돼도 통과한다.
        DB가 Common이 지원하지 않는 event를 받아들이는 상태가 green으로 남는다
        (구현리뷰 필수 1).
        """

        assert _migration_audit_pairs() == Counter(CANONICAL_MAPPING.items())

    def test_api_spec_table_matches_canonical(self) -> None:
        """**§6 절만 격리해서 본다.**

        문서 전체를 훑으면 다른 절의 2열 코드 표까지 섞이고, 조회식 단언은 추가된 row를
        드러내지 못한다(구현리뷰 필수 1).
        """

        assert _api_spec_audit_pairs() == Counter(CANONICAL_MAPPING.items())

    def test_entity_types_are_exactly_four(self) -> None:
        assert {t.value for t in AuditEntityType} == set(CANONICAL_MAPPING.values())

    def test_mapping_cannot_be_mutated_at_runtime(self) -> None:
        """entity_type은 event의 성질이지 실행 중 바꿀 설정이 아니다."""

        with pytest.raises(TypeError):
            EVENT_ENTITY_TYPE[AuditEvent.ACTION_SENT] = AuditEntityType.APPROVAL  # type: ignore[index]

    def test_legacy_events_do_not_exist(self) -> None:
        for legacy in ("CLASSIFICATION_COMPLETED", "ACTION_SKIPPED"):
            with pytest.raises(ValueError):
                AuditEvent(legacy)


# ─────────────────────────────────────────────────────────────────────────────
# 2. record 경계 — DB에 닿기 전에 막는다
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditRecordBoundary:
    def test_entity_type_is_derived_not_supplied(self) -> None:
        record = AuditRecord(
            event_type=AuditEvent.APPROVAL_DECIDED,
            actor_type="HUMAN",
            entity_id="APR-0001",
        )

        assert record.entity_type is AuditEntityType.APPROVAL

    def test_entity_type_cannot_be_overridden(self) -> None:
        with pytest.raises(ValidationError):
            AuditRecord(
                event_type=AuditEvent.APPROVAL_DECIDED,
                actor_type="HUMAN",
                entity_id="APR-0001",
                entity_type="ACTION",
            )

    def test_occurred_at_cannot_be_injected(self) -> None:
        """**append 시각은 caller가 정하지 않는다.**

        과거·미래 시각을 주입할 수 있으면 감사 원본의 의미가 사라진다.
        """

        with pytest.raises(ValidationError):
            AuditRecord(
                event_type=AuditEvent.APPROVAL_DECIDED,
                actor_type="HUMAN",
                entity_id="APR-0001",
                occurred_at="2020-01-01T00:00:00Z",
            )

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditRecord(
                event_type=AuditEvent.APPROVAL_DECIDED,
                actor_type="HUMAN",
                entity_id="APR-0001",
                surprise=1,
            )

    @pytest.mark.parametrize("entity_id", ["", "   ", "\t\n"])
    def test_entity_id_rejects_blank(self, entity_id: str) -> None:
        with pytest.raises(ValidationError):
            _action_record(entity_id=entity_id)

    @pytest.mark.parametrize("actor_id", ["", "   ", "\t"])
    def test_actor_id_rejects_blank(self, actor_id: str) -> None:
        with pytest.raises(ValidationError):
            _action_record(actor_id=actor_id)

    def test_ids_are_stripped(self) -> None:
        record = _action_record(entity_id="  ACT-0001  ", actor_id="  operator  ")

        assert (record.entity_id, record.actor_id) == ("ACT-0001", "operator")

    def test_actor_id_is_optional(self) -> None:
        assert _action_record().actor_id is None

    # DB varchar 경계. 넘겨 보내면 DataError로만 알 수 있고 그때는 이미 caller의
    # 업무 트랜잭션이 열려 있다.
    @pytest.mark.parametrize(("field", "limit"), [("entity_id", 20), ("actor_id", 40)])
    def test_id_length_boundary_matches_db_column(self, field: str, limit: int) -> None:
        assert _action_record(**{field: "A" * limit})

        with pytest.raises(ValidationError):
            _action_record(**{field: "A" * (limit + 1)})

    @pytest.mark.parametrize(("column", "limit"), [("entity_id", 20), ("actor_id", 40)])
    def test_declared_limits_match_the_migration(self, column: str, limit: int) -> None:
        """DTO 경계가 DDL에서 온 값인지 확인한다."""

        body = MIGRATION.read_text(encoding="utf-8")

        assert re.search(rf"\b{column} varchar\({limit}\)", body), column


# ─────────────────────────────────────────────────────────────────────────────
# 3. ACTION event의 최소 payload (시스템설계 §11)
# ─────────────────────────────────────────────────────────────────────────────


class TestActionDeliveryContext:
    @pytest.mark.parametrize(
        "event_type", [AuditEvent.ACTION_SENT, AuditEvent.ACTION_SEND_FAILED]
    )
    def test_channel_and_transport_are_required(self, event_type: AuditEvent) -> None:
        assert _action_record(event_type=event_type)

    @pytest.mark.parametrize(
        "event_type", [AuditEvent.ACTION_SENT, AuditEvent.ACTION_SEND_FAILED]
    )
    @pytest.mark.parametrize(
        "after",
        [
            None,
            {},
            {"channel": "MES_MOCK"},
            {"transport": "KAFKA"},
            {"channel": "  ", "transport": "KAFKA"},
            {"channel": "MES_MOCK", "transport": ""},
            {"channel": "MES_MOCK", "transport": None},
            {"channel": 1, "transport": "KAFKA"},
        ],
    )
    def test_missing_or_blank_delivery_context_is_rejected(
        self, event_type: AuditEvent, after: dict[str, Any] | None
    ) -> None:
        with pytest.raises(ValidationError):
            _action_record(event_type=event_type, after=after)

    @pytest.mark.parametrize(
        "event_type",
        [e for e in AuditEvent if not e.value.startswith("ACTION_")],
    )
    def test_non_action_events_are_not_burdened(self, event_type: AuditEvent) -> None:
        """**근거 없는 요구를 다른 event로 넓히지 않는다.**

        설계 §11이 channel·transport를 요구한 것은 ACTION 두 event뿐이다.
        """

        assert AuditRecord(
            event_type=event_type, actor_type="SYSTEM", entity_id="LOT-0001"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. append helper — transaction ownership과 시각
# ─────────────────────────────────────────────────────────────────────────────


def _sqlite_engine() -> sa.Engine:
    """공용 DB 대체가 아니다.

    helper의 **두 기계적 성질**만 본다 — connect 직후 상태에서 SQL을 실행하지 않는지,
    한 transaction 안의 두 append가 서로 다른 시각을 남기는지. PostgreSQL 함수의 의미는
    SQL 정적 검사(`TestAppendOnlySurface`)가 고정한다.
    """

    engine = sa.create_engine("sqlite://")
    tick = count(1)

    @event.listens_for(engine, "connect")
    def _register(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.create_function(
            "clock_timestamp", 0, lambda: f"2026-08-24T00:00:{next(tick):02d}Z"
        )

    return engine


#: `_INSERT_AUDIT_LOG`의 bind parameter 등장 순서. DBAPI가 위치 인자로 넘길 때 쓴다.
_INSERT_BIND_ORDER = (
    "actor_type",
    "actor_id",
    "event_type",
    "entity_type",
    "entity_id",
    "before_json",
    "after_json",
    "detail",
)


def _create_audit_log(connection: sa.Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE audit_log (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                before_json,
                after_json,
                detail TEXT
            )
            """
        )
    )


class TestTransactionOwnership:
    def test_untouched_connection_is_refused_before_any_sql(self) -> None:
        """**감사 INSERT가 unit of work의 첫 statement가 되는 실수를 막는다.**

        그 경우 `with engine.connect()` 블록이 commit 없이 끝나면 감사가 조용히
        사라진다. `MagicMock`으로는 이 계약을 검증할 수 없다 — mock의
        `in_transaction()`은 기본값이 truthy라 가드를 지워도 통과한다.
        """

        statements: list[str] = []
        engine = _sqlite_engine()

        @event.listens_for(engine, "before_cursor_execute")
        def _spy(_c: Any, _cur: Any, statement: str, *_rest: Any) -> None:
            statements.append(statement)

        with engine.connect() as connection:
            assert connection.in_transaction() is False

            with pytest.raises(AuditContractError):
                append_audit_log(connection, _action_record())

            assert statements == []

    def test_two_appends_in_one_transaction_get_distinct_times(self) -> None:
        """**`clock_timestamp()`이며 `now()`가 아니다.**

        PostgreSQL `now()`는 `transaction_timestamp()`라 한 트랜잭션 안에서 움직이지
        않는다. 설계 §12.1의 advisory lock 획득·재조회·DML이 한 트랜잭션이므로 lock
        대기 시간만큼 실제 event 시각과 벌어진다.
        """

        engine = _sqlite_engine()
        with engine.connect() as connection:
            _create_audit_log(connection)
            assert connection.in_transaction() is True

            first = append_audit_log(connection, _action_record(entity_id="ACT-0001"))
            second = append_audit_log(connection, _action_record(entity_id="ACT-0002"))

            rows = connection.execute(
                text("SELECT audit_id, occurred_at FROM audit_log ORDER BY audit_id")
            ).all()

        assert [first, second] == [1, 2]
        assert rows[0].occurred_at != rows[1].occurred_at

    def test_helper_never_ends_the_caller_transaction(self) -> None:
        calls: list[str] = []
        engine = _sqlite_engine()

        @event.listens_for(engine, "commit")
        def _on_commit(_conn: Any) -> None:
            calls.append("commit")

        @event.listens_for(engine, "rollback")
        def _on_rollback(_conn: Any) -> None:
            calls.append("rollback")

        with engine.connect() as connection:
            _create_audit_log(connection)
            append_audit_log(connection, _action_record())

            assert calls == []
            assert connection.in_transaction() is True
            assert connection.closed is False


class TestInsertParameters:
    def test_canonical_parameters_are_written_once(self) -> None:
        engine = _sqlite_engine()
        with engine.connect() as connection:
            _create_audit_log(connection)
            append_audit_log(
                connection,
                AuditRecord(
                    event_type=AuditEvent.APPROVAL_DECIDED,
                    actor_type="HUMAN",
                    entity_id="APR-0001",
                    actor_id="operator",
                    before={"status": "PENDING"},
                    after={"status": "APPROVED"},
                    detail="승인",
                ),
            )
            row = connection.execute(text("SELECT * FROM audit_log")).one()

        assert row.event_type == "APPROVAL_DECIDED"
        # entity_type은 caller 입력이 아니라 mapping에서 온다.
        assert row.entity_type == "APPROVAL"
        assert (row.entity_id, row.actor_id, row.actor_type) == (
            "APR-0001",
            "operator",
            "HUMAN",
        )
        assert row.detail == "승인"

    def test_null_payload_is_sql_null_and_objects_are_sorted_json(self) -> None:
        """**저장값이 아니라 바인딩 파라미터를 본다.**

        SQLite에서 `CAST(... AS jsonb)`는 NUMERIC affinity가 되어 JSON 문자열을 0으로
        강제한다. 그 강제는 fixture의 성질이지 helper의 계약이 아니므로, helper가
        무엇을 넘겼는지를 직접 확인한다. jsonb 왕복은 PostgreSQL을 쓰는
        `V5-C-0.1` 통합 테스트가 소유한다.
        """

        captured: list[Any] = []
        engine = _sqlite_engine()

        @event.listens_for(engine, "before_cursor_execute")
        def _spy(_c: Any, _cur: Any, _stmt: str, parameters: Any, *_rest: Any) -> None:
            captured.append(parameters)

        with engine.connect() as connection:
            _create_audit_log(connection)
            captured.clear()
            append_audit_log(
                connection,
                AuditRecord(
                    event_type=AuditEvent.AGENT_RUN_STARTED,
                    actor_type="AGENT",
                    entity_id="RUN-0001",
                    after={"b": 2, "a": 1},
                ),
            )

        (parameters,) = captured
        values = (
            parameters
            if isinstance(parameters, dict)
            else dict(zip(_INSERT_BIND_ORDER, parameters, strict=True))
        )

        assert values["before_json"] is None
        # key 정렬 — 같은 payload는 항상 같은 문자열이어야 감사 원본을 비교할 수 있다.
        assert values["after_json"] == '{"a": 1, "b": 2}'

    def test_json_dump_is_deterministic_and_preserves_non_ascii(self) -> None:
        assert audit_module._dump_json(None) is None
        assert audit_module._dump_json({"b": 1, "a": "설비"}) == (
            '{"a": "설비", "b": 1}'
        )

    def test_db_error_propagates_unchanged(self) -> None:
        """감사 실패를 성공처럼 바꾸지 않는다.

        caller가 업무 변경과 함께 rollback해야 하기 때문이다.
        """

        engine = _sqlite_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))  # table을 만들지 않는다

            with pytest.raises(sa.exc.OperationalError):
                append_audit_log(connection, _action_record())


# ─────────────────────────────────────────────────────────────────────────────
# 5. append-only surface
# ─────────────────────────────────────────────────────────────────────────────


class TestAppendOnlySurface:
    def test_every_sql_constant_is_a_single_insert(self) -> None:
        """**SQL 상수를 전부 열거한다.**

        하나만 골라 보면 나중에 추가된 UPDATE 상수를 놓친다.
        """

        clauses = [
            value
            for value in vars(audit_module).values()
            if isinstance(value, sa.TextClause)
        ]

        assert clauses, "감사 SQL 상수가 없다"
        for clause in clauses:
            sql = str(clause).strip().upper()
            assert sql.startswith("INSERT INTO AUDIT_LOG")
            assert sql.count("INSERT") == 1
            assert "RETURNING AUDIT_ID" in sql
            for forbidden in ("UPDATE ", "DELETE ", "TRUNCATE", "DROP "):
                assert forbidden not in sql, forbidden

    def test_insert_records_statement_time_not_transaction_time(self) -> None:
        sql = str(audit_module._INSERT_AUDIT_LOG).lower()

        assert "clock_timestamp()" in sql
        for scoped in ("now()", "transaction_timestamp", "current_timestamp"):
            assert scoped not in sql, scoped

    def test_no_public_update_or_delete_callable(self) -> None:
        offenders = [
            name
            for name in dir(audit_module)
            if not name.startswith("_")
            and callable(getattr(audit_module, name))
            and re.match(r"(update|delete|remove|purge|drop)", name, re.IGNORECASE)
        ]

        assert offenders == []

    def test_append_is_the_only_public_write_entry_point(self) -> None:
        writers = [
            name
            for name in dir(audit_module)
            if not name.startswith("_")
            and callable(getattr(audit_module, name))
            and getattr(getattr(audit_module, name), "__module__", "")
            == audit_module.__name__
            and not isinstance(getattr(audit_module, name), type)
        ]

        assert writers == ["append_audit_log"]
