"""최종 profile manifest **candidate builder** (`V5-CM-1.8`).

DB 상태를 읽어 그대로 manifest로 복사하지 않는다. 그러면 live drift까지 "정답"으로
등록하게 된다(계획 §0.2). candidate는 검증된 저장소 artifact와 코드 계약만으로
결정론적으로 만들고, 공용 DB는 **그 candidate와 일치하는지 확인만** 한다.

```text
source-manifest-v4.json  base 9의 columns·row_count·content_hash
      + 코드 계약        R03 12컬럼 · Runtime 9 table · RAG · nl_query_log
      + profile 정책     §3.4 표
      ↓
candidate 2종 (runtime 22 · evaluation 13)
      ↓  묶음 2·3
read-only 대조 후에만 저장소 active manifest 교체
```

**Runtime RAG만 예외다.** source CSV가 없는 기존 B 적재 산출물이라 provenance Gate 5종을
통과한 경우에만 candidate hash를 만든다(§3.4). 이 모듈은 그 Gate의 **입력을 요구**하고,
값이 없으면 candidate를 만들지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import apply_agent_runtime
import apply_reference_extensions as reference_v4
import apply_reference_extensions_v5 as reference_v5
import build_source_manifest_v4 as source_v4
import manifest_v3

BOOTSTRAP_ROOT = manifest_v3.BOOTSTRAP_ROOT
SOURCE_MANIFEST_V4_PATH = BOOTSTRAP_ROOT / "source-manifest-v4.json"

#: source manifest v4가 소유하는 canonical CSV **9종**.
#:
#: `manifest_v3.SOURCE_TABLE_FILES`(8종)와 다르다 — 그쪽은 `dim_parameter`가 없는 구 v3
#: 목록이다. 최종 base 계약의 정본은 `V5-CM-1.3`·`V5-CM-1.4`의 v4 발급기다.
SOURCE_TABLES: frozenset[str] = frozenset(source_v4.TABLE_MEMBERS)

#: 두 profile이 공유하는 reference table. R03는 별도 계약이라 여기 넣지 않는다.
REFERENCE_TABLES: tuple[str, ...] = ("document", "document_chunk", "nl_query_log")

#: 누적 로그라 내용을 고정하지 않는다.
SCHEMA_ONLY_TABLES: frozenset[str] = frozenset({"nl_query_log"})

#: hash에서 제외하는 **적재 시각** 컬럼. 두 Runtime DB가 독립 적재해 항상 다르다.
RUNTIME_RAG_VOLATILE_COLUMNS: Mapping[str, frozenset[str]] = MappingProxyType(
    {"document": frozenset({"created_at"})}
)
RUNTIME_RAG_CONTENT_SUBSET: frozenset[str] = frozenset(RUNTIME_RAG_VOLATILE_COLUMNS)

#: Runtime 전용 9 table. 초기 stage라 전부 0행이다.
RUNTIME_ONLY_TABLES: tuple[str, ...] = tuple(
    sorted(apply_agent_runtime.EXPECTED_TABLE_COLUMNS)
)

#: `V5-B-1.3` 적재 결과. **source CSV가 없어 provenance Gate가 필요하다**(§3.4).
RUNTIME_RAG_ROWS: Mapping[str, int] = MappingProxyType(
    {"document": 3, "document_chunk": 25}
)

#: candidate에 **content hash로 들어가는** RAG table. `document`는 `schema_only`라
#: 행 수만 provenance Gate가 확인하고 hash는 만들지 않는다.
RUNTIME_RAG_HASHED: frozenset[str] = frozenset({"document", "document_chunk"})


class CandidateError(manifest_v3.VerificationError):
    """candidate를 만들 수 없다. **부분 결과를 반환하지 않는다.**"""


def _empty_hash() -> str:
    """빈 table의 content hash.

    문자열 상수를 복사하지 않는다 — 해싱 규약이 바뀌면 두 곳이 갈린다(§3.4).
    """

    return manifest_v3.hash_canonical_rows([])


#: source manifest v4의 exact top-level key.
SOURCE_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "artifact_type",
        "artifacts",
        "canonicalization_version",
        "dataset_epoch",
        "derived_from",
        "format_version",
        "generator_reproduction",
        "generator_sha256",
        "hash_algorithm",
        "origin_package",
        "schema_sha256",
        "selected_entry_manifest_sha256",
        "source_archive_sha256",
        "tables",
        "value_normalization_version",
    }
)

#: table entry의 exact key.
SOURCE_ENTRY_KEYS: frozenset[str] = frozenset(
    {
        "column_types",
        "columns",
        "content_hash",
        "file_id",
        "included_by_profile",
        "primary_key",
        "row_count",
    }
)

SOURCE_FORMAT_VERSION = 4
SOURCE_ARTIFACT_TYPE = "source_files"

#: **최종 source manifest v4의 불변 증적.**
#:
#: epoch·archive만 보면 유효한 64자리 hash를 아무 값으로나 바꾼 artifact도 통과한다.
#: 그러면 손상된 source가 candidate와 DB logical type의 정본으로 승격된다
#: (구현리뷰 7차 필수 1). canonical payload 전체를 고정한다.
SOURCE_PAYLOAD_SHA256 = (
    "fe415294ab06afc532964f78ba9352678d918d2ef4690b6d5ea2e43c85f97720"
)


def assert_source_manifest(payload: Any) -> dict[str, Any]:
    """**파일 경로와 주입 경로가 함께 거치는 단일 gate.**

    `build_profile_candidate(source=...)`가 loader를 우회하던 구멍을 막는다. 검증을
    loader에만 두면 주입 입력은 epoch·archive 검사조차 받지 않는다.
    """

    if not isinstance(payload, dict):
        raise CandidateError("source manifest v4 최상위 값이 object가 아닙니다")
    if set(payload) != SOURCE_TOP_LEVEL_KEYS:
        raise CandidateError("source manifest v4 top-level key가 계약과 다릅니다")

    if payload["format_version"] != SOURCE_FORMAT_VERSION:
        raise CandidateError("source manifest v4 format_version이 4가 아닙니다")
    if payload["artifact_type"] != SOURCE_ARTIFACT_TYPE:
        raise CandidateError("source manifest v4 artifact_type이 잘못됐습니다")
    if payload["dataset_epoch"] != manifest_v3.DATASET_EPOCH:
        raise CandidateError("source manifest v4가 최종 epoch이 아닙니다")
    if payload["source_archive_sha256"] != manifest_v3.FINAL_ARCHIVE_SHA256:
        raise CandidateError("source manifest v4 archive가 최종 ZIP이 아닙니다")
    if payload["hash_algorithm"] != manifest_v3.HASH_ALGORITHM:
        raise CandidateError("source manifest v4 hash_algorithm이 다릅니다")
    if (
        payload["value_normalization_version"]
        != manifest_v3.VALUE_NORMALIZATION_VERSION
    ):
        raise CandidateError(
            "source manifest v4 value_normalization_version이 다릅니다"
        )

    tables = payload["tables"]
    if not isinstance(tables, dict) or set(tables) != SOURCE_TABLES:
        raise CandidateError("source manifest v4 table 집합이 계약과 다릅니다")
    for table, entry in tables.items():
        _assert_source_entry(table, entry)

    # **마지막 겹.** 위 검사를 모두 통과해도 개별 값은 바뀔 수 있다. 최종 artifact는
    # 이미 발급이 끝난 불변 정본이므로 payload 전체를 고정한다.
    if manifest_v3.canonical_payload_sha256(payload) != SOURCE_PAYLOAD_SHA256:
        raise CandidateError("source manifest v4 payload가 정본과 다릅니다")
    return payload


def _assert_source_entry(table: str, entry: Any) -> None:
    if not isinstance(entry, dict) or set(entry) != SOURCE_ENTRY_KEYS:
        raise CandidateError(f"source manifest v4 entry key가 다릅니다: {table}")

    columns = entry["columns"]
    column_types = entry["column_types"]
    primary_key = entry["primary_key"]
    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(name, str) and name for name in columns)
        or len(set(columns)) != len(columns)
    ):
        raise CandidateError(f"source manifest v4 columns가 잘못됐습니다: {table}")
    # 키 집합 대조는 두지 않는다 — payload digest가 이미 덮어 어떤 변이로도 독립적으로
    # 실패하지 않았다. 여기서는 뒤 코드가 KeyError를 내지 않을 만큼만 본다.
    if not isinstance(column_types, dict) or not column_types:
        raise CandidateError(f"source manifest v4 column_types가 다릅니다: {table}")
    if not isinstance(primary_key, list) or not set(primary_key) <= set(columns):
        raise CandidateError(f"source manifest v4 primary_key가 잘못됐습니다: {table}")

    row_count = entry["row_count"]
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise CandidateError(f"source manifest v4 row_count가 잘못됐습니다: {table}")
    if not manifest_v3.HEX_SHA256_PATTERN.fullmatch(str(entry["content_hash"])):
        raise CandidateError(f"source manifest v4 content_hash 형식 오류: {table}")


def load_source_manifest(path: Path = SOURCE_MANIFEST_V4_PATH) -> dict[str, Any]:
    """검증된 source manifest v4를 읽는다."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CandidateError("source manifest v4를 읽을 수 없습니다") from exc
    return assert_source_manifest(payload)


def _source_entry(source: Mapping[str, Any], table: str) -> dict[str, Any]:
    entry = source["tables"][table]
    columns = entry.get("columns")
    row_count = entry.get("row_count")
    content_hash = entry.get("content_hash")
    if (
        not isinstance(columns, list)
        or not columns
        or not isinstance(row_count, int)
        or isinstance(row_count, bool)
        or row_count < 0
        or not isinstance(content_hash, str)
    ):
        raise CandidateError(f"source manifest v4 entry가 잘못됐습니다: {table}")
    return {
        "columns": list(columns),
        "verification_policy": "immutable_content",
        "row_count": row_count,
        "content_hash": content_hash,
    }


def _empty_entry(columns: tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {
        "columns": list(columns),
        "verification_policy": "bootstrap_empty",
        "row_count": 0,
        "content_hash": _empty_hash(),
    }


def _schema_only_entry(columns: tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {"columns": list(columns), "verification_policy": "schema_only"}


def _v4_columns(table: str) -> tuple[str, ...]:
    return tuple(
        name for name, _type, _nullable in reference_v4.EXPECTED_TABLE_COLUMNS[table]
    )


def _r03_columns() -> tuple[str, ...]:
    """**V5 12컬럼이다.** V4 11컬럼 registry로 내려가지 않는다(§3.5-2·3)."""

    return tuple(name for name, _type, _length, _nullable in reference_v5.R03_COLUMNS)


def _runtime_only_columns(table: str) -> tuple[str, ...]:
    return tuple(
        column.name for column in apply_agent_runtime.EXPECTED_TABLE_COLUMNS[table]
    )


def build_profile_candidate(
    profile: str,
    *,
    source: Mapping[str, Any],
    runtime_rag: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """profile 하나의 최종 manifest candidate를 만든다.

    `runtime_rag`는 `{table: {"row_count": int, "content_hash": str}}`이며 Runtime에서만
    필요하다. **`None`이면 Runtime candidate를 만들지 않는다** — provenance Gate를
    통과한 값 없이 live를 복사하는 경로를 열지 않기 위해서다(§3.4-5).
    """

    stage = reference_v5.FINAL_STAGE_BY_PROFILE.get(profile)
    if stage is None:
        raise CandidateError(f"알 수 없는 profile입니다: {profile}")
    # 주입 입력도 파일 입력과 **같은 gate**를 거친다(구현리뷰 7차 필수 1).
    source = assert_source_manifest(source)
    contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[(profile, stage)]

    tables: dict[str, dict[str, Any]] = {}

    # ── base 9 ──────────────────────────────────────────────────────────
    for table in sorted(SOURCE_TABLES - {"action_history"}):
        tables[table] = _source_entry(source, table)

    # ── action_history: profile projection ─────────────────────────────
    if contract.action_rows == 0:
        action = _empty_entry(source["tables"]["action_history"]["columns"])
    else:
        action = _source_entry(source, "action_history")
        if action["row_count"] != contract.action_rows:
            raise CandidateError("source action_history 행 수가 stage 계약과 다릅니다")
    if contract.action_fixture_type is not None:
        action["fixture_type"] = contract.action_fixture_type
    tables["action_history"] = action

    # ── R03: A-1.4 전이라 두 profile 모두 0행 ──────────────────────────
    tables[reference_v5.R03_TABLE] = _empty_entry(_r03_columns())

    # ── RAG·nl_query_log ───────────────────────────────────────────────
    for table in REFERENCE_TABLES:
        columns = _v4_columns(table)
        if table in SCHEMA_ONLY_TABLES:
            tables[table] = _schema_only_entry(columns)
        elif profile == "runtime" and table in RUNTIME_RAG_CONTENT_SUBSET:
            # **`created_at`만 hash에서 뺀다.**
            #
            # 적재 시각(`now()`)이라 두 Runtime DB가 독립 적재하는 한 항상 다르다
            # (실측: agent 16:37:57 vs e2e 16:33:07). 그렇다고 `schema_only`로 두면
            # 3행이 0행이 돼도, 업무 컬럼이 변조돼도 통과한다 — 검증을 없애는 것이지
            # 해결이 아니다(구현리뷰 16차 필수 1).
            #
            # catalog 대조는 전체 `columns`로 그대로 하고, hash만 부분집합으로 낸다.
            tables[table] = _runtime_rag_entry(table, columns, runtime_rag)
            tables[table]["content_columns"] = [
                name
                for name in columns
                if name not in RUNTIME_RAG_VOLATILE_COLUMNS[table]
            ]
        elif profile == "runtime":
            tables[table] = _runtime_rag_entry(table, columns, runtime_rag)
        else:
            # B-1.1이 Evaluation schema를 적용한 뒤 0행이다.
            tables[table] = _empty_entry(columns)

    # ── Runtime 전용 9 table ───────────────────────────────────────────
    if profile == "runtime":
        for table in RUNTIME_ONLY_TABLES:
            tables[table] = _empty_entry(_runtime_only_columns(table))

    expected = reference_v5.FINAL_PROFILE_TABLE_COUNTS[profile]
    if len(tables) != expected:
        raise CandidateError(f"{profile} candidate inventory가 {expected}개가 아닙니다")

    candidate = {
        "format_version": manifest_v3.MANIFEST_FORMAT_VERSION,
        "artifact_type": "db_bootstrap",
        "dataset_epoch": manifest_v3.DATASET_EPOCH,
        "source_archive_sha256": manifest_v3.FINAL_ARCHIVE_SHA256,
        "correction_version": manifest_v3.FINAL_CORRECTION_VERSION,
        "hash_algorithm": manifest_v3.HASH_ALGORITHM,
        "value_normalization_version": manifest_v3.VALUE_NORMALIZATION_VERSION,
        "profile": profile,
        "applies_to": list(manifest_v3.PROFILE_APPLIES_TO[profile]),
        "bootstrap_stage": stage,
        "schema_stage": contract.schema_stage,
        "applied_migrations": list(contract.applied_migrations),
        "tables": tables,
    }

    # **자기 산출물을 기존 검증기로 다시 본다.** 여기서 통과하지 못하면 등록 단계까지
    # 갈 이유가 없다(계획 §3.6).
    manifest_v3.validate_manifest_schema(
        candidate,
        expected_artifact_type="db_bootstrap",
        expected_profile=profile,
        expected_stage=stage,
        expected_archive_sha256=manifest_v3.FINAL_ARCHIVE_SHA256,
    )
    return candidate


def _runtime_rag_entry(
    table: str,
    columns: tuple[str, ...],
    runtime_rag: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Runtime RAG는 **provenance Gate를 통과한 값**으로만 만든다(§3.4).

    source CSV가 없는 유일한 내용 보존 대상이라, live SELECT 값을 그대로 복사하면
    drift가 정답이 된다. 값이 없으면 candidate 자체를 만들지 않는다.
    """

    if runtime_rag is None:
        raise CandidateError("RAG_PROVENANCE_REQUIRED")
    entry = runtime_rag.get(table)
    if not isinstance(entry, Mapping):
        raise CandidateError("RAG_PROVENANCE_REQUIRED")

    row_count = entry.get("row_count")
    content_hash = entry.get("content_hash")
    if row_count != RUNTIME_RAG_ROWS[table]:
        raise CandidateError("RAG_PROVENANCE_MISMATCH")
    if not isinstance(
        content_hash, str
    ) or not manifest_v3.HEX_SHA256_PATTERN.fullmatch(content_hash):
        raise CandidateError("RAG_PROVENANCE_MISMATCH")

    return {
        "columns": list(columns),
        "verification_policy": "immutable_content",
        "row_count": row_count,
        "content_hash": content_hash,
    }


def build_final_bundle(
    *,
    source: Mapping[str, Any] | None = None,
    runtime_rag: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """두 profile candidate를 한 번에 만든다. 하나라도 실패하면 전부 실패한다."""

    payload = (
        assert_source_manifest(source) if source is not None else load_source_manifest()
    )
    return {
        profile: build_profile_candidate(
            profile, source=payload, runtime_rag=runtime_rag
        )
        for profile in sorted(reference_v5.FINAL_STAGE_BY_PROFILE)
    }
