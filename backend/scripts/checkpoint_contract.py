"""`langgraph-checkpoint-postgres` checkpoint 저장소의 **불변 계약** (`V5-CM-3.4`).

## 왜 별도 모듈인가

package migration SQL을 저장소에 복사해 별도 정본을 만들지 않는다. 대신 설치된
package를 **읽어서** 계약과 대조한다. 복사하면 정본이 두 갈래가 되고, package를
올릴 때 한쪽만 낡는다.

## 왜 transaction으로 감쌀 수 없나

`MIGRATIONS` 9개 중 **3개가 `CREATE INDEX CONCURRENTLY`**다.

```text
autocommit=False  →  ActiveSqlTransaction: cannot run inside a transaction block
autocommit=True   →  OK
```

`V5-CM-3.2`·`V5-CM-3.3`은 단일 transaction에서 원자 전환하고 실패 시 rollback했다.
**checkpoint setup은 그럴 수 없다.** 중간 실패는 부분 적용으로 남으며, 그 복구는
승인된 backup restore가 담당한다.

## 왜 재실행만으로는 낫지 않나

`setup()`은 `checkpoint_migrations`의 최대 `v`만 보고 그 다음부터 실행한다. version이
이미 8이면 **아무 문장도 실행하지 않는다.** index가 사라지거나 invalid로 남아도
재실행이 복구하지 않는다 — 실측으로 확인했다.

그래서 `setup()`이 해주지 않는 **postcheck가 이 Task의 본체**다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

#: pinned package 버전. `requirements.txt`와 같아야 한다.
PACKAGE_NAME = "langgraph-checkpoint-postgres"
PACKAGE_VERSION = "2.0.9"

#: `manifest_v3.CHECKPOINT_MIGRATION_ID`와 같아야 한다. 회귀가 두 상수를 대조한다.
MIGRATION_ID = "langgraph_checkpoint_postgres_2_0_9_v8"

#: package migration 수와 최종 version. `setup()`이 `0..LATEST_VERSION`을 기록한다.
MIGRATION_COUNT = 9
LATEST_VERSION = 8

#: `MIGRATIONS` canonical digest.
#:
#: **정규화하지 않는다.** SQL whitespace를 접으면 문자열 literal 내부 변경을 놓친다.
#: pinned version에서 공백만 달라져도 설치 artifact가 기대와 다른 것이므로
#: fail-closed가 맞다. `MIGRATIONS[5]`가 `'\\n    '`인 것까지 그대로 pin한다.
MIGRATION_DIGEST_SHA256 = (
    "59b821ceebaf49a3e31cc431be9705729920c3792eb6064e4f3d2a036dc7e485"
)

#: `setup()`이 만드는 table. 순서는 migration 0~3 순서다.
CHECKPOINT_TABLES: tuple[str, ...] = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)

#: operational table. `setup()` 직후 전부 0행이며 `V5-C-*`가 채운다.
OPERATIONAL_TABLES: tuple[str, ...] = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)

#: migration 6·7·8이 만드는 concurrent index. **모두 valid여야 한다.**
CHECKPOINT_INDEXES: tuple[str, ...] = (
    "checkpoints_thread_id_idx",
    "checkpoint_blobs_thread_id_idx",
    "checkpoint_writes_thread_id_idx",
)

#: **PostgreSQL 16 실측이다.** 손으로 적지 않았다 — 격리 container에서
#: `PostgresSaver(conn).setup()`을 돌리고 catalog를 읽어 생성했다.
#:
#: 이름만 보면 컬럼·PK·index 정의가 통째로 바뀐 schema도 통과한다
#: (구현리뷰 필수 2가 재현했다). exact 계약이 있어야 그것을 잡는다.
EXPECTED_COLUMNS: Mapping[str, tuple[dict[str, Any], ...]] = MappingProxyType(
    {
        "checkpoint_blobs": (
            {"column": "thread_id", "type": "text", "not_null": True, "default": None},
            {
                "column": "checkpoint_ns",
                "type": "text",
                "not_null": True,
                "default": "''::text",
            },
            {"column": "channel", "type": "text", "not_null": True, "default": None},
            {"column": "version", "type": "text", "not_null": True, "default": None},
            {"column": "type", "type": "text", "not_null": True, "default": None},
            {"column": "blob", "type": "bytea", "not_null": False, "default": None},
        ),
        "checkpoint_migrations": (
            {"column": "v", "type": "integer", "not_null": True, "default": None},
        ),
        "checkpoint_writes": (
            {"column": "thread_id", "type": "text", "not_null": True, "default": None},
            {
                "column": "checkpoint_ns",
                "type": "text",
                "not_null": True,
                "default": "''::text",
            },
            {
                "column": "checkpoint_id",
                "type": "text",
                "not_null": True,
                "default": None,
            },
            {"column": "task_id", "type": "text", "not_null": True, "default": None},
            {"column": "idx", "type": "integer", "not_null": True, "default": None},
            {"column": "channel", "type": "text", "not_null": True, "default": None},
            {"column": "type", "type": "text", "not_null": False, "default": None},
            {"column": "blob", "type": "bytea", "not_null": True, "default": None},
        ),
        "checkpoints": (
            {"column": "thread_id", "type": "text", "not_null": True, "default": None},
            {
                "column": "checkpoint_ns",
                "type": "text",
                "not_null": True,
                "default": "''::text",
            },
            {
                "column": "checkpoint_id",
                "type": "text",
                "not_null": True,
                "default": None,
            },
            {
                "column": "parent_checkpoint_id",
                "type": "text",
                "not_null": False,
                "default": None,
            },
            {"column": "type", "type": "text", "not_null": False, "default": None},
            {
                "column": "checkpoint",
                "type": "jsonb",
                "not_null": True,
                "default": None,
            },
            {
                "column": "metadata",
                "type": "jsonb",
                "not_null": True,
                "default": "'{}'::jsonb",
            },
        ),
    }
)

#: PK 정의. column 순서까지 계약이다.
EXPECTED_PRIMARY_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "checkpoint_blobs": "PRIMARY KEY (thread_id, checkpoint_ns, channel, version)",
        "checkpoint_migrations": "PRIMARY KEY (v)",
        "checkpoint_writes": (
            "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)"
        ),
        "checkpoints": "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)",
    }
)

#: index 정의. 대상 table·column까지 본다 — 이름만 맞고 다른 컬럼을 거는
#: index는 thread 조회를 못 살린다.
EXPECTED_INDEX_DEFINITIONS: Mapping[str, dict[str, str]] = MappingProxyType(
    {
        "checkpoint_blobs_thread_id_idx": {
            "table": "checkpoint_blobs",
            "definition": (
                "CREATE INDEX checkpoint_blobs_thread_id_idx "
                "ON public.checkpoint_blobs USING btree (thread_id)"
            ),
        },
        "checkpoint_writes_thread_id_idx": {
            "table": "checkpoint_writes",
            "definition": (
                "CREATE INDEX checkpoint_writes_thread_id_idx "
                "ON public.checkpoint_writes USING btree (thread_id)"
            ),
        },
        "checkpoints_thread_id_idx": {
            "table": "checkpoints",
            "definition": (
                "CREATE INDEX checkpoints_thread_id_idx "
                "ON public.checkpoints USING btree (thread_id)"
            ),
        },
    }
)


#: `CREATE INDEX CONCURRENTLY`인 migration version. 이것 때문에 autocommit이 필수다.
CONCURRENT_INDEX_VERSIONS: tuple[int, ...] = (6, 7, 8)


class CheckpointContractError(RuntimeError):
    """계약 위반. **DB에 연결하기 전에** 난다."""

    def __init__(self, message: str, *, reason_code: str = "CONTRACT_INVALID") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.exit_code = 2


def migration_digest(migrations: Sequence[str]) -> str:
    """`MIGRATIONS`의 canonical digest.

    raw 문자열 배열을 canonical JSON으로 직렬화한 UTF-8 bytes의 SHA-256이다.
    migration 순서와 각 문자열의 문자·공백을 그대로 pin한다.
    """

    payload = json.dumps(
        list(migrations), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assert_package_contract() -> tuple[str, ...]:
    """설치된 package가 pinned 계약과 같은지 본다. **DB보다 앞이다.**

    반환값은 `MIGRATIONS`다 — 호출부가 다시 import하지 않게 여기서 넘긴다.
    """

    import importlib.metadata as metadata

    try:
        version = metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError as exc:
        raise CheckpointContractError(
            f"{PACKAGE_NAME}가 설치돼 있지 않습니다", reason_code="PACKAGE_MISSING"
        ) from exc
    if version != PACKAGE_VERSION:
        raise CheckpointContractError(
            "checkpoint package version이 계약과 다릅니다",
            reason_code="PACKAGE_VERSION_MISMATCH",
        )

    from langgraph.checkpoint.postgres import base

    migrations = tuple(base.MIGRATIONS)
    if len(migrations) != MIGRATION_COUNT:
        raise CheckpointContractError(
            "checkpoint migration 수가 계약과 다릅니다",
            reason_code="MIGRATION_COUNT_MISMATCH",
        )
    if migration_digest(migrations) != MIGRATION_DIGEST_SHA256:
        raise CheckpointContractError(
            "checkpoint migration digest가 계약과 다릅니다",
            reason_code="MIGRATION_DIGEST_MISMATCH",
        )
    concurrent = tuple(
        index
        for index, statement in enumerate(migrations)
        if "CONCURRENTLY" in statement.upper()
    )
    if concurrent != CONCURRENT_INDEX_VERSIONS:
        raise CheckpointContractError(
            "concurrent index migration 위치가 계약과 다릅니다",
            reason_code="MIGRATION_SHAPE_MISMATCH",
        )
    return migrations


def expected_versions() -> tuple[int, ...]:
    """`checkpoint_migrations.v`의 exact 집합. 중복·gap이 없어야 한다."""

    return tuple(range(LATEST_VERSION + 1))


def catalog_signature(payload: Mapping[str, Any]) -> str:
    """checkpoint catalog의 canonical signature.

    table·column·PK·index validity·version 집합을 한 값으로 접는다.
    """

    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# live catalog 판정 — `setup()`이 해주지 않는 부분
# ---------------------------------------------------------------------------

#: table·column·PK를 한 번에 읽는다. `setup()`은 이것을 검증하지 않는다.
CATALOG_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND c.relname = ANY(%s)
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

#: **index validity가 핵심이다.**
#:
#: `CREATE INDEX CONCURRENTLY`는 실패 시 invalid index를 남기고, `IF NOT EXISTS`가
#: 그것을 건너뛴다. `indisvalid`·`indisready`를 보지 않으면 부분 적용이 정상으로
#: 보인다.
INDEX_SQL = """
SELECT c.relname AS index_name,
       i.indisvalid AS is_valid,
       i.indisready AS is_ready,
       t.relname AS table_name,
       pg_get_indexdef(i.indexrelid) AS definition
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
ORDER BY c.relname
"""

PRIMARY_KEY_SQL = """
SELECT t.relname AS table_name,
       pg_get_constraintdef(x.oid, true) AS definition
FROM pg_constraint x
JOIN pg_class t ON t.oid = x.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND x.contype = 'p' AND t.relname = ANY(%s)
ORDER BY t.relname
"""


#: checkpoint 4종의 **소유자와 PUBLIC 노출**을 함께 읽는다.
#:
#: `setup()`은 자기가 만든 object의 권한을 좁혀 주지 않는다. 그래서 catalog와 **같은
#: 시점에** 읽어 하나의 상태 판정에 넣는다 — 이 축을 별도 postcheck로만 두면
#: preflight·no-op·verify·smoke가 ACL을 보지 않는 경로가 되고, 그중 가장 느슨한 쪽이
#: 실효 계약이 된다(구현리뷰 13차 필수 3).
CHECKPOINT_ACL_SQL = """/* cm34:checkpoint-acl */
SELECT c.relname AS table_name,
       pg_get_userbyid(c.relowner) AS owner,
       coalesce(
           (
               SELECT array_agg(DISTINCT a.privilege_type ORDER BY a.privilege_type)
               FROM aclexplode(c.relacl) AS a
               WHERE a.grantee = 0
           ),
           ARRAY[]::text[]
       ) AS public_table_grants,
       coalesce(
           (
               SELECT array_agg(DISTINCT att.attname || ':' || ca.privilege_type)
               FROM pg_attribute att
               CROSS JOIN LATERAL aclexplode(att.attacl) AS ca
               WHERE att.attrelid = c.oid
                 AND att.attnum > 0
                 AND NOT att.attisdropped
                 AND ca.grantee = 0
           ),
           ARRAY[]::text[]
       ) AS public_column_grants
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
ORDER BY 1
"""


class CheckpointStateError(RuntimeError):
    """live 상태가 자동 보정 대상이 아니다."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.exit_code = 1


def inspect_catalog(rows: Mapping[str, Any]) -> dict[str, Any]:
    """읽은 catalog row를 판정 가능한 payload로 접는다. **연결을 모른다.**

    순수 함수라 회귀가 변이를 직접 넣을 수 있다.

    `rows["expected_owner"]`는 **읽는 쪽이 정한다**. 이 함수는 그것이 누구인지
    묻지 않고 판정에만 쓴다 — catalog를 읽은 연결이 그 답을 갖고 있다.
    """

    columns: dict[str, list[dict[str, Any]]] = {}
    for row in rows["columns"]:
        default = row.get("column_default")
        columns.setdefault(str(row["table_name"]), []).append(
            {
                "column": str(row["column_name"]),
                "type": str(row["data_type"]),
                "not_null": bool(row["not_null"]),
                "default": None if default is None else str(default),
            }
        )
    indexes = {
        str(row["index_name"]): {
            "valid": bool(row["is_valid"]),
            "ready": bool(row["is_ready"]),
            "table": str(row["table_name"]),
            "definition": str(row.get("definition") or ""),
        }
        for row in rows["indexes"]
    }
    primary_keys = {
        str(row["table_name"]): str(row["definition"]) for row in rows["primary_keys"]
    }
    versions = sorted(int(row["v"]) for row in rows["versions"])
    return {
        "tables": sorted(columns),
        "columns": columns,
        "indexes": indexes,
        "primary_keys": primary_keys,
        "versions": versions,
        # **ACL은 선택 축이 아니다.** `rows["acl"]`이 없으면 여기서 `KeyError`다 —
        # 읽지 않고 판정하는 경로를 만들지 않기 위해서다.
        "acl": fold_acl(rows["acl"]),
        # **기대 owner도 마찬가지다.** 없으면 `KeyError`다.
        #
        # signature payload에는 넣지 않는다 — signature는 "DB가 어떤 상태인가"를
        # 말해야 하고, 기대값은 "누가 물었는가"이기 때문이다. 둘을 섞으면 다른
        # 계정으로 검증할 때 signature가 달라진다.
        "expected_owner": str(rows["expected_owner"]),
    }


def fold_acl(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`CHECKPOINT_ACL_SQL` row를 판정 가능한 축으로 접는다. **순수 함수다.**

    table 권한과 column 권한을 **한 목록**으로 합친다. 둘을 나눠 두면 한쪽만 보는
    소비자가 생긴다 — 4차 필수 2가 정확히 그 모양이었다.
    """

    owners = {str(row["table_name"]): str(row["owner"]) for row in rows}
    grants: set[str] = set()
    for row in rows:
        table = str(row["table_name"])
        for privilege in row["public_table_grants"] or ():
            grants.add(f"{table}:{privilege}")
        for entry in row["public_column_grants"] or ():
            grants.add(f"{table}.{entry}")
    return {"owners": owners, "public_grants": sorted(grants)}


def classify_state(catalog: Mapping[str, Any]) -> str:
    """`ABSENT` · `READY` · `PARTIAL` 중 하나로 접는다.

    **`READY`는 전부 맞을 때뿐이다.** 하나라도 어긋나면 `PARTIAL`이고, 자동으로
    이어 붙이지 않는다 — `setup()` 재실행이 index를 복구하지 못하기 때문이다.
    """

    tables = set(catalog["tables"])
    expected_tables = set(CHECKPOINT_TABLES)
    if not tables & expected_tables:
        return "ABSENT"
    if tables & expected_tables != expected_tables:
        return "PARTIAL"

    indexes = catalog["indexes"]
    if set(indexes) != set(CHECKPOINT_INDEXES):
        return "PARTIAL"
    if not all(item["valid"] and item["ready"] for item in indexes.values()):
        # invalid index는 `IF NOT EXISTS`가 건너뛰므로 재실행으로 낫지 않는다.
        return "PARTIAL"
    if tuple(catalog["versions"]) != expected_versions():
        return "PARTIAL"

    # **이름만 보지 않는다.**
    #
    # 컬럼·PK·index 정의를 통째로 바꾼 schema가 `READY`로 통과했다
    # (구현리뷰 필수 2가 재현했다). 여기서 exact 계약과 대조한다.
    for table, expected in EXPECTED_COLUMNS.items():
        if tuple(catalog["columns"].get(table, ())) != expected:
            return "DRIFT"
    if dict(catalog["primary_keys"]) != dict(EXPECTED_PRIMARY_KEYS):
        return "DRIFT"
    for name, expected_index in EXPECTED_INDEX_DEFINITIONS.items():
        actual = indexes[name]
        if (actual["table"], actual["definition"]) != (
            expected_index["table"],
            expected_index["definition"],
        ):
            return "DRIFT"

    # **owner·PUBLIC ACL도 같은 판정 안에서 본다**(구현리뷰 13차 필수 3).
    #
    # 이전에는 ACL 판정이 apply 직후와 full verifier에서만 돌아서,
    # `GRANT SELECT ON checkpoints TO PUBLIC` 뒤에도 preflight가 `READY_MARKED`,
    # 재실행이 `NO_OP`, verify가 성공했다. 계획의 "DRIFT = owner·PUBLIC ACL 계약
    # 불일치"와 코드가 달랐고, 복구의 `DRIFT` 허용 경로에도 들어오지 못했다.
    acl = catalog["acl"]
    if set(acl["owners"]) != expected_tables:
        return "DRIFT"
    if acl["public_grants"]:
        return "DRIFT"
    owners = set(acl["owners"].values())
    if len(owners) != 1:
        # 4종의 주인이 갈리면 하나의 저장소가 아니다.
        return "DRIFT"
    if owners != {catalog["expected_owner"]}:
        # **네 개를 함께 옮겨도 drift다**(구현리뷰 14차 필수 1).
        #
        # 이전에는 "서로 같은가"만 봤다. 그래서 4종을 한꺼번에 다른 role로 넘기면
        # `READY`였고, marker를 읽는 경로들은 signature 차이로 `MARKER_DRIFT`를
        # 냈지만 **marker를 읽지 않는 복구**는 `PARTIAL|DRIFT`만 허용하므로
        # `RECOVERY_STATE_INVALID`가 됐다. 계획이 정한 유일한 복구 경로가 닫힌
        # 것이다. 하나만 바꾸면 복구 대상이고 넷을 바꾸면 복구 거부라는 차이는
        # 보안 계약상 의미가 없다.
        return "DRIFT"
    return "READY"


def assert_ready(catalog: Mapping[str, Any]) -> str:
    """`READY`가 아니면 sanitized reason으로 끝낸다. 반환값은 catalog signature다."""

    state = classify_state(catalog)
    if state != "READY":
        raise CheckpointStateError(
            "checkpoint catalog가 계약과 다릅니다", reason_code=state
        )
    return catalog_signature(
        {
            "columns": catalog["columns"],
            "indexes": catalog["indexes"],
            "primary_keys": catalog["primary_keys"],
            "versions": catalog["versions"],
            # **owner를 signature에 넣는다.** 4종이 한꺼번에 다른 role로 넘어가면
            # 물리 계약은 그대로라 `classify_state()`가 `READY`를 내는데, marker가
            # 기록한 signature와 달라져 `MARKER_DRIFT`로 걸린다.
            "acl": catalog["acl"],
        }
    )


def assert_checkpoint_acl(catalog: Mapping[str, Any]) -> dict[str, str]:
    """checkpoint 4종의 **소유자와 PUBLIC 노출**을 본다. 반환은 `table → owner`다.

    `classify_state()`가 이미 같은 축을 보지만 상태 하나로 접어 `DRIFT`만 낸다.
    운영자에게는 어느 축인지가 필요하므로 여기서 `ACL_PUBLIC`·`ACL_OWNER`로 나눈다.
    **판정 근거는 같은 `catalog` 하나다** — 두 함수가 각자 읽지 않고, 기대 owner도
    각자 정하지 않는다(구현리뷰 14차 필수 1).

    ## 권한을 열거하지 않는다

    초판은 `SELECT·INSERT·UPDATE·DELETE` 넷만 OR로 봤다. PostgreSQL table privilege는
    `TRUNCATE·REFERENCES·TRIGGER`도 있고 column 단위 grant도 따로 있다. 그래서
    `GRANT TRUNCATE ON checkpoints TO PUBLIC`이 남아도 통과했다(구현리뷰 4차 필수 2).
    지금은 **grantee가 PUBLIC(oid 0)인 grant가 하나라도 있으면** 걸린다.
    """

    acl = catalog["acl"]
    owners = dict(acl["owners"])
    if set(owners) != set(CHECKPOINT_TABLES):
        raise CheckpointStateError(
            "checkpoint table 집합이 계약과 다릅니다", reason_code="DRIFT"
        )
    if acl["public_grants"]:
        raise CheckpointStateError(
            "checkpoint table에 PUBLIC 권한이 남아 있습니다", reason_code="ACL_PUBLIC"
        )
    distinct = set(owners.values())
    if len(distinct) != 1:
        raise CheckpointStateError(
            "checkpoint table 소유자가 서로 다릅니다", reason_code="ACL_OWNER"
        )
    if distinct != {catalog["expected_owner"]}:
        raise CheckpointStateError(
            "checkpoint 소유자가 관리 계정과 다릅니다", reason_code="ACL_OWNER"
        )
    return owners


# ---------------------------------------------------------------------------
# app startup 분리 — 앱은 checkpoint를 초기화하지 않는다
# ---------------------------------------------------------------------------

#: production 경로가 import하면 안 되는 admin module.
ADMIN_MODULES = frozenset({"setup_checkpoint", "checkpoint_contract"})

#: `.setup()`을 부르는 것으로 간주할 수신자 이름.
#:
#: **임의 `.setup()` 전체를 막지 않는다.** 다른 라이브러리의 정상 `setup` 메서드와
#: 충돌한다. saver 계열만 본다(구현리뷰 권장 1).
SAVER_RECEIVER_HINTS = ("saver", "checkpointer", "postgressaver")


def scan_startup_violations(root: Any) -> list[str]:
    """production source에서 checkpoint 초기화 흔적을 찾는다.

    `rg` 결과만 기록하지 않고 AST로 본다. 반환값은 `파일:줄:종류` 목록이며 빈
    목록이 정상이다.

    **회귀가 이 함수를 직접 부른다** — 검사 구현의 유효성까지 증명하려면 production
    root와 임시 root에 같은 함수를 써야 한다.
    """

    import ast
    import pathlib

    violations: list[str] = []
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations += [
                    f"{path.name}:{node.lineno}:import"
                    for alias in node.names
                    if alias.name in ADMIN_MODULES
                ]
            elif isinstance(node, ast.ImportFrom) and node.module in ADMIN_MODULES:
                violations.append(f"{path.name}:{node.lineno}:import")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setup"
            ):
                receiver = node.func.value
                name = (
                    receiver.id
                    if isinstance(receiver, ast.Name)
                    else receiver.func.id
                    if isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    else ""
                )
                if any(hint in name.lower() for hint in SAVER_RECEIVER_HINTS):
                    violations.append(f"{path.name}:{node.lineno}:setup")
    return violations
