"""Apply only the B Knowledge RAG document schema.

This runner exists for the case where the shared database already has valid
base data, but the RAG-only tables still follow an older design.  It must not
touch base tables, alarm tables, action/runtime tables, or Neo4j-related data.

The operation is intentionally narrow:

* create the ``vector`` extension if it is missing;
* drop old RAG-only tables ``document_chunk``, ``document``,
  ``document_corpus``;
* recreate mentor-compatible ``document`` and ``document_chunk``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
#: **schema 적용 대상.** `postgres_transition.B_SCHEMA_TARGETS`와 같다.
#:
#: 적재 대상(`load_rag_documents.ALLOWED_RAG_DATABASES` ·
#: `postgres_transition.B_LOADED_RAG_TARGETS`)보다 **넓다.** 문서를 넣지 않는 target에도
#: schema는 맞춰야 하기 때문이다. 두 집합을 같다고 강제하면 안 된다 — 적재하지 않은
#: target에 B의 fingerprint 산식이 돌아 `UndefinedColumn`으로 죽는다.
#:
#: `kosa_text2sql`이 여기 들어간 것은 그 DB의 RAG table이 구 epoch(PR #48) 형상이라
#: `V5-CM-1.8`이 요구하는 evaluation 물리 inventory를 만들 수 없기 때문이다.
#: **일회성 호환 보완**이며 `V5-B-1.1` 완료를 뜻하지 않는다. `PUBLIC` revoke·role
#: GRANT·runner 정비는 B 담당 범위다.
ALLOWED_RAG_DATABASES = frozenset(
    {"kosa_agent", "kosa_agent_e2e", "kosa_text2sql"}
)
RAG_TABLES_TO_REPLACE = ("document_chunk", "document", "document_corpus")
RAG_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

DROP TABLE IF EXISTS document_chunk;
DROP TABLE IF EXISTS document;
DROP TABLE IF EXISTS document_corpus;

CREATE TABLE document (
    doc_id       varchar(30)  PRIMARY KEY,
    title        varchar(200) NOT NULL,
    doc_type     varchar(20)  CHECK (doc_type IN ('SPEC','MANUAL','TROUBLESHOOT')),
    model_code   varchar(20),
    source_path  varchar(300),
    version      varchar(20),
    created_at   timestamp    DEFAULT now()
);

CREATE TABLE document_chunk (
    chunk_id       varchar(40)  PRIMARY KEY,
    doc_id         varchar(30)  NOT NULL REFERENCES document(doc_id) ON DELETE CASCADE,
    chunk_seq      integer      NOT NULL,
    section_title  varchar(200),
    content        text         NOT NULL,
    token_cnt      integer,
    embedding      vector(1024),
    metadata_json  jsonb,
    UNIQUE (doc_id, chunk_seq)
);
"""


class RagSchemaError(RuntimeError):
    """RAG schema target or mutation contract failed."""


def validate_rag_target(database: str) -> BootstrapTarget:
    if database not in ALLOWED_RAG_DATABASES:
        allowed = ", ".join(sorted(ALLOWED_RAG_DATABASES))
        raise TargetValidationError(f"RAG schema는 {allowed}만 허용합니다")
    return load_bootstrap_target(database)


def _mapping_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def inspect_rag_objects(connection: Any) -> dict[str, int]:
    rows = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name IN ('document_corpus', 'document', 'document_chunk')
            """
        )
    )
    existing = {str(row["table_name"]) for row in rows}
    counts: dict[str, int] = {}
    for table in RAG_TABLES_TO_REPLACE:
        if table not in existing:
            counts[table] = 0
            continue
        count_row = connection.exec_driver_sql(
            f'SELECT count(*) AS row_count FROM "{table}"'
        ).mappings().one()
        counts[table] = int(count_row["row_count"])
    return counts


def apply_rag_schema(connection: Any) -> None:
    for statement in [part.strip() for part in RAG_SCHEMA_SQL.split(";") if part.strip()]:
        connection.exec_driver_sql(statement)


def verify_rag_schema(connection: Any) -> None:
    rows = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name IN ('document', 'document_chunk')
             ORDER BY table_name, ordinal_position
            """
        )
    )
    actual: dict[str, list[str]] = {"document": [], "document_chunk": []}
    for row in rows:
        actual[str(row["table_name"])].append(str(row["column_name"]))
    expected = {
        "document": [
            "doc_id",
            "title",
            "doc_type",
            "model_code",
            "source_path",
            "version",
            "created_at",
        ],
        "document_chunk": [
            "chunk_id",
            "doc_id",
            "chunk_seq",
            "section_title",
            "content",
            "token_cnt",
            "embedding",
            "metadata_json",
        ],
    }
    if actual != expected:
        raise RagSchemaError("RAG document schema 검증에 실패했습니다")

    stale = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name = 'document_corpus'
            """
        )
    )
    if stale:
        raise RagSchemaError("document_corpus가 남아 있습니다")


def run_apply(*, database: str) -> dict[str, Any]:
    load_dotenv(REPOSITORY_ROOT / ".env")
    target = validate_rag_target(database)
    url = target.create_url()
    validate_url_components(url, target)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            validate_connected_identity(connection, target)
            set_and_validate_public_search_path(connection)
            before = inspect_rag_objects(connection)
            apply_rag_schema(connection)
            verify_rag_schema(connection)
            return {
                "database": target.database,
                "replaced_tables": list(RAG_TABLES_TO_REPLACE),
                "row_counts_before": before,
            }
    finally:
        engine.dispose()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        choices=sorted(ALLOWED_RAG_DATABASES),
        help="RAG schema 적용 대상 DB",
    )
    parser.add_argument(
        "--confirm-target",
        required=True,
        choices=sorted(ALLOWED_RAG_DATABASES),
        help="오조작 방지를 위해 --database와 같은 값을 넣는다.",
    )
    args = parser.parse_args(argv)
    if args.database != args.confirm_target:
        raise RagSchemaError("--confirm-target과 --database가 다릅니다")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = run_apply(database=args.database)
    print(
        "rag_schema_applied "
        f"database={result['database']} "
        f"row_counts_before={result['row_counts_before']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
