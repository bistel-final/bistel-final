"""PostgreSQL 최소권한 role matrix 적용 runner (`V5-CM-3.5`).

한 번에 target 하나와 stage 하나만 다룬다. PostgreSQL role은 cluster-global이고 object
ACL은 database-local이므로 3 DB 성공을 하나의 원자 transaction처럼 보고하지 않는다.

지원 mode:

* ``--preflight``: read-only inventory·role·ACL·credential 점검
* ``--preview``: read-only delta 집계(원문 SQL·secret 없음)
* ``--apply``: 승인·snapshot·target confirm 뒤 한 DB transaction 적용
* ``--verify``: live effective privilege와 marker exact 대조
* ``--recover-marker``: live post-state가 exact이고 원 approval·snapshot이 있을 때
  marker만 복구

``PUBLIC`` 회수 범위는 database/schema/relation/sequence data access surface다. pgvector
extension function/type/operator ACL은 수정하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NoReturn

from dotenv import load_dotenv
from psycopg import Error as PsycopgError
from psycopg import sql
from sqlalchemy.engine import URL, Connection
from sqlalchemy.exc import SQLAlchemyError

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import bootstrap_common  # noqa: E402
import db_target  # noqa: E402
import manifest_v3  # noqa: E402
import postgres_role_matrix as matrix  # noqa: E402
from mutation_runtime import resolve_exclusive_mode  # noqa: E402

REPOSITORY_ROOT = SCRIPTS_ROOT.parents[1]
MARKER_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "markers"
APPROVAL_ARTIFACT_TYPE = "postgres_role_matrix_change_approval"
MARKER_FORMAT_VERSION = 1
APPROVAL_FORMAT_VERSION = 1
CHANGE_REFERENCE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
SNAPSHOT_SAFE_KEYS: Final = (
    "database",
    "tables",
    "views",
    "sequences",
    "relation_columns",
    "roles",
    "memberships",
    "role_settings",
    "managed_owners",
    "unmanageable_owners",
    "relation_catalog",
    "view_definitions",
    "indexes",
    "constraints",
    "public_acl",
    "default_acl",
    "database_privileges",
    "schema_privileges",
    "relation_privileges",
    "column_privileges",
    "sequence_privileges",
    "row_counts",
    "content_hashes",
)

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3

ROLE_PASSWORD_ENV: Mapping[matrix.ManagedRole, str] = {
    matrix.ManagedRole.APP: "APP_DB_PASSWORD",
    matrix.ManagedRole.READONLY: "READONLY_PASSWORD",
    matrix.ManagedRole.EVALUATION: "EVALUATION_DB_PASSWORD",
    matrix.ManagedRole.LOGGER: "QUERY_LOGGER_PASSWORD",
}


class RoleMatrixError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "ROLE_MATRIX_INVALID",
        exit_code: int = EXIT_USAGE,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class Inspection:
    state: str
    unsafe: tuple[str, ...]
    delta: tuple[str, ...]
    actual_privilege_count: int
    expected_privilege_count: int

    @property
    def exact(self) -> bool:
        return not self.unsafe and not self.delta


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5-CM-3.5 PostgreSQL role matrix")
    parser.add_argument("--database")
    parser.add_argument("--profile", choices=[item.value for item in matrix.Profile])
    parser.add_argument(
        "--schema-stage", choices=[item.value for item in matrix.SchemaStage]
    )
    parser.add_argument(
        "--matrix-stage", choices=[item.value for item in matrix.MatrixStage]
    )
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--snapshot-out", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--recover-marker", action="store_true")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    try:
        mode = resolve_exclusive_mode(
            {
                "preflight": args.preflight,
                "preview": args.preview,
                "apply": args.apply,
                "verify": args.verify,
                "recover": args.recover_marker,
            },
            default_mode="",
            mutually_exclusive_message="role matrix mode는 하나만 선택해야 합니다",
        )
    except Exception as exc:
        raise RoleMatrixError(str(exc)) from exc
    if not mode:
        raise RoleMatrixError("role matrix mode를 하나 명시해야 합니다")
    return mode


def resolve_contract(args: argparse.Namespace) -> matrix.RoleMatrixContract:
    if (
        not args.database
        or not args.profile
        or not args.schema_stage
        or not args.matrix_stage
    ):
        raise RoleMatrixError(
            "--database/--profile/--schema-stage/--matrix-stage가 모두 필요합니다"
        )
    expected_profile = matrix.DATABASE_PROFILES.get(args.database)
    if expected_profile is None or expected_profile.value != args.profile:
        raise RoleMatrixError(
            "database와 profile이 allowlist 계약과 다릅니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )
    try:
        return matrix.build_contract(
            args.database, args.matrix_stage, args.schema_stage
        )
    except matrix.ContractError as exc:
        raise RoleMatrixError(str(exc), reason_code=exc.reason_code) from exc


def _rows(result: Any) -> list[Mapping[str, Any]]:
    try:
        return list(result.mappings().all())
    except (AttributeError, TypeError) as exc:
        raise RoleMatrixError(
            "PostgreSQL catalog 응답 형식이 잘못됐습니다",
            reason_code="CATALOG_INVALID",
            exit_code=EXIT_MISMATCH,
        ) from exc


def _one(result: Any) -> Mapping[str, Any]:
    rows = _rows(result)
    if len(rows) != 1:
        raise RoleMatrixError(
            "PostgreSQL catalog 단일 응답 행 수가 잘못됐습니다",
            reason_code="CATALOG_INVALID",
            exit_code=EXIT_MISMATCH,
        )
    return rows[0]


def _grid(
    connection: Connection,
    function: str,
    roles: Sequence[str],
    objects: Sequence[str],
    privileges: Sequence[str],
) -> list[dict[str, Any]]:
    if not objects:
        return []
    if function not in {
        "has_database_privilege",
        "has_schema_privilege",
        "has_table_privilege",
        "has_sequence_privilege",
    }:
        raise RoleMatrixError("허용되지 않은 privilege 검사 함수입니다")
    object_expr = "object_name"
    if function in {"has_table_privilege", "has_sequence_privilege"}:
        object_expr = "format('public.%%I', object_name)"
    query = f"""
        SELECT role_name, object_name, privilege_name,
               {function}(role_name, {object_expr}, privilege_name) AS allowed
        FROM unnest(%s::text[]) AS roles(role_name)
        CROSS JOIN unnest(%s::text[]) AS objects(object_name)
        CROSS JOIN unnest(%s::text[]) AS privileges(privilege_name)
        ORDER BY role_name, object_name, privilege_name
    """
    return [
        dict(row)
        for row in _rows(
            connection.exec_driver_sql(
                query, (list(roles), list(objects), list(privileges))
            )
        )
    ]


def _column_grid(
    connection: Connection,
    roles: Sequence[str],
    relation_columns: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    pairs = [
        {"relation": relation, "column": column}
        for relation, columns in sorted(relation_columns.items())
        for column in columns
    ]
    if not pairs:
        return []
    query = """
        SELECT role_name, pair->>'relation' AS object_name,
               pair->>'column' AS column_name, privilege_name,
               has_column_privilege(
                   role_name,
                   format('public.%%I', pair->>'relation'),
                   pair->>'column',
                   privilege_name
               ) AS allowed
        FROM unnest(%s::text[]) AS roles(role_name)
        CROSS JOIN jsonb_array_elements(%s::jsonb) AS pair
        CROSS JOIN unnest(%s::text[]) AS privileges(privilege_name)
        ORDER BY role_name, object_name, column_name, privilege_name
    """
    return [
        dict(row)
        for row in _rows(
            connection.exec_driver_sql(
                query,
                (
                    list(roles),
                    json.dumps(pairs, separators=(",", ":")),
                    list(matrix.COLUMN_PRIVILEGES),
                ),
            )
        )
    ]


def _denied_grid(
    roles: Sequence[str],
    objects: Sequence[str],
    privileges: Sequence[str],
) -> list[dict[str, Any]]:
    """아직 생성되지 않은 role의 effective privilege 0 셀을 보충한다."""

    return [
        {
            "role_name": role,
            "object_name": object_name,
            "privilege_name": privilege,
            "allowed": False,
        }
        for role in roles
        for object_name in objects
        for privilege in privileges
    ]


def _denied_column_grid(
    roles: Sequence[str], relation_columns: Mapping[str, Sequence[str]]
) -> list[dict[str, Any]]:
    return [
        {
            "role_name": role,
            "object_name": relation,
            "column_name": column,
            "privilege_name": privilege,
            "allowed": False,
        }
        for role in roles
        for relation, columns in sorted(relation_columns.items())
        for column in columns
        for privilege in matrix.COLUMN_PRIVILEGES
    ]


def read_snapshot(connection: Connection) -> dict[str, Any]:
    """한 read-only transaction의 catalog·effective privilege snapshot."""

    role_names = [role.value for role in matrix.MANAGED_ROLES]
    identity = _one(
        connection.exec_driver_sql(
            "SELECT current_database() AS database, current_user AS current_user, "
            "(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS superuser"
        )
    )
    relation_rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT c.relname AS name, c.relkind AS kind,
                   pg_get_userbyid(c.relowner) AS owner,
                   (current_user = pg_get_userbyid(c.relowner)
                    OR (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
                    OR pg_has_role(current_user, pg_get_userbyid(c.relowner), 'MEMBER'))
                       AS manageable
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
            ORDER BY c.relname
            """
        )
    )
    tables = sorted(
        str(row["name"]) for row in relation_rows if row["kind"] in {"r", "p"}
    )
    views = sorted(
        str(row["name"]) for row in relation_rows if row["kind"] in {"v", "m"}
    )
    sequences = sorted(str(row["name"]) for row in relation_rows if row["kind"] == "S")
    columns_rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema='public'
            ORDER BY table_name, ordinal_position
            """
        )
    )
    relation_columns: dict[str, list[str]] = {}
    for row in columns_rows:
        relation_columns.setdefault(str(row["table_name"]), []).append(
            str(row["column_name"])
        )
    view_definitions = [
        dict(row)
        for row in _rows(
            connection.exec_driver_sql(
                """
                SELECT c.relname AS view_name,
                       pg_get_viewdef(c.oid, true) AS definition
                FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('v','m')
                ORDER BY view_name
                """
            )
        )
    ]
    indexes = [
        dict(row)
        for row in _rows(
            connection.exec_driver_sql(
                """
                SELECT table_name, index_name, definition
                FROM (
                    SELECT tablename AS table_name, indexname AS index_name,
                           indexdef AS definition
                    FROM pg_indexes
                    WHERE schemaname='public'
                ) value
                ORDER BY table_name, index_name
                """
            )
        )
    ]
    constraints = [
        dict(row)
        for row in _rows(
            connection.exec_driver_sql(
                """
                SELECT c.relname AS table_name, x.conname AS constraint_name,
                       x.contype::text AS constraint_type,
                       pg_get_constraintdef(x.oid, true) AS definition
                FROM pg_constraint x
                JOIN pg_class c ON c.oid=x.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public'
                ORDER BY table_name, constraint_name
                """
            )
        )
    ]

    role_rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT rolname AS role_name, rolcanlogin AS login,
                   rolsuper AS superuser, rolcreatedb AS createdb,
                   rolcreaterole AS createrole, rolreplication AS replication,
                   rolbypassrls AS bypassrls
            FROM pg_roles
            WHERE rolname = ANY(%s::text[])
            ORDER BY rolname
            """,
            (role_names,),
        )
    )
    existing_role_names = sorted(str(row["role_name"]) for row in role_rows)
    missing_role_names = sorted(set(role_names) - set(existing_role_names))
    memberships = _rows(
        connection.exec_driver_sql(
            """
            SELECT member.rolname AS member, granted.rolname AS granted_role
            FROM pg_auth_members m
            JOIN pg_roles member ON member.oid=m.member
            JOIN pg_roles granted ON granted.oid=m.roleid
            WHERE member.rolname = ANY(%s::text[])
               OR granted.rolname = ANY(%s::text[])
            ORDER BY member, granted_role
            """,
            (role_names, role_names),
        )
    )
    role_settings = _rows(
        connection.exec_driver_sql(
            """
            SELECT r.rolname AS role_name, setting
            FROM pg_db_role_setting s
            JOIN pg_roles r ON r.oid=s.setrole
            CROSS JOIN LATERAL unnest(s.setconfig) AS setting
            WHERE s.setdatabase=(
                SELECT oid FROM pg_database WHERE datname=current_database()
            )
              AND r.rolname=ANY(%s::text[])
            ORDER BY role_name, setting
            """,
            (role_names,),
        )
    )
    managed_owners = [
        {"object": str(row["name"]), "owner": str(row["owner"])}
        for row in relation_rows
        if row["owner"] in role_names
    ]
    unmanageable_owners = sorted(
        {str(row["owner"]) for row in relation_rows if row["manageable"] is not True}
    )

    public_acl = _rows(
        connection.exec_driver_sql(
            """
            SELECT object_type, object_name, privilege_type
            FROM (
              SELECT 'database'::text AS object_type, d.datname::text AS object_name,
                     x.privilege_type::text
              FROM pg_database d
              CROSS JOIN LATERAL aclexplode(
                  coalesce(d.datacl, acldefault('d', d.datdba))
              ) x
              WHERE d.datname=current_database() AND x.grantee=0
              UNION ALL
              SELECT 'schema', n.nspname, x.privilege_type::text
              FROM pg_namespace n
              CROSS JOIN LATERAL aclexplode(
                  coalesce(n.nspacl, acldefault('n', n.nspowner))
              ) x
              WHERE n.nspname='public' AND x.grantee=0
              UNION ALL
              SELECT CASE WHEN c.relkind='S' THEN 'sequence' ELSE 'relation' END,
                     c.relname, x.privilege_type::text
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
              CROSS JOIN LATERAL aclexplode(
                  coalesce(
                    c.relacl,
                    acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char"
                                    ELSE 'r'::"char" END, c.relowner)
                  )
              ) x
              WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
                AND x.grantee=0
            ) acl
            ORDER BY object_type, object_name, privilege_type
            """
        )
    )
    default_acl = _rows(
        connection.exec_driver_sql(
            """
            SELECT pg_get_userbyid(d.defaclrole) AS owner,
                   CASE WHEN d.defaclnamespace=0 THEN 'GLOBAL'
                        ELSE 'public' END AS schema_scope,
                   d.defaclobjtype AS object_type,
                   CASE WHEN x.grantee=0 THEN 'PUBLIC'
                        ELSE pg_get_userbyid(x.grantee) END AS grantee,
                   x.privilege_type::text AS privilege_type
            FROM pg_default_acl d
            CROSS JOIN LATERAL aclexplode(d.defaclacl) x
            WHERE d.defaclnamespace IN (
                0, (SELECT oid FROM pg_namespace WHERE nspname='public')
            )
              AND (x.grantee=0 OR pg_get_userbyid(x.grantee)=ANY(%s::text[]))
            ORDER BY owner, schema_scope, object_type, grantee, privilege_type
            """,
            (role_names,),
        )
    )
    default_acl_owner_rows = _rows(
        connection.exec_driver_sql(
            """
            SELECT DISTINCT pg_get_userbyid(d.defaclrole) AS owner,
                   (current_user=pg_get_userbyid(d.defaclrole)
                    OR (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
                    OR pg_has_role(
                        current_user, pg_get_userbyid(d.defaclrole), 'MEMBER'
                    )) AS manageable
            FROM pg_default_acl d
            CROSS JOIN LATERAL aclexplode(d.defaclacl) x
            WHERE d.defaclnamespace IN (
                0, (SELECT oid FROM pg_namespace WHERE nspname='public')
            )
              AND (x.grantee=0 OR pg_get_userbyid(x.grantee)=ANY(%s::text[]))
            ORDER BY owner
            """,
            (role_names,),
        )
    )
    unmanageable_owners = sorted(
        set(unmanageable_owners)
        | {
            str(row["owner"])
            for row in default_acl_owner_rows
            if row["manageable"] is not True
        }
    )

    database_privileges = _grid(
        connection,
        "has_database_privilege",
        existing_role_names,
        [str(identity["database"])],
        matrix.DATABASE_PRIVILEGES,
    )
    database_privileges.extend(
        _denied_grid(
            missing_role_names,
            [str(identity["database"])],
            matrix.DATABASE_PRIVILEGES,
        )
    )
    schema_privileges = _grid(
        connection,
        "has_schema_privilege",
        existing_role_names,
        ["public"],
        matrix.SCHEMA_PRIVILEGES,
    )
    schema_privileges.extend(
        _denied_grid(missing_role_names, ["public"], matrix.SCHEMA_PRIVILEGES)
    )
    relation_privileges = _grid(
        connection,
        "has_table_privilege",
        existing_role_names,
        [*tables, *views],
        matrix.RELATION_PRIVILEGES,
    )
    relation_privileges.extend(
        _denied_grid(
            missing_role_names,
            [*tables, *views],
            matrix.RELATION_PRIVILEGES,
        )
    )
    sequence_privileges = _grid(
        connection,
        "has_sequence_privilege",
        existing_role_names,
        sequences,
        matrix.SEQUENCE_PRIVILEGES,
    )
    sequence_privileges.extend(
        _denied_grid(missing_role_names, sequences, matrix.SEQUENCE_PRIVILEGES)
    )
    column_privileges = _column_grid(connection, existing_role_names, relation_columns)
    column_privileges.extend(_denied_column_grid(missing_role_names, relation_columns))

    row_counts: dict[str, int] = {}
    content_hashes: dict[str, str] = {}
    for table in tables:
        quoted = _quote(table)
        row = _one(
            connection.exec_driver_sql(f"SELECT count(*) AS count FROM {quoted}")
        )
        row_counts[table] = int(row["count"])
        digest = hashlib.sha256()
        result = connection.exec_driver_sql(
            f"SELECT row_to_json(source_row)::text AS payload "
            f"FROM {quoted} AS source_row ORDER BY row_to_json(source_row)::text"
        )
        for content_row in result.mappings():
            encoded = str(content_row["payload"]).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, byteorder="big"))
            digest.update(encoded)
        content_hashes[table] = digest.hexdigest()

    return {
        "database": str(identity["database"]),
        "current_user": str(identity["current_user"]),
        "current_user_superuser": identity["superuser"] is True,
        "tables": tables,
        "views": views,
        "sequences": sequences,
        "relation_columns": relation_columns,
        "view_definitions": view_definitions,
        "indexes": indexes,
        "constraints": constraints,
        "roles": [dict(row) for row in role_rows],
        "memberships": [dict(row) for row in memberships],
        "role_settings": [dict(row) for row in role_settings],
        "managed_owners": managed_owners,
        "unmanageable_owners": unmanageable_owners,
        "relation_catalog": [dict(row) for row in relation_rows],
        "public_acl": [dict(row) for row in public_acl],
        "default_acl": [dict(row) for row in default_acl],
        "database_privileges": database_privileges,
        "schema_privileges": schema_privileges,
        "relation_privileges": relation_privileges,
        "column_privileges": column_privileges,
        "sequence_privileges": sequence_privileges,
        "row_counts": row_counts,
        "content_hashes": content_hashes,
    }


def _expected_allowed(
    contract: matrix.RoleMatrixContract,
    category: str,
    role: matrix.ManagedRole,
    object_name: str,
    privilege: str,
    *,
    column: str | None = None,
) -> bool:
    if category == "database":
        return privilege in contract.database_privileges[role]
    if category == "schema":
        return privilege in contract.schema_privileges[role]
    if category == "relation":
        return privilege in contract.relation_privileges[object_name][role]
    if category == "sequence":
        return privilege in contract.sequence_privileges[object_name][role]
    if category == "column" and column is not None:
        # table-level privilege는 모든 column에서 effective하게 보인다.
        if privilege in contract.relation_privileges[object_name][role]:
            return True
        return privilege in (
            contract.column_privileges.get(object_name, {})
            .get(column, {})
            .get(role, frozenset())
        )
    raise RoleMatrixError("알 수 없는 privilege category입니다")


def inspect_snapshot(
    snapshot: Mapping[str, Any], contract: matrix.RoleMatrixContract
) -> Inspection:
    unsafe: list[str] = []
    delta: list[str] = []
    if snapshot.get("database") != contract.database:
        unsafe.append("DATABASE_IDENTITY_MISMATCH")
    for label, actual, expected in (
        ("TABLE", set(snapshot.get("tables", ())), set(contract.inventory.tables)),
        ("VIEW", set(snapshot.get("views", ())), set(contract.inventory.views)),
        (
            "SEQUENCE",
            set(snapshot.get("sequences", ())),
            set(contract.inventory.sequences),
        ),
    ):
        if actual != expected:
            unsafe.append(f"{label}_INVENTORY_MISMATCH")

    expected_columns = matrix.expected_table_columns(
        contract.profile, contract.schema_stage
    )
    actual_columns = snapshot.get("relation_columns", {})
    if any(
        tuple(actual_columns.get(table, ())) != columns
        for table, columns in expected_columns.items()
    ):
        unsafe.append("TABLE_COLUMN_INVENTORY_MISMATCH")
    content_hashes = snapshot.get("content_hashes", {})
    if set(content_hashes) != set(contract.inventory.tables) or any(
        not HEX_SHA256.fullmatch(str(value)) for value in content_hashes.values()
    ):
        unsafe.append("TABLE_CONTENT_FINGERPRINT_INVALID")

    role_rows = {row["role_name"]: row for row in snapshot.get("roles", ())}
    for role in matrix.MANAGED_ROLES:
        row = role_rows.get(role.value)
        if row is None:
            delta.append(f"ROLE_MISSING:{role.value}")
            continue
        if any(
            row.get(field) is True
            for field in (
                "superuser",
                "createdb",
                "createrole",
                "replication",
                "bypassrls",
            )
        ):
            unsafe.append(f"ROLE_ELEVATED:{role.value}")
        if bool(row.get("login")) != matrix.ROLE_SPECS[role].login:
            delta.append(f"ROLE_LOGIN_DRIFT:{role.value}")

    if snapshot.get("memberships"):
        unsafe.append("ROLE_MEMBERSHIP_PRESENT")
    if snapshot.get("managed_owners"):
        unsafe.append("MANAGED_ROLE_OWNS_OBJECT")
    if snapshot.get("unmanageable_owners"):
        unsafe.append("OWNER_PRIVILEGE_INSUFFICIENT")
    if snapshot.get("public_acl"):
        delta.append("PUBLIC_PRIVILEGE_PRESENT")
    if snapshot.get("default_acl"):
        delta.append("DEFAULT_PRIVILEGE_PRESENT")

    actual_settings: dict[str, set[str]] = {
        role.value: set() for role in matrix.MANAGED_ROLES
    }
    for row in snapshot.get("role_settings", ()):
        role_name = str(row.get("role_name"))
        if role_name not in actual_settings:
            unsafe.append("ROLE_SETTING_ROLE_INVALID")
            continue
        actual_settings[role_name].add(str(row.get("setting")))
    for role in matrix.MANAGED_ROLES:
        expected_settings = (
            {"default_transaction_read_only=on", "statement_timeout=5s"}
            if role in {matrix.ManagedRole.READONLY, matrix.ManagedRole.EVALUATION}
            else set()
        )
        if actual_settings[role.value] != expected_settings:
            delta.append(f"ROLE_SETTING_DRIFT:{role.value}")

    actual_count = 0
    expected_count = 0
    categories = (
        ("database", snapshot.get("database_privileges", ())),
        ("schema", snapshot.get("schema_privileges", ())),
        ("relation", snapshot.get("relation_privileges", ())),
        ("column", snapshot.get("column_privileges", ())),
        ("sequence", snapshot.get("sequence_privileges", ())),
    )
    for category, rows in categories:
        for row in rows:
            try:
                role = matrix.ManagedRole(str(row["role_name"]))
            except (KeyError, ValueError):
                unsafe.append("PRIVILEGE_ROLE_INVALID")
                continue
            object_name = str(row["object_name"])
            privilege = str(row["privilege_name"])
            allowed = row.get("allowed") is True
            if allowed:
                actual_count += 1
            expected = _expected_allowed(
                contract,
                category,
                role,
                object_name,
                privilege,
                column=(
                    str(row.get("column_name"))
                    if row.get("column_name") is not None
                    else None
                ),
            )
            if expected:
                expected_count += 1
            if allowed != expected:
                column_suffix = (
                    f":{row['column_name']}"
                    if row.get("column_name") is not None
                    else ""
                )
                delta.append(
                    f"ACL_DRIFT:{category}:{role.value}:{object_name}"
                    f"{column_suffix}:{privilege}"
                )

    unique_unsafe = tuple(sorted(set(unsafe)))
    unique_delta = tuple(sorted(set(delta)))
    state = "UNSAFE" if unique_unsafe else ("READY_DRIFT" if unique_delta else "READY")
    return Inspection(
        state=state,
        unsafe=unique_unsafe,
        delta=unique_delta,
        actual_privilege_count=actual_count,
        expected_privilege_count=expected_count,
    )


def _quote(identifier: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
        raise RoleMatrixError("SQL identifier가 allowlist 형식이 아닙니다")
    return '"' + identifier.replace('"', '""') + '"'


def _quote_catalog_identifier(identifier: str) -> str:
    if not identifier or "\x00" in identifier:
        raise RoleMatrixError("catalog SQL identifier가 잘못됐습니다")
    return '"' + identifier.replace('"', '""') + '"'


def _role_sql(role: matrix.ManagedRole) -> str:
    return _quote(role.value)


def _revoke_targets() -> str:
    return ", ".join(_role_sql(role) for role in matrix.MANAGED_ROLES)


def _read_secrets(environ: Mapping[str, str]) -> dict[matrix.ManagedRole, str]:
    secrets: dict[matrix.ManagedRole, str] = {}
    for role, env_name in ROLE_PASSWORD_ENV.items():
        value = environ.get(env_name, "")
        if not value:
            raise RoleMatrixError(
                f"필수 role credential이 없습니다: {env_name}",
                reason_code="ROLE_CREDENTIAL_MISSING",
            )
        secrets[role] = value
    return secrets


def _create_missing_roles(
    connection: Connection,
    snapshot: Mapping[str, Any],
    secrets: Mapping[matrix.ManagedRole, str],
) -> None:
    existing = {str(row["role_name"]) for row in snapshot.get("roles", ())}
    driver = connection.connection.driver_connection
    for role in matrix.MANAGED_ROLES:
        if role.value in existing:
            continue
        login = matrix.ROLE_SPECS[role].login
        with driver.cursor() as cursor:
            if login:
                verifier = _scram_verifier(driver, role, secrets[role])
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(role.value), sql.Literal(verifier))
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(role.value))
                )


def _scram_verifier(driver: Any, role: matrix.ManagedRole, secret: str) -> str:
    """libpq로 SCRAM verifier를 만들어 평문 secret의 SQL 전송을 막는다."""

    try:
        encrypted = driver.pgconn.encrypt_password(
            secret.encode("utf-8"),
            role.value.encode("utf-8"),
            b"scram-sha-256",
        )
        verifier = encrypted.decode("ascii")
    except (AttributeError, TypeError, ValueError, UnicodeError, PsycopgError) as exc:
        raise RoleMatrixError(
            "SCRAM verifier를 생성할 수 없습니다",
            reason_code="ROLE_VERIFIER_FAILED",
            exit_code=EXIT_BLOCKED,
        ) from exc
    if not verifier.startswith("SCRAM-SHA-256$") or verifier == secret:
        raise RoleMatrixError(
            "SCRAM verifier 형식이 잘못됐습니다",
            reason_code="ROLE_VERIFIER_FAILED",
            exit_code=EXIT_BLOCKED,
        )
    return verifier


def _all_columns(snapshot: Mapping[str, Any], relation: str) -> tuple[str, ...]:
    columns = snapshot.get("relation_columns", {}).get(relation, ())
    if not columns:
        raise RoleMatrixError(f"relation column inventory가 없습니다: {relation}")
    return tuple(str(column) for column in columns)


def _apply_acl(
    connection: Connection,
    contract: matrix.RoleMatrixContract,
    snapshot: Mapping[str, Any],
    secrets: Mapping[matrix.ManagedRole, str],
) -> None:
    _create_missing_roles(connection, snapshot, secrets)
    for role in matrix.MANAGED_ROLES:
        login = "LOGIN" if matrix.ROLE_SPECS[role].login else "NOLOGIN"
        connection.exec_driver_sql(
            f"ALTER ROLE {_role_sql(role)} {login} NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )

    database = _quote(contract.database)
    managed = _revoke_targets()
    connection.exec_driver_sql(
        f"REVOKE ALL PRIVILEGES ON DATABASE {database} FROM PUBLIC"
    )
    connection.exec_driver_sql(
        f"REVOKE ALL PRIVILEGES ON DATABASE {database} FROM {managed}"
    )
    connection.exec_driver_sql("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
    connection.exec_driver_sql(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {managed}")

    for role, privileges in contract.database_privileges.items():
        if privileges:
            connection.exec_driver_sql(
                f"GRANT {', '.join(sorted(privileges))} ON DATABASE {database} "
                f"TO {_role_sql(role)}"
            )
    for role, privileges in contract.schema_privileges.items():
        if privileges:
            connection.exec_driver_sql(
                f"GRANT {', '.join(sorted(privileges))} ON SCHEMA public "
                f"TO {_role_sql(role)}"
            )

    for relation in sorted(contract.inventory.relations):
        quoted = _quote(relation)
        connection.exec_driver_sql(
            f"REVOKE ALL PRIVILEGES ON TABLE {quoted} FROM PUBLIC"
        )
        connection.exec_driver_sql(
            f"REVOKE ALL PRIVILEGES ON TABLE {quoted} FROM {managed}"
        )
        column_list = ", ".join(
            _quote(name) for name in _all_columns(snapshot, relation)
        )
        for role in matrix.MANAGED_ROLES:
            for privilege in matrix.COLUMN_PRIVILEGES:
                connection.exec_driver_sql(
                    f"REVOKE {privilege} ({column_list}) ON TABLE {quoted} "
                    f"FROM {_role_sql(role)}"
                )
        for role, privileges in contract.relation_privileges[relation].items():
            if privileges:
                connection.exec_driver_sql(
                    f"GRANT {', '.join(sorted(privileges))} ON TABLE {quoted} "
                    f"TO {_role_sql(role)}"
                )

    for relation, column_map in contract.column_privileges.items():
        quoted_relation = _quote(relation)
        by_role: dict[matrix.ManagedRole, dict[str, list[str]]] = {
            role: {} for role in matrix.MANAGED_ROLES
        }
        for column, role_map in column_map.items():
            for role, privileges in role_map.items():
                for privilege in privileges:
                    by_role[role].setdefault(privilege, []).append(column)
        for role, privileges in by_role.items():
            for privilege, columns in privileges.items():
                quoted_columns = ", ".join(_quote(column) for column in sorted(columns))
                connection.exec_driver_sql(
                    f"GRANT {privilege} ({quoted_columns}) ON TABLE {quoted_relation} "
                    f"TO {_role_sql(role)}"
                )

    for sequence in sorted(contract.inventory.sequences):
        quoted = _quote(sequence)
        connection.exec_driver_sql(
            f"REVOKE ALL PRIVILEGES ON SEQUENCE {quoted} FROM PUBLIC"
        )
        connection.exec_driver_sql(
            f"REVOKE ALL PRIVILEGES ON SEQUENCE {quoted} FROM {managed}"
        )
        for role, privileges in contract.sequence_privileges[sequence].items():
            if privileges:
                connection.exec_driver_sql(
                    f"GRANT {', '.join(sorted(privileges))} ON SEQUENCE {quoted} "
                    f"TO {_role_sql(role)}"
                )

    owners = sorted(
        {str(row["owner"]) for row in snapshot.get("relation_catalog", ())}
        | {str(row["owner"]) for row in snapshot.get("default_acl", ())}
    )
    for owner in owners:
        quoted_owner = _quote_catalog_identifier(owner)
        for target in ("PUBLIC", *(_role_sql(role) for role in matrix.MANAGED_ROLES)):
            for scope in ("", " IN SCHEMA public"):
                connection.exec_driver_sql(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_owner}{scope} "
                    f"REVOKE ALL PRIVILEGES ON TABLES FROM {target}"
                )
                connection.exec_driver_sql(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_owner}{scope} "
                    f"REVOKE ALL PRIVILEGES ON SEQUENCES FROM {target}"
                )

    for role in matrix.MANAGED_ROLES:
        connection.exec_driver_sql(
            f"ALTER ROLE {_role_sql(role)} IN DATABASE {database} RESET ALL"
        )
    for role in (matrix.ManagedRole.READONLY, matrix.ManagedRole.EVALUATION):
        connection.exec_driver_sql(
            f"ALTER ROLE {_role_sql(role)} IN DATABASE {database} "
            "SET default_transaction_read_only = on"
        )
        connection.exec_driver_sql(
            f"ALTER ROLE {_role_sql(role)} IN DATABASE {database} "
            "SET statement_timeout = '5s'"
        )


def _credential_url(
    target: db_target.BootstrapTarget, role: matrix.ManagedRole, password: str
) -> URL:
    return URL.create(
        drivername=db_target.BOOTSTRAP_DRIVER,
        username=role.value,
        password=password,
        host=target.host,
        port=target.port,
        database=target.database,
    )


def verify_allowed_credentials(
    target: db_target.BootstrapTarget,
    contract: matrix.RoleMatrixContract,
    secrets: Mapping[matrix.ManagedRole, str],
    roles: frozenset[matrix.ManagedRole] | None = None,
) -> None:
    from sqlalchemy import create_engine

    selected = matrix.LOGIN_ROLES if roles is None else roles
    for role in selected:
        if "CONNECT" not in contract.database_privileges[role]:
            continue
        engine = create_engine(
            _credential_url(target, role, secrets[role]),
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
        try:
            with engine.connect() as connection:
                current = _one(
                    connection.exec_driver_sql("SELECT current_user AS current_user")
                )
                if current["current_user"] != role.value:
                    raise RoleMatrixError(
                        "role credential identity가 다릅니다",
                        reason_code="ROLE_CREDENTIAL_MISMATCH",
                    )
        except SQLAlchemyError as exc:
            raise RoleMatrixError(
                f"role credential 검증 실패: {role.value}",
                reason_code="ROLE_CREDENTIAL_MISMATCH",
                exit_code=EXIT_BLOCKED,
            ) from exc
        finally:
            engine.dispose()


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_utc_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise RoleMatrixError(
            f"{label} timestamp가 잘못됐습니다", reason_code="ARTIFACT_INVALID"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RoleMatrixError(
            f"{label} timestamp가 잘못됐습니다", reason_code="ARTIFACT_INVALID"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise RoleMatrixError(
            f"{label} timestamp는 UTC여야 합니다", reason_code="ARTIFACT_INVALID"
        )


def _snapshot_payload(
    snapshot: Mapping[str, Any],
    contract: matrix.RoleMatrixContract,
    change_reference: str,
) -> dict[str, Any]:
    safe = {key: snapshot[key] for key in SNAPSHOT_SAFE_KEYS}
    return {
        "artifact_type": "postgres_role_matrix_acl_snapshot",
        "format_version": 1,
        "task_id": matrix.TASK_ID,
        "dataset_epoch": matrix.DATASET_EPOCH,
        "database": contract.database,
        "profile": contract.profile.value,
        "schema_stage": contract.schema_stage.value,
        "matrix_stage": contract.matrix_stage.value,
        "matrix_digest_sha256": matrix.contract_digest(contract),
        "change_reference": change_reference,
        "captured_at": datetime.now(UTC).isoformat(),
        "snapshot": safe,
    }


def _validate_snapshot_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise RoleMatrixError("snapshot 경로는 절대경로여야 합니다")
    cursor = Path(expanded.anchor)
    for part in expanded.parts[1:]:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise RoleMatrixError(
                "ACL snapshot 경로에 symlink를 사용할 수 없습니다",
                reason_code="SNAPSHOT_PATH_UNSAFE",
            )
    resolved = expanded.resolve(strict=False)
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        pass
    else:
        raise RoleMatrixError(
            "ACL snapshot은 저장소 밖에 둬야 합니다",
            reason_code="SNAPSHOT_PATH_UNSAFE",
        )
    return resolved


def write_snapshot(path: Path, payload: Mapping[str, Any]) -> str:
    target = _validate_snapshot_path(path)
    if target.exists():
        raise RoleMatrixError(
            "ACL snapshot 경로가 이미 존재합니다",
            reason_code="SNAPSHOT_ALREADY_EXISTS",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_v3.scan_for_sensitive_values(payload)
    manifest_v3.atomic_save_json(target, payload)
    return _canonical_hash(payload)


def _read_json(path: Path, reason: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoleMatrixError(reason, reason_code="ARTIFACT_INVALID") from exc
    if not isinstance(payload, dict):
        raise RoleMatrixError(reason, reason_code="ARTIFACT_INVALID")
    return payload


def validate_approval(
    payload: Mapping[str, Any],
    contract: matrix.RoleMatrixContract,
    change_reference: str,
) -> str:
    expected_keys = {
        "artifact_type",
        "format_version",
        "task_id",
        "dataset_epoch",
        "change_reference",
        "status",
        "targets",
        "approved_at",
    }
    if set(payload) != expected_keys:
        raise RoleMatrixError(
            "approval key가 계약과 다릅니다", reason_code="APPROVAL_INVALID"
        )
    if (
        payload["artifact_type"] != APPROVAL_ARTIFACT_TYPE
        or payload["format_version"] != APPROVAL_FORMAT_VERSION
        or payload["task_id"] != matrix.TASK_ID
        or payload["dataset_epoch"] != matrix.DATASET_EPOCH
        or payload["status"] != "APPROVED"
    ):
        raise RoleMatrixError(
            "approval identity가 다릅니다", reason_code="APPROVAL_MISMATCH"
        )
    if payload["change_reference"] != change_reference:
        raise RoleMatrixError(
            "approval change ref가 다릅니다", reason_code="APPROVAL_MISMATCH"
        )
    expected = {
        "database": contract.database,
        "profile": contract.profile.value,
        "schema_stage": contract.schema_stage.value,
        "matrix_stage": contract.matrix_stage.value,
        "matrix_digest_sha256": matrix.contract_digest(contract),
    }
    targets = payload.get("targets")
    if not isinstance(targets, list) or targets.count(expected) != 1:
        raise RoleMatrixError(
            "approval target이 없습니다", reason_code="APPROVAL_MISMATCH"
        )
    _validate_utc_timestamp(payload["approved_at"], "approval")
    manifest_v3.scan_for_sensitive_values(payload)
    return _canonical_hash(payload)


def validate_snapshot(
    payload: Mapping[str, Any],
    contract: matrix.RoleMatrixContract,
    change_reference: str,
) -> str:
    """유실 marker 복구에 사용할 원 pre-state snapshot의 provenance를 검증한다."""

    expected_keys = {
        "artifact_type",
        "format_version",
        "task_id",
        "dataset_epoch",
        "database",
        "profile",
        "schema_stage",
        "matrix_stage",
        "matrix_digest_sha256",
        "change_reference",
        "captured_at",
        "snapshot",
    }
    if set(payload) != expected_keys:
        raise RoleMatrixError(
            "ACL snapshot key가 계약과 다릅니다",
            reason_code="SNAPSHOT_INVALID",
        )
    expected_identity = {
        "artifact_type": "postgres_role_matrix_acl_snapshot",
        "format_version": 1,
        "task_id": matrix.TASK_ID,
        "dataset_epoch": matrix.DATASET_EPOCH,
        "database": contract.database,
        "profile": contract.profile.value,
        "schema_stage": contract.schema_stage.value,
        "matrix_stage": contract.matrix_stage.value,
        "matrix_digest_sha256": matrix.contract_digest(contract),
        "change_reference": change_reference,
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        raise RoleMatrixError(
            "ACL snapshot identity가 다릅니다",
            reason_code="SNAPSHOT_MISMATCH",
        )
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict) or set(snapshot) != set(SNAPSHOT_SAFE_KEYS):
        raise RoleMatrixError(
            "ACL snapshot inventory가 계약과 다릅니다",
            reason_code="SNAPSHOT_INVALID",
        )
    if snapshot.get("database") != contract.database:
        raise RoleMatrixError(
            "ACL snapshot database가 다릅니다",
            reason_code="SNAPSHOT_MISMATCH",
        )
    _validate_utc_timestamp(payload["captured_at"], "snapshot")
    manifest_v3.scan_for_sensitive_values(payload)
    return _canonical_hash(payload)


def marker_path(contract: matrix.RoleMatrixContract, root: Path = MARKER_ROOT) -> Path:
    prefix = (
        "role_matrix_core"
        if contract.matrix_stage is matrix.MatrixStage.CORE
        else "role_matrix_checkpoint"
    )
    return root / f"{prefix}.{contract.database}.json"


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RoleMatrixError(
            f"선행 artifact를 읽을 수 없습니다: {path.name}",
            reason_code="PREDECESSOR_INVALID",
            exit_code=EXIT_BLOCKED,
        ) from exc


def predecessor_artifacts(
    contract: matrix.RoleMatrixContract, marker_root: Path = MARKER_ROOT
) -> dict[str, str]:
    """선택 stage의 선행 receipt를 filename과 content hash로 고정한다."""

    if contract.profile is matrix.Profile.EVALUATION:
        paths = [
            REPOSITORY_ROOT
            / "infra/bootstrap/manifests/evaluation.evaluation_reference.json"
        ]
    elif contract.matrix_stage is matrix.MatrixStage.CHECKPOINTED:
        core = matrix.build_contract(
            contract.database,
            matrix.MatrixStage.CORE,
            matrix.SchemaStage.RUNTIME_CHECKPOINTED,
        )
        paths = [
            marker_path(core, marker_root),
            marker_root / f"checkpoint_setup_final.{contract.database}.json",
        ]
    else:
        paths = [marker_root / f"agent_severity_guard_final.{contract.database}.json"]
        if contract.schema_stage is matrix.SchemaStage.RUNTIME_CHECKPOINTED:
            paths.append(
                marker_root / f"checkpoint_setup_final.{contract.database}.json"
            )
    return {path.name: _file_sha256(path) for path in paths}


def _marker_payload(
    contract: matrix.RoleMatrixContract,
    target: db_target.BootstrapTarget,
    change_reference: str,
    approval_sha256: str,
    snapshot_sha256: str,
    row_fingerprint_sha256: str,
    status: str,
    *,
    marker_root: Path = MARKER_ROOT,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "artifact_type": (
            "role_matrix_core"
            if contract.matrix_stage is matrix.MatrixStage.CORE
            else "role_matrix_checkpoint"
        ),
        "format_version": MARKER_FORMAT_VERSION,
        "task_id": matrix.TASK_ID,
        "dataset_epoch": matrix.DATASET_EPOCH,
        "database": contract.database,
        "profile": contract.profile.value,
        "schema_stage": contract.schema_stage.value,
        "matrix_stage": contract.matrix_stage.value,
        "matrix_digest_sha256": matrix.contract_digest(contract),
        "target_host_fingerprint": db_target.host_fingerprint(target.host, target.port),
        "approval_sha256": approval_sha256,
        "snapshot_sha256": snapshot_sha256,
        "row_fingerprint_sha256": row_fingerprint_sha256,
        "predecessor_artifacts": predecessor_artifacts(contract, marker_root),
        "change_reference": change_reference,
        "status": status,
        "applied_at": now,
        "recorded_at": now,
    }


def validate_marker(
    payload: Mapping[str, Any],
    contract: matrix.RoleMatrixContract,
    target: db_target.BootstrapTarget,
    marker_root: Path = MARKER_ROOT,
) -> None:
    expected_keys = set(
        _marker_payload(
            contract,
            target,
            "GH-0",
            "0" * 64,
            "0" * 64,
            "0" * 64,
            "APPLIED",
            marker_root=marker_root,
        )
    )
    if set(payload) != expected_keys:
        raise RoleMatrixError(
            "role marker key가 다릅니다", reason_code="MARKER_INVALID"
        )
    if (
        payload["artifact_type"]
        != (
            "role_matrix_core"
            if contract.matrix_stage is matrix.MatrixStage.CORE
            else "role_matrix_checkpoint"
        )
        or payload["format_version"] != MARKER_FORMAT_VERSION
        or payload["task_id"] != matrix.TASK_ID
        or payload["dataset_epoch"] != matrix.DATASET_EPOCH
        or payload["database"] != contract.database
        or payload["profile"] != contract.profile.value
        or payload["schema_stage"] != contract.schema_stage.value
        or payload["matrix_stage"] != contract.matrix_stage.value
        or payload["matrix_digest_sha256"] != matrix.contract_digest(contract)
        or payload["predecessor_artifacts"]
        != predecessor_artifacts(contract, marker_root)
        or payload["target_host_fingerprint"]
        != db_target.host_fingerprint(target.host, target.port)
        or payload["status"] not in {"APPLIED", "RECOVERED"}
    ):
        raise RoleMatrixError(
            "role marker identity가 다릅니다", reason_code="MARKER_INVALID"
        )
    for key in (
        "approval_sha256",
        "snapshot_sha256",
        "row_fingerprint_sha256",
    ):
        if not HEX_SHA256.fullmatch(str(payload[key])):
            raise RoleMatrixError(
                "role marker hash가 잘못됐습니다", reason_code="MARKER_INVALID"
            )
    if payload["status"] in {"APPLIED", "RECOVERED"} and (
        payload["approval_sha256"] == ZERO_SHA256
        or payload["snapshot_sha256"] == ZERO_SHA256
    ):
        raise RoleMatrixError(
            "role marker 증적 hash가 비어 있습니다",
            reason_code="MARKER_INVALID",
        )
    if not CHANGE_REFERENCE.fullmatch(str(payload["change_reference"])):
        raise RoleMatrixError(
            "role marker change ref가 잘못됐습니다", reason_code="MARKER_INVALID"
        )
    _validate_utc_timestamp(payload["applied_at"], "marker applied_at")
    _validate_utc_timestamp(payload["recorded_at"], "marker recorded_at")
    manifest_v3.scan_for_sensitive_values(payload)


def _row_fingerprint(snapshot: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            "tables": sorted(snapshot.get("tables", ())),
            "views": sorted(snapshot.get("views", ())),
            "sequences": sorted(snapshot.get("sequences", ())),
            "row_counts": snapshot.get("row_counts", {}),
            "content_hashes": snapshot.get("content_hashes", {}),
            "view_definitions": snapshot.get("view_definitions", ()),
            "indexes": snapshot.get("indexes", ()),
            "constraints": snapshot.get("constraints", ()),
        }
    )


def _result(
    contract: matrix.RoleMatrixContract,
    inspection: Inspection,
    *,
    mode: str,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": matrix.TASK_ID,
        "mode": mode,
        "database": contract.database,
        "profile": contract.profile.value,
        "schema_stage": contract.schema_stage.value,
        "matrix_stage": contract.matrix_stage.value,
        "matrix_digest_sha256": matrix.contract_digest(contract),
        "state": inspection.state,
        "status": status or inspection.state,
        "unsafe_count": len(inspection.unsafe),
        "delta_count": len(inspection.delta),
        "unsafe_reasons": list(inspection.unsafe),
        "delta_reasons": list(inspection.delta),
        "actual_privilege_count": inspection.actual_privilege_count,
        "expected_privilege_count": inspection.expected_privilege_count,
    }


def run(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
    marker_root: Path = MARKER_ROOT,
) -> tuple[int, dict[str, Any]]:
    mode = resolve_mode(args)
    contract = resolve_contract(args)
    source = os.environ if environ is None else environ
    target = db_target.load_bootstrap_target(contract.database, environ=source)
    secrets = _read_secrets(source)
    # predecessor 부재를 DB 연결·snapshot·ACL mutation보다 먼저 차단한다.
    predecessor_artifacts(contract, marker_root)
    engine = bootstrap_common._engine_for(target)
    before: dict[str, Any]
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL search_path = public")
            before = read_snapshot(connection)
        inspection = inspect_snapshot(before, contract)

        # checkpoint successor가 이미 기록된 뒤 core를 다시 실행해 app의
        # checkpoint 권한을 회수하는 downgrade를 막는다.
        if (
            contract.profile is matrix.Profile.RUNTIME
            and contract.matrix_stage is matrix.MatrixStage.CORE
            and contract.schema_stage is matrix.SchemaStage.RUNTIME_CHECKPOINTED
        ):
            successor = matrix.build_contract(
                contract.database,
                matrix.MatrixStage.CHECKPOINTED,
                matrix.SchemaStage.RUNTIME_CHECKPOINTED,
            )
            successor_path = marker_path(successor, marker_root)
            if successor_path.exists():
                successor_inspection = inspect_snapshot(before, successor)
                if not successor_inspection.exact:
                    return EXIT_BLOCKED, _result(
                        successor,
                        successor_inspection,
                        mode=mode,
                        status="SUCCESSOR_DRIFT",
                    )
                core_marker = _read_json(
                    marker_path(contract, marker_root),
                    "core role marker를 읽을 수 없습니다",
                )
                successor_marker = _read_json(
                    successor_path, "checkpoint role marker를 읽을 수 없습니다"
                )
                validate_marker(core_marker, contract, target, marker_root)
                validate_marker(successor_marker, successor, target, marker_root)
                return EXIT_OK, _result(
                    successor,
                    successor_inspection,
                    mode=mode,
                    status="SUPERSEDED_NO_OP",
                )

        if inspection.unsafe:
            return EXIT_BLOCKED, _result(
                contract, inspection, mode=mode, status="UNSAFE"
            )

        if mode in {"preflight", "preview"}:
            # existing allowed LOGIN role의 secret mismatch를 mutation 전에 드러낸다.
            existing = {str(row["role_name"]) for row in before["roles"]}
            existing_allowed = frozenset(
                role
                for role in matrix.LOGIN_ROLES
                if role.value in existing
                and "CONNECT" in contract.database_privileges[role]
            )
            verify_allowed_credentials(
                target, contract, secrets, roles=existing_allowed
            )
            return EXIT_OK, _result(contract, inspection, mode=mode)

        path = marker_path(contract, marker_root)
        if mode == "verify":
            if not inspection.exact:
                return EXIT_MISMATCH, _result(
                    contract, inspection, mode=mode, status="ACL_DRIFT"
                )
            marker = _read_json(path, "role marker를 읽을 수 없습니다")
            validate_marker(marker, contract, target, marker_root)
            return EXIT_OK, _result(contract, inspection, mode=mode, status="VERIFIED")

        if mode == "recover":
            if not inspection.exact:
                return EXIT_MISMATCH, _result(
                    contract, inspection, mode=mode, status="ACL_DRIFT"
                )
            if path.exists():
                marker = _read_json(path, "role marker를 읽을 수 없습니다")
                validate_marker(marker, contract, target, marker_root)
                return EXIT_OK, _result(contract, inspection, mode=mode, status="NO_OP")
            if not args.change_ref or not CHANGE_REFERENCE.fullmatch(args.change_ref):
                raise RoleMatrixError(
                    "--recover-marker에는 valid --change-ref가 필요합니다"
                )
            if args.approval is None or args.snapshot_out is None:
                raise RoleMatrixError(
                    "--recover-marker에는 원 --approval과 --snapshot-out이 필요합니다"
                )
            approval = _read_json(args.approval, "change approval을 읽을 수 없습니다")
            approval_sha = validate_approval(approval, contract, args.change_ref)
            snapshot_path = _validate_snapshot_path(args.snapshot_out)
            snapshot = _read_json(snapshot_path, "ACL snapshot을 읽을 수 없습니다")
            snapshot_sha = validate_snapshot(snapshot, contract, args.change_ref)
            payload = _marker_payload(
                contract,
                target,
                args.change_ref,
                approval_sha,
                snapshot_sha,
                _row_fingerprint(before),
                "RECOVERED",
                marker_root=marker_root,
            )
            manifest_v3.atomic_save_json(path, payload)
            return EXIT_OK, _result(contract, inspection, mode=mode, status="RECOVERED")

        if args.confirm_target != contract.database:
            raise RoleMatrixError("--confirm-target이 database와 다릅니다")
        if not args.change_ref or not CHANGE_REFERENCE.fullmatch(args.change_ref):
            raise RoleMatrixError("--apply에는 valid --change-ref가 필요합니다")

        if inspection.exact:
            if not path.exists():
                raise RoleMatrixError(
                    "live ACL은 exact지만 marker가 없습니다. "
                    "--recover-marker를 사용하세요",
                    reason_code="READY_UNMARKED",
                    exit_code=EXIT_MISMATCH,
                )
            marker = _read_json(path, "role marker를 읽을 수 없습니다")
            validate_marker(marker, contract, target, marker_root)
            verify_allowed_credentials(target, contract, secrets)
            return EXIT_OK, _result(contract, inspection, mode=mode, status="NO_OP")

        if args.approval is None or args.snapshot_out is None:
            raise RoleMatrixError(
                "--apply에는 --approval과 --snapshot-out이 필요합니다"
            )

        approval = _read_json(args.approval, "change approval을 읽을 수 없습니다")
        approval_sha = validate_approval(approval, contract, args.change_ref)
        snapshot_payload = _snapshot_payload(before, contract, args.change_ref)
        snapshot_sha = write_snapshot(args.snapshot_out, snapshot_payload)
        row_fingerprint = _row_fingerprint(before)

        existing = {str(row["role_name"]) for row in before["roles"]}
        existing_allowed = frozenset(
            role
            for role in matrix.LOGIN_ROLES
            if role.value in existing
            and "CONNECT" in contract.database_privileges[role]
        )
        verify_allowed_credentials(target, contract, secrets, roles=existing_allowed)

        with engine.connect() as connection, connection.begin():
            db_target.validate_connected_identity(connection, target)
            db_target.set_and_validate_public_search_path(connection)
            bootstrap_common.acquire_advisory_lock(connection, contract.database)
            locked = read_snapshot(connection)
            locked_inspection = inspect_snapshot(locked, contract)
            if locked_inspection.unsafe:
                raise RoleMatrixError(
                    "lock 안에서 unsafe state가 확인됐습니다",
                    reason_code="UNSAFE_STATE",
                    exit_code=EXIT_BLOCKED,
                )
            if _row_fingerprint(locked) != row_fingerprint:
                raise RoleMatrixError(
                    "preflight 이후 data inventory가 바뀌었습니다",
                    reason_code="DATA_DRIFT",
                    exit_code=EXIT_BLOCKED,
                )
            _apply_acl(connection, contract, locked, secrets)
            after_in_tx = read_snapshot(connection)
            post = inspect_snapshot(after_in_tx, contract)
            if not post.exact:
                raise RoleMatrixError(
                    "role matrix transaction postcheck가 실패했습니다",
                    reason_code="POSTCHECK_FAILED",
                    exit_code=EXIT_MISMATCH,
                )
            if _row_fingerprint(after_in_tx) != row_fingerprint:
                raise RoleMatrixError(
                    "role matrix 적용이 data fingerprint를 바꿨습니다",
                    reason_code="DATA_CHANGED",
                    exit_code=EXIT_MISMATCH,
                )

        verify_allowed_credentials(target, contract, secrets)
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL search_path = public")
            committed = read_snapshot(connection)
        committed_inspection = inspect_snapshot(committed, contract)
        if (
            not committed_inspection.exact
            or _row_fingerprint(committed) != row_fingerprint
        ):
            raise RoleMatrixError(
                "commit 뒤 role matrix 검증이 실패했습니다",
                reason_code="COMMITTED_STATE_INVALID",
                exit_code=EXIT_MISMATCH,
            )

        marker = _marker_payload(
            contract,
            target,
            args.change_ref,
            approval_sha,
            snapshot_sha,
            row_fingerprint,
            "APPLIED",
            marker_root=marker_root,
        )
        manifest_v3.atomic_save_json(path, marker)
        return EXIT_OK, _result(
            contract, committed_inspection, mode=mode, status="APPLIED"
        )
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> NoReturn:
    args = _parser().parse_args(argv)
    try:
        load_dotenv(REPOSITORY_ROOT / ".env")
        exit_code, payload = run(args)
    except (RoleMatrixError, db_target.TargetValidationError) as exc:
        exit_code = getattr(exc, "exit_code", EXIT_USAGE)
        payload = {
            "task_id": matrix.TASK_ID,
            "status": "FAIL",
            "reason": getattr(exc, "reason_code", "TARGET_INVALID"),
        }
    except (SQLAlchemyError, PsycopgError):
        exit_code = EXIT_BLOCKED
        payload = {
            "task_id": matrix.TASK_ID,
            "status": "FAIL",
            "reason": "DATABASE_OPERATION_FAILED",
        }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
