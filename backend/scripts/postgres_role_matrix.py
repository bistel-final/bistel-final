"""PostgreSQL 최소권한 role matrix의 불변 계약 (`V5-CM-3.5`).

이 모듈은 SQL을 실행하지 않는다. 최종 profile manifest와 이미 병합된 migration 계약을
role/profile/stage별 exact allow matrix로 접고 canonical digest를 만든다.

중요한 경계는 다음과 같다.

* LLM 생성 SQL은 ``kosa_readonly``만 실행한다.
* Runtime application은 ``kosa_app``만 사용한다.
* 합성 label은 ``kosa_evaluation``만 읽는다.
* query logger는 Evaluation ``nl_query_log`` append에 필요한 최소 권한만 가진다.
* n8n은 signed HTTP callback을 사용하므로 DB delivery role은 NOLOGIN·deny-all이다.
* ``PUBLIC``의 database/schema/relation/sequence data-access privilege는 0이다.

extension function/type/operator ACL은 이 계약의 관리 대상이 아니다. pgvector 검색을
깨뜨리지 않기 위해 relation/sequence surface만 관리한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "manifests"

TASK_ID: Final = "V5-CM-3.5"
DATASET_EPOCH: Final = "fdc_final_20260818"


class ContractError(RuntimeError):
    """role matrix 입력이 allowlist 밖이거나 정본과 충돌한다."""

    def __init__(self, message: str, *, reason_code: str = "CONTRACT_INVALID") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.exit_code = 2


class Profile(StrEnum):
    RUNTIME = "runtime"
    EVALUATION = "evaluation"


class MatrixStage(StrEnum):
    CORE = "role_core"
    CHECKPOINTED = "role_checkpointed"


class SchemaStage(StrEnum):
    RUNTIME_GUARDED = "runtime_guarded"
    RUNTIME_CHECKPOINTED = "runtime_checkpointed"
    EVALUATION_REFERENCE = "evaluation_reference"


class ManagedRole(StrEnum):
    APP = "kosa_app"
    READONLY = "kosa_readonly"
    EVALUATION = "kosa_evaluation"
    LOGGER = "kosa_query_logger"
    DELIVERY = "kosa_n8n_delivery"


DATABASE_PROFILES: Final[Mapping[str, Profile]] = MappingProxyType(
    {
        "kosa_agent": Profile.RUNTIME,
        "kosa_agent_e2e": Profile.RUNTIME,
        "kosa_text2sql": Profile.EVALUATION,
    }
)
MANAGED_ROLES: Final = tuple(ManagedRole)
LOGIN_ROLES: Final = frozenset(
    {
        ManagedRole.APP,
        ManagedRole.READONLY,
        ManagedRole.EVALUATION,
        ManagedRole.LOGGER,
    }
)

DATABASE_PRIVILEGES: Final = ("CONNECT", "TEMPORARY")
SCHEMA_PRIVILEGES: Final = ("USAGE", "CREATE")
RELATION_PRIVILEGES: Final = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
COLUMN_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SEQUENCE_PRIVILEGES: Final = ("USAGE", "SELECT", "UPDATE")

BASE_READ: Final = frozenset(
    {
        "dim_parameter",
        "fdc_trace",
        "summary_data",
        "evaluation",
        "trace_alarm_history",
        "summary_alarm_history",
    }
)
REFERENCE_READ: Final = frozenset({"r03_alarm_history", "v_alarm_event"})
RAG_TABLES: Final = frozenset({"document", "document_chunk"})
RUNTIME_TABLES: Final = frozenset(
    {
        "agent_run",
        "agent_run_alarm",
        "agent_prediction",
        "agent_prediction_review",
        "agent_run_action",
        "agent_tool_call",
        "approval_request",
        "action_delivery",
    }
)
CHECKPOINT_CATALOG: Final = frozenset({"checkpoint_migrations"})
CHECKPOINT_OPERATIONAL: Final = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}
)
RUNTIME_SEQUENCES: Final = frozenset(
    {"agent_prediction_review_review_id_seq", "audit_log_audit_id_seq"}
)
QUERY_LOG_SEQUENCE: Final = "nl_query_log_nl_query_log_id_seq"
VIEWS: Final = frozenset({"v_alarm_event"})


@dataclass(frozen=True, slots=True)
class RoleSpec:
    login: bool
    superuser: bool = False
    createdb: bool = False
    createrole: bool = False
    replication: bool = False
    bypassrls: bool = False


ROLE_SPECS: Final[Mapping[ManagedRole, RoleSpec]] = MappingProxyType(
    {role: RoleSpec(login=role in LOGIN_ROLES) for role in MANAGED_ROLES}
)


@dataclass(frozen=True, slots=True)
class Inventory:
    tables: frozenset[str]
    views: frozenset[str]
    sequences: frozenset[str]

    @property
    def relations(self) -> frozenset[str]:
        return self.tables | self.views


@dataclass(frozen=True, slots=True)
class RoleMatrixContract:
    database: str
    profile: Profile
    schema_stage: SchemaStage
    matrix_stage: MatrixStage
    inventory: Inventory
    database_privileges: Mapping[ManagedRole, frozenset[str]]
    schema_privileges: Mapping[ManagedRole, frozenset[str]]
    relation_privileges: Mapping[str, Mapping[ManagedRole, frozenset[str]]]
    column_privileges: Mapping[str, Mapping[str, Mapping[ManagedRole, frozenset[str]]]]
    sequence_privileges: Mapping[str, Mapping[ManagedRole, frozenset[str]]]


def _load_manifest(filename: str, expected_stage: str) -> dict[str, Any]:
    path = MANIFEST_ROOT / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"profile manifest를 읽을 수 없습니다: {filename}") from exc
    if not isinstance(payload, dict) or payload.get("dataset_epoch") != DATASET_EPOCH:
        raise ContractError(f"profile manifest epoch이 다릅니다: {filename}")
    if payload.get("schema_stage") != expected_stage:
        raise ContractError(f"profile manifest schema stage가 다릅니다: {filename}")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise ContractError(f"profile manifest table inventory가 없습니다: {filename}")
    names = frozenset(tables)
    if any(not isinstance(name, str) or not name for name in names):
        raise ContractError(f"profile manifest table 이름이 잘못됐습니다: {filename}")
    for name, metadata in tables.items():
        columns = metadata.get("columns") if isinstance(metadata, dict) else None
        if (
            not isinstance(columns, list)
            or not columns
            or any(not isinstance(column, str) or not column for column in columns)
            or len(columns) != len(set(columns))
        ):
            raise ContractError(f"profile manifest column 계약이 잘못됐습니다: {name}")
    return payload


def _manifest_tables(payload: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(payload["tables"])


LOT_HISTORY_COLUMNS: Final = (
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
)
LOT_HISTORY_SAFE_COLUMNS: Final = tuple(
    column for column in LOT_HISTORY_COLUMNS if column != "fault_code"
)
EVALUATION_LABEL_COLUMNS: Final = (
    "lot_hist_id",
    "lot_id",
    "wafer_no",
    "wafer_id",
    "step_id",
    "equipment_id",
    "chamber_id",
    "recipe_id",
    "fault_code",
)
EVALUATION_ACTION_COLUMNS: Final = (
    "action_id",
    "lot_id",
    "equipment_id",
    "chamber_id",
    "action_code",
    "approval_required",
    "approval_status",
)


@dataclass(frozen=True, slots=True)
class _ManifestContract:
    runtime_guarded: Mapping[str, Any]
    runtime_checkpointed: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    runtime_guarded_tables: frozenset[str]
    runtime_checkpointed_tables: frozenset[str]
    evaluation_tables: frozenset[str]


@cache
def _manifest_contract() -> _ManifestContract:
    """final manifest를 첫 contract build 시점에 한 번만 적재한다.

    모듈 import는 app 기동과 pytest collection에서도 사용된다. manifest 파일
    손상은 권한 계약을 실제로 사용할 때 ``ContractError``로 보고하고, 무관한
    liveness/import 경로를 막지 않는다.
    """

    runtime_guarded = _load_manifest(
        "runtime.runtime_guarded.json", SchemaStage.RUNTIME_GUARDED.value
    )
    runtime_checkpointed = _load_manifest(
        "runtime.runtime_checkpointed.json", SchemaStage.RUNTIME_CHECKPOINTED.value
    )
    # SchemaStage enum과 파일명은 현재 lifecycle인 evaluation_reference지만,
    # pin된 manifest 내부 schema_stage는 발급 이력값 reference_final을 유지한다.
    # 두 값의 차이는 typo가 아니며 manifest 재발급 Task 없이 바꾸지 않는다.
    evaluation = _load_manifest(
        "evaluation.evaluation_reference.json", "reference_final"
    )

    runtime_guarded_tables = _manifest_tables(runtime_guarded)
    runtime_checkpointed_tables = _manifest_tables(runtime_checkpointed)
    evaluation_tables = _manifest_tables(evaluation)
    if runtime_checkpointed_tables != (
        runtime_guarded_tables | CHECKPOINT_CATALOG | CHECKPOINT_OPERATIONAL
    ):
        raise ContractError(
            "Runtime checkpoint successor inventory가 guarded + 4 table이 아닙니다"
        )
    if len(runtime_guarded_tables) != 22 or len(runtime_checkpointed_tables) != 26:
        raise ContractError("Runtime final inventory 수가 22/26 계약과 다릅니다")
    if len(evaluation_tables) != 13:
        raise ContractError("Evaluation final inventory 수가 13 계약과 다릅니다")
    for payload in (runtime_guarded, runtime_checkpointed, evaluation):
        if tuple(payload["tables"]["lot_history"]["columns"]) != LOT_HISTORY_COLUMNS:
            raise ContractError(
                "lot_history column projection이 final manifest와 다릅니다"
            )
    if not set(EVALUATION_ACTION_COLUMNS).issubset(
        evaluation["tables"]["action_history"]["columns"]
    ):
        raise ContractError("evaluation action projection이 final manifest와 다릅니다")
    return _ManifestContract(
        runtime_guarded=runtime_guarded,
        runtime_checkpointed=runtime_checkpointed,
        evaluation=evaluation,
        runtime_guarded_tables=runtime_guarded_tables,
        runtime_checkpointed_tables=runtime_checkpointed_tables,
        evaluation_tables=evaluation_tables,
    )


def expected_inventory(profile: Profile, schema_stage: SchemaStage) -> Inventory:
    manifests = _manifest_contract()
    if profile is Profile.RUNTIME:
        if schema_stage is SchemaStage.RUNTIME_GUARDED:
            tables = manifests.runtime_guarded_tables
        elif schema_stage is SchemaStage.RUNTIME_CHECKPOINTED:
            tables = manifests.runtime_checkpointed_tables
        else:
            raise ContractError("Runtime profile의 schema stage가 아닙니다")
        sequences = RUNTIME_SEQUENCES | {QUERY_LOG_SEQUENCE}
        return Inventory(tables=tables, views=VIEWS, sequences=frozenset(sequences))
    if schema_stage is not SchemaStage.EVALUATION_REFERENCE:
        raise ContractError("Evaluation profile의 schema stage가 아닙니다")
    return Inventory(
        tables=manifests.evaluation_tables,
        views=VIEWS,
        sequences=frozenset({QUERY_LOG_SEQUENCE}),
    )


def expected_table_columns(
    profile: Profile, schema_stage: SchemaStage
) -> Mapping[str, tuple[str, ...]]:
    """선택한 final profile manifest의 exact table column 계약."""

    manifests = _manifest_contract()
    if profile is Profile.RUNTIME:
        if schema_stage is SchemaStage.RUNTIME_GUARDED:
            payload = manifests.runtime_guarded
        elif schema_stage is SchemaStage.RUNTIME_CHECKPOINTED:
            payload = manifests.runtime_checkpointed
        else:
            raise ContractError("Runtime profile의 schema stage가 아닙니다")
    elif schema_stage is SchemaStage.EVALUATION_REFERENCE:
        payload = manifests.evaluation
    else:
        raise ContractError("Evaluation profile의 schema stage가 아닙니다")
    return MappingProxyType(
        {
            name: tuple(metadata["columns"])
            for name, metadata in payload["tables"].items()
        }
    )


def _empty_role_map() -> dict[ManagedRole, frozenset[str]]:
    return {role: frozenset() for role in MANAGED_ROLES}


def _relation_matrix(
    inventory: Inventory,
) -> dict[str, dict[ManagedRole, frozenset[str]]]:
    return {name: _empty_role_map() for name in sorted(inventory.relations)}


def _sequence_matrix(
    inventory: Inventory,
) -> dict[str, dict[ManagedRole, frozenset[str]]]:
    return {name: _empty_role_map() for name in sorted(inventory.sequences)}


def _column_matrix() -> dict[str, dict[str, dict[ManagedRole, frozenset[str]]]]:
    return {}


def _set_relation(
    matrix: dict[str, dict[ManagedRole, frozenset[str]]],
    names: frozenset[str],
    role: ManagedRole,
    privileges: frozenset[str],
) -> None:
    for name in names:
        if name not in matrix:
            raise ContractError(f"matrix relation이 inventory에 없습니다: {name}")
        matrix[name][role] = privileges


def _set_columns(
    matrix: dict[str, dict[str, dict[ManagedRole, frozenset[str]]]],
    relation: str,
    columns: tuple[str, ...],
    role: ManagedRole,
) -> None:
    relation_map = matrix.setdefault(relation, {})
    for column in columns:
        role_map = relation_map.setdefault(column, _empty_role_map())
        role_map[role] = frozenset({"SELECT"})


def _runtime_contract(
    database: str, schema_stage: SchemaStage, matrix_stage: MatrixStage
) -> RoleMatrixContract:
    inventory = expected_inventory(Profile.RUNTIME, schema_stage)
    if (
        matrix_stage is MatrixStage.CHECKPOINTED
        and schema_stage is not SchemaStage.RUNTIME_CHECKPOINTED
    ):
        raise ContractError(
            "checkpoint role stage는 checkpoint schema에서만 허용됩니다"
        )

    db = _empty_role_map()
    schema = _empty_role_map()
    for role in (ManagedRole.APP, ManagedRole.READONLY):
        db[role] = frozenset({"CONNECT"})
        schema[role] = frozenset({"USAGE"})

    relations = _relation_matrix(inventory)
    columns = _column_matrix()
    sequences = _sequence_matrix(inventory)
    read = frozenset({"SELECT"})
    dml = frozenset({"SELECT", "INSERT", "UPDATE"})

    _set_relation(relations, BASE_READ, ManagedRole.APP, read)
    _set_relation(relations, BASE_READ, ManagedRole.READONLY, read)
    _set_columns(columns, "lot_history", LOT_HISTORY_SAFE_COLUMNS, ManagedRole.APP)
    _set_columns(columns, "lot_history", LOT_HISTORY_SAFE_COLUMNS, ManagedRole.READONLY)
    relations["metrology"][ManagedRole.READONLY] = read
    relations["action_history"][ManagedRole.APP] = dml
    relations["action_history"][ManagedRole.READONLY] = read
    _set_relation(relations, REFERENCE_READ | RAG_TABLES, ManagedRole.APP, read)
    _set_relation(relations, REFERENCE_READ | RAG_TABLES, ManagedRole.READONLY, read)
    _set_relation(relations, RUNTIME_TABLES, ManagedRole.APP, dml)
    relations["audit_log"][ManagedRole.APP] = frozenset({"SELECT", "INSERT"})
    for sequence in RUNTIME_SEQUENCES:
        sequences[sequence][ManagedRole.APP] = frozenset({"USAGE", "SELECT"})

    if matrix_stage is MatrixStage.CHECKPOINTED:
        _set_relation(relations, CHECKPOINT_CATALOG, ManagedRole.APP, read)
        _set_relation(relations, CHECKPOINT_OPERATIONAL, ManagedRole.APP, dml)

    return RoleMatrixContract(
        database=database,
        profile=Profile.RUNTIME,
        schema_stage=schema_stage,
        matrix_stage=matrix_stage,
        inventory=inventory,
        database_privileges=MappingProxyType(db),
        schema_privileges=MappingProxyType(schema),
        relation_privileges=_freeze_nested(relations),
        column_privileges=_freeze_columns(columns),
        sequence_privileges=_freeze_nested(sequences),
    )


def _evaluation_contract(
    database: str, matrix_stage: MatrixStage
) -> RoleMatrixContract:
    if matrix_stage is not MatrixStage.CORE:
        raise ContractError("Evaluation에는 checkpoint role stage가 없습니다")
    inventory = expected_inventory(Profile.EVALUATION, SchemaStage.EVALUATION_REFERENCE)
    db = _empty_role_map()
    schema = _empty_role_map()
    for role in (ManagedRole.READONLY, ManagedRole.EVALUATION, ManagedRole.LOGGER):
        db[role] = frozenset({"CONNECT"})
        schema[role] = frozenset({"USAGE"})

    relations = _relation_matrix(inventory)
    columns = _column_matrix()
    sequences = _sequence_matrix(inventory)
    read = frozenset({"SELECT"})

    readonly_tables = (
        BASE_READ | {"metrology", "action_history"} | REFERENCE_READ | RAG_TABLES
    )
    _set_relation(relations, readonly_tables, ManagedRole.READONLY, read)
    _set_columns(columns, "lot_history", LOT_HISTORY_SAFE_COLUMNS, ManagedRole.READONLY)
    _set_columns(
        columns, "lot_history", EVALUATION_LABEL_COLUMNS, ManagedRole.EVALUATION
    )
    relations["metrology"][ManagedRole.EVALUATION] = read
    _set_columns(
        columns,
        "action_history",
        EVALUATION_ACTION_COLUMNS,
        ManagedRole.EVALUATION,
    )
    relations["nl_query_log"][ManagedRole.READONLY] = read
    relations["nl_query_log"][ManagedRole.LOGGER] = frozenset({"INSERT"})
    _set_columns(columns, "nl_query_log", ("nl_query_log_id",), ManagedRole.LOGGER)
    sequences[QUERY_LOG_SEQUENCE][ManagedRole.LOGGER] = frozenset({"USAGE", "SELECT"})

    return RoleMatrixContract(
        database=database,
        profile=Profile.EVALUATION,
        schema_stage=SchemaStage.EVALUATION_REFERENCE,
        matrix_stage=matrix_stage,
        inventory=inventory,
        database_privileges=MappingProxyType(db),
        schema_privileges=MappingProxyType(schema),
        relation_privileges=_freeze_nested(relations),
        column_privileges=_freeze_columns(columns),
        sequence_privileges=_freeze_nested(sequences),
    )


def _freeze_nested(
    value: Mapping[str, Mapping[ManagedRole, frozenset[str]]],
) -> Mapping[str, Mapping[ManagedRole, frozenset[str]]]:
    return MappingProxyType(
        {name: MappingProxyType(dict(role_map)) for name, role_map in value.items()}
    )


def _freeze_columns(
    value: Mapping[str, Mapping[str, Mapping[ManagedRole, frozenset[str]]]],
) -> Mapping[str, Mapping[str, Mapping[ManagedRole, frozenset[str]]]]:
    return MappingProxyType(
        {
            relation: MappingProxyType(
                {
                    column: MappingProxyType(dict(role_map))
                    for column, role_map in column_map.items()
                }
            )
            for relation, column_map in value.items()
        }
    )


def build_contract(
    database: str,
    matrix_stage: MatrixStage | str,
    schema_stage: SchemaStage | str,
) -> RoleMatrixContract:
    try:
        stage = MatrixStage(matrix_stage)
        schema = SchemaStage(schema_stage)
    except ValueError as exc:
        raise ContractError("role/schema stage가 allowlist 밖입니다") from exc
    profile = DATABASE_PROFILES.get(database)
    if profile is None:
        raise ContractError(
            "database가 allowlist 밖입니다", reason_code="PROFILE_NOT_ALLOWED"
        )
    if profile is Profile.RUNTIME:
        return _runtime_contract(database, schema, stage)
    return _evaluation_contract(database, stage)


def _sorted_privileges(
    value: Mapping[ManagedRole, frozenset[str]],
) -> dict[str, list[str]]:
    return {role.value: sorted(value[role]) for role in MANAGED_ROLES}


def contract_payload(contract: RoleMatrixContract) -> dict[str, Any]:
    """password·host·DSN이 없는 canonical contract payload."""

    return {
        "task_id": TASK_ID,
        "dataset_epoch": DATASET_EPOCH,
        "database": contract.database,
        "profile": contract.profile.value,
        "schema_stage": contract.schema_stage.value,
        "matrix_stage": contract.matrix_stage.value,
        "public_scope": [
            "database_connect_temp",
            "schema_usage_create",
            "relation_privileges",
            "sequence_privileges",
        ],
        "roles": {
            role.value: {
                "login": ROLE_SPECS[role].login,
                "superuser": False,
                "createdb": False,
                "createrole": False,
                "replication": False,
                "bypassrls": False,
            }
            for role in MANAGED_ROLES
        },
        "inventory": {
            "tables": sorted(contract.inventory.tables),
            "views": sorted(contract.inventory.views),
            "sequences": sorted(contract.inventory.sequences),
        },
        "database_privileges": _sorted_privileges(contract.database_privileges),
        "schema_privileges": _sorted_privileges(contract.schema_privileges),
        "relation_privileges": {
            name: _sorted_privileges(role_map)
            for name, role_map in sorted(contract.relation_privileges.items())
        },
        "column_privileges": {
            relation: {
                column: _sorted_privileges(role_map)
                for column, role_map in sorted(column_map.items())
            }
            for relation, column_map in sorted(contract.column_privileges.items())
        },
        "sequence_privileges": {
            name: _sorted_privileges(role_map)
            for name, role_map in sorted(contract.sequence_privileges.items())
        },
    }


def contract_digest(contract: RoleMatrixContract) -> str:
    payload = json.dumps(
        contract_payload(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def expected_privilege_count(contract: RoleMatrixContract) -> int:
    """preview·marker에 쓰는 secret 없는 허용 privilege cell 수."""

    total = sum(map(len, contract.database_privileges.values()))
    total += sum(map(len, contract.schema_privileges.values()))
    total += sum(
        len(privileges)
        for role_map in contract.relation_privileges.values()
        for privileges in role_map.values()
    )
    total += sum(
        len(privileges)
        for column_map in contract.column_privileges.values()
        for role_map in column_map.values()
        for privileges in role_map.values()
    )
    total += sum(
        len(privileges)
        for role_map in contract.sequence_privileges.values()
        for privileges in role_map.values()
    )
    return total


__all__ = [
    "BASE_READ",
    "CHECKPOINT_CATALOG",
    "CHECKPOINT_OPERATIONAL",
    "COLUMN_PRIVILEGES",
    "ContractError",
    "DATABASE_PRIVILEGES",
    "DATABASE_PROFILES",
    "DATASET_EPOCH",
    "EVALUATION_ACTION_COLUMNS",
    "EVALUATION_LABEL_COLUMNS",
    "Inventory",
    "LOGIN_ROLES",
    "LOT_HISTORY_SAFE_COLUMNS",
    "MANAGED_ROLES",
    "ManagedRole",
    "MatrixStage",
    "Profile",
    "RELATION_PRIVILEGES",
    "ROLE_SPECS",
    "RoleMatrixContract",
    "SCHEMA_PRIVILEGES",
    "SEQUENCE_PRIVILEGES",
    "SchemaStage",
    "TASK_ID",
    "build_contract",
    "contract_digest",
    "contract_payload",
    "expected_inventory",
    "expected_table_columns",
    "expected_privilege_count",
]
