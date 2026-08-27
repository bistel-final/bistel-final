"""Load corrected RAG documents into ``document`` and ``document_chunk``.

This is a small adapter around the mentor-provided RAG loading contract.  It
keeps only the parts that belong to B Knowledge:

* corrected RAG source files are explicit input;
* only ``kosa_agent`` and ``kosa_agent_e2e`` can be targeted;
* canonical document IDs and deterministic chunk IDs are used;
* only the three canonical documents are replaced in one transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import manifest_v3
from dotenv import load_dotenv
from db_target import (
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
    set_and_validate_public_search_path,
    validate_connected_identity,
    validate_url_components,
)
from master_cypher import canonical_sha256
from sqlalchemy import create_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORRECTED_RAG_DIR = REPOSITORY_ROOT / "docs" / "knowledge" / "rag-corrected"
MARKER_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
SCHEMA_PATH = REPOSITORY_ROOT / "backend" / "migrations" / "001_reference_extensions.sql"

ALLOWED_RAG_DATABASES = frozenset({"kosa_agent", "kosa_agent_e2e"})
CANONICAL_DOCUMENT_IDS = (
    "DOC-SPEC-ET7500",
    "DOC-SPEC-PH9000",
    "DOC-TROUBLE-FDC",
)
REQUIRED_SOURCE_FILES = (
    "SPEC_ET-7500_DryEtcher.md",
    "SPEC_PH-9000_PhotoScanner.md",
    "TROUBLE_FDC_FaultGuide.md",
)
CHUNK_SCHEMA_VERSION = "cs2"
CHUNK_ID_FORMAT = "<document_id>:cs2:<seq:04d>"
CHUNK_CONTRACT_SHA256 = (
    "1498123916cb73f5c9df8906f1ba6a9c2ed0736db9b5d3de7eb583653bb1e61e"
)
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_DIMENSION = 1024
SEARCH_SMOKE_CASES = (
    ("PH-9000 Photo Scanner 적용 범위", "PH-9000", "DOC-SPEC-PH9000"),
    ("ET-7500 Dry Etcher 적용 범위", "ET-7500", "DOC-SPEC-ET7500"),
    ("연속 3 WAFER OOS 승인 조치", "PH-9000", "DOC-TROUBLE-FDC"),
)
H2_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
H3_HEADING_PATTERN = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
MAX_CHARS = 1000


class RagLoadError(RuntimeError):
    """RAG source, target, or load contract failed."""


@dataclass(frozen=True)
class RagDocument:
    document_id: str
    title: str
    doc_type: str
    model_code: str | None
    source_path: str
    version: str | None
    content: str
    corrected_sha256: str


@dataclass(frozen=True)
class RagChunk:
    chunk_id: str
    doc_id: str
    chunk_seq: int
    section_title: str
    content: str
    token_cnt: int
    embedding_input: str
    metadata_json: dict[str, Any]


@dataclass(frozen=True)
class PreparedRagCorpus:
    documents: tuple[RagDocument, ...]
    chunks: tuple[RagChunk, ...]


@dataclass(frozen=True)
class PostLoadVerification:
    document_count: int
    chunk_count: int
    null_embedding_count: int
    live_db_fingerprint_sha256: str
    search_smoke: tuple[dict[str, Any], ...]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_markdown(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


def _parse_front_matter(path: Path) -> tuple[dict[str, str], str, str]:
    raw = path.read_text(encoding="utf-8")
    text = _normalize_markdown(raw)
    corrected_sha256 = _sha256_text(text)
    if not text.startswith("---\n"):
        raise RagLoadError(f"{path.name}: YAML front matter가 없습니다")
    try:
        _, front_matter, body = text.split("---\n", 2)
    except ValueError as exc:
        raise RagLoadError(f"{path.name}: YAML front matter가 닫히지 않았습니다") from exc

    metadata: dict[str, str] = {}
    for line in front_matter.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise RagLoadError(f"{path.name}: front matter 형식이 잘못됐습니다")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, body.strip(), corrected_sha256


def _drop_leading_h1(content: str) -> str:
    lines = [
        line
        for line in content.splitlines()
        if not (line.startswith("# ") and not line.startswith("## "))
    ]
    return "\n".join(lines).strip()


def _require_metadata(metadata: Mapping[str, str], key: str, *, filename: str) -> str:
    value = metadata.get(key, "").strip()
    if not value:
        raise RagLoadError(f"{filename}: 필수 front matter가 없습니다: {key}")
    return value


def load_corrected_documents(source_dir: Path) -> tuple[RagDocument, ...]:
    resolved = source_dir.resolve()
    if not resolved.is_dir():
        raise RagLoadError(f"corrected RAG 경로가 없습니다: {source_dir}")

    documents: list[RagDocument] = []
    for filename in REQUIRED_SOURCE_FILES:
        path = resolved / filename
        if not path.is_file():
            raise RagLoadError(f"corrected RAG 파일이 없습니다: {filename}")
        metadata, body, corrected_sha256 = _parse_front_matter(path)
        doc_id = _require_metadata(metadata, "doc_id", filename=filename)
        if doc_id not in CANONICAL_DOCUMENT_IDS:
            raise RagLoadError(f"{filename}: canonical doc_id가 아닙니다: {doc_id}")
        doc_type = _require_metadata(metadata, "doc_type", filename=filename)
        if doc_type not in {"SPEC", "MANUAL", "TROUBLESHOOT"}:
            raise RagLoadError(f"{filename}: doc_type이 DB CHECK와 다릅니다: {doc_type}")
        documents.append(
            RagDocument(
                document_id=doc_id,
                title=_require_metadata(metadata, "title", filename=filename),
                doc_type=doc_type,
                model_code=metadata.get("model_code") or None,
                source_path=path.relative_to(REPOSITORY_ROOT).as_posix(),
                version=metadata.get("version") or None,
                content=_drop_leading_h1(body),
                corrected_sha256=corrected_sha256,
            )
        )

    ordered_ids = tuple(document.document_id for document in documents)
    if ordered_ids != CANONICAL_DOCUMENT_IDS:
        raise RagLoadError(f"canonical document 순서가 다릅니다: {ordered_ids}")
    return tuple(documents)


def _iter_sections(content: str) -> Iterable[tuple[str, str]]:
    h2_matches = list(H2_HEADING_PATTERN.finditer(content))
    if not h2_matches:
        if content.strip():
            yield "문서 안내", content.strip()
        return

    preamble = content[: h2_matches[0].start()].strip()
    for h2_index, h2_match in enumerate(h2_matches):
        h2_title = h2_match.group(1).strip()
        h2_start = h2_match.end()
        h2_end = (
            h2_matches[h2_index + 1].start()
            if h2_index + 1 < len(h2_matches)
            else len(content)
        )
        h3_matches = list(H3_HEADING_PATTERN.finditer(content, h2_start, h2_end))

        h2_body_end = h3_matches[0].start() if h3_matches else h2_end
        h2_body = content[h2_start:h2_body_end].strip()
        if h2_index == 0 and preamble:
            h2_body = f"{preamble}\n\n{h2_body}".strip()
        if h2_body:
            yield h2_title, h2_body

        for h3_index, h3_match in enumerate(h3_matches):
            h3_start = h3_match.end()
            h3_end = (
                h3_matches[h3_index + 1].start()
                if h3_index + 1 < len(h3_matches)
                else h2_end
            )
            h3_body = content[h3_start:h3_end].strip()
            if h3_body:
                yield f"{h2_title} > {h3_match.group(1).strip()}", h3_body


def _split_long_section(section_title: str, content: str) -> list[tuple[str, str]]:
    if len(content) <= MAX_CHARS:
        return [(section_title, content)]

    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n{2,}", content):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= MAX_CHARS or not current:
            current = candidate
            continue
        pieces.append(current)
        current = paragraph
    if current:
        pieces.append(current)

    return [
        (section_title if index == 1 else f"{section_title} ({index})", piece)
        for index, piece in enumerate(pieces, start=1)
    ]


def chunk_document(document: RagDocument) -> tuple[RagChunk, ...]:
    pieces: list[tuple[str, str]] = []
    for section_title, section_content in _iter_sections(document.content):
        pieces.extend(_split_long_section(section_title, section_content))

    chunks: list[RagChunk] = []
    for sequence, (section_title, content) in enumerate(pieces, start=1):
        chunk_id = f"{document.document_id}:{CHUNK_SCHEMA_VERSION}:{sequence:04d}"
        chunks.append(
            RagChunk(
                chunk_id=chunk_id,
                doc_id=document.document_id,
                chunk_seq=sequence,
                section_title=section_title,
                content=content,
                token_cnt=len(content.split()),
                embedding_input=f"{document.title} / {section_title}\n\n{content}",
                metadata_json={
                    "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                    "chunk_contract_sha256": CHUNK_CONTRACT_SHA256,
                    "corrected_sha256": document.corrected_sha256,
                    "embedding_model": EMBEDDING_MODEL,
                    "embedding_model_revision": EMBEDDING_MODEL_REVISION,
                    "embedding_dimension": EMBEDDING_DIMENSION,
                },
            )
        )
    if not chunks:
        raise RagLoadError(f"{document.document_id}: chunk가 생성되지 않았습니다")
    return tuple(chunks)


def prepare_corpus(source_dir: Path) -> PreparedRagCorpus:
    documents = load_corrected_documents(source_dir)
    chunks = tuple(chunk for document in documents for chunk in chunk_document(document))
    return PreparedRagCorpus(documents=documents, chunks=chunks)


def validate_rag_target(database: str) -> BootstrapTarget:
    if database not in ALLOWED_RAG_DATABASES:
        raise TargetValidationError("RAG 적재는 kosa_agent, kosa_agent_e2e만 허용합니다")
    return load_bootstrap_target(database)


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in ALLOWED_RAG_DATABASES:
        raise RagLoadError("허용되지 않은 RAG marker database입니다")
    return root / f"rag_load.{database}.json"


def _format_vector(values: Sequence[float]) -> str:
    if len(values) != EMBEDDING_DIMENSION:
        raise RagLoadError("embedding dimension이 1024가 아닙니다")
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def preflight_schema(connection: Any) -> None:
    result = connection.exec_driver_sql(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name IN ('document', 'document_chunk')
         ORDER BY table_name, ordinal_position
        """
    )
    rows = result.mappings().all()
    columns_by_table: dict[str, list[str]] = {"document": [], "document_chunk": []}
    for row in rows:
        columns_by_table[str(row["table_name"])].append(str(row["column_name"]))

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
    for table, expected_columns in expected.items():
        if columns_by_table[table] != expected_columns:
            raise RagLoadError(f"{table} schema가 RAG 적재 계약과 다릅니다")


def load_corpus(
    connection: Any,
    corpus: PreparedRagCorpus,
    *,
    encode: Callable[[Sequence[str]], Sequence[Sequence[float]]],
) -> int:
    document_ids = tuple(document.document_id for document in corpus.documents)
    embeddings = tuple(encode([chunk.embedding_input for chunk in corpus.chunks]))
    if len(embeddings) != len(corpus.chunks):
        raise RagLoadError("embedding 개수가 chunk 개수와 다릅니다")

    connection.exec_driver_sql(
        "DELETE FROM document WHERE doc_id = ANY (%(document_ids)s)",
        {"document_ids": list(document_ids)},
    )
    connection.exec_driver_sql(
        """
        INSERT INTO document
            (doc_id, title, doc_type, model_code, source_path, version)
        VALUES
            (%(doc_id)s, %(title)s, %(doc_type)s, %(model_code)s, %(source_path)s, %(version)s)
        """,
        [
            {
                "doc_id": document.document_id,
                "title": document.title,
                "doc_type": document.doc_type,
                "model_code": document.model_code,
                "source_path": document.source_path,
                "version": document.version,
            }
            for document in corpus.documents
        ],
    )
    connection.exec_driver_sql(
        """
        INSERT INTO document_chunk
            (chunk_id, doc_id, chunk_seq, section_title, content, token_cnt, embedding, metadata_json)
        VALUES
            (%(chunk_id)s, %(doc_id)s, %(chunk_seq)s, %(section_title)s, %(content)s,
             %(token_cnt)s, %(embedding)s::vector, %(metadata_json)s::jsonb)
        """,
        [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "chunk_seq": chunk.chunk_seq,
                "section_title": chunk.section_title,
                "content": chunk.content,
                "token_cnt": chunk.token_cnt,
                "embedding": _format_vector(embedding),
                "metadata_json": json.dumps(
                    chunk.metadata_json,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
            for chunk, embedding in zip(corpus.chunks, embeddings, strict=True)
        ],
    )
    return len(corpus.documents) + len(corpus.chunks)


def _embedding_cache_dir() -> Path:
    raw_path = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
    if not raw_path:
        raise RagLoadError("EMBEDDING_MODEL_PATH가 필요합니다")
    path = Path(raw_path)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    if not path.exists():
        raise RagLoadError(f"임베딩 모델 캐시 경로가 없습니다: {path}")
    return path


def _validate_embedding_environment() -> None:
    if os.getenv("EMBEDDING_MODEL", "").strip() != EMBEDDING_MODEL:
        raise RagLoadError(f"EMBEDDING_MODEL은 {EMBEDDING_MODEL}이어야 합니다")
    if os.getenv("EMBEDDING_MODEL_REVISION", "").strip() != EMBEDDING_MODEL_REVISION:
        raise RagLoadError("EMBEDDING_MODEL_REVISION이 공식 revision과 다릅니다")
    try:
        dimension = int(os.getenv("EMBEDDING_DIM", ""))
    except ValueError as exc:
        raise RagLoadError("EMBEDDING_DIM은 정수여야 합니다") from exc
    if dimension != EMBEDDING_DIMENSION:
        raise RagLoadError(f"EMBEDDING_DIM은 {EMBEDDING_DIMENSION}이어야 합니다")


def _load_sentence_transformer_encoder() -> Callable[[Sequence[str]], Sequence[Sequence[float]]]:
    _validate_embedding_environment()
    cache_dir = _embedding_cache_dir()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RagLoadError("sentence-transformers 의존성이 필요합니다") from exc

    model = SentenceTransformer(str(cache_dir), local_files_only=True)

    def encode(texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=True,
        )

    return encode


def _mapping_rows(result: Any) -> list[Mapping[str, Any]]:
    return list(result.mappings().all())


def _canonical_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return unicodedata.normalize("NFC", value)
        return _canonical_json(parsed)
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_canonical_json(item) for item in value]
    return value


def _live_fingerprint(connection: Any, document_ids: Sequence[str]) -> str:
    documents = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT doc_id, title, doc_type, model_code, source_path, version
              FROM document
             WHERE doc_id = ANY (%(document_ids)s)
             ORDER BY doc_id
            """,
            {"document_ids": list(document_ids)},
        )
    )
    chunks = _mapping_rows(
        connection.exec_driver_sql(
            """
            SELECT chunk_id, doc_id, chunk_seq, section_title, content, token_cnt, metadata_json
              FROM document_chunk
             WHERE doc_id = ANY (%(document_ids)s)
             ORDER BY doc_id, chunk_seq, chunk_id
            """,
            {"document_ids": list(document_ids)},
        )
    )
    return canonical_sha256(
        {
            "documents": [_canonical_json(dict(row)) for row in documents],
            "chunks": [_canonical_json(dict(row)) for row in chunks],
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "chunk_contract_sha256": CHUNK_CONTRACT_SHA256,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_model_revision": EMBEDDING_MODEL_REVISION,
            "embedding_dimension": EMBEDDING_DIMENSION,
        }
    )


#: `V5-CM-2.6`이 전환 전후 RAG 보존을 확인할 때 **이 산식을 그대로** 쓴다. 2.6이 별도
#: 산식을 만들면 B-1.3이 남긴 marker와 대조할 수 없다. 이름만 공개하고 본문은 B 소유다.
live_fingerprint = _live_fingerprint


def _scalar_count(connection: Any, sql: str, parameters: Mapping[str, Any]) -> int:
    row = connection.exec_driver_sql(sql, parameters).mappings().one()
    return int(next(iter(row.values())))


def _search_smoke(
    connection: Any,
    *,
    encode: Callable[[Sequence[str]], Sequence[Sequence[float]]],
) -> tuple[dict[str, Any], ...]:
    query_embeddings = tuple(encode([case[0] for case in SEARCH_SMOKE_CASES]))
    if len(query_embeddings) != len(SEARCH_SMOKE_CASES):
        raise RagLoadError("검색 smoke embedding 개수가 다릅니다")

    results: list[dict[str, Any]] = []
    for (query, model_code, expected_doc_id), embedding in zip(
        SEARCH_SMOKE_CASES, query_embeddings, strict=True
    ):
        rows = _mapping_rows(
            connection.exec_driver_sql(
                """
                SELECT d.doc_id, c.chunk_id, 1 - (c.embedding <=> %(embedding)s::vector) AS score
                  FROM document_chunk c
                  JOIN document d ON d.doc_id = c.doc_id
                 WHERE (%(model_code)s::varchar IS NULL
                        OR d.model_code = %(model_code)s::varchar
                        OR d.model_code = 'COMMON')
                 ORDER BY c.embedding <=> %(embedding)s::vector, c.chunk_seq
                 LIMIT 4
                """,
                {
                    "embedding": _format_vector(embedding),
                    "model_code": model_code,
                },
            )
        )
        top_document_ids = [str(row["doc_id"]) for row in rows]
        passed = expected_doc_id in top_document_ids
        results.append(
            {
                "query": query,
                "model_code": model_code,
                "expected_document_id": expected_doc_id,
                "top_document_ids": top_document_ids,
                "passed": passed,
            }
        )
        if not passed:
            raise RagLoadError(f"검색 smoke 실패: {query} -> {expected_doc_id}")
    return tuple(results)


def verify_live_load(
    connection: Any,
    corpus: PreparedRagCorpus,
    *,
    encode: Callable[[Sequence[str]], Sequence[Sequence[float]]],
) -> PostLoadVerification:
    document_ids = tuple(document.document_id for document in corpus.documents)
    parameters = {"document_ids": list(document_ids)}
    document_count = _scalar_count(
        connection,
        "SELECT count(*) AS value FROM document WHERE doc_id = ANY (%(document_ids)s)",
        parameters,
    )
    chunk_count = _scalar_count(
        connection,
        "SELECT count(*) AS value FROM document_chunk WHERE doc_id = ANY (%(document_ids)s)",
        parameters,
    )
    null_embedding_count = _scalar_count(
        connection,
        """
        SELECT count(*) AS value
          FROM document_chunk
         WHERE doc_id = ANY (%(document_ids)s)
           AND (embedding IS NULL OR vector_dims(embedding) <> 1024)
        """,
        parameters,
    )
    if document_count != len(corpus.documents):
        raise RagLoadError("live document count가 canonical 3건과 다릅니다")
    if chunk_count != len(corpus.chunks):
        raise RagLoadError("live chunk count가 적재 chunk 수와 다릅니다")
    if null_embedding_count != 0:
        raise RagLoadError("embedding NULL 또는 dimension mismatch가 있습니다")

    return PostLoadVerification(
        document_count=document_count,
        chunk_count=chunk_count,
        null_embedding_count=null_embedding_count,
        live_db_fingerprint_sha256=_live_fingerprint(connection, document_ids),
        search_smoke=_search_smoke(connection, encode=encode),
    )


def build_marker(
    target: BootstrapTarget,
    corpus: PreparedRagCorpus,
    verification: PostLoadVerification,
) -> dict[str, Any]:
    source_sha256_by_document = {
        document.document_id: document.corrected_sha256 for document in corpus.documents
    }
    marker = {
        "format_version": 1,
        "artifact_type": "rag_load_marker",
        "status": "COMMITTED",
        "database": target.database,
        "profile": target.profile,
        "schema_sha256": _sha256_file(SCHEMA_PATH),
        "document_ids": list(CANONICAL_DOCUMENT_IDS),
        "source_sha256_by_document": source_sha256_by_document,
        "corrected_sha256_by_document": source_sha256_by_document,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_contract_sha256": CHUNK_CONTRACT_SHA256,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_revision": EMBEDDING_MODEL_REVISION,
        "dimension": EMBEDDING_DIMENSION,
        "document_count": verification.document_count,
        "chunk_count": verification.chunk_count,
        "null_embedding_count": verification.null_embedding_count,
        "live_db_fingerprint_sha256": verification.live_db_fingerprint_sha256,
        "search_smoke": list(verification.search_smoke),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    manifest_v3.scan_for_sensitive_values(marker)
    return marker


def save_marker(marker: Mapping[str, Any], *, database: str, root: Path = MARKER_ROOT) -> None:
    path = marker_path(database, root=root)
    manifest_v3.atomic_save_json(path, marker)


def run_load(*, database: str, source_dir: Path) -> int:
    load_dotenv(REPOSITORY_ROOT / ".env")
    target = validate_rag_target(database)
    corpus = prepare_corpus(source_dir)
    encode = _load_sentence_transformer_encoder()
    url = target.create_url()
    validate_url_components(url, target)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                validate_connected_identity(connection, target)
                set_and_validate_public_search_path(connection)
                preflight_schema(connection)
                inserted = load_corpus(connection, corpus, encode=encode)
        with engine.connect() as connection:
            validate_connected_identity(connection, target)
            verification = verify_live_load(connection, corpus, encode=encode)
        save_marker(build_marker(target, corpus, verification), database=database)
        return inserted
    finally:
        engine.dispose()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        choices=sorted(ALLOWED_RAG_DATABASES),
        help="RAG 적재 대상 DB. 기본값과 평가 DB는 허용하지 않는다.",
    )
    parser.add_argument(
        "--confirm-target",
        required=True,
        choices=sorted(ALLOWED_RAG_DATABASES),
        help="오조작 방지를 위해 --database와 같은 값을 넣는다.",
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="검증된 corrected RAG 3종이 들어 있는 명시 경로.",
    )
    args = parser.parse_args(argv)
    if args.database != args.confirm_target:
        raise RagLoadError("--confirm-target과 --database가 다릅니다")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    inserted = run_load(database=args.database, source_dir=args.source_dir)
    print(f"loaded_rag_rows={inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
