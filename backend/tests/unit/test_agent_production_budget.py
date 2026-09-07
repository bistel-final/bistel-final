"""V5-C-7.1: new production budgets bind once; legacy/U10 policy is preserved."""

import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent import rehydration, repository, tools
from app.agent.investigation_budget import (
    DEVELOPMENT_WIDE,
    PRODUCTION_WIDE_V1,
    RUN_PROFILE_KEY,
    STANDARD,
    persisted_profile,
    profile_for_new_run,
    resolve_run_budget,
)
from app.agent.state import ToolBudget
from app.common.enums import RunStatus
from tests.unit import test_agent_rehydration as restore_fixture
from tests.unit import test_agent_repository as repository_fixture
from tests.unit import test_agent_run_guard as start_fixture

PROFILE = "PRODUCTION_WIDE_V1"


def _counts(by_tool=None, *, profile=PROFILE, level=3):
    counts = by_tool or {}
    return repository.ToolBudgetCounts(
        total=sum(counts.values()),
        by_tool=counts,
        pending_reservations=0,
        autonomy_level=level,
        investigation_budget_profile=profile,
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_new_run_binding_and_legacy_resolution_are_distinct(monkeypatch, level):
    fixture = start_fixture._Harness(monkeypatch)
    fixture.start(autonomy_level=level)
    expected = PROFILE if level == 3 else None
    assert fixture.commands[0].investigation_budget_profile == expected
    assert profile_for_new_run(level) == expected
    assert persisted_profile(level, None) is None
    assert persisted_profile(level, {"unrelated": "legacy"}) is None
    assert resolve_run_budget(level, None) == (STANDARD if level == 3 else None)


def test_production_profile_is_versioned_immutable_and_distinct_from_development():
    assert resolve_run_budget(3, PROFILE) is PRODUCTION_WIDE_V1
    assert PRODUCTION_WIDE_V1 != DEVELOPMENT_WIDE
    assert (
        PRODUCTION_WIDE_V1.read_cap,
        PRODUCTION_WIDE_V1.selector_cap,
        PRODUCTION_WIDE_V1.same_tool_cap,
        PRODUCTION_WIDE_V1.send_budget,
        PRODUCTION_WIDE_V1.guard_rejection_cap,
    ) == (24, 28, 8, 2, 2)
    with pytest.raises(FrozenInstanceError):
        PRODUCTION_WIDE_V1.read_cap = 25
    with pytest.raises(ValueError, match="INVESTIGATION_BUDGET_PROFILE_INVALID"):
        replace(PRODUCTION_WIDE_V1, same_tool_cap=9)


@pytest.mark.parametrize(
    "value", [None, "UNKNOWN", "DEVELOPMENT_WIDE", "STANDARD", 24, {}]
)
def test_present_invalid_metadata_never_falls_back_to_legacy(value):
    with pytest.raises(ValueError, match="INVESTIGATION_BUDGET_PROFILE_INVALID"):
        persisted_profile(3, {RUN_PROFILE_KEY: value})


@pytest.mark.parametrize("level", [1, 2])
def test_wide_profile_cannot_change_level_one_or_two(level):
    with pytest.raises(ValueError, match="INVESTIGATION_BUDGET_PROFILE_INVALID"):
        persisted_profile(level, {RUN_PROFILE_KEY: PROFILE})
    with pytest.raises(repository.RepositoryContractError):
        repository._validate_create_command(
            repository_fixture._command(
                autonomy_level=level, investigation_budget_profile=PROFILE
            )
        )


def test_profile_is_in_the_initial_insert_and_member_audit_transaction(monkeypatch):
    captured = {}
    events = []

    def insert(connection, statement, parameters):
        assert statement is repository._INSERT_RUN
        events.append("run")
        captured.update(parameters)
        return parameters

    connection = SimpleNamespace(
        in_transaction=lambda: True,
        execute=lambda *_args: events.append("member"),
    )
    monkeypatch.setattr(repository, "_insert_one", insert)
    monkeypatch.setattr(repository, "_run_row", lambda row: row)
    monkeypatch.setattr(
        repository, "append_audit_log", lambda *_args: events.append("audit")
    )
    repository.create_agent_run(
        connection,
        repository_fixture._command(
            autonomy_level=3, investigation_budget_profile=PROFILE
        ),
    )
    assert json.loads(captured["evidence"]) == {RUN_PROFILE_KEY: PROFILE}
    assert events == ["run", "member", "member", "audit"]


@pytest.mark.parametrize("evidence", [None, {}, {RUN_PROFILE_KEY: PROFILE}])
def test_locked_db_profile_drives_ledger_snapshot(evidence):
    statements = []

    def execute(statement, _parameters):
        statements.append(statement)
        return SimpleNamespace(
            one_or_none=lambda: SimpleNamespace(autonomy_level=3, evidence=evidence),
            all=lambda: [],
        )

    connection = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    counts = repository.count_tool_calls_for_budget(connection, "RUN-1")
    budget = tools._budget_snapshot(counts)
    expected = PROFILE if evidence else None
    assert budget.investigation_budget_profile == expected
    assert budget.max_calls == (26 if expected else 10)
    assert statements == [repository._LOCK_RUN, repository._SELECT_TOOL_CALLS]
    assert "evidence" in str(statements[0]) and "FOR UPDATE" in str(statements[0])


def test_invalid_db_profile_fails_before_reading_or_reserving_any_tool():
    statements = []

    def execute(statement, _parameters):
        statements.append(statement)
        return SimpleNamespace(
            one_or_none=lambda: SimpleNamespace(
                autonomy_level=3, evidence={RUN_PROFILE_KEY: None}
            )
        )

    connection = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    with pytest.raises(
        repository.RepositoryContractError, match="INVESTIGATION_BUDGET_PROFILE_INVALID"
    ):
        repository.count_tool_calls_for_budget(connection, "RUN-1")
    assert statements == [repository._LOCK_RUN]


def test_production_read_and_per_tool_caps_reserve_exactly_two_sends():
    eight = {"get_fdc_summary": 4, "search_documents": 4}
    assert tools._budget_block_code(_counts(eight), "get_fdc_summary") is None
    assert (
        tools._budget_block_code(_counts(eight, profile=None), "get_fdc_summary")
        == "TOOL_BUDGET_RESERVED"
    )
    assert (
        tools._budget_block_code(_counts({"search_documents": 7}), "search_documents")
        is None
    )
    assert (
        tools._budget_block_code(_counts({"search_documents": 8}), "search_documents")
        == "TOOL_RETRY_EXHAUSTED"
    )
    full_reads = {
        "get_fdc_summary": 8,
        "search_documents": 8,
        "get_chamber_parameter_history": 8,
    }
    assert (
        tools._budget_block_code(_counts(full_reads), "get_metrology_result")
        == "TOOL_BUDGET_RESERVED"
    )
    assert tools._budget_block_code(_counts(full_reads), "send_action") is None
    assert (
        tools._budget_block_code(
            _counts({**full_reads, "send_action": 1}), "send_action"
        )
        is None
    )
    assert (
        tools._budget_block_code(
            _counts({**full_reads, "send_action": 2}), "send_action"
        )
        == "TOOL_BUDGET_EXHAUSTED"
    )
    assert (
        tools._budget_block_code(_counts({"send_action": 2}), "send_action")
        == "TOOL_SEND_ACTION_LIMIT"
    )


def test_actual_executor_uses_locked_profile_and_does_not_reserve_ninth_same_tool(
    monkeypatch,
):
    counts = _counts({"search_documents": 7})
    events = []

    @contextmanager
    def transactions():
        events.append("begin")
        yield object()

    monkeypatch.setattr(tools, "count_tool_calls_for_budget", lambda *_args: counts)
    monkeypatch.setattr(
        tools, "reserve_tool_call", lambda *_args, **_kwargs: events.append("reserve")
    )
    executor = tools.AuditedToolExecutor(
        transactions=transactions,
        boundary=tools.ToolBoundary(
            fdc_summary=lambda _: None,
            equipment_context=lambda _: None,
            document_search=lambda _: None,
        ),
        deadline_runner=None,
    )
    executor._reserve_within_budget(
        agent_run_id="RUN-1", tool_name="search_documents", request={"query": "check"}
    )
    counts = _counts({"search_documents": 8})
    with pytest.raises(tools.ToolBudgetBlocked, match="TOOL_RETRY_EXHAUSTED") as caught:
        executor._reserve_within_budget(
            agent_run_id="RUN-1",
            tool_name="search_documents",
            request={"query": "another"},
        )
    assert caught.value.budget.investigation_budget_profile == PROFILE
    assert events == ["begin", "reserve", "begin"]


@pytest.mark.parametrize(
    "incoming", [{}, {"answer": "done"}, {RUN_PROFILE_KEY: PROFILE}]
)
def test_terminal_replacement_preserves_the_immutable_profile(monkeypatch, incoming):
    current = SimpleNamespace(autonomy_level=3, evidence={RUN_PROFILE_KEY: PROFILE})
    captured = {}
    monkeypatch.setattr(repository, "lock_agent_run", lambda *_args: current)
    monkeypatch.setattr(repository, "_run_row", lambda row: row)
    monkeypatch.setattr(repository, "append_audit_log", lambda *_args: None)

    def execute(_statement, parameters):
        captured.update(parameters)
        return SimpleNamespace(one_or_none=lambda: parameters)

    connection = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    repository.finish_agent_run(
        connection, "RUN-1", RunStatus.COMPLETED, evidence=incoming
    )
    assert json.loads(captured["evidence"])[RUN_PROFILE_KEY] == PROFILE


@pytest.mark.parametrize(
    "old,incoming", [(PROFILE, None), (PROFILE, "UNKNOWN"), (None, PROFILE)]
)
def test_existing_policy_cannot_be_cleared_replaced_or_added_to_legacy(
    monkeypatch, old, incoming
):
    current = SimpleNamespace(
        autonomy_level=3, evidence={} if old is None else {RUN_PROFILE_KEY: old}
    )
    monkeypatch.setattr(repository, "lock_agent_run", lambda *_args: current)
    with pytest.raises(
        repository.RepositoryConflict, match="INVESTIGATION_BUDGET_PROFILE_IMMUTABLE"
    ):
        repository.finish_agent_run(
            repository_fixture._Connection(),
            "RUN-1",
            RunStatus.FAILED,
            evidence={RUN_PROFILE_KEY: incoming},
        )
    with pytest.raises(
        repository.RepositoryContractError, match="ACTION_PROVENANCE_RESERVED"
    ):
        repository.merge_run_action_provenance(
            repository_fixture._Connection(),
            "RUN-1",
            terminal_evidence={RUN_PROFILE_KEY: incoming},
        )


def test_legacy_checkpoint_bytes_omit_new_optional_metadata():
    legacy = ToolBudget(used=0).model_dump(mode="json")
    assert RUN_PROFILE_KEY not in legacy
    assert ToolBudget.model_validate(legacy).model_dump(mode="json") == legacy
    wide = ToolBudget(max_calls=26, used=0, investigation_budget_profile=PROFILE)
    assert wide.model_dump(mode="json")[RUN_PROFILE_KEY] == PROFILE
    with pytest.raises(ValidationError, match="INVESTIGATION_BUDGET_MISMATCH"):
        ToolBudget(max_calls=10, used=0, investigation_budget_profile=PROFILE)


def test_wide_rehydration_retains_db_bound_profile_and_legacy_still_reads(monkeypatch):
    snapshot = restore_fixture._snapshot()
    snapshot.tool_budget = ToolBudget(
        max_calls=26, used=0, investigation_budget_profile=PROFILE
    )
    state = restore_fixture._wire(monkeypatch, snapshot=snapshot, autonomy_level=3)
    state.run.evidence[RUN_PROFILE_KEY] = PROFILE
    payload = rehydration.build_rehydrated_state(object(), "RUN-1")
    assert payload["tool_budget"].investigation_budget_profile == PROFILE
    assert payload["tool_budget"].max_calls == 26
    restore_fixture._wire(monkeypatch, autonomy_level=3)
    assert (
        rehydration.build_rehydrated_state(object(), "RUN-1")[
            "tool_budget"
        ].investigation_budget_profile
        is None
    )


@pytest.mark.parametrize(
    "mismatch", ["missing_snapshot", "missing_db", "null_db", "level_two"]
)
def test_rehydration_cannot_promote_or_downgrade_a_bound_profile(monkeypatch, mismatch):
    snapshot = restore_fixture._snapshot()
    snapshot.tool_budget = ToolBudget(
        max_calls=26, used=0, investigation_budget_profile=PROFILE
    )
    if mismatch == "missing_snapshot":
        snapshot.tool_budget = ToolBudget(used=0)
    state = restore_fixture._wire(
        monkeypatch,
        snapshot=snapshot,
        autonomy_level=2 if mismatch == "level_two" else 3,
    )
    if mismatch != "missing_db":
        state.run.evidence[RUN_PROFILE_KEY] = None if mismatch == "null_db" else PROFILE
    with pytest.raises(
        rehydration.RehydrationError, match="REHYDRATE_PROVENANCE_MISMATCH"
    ):
        rehydration.build_rehydrated_state(object(), "RUN-1")
