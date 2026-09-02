"""`V5-C-1.1` incident 해석 단위 회귀.

DB 없이 닫을 수 있는 것만 여기서 본다. 실제 View 형상·NULL·중복·group 경계는
`test_agent_incident_container.py`가 소유한다 — 단위 fake는 SQL의 의미를 대신 못 한다.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import incident as inc  # noqa: E402
from app.agent import incident_repository as inc_repo  # noqa: E402
from app.agent import repository as repo  # noqa: E402
from app.common.enums import AlarmSource  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402

TRACE = AlarmSource.TRACE
SUMMARY = AlarmSource.SUMMARY
R03 = AlarmSource.R03

LOT = "LOT001"
CHAMBER = "EQP01-PM1"
T0 = datetime(2026, 8, 1, 10, 0, 0)
T1 = datetime(2026, 8, 1, 11, 0, 0)


def _ref(source: AlarmSource, alarm_id: str) -> AlarmRef:
    return AlarmRef(source=source, alarm_id=alarm_id)


def _member(
    source: AlarmSource,
    alarm_id: str,
    occurred_at: datetime | None = T0,
    lot_hist_id: str = "LH0001",
) -> dict[str, Any]:
    return {
        "member_source": source.value,
        "member_alarm_id": alarm_id,
        "member_occurred_at": occurred_at,
        "member_lot_hist_id": lot_hist_id,
    }


def _rows(
    members: list[dict[str, Any]],
    *,
    requested_count: int = 1,
    lot_id: str | None = LOT,
    chamber_id: str | None = CHAMBER,
    unresolved_count: int = 0,
    drift_count: int = 0,
) -> list[Any]:
    """statement가 돌려주는 모양 그대로 만든다 — status가 member마다 반복된다."""

    head = {
        "requested_count": requested_count,
        "lot_id": lot_id,
        "chamber_id": chamber_id,
        "unresolved_count": unresolved_count,
        "drift_count": drift_count,
    }
    if not members:
        # member가 없어도 LEFT JOIN이라 status sentinel 1행은 온다. 그때 member
        # 컬럼은 전부 NULL이다 — 초판은 이 자리를 `SimpleNamespace | None`으로 적어
        # **호출하는 순간 TypeError**였고, 아무도 부르지 않아 드러나지 않았다
        # (구현리뷰 1차 권장 1). 이제 실제 sentinel 모양을 만들고 아래 회귀가 쓴다.
        return [
            SimpleNamespace(
                **head,
                member_source=None,
                member_alarm_id=None,
                member_occurred_at=None,
                member_lot_hist_id=None,
            )
        ]
    return [SimpleNamespace(**head, **member) for member in members]


class _Connection:
    """`.execute(stmt, params).all()`만 흉내 낸다."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.params: dict[str, Any] | None = None

    def execute(self, statement: Any, params: Any = None) -> Any:
        self.params = params
        return SimpleNamespace(all=lambda: self._rows)


class _FailingConnection:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def execute(self, statement: Any, params: Any = None) -> Any:
        raise self._error


def _resolve(rows: list[Any], requested: AlarmRef) -> inc.ResolvedIncident:
    return inc.resolve_incident(_Connection(rows), requested)


def _r03_contract() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    wafers = [
        {"lot_hist_id": f"LH-{index}", "wafer_id": f"W{index}"} for index in range(1, 4)
    ]
    alarms = [
        {"source": "TRACE", "alarm_id": f"TA-{index:02d}"} for index in range(1, 10)
    ]
    return wafers, alarms


def test_r03_persisted_member_contract_is_exact_and_unique() -> None:
    wafers, alarms = _r03_contract()
    parsed_wafers, parsed_alarms = inc_repo.parse_r03_member_contract(
        wafers,
        alarms,
    )

    assert parsed_wafers == (("LH-1", "W1"), ("LH-2", "W2"), ("LH-3", "W3"))
    assert len(parsed_alarms) == 9


@pytest.mark.parametrize("mutation", ["extra_key", "duplicate_wafer", "r03_alarm"])
def test_r03_persisted_member_contract_rejects_shape_drift(mutation: str) -> None:
    wafers, alarms = _r03_contract()
    if mutation == "extra_key":
        wafers[0]["unexpected"] = "x"
    elif mutation == "duplicate_wafer":
        wafers[1] = dict(wafers[0])
    else:
        alarms[0]["source"] = "R03"

    with pytest.raises(repo.RepositoryContractError) as exc:
        inc_repo.parse_r03_member_contract(wafers, alarms)
    assert exc.value.code == "FINAL_DATASET_CONTRACT_MISMATCH"


# --- 대표 선정 --------------------------------------------------------------


class TestRepresentativeOrdering:
    """설계서 774~775행의 exact 순서를 고정한다."""

    def test_priority_is_an_explicit_table_not_a_coincidence(self) -> None:
        assert inc.SOURCE_PRIORITY == {R03: 0, TRACE: 1, SUMMARY: 2}
        # 알파벳이면 R03 < SUMMARY < TRACE가 된다 — 설계서 순서와 다르다.
        alphabetical = sorted(inc.SOURCE_PRIORITY, key=lambda s: s.value)
        by_priority = sorted(inc.SOURCE_PRIORITY, key=inc.SOURCE_PRIORITY.__getitem__)
        assert alphabetical != by_priority

    def test_time_dominates_source_priority(self) -> None:
        """**더 오래된 SUMMARY가 더 최신 R03보다 먼저다.**

        priority를 시간보다 먼저 적용하면 이 단언이 뒤집힌다.
        """

        rows = _rows(
            [
                _member(SUMMARY, "SA-01", T0),
                _member(R03, "RA-01", T1),
            ]
        )
        result = _resolve(rows, _ref(R03, "RA-01"))
        assert result.representative_alarm == _ref(SUMMARY, "SA-01")

    def test_source_priority_breaks_a_time_tie(self) -> None:
        rows = _rows(
            [
                _member(SUMMARY, "SA-01", T0),
                _member(TRACE, "TA-01", T0),
                _member(R03, "RA-01", T0),
            ]
        )
        result = _resolve(rows, _ref(TRACE, "TA-01"))
        assert result.representative_alarm == _ref(R03, "RA-01")
        assert [a.source for a in result.member_alarms] == [R03, TRACE, SUMMARY]

    def test_alarm_id_breaks_the_last_tie(self) -> None:
        rows = _rows(
            [
                _member(TRACE, "TA-09", T0),
                _member(TRACE, "TA-02", T0),
            ]
        )
        result = _resolve(rows, _ref(TRACE, "TA-09"))
        assert result.representative_alarm == _ref(TRACE, "TA-02")
        assert [a.alarm_id for a in result.member_alarms] == ["TA-02", "TA-09"]

    def test_members_and_representative_share_one_sort_key(self) -> None:
        """대표는 member 배열의 첫 원소다 — 두 규칙이 갈라질 자리가 없다."""

        rows = _rows(
            [
                _member(TRACE, "TA-05", T1),
                _member(R03, "RA-01", T0),
                _member(SUMMARY, "SA-03", T1),
            ]
        )
        result = _resolve(rows, _ref(SUMMARY, "SA-03"))
        assert result.member_alarms[0] == result.representative_alarm


# --- 요청 identity ----------------------------------------------------------


class TestRequestedIdentityIsPreserved:
    def test_the_request_survives_when_it_is_not_representative(self) -> None:
        requested = _ref(TRACE, "TA-09")
        rows = _rows(
            [
                _member(SUMMARY, "SA-01", T0),
                _member(TRACE, "TA-09", T1),
            ]
        )
        result = _resolve(rows, requested)
        assert result.representative_alarm == _ref(SUMMARY, "SA-01")
        assert result.requested_alarm == requested
        assert requested in result.member_alarms

    def test_the_lookup_binds_both_source_and_alarm_id(self) -> None:
        connection = _Connection(_rows([_member(TRACE, "TA-01")]))
        inc.resolve_incident(connection, _ref(TRACE, "TA-01"))
        assert connection.params == {"source": "TRACE", "alarm_id": "TA-01"}

    def test_the_same_alarm_id_in_two_sources_stays_two_members(self) -> None:
        """알람 ID 공간은 source별이다(요구사항 §8.1)."""

        rows = _rows(
            [
                _member(TRACE, "A-001", T0),
                _member(SUMMARY, "A-001", T1),
            ]
        )
        result = _resolve(rows, _ref(SUMMARY, "A-001"))
        assert len(result.member_alarms) == 2
        assert {a.source for a in result.member_alarms} == {TRACE, SUMMARY}


# --- fail-closed ------------------------------------------------------------


class TestSnapshotMappingIsFailClosed:
    def test_a_missing_request_is_not_found(self) -> None:
        # 요청이 없으면 candidate도 비어 member 컬럼이 전부 NULL로 온다.
        rows = _rows([], requested_count=0)
        with pytest.raises(repo.RepositoryNotFound) as exc:
            _resolve(rows, _ref(TRACE, "nope"))
        assert exc.value.code == "ALARM_NOT_FOUND"

    def test_a_duplicate_request_is_never_resolved_by_picking_one(self) -> None:
        """요청 identity가 여러 행이면 **어느 incident인지 알 수 없다.**"""

        rows = _rows([_member(TRACE, "TA-01")], requested_count=2)
        with pytest.raises(repo.RepositoryContractError) as exc:
            _resolve(rows, _ref(TRACE, "TA-01"))
        assert exc.value.code == "REQUESTED_ALARM_AMBIGUOUS"

    @pytest.mark.parametrize("key", ["lot_id", "chamber_id"])
    def test_an_unresolved_request_owner_fails(self, key: str) -> None:
        # key가 NULL이면 candidate 자체가 비므로 member도 없다.
        rows = _rows([], **{key: None})
        with pytest.raises(repo.RepositoryContractError) as exc:
            _resolve(rows, _ref(TRACE, "TA-01"))
        assert exc.value.code == "ALARM_OWNER_UNRESOLVED"

    def test_an_unresolved_member_inside_the_incident_fails(self) -> None:
        rows = _rows([_member(TRACE, "TA-01")], unresolved_count=1)
        with pytest.raises(repo.RepositoryContractError) as exc:
            _resolve(rows, _ref(TRACE, "TA-01"))
        assert exc.value.code == "ALARM_OWNER_UNRESOLVED"

    def test_raw_and_canonical_drift_fails(self) -> None:
        rows = _rows([_member(R03, "RA-01")], drift_count=1)
        with pytest.raises(repo.RepositoryContractError) as exc:
            _resolve(rows, _ref(R03, "RA-01"))
        assert exc.value.code == "INCIDENT_KEY_MISMATCH"

    def test_a_null_occurred_at_is_not_silently_ordered(self) -> None:
        rows = _rows(
            [
                _member(TRACE, "TA-01", T0),
                _member(SUMMARY, "SA-01", None),
            ]
        )
        with pytest.raises(repo.RepositoryContractError) as exc:
            _resolve(rows, _ref(TRACE, "TA-01"))
        assert exc.value.code == "ALARM_OCCURRED_AT_MISSING"

    def test_duplicate_member_identity_fails(self) -> None:
        """**요청 중복과 다른 code다.** incident는 특정됐는데 같은 알람이 둘이다.

        어느 행을 버릴지 이 계층이 정하지 않는다.
        """

        rows = _rows(
            [
                _member(TRACE, "TA-01", T0, "LH0001"),
                _member(TRACE, "TA-01", T0, "LH0002"),
            ]
        )
        with pytest.raises(repo.RepositoryContractError) as exc:
            _resolve(rows, _ref(TRACE, "TA-01"))
        assert exc.value.code == "DUPLICATE_MEMBER_ALARM"

    def test_the_mapping_order_is_fixed(self) -> None:
        """요청 수 → owner → scoped unresolved → drift.

        순서가 바뀌면 확정되지 않은 key로 뒤 단계를 판정하게 된다.
        """

        body = ast.unparse(ast.parse(inspect_source(inc_repo.fetch_incident_snapshot)))
        order = [
            body.index("_REQUESTED_MISSING"),
            body.index("_REQUESTED_AMBIGUOUS"),
            body.index("head.lot_id"),
            body.index("unresolved_count"),
            body.index("drift_count"),
        ]
        assert order == sorted(order), order


def inspect_source(obj: Any) -> str:
    import inspect

    return inspect.getsource(obj).lstrip()


# --- C-0.1 인계 -------------------------------------------------------------


class TestHandoffToTheRunCommand:
    """결과를 그대로 C-0.1 command에 넣을 수 있어야 한다."""

    def test_the_result_passes_repository_validation(self) -> None:
        rows = _rows(
            [
                _member(SUMMARY, "SA-01", T0),
                _member(TRACE, "TA-09", T1),
            ]
        )
        result = _resolve(rows, _ref(TRACE, "TA-09"))
        command = repo.CreateAgentRunCommand(
            thread_id="8f14e45f-ceea-467a-9c2b-1f0b8f1a0001",
            lot_id=result.lot_id,
            chamber_id=result.chamber_id,
            autonomy_level=2,
            requested_alarm=result.requested_alarm,
            representative_alarm=result.representative_alarm,
            member_alarms=result.member_alarms,
            llm_model="test-model",
        )
        normalized = repo._validate_create_command(command)
        assert normalized.lot_id == LOT
        assert normalized.chamber_id == CHAMBER

    def test_this_task_never_creates_a_run(self) -> None:
        body = Path(inc.__file__).read_text(encoding="utf-8")
        body += Path(inc_repo.__file__).read_text(encoding="utf-8")
        for forbidden in ("create_agent_run", "append_audit_log", "AuditEvent"):
            assert forbidden not in body, forbidden


# --- 읽기 전용·비누수 ------------------------------------------------------


class TestTheModulesAreReadOnly:
    def test_no_write_sql_and_no_connection_ownership(self) -> None:
        body = Path(inc_repo.__file__).read_text(encoding="utf-8")
        upper = body.upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE"):
            assert forbidden not in upper, forbidden
        for forbidden in ("create_engine", ".commit(", ".rollback(", ".begin("):
            assert forbidden not in body, forbidden

    def test_no_label_or_reference_names_reach_the_query(self) -> None:
        """설계 §6.2 — label·secret·action reference를 실행 문맥에 넣지 않는다."""

        body = Path(inc_repo.__file__).read_text(encoding="utf-8")
        body += Path(inc.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "fault_code",
            "ground_truth",
            "label_source",
            "action_history",
            "anomaly_score",
            "alarm_result",
            "FAULTS",
            "password",
            "postgresql",
        ):
            assert forbidden not in body, forbidden

    def test_the_result_type_carries_only_the_five_handoff_values(self) -> None:
        fields = set(inc.ResolvedIncident.__dataclass_fields__)
        assert fields == {
            "lot_id",
            "chamber_id",
            "requested_alarm",
            "representative_alarm",
            "member_alarms",
        }


# --- C-0.1 오류 계층 재사용 -------------------------------------------------


class TestErrorTranslationIsBorrowedNotCopied:
    """C-0.1의 public read·오류 seam을 재사용하고 분류를 복제하지 않는다."""

    class _Orig(Exception):
        def __init__(self, sqlstate: str) -> None:
            super().__init__("driver detail")
            self.sqlstate = sqlstate
            self.diag = SimpleNamespace(constraint_name=None)

    def test_no_parallel_error_hierarchy_is_declared(self) -> None:
        body = Path(inc_repo.__file__).read_text(encoding="utf-8")
        assert "class Repository" not in body
        assert "SQLSTATE" not in body.upper() or "RETRYABLE_SQLSTATES" not in body

    @pytest.mark.parametrize(
        ("sqlstate", "code"),
        [
            ("40001", "SERIALIZATION_FAILURE"),
            ("40P01", "DEADLOCK_DETECTED"),
            ("55P03", "LOCK_NOT_AVAILABLE"),
            ("57014", "STATEMENT_CANCELED"),
        ],
    )
    def test_contention_reaches_this_module_as_retryable(
        self, sqlstate: str, code: str
    ) -> None:
        error = OperationalError("SELECT 1", {}, self._Orig(sqlstate))
        with pytest.raises(repo.RepositoryRetryable) as exc:
            inc_repo.fetch_incident_snapshot(
                _FailingConnection(error), _ref(TRACE, "TA-01")
            )
        assert exc.value.code == code

    def test_a_connection_failure_reaches_this_module_as_unavailable(self) -> None:
        error = OperationalError("SELECT 1", {}, Exception("no sqlstate"))
        with pytest.raises(repo.RepositoryUnavailable):
            inc_repo.fetch_incident_snapshot(
                _FailingConnection(error), _ref(TRACE, "TA-01")
            )

    def test_a_constraint_error_reaches_this_module_as_contract(self) -> None:
        error = IntegrityError("SELECT 1", {}, self._Orig("23514"))
        with pytest.raises(repo.RepositoryContractError):
            inc_repo.fetch_incident_snapshot(
                _FailingConnection(error), _ref(TRACE, "TA-01")
            )

    def test_no_driver_text_or_sql_reaches_the_caller(self) -> None:
        error = OperationalError("SELECT * FROM v_alarm_event", {}, self._Orig("40P01"))
        with pytest.raises(repo.RepositoryRetryable) as exc:
            inc_repo.fetch_incident_snapshot(
                _FailingConnection(error), _ref(TRACE, "TA-01")
            )
        rendered = str(exc.value).lower()
        assert "select" not in rendered
        assert "v_alarm_event" not in rendered

    def test_this_module_does_not_retry(self) -> None:
        """분류만 보존한다. 재시도 정책은 caller 소관이다.

        문자열로 `retry`를 찾으면 `RepositoryRetryable`이라는 **이름**에 걸린다. 그건
        재시도가 아니라 분류다. 그래서 구조를 본다 — 공용 read seam 호출이 하나이고
        그것을 감싼 loop이 없어야 한다.
        """

        source = Path(inc_repo.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "execute_read_all"
        ]
        assert len(reads) == 1, "공용 read seam 호출 지점이 하나여야 한다"

        for loop in [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.For | ast.While | ast.AsyncFor)
        ]:
            nested = [
                call
                for call in ast.walk(loop)
                if isinstance(call, ast.Call)
                and getattr(call.func, "id", None) == "execute_read_all"
            ]
            assert nested == [], "공용 read seam 호출이 loop 안에 있다"

        for forbidden in ("sleep", "backoff"):
            assert forbidden not in source.lower(), forbidden
