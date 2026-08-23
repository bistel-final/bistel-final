"""격리 rehearsal의 재실행·복구 계약(`V5-CM-2.5`).

이 모듈은 순수하다. Docker lifecycle·CLI·engine을 소유하지 않고, 이미 열린
connection과 이미 검증된 acceptance만 받는다. lifecycle과 CLI는
`rehearse_recovery.py`가 소유한다(계획 §7.2).

세 축을 구현한다.

1. **상태 판정** — handler에서는 catalog 집합만 보고 `ADOPTED_CANDIDATE`를 고른다.
   실제 `ADOPTED` 승격은 2.4 full acceptance가 postcheck에서 한 번 통과한 뒤다.
   같은 검사를 handler와 postcheck에서 두 번 돌리지 않는다(계획 §5.1).
2. **marker 계약** — exact key set·타입·hex·provenance·fingerprint를 검증하고,
   같은 디렉터리 임시 파일을 fsync한 뒤 `os.replace()`하는 marker-last 저장을 한다.
3. **cross-platform lock** — `fcntl`/`msvcrt` 조건부 adapter. `V5-CM-1.6`이 삭제한
   `build_corrected_dataset.py`와 독립이며 그것을 import하지 않는다.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3  # noqa: E402
import rehearsal_profile_verifier as verifier  # noqa: E402

EXIT_MISMATCH = 1
EXIT_USAGE = 2

MARKER_FORMAT_VERSION = 1
MARKER_ARTIFACT_TYPE = "postgres_profile_marker"
MARKER_STATUS = "COMMITTED"
DATASET_EPOCH = verifier.DATASET_EPOCH

MARKER_KEYS = frozenset(
    {
        "format_version",
        "artifact_type",
        "status",
        "dataset_epoch",
        "profile",
        "database",
        "logical_targets",
        "source_archive_sha256",
        "source_manifest_sha256",
        "schema_sha256",
        "table_fingerprints",
        "live_db_fingerprint_sha256",
    }
)
FINGERPRINT_KEYS = frozenset({"row_count", "content_hash"})
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# marker root에서 marker와 lock을 이름으로 분리한다. `marker 1` 계산은 항상
# `*.json`만 센다(계획리뷰 2차 권장 1).
MARKER_SUFFIX = ".json"
LOCK_SUFFIX = ".lock"

ErrorFactory = Callable[[str, int], BaseException]

# POSIX 전용 flag다. Windows에는 없으므로 참조 자체가 AttributeError가 된다
# (구현리뷰 1차 필수 1). 없으면 0으로 떨어뜨리고 symlink 방어는 경로 검사로 한다.
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# Windows는 디렉터리 fd를 열 수 없다. 시도조차 하지 않는다.
DIRECTORY_FSYNC_SUPPORTED = os.name != "nt"


class MarkerPathError(OSError):
    """marker·lock 경로가 신뢰 경계를 벗어났다.

    `OSError` 하위라 `RecoverySession.post_commit()`의 저장 실패 처리가 그대로
    `ARTIFACT_WRITE_FAILED`로 외부화한다.
    """


def reject_symlinked_path(path: Path, trusted_root: Path) -> None:
    """`trusted_root` **아래**의 모든 경로 component가 symlink가 아님을 확인한다.

    terminal component만 보면 `linked-parent -> real-parent` 아래의 평범한
    디렉터리를 root로 넘기는 것을 막지 못한다(구현리뷰 1차 필수 3).

    `trusted_root` 자신은 호출자가 만들었거나 신뢰한다고 선언한 지점이라 검사하지
    않는다. macOS의 `/var -> /private/var`처럼 플랫폼이 소유한 symlink를 오탐하지
    않으려면 이 경계가 필요하다.
    """

    try:
        relative = path.relative_to(trusted_root)
    except ValueError as exc:
        raise MarkerPathError(f"신뢰 경계 밖 경로입니다: {path.name}") from exc
    current = trusted_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise MarkerPathError(f"경로에 symlink가 있습니다: {part}")


def fsync_directory(path: Path) -> None:
    """rename 자체를 durable하게 만든다. 지원하는 플랫폼에서만 수행한다."""

    if not DIRECTORY_FSYNC_SUPPORTED:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class DbState(StrEnum):
    FRESH = "FRESH"
    ADOPTED_CANDIDATE = "ADOPTED_CANDIDATE"
    PARTIAL_OR_DRIFT = "PARTIAL_OR_DRIFT"


class MarkerState(StrEnum):
    MISSING = "MISSING"
    VALID = "VALID"
    INVALID = "INVALID"
    MISMATCH = "MISMATCH"


class Outcome(StrEnum):
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    RECOVER = "RECOVER"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class MarkerIdentity:
    """marker payload를 만들 provenance. 전부 이미 검증된 값이다."""

    profile: str
    database: str
    logical_targets: tuple[str, ...]
    source_archive_sha256: str
    source_manifest_sha256: str
    schema_sha256: str
    table_fingerprints: Mapping[str, Mapping[str, Any]] = MappingProxyType({})


def table_fingerprints(
    acceptances: Sequence[verifier.TableAcceptance], profile: str
) -> dict[str, dict[str, Any]]:
    """profile projection을 적용한 table 이름 오름차순 fingerprint(계획 §6.1).

    runtime의 `action_history`는 source 12행이 아니라 0행·empty canonical hash다.
    """

    result: dict[str, dict[str, Any]] = {}
    for entry in sorted(acceptances, key=lambda item: item.name):
        row_count, content_hash = entry.expected_for(profile)
        result[entry.name] = {"row_count": row_count, "content_hash": content_hash}
    return result


def live_db_fingerprint(
    *,
    dataset_epoch: str,
    profile: str,
    schema_sha256: str,
    fingerprints: Mapping[str, Mapping[str, Any]],
) -> str:
    """marker/DB 상태를 한 값으로 비교하는 aggregate(계획 §6.1).

    `hash_canonical_rows()`가 key 정렬·공백 없는 UTF-8 JSON을 고정한다. nested 값은
    ASCII allowlist(table 이름·정수·hex)뿐이라 같은 source/profile이면 결정론적이다.
    full acceptance를 **대체하는** 신뢰 근거로 쓰지 않는다.
    """

    row = {
        "dataset_epoch": dataset_epoch,
        "profile": profile,
        "schema_sha256": schema_sha256,
        "table_fingerprints": {
            name: dict(entry) for name, entry in sorted(fingerprints.items())
        },
    }
    return manifest_v3.hash_canonical_rows([row])


def build_marker(identity: MarkerIdentity) -> dict[str, Any]:
    """timestamp 없는 결정론 payload. 복구본이 최초본과 byte-identical해진다."""

    fingerprints = {
        name: dict(entry) for name, entry in sorted(identity.table_fingerprints.items())
    }
    payload = {
        "format_version": MARKER_FORMAT_VERSION,
        "artifact_type": MARKER_ARTIFACT_TYPE,
        "status": MARKER_STATUS,
        "dataset_epoch": DATASET_EPOCH,
        "profile": identity.profile,
        "database": identity.database,
        "logical_targets": list(identity.logical_targets),
        "source_archive_sha256": identity.source_archive_sha256,
        "source_manifest_sha256": identity.source_manifest_sha256,
        "schema_sha256": identity.schema_sha256,
        "table_fingerprints": fingerprints,
        "live_db_fingerprint_sha256": live_db_fingerprint(
            dataset_epoch=DATASET_EPOCH,
            profile=identity.profile,
            schema_sha256=identity.schema_sha256,
            fingerprints=fingerprints,
        ),
    }
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def marker_bytes(payload: Mapping[str, Any]) -> bytes:
    """저장 형태를 한 곳에 고정한다. 복구 byte-identity의 근거다."""

    rendered = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return unicodedata.normalize("NFC", rendered).encode("utf-8") + b"\n"


# ---------------------------------------------------------------------------
# marker validator
# ---------------------------------------------------------------------------


def _hex(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_HEX.fullmatch(value))


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_marker_payload(payload: Any, expected: Mapping[str, Any]) -> MarkerState:
    """구조 오류와 내용 불일치를 서로 다른 상태로 나눈다(계획 §5.2).

    `INVALID`는 형식이 깨진 것이고 `MISMATCH`는 형식은 옳은데 이 DB·source의
    marker가 아닌 것이다. 두 경우 모두 절대 덮어쓰지 않는다.
    """

    if not isinstance(payload, Mapping) or set(payload) != MARKER_KEYS:
        return MarkerState.INVALID
    if payload["format_version"] != MARKER_FORMAT_VERSION:
        return MarkerState.INVALID
    if payload["artifact_type"] != MARKER_ARTIFACT_TYPE:
        return MarkerState.INVALID
    if payload["status"] != MARKER_STATUS:
        return MarkerState.INVALID
    for key in ("profile", "database", "dataset_epoch"):
        if not isinstance(payload[key], str) or not payload[key]:
            return MarkerState.INVALID
    targets = payload["logical_targets"]
    if not isinstance(targets, list) or not targets:
        return MarkerState.INVALID
    if not all(
        isinstance(name, str) and IDENTIFIER.fullmatch(name) for name in targets
    ):
        return MarkerState.INVALID
    if len(set(targets)) != len(targets):
        return MarkerState.INVALID
    for key in (
        "source_archive_sha256",
        "source_manifest_sha256",
        "schema_sha256",
        "live_db_fingerprint_sha256",
    ):
        if not _hex(payload[key]):
            return MarkerState.INVALID
    fingerprints = payload["table_fingerprints"]
    if not isinstance(fingerprints, Mapping) or not fingerprints:
        return MarkerState.INVALID
    for name, entry in fingerprints.items():
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            return MarkerState.INVALID
        if not isinstance(entry, Mapping) or set(entry) != FINGERPRINT_KEYS:
            return MarkerState.INVALID
        if not _count(entry["row_count"]) or not _hex(entry["content_hash"]):
            return MarkerState.INVALID

    # 형식이 옳은 marker만 provenance를 비교한다.
    if payload["dataset_epoch"] != DATASET_EPOCH:
        return MarkerState.MISMATCH
    for key in (
        "profile",
        "database",
        "source_archive_sha256",
        "source_manifest_sha256",
        "schema_sha256",
    ):
        if payload[key] != expected[key]:
            return MarkerState.MISMATCH
    if list(targets) != list(expected["logical_targets"]):
        return MarkerState.MISMATCH
    if {n: dict(e) for n, e in fingerprints.items()} != {
        n: dict(e) for n, e in expected["table_fingerprints"].items()
    }:
        return MarkerState.MISMATCH
    if payload["live_db_fingerprint_sha256"] != expected["live_db_fingerprint_sha256"]:
        return MarkerState.MISMATCH
    return MarkerState.VALID


# ---------------------------------------------------------------------------
# cross-platform lock
# ---------------------------------------------------------------------------

try:  # pragma: no cover - platform 분기
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform 분기
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover
    _msvcrt = None  # type: ignore[assignment]


class LockUnavailableError(RuntimeError):
    """다른 프로세스가 같은 marker root를 잡고 있다."""


@contextlib.contextmanager
def marker_lock(
    root: Path, profile: str, *, trusted_root: Path | None = None
) -> Iterator[None]:
    """marker 판정 전부터 post-commit 저장 완료까지 유지하는 배타 lock.

    lock 파일은 marker와 같은 root에 두되 확장자로 분리한다. marker 개수를 세는
    검증은 `*.json`만 세므로 lock 파일이 계수를 흔들지 않는다.
    """

    lock_path = root / f"{profile}{LOCK_SUFFIX}"
    reject_symlinked_path(lock_path, trusted_root or Path(root.anchor))
    with open(lock_path, "a+b") as handle:  # noqa: PTH123 - fileno가 필요하다
        _acquire(handle)
        try:
            yield None
        finally:
            _release(handle)


def _acquire(handle: Any) -> None:
    if _fcntl is not None:
        try:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            raise LockUnavailableError from exc
        return
    if _msvcrt is not None:  # pragma: no cover - Windows CI에서만 실행
        # 빈 파일에는 range lock을 걸 수 없다. 저장소의 기존 adapter와 같이
        # 1 byte를 먼저 보장한다(구현리뷰 1차 필수 1).
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise LockUnavailableError from exc
        return
    raise LockUnavailableError  # pragma: no cover


def _release(handle: Any) -> None:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:  # pragma: no cover - Windows CI에서만 실행
        handle.seek(0)
        with contextlib.suppress(OSError):
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)


# ---------------------------------------------------------------------------
# marker store
# ---------------------------------------------------------------------------


class MarkerStore:
    """marker-last 저장소. symlink를 거부하고 원자 교체만 한다."""

    def __init__(
        self, root: Path, profile: str, *, trusted_root: Path | None = None
    ) -> None:
        self._root = root
        self._path = root / f"{profile}{MARKER_SUFFIX}"
        # 호출자가 만들었거나 신뢰한다고 선언한 지점. 기본값은 filesystem root라
        # 모든 component를 검사한다. 이 경우 호출자가 root를 미리 `resolve()`해야
        # 플랫폼 소유 symlink(macOS `/var`)에서 오탐하지 않는다.
        self._trusted_root = trusted_root or Path(root.anchor)

    @property
    def path(self) -> Path:
        return self._path

    def markers(self) -> list[Path]:
        return sorted(p for p in self._root.glob(f"*{MARKER_SUFFIX}") if p.is_file())

    def read(self) -> tuple[MarkerState, Any]:
        """symlink·비정상 경로는 읽기 전에 `INVALID`로 닫는다."""

        try:
            reject_symlinked_path(self._path, self._trusted_root)
        except MarkerPathError:
            return MarkerState.INVALID, None
        if not self._path.exists():
            return MarkerState.MISSING, None
        if not self._path.is_file():
            return MarkerState.INVALID, None
        try:
            payload = json.loads(self._path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return MarkerState.INVALID, None
        return MarkerState.VALID, payload

    def save(self, payload: Mapping[str, Any]) -> None:
        """같은 디렉터리 임시 파일 → fsync → `os.replace()` → 디렉터리 durability.

        **실패를 보고하면 marker는 남지 않는다.** replace 이후 durability 단계가
        실패하면 방금 놓은 marker를 되돌린다. 그러지 않으면 `ARTIFACT_WRITE_FAILED`를
        보고하고도 valid marker가 남아, 다음 명시 복구가 `RECOVERY_NOT_ALLOWED`로
        막히는 막다른 상태가 된다(구현리뷰 1차 필수 2).
        """

        reject_symlinked_path(self._path, self._trusted_root)
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        reject_symlinked_path(temporary, self._trusted_root)
        payload_bytes = marker_bytes(payload)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, self._path)
        try:
            fsync_directory(self._root)
        except OSError:
            self._path.unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# DB 상태 판정 (catalog만 본다)
# ---------------------------------------------------------------------------

RELATIONS_SQL = (
    "SELECT c.relname AS name, c.relkind AS kind "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f','i','I')"
)


def classify_db_state(
    connection: Any, expected_tables: Sequence[str], expected_indexes: Sequence[str]
) -> DbState:
    """catalog 집합만으로 후보를 고른다. 값 검증은 postcheck 몫이다(계획 §5.1).

    여기서 2.4 full acceptance를 돌리면 postcheck와 중복 실행이 된다. 그래서 이
    함수는 relation 이름 집합만 본다.
    """

    from sqlalchemy import text

    rows = [
        dict(row) for row in connection.execute(text(RELATIONS_SQL)).mappings().all()
    ]
    if not rows:
        return DbState.FRESH
    tables = {str(r["name"]) for r in rows if str(r["kind"]) in {"r", "p"}}
    indexes = {str(r["name"]) for r in rows if str(r["kind"]) in {"i", "I"}}
    other = {str(r["name"]) for r in rows if str(r["kind"]) not in {"r", "p", "i", "I"}}
    if other:
        return DbState.PARTIAL_OR_DRIFT
    if tables != set(expected_tables):
        return DbState.PARTIAL_OR_DRIFT
    if not set(expected_indexes) <= indexes:
        return DbState.PARTIAL_OR_DRIFT
    return DbState.ADOPTED_CANDIDATE


# ---------------------------------------------------------------------------
# RecoverySession
# ---------------------------------------------------------------------------


class RecoverySession:
    """호출마다 새로 만든다. module global mutable state를 두지 않는다.

    handler → postcheck → post-commit hook 세 콜백을 한 세션이 소유해서, outcome이
    한 실행 안에서만 흐르게 한다.
    """

    def __init__(
        self,
        *,
        store: MarkerStore,
        identity: MarkerIdentity,
        acceptances: Sequence[verifier.TableAcceptance],
        profile: str,
        expected_tables: Sequence[str],
        expected_indexes: Sequence[str],
        fresh_handler: Callable[[Any, Any], None],
        acceptance_postcheck: Callable[[Any, Any], None],
        error_factory: ErrorFactory,
        recover_artifact: bool = False,
    ) -> None:
        self._store = store
        self._acceptances = tuple(acceptances)
        self._profile = profile
        self._expected_tables = tuple(expected_tables)
        self._expected_indexes = tuple(expected_indexes)
        self._fresh_handler = fresh_handler
        self._acceptance_postcheck = acceptance_postcheck
        self._fail = error_factory
        self._recover = recover_artifact
        self._identity = MarkerIdentity(
            profile=identity.profile,
            database=identity.database,
            logical_targets=identity.logical_targets,
            source_archive_sha256=identity.source_archive_sha256,
            source_manifest_sha256=identity.source_manifest_sha256,
            schema_sha256=identity.schema_sha256,
            table_fingerprints=MappingProxyType(
                table_fingerprints(acceptances, profile)
            ),
        )
        self._expected = build_marker(self._identity)
        self._outcome: Outcome | None = None

    @property
    def outcome(self) -> Outcome | None:
        return self._outcome

    @property
    def expected_marker(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self._expected))

    def handler(self, connection: Any, plan: Any) -> None:
        db_state = classify_db_state(
            connection, self._expected_tables, self._expected_indexes
        )
        marker_state, payload = self._store.read()
        if marker_state is MarkerState.VALID:
            marker_state = validate_marker_payload(payload, self._expected)

        if marker_state is MarkerState.INVALID:
            raise self._fail("ARTIFACT_INVALID", EXIT_USAGE)
        if marker_state is MarkerState.MISMATCH:
            raise self._fail("ARTIFACT_MISMATCH", EXIT_MISMATCH)

        if db_state is DbState.PARTIAL_OR_DRIFT:
            raise self._fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)

        if db_state is DbState.FRESH:
            if marker_state is MarkerState.VALID:
                # 값이 맞는 marker가 비어 있는 DB를 가리킨다 — 덮어쓰지 않는다.
                raise self._fail("ARTIFACT_MISMATCH", EXIT_MISMATCH)
            if self._recover:
                raise self._fail("RECOVERY_NOT_ALLOWED", EXIT_MISMATCH)
            self._outcome = Outcome.APPLIED
            self._fresh_handler(connection, plan)
            return

        # 여기부터 ADOPTED_CANDIDATE. DDL·COPY를 부르지 않는다.
        if marker_state is MarkerState.VALID:
            if self._recover:
                raise self._fail("RECOVERY_NOT_ALLOWED", EXIT_MISMATCH)
            self._outcome = Outcome.NOOP
            return
        self._outcome = Outcome.RECOVER if self._recover else Outcome.RECOVERY_REQUIRED

    def postcheck(self, connection: Any, plan: Any) -> None:
        if self._outcome is None:
            raise self._fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        # 어떤 outcome이든 2.4 full acceptance를 정확히 한 번 통과해야 한다.
        self._acceptance_postcheck(connection, plan)
        if self._outcome is Outcome.RECOVERY_REQUIRED:
            # 검증에 성공했으므로 drift가 아니라 marker 유실이다. 그 사실을 확정한
            # 뒤에 거부해야 진단이 성립한다(계획 §7.2).
            raise self._fail("RECOVERY_REQUIRED", EXIT_MISMATCH)

    def post_commit(self, _connection: Any, _plan: Any) -> None:
        if self._outcome is Outcome.NOOP:
            return
        if self._outcome not in {Outcome.APPLIED, Outcome.RECOVER}:
            raise self._fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        try:
            self._store.save(self._expected)
        except OSError as exc:
            # commit은 이미 끝났다. 되돌릴 수 없으므로 숨기지 않고 실패시킨다.
            raise self._fail("ARTIFACT_WRITE_FAILED", EXIT_USAGE) from exc
