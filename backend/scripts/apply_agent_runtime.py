"""최종 epoch Runtime 002 migration runner (`V5-CM-3.2`).

`002_agent_runtime_clean.sql`의 9-table 물리 계약을 최종 `fdc_final_20260818` epoch에
다시 연결하고, Runtime 두 DB의 **적용 증명 marker**를 발급한다.

지원되는 mutation 경로는 이 runner뿐이다. SQL 파일은 target·빈 데이터 guard만 담고
transaction 범위의 쓰기 배제는 runner가 소유하므로 `psql -f` 직접 실행은
지원하지 않는다.

## V4 계보와의 분리

`V5-CM-1.6`이 이 runner를 `FINAL_RUNTIME_MIGRATION_NOT_WIRED`로 막았다. 구 corrected
계보 위에서만 성립했기 때문이다. 막힌 이유는 두 겹이었다.

1. `run_apply()`가 engine 생성 전에 명시적으로 raise했다.
2. `_artifact_identity()`가 **active에 없는 V4 `001` marker**와 v4
   `001_reference_extensions.sql`의 SHA를 요구했다. 그 marker는 `V5-CM-1.2`가
   `history/kosa_0813/markers/`로 격리했고, 최종 계보는
   `migrations/v5/001_reference_extensions_final.sql`이다.

둘 다 제거했다. V4 `001` provenance 주장은 다음 둘이 대체한다.

- **V5 reference가 lineage 앞에 있음** — schema 검증을 통과한 active manifest의
  exact `applied_migrations` 2원소와 `manifest_sha256`
- **V5 reference 물리 결과가 맞음** — CM-3.1 순수 판정기를 read-only로 돌린
  live R03/View postcheck

**V4 module을 import하지 않는다.** 계보 심볼(`load_marker`·`postcheck_database`)은
제거했고, epoch 중립 plumbing은 `bootstrap_common`으로 옮겨 거기서 가져온다. V4는
같은 module에서 재수출하므로 기존 소비자는 그대로다(구현리뷰 필수 4).

## 상태 기계

물리 schema가 이미 있는 DB를 다시 만들지 않는다. 상태를 먼저 판정하고
그에 맞는 동작만 한다.

| 상태 | 동작 |
|---|---|
| `ABSENT` | 승인된 fresh apply에서만 9 table 생성 |
| `EXACT_UNMARKED` | **DB 쓰기 0건**, receipt·marker만 발급 (`VERIFIED_EXISTING`) |
| `EXACT_MARKED` | `NO_OP` |
| `PARTIAL`·`DRIFT` | 중단. 자동 drop/add/rename **금지** |
| `ACTION_PRESENT` | lock 안에서 중단, DDL·artifact 0건 |
| `PROFILE_NOT_ALLOWED` | parser 직후 중단 |
| `MARKER_STALE` | final marker로 간주하지 않으며 덮어쓰지 않음 |
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import apply_reference_extensions_v5 as reference_v5
import manifest_v3
from bootstrap_common import (
    BASE_TABLES,
    REFERENCE_TABLES,
    REFERENCE_VIEW,
    ReferenceExtensionError,
    _canonical_hash,
    _engine_for,
    _exclusive_artifact_lock,
    _json_safe,
    _result_rows,
    _single_row,
    _timezone_text,
    acquire_advisory_lock,
    validate_change_reference,
)
from db_target import (
    ALLOWED_DATABASES,
    BootstrapTarget,
    TargetValidationError,
    load_bootstrap_target,
)
from dotenv import load_dotenv
from manifest_v3 import (
    VerificationError,
    atomic_save_json,
    resolve_bootstrap_manifest_path,
    scan_for_sensitive_values,
)
from mutation_runtime import (
    MutationRuntimeError,
    prepare_transaction,
    resolve_exclusive_mode,
)
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap"
MIGRATION_PATH = (
    REPOSITORY_ROOT / "backend" / "migrations" / "002_agent_runtime_clean.sql"
)
MARKER_ROOT = BOOTSTRAP_ROOT / "markers"
REPORT_ROOT = BOOTSTRAP_ROOT / "reports"

RUNTIME_DATABASES = frozenset({"kosa_agent", "kosa_agent_e2e"})
RUNTIME_PROFILE = "runtime"
RUNTIME_STAGE = "runtime_clean"

#: 이 runner가 발급하는 artifact의 단일 식별자.
#:
#: `V5-CM-1.2`가 격리한 구 `runtime_clean.<database>.json`과 이름이
#: **겹치지 않게** 한다.
#: 같은 이름을 재사용하면 history의 폐기 marker를 final로 잘못 복원·승격하는 사고가
#: 구조적으로 가능해진다(계획 §4.7).
FINAL_ARTIFACT_TYPE = "agent_runtime_final"
FINAL_MARKER_FORMAT_VERSION = 1
TASK_ID = "V5-CM-3.2"

#: active Runtime manifest가 등록한 migration 계보. 순서까지 계약이다.
MIGRATION_ID = "002_agent_runtime_clean"
EXPECTED_MIGRATION_LINEAGE = ("v5_001_reference_extensions_final", MIGRATION_ID)

#: R03 실측 행 수로 허용하는 값.
#:
#: `V5-A-1.4`가 연속 3회 파생을 적재하기 전에는 0, 적재 뒤에는 3이다. 그 사이 값은
#: 적재가 끊긴 상태이므로 Runtime marker를 발급할 근거가 되지 않는다.
#: View 총량 189·192 대조는 CM-3.1 판정기가 한다.
ALLOWED_R03_ROWS = frozenset({0, 3})
RUNTIME_TABLES = (
    "action_delivery",
    "agent_prediction",
    "agent_prediction_review",
    "agent_run",
    "agent_run_action",
    "agent_run_alarm",
    "agent_tool_call",
    "approval_request",
    "audit_log",
)
EXPECTED_ALL_TABLES = frozenset({*BASE_TABLES, *REFERENCE_TABLES, *RUNTIME_TABLES})
EXPECTED_SEQUENCE_NAMES = frozenset(
    {"agent_prediction_review_review_id_seq", "audit_log_audit_id_seq"}
)
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")


@dataclass(frozen=True)
class ColumnContract:
    name: str
    data_type: str
    nullable: bool
    default: str | None = None


def _column(
    name: str, data_type: str, nullable: bool, default: str | None = None
) -> ColumnContract:
    return ColumnContract(name, data_type, nullable, default)


EXPECTED_TABLE_COLUMNS: dict[str, tuple[ColumnContract, ...]] = {
    "agent_run": (
        _column("agent_run_id", "character varying(20)", False),
        _column("thread_id", "character varying(36)", False),
        _column("retry_of_run_id", "character varying(20)", True),
        _column("lot_id", "character varying(20)", False),
        _column("chamber_id", "character varying(24)", False),
        _column("requested_alarm_source", "character varying(10)", False),
        _column("requested_alarm_id", "character varying(24)", False),
        _column("representative_alarm_source", "character varying(10)", False),
        _column("representative_alarm_id", "character varying(24)", False),
        _column("status", "character varying(20)", False),
        _column("autonomy_level", "smallint", False),
        _column("action", "character varying(20)", True),
        _column("severity", "character varying(10)", True),
        _column("llm_model", "character varying(64)", True),
        _column("prompt_version", "character varying(40)", True),
        _column("evidence", "jsonb", True),
        _column("input_tokens", "integer", True),
        _column("output_tokens", "integer", True),
        _column("latency_ms", "integer", True),
        _column("started_at", "timestamp with time zone", False, "now()"),
        _column("ended_at", "timestamp with time zone", True),
    ),
    "agent_run_alarm": (
        _column("agent_run_id", "character varying(20)", False),
        _column("alarm_source", "character varying(10)", False),
        _column("alarm_id", "character varying(24)", False),
        _column("is_representative", "boolean", False, "false"),
    ),
    "agent_prediction": (
        _column("agent_run_id", "character varying(20)", False),
        _column("predicted_fault_code", "character varying(10)", False),
        _column("confidence", "numeric(4,3)", False),
        _column("cause_summary", "text", False),
        _column("evidence", "jsonb", False),
        _column("llm_model", "character varying(64)", False),
        _column("prompt_version", "character varying(40)", False),
        _column("created_at", "timestamp with time zone", False, "now()"),
    ),
    "agent_prediction_review": (
        _column(
            "review_id",
            "bigint",
            False,
            "nextval('agent_prediction_review_review_id_seq'::regclass)",
        ),
        _column("agent_run_id", "character varying(20)", False),
        _column("reviewed_fault_code", "character varying(10)", True),
        _column("disposition", "character varying(16)", False),
        _column("label_source", "character varying(16)", False),
        _column("reviewer", "character varying(40)", False),
        _column("reviewed_at", "timestamp with time zone", False, "now()"),
        _column("comment", "text", True),
    ),
    "agent_run_action": (
        _column("agent_run_id", "character varying(20)", False),
        _column("action_id", "character varying(20)", False),
        _column("link_role", "character varying(8)", False),
        _column("lot_id", "character varying(20)", False),
        _column("chamber_id", "character varying(24)", False),
        _column("trigger_alarm_source", "character varying(10)", False),
        _column("trigger_alarm_id", "character varying(24)", False),
        _column("linked_at", "timestamp with time zone", False, "now()"),
    ),
    "agent_tool_call": (
        _column("tool_call_id", "character varying(29)", False),
        _column("agent_run_id", "character varying(20)", False),
        _column("call_seq", "integer", False),
        _column("tool_name", "character varying(40)", False),
        _column("input", "jsonb", True),
        _column("output", "jsonb", True),
        _column("status", "character varying(10)", False),
        _column("latency_ms", "integer", True),
        _column("called_at", "timestamp with time zone", False, "now()"),
        _column("error_msg", "text", True),
    ),
    "approval_request": (
        _column("approval_id", "character varying(20)", False),
        _column("action_id", "character varying(20)", False),
        _column("agent_run_id", "character varying(20)", False),
        _column(
            "status", "character varying(12)", False, "'PENDING'::character varying"
        ),
        _column("requested_at", "timestamp with time zone", False, "now()"),
        _column("decided_by", "character varying(40)", True),
        _column("decided_at", "timestamp with time zone", True),
        _column("decision_comment", "character varying(1000)", True),
    ),
    "action_delivery": (
        _column("action_id", "character varying(20)", False),
        _column("channel", "character varying(10)", False),
        _column("status", "character varying(10)", False),
        _column("request_hash", "character(64)", False),
        _column("attempt_count", "integer", False, "0"),
        _column("provider_message_id", "text", True),
        _column("started_at", "timestamp with time zone", True),
        _column("completed_at", "timestamp with time zone", True),
        _column("last_error", "text", True),
        _column("result", "jsonb", True),
    ),
    "audit_log": (
        _column(
            "audit_id", "bigint", False, "nextval('audit_log_audit_id_seq'::regclass)"
        ),
        _column("occurred_at", "timestamp with time zone", False, "now()"),
        _column("actor_type", "character varying(10)", False),
        _column("actor_id", "character varying(40)", True),
        _column("event_type", "character varying(32)", False),
        _column("entity_type", "character varying(16)", False),
        _column("entity_id", "character varying(20)", False),
        _column("before_json", "jsonb", True),
        _column("after_json", "jsonb", True),
        _column("detail", "text", True),
    ),
}


#: **exact constraint 계약.** 이름을 key로 두고 table·종류·컬럼·FK endpoint·참조 동작·
#: 정규화 정의를 전부 비교한다.
#:
#: 구 `EXPECTED_CONSTRAINT_COUNTS`는 table별 **종류 개수**와 전체 정의를 이어 붙인
#: 문자열에서 조각을 찾는 방식이었다. 그러면 다음이 전부 통과한다(계획 §4.3).
#:
#: - constraint가 엉뚱한 table로 옮겨져도 개수만 맞으면 통과
#: - 이름만 같고 정의가 다른 변조
#: - FK가 허용 table을 가리키되 컬럼·`ON DELETE`가 다른 경우
#:
#: 값은 PostgreSQL 16에 `002_agent_runtime_clean.sql`을 실제로 적용해
#: `pg_constraint`에서 뜬 것이다. 손으로 적은 기대값이 아니다.
@dataclass(frozen=True)
class ConstraintContract:
    table: str
    contype: str
    columns: tuple[str, ...]
    referenced_table: str | None
    referenced_columns: tuple[str, ...]
    on_delete: str
    on_update: str
    definition: str


#: **exact index 계약.** predicate를 정규화 문자열로 통째 비교한다.
#:
#: 구 `PARTIAL_INDEX_VALUES`는 predicate에서 `'([A-Z_]+)'` 값만 뽑아 집합 비교했다.
#: 같은 값을 그대로 두고 `OR true`나 다른 조건을 덧붙인 변조가 통과한다(계획 §4.4).
@dataclass(frozen=True)
class IndexContract:
    table: str
    unique: bool
    method: str
    columns: tuple[str, ...]
    predicate: str | None
    expressions: str | None


EXPECTED_CONSTRAINTS: Mapping[str, ConstraintContract] = MappingProxyType(
    {
        "action_delivery_action_id_fkey": ConstraintContract(
            table="action_delivery",
            contype="f",
            columns=("action_id",),
            referenced_table="action_history",
            referenced_columns=("action_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (action_id) references action_history(action_id)",
        ),
        "action_delivery_attempt_count_check": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("attempt_count",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (attempt_count >= 0)",
        ),
        "action_delivery_channel_check": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("channel",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (channel = any (array['email', 'mes_mock']))",
        ),
        "action_delivery_check": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("completed_at", "started_at"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (completed_at is null or started_at is not null)",
        ),
        "action_delivery_check1": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("completed_at", "started_at"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (completed_at is null or completed_at >= started_at)",
        ),
        "action_delivery_pkey": ConstraintContract(
            table="action_delivery",
            contype="p",
            columns=("action_id", "channel"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (action_id, channel)",
        ),
        "action_delivery_provider_message_id_check": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("provider_message_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (provider_message_id is null or btrim(provider_message_id)"
                " <> '')"
            ),
        ),
        "action_delivery_request_hash_check": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("request_hash",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (request_hash ~ '^[0-9a-f]{64}$')",
        ),
        "action_delivery_status_check": ConstraintContract(
            table="action_delivery",
            contype="c",
            columns=("status",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (status = any (array['blocked', 'waiting', 'sending', "
                "'sent', 'failed', 'canceled', 'unknown']))"
            ),
        ),
        "agent_prediction_agent_run_id_fkey": ConstraintContract(
            table="agent_prediction",
            contype="f",
            columns=("agent_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (agent_run_id) references agent_run(agent_run_id)",
        ),
        "agent_prediction_cause_summary_check": ConstraintContract(
            table="agent_prediction",
            contype="c",
            columns=("cause_summary",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(cause_summary) <> '')",
        ),
        "agent_prediction_confidence_check": ConstraintContract(
            table="agent_prediction",
            contype="c",
            columns=("confidence",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (confidence >= 0 and confidence <= 1)",
        ),
        "agent_prediction_llm_model_check": ConstraintContract(
            table="agent_prediction",
            contype="c",
            columns=("llm_model",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(llm_model) <> '')",
        ),
        "agent_prediction_pkey": ConstraintContract(
            table="agent_prediction",
            contype="p",
            columns=("agent_run_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (agent_run_id)",
        ),
        "agent_prediction_predicted_fault_code_check": ConstraintContract(
            table="agent_prediction",
            contype="c",
            columns=("predicted_fault_code",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (predicted_fault_code = any (array['foc', 'rfm', 'mfd', "
                "'tmd', 'oth']))"
            ),
        ),
        "agent_prediction_prompt_version_check": ConstraintContract(
            table="agent_prediction",
            contype="c",
            columns=("prompt_version",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(prompt_version) <> '')",
        ),
        "agent_prediction_review_agent_run_id_fkey": ConstraintContract(
            table="agent_prediction_review",
            contype="f",
            columns=("agent_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (agent_run_id) references agent_run(agent_run_id)",
        ),
        "agent_prediction_review_check": ConstraintContract(
            table="agent_prediction_review",
            contype="c",
            columns=("disposition", "reviewed_fault_code"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (disposition <> 'corrected' or reviewed_fault_code is not "
                "null)"
            ),
        ),
        "agent_prediction_review_disposition_check": ConstraintContract(
            table="agent_prediction_review",
            contype="c",
            columns=("disposition",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (disposition = any (array['accepted', 'corrected', "
                "'undetermined']))"
            ),
        ),
        "agent_prediction_review_label_source_check": ConstraintContract(
            table="agent_prediction_review",
            contype="c",
            columns=("label_source",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (label_source = any (array['human_review', "
                "'mentor_review', 'hidden_gold']))"
            ),
        ),
        "agent_prediction_review_pkey": ConstraintContract(
            table="agent_prediction_review",
            contype="p",
            columns=("review_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (review_id)",
        ),
        "agent_prediction_review_reviewed_fault_code_check": ConstraintContract(
            table="agent_prediction_review",
            contype="c",
            columns=("reviewed_fault_code",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (reviewed_fault_code = any (array['foc', 'rfm', 'mfd', "
                "'tmd', 'oth']))"
            ),
        ),
        "agent_prediction_review_reviewer_check": ConstraintContract(
            table="agent_prediction_review",
            contype="c",
            columns=("reviewer",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(reviewer) <> '')",
        ),
        "agent_run_action_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("action",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (action = any (array['monitoring', 'warning', " "'eqp_hold']))"
            ),
        ),
        "agent_run_agent_run_id_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("agent_run_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (agent_run_id ~ '^run-[0-9a-f]{16}$')",
        ),
        "agent_run_autonomy_level_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("autonomy_level",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (autonomy_level = any (array[1, 2, 3]))",
        ),
        "agent_run_chamber_id_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("chamber_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(chamber_id) <> '')",
        ),
        "agent_run_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("ended_at", "started_at"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (ended_at is null or ended_at >= started_at)",
        ),
        "agent_run_check1": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("action", "severity"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (action is null and severity is null or action = "
                "'monitoring' and severity = 'low' or action = 'warning' and "
                "severity = 'medium' or action = 'eqp_hold' and severity = "
                "'high')"
            ),
        ),
        "agent_run_input_tokens_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("input_tokens",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (input_tokens >= 0)",
        ),
        "agent_run_latency_ms_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("latency_ms",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (latency_ms >= 0)",
        ),
        "agent_run_llm_model_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("llm_model",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (llm_model is null or btrim(llm_model) <> '')",
        ),
        "agent_run_lot_id_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("lot_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(lot_id) <> '')",
        ),
        "agent_run_output_tokens_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("output_tokens",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (output_tokens >= 0)",
        ),
        "agent_run_pkey": ConstraintContract(
            table="agent_run",
            contype="p",
            columns=("agent_run_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (agent_run_id)",
        ),
        "agent_run_prompt_version_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("prompt_version",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (prompt_version is null or btrim(prompt_version) <> '')",
        ),
        "agent_run_representative_alarm_id_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("representative_alarm_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(representative_alarm_id) <> '')",
        ),
        "agent_run_representative_alarm_source_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("representative_alarm_source",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (representative_alarm_source = any (array['trace', "
                "'summary', 'r03']))"
            ),
        ),
        "agent_run_requested_alarm_id_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("requested_alarm_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(requested_alarm_id) <> '')",
        ),
        "agent_run_requested_alarm_source_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("requested_alarm_source",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (requested_alarm_source = any (array['trace', 'summary', "
                "'r03']))"
            ),
        ),
        "agent_run_retry_of_run_id_fkey": ConstraintContract(
            table="agent_run",
            contype="f",
            columns=("retry_of_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition=(
                "foreign key (retry_of_run_id) references agent_run(agent_run_id)"
            ),
        ),
        "agent_run_severity_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("severity",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (severity = any (array['low', 'medium', 'high']))",
        ),
        "agent_run_status_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("status",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (status = any (array['running', 'waiting_approval', "
                "'completed', 'failed']))"
            ),
        ),
        "agent_run_thread_id_check": ConstraintContract(
            table="agent_run",
            contype="c",
            columns=("thread_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(thread_id) <> '')",
        ),
        "agent_run_action_action_id_check": ConstraintContract(
            table="agent_run_action",
            contype="c",
            columns=("action_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(action_id) <> '')",
        ),
        "agent_run_action_action_id_fkey": ConstraintContract(
            table="agent_run_action",
            contype="f",
            columns=("action_id",),
            referenced_table="action_history",
            referenced_columns=("action_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (action_id) references action_history(action_id)",
        ),
        "agent_run_action_agent_run_id_fkey": ConstraintContract(
            table="agent_run_action",
            contype="f",
            columns=("agent_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (agent_run_id) references agent_run(agent_run_id)",
        ),
        "agent_run_action_chamber_id_check": ConstraintContract(
            table="agent_run_action",
            contype="c",
            columns=("chamber_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(chamber_id) <> '')",
        ),
        "agent_run_action_link_role_check": ConstraintContract(
            table="agent_run_action",
            contype="c",
            columns=("link_role",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (link_role = any (array['created', 'reused']))",
        ),
        "agent_run_action_lot_id_check": ConstraintContract(
            table="agent_run_action",
            contype="c",
            columns=("lot_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(lot_id) <> '')",
        ),
        "agent_run_action_pkey": ConstraintContract(
            table="agent_run_action",
            contype="p",
            columns=("agent_run_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (agent_run_id)",
        ),
        "agent_run_action_trigger_alarm_id_check": ConstraintContract(
            table="agent_run_action",
            contype="c",
            columns=("trigger_alarm_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(trigger_alarm_id) <> '')",
        ),
        "agent_run_action_trigger_alarm_source_check": ConstraintContract(
            table="agent_run_action",
            contype="c",
            columns=("trigger_alarm_source",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (trigger_alarm_source = any (array['trace', 'summary', "
                "'r03']))"
            ),
        ),
        "agent_run_alarm_agent_run_id_fkey": ConstraintContract(
            table="agent_run_alarm",
            contype="f",
            columns=("agent_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (agent_run_id) references agent_run(agent_run_id)",
        ),
        "agent_run_alarm_alarm_id_check": ConstraintContract(
            table="agent_run_alarm",
            contype="c",
            columns=("alarm_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(alarm_id) <> '')",
        ),
        "agent_run_alarm_alarm_source_check": ConstraintContract(
            table="agent_run_alarm",
            contype="c",
            columns=("alarm_source",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (alarm_source = any (array['trace', 'summary', 'r03']))",
        ),
        "agent_run_alarm_pkey": ConstraintContract(
            table="agent_run_alarm",
            contype="p",
            columns=("agent_run_id", "alarm_source", "alarm_id"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (agent_run_id, alarm_source, alarm_id)",
        ),
        "agent_tool_call_agent_run_id_call_seq_key": ConstraintContract(
            table="agent_tool_call",
            contype="u",
            columns=("agent_run_id", "call_seq"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="unique (agent_run_id, call_seq)",
        ),
        "agent_tool_call_agent_run_id_fkey": ConstraintContract(
            table="agent_tool_call",
            contype="f",
            columns=("agent_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (agent_run_id) references agent_run(agent_run_id)",
        ),
        "agent_tool_call_call_seq_check": ConstraintContract(
            table="agent_tool_call",
            contype="c",
            columns=("call_seq",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (call_seq >= 1)",
        ),
        "agent_tool_call_latency_ms_check": ConstraintContract(
            table="agent_tool_call",
            contype="c",
            columns=("latency_ms",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (latency_ms >= 0)",
        ),
        "agent_tool_call_pkey": ConstraintContract(
            table="agent_tool_call",
            contype="p",
            columns=("tool_call_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (tool_call_id)",
        ),
        "agent_tool_call_status_check": ConstraintContract(
            table="agent_tool_call",
            contype="c",
            columns=("status",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (status = any (array['success', 'error', 'timeout']))",
        ),
        "agent_tool_call_tool_call_id_check": ConstraintContract(
            table="agent_tool_call",
            contype="c",
            columns=("tool_call_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (tool_call_id ~ '^tool-[0-9a-f]{24}$')",
        ),
        "agent_tool_call_tool_name_check": ConstraintContract(
            table="agent_tool_call",
            contype="c",
            columns=("tool_name",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(tool_name) <> '')",
        ),
        "approval_request_action_id_fkey": ConstraintContract(
            table="approval_request",
            contype="f",
            columns=("action_id",),
            referenced_table="action_history",
            referenced_columns=("action_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (action_id) references action_history(action_id)",
        ),
        "approval_request_action_id_key": ConstraintContract(
            table="approval_request",
            contype="u",
            columns=("action_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="unique (action_id)",
        ),
        "approval_request_agent_run_id_fkey": ConstraintContract(
            table="approval_request",
            contype="f",
            columns=("agent_run_id",),
            referenced_table="agent_run",
            referenced_columns=("agent_run_id",),
            on_delete="a",
            on_update="a",
            definition="foreign key (agent_run_id) references agent_run(agent_run_id)",
        ),
        "approval_request_approval_id_check": ConstraintContract(
            table="approval_request",
            contype="c",
            columns=("approval_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (approval_id ~ '^apr-[0-9a-f]{16}$')",
        ),
        "approval_request_check": ConstraintContract(
            table="approval_request",
            contype="c",
            columns=(
                "status",
                "decided_by",
                "decided_at",
                "decision_comment",
                "requested_at",
            ),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (status = 'pending' and decided_by is null and decided_at "
                "is null and decision_comment is null or (status = any "
                "(array['approved', 'rejected'])) and coalesce(btrim(decided_by),"
                " '') <> '' and decided_at is not null and decided_at >= "
                "requested_at or status = 'expired' and decided_by is null)"
            ),
        ),
        "approval_request_decision_comment_check": ConstraintContract(
            table="approval_request",
            contype="c",
            columns=("decision_comment",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (decision_comment is null or btrim(decision_comment) <> " "'')"
            ),
        ),
        "approval_request_pkey": ConstraintContract(
            table="approval_request",
            contype="p",
            columns=("approval_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (approval_id)",
        ),
        "approval_request_status_check": ConstraintContract(
            table="approval_request",
            contype="c",
            columns=("status",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (status = any (array['pending', 'approved', 'rejected', "
                "'expired']))"
            ),
        ),
        "audit_log_actor_id_check": ConstraintContract(
            table="audit_log",
            contype="c",
            columns=("actor_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (actor_id is null or btrim(actor_id) <> '')",
        ),
        "audit_log_actor_type_check": ConstraintContract(
            table="audit_log",
            contype="c",
            columns=("actor_type",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (actor_type = any (array['system', 'agent', 'human']))",
        ),
        "audit_log_check": ConstraintContract(
            table="audit_log",
            contype="c",
            columns=("event_type", "entity_type"),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition=(
                "check (event_type = 'detection_completed' and entity_type = "
                "'lot_hist' or event_type = 'agent_run_started' and entity_type ="
                " 'agent_run' or event_type = 'hypothesis_generated' and "
                "entity_type = 'agent_run' or event_type = 'approval_requested' "
                "and entity_type = 'approval' or event_type = 'approval_decided' "
                "and entity_type = 'approval' or event_type = 'action_sent' and "
                "entity_type = 'action' or event_type = 'action_send_failed' and "
                "entity_type = 'action' or event_type = 'agent_run_completed' and"
                " entity_type = 'agent_run' or event_type = 'agent_run_failed' "
                "and entity_type = 'agent_run')"
            ),
        ),
        "audit_log_entity_id_check": ConstraintContract(
            table="audit_log",
            contype="c",
            columns=("entity_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="check (btrim(entity_id) <> '')",
        ),
        "audit_log_pkey": ConstraintContract(
            table="audit_log",
            contype="p",
            columns=("audit_id",),
            referenced_table=None,
            referenced_columns=(),
            on_delete=" ",
            on_update=" ",
            definition="primary key (audit_id)",
        ),
    }
)

EXPECTED_INDEXES: Mapping[str, IndexContract] = MappingProxyType(
    {
        "action_delivery_pkey": IndexContract(
            table="action_delivery",
            unique=True,
            method="btree",
            columns=("action_id", "channel"),
            predicate=None,
            expressions=None,
        ),
        "agent_prediction_pkey": IndexContract(
            table="agent_prediction",
            unique=True,
            method="btree",
            columns=("agent_run_id",),
            predicate=None,
            expressions=None,
        ),
        "agent_prediction_review_pkey": IndexContract(
            table="agent_prediction_review",
            unique=True,
            method="btree",
            columns=("review_id",),
            predicate=None,
            expressions=None,
        ),
        "agent_run_pkey": IndexContract(
            table="agent_run",
            unique=True,
            method="btree",
            columns=("agent_run_id",),
            predicate=None,
            expressions=None,
        ),
        "ux_agent_run_incident_active": IndexContract(
            table="agent_run",
            unique=True,
            method="btree",
            columns=("lot_id", "chamber_id"),
            predicate="((status) = any ((array['running', 'waiting_approval'])))",
            expressions=None,
        ),
        "agent_run_action_pkey": IndexContract(
            table="agent_run_action",
            unique=True,
            method="btree",
            columns=("agent_run_id",),
            predicate=None,
            expressions=None,
        ),
        "ux_agent_run_action_created": IndexContract(
            table="agent_run_action",
            unique=True,
            method="btree",
            columns=("action_id",),
            predicate="((link_role) = 'created')",
            expressions=None,
        ),
        "ux_agent_run_action_incident": IndexContract(
            table="agent_run_action",
            unique=True,
            method="btree",
            columns=("lot_id", "chamber_id"),
            predicate="((link_role) = 'created')",
            expressions=None,
        ),
        "agent_run_alarm_pkey": IndexContract(
            table="agent_run_alarm",
            unique=True,
            method="btree",
            columns=("agent_run_id", "alarm_source", "alarm_id"),
            predicate=None,
            expressions=None,
        ),
        "ux_agent_run_alarm_representative": IndexContract(
            table="agent_run_alarm",
            unique=True,
            method="btree",
            columns=("agent_run_id",),
            predicate="is_representative",
            expressions=None,
        ),
        "agent_tool_call_agent_run_id_call_seq_key": IndexContract(
            table="agent_tool_call",
            unique=True,
            method="btree",
            columns=("agent_run_id", "call_seq"),
            predicate=None,
            expressions=None,
        ),
        "agent_tool_call_pkey": IndexContract(
            table="agent_tool_call",
            unique=True,
            method="btree",
            columns=("tool_call_id",),
            predicate=None,
            expressions=None,
        ),
        "approval_request_action_id_key": IndexContract(
            table="approval_request",
            unique=True,
            method="btree",
            columns=("action_id",),
            predicate=None,
            expressions=None,
        ),
        "approval_request_pkey": IndexContract(
            table="approval_request",
            unique=True,
            method="btree",
            columns=("approval_id",),
            predicate=None,
            expressions=None,
        ),
        "audit_log_pkey": IndexContract(
            table="audit_log",
            unique=True,
            method="btree",
            columns=("audit_id",),
            predicate=None,
            expressions=None,
        ),
    }
)


TABLES_SQL = """/* agent-runtime:tables */
SELECT c.relname AS object_name, c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
ORDER BY c.relname
"""
COLUMNS_SQL = """/* agent-runtime:columns */
SELECT c.relname AS table_name, a.attnum AS ordinal_position,
       a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relname = ANY(%s)
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
CONSTRAINTS_SQL = """/* agent-runtime:constraints */
SELECT t.relname AS table_name, con.conname AS constraint_name,
       con.contype::text AS constraint_type,
       pg_get_constraintdef(con.oid, true) AS definition,
       (SELECT array_agg(a.attname ORDER BY k.ord)
          FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
       ) AS local_columns,
       ref.relname AS referenced_table,
       (SELECT array_agg(a.attname ORDER BY k.ord)
          FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
       ) AS referenced_columns,
       con.confdeltype::text AS on_delete,
       con.confupdtype::text AS on_update
FROM pg_constraint con JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_class ref ON ref.oid = con.confrelid
WHERE n.nspname = 'public' AND t.relname = ANY(%s)
ORDER BY t.relname, con.conname
"""
INDEXES_SQL = """/* agent-runtime:indexes */
SELECT t.relname AS table_name, i.relname AS index_name,
       x.indisunique AS is_unique, am.amname AS method,
       pg_get_indexdef(i.oid) AS definition,
       pg_get_expr(x.indpred, x.indrelid) AS predicate,
       pg_get_expr(x.indexprs, x.indrelid) AS expressions
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_am am ON am.oid = i.relam
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND t.relname = ANY(%s)
ORDER BY t.relname, i.relname
"""
SEQUENCES_SQL = """/* agent-runtime:sequences */
SELECT seq.relname AS sequence_name
FROM pg_class seq
JOIN pg_namespace n ON n.oid = seq.relnamespace
JOIN pg_depend dep ON dep.objid = seq.oid AND dep.deptype IN ('a', 'i')
JOIN pg_class owner_table ON owner_table.oid = dep.refobjid
WHERE n.nspname = 'public' AND seq.relkind = 'S'
  AND owner_table.relname = ANY(%s)
ORDER BY seq.relname
"""
ACTION_COUNT_SQL = "SELECT count(*) AS row_count FROM public.action_history"
ALARM_COUNT_SQL = f"SELECT count(*) AS row_count FROM public.{REFERENCE_VIEW}"
ALL_TABLES_SQL = """SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"""

FORBIDDEN_SQL = re.compile(
    r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|INSERT|COPY|ALTER)\b", re.I
)
IF_NOT_EXISTS = re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.I)
LEGACY_ALARM = re.compile(r"\bfdc_alarm\b", re.I)


class AgentRuntimeError(RuntimeError):
    exit_code = 2
    default_reason_code = "CONTRACT_INVALID"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code or self.default_reason_code


class AgentRuntimeStateError(AgentRuntimeError):
    exit_code = 3
    default_reason_code = "SCHEMA_STATE_INVALID"


class AgentRuntimeArtifactError(AgentRuntimeError):
    exit_code = 5
    default_reason_code = "ARTIFACT_INVALID"


class _RehearsalRollback(Exception):
    pass


@dataclass(frozen=True)
class RuntimeInspection:
    state: str
    inventory: tuple[tuple[str, str], ...]
    signature: Mapping[str, Any] | None
    schema_signature_sha256: str | None


@dataclass(frozen=True)
class RuntimePostcheck:
    signature: Mapping[str, Any]
    schema_signature_sha256: str
    action_history_rows: int
    alarm_event_rows: int


def _strip_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/|--[^\r\n]*", " ", sql, flags=re.S)


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL while preserving single-quoted and dollar-quoted bodies."""

    body = _strip_comments(sql)
    statements: list[str] = []
    current: list[str] = []
    single = False
    dollar_tag: str | None = None
    index = 0
    while index < len(body):
        if dollar_tag is not None:
            if body.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(body[index])
            index += 1
            continue
        character = body[index]
        if character == "'":
            current.append(character)
            if single and index + 1 < len(body) and body[index + 1] == "'":
                current.append("'")
                index += 2
                continue
            single = not single
            index += 1
            continue
        if not single and character == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", body[index:])
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if character == ";" and not single:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
        index += 1
    if single or dollar_tag is not None:
        raise AgentRuntimeError("002 SQL literal이 닫히지 않았습니다")
    if "".join(current).strip():
        raise AgentRuntimeError("002 SQL 마지막 문장에 세미콜론이 없습니다")
    return statements


def load_and_validate_sql(path: Path = MIGRATION_PATH) -> tuple[str, list[str]]:
    try:
        sql = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentRuntimeError("002 migration SQL을 읽을 수 없습니다") from exc
    body = _strip_comments(sql)
    if (
        FORBIDDEN_SQL.search(body)
        or IF_NOT_EXISTS.search(body)
        or LEGACY_ALARM.search(body)
    ):
        raise AgentRuntimeError(
            "002 migration에 금지된 DML/legacy/완화 구문이 있습니다"
        )
    if "LOCK TABLE" in body.upper():
        raise AgentRuntimeError("002 SQL은 transaction lock을 직접 소유할 수 없습니다")
    statements = split_sql_statements(sql)
    counts = Counter(
        "do"
        if statement.upper().startswith("DO ")
        else "table"
        if statement.upper().startswith("CREATE TABLE")
        else "index"
        if statement.upper().startswith("CREATE UNIQUE INDEX")
        else "other"
        for statement in statements
    )
    if counts != Counter({"table": 9, "index": 4, "do": 1}):
        raise AgentRuntimeError("002 migration 객체 수가 계약과 다릅니다")
    normalized = " ".join(body.lower().split())
    if (
        "current_database() not in ('kosa_agent', 'kosa_agent_e2e')" not in normalized
        or "select count(*) from action_history" not in normalized
    ):
        raise AgentRuntimeError("002 SQL 선두 target/action guard가 없습니다")
    return sql, statements


def migration_sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def _require_runtime_target(target: BootstrapTarget) -> None:
    if target.database not in RUNTIME_DATABASES or target.profile != "runtime":
        raise AgentRuntimeStateError(
            "002는 runtime profile에만 적용할 수 있습니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )


def _prepare_transaction(
    connection: Any, target: BootstrapTarget, *, readonly: bool
) -> None:
    _require_runtime_target(target)
    prepare_transaction(
        connection,
        target,
        readonly=readonly,
        acquire_lock=acquire_advisory_lock,
    )


def lock_action_history(connection: Any) -> None:
    connection.exec_driver_sql("LOCK TABLE action_history IN SHARE MODE")


def action_history_count(connection: Any) -> int:
    row = _single_row(
        connection.exec_driver_sql(ACTION_COUNT_SQL), label="action count"
    )
    return int(row["row_count"])


def alarm_event_count(connection: Any) -> int:
    row = _single_row(connection.exec_driver_sql(ALARM_COUNT_SQL), label="alarm count")
    return int(row["row_count"])


def _actual_table_set(connection: Any) -> set[str]:
    return {
        str(row["table_name"])
        for row in _result_rows(connection.exec_driver_sql(ALL_TABLES_SQL))
    }


def validate_prerequisites(connection: Any, target: BootstrapTarget) -> tuple[int, int]:
    _require_runtime_target(target)
    actual = _actual_table_set(connection)
    reference_tables = frozenset({*BASE_TABLES, *REFERENCE_TABLES})
    if not reference_tables.issubset(actual) or actual - reference_tables - set(
        RUNTIME_TABLES
    ):
        raise AgentRuntimeStateError(
            "corrected/reference schema가 runtime 계약과 다릅니다",
            reason_code="MISSING_BASE",
        )
    rows = action_history_count(connection)
    if rows != 0:
        raise AgentRuntimeStateError(
            "runtime action_history가 비어 있지 않습니다",
            reason_code="ACTION_PRESENT",
        )
    assert_final_reference_state(connection)
    return rows, alarm_event_count(connection)


def _v5_execute(connection: Any) -> Callable[..., list[dict[str, Any]]]:
    """CM-3.1 판정기가 기대하는 `(sql, params) -> rows` 어댑터.

    v5 module은 driver를 고르지 않는다. named parameter(`%(view)s`)를 쓰므로 그대로
    넘긴다.
    """

    def run(sql: str, params: Any = None) -> list[dict[str, Any]]:
        return [
            dict(row) for row in _result_rows(connection.exec_driver_sql(sql, params))
        ]

    return run


def assert_final_reference_state(connection: Any) -> None:
    """002를 얹기 전에 **final reference 계보**가 먼저 서 있는지 본다.

    구현은 `V5-CM-3.1`이 만든 정본 판정기를 read-only로 재사용한다. 판정 규칙을 여기서
    다시 쓰면 두 경로가 갈린다. **V4 `postcheck_database()`는 부르지 않는다** —
    그 경로는 R03 11컬럼·V4 View 계약이라 final DB가 거기서 실패한다.

    이 검사가 `_artifact_identity()`가 잃은 001 provenance의 절반을 대신한다. 나머지
    절반은 active manifest의 `applied_migrations` 2원소와 `manifest_sha256`이다.

    ## 왜 columns 3종으로는 부족한가

    1차 구현은 R03 columns·constraints·View columns 셋만 봤다. 그러면 **View 컬럼 이름만
    같은 임의 정의**나 comment drift, `PUBLIC` grant가 있어도 adopt와 marker 발급이
    통과한다(구현리뷰 필수 2). active manifest hash는 *계약 문서*의 identity일 뿐
    이 target의 live View 정의를 증명하지 못한다.

    그래서 CM-3.1의 `read_live_schema()`·`live_signatures()`를 그대로 돌린다. 그 둘은
    `assert_*`를 먼저 통과해야 signature를 내주므로, **non-canonical catalog에서는
    signature 자체가 나오지 않는다.**
    """

    execute = _v5_execute(connection)
    try:
        live = reference_v5.read_live_schema(execute)
        reference_v5.assert_r03_columns(live["r03_columns"])
        reference_v5.assert_r03_constraints(live["r03_constraints"])
        reference_v5.assert_view_columns(live["view_columns"])
        reference_v5.assert_view_identity(str(live["view_definition"]))

        security = live["security"]
        reference_v5.assert_canonical_comments(
            r03_comment=security[reference_v5.R03_TABLE].get("comment"),
            view_comment=security[reference_v5.ALARM_VIEW].get("comment"),
        )
        for row in security.values():
            reference_v5.assert_no_public_grant(row.get("relacl"))

        # signature를 실제로 만들어 본다 — 판정기 전부가 통과해야 값이 나온다.
        reference_v5.live_signatures(live, mode="base_only")

        # **data gate를 완화하지 않는다.**
        #
        # 1차 구현은 `require_final_dataset=False`로 불렀다. R03 행 수만 유연하게
        # 하려던 것인데, CM-3.1 판정기는 그 flag에서 **즉시 반환**한다 — 그 아래
        # TRACE 138·SUMMARY 51 분포와 `null_owner` 검사가 통째로 죽는다.
        # `TRACE=1 · SUMMARY=0 · null_owner=1`인 View도 통과했다(구현리뷰 2차 필수 1).
        #
        # 두 요구는 충돌하지 않는다. R03 행 수 allowlist를 **바깥에서** 고정하면
        # 판정기가 동적 `r03_rows`를 기대 분포에 넣어 주므로 나머지를 전부 볼 수 있다.
        r03_rows = int(
            execute(f"SELECT count(*) AS n FROM public.{reference_v5.R03_TABLE}")[0][
                "n"
            ]
        )
        if r03_rows not in ALLOWED_R03_ROWS:
            # `V5-A-1.4` 적재 전 0, 적재 후 3. 그 사이 값은 적재가 끊긴 상태다.
            raise AgentRuntimeStateError(
                "R03 행 수가 허용 범위 밖입니다",
                reason_code="MISSING_FINAL_REFERENCE",
            )
        reference_v5.assert_view_branches(
            execute,
            r03_rows=r03_rows,
            view_rows=alarm_event_count(connection),
            require_final_dataset=True,
        )
    except (reference_v5.ReferenceV5Error, KeyError, IndexError, TypeError) as exc:
        raise AgentRuntimeStateError(
            "final reference 계약이 서 있지 않습니다",
            reason_code="MISSING_FINAL_REFERENCE",
        ) from exc


def build_schema_signature(connection: Any) -> dict[str, Any]:
    tables = list(RUNTIME_TABLES)
    return {
        "columns": [
            _json_safe(dict(row))
            for row in _result_rows(connection.exec_driver_sql(COLUMNS_SQL, (tables,)))
        ],
        "constraints": [
            _json_safe(dict(row))
            for row in _result_rows(
                connection.exec_driver_sql(CONSTRAINTS_SQL, (tables,))
            )
        ],
        "indexes": [
            _json_safe(dict(row))
            for row in _result_rows(connection.exec_driver_sql(INDEXES_SQL, (tables,)))
        ],
        "sequences": [
            _json_safe(dict(row))
            for row in _result_rows(
                connection.exec_driver_sql(SEQUENCES_SQL, (tables,))
            )
        ],
    }


#: catalog 텍스트를 비교 가능한 하나의 형태로 만든다.
#:
#: PostgreSQL이 돌려주는 `pg_get_constraintdef`·`pg_get_expr`에는 cast 표기와 공백이
#: 섞여 있어(`(status)::text = ANY ((ARRAY['RUNNING'::character varying])::text[])`)
#: 원문 그대로는 비교 기준이 될 수 없다. cast를 지우고 공백을 접어 정규화한다.
#: `::` cast에서 지울 수 있는 **type 이름만** 나열한다.
#:
#: 구현은 `::[a-z_ ]+`였다. 그 패턴은 lowercase 입력에서 type이 아닌 keyword까지
#: 먹는다 — `check (status::text is not null)`이 `check (status)`로 접혀
#: `is null` 버전과 구별되지 않는다. 실제 PostgreSQL 출력은 keyword가 uppercase라
#: 충돌하지 않았지만, **입력 대소문자에 따라 의미가 달라지는 정규화**는 계약이 될 수
#: 없다(구현리뷰 권장 1).
#:
#: 002가 쓰는 type만 열거하고, 모르는 type이 나오면 지우지 않는다.
_CAST_TYPES = (
    "character varying",
    "timestamp with time zone",
    "timestamp without time zone",
    "double precision",
    "numeric",
    "integer",
    "smallint",
    "bigint",
    "boolean",
    "jsonb",
    "json",
    "text",
    "uuid",
    "date",
)
_CAST_PATTERN = re.compile(
    r"::(?:" + "|".join(re.escape(name) for name in _CAST_TYPES) + r")(\[\])?",
    re.IGNORECASE,
)


def normalize_catalog_text(value: str | None) -> str | None:
    """catalog 텍스트를 비교 가능한 하나의 형태로 만든다. **멱등이다.**

    cast는 알려진 type 이름에만 붙는다. 대소문자를 가리지 않으므로 이미 lowercase로
    접힌 문자열을 다시 넣어도 같은 결과가 나온다.
    """

    if value is None:
        return None
    text_value = _CAST_PATTERN.sub("", str(value))
    text_value = re.sub(r"\s+", " ", text_value).strip().lower()
    return text_value.replace("( ", "(").replace(" )", ")")


def _tuple_of(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(str(item) for item in value)


def _index_columns(definition: str) -> tuple[str, ...]:
    """`pg_get_indexdef`에서 대상 컬럼만 뽑는다.

    `WHERE`를 먼저 떼지 않으면 greedy match가 predicate까지 삼켜 컬럼 계약이
    predicate 변조에 딸려 움직인다. 두 축은 분리돼야 한다.
    """

    body = str(definition).split(" WHERE ", 1)[0]
    match = re.search(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*$", body)
    if match is None:
        return ()
    return tuple(part.strip() for part in match.group(1).split(","))


def _validate_columns_contract(columns: Any) -> None:
    if not isinstance(columns, list):
        raise AgentRuntimeStateError("runtime column signature가 잘못됐습니다")
    for table, expected in EXPECTED_TABLE_COLUMNS.items():
        actual = tuple(
            ColumnContract(
                str(row["column_name"]),
                str(row["data_type"]),
                bool(row["nullable"]),
                str(row["column_default"])
                if row.get("column_default") is not None
                else None,
            )
            for row in columns
            if row["table_name"] == table
        )
        if actual != expected:
            raise AgentRuntimeStateError(f"{table} 컬럼 계약이 다릅니다")


def _validate_constraints_contract(constraints: Any) -> None:
    """이름을 key로 **전수 exact** 대조한다.

    `!=` 한 번으로 끝내지 않고 이름 집합을 먼저 가르는 이유는, 추가·누락과 정의 변조가
    서로 다른 사고이고 reason이 갈려야 원인을 찾을 수 있기 때문이다.
    """

    if not isinstance(constraints, list):
        raise AgentRuntimeStateError("runtime constraint signature가 잘못됐습니다")
    actual: dict[str, ConstraintContract] = {}
    for row in constraints:
        name = str(row["constraint_name"])
        if name in actual:
            raise AgentRuntimeStateError("runtime constraint 이름이 중복됐습니다")
        actual[name] = ConstraintContract(
            table=str(row["table_name"]),
            contype=str(row["constraint_type"]),
            columns=_tuple_of(row.get("local_columns")),
            referenced_table=(
                str(row["referenced_table"])
                if row.get("referenced_table") is not None
                else None
            ),
            referenced_columns=_tuple_of(row.get("referenced_columns")),
            on_delete=str(row.get("on_delete") or " "),
            on_update=str(row.get("on_update") or " "),
            definition=normalize_catalog_text(row["definition"]) or "",
        )
    if set(actual) != set(EXPECTED_CONSTRAINTS):
        # 추가·누락 자체가 drift다. extra CHECK 하나로도 계약이 달라진다.
        raise AgentRuntimeStateError("runtime constraint allowlist가 다릅니다")
    for name, expected in EXPECTED_CONSTRAINTS.items():
        if actual[name] != expected:
            raise AgentRuntimeStateError(f"{name} constraint 계약이 다릅니다")


def _validate_legacy_alarm_fk(constraints: Sequence[Mapping[str, Any]]) -> None:
    """legacy alarm FK **0건**을 독립 축으로 다시 센다(WBS 완료 기준).

    `_validate_constraints_contract`가 이미 전수 대조를 하므로 중복처럼 보이지만
    같지 않다. 저쪽은 "계약과 같은가"를, 여기는 "AlarmRef를 물리 FK로 묶지 않았는가"를
    본다. AlarmRef는 `(source, alarm_id)` **값 계약**으로만 저장한다.
    """

    forbidden = {
        "trace_alarm_history",
        "summary_alarm_history",
        "r03_alarm_history",
        "fdc_alarm",
    }
    for row in constraints:
        if str(row["constraint_type"]) != "f":
            continue
        referenced = row.get("referenced_table")
        if referenced is not None and str(referenced) in forbidden:
            raise AgentRuntimeStateError(
                "legacy alarm FK가 있습니다",
                reason_code="LEGACY_ALARM_FK",
            )


def _validate_indexes_contract(indexes: Any) -> None:
    if not isinstance(indexes, list):
        raise AgentRuntimeStateError("runtime index signature가 잘못됐습니다")
    actual: dict[str, IndexContract] = {}
    for row in indexes:
        name = str(row["index_name"])
        actual[name] = IndexContract(
            table=str(row["table_name"]),
            unique=bool(row["is_unique"]),
            method=str(row["method"]),
            columns=_index_columns(row["definition"]),
            predicate=normalize_catalog_text(row.get("predicate")),
            expressions=normalize_catalog_text(row.get("expressions")),
        )
    if set(actual) != set(EXPECTED_INDEXES):
        raise AgentRuntimeStateError("runtime index allowlist가 다릅니다")
    for name, expected in EXPECTED_INDEXES.items():
        if actual[name] != expected:
            raise AgentRuntimeStateError(f"{name} index 계약이 다릅니다")


def _validate_signature_contract(signature: Mapping[str, Any]) -> str:
    _validate_columns_contract(signature.get("columns"))
    constraints = signature.get("constraints")
    _validate_constraints_contract(constraints)
    _validate_legacy_alarm_fk(constraints if isinstance(constraints, list) else ())
    _validate_indexes_contract(signature.get("indexes"))
    sequences = signature.get("sequences")
    if (
        not isinstance(sequences, list)
        or {str(row["sequence_name"]) for row in sequences} != EXPECTED_SEQUENCE_NAMES
    ):
        raise AgentRuntimeStateError("runtime sequence allowlist가 다릅니다")
    return _canonical_hash(signature)


def inspect_database(connection: Any) -> RuntimeInspection:
    """물리 상태를 판정한다. **읽기만 한다.**

    `PARTIAL`과 `DRIFT`를 가른다. 구현은 둘 다 `DRIFT`로 묶었는데 원인이 다르다 —
    `PARTIAL`은 9종 중 일부만 있는 것이고(적용이 중간에 끊겼다), `DRIFT`는 9종이 다
    있는데 계약이 다른 것이다(누가 손댔다). **어느 쪽도 자동 보정하지 않지만** reason이
    갈려야 원인을 찾을 수 있다.
    """

    rows = _result_rows(connection.exec_driver_sql(TABLES_SQL, (list(RUNTIME_TABLES),)))
    inventory = tuple(
        sorted((str(row["object_name"]), str(row["relkind"])) for row in rows)
    )
    if not inventory:
        return RuntimeInspection("ABSENT", (), None, None)
    expected = tuple(sorted((table, "r") for table in RUNTIME_TABLES))
    if inventory != expected:
        names = {name for name, _kind in inventory}
        state = "PARTIAL" if names < set(RUNTIME_TABLES) else "DRIFT"
        return RuntimeInspection(state, inventory, None, None)
    signature = build_schema_signature(connection)
    try:
        signature_hash = _validate_signature_contract(signature)
    except AgentRuntimeStateError:
        return RuntimeInspection("DRIFT", inventory, signature, None)
    return RuntimeInspection("PRESENT", inventory, signature, signature_hash)


def _privilege_violations(connection: Any) -> list[tuple[str, str, str]]:
    violations: list[tuple[str, str, str]] = []
    for table in RUNTIME_TABLES:
        for privilege in TABLE_PRIVILEGES:
            row = _single_row(
                connection.exec_driver_sql(
                    "SELECT has_table_privilege('public', %s, %s) AS allowed",
                    (f"public.{table}", privilege),
                ),
                label="table privilege",
            )
            if row["allowed"] is True:
                violations.append(("table", table, privilege))
    for sequence in EXPECTED_SEQUENCE_NAMES:
        for privilege in SEQUENCE_PRIVILEGES:
            row = _single_row(
                connection.exec_driver_sql(
                    "SELECT has_sequence_privilege('public', %s, %s) AS allowed",
                    (f"public.{sequence}", privilege),
                ),
                label="sequence privilege",
            )
            if row["allowed"] is True:
                violations.append(("sequence", sequence, privilege))
    return violations


def postcheck_database(connection: Any, *, alarm_rows_before: int) -> RuntimePostcheck:
    inspection = inspect_database(connection)
    if (
        inspection.state != "PRESENT"
        or inspection.signature is None
        or inspection.schema_signature_sha256 is None
    ):
        raise AgentRuntimeStateError("002 schema postcheck에 실패했습니다")
    action_rows = action_history_count(connection)
    alarm_rows = alarm_event_count(connection)
    if action_rows != 0 or alarm_rows != alarm_rows_before:
        raise AgentRuntimeStateError("002가 base/action/View 불변식을 위반했습니다")
    if _actual_table_set(connection) != EXPECTED_ALL_TABLES:
        raise AgentRuntimeStateError("runtime table 전체 allowlist가 다릅니다")
    if violations := _privilege_violations(connection):
        raise AgentRuntimeStateError(
            f"PUBLIC 권한이 남았습니다: {len(violations)}건",
            reason_code="PUBLIC_PRIVILEGE_DETECTED",
        )
    return RuntimePostcheck(
        inspection.signature,
        inspection.schema_signature_sha256,
        action_rows,
        alarm_rows,
    )


def execute_schema(connection: Any, statements: Sequence[str]) -> None:
    for statement in statements:
        # ``exec_driver_sql`` passes literal percent signs straight to psycopg,
        # which then mistakes the ``RAISE EXCEPTION ... %`` format marker in
        # the guard block for a DBAPI placeholder.  TextClause compilation
        # escapes the literal for the active PostgreSQL paramstyle.
        connection.execute(text(statement))


def marker_path(database: str, *, root: Path = MARKER_ROOT) -> Path:
    if database not in RUNTIME_DATABASES:
        raise AgentRuntimeArtifactError("runtime marker database가 허용되지 않았습니다")
    return root / f"{FINAL_ARTIFACT_TYPE}.{database}.json"


def receipt_path(database: str, operation_id: str, *, root: Path = REPORT_ROOT) -> Path:
    try:
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise AgentRuntimeArtifactError(
            "runtime receipt operation id가 잘못됐습니다"
        ) from exc
    return root / f"{FINAL_ARTIFACT_TYPE}.{database}.{operation_id}.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentRuntimeArtifactError("runtime artifact를 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise AgentRuntimeArtifactError("runtime artifact는 object여야 합니다")
    scan_for_sensitive_values(payload)
    return payload


def load_final_manifest() -> dict[str, Any]:
    """active Runtime manifest를 **schema 검증한 뒤** 돌려준다.

    검증 없이 읽으면 변조된 manifest의 hash를 marker에 그대로 박게 된다. marker가
    주장하는 것은 "이 DB가 **이 계약**과 같다"이므로, 계약 자체가 먼저 유효해야 한다.
    """

    manifest = _read_json(resolve_bootstrap_manifest_path("runtime", "runtime_clean"))
    try:
        manifest_v3.validate_manifest_schema(
            manifest,
            expected_artifact_type="db_bootstrap",
            expected_profile=RUNTIME_PROFILE,
            expected_stage=RUNTIME_STAGE,
            expected_archive_sha256=manifest_v3.FINAL_ARCHIVE_SHA256,
        )
    except VerificationError as exc:
        raise AgentRuntimeArtifactError(
            "active Runtime manifest가 최종 계약과 다릅니다"
        ) from exc
    lineage = tuple(manifest["applied_migrations"])
    if lineage != EXPECTED_MIGRATION_LINEAGE:
        raise AgentRuntimeArtifactError("Runtime migration lineage가 다릅니다")
    return manifest


def _artifact_identity(target: BootstrapTarget) -> dict[str, Any]:
    """final marker가 주장할 계보 identity.

    **V4 `001` marker를 읽지 않는다.** `V5-CM-1.2`가 `reference_extensions.*`를
    `history/kosa_0813/markers/`로 격리했고, 그 marker의 SHA 입력이던
    `migrations/001_reference_extensions.sql`은 최종 계보가 아니다
    (최종은 `migrations/v5/001_reference_extensions_final.sql`).

    001 provenance는 둘이 대신한다 — 여기의 lineage·manifest hash와,
    `assert_final_reference_state()`의 live R03/View postcheck다.
    """

    _require_runtime_target(target)
    manifest = load_final_manifest()
    return {
        "dataset_epoch": str(manifest["dataset_epoch"]),
        "source_archive_sha256": str(manifest["source_archive_sha256"]),
        "bootstrap_stage": str(manifest["bootstrap_stage"]),
        "manifest_sha256": _canonical_hash(manifest),
    }


def _marker_candidate(
    target: BootstrapTarget,
    result: RuntimePostcheck,
    *,
    migration_sha: str,
    change_reference: str,
    status: str,
    applied_at: str | None = None,
) -> dict[str, Any]:
    """final marker payload.

    `dataset_epoch`·`source_archive_sha256`·`bootstrap_stage`·`manifest_sha256`는
    `_artifact_identity()`가 **schema 검증을 통과한** active manifest에서 낸 값이다.
    """

    identity = _artifact_identity(target)
    now = _timezone_text(datetime.now(UTC))
    return {
        "artifact_type": FINAL_ARTIFACT_TYPE,
        "format_version": FINAL_MARKER_FORMAT_VERSION,
        "task_id": TASK_ID,
        "database": target.database,
        "profile": target.profile,
        "status": status,
        "dataset_epoch": identity["dataset_epoch"],
        "source_archive_sha256": identity["source_archive_sha256"],
        "bootstrap_stage": identity["bootstrap_stage"],
        "migration_id": MIGRATION_ID,
        "migration_sha256": migration_sha,
        "manifest_sha256": identity["manifest_sha256"],
        "schema_signature_sha256": result.schema_signature_sha256,
        "action_history_rows": result.action_history_rows,
        "change_reference": change_reference,
        "applied_at": applied_at or now,
        "recorded_at": now,
    }


MARKER_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "database",
        "profile",
        "status",
        "dataset_epoch",
        "source_archive_sha256",
        "bootstrap_stage",
        "migration_id",
        "migration_sha256",
        "manifest_sha256",
        "schema_signature_sha256",
        "action_history_rows",
        "change_reference",
        "applied_at",
        "recorded_at",
    }
)
MARKER_STATUSES = frozenset({"APPLIED", "VERIFIED_EXISTING"})


def validate_marker(
    payload: Mapping[str, Any], target: BootstrapTarget, *, migration_sha: str
) -> None:
    """final marker 계약. **구 계보 marker는 여기서 걸린다.**

    구 `runtime_clean.<database>.json`은 `artifact_type`이 `runtime_clean`이고
    `task_id`·`bootstrap_stage`·`manifest_sha256`가 없다. 파일명을 바꿔 넣어도 key 집합
    비교에서 거부된다 — `MARKER_STALE`이지 final 증적이 아니다.
    """

    if (
        set(payload) != MARKER_KEYS
        or payload.get("artifact_type") != FINAL_ARTIFACT_TYPE
        or payload.get("format_version") != FINAL_MARKER_FORMAT_VERSION
        or payload.get("task_id") != TASK_ID
    ):
        raise AgentRuntimeArtifactError(
            "runtime marker key/value 계약이 다릅니다",
            reason_code="MARKER_STALE",
        )
    if (
        payload.get("database") != target.database
        or payload.get("profile") != RUNTIME_PROFILE
        or payload.get("migration_id") != MIGRATION_ID
        or payload.get("migration_sha256") != migration_sha
    ):
        raise AgentRuntimeArtifactError("runtime marker provenance가 다릅니다")

    # **epoch·manifest도 provenance다.** 여기서 안 보면 다른 epoch에서 만든 marker가
    # 이름만 맞으면 통과한다. 001 marker를 뺀 자리를 이 두 값이 메운다.
    identity = _artifact_identity(target)
    if (
        payload.get("dataset_epoch") != identity["dataset_epoch"]
        or payload.get("source_archive_sha256") != identity["source_archive_sha256"]
        or payload.get("bootstrap_stage") != identity["bootstrap_stage"]
        or payload.get("manifest_sha256") != identity["manifest_sha256"]
    ):
        raise AgentRuntimeArtifactError("runtime marker epoch/manifest가 다릅니다")

    if (
        payload.get("status") not in MARKER_STATUSES
        or payload.get("action_history_rows") != 0
    ):
        raise AgentRuntimeArtifactError("runtime marker 상태가 다릅니다")
    if not _is_sha256(payload.get("schema_signature_sha256")):
        raise AgentRuntimeArtifactError(
            "runtime marker schema signature가 잘못됐습니다"
        )
    validate_change_reference(str(payload.get("change_reference")))
    scan_for_sensitive_values(payload)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def load_marker(
    target: BootstrapTarget, *, migration_sha: str, root: Path = MARKER_ROOT
) -> dict[str, Any] | None:
    path = marker_path(target.database, root=root)
    if not path.exists():
        return None
    payload = _read_json(path)
    validate_marker(payload, target, migration_sha=migration_sha)
    return payload


def save_marker(
    payload: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    migration_sha: str,
    root: Path = MARKER_ROOT,
) -> None:
    validate_marker(payload, target, migration_sha=migration_sha)
    path = marker_path(target.database, root=root)
    lock_path = root / f".runtime_clean.{target.database}.lock"
    with _exclusive_artifact_lock(lock_path):
        existing = load_marker(target, migration_sha=migration_sha, root=root)
        if existing is not None and existing != payload:
            raise AgentRuntimeArtifactError("기존 runtime marker와 충돌합니다")
        atomic_save_json(path, dict(payload))


RECEIPT_ARTIFACT_TYPE = "agent_runtime_final_receipt"
RECEIPT_FORMAT_VERSION = 1
RECEIPT_STATUSES = frozenset({"STARTED", "COMMITTED", "ABORTED"})
RECEIPT_RESULTS = frozenset({"APPLIED", "VERIFIED_EXISTING"})

#: 모든 status가 공통으로 갖는 key.
_RECEIPT_COMMON_KEYS = frozenset(
    {
        "artifact_type",
        "format_version",
        "task_id",
        "operation_id",
        "attempt",
        "database",
        "profile",
        "status",
        "status_result",
        "migration_id",
        "migration_sha256",
        "adoption_identity",
        "change_reference",
        "started_at",
        "action_history_rows_before",
    }
)
#: status별 추가 key. exact 집합이므로 부족해도 남아도 거부다.
_RECEIPT_STATUS_KEYS = MappingProxyType(
    {
        "STARTED": frozenset(),
        "COMMITTED": frozenset(
            {"committed_at", "action_history_rows_after", "schema_signature_sha256"}
        ),
        "ABORTED": frozenset({"aborted_at", "abort_reason"}),
    }
)


def _receipt_files(database: str, *, root: Path) -> list[Path]:
    """final receipt만 찾는다.

    구현은 구 `agent_runtime.<db>.*.json`을 찾았다. 저장은 final prefix로 하면서
    탐색만 구 prefix였으므로 **저장한 receipt를 한 건도 찾지 못했다**. 방향도 반대였다 —
    계획이 "읽어 증적으로 승격하지 않는다"고 못 박은 폐기 prefix를 뒤지고 있었다
    (구현리뷰 필수 1-1).
    """

    if not root.exists():
        return []
    return sorted(root.glob(f"{FINAL_ARTIFACT_TYPE}.{database}.*.json"))


#: `_finish_receipt`가 쓸 수 있는 중단 사유. 자유 문자열을 받지 않는다.
ABORT_REASONS = frozenset({"APPLY_FAILED", "SUPERSEDED_BEFORE_RETRY"})


def _is_zero_int(value: Any) -> bool:
    """`0`만 참. **`False`는 거부한다** — `bool`은 `int`의 부분형이다."""

    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def assert_adoption_identity(value: Any) -> None:
    """receipt가 담은 manifest identity를 **값까지** 본다.

    key 존재만 확인하면 `dataset_epoch=123`·`manifest_sha256=[]`인 receipt가 통과한다
    (구현리뷰 2차 필수 2). `_artifact_identity()`가 내는 것과 같은 모양이어야 한다.
    """

    if not isinstance(value, Mapping) or set(value) != {
        "dataset_epoch",
        "source_archive_sha256",
        "bootstrap_stage",
        "manifest_sha256",
    }:
        raise AgentRuntimeArtifactError("runtime receipt manifest identity가 다릅니다")
    if value["dataset_epoch"] != manifest_v3.DATASET_EPOCH:
        raise AgentRuntimeArtifactError("runtime receipt epoch가 다릅니다")
    if value["bootstrap_stage"] != RUNTIME_STAGE:
        raise AgentRuntimeArtifactError("runtime receipt stage가 다릅니다")
    for key in ("source_archive_sha256", "manifest_sha256"):
        if not _is_sha256(value[key]):
            raise AgentRuntimeArtifactError(f"runtime receipt {key}가 잘못됐습니다")


def validate_receipt(
    payload: Mapping[str, Any], *, database: str, operation_id: str | None = None
) -> None:
    """receipt exact schema. **key가 아니라 값이 계약이다.**

    없으면 `{"operation_id": "<uuid>"}` 한 필드짜리 임의 JSON도 복구 후보가 된다.
    receipt는 "이 DB에서 이 migration이 commit됐다"는 주장이므로, 그 주장을 이루는
    필드가 전부 있고 **각 값이 계약을 만족해야** 근거가 된다(구현리뷰 1·2차 필수).
    """

    status = payload.get("status")
    if status not in RECEIPT_STATUSES:
        raise AgentRuntimeArtifactError("runtime receipt status가 잘못됐습니다")
    expected = _RECEIPT_COMMON_KEYS | _RECEIPT_STATUS_KEYS[str(status)]
    if set(payload) != expected:
        raise AgentRuntimeArtifactError("runtime receipt key 계약이 다릅니다")
    if (
        payload.get("artifact_type") != RECEIPT_ARTIFACT_TYPE
        or payload.get("format_version") != RECEIPT_FORMAT_VERSION
        or payload.get("task_id") != TASK_ID
        or payload.get("migration_id") != MIGRATION_ID
    ):
        raise AgentRuntimeArtifactError("runtime receipt 계보가 다릅니다")
    if payload.get("database") != database or payload.get("profile") != RUNTIME_PROFILE:
        raise AgentRuntimeArtifactError("runtime receipt 대상이 다릅니다")
    if payload.get("status_result") not in RECEIPT_RESULTS:
        raise AgentRuntimeArtifactError("runtime receipt 적용 방식이 잘못됐습니다")
    if not _is_sha256(payload.get("migration_sha256")):
        raise AgentRuntimeArtifactError("runtime receipt migration sha가 잘못됐습니다")
    try:
        parsed = uuid.UUID(str(payload.get("operation_id")))
    except ValueError as exc:
        raise AgentRuntimeArtifactError(
            "runtime receipt operation id가 잘못됐습니다"
        ) from exc
    if operation_id is not None and str(parsed) != operation_id:
        # 파일명과 payload가 다르면 어느 쪽이 참인지 알 수 없다.
        raise AgentRuntimeArtifactError("runtime receipt 파일명과 내용이 다릅니다")
    attempt = payload.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise AgentRuntimeArtifactError("runtime receipt attempt가 잘못됐습니다")

    # **행 수는 정수 0만이다.** `"not-zero"`·`False`·`0.0`을 전부 거부한다.
    if not _is_zero_int(payload.get("action_history_rows_before")):
        raise AgentRuntimeArtifactError("runtime receipt action 행 수가 잘못됐습니다")
    if status == "COMMITTED" and not _is_zero_int(
        payload.get("action_history_rows_after")
    ):
        raise AgentRuntimeArtifactError("runtime receipt 완료 행 수가 잘못됐습니다")

    assert_adoption_identity(payload.get("adoption_identity"))
    validate_change_reference(str(payload.get("change_reference")))

    for key in ("started_at", *_RECEIPT_STATUS_KEYS[str(status)]):
        if key.endswith("_at") and not _is_timestamp(payload.get(key)):
            raise AgentRuntimeArtifactError(f"runtime receipt {key}가 잘못됐습니다")
    if status == "COMMITTED" and not _is_sha256(payload.get("schema_signature_sha256")):
        raise AgentRuntimeArtifactError(
            "runtime receipt schema signature가 잘못됐습니다"
        )
    if status == "ABORTED" and payload.get("abort_reason") not in ABORT_REASONS:
        raise AgentRuntimeArtifactError("runtime receipt 중단 사유가 잘못됐습니다")
    scan_for_sensitive_values(payload)


def _is_timestamp(value: Any) -> bool:
    """**timezone-aware**만 참.

    naive 문자열을 받으면 두 DB의 시각을 비교할 수 없다. `_timezone_text()`가 쓰기
    쪽에서 이미 강제하지만, 읽기 쪽이 안 보면 손으로 만든 파일이 통과한다.
    """

    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _load_receipts(target: BootstrapTarget, *, root: Path) -> list[dict[str, Any]]:
    """**검증을 통과한** receipt만 돌려준다.

    깨진 payload를 조용히 거르지 않고 거부한다 — 무시하면 "후보가 정확히 1건"이라는
    recovery 계약이 다른 파일의 존재 여부에 따라 흔들린다.
    """

    receipts = []
    for path in _receipt_files(target.database, root=root):
        payload = _read_json(path)
        validate_receipt(
            payload,
            database=target.database,
            operation_id=path.name.split(".")[-2],
        )
        receipts.append(payload)
    return sorted(
        receipts,
        key=lambda item: (int(item.get("attempt", 0)), str(item.get("started_at", ""))),
    )


def _save_receipt(
    payload: Mapping[str, Any], target: BootstrapTarget, *, root: Path
) -> None:
    validate_receipt(payload, database=target.database)
    try:
        atomic_save_json(
            receipt_path(target.database, str(payload["operation_id"]), root=root),
            dict(payload),
        )
    except OSError as exc:
        # 경로·권한 오류가 그대로 올라가면 절대경로가 노출된다.
        raise AgentRuntimeArtifactError("runtime receipt 저장에 실패했습니다") from exc


def _start_receipt(
    target: BootstrapTarget,
    *,
    migration_sha: str,
    change_reference: str,
    adoption_identity: Mapping[str, Any],
    status_result: str,
    root: Path,
) -> dict[str, Any]:
    existing = [
        item
        for item in _load_receipts(target, root=root)
        if item.get("migration_sha256") == migration_sha
        and item.get("change_reference") == change_reference
    ]
    now = _timezone_text(datetime.now(UTC))
    for item in existing:
        if item.get("status") == "STARTED":
            stale = dict(item)
            stale.update(
                status="ABORTED", aborted_at=now, abort_reason="SUPERSEDED_BEFORE_RETRY"
            )
            _save_receipt(stale, target, root=root)
    payload = {
        "artifact_type": RECEIPT_ARTIFACT_TYPE,
        "format_version": RECEIPT_FORMAT_VERSION,
        "task_id": TASK_ID,
        "operation_id": str(uuid.uuid4()),
        "attempt": max((int(item.get("attempt", 0)) for item in existing), default=0)
        + 1,
        "database": target.database,
        "profile": target.profile,
        "status": "STARTED",
        # **적용 방식을 여기에 적는다.** recovery가 그대로 승계해야 fresh `APPLIED`가
        # `VERIFIED_EXISTING`으로 바뀌지 않는다(구현리뷰 필수 1-2).
        "status_result": status_result,
        "migration_id": MIGRATION_ID,
        "migration_sha256": migration_sha,
        "adoption_identity": dict(adoption_identity),
        "change_reference": change_reference,
        "started_at": now,
        "action_history_rows_before": 0,
    }
    _save_receipt(payload, target, root=root)
    return payload


def _finish_receipt(
    receipt: Mapping[str, Any],
    target: BootstrapTarget,
    *,
    result: RuntimePostcheck | None,
    root: Path,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = dict(receipt)
    if result is None:
        payload.pop("committed_at", None)
        payload.pop("action_history_rows_after", None)
        payload.pop("schema_signature_sha256", None)
        payload.update(
            status="ABORTED",
            aborted_at=_timezone_text(datetime.now(UTC)),
            abort_reason=reason or "APPLY_FAILED",
        )
    else:
        payload.pop("aborted_at", None)
        payload.pop("abort_reason", None)
        payload.update(
            status="COMMITTED",
            committed_at=_timezone_text(datetime.now(UTC)),
            action_history_rows_after=result.action_history_rows,
            schema_signature_sha256=result.schema_signature_sha256,
        )
    _save_receipt(payload, target, root=root)
    return payload


def run_preflight(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> RuntimeInspection:
    _require_runtime_target(target)
    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            validate_prerequisites(connection, target)
            inspection = inspect_database(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            if marker is not None and inspection.state != "PRESENT":
                raise AgentRuntimeStateError(
                    "LOST_SCHEMA: marker와 runtime schema가 다릅니다",
                    reason_code="LOST_SCHEMA",
                )
            if (
                inspection.state == "PRESENT"
                and marker is not None
                and marker["schema_signature_sha256"]
                != inspection.schema_signature_sha256
            ):
                raise AgentRuntimeStateError(
                    "runtime marker와 schema signature가 다릅니다", reason_code="DRIFT"
                )
            return inspection
    finally:
        engine.dispose()


def run_rehearsal(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> RuntimePostcheck:
    if target.database != "kosa_agent_e2e":
        raise AgentRuntimeStateError("rehearse는 kosa_agent_e2e에서만 허용됩니다")
    sql, statements = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    engine = engine_factory(target)
    result: RuntimePostcheck | None = None
    try:
        try:
            with engine.connect() as connection, connection.begin():
                _prepare_transaction(connection, target, readonly=False)
                lock_action_history(connection)
                _, alarm_before = validate_prerequisites(connection, target)
                if (
                    inspect_database(connection).state != "ABSENT"
                    or load_marker(
                        target, migration_sha=migration_sha, root=marker_root
                    )
                    is not None
                ):
                    raise AgentRuntimeStateError(
                        "rehearse는 runtime schema/marker가 없는 "
                        "E2E DB에서만 허용됩니다"
                    )
                execute_schema(connection, statements)
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
                raise _RehearsalRollback
        except _RehearsalRollback:
            pass
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            validate_prerequisites(connection, target)
            if inspect_database(connection).state != "ABSENT":
                raise AgentRuntimeStateError(
                    "rehearse rollback 뒤 runtime 객체가 남았습니다"
                )
        if result is None:
            raise AgentRuntimeStateError("rehearse 결과가 없습니다")
        return result
    finally:
        engine.dispose()


def _exact_marker(
    marker: Mapping[str, Any], inspection: RuntimeInspection, *, migration_sha: str
) -> bool:
    return (
        marker.get("migration_sha256") == migration_sha
        and marker.get("schema_signature_sha256") == inspection.schema_signature_sha256
    )


#: 이 runner가 판정하는 전체 상태. CLI reason code와 1:1이다.
RUNTIME_STATES = frozenset(
    {
        "ABSENT",
        "EXACT_UNMARKED",
        "EXACT_MARKED",
        "PARTIAL",
        "DRIFT",
        "LOST_SCHEMA",
    }
)


def classify_state(
    inspection: RuntimeInspection,
    marker: Mapping[str, Any] | None,
    *,
    migration_sha: str,
) -> str:
    """물리 상태 + marker 유무를 **하나의 판정**으로 접는다.

    분기를 `run_apply()` 본문에 인라인으로 두면 판정 자체를 회귀가 잡지 못한다 —
    `EXACT_UNMARKED`를 `ABSENT`로 잘못 접어도 정상 입력에서는 어차피 통과한다
    (CM-1.8 `reference_postcheck_routing`과 같은 이유).
    """

    if inspection.state == "ABSENT":
        # marker가 있는데 schema가 없다 — 절대 자동 재생성하지 않는다.
        return "LOST_SCHEMA" if marker is not None else "ABSENT"
    if inspection.state in {"PARTIAL", "DRIFT"}:
        return inspection.state
    if marker is None:
        return "EXACT_UNMARKED"
    if not _exact_marker(marker, inspection, migration_sha=migration_sha):
        return "DRIFT"
    return "EXACT_MARKED"


def _refuse(state: str) -> None:
    """자동 보정하지 않는 상태를 sanitized reason으로 끝낸다."""

    messages = {
        "PARTIAL": "부분 runtime schema를 자동 보정하지 않습니다",
        "DRIFT": "runtime schema drift를 자동 보정하지 않습니다",
        "LOST_SCHEMA": "marker는 있는데 runtime schema가 없습니다",
    }
    raise AgentRuntimeStateError(messages[state], reason_code=state)


def run_apply(
    target: BootstrapTarget,
    *,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> tuple[str, RuntimePostcheck]:
    """`ABSENT`면 생성하고, `EXACT_UNMARKED`면 **DB를 건드리지 않고 채택**한다.

    `V5-CM-1.6`이 걸어 둔 `FINAL_RUNTIME_MIGRATION_NOT_WIRED` 차단을 이 Task가 해제했다.
    해제한 것은 raise 한 줄이 아니다 — 그 아래 경로가 요구하던 V4 `001` marker 의존을
    같이 끊어야 실제로 성립한다(모듈 docstring).

    ## `EXACT_UNMARKED`는 왜 DB에 쓰지 않는가

    공용 두 Runtime DB에는 9 table이 이미 있다(`V5-CM-1.8` 묶음 3이 22/22로 실측).
    없는 것을 만드는 것이 아니라 **그 상태를 증명**하는 것이 남은 일이므로, DDL을 다시
    치면 오히려 계약이 아니라 사고다. 이 경로에서 발행되는 문장은 `SELECT`·`SET`·
    `LOCK`뿐이며 회귀가 실제 발행 SQL을 수집해 그것을 단언한다.
    """

    _require_runtime_target(target)
    change_reference = validate_change_reference(change_reference)
    sql, statements = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    # manifest 검증을 engine보다 먼저 한다 — 계약이 깨져 있으면 접속할 이유가 없다.
    _artifact_identity(target)
    engine = engine_factory(target)
    receipt: dict[str, Any] | None = None
    result: RuntimePostcheck | None = None
    status = "APPLIED"
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=False)
            # **lock이 먼저다.** action count·schema·marker를 lock 뒤에 다시 읽는다.
            # 그러지 않으면 concurrent INSERT가 판정과 실행 사이로 들어온다.
            lock_action_history(connection)
            _, alarm_before = validate_prerequisites(connection, target)
            inspection = inspect_database(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            state = classify_state(inspection, marker, migration_sha=migration_sha)
            if state in {"PARTIAL", "DRIFT", "LOST_SCHEMA"}:
                _refuse(state)
            if state == "EXACT_MARKED":
                return "NO_OP", postcheck_database(
                    connection, alarm_rows_before=alarm_before
                )
            if state == "EXACT_UNMARKED":
                # DB에 쓰지 않는다. postcheck는 read-only다.
                status = "VERIFIED_EXISTING"
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
                receipt = _start_receipt(
                    target,
                    migration_sha=migration_sha,
                    change_reference=change_reference,
                    adoption_identity=_artifact_identity(target),
                    status_result=status,
                    root=report_root,
                )
            else:
                receipt = _start_receipt(
                    target,
                    migration_sha=migration_sha,
                    change_reference=change_reference,
                    adoption_identity=_artifact_identity(target),
                    status_result=status,
                    root=report_root,
                )
                execute_schema(connection, statements)
                result = postcheck_database(connection, alarm_rows_before=alarm_before)
        if result is None or receipt is None:
            raise AgentRuntimeStateError("runtime apply 결과가 없습니다")
        # **receipt 먼저, marker 마지막.** marker는 "commit된 사실"의 증명서이므로
        # commit·postcheck·committed receipt 뒤에만 나온다.
        receipt = _finish_receipt(receipt, target, result=result, root=report_root)
        save_marker(
            _marker_candidate(
                target,
                result,
                migration_sha=migration_sha,
                change_reference=change_reference,
                status=status,
                applied_at=str(receipt.get("committed_at")),
            ),
            target,
            migration_sha=migration_sha,
            root=marker_root,
        )
        return status, result
    except Exception:
        if receipt is not None and receipt.get("status") == "STARTED":
            _finish_receipt(receipt, target, result=None, root=report_root)
        raise
    finally:
        engine.dispose()


def run_recover_marker(
    target: BootstrapTarget,
    *,
    change_reference: str,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
    report_root: Path = REPORT_ROOT,
) -> tuple[str, RuntimePostcheck]:
    """commit은 됐는데 marker 쓰기가 실패한 경우만 되살린다.

    DB는 건드리지 않는다. **committed receipt가 정확히 1건**이고 그 receipt의 schema
    signature가 live와 같을 때만 marker를 쓴다. 그 조건이 없으면 "무엇을 증명하는지"
    모르는 marker가 나온다.
    """

    _require_runtime_target(target)
    change_reference = validate_change_reference(change_reference)
    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    adoption = _artifact_identity(target)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            validate_prerequisites(connection, target)
            inspection = inspect_database(connection)
            if load_marker(target, migration_sha=migration_sha, root=marker_root):
                raise AgentRuntimeArtifactError("marker가 이미 있습니다")
            if inspection.state != "PRESENT":
                _refuse("DRIFT" if inspection.state != "ABSENT" else "LOST_SCHEMA")
            candidates = [
                item
                for item in _load_receipts(target, root=report_root)
                if item.get("migration_sha256") == migration_sha
                and item.get("adoption_identity") == adoption
                and item.get("status") == "COMMITTED"
                # **요청한 변경과 같은 receipt만 본다.** 빠뜨리면 다른 변경 건의
                # committed receipt로 marker를 발급하게 된다.
                and item.get("change_reference") == change_reference
                and item.get("schema_signature_sha256")
                == inspection.schema_signature_sha256
            ]
            if len(candidates) != 1:
                raise AgentRuntimeArtifactError(
                    "복구 receipt 후보는 정확히 1건이어야 합니다"
                )
            result = postcheck_database(
                connection, alarm_rows_before=alarm_event_count(connection)
            )
            receipt = candidates[0]
        save_marker(
            _marker_candidate(
                target,
                result,
                migration_sha=migration_sha,
                change_reference=change_reference,
                # **적용 방식을 receipt에서 그대로 승계한다.** fallback을 두면
                # fresh `APPLIED` 뒤 marker만 실패한 경우가 `VERIFIED_EXISTING`으로
                # 바뀌어 실제 적용 방식을 잃는다(구현리뷰 필수 1-2).
                status=str(receipt["status_result"]),
                applied_at=str(receipt.get("committed_at")),
            ),
            target,
            migration_sha=migration_sha,
            root=marker_root,
        )
        return "RECOVERED", result
    finally:
        engine.dispose()


def run_verify(
    target: BootstrapTarget,
    *,
    engine_factory: Callable[[BootstrapTarget], Engine] = _engine_for,
    marker_root: Path = MARKER_ROOT,
) -> tuple[str, RuntimePostcheck]:
    """live schema와 발급된 marker가 같은지 본다. **read-only다.**

    `run_preflight()`와 다른 점은 marker를 **필수**로 본다는 것이다. preflight는
    marker가 없는 상태(`EXACT_UNMARKED`)도 정상 보고이지만, verify는 실패로 본다.
    """

    _require_runtime_target(target)
    sql, _ = load_and_validate_sql()
    migration_sha = migration_sha256(sql)
    engine = engine_factory(target)
    try:
        with engine.connect() as connection, connection.begin():
            _prepare_transaction(connection, target, readonly=True)
            _, alarm_before = validate_prerequisites(connection, target)
            inspection = inspect_database(connection)
            marker = load_marker(target, migration_sha=migration_sha, root=marker_root)
            state = classify_state(inspection, marker, migration_sha=migration_sha)
            if state != "EXACT_MARKED":
                raise AgentRuntimeStateError(
                    "live schema와 marker가 일치하지 않습니다",
                    reason_code=state if state in RUNTIME_STATES else "DRIFT",
                )
            return state, postcheck_database(connection, alarm_rows_before=alarm_before)
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5-CM-3.2 Runtime 002 migration")
    parser.add_argument("--database", choices=sorted(ALLOWED_DATABASES))
    parser.add_argument("--confirm-target")
    parser.add_argument("--change-ref")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--recover-marker", action="store_true")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    """mode는 **하나만** 명시한다. mutation을 암묵적 기본값으로 두지 않는다."""

    mode = resolve_exclusive_mode(
        {
            "preflight": args.preflight,
            "rehearse": args.rehearse,
            "apply": args.apply,
            "verify": args.verify,
            "recover": args.recover_marker,
        },
        default_mode="",
        mutually_exclusive_message="runtime mode는 하나만 선택해야 합니다",
    )
    if not mode:
        # 구현은 mode 누락을 `apply`로 접었다. mutation이 기본값이면 오타 한 번으로
        # 공용 DB에 쓰기가 돌 수 있다. 명시하지 않으면 아무것도 하지 않는다.
        raise AgentRuntimeError(
            "runtime mode를 하나 명시해야 합니다 "
            "(--preflight/--rehearse/--apply/--verify/--recover-marker)"
        )
    return mode


def assert_runtime_database(database: str | None) -> str:
    """**parser 직후 경계.** evaluation·임의 DB를 여기서 끝낸다.

    `load_dotenv`·`load_bootstrap_target`·engine보다 앞이다. 뒤에 두면 거부되는
    입력에도 자격증명을 읽고 connection을 만들려 시도한 뒤에야 멈춘다 — 계획 §6이
    "connector 이전 경계"라고 적은 자리다.
    """

    if database is None:
        raise AgentRuntimeError("--database가 필요합니다")
    if database not in RUNTIME_DATABASES:
        raise AgentRuntimeStateError(
            "002는 runtime profile에만 적용할 수 있습니다",
            reason_code="PROFILE_NOT_ALLOWED",
        )
    return database


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        mode = resolve_mode(args)
        database = assert_runtime_database(args.database)
        # 여기서부터만 자격증명을 읽는다.
        load_dotenv(REPOSITORY_ROOT / ".env", override=False)
        target = load_bootstrap_target(database)
        if mode == "preflight":
            inspection = run_preflight(target)
            print(
                f"RUNTIME_PREFLIGHT database={target.database} "
                f"state={inspection.state}"
            )
            return 0
        if mode == "verify":
            state, result = run_verify(target)
            print(
                f"RUNTIME_VERIFY_OK database={target.database} state={state} "
                f"action_rows={result.action_history_rows}"
            )
            return 0
        if args.confirm_target != target.database:
            raise AgentRuntimeError("--confirm-target이 대상 database와 다릅니다")
        if mode == "rehearse":
            result = run_rehearsal(target)
            print(
                f"RUNTIME_REHEARSAL_OK database={target.database} tables=9 "
                f"action_rows={result.action_history_rows}"
            )
            return 0
        if not args.change_ref:
            raise AgentRuntimeError("apply/recover에는 --change-ref가 필요합니다")
        runner = run_recover_marker if mode == "recover" else run_apply
        status, result = runner(target, change_reference=args.change_ref)
        print(
            f"RUNTIME_{status} database={target.database} tables=9 "
            f"action_rows={result.action_history_rows}"
        )
        return 0
    except (
        AgentRuntimeError,
        MutationRuntimeError,
        ReferenceExtensionError,
        VerificationError,
        TargetValidationError,
    ) as exc:
        fallback_reason = (
            "TARGET_VALIDATION_FAILED"
            if isinstance(exc, TargetValidationError)
            else "CONTRACT_INVALID"
        )
        reason = getattr(
            exc,
            "reason_code",
            getattr(exc, "code", fallback_reason),
        )
        print(
            f"RUNTIME_FAIL database={args.database or 'none'} reason={reason}",
            file=sys.stderr,
        )
        return getattr(exc, "exit_code", 2)
    except SQLAlchemyError:
        print(
            f"RUNTIME_FAIL database={args.database or 'none'} "
            "reason=CONNECT_OR_QUERY_FAILED",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
