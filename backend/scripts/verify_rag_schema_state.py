"""V5-B-1.1 RAG 스키마 상태를 읽기 전용으로 검증한다.

live public schema가 이미 B Knowledge RAG 스키마 계약과 맞는지 확인하는
증적용 runner다. create/drop/grant/revoke/insert/update/delete를 의도적으로
수행하지 않는다. 스키마가 이미 맞다면 ``apply_rag_schema.py``를 다시 실행하지
않고 이 검증기로 안전하게 완료 증적을 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apply_rag_schema import (
    ALLOWED_RAG_DATABASES,
    RAG_CONSTRAINT_CONTRACT,
    RAG_DEFAULT_CONTRACT,
    RAG_TABLES_TO_REPLACE,
    REPOSITORY_ROOT,
    verify_rag_objects,
    verify_rag_schema,
)
from db_target import (
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    set_and_validate_public_search_path,
    validate_connected_identity,
    validate_url_components,
)
from dotenv import load_dotenv
from sqlalchemy import create_engine

ARTIFACT_ROOT = REPOSITORY_ROOT / "backend" / "artifacts" / "rag_schema_validation"
EXPECTED_RAG_COLUMN_TYPES = {
    "document": {
        "doc_id": "character varying(30)",
        "title": "character varying(200)",
        "doc_type": "character varying(20)",
        "model_code": "character varying(20)",
        "source_path": "character varying(300)",
        "version": "character varying(20)",
        "created_at": "timestamp without time zone",
    },
    "document_chunk": {
        "chunk_id": "character varying(40)",
        "doc_id": "character varying(30)",
        "chunk_seq": "integer",
        "section_title": "character varying(200)",
        "content": "text",
        "token_cnt": "integer",
        "embedding": "vector(1024)",
        "metadata_json": "jsonb",
    },
}

VECTOR_EXTENSION_SQL = """
SELECT extversion
  FROM pg_extension
 WHERE extname = 'vector'
"""

RAG_COLUMN_TYPES_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid
 WHERE n.nspname = 'public'
   AND c.relname = ANY(%s)
   AND a.attnum > 0
   AND NOT a.attisdropped
 ORDER BY c.relname, a.attnum
"""

RAG_TABLE_EXISTENCE_SQL = """
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND table_name IN ('document', 'document_chunk', 'document_corpus')
 ORDER BY table_name
"""

PUBLIC_PRIVILEGES_SQL = """
SELECT object_type, object_name, privilege_type
FROM (
  SELECT 'database'::text AS object_type, d.datname::text AS object_name,
         x.privilege_type::text
    FROM pg_database d
    CROSS JOIN LATERAL aclexplode(
        coalesce(d.datacl, acldefault('d', d.datdba))
    ) x
   WHERE d.datname = current_database()
     AND x.grantee = 0
  UNION ALL
  SELECT 'schema', n.nspname, x.privilege_type::text
    FROM pg_namespace n
    CROSS JOIN LATERAL aclexplode(
        coalesce(n.nspacl, acldefault('n', n.nspowner))
    ) x
   WHERE n.nspname = 'public'
     AND x.grantee = 0
  UNION ALL
  SELECT 'relation', c.relname, x.privilege_type::text
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) x
   WHERE n.nspname = 'public'
     AND c.relkind IN ('r', 'p')
     AND c.relname = ANY(%s)
     AND x.grantee = 0
  UNION ALL
  SELECT 'column', c.relname || '.' || a.attname, x.privilege_type::text
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    CROSS JOIN LATERAL aclexplode(a.attacl) x
   WHERE n.nspname = 'public'
     AND c.relkind IN ('r', 'p')
     AND c.relname = ANY(%s)
     AND a.attnum > 0
     AND NOT a.attisdropped
     AND x.grantee = 0
) public_acl
ORDER BY object_type, object_name, privilege_type
"""

DUPLICATE_RAG_OBJECTS_SQL = """
SELECT n.nspname AS schema_name,
       c.relname AS object_name,
       c.relkind AS object_kind
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relname = ANY(%s)
   AND n.nspname <> 'public'
 ORDER BY n.nspname, c.relname, c.relkind
"""


class RagSchemaStateError(RuntimeError):
    """live RAG 스키마 상태가 V5-B-1.1 계약을 만족하지 않는다."""


def _mapping_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def _check_rag_objects(connection: Any) -> str:
    try:
        verify_rag_schema(connection)
        verify_rag_objects(connection)
    except Exception as exc:
        return str(exc)
    return "PASS"


def _column_types(connection: Any) -> dict[str, dict[str, str]]:
    actual: dict[str, dict[str, str]] = {"document": {}, "document_chunk": {}}
    for row in _mapping_rows(
        connection.exec_driver_sql(
            RAG_COLUMN_TYPES_SQL,
            (sorted(EXPECTED_RAG_COLUMN_TYPES),),
        )
    ):
        actual[str(row["table_name"])][str(row["column_name"])] = str(row["data_type"])
    return actual


def _public_privileges(connection: Any) -> list[dict[str, str]]:
    return [
        {
            "object_type": str(row["object_type"]),
            "object_name": str(row["object_name"]),
            "privilege_type": str(row["privilege_type"]),
        }
        for row in _mapping_rows(
            connection.exec_driver_sql(
                PUBLIC_PRIVILEGES_SQL,
                (
                    sorted(RAG_CONSTRAINT_CONTRACT),
                    sorted(RAG_CONSTRAINT_CONTRACT),
                ),
            )
        )
    ]


def _duplicate_rag_objects(connection: Any) -> list[dict[str, str]]:
    return [
        {
            "schema_name": str(row["schema_name"]),
            "object_name": str(row["object_name"]),
            "object_kind": str(row["object_kind"]),
        }
        for row in _mapping_rows(
            connection.exec_driver_sql(
                DUPLICATE_RAG_OBJECTS_SQL,
                (sorted(RAG_TABLES_TO_REPLACE),),
            )
        )
    ]


def verify_connection(connection: Any, *, target: BootstrapTarget) -> dict[str, Any]:
    """target DB 하나를 변경 없이 조회한다."""

    existing_tables = sorted(
        str(row["table_name"])
        for row in _mapping_rows(connection.exec_driver_sql(RAG_TABLE_EXISTENCE_SQL))
    )
    vector_versions = [
        str(row["extversion"])
        for row in _mapping_rows(connection.exec_driver_sql(VECTOR_EXTENSION_SQL))
    ]
    column_types = _column_types(connection)
    public_privileges = _public_privileges(connection)
    duplicate_rag_objects = _duplicate_rag_objects(connection)
    object_contract = _check_rag_objects(connection)

    checks = {
        "vector_extension_present": len(vector_versions) == 1,
        "adopted_tables_present": {
            "document": "document" in existing_tables,
            "document_chunk": "document_chunk" in existing_tables,
        },
        "legacy_document_corpus_absent": "document_corpus" not in existing_tables,
        "column_types_match": column_types == EXPECTED_RAG_COLUMN_TYPES,
        "constraint_and_default_contract": object_contract == "PASS",
        "public_privilege_count": len(public_privileges),
        "duplicate_rag_object_count": len(duplicate_rag_objects),
    }
    passed = (
        checks["vector_extension_present"]
        and all(checks["adopted_tables_present"].values())
        and checks["legacy_document_corpus_absent"]
        and checks["column_types_match"]
        and checks["constraint_and_default_contract"]
        and checks["public_privilege_count"] == 0
        and checks["duplicate_rag_object_count"] == 0
    )
    return {
        "database": target.database,
        "profile": target.profile,
        "status": "PASS" if passed else "FAIL",
        "checks": checks,
        "vector_extension_versions": vector_versions,
        "existing_rag_tables": existing_tables,
        "expected_column_types": EXPECTED_RAG_COLUMN_TYPES,
        "actual_column_types": column_types,
        "expected_constraints": RAG_CONSTRAINT_CONTRACT,
        "expected_defaults": RAG_DEFAULT_CONTRACT,
        "constraint_default_result": object_contract,
        "public_privileges": public_privileges,
        "duplicate_rag_objects": duplicate_rag_objects,
    }


def validate_rag_schema_state(report: Mapping[str, Any]) -> None:
    """DB별 report가 B-1.1 완료 증적으로 실패 상태이면 예외를 던진다."""

    if report.get("status") == "PASS":
        return
    checks = report.get("checks", {})
    failed = [
        key
        for key, value in checks.items()
        if (isinstance(value, bool) and not value)
        or (key.endswith("_count") and value != 0)
        or (
            key == "adopted_tables_present"
            and isinstance(value, Mapping)
            and not all(value.values())
        )
    ]
    database = report.get("database", "?")
    raise RagSchemaStateError(
        f"{database} RAG schema state 검증 실패: {', '.join(failed)}"
    )


def run_verify(*, database: str) -> dict[str, Any]:
    load_dotenv(REPOSITORY_ROOT / ".env")
    target = validate_rag_target(database)
    url = target.create_url()
    validate_url_components(url, target)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            validate_connected_identity(connection, target)
            set_and_validate_public_search_path(connection)
            report = verify_connection(connection, target=target)
            validate_rag_schema_state(report)
            return report
    finally:
        engine.dispose()


def validate_rag_target(database: str) -> BootstrapTarget:
    if database not in ALLOWED_RAG_DATABASES:
        allowed = ", ".join(sorted(ALLOWED_RAG_DATABASES))
        raise TargetValidationError(f"RAG schema 검증은 {allowed}만 허용합니다")
    return load_bootstrap_target(database)


def build_report(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    status = (
        "PASS" if all(result.get("status") == "PASS" for result in results) else "FAIL"
    )
    return {
        "format_version": 1,
        "artifact_type": "rag_schema_validation",
        "task_id": "V5-B-1.1",
        "status": status,
        "checked_at": datetime.now(UTC).isoformat(),
        "databases": list(results),
    }


def save_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--database", choices=sorted(ALLOWED_RAG_DATABASES))
    group.add_argument(
        "--all",
        action="store_true",
        help="세 RAG schema target을 모두 검증한다.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="검증 artifact JSON 저장 경로. 생략하면 stdout만 출력한다.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    databases = sorted(ALLOWED_RAG_DATABASES) if args.all else [args.database]
    results = [run_verify(database=database) for database in databases]
    report = build_report(results)
    if args.output is not None:
        save_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
