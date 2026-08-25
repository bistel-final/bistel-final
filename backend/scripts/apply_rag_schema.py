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
from collections.abc import Mapping, Sequence
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
ALLOWED_RAG_DATABASES = frozenset({"kosa_agent", "kosa_agent_e2e", "kosa_text2sql"})
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


#: `RAG_SCHEMA_SQL`이 만드는 **object 계약**. 위 DDL과 한 몸이며 따로 움직이면 안 된다.
#:
#: 초판 `verify_rag_schema()`는 column **이름**만 봤다. 그래서 `document_chunk`의
#: `(doc_id, chunk_seq)` UNIQUE나 `doc_id` FK, `document.doc_type` CHECK를 지워도
#: 검증을 통과했다 — DDL이 만드는 것과 검증이 보는 것이 달랐다(`V5-CM-3.4` 구현리뷰
#: 5차 필수 2).
#:
#: `pg_get_constraintdef()`는 출력을 정규화하므로 여기 문자열은 catalog 표현 그대로다.
RAG_CONSTRAINT_CONTRACT: dict[str, dict[str, str]] = {
    "document": {
        "document_pkey": "PRIMARY KEY (doc_id)",
        "document_doc_type_check": (
            "CHECK (((doc_type)::text = ANY "
            "((ARRAY['SPEC'::character varying, 'MANUAL'::character varying, "
            "'TROUBLESHOOT'::character varying])::text[])))"
        ),
    },
    "document_chunk": {
        "document_chunk_pkey": "PRIMARY KEY (chunk_id)",
        "document_chunk_doc_id_chunk_seq_key": "UNIQUE (doc_id, chunk_seq)",
        "document_chunk_doc_id_fkey": (
            "FOREIGN KEY (doc_id) REFERENCES document(doc_id) ON DELETE CASCADE"
        ),
    },
}

#: DDL이 선언한 column **기본값**. `created_at timestamp DEFAULT now()` 하나뿐이지만,
#: 없으면 적재 시각이 조용히 NULL이 된다.
RAG_DEFAULT_CONTRACT: dict[str, dict[str, str]] = {
    "document": {"created_at": "now()"},
}

RAG_CONSTRAINT_SQL = """/* rag:constraints */
SELECT c.relname AS table_name, con.conname AS constraint_name,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
ORDER BY 1, 2
"""

RAG_DEFAULT_SQL = """/* rag:defaults */
SELECT c.relname AS table_name, a.attname AS column_name,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY 1, 2
"""


def _normalized(definitions: Mapping[str, str]) -> dict[str, str | None]:
    """정의를 비교 가능한 하나의 형태로. Common helper를 그대로 쓴다."""

    import apply_agent_runtime as agent_runtime

    return {
        name: agent_runtime.normalize_catalog_text(value)
        for name, value in definitions.items()
    }


def verify_rag_objects(connection: Any) -> None:
    """`RAG_SCHEMA_SQL`이 만든 **constraint와 default**를 exact 대조한다.

    `verify_rag_schema()`가 column 이름·순서를 보는 것과 짝이다. 둘을 합치지 않는
    이유는 호출자가 "이름은 맞는데 제약이 사라졌다"를 구분할 수 있어야 해서다.
    """

    tables = sorted(RAG_CONSTRAINT_CONTRACT)
    actual: dict[str, dict[str, str]] = {table: {} for table in tables}
    for row in _mapping_rows(connection.exec_driver_sql(RAG_CONSTRAINT_SQL, (tables,))):
        actual[str(row["table_name"])][str(row["constraint_name"])] = str(
            row["definition"]
        )
    for table, expected in RAG_CONSTRAINT_CONTRACT.items():
        # **양쪽을 정규화한다.**
        #
        # `pg_restore`는 같은 CHECK를 다르게 재출력한다 — cast를 배열 바깥에 두느냐
        # 각 원소에 두느냐가 갈린다. 원문 비교로는 **복원본이 이 계약을 통과하지
        # 못하고**, 그러면 backup이 복구 수단으로 성립하지 않는다
        # (`V5-CM-3.4` 구현리뷰 10차 필수 2에서 실측).
        if _normalized(actual.get(table, {})) != _normalized(expected):
            raise RagSchemaError(f"{table} constraint 계약이 다릅니다")

    defaults: dict[str, dict[str, str]] = {table: {} for table in tables}
    for row in _mapping_rows(connection.exec_driver_sql(RAG_DEFAULT_SQL, (tables,))):
        defaults[str(row["table_name"])][str(row["column_name"])] = str(
            row["column_default"]
        )
    for table in tables:
        if defaults.get(table, {}) != RAG_DEFAULT_CONTRACT.get(table, {}):
            raise RagSchemaError(f"{table} column default 계약이 다릅니다")


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
        count_row = (
            connection.exec_driver_sql(f'SELECT count(*) AS row_count FROM "{table}"')
            .mappings()
            .one()
        )
        counts[table] = int(count_row["row_count"])
    return counts


def apply_rag_schema(connection: Any) -> None:
    for statement in [
        part.strip() for part in RAG_SCHEMA_SQL.split(";") if part.strip()
    ]:
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
            # **자기 산출물을 전부 검증한다.**
            #
            # column 이름·순서만 보면 PK·FK·UNIQUE·CHECK·default가 빠진 채로 적용이
            # 성공한다. 두 verifier는 다른 질문에 답하므로 둘 다 부른다 — 예외는
            # `engine.begin()` 안에서 나므로 transaction이 rollback된다
            # (`V5-CM-3.4` 구현리뷰 6차 권장 2).
            verify_rag_schema(connection)
            verify_rag_objects(connection)
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
