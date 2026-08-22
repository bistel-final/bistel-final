"""backup orchestrator·session adapter·handler 계약(`V5-CM-2.6`).

구현리뷰 8·9차 필수 3이 지적한 공백을 검증한다. 이 파일은 **공용 DB에 닿지 않는다.**
lifecycle과 runner를 주입해 실행 흐름만 본다. 실물 검증은 container 회귀가 한다.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_orchestrator as orchestrator  # noqa: E402
import postgres_backup as backup  # noqa: E402
import postgres_transition as transition  # noqa: E402
import transition_sessions as sessions  # noqa: E402
from test_postgres_transition import _inventory  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_base_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    def pinned(inventory: Any) -> str:
        wafer = {
            inventory.column_types.get(name, {}).get("wafer", "")
            for name in transition.WAFER_ALTER_TABLES
        }
        return (
            transition.FINAL_BASE_CATALOG_SHA256
            if wafer == {transition.FINAL_WAFER_TYPE}
            else transition.LEGACY_BASE_CATALOG_SHA256
        )

    monkeypatch.setattr(transition, "base_catalog_sha256", pinned)


class _Endpoint:
    database = "fdc_restore_verify"
    password = "irrelevant"
    host = "127.0.0.1"
    port = 55432
    username = "postgres"


@pytest.fixture(autouse=True)
def _trusted_backup_root(tmp_path: Path) -> None:
    """production backup root는 실행 계정 단독 소유의 `0700`이다(계획 §16.1).

    pytest 기본 mode는 그보다 넓어서, 이걸 맞추지 않으면 모든 회귀가
    `BACKUP_ROOT_UNTRUSTED`로 끝난다. 신뢰 조건 자체는 전용 회귀가 확인한다.
    """

    tmp_path.chmod(0o700)


def _source(password: str = "pw") -> Any:
    return orchestrator.SourceEndpoint(
        database="kosa_agent",
        password=password,
        host="db.internal",
        port=5432,
        username="transition_role",
    )


@contextlib.contextmanager
def _lifecycle(**_kwargs: Any) -> Iterator[Any]:
    yield _Endpoint()


def _tool(argv: Sequence[str]) -> str:
    """`docker run ... --entrypoint <tool> <image> ...`에서 도구 이름을 꺼낸다."""

    if "--entrypoint" in argv:
        return argv[argv.index("--entrypoint") + 1]
    return argv[0] if argv else ""


def _host_path(argv: Sequence[str], container: str) -> Path:
    """container 경로를 mount에서 host 경로로 되돌린다."""

    mount = next(arg for i, arg in enumerate(argv) if i and argv[i - 1] == "--volume")
    host_root, container_root = mount.split(":", 1)
    return Path(host_root) / Path(container).relative_to(container_root)


def _listing() -> str:
    return "\n".join(
        f"1{i}; 1259 1{i} TABLE public {name} postgres"
        for i, name in enumerate(sorted(transition.BASE_TABLES))
    )


def _runner(record: list[Sequence[str]], *, returncode: int = 0) -> Any:
    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        record.append(list(argv))
        tool = _tool(argv)
        stdout = ""
        if tool in {"pg_dump", "pg_restore"} and "--version" in argv:
            stdout = f"{tool} (PostgreSQL) 16.15"
        if tool == "pg_dump" and "--file" in argv:
            _host_path(argv, argv[argv.index("--file") + 1]).write_bytes(b"dump-bytes")
        if tool == "pg_restore" and "--list" in argv:
            stdout = _listing()
        failed = (
            tool == "pg_restore" and "--version" not in argv and "--list" not in argv
        )
        return subprocess.CompletedProcess(
            list(argv), returncode if failed else 0, stdout, ""
        )

    return run


def _reader(source: Any) -> Any:
    def read(_endpoint: Any, _database: str, _profile: str) -> Any:
        return source

    return read


# ---------------------------------------------------------------------------
# 구현리뷰 9차 필수 3 — restore_verified 는 관측 결과여야 한다
# ---------------------------------------------------------------------------


def test_receipt_restore_verified_comes_from_a_real_restore(tmp_path: Path) -> None:
    """지금까지 `restore_verified`는 **인자로 받은 값**이었다.

    이제 orchestrator가 실제 dump·restore·대조를 거친 뒤에만 True를 적는다.
    """

    inventory = _inventory("kosa_agent")
    calls: list[Sequence[str]] = []
    receipt = orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner(calls),
        reader=_reader(inventory),
    )
    transition.validate_receipt(receipt)
    assert receipt["restore_verified"] is True
    assert receipt["backup_image_digest"] == backup.expected_client_image(16)

    programs = [_tool(argv) for argv in calls]
    assert "pg_dump" in programs and "pg_restore" in programs
    # 모든 client 실행이 digest 고정 image 안에서 일어난다.
    assert all(argv[0] == "docker" for argv in calls)
    assert all(backup.expected_client_image(16) in argv for argv in calls)
    # 증적 3종이 실물로 남는다.
    assert (tmp_path / transition.archive_name("kosa_agent", "GH-104")).is_file()
    assert (tmp_path / transition.view_sidecar_name("kosa_agent", "GH-104")).is_file()
    assert list(tmp_path.glob("*.receipt.json"))


def test_restore_failure_never_produces_evidence(tmp_path: Path) -> None:
    """복원이 실패하면 sidecar·receipt를 만들지 않는다."""

    inventory = _inventory("kosa_agent")
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([], returncode=1),
            reader=_reader(inventory),
        )
    assert caught.value.reason_code == "RESTORE_NOT_VERIFIED"
    assert not list(tmp_path.glob("*.json"))


def test_restored_content_mismatch_is_not_verified(tmp_path: Path) -> None:
    """복원본이 원본과 다르면 `restore_verified`가 될 수 없다."""

    inventory = _inventory("kosa_agent")
    drifted = _inventory(
        "kosa_agent",
        base_content={**dict(inventory.base_content), "lot_history": "9" * 64},
    )
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(drifted),
        )
    assert caught.value.reason_code == "RESTORE_NOT_VERIFIED"
    assert not list(tmp_path.glob("*.json"))


def test_dump_only_covers_the_base_allowlist(tmp_path: Path) -> None:
    """base 9 밖 table을 덤프하지 않는다."""

    inventory = _inventory("kosa_agent")
    calls: list[Sequence[str]] = []
    orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner(calls),
        reader=_reader(inventory),
    )
    dump = next(argv for argv in calls if _tool(argv) == "pg_dump" and "--file" in argv)
    tables = sorted(
        arg.split("=", 1)[1].removeprefix("public.")
        for arg in dump
        if arg.startswith("--table=")
    )
    assert tables == sorted(transition.BASE_TABLES)


def test_existing_archive_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / transition.archive_name("kosa_agent", "GH-104")).write_bytes(b"old")
    (tmp_path / orchestrator.completion_name("kosa_agent", "GH-104")).write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            _inventory("kosa_agent"),
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(_inventory("kosa_agent")),
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_secrets_never_reach_argv_or_evidence(tmp_path: Path) -> None:
    """password는 child environment로만 간다."""

    inventory = _inventory("kosa_agent")
    calls: list[Sequence[str]] = []
    receipt = orchestrator.backup_and_verify(
        inventory,
        source=_source("s3cret-value"),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner(calls),
        reader=_reader(inventory),
    )
    flat = json.dumps(receipt, ensure_ascii=False)
    assert "s3cret-value" not in flat
    for argv in calls:
        assert "s3cret-value" not in " ".join(argv)
    for path in tmp_path.iterdir():
        if path.suffix == ".json":
            assert "s3cret-value" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# session adapter — 자격증명 없이는 열리지 않는다
# ---------------------------------------------------------------------------


def test_sessions_refuse_to_open_without_credentials() -> None:
    for factory in (sessions.read_only_session, sessions.mutating_session):
        with pytest.raises(sessions.SessionError) as caught:
            with factory("kosa_agent", environ={}):
                pass
        assert caught.value.reason_code == "APPROVAL_REQUIRED"
        assert caught.value.exit_code == sessions.EXIT_CONFIRM_REQUIRED


def _published(root: Path) -> list[str]:
    """게시된 증적만 센다.

    lock 파일은 해제해도 남는다 — 지우면 `read → unlink` 사이에 다른 실행이 새 lock을
    만들어 남의 lock을 지우게 된다(구현리뷰 16차 필수 2). 남은 파일은 소유권 주장이
    없는 `RELEASED` 상태이며 증적이 아니다.
    """

    return sorted(
        path.name
        for path in root.iterdir()
        if not path.name.endswith(orchestrator.LOCK_SUFFIX)
    )


def _lock_is_free(root: Path, database: str, change_ref: str) -> bool:
    status = orchestrator.read_lock(root, database, change_ref)
    return status["state"] == "absent" and status["live"] is False


def test_sessions_refuse_targets_outside_the_allowlist() -> None:
    env = dict.fromkeys(sessions.DSN_ENV_KEYS, "x")
    env["POSTGRES_TRANSITION_PORT"] = "5432"
    env[transition.ALLOWED_ENDPOINT_ENV_KEY] = transition.endpoint_fingerprint(
        "x", 5432
    )
    with pytest.raises(sessions.SessionError) as caught:
        with sessions.mutating_session("kosa", environ=env):
            pass
    assert caught.value.reason_code == "TARGET_NOT_ALLOWED"


def test_public_sessions_registry_is_opt_in() -> None:
    """`build_public_sessions()`를 부르지 않으면 registry는 비어 있다."""

    import transition_public_postgres as cli

    assert cli.PUBLIC_SESSIONS == {}
    built = sessions.build_public_sessions(environ={})
    assert set(built) == {"read_only", "mutating"}


# ---------------------------------------------------------------------------
# handler — schema/View만 바꾼다
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self, definition: str) -> None:
        self.definition = definition
        self.log: list[str] = []

    def _acl_rows(self) -> Any:
        return []

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        self.log.append(sql)
        acl_rows = self._acl_rows() if "pg_get_userbyid" in sql else []

        class _Result:
            def scalar(_self) -> Any:  # noqa: N805
                return self.definition if "pg_get_viewdef" in sql else None

            def mappings(_self) -> Any:  # noqa: N805
                return _self

            def all(_self) -> Any:  # noqa: N805
                return acl_rows

        return _Result()


def _rendered(statement: Any) -> str:
    """`psycopg.sql` 조각을 사람이 읽는 문자열로 편다."""

    text = " ".join(str(statement).split())
    if "SQL(" in text:
        import re

        parts = re.findall(r"SQL\('([^']*)'\)|Identifier\(([^)]*)\)", text)
        text = " ".join(
            (a or b.replace("'", "").replace(", ", ".")) for a, b in parts
        ).strip()
    return " ".join(text.split())


class _Transactional(_Recorder):
    """COPY와 ACL 확인까지 받는 fake. psycopg driver 흉내만 낸다."""

    def _acl_rows(self) -> Any:
        import postgres_transition as t

        return [
            {
                "owner": t.LEGACY_VIEW_OWNER,
                "acl": t.LEGACY_VIEW_ACL,
                "comment": t.LEGACY_VIEW_COMMENT,
            }
        ]

    @property
    def connection(self) -> Any:
        return self

    def cursor(self) -> Any:
        recorder = self

        class _Cursor:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_exc: Any) -> None:
                return None

            def execute(self, statement: Any) -> None:
                recorder.log.append(_rendered(statement))

            def copy(self, statement: Any) -> Any:
                recorder.log.append(_rendered(statement))

                class _Copy:
                    def __enter__(self) -> Any:
                        return self

                    def __exit__(self, *_exc: Any) -> None:
                        return None

                    def write(self, _payload: bytes) -> None:
                        return None

                return _Copy()

        return _Cursor()


LEGACY_BODY = (
    "SELECT a.wafer AS wafer_no FROM a JOIN h ON h.wafer_no = a.wafer "
    "WHERE a.wafer AS wafer_no IS NOT NULL AND h.wafer_no = a.wafer"
)


def test_handler_refuses_a_view_that_is_not_the_pinned_legacy_definition() -> None:
    """복구할 수 없는 View를 바꾸지 않는다."""

    connection = _Recorder(LEGACY_BODY)
    with pytest.raises(sessions.SessionError) as caught:
        sessions.transition_handler(connection, "kosa_agent", _inventory("kosa_agent"))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"
    assert not [s for s in connection.log if s.startswith(("DROP", "ALTER", "CREATE"))]


def test_handler_emits_only_schema_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """데이터 교체는 이 handler가 하지 않는다(계획 §8)."""

    monkeypatch.setattr(
        transition, "LEGACY_VIEW_SHA256", transition.view_fingerprint(LEGACY_BODY)
    )
    connection = _Recorder(LEGACY_BODY)
    sessions.transition_handler(connection, "kosa_agent", _inventory("kosa_agent"))
    emitted = [s for s in connection.log if not s.startswith("SELECT")]
    assert emitted[0] == transition.DROP_VIEW_SQL
    assert emitted[-1].startswith(f"CREATE VIEW public.{transition.LEGACY_VIEW}")
    assert not [
        s
        for s in emitted
        if s.split()[0] in {"INSERT", "UPDATE", "DELETE", "COPY", "TRUNCATE"}
    ]


def test_handler_refuses_a_mismatched_target() -> None:
    connection = _Recorder(LEGACY_BODY)
    with pytest.raises(sessions.SessionError) as caught:
        sessions.transition_handler(
            connection, "kosa_text2sql", _inventory("kosa_agent")
        )
    assert caught.value.reason_code == "TARGET_NOT_ALLOWED"


def test_restored_catalog_mismatch_is_not_verified(tmp_path: Path) -> None:
    """행 내용이 같아도 **schema**가 다르면 복원 검증이 아니다.

    catalog 대조를 빼면 wafer type이 다른 복원본도 통과한다.
    """

    inventory = _inventory("kosa_agent")
    # 내용·행 수는 같고 wafer type만 final인 복원본.
    drifted = _inventory(
        "kosa_agent",
        wafer_type=transition.FINAL_WAFER_TYPE,
        base_content=dict(inventory.base_content),
        alarms={
            name: inventory.row_counts[name]
            for name in ("trace_alarm_history", "summary_alarm_history")
        },
        action_rows=inventory.row_counts["action_history"],
    )
    assert dict(drifted.base_content) == dict(inventory.base_content)

    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(drifted),
        )
    assert caught.value.reason_code == "RESTORE_NOT_VERIFIED"
    assert not list(tmp_path.glob("*.json"))


def test_observed_client_version_must_match_the_pin(tmp_path: Path) -> None:
    """image가 digest로 고정됐으니 관측 version도 고정이다.

    `verify_client_major()`는 major만 본다. patch가 달라도 통과한다.
    """

    def runner(argv: Sequence[str], **kwargs: Any) -> Any:
        tool = _tool(argv)
        stdout = ""
        if "--version" in argv:
            stdout = f"{tool} (PostgreSQL) 16.10"
        if tool == "pg_dump" and "--file" in argv:
            _host_path(argv, argv[argv.index("--file") + 1]).write_bytes(b"x")
        if tool == "pg_restore" and "--list" in argv:
            stdout = _listing()
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    inventory = _inventory("kosa_agent")
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=runner,
            reader=_reader(inventory),
        )
    assert caught.value.reason_code == "BACKUP_CLIENT_VERSION_MISMATCH"
    assert not list(tmp_path.glob("*.json"))


def test_read_only_session_asks_the_server_to_refuse_writes() -> None:
    """`default_transaction_read_only=on`이 빠지면 서버가 실수 쓰기를 막지 않는다."""

    captured: dict[str, Any] = {}

    def fake_create_engine(url: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        captured["database"] = url.database
        raise RuntimeError("stop-before-connect")

    import sqlalchemy

    original = sqlalchemy.create_engine
    sqlalchemy.create_engine = fake_create_engine  # type: ignore[assignment]
    env = dict.fromkeys(sessions.DSN_ENV_KEYS, "x")
    env["POSTGRES_TRANSITION_PORT"] = "5432"
    env[transition.ALLOWED_ENDPOINT_ENV_KEY] = transition.endpoint_fingerprint(
        "x", 5432
    )
    try:
        for factory, expected in (
            (sessions.read_only_session, sessions.READ_ONLY_OPTIONS),
            (sessions.mutating_session, None),
        ):
            captured.clear()
            with pytest.raises(RuntimeError):
                with factory("kosa_agent", environ=env):
                    pass
            assert captured["database"] == "kosa_agent"
            assert captured["isolation_level"] == "REPEATABLE READ"
            assert captured["connect_args"].get("options") == expected
    finally:
        sqlalchemy.create_engine = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 구현리뷰 10차 필수 2 — 실패는 재시도를 막지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["restore", "content"])
def test_failure_leaves_no_archive_and_allows_an_immediate_retry(
    tmp_path: Path, stage: str
) -> None:
    """restore가 실패하면 dump도 남지 않아야 같은 change-ref로 다시 시도할 수 있다.

    이전 구현은 dump를 final 이름으로 먼저 만들어, 실패 뒤 재시도가
    `BACKUP_INVALID`로 봉쇄됐다(구현리뷰 10차 필수 2).
    """

    inventory = _inventory("kosa_agent")
    drifted = _inventory(
        "kosa_agent",
        base_content={**dict(inventory.base_content), "lot_history": "9" * 64},
    )
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([], returncode=1 if stage == "restore" else 0),
            reader=_reader(inventory if stage == "restore" else drifted),
        )
    assert _published(tmp_path) == [], "실패 뒤 잔여 artifact가 있다"

    # 즉시 재시도가 성공한다.
    receipt = orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner([]),
        reader=_reader(inventory),
    )
    assert receipt["restore_verified"] is True
    assert (tmp_path / transition.archive_name("kosa_agent", "GH-104")).is_file()
    assert not list(tmp_path.glob(f"*{orchestrator.PARTIAL_SUFFIX}"))


def test_a_healthy_archive_is_still_never_overwritten(tmp_path: Path) -> None:
    """정상 archive 보호 계약은 그대로다. 근거는 completion marker다."""

    (tmp_path / transition.archive_name("kosa_agent", "GH-104")).write_bytes(b"old")
    (tmp_path / orchestrator.completion_name("kosa_agent", "GH-104")).write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            _inventory("kosa_agent"),
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(_inventory("kosa_agent")),
        )
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert (
        tmp_path / transition.archive_name("kosa_agent", "GH-104")
    ).read_bytes() == b"old"


# ---------------------------------------------------------------------------
# 구현리뷰 10차 필수 1 — 고정 image·지정 endpoint·clean env
# ---------------------------------------------------------------------------


def test_child_environment_does_not_inherit_pg_variables() -> None:
    """상위 env의 `PGHOST`를 물려받으면 의도한 서버가 아닌 곳에 붙는다."""

    polluted = {
        "PGHOST": "public.example",
        "PGPORT": "9999",
        "PGUSER": "someone",
        "PGDATABASE": "kosa",
        "PGSSLMODE": "disable",
        "PATH": "/usr/bin",
    }
    child = backup.child_environment(
        "pw",
        host="db.internal",
        port=5432,
        user="role",
        database="kosa_agent",
        base=polluted,
    )
    assert child["PGHOST"] == "db.internal"
    assert child["PGPORT"] == "5432"
    assert child["PGUSER"] == "role"
    assert child["PGDATABASE"] == "kosa_agent"
    assert child["PGPASSWORD"] == "pw"
    # 목록 밖 `PG*`는 아예 전달하지 않는다.
    assert "PGSSLMODE" not in child
    assert child["PATH"] == "/usr/bin"


def test_pinned_client_argv_runs_inside_the_digest_image() -> None:
    """host binary를 쓰면 receipt의 image digest가 쓰이지도 않은 값이 된다."""

    image = backup.expected_client_image(16)
    argv = backup.pinned_client_argv(
        ("pg_dump", "--dbname", "kosa_agent"),
        image=image,
        child_env={"PGPASSWORD": "pw", "PGHOST": "db.internal"},
        mounts={"/backups": "/backups"},
    )
    assert argv[0] == "docker"
    assert image in argv
    assert argv[argv.index("--entrypoint") + 1] == "pg_dump"
    assert "--volume" in argv and "/backups:/backups" in argv
    # secret 값은 argv에 없다. 이름만 전달한다.
    assert "pw" not in " ".join(argv)
    assert "PGPASSWORD" in argv


def test_pinned_client_argv_refuses_an_unpinned_image() -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.pinned_client_argv(
            ("pg_dump",),
            image="postgres:16",
            child_env={},
            mounts={},
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_restore_uses_the_lifecycle_endpoint_address(tmp_path: Path) -> None:
    """격리 lifecycle이 준 host·port·user를 버리면 상위 env가 대상을 정한다."""

    inventory = _inventory("kosa_agent")
    seen: list[Any] = []

    def runner(argv: Sequence[str], **kwargs: Any) -> Any:
        tool = _tool(argv)
        seen.append((tool, dict(kwargs.get("env") or {})))
        stdout = f"{tool} (PostgreSQL) 16.15" if "--version" in argv else ""
        if tool == "pg_dump" and "--file" in argv:
            _host_path(argv, argv[argv.index("--file") + 1]).write_bytes(b"x")
        if tool == "pg_restore" and "--list" in argv:
            stdout = _listing()
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=runner,
        reader=_reader(inventory),
    )
    # version 확인도 pg_restore를 부르므로, 격리 DB를 향한 호출만 고른다.
    restore_env = next(
        env
        for tool, env in seen
        if tool == "pg_restore" and env.get("PGDATABASE") == _Endpoint.database
    )
    import sys as _sys

    assert restore_env["PGHOST"] == backup.rewrite_host(_Endpoint.host, _sys.platform)
    assert restore_env["PGPORT"] == str(_Endpoint.port)
    assert restore_env["PGUSER"] == _Endpoint.username
    assert restore_env["PGDATABASE"] == _Endpoint.database

    dump_env = next(
        env
        for tool, env in seen
        if tool == "pg_dump" and env.get("PGDATABASE") == "kosa_agent"
    )
    assert dump_env["PGHOST"] == "db.internal"
    assert dump_env["PGUSER"] == "transition_role"


@pytest.mark.parametrize("tool", ["pg_dump", "pg_restore"])
def test_both_client_versions_must_be_exact(tmp_path: Path, tool: str) -> None:
    """major만 보면 patch가 다른 client도 통과한다."""

    def runner(argv: Sequence[str], **kwargs: Any) -> Any:
        observed = _tool(argv)
        stdout = ""
        if "--version" in argv:
            patch = "16.10" if observed == tool else "16.15"
            stdout = f"{observed} (PostgreSQL) {patch}"
        if observed == "pg_dump" and "--file" in argv:
            _host_path(argv, argv[argv.index("--file") + 1]).write_bytes(b"x")
        if observed == "pg_restore" and "--list" in argv:
            stdout = _listing()
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            _inventory("kosa_agent"),
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=runner,
            reader=_reader(_inventory("kosa_agent")),
        )
    assert caught.value.reason_code == "BACKUP_CLIENT_VERSION_MISMATCH"
    assert _published(tmp_path) == []


# ---------------------------------------------------------------------------
# 구현리뷰 10차 필수 3·4 — 데이터 교체와 진입점
# ---------------------------------------------------------------------------


def _run_handler(tables: Sequence[str], profile: str) -> Any:
    import postgres_transition as t
    import transition_sessions as ts

    handler = ts.make_transition_handler(
        csv_bodies=dict.fromkeys(tables, b"a\n1\n"),
        columns_by_table=dict.fromkeys(tables, ("a",)),
        tables=tables,
        profile=profile,
    )
    connection = _Transactional(LEGACY_BODY)
    original = t.LEGACY_VIEW_SHA256
    t.LEGACY_VIEW_SHA256 = t.view_fingerprint(LEGACY_BODY)
    try:
        handler(connection, "kosa_agent", _inventory("kosa_agent"))
    finally:
        t.LEGACY_VIEW_SHA256 = original
    return connection


def test_transition_handler_follows_the_planned_statement_order() -> None:
    """계획 §8은 `DROP → DELETE → ALTER → COPY → CREATE`를 고정했다.

    채워진 table을 먼저 ALTER하면 곧 지울 행을 rewrite하며 WAL과 `ACCESS EXCLUSIVE`
    보유 시간을 늘린다(구현리뷰 11차 필수 2). 순서를 **정확히** 고정한다.
    """

    tables = ("lot_history", "fdc_trace")
    connection = _run_handler(tables, "runtime")
    kinds = [
        s.split()[0]
        for s in connection.log
        if s.split()[0]
        in {"DROP", "DELETE", "ALTER", "COPY", "CREATE", "GRANT", "REVOKE"}
    ]
    assert kinds == [
        "DROP",
        *["DELETE"] * len(tables),
        *["ALTER"] * 4,
        *["COPY"] * len(tables),
        "CREATE",
        # View를 DROP하면 ACL이 사라진다. 계획 §8.1대로 명시 복원한다.
        "REVOKE",
        "ALTER",
        "GRANT",
    ]


def test_alter_only_runs_after_the_tables_are_empty() -> None:
    """ALTER 직전에 행이 0인지 확인한다. 비어 있지 않으면 멈춘다."""

    import postgres_transition as t
    import transition_sessions as ts

    class _Populated(_Transactional):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            result = super().execute(statement, parameters)
            if "count(*)" in " ".join(str(statement).split()):

                class _Rows:
                    def scalar(self) -> int:
                        return 14_400

                return _Rows()
            return result

    tables = ("lot_history",)
    handler = ts.make_transition_handler(
        csv_bodies=dict.fromkeys(tables, b"a\n1\n"),
        columns_by_table=dict.fromkeys(tables, ("a",)),
        tables=tables,
        profile="runtime",
    )
    connection = _Populated(LEGACY_BODY)
    original = t.LEGACY_VIEW_SHA256
    t.LEGACY_VIEW_SHA256 = t.view_fingerprint(LEGACY_BODY)
    try:
        with pytest.raises(ts.SessionError) as caught:
            handler(connection, "kosa_agent", _inventory("kosa_agent"))
    finally:
        t.LEGACY_VIEW_SHA256 = original
    assert caught.value.reason_code == "MODE_CONTRACT_ERROR"
    assert not [s for s in connection.log if s.startswith("ALTER")]


def test_delete_uses_the_reverse_load_order() -> None:
    """FK child를 먼저 지운다."""

    import rehearsal_profile_loader as pl

    tables = tuple(pl.LOAD_ORDER[:3])
    connection = _run_handler(tables, "runtime")
    deleted = [
        s.split("public.")[1].split()[0]
        for s in connection.log
        if s.startswith("DELETE")
    ]
    assert deleted == list(reversed(tables))


def test_entrypoint_reports_missing_credentials_not_internal_error() -> None:
    """자격증명 누락이 `INTERNAL_ERROR`로 뭉개지면 원인을 알 수 없다."""

    import transition_public_postgres as cli

    assert cli.main(["--preflight"], builder=lambda: {}) == cli.EXIT_USAGE
    import transition_sessions as ts

    assert (
        cli.main(
            ["--preflight"],
            builder=lambda: ts.build_public_sessions(environ={}),
        )
        == 3
    )


def test_backup_entrypoint_validates_before_touching_anything(tmp_path: Path) -> None:
    """confirm·root·bundle 검증을 마치기 전에는 연결하지 않는다."""

    base = [
        "--backup-root",
        str(tmp_path),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        "0" * 64,
    ]
    assert orchestrator.main(base, environ={}) == orchestrator.EXIT_CONFIRM_REQUIRED

    confirmed = base + [
        arg for t in transition.ORDERED_TARGETS for arg in ("--confirm-target", t)
    ]
    # confirm은 맞지만 자격증명이 없다.
    assert orchestrator.main(confirmed, environ={}) == (
        orchestrator.EXIT_CONFIRM_REQUIRED
    )
    assert _published(tmp_path) == []


def test_source_endpoint_comes_only_from_the_environment() -> None:
    env = {
        "POSTGRES_TRANSITION_HOST": "db.internal",
        "POSTGRES_TRANSITION_PORT": "5432",
        "POSTGRES_TRANSITION_USER": "role",
        "POSTGRES_TRANSITION_PASSWORD": "pw",
        "POSTGRES_TRANSITION_ALLOWED_HOST_SHA256": transition.endpoint_fingerprint(
            "db.internal", 5432
        ),
    }
    endpoint = orchestrator.source_from_environment("kosa_agent", env)
    assert endpoint.host == "db.internal"
    assert endpoint.port == 5432
    assert endpoint.username == "role"

    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.source_from_environment("kosa", env)
    assert caught.value.reason_code == "TARGET_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# 구현리뷰 11차 필수 3·4 · 권장 1 — 원자 게시, 플랫폼 transport, 입력 정규화
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["classify", "sidecar", "receipt"])
def test_a_late_failure_leaves_the_backup_root_empty(
    tmp_path: Path, stage: str
) -> None:
    """archive만 먼저 승격하면 후반 실패가 같은 change-ref 재시도를 봉쇄한다.

    이전 구현은 restore 검증 직후 승격하고 sidecar·receipt를 그 뒤에 만들었다
    (구현리뷰 11차 필수 3).
    """

    inventory = _inventory("kosa_agent")
    attribute = {
        "classify": "classify_target",
        "sidecar": "build_sidecar",
        "receipt": "build_receipt",
    }[stage]
    original = getattr(transition, attribute)

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("late failure")

    setattr(transition, attribute, boom)
    try:
        with pytest.raises(RuntimeError):
            orchestrator.backup_and_verify(
                inventory,
                source=_source(),
                change_ref="GH-104",
                preflight_bundle_sha256="0" * 64,
                backup_root=tmp_path,
                lifecycle=_lifecycle,
                runner=_runner([]),
                reader=_reader(inventory),
            )
    finally:
        setattr(transition, attribute, original)
    assert _published(tmp_path) == [], "후반 실패 뒤 잔여 artifact가 있다"

    # 즉시 재시도가 성공한다.
    receipt = orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner([]),
        reader=_reader(inventory),
    )
    assert receipt["restore_verified"] is True
    assert _published(tmp_path) == sorted(
        [
            transition.archive_name("kosa_agent", "GH-104"),
            transition.view_sidecar_name("kosa_agent", "GH-104"),
            orchestrator.receipt_name("kosa_agent", "GH-104"),
            orchestrator.completion_name("kosa_agent", "GH-104"),
        ]
    )


def test_concurrent_runs_do_not_delete_each_others_work(tmp_path: Path) -> None:
    """staging은 execution별이라 다른 실행의 작업 파일을 지우지 않는다."""

    ids = {orchestrator._execution_id() for _ in range(50)}
    assert len(ids) == 50


def test_archive_object_list_must_be_exactly_the_base_allowlist(
    tmp_path: Path,
) -> None:
    """dump argv가 옳아도 archive에 무엇이 들었는지는 따로 봐야 한다(계획 §7.2)."""

    def runner(argv: Sequence[str], **kwargs: Any) -> Any:
        tool = _tool(argv)
        stdout = ""
        if "--version" in argv:
            stdout = f"{tool} (PostgreSQL) 16.15"
        if tool == "pg_dump" and "--file" in argv:
            _host_path(argv, argv[argv.index("--file") + 1]).write_bytes(b"x")
        if tool == "pg_restore" and "--list" in argv:
            stdout = _listing() + "\n99; 1259 99 TABLE public nl_query_log postgres"
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.backup_and_verify(
            _inventory("kosa_agent"),
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=runner,
            reader=_reader(_inventory("kosa_agent")),
        )
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert _published(tmp_path) == []


def test_archive_table_names_parses_a_restore_listing() -> None:
    listing = (
        ";\n; Archive created at 2026-08-22\n;\n"
        "205; 1259 16456 TABLE public lot_history kosa\n"
        "206; 1259 16460 TABLE public fdc_trace kosa\n"
        "3421; 0 16456 TABLE DATA public lot_history kosa\n"
    )
    assert orchestrator.archive_table_names(listing) == {"lot_history", "fdc_trace"}


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux", ("--network=host",)),
        ("darwin", ("--add-host", "host.docker.internal:host-gateway")),
        ("win32", ("--add-host", "host.docker.internal:host-gateway")),
    ],
)
def test_host_transport_is_chosen_per_platform(
    platform: str, expected: tuple[str, ...]
) -> None:
    """`--network=host`는 Linux 계약이다. Desktop에서는 gateway alias를 쓴다."""

    assert backup.host_transport(platform) == expected


@pytest.mark.parametrize(
    ("platform", "host", "expected"),
    [
        ("linux", "127.0.0.1", "127.0.0.1"),
        ("darwin", "127.0.0.1", "host.docker.internal"),
        ("darwin", "localhost", "host.docker.internal"),
        ("win32", "::1", "host.docker.internal"),
        ("darwin", "db.internal", "db.internal"),
    ],
)
def test_loopback_is_rewritten_only_on_desktop(
    platform: str, host: str, expected: str
) -> None:
    """Desktop container 안의 `127.0.0.1`은 host가 아니라 그 container다."""

    assert backup.rewrite_host(host, platform) == expected


def test_container_archive_path_is_a_fixed_posix_path() -> None:
    """host 절대경로를 container 경로로 쓰면 Windows에서 성립하지 않는다."""

    path = orchestrator.container_path("epoch.db.GH-104.dump")
    assert path == f"{orchestrator.CONTAINER_BACKUP_DIR}/epoch.db.GH-104.dump"
    assert path.startswith("/")
    assert "\\" not in path


#: 두 진입점이 **같은** 판정을 내야 하는 값들. `int()`만 쓰면 `" 5432"`·`"+5432"`·
#: `"5_432"`가 한쪽에서만 통과한다(구현리뷰 17차 필수 3).
_INVALID_PORTS = [
    "not-a-port",
    "0",
    "65536",
    "-1",
    "5432.5",
    "+5432",
    "5_432",
    "0x1538",
    # 전각 숫자. `int()`는 받지만 PostgreSQL port로 쓸 값이 아니다.
    "５４３２",
]


@pytest.mark.windows_contract
@pytest.mark.parametrize("raw", _INVALID_PORTS)
def test_invalid_port_is_the_same_typed_reason_in_both_entrypoints(raw: str) -> None:
    """`int()`를 그대로 쓰면 JSON reason도 exit code도 없이 죽는다.

    16차 보완 기준은 형식 오류를 `ENDPOINT_NOT_ALLOWED`·exit 2로 고정했는데, transition
    session만 raw `int()`라 `INTERNAL_ERROR`가 됐다(구현리뷰 17차 필수 3). 두 진입점이
    같은 parser를 쓰는지 **같은 matrix로** 확인한다.
    """

    import sqlalchemy

    env = _endpoint_env(
        "db.internal", raw, transition.endpoint_fingerprint("db.internal", 5432)
    )

    opened: list[Any] = []
    original = sqlalchemy.create_engine

    def spy(*args: Any, **kwargs: Any) -> Any:
        opened.append(args)
        raise AssertionError("engine을 만들면 안 된다")

    sqlalchemy.create_engine = spy  # type: ignore[assignment]
    try:
        with pytest.raises(orchestrator.OrchestrationError) as backup_error:
            orchestrator.source_from_environment("kosa_agent", env)
        assert backup_error.value.reason_code == "ENDPOINT_NOT_ALLOWED"
        assert backup_error.value.exit_code == orchestrator.EXIT_USAGE

        with pytest.raises(sessions.SessionError) as session_error:
            with sessions.read_only_session("kosa_agent", environ=env):
                pass
        assert session_error.value.reason_code == "ENDPOINT_NOT_ALLOWED"
        assert session_error.value.exit_code == sessions.EXIT_USAGE
    finally:
        sqlalchemy.create_engine = original  # type: ignore[assignment]
    assert opened == [], "거부인데 engine을 만들었다"


#: 양쪽 모두 값을 `strip()`한 뒤 보므로 주변 공백은 같은 port다.
@pytest.mark.windows_contract
@pytest.mark.parametrize("raw", ["1", "5432", "65535", " 5432 "])
def test_valid_ports_are_accepted_by_the_shared_parser(raw: str) -> None:
    assert transition.parse_port(raw) == int(raw.strip())
    assert transition.port_rejection(raw) is None


@pytest.mark.windows_contract
def test_an_empty_port_is_a_missing_value_in_both_entrypoints() -> None:
    """빈 값은 형식 오류가 아니라 **미설정**이다. 운영자 조치가 다르다."""

    env = _endpoint_env(
        "db.internal", "", transition.endpoint_fingerprint("db.internal", 5432)
    )
    with pytest.raises(orchestrator.OrchestrationError) as backup_error:
        orchestrator.source_from_environment("kosa_agent", env)
    assert backup_error.value.reason_code == "APPROVAL_REQUIRED"
    with pytest.raises(sessions.SessionError) as session_error:
        with sessions.read_only_session("kosa_agent", environ=env):
            pass
    assert session_error.value.reason_code == "APPROVAL_REQUIRED"


def test_backup_cli_reports_invalid_port_as_json(tmp_path: Path) -> None:
    argv = [
        "--backup-root",
        str(tmp_path),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        "0" * 64,
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]
    env = {
        "POSTGRES_TRANSITION_HOST": "db.internal",
        "POSTGRES_TRANSITION_PORT": "not-a-port",
        "POSTGRES_TRANSITION_USER": "role",
        "POSTGRES_TRANSITION_PASSWORD": "pw",
        "POSTGRES_TRANSITION_ALLOWED_HOST_SHA256": transition.endpoint_fingerprint(
            "db.internal", 5432
        ),
    }
    assert orchestrator.main(argv, environ=env) == orchestrator.EXIT_USAGE


# ---------------------------------------------------------------------------
# 구현리뷰 11차 필수 1 — production 배선
# ---------------------------------------------------------------------------


def test_backup_cli_runs_all_three_targets_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI가 실제로 backup·restore·증적 생성을 수행한다.

    이전에는 인자만 확인하고 무조건 `MODE_NOT_WIRED`였다(구현리뷰 11차 필수 1).
    """

    import transition_public_postgres as cli

    inventories = {d: _inventory(d) for d in transition.ORDERED_TARGETS}

    @contextlib.contextmanager
    def inspector(database: str) -> Iterator[Any]:
        yield object()

    monkeypatch.setattr(
        transition,
        "read_inventory",
        lambda _c, *, database, **_k: inventories[database],
    )
    bundle = cli.preflight_report(inventories)["bundle_sha256"]

    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    argv = [
        "--backup-root",
        str(root),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        bundle,
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]
    env = {
        "POSTGRES_TRANSITION_HOST": "db.internal",
        "POSTGRES_TRANSITION_PORT": "5432",
        "POSTGRES_TRANSITION_USER": "role",
        "POSTGRES_TRANSITION_PASSWORD": "pw",
        "POSTGRES_TRANSITION_ALLOWED_HOST_SHA256": transition.endpoint_fingerprint(
            "db.internal", 5432
        ),
    }

    # reader는 복원본을 읽는다. 원본과 같은 형상을 돌려주면 검증을 통과한다.
    order: list[str] = []

    def reader(_endpoint: Any, _database: str, profile: str) -> Any:
        database = transition.ORDERED_TARGETS[len(order)]
        order.append(database)
        return inventories[database]

    calls: list[Sequence[str]] = []
    code = orchestrator.main(
        argv,
        environ=env,
        inspector=inspector,
        reader=reader,
        lifecycle=_lifecycle,
        runner=_runner(calls),
    )
    assert order == list(transition.ORDERED_TARGETS)
    assert code == 0
    for database in transition.ORDERED_TARGETS:
        assert (root / transition.archive_name(database, "GH-104")).is_file()
        assert (root / transition.view_sidecar_name(database, "GH-104")).is_file()
        assert (root / orchestrator.receipt_name(database, "GH-104")).is_file()
        assert (root / orchestrator.completion_name(database, "GH-104")).is_file()
    assert not list(root.glob(".staging*"))


def test_backup_cli_rejects_a_bundle_that_does_not_match_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """approval의 bundle과 지금 상태가 다르면 dump하지 않는다."""

    inventories = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    monkeypatch.setattr(
        transition,
        "read_inventory",
        lambda _c, *, database, **_k: inventories[database],
    )

    @contextlib.contextmanager
    def inspector(database: str) -> Iterator[Any]:
        yield object()

    argv = [
        "--backup-root",
        str(tmp_path),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        "9" * 64,
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]
    env = {
        "POSTGRES_TRANSITION_HOST": "db.internal",
        "POSTGRES_TRANSITION_PORT": "5432",
        "POSTGRES_TRANSITION_USER": "role",
        "POSTGRES_TRANSITION_PASSWORD": "pw",
        "POSTGRES_TRANSITION_ALLOWED_HOST_SHA256": transition.endpoint_fingerprint(
            "db.internal", 5432
        ),
    }
    assert (
        orchestrator.main(
            argv,
            environ=env,
            inspector=inspector,
            reader=lambda *a: inventories["kosa_agent"],
            lifecycle=_lifecycle,
            runner=_runner([]),
        )
        == orchestrator.EXIT_MISMATCH
    )
    assert _published(tmp_path) == []


def test_dispatching_handler_binds_profile_to_target() -> None:
    """runtime CSV를 evaluation target에 붙이면 mutation 전에 막는다."""

    import transition_sessions as ts

    snapshot = ts.ProfileSnapshot(
        profile="runtime",
        tables=("lot_history",),
        csv_bodies={"lot_history": b"a\n1\n"},
        columns_by_table={"lot_history": ("a",)},
    )
    dispatch = ts.make_dispatching_handler({"runtime": snapshot})
    with pytest.raises(ts.SessionError) as caught:
        dispatch(object(), "kosa_text2sql", _inventory("kosa_text2sql"))
    assert caught.value.reason_code == "MODE_NOT_WIRED"

    with pytest.raises(ts.SessionError) as caught:
        dispatch(object(), "kosa_agent", _inventory("kosa_text2sql"))
    assert caught.value.reason_code == "PROFILE_MISMATCH"


def test_default_builder_wires_handler_only_with_an_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """최종 ZIP이 없으면 preflight는 되고 apply는 `MODE_NOT_WIRED`다."""

    import transition_public_postgres as cli
    import transition_sessions as ts

    monkeypatch.delenv(cli.ARCHIVE_ENV_KEY, raising=False)
    assert "handler" not in ts.build_public_wiring(None, environ={})


def test_publish_refuses_an_incomplete_evidence_set(tmp_path: Path) -> None:
    """세 파일이 다 있을 때만 게시한다.

    확인 없이 rename하면 일부만 있는 상태가 정상 증적으로 남는다
    (구현리뷰 11차 필수 3).
    """

    staging = tmp_path / "staging"
    staging.mkdir()
    targets = [
        tmp_path / transition.archive_name("kosa_agent", "GH-104"),
        tmp_path / transition.view_sidecar_name("kosa_agent", "GH-104"),
        tmp_path / orchestrator.receipt_name("kosa_agent", "GH-104"),
    ]
    # archive만 준비한다.
    (staging / targets[0].name).write_bytes(b"x")
    complete = tmp_path / orchestrator.completion_name("kosa_agent", "GH-104")
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator._publish(
            staging, targets, complete, database="kosa_agent", change_ref="GH-104"
        )
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert not [p for p in tmp_path.iterdir() if p.is_file()]

    # 셋을 모두 채우면 게시되고 marker가 마지막에 생긴다.
    for target in targets[1:]:
        (staging / target.name).write_bytes(b"y")
    orchestrator._publish(
        staging, targets, complete, database="kosa_agent", change_ref="GH-104"
    )
    assert all(target.is_file() for target in targets)
    assert complete.is_file()


def test_staging_is_owned_by_one_execution(tmp_path: Path) -> None:
    """같은 target·change-ref를 동시에 돌려도 서로의 작업 파일을 지우지 않는다.

    staging 이름이 execution별이 아니면 한쪽 실패가 다른 쪽 dump를 지운다
    (구현리뷰 11차 필수 3).
    """

    inventory = _inventory("kosa_agent")
    seen: list[str] = []
    real_mkdir = Path.mkdir

    def record(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name.startswith(".staging."):
            seen.append(self.name)
        return real_mkdir(self, *args, **kwargs)

    Path.mkdir = record  # type: ignore[method-assign]
    try:
        for _ in range(2):
            root = tmp_path / f"root{len(seen)}"
            root.mkdir(mode=0o700)
            orchestrator.backup_and_verify(
                inventory,
                source=_source(),
                change_ref="GH-104",
                preflight_bundle_sha256="0" * 64,
                backup_root=root,
                lifecycle=_lifecycle,
                runner=_runner([]),
                reader=_reader(inventory),
            )
    finally:
        Path.mkdir = real_mkdir  # type: ignore[method-assign]

    assert len(seen) == 2
    assert seen[0] != seen[1], "staging 이름이 실행마다 달라야 한다"


# ---------------------------------------------------------------------------
# 구현리뷰 12차 필수 1·2·3·4 — 이번 라운드 방어를 각각 격리해서 본다
# ---------------------------------------------------------------------------


def _mini_archive(tmp_path: Path, extra: str | None = None) -> Path:
    """정본이 아닌 합성 ZIP. pin 대조가 살아 있으면 반드시 거부된다."""

    import zipfile

    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project/repository/sample/data/lot_history.csv", "a\n1\n")
        if extra:
            archive.writestr(extra, "x")
    return path


def test_snapshot_loader_rejects_an_archive_that_is_not_the_pinned_one(
    tmp_path: Path,
) -> None:
    """선택 CSV만 보면 재포장 ZIP도 승인된다 — 실제로 승인됐다.

    archive **전체** SHA를 pin과 대조해야 계획 §2·§13의 "source 정본은 최종 ZIP과
    manifest v4뿐"이 성립한다(구현리뷰 12차 필수 2).
    """

    import transition_sessions as ts

    with pytest.raises(ts.SessionError) as caught:
        ts.load_profile_snapshots(_mini_archive(tmp_path))
    assert caught.value.reason_code == "ARCHIVE_MISMATCH"


def test_archive_pin_is_checked_by_streaming_the_whole_file(tmp_path: Path) -> None:
    """pin 대조 자체를 격리해서 본다."""

    import transition_sessions as ts

    with pytest.raises(ts.SessionError) as caught:
        ts.assert_archive_is_pinned(_mini_archive(tmp_path))
    assert caught.value.reason_code == "ARCHIVE_MISMATCH"

    missing = tmp_path / "nope.zip"
    with pytest.raises(ts.SessionError) as caught:
        ts.assert_archive_is_pinned(missing)
    assert caught.value.reason_code == "ARCHIVE_INVALID"


def test_member_set_must_cover_every_declared_member(tmp_path: Path) -> None:
    """intake가 선언한 member가 ZIP에 없으면 거부한다."""

    import zipfile

    import transition_sessions as ts

    path = _mini_archive(tmp_path)
    with zipfile.ZipFile(path) as archive:
        ts.assert_members_match_intake(archive, {})
        with pytest.raises(ts.SessionError) as caught:
            ts.assert_members_match_intake(archive, {"missing/member.csv": {}})
    assert caught.value.reason_code == "ARCHIVE_MISMATCH"


def test_snapshot_loader_cross_validates_the_bootstrap_artifacts(
    tmp_path: Path,
) -> None:
    """epoch·manifest·intake 교차 검증을 건너뛰면 변조된 manifest가 통과한다."""

    import json

    import transition_sessions as ts

    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps({"tables": {}}), encoding="utf-8")
    with pytest.raises(Exception) as caught:
        ts.load_profile_snapshots(
            Path.home() / "Downloads" / "project.zip", manifest_path=broken
        )
    assert not isinstance(caught.value, AssertionError)


def test_partial_publication_is_cleaned_up_and_retryable(tmp_path: Path) -> None:
    """rename 사이에 죽으면 marker 없는 부분 set이 남는다.

    그걸 치우지 않으면 다음 실행이 `BACKUP_INVALID`로 봉쇄된다
    (구현리뷰 12차 필수 3).
    """

    inventory = _inventory("kosa_agent")
    real_replace = Path.replace
    calls = {"n": 0}

    def flaky(self: Path, target: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("rename 실패")
        return real_replace(self, target)

    Path.replace = flaky  # type: ignore[method-assign]
    try:
        with pytest.raises(OSError):
            orchestrator.backup_and_verify(
                inventory,
                source=_source(),
                change_ref="GH-104",
                preflight_bundle_sha256="0" * 64,
                backup_root=tmp_path,
                lifecycle=_lifecycle,
                runner=_runner([]),
                reader=_reader(inventory),
            )
    finally:
        Path.replace = real_replace  # type: ignore[method-assign]

    assert _published(tmp_path) == [], "부분 게시가 남았다"
    receipt = orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner([]),
        reader=_reader(inventory),
    )
    assert receipt["restore_verified"] is True


def test_incomplete_set_without_a_marker_is_not_treated_as_evidence(
    tmp_path: Path,
) -> None:
    """marker 없는 잔재는 정상 증적이 아니다 — 치우고 진행한다."""

    inventory = _inventory("kosa_agent")
    stale = tmp_path / transition.archive_name("kosa_agent", "GH-104")
    stale.write_bytes(b"stale")
    receipt = orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner([]),
        reader=_reader(inventory),
    )
    assert receipt["restore_verified"] is True
    assert stale.read_bytes() != b"stale"


def test_view_acl_postcheck_rejects_a_wrong_acl() -> None:
    """복원 문장을 보냈어도 결과가 다르면 commit하지 않는다."""

    import transition_sessions as ts

    class _WrongAcl(_Transactional):
        def _acl_rows(self) -> Any:
            return [
                {
                    "owner": transition.LEGACY_VIEW_OWNER,
                    "acl": "{kosa=arwdDxt/kosa}",
                    "comment": transition.LEGACY_VIEW_COMMENT,
                }
            ]

    tables = ("lot_history",)
    handler = ts.make_transition_handler(
        csv_bodies=dict.fromkeys(tables, b"a\n1\n"),
        columns_by_table=dict.fromkeys(tables, ("a",)),
        tables=tables,
        profile="runtime",
    )
    connection = _WrongAcl(LEGACY_BODY)
    original = transition.LEGACY_VIEW_SHA256
    transition.LEGACY_VIEW_SHA256 = transition.view_fingerprint(LEGACY_BODY)
    try:
        with pytest.raises(ts.SessionError) as caught:
            handler(connection, "kosa_agent", _inventory("kosa_agent"))
    finally:
        transition.LEGACY_VIEW_SHA256 = original
    assert caught.value.reason_code == "MODE_CONTRACT_ERROR"


def test_version_command_needs_no_mount(tmp_path: Path) -> None:
    """version 확인에 host 경로를 mount하면 Windows에서 성립하지 않는다."""

    calls: list[Sequence[str]] = []
    orchestrator.backup_and_verify(
        _inventory("kosa_agent"),
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner(calls),
        reader=_reader(_inventory("kosa_agent")),
    )
    for argv in calls:
        if "--version" in argv:
            assert "--volume" not in argv, "version 명령에 mount가 붙었다"


def test_backup_cli_builds_its_own_dependencies(tmp_path: Path) -> None:
    """운영 기본값이 없으면 격리 환경에서도 production 경로를 돌릴 수 없다.

    자격증명이 없으면 연결 0건으로 `APPROVAL_REQUIRED`이고, 있으면 실제로 factory를
    만들어 연결을 시도한다(구현리뷰 12차 필수 1).
    """

    argv = [
        "--backup-root",
        str(tmp_path),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        "0" * 64,
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]
    # inspector·reader를 주지 않아도 `MODE_NOT_WIRED`가 아니다.
    assert orchestrator.main(argv, environ={}) == orchestrator.EXIT_CONFIRM_REQUIRED


def test_snapshot_loader_calls_every_gate_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """artifact 교차검증 → archive pin → member set 순으로 **모두** 부른다.

    하나라도 호출이 빠지면 그 방어가 죽는다(구현리뷰 12차 필수 2).
    """

    import rebuild_runner
    import transition_sessions as ts

    order: list[str] = []
    real_validate = rebuild_runner.validate_artifacts
    real_pin = ts.assert_archive_is_pinned
    real_members = ts.assert_members_match_intake

    def track(name: str, real: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            order.append(name)
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        rebuild_runner, "validate_artifacts", track("artifacts", real_validate)
    )
    monkeypatch.setattr(ts, "assert_archive_is_pinned", track("pin", real_pin))
    monkeypatch.setattr(
        ts, "assert_members_match_intake", track("members", real_members)
    )
    ts.load_profile_snapshots(Path.home() / "Downloads" / "project.zip")
    assert order == ["artifacts", "pin", "members"]


def test_snapshot_loader_stops_on_a_broken_manifest(tmp_path: Path) -> None:
    """교차검증을 건너뛰면 변조된 manifest가 통과한다."""

    import json

    import rebuild_runner
    import transition_sessions as ts

    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps({"tables": {}}), encoding="utf-8")
    with pytest.raises(
        (
            ts.SessionError,
            rebuild_runner.RunnerError,
            rebuild_runner.ManifestSchemaError,
            KeyError,
            ValueError,
        )
    ) as caught:
        ts.load_profile_snapshots(
            Path.home() / "Downloads" / "project.zip", manifest_path=broken
        )
    # 어떤 경로로든 snapshot을 만들지 않고 멈춘다.
    assert caught.value is not None


def test_backup_cli_does_not_report_mode_not_wired_with_valid_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """운영 기본값이 있으면 배선 부족으로 끝나지 않는다.

    기본 배선을 지우면 valid 환경에서도 `MODE_NOT_WIRED`, exit 2였다
    (구현리뷰 12차 필수 1).
    """

    argv = [
        "--backup-root",
        str(tmp_path),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        "0" * 64,
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]
    env = {
        "POSTGRES_TRANSITION_HOST": "nonexistent.invalid",
        "POSTGRES_TRANSITION_PORT": "5432",
        "POSTGRES_TRANSITION_USER": "role",
        "POSTGRES_TRANSITION_PASSWORD": "pw",
        "POSTGRES_TRANSITION_ALLOWED_HOST_SHA256": transition.endpoint_fingerprint(
            "nonexistent.invalid", 5432
        ),
    }
    # 연결은 실패하지만 배선 문제는 아니다. exit code가 둘 다 2라 **reason**을 본다.
    code = orchestrator.main(argv, environ=env)
    assert code == orchestrator.EXIT_USAGE
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["reason_code"] != "MODE_NOT_WIRED"
    assert _published(tmp_path) == []


# ---------------------------------------------------------------------------
# 구현리뷰 13차 필수 2 — 동시 실행 소유권과 소비자 연결
# ---------------------------------------------------------------------------


def test_a_second_run_cannot_delete_a_publishing_runs_files(tmp_path: Path) -> None:
    """A가 게시 중일 때 B가 A의 파일을 지우면 marker만 남고 실물이 사라진다.

    barrier로 그 interleaving을 **결정적으로** 재현한다. 이전 회귀는 UUID 유일성만
    확인해 이 경쟁을 실행하지 않았다(구현리뷰 13차 필수 2).
    """

    import threading

    inventory = _inventory("kosa_agent")
    published = threading.Event()
    b_done = threading.Event()
    real_write = backup.atomic_write_json
    failures: list[BaseException] = []

    def paused(path: Path, payload: Any, *, trusted_root: Path) -> Any:
        if path.name.endswith(".complete.json"):
            # A가 세 파일을 옮긴 뒤 marker를 쓰기 **직전**이다.
            published.set()
            b_done.wait(timeout=10)
        return real_write(path, payload, trusted_root=trusted_root)

    def run_b() -> None:
        published.wait(timeout=10)
        try:
            orchestrator.backup_and_verify(
                inventory,
                source=_source(),
                change_ref="GH-104",
                preflight_bundle_sha256="0" * 64,
                backup_root=tmp_path,
                lifecycle=_lifecycle,
                runner=_runner([]),
                reader=_reader(inventory),
            )
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)
        finally:
            b_done.set()

    backup.atomic_write_json = paused  # type: ignore[assignment]
    thread = threading.Thread(target=run_b)
    try:
        thread.start()
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(inventory),
        )
        thread.join(timeout=15)
    finally:
        backup.atomic_write_json = real_write  # type: ignore[assignment]
        b_done.set()
        thread.join(timeout=15)

    # B는 소유권을 잡지 못해 물러난다.
    assert failures and isinstance(failures[0], orchestrator.OrchestrationError)
    assert failures[0].reason_code == "TARGET_BUSY"

    # A의 증적은 marker와 세 실물이 **모두** 남는다.
    names = {p.name for p in tmp_path.iterdir()}
    assert transition.archive_name("kosa_agent", "GH-104") in names
    assert transition.view_sidecar_name("kosa_agent", "GH-104") in names
    assert orchestrator.receipt_name("kosa_agent", "GH-104") in names
    assert orchestrator.completion_name("kosa_agent", "GH-104") in names


def test_ownership_lock_is_released_on_failure(tmp_path: Path) -> None:
    """실패해도 lock이 남으면 그 target은 영영 막힌다."""

    inventory = _inventory("kosa_agent")
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([], returncode=1),
            reader=_reader(inventory),
        )
    # 실패해도 소유권 주장이 남지 않는다. 파일 자체는 지우지 않는다 —
    # 지우면 `read → unlink` 경쟁이 생긴다(구현리뷰 16차 필수 2).
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
    # 즉시 재시도가 성공한다.
    orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner([]),
        reader=_reader(inventory),
    )


def test_completion_marker_carries_the_three_digests(tmp_path: Path) -> None:
    """marker가 세 실물 digest를 고정해야 소비자가 대조할 수 있다."""

    import json

    inventory = _inventory("kosa_agent")
    orchestrator.backup_and_verify(
        inventory,
        source=_source(),
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        backup_root=tmp_path,
        lifecycle=_lifecycle,
        runner=_runner([]),
        reader=_reader(inventory),
    )
    payload = json.loads(
        (tmp_path / orchestrator.completion_name("kosa_agent", "GH-104")).read_text(
            encoding="utf-8"
        )
    )
    orchestrator.validate_completion(payload)
    assert payload["database"] == "kosa_agent"
    assert payload["archive_sha256"] == backup.archive_digest(
        tmp_path / transition.archive_name("kosa_agent", "GH-104")
    )
    assert payload["receipt_sha256"] == backup.archive_digest(
        tmp_path / orchestrator.receipt_name("kosa_agent", "GH-104")
    )


@pytest.mark.parametrize("field", sorted(orchestrator.COMPLETION_KEYS))
def test_completion_schema_is_exact(field: str) -> None:
    payload = {
        "artifact_type": orchestrator.COMPLETION_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": "kosa_agent",
        "change_ref": "GH-104",
        "archive_sha256": "a" * 64,
        "view_sidecar_sha256": "b" * 64,
        "receipt_sha256": "c" * 64,
    }
    orchestrator.validate_completion(payload)
    broken = dict(payload)
    del broken[field]
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.validate_completion(broken)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_restore_reader_builds_a_safe_url() -> None:
    """특수문자 비밀번호가 host·database로 잘려 들어가면 틀린 곳에 붙는다."""

    from sqlalchemy.engine import make_url

    captured: dict[str, Any] = {}

    def fake_create_engine(url: Any, **kwargs: Any) -> Any:
        captured["url"] = url
        captured["kwargs"] = kwargs
        raise RuntimeError("stop-before-connect")

    import sqlalchemy

    original = sqlalchemy.create_engine
    sqlalchemy.create_engine = fake_create_engine  # type: ignore[assignment]

    class _Endpoint2:
        host = "127.0.0.1"
        port = 55432
        username = "role/with:chars"
        password = "p@ss/word %20"
        database = "fdc_restore_verify"

    try:
        with pytest.raises(RuntimeError):
            orchestrator.default_restore_reader(_Endpoint2(), "kosa_agent", "runtime")
    finally:
        sqlalchemy.create_engine = original  # type: ignore[assignment]

    parsed = make_url(captured["url"].render_as_string(hide_password=False))
    assert parsed.username == _Endpoint2.username
    assert parsed.password == _Endpoint2.password
    assert parsed.host == _Endpoint2.host
    assert parsed.port == _Endpoint2.port
    assert parsed.database == _Endpoint2.database
    assert captured["kwargs"]["hide_parameters"] is True
    # 문자열 표현에 secret이 드러나지 않는다.
    assert _Endpoint2.password not in str(captured["url"])


def test_restore_engine_hides_parameters() -> None:
    """예외·log에 DSN이 실리지 않게 한다."""

    captured: dict[str, Any] = {}

    def fake_create_engine(url: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        raise RuntimeError("stop")

    import sqlalchemy

    original = sqlalchemy.create_engine
    sqlalchemy.create_engine = fake_create_engine  # type: ignore[assignment]

    class _Endpoint3:
        host = "127.0.0.1"
        port = 5432
        username = "u"
        password = "p"
        database = "d"

    try:
        with pytest.raises(RuntimeError):
            orchestrator.default_restore_reader(_Endpoint3(), "kosa_agent", "runtime")
    finally:
        sqlalchemy.create_engine = original  # type: ignore[assignment]
    assert captured["hide_parameters"] is True


# ---------------------------------------------------------------------------
# 구현리뷰 14차 필수 3 · 권장 1 — stale lock 복구, archive object type
# ---------------------------------------------------------------------------


def test_lock_carries_provenance(tmp_path: Path) -> None:
    """강제 종료 뒤 소유자를 판정하려면 lock에 근거가 있어야 한다."""

    import json

    seen: dict[str, Any] = {}
    with orchestrator._own_evidence(tmp_path, "kosa_agent", "GH-104"):
        path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
        seen["payload"] = json.loads(path.read_text(encoding="utf-8"))
    orchestrator.validate_lock(seen["payload"])
    assert seen["payload"]["database"] == "kosa_agent"
    assert seen["payload"]["change_ref"] == "GH-104"
    assert seen["payload"]["token"]
    # secret은 담지 않는다.
    assert set(seen["payload"]) == set(orchestrator.LOCK_KEYS)
    # 정상 종료에서는 소유권이 풀린다. 경로는 남지만 주장이 없다.
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")


def test_a_crashed_run_leaves_a_lock_that_blocks_until_recovered(
    tmp_path: Path,
) -> None:
    """`SIGKILL`이면 `finally`가 돌지 않아 lock이 남는다.

    자동 만료로 회수하면 살아 있는 다른 실행의 lock을 훔친다. 그래서 운영자가 token을
    확인해 명시 승인해야만 회수된다(구현리뷰 14차 필수 3).
    """

    import json

    # 강제 종료 재현 — `finally`가 돌지 못한 상태를 그대로 만든다.
    # (contextmanager를 열어 두면 GC가 `__exit__`를 실행해 lock이 사라진다.)
    token = "abcdef123456"
    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    path.write_text(
        json.dumps(
            {
                "artifact_type": orchestrator.LOCK_ARTIFACT_TYPE,
                "dataset_epoch": transition.DATASET_EPOCH,
                "database": "kosa_agent",
                "change_ref": "GH-104",
                "token": token,
                "created_at": "2026-08-22T12:00:00+09:00",
            }
        ),
        encoding="utf-8",
    )

    inventory = _inventory("kosa_agent")

    def attempt() -> Any:
        return orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(inventory),
        )

    with pytest.raises(orchestrator.OrchestrationError) as caught:
        attempt()
    assert caught.value.reason_code == "TARGET_BUSY"

    # 승인 없이는 회수되지 않는다.
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.recover_stale_lock(
            tmp_path, "kosa_agent", "GH-104", token=token, environ={}
        )
    assert caught.value.reason_code == "TARGET_BUSY"
    assert path.is_file()

    # 다른 token으로는 회수할 수 없다.
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.recover_stale_lock(
            tmp_path,
            "kosa_agent",
            "GH-104",
            token="0" * 12,
            environ={orchestrator.STALE_LOCK_ENV_KEY: "0" * 12},
        )
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert path.is_file()

    # 승인하면 회수되고 재시도가 성공한다.
    orchestrator.recover_stale_lock(
        tmp_path,
        "kosa_agent",
        "GH-104",
        token=token,
        environ={orchestrator.STALE_LOCK_ENV_KEY: token},
    )
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
    assert attempt()["restore_verified"] is True


def test_recovery_never_touches_other_evidence(tmp_path: Path) -> None:
    """복구는 lock만 지운다. 다른 실행의 증적은 건드리지 않는다."""

    import json

    other = tmp_path / transition.archive_name("kosa_agent_e2e", "GH-104")
    other.write_bytes(b"other-run")
    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    payload = {
        "artifact_type": orchestrator.LOCK_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": "kosa_agent",
        "change_ref": "GH-104",
        "token": "abcdef123456",
        "created_at": "2026-08-22T12:00:00+09:00",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    orchestrator.recover_stale_lock(
        tmp_path,
        "kosa_agent",
        "GH-104",
        token="abcdef123456",
        environ={orchestrator.STALE_LOCK_ENV_KEY: "abcdef123456"},
    )
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
    assert other.read_bytes() == b"other-run"


@pytest.mark.parametrize("field", sorted(orchestrator.LOCK_KEYS))
def test_lock_schema_is_exact(field: str) -> None:
    payload = {
        "artifact_type": orchestrator.LOCK_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": "kosa_agent",
        "change_ref": "GH-104",
        "token": "abcdef123456",
        "created_at": "2026-08-22T12:00:00+09:00",
    }
    orchestrator.validate_lock(payload)
    broken = dict(payload)
    del broken[field]
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.validate_lock(broken)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


@pytest.mark.parametrize(
    "entry",
    [
        "205; 1259 16456 VIEW public v_alarm_event kosa",
        "206; 1255 16460 FUNCTION public f() kosa",
        "207; 1259 16461 SEQUENCE public s kosa",
        "208; 1259 16462 TABLE other lot_history kosa",
        "209; 1259 16463 CONSTRAINT other c kosa",
    ],
)
def test_archive_rejects_object_types_outside_the_allowlist(entry: str) -> None:
    """계획 §7.2는 "base 9 외 object가 있으면 실패"다. table 이름만 보면 놓친다."""

    listing = "\n".join(
        f"1{i}; 1259 1{i} TABLE public {name} kosa"
        for i, name in enumerate(sorted(transition.BASE_TABLES))
    )
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.archive_table_names(listing + "\n" + entry)
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_archive_accepts_table_and_table_data_entries() -> None:
    listing = (
        "205; 1259 16456 TABLE public lot_history kosa\n"
        "3421; 0 16456 TABLE DATA public lot_history kosa\n"
    )
    assert orchestrator.archive_table_names(listing) == {"lot_history"}


def test_recovery_rejects_a_wrong_token_for_a_malformed_lock(tmp_path: Path) -> None:
    """깨진 lock도 회수할 수 있어야 하지만, 아무 token으로는 안 된다."""

    import json

    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    path.write_text(json.dumps({"database": "kosa_agent"}), encoding="utf-8")
    assert orchestrator.read_lock(tmp_path, "kosa_agent", "GH-104")["state"] == (
        "malformed"
    )
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.recover_stale_lock(
            tmp_path,
            "kosa_agent",
            "GH-104",
            token="abcdef123456",
            environ={orchestrator.STALE_LOCK_ENV_KEY: "abcdef123456"},
        )
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert path.is_file(), "잘못된 token으로 지웠다"


def test_lock_tokens_are_unique_per_execution(tmp_path: Path) -> None:
    """token이 고정값이면 다른 실행의 lock을 그 값으로 회수할 수 있다."""

    import json

    tokens: set[str] = set()
    for index in range(5):
        root = tmp_path / f"root{index}"
        root.mkdir(mode=0o700)
        with orchestrator._own_evidence(root, "kosa_agent", "GH-104"):
            payload = json.loads(
                (root / orchestrator.lock_name("kosa_agent", "GH-104")).read_text(
                    encoding="utf-8"
                )
            )
            tokens.add(payload["token"])
    assert len(tokens) == 5


def test_archive_listing_rejects_an_unknown_object_type_directly() -> None:
    """허용 목록 밖 entry는 base 9만 있어도 거부한다."""

    # schema는 `public`이다 — type 판정만이 이걸 걸러낼 수 있다.
    # VIEW·FUNCTION·SEQUENCE는 base 9만 덤프한 archive에 들어갈 수 없다.
    listing = "205; 1259 16456 VIEW public v_alarm_event kosa"
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.archive_table_names(listing)
    assert caught.value.reason_code == "BACKUP_INVALID"
    for other in (
        "206; 1255 16460 FUNCTION public f() kosa",
        "207; 1259 16461 SEQUENCE public s kosa",
        "208; 1259 16462 PROCEDURE public p() kosa",
    ):
        with pytest.raises(orchestrator.OrchestrationError):
            orchestrator.archive_table_names(other)
    # base 9에 **딸린** entry는 정상이다 — 실제 `pg_restore --list`가 그렇다.
    assert orchestrator.TABLE_ARCHIVE_ENTRIES <= orchestrator.ALLOWED_ARCHIVE_ENTRIES
    assert "CONSTRAINT" in orchestrator.ALLOWED_ARCHIVE_ENTRIES


def test_final_archive_env_must_point_at_the_pinned_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """지정된 경로가 정본이 아니면 skip이 아니라 실패다.

    개인 경로 fallback이나 조용한 skip을 두면 CI가 green인데 핵심 회귀가 돌지 않는다
    (구현리뷰 14차 필수 2).
    """

    import zipfile

    import test_transition_e2e_container as e2e
    import transition_sessions as ts

    other = tmp_path / "not-the-final.zip"
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("x.txt", "x")
    monkeypatch.setenv(e2e.FINAL_ARCHIVE_ENV_KEY, str(other))
    with pytest.raises(ts.SessionError) as caught:
        e2e._final_archive()
    assert caught.value.reason_code == "ARCHIVE_MISMATCH"

    monkeypatch.setenv(e2e.FINAL_ARCHIVE_ENV_KEY, str(tmp_path / "missing.zip"))
    with pytest.raises(BaseException) as failure:
        e2e._final_archive()
    assert "Skipped" not in type(failure.value).__name__


# ---------------------------------------------------------------------------
# 구현리뷰 15차 필수 2 — 실제 강제 종료가 남기는 lock
# ---------------------------------------------------------------------------

_CRASH_SCRIPT = """
import os, signal, sys
sys.path.insert(0, {scripts!r})
import backup_orchestrator as orchestrator
from pathlib import Path

root = Path(sys.argv[1])
stage = sys.argv[2]
real_open = os.open
real_write = os.write


def hooked_open(path, flags, mode=0o777):
    descriptor = real_open(path, flags, mode)
    if stage == "empty" and str(path).endswith(".lock"):
        # 파일만 만들고 body를 쓰기 전에 죽는다.
        os.kill(os.getpid(), signal.SIGKILL)
    return descriptor


def hooked_write(descriptor, payload):
    if stage == "partial" and len(payload) > 2:
        # body를 절반만 쓰고 죽는다.
        real_write(descriptor, payload[: len(payload) // 2])
        os.kill(os.getpid(), signal.SIGKILL)
    return real_write(descriptor, payload)


os.open = hooked_open
os.write = hooked_write
with orchestrator._own_evidence(root, "kosa_agent", "GH-104"):
    if stage == "valid":
        # body까지 쓴 뒤 죽는다.
        os.kill(os.getpid(), signal.SIGKILL)
"""


@pytest.mark.parametrize("stage", ["empty", "partial", "valid"])
def test_a_killed_process_leaves_a_recoverable_lock(tmp_path: Path, stage: str) -> None:
    """실제 `SIGKILL`이 만드는 lock 세 상태가 모두 다시 진행 가능해야 한다.

    JSON을 손으로 완성해 두는 회귀는 0-byte·부분 기록 상태를 보지 못했다. 그 상태는
    schema parse에서 막혀 **영구 `TARGET_BUSY`**였다(구현리뷰 15차 필수 2).

    `empty`는 승인 없이 바로 진행된다. 파일만 만들고 죽은 실행은 소유권을 주장한 적이
    없고(주장은 payload를 쓴 뒤에 성립한다) advisory lock도 비어 있어 살아 있지도
    않다. 승인을 요구할 근거가 없다. `partial`·`valid`는 주장이 남아 있어 명시 회수를
    거쳐야 한다.
    """

    import subprocess
    import sys as _sys

    script = _CRASH_SCRIPT.format(scripts=str(SCRIPTS_ROOT))
    completed = subprocess.run(
        [_sys.executable, "-c", script, str(tmp_path), stage],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0, "프로세스가 죽지 않았다"

    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    assert path.is_file(), f"{stage}: lock이 남지 않았다"

    status = orchestrator.read_lock(tmp_path, "kosa_agent", "GH-104")
    expected = {"empty": "empty", "partial": "malformed", "valid": "valid"}[stage]
    assert status["state"] == expected

    inventory = _inventory("kosa_agent")

    def attempt() -> Any:
        return orchestrator.backup_and_verify(
            inventory,
            source=_source(),
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            backup_root=tmp_path,
            lifecycle=_lifecycle,
            runner=_runner([]),
            reader=_reader(inventory),
        )

    if stage == "empty":
        # 주장이 없으므로 회수 절차 없이 바로 진행된다.
        assert attempt()["restore_verified"] is True
        assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
        return

    with pytest.raises(orchestrator.OrchestrationError) as caught:
        attempt()
    assert caught.value.reason_code == "TARGET_BUSY"

    token = status["token"] or orchestrator.MALFORMED_LOCK_TOKEN
    # 승인 없이는 회수되지 않는다.
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.recover_stale_lock(
            tmp_path, "kosa_agent", "GH-104", token=token, environ={}
        )
    assert caught.value.reason_code == "TARGET_BUSY"
    assert path.is_file()

    # 승인하면 회수되고 즉시 재시도가 성공한다.
    orchestrator.recover_stale_lock(
        tmp_path,
        "kosa_agent",
        "GH-104",
        token=token,
        environ={orchestrator.STALE_LOCK_ENV_KEY: token},
    )
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
    assert attempt()["restore_verified"] is True


def test_release_never_deletes_another_runs_lock(tmp_path: Path) -> None:
    """회수 뒤 새 실행이 lock을 잡았는데 이전 실행의 `finally`가 지우면 안 된다."""

    import json

    manager = orchestrator._own_evidence(tmp_path, "kosa_agent", "GH-104")
    manager.__enter__()
    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    mine = json.loads(path.read_text(encoding="utf-8"))["token"]

    # 운영자가 회수하고 다른 실행이 새로 잡았다.
    path.unlink()
    with orchestrator._own_evidence(tmp_path, "kosa_agent", "GH-104"):
        other = json.loads(path.read_text(encoding="utf-8"))["token"]
        assert other != mine
        # 이전 실행의 finally가 여기서 돈다.
        manager.__exit__(None, None, None)
        assert path.is_file(), "남의 lock을 지웠다"
        assert json.loads(path.read_text(encoding="utf-8"))["token"] == other


@pytest.mark.windows_contract
def test_a_live_owner_blocks_acquire_recover_and_reports_live(tmp_path: Path) -> None:
    """소유 기간 **내내** 다른 프로세스가 진입하지 못해야 한다.

    이전 protocol은 `read token → unlink` 두 단계라, 그 사이에 다른 실행이 새 lock을
    만들면 이전 소유자·회수자가 남의 lock을 지웠다(구현리뷰 16차 필수 2). 이 회귀는
    **다른 프로세스**를 그 구간에 밀어 넣는다. advisory lock을 소유자가 쥐고 있으므로
    진입 자체가 실패해야 한다.
    """

    import json
    import subprocess
    import sys as _sys

    probe = _LOCK_PROBE.format(scripts=str(SCRIPTS_ROOT))

    def run_probe(action: str, token: str) -> dict[str, Any]:
        completed = subprocess.run(
            [_sys.executable, "-c", probe, str(tmp_path), action, token],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        return dict(json.loads(completed.stdout.strip().splitlines()[-1]))

    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    with orchestrator._own_evidence(tmp_path, "kosa_agent", "GH-104"):
        # 살아 있는 lock의 **내용은 읽을 수 있어야** 한다. Windows `msvcrt.locking`은
        # 강제 lock이라 payload 안을 잠그면 여기서 `PermissionError`가 난다 —
        # 실제 Windows CI가 그렇게 실패했다(구현리뷰 19차 필수 2 검증).
        token = json.loads(path.read_text(encoding="utf-8"))["token"]
        # 살아 있는 소유자는 acquire·recover 양쪽을 막는다.
        assert run_probe("acquire", token) == {"reason": "TARGET_BUSY"}
        # 승인 token이 맞아도 살아 있는 lock은 뺏지 못한다.
        assert run_probe("recover", token) == {"reason": "TARGET_BUSY"}
        # 소유권 주장은 그대로다.
        assert json.loads(path.read_text(encoding="utf-8"))["token"] == token
        assert orchestrator.read_lock(tmp_path, "kosa_agent", "GH-104")["live"] is True

    # 놓은 뒤에는 같은 프로세스가 바로 잡는다.
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
    assert run_probe("acquire", token) == {"ok": True}


#: 별도 프로세스에서 lock 진입을 시도한다. 같은 프로세스면 flock이 같은 fd를 재사용할
#: 여지가 있어 경쟁을 재현하지 못한다.
_LOCK_PROBE = """
import json
import sys

sys.path.insert(0, {scripts!r})
import backup_orchestrator as orchestrator

root, action, token = sys.argv[1], sys.argv[2], sys.argv[3]
from pathlib import Path

try:
    if action == "acquire":
        with orchestrator._own_evidence(Path(root), "kosa_agent", "GH-104"):
            pass
    else:
        orchestrator.recover_stale_lock(
            Path(root),
            "kosa_agent",
            "GH-104",
            token=token,
            environ={{orchestrator.STALE_LOCK_ENV_KEY: token}},
        )
except orchestrator.OrchestrationError as exc:
    print(json.dumps({{"reason": exc.reason_code}}))
else:
    print(json.dumps({{"ok": True}}))
"""


def test_lock_cli_inspects_and_recovers(tmp_path: Path) -> None:
    """운영자가 Python 내부 함수 대신 CLI로 처리할 수 있어야 한다."""

    import json

    path = tmp_path / orchestrator.lock_name("kosa_agent", "GH-104")
    path.write_bytes(b"")
    base = [
        "--backup-root",
        str(tmp_path),
        "--change-ref",
        "GH-104",
        "--database",
        "kosa_agent",
    ]
    assert orchestrator.main([*base, "--inspect-lock"], environ={}) == 0

    token = orchestrator.MALFORMED_LOCK_TOKEN
    # 승인 없이는 거부한다.
    assert (
        orchestrator.main([*base, "--recover-lock", token], environ={})
        == orchestrator.EXIT_CONFIRM_REQUIRED
    )
    assert path.is_file()

    assert (
        orchestrator.main(
            [*base, "--recover-lock", token],
            environ={orchestrator.STALE_LOCK_ENV_KEY: token},
        )
        == 0
    )
    assert _lock_is_free(tmp_path, "kosa_agent", "GH-104")
    _ = json


# ---------------------------------------------------------------------------
# 구현리뷰 16차 필수 1 — 공용 endpoint identity
# ---------------------------------------------------------------------------


def _endpoint_env(host: str, port: str, allowed: str) -> dict[str, str]:
    return {
        "POSTGRES_TRANSITION_HOST": host,
        "POSTGRES_TRANSITION_PORT": port,
        "POSTGRES_TRANSITION_USER": "role",
        "POSTGRES_TRANSITION_PASSWORD": "pw",
        transition.ALLOWED_ENDPOINT_ENV_KEY: allowed,
    }


@pytest.mark.windows_contract
def test_endpoint_fingerprint_matches_the_bootstrap_formula() -> None:
    """bootstrap과 형식이 다르면 운영자가 같은 서버에 두 hash를 관리하게 된다.

    그 순간 한쪽은 반드시 틀리고, 틀린 쪽이 이 경로면 base 9가 지워진다.
    """

    import db_target

    assert transition.endpoint_fingerprint(
        "db.internal", 5432
    ) == db_target.host_fingerprint("db.internal", 5432)


@pytest.mark.windows_contract
@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("db.internal", "5432"),
        # host 대소문자는 같은 서버다.
        ("DB.Internal", "5432"),
        # port는 십진수 문자열이다.
        ("db.internal", "05432"),
    ],
)
def test_matching_endpoint_is_accepted(host: str, port: str) -> None:
    allowed = transition.endpoint_fingerprint("db.internal", 5432)
    assert (
        transition.endpoint_rejection(
            host, int(port), environ={transition.ALLOWED_ENDPOINT_ENV_KEY: allowed}
        )
        is None
    )


@pytest.mark.windows_contract
@pytest.mark.parametrize(
    ("host", "port", "allowed", "exit_code"),
    [
        # 다른 서버.
        ("other.internal", "5432", None, orchestrator.EXIT_MISMATCH),
        # 같은 host, 다른 port — 별개의 PostgreSQL이다.
        ("db.internal", "5433", None, orchestrator.EXIT_MISMATCH),
        # 지정 누락.
        ("db.internal", "5432", "", orchestrator.EXIT_USAGE),
        # 형식 오류.
        ("db.internal", "5432", "not-a-sha256", orchestrator.EXIT_USAGE),
        # 대문자 hex는 우리가 쓰는 형식이 아니다.
        (
            "db.internal",
            "5432",
            transition.endpoint_fingerprint("db.internal", 5432).upper(),
            orchestrator.EXIT_USAGE,
        ),
    ],
)
def test_wrong_endpoint_is_refused_before_any_connection(
    host: str, port: str, allowed: str | None, exit_code: int
) -> None:
    """거부는 connector 0회여야 한다.

    `.env`가 다른 PostgreSQL을 가리켜도 그 서버에서 preflight와 approval을 새로 만들면
    나머지 계약은 전부 통과한다. approval의 fingerprint는 **DB 내용**의 신원이지
    network target의 신원이 아니기 때문이다(구현리뷰 16차 필수 1).
    """

    import sqlalchemy

    if allowed is None:
        allowed = transition.endpoint_fingerprint("db.internal", 5432)
    env = _endpoint_env(host, port, allowed)

    opened: list[Any] = []
    original = sqlalchemy.create_engine

    def spy(*args: Any, **kwargs: Any) -> Any:
        opened.append(args)
        raise AssertionError("engine을 만들면 안 된다")

    sqlalchemy.create_engine = spy  # type: ignore[assignment]
    try:
        # backup 진입점.
        with pytest.raises(orchestrator.OrchestrationError) as backup_error:
            orchestrator.source_from_environment("kosa_agent", env)
        assert backup_error.value.reason_code == "ENDPOINT_NOT_ALLOWED"
        assert backup_error.value.exit_code == exit_code

        # transition 진입점 — **같은** validator를 써야 한다.
        with pytest.raises(sessions.SessionError) as session_error:
            with sessions.read_only_session("kosa_agent", environ=env):
                pass
        assert session_error.value.reason_code == "ENDPOINT_NOT_ALLOWED"
        assert session_error.value.exit_code == exit_code
    finally:
        sqlalchemy.create_engine = original  # type: ignore[assignment]
    assert opened == [], "거부인데 engine을 만들었다"


@pytest.mark.windows_contract
def test_endpoint_reason_never_carries_the_host() -> None:
    """reason에 host가 실리면 외부 report로 새어 나간다."""

    env = _endpoint_env("secret-host.internal", "5432", "0" * 64)
    with pytest.raises(orchestrator.OrchestrationError) as caught:
        orchestrator.source_from_environment("kosa_agent", env)
    assert caught.value.reason_code == "ENDPOINT_NOT_ALLOWED"
    assert "secret-host" not in str(caught.value)
    assert "5432" not in str(caught.value)


@pytest.mark.windows_contract
def test_both_entrypoints_share_the_same_five_endpoint_values() -> None:
    """공통은 endpoint 5개다. archive는 transition apply 전용이라 여기 없다.

    `.env.example`이 "두 CLI가 같은 6개"라고 쓰면 운영자가 backup에도 archive를 넣으려
    한다(구현리뷰 17차 편집 2).
    """

    assert set(orchestrator.REQUIRED_ENV_KEYS) == set(sessions.REQUIRED_ENV_KEYS)
    assert len(orchestrator.REQUIRED_ENV_KEYS) == 5
    assert transition.ALLOWED_ENDPOINT_ENV_KEY in orchestrator.REQUIRED_ENV_KEYS


# ---------------------------------------------------------------------------
# 구현리뷰 17차 필수 2 — backup root 신뢰를 mutation **전에** 본다
# ---------------------------------------------------------------------------


def test_root_trust_is_checked_before_the_first_connector(tmp_path: Path) -> None:
    """root가 열려 있으면 연결도 열지 않는다.

    이전에는 경로·symlink만 보고 DB를 연 뒤, 세 target commit이 끝난 closure에서야
    mode를 봤다. 실행 중에는 다른 사용자가 쓸 수 있었다가 closure 전에 좁혀진 root도
    정상 증적으로 기록됐다(구현리뷰 17차 필수 2).
    """

    root = tmp_path / "outside"
    root.mkdir(mode=0o755)
    env = _endpoint_env(
        "db.internal", "5432", transition.endpoint_fingerprint("db.internal", 5432)
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("신뢰하지 못하는 root인데 연결을 열었다")

    argv = [
        "--backup-root",
        str(root),
        "--change-ref",
        "GH-104",
        "--preflight-bundle-sha256",
        "0" * 64,
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]
    code = orchestrator.main(argv, environ=env, inspector=forbidden, reader=forbidden)
    assert code == orchestrator.EXIT_MISMATCH
    assert _published(root) == []


def test_apply_refuses_an_untrusted_root_before_reading(tmp_path: Path) -> None:
    """transition apply도 같은 validator로 read 0건에서 멈춘다."""

    import transition_public_postgres as cli

    root = tmp_path / "outside"
    root.mkdir(mode=0o777)
    approval = root / "approval.json"
    approval.write_text("{}", encoding="utf-8")

    def forbidden(database: str) -> Any:
        raise AssertionError("신뢰하지 못하는 root인데 연결을 열었다")

    argv = [
        "--apply",
        "--approval",
        str(approval),
        "--backup-root",
        str(root),
        "--change-ref",
        "GH-104",
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
        *[a for t in transition.ORDERED_TARGETS for a in ("--receipt", str(approval))],
    ]
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(argv, inspector=forbidden, repository_root=tmp_path / "repo")
    assert caught.value.reason_code == "BACKUP_ROOT_UNTRUSTED"


#: **POSIX 전용이다.** Windows에서 `backup_root_trust()`는 ACL 명시 입력 분기를 타므로
#: `chmod(0700)`이 `("0700", None)`이 되지 않는다. `windows_contract`로 표시하면 native
#: Windows에서 실패한다(구현리뷰 19차 필수 2).
@pytest.mark.skipif(os.name == "nt", reason="POSIX owner·mode 계약")
@pytest.mark.parametrize("mode", [0o755, 0o770, 0o701, 0o600])
def test_only_exact_0700_is_trusted(tmp_path: Path, mode: int) -> None:
    """`0700`보다 넓어도, 좁아도 우리가 만든 root가 아니다."""

    root = tmp_path / f"root-{mode:o}"
    root.mkdir(mode=mode)
    _, rejection = backup.backup_root_trust(root, change_ref="GH-104", environ={})
    assert rejection == ("BACKUP_ROOT_UNTRUSTED", orchestrator.EXIT_MISMATCH)

    root.chmod(0o700)
    value, rejection = backup.backup_root_trust(root, change_ref="GH-104", environ={})
    assert (value, rejection) == ("0700", None)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner 계약")
def test_a_root_owned_by_another_account_is_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode가 `0700`이어도 **다른 계정 소유**면 그 계정이 증적을 갈아끼울 수 있다.

    `0700`만 보면 남의 `0700` 디렉터리를 backup root로 지정해도 통과한다. 변이 검사에서
    소유자 조건을 지웠을 때 아무 회귀도 깨지지 않아 이 회귀를 추가했다(M240).
    """

    root = tmp_path / "outside"
    root.mkdir(mode=0o700)

    # 실제 chown은 root 권한이 필요하다. 판정 입력인 uid를 바꿔 같은 상황을 만든다.
    monkeypatch.setattr(backup.os, "getuid", lambda: root.stat().st_uid + 1)
    value, rejection = backup.backup_root_trust(root, change_ref="GH-104", environ={})
    assert value == "0700", "mode는 통과하는 상황이어야 소유자 조건만 검증한다"
    assert rejection == ("BACKUP_ROOT_UNTRUSTED", orchestrator.EXIT_MISMATCH)

    # 같은 계정이면 통과한다.
    monkeypatch.setattr(backup.os, "getuid", lambda: root.stat().st_uid)
    assert backup.backup_root_trust(root, change_ref="GH-104", environ={}) == (
        "0700",
        None,
    )


@pytest.mark.windows_contract
@pytest.mark.parametrize(
    ("reviewed", "trusted"),
    [
        # 확인이 없다.
        (None, False),
        # 다른 실행의 확인이다.
        ("GH-999", False),
        # 그 실행을 명시했다.
        ("GH-104", True),
    ],
)
def test_windows_acl_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewed: str | None, trusted: bool
) -> None:
    """Windows에서 **실제로 성립하는** 세 경로. native runner에서 그대로 돈다."""

    monkeypatch.setattr(backup.os, "name", "nt")
    root = tmp_path / "outside"
    root.mkdir()
    environ = {} if reviewed is None else {backup.ROOT_ACL_ENV_KEY: reviewed}
    value, rejection = backup.backup_root_trust(
        root, change_ref="GH-104", environ=environ
    )
    if trusted:
        assert (value, rejection) == (backup.WINDOWS_ACL_REVIEWED, None)
    else:
        assert value == ""
        assert rejection == (
            "BACKUP_ROOT_UNTRUSTED",
            orchestrator.EXIT_CONFIRM_REQUIRED,
        )


@pytest.mark.windows_contract
def test_windows_acl_needs_an_explicit_operator_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.name == "nt"`이라는 이유로 "확인했다"를 만들면 그건 증적이 아니다.

    OS 종류를 다른 이름으로 쓴 값일 뿐이다(구현리뷰 17차 필수 2). 운영자가 그 실행의
    `change_ref`를 명시해야 한다.
    """

    monkeypatch.setattr(backup.os, "name", "nt")
    root = tmp_path / "outside"
    root.mkdir(mode=0o777)

    # 확인이 없으면 막힌다.
    assert backup.backup_root_trust(root, change_ref="GH-104", environ={})[1] == (
        "BACKUP_ROOT_UNTRUSTED",
        orchestrator.EXIT_CONFIRM_REQUIRED,
    )
    # 다른 실행의 확인은 쓸 수 없다.
    assert backup.backup_root_trust(
        root,
        change_ref="GH-104",
        environ={backup.ROOT_ACL_ENV_KEY: "GH-999"},
    )[1] == ("BACKUP_ROOT_UNTRUSTED", orchestrator.EXIT_CONFIRM_REQUIRED)
    # 그 실행을 명시하면 통과하고, 값이 증적에 남는다.
    assert backup.backup_root_trust(
        root,
        change_ref="GH-104",
        environ={backup.ROOT_ACL_ENV_KEY: "GH-104"},
    ) == (backup.WINDOWS_ACL_REVIEWED, None)


def _credential_free_env(**extra: str) -> dict[str, str]:
    """자격증명만 뺀 환경. **환경을 통째로 비우지 않는다.**

    처음엔 `PATH`만 넘겼는데 native Windows에서 두 가지로 깨졌다(PR #108 run
    `32577793035`).

    - `SystemRoot`가 없어 `import _overlapped`(asyncio)가 `WinError 10106`으로 실패했다.
      SQLAlchemy가 asyncio를 import하므로 모듈 적재 자체가 안 됐다.
    - `PYTHONIOENCODING`이 없어 stdout이 cp1252가 됐고, `--help`의 `→`가
      `UnicodeEncodeError`를 냈다.

    둘 다 **테스트 환경 문제**였지 진입점 결함이 아니다. 목표는 "환경 없음"이 아니라
    "자격증명 없음"이므로 그 키만 덜어낸다 — 연결 0회는 그대로다.
    """

    env = {k: v for k, v in os.environ.items() if not _is_credential(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Windows 기본 콘솔 인코딩(cp1252)이 도움말의 non-ASCII를 못 찍는다.
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _is_credential(key: str) -> bool:
    return key.startswith(("POSTGRES_", "NEO4J_", "N8N_")) or "PASSWORD" in key


@pytest.mark.windows_contract
def test_the_cli_smoke_env_carries_no_credential() -> None:
    """자격증명을 걸러내지 못하면 subprocess가 공용 DB에 붙을 수 있다."""

    env = _credential_free_env()
    assert not [k for k in env if _is_credential(k)]
    assert env["PYTHONIOENCODING"] == "utf-8"
    # 남겨야 하는 것은 남는다. Windows `os.environ`은 키를 대문자로 정규화하므로
    # 대소문자를 구분하지 않고 본다.
    if os.name == "nt":  # pragma: no cover - Windows 전용 분기
        assert any(k.upper() == "SYSTEMROOT" for k in env)


@pytest.mark.windows_contract
@pytest.mark.parametrize(
    "script", ["backup_orchestrator.py", "transition_public_postgres.py"]
)
def test_cli_scripts_actually_run_as_entrypoints(script: str) -> None:
    """**subprocess로 실제 실행한다.** 문자열 검사로는 이번 결함을 못 잡는다.

    `backup_orchestrator.py`는 `__main__` 가드가 없어 스크립트로 돌리면 모듈만
    import되고 **아무 일 없이 exit 0**이었다. 격리 E2E가 `main(argv)`를 in-process로
    불러서 못 봤다(2026-08-22 공용 실행에서 발견). `inspect.getsource()`로 문자열만
    보면 주석이나 도달 불가 분기에 있어도 통과하므로 진입점을 직접 밟는다.

    `--help`는 argparse가 usage를 찍고 `SystemExit(0)`으로 끝난다. 진입점이 안 불리면
    stdout이 비고, 그것이 이번 결함의 정확한 증상이다. **DB에 닿지 않는다.**
    """

    import subprocess
    import sys as _sys

    completed = subprocess.run(
        [_sys.executable, str(SCRIPTS_ROOT / script), "--help"],
        capture_output=True,
        text=True,
        env=_credential_free_env(),
    )
    # `capture_output`이라도 플랫폼에 따라 `None`이 올 수 있어 정규화한다. 실패 시
    # 원인을 보려면 stderr가 메시지에 있어야 한다.
    out = completed.stdout or ""
    err = completed.stderr or ""
    assert completed.returncode == 0, err
    assert out.strip(), f"진입점이 불리지 않아 출력이 비었다 · stderr={err[:400]}"
    assert "usage:" in out, out[:400]
    for flag in ("--backup-root", "--change-ref"):
        assert flag in out, flag


@pytest.mark.windows_contract
def test_a_module_without_a_guard_would_be_caught(tmp_path: Path) -> None:
    """위 회귀가 실제로 결함을 잡는지 **결함 있는 사본으로** 확인한다.

    positive만 있으면 회귀가 무엇을 막는지 알 수 없다. 가드를 지운 사본은 출력이
    비어야 한다 — 그것이 2026-08-22에 공용에서 본 증상이다.
    """

    import subprocess
    import sys as _sys

    source = (SCRIPTS_ROOT / "backup_orchestrator.py").read_text(encoding="utf-8")
    broken = source.replace('if __name__ == "__main__":\n    sys.exit(main())', "")
    assert broken != source, "가드를 찾지 못했다"
    copy = tmp_path / "no_guard.py"
    copy.write_text(broken, encoding="utf-8")

    completed = subprocess.run(
        [_sys.executable, str(copy), "--help"],
        capture_output=True,
        text=True,
        env=_credential_free_env(PYTHONPATH=str(SCRIPTS_ROOT)),
    )
    # 가드가 없으면 조용히 성공한다 — exit 0인데 usage가 없다.
    assert completed.returncode == 0, completed.stderr or ""
    assert "usage:" not in (completed.stdout or "")
