"""Stage2's real Bash parser/read-only entry. No Docker/runtime subprocesses."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent import release_entry as m
from app.agent.release_artifacts import EvidenceError, canonical_json, write_private
from app.agent.release_lifecycle import claim_filename, terminal_filename
from tests.unit.test_agent_release import ATTEMPT, claim, outcome, prepared_payload

REPO = Path(__file__).resolve().parents[3]
STAGE2 = REPO / "deploy/compose/cm52_stage2.sh"


@pytest.fixture
def entry(tmp_path):
    report = tmp_path / "reports"
    report.mkdir(mode=0o700)
    (report / "cm-5.2").mkdir(mode=0o700)
    attempt = report / "cm-5.2" / ATTEMPT
    attempt.mkdir(mode=0o700)
    bundle = attempt / "robustness"
    bundle.mkdir(mode=0o700)
    write_private(bundle, "prepared-attempt.json", prepared_payload())
    return dict(
        report_root=report,
        repository=REPO,
        attempt_id=ATTEMPT,
        mode="abort",
        prepared_path=bundle / "prepared-attempt.json",
    )


def run(args, entry=None):
    env = dict(os.environ, CM52_HOST_PYTHON=sys.executable)
    if entry:
        env["CM52_REPORT_ROOT"] = str(entry["report_root"])
    return subprocess.run(
        ["bash", str(STAGE2), *args, "--attempt-id", ATTEMPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "args,mode",
    [
        ([], "full"),
        (["--hold-after", "5d"], "hold"),
        (["--resume-from", "6"], "resume"),
        (["--hold-after", "5d", "--prepare-only"], "prepare"),
        (["--prepare-only", "--hold-after", "5d"], "prepare"),
        (
            [
                "--hold-after",
                "5d",
                "--resume-workload",
                "--prepared-attempt",
                "/missing",
                "--approval-record",
                "/grant",
            ],
            "resume_workload",
        ),
        (
            [
                "--approval-record",
                "/grant",
                "--prepared-attempt",
                "/missing",
                "--resume-workload",
                "--hold-after",
                "5d",
            ],
            "resume_workload",
        ),
        (["--abort-prepared", "/missing"], "abort"),
        (["--recover-prepared", "/missing"], "recover"),
    ],
)
def test_seven_modes_plan_does_not_open_files_or_require_env(args, mode):
    result = run(["--plan", *args])
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("mode=" + mode + "\n")
    if mode in {"prepare", "resume_workload"}:
        assert "MOCK-NOTIFY-V1" in result.stdout
        assert "NOT OPERATIONAL" not in result.stdout
    if mode in {"abort", "recover"}:
        assert "O_EXCL terminal" in result.stdout
        assert "NOT OPERATIONAL" not in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--prepare-only"],
        ["--resume-workload"],
        ["--hold-after", "5d", "--resume-workload", "--prepared-attempt", "/missing"],
        ["--hold-after", "5d", "--resume-workload", "--approval-record", "/grant"],
        ["--hold-after", "5d", "--prepare-only", "--resume-workload"],
        ["--hold-after", "5d", "--prepare-only", "--approval-record", "/grant"],
        ["--hold-after", "5d", "--prepare-only", "--prepared-attempt", "/missing"],
        ["--prepared-attempt", "/missing"],
        ["--approval-record", "/grant"],
        ["--abort-prepared", "/missing", "--hold-after", "5d"],
        ["--recover-prepared", "/missing", "--hold-after", "5d"],
        ["--abort-prepared", "/missing", "--recover-prepared", "/missing"],
        ["--resume-from", "6", "--prepare-only"],
        ["--resume-from", "6", "--abort-prepared", "/missing"],
        ["--resume-from", "6", "--recover-prepared", "/missing"],
        ["--abort-prepared", "/missing", "--approval-record", "/grant"],
        ["--recover-prepared", "/missing", "--approval-record", "/grant"],
        ["--prepare-only", "--prepare-only", "--hold-after", "5d"],
        ["--abort-prepared", "--plan"],
        ["--recover-prepared"],
    ],
)
def test_invalid_mode_combinations_fail_at_parser_even_with_plan(args):
    result = run(["--plan", *args])
    assert result.returncode == 2
    assert "usage:" in result.stderr and not result.stdout


@pytest.mark.parametrize("mode", ["abort", "resume_workload"])
def test_prepared_inspection_is_private_read_only_not_execution(entry, mode):
    entry["mode"] = mode
    before = {p: p.read_bytes() for p in entry["report_root"].rglob("*") if p.is_file()}
    result = m.inspect_entry(**entry)
    assert result["observed_state"] == "PREPARED"
    assert not result["execution_authorized"] and not result["cleanup_performed"]
    assert result["warning"] == "production is DOWN"
    assert "Team@example.invalid" not in json.dumps(result)
    assert {
        p: p.read_bytes() for p in entry["report_root"].rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize("state", ["UNRESOLVED", "TERMINAL", "HELD"])
def test_state_rejection_and_recovery_inspection_does_not_assume_owner_dead(
    entry, state
):
    root = entry["prepared_path"].parent
    files = {"prepared-attempt.json": entry["prepared_path"].read_bytes()}
    phase = "ABORT" if state == "TERMINAL" else "RESUME_WORKLOAD"
    value = claim(files, phase)
    files[claim_filename(phase)] = canonical_json(value) + b"\n"
    write_private(root, claim_filename(phase), value)
    if state != "UNRESOLVED":
        write_private(
            root,
            terminal_filename(phase),
            outcome(files, phase, "ABORTED" if state == "TERMINAL" else "HELD"),
        )
    with pytest.raises(EvidenceError, match="^LIFECYCLE_TRANSITION_INVALID$"):
        m.inspect_entry(**entry)
    if state == "UNRESOLVED":
        entry["mode"] = "recover"
        result = m.inspect_entry(**entry)
        assert result["observed_state"] == state and not result["execution_authorized"]
        assert "INDETERMINATE" in result["warning"]


@pytest.mark.parametrize("part", ["cm-5.2", "attempt", "robustness", "prepared"])
def test_symlink_cannot_redirect_entry_read(entry, tmp_path, part):
    path = {
        "cm-5.2": entry["report_root"] / "cm-5.2",
        "attempt": entry["prepared_path"].parents[1],
        "robustness": entry["prepared_path"].parent,
        "prepared": entry["prepared_path"],
    }[part]
    moved = tmp_path / "moved"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=part != "prepared")
    with pytest.raises(EvidenceError):
        m.inspect_entry(**entry)


def test_observation_drift_is_not_ignored(entry, monkeypatch):
    read = m.read_lifecycle
    calls = 0

    def changed(*a, **k):
        nonlocal calls
        result = read(*a, **k)
        calls += 1
        if calls == 2:
            result["prepared-attempt.json"] += b" "
        return result

    monkeypatch.setattr(m, "read_lifecycle", changed)
    with pytest.raises(EvidenceError, match="^STAGE2_ENTRY_DRIFT$"):
        m.inspect_entry(**entry)


@pytest.mark.parametrize("mode", ["prepare", "resume_workload", "recover"])
def test_incomplete_inputs_never_enter_legacy_initialization(entry, mode):
    args = (
        ["--hold-after", "5d", "--prepare-only"]
        if mode == "prepare"
        else [
            "--hold-after",
            "5d",
            "--resume-workload",
            "--prepared-attempt",
            str(entry["prepared_path"]),
            "--approval-record",
            "/not-read",
        ]
        if mode == "resume_workload"
        else ["--" + mode + "-prepared", str(entry["prepared_path"])]
    )
    before = {p: p.read_bytes() for p in entry["report_root"].rglob("*") if p.is_file()}
    result = run(args, entry)
    assert result.returncode == 1
    if mode == "prepare":
        assert '"status": "FAIL"' in result.stdout
    elif mode == "resume_workload":
        assert "STAGE2_POLICY_MISMATCH" in result.stdout
    else:
        assert "LIFECYCLE_TRANSITION_INVALID" in result.stdout
    assert {
        p: p.read_bytes() for p in entry["report_root"].rglob("*") if p.is_file()
    } == before
    assert not list(entry["report_root"].rglob("stage2-log.jsonl"))
    assert not list(entry["report_root"].rglob(".lifecycle.lock"))
