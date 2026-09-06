"""Private temporary files and real offline validation; no runtime/LLM calls."""

import subprocess
import sys

import pytest

from app.agent import u10_evaluation as subject
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    write_private,
)
from tests.unit.test_agent_u10_comparison import artifact_payload, react_rows, recompute


def bundle(tmp_path, *, negative=False, change=None):
    tmp_path.chmod(0o700)
    artifact, benchmark = artifact_payload()
    if negative:
        react_rows(artifact)[0]["completion"] = False
        artifact["result"] = recompute(artifact, benchmark)
    artifact_bytes = canonical_json(artifact) + b"\n"
    receipt = {
        "schema_version": "u10-evaluation-receipt-v1",
        "artifact_sha256": digest(artifact_bytes),
        **{
            key: benchmark[key]
            for key in (
                "fixture_sha256",
                "oracle_sha256",
                "inventory_sha256",
                "tool_contract_sha256",
                "fixed_policy_sha256",
            )
        },
        "verdict_rules_sha256": artifact["verdict_rules_sha256"],
        "effective_budget_policy": {"total": 10, "read": 8, "send": 2, "same_tool": 4},
        "validator_command": [
            "python",
            "scripts/verify_u10_comparison.py",
            "--artifact",
            "artifact.json",
        ],
        "validator_exit_code": 0,
        "agent_justification_verdict": artifact["result"]["agent_verdict"],
        "evaluated_revision": artifact["evaluated_revision"],
        "evaluated_tree_oid": {
            name: "b" * 40 for name in ("backend", "frontend", "deploy")
        },
        "git_object_format": "sha1",
        "decided_at": "2026-09-05T01:02:03Z",
    }
    if change:
        change(artifact, benchmark, receipt)
    a = write_private(tmp_path, "artifact.json", artifact)
    b = write_private(tmp_path, "benchmark.json", benchmark)
    r = write_private(tmp_path, "receipt.json", receipt)
    return (
        dict(
            artifact=tmp_path / a.relative_path,
            benchmark=tmp_path / b.relative_path,
            evaluation_receipt=tmp_path / r.relative_path,
            pinned_benchmark_sha256=b.sha256,
        ),
        receipt,
        artifact,
    )


@pytest.mark.parametrize("negative", [False, True])
def test_recalculates_positive_and_preserves_negative_without_permission(
    tmp_path, negative
):
    args, receipt, artifact = bundle(tmp_path, negative=negative)
    result = subject.verify_evaluation(**args)
    assert result.agent_verdict == artifact["result"]["agent_verdict"]
    assert result.verdict_reason == ("HARD_GATE_FAIL:COMPLETION" if negative else None)
    assert result.receipt.model_dump() == receipt
    assert result.artifact_sha256 == receipt["artifact_sha256"]
    assert result.receipt_sha256 == digest(args["evaluation_receipt"].read_bytes())
    assert result.benchmark_sha256 == digest(args["benchmark"].read_bytes())
    assert (
        result.benchmark_sha256 != artifact["benchmark_sha256"]
    )  # newline vs canonical
    assert set(result.model_dump()) == {
        "receipt",
        "artifact_sha256",
        "receipt_sha256",
        "benchmark_sha256",
        "agent_verdict",
        "verdict_reason",
    }


@pytest.mark.parametrize(
    "field",
    [
        "fixture_sha256",
        "oracle_sha256",
        "inventory_sha256",
        "tool_contract_sha256",
        "fixed_policy_sha256",
    ],
)
def test_every_receipt_benchmark_binding_is_recomputed(tmp_path, field):
    args, _, _ = bundle(tmp_path, change=lambda a, b, r: r.update({field: "0" * 64}))
    with pytest.raises(EvidenceError, match="^U10_RECEIPT_BENCHMARK_MISMATCH$"):
        subject.verify_evaluation(**args)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("artifact_sha256", "0" * 64, "ARTIFACT_SHA"),
        ("evaluated_revision", "b" * 40, "REVISION"),
        ("verdict_rules_sha256", "0" * 64, "RULES"),
        (
            "effective_budget_policy",
            {"total": 10, "read": 6, "send": 2, "same_tool": 4},
            "BUDGET",
        ),
        (
            "agent_justification_verdict",
            "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21",
            "VERDICT",
        ),
    ],
)
def test_receipt_disagreement_rejected(tmp_path, field, value, code):
    args, _, _ = bundle(tmp_path, change=lambda a, b, r: r.update({field: value}))
    with pytest.raises(EvidenceError, match=f"^U10_RECEIPT_{code}_MISMATCH$"):
        subject.verify_evaluation(**args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("validator_exit_code", 1),
        ("validator_exit_code", False),
        ("validator_command", []),
        ("decided_at", "2026-02-30T00:00:00Z"),
        ("decided_at", "2026-09-05T01:02:03.123Z"),
        ("extra", "private"),
        (
            "effective_budget_policy",
            {"total": 10.0, "read": 8, "send": 2, "same_tool": 4},
        ),
        ("git_object_format", "sha256"),
        ("evaluated_revision", "a" * 64),
    ],
)
def test_receipt_schema_is_strict(tmp_path, field, value):
    args, _, _ = bundle(tmp_path, change=lambda a, b, r: r.update({field: value}))
    with pytest.raises(EvidenceError, match="^U10_EVALUATION_INPUT_INVALID$"):
        subject.verify_evaluation(**args)


def test_recorded_success_does_not_replace_actual_validator(tmp_path):
    def change(a, b, r):
        a["attempts"].pop()
        r["artifact_sha256"] = digest(canonical_json(a) + b"\n")

    args, _, _ = bundle(tmp_path, change=change)
    with pytest.raises(EvidenceError, match="^ATTEMPT_POPULATION_INVALID$"):
        subject.verify_evaluation(**args)


def test_recorded_command_is_never_executed(tmp_path, monkeypatch):
    args, _, _ = bundle(
        tmp_path,
        change=lambda a, b, r: r.update(validator_command=["untrusted-command"]),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("executed receipt command")
    )
    subject.verify_evaluation(**args)


@pytest.mark.parametrize("field", ["artifact", "evaluation_receipt", "benchmark"])
@pytest.mark.parametrize("fault", ["mode", "symlink", "missing"])
def test_private_files_fail_closed(tmp_path, field, fault):
    args, _, _ = bundle(tmp_path)
    path = args[field]
    if fault == "mode":
        path.chmod(0o644)
    elif fault == "symlink":
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    else:
        args[field] = path.with_suffix(".missing")
    with pytest.raises(EvidenceError):
        subject.verify_evaluation(**args)


def test_benchmark_pin_cannot_be_taken_from_artifact(tmp_path):
    args, _, artifact = bundle(tmp_path)
    args["pinned_benchmark_sha256"] = artifact["benchmark_sha256"]
    with pytest.raises(EvidenceError, match="^PINNED_BENCHMARK_SHA_MISMATCH$"):
        subject.verify_evaluation(**args)


@pytest.mark.parametrize("pin", [None, "bad", "a" * 65])
def test_bad_pin_precedes_file_reads(tmp_path, monkeypatch, pin):
    args, _, _ = bundle(tmp_path)
    args["pinned_benchmark_sha256"] = pin
    monkeypatch.setattr(subject, "read_private", lambda *_: pytest.fail("read file"))
    with pytest.raises(EvidenceError, match="^U10_BENCHMARK_PIN_INVALID$"):
        subject.verify_evaluation(**args)


def test_import_does_not_load_providers_or_execute_process():
    code = """
import sys, subprocess
def fail(*args, **kwargs): raise AssertionError('process on import')
subprocess.run = fail
import app.agent.u10_evaluation
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
