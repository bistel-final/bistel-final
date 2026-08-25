"""LangGraph checkpoint 저장소 one-shot 초기화 admin runner (`V5-CM-3.4`).

## 이 runner가 존재하는 이유

`PostgresSaver.setup()`은 **transaction으로 감쌀 수 없다** — `MIGRATIONS` 9개 중 3개가
`CREATE INDEX CONCURRENTLY`다. `V5-CM-3.2`·`V5-CM-3.3`처럼 실패 시 rollback할 수 없고,
중간에 죽으면 부분 적용이 남는다.

더 나쁜 것은 **재실행이 그것을 낫게 하지 않는다**는 점이다. `setup()`은
`checkpoint_migrations`의 최대 `v`만 보고 그 다음부터 실행하므로, version이 이미 8이면
index가 사라져도 아무 문장도 실행하지 않는다.

```text
setup() 완료 → index 3개 valid
index 하나 DROP
setup() 재실행 → 복구 안 됨 (version 8 그대로)
```

그래서 **`setup()`이 해주지 않는 postcheck가 이 runner의 본체**다.

## app startup과 분리한다

`.setup()`은 이 runner의 apply 경로에서만 호출한다. 앱은 이미 초기화된 saver를
사용하며, 미초기화 DB를 시작 시 몰래 고치는 fallback을 두지 않는다 — readiness나
명시적 오류로 노출한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime as agent_runtime  # noqa: E402
import apply_severity_pair_guard as severity_guard  # noqa: E402
import checkpoint_backup  # noqa: E402
import checkpoint_contract as contract  # noqa: E402
import manifest_v3  # noqa: E402
from mutation_runtime import (  # noqa: E402
    MutationRuntimeError,
    resolve_exclusive_mode,
)

REPOSITORY_ROOT = SCRIPTS_ROOT.resolve().parents[1]

#: **Runtime 두 DB만이다.** Evaluation은 parser 직후 거부한다.
RUNTIME_DATABASES = frozenset({"kosa_agent", "kosa_agent_e2e"})

#: 이름을 **다시 정의하지 않고 빌려온다.** 같은 뜻의 상수를 두 곳에서 선언하면
#: 한쪽만 고쳐졌을 때 계약이 조용히 갈라진다(CM-3.3 팀리뷰 필수 2와 같은 이유).
RUNTIME_PROFILE = agent_runtime.RUNTIME_PROFILE
GUARDED_STAGE = severity_guard.GUARDED_STAGE

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2


class CheckpointSetupError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str = "CONTRACT_INVALID") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.exit_code = EXIT_USAGE


def assert_runtime_database(database: str | None) -> str:
    """**parser 직후 경계다.** `load_dotenv`·target loader·connector보다 앞이다.

    `V5-CM-3.3`의 같은 이름 함수와 같은 자리를 지킨다 — Evaluation 오적용은
    자격증명을 읽기 전에 끝나야 한다.
    """

    if database is None:
        raise CheckpointSetupError("--database가 필요합니다")
    if database not in RUNTIME_DATABASES:
        raise CheckpointSetupError(
            "checkpoint는 runtime profile에만 초기화합니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )
    return database


def resolve_mode(args: argparse.Namespace) -> str:
    """mode는 **하나만** 명시한다. mutation을 암묵적 기본값으로 두지 않는다."""

    mode = resolve_exclusive_mode(
        {
            "preflight": args.preflight,
            "apply": args.apply,
            "verify": args.verify,
            "smoke": args.smoke,
            "recover": args.recover_marker,
        },
        default_mode="",
        mutually_exclusive_message="checkpoint mode는 하나만 선택해야 합니다",
    )
    if not mode:
        raise CheckpointSetupError(
            "checkpoint mode를 하나 명시해야 합니다 "
            "(--preflight/--apply/--verify/--smoke/--recover-marker)"
        )
    return mode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5-CM-3.4 checkpoint 초기화")
    parser.add_argument("--database")
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--recover-marker", action="store_true")
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="backup 증적 디렉토리 — **저장소 밖** 절대경로 (--apply에서만 필요)",
    )
    parser.add_argument(
        "--approval",
        type=Path,
        default=None,
        help="팀 change approval 파일 (--apply에서만 필요)",
    )
    return parser


def read_catalog(cursor: Any) -> dict[str, Any]:
    """live catalog을 읽어 판정 payload로 접는다. **읽기만 한다.**"""

    rows: dict[str, Sequence[Mapping[str, Any]]] = {}
    cursor.execute(contract.CATALOG_SQL, (list(contract.CHECKPOINT_TABLES),))
    rows["columns"] = cursor.fetchall()
    cursor.execute(contract.INDEX_SQL, (list(contract.CHECKPOINT_INDEXES),))
    rows["indexes"] = cursor.fetchall()
    cursor.execute(contract.PRIMARY_KEY_SQL, (list(contract.CHECKPOINT_TABLES),))
    rows["primary_keys"] = cursor.fetchall()
    cursor.execute(
        "SELECT to_regclass('public.checkpoint_migrations') IS NOT NULL AS present"
    )
    if cursor.fetchone()["present"]:
        cursor.execute("SELECT v FROM checkpoint_migrations")
        rows["versions"] = cursor.fetchall()
    else:
        rows["versions"] = []
    # **owner·PUBLIC ACL을 catalog와 같은 시점에 읽는다.** 별도 postcheck로만 두면
    # preflight·no-op·verify·smoke가 ACL을 보지 않는 경로가 된다(13차 필수 3).
    cursor.execute(contract.CHECKPOINT_ACL_SQL, (sorted(contract.CHECKPOINT_TABLES),))
    rows["acl"] = cursor.fetchall()
    # **기대 owner는 지금 연결된 관리 계정이다**(14차 필수 1).
    #
    # marker에 적어 두면 marker를 읽지 않는 복구 경로가 그 값을 못 본다. 반대로
    # 여기서 읽으면 catalog를 읽는 **모든** 경로가 같은 기준을 갖는다 — checkpoint
    # 저장소는 그것을 만든 관리 계정 소유여야 한다는 계약이 그대로 판정이 된다.
    rows["expected_owner"] = _connected_role(cursor)
    return contract.inspect_catalog(rows)


def operational_row_counts(cursor: Any) -> dict[str, int]:
    """operational 3 table의 행 수. no-op·smoke가 전후를 대조한다."""

    counts: dict[str, int] = {}
    for table in contract.OPERATIONAL_TABLES:
        cursor.execute(f'SELECT count(*) AS c FROM "{table}"')
        counts[table] = int(cursor.fetchone()["c"])
    return counts


# ---------------------------------------------------------------------------
# marker · receipt — "물리적으로 맞다"와 "우리가 적용했다"는 다른 질문이다
# ---------------------------------------------------------------------------

MARKER_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
REPORT_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "reports"

ARTIFACT_TYPE = "checkpoint_setup_final"
MARKER_FORMAT_VERSION = 1
TASK_ID = "V5-CM-3.4"

#: marker의 exact key 집합. 하나라도 더하거나 빼면 거부한다.
MARKER_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "database",
        "profile",
        "status",
        "package_name",
        "package_version",
        "migration_id",
        "migration_digest_sha256",
        "migration_count",
        "latest_version",
        "bootstrap_stage",
        "dataset_epoch",
        "source_archive_sha256",
        "target_host_fingerprint",
        "predecessor_stage",
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
        "backup_archive_sha256",
        "backup_receipt_sha256",
        "change_approval_sha256",
        "catalog_signature_sha256",
        "change_reference",
        "applied_at",
        "recorded_at",
    }
)

RECEIPT_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "database",
        "status",
        "package_version",
        "migration_digest_sha256",
        "dataset_epoch",
        "target_host_fingerprint",
        "predecessor_stage",
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
        "backup_archive_sha256",
        "backup_receipt_sha256",
        "change_approval_sha256",
        "change_reference",
        "started_at",
        "committed_at",
        "catalog_signature_sha256",
    }
)

CHANGE_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")

#: checkpoint 적용 후 도달하는 bootstrap stage. `verify_bootstrap_state`의 같은
#: 이름 상수와 **한 글자도 달라선 안 된다** — 다르면 verifier가 이 marker를
#: 자기 stage로 인정하지 않는다.
CHECKPOINT_STAGE = "runtime_checkpointed"


class CheckpointArtifactError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str = "ARTIFACT_INVALID") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.exit_code = EXIT_MISMATCH


def validate_change_reference(value: str | None) -> str:
    if not isinstance(value, str) or not CHANGE_REFERENCE_PATTERN.fullmatch(value):
        raise CheckpointSetupError("change reference 형식이 잘못됐습니다")
    return value


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in RUNTIME_DATABASES:
        raise CheckpointSetupError("runtime database가 아닙니다")
    return root / f"{ARTIFACT_TYPE}.{database}.json"


def receipt_path(database: str, *, root: Path = REPORT_ROOT) -> Path:
    if database not in RUNTIME_DATABASES:
        raise CheckpointSetupError("runtime database가 아닙니다")
    return root / f"{ARTIFACT_TYPE}.{database}.json"


def _dataset_epoch() -> dict[str, str]:
    """epoch 등록 artifact를 **단일 출처로** 읽는다.

    상수로 다시 적으면 등록 artifact와 갈라진다. `load_dataset_epoch()`는 이미
    `fdc_final_20260818`이 아니면 거부하므로, 폐기 epoch(`kosa_0813`)로 만든
    marker는 여기서 막힌다(CLAUDE.md CAUTION).
    """

    epoch = manifest_v3.load_dataset_epoch()
    return {
        "dataset_epoch": str(epoch["dataset_epoch"]),
        "source_archive_sha256": str(epoch["archive"]["sha256"]),
    }


def _parse_instant(value: Any, *, label: str) -> datetime:
    """ISO-8601 UTC만 받는다. **naive는 거부한다.**

    tz 없는 시각은 비교하면 조용히 틀린다 — 같은 문자열이 두 시간대에서 다른
    순간을 뜻하기 때문이다.
    """

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckpointArtifactError(f"{label} 시각 형식이 잘못됐습니다") from exc
    if parsed.tzinfo is None:
        raise CheckpointArtifactError(f"{label}에 시간대가 없습니다")
    return parsed


def _assert_ordered(payload: Mapping[str, Any], earlier: str, later: str) -> None:
    """`earlier <= later`를 강제한다.

    뒤집힌 시각은 손으로 고친 artifact의 가장 흔한 흔적이다. 초판은 두 필드를
    형식조차 보지 않았다(2차 필수 5).
    """

    if _parse_instant(payload[earlier], label=earlier) > _parse_instant(
        payload[later], label=later
    ):
        raise CheckpointArtifactError(f"{earlier}가 {later}보다 늦습니다")


def _sha256_hex(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value or ""))


def predecessor_identity(target: Any) -> dict[str, Any]:
    """**CM-3.3 guarded marker에서 계보를 읽는다.**

    checkpoint를 무엇 위에 얹었는지는 `checkpoint_migrations`가 모른다. version은
    package 진행도이지 target·시각·계보가 아니다. predecessor marker의 hash가
    CM-3.3 계보와 잇는 유일한 연결이다.

    ## database 이름이 아니라 target을 받는다

    초판은 `database` 문자열을 받아 **환경에서 target을 다시 로드했다.** 그러면 caller가
    넘긴 target과 여기서 읽은 target이 갈릴 수 있다 — marker·fingerprint는 환경 target을
    가리키는데 DB 연결은 인자 target으로 가는 상태다. 테스트 seam 문제가 아니라 "같은
    target을 끝까지 쓴다"는 안전 계약이 끊긴 것이다(구현리뷰 3차 필수 2).
    """

    import db_target

    sql, _ = severity_guard.load_and_validate_sql()
    marker = severity_guard.load_marker(
        target, migration_sha=severity_guard.migration_sha256(sql)
    )
    if marker is None:
        raise CheckpointArtifactError(
            "CM-3.3 guarded marker가 없습니다", reason_code="PREDECESSOR_MISSING"
        )
    signature = marker.get("guarded_schema_signature_sha256")
    if not _sha256_hex(str(signature)):
        raise CheckpointArtifactError(
            "CM-3.3 marker에 guarded schema signature가 없습니다",
            reason_code="PREDECESSOR_INVALID",
        )
    return {
        "predecessor_stage": severity_guard.GUARDED_STAGE,
        "predecessor_marker_sha256": manifest_v3.canonical_payload_sha256(marker),
        # **선행 stage의 스키마 자체**를 가리킨다. marker hash만으로는 "그 파일이
        # 그대로다"까지만 말할 수 있고, 그 파일이 서술하는 DB가 지금도 같은지는
        # 말하지 못한다(2차 필수 5).
        "predecessor_schema_signature_sha256": str(signature),
        "target_host_fingerprint": db_target.host_fingerprint(target.host, target.port),
    }


def validate_marker(payload: Mapping[str, Any], database: str) -> None:
    """strict schema. **key 집합·형식·계약값을 전부 본다.**"""

    if set(payload) != MARKER_KEYS:
        raise CheckpointArtifactError("checkpoint marker key 집합이 잘못됐습니다")
    if payload["artifact_type"] != ARTIFACT_TYPE:
        raise CheckpointArtifactError("checkpoint marker artifact type이 다릅니다")
    if payload["format_version"] != MARKER_FORMAT_VERSION or isinstance(
        payload["format_version"], bool
    ):
        raise CheckpointArtifactError("checkpoint marker format version이 다릅니다")
    if payload["task_id"] != TASK_ID or payload["database"] != database:
        raise CheckpointArtifactError("checkpoint marker provenance가 다릅니다")
    if payload["status"] != "APPLIED":
        raise CheckpointArtifactError("checkpoint marker 상태가 다릅니다")
    if (
        payload["package_name"] != contract.PACKAGE_NAME
        or payload["package_version"] != contract.PACKAGE_VERSION
        or payload["migration_id"] != contract.MIGRATION_ID
        or payload["migration_digest_sha256"] != contract.MIGRATION_DIGEST_SHA256
        or payload["migration_count"] != contract.MIGRATION_COUNT
        or payload["latest_version"] != contract.LATEST_VERSION
    ):
        raise CheckpointArtifactError("checkpoint marker package 계약이 다릅니다")
    if payload["profile"] != RUNTIME_PROFILE:
        raise CheckpointArtifactError("checkpoint marker profile이 다릅니다")
    if payload["bootstrap_stage"] != CHECKPOINT_STAGE:
        raise CheckpointArtifactError("checkpoint marker stage가 다릅니다")
    if payload["predecessor_stage"] != GUARDED_STAGE:
        raise CheckpointArtifactError("checkpoint marker 선행 stage가 다릅니다")
    epoch = _dataset_epoch()
    if payload["dataset_epoch"] != epoch["dataset_epoch"]:
        raise CheckpointArtifactError("checkpoint marker dataset epoch이 다릅니다")
    if payload["source_archive_sha256"] != epoch["source_archive_sha256"]:
        raise CheckpointArtifactError("checkpoint marker 원본 archive가 다릅니다")
    for key in (
        "catalog_signature_sha256",
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
        "target_host_fingerprint",
        # **gate를 통과시킨 증적을 byte 단위로 남긴다.**
        # 이것이 없으면 어느 backup·어느 승인으로 적용했는지 감사할 수 없다.
        "backup_archive_sha256",
        "backup_receipt_sha256",
        "change_approval_sha256",
    ):
        if not _sha256_hex(str(payload[key])):
            raise CheckpointArtifactError(f"checkpoint marker {key}가 잘못됐습니다")
    _assert_ordered(payload, "applied_at", "recorded_at")
    validate_change_reference(str(payload["change_reference"]))
    manifest_v3.scan_for_sensitive_values(dict(payload))


def load_marker(database: str, *, root: Path = MARKER_ROOT) -> dict[str, Any] | None:
    path = marker_path(database, root=root)
    if not path.is_file():
        return None
    payload = manifest_v3._load_json(path)
    if not isinstance(payload, dict):
        raise CheckpointArtifactError("checkpoint marker는 object여야 합니다")
    validate_marker(payload, database)
    return payload


def save_marker(payload: Mapping[str, Any], *, root: Path = MARKER_ROOT) -> None:
    validate_marker(payload, str(payload["database"]))
    try:
        manifest_v3.atomic_save_json(
            marker_path(str(payload["database"]), root=root), dict(payload)
        )
    except OSError as exc:
        raise CheckpointArtifactError("checkpoint marker 저장에 실패했습니다") from exc


def validate_receipt(payload: Mapping[str, Any], database: str) -> None:
    if set(payload) != RECEIPT_KEYS:
        raise CheckpointArtifactError("checkpoint receipt key 집합이 잘못됐습니다")
    if payload["artifact_type"] != f"{ARTIFACT_TYPE}_receipt":
        raise CheckpointArtifactError("checkpoint receipt artifact type이 다릅니다")
    if payload["database"] != database or payload["task_id"] != TASK_ID:
        raise CheckpointArtifactError("checkpoint receipt provenance가 다릅니다")
    if payload["status"] not in {"STARTED", "COMMITTED", "ABORTED"}:
        raise CheckpointArtifactError("checkpoint receipt 상태가 잘못됐습니다")
    # **key에 있다고 검증되는 것이 아니다.** 초판은 `format_version`·`package_version`을
    # 집합에만 넣고 값을 보지 않아, version `999`도 migration digest만 같으면 통과했다
    # (구현리뷰 4차 필수 3). `bool`은 `int`의 하위형이라 따로 배제한다.
    if payload["format_version"] != MARKER_FORMAT_VERSION or isinstance(
        payload["format_version"], bool
    ):
        raise CheckpointArtifactError("checkpoint receipt format version이 다릅니다")
    if (
        payload["package_version"] != contract.PACKAGE_VERSION
        or payload["migration_digest_sha256"] != contract.MIGRATION_DIGEST_SHA256
    ):
        raise CheckpointArtifactError("checkpoint receipt package 계약이 다릅니다")
    if payload["dataset_epoch"] != _dataset_epoch()["dataset_epoch"]:
        raise CheckpointArtifactError("checkpoint receipt dataset epoch이 다릅니다")
    if payload["predecessor_stage"] != GUARDED_STAGE:
        raise CheckpointArtifactError("checkpoint receipt 선행 stage가 다릅니다")
    for key in (
        "predecessor_marker_sha256",
        "predecessor_schema_signature_sha256",
        "target_host_fingerprint",
        "backup_archive_sha256",
        "backup_receipt_sha256",
        "change_approval_sha256",
    ):
        if not _sha256_hex(str(payload[key])):
            raise CheckpointArtifactError(f"checkpoint receipt {key}가 잘못됐습니다")
    validate_change_reference(str(payload["change_reference"]))
    _parse_instant(payload["started_at"], label="started_at")
    if payload["status"] == "STARTED":
        # 아직 끝나지 않은 작업은 완료 시각을 가질 수 없다.
        if payload["committed_at"] is not None:
            raise CheckpointArtifactError("STARTED receipt에 완료 시각이 있습니다")
        if payload["catalog_signature_sha256"] is not None:
            raise CheckpointArtifactError("STARTED receipt에 signature가 있습니다")
    elif payload["status"] == "COMMITTED":
        if not _sha256_hex(str(payload["catalog_signature_sha256"])):
            raise CheckpointArtifactError(
                "COMMITTED receipt에 catalog signature가 없습니다"
            )
        _assert_ordered(payload, "started_at", "committed_at")
    else:
        # **`ABORTED`는 결과를 가지지 않는다.**
        #
        # 초판은 이 분기가 없어서 중단된 실행의 receipt에 임의 `committed_at`이나
        # catalog signature가 있어도 통과했다. 그러면 `--recover-marker`가 무엇을
        # 근거로 거부하는지가 흐려진다(구현리뷰 4차 필수 3).
        if payload["committed_at"] is not None:
            raise CheckpointArtifactError("ABORTED receipt에 완료 시각이 있습니다")
        if payload["catalog_signature_sha256"] is not None:
            raise CheckpointArtifactError("ABORTED receipt에 signature가 있습니다")
    manifest_v3.scan_for_sensitive_values(dict(payload))


def load_receipt(database: str, *, root: Path = REPORT_ROOT) -> dict[str, Any] | None:
    path = receipt_path(database, root=root)
    if not path.is_file():
        return None
    payload = manifest_v3._load_json(path)
    if not isinstance(payload, dict):
        raise CheckpointArtifactError("checkpoint receipt는 object여야 합니다")
    validate_receipt(payload, database)
    return payload


def _save_receipt(payload: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    validate_receipt(payload, str(payload["database"]))
    root.mkdir(parents=True, exist_ok=True)
    manifest_v3.atomic_save_json(
        receipt_path(str(payload["database"]), root=root), dict(payload)
    )
    return dict(payload)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _refuse(state: str) -> NoReturn:
    messages = {
        "ABSENT": "checkpoint 저장소가 아직 없습니다",
        "PARTIAL": "checkpoint 적용이 부분 상태입니다 — 자동 보정하지 않습니다",
        "READY": "checkpoint 저장소가 이미 준비돼 있습니다",
        "READY_UNMARKED": "checkpoint는 적용됐으나 marker가 없습니다",
        "MARKER_DRIFT": "marker가 live catalog·계보와 다릅니다",
        "DRIFT": "checkpoint catalog가 계약과 다릅니다",
    }
    raise contract.CheckpointStateError(
        messages.get(state, "checkpoint 상태가 자동 보정 대상이 아닙니다"),
        reason_code=state,
    )


# ---------------------------------------------------------------------------
# 연결 — autocommit이 필수다
# ---------------------------------------------------------------------------


def _connect(target: Any) -> Any:
    """**`autocommit=True`가 계약이다.**

    `CREATE INDEX CONCURRENTLY`는 transaction block 안에서 못 돈다. 기본
    `autocommit=False`로 열면 `setup()`이 `ActiveSqlTransaction`으로 죽는다.

    `prepare_threshold=0`은 설계서 8장이 명시한 값이다 — 같은 문장을 여러 DB에
    돌릴 때 prepared statement 캐시가 stage마다 갈리는 것을 막는다.
    """

    import psycopg

    return psycopg.connect(
        conninfo=str(target.create_url().render_as_string(hide_password=False)).replace(
            "postgresql+psycopg://", "postgresql://"
        ),
        autocommit=True,
        prepare_threshold=0,
        row_factory=psycopg.rows.dict_row,
        connect_timeout=5,
    )


#: marker가 identity와 **정확히** 일치해야 하는 필드. 하나라도 빠지면 그 축의 변조가
#: 통과한다 — 2차에서 `target_host_fingerprint`와 선행 schema가 여기 없어서, 다른 host를
#: 가리키는 marker가 `READY_MARKED`가 됐다(구현리뷰 3차 필수 2).
IDENTITY_BOUND_KEYS: tuple[str, ...] = (
    "predecessor_stage",
    "predecessor_marker_sha256",
    "predecessor_schema_signature_sha256",
    "target_host_fingerprint",
)


def marker_identity_mismatches(
    marker: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    catalog_signature: str | None = None,
) -> list[str]:
    """marker와 현재 identity의 **불일치 축을 전부** 돌려준다.

    **판정을 한 곳에 둔다.** preflight·apply no-op·verify·full verifier가 각자 비교하면
    가장 느슨한 경로가 실효 계약이 된다. 2차가 정확히 그랬다 — verify는 계보를 보는데
    `resolve_state()`는 보지 않아서 같은 DB가 apply에서는 `NO_OP`,
    verify에서는 실패였다.

    첫 불일치에서 멈추지 않고 **모두 모은다.** 하나씩 고치며 재실행하는 것보다 한 번에
    보이는 편이 원인 규명 입력으로 낫다.
    """

    mismatches = [k for k in IDENTITY_BOUND_KEYS if marker.get(k) != identity[k]]
    if catalog_signature is not None and (
        marker.get("catalog_signature_sha256") != catalog_signature
    ):
        mismatches.append("catalog_signature_sha256")
    return mismatches


def resolve_state(
    catalog: Mapping[str, Any],
    marker: Mapping[str, Any] | None,
    *,
    identity: Mapping[str, Any] | None = None,
) -> str:
    """물리 상태 + artifact를 **하나의 판정**으로 접는다.

    "물리적으로 맞다"와 "우리가 적용했다"는 다른 질문이다. 외부에서 우연히 만든
    catalog나 apply 도중 marker 발급 전에 멈춘 상태를 완료로 보면 안 된다
    (구현리뷰 1차 필수 3).

    ## marker 존재만으로 `READY_MARKED`를 주지 않는다

    초판은 형식만 유효한 marker가 있으면 무조건 `READY_MARKED`였다. 그래서 실제
    catalog signature와 다른 임의 hash를 넣은 marker도 preflight를 통과하고
    `run_apply()`가 `NO_OP`을 냈다(구현리뷰 2차 필수 2).

    live catalog signature와 현재 predecessor 계보까지 맞아야 `READY_MARKED`다.
    """

    physical = contract.classify_state(catalog)
    if physical != "READY":
        return physical
    if marker is None:
        return "READY_UNMARKED"
    signature = contract.assert_ready(catalog)
    if identity is None:
        # identity 없이는 계보를 물을 수 없다. catalog만 대조한다.
        return (
            "READY_MARKED"
            if marker.get("catalog_signature_sha256") == signature
            else "MARKER_DRIFT"
        )
    if marker_identity_mismatches(marker, identity, catalog_signature=signature):
        return "MARKER_DRIFT"
    return "READY_MARKED"


def run_preflight(
    target: Any, *, connect: Any = _connect, marker_root: Path = MARKER_ROOT
) -> str:
    """read-only 상태 판정. **아무것도 쓰지 않는다.**"""

    contract.assert_package_contract()
    connection = connect(target)
    try:
        catalog = read_catalog(connection.cursor())
    finally:
        connection.close()
    marker = load_marker(target.database, root=marker_root)
    identity = predecessor_identity(target) if marker is not None else None
    return resolve_state(catalog, marker, identity=identity)


#: 같은 autocommit session 전체를 보호한다.
#:
#: **transaction advisory lock을 쓰지 않는다.** autocommit에서는 매 문장마다 풀리므로
#: non-atomic migration 전체를 덮지 못한다(계획 §5.3-4).
ADVISORY_LOCK_KEY = 0x434D3334


def _try_session_lock(cursor: Any) -> bool:
    """lock을 **시도만** 한다. 잡히면 True.

    회귀가 "지금 이 시점에 다른 session이 lock을 잡을 수 있는가"를 물을 수 있어야
    한다. 소스 문자열 순서로는 그 질문에 답할 수 없다(구현리뷰 3차 필수 5).
    """

    cursor.execute("SELECT pg_try_advisory_lock(%s) AS ok", (ADVISORY_LOCK_KEY,))
    return bool(cursor.fetchone()["ok"])


def _acquire_session_lock(cursor: Any) -> None:
    if not _try_session_lock(cursor):
        raise contract.CheckpointStateError(
            "다른 실행이 checkpoint lock을 보유합니다", reason_code="LOCK_BUSY"
        )


def _release_session_lock(cursor: Any) -> None:
    cursor.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


def _assert_connected_identity(cursor: Any, target: Any) -> None:
    """**연결된 곳이 대상인지 본다.** DSN을 믿지 않는다."""

    cursor.execute("SELECT current_database() AS db, current_schema() AS schema_name")
    row = cursor.fetchone()
    if str(row["db"]) != target.database:
        raise contract.CheckpointStateError(
            "연결된 database가 대상과 다릅니다", reason_code="IDENTITY_MISMATCH"
        )
    if str(row["schema_name"]) != "public":
        raise contract.CheckpointStateError(
            "search_path가 public이 아닙니다", reason_code="IDENTITY_MISMATCH"
        )


def _connected_role(cursor: Any) -> str:
    """`setup()`을 실제로 실행한 role. ACL 대조의 기준이 된다."""

    cursor.execute("SELECT current_user AS role")
    return str(cursor.fetchone()["role"])


#: `--require-backup=False`(container 회귀)에서 쓰는 자리표시자.
#:
#: **실제 적용 경로에서는 절대 쓰이지 않는다.** 값이 0으로 채워져 있어 증적에 남아도
#: "검증하지 않았다"가 한눈에 보인다.
_UNVERIFIED_EVIDENCE = {
    "backup_archive_sha256": "0" * 64,
    "backup_receipt_sha256": "0" * 64,
    "change_approval_sha256": "0" * 64,
}

#: 팀 change approval의 관례적 위치.
#:
#: CLI는 `--approval`과 `--backup-root`를 **둘 다 필수로** 받는다. 기본 경로로 조용히
#: 흘러가면 운영자가 어떤 승인·어떤 backup으로 적용했는지 명령에 남지 않는다 —
#: 되돌릴 수 없는 작업에서 그건 증적 공백이다(구현리뷰 4차 필수 4).
#:
#: **backup root에는 기본값을 두지 않는다.** 저장소 안 경로는 `validate_backup_root()`가
#: 거부하므로, `infra/bootstrap/backups`를 기본값으로 두면 **backup 도구가 절대 쓸 수
#: 없는 곳**을 apply가 뒤지게 된다. 두 도구의 계약이 서로 어긋난 상태였다.
DEFAULT_APPROVAL_PATH = (
    REPOSITORY_ROOT / "infra" / "bootstrap" / "approvals" / "change_approval.json"
)


def require_backup_evidence(
    target: Any,
    change_reference: str,
    *,
    backup_root: Path,
    approval_path: Path,
    shape: Mapping[str, str],
) -> dict[str, str]:
    """**되돌릴 수 없는 작업 앞에 되돌릴 수단을 요구한다.**

    `setup()`은 non-atomic이고 `PARTIAL`은 자동 보정하지 않기로 했다(계획 §5.3).
    그러면 복구 수단은 backup restore 하나뿐인데, 초판은 그것이 있는지 **묻지도
    않고** 공용 DB에 `setup()`을 돌렸다(구현리뷰 2차 필수 3).

    ## 왜 transition receipt를 더는 쓰지 않는가

    구현리뷰 4차 필수 3에 따라 `postgres_transition.assert_receipt_matches()`를
    재사용했었다. 그 결속 자체는 옳았지만, 그 receipt를 만드는 `backup_orchestrator`가
    **`V5-CM-2.6` 전환 preflight**를 통과해야만 동작한다. `V5-CM-3.1`이
    `v_alarm_event`를 final 계약으로 재정의한 뒤로는 세 target 모두
    `TARGET_STATE_UNSUPPORTED`다 — 그 게이트는 "전환 준비가 됐는가"를 묻는 전환
    **이전** 질문이고 우리는 이미 지나왔다.

    그리고 그 backup은 `BACKUP_TABLES`(base 9)만 담는다. checkpoint가 되돌려야 하는
    것은 `runtime_guarded` 형상 **전체**다.

    그래서 `checkpoint_backup`의 계약으로 바꿨다. 결속의 강도는 유지한다 — 이름이 같은
    다른 host의 bundle, 다른 change ref, 형상이 달라진 backup을 모두 거른다. 다른 점은
    **무엇과 결속하느냐**다: 전환 시점 inventory가 아니라
    **적용 직전 guarded 형상**이다.
    """

    import checkpoint_backup as cbackup
    import db_target

    database = target.database
    approval = _load_change_approval(approval_path, database, change_reference)
    try:
        receipt = cbackup.load_evidence(
            database, change_reference, backup_root=backup_root
        )
        cbackup.assert_receipt_matches(
            receipt,
            target=target,
            shape=shape,
            host_fingerprint=db_target.host_fingerprint(target.host, target.port),
        )
    except cbackup.CheckpointBackupError as exc:
        raise CheckpointArtifactError(
            "backup 증적이 현재 target·형상과 맞지 않습니다",
            reason_code=exc.reason_code,
        ) from exc
    # **키 이름과 값이 같은 것을 가리킨다.**
    #
    # 초판은 `backup_receipt_sha256`에 `archive_sha256`를 넣었다 — 이름은 receipt인데
    # 값은 archive였다(구현리뷰 10차 필수 4).
    verified = receipt["_verified"]
    return {
        "backup_archive_sha256": verified["archive_sha256"],
        "backup_receipt_sha256": verified["receipt_sha256"],
        "change_approval_sha256": approval["_digest"],
    }


#: checkpoint 적용 승인 artifact. **전환 approval과 다른 계약이다.**
#:
#: 전환 approval은 18키 중 9개가 `transition_public_postgres --preflight` 산출물이다.
#: 그 preflight는 `V5-CM-3.1`이 `v_alarm_event`를 재정의한 뒤 세 target 모두
#: `TARGET_STATE_UNSUPPORTED`이므로, 그 형식을 요구하면 **만들 수 없는 승인**을
#: 요구하게 된다(`V5-CM-3.4` 묶음 2 준비).
#:
#: 여기서 묶는 것은 **되돌릴 수 없는 그 일**이다 — 어느 target에, 어느 stage에서 어느
#: stage로, 어느 package를 적용하는가. 사람이 직접 쓸 수 있고 무엇을 승인했는지가
#: 문장으로 남는다.
APPROVAL_ARTIFACT_TYPE = "checkpoint_change_approval"

APPROVAL_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "dataset_epoch",
        "change_reference",
        "status",
        "targets",
        "from_stage",
        "to_stage",
        "package_name",
        "package_version",
        "migration_digest_sha256",
        # **복구는 적용과 다른 의사표시다.**
        #
        # 같은 파일을 쓰되 별도 flag를 요구한다. 적용 승인이 자동으로 "장애 시 되돌려도
        # 좋다"까지 뜻하지는 않는다 — 복구는 공용 DB를 backup 시점으로 되돌리는 별개의
        # 파괴적 작업이다(구현리뷰 11차 필수 2).
        "recovery_approved",
        "approved_at",
    }
)


def validate_change_approval(
    payload: Any, database: str, change_reference: str
) -> None:
    """승인이 **이 적용을 가리키는지** 본다.

    형식만 맞는 승인은 승인이 아니다. target·stage 전이·package pin이 실제 적용과
    같아야 한다 — 다르면 "다른 것을 승인해 놓고 이것을 실행"하는 상태다.
    """

    if not isinstance(payload, Mapping) or set(payload) != APPROVAL_KEYS:
        raise CheckpointArtifactError(
            "change approval key 집합이 잘못됐습니다", reason_code="APPROVAL_INVALID"
        )
    if payload["artifact_type"] != APPROVAL_ARTIFACT_TYPE:
        raise CheckpointArtifactError(
            "change approval artifact type이 다릅니다", reason_code="APPROVAL_INVALID"
        )
    if payload["format_version"] != MARKER_FORMAT_VERSION or isinstance(
        payload["format_version"], bool
    ):
        raise CheckpointArtifactError(
            "change approval format version이 다릅니다", reason_code="APPROVAL_INVALID"
        )
    if payload["task_id"] != TASK_ID:
        raise CheckpointArtifactError(
            "change approval이 다른 Task의 것입니다", reason_code="APPROVAL_MISMATCH"
        )
    if payload["dataset_epoch"] != _dataset_epoch()["dataset_epoch"]:
        raise CheckpointArtifactError(
            "change approval dataset epoch이 다릅니다", reason_code="APPROVAL_MISMATCH"
        )
    if payload["status"] != "APPROVED":
        raise CheckpointArtifactError(
            "승인 상태가 APPROVED가 아닙니다", reason_code="APPROVAL_MISMATCH"
        )
    if payload["change_reference"] != change_reference:
        raise CheckpointArtifactError(
            "change approval이 다른 change ref입니다", reason_code="APPROVAL_MISMATCH"
        )
    targets = payload["targets"]
    if not isinstance(targets, list) or not set(targets) <= RUNTIME_DATABASES:
        raise CheckpointArtifactError(
            "승인 target이 runtime DB가 아닙니다", reason_code="APPROVAL_INVALID"
        )
    if database not in targets:
        raise CheckpointArtifactError(
            "change approval에 이 target이 없습니다", reason_code="APPROVAL_MISMATCH"
        )
    # **stage 전이를 명시적으로 승인받는다.**
    # 무엇에서 무엇으로 가는지가 문장으로 남는다.
    if (
        payload["from_stage"] != GUARDED_STAGE
        or payload["to_stage"] != CHECKPOINT_STAGE
    ):
        raise CheckpointArtifactError(
            "승인된 stage 전이가 다릅니다", reason_code="APPROVAL_MISMATCH"
        )
    # **package pin까지 승인 대상이다.** 되돌릴 수 없는 것은 "checkpoint 적용"이 아니라
    # "이 package의 이 migration 적용"이다.
    if (
        payload["package_name"] != contract.PACKAGE_NAME
        or payload["package_version"] != contract.PACKAGE_VERSION
        or payload["migration_digest_sha256"] != contract.MIGRATION_DIGEST_SHA256
    ):
        raise CheckpointArtifactError(
            "승인된 package 계약이 다릅니다", reason_code="APPROVAL_MISMATCH"
        )
    if not isinstance(payload["recovery_approved"], bool):
        raise CheckpointArtifactError(
            "recovery_approved는 bool이어야 합니다", reason_code="APPROVAL_INVALID"
        )
    _parse_instant(payload["approved_at"], label="approved_at")
    manifest_v3.scan_for_sensitive_values(dict(payload))


def _load_change_approval(
    path: Path, database: str, change_reference: str
) -> dict[str, Any]:
    """팀 change approval을 읽는다.

    승인 없이 공용 DB를 바꾸지 않는다는 규칙은 문서가 아니라 여기서 강제된다.
    파일을 **한 번만 읽어** 내용과 digest를 함께 만든다 — 두 번 읽으면 그 사이에 파일이
    바뀌어 "판단한 것"과 "증적에 적은 것"이 갈릴 수 있다.
    """

    import hashlib

    if path.is_symlink() or not path.is_file():
        raise CheckpointArtifactError(
            "팀 change approval이 없습니다", reason_code="APPROVAL_MISSING"
        )
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointArtifactError(
            "change approval을 읽을 수 없습니다", reason_code="APPROVAL_INVALID"
        ) from exc
    validate_change_approval(payload, database, change_reference)
    return {**dict(payload), "_digest": hashlib.sha256(raw).hexdigest()}


class _SignatureConnection:
    """`build_schema_signature()`가 psycopg cursor 위에서도 돌게 하는 얇은 어댑터.

    signature 계산을 여기서 다시 구현하면 CM-3.3이 세운 정본이 두 벌이 된다. 계약은
    빌려오고, 연결 모양만 맞춘다.
    """

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def exec_driver_sql(self, sql: str, params: Any = None) -> Any:
        self._cursor.execute(sql, params)
        return _Mappings([dict(row) for row in self._cursor.fetchall()])

    # `execute(text(...))`는 제거했다.
    #
    # `postgres_transition.read_inventory()`만 그 모양을 썼고, backup 계약을
    # checkpoint 전용으로 바꾸면서 그 호출이 사라졌다. 판정 코드를 raise로 바꿔
    # container 72건을 돌려 **아무도 부르지 않음을 확인**한 뒤 지웠다 — 쓰이지 않는
    # 어댑터 표면은 검증되지 않은 채 남는다.


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar_one(self) -> Any:
        """SQLAlchemy `Result.scalar_one()` — **정확히 한 행 한 컬럼**이다.

        `verify_bootstrap_state._scalar()`가 이 모양을 쓴다. 개수를 느슨하게 보면
        "여러 행 중 첫 값"이 조용히 통과해 판정이 달라진다.
        """

        if len(self._rows) != 1:
            raise ValueError("scalar_one은 정확히 한 행을 요구합니다")
        values = list(self._rows[0].values())
        if len(values) != 1:
            raise ValueError("scalar_one은 정확히 한 컬럼을 요구합니다")
        return values[0]


def guarded_signature(cursor: Any) -> str:
    """**live** guarded stage schema signature.

    `build_schema_signature()`는 `RUNTIME_TABLES` 22개만 본다. checkpoint 4개는 그
    바깥이므로 이 값은 checkpoint 적용 **전후로 같아야 한다** — 그래서 선행 상태의
    증명이자 동시에 "checkpoint가 업무 스키마를 안 건드렸다"의 증명이 된다.
    """

    # **guarded allowlist를 쓴다.** base `EXPECTED_CONSTRAINTS`로 재면 CM-3.3이 바꾼
    # CHECK 하나 때문에 항상 drift가 난다 — 선행 stage는 guarded이지 clean이 아니다.
    return agent_runtime.schema_signature_sha256(
        _SignatureConnection(cursor),
        expected_constraints=severity_guard.GUARDED_CONSTRAINTS,
    )


def guarded_alarm_rows(cursor: Any) -> int:
    """`v_alarm_event` 행 수. **driver 오류를 판정으로 접는다.**

    이 View는 CM-3.1 계약의 일부이므로 없으면 그 자체가 drift다. 초판은 이 호출이
    `predecessor_postcheck()` 밖에 있어서, View가 사라진 DB에서 `run_apply`가 판정
    대신 psycopg stack trace로 끝났다(구현리뷰 4차 필수 1).
    """

    import psycopg

    try:
        return agent_runtime.alarm_event_count(_SignatureConnection(cursor))
    except (psycopg.Error, agent_runtime.AgentRuntimeError) as exc:
        raise contract.CheckpointStateError(
            "live 선행 stage의 alarm View 계약이 다릅니다",
            reason_code="PREDECESSOR_DRIFT",
        ) from exc


def reference_physical_mismatches(cursor: Any) -> list[str]:
    """base·reference·RAG table의 **물리 계약**을 본다.

    `postcheck_database()`는 Runtime 9종의 constraint·index와 22-table **이름**
    allowlist를 본다. `_final_reference_mismatches()`는 R03·View를 본다. 그 사이가
    비어 있었다(구현리뷰 4·5차 필수 1·2).

    ## 무엇이 비어 있었나

    - **base 9** — 이름 집합 말고는 아무것도 보지 않았다. `fdc_trace.value` type,
      `lot_history` nullability, `dim_parameter` column 순서가 drift해도 통과했다.
    - **reference/RAG** — column을 dict로 비교해 **순서를 잃었고**, PK·FK·UNIQUE·
      CHECK·default는 아예 보지 않았다. `document_chunk`의 `(doc_id, chunk_seq)`
      UNIQUE를 지워도 통과했다.

    ## 정본을 빌려온다

    base 9는 **source manifest v4**(`columns`·`column_types`·`primary_key`)가, RAG
    object는 `apply_rag_schema`의 `RAG_SCHEMA_SQL`과 짝인 계약이 정본이다. 여기서
    다시 적으면 두 벌이 갈린다.

    R03는 제외한다 — `reference_extensions` registry는 V4 11컬럼이고 final은 V5
    12컬럼이라 그대로 비교하면 **정상 DB가 항상 mismatch**가 된다. R03 계약은
    `assert_r03_columns()`가 본다.
    """

    import apply_rag_schema as rag
    import apply_reference_extensions as reference_extensions
    import apply_reference_extensions_v5 as reference_v5
    import bootstrap_base_schema as base_schema
    import final_profile_manifests as final_manifests
    import value_normalization as norms

    mismatches: list[str] = []
    connection = _SignatureConnection(cursor)
    live = _live_columns(cursor)

    # --- base 9 — source manifest v4가 정본 ---------------------------------
    for table, entry in final_manifests.load_source_manifest()["tables"].items():
        actual = live.get(table)
        if actual is None:
            mismatches.append(f"base_missing:{table}")
            continue
        if [name for name, _t, _n, _d in actual] != list(entry["columns"]):
            mismatches.append(f"base_column_order:{table}")
            continue
        if {
            name: norms.logical_type(data_type) for name, data_type, _n, _d in actual
        } != dict(entry["column_types"]):
            mismatches.append(f"base_column_type:{table}")
        # **nullability·default는 `bootstrap_base_schema`가 정본이다.**
        #
        # source manifest v4에는 그 두 축이 없다. 반대로 그 registry의 `data_type`은
        # `wafer` 4곳을 아직 `smallint`로 보므로 **type은 여기서 읽지 않는다** —
        # `_final_source_column_types()`가 registry를 피하는 이유와 같다. 두 정본을
        # 축별로 나눠 쓰되, 어느 축을 어디서 읽는지는 명시한다.
        contract_columns = base_schema.BASE_COLUMNS.get(table)
        if contract_columns is not None:
            expected_nullability = {
                column.name: (column.nullable, _canonical_default(column.default))
                for column in contract_columns
            }
            if {
                name: (nullable, _canonical_default(default))
                for name, _t, nullable, default in actual
            } != expected_nullability:
                mismatches.append(f"base_nullability:{table}")
        if _primary_key(cursor, table) != tuple(entry["primary_key"]):
            mismatches.append(f"base_primary_key:{table}")

    # --- base 9 object — **소유 모듈의 signature를 그대로 쓴다** -------------
    #
    # `build_actual_signature()`는 FK·CHECK·explicit index를 이미 만든다. 여기서
    # 다시 비교 규칙을 적으면 `bootstrap_base_schema`와 갈라진다(구현리뷰 6차 필수 1).
    #
    # **`columns` 축은 비교하지 않는다.** 그 registry의 `data_type`은 `wafer` 4곳을
    # 아직 `smallint`로 보므로 정상 DB가 먼저 실패한다. column은 위에서 source
    # manifest v4(type)와 registry(nullability·default)로 축을 나눠 이미 봤다.
    expected_tables = base_schema.EXPECTED_SIGNATURE["tables"]
    try:
        actual_signature, _counts = base_schema.build_actual_signature(connection)
    except base_schema.BootstrapError:
        mismatches.append("base_signature")
    else:
        for table, actual_entry in actual_signature["tables"].items():
            expected_entry = expected_tables[table]
            if actual_entry["constraints"] != expected_entry["constraints"]:
                # PK·FK·CHECK가 한 축이다.
                mismatches.append(f"base_constraint:{table}")
            if actual_entry["indexes"] != expected_entry["indexes"]:
                mismatches.append(f"base_index:{table}")

    # --- reference·RAG column — **순서까지** 본다 ---------------------------
    for table, columns in reference_extensions.EXPECTED_TABLE_COLUMNS.items():
        if table == reference_v5.R03_TABLE:
            continue
        expected = [
            (name, data_type, nullable) for name, data_type, nullable in columns
        ]
        actual = live.get(table)
        if (
            actual is None
            or [(name, data_type, nullable) for name, data_type, nullable, _d in actual]
            != expected
        ):
            mismatches.append(f"reference_column:{table}")

    # --- RAG object — DDL과 짝인 계약 ---------------------------------------
    try:
        # 컬럼 **순서**와 폐기 `document_corpus` 잔존
        rag.verify_rag_schema(connection)
        # PK·FK·UNIQUE·CHECK와 column default
        rag.verify_rag_objects(connection)
    except rag.RagSchemaError:
        mismatches.append("rag_schema")

    # --- nl_query_log object — 소유 모듈의 단독 계약 -------------------------
    mismatches.extend(reference_extensions.nl_query_log_object_mismatches(connection))

    cursor.execute("SELECT extname FROM pg_extension ORDER BY 1")
    if "vector" not in {str(row["extname"]) for row in cursor.fetchall()}:
        # `document_chunk.embedding vector(1024)`가 계약이므로 확장이 없으면 그 계약이
        # 성립할 수 없다.
        mismatches.append("extension:vector")
    return mismatches


LIVE_COLUMN_SQL = """/* cm34:live-columns */
SELECT c.relname AS table_name, a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

PRIMARY_KEY_SQL = """/* cm34:primary-key */
SELECT a.attname AS column_name
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE n.nspname = 'public' AND c.relname = %s AND con.contype = 'p'
ORDER BY k.ord
"""


#: `'literal'` 또는 `'literal'::type` **전체**에만 맞는다. 함수 호출·연산식은 걸리지
#: 않으므로 그대로 통과한다.
QUOTED_LITERAL_DEFAULT = re.compile(
    r"^'((?:[^']|'')*)'(?:::[A-Za-z_][A-Za-z0-9_ .\"]*(?:\[\])?)?$"
)


def _canonical_default(value: str | None) -> str | None:
    """literal 기본값을 **한 표현으로** 접는다.

    registry는 `'OOC'`를, catalog는 `'OOC'::character varying`을 준다. 같은 값인데
    문자열이 달라 정상 DB가 drift로 보고됐다.

    ## 첫 `::`에서 자르면 안 된다

    초판은 문자열 안의 **첫** `::`부터 잘랐다. 그래서
    `nextval('nl_query_log_id_seq'::regclass)`가 `nextval('nl_query_log_id_seq'`로
    손상되고, `'a::b'::text` 같은 리터럴도 `'a`가 됐다. 회귀는
    `startswith("nextval(")`만 봐서 그 손상을 통과시켰다(구현리뷰 6차 권장 1).

    지금은 **전체가 quoted literal(+선택적 cast) 하나일 때만** 접는다. 그 밖의 표현은
    입력과 정확히 같은 문자열로 돌려준다 — 함수 호출은 표현이 아니라 값의 문제이므로
    비교를 그대로 해야 한다.
    """

    if value is None:
        return None
    matched = QUOTED_LITERAL_DEFAULT.fullmatch(value.strip())
    if matched is None:
        return value
    return matched.group(1).replace("''", "'")


def _live_columns(cursor: Any) -> dict[str, list[tuple[str, str, bool, str | None]]]:
    """**한 번에 읽는다.** table마다 query하면 22번 왕복하고 snapshot도 흔들린다."""

    cursor.execute(LIVE_COLUMN_SQL)
    columns: dict[str, list[tuple[str, str, bool, str | None]]] = {}
    for row in cursor.fetchall():
        columns.setdefault(str(row["table_name"]), []).append(
            (
                str(row["column_name"]),
                str(row["data_type"]),
                bool(row["nullable"]),
                None if row["column_default"] is None else str(row["column_default"]),
            )
        )
    return columns


def _primary_key(cursor: Any, table: str) -> tuple[str, ...]:
    cursor.execute(PRIMARY_KEY_SQL, (table,))
    return tuple(str(row["column_name"]) for row in cursor.fetchall())


def predecessor_projection(
    cursor: Any, *, extra_tables: Sequence[str] | None = None
) -> dict[str, str]:
    """선행 stage의 **의미 있는 형상 전체**를 값 몇 개로 접는다. read-only다.

    `predecessor_postcheck()`가 요구하는 것과 **같은 계약**을 관측 형태로 돌려준다.
    apply는 이것으로 "지금 맞는가"를 묻고, backup은 같은 값으로 "복원본이 원본과
    같은가"를 묻는다. 두 질문이 같은 정본을 쓰지 않으면 backup이 통과시키는 형상과
    apply가 요구하는 형상이 갈린다 — 실제로 갈려 있었다(구현리뷰 10차 필수 1).

    ## 왜 signature 하나로 접지 않나

    `guarded_signature()`는 `RUNTIME_TABLES` 9종만 본다. base 9의 column 순서·type·
    nullability·default·PK·FK·CHECK·index, reference/RAG object, `nl_query_log`, R03,
    View, `vector` extension, table exact allowlist, `PUBLIC` 권한은 그 밖이다. 그래서
    축을 나눠 각각 hash로 남긴다 — 어느 축이 달라졌는지가 값으로 드러난다.
    """

    import verify_bootstrap_state as verifier

    connection = _SignatureConnection(cursor)
    allowlist = sorted(set(agent_runtime.EXPECTED_ALL_TABLES) | set(extra_tables or ()))
    return {
        # Runtime 9종 — constraint·index·sequence까지.
        #
        # **정규화 후 해시한다.**
        #
        # `schema_signature_sha256()`는 catalog 원문을 해시하는데, `pg_restore`가 같은
        # predicate를 다르게 재출력하므로 그 값은 dump/restore 간 비교가 원리적으로
        # 불가능하다(구현리뷰 10차 필수 2에서 실측). 정규화된 계약 관점은 표현에
        # 흔들리지 않으면서 실제 drift는 그대로 잡는다.
        "runtime_contract_sha256": _normalized_runtime_contract(cursor),
        # base·reference·RAG의 column·object 계약
        "reference_physical_sha256": manifest_v3.hash_canonical_rows(
            [{"axis": name} for name in sorted(reference_physical_mismatches(cursor))]
            or [{"axis": "clean"}]
        ),
        # R03·View — CM-3.1 정본 판정
        "final_reference_sha256": manifest_v3.hash_canonical_rows(
            [
                {"kind": str(item.get("mismatch_kind"))}
                for item in verifier.reference_postcheck_mismatches(
                    connection,
                    profile=RUNTIME_PROFILE,
                    stage=GUARDED_STAGE,
                    action_rows_before=agent_runtime.action_history_count(connection),
                )
            ]
            or [{"kind": "clean"}]
        ),
        # table exact allowlist + PUBLIC 권한 + 행 수
        "inventory_sha256": _inventory_projection(cursor, allowlist),
        # **owner·ACL 전체.** CM-3.1이 고정한 보안 상태가 복구되는지까지 본다.
        "security_sha256": security_projection(
            cursor, exclude=contract.CHECKPOINT_TABLES
        ),
    }


def _normalized_runtime_contract(cursor: Any) -> str:
    """Runtime 9종의 **정규화된** column·constraint·index 계약을 한 hash로.

    `normalize_catalog_text()`는 cast와 불필요한 괄호를 접으므로 deparser 표현 차이에
    흔들리지 않는다. 정의가 실제로 바뀌면 그대로 달라진다.
    """

    signature = agent_runtime.build_schema_signature(_SignatureConnection(cursor))
    return manifest_v3.hash_canonical_rows(
        [
            {
                "kind": kind,
                "row": {
                    key: agent_runtime.normalize_catalog_text(value)
                    if isinstance(value, str)
                    else value
                    for key, value in sorted(dict(row).items())
                },
            }
            for kind in ("columns", "constraints", "indexes", "sequences")
            for row in signature.get(kind, [])
        ]
    )


SECURITY_PROJECTION_SQL = """/* cm34:security-projection */
SELECT c.relname AS object_name,
       c.relkind::text AS object_kind,
       pg_get_userbyid(c.relowner) AS owner,
       coalesce(
           (
               SELECT array_agg(
                          a.grantor::regrole::text || '>' ||
                          a.grantee::regrole::text || ':' ||
                          a.privilege_type || ':' || a.is_grantable::text
                          ORDER BY 1
                      )
               FROM aclexplode(c.relacl) AS a
           ),
           ARRAY[]::text[]
       ) AS relation_acl,
       coalesce(
           (
               SELECT array_agg(
                          att.attname || '|' ||
                          ca.grantor::regrole::text || '>' ||
                          ca.grantee::regrole::text || ':' ||
                          ca.privilege_type || ':' || ca.is_grantable::text
                          ORDER BY 1
                      )
               FROM pg_attribute att
               CROSS JOIN LATERAL aclexplode(att.attacl) AS ca
               WHERE att.attrelid = c.oid AND att.attnum > 0
                 AND NOT att.attisdropped
           ),
           ARRAY[]::text[]
       ) AS column_acl
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'S', 'p')
ORDER BY 1
"""


def security_projection(cursor: Any, *, exclude: Sequence[str] = ()) -> str:
    """public schema **전체 object**의 owner·ACL을 한 hash로.

    ## 왜 `PUBLIC` grant만으로 부족한가

    `_inventory_projection()`은 ordinary table의 `PUBLIC` grant만 본다. 그것은
    "누구에게나 열려 있지 않다"는 확인이지 **"원래 주인과 원래 권한이 그대로다"**가
    아니다. `V5-CM-3.1`은 R03·View의 owner·ACL을 승인 pre-state대로 복원·검증하도록
    고정했는데, 그 상태가 사라져도 나머지 축은 같았다(구현리뷰 11차 필수 1).

    View·sequence·column ACL과 **비-PUBLIC grantee**까지 포함한다. backup이
    `PARTIAL`의 유일한 복구 수단이라면, 복원본이 owner·권한까지 원본이어야 한다.

    ## grantor·grant option까지 담는다

    초판은 grantee와 privilege type만 hash했다. 그러면 아래 둘이 **같은 projection**이
    된다 — restore에서 grant option이 붙거나 사라져도 통과한다(구현리뷰 12차 필수 1).

    ```sql
    GRANT SELECT ON x TO r;
    GRANT SELECT ON x TO r WITH GRANT OPTION;
    ```

    `grantor>grantee:privilege:grantable`을 한 문자열로 정렬해 담는다.
    """

    cursor.execute(SECURITY_PROJECTION_SQL)
    skip = set(exclude)
    rows = [
        {
            "object": str(row["object_name"]),
            "kind": str(row["object_kind"]),
            "owner": str(row["owner"]),
            "relation_acl": list(row["relation_acl"]),
            "column_acl": list(row["column_acl"]),
        }
        for row in cursor.fetchall()
        if str(row["object_name"]) not in skip
    ]
    return manifest_v3.hash_canonical_rows(rows)


INVENTORY_PROJECTION_SQL = """/* cm34:inventory-projection */
SELECT c.relname AS table_name,
       coalesce(
           (
               SELECT array_agg(DISTINCT a.privilege_type ORDER BY a.privilege_type)
               FROM aclexplode(c.relacl) AS a
               WHERE a.grantee = 0
           ),
           ARRAY[]::text[]
       ) AS public_grants
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY 1
"""


def _inventory_projection(cursor: Any, allowlist: Sequence[str]) -> str:
    """table 집합·`PUBLIC` 권한·행 수를 한 hash로.

    이름 집합만 보면 계약 밖 table이 없다는 것까지만 말한다. `PUBLIC` grant와 행 수를
    함께 넣어야 "복원본이 원본과 같은 상태"가 성립한다.
    """

    cursor.execute(INVENTORY_PROJECTION_SQL)
    # **checkpoint 4종은 세지 않는다.**
    #
    # 적용이 스스로 만드는 것이므로 넣으면 적용 뒤 재실행(no-op)에서 backup이 stale로
    # 보인다. 그 4종의 형상과 ACL은 `classify_state()`가 `catalog["acl"]`로 본다 —
    # 두 번 세지 않는다.
    rows = [
        (str(r["table_name"]), list(r["public_grants"]))
        for r in cursor.fetchall()
        if str(r["table_name"]) not in contract.CHECKPOINT_TABLES
    ]
    counts: dict[str, int] = {}
    for name, _grants in rows:
        cursor.execute(f'SELECT count(*) AS c FROM "{name}"')
        counts[name] = int(cursor.fetchone()["c"])
    return manifest_v3.hash_canonical_rows(
        [
            {
                "table": name,
                "in_allowlist": name in set(allowlist),
                "public_grants": grants,
                "rows": counts[name],
            }
            for name, grants in rows
        ]
    )


def predecessor_postcheck(
    cursor: Any,
    identity: Mapping[str, Any],
    *,
    alarm_rows_before: int | None = None,
    extra_tables: Sequence[str] | None = None,
) -> Any:
    """선행 stage를 **live DB에서 full projection으로** 확인한다.

    ## 두 번 좁았다

    1차는 CM-3.3 marker 파일의 hash만 봤다 — "파일이 안 바뀌었다"까지만 말하고, 그
    파일이 서술하는 DB가 지금도 guarded인지는 말하지 못한다(2차 필수 3).

    2차는 `schema_signature_sha256()`으로 넓혔지만 그 signature는 `RUNTIME_TABLES`
    9종만 본다. base·reference·RAG table·View가 drift했거나 stray table이 있어도
    Runtime 9종만 맞으면 `setup()`을 시작할 수 있었다 — 계획 §5.3이 요구한 "guarded
    predecessor schema·manifest·marker 재확인"보다 좁다(3차 필수 4).

    그래서 CM-3.2의 `postcheck_database()`를 그대로 재사용한다. 22-table exact
    allowlist·`action_history` 0행·alarm 불변·`PUBLIC` 권한 0건이 한 번에 걸린다.
    **규칙을 다시 구현하지 않는다** — 두 벌이 되면 갈린다.
    """

    import verify_bootstrap_state as verifier

    connection = _SignatureConnection(cursor)
    before = (
        guarded_alarm_rows(cursor) if alarm_rows_before is None else alarm_rows_before
    )
    try:
        result = agent_runtime.postcheck_database(
            connection,
            alarm_rows_before=before,
            expected_constraints=severity_guard.GUARDED_CONSTRAINTS,
            extra_tables=extra_tables,
        )
    except agent_runtime.AgentRuntimeError as exc:
        raise contract.CheckpointStateError(
            "live 선행 stage가 CM-3.3 guarded 계약과 다릅니다",
            reason_code="PREDECESSOR_DRIFT",
        ) from exc

    # **reference/RAG 물리 계약도 setup 전에 본다.**
    #
    # `postcheck_database()`는 Runtime 9종과 22-table 이름 allowlist를 본다.
    # 그것만으로는
    # `document_chunk.embedding` type, R03 컬럼, `v_fdc_trace_with_context`, RAG index가
    # drift한 DB에서도 통과한다. full verifier는 setup **뒤에** 도는데 그때는 이미
    # rollback이 불가능하다(구현리뷰 4차 필수 1).
    #
    # CM-1.8의 routing helper를 그대로 쓴다 — 판정을 다시 구현하면 두 벌이 갈린다.
    import psycopg
    from sqlalchemy.exc import SQLAlchemyError

    try:
        reference = verifier.reference_postcheck_mismatches(
            connection,
            profile=RUNTIME_PROFILE,
            # **predecessor stage로 묻는다.** checkpoint가 없는 형상의 계약이다.
            stage=GUARDED_STAGE,
            action_rows_before=agent_runtime.action_history_count(connection),
        )
        physical = reference_physical_mismatches(cursor)
    except (SQLAlchemyError, psycopg.Error, verifier.UnverifiableError) as exc:
        # **계약이 요구하는 object가 없으면 driver 오류가 난다.** 그것을 그대로
        # 흘리면 `setup()` 직전 판정이 stack trace로 끝나고, 호출자는 "확인에
        # 실패했다"와 "확인 결과 drift다"를 구분할 수 없다. 둘 다 시작하지 않는다.
        raise contract.CheckpointStateError(
            "live 선행 stage의 reference/RAG 계약을 확인할 수 없습니다",
            reason_code="PREDECESSOR_DRIFT",
        ) from exc
    if reference or physical:
        raise contract.CheckpointStateError(
            "live 선행 stage의 reference/RAG 계약이 다릅니다",
            reason_code="PREDECESSOR_DRIFT",
        )
    if (
        result.schema_signature_sha256
        != identity["predecessor_schema_signature_sha256"]
    ):
        raise contract.CheckpointStateError(
            "live 선행 stage schema가 CM-3.3 guarded marker와 다릅니다",
            reason_code="PREDECESSOR_DRIFT",
        )
    return result


def _runtime_snapshot(cursor: Any) -> dict[str, Any]:
    """적용 전후 불변을 대조할 업무 object 요약.

    checkpoint setup이 Runtime·Reference·RAG·source를 건드리지 않았음을 증명한다.
    """

    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' "
        "AND table_name <> ALL(%s) ORDER BY 1",
        (list(contract.CHECKPOINT_TABLES),),
    )
    tables = [str(row["table_name"]) for row in cursor.fetchall()]
    counts: dict[str, int] = {}
    for table in tables:
        cursor.execute(f'SELECT count(*) AS c FROM "{table}"')
        counts[table] = int(cursor.fetchone()["c"])
    # **table 이름·행 수만으로는 부족하다.** 컬럼 추가·CHECK 삭제·index drop이 전부
    # 같은 요약을 낸다. signature까지 넣어야 스키마 변형을 잡는다(2차 필수 3).
    return {
        "tables": tables,
        "row_counts": counts,
        "schema_signature_sha256": guarded_signature(cursor),
    }


def run_apply(
    target: Any,
    *,
    change_reference: str,
    connect: Any = _connect,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
    backup_root: Path,
    approval_path: Path | None = None,
    require_backup: bool = True,
) -> str:
    """`ABSENT`에서만 `setup()`을 정확히 한 번 부른다.

    ## 순서 (계획 §5.3)

    ```text
    change ref → package pin → predecessor 계보 → backup 증적
    → 연결 → session advisory lock → connected identity·search_path
    → live 선행 stage postcheck → 상태 판정 → 업무 object+schema snapshot
    → receipt STARTED
    → setup()  ← 여기부터 rollback 불가
    → catalog exact postcheck → operational 0행 → ACL(owner·PUBLIC)
    → 업무 object+schema 불변 → receipt COMMITTED → marker-last
    ```

    ## 왜 `PARTIAL`에서 이어 붙이지 않나

    package는 latest version 다음부터 이어 갈 수 있다. 그러나 실패한 concurrent
    index가 invalid로 남았는지와 이전 migration이 정확히 끝났는지를 **latest version
    하나만으로 증명하지 못한다.** 복구는 승인된 backup restore가 담당한다.
    """

    change_reference = validate_change_reference(change_reference)
    contract.assert_package_contract()
    # **계보를 연결보다 먼저 읽는다.** predecessor가 없으면 접속할 이유가 없다.
    identity = predecessor_identity(target)

    from langgraph.checkpoint.postgres import PostgresSaver

    connection = connect(target)
    receipt: dict[str, Any] | None = None
    cursor = connection.cursor()
    try:
        _acquire_session_lock(cursor)
        _assert_connected_identity(cursor, target)
        # marker 파일이 아니라 **live DB**에서 선행 stage를 full projection으로 본다.
        alarm_rows = guarded_alarm_rows(cursor)
        catalog = read_catalog(cursor)
        # **재실행에서는 checkpoint table이 이미 있다.** 없는 것으로 재면 정상 no-op이
        # allowlist 위반으로 잡힌다. 물리 상태에 맞춰 허용 목록을 정한다.
        present = sorted(set(catalog["tables"]) & set(contract.CHECKPOINT_TABLES))
        predecessor_postcheck(
            cursor,
            identity,
            alarm_rows_before=alarm_rows,
            extra_tables=present or None,
        )

        # **복구 수단을 `setup()`보다 먼저 확인한다.** inventory가 필요하므로 연결
        # 뒤이지만, mutation은 아직 하나도 보내지 않았다.
        # **반환값을 받는다.** 버리면 gate를 통과시킨 증적이 적용 기록에 남지 않는다
        # (구현리뷰 10차 필수 4).
        evidence = _UNVERIFIED_EVIDENCE
        if require_backup:
            evidence = require_backup_evidence(
                target,
                change_reference,
                backup_root=backup_root,
                approval_path=approval_path or DEFAULT_APPROVAL_PATH,
                # **적용 직전 형상**과 결속한다. backup 시점 이후 DB가 바뀌었으면
                # 그 backup은 이 적용의 복구 수단이 아니다.
                shape=checkpoint_backup.observe_shape(cursor),
            )

        marker = load_marker(target.database, root=marker_root)
        state = resolve_state(catalog, marker, identity=identity)
        if state == "READY_MARKED":
            # no-op도 **verify와 같은 판정**을 쓴다. 확인 없는 no-op은 "아무 일도
            # 없었다"가 아니라 "아무것도 보지 않았다"이다. 상태 보고가 verify보다
            # 느슨하면 stale marker가 `NO_OP`으로 통과한다(2차 필수 2).
            return "NO_OP"
        if state != "ABSENT":
            _refuse(state)

        before = _runtime_snapshot(cursor)
        receipt = _save_receipt(
            {
                "artifact_type": f"{ARTIFACT_TYPE}_receipt",
                "format_version": MARKER_FORMAT_VERSION,
                "task_id": TASK_ID,
                "database": target.database,
                "status": "STARTED",
                "package_version": contract.PACKAGE_VERSION,
                "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
                "dataset_epoch": _dataset_epoch()["dataset_epoch"],
                "target_host_fingerprint": identity["target_host_fingerprint"],
                "predecessor_stage": identity["predecessor_stage"],
                "predecessor_marker_sha256": identity["predecessor_marker_sha256"],
                "predecessor_schema_signature_sha256": identity[
                    "predecessor_schema_signature_sha256"
                ],
                **evidence,
                "change_reference": change_reference,
                "started_at": _now(),
                "committed_at": None,
                "catalog_signature_sha256": None,
            },
            root=report_root,
        )

        # **여기부터 rollback이 없다.**
        PostgresSaver(connection).setup()

        # **한 번 읽어 두 판정에 쓴다.** ACL을 먼저 보는 것은 reason code 때문이다 —
        # `assert_ready()`가 먼저 돌면 PUBLIC 잔존이 일반 `DRIFT`로 뭉개진다.
        applied_catalog = read_catalog(cursor)
        if any(operational_row_counts(cursor).values()):
            raise contract.CheckpointStateError(
                "setup 직후 operational table이 비어 있지 않습니다",
                reason_code="UNEXPECTED_ROWS",
            )
        contract.assert_checkpoint_acl(applied_catalog)
        signature = contract.assert_ready(applied_catalog)
        # **적용 후에도 같은 full projection이 유지된다.** checkpoint 4개만 늘어난다.
        predecessor_postcheck(
            cursor,
            identity,
            alarm_rows_before=alarm_rows,
            extra_tables=sorted(contract.CHECKPOINT_TABLES),
        )
        if _runtime_snapshot(cursor) != before:
            raise contract.CheckpointStateError(
                "checkpoint setup이 업무 object를 바꿨습니다",
                reason_code="RUNTIME_MUTATED",
            )

        applied_at = _now()
        receipt = _save_receipt(
            {
                **receipt,
                "status": "COMMITTED",
                "committed_at": applied_at,
                "catalog_signature_sha256": signature,
            },
            root=report_root,
        )
        # **marker-last.** receipt가 먼저 COMMITTED가 되어야 복구가 성립한다.
        save_marker(
            {
                "artifact_type": ARTIFACT_TYPE,
                "format_version": MARKER_FORMAT_VERSION,
                "task_id": TASK_ID,
                "database": target.database,
                "profile": RUNTIME_PROFILE,
                "status": "APPLIED",
                "bootstrap_stage": CHECKPOINT_STAGE,
                **_dataset_epoch(),
                "package_name": contract.PACKAGE_NAME,
                "package_version": contract.PACKAGE_VERSION,
                "migration_id": contract.MIGRATION_ID,
                "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
                "migration_count": contract.MIGRATION_COUNT,
                "latest_version": contract.LATEST_VERSION,
                **identity,
                **evidence,
                "catalog_signature_sha256": signature,
                "change_reference": change_reference,
                "applied_at": applied_at,
                "recorded_at": _now(),
            },
            root=marker_root,
        )
        return "APPLIED"
    except Exception:
        if receipt is not None and receipt.get("status") == "STARTED":
            _save_receipt({**receipt, "status": "ABORTED"}, root=report_root)
        raise
    finally:
        try:
            _release_session_lock(cursor)
        finally:
            connection.close()


def run_recover_marker(
    target: Any,
    *,
    change_reference: str,
    connect: Any = _connect,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> str:
    """commit은 됐는데 marker 쓰기가 실패한 경우만 되살린다. **DB는 안 건드린다.**

    **verify를 생략하는 shortcut이 아니다.** marker를 쓰기 전에 같은 catalog·index
    validity·version·predecessor 계보·ACL postcheck를 다시 통과해야 한다.

    ## critical section이 끊겨 있었다

    2차 보완은 lock을 잡았지만 `finally`에서 lock을 푼 **뒤에** receipt 비교와
    `save_marker()`를 했고, marker 부재 확인과 receipt load는 lock 획득 **전**이었다.
    실제 구간은 이랬다.

    ```text
    marker 없음 확인 → receipt load → lock → live 확인 → unlock
    → receipt/live 비교 → marker save          ← 여기가 보호되지 않는다
    ```

    unlock과 save 사이에 다른 apply/recover가 상태나 marker를 바꿀 수 있다. 그때 두
    실행이 같은 DB에 서로 다른 증적을 남긴다(3차 필수 5).

    지금은 **읽기·판정·저장·재-read가 모두 한 lock 안**에 있다.
    """

    change_reference = validate_change_reference(change_reference)
    contract.assert_package_contract()
    identity = predecessor_identity(target)

    connection = connect(target)
    cursor = connection.cursor()
    try:
        _acquire_session_lock(cursor)
        _assert_connected_identity(cursor, target)

        # **lock을 잡은 뒤에 읽는다.** 잡기 전에 읽은 값은 저장 시점의 사실이 아니다.
        if load_marker(target.database, root=marker_root) is not None:
            raise CheckpointArtifactError(
                "checkpoint marker가 이미 있습니다", reason_code="MARKER_EXISTS"
            )
        receipt = load_receipt(target.database, root=report_root)
        if receipt is None or receipt["status"] != "COMMITTED":
            raise CheckpointArtifactError(
                "COMMITTED receipt가 없습니다", reason_code="RECEIPT_MISSING"
            )

        # apply와 같은 ACL 계약을 본다 — 사후 `GRANT ... TO PUBLIC`은 recovery에서도
        # 통과시키지 않는다. **같은 catalog 한 본을 두 판정이 나눠 쓴다.**
        recovered_catalog = read_catalog(cursor)
        contract.assert_checkpoint_acl(recovered_catalog)
        signature = contract.assert_ready(recovered_catalog)
        predecessor_postcheck(
            cursor,
            identity,
            extra_tables=sorted(contract.CHECKPOINT_TABLES),
        )

        if receipt["catalog_signature_sha256"] != signature:
            raise contract.CheckpointStateError(
                "receipt와 live catalog signature가 다릅니다", reason_code="DRIFT"
            )
        for key in IDENTITY_BOUND_KEYS:
            if receipt.get(key) != identity[key]:
                raise CheckpointArtifactError(
                    "receipt가 현재 target·계보와 다릅니다",
                    reason_code="PREDECESSOR_MISMATCH",
                )
        if receipt["change_reference"] != change_reference:
            raise CheckpointArtifactError("receipt change reference가 다릅니다")

        payload = {
            "artifact_type": ARTIFACT_TYPE,
            "format_version": MARKER_FORMAT_VERSION,
            "task_id": TASK_ID,
            "database": target.database,
            "profile": RUNTIME_PROFILE,
            "status": "APPLIED",
            "bootstrap_stage": CHECKPOINT_STAGE,
            **_dataset_epoch(),
            "package_name": contract.PACKAGE_NAME,
            "package_version": contract.PACKAGE_VERSION,
            "migration_id": contract.MIGRATION_ID,
            "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
            "migration_count": contract.MIGRATION_COUNT,
            "latest_version": contract.LATEST_VERSION,
            **identity,
            # **receipt가 담고 있던 증적 digest를 그대로 옮긴다.**
            # recover는 새로 검증하지 않는다 — apply가 통과시킨 그 값이어야 한다.
            **{key: receipt[key] for key in _UNVERIFIED_EVIDENCE},
            "catalog_signature_sha256": signature,
            "change_reference": change_reference,
            "applied_at": str(receipt["committed_at"]),
            "recorded_at": _now(),
        }
        save_marker(payload, root=marker_root)

        # **쓴 것을 다시 읽어 확인한다.** 저장이 성공했다는 것과 저장된 내용이 옳다는
        # 것은 다른 질문이다 — 부분 쓰기·경쟁 덮어쓰기가 여기서 걸린다.
        stored = load_marker(target.database, root=marker_root)
        if stored is None or marker_identity_mismatches(
            stored, identity, catalog_signature=signature
        ):
            raise CheckpointArtifactError(
                "저장된 marker가 기대와 다릅니다", reason_code="MARKER_DRIFT"
            )
        return "RECOVERED"
    finally:
        try:
            _release_session_lock(cursor)
        finally:
            connection.close()


def run_smoke(
    target: Any,
    *,
    change_reference: str,
    connect: Any = _connect,
    marker_root: Path = MARKER_ROOT,
) -> str:
    """thread를 쓰고 **연결을 닫았다 다시 열어** 읽는다(계획 §5.5).

    ## 무엇을 증명하나

    checkpoint가 프로세스 메모리가 아니라 **PostgreSQL에 남는다**는 것이다. 같은
    연결에서 읽으면 saver 캐시가 답할 수 있어 증명이 되지 않는다.

    ## 무엇을 하지 않나

    Agent graph·interrupt·approval·Tool budget·`agent_run` 연결을 만들지 않는다 —
    그것은 `V5-C-0.2` 소관이다. 독립 UUID thread 하나만 쓰고 지운다.

    `READY_MARKED`에서만 돈다. 증명서 없는 DB에 쓰기를 하지 않는다.
    """

    import uuid

    change_reference = validate_change_reference(change_reference)
    if run_verify(target, connect=connect, marker_root=marker_root) != "READY_MARKED":
        raise contract.CheckpointStateError(
            "smoke는 marker가 있는 상태에서만 실행합니다", reason_code="READY_UNMARKED"
        )

    from langgraph.checkpoint.postgres import PostgresSaver

    thread_id = f"cm34-smoke-{uuid.uuid4()}"
    namespace = "cm34_smoke"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}
    checkpoint = {
        "v": 1,
        "id": str(uuid.uuid4()),
        "ts": _now(),
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }

    def _delete_thread() -> dict[str, int]:
        """대상 thread만 지운다. FK가 없으므로 의존 순서대로 제거한다.

        **없는 row를 지우는 것은 안전한 no-op이다.** 그래서 "썼는지"를 추측하지 않고
        write를 시도한 뒤에는 항상 부른다.
        """

        cleanup = connect(target)
        try:
            cursor = cleanup.cursor()
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cursor.execute(
                    f'DELETE FROM "{table}" WHERE thread_id = %s', (thread_id,)
                )
            return operational_row_counts(cursor)
        finally:
            cleanup.close()

    connection = connect(target)
    try:
        before = operational_row_counts(connection.cursor())
    finally:
        connection.close()

    try:
        connection = connect(target)
        try:
            # **여기부터 row가 남을 수 있다.** `put()`이 쓰고 나서 raise해도
            # 마찬가지이므로, 반환 여부로 판단하지 않는다(3차 필수 6).
            PostgresSaver(connection).put(config, checkpoint, {"source": "cm34"}, {})
        finally:
            connection.close()

        # **연결을 새로 연다.** 같은 연결에서 읽으면 저장을 증명하지 못한다.
        reopened = connect(target)
        try:
            tuple_ = PostgresSaver(reopened).get_tuple(config)
            if tuple_ is None or tuple_.checkpoint["id"] != checkpoint["id"]:
                raise contract.CheckpointStateError(
                    "재개한 thread의 checkpoint를 읽지 못했습니다",
                    reason_code="SMOKE_READ_FAILED",
                )
            # 다른 thread는 0건이어야 한다 — 격리 확인.
            other = PostgresSaver(reopened).get_tuple(
                {
                    "configurable": {
                        "thread_id": f"cm34-smoke-{uuid.uuid4()}",
                        "checkpoint_ns": namespace,
                    }
                }
            )
            if other is not None:
                raise contract.CheckpointStateError(
                    "다른 thread가 격리되지 않았습니다", reason_code="SMOKE_LEAK"
                )
        finally:
            reopened.close()
    except BaseException as original:
        # **두 오류를 모두 보존한다.** cleanup 실패가 원래 원인을 덮으면 무엇이
        # 잘못됐는지 알 수 없다 — 2차 주석은 그렇게 주장했지만 제어 흐름은 달랐다.
        try:
            _delete_thread()
        except BaseException as cleanup_error:
            raise contract.CheckpointStateError(
                "smoke 실패 후 cleanup도 실패했습니다",
                reason_code="SMOKE_CLEANUP_FAILED",
            ) from cleanup_error
        raise original

    after = _delete_thread()
    if after != before:
        raise contract.CheckpointStateError(
            "smoke cleanup 뒤 행 수가 복원되지 않았습니다",
            reason_code="SMOKE_CLEANUP_FAILED",
        )
    return "OK"


def run_verify(
    target: Any, *, connect: Any = _connect, marker_root: Path = MARKER_ROOT
) -> str:
    """live catalog full 대조. **read-only다.**

    ## 두 가지 실패 방식

    물리 검증은 통과하지만 marker가 없으면 **적용 증적이 미완성**이다. 그때
    `MARKER_MISSING`·exit 1로 끝낸다 — 물리 상태는 확인하되 Task 완료로 간주하지
    않는다(계획 §5.2, 계획리뷰 필수 1).

    marker 없이 통과시키면 외부에서 우연히 만든 catalog도 완료처럼 보인다.
    """

    contract.assert_package_contract()
    connection = connect(target)
    try:
        catalog = read_catalog(connection.cursor())
    finally:
        connection.close()

    # **ACL을 먼저 본다.** `assert_ready()`가 먼저 돌면 PUBLIC 잔존이 일반 `DRIFT`로
    # 뭉개져 운영자가 어느 축인지 알 수 없다(13차 필수 3).
    contract.assert_checkpoint_acl(catalog)
    signature = contract.assert_ready(catalog)
    marker = load_marker(target.database, root=marker_root)
    if marker is None:
        raise CheckpointArtifactError(
            "checkpoint marker가 없습니다", reason_code="MARKER_MISSING"
        )
    identity = predecessor_identity(target)
    # **preflight·no-op과 같은 helper다.** 경로마다 다르게 비교하면 가장 느슨한 쪽이
    # 실효 계약이 된다 — 2차가 그랬다.
    if marker_identity_mismatches(marker, identity, catalog_signature=signature):
        raise CheckpointArtifactError(
            "checkpoint marker가 현재 target·계보와 다릅니다",
            reason_code="MARKER_DRIFT",
        )
    # **apply·recover와 같은 기준으로 본다.** verify가 더 느슨하면 "적용은 막히는데
    # 검증은 통과하는" 상태가 생긴다 — 그때 어느 쪽을 믿어야 하는지 알 수 없다.
    connection = connect(target)
    try:
        # **checkpointed stage를 본다.** checkpoint 4개를 허용 목록에 넣지 않으면 정상
        # 상태가 allowlist 위반으로 잡힌다.
        predecessor_postcheck(
            connection.cursor(),
            identity,
            extra_tables=sorted(contract.CHECKPOINT_TABLES),
        )
    finally:
        connection.close()
    return "READY_MARKED"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        mode = resolve_mode(args)
        database = assert_runtime_database(args.database)
        # 여기서부터만 자격증명을 읽는다.
        from db_target import load_bootstrap_target
        from dotenv import load_dotenv

        load_dotenv(REPOSITORY_ROOT / ".env", override=False)
        target = load_bootstrap_target(database)

        if mode == "preflight":
            state = run_preflight(target)
            print(f"CHECKPOINT_PREFLIGHT database={database} state={state}")
            return EXIT_OK
        if mode == "verify":
            state = run_verify(target)
            print(f"CHECKPOINT_VERIFY_OK database={database} state={state}")
            return EXIT_OK

        # 아래는 DB를 바꾼다 — 승인이 필요하다.
        if args.confirm_target != database:
            raise CheckpointSetupError("--confirm-target이 대상 database와 다릅니다")
        if mode == "apply":
            # **apply에서만 요구한다.** preflight·verify·smoke·recover는 DB를 바꾸지
            # 않으므로 승인 파일을 강요하지 않는다(구현리뷰 4차 필수 4).
            if args.backup_root is None:
                raise CheckpointSetupError(
                    "--apply에는 --backup-root가 필요합니다",
                    reason_code="BACKUP_MISSING",
                )
            if args.approval is None:
                # **원인 코드를 구분한다.** 기본 `CONTRACT_INVALID`는 package 계약
                # 위반과 같은 값이라, 운영자가 "승인 파일을 안 줬다"와 "package가
                # 계약과 다르다"를 로그에서 구분할 수 없다.
                raise CheckpointSetupError(
                    "--apply에는 --approval이 필요합니다",
                    reason_code="APPROVAL_MISSING",
                )
            status = run_apply(
                target,
                change_reference=args.change_ref or "",
                backup_root=args.backup_root,
                approval_path=args.approval,
            )
            print(f"CHECKPOINT_{status} database={database}")
            return EXIT_OK
        if mode == "recover":
            status = run_recover_marker(target, change_reference=args.change_ref or "")
            print(f"CHECKPOINT_{status} database={database}")
            return EXIT_OK
        if mode == "smoke":
            status = run_smoke(target, change_reference=args.change_ref or "")
            print(f"CHECKPOINT_SMOKE_{status} database={database}")
            return EXIT_OK
        raise CheckpointSetupError(f"아직 구현되지 않은 mode입니다: {mode}")
    except (
        contract.CheckpointContractError,
        contract.CheckpointStateError,
        CheckpointArtifactError,
    ) as exc:
        print(
            f"CHECKPOINT_FAIL database={args.database or 'none'} "
            f"reason={exc.reason_code}",
            file=sys.stderr,
        )
        return exc.exit_code
    except CheckpointSetupError as exc:
        print(
            f"CHECKPOINT_FAIL database={args.database or 'none'} "
            f"reason={exc.reason_code}",
            file=sys.stderr,
        )
        return exc.exit_code
    except MutationRuntimeError:
        # mode 배타 위반도 sanitized reason으로 끝낸다 — raw traceback을 남기지 않는다.
        print(
            f"CHECKPOINT_FAIL database={args.database or 'none'} "
            "reason=MODE_NOT_EXCLUSIVE",
            file=sys.stderr,
        )
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
