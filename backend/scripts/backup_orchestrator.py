"""backup → 격리 restore → full verify → 증적 저장(`V5-CM-2.6`).

구현리뷰 8차·9차 필수 3이 지적한 공백을 메운다. 그전까지 `postgres_backup`은 argv와
digest helper였고, receipt의 `restore_verified`는 **인자로 받은 값**이었다. 즉 receipt가
주장하는 "독립 restore 성공"을 실제 실행 결과로 산출하지 않았다.

이 모듈이 그 흐름을 소유한다.

1. digest 고정 image의 `pg_dump`로 base 9만 덤프한다.
2. **격리** PostgreSQL 16에 `pg_restore`한다. 원본 DB는 읽기만 한다.
3. 복원본에서 legacy full fingerprint를 다시 계산해 대조한다 — 이게 통과해야만
   `restore_verified=True`다.
4. 검증을 통과한 경우에만 sidecar·receipt를 원자적으로 저장한다.

secret은 child environment로만 간다. DSN·host·port·user·password·행 값·절대경로를
receipt·sidecar·예외에 넣지 않는다.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import postgres_backup as backup  # noqa: E402
import postgres_transition as transition  # noqa: E402

EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

#: 검증 전 archive는 이 접미사를 달고 있다. 승격 전에는 receipt가 가리키지 않는다.
PARTIAL_SUFFIX = ".partial"

#: container 안에서 archive가 놓이는 고정 POSIX 경로. host 경로와 분리한다.
CONTAINER_BACKUP_DIR = "/backups"


@dataclass(frozen=True)
class SourceEndpoint:
    """덤프 대상. 이 모듈은 **읽기만** 한다.

    host·port·user를 명시한다. 없으면 상위 env의 `PG*`가 대상을 정하게 되어, 의도한
    서버가 아닌 곳에 붙을 수 있다(구현리뷰 10차 필수 1).
    """

    database: str
    password: str
    host: str
    port: int
    username: str


#: 격리 restore 대상을 여는 lifecycle. 기본은 일회성 container다.
RestoreLifecycle = Callable[..., Any]


class OrchestrationError(Exception):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def backup_and_verify(
    inventory: transition.TargetInventory,
    *,
    source: SourceEndpoint,
    change_ref: str,
    preflight_bundle_sha256: str,
    backup_root: Path,
    lifecycle: RestoreLifecycle,
    runner: backup.CommandRunner,
    reader: Callable[[Any, str, str], transition.TargetInventory],
) -> dict[str, Any]:
    """한 target의 backup·독립 restore·검증·증적 저장을 끝까지 수행한다.

    `restore_verified`는 인자가 아니라 **이 함수의 관측 결과**다.
    """

    with _own_evidence(backup_root, inventory.database, change_ref):
        return _backup_and_verify_owned(
            inventory,
            source=source,
            change_ref=change_ref,
            preflight_bundle_sha256=preflight_bundle_sha256,
            backup_root=backup_root,
            lifecycle=lifecycle,
            runner=runner,
            reader=reader,
        )


def _backup_and_verify_owned(
    inventory: transition.TargetInventory,
    *,
    source: SourceEndpoint,
    change_ref: str,
    preflight_bundle_sha256: str,
    backup_root: Path,
    lifecycle: RestoreLifecycle,
    runner: backup.CommandRunner,
    reader: Callable[[Any, str, str], transition.TargetInventory],
) -> dict[str, Any]:
    """소유권을 쥔 상태에서만 실행되는 본문."""

    client = backup.select_backup_client(inventory.server_major)
    import sys as _sys

    child_env = backup.child_environment(
        source.password,
        host=backup.rewrite_host(source.host, _sys.platform),
        port=source.port,
        user=source.username,
        database=source.database,
    )
    versions = {
        tool: backup.run_command(
            backup.pinned_client_argv(
                (tool, "--version"),
                image=client.image,
                child_env=child_env,
                # version 확인에는 mount가 필요 없다. host 절대경로를 container에
                # 붙이면 Windows에서 성립하지 않는다(구현리뷰 12차 필수 4).
                mounts={},
            ),
            runner=runner,
            child_env=child_env,
            failure_reason="BACKUP_CLIENT_UNAVAILABLE",
            failure_exit=EXIT_CONFIRM_REQUIRED,
        ).stdout.strip()
        for tool in ("pg_dump", "pg_restore")
    }
    backup.verify_client_major(
        client,
        dump_version=versions["pg_dump"],
        restore_version=versions["pg_restore"],
    )
    # 관측한 version이 digest 고정 image의 pin과 같아야 한다.
    expected_version = backup.expected_client_version(inventory.server_major)
    for tool, observed in versions.items():
        # major만 보면 patch가 다른 client도 통과한다. image가 digest로 고정됐으므로
        # 두 도구 모두 exact여야 한다(구현리뷰 10차 필수 1).
        if observed != expected_version.replace("pg_dump", tool):
            raise OrchestrationError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)

    archive = backup_root / transition.archive_name(inventory.database, change_ref)
    sidecar_path = backup_root / transition.view_sidecar_name(
        inventory.database, change_ref
    )
    receipt_path = backup_root / receipt_name(inventory.database, change_ref)
    complete_path = backup_root / completion_name(inventory.database, change_ref)
    published = [archive, sidecar_path, receipt_path]
    _reserve(published, complete_path)

    # **증적 전체를 staging에서 완성한 뒤 한 번에 게시한다.** archive만 먼저 승격하면
    # 그 뒤 sidecar·receipt 단계가 실패했을 때 archive만 남아 같은 change-ref 재시도가
    # `BACKUP_INVALID`로 봉쇄된다(구현리뷰 11차 필수 3).
    #
    # staging directory 이름에 execution id를 넣어, 같은 target을 동시에 실행해도 서로의
    # 작업 파일을 지우지 않는다.
    staging = backup_root / (
        f".staging.{inventory.database}.{change_ref}.{_execution_id()}"
    )
    staging.mkdir(parents=True)
    try:
        staged_archive = staging / archive.name
        backup.run_command(
            backup.pinned_client_argv(
                backup.dump_argv(
                    database=source.database,
                    tables=sorted(transition.BASE_TABLES),
                    out_path=container_path(staged_archive.name),
                ),
                image=client.image,
                child_env=child_env,
                mounts={str(staging.resolve()): CONTAINER_BACKUP_DIR},
            ),
            runner=runner,
            child_env=child_env,
        )
        backup.validate_archive_path(staged_archive, trusted_root=staging)
        assert_archive_contains_only_base(
            staged_archive,
            image=client.image,
            child_env=child_env,
            staging=staging,
            runner=runner,
        )
        archive_sha256 = backup.archive_digest(staged_archive)

        restored = restore_and_fingerprint(
            staged_archive,
            profile=inventory.profile,
            lifecycle=lifecycle,
            runner=runner,
            reader=reader,
            image=client.image,
            staging=staging,
        )
        # constraint 정의는 비교하지 않는다. base 9만 dump하면 범위 밖 table을 참조하는
        # FK가 복원될 수 없어 정상 backup도 실패한다. column shape·행 수·content를
        # 비교하는 것으로 복원 충실도는 충분히 확인된다.
        if restored["base_column_shape_sha256"] != (
            transition.base_column_shape_sha256(inventory)
        ):
            raise OrchestrationError("RESTORE_NOT_VERIFIED", EXIT_MISMATCH)
        if restored["base_content"] != dict(inventory.base_content):
            raise OrchestrationError("RESTORE_NOT_VERIFIED", EXIT_MISMATCH)
        if restored["base_rows"] != {
            name: inventory.row_counts.get(name) for name in transition.BASE_TABLES
        }:
            raise OrchestrationError("RESTORE_NOT_VERIFIED", EXIT_MISMATCH)

        state = transition.classify_target(inventory)
        sidecar_sha256 = backup.atomic_write_json(
            staging / sidecar_path.name,
            transition.build_sidecar(
                inventory,
                state=state,
                change_ref=change_ref,
                preflight_bundle_sha256=preflight_bundle_sha256,
            ),
            trusted_root=staging,
        )
        receipt = transition.build_receipt(
            inventory,
            change_ref=change_ref,
            preflight_bundle_sha256=preflight_bundle_sha256,
            archive_sha256=archive_sha256,
            view_sidecar_sha256=sidecar_sha256,
            restore_verified=True,
            backup_image_digest=client.image,
            backup_tool_version=versions["pg_dump"],
        )
        transition.validate_receipt(receipt)
        backup.atomic_write_json(
            staging / receipt_path.name, receipt, trusted_root=staging
        )
        # 세 파일이 모두 완성된 뒤에 게시한다. 게시 실패도 이 guard 안이다.
        _publish(
            staging,
            published,
            complete_path,
            database=inventory.database,
            change_ref=change_ref,
        )
    except BaseException:
        # 어떤 실패에서도 backup root에는 아무것도 남지 않는다. marker가 없으면 앞선
        # rename으로 놓인 파일도 정상 증적이 아니므로 함께 치운다.
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        # 다른 실행의 staging은 건드리지 않는다 — 이 directory는 이 execution 것뿐이다.
        _remove_tree(staging)

    return receipt


def assert_archive_contains_only_base(
    archive: Path,
    *,
    image: str,
    child_env: Mapping[str, str],
    staging: Path,
    runner: backup.CommandRunner,
) -> None:
    """archive object list가 base 9 allowlist와 exact인지 본다(계획 §7.2).

    dump argv가 옳아도 archive에 무엇이 들었는지는 따로 확인해야 한다. 지금까지
    `pg_restore --list` 검사가 없었다(구현리뷰 11차 필수 1).
    """

    completed = backup.run_command(
        backup.pinned_client_argv(
            ("pg_restore", "--list", container_path(archive.name)),
            image=image,
            child_env=child_env,
            mounts={str(staging.resolve()): CONTAINER_BACKUP_DIR},
        ),
        runner=runner,
        child_env=child_env,
        failure_reason="BACKUP_INVALID",
        failure_exit=EXIT_MISMATCH,
    )
    listed = archive_table_names(completed.stdout)
    if listed != set(transition.BASE_TABLES):
        raise OrchestrationError("BACKUP_INVALID", EXIT_MISMATCH)


#: archive에 허용하는 TOC entry type. 계획 §7.2의 "base 9 외 object 0"을 그대로
#: 구현하려면 table 이름만이 아니라 **object type**까지 봐야 한다(구현리뷰 14차 권장 1).
#:
#: 값은 고정 image의 실제 `pg_restore --list` 출력을 관측해 정했다. `--table=`로 base 9
#: 만 덤프해도 그 table에 딸린 CONSTRAINT·INDEX·COMMENT·FK entry가 함께 들어간다.
#: VIEW·FUNCTION·SEQUENCE·PROCEDURE 등은 들어가지 않으므로 거부 대상이다.
ALLOWED_ARCHIVE_ENTRIES = frozenset(
    {"TABLE", "TABLE DATA", "CONSTRAINT", "INDEX", "COMMENT", "FK CONSTRAINT"}
)

#: 이 중 relation 이름이 곧 base table인 entry만 allowlist 대조 대상이다.
TABLE_ARCHIVE_ENTRIES = frozenset({"TABLE", "TABLE DATA"})


def archive_entries(listing: str) -> list[tuple[str, str, str]]:
    """`pg_restore --list` 출력을 `(type, schema, name)`으로 편다."""

    entries: list[tuple[str, str, str]] = []
    for line in listing.splitlines():
        text = line.strip()
        if not text or text.startswith(";"):
            continue
        body = text.split(";", 1)[-1].strip()
        parts = body.split()
        # `<oid> <oid> TABLE public <name> <owner>` 또는
        # `<oid> <oid> TABLE DATA public <name> <owner>`
        # 허용 여부와 무관하게 **모든** entry를 편다. 여기서 걸러내면 호출자의 type
        # 판정이 죽은 방어가 된다(구현리뷰 14차 권장 1).
        for offset in (3, 2):
            if len(parts) <= offset + 2:
                continue
            kind = " ".join(parts[2 : offset + 1])
            if kind in ALLOWED_ARCHIVE_ENTRIES:
                entries.append((kind, parts[offset + 1], parts[offset + 2]))
                break
        else:
            if len(parts) > 4:
                entries.append((parts[2], parts[3], parts[4]))
            else:
                entries.append((parts[2] if len(parts) > 2 else "", "", ""))
    return entries


def archive_table_names(listing: str) -> set[str]:
    """archive가 담은 public table 이름. 허용 밖 object가 있으면 거부한다."""

    names: set[str] = set()
    for kind, schema, name in archive_entries(listing):
        if kind not in ALLOWED_ARCHIVE_ENTRIES:
            raise OrchestrationError("BACKUP_INVALID", EXIT_MISMATCH)
        if kind == "FK CONSTRAINT":
            # `FK CONSTRAINT`는 schema·name 위치가 한 칸 밀린 형태다.
            # 이름 대조 대상이 아니다.
            continue
        if schema != "public":
            raise OrchestrationError("BACKUP_INVALID", EXIT_MISMATCH)
        if kind in TABLE_ARCHIVE_ENTRIES:
            names.add(name)
    return names


def receipt_name(database: str, change_ref: str) -> str:
    return f"{transition.DATASET_EPOCH}.{database}.{change_ref}.receipt.json"


def container_path(name: str) -> str:
    """container 안 archive 경로. host 절대경로와 분리한다.

    host 경로를 그대로 container에 mount하면 Windows `C:\\...`가 Linux container 경로가
    될 수 없다(구현리뷰 11차 필수 4).
    """

    return f"{CONTAINER_BACKUP_DIR}/{name}"


def _execution_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


COMPLETION_ARTIFACT_TYPE = "postgres_backup_completion"

COMPLETION_KEYS = frozenset(
    {
        "artifact_type",
        "dataset_epoch",
        "database",
        "change_ref",
        "archive_sha256",
        "view_sidecar_sha256",
        "receipt_sha256",
    }
)

LOCK_SUFFIX = ".lock"


def completion_name(database: str, change_ref: str) -> str:
    """completion marker 이름. **이 파일이 있어야만** 정상 증적이다."""

    return f"{transition.DATASET_EPOCH}.{database}.{change_ref}.complete.json"


def lock_name(database: str, change_ref: str) -> str:
    return f".{transition.DATASET_EPOCH}.{database}.{change_ref}{LOCK_SUFFIX}"


LOCK_KEYS = frozenset(
    {"artifact_type", "dataset_epoch", "database", "change_ref", "token", "created_at"}
)

LOCK_ARTIFACT_TYPE = "postgres_backup_lock"

#: stale lock을 **자동으로** 훔치지 않는다. 운영자가 명시로 승인해야 한다.
STALE_LOCK_ENV_KEY = "POSTGRES_BACKUP_RECOVER_LOCK"


def validate_lock(payload: Any) -> None:
    """lock 파일이 우리가 만든 것인지 판정한다. secret은 담지 않는다."""

    if not isinstance(payload, Mapping) or set(payload) != LOCK_KEYS:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != LOCK_ARTIFACT_TYPE:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["dataset_epoch"] != transition.DATASET_EPOCH:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["database"] not in transition.TARGET_PROFILE:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in ("change_ref", "token"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    transition._require_offset_timestamp(payload["created_at"])


#: 해제된 lock. 파일을 **지우지 않고** 이 내용으로 바꾼다.
#:
#: 지우면 `read → unlink` 사이에 다른 실행이 새 lock을 만들 수 있고, 그러면 이전
#: 소유자·회수자가 남의 lock을 지운다(구현리뷰 16차 필수 2). 경로가 사라지지 않으므로
#: 그 경쟁 자체가 없다.
RELEASED_STATE = "RELEASED"
RELEASED_BODY = b'{"state": "RELEASED"}'


#: Windows `msvcrt.locking`은 POSIX `flock`과 달리 **강제(mandatory) lock**이다. 잠근
#: byte는 다른 프로세스가 **읽지도** 못한다. payload 안(offset 0)을 잠그면 살아 있는
#: lock을 `--inspect-lock`이 읽을 때 `PermissionError`로 죽는다 — 실제 Windows CI에서
#: 그렇게 실패했다(구현리뷰 19차 필수 2 검증).
#:
#: 그래서 **내용 밖의 byte 하나**만 잠근다. lock 파일은 수백 바이트라 이 offset은 항상
#: EOF 너머이고, Windows는 EOF 너머 잠금을 허용한다. POSIX `flock`은 파일 단위 advisory
#: 라 offset과 무관하므로 두 OS의 관측 동작이 같아진다.
_WINDOWS_LOCK_OFFSET = 1 << 20


def _advisory_lock(descriptor: int) -> bool:
    """OS advisory lock을 **비차단**으로 시도한다. 잡았으면 True.

    token 파일만으로는 소유자가 살아 있는지 알 수 없다. 커널은 안다 — 프로세스가
    강제 종료되면 자동으로 놓아준다. 그래서 "살아 있는 소유자"와 "죽은 소유자가 남긴
    기록"을 정확히 가른다.

    macOS·Linux는 `fcntl.flock`, Windows는 `msvcrt.locking`을 쓴다. 두 OS를 함께
    쓰는 팀이라 한쪽만 지원할 수 없다(구현리뷰 16차 필수 2).
    """

    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows 전용 분기
        import msvcrt

        try:
            os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        finally:
            # 이후 read/write는 내용 위치에서 한다.
            os.lseek(descriptor, 0, os.SEEK_SET)
        return True
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _advisory_unlock(descriptor: int) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows 전용 분기
        import msvcrt

        with contextlib.suppress(OSError):
            os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return
    with contextlib.suppress(OSError):
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _classify(raw: bytes, database: str, change_ref: str) -> dict[str, Any]:
    """lock 파일 내용만으로 상태를 판정한다. 파일을 열지 않는다."""

    import json

    if not raw.strip():
        return {"state": "empty", "token": None}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"state": "malformed", "token": None}
    if isinstance(payload, Mapping) and payload.get("state") == RELEASED_STATE:
        return {"state": "absent", "token": None}
    try:
        validate_lock(payload)
    except OrchestrationError:
        return {"state": "malformed", "token": None}
    if payload["database"] != database or payload["change_ref"] != change_ref:
        return {"state": "malformed", "token": None}
    return {"state": "valid", "token": payload["token"]}


def read_lock(backup_root: Path, database: str, change_ref: str) -> dict[str, Any]:
    """lock 상태를 판정한다. **깨진 lock도 상태로 돌려준다.**

    강제 종료는 파일을 만든 직후에도, JSON을 쓰는 중에도 일어난다. schema parse 실패로
    예외를 던지면 그 lock은 영원히 회수할 수 없다(구현리뷰 15차 필수 2).

    `live`는 **지금 그 lock을 쥔 프로세스가 있는지**다. 운영자의 2인 확인이 실제로
    답해야 하는 질문이 이것이라, 내용 상태와 따로 돌려준다(구현리뷰 16차 필수 2).
    이 함수는 상태를 바꾸지 않는다.
    """

    path = backup_root / lock_name(database, change_ref)
    if not path.is_file():
        return {"state": "absent", "token": None, "live": False}
    status = _classify(path.read_bytes(), database, change_ref)
    descriptor = os.open(path, os.O_RDWR)
    try:
        held = _advisory_lock(descriptor)
        if held:
            _advisory_unlock(descriptor)
    finally:
        os.close(descriptor)
    status["live"] = not held
    return status


#: 깨진 lock에는 token이 없다. 운영자가 이 값을 대신 지정한다.
MALFORMED_LOCK_TOKEN = "malformed"


def recover_stale_lock(
    backup_root: Path,
    database: str,
    change_ref: str,
    *,
    token: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """강제 종료가 남긴 lock을 **명시 승인으로만** 회수한다.

    시간 만료로 자동 회수하면 살아 있는 다른 실행의 lock을 훔친다. 그래서 운영자가
    lock 상태를 확인해 환경변수로 지정해야 한다(구현리뷰 14차 필수 3).

    유효한 lock은 그 `token`을, 0-byte·부분 기록된 lock은 `MALFORMED_LOCK_TOKEN`을
    지정한다. 어느 쪽이든 승인 없이는 건드리지 않는다(구현리뷰 15차 필수 2).

    회수는 advisory lock을 **잡은 상태에서** 이뤄진다. 못 잡으면 그 실행은 아직
    살아 있다는 뜻이라 승인이 있어도 거부한다 — 운영자의 "죽은 것이 맞다"는 판단이
    틀렸을 때 살아 있는 backup을 깨뜨리지 않게 한다. 잡은 뒤에는 다른 실행이 진입할
    수 없으므로 판정과 해제 사이에 경쟁이 없다(구현리뷰 16차 필수 2).

    이 함수는 lock 상태만 바꾼다. 다른 실행의 staging·final 증적은 건드리지 않는다.
    """

    source = os.environ if environ is None else environ
    path = backup_root / lock_name(database, change_ref)
    if not path.is_file():
        return "absent"
    descriptor = os.open(path, os.O_RDWR)
    try:
        if not _advisory_lock(descriptor):
            # 아직 살아 있는 소유자가 있다. 승인이 있어도 뺏지 않는다.
            raise OrchestrationError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            status = _classify(
                os.read(descriptor, _LOCK_READ_LIMIT), database, change_ref
            )
            if status["state"] == "absent":
                return "absent"
            expected = status["token"] or MALFORMED_LOCK_TOKEN
            if token != expected:
                raise OrchestrationError("BACKUP_INVALID", EXIT_MISMATCH)
            if source.get(STALE_LOCK_ENV_KEY, "").strip() != token:
                # 승인 없이는 회수하지 않는다.
                raise OrchestrationError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
            _write_state(descriptor, RELEASED_BODY)
            return str(status["state"])
        finally:
            _advisory_unlock(descriptor)
    finally:
        os.close(descriptor)


#: lock 파일은 수백 바이트다. 이 이상은 우리가 쓴 것이 아니다.
_LOCK_READ_LIMIT = 65536

#: 소유권 주장이 **없는** 상태. advisory lock을 잡은 실행이 그대로 가져갈 수 있다.
#:
#: `empty`는 파일을 만든 직후 죽었거나 우리가 방금 만든 것이다. 어느 쪽이든 그 실행은
#: 아무 증적도 publish하지 못했고(publish는 payload를 쓴 뒤에만 일어난다) advisory
#: lock이 비어 있으니 살아 있지도 않다. 승인을 요구할 근거가 없다.
_FREE_LOCK_STATES = frozenset({"absent", "empty"})


def _write_state(descriptor: int, body: bytes) -> None:
    """advisory lock을 쥔 채 내용을 통째로 바꾼다. 경로는 그대로 둔다."""

    os.lseek(descriptor, 0, os.SEEK_SET)
    os.truncate(descriptor, 0)
    os.write(descriptor, body)
    os.fsync(descriptor)


@contextlib.contextmanager
def _own_evidence(backup_root: Path, database: str, change_ref: str) -> Iterator[None]:
    """target·change-ref 단위 **배타 소유권**을 잡는다.

    `_reserve()`만으로는 부족하다. A가 세 파일을 rename한 뒤 marker를 쓰기 직전에 B가
    들어오면 B가 A의 파일을 지우고, A는 정상 marker를 남긴다 — marker는 있는데 파일이
    없는 상태가 된다(구현리뷰 13차 필수 2).

    소유권 primitive는 **OS advisory lock**이다. `O_CREAT|O_EXCL` + token 파일만 쓰면
    해제가 `read token → unlink` 두 단계라, 그 사이에 다른 실행이 새 lock을 만들면
    남의 lock을 지운다(구현리뷰 16차 필수 2). advisory lock은 소유 기간 내내 커널이
    들고 있어서 그 사이 진입 자체가 불가능하고, 강제 종료되면 커널이 자동으로 놓는다.

    lock 파일은 **한 번 만들면 지우지 않는다.** 해제는 `RELEASED_BODY`로 내용을 바꾸는
    것이다. 경로가 사라지지 않으므로 "경로가 다른 파일로 바뀌는" 경쟁이 없다.

    advisory lock을 잡았는데 내용이 남의 claim이면 그 실행은 **죽은** 것이다. 그래도
    자동으로 뺏지 않는다 — 운영자가 `recover_stale_lock()`으로 명시 승인해야 한다
    (구현리뷰 14차 필수 3의 정책을 그대로 둔다).
    """

    import json

    path = backup_root / lock_name(database, change_ref)
    payload = {
        "artifact_type": LOCK_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": database,
        "change_ref": change_ref,
        "token": _execution_id(),
        "created_at": _now_offset(),
    }
    validate_lock(payload)
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not _advisory_lock(descriptor):
            # 살아 있는 소유자가 있다. 파일은 건드리지 않는다.
            raise OrchestrationError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        status = _classify(os.read(descriptor, _LOCK_READ_LIMIT), database, change_ref)
        if status["state"] not in _FREE_LOCK_STATES:
            # 죽은 실행이 남긴 claim이다. 승인 회수를 거쳐야 한다.
            raise OrchestrationError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
        _write_state(descriptor, body)
        try:
            yield None
        finally:
            # advisory lock을 **쥔 채로** 해제 상태를 쓴다. 이 사이에 다른 실행이
            # 새 lock을 잡는 것이 불가능하므로 남의 lock을 덮어쓸 수 없다.
            _write_state(descriptor, RELEASED_BODY)
    finally:
        _advisory_unlock(descriptor)
        os.close(descriptor)


def _now_offset() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).astimezone().isoformat()


def validate_completion(payload: Any) -> None:
    """completion marker schema. 소비자가 이 형식만 신뢰한다."""

    if not isinstance(payload, Mapping) or set(payload) != COMPLETION_KEYS:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != COMPLETION_ARTIFACT_TYPE:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["dataset_epoch"] != transition.DATASET_EPOCH:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["database"] not in transition.TARGET_PROFILE:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    if not isinstance(payload["change_ref"], str) or not payload["change_ref"]:
        raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in ("archive_sha256", "view_sidecar_sha256", "receipt_sha256"):
        value = payload[key]
        if not isinstance(value, str) or not transition.SHA256_HEX.fullmatch(value):
            raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)


def _reserve(published: Sequence[Path], complete_path: Path) -> None:
    """자리를 잡는다. 정상 set은 지키고, 미완성 잔재는 치운다.

    소유권(`_own_evidence`) 안에서만 부른다. 밖에서 부르면 다른 실행의 파일을 지운다.
    """

    if complete_path.exists():
        # 완결된 증적이다. 절대 덮지 않는다.
        raise OrchestrationError("BACKUP_INVALID", EXIT_USAGE)
    for path in published:
        # marker가 없으므로 중단된 실행의 잔재다. 소유권을 쥔 상태에서만 지운다.
        path.unlink(missing_ok=True)


def _publish(
    staging: Path,
    targets: Sequence[Path],
    complete_path: Path,
    *,
    database: str,
    change_ref: str,
) -> None:
    """staging의 완성본을 최종 이름으로 옮기고 **마지막에** marker를 게시한다."""

    for target in targets:
        if not (staging / target.name).is_file():
            raise OrchestrationError("BACKUP_INVALID", EXIT_USAGE)
    for target in targets:
        (staging / target.name).replace(target)
    backup.fsync_directory(targets[0].parent)
    payload = {
        "artifact_type": COMPLETION_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": database,
        "change_ref": change_ref,
        "archive_sha256": backup.archive_digest(targets[0]),
        "view_sidecar_sha256": backup.archive_digest(targets[1]),
        "receipt_sha256": backup.archive_digest(targets[2]),
    }
    validate_completion(payload)
    backup.atomic_write_json(complete_path, payload, trusted_root=complete_path.parent)


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def restore_and_fingerprint(
    archive: Path,
    *,
    profile: str,
    lifecycle: RestoreLifecycle,
    runner: backup.CommandRunner,
    reader: Callable[[Any, str, str], transition.TargetInventory],
    image: str,
    staging: Path,
) -> dict[str, Any]:
    """archive를 **격리** PostgreSQL에 복원하고 base 9 identity를 다시 만든다.

    복원 대상은 일회성이다. 공용 DB에 restore하지 않는다. lifecycle이 준
    host·port·user를 **그대로** 쓴다 — 버리면 상위 env가 대상을 정한다(10차 필수 1).
    """

    with lifecycle(database="fdc_restore_verify") as endpoint:
        import sys as _sys

        restore_env = backup.child_environment(
            endpoint.password,
            host=backup.rewrite_host(endpoint.host, _sys.platform),
            port=endpoint.port,
            user=endpoint.username,
            database=endpoint.database,
        )
        completed = runner(
            list(
                backup.pinned_client_argv(
                    backup.restore_argv(
                        database=endpoint.database,
                        archive_path=container_path(Path(archive).name),
                    ),
                    image=image,
                    child_env=restore_env,
                    mounts={str(staging.resolve()): CONTAINER_BACKUP_DIR},
                )
            ),
            shell=False,
            capture_output=True,
            text=True,
            env=restore_env,
            timeout=backup.COMMAND_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise OrchestrationError("RESTORE_NOT_VERIFIED", EXIT_MISMATCH)
        inventory = reader(endpoint, endpoint.database, profile)

    return {
        "base_column_shape_sha256": transition.base_column_shape_sha256(inventory),
        "base_content": dict(inventory.base_content),
        "base_rows": {
            name: inventory.row_counts.get(name) for name in transition.BASE_TABLES
        },
    }


# ---------------------------------------------------------------------------
# 진입점 — 검증 전에는 아무 연결도 열지 않는다(구현리뷰 10차 필수 4)
# ---------------------------------------------------------------------------

SOURCE_ENV_KEYS = (
    "POSTGRES_TRANSITION_HOST",
    "POSTGRES_TRANSITION_PORT",
    "POSTGRES_TRANSITION_USER",
    "POSTGRES_TRANSITION_PASSWORD",
)

#: DSN 4종에 더해 **허용 endpoint 지정**까지 있어야 접속을 만든다(구현리뷰 16차 필수 1).
REQUIRED_ENV_KEYS = SOURCE_ENV_KEYS + (transition.ALLOWED_ENDPOINT_ENV_KEY,)


def source_from_environment(
    database: str, environ: Mapping[str, str] | None = None
) -> SourceEndpoint:
    """접속 정보를 환경변수에서만 읽는다. 값은 어디에도 직렬화하지 않는다."""

    import os

    source = os.environ if environ is None else environ
    if database not in transition.TARGET_PROFILE:
        raise OrchestrationError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    missing = [key for key in SOURCE_ENV_KEYS if not source.get(key, "").strip()]
    if missing:
        raise OrchestrationError("APPROVAL_REQUIRED", EXIT_CONFIRM_REQUIRED)
    host = source["POSTGRES_TRANSITION_HOST"].strip()
    port = _port(source["POSTGRES_TRANSITION_PORT"])
    # engine·subprocess를 만들기 **전에** 서버 신원을 고정한다. 잘못된 `.env`가 다른
    # PostgreSQL을 가리키면 여기서 connector 0회로 끝난다(구현리뷰 16차 필수 1).
    rejection = transition.endpoint_rejection(host, port, environ=source)
    if rejection is not None:
        raise OrchestrationError(*rejection)
    return SourceEndpoint(
        database=database,
        password=source["POSTGRES_TRANSITION_PASSWORD"].strip(),
        host=host,
        port=port,
        username=source["POSTGRES_TRANSITION_USER"].strip(),
    )


def _port(raw: str) -> int:
    """port를 sanitized reason으로 정규화한다.

    `int()`를 그대로 쓰면 `not-a-port`가 uncaught `ValueError`로 새어나가 JSON reason도
    exit code도 없이 죽는다(구현리뷰 11차 권장 1). transition session과 **같은**
    parser를 써서 두 진입점의 판정이 갈리지 않게 한다(구현리뷰 17차 필수 3).
    """

    rejection = transition.port_rejection(raw)
    if rejection is not None:
        raise OrchestrationError(*rejection)
    value = transition.parse_port(raw)
    assert value is not None  # port_rejection이 이미 걸렀다
    return value


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--change-ref", required=True)
    # lock 명령은 이 값이 필요 없다. 필수 여부를 `_select_mode()`가 판정한다.
    parser.add_argument("--preflight-bundle-sha256")
    parser.add_argument("--confirm-target", action="append", default=[])
    parser.add_argument("--archive-image-check", action="store_true")
    # stale lock 회수 전용. backup을 수행하지 않는다.
    parser.add_argument("--inspect-lock", action="store_true")
    parser.add_argument("--recover-lock")
    parser.add_argument("--database")
    return parser


def _lock_command(args: Any, environ: Mapping[str, str] | None) -> int:
    """lock 상태 조회·회수. **backup을 수행하지 않고 DB에 연결하지 않는다.**

    운영자가 강제 종료 뒤 이 명령으로 상태를 보고, 승인 환경변수를 준 뒤 회수한다
    (구현리뷰 15차 필수 2).
    """

    import json

    try:
        if args.database not in transition.TARGET_PROFILE:
            raise OrchestrationError("TARGET_NOT_ALLOWED", EXIT_USAGE)
        if not args.change_ref:
            raise OrchestrationError("ARG_INVALID", EXIT_USAGE)
        root = backup.validate_backup_root(
            Path(args.backup_root), repository_root=SCRIPTS_ROOT.parents[1]
        )
        if args.inspect_lock:
            status = read_lock(root, args.database, args.change_ref)
            print(json.dumps({"status": "OK", **status}))
            return 0
        state = recover_stale_lock(
            root,
            args.database,
            args.change_ref,
            token=args.recover_lock,
            environ=environ,
        )
    except (OrchestrationError, backup.BackupError) as exc:
        print(json.dumps({"reason_code": exc.reason_code, "status": "FAILED"}))
        return exc.exit_code
    print(json.dumps({"status": "RECOVERED", "state": state}))
    return 0


def _default_lifecycle(**kwargs: Any) -> Any:
    import rehearsal_postgres

    return rehearsal_postgres.one_off_postgres(**kwargs)


def _default_inspector(environ: Mapping[str, str] | None) -> Any:
    """공용 read-only session factory. 자격증명이 없으면 첫 호출에서 멈춘다."""

    import transition_sessions

    return lambda database: transition_sessions.read_only_session(
        database, environ=environ
    )


def default_restore_reader(
    endpoint: Any, database: str, profile: str
) -> transition.TargetInventory:
    """격리 복원본을 실제로 읽는다.

    지금까지 reader는 주입 전용이었고 운영 기본값이 없었다(구현리뷰 12차 필수 1).
    """

    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    # 문자열로 이으면 `p@ss/word` 같은 비밀번호가 host·database로 잘려 들어간다.
    # 값을 출력하는 문제 이전에 **틀린 endpoint로 가는** 운영 결함이다
    # (구현리뷰 13차 필수 3).
    url = URL.create(
        "postgresql+psycopg",
        username=endpoint.username,
        password=endpoint.password,
        host=endpoint.host,
        port=int(endpoint.port),
        database=endpoint.database,
    )
    engine = create_engine(url, isolation_level="REPEATABLE READ", hide_parameters=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                return transition.read_inventory(
                    connection,
                    database=database,
                    profile=profile,
                    require_snapshot=True,
                )
    finally:
        engine.dispose()


def main(
    argv: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    inspector: Any = None,
    reader: Any = None,
    lifecycle: RestoreLifecycle | None = None,
    runner: backup.CommandRunner | None = None,
) -> int:
    """세 target의 backup·독립 restore·증적 생성을 고정 순서로 수행한다.

    `--confirm-target`은 `ORDERED_TARGETS`와 **정확히** 같아야 한다. 전환 CLI와 같은
    규칙이라 부분집합·순서 변경·중복을 전부 거부한다.
    """

    import json
    import subprocess

    args = _parser().parse_args(argv)
    if args.inspect_lock or args.recover_lock:
        return _lock_command(args, environ)
    try:
        if tuple(args.confirm_target) != transition.ORDERED_TARGETS:
            raise OrchestrationError("CONFIRM_REQUIRED", EXIT_CONFIRM_REQUIRED)
        root = backup.validate_backup_root(
            Path(args.backup_root),
            repository_root=SCRIPTS_ROOT.parents[1],
        )
        # 증적을 쓸 곳이 신뢰할 수 있는지 **연결 전에** 본다(구현리뷰 17차 필수 2).
        _, rejection = backup.backup_root_trust(
            root, change_ref=args.change_ref, environ=environ
        )
        if rejection is not None:
            raise OrchestrationError(*rejection)
        if not args.preflight_bundle_sha256 or not transition.SHA256_HEX.fullmatch(
            args.preflight_bundle_sha256
        ):
            raise OrchestrationError("ARTIFACT_INVALID", EXIT_USAGE)
        # 여기까지 통과해야 첫 연결이 열린다.
        sources = {
            database: source_from_environment(database, environ)
            for database in transition.ORDERED_TARGETS
        }
        # 운영 기본값을 여기서 만든다. 자격증명이 없으면 첫 호출에서 멈춘다.
        inspector = inspector or _default_inspector(environ)
        reader = reader or default_restore_reader

        import transition_public_postgres as cli

        inventories = cli.collect_inventories(cli.ConnectionLedger(), inspector)
        bundle = cli.preflight_report(inventories)["bundle_sha256"]
        if bundle != args.preflight_bundle_sha256:
            raise OrchestrationError("APPROVAL_MISMATCH", EXIT_MISMATCH)

        for database in transition.ORDERED_TARGETS:
            backup_and_verify(
                inventories[database],
                source=sources[database],
                change_ref=args.change_ref,
                preflight_bundle_sha256=bundle,
                backup_root=Path(args.backup_root),
                lifecycle=lifecycle or _default_lifecycle,
                runner=runner or subprocess.run,
                reader=reader,
            )
    except (OrchestrationError, backup.BackupError) as exc:
        print(json.dumps({"reason_code": exc.reason_code, "status": "FAILED"}))
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        # 연결 실패 같은 하위 예외가 그대로 새어나가면 JSON reason도 exit code도 없다.
        # stdout에 DSN·host가 섞여 나올 수 있으므로 내용은 싣지 않는다.
        import transition_sessions

        reason = getattr(exc, "reason_code", None)
        if reason is None:
            reason = (
                "APPROVAL_REQUIRED"
                if isinstance(exc, transition_sessions.SessionError)
                else "INTERNAL_ERROR"
            )
        print(json.dumps({"reason_code": reason, "status": "FAILED"}))
        return getattr(exc, "exit_code", EXIT_USAGE)
    print(json.dumps({"status": "OK", "targets": len(transition.ORDERED_TARGETS)}))
    return 0
