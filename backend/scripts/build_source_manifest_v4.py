"""최종 패키지 source manifest v4 생성기 (V5-CM-1.3·V5-CM-1.4).

`V5-CM-1.1`이 파일을 등록했고(경로·크기·바이트 해시) `V5-CM-1.2`가 epoch를 발급했다.
이 스크립트는 그 파일의 **내용**을 계약으로 고정한다 — 9개 CSV의 컬럼 목록·행 수·
typed canonical row hash와 DDL·cypher·Generator·RAG 파일 해시를
`infra/bootstrap/source-manifest-v4.json` 하나에 담는다. `V5-CM-1.4`는 ZIP 안의
Generator를 격리된 임시 디렉터리에서 매번 실행해 9개 CSV의 바이트 동일성과
`master.cypher`의 CRLF→LF 정규화 동일성을 함께 증명한다.

typed의 의미: 각 cell을 `03_schema_clean.sql`의 컬럼 타입으로 정규화
(`value_normalization.normalize_csv_row`, `db-value-v1`)한 뒤 행 순서 무관 canonical
hash(`manifest_v3.hash_canonical_rows`)를 얻는다. 문자열 셀을 그대로 해싱하면 적재 후
DB 측 해시와 영원히 어긋난다 — `V5-CM-2.4` 적재 검증이 이 해시를 대조 기준으로 쓴다.

해싱 규약은 재구현하지 않고 `manifest_v3`에서 **import**한다(작업계획 결정 8).
import은 `load_dataset_epoch()`의 epoch 검사를 트리거하지 않으므로 `V5-CM-1.2`의
동시 참조 금지와 무관하다.

exit 규약은 `intake_final_zip.py`·`issue_final_epoch.py`와 같다 — 0 정상 ·
1 기준 불일치 · 2 사용법·입출력 오류 · 3 승인 필요.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from manifest_v3 import (
    HASH_ALGORITHM,
    ManifestSchemaError,
    hash_canonical_rows,
    parse_csv_bytes,
)
from value_normalization import (
    VALUE_NORMALIZATION_VERSION,
    ValueNormalizationError,
    logical_type,
    normalize_csv_row,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
DATASET_EPOCH_PATH = BOOTSTRAP_ROOT / "dataset-epoch.json"
INTAKE_ARTIFACT_PATH = BOOTSTRAP_ROOT / "final-zip-intake.json"
MANIFEST_V4_PATH = BOOTSTRAP_ROOT / "source-manifest-v4.json"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

MANIFEST_FORMAT_VERSION = 4
ARTIFACT_TYPE = "source_files"
SELECTED_MEMBER_COUNT = 15
GENERATOR_REPRODUCTION_CONTRACT_VERSION = 1
GENERATOR_TIMEOUT_SECONDS = 60
GENERATOR_LOCALE = "C.UTF-8"

MEMBER_PREFIX = "project/repository/sample"
SCHEMA_SQL_MEMBER = f"{MEMBER_PREFIX}/schema/03_schema_clean.sql"
MASTER_CYPHER_MEMBER = f"{MEMBER_PREFIX}/ontology/master.cypher"
GENERATOR_MEMBER = "project/repository/mvp/gen_sample_data.py"
RAG_MEMBERS = (
    f"{MEMBER_PREFIX}/rag/SPEC_ET-7500_DryEtcher.md",
    f"{MEMBER_PREFIX}/rag/SPEC_PH-9000_PhotoScanner.md",
    f"{MEMBER_PREFIX}/rag/TROUBLE_FDC_FaultGuide.md",
)

# ① 교육생 배포패키지에서 선별하는 artifact — 배포패키지_기준.md §3.1 표 그대로.
# ③(project.zip)에 없는 것만 ①에서 가져온다는 판정 규칙(§2)의 기계 검증 지점이며,
# 문서가 바뀌면 tripwire 테스트가 실패해 사람이 의도적으로 동기화하게 한다.
ORIGIN_PACKAGE_NAME = "교육생_배포패키지"
ORIGIN_SELECTION_RULE = "final-package-first"
ORIGIN_REFERENCE_DOCUMENT = "docs/reference/배포패키지_기준.md"
ORIGIN_ARTIFACTS: dict[str, dict[str, str]] = {
    "document_schema_sql": {
        "file_id": "03_db/01_schema.sql",
        "sha256": ("2b0195ba55e2f49fd3af63afccf395568a6aea5c9d008a871462754d4d9f2cb2"),
        "role": (
            "document·document_chunk 스키마와 vector extension 선언의 출처 —"
            " ③에 없다 (V5-CM-2.x·V5-B-1.3 소비)"
        ),
    },
    "rag_loader": {
        "file_id": "05_scripts/load_documents.py",
        "sha256": ("73c30492c6266503fbf6b4f56ce2b5d0b86ba9796f81ab62b1116707e37a2ba7"),
        "role": "RAG 적재 loader 원형 — V5-B-1.3이 adapter 계약으로 재사용",
    },
    "embedding_requirements": {
        "file_id": "04_infra/requirements.txt",
        "sha256": ("c531fac10bb950ae05451572dfdbf573638e1702cddf43fe51afdd7d074f0e72"),
        "role": "임베딩 모델(BAAI/bge-m3·1024) 의존성 근거",
    },
}

# --- 테스트 주입 seam (모듈 상수) -------------------------------------------------
# 단위 테스트는 합성 mini-ZIP·합성 epoch/intake fixture를 쓴다.
# monkeypatch.setattr(build_source_manifest_v4, "<상수>", ...)로 대체한다
# (작업계획 §2.1).

TARGET_EPOCH = "fdc_final_20260818"

# 기준표 §2 그대로. 기준표가 바뀌면 tripwire 테스트가 실패해 사람이 의도적으로
# 동기화하게 한다.
EXPECTED_ROW_COUNTS = {
    "dim_parameter": 8,
    "lot_history": 600,
    "fdc_trace": 14_400,
    "summary_data": 4_800,
    "evaluation": 4_800,
    "trace_alarm_history": 138,
    "summary_alarm_history": 51,
    "metrology": 48,
    "action_history": 12,
}

# 8개는 manifest_v3.SOURCE_EXPECTED_COLUMNS의 값을 승계(값 복사)했고, dim_parameter는
# 이 Task가 처음 정의하는 신규 계약이다(작업계획 결정 9 — 최종 CSV 헤더·DDL 대조로
# 확정).
# import 대신 복사한 이유: V5-CM-1.6이 manifest_v3의 구 계약 상수를 삭제할 수 있고,
# 존속이 보장된 것은 parse_csv_bytes·hash_canonical_rows 두 함수뿐이다(작업계획 §1.2).
EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
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
    "dim_parameter": (
        "parameter_id",
        "parameter_name",
        "unit",
        "area",
        "target_value",
        "spec_lower",
        "ctrl_lower",
        "ctrl_upper",
        "spec_upper",
        "upper_only",
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

# 값은 value_normalization.logical_type()의 산출값이다 — {numeric, boolean, timestamp,
# json, vector, text} 6종. DDL 타입 문자열이 아니다(smallint·integer·bigint → numeric).
# content_hash를 좌우하는 입력이므로 컬럼 이름과 같은 층위에서 고정하며, ZIP의
# 03_schema_clean.sql 파싱 결과가 이 상수와 다르면 exit 1이다(작업계획 §2.1).
EXPECTED_COLUMN_TYPES: dict[str, dict[str, str]] = {
    "action_history": {
        "action_id": "text",
        "lot_id": "text",
        "recipe_step_name": "text",
        "equipment_id": "text",
        "chamber_id": "text",
        "trigger_alarm_lot_hist_id": "text",
        "action_code": "text",
        "reason": "text",
        "approval_required": "text",
        "approval_status": "text",
        "approved_by": "text",
        "approved_at": "timestamp",
        "notify_status": "text",
        "notify_at": "timestamp",
        "mes_status": "text",
        "mes_at": "timestamp",
        "created_at": "timestamp",
    },
    "dim_parameter": {
        "parameter_id": "text",
        "parameter_name": "text",
        "unit": "text",
        "area": "text",
        "target_value": "numeric",
        "spec_lower": "numeric",
        "ctrl_lower": "numeric",
        "ctrl_upper": "numeric",
        "spec_upper": "numeric",
        "upper_only": "boolean",
    },
    "evaluation": {
        "lot_hist_id": "text",
        "area": "text",
        "equipment": "text",
        "chamber": "text",
        "parameter": "text",
        "recipe": "text",
        "lot": "text",
        "wafer": "text",
        "step_no": "numeric",
        "step_seq": "numeric",
        "point_cnt": "numeric",
        "ooc_point_cnt": "numeric",
        "oos_point_cnt": "numeric",
        "alarm_type": "text",
    },
    "fdc_trace": {
        "lot_hist_id": "text",
        "parameter_id": "text",
        "seq_no": "numeric",
        "recipe_step_no": "numeric",
        "step_seq": "numeric",
        "measured_at": "timestamp",
        "value": "numeric",
    },
    "lot_history": {
        "lot_hist_id": "text",
        "lot_id": "text",
        "wafer_no": "numeric",
        "wafer_id": "text",
        "device_id": "text",
        "step_id": "text",
        "area_id": "text",
        "equipment_id": "text",
        "chamber_id": "text",
        "recipe_id": "text",
        "track_in_at": "timestamp",
        "track_out_at": "timestamp",
        "duration_sec": "numeric",
        "chamber_wafer_cum": "numeric",
        "lot_seq": "numeric",
        "fault_code": "text",
    },
    "metrology": {
        "metrology_id": "text",
        "lot_hist_id": "text",
        "lot_id": "text",
        "wafer_no": "numeric",
        "wafer_id": "text",
        "step_id": "text",
        "measure_type": "text",
        "unit": "text",
        "measured_value": "numeric",
        "spec_center": "numeric",
        "spec_lower": "numeric",
        "spec_upper": "numeric",
        "alarm_result": "text",
        "measured_at": "timestamp",
    },
    "summary_alarm_history": {
        "alarm_id": "text",
        "occurred_at": "timestamp",
        "area": "text",
        "equipment": "text",
        "chamber": "text",
        "parameter": "text",
        "recipe": "text",
        "lot": "text",
        "wafer": "text",
        "step_no": "numeric",
        "step_seq": "numeric",
        "statistic_type": "text",
        "stat_value": "numeric",
        "cl": "numeric",
        "ucl": "numeric",
        "lcl": "numeric",
        "limit_type": "text",
        "alarm_type": "text",
    },
    "summary_data": {
        "lot_hist_id": "text",
        "area": "text",
        "equipment": "text",
        "chamber": "text",
        "parameter": "text",
        "recipe": "text",
        "lot": "text",
        "wafer": "text",
        "step_no": "numeric",
        "step_seq": "numeric",
        "value_mean": "numeric",
        "value_std": "numeric",
        "value_min": "numeric",
        "value_max": "numeric",
        "point_cnt": "numeric",
    },
    "trace_alarm_history": {
        "alarm_id": "text",
        "occurred_at": "timestamp",
        "area": "text",
        "equipment": "text",
        "chamber": "text",
        "parameter": "text",
        "recipe": "text",
        "lot": "text",
        "wafer": "text",
        "step_no": "numeric",
        "step_seq": "numeric",
        "seq_no": "numeric",
        "value": "numeric",
        "limit_type": "text",
        "limit_value": "numeric",
        "alarm_type": "text",
    },
}

TABLE_MEMBERS = {
    table: f"{MEMBER_PREFIX}/data/{table}.csv" for table in EXPECTED_ROW_COUNTS
}

# canonical row 직렬화 규약 라벨(시스템설계 §2.3 `canonicalization_version`).
# `hash_algorithm`에서 기계적으로 파생시켜 두 라벨이 어긋날 수 없게 한다.
CANONICALIZATION_VERSION = HASH_ALGORITHM.removeprefix("sha256-")

# 최종 03_schema_clean.sql의 선언 PK 그대로(시스템설계 §2.3 `tables[*].primary_key`).
# EXPECTED_COLUMN_TYPES와 같은 층위의 seam이며, ZIP의 DDL 파싱 결과가 이 상수와
# 다르면 exit 1이다.
EXPECTED_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
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

# 시스템설계 §2.4 profile 표 그대로 — runtime·runtime-e2e는 물리 table 9개를 만들되
# action_history.csv는 적재하지 않고(0행), evaluation만 9개 전부를 적재한다(12행).
PROFILES = ("runtime", "runtime-e2e", "evaluation")
INCLUDED_BY_PROFILE: dict[str, tuple[str, ...]] = {
    table: (("evaluation",) if table == "action_history" else PROFILES)
    for table in EXPECTED_ROW_COUNTS
}

# --------------------------------------------------------------------------------


class ManifestBuildError(Exception):
    """sanitized 사유와 exit code를 함께 전달한다."""

    def __init__(self, reason: str, exit_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


def _short(value: Any, limit: int = 24) -> str:
    """파일에서 읽은 값을 사유 메시지에 넣기 전에 한 줄로 무해화한다."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    text = "".join(char if char.isprintable() else "?" for char in text)
    return text[:limit] + "…" if len(text) > limit else text


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path, *, label: str, corrupt_exit: int) -> dict[str, Any]:
    """UTF-8 JSON object 하나를 읽는다. 손상은 traceback 없이 사유 한 줄로 끝낸다.

    corrupt_exit 분기는 `issue_final_epoch.py`와 같다 — 입력 artifact(epoch·intake)는
    대조에 도달하지 못하므로 EXIT_USAGE, 쓰기 대상(기존 manifest)은 [승계] 규약대로
    EXIT_MISMATCH.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestBuildError(f"{label}가 UTF-8이 아닙니다", corrupt_exit) from exc
    except OSError as exc:
        raise ManifestBuildError(f"{label}를 읽을 수 없습니다", EXIT_USAGE) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestBuildError(
            f"{label}가 올바른 JSON이 아닙니다", corrupt_exit
        ) from exc
    if not isinstance(payload, dict):
        raise ManifestBuildError(
            f"{label}가 JSON object가 아닙니다 (최상위 타입 {type(payload).__name__})",
            corrupt_exit,
        )
    return {"raw": raw, "payload": payload}


def load_epoch(epoch_path: Path) -> dict[str, Any]:
    """epoch v2를 읽고 epoch 문자열·archive SHA-256을 확정한다."""
    epoch = _read_json_object(
        epoch_path, label="epoch artifact", corrupt_exit=EXIT_USAGE
    )["payload"]
    declared = epoch.get("dataset_epoch")
    if declared != TARGET_EPOCH:
        raise ManifestBuildError(
            f"epoch artifact가 발급 기준과 다릅니다 — dataset_epoch"
            f" (기대 {TARGET_EPOCH} / 실측 {_short(declared)})",
            EXIT_MISMATCH,
        )
    archive = epoch.get("archive")
    sha = archive.get("sha256") if isinstance(archive, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ManifestBuildError(
            "epoch artifact의 archive.sha256이 올바르지 않습니다", EXIT_MISMATCH
        )
    return {"dataset_epoch": declared, "archive_sha256": sha}


def load_intake(intake_path: Path, *, archive_sha256: str) -> dict[str, dict[str, Any]]:
    """intake의 selected_members 15개를 얻는다. archive 해시가 epoch와 다르면 실패."""
    intake = _read_json_object(
        intake_path, label="intake artifact", corrupt_exit=EXIT_USAGE
    )["payload"]
    archive = intake.get("archive")
    sha = archive.get("sha256") if isinstance(archive, dict) else None
    if sha != archive_sha256:
        raise ManifestBuildError(
            "intake artifact의 archive.sha256이 epoch artifact와 다릅니다"
            f" (epoch {archive_sha256[:8]} / intake {_short(sha, 8)})",
            EXIT_MISMATCH,
        )
    members = intake.get("selected_members")
    if not isinstance(members, list) or not all(
        isinstance(member, dict)
        and isinstance(member.get("path"), str)
        and isinstance(member.get("sha256"), str)
        for member in members
    ):
        raise ManifestBuildError(
            "intake artifact의 selected_members 형식이 올바르지 않습니다", EXIT_USAGE
        )
    if len(members) != SELECTED_MEMBER_COUNT:
        raise ManifestBuildError(
            "intake selected_members 수가 기대와 다릅니다"
            f" (기대 {SELECTED_MEMBER_COUNT} / 실측 {len(members)})",
            EXIT_MISMATCH,
        )
    by_path = {member["path"]: member for member in members}
    if len(by_path) != len(members):
        raise ManifestBuildError(
            "intake selected_members 경로가 중복됐습니다", EXIT_MISMATCH
        )
    return by_path


def read_members(
    archive_path: Path,
    *,
    archive_sha256: str,
    selected: dict[str, dict[str, Any]],
) -> dict[str, bytes]:
    """ZIP 전체 해시를 먼저 확정한 뒤 intake 등록 15개 member만 판독한다.

    등록 밖 member(참고 Backend·Frontend·node_modules 8,101개)는 읽지 않는다.
    각 member의 파일 해시가 intake 등록값과 다르면 실패한다.
    """
    if not archive_path.is_file():
        raise ManifestBuildError("대상 ZIP 파일을 찾을 수 없습니다", EXIT_USAGE)
    try:
        measured = _sha256_file(archive_path)
    except OSError as exc:
        raise ManifestBuildError(
            "대상 ZIP을 안전하게 읽을 수 없습니다", EXIT_USAGE
        ) from exc
    if measured != archive_sha256:
        raise ManifestBuildError(
            "ZIP 전체 SHA-256이 epoch·intake 기준과 다릅니다"
            f" (기대 {archive_sha256[:8]} / 실측 {measured[:8]})",
            EXIT_MISMATCH,
        )

    payloads: dict[str, bytes] = {}
    mismatched: list[str] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = {info.filename for info in archive.infolist() if not info.is_dir()}
            missing = sorted(set(selected) - names)
            if missing:
                raise ManifestBuildError(
                    "intake 등록 member가 ZIP에 없습니다 — " + ", ".join(missing),
                    EXIT_MISMATCH,
                )
            for path in sorted(selected):
                payload = archive.read(path)
                if _sha256_bytes(payload) != selected[path]["sha256"]:
                    mismatched.append(path)
                    continue
                payloads[path] = payload
    except zipfile.BadZipFile as exc:
        raise ManifestBuildError("대상 ZIP 형식이 손상됐습니다", EXIT_USAGE) from exc
    except OSError as exc:
        raise ManifestBuildError(
            "대상 ZIP member를 안전하게 읽을 수 없습니다", EXIT_USAGE
        ) from exc
    if mismatched:
        raise ManifestBuildError(
            "member 해시가 intake 등록값과 다릅니다 — " + ", ".join(sorted(mismatched)),
            EXIT_MISMATCH,
        )
    return payloads


def parse_schema_contract(sql_text: str) -> dict[str, dict[str, Any]]:
    """`03_schema_clean.sql`의 CREATE TABLE에서 컬럼별 logical type과 선언 PK를 얻는다.

    이 파싱은 검증용이다 — 결과가 `EXPECTED_COLUMN_TYPES`·`EXPECTED_PRIMARY_KEYS`
    상수와 다르면 실패시켜 "DDL이 바뀌었는데 상수만 남는" 상태를 막는다. 발급물의
    값 산출 자체는 상수를 쓴다. PK는 inline(`col type PRIMARY KEY`)과 table-level
    (`PRIMARY KEY (a, b)`) 두 표기를 모두 해석한다.
    """
    contract: dict[str, dict[str, Any]] = {}
    for match in re.finditer(
        r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\);", sql_text, re.DOTALL
    ):
        table, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        primary_key: list[str] = []
        for line in body.splitlines():
            line = line.split("--", 1)[0].strip().rstrip(",").strip()
            if not line:
                continue
            head = line.split()[0].upper()
            if head == "PRIMARY":
                pk_match = re.match(r"PRIMARY\s+KEY\s*\(([^)]*)\)", line, re.I)
                if not pk_match:
                    raise ManifestBuildError(
                        f"DDL PK 정의를 해석할 수 없습니다 — {table}: {_short(line)}",
                        EXIT_MISMATCH,
                    )
                primary_key = [part.strip() for part in pk_match.group(1).split(",")]
                continue
            if head in {"FOREIGN", "CONSTRAINT", "UNIQUE", "CHECK"}:
                continue
            parts = line.split()
            if len(parts) < 2:
                raise ManifestBuildError(
                    f"DDL 컬럼 정의를 해석할 수 없습니다 — {table}: {_short(line)}",
                    EXIT_MISMATCH,
                )
            name, data_type = parts[0], parts[1]
            if re.search(r"\bPRIMARY\s+KEY\b", line, re.I):
                primary_key.append(name)
            try:
                columns[name] = logical_type(data_type)
            except ValueNormalizationError as exc:
                raise ManifestBuildError(
                    f"DDL 타입을 logical type으로 축약할 수 없습니다 —"
                    f" {table}.{name}: {_short(data_type)}",
                    EXIT_MISMATCH,
                ) from exc
        contract[table] = {"column_types": columns, "primary_key": primary_key}
    return contract


def build_tables(payloads: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    """CSV 9개를 파싱·정규화·해싱해 tables 계약을 만든다."""
    schema_contract = parse_schema_contract(
        payloads[SCHEMA_SQL_MEMBER].decode("utf-8", errors="replace")
    )
    tables: dict[str, dict[str, Any]] = {}
    for table in sorted(EXPECTED_ROW_COUNTS):
        member = TABLE_MEMBERS[table]
        if member not in payloads:
            raise ManifestBuildError(
                f"CSV member가 판독 대상에 없습니다 — {table}", EXIT_MISMATCH
            )
        expected_columns = EXPECTED_COLUMNS[table]
        column_types = EXPECTED_COLUMN_TYPES[table]
        primary_key = EXPECTED_PRIMARY_KEYS[table]

        # DDL 실물과 상수의 대조 — 상수가 낡으면 여기서 멈춘다.
        ddl = schema_contract.get(table) or {}
        if ddl.get("column_types") != column_types:
            raise ManifestBuildError(
                f"DDL 컬럼 타입이 기대 상수와 다릅니다 — {table}", EXIT_MISMATCH
            )
        if tuple(ddl.get("primary_key") or ()) != primary_key:
            raise ManifestBuildError(
                f"DDL 선언 PK가 기대 상수와 다릅니다 — {table}", EXIT_MISMATCH
            )
        if not set(primary_key) <= set(expected_columns):
            raise ManifestBuildError(
                f"PK 컬럼이 CSV 컬럼 계약 밖입니다 — {table}", EXIT_MISMATCH
            )

        try:
            columns, rows = parse_csv_bytes(
                payloads[member], table=table, expected_columns=expected_columns
            )
        except ManifestSchemaError as exc:
            raise ManifestBuildError(str(exc), EXIT_MISMATCH) from exc

        if len(rows) != EXPECTED_ROW_COUNTS[table]:
            raise ManifestBuildError(
                f"행 수가 기준표와 다릅니다 — {table}"
                f" (기대 {EXPECTED_ROW_COUNTS[table]} / 실측 {len(rows)})",
                EXIT_MISMATCH,
            )

        try:
            normalized = [normalize_csv_row(row, column_types) for row in rows]
        except ValueNormalizationError as exc:
            raise ManifestBuildError(
                f"{table}: 값 정규화에 실패했습니다 — {_short(str(exc), 60)}",
                EXIT_MISMATCH,
            ) from exc

        tables[table] = {
            "file_id": member,
            "columns": list(columns),
            "column_types": {column: column_types[column] for column in columns},
            "primary_key": list(primary_key),
            "row_count": len(rows),
            "content_hash": hash_canonical_rows(normalized),
            "included_by_profile": list(INCLUDED_BY_PROFILE[table]),
        }
    return tables


def read_origin_package(
    origin_root: Path, selected: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """① 배포패키지의 선별 3파일을 검증·기록한다 (WBS 확대분 · 구현리뷰 1차 필수 1).

    두 겹의 방어다.
    1. **③ 대체 금지** — ①에서 기록하려는 file_id의 basename이 ③ 선별 member와
       겹치면 실패한다. 판정 규칙 "③에 있으면 ③을 쓴다"(배포패키지_기준.md §2)를
       기계로 강제하는 지점이며, RAG 3종·master.cypher처럼 양쪽에 같은 이름으로
       존재하는 파일(§3.2)이 ① 출처로 둔갑하는 것을 막는다.
    2. **해시 고정** — 실물 파일의 SHA-256이 §3.1 표(상수)와 다르면 실패한다.
    """
    final_basenames = {path.rsplit("/", 1)[-1] for path in selected}
    for key, spec in ORIGIN_ARTIFACTS.items():
        basename = spec["file_id"].rsplit("/", 1)[-1]
        if basename in final_basenames:
            raise ManifestBuildError(
                "③에 있는 artifact를 ①에서 대체할 수 없습니다 —"
                f" {key}: {spec['file_id']}",
                EXIT_MISMATCH,
            )

    if not origin_root.is_dir():
        raise ManifestBuildError("① 배포패키지 디렉터리를 찾을 수 없습니다", EXIT_USAGE)
    entries: dict[str, dict[str, Any]] = {}
    mismatched: list[str] = []
    for key, spec in ORIGIN_ARTIFACTS.items():
        path = origin_root / spec["file_id"]
        if not path.is_file():
            raise ManifestBuildError(
                f"① artifact가 없습니다 — {spec['file_id']}", EXIT_USAGE
            )
        try:
            measured = _sha256_file(path)
        except OSError as exc:
            raise ManifestBuildError(
                f"① artifact를 읽을 수 없습니다 — {spec['file_id']}", EXIT_USAGE
            ) from exc
        if measured != spec["sha256"]:
            mismatched.append(
                f"{spec['file_id']} (기대 {spec['sha256'][:8]} / 실측 {measured[:8]})"
            )
            continue
        entries[key] = {
            "file_id": spec["file_id"],
            "sha256": measured,
            "role": spec["role"],
        }
    if mismatched:
        raise ManifestBuildError(
            "① artifact 해시가 기준 문서와 다릅니다 — " + ", ".join(sorted(mismatched)),
            EXIT_MISMATCH,
        )
    return entries


def build_artifacts(
    payloads: dict[str, bytes], selected: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """비-CSV 6개 파일을 4개 키로 기록한다. 파일 해시만 남기고 구조 해석은 없다."""

    def _entry(member: str) -> dict[str, Any]:
        return {"file_id": member, "sha256": _sha256_bytes(payloads[member])}

    rag_documents = []
    for member in RAG_MEMBERS:
        entry = _entry(member)
        # intake의 pinned 판단을 그대로 승계한다(하드코딩 금지 — 기준표 §8이 곧
        # 원천이다). 2026-08-20 §8 확대로 RAG 3종도 원본 해시가 고정됐다. V5-B-1.2
        # 정정본은 별도 경로의 정본이며 이 원본 해시는 보존된다(기준표 §7).
        entry["pinned"] = bool(selected[member].get("pinned", False))
        rag_documents.append(entry)

    return {
        "schema_sql": _entry(SCHEMA_SQL_MEMBER),
        "master_cypher": _entry(MASTER_CYPHER_MEMBER),
        "generator": _entry(GENERATOR_MEMBER),
        "rag_documents": rag_documents,
    }


def _newline_style(payload: bytes) -> str:
    """재현 증적용 개행 라벨. 혼합·CR-only는 불명확한 계약이므로 거부한다."""
    crlf = payload.count(b"\r\n")
    bare_lf = payload.count(b"\n") - crlf
    bare_cr = payload.count(b"\r") - crlf
    if crlf and not bare_lf and not bare_cr:
        return "CRLF"
    if bare_lf and not crlf and not bare_cr:
        return "LF"
    if not crlf and not bare_lf and not bare_cr:
        return "NONE"
    return "MIXED"


def _generator_failure_detail(stderr: bytes | str | None) -> str:
    """Generator stderr의 마지막 비어있지 않은 한 줄만 무해화한다."""
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    else:
        text = stderr or ""
    lines = [line for line in text.splitlines() if line.strip()]
    return _short(lines[-1], 80) if lines else "stderr 없음"


def build_generator_reproduction(
    payloads: dict[str, bytes], selected: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """ZIP Generator를 격리 실행하고 결정론적 재현 증적을 만든다.

    임시 루트는 OS 임시 영역에 만들고 저장소 내부·symlink를 거부한다. Generator는
    현재 Python의 isolated mode(`-I`)와 locale만 남긴 최소 환경에서 실행한다.
    결과 inventory가 정확히 9 CSV + master.cypher가 아니거나 내용이 다르면 manifest를
    만들지 않는다. 성공 결과에는 실행 시각·호스트 등 비결정적 값이 들어가지 않는다.
    """
    generator_payload = payloads[GENERATOR_MEMBER]
    expected_generator_sha = selected[GENERATOR_MEMBER]["sha256"]
    measured_generator_sha = _sha256_bytes(generator_payload)
    if measured_generator_sha != expected_generator_sha:
        raise ManifestBuildError(
            "Generator payload 해시가 intake 등록값과 다릅니다", EXIT_MISMATCH
        )

    scratch_root: Path | None = None
    cleanup_allowed = False
    try:
        scratch_root = Path(
            tempfile.mkdtemp(prefix="fdc-generator-reproduction-")
        ).resolve()
        repository_root = REPOSITORY_ROOT.resolve()
        cleanup_allowed = (
            scratch_root != repository_root
            and scratch_root.name.startswith("fdc-generator-reproduction-")
        )
        if scratch_root == repository_root or repository_root in scratch_root.parents:
            raise ManifestBuildError(
                "Generator 임시 디렉터리가 저장소 내부에 생성됐습니다", EXIT_USAGE
            )
        if scratch_root.is_symlink() or not scratch_root.is_dir():
            raise ManifestBuildError(
                "Generator 임시 디렉터리가 안전하지 않습니다", EXIT_USAGE
            )

        run_root = scratch_root / "run"
        if run_root.exists() or run_root.is_symlink():
            raise ManifestBuildError(
                "Generator 실행 디렉터리가 fresh 상태가 아닙니다", EXIT_USAGE
            )
        generator_path = run_root / "mvp" / "gen_sample_data.py"
        generator_path.parent.mkdir(parents=True, mode=0o700)
        generator_path.write_bytes(generator_payload)

        child_env = {
            "LANG": GENERATOR_LOCALE,
            "LC_ALL": GENERATOR_LOCALE,
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(generator_path)],
                cwd=run_root,
                env=child_env,
                capture_output=True,
                check=False,
                timeout=GENERATOR_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ManifestBuildError(
                f"Generator 실행 시간이 {GENERATOR_TIMEOUT_SECONDS}초를 초과했습니다",
                EXIT_MISMATCH,
            ) from exc
        except OSError as exc:
            raise ManifestBuildError(
                "Generator를 실행할 수 없습니다", EXIT_USAGE
            ) from exc
        if completed.returncode != 0:
            detail = _generator_failure_detail(completed.stderr)
            raise ManifestBuildError(
                "Generator 실행이 실패했습니다"
                f" (exit {completed.returncode}: {detail})",
                EXIT_MISMATCH,
            )

        data_root = run_root / "sample" / "data"
        ontology_root = run_root / "sample" / "ontology"
        expected_csv_names = {Path(member).name for member in TABLE_MEMBERS.values()}
        csv_entries = list(data_root.iterdir()) if data_root.is_dir() else []
        actual_csv_names = {path.name for path in csv_entries}
        expected_ontology_names = {Path(MASTER_CYPHER_MEMBER).name}
        ontology_entries = (
            list(ontology_root.iterdir()) if ontology_root.is_dir() else []
        )
        actual_ontology_names = {path.name for path in ontology_entries}
        inventory_errors: list[str] = []
        unsafe_entries = sorted(
            str(path.relative_to(run_root))
            for path in csv_entries + ontology_entries
            if path.is_symlink() or not path.is_file()
        )
        if unsafe_entries:
            inventory_errors.append("비정상 entry=" + ",".join(unsafe_entries))
        for label, expected, actual in (
            ("CSV", expected_csv_names, actual_csv_names),
            ("ontology", expected_ontology_names, actual_ontology_names),
        ):
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            if missing:
                inventory_errors.append(f"{label} 누락={','.join(missing)}")
            if unexpected:
                inventory_errors.append(f"{label} 추가={','.join(unexpected)}")
        if inventory_errors:
            raise ManifestBuildError(
                "Generator 출력 inventory가 계약과 다릅니다 — "
                + "; ".join(inventory_errors),
                EXIT_MISMATCH,
            )
        expected_output_paths = {
            Path("sample/data") / Path(member).name for member in TABLE_MEMBERS.values()
        } | {Path("sample/ontology") / Path(MASTER_CYPHER_MEMBER).name}
        actual_output_paths = {
            path.relative_to(run_root)
            for path in run_root.rglob("*")
            if path.is_file() and path != generator_path
        }
        unexpected_output_paths = sorted(actual_output_paths - expected_output_paths)
        if unexpected_output_paths:
            raise ManifestBuildError(
                "Generator가 계약 밖 파일을 만들었습니다 — "
                + ", ".join(map(str, unexpected_output_paths)),
                EXIT_MISMATCH,
            )

        csv_results: list[dict[str, Any]] = []
        mismatched: list[str] = []
        for table in sorted(TABLE_MEMBERS):
            member = TABLE_MEMBERS[table]
            generated = (data_root / Path(member).name).read_bytes()
            expected = payloads[member]
            match = generated == expected
            csv_results.append(
                {
                    "file_id": member,
                    "expected_sha256": _sha256_bytes(expected),
                    "generated_sha256": _sha256_bytes(generated),
                    "match": match,
                }
            )
            if not match:
                mismatched.append(member)

        source_cypher = payloads[MASTER_CYPHER_MEMBER]
        generated_cypher = (
            ontology_root / Path(MASTER_CYPHER_MEMBER).name
        ).read_bytes()
        source_style = _newline_style(source_cypher)
        generated_style = _newline_style(generated_cypher)
        normalized_source = source_cypher.replace(b"\r\n", b"\n")
        normalized_generated = generated_cypher.replace(b"\r\n", b"\n")
        cypher_match = normalized_source == normalized_generated
        if source_style not in {"CRLF", "LF"} or generated_style not in {"CRLF", "LF"}:
            raise ManifestBuildError(
                "master.cypher 개행 형식이 CRLF/LF 계약과 다릅니다"
                f" (source {source_style} / generated {generated_style})",
                EXIT_MISMATCH,
            )
        if not cypher_match:
            mismatched.append(MASTER_CYPHER_MEMBER)
        if mismatched:
            raise ManifestBuildError(
                "Generator 재현 결과가 원본과 다릅니다 — " + ", ".join(mismatched),
                EXIT_MISMATCH,
            )

        return {
            "contract_version": GENERATOR_REPRODUCTION_CONTRACT_VERSION,
            "generator_sha256": measured_generator_sha,
            "csv_byte_identical": True,
            "csv_results": csv_results,
            "newline_normalized": [
                {
                    "file_id": MASTER_CYPHER_MEMBER,
                    "source_newline": source_style,
                    "generated_newline": generated_style,
                    "normalized_sha256": _sha256_bytes(normalized_source),
                    "match": True,
                }
            ],
            "mismatched": [],
        }
    except OSError as exc:
        raise ManifestBuildError(
            "Generator 재현 산출물을 안전하게 처리할 수 없습니다", EXIT_USAGE
        ) from exc
    finally:
        if scratch_root is not None and cleanup_allowed:
            active_exception = sys.exc_info()[0] is not None
            try:
                shutil.rmtree(scratch_root)
            except OSError as exc:
                if not active_exception:
                    raise ManifestBuildError(
                        "Generator 임시 디렉터리를 정리할 수 없습니다", EXIT_USAGE
                    ) from exc


def build_payload(
    epoch: dict[str, str],
    tables: dict[str, dict[str, Any]],
    artifacts: dict[str, Any],
    origin_artifacts: dict[str, dict[str, Any]],
    generator_reproduction: dict[str, Any],
    *,
    selected_entry_manifest_sha256: str,
) -> dict[str, Any]:
    """manifest v4 본문 — 시스템설계 §2.3 필수 필드 + 작업계획 §2.2 + WBS 확대분.

    키 순서가 파일 형태를 결정한다. `selected_entry_manifest_sha256`은 intake artifact
    바이트의 SHA-256으로, intake가 같은 경로에서 내용만 바뀌어도 이 manifest와의
    provenance 대조가 어긋나게 한다(최종검증 1차 필수 1). `schema_sha256`·
    `generator_sha256`은 §2.3이 정한 이름의 최상위 사본이며 `artifacts`의 값과 항상
    같다 — 소비자는 어느 쪽을 읽어도 된다.
    """
    return {
        "format_version": MANIFEST_FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "dataset_epoch": epoch["dataset_epoch"],
        "source_archive_sha256": epoch["archive_sha256"],
        "selected_entry_manifest_sha256": selected_entry_manifest_sha256,
        "schema_sha256": artifacts["schema_sql"]["sha256"],
        "generator_sha256": artifacts["generator"]["sha256"],
        "canonicalization_version": CANONICALIZATION_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "value_normalization_version": VALUE_NORMALIZATION_VERSION,
        "derived_from": {
            "dataset_epoch_artifact": "infra/bootstrap/dataset-epoch.json",
            "intake_artifact": "infra/bootstrap/final-zip-intake.json",
        },
        "tables": tables,
        "artifacts": artifacts,
        "generator_reproduction": generator_reproduction,
        # ① 선별 artifact의 출처 역할 고정(WBS 확대분). ③이 정본이고, ③에 없는 것만
        # ①에서 가져온다 — 판정 규칙과 근거 문서를 함께 기록해 소비자가 재확인한다.
        "origin_package": {
            "package": ORIGIN_PACKAGE_NAME,
            "selection_rule": ORIGIN_SELECTION_RULE,
            "reference": ORIGIN_REFERENCE_DOCUMENT,
            "artifacts": origin_artifacts,
        },
    }


def serialize(payload: dict[str, Any]) -> str:
    """manifest_v3.py:901·intake·issue 스크립트와 동일 규약."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _changed_keys(existing: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    keys = sorted(set(existing) | set(payload))
    return [key for key in keys if existing.get(key) != payload.get(key)]


def _atomic_write(path: Path, text: str) -> None:
    """같은 디렉터리 임시 파일 → fsync → 교체. 잘린 JSON을 남기지 않는다 [승계]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def write_artifact(
    out_path: Path, payload: dict[str, Any], *, verify_only: bool, confirm: bool
) -> str:
    """5-case 보호 규칙([승계] intake_final_zip.py:306-348)."""
    serialized = serialize(payload)

    def _write(outcome: str) -> str:
        try:
            _atomic_write(out_path, serialized)
        except OSError as exc:
            raise ManifestBuildError(
                "manifest를 기록할 수 없습니다", EXIT_USAGE
            ) from exc
        return outcome

    if not out_path.exists():
        if verify_only:
            raise ManifestBuildError("대조할 manifest가 없습니다", EXIT_USAGE)
        # 시스템설계 §2.3: generate는 최초 생성과 갱신 **모두** confirm을 요구한다.
        # 오염된 source가 형식 검사를 우연히 통과해도 운영자 확인 없이 최초 기준
        # artifact로 확정되지 않는다(최종검증 1차 필수 2). CM-1.1·1.2의 무승인 생성
        # 규약과 다른 점은 상위 설계가 이 artifact에 별도 요구를 두기 때문이다.
        if not confirm:
            raise ManifestBuildError(
                "manifest 최초 생성에도 --confirm이 필요합니다 — 생성 예정:"
                f" epoch {payload.get('dataset_epoch')}"
                f" · tables {len(payload.get('tables') or {})}",
                EXIT_CONFIRM_REQUIRED,
            )
        return _write("생성")

    existing_state = _read_json_object(
        out_path, label="기존 manifest", corrupt_exit=EXIT_MISMATCH
    )
    raw = existing_state["raw"]
    existing = existing_state["payload"]

    if existing == payload:
        if raw == serialized:
            return "변경 없음"
        if verify_only:
            return "객체 동일 (바이트 상이 — 검증 모드라 재작성하지 않음)"
        return _write("규약 형태로 재작성")

    changed = (
        ", ".join(_changed_keys(existing, payload)) or "(최상위 키 동일, 하위 값 상이)"
    )
    if verify_only:
        raise ManifestBuildError(
            f"manifest가 현재 ZIP·artifact 체계와 다릅니다 — {changed}", EXIT_MISMATCH
        )
    if not confirm:
        raise ManifestBuildError(
            f"기존 manifest를 덮어쓰려면 --confirm이 필요합니다 — 차이: {changed}",
            EXIT_CONFIRM_REQUIRED,
        )
    return _write("덮어쓰기")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    # 개인 절대경로를 기본값으로 두지 않는다(작업계획 §7) — 인자로만 받는다.
    parser.add_argument("--archive", type=Path, required=True)
    # ① 배포패키지 루트. --archive와 같은 이유로 기본값을 두지 않는다.
    parser.add_argument("--origin-package", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=MANIFEST_V4_PATH)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--confirm", "--force", action="store_true", dest="confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    # 비-UTF-8 stdout에서 성공 출력이 죽으면 "부작용 완료 + exit 1 오독"이 된다
    # (V5-CM-1.2 구현리뷰 1차 필수 1과 같은 완화를 처음부터 적용).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = _parser().parse_args(argv)
    try:
        if args.verify_only and args.confirm:
            raise ManifestBuildError(
                "--verify-only와 승인 플래그(--confirm/--force)는 함께 쓸 수 없습니다",
                EXIT_USAGE,
            )
        epoch = load_epoch(DATASET_EPOCH_PATH)
        selected = load_intake(
            INTAKE_ARTIFACT_PATH, archive_sha256=epoch["archive_sha256"]
        )
        try:
            selected_entry_sha = _sha256_file(INTAKE_ARTIFACT_PATH)
        except OSError as exc:
            raise ManifestBuildError(
                "intake artifact를 읽을 수 없습니다", EXIT_USAGE
            ) from exc
        payloads = read_members(
            args.archive, archive_sha256=epoch["archive_sha256"], selected=selected
        )
        origin_artifacts = read_origin_package(args.origin_package, selected)
        tables = build_tables(payloads)
        artifacts = build_artifacts(payloads, selected)
        generator_reproduction = build_generator_reproduction(payloads, selected)
        payload = build_payload(
            epoch,
            tables,
            artifacts,
            origin_artifacts,
            generator_reproduction,
            selected_entry_manifest_sha256=selected_entry_sha,
        )
        outcome = write_artifact(
            args.out, payload, verify_only=args.verify_only, confirm=args.confirm
        )
    except ManifestBuildError as exc:
        print(f"[manifest-v4] 실패: {exc.reason}", file=sys.stderr)
        return exc.exit_code

    total_rows = sum(entry["row_count"] for entry in payload["tables"].values())
    print(
        f"[manifest-v4] {outcome} · epoch {payload['dataset_epoch']}"
        f" · tables {len(payload['tables'])} · rows {total_rows}"
        f" · 대상 {args.out.name}"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
