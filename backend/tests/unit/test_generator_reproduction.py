"""V5-CM-1.4 Generator 격리 실행·재현 계약 집중 테스트."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_source_manifest_v4 as builder  # noqa: E402


def _generator(files: dict[str, bytes]) -> bytes:
    entries = ",\n".join(
        f"    {relative!r}: {payload!r}" for relative, payload in sorted(files.items())
    )
    return (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "FILES = {\n"
        f"{entries}\n"
        "}\n"
        "for relative, payload in FILES.items():\n"
        "    target = ROOT / relative\n"
        "    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    target.write_bytes(payload)\n"
    ).encode()


def _fixture(
    *,
    mutate_csv: str | None = None,
    omit_csv: str | None = None,
    mutate_cypher: bool = False,
    extra_file: bool = False,
    extra_root_file: bool = False,
) -> tuple[dict[str, bytes], dict[str, dict[str, str]]]:
    payloads: dict[str, bytes] = {}
    generated: dict[str, bytes] = {}
    for table, member in builder.TABLE_MEMBERS.items():
        source = f"name,value\n{table},1\n".encode()
        payloads[member] = source
        if table != omit_csv:
            generated[f"sample/data/{Path(member).name}"] = (
                source + b"changed\n" if table == mutate_csv else source
            )
    payloads[builder.MASTER_CYPHER_MEMBER] = b"CREATE (n);\r\nCREATE (m);\r\n"
    generated["sample/ontology/master.cypher"] = (
        b"CREATE (changed);\n" if mutate_cypher else b"CREATE (n);\nCREATE (m);\n"
    )
    if extra_file:
        generated["sample/data/unexpected.csv"] = b"unexpected\n"
    if extra_root_file:
        generated["unexpected.txt"] = b"unexpected\n"
    generator = _generator(generated)
    payloads[builder.GENERATOR_MEMBER] = generator
    selected = {
        builder.GENERATOR_MEMBER: {"sha256": hashlib.sha256(generator).hexdigest()}
    }
    return payloads, selected


def test_real_generator_reproduces_nine_csv_and_normalized_cypher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    scratch = tmp_path / "fdc-generator-reproduction-success"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    result = builder.build_generator_reproduction(payloads, selected)

    assert result == {
        "contract_version": 1,
        "generator_sha256": selected[builder.GENERATOR_MEMBER]["sha256"],
        "csv_byte_identical": True,
        "csv_results": result["csv_results"],
        "newline_normalized": [
            {
                "file_id": builder.MASTER_CYPHER_MEMBER,
                "source_newline": "CRLF",
                "generated_newline": "LF",
                "normalized_sha256": hashlib.sha256(
                    b"CREATE (n);\nCREATE (m);\n"
                ).hexdigest(),
                "match": True,
            }
        ],
        "mismatched": [],
    }
    assert len(result["csv_results"]) == 9
    assert [entry["file_id"] for entry in result["csv_results"]] == sorted(
        builder.TABLE_MEMBERS.values()
    )
    assert all(entry["match"] is True for entry in result["csv_results"])
    assert all(
        entry["expected_sha256"] == entry["generated_sha256"]
        for entry in result["csv_results"]
    )
    assert not scratch.exists()


def test_csv_mismatch_fails_and_cleans_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture(mutate_csv="evaluation")
    scratch = tmp_path / "fdc-generator-reproduction-mismatch"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    with pytest.raises(builder.ManifestBuildError, match="evaluation.csv") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()


def test_unexpected_output_inventory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture(extra_file=True)
    scratch = tmp_path / "fdc-generator-reproduction-extra"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    with pytest.raises(builder.ManifestBuildError, match="unexpected.csv") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()


def test_missing_output_inventory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture(omit_csv="metrology")
    scratch = tmp_path / "fdc-generator-reproduction-missing"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    with pytest.raises(builder.ManifestBuildError, match="metrology.csv") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()


def test_cypher_content_mismatch_after_newline_normalization_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture(mutate_cypher=True)
    scratch = tmp_path / "fdc-generator-reproduction-cypher"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    with pytest.raises(builder.ManifestBuildError, match="master.cypher") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()


def test_output_outside_expected_directories_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture(extra_root_file=True)
    scratch = tmp_path / "fdc-generator-reproduction-root-extra"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    with pytest.raises(builder.ManifestBuildError, match="unexpected.txt") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()


def test_timeout_is_sanitized_and_cleans_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    scratch = tmp_path / "fdc-generator-reproduction-timeout"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="generator", timeout=60)

    monkeypatch.setattr(builder.subprocess, "run", _timeout)
    with pytest.raises(builder.ManifestBuildError, match="60초") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()


def test_subprocess_uses_isolated_python_and_minimal_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    scratch = tmp_path / "fdc-generator-reproduction-command"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))
    observed: dict = {}

    def _failed(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 7, b"", b"secret\nlast failure\n")

    monkeypatch.setattr(builder.subprocess, "run", _failed)
    with pytest.raises(builder.ManifestBuildError, match="last failure") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert observed["command"][:2] == [sys.executable, "-I"]
    assert set(observed["kwargs"]["env"]) == {"LANG", "LC_ALL", "PYTHONIOENCODING"}
    assert observed["kwargs"]["env"]["LANG"] == "C.UTF-8"
    assert observed["kwargs"]["env"]["LC_ALL"] == "C.UTF-8"
    assert observed["kwargs"]["timeout"] == 60
    assert observed["kwargs"]["capture_output"] is True
    assert not scratch.exists()


def test_generator_hash_must_match_intake_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads, selected = _fixture()
    selected[builder.GENERATOR_MEMBER]["sha256"] = "0" * 64
    called = False

    def _unexpected(**_kwargs):
        nonlocal called
        called = True
        return "unused"

    monkeypatch.setattr(builder.tempfile, "mkdtemp", _unexpected)
    with pytest.raises(builder.ManifestBuildError, match="payload 해시") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert called is False


def test_scratch_inside_repository_is_rejected_before_any_file_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    scratch = repository_root / "fdc-generator-reproduction-inside-repository"
    scratch.mkdir()
    subprocess_called = False

    def _unexpected_run(*_args, **_kwargs):
        nonlocal subprocess_called
        subprocess_called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(builder, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))
    monkeypatch.setattr(builder.subprocess, "run", _unexpected_run)

    with pytest.raises(builder.ManifestBuildError, match="저장소 내부") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_USAGE
    assert subprocess_called is False
    assert not scratch.exists()
    assert list(repository_root.iterdir()) == []


def test_existing_run_directory_is_rejected_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    scratch = tmp_path / "fdc-generator-reproduction-not-fresh"
    (scratch / "run").mkdir(parents=True)
    subprocess_called = False

    def _unexpected_run(*_args, **_kwargs):
        nonlocal subprocess_called
        subprocess_called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))
    monkeypatch.setattr(builder.subprocess, "run", _unexpected_run)

    with pytest.raises(builder.ManifestBuildError, match="fresh") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_USAGE
    assert subprocess_called is False
    assert not scratch.exists()


def test_symlink_output_is_rejected_even_when_bytes_and_name_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    scratch = tmp_path / "fdc-generator-reproduction-symlink"
    scratch.mkdir()
    linked_table = "evaluation"
    linked_member = builder.TABLE_MEMBERS[linked_table]
    external_target = tmp_path / "external-evaluation.csv"
    external_target.write_bytes(payloads[linked_member])

    def _write_outputs(command, **kwargs):
        run_root = Path(kwargs["cwd"])
        for table, member in builder.TABLE_MEMBERS.items():
            target = run_root / "sample" / "data" / Path(member).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if table == linked_table:
                target.symlink_to(external_target)
            else:
                target.write_bytes(payloads[member])
        cypher = run_root / "sample" / "ontology" / "master.cypher"
        cypher.parent.mkdir(parents=True, exist_ok=True)
        cypher.write_bytes(
            payloads[builder.MASTER_CYPHER_MEMBER].replace(b"\r\n", b"\n")
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))
    monkeypatch.setattr(builder.subprocess, "run", _write_outputs)

    with pytest.raises(builder.ManifestBuildError, match="비정상 entry") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert external_target.read_bytes() == payloads[linked_member]
    assert not scratch.exists()


def test_mixed_newlines_are_rejected_even_when_normalized_content_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads, selected = _fixture()
    payloads[builder.MASTER_CYPHER_MEMBER] = b"CREATE (n);\r\nCREATE (m);\n"
    scratch = tmp_path / "fdc-generator-reproduction-mixed-newline"
    scratch.mkdir()
    monkeypatch.setattr(builder.tempfile, "mkdtemp", lambda **_kwargs: str(scratch))

    with pytest.raises(builder.ManifestBuildError, match="source MIXED") as raised:
        builder.build_generator_reproduction(payloads, selected)

    assert raised.value.exit_code == builder.EXIT_MISMATCH
    assert not scratch.exists()
