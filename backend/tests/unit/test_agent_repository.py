"""`V5-C-0.1` Runtime Repository 단위 회귀 — 묶음 1.

DB 없이 확인할 수 있는 것만 여기서 본다. transaction 원자성·제약 충돌·동시성은
실제 PostgreSQL이 필요하므로 `test_agent_repository_container.py`가 소유한다.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from dataclasses import MISSING
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import repository as repo  # noqa: E402
from app.common.enums import (  # noqa: E402
    ActionCode,
    AlarmSource,
    ApprovalStatus,
    DeliveryChannel,
    DeliveryStatus,
    FaultHypothesis,
    RunStatus,
    Severity,
    ToolCallStatus,
    resolve_severity,
)
from app.common.schemas import AlarmRef  # noqa: E402

TRACE = AlarmSource.TRACE


def _code_only(module_path: Path) -> str:
    """**docstring을 뺀 본문 코드만** 문자열로 낸다.

    `ast.unparse()`는 docstring을 그대로 담는다. 그래서 "`commit`을 부르지 않는다"라고
    적은 설명이 `commit` 호출로 오인된다. 주석은 애초에 AST에 없다.
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


def _ref(alarm_id: str, source: AlarmSource = TRACE) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


def _command(**overrides: Any) -> repo.CreateAgentRunCommand:
    members = (_ref("A1"), _ref("A2"))
    payload: dict[str, Any] = {
        "thread_id": "11111111-2222-3333-4444-555555555555",
        "lot_id": "LOT-1",
        "chamber_id": "CH-1",
        "autonomy_level": 2,
        "requested_alarm": members[0],
        "representative_alarm": members[0],
        "member_alarms": members,
        "llm_model": "test-model",
    }
    payload.update(overrides)
    return repo.CreateAgentRunCommand(**payload)


def _timed_run(
    *,
    status: RunStatus,
    latency_ms: int,
    evidence: dict[str, Any] | None = None,
) -> repo.AgentRunRow:
    started = datetime(2026, 8, 29, 1, 0, tzinfo=UTC)
    alarm = _ref("A1")
    return repo.AgentRunRow(
        agent_run_id="RUN-0000000000000001",
        thread_id="11111111-2222-3333-4444-555555555555",
        lot_id="LOT-1",
        chamber_id="CH-1",
        status=status,
        autonomy_level=2,
        requested_alarm=alarm,
        representative_alarm=alarm,
        action=None,
        severity=None,
        retry_of_run_id=None,
        llm_model="configured-model",
        prompt_version="prompt-v1",
        evidence=evidence,
        input_tokens=None,
        output_tokens=None,
        latency_ms=latency_ms,
        started_at=started,
        ended_at=None,
    )


def test_active_latency_excludes_wait_and_adds_resumed_segment() -> None:
    started = datetime(2026, 8, 29, 1, 0, tzinfo=UTC)
    initial = _timed_run(status=RunStatus.RUNNING, latency_ms=0)
    waiting = _timed_run(
        status=RunStatus.WAITING_APPROVAL,
        latency_ms=1500,
        evidence={
            repo.ACTIVE_TIMING_KEY: {
                "schema": repo.ACTIVE_TIMING_SCHEMA,
                "active_started_at": None,
            }
        },
    )
    resumed = _timed_run(
        status=RunStatus.RUNNING,
        latency_ms=1500,
        evidence={
            repo.ACTIVE_TIMING_KEY: {
                "schema": repo.ACTIVE_TIMING_SCHEMA,
                "active_started_at": (started + timedelta(hours=1)).isoformat(),
            }
        },
    )

    assert (
        repo.active_run_latency_ms(
            initial,
            now=started + timedelta(milliseconds=800),
        )
        == 800
    )
    assert (
        repo.active_run_latency_ms(
            waiting,
            now=started + timedelta(days=1),
        )
        == 1500
    )
    assert (
        repo.active_run_latency_ms(
            resumed,
            now=started + timedelta(hours=1, milliseconds=500),
        )
        == 2000
    )


class _Connection:
    """활성 transaction만 흉내 낸다. SQL은 실행하지 않는다."""

    def __init__(self, *, in_transaction: bool = True) -> None:
        self._in_transaction = in_transaction
        self.statements: list[Any] = []

    def in_transaction(self) -> bool:
        return self._in_transaction

    def execute(self, statement: Any, params: Any = None) -> Any:  # pragma: no cover
        self.statements.append(statement)
        raise AssertionError("단위 회귀는 SQL을 실행하지 않는다")


class _FailingConnection(_Connection):
    """**driver 예외만** 흉내 낸다. 성공 경로는 container가 소유한다."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def execute(self, statement: Any, params: Any = None) -> Any:
        raise self._error


# --- transaction 계약 -------------------------------------------------------


class TestRepositoryDoesNotOwnTransactions:
    """**caller가 transaction을 소유한다**(계획 §1.1).

    Repository가 engine을 만들거나 commit하면 업무 rollback 시 감사만 남는 상태가
    가능해진다. `V5-CM-4.2`가 그 원자성 증명을 이 Task에 넘겼다.
    """

    def test_no_engine_or_commit_anywhere(self) -> None:
        body = _code_only(Path(repo.__file__))
        for forbidden in (
            "create_engine",
            ".commit(",
            ".rollback(",
            ".begin(",
            "get_db_connection",
        ):
            assert forbidden not in body, forbidden

    def test_repository_has_no_audit_sql(self) -> None:
        """감사 SQL을 재구현하지 않는다 — Common helper만 부른다.

        `audit_log`를 언급하는 SQL 상수가 하나라도 생기면 두 벌의 감사 writer가 되고,
        그때부터 어느 쪽이 실효 계약인지 알 수 없다.
        """

        tree = ast.parse(Path(repo.__file__).read_text(encoding="utf-8"))
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        sql_literals = [value for value in literals if "INSERT INTO" in value.upper()]
        assert sql_literals, "SQL 상수를 못 찾았다"
        assert all("audit_log" not in value for value in sql_literals)

        # 그리고 Common helper를 **실제로** 부른다.
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "append_audit_log"
        ]
        assert calls, "Common helper를 부르지 않는다"

        # **record를 만드는 자리는 하나다.** 여러 곳에서 만들면 검증 시점이 갈리고,
        # 그중 하나가 업무 DML 뒤로 밀리면 업무만 commit되는 창이 생긴다.
        constructed = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "AuditRecord"
        ]
        assert len(constructed) == 1

    @pytest.mark.parametrize(
        ("call", "kwargs"),
        [
            ("create_agent_run", {}),
            ("set_run_action", {}),
            ("insert_action_history", {}),
            ("merge_run_action_provenance", {}),
            ("insert_prediction", {}),
            ("insert_human_prediction_review", {}),
            ("finish_agent_run", {}),
            ("record_run_llm_usage", {}),
        ],
    )
    def test_write_requires_an_active_transaction(
        self, call: str, kwargs: dict[str, Any]
    ) -> None:
        """비활성 connection이면 **DB에 닿기 전에** 멈춘다."""

        connection = _Connection(in_transaction=False)
        arguments: dict[str, dict[str, Any]] = {
            "create_agent_run": {"command": _command()},
            "set_run_action": {"agent_run_id": "RUN-1", "action": None},
            "insert_action_history": {
                "action_id": "ACT-1",
                "lot_id": "LOT-1",
                "chamber_id": "CH-1",
                "action_code": ActionCode.WARNING,
                "reason": "TRACE OOS",
                "created_at": SimpleNamespace(),
            },
            "merge_run_action_provenance": {"agent_run_id": "RUN-1"},
            "insert_prediction": {
                "agent_run_id": "RUN-1",
                "predicted_fault_code": FaultHypothesis.FOC,
                "confidence": 0.5,
                "cause_summary": "x",
                "evidence": {},
                "llm_model": "m",
                "prompt_version": "v",
            },
            "insert_human_prediction_review": {
                "agent_run_id": "RUN-1",
                "disposition": "ACCEPTED",
                "label_source": "HUMAN_REVIEW",
                "reviewer": "r",
            },
            "finish_agent_run": {
                "agent_run_id": "RUN-1",
                "status": RunStatus.COMPLETED,
            },
            "record_run_llm_usage": {
                "agent_run_id": "RUN-1",
                "llm_model": "m",
                "prompt_version": "v",
                "input_tokens": 1,
                "output_tokens": 1,
            },
        }[call]
        with pytest.raises(repo.RepositoryContractError) as exc:
            getattr(repo, call)(connection, **{**arguments, **kwargs})
        assert exc.value.code == "NO_ACTIVE_TRANSACTION"
        assert connection.statements == []


class TestRunLlmUsageContract:
    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("input_tokens", True, "INVALID_INPUT_TOKENS"),
            ("input_tokens", -1, "INVALID_INPUT_TOKENS"),
            ("output_tokens", 2_147_483_648, "INVALID_OUTPUT_TOKENS"),
            ("llm_model", "M" * 65, "LLM_MODEL_TOO_LONG"),
            ("prompt_version", "P" * 41, "PROMPT_VERSION_TOO_LONG"),
        ],
    )
    def test_invalid_usage_is_refused_before_sql(
        self, field: str, value: object, code: str
    ) -> None:
        connection = _Connection()
        arguments: dict[str, object] = {
            "llm_model": "m",
            "prompt_version": "v",
            "input_tokens": 1,
            "output_tokens": 1,
        }
        arguments[field] = value
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.record_run_llm_usage(connection, "RUN-1", **arguments)  # type: ignore[arg-type]
        assert exc.value.code == code
        assert connection.statements == []

    def test_usage_update_locks_the_run(self) -> None:
        statement = str(repo._SELECT_RUN_FOR_UPDATE)
        assert "FOR UPDATE" in statement


# --- run 생성 입력과 불변 ---------------------------------------------------


class TestCreateAgentRunInvariants:
    """1차 계획리뷰 필수 2 — 대표 알람 불변을 **표현할 수 없게** 만든다."""

    def test_new_run_supplies_the_public_non_null_storage_fields(self) -> None:
        """신규 행은 nullable legacy DDL과 무관하게 공개 DTO 계약을 만족한다."""

        statement = " ".join(str(repo._INSERT_RUN).split())
        assert ":llm_model, :prompt_version, 0" in statement
        field = repo.CreateAgentRunCommand.__dataclass_fields__["llm_model"]
        assert field.default is MISSING
        assert field.default_factory is MISSING

    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            ({"member_alarms": ()}, "EMPTY_MEMBER_ALARMS"),
            (
                {"member_alarms": (_ref("A1"), _ref("A1"))},
                "DUPLICATE_MEMBER_ALARM",
            ),
            (
                {"requested_alarm": _ref("ZZ")},
                "REQUESTED_ALARM_NOT_MEMBER",
            ),
            (
                {"representative_alarm": _ref("ZZ")},
                "REPRESENTATIVE_ALARM_NOT_MEMBER",
            ),
            ({"autonomy_level": 4}, "INVALID_AUTONOMY_LEVEL"),
            ({"autonomy_level": 0}, "INVALID_AUTONOMY_LEVEL"),
            ({"llm_model": None}, "INVALID_LLM_MODEL"),
            ({"llm_model": "   "}, "EMPTY_LLM_MODEL"),
        ],
    )
    def test_bad_input_is_refused_before_sql(
        self, overrides: dict[str, Any], code: str
    ) -> None:
        connection = _Connection()
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.create_agent_run(connection, _command(**overrides))
        assert exc.value.code == code
        assert connection.statements == []

    def test_same_alarm_id_from_another_source_is_not_a_duplicate(self) -> None:
        """중복 판정은 `(source, alarm_id)` 짝이다 — id만 같은 것은 다른 알람이다."""

        members = (_ref("A1", AlarmSource.TRACE), _ref("A1", AlarmSource.R03))
        repo._validate_create_command(
            _command(
                member_alarms=members,
                requested_alarm=members[0],
                representative_alarm=members[1],
            )
        )

    def test_is_representative_is_not_a_caller_input(self) -> None:
        """caller가 대표 flag를 줄 자리가 없다.

        `agent_run`의 대표 scalar와 `agent_run_alarm`의 대표 행이 어긋난 상태를
        public API로 만들 수 없어야 한다.
        """

        fields = repo.CreateAgentRunCommand.__dataclass_fields__
        assert "is_representative" not in fields
        body = ast.unparse(ast.parse(inspect.getsource(repo.create_agent_run).lstrip()))
        # 대표 여부는 representative token과의 equality로만 파생한다.
        assert "alarm.to_token() == representative" in body


# --- action·severity --------------------------------------------------------


class TestActionSeverityCannotBeMismatched:
    def test_severity_is_not_a_parameter(self) -> None:
        signature = inspect.signature(repo.set_run_action)
        assert "severity" not in signature.parameters
        assert list(signature.parameters) == [
            "connection",
            "agent_run_id",
            "action",
        ]

    @pytest.mark.parametrize(
        ("action", "severity"),
        [
            (ActionCode.MONITORING, Severity.LOW),
            (ActionCode.WARNING, Severity.MEDIUM),
            (ActionCode.EQP_HOLD, Severity.HIGH),
        ],
    )
    def test_the_only_derivation_is_the_shared_rule(
        self, action: ActionCode, severity: Severity
    ) -> None:
        """003의 named CHECK가 허용하는 3쌍이 곧 `resolve_severity()`의 출력이다."""

        assert resolve_severity(action) is severity

    def test_terminal_status_is_closed(self) -> None:
        assert set(repo.TERMINAL_EVENTS) == {RunStatus.COMPLETED, RunStatus.FAILED}
        connection = _Connection()
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.finish_agent_run(connection, "RUN-1", RunStatus.RUNNING)
        assert exc.value.code == "NOT_TERMINAL_STATUS"
        assert connection.statements == []


# --- label 격리 -------------------------------------------------------------


class TestHiddenGoldIsolation:
    """Runtime 기본 경로는 정답 label을 읽지도 쓰지도 않는다(계획 §7)."""

    def test_runtime_label_sources_exclude_hidden_gold(self) -> None:
        assert repo.RUNTIME_REVIEW_LABEL_SOURCES == ("HUMAN_REVIEW", "MENTOR_REVIEW")
        assert repo.HIDDEN_GOLD not in repo.RUNTIME_REVIEW_LABEL_SOURCES

    def test_hidden_gold_write_is_refused_before_sql(self) -> None:
        connection = _Connection()
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.insert_human_prediction_review(
                connection,
                agent_run_id="RUN-1",
                disposition="ACCEPTED",
                label_source=repo.HIDDEN_GOLD,
                reviewer="r",
            )
        assert exc.value.code == "LABEL_SOURCE_NOT_ALLOWED"
        assert connection.statements == []

    def test_the_read_filter_lives_in_sql(self) -> None:
        """애플리케이션 필터가 아니라 SQL이 막는다.

        새 조회 경로가 생길 때마다 같은 필터를 다시 붙이는 실수를 없앤다.
        """

        statement = str(repo._SELECT_REVIEWS)
        assert "label_source = ANY(:allowed)" in statement

    def test_no_evaluation_label_join(self) -> None:
        body = _code_only(Path(repo.__file__))
        # C-4.5는 조치 대상 장비 식별을 위해 lot_history의 equipment_id만 읽는다.
        # fault_code·evaluation 정답 label에는 계속 접근하지 않는다.
        for forbidden in ("fault_code FROM", "evaluation"):
            assert forbidden not in body, forbidden
        equipment_sql = str(repo._SELECT_INCIDENT_EQUIPMENT)
        assert "SELECT DISTINCT equipment_id" in equipment_sql
        assert "FROM lot_history" in equipment_sql
        assert "fault_code" not in equipment_sql


# --- SQL·오류 계약 ----------------------------------------------------------


class TestSqlAndErrorContract:
    def test_every_statement_is_a_module_constant_with_bind_params(self) -> None:
        """문자열 보간 SQL을 만들지 않는다.

        f-string으로 컬럼 목록을 조립하는 자리는 있지만 그 입력은 module 상수뿐이며,
        **값**은 전부 bind parameter다.
        """

        source = Path(repo.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "text":
                continue
            argument = node.args[0]
            if isinstance(argument, ast.JoinedStr):
                # 컬럼 목록 상수만 삽입한다 — 값 보간이 아니다.
                names = {
                    part.value.id
                    for part in argument.values
                    if isinstance(part, ast.FormattedValue)
                    and isinstance(part.value, ast.Name)
                }
                assert names <= {
                    "_RUN_COLUMNS",
                    "_PREDICTION_COLUMNS",
                    "_REVIEW_COLUMNS",
                    "_RUN_ACTION_COLUMNS",
                    "_TOOL_CALL_COLUMNS",
                    "_APPROVAL_COLUMNS",
                    "_DELIVERY_COLUMNS",
                    "_STALE_DELIVERY_PREDICATE",
                    "_ACTION_HISTORY_COLUMNS",
                    "_PUBLIC_ACTION_SELECT",
                    "_PUBLIC_APPROVAL_SELECT",
                }
                continue
            assert isinstance(argument, ast.Constant)

    def test_conflict_codes_are_stable_and_unique(self) -> None:
        codes = list(repo.CONFLICT_CODES.values())
        assert len(set(codes)) == len(codes)
        # 1차 계획리뷰 필수 3 — PK 3종이 포함돼야 한다.
        for name in (
            "agent_run_action_pkey",
            "action_delivery_pkey",
            "agent_run_alarm_pkey",
        ):
            assert name in repo.CONFLICT_CODES

    def test_constraint_name_comes_from_the_driver_not_the_message(self) -> None:
        """메시지 문자열을 파싱하지 않는다 — 형식이 바뀌면 조용히 어긋난다."""

        class _Diag:
            constraint_name = "ux_agent_run_incident_active"

        class _Orig(Exception):
            diag = _Diag()

        class _Error(Exception):
            orig = _Orig()

        assert repo._constraint_name(_Error()) == "ux_agent_run_incident_active"
        assert repo._constraint_name(Exception()) is None

    def test_unknown_constraint_is_a_contract_error(self) -> None:
        from sqlalchemy.exc import IntegrityError

        error = IntegrityError("stmt", {}, Exception("boom"))
        translated = repo._translate(error)
        assert isinstance(translated, repo.RepositoryContractError)
        assert translated.code == "CONSTRAINT_VIOLATION"

    def test_known_constraint_becomes_a_conflict(self) -> None:
        from sqlalchemy.exc import IntegrityError

        class _Diag:
            constraint_name = "approval_request_action_id_key"

        class _Orig(Exception):
            diag = _Diag()

        error = IntegrityError("stmt", {}, _Orig())
        translated = repo._translate(error)
        assert isinstance(translated, repo.RepositoryConflict)
        assert translated.code == "APPROVAL_ALREADY_EXISTS"

    def test_sanitized_messages_carry_no_sql_or_dsn(self) -> None:
        for error in (
            repo.RepositoryConflict("ACTIVE_RUN_EXISTS"),
            repo.RepositoryNotFound("RUN_NOT_FOUND"),
            repo.RepositoryContractError("CONSTRAINT_VIOLATION"),
            repo.RepositoryUnavailable("DATABASE_UNAVAILABLE"),
        ):
            text_value = str(error)
            assert "postgresql" not in text_value
            assert "INSERT" not in text_value
            assert "SELECT" not in text_value


class TestErrorClassification:
    """**연결 실패만 Unavailable이다**(구현리뷰 묶음 1 필수 2).

    이전에는 모든 `DBAPIError`가 `DATABASE_UNAVAILABLE`이었다. 그래서 caller 입력이
    varchar 경계를 넘겨 생긴 `DataError`가 장애로 분류돼 503 후보가 됐다.
    """

    @staticmethod
    def _error(kind: type) -> Any:
        return kind("stmt", {}, Exception("boom"))

    def test_data_errors_are_contract_violations(self) -> None:
        from sqlalchemy.exc import DataError, ProgrammingError

        for kind in (DataError, ProgrammingError):
            translated = repo._translate(self._error(kind))
            assert isinstance(translated, repo.RepositoryContractError), kind
            assert translated.code == "DATA_CONTRACT_VIOLATION"

    def test_only_connection_failures_are_unavailable(self) -> None:
        from sqlalchemy.exc import InterfaceError, OperationalError

        for kind in (OperationalError, InterfaceError):
            translated = repo._translate(self._error(kind))
            assert isinstance(translated, repo.RepositoryUnavailable), kind
            assert translated.code == "DATABASE_UNAVAILABLE"


class TestStringBoundariesMatchTheMigration:
    """경계값은 `002_agent_runtime_clean.sql`의 varchar 상한이다."""

    def test_limits_match_the_migration(self) -> None:
        migration = (
            Path(__file__).resolve().parents[3]
            / "backend"
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        for column, limit in (
            ("thread_id", 36),
            ("lot_id", 20),
            ("chamber_id", 24),
            ("alarm_id", 24),
            ("llm_model", 64),
            ("prompt_version", 40),
        ):
            assert f"{column} varchar({limit})" in migration, column
            assert repo.COLUMN_LIMITS[column] == limit

    @pytest.mark.parametrize(
        ("value", "code"),
        [
            ("", "EMPTY_LOT_ID"),
            ("   ", "EMPTY_LOT_ID"),
            ("L" * 21, "LOT_ID_TOO_LONG"),
            (None, "INVALID_LOT_ID"),
            (123, "INVALID_LOT_ID"),
        ],
    )
    def test_bad_text_is_refused(self, value: Any, code: str) -> None:
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo._require_text(value, "lot_id")
        assert exc.value.code == code

    def test_the_boundary_value_passes_and_is_trimmed(self) -> None:
        assert repo._require_text("  L" + "x" * 17 + "  ", "lot_id") == "L" + "x" * 17
        assert repo._require_text("L" * 20, "lot_id") == "L" * 20

    def test_optional_text_allows_none_but_not_blank(self) -> None:
        assert repo._optional_text(None, "llm_model") is None
        with pytest.raises(repo.RepositoryContractError):
            repo._optional_text("   ", "llm_model")


#: 업무 DML을 실행하는 호출 표기.
#:
#: `_insert_one()` 도입 전에는 `connection.execute` 하나였다. 그 이름만 찾으면
#: INSERT 경로에서 순서 검사가 **조용히 사라진다** — 실제로 이 helper를 넣기 전에
#: `insert_prediction`이 `ValueError: substring not found`로 깨졌다.
_DML_MARKERS = ("connection.execute", "_insert_one(", "_fetch_one(")


def _first_dml(body: str) -> int:
    """함수 본문에서 **첫 DML 실행 위치**를 돌려준다."""

    found = [body.index(marker) for marker in _DML_MARKERS if marker in body]
    assert found, "DML 실행 지점을 찾지 못했다 — 순서 검사가 무의미해진다"
    return min(found)


class TestAuditRecordIsBuiltBeforeDml:
    """감사 record 구성이 업무 DML보다 **앞**이다(구현리뷰 묶음 1 필수 1)."""

    @pytest.mark.parametrize(
        "function",
        ["create_agent_run", "finish_agent_run", "insert_prediction"],
    )
    def test_record_precedes_execute(self, function: str) -> None:
        body = ast.unparse(
            ast.parse(inspect.getsource(getattr(repo, function)).lstrip())
        )
        assert body.index("_run_audit_record") < _first_dml(body)

    @pytest.mark.parametrize(
        "function",
        ["create_agent_run", "finish_agent_run", "insert_prediction"],
    )
    def test_audit_append_is_inside_the_boundary(self, function: str) -> None:
        """`append_audit_log`가 `_write()` 경계 밖에 있으면 raw 예외가 샌다."""

        body = ast.unparse(
            ast.parse(inspect.getsource(getattr(repo, function)).lstrip())
        )
        assert "append_audit_log(connection, record)" in body
        # 감사 append는 `_write`에 넘기는 내부 함수 안에서만 불린다.
        assert body.index("append_audit_log") < body.index("_write(connection")

    def test_an_invalid_record_is_a_contract_error(self) -> None:
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo._run_audit_record(
                repo.AuditEvent.AGENT_RUN_STARTED,
                entity_id="R" * 21,
                actor_type=repo.ActorType.AGENT,
                actor_id=None,
                after={},
            )
        assert exc.value.code == "AUDIT_RECORD_INVALID"


class TestNormalisationReachesTheWrite:
    """검증 결과를 **쓰지 않으면** 검증이 아무것도 보장하지 않는다(2차 필수 1-A)."""

    def test_the_validator_returns_a_normalised_command(self) -> None:
        padded = _command(
            thread_id="  " + "T" * 36 + "  ",
            lot_id="  LOT-1  ",
            chamber_id="  CH-1  ",
            llm_model="  claude  ",
            prompt_version="  v1  ",
        )
        normalized = repo._validate_create_command(padded)
        assert normalized.thread_id == "T" * 36
        assert normalized.lot_id == "LOT-1"
        assert normalized.chamber_id == "CH-1"
        assert normalized.llm_model == "claude"
        assert normalized.prompt_version == "v1"
        # 원본은 그대로다 — frozen dataclass를 제자리에서 바꾸지 않는다.
        assert padded.lot_id == "  LOT-1  "

    def test_create_rebinds_the_command_before_use(self) -> None:
        """`command = _validate_create_command(command)` 형태여야 한다.

        반환값을 버리면 원문이 bind된다 — 그게 2차 지적의 재현이었다.
        """

        body = ast.unparse(ast.parse(inspect.getsource(repo.create_agent_run).lstrip()))
        assert "command = _validate_create_command(command)" in body

    @pytest.mark.parametrize(
        ("function", "names"),
        [
            ("insert_prediction", ("cause_summary", "llm_model", "prompt_version")),
            ("insert_human_prediction_review", ("reviewer", "disposition")),
        ],
    )
    def test_text_arguments_are_rebound(
        self, function: str, names: tuple[str, ...]
    ) -> None:
        body = ast.unparse(
            ast.parse(inspect.getsource(getattr(repo, function)).lstrip())
        )
        for name in names:
            # `ast.unparse()`는 따옴표를 작은따옴표로 정규화한다.
            assert f"{name} = _require_text({name}, '{name}')" in body, name


class TestJsonSerialisationIsSanitised:
    """직렬화 실패가 raw `TypeError`로 새지 않는다(2차 필수 1-B)."""

    def test_serialisation_happens_in_one_place(self) -> None:
        tree = ast.parse(Path(repo.__file__).read_text(encoding="utf-8"))
        dumps = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(getattr(node.func, "value", None), "id", None) == "json"
            and getattr(node.func, "attr", None) == "dumps"
        ]
        assert len(dumps) == 1, "직렬화 지점이 여러 곳이면 하나가 경계 밖에 남는다"

    @pytest.mark.parametrize(
        "value",
        [
            {"bad": object()},
            {"cycle": {1, 2}},
            # **non-finite float는 직렬화에 성공한다** — 그게 문제였다.
            # `json.dumps()` 기본값 `allow_nan=True`가 `{"v": NaN}`을 만들고,
            # 그 출력은 JSON이 아니라 PostgreSQL `::jsonb`가 거부한다.
            {"v": float("nan")},
            {"v": float("inf")},
            {"v": float("-inf")},
            {"nested": {"deep": [1, float("nan")]}},
        ],
    )
    def test_unserialisable_payload_becomes_a_contract_error(
        self, value: dict[str, Any]
    ) -> None:
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo._json_payload(value, "evidence")
        assert exc.value.code == "INVALID_JSON_EVIDENCE"
        assert "object" not in str(exc.value)

    def test_finite_floats_still_serialise(self) -> None:
        """**양성 대조군.** 정상 float까지 막지 않는다."""

        assert repo._json_payload({"v": 1.5, "z": 0.0}, "evidence") == (
            '{"v": 1.5, "z": 0.0}'
        )

    def test_allow_nan_is_disabled(self) -> None:
        """계약을 상수 위치에서 고정한다 — 기본값으로 되돌아가면 red다."""

        body = ast.unparse(ast.parse(inspect.getsource(repo._json_payload).lstrip()))
        assert "allow_nan=False" in body

    def test_none_stays_none(self) -> None:
        assert repo._json_payload(None, "evidence") is None

    def test_serialisation_precedes_execute(self) -> None:
        for name in ("finish_agent_run", "insert_prediction"):
            body = ast.unparse(
                ast.parse(inspect.getsource(getattr(repo, name)).lstrip())
            )
            assert body.index("_json_payload") < _first_dml(body), name


# ===========================================================================
# 묶음 2 — 계약을 DB 없이 확인할 수 있는 부분
# ===========================================================================


class TestToolCallContract:
    def test_the_sentinel_uses_a_status_the_migration_allows(self) -> None:
        """`002`의 status CHECK에 `STARTED`가 없어 sentinel이 불가피하다."""

        migration = (
            Path(__file__).resolve().parents[3]
            / "backend"
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        assert "status IN ('SUCCESS', 'ERROR', 'TIMEOUT')" in migration
        # `agent_tool_call`의 status 절에 `STARTED`가 없다.
        # (`AGENT_RUN_STARTED`는 audit event라 문서 전체 검색은 무의미하다.)
        clause = migration[migration.index("CREATE TABLE agent_tool_call (") :].split(
            ");", 1
        )[0]
        assert "STARTED" not in clause
        assert repo.RESERVED_ERROR_MSG == "CALL_RESERVED_NOT_COMPLETED"

    def test_reserve_locks_the_run_before_reading_the_sequence(self) -> None:
        """lock이 `max(call_seq)` 조회보다 **앞**이어야 직렬화된다."""

        body = ast.unparse(
            ast.parse(inspect.getsource(repo.reserve_tool_call).lstrip())
        )
        assert body.index("_LOCK_RUN") < body.index("_NEXT_CALL_SEQ")
        assert "FOR UPDATE" in str(repo._LOCK_RUN)

    def test_finalize_matches_the_whole_sentinel(self) -> None:
        """조건이 sentinel 전체와 맞을 때만 1행이다.

        하나라도 빠지면 이미 닫힌 호출을 다시 덮을 수 있다.
        """

        statement = str(repo._FINALIZE_TOOL_CALL)
        for clause in (
            "status = :reserved_status",
            "error_msg = :reserved_error",
            "output IS NULL",
            "latency_ms IS NULL",
            "agent_run_id = :agent_run_id",
        ):
            assert clause in statement, clause

    @pytest.mark.parametrize("key", repo.RESERVED_TOOL_OUTPUT_KEYS)
    def test_reserved_output_keys_are_refused(self, key: str) -> None:
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo._assert_no_reserved_keys({key: 1})
        assert exc.value.code == "RESERVED_OUTPUT_KEY"

    def test_domain_payload_is_allowed(self) -> None:
        """**양성 대조군.** 실행 metadata가 아닌 key는 통과한다."""

        repo._assert_no_reserved_keys({"rows": 3, "summary": "x"})
        repo._assert_no_reserved_keys(None)

    def test_negative_latency_is_refused(
        self,
    ) -> None:
        connection = _Connection()
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.finalize_tool_call(
                connection,
                tool_call_id="TOOL-" + "0" * 24,
                agent_run_id="RUN-1",
                status=ToolCallStatus.SUCCESS,
                latency_ms=-1,
            )
        assert exc.value.code == "NEGATIVE_LATENCY"
        assert connection.statements == []


class TestApprovalStatusCannotBeAuto:
    """`ApprovalStatus`는 5값인데 DB CHECK는 4값이다(계획리뷰 1차 권장 1)."""

    def test_the_enum_and_the_migration_disagree(self) -> None:
        migration = (
            Path(__file__).resolve().parents[3]
            / "backend"
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        assert "status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')" in migration
        assert "AUTO" in {member.value for member in ApprovalStatus}

    def test_create_has_no_status_parameter(self) -> None:
        """넘길 자리가 없으면 잘못된 값을 만들 수 없다."""

        assert (
            "status" not in inspect.signature(repo.create_approval_request).parameters
        )
        tree = ast.parse(inspect.getsource(repo.create_approval_request).lstrip())
        function = tree.body[0]
        # docstring이 `AUTO`를 설명하므로 본문 코드만 본다.
        statements = [
            node
            for node in function.body  # type: ignore[attr-defined]
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        body = "\n".join(ast.unparse(node) for node in statements)
        assert "ApprovalStatus.PENDING.value" in body
        assert "AUTO" not in body


class TestDeliveryInitialContract:
    def test_the_pairs_match_the_design(self) -> None:
        """설계 §7.1이 초기 조합을 **둘로** 고정한다.

        상태 목록으로 두고 channel과 독립 검증하면 `MES_MOCK=WAITING`(승인 전 전송
        가능)·`EMAIL=CANCELED`(반려 전이 우회) 같은 조합이 만들어진다
        (구현리뷰 묶음 2 필수 1).
        """

        design = (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "specifications"
            / "시스템설계서_v2_1_작업본.md"
        ).read_text(encoding="utf-8")
        assert "| EQP_HOLD | PENDING | EMAIL=WAITING, MES_MOCK=BLOCKED |" in design
        assert "| WARNING | AUTO | EMAIL=WAITING |" in design

        assert dict(repo.INITIAL_DELIVERY_PAIRS) == {
            DeliveryChannel.EMAIL: DeliveryStatus.WAITING,
            DeliveryChannel.MES_MOCK: DeliveryStatus.BLOCKED,
        }
        # 모든 channel이 정확히 하나의 초기 상태를 갖는다.
        assert set(repo.INITIAL_DELIVERY_PAIRS) == set(DeliveryChannel)

    def test_no_post_send_status_is_creatable(self) -> None:
        """전이 결과 상태는 어느 channel의 초기값도 아니다."""

        for forbidden in (
            DeliveryStatus.SENDING,
            DeliveryStatus.SENT,
            DeliveryStatus.FAILED,
            DeliveryStatus.CANCELED,
            DeliveryStatus.UNKNOWN,
        ):
            assert forbidden not in repo.INITIAL_DELIVERY_PAIRS.values()

    def test_request_hash_pattern_matches_the_migration(self) -> None:
        migration = (
            Path(__file__).resolve().parents[3]
            / "backend"
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        assert "request_hash char(64)" in migration
        assert "request_hash ~ '^[0-9a-f]{64}$'" in migration
        assert repo._HEX64.fullmatch("a" * 64)
        assert not repo._HEX64.fullmatch("A" * 64)


class TestActionPersistenceSeams:
    def test_action_insert_explicitly_nulls_legacy_delivery_columns(self) -> None:
        sql = " ".join(str(repo._INSERT_ACTION_HISTORY).split())
        for column in (
            "recipe_step_name",
            "equipment_id",
            "trigger_alarm_lot_hist_id",
            "notify_status",
            "notify_at",
            "mes_status",
            "mes_at",
        ):
            assert column in sql
        # base action_history에는 생성 run column이 없다. 생성 provenance 정본은
        # agent_run_action의 단일 CREATED link다.
        assert "created_by_agent_run_id" not in sql

    def test_bundle_projection_reads_only_identity_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = SimpleNamespace(
            action_id="ACT-1",
            action_code="WARNING",
            approval_id=None,
            approval_status=None,
            approval_agent_run_id=None,
        )
        deliveries = [
            SimpleNamespace(channel=DeliveryChannel.EMAIL),
        ]
        monkeypatch.setattr(repo, "_fetch_one", lambda *_a, **_k: row)
        monkeypatch.setattr(repo, "list_action_deliveries", lambda *_a: deliveries)

        bundle = repo.get_action_bundle(_Connection(), "ACT-1")

        assert bundle == repo.ActionBundle(
            action_id="ACT-1",
            action_code=ActionCode.WARNING,
            approval_id=None,
            approval_status=None,
            approval_agent_run_id=None,
            delivery_channels=(DeliveryChannel.EMAIL,),
        )
        assert "p.status AS approval_status" in " ".join(
            str(repo._SELECT_ACTION_BUNDLE).split()
        )

    def test_approval_request_is_unique_per_action_in_the_migration(self) -> None:
        migration = (
            Path(repo.__file__).resolve().parents[2]
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        assert "action_id varchar(20) NOT NULL UNIQUE" in migration

    def test_terminal_merge_preserves_action_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provenance = {
            "schema": repo.ACTION_PROVENANCE_SCHEMA,
            "action_policy_version": "ACTION-POLICY-V1",
            "member_alarms": [{"source": AlarmSource.TRACE.value, "alarm_id": "TA-01"}],
        }
        current = SimpleNamespace(
            status=RunStatus.RUNNING,
            evidence={repo.ACTION_PROVENANCE_KEY: provenance},
        )
        captured: dict[str, Any] = {}
        monkeypatch.setattr(repo, "lock_agent_run", lambda *_a: current)

        class CapturingConnection(_Connection):
            def execute(self, statement: Any, params: Any = None) -> Any:
                self.statements.append(statement)
                captured.update(params)
                return SimpleNamespace(one_or_none=lambda: SimpleNamespace(evidence={}))

        monkeypatch.setattr(repo, "_run_row", lambda row: row)

        repo.merge_run_action_provenance(
            CapturingConnection(),
            "RUN-1",
            terminal_evidence={"code": "FAILED"},
        )

        payload = json.loads(captured["evidence"])
        assert payload[repo.ACTION_PROVENANCE_KEY] == provenance
        assert payload["code"] == "FAILED"

    def test_policy_version_error_names_its_own_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            repo,
            "lock_agent_run",
            lambda *_a: SimpleNamespace(status=RunStatus.RUNNING, evidence={}),
        )

        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.merge_run_action_provenance(
                _Connection(),
                "RUN-1",
                action_policy_version="V" * 41,
                member_alarms=[_ref("TA-01")],
            )

        assert exc.value.code == "ACTION_POLICY_VERSION_TOO_LONG"

    def test_action_insert_sanitizes_an_invalid_action_code(self) -> None:
        connection = _Connection()

        with pytest.raises(repo.RepositoryContractError) as exc:
            repo.insert_action_history(
                connection,
                action_id="ACT-1",
                lot_id="LOT-1",
                chamber_id="EQP01-PM1",
                action_code="NOT_AN_ACTION",  # type: ignore[arg-type]
                reason="reason",
                created_at=SimpleNamespace(),
            )

        assert exc.value.code == "INVALID_ACTION_CODE"
        assert connection.statements == []

    def test_provenance_cannot_be_replaced_with_a_different_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = {
            "schema": repo.ACTION_PROVENANCE_SCHEMA,
            "action_policy_version": "ACTION-POLICY-V1",
            "member_alarms": [{"source": AlarmSource.TRACE.value, "alarm_id": "TA-01"}],
        }
        connection = _Connection()
        monkeypatch.setattr(
            repo,
            "lock_agent_run",
            lambda *_a: SimpleNamespace(
                status=RunStatus.RUNNING,
                evidence={repo.ACTION_PROVENANCE_KEY: existing},
            ),
        )

        with pytest.raises(repo.RepositoryConflict) as exc:
            repo.merge_run_action_provenance(
                connection,
                "RUN-1",
                action_policy_version="ACTION-POLICY-V1",
                member_alarms=[_ref("TA-02")],
            )

        assert exc.value.code == "ACTION_PROVENANCE_MISMATCH"
        assert connection.statements == []

    @pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.FAILED])
    def test_provenance_merge_rejects_terminal_runs_before_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: RunStatus,
    ) -> None:
        connection = _Connection()
        monkeypatch.setattr(
            repo,
            "lock_agent_run",
            lambda *_a: SimpleNamespace(status=status, evidence={}),
        )

        with pytest.raises(repo.RepositoryConflict) as exc:
            repo.merge_run_action_provenance(
                connection,
                "RUN-1",
                terminal_evidence={"code": "LATE_WRITE"},
            )

        assert exc.value.code == "RUN_NOT_ACTIVE"
        assert connection.statements == []

    def test_provenance_update_sql_keeps_the_active_status_guard(self) -> None:
        sql = " ".join(str(repo._UPDATE_RUN_EVIDENCE).split())
        assert "status = ANY(:active)" in sql


class TestBundleTwoConflictCoverage:
    def test_every_declared_conflict_name_is_in_the_migration(self) -> None:
        """**이름은 실측이어야 한다.**

        index·unique는 migration에 문자열로 있고, PK 3종은 PostgreSQL이
        `<table>_pkey`로 자동 명명한다. 후자는 container 회귀가 실제 위반으로
        확인한다.
        """

        migration = (
            Path(__file__).resolve().parents[3]
            / "backend"
            / "migrations"
            / "002_agent_runtime_clean.sql"
        ).read_text(encoding="utf-8")
        for name in repo.CONFLICT_CODES:
            if name.endswith("_pkey"):
                table = name[: -len("_pkey")]
                assert f"CREATE TABLE {table} (" in migration, name
                continue
            if name.endswith("_key"):
                continue  # inline UNIQUE — PostgreSQL 자동 명명
            assert name in migration, name


# ===========================================================================
# 구현리뷰 필수 1·2 — INSERT의 "부모 없음"과 경합/장애 구분
# ===========================================================================


class _Orig(Exception):
    """psycopg 예외 자리표시자. `_translate()`가 읽는 것만 갖는다."""

    def __init__(self, sqlstate: str, constraint: str | None = None) -> None:
        super().__init__("driver detail with dsn and sql")
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(constraint_name=constraint)


def _operational(sqlstate: str | None) -> OperationalError:
    return OperationalError(
        "SELECT 1", {}, _Orig(sqlstate) if sqlstate else Exception()
    )


def _integrity(sqlstate: str, constraint: str | None) -> IntegrityError:
    return IntegrityError("INSERT INTO t VALUES (1)", {}, _Orig(sqlstate, constraint))


class TestContentionIsNotAnOutage:
    """lock 경합은 장애가 아니다(구현리뷰 필수 2).

    psycopg3는 `DeadlockDetected`·`SerializationFailure`·`LockNotAvailable`·
    `QueryCanceled`를 모두 `OperationalError` 아래에 둔다. 클래스만 보면 접속 실패와
    구분되지 않아 전부 `DATABASE_UNAVAILABLE`이 됐고, 상위가 503으로 올리면
    **재시도하면 되는 상황을 장애로 보고**한다.
    """

    @pytest.mark.parametrize(
        ("sqlstate", "code"),
        [
            ("40001", "SERIALIZATION_FAILURE"),
            ("40P01", "DEADLOCK_DETECTED"),
            ("55P03", "LOCK_NOT_AVAILABLE"),
            ("57014", "STATEMENT_CANCELED"),
        ],
    )
    def test_a_contention_sqlstate_is_retryable(self, sqlstate: str, code: str) -> None:
        translated = repo._translate(_operational(sqlstate))
        assert isinstance(translated, repo.RepositoryRetryable)
        assert translated.code == code

    def test_retryable_is_not_an_unavailable_subclass(self) -> None:
        """상위가 `RepositoryUnavailable`을 503으로 잡아도 경합은 걸리지 않는다."""

        assert not issubclass(repo.RepositoryRetryable, repo.RepositoryUnavailable)
        assert not issubclass(repo.RepositoryUnavailable, repo.RepositoryRetryable)
        assert issubclass(repo.RepositoryRetryable, repo.AgentRepositoryError)

    def test_a_real_connection_failure_is_still_unavailable(self) -> None:
        """음성 대조군. SQLSTATE가 없는 접속 실패는 그대로 장애다."""

        translated = repo._translate(_operational(None))
        assert isinstance(translated, repo.RepositoryUnavailable)
        assert translated.code == "DATABASE_UNAVAILABLE"

    def test_the_contention_check_precedes_the_outage_branch(self) -> None:
        body = ast.unparse(ast.parse(inspect.getsource(repo._translate).lstrip()))
        assert body.index("RETRYABLE_SQLSTATES") < body.index("DATABASE_UNAVAILABLE")

    def test_no_driver_text_reaches_the_caller(self) -> None:
        translated = repo._translate(_operational("40P01"))
        assert "dsn" not in str(translated).lower()
        assert "insert" not in str(translated).lower()


class TestInsertPathsNameTheMissingParent:
    """`INSERT ... RETURNING`은 0행이 아니라 예외다(구현리뷰 필수 1).

    그래서 INSERT 자리의 `missing_code`는 도달할 수 없었고, 실제로는 FK 위반이
    `CONSTRAINT_VIOLATION`으로 나와 CHECK 위반과 구분되지 않았다. 후속 C Task가
    "없는 run이면 `RUN_NOT_FOUND`"로 읽고 404로 mapping할 것이라서 문제다.
    """

    def test_no_insert_statement_is_routed_through_fetch_one(self) -> None:
        """**표기가 아니라 배선을 본다.** 한 자리라도 되돌아가면 여기서 걸린다."""

        tree = ast.parse(Path(repo.__file__).read_text(encoding="utf-8"))
        offenders = [
            node.args[1].id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_fetch_one"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id.startswith("_INSERT_")
        ]
        assert offenders == [], offenders

    def test_insert_one_only_ever_receives_insert_statements(self) -> None:
        """**"INSERT 자리에 한정한다"를 구조로 닫는다.**

        `23503`은 방향을 구분하지 못한다 — 자식 INSERT(부모 없음)와 부모 DELETE(자식
        남음)가 같은 constraint 이름·같은 `diag.table_name`을 준다(실측). 그래서
        `_insert_one()`에 SELECT·UPDATE 문이 하나라도 들어오면 "부모가 없다"는 해석이
        더 이상 참이 아니게 된다. 반대 방향(`_fetch_one`에 INSERT)만 막아서는 이 성질이
        지켜지지 않는다.
        """

        tree = ast.parse(Path(repo.__file__).read_text(encoding="utf-8"))
        statements = [
            node.args[1].id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_insert_one"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Name)
        ]
        assert statements, "호출 자리를 찾지 못했다 — 검사가 무의미해진다"
        offenders = [name for name in statements if not name.startswith("_INSERT_")]
        assert offenders == [], offenders

    def test_fetch_one_is_never_given_a_dead_missing_code(self) -> None:
        """남은 `_fetch_one`은 전부 SELECT·UPDATE다 — 0행이 실제로 가능하다."""

        tree = ast.parse(Path(repo.__file__).read_text(encoding="utf-8"))
        statements = {
            node.args[1].id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_fetch_one"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Name)
        }
        assert statements
        assert all(
            name.startswith(("_SELECT_", "_UPDATE_", "_FINALIZE_"))
            for name in statements
        ), sorted(statements)

    @pytest.mark.parametrize(
        ("constraint", "code"),
        sorted(repo.FOREIGN_KEY_CODES.items()),
    )
    def test_an_insert_fk_violation_names_the_parent(
        self, constraint: str, code: str
    ) -> None:
        connection = _FailingConnection(_integrity("23503", constraint))
        with pytest.raises(repo.RepositoryNotFound) as exc:
            repo._insert_one(connection, "INSERT", {})
        assert exc.value.code == code

    def test_a_check_violation_stays_a_contract_error(self) -> None:
        """**둘이 구분된다**는 것이 이 수정의 핵심이다."""

        connection = _FailingConnection(_integrity("23514", "agent_run_severity_pair"))
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo._insert_one(connection, "INSERT", {})
        assert exc.value.code == "CONSTRAINT_VIOLATION"

    def test_an_unknown_fk_name_is_not_silently_a_not_found(self) -> None:
        """표에 없는 이름을 추측으로 404로 만들지 않는다."""

        connection = _FailingConnection(_integrity("23503", "some_future_fkey"))
        with pytest.raises(repo.RepositoryContractError) as exc:
            repo._insert_one(connection, "INSERT", {})
        assert exc.value.code == "CONSTRAINT_VIOLATION"

    def test_a_known_conflict_name_still_wins(self) -> None:
        """unique 위반은 FK 경로를 타지 않는다 — `23505`다."""

        connection = _FailingConnection(
            _integrity("23505", "ux_agent_run_incident_active")
        )
        with pytest.raises(repo.RepositoryConflict) as exc:
            repo._insert_one(connection, "INSERT", {})
        assert exc.value.code == "ACTIVE_RUN_EXISTS"

    def test_every_declared_fk_code_is_a_stable_name(self) -> None:
        assert all(name.endswith("_fkey") for name in repo.FOREIGN_KEY_CODES)
        assert set(repo.FOREIGN_KEY_CODES.values()) == {
            "RUN_NOT_FOUND",
            "ACTION_NOT_FOUND",
            "RETRY_SOURCE_RUN_NOT_FOUND",
        }
