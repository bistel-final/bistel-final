"""bootstrap runner가 공유하는 **epoch 중립** 기반 요소 (`V5-CM-3.2`).

## 왜 떼어냈나

`apply_agent_runtime`은 오래 `apply_reference_extensions`(V4)에서 이 함수들을
가져다 썼다. 그 중 `load_marker`·`postcheck_database` 둘은 V4 **계보**에 묶인
것이라 `V5-CM-3.2`가 끊었지만, 나머지는 계보와 무관한 배관이다.

작업계획 §4.7·§5·§7.1은 production `apply_agent_runtime`이 V4 module을 import하지
않는 것을 완료 조건으로 뒀다. 계보 심볼만 끊고 배관을 남기면 그 조건이 문언대로
충족되지 않는다(구현리뷰 필수 4). 배관을 여기로 옮기고 V4가 **재수출**하면 두 가지가
동시에 성립한다.

- `apply_agent_runtime`은 V4 module을 import하지 않는다
- V4의 기존 소비자(`verify_bootstrap_state`·`final_profile_manifests`·회귀)는 그대로다

예외 클래스도 함께 옮긴 이유는 **identity가 하나여야** 하기 때문이다. 양쪽이 각자
정의하면 `except ReferenceExtensionError`가 상대편 예외를 잡지 못한다.

`schema_lock`이 advisory lock key를 이미 이렇게 분리해 뒀다 — 같은 패턴이다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from db_target import BootstrapTarget, validate_url_components
from schema_lock import advisory_lock_key
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - native Windows only
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX only
    _msvcrt = None


# ---------------------------------------------------------------------------
# 물리 inventory — epoch가 아니라 **설계**가 정한다
# ---------------------------------------------------------------------------

#: `001_reference_extensions` 계보가 소유하는 table.
REFERENCE_TABLES = (
    "document",
    "document_chunk",
    "nl_query_log",
    "r03_alarm_history",
)
REFERENCE_VIEW = "v_alarm_event"
REFERENCE_OBJECTS = frozenset((*REFERENCE_TABLES, REFERENCE_VIEW))

#: 멘토 최종 `03_schema_clean.sql`이 만드는 base 9종.
BASE_TABLES = frozenset(
    {
        "action_history",
        "dim_parameter",
        "evaluation",
        "fdc_trace",
        "lot_history",
        "metrology",
        "summary_alarm_history",
        "summary_data",
        "trace_alarm_history",
    }
)

#: 팀 change approval 참조. Issue·PR 번호 형식만 받는다.
CHANGE_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")


# ---------------------------------------------------------------------------
# 예외 — 두 module이 **같은 객체**를 봐야 한다
# ---------------------------------------------------------------------------


class ReferenceExtensionError(RuntimeError):
    exit_code = 2
    default_reason_code = "CONTRACT_INVALID"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code or self.default_reason_code


class ReferenceStateError(ReferenceExtensionError):
    exit_code = 3
    default_reason_code = "SCHEMA_STATE_INVALID"


class ReferenceLockError(ReferenceExtensionError):
    exit_code = 4
    default_reason_code = "LOCK_UNAVAILABLE"


class ReferenceArtifactError(ReferenceExtensionError):
    exit_code = 5
    default_reason_code = "ARTIFACT_INVALID"


# ---------------------------------------------------------------------------
# 직렬화·해시
# ---------------------------------------------------------------------------


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _timezone_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReferenceArtifactError("artifact 시각은 timezone-aware여야 합니다")
    return value.isoformat()


def validate_change_reference(value: str | None) -> str:
    if not isinstance(value, str) or not CHANGE_REFERENCE_PATTERN.fullmatch(value):
        raise ReferenceExtensionError("change_reference 형식이 잘못됐습니다")
    return value


# ---------------------------------------------------------------------------
# catalog 응답
# ---------------------------------------------------------------------------


def _result_rows(result: Any) -> list[Mapping[str, Any]]:
    try:
        return list(result.mappings().all())
    except (AttributeError, TypeError) as exc:
        raise ReferenceStateError(
            "PostgreSQL catalog 응답 형식이 잘못됐습니다"
        ) from exc


def _single_row(result: Any, *, label: str) -> Mapping[str, Any]:
    rows = _result_rows(result)
    if len(rows) != 1:
        raise ReferenceStateError(f"{label} 응답 행 수가 잘못됐습니다")
    return rows[0]


# ---------------------------------------------------------------------------
# lock
# ---------------------------------------------------------------------------


def acquire_advisory_lock(connection: Any, database: str) -> None:
    namespace, database_id = advisory_lock_key(database)
    result = connection.exec_driver_sql(
        "SELECT pg_try_advisory_xact_lock(%s, %s) AS acquired",
        (namespace, database_id),
    )
    row = _single_row(result, label="advisory lock")
    if row.get("acquired") is not True:
        raise ReferenceLockError("다른 프로세스가 같은 DB schema를 변경 중입니다")


def _acquire_file_lock(file: BinaryIO) -> None:
    if sys.platform == "win32":
        if _msvcrt is None:
            raise OSError("Windows lock backend unavailable")
        file.seek(0)
        if file.read(1) != b"\0":
            file.seek(0)
            file.write(b"\0")
            file.flush()
        file.seek(0)
        _msvcrt.locking(file.fileno(), _msvcrt.LK_LOCK, 1)
    else:
        if _fcntl is None:
            raise OSError("POSIX lock backend unavailable")
        _fcntl.flock(file.fileno(), _fcntl.LOCK_EX)


def _release_file_lock(file: BinaryIO) -> None:
    if sys.platform == "win32":
        if _msvcrt is None:
            raise OSError("Windows lock backend unavailable")
        file.seek(0)
        _msvcrt.locking(file.fileno(), _msvcrt.LK_UNLCK, 1)
    else:
        if _fcntl is None:
            raise OSError("POSIX lock backend unavailable")
        _fcntl.flock(file.fileno(), _fcntl.LOCK_UN)


@contextmanager
def _exclusive_artifact_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ReferenceArtifactError("artifact lock에 symlink를 사용할 수 없습니다")
    import os

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        lock_file: BinaryIO = os.fdopen(descriptor, "a+b")
        _acquire_file_lock(lock_file)
    except OSError as exc:
        raise ReferenceArtifactError("artifact lock을 사용할 수 없습니다") from exc
    try:
        yield
    finally:
        try:
            _release_file_lock(lock_file)
        finally:
            lock_file.close()


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def _engine_for(target: BootstrapTarget) -> Engine:
    url = target.create_url()
    validate_url_components(url, target)
    return create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
