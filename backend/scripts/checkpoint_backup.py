"""`V5-CM-3.4` checkpoint 전용 backup·독립 restore 검증.

## 왜 전환 backup을 쓰지 않는가

`backup_orchestrator`는 `V5-CM-2.6` 전환 gate를 재사용한다. 그 gate는 **전환 이전**
형상을 서술하므로, `V5-CM-3.1`이 `v_alarm_event`를 final 계약으로 재정의한 뒤에는
세 target 모두 `TARGET_STATE_UNSUPPORTED`가 된다.

그 pin이 **낡은 것이 아니라 다른 질문**이다. compat view 검사는 "전환 준비가 됐는가"를
묻는 전환 이전 게이트이고, 우리는 이미 그 지점을 지났다. 그래서 pin을 고치는 대신
checkpoint가 필요로 하는 것만 증명하는 경로를 따로 둔다.

그리고 그 도구의 `BACKUP_TABLES`는 **base 9뿐**이다. checkpoint 적용이 되돌려야 하는
것은 `runtime_guarded` 형상 전체 — Runtime 9종의 constraint·index와 reference·RAG
객체까지다. base 9만 복원하면 복구 수단이 되지 못한다.

## 무엇을 증명하는가

`restore_verified`는 인자가 아니라 **관측 결과**다. 덤프를 격리 container에 복원한 뒤
원본과 **같은 guarded schema signature**가 나오는지 본다. 그 signature는 apply의 선행
확인이 쓰는 값과 같은 함수에서 나온다 — 복구 수단이 "적용 직전 형상"을
재현한다는 뜻이다.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3  # noqa: E402
import postgres_backup as backup  # noqa: E402

REPOSITORY_ROOT = SCRIPTS_ROOT.resolve().parents[1]

TASK_ID = "V5-CM-3.4"
FORMAT_VERSION = 1
RECEIPT_ARTIFACT_TYPE = "checkpoint_backup_receipt"
COMPLETION_ARTIFACT_TYPE = "checkpoint_backup_completion"
#: 파괴적 복구의 실행 증적. apply의 receipt·marker와 대칭이다.
RECOVERY_ARTIFACT_TYPE = "checkpoint_recovery_receipt"

#: 전환 backup 산출물과 **이름으로 구분한다.** 같은 디렉토리에 섞여도 소비자가 다른
#: 계약을 잘못 읽지 않는다.
ARCHIVE_SUFFIX = "checkpoint.dump"
RECEIPT_SUFFIX = "checkpoint.receipt.json"
COMPLETION_SUFFIX = "checkpoint.complete.json"

#: **DB 전체를 뜬다.** table allowlist를 두지 않는 것이 이 계약의 핵심이다.
#:
#: `--no-owner --no-privileges`를 쓰지 않는다. 그러면 archive에 relation owner와 role별
#: GRANT가 남지 않아, 복원해도 `V5-CM-3.1`이 고정한 보안 상태가 사라진다.
#: 그런 archive는 `PARTIAL`의 복구 수단이 아니다(구현리뷰 11차 필수 1).
DUMP_OPTIONS = ("--format=custom",)

RECEIPT_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "dataset_epoch",
        "database",
        "profile",
        "change_reference",
        "server_major",
        "client_major",
        "backup_image_digest",
        "backup_tool_version",
        "archive_sha256",
        "target_host_fingerprint",
        "predecessor_stage",
        "source_projection",
        "restored_projection",
        "restore_verified",
        "created_at",
    }
)

COMPLETION_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "dataset_epoch",
        "database",
        "change_reference",
        "archive_sha256",
        "receipt_sha256",
    }
)


#: 복구 증적의 exact key 집합. apply receipt와 같은 규칙으로 닫는다.
RECOVERY_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "dataset_epoch",
        "database",
        "change_reference",
        "target_host_fingerprint",
        "archive_sha256",
        "backup_receipt_sha256",
        "change_approval_sha256",
        "status",
        "state_before",
        "recovered_projection",
        "started_at",
        "completed_at",
    }
)

#: 복구 증적의 상태. **`STARTED`를 mutation 전에 쓴다.**
#:
#: 물리 복구는 끝났는데 증적 쓰기만 실패하면, 같은 명령을 다시 돌려도 상태 Gate가
#: `ABSENT`를 보고 거부하므로 증적만 되살릴 방법이 없다. `STARTED`가 먼저 남아 있으면
#: "복구를 시작했다"는 사실 자체는 파일에 있다 — apply의 receipt와 같은 구조다
#: (구현리뷰 13차 권장 1).
RECOVERY_STATUSES = ("STARTED", "COMMITTED", "ABORTED")


class CheckpointBackupError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _epoch() -> str:
    return str(manifest_v3.load_dataset_epoch()["dataset_epoch"])


def archive_name(database: str, change_reference: str) -> str:
    return f"{_epoch()}.{database}.{change_reference}.{ARCHIVE_SUFFIX}"


def receipt_name(database: str, change_reference: str) -> str:
    return f"{_epoch()}.{database}.{change_reference}.{RECEIPT_SUFFIX}"


def recovery_receipt_name(database: str, change_reference: str) -> str:
    return f"{_epoch()}.{database}.{change_reference}.checkpoint.recovery.json"


def completion_name(database: str, change_reference: str) -> str:
    return f"{_epoch()}.{database}.{change_reference}.{COMPLETION_SUFFIX}"


# `row_counts_sha256()`는 제거했다.
#
# 형상을 축 projection으로 바꾸면서 행 수는 `inventory_sha256` 안으로 들어갔다. 남겨
# 두면 같은 것을 두 방식으로 세는 표면이 되고, 그 표면은 아무도 부르지 않아 검증되지
# 않는다(구현리뷰 전 자체점검 4항).


def validate_receipt(payload: Mapping[str, Any], database: str) -> None:
    """strict schema. 값이 아니라 **계약**을 본다."""

    if set(payload) != RECEIPT_KEYS:
        raise CheckpointBackupError(
            "checkpoint backup receipt key 집합이 잘못됐습니다",
            reason_code="BACKUP_INVALID",
        )
    if payload["artifact_type"] != RECEIPT_ARTIFACT_TYPE:
        raise CheckpointBackupError(
            "artifact type이 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["format_version"] != FORMAT_VERSION or isinstance(
        payload["format_version"], bool
    ):
        raise CheckpointBackupError(
            "format version이 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["task_id"] != TASK_ID or payload["database"] != database:
        raise CheckpointBackupError(
            "receipt provenance가 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    if payload["dataset_epoch"] != _epoch():
        raise CheckpointBackupError(
            "dataset epoch이 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    if payload["restore_verified"] is not True:
        # restore를 확인하지 못한 backup은 복구 수단이 아니라 파일일 뿐이다.
        raise CheckpointBackupError(
            "restore를 확인하지 못했습니다", reason_code="RESTORE_NOT_VERIFIED"
        )
    # **복원본이 원본과 같은 형상인지**가 이 receipt의 핵심 주장이다.
    #
    # 축 하나만 비교하면 나머지가 손상돼도 통과한다. apply의 선행 확인이 요구하는
    # `SHAPE_KEYS` 전부를 대조한다
    # (구현리뷰 10차 필수 1 · 11차 필수 1로 security 축 추가).
    source = payload["source_projection"]
    restored = payload["restored_projection"]
    for name, value in (
        ("source_projection", source),
        ("restored_projection", restored),
    ):
        if not isinstance(value, Mapping) or set(value) != set(SHAPE_KEYS):
            raise CheckpointBackupError(
                f"{name} 축 집합이 계약과 다릅니다", reason_code="BACKUP_INVALID"
            )
        for axis in SHAPE_KEYS:
            if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(value[axis])):
                raise CheckpointBackupError(
                    f"{name}.{axis} 형식이 잘못됐습니다", reason_code="BACKUP_INVALID"
                )
    drifted = [axis for axis in SHAPE_KEYS if source[axis] != restored[axis]]
    if drifted:
        raise CheckpointBackupError(
            "복원본 형상이 원본과 다릅니다", reason_code="RESTORE_NOT_VERIFIED"
        )
    for key in ("archive_sha256", "target_host_fingerprint"):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload[key])):
            raise CheckpointBackupError(
                f"{key} 형식이 잘못됐습니다", reason_code="BACKUP_INVALID"
            )

    # **strict schema라고 부르는 이상 나머지도 닫는다**(구현리뷰 10차 권장 1).
    #
    # 이전에는 key 집합만 보고 값은 통과시켜, fixture의 `backup_image_digest="img"`도
    # 유효한 증적이었다.
    import setup_checkpoint as checkpoint

    if payload["profile"] != checkpoint.RUNTIME_PROFILE:
        raise CheckpointBackupError(
            "runtime profile이 아닙니다", reason_code="BACKUP_MISMATCH"
        )
    if payload["predecessor_stage"] != checkpoint.GUARDED_STAGE:
        raise CheckpointBackupError(
            "선행 stage가 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    # **자기 예외로 통일한다.** 호출자는 `CheckpointBackupError`만 잡으므로 다른 모듈의
    # 예외가 새어 나가면 그 자리에서 진단이 끊긴다.
    try:
        checkpoint.validate_change_reference(str(payload["change_reference"]))
    except checkpoint.CheckpointSetupError as exc:
        raise CheckpointBackupError(
            "change reference 형식이 잘못됐습니다", reason_code="BACKUP_INVALID"
        ) from exc
    for key in ("server_major", "client_major"):
        value = payload[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise CheckpointBackupError(
                f"{key}가 양의 정수가 아닙니다", reason_code="BACKUP_INVALID"
            )
    if payload["server_major"] != payload["client_major"]:
        # major가 다른 client로 뜬 덤프는 복원 호환을 보장하지 않는다.
        raise CheckpointBackupError(
            "server·client major가 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    # **하위 예외를 흘리지 않는다.** 지원하지 않는 major면 `expected_client_image()`가
    # `BackupError`를 던지는데, 호출자는 `CheckpointBackupError`만 잡으므로 변조
    # receipt가 sanitized 결과가 아니라 traceback으로 끝난다(구현리뷰 11차 권장 1).
    try:
        expected_image = backup.expected_client_image(int(payload["server_major"]))
    except backup.BackupError as exc:
        raise CheckpointBackupError(
            "지원하지 않는 server major입니다", reason_code="BACKUP_MISMATCH"
        ) from exc
    if payload["backup_image_digest"] != expected_image:
        raise CheckpointBackupError(
            "pinned client image가 아닙니다", reason_code="BACKUP_MISMATCH"
        )
    # **image와 같은 기준으로 exact 대조한다.** 비어 있지 않은지만 보면
    # `"anything-at-all"`도 통과한다(구현리뷰 11차 권장 1).
    if payload["backup_tool_version"] != backup.expected_client_version(
        int(payload["server_major"])
    ):
        raise CheckpointBackupError(
            "pinned client version이 아닙니다", reason_code="BACKUP_MISMATCH"
        )
    try:
        checkpoint._parse_instant(payload["created_at"], label="created_at")
    except checkpoint.CheckpointArtifactError as exc:
        raise CheckpointBackupError(
            "created_at 시각이 잘못됐습니다", reason_code="BACKUP_INVALID"
        ) from exc
    manifest_v3.scan_for_sensitive_values(dict(payload))


def validate_completion(payload: Mapping[str, Any], database: str) -> None:
    if set(payload) != COMPLETION_KEYS:
        raise CheckpointBackupError(
            "completion key 집합이 잘못됐습니다", reason_code="BACKUP_INVALID"
        )
    if payload["artifact_type"] != COMPLETION_ARTIFACT_TYPE:
        raise CheckpointBackupError(
            "completion artifact type이 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["format_version"] != FORMAT_VERSION or isinstance(
        payload["format_version"], bool
    ):
        raise CheckpointBackupError(
            "completion format version이 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["database"] != database or payload["dataset_epoch"] != _epoch():
        raise CheckpointBackupError(
            "completion provenance가 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    for key in ("archive_sha256", "receipt_sha256"):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload[key])):
            raise CheckpointBackupError(
                f"completion {key} 형식이 잘못됐습니다", reason_code="BACKUP_INVALID"
            )


def validate_recovery_receipt(payload: Mapping[str, Any], database: str) -> None:
    """복구 증적 strict schema. **파괴 작업의 증적이므로 값까지 닫는다.**

    이전에는 key 집합·값·시각·digest를 보는 validator도 소비자도 없어서, 손으로 쓴
    JSON 한 본이 "승인된 복구를 했다"는 주장과 구분되지 않았다(구현리뷰 13차 권장 1).
    """

    import setup_checkpoint as checkpoint

    if set(payload) != RECOVERY_KEYS:
        raise CheckpointBackupError(
            "복구 증적 key 집합이 잘못됐습니다", reason_code="BACKUP_INVALID"
        )
    if payload["artifact_type"] != RECOVERY_ARTIFACT_TYPE:
        raise CheckpointBackupError(
            "복구 증적 artifact type이 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["format_version"] != FORMAT_VERSION or isinstance(
        payload["format_version"], bool
    ):
        raise CheckpointBackupError(
            "복구 증적 format version이 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["task_id"] != TASK_ID or payload["database"] != database:
        raise CheckpointBackupError(
            "복구 증적 provenance가 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    if payload["dataset_epoch"] != _epoch():
        raise CheckpointBackupError(
            "복구 증적 dataset epoch이 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    if payload["status"] not in RECOVERY_STATUSES:
        raise CheckpointBackupError(
            "복구 증적 status가 계약과 다릅니다", reason_code="BACKUP_INVALID"
        )
    if payload["state_before"] not in RECOVERABLE_STATES:
        raise CheckpointBackupError(
            "복구 대상 상태가 아닙니다", reason_code="BACKUP_INVALID"
        )
    for key in (
        "target_host_fingerprint",
        "archive_sha256",
        "backup_receipt_sha256",
        "change_approval_sha256",
    ):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(payload[key])):
            raise CheckpointBackupError(
                f"복구 증적 {key} 형식이 잘못됐습니다", reason_code="BACKUP_INVALID"
            )
    try:
        checkpoint.validate_change_reference(str(payload["change_reference"]))
        started = checkpoint._parse_instant(payload["started_at"], label="started_at")
    except (checkpoint.CheckpointSetupError, checkpoint.CheckpointArtifactError) as exc:
        raise CheckpointBackupError(
            "복구 증적 change reference·시각이 잘못됐습니다",
            reason_code="BACKUP_INVALID",
        ) from exc

    committed = payload["status"] == "COMMITTED"
    projection = payload["recovered_projection"]
    if not committed:
        # **끝나지 않은 복구는 결과를 주장하지 않는다.**
        if projection is not None or payload["completed_at"] is not None:
            raise CheckpointBackupError(
                "미완결 복구 증적이 결과를 주장합니다", reason_code="BACKUP_INVALID"
            )
        manifest_v3.scan_for_sensitive_values(dict(payload))
        return
    if not isinstance(projection, Mapping) or set(projection) != set(SHAPE_KEYS):
        raise CheckpointBackupError(
            "복구 결과 축 집합이 계약과 다릅니다", reason_code="BACKUP_INVALID"
        )
    for axis in SHAPE_KEYS:
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(projection[axis])):
            raise CheckpointBackupError(
                f"복구 결과 {axis} 형식이 잘못됐습니다", reason_code="BACKUP_INVALID"
            )
    try:
        completed = checkpoint._parse_instant(
            payload["completed_at"], label="completed_at"
        )
    except checkpoint.CheckpointArtifactError as exc:
        raise CheckpointBackupError(
            "복구 증적 completed_at이 잘못됐습니다", reason_code="BACKUP_INVALID"
        ) from exc
    if completed < started:
        raise CheckpointBackupError(
            "복구 증적 시각 순서가 뒤집혔습니다", reason_code="BACKUP_INVALID"
        )
    manifest_v3.scan_for_sensitive_values(dict(payload))


def save_recovery_receipt(
    payload: Mapping[str, Any], *, backup_root: Path
) -> dict[str, Any]:
    """복구 증적 **한 본**을 검증하고 atomic으로 쓴다.

    completion 짝을 두지 않는다 — 같은 경로를 `STARTED` → `COMMITTED`(또는 `ABORTED`)로
    교체하므로 "파일이 없으면 시작조차 하지 않은 것"이 성립한다.
    """

    record = dict(payload)
    validate_recovery_receipt(record, str(record["database"]))
    path = backup_root / recovery_receipt_name(
        str(record["database"]), str(record["change_reference"])
    )
    # **`atomic_write_json()`을 쓰지 않는다.** 그것은 기존 파일을 덮지 않도록 막는데,
    # 여기서는 같은 경로를 `STARTED` → `COMMITTED`로 **교체**하는 것이 계약이다.
    # 대신 그 함수가 하던 trusted root 봉쇄는 직접 한다.
    try:
        path.resolve().parent.relative_to(backup_root.resolve())
    except ValueError as exc:
        raise CheckpointBackupError(
            "복구 증적 경로가 backup root 밖입니다", reason_code="BACKUP_INVALID"
        ) from exc
    manifest_v3.atomic_save_json(path, record)
    backup.fsync_directory(backup_root)
    return record


def load_recovery_evidence(
    database: str, change_reference: str, *, backup_root: Path
) -> dict[str, Any]:
    """복구 증적을 **다시 읽어** validator를 통과시킨다.

    13차 권장 1은 validator를 요구했고 그것은 만들어졌지만, 쓰기 직전 한 번만
    불렸다. 저장 뒤 status·digest·projection이 변조되거나 잘려도 어느 Gate도 읽지
    않았다 — validator는 닫혔는데 **consumer가 열려 있었다**(구현리뷰 14차 권장 1).
    """

    path = backup_root / recovery_receipt_name(database, change_reference)
    if not path.is_file():
        raise CheckpointBackupError(
            "복구 증적이 없습니다", reason_code="BACKUP_MISSING"
        )
    try:
        record = manifest_v3._load_json(path)
    except Exception as exc:  # noqa: BLE001 - 잘린 파일도 sanitized로 끝낸다
        raise CheckpointBackupError(
            "복구 증적을 읽을 수 없습니다", reason_code="BACKUP_INVALID"
        ) from exc
    if not isinstance(record, Mapping):
        raise CheckpointBackupError(
            "복구 증적이 object가 아닙니다", reason_code="BACKUP_INVALID"
        )
    validate_recovery_receipt(record, database)
    if record["change_reference"] != change_reference:
        raise CheckpointBackupError(
            "복구 증적이 다른 change ref입니다", reason_code="BACKUP_MISMATCH"
        )
    return dict(record)


#: 복구 증적을 물을 수 있는 **두 시점**.
#:
#: 정본 §3.6은 복구가 끝나면 `ABSENT`를 확인하고 §3.3 (b) 재적용으로 돌아가라고 한다.
#: 그리고 §3.7은 복구가 발생한 target을 **closure에서** 다시 확인하라고 한다. 그 두
#: 시점의 상태는 서로 다르다 — 복구 직후는 `ABSENT`, closure는 `READY_MARKED`다.
RECOVERY_EVIDENCE_STATES: tuple[str, ...] = ("ABSENT", "READY_MARKED")


def assert_recovery_evidence_state(
    cursor: Any, target: Any, checkpoint: Any, *, marker_root: Path | None = None
) -> str:
    """복구 증적을 **물을 수 있는 시점**인지 본다. read-only다.

    ## `assert_backup_source()`를 재사용하지 않는다

    그 helper는 predecessor archive 발급용이라 `ABSENT` 하나만 허용한다. 그것을
    closure Gate에 그대로 쓰면 정본이 요구하는 절차가 코드상 성립하지 않는다 —
    복구 직후에는 통과하는데 **정상적으로 재적용한 뒤에는 반드시 실패**한다
    (구현리뷰 15차 필수 1).

    두 질문은 다르다. backup은 "checkpoint가 없어야 한다"를 묻고, 복구 증적은
    "이 target이 그 복구 결과 위에 서 있는가"를 묻는다.

    ## 재적용 뒤에는 marker·계보까지 본다

    `READY_MARKED`만 통과시킨다. `PARTIAL`·`DRIFT`·`READY_UNMARKED`·`MARKER_DRIFT`는
    "복구했고 정상 재적용까지 끝났다"는 주장을 뒷받침하지 못한다.
    """

    checkpoint._assert_connected_identity(cursor, target)
    catalog = checkpoint.read_catalog(cursor)
    if checkpoint.contract.classify_state(catalog) == "ABSENT":
        # 복구 직후 — 아직 재적용 전이다.
        return "ABSENT"
    marker = (
        checkpoint.load_marker(target.database)
        if marker_root is None
        else checkpoint.load_marker(target.database, root=marker_root)
    )
    identity = checkpoint.predecessor_identity(target)
    state = checkpoint.resolve_state(catalog, marker, identity=identity)
    if state != "READY_MARKED":
        raise CheckpointBackupError(
            f"복구 증적을 확인할 수 있는 상태가 아닙니다: {state}",
            reason_code="RECOVERY_EVIDENCE_INVALID",
        )
    return state


def run_verify_recovery(
    target: Any,
    *,
    change_reference: str,
    backup_root: Path,
    connect: Any = None,
    marker_root: Path | None = None,
) -> dict[str, Any]:
    """복구가 **끝났고 그 결과가 지금도 유지되는지** 확인한다. read-only다.

    closure에서 "이 target은 복구된 적이 있다"를 증적으로 인정하려면, 파일이 있다는
    사실이 아니라 그 파일이 서술하는 상태가 현재 DB와 같아야 한다.

    - `COMMITTED`가 아니면 완료 증적이 아니다 — `STARTED`는 물리 복구 도중,
      `ABORTED`는 실패다.
    - 상태는 **두 시점 중 하나**여야 한다 — 복구 직후 `ABSENT`, 재적용 뒤
      `READY_MARKED`. 그 사이 값(`PARTIAL`·`DRIFT`·`READY_UNMARKED`·`MARKER_DRIFT`)은
      거부한다.
    - 5축이 증적의 `recovered_projection`과 exact 일치해야 한다. 5축은 checkpoint
      4종을 제외하므로 재적용 뒤에도 같은 값이다 — 그것이 이 대조가 두 시점에서
      함께 성립하는 이유다.
    """

    import db_target
    import setup_checkpoint as checkpoint

    connect = connect or checkpoint._connect

    root = backup.validate_backup_root(backup_root, repository_root=REPOSITORY_ROOT)
    _, rejection = backup.backup_root_trust(root, change_ref=change_reference)
    if rejection is not None:
        raise CheckpointBackupError(
            "backup root를 신뢰할 수 없습니다", reason_code=rejection[0]
        )

    record = load_recovery_evidence(target.database, change_reference, backup_root=root)
    if record["status"] != "COMMITTED":
        raise CheckpointBackupError(
            "완료되지 않은 복구 증적입니다", reason_code="RECOVERY_INCOMPLETE"
        )
    if record["target_host_fingerprint"] != db_target.host_fingerprint(
        target.host, target.port
    ):
        raise CheckpointBackupError(
            "복구 증적이 다른 host의 것입니다", reason_code="BACKUP_MISMATCH"
        )

    connection = connect(target)
    try:
        cursor = connection.cursor()
        state = assert_recovery_evidence_state(
            cursor, target, checkpoint, marker_root=marker_root
        )
        shape = observe_shape(cursor)
    finally:
        connection.close()

    drifted = [
        axis
        for axis in SHAPE_KEYS
        if shape[axis] != record["recovered_projection"][axis]
    ]
    if drifted:
        raise CheckpointBackupError(
            "현재 형상이 복구 증적과 다릅니다", reason_code="RECOVERY_DRIFT"
        )
    # **어느 시점에서 확인했는지 함께 돌려준다.** 같은 통과라도 `ABSENT`와
    # `READY_MARKED`는 다른 사실을 뜻한다.
    return {**record, "_observed_state": state}


def dump_argv(*, database: str, out_path: str) -> tuple[str, ...]:
    """**전체 DB** 덤프 argv. table allowlist를 두지 않는다.

    전환 backup의 `dump_argv()`는 base 9만 담는다. checkpoint가 되돌려야 하는 것은
    `runtime_guarded` 형상 전체이므로 filter를 걸지 않는다. secret은 argv에 넣지 않고
    `PG*` 환경변수로만 넘긴다.
    """

    if not backup.IDENTIFIER.fullmatch(database):
        raise CheckpointBackupError(
            "database 이름이 허용 형식이 아닙니다", reason_code="BACKUP_INVALID"
        )
    return ("pg_dump", "--dbname", database, *DUMP_OPTIONS, "--file", out_path)


def restore_argv(*, database: str, archive_path: str) -> tuple[str, ...]:
    if not backup.IDENTIFIER.fullmatch(database):
        raise CheckpointBackupError(
            "database 이름이 허용 형식이 아닙니다", reason_code="BACKUP_INVALID"
        )
    # owner·ACL을 그대로 복원한다. 필요한 role은 `_prepare_roles()`가 미리 만든다.
    return ("pg_restore", "--dbname", database, "--exit-on-error", archive_path)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def observe_shape(cursor: Any, *, extra_tables: Any = None) -> dict[str, str]:
    """이 target의 **형상**을 관측한다. read-only다.

    `setup_checkpoint.predecessor_projection()`을 그대로 쓴다 — apply의 선행 확인이
    요구하는 것과 **같은 계약**이다.

    ## 초판은 이보다 훨씬 좁았다

    `guarded_signature()`(Runtime 9종) + checkpoint 제외 행 수 hash 둘뿐이었다. 그래서
    복원본에서 `document_chunk` UNIQUE·FK, base CHECK·index, R03, `v_alarm_event`,
    `vector` extension이 손상돼도 Runtime 9종과 행 수만 같으면 `restore_verified=true`가
    됐다. 구현보고의 "apply와 같은 guarded 형상" 주장이 코드와 달랐다
    (구현리뷰 10차 필수 1).
    """

    import setup_checkpoint as checkpoint

    return checkpoint.predecessor_projection(cursor, extra_tables=extra_tables)


SHAPE_KEYS: tuple[str, ...] = (
    "runtime_contract_sha256",
    "reference_physical_sha256",
    "final_reference_sha256",
    "inventory_sha256",
    "security_sha256",
)


def save_evidence(
    receipt: Mapping[str, Any], *, backup_root: Path, archive: Path
) -> dict[str, str]:
    """receipt를 먼저 쓰고 **completion을 마지막에** 쓴다.

    completion이 있으면 정상 증적이라는 뜻이므로, 그것을 먼저 쓰면 중단된 실행이 완결로
    보인다(`backup_orchestrator`와 같은 규칙).
    """

    database = str(receipt["database"])
    change_reference = str(receipt["change_reference"])
    validate_receipt(receipt, database)

    receipt_path = backup_root / receipt_name(database, change_reference)
    backup.atomic_write_json(receipt_path, dict(receipt), trusted_root=backup_root)

    completion = {
        "artifact_type": COMPLETION_ARTIFACT_TYPE,
        "format_version": FORMAT_VERSION,
        "dataset_epoch": _epoch(),
        "database": database,
        "change_reference": change_reference,
        "archive_sha256": backup.archive_digest(archive),
        "receipt_sha256": backup.archive_digest(receipt_path),
    }
    validate_completion(completion, database)
    backup.atomic_write_json(
        backup_root / completion_name(database, change_reference),
        completion,
        trusted_root=backup_root,
    )
    backup.fsync_directory(backup_root)
    return {"receipt_sha256": completion["receipt_sha256"]}


def load_evidence(
    database: str, change_reference: str, *, backup_root: Path
) -> dict[str, Any]:
    """completion → receipt → 실물 digest를 **다시 계산해** 확인한다.

    marker만 읽고 믿으면 손으로 만든 파일 하나로 통과한다.
    """

    complete = backup_root / completion_name(database, change_reference)
    receipt_path = backup_root / receipt_name(database, change_reference)
    archive = backup_root / archive_name(database, change_reference)
    for path in (complete, receipt_path, archive):
        if not path.is_file():
            raise CheckpointBackupError(
                "backup 증적이 없습니다", reason_code="BACKUP_MISSING"
            )

    completion = manifest_v3._load_json(complete)
    validate_completion(completion, database)
    if completion["change_reference"] != change_reference:
        raise CheckpointBackupError(
            "completion이 다른 change ref입니다", reason_code="BACKUP_MISMATCH"
        )
    if completion["archive_sha256"] != backup.archive_digest(archive):
        raise CheckpointBackupError(
            "archive digest가 증적과 다릅니다", reason_code="BACKUP_INVALID"
        )
    if completion["receipt_sha256"] != backup.archive_digest(receipt_path):
        raise CheckpointBackupError(
            "receipt digest가 증적과 다릅니다", reason_code="BACKUP_INVALID"
        )

    receipt = manifest_v3._load_json(receipt_path)
    validate_receipt(receipt, database)
    # **검증한 digest를 함께 돌려준다.**
    # 여기서 버리면 apply 증적에 "무엇을 확인했는지"가 남지 않는다(10차 필수 4).
    receipt = {
        **receipt,
        "_verified": {
            "archive_sha256": completion["archive_sha256"],
            "receipt_sha256": completion["receipt_sha256"],
        },
    }
    if receipt["change_reference"] != change_reference:
        raise CheckpointBackupError(
            "receipt가 다른 change ref입니다", reason_code="BACKUP_MISMATCH"
        )
    if receipt["archive_sha256"] != completion["archive_sha256"]:
        raise CheckpointBackupError(
            "receipt와 completion의 archive가 다릅니다", reason_code="BACKUP_INVALID"
        )
    return dict(receipt)


def assert_receipt_matches(
    receipt: Mapping[str, Any],
    *,
    target: Any,
    shape: Mapping[str, str],
    host_fingerprint: str,
) -> None:
    """증적이 **이 target의 것인지** 본다.

    "복원됐다"와 "지금 바꿀 target의 backup이다"는 다른 주장이다. 이름이 같은 다른
    서버의 bundle을 여기서 거른다.
    """

    if receipt["database"] != target.database or receipt["profile"] != target.profile:
        raise CheckpointBackupError(
            "receipt가 다른 target의 것입니다", reason_code="BACKUP_MISMATCH"
        )
    if receipt["target_host_fingerprint"] != host_fingerprint:
        raise CheckpointBackupError(
            "receipt가 다른 host의 것입니다", reason_code="BACKUP_MISMATCH"
        )
    source = receipt["source_projection"]
    if any(source[axis] != shape[axis] for axis in SHAPE_KEYS):
        raise CheckpointBackupError(
            "backup 시점 형상이 현재와 다릅니다", reason_code="BACKUP_STALE"
        )


#: backup source가 만족해야 하는 checkpoint 상태. **`ABSENT` 하나뿐이다.**
#:
#: 이 archive는 `runtime_checkpointed`의 **predecessor**다. checkpoint object가 섞여
#: 들어가면 §3.6 복구가 그 4종을 지운 직후 archive가 다시 만들어 `RECOVERY_INCOMPLETE`로
#: 끝난다 — 복구 수단이 아니라 복구를 막는 파일이 된다(구현리뷰 13차 필수 2).
BACKUP_SOURCE_STATE = "ABSENT"


def assert_backup_source(cursor: Any, target: Any, checkpoint: Any) -> str:
    """**연결된 곳이 대상이고 checkpoint가 없는지** 본다. read-only다.

    5축 projection의 inventory·security는 checkpoint 4종을 **의도적으로 제외한다** —
    적용 뒤 재실행에서 backup이 stale로 보이지 않게 하기 위해서다. 그래서
    projection 대조만으로는 archive에 checkpoint가 들어갔는지 알 수 없다. 그 축이
    보지 않기로 한 것을 여기서 본다.
    """

    checkpoint._assert_connected_identity(cursor, target)
    state = checkpoint.contract.classify_state(checkpoint.read_catalog(cursor))
    if state != BACKUP_SOURCE_STATE:
        raise CheckpointBackupError(
            f"backup source가 {BACKUP_SOURCE_STATE}가 아닙니다: {state}",
            reason_code="SOURCE_STATE_INVALID",
        )
    return state


CONTAINER_BACKUP_DIR = "/backups"


def run_backup(
    target: Any,
    *,
    change_reference: str,
    backup_root: Path,
    connect: Any = None,
    runner: Any = None,
    lifecycle: Any = None,
) -> dict[str, Any]:
    """덤프를 뜨고 **격리 container에 복원해** 같은 형상인지 확인한다.

    ## 순서

    ```text
    backup root 신뢰 확인 (연결 전)
      → advisory lock ─┐
        connected identity·checkpoint ABSENT 확인
        guarded predecessor postcheck
        원본 형상 관측 (read-only)
        pg_dump  (pinned client image)
        덤프 직후 ABSENT·형상 재확인        ← 같은 lock 안이라 TOCTOU가 닫힌다
      ────────────────┘ lock 해제
      → 일회용 container에 pg_restore
      → 복원본 형상 관측 → 원본과 대조   ← `restore_verified`는 이 관측의 결과다
      → receipt → completion (마지막)
    ```

    공용 DB에는 **쓰기를 하지 않는다.** 읽기와 덤프뿐이다.

    ## 왜 상태 Gate가 여기 있나

    이 archive는 `runtime_checkpointed`의 predecessor다. 그런데 5축 projection의
    inventory·security는 checkpoint 4종을 **의도적으로 제외한다.** 그래서 `READY`나
    `PARTIAL`인 DB를 떠도 source/restored projection이 같게 나오고
    `restore_verified=true`가 찍힌다 — archive에는 checkpoint table이 들어 있는데도.
    그 archive로 복구하면 4종을 지운 직후 restore가 다시 만들어 `RECOVERY_INCOMPLETE`다
    (구현리뷰 13차 필수 2).
    """

    import subprocess

    import rehearsal_postgres
    import setup_checkpoint as checkpoint

    connect = connect or checkpoint._connect
    runner = runner or subprocess.run
    lifecycle = lifecycle or rehearsal_postgres.one_off_postgres

    root = backup.validate_backup_root(backup_root, repository_root=REPOSITORY_ROOT)
    _, rejection = backup.backup_root_trust(root, change_ref=change_reference)
    if rejection is not None:
        raise CheckpointBackupError(
            "backup root를 신뢰할 수 없습니다", reason_code=rejection[0]
        )

    # --- 덤프 → 검증 → 승격 -------------------------------------------------
    #
    # **하나의 실패 경계로 묶는다.**
    #
    # 초판은 restore와 증적 발급만 감쌌다. `pg_dump --file`은 그보다 앞이라, dump가
    # 파일을 만든 뒤 non-zero로 끝나면 partial archive가 남고 다음 실행이
    # `archive.exists()`에서 영구히 막혔다. AST 회귀가 handler를 **정확히 2개**로
    # 고정해 그 누락을 정상으로 만들기까지 했다(구현리뷰 10차 필수 3).
    #
    # 이제 임시 이름에 쓰고 **검증이 끝난 뒤에만** 최종 이름으로 승격한다. 중간에
    # 무엇이 실패하든 최종 이름은 생기지 않고 임시 파일은 지워진다.
    archive = root / archive_name(target.database, change_reference)
    if archive.exists():
        # 완결된 증적을 덮지 않는다.
        raise CheckpointBackupError(
            "같은 이름의 archive가 이미 있습니다", reason_code="BACKUP_INVALID"
        )
    staging = root / f".{archive.name}.partial"
    staging.unlink(missing_ok=True)

    import db_target

    try:
        # --- 원본 관측·덤프 (read-only, **하나의 advisory lock 아래**) ----------
        #
        # apply가 쓰는 것과 같은 lock이다. 잡고 있는 동안 적용이 시작될 수 없으므로
        # "ABSENT를 확인했는데 덤프 도중 checkpoint가 생기는" TOCTOU가 닫힌다.
        connection = connect(target)
        cursor = connection.cursor()
        try:
            checkpoint._acquire_session_lock(cursor)
            assert_backup_source(cursor, target, checkpoint)
            # **predecessor archive라고 부르려면 predecessor여야 한다.**
            # guarded 계약을 통과하지 못한 형상은 복구해도 apply의 선행 확인을
            # 통과하지 못한다 — 그런 archive는 복구 수단이 아니다.
            identity = checkpoint.predecessor_identity(target)
            checkpoint.predecessor_postcheck(cursor, identity)
            cursor.execute("SHOW server_version_num")
            server_major = int(next(iter(cursor.fetchone().values()))) // 10000
            shape = observe_shape(cursor)
            roles = source_roles(cursor)

            client = backup.select_backup_client(server_major)
            child_env = backup.child_environment(
                target.password,
                host=backup.rewrite_host(target.host, sys.platform),
                port=target.port,
                user=target.username,
                database=target.database,
            )
            versions = {
                tool: backup.run_command(
                    backup.pinned_client_argv(
                        (tool, "--version"),
                        image=client.image,
                        child_env=child_env,
                        mounts={},
                    ),
                    runner=runner,
                    child_env=child_env,
                    failure_reason="BACKUP_CLIENT_UNAVAILABLE",
                    failure_exit=backup.EXIT_CONFIRM_REQUIRED,
                ).stdout.strip()
                for tool in ("pg_dump", "pg_restore")
            }
            backup.verify_client_major(
                client,
                dump_version=versions["pg_dump"],
                restore_version=versions["pg_restore"],
            )

            backup.run_command(
                backup.pinned_client_argv(
                    dump_argv(
                        database=target.database,
                        out_path=f"{CONTAINER_BACKUP_DIR}/{staging.name}",
                    ),
                    image=client.image,
                    child_env=child_env,
                    mounts={str(root.resolve()): CONTAINER_BACKUP_DIR},
                ),
                runner=runner,
                child_env=child_env,
                failure_reason="BACKUP_FAILED",
                failure_exit=backup.EXIT_MISMATCH,
            )
            if not staging.is_file():
                raise CheckpointBackupError(
                    "archive가 만들어지지 않았습니다", reason_code="BACKUP_FAILED"
                )

            # **덤프 직후 같은 lock 아래에서 다시 본다.** lock을 쥔 채 떴으므로
            # 여기서 달라졌다면 lock을 쓰지 않는 경로가 개입한 것이다.
            assert_backup_source(cursor, target, checkpoint)
            if observe_shape(cursor) != shape:
                raise CheckpointBackupError(
                    "덤프 도중 원본 형상이 바뀌었습니다",
                    reason_code="SOURCE_STATE_INVALID",
                )
        finally:
            try:
                checkpoint._release_session_lock(cursor)
            finally:
                connection.close()

        restored = _restore_and_observe(
            staging,
            database=target.database,
            client=client,
            root=root,
            runner=runner,
            lifecycle=lifecycle,
            connect=connect,
            roles=roles,
        )

        receipt = {
            "artifact_type": RECEIPT_ARTIFACT_TYPE,
            "format_version": FORMAT_VERSION,
            "task_id": TASK_ID,
            "dataset_epoch": _epoch(),
            "database": target.database,
            "profile": target.profile,
            "change_reference": change_reference,
            "server_major": server_major,
            "client_major": backup.parse_client_major(versions["pg_dump"]),
            "backup_image_digest": client.image,
            "backup_tool_version": versions["pg_dump"],
            "archive_sha256": backup.archive_digest(staging),
            "target_host_fingerprint": db_target.host_fingerprint(
                target.host, target.port
            ),
            "predecessor_stage": checkpoint.GUARDED_STAGE,
            "source_projection": dict(shape),
            "restored_projection": dict(restored),
            # **관측 결과다.** `SHAPE_KEYS`가 전부 같을 때만 True가 된다.
            "restore_verified": all(
                shape[axis] == restored[axis] for axis in SHAPE_KEYS
            ),
            "created_at": _now(),
        }
        # 검증이 끝났다 — 이제 최종 이름을 준다.
        staging.replace(archive)
        save_evidence(receipt, backup_root=root, archive=archive)
    except BaseException:
        staging.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        raise
    return receipt


ROLE_INVENTORY_SQL = """/* cm34:role-inventory */
SELECT DISTINCT role_name FROM (
    -- object owner
    SELECT pg_get_userbyid(c.relowner) AS role_name
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'S', 'p')
    UNION
    -- relation ACL의 grantee와 **grantor**
    SELECT a.grantee::regrole::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) AS a
    WHERE n.nspname = 'public' AND a.grantee <> 0
    UNION
    SELECT a.grantor::regrole::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) AS a
    WHERE n.nspname = 'public'
    UNION
    -- **column ACL**의 grantee와 grantor
    SELECT ca.grantee::regrole::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute att ON att.attrelid = c.oid
    CROSS JOIN LATERAL aclexplode(att.attacl) AS ca
    WHERE n.nspname = 'public' AND att.attnum > 0 AND NOT att.attisdropped
      AND ca.grantee <> 0
    UNION
    SELECT ca.grantor::regrole::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute att ON att.attrelid = c.oid
    CROSS JOIN LATERAL aclexplode(att.attacl) AS ca
    WHERE n.nspname = 'public' AND att.attnum > 0 AND NOT att.attisdropped
) AS roles
WHERE role_name IS NOT NULL AND role_name <> '-'
ORDER BY 1
"""


def source_roles(cursor: Any) -> list[str]:
    """archive를 복원하려면 있어야 하는 role.

    owner·relation ACL·**column ACL**의 grantee와 grantor를 전부 합친다.

    초판은 relation ACL의 grantee만 읽었다. 양성 회귀의 role이 relation GRANT와 column
    GRANT를 **둘 다** 받고 있어 우연히 준비됐을 뿐, **column GRANT만 가진 role**은
    일회용 복원 환경에 만들어지지 않아 `pg_restore`가 실패한다(구현리뷰 12차 필수 1).
    """

    cursor.execute(ROLE_INVENTORY_SQL)
    return [str(row["role_name"]) for row in cursor.fetchall()]


def _prepare_roles(cursor: Any, roles: Sequence[str]) -> None:
    """복원 대상에 **비밀번호 없는 NOLOGIN role**을 만든다.

    owner·ACL을 보존한 archive는 그 role들이 있어야 복원된다. 검증용 일회용
    container에는 없으므로 여기서 만든다 — `NOLOGIN`·비밀번호 없음이라 접속 수단이
    되지 않는다.
    """

    for role in roles:
        if not backup.IDENTIFIER.fullmatch(role):
            raise CheckpointBackupError(
                "role 이름이 허용 형식이 아닙니다", reason_code="RESTORE_FAILED"
            )
        cursor.execute(
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} NOLOGIN; END IF; END $$"
        )


def _restore_and_observe(
    archive: Path,
    *,
    database: str,
    client: Any,
    root: Path,
    runner: Any,
    lifecycle: Any,
    connect: Any,
    roles: Sequence[str] = (),
) -> dict[str, str]:
    """일회용 container에 복원하고 **같은 관측 함수**로 형상을 읽는다.

    archive가 owner·ACL을 담고 있으므로 복원 **전에** 그 role들을 만든다. 없으면
    `pg_restore`가 owner 지정과 GRANT에서 실패한다.
    """

    import db_target
    import rehearsal_postgres

    with lifecycle(
        database=database, image=rehearsal_postgres.POSTGRES_RAG_IMAGE
    ) as endpoint:
        restored_target = db_target.BootstrapTarget(
            host=endpoint.host,
            port=int(endpoint.port),
            username=endpoint.username,
            password=endpoint.password,
            database=database,
            profile="runtime",
        )
        setup = connect(restored_target)
        try:
            _prepare_roles(setup.cursor(), roles)
        finally:
            setup.close()

        child_env = backup.child_environment(
            endpoint.password,
            host=backup.rewrite_host(endpoint.host, sys.platform),
            port=int(endpoint.port),
            user=endpoint.username,
            database=database,
        )
        backup.run_command(
            backup.pinned_client_argv(
                restore_argv(
                    database=database,
                    archive_path=f"{CONTAINER_BACKUP_DIR}/{archive.name}",
                ),
                image=client.image,
                child_env=child_env,
                mounts={str(root.resolve()): CONTAINER_BACKUP_DIR},
            ),
            runner=runner,
            child_env=child_env,
            failure_reason="RESTORE_FAILED",
            failure_exit=backup.EXIT_MISMATCH,
        )
        connection = connect(restored_target)
        try:
            return observe_shape(connection.cursor())
        finally:
            connection.close()


#: 복구가 지워야 하는 checkpoint 전용 object. archive에는 없으므로 `pg_restore`가
#: 자동으로 없애 주지 않는다.
def _checkpoint_objects() -> tuple[str, ...]:
    import checkpoint_contract as contract

    return tuple(sorted(contract.CHECKPOINT_TABLES))


#: 복구가 허용되는 상태. **`PARTIAL`과 `DRIFT`만이다.**
#:
#: `ABSENT`는 되돌릴 것이 없고, `READY_MARKED`는 정상 적용본이다 — 그것을 복구하면
#: operational checkpoint를 지우는 파괴다(구현리뷰 12차 필수 2).
RECOVERABLE_STATES = frozenset({"PARTIAL", "DRIFT"})


def _assert_recoverable_state(
    cursor: Any, target: Any, receipt: Mapping[str, Any], checkpoint: Any
) -> str:
    """지금 이 target이 정말 복구 대상인지 본다. **lock 안에서 부른다.**"""

    state = checkpoint.contract.classify_state(checkpoint.read_catalog(cursor))
    if state not in RECOVERABLE_STATES:
        raise CheckpointBackupError(
            f"복구 대상 상태가 아닙니다: {state}", reason_code="RECOVERY_STATE_INVALID"
        )
    # server major가 receipt와 다르면 archive 호환을 보장할 수 없다.
    cursor.execute("SHOW server_version_num")
    major = int(next(iter(cursor.fetchone().values()))) // 10000
    if major != int(receipt["server_major"]):
        raise CheckpointBackupError(
            "현재 server major가 receipt와 다릅니다", reason_code="BACKUP_MISMATCH"
        )
    return state


def _assert_client_ready(
    client: Any, child_env: Mapping[str, str], root: Path, archive: Path, runner: Any
) -> None:
    """**DB를 건드리기 전에** 복원 도구가 실제로 동작하는지 확인한다.

    초판은 receipt의 major로 상수만 골랐다. Docker·image·client를 쓸 수 없으면
    복원 가능성을 확인하기도 전에 DB부터 바뀐다.
    """

    backup.run_command(
        backup.pinned_client_argv(
            ("pg_restore", "--version"),
            image=client.image,
            child_env=child_env,
            mounts={},
        ),
        runner=runner,
        child_env=child_env,
        failure_reason="BACKUP_CLIENT_UNAVAILABLE",
        failure_exit=backup.EXIT_CONFIRM_REQUIRED,
    )
    # archive를 읽을 수 있는지도 여기서 본다 — 목록만 뽑고 아무것도 바꾸지 않는다.
    backup.run_command(
        backup.pinned_client_argv(
            ("pg_restore", "--list", f"{CONTAINER_BACKUP_DIR}/{archive.name}"),
            image=client.image,
            child_env=child_env,
            mounts={str(root.resolve()): CONTAINER_BACKUP_DIR},
        ),
        runner=runner,
        child_env=child_env,
        failure_reason="BACKUP_INVALID",
        failure_exit=backup.EXIT_MISMATCH,
    )


def run_recover(
    target: Any,
    *,
    change_reference: str,
    backup_root: Path,
    approval_path: Path,
    connect: Any = None,
    runner: Any = None,
) -> dict[str, Any]:
    """`PARTIAL` 공용 target을 **predecessor 형상으로 되돌린다.**

    ## 왜 별도 경로가 필요한가

    `run_backup()`은 archive를 만들고 일회용 container에 복원해 보는 도구다. 공용
    target에 복구하는 mode가 없었고, 문서는 "승인된 backup restore로 간다"고만 적었다 —
    실제 장애 때 수행할 수 없는 문장이었다(구현리뷰 11차 필수 2).

    ## 왜 `pg_restore --clean`만으로 안 되는가

    predecessor archive에는 **checkpoint 4종이 없다.** `--clean`은 archive에 있는
    object만 지우므로 checkpoint-only object는 남고, `preflight`가 계속 `PARTIAL`이다.
    그래서 그 4종을 **명시적으로** 먼저 지운다. DB drop/recreate는 팀 공용 DB 전체를
    바꾸는 별도 파괴 작업이라 이 승인으로 확장하지 않는다.

    ## 순서

    ```text
    복구 승인 확인 → backup 증적 재계산 → target·host fingerprint 대조
      → 복원 도구·archive 판독 확인 (DB 변경 전)
      → advisory lock → state 재확인 → 복구 증적 STARTED
      → checkpoint object 제거 → pg_restore --clean
      → predecessor projection exact 대조 → checkpoint 0건 확인
      → 복구 증적 COMMITTED (실패 시 ABORTED)
    ```
    """

    import subprocess

    import setup_checkpoint as checkpoint

    connect = connect or checkpoint._connect
    runner = runner or subprocess.run

    root = backup.validate_backup_root(backup_root, repository_root=REPOSITORY_ROOT)
    _, rejection = backup.backup_root_trust(root, change_ref=change_reference)
    if rejection is not None:
        raise CheckpointBackupError(
            "backup root를 신뢰할 수 없습니다", reason_code=rejection[0]
        )

    # **복구는 적용과 다른 승인이다.** 같은 파일을 쓰되 별도 의사표시를 요구한다.
    approval = checkpoint._load_change_approval(
        approval_path, target.database, change_reference
    )
    if approval.get("recovery_approved") is not True:
        raise CheckpointBackupError(
            "복구가 승인되지 않았습니다", reason_code="RECOVERY_NOT_APPROVED"
        )

    started_at = _now()
    receipt = load_evidence(target.database, change_reference, backup_root=root)
    import db_target

    if receipt["target_host_fingerprint"] != db_target.host_fingerprint(
        target.host, target.port
    ):
        raise CheckpointBackupError(
            "archive가 다른 host의 것입니다", reason_code="BACKUP_MISMATCH"
        )

    archive = root / archive_name(target.database, change_reference)
    client = backup.select_backup_client(int(receipt["server_major"]))
    child_env = backup.child_environment(
        target.password,
        host=backup.rewrite_host(target.host, sys.platform),
        port=target.port,
        user=target.username,
        database=target.database,
    )

    # --- mutation 전에 전부 확인한다 ----------------------------------------
    #
    # 초판은 승인·증적만 보고 바로 `DROP ... CASCADE`했다. 그래서 `ABSENT` target을
    # 불필요하게 통째로 복원하거나 정상 `READY_MARKED` target의 operational checkpoint를
    # 지우는 오호출도 허용했다. 게다가 lock을 **restore 전에** 풀어, DROP 직후부터
    # 사후검증이 끝날 때까지 다른 apply/recover가 끼어들 수 있었다
    # (구현리뷰 12차 필수 2).
    #
    # 이제 한 session advisory lock을 **state 재확인 → DROP → restore →
    # postcheck**까지 계속 쥔다.
    _assert_client_ready(client, child_env, root, archive, runner)

    connection = connect(target)
    cursor = connection.cursor()
    record: dict[str, Any] | None = None
    try:
        checkpoint._acquire_session_lock(cursor)
        checkpoint._assert_connected_identity(cursor, target)
        # **lock을 잡은 뒤 다시 본다** — 잡기 전 판정은 TOCTOU다.
        state_before = _assert_recoverable_state(cursor, target, receipt, checkpoint)

        # **감사 계보를 apply와 대칭으로 만든다.**
        #
        # 누가 어떤 승인·어떤 archive로 언제 어떤 target을 되돌렸는지가 stdout에만
        # 남으면 파괴 작업의 증적이 사라진다. 그리고 **mutation 전에** 쓴다 — 물리
        # 복구가 끝난 뒤 증적 쓰기만 실패하면 같은 명령을 다시 돌려도 상태 Gate가
        # 거부하므로 증적만 되살릴 방법이 없다(구현리뷰 13차 권장 1).
        record = save_recovery_receipt(
            {
                "artifact_type": RECOVERY_ARTIFACT_TYPE,
                "format_version": FORMAT_VERSION,
                "task_id": TASK_ID,
                "dataset_epoch": _epoch(),
                "database": target.database,
                "change_reference": change_reference,
                "target_host_fingerprint": receipt["target_host_fingerprint"],
                "archive_sha256": receipt["archive_sha256"],
                "backup_receipt_sha256": receipt["_verified"]["receipt_sha256"],
                "change_approval_sha256": approval["_digest"],
                "status": "STARTED",
                "state_before": state_before,
                "recovered_projection": None,
                "started_at": started_at,
                "completed_at": None,
            },
            backup_root=root,
        )

        for name in _checkpoint_objects():
            cursor.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')

        backup.run_command(
            backup.pinned_client_argv(
                (
                    "pg_restore",
                    "--dbname",
                    target.database,
                    "--clean",
                    "--if-exists",
                    "--exit-on-error",
                    f"{CONTAINER_BACKUP_DIR}/{archive.name}",
                ),
                image=client.image,
                child_env=child_env,
                mounts={str(root.resolve()): CONTAINER_BACKUP_DIR},
            ),
            runner=runner,
            child_env=child_env,
            failure_reason="RESTORE_FAILED",
            failure_exit=backup.EXIT_MISMATCH,
        )

        # **같은 lock 아래에서** 사후 검증까지 끝낸다.
        restored = observe_shape(cursor)
        catalog = checkpoint.read_catalog(cursor)
        drifted = [
            axis
            for axis in SHAPE_KEYS
            if restored[axis] != receipt["source_projection"][axis]
        ]
        if drifted:
            raise CheckpointBackupError(
                "복구 결과가 backup 시점 형상과 다릅니다", reason_code="RECOVERY_DRIFT"
            )
        if catalog["tables"]:
            # checkpoint 저장소가 남아 있으면 `PARTIAL`이 그대로다.
            raise CheckpointBackupError(
                "복구 뒤에도 checkpoint object가 남아 있습니다",
                reason_code="RECOVERY_INCOMPLETE",
            )

        # 같은 경로를 `COMMITTED`로 atomic 교체한다. completion 짝을 두지 않으므로
        # "파일이 없으면 시작조차 하지 않은 것"이 그대로 성립한다.
        record = save_recovery_receipt(
            {
                **record,
                "status": "COMMITTED",
                "recovered_projection": dict(restored),
                "completed_at": _now(),
            },
            backup_root=root,
        )
    except BaseException:
        if record is not None and record["status"] == "STARTED":
            # 증적 갱신 실패가 원래 원인을 덮지 않는다.
            with contextlib.suppress(Exception):
                save_recovery_receipt({**record, "status": "ABORTED"}, backup_root=root)
        raise
    finally:
        try:
            checkpoint._release_session_lock(cursor)
        finally:
            connection.close()
    return record


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="runtime DB만 허용한다")
    parser.add_argument("--confirm-target", required=True)
    parser.add_argument("--change-ref", required=True)
    parser.add_argument(
        "--backup-root",
        required=True,
        type=Path,
        help="**저장소 밖** 절대경로, mode 0700, 소유자 본인",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="PARTIAL target을 backup 시점으로 되돌린다 (--approval 필요)",
    )
    parser.add_argument(
        "--verify-recovery",
        action="store_true",
        help="복구 증적을 다시 읽어 현재 형상과 대조한다 (read-only)",
    )
    parser.add_argument("--approval", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """공용 진입점.

    기본 mode(backup)는 공용 DB에 **읽기와 덤프만** 보낸다. `--recover`는 다르다 —
    checkpoint object를 `DROP`하고 전체 archive를 `pg_restore --clean`으로 복원하는
    **파괴 작업**이며 별도 `recovery_approved` 승인을 요구한다(구현리뷰 12차 권장 2).
    """

    import json

    import setup_checkpoint as checkpoint

    args = _parser().parse_args(argv)
    try:
        database = checkpoint.assert_runtime_database(args.database)
        if args.confirm_target != database:
            raise CheckpointBackupError(
                "--confirm-target이 대상과 다릅니다", reason_code="CONFIRM_REQUIRED"
            )
        checkpoint.validate_change_reference(args.change_ref)

        from db_target import load_bootstrap_target
        from dotenv import load_dotenv

        load_dotenv(REPOSITORY_ROOT / ".env", override=False)
        target = load_bootstrap_target(database)
        if args.recover and args.verify_recovery:
            raise CheckpointBackupError(
                "--recover와 --verify-recovery를 함께 쓰지 않습니다",
                reason_code="CONFIRM_REQUIRED",
            )
        if args.verify_recovery:
            # **read-only다.** 승인을 요구하지 않는다.
            record = run_verify_recovery(
                target,
                change_reference=args.change_ref,
                backup_root=args.backup_root,
            )
            print(
                json.dumps(
                    {
                        "status": "RECOVERY_VERIFIED",
                        "database": record["database"],
                        "change_reference": record["change_reference"],
                        "archive_sha256": record["archive_sha256"],
                        "state_before": record["state_before"],
                        # 복구 직후인지 재적용 뒤인지가 드러나야 한다.
                        "observed_state": record["_observed_state"],
                        "completed_at": record["completed_at"],
                    }
                )
            )
            return 0
        if args.recover:
            if args.approval is None:
                raise CheckpointBackupError(
                    "--recover에는 --approval이 필요합니다",
                    reason_code="APPROVAL_MISSING",
                )
            result = run_recover(
                target,
                change_reference=args.change_ref,
                backup_root=args.backup_root,
                approval_path=args.approval,
            )
            print(
                json.dumps(
                    {
                        "status": "RECOVERED",
                        **{
                            k: v
                            for k, v in result.items()
                            if k != "recovered_projection"
                        },
                    }
                )
            )
            return 0
        receipt = run_backup(
            target,
            change_reference=args.change_ref,
            backup_root=args.backup_root,
        )
    except (
        CheckpointBackupError,
        backup.BackupError,
        checkpoint.CheckpointSetupError,
        checkpoint.CheckpointArtifactError,
        checkpoint.contract.CheckpointStateError,
    ) as exc:
        print(
            json.dumps({"reason_code": exc.reason_code, "status": "FAILED"}),
            file=sys.stderr,
        )
        return backup.EXIT_MISMATCH
    except Exception as exc:  # noqa: BLE001 - 원인 코드를 하나로 통일한다
        print(
            json.dumps({"reason_code": "INTERNAL_ERROR", "status": "FAILED"}),
            file=sys.stderr,
        )
        raise SystemExit(backup.EXIT_USAGE) from exc

    # **경로·자격증명을 출력하지 않는다.** digest와 판정만 남긴다.
    print(
        json.dumps(
            {
                "status": "OK",
                "database": receipt["database"],
                "change_reference": receipt["change_reference"],
                "archive_sha256": receipt["archive_sha256"],
                "restore_verified": receipt["restore_verified"],
            }
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    raise SystemExit(main())
