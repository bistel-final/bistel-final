"""`V5-CM-3.2`를 실제 PostgreSQL 16에서 실증한다.

계획 §7.3. **공용 DB에는 접근하지 않는다** — 일회성 container에 base `action_history`와
final reference 형상을 세우고 `ABSENT → APPLIED → EXACT_MARKED` 전이를 돌린다.

단위 테스트 green만으로는 fresh apply를 승인하지 않는다는 것이 계획의 전제다. 여기서
확인하는 것은 fake adapter가 아니라 **PostgreSQL이 실제로 만든 catalog**다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime as runner  # noqa: E402
import apply_reference_extensions_v5 as v5  # noqa: E402
import db_target  # noqa: E402
import postgres_transition as transition  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402

pytestmark = pytest.mark.container

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPOSITORY_ROOT / "backend" / "tests" / "fixtures" / "v5_cm_2_6"


def _action_history_ddl() -> str:
    """base `action_history`만 떼어 온다 — 002의 유일한 외부 FK 대상이다."""

    text = (FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8")
    start = text.index("CREATE TABLE action_history (")
    return text[start : text.index(");", start) + 2]


WAFER_ALTER = "\n".join(
    f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
    f"USING wafer::varchar(24);"
    for table in transition.WAFER_ALTER_TABLES
)


FINAL_ARCHIVE_ENV_KEY = "MENTOR_FINAL_ARCHIVE"


def _final_archive() -> Path:
    """최종 ZIP을 찾는다. **지정됐는데 없거나 hash가 다르면 skip이 아니라 실패다.**

    `V5-CM-3.1` container와 같은 gate다. 이 archive가 없으면 TRACE 138·SUMMARY 51과
    View 189/192를 실증할 수 없다 — CM-2.6 fixture는 legacy 값(126/47)을 담고 있다.
    """

    import os

    import transition_sessions as ts

    declared = os.environ.get(FINAL_ARCHIVE_ENV_KEY, "").strip()
    if not declared:
        pytest.skip(f"{FINAL_ARCHIVE_ENV_KEY}가 없다")
    path = Path(declared).expanduser()
    if not path.is_file():
        pytest.fail(f"{FINAL_ARCHIVE_ENV_KEY}가 가리키는 파일이 없다")
    ts.assert_archive_is_pinned(path)
    return path


def _load_final_dataset(cursor: Any) -> None:
    """최종 ZIP의 9 CSV를 base 9에 적재한다. **완화 flag가 필요 없어진다.**"""

    import csv
    import io

    import transition_sessions as ts

    snapshot = ts.load_profile_snapshots(_final_archive())["runtime"]
    for table in snapshot.tables:
        columns = snapshot.columns_by_table[table]
        body = snapshot.csv_bodies[table].decode("utf-8")
        rows = list(csv.reader(io.StringIO(body)))[1:]
        if not rows:
            continue
        placeholders = ", ".join(["%s"] * len(columns))
        names = ", ".join(f'"{column}"' for column in columns)
        cursor.executemany(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            [[None if value == "" else value for value in row] for row in rows],
        )


def _build_final_runtime_shape(cursor: Any) -> None:
    """공용 Runtime DB의 **적용 전 형상**을 세운다.

    `validate_prerequisites()`가 base 9 + final reference 계보를 요구하기 때문이다.
    그 요구는 정당하다 — 002는 `action_history`를 FK로 참조하고, marker는 001 계보가
    먼저 서 있다는 것을 (V4 marker 대신) live postcheck로 주장한다. 전제를 우회하면
    이 테스트가 증명하는 것이 없다.

    **최종 CSV를 실제로 적재한다.** 1차 구현은 schema와 stub만 세우고 data gate를
    `require_final_dataset=False`로 완화해 통과시켰다 — runner가 성공한 것이 그 완화
    덕분이었다(구현리뷰 2차 필수 1). 지금은 TRACE 138·SUMMARY 51·null owner 0을
    실물에서 만족해야 통과한다.
    """

    cursor.execute((FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8"))
    cursor.execute(WAFER_ALTER)
    _load_final_dataset(cursor)
    cursor.execute(v5.CANONICAL_SQL.read_text(encoding="utf-8"))
    # Runtime 9종은 stub으로 만들지 않는다 — 002가 만드는 것이 이 테스트의 대상이다.
    skip = {v5.R03_TABLE, *runner.RUNTIME_TABLES}
    for name in v5.PRESERVED_TABLES_BY_PROFILE["runtime"]:
        if name in skip:
            continue
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS public.{name} "
            "(stub_id integer PRIMARY KEY, note text)"
        )


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


class _Connection:
    """psycopg cursor를 runner가 기대하는 `exec_driver_sql` 모양으로 감싼다."""

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

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))
        self.cursor.execute(str(statement))

    def begin(self) -> Any:
        """runner가 여는 transaction을 실제 `BEGIN`/`COMMIT`으로 잇는다.

        fixture connection이 autocommit이라 `LOCK TABLE`이 성립하지 않는다. 여기서
        transaction을 실제로 열어야 lock 계약을 실물로 검증할 수 있다.
        """

        cursor = self.cursor
        log = self.statements

        class _Tx:
            def __enter__(self) -> None:
                log.append("BEGIN")
                cursor.execute("BEGIN")

            def __exit__(self, exc_type: Any, *_: object) -> bool:
                keyword = "ROLLBACK" if exc_type else "COMMIT"
                log.append(keyword)
                cursor.execute(keyword)
                return False

        return _Tx()

    @property
    def writes(self) -> list[str]:
        """DDL/DML만 남긴다. **판정은 runner가 소유한다.**

        allowlist를 테스트에 두면 정의가 갈린다 — 실제로 container와 unit 두 벌이
        달랐고, container 쪽만 `BEGIN`/`COMMIT`을 무해로 셌다(PR #123 리뷰 필수 1).
        """

        return runner.mutating_statements(self.statements)


def _session(full: bool) -> Any:
    from contextlib import contextmanager

    @contextmanager
    def _open() -> Any:
        with postgres.one_off_postgres(database="kosa_agent_e2e") as endpoint:
            with psycopg.connect(
                host=endpoint.host,
                port=endpoint.port,
                dbname="kosa_agent_e2e",
                user=endpoint.username,
                password=endpoint.password,
                autocommit=True,
            ) as connection:
                cursor = connection.cursor()
                if full:
                    _build_final_runtime_shape(cursor)
                else:
                    cursor.execute(_action_history_ddl())
                yield _Connection(cursor)

    return _open()


@pytest.fixture
def runtime_db() -> Any:
    """9 table이 **없는** 최소 형상. schema 계약 검증만 하는 테스트가 쓴다."""

    with _session(full=False) as connection:
        yield connection


@pytest.fixture
def final_runtime_db() -> Any:
    """공용 Runtime DB와 같은 **적용 전 전체 형상**. runner 경로가 쓴다."""

    with _session(full=True) as connection:
        yield connection


class _Ctx:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, *_: object) -> bool:
        return False


def _engine_for(connection: Any) -> Any:
    class _Engine:
        def connect(self) -> Any:
            return _Ctx(connection)

        def dispose(self) -> None:
            return None

    return lambda _target: _Engine()


def _target() -> db_target.BootstrapTarget:
    return db_target.BootstrapTarget(
        host="localhost",
        port=5432,
        username="rehearsal",
        password="hidden",
        database="kosa_agent_e2e",
        profile="runtime",
    )


def test_the_sql_creates_the_exact_contracted_schema(runtime_db: Any) -> None:
    """SQL이 만드는 catalog가 **계약과 exact**임을 확인한다.

    이 테스트는 `execute_schema()`를 직접 부른다 — runner의 fresh apply 경로가 아니다.
    그 경로는 `test_runner_fresh_apply_*`가 본다(구현리뷰 필수 3).

    이 테스트가 통과한다는 것은 `EXPECTED_CONSTRAINTS`·`EXPECTED_INDEXES`가
    PostgreSQL 16이 실제로 만드는 것과 같다는 뜻이다. 손으로 적은 기대값이면 여기서
    깨진다.
    """

    assert runner.inspect_database(runtime_db).state == "ABSENT"

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)

    inspection = runner.inspect_database(runtime_db)
    assert inspection.state == "PRESENT", "실제 catalog가 계약과 달랐다"
    assert len(inspection.schema_signature_sha256 or "") == 64


def test_every_runtime_table_starts_empty(runtime_db: Any) -> None:
    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)

    for table in runner.RUNTIME_TABLES:
        rows = runtime_db.exec_driver_sql(
            f'SELECT count(*) AS row_count FROM public."{table}"'
        ).all()
        assert rows[0]["row_count"] == 0, table


def test_public_privileges_are_absent_after_apply(runtime_db: Any) -> None:
    """`PUBLIC` 권한이 남으면 final manifest로 등록될 수 없다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)

    assert runner._privilege_violations(runtime_db) == []


def test_partial_schema_is_detected_and_never_repaired(runtime_db: Any) -> None:
    """9종 중 일부만 있으면 `PARTIAL`이다. `DRIFT`와 원인이 다르다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)
    runtime_db.exec_driver_sql("DROP TABLE public.audit_log CASCADE")

    assert runner.inspect_database(runtime_db).state == "PARTIAL"


def test_broadened_partial_predicate_is_rejected_by_the_real_catalog(
    runtime_db: Any,
) -> None:
    """**구 검증이 놓치던 변이를 실물에서 재현한다.**

    허용 값을 그대로 두고 `OR true`를 붙이면 partial index가 사실상 전체 index가 된다.
    값 집합만 regex로 뽑던 구 검증은 이것을 통과시켰다.
    """

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)
    assert runner.inspect_database(runtime_db).state == "PRESENT"

    runtime_db.exec_driver_sql("DROP INDEX public.ux_agent_run_incident_active")
    runtime_db.exec_driver_sql(
        "CREATE UNIQUE INDEX ux_agent_run_incident_active "
        "ON public.agent_run (lot_id, chamber_id) "
        "WHERE status IN ('RUNNING', 'WAITING_APPROVAL') OR true"
    )

    assert runner.inspect_database(runtime_db).state == "DRIFT"


def test_an_extra_check_constraint_is_drift(runtime_db: Any) -> None:
    """추가 constraint도 drift다. 개수만 세던 구 계약은 종류가 같으면 통과했다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)

    runtime_db.exec_driver_sql(
        "ALTER TABLE public.agent_run ADD CONSTRAINT extra_check CHECK (true)"
    )

    assert runner.inspect_database(runtime_db).state == "DRIFT"


def test_a_legacy_alarm_foreign_key_is_rejected(runtime_db: Any) -> None:
    """AlarmRef를 물리 FK로 묶으면 거부한다 — WBS 완료 기준."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(runtime_db, statements)
    runtime_db.exec_driver_sql(
        "CREATE TABLE public.trace_alarm_history (alarm_id varchar(20) PRIMARY KEY)"
    )
    runtime_db.exec_driver_sql(
        "ALTER TABLE public.agent_run_alarm "
        "ADD CONSTRAINT agent_run_alarm_legacy_fkey "
        "FOREIGN KEY (alarm_id) REFERENCES public.trace_alarm_history(alarm_id)"
    )

    signature = runner.build_schema_signature(runtime_db)
    with pytest.raises(runner.AgentRuntimeStateError):
        runner._validate_signature_contract(signature)

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner._validate_legacy_alarm_fk(signature["constraints"])
    assert caught.value.reason_code == "LEGACY_ALARM_FK"


def test_adopting_a_present_schema_issues_a_marker_without_writing(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """`EXACT_UNMARKED → VERIFIED_EXISTING`을 실물에서 재현한다.

    공용 두 Runtime DB가 이 상태다. adopt 경로에서 발행된 문장 전수를 수집해
    **DDL/DML 0건**을 단언한다.
    """

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    final_runtime_db.statements.clear()

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()

    status, result = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=marker_root,
        report_root=report_root,
    )

    assert status == "VERIFIED_EXISTING"
    assert result.action_history_rows == 0
    assert final_runtime_db.writes == [], final_runtime_db.writes
    assert runner.marker_path("kosa_agent_e2e", root=marker_root).exists()

    # 재실행은 no-op이며 DB에도 저장소에도 쓰지 않는다.
    final_runtime_db.statements.clear()
    again, _ = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=marker_root,
        report_root=report_root,
    )
    assert again == "NO_OP"
    assert final_runtime_db.writes == []


# ---------------------------------------------------------------------------
# runner의 실제 mutation 경로 (구현리뷰 필수 3)
#
# 위 테스트들은 `execute_schema()`를 직접 부른다 — SQL과 drift detector를 본다.
# 아래는 **runner 자신**을 돌린다. 안전 migration runner의 핵심 경로가 단위 mock에만
# 남으면, 그 경로가 실제 PostgreSQL에서 성립하는지는 아무도 모른다.
# ---------------------------------------------------------------------------


def _runtime_table_count(connection: Any) -> int:
    rows = connection.exec_driver_sql(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (list(runner.RUNTIME_TABLES),),
    ).all()
    return int(rows[0]["n"])


def test_runner_rehearsal_rolls_back_every_runtime_object(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """`run_rehearsal()`이 apply 후 **transaction rollback**으로 되돌린다.

    rollback 뒤 9 table이 하나도 남지 않아야 한다. marker·receipt도 0건이다.
    """

    assert _runtime_table_count(final_runtime_db) == 0

    result = runner.run_rehearsal(
        _target(),
        engine_factory=_engine_for(final_runtime_db),
        marker_root=tmp_path / "markers",
    )

    assert result.action_history_rows == 0
    assert _runtime_table_count(final_runtime_db) == 0, "rollback 뒤 객체가 남았다"
    assert not (tmp_path / "markers").exists() or not list(
        (tmp_path / "markers").iterdir()
    )


def test_runner_fresh_apply_commits_and_issues_marker_last(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """`ABSENT → APPLIED` fresh commit을 runner로 실증한다.

    lock → guard → DDL → postcheck → commit → receipt → marker 순서가 실제
    PostgreSQL에서 성립하는지 본다.
    """

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    assert _runtime_table_count(final_runtime_db) == 0

    status, result = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=marker_root,
        report_root=report_root,
    )

    assert status == "APPLIED"
    assert result.action_history_rows == 0
    assert _runtime_table_count(final_runtime_db) == 9

    marker = json.loads(
        runner.marker_path("kosa_agent_e2e", root=marker_root).read_text(
            encoding="utf-8"
        )
    )
    assert marker["status"] == "APPLIED"
    assert marker["schema_signature_sha256"] == result.schema_signature_sha256

    receipts = list(report_root.glob("agent_runtime_final.kosa_agent_e2e.*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "COMMITTED"
    assert receipt["status_result"] == "APPLIED"
    assert marker["applied_at"] == receipt["committed_at"]

    # 9 table 전부 0행이고 base·reference는 그대로다.
    for table in runner.RUNTIME_TABLES:
        rows = final_runtime_db.exec_driver_sql(
            f'SELECT count(*) AS row_count FROM public."{table}"'
        ).all()
        assert rows[0]["row_count"] == 0, table


def test_runner_second_apply_is_a_no_op_on_real_postgres(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """재실행이 `NO_OP`이며 catalog·artifact가 변하지 않는다."""

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    kwargs: dict[str, Any] = {
        "change_reference": "GH-121",
        "engine_factory": _engine_for(final_runtime_db),
        "marker_root": marker_root,
        "report_root": report_root,
    }

    runner.run_apply(_target(), **kwargs)
    marker_bytes = runner.marker_path("kosa_agent_e2e", root=marker_root).read_bytes()
    final_runtime_db.statements.clear()

    status, _ = runner.run_apply(_target(), **kwargs)

    assert status == "NO_OP"
    assert final_runtime_db.writes == [], final_runtime_db.writes
    assert _runtime_table_count(final_runtime_db) == 9
    assert (
        runner.marker_path("kosa_agent_e2e", root=marker_root).read_bytes()
        == marker_bytes
    )
    assert len(list(report_root.glob("*.json"))) == 1


def test_marker_write_failure_leaves_a_committed_receipt_to_recover_from(
    final_runtime_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**commit 뒤 marker 실패**가 recovery로 닫히는지 실물에서 본다.

    이 seam이 없으면 DB는 적용됐는데 증명서가 없는 상태에서 빠져나올 길이 없다.
    """

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    kwargs: dict[str, Any] = {
        "change_reference": "GH-121",
        "engine_factory": _engine_for(final_runtime_db),
        "marker_root": marker_root,
        "report_root": report_root,
    }

    def _boom(*_a: Any, **_k: Any) -> None:
        raise runner.AgentRuntimeArtifactError("marker 저장에 실패했습니다")

    monkeypatch.setattr(runner, "save_marker", _boom)
    with pytest.raises(runner.AgentRuntimeArtifactError):
        runner.run_apply(_target(), **kwargs)

    # DB는 적용됐고 committed receipt는 남았다. marker만 없다.
    assert _runtime_table_count(final_runtime_db) == 9
    assert not list(marker_root.iterdir())
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in report_root.glob("*.json")
    ]
    assert [item["status"] for item in receipts] == ["COMMITTED"]

    monkeypatch.undo()
    status, _ = runner.run_recover_marker(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=marker_root,
        report_root=report_root,
    )

    assert status == "RECOVERED"
    marker = json.loads(
        runner.marker_path("kosa_agent_e2e", root=marker_root).read_text(
            encoding="utf-8"
        )
    )
    # **적용 방식을 승계한다.** fresh apply였으므로 `VERIFIED_EXISTING`이 아니다.
    assert marker["status"] == "APPLIED"

    final_runtime_db.statements.clear()
    again, _ = runner.run_apply(_target(), **kwargs)
    assert again == "NO_OP"
    assert final_runtime_db.writes == []


def test_a_view_definition_drift_blocks_adoption(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """**필수 2 회귀.** View 컬럼만 같은 임의 정의는 final reference가 아니다.

    구현은 R03 columns·constraints·View columns 셋만 봤다. 그러면 정의가 바뀌어도
    `EXACT_UNMARKED` adopt와 marker 발급이 통과한다.
    """

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)

    columns = final_runtime_db.exec_driver_sql(
        "SELECT string_agg(column_name, ', ' ORDER BY ordinal_position) AS cols "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = '{v5.ALARM_VIEW}'"
    ).all()[0]["cols"]
    final_runtime_db.exec_driver_sql(f"DROP VIEW public.{v5.ALARM_VIEW}")
    nulls = ", ".join(
        f"NULL::text AS {name.strip()}" for name in str(columns).split(",")
    )
    final_runtime_db.exec_driver_sql(
        f"CREATE VIEW public.{v5.ALARM_VIEW} AS SELECT {nulls} WHERE false"
    )

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=_engine_for(final_runtime_db),
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )

    assert caught.value.reason_code == "MISSING_FINAL_REFERENCE"
    assert not (tmp_path / "markers").exists()


def test_a_public_grant_blocks_adoption(final_runtime_db: Any, tmp_path: Path) -> None:
    """**필수 2 회귀.** `PUBLIC` grant가 붙은 reference 객체는 채택하지 않는다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    final_runtime_db.exec_driver_sql(f"GRANT SELECT ON public.{v5.R03_TABLE} TO PUBLIC")

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=_engine_for(final_runtime_db),
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )

    assert caught.value.reason_code == "MISSING_FINAL_REFERENCE"


def test_a_removed_r03_comment_blocks_adoption(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """**필수 2 회귀.** comment도 schema identity의 일부다(CM-3.1 계약)."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    final_runtime_db.exec_driver_sql(f"COMMENT ON TABLE public.{v5.R03_TABLE} IS NULL")

    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=_engine_for(final_runtime_db),
            marker_root=tmp_path / "markers",
            report_root=tmp_path / "reports",
        )

    assert caught.value.reason_code == "MISSING_FINAL_REFERENCE"


# ---------------------------------------------------------------------------
# final data gate 음성 회귀 (구현리뷰 2차 필수 1)
#
# 1차 구현은 `require_final_dataset=False`로 이 검사를 통째로 껐다. 아래 변조가
# **전부 통과했었다.** gate가 실제로 무는지 확인한다.
# ---------------------------------------------------------------------------


def _expect_reference_refusal(connection: Any, tmp_path: Path) -> None:
    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    with pytest.raises(runner.AgentRuntimeStateError) as caught:
        runner.run_apply(
            _target(),
            change_reference="GH-121",
            engine_factory=_engine_for(connection),
            marker_root=marker_root,
            report_root=report_root,
        )
    assert caught.value.reason_code == "MISSING_FINAL_REFERENCE"
    for root in (marker_root, report_root):
        assert not root.exists() or not list(root.iterdir()), "중단은 artifact 0건이다"


def test_the_gate_bites_before_any_tampering(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """대조군 — 정상 형상에서는 통과한다.

    아래 음성 회귀가 "무엇을 해도 실패한다"가 아님을 보인다.
    """

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    status, _ = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
    )
    assert status == "VERIFIED_EXISTING"


@pytest.mark.parametrize(
    ("label", "sql"),
    [
        (
            "TRACE 1건 삭제 — 138/51 분포가 깨진다",
            "DELETE FROM public.trace_alarm_history "
            "WHERE alarm_id = (SELECT min(alarm_id) FROM public.trace_alarm_history)",
        ),
        (
            "SUMMARY 1건 삭제",
            "DELETE FROM public.summary_alarm_history "
            "WHERE alarm_id = (SELECT min(alarm_id) FROM public.summary_alarm_history)",
        ),
    ],
)
def test_branch_distribution_tampering_is_refused(
    final_runtime_db: Any, tmp_path: Path, label: str, sql: str
) -> None:
    """합계만 보면 `TRACE 137 / SUMMARY 52`도 통과한다 — 분포까지 exact다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    final_runtime_db.exec_driver_sql(sql)

    _expect_reference_refusal(final_runtime_db, tmp_path)


def test_a_null_owner_row_is_refused(final_runtime_db: Any, tmp_path: Path) -> None:
    """`h.wafer_id = a.wafer`가 풀리지 않는 행이 하나라도 있으면 거부한다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    final_runtime_db.exec_driver_sql(
        "UPDATE public.trace_alarm_history SET wafer = 'NO_SUCH_WAFER' "
        "WHERE alarm_id = (SELECT min(alarm_id) FROM public.trace_alarm_history)"
    )

    _expect_reference_refusal(final_runtime_db, tmp_path)


def _seed_r03(connection: Any, rows: int) -> int:
    """final data 위에 R03 행을 정확히 `rows`건 넣는다.

    **R03 정답 fixture가 아니다.** 최종 base FK와 R03 table의 물리 CHECK
    (`^R03-[0-9a-f]{20}$` · `member_wafer_refs` 길이 3 · `policy_version`)를 만족하는
    **runner-phase 전용 stub**이다. 연속 OOS 계산과 canonical member payload
    (`{lot_hist_id, wafer_id}` 3개 · TRACE AlarmRef 9개)는 재현하지 않으며
    `V5-A-1.4`가 소유한다 — 여기서 복제하면 담당 경계를 넘는다.

    CM-3.2 runner가 보는 것은 **행 수와 View 분포**뿐이라 이 범위로 충분하다.
    A의 R03 정답 fixture로 재사용하지 말 것.

    CM-3.1 fixture의 `r03_seed.sql`은 쓸 수 없다 — 그 `lot_hist_id`가 최종 dataset에
    없어 FK가 막는다.
    """

    if rows:
        connection.exec_driver_sql(
            f"""
            INSERT INTO public.{v5.R03_TABLE}
                (alarm_id, occurred_at, lot_hist_id, lot_id, equipment_id, chamber_id,
                 parameter_id, recipe_step_no, trigger_wafer_no, member_wafer_refs,
                 member_alarm_refs, policy_version)
            SELECT
                   'R03-' || lpad(to_hex(row_number() OVER ()::int), 20, '0'),
                   h.track_in_at, h.lot_hist_id,
                   h.lot_id, h.equipment_id, h.chamber_id, p.parameter_id,
                   1, 3,
                   '[1, 2, 3]'::jsonb, '[]'::jsonb, 'R03_CONSEC_V1'
            FROM (
                SELECT * FROM public.lot_history ORDER BY lot_hist_id LIMIT {rows}
            ) AS h
            CROSS JOIN (
                SELECT parameter_id FROM public.dim_parameter
                ORDER BY parameter_id LIMIT 1
            ) AS p
            """
        )
    actual = int(
        connection.exec_driver_sql(
            f"SELECT count(*) AS n FROM public.{v5.R03_TABLE}"
        ).all()[0]["n"]
    )
    assert actual == rows, "fixture가 의도한 R03 행 수를 만들지 못했다"
    return actual


def _view_branches(connection: Any) -> dict[str, dict[str, int]]:
    return {
        str(row["source"]): {"n": int(row["n"]), "null_owner": int(row["null_owner"])}
        for row in connection.exec_driver_sql(v5.VIEW_BRANCH_SQL).all()
    }


@pytest.mark.parametrize("rows", [1, 2, 4])
def test_an_r03_row_count_outside_the_allowlist_is_refused(
    final_runtime_db: Any, tmp_path: Path, rows: int
) -> None:
    """R03는 `V5-A-1.4` 전 0, 후 3이다. 1·2·4는 적재가 끊긴 상태다."""

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    _seed_r03(final_runtime_db, rows)

    _expect_reference_refusal(final_runtime_db, tmp_path)


def test_r03_three_rows_and_view_192_is_the_other_accepted_phase(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """**`V5-A-1.4` 적재 후 상태에서도 runner가 성공한다.**

    지금까지 증명한 것은 R03 0·View 189 성공과 1·2·4 거부뿐이었다. 허용 phase가 둘인데
    한쪽만 positive 증적이 있었다(구현리뷰 3차 필수 1). 공용 Gate 0의 대상이 이미
    3건일 수 있으므로, **격리 container에서 먼저** 닫는다.

    실제 데이터로 View 분포까지 확인한다 — 189 + 3 = 192.
    """

    _sql, statements = runner.load_and_validate_sql()
    runner.execute_schema(final_runtime_db, statements)
    _seed_r03(final_runtime_db, 3)

    branches = _view_branches(final_runtime_db)
    assert {name: item["n"] for name, item in branches.items()} == {
        "TRACE": 138,
        "SUMMARY": 51,
        "R03": 3,
    }
    assert sum(item["null_owner"] for item in branches.values()) == 0
    assert sum(item["n"] for item in branches.values()) == 192

    marker_root = tmp_path / "markers"
    report_root = tmp_path / "reports"
    marker_root.mkdir()
    report_root.mkdir()
    final_runtime_db.statements.clear()

    status, result = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=marker_root,
        report_root=report_root,
    )

    assert status == "VERIFIED_EXISTING"
    assert final_runtime_db.writes == [], final_runtime_db.writes

    marker = json.loads(
        runner.marker_path("kosa_agent_e2e", root=marker_root).read_text(
            encoding="utf-8"
        )
    )
    assert marker["status"] == "VERIFIED_EXISTING"
    assert marker["schema_signature_sha256"] == result.schema_signature_sha256

    # 재실행 no-op까지 같은 phase에서 확인한다.
    final_runtime_db.statements.clear()
    again, _ = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=marker_root,
        report_root=report_root,
    )
    assert again == "NO_OP"
    assert final_runtime_db.writes == []


def test_r03_three_rows_also_works_from_absent(
    final_runtime_db: Any, tmp_path: Path
) -> None:
    """R03 3건 phase에서 **fresh apply**도 성립한다."""

    _seed_r03(final_runtime_db, 3)
    assert _runtime_table_count(final_runtime_db) == 0

    status, _ = runner.run_apply(
        _target(),
        change_reference="GH-121",
        engine_factory=_engine_for(final_runtime_db),
        marker_root=tmp_path / "markers",
        report_root=tmp_path / "reports",
    )

    assert status == "APPLIED"
    assert _runtime_table_count(final_runtime_db) == 9
