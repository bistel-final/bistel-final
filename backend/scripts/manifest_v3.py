"""최종 epoch artifact와 bootstrap profile용 manifest v3 계약·검증기.

`V5-CM-1.8`이 active 기준을 `fdc_final_20260818`으로 전환했다. 폐기 epoch `kosa_0813`은
`infra/bootstrap/history/kosa_0813/`에만 남으며, 그 계보를 검증하는 경로는
`validate_historical_bootstrap_manifest()` **하나**다. active 검증기는 최종 epoch만
받는다 — 두 epoch을 함께 받는 union 검증을 만들면 구 artifact가 최종 경로로 다시
흘러든다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, NamedTuple

from value_normalization import VALUE_NORMALIZATION_VERSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
DATASET_EPOCH_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"
SOURCE_MANIFEST_PATH = BOOTSTRAP_ROOT / "source-data-manifest.json"
SYNTHETIC_MANIFEST_PATH = BOOTSTRAP_ROOT / "synthetic-evaluation-manifest.json"
MANIFEST_REGISTRY_ROOT = BOOTSTRAP_ROOT / "manifests"

MANIFEST_FORMAT_VERSION = 3
HASH_ALGORITHM = "sha256-canonical-json-nfc-codepoint-v1"

#: **최종 epoch.** 폐기된 `kosa_0813`은 `infra/bootstrap/history/kosa_0813/`에만 남는다.
#:
#: `V5-CM-1.8`이 `kosa_0813`에서 전환했다. `_validate_common_envelope()`가 모든
#: manifest의 `dataset_epoch`와 exact 비교하므로, 여기가 구 epoch면 최종 epoch을 담은
#: manifest가 예외 없이 거부된다.
DATASET_EPOCH = "fdc_final_20260818"

#: 최종 `project.zip`의 SHA-256. epoch v2·final intake·source manifest v4 **3자가 같아야
#: 한다**는 것은 회귀가 고정한다. 한 곳만 고치면 나머지 둘과 갈린다.
FINAL_ARCHIVE_SHA256 = (
    "e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3"
)

#: 최종 epoch profile manifest의 정정 계보. 구 `corrected-*` 계열과 구분된다.
FINAL_CORRECTION_VERSION = "final-v1"

#: epoch artifact의 현재 format. v1은 다시 허용하지 않는다 — union parser를 만들면
#: 구 artifact가 최종 경로로 다시 흘러든다.
DATASET_EPOCH_FORMAT_VERSION = 2
DATASET_EPOCH_ARTIFACT_TYPE = "dataset_epoch_registration"

#: 폐기 epoch과 그 격리 경로. epoch v2의 `supersedes`가 이 값이어야 한다.
SUPERSEDED_DATASET_EPOCH = "kosa_0813"
SUPERSEDED_ISOLATION_ROOT = "infra/bootstrap/history/kosa_0813/"

#: epoch v2의 정본 field. **형식만 보면 다른 패키지를 최종으로 등록할 수 있다**
#: (구현리뷰 필수 2). 값까지 고정한다.
FINAL_ARCHIVE_FILENAME = "project.zip"
FINAL_RECEIVED_DATE = "2026-08-18"
FINAL_INVENTORY_SCOPE = "selected_source_members"
FINAL_INTAKE_ARTIFACT = "infra/bootstrap/final-zip-intake.json"

#: 폐기 계보의 archive SHA-256. **history manifest는 이 ZIP에서만 나올 수 있다.**
#:
#: epoch 이름만 맞추면 임의의 유효한 64자리 값을 넣은 manifest가 "대체된 계보"로
#: 통과했다(구현리뷰 필수 1). 계보 재현성(NFR-06)이 깨지는 지점이라 값으로 고정한다.
SUPERSEDED_ARCHIVE_SHA256 = (
    "8bbe0bdd646290e2da300db0c293d6775927f61a35375721d4b042b239803c96"
)
SUPERSEDED_CORRECTION_VERSION = "corrected-base-v1"


#: 폐기 계보의 **불변 계약**. active registry와 완전히 독립이다.
#:
#: `V5-CM-1.8`의 후속 구현이 active `evaluation_mock`을 제거하고 `runtime_clean`의
#: schema/migration을 final 값으로 갱신한다. history 검증이
#: `BOOTSTRAP_STAGE_CONTRACTS`를 읽으면 **그 순간 과거 manifest를 검증하지 못하게
#: 된다**(구현리뷰 2차 필수 1). 계획 §3.8이 "final active registry에서
#: `evaluation_mock`이 제거되더라도 과거 lineage는 바뀌지 않는다"고 못박은 지점이다.
#:
#: `payload_sha256`은 canonical JSON(정렬 key·공백 없는 구분자)의 SHA-256이다.
#: archive 계보만 고정하면 table 삭제·columns 변조·content_hash 변조가 전부 통과한다
#: (구현리뷰 2차 필수 2).
class HistoricalStageContract(NamedTuple):
    schema_stage: str
    applied_migrations: tuple[str, ...]
    applies_to: tuple[str, ...]
    table_count: int
    payload_sha256: str


HISTORICAL_CONTRACTS: Mapping[tuple[str, str], HistoricalStageContract] = (
    MappingProxyType(
        {
            ("runtime", "runtime_clean"): HistoricalStageContract(
                schema_stage="runtime_clean",
                applied_migrations=(
                    "001_reference_extensions",
                    "002_agent_runtime_clean",
                ),
                applies_to=("kosa_agent", "kosa_agent_e2e"),
                table_count=23,
                payload_sha256=(
                    "2d7f94a1e83b966718fb030eabad8fef"
                    "48718b7ef3acccf6391fc7802ebae2e2"
                ),
            ),
            ("evaluation", "evaluation_mock"): HistoricalStageContract(
                schema_stage="reference_extensions",
                applied_migrations=("001_reference_extensions",),
                applies_to=("kosa_text2sql",),
                table_count=14,
                payload_sha256=(
                    "816a3bd810bff16960f7926aba668c0f"
                    "9db10d062f526a499976c5ab5e965405"
                ),
            ),
        }
    )
)

#: history 검증이 허용하는 **정확한 (profile, stage) 쌍**.
HISTORICAL_STAGES: Mapping[str, str] = MappingProxyType(
    {profile: stage for profile, stage in HISTORICAL_CONTRACTS}
)


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    """artifact 전체의 canonical JSON SHA-256."""

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3
EXIT_METADATA = 4
EXIT_SCHEMA = 5
EXIT_NOT_REGISTERED = 6

HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
URI_USERINFO_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s@]+@")
DATABASE_URI_PATTERN = re.compile(
    r"^(?:postgres(?:ql)?|neo4j|bolt)(?:\+[A-Za-z0-9_.-]+)?://",
    re.IGNORECASE,
)
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
FORBIDDEN_KEYS = {
    "credential",
    "database_url",
    "dsn",
    "password",
    "secret",
    "username",
}

ARTIFACT_TYPES = {
    "source_files",
    "db_bootstrap",
    "synthetic_evaluation",
}
CLI_ARTIFACT_TYPES = {
    "source-files": "source_files",
    "db-bootstrap": "db_bootstrap",
    "synthetic-evaluation": "synthetic_evaluation",
}
COMMON_ENVELOPE_KEYS = {
    "format_version",
    "artifact_type",
    "dataset_epoch",
    "source_archive_sha256",
    "correction_version",
    "hash_algorithm",
}

SOURCE_TABLE_FILES: dict[str, str] = {
    "action_history": "kosa_0813/클린데이터셋/postgres/action_history.csv",
    "evaluation": "kosa_0813/클린데이터셋/postgres/evaluation.csv",
    "fdc_trace": "kosa_0813/클린데이터셋/postgres/fdc_trace.csv",
    "lot_history": "kosa_0813/클린데이터셋/postgres/lot_history.csv",
    "metrology": "kosa_0813/클린데이터셋/postgres/metrology.csv",
    "summary_alarm_history": (
        "kosa_0813/클린데이터셋/postgres/summary_alarm_history.csv"
    ),
    "summary_data": "kosa_0813/클린데이터셋/postgres/summary_data.csv",
    "trace_alarm_history": ("kosa_0813/클린데이터셋/postgres/trace_alarm_history.csv"),
}

SOURCE_EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "action_history": (
        "action_id",
        "lot_id",
        "recipe_step_name",
        "equipment_id",
        "chamber_id",
        "trigger_alarm_lot_hist_id",
        "action_code",
        "reason",
        "approval_required",
        "approval_status",
        "approved_by",
        "approved_at",
        "notify_status",
        "notify_at",
        "mes_status",
        "mes_at",
        "created_at",
    ),
    "evaluation": (
        "lot_hist_id",
        "area",
        "equipment",
        "chamber",
        "parameter",
        "recipe",
        "lot",
        "wafer",
        "step_no",
        "step_seq",
        "point_cnt",
        "ooc_point_cnt",
        "oos_point_cnt",
        "alarm_type",
    ),
    "fdc_trace": (
        "lot_hist_id",
        "parameter_id",
        "seq_no",
        "recipe_step_no",
        "step_seq",
        "measured_at",
        "value",
    ),
    "lot_history": (
        "lot_hist_id",
        "lot_id",
        "wafer_no",
        "wafer_id",
        "device_id",
        "step_id",
        "area_id",
        "equipment_id",
        "chamber_id",
        "recipe_id",
        "track_in_at",
        "track_out_at",
        "duration_sec",
        "chamber_wafer_cum",
        "lot_seq",
        "fault_code",
    ),
    "metrology": (
        "metrology_id",
        "lot_hist_id",
        "lot_id",
        "wafer_no",
        "wafer_id",
        "step_id",
        "measure_type",
        "unit",
        "measured_value",
        "spec_center",
        "spec_lower",
        "spec_upper",
        "alarm_result",
        "measured_at",
    ),
    "summary_alarm_history": (
        "alarm_id",
        "occurred_at",
        "area",
        "equipment",
        "chamber",
        "parameter",
        "recipe",
        "lot",
        "wafer",
        "step_no",
        "step_seq",
        "statistic_type",
        "stat_value",
        "cl",
        "ucl",
        "lcl",
        "limit_type",
        "alarm_type",
    ),
    "summary_data": (
        "lot_hist_id",
        "area",
        "equipment",
        "chamber",
        "parameter",
        "recipe",
        "lot",
        "wafer",
        "step_no",
        "step_seq",
        "value_mean",
        "value_std",
        "value_min",
        "value_max",
        "point_cnt",
    ),
    "trace_alarm_history": (
        "alarm_id",
        "occurred_at",
        "area",
        "equipment",
        "chamber",
        "parameter",
        "recipe",
        "lot",
        "wafer",
        "step_no",
        "step_seq",
        "seq_no",
        "value",
        "limit_type",
        "limit_value",
        "alarm_type",
    ),
}

SCHEMA_ONLY_TABLES = frozenset({"nl_query_log"})
PROFILE_APPLIES_TO: dict[str, tuple[str, ...]] = {
    "runtime": ("kosa_agent", "kosa_agent_e2e"),
    "evaluation": ("kosa_text2sql",),
}


@dataclass(frozen=True)
class BootstrapStageContract:
    schema_stage: str
    applied_migrations: tuple[str, ...]
    #: `action_history`의 검증 정책·행 수·fixture 표기.
    #:
    #: **stage마다 다르므로 계약에 둔다.** 예전에는 `_validate_db_tables()` 본문이
    #: `("evaluation", "evaluation_mock")`을 이름으로 분기해 48행·MOCK을 못 박았다.
    #: `V5-CM-1.8`이 `evaluation_reference`(12행·REFERENCE)를 등록하면서 그 하드코딩이
    #: 새 stage를 표현하지 못하게 됐다.
    action_policy: str = "bootstrap_empty"
    action_rows: int = 0
    action_fixture_type: str | None = None


#: `V5-CM-1.8`이 등록하는 최종 migration. `apply_reference_extensions_v5.MIGRATION_ID`와
#: 같은 값이며 회귀가 두 상수를 대조한다.
FINAL_MIGRATION_ID = "v5_001_reference_extensions_final"

BOOTSTRAP_STAGE_CONTRACTS: dict[tuple[str, str], BootstrapStageContract] = {
    ("runtime", "base_schema"): BootstrapStageContract("base", ()),
    ("evaluation", "base_schema"): BootstrapStageContract("base", ()),
    ("runtime", "runtime_clean"): BootstrapStageContract(
        "runtime_clean",
        (FINAL_MIGRATION_ID, "002_agent_runtime_clean"),
    ),
    # **최종 evaluation stage.** 구 `evaluation_mock`의 48행 MOCK 특례를 재사용하지
    # 않는다. 최종 `action_history`는 실제 12행이며 `fixture_type=REFERENCE`다.
    ("evaluation", "evaluation_reference"): BootstrapStageContract(
        "reference_final",
        (FINAL_MIGRATION_ID,),
        action_policy="immutable_content",
        action_rows=12,
        action_fixture_type="REFERENCE",
    ),
}
BOOTSTRAP_MANIFEST_REGISTRY: dict[tuple[str, str], Path] = {
    key: MANIFEST_REGISTRY_ROOT / f"{key[0]}.{key[1]}.json"
    for key in BOOTSTRAP_STAGE_CONTRACTS
}


class VerificationError(RuntimeError):
    exit_code = EXIT_USAGE
    code = "INVALID_INPUT"


class ArtifactMismatchError(VerificationError):
    exit_code = EXIT_MISMATCH
    code = "MISMATCH"


class ManifestMetadataError(VerificationError):
    exit_code = EXIT_METADATA
    code = "INVALID_METADATA"


class ManifestSchemaError(VerificationError):
    exit_code = EXIT_SCHEMA
    code = "INVALID_SCHEMA"


class RetiredSourcePathError(VerificationError):
    """구 epoch 전용 경로를 최종 epoch에서 호출했다.

    `V5-CM-1.8`이 active 기준을 최종 epoch v2로 옮기면서 v1 `file_inventory`를 전제한
    source 경로가 성립하지 않게 됐다. 물리 삭제는 `V5-CM-1.7` 소관이므로 여기서는
    **fail-closed**로만 막는다(계획 §3.1).
    """


class NotRegisteredError(VerificationError):
    exit_code = EXIT_NOT_REGISTERED
    code = "NOT_REGISTERED"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _canonicalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        unicodedata.normalize("NFC", key): (
            unicodedata.normalize("NFC", value) if isinstance(value, str) else value
        )
        for key, value in row.items()
    }


def _hash_canonical_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    """DB locale과 입력 행 순서에 무관한 canonical row SHA-256."""
    row_payloads = sorted(
        json.dumps(
            _canonicalize_row(row),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        for row in rows
    )
    return hashlib.sha256(
        ("[" + ",".join(row_payloads) + "]").encode("utf-8")
    ).hexdigest()


def hash_canonical_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    """후속 artifact producer가 사용하는 canonical row hash 공개 API."""
    return _hash_canonical_rows(rows)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotRegisteredError("manifest 파일이 등록되지 않았습니다") from exc
    except json.JSONDecodeError as exc:
        raise ManifestSchemaError("manifest JSON 형식이 잘못됐습니다") from exc
    except OSError as exc:
        raise ManifestSchemaError("manifest 파일을 안전하게 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise ManifestSchemaError("manifest 최상위 값은 object여야 합니다")
    return payload


def _is_absolute_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("\\\\")
        or bool(WINDOWS_ABSOLUTE_PATH_PATTERN.match(value))
    )


def _scan_for_sensitive_values(value: Any, *, path: tuple[str, ...] = ()) -> None:
    """오류에 실제 값을 노출하지 않고 비밀·절대경로를 거부한다."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text.casefold() in FORBIDDEN_KEYS:
                raise ManifestSchemaError(
                    f"금지된 manifest key입니다: {'.'.join((*path, key_text))}"
                )
            _scan_for_sensitive_values(child, path=(*path, key_text))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_sensitive_values(child, path=(*path, str(index)))
        return
    if not isinstance(value, str):
        return
    location = ".".join(path) or "<root>"
    if URI_USERINFO_PATTERN.search(value):
        raise ManifestSchemaError(f"userinfo URI를 기록할 수 없습니다: {location}")
    if value.casefold().startswith("file://") or DATABASE_URI_PATTERN.search(value):
        raise ManifestSchemaError(f"URI/DSN을 기록할 수 없습니다: {location}")
    if _is_absolute_path(value):
        raise ManifestSchemaError(f"로컬 절대 경로를 기록할 수 없습니다: {location}")


def scan_for_sensitive_values(value: Any) -> None:
    """후속 artifact producer용 비밀정보·절대경로 검사 공개 API."""
    _scan_for_sensitive_values(value)


def _require_exact_keys(
    payload: Mapping[str, Any], expected: set[str], *, context: str
) -> None:
    actual = set(payload)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"누락={missing}")
    if extra:
        details.append(f"추가={extra}")
    raise ManifestSchemaError(f"{context} key 집합 오류: {'; '.join(details)}")


def _require_sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not HEX_SHA256_PATTERN.fullmatch(value):
        raise ManifestSchemaError(f"{context}는 64자리 소문자 SHA-256이어야 합니다")
    return value


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


#: 저장소 상대 경로로 받아들일 수 있는 문자. allowlist로 판정한다.
#:
#: **blocklist는 계속 새는 것이 확인됐다**(구현리뷰 필수 2).
#: `PurePosixPath(...).parts`만 보면 POSIX `../`만 잡혀서
#: `..\secret.json`·`postgresql://user:pass@host/db`·
#: `file:///etc/passwd`가 모두 통과했다. 앞의 둘은 Windows 개발자 환경에서 실제
#: traversal이고, 둘째는 epoch artifact에 credential을 담는 길이다.
#: 끝의 `/`는 디렉터리 표기라 허용한다 — `supersedes.isolated_to`가 그 형태다.
_RELATIVE_PATH_PATTERN = re.compile(
    r"^[A-Za-z0-9._][A-Za-z0-9._-]*(/[A-Za-z0-9._-]+)*/?$"
)


def _require_relative_path(value: Any, *, context: str) -> str:
    """저장소 상대 경로만 받는다.

    URI scheme·userinfo·drive·UNC·절대경로·`..`를 모두 거부한다.

    판정은 **문자 allowlist**다. 역슬래시가 allowlist에 없으므로 Windows 구분자를 쓴
    traversal도 별도 정규화 없이 거부된다 — 정규화 코드도 넣어 봤지만 allowlist가
    이미 전부를 덮어 어떤 변이로도 독립적으로 실패하지 않았다.
    """

    if not isinstance(value, str) or not value:
        raise ManifestSchemaError(f"{context}는 저장소 상대 경로여야 합니다")
    if (
        _is_absolute_path(value)
        or not _RELATIVE_PATH_PATTERN.fullmatch(value)
        or ".." in PurePosixPath(value).parts
    ):
        raise ManifestSchemaError(f"{context}는 저장소 상대 경로여야 합니다")
    return value


def load_dataset_epoch(path: Path = DATASET_EPOCH_PATH) -> dict[str, Any]:
    """최종 epoch registration(v2)을 읽는다.

    **v1을 다시 허용하지 않고 v1/v2 union parser도 만들지 않는다**(계획 §3.1).
    구 artifact가 최종 경로로 흘러들면 `file_inventory` 없는 v2를 "불완전한 v1"으로
    오독하거나 그 반대가 된다. 형식이 다르면 그 자리에서 거부한다.

    v1의 `file_count`·`file_inventory`는 **여기서 다루지 않는다.** 최종 intake의 선별
    member 목록은 `intake_artifact`가 가리키는 별도 artifact가 소유한다.
    """

    epoch = _load_json(path)
    _require_exact_keys(
        epoch,
        {
            "format_version",
            "artifact_type",
            "dataset_epoch",
            "received_date",
            "archive",
            "inventory_scope",
            "intake_artifact",
            "supersedes",
        },
        context="dataset epoch",
    )
    if epoch["format_version"] != DATASET_EPOCH_FORMAT_VERSION:
        raise ManifestSchemaError("dataset epoch format_version이 v2가 아닙니다")
    if epoch["artifact_type"] != DATASET_EPOCH_ARTIFACT_TYPE:
        raise ManifestSchemaError("dataset epoch artifact_type이 잘못됐습니다")
    if epoch["dataset_epoch"] != DATASET_EPOCH:
        raise ManifestMetadataError("지원하지 않는 dataset epoch입니다")

    archive = epoch["archive"]
    if not isinstance(archive, dict):
        raise ManifestSchemaError("dataset epoch archive는 object여야 합니다")
    _require_exact_keys(archive, {"filename", "sha256"}, context="archive")
    _require_sha256(archive["sha256"], context="archive.sha256")
    if archive["sha256"] != FINAL_ARCHIVE_SHA256:
        # 여기서 막지 않으면 다른 ZIP으로 만든 manifest가 최종으로 등록된다.
        raise ManifestMetadataError("dataset epoch archive가 최종 ZIP이 아닙니다")
    if archive["filename"] != FINAL_ARCHIVE_FILENAME:
        raise ManifestMetadataError("dataset epoch archive filename이 정본이 아닙니다")

    # **정본값까지 고정한다.** 형식만 보면 다른 패키지·다른 수령일을 담은 artifact가
    # 최종으로 통과한다(구현리뷰 필수 2).
    _require_relative_path(epoch["intake_artifact"], context="intake_artifact")
    if epoch["intake_artifact"] != FINAL_INTAKE_ARTIFACT:
        raise ManifestMetadataError("dataset epoch intake_artifact가 정본이 아닙니다")
    if epoch["received_date"] != FINAL_RECEIVED_DATE:
        raise ManifestMetadataError("dataset epoch received_date가 정본이 아닙니다")
    if epoch["inventory_scope"] != FINAL_INVENTORY_SCOPE:
        raise ManifestMetadataError("dataset epoch inventory_scope가 정본이 아닙니다")

    # secret scan을 여기 두지 않는다. v2의 8개 key가 모두 정본 상수로 고정돼 있어
    # 임의 문자열을 담을 자리가 없고, 실제로 어떤 변이로도 scan만 단독으로 실패하지
    # 않았다. 자리가 생기면(새 key 추가) 그때 함께 넣는다.

    superseded = epoch["supersedes"]
    if not isinstance(superseded, dict):
        raise ManifestSchemaError("dataset epoch supersedes는 object여야 합니다")
    _require_exact_keys(
        superseded, {"dataset_epoch", "isolated_to"}, context="supersedes"
    )
    if superseded["dataset_epoch"] != SUPERSEDED_DATASET_EPOCH:
        raise ManifestMetadataError("supersedes.dataset_epoch가 폐기 epoch이 아닙니다")
    if superseded["dataset_epoch"] == epoch["dataset_epoch"]:
        # 자기 자신을 대체했다고 주장할 수 없다.
        raise ManifestMetadataError("supersedes가 현재 epoch과 같습니다")
    _require_relative_path(superseded["isolated_to"], context="supersedes.isolated_to")
    if superseded["isolated_to"] != SUPERSEDED_ISOLATION_ROOT:
        raise ManifestMetadataError("폐기 epoch 격리 경로가 잘못됐습니다")
    return epoch


def verify_archive_inventory(
    archive_path: Path, epoch: Mapping[str, Any]
) -> dict[str, bytes]:
    """ZIP 전체 byte 무결성을 확인한 뒤 member bytes를 반환한다."""
    if not archive_path.is_file():
        raise ArtifactMismatchError("등록 ZIP 파일을 찾을 수 없습니다")
    try:
        archive_hash = _sha256_file(archive_path)
    except OSError as exc:
        raise ArtifactMismatchError("등록 ZIP을 안전하게 읽을 수 없습니다") from exc
    if archive_hash != epoch["archive"]["sha256"]:
        raise ArtifactMismatchError("등록 ZIP SHA-256이 dataset epoch와 다릅니다")

    expected = {entry["path"]: entry for entry in epoch["file_inventory"]}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ArtifactMismatchError("ZIP member 경로가 중복됐습니다")
            if set(names) != set(expected):
                raise ArtifactMismatchError(
                    "ZIP member inventory가 dataset epoch와 다릅니다"
                )
            contents = {}
            for info in infos:
                payload = archive.read(info)
                registered = expected[info.filename]
                if info.file_size != registered["size_bytes"]:
                    raise ArtifactMismatchError(
                        f"ZIP member 크기가 다릅니다: {info.filename}"
                    )
                if _sha256_bytes(payload) != registered["sha256"]:
                    raise ArtifactMismatchError(
                        f"ZIP member SHA-256이 다릅니다: {info.filename}"
                    )
                contents[info.filename] = payload
    except zipfile.BadZipFile as exc:
        raise ArtifactMismatchError("등록 ZIP 형식이 손상됐습니다") from exc
    except OSError as exc:
        raise ArtifactMismatchError(
            "등록 ZIP member를 안전하게 읽을 수 없습니다"
        ) from exc
    return contents


def parse_csv_bytes(
    payload: bytes, *, table: str, expected_columns: Sequence[str]
) -> tuple[list[str], list[dict[str, str]]]:
    """시작 BOM만 제거하고 모든 cell을 NFC 문자열로 읽는다."""
    try:
        text_payload = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ManifestSchemaError(f"{table}: CSV가 UTF-8이 아닙니다") from exc
    if "\ufeff" in text_payload:
        raise ManifestSchemaError(f"{table}: CSV 내부 BOM은 허용하지 않습니다")
    reader = csv.reader(io.StringIO(text_payload, newline=""), strict=True)
    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise ManifestSchemaError(f"{table}: CSV header가 없습니다") from exc
    except csv.Error as exc:
        raise ManifestSchemaError(f"{table}: CSV header를 읽을 수 없습니다") from exc

    columns = [unicodedata.normalize("NFC", column) for column in raw_header]
    if not columns or any(not column for column in columns):
        raise ManifestSchemaError(f"{table}: 빈 CSV header를 허용하지 않습니다")
    if len(columns) != len(set(columns)):
        raise ManifestSchemaError(f"{table}: 중복 CSV header를 허용하지 않습니다")
    if columns != list(expected_columns):
        raise ManifestSchemaError(f"{table}: CSV columns 계약이 다릅니다")

    rows = []
    try:
        for line_number, raw_row in enumerate(reader, start=2):
            if len(raw_row) != len(columns):
                raise ManifestSchemaError(
                    f"{table}: {line_number}행의 열 수가 header와 다릅니다"
                )
            values = [unicodedata.normalize("NFC", value) for value in raw_row]
            if any("\ufeff" in value for value in values):
                raise ManifestSchemaError(
                    f"{table}: {line_number}행에 내부 BOM이 있습니다"
                )
            rows.append(dict(zip(columns, values, strict=True)))
    except csv.Error as exc:
        raise ManifestSchemaError(f"{table}: CSV data를 읽을 수 없습니다") from exc
    return columns, rows


def build_source_manifest(
    archive_path: Path, *, epoch_path: Path = DATASET_EPOCH_PATH
) -> dict[str, Any]:
    """**최종 epoch에서는 호출할 수 없다.**

    이 함수는 epoch v1의 `file_inventory`로 ZIP member를 대조하고
    `source-data-manifest.json`을 만들던 경로다. 최종 epoch v2는 member 목록을
    `intake_artifact`가 가리키는 별도 artifact에 위임하고, source manifest 정본은
    `V5-CM-1.3`·`V5-CM-1.4`의 `build_source_manifest_v4`가
    `infra/bootstrap/source-manifest-v4.json`으로 발급한다.

    조용히 통과시키면 구 경로가 최종 artifact를 v1 source inventory로 **오독**한다.
    """

    raise RetiredSourcePathError(
        "source manifest 발급은 build_source_manifest_v4가 소유합니다",
    )


def _build_source_manifest_v1(
    archive_path: Path, *, epoch_path: Path = DATASET_EPOCH_PATH
) -> dict[str, Any]:
    """구 v1 구현. 호출자는 없으며 `V5-CM-1.7`이 물리 삭제한다."""

    epoch = load_dataset_epoch(epoch_path)
    contents = verify_archive_inventory(archive_path, epoch)
    tables = {}
    inventory_paths = {entry["path"] for entry in epoch["file_inventory"]}
    for table in sorted(SOURCE_TABLE_FILES):
        file_id = SOURCE_TABLE_FILES[table]
        if file_id not in inventory_paths or file_id not in contents:
            raise ArtifactMismatchError(f"source CSV가 inventory에 없습니다: {table}")
        columns, rows = parse_csv_bytes(
            contents[file_id],
            table=table,
            expected_columns=SOURCE_EXPECTED_COLUMNS[table],
        )
        tables[table] = {
            "file_id": file_id,
            "columns": columns,
            "row_count": len(rows),
            "content_hash": _hash_canonical_rows(rows),
        }
    manifest = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "artifact_type": "source_files",
        "dataset_epoch": epoch["dataset_epoch"],
        "source_archive_sha256": epoch["archive"]["sha256"],
        "correction_version": "none",
        "hash_algorithm": HASH_ALGORITHM,
        "tables": tables,
    }
    validate_manifest_schema(
        manifest,
        expected_artifact_type="source_files",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    return manifest


def validate_historical_bootstrap_manifest(
    manifest: Mapping[str, Any], *, profile: str, stage: str
) -> None:
    """**대체된 구 계보 `db_bootstrap` manifest 전용 검증기.**

    `V5-CM-1.8`이 active를 최종 epoch으로 올린 뒤, `V5-CM-3.1`의 migration contract는
    "무엇을 대체했는가"를 기록하려고 여전히 구 manifest를 입력으로 받는다. 그 입력은
    정의상 active validator를 통과할 수 없다.

    **`BOOTSTRAP_STAGE_CONTRACTS`를 읽지 않는다.** 이 Task의 후속 구현이 active
    `evaluation_mock`을 제거하고 `runtime_clean`을 final 값으로 갱신하는데, active
    registry를 조회하면 그 순간 과거 manifest를 검증하지 못하게 된다
    (구현리뷰 2차 필수 1). 계약은 `HISTORICAL_CONTRACTS`가 독립적으로 소유한다.

    검사는 **두 개뿐이다.** history manifest는 이미 발급이 끝난 불변 artifact이므로
    "정본과 같은가"가 곧 전부다. epoch·archive SHA·envelope·table 수를 따로 비교하는
    코드도 넣어 봤지만, canonical payload SHA-256이 그 전부를 포함해 어떤 변이로도
    독립적으로 실패하지 않았다 — 검증되지 않는 검사는 조용히 썩는다. 계보의 개별
    사실은 `HISTORICAL_CONTRACTS` 상수와 그 짝 회귀가 실물 파일과 대조해 지킨다.
    """

    contract = HISTORICAL_CONTRACTS.get((profile, stage))
    if contract is None:
        raise ManifestMetadataError("history 계보에 없는 profile/stage 조합입니다")
    if canonical_payload_sha256(manifest) != contract.payload_sha256:
        raise ManifestMetadataError("history manifest payload가 정본과 다릅니다")


def _validate_common_envelope(
    manifest: Mapping[str, Any],
    *,
    expected_artifact_type: str,
    expected_dataset_epoch: str = DATASET_EPOCH,
) -> None:
    if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
        raise ManifestMetadataError("manifest format_version이 3이 아닙니다")
    artifact_type = manifest.get("artifact_type")
    if artifact_type not in ARTIFACT_TYPES or artifact_type != expected_artifact_type:
        raise ManifestMetadataError("manifest artifact_type이 요청과 다릅니다")
    if expected_dataset_epoch not in {DATASET_EPOCH, SUPERSEDED_DATASET_EPOCH}:
        # 임의 문자열을 기대 epoch으로 받으면 검증이 무의미해진다.
        raise ManifestMetadataError("검증 가능한 dataset epoch이 아닙니다")
    if manifest.get("dataset_epoch") != expected_dataset_epoch:
        raise ManifestMetadataError("manifest dataset_epoch가 현재 기준과 다릅니다")
    if manifest.get("hash_algorithm") != HASH_ALGORITHM:
        raise ManifestMetadataError("manifest hash_algorithm이 현재 계약과 다릅니다")
    if (
        expected_artifact_type == "db_bootstrap"
        and manifest.get("value_normalization_version") != VALUE_NORMALIZATION_VERSION
    ):
        raise ManifestMetadataError(
            "manifest value_normalization_version이 현재 계약과 다릅니다"
        )
    _require_sha256(
        manifest.get("source_archive_sha256"), context="source_archive_sha256"
    )
    correction_version = manifest.get("correction_version")
    if not isinstance(correction_version, str) or not correction_version:
        raise ManifestMetadataError("manifest correction_version이 비어 있습니다")
    if artifact_type == "source_files" and correction_version != "none":
        raise ManifestMetadataError(
            "source_files correction_version은 none이어야 합니다"
        )
    if artifact_type == "db_bootstrap" and correction_version == "none":
        raise ManifestMetadataError(
            f"{artifact_type} correction_version은 보정 revision이어야 합니다"
        )


def _validate_columns(value: Any, *, context: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ManifestSchemaError(
            f"{context}.columns는 비어 있지 않은 배열이어야 합니다"
        )
    if any(not isinstance(column, str) or not column for column in value):
        raise ManifestSchemaError(f"{context}.columns 값이 잘못됐습니다")
    if len(value) != len(set(value)):
        raise ManifestSchemaError(f"{context}.columns가 중복됐습니다")
    return value


def _validate_file_tables(
    tables: Any, *, expected_tables: set[str] | frozenset[str], source: bool
) -> None:
    if not isinstance(tables, dict):
        raise ManifestSchemaError("manifest tables는 object여야 합니다")
    if set(tables) != set(expected_tables):
        raise ManifestSchemaError("file manifest table 집합이 계약과 다릅니다")
    for table, entry in tables.items():
        if not isinstance(entry, dict):
            raise ManifestSchemaError(f"{table}: table entry는 object여야 합니다")
        _require_exact_keys(
            entry,
            {"file_id", "columns", "row_count", "content_hash"},
            context=f"{table} entry",
        )
        file_id = entry["file_id"]
        if (
            not isinstance(file_id, str)
            or not file_id
            or _is_absolute_path(file_id)
            or ".." in PurePosixPath(file_id).parts
        ):
            raise ManifestSchemaError(f"{table}: file_id가 안전한 상대 경로가 아닙니다")
        if source and file_id != SOURCE_TABLE_FILES[table]:
            raise ManifestSchemaError(
                f"{table}: dataset epoch canonical file_id가 아닙니다"
            )
        columns = _validate_columns(entry["columns"], context=table)
        if source and columns != list(SOURCE_EXPECTED_COLUMNS[table]):
            raise ManifestSchemaError(f"{table}: source columns 계약이 다릅니다")
        if not _is_nonnegative_int(entry["row_count"]):
            raise ManifestSchemaError(f"{table}: row_count가 잘못됐습니다")
        if table == "action_history" and entry["row_count"] != 48:
            raise ManifestSchemaError(
                "file artifact의 action_history는 Mock/reference 48행이어야 합니다"
            )
        _require_sha256(entry["content_hash"], context=f"{table}.content_hash")


def _validate_content_columns(
    entry: Mapping[str, Any], *, table: str, policy: str
) -> None:
    """`content_columns`는 hash 대상 **부분집합**이다.

    `document.created_at`은 적재 시각(`now()`)이라 두 Runtime DB가 독립 적재하는 한
    항상 다르다. 그렇다고 그 table을 `schema_only`로 두면 3행이 0행이 돼도, 업무
    컬럼이 변조돼도 검증이 통과한다 — 검증을 없애는 것이지 해결이 아니다
    (구현리뷰 16차 필수 1).

    그래서 catalog 전체 `columns`는 그대로 두고, hash는 선언된 부분집합으로만 계산한다.
    verifier도 같은 목록으로 `SELECT`한다.
    """

    if "content_columns" not in entry:
        return
    if policy != "immutable_content":
        raise ManifestSchemaError(
            f"{table}: content_columns는 immutable_content에만 허용됩니다"
        )
    content_columns = entry["content_columns"]
    _validate_columns(content_columns, context=f"{table}.content_columns")
    columns = list(entry["columns"])
    if not set(content_columns) <= set(columns):
        raise ManifestSchemaError(
            f"{table}: content_columns가 columns의 부분집합이 아닙니다"
        )
    if list(content_columns) != [c for c in columns if c in set(content_columns)]:
        # 순서가 다르면 같은 내용에서 다른 hash가 나온다.
        raise ManifestSchemaError(f"{table}: content_columns 순서가 columns와 다릅니다")
    if len(content_columns) == len(columns):
        # 전체와 같으면 이 field가 있을 이유가 없다.
        raise ManifestSchemaError(f"{table}: content_columns가 columns와 같습니다")


def _validate_db_tables(tables: Any, *, profile: str, stage: str) -> None:
    if not isinstance(tables, dict) or not tables:
        raise ManifestSchemaError(
            "db_bootstrap tables는 비어 있지 않은 object여야 합니다"
        )
    contract = BOOTSTRAP_STAGE_CONTRACTS.get((profile, stage))
    allowed_policies = {"immutable_content", "bootstrap_empty", "schema_only"}
    for table, entry in tables.items():
        if not isinstance(table, str) or not table or not isinstance(entry, dict):
            raise ManifestSchemaError("db_bootstrap table entry가 잘못됐습니다")
        policy = entry.get("verification_policy")
        if policy not in allowed_policies:
            raise ManifestSchemaError(f"{table}: verification_policy가 잘못됐습니다")
        required = {"columns", "verification_policy"}
        if policy in {"immutable_content", "bootstrap_empty"}:
            required |= {"row_count", "content_hash"}
        if "fixture_type" in entry:
            required.add("fixture_type")
        if "content_columns" in entry:
            required.add("content_columns")
        _require_exact_keys(entry, required, context=f"{table} entry")
        _validate_columns(entry["columns"], context=table)
        _validate_content_columns(entry, table=table, policy=policy)
        if policy in {"immutable_content", "bootstrap_empty"}:
            if not _is_nonnegative_int(entry["row_count"]):
                raise ManifestSchemaError(f"{table}: row_count가 잘못됐습니다")
            if policy == "bootstrap_empty" and entry["row_count"] != 0:
                raise ManifestSchemaError(
                    f"{table}: bootstrap_empty는 0행이어야 합니다"
                )
            _require_sha256(entry["content_hash"], context=f"{table}.content_hash")
        if "fixture_type" in entry and (
            table != "action_history"
            or contract is None
            or entry["fixture_type"] != contract.action_fixture_type
        ):
            raise ManifestSchemaError(
                "fixture_type은 해당 stage의 action_history에만 허용됩니다"
            )
        if table in SCHEMA_ONLY_TABLES and policy != "schema_only":
            raise ManifestSchemaError(
                f"{table}: 누적 이력 table은 schema_only로만 검증해야 합니다"
            )

    action = tables.get("action_history")
    if action is None:
        raise ManifestSchemaError("db_bootstrap에는 action_history 기준이 필요합니다")
    if contract is None:
        raise ManifestMetadataError("허용되지 않은 profile/bootstrap_stage 조합입니다")
    # `.get()`으로 비교하면 `fixture_type: null`과 key 부재가 같아진다.
    has_fixture = "fixture_type" in action
    if (
        action.get("verification_policy") != contract.action_policy
        or action.get("row_count") != contract.action_rows
        or has_fixture != (contract.action_fixture_type is not None)
        or (has_fixture and action["fixture_type"] != contract.action_fixture_type)
    ):
        raise ManifestSchemaError("action_history가 해당 stage의 계약과 다릅니다")


def _validate_db_envelope(
    manifest: Mapping[str, Any],
    *,
    expected_profile: str | None,
    expected_stage: str | None,
) -> None:
    profile = manifest.get("profile")
    stage = manifest.get("bootstrap_stage")
    if not isinstance(profile, str) or not isinstance(stage, str):
        raise ManifestMetadataError("db profile과 bootstrap_stage가 필요합니다")
    contract = BOOTSTRAP_STAGE_CONTRACTS.get((profile, stage))
    if contract is None:
        raise ManifestMetadataError("허용되지 않은 profile/bootstrap_stage 조합입니다")
    if expected_profile is not None and profile != expected_profile:
        raise ManifestMetadataError("manifest profile이 요청과 다릅니다")
    if expected_stage is not None and stage != expected_stage:
        raise ManifestMetadataError("manifest bootstrap_stage가 요청과 다릅니다")
    applies_to = manifest.get("applies_to")
    if applies_to != list(PROFILE_APPLIES_TO[profile]):
        raise ManifestMetadataError("manifest applies_to가 profile 계약과 다릅니다")
    if manifest.get("schema_stage") != contract.schema_stage:
        raise ManifestMetadataError("manifest schema_stage가 stage 계약과 다릅니다")
    if manifest.get("applied_migrations") != list(contract.applied_migrations):
        raise ManifestMetadataError(
            "manifest applied_migrations가 stage 계약과 다릅니다"
        )


def _validate_synthetic_envelope(manifest: Mapping[str, Any]) -> None:
    if manifest.get("usage_scope") != "EVALUATION_ONLY":
        raise ManifestMetadataError(
            "synthetic usage_scope는 EVALUATION_ONLY여야 합니다"
        )
    if manifest.get("ground_truth_available") is not True:
        raise ManifestMetadataError(
            "synthetic ground_truth_available은 true여야 합니다"
        )
    if manifest.get("label_source") != "SYNTHETIC_GENERATOR":
        raise ManifestMetadataError("synthetic label_source가 잘못됐습니다")
    if manifest.get("production_ground_truth_available") is not False:
        raise ManifestMetadataError(
            "synthetic production_ground_truth_available은 false여야 합니다"
        )


def validate_manifest_schema(
    manifest: Mapping[str, Any],
    *,
    expected_artifact_type: str,
    expected_profile: str | None = None,
    expected_stage: str | None = None,
    expected_archive_sha256: str | None = None,
) -> None:
    """artifact를 외부 입력/DB 접근 전에 extra-forbid 방식으로 검증한다.

    **active epoch 전용이다.** history 계보 검증은
    `validate_historical_bootstrap_manifest()`가 별도로 소유한다 — generic validator에
    epoch 인자를 열어 두면 어떤 artifact type이든 구 epoch으로 통과시킬 수 있다
    (구현리뷰 필수 1).
    """
    _scan_for_sensitive_values(manifest)
    artifact_keys = {
        "source_files": COMMON_ENVELOPE_KEYS | {"tables"},
        "db_bootstrap": COMMON_ENVELOPE_KEYS
        | {
            "value_normalization_version",
            "profile",
            "applies_to",
            "bootstrap_stage",
            "schema_stage",
            "applied_migrations",
            "tables",
        },
        "synthetic_evaluation": COMMON_ENVELOPE_KEYS
        | {
            "usage_scope",
            "ground_truth_available",
            "label_source",
            "production_ground_truth_available",
        },
    }
    expected_keys = artifact_keys.get(expected_artifact_type)
    if expected_keys is None:
        raise ManifestMetadataError("지원하지 않는 artifact_type입니다")
    _require_exact_keys(manifest, expected_keys, context=expected_artifact_type)
    _validate_common_envelope(manifest, expected_artifact_type=expected_artifact_type)
    if (
        expected_archive_sha256 is not None
        and manifest["source_archive_sha256"] != expected_archive_sha256
    ):
        raise ManifestMetadataError(
            "manifest source_archive_sha256가 dataset epoch와 다릅니다"
        )
    if expected_artifact_type == "source_files":
        _validate_file_tables(
            manifest["tables"],
            expected_tables=set(SOURCE_TABLE_FILES),
            source=True,
        )
    elif expected_artifact_type == "db_bootstrap":
        _validate_db_envelope(
            manifest,
            expected_profile=expected_profile,
            expected_stage=expected_stage,
        )
        _validate_db_tables(
            manifest["tables"],
            profile=manifest["profile"],
            stage=manifest["bootstrap_stage"],
        )
    else:
        _validate_synthetic_envelope(manifest)


def compare_manifests(actual: Any, expected: Any, *, path: str = "$") -> list[str]:
    """값을 노출하지 않고 차이가 난 위치만 반환한다."""
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        differences = []
        actual_keys = set(actual)
        expected_keys = set(expected)
        for key in sorted(expected_keys - actual_keys):
            differences.append(f"{path}.{key}: actual 누락")
        for key in sorted(actual_keys - expected_keys):
            differences.append(f"{path}.{key}: actual 추가")
        for key in sorted(actual_keys & expected_keys):
            differences.extend(
                compare_manifests(actual[key], expected[key], path=f"{path}.{key}")
            )
        return differences
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return [f"{path}: 배열 길이 불일치"]
        differences = []
        for index, (actual_item, expected_item) in enumerate(
            zip(actual, expected, strict=True)
        ):
            differences.extend(
                compare_manifests(actual_item, expected_item, path=f"{path}[{index}]")
            )
        return differences
    return [] if actual == expected else [f"{path}: 값 불일치"]


def _atomic_save_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def atomic_save_json(path: Path, payload: Mapping[str, Any]) -> None:
    """같은 filesystem의 임시 파일을 이용해 JSON을 원자 교체한다."""
    _atomic_save_json(path, payload)


def write_manifest_with_confirmation(
    path: Path, candidate: Mapping[str, Any], *, confirm: bool
) -> int:
    if path.exists():
        differences = compare_manifests(candidate, _load_json(path))
        if not differences:
            print("source manifest가 이미 현재 dataset epoch와 일치합니다")
            return EXIT_OK
        print("source manifest 변경 미리보기:")
        for difference in differences:
            print(f"- {difference}")
    else:
        print("source manifest 신규 등록 미리보기")
    if not confirm:
        print("파일을 쓰지 않았습니다. 승인하려면 --confirm을 추가하세요")
        return EXIT_CONFIRM_REQUIRED
    try:
        _atomic_save_json(path, candidate)
    except OSError as exc:
        raise VerificationError(
            "source manifest를 안전하게 저장할 수 없습니다"
        ) from exc
    print("source manifest를 원자적으로 등록했습니다")
    return EXIT_OK


def resolve_bootstrap_manifest_path(profile: str, stage: str) -> Path:
    try:
        return BOOTSTRAP_MANIFEST_REGISTRY[(profile, stage)]
    except KeyError as exc:
        raise ManifestMetadataError(
            "허용되지 않은 profile/bootstrap_stage 조합입니다"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", choices=sorted(CLI_ARTIFACT_TYPES), required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILE_APPLIES_TO))
    parser.add_argument(
        "--stage",
        choices=sorted({stage for _, stage in BOOTSTRAP_STAGE_CONTRACTS}),
    )
    parser.add_argument("--mode", choices=("bootstrap", "steady-state"))
    targets = {target for values in PROFILE_APPLIES_TO.values() for target in values}
    parser.add_argument("--target", choices=sorted(targets))
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    return parser


def validate_cli_args(args: argparse.Namespace) -> str:
    artifact_type = CLI_ARTIFACT_TYPES[args.artifact]
    if args.confirm and not args.generate:
        raise VerificationError("--confirm은 --generate와 함께만 사용할 수 있습니다")
    if artifact_type == "source_files":
        if args.archive is None:
            raise VerificationError("source-files에는 --archive가 필요합니다")
        if any(
            value is not None
            for value in (
                args.data_dir,
                args.profile,
                args.stage,
                args.mode,
                args.target,
            )
        ):
            raise VerificationError("source-files에 DB 옵션을 사용할 수 없습니다")
    elif artifact_type == "db_bootstrap":
        if any(
            value is None
            for value in (args.profile, args.stage, args.mode, args.target)
        ):
            raise VerificationError(
                "db-bootstrap에는 --profile/--stage/--mode/--target이 필요합니다"
            )
        if args.archive is not None or args.data_dir is not None:
            raise VerificationError(
                "db-bootstrap에 file artifact 옵션을 사용할 수 없습니다"
            )
        resolve_bootstrap_manifest_path(args.profile, args.stage)
        if args.target not in PROFILE_APPLIES_TO[args.profile]:
            raise ManifestMetadataError("논리 target이 profile applies_to와 다릅니다")
    elif any(
        value is not None
        for value in (
            args.archive,
            args.data_dir,
            args.profile,
            args.stage,
            args.mode,
            args.target,
        )
    ):
        raise VerificationError(
            "synthetic-evaluation은 이번 Task에서 입력을 받지 않습니다"
        )
    return artifact_type


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifact_type = validate_cli_args(args)
    if artifact_type == "source_files":
        candidate = build_source_manifest(args.archive)
        if args.generate:
            return write_manifest_with_confirmation(
                SOURCE_MANIFEST_PATH, candidate, confirm=args.confirm
            )
        expected = _load_json(SOURCE_MANIFEST_PATH)
        validate_manifest_schema(
            expected,
            expected_artifact_type="source_files",
            expected_archive_sha256=candidate["source_archive_sha256"],
        )
        differences = compare_manifests(candidate, expected)
        if differences:
            print("source manifest 불일치:")
            for difference in differences:
                print(f"- {difference}")
            return EXIT_MISMATCH
        print("source ZIP 8개 PostgreSQL CSV가 v3 manifest와 일치합니다")
        return EXIT_OK

    if artifact_type == "db_bootstrap":
        manifest_path = resolve_bootstrap_manifest_path(args.profile, args.stage)
        if not manifest_path.exists():
            raise NotRegisteredError("선택한 DB bootstrap stage가 등록되지 않았습니다")
        manifest = _load_json(manifest_path)
        epoch = load_dataset_epoch()
        validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile=args.profile,
            expected_stage=args.stage,
            expected_archive_sha256=epoch["archive"]["sha256"],
        )
        raise NotRegisteredError("DB 연결 검증 활성화는 bootstrap 소유 Task 범위입니다")

    if not SYNTHETIC_MANIFEST_PATH.exists():
        raise NotRegisteredError("synthetic evaluation artifact가 등록되지 않았습니다")
    manifest = _load_json(SYNTHETIC_MANIFEST_PATH)
    epoch = load_dataset_epoch()
    validate_manifest_schema(
        manifest,
        expected_artifact_type="synthetic_evaluation",
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    raise NotRegisteredError("synthetic artifact 생성·검증은 평가 소유 Task 범위입니다")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except VerificationError as exc:
        print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
        return exc.exit_code
