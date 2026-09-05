"""Bundle A CLI seam: real private files/Git/offline subprocess, fake Docker/HTTP."""

import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import u10_receipt
from app.agent.release_artifacts import (
    EvidenceError,
    canonical_json,
    digest,
    parse_json,
)
from app.agent.u10_evaluation import verify_evaluation
from app.agent.u10_preflight_report import allowed_actions
from scripts.emit_u10_evaluation_receipt import main as emit_main
from scripts.u10_preflight import main as preflight_main
from tests.unit.test_agent_u10_comparison import ids, react_rows, recompute
from tests.unit.test_agent_u10_integrity import setup as integrity_setup
from tests.unit.test_agent_u10_revision import git


def setup(tmp_path, monkeypatch, profile="e2e_level3", negative=False):
    args, containers, events, _ = integrity_setup(
        tmp_path, monkeypatch, profile, negative
    )
    repo = args["repository"]
    (repo / ".gitignore").write_text("output/\n")
    git(repo, "commit", "-qam", "ignore private receipts")
    revision = git(repo, "rev-parse", "HEAD")
    artifact = parse_json(args["artifact"].read_bytes())
    artifact["evaluated_revision"] = revision
    args["artifact"].write_bytes(canonical_json(artifact))
    inspect = args["inspect"]

    def new_labels(kind, identifier):
        value = inspect(kind, identifier)
        if kind == "image":
            value["label_revision"] = revision
        return value

    args["inspect"] = new_labels
    args["clock"] = lambda: datetime(2026, 9, 5, 2, tzinfo=UTC)
    return args, containers, events, revision


def emit_argv(args):
    return [
        "--repository",
        str(args["repository"]),
        "--artifact",
        str(args["artifact"]),
        "--benchmark",
        str(args["benchmark"]),
        "--benchmark-sha256",
        args["pinned_benchmark_sha256"],
    ]


def preflight_argv(args):
    argv = emit_argv(args) + [
        "--evaluation-receipt",
        str(args["evaluation_receipt"]),
        "--profile",
        args["profile"],
        "--phase",
        args["phase"],
    ]
    for role, identifier in args["expected_image_ids"].items():
        argv += ["--image-id", f"{role}={identifier}"]
    for role, identifier in args["container_ids"].items():
        argv += ["--container-id", f"{role}={identifier}"]
    if args["expected_attempt_id"]:
        argv += ["--expected-attempt-id", args["expected_attempt_id"]]
    return argv


def output(capsys):
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.splitlines()) == 1
    return json.loads(captured.out)


def emit(args, capsys):
    assert emit_main(emit_argv(args)) == 0
    result = output(capsys)
    args["evaluation_receipt"] = Path(result["receipt_path"])
    return result


def run_preflight(args, argv=None):
    return preflight_main(
        preflight_argv(args) if argv is None else argv,
        **{k: args[k] for k in ("inspect", "read", "fetch", "clock")},
    )


@pytest.mark.parametrize(
    "profile", ["production_level2", "e2e_level3", "production_level3"]
)
@pytest.mark.parametrize("negative", [False, True])
def test_emission_to_preflight_cli_seam_is_private_and_idempotent(
    tmp_path, monkeypatch, capsys, profile, negative
):
    args, _, _, revision = setup(tmp_path, monkeypatch, profile, negative)
    issued = emit(args, capsys)
    receipt_path = args["evaluation_receipt"]
    assert (
        receipt_path
        == args["repository"]
        / "output/v5-c-7.1"
        / revision
        / "u10-evaluation-receipt.json"
    )
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt_path.parent.stat().st_mode) == 0o700
    receipt_bytes = receipt_path.read_bytes()
    assert issued["receipt_sha256"] == digest(receipt_bytes)
    receipt = parse_json(receipt_bytes)
    command = receipt["validator_command"]
    assert command[0] == sys.executable
    assert command[1].endswith("/backend/scripts/verify_u10_comparison.py")
    assert command[2:] == [
        "--artifact",
        str(args["artifact"]),
        "--benchmark",
        str(args["benchmark"]),
        "--benchmark-sha256",
        args["pinned_benchmark_sha256"],
    ]
    for _ in range(2):
        assert run_preflight(args) == 0
        report = output(capsys)
        assert report["integrity"] == "PASS" and report["failed_checks"] == []
        assert report["head"] == report["evaluated_revision"] == revision
        assert report["profile"] == profile and report["phase"] == args["phase"]
        assert report["repository_root"] == str(args["repository"])
        assert report["checked_at"] == "2026-09-05T02:00:00Z"
        assert report["verdict_reason"] == (
            "HARD_GATE_FAIL:COMPLETION" if negative else None
        )
        assert report["agent_verdict"] == receipt["agent_justification_verdict"]
        assert report["robustness"] == report["delivery_integrity"] == "NOT_RUN"
        assert report["allowed_actions"] == {
            "u9": True,
            "e2e": True,
            "production_level3": False,
        }
        assert report["image_ids"] == args["expected_image_ids"]
        assert report["reset_attempt_id"] is None
        assert set(report) == {
            "profile",
            "phase",
            "checked_at",
            "repository_root",
            "head",
            "evaluated_revision",
            "integrity",
            "failed_checks",
            "agent_verdict",
            "verdict_reason",
            "robustness",
            "delivery_integrity",
            "allowed_actions",
            "image_ids",
            "reset_attempt_id",
        }
        assert all(
            key not in json.dumps(report)
            for key in ("validator_command", "demo_ack", "budget_policy")
        )
    assert receipt_path.read_bytes() == receipt_bytes
    assert git(args["repository"], "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "integrity,robustness,delivery",
    list(
        product(
            ["PASS", "FAIL"], ["PASS", "FAIL", "NOT_RUN"], ["PASS", "FAIL", "NOT_RUN"]
        )
    ),
)
def test_three_gate_truth_table_does_not_take_research_verdict(
    integrity, robustness, delivery
):
    result = allowed_actions(integrity, robustness, delivery)
    assert result["u9"] == result["e2e"] == (integrity == "PASS")
    assert result["production_level3"] == (
        integrity == robustness == delivery == "PASS"
    )


@pytest.mark.parametrize(
    "values",
    [("UNKNOWN", "PASS", "PASS"), ("PASS", True, "PASS"), ("PASS", "PASS", None)],
)
def test_invalid_axis_values_are_not_success(values):
    with pytest.raises(EvidenceError, match="^U10_GATE_AXIS_INVALID$"):
        allowed_actions(*values)


@pytest.mark.parametrize(
    "fault,code",
    [
        ("artifact_mode", "COMPONENT_FILE_INVALID"),
        ("benchmark_pin", "PINNED_BENCHMARK_SHA_MISMATCH"),
        ("stopped", "U10_CONTAINER_NOT_RUNNING"),
        ("readback", "U10_RUNTIME_READBACK_INVALID"),
        ("http", "U10_READINESS_HTTP_STATUS_INVALID"),
        ("unexpected", "U10_CLI_FAILED"),
    ],
)
def test_preflight_failure_json_and_exit_are_safe(
    tmp_path, monkeypatch, capsys, fault, code
):
    args, containers, _, _ = setup(tmp_path, monkeypatch)
    emit(args, capsys)
    if fault == "artifact_mode":
        args["artifact"].chmod(0o644)
    elif fault == "benchmark_pin":
        args["pinned_benchmark_sha256"] = "0" * 64
    elif fault == "stopped":
        containers[args["container_ids"]["backend"]]["running"] = False
    elif fault == "readback":
        args["read"] = lambda *_: {"private": "password"}
    elif fault == "http":
        from app.agent.u10_readiness import ProbeResponse

        args["fetch"] = lambda *_: ProbeResponse(503)
    else:

        def fail(*_):
            raise RuntimeError("postgresql://user:private@host/db")

        args["inspect"] = fail
    assert run_preflight(args) == 1
    report = output(capsys)
    assert report["integrity"] == "FAIL" and report["failed_checks"] == [code]
    assert not any(report["allowed_actions"].values())
    assert (
        report["agent_verdict"] is report["head"] is report["repository_root"] is None
    )
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    "argv", [[], ["--profile", "private-secret"], ["--password", "private-secret"]]
)
def test_parser_failures_never_echo_arguments(capsys, argv):
    assert preflight_main(argv) == 1
    assert output(capsys)["failed_checks"] == ["U10_CLI_ARGUMENT_INVALID"]
    assert emit_main(argv) == 1
    assert output(capsys) == {"status": "FAIL", "code": "U10_CLI_ARGUMENT_INVALID"}


def test_duplicate_pins_fail_before_runtime(tmp_path, monkeypatch, capsys):
    args, _, events, _ = setup(tmp_path, monkeypatch)
    argv = preflight_argv(args) + [
        "--image-id",
        "backend=" + args["expected_image_ids"]["backend"],
    ]
    assert run_preflight(args, argv) == 1
    assert output(capsys)["failed_checks"] == ["U10_CLI_PIN_INVALID"]
    assert events == []


def test_receipt_no_clobber_preserves_original(tmp_path, monkeypatch, capsys):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    emit(args, capsys)
    before = args["evaluation_receipt"].read_bytes()
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys)["code"] == "ARTIFACT_EXISTS"
    assert args["evaluation_receipt"].read_bytes() == before


@pytest.mark.parametrize(
    "state,code",
    [("feature", "U10_MAIN_REQUIRED"), ("dirty", "U10_WORKTREE_NOT_CLEAN")],
)
def test_emitter_rejects_non_execution_revision_before_publish(
    tmp_path, monkeypatch, capsys, state, code
):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    if state == "feature":
        git(args["repository"], "checkout", "-qb", "feature")
    else:
        (args["repository"] / "backend/source.txt").write_text("dirty")
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys)["code"] == code
    assert not (args["repository"] / "output").exists()


def test_emitter_rejects_symlink_output_parent(tmp_path, monkeypatch, capsys):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = args["repository"] / "output"
    root.mkdir()
    (root / "v5-c-7.1").symlink_to(outside, target_is_directory=True)
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys)["code"] == "U10_RECEIPT_DIRECTORY_INVALID"
    assert list(outside.iterdir()) == []


def test_emitter_input_drift_during_real_validator_is_rejected(
    tmp_path, monkeypatch, capsys
):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    original = subprocess.run

    def run(command, **kwargs):
        result = original(command, **kwargs)
        if any(str(p).endswith("verify_u10_comparison.py") for p in command):
            args["artifact"].write_bytes(args["artifact"].read_bytes() + b" ")
        return result

    monkeypatch.setattr(u10_receipt.subprocess, "run", run)
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys)["code"] == "U10_RECEIPT_INPUT_DRIFT"
    assert not (args["repository"] / "output").exists()


@pytest.mark.parametrize(
    "script", ["u10_preflight.py", "emit_u10_evaluation_receipt.py"]
)
def test_script_entrypoint_errors_are_single_json(script):
    path = Path(__file__).resolve().parents[2] / "scripts" / script
    result = subprocess.run(
        [sys.executable, str(path), "--private-secret"], capture_output=True, text=True
    )
    assert result.returncode == 1 and result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert "private-secret" not in result.stdout


def test_receipt_is_accepted_by_existing_evaluation_validator(
    tmp_path, monkeypatch, capsys
):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    emit(args, capsys)
    result = verify_evaluation(
        **{
            k: args[k]
            for k in (
                "artifact",
                "evaluation_receipt",
                "benchmark",
                "pinned_benchmark_sha256",
            )
        }
    )
    assert result.receipt.validator_exit_code == 0


@pytest.mark.parametrize(
    "fault", ["exit", "oversize", "json", "sha", "timeout", "oserror"]
)
def test_emitter_validator_failure_never_publishes(
    tmp_path, monkeypatch, capsys, fault
):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    original = subprocess.run
    artifact = parse_json(args["artifact"].read_bytes())
    expected = {
        "integrity": "PASS",
        "artifact_sha256": digest(args["artifact"].read_bytes()),
        "agent_verdict": artifact["result"]["agent_verdict"],
        "verdict_reason": artifact["result"]["verdict_reason"],
        "inspection_only": True,
    }

    def run(command, **kwargs):
        if not any(str(p).endswith("verify_u10_comparison.py") for p in command):
            return original(command, **kwargs)
        if fault == "timeout":
            raise subprocess.TimeoutExpired("private-command", 30)
        if fault == "oserror":
            raise OSError("private-path")
        if fault == "sha":
            expected["artifact_sha256"] = "0" * 64
        body = canonical_json(expected)
        if fault == "oversize":
            body += b" " * (16385 - len(body))
        if fault == "json":
            body = b"private-output"
        return SimpleNamespace(
            returncode=1 if fault == "exit" else 0, stdout=body, stderr=b"private-error"
        )

    monkeypatch.setattr(u10_receipt.subprocess, "run", run)
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys) == {"status": "FAIL", "code": "U10_RECEIPT_VALIDATOR_FAILED"}
    assert not (args["repository"] / "output").exists()


@pytest.mark.parametrize("fault", ["head", "dirty"])
def test_emitter_rechecks_revision_after_verifier(tmp_path, monkeypatch, capsys, fault):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    original = subprocess.run

    def run(command, **kwargs):
        result = original(command, **kwargs)
        if any(str(p).endswith("verify_u10_comparison.py") for p in command):
            if fault == "head":
                git(args["repository"], "commit", "--allow-empty", "-qm", "drift")
            else:
                (args["repository"] / "backend/source.txt").write_text("dirty")
        return result

    monkeypatch.setattr(u10_receipt.subprocess, "run", run)
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys)["code"] == (
        "U10_EXECUTION_REVISION_MISMATCH"
        if fault == "head"
        else "U10_WORKTREE_NOT_CLEAN"
    )
    assert not (args["repository"] / "output").exists()


def test_bundle_imports_do_not_load_providers():
    code = """
import sys, subprocess, httpx
def fail(*args, **kwargs): raise AssertionError('IO on import')
subprocess.run = fail
httpx.Client = fail
import scripts.u10_preflight
import scripts.emit_u10_evaluation_receipt
assert 'app.common.config' not in sys.modules
assert 'app.common.db' not in sys.modules
assert 'app.common.llm' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("reason", ["NO_GAIN", "COST_CAP_EXCEEDED"])
def test_cli_preserves_each_negative_reason(tmp_path, monkeypatch, capsys, reason):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    artifact = parse_json(args["artifact"].read_bytes())
    benchmark = parse_json(args["benchmark"].read_bytes())
    for row in react_rows(artifact):
        row["end_to_end_latency_ms"] = 1251 if reason == "NO_GAIN" else 1501
    if reason == "COST_CAP_EXCEEDED":
        for row in artifact["attempts"]:
            if row["policy"] == "FIXED_POLICY_V21":
                row["cited_evidence_ids"] = ids("CURRENT_FDC")
    artifact["result"] = recompute(artifact, benchmark)
    args["artifact"].write_bytes(canonical_json(artifact))
    emit(args, capsys)
    assert run_preflight(args) == 0
    report = output(capsys)
    assert report["verdict_reason"] == reason
    assert report["agent_verdict"] == "AGENT_JUSTIFICATION_NOT_ESTABLISHED_V21"
    assert report["allowed_actions"]["production_level3"] is False


def test_private_receipt_metadata_is_not_echoed(tmp_path, monkeypatch, capsys):
    args, _, _, _ = setup(tmp_path, monkeypatch)
    emit(args, capsys)
    receipt = parse_json(args["evaluation_receipt"].read_bytes())
    receipt["validator_command"] = ["postgresql://user:secret-token@host/private-db"]
    args["evaluation_receipt"].write_bytes(canonical_json(receipt))
    assert run_preflight(args) == 0
    assert "secret-token" not in json.dumps(output(capsys))


def test_existing_revision_directory_is_not_chmodded(tmp_path, monkeypatch, capsys):
    args, _, _, revision = setup(tmp_path, monkeypatch)
    root = args["repository"] / "output/v5-c-7.1" / revision
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    assert emit_main(emit_argv(args)) == 1
    assert output(capsys)["code"] == "U10_RECEIPT_DIRECTORY_INVALID"
    assert stat.S_IMODE(root.stat().st_mode) == 0o755 and list(root.iterdir()) == []
