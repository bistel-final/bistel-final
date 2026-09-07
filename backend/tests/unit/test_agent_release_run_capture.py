"""Real persisted-row projection and scope validators with synthetic DTOs."""

import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from app.agent import release_run_capture as subject
from app.agent.release_artifacts import EvidenceError, canonical_json
from app.agent.release_budget import profile_fields
from app.agent.release_model import ModelContext
from app.agent.routing import ResolvedIncidentRoute
from app.agent.state import Hypothesis
from scripts.read_release_runs import main
from tests.unit.test_agent_release import S
from tests.unit.test_agent_release_context import projection
from tests.unit.test_agent_release_round import template  # noqa: F401


@pytest.mark.parametrize("module", ["emit_level3_robustness", "stage2_level3_phase"])
def test_offline_release_cli_import_never_loads_langgraph_checkpoint(module):
    code = f"""
import builtins, importlib, sys
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name.startswith('langgraph'):
        raise AssertionError('OFFLINE_CHECKPOINT_IMPORT_FORBIDDEN')
    return original(name, *args, **kwargs)
builtins.__import__ = guard
importlib.import_module('scripts.{module}')
assert not any(name.startswith('langgraph') for name in sys.modules)
print('OFFLINE_IMPORT_PASS')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "OFFLINE_IMPORT_PASS\n"
    assert result.stderr == ""


def capture_args(raw, model):
    route = TypeAdapter(ResolvedIncidentRoute).validate_json(
        canonical_json(raw["route"])
    )
    row = SimpleNamespace(
        agent_run_id=raw["run_id"],
        thread_id="test-thread",
        lot_id=route.incident.lot_id,
        chamber_id=route.incident.chamber_id,
        autonomy_level=3,
        status=raw["status"],
        action=raw["action_code"],
        retry_of_run_id=None,
        llm_model=raw["hypothesis_model_revision"],
        prompt_version=raw["hypothesis_prompt_version"],
        input_tokens=raw["hypothesis_tokens"]["input"],
        output_tokens=raw["hypothesis_tokens"]["output"],
        latency_ms=raw["latency_ms"],
        evidence=profile_fields(raw.get("investigation_budget_profile")),
    )
    state = dict(
        run_id=row.agent_run_id,
        thread_id=row.thread_id,
        route=route,
        chamber_id=row.chamber_id,
        action_id=raw["action_id"],
        autonomy_level=3,
        tool_budget=profile_fields(raw.get("investigation_budget_profile")),
        fdc_lot_hist_ids=tuple(raw["current_lot_hist_ids"]),
        graph_evidence=SimpleNamespace(ok=True, model_code=raw["document_model_code"]),
        hypothesis=Hypothesis.model_validate(raw["hypothesis"]),
        react_trace=deepcopy(raw["react_trace"]),
        errors=(),
    )
    return dict(
        run=row,
        state=state,
        model=model,
        action=SimpleNamespace(
            action_id=raw["action_id"],
            lot_id=row.lot_id,
            chamber_id=row.chamber_id,
            action_code=row.action,
        ),
        calls=[
            SimpleNamespace(
                agent_run_id=row.agent_run_id,
                call_seq=r["seq"],
                tool_name=r["tool"],
                input=deepcopy(r["request"]),
                output=deepcopy(r["result"]),
                status=r["status"],
                error_msg=None,
                latency_ms=r["latency_ms"],
            )
            for r in raw["reads"]
        ],
        deliveries=[
            SimpleNamespace(
                action_id=raw["action_id"],
                channel="MES_MOCK" if d["channel"] == "MES" else d["channel"],
                status=d["status"],
                request_hash=d["request_hash"],
            )
            for d in raw["deliveries"]
        ],
    )


@pytest.fixture
def model(template):  # noqa: F811
    return ModelContext(
        schema_version="level3-model-context-v1",
        llm=template["llm"],
        endpoint_sha256=S,
        model_config_digest=template["model_config_digest"],
        published_attempt_id=None,
    )


def test_twelve_actual_dto_projections_equal_offline_evidence(template, model):  # noqa: F811
    for raw in template["runs"]:
        result = subject.capture_run(**capture_args(raw, model))
        assert result.model_dump(mode="json") == raw


@pytest.mark.parametrize(
    "fault",
    [
        "state_run",
        "state_action",
        "sequence",
        "reserved",
        "request",
        "missing_read",
        "model",
        "latency",
        "result",
    ],
)
def test_missing_or_unbound_records_never_turn_into_success(template, model, fault):  # noqa: F811
    args = capture_args(template["runs"][5], model)
    if fault == "state_run":
        args["state"]["run_id"] = "different"
    elif fault == "state_action":
        args["state"]["action_id"] = "different"
    elif fault == "sequence":
        args["calls"][0].call_seq = 2
    elif fault == "reserved":
        args["calls"][0].error_msg = subject.repo.RESERVED_ERROR_MSG
    elif fault == "request":
        args["calls"][1].input = {"lot_hist_id": "different"}
    elif fault == "missing_read":
        args["calls"].pop()
    elif fault == "model":
        args["run"].llm_model = "different"
    elif fault == "latency":
        args["run"].latency_ms = None
    elif fault == "result":
        args["calls"][0].output = None
    with pytest.raises(EvidenceError, match="^ROUND_RUN_CAPTURE_INVALID$"):
        subject.capture_run(**args)


def test_cli_rejects_extra_arguments_before_live_imports(capsys):
    assert main(["--secret", "never-print"]) == 2
    assert (
        capsys.readouterr().out
        == '{"status":"FAIL","code":"ROUND_CAPTURE_ARGUMENT_INVALID"}\n'
    )


@pytest.mark.parametrize(
    "fault",
    [None, "target", "identity", "population", "checkpoint", "model", "too_many_tools"],
)
def test_bounded_readonly_db_collection_and_drift(template, model, monkeypatch, fault):  # noqa: F811
    data = {r["run_id"]: capture_args(r, model) for r in template["runs"]}
    ids = sorted(data)
    statements = []
    iterations = 0

    class Connection:
        def exec_driver_sql(self, sql):
            statements.append(sql)

        def execute(self, sql):
            if "pg_control_system" in str(sql):
                result = dict(
                    database_name="kosa_agent_e2e",
                    role_name="kosa_app",
                    read_only="on",
                    isolation="repeatable read",
                    system_identifier=projection().identity.system_identifier,
                )
                if fault == "identity":
                    result["database_name"] = "kosa_agent"
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(one=lambda: result)
                )
            assert "LIMIT 13" in str(sql)
            return SimpleNamespace(
                scalars=lambda: ids[:-1] if fault == "population" else ids
            )

    @contextmanager
    def connect():
        nonlocal iterations
        iterations += 1
        yield Connection()

    identity = projection().identity
    engine = SimpleNamespace(
        url=SimpleNamespace(
            host=identity.host_alias,
            database="kosa_agent" if fault == "target" else "kosa_agent_e2e",
            username="kosa_app",
        ),
        connect=connect,
    )
    monkeypatch.setattr(subject.repo, "get_agent_run", lambda c, rid: data[rid]["run"])
    monkeypatch.setattr(
        subject.repo,
        "get_run_action",
        lambda c, rid: SimpleNamespace(action_id=rid, link_role="CREATED"),
    )
    monkeypatch.setattr(
        subject.repo, "get_action_history", lambda c, aid: data[aid]["action"]
    )
    monkeypatch.setattr(
        subject.repo,
        "count_tool_calls",
        lambda c, rid: 101 if fault == "too_many_tools" else len(data[rid]["calls"]),
    )
    monkeypatch.setattr(
        subject.repo, "list_tool_calls", lambda c, rid: data[rid]["calls"]
    )
    monkeypatch.setattr(
        subject.repo, "list_action_deliveries", lambda c, aid: data[aid]["deliveries"]
    )
    for rid, args in data.items():
        args["run"].thread_id = args["state"]["thread_id"] = rid

    def checkpoint(tid):
        state = deepcopy(data[tid]["state"])
        if iterations == 2 and fault == "checkpoint":
            state["action_id"] = "changed"
        return state

    def current_model():
        return (
            model.model_copy(update={"endpoint_sha256": "b" * 64})
            if iterations == 2 and fault == "model"
            else model
        )

    kwargs = dict(
        identity=identity, read_checkpoint=checkpoint, read_model=current_model
    )
    if fault:
        with pytest.raises(EvidenceError, match="^ROUND_RUN_CAPTURE_FAILED$"):
            subject.collect_runs(engine, **kwargs)
    else:
        result = subject.collect_runs(engine, **kwargs)
        assert len(result.runs) == 12 and iterations == 2
        assert result.model == model
    if fault == "target":
        assert iterations == 0
    else:
        assert statements[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
        assert not any(
            word in " ".join(statements)
            for word in ("INSERT", "UPDATE", "DELETE", "COMMIT")
        )
