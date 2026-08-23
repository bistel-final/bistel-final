"""`V5-CM-2.6` backup·restore adapter 단위 테스트.

DB·Docker를 쓰지 않는다. argv·버전 판정·경로 경계만 고정한다. Windows에서 갈라지는
계약(드라이브 절대경로·저장소 내부·symlink·argv 인용)이 여기 있으므로 대부분
`windows_contract`로 표시해 Windows job이 실제로 수집한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import postgres_backup as backup  # noqa: E402

REPOSITORY_ROOT = SCRIPTS_ROOT.parents[1]


# ---------------------------------------------------------------------------
# client / server major
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_gate0_major_16_selects_pinned_digest() -> None:
    client = backup.select_backup_client(16)
    assert client.major == 16
    assert client.image.startswith("postgres@sha256:")
    assert ":" in client.image and "latest" not in client.image


@pytest.mark.windows_contract
def test_unregistered_major_is_precondition_not_mismatch() -> None:
    """digest를 pin해야 하는 상태(exit 3)와 major가 어긋난 상태(exit 1)는 다르다.

    한 코드로 묶으면 운영자가 exit만 보고 무엇을 해야 할지 알 수 없다
    (2차 계획리뷰 권장 1).
    """

    with pytest.raises(backup.BackupError) as caught:
        backup.select_backup_client(17)
    assert caught.value.reason_code == "BACKUP_CLIENT_UNAVAILABLE"
    assert caught.value.exit_code == backup.EXIT_CONFIRM_REQUIRED


@pytest.mark.windows_contract
@pytest.mark.parametrize("value", [None, "16", 16.0, True])
def test_non_integer_major_is_invalid(value: Any) -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.select_backup_client(value)
    assert caught.value.reason_code == "BACKUP_INVALID"


@pytest.mark.windows_contract
def test_matching_dump_and_restore_majors_pass() -> None:
    client = backup.select_backup_client(16)
    backup.verify_client_major(
        client,
        dump_version="pg_dump (PostgreSQL) 16.15",
        restore_version="pg_restore (PostgreSQL) 16.15",
    )


@pytest.mark.windows_contract
@pytest.mark.parametrize(
    ("dump", "restore"),
    [
        ("pg_dump (PostgreSQL) 15.9", "pg_restore (PostgreSQL) 16.15"),
        ("pg_dump (PostgreSQL) 16.15", "pg_restore (PostgreSQL) 15.9"),
        ("pg_dump (PostgreSQL) 17.2", "pg_restore (PostgreSQL) 17.2"),
    ],
)
def test_either_client_major_mismatch_is_rejected(dump: str, restore: str) -> None:
    """dump만 보면 새 서버 archive를 낮은 `pg_restore`로 못 되돌리는 경우를 놓친다."""

    client = backup.select_backup_client(16)
    with pytest.raises(backup.BackupError) as caught:
        backup.verify_client_major(client, dump_version=dump, restore_version=restore)
    assert caught.value.reason_code == "BACKUP_CLIENT_VERSION_MISMATCH"
    assert caught.value.exit_code == backup.EXIT_MISMATCH


@pytest.mark.windows_contract
@pytest.mark.parametrize("output", ["", "pg_dump 16.15", "garbage"])
def test_unparsable_version_is_invalid(output: str) -> None:
    with pytest.raises(backup.BackupError):
        backup.parse_client_major(output)


# ---------------------------------------------------------------------------
# backup root 경계 — Windows에서 갈라진다
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_absolute_root_outside_repository_is_accepted(tmp_path: Path) -> None:
    assert backup.validate_backup_root(tmp_path, repository_root=REPOSITORY_ROOT)


@pytest.mark.windows_contract
def test_relative_root_is_rejected() -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_backup_root(Path("backups"), repository_root=REPOSITORY_ROOT)
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert caught.value.exit_code == backup.EXIT_USAGE


@pytest.mark.windows_contract
def test_root_inside_repository_is_rejected() -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_backup_root(
            REPOSITORY_ROOT / "backups", repository_root=REPOSITORY_ROOT
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


@pytest.mark.windows_contract
def test_missing_root_is_precondition(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_backup_root(
            tmp_path / "absent", repository_root=REPOSITORY_ROOT
        )
    assert caught.value.reason_code == "BACKUP_REQUIRED"
    assert caught.value.exit_code == backup.EXIT_CONFIRM_REQUIRED


@pytest.mark.windows_contract
def test_symlinked_component_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    try:
        (tmp_path / "linked").symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - 플랫폼 의존
        pytest.skip("이 플랫폼에서는 symlink를 만들 수 없다")
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_backup_root(
            tmp_path / "linked", repository_root=REPOSITORY_ROOT
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


# ---------------------------------------------------------------------------
# argv — secret 금지, allowlist만
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_dump_argv_has_no_secret_and_only_allowlisted_tables() -> None:
    argv = backup.dump_argv(
        database="kosa_agent", tables=backup.BACKUP_TABLES, out_path="/out/a.dump"
    )
    assert argv[0] == "pg_dump"
    assert all(isinstance(part, str) for part in argv)
    table_args = [a for a in argv if a.startswith("--table=")]
    assert len(table_args) == 9
    assert table_args == sorted(table_args)
    assert all(a.startswith("--table=public.") for a in table_args)
    for forbidden in ("PGPASSWORD", "password", "--dbname=", ";", "|", "&&"):
        assert not any(forbidden in a for a in argv)


@pytest.mark.windows_contract
@pytest.mark.parametrize(
    "tables",
    [
        (),
        ("action_history",),
        (*backup.BACKUP_TABLES, "document"),
        ("action_history; DROP TABLE audit_log", *backup.BACKUP_TABLES[1:]),
    ],
)
def test_dump_argv_rejects_table_sets_outside_allowlist(
    tables: tuple[str, ...],
) -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.dump_argv(database="kosa_agent", tables=tables, out_path="/out/a.dump")
    assert caught.value.reason_code == "BACKUP_INVALID"


@pytest.mark.windows_contract
@pytest.mark.parametrize("database", ["", "Kosa_Agent", "kosa agent", "kosa;drop"])
def test_argv_rejects_non_identifier_database(database: str) -> None:
    with pytest.raises(backup.BackupError):
        backup.dump_argv(
            database=database, tables=backup.BACKUP_TABLES, out_path="/out/a.dump"
        )
    with pytest.raises(backup.BackupError):
        backup.restore_argv(database=database, archive_path="/out/a.dump")


@pytest.mark.windows_contract
def test_child_environment_carries_secret_and_argv_does_not() -> None:
    child = backup.child_environment("s3cret", base={"PATH": "/usr/bin"})
    assert child["PGPASSWORD"] == "s3cret"
    assert child["PATH"] == "/usr/bin"
    argv = backup.dump_argv(
        database="kosa_agent", tables=backup.BACKUP_TABLES, out_path="/out/a.dump"
    )
    assert all("s3cret" not in part for part in argv)


@pytest.mark.windows_contract
def test_run_command_uses_list_argv_without_shell() -> None:
    seen: dict[str, Any] = {}

    def runner(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    backup.run_command(
        ("pg_dump", "--version"), runner=runner, child_env={"PGPASSWORD": "x"}
    )
    assert isinstance(seen["argv"], list)
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["timeout"] > 0
    assert seen["kwargs"]["env"]["PGPASSWORD"] == "x"


def _failing_runner(argv: Any, **_: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 1, "", "host=db.internal password=hunter2")


@pytest.mark.windows_contract
def test_failed_command_defaults_to_internal_error() -> None:
    """계획 §11에 "dump 실행 실패" reason이 없다. 구현이 만들지 않는다.

    `BACKUP_INVALID`(exit 1)는 내용 불일치용이라 subprocess 죽음에 쓰면 통이 어긋난다.
    """

    with pytest.raises(backup.BackupError) as caught:
        backup.run_command(
            ("pg_dump", "--version"),
            runner=_failing_runner,
            child_env={"PGPASSWORD": "x"},
        )
    assert caught.value.reason_code == "INTERNAL_ERROR"
    assert caught.value.exit_code == backup.EXIT_USAGE


@pytest.mark.windows_contract
def test_caller_supplies_reason_where_classification_exists() -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.run_command(
            ("pg_restore", "--version"),
            runner=_failing_runner,
            child_env={"PGPASSWORD": "x"},
            failure_reason="RESTORE_NOT_VERIFIED",
            failure_exit=backup.EXIT_MISMATCH,
        )
    assert caught.value.reason_code == "RESTORE_NOT_VERIFIED"
    assert caught.value.exit_code == backup.EXIT_MISMATCH


@pytest.mark.windows_contract
def test_command_output_never_reaches_the_exception() -> None:
    """stderr에는 host·credential이 섞여 나올 수 있다."""

    with pytest.raises(backup.BackupError) as caught:
        backup.run_command(
            ("pg_dump", "--version"),
            runner=_failing_runner,
            child_env={"PGPASSWORD": "x"},
        )
    rendered = str(caught.value)
    for forbidden in ("hunter2", "db.internal", "host=", "password="):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# 구현리뷰 3차 필수 2 — archive 경로 경계
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_regular_archive_inside_root_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "a.dump"
    path.write_bytes(b"dump")
    assert backup.validate_archive_path(path, trusted_root=tmp_path) == path


@pytest.mark.windows_contract
def test_symlinked_archive_is_rejected(tmp_path: Path) -> None:
    """`is_file()`·`open()`은 symlink를 따라간다. root만 검사하면 부족하다."""

    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"secret")
    root = tmp_path / "backups"
    root.mkdir()
    link = root / "a.dump"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - 플랫폼 의존
        pytest.skip("이 플랫폼에서는 symlink를 만들 수 없다")
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_archive_path(link, trusted_root=root)
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert outside.read_bytes() == b"secret"


@pytest.mark.windows_contract
def test_symlink_inside_root_is_still_rejected(tmp_path: Path) -> None:
    """root 안을 가리키는 symlink는 경로 포함 검사로는 안 잡힌다.

    `S_ISLNK` 판정이 없으면 통과한다 — 두 검사가 서로를 가려주지 않도록 격리한다.
    """

    root = tmp_path / "backups"
    root.mkdir()
    real = root / "real.dump"
    real.write_bytes(b"dump")
    link = root / "a.dump"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - 플랫폼 의존
        pytest.skip("이 플랫폼에서는 symlink를 만들 수 없다")
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_archive_path(link, trusted_root=root)
    assert caught.value.reason_code == "BACKUP_INVALID"


@pytest.mark.windows_contract
def test_regular_file_outside_root_is_rejected(tmp_path: Path) -> None:
    """symlink가 아니어도 root 밖 파일은 거부한다."""

    root = tmp_path / "backups"
    root.mkdir()
    outside = tmp_path / "a.dump"
    outside.write_bytes(b"dump")
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_archive_path(outside, trusted_root=root)
    assert caught.value.reason_code == "BACKUP_INVALID"


@pytest.mark.windows_contract
def test_missing_archive_is_precondition(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_archive_path(tmp_path / "absent.dump", trusted_root=tmp_path)
    assert caught.value.reason_code == "BACKUP_REQUIRED"
    assert caught.value.exit_code == backup.EXIT_CONFIRM_REQUIRED


@pytest.mark.windows_contract
def test_directory_named_like_archive_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.dump"
    path.mkdir()
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_archive_path(path, trusted_root=tmp_path)
    assert caught.value.reason_code == "BACKUP_INVALID"


# ---------------------------------------------------------------------------
# 구현리뷰 4차 필수 3 — image·version 고정 값
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_expected_client_image_is_the_pinned_digest() -> None:
    import postgres_transition as transition

    image = backup.expected_client_image(16)
    assert image == backup.POSTGRES_BACKUP_CLIENT_IMAGES[16]
    # receipt 형식 검사와 같은 규칙을 만족해야 대조 자체가 성립한다.
    assert transition.IMAGE_DIGEST.match(image)


@pytest.mark.windows_contract
def test_expected_client_version_matches_the_pinned_image() -> None:
    """digest가 고정이므로 version 문자열도 고정이다.

    이 값은 고정 image에서 `pg_dump --version`을 실제로 실행해 관측했다.
    """

    version = backup.expected_client_version(16)
    assert version == "pg_dump (PostgreSQL) 16.15"
    assert backup.parse_client_major(version) == 16


@pytest.mark.windows_contract
@pytest.mark.parametrize("major", [9, 14, 15, 17, 18])
def test_unpinned_major_has_no_image_or_version(major: int) -> None:
    for call in (backup.expected_client_image, backup.expected_client_version):
        with pytest.raises(backup.BackupError) as caught:
            call(major)
        assert caught.value.reason_code == "BACKUP_CLIENT_UNAVAILABLE"
        assert caught.value.exit_code == backup.EXIT_CONFIRM_REQUIRED


@pytest.mark.windows_contract
def test_evidence_path_uses_the_same_rule_as_the_archive(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    good = root / "evidence.json"
    good.write_text("{}", encoding="utf-8")
    assert backup.validate_evidence_path(good, trusted_root=root) == good

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(backup.BackupError) as caught:
        backup.validate_evidence_path(outside, trusted_root=root)
    assert caught.value.reason_code == "BACKUP_INVALID"


# ---------------------------------------------------------------------------
# 구현리뷰 7차 필수 2 — 증적은 원자적으로 쓰고 덮지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_atomic_write_refuses_to_overwrite_an_existing_artifact(tmp_path: Path) -> None:
    """no-op 재실행이 mtime을 바꾸면 "변경 0"이 깨진다."""

    root = tmp_path / "root"
    root.mkdir()
    path = root / "marker.json"
    digest = backup.atomic_write_json(path, {"a": 1}, trusted_root=root)
    assert len(digest) == 64
    stamp = path.stat().st_mtime_ns

    with pytest.raises(backup.BackupError) as caught:
        backup.atomic_write_json(path, {"a": 2}, trusted_root=root)
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert path.stat().st_mtime_ns == stamp
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


@pytest.mark.windows_contract
def test_atomic_write_stays_inside_the_trusted_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(backup.BackupError) as caught:
        backup.atomic_write_json(tmp_path / "outside.json", {"a": 1}, trusted_root=root)
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert not (tmp_path / "outside.json").exists()


@pytest.mark.windows_contract
def test_atomic_write_leaves_no_partial_file_when_it_fails(tmp_path: Path) -> None:
    """중간에 죽으면 반쯤 쓰인 증적이 남아 다음 실행이 그걸 믿는다."""

    root = tmp_path / "root"
    root.mkdir()

    class _Unserializable:
        pass

    with pytest.raises(TypeError):
        backup.atomic_write_json(
            root / "marker.json", {"a": _Unserializable()}, trusted_root=root
        )
    assert list(root.iterdir()) == []
