"""Runner seams: real temporary Git + policies + provider parser + receipt CLI.

Only source pin/owned database/HTTP and package-root location are substituted
for this portable test. These outputs are NOT real research artifacts.
"""

import json
import subprocess
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from app.agent import hypothesis, react
from app.agent import u10_runner as runner
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    write_private,
)
from app.agent.u10_batch import BatchBinding
from app.agent.u10_comparison import Benchmark
from app.agent.u10_fixture_bundle import build_benchmark, write_bundle
from app.agent.u10_fixture_database import restored_inventory
from app.agent.u10_source import BACKEND_ROOT
from tests.unit.test_agent_hypothesis import _content
from tests.unit.test_agent_u10_comparison import seal_benchmark
from tests.unit.test_agent_u10_fixtures import engine, source  # noqa: F401
from tests.unit.test_agent_u10_provider import (  # noqa: F401
    completion,
    config,
    settings,
    stop_payload,
)


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def setup(source, engine, settings, tmp_path, monkeypatch):  # noqa: F811
    import httpx

    root = tmp_path / "repo"
    root.mkdir()
    for name in ("backend", "frontend", "deploy"):
        (root / name).mkdir()
        (root / name / "marker").write_text("test fixture\n")
    (root / ".gitignore").write_text("output/\n")
    scripts = root / "backend/scripts"
    scripts.mkdir()
    (scripts / "emit_u10_evaluation_receipt.py").symlink_to(
        BACKEND_ROOT / "scripts/emit_u10_evaluation_receipt.py"
    )
    git(root, "init", "-b", "main")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=U10 test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "test fixture",
    )
    revision = git(root, "rev-parse", "HEAD")
    output = root / "output"
    output.mkdir(mode=0o700)
    inputs = output / "inputs"
    with restored_inventory(engine, source) as conn:
        benchmark, snapshots = build_benchmark(source, conn)
    benchmark_sha = write_bundle(inputs, benchmark, snapshots)
    cfg = config()
    write_private(inputs, "llm.json", cfg)
    binding = BatchBinding(
        revision,
        digest(canonical_json(benchmark)),
        digest(canonical_json(cfg)),
        benchmark.tool_contract_sha256,
        benchmark.fixed_policy_sha256,
    )
    now = datetime.now(UTC)
    write_private(
        inputs,
        "export.json",
        {
            "schema_version": "u10-data-export-grant-v1",
            "purpose": "U10_DATA_EXPORT_GRANT",
            "approved_by": "방대혁",
            "binding": asdict(binding),
            "issued_at": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    events, desired = [], []

    @contextmanager
    def database(_):
        events.append("database_open")
        try:
            yield engine
        finally:
            events.append("database_closed")

    monkeypatch.setattr(runner, "isolated_database", database)
    # The real guard is independently exercised by package-mismatch regression.
    monkeypatch.setattr(runner, "BACKEND_ROOT", root / "backend")
    actual_generate, actual_select = (
        hypothesis.generate_hypothesis,
        react.select_next_step,
    )

    def generate(**kwargs):
        alarm = kwargs["route"].incident.member_alarms[0].model_dump(mode="json")
        documents = kwargs["document_evidence"]
        desired[:] = [
            _content(
                supporting_alarms=[alarm],
                supporting_chunk_ids=[]
                if documents is None
                else [h.chunk_id for h in documents.hits],
                supporting_relation_ids=[],
            )
        ]
        return actual_generate(**kwargs)

    def select(context, **kwargs):
        payload = json.loads(stop_payload())
        if not context.fetched_fdc_candidate_ids:
            payload["next"] = "get_fdc_summary"
            payload["arguments"]["fdc_candidate_id"] = context.candidates.fdc[
                0
            ].candidate_id
        desired[:] = [json.dumps(payload)]
        return actual_select(context, **kwargs)

    def post(*args, **kwargs):
        events.append("mock_http")
        return completion(desired[0])

    monkeypatch.setattr(hypothesis, "generate_hypothesis", generate)
    monkeypatch.setattr(react, "select_next_step", select)
    monkeypatch.setattr(httpx, "post", post)
    return dict(
        repository=root,
        revision=revision,
        inputs=inputs,
        benchmark_sha256=benchmark_sha,
        llm_config=inputs / "llm.json",
        llm_config_sha256=digest((inputs / "llm.json").read_bytes()),
        export_grant=inputs / "export.json",
        export_grant_sha256=digest((inputs / "export.json").read_bytes()),
        postgres_image_id="sha256:" + "a" * 64,
    ), events


def test_32_real_policy_paths_to_private_artifact_and_actual_receipt_cli(setup):
    args, events = setup
    result = runner.run_comparison(**args)
    assert result["status"] == "PASS" and result["attempts_executed"] == 32
    assert result["production_enabled"] is False
    directory = args["repository"] / "output/v5-c-7.1" / args["revision"]
    artifact = json.loads((directory / "u10-comparison.json").read_bytes())
    assert len(artifact["attempts"]) == 32
    assert all(a["external_effects"] == 0 for a in artifact["attempts"])
    assert all(a["completion"] for a in artifact["attempts"])
    assert events[0] == "database_open" and events[-1] == "database_closed"
    assert events.count("mock_http") == 64  # 32 hypotheses + 16 * 2 selectors.
    receipt = json.loads((directory / "u10-evaluation-receipt.json").read_bytes())
    assert receipt["validator_exit_code"] == 0
    assert receipt["artifact_sha256"] == result["artifact_sha256"]
    assert receipt["evaluated_revision"] == args["revision"]
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
    assert git(args["repository"], "status", "--porcelain") == ""
    before = list(events)
    with pytest.raises(EvidenceError, match="U10_EXECUTION_ALREADY_EXISTS"):
        runner.run_comparison(**args)
    assert events == before


@pytest.mark.parametrize(
    "kind",
    ["dirty", "branch", "package", "grant", "benchmark", "llm", "snapshot", "claim"],
)
def test_prework_fail_closed(setup, monkeypatch, kind):
    args, events = setup
    if kind == "dirty":
        (args["repository"] / "backend/marker").write_text("changed")
    elif kind == "branch":
        git(args["repository"], "checkout", "-b", "test-feature")
    elif kind == "package":
        monkeypatch.setattr(runner, "BACKEND_ROOT", BACKEND_ROOT)
    elif kind in {"grant", "benchmark", "llm"}:
        field = {
            "grant": "export_grant_sha256",
            "benchmark": "benchmark_sha256",
            "llm": "llm_config_sha256",
        }[kind]
        args[field] = "0" * 64
    elif kind == "snapshot":
        (args["inputs"] / "CF-8.json").write_bytes(b"{}")
    else:
        path = args["repository"] / "output/v5-c-7.1" / args["revision"]
        path.mkdir(parents=True, mode=0o700)
        write_private(path, "u10-execution-claim.json", {"previous": True})
    with pytest.raises(EvidenceError):
        runner.run_comparison(**args)
    assert events == []
    claim = (
        args["repository"]
        / "output/v5-c-7.1"
        / args["revision"]
        / "u10-execution-claim.json"
    )
    assert claim.exists() is (kind == "claim")


def test_failed_batch_keeps_claim_and_has_no_artifact_or_receipt(setup, monkeypatch):
    args, events = setup

    def fail(**kwargs):
        raise EvidenceError("U10_TEST_ABORT")

    monkeypatch.setattr(runner, "execute_batch", fail)
    with pytest.raises(EvidenceError, match="U10_TEST_ABORT"):
        runner.run_comparison(**args)
    path = args["repository"] / "output/v5-c-7.1" / args["revision"]
    assert [p.name for p in path.iterdir()] == ["u10-execution-claim.json"]
    assert events == ["database_open", "database_closed"]


def test_resealed_oracle_is_rejected_by_database_recount(setup, monkeypatch):
    args, events = setup
    path = args["inputs"] / "benchmark.json"
    payload = json.loads(path.read_bytes())
    evidence = payload["fixtures"][0]["required_evidence_ids"]
    evidence["values"] = sorted([*evidence["values"], "CHUNK:tampered"])
    evidence["sha256"] = digest(canonical_json(evidence["values"]))
    seal_benchmark(payload)
    benchmark = Benchmark.model_validate(payload)
    path.write_bytes(canonical_json(benchmark) + b"\n")
    args["benchmark_sha256"] = digest(path.read_bytes())
    # Rebind the test grant too: neither DTO/hash nor consent gates may mask R3.
    grant_path = args["export_grant"]
    grant = json.loads(grant_path.read_bytes())
    grant["binding"]["benchmark_sha256"] = digest(canonical_json(benchmark))
    grant_path.write_bytes(canonical_json(grant) + b"\n")
    args["export_grant_sha256"] = digest(grant_path.read_bytes())
    actual_provider = runner.RealProvider

    def provider(*a, **kw):
        events.append("provider_created")
        return actual_provider(*a, **kw)

    monkeypatch.setattr(runner, "RealProvider", provider)
    with pytest.raises(EvidenceError, match="^U10_BENCHMARK_RECOUNT_MISMATCH$"):
        runner.run_comparison(**args)
    # Recount needs the DB seam; it must never construct a provider or call HTTP.
    assert events == ["database_open", "database_closed"]
    directory = args["repository"] / "output/v5-c-7.1" / args["revision"]
    assert [p.name for p in directory.iterdir()] == ["u10-execution-claim.json"]


@pytest.mark.parametrize(
    "corruption", ["missing_row", "key", "requests", "blocked", "send"]
)
def test_observer_population_invalid_prevents_publication(
    setup, monkeypatch, corruption
):
    args, events = setup
    providers = []
    actual_provider, actual_batch = runner.RealProvider, runner.execute_batch

    def provider(*a, **kw):
        instance = actual_provider(*a, **kw)
        providers.append(instance)
        return instance

    def batch(**kwargs):
        artifact = actual_batch(**kwargs)
        rows = providers[0].observations
        assert len(rows) == 32
        # Corrupt only the closed observer log, not the valid batch artifact.
        if corruption == "missing_row":
            rows.pop()
        elif corruption == "key":
            rows[0]["fixture_id"] = "CF-8"
        else:
            field, value = {
                "requests": ("provider_requests", 0),
                "blocked": ("blocked_effect_attempts", 1),
                "send": ("send_action_selected", 1),
            }[corruption]
            rows[0][field] = value
        return artifact

    monkeypatch.setattr(runner, "RealProvider", provider)
    monkeypatch.setattr(runner, "execute_batch", batch)
    with pytest.raises(EvidenceError, match="^U10_OBSERVER_POPULATION_INVALID$"):
        runner.run_comparison(**args)
    assert events.count("mock_http") == 64 and events[-1] == "database_closed"
    directory = args["repository"] / "output/v5-c-7.1" / args["revision"]
    assert [p.name for p in directory.iterdir()] == ["u10-execution-claim.json"]


def test_first_attempt_input_drift_stops_before_next_attempt(setup, monkeypatch):
    args, events = setup
    actual_scope = runner.RealProvider.scope
    attempts = []
    benchmark_path = args["inputs"] / "benchmark.json"

    @contextmanager
    def scope(self, key):
        attempts.append(key)
        with actual_scope(self, key) as ports:
            yield ports
        # Inject just after the real IO fence closes, so the independent input
        # guard (not the write-blocking observer) must stop this batch.
        if len(attempts) == 1:
            benchmark_path.write_bytes(benchmark_path.read_bytes() + b" ")

    monkeypatch.setattr(runner.RealProvider, "scope", scope)
    with pytest.raises(EvidenceError, match="^U10_EXECUTION_INPUT_DRIFT$"):
        runner.run_comparison(**args)
    assert len(attempts) == 1
    assert events[-1] == "database_closed"
    directory = args["repository"] / "output/v5-c-7.1" / args["revision"]
    assert [p.name for p in directory.iterdir()] == ["u10-execution-claim.json"]


@pytest.mark.parametrize(
    "field", ["receipt_sha256", "artifact_sha256", "receipt_path", "evaluated_revision"]
)
def test_receipt_cli_stdout_binding_mismatch_blocks_completion(
    setup, monkeypatch, field
):
    args, events = setup
    actual_run = subprocess.run
    calls = []

    def run(command, **kwargs):
        result = actual_run(command, **kwargs)
        if len(command) > 1 and str(command[1]).endswith(
            "/scripts/emit_u10_evaluation_receipt.py"
        ):
            assert events[-1] == "database_closed"
            assert result.returncode == 0
            payload = json.loads(result.stdout)
            assert payload["status"] == "PASS"
            calls.append(payload.copy())
            payload[field] = "tampered-" + payload[field]
            result.stdout = canonical_json(payload) + b"\n"
        return result

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(EvidenceError, match="^U10_RECEIPT_CLI_FAILED$"):
        runner.run_comparison(**args)
    assert len(calls) == 1
    directory = args["repository"] / "output/v5-c-7.1" / args["revision"]
    assert (
        digest((directory / "u10-comparison.json").read_bytes())
        == calls[0]["artifact_sha256"]
    )
    assert (
        digest((directory / "u10-evaluation-receipt.json").read_bytes())
        == calls[0]["receipt_sha256"]
    )
    assert not (directory / "u10-execution-complete.json").exists()


def test_cli_requires_execute_and_sanitizes_input(capsys):
    from scripts.run_u10_comparison import main

    assert main(["--repository", "secret-path"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "status": "FAIL",
        "code": "U10_CLI_ARGUMENT_INVALID",
    }
    assert "secret" not in output.out + output.err and not output.err


def test_receipt_failure_preserves_artifact_without_completion(setup, monkeypatch):
    args, events = setup

    def fail(*_):
        assert events[-1] == "database_closed"
        raise EvidenceError("U10_RECEIPT_CLI_FAILED")

    monkeypatch.setattr(runner, "_emit_receipt", fail)
    with pytest.raises(EvidenceError, match="U10_RECEIPT_CLI_FAILED"):
        runner.run_comparison(**args)
    directory = args["repository"] / "output/v5-c-7.1" / args["revision"]
    assert (directory / "u10-comparison.json").is_file()
    assert not (directory / "u10-execution-complete.json").exists()
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(EvidenceError):
        runner.run_comparison(**args)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_cli_execute_flag_required_even_with_all_other_arguments(monkeypatch, capsys):
    from scripts import run_u10_comparison as cli

    calls = []
    monkeypatch.setattr(cli, "run_comparison", lambda **kw: calls.append(kw) or {})
    args = []
    for name in (
        "repository",
        "inputs",
        "llm-config",
        "export-grant",
        "revision",
        "benchmark-sha256",
        "llm-config-sha256",
        "export-grant-sha256",
        "postgres-image-id",
    ):
        args.extend(["--" + name, "test-placeholder"])
    assert cli.main(args) == 1 and calls == []
    assert json.loads(capsys.readouterr().out)["code"] == "U10_CLI_ARGUMENT_INVALID"
