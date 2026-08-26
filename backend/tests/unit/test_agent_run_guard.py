"""`V5-C-1.3` 중복 실행 방지 — 순서·정책·비누수 단위 회귀.

실제 lock 거동과 partial unique 발화는 `test_agent_run_guard_container.py`가 소유한다.
여기서는 **fake Connection으로 호출 순서와 판정**을 고정한다. 순서는 실제 DB 없이도
관측할 수 있고, 순서가 이 Task의 계약이기 때문이다.
"""

from __future__ import annotations

import ast
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import repository as repo  # noqa: E402
from app.agent import run_guard as guard  # noqa: E402
from app.agent import run_guard_repository as guard_repo  # noqa: E402
from app.agent.incident import ResolvedIncident  # noqa: E402
from app.common.enums import AlarmSource, RunStatus  # noqa: E402
from app.common.exceptions import (  # noqa: E402
    IncidentAlreadyProcessedError,
    IncidentAlreadyRunningError,
)
from app.common.schemas import AlarmRef  # noqa: E402

LOT = "LOT001"
PHOTO = "EQP01-PM1"
T0 = datetime(2026, 8, 1, 10, 0, 0)


def _ref(source: AlarmSource, alarm_id: str) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


def _incident(**over: Any) -> ResolvedIncident:
    members = over.pop(
        "member_alarms",
        (_ref(AlarmSource.TRACE, "TA-01"), _ref(AlarmSource.R03, "R03-01")),
    )
    fields: dict[str, Any] = {
        "lot_id": LOT,
        "chamber_id": PHOTO,
        "requested_alarm": members[0],
        "representative_alarm": members[0],
        "member_alarms": members,
    }
    fields.update(over)
    return ResolvedIncident(**fields)


def _run(
    status: RunStatus, run_id: str, *, minutes: int = 0
) -> guard_repo.IncidentRunRow:
    return guard_repo.IncidentRunRow(
        agent_run_id=run_id,
        status=status,
        started_at=T0 + timedelta(minutes=minutes),
    )


class _Connection:
    """호출 순서를 기록하는 fake. **transaction은 열려 있다고 답한다.**"""

    def __init__(self, *, in_transaction: bool = True) -> None:
        self._in_transaction = in_transaction
        self.calls: list[str] = []

    def in_transaction(self) -> bool:
        self.calls.append("in_transaction")
        return self._in_transaction


class _Harness:
    """`start_incident_run()`의 협력자를 전부 spy로 바꾼다."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        history: tuple[guard_repo.IncidentRunRow, ...] = (),
        incident: ResolvedIncident | None = None,
        create: Any = None,
    ) -> None:
        self.order: list[str] = []
        self.commands: list[repo.CreateAgentRunCommand] = []
        self.threads_issued = 0
        self._history = history
        self._incident = incident or _incident()

        def _resolve(connection: Any, alarm: AlarmRef) -> ResolvedIncident:
            self.order.append("resolve")
            return self._incident

        def _lock(connection: Any, *, lot_id: str, chamber_id: str) -> None:
            self.order.append(f"lock:{lot_id}:{chamber_id}")

        def _read(
            connection: Any, *, lot_id: str, chamber_id: str
        ) -> tuple[guard_repo.IncidentRunRow, ...]:
            self.order.append(f"read:{lot_id}:{chamber_id}")
            return self._history

        def _create(
            connection: Any, command: repo.CreateAgentRunCommand, **kwargs: Any
        ) -> Any:
            self.order.append("create")
            self.commands.append(command)
            if create is not None:
                return create(command)
            return object()

        def _thread() -> str:
            self.order.append("thread")
            self.threads_issued += 1
            return "11111111-2222-4333-8444-555555555555"

        monkeypatch.setattr(guard, "resolve_incident", _resolve)
        monkeypatch.setattr(guard, "lock_incident", _lock)
        monkeypatch.setattr(guard, "read_incident_runs", _read)
        monkeypatch.setattr(guard, "create_agent_run", _create)
        self.thread = _thread

    def start(self, **over: Any) -> Any:
        kwargs: dict[str, Any] = {
            "autonomy_level": 2,
            "thread_id_factory": self.thread,
        }
        kwargs.update(over)
        return guard.start_incident_run(
            _Connection(), _ref(AlarmSource.TRACE, "TA-01"), **kwargs
        )


# ===========================================================================
# 순서 — 이 Task의 계약
# ===========================================================================


class TestTheOrderIsTheContract:
    def test_history_is_read_after_the_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**lock → read.** 뒤집히면 두 caller가 같은 0건을 본다."""

        harness = _Harness(monkeypatch)
        harness.start()

        assert harness.order.index(f"lock:{LOT}:{PHOTO}") < harness.order.index(
            f"read:{LOT}:{PHOTO}"
        )

    def test_the_incident_is_resolved_before_the_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """lock key를 만들려면 incident를 먼저 알아야 한다 — 의도된 순서다."""

        harness = _Harness(monkeypatch)
        harness.start()

        assert harness.order[0] == "resolve"
        assert harness.order[1].startswith("lock:")

    def test_the_lock_uses_the_canonical_incident_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """요청 알람의 raw key가 아니라 **C-1.1이 확정한 canonical key**로 잠근다."""

        harness = _Harness(
            monkeypatch, incident=_incident(lot_id="LOT777", chamber_id="EQP09-PM3")
        )
        harness.start()

        assert "lock:LOT777:EQP09-PM3" in harness.order
        assert "read:LOT777:EQP09-PM3" in harness.order

    def test_the_full_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = _Harness(monkeypatch)
        harness.start()

        assert harness.order == [
            "resolve",
            f"lock:{LOT}:{PHOTO}",
            f"read:{LOT}:{PHOTO}",
            "thread",
            "create",
        ]


# ===========================================================================
# transaction 가드 — SQL도 ID도 만들기 전에
# ===========================================================================


class TestNothingHappensOutsideATransaction:
    def test_no_sql_and_no_id_without_a_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """transaction 밖이면 advisory lock은 즉시 풀린다 — 잠갔다는 말이 거짓이다."""

        harness = _Harness(monkeypatch)
        with pytest.raises(repo.RepositoryContractError) as exc:
            guard.start_incident_run(
                _Connection(in_transaction=False),
                _ref(AlarmSource.TRACE, "TA-01"),
                autonomy_level=2,
                thread_id_factory=harness.thread,
            )

        assert exc.value.code == "NO_ACTIVE_TRANSACTION"
        assert harness.order == []
        assert harness.threads_issued == 0

    @pytest.mark.parametrize(
        "name", ["lock_incident", "read_incident_runs", "start_incident_run"]
    )
    def test_every_entry_point_uses_the_public_wrapper(self, name: str) -> None:
        """세 진입점이 **같은 wrapper**를 건다. 하나라도 빠지면 우회 경로가 생긴다.

        초판은 본문에 C-0.1의 private `_require_transaction(connection)` 문자열이
        있는지를 봤다. 그건 승인된 계획(§5.1-2)과 **반대 구조를 고정하는 것**이었다 —
        wrapper로 고치면 오히려 red가 됐다. 테스트가 잘못된 결합을 지키고 있었다.
        """

        module = guard if name == "start_incident_run" else guard_repo
        body = ast.unparse(_function(Path(module.__file__), name))
        assert "require_active_transaction(connection)" in body, name

    def test_only_the_wrapper_touches_the_private_seam(self) -> None:
        """**private seam에 닿는 자리는 한 곳뿐이다.**

        두 파일이 각각 `_require_transaction`을 import하면 C-0.1이 이름·서명을 바꿀 때
        고쳐야 할 자리가 흩어진다. wrapper 본문 하나만 private을 부른다.
        """

        service = _code_only(Path(guard.__file__))
        assert "_require_transaction" not in service

        repository = Path(guard_repo.__file__).read_text(encoding="utf-8")
        tree = ast.parse(repository)
        callers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and "_require_transaction("
            in _body_only(Path(guard_repo.__file__), node.name)
        }
        assert callers == {"require_active_transaction"}

    def test_the_wrapper_delegates_and_does_not_reimplement(self) -> None:
        """`in_transaction()` 판정을 복제하지 않는다 — 두 벌이면 조용히 갈라진다."""

        body = _body_only(Path(guard_repo.__file__), "require_active_transaction")
        assert body == "_require_transaction(connection)"

    def test_the_wrapper_is_public(self) -> None:
        assert "require_active_transaction" in guard_repo.__all__


# ===========================================================================
# 상태 정책
# ===========================================================================


class TestTheStatePolicy:
    @pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.WAITING_APPROVAL])
    def test_an_active_run_refuses(
        self, monkeypatch: pytest.MonkeyPatch, status: RunStatus
    ) -> None:
        harness = _Harness(monkeypatch, history=(_run(status, "AR-1"),))
        with pytest.raises(IncidentAlreadyRunningError):
            harness.start()
        assert "create" not in harness.order

    def test_a_completed_run_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = _Harness(monkeypatch, history=(_run(RunStatus.COMPLETED, "AR-DONE"),))
        with pytest.raises(IncidentAlreadyProcessedError):
            harness.start()
        assert "create" not in harness.order

    def test_active_is_reported_before_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**순서가 뜻을 갖는다.**

        completed를 먼저 보면 재실행이 진행 중인 incident가 "이미 처리됨"으로
        보고된다. 운영자가 받는 이유가 실제 상태와 달라진다.
        """

        harness = _Harness(
            monkeypatch,
            history=(
                _run(RunStatus.RUNNING, "AR-NOW", minutes=10),
                _run(RunStatus.COMPLETED, "AR-OLD"),
            ),
        )
        with pytest.raises(IncidentAlreadyRunningError):
            harness.start()

    def test_a_failed_history_starts_a_new_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch, history=(_run(RunStatus.FAILED, "AR-F1"),))
        harness.start()

        # `StartedIncidentRun`에 최상위 사본을 두지 않는다 — command가 정본이다.
        assert harness.commands[0].retry_of_run_id == "AR-F1"

    def test_no_history_starts_a_first_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        harness.start()

        assert harness.commands[0].retry_of_run_id is None


class TestTheRetryTarget:
    def test_the_latest_failure_not_the_chain_root(self) -> None:
        """**root로 평탄화하지 않는다.** 새 run은 바로 직전 실패를 가리킨다."""

        history = (
            _run(RunStatus.FAILED, "AR-F3", minutes=20),
            _run(RunStatus.FAILED, "AR-F2", minutes=10),
            _run(RunStatus.FAILED, "AR-F1"),
        )
        assert guard.select_retry_target(history) == "AR-F3"

    def test_completed_and_active_rows_are_not_retry_targets(self) -> None:
        history = (
            _run(RunStatus.COMPLETED, "AR-C", minutes=20),
            _run(RunStatus.FAILED, "AR-F", minutes=10),
        )
        assert guard.select_retry_target(history) == "AR-F"

    def test_no_failure_means_no_target(self) -> None:
        assert guard.select_retry_target(()) is None
        assert guard.select_retry_target((_run(RunStatus.COMPLETED, "AR-C"),)) is None


# ===========================================================================
# thread ID — 거부 경로에서 태우지 않는다
# ===========================================================================


class TestTheThreadIdIsIssuedLast:
    @pytest.mark.parametrize(
        ("history", "error"),
        [
            ((_run(RunStatus.RUNNING, "AR-1"),), IncidentAlreadyRunningError),
            ((_run(RunStatus.COMPLETED, "AR-1"),), IncidentAlreadyProcessedError),
        ],
    )
    def test_a_refused_request_burns_no_thread_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        history: tuple[guard_repo.IncidentRunRow, ...],
        error: type[Exception],
    ) -> None:
        harness = _Harness(monkeypatch, history=history)
        with pytest.raises(error):
            harness.start()
        assert harness.threads_issued == 0

    def test_a_successful_request_issues_exactly_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch)
        harness.start()
        assert harness.threads_issued == 1

    def test_the_default_factory_makes_a_canonical_uuid(self) -> None:
        """`checkpoint.normalize_thread_id()`가 canonical 표기만 받는다."""

        from app.agent.checkpoint import normalize_thread_id

        value = guard.new_thread_id()
        assert normalize_thread_id(value) == value

    def test_the_generator_is_the_common_one(self) -> None:
        """**생성기를 복제하지 않는다.** C-0.2가 `checkpoint.py`에 건 것과 같은 회귀다.

        초판은 `str(uuid.uuid4())`를 이 module에서 다시 정의했다. 지금은 두 구현이 같은
        값을 내지만 **그 일치가 우연에 걸려 있다** — 공용 생성기가 형식을 바꾸면
        `normalize_thread_id()`는 새 형식을 받는데 여기만 `uuid4`를 발급해
        `agent_run.thread_id`와 checkpoint key가 갈린다. 그때 C-0.2의 회귀는 자기
        module만 보므로 여전히 green이다(구현리뷰 PR #159 필수 1).
        """

        from app.common.ids import new_thread_id as common_new_thread_id

        assert guard.new_thread_id is common_new_thread_id
        body = _code_only(Path(guard.__file__))
        assert "uuid" not in body


# ===========================================================================
# C-0.1 command 조립
# ===========================================================================


class TestTheCommandCarriesC11Values:
    def test_the_incident_values_are_passed_through_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """대표를 다시 고르지 않는다 — C-1.1이 정한 값을 그대로 옮긴다."""

        members = (
            _ref(AlarmSource.R03, "R03-09"),
            _ref(AlarmSource.SUMMARY, "SA-02"),
        )
        incident = _incident(
            member_alarms=members,
            requested_alarm=members[1],
            representative_alarm=members[0],
        )
        harness = _Harness(monkeypatch, incident=incident)
        harness.start()

        command = harness.commands[0]
        assert command.lot_id == incident.lot_id
        assert command.chamber_id == incident.chamber_id
        assert command.requested_alarm == members[1]
        assert command.representative_alarm == members[0]
        assert command.member_alarms == members

    def test_the_requested_alarm_survives_not_being_representative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        members = (
            _ref(AlarmSource.R03, "R03-09"),
            _ref(AlarmSource.SUMMARY, "SA-02"),
        )
        harness = _Harness(
            monkeypatch,
            incident=_incident(
                member_alarms=members,
                requested_alarm=members[1],
                representative_alarm=members[0],
            ),
        )
        command_requested = harness.start().incident.requested_alarm
        assert command_requested == members[1]

    def test_the_result_carries_the_incident_for_c21(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C-2.1이 incident를 DB에서 다시 해석하지 않게 함께 돌려준다."""

        incident = _incident()
        harness = _Harness(monkeypatch, incident=incident)
        assert harness.start().incident is incident


# ===========================================================================
# fallback 정규화
# ===========================================================================


class TestTheFallbackIsNormalized:
    def test_active_run_exists_becomes_the_domain_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """partial unique가 발화한 경우도 같은 사실이므로 같은 예외로 옮긴다."""

        def _boom(command: repo.CreateAgentRunCommand) -> Any:
            raise repo.RepositoryConflict("ACTIVE_RUN_EXISTS")

        harness = _Harness(monkeypatch, create=_boom)
        with pytest.raises(IncidentAlreadyRunningError):
            harness.start()

    def test_another_conflict_is_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**다른 conflict는 뜻이 다르다.** 같은 409로 뭉개면 원인이 사라진다."""

        def _boom(command: repo.CreateAgentRunCommand) -> Any:
            raise repo.RepositoryConflict("DUPLICATE_RUN_ALARM")

        harness = _Harness(monkeypatch, create=_boom)
        with pytest.raises(repo.RepositoryConflict) as exc:
            harness.start()
        assert exc.value.code == "DUPLICATE_RUN_ALARM"

    @pytest.mark.parametrize(
        "error",
        [
            repo.RepositoryRetryable("SERIALIZATION_FAILURE"),
            repo.RepositoryContractError("CONSTRAINT_VIOLATION"),
            repo.RepositoryUnavailable("DATABASE_UNAVAILABLE"),
        ],
    )
    def test_other_repository_errors_pass_through(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        """분류를 보존한다. 경합·계약 위반·장애가 전부 409가 되면 안 된다."""

        def _boom(command: repo.CreateAgentRunCommand) -> Any:
            raise error

        harness = _Harness(monkeypatch, create=_boom)
        with pytest.raises(type(error)):
            harness.start()


# ===========================================================================
# DB 오류 번역 — **두 진입점의 경계를 실제로 실행한다**
# ===========================================================================


#: 이 파일이 직접 소유하는 기댓값 표.
#:
#: 생산 `RETRYABLE_SQLSTATES`를 읽어 비교하면 그 표를 바꾸는 변이가 **red가 되지
#: 않는다** — 기대와 구현이 같은 값을 보게 되기 때문이다. C-1.2에서 같은 실수를 했다.
EXPECTED_RETRYABLE: dict[str, str] = {
    "40001": "SERIALIZATION_FAILURE",
    "40P01": "DEADLOCK_DETECTED",
    "55P03": "LOCK_NOT_AVAILABLE",
    "57014": "STATEMENT_CANCELED",
}


class _Driver(Exception):
    """psycopg 예외 모양. `_translate()`는 `orig.sqlstate`만 읽는다."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__("relation agent_run ... SELECT ... host=db user=kosa_app")
        self.sqlstate = sqlstate


class _FailingConnection:
    """`execute()`가 SQLAlchemy 오류를 던지는 fake.

    `_translate()`를 **실제로 거치게** 하는 것이 목적이다. 이미 번역된 예외를 던지는
    mock으로는 두 진입점의 `except SQLAlchemyError` 경계가 한 번도 실행되지 않는다.
    """

    def __init__(self, sqlstate: str) -> None:
        self._sqlstate = sqlstate

    def in_transaction(self) -> bool:
        return True

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        raise OperationalError("SELECT ...", {}, _Driver(self._sqlstate))


class TestDbErrorsAreTranslatedByC01:
    """**두 DB 진입점이 각각 C-0.1 `_translate()`를 거친다.**

    초판 회귀는 `create_agent_run()` mock이 이미 번역된 `RepositoryRetryable`을 던지게
    했을 뿐이라, `lock_incident()`·`read_incident_runs()`의 번역 경계를 한 번도 실행하지
    않았다. 두 함수가 `_translate()` 대신 `RepositoryUnavailable`로 바뀌어도 단위·정상
    경로 container가 그대로 통과했다(구현리뷰 1차 필수 2).
    """

    @pytest.mark.parametrize("sqlstate", sorted(EXPECTED_RETRYABLE))
    @pytest.mark.parametrize("name", ["lock_incident", "read_incident_runs"])
    def test_each_retryable_sqlstate_keeps_its_code(
        self, name: str, sqlstate: str
    ) -> None:
        with pytest.raises(repo.RepositoryRetryable) as exc:
            getattr(guard_repo, name)(
                _FailingConnection(sqlstate), lot_id=LOT, chamber_id=PHOTO
            )
        assert exc.value.code == EXPECTED_RETRYABLE[sqlstate]

    @pytest.mark.parametrize("name", ["lock_incident", "read_incident_runs"])
    def test_a_connection_failure_stays_unavailable(self, name: str) -> None:
        """경합이 아닌 것을 retryable로 승격하지 않는다."""

        with pytest.raises(repo.RepositoryUnavailable) as exc:
            getattr(guard_repo, name)(
                _FailingConnection("08006"), lot_id=LOT, chamber_id=PHOTO
            )
        assert exc.value.code == "DATABASE_UNAVAILABLE"

    @pytest.mark.parametrize("name", ["lock_incident", "read_incident_runs"])
    def test_no_driver_text_reaches_the_caller(self, name: str) -> None:
        """**SQL·접속 정보를 새 예외로 옮기지 않는다.**

        driver 메시지에 table·SQL·host·user가 섞여 온다. `_translate()`는 SQLSTATE만
        읽고 원인은 `raise ... from`으로만 보존한다.
        """

        with pytest.raises(repo.AgentRepositoryError) as exc:
            getattr(guard_repo, name)(
                _FailingConnection("40001"), lot_id=LOT, chamber_id=PHOTO
            )
        rendered = str(exc.value)
        for forbidden in ("SELECT", "agent_run", "host=", "user=", "kosa_app"):
            assert forbidden not in rendered, forbidden

    def test_the_expected_table_matches_c01_exactly(self) -> None:
        """C-0.1이 표를 늘리거나 줄이면 여기서 드러난다."""

        assert EXPECTED_RETRYABLE == dict(repo.RETRYABLE_SQLSTATES)

    def test_the_translation_uses_the_public_seam(self) -> None:
        """`_translate`가 아니라 `translate_db_error()`를 쓴다.

        `require_active_transaction()`에 적용한 것과 같은 논거다 — 새 계층이 C-0.1의
        private 이름에 직접 결합하면 그것을 바꿀 때 고쳐야 할 자리가 흩어진다
        (구현리뷰 PR #159 권고 1).
        """

        body = _code_only(Path(guard_repo.__file__))
        assert "translate_db_error(" in body
        assert "_translate(" not in body
        assert "translate_db_error" in repo.__all__

    def test_no_sqlstate_table_is_duplicated_in_the_new_modules(self) -> None:
        """**복제 계층 0.** 새 mapping을 만들지 않고 C-0.1 것을 쓴다."""

        for module in (guard, guard_repo):
            body = _code_only(Path(module.__file__))
            for sqlstate in EXPECTED_RETRYABLE:
                assert sqlstate not in body, (module.__name__, sqlstate)


# ===========================================================================
# 정적 계약
# ===========================================================================


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}이 없습니다: {path.name}")


def _body_only(path: Path, name: str) -> str:
    """함수 본문에서 **docstring을 걷어낸** 코드.

    docstring이 남으면 "…을 복제하지 않는다"처럼 **금지를 설명하는 문장**이 금지어
    검사에 걸린다. `_code_only()`가 module 단위로 하는 일을 함수 하나에 적용한다.
    """

    node = _function(path, name)
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    return "\n".join(ast.unparse(statement) for statement in body)


def _code_only(path: Path) -> str:
    """docstring을 걷어낸 본문. 금지어 검사가 **설명 문장**에 걸리지 않게 한다."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
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
    def test_every_run_status_is_classified(self) -> None:
        """**enum 안쪽도 전수 분류한다.**

        `_row()`가 계약 밖 문자열을 거부하는 이유를 "정책이 조용히 무시하면 그 무시가
        곧 중복 run 허용"이라고 적었다. 같은 논리가 enum 안에도 적용된다 —
        `ACTIVE_STATUSES ∪ {COMPLETED, FAILED}` 밖의 멤버가 생기면
        `_assert_startable()`은 지나가고 `select_retry_target()`은 `None`을 낸다.
        결과는 "새 run을 만들어도 된다"다.

        지금 `RunStatus`가 정확히 4값이라 동작은 맞다. 문제는 그 정확성이 **enum 밖
        사실에만 걸려 있고 아무 회귀도 붙들지 않는다**는 것이었다(구현리뷰 필수 2).

        나중에 `CANCELED` 같은 값이 늘면 여기서 red가 나고, 그때 "취소된 incident는
        재실행 가능한가"를 정하게 된다. 지금은 그 질문이 던져지지 않은 채 "가능"으로
        답해진다.
        """

        assert set(RunStatus) == guard.ACTIVE_STATUSES | {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
        }

    def test_the_active_set_matches_the_partial_index(self) -> None:
        """**활성의 정의가 두 곳에서 갈라지면** DB와 정책이 어긋난다.

        `002`의 partial unique index가 쓰는 상태 집합과 `ACTIVE_STATUSES`가 같아야
        한다. 다르면 DB는 막는데 정책은 통과시키거나 그 반대가 된다.
        """

        sql = (BACKEND_ROOT / "migrations" / "002_agent_runtime_clean.sql").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"CREATE UNIQUE INDEX ux_agent_run_incident_active.*?"
            r"WHERE status IN \(([^)]*)\)",
            sql,
            re.S,
        )
        assert match is not None, "partial index를 찾지 못했습니다"
        in_sql = {value.strip().strip("'") for value in match.group(1).split(",")}
        assert in_sql == {status.value for status in guard.ACTIVE_STATUSES}

    def test_no_write_sql_in_the_guard_repository(self) -> None:
        """읽기와 잠금만 한다. run 생성은 C-0.1의 것을 부른다."""

        body = _code_only(Path(guard_repo.__file__)).upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert forbidden not in body, forbidden

    def test_no_session_level_advisory_lock(self) -> None:
        """session lock은 **pool로 반납된 연결에 남는다.**"""

        body = _code_only(Path(guard_repo.__file__))
        assert "pg_advisory_xact_lock" in body
        for forbidden in ("pg_advisory_lock(", "pg_advisory_unlock"):
            assert forbidden not in body, forbidden

    def test_the_lock_key_is_not_a_bare_concatenation(self) -> None:
        """`('AB','C')`와 `('A','BC')`가 같은 key가 되면 안 된다."""

        body = _code_only(Path(guard_repo.__file__))
        assert "jsonb_build_array" in body
        assert "|| :chamber_id" not in body
        assert ":lot_id || " not in body

    def test_no_select_star(self) -> None:
        body = _code_only(Path(guard_repo.__file__))
        assert "SELECT *" not in body

    def test_no_isolation_level_is_set(self) -> None:
        """격리 수준은 caller가 정한다 — C-0.1·C-1.1과 같은 계약이다."""

        for module in (guard, guard_repo):
            body = _code_only(Path(module.__file__)).upper()
            assert "SET TRANSACTION" not in body
            assert "ISOLATION LEVEL" not in body

    def test_no_commit_or_rollback(self) -> None:
        """commit하면 advisory lock이 그 시점에 풀려 경쟁 창이 열린다."""

        for module in (guard, guard_repo):
            body = _code_only(Path(module.__file__))
            assert ".commit()" not in body
            assert ".rollback()" not in body
            assert "begin()" not in body

    def test_no_http_status_or_router(self) -> None:
        """409 body는 C-5.1이 만든다."""

        for module in (guard, guard_repo):
            body = _code_only(Path(module.__file__))
            for forbidden in ("409", "status_code", "APIRouter", "HTTPException"):
                assert forbidden not in body, forbidden

    def test_no_run_id_leaks_into_the_domain_error(self) -> None:
        """기존 run ID를 예외에 실으면 다른 사용자의 실행 정보가 흐른다."""

        body = _code_only(Path(guard.__file__))
        assert "IncidentAlreadyRunningError()" in body
        assert "IncidentAlreadyProcessedError()" in body
        assert re.search(r"IncidentAlready\w+Error\([^)]", body) is None

    def test_the_policy_is_not_duplicated_in_sql(self) -> None:
        """상태 판정이 SQL과 Python 두 벌이면 한쪽만 고쳐도 통과한다."""

        body = _code_only(Path(guard_repo.__file__)).upper()
        for forbidden in ("RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED"):
            assert forbidden not in body, forbidden

    def test_no_label_or_score_names(self) -> None:
        for module in (guard, guard_repo):
            body = _code_only(Path(module.__file__))
            for forbidden in ("fault_code", "anomaly_score", "predicted_"):
                assert forbidden not in body, forbidden


class TestTheStatusIsNotSilentlyIgnored:
    def test_an_unknown_status_is_a_contract_error(self) -> None:
        """계약 밖 상태를 무시하면 **그 무시가 곧 중복 run 허용**이다."""

        row = type(
            "R", (), {"agent_run_id": "AR-1", "status": "PAUSED", "started_at": T0}
        )()
        with pytest.raises(repo.RepositoryContractError) as exc:
            guard_repo._row(row)
        assert exc.value.code == "UNKNOWN_RUN_STATUS"
        assert "PAUSED" not in str(exc.value)
