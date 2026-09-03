from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPOSITORY_ROOT / "deploy" / "compose"
STAGE2 = COMPOSE_DIR / "cm52_stage2.sh"
SET_PATHS = COMPOSE_DIR / "set_artifact_paths.py"
ASSERT_0600 = COMPOSE_DIR / "assert_owned_0600.py"
SECRET_SCAN = COMPOSE_DIR / "scan_cm52_artifacts.py"
ATTEMPT = "20260902T010203Z-0123456789ab"
REVISION = "0123456789abcdef0123456789abcdef01234567"
PREVIOUS_REVISION = "f" * 40
PREV_ATTEMPT = "20260901T000000Z-aaaaaaaaaaaa"
PREV_FAULT = f"/reports/cm-5.2/{PREV_ATTEMPT}/fault-5class.json"
PREV_GOLDEN = f"/reports/cm-5.2/{PREV_ATTEMPT}/golden-flow.json"
NEW_FAULT = f"/reports/cm-5.2/{ATTEMPT}/fault-5class.json"
NEW_GOLDEN = f"/reports/cm-5.2/{ATTEMPT}/golden-flow.json"
OBSERVER_ARTIFACT_TYPE = "cm52_public_database_observer"


def _env_file(path: Path, fault: str = PREV_FAULT, golden: str = PREV_GOLDEN) -> None:
    path.write_text(
        "# preserve this comment\n"
        "APP_DB_PASSWORD=do-not-print-this-secret\n"
        f"AGENT_FAULT_EVAL_ARTIFACT_PATH={fault}\n"
        "UNRELATED=value with spaces\n"
        f"AGENT_GOLDEN_FLOW_SUMMARY_PATH={golden}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _values(path: Path) -> tuple[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return (
        values["AGENT_FAULT_EVAL_ARTIFACT_PATH"],
        values["AGENT_GOLDEN_FLOW_SUMMARY_PATH"],
    )


def _write_observer_baseline(
    path: Path,
    *,
    artifact_type: str = OBSERVER_ARTIFACT_TYPE,
    mode: int = 0o600,
) -> None:
    path.write_text(
        json.dumps(
            {"artifact_type": artifact_type, "format_version": 1},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    path.chmod(mode)


def _run_stage2(
    tmp_path: Path,
    *,
    fail_at: str = "",
    stage_pass: int = 1,
    original_rc: int = 0,
    fault: str = PREV_FAULT,
    golden: str = PREV_GOLDEN,
    previous_revision: str = PREVIOUS_REVISION,
    running_revision: str = PREVIOUS_REVISION,
    extra_args: tuple[str, ...] = (),
    reuse_attempt: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    env_file = tmp_path / ".env.team"
    report_root = tmp_path / "reports"
    attempt_dir = report_root / "cm-5.2" / ATTEMPT
    if not reuse_attempt:
        _env_file(env_file, fault=fault, golden=golden)
        attempt_dir.mkdir(parents=True)
        _write_observer_baseline(attempt_dir / "observer-baseline.json")
    environment = {
        **os.environ,
        "CM52_ENV_FILE": str(env_file),
        "CM52_REPORT_ROOT": str(report_root),
        "CM52_ATTEMPT_DIR": str(attempt_dir),
        "CM52_REVISION": REVISION,
        "CM52_STAGE2_TEST_MODE": "1",
        "CM52_STAGE2_FAIL_AT": fail_at,
        "CM52_TEST_STAGE2_PASS": str(stage_pass),
        "CM52_TEST_ORIGINAL_RC": str(original_rc),
        "CM52_TEST_PREV_REV": previous_revision,
        "CM52_TEST_RUNNING_REV": running_revision,
    }
    completed = subprocess.run(
        ["bash", str(STAGE2), *extra_args, "--attempt-id", ATTEMPT],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed, env_file, attempt_dir / "stage2-log.jsonl"


def _outcome(log: Path) -> str | None:
    cleanup = _cleanup_records(log)
    return cleanup[-1]["outcome"] if cleanup else None


def _cleanup_records(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    lines = [json.loads(line) for line in log.read_text().splitlines() if line]
    return [line for line in lines if line.get("step") == "cleanup"]


def test_set_artifact_paths_changes_only_two_values_and_preserves_mode(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.team"
    _env_file(env_file)
    before = env_file.read_bytes()

    completed = subprocess.run(
        ["python3", str(SET_PATHS), str(env_file), NEW_FAULT, NEW_GOLDEN],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == completed.stderr == ""
    assert _values(env_file) == (NEW_FAULT, NEW_GOLDEN)
    after = env_file.read_bytes()
    for line in (
        b"# preserve this comment\n",
        b"APP_DB_PASSWORD=do-not-print-this-secret\n",
        b"UNRELATED=value with spaces\n",
    ):
        assert before.count(line) == after.count(line) == 1
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    get = subprocess.run(
        [
            "python3",
            str(SET_PATHS),
            "--get",
            str(env_file),
            "AGENT_FAULT_EVAL_ARTIFACT_PATH",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert get.returncode == 0
    assert get.stdout.strip() == NEW_FAULT
    rejected = subprocess.run(
        ["python3", str(SET_PATHS), "--get", str(env_file), "APP_DB_PASSWORD"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    assert "do-not-print-this-secret" not in rejected.stdout + rejected.stderr


def test_assert_owned_0600_rejects_mode_and_symlink_without_path_leak(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    artifact.chmod(0o600)
    ok = subprocess.run(
        ["python3", str(ASSERT_0600), str(artifact)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0

    artifact.chmod(0o644)
    bad = subprocess.run(
        ["python3", str(ASSERT_0600), str(artifact)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    assert bad.stderr.strip() == "ARTIFACT_OWNER_MODE_INVALID"
    assert str(artifact) not in bad.stderr

    artifact.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(artifact)
    linked = subprocess.run(
        ["python3", str(ASSERT_0600), str(link)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert linked.returncode == 1


def test_attempt_secret_scan_blocks_env_values_and_fixed_questions(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.team"
    _env_file(env_file)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    evidence = attempt / "receipt.json"
    evidence.write_text('{"status":"PASS"}', encoding="utf-8")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SECRET_SCAN),
                "--root",
                str(attempt),
                "--env-file",
                str(env_file),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    assert run().returncode == 0
    evidence.write_text("do-not-print-this-secret", encoding="utf-8")
    leaked = run()
    assert leaked.returncode == 1
    assert "do-not-print-this-secret" not in leaked.stdout + leaked.stderr

    from scripts.e2e_analytics_questions import QUESTIONS

    evidence.write_text(QUESTIONS[0], encoding="utf-8")
    raw_question = run()
    assert raw_question.returncode == 1
    assert QUESTIONS[0] not in raw_question.stdout + raw_question.stderr


@pytest.mark.parametrize(
    ("fail_at", "expected_rc", "expected_outcome", "expected_paths"),
    [
        ("", 0, "PUBLISHED", (NEW_FAULT, NEW_GOLDEN)),
        ("e2e_down", 1, "PUBLISH_FAILED", (PREV_FAULT, PREV_GOLDEN)),
        ("publish_apply", 1, "PUBLISH_FAILED", (PREV_FAULT, PREV_GOLDEN)),
        ("publish_preflight", 1, "PUBLISH_FAILED", (PREV_FAULT, PREV_GOLDEN)),
        ("publish_up", 1, "PUBLISH_FAILED", (PREV_FAULT, PREV_GOLDEN)),
        ("publish_verify", 1, "PUBLISH_FAILED", (PREV_FAULT, PREV_GOLDEN)),
        (
            "publish_verify,restore_up",
            2,
            "RESTORE_FAILED",
            (PREV_FAULT, PREV_GOLDEN),
        ),
        (
            "publish_verify,restore_apply",
            2,
            "RESTORE_FAILED",
            (NEW_FAULT, NEW_GOLDEN),
        ),
        (
            "e2e_down,restore_up",
            2,
            "RESTORE_FAILED",
            (PREV_FAULT, PREV_GOLDEN),
        ),
        ("log_append", 1, None, (PREV_FAULT, PREV_GOLDEN)),
        ("log_after", 1, "PUBLISHED", (PREV_FAULT, PREV_GOLDEN)),
        ("log_precheck", 1, "PUBLISH_FAILED", (PREV_FAULT, PREV_GOLDEN)),
    ],
)
def test_cleanup_outcomes_preserve_the_path_invariant(
    tmp_path: Path,
    fail_at: str,
    expected_rc: int,
    expected_outcome: str | None,
    expected_paths: tuple[str, str],
) -> None:
    completed, env_file, log = _run_stage2(tmp_path, fail_at=fail_at)

    assert completed.returncode == expected_rc, completed.stderr
    assert _values(env_file) == expected_paths
    assert _outcome(log) == expected_outcome
    assert "do-not-print-this-secret" not in completed.stdout + completed.stderr
    if expected_rc == 2:
        assert "RESTORE_FAILED" in completed.stderr
        assert "상태 미보장" in completed.stderr
    if fail_at == "log_precheck":
        assert _cleanup_records(log)[-1]["original_rc"] == expected_rc


def test_fail_or_hold_restores_original_rc_and_paths(tmp_path: Path) -> None:
    completed, env_file, log = _run_stage2(tmp_path, stage_pass=0, original_rc=3)

    assert completed.returncode == 3
    assert _values(env_file) == (PREV_FAULT, PREV_GOLDEN)
    assert _outcome(log) == "RESTORED"


def test_invalid_previous_binding_stops_before_cleanup(tmp_path: Path) -> None:
    completed, env_file, log = _run_stage2(tmp_path, golden="")

    assert completed.returncode == 1
    assert "PREV_STATE_INVALID" in completed.stderr
    assert _values(env_file) == (PREV_FAULT, "")
    assert not log.exists()


def test_stage2_accepts_the_running_previous_revision_before_publishing(
    tmp_path: Path,
) -> None:
    completed, env_file, log = _run_stage2(
        tmp_path,
        previous_revision=PREVIOUS_REVISION,
        running_revision=PREVIOUS_REVISION,
    )

    assert PREVIOUS_REVISION != REVISION
    assert completed.returncode == 0, completed.stderr
    assert _values(env_file) == (NEW_FAULT, NEW_GOLDEN)
    assert _outcome(log) == "PUBLISHED"


def test_stage2_rejects_an_unreadable_running_production_revision(
    tmp_path: Path,
) -> None:
    completed, env_file, log = _run_stage2(
        tmp_path,
        running_revision="not-a-40-character-revision",
    )

    assert completed.returncode == 1
    assert completed.stderr.strip() == "PRODUCTION_REVISION_UNREADABLE"
    assert _values(env_file) == (PREV_FAULT, PREV_GOLDEN)
    assert not log.exists()


@pytest.mark.parametrize("baseline_case", ["missing", "mode", "artifact_type"])
def test_invalid_observer_baseline_stops_before_team_down(
    tmp_path: Path,
    baseline_case: str,
) -> None:
    env_file = tmp_path / ".env.team"
    _env_file(env_file, fault="", golden="")
    report_root = tmp_path / "reports"
    attempt_dir = report_root / "cm-5.2" / ATTEMPT
    attempt_dir.mkdir(parents=True)
    baseline = attempt_dir / "observer-baseline.json"
    if baseline_case == "mode":
        _write_observer_baseline(baseline, mode=0o644)
    elif baseline_case == "artifact_type":
        _write_observer_baseline(baseline, artifact_type="wrong_observer")

    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    docker_marker = tmp_path / "docker-was-called"
    fake_docker = command_dir / "docker"
    fake_docker.write_text(
        '#!/bin/sh\n: > "$CM52_DOCKER_MARKER"\nexit 99\n',
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    completed = subprocess.run(
        ["bash", str(STAGE2), "--attempt-id", ATTEMPT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "PATH": f"{command_dir}{os.pathsep}{os.environ['PATH']}",
            "CM52_DOCKER_MARKER": str(docker_marker),
            "CM52_ENV_FILE": str(env_file),
            "CM52_REPORT_ROOT": str(report_root),
            "CM52_ATTEMPT_DIR": str(attempt_dir),
            "CM52_REVISION": REVISION,
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert not docker_marker.exists()
    assert not (attempt_dir / "stage2-log.jsonl").exists()


def test_stage2_log_is_jsonl_and_plan_is_non_mutating(tmp_path: Path) -> None:
    completed, _env_file_path, log = _run_stage2(tmp_path)
    assert completed.returncode == 0
    assert all(json.loads(line) for line in log.read_text().splitlines())

    env_file = tmp_path / "plan.env"
    _env_file(env_file)
    before = env_file.read_bytes()
    plan = subprocess.run(
        ["bash", str(STAGE2), "--plan", "--attempt-id", ATTEMPT],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "CM52_ENV_FILE": str(env_file)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert plan.returncode == 0
    assert "team down" in plan.stdout
    assert env_file.read_bytes() == before


def test_stage2_shell_syntax_and_non_root_runner_contract() -> None:
    syntax = subprocess.run(
        ["bash", "-n", str(STAGE2)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    common = (COMPOSE_DIR / "cm52_common.sh").read_text(encoding="utf-8")
    assert '--user "$(id -u):$(id -g)"' in common
    assert ".env.team" not in (COMPOSE_DIR / "cm52_stage2.sh").read_text(
        encoding="utf-8"
    ).replace("CM52_ENV_FILE", "")

    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is unavailable")
    checked = subprocess.run(
        [shellcheck, str(COMPOSE_DIR / "cm52_common.sh"), str(STAGE2)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_stage2_requires_manual_observer_baseline_and_verifies_on_host() -> None:
    stage = STAGE2.read_text(encoding="utf-8")
    host_verify = (
        'python3 "$CM52_REPO_ROOT/backend/scripts/observe_public_databases.py" verify'
    )

    assert "observe_public_databases.py capture" not in stage
    assert host_verify in stage
    assert "runner python scripts/observe_public_databases.py verify" not in stage
    assert 'assert_owned_0600 "$A/observer-baseline.json"' in stage
    assert stage.index('assert_owned_0600 "$A/observer-baseline.json"') < stage.index(
        "\n  team down\n"
    )


def test_stage2_uses_running_revision_then_current_after_restore() -> None:
    stage = STAGE2.read_text(encoding="utf-8")
    verify_function = stage.split("verify_prev_state()", maxsplit=1)[1].split(
        "restore_prev()", maxsplit=1
    )[0]
    restore_function = stage.split("restore_prev()", maxsplit=1)[1].split(
        "publish_new()", maxsplit=1
    )[0]

    assert "team exec -T backend printenv BISTEL_SOURCE_REVISION" in stage
    assert 'verify_prev_state "$RUNNING_REV"' in stage
    assert 'verify_prev_state "$REV"' in restore_function
    assert '--expect-revision "$PREV_REV"' in verify_function
    assert (
        '--expect-container-revision "$expected_container_revision"' in verify_function
    )


def test_stage2_postcondition_requires_v2_complete_exact_distribution() -> None:
    stage = STAGE2.read_text(encoding="utf-8")

    assert "prompt_version='agent-hypothesis-v2-ko1'" in stage
    # kosa_readonly는 agent_run을 못 읽는다(C-0.2 allowlist) — kosa_app engine으로 센다.
    assert "get_app_engine(); c=e.connect(); q=text(" in stage
    assert "pool_factory.get_engine(LogicalDb.RUNTIME,PoolRole.QUERY)" not in stage
    assert "status IN ('RUNNING','FAILED')" in stage
    assert "grep -q '(12, 12, 12, 0, 0, 5, 4, 3, 0)'" in stage


def _hold_records(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    lines = [json.loads(line) for line in log.read_text().splitlines() if line]
    return [line for line in lines if line.get("step") == "hold"]


def test_hold_after_5d_keeps_e2e_and_previous_paths_without_cleanup(
    tmp_path: Path,
) -> None:
    """--hold-after 5d 는 복구·게시 없이 HELD_FOR_GOLDEN_FLOW 만 남기고 0으로 끝난다."""

    completed, env_file, log = _run_stage2(tmp_path, extra_args=("--hold-after", "5d"))

    assert completed.returncode == 0, completed.stderr
    assert _values(env_file) == (PREV_FAULT, PREV_GOLDEN)
    assert _cleanup_records(log) == []
    (hold,) = _hold_records(log)
    assert hold["outcome"] == "HELD_FOR_GOLDEN_FLOW"
    assert hold["attempt"] == ATTEMPT
    assert hold["running_rev"] == PREVIOUS_REVISION
    assert hold["last_ok_step"] == "5d"
    assert (hold["prev_fault"], hold["prev_golden"]) == (PREV_FAULT, PREV_GOLDEN)
    assert "do-not-print-this-secret" not in completed.stdout + completed.stderr


def test_hold_with_failure_before_5d_still_restores(tmp_path: Path) -> None:
    completed, env_file, log = _run_stage2(
        tmp_path, extra_args=("--hold-after", "5d"), original_rc=4
    )

    assert completed.returncode == 4
    assert _hold_records(log) == []
    assert _outcome(log) == "RESTORED"
    assert _values(env_file) == (PREV_FAULT, PREV_GOLDEN)


def test_hold_record_write_failure_falls_back_to_restore(tmp_path: Path) -> None:
    completed, env_file, log = _run_stage2(
        tmp_path, extra_args=("--hold-after", "5d"), fail_at="log_hold"
    )

    assert completed.returncode == 1
    assert "HOLD_RECORD_WRITE_FAILED" in completed.stderr
    assert _hold_records(log) == []
    # hold 포기 → E2E down → 이전 production 복원. rc 1은 hold 실패를 그대로 전달한다.
    assert _outcome(log) == "RESTORED"
    assert _cleanup_records(log)[-1]["original_rc"] == 1
    assert _values(env_file) == (PREV_FAULT, PREV_GOLDEN)


def test_resume_from_6_requires_a_matching_hold_record(tmp_path: Path) -> None:
    completed, env_file, log = _run_stage2(tmp_path, extra_args=("--resume-from", "6"))

    assert completed.returncode == 1
    assert "HOLD_RECORD_REQUIRED" in completed.stderr
    assert _values(env_file) == (PREV_FAULT, PREV_GOLDEN)
    assert not log.exists()


def test_hold_then_resume_publishes_with_the_held_running_revision(
    tmp_path: Path,
) -> None:
    held, env_file, log = _run_stage2(tmp_path, extra_args=("--hold-after", "5d"))
    assert held.returncode == 0, held.stderr

    resumed, _env, _log = _run_stage2(
        tmp_path,
        extra_args=("--resume-from", "6"),
        reuse_attempt=True,
        running_revision="",  # production은 내려가 있어 조회 불가 — hold 기록만 쓴다
    )

    assert resumed.returncode == 0, resumed.stderr
    assert _values(env_file) == (NEW_FAULT, NEW_GOLDEN)
    assert len(_hold_records(log)) == 1
    assert _outcome(log) == "PUBLISHED"


def test_resume_rejects_a_hold_record_whose_previous_paths_changed(
    tmp_path: Path,
) -> None:
    held, env_file, log = _run_stage2(tmp_path, extra_args=("--hold-after", "5d"))
    assert held.returncode == 0, held.stderr
    _env_file(env_file, fault="", golden="")

    resumed, _env, _log = _run_stage2(
        tmp_path, extra_args=("--resume-from", "6"), reuse_attempt=True
    )

    assert resumed.returncode == 1
    assert "HOLD_RECORD_REQUIRED" in resumed.stderr
    assert _cleanup_records(log) == []


def test_hold_and_resume_flags_are_exclusive_and_exact() -> None:
    for args in (
        ("--hold-after", "6"),
        ("--resume-from", "5d"),
        ("--hold-after", "5d", "--resume-from", "6"),
    ):
        completed = subprocess.run(
            ["bash", str(STAGE2), *args, "--attempt-id", ATTEMPT],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 2, args
        assert "usage:" in completed.stderr
