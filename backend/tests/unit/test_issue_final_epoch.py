"""V5-CM-1.2 epoch 발급기 단위 테스트.

합성 intake fixture와 tmp `--out`만 쓴다 — 이 파일의 어떤 테스트도 실 저장소
`infra/bootstrap/`을 쓰지 않는다(작업계획 §4 묶음 1: 저장소 상태 불변).
합성 archive 해시는 모듈 상수 `EXPECTED_ARCHIVE_SHA256`을 monkeypatch로 주입한다
(작업계획 §2.1 seam). 발급물 자체(v2 계약·저장소 상태)는 `test_dataset_epoch.py`가
묶음 2에서 검증한다.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend" / "scripts"))

import issue_final_epoch as issue  # noqa: E402

SYNTHETIC_SHA256 = "ab" * 32


def _intake_payload() -> dict:
    """검증 4개 항목을 모두 통과하는 최소 intake object."""
    return {
        "format_version": 1,
        "artifact_type": "final_zip_intake",
        "declared_target_epoch": issue.TARGET_EPOCH,
        "received_date": "2026-08-18",
        "archive": {"filename": "project.zip", "sha256": SYNTHETIC_SHA256},
        "selected_count": 15,
    }


@pytest.fixture
def inject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(issue, "EXPECTED_ARCHIVE_SHA256", SYNTHETIC_SHA256)


@pytest.fixture
def intake_path(tmp_path: Path) -> Path:
    path = tmp_path / "final-zip-intake.json"
    path.write_text(
        json.dumps(_intake_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _run(intake: Path, out: Path, *flags: str) -> int:
    return issue.main(["--intake", str(intake), "--out", str(out), *flags])


def _write_intake(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --- 1. 정상 -----------------------------------------------------------------


def test_normal_issue_creates_v2_artifact(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    out = tmp_path / "dataset-epoch.json"

    assert _run(intake_path, out) == issue.EXIT_OK

    payload = json.loads(out.read_text(encoding="utf-8"))
    # §2.2의 8키, 삽입 순서 그대로.
    assert list(payload) == [
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "received_date",
        "archive",
        "inventory_scope",
        "intake_artifact",
        "supersedes",
    ]
    assert payload["format_version"] == 2
    assert payload["artifact_type"] == "dataset_epoch_registration"
    assert payload["dataset_epoch"] == "fdc_final_20260818"
    assert payload["received_date"] == "2026-08-18"
    # archive 해시는 상수 전사가 아니라 intake에서 읽은 값이다.
    assert payload["archive"] == {
        "filename": "project.zip",
        "sha256": SYNTHETIC_SHA256,
    }
    assert payload["inventory_scope"] == "selected_source_members"
    # CLI 인자와 무관하게 정본 경로를 참조한다. 해시는 넣지 않는다(결정 7).
    assert payload["intake_artifact"] == "infra/bootstrap/final-zip-intake.json"
    assert payload["supersedes"] == {
        "dataset_epoch": "kosa_0813",
        "isolated_to": "infra/bootstrap/history/kosa_0813/",
    }
    # v1의 전수 inventory 키가 새어들지 않는다(§2.2).
    assert "file_inventory" not in payload
    assert "file_count" not in payload


def test_real_repository_intake_passes_verification(tmp_path: Path) -> None:
    """실 intake artifact가 seam 주입 없이 발급 기준을 통과한다.

    `EXPECTED_ARCHIVE_SHA256` 상수·기준표·intake artifact의 3자 일치가 전제이며,
    셋 중 하나라도 바뀌면 여기서 잡힌다.
    """
    real_intake = REPOSITORY_ROOT / "infra" / "bootstrap" / "final-zip-intake.json"
    out = tmp_path / "dataset-epoch.json"

    assert _run(real_intake, out) == issue.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["archive"]["sha256"] == issue.EXPECTED_ARCHIVE_SHA256


def test_serialized_form_matches_convention(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    """artifact byte 형태를 규약(indent 2·비ASCII 원문·말미 개행)으로 못박는다."""
    out = tmp_path / "dataset-epoch.json"
    assert _run(intake_path, out) == issue.EXIT_OK

    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("}\n")
    assert "\\u" not in raw
    assert raw.splitlines()[1] == '  "format_version": 2,'


# --- 2. intake 검증 실패 -------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("artifact_type", "dataset_epoch_registration"),
        ("declared_target_epoch", "kosa_0813"),
        ("selected_count", 14),
        ("archive", {"filename": "project.zip", "sha256": "0" * 64}),
        ("archive", "not-a-dict"),
        ("archive", None),
    ],
)
def test_intake_field_mismatch(
    tmp_path: Path,
    inject,
    capsys: pytest.CaptureFixture[str],
    key: str,
    value: object,
) -> None:
    payload = _intake_payload()
    payload[key] = value
    intake = tmp_path / "intake.json"
    _write_intake(intake, payload)
    out = tmp_path / "dataset-epoch.json"

    assert _run(intake, out) == issue.EXIT_MISMATCH
    assert not out.exists()
    err = capsys.readouterr().err
    assert err.startswith("[epoch] 실패:")
    assert err.count("\n") == 1


@pytest.mark.parametrize(
    "key",
    ["artifact_type", "declared_target_epoch", "selected_count", "archive"],
)
def test_intake_field_missing(tmp_path: Path, inject, key: str) -> None:
    payload = _intake_payload()
    del payload[key]
    intake = tmp_path / "intake.json"
    _write_intake(intake, payload)

    assert _run(intake, tmp_path / "a.json") == issue.EXIT_MISMATCH


def test_mismatch_reports_all_problems_at_once(
    tmp_path: Path, inject, capsys: pytest.CaptureFixture[str]
) -> None:
    """항목별 축차 실패가 아니라 4개 대조를 전부 수행한 뒤 한 번에 보고한다."""
    payload = _intake_payload()
    payload["declared_target_epoch"] = "kosa_0813"
    payload["selected_count"] = 3
    intake = tmp_path / "intake.json"
    _write_intake(intake, payload)

    assert _run(intake, tmp_path / "a.json") == issue.EXIT_MISMATCH
    err = capsys.readouterr().err
    assert "declared_target_epoch" in err
    assert "selected_count" in err


def test_mismatch_reason_is_sanitized_single_line(
    tmp_path: Path, inject, capsys: pytest.CaptureFixture[str]
) -> None:
    """손상 값(개행·제어문자·과대 길이)이 사유에 원문 그대로 새지 않는다."""
    payload = _intake_payload()
    payload["declared_target_epoch"] = "evil\nepoch\x07" + "x" * 300
    intake = tmp_path / "intake.json"
    _write_intake(intake, payload)

    assert _run(intake, tmp_path / "a.json") == issue.EXIT_MISMATCH
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "\x07" not in err
    assert len(err) < 400


# --- 3. 손상 intake 입력 -------------------------------------------------------


def test_missing_intake_is_usage_error(tmp_path: Path, inject) -> None:
    assert (
        _run(tmp_path / "absent.json", tmp_path / "a.json") == issue.EXIT_USAGE
    )


def test_intake_path_is_directory_is_usage_error(tmp_path: Path, inject) -> None:
    intake = tmp_path / "isdir.json"
    intake.mkdir()

    assert _run(intake, tmp_path / "a.json") == issue.EXIT_USAGE


@pytest.mark.parametrize(
    "content", ["{not json", "[]", "42", '"x"', "null"]
)
def test_corrupt_intake_is_usage_error(
    tmp_path: Path,
    inject,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    """비-JSON·비-object intake는 대조에 도달하지 못하므로 EXIT_USAGE다.

    traceback 없이 sanitized 한 줄로 끝난다.
    """
    intake = tmp_path / "intake.json"
    intake.write_text(content, encoding="utf-8")

    assert _run(intake, tmp_path / "a.json") == issue.EXIT_USAGE
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.startswith("[epoch] 실패:")
    assert captured.err.count("\n") == 1


def test_non_utf8_intake_is_usage_error(
    tmp_path: Path, inject, capsys: pytest.CaptureFixture[str]
) -> None:
    """CM-1.1 최종검증이 마지막에 잡았던 비-UTF-8 케이스를 처음부터 포함한다([승계])."""
    intake = tmp_path / "intake.json"
    intake.write_bytes('{"a": "가"}'.encode("cp949"))

    assert _run(intake, tmp_path / "a.json") == issue.EXIT_USAGE
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


# --- 4. artifact 쓰기 5-case ---------------------------------------------------


def test_rerun_byte_identical_does_not_write(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    out = tmp_path / "dataset-epoch.json"
    assert _run(intake_path, out) == issue.EXIT_OK
    stamp = out.stat().st_mtime_ns
    raw = out.read_text(encoding="utf-8")

    assert _run(intake_path, out) == issue.EXIT_OK
    assert out.stat().st_mtime_ns == stamp
    assert out.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("verify_only", [False, True])
def test_object_equal_bytes_differ(
    intake_path: Path, tmp_path: Path, inject, verify_only: bool
) -> None:
    """쓰기 모드는 규약 형태로 재작성하고, 검증 모드는 어떤 경우에도 쓰지 않는다."""
    out = tmp_path / "dataset-epoch.json"
    assert _run(intake_path, out) == issue.EXIT_OK
    canonical = out.read_text(encoding="utf-8")

    mangled = json.dumps(
        json.loads(canonical), ensure_ascii=False, indent=4, sort_keys=True
    )
    out.write_text(mangled, encoding="utf-8")
    stamp = out.stat().st_mtime_ns

    flags = ("--verify-only",) if verify_only else ()
    assert _run(intake_path, out, *flags) == issue.EXIT_OK

    if verify_only:
        assert out.read_text(encoding="utf-8") == mangled
        assert out.stat().st_mtime_ns == stamp
    else:
        assert out.read_text(encoding="utf-8") == canonical


def test_object_differs_requires_confirm(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    """구 v1 epoch가 있는 실전 형태의 경로다 — 승인 없이 덮어쓰지 않는다."""
    out = tmp_path / "dataset-epoch.json"
    stale = {"format_version": 1, "dataset_epoch": "kosa_0813"}
    out.write_text(issue.serialize(stale), encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    assert _run(intake_path, out) == issue.EXIT_CONFIRM_REQUIRED
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("flag", ["--confirm", "--force"])
def test_confirm_overwrites(
    intake_path: Path, tmp_path: Path, inject, flag: str
) -> None:
    out = tmp_path / "dataset-epoch.json"
    stale = {"format_version": 1, "dataset_epoch": "kosa_0813"}
    out.write_text(issue.serialize(stale), encoding="utf-8")

    assert _run(intake_path, out, flag) == issue.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["format_version"] == 2
    assert payload["dataset_epoch"] == "fdc_final_20260818"


# --- 5. --verify-only ----------------------------------------------------------


def test_verify_only_matching_and_absent(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    out = tmp_path / "dataset-epoch.json"

    # 대조할 파일이 없으면 사용법 오류다.
    assert _run(intake_path, out, "--verify-only") == issue.EXIT_USAGE
    assert not out.exists()

    assert _run(intake_path, out) == issue.EXIT_OK
    stamp = out.stat().st_mtime_ns
    assert _run(intake_path, out, "--verify-only") == issue.EXIT_OK
    assert out.stat().st_mtime_ns == stamp


def test_verify_only_object_differs_never_writes(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    out = tmp_path / "dataset-epoch.json"
    stale = {"format_version": 1, "dataset_epoch": "kosa_0813"}
    out.write_text(issue.serialize(stale), encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    assert _run(intake_path, out, "--verify-only") == issue.EXIT_MISMATCH
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("flag", ["--confirm", "--force"])
def test_verify_only_with_confirm_is_usage_error(
    intake_path: Path, tmp_path: Path, inject, flag: str
) -> None:
    """일치하는 artifact를 먼저 만들어 두어야 배타 검사 부재 시 exit 0으로 실패한다."""
    out = tmp_path / "dataset-epoch.json"
    assert _run(intake_path, out) == issue.EXIT_OK

    assert _run(intake_path, out, "--verify-only", flag) == issue.EXIT_USAGE


# --- 6. 손상된 기존 epoch artifact ---------------------------------------------


@pytest.mark.parametrize("content", ["{not json", "[]", "42", '"x"', "null"])
@pytest.mark.parametrize("verify_only", [False, True])
def test_existing_artifact_corrupt_is_mismatch(
    intake_path: Path,
    tmp_path: Path,
    inject,
    capsys: pytest.CaptureFixture[str],
    content: str,
    verify_only: bool,
) -> None:
    """쓰기 대상의 손상은 [승계] 규약대로 EXIT_MISMATCH이고 원본을 보존한다."""
    out = tmp_path / "dataset-epoch.json"
    out.write_text(content, encoding="utf-8")

    flags = ("--verify-only",) if verify_only else ()
    assert _run(intake_path, out, *flags) == issue.EXIT_MISMATCH

    assert out.read_text(encoding="utf-8") == content
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.startswith("[epoch] 실패:")
    assert captured.err.count("\n") == 1


@pytest.mark.parametrize("flags", [(), ("--verify-only",), ("--confirm",)])
def test_existing_artifact_not_utf8_is_mismatch(
    intake_path: Path,
    tmp_path: Path,
    inject,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[str, ...],
) -> None:
    out = tmp_path / "dataset-epoch.json"
    payload = '{"a": "가"}'.encode("cp949")
    out.write_bytes(payload)

    assert _run(intake_path, out, *flags) == issue.EXIT_MISMATCH

    assert out.read_bytes() == payload
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


def test_out_path_is_directory_is_usage_error(
    intake_path: Path, tmp_path: Path, inject
) -> None:
    out = tmp_path / "isdir.json"
    out.mkdir()

    assert _run(intake_path, out) == issue.EXIT_USAGE


# --- 7. atomic write -----------------------------------------------------------


def test_atomic_write_leaves_no_temp_file(
    intake_path: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """쓰기 중 실패해도 임시 파일이 남지 않고 기존 파일이 보존된다."""
    out = tmp_path / "dataset-epoch.json"
    stale = {"format_version": 1, "dataset_epoch": "kosa_0813"}
    out.write_text(issue.serialize(stale), encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    def _boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(issue.os, "replace", _boom)

    assert _run(intake_path, out, "--confirm") == issue.EXIT_USAGE
    assert out.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []
# --- 8. 비-UTF-8 stdout 환경 -----------------------------------------------------


def _ascii_stdout(monkeypatch: pytest.MonkeyPatch) -> io.TextIOWrapper:
    """strict-ascii stdout을 흉내 낸다. PYTHONIOENCODING=ascii 서브프로세스와 등가다."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", stream)
    return stream


def test_success_output_survives_ascii_stdout(
    intake_path: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """비-UTF-8 stdout에서도 정상 발급이 traceback 없이 EXIT_OK로 끝난다.

    완화가 없으면 파일을 이미 교체한 뒤 성공 출력에서 UnicodeEncodeError로 죽어
    "부작용 완료 + exit 1(=EXIT_MISMATCH) 오독"이 된다(구현리뷰 1차 필수 1).
    """
    _ascii_stdout(monkeypatch)
    out = tmp_path / "dataset-epoch.json"

    assert _run(intake_path, out) == issue.EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8"))["format_version"] == 2


def test_verify_only_output_survives_ascii_stdout(
    intake_path: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "dataset-epoch.json"
    assert _run(intake_path, out) == issue.EXIT_OK

    _ascii_stdout(monkeypatch)
    assert _run(intake_path, out, "--verify-only") == issue.EXIT_OK
