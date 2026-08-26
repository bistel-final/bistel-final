"""`V5-C-0.2` thread·checkpoint 계약 단위 회귀.

DB 없이 확인할 수 있는 축만 여기 둔다. 실제 interrupt·연결 재개·동일 thread resume는
`test_agent_checkpoint_container.py`가 소유한다.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import checkpoint as ck  # noqa: E402
from app.common.ids import new_agent_run_id, new_thread_id  # noqa: E402
from tests.unit import checkpoint_state_guard as guard  # noqa: E402


def _code_only(module_path: Path) -> str:
    """docstring을 뺀 본문 코드만 낸다.

    `ast.unparse()`는 docstring을 그대로 담아, "`setup()`을 부르지 않는다"는 설명이
    호출로 오인된다.
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


# --- thread identity --------------------------------------------------------


class TestThreadIdentity:
    def test_the_generator_is_the_common_one(self) -> None:
        """별도 UUID 생성기를 복제하지 않는다."""

        assert ck.new_thread_id is new_thread_id
        body = _code_only(Path(ck.__file__))
        assert "uuid.uuid4" not in body

    def test_a_new_thread_id_is_canonical_and_36_chars(self) -> None:
        value = ck.new_thread_id()
        assert len(value) == ck.THREAD_ID_LENGTH == 36
        assert ck.normalize_thread_id(value) == value
        assert str(uuid.UUID(value)) == value

    def test_thread_and_run_ids_are_independent(self) -> None:
        """checkpoint key와 업무 식별자는 서로 다른 공간이다."""

        thread, run = ck.new_thread_id(), new_agent_run_id()
        assert thread != run
        assert run.startswith("RUN-") and not thread.startswith("RUN-")
        assert len(thread) == 36 and len(run) == 20

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "not-a-uuid",
            123,
            None,
            b"0e6935da-2fe6-4be2-8be7-84848c20f38c",
        ],
    )
    def test_garbage_is_refused(self, value: Any) -> None:
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.normalize_thread_id(value)
        assert exc.value.reason_code == "INVALID_THREAD_ID"

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(str.upper, id="대문자"),
            pytest.param(lambda v: "{" + v + "}", id="중괄호"),
            pytest.param(lambda v: "urn:uuid:" + v, id="urn 접두사"),
            pytest.param(lambda v: v.replace("-", ""), id="하이픈 없음"),
        ],
    )
    def test_non_canonical_notation_is_refused(self, mutate: Any) -> None:
        """**parse 통과로 끝내지 않는다.**

        `uuid.UUID()`는 이 표기들을 전부 받아들인다. 그대로 checkpoint key로 쓰면 같은
        thread가 표기 차이로 갈라진다 — DB의 `thread_id`는 문자열 비교다.
        """

        canonical = ck.new_thread_id()
        variant = mutate(canonical)
        # 같은 UUID이지만 문자열이 다르다는 것을 먼저 고정한다.
        assert uuid.UUID(variant) == uuid.UUID(canonical)
        assert variant != canonical
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.normalize_thread_id(variant)
        assert exc.value.reason_code == "INVALID_THREAD_ID"


class TestThreadConfig:
    def test_the_config_has_exactly_one_identity_key(self) -> None:
        thread = ck.new_thread_id()
        config = ck.build_thread_config(thread)
        assert config == {"configurable": {"thread_id": thread}}
        assert list(config["configurable"]) == ["thread_id"]

    def test_a_run_id_cannot_be_used_as_a_thread(self) -> None:
        """`agent_run_id`를 checkpoint key로 넣는 우회를 막는다."""

        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_thread_config(new_agent_run_id())
        assert exc.value.reason_code == "INVALID_THREAD_ID"

    def test_the_caller_does_not_assemble_a_checkpoint_id(self) -> None:
        """재개는 첫 실행과 같은 base config를 쓴다."""

        body = _code_only(Path(ck.__file__))
        assert "checkpoint_id" not in body
        assert "checkpoint_ns" not in body


# --- 연결 수명주기 ----------------------------------------------------------


class TestDurabilityIsFailClosed:
    """비-autocommit 연결은 **saver를 만들기 전에** 거부한다.

    `PostgresSaver`는 쓰기에서 pipeline/transaction을 쓴다. 그래서 autocommit이 아닌
    연결에서는 연결을 닫는 순간 checkpoint가 사라진다. 그 성질이 이 Task의 존재
    이유이므로 caller가 `commit()`을 기억하는 계약으로 두지 않는다.
    """

    def test_a_non_autocommit_connection_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import psycopg

        conn = object.__new__(psycopg.Connection)
        monkeypatch.setattr(type(conn), "autocommit", False, raising=False)
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(conn)
        assert exc.value.reason_code == "CHECKPOINT_AUTOCOMMIT_REQUIRED"

    @pytest.mark.parametrize(
        "kwargs",
        [
            None,
            {},
            {"autocommit": False},
            {"autocommit": "true"},
            {"prepare_threshold": 0},
        ],
    )
    def test_a_pool_without_declared_autocommit_is_refused(self, kwargs: Any) -> None:
        """`configure` callback에만 의존하는 불투명 설정도 여기서 걸린다."""

        import psycopg_pool

        pool = object.__new__(psycopg_pool.ConnectionPool)
        pool.kwargs = kwargs
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(pool)
        assert exc.value.reason_code == "CHECKPOINT_POOL_CONFIG_INVALID"

    @pytest.mark.parametrize("value", [None, "conn", 42, object()])
    def test_a_foreign_object_is_refused(self, value: Any) -> None:
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(value)
        assert exc.value.reason_code == "CHECKPOINT_CONNECTION_INVALID"

    def test_the_check_precedes_saver_construction(self) -> None:
        """거부는 saver·SQL보다 앞이다."""

        body = _code_only(Path(ck.__file__))
        assert body.index("_assert_durable(conn_or_pool)") < body.index("PostgresSaver")


class TestModuleOwnsNoSchemaOrConnection:
    def test_setup_is_never_called(self) -> None:
        """시스템설계 §12.2 — 앱이 DDL을 자동 수행하지 않는다."""

        body = _code_only(Path(ck.__file__))
        assert ".setup(" not in body

    def test_no_connection_or_engine_is_created(self) -> None:
        body = _code_only(Path(ck.__file__))
        for forbidden in (
            "psycopg.connect",
            "connect(",
            "create_engine",
            "from_conn_string",
            "ConnectionPool(",
            "getenv",
            "environ",
        ):
            assert forbidden not in body, forbidden

    def test_import_has_no_side_effect(self) -> None:
        """import 시 DB 연결·전역 상태가 생기지 않는다."""

        tree = ast.parse(Path(ck.__file__).read_text(encoding="utf-8"))
        calls = [
            node
            for node in tree.body
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        ]
        assert calls == []

    def test_the_whole_app_package_never_calls_setup(self) -> None:
        """생산 코드 전체에서 0건이어야 한다 — 이 모듈만의 문제가 아니다."""

        offenders = [
            path
            for path in (BACKEND_ROOT / "app").rglob("*.py")
            if ".setup(" in _code_only(path)
        ]
        assert offenders == []


class TestSanitizedErrors:
    def test_no_reason_code_leaks_sql_or_dsn(self) -> None:
        for code in (
            "INVALID_THREAD_ID",
            "CHECKPOINT_AUTOCOMMIT_REQUIRED",
            "CHECKPOINT_POOL_CONFIG_INVALID",
            "CHECKPOINT_CONNECTION_INVALID",
        ):
            message = str(ck.AgentCheckpointError(code))
            for leak in ("postgresql://", "INSERT", "SELECT", "password"):
                assert leak not in message

    def test_the_error_is_not_borrowed_from_the_admin_script(self) -> None:
        """CM-3.4 admin 계약과 이름을 공유하지 않는다."""

        body = _code_only(Path(ck.__file__))
        assert "setup_checkpoint" not in body
        assert "checkpoint_contract" not in body

    def test_the_contract_documents_the_measured_reason(self) -> None:
        """`autocommit=True`가 취향이 아니라 실측 근거임을 모듈이 남긴다."""

        source = Path(ck.__file__).read_text(encoding="utf-8")
        assert "autocommit=False" in source and "checkpoint 0건" in source


class TestPlannedApiIsStable:
    def test_exports_match_the_plan(self) -> None:
        assert set(ck.__all__) == {
            "AgentCheckpointError",
            "THREAD_ID_LENGTH",
            "new_thread_id",
            "normalize_thread_id",
            "build_thread_config",
            "build_postgres_saver",
        }
        for name in ck.__all__:
            assert hasattr(ck, name), name

    def test_build_postgres_saver_takes_one_injected_object(self) -> None:
        params = list(inspect.signature(ck.build_postgres_saver).parameters)
        assert params == ["conn_or_pool"]


# ===========================================================================
# 구현리뷰 1차 필수 2·3 — pool 관측 guard와 State 판정의 변이 감지력
# ===========================================================================


class _FakeConnection:
    def __init__(self, autocommit: Any) -> None:
        self.autocommit = autocommit


@contextlib.contextmanager
def _yielding(value: Any) -> Any:
    yield value


def _fake_pool(
    kwargs: Any,
    *,
    observed: Any = True,
    error: Exception | None = None,
    spy: list[str] | None = None,
) -> Any:
    """`ConnectionPool` 자리표시자. 실제 DB에 붙지 않는다.

    `_assert_pool_durable()`이 쓰는 표면은 `kwargs`와 `connection()` 둘뿐이다.
    """

    import psycopg_pool

    pool = object.__new__(psycopg_pool.ConnectionPool)
    pool.kwargs = kwargs

    def _connection() -> Any:
        if spy is not None:
            spy.append("checkout")
        if error is not None:
            raise error
        return _yielding(_FakeConnection(observed))

    pool.connection = _connection
    return pool


class TestPoolDurabilityIsObservedNotDeclared:
    """**선언이 아니라 관측이다**(구현리뷰 1차 필수 2).

    `psycopg_pool`은 checkout 직전에 `configure`를 부른다. 그래서 `pool.kwargs`가
    `autocommit=True`여도 실제 연결은 `False`일 수 있다. 선언만 보는 guard는 그 pool을
    통과시키고, checkpoint는 반납 시점에 사라진다.
    """

    def test_an_overridden_pool_is_refused(self) -> None:
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(_fake_pool({"autocommit": True}, observed=False))
        assert exc.value.reason_code == "CHECKPOINT_POOL_NOT_DURABLE"

    @pytest.mark.parametrize("observed", [None, "true", 1, 0])
    def test_a_truthy_lookalike_is_not_enough(self, observed: Any) -> None:
        """`is True`다. `1`·`"true"`가 통과하면 판정이 아니라 짐작이다."""

        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(_fake_pool({"autocommit": True}, observed=observed))
        assert exc.value.reason_code == "CHECKPOINT_POOL_NOT_DURABLE"

    def test_an_unobservable_pool_is_refused(self) -> None:
        """판정할 수 없는 것을 통과시키면 fail-closed가 아니다."""

        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(
                _fake_pool({"autocommit": True}, error=RuntimeError("pool is closed"))
            )
        assert exc.value.reason_code == "CHECKPOINT_POOL_UNOBSERVABLE"

    def test_the_failure_carries_no_driver_text(self) -> None:
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(
                _fake_pool(
                    {"autocommit": True},
                    error=RuntimeError("postgresql://user:pw@host:5432/db"),
                )
            )
        assert "postgresql" not in str(exc.value)
        assert "pw" not in str(exc.value)

    def test_a_bad_declaration_never_reaches_checkout(self) -> None:
        """선언이 먼저다 — 잘못 선언한 pool 때문에 연결을 빌리지 않는다."""

        spy: list[str] = []
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(_fake_pool({"autocommit": False}, spy=spy))
        assert exc.value.reason_code == "CHECKPOINT_POOL_CONFIG_INVALID"
        assert spy == []

    def test_a_declared_and_observed_pool_is_accepted(self) -> None:
        """양성 대조군. 둘 다 참이면 saver가 만들어진다."""

        spy: list[str] = []
        saver = ck.build_postgres_saver(_fake_pool({"autocommit": True}, spy=spy))
        assert saver is not None
        assert spy == ["checkout"], "관측을 건너뛰었다"

    def test_a_pool_with_a_reset_callback_is_refused_without_borrowing(self) -> None:
        """**`reset`은 관측으로 잡을 수 없다.**

        `psycopg-pool==3.3.1`의 `_putconn()`은 reset이 있으면 반납을 worker task로
        보낸다. 블록이 끝났다는 것이 reset 완료 barrier가 아니라서, 두 번 관측하는
        방식은 같은 container 회귀가 6회 중 2회 실패했다. 그래서 빌리지 않고 거부한다.
        """

        spy: list[str] = []
        pool = _fake_pool({"autocommit": True}, spy=spy)
        pool._reset = lambda conn: None
        with pytest.raises(ck.AgentCheckpointError) as exc:
            ck.build_postgres_saver(pool)
        assert exc.value.reason_code == "CHECKPOINT_POOL_RESET_UNSUPPORTED"
        assert spy == [], "거부 전에 연결을 빌렸다"

    def test_the_private_reset_attribute_still_exists(self) -> None:
        """**private 속성 의존을 고정한다.**

        `ConnectionPool`에 reset 설정 여부를 알려 주는 public 접근자가 없어
        `_reset`을 읽는다. 이름이 바뀌면 `getattr(..., None)`이 조용히 `None`이 되어
        reset pool을 통과시킨다 — 그 상태를 여기서 red로 만든다.
        """

        import psycopg_pool

        assert ck._RESET_ATTRIBUTE == "_reset"
        with_reset = object.__new__(psycopg_pool.ConnectionPool)
        psycopg_pool.ConnectionPool.__init__(
            with_reset, "postgresql:///x", open=False, reset=lambda c: None
        )
        without = object.__new__(psycopg_pool.ConnectionPool)
        psycopg_pool.ConnectionPool.__init__(without, "postgresql:///x", open=False)
        assert getattr(with_reset, ck._RESET_ATTRIBUTE) is not None
        assert getattr(without, ck._RESET_ATTRIBUTE) is None


class TestStateGuardIsMutationSensitive:
    """판정을 실제 DB 없이 직접 검증한다(구현리뷰 1차 필수 3).

    판정이 container에만 있으면 그 판정이 느슨해져도 어떤 테스트도 red가 되지 않는다.
    """

    @pytest.mark.parametrize(
        "mutation",
        [
            {"predicted_fault_code": "FOC"},
            {"ground_truth_fault_code": "RFM"},
            {"label": "hidden_gold"},
            {"injected_fault": "RFM"},
            {"generator": {"injection": "step-3"}},
            {"password": "hunter2"},
            {"api_key": "sk-live-0"},
            {"webhook_secret": "whsec_0"},
            {"dsn": "postgresql://user:pw@host:5432/db"},
            {"url": "postgresql+psycopg://user:pw@host:5432/db"},
            {"nested": [{"deep": {"api_key": "sk-0"}}]},
            {"nested": ["ok", ["deeper", "ground_truth_fault_code=FOC"]]},
        ],
    )
    def test_a_forbidden_value_is_found(self, mutation: dict[str, Any]) -> None:
        assert guard.find_sensitive(mutation) != [], mutation

    def test_a_clean_state_is_silent(self) -> None:
        clean = {
            "agent_run_id": "RUN-0000000000000001",
            "thread_id": str(uuid.uuid4()),
            "pre_interrupt_count": 1,
            "resume_value": "APPROVED",
            "phase": "COMPLETED",
        }
        assert guard.find_sensitive(clean) == []
        assert guard.domain_fields(clean, ("a", "b")) == guard.ALLOWED_STATE_FIELDS

    def test_both_dsn_spellings_are_caught(self) -> None:
        """`postgresql://`만 적으면 SQLAlchemy 형식 전체 DSN이 통과한다."""

        for spelling in ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"):
            assert guard.find_sensitive({"v": spelling}) != [], spelling

    def test_the_report_does_not_echo_the_value(self) -> None:
        """실패 메시지가 그대로 CI 로그에 남는다 — 값을 담지 않는다."""

        found = guard.find_sensitive({"api_key": "sk-live-SECRETVALUE"})
        assert found
        assert all("SECRETVALUE" not in item for item in found)

    def test_an_unexpected_channel_stays_in_the_domain(self) -> None:
        """`":" in key`를 내부 채널로 뭉뚱그리지 않는다."""

        values = {"branch:to:wait": 1, "wait": 1, "surprise:channel": 1}
        assert guard.domain_fields(values, ("wait",)) == {"surprise:channel"}

    def test_a_missing_field_breaks_the_exact_comparison(self) -> None:
        """**부분집합 비교였다면 통과했을 변이다.**"""

        values = {"agent_run_id": "x", "thread_id": "y"}
        domain = guard.domain_fields(values, ("wait",))
        assert domain <= guard.ALLOWED_STATE_FIELDS
        assert domain != guard.ALLOWED_STATE_FIELDS

    def test_declared_internal_channels_are_exactly_derived(self) -> None:
        assert guard.internal_channels(("wait",)) == {
            "wait",
            "branch:to:wait",
            "__start__",
            "__end__",
            "__interrupt__",
        }


class TestTheSensitiveReportLeaksNothing:
    """**red가 되는 순간에 원문이 새면 guard가 사고를 만든다**(구현리뷰 2차 필수 1).

    민감한 것이 값이 아니라 key 자체일 수 있다. 초판은 key를 경로에 그대로 넣어,
    검사가 성공적으로 실패하는 그 순간에 DSN·비밀번호를 CI 로그로 내보냈다.
    """

    #: 반환 문자열의 **유일한** 모양. 입력에서 온 조각이 들어올 자리가 없다.
    SHAPE = re.compile(r"^\$(\.(key|value)\[\d+\]|\[\d+\])*: [a-z_]+$")

    @pytest.mark.parametrize(
        "payload",
        [
            {"postgresql://sentineluser:sentinelpw@sentinelhost/db": "x"},
            {"api_key=SENTINELSECRET": "x"},
            {"outer": {"webhook_secret=SENTINELSECRET": "x"}},
            {"postgresql+psycopg://sentineluser:sentinelpw@sentinelhost/db": {"a": 1}},
            {"ok": ["fine", {"password=SENTINELSECRET": "x"}]},
        ],
    )
    def test_a_sensitive_key_is_found_without_echoing_it(
        self, payload: dict[str, Any]
    ) -> None:
        found = guard.find_sensitive(payload)
        assert found, payload
        report = " | ".join(found)
        for leak in ("sentineluser", "sentinelpw", "sentinelhost", "SENTINELSECRET"):
            assert leak.lower() not in report.lower(), report

    def test_every_reported_location_matches_the_fixed_shape(self) -> None:
        """모양을 고정한다 — 임의 문자열이 섞일 자리가 구조적으로 없다."""

        payload = {
            "postgresql://u:pw@h/db": {"api_key=S": ["dsn=postgresql://u:pw@h/db"]},
            "clean": 1,
        }
        found = guard.find_sensitive(payload)
        assert found
        assert all(self.SHAPE.match(item) for item in found), found

    def test_the_index_still_locates_the_offender(self) -> None:
        """익명화가 진단을 없애지 않는다 — index로 찾아갈 수 있다."""

        payload = {"a": 1, "b": 2, "api_key": "x"}
        assert guard.find_sensitive(payload) == ["$.key[2]: api_key"]
