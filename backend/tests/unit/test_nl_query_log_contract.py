"""Text2SQL QueryLog의 DB·Pydantic 4상태 계약을 고정한다."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analytics.schemas import NlQueryLogItem, NlQueryOutcome

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "001_reference_extensions.sql"
)


def _item(outcome: NlQueryOutcome, **overrides: object) -> NlQueryLogItem:
    values: dict[str, object] = {
        "nl_query_log_id": 1,
        "asked_at": datetime(2026, 8, 17, tzinfo=UTC),
        "question": "알람 수를 알려줘",
        "generated_sql": None,
        "outcome": outcome,
        "is_valid": False,
        "is_rejected": False,
        "reject_reason": None,
        "row_cnt": None,
        "latency_ms": 10,
        "error_msg": None,
    }
    values.update(overrides)
    return NlQueryLogItem.model_validate(values)


@pytest.mark.parametrize(
    ("outcome", "fields"),
    [
        (
            NlQueryOutcome.SUCCESS,
            {"is_valid": True, "row_cnt": 0},
        ),
        (
            NlQueryOutcome.POLICY_REJECTED,
            {"is_rejected": True, "reject_reason": "허용되지 않은 table"},
        ),
        (
            NlQueryOutcome.VALIDATION_FAILED,
            {"error_msg": "SQL AST 검증 실패"},
        ),
        (
            NlQueryOutcome.DB_ERROR,
            {"is_valid": True, "error_msg": "실행 실패"},
        ),
    ],
)
def test_valid_outcome_states(
    outcome: NlQueryOutcome, fields: dict[str, object]
) -> None:
    assert _item(outcome, **fields).outcome is outcome


@pytest.mark.parametrize(
    ("outcome", "fields"),
    [
        (NlQueryOutcome.SUCCESS, {"is_valid": True, "row_cnt": None}),
        (
            NlQueryOutcome.POLICY_REJECTED,
            {"is_valid": True, "is_rejected": True, "reject_reason": "정책"},
        ),
        (
            NlQueryOutcome.POLICY_REJECTED,
            {"is_rejected": True, "reject_reason": "   "},
        ),
        (NlQueryOutcome.VALIDATION_FAILED, {"error_msg": None}),
        (NlQueryOutcome.DB_ERROR, {"is_valid": False, "error_msg": "DB"}),
        (
            NlQueryOutcome.SUCCESS,
            {"is_valid": True, "row_cnt": 1, "error_msg": "unexpected"},
        ),
    ],
)
def test_inconsistent_outcome_states_are_rejected(
    outcome: NlQueryOutcome, fields: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="outcome"):
        _item(outcome, **fields)


@pytest.mark.parametrize(
    ("outcome", "valid_fields", "blank_field"),
    [
        (NlQueryOutcome.SUCCESS, {"is_valid": True, "row_cnt": 0}, "reject_reason"),
        (NlQueryOutcome.SUCCESS, {"is_valid": True, "row_cnt": 0}, "error_msg"),
        (
            NlQueryOutcome.POLICY_REJECTED,
            {"is_rejected": True, "reject_reason": "정책"},
            "reject_reason",
        ),
        (
            NlQueryOutcome.POLICY_REJECTED,
            {"is_rejected": True, "reject_reason": "정책"},
            "error_msg",
        ),
        (
            NlQueryOutcome.VALIDATION_FAILED,
            {"error_msg": "검증 실패"},
            "reject_reason",
        ),
        (
            NlQueryOutcome.VALIDATION_FAILED,
            {"error_msg": "검증 실패"},
            "error_msg",
        ),
        (
            NlQueryOutcome.DB_ERROR,
            {"is_valid": True, "error_msg": "실행 실패"},
            "reject_reason",
        ),
        (
            NlQueryOutcome.DB_ERROR,
            {"is_valid": True, "error_msg": "실행 실패"},
            "error_msg",
        ),
    ],
)
def test_empty_string_never_substitutes_for_database_null_contract(
    outcome: NlQueryOutcome,
    valid_fields: dict[str, object],
    blank_field: str,
) -> None:
    fields = {**valid_fields, blank_field: ""}

    with pytest.raises(ValidationError, match="outcome"):
        _item(outcome, **fields)


@pytest.mark.parametrize(
    ("outcome", "fields"),
    [
        (NlQueryOutcome.POLICY_REJECTED, {"is_rejected": True}),
        (NlQueryOutcome.VALIDATION_FAILED, {}),
        (NlQueryOutcome.DB_ERROR, {"is_valid": True}),
    ],
)
def test_required_reason_null_is_rejected(
    outcome: NlQueryOutcome, fields: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="outcome"):
        _item(outcome, **fields)


def _database_check_accepts(
    outcome: NlQueryOutcome,
    *,
    reject_reason: str | None,
    error_msg: str | None,
    row_cnt: int | None,
) -> bool:
    """Evaluate the NULL-safe five-field CHECK contract as PostgreSQL does."""

    reject_present = (reject_reason or "").strip() != ""
    error_present = (error_msg or "").strip() != ""
    flags = {
        NlQueryOutcome.SUCCESS: (True, False),
        NlQueryOutcome.POLICY_REJECTED: (False, True),
        NlQueryOutcome.VALIDATION_FAILED: (False, False),
        NlQueryOutcome.DB_ERROR: (True, False),
    }
    is_valid, is_rejected = flags[outcome]
    rules = {
        NlQueryOutcome.SUCCESS: (
            is_valid
            and not is_rejected
            and reject_reason is None
            and error_msg is None
            and row_cnt is not None
        ),
        NlQueryOutcome.POLICY_REJECTED: (
            not is_valid
            and is_rejected
            and reject_present
            and error_msg is None
            and row_cnt is None
        ),
        NlQueryOutcome.VALIDATION_FAILED: (
            not is_valid
            and not is_rejected
            and reject_reason is None
            and error_present
            and row_cnt is None
        ),
        NlQueryOutcome.DB_ERROR: (
            is_valid
            and not is_rejected
            and reject_reason is None
            and error_present
            and row_cnt is None
        ),
    }
    return rules[outcome]


def test_database_check_is_null_safe_and_matches_dto_for_all_reason_states() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    assert sql.count("coalesce(btrim(reject_reason), '') <> ''") == 1
    assert sql.count("coalesce(btrim(error_msg), '') <> ''") == 2
    assert "AND btrim(reject_reason) <> ''" not in sql
    assert "AND btrim(error_msg) <> ''" not in sql

    reason_values: tuple[str | None, ...] = (None, "", "  ", "x")
    row_values: tuple[int | None, ...] = (None, 0)
    flags = {
        NlQueryOutcome.SUCCESS: {"is_valid": True, "is_rejected": False},
        NlQueryOutcome.POLICY_REJECTED: {
            "is_valid": False,
            "is_rejected": True,
        },
        NlQueryOutcome.VALIDATION_FAILED: {
            "is_valid": False,
            "is_rejected": False,
        },
        NlQueryOutcome.DB_ERROR: {"is_valid": True, "is_rejected": False},
    }

    for outcome, reject_reason, error_msg, row_cnt in product(
        NlQueryOutcome, reason_values, reason_values, row_values
    ):
        database_accepts = _database_check_accepts(
            outcome,
            reject_reason=reject_reason,
            error_msg=error_msg,
            row_cnt=row_cnt,
        )
        try:
            _item(
                outcome,
                **flags[outcome],
                reject_reason=reject_reason,
                error_msg=error_msg,
                row_cnt=row_cnt,
            )
            dto_accepts = True
        except ValidationError:
            dto_accepts = False

        assert dto_accepts is database_accepts, (
            outcome,
            reject_reason,
            error_msg,
            row_cnt,
        )


def test_json_schema_exposes_exact_outcome_enum() -> None:
    schema = NlQueryLogItem.model_json_schema()
    outcome_ref = schema["properties"]["outcome"]["$ref"].split("/")[-1]

    assert schema["$defs"][outcome_ref]["enum"] == [
        "SUCCESS",
        "POLICY_REJECTED",
        "VALIDATION_FAILED",
        "DB_ERROR",
    ]
