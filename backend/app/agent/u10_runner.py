"""U10 real execution composition: pinned inputs -> 32 attempts -> A receipt CLI.

Use only in a dedicated CLI process at merged clean local main R. No live
injection/skip switches, no retries of a claimed batch, no production enable.
The private claim remains after failures, as do any already-issued artifacts.
"""

import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    parse_json,
    read_private,
    write_private,
)
from app.agent.u10_batch import BatchBinding, execute_batch, execution_plan
from app.agent.u10_comparison import Benchmark, LlmConfiguration, validate_artifact
from app.agent.u10_export import ExportAuthorization
from app.agent.u10_fixture_bundle import build_benchmark, load_snapshot
from app.agent.u10_fixture_database import isolated_database, restored_inventory
from app.agent.u10_preparation import counterfactual_loader, verified_preparer
from app.agent.u10_provider import RealProvider
from app.agent.u10_receipt import _receipt_directory
from app.agent.u10_revision import verify_execution_revision
from app.agent.u10_source import BACKEND_ROOT, verify_source_binding


def _pinned(path, pin, code):
    raw = read_private(path.parent, path.name)
    if digest(raw) != pin:
        raise EvidenceError(code)
    return raw


def _emit_receipt(repository, artifact, benchmark, pin, revision):
    command = [
        sys.executable,
        str(BACKEND_ROOT / "scripts/emit_u10_evaluation_receipt.py"),
        "--repository",
        str(repository),
        "--artifact",
        str(artifact),
        "--benchmark",
        str(benchmark),
        "--benchmark-sha256",
        pin,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=90, check=False)
        if result.returncode or len(result.stdout) > 16384:
            raise EvidenceError("U10_RECEIPT_CLI_FAILED")
        payload = parse_json(result.stdout)
        path = artifact.parent / "u10-evaluation-receipt.json"
        raw = read_private(path.parent, path.name)
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "PASS"
            or payload.get("receipt_path") != str(path)
            or payload.get("receipt_sha256") != digest(raw)
            or payload.get("evaluated_revision") != revision
            or payload.get("artifact_sha256")
            != digest(read_private(artifact.parent, artifact.name))
        ):
            raise EvidenceError("U10_RECEIPT_CLI_FAILED")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise EvidenceError("U10_RECEIPT_CLI_FAILED") from None
    return payload


def run_comparison(
    *,
    repository: Path,
    revision: str,
    inputs: Path,
    benchmark_sha256: str,
    llm_config: Path,
    llm_config_sha256: str,
    export_grant: Path,
    export_grant_sha256: str,
    postgres_image_id: str,
):
    repository = repository.resolve(strict=True)
    identity = verify_execution_revision(repository, revision)
    if BACKEND_ROOT.parent.resolve() != repository:
        raise EvidenceError("U10_EXECUTING_PACKAGE_MISMATCH")
    benchmark_path = inputs / "benchmark.json"
    raw_b = _pinned(benchmark_path, benchmark_sha256, "PINNED_BENCHMARK_SHA_MISMATCH")
    raw_l = _pinned(llm_config, llm_config_sha256, "LLM_CONFIG_MISMATCH")
    benchmark = Benchmark.model_validate(parse_json(raw_b))
    llm = LlmConfiguration.model_validate(parse_json(raw_l))
    verify_source_binding(benchmark)
    binding = BatchBinding(
        revision,
        digest(canonical_json(benchmark)),
        digest(canonical_json(llm)),
        benchmark.tool_contract_sha256,
        benchmark.fixed_policy_sha256,
    )
    authorize = ExportAuthorization(export_grant, export_grant_sha256)
    authorize(binding)
    snapshots = {
        f.fixture_id: inputs / (f.fixture_id + ".json") for f in benchmark.fixtures
    }
    source = None
    for fixture in benchmark.fixtures:
        raw = _pinned(
            snapshots[fixture.fixture_id],
            fixture.initial_snapshot_sha256,
            "SNAPSHOT_MISMATCH",
        )
        restored = load_snapshot(raw, fixture.fixture_id)
        if source is not None and source != restored:
            raise EvidenceError("U10_SOURCE_PROJECTION_MISMATCH")
        source = restored
    directory = _receipt_directory(repository, revision)
    artifact_path = directory / "u10-comparison.json"
    if any(
        (directory / name).exists() or (directory / name).is_symlink()
        for name in (
            "u10-comparison.json",
            "u10-evaluation-receipt.json",
            "u10-execution-complete.json",
            "u10-io-observations.json",
        )
    ):
        raise EvidenceError("U10_EXECUTION_ALREADY_EXISTS")
    # O_EXCL is the concurrency boundary, before Docker/DNS/provider work.
    write_private(
        directory,
        "u10-execution-claim.json",
        {
            "schema_version": "u10-execution-claim-v1",
            "binding": asdict(binding),
            "benchmark_raw_sha256": benchmark_sha256,
            "grant_sha256": export_grant_sha256,
        },
    )

    def unchanged():
        if verify_execution_revision(repository, revision) != identity:
            raise EvidenceError("U10_EXECUTION_REVISION_MISMATCH")
        if (
            _pinned(benchmark_path, benchmark_sha256, "U10_EXECUTION_INPUT_DRIFT")
            != raw_b
        ):
            raise EvidenceError("U10_EXECUTION_INPUT_DRIFT")
        if _pinned(llm_config, llm_config_sha256, "U10_EXECUTION_INPUT_DRIFT") != raw_l:
            raise EvidenceError("U10_EXECUTION_INPUT_DRIFT")
        verify_source_binding(benchmark)
        authorize(binding)

    unchanged()
    with isolated_database(postgres_image_id) as engine:
        with restored_inventory(engine, source) as connection:
            actual, _ = build_benchmark(source, connection)
            if canonical_json(actual) != canonical_json(benchmark):
                raise EvidenceError("U10_BENCHMARK_RECOUNT_MISMATCH")
        provider = RealProvider(llm, binding, authorize)
        prepared = verified_preparer(
            benchmark=benchmark,
            snapshots=snapshots,
            binding=binding,
            authorize=authorize,
            open_snapshot=counterfactual_loader(engine, provider.scope),
        )

        @contextmanager
        def prepare(key):
            # Git subprocess checks occur outside the active attempt IO fence.
            unchanged()
            with prepared(key) as env:
                yield env
            unchanged()

        artifact = execute_batch(
            benchmark=benchmark,
            llm=llm,
            evaluated_revision=revision,
            expected_benchmark_sha256=binding.benchmark_sha256,
            expected_tool_contract_sha256=binding.tool_contract_sha256,
            authorize=authorize,
            prepare=prepare,
        )
    unchanged()  # Including successful owned-PG cleanup, before publication.
    if len(provider.observations) != 32 or any(
        any(row.get(k) != v for k, v in asdict(key).items())
        or row["provider_requests"] < 1
        or row["blocked_effect_attempts"]
        or row["send_action_selected"]
        for row, key in zip(provider.observations, execution_plan(), strict=True)
    ):
        raise EvidenceError("U10_OBSERVER_POPULATION_INVALID")
    result = validate_artifact(artifact.model_dump(), benchmark.model_dump())
    io_component = write_private(
        directory,
        "u10-io-observations.json",
        {
            "schema_version": "u10-io-observations-v1",
            "binding": asdict(binding),
            "attempts": provider.observations,
            "boundary": "DEDICATED_PROCESS_PYTHON_AUDIT_NOT_OS_SANDBOX",
        },
    )
    component = write_private(directory, artifact_path.name, artifact)
    receipt = _emit_receipt(
        repository, artifact_path, benchmark_path, benchmark_sha256, revision
    )
    unchanged()
    complete = {
        "status": "PASS",
        "evaluated_revision": revision,
        "attempts_executed": 32,
        "artifact_sha256": component.sha256,
        "receipt_sha256": receipt["receipt_sha256"],
        "io_observations_sha256": io_component.sha256,
        "agent_verdict": result["agent_verdict"],
        "verdict_reason": result["verdict_reason"],
        "production_enabled": False,
    }
    write_private(directory, "u10-execution-complete.json", complete)
    return complete
