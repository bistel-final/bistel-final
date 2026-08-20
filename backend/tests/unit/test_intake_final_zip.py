"""V5-CM-1.1 intake 검증기 단위 테스트.

실제 `project.zip`(8,202 member)은 저장소에 없으므로 합성 mini-ZIP fixture를 쓴다.
실제 CSV 해시를 가진 fixture는 만들 수 없어, 정상·변조 케이스는 모듈 상수
`EXPECTED_ARCHIVE_SHA256`·`PINNED_MEMBER_HASHES`를 monkeypatch로 주입해 수행한다
(작업계획 §2.1 seam). 실 ZIP 대상 검증은 구현보고의 수동 실행 증적으로 남긴다.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend" / "scripts"))

import intake_final_zip as intake  # noqa: E402

PREFIX = "project/repository/"

# 실제 15개 경로를 그대로 모사하되 내용만 합성한다.
FIXTURE_MEMBERS = {
    f"{PREFIX}mvp/gen_sample_data.py": b"# synthetic generator\n",
    f"{PREFIX}sample/schema/03_schema_clean.sql": b"-- synthetic ddl\n",
    f"{PREFIX}sample/ontology/master.cypher": b"// synthetic cypher\n",
    f"{PREFIX}sample/data/dim_parameter.csv": b"a,b\n1,2\n",
    f"{PREFIX}sample/data/lot_history.csv": b"a,b\n3,4\n",
    f"{PREFIX}sample/data/fdc_trace.csv": b"a,b\n5,6\n",
    f"{PREFIX}sample/data/summary_data.csv": b"a,b\n7,8\n",
    f"{PREFIX}sample/data/evaluation.csv": b"a,b\n9,10\n",
    f"{PREFIX}sample/data/trace_alarm_history.csv": b"a,b\n11,12\n",
    f"{PREFIX}sample/data/summary_alarm_history.csv": b"a,b\n13,14\n",
    f"{PREFIX}sample/data/metrology.csv": b"a,b\n15,16\n",
    f"{PREFIX}sample/data/action_history.csv": b"a,b\n17,18\n",
    f"{PREFIX}sample/rag/SPEC_PH-9000_PhotoScanner.md": b"# spec ph\n",
    f"{PREFIX}sample/rag/SPEC_ET-7500_DryEtcher.md": b"# spec et\n",
    f"{PREFIX}sample/rag/TROUBLE_FDC_FaultGuide.md": b"# trouble\n",
}

# 실제 패키지처럼 제외 대상도 섞어 둔다.
NOISE_MEMBERS = {
    f"{PREFIX}frontend/node_modules/react/index.js": b"noise\n",
    f"{PREFIX}frontend/node_modules/vite/bin.js": b"noise\n",
    f"{PREFIX}backend/app/main.py": b"noise\n",
    f"{PREFIX}docs/07_n8n.md": b"noise\n",
}

# 기준표 §8이 RAG 3종을 포함해 15개 전부를 고정한다(2026-08-20 확대).
PINNED_PATHS = tuple(FIXTURE_MEMBERS)


def _build_zip(
    path: Path, members: dict[str, bytes], *, directory_entries: bool = False
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if directory_entries:
            # 실 ZIP에는 디렉터리 엔트리가 691개 있고 그중 4개가 선별 prefix와 정확히
            # 일치한다. writestr만 쓰는 fixture는 이를 재현하지 못해 is_dir 필터 회귀를
            # 놓친다(구현리뷰 2차 권장 1).
            for name in {
                name.rsplit("/", 1)[0] + "/"
                for name in members
                if "/" in name
            }:
                archive.writestr(zipfile.ZipInfo(name), b"")
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def _fixture_pins(members: dict[str, bytes]) -> dict[str, str]:
    return {
        path: hashlib.sha256(members[path]).hexdigest()
        for path in PINNED_PATHS
        if path in members
    }


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return _build_zip(tmp_path / "project.zip", {**FIXTURE_MEMBERS, **NOISE_MEMBERS})


@pytest.fixture
def inject(monkeypatch: pytest.MonkeyPatch):
    """fixture ZIP과 그 member 해시를 모듈 상수에 주입한다."""

    def _inject(archive_path: Path, members: dict[str, bytes] | None = None) -> None:
        payload = members if members is not None else FIXTURE_MEMBERS
        monkeypatch.setattr(
            intake,
            "EXPECTED_ARCHIVE_SHA256",
            hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(intake, "PINNED_MEMBER_HASHES", _fixture_pins(payload))

    return _inject


def _run(archive_path: Path, out_path: Path, *flags: str) -> int:
    return intake.main(
        ["--archive", str(archive_path), "--out", str(out_path), *flags]
    )


# --- 1. 정상 -----------------------------------------------------------------


def test_normal_zip_creates_artifact(archive: Path, tmp_path: Path, inject) -> None:
    inject(archive)
    out = tmp_path / "final-zip-intake.json"

    assert _run(archive, out) == intake.EXIT_OK

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["selected_count"] == 15
    assert sum(1 for m in payload["selected_members"] if m["pinned"]) == 15
    assert payload["member_total"] == len(FIXTURE_MEMBERS) + len(NOISE_MEMBERS)
    assert payload["excluded_summary"]["node_modules"] == 2
    assert payload["excluded_summary"]["reference_app_and_docs"] == 2
    assert payload["declared_target_epoch"] == "fdc_final_20260818"
    # node_modules 경로가 등록물에 새어들지 않는다.
    assert not any(
        "node_modules" in m["path"] for m in payload["selected_members"]
    )


def test_directory_entries_are_excluded(tmp_path: Path, inject) -> None:
    """디렉터리 엔트리가 있어도 선별 15와 member_total이 흔들리지 않는다.

    실 ZIP에는 디렉터리 엔트리가 691개 있고 그중 4개(`sample/data/`·`ontology/`·
    `rag/`·`schema/`)가 선별 prefix와 정확히 일치한다. `is_dir()` 필터가 없으면
    선별이 19가 되고 `member_total`도 틀어지는데, `writestr`만 쓰는 fixture로는
    이 회귀를 잡지 못한다.
    """
    members = {**FIXTURE_MEMBERS, **NOISE_MEMBERS}
    archive_path = _build_zip(
        tmp_path / "with-dirs.zip", members, directory_entries=True
    )
    inject(archive_path)

    with zipfile.ZipFile(archive_path) as handle:
        assert any(info.is_dir() for info in handle.infolist())

    out = tmp_path / "final-zip-intake.json"
    assert _run(archive_path, out) == intake.EXIT_OK

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["selected_count"] == 15
    assert payload["member_total"] == len(members)
    assert not any(m["path"].endswith("/") for m in payload["selected_members"])


# --- 2. 검증 실패 -------------------------------------------------------------


def test_archive_hash_mismatch_reads_no_member(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원본 상수를 유지하면 fixture ZIP은 전체 해시에서 걸린다."""

    def _fail(*args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("전체 해시 불일치인데 member를 판독했다")

    monkeypatch.setattr(zipfile.ZipFile, "read", _fail)
    out = tmp_path / "final-zip-intake.json"

    assert _run(archive, out) == intake.EXIT_MISMATCH
    assert not out.exists()


def test_pinned_hash_tampered(
    archive: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    inject(archive)
    tampered = dict(intake.PINNED_MEMBER_HASHES)
    victim = f"{PREFIX}sample/data/metrology.csv"
    tampered[victim] = "0" * 64
    # 직접 대입하면 teardown 복원이 다른 fixture의 부수효과에 의존하게 된다.
    monkeypatch.setattr(intake, "PINNED_MEMBER_HASHES", tampered)

    assert _run(archive, tmp_path / "a.json") == intake.EXIT_MISMATCH


def test_pinned_path_renamed(tmp_path: Path, inject) -> None:
    """선별 15개는 유지한 채 pinned 경로 1건만 개명하면 '누락'으로 잡는다."""
    members = dict(FIXTURE_MEMBERS)
    victim = f"{PREFIX}sample/data/metrology.csv"
    members[f"{PREFIX}sample/data/metrology_v2.csv"] = members.pop(victim)
    archive_path = _build_zip(tmp_path / "renamed.zip", {**members, **NOISE_MEMBERS})
    inject(archive_path, FIXTURE_MEMBERS)

    assert _run(archive_path, tmp_path / "a.json") == intake.EXIT_MISMATCH


@pytest.mark.parametrize("mutation", ["drop", "add"])
def test_selected_count_changed(tmp_path: Path, inject, mutation: str) -> None:
    members = dict(FIXTURE_MEMBERS)
    if mutation == "drop":
        members.pop(f"{PREFIX}sample/rag/TROUBLE_FDC_FaultGuide.md")
    else:
        members[f"{PREFIX}sample/data/extra.csv"] = b"a,b\n99,99\n"
    archive_path = _build_zip(
        tmp_path / f"{mutation}.zip", {**members, **NOISE_MEMBERS}
    )
    inject(archive_path, members)

    assert _run(archive_path, tmp_path / "a.json") == intake.EXIT_MISMATCH


def test_duplicate_member_path(
    tmp_path: Path, inject, capsys: pytest.CaptureFixture[str]
) -> None:
    """중복 검사가 작동하는지 본다.

    선별 대상을 중복시키면 선별 수가 16이 되어 count 검사가 대신 EXIT_MISMATCH를 낸다.
    선별 밖 경로를 중복시켜 count 검사를 우회하고, 사유 메시지로 어느 방어가 작동했는지
    확정한다.
    """
    archive_path = tmp_path / "dup.zip"
    with zipfile.ZipFile(archive_path, "w") as handle:
        for name, payload in {**FIXTURE_MEMBERS, **NOISE_MEMBERS}.items():
            handle.writestr(name, payload)
        handle.writestr(f"{PREFIX}backend/app/main.py", b"noise\n")
    inject(archive_path)

    assert _run(archive_path, tmp_path / "a.json") == intake.EXIT_MISMATCH
    assert "중복" in capsys.readouterr().err


def test_corrupt_archive_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전체 해시는 통과하되 ZIP 구조가 깨진 경우에만 BadZipFile 경로에 닿는다."""
    target = tmp_path / "broken.zip"
    payload = b"not a zip at all"
    target.write_bytes(payload)
    monkeypatch.setattr(
        intake, "EXPECTED_ARCHIVE_SHA256", hashlib.sha256(payload).hexdigest()
    )

    assert _run(target, tmp_path / "a.json") == intake.EXIT_USAGE


def test_missing_archive_is_usage_error(tmp_path: Path) -> None:
    assert _run(tmp_path / "absent.zip", tmp_path / "a.json") == intake.EXIT_USAGE


# --- 3. artifact 쓰기 5-case --------------------------------------------------


def test_rerun_byte_identical_does_not_write(
    archive: Path, tmp_path: Path, inject
) -> None:
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK
    stamp = out.stat().st_mtime_ns
    raw = out.read_text(encoding="utf-8")

    assert _run(archive, out) == intake.EXIT_OK
    assert out.stat().st_mtime_ns == stamp
    assert out.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("verify_only", [False, True])
def test_object_equal_bytes_differ(
    archive: Path, tmp_path: Path, inject, verify_only: bool
) -> None:
    """쓰기 모드는 규약 형태로 재작성하고, 검증 모드는 재작성하지 않는다."""
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK
    canonical = out.read_text(encoding="utf-8")

    # 키 정렬 + 들여쓰기 4칸 + 말미 개행 제거 — 객체는 같고 바이트만 다르다.
    mangled = json.dumps(
        json.loads(canonical), ensure_ascii=False, indent=4, sort_keys=True
    )
    out.write_text(mangled, encoding="utf-8")
    stamp = out.stat().st_mtime_ns

    flags = ("--verify-only",) if verify_only else ()
    assert _run(archive, out, *flags) == intake.EXIT_OK

    if verify_only:
        # 계획 §2.1: 검증 모드는 어떤 경우에도 쓰지 않는다.
        assert out.read_text(encoding="utf-8") == mangled
        assert out.stat().st_mtime_ns == stamp
    else:
        assert out.read_text(encoding="utf-8") == canonical


def test_object_differs_requires_confirm(archive: Path, tmp_path: Path, inject) -> None:
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK
    stale = json.loads(out.read_text(encoding="utf-8"))
    stale["selected_count"] = 99
    out.write_text(intake.serialize(stale), encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    assert _run(archive, out) == intake.EXIT_CONFIRM_REQUIRED
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("flag", ["--confirm", "--force"])
def test_confirm_overwrites(
    archive: Path, tmp_path: Path, inject, flag: str
) -> None:
    """정식 이름은 `--confirm`이고 `--force`는 계획 표기와의 호환 별칭이다."""
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK
    canonical = out.read_text(encoding="utf-8")
    stale = json.loads(canonical)
    stale["selected_count"] = 99
    out.write_text(intake.serialize(stale), encoding="utf-8")

    assert _run(archive, out, flag) == intake.EXIT_OK
    assert out.read_text(encoding="utf-8") == canonical


# --- 4. --verify-only ---------------------------------------------------------


def test_verify_only_matching_and_absent(
    archive: Path, tmp_path: Path, inject
) -> None:
    inject(archive)
    out = tmp_path / "final-zip-intake.json"

    # 대조할 파일이 없으면 사용법 오류다.
    assert _run(archive, out, "--verify-only") == intake.EXIT_USAGE
    assert not out.exists()

    assert _run(archive, out) == intake.EXIT_OK
    stamp = out.stat().st_mtime_ns
    assert _run(archive, out, "--verify-only") == intake.EXIT_OK
    assert out.stat().st_mtime_ns == stamp


def test_verify_only_object_differs_never_writes(
    archive: Path, tmp_path: Path, inject
) -> None:
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK
    stale = json.loads(out.read_text(encoding="utf-8"))
    stale["selected_count"] = 99
    out.write_text(intake.serialize(stale), encoding="utf-8")
    before = out.read_text(encoding="utf-8")

    # 검증 모드의 상이는 확인 대상이 아니라 불일치다(계획 §3 결정 10).
    assert _run(archive, out, "--verify-only") == intake.EXIT_MISMATCH
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("flag", ["--confirm", "--force"])
def test_verify_only_with_confirm_is_usage_error(
    archive: Path, tmp_path: Path, inject, flag: str
) -> None:
    """모드 배타 검사 자체가 작동하는지 본다.

    `--out`이 없는 상태로 실행하면 "파일 없음 + verify_only" 분기가 같은 EXIT_USAGE를
    내므로 검사를 지워도 통과한다. 일치하는 artifact를 먼저 만들어 두면 검사가 없을 때
    exit 0이 되어 반드시 실패한다.
    """
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK

    assert _run(archive, out, "--verify-only", flag) == intake.EXIT_USAGE


# --- 4-1. 손상된 기존 artifact ------------------------------------------------


@pytest.mark.parametrize("content", ["[]", "42", '"x"', "null"])
@pytest.mark.parametrize("verify_only", [False, True])
def test_existing_artifact_not_object_is_reported(
    archive: Path,
    tmp_path: Path,
    inject,
    capsys: pytest.CaptureFixture[str],
    content: str,
    verify_only: bool,
) -> None:
    """유효한 JSON이지만 object가 아니면 traceback 없이 EXIT_MISMATCH로 끝난다.

    미처리 예외의 종료 코드는 1이라 EXIT_MISMATCH와 구별되지 않는다. V5-CM-1.2가 이
    스크립트를 게이트로 쓸 때 "손상된 등록물"을 "ZIP 불일치"로 오독하지 않게 한다.
    """
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    out.write_text(content, encoding="utf-8")

    flags = ("--verify-only",) if verify_only else ()
    assert _run(archive, out, *flags) == intake.EXIT_MISMATCH

    assert out.read_text(encoding="utf-8") == content
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.startswith("[intake] 실패:")
    assert captured.err.count("\n") == 1


def test_existing_artifact_broken_json_is_reported(
    archive: Path, tmp_path: Path, inject
) -> None:
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    out.write_text("{not json", encoding="utf-8")

    assert _run(archive, out) == intake.EXIT_MISMATCH


@pytest.mark.parametrize("flags", [(), ("--verify-only",), ("--confirm",)])
def test_existing_artifact_not_utf8_is_reported(
    archive: Path,
    tmp_path: Path,
    inject,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[str, ...],
) -> None:
    """비-UTF-8 바이트도 traceback 없이 EXIT_MISMATCH로 끝난다.

    `UnicodeDecodeError`는 `ValueError` 계열이라 `except OSError`에도
    `except JSONDecodeError`에도 걸리지 않는다. 한국어 환경에서 CP949로 저장된
    artifact가 현실적인 입력이며, 미처리 예외는 종료 코드가 1이라 EXIT_MISMATCH와
    구별되지 않아 V5-CM-1.2가 "손상된 등록물"을 "ZIP 불일치"로 오독한다.
    """
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    payload = '{"a": "가"}'.encode("cp949")
    out.write_bytes(payload)

    assert _run(archive, out, *flags) == intake.EXIT_MISMATCH

    assert out.read_bytes() == payload
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.startswith("[intake] 실패:")
    assert captured.err.count("\n") == 1


def test_out_path_is_directory_is_usage_error(
    archive: Path, tmp_path: Path, inject
) -> None:
    inject(archive)
    out = tmp_path / "isdir.json"
    out.mkdir()

    assert _run(archive, out) == intake.EXIT_USAGE


# --- 4-2. 계약 형태·판독 범위 -------------------------------------------------


def test_only_selected_members_are_read(
    archive: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 경로에서도 선별 15개만 판독한다.

    Task 완료 기준의 "참고 Backend·Frontend·node_modules는 제외한다"를 지키는 방어다.
    기존 해시 불일치 테스트는 '미판독'만 증명하고 정상 경로는 다루지 않았다.
    """
    read_names: list[str] = []
    original = zipfile.ZipFile.read

    def _spy(self: zipfile.ZipFile, name: object, pwd: object = None) -> bytes:
        read_names.append(name if isinstance(name, str) else name.filename)
        return original(self, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", _spy)
    inject(archive)

    assert _run(archive, tmp_path / "a.json") == intake.EXIT_OK
    assert sorted(read_names) == sorted(FIXTURE_MEMBERS)
    assert not any("node_modules" in name for name in read_names)


def test_serialized_form_matches_convention(
    archive: Path, tmp_path: Path, inject
) -> None:
    """artifact byte 형태를 고정 기대값으로 못박는다.

    기존 비교는 전부 "스크립트가 쓴 것끼리"라 자기참조여서, 말미 개행이나 member 정렬을
    없애도 잡히지 않았다.
    """
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK

    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("}\n")
    assert "\\u" not in raw
    assert raw.splitlines()[1].startswith('  "')

    payload = json.loads(raw)
    # 키는 삽입 순서(§2.2)이고 sort_keys가 아니다.
    assert list(payload)[:3] == [
        "format_version",
        "artifact_type",
        "declared_target_epoch",
    ]
    paths = [m["path"] for m in payload["selected_members"]]
    assert paths == sorted(paths)


def test_atomic_write_leaves_no_temp_file(
    archive: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """쓰기 중 실패해도 임시 파일이 남지 않고 기존 파일이 보존된다."""
    inject(archive)
    out = tmp_path / "final-zip-intake.json"
    assert _run(archive, out) == intake.EXIT_OK
    canonical = out.read_bytes()

    def _boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(intake.os, "replace", _boom)
    stale = json.loads(canonical.decode("utf-8"))
    stale["selected_count"] = 99
    out.write_text(intake.serialize(stale), encoding="utf-8")

    assert _run(archive, out, "--confirm") == intake.EXIT_USAGE
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


# --- 5. 기준표 §8 tripwire ----------------------------------------------------


def test_archive_hash_matches_reference_and_artifact() -> None:
    """ZIP 원본 해시 3자를 대조한다.

    기준표의 원본 SHA-256은 §8 표가 아니라 문서 머리에 있어 §8 tripwire 범위 밖이었고,
    `EXPECTED_ARCHIVE_SHA256`를 바꿔도 아무 테스트가 잡지 못했다. Task 완료 기준 1을
    직접 지키는 방어다.
    """
    doc = (
        REPOSITORY_ROOT / "docs" / "reference" / "mentor-final-20260818" / "README.md"
    ).read_text(encoding="utf-8")
    documented = re.search(r"원본 SHA-256: `([0-9a-f]{64})`", doc)
    assert documented is not None
    assert documented.group(1) == intake.EXPECTED_ARCHIVE_SHA256

    registered = json.loads(
        (REPOSITORY_ROOT / "infra" / "bootstrap" / "final-zip-intake.json").read_text(
            encoding="utf-8"
        )
    )
    assert registered["archive"]["sha256"] == intake.EXPECTED_ARCHIVE_SHA256


def test_reference_table_section8_tripwire() -> None:
    """기준표 §8이 바뀌면 실패시켜 상수 동기화를 사람이 의도적으로 하게 만든다."""
    doc = (
        REPOSITORY_ROOT / "docs" / "reference" / "mentor-final-20260818" / "README.md"
    ).read_text(encoding="utf-8")
    section = doc.split("## 8.")[1].split("## 9.")[0]

    pins = dict(re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", section))
    assert len(pins) == 15

    # (1) 상수와 기준표 표의 동기화 — 실패하면 반드시 상수를 고쳐야 한다.
    expected = {f"project/repository/{rel}": sha for rel, sha in pins.items()}
    assert expected == intake.PINNED_MEMBER_HASHES, (
        "기준표 §8 표와 PINNED_MEMBER_HASHES가 어긋났다. 상수를 동기화하라."
    )

    # (2) 섹션 본문 변경 감지 — (1)이 통과했다면 상수는 이미 무결하므로, 여기서만
    # 실패하는 경우는 표 외 서술이 바뀐 것이다. 해시만 갱신하면 된다.
    digest = hashlib.sha256(section.encode("utf-8")).hexdigest()
    assert digest == (
        "0400260405f16160427c7f70b01f59094e9c72cebd21d61e66eec9f9c439c3b4"
    ), "기준표 §8 본문(표 외)이 바뀌었다. 표·상수는 이미 일치하므로 이 해시만 갱신하라."
# --- 6. 비-UTF-8 stdout 환경 ----------------------------------------------------


def test_success_output_survives_ascii_stdout(
    archive: Path, tmp_path: Path, inject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """비-UTF-8 stdout에서도 정상 등록·검증이 EXIT_OK로 끝난다.

    완화가 없으면 등록을 마친 뒤 성공 출력에서 UnicodeEncodeError로 죽어 exit 1
    (=EXIT_MISMATCH)로 오독된다 — V5-CM-1.2 계획 §5가 경고한 경로이며
    issue_final_epoch.py와 같은 완화를 공유한다(V5-CM-1.2 구현리뷰 1차 필수 1).
    """
    monkeypatch.setattr(
        sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    )
    inject(archive)
    out = tmp_path / "final-zip-intake.json"

    assert _run(archive, out) == intake.EXIT_OK
    assert _run(archive, out, "--verify-only") == intake.EXIT_OK
