from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rebuild_runner as runner  # noqa: E402
import rehearse_schema as wrapper  # noqa: E402
from rehearsal_postgres import RehearsalEndpoint, RehearsalError  # noqa: E402


def _json_line(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


@pytest.fixture
def fixture_artifacts(tmp_path: Path) -> tuple[Path, runner.ArtifactPaths]:
    sql = b"CREATE TABLE fixture_table(id integer PRIMARY KEY);\n"
    member = "project/repository/sample/schema/03_schema_clean.sql"
    archive_path = tmp_path / "fixture.zip"
    info = zipfile.ZipInfo(member, date_time=(2026, 8, 20, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, sql)
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    sql_sha = hashlib.sha256(sql).hexdigest()

    epoch_path = tmp_path / "dataset-epoch.json"
    manifest_path = tmp_path / "source-manifest-v4.json"
    intake_path = tmp_path / "final-zip-intake.json"
    shutil.copyfile(runner.DATASET_EPOCH_PATH, epoch_path)
    shutil.copyfile(runner.SOURCE_MANIFEST_PATH, manifest_path)
    shutil.copyfile(runner.INTAKE_PATH, intake_path)

    epoch = json.loads(epoch_path.read_text(encoding="utf-8"))
    epoch["archive"]["sha256"] = archive_sha
    epoch_path.write_text(json.dumps(epoch), encoding="utf-8")

    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    intake["archive"]["sha256"] = archive_sha
    intake_path.write_text(json.dumps(intake), encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_archive_sha256"] = archive_sha
    manifest["schema_sha256"] = sql_sha
    manifest["artifacts"]["schema_sql"]["sha256"] = sql_sha
    manifest["selected_entry_manifest_sha256"] = hashlib.sha256(
        intake_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return archive_path, runner.ArtifactPaths(epoch_path, manifest_path, intake_path)


def _endpoint(profile: str = "runtime") -> RehearsalEndpoint:
    return RehearsalEndpoint(
        host="127.0.0.1",
        port=55432,
        database=f"fdc_rehearsal_{profile}",
        username="postgres",
        password="secret-not-for-output",
        container_id="container-not-for-output",
    )


def test_archive_is_verified_before_lifecycle(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
) -> None:
    archive, artifacts = fixture_artifacts
    archive.write_bytes(archive.read_bytes() + b"tampered")
    called = False

    @contextlib.contextmanager
    def lifecycle(**_: Any) -> Any:
        nonlocal called
        called = True
        yield _endpoint()

    with pytest.raises(RehearsalError) as raised:
        wrapper._run(
            ["--archive", str(archive), "--profile", "runtime"],
            artifact_paths=artifacts,
            lifecycle=lifecycle,
        )
    assert raised.value.reason_code == "ARCHIVE_MISMATCH"
    assert called is False


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink"])
def test_unsafe_archive_is_rejected_before_lifecycle(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths], tmp_path: Path, kind: str
) -> None:
    archive, artifacts = fixture_artifacts
    target = tmp_path / "unsafe"
    if kind == "directory":
        target.mkdir()
    elif kind == "symlink":
        target.symlink_to(archive)
    with pytest.raises(RehearsalError) as raised:
        wrapper._run(
            ["--archive", str(target), "--profile", "runtime"],
            artifact_paths=artifacts,
            lifecycle=lambda **_: pytest.fail("must not start"),
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"


@pytest.mark.parametrize(
    ("text", "code", "expected"),
    [
        ("", 0, wrapper.RunnerOutcome(0, None)),
        (
            '{"reason_code":"TARGET_NOT_FRESH","status":"FAILED"}\n',
            1,
            wrapper.RunnerOutcome(1, "TARGET_NOT_FRESH"),
        ),
        ("", 1, wrapper.RunnerOutcome(2, "INTERNAL_ERROR")),
        ("not-json", 1, wrapper.RunnerOutcome(2, "INTERNAL_ERROR")),
        (
            '{"reason_code":"RAW_SECRET","status":"FAILED"}',
            1,
            wrapper.RunnerOutcome(2, "INTERNAL_ERROR"),
        ),
        (
            '{"reason_code":"LOCK_BUSY"}\n{"reason_code":"LOCK_BUSY"}',
            1,
            wrapper.RunnerOutcome(2, "INTERNAL_ERROR"),
        ),
    ],
)
def test_runner_stderr_adapter_is_fail_closed(
    text: str, code: int, expected: wrapper.RunnerOutcome
) -> None:
    assert wrapper._parse_runner_output(text, code) == expected


def test_wrapper_emits_primary_only_after_cleanup(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive, artifacts = fixture_artifacts
    events: list[str] = []

    @contextlib.contextmanager
    def lifecycle(**_: Any) -> Any:
        events.append("start")
        yield _endpoint()
        events.append("cleanup")

    monkeypatch.setattr(
        wrapper,
        "_call_runner",
        lambda *_args, **_kwargs: (
            events.append("runner") or wrapper.RunnerOutcome(1, "TARGET_NOT_FRESH")
        ),
    )
    with pytest.raises(RehearsalError) as raised:
        wrapper._run(
            ["--archive", str(archive), "--profile", "runtime"],
            artifact_paths=artifacts,
            lifecycle=lifecycle,
        )
    assert raised.value.reason_code == "TARGET_NOT_FRESH"
    assert events == ["start", "runner", "cleanup"]
    assert capsys.readouterr().err == ""


def test_cleanup_failure_keeps_runner_primary_reason(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, artifacts = fixture_artifacts

    @contextlib.contextmanager
    def lifecycle(**_: Any) -> Any:
        yield _endpoint()
        raise RehearsalError("REHEARSAL_CLEANUP_FAILED", 2)

    monkeypatch.setattr(
        wrapper,
        "_call_runner",
        lambda *_args, **_kwargs: wrapper.RunnerOutcome(1, "TARGET_NOT_FRESH"),
    )
    with pytest.raises(RehearsalError) as raised:
        wrapper._run(
            ["--archive", str(archive), "--profile", "runtime"],
            artifact_paths=artifacts,
            lifecycle=lifecycle,
        )
    assert raised.value.reason_code == "REHEARSAL_CLEANUP_FAILED"
    assert raised.value.primary_reason_code == "TARGET_NOT_FRESH"


def test_main_help_and_invalid_argument_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert wrapper.main(["--help"]) == 0
    help_output = capsys.readouterr()
    assert "usage:" in help_output.out
    assert help_output.err == ""

    assert wrapper.main(["--unknown", "raw-secret"]) == 2
    invalid = capsys.readouterr()
    assert invalid.out == ""
    assert _json_line(invalid.err) == {"reason_code": "ARG_INVALID", "status": "FAILED"}
    assert "raw-secret" not in invalid.err


def test_main_maps_keyboard_interrupt_and_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        wrapper,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    assert wrapper.main([]) == 2
    assert _json_line(capsys.readouterr().err) == {
        "reason_code": "INTERRUPTED",
        "status": "FAILED",
    }

    monkeypatch.setattr(
        wrapper,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("raw-secret")),
    )
    assert wrapper.main([]) == 2
    unexpected = capsys.readouterr().err
    assert _json_line(unexpected) == {
        "reason_code": "INTERNAL_ERROR",
        "status": "FAILED",
    }
    assert "raw-secret" not in unexpected


def test_runner_receives_only_wrapper_owned_rehearsal_endpoint(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, artifacts = fixture_artifacts
    captured: dict[str, Any] = {}

    def fake_run(*_args: Any, **kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(wrapper.rebuild_runner, "run", fake_run)
    outcome = wrapper._call_runner(
        _endpoint(),
        profile="runtime",
        artifact_paths=artifacts,
        handler=lambda *_: None,
        postcheck=lambda *_: None,
    )
    assert outcome == wrapper.RunnerOutcome(0, None)
    assert set(captured["environ"]) == {
        "POSTGRES_REHEARSAL_HOST",
        "POSTGRES_REHEARSAL_PORT",
        "POSTGRES_REHEARSAL_DATABASE",
        "POSTGRES_REHEARSAL_USER",
        "POSTGRES_REHEARSAL_PASSWORD",
    }
    assert not any(key.startswith("POSTGRES_BOOTSTRAP_") for key in captured["environ"])


def test_error_payload_shape_and_allowlist_are_sanitized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = wrapper._emit_error(
        RehearsalError(
            "REHEARSAL_CLEANUP_FAILED",
            2,
            primary_reason_code="unknown-raw-secret",
        )
    )
    assert result == 2
    assert _json_line(capsys.readouterr().err) == {
        "primary_reason_code": "INTERNAL_ERROR",
        "reason_code": "REHEARSAL_CLEANUP_FAILED",
        "status": "FAILED",
    }


def test_public_parser_has_no_hash_path_or_target_override() -> None:
    destinations = {action.dest for action in wrapper._parser()._actions}
    assert destinations == {"help", "archive", "profile"}
    assert runner.MODE_HANDLERS == {}
    assert runner.POSTCHECKS == {}
