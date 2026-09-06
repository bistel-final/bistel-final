"""Single live invocation guard with actual no-clobber stdout file semantics."""

import os
from types import SimpleNamespace

import pytest

from app.agent import release_stage2_workload as m
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    read_private,
    write_private,
)


@pytest.mark.parametrize("failed", [False, True])
def test_batch_stdout_is_reserved_before_exec_and_never_replayed(
    tmp_path, monkeypatch, failed
):
    tmp_path.chmod(0o700)
    events = []
    runtime = SimpleNamespace(user="501:20", env={}, verify_running=lambda _: None)
    running = SimpleNamespace(
        containers={"runner": SimpleNamespace(container_id="a" * 64)}
    )

    def run(argv, **kwargs):
        events.append(argv)
        assert (tmp_path / "pending-run.jsonl").exists()
        os.write(kwargs["stdout"], b'{"type":"final"}\n')
        return SimpleNamespace(returncode=1 if failed else 0)

    monkeypatch.setattr(m.subprocess, "run", run)
    args = dict(
        root=tmp_path,
        name="pending-run.jsonl",
        arguments=["scripts/run_pending_incidents.py", "--once"],
    )
    if failed:
        with pytest.raises(EvidenceError, match="STAGE2_COMMAND_FAILED"):
            m.capture_runner(runtime, running, **args)
    else:
        m.capture_runner(runtime, running, **args)
    before = read_private(tmp_path, "pending-run.jsonl")
    with pytest.raises(EvidenceError, match="COMPONENT_IO_INVALID"):
        m.capture_runner(runtime, running, **args)
    assert len(events) == 1
    assert read_private(tmp_path, "pending-run.jsonl") == before
    assert events[0][:3] == ["docker", "exec", "--user"]


@pytest.mark.parametrize(
    "case,code",
    [
        ("success", None),
        ("offsets", "GOLDEN_MOCK_BEFORE_OFFSETS_MISMATCH"),
        ("plan", "STAGE2_PENDING_POPULATION_INVALID"),
        ("rejected", "STAGE2_PENDING_POPULATION_INVALID"),
        ("incomplete", "STAGE2_PENDING_POPULATION_INVALID"),
        ("postcondition", "STAGE2_BATCH_POSTCONDITION_FAILED"),
    ],
)
def test_execute_checks_population_before_once_and_snapshots_only_full_success(
    tmp_path, monkeypatch, case, code
):
    from app.agent.diagnostics import CANONICAL_INCIDENT_KEYS

    tmp_path.chmod(0o700)
    for name in ("evidence", "evidence/artifacts", "evidence/artifacts/PREFLIGHT"):
        (tmp_path / name).mkdir(mode=0o700)
    offsets = {"fdc.actions:0": 10, "fdc.actions.result:0": 20}
    write_private(tmp_path, "evidence/artifacts/PREFLIGHT/kafka.json", offsets)
    selected = [
        dict(
            lot_id=lot,
            chamber_id=chamber,
            member_count=1,
            representative=dict(source="TRACE", alarm_id="synthetic"),
        )
        for lot, chamber in sorted(CANONICAL_INCIDENT_KEYS)
    ]
    plan = dict(
        type="plan",
        database="kosa_agent_e2e",
        selected=selected[:-1] if case == "plan" else selected,
        rejected=[],
        incomplete=[],
        excluded=dict(canonical_null_rows=0, canonical_null_by_source={}),
    )
    if case in ("rejected", "incomplete"):
        plan[case] = [
            dict(lot_id="LOT002", chamber_id="EQP05-PM2", reason="INCOMPLETE_RUN")
        ]
    events = []
    running = SimpleNamespace(
        containers={"runner": SimpleNamespace(container_id="a" * 64)}
    )

    def exec_runner(expected, argv, **kwargs):
        assert expected is running
        events.append(
            "analytics" if "scripts/e2e_analytics_questions.py" in argv else "plan"
        )
        return canonical_json(plan)

    runtime = SimpleNamespace(
        user="501:20", env={}, verify_running=lambda _: None, exec_runner=exec_runner
    )
    monkeypatch.setenv("CM52_ANALYTICS_QUERY_IDS", "1,2,3")
    monkeypatch.setattr(
        m, "current_runtime", lambda **kw: (tmp_path, None, running, runtime)
    )

    def read_source(rt, expected, operation):
        assert rt is runtime and expected is running and operation == "offsets"
        events.append("offsets")
        return {**offsets, "fdc.actions:0": 11} if case == "offsets" else offsets

    monkeypatch.setattr(m, "source", read_source)

    def run(argv, **kwargs):
        assert "scripts/run_pending_incidents.py" in argv and argv.count("--once") == 1
        assert "pending-run.jsonl" in {p.name for p in tmp_path.iterdir()}
        events.append("once")
        final = dict(
            type="final",
            attempted=12,
            succeeded=11 if case == "postcondition" else 12,
            failed=0,
            skipped=0,
            new_runs_observed=12,
        )
        os.write(kwargs["stdout"], canonical_json(final) + b"\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(m.subprocess, "run", run)
    monkeypatch.setattr(
        m, "snapshot", lambda rt, expected, a, phase: events.append(phase)
    )
    if code:
        with pytest.raises(EvidenceError, match=f"^{code}$"):
            m.execute_workload()
    else:
        assert m.execute_workload() == dict(status="BATCH_CAPTURED", run_count=12)
    expected = ["analytics", "offsets"]
    if case != "offsets":
        expected.append("plan")
    if case not in ("offsets", "plan", "rejected", "incomplete"):
        expected.append("once")
    if case == "success":
        expected.append("BATCH_BASELINE")
    assert events == expected
    assert (tmp_path / "pending-run.jsonl").exists() == ("once" in expected)
