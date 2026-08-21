"""`V5-CM-2.5` recovery CLI 오케스트레이션 단위 테스트.

Docker·DB를 쓰지 않는다. lifecycle과 profile 시나리오는 stub으로 두고, CLI 자신이
소유한 계약(옵션·source SHA 불변·기대 결과 판정·오류 외부화)만 고정한다. 실제
PostgreSQL 전이는 `test_rehearsal_container.py`가 `container` marker로 검증한다.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rebuild_runner as runner  # noqa: E402
import rehearse_recovery as cli  # noqa: E402
import rehearse_schema as wrapper  # noqa: E402
from rehearsal_postgres import RehearsalEndpoint, RehearsalError  # noqa: E402


def _endpoint() -> RehearsalEndpoint:
    return RehearsalEndpoint(
        host="127.0.0.1",
        port=55432,
        database="fdc_rehearsal_runtime",
        username="rehearsal_user",
        password="do-not-print-this-password",
        container_id="container",
    )


@contextlib.contextmanager
def _fake_lifecycle(**_: Any) -> Iterator[RehearsalEndpoint]:
    yield _endpoint()


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """archive만 실제 파일이고 나머지 검증은 stub이다.

    lifecycle은 `_run(lifecycle=...)`로 주입한다. 모듈 속성을 바꿔도 기본 인자는
    정의 시점에 묶여 있어 실제 Docker가 뜬다.
    """

    archive = tmp_path / "fixture.zip"
    archive.write_bytes(b"archive-bytes")
    monkeypatch.setattr(cli.wrapper, "_verified_archive_snapshot", lambda *_: object())
    monkeypatch.setattr(cli.rebuild_runner, "validate_artifacts", lambda *_: None)
    return archive


def test_success_returns_zero_without_output(
    stubbed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_rehearse_profile", lambda *a, **k: None)
    assert (
        cli._run(
            ["--archive", str(stubbed), "--profile", "runtime"],
            lifecycle=_fake_lifecycle,
        )
        == cli.EXIT_OK
    )
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_source_mutation_during_run_is_detected(
    stubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실행 중 source가 바뀌면 `ARCHIVE_MISMATCH`다(계획 §8.1).

    snapshot 이후 archive가 교체되면 검증한 것과 다른 입력으로 판정한 셈이 된다.
    """

    def tamper(*_a: Any, **_k: Any) -> None:
        stubbed.write_bytes(stubbed.read_bytes() + b"tampered")

    monkeypatch.setattr(cli, "_rehearse_profile", tamper)
    with pytest.raises(RehearsalError) as raised:
        cli._run(
            ["--archive", str(stubbed), "--profile", "runtime"],
            lifecycle=_fake_lifecycle,
        )
    assert raised.value.reason_code == "ARCHIVE_MISMATCH"
    assert raised.value.exit_code == cli.EXIT_MISMATCH


def test_marker_root_is_temporary_and_removed(
    stubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repository에 marker를 남기지 않는다(계획 §6.2)."""

    seen: list[Path] = []

    def capture(*_a: Any, marker_root: Path, **_k: Any) -> None:
        seen.append(marker_root)
        (marker_root / "runtime.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "_rehearse_profile", capture)
    cli._run(
        ["--archive", str(stubbed), "--profile", "runtime"], lifecycle=_fake_lifecycle
    )
    assert len(seen) == 1
    assert not seen[0].exists()


def test_lifecycle_opens_profile_specific_database(
    stubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """profile마다 자기 DB의 lifecycle을 연다(계획 §7.3 · 계획리뷰 1차 필수 2)."""

    opened: list[str] = []

    @contextlib.contextmanager
    def lifecycle(*, database: str, **_: Any) -> Iterator[RehearsalEndpoint]:
        opened.append(database)
        yield _endpoint()

    monkeypatch.setattr(cli, "_rehearse_profile", lambda *a, **k: None)
    for profile in ("runtime", "evaluation"):
        cli._run(["--archive", str(stubbed), "--profile", profile], lifecycle=lifecycle)
    assert opened == ["fdc_rehearsal_runtime", "fdc_rehearsal_evaluation"]


def test_source_digests_cover_all_four_inputs(stubbed: Path) -> None:
    digests = cli._source_digests(stubbed, runner.DEFAULT_ARTIFACT_PATHS)
    assert len(digests) == 4
    assert len(set(digests)) == 4
    assert all(len(value) == 64 for value in digests)


def test_expect_rejects_unexpected_outcome() -> None:
    cli._expect(wrapper.RunnerOutcome(0, None), None, 0)
    for outcome in (
        wrapper.RunnerOutcome(1, "RECOVERY_REQUIRED"),
        wrapper.RunnerOutcome(0, "RECOVERY_REQUIRED"),
        wrapper.RunnerOutcome(1, None),
    ):
        with pytest.raises(RehearsalError) as raised:
            cli._expect(outcome, None, 0)
        assert raised.value.reason_code == "MODE_CONTRACT_ERROR"


def test_poison_raises_runner_error_not_rehearsal_error() -> None:
    """transaction 안에서 던지는 실패는 `RunnerError`여야 분류가 보존된다.

    `RehearsalError`를 던지면 `rebuild_runner.run()`의 generic handler가
    `INTERNAL_ERROR`로 숨긴다.
    """

    with pytest.raises(runner.RunnerError) as raised:
        cli._poison(object(), object())
    assert raised.value.reason_code == "MODE_CONTRACT_ERROR"
    assert raised.value.exit_code == cli.EXIT_MISMATCH
    assert not isinstance(raised.value, RehearsalError)


def test_main_maps_failures_to_allowlist_json(
    stubbed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    def boom(*_a: Any, **_k: Any) -> int:
        raise RehearsalError("RECOVERY_REQUIRED", cli.EXIT_MISMATCH)

    monkeypatch.setattr(cli, "_run", boom)
    exit_code = cli.main(["--archive", str(stubbed), "--profile", "runtime"])
    assert exit_code == cli.EXIT_MISMATCH
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload == {"reason_code": "RECOVERY_REQUIRED", "status": "FAILED"}


def test_main_hides_unexpected_errors(
    stubbed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    def boom(*_a: Any, **_k: Any) -> int:
        raise ValueError("절대 노출되면 안 되는 내부 메시지")

    monkeypatch.setattr(cli, "_run", boom)
    assert cli.main(["--archive", str(stubbed), "--profile", "runtime"]) == (
        cli.EXIT_USAGE
    )
    rendered = capsys.readouterr().err
    assert json.loads(rendered.strip())["reason_code"] == "INTERNAL_ERROR"
    assert "내부 메시지" not in rendered


def test_parser_has_no_target_or_marker_override() -> None:
    """public target·marker 경로를 CLI로 지정할 수 없다(계획 §4.2)."""

    options = {
        action.option_strings[0]
        for action in cli._parser()._actions
        if action.option_strings
    }
    assert options == {"-h", "--archive", "--profile"}
