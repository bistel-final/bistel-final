"""공용 3 DB 전환 gate의 순수 계약(`V5-CM-2.6`).

DB를 열지 않는다. 얇은 read 함수가 catalog를 `TargetInventory`로 담아 오고, 판정은
전부 순수 함수가 한다. transaction·lifecycle·CLI는 `transition_public_postgres.py`가
소유한다.

상수는 전부 **Gate 0 실측**(`output/V5-CM-2.6_Gate0_조사.md`, canonical
`7f58c461…`)에서 왔고, 폐기된 `kosa_0813` 패키지를 근거로 쓰지 않는다.

소유 경계는 세 갈래다(계획 §4).

- **교체**: base 9 데이터 + `wafer` 4종 type + `v_alarm_event`
- **보존**: RAG 3종 + 현행 Runtime/D 구조 + sequence. 삭제·변경 0
- **legacy handoff**: `kosa_text2sql`의 `document_corpus` 계열. 표시만 하고 B에 넘긴다
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

#: 공용 endpoint identity guard(구현리뷰 16차 필수 1).
#:
#: approval의 `target_fingerprint_sha256_by_target`은 **DB 내용**의 신원이지 network
#: target의 신원이 아니다. 같은 dump로 세운 다른 서버에서 preflight와 approval을 함께
#: 만들면 둘을 구분할 수 없다. bootstrap은 이미
#: `POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256`으로 이 guard를 갖고 있는데, base 9를
#: DELETE하는 이 경로가 그보다 약할 수는 없다.
ALLOWED_ENDPOINT_ENV_KEY = "POSTGRES_TRANSITION_ALLOWED_HOST_SHA256"
_ENDPOINT_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def endpoint_fingerprint(host: str, port: int) -> str:
    """bootstrap `db_target.host_fingerprint()`와 **같은** canonical 형식을 쓴다.

    두 경로가 다른 형식을 쓰면 운영자가 같은 서버에 두 개의 다른 hash를 관리하게 되고,
    그 순간 한쪽은 반드시 틀린다.
    """

    return hashlib.sha256(f"{host.strip().lower()}:{int(port)}".encode()).hexdigest()


#: decimal integer만 허용한다. `int()`는 `" 5432"`·`"+5432"`·`"5_432"`도 받아
#: 두 진입점의 판정이 갈릴 수 있다(구현리뷰 17차 필수 3).
_PORT = re.compile(r"^[0-9]{1,5}$")


def parse_port(raw: Any) -> int | None:
    """`1..65535`의 decimal 문자열만 port로 인정한다. 아니면 `None`.

    backup과 transition이 **같은** parser를 쓴다. 한쪽만 typed면 같은 `.env`가 한쪽
    에서는 계약 reason으로, 다른 쪽에서는 `INTERNAL_ERROR`가 되어 복구 경로가 갈린다.
    """

    if isinstance(raw, bool) or not isinstance(raw, str | int):
        return None
    text = str(raw).strip() if isinstance(raw, str) else str(raw)
    if not _PORT.fullmatch(text):
        return None
    value = int(text)
    return value if 1 <= value <= 65535 else None


def port_rejection(raw: Any) -> tuple[str, int] | None:
    """port 형식·범위 오류를 endpoint 계약과 **같은** reason으로 돌려준다."""

    if parse_port(raw) is None:
        return ("ENDPOINT_NOT_ALLOWED", EXIT_USAGE)
    return None


def endpoint_rejection(
    host: str, port: int, *, environ: Mapping[str, str]
) -> tuple[str, int] | None:
    """허용 endpoint가 아니면 `(reason, exit)`를, 맞으면 `None`을 준다.

    호출자마다 예외 타입이 달라(`OrchestrationError`·`SessionError`) 여기서 raise하지
    않는다. **engine을 만들기 전에** 부르는 것이 계약이며, 거부는 connector 0회다.

    host·port 값은 반환하지 않는다. 운영자는 reason만 보고 `.env`를 고친다.
    """

    expected = environ.get(ALLOWED_ENDPOINT_ENV_KEY, "").strip()
    if not _ENDPOINT_SHA256.fullmatch(expected):
        # 지정 누락·형식 오류는 설정 오류다.
        return ("ENDPOINT_NOT_ALLOWED", EXIT_USAGE)
    if not hmac.compare_digest(endpoint_fingerprint(host, port), expected):
        return ("ENDPOINT_NOT_ALLOWED", EXIT_MISMATCH)
    return None


DATASET_EPOCH = "fdc_final_20260818"
ORDERED_TARGETS: tuple[str, ...] = ("kosa_agent_e2e", "kosa_agent", "kosa_text2sql")
TARGET_PROFILE: Mapping[str, str] = MappingProxyType(
    {
        "kosa_agent_e2e": "runtime",
        "kosa_agent": "runtime",
        "kosa_text2sql": "evaluation",
    }
)

BASE_TABLES: tuple[str, ...] = (
    "action_history",
    "dim_parameter",
    "evaluation",
    "fdc_trace",
    "lot_history",
    "metrology",
    "summary_alarm_history",
    "summary_data",
    "trace_alarm_history",
)

#: `wafer smallint` → `varchar(24)`. 2차 → 3차 DDL 변경은 이 4줄이 전부다.
WAFER_ALTER_TABLES: tuple[str, ...] = (
    "evaluation",
    "summary_alarm_history",
    "summary_data",
    "trace_alarm_history",
)
WAFER_COLUMN = "wafer"
LEGACY_WAFER_TYPE = "smallint"
FINAL_WAFER_TYPE = "character varying"
#: `information_schema.data_type`은 길이를 담지 않는다. DDL에는 길이가 필요하다.
FINAL_WAFER_DDL_TYPE = "varchar(24)"
FINAL_WAFER_MAX_LENGTH = 24

RAG_TABLES: tuple[str, ...] = ("document", "document_chunk")
RAG_EXTENSION = "vector"

#: B가 `V5-B-1.3`으로 **실제 적재한** target. `apply_rag_schema.py`와
#: `load_rag_documents.py`의 `ALLOWED_RAG_DATABASES`와 같아야 하고,
#: `infra/bootstrap/markers/rag_load.*.json`도 이 둘뿐이다.
#:
#: `kosa_text2sql`에도 같은 **이름의** table이 있지만 구 epoch(PR #48) 형상이라 컬럼이
#: 다르다(`token_cnt`·`metadata_json` 없음). 이름만 보고 B의 fingerprint 산식을 돌리면
#: `UndefinedColumn`으로 죽는다 — Gate 0 §2.3이 "세 DB의 RAG 형상이 서로 다르다"고
#: 적어 둔 그대로다. B marker가 있는 target에만 그 산식을 적용한다.
B_MANAGED_RAG_TARGETS: frozenset[str] = frozenset({"kosa_agent", "kosa_agent_e2e"})

#: Gate 0 실측. 전부 이 저장소의 커밋된 migration이 만든 현행 설계 구조다
#: (`001_reference_extensions.sql` PR #48 · `002_agent_runtime_clean.sql` PR #58).
PRESERVED_TABLES_BY_PROFILE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "runtime": (
            "action_delivery",
            "agent_prediction",
            "agent_prediction_review",
            "agent_run",
            "agent_run_action",
            "agent_run_alarm",
            "agent_tool_call",
            "approval_request",
            "audit_log",
            "nl_query_log",
            "r03_alarm_history",
        ),
        "evaluation": ("nl_query_log", "r03_alarm_history"),
    }
)
PRESERVED_SEQUENCES_BY_PROFILE: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "runtime": (
            "agent_prediction_review_review_id_seq",
            "audit_log_audit_id_seq",
            "nl_query_log_nl_query_log_id_seq",
        ),
        "evaluation": ("nl_query_log_nl_query_log_id_seq",),
    }
)

#: `V5-B-1.1`이 "채택하지 않는다"고 한 legacy 객체. 2.6은 보존만 하고 B가 정리한다.
LEGACY_HANDOFF_TABLES_BY_TARGET: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"kosa_text2sql": ("document", "document_chunk", "document_corpus")}
)
LEGACY_HANDOFF_INDEXES_BY_TARGET: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"kosa_text2sql": ("ux_document_corpus_active",)}
)
LEGACY_HANDOFF_LABEL = "LEGACY_HANDOFF_B"

#: base 9를 참조하는 외부 FK. profile마다 다르다 — `kosa_text2sql`에는
#: `action_history`를 참조하는 세 table 자체가 없다(5차 계획리뷰 필수 1).
#:
#: `(name, child, child cols, parent, parent cols, on delete, on update)`까지
#: exact다. table pair만 비교하면 constraint rename과 column swap을 놓친다
#: (구현리뷰 1차 필수 4). 값은 Gate 0 후속 read-only 실측이다.
ForeignKeyTuple = tuple[str, str, tuple[str, ...], str, tuple[str, ...], str, str]

_R03_FKS: tuple[ForeignKeyTuple, ...] = (
    (
        "r03_alarm_history_lot_hist_id_fkey",
        "r03_alarm_history",
        ("lot_hist_id",),
        "lot_history",
        ("lot_hist_id",),
        "a",
        "a",
    ),
    (
        "r03_alarm_history_parameter_id_fkey",
        "r03_alarm_history",
        ("parameter_id",),
        "dim_parameter",
        ("parameter_id",),
        "a",
        "a",
    ),
)
_ACTION_FKS: tuple[ForeignKeyTuple, ...] = tuple(
    (
        f"{child}_action_id_fkey",
        child,
        ("action_id",),
        "action_history",
        ("action_id",),
        "a",
        "a",
    )
    for child in ("action_delivery", "agent_run_action", "approval_request")
)
EXPECTED_EXTERNAL_FKS_BY_PROFILE: Mapping[str, tuple[ForeignKeyTuple, ...]] = (
    MappingProxyType({"runtime": (*_ACTION_FKS, *_R03_FKS), "evaluation": _R03_FKS})
)
#: NO ACTION. CASCADE면 base 9 DELETE가 보존 table로 번진다.
EXPECTED_FK_ACTION = "a"

LEGACY_VIEW = "v_alarm_event"
#: 격리 PostgreSQL **16**에 커밋된 legacy DDL을 적용한 뒤 `pg_get_viewdef(oid, true)`를
#: 읽어 공백 축약·trim 후 UTF-8 SHA-256. raw SQL 문자열 hash(`5d075192…`)가 아니다.
LEGACY_VIEW_SHA256 = "e47274e8e05fa9e0961ead517333017c8d7e501c260abf4ca4ea691ba67e030f"
LEGACY_VIEW_OWNER = "kosa"
LEGACY_VIEW_ACL = "{kosa=arwdDxt/kosa,kosa_readonly=r/kosa}"
LEGACY_VIEW_COMMENT: str | None = None
LEGACY_VIEW_GRANTS: tuple[tuple[str, str], ...] = (("SELECT", "kosa_readonly"),)

#: 호환 View는 column 이름·순서·타입을 유지한다. TRACE/SUMMARY branch에서만 두 치환을
#: 각각 정확히 2회 수행한다. R03 branch와 `r03_alarm_history`는 건드리지 않는다.
COMPAT_VIEW_SUBSTITUTIONS: tuple[tuple[str, str, int], ...] = (
    ("a.wafer AS wafer_no", "h.wafer_no AS wafer_no", 2),
    ("h.wafer_no = a.wafer", "h.wafer_id = a.wafer", 2),
)
#: 치환 결과를 격리 PostgreSQL 16에 생성한 뒤 `pg_get_viewdef(oid, true)`로 읽어 산출한
#: 값이다. legacy hash(`e47274e8…`)를 재사용하면 approval이 **실제로 만들어질 View**를
#: 증명하지 못한다(구현리뷰 2차 필수 1-1).
COMPAT_VIEW_SHA256 = "79b35b5d5a5ea1874ab7d21a79bbf04ac0282c93f840214f0196e1f24ba2a7c8"
COMPAT_VIEW_ROWS = 189  # trace 138 + summary 51

#: base 9 schema identity. vendored legacy DDL을 격리 PostgreSQL 16에 적용해 산출한
#: `base_catalog_sha256()` 값이다. final은 `wafer` 4종 ALTER 뒤 값이다.
#: 127개 column을 나열하는 대신 hash 하나로 고정한다(구현리뷰 2차 필수 2).
LEGACY_BASE_CATALOG_SHA256 = (
    "ad791b55e6c8fc6adf5aa0fdfd6c528746169e8a95f03e710fb7b0ad5d56397c"
)
FINAL_BASE_CATALOG_SHA256 = (
    "2baa36ac132a6ce55d53ec2cd3eea972c3b1eabe6c23207d18c523a367dcd3e4"
)
COMPAT_VIEW_R03_ROWS = 0

#: Gate 0이 관측한 구 epoch 값. `BASE_LEGACY_EPOCH` 판정의 기대치다.
LEGACY_ROW_COUNTS: Mapping[str, Mapping[str, int]] = MappingProxyType(
    {
        "runtime": MappingProxyType(
            {
                "trace_alarm_history": 126,
                "summary_alarm_history": 47,
                "action_history": 0,
            }
        ),
        "evaluation": MappingProxyType(
            {
                "trace_alarm_history": 126,
                "summary_alarm_history": 47,
                "action_history": 48,
            }
        ),
    }
)
FINAL_ACTION_ROWS: Mapping[str, int] = MappingProxyType(
    {"runtime": 0, "evaluation": 12}
)

#: Gate 0에서 세 target 모두 major 16이다. 다른 major는 backup client부터 없다.
POSTGRES_SUPPORTED_MAJORS: frozenset[int] = frozenset({16})

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class BaseState(StrEnum):
    BASE_LEGACY_EPOCH = "BASE_LEGACY_EPOCH"
    FINAL_ADOPTED = "FINAL_ADOPTED"
    PARTIAL_OR_DRIFT = "PARTIAL_OR_DRIFT"


class TransitionError(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# read-only inventory
# ---------------------------------------------------------------------------

RELATIONS_SQL = (
    "SELECT c.relname AS name, c.relkind AS kind "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f','i','I')"
)
EXTENSIONS_SQL = "SELECT extname FROM pg_extension"
SERVER_MAJOR_SQL = "SELECT current_setting('server_version_num') AS value"
COLUMN_TYPES_SQL = (
    "SELECT table_name, column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = 'public'"
)
#: nullability·default·길이·precision까지 담는다. `data_type`만 보면 같은 이름을 유지한
#: 채 NOT NULL·default·길이를 바꿔도 hash가 같다(구현리뷰 2차 필수 3).
COLUMN_DETAIL_SQL = (
    "SELECT table_name, column_name, data_type, udt_name, is_nullable, "
    "column_default, character_maximum_length, numeric_precision, numeric_scale "
    "FROM information_schema.columns WHERE table_schema = 'public'"
)
EXTERNAL_FK_SQL = (
    "SELECT c.conname AS name, ch.relname AS child, pa.relname AS parent, "
    "(SELECT array_agg(a.attname ORDER BY k.ord) "
    " FROM unnest(c.conkey) WITH ORDINALITY k(attnum, ord) "
    " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) "
    " AS child_columns, "
    "(SELECT array_agg(a.attname ORDER BY k.ord) "
    " FROM unnest(c.confkey) WITH ORDINALITY k(attnum, ord) "
    " JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.attnum) "
    " AS parent_columns, "
    "c.confdeltype AS delete_action, c.confupdtype AS update_action "
    "FROM pg_constraint c "
    "JOIN pg_class ch ON ch.oid = c.conrelid "
    "JOIN pg_class pa ON pa.oid = c.confrelid "
    "JOIN pg_namespace n ON n.oid = ch.relnamespace "
    "WHERE c.contype = 'f' AND n.nspname = 'public'"
)
INDEXES_SQL = (
    "SELECT c.relname AS table_name, i.relname AS index_name, "
    "pg_get_indexdef(x.indexrelid) AS definition "
    "FROM pg_index x "
    "JOIN pg_class c ON c.oid = x.indrelid "
    "JOIN pg_class i ON i.oid = x.indexrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public'"
)
CONSTRAINTS_SQL = (
    "SELECT c.relname AS table_name, con.conname AS name, con.contype AS kind, "
    "pg_get_constraintdef(con.oid, true) AS definition "
    "FROM pg_constraint con "
    "JOIN pg_class c ON c.oid = con.conrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public'"
)
VIEW_DEF_SQL = "SELECT pg_get_viewdef('public.v_alarm_event'::regclass, true) AS value"
PRIVILEGE_SQL = (
    "SELECT current_user AS role_name, "
    "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser"
)
OWNER_MATCH_SQL = (
    "SELECT count(*) AS mismatched FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v') "
    "AND pg_get_userbyid(c.relowner) <> current_user"
)
VIEW_IDENTITY_SQL = (
    "SELECT pg_get_userbyid(c.relowner) AS owner, c.relacl::text AS acl, "
    "obj_description(c.oid, 'pg_class') AS comment "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = 'v_alarm_event'"
)


@dataclass(frozen=True)
class TargetInventory:
    """preflight가 읽는 것 전부. 여기에 row content·DSN·credential은 없다."""

    database: str
    profile: str
    server_major: int
    tables: tuple[str, ...]
    views: tuple[str, ...]
    sequences: tuple[str, ...]
    other_relations: tuple[str, ...]
    extensions: tuple[str, ...]
    row_counts: Mapping[str, int] = field(repr=False, default_factory=dict)
    column_types: Mapping[str, Mapping[str, str]] = field(
        repr=False, default_factory=dict
    )
    external_fks: tuple[ForeignKeyTuple, ...] = ()
    indexes: Mapping[str, Mapping[str, str]] = field(repr=False, default_factory=dict)
    constraints: Mapping[str, tuple[tuple[str, str, str], ...]] = field(
        repr=False, default_factory=dict
    )
    column_details: Mapping[str, Mapping[str, Mapping[str, Any]]] = field(
        repr=False, default_factory=dict
    )
    rag_live_fingerprint: str | None = None
    rag_embedding_projection: str | None = None
    #: 실행 계정 실측. approval의 자기 선언을 믿지 않는다(구현리뷰 2차 필수 1-3).
    #: base 9의 table별 typed content hash. `V5-CM-2.4` 산식을 그대로 쓴다.
    base_content: Mapping[str, str] = field(repr=False, default_factory=dict)
    role_name: str | None = field(repr=False, default=None)
    is_superuser: bool | None = None
    owner_match: bool | None = None
    view_sha256: str | None = None
    view_owner: str | None = None
    view_acl: str | None = None
    view_comment: str | None = None


RAG_DOCUMENT_IDS_SQL = "SELECT doc_id FROM public.document ORDER BY doc_id"
RAG_EMBEDDING_SQL = (
    "SELECT chunk_id, embedding::text AS embedding "
    "FROM public.document_chunk ORDER BY chunk_id"
)


def base_content_hashes(connection: Any, inventory: TargetInventory) -> dict[str, str]:
    """base 9의 table별 typed content hash를 `V5-CM-2.4` 산식으로 계산한다.

    catalog와 행 수만 보면 PK를 유지한 채 값 하나를 바꾼 drift가 통과한다
    (구현리뷰 3차 필수 1). 여기서 산식을 새로 만들지 않고 2.4의
    `normalize_db_row` + `hash_canonical_rows`를 그대로 재사용한다 — 정본이 둘이 되면
    한쪽만 바뀌는 drift가 생긴다.
    """

    import manifest_v3
    import rehearsal_profile_verifier as verifier
    import value_normalization
    from psycopg import sql
    from sqlalchemy import text

    result: dict[str, str] = {}
    for name in BASE_TABLES:
        details = inventory.column_details.get(name, {})
        if not details:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
        column_types: dict[str, str] = {}
        for column, detail in details.items():
            logical = verifier.DB_TYPE_MAP.get(str(detail.get("data_type")))
            if logical is None:
                raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
            column_types[column] = logical
        columns = sorted(column_types)
        statement = sql.SQL("SELECT {columns} FROM {table}").format(
            columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            table=sql.Identifier("public", name),
        )
        rows = _rows(connection.execute(text(statement.as_string(None))))
        try:
            normalized = [
                value_normalization.normalize_db_row(row, column_types) for row in rows
            ]
        except value_normalization.ValueNormalizationError as exc:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH) from exc
        result[name] = manifest_v3.hash_canonical_rows(normalized)
    return result


def target_fingerprint(inventory: TargetInventory) -> str:
    """한 target의 **전체** identity를 한 값으로 만든다.

    `preserved_projection_sha256()`만 비교하면 다른 DB의 base content·catalog·View가
    동시에 바뀌어도 "변경 0건"을 통과한다(구현리뷰 3차 필수 1).

    relation 집합·extension·server major·실행 role까지 담는다. 이것들이 빠지면 unknown
    table 추가 같은 drift가 fingerprint를 바꾸지 않아 mutation 직전 재확인을 우회한다
    (구현리뷰 4차 필수 2).
    """

    import manifest_v3

    return manifest_v3.hash_canonical_rows(
        [
            {
                "database": inventory.database,
                "profile": inventory.profile,
                "server_major": inventory.server_major,
                "relations": _canonical(
                    {
                        "tables": sorted(inventory.tables),
                        "views": sorted(inventory.views),
                        "sequences": sorted(inventory.sequences),
                        "other": sorted(inventory.other_relations),
                    }
                ),
                "extensions": _canonical(sorted(inventory.extensions)),
                "base_catalog": base_catalog_sha256(inventory),
                "base_content": _canonical(
                    dict(sorted(inventory.base_content.items()))
                ),
                "base_rows": _canonical(
                    {name: inventory.row_counts.get(name) for name in BASE_TABLES}
                ),
                "preserved": preserved_projection_sha256(inventory),
                "external_fks": external_fk_projection_sha256(inventory),
                "view": inventory.view_sha256 or "",
                "view_owner_acl": _canonical(
                    [inventory.view_owner, inventory.view_acl, inventory.view_comment]
                ),
                "execution": _canonical(
                    [
                        inventory.role_name,
                        inventory.is_superuser,
                        inventory.owner_match,
                    ]
                ),
            }
        ]
    )


def _rag_live_fingerprint(connection: Any) -> str:
    """`V5-B-1.3`의 산식을 그대로 재사용한다.

    2.6이 별도 산식을 만들면 저장소의 `rag_load.<database>.json` marker와 대조할 수
    없다. 여기서는 public alias를 호출하기만 한다(계획 §4.2).
    """

    import load_rag_documents
    from sqlalchemy import text

    ids = [
        str(row["doc_id"])
        for row in _rows(connection.execute(text(RAG_DOCUMENT_IDS_SQL)))
    ]
    return load_rag_documents.live_fingerprint(connection, ids)


def _rag_embedding_projection(connection: Any) -> str:
    """embedding 자체의 결정론 hash. B의 fingerprint는 vector를 포함하지 않는다."""

    import manifest_v3
    from sqlalchemy import text

    rows = _rows(connection.execute(text(RAG_EMBEDDING_SQL)))
    return manifest_v3.hash_canonical_rows(
        [
            {"chunk_id": str(row["chunk_id"]), "embedding": str(row["embedding"])}
            for row in rows
        ]
    )


def normalize_view_definition(definition: str) -> str:
    """`pg_get_viewdef(oid, true)` 결과 정규화. 공백 축약 후 trim만 한다."""

    return re.sub(r"[ \t\r\n\f\v]+", " ", definition).strip()


def view_fingerprint(definition: str) -> str:
    import hashlib

    return hashlib.sha256(
        normalize_view_definition(definition).encode("utf-8")
    ).hexdigest()


def _rows(result: Any) -> list[Mapping[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


SNAPSHOT_ISOLATIONS = frozenset({"repeatable read", "serializable"})

ISOLATION_SQL = "SHOW transaction_isolation"

#: 지금 이 session이 **열린 transaction 안**인지. autocommit이면 `SHOW`가
#: repeatable read를 답해도 statement마다 별도 transaction·별도 snapshot이다.
IN_TRANSACTION_SQL = (
    "SELECT pg_current_xact_id_if_assigned() IS NOT NULL AS assigned, "
    "now() <> statement_timestamp() AS started, "
    "pg_backend_pid() AS pid"
)

#: target별 advisory lock key. database 이름에서 결정적으로 유도한다.
ADVISORY_LOCK_NAMESPACE = 0x5643_4D32  # "VCM2"

#: **session-level** mutex. transaction 밖에서 잡고 finally로 푼다.
#: `pg_advisory_xact_lock`을 transaction 안에서 쓰면 SHARE lock 뒤에 오게 되어
#: lock inversion이 생긴다(구현리뷰 7차 필수 1).
ADVISORY_LOCK_SQL = "SELECT pg_advisory_lock(:namespace, :key)"
ADVISORY_UNLOCK_SQL = "SELECT pg_advisory_unlock(:namespace, :key) AS released"

#: mutex를 잡는 동안에도 무한 대기하지 않는다.
MUTEX_TIMEOUT_SQL = "SET lock_timeout = '30s'"

#: 이 backend가 그 target의 session advisory lock을 **실제로** 들고 있는가.
#: `pg_advisory_xact_lock`은 transaction 종료와 함께 사라지므로 구분된다.
ADVISORY_HELD_SQL = (
    "SELECT count(*) AS held FROM pg_locks "
    "WHERE locktype = 'advisory' AND granted "
    "AND pid = pg_backend_pid() "
    "AND classid = :namespace AND objid = :key"
)

#: 첫 mutation 전에 fail-closed 시킨다. 무한 대기로 공용 서버를 붙잡지 않는다.
#: transaction **전체** 예산. `statement_timeout`은 statement마다 걸리므로 여러 query의
#: 합산 lock 보유 시간을 제한하지 못한다(구현리뷰 7차 권장 1). no-op은 조회를 막지
#: 않지만 `SHARE`로 공용 쓰기를 막으므로 예산을 따로 둔다.
TRANSACTION_BUDGET_SECONDS = 120.0

LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s'"
STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '600s'"
IDLE_TIMEOUT_SQL = "SET LOCAL idle_in_transaction_session_timeout = '60s'"

#: `SHOW`는 utility statement라 transaction snapshot을 잡지 않는다.
#: `SELECT current_setting(...)`을 쓰면 lock보다 먼저 snapshot이 고정된다
#: (구현리뷰 6차 필수 2).
SHOW_TIMEOUT_SQL: Mapping[str, str] = MappingProxyType(
    {
        "lock_timeout": "SHOW lock_timeout",
        "statement_timeout": "SHOW statement_timeout",
        "idle_in_transaction_session_timeout": (
            "SHOW idle_in_transaction_session_timeout"
        ),
    }
)


def advisory_lock_key(database: str) -> int:
    """database 이름 → 32bit advisory lock key.

    사람이 고른 숫자를 쓰면 두 target이 같은 key를 갖는 실수를 잡을 수 없다.
    """

    if database not in TARGET_PROFILE:
        raise TransitionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    digest = hashlib.sha256(database.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF


def require_snapshot_isolation(connection: Any) -> str:
    """이 connection이 **한 snapshot을 보는 열린 transaction** 안인지 확인한다.

    `read_inventory()`는 catalog·행 수·base 9 content hash를 여러 query로 나눠 읽는다.
    READ COMMITTED이면 query마다 snapshot이 새로 잡혀 서로 다른 시점이 한 fingerprint에
    섞인다(구현리뷰 4차 필수 2).

    isolation 문자열만 보는 것으로는 부족하다. session default가 REPEATABLE READ여도
    autocommit이면 statement마다 별도 transaction이라 같은 일이 벌어진다. transaction이
    실제로 열려 있는지 함께 본다(구현리뷰 5차 필수 3).

    `SHOW`와 `SELECT`를 나눠 쓴다. `SHOW`는 snapshot을 만들지 않으므로 lock 전에 부를 수
    있고, `SELECT`는 만들므로 lock 뒤에만 부른다(구현리뷰 6차 필수 2).
    """

    from sqlalchemy import text

    level = str(
        _rows(connection.execute(text(ISOLATION_SQL)))[0]["transaction_isolation"]
    )
    if level.strip().lower() not in SNAPSHOT_ISOLATIONS:
        raise TransitionError("SNAPSHOT_NOT_ISOLATED", EXIT_MISMATCH)
    return level


def require_open_transaction(connection: Any) -> None:
    """열린 transaction 안인지 SELECT로 확인한다. **lock을 잡은 뒤에** 부른다."""

    from sqlalchemy import text

    row = _rows(connection.execute(text(IN_TRANSACTION_SQL)))[0]
    if not bool(row["started"]):
        raise TransitionError("SNAPSHOT_NOT_ISOLATED", EXIT_MISMATCH)


def _apply_timeouts(connection: Any) -> None:
    """`SET LOCAL`을 걸고 `SHOW`로 실제 적용을 확인한다.

    autocommit이면 `SET LOCAL`이 자기 암묵 transaction과 함께 버려져 `SHOW`가 session
    기본값(`0`)을 답한다. 즉 이 확인 하나가 "timeout이 걸렸다"와 "열린 transaction
    안이다"를 동시에 증명한다 — 그리고 snapshot을 만들지 않는다.
    """

    from sqlalchemy import text

    for statement in (LOCK_TIMEOUT_SQL, STATEMENT_TIMEOUT_SQL, IDLE_TIMEOUT_SQL):
        connection.execute(text(statement))
    for name, statement in SHOW_TIMEOUT_SQL.items():
        value = str(_rows(connection.execute(text(statement)))[0][name]).strip()
        if value in {"0", ""}:
            raise TransitionError("SNAPSHOT_NOT_ISOLATED", EXIT_MISMATCH)


def _lock_statements(database: str, mode: str, names: Sequence[str]) -> list[str]:
    """고정 allowlist에서만 lock 문을 만든다. 이름은 상수에서 온다."""

    _ = database
    return [f"LOCK TABLE public.{name} IN {mode} MODE" for name in names]


def _execute_locks(connection: Any, statements: Sequence[str]) -> None:
    """lock 문을 보낸다. 없는 table은 타입 있는 실패로 정규화한다.

    존재를 미리 catalog SELECT로 확인하면 그 SELECT가 lock보다 먼저 snapshot을
    고정한다(구현리뷰 6차 필수 2). 그래서 확인 없이 보내고 경계에서 정규화한다.
    """

    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    for statement in statements:
        try:
            connection.execute(text(statement))
        except DatabaseError as exc:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH) from exc


def watched_tables(database: str) -> tuple[str, ...]:
    """전환이 건드리면 안 되는 table 전체. 이름 정렬 순서다."""

    profile = TARGET_PROFILE[database]
    return tuple(
        sorted(
            set(PRESERVED_TABLES_BY_PROFILE.get(profile, ()))
            | set(RAG_TABLES)
            | set(LEGACY_HANDOFF_TABLES_BY_TARGET.get(database, ()))
        )
    )


def acquire_target_mutex(connection: Any, *, database: str) -> None:
    """target 단위 **session** advisory lock을 transaction **밖에서** 잡는다.

    이걸 transaction 안에서 잡으면 두 실행이 동시에 들어올 때 교착한다
    (구현리뷰 7차 필수 1).

    ```
    T1: SHARE 획득 → advisory 획득 → ACCESS EXCLUSIVE 승격 대기(T2의 SHARE)
    T2: SHARE 획득 → advisory 대기(T1), SHARE는 계속 보유
    ```

    T1은 T2의 SHARE를, T2는 T1의 advisory를 기다린다. 직렬화하라고 넣은 advisory가
    오히려 inversion을 만든다.

    mutex를 먼저, 그것도 **자기 transaction 밖에서** 잡으면 두 번째 실행은 table lock을
    하나도 잡지 않은 채 기다린다. 그리고 뒤이어 여는 REPEATABLE READ transaction의
    snapshot은 table lock 뒤에 고정되므로 6차 필수 2도 계속 만족한다.

    `pg_advisory_lock`은 **session** lock이라 transaction commit 뒤에도 남는다. 그래서
    이 함수를 부른 뒤 그 session에서 새 transaction을 열어도 mutex는 유지된다.
    `pg_advisory_xact_lock`을 쓰면 그 성질이 사라진다 — 같은 session에서만 부르고,
    보유 여부는 transaction 안에서 `require_target_mutex()`가 다시 확인한다.
    """

    if database not in TARGET_PROFILE:
        raise TransitionError("TARGET_NOT_ALLOWED", EXIT_USAGE)

    from sqlalchemy import text

    connection.execute(text(MUTEX_TIMEOUT_SQL))
    try:
        connection.execute(
            text(ADVISORY_LOCK_SQL),
            {"namespace": ADVISORY_LOCK_NAMESPACE, "key": advisory_lock_key(database)},
        )
    except Exception as exc:  # lock_timeout 포함
        from sqlalchemy.exc import DatabaseError

        if isinstance(exc, DatabaseError):
            raise TransitionError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED) from exc
        raise


def release_target_mutex(connection: Any, *, database: str) -> None:
    """mutex를 푼다. 성공·실패와 무관하게 finally에서 부른다.

    **결과를 확인한다.** `pg_advisory_unlock`은 잡고 있지 않으면 `false`를 돌려줄 뿐
    예외를 내지 않는다. 그리고 SQLAlchemy connection close는 physical 종료가 아니라
    pool 반환일 수 있어, session advisory lock이 pool 안에 남을 수 있다. 그래서 해제에
    실패하면 그 connection을 **pool에 돌려보내지 않고 무효화**한다(구현리뷰 8차 필수 2).
    """

    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    try:
        released = bool(
            _rows(
                connection.execute(
                    text(ADVISORY_UNLOCK_SQL),
                    {
                        "namespace": ADVISORY_LOCK_NAMESPACE,
                        "key": advisory_lock_key(database),
                    },
                )
            )[0]["released"]
        )
    except DatabaseError as exc:
        _discard(connection)
        raise TransitionError("TARGET_MUTEX_LEAKED", EXIT_MISMATCH) from exc
    if not released:
        _discard(connection)
        raise TransitionError("TARGET_MUTEX_LEAKED", EXIT_MISMATCH)


def _discard(connection: Any) -> None:
    """정리 자체가 실패해도 원인 예외를 덮지 않는다."""

    try:
        _invalidate(connection)
    except TransitionError:
        pass


def _invalidate(connection: Any) -> None:
    """physical connection을 버린다. pool 재사용으로 lock이 새는 것을 막는다.

    `invalidate()`가 없거나 그것마저 실패하면 `close()`·`detach()`로 물러선다. 하나도
    성공하지 못하면 조용히 넘어가지 않고 알린다 — 그 connection이 pool로 돌아가면
    advisory lock이 남는다(구현리뷰 9차 필수 2).
    """

    for name in ("invalidate", "detach", "close"):
        method = getattr(connection, name, None)
        if not callable(method):
            continue
        try:
            method()
        except Exception:  # noqa: BLE001 - 다음 수단으로 넘어간다
            continue
        return
    raise TransitionError("TARGET_MUTEX_LEAKED", EXIT_MISMATCH)


def require_target_mutex(connection: Any, *, database: str) -> None:
    """지금 이 backend가 그 target의 session mutex를 들고 있는지 확인한다.

    획득은 session factory 몫이지만, 안 잡고 들어오는 배선을 여기서 막는다.
    """

    from sqlalchemy import text

    held = _rows(
        connection.execute(
            text(ADVISORY_HELD_SQL),
            {"namespace": ADVISORY_LOCK_NAMESPACE, "key": advisory_lock_key(database)},
        )
    )[0]["held"]
    if not int(held):
        raise TransitionError("TARGET_MUTEX_MISSING", EXIT_MISMATCH)


def acquire_target_locks(
    connection: Any, *, database: str, clock: BudgetClock | None = None
) -> None:
    """읽기 **전에** table lock을 잡는다. 순서를 고정한다.

    **snapshot을 만드는 statement가 lock보다 앞서면 안 된다.** REPEATABLE READ는 첫
    snapshot-bearing SELECT에서 transaction snapshot을 고정한다. lock을 기다리는 사이
    다른 transaction이 commit하면, lock을 잡고 읽어도 그 commit이 보이지 않는다 —
    "lock 안 재확인"이 아니라 "lock 이전 과거 재확인"이 된다(구현리뷰 6차 필수 2).
    그래서 여기서는 `SHOW`와 `LOCK TABLE`(둘 다 utility)만 쓴다.

    **base 9에도 `SHARE`만 건다.** `SHARE`는 DML·DDL을 막지만 조회는 막지 않는다. 이미
    final인 target을 확인만 하는데 `ACCESS EXCLUSIVE`를 걸면 공용 DB의 정상 조회까지
    막는다(구현리뷰 6차 필수 3). 전환이 실제로 필요할 때만 `escalate_base_locks()`로
    올린다.

    **target mutex는 이 함수보다 먼저**, transaction 밖에서 잡혀 있어야 한다
    (`acquire_target_mutex()`). 여기서는 보유 여부만 확인한다 — 그 확인은 SELECT라
    table lock 뒤에 온다(구현리뷰 7차 필수 1).

    1. isolation을 `SHOW`로 확인한다.
    2. timeout을 `SET LOCAL`로 걸고 `SHOW`로 적용을 확인한다. 열린 transaction 증명을
       겸한다.
    3. 보존 대상과 base 9에 `SHARE`를 **이름 정렬 순서**로 잡는다. 고정 순서가 아니면
       두 실행이 서로를 기다리는 교착이 가능하다.
    4. 그다음에야 snapshot을 만드는 확인 SELECT를 보낸다.
    """

    if database not in TARGET_PROFILE:
        raise TransitionError("TARGET_NOT_ALLOWED", EXIT_USAGE)

    require_snapshot_isolation(connection)
    _apply_timeouts(connection)
    if clock is not None:
        # 여러 `LOCK TABLE`을 순차로 보낸다. 그 합계도 예산 안이어야 한다.
        import time as _time

        clock.remaining(_time.monotonic())

    _execute_locks(
        connection, _lock_statements(database, "SHARE", watched_tables(database))
    )
    _execute_locks(connection, _lock_statements(database, "SHARE", sorted(BASE_TABLES)))

    # 여기부터 snapshot이 고정된다. 위 lock이 이미 target을 얼려 놓았다.
    require_open_transaction(connection)
    require_target_mutex(connection, database=database)


def assert_within_budget(started: float, *, now: float) -> None:
    """lock을 쥔 시간이 예산을 넘으면 그 자리에서 중단한다.

    `statement_timeout`은 statement별이라 여러 query가 이어지면 합계가 그보다 길어진다.
    공용 쓰기가 예측 가능한 시간 안에 재개되도록 transaction 전체에 상한을 둔다
    (구현리뷰 7차 권장 1).
    """

    if now - started > TRANSACTION_BUDGET_SECONDS:
        raise TransitionError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)


class BudgetClock:
    """transaction 시작을 기준으로 한 **절대 deadline**.

    phase 앞에서만 `statement_timeout`을 다시 걸면, 그 phase 안의 여러 statement가 각각
    같은 남은 시간을 새로 쓴다. 합계가 예산을 넘을 수 있다(구현리뷰 9차 권장 1).
    statement를 보내기 직전마다 이 시계로 남은 시간을 다시 계산한다.
    """

    def __init__(self, started: float, *, budget: float | None = None) -> None:
        self.started = started
        self.budget = TRANSACTION_BUDGET_SECONDS if budget is None else budget

    def remaining(self, now: float) -> float:
        left = self.budget - (now - self.started)
        if left <= 0:
            raise TransitionError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
        return left

    def apply(self, connection: Any, *, now: float) -> float:
        from sqlalchemy import text

        left = self.remaining(now)
        connection.execute(
            text(f"SET LOCAL statement_timeout = '{int(left * 1000)}ms'")
        )
        return left


def apply_remaining_budget(connection: Any, started: float, *, now: float) -> float:
    """남은 예산으로 `statement_timeout`을 **줄인다**.

    사후 검사만으로는 lock 보유 상한이 되지 않는다. `statement_timeout`이 600초면 단일
    statement가 5분 넘게 lock을 쥔 뒤에야 "예산 초과"로 rollback한다 — commit은 막지만
    공용 쓰기 차단 시간은 못 막는다(구현리뷰 8차 권장 1).

    그래서 blocking 단계마다 남은 예산을 계산해 그만큼으로 다시 건다. 남은 예산이 없으면
    statement를 보내기 전에 멈춘다.
    """

    from sqlalchemy import text

    remaining = TRANSACTION_BUDGET_SECONDS - (now - started)
    if remaining <= 0:
        raise TransitionError("TARGET_BUSY", EXIT_CONFIRM_REQUIRED)
    connection.execute(
        text(f"SET LOCAL statement_timeout = '{int(remaining * 1000)}ms'")
    )
    return remaining


def escalate_base_locks(connection: Any, *, database: str) -> None:
    """전환을 실제로 수행하기 직전에만 base 9를 `ACCESS EXCLUSIVE`로 올린다.

    이미 `SHARE`를 들고 있어 그 사이 어떤 DML·DDL도 commit되지 않았다. 따라서 승격이
    상태를 바꿀 여지를 열지 않는다(구현리뷰 6차 필수 3).
    """

    if database not in TARGET_PROFILE:
        raise TransitionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    _execute_locks(
        connection,
        _lock_statements(database, "ACCESS EXCLUSIVE", sorted(BASE_TABLES)),
    )


def read_inventory(
    connection: Any,
    *,
    database: str,
    profile: str,
    with_content: bool = True,
    require_snapshot: bool = False,
    clock: BudgetClock | None = None,
) -> TargetInventory:
    """read-only 수집. 이 함수는 어떤 mutation도 보내지 않는다.

    `with_content=False`는 content hash를 건너뛴다. 24,845행을 읽는 비용 때문에
    catalog만 필요한 호출에서만 쓴다 — 승인·전환 경로는 항상 True다.

    `require_snapshot=True`는 수집 전체가 한 snapshot에서 이뤄지는지 먼저 확인한다.
    전환 경로의 모든 수집은 이 값을 켠다.
    """

    from sqlalchemy import text

    if require_snapshot:
        require_snapshot_isolation(connection)
    if clock is not None:
        # 수집은 여러 query로 나뉜다. 각 query가 같은 남은 시간을 새로 쓰지 않도록
        # 절대 deadline으로 다시 건다(구현리뷰 9차 권장 1).
        import time as _time

        clock.apply(connection, now=_time.monotonic())
    major = int(_rows(connection.execute(text(SERVER_MAJOR_SQL)))[0]["value"]) // 10000
    relations = _rows(connection.execute(text(RELATIONS_SQL)))
    kinds: dict[str, list[str]] = {}
    for row in relations:
        kinds.setdefault(str(row["kind"]), []).append(str(row["name"]))
    tables = tuple(sorted(kinds.get("r", []) + kinds.get("p", [])))
    views = tuple(sorted(kinds.get("v", []) + kinds.get("m", [])))
    sequences = tuple(sorted(kinds.get("S", [])))
    other = tuple(
        sorted(
            name
            for kind, names in kinds.items()
            if kind not in {"r", "p", "v", "m", "S", "i", "I"}
            for name in names
        )
    )
    extensions = tuple(
        sorted(
            str(r["extname"]) for r in _rows(connection.execute(text(EXTENSIONS_SQL)))
        )
    )

    catalog: dict[str, dict[str, str]] = {}
    for row in _rows(connection.execute(text(COLUMN_TYPES_SQL))):
        catalog.setdefault(str(row["table_name"]), {})[str(row["column_name"])] = str(
            row["data_type"]
        )

    counted = set(BASE_TABLES) | set(RAG_TABLES)
    counted |= set(PRESERVED_TABLES_BY_PROFILE.get(profile, ()))
    counted |= set(LEGACY_HANDOFF_TABLES_BY_TARGET.get(database, ()))
    counts: dict[str, int] = {}
    for name in sorted(counted & set(tables)):
        statement = f'SELECT count(*) AS value FROM public."{name}"'
        counts[name] = int(_rows(connection.execute(text(statement)))[0]["value"])

    base = set(BASE_TABLES)
    fks = tuple(
        sorted(
            (
                str(r["name"]),
                str(r["child"]),
                tuple(str(c) for c in (r["child_columns"] or ())),
                str(r["parent"]),
                tuple(str(c) for c in (r["parent_columns"] or ())),
                str(r["delete_action"]),
                str(r["update_action"]),
            )
            for r in _rows(connection.execute(text(EXTERNAL_FK_SQL)))
            if str(r["parent"]) in base and str(r["child"]) not in base
        )
    )

    privilege = _rows(connection.execute(text(PRIVILEGE_SQL)))[0]
    role_name = str(privilege["role_name"])
    is_superuser = bool(privilege["is_superuser"])
    owner_match = (
        int(_rows(connection.execute(text(OWNER_MATCH_SQL)))[0]["mismatched"]) == 0
    )

    rag_live = rag_embedding = None
    if database in B_MANAGED_RAG_TARGETS and set(RAG_TABLES) <= set(tables):
        # B가 적재한 형상에서만 B의 산식을 쓴다. 다른 형상에 돌리면 죽는다.
        rag_live = _rag_live_fingerprint(connection)
        rag_embedding = _rag_embedding_projection(connection)

    indexes: dict[str, dict[str, str]] = {}
    for row in _rows(connection.execute(text(INDEXES_SQL))):
        indexes.setdefault(str(row["table_name"]), {})[str(row["index_name"])] = str(
            row["definition"]
        )
    constraints: dict[str, list[tuple[str, str, str]]] = {}
    for row in _rows(connection.execute(text(CONSTRAINTS_SQL))):
        constraints.setdefault(str(row["table_name"]), []).append(
            (str(row["name"]), str(row["kind"]), str(row["definition"]))
        )

    details: dict[str, dict[str, dict[str, Any]]] = {}
    for row in _rows(connection.execute(text(COLUMN_DETAIL_SQL))):
        details.setdefault(str(row["table_name"]), {})[str(row["column_name"])] = {
            "data_type": str(row["data_type"]),
            "udt_name": str(row["udt_name"]),
            "is_nullable": str(row["is_nullable"]),
            "column_default": (
                None if row["column_default"] is None else str(row["column_default"])
            ),
            "character_maximum_length": row["character_maximum_length"],
            "numeric_precision": row["numeric_precision"],
            "numeric_scale": row["numeric_scale"],
        }

    definition = owner = acl = comment = None
    if LEGACY_VIEW in views:
        definition = str(_rows(connection.execute(text(VIEW_DEF_SQL)))[0]["value"])
        identity = _rows(connection.execute(text(VIEW_IDENTITY_SQL)))[0]
        owner = str(identity["owner"])
        acl = None if identity["acl"] is None else str(identity["acl"])
        comment = None if identity["comment"] is None else str(identity["comment"])

    inventory = TargetInventory(
        database=database,
        profile=profile,
        server_major=major,
        tables=tables,
        views=views,
        sequences=sequences,
        other_relations=other,
        extensions=extensions,
        row_counts=MappingProxyType(counts),
        column_types=MappingProxyType(
            {name: MappingProxyType(cols) for name, cols in catalog.items()}
        ),
        external_fks=fks,
        indexes=MappingProxyType(
            {name: MappingProxyType(value) for name, value in indexes.items()}
        ),
        constraints=MappingProxyType(
            {name: tuple(sorted(value)) for name, value in constraints.items()}
        ),
        column_details=MappingProxyType(
            {
                name: MappingProxyType(
                    {c: MappingProxyType(v) for c, v in columns.items()}
                )
                for name, columns in details.items()
            }
        ),
        rag_live_fingerprint=rag_live,
        rag_embedding_projection=rag_embedding,
        role_name=role_name,
        is_superuser=is_superuser,
        owner_match=owner_match,
        view_sha256=None if definition is None else view_fingerprint(definition),
        view_owner=owner,
        view_acl=acl,
        view_comment=comment,
    )
    if with_content and set(BASE_TABLES) <= set(tables):
        inventory = TargetInventory(
            **{
                **inventory.__dict__,
                "base_content": MappingProxyType(
                    base_content_hashes(connection, inventory)
                ),
            }
        )
    return inventory


# ---------------------------------------------------------------------------
# 순수 판정
# ---------------------------------------------------------------------------


def expected_relations(inventory: TargetInventory) -> tuple[frozenset[str], ...]:
    """이 target에서 존재가 허용된 table·sequence 집합."""

    profile = inventory.profile
    tables = (
        set(BASE_TABLES)
        | set(PRESERVED_TABLES_BY_PROFILE.get(profile, ()))
        | set(LEGACY_HANDOFF_TABLES_BY_TARGET.get(inventory.database, ()))
    )
    if inventory.database in B_MANAGED_RAG_TARGETS:
        tables |= set(RAG_TABLES)
    sequences = set(PRESERVED_SEQUENCES_BY_PROFILE.get(profile, ()))
    return frozenset(tables), frozenset(sequences)


def check_relation_set(inventory: TargetInventory) -> None:
    """table·sequence·view 집합이 Gate 0 형상과 exact인지 본다.

    `base 9 + RAG 외 relation 0`이 아니다. 현행 설계 구조 13종은 **보존 대상**이며
    없는 것도 drift다(4차 계획리뷰 필수 1).
    """

    tables, sequences = expected_relations(inventory)
    if set(inventory.tables) != tables:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if set(inventory.sequences) != sequences:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if set(inventory.views) != {LEGACY_VIEW}:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.other_relations:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def check_rag_presence(inventory: TargetInventory) -> None:
    """RAG 형상을 본다. **B가 적재한 target과 아닌 target의 기준이 다르다.**

    `vector` extension은 세 DB 모두에 있어야 한다(Gate 0 §2.3 실측).

    B 관리 target(`B_MANAGED_RAG_TARGETS`)은 RAG 2종이 **전체 존재하고 행이 있어야**
    한다. 부분 상태는 drift다(계획 §4.2).

    `kosa_text2sql`의 같은 이름 table은 구 epoch 잔재이며 B가 "지워도 된다"고 한
    대상이다(2026-08-22 확인). 2.6은 **지우지 않고 legacy handoff로 보존만** 하고,
    전체 존재도 행 수도 요구하지 않는다 — B가 먼저 정리해도 2.6이 drift로 오판하지
    않게 한다. 실제 정리는 B 소유다.
    """

    if RAG_EXTENSION not in inventory.extensions:
        raise TransitionError("RAG_PRESERVATION_FAILED", EXIT_MISMATCH)
    if inventory.database not in B_MANAGED_RAG_TARGETS:
        return
    if not set(RAG_TABLES) <= set(inventory.tables):
        raise TransitionError("RAG_PRESERVATION_FAILED", EXIT_MISMATCH)
    for name in RAG_TABLES:
        if inventory.row_counts.get(name, 0) <= 0:
            raise TransitionError("RAG_PRESERVATION_FAILED", EXIT_MISMATCH)


def check_external_fks(inventory: TargetInventory) -> None:
    """profile별 expected FK 집합과 exact이고 `ON DELETE NO ACTION`인지 본다.

    CASCADE가 하나라도 있으면 base 9 `DELETE`가 보존 table로 번진다.
    """

    expected = set(EXPECTED_EXTERNAL_FKS_BY_PROFILE.get(inventory.profile, ()))
    # constraint 이름·child/parent column·update/delete action까지 포함한 exact 비교다.
    if set(inventory.external_fks) != expected:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def check_external_fk_children_empty(inventory: TargetInventory) -> None:
    """FK child가 한 행이라도 있으면 자동 삭제하지 않고 중단한다."""

    for _name, child, _cc, _parent, _pc, _delete, _update in inventory.external_fks:
        if inventory.row_counts.get(child, 0) != 0:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def check_execution_privilege(inventory: TargetInventory) -> None:
    """실행 계정을 **실측**으로 확인한다.

    approval의 `execution_privilege`·`owner_match`는 자기 선언이라 그것만으로는
    일반 사용자도 통과한다(구현리뷰 2차 필수 1-3).
    """

    if inventory.is_superuser is not True:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.owner_match is not True:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_owner != LEGACY_VIEW_OWNER:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def check_legacy_view_identity(inventory: TargetInventory) -> None:
    """정의 hash·owner·ACL·comment가 pin 값과 exact인지 본다."""

    if inventory.view_sha256 != LEGACY_VIEW_SHA256:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_owner != LEGACY_VIEW_OWNER:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_acl != LEGACY_VIEW_ACL:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_comment != LEGACY_VIEW_COMMENT:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def check_compatibility_view_identity(inventory: TargetInventory) -> None:
    """전환 **후** View는 compat 정의여야 한다. owner·ACL·comment는 그대로 보존된다."""

    if inventory.view_sha256 != COMPAT_VIEW_SHA256:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_owner != LEGACY_VIEW_OWNER:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_acl != LEGACY_VIEW_ACL:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if inventory.view_comment != LEGACY_VIEW_COMMENT:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def _wafer_types(inventory: TargetInventory) -> set[str]:
    return {
        inventory.column_types.get(name, {}).get(WAFER_COLUMN, "")
        for name in WAFER_ALTER_TABLES
    }


def base_catalog_projection(inventory: TargetInventory) -> dict[str, Any]:
    """base 9의 **schema identity**만 담는다. 행 수는 따로 본다.

    `wafer` 네 개와 row count 세 개만 보면 비-wafer column type, PK/index, 나머지 6
    table의 행 수가 달라져도 허용 상태로 오판한다(구현리뷰 2차 필수 2).

    127개 column을 상수로 나열하는 대신 legacy View hash와 **같은 방식**으로 hash 하나를
    pin한다 — vendored legacy DDL을 격리 PostgreSQL 16에 적용해 독립 산출한다.
    """

    result: dict[str, Any] = {}
    for name in BASE_TABLES:
        columns = inventory.column_details.get(name, {})
        result[name] = {
            "columns": {
                column: dict(sorted(detail.items()))
                for column, detail in sorted(columns.items())
            },
            "constraints": [list(item) for item in inventory.constraints.get(name, ())],
            "indexes": dict(sorted(inventory.indexes.get(name, {}).items())),
        }
    return result


def base_column_shape_sha256(inventory: TargetInventory) -> str:
    """base 9의 **column shape**만 담은 hash.

    복원본 검증에는 이걸 쓴다. `base_catalog_sha256()`는 constraint 정의까지 담는데,
    base 9만 dump하면 범위 밖 table을 참조하는 FK는 복원될 수 없다. 그 값을 그대로
    비교하면 정상 backup도 영원히 `RESTORE_NOT_VERIFIED`가 된다
    (구현리뷰 13차 필수 1 작업 중 발견).

    행 수·content hash는 별도로 비교하므로 복원 충실도 검증은 약해지지 않는다.
    """

    import manifest_v3

    shapes = {
        table: entry["columns"]
        for table, entry in base_catalog_projection(inventory).items()
    }
    return manifest_v3.hash_canonical_rows([{"columns": _canonical(shapes)}])


def base_catalog_sha256(inventory: TargetInventory) -> str:
    import manifest_v3

    return manifest_v3.hash_canonical_rows(
        [{"base": _canonical(base_catalog_projection(inventory))}]
    )


def check_base_row_counts(
    inventory: TargetInventory, expected: Mapping[str, int]
) -> None:
    """**9 table 전부** 행 수를 본다. 세 개만 보면 나머지 drift를 놓친다."""

    for name in BASE_TABLES:
        if inventory.row_counts.get(name) != expected.get(name):
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


#: Gate 0이 관측한 구 epoch 전체 행 수. 두 profile은 `action_history`만 다르다.
LEGACY_BASE_ROWS: Mapping[str, Mapping[str, int]] = MappingProxyType(
    {
        profile: MappingProxyType(
            {
                "action_history": action,
                "dim_parameter": 8,
                "evaluation": 4800,
                "fdc_trace": 14400,
                "lot_history": 600,
                "metrology": 48,
                "summary_alarm_history": 47,
                "summary_data": 4800,
                "trace_alarm_history": 126,
            }
        )
        for profile, action in (("runtime", 0), ("evaluation", 48))
    }
)


def final_base_rows(profile: str) -> dict[str, int]:
    """최종 epoch 기대 행 수. manifest v4 + profile projection이 정본이다."""

    import json

    import rebuild_runner

    manifest = json.loads(
        rebuild_runner.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    rows = {name: int(e["row_count"]) for name, e in manifest["tables"].items()}
    rows["action_history"] = FINAL_ACTION_ROWS[profile]
    return rows


def classify_base(inventory: TargetInventory) -> BaseState:
    """base 9의 상태만 판정한다. 나머지 projection 검사는 호출자가 따로 한다.

    `BASE_FRESH`·`RUNTIME_REFERENCE_ACTION`은 Gate 0 실측 이후 지원하지 않는다.
    """

    if not set(BASE_TABLES) <= set(inventory.tables):
        return BaseState.PARTIAL_OR_DRIFT

    wafer = _wafer_types(inventory)
    if wafer == {LEGACY_WAFER_TYPE}:
        expected = LEGACY_BASE_ROWS.get(inventory.profile, {})
    elif wafer == {FINAL_WAFER_TYPE}:
        expected = final_base_rows(inventory.profile)
    else:
        return BaseState.PARTIAL_OR_DRIFT

    try:
        check_base_row_counts(inventory, expected)
        _check_base_catalog(inventory, wafer)
        if wafer == {FINAL_WAFER_TYPE}:
            _check_final_base_content(inventory)
        else:
            _require_base_content(inventory)
    except TransitionError:
        return BaseState.PARTIAL_OR_DRIFT
    return (
        BaseState.BASE_LEGACY_EPOCH
        if wafer == {LEGACY_WAFER_TYPE}
        else BaseState.FINAL_ADOPTED
    )


def _require_base_content(inventory: TargetInventory) -> None:
    """legacy 상태에서도 content hash를 **반드시 계산**해야 한다.

    값이 없으면 승인·불변 검사가 빈 값을 동결하게 되어 drift를 못 잡는다.
    legacy 기대값 자체는 Gate 0 시점 실측이므로 preflight가 approval에 동결한다.
    """

    if set(inventory.base_content) != set(BASE_TABLES):
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if any(not SHA256_HEX.fullmatch(v) for v in inventory.base_content.values()):
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def _check_final_base_content(inventory: TargetInventory) -> None:
    """final 상태는 manifest v4의 typed content hash와 exact여야 한다.

    행 수·catalog가 같아도 값이 다르면 `FINAL_ADOPTED`가 아니다.
    """

    _require_base_content(inventory)
    for name, expected in final_base_content(inventory.profile).items():
        if inventory.base_content.get(name) != expected:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def final_base_content(profile: str) -> dict[str, str]:
    """최종 epoch의 table별 typed content hash. manifest v4가 정본이다.

    runtime의 `action_history`는 0행이므로 empty canonical hash다.
    """

    import json

    import rebuild_runner
    import rehearsal_profile_verifier as verifier

    manifest = json.loads(
        rebuild_runner.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    result = {
        name: str(entry["content_hash"]) for name, entry in manifest["tables"].items()
    }
    if FINAL_ACTION_ROWS[profile] == 0:
        result["action_history"] = verifier.EMPTY_ROWS_HASH
    return result


def _check_base_catalog(inventory: TargetInventory, wafer: set[str]) -> None:
    """base 9 schema identity를 pinned hash와 대조한다.

    legacy와 final은 `wafer` 네 개만 다르므로 상태별 상수 두 개를 둔다.
    """

    expected = (
        FINAL_BASE_CATALOG_SHA256
        if wafer == {FINAL_WAFER_TYPE}
        else LEGACY_BASE_CATALOG_SHA256
    )
    if expected and base_catalog_sha256(inventory) != expected:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)


def _check_common_preconditions(inventory: TargetInventory) -> None:
    """base 상태와 무관한 precondition. View만 상태별로 다르다."""

    if inventory.database not in TARGET_PROFILE:
        raise TransitionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    if TARGET_PROFILE[inventory.database] != inventory.profile:
        raise TransitionError("PROFILE_MISMATCH", EXIT_MISMATCH)
    if inventory.server_major not in POSTGRES_SUPPORTED_MAJORS:
        raise TransitionError("BACKUP_CLIENT_UNAVAILABLE", EXIT_CONFIRM_REQUIRED)
    check_relation_set(inventory)
    check_rag_presence(inventory)
    check_external_fks(inventory)
    check_external_fk_children_empty(inventory)
    check_execution_privilege(inventory)


def classify_target(inventory: TargetInventory) -> BaseState:
    """전체 precondition을 통과한 뒤에만 base 상태를 확정한다.

    **View 기대값은 base 상태를 따라간다.** legacy base면 legacy View, final base면
    compat View다. 상태와 무관하게 legacy View를 요구하면 이미 전환된 target을
    `TARGET_STATE_UNSUPPORTED`로 거부해, 계획 §5.1·§10.2가 허용한 `FINAL_ADOPTED`
    no-op과 **부분 성공 후 재실행이 불가능해진다**(구현리뷰 5차 필수 2).
    """

    _check_common_preconditions(inventory)

    state = classify_base(inventory)
    if state is BaseState.PARTIAL_OR_DRIFT:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if state is BaseState.FINAL_ADOPTED:
        check_compatibility_view_identity(inventory)
    else:
        check_legacy_view_identity(inventory)
    return state


def classify_post_state(inventory: TargetInventory) -> BaseState:
    """전환 직후 상태를 판정한다. `FINAL_ADOPTED`가 아니면 전환이 끝나지 않았다.

    `classify_target()`이 이미 상태별 View를 요구하므로 여기서는 결과가 final인지만
    더 본다(구현리뷰 4차 필수 1·5차 필수 2).
    """

    state = classify_target(inventory)
    if state is not BaseState.FINAL_ADOPTED:
        raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    return state


#: View를 DROP하면 object ACL이 사라진다. 재생성 뒤 계획 §8.1대로 명시 복원한다.
#: 서버의 `ALTER DEFAULT PRIVILEGES`에 우연히 기대면 결과가 계획으로 결정되지 않는다
#: (구현리뷰 12차 필수 4).
VIEW_OWNER_SQL_TEMPLATE = "ALTER VIEW public.{view} OWNER TO {owner}"
VIEW_GRANT_SQL_TEMPLATE = "GRANT SELECT ON public.{view} TO {role}"
VIEW_REVOKE_SQL = "REVOKE ALL ON public.{view} FROM PUBLIC"
READONLY_ROLE = "kosa_readonly"


def compatibility_view_acl_statements() -> list[str]:
    """호환 View 재생성 뒤 owner·ACL을 계획대로 되돌리는 문장.

    `LEGACY_VIEW_ACL`이 `{kosa=arwdDxt/kosa,kosa_readonly=r/kosa}`이므로 owner는 `kosa`,
    `kosa_readonly`에 SELECT뿐이다. PUBLIC 권한은 명시적으로 회수한다.
    """

    return [
        VIEW_REVOKE_SQL.format(view=LEGACY_VIEW),
        VIEW_OWNER_SQL_TEMPLATE.format(view=LEGACY_VIEW, owner=LEGACY_VIEW_OWNER),
        VIEW_GRANT_SQL_TEMPLATE.format(view=LEGACY_VIEW, role=READONLY_ROLE),
    ]


#: 전환 DDL. `v_alarm_event`가 `wafer`를 참조하므로 View를 먼저 지워야 ALTER가 통과한다
#: (Gate 0에서 격리 PostgreSQL 16으로 실증). 순서를 바꾸면 첫 ALTER에서 실패한다.
DROP_VIEW_SQL = f"DROP VIEW public.{LEGACY_VIEW}"

ALTER_WAFER_SQL_TEMPLATE = (
    "ALTER TABLE public.{table} "
    "ALTER COLUMN {column} TYPE {type} USING {column}::{type}"
)


def alter_wafer_statements() -> list[str]:
    """`wafer smallint → varchar(24)`. 이름 정렬 순서다.

    **빈 table에만 보낸다.** 계획 §8은 `DELETE` 뒤에 ALTER하도록 고정했다. 채워진
    table을 먼저 ALTER하면 곧 지울 14,400행을 rewrite하며 WAL과 `ACCESS EXCLUSIVE`
    보유 시간을 늘린다(구현리뷰 11차 필수 2).
    """

    return [
        ALTER_WAFER_SQL_TEMPLATE.format(
            table=table, column=WAFER_COLUMN, type=FINAL_WAFER_DDL_TYPE
        )
        for table in sorted(WAFER_ALTER_TABLES)
    ]


def transition_statements(legacy_definition: str) -> list[str]:
    """DDL만 담은 **schema-only** 순서. 데이터 교체가 없는 격리 확인용이다.

    실제 전환은 `transition_sessions.make_transition_handler()`가 계획 §8 순서
    (`DROP → DELETE → ALTER → COPY → CREATE`)로 조립한다.
    """

    return [
        DROP_VIEW_SQL,
        *alter_wafer_statements(),
        build_compatibility_view_sql(legacy_definition),
    ]


def build_compatibility_view_sql(legacy_definition: str) -> str:
    """legacy 정의에서 두 곳만 치환한 2.6 호환 View SQL을 만든다.

    치환 횟수가 각각 정확히 2가 아니면 만들지 않는다. legacy 정의가 pin 값과 다르면
    이 함수에 오기 전에 이미 중단된다.
    """

    body = legacy_definition
    for old, new, count in COMPAT_VIEW_SUBSTITUTIONS:
        if body.count(old) != count:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
        body = body.replace(old, new)
    for old, _new, _count in COMPAT_VIEW_SUBSTITUTIONS:
        if old in body:
            raise TransitionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    return f"CREATE VIEW public.{LEGACY_VIEW} AS {body.rstrip().rstrip(';')}"


# ---------------------------------------------------------------------------
# approval · receipt · marker
# ---------------------------------------------------------------------------

APPROVAL_ARTIFACT_TYPE = "postgres_transition_approval"
APPROVAL_STATUS = "APPROVED"
APPROVAL_KEYS = frozenset(
    {
        "artifact_type",
        "dataset_epoch",
        "change_ref",
        "status",
        "ordered_targets",
        "preflight_bundle_sha256",
        "source_manifest_sha256",
        "gate0_inventory_sha256",
        "server_major_by_target",
        "planned_outcome_by_target",
        "preserved_projection_sha256_by_target",
        "external_fk_projection_sha256_by_target",
        "target_fingerprint_sha256_by_target",
        "compatibility_view_sha256",
        "compatibility_view_owner_acl_sha256",
        "execution_privilege",
        "owner_match",
        "approved_at",
    }
)
EXECUTION_PRIVILEGE = "SUPERUSER"
CHANGE_REF = re.compile(r"^GH-\d+$")
PLANNED_OUTCOMES = frozenset({BaseState.BASE_LEGACY_EPOCH, BaseState.FINAL_ADOPTED})

#: approval에서 **현재 상태와 대조해야 하는** 키. 호출자가 전부 독립 산출해 넘긴다.
APPROVAL_EXPECTED_KEYS = frozenset(
    {
        "change_ref",
        "gate0_inventory_sha256",
        "source_manifest_sha256",
        "preflight_bundle_sha256",
        "compatibility_view_sha256",
        "compatibility_view_owner_acl_sha256",
        "server_major_by_target",
        "planned_outcome_by_target",
        "preserved_projection_sha256_by_target",
        "external_fk_projection_sha256_by_target",
        "target_fingerprint_sha256_by_target",
    }
)

#: Gate 0 조사 보고서의 canonical hash. approval이 "그때 측정한 그 상태"에 묶인다.
GATE0_INVENTORY_SHA256 = (
    "7f58c4618fd241865e4e9d7b2934d663a6f2768649a2f8ee180e63fdb9030b66"
)

RECEIPT_ARTIFACT_TYPE = "postgres_transition_receipt"
RECEIPT_KEYS = frozenset(
    {
        "artifact_type",
        "dataset_epoch",
        "database",
        "profile",
        "change_ref",
        "preflight_bundle_sha256",
        "table_allowlist",
        "archive_sha256",
        "client_major",
        "server_major",
        "restore_verified",
        # 계획 §7.1·§8.1·§13.4의 보호 증적(구현리뷰 3차 필수 2).
        "target_fingerprint_sha256",
        "compatibility_view_sha256",
        "compatibility_view_owner_acl_sha256",
        "backup_image_digest",
        "backup_tool_version",
        "execution_role",
        "execution_is_superuser",
        "execution_owner_match",
        # View 정의·owner·ACL·comment sidecar 증적(구현리뷰 4차 필수 3).
        "view_sidecar_name",
        "view_sidecar_sha256",
    }
)
IMAGE_DIGEST = re.compile(r"^postgres@sha256:[0-9a-f]{64}$")

MARKER_ARTIFACT_TYPE = "postgres_profile_marker"
MARKER_NAME_TEMPLATE = "postgres_profile.{database}.json"


def view_sidecar_name(database: str, change_ref: str) -> str:
    """View 정의·owner·ACL·comment를 담은 증적 파일 이름.

    archive와 같은 규칙으로 target·change-ref에 묶어 다른 Task의 파일 재사용을 막는다.
    """

    return f"{DATASET_EPOCH}.{database}.{change_ref}.view.json"


#: 상태 중립 이름. `base_state`로 legacy·final을 구분하므로 legacy 전용 이름은 더 이상
#: 내용과 맞지 않는다(구현리뷰 7차 편집 1).
SIDECAR_ARTIFACT_TYPE = "postgres_view_sidecar"

SIDECAR_KEYS = frozenset(
    {
        "artifact_type",
        "dataset_epoch",
        "database",
        "profile",
        "change_ref",
        "preflight_bundle_sha256",
        "view_name",
        "view_definition_sha256",
        "view_owner",
        "view_acl",
        "view_comment",
        # 이 증적이 legacy 전환용인지 final no-op용인지(구현리뷰 6차 필수 1).
        "base_state",
    }
)

#: base 상태별로 sidecar가 담아야 할 View 정의 hash.
SIDECAR_VIEW_BY_STATE: Mapping[str, str] = MappingProxyType(
    {
        BaseState.BASE_LEGACY_EPOCH.value: LEGACY_VIEW_SHA256,
        BaseState.FINAL_ADOPTED.value: COMPAT_VIEW_SHA256,
    }
)


def validate_sidecar_schema(payload: Any) -> None:
    """sidecar가 **정확히** 이 schema인지 본다. 연결 전에 형식만 판정한다."""

    if not isinstance(payload, Mapping) or set(payload) != SIDECAR_KEYS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != SIDECAR_ARTIFACT_TYPE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["dataset_epoch"] != DATASET_EPOCH:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["database"] not in TARGET_PROFILE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["profile"] != TARGET_PROFILE[payload["database"]]:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["view_name"] != LEGACY_VIEW:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["base_state"] not in SIDECAR_VIEW_BY_STATE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in ("view_definition_sha256", "preflight_bundle_sha256"):
        value = payload[key]
        if not isinstance(value, str) or not SHA256_HEX.match(value):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in ("change_ref", "view_owner", "view_acl"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["view_comment"] is not None and not isinstance(
        payload["view_comment"], str
    ):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)


def assert_sidecar_matches(
    payload: Mapping[str, Any],
    *,
    database: str,
    profile: str,
    state: BaseState,
    view_sha256: str | None,
    view_owner: str | None,
    view_acl: str | None,
    view_comment: str | None,
    change_ref: str,
    preflight_bundle_sha256: str,
) -> None:
    """sidecar **내용**이 그 target의 그 시점 View인지 대조한다.

    파일 digest만 보면 "그 파일이 실물이다"만 증명된다. 임의 바이트를 쓰고 그 hash를
    receipt에 적으면 그대로 "View 복구 근거"가 됐다(구현리뷰 5차 필수 4). 복구에 쓸 값
    자체를 대조 근거·Gate 0 pin과 비교한다.

    **상태별로 기대값이 다르다.** legacy target은 legacy View를, 이미 전환된 target은
    compat View를 담는다. legacy 전용으로 고정하면 final target에는 유효한 sidecar를
    만들 수 없어 재실행이 `BACKUP_INVALID`로 막힌다(구현리뷰 6차 필수 1).

    대조 근거는 **명시 인자**로 받는다. 재실행에서 이미 전환된 target은 현재 inventory가
    아니라 marker가 기록한 전환 전 상태가 근거다(구현리뷰 7차 필수 2).
    """

    if payload["database"] != database:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["profile"] != profile:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["change_ref"] != change_ref:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["preflight_bundle_sha256"] != preflight_bundle_sha256:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    # 선언한 base 상태가 대조 근거의 상태와 같아야 한다. legacy 증적을 final target에,
    # final 증적을 legacy target에 돌려 쓸 수 없다(구현리뷰 6차 필수 1).
    if payload["base_state"] != state.value:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["view_owner"] != LEGACY_VIEW_OWNER:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["view_acl"] != LEGACY_VIEW_ACL:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["view_comment"] != LEGACY_VIEW_COMMENT:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    # 그리고 그 시점 target이 실제로 그 상태여야 한다. View 정의 hash를
    # `SIDECAR_VIEW_BY_STATE`로 또 보는 것은 이 대조에 포섭되는 죽은 방어다.
    if view_sha256 != payload["view_definition_sha256"]:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if view_owner != payload["view_owner"]:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if view_acl != payload["view_acl"]:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if view_comment != payload["view_comment"]:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)


def build_sidecar(
    inventory: TargetInventory,
    *,
    state: BaseState,
    change_ref: str,
    preflight_bundle_sha256: str,
) -> dict[str, Any]:
    """현재 View identity를 담은 sidecar 내용을 만든다(구현리뷰 8차 필수 3).

    지금까지는 validator만 있고 producer가 없어 테스트 fixture가 그 자리를 대신했다.
    """

    return {
        "artifact_type": SIDECAR_ARTIFACT_TYPE,
        "dataset_epoch": DATASET_EPOCH,
        "database": inventory.database,
        "profile": inventory.profile,
        "change_ref": change_ref,
        "preflight_bundle_sha256": preflight_bundle_sha256,
        "base_state": state.value,
        "view_name": LEGACY_VIEW,
        "view_definition_sha256": inventory.view_sha256,
        "view_owner": inventory.view_owner,
        "view_acl": inventory.view_acl,
        "view_comment": inventory.view_comment,
    }


def build_receipt(
    inventory: TargetInventory,
    *,
    change_ref: str,
    preflight_bundle_sha256: str,
    archive_sha256: str,
    view_sidecar_sha256: str,
    restore_verified: bool,
    backup_image_digest: str,
    backup_tool_version: str,
) -> dict[str, Any]:
    """backup·독립 restore 결과를 담은 receipt 내용을 만든다(구현리뷰 8차 필수 3)."""

    return {
        "artifact_type": RECEIPT_ARTIFACT_TYPE,
        "dataset_epoch": DATASET_EPOCH,
        "database": inventory.database,
        "profile": inventory.profile,
        "change_ref": change_ref,
        "preflight_bundle_sha256": preflight_bundle_sha256,
        "table_allowlist": list(BASE_TABLES),
        "archive_sha256": archive_sha256,
        "client_major": inventory.server_major,
        "server_major": inventory.server_major,
        "restore_verified": restore_verified,
        "target_fingerprint_sha256": target_fingerprint(inventory),
        "compatibility_view_sha256": COMPAT_VIEW_SHA256,
        "compatibility_view_owner_acl_sha256": compatibility_view_owner_acl_sha256(),
        "backup_image_digest": backup_image_digest,
        "backup_tool_version": backup_tool_version,
        "execution_role": inventory.role_name,
        "execution_is_superuser": inventory.is_superuser,
        "execution_owner_match": inventory.owner_match,
        "view_sidecar_name": view_sidecar_name(inventory.database, change_ref),
        "view_sidecar_sha256": view_sidecar_sha256,
    }


def marker_name(database: str) -> str:
    if database not in TARGET_PROFILE:
        raise TransitionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    return MARKER_NAME_TEMPLATE.format(database=database)


MARKER_KEYS = frozenset(
    {
        "artifact_type",
        "dataset_epoch",
        "database",
        "profile",
        "change_ref",
        "preflight_bundle_sha256",
        "base_state",
        "target_fingerprint_sha256",
        "compatibility_view_sha256",
        "archive_sha256",
        "view_sidecar_sha256",
        "recorded_at",
        # 전환 **전** preflight report 항목. 재실행이 bundle을 독립 산출하는 근거다
        # (구현리뷰 7차 필수 2).
        "preflight_target_entry",
        # mutation **직전**에 잰 backup root 신뢰 값. closure가 사후 실측과 대조해
        # "실행 중에는 열려 있다가 나중에 좁혀진" root를 잡는다(구현리뷰 17차 필수 2).
        "backup_root_trust",
        # 그 mutation에 **실제로 쓴** approval 파일의 exact hash. 이게 없으면 전환 뒤
        # approval을 바꿔도 closure가 알지 못한다(구현리뷰 18차 필수 1).
        "approval_sha256",
    }
)

PREFLIGHT_ENTRY_KEYS = frozenset(
    {
        "profile",
        "server_major",
        "state",
        "preserved_projection_sha256",
        "external_fk_projection_sha256",
        "target_fingerprint_sha256",
        "legacy_view_sha256",
        "action_history_rows",
    }
)


def build_marker(
    inventory: TargetInventory,
    *,
    state: BaseState,
    change_ref: str,
    preflight_bundle_sha256: str,
    archive_sha256: str,
    view_sidecar_sha256: str,
    recorded_at: str,
    preflight_target_entry: Mapping[str, Any],
    backup_root_trust: str,
    approval_sha256: str,
) -> dict[str, Any]:
    """전환이 끝난 target의 COMMITTED marker 내용.

    commit·full verify가 모두 끝난 뒤 **마지막에** 기록한다(계획 §10 marker-last).
    """

    return {
        "artifact_type": MARKER_ARTIFACT_TYPE,
        "dataset_epoch": DATASET_EPOCH,
        "database": inventory.database,
        "profile": inventory.profile,
        "change_ref": change_ref,
        "preflight_bundle_sha256": preflight_bundle_sha256,
        "base_state": state.value,
        "target_fingerprint_sha256": target_fingerprint(inventory),
        "compatibility_view_sha256": COMPAT_VIEW_SHA256,
        "archive_sha256": archive_sha256,
        "view_sidecar_sha256": view_sidecar_sha256,
        "recorded_at": recorded_at,
        "preflight_target_entry": dict(preflight_target_entry),
        "backup_root_trust": backup_root_trust,
        "approval_sha256": approval_sha256,
    }


def validate_marker_schema(payload: Any) -> None:
    """marker가 **정확히** 이 schema인지 본다."""

    if not isinstance(payload, Mapping) or set(payload) != MARKER_KEYS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != MARKER_ARTIFACT_TYPE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["dataset_epoch"] != DATASET_EPOCH:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["database"] not in TARGET_PROFILE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["profile"] != TARGET_PROFILE[payload["database"]]:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["base_state"] not in SIDECAR_VIEW_BY_STATE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in (
        "preflight_bundle_sha256",
        "target_fingerprint_sha256",
        "compatibility_view_sha256",
        "archive_sha256",
        "view_sidecar_sha256",
    ):
        if not _hex(payload[key]):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if not isinstance(payload["change_ref"], str) or not payload["change_ref"]:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    entry = payload["preflight_target_entry"]
    if not isinstance(entry, Mapping) or set(entry) != PREFLIGHT_ENTRY_KEYS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    # 이 approval은 legacy → final 전환을 승인했다. 따라서 marker가 기록하는 **전환 전**
    # 상태는 언제나 legacy다. final이 적혀 있으면 그 marker로 bundle을 다시 만들 수 없다
    # (구현리뷰 9차 필수 1).
    if entry["state"] != BaseState.BASE_LEGACY_EPOCH.value:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if entry["legacy_view_sha256"] != LEGACY_VIEW_SHA256:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    trust = payload["backup_root_trust"]
    if trust != WINDOWS_ACL_SENTINEL and not _BACKUP_ROOT_MODE.fullmatch(str(trust)):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if not _hex(payload["approval_sha256"]):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    # 고정 의미 필드는 64-hex인지가 아니라 **정본 상수와 같은지**를 본다. hex 형식만
    # 보면 임의 hash로 바꿔도 통과한다(구현리뷰 18차 필수 1).
    if payload["compatibility_view_sha256"] != COMPAT_VIEW_SHA256:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if entry["server_major"] not in POSTGRES_SUPPORTED_MAJORS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if entry["profile"] != payload["profile"]:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    _require_offset_timestamp(payload["recorded_at"])


def assert_marker_matches(
    payload: Mapping[str, Any],
    *,
    inventory: TargetInventory,
    change_ref: str,
    preflight_bundle_sha256: str,
    backup_root_trust: str,
    approval_sha256: str,
) -> None:
    """기존 COMMITTED marker가 지금 그 target을 가리키는지 본다.

    이미 final인 target을 재실행할 때 marker를 새로 쓰지 않고 **읽어서 검증**한다
    (구현리뷰 7차 필수 2).

    **root 신뢰와 approval 신원도 여기서 본다.** 이전 실행이 `0777` root에 남긴 marker를
    완료로 인정하면, 뒤 target을 mutation한 다음 closure에서야 불일치가 드러난다. 안전
    gate는 남은 mutation **전에** 닫혀야 한다(구현리뷰 18차 필수 2).
    """

    if payload["database"] != inventory.database:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["profile"] != inventory.profile:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["change_ref"] != change_ref:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["preflight_bundle_sha256"] != preflight_bundle_sha256:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["base_state"] != BaseState.FINAL_ADOPTED.value:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["target_fingerprint_sha256"] != target_fingerprint(inventory):
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    assert_marker_identity(
        payload,
        backup_root_trust=backup_root_trust,
        approval_sha256=approval_sha256,
    )


def assert_marker_identity(
    payload: Mapping[str, Any], *, backup_root_trust: str, approval_sha256: str
) -> None:
    """marker가 **이번 실행과 같은** root 신뢰·approval을 가리키는지 본다.

    fresh·resume·closure 세 경로가 이 함수 하나를 쓴다. 경로마다 따로 쓰면 한 곳이
    빠져도 다른 곳이 통과시킨다(구현리뷰 18차 필수 2).
    """

    if payload["backup_root_trust"] != backup_root_trust:
        raise TransitionError("BACKUP_ROOT_UNTRUSTED", EXIT_MISMATCH)
    if payload["approval_sha256"] != approval_sha256:
        raise TransitionError("APPROVAL_MISMATCH", EXIT_MISMATCH)


def _hex(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_HEX.fullmatch(value))


def _require_offset_timestamp(value: Any) -> None:
    """timezone offset을 포함한 ISO-8601인지 본다.

    non-empty만 보면 `not-a-date`가 통과한다(구현리뷰 2차 필수 1-3).
    """

    from datetime import datetime

    if not isinstance(value, str):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)


def validate_approval_schema(payload: Any) -> None:
    """승인 artifact의 **구조**만 본다. DB에 연결하기 전에 수행한다.

    내용 대조는 현재 inventory가 있어야 하므로 `assert_approval_matches()`가 read-only
    preflight 뒤에 한다. 형식이 깨진 approval은 연결 0건으로 끝나야 하므로 두 단계로
    나눈다(계획 §6 · 구현리뷰 1차 필수 3).

    구조 오류는 `ARTIFACT_INVALID`(2)다.
    """

    if not isinstance(payload, Mapping) or set(payload) != APPROVAL_KEYS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != APPROVAL_ARTIFACT_TYPE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["status"] != APPROVAL_STATUS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    change_ref = payload["change_ref"]
    if not isinstance(change_ref, str) or not CHANGE_REF.fullmatch(change_ref):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in (
        "preflight_bundle_sha256",
        "source_manifest_sha256",
        "gate0_inventory_sha256",
        "compatibility_view_sha256",
        "compatibility_view_owner_acl_sha256",
    ):
        if not _hex(payload[key]):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    _require_offset_timestamp(payload["approved_at"])

    targets = payload["ordered_targets"]
    if not isinstance(targets, list) or tuple(targets) != ORDERED_TARGETS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in (
        "server_major_by_target",
        "planned_outcome_by_target",
        "preserved_projection_sha256_by_target",
        "external_fk_projection_sha256_by_target",
        "target_fingerprint_sha256_by_target",
    ):
        mapping = payload[key]
        if not isinstance(mapping, Mapping) or set(mapping) != set(ORDERED_TARGETS):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for value in payload["server_major_by_target"].values():
        if not isinstance(value, int) or isinstance(value, bool):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for value in payload["planned_outcome_by_target"].values():
        if value not in {state.value for state in PLANNED_OUTCOMES}:
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in (
        "preserved_projection_sha256_by_target",
        "external_fk_projection_sha256_by_target",
        "target_fingerprint_sha256_by_target",
    ):
        for value in payload[key].values():
            if not _hex(value):
                raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["execution_privilege"] != EXECUTION_PRIVILEGE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["owner_match"] is not True:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)

    if payload["dataset_epoch"] != DATASET_EPOCH:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)


def assert_approval_matches(
    payload: Mapping[str, Any], expected: Mapping[str, Any]
) -> None:
    """형식이 옳은 approval을 **현재 상태**와 대조한다.

    `expected`는 호출자가 pinned 상수와 현재 inventory에서 **독립 산출**한 값이어야
    한다. approval 자신의 값을 되돌려주면 형식만 맞는 임의 hex가 통과한다
    (구현리뷰 1차 필수 3).
    """

    if APPROVAL_EXPECTED_KEYS - set(expected):
        raise TransitionError("APPROVAL_MISMATCH", EXIT_MISMATCH)
    for key in sorted(APPROVAL_EXPECTED_KEYS):
        if payload[key] != expected[key]:
            raise TransitionError("APPROVAL_MISMATCH", EXIT_MISMATCH)


def validate_receipt(payload: Any) -> None:
    """backup·restore receipt의 exact schema. 형식 오류는 `ARTIFACT_INVALID`(2)다.

    receipt에는 경로·DSN·계정을 넣지 않는다. 여기서 검증하는 것도 그 밖의 값뿐이다.
    """

    if not isinstance(payload, Mapping) or set(payload) != RECEIPT_KEYS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != RECEIPT_ARTIFACT_TYPE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["dataset_epoch"] != DATASET_EPOCH:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["database"] not in TARGET_PROFILE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["profile"] != TARGET_PROFILE[payload["database"]]:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    change_ref = payload["change_ref"]
    if not isinstance(change_ref, str) or not CHANGE_REF.fullmatch(change_ref):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in ("preflight_bundle_sha256", "archive_sha256"):
        if not _hex(payload[key]):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    allowlist = payload["table_allowlist"]
    if not isinstance(allowlist, list) or tuple(allowlist) != BASE_TABLES:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in ("client_major", "server_major"):
        value = payload[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in (
        "target_fingerprint_sha256",
        "compatibility_view_sha256",
        "compatibility_view_owner_acl_sha256",
    ):
        if not _hex(payload[key]):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    image = payload["backup_image_digest"]
    if not isinstance(image, str) or not IMAGE_DIGEST.fullmatch(image):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    tool = payload["backup_tool_version"]
    if not isinstance(tool, str) or not tool:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    role = payload["execution_role"]
    if not isinstance(role, str) or not IDENTIFIER.fullmatch(role):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["execution_is_superuser"] is not True:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["execution_owner_match"] is not True:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["restore_verified"] is not True:
        # restore를 확인하지 못한 receipt로는 공용 apply를 시작하지 않는다.
        raise TransitionError("RESTORE_NOT_VERIFIED", EXIT_MISMATCH)


def archive_name(database: str, change_ref: str) -> str:
    """archive 파일 이름을 database·change-ref에서 결정한다.

    receipt가 임의 경로를 가리키면 아무 hash나 적어도 통과한다. 이름을 계약으로
    고정하면 receipt와 실물이 1:1로 묶인다(구현리뷰 2차 필수 1-2).
    """

    if database not in TARGET_PROFILE or not CHANGE_REF.fullmatch(change_ref):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    return f"{DATASET_EPOCH}.{database}.{change_ref}.dump"


def assert_receipt_matches(
    payload: Mapping[str, Any],
    *,
    inventory: TargetInventory,
    change_ref: str,
    preflight_bundle_sha256: str,
    archive_sha256: str | None = None,
    view_sidecar_sha256: str | None = None,
    expected_fingerprint: str | None = None,
) -> None:
    """형식이 옳은 receipt를 현재 target·승인과 대조한다.

    다른 Task·다른 preflight의 receipt 재사용을 여기서 막는다.
    """

    if payload["database"] != inventory.database:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["profile"] != inventory.profile:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["change_ref"] != change_ref:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["preflight_bundle_sha256"] != preflight_bundle_sha256:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["server_major"] != inventory.server_major:
        raise TransitionError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)
    if payload["client_major"] != inventory.server_major:
        raise TransitionError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)
    if archive_sha256 is None:
        # 실물 archive를 확인하지 못하면 receipt를 신뢰하지 않는다.
        raise TransitionError("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED)
    if payload["archive_sha256"] != archive_sha256:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    # 재실행에서는 이미 전환된 target의 현재 fingerprint가 receipt와 다르다. 그때는
    # marker가 기록한 **전환 전** 값이 대조 근거다(구현리뷰 7차 필수 2).
    if payload["target_fingerprint_sha256"] != (
        target_fingerprint(inventory)
        if expected_fingerprint is None
        else expected_fingerprint
    ):
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["compatibility_view_sha256"] != COMPAT_VIEW_SHA256:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["compatibility_view_owner_acl_sha256"] != (
        compatibility_view_owner_acl_sha256()
    ):
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["execution_role"] != inventory.role_name:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["execution_is_superuser"] != inventory.is_superuser:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["execution_owner_match"] != inventory.owner_match:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)

    import postgres_backup

    # image digest·tool version을 형식만 보면 임의 값이 통과한다. digest로 고정한
    # 값 자체와 대조한다(구현리뷰 4차 필수 3).
    if payload["backup_image_digest"] != postgres_backup.expected_client_image(
        inventory.server_major
    ):
        raise TransitionError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)
    if payload["backup_tool_version"] != postgres_backup.expected_client_version(
        inventory.server_major
    ):
        raise TransitionError("BACKUP_CLIENT_VERSION_MISMATCH", EXIT_MISMATCH)
    if payload["view_sidecar_name"] != view_sidecar_name(
        inventory.database, change_ref
    ):
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)
    if view_sidecar_sha256 is None:
        # 실물 sidecar를 확인하지 못하면 receipt를 신뢰하지 않는다.
        raise TransitionError("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED)
    if payload["view_sidecar_sha256"] != view_sidecar_sha256:
        raise TransitionError("BACKUP_INVALID", EXIT_MISMATCH)


def preserved_projection(inventory: TargetInventory) -> dict[str, Any]:
    """보존 대상의 **catalog identity + 행 수 + RAG fingerprint**.

    행 수만 담으면 같은 행 수의 내용 변화나 column·constraint·index 변화를 잡지 못한다
    (구현리뷰 1차 필수 5). 그래서 세 층을 함께 담는다.

    1. table별 column/type, constraint(이름·종류), index(이름·정의)
    2. table별 행 수와 sequence 목록
    3. RAG는 B-1.3 live fingerprint + 2.6 embedding projection hash

    일반 보존 table에 **content hash는 요구하지 않는다.** 다른 영역의 미확정 data
    schema를 2.6이 재직렬화하지 않는다는 계획 §4.2 결정은 유지한다 — catalog가
    바뀌거나 행 수가 바뀌면 잡히고, 그 안의 값 변화는 소유 영역의 책임이다.
    """

    profile = inventory.profile
    database = inventory.database
    tables = tuple(PRESERVED_TABLES_BY_PROFILE.get(profile, ()))
    handoff_tables = tuple(LEGACY_HANDOFF_TABLES_BY_TARGET.get(database, ()))
    handoff_indexes = tuple(LEGACY_HANDOFF_INDEXES_BY_TARGET.get(database, ()))
    watched = tuple(sorted({*tables, *handoff_tables, *RAG_TABLES}))

    def catalog(name: str) -> dict[str, Any]:
        columns = inventory.column_details.get(name)
        if columns:
            rendered = {
                column: dict(sorted(detail.items()))
                for column, detail in sorted(columns.items())
            }
        else:
            # detail이 없으면 최소한 type이라도 담는다.
            rendered = {
                column: {"data_type": value}
                for column, value in sorted(
                    inventory.column_types.get(name, {}).items()
                )
            }
        return {
            "columns": rendered,
            "constraints": [list(item) for item in inventory.constraints.get(name, ())],
            "indexes": dict(sorted(inventory.indexes.get(name, {}).items())),
            "row_count": inventory.row_counts.get(name, 0),
        }

    all_indexes = {
        index: definition
        for table in inventory.indexes.values()
        for index, definition in table.items()
    }
    return {
        "preserved": {
            name: catalog(name) for name in watched if name not in RAG_TABLES
        },
        "preserved_sequences": sorted(PRESERVED_SEQUENCES_BY_PROFILE.get(profile, ())),
        "rag": {name: catalog(name) for name in RAG_TABLES},
        "rag_extension": RAG_EXTENSION in inventory.extensions,
        "rag_live_fingerprint": inventory.rag_live_fingerprint,
        "rag_embedding_projection": inventory.rag_embedding_projection,
        LEGACY_HANDOFF_LABEL: {
            "tables": {name: catalog(name) for name in handoff_tables},
            # 선언만 하고 쓰지 않으면 index가 사라져도 통과한다.
            "indexes": {name: all_indexes.get(name) for name in handoff_indexes},
        },
    }


def _canonical(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def external_fk_projection(inventory: TargetInventory) -> list[list[Any]]:
    """FK identity의 정렬된 직렬 형태. approval에 hash로 실린다."""

    return [
        [name, child, list(cc), parent, list(pc), delete, update]
        for name, child, cc, parent, pc, delete, update in sorted(
            inventory.external_fks
        )
    ]


def external_fk_projection_sha256(inventory: TargetInventory) -> str:
    import manifest_v3

    return manifest_v3.hash_canonical_rows(
        [{"external_fks": _canonical(external_fk_projection(inventory))}]
    )


def compatibility_view_owner_acl_sha256() -> str:
    """호환 View가 복원해야 할 owner·ACL·comment identity의 고정 hash."""

    import manifest_v3

    return manifest_v3.hash_canonical_rows(
        [
            {
                "owner": LEGACY_VIEW_OWNER,
                "acl": LEGACY_VIEW_ACL,
                "comment": "" if LEGACY_VIEW_COMMENT is None else LEGACY_VIEW_COMMENT,
            }
        ]
    )


def preserved_projection_sha256(inventory: TargetInventory) -> str:
    import manifest_v3

    # nested mapping은 `_canonical`로 한 번 직렬화해 넣는다. `hash_canonical_rows`는
    # top-level만 NFC 정규화하므로 중첩 구조를 문자열로 고정하는 편이 안전하다.
    return manifest_v3.hash_canonical_rows(
        [{"preserved": _canonical(preserved_projection(inventory))}]
    )


def assert_preserved_unchanged(before: TargetInventory, after: TargetInventory) -> None:
    """전후 보존 projection이 한 바이트도 다르지 않아야 한다."""

    if preserved_projection_sha256(before) != preserved_projection_sha256(after):
        raise TransitionError("RAG_PRESERVATION_FAILED", EXIT_MISMATCH)


def assert_target_invariants_held(
    before: TargetInventory, after: TargetInventory
) -> None:
    """전환이 **건드리면 안 되는 것**이 그대로인지 transaction 안에서 확인한다.

    `classify_post_state()`는 "final base와 compat View가 있다"만 본다. handler가 그
    과정에 `nl_query_log` 한 행을 더하거나 RAG embedding을 바꿔도 통과한다 — 통과했다
    (구현리뷰 5차 필수 1).

    **비교 범위는 `preserved_projection()`이 담는 만큼이다.** 일반 보존 table은 계획
    §4.2 결정대로 catalog·행 수·sequence만 보므로, 같은 행 수를 유지한 값 변경은 여기서
    잡히지 않는다. RAG만 live fingerprint와 embedding projection으로 값까지 본다
    (구현리뷰 6차 편집 1). 그 밖에 외부 FK·실행 주체·extension·relation 집합은 전후가
    정확히 같아야 한다.

    commit 전에 부르는 것이 핵심이다. 갱신된 값을 기준선으로 채택해 버리면 다음
    iteration의 비대상 검사도 함께 통과한다.
    """

    if before.database != after.database or before.profile != after.profile:
        raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)

    # 보존 대상 catalog·행 수·sequence·RAG projection
    assert_preserved_unchanged(before, after)

    # 외부 FK identity와 child 행 수
    if external_fk_projection_sha256(before) != external_fk_projection_sha256(after):
        raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)

    # 실행 주체와 서버
    if _canonical(
        [before.role_name, before.is_superuser, before.owner_match, before.server_major]
    ) != _canonical(
        [after.role_name, after.is_superuser, after.owner_match, after.server_major]
    ):
        raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)

    # extension 집합
    if sorted(before.extensions) != sorted(after.extensions):
        raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)

    # relation 집합은 View 하나도 늘거나 줄지 않는다.
    for label in ("tables", "views", "sequences", "other_relations"):
        if sorted(getattr(before, label)) != sorted(getattr(after, label)):
            raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)


def assert_recoverable_without_marker(
    inventory: TargetInventory,
    *,
    preserved_projection_sha256: str,
    external_fk_projection_sha256: str,
) -> None:
    """marker 없이 final인 target을 복구해도 되는지 판정한다.

    `commit 성공 → marker write 전 중단`이면 DB는 final인데 증적이 없다. 그 상태를 그냥
    받아들이면 아무 final target이나 완료로 인정하는 셈이다(구현리뷰 8차 필수 1).

    근거는 **전환이 건드리지 않는 것**이다. `assert_target_invariants_held()`가 보장하듯
    보존 projection과 외부 FK identity는 전환 전후로 한 바이트도 달라지지 않는다. 따라서
    approval이 전환 **전**에 기록한 두 값이 지금도 같다면, 이 target은 그 approval이
    승인한 바로 그 target이 전환을 마친 상태다.

    호출자는 이 함수 전에 `classify_target()`으로 `FINAL_ADOPTED`를 확인해야 한다.
    """

    if preserved_projection_sha256_of(inventory) != preserved_projection_sha256:
        raise TransitionError("APPROVAL_MISMATCH", EXIT_MISMATCH)
    if external_fk_projection_sha256_of(inventory) != external_fk_projection_sha256:
        raise TransitionError("APPROVAL_MISMATCH", EXIT_MISMATCH)


def preserved_projection_sha256_of(inventory: TargetInventory) -> str:
    return preserved_projection_sha256(inventory)


def external_fk_projection_sha256_of(inventory: TargetInventory) -> str:
    return external_fk_projection_sha256(inventory)


def assert_target_untouched(before: TargetInventory, after: TargetInventory) -> None:
    """이미 final인 target을 재실행할 때는 **아무것도** 달라지면 안 된다."""

    if target_fingerprint(before) != target_fingerprint(after):
        raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)


def assert_other_targets_unchanged(
    before: Mapping[str, str], after: Mapping[str, str]
) -> None:
    """비대상 DB fingerprint가 직전 값과 같아야 한다(계획 §5.3)."""

    if set(before) != set(after):
        raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)
    for database, value in before.items():
        if after[database] != value:
            raise TransitionError("OTHER_TARGET_CHANGED", EXIT_MISMATCH)


# ---------------------------------------------------------------------------
# closure evidence — 사후 증적(구현리뷰 16차 필수 3)
# ---------------------------------------------------------------------------

#: 사전 approval과 **분리된** 사후 계약이다.
#:
#: 계획 §16.3은 archive·marker hash와 실행 시각·role을 "approval artifact에 함께"
#: 남긴다고 했는데, approval은 전환 **전에** 확정되는 exact 18-key 입력이고 그 값들은
#: 전환 **뒤에** 생긴다. 넣으면 `validate_approval_schema()`가 extra key로 거부하고,
#: 기존 approval을 사후 수정하면 승인 자체의 의미가 깨진다. 그래서 별도 artifact다.
#:
#: - 생성자: `transition_public_postgres --closure`
#: - 생성 시점: 세 target의 COMMITTED marker가 모두 있는 뒤
#: - hash 대상: approval·archive·sidecar·receipt·completion·COMMITTED marker 실물
#: - 저장 위치: backup root(저장소 밖)
#: - consumer: 팀 change record 첨부와 closure gate
#: - 재실행: 같은 내용이면 no-op
#: - secret: host·port·user·password·경로 원문을 담지 않는다
CLOSURE_ARTIFACT_TYPE = "postgres_transition_closure_evidence"
CLOSURE_NAME_TEMPLATE = ".{epoch}.{change_ref}.closure.json"

CLOSURE_KEYS = frozenset(
    {
        "artifact_type",
        "dataset_epoch",
        "change_ref",
        "ordered_targets",
        "approval_sha256",
        "archive_sha256_by_target",
        "view_sidecar_sha256_by_target",
        "receipt_sha256_by_target",
        "completion_sha256_by_target",
        "committed_marker_sha256_by_target",
        "backup_root_mode",
        "operator_os_user",
        "recorded_at",
    }
)

_CLOSURE_DIGEST_KEYS = (
    "archive_sha256_by_target",
    "view_sidecar_sha256_by_target",
    "receipt_sha256_by_target",
    "completion_sha256_by_target",
    "committed_marker_sha256_by_target",
)


def closure_name(change_ref: str) -> str:
    return CLOSURE_NAME_TEMPLATE.format(epoch=DATASET_EPOCH, change_ref=change_ref)


def build_closure_evidence(
    *,
    change_ref: str,
    approval_sha256: str,
    digests_by_target: Mapping[str, Mapping[str, str]],
    backup_root_mode: str,
    operator_os_user: str,
    recorded_at: str,
) -> dict[str, Any]:
    """세 target 전환이 끝난 뒤의 외부 무결성 기록.

    저장소 밖 증적이 사후에 바뀌지 않았음을 팀이 확인할 수 있게 한다.
    `digests_by_target`은 target별 `{artifact: sha256}`이며 호출자가 **실물에서** 낸다.
    """

    payload: dict[str, Any] = {
        "artifact_type": CLOSURE_ARTIFACT_TYPE,
        "dataset_epoch": DATASET_EPOCH,
        "change_ref": change_ref,
        "ordered_targets": list(ORDERED_TARGETS),
        "approval_sha256": approval_sha256,
        "backup_root_mode": backup_root_mode,
        # receipt의 `execution_role`은 PostgreSQL `current_user`다. 같은 이름이 두 뜻을
        # 가지면 대조가 틀린다(구현리뷰 17차 권장 2). 여기는 OS 운영자다.
        "operator_os_user": operator_os_user,
        "recorded_at": recorded_at,
    }
    for key, artifact in zip(
        _CLOSURE_DIGEST_KEYS,
        ("archive", "view_sidecar", "receipt", "completion", "committed_marker"),
        strict=True,
    ):
        payload[key] = {
            database: digests_by_target[database][artifact]
            for database in ORDERED_TARGETS
        }
    return payload


def validate_closure_schema(payload: Any) -> None:
    """closure evidence가 **정확히** 이 schema인지 본다."""

    if not isinstance(payload, Mapping) or set(payload) != CLOSURE_KEYS:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["artifact_type"] != CLOSURE_ARTIFACT_TYPE:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if payload["dataset_epoch"] != DATASET_EPOCH:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    change_ref = payload["change_ref"]
    if not isinstance(change_ref, str) or not CHANGE_REF.fullmatch(change_ref):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if (
        not isinstance(payload["ordered_targets"], list)
        or tuple(payload["ordered_targets"]) != ORDERED_TARGETS
    ):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    if not _hex(payload["approval_sha256"]):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    for key in _CLOSURE_DIGEST_KEYS:
        mapping = payload[key]
        if not isinstance(mapping, Mapping) or set(mapping) != set(ORDERED_TARGETS):
            raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
        for value in mapping.values():
            if not _hex(value):
                raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    # POSIX mode는 `0700` 같은 4자리 8진수 문자열이다. Windows는 ACL 확인을 runbook에
    # 분리하므로 `WINDOWS_ACL_REVIEWED`를 쓴다(구현리뷰 16차 권장 1).
    mode = payload["backup_root_mode"]
    if mode != WINDOWS_ACL_SENTINEL and not _BACKUP_ROOT_MODE.fullmatch(str(mode)):
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    operator = payload["operator_os_user"]
    if not isinstance(operator, str) or not operator:
        raise TransitionError("ARTIFACT_INVALID", EXIT_USAGE)
    _require_offset_timestamp(payload["recorded_at"])


WINDOWS_ACL_SENTINEL = "WINDOWS_ACL_REVIEWED"
_BACKUP_ROOT_MODE = re.compile(r"^0[0-7]{3}$")
