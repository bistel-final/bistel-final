"""`V5-CM-3.3`을 실제 PostgreSQL 16에서 실증한다.

계획 §10.2. **공용 DB에는 접근하지 않는다** — 일회성 container에 base + 002 형상을
세우고 003 전환과 16조합 matrix를 돌린다.

여기서 확인하는 것은 진리표가 아니라 **PostgreSQL이 실제로 어떻게 판정하는가**다.
이 Task의 출발점 자체가 "3값 논리 때문에 익명 CHECK가 반쪽 NULL을 통과시킨다"이므로,
그 사실을 손계산이 아니라 DB로 고정해야 한다.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime as agent_runtime  # noqa: E402
import apply_severity_pair_guard as guard  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402

pytestmark = pytest.mark.container

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

#: `baseline` fixture가 띄운 container endpoint. production engine 회귀 전용.
_BASELINE_ENDPOINT: Any = None
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"
SQL_002 = REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
SQL_003 = REPOSITORY_ROOT / "backend" / "migrations" / "003_agent_run_severity_pair.sql"


def _action_history_ddl() -> str:
    text = (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
    start = text.index("CREATE TABLE action_history (")
    return text[start : text.index(");", start) + 2]


INSERT = f"""INSERT INTO public.{guard.GUARD_TABLE}
    (agent_run_id, thread_id, lot_id, chamber_id,
     requested_alarm_source, requested_alarm_id,
     representative_alarm_source, representative_alarm_id,
     status, autonomy_level, action, severity)
VALUES (%s, %s, %s, %s, 'TRACE', %s, 'TRACE', %s, 'COMPLETED', 1, %s, %s)"""


def _row(index: int, action: str | None, severity: str | None) -> tuple[Any, ...]:
    token = f"{index:016x}"
    return (
        f"RUN-{token}",
        f"cm33-{token}",
        f"LOT-CM33-{index:02d}",
        f"EQP01-PM-CM33-{index:02d}",
        f"TA-CM33-{index:02d}",
        f"TA-CM33-{index:02d}",
        action,
        severity,
    )


def _matrix(cursor: Any) -> tuple[set[tuple[Any, Any]], set[tuple[Any, Any]], set[str]]:
    """16조합을 실제로 INSERT하고 전부 savepoint rollback한다."""

    accepted: set[tuple[Any, Any]] = set()
    rejected: set[tuple[Any, Any]] = set()
    names: set[str] = set()
    for index, (action, severity, _ok) in enumerate(guard.truth_table()):
        cursor.execute("SAVEPOINT m")
        try:
            cursor.execute(INSERT, _row(index, action, severity))
        except psycopg.errors.CheckViolation as exc:
            rejected.add((action, severity))
            names.add(exc.diag.constraint_name or "")
            cursor.execute("ROLLBACK TO SAVEPOINT m")
        else:
            accepted.add((action, severity))
            cursor.execute("ROLLBACK TO SAVEPOINT m")
    return accepted, rejected, names


@pytest.fixture
def baseline() -> Any:
    """002까지 적용한 **CM-3.2 물리 계약 전체** 형상.

    `postcheck_database()`가 base 9 + reference 4 + Runtime 9 = 22 table allowlist와
    `PUBLIC` privilege를 본다. 그 전제를 우회하면 baseline precondition 회귀가
    증명하는 것이 없다(구현리뷰 필수 G).
    """

    import apply_reference_extensions_v5 as v5
    import postgres_transition as transition

    wafer_alter = "\n".join(
        f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
        f"USING wafer::varchar(24);"
        for table in transition.WAFER_ALTER_TABLES
    )
    global _BASELINE_ENDPOINT
    with postgres.one_off_postgres(database="kosa_agent_e2e") as endpoint:
        # production `_engine_for`를 그대로 쓰는 회귀가 이 endpoint를 필요로 한다.
        # fixture 하나당 container 하나이고 test는 순차 실행이라 module 변수로 충분하다.
        _BASELINE_ENDPOINT = endpoint
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname="kosa_agent_e2e",
            user=endpoint.username,
            password=endpoint.password,
        ) as connection:
            cursor = connection.cursor()
            cursor.execute(
                (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
            )
            cursor.execute(wafer_alter)
            cursor.execute(v5.CANONICAL_SQL.read_text(encoding="utf-8"))
            skip = {v5.R03_TABLE, *agent_runtime.RUNTIME_TABLES}
            for name in v5.PRESERVED_TABLES_BY_PROFILE["runtime"]:
                if name in skip:
                    continue
                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS public.{name} "
                    "(stub_id integer PRIMARY KEY, note text)"
                )
            cursor.execute(SQL_002.read_text(encoding="utf-8"))
            connection.commit()
            yield connection


def test_the_predecessor_check_lets_half_null_through(baseline: Any) -> None:
    """**이 Task의 존재 이유를 DB로 고정한다.**

    PostgreSQL CHECK는 결과가 `FALSE`일 때만 거부한다. 익명 CHECK는 반쪽 NULL에서
    식 전체가 `NULL`이 되어 통과한다.

    이 테스트가 깨지면(= baseline이 6건을 거부하면) `V5-CM-3.3`의 전제가 바뀐 것이다.
    """

    cursor = baseline.cursor()
    accepted, rejected, _names = _matrix(cursor)
    baseline.rollback()

    assert len(accepted) == 10, "익명 CHECK는 16조합 중 10건을 수락한다"
    assert len(rejected) == 6

    holes = accepted - guard.ACCEPTED_PAIRS
    assert holes == {
        (None, "LOW"),
        (None, "MEDIUM"),
        (None, "HIGH"),
        ("MONITORING", None),
        ("WARNING", None),
        ("EQP_HOLD", None),
    }, "반쪽 NULL 6종이 구멍이다"


def test_003_closes_every_hole(baseline: Any) -> None:
    """003 적용 후 **수락 4 · 거부 12 · 오판 0**."""

    cursor = baseline.cursor()
    cursor.execute(SQL_003.read_text(encoding="utf-8"))
    baseline.commit()

    accepted, rejected, names = _matrix(cursor)
    baseline.rollback()

    assert accepted == set(guard.ACCEPTED_PAIRS)
    assert len(rejected) == 12
    assert names == {guard.GUARD_CONSTRAINT}, "거부는 전부 named pair guard가 낸다"


def test_the_swap_is_atomic_and_keeps_the_check_count(baseline: Any) -> None:
    """drop+add는 한 문장이라 CHECK 총수가 변하지 않는다."""

    cursor = baseline.cursor()

    def checks() -> set[str]:
        cursor.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.agent_run'::regclass AND contype = 'c'"
        )
        return {row[0] for row in cursor.fetchall()}

    before = checks()
    assert guard.PREDECESSOR_CONSTRAINT in before
    assert guard.GUARD_CONSTRAINT not in before

    cursor.execute(SQL_003.read_text(encoding="utf-8"))
    baseline.commit()

    after = checks()
    assert guard.PREDECESSOR_CONSTRAINT not in after
    assert guard.GUARD_CONSTRAINT in after
    assert len(after) == len(before), "총수 불변 — 교체이지 추가가 아니다"
    assert after - before == {guard.GUARD_CONSTRAINT}
    assert before - after == {guard.PREDECESSOR_CONSTRAINT}


def test_the_measured_definition_matches_the_constant(baseline: Any) -> None:
    """`GUARD_DEFINITION`이 실측값인지 본다.

    손으로 적은 기대값이면 여기서 깨진다.
    """

    cursor = baseline.cursor()
    cursor.execute(SQL_003.read_text(encoding="utf-8"))
    baseline.commit()

    cursor.execute(
        "SELECT pg_get_constraintdef(oid, true) FROM pg_constraint "
        "WHERE conrelid = 'public.agent_run'::regclass AND conname = %s",
        (guard.GUARD_CONSTRAINT,),
    )
    measured = agent_runtime.normalize_catalog_text(cursor.fetchone()[0])
    assert measured == guard.GUARD_DEFINITION


def test_the_matrix_leaves_no_rows(baseline: Any) -> None:
    """수락 4건도 즉시 rollback한다 — 남으면 그것이 데이터다."""

    cursor = baseline.cursor()
    cursor.execute(SQL_003.read_text(encoding="utf-8"))
    baseline.commit()

    cursor.execute(f"SELECT count(*) FROM public.{guard.GUARD_TABLE}")
    before = cursor.fetchone()[0]

    _matrix(cursor)
    baseline.rollback()

    cursor.execute(f"SELECT count(*) FROM public.{guard.GUARD_TABLE}")
    assert cursor.fetchone()[0] == before == 0


def test_003_refuses_a_second_apply(baseline: Any) -> None:
    """재적용은 멈춘다 — 상태를 추정하지 않는다.

    predecessor 부재가 successor 존재보다 **먼저** 걸린다. guard 순서가 그렇고,
    둘 중 어느 쪽이든 "이미 전환된 DB"라는 같은 사실을 가리킨다.
    """

    cursor = baseline.cursor()
    sql = SQL_003.read_text(encoding="utf-8")
    cursor.execute(sql)
    baseline.commit()

    with pytest.raises(psycopg.errors.RaiseException, match="predecessor"):
        cursor.execute(sql)
    baseline.rollback()


def test_003_refuses_when_the_successor_already_exists(baseline: Any) -> None:
    """predecessor는 있는데 successor도 있는 형상 — `PARTIAL_OR_DRIFT`다."""

    cursor = baseline.cursor()
    cursor.execute(
        f"ALTER TABLE public.{guard.GUARD_TABLE} "
        f"ADD CONSTRAINT {guard.GUARD_CONSTRAINT} CHECK (true)"
    )
    baseline.commit()

    with pytest.raises(psycopg.errors.RaiseException, match="이미 있다"):
        cursor.execute(SQL_003.read_text(encoding="utf-8"))
    baseline.rollback()


def test_003_refuses_a_violating_row(baseline: Any) -> None:
    """기존 행이 계약을 위반하면 DDL 전에 멈춘다.

    `NOT VALID`을 쓰지 않는 이유가 이것이다 — 쓰면 이 행이 그대로 남는다.
    """

    cursor = baseline.cursor()
    # 익명 CHECK가 통과시키는 반쪽 NULL을 심는다.
    cursor.execute(INSERT, _row(99, "WARNING", None))
    baseline.commit()

    with pytest.raises(psycopg.errors.RaiseException, match="위반 행"):
        cursor.execute(SQL_003.read_text(encoding="utf-8"))
    baseline.rollback()

    # DDL은 돌지 않았다.
    cursor.execute(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid = 'public.agent_run'::regclass AND conname = %s",
        (guard.GUARD_CONSTRAINT,),
    )
    assert cursor.fetchone()[0] == 0


def test_003_refuses_a_non_runtime_database() -> None:
    """Evaluation DB에는 적용하지 않는다."""

    with postgres.one_off_postgres(database="kosa_text2sql") as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname="kosa_text2sql",
            user=endpoint.username,
            password=endpoint.password,
        ) as connection:
            cursor = connection.cursor()
            with pytest.raises(psycopg.errors.RaiseException, match="runtime database"):
                cursor.execute(SQL_003.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# runner 경로 (구현리뷰 필수 2 · 권장 1)
#
# 위 테스트들은 SQL을 직접 commit한다 — DB 사실을 증명한다.
# 아래는 **runner 자신**을 돌린다. 공용에서 실행할 코드가 CI에서도 같은 순서로
# 검증되어야 한다.
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, *_: object) -> bool:
        return False


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one(self) -> dict[str, Any]:
        if len(self.rows) != 1:
            raise LookupError("정확히 1행이어야 합니다")
        return self.rows[0]

    def scalar_one(self) -> Any:
        """`verify_bootstrap_state._scalar()`가 쓰는 모양.

        runner만 돌 때는 필요 없었다. full verifier를 실제로 부르면서 처음
        도는 경로다(구현리뷰 필수 I-3).
        """

        row = self.one()
        if len(row) != 1:
            raise LookupError("정확히 1열이어야 합니다")
        return next(iter(row.values()))


class _Connection:
    """psycopg cursor를 runner가 기대하는 모양으로 감싼다."""

    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor
        self.statements: list[str] = []

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> _Rows:
        self.statements.append(statement)
        self.cursor.execute(statement, parameters)
        if self.cursor.description is None:
            return _Rows([])
        columns = [column.name for column in self.cursor.description]
        return _Rows(
            [dict(zip(columns, row, strict=True)) for row in self.cursor.fetchall()]
        )

    def execute(self, clause: Any, parameters: Any = None) -> _Rows:
        """`agent_runtime.execute_schema()`가 쓰는 `text()` 경로.

        **이 shim은 production과 다르다.** psycopg는 parameter를 받을 때만
        `%`를 placeholder로 파싱하는데 여기서는 `None`을 넘긴다. 그래서 이
        shim으로는 `%` 결함을 재현할 수 없다 —
        `test_the_production_engine_applies_the_guard`가 그 축을 본다.
        """

        return self.exec_driver_sql(str(clause), parameters)

    def begin(self) -> Any:
        cursor = self.cursor
        log = self.statements

        class _Tx:
            def __enter__(self) -> _Tx:
                log.append("BEGIN")
                cursor.execute("BEGIN")
                return self

            def rollback(self) -> None:
                log.append("ROLLBACK")
                cursor.execute("ROLLBACK")

            def __exit__(self, exc_type: Any, *_: object) -> bool:
                keyword = "ROLLBACK" if exc_type else "COMMIT"
                log.append(keyword)
                cursor.execute(keyword)
                return False

        return _Tx()


def _engine_for(connection: Any) -> Any:
    class _Engine:
        def connect(self) -> Any:
            return _Ctx(connection)

        def dispose(self) -> None:
            return None

    return lambda _target: _Engine()


def _target() -> Any:
    import db_target

    return db_target.BootstrapTarget(
        host="localhost",
        port=5432,
        username="rehearsal",
        password="hidden",
        database="kosa_agent_e2e",
        profile="runtime",
    )


def _stub_predecessor(
    monkeypatch: pytest.MonkeyPatch, connection: Any, tmp_path: Path
) -> None:
    """CM-3.2 marker를 **계약대로** 만든다.

    한 필드짜리 가짜를 쓰면 `guarded_identity()`의 predecessor 검증 구멍을 정상
    경로로 고정하게 된다(구현리뷰 필수 C).
    """

    import json

    path = tmp_path / "pred.json"
    if path.exists():
        # **한 번 만들면 고정이다.** predecessor marker는 baseline 시점의 artifact다.
        # 003 적용 뒤 다시 계산하면 guarded signature가 담겨 receipt와 어긋난다.
        monkeypatch.setattr(agent_runtime, "marker_path", lambda _db, **_k: path)
        return

    sql, _ = agent_runtime.load_and_validate_sql()
    signature = agent_runtime._canonical_hash(
        agent_runtime._json_safe(agent_runtime.build_schema_signature(connection))
    )
    identity = agent_runtime._artifact_identity(_target())
    payload = {
        "artifact_type": "agent_runtime_final",
        "format_version": 1,
        "task_id": "V5-CM-3.2",
        "database": "kosa_agent_e2e",
        "profile": "runtime",
        "status": "VERIFIED_EXISTING",
        **identity,
        "migration_id": "002_agent_runtime_clean",
        "migration_sha256": agent_runtime.migration_sha256(sql),
        "schema_signature_sha256": signature,
        "action_history_rows": 0,
        "change_reference": "GH-121",
        "applied_at": "2026-08-24T00:00:00+00:00",
        "recorded_at": "2026-08-24T00:00:00+00:00",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(agent_runtime, "marker_path", lambda _db, **_k: path)


@pytest.fixture
def runner_db(baseline: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """runner가 붙을 baseline connection.

    `prepare_transaction`은 connected identity·`search_path`를 보는데 격리
    container는 그 계약 밖이다. runner 경로 자체를 보는 것이 목적이므로 그 한 겹만
    건너뛴다.
    """

    monkeypatch.setattr(guard, "prepare_transaction", lambda *a, **k: None)
    yield _Connection(baseline.cursor())


def test_runner_applies_and_issues_a_marker(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BASELINE_MARKED → APPLIED`. receipt 먼저, marker 마지막."""

    import json

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    # predecessor marker가 있어야 successor가 무엇 위에 쌓는지 안다.
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    status, matrix = guard.run_apply(
        _target(),
        change_reference="GH-126",
        engine_factory=_engine_for(runner_db),
        marker_root=marker_root,
        report_root=report_root,
    )

    assert status == "APPLIED"
    assert matrix is not None and matrix.counts == (4, 12)

    marker = json.loads(
        guard.marker_path("kosa_agent_e2e", root=marker_root).read_text(
            encoding="utf-8"
        )
    )
    assert marker["status"] == "APPLIED"
    assert marker["matrix_accepted"] == 4
    assert marker["matrix_rejected"] == 12
    assert marker["agent_run_rows"] == 0
    assert (
        marker["baseline_schema_signature_sha256"]
        != marker["guarded_schema_signature_sha256"]
    ), "교체됐으면 signature가 달라진다"

    receipts = list(report_root.glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "COMMITTED"
    assert marker["applied_at"] == receipt["committed_at"]


def test_a_failing_matrix_rolls_back_the_ddl(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**핵심 승인 Gate.** matrix가 실패하면 DROP+ADD째 rollback된다.

    이것이 apply-time matrix를 같은 transaction에 둔 이유다 — guard가 실제로
    4/12로 동작하지 않으면 애초에 commit하지 않는다.
    """

    import json

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    # predecessor marker가 있어야 successor가 무엇 위에 쌓는지 안다.
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    def _wrong(_connection: Any) -> guard.MatrixResult:
        return guard.MatrixResult(((None, None),), (), ())

    monkeypatch.setattr(guard, "run_matrix", _wrong)

    with pytest.raises(guard.SeverityGuardStateError, match="16조합"):
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )

    # DDL이 되돌아갔다 — predecessor가 살아 있고 guard는 없다.
    cursor = runner_db.cursor
    cursor.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'public.agent_run'::regclass AND contype = 'c'"
    )
    names = {row[0] for row in cursor.fetchall()}
    assert guard.PREDECESSOR_CONSTRAINT in names, "predecessor가 복원돼야 한다"
    assert guard.GUARD_CONSTRAINT not in names

    assert not list(marker_root.iterdir()), "marker 0건"
    aborted = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in report_root.glob("*.json")
    ]
    assert [item["status"] for item in aborted] == ["ABORTED"]


def test_a_guarded_catalog_passes_the_stage_aware_postcheck(runner_db: Any) -> None:
    """**필수 A.** 정상 guarded DB가 baseline allowlist에서 실패하면 안 된다.

    구현은 `postcheck_database()`가 CM-3.2 baseline을 전수 exact 비교해
    `-agent_run_check1 +ck_...pair`가 그대로 mismatch로 잡혔다. AST 회귀는 그
    false-negative를 잡지 못한다 — **실제 catalog를 통과시켜야** 안다.
    """

    cursor = runner_db.cursor
    cursor.execute(SQL_003.read_text(encoding="utf-8"))
    cursor.execute("COMMIT")
    cursor.execute("BEGIN")

    # `postcheck_database()`는 `v_alarm_event`까지 요구한다 — 여기 fixture 범위 밖이다.
    # constraint allowlist 축만 직접 본다. 그것이 필수 A의 실패 지점이다.
    assert (
        agent_runtime.inspect_database(
            runner_db, expected_constraints=agent_runtime.EXPECTED_CONSTRAINTS
        ).state
        == "DRIFT"
    ), "baseline allowlist로는 정상 guarded가 DRIFT로 잡힌다"

    inspection = agent_runtime.inspect_database(
        runner_db, expected_constraints=guard.GUARDED_CONSTRAINTS
    )
    assert inspection.state == "PRESENT", "guarded allowlist로는 통과해야 한다"
    assert len(inspection.schema_signature_sha256 or "") == 64


def test_the_predecessor_stage_still_uses_the_baseline_allowlist(
    runner_db: Any,
) -> None:
    """`runtime_clean`은 기존 계약 그대로다 — successor가 predecessor를 안 깬다."""

    assert (
        agent_runtime.inspect_database(
            runner_db, expected_constraints=agent_runtime.EXPECTED_CONSTRAINTS
        ).state
        == "PRESENT"
    )
    # 반대로 guarded allowlist로는 DRIFT다 — 두 축이 실제로 갈린다.
    assert (
        agent_runtime.inspect_database(
            runner_db, expected_constraints=guard.GUARDED_CONSTRAINTS
        ).state
        == "DRIFT"
    )


def test_rehearsal_rolls_everything_back(runner_db: Any, tmp_path: Path) -> None:
    """**필수 B.** rehearsal은 증명서를 만들지 않고 전부 되돌린다."""

    matrix = guard.run_rehearse(
        _target(),
        change_reference="GH-126",
        engine_factory=_engine_for(runner_db),
    )
    assert matrix.counts == (4, 12)

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'public.agent_run'::regclass AND contype = 'c'"
    )
    names = {row[0] for row in cursor.fetchall()}
    assert guard.PREDECESSOR_CONSTRAINT in names, "predecessor가 복원돼야 한다"
    assert guard.GUARD_CONSTRAINT not in names
    assert not list(tmp_path.iterdir()), "artifact 0건"


def test_a_valid_existing_row_is_preserved_and_recorded(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**필수 C.** 유효 row가 있어도 전환되고 artifact가 **실측**을 기록한다.

    구현은 0으로 고정해 valid row가 있는 DB에서 거짓을 적었다.
    """

    import json

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(INSERT, _row(50, "WARNING", "MEDIUM"))
    cursor.execute("COMMIT")

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    status, matrix = guard.run_apply(
        _target(),
        change_reference="GH-126",
        engine_factory=_engine_for(runner_db),
        marker_root=marker_root,
        report_root=report_root,
    )
    assert status == "APPLIED"

    marker = json.loads(
        guard.marker_path("kosa_agent_e2e", root=marker_root).read_text(
            encoding="utf-8"
        )
    )
    assert marker["agent_run_rows"] == 1, "실측 1행이 기록돼야 한다"

    receipt = json.loads(next(report_root.glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["agent_run_rows_before"] == 1
    assert receipt["agent_run_rows_after"] == 1

    # 행이 살아 있다.
    cursor.execute("BEGIN")
    cursor.execute(f"SELECT count(*) FROM public.{guard.GUARD_TABLE}")
    assert cursor.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 실행 회귀 (구현리뷰 필수 I)
#
# 위 unit 회귀 일부는 `inspect.getsource()`로 문자열만 봤다. 그것은 "코드에 그
# 문구가 있다"를 증명할 뿐 "그 경로가 실제로 그렇게 동작한다"를 증명하지 않는다.
# ---------------------------------------------------------------------------


def test_a_public_grant_stops_the_apply(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**필수 G.** signature만 보면 `PUBLIC` 권한 drift가 통과한다.

    `inspect_guard()`의 signature에는 privilege가 없다. `postcheck_database()`가
    그 축을 본다.
    """

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(f"GRANT SELECT ON public.{guard.GUARD_TABLE} TO PUBLIC")
    cursor.execute("COMMIT")

    with pytest.raises(guard.SeverityGuardStateError) as caught:
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
    assert caught.value.reason_code == "BASELINE_DRIFT"

    # DDL·artifact 0건.
    cursor.execute("BEGIN")
    cursor.execute(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid = 'public.agent_run'::regclass AND conname = %s",
        (guard.GUARD_CONSTRAINT,),
    )
    assert cursor.fetchone()[0] == 0
    assert not list(marker_root.iterdir())
    assert not list(report_root.iterdir())


def test_a_marker_failure_is_recovered_from_the_receipt(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**필수 I.** marker 저장 실패 → COMMITTED receipt → recovery.

    문자열 검사가 아니라 실제로 그 경로를 밟는다.
    """

    import json

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise guard.SeverityGuardArtifactError("marker 저장에 실패했습니다")

    monkeypatch.setattr(guard, "save_marker", _boom)
    with pytest.raises(guard.SeverityGuardArtifactError):
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )

    # DB는 전환됐고 COMMITTED receipt만 남았다.
    assert not list(marker_root.iterdir())
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in report_root.glob("*.json")
    ]
    assert [item["status"] for item in receipts] == ["COMMITTED"]

    monkeypatch.undo()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)
    status = guard.run_recover_marker(
        _target(),
        change_reference="GH-126",
        engine_factory=_engine_for(runner_db),
        marker_root=marker_root,
        report_root=report_root,
    )
    assert status == "RECOVERED"

    marker = json.loads(
        guard.marker_path("kosa_agent_e2e", root=marker_root).read_text(
            encoding="utf-8"
        )
    )
    receipt = receipts[0]
    # **receipt와 exact 일치한다** — canonical 값을 새로 합성하지 않는다.
    assert marker["matrix_accepted"] == receipt["matrix_accepted"]
    assert marker["matrix_rejected"] == receipt["matrix_rejected"]
    assert marker["agent_run_rows"] == receipt["agent_run_rows_after"]
    assert (
        marker["guarded_schema_signature_sha256"]
        == receipt["guarded_schema_signature_sha256"]
    )


@pytest.mark.parametrize(
    ("label", "field", "value"),
    [
        ("matrix 변조", "matrix_accepted", 3),
        ("guarded signature 변조", "guarded_schema_signature_sha256", "9" * 64),
        ("행 수 변조", "agent_run_rows_after", 7),
        ("change ref 변조", "change_reference", "GH-999"),
    ],
)
def test_a_tampered_receipt_cannot_recover(
    runner_db: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    field: str,
    value: Any,
) -> None:
    """**필수 I.** 단일 변이마다 marker 0건."""

    import json

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise guard.SeverityGuardArtifactError("marker 저장에 실패했습니다")

    monkeypatch.setattr(guard, "save_marker", _boom)
    with pytest.raises(guard.SeverityGuardArtifactError):
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
    monkeypatch.undo()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    path = next(report_root.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(guard.SeverityGuardError):
        guard.run_recover_marker(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
    assert not list(marker_root.iterdir()), "변조 receipt로 marker가 나오면 안 된다"


# ---------------------------------------------------------------------------
# full verifier·guarded postcondition (구현리뷰 필수 I-2 · J)
# ---------------------------------------------------------------------------


def _applied(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """003을 runner로 적용하고 marker·receipt를 남긴다."""

    import json

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir(exist_ok=True)
    report_root.mkdir(exist_ok=True)
    _stub_predecessor(monkeypatch, runner_db, tmp_path)
    guard.run_apply(
        _target(),
        change_reference="GH-126",
        engine_factory=_engine_for(runner_db),
        marker_root=marker_root,
        report_root=report_root,
    )
    marker_path = guard.marker_path("kosa_agent_e2e", root=marker_root)
    return {
        "marker_root": marker_root,
        "report_root": report_root,
        "marker_path": marker_path,
        "marker": json.loads(marker_path.read_text(encoding="utf-8")),
    }


def test_public_drift_after_apply_is_caught_by_verify_and_no_op(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**필수 J.** 적용 후 `PUBLIC` drift를 verify·no-op이 모두 잡아야 한다.

    `inspect_guard()`의 signature에는 privilege가 없어 셋 다 거짓 green이었다.
    """

    state = _applied(runner_db, tmp_path, monkeypatch)

    # 정상 상태에서는 통과한다 — 대조군.
    assert (
        guard.run_verify(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
        )
        == "GUARDED_MARKED"
    )

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(f"GRANT SELECT ON public.{guard.GUARD_TABLE} TO PUBLIC")
    cursor.execute("COMMIT")

    with pytest.raises(guard.SeverityGuardStateError) as caught:
        guard.run_verify(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
        )
    assert caught.value.reason_code == "GUARDED_DRIFT"

    # no-op도 막힌다 — 확인 없는 no-op은 "아무것도 보지 않았다"이다.
    with pytest.raises(guard.SeverityGuardStateError):
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        )

    # preflight도 같은 판정을 쓴다.
    assert (
        guard.run_preflight(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        )
        == "GUARDED_DRIFT"
    )


def test_preflight_reports_baseline_drift_before_apply(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BASELINE_MARKED` 분기도 apply와 같은 물리 계약을 본다.

    필수 J는 guarded 분기만 닫았다. baseline 분기는 `violation_rows()`만 보고
    `BASELINE_MARKED`를 그대로 냈다 — preflight가 "적용 가능"이라 보고한 DB가
    apply에서 `BASELINE_DRIFT`로 막히는 상태였다.
    """

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    # 대조군 — drift가 없으면 적용 가능으로 본다.
    assert (
        guard.run_preflight(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
        == "BASELINE_MARKED"
    )

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(f"GRANT SELECT ON public.{guard.GUARD_TABLE} TO PUBLIC")
    cursor.execute("COMMIT")

    assert (
        guard.run_preflight(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
        == "BASELINE_DRIFT"
    )

    # preflight는 read-only다 — 판정이 바뀌어도 아무것도 쓰지 않는다.
    assert not list(marker_root.iterdir())
    assert not list(report_root.iterdir())


def test_recovery_refuses_to_certify_a_drifted_database(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """복구 경로도 물리 계약을 본다.

    `run_recover_marker()`는 `inspect_guard()` 상태와 receipt만 대조하고
    marker를 썼다. signature에는 `PUBLIC` privilege가 없으므로 drift가 있는 DB에
    "증명서"가 발급될 수 있었다 — 필수 J와 같은 원인이 복구 경로에 남아 있었다.
    """

    import json

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise guard.SeverityGuardArtifactError("marker 저장에 실패했습니다")

    monkeypatch.setattr(guard, "save_marker", _boom)
    with pytest.raises(guard.SeverityGuardArtifactError):
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
    monkeypatch.undo()
    _stub_predecessor(monkeypatch, runner_db, tmp_path)

    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in report_root.glob("*.json")
    ]
    assert [item["status"] for item in receipts] == ["COMMITTED"]

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(f"GRANT SELECT ON public.{guard.GUARD_TABLE} TO PUBLIC")
    cursor.execute("COMMIT")

    with pytest.raises(guard.SeverityGuardStateError) as caught:
        guard.run_recover_marker(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
    assert caught.value.reason_code == "GUARDED_DRIFT"
    # **marker는 나오지 않는다.**
    assert not list(marker_root.iterdir())

    # drift를 되돌리면 같은 receipt로 복구된다 — 거부가 영구 차단이 아니다.
    cursor.execute("BEGIN")
    cursor.execute(f"REVOKE SELECT ON public.{guard.GUARD_TABLE} FROM PUBLIC")
    cursor.execute("COMMIT")
    assert (
        guard.run_recover_marker(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=marker_root,
            report_root=report_root,
        )
        == "RECOVERED"
    )


def test_a_tampered_guarded_signature_is_refused_by_every_entrypoint(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**필수 J-2.** 변조된 증명서를 진입점마다 다르게 판정하면 안 된다.

    `assert_guarded_postcondition()`은 올바른 live signature를 반환하는데
    `run_preflight()`은 그 값을 **버렸고** `run_apply()`의 no-op도 marker와
    비교하지 않았다. 그래서 다음이 나왔다.

    ```text
    PREFLIGHT_TAMPERED_MARKER  GUARDED_MARKED   ← 거짓 green
    REAPPLY_TAMPERED_MARKER    NO_OP            ← 거짓 green
    VERIFY_TAMPERED_MARKER     REJECT DRIFT     ← 유일하게 정상
    ```
    """

    import json

    state = _applied(runner_db, tmp_path, monkeypatch)
    tampered = dict(state["marker"])
    tampered["guarded_schema_signature_sha256"] = "7" * 64
    state["marker_path"].write_text(json.dumps(tampered), encoding="utf-8")

    # ① verify
    with pytest.raises(guard.SeverityGuardStateError) as caught:
        guard.run_verify(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
        )
    assert caught.value.reason_code == "DRIFT"

    # ② preflight — 상태로 낸다.
    assert (
        guard.run_preflight(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        )
        == "GUARDED_DRIFT"
    )

    # ③ no-op — 재적용도 막힌다.
    with pytest.raises(guard.SeverityGuardStateError) as caught:
        guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        )
    assert caught.value.reason_code == "DRIFT"

    # ④ verify-matrix — verify 위임이라 같이 막힌다.
    with pytest.raises(guard.SeverityGuardStateError):
        guard.run_verify_matrix(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        )


def test_a_tampered_baseline_is_refused_when_the_marker_loads(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """baseline 계보는 **artifact 검증**이 소유한다 — live가 필요 없다.

    `validate_marker()`가 `load_marker()` 안에서 identity와 대조하므로, marker를
    읽는 모든 진입점이 같은 지점에서 같은 예외로 멈춘다. runner 본문에서 또
    비교하면 **도달할 수 없는 분기**가 된다(구현리뷰 필수 J-2 보완 중 확인).
    """

    import json

    state = _applied(runner_db, tmp_path, monkeypatch)
    tampered = dict(state["marker"])
    tampered["baseline_schema_signature_sha256"] = "8" * 64
    state["marker_path"].write_text(json.dumps(tampered), encoding="utf-8")

    for call in (
        lambda: guard.run_verify(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
        ),
        lambda: guard.run_preflight(
            _target(),
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        ),
        lambda: guard.run_apply(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        ),
    ):
        with pytest.raises(guard.SeverityGuardArtifactError):
            call()


def test_verify_matrix_inherits_the_guarded_postcondition(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--verify-matrix`는 `run_verify()`에 의존하므로 같은 계약을 물려받는다."""

    state = _applied(runner_db, tmp_path, monkeypatch)
    matrix = guard.run_verify_matrix(
        _target(),
        change_reference="GH-126",
        engine_factory=_engine_for(runner_db),
        marker_root=state["marker_root"],
        report_root=state["report_root"],
    )
    assert matrix.counts == (4, 12)

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(f"GRANT SELECT ON public.{guard.GUARD_TABLE} TO PUBLIC")
    cursor.execute("COMMIT")

    with pytest.raises(guard.SeverityGuardStateError):
        guard.run_verify_matrix(
            _target(),
            change_reference="GH-126",
            engine_factory=_engine_for(runner_db),
            marker_root=state["marker_root"],
            report_root=state["report_root"],
        )


def test_the_guarded_mismatch_helper_covers_both_markers(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_guarded_mismatches()` 단위 계약. **full verifier 주장은 하지 않는다.**

    이름과 docstring이 `verify_database()`를 주장했으나 실제로는 이 helper만
    불렀다(구현리뷰 필수 I-3). full 경로는
    `test_the_full_verifier_runs_end_to_end_on_the_guarded_stage`가 본다.
    """

    import json

    import verify_bootstrap_state as verifier

    state = _applied(runner_db, tmp_path, monkeypatch)
    monkeypatch.setattr(guard, "marker_path", lambda _db, **_k: state["marker_path"])

    # 정상: predecessor·successor·live가 일관된다.
    assert (
        verifier._guarded_mismatches(
            runner_db,
            _target(),
            require_marker=True,
            runtime_result=None,
        )
        == []
    )

    # successor의 guarded signature 변조 → GUARD_MARKER
    tampered = dict(state["marker"])
    tampered["guarded_schema_signature_sha256"] = "9" * 64
    state["marker_path"].write_text(json.dumps(tampered), encoding="utf-8")
    assert verifier._guarded_mismatches(
        runner_db, _target(), require_marker=True, runtime_result=None
    ) == [{"mismatch_kind": "GUARD_MARKER"}]

    # successor의 baseline이 predecessor와 어긋나면 → GUARD_MARKER
    tampered = dict(state["marker"])
    tampered["baseline_schema_signature_sha256"] = "8" * 64
    state["marker_path"].write_text(json.dumps(tampered), encoding="utf-8")
    assert verifier._guarded_mismatches(
        runner_db, _target(), require_marker=True, runtime_result=None
    ) == [{"mismatch_kind": "GUARD_MARKER"}]


def _live_shape(
    runner_db: Any,
) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    """live의 base table column 이름·논리 type을 잰다.

    verifier의 `_table_names()`와 **같은 집합**이어야 한다 — 그쪽은
    `table_type = 'BASE TABLE'`로 view를 제외한다. type은 verifier가 쓰는
    `format_type()` + `logical_type()`을 그대로 통과시킨다.
    """

    import verify_bootstrap_state as verifier

    cursor = runner_db.cursor
    cursor.execute("BEGIN")
    cursor.execute(
        """
        SELECT c.relname AS table_name,
               a.attname AS column_name,
               format_type(a.atttypid, a.atttypmod) AS data_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
        """
    )
    columns: dict[str, list[str]] = {}
    types: dict[str, dict[str, str]] = {}
    for table, column, data_type in cursor.fetchall():
        columns.setdefault(str(table), []).append(str(column))
        types.setdefault(str(table), {})[str(column)] = verifier.logical_type(
            str(data_type)
        )
    cursor.execute("COMMIT")
    return columns, types


def _schema_only_candidate(
    columns: Mapping[str, list[str]],
) -> dict[str, Any]:
    """live에서 잰 table·column으로 candidate를 만든다.

    content 축(`row_count`·`content_hash`)은 계약이 허용하는 `schema_only`로
    대체한다 — container fixture는 최종 dataset이 아니다. **Runtime postcheck·
    marker 분기·mismatch 병합은 대체하지 않는다.**

    `action_history`만 예외다. stage 계약이 policy·행 수를 고정하므로 등록본
    entry를 그대로 쓰고 column만 live에 맞춘다.
    """

    import json

    registered = json.loads(
        (
            REPOSITORY_ROOT / "infra/bootstrap/manifests/runtime.runtime_guarded.json"
        ).read_text(encoding="utf-8")
    )
    tables: dict[str, dict[str, Any]] = {}
    for table, cols in sorted(columns.items()):
        entry = registered["tables"].get(table)
        if entry is not None and entry["verification_policy"] != "immutable_content":
            tables[table] = {**entry, "columns": list(cols)}
        else:
            tables[table] = {
                "columns": list(cols),
                "verification_policy": "schema_only",
            }
    candidate = dict(registered)
    candidate["tables"] = tables
    return candidate


def test_the_full_verifier_runs_end_to_end_on_the_guarded_stage(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**필수 I-3.** `verify_database(runtime_guarded)`를 **실제로 호출한다.**

    이전 회귀는 `_guarded_mismatches()`만 불러 놓고 full verifier를 주장했다.
    그래서 다음이 증명되지 않았다.

    - stage-aware Runtime postcheck가 guarded allowlist를 넘기는지
    - predecessor marker를 live guarded signature와 **직접 비교하지 않는** 분기
    - `_guarded_mismatches()` 반환값이 `mismatches`에 실제 병합되는지
    - 정상 종료가 `CheckResult(PASS)`인지

    ## 무엇을 주입하는가

    fixture는 RAG table 3종(`document`·`document_chunk`·`nl_query_log`)을 stub으로
    만든다 — `document_chunk.embedding`이 `vector`이고 **container image에 pgvector가
    없다**(`pg_available_extensions` 조회 결과 0건). 그 column type 기대값만 live로
    바꾼다. **guard와 무관한 외부 축이다.** `agent_run`을 포함한 나머지 table은
    실제 계약 type으로 그대로 검증된다.
    """

    import json

    import verify_bootstrap_state as verifier

    state = _applied(runner_db, tmp_path, monkeypatch)
    monkeypatch.setattr(guard, "marker_path", lambda _db, **_k: state["marker_path"])
    monkeypatch.setattr(verifier, "load_bootstrap_target", lambda *_a, **_k: _target())

    columns, live_types = _live_shape(runner_db)
    candidate = _schema_only_candidate(columns)

    # stub으로 만들어진 table만 골라 기대 type을 live로 바꾼다.
    real_types = verifier._expected_column_types
    stubbed = {
        table for table, actual in live_types.items() if actual != real_types(table)
    }
    assert stubbed == {"document", "document_chunk", "nl_query_log"}, stubbed

    def _expected(table: str) -> dict[str, str]:
        return live_types[table] if table in stubbed else real_types(table)

    monkeypatch.setattr(verifier, "_expected_column_types", _expected)

    def _verify() -> Any:
        return verifier.verify_database(
            "kosa_agent_e2e",
            guard.GUARDED_STAGE,
            engine_factory=_engine_for(runner_db),
            candidate=candidate,
            require_runtime_marker=True,
        )

    # ① 일관된 상태 → PASS.
    result = _verify()
    assert result.status == verifier.STATUS_PASS
    assert result.exit_code == verifier.EXIT_OK

    def _kinds() -> set[str]:
        with pytest.raises(verifier.AcceptanceMismatchError) as caught:
            _verify()
        return {item["mismatch_kind"] for item in caught.value.details["mismatches"]}

    # ② successor marker의 guarded signature 변조 → GUARD_MARKER.
    tampered = dict(state["marker"])
    tampered["guarded_schema_signature_sha256"] = "9" * 64
    state["marker_path"].write_text(json.dumps(tampered), encoding="utf-8")
    assert _kinds() == {"GUARD_MARKER"}

    # ③ successor marker의 baseline 계보 변조 → GUARD_MARKER.
    tampered = dict(state["marker"])
    tampered["baseline_schema_signature_sha256"] = "8" * 64
    state["marker_path"].write_text(json.dumps(tampered), encoding="utf-8")
    assert _kinds() == {"GUARD_MARKER"}


def test_the_production_engine_applies_the_guard(
    baseline: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**권장 1 closure.** production `_engine_for`로 003을 실제 적용한다.

    다른 회귀는 custom `_Connection`을 쓴다. 그 shim은 `cursor.execute(sql, None)`
    으로 부르는데 **psycopg는 parameter를 받을 때만 `%`를 placeholder로 파싱한다.**
    production의 SQLAlchemy 경로는 빈 parameter를 넘기므로 파싱이 켜진다.

    그래서 shim 30건이 전부 green인 상태로, **공용 `kosa_agent_e2e`에 대한 실제
    `--rehearse` 실행**이 이렇게 죽었다(2026-08-24, 영구 DDL 0건·artifact 0건).

    ```text
    psycopg.ProgrammingError:
      only '%s', '%b', '%t' are allowed as placeholders, got '%''
    ```

    003의 DO guard에 `RAISE EXCEPTION '... : %', current_database()`가 있기
    때문이다. `agent_runtime.execute_schema()`가 `text()` 컴파일로 escape한다 —
    CM-3.2가 이미 겪고 고친 문제였고 이 runner가 재사용하지 않았다.

    이 회귀는 **production plumbing을 그대로 탄다.** 다른 회귀와 달리
    `prepare_transaction()`을 우회하지 않으므로 다음이 전부 실물이다.

    ```text
    _engine_for                            실제 SQLAlchemy engine
    validate_connected_identity()          current_user·database 대조
    set_and_validate_public_search_path()  search_path 고정
    acquire_advisory_lock()                advisory lock
    TextClause statement 실행              execute_schema()
    APPLIED → VERIFY → NO_OP               marker·receipt lifecycle
    ```
    """

    import db_target

    assert _BASELINE_ENDPOINT is not None
    target = db_target.BootstrapTarget(
        host=_BASELINE_ENDPOINT.host,
        port=int(_BASELINE_ENDPOINT.port),
        username=_BASELINE_ENDPOINT.username,
        password=_BASELINE_ENDPOINT.password,
        database="kosa_agent_e2e",
        profile="runtime",
    )
    # **`prepare_transaction()`을 우회하지 않는다.** 그것이 소유한
    # `validate_connected_identity()`·`set_and_validate_public_search_path()`·
    # `acquire_advisory_lock()`까지 실물로 돈다.
    _stub_predecessor(monkeypatch, _Connection(baseline.cursor()), tmp_path)

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()

    status, matrix = guard.run_apply(
        target,
        change_reference="GH-126",
        marker_root=marker_root,
        report_root=report_root,
    )
    assert status == "APPLIED"
    assert matrix is not None
    assert matrix.counts == (4, 12)

    # 재실행은 no-op이고, 그 판정도 production engine으로 돈다.
    assert guard.run_verify(target, marker_root=marker_root) == "GUARDED_MARKED"
    again, _ = guard.run_apply(
        target,
        change_reference="GH-126",
        marker_root=marker_root,
        report_root=report_root,
    )
    assert again == "NO_OP"


def test_the_predecessor_stage_marker_branch_is_unchanged(
    runner_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runtime_clean`은 기존 계약 그대로다 — successor가 predecessor를 안 깬다."""

    import verify_bootstrap_state as verifier

    assert ("runtime", "runtime_clean") in verifier.RUNTIME_POSTCHECK_STAGES
    assert ("runtime", guard.GUARDED_STAGE) in verifier.RUNTIME_POSTCHECK_STAGES
    # 003 적용 전에는 guarded 분기가 성립하지 않는다.
    assert verifier._guarded_mismatches(
        runner_db, _target(), require_marker=False, runtime_result=None
    ) == [{"mismatch_kind": "GUARD_SCHEMA"}]
