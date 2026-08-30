"""`V5-C-5.3` command boundary의 격리 PostgreSQL 2회차 회귀."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import action_store as action_store_module  # noqa: E402
from app.agent import checkpoint as checkpoint_module  # noqa: E402
from app.agent.graph import build_agent_graph  # noqa: E402
from app.agent.runtime_composition import (  # noqa: E402
    AgentRuntime,
    RuntimeResources,
)
from app.agent.state import ActionDecision  # noqa: E402
from app.common.enums import ActionCode, RunStatus, Severity  # noqa: E402
from scripts import run_pending_incidents as runner  # noqa: E402
from tests.unit import test_agent_graph_container as graph_fixture  # noqa: E402

pytestmark = pytest.mark.container

APP_ROLE = "kosa_app"
APP_PASSWORD = "isolated-app-password"


class _WarningPorts(graph_fixture._AssemblyPorts):
    """실제 action UoW가 EMAIL delivery를 만들게 하는 local business port."""

    def decide_action(self, _route: Any) -> ActionDecision:
        self.calls.append("decide_action")
        return ActionDecision(
            action=ActionCode.WARNING,
            severity=Severity.MEDIUM,
            requires_approval=False,
            matched_rule="TRACE_OOS",
            policy_version="ACTION-POLICY-V1",
        )


def _grant_app_role(super_engine: Any) -> Any:
    with super_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"
        )
        connection.execute(
            text(
                "GRANT CONNECT ON DATABASE "
                f"{graph_fixture.TARGET_DATABASE} TO {APP_ROLE}"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        connection.execute(
            text(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
        )
        connection.execute(
            text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
        )
    url = super_engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
    return create_engine(url)


def _runtime_factory(endpoint: Any, app_engine: Any) -> AgentRuntime:
    def resources(model: str) -> RuntimeResources:
        conninfo = (
            f"postgresql://{endpoint.username}:{endpoint.password}"
            f"@{endpoint.host}:{endpoint.port}/{graph_fixture.TARGET_DATABASE}"
        )
        pool = ConnectionPool(
            conninfo=conninfo,
            kwargs={"autocommit": True},
            min_size=1,
            max_size=2,
            open=False,
            name="batch-command-checkpoint",
        )
        pool.open(wait=True, timeout=5.0)
        with pool.connection() as connection:
            PostgresSaver(connection).setup()

        ports = _WarningPorts()
        ports.persist_action = action_store_module.production_port(  # type: ignore[method-assign]
            app_engine.begin
        )
        dependencies = graph_fixture._dependencies(
            app_engine,
            ports,
            configured_llm_model=model,
            require_bound_thread=True,
        )
        graph = build_agent_graph(
            dependencies,
            checkpointer=checkpoint_module.build_postgres_saver(pool),
            interrupt_after=("load_incident",),
        )
        return RuntimeResources(
            graph=graph,
            transactions=app_engine.begin,
            resume_connections=app_engine.connect,
            checkpoint_pool=pool,
            deadline_executor=ThreadPoolExecutor(max_workers=1),
            llm_model=model,
        )

    return AgentRuntime(
        factory=resources,
        llm_preflight=lambda: "fixture-model",
        model_config=lambda: "fixture-model",
        autonomy_level=2,
    )


def _json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line]


def _runtime_counts(engine: Any) -> tuple[int, int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM agent_run) AS runs, "
                "(SELECT count(*) FROM action_history) AS actions, "
                "(SELECT count(*) FROM action_delivery) AS deliveries"
            )
        ).one()
    return int(row.runs), int(row.actions), int(row.deliveries)


def test_command_runs_the_real_graph_once_and_the_second_run_is_a_noop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main()→local graph→실제 3-table DML, 이어서 같은 명령은 delta 0."""

    with graph_fixture._runtime_context() as (endpoint, super_engine):
        graph_fixture._seed_runtime(super_engine)
        app_engine = _grant_app_role(super_engine)
        try:
            first = runner.main(
                ["--database", graph_fixture.TARGET_DATABASE, "--once"],
                engine_factory=lambda: app_engine,
                runtime_factory=lambda: _runtime_factory(endpoint, app_engine),
            )
            first_output = _json_lines(capsys.readouterr().out)
            first_counts = _runtime_counts(app_engine)

            second = runner.main(
                ["--database", graph_fixture.TARGET_DATABASE, "--once"],
                engine_factory=lambda: app_engine,
                runtime_factory=lambda: _runtime_factory(endpoint, app_engine),
            )
            second_output = _json_lines(capsys.readouterr().out)
            second_counts = _runtime_counts(app_engine)
        finally:
            app_engine.dispose()

    assert first == runner.EXIT_OK
    assert first_output[0]["outcome"] == "STARTED_COMPLETED"
    assert first_output[-1] == {
        "type": "final",
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "new_runs_observed": 1,
        "new_actions_observed": 1,
        "new_deliveries_observed": 1,
    }
    assert first_counts == (1, 1, 1)

    assert second == runner.EXIT_OK
    assert second_output == [
        {
            "type": "final",
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "new_runs_observed": 0,
            "new_actions_observed": 0,
            "new_deliveries_observed": 0,
        }
    ]
    assert second_counts == first_counts


def test_failed_run_without_action_is_not_pending() -> None:
    """status와 action 유무가 무관하게 run history 자체가 incident를 제외한다."""

    with graph_fixture._runtime_context() as (_endpoint, super_engine):
        graph_fixture._seed_runtime(super_engine)
        app_engine = _grant_app_role(super_engine)
        try:
            run_id = graph_fixture._start_runtime_run(app_engine)
            with app_engine.begin() as connection:
                graph_fixture.repo.finish_agent_run(
                    connection,
                    run_id,
                    RunStatus.FAILED,
                )
            with app_engine.connect() as connection:
                action_links = connection.execute(
                    text("SELECT count(*) FROM agent_run_action")
                ).scalar_one()
                plan = runner.build_pending_batch_plan(connection)
        finally:
            app_engine.dispose()

    assert action_links == 0
    assert plan.selected == ()
    assert plan.rejected == ()
    assert plan.canonical_null_rows == 0


def test_running_history_is_reported_as_incomplete_not_empty() -> None:
    """이전 회차의 RUNNING 고아가 다음 dry-run의 empty로 숨지 않는다."""

    with graph_fixture._runtime_context() as (_endpoint, super_engine):
        graph_fixture._seed_runtime(super_engine)
        app_engine = _grant_app_role(super_engine)
        try:
            graph_fixture._start_runtime_run(app_engine)
            with app_engine.connect() as connection:
                plan = runner.build_pending_batch_plan(connection)
        finally:
            app_engine.dispose()

    assert plan.selected == ()
    assert plan.rejected == ()
    assert [(item.lot_id, item.chamber_id) for item in plan.incomplete] == [
        ("LOT001", "EQP01-PM1")
    ]
