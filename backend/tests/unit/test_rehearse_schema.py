from __future__ import annotations

import contextlib
import dataclasses
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

    # profile 적재 경로가 검증하는 9 CSV member. 실제 데이터가 아니라 header 1줄 +
    # 데이터 1줄짜리 최소 fixture이며, manifest/intake도 이 hash로 다시 쓴다.
    real_manifest = json.loads(runner.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    csv_members: dict[str, bytes] = {}
    for entry in real_manifest["tables"].values():
        columns = entry["columns"]
        header = ",".join(columns)
        row = ",".join("" for _ in columns)
        csv_members[entry["file_id"]] = ("\ufeff" + header + "\n" + row + "\n").encode(
            "utf-8"
        )

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, sql)
        for name, payload in csv_members.items():
            csv_info = zipfile.ZipInfo(name, date_time=(2026, 8, 20, 0, 0, 0))
            csv_info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(csv_info, payload)
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
    for entry in intake["selected_members"]:
        payload = csv_members.get(entry["path"])
        if payload is not None:
            entry["size_bytes"] = len(payload)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
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


# --- 구현리뷰 1차 필수 회귀 ---------------------------------------------------------


def test_archive_is_read_exactly_once(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """단일 snapshot 계약: archive path의 `read_bytes()`는 1회다 (필수 4)."""

    archive, artifacts = fixture_artifacts
    original = Path.read_bytes
    reads: list[str] = []

    def counting_read(self: Path) -> bytes:
        if self == archive:
            reads.append(str(self))
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read)
    wrapper._verified_archive_snapshot(archive, artifacts, "runtime")
    assert reads == [str(archive)]


def test_snapshot_is_deeply_immutable(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
) -> None:
    """검증 후 COPY payload·column 계약을 바꿀 수 없다 (필수 2)."""

    archive, artifacts = fixture_artifacts
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, "runtime")

    with pytest.raises(TypeError):
        snapshot.csv_bodies["dim_parameter"] = b"mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.columns_by_table["dim_parameter"][0] = "changed"  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.verified_tables[0].body = b"mutated"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.schema_bytes = b"mutated"  # type: ignore[misc]


def _spy_handlers(events: list[str]):
    def make(name: str, fail: bool = False):
        def fn(_connection: object, _plan: object) -> None:
            events.append(name)
            if fail:
                raise RuntimeError(name)

        return fn

    return make


def test_composite_calls_schema_then_loader_exactly_once(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schema 1회 → loader 1회, postcheck도 같은 순서다 (필수 4)."""

    archive, artifacts = fixture_artifacts
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, "runtime")
    events: list[str] = []
    make = _spy_handlers(events)

    monkeypatch.setattr(
        wrapper.rehearsal_schema,
        "make_handlers",
        lambda *_: (make("schema"), make("schema_post")),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_loader,
        "make_load_handlers",
        lambda *_: (make("load"), make("load_post")),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_verifier,
        "make_acceptance_postcheck",
        lambda *_, **__: make("acceptance_post"),
    )

    handler, postcheck = wrapper._composite(snapshot, "runtime")
    assert handler(object(), object()) is None
    assert postcheck(object(), object()) is None
    assert events == ["schema", "load", "schema_post", "load_post", "acceptance_post"]


@pytest.mark.parametrize(
    ("failing", "expected"),
    [("schema", ["schema"]), ("load", ["schema", "load"])],
)
def test_composite_handler_short_circuits_on_failure(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
    expected: list[str],
) -> None:
    """handler 단계가 실패하면 이후 단계와 postcheck를 부르지 않는다 (필수 4).

    `handler`와 `postcheck`를 한 `pytest.raises` 블록에 넣으면 앞 줄이 던질 때
    뒷줄이 죽은 코드가 되고, 예외가 어느 단계에서 났는지도 구분하지 못한다.
    단계별로 나눠 호출한다(PR #93 리뷰).
    """

    archive, artifacts = fixture_artifacts
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, "runtime")
    events: list[str] = []
    make = _spy_handlers(events)

    monkeypatch.setattr(
        wrapper.rehearsal_schema,
        "make_handlers",
        lambda *_: (make("schema", failing == "schema"), make("schema_post")),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_loader,
        "make_load_handlers",
        lambda *_: (make("load", failing == "load"), make("load_post")),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_verifier,
        "make_acceptance_postcheck",
        lambda *_, **__: make("acceptance_post"),
    )

    handler, postcheck = wrapper._composite(snapshot, "runtime")
    with pytest.raises(RuntimeError, match=failing):
        handler(object(), object())
    assert events == expected
    # postcheck는 호출조차 되지 않아야 한다. 호출됐다면 이벤트가 늘어난다.
    assert "schema_post" not in events
    assert "load_post" not in events
    assert "acceptance_post" not in events


def test_composite_postcheck_short_circuits_on_failure(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schema postcheck가 실패하면 loader postcheck를 부르지 않는다 (필수 4)."""

    archive, artifacts = fixture_artifacts
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, "runtime")
    events: list[str] = []
    make = _spy_handlers(events)

    monkeypatch.setattr(
        wrapper.rehearsal_schema,
        "make_handlers",
        lambda *_: (make("schema"), make("schema_post", True)),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_loader,
        "make_load_handlers",
        lambda *_: (make("load"), make("load_post")),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_verifier,
        "make_acceptance_postcheck",
        lambda *_, **__: make("acceptance_post"),
    )

    handler, postcheck = wrapper._composite(snapshot, "runtime")
    # handler는 정상 종료해야 한다 — 여기서 던지면 postcheck 단계를 검증할 수 없다.
    assert handler(object(), object()) is None
    assert events == ["schema", "load"]

    with pytest.raises(RuntimeError, match="schema_post"):
        postcheck(object(), object())
    assert events == ["schema", "load", "schema_post"]
    assert "load_post" not in events
    assert "acceptance_post" not in events


def test_composite_acceptance_postcheck_runs_last(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """loader 최소 postcheck가 실패하면 full acceptance를 부르지 않는다 (2.4 §3.8)."""

    archive, artifacts = fixture_artifacts
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, "runtime")
    events: list[str] = []
    make = _spy_handlers(events)

    monkeypatch.setattr(
        wrapper.rehearsal_schema,
        "make_handlers",
        lambda *_: (make("schema"), make("schema_post")),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_loader,
        "make_load_handlers",
        lambda *_: (make("load"), make("load_post", True)),
    )
    monkeypatch.setattr(
        wrapper.rehearsal_profile_verifier,
        "make_acceptance_postcheck",
        lambda *_, **__: make("acceptance_post"),
    )

    _, postcheck = wrapper._composite(snapshot, "runtime")
    with pytest.raises(RuntimeError, match="load_post"):
        postcheck(object(), object())
    assert events == ["schema_post", "load_post"]
    assert "acceptance_post" not in events


@pytest.mark.parametrize(("profile", "loaded"), [("runtime", 8), ("evaluation", 9)])
def test_snapshot_keeps_nine_acceptances_regardless_of_profile(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    profile: str,
    loaded: int,
) -> None:
    """`acceptances`는 항상 9종, `verified_tables`는 profile 선택분이다 (2.4 §3.2)."""

    archive, artifacts = fixture_artifacts
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, profile)
    assert len(snapshot.acceptances) == 9
    assert len(snapshot.verified_tables) == loaded
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.acceptances[0].name = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.acceptances[0].column_types["id"] = "text"  # type: ignore[index]


@pytest.mark.parametrize("bad", [[], {"nested": 1}, 7, None, True])
def test_bad_logical_type_fails_closed_before_lifecycle(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths], bad: Any
) -> None:
    """non-string logical type은 Docker lifecycle 이전 `ARCHIVE_INVALID`다.

    membership을 먼저 하면 raw `TypeError`가 `INTERNAL_ERROR`로 잘못 분류된다
    (구현리뷰 1차 필수 1).
    """

    archive, artifacts = fixture_artifacts
    manifest = json.loads(artifacts.source_manifest.read_text(encoding="utf-8"))
    entry = manifest["tables"]["metrology"]
    entry["column_types"][entry["columns"][0]] = bad
    artifacts.source_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RehearsalError) as raised:
        wrapper._run(
            ["--archive", str(archive), "--profile", "runtime"],
            artifact_paths=artifacts,
            lifecycle=lambda **_: pytest.fail("lifecycle must not start"),
        )
    assert raised.value.reason_code == "ARCHIVE_INVALID"
    assert raised.value.exit_code == wrapper.EXIT_USAGE


# ---------------------------------------------------------------------------
# V5-CM-2.5 — 공유 reason allowlist와 runner 호출 일반화
# ---------------------------------------------------------------------------

_V5_CM_24_REASONS = frozenset(
    {
        "ARG_INVALID",
        "ARCHIVE_INVALID",
        "ARCHIVE_MISMATCH",
        "DOCKER_UNAVAILABLE",
        "DOCKER_DAEMON_DOWN",
        "DOCKER_IMAGE_UNAVAILABLE",
        "DOCKER_PORT_UNAVAILABLE",
        "DOCKER_TIMEOUT",
        "REHEARSAL_NOT_READY",
        "REHEARSAL_CLEANUP_FAILED",
        "INTERRUPTED",
        "INTERNAL_ERROR",
        "MODE_CONFLICT",
        "TARGET_NOT_ALLOWED",
        "TARGET_ENV_INVALID",
        "PROFILE_MISMATCH",
        "MODE_NOT_WIRED",
        "EPOCH_MISMATCH",
        "ARTIFACT_INVALID",
        "CONFIRM_REQUIRED",
        "MODE_CONTRACT_ERROR",
        "LOCK_BUSY",
        "TARGET_NOT_FRESH",
        "SCHEMA_FORBIDDEN_STATEMENT",
    }
)
_V5_CM_25_REASONS = frozenset(
    {
        "RECOVERY_REQUIRED",
        "RECOVERY_NOT_ALLOWED",
        "ARTIFACT_MISMATCH",
        "ARTIFACT_WRITE_FAILED",
    }
)


_V5_CM_26_REASONS = frozenset(
    {
        "APPROVAL_REQUIRED",
        "APPROVAL_MISMATCH",
        "BACKUP_REQUIRED",
        "BACKUP_INVALID",
        "BACKUP_CLIENT_UNAVAILABLE",
        "BACKUP_CLIENT_VERSION_MISMATCH",
        "RESTORE_REQUIRED",
        "RESTORE_NOT_VERIFIED",
        "RAG_PRESERVATION_FAILED",
        "TARGET_STATE_UNSUPPORTED",
        "OTHER_TARGET_CHANGED",
        # 구현리뷰 4차 필수 2 — 수집이 한 snapshot에서 이뤄지지 않았다.
        "SNAPSHOT_NOT_ISOLATED",
        # 구현리뷰 7차 필수 1 — target mutex 경쟁·미보유.
        "TARGET_BUSY",
        "TARGET_MUTEX_MISSING",
        # 구현리뷰 8차 필수 2 — mutex 해제 실패로 pool에 lock이 남을 수 있다.
        "TARGET_MUTEX_LEAKED",
        # 구현리뷰 16차 필수 1 — 허용 공용 endpoint 고정.
        "ENDPOINT_NOT_ALLOWED",
        # 구현리뷰 16차 필수 3 — 사후 closure 증적 불일치.
        "CLOSURE_BLOCKED",
        # 구현리뷰 17차 필수 2 — backup root 신뢰 실패.
        "BACKUP_ROOT_UNTRUSTED",
    }
)


def test_reason_allowlist_is_exactly_previous_plus_new() -> None:
    """기존 28종이 하나도 빠지지 않고 2.6의 18종만 더해져 정확히 46종이다.

    `V5-CM-2.5`가 정한 대로 이 집합이 유일한 정본이다. 새 wrapper가 별도 allowlist를
    만들면 두 집합이 갈라진다(`V5-CM-2.6` 계획 §11).
    """

    assert len(_V5_CM_24_REASONS) == 24
    assert len(_V5_CM_25_REASONS) == 4
    assert len(_V5_CM_26_REASONS) == 18
    previous = _V5_CM_24_REASONS | _V5_CM_25_REASONS
    assert len(previous) == 28
    assert previous & _V5_CM_26_REASONS == frozenset()
    assert wrapper.REASON_ALLOWLIST == previous | _V5_CM_26_REASONS
    assert len(wrapper.REASON_ALLOWLIST) == 46
    assert previous <= wrapper.REASON_ALLOWLIST


@pytest.mark.parametrize("reason", sorted(_V5_CM_25_REASONS | _V5_CM_26_REASONS))
def test_new_reasons_survive_both_sanitizers(reason: str) -> None:
    """두 sanitizer 어느 쪽도 새 reason을 INTERNAL_ERROR로 바꾸지 않는다."""

    assert wrapper._checked(reason) == reason
    payload = json.dumps({"reason_code": reason, "status": "FAILED"}) + "\n"
    assert wrapper._parse_runner_output(payload, 1) == wrapper.RunnerOutcome(1, reason)


def test_unknown_reason_is_still_collapsed() -> None:
    assert wrapper._checked("SOMETHING_NEW") == "INTERNAL_ERROR"


def test_call_runner_defaults_to_rollback_rehearsal(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기존 CLI 기본 동작은 그대로 `--rehearse`다(계획 §3.3)."""

    _archive, artifacts = fixture_artifacts
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> int:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(wrapper.rebuild_runner, "run", fake_run)
    wrapper._call_runner(
        _endpoint(),
        profile="runtime",
        artifact_paths=artifacts,
        handler=lambda *_: None,
        postcheck=lambda *_: None,
    )
    assert "--rehearse" in seen["argv"]
    assert "--apply" not in seen["argv"]
    assert "--recover-artifact" not in seen["argv"]
    assert seen["kwargs"]["post_commits"] == {}
    assert set(seen["kwargs"]["mode_handlers"]) == {runner.RunMode.REHEARSE}


def test_call_runner_wires_apply_recover_and_hook(
    fixture_artifacts: tuple[Path, runner.ArtifactPaths],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _archive, artifacts = fixture_artifacts
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> int:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(wrapper.rebuild_runner, "run", fake_run)

    def hook(_c: Any, _p: Any) -> None:
        return None

    wrapper._call_runner(
        _endpoint(),
        profile="runtime",
        artifact_paths=artifacts,
        handler=lambda *_: None,
        postcheck=lambda *_: None,
        mode=runner.RunMode.APPLY,
        post_commit=hook,
        recover_artifact=True,
    )
    assert "--apply" in seen["argv"]
    assert "--recover-artifact" in seen["argv"]
    assert seen["kwargs"]["post_commits"] == {runner.RunMode.APPLY: hook}
    assert set(seen["kwargs"]["postchecks"]) == {runner.RunMode.APPLY}
