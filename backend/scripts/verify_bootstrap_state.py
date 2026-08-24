"""최종 ZIP intake · DB · Neo4j 통합 read-only verifier.

검증 명령은 PostgreSQL과 Neo4j를 변경하지 않는다. 저장소 artifact 쓰기는
``--all``/명시적 ``--report``에만 있다.

`V5-CM-1.6`이 구 corrected 등록 경로를 제거했다. `--files-only`는 corrected build
대신 **최종 ZIP intake**를 검증한다(계획 §6.2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import apply_agent_runtime as agent_runtime
import apply_reference_extensions as reference_extensions
import apply_reference_extensions_v5 as reference_v5
import bootstrap_neo4j_graph as neo4j_bootstrap
import build_source_manifest_v4 as source_manifest_v4
import final_profile_manifests as final_manifests
import intake_final_zip as intake
import manifest_v3
from db_target import (
    ALLOWED_DATABASES,
    DATABASE_PROFILE,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    validate_url_components,
)
from dotenv import load_dotenv
from master_cypher import GraphManifestError
from neo4j.exceptions import DriverError, Neo4jError
from neo4j_target import Neo4jTargetError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    logical_type,
    normalize_db_row,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"
NEO4J_DATABASE = "neo4j"

EXIT_OK = manifest_v3.EXIT_OK
EXIT_MISMATCH = manifest_v3.EXIT_MISMATCH
EXIT_USAGE = manifest_v3.EXIT_USAGE
EXIT_CONFIRM_REQUIRED = manifest_v3.EXIT_CONFIRM_REQUIRED
EXIT_METADATA = manifest_v3.EXIT_METADATA
EXIT_SCHEMA = manifest_v3.EXIT_SCHEMA
EXIT_NOT_REGISTERED = manifest_v3.EXIT_NOT_REGISTERED
EXIT_UNVERIFIABLE = 7

EXPECTED_STAGES = {
    "kosa_agent": "runtime_clean",
    "kosa_agent_e2e": "runtime_clean",
    "kosa_text2sql": "evaluation_mock",
}
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_REGISTERED = "NOT_REGISTERED"
STATUS_UNVERIFIABLE = "UNVERIFIABLE"
STATUS_BY_EXIT = {
    EXIT_OK: STATUS_PASS,
    EXIT_MISMATCH: STATUS_FAIL,
    EXIT_NOT_REGISTERED: STATUS_NOT_REGISTERED,
    EXIT_UNVERIFIABLE: STATUS_UNVERIFIABLE,
}

PK_COLUMNS: dict[str, tuple[str, ...]] = {
    "action_history": ("action_id",),
    "dim_parameter": ("parameter_id",),
    "evaluation": ("lot_hist_id", "parameter", "step_no"),
    "fdc_trace": ("lot_hist_id", "parameter_id", "seq_no"),
    "lot_history": ("lot_hist_id",),
    "metrology": ("metrology_id",),
    "summary_alarm_history": ("alarm_id",),
    "summary_data": ("lot_hist_id", "parameter", "step_no"),
    "trace_alarm_history": ("alarm_id",),
}
#: `V5-CM-1.6`이 loader를 삭제한 stage. `V5-CM-1.8`이 `evaluation_reference`로 교체한다.
EVALUATION_MOCK_STAGE = "evaluation_mock"
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
#: **transaction의 첫 문장**. `REPEATABLE READ`가 없으면 22 table을 순회하는 동안
#: 스냅샷이 흔들려, 서로 다른 시점의 행 수·hash를 한 manifest에 섞어 넣게 된다
#: (`V5-CM-1.8` 계획 §1.2).
READ_ONLY_TRANSACTION_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
READ_ONLY_PREFIXES = (
    "SELECT ",
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    "SET TRANSACTION READ ONLY",
    "SET LOCAL ",
)


class UnverifiableError(manifest_v3.VerificationError):
    exit_code = EXIT_UNVERIFIABLE
    code = "UNVERIFIABLE"

    def __init__(self, message: str, *, reason_code: str = "UNVERIFIABLE") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class AcceptanceMismatchError(manifest_v3.ArtifactMismatchError):
    def __init__(self, message: str, *, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.details = dict(details)


@dataclass(frozen=True)
class CheckResult:
    target: str
    status: str
    exit_code: int
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "target": self.target,
            "status": self.status,
            "exit_code": self.exit_code,
            "details": self.details,
        }
        manifest_v3.scan_for_sensitive_values(payload)
        return payload


def _read_json(path: Path, *, missing: type[Exception]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise missing("등록 artifact가 없습니다") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise manifest_v3.ManifestSchemaError(
            "등록 JSON을 안전하게 읽을 수 없습니다"
        ) from exc
    if not isinstance(payload, dict):
        raise manifest_v3.ManifestSchemaError("등록 JSON 최상위 값은 object여야 합니다")
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise manifest_v3.ManifestSchemaError(
            "artifact byte hash를 계산할 수 없습니다"
        ) from exc
    return digest.hexdigest()


def _archive_path(value: Path | None, environ: Mapping[str, str]) -> Path:
    if value is not None:
        return value.expanduser()
    package = environ.get("MENTOR_PACKAGE_DIR", "").strip()
    if not package:
        raise UnverifiableError(
            "등록 ZIP 위치가 설정되지 않았습니다",
            reason_code="MISSING_CONFIGURATION",
        )
    path = Path(package).expanduser()
    return path / intake.ARCHIVE_FILENAME if path.is_dir() else path


def verify_files(
    *,
    archive_path: Path,
) -> CheckResult:
    """최종 ZIP intake gate. **corrected build를 더 이상 요구하지 않는다.**

    `V5-CM-1.6`이 구 corrected 계열을 제거하면서 파일 검사를 비우지 않고 최종 intake
    검사로 대체했다(계획 §0-2 · §6.2). 검증하는 것은 다섯 가지다.

    1. archive 전체 SHA-256이 최종 pinned 값과 일치
    2. selected member 15개가 exact 집합·hash로 일치
    3. 재구성한 intake payload가 등록 `final-zip-intake.json`과 exact 일치
    4. `dataset-epoch.json`·`source-manifest-v4.json`의 epoch·archive SHA가 같은 값
    5. 쓰기·임시 artifact·DB connector 호출 0건

    `intake_final_zip`의 pure helper를 재사용한다. final profile manifest나 source
    manifest v4 parser를 여기서 새로 만들지 않는다 — 각각 `V5-CM-1.8`·`V5-CM-1.3`
    소유다.
    """

    # **`load_dataset_epoch()`을 거치지 않는다.** `V5-CM-1.8`이 그 loader를 v2로
    # 전환했지만, 여기서 필요한 것은 세 artifact의 epoch·archive SHA 일치뿐이다.
    # loader를 태우면 같은 파일을 두 번 읽으면서 검사 주체가 갈린다(계획 §6.2-4).
    epoch = _read_json(
        manifest_v3.DATASET_EPOCH_PATH, missing=manifest_v3.NotRegisteredError
    )
    registered = _read_json(
        intake.INTAKE_ARTIFACT_PATH, missing=manifest_v3.NotRegisteredError
    )
    source = _read_json(
        source_manifest_v4.MANIFEST_V4_PATH, missing=manifest_v3.NotRegisteredError
    )
    scan = intake.read_archive(archive_path)
    rebuilt = intake.build_payload(scan)

    if rebuilt != registered:
        raise manifest_v3.ArtifactMismatchError(
            "최종 ZIP이 등록 intake artifact와 다릅니다"
        )

    # **3자 대조.** 둘만 맞으면 나머지 하나가 조용히 다른 ZIP을 가리킬 수 있다.
    archive_sha = registered["archive"]["sha256"]
    for value, label in (
        (epoch["archive"]["sha256"], "epoch archive"),
        (source["source_archive_sha256"], "source manifest archive"),
    ):
        if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(value)):
            raise manifest_v3.ManifestSchemaError(f"{label}가 SHA-256 형식이 아닙니다")
        if value != archive_sha:
            raise manifest_v3.ArtifactMismatchError(
                f"intake archive가 {label}와 다릅니다"
            )

    declared = registered["declared_target_epoch"]
    for value, label in (
        (epoch["dataset_epoch"], "epoch dataset_epoch"),
        (source["dataset_epoch"], "source manifest dataset_epoch"),
    ):
        if value != declared:
            raise manifest_v3.ArtifactMismatchError(
                f"intake epoch가 {label}와 다릅니다"
            )

    if registered["selected_count"] != intake.SELECTED_MEMBER_COUNT:
        raise manifest_v3.ArtifactMismatchError("selected member 수가 계약과 다릅니다")

    details = {
        "artifact_type": registered["artifact_type"],
        "dataset_epoch": declared,
        "archive_sha256": archive_sha,
        "selected_count": registered["selected_count"],
        "member_total": registered["member_total"],
    }
    return CheckResult("files", STATUS_PASS, EXIT_OK, details)


def _sql(connection: Any, statement: str, parameters: Any = None) -> Any:
    normalized = " ".join(statement.strip().split()).upper()
    if ";" in normalized or not normalized.startswith(READ_ONLY_PREFIXES):
        raise manifest_v3.VerificationError(
            "read-only verifier가 허용하지 않는 SQL을 거부했습니다"
        )
    if parameters is None:
        return connection.exec_driver_sql(statement)
    return connection.exec_driver_sql(statement, parameters)


def _rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in result.mappings().all()]
    except (AttributeError, TypeError, ValueError) as exc:
        raise UnverifiableError(
            "DB query 응답 형식이 잘못됐습니다",
            reason_code="INVALID_RESPONSE",
        ) from exc


def _scalar(result: Any) -> Any:
    try:
        return result.scalar_one()
    except (AttributeError, LookupError, TypeError) as exc:
        raise UnverifiableError(
            "DB scalar 응답 형식이 잘못됐습니다",
            reason_code="INVALID_RESPONSE",
        ) from exc


#: 최종 stage 2종. V4 postcheck를 타지 않는다.
FINAL_STAGES: frozenset[tuple[str, str]] = frozenset(
    (profile, stage) for profile, stage in reference_v5.FINAL_STAGE_BY_PROFILE.items()
)


def reference_postcheck_routing(profile: str, stage: str) -> str:
    """stage가 어느 reference postcheck를 타는지 판정한다.

    분기를 함수로 빼는 이유는 **판정 자체를 테스트할 수 있게** 하기 위해서다. 인라인
    `if`로 두면 routing이 잘못돼도 회귀가 잡지 못한다(구현리뷰 7차 필수 2 · 변이 M41).

    - `final` — CM-3.1의 순수 판정기를 read-only로 재사용한다. **V4
      `postcheck_database()`를 호출하지 않는다.** 그 경로는 R03 11컬럼·V4 View 계약이라
      logical type registry만 V5로 바꿔도 final DB가 여기서 실패한다(계획 §3.5).
    - `v4` — historical stage만 기존 routing을 유지한다.
    - `none` — `base_schema`는 reference 객체가 아직 없다.
    """

    if (profile, stage) in FINAL_STAGES:
        return "final"
    if stage == "base_schema":
        return "none"
    return "v4"


def reference_postcheck_mismatches(
    connection: Any,
    *,
    profile: str,
    stage: str,
    action_rows_before: int,
) -> list[dict[str, Any]]:
    """routing에 따라 **실제로 어느 postcheck를 소비하는지**를 결정한다.

    `verify_database()` 본문에 인라인으로 두면 소비 경계를 회귀가 잡지 못한다 —
    routing 반환값과 판정기 내부는 각각 통과하므로 `extend(...)` 한 줄을 지워도
    green이었다(구현리뷰 8차 필수 2).
    """

    routing = reference_postcheck_routing(profile, stage)
    if routing == "final":
        return _final_reference_mismatches(connection)
    if routing == "none":
        return []
    try:
        reference_extensions.postcheck_database(
            connection, action_rows_before=action_rows_before
        )
    except reference_extensions.ReferenceExtensionError:
        return [{"mismatch_kind": "REFERENCE_SIGNATURE_OR_VIEW"}]
    return []


def _final_reference_mismatches(connection: Any) -> list[dict[str, Any]]:
    """final stage의 R03·View 수용 검증. **read-only다.**

    `V5-CM-3.1`이 만든 순수 판정기에 catalog 행을 넘긴다. 판정 규칙을 여기서 다시
    구현하면 두 경로가 갈린다.
    """

    mismatches: list[dict[str, Any]] = []
    try:
        reference_v5.assert_r03_columns(
            _rows(
                _sql(
                    connection,
                    reference_v5.R03_COLUMNS_SQL.replace(
                        "%(table)s", f"'{reference_v5.R03_TABLE}'"
                    ),
                )
            )
        )
    except reference_v5.ReferenceV5Error:
        mismatches.append({"mismatch_kind": "FINAL_R03_CONTRACT"})

    try:
        reference_v5.assert_r03_constraints(
            _rows(_sql(connection, reference_v5.R03_CONSTRAINTS_SQL))
        )
    except reference_v5.ReferenceV5Error:
        mismatches.append({"mismatch_kind": "FINAL_R03_CONSTRAINTS"})

    view = reference_v5.ALARM_VIEW
    try:
        reference_v5.assert_view_columns(
            _rows(
                _sql(
                    connection,
                    reference_v5.VIEW_COLUMNS_SQL.replace("%(view)s", f"'{view}'"),
                )
            )
        )
        definition = _scalar(
            _sql(
                connection,
                reference_v5.VIEW_DEFINITION_SQL.replace("%(view)s", f"'{view}'"),
            )
        )
        reference_v5.assert_view_identity(str(definition))
    except reference_v5.ReferenceV5Error:
        mismatches.append({"mismatch_kind": "FINAL_VIEW_CONTRACT"})

    # **comment도 schema identity의 일부다**(계획 §3.5 · 구현리뷰 8차 필수 1).
    #
    # CM-3.1은 `assert_canonical_comments()`를 통과해야 schema identity를 발급한다.
    # 여기서 빠뜨리면 R03 comment가 지워지거나 View에 금지된 comment가 붙어도 final
    # `verify_database()`가 통과한다. CM-2.6·CM-3.1과 **같은 query 표현**을 쓴다 —
    # 같은 값을 다르게 읽으면 대조가 무의미하다.
    try:
        comments = {
            str(row["relname"]): row["comment"]
            for row in _rows(
                _sql(
                    connection,
                    reference_v5.RELATION_SECURITY_SQL,
                    {"names": [reference_v5.R03_TABLE, view]},
                )
            )
        }
        reference_v5.assert_canonical_comments(
            r03_comment=comments.get(reference_v5.R03_TABLE),
            view_comment=comments.get(view),
        )
    except reference_v5.ReferenceV5Error:
        mismatches.append({"mismatch_kind": "FINAL_COMMENT_CONTRACT"})
    return mismatches


def _final_source_column_types(table: str) -> dict[str, str] | None:
    """base 9의 logical type은 **source manifest v4**가 정본이다(계획 §3.5-1).

    구 `bootstrap_base_schema.BASE_COLUMNS`는 `wafer` 4곳을 아직 `smallint`로 본다.
    최종 DDL은 `varchar(24)`이므로 logical type이 `numeric`이 아니라 `text`다. 구
    registry를 그대로 두면 final DB가 base에서 먼저 실패한다.
    """

    tables = final_manifests.load_source_manifest()["tables"]
    entry = tables.get(table)
    if entry is None:
        return None
    column_types = entry.get("column_types")
    if not isinstance(column_types, dict) or not column_types:
        raise manifest_v3.ManifestSchemaError(
            f"{table}: source manifest v4에 column_types가 없습니다"
        )
    return dict(column_types)


def _expected_column_types(table: str) -> dict[str, str]:
    """final stage의 logical type 우선순위(계획 §3.5).

    1. base 9 — source manifest v4
    2. R03 — `apply_reference_extensions_v5.R03_COLUMNS` **12개**
    3. RAG·`nl_query_log` — 기존 reference registry (**R03는 여기로 내려오지 않는다**)
    4. Runtime 9 — `apply_agent_runtime.EXPECTED_TABLE_COLUMNS`
    """

    from_source = _final_source_column_types(table)
    if from_source is not None:
        return from_source
    if table == reference_v5.R03_TABLE:
        # **V4 11컬럼 registry로 내려가지 않는다.** 순서가 뒤집히면 R03가 구 계약으로
        # 판정되고 `member_wafer_refs`·`member_alarm_refs`가 사라진다(계획 §3.5-3).
        # 길이는 logical type에 영향이 없다(`varchar(24)`·`varchar` 모두 `text`).
        # 되돌리는 helper도 넣어 봤지만 어떤 변이로도 독립적으로 실패하지 않았다.
        return {
            name: logical_type(data_type)
            for name, data_type, _length, _nullable in reference_v5.R03_COLUMNS
        }
    if table in reference_extensions.EXPECTED_TABLE_COLUMNS:
        columns = reference_extensions.EXPECTED_TABLE_COLUMNS[table]
        return {name: logical_type(data_type) for name, data_type, _nullable in columns}
    if table in agent_runtime.EXPECTED_TABLE_COLUMNS:
        columns = agent_runtime.EXPECTED_TABLE_COLUMNS[table]
        return {column.name: logical_type(column.data_type) for column in columns}
    raise manifest_v3.ManifestSchemaError(f"{table}: logical type registry가 없습니다")


def _mismatch_details(
    *,
    target: BootstrapTarget,
    stage: str,
    inventory: str,
    table_count: int,
    action_history_rows: int | None,
    table: str | None = None,
    mismatch_kind: str | None = None,
    expected_row_count: int | None = None,
    actual_row_count: int | None = None,
    expected_policy: str | None = None,
    mismatches: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "profile": target.profile,
        "expected_stage": stage,
        "inventory": inventory,
        "table_count": table_count,
        "action_history_rows": action_history_rows,
    }
    optional = {
        "table": table,
        "mismatch_kind": mismatch_kind,
        "expected_row_count": expected_row_count,
        "actual_row_count": actual_row_count,
        "expected_policy": expected_policy,
    }
    details.update({key: value for key, value in optional.items() if value is not None})
    if mismatches is not None:
        details["mismatches"] = [dict(mismatch) for mismatch in mismatches]
    return details


def _table_mismatch(
    table: str,
    mismatch_kind: str,
    *,
    expected_row_count: int | None = None,
    actual_row_count: int | None = None,
    expected_policy: str | None = None,
) -> dict[str, Any]:
    mismatch: dict[str, Any] = {
        "table": table,
        "mismatch_kind": mismatch_kind,
    }
    optional = {
        "expected_row_count": expected_row_count,
        "actual_row_count": actual_row_count,
        "expected_policy": expected_policy,
    }
    mismatch.update(
        {key: value for key, value in optional.items() if value is not None}
    )
    return mismatch


def _engine_for(target: BootstrapTarget) -> Engine:
    url = target.create_url()
    validate_url_components(url, target)
    return create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def _validate_read_identity(connection: Any, target: BootstrapTarget) -> None:
    row = _rows(
        _sql(
            connection,
            """
            SELECT current_database() AS database_name,
                   EXISTS (
                       SELECT 1 FROM pg_namespace WHERE nspname = 'public'
                   ) AS public_exists,
                   has_schema_privilege(current_user, 'public', 'USAGE') AS can_use
            """,
        )
    )
    if len(row) != 1 or row[0].get("database_name") != target.database:
        raise UnverifiableError(
            "연결된 PostgreSQL database가 target과 다릅니다",
            reason_code="TARGET_IDENTITY_MISMATCH",
        )
    if row[0].get("public_exists") is not True or row[0].get("can_use") is not True:
        raise UnverifiableError(
            "public schema 읽기 권한이 없습니다",
            reason_code="NO_SCHEMA_USAGE",
        )


def _table_names(connection: Any) -> set[str]:
    return {
        str(row["table_name"])
        for row in _rows(
            _sql(
                connection,
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """,
            )
        )
    }


def _inventory_state(
    actual_tables: set[str], expected_tables: set[str], row_counts: Mapping[str, int]
) -> str:
    if not actual_tables:
        return "NO_SCHEMA"
    if actual_tables != expected_tables:
        return "UNKNOWN"
    return (
        "BASE_SCHEMA"
        if all(value == 0 for value in row_counts.values())
        else "EARLY_DATA"
    )


def verify_database(
    database: str,
    stage: str,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    candidate: Mapping[str, Any] | None = None,
    require_runtime_marker: bool = True,
) -> CheckResult:
    """등록 manifest(또는 `candidate`)를 실제 DB와 대조한다.

    **`require_runtime_marker`는 소유권 경계다.** `runtime_clean`의
    `agent_runtime` marker는 "final migration이 적용됐다"는 **증명서**이고, 그 적용과
    발급은 `V5-CM-3.2`가 소유한다(`apply_agent_runtime.run_apply`가
    `FINAL_RUNTIME_MIGRATION_NOT_WIRED`로 그렇게 선언한다).

    `V5-CM-1.8`은 profile manifest의 **내용**(inventory·행 수·content hash·column type·
    R03/View/comment)이 DB와 맞는지만 판정한다. 둘을 한 함수에 묶어 두면 CM-1.8이
    CM-3.2의 marker를 기다리고 CM-3.2는 CM-1.8 완료를 기다리는 **순환**이 된다
    (구현리뷰 12차 필수 2). 물리 DB에는 두 migration이 이미 적용돼 있고 빠진 것은
    저장소 marker뿐이다.

    **끄는 것은 marker load·SHA 대조뿐이다.** Runtime 물리 postcheck(constraint·index·
    `PUBLIC` privilege)는 이 flag와 무관하게 항상 수행한다 — 그것까지 끄면 partial
    index가 없거나 `PUBLIC` 권한이 남은 DB도 final manifest로 등록된다
    (구현리뷰 13차 필수 1).

    `candidate`를 넘기면 **저장소 등록본 대신 그것을 기준으로** 검증한다. 발급 전
    후보를 먼저 DB와 맞춰 보기 위한 경로이며(`V5-CM-1.8` 계획 §5 묶음 2-1), 저장소
    파일을 읽지도 쓰지도 않는다. 후보는 넘기기 전에 이미
    `manifest_v3.validate_manifest_schema()`를 통과해야 한다 — 여기서 다시 본다.
    """

    if database not in ALLOWED_DATABASES:
        raise manifest_v3.VerificationError("허용되지 않은 PostgreSQL database입니다")
    if stage == EVALUATION_MOCK_STAGE and DATABASE_PROFILE[database] == "evaluation":
        # **engine·target·epoch loader보다 앞이다**(계획 §6.3 · 구현리뷰 필수 1).
        #
        # `V5-CM-1.6`이 `load_evaluation_mock`을 삭제했다. 이 stage를 green으로 오인하지
        # 않으면서 connector를 **0회** 호출한다. 뒤에 두면 CM-1.8이 epoch loader를 v2로
        # 전환하는 순간 engine을 열게 된다.
        #
        # **profile까지 본다**(구현리뷰 권장 1). runtime DB에 evaluation 전용 stage를
        # 넣은 호출은 폐기 통보가 아니라 원래의 "허용되지 않은 profile/stage 조합"
        # 오류를 받아야 한다. stage 이름만 보면 그 입력 계약이 조용히 덮인다.
        #
        # stage 자체는 남긴다 — `final_manifest_blockers()`의
        # `EVALUATION_MOCK_PINS_48_ACTION_ROWS`가 실제 공백을 세야 하기 때문이다.
        # **`V5-CM-1.8`이 `evaluation_reference`를 등록하면 이 분기와 짝 테스트를 함께
        # 제거한다.**
        return CheckResult(
            database,
            STATUS_FAIL,
            EXIT_MISMATCH,
            {
                "stage": stage,
                "mismatches": [{"mismatch_kind": "EVALUATION_MOCK_RETIRED"}],
            },
        )
    profile = DATABASE_PROFILE[database]
    if candidate is None:
        manifest_path = manifest_v3.resolve_bootstrap_manifest_path(profile, stage)
        registered = _read_json(manifest_path, missing=manifest_v3.NotRegisteredError)
    else:
        # 후보 경로는 저장소를 읽지 않는다. 등록본이 아직 구 계보여도 후보만으로
        # DB를 대조할 수 있어야 발급 **전** 검증이 성립한다.
        registered = dict(candidate)
    epoch = manifest_v3.load_dataset_epoch()
    manifest_v3.validate_manifest_schema(
        registered,
        expected_artifact_type="db_bootstrap",
        expected_profile=profile,
        expected_stage=stage,
        expected_archive_sha256=epoch["archive"]["sha256"],
    )
    if registered["value_normalization_version"] != VALUE_NORMALIZATION_VERSION:
        raise manifest_v3.ManifestMetadataError(
            "DB manifest value normalization version이 다릅니다"
        )
    target = load_bootstrap_target(database, environ=environ)
    expected_tables = set(registered["tables"])
    if any(not IDENTIFIER_PATTERN.fullmatch(table) for table in expected_tables):
        raise manifest_v3.ManifestSchemaError("DB manifest table 식별자가 잘못됐습니다")
    if any(
        not IDENTIFIER_PATTERN.fullmatch(column)
        for entry in registered["tables"].values()
        for column in entry["columns"]
    ):
        raise manifest_v3.ManifestSchemaError(
            "DB manifest column 식별자가 잘못됐습니다"
        )
    completion_marker: dict[str, Any] | None = None
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _sql(connection, READ_ONLY_TRANSACTION_SQL)
            _sql(connection, "SET LOCAL search_path = public")
            _sql(connection, "SET LOCAL statement_timeout = '30s'")
            _validate_read_identity(connection, target)
            actual_tables = _table_names(connection)
            relevant = actual_tables & expected_tables
            row_counts = {
                table: int(_scalar(_sql(connection, f'SELECT count(*) FROM "{table}"')))
                for table in sorted(relevant)
                if IDENTIFIER_PATTERN.fullmatch(table)
            }
            inventory = _inventory_state(actual_tables, expected_tables, row_counts)
            if actual_tables != expected_tables:
                raise AcceptanceMismatchError(
                    "DB object 집합이 stage 계약과 다릅니다",
                    details=_mismatch_details(
                        target=target,
                        stage=stage,
                        inventory=inventory,
                        table_count=len(actual_tables),
                        action_history_rows=row_counts.get("action_history"),
                        mismatch_kind="TABLE_INVENTORY",
                    ),
                )
            mismatches: list[dict[str, Any]] = []
            for table in sorted(expected_tables):
                privilege = _scalar(
                    _sql(
                        connection,
                        "SELECT has_table_privilege(current_user, %s, 'SELECT')",
                        (f"public.{table}",),
                    )
                )
                if privilege is not True:
                    raise UnverifiableError(
                        "DB table SELECT 권한이 없습니다",
                        reason_code="NO_SELECT_GRANT",
                    )
                column_rows = _rows(
                    _sql(
                        connection,
                        """
                        SELECT a.attname AS column_name,
                               format_type(a.atttypid, a.atttypmod) AS data_type
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        JOIN pg_attribute a ON a.attrelid = c.oid
                        WHERE n.nspname = 'public' AND c.relname = %s
                          AND c.relkind IN ('r','p')
                          AND a.attnum > 0 AND NOT a.attisdropped
                        ORDER BY a.attnum
                        """,
                        (table,),
                    )
                )
                columns = [str(row["column_name"]) for row in column_rows]
                expected = registered["tables"][table]
                if any(not IDENTIFIER_PATTERN.fullmatch(column) for column in columns):
                    raise manifest_v3.ManifestSchemaError(
                        "DB manifest column 식별자가 잘못됐습니다"
                    )
                if columns != expected["columns"]:
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "COLUMN_ORDER",
                            expected_policy=expected["verification_policy"],
                        )
                    )
                    continue
                actual_types = {
                    str(row["column_name"]): logical_type(str(row["data_type"]))
                    for row in column_rows
                }
                expected_types = _expected_column_types(table)
                if actual_types != expected_types:
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "COLUMN_TYPE",
                            expected_policy=expected["verification_policy"],
                        )
                    )
                    continue
                if expected["verification_policy"] == "schema_only":
                    continue
                # **hash는 `content_columns`가 있으면 그 부분집합으로만 낸다.**
                #
                # `document.created_at`처럼 두 DB가 독립 적재해 항상 달라지는 컬럼을
                # 빼기 위해서다. catalog 대조는 위에서 전체 `columns`로 이미 했다
                # (구현리뷰 16차 필수 1).
                hashed = list(expected.get("content_columns") or columns)
                hashed_types = {name: expected_types[name] for name in hashed}
                selected = ", ".join(f'"{column}"' for column in hashed)
                values = [
                    normalize_db_row(row, hashed_types)
                    for row in _rows(
                        _sql(connection, f'SELECT {selected} FROM "{table}"')
                    )
                ]
                if len(values) != expected["row_count"]:
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "ROW_COUNT",
                            expected_row_count=expected["row_count"],
                            actual_row_count=len(values),
                            expected_policy=expected["verification_policy"],
                        )
                    )
                elif (
                    manifest_v3.hash_canonical_rows(values) != expected["content_hash"]
                ):
                    mismatches.append(
                        _table_mismatch(
                            table,
                            "CONTENT_HASH",
                            expected_row_count=expected["row_count"],
                            actual_row_count=len(values),
                            expected_policy=expected["verification_policy"],
                        )
                    )
            mismatches.extend(
                reference_postcheck_mismatches(
                    connection,
                    profile=profile,
                    stage=stage,
                    action_rows_before=row_counts.get("action_history", 0),
                )
            )
            if stage == "runtime_clean":
                # **물리 postcheck는 항상 한다**(구현리뷰 13차 필수 1).
                #
                # `postcheck_database()`는 marker 검사가 아니다 — Runtime 9 table의
                # constraint·FK·CHECK, index allowlist와 partial index, action/alarm
                # 불변식, table allowlist, `PUBLIC` privilege 0건을 본다. manifest의
                # columns/type/row/hash 검증이 대체하지 못하는 축이다. opt-out이
                # 이것까지 끄면 순환을 끊은 것이 아니라 적용 증명의 절반을 함께
                # 끈 것이 된다.
                runtime_result = None
                try:
                    runtime_result = agent_runtime.postcheck_database(
                        connection,
                        alarm_rows_before=agent_runtime.alarm_event_count(connection),
                    )
                except agent_runtime.AgentRuntimeError:
                    mismatches.append({"mismatch_kind": "RUNTIME_SCHEMA"})

                # **SQL load·SHA·marker를 함께 미룬다**(구현리뷰 14차 필수 1).
                #
                # `migration_sha`는 marker를 읽을 때만 쓰인다. loader를 flag 앞에 두면
                # CM-1.8이 CM-3.2 소유 `002_agent_runtime_clean.sql`을 무조건 읽고
                # 문법·객체 수·guard까지 검증하게 된다 — marker 발급뿐 아니라 그 SHA
                # 입력 artifact에도 결합된 상태다. "현재 파일이 우연히 유효하다"와
                # "의존하지 않는다"는 다른 계약이다.
                if require_runtime_marker and runtime_result is not None:
                    try:
                        runtime_sql, _ = agent_runtime.load_and_validate_sql()
                        migration_sha = agent_runtime.migration_sha256(runtime_sql)
                        runtime_marker = agent_runtime.load_marker(
                            target,
                            migration_sha=migration_sha,
                        )
                        if (
                            runtime_marker is None
                            or runtime_marker["schema_signature_sha256"]
                            != runtime_result.schema_signature_sha256
                        ):
                            raise agent_runtime.AgentRuntimeArtifactError(
                                "runtime marker가 schema와 다릅니다"
                            )
                    except agent_runtime.AgentRuntimeError:
                        mismatches.append({"mismatch_kind": "RUNTIME_MARKER"})
            if mismatches:
                raise AcceptanceMismatchError(
                    "DB acceptance 계약이 다릅니다",
                    details=_mismatch_details(
                        target=target,
                        stage=stage,
                        inventory=inventory,
                        table_count=len(actual_tables),
                        action_history_rows=row_counts.get("action_history"),
                        mismatches=mismatches,
                    ),
                )
    except SQLAlchemyError as exc:
        raise UnverifiableError(
            "PostgreSQL read-only 검증을 수행할 수 없습니다",
            reason_code="CONNECT_OR_QUERY_FAILED",
        ) from exc
    finally:
        engine.dispose()
    details = {
        "profile": target.profile,
        "expected_stage": stage,
        "inventory": inventory,
        "table_count": len(expected_tables),
        "action_history_rows": row_counts.get("action_history", 0),
    }
    if completion_marker is not None:
        details.update(
            {
                "fixture_type": completion_marker["fixture_type"],
                "fixture_marker_status": completion_marker["status"],
            }
        )
    return CheckResult(
        database,
        STATUS_PASS,
        EXIT_OK,
        details,
    )


def verify_neo4j(
    *,
    archive_path: Path,
    environ: Mapping[str, str] | None = None,
    marker_root: Path = neo4j_bootstrap.MARKER_ROOT,
    state_reader: Callable[..., Any] = neo4j_bootstrap.read_current_state,
) -> CheckResult:
    try:
        context = neo4j_bootstrap.load_context(
            archive_path,
            NEO4J_DATABASE,
            environ=environ,
        )
    except GraphManifestError as exc:
        raise manifest_v3.ManifestMetadataError(
            "Neo4j graph artifact metadata가 잘못됐습니다"
        ) from exc
    try:
        marker = neo4j_bootstrap.load_marker(NEO4J_DATABASE, root=marker_root)
    except neo4j_bootstrap.MarkerError as exc:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j marker가 유효하지 않습니다"
        ) from exc
    if marker is None:
        raise UnverifiableError(
            "Neo4j success marker가 없습니다",
            reason_code="MISSING_SUCCESS_MARKER",
        )
    try:
        neo4j_bootstrap.validate_marker_for_context(marker, context)
    except neo4j_bootstrap.MarkerError as exc:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j marker가 현재 target/artifact와 다릅니다"
        ) from exc
    if not neo4j_bootstrap.marker_is_readiness_success(marker):
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j marker가 readiness success 상태가 아닙니다"
        )
    try:
        snapshot, schema_fingerprint = state_reader(
            context.target,
            require_supported_schema=True,
        )
    except neo4j_bootstrap.GraphStateError as exc:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j live graph 구조가 계약과 다릅니다"
        ) from exc
    except Neo4jTargetError as exc:
        raise UnverifiableError(
            "Neo4j read-only 검증을 수행할 수 없습니다",
            reason_code="TARGET_VALIDATION_FAILED",
        ) from exc
    except (DriverError, Neo4jError) as exc:
        raise UnverifiableError(
            "Neo4j read-only 검증을 수행할 수 없습니다",
            reason_code="CONNECT_OR_QUERY_FAILED",
        ) from exc
    graph_fingerprint = neo4j_bootstrap.snapshot_fingerprint(snapshot)
    label_counts = Counter(node.label for node in snapshot.nodes)
    type_counts = Counter(item.relation_type for item in snapshot.relationships)
    relation_id_count = sum(
        item.relation_id is not None for item in snapshot.relationships
    )
    manifest = context.manifest
    expected = {
        "node_count": manifest["node_count"],
        "relationship_count": manifest["relationship_count"],
        "relation_id_count": manifest["relationship_count"],
        "relation_id_duplicates": 0,
        "label_distribution": manifest["label_distribution"],
        "relationship_type_distribution": manifest["relationship_type_distribution"],
        "graph_fingerprint": manifest["expected_graph_fingerprint_sha256"],
        "schema_fingerprint": marker.get("schema_fingerprint_sha256"),
    }
    actual = {
        "node_count": snapshot.node_count,
        "relationship_count": snapshot.relationship_count,
        "relation_id_count": relation_id_count,
        "relation_id_duplicates": snapshot.relation_id_duplicates,
        "label_distribution": dict(sorted(label_counts.items())),
        "relationship_type_distribution": dict(sorted(type_counts.items())),
        "graph_fingerprint": graph_fingerprint,
        "schema_fingerprint": schema_fingerprint,
    }
    forbidden = {"Sensor", "LOCATED_IN", "UPSTREAM_OF", "USED_IN"}
    if actual != expected or forbidden & (set(label_counts) | set(type_counts)):
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j live graph가 등록 manifest/marker와 다릅니다"
        )
    if marker["actual_graph_fingerprint_sha256"] != graph_fingerprint:
        raise manifest_v3.ArtifactMismatchError(
            "Neo4j live graph fingerprint가 marker와 다릅니다"
        )
    return CheckResult(
        "neo4j",
        STATUS_PASS,
        EXIT_OK,
        {
            "database": NEO4J_DATABASE,
            "marker_status": marker["status"],
            "node_count": snapshot.node_count,
            "relationship_count": snapshot.relationship_count,
            "relation_id_count": relation_id_count,
            "relation_id_duplicates": snapshot.relation_id_duplicates,
        },
    )


def _failure_result(target: str, exc: Exception) -> CheckResult:
    exit_code = getattr(exc, "exit_code", EXIT_USAGE)
    if exit_code == EXIT_NOT_REGISTERED:
        status = STATUS_NOT_REGISTERED
    elif exit_code == EXIT_UNVERIFIABLE:
        status = STATUS_UNVERIFIABLE
    else:
        status = STATUS_FAIL
    details = {
        "reason_code": getattr(
            exc,
            "reason_code",
            getattr(exc, "code", type(exc).__name__),
        )
    }
    provided = getattr(exc, "details", None)
    if isinstance(provided, Mapping):
        details.update(provided)
    return CheckResult(
        target,
        status,
        exit_code,
        details,
    )


def aggregate_exit_code(results: Sequence[CheckResult]) -> int:
    codes = {result.exit_code for result in results}
    if EXIT_MISMATCH in codes:
        return EXIT_MISMATCH
    if any(
        code not in {EXIT_OK, EXIT_NOT_REGISTERED, EXIT_UNVERIFIABLE} for code in codes
    ):
        return EXIT_MISMATCH
    if EXIT_NOT_REGISTERED in codes:
        return EXIT_NOT_REGISTERED
    if EXIT_UNVERIFIABLE in codes:
        return EXIT_UNVERIFIABLE
    return EXIT_OK


def _report_payload(results: Sequence[CheckResult]) -> dict[str, Any]:
    exit_code = aggregate_exit_code(results)
    payload = {
        "artifact_type": "bootstrap_verification_report",
        "format_version": 1,
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "overall_status": STATUS_BY_EXIT.get(exit_code, STATUS_FAIL),
        "exit_code": exit_code,
        "targets": [result.as_dict() for result in results],
        "verified_at": datetime.now(UTC).isoformat(),
    }
    manifest_v3.scan_for_sensitive_values(payload)
    return payload


def _save_report(path: Path, results: Sequence[CheckResult]) -> dict[str, Any]:
    payload = _report_payload(results)
    manifest_v3.atomic_save_json(path, payload)
    return payload


def _target_unverifiable(exc: Exception) -> UnverifiableError:
    message = str(exc)
    reason_code = (
        "MISSING_CONFIGURATION"
        if "설정이 비어" in message or "설정되지 않았" in message
        else "TARGET_VALIDATION_FAILED"
    )
    return UnverifiableError(
        "검증 target을 안전하게 확인할 수 없습니다",
        reason_code=reason_code,
    )


def run_all(
    *,
    archive_path: Path,
    environ: Mapping[str, str],
) -> tuple[list[CheckResult], int]:
    checks: list[tuple[str, Callable[[], CheckResult]]] = [
        ("files", lambda: verify_files(archive_path=archive_path)),
        *[
            (
                database,
                lambda database=database: verify_database(
                    database,
                    EXPECTED_STAGES[database],
                    environ=environ,
                ),
            )
            for database in EXPECTED_STAGES
        ],
        (
            "neo4j",
            lambda: verify_neo4j(archive_path=archive_path, environ=environ),
        ),
    ]
    results = []
    for target, check in checks:
        try:
            results.append(check())
        except manifest_v3.VerificationError as exc:
            if exc.exit_code not in {
                EXIT_MISMATCH,
                EXIT_NOT_REGISTERED,
                EXIT_UNVERIFIABLE,
            }:
                raise
            results.append(_failure_result(target, exc))
        except (
            TargetValidationError,
            Neo4jTargetError,
            neo4j_bootstrap.Neo4jBootstrapError,
        ) as exc:
            results.append(_failure_result(target, _target_unverifiable(exc)))
        except SQLAlchemyError:
            results.append(
                _failure_result(
                    target,
                    UnverifiableError(
                        "target read-only 검증을 수행할 수 없습니다",
                        reason_code="CONNECT_OR_QUERY_FAILED",
                    ),
                )
            )
        except agent_runtime.AgentRuntimeError as exc:
            results.append(_failure_result(target, exc))
    return results, aggregate_exit_code(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--files-only", action="store_true")
    modes.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    modes.add_argument("--neo4j", action="store_true")
    modes.add_argument("--all", action="store_true")
    parser.add_argument("--stage")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def _validate_cli(args: argparse.Namespace) -> None:
    if args.database and not args.stage:
        raise manifest_v3.VerificationError("--database에는 --stage가 필요합니다")
    if args.stage and not args.database:
        raise manifest_v3.VerificationError("--stage는 --database와 함께 사용합니다")


def _print_result(result: CheckResult) -> None:
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)
    args = _parser().parse_args(argv)
    try:
        _validate_cli(args)
        environ = dict(os.environ)
        if args.files_only:
            archive = _archive_path(args.archive, environ)
            result = verify_files(archive_path=archive)
            results = [result]
        elif args.database:
            result = verify_database(
                args.database,
                args.stage,
                environ=environ,
            )
            results = [result]
        elif args.neo4j:
            archive = _archive_path(args.archive, environ)
            result = verify_neo4j(archive_path=archive, environ=environ)
            results = [result]
        else:
            archive = _archive_path(args.archive, environ)
            results, exit_code = run_all(archive_path=archive, environ=environ)
            path = args.report or REPORT_ROOT / (
                datetime.now(UTC).strftime("bootstrap-%Y%m%dT%H%M%SZ.json")
            )
            report_payload = _save_report(path, results)
            print(json.dumps(report_payload, ensure_ascii=False, sort_keys=True))
            return exit_code
        if args.report:
            _save_report(args.report, results)
        _print_result(results[0])
        return results[0].exit_code
    except manifest_v3.VerificationError as exc:
        _print_result(_failure_result("command", exc))
        return exc.exit_code
    except (
        TargetValidationError,
        Neo4jTargetError,
        neo4j_bootstrap.Neo4jBootstrapError,
    ) as exc:
        error = _target_unverifiable(exc)
        _print_result(_failure_result("command", error))
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
