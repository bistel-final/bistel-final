"""CM-4.7 E2E reset의 DB fingerprint와 crash-safe receipt 계약.

이 모듈은 DB를 변경하지 않는다. ``kosa_agent_e2e``의 preserved projection과
observer DB 두 곳의 full fingerprint가 **같은 typed canonicalization**을 쓰도록 한
owner에 모은다. DSN·credential·원문 row는 artifact에 기록하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import manifest_v3
import value_normalization

DATASET_EPOCH = "fdc_final_20260818"
TASK_ID = "V5-CM-4.7"
SNAPSHOT_VERSION = "e2e-reset-db-snapshot-v1"
RECEIPT_FORMAT_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "reports"


class EvidenceError(RuntimeError):
    """Fingerprint 또는 receipt가 증거로 사용할 수 없다."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def new_run_id() -> str:
    return uuid.uuid4().hex


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    return manifest_v3.canonical_payload_sha256(payload)


def _rows(result: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in result.mappings().all()]
    except AttributeError:
        return [dict(row) for row in result]


def _one(result: Any) -> dict[str, Any]:
    rows = _rows(result)
    if len(rows) != 1:
        raise EvidenceError("SNAPSHOT_QUERY_INVALID")
    return rows[0]


def _quote(identifier: str) -> str:
    """Catalog에서 읽은 PostgreSQL identifier를 다시 SQL에 넣는다."""

    return '"' + identifier.replace('"', '""') + '"'


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(child) for child in value]
    return str(value)


RELATIONS_SQL = """
SELECT c.relname AS relation_name,
       c.relkind::text AS relation_kind,
       pg_get_userbyid(c.relowner) AS owner,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
ORDER BY c.relname
"""

COLUMNS_SQL = """
SELECT c.relname AS relation_name,
       a.attname AS column_name,
       a.attnum AS ordinal_position,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       pg_get_expr(d.adbin, d.adrelid) AS column_default,
       col_description(c.oid, a.attnum) AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

CONSTRAINTS_SQL = """
SELECT c.relname AS relation_name,
       x.conname AS constraint_name,
       x.contype::text AS constraint_type,
       pg_get_constraintdef(x.oid, true) AS definition,
       x.convalidated AS validated
FROM pg_constraint x
JOIN pg_class c ON c.oid = x.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
ORDER BY c.relname, x.conname
"""

INDEXES_SQL = """
SELECT t.relname AS relation_name,
       i.relname AS index_name,
       x.indisvalid AS valid,
       x.indisready AS ready,
       pg_get_indexdef(i.oid) AS definition
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
ORDER BY t.relname, i.relname
"""

ACL_SQL = """
SELECT c.relname AS relation_name,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(a.grantee) END AS grantee,
       pg_get_userbyid(a.grantor) AS grantor,
       a.privilege_type::text AS privilege,
       a.is_grantable AS grantable
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL aclexplode(
  coalesce(c.relacl,
    acldefault(CASE WHEN c.relkind = 'S' THEN 'S'::"char"
                    ELSE 'r'::"char" END, c.relowner))
) a
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
ORDER BY c.relname, grantee, privilege, grantor
"""


def _assert_repeatable_read(connection: Any) -> None:
    isolation = str(
        _one(
            connection.exec_driver_sql(
                "SELECT current_setting('transaction_isolation') AS value"
            )
        )["value"]
    ).lower()
    if isolation not in {"repeatable read", "serializable"}:
        raise EvidenceError("SNAPSHOT_ISOLATION_INVALID")


def _typed_table_hash(
    connection: Any,
    table: str,
    columns: Sequence[Mapping[str, Any]],
) -> tuple[int, str]:
    names = [str(column["column_name"]) for column in columns]
    if not names:
        raise EvidenceError("SNAPSHOT_COLUMN_INVALID")
    logical_types: dict[str, str] = {}
    for column in columns:
        name = str(column["column_name"])
        try:
            logical_types[name] = value_normalization.logical_type(
                str(column["data_type"])
            )
        except value_normalization.ValueNormalizationError as exc:
            raise EvidenceError("SNAPSHOT_TYPE_UNSUPPORTED") from exc

    selected = ", ".join(_quote(name) for name in names)
    result = connection.exec_driver_sql(
        f"SELECT {selected} FROM public.{_quote(table)}"
    )
    normalized: list[dict[str, Any]] = []
    for raw in _rows(result):
        row: dict[str, Any] = {}
        for name in names:
            value = raw[name]
            logical = logical_types[name]
            if logical == "bytes":
                row[name] = None if value is None else bytes(value).hex()
            else:
                try:
                    row[name] = value_normalization.normalize_value(value, logical)
                except value_normalization.ValueNormalizationError as exc:
                    raise EvidenceError("SNAPSHOT_VALUE_INVALID") from exc
        normalized.append(row)
    return len(normalized), manifest_v3.hash_canonical_rows(normalized)


def _sequence_state(connection: Any, name: str) -> dict[str, Any]:
    row = _one(
        connection.exec_driver_sql(
            f"SELECT last_value, is_called FROM public.{_quote(name)}"
        )
    )
    return {
        "last_value": int(row["last_value"]),
        "is_called": row["is_called"] is True,
    }


def snapshot_database_fingerprint(
    connection: Any,
    *,
    mutable_tables: Sequence[str] = (),
    mutable_sequences: Sequence[str] = (),
) -> dict[str, Any]:
    """한 repeatable-read snapshot에서 public DB 전체를 canonical fingerprint한다.

    ``mutable_*``는 catalog에는 남기되 content/sequence state 비교에서만 제외한다.
    따라서 E2E reset 전후 schema·owner·ACL·constraint·index drift는 계속 잡힌다.
    observer DB에는 빈 tuple을 넘겨 모든 relation content와 sequence state를 본다.
    """

    _assert_repeatable_read(connection)
    database = str(
        _one(connection.exec_driver_sql("SELECT current_database() AS value"))["value"]
    )
    relation_rows = _rows(connection.exec_driver_sql(RELATIONS_SQL))
    column_rows = _rows(connection.exec_driver_sql(COLUMNS_SQL))
    constraints = _rows(connection.exec_driver_sql(CONSTRAINTS_SQL))
    indexes = _rows(connection.exec_driver_sql(INDEXES_SQL))
    acl = _rows(connection.exec_driver_sql(ACL_SQL))

    columns_by_relation: dict[str, list[dict[str, Any]]] = {}
    for row in column_rows:
        columns_by_relation.setdefault(str(row["relation_name"]), []).append(row)

    mutable_table_set = set(mutable_tables)
    content_relations = [
        str(row["relation_name"])
        for row in relation_rows
        if str(row["relation_kind"]) in {"r", "p", "v", "m"}
    ]
    table_content: dict[str, dict[str, Any]] = {}
    for relation in content_relations:
        if relation in mutable_table_set:
            continue
        count, digest = _typed_table_hash(
            connection, relation, columns_by_relation.get(relation, ())
        )
        table_content[relation] = {"row_count": count, "content_sha256": digest}

    mutable_sequence_set = set(mutable_sequences)
    sequence_state = {
        name: _sequence_state(connection, name)
        for name in sorted(
            str(row["relation_name"])
            for row in relation_rows
            if str(row["relation_kind"]) == "S"
            and str(row["relation_name"]) not in mutable_sequence_set
        )
    }
    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "database": database,
        "relations": [_json_safe(row) for row in relation_rows],
        "columns": [_json_safe(row) for row in column_rows],
        "constraints": [_json_safe(row) for row in constraints],
        "indexes": [_json_safe(row) for row in indexes],
        "acl": [_json_safe(row) for row in acl],
        "table_content": table_content,
        "sequence_state": sequence_state,
        "mutable_content_excluded": sorted(mutable_table_set),
        "mutable_sequences_excluded": sorted(mutable_sequence_set),
    }
    return {"payload": payload, "sha256": canonical_sha256(payload)}


def receipt_path(root: Path, run_id: str, stage: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id) or not re.fullmatch(r"[a-z_]+", stage):
        raise EvidenceError("RECEIPT_ID_INVALID")
    return root / f"e2e_reset.{run_id}.{stage}.json"


def _receipt_bytes(payload: Mapping[str, Any]) -> bytes:
    manifest_v3.scan_for_sensitive_values(payload)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _fsync_parent(path: Path) -> None:
    """POSIX에서는 directory entry까지, Windows에서는 가능한 범위까지 flush한다."""

    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_exclusive_receipt(path: Path, payload: Mapping[str, Any]) -> str:
    """pre receipt도 완성된 inode만 O_EXCL 의미로 공개한다."""

    return write_atomic_receipt(path, payload)


def write_atomic_receipt(path: Path, payload: Mapping[str, Any]) -> str:
    """완성된 temp를 exclusive hard-link해 기존 stage를 덮어쓰지 않는다."""

    if path.exists():
        raise EvidenceError("RECEIPT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _receipt_bytes(payload)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(raw)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            # 같은 directory의 완전히 fsync된 inode만 link한다. ``replace``와 달리
            # POSIX에서도 concurrent creator의 stage를 덮어쓸 수 없다.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise EvidenceError("RECEIPT_ALREADY_EXISTS") from exc
        _fsync_parent(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(raw).hexdigest()


def base_receipt(
    artifact_type: str,
    run_id: str,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise EvidenceError("RECEIPT_ID_INVALID")
    return {
        "artifact_type": artifact_type,
        "format_version": RECEIPT_FORMAT_VERSION,
        "task_id": TASK_ID,
        "dataset_epoch": DATASET_EPOCH,
        "run_id": run_id,
        "recorded_at": recorded_at or datetime.now(UTC).isoformat(),
    }


def load_receipt(
    path: Path,
    *,
    artifact_type: str | None = None,
    run_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError("RECEIPT_INVALID") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("RECEIPT_INVALID")
    if (
        payload.get("format_version") != RECEIPT_FORMAT_VERSION
        or payload.get("task_id") != TASK_ID
        or payload.get("dataset_epoch") != DATASET_EPOCH
        or not RUN_ID_RE.fullmatch(str(payload.get("run_id", "")))
        or (artifact_type is not None and payload.get("artifact_type") != artifact_type)
        or (run_id is not None and payload.get("run_id") != run_id)
    ):
        raise EvidenceError("RECEIPT_INVALID")
    manifest_v3.scan_for_sensitive_values(payload)
    return payload, hashlib.sha256(raw).hexdigest()


def unresolved_run_ids(root: Path) -> tuple[str, ...]:
    """새 baseline을 만들면 안 되는 미해결 destructive run을 찾는다."""

    unresolved: list[str] = []
    for path in sorted(root.glob("e2e_reset.*.pre.json")):
        match = re.fullmatch(r"e2e_reset\.([0-9a-f]{32})\.pre\.json", path.name)
        if match is None:
            continue
        run_id = match.group(1)
        final = receipt_path(root, run_id, "final")
        if not final.exists():
            unresolved.append(run_id)
            continue
        try:
            payload, _ = load_receipt(
                final, artifact_type="e2e_reset_final", run_id=run_id
            )
        except EvidenceError:
            unresolved.append(run_id)
            continue
        if payload.get("status") not in {"PASS", "NO_MUTATION_BLOCKED"}:
            unresolved.append(run_id)
    return tuple(unresolved)


def assert_sha256(value: Any, reason_code: str = "RECEIPT_INVALID") -> str:
    rendered = str(value)
    if not SHA256_RE.fullmatch(rendered):
        raise EvidenceError(reason_code)
    return rendered
