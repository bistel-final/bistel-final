"""Read adapter wiring with production DTOs and local-only deadline execution."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.release_artifacts import EvidenceError
from app.agent.tools import (
    ThreadDeadlineRunner,
    ToolDeadlineExceeded,
    ToolRunnerSaturated,
)
from app.agent.u10_comparison import EvidenceIds
from app.agent.u10_react_execution import execute_react_policy
from app.agent.u10_read_adapter import ReadAdapter, ReadPorts
from app.agent.u10_read_execution import ReadSession, fixed_policy_requests
from app.common import tool_contracts as dto
from tests.unit.test_agent_graph import _equipment, _fdc
from tests.unit.test_agent_u10_comparison import ids
from tests.unit.test_agent_u10_observations import context, history_call, history_result
from tests.unit.test_agent_u10_react_execution import outcome
from tests.unit.test_agent_u10_read_execution import context as document_context
from tests.unit.test_agent_u10_read_execution import inputs, inventory


def projected(_tool, result):
    return EvidenceIds.model_validate(
        ids(result.wafer.lot_hist_id)
        if isinstance(result, dto.FdcSummaryToolResult)
        else ids()
    )


class Immediate:
    def __init__(self):
        self.deadlines = []

    def call(self, fn, payload, *, seconds):
        self.deadlines.append(seconds)
        return fn(payload)


def ports(fn):
    return ReadPorts(fn, fn, fn, fn, fn)


def test_authorize_then_read_then_validate_project_and_record(monkeypatch):
    state, events = context(), []
    for name in ("authorize", "validate_result", "record"):
        original = getattr(state, name)

        def wrapped(*args, _name=name, _original=original):
            events.append(_name)
            return _original(*args)

        monkeypatch.setattr(state, name, wrapped)

    def read(payload):
        events.append("read")
        assert state.results("get_fdc_summary") == []
        payload["lot_hist_id"] = "mutated"
        return _fdc()

    def project(tool, result):
        events.append("project")
        assert state.results("get_fdc_summary") == []
        return projected(tool, result)

    deadline = Immediate()
    adapter = ReadAdapter(state, ports(read), deadline, project)
    request = {"lot_hist_id": "LH-REP"}
    result = adapter("get_fdc_summary", request)
    assert result.status == "SUCCESS" and result.evidence_ids.values == ["LH-REP"]
    assert request == {"lot_hist_id": "LH-REP"}
    assert (
        events.index("authorize")
        < events.index("read")
        < events.index("validate_result")
        < events.index("project")
        < events.index("record")
    )
    assert deadline.deadlines == [8.0]
    assert len(state.results("get_fdc_summary")) == 1


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("send_action", {"action_id": "A"}),
        ("get_fdc_summary", {"lot_hist_id": "OTHER"}),
        ("search_documents", {"query": "FDC"}),
        ("search_documents", {"query": "FDC", "model_code": "OTHER"}),
    ],
)
def test_rejected_scope_never_reaches_deadline_or_port(tool, arguments):
    deadline = Immediate()
    calls = []
    adapter = ReadAdapter(
        context(), ports(lambda p: calls.append(p)), deadline, projected
    )
    with pytest.raises(EvidenceError):
        adapter(tool, arguments)
    assert calls == deadline.deadlines == []


@pytest.mark.parametrize(
    "error,status",
    [
        (TimeoutError, "TIMEOUT"),
        (ToolDeadlineExceeded, "TIMEOUT"),
        (ToolRunnerSaturated, "ERROR"),
        (RuntimeError, "ERROR"),
    ],
)
def test_deadline_errors_do_not_publish_or_leak_exception(error, status):
    state = context()

    class Failure:
        def call(self, *args, **kwargs):
            raise error("credential-must-not-leak")

    adapter = ReadAdapter(state, ports(lambda _: _fdc()), Failure(), projected)
    result = adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
    assert result.status == status and result.evidence_ids.values == []
    assert state.results("get_fdc_summary") == []
    assert "credential" not in result.model_dump_json()


@pytest.mark.parametrize(
    "reason,status",
    [("TIMEOUT: DB", "TIMEOUT"), ("DEPENDENCY_ERROR: unavailable", "ERROR")],
)
def test_failed_dto_does_not_call_evidence_projector(reason, status):
    state = context()
    projected_calls = []
    adapter = ReadAdapter(
        state,
        ports(lambda _: dto.FdcSummaryToolResult(ok=False, reason=reason)),
        Immediate(),
        lambda *args: projected_calls.append(args),
    )
    result = adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
    assert result.status == status and projected_calls == []
    assert state.results("get_fdc_summary") == []


@pytest.mark.parametrize("bad", ["type", "scope", "projection"])
def test_invalid_output_never_commits_an_observation(bad):
    state = context()
    raw = _fdc()
    if bad == "type":
        raw = _equipment()
    if bad == "scope":
        raw.wafer.lot_id = "OTHER"
    adapter = ReadAdapter(
        state,
        ports(lambda _: raw),
        Immediate(),
        lambda *args: None if bad == "projection" else projected(*args),
    )
    with pytest.raises(EvidenceError):
        adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
    assert state.results("get_fdc_summary") == []


def test_projector_mutation_is_isolated_and_projection_sha_revalidated():
    state = context()

    def mutate(tool, value):
        result = projected(tool, value)
        value.wafer.lot_id = "mutated"
        return result

    adapter = ReadAdapter(state, ports(lambda _: _fdc()), Immediate(), mutate)
    result = adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
    result.evidence_ids.values.append("caller-change")
    assert state.results("get_fdc_summary")[0].wafer.lot_id == "LOT001"


def test_invalid_projected_sha_does_not_publish_success():
    state = context()
    invalid = EvidenceIds.model_validate(ids("LH-REP")).model_copy(
        update={"sha256": "0" * 64}
    )
    adapter = ReadAdapter(
        state, ports(lambda _: _fdc()), Immediate(), lambda *_: invalid
    )
    with pytest.raises(ValidationError, match="EVIDENCE_IDS_SHA_MISMATCH"):
        adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
    assert state.results("get_fdc_summary") == []


def test_adapter_reentry_cannot_issue_another_read():
    calls = []

    def read(payload):
        calls.append(payload)
        with pytest.raises(EvidenceError, match="^U10_READ_ADAPTER_BUSY$"):
            adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
        return _fdc()

    adapter = ReadAdapter(context(), ports(read), Immediate(), projected)
    assert adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"}).status == "SUCCESS"
    assert len(calls) == 1


def test_history_internal_context_is_only_joined_at_read_boundary():
    state = context()
    state.record("get_fdc_summary", {"lot_hist_id": "LH-REP"}, _fdc())
    request, internal = history_call(state)
    received = []

    def read(payload):
        received.append(payload.copy())
        return history_result()

    adapter = ReadAdapter(state, ports(read), Immediate(), projected)
    assert (
        adapter("get_chamber_parameter_history", request, internal).status == "SUCCESS"
    )
    assert received[0] == {**request, "_context": internal}
    assert "_context" not in request and internal["scope"] == "CURRENT"


def test_late_worker_result_cannot_update_observation_context():
    state, release, started = context(), Event(), Event()

    def read(_):
        started.set()
        assert release.wait(2)
        return _fdc()

    with ThreadPoolExecutor(max_workers=1) as pool:
        runner = ThreadDeadlineRunner(pool)

        class ShortDeadline:
            def call(self, fn, payload, *, seconds):
                assert seconds == 8
                return runner.call(fn, payload, seconds=0.01)

        adapter = ReadAdapter(state, ports(read), ShortDeadline(), projected)
        try:
            result = adapter("get_fdc_summary", {"lot_hist_id": "LH-REP"})
            assert result.status == "TIMEOUT" and started.is_set()
            assert state.results("get_fdc_summary") == []
        finally:
            release.set()
    assert state.results("get_fdc_summary") == []


def test_shared_session_retry_flows_through_adapter_and_context():
    state, attempts = context(), []

    def read(payload):
        attempts.append(payload.copy())
        return _fdc(ok=len(attempts) > 1)

    adapter = ReadAdapter(state, ports(read), Immediate(), projected)
    from app.agent.u10_read_execution import ReadRequest

    session = ReadSession(inventory(), adapter)
    calls = session.execute(
        ReadRequest(slot="CURRENT_FDC", arguments={"lot_hist_id": "LH-REP"})
    )
    assert [c.status for c in calls] == ["TIMEOUT", "SUCCESS"]
    assert attempts == [{"lot_hist_id": "LH-REP"}] * 2
    assert len(state.results("get_fdc_summary")) == 1


def test_react_policy_uses_adapter_and_observation_builder_together():
    state = context()
    adapter = ReadAdapter(state, ports(lambda _: _fdc()), Immediate(), projected)
    choices = iter([outcome("get_fdc_summary", fdc_candidate_id="F1"), outcome("stop")])
    result = execute_react_policy(
        inventory(),
        state.build_context,
        lambda _: next(choices),
        adapter,
        document_model_code="MODEL-1",
        expected_selector_model="fixture-model",
    )
    assert result.stop_reason == "LLM_STOP" and len(result.calls) == 1
    assert state.build_context().observed_parameter_keys == (("PARAM-1", 1),)


def test_fixed_policy_documents_include_explicit_snapshot_model_filter():
    requests, _ = fixed_policy_requests(
        inventory(), inputs(inventory()), document_context()
    )
    assert [r.arguments["model_code"] for r in requests[-2:]] == ["PH-9000"] * 2


def test_production_factory_only_copies_five_read_ports(monkeypatch):
    from app.agent.tools import ToolBoundary

    funcs = [lambda _: None for _ in range(5)]
    boundary = SimpleNamespace(
        fdc_summary=funcs[0],
        equipment_context=funcs[1],
        document_search=funcs[2],
        chamber_parameter_history=funcs[3],
        metrology_result=funcs[4],
        send_action=lambda _: pytest.fail("send"),
    )
    monkeypatch.setattr(ToolBoundary, "production", lambda: boundary)
    result = ReadPorts.production()
    assert list(vars(result).values()) == funcs
    assert not hasattr(result, "send_action")


def test_import_is_offline():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from app.agent import u10_read_adapter; "
            "assert not {'app.agent.tools', 'app.common.config', "
            "'app.common.llm'} & set(sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
