"""공용 전환 전 base 9 backup·격리 restore adapter(`V5-CM-2.6`).

이 모듈은 순수하다. 공용 DB에 **쓰지 않고**, 읽기도 하지 않는다. 주입받은 정보로
argv를 만들고 결과를 판정할 뿐이며 lifecycle은 `rehearsal_postgres.one_off_postgres`가
계속 소유한다(계획 §7).

세 가지를 강제한다.

1. **client/server major exact.** `pg_dump`는 자기보다 새로운 서버를 덤프하지 않는다.
   고정 image client는 16.15라 server major가 16이 아니면 dump 전에 멈춘다.
   이 검증이 없으면 실환경에서만 드러나는 실패가 된다(1차 계획리뷰 필수 1).
2. **secret은 child environment로만.** `PGPASSWORD`를 argv·stdout·receipt에 넣지 않는다.
3. **dump 대상은 base 9 allowlist뿐.** shell·임의 pattern·추가 option을 받지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

#: server major -> backup client image exact digest.
#: Gate 0에서 공용 3 DB가 모두 major 16임을 확인했다. floating tag·사용자 override·
#: 더 낮은 major fallback은 허용하지 않는다(계획 §7).
POSTGRES_BACKUP_CLIENT_IMAGES: Mapping[int, str] = MappingProxyType(
    {
        16: (
            "postgres@sha256:"
            "cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
        ),
    }
)

#: dump 대상. `rehearsal_profile_loader.LOAD_ORDER`와 같은 9종이며 이 밖은 받지 않는다.
BACKUP_TABLES: tuple[str, ...] = (
    "action_history",
    "dim_parameter",
    "evaluation",
    "fdc_trace",
    "lot_history",
    "metrology",
    "summary_alarm_history",
    "summary_data",
    "trace_alarm_history",
)

DUMP_OPTIONS: tuple[str, ...] = ("--format=custom", "--no-owner", "--no-privileges")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
VERSION_LINE = re.compile(r"\(PostgreSQL\)\s+(\d+)\.")
COMMAND_TIMEOUT_SECONDS = 900.0

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ErrorFactory = Callable[[str, int], BaseException]


class BackupError(RuntimeError):
    """backup·restore 실패. reason과 exit를 그대로 나른다."""

    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


@dataclass(frozen=True)
class BackupClient:
    """선택된 backup client image와 그 major."""

    major: int
    image: str


def select_backup_client(server_major: Any) -> BackupClient:
    """관측 server major에 대응하는 exact digest image를 고른다.

    mapping이 없는 것과 major가 어긋나는 것은 **다른 상황**이다. 전자는 운영자가
    digest를 먼저 pin해야 하는 선행 조건 누락(exit 3)이고, 후자는 현재 상태 불일치
    (exit 1)다. 하나로 뭉치면 운영자가 무엇을 해야 할지 알 수 없다(2차 계획리뷰 권장 1).
    """

    if not isinstance(server_major, int) or isinstance(server_major, bool):
        raise BackupError("BACKUP_INVALID", EXIT_MISMATCH)
    image = POSTGRES_BACKUP_CLIENT_IMAGES.get(server_major)
    if image is None:
        raise BackupError("BACKUP_CLIENT_UNAVAILABLE", EXIT_CONFIRM_REQUIRED)
    return BackupClient(major=server_major, image=image)


#: digest 고정 image가 실제로 내놓는 client version 문자열.
#: image가 digest로 고정돼 있으므로 이 값도 고정이다. receipt의 `backup_tool_version`을
#: 임의 문자열로 적을 수 없게 여기에 대조한다(구현리뷰 4차 필수 3).
POSTGRES_BACKUP_CLIENT_VERSIONS: Mapping[int, str] = MappingProxyType(
    {16: "pg_dump (PostgreSQL) 16.15"}
)


def expected_client_image(server_major: int) -> str:
    """이 major에 허용된 **유일한** image digest."""

    image = POSTGRES_BACKUP_CLIENT_IMAGES.get(server_major)
    if image is None:
        raise BackupError("BACKUP_CLIENT_UNAVAILABLE", EXIT_CONFIRM_REQUIRED)
    return image


def expected_client_version(server_major: int) -> str:
    """digest 고정 image에서 관측한 client version 문자열."""

    version = POSTGRES_BACKUP_CLIENT_VERSIONS.get(server_major)
    if version is None:
        raise BackupError("BACKUP_CLIENT_UNAVAILABLE", EXIT_CONFIRM_REQUIRED)
    return version


def validate_evidence_path(path: Path, *, trusted_root: Path) -> Path:
    """archive가 아닌 증적 파일에도 같은 경로 규칙을 적용한다.

    View 정의 sidecar를 경로만 보고 읽으면 저장소 밖 임의 파일을 증적으로 쓸 수 있다
    (구현리뷰 4차 필수 3).
    """

    return validate_archive_path(path, trusted_root=trusted_root)


def parse_client_major(version_output: str) -> int:
    """`pg_dump --version` / `pg_restore --version` 출력에서 major만 뽑는다."""

    match = VERSION_LINE.search(version_output or "")
    if match is None:
        raise BackupError("BACKUP_INVALID", EXIT_MISMATCH)
    return int(match.group(1))


def verify_client_major(
    client: BackupClient,
    *,
    dump_version: str,
    restore_version: str,
) -> None:
    """dump·restore **양쪽** client major가 server major와 exact인지 본다.

    dump만 확인하면 새 서버에서 만든 archive를 낮은 `pg_restore`로 되돌리지 못하는
    경우를 놓친다.
    """

    if parse_client_major(dump_version) != client.major:
        raise BackupError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)
    if parse_client_major(restore_version) != client.major:
        raise BackupError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)


def validate_backup_root(root: Path, *, repository_root: Path) -> Path:
    """backup root는 저장소 밖 절대경로여야 하고 경로에 symlink가 없어야 한다."""

    if not root.is_absolute():
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    resolved_repo = repository_root.resolve()
    try:
        root.resolve().relative_to(resolved_repo)
    except ValueError:
        pass
    else:
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    if not root.is_dir():
        raise BackupError("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED)
    return root


#: Windows에서 ACL을 사람이 확인했다는 **명시 입력**. 값은 그 실행의 `change_ref`여야
#: 한다. 코드가 스스로 "확인했다"를 만들면 그건 증적이 아니라 OS 종류를 다른 이름으로
#: 쓴 것이다(구현리뷰 17차 필수 2).
ROOT_ACL_ENV_KEY = "POSTGRES_BACKUP_ROOT_ACL_REVIEWED"
WINDOWS_ACL_REVIEWED = "WINDOWS_ACL_REVIEWED"
REQUIRED_ROOT_MODE = 0o700


def backup_root_trust(
    root: Path, *, change_ref: str, environ: Mapping[str, str] | None = None
) -> tuple[str, tuple[str, int] | None]:
    """backup root의 신뢰 상태를 측정한다. `(값, 거부사유 또는 None)`.

    root 쓰기 권한자는 completion·COMMITTED marker를 위조해 "이미 전환됨"을 만들 수 있고
    코드는 그걸 막지 못한다(계획 §16.1). 그래서 **첫 connector를 열기 전에** 소유자와
    mode를 본다. 전환이 끝난 뒤에 보면, 실행 중에는 열려 있다가 나중에 좁혀진 root도
    정상으로 기록된다(구현리뷰 17차 필수 2).

    호출자마다 예외 타입이 달라 여기서 raise하지 않는다. 경로 원문은 돌려주지 않는다.
    """

    source = os.environ if environ is None else environ
    if os.name == "nt":  # pragma: no cover - Windows 전용 분기
        reviewed = source.get(ROOT_ACL_ENV_KEY, "").strip()
        if not reviewed or reviewed != change_ref:
            return ("", ("BACKUP_ROOT_UNTRUSTED", EXIT_CONFIRM_REQUIRED))
        return (WINDOWS_ACL_REVIEWED, None)
    try:
        info = root.stat()
    except OSError:
        return ("", ("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED))
    mode = format(stat.S_IMODE(info.st_mode), "04o")
    if info.st_uid != os.getuid():
        # 다른 계정 소유면 그 계정이 증적을 갈아끼울 수 있다.
        return (mode, ("BACKUP_ROOT_UNTRUSTED", EXIT_MISMATCH))
    if stat.S_IMODE(info.st_mode) != REQUIRED_ROOT_MODE:
        return (mode, ("BACKUP_ROOT_UNTRUSTED", EXIT_MISMATCH))
    return (mode, None)


def dump_argv(
    *, database: str, tables: Sequence[str], out_path: str
) -> tuple[str, ...]:
    """`pg_dump` argv. secret은 들어가지 않고 table은 allowlist뿐이다."""

    if not IDENTIFIER.fullmatch(database):
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    if tuple(sorted(tables)) != tuple(sorted(BACKUP_TABLES)):
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    argv: list[str] = ["pg_dump", "--dbname", database, *DUMP_OPTIONS]
    for table in sorted(tables):
        if not IDENTIFIER.fullmatch(table):
            raise BackupError("BACKUP_INVALID", EXIT_USAGE)
        argv.append(f"--table=public.{table}")
    argv += ["--file", out_path]
    return tuple(argv)


def restore_argv(*, database: str, archive_path: str) -> tuple[str, ...]:
    if not IDENTIFIER.fullmatch(database):
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    return (
        "pg_restore",
        "--dbname",
        database,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        archive_path,
    )


#: child에 넘길 PG 접속 변수. 이 목록 밖의 `PG*`는 **상속하지 않는다**.
PG_CONNECTION_KEYS = ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD")


def child_environment(
    password: str,
    *,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    database: str | None = None,
    base: Mapping[str, str] | None = None,
) -> dict:
    """secret을 child environment로만 전달한다.

    **상위 env의 `PG*`를 물려받지 않는다.** 상속하면 우연히 설정된 `PGHOST` 때문에
    의도한 서버가 아닌 곳에 붙을 수 있다 — 공용 DB를 향할 위험이 있다
    (구현리뷰 10차 필수 1). 접속 대상은 인자로만 정한다.
    """

    source = os.environ if base is None else base
    child = {k: v for k, v in source.items() if not k.startswith("PG")}
    child["PGPASSWORD"] = password
    if host is not None:
        child["PGHOST"] = host
    if port is not None:
        child["PGPORT"] = str(port)
    if user is not None:
        child["PGUSER"] = user
    if database is not None:
        child["PGDATABASE"] = database
    return child


#: host에 닿는 방법은 플랫폼마다 다르다. `--network=host`는 Linux 계약이고 Docker
#: Desktop(macOS·Windows)에서는 버전·설정에 의존한다(구현리뷰 11차 필수 4).
HOST_GATEWAY_ALIAS = "host.docker.internal"


def host_transport(platform: str) -> tuple[str, ...]:
    """이 플랫폼에서 container가 host DB에 닿는 docker 인자.

    Linux는 host network를 그대로 쓰고, Desktop은 `host.docker.internal`을 gateway로
    매핑한다. 어느 쪽이든 `PGHOST`가 `localhost`/`127.0.0.1`이면 호출자가
    `rewrite_host()`로 바꿔야 한다.
    """

    if platform.startswith("linux"):
        return ("--network=host",)
    return ("--add-host", f"{HOST_GATEWAY_ALIAS}:host-gateway")


def rewrite_host(host: str, platform: str) -> str:
    """container 관점 host 이름으로 바꾼다.

    Desktop container 안의 `127.0.0.1`은 host가 아니라 그 container다. 격리 lifecycle이
    published port를 loopback으로 주므로 이 변환이 없으면 restore가 붙지 못한다.
    """

    if platform.startswith("linux"):
        return host
    if host in {"localhost", "127.0.0.1", "::1"}:
        return HOST_GATEWAY_ALIAS
    return host


def pinned_client_argv(
    argv: Sequence[str],
    *,
    image: str,
    child_env: Mapping[str, str],
    mounts: Mapping[str, str],
    platform: str | None = None,
) -> tuple[str, ...]:
    """digest 고정 image **안에서** client를 실행하는 argv를 만든다.

    host에 설치된 binary를 쓰면 receipt에 적은 image digest가 실제로 쓰이지 않은
    값이 된다(구현리뷰 10차 필수 1). 이 함수는 그 주장을 실행과 일치시킨다.

    - host 접근 방식은 플랫폼마다 다르다(`host_transport()`).
    - secret은 `--env PGPASSWORD`로만 넘기고 값은 argv에 넣지 않는다.
    - mount는 staging directory 하나뿐이고 container 경로는 고정 POSIX 경로다.
    """

    if image not in set(POSTGRES_BACKUP_CLIENT_IMAGES.values()):
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    import sys as _sys

    target = _sys.platform if platform is None else platform
    command: list[str] = ["docker", "run", "--rm", *host_transport(target)]
    for key in PG_CONNECTION_KEYS:
        if key in child_env:
            # 값은 여기서 전달하지 않는다. child process env에서 상속된다.
            command += ["--env", key]
    for host_path, container_path in sorted(mounts.items()):
        command += ["--volume", f"{host_path}:{container_path}"]
    command += ["--entrypoint", argv[0], image, *argv[1:]]
    return tuple(command)


def run_command(
    argv: Sequence[str],
    *,
    runner: CommandRunner,
    child_env: Mapping[str, str],
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    failure_reason: str = "INTERNAL_ERROR",
    failure_exit: int = EXIT_USAGE,
) -> subprocess.CompletedProcess[str]:
    """list argv·`shell=False`·timeout 고정. 실패는 호출자가 정한 reason으로 올린다.

    계획 §11의 reason 표에는 "dump 명령 자체가 실패했다"에 해당하는 항목이 없다.
    `BACKUP_INVALID`(exit 1)는 "유효한 receipt의 target·profile·hash·allowlist
    **불일치**"용이라 subprocess 죽음에 쓰면 통이 어긋난다. 그래서 기본값은
    `INTERNAL_ERROR`(exit 2, "예상 밖 내부 오류")로 두고, 분류가 있는 단계는
    호출자가 넘긴다 — restore 실패는 `RESTORE_NOT_VERIFIED`다.

    구현이 새 reason을 만들지 않는다. 운영자에게 더 세밀한 신호가 필요하면 그건
    계획 §11에 추가할 일이지 여기서 정할 일이 아니다.

    stdout/stderr는 절대 예외에 싣지 않는다. DSN·경로·SQL이 섞여 나올 수 있다.
    """

    completed = runner(
        list(argv),
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(child_env),
    )
    if completed.returncode != 0:
        raise BackupError(failure_reason, failure_exit)
    return completed


def validate_archive_path(path: Path, *, trusted_root: Path) -> Path:
    """archive가 symlink가 아닌 regular file이고 trusted root 안인지 본다.

    `is_file()`과 `open()`은 symlink를 따라간다. root 경로만 검사하면 archive 자체를
    저장소 밖 임의 파일로 연결할 수 있다(구현리뷰 3차 필수 2).
    """

    import stat

    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED) from exc
    # `lstat()`은 symlink를 따라가지 않으므로 symlink는 여기서 `S_IFLNK`다.
    # 따라서 regular file 판정 하나로 symlink·디렉터리·특수 파일을 모두 막는다.
    if not stat.S_ISREG(info.st_mode):
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)
    resolved_root = trusted_root.resolve()
    try:
        path.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise BackupError("BACKUP_INVALID", EXIT_USAGE) from exc
    return path


def archive_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 증적 producer — 원자적 쓰기(구현리뷰 7차 필수 2)
# ---------------------------------------------------------------------------


def atomic_write_json(
    path: Path, payload: Mapping[str, Any], *, trusted_root: Path
) -> str:
    """같은 디렉터리 임시 파일에 쓰고 fsync 후 rename한다.

    직접 쓰면 중간에 죽었을 때 **반쯤 쓰인 증적**이 남아 다음 실행이 그걸 믿는다.
    rename은 같은 파일 시스템 안에서 원자적이다.

    이미 있는 파일은 덮지 않는다. no-op 재실행이 mtime을 바꾸면 "변경 0"이 깨진다.
    """

    import json
    import os
    import tempfile

    resolved_root = trusted_root.resolve()
    try:
        path.resolve().parent.relative_to(resolved_root)
    except ValueError as exc:
        raise BackupError("BACKUP_INVALID", EXIT_USAGE) from exc
    if path.exists():
        raise BackupError("BACKUP_INVALID", EXIT_USAGE)

    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    fsync_directory(path.parent)
    return hashlib.sha256(body).hexdigest()


def fsync_directory(path: Path) -> None:
    """rename을 디스크에 확정한다. Windows는 디렉터리 fsync를 지원하지 않는다."""

    import os

    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
