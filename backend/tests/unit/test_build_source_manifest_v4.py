"""V5-CM-1.3 source manifest v4 생성기 단위 테스트.

실제 `project.zip`은 저장소에 없으므로 **합성 mini-ZIP + 합성 epoch/intake fixture**를
쓴다(작업계획 §2.3 [승계] seam 방식). 컬럼·타입 계약은 실제 모듈 상수
(`EXPECTED_COLUMNS`·`EXPECTED_COLUMN_TYPES`)를 그대로 쓰고, 행 수만
`EXPECTED_ROW_COUNTS`를 fixture 값으로 monkeypatch한다.

실 ZIP 대상 4자 대조·기준표 tripwire는 `test_source_manifest_v4.py`(묶음 2)가 맡는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_source_manifest_v4 as builder  # noqa: E402
import manifest_v3 as mv3  # noqa: E402
import value_normalization as vn  # noqa: E402

FIXTURE_ROWS = 2

# logical type → 합성 DDL 타입. logical_type()이 역방향으로 같은 값을 내는 대표를 쓴다.
DDL_TYPE = {
    "text": "varchar(20)",
    "numeric": "numeric(12,4)",
    "boolean": "boolean",
    "timestamp": "timestamp",
}

NOISE_MEMBERS = {
    "project/repository/frontend/node_modules/react/index.js": b"noise\n",
    "project/repository/backend/app/main.py": b"noise\n",
}


def _cell(table: str, column: str, row_index: int) -> str:
    kind = builder.EXPECTED_COLUMN_TYPES[table][column]
    if kind == "numeric":
        return str(row_index + 1)
    if kind == "boolean":
        return "true" if row_index % 2 == 0 else "false"
    if kind == "timestamp":
        return f"2026-08-18 00:00:{row_index:02d}"
    return f"{column}_{row_index}"


def _synth_csv(table: str, rows: list[dict[str, str]] | None = None) -> bytes:
    columns = builder.EXPECTED_COLUMNS[table]
    if rows is None:
        rows = [
            {column: _cell(table, column, index) for column in columns}
            for index in range(FIXTURE_ROWS)
        ]
    lines = [",".join(columns)]
    lines += [",".join(row[column] for column in columns) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _synth_ddl() -> bytes:
    blocks = []
    for table in sorted(builder.EXPECTED_COLUMN_TYPES):
        primary_key = builder.EXPECTED_PRIMARY_KEYS[table]
        lines = []
        for column, kind in builder.EXPECTED_COLUMN_TYPES[table].items():
            suffix = (
                " PRIMARY KEY"
                if len(primary_key) == 1 and column == primary_key[0]
                else ""
            )
            lines.append(f"    {column} {DDL_TYPE[kind]}{suffix}")
        if len(primary_key) > 1:
            lines.append(f"    PRIMARY KEY ({', '.join(primary_key)})")
        blocks.append(f"CREATE TABLE {table} (\n" + ",\n".join(lines) + "\n);")
    return ("-- synthetic ddl\n" + "\n\n".join(blocks) + "\n").encode("utf-8")


def _default_members(
    csv_overrides: dict[str, bytes] | None = None,
    ddl: bytes | None = None,
) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for table, member in builder.TABLE_MEMBERS.items():
        members[member] = (csv_overrides or {}).get(table) or _synth_csv(table)
    members[builder.SCHEMA_SQL_MEMBER] = ddl if ddl is not None else _synth_ddl()
    members[builder.MASTER_CYPHER_MEMBER] = b"// synthetic cypher\n"
    members[builder.GENERATOR_MEMBER] = b"# synthetic generator\n"
    for member in builder.RAG_MEMBERS:
        members[member] = f"# rag {member}\n".encode()
    return members


def _synth_reproduction(payloads: dict[str, bytes], selected: dict[str, dict]) -> dict:
    """대부분의 builder 테스트가 subprocess 비용 없이 provenance wiring을 검증한다.

    실제 격리 실행·inventory·timeout 계약은 test_generator_reproduction.py가 맡는다.
    """
    csv_results = []
    for table in sorted(builder.TABLE_MEMBERS):
        member = builder.TABLE_MEMBERS[table]
        digest = hashlib.sha256(payloads[member]).hexdigest()
        csv_results.append(
            {
                "file_id": member,
                "expected_sha256": digest,
                "generated_sha256": digest,
                "match": True,
            }
        )
    cypher = payloads[builder.MASTER_CYPHER_MEMBER].replace(b"\r\n", b"\n")
    return {
        "contract_version": 1,
        "generator_sha256": selected[builder.GENERATOR_MEMBER]["sha256"],
        "csv_byte_identical": True,
        "csv_results": csv_results,
        "newline_normalized": [
            {
                "file_id": builder.MASTER_CYPHER_MEMBER,
                "source_newline": "LF",
                "generated_newline": "LF",
                "normalized_sha256": hashlib.sha256(cypher).hexdigest(),
                "match": True,
            }
        ],
        "mismatched": [],
    }


@dataclass
class Env:
    zip_path: Path
    origin_path: Path
    out_path: Path


ORIGIN_FIXTURE_FILES = {
    "document_schema_sql": ("03_db/01_schema.sql", b"-- synthetic document ddl\n"),
    "rag_loader": ("05_scripts/load_documents.py", b"# synthetic loader\n"),
    "embedding_requirements": ("04_infra/requirements.txt", b"sentence-transformers\n"),
}


def _build_origin(root: Path, files=ORIGIN_FIXTURE_FILES) -> dict[str, dict[str, str]]:
    """합성 ① 배포패키지를 만들고 그에 맞는 ORIGIN_ARTIFACTS 상수 값을 돌려준다."""
    spec: dict[str, dict[str, str]] = {}
    for key, (file_id, payload) in files.items():
        path = root / file_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        spec[key] = {
            "file_id": file_id,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "role": f"synthetic role for {key}",
        }
    return spec


@pytest.fixture
def make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """합성 ZIP + epoch/intake fixture를 만들고 모듈 seam을 주입한다."""

    def _make(
        members: dict[str, bytes] | None = None,
        *,
        name: str = "project.zip",
        intake_members: dict[str, bytes] | None = None,
        epoch_name: str | None = None,
        row_counts: dict[str, int] | None = None,
        noise: bool = True,
    ) -> Env:
        members = members if members is not None else _default_members()
        zip_path = tmp_path / name
        with zipfile.ZipFile(zip_path, "w") as archive:
            for member, payload in members.items():
                archive.writestr(member, payload)
            if noise:
                for member, payload in NOISE_MEMBERS.items():
                    archive.writestr(member, payload)
        archive_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()

        # intake가 주장하는 member 해시는 기본적으로 ZIP 실물과 같다.
        claimed = intake_members if intake_members is not None else members
        selected = [
            {
                "path": member,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                # 합성 intake는 RAG를 비고정으로 주장한다 — builder가 주장을
                # 하드코딩 없이 복사(승계)하는지 검증하기 위한 값이다. 실 세계
                # (§8 확대 후 pinned 15/15)는 test_source_manifest_v4.py가 고정한다.
                "pinned": member not in builder.RAG_MEMBERS,
            }
            for member, payload in sorted(claimed.items())
        ]
        intake_path = tmp_path / f"{name}.intake.json"
        intake_path.write_text(
            json.dumps(
                {
                    "artifact_type": "final_zip_intake",
                    "declared_target_epoch": builder.TARGET_EPOCH,
                    "archive": {"filename": name, "sha256": archive_sha},
                    "selected_count": len(selected),
                    "selected_members": selected,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        epoch_path = tmp_path / f"{name}.epoch.json"
        epoch_path.write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "artifact_type": "dataset_epoch_registration",
                    "dataset_epoch": epoch_name or builder.TARGET_EPOCH,
                    "archive": {"filename": name, "sha256": archive_sha},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        origin_root = tmp_path / f"{name}.origin"
        monkeypatch.setattr(builder, "ORIGIN_ARTIFACTS", _build_origin(origin_root))
        monkeypatch.setattr(builder, "DATASET_EPOCH_PATH", epoch_path)
        monkeypatch.setattr(builder, "INTAKE_ARTIFACT_PATH", intake_path)
        monkeypatch.setattr(
            builder,
            "EXPECTED_ROW_COUNTS",
            row_counts
            or {table: FIXTURE_ROWS for table in builder.EXPECTED_ROW_COUNTS},
        )
        monkeypatch.setattr(
            builder, "build_generator_reproduction", _synth_reproduction
        )
        return Env(
            zip_path=zip_path,
            origin_path=origin_root,
            out_path=tmp_path / f"{name}.manifest.json",
        )

    return _make


def _create(env: Env) -> int:
    """최초 생성 경로. 시스템설계 §2.3에 따라 --confirm이 필요하다."""
    return _run(env, "--confirm")


def _run(env: Env, *flags: str) -> int:
    return builder.main(
        [
            "--archive",
            str(env.zip_path),
            "--origin-package",
            str(env.origin_path),
            "--out",
            str(env.out_path),
            *flags,
        ]
    )


def _manifest(env: Env) -> dict:
    return json.loads(env.out_path.read_text(encoding="utf-8"))


# --- 1. 정상 -----------------------------------------------------------------


def test_normal_zip_builds_v4_manifest(make_env) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK

    manifest = _manifest(env)
    assert list(manifest) == [
        "format_version",
        "artifact_type",
        "dataset_epoch",
        "source_archive_sha256",
        "selected_entry_manifest_sha256",
        "schema_sha256",
        "generator_sha256",
        "canonicalization_version",
        "hash_algorithm",
        "value_normalization_version",
        "derived_from",
        "tables",
        "artifacts",
        "generator_reproduction",
        "origin_package",
    ]
    assert manifest["format_version"] == 4
    assert manifest["artifact_type"] == "source_files"
    assert manifest["dataset_epoch"] == "fdc_final_20260818"
    assert manifest["hash_algorithm"] == mv3.HASH_ALGORITHM
    # canonicalization_version은 hash_algorithm에서 기계 파생 — 어긋날 수 없다.
    assert manifest["canonicalization_version"] == "canonical-json-nfc-codepoint-v1"
    assert (
        "sha256-" + manifest["canonicalization_version"] == manifest["hash_algorithm"]
    )
    # intake artifact 바이트의 SHA-256 — provenance 결합(시스템설계 §2.3).
    assert (
        manifest["selected_entry_manifest_sha256"]
        == hashlib.sha256(builder.INTAKE_ARTIFACT_PATH.read_bytes()).hexdigest()
    )
    # §2.3이 정한 이름의 최상위 사본 — artifacts 값과 항상 같다.
    assert manifest["schema_sha256"] == manifest["artifacts"]["schema_sql"]["sha256"]
    assert manifest["generator_sha256"] == manifest["artifacts"]["generator"]["sha256"]
    assert len(manifest["tables"]) == 9
    for table, entry in manifest["tables"].items():
        assert entry["file_id"] == builder.TABLE_MEMBERS[table]
        assert tuple(entry["columns"]) == builder.EXPECTED_COLUMNS[table]
        assert entry["column_types"] == {
            column: builder.EXPECTED_COLUMN_TYPES[table][column]
            for column in entry["columns"]
        }
        assert entry["primary_key"] == list(builder.EXPECTED_PRIMARY_KEYS[table])
        assert entry["row_count"] == FIXTURE_ROWS
        assert len(entry["content_hash"]) == 64
        # 시스템설계 §2.4 — action_history는 evaluation에만 적재된다.
        expected_profiles = (
            ["evaluation"]
            if table == "action_history"
            else ["runtime", "runtime-e2e", "evaluation"]
        )
        assert entry["included_by_profile"] == expected_profiles
    # artifacts 4키/6파일.
    artifacts = manifest["artifacts"]
    assert set(artifacts) == {
        "schema_sql",
        "master_cypher",
        "generator",
        "rag_documents",
    }
    assert len(artifacts["rag_documents"]) == 3
    # 합성 intake의 주장(False)이 그대로 복사됐는지 본다 — 위 fixture 주석 참조.
    assert all(entry["pinned"] is False for entry in artifacts["rag_documents"])
    reproduction = manifest["generator_reproduction"]
    assert reproduction["contract_version"] == 1
    assert reproduction["generator_sha256"] == manifest["generator_sha256"]
    assert reproduction["csv_byte_identical"] is True
    assert len(reproduction["csv_results"]) == 9
    assert reproduction["mismatched"] == []
    assert manifest["derived_from"] == {
        "dataset_epoch_artifact": "infra/bootstrap/dataset-epoch.json",
        "intake_artifact": "infra/bootstrap/final-zip-intake.json",
    }
    origin = manifest["origin_package"]
    assert origin["package"] == "교육생_배포패키지"
    assert origin["selection_rule"] == "final-package-first"
    assert origin["reference"] == "docs/reference/배포패키지_기준.md"
    assert origin["artifacts"] == builder.ORIGIN_ARTIFACTS


def test_main_wires_generator_reproduction_into_manifest(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env()
    called = 0

    def _spy(payloads, selected):
        nonlocal called
        called += 1
        return _synth_reproduction(payloads, selected)

    monkeypatch.setattr(builder, "build_generator_reproduction", _spy)
    assert _create(env) == builder.EXIT_OK
    assert called == 1
    assert _manifest(env)["generator_reproduction"]["contract_version"] == 1
    assert _run(env, "--verify-only") == builder.EXIT_OK
    assert called == 2


def test_generator_reproduction_failure_never_changes_manifest(
    make_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    before = env.out_path.read_bytes()

    def _fail(_payloads, _selected):
        raise builder.ManifestBuildError(
            "Generator 재현 결과가 원본과 다릅니다 — evaluation.csv",
            builder.EXIT_MISMATCH,
        )

    monkeypatch.setattr(builder, "build_generator_reproduction", _fail)
    assert _run(env, "--confirm") == builder.EXIT_MISMATCH
    assert env.out_path.read_bytes() == before
    assert "evaluation.csv" in capsys.readouterr().err


def test_value_normalization_version_is_recorded(make_env) -> None:
    """상수를 바꾸면 실패한다 — 정규화 규약이 manifest에 남는다는 계약."""
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    assert (
        _manifest(env)["value_normalization_version"] == vn.VALUE_NORMALIZATION_VERSION
    )


def test_serialized_form_matches_convention(make_env) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    raw = env.out_path.read_text(encoding="utf-8")
    assert raw.endswith("}\n")
    assert "\\u" not in raw
    assert raw.splitlines()[1] == '  "format_version": 4,'


# --- 2. typed canonical hash -----------------------------------------------------


def test_row_order_does_not_change_content_hash(make_env) -> None:
    """canonical hash를 쓰는 유일한 이유. 깨지면 CM-2.4 적재 검증이 무의미해진다."""
    columns = builder.EXPECTED_COLUMNS["dim_parameter"]
    rows = [
        {column: _cell("dim_parameter", column, index) for column in columns}
        for index in range(FIXTURE_ROWS)
    ]
    env_a = make_env(
        _default_members({"dim_parameter": _synth_csv("dim_parameter", rows)}),
        name="a.zip",
    )
    assert _create(env_a) == builder.EXIT_OK
    env_b = make_env(
        _default_members({"dim_parameter": _synth_csv("dim_parameter", rows[::-1])}),
        name="b.zip",
    )
    assert _create(env_b) == builder.EXIT_OK

    assert (
        _manifest(env_a)["tables"]["dim_parameter"]["content_hash"]
        == _manifest(env_b)["tables"]["dim_parameter"]["content_hash"]
    )


def test_typed_normalization_unifies_value_representation(make_env) -> None:
    """`45.0`과 `45`가 같은 content_hash를 낸다 — WBS "typed"의 실체."""
    columns = builder.EXPECTED_COLUMNS["dim_parameter"]

    def _rows(numeric_value: str) -> list[dict[str, str]]:
        return [
            {
                column: (
                    numeric_value
                    if builder.EXPECTED_COLUMN_TYPES["dim_parameter"][column]
                    == "numeric"
                    else _cell("dim_parameter", column, 0)
                )
                for column in columns
            }
        ]

    one_row = {
        table: (1 if table == "dim_parameter" else FIXTURE_ROWS)
        for table in builder.EXPECTED_ROW_COUNTS
    }
    env_a = make_env(
        _default_members({"dim_parameter": _synth_csv("dim_parameter", _rows("45.0"))}),
        name="a.zip",
        row_counts=one_row,
    )
    assert _create(env_a) == builder.EXIT_OK
    env_b = make_env(
        _default_members({"dim_parameter": _synth_csv("dim_parameter", _rows("45"))}),
        name="b.zip",
        row_counts=one_row,
    )
    assert _create(env_b) == builder.EXIT_OK

    assert (
        _manifest(env_a)["tables"]["dim_parameter"]["content_hash"]
        == _manifest(env_b)["tables"]["dim_parameter"]["content_hash"]
    )


def test_nfc_normalization_unifies_cell_representation(make_env) -> None:
    """조합형(NFD)·완성형(NFC) 셀이 같은 content_hash로 정규화된다."""
    columns = builder.EXPECTED_COLUMNS["dim_parameter"]
    nfc = "가나다"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd

    def _rows(text_value: str) -> list[dict[str, str]]:
        return [
            {
                column: (
                    text_value
                    if builder.EXPECTED_COLUMN_TYPES["dim_parameter"][column] == "text"
                    else _cell("dim_parameter", column, 0)
                )
                for column in columns
            }
        ]

    one_row = {
        table: (1 if table == "dim_parameter" else FIXTURE_ROWS)
        for table in builder.EXPECTED_ROW_COUNTS
    }
    env_a = make_env(
        _default_members({"dim_parameter": _synth_csv("dim_parameter", _rows(nfc))}),
        name="a.zip",
        row_counts=one_row,
    )
    assert _create(env_a) == builder.EXIT_OK
    env_b = make_env(
        _default_members({"dim_parameter": _synth_csv("dim_parameter", _rows(nfd))}),
        name="b.zip",
        row_counts=one_row,
    )
    assert _create(env_b) == builder.EXIT_OK

    assert (
        _manifest(env_a)["tables"]["dim_parameter"]["content_hash"]
        == _manifest(env_b)["tables"]["dim_parameter"]["content_hash"]
    )


def test_normalization_feeds_hashing_for_all_tables(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9개 테이블 전부 normalize_csv_row를 거치고 그 반환값이 해시 입력이 된다."""
    normalized_ids: set[int] = set()
    original_norm = builder.normalize_csv_row

    def _spy_norm(row, column_types):
        result = original_norm(row, column_types)
        normalized_ids.add(id(result))
        return result

    hash_inputs: list[list] = []
    original_hash = builder.hash_canonical_rows

    def _spy_hash(rows):
        rows = list(rows)
        hash_inputs.append(rows)
        return original_hash(rows)

    monkeypatch.setattr(builder, "normalize_csv_row", _spy_norm)
    monkeypatch.setattr(builder, "hash_canonical_rows", _spy_hash)

    env = make_env()
    assert _create(env) == builder.EXIT_OK

    assert len(hash_inputs) == 9
    hashed_ids = {id(row) for rows in hash_inputs for row in rows}
    assert len(normalized_ids) == 9 * FIXTURE_ROWS
    # 해시에 들어간 행이 전부 정규화 반환값 그 객체다 — 우회 경로가 없다.
    assert hashed_ids == normalized_ids


def test_column_types_alone_reproduce_content_hash(make_env) -> None:
    """manifest의 column_types만으로 재정규화·재해싱하면 기록 해시가 나온다.

    DDL 재파싱 없이 계약이 닫힌다(계획 v3 신설 — 2차 리뷰 권장 1 강한 안).
    """
    members = _default_members()
    env = make_env(members)
    assert _create(env) == builder.EXIT_OK
    manifest = _manifest(env)

    for table, entry in manifest["tables"].items():
        _, rows = mv3.parse_csv_bytes(
            members[builder.TABLE_MEMBERS[table]],
            table=table,
            expected_columns=entry["columns"],
        )
        rehashed = mv3.hash_canonical_rows(
            vn.normalize_csv_row(row, entry["column_types"]) for row in rows
        )
        assert rehashed == entry["content_hash"], table


# --- 3. 계약 위반 검출 -----------------------------------------------------------


def test_row_count_mismatch_names_table(
    make_env, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(
        row_counts={
            table: (99 if table == "metrology" else FIXTURE_ROWS)
            for table in builder.EXPECTED_ROW_COUNTS
        }
    )
    assert _run(env) == builder.EXIT_MISMATCH
    assert not env.out_path.exists()
    err = capsys.readouterr().err
    assert "metrology" in err
    assert err.count("\n") == 1


def test_column_order_change_is_rejected(make_env) -> None:
    columns = list(builder.EXPECTED_COLUMNS["evaluation"])
    columns[0], columns[1] = columns[1], columns[0]
    rows = [
        {column: _cell("evaluation", column, index) for column in columns}
        for index in range(FIXTURE_ROWS)
    ]
    lines = [",".join(columns)]
    lines += [",".join(row[column] for column in columns) for row in rows]
    payload = ("\n".join(lines) + "\n").encode("utf-8")

    env = make_env(_default_members({"evaluation": payload}))
    assert _run(env) == builder.EXIT_MISMATCH


def test_interior_bom_is_rejected(make_env) -> None:
    payload = _synth_csv("fdc_trace")
    tampered = payload.replace(b"\n", b"\n\xef\xbb\xbf", 1)
    env = make_env(_default_members({"fdc_trace": tampered}))
    assert _run(env) == builder.EXIT_MISMATCH


def test_ddl_type_drift_is_rejected(
    make_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """DDL 실물이 상수와 다르면 멈춘다 — 상수가 낡은 채 해시가 나가는 것을 막는다."""
    ddl = _synth_ddl().replace(b"upper_only boolean", b"upper_only varchar(5)")
    env = make_env(_default_members(ddl=ddl))
    assert _run(env) == builder.EXIT_MISMATCH
    assert "dim_parameter" in capsys.readouterr().err


def test_member_hash_mismatch_with_intake_is_rejected(
    make_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """ZIP 실물이 intake 등록 해시와 다르면 실패한다."""
    members = _default_members()
    claimed = dict(members)
    members[builder.GENERATOR_MEMBER] = b"# tampered generator\n"
    env = make_env(members, intake_members=claimed)
    assert _run(env) == builder.EXIT_MISMATCH
    assert "gen_sample_data.py" in capsys.readouterr().err


def test_epoch_mismatch_is_rejected(make_env) -> None:
    env = make_env(epoch_name="kosa_0813")
    assert _run(env) == builder.EXIT_MISMATCH


def test_intake_archive_sha_disagreeing_with_epoch_is_rejected(
    make_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """epoch·intake의 archive.sha256이 서로 다르면 ZIP을 열기 전에 실패한다."""
    env = make_env()
    intake = json.loads(builder.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8"))
    intake["archive"]["sha256"] = "0" * 64
    builder.INTAKE_ARTIFACT_PATH.write_text(
        json.dumps(intake, ensure_ascii=False), encoding="utf-8"
    )
    assert _run(env) == builder.EXIT_MISMATCH
    assert not env.out_path.exists()
    assert "intake" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", ["drop", "add"])
def test_selected_member_count_mismatch_is_rejected(make_env, mutation: str) -> None:
    """intake selected_members가 15개가 아니면 실패한다.

    add 케이스가 결정적이다 — 검사가 없으면 ZIP에 실존하는 16번째 member가 그대로
    판독·통과되어 exit 0이 된다.
    """
    members = _default_members()
    claimed = dict(members)
    if mutation == "drop":
        del claimed[builder.RAG_MEMBERS[0]]
    else:
        claimed["project/repository/backend/app/main.py"] = NOISE_MEMBERS[
            "project/repository/backend/app/main.py"
        ]
    env = make_env(members, intake_members=claimed)
    assert _run(env) == builder.EXIT_MISMATCH


def test_full_archive_hash_guards_equal_members(make_env, tmp_path: Path) -> None:
    """member 해시가 전부 일치해도 ZIP 전체 해시가 다르면 실패한다.

    member 대조만으로는 잡히지 않는 유일한 변형(등록 밖 내용 변경)을 전체 해시가
    막는다는 계약 — 이 검사가 없으면 exit 0이 된다.
    """
    env = make_env()
    members = _default_members()
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
        archive.writestr("project/repository/EXTRA.txt", b"unregistered change\n")
    assert (
        _run(Env(zip_path=other, origin_path=env.origin_path, out_path=env.out_path))
        == builder.EXIT_MISMATCH
    )
    assert not env.out_path.exists()


def test_archive_hash_mismatch_reads_no_member(
    make_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = make_env()
    # epoch·intake가 다른 ZIP(빈 파일)을 가리키게 한다.
    stale = tmp_path / "stale.zip"
    with zipfile.ZipFile(stale, "w") as archive:
        archive.writestr("placeholder.txt", b"x")
    env_stale = Env(zip_path=stale, origin_path=env.origin_path, out_path=env.out_path)

    def _fail(*args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("전체 해시 불일치인데 member를 판독했다")

    monkeypatch.setattr(zipfile.ZipFile, "read", _fail)
    assert _run(env_stale) == builder.EXIT_MISMATCH


def test_only_selected_members_are_read(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """판독 범위 계약 — intake 등록 15개와 정확히 일치, node_modules 미판독."""
    read_names: list[str] = []
    original = zipfile.ZipFile.read

    def _spy(self: zipfile.ZipFile, name: object, pwd: object = None) -> bytes:
        read_names.append(name if isinstance(name, str) else name.filename)
        return original(self, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", _spy)
    env = make_env()
    assert _create(env) == builder.EXIT_OK

    assert sorted(read_names) == sorted(_default_members())
    assert len(read_names) == 15
    assert not any("node_modules" in name for name in read_names)


def test_missing_selected_member_is_rejected(make_env) -> None:
    members = _default_members()
    claimed = dict(members)
    del members[builder.MASTER_CYPHER_MEMBER]
    env = make_env(members, intake_members=claimed)
    assert _run(env) == builder.EXIT_MISMATCH


# --- 4. 손상 입력 ---------------------------------------------------------------


@pytest.mark.parametrize("target", ["epoch", "intake"])
@pytest.mark.parametrize("content", [b"{not json", b"[]", b"42"])
def test_corrupt_input_artifact_is_usage_error(
    make_env,
    capsys: pytest.CaptureFixture[str],
    target: str,
    content: bytes,
) -> None:
    env = make_env()
    path = (
        builder.DATASET_EPOCH_PATH
        if target == "epoch"
        else builder.INTAKE_ARTIFACT_PATH
    )
    path.write_bytes(content)
    assert _run(env) == builder.EXIT_USAGE
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.startswith("[manifest-v4] 실패:")
    assert captured.err.count("\n") == 1


@pytest.mark.parametrize("target", ["epoch", "intake"])
def test_non_utf8_input_artifact_is_usage_error(
    make_env, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    env = make_env()
    path = (
        builder.DATASET_EPOCH_PATH
        if target == "epoch"
        else builder.INTAKE_ARTIFACT_PATH
    )
    path.write_bytes('{"a": "가"}'.encode("cp949"))
    assert _run(env) == builder.EXIT_USAGE
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


def test_missing_epoch_artifact_is_usage_error(
    make_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = make_env()
    monkeypatch.setattr(builder, "DATASET_EPOCH_PATH", tmp_path / "absent.json")
    assert _run(env) == builder.EXIT_USAGE


def test_missing_archive_is_usage_error(make_env, tmp_path: Path) -> None:
    env = make_env()
    absent = Env(
        zip_path=tmp_path / "absent.zip",
        origin_path=env.origin_path,
        out_path=env.out_path,
    )
    assert _run(absent) == builder.EXIT_USAGE


def test_corrupt_archive_is_usage_error(make_env, tmp_path: Path) -> None:
    """전체 해시는 통과하되 ZIP 구조가 깨진 경우."""
    env = make_env()
    payload = b"not a zip at all"
    broken = tmp_path / "broken.zip"
    broken.write_bytes(payload)
    # epoch·intake fixture를 깨진 파일의 해시로 다시 쓴다.
    sha = hashlib.sha256(payload).hexdigest()
    for path in (builder.DATASET_EPOCH_PATH, builder.INTAKE_ARTIFACT_PATH):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact["archive"]["sha256"] = sha
        path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    assert (
        _run(Env(zip_path=broken, origin_path=env.origin_path, out_path=env.out_path))
        == builder.EXIT_USAGE
    )


@pytest.mark.parametrize("content", ["{not json", "[]", "42"])
@pytest.mark.parametrize("verify_only", [False, True])
def test_existing_manifest_corrupt_is_mismatch(
    make_env,
    capsys: pytest.CaptureFixture[str],
    content: str,
    verify_only: bool,
) -> None:
    """쓰기 대상의 손상은 [승계] 규약대로 EXIT_MISMATCH이고 원본을 보존한다."""
    env = make_env()
    env.out_path.write_text(content, encoding="utf-8")
    flags = ("--verify-only",) if verify_only else ()
    assert _run(env, *flags) == builder.EXIT_MISMATCH
    assert env.out_path.read_text(encoding="utf-8") == content
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


def test_out_path_is_directory_is_usage_error(make_env, tmp_path: Path) -> None:
    env = make_env()
    out_dir = tmp_path / "isdir.json"
    out_dir.mkdir()
    assert (
        _run(Env(zip_path=env.zip_path, origin_path=env.origin_path, out_path=out_dir))
        == builder.EXIT_USAGE
    )


# --- 5. artifact 쓰기 5-case + --verify-only -------------------------------------


def test_rerun_byte_identical_does_not_write(make_env) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    stamp = env.out_path.stat().st_mtime_ns
    raw = env.out_path.read_text(encoding="utf-8")

    assert _create(env) == builder.EXIT_OK
    assert env.out_path.stat().st_mtime_ns == stamp
    assert env.out_path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("verify_only", [False, True])
def test_object_equal_bytes_differ(make_env, verify_only: bool) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    canonical = env.out_path.read_text(encoding="utf-8")
    mangled = json.dumps(
        json.loads(canonical), ensure_ascii=False, indent=4, sort_keys=True
    )
    env.out_path.write_text(mangled, encoding="utf-8")
    stamp = env.out_path.stat().st_mtime_ns

    flags = ("--verify-only",) if verify_only else ()
    assert _run(env, *flags) == builder.EXIT_OK
    if verify_only:
        assert env.out_path.read_text(encoding="utf-8") == mangled
        assert env.out_path.stat().st_mtime_ns == stamp
    else:
        assert env.out_path.read_text(encoding="utf-8") == canonical


def test_object_differs_requires_confirm(make_env) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    stale = _manifest(env)
    stale["dataset_epoch"] = "kosa_0813"
    env.out_path.write_text(builder.serialize(stale), encoding="utf-8")
    before = env.out_path.read_text(encoding="utf-8")

    assert _run(env) == builder.EXIT_CONFIRM_REQUIRED
    assert env.out_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("flag", ["--confirm", "--force"])
def test_confirm_overwrites(make_env, flag: str) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    canonical = env.out_path.read_text(encoding="utf-8")
    stale = json.loads(canonical)
    stale["dataset_epoch"] = "kosa_0813"
    env.out_path.write_text(builder.serialize(stale), encoding="utf-8")

    assert _run(env, flag) == builder.EXIT_OK
    assert env.out_path.read_text(encoding="utf-8") == canonical


def test_verify_only_matching_and_absent(make_env) -> None:
    env = make_env()
    assert _run(env, "--verify-only") == builder.EXIT_USAGE
    assert not env.out_path.exists()

    assert _create(env) == builder.EXIT_OK
    stamp = env.out_path.stat().st_mtime_ns
    assert _run(env, "--verify-only") == builder.EXIT_OK
    assert env.out_path.stat().st_mtime_ns == stamp


def test_verify_only_object_differs_never_writes(make_env) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    stale = _manifest(env)
    stale["dataset_epoch"] = "kosa_0813"
    env.out_path.write_text(builder.serialize(stale), encoding="utf-8")
    before = env.out_path.read_text(encoding="utf-8")

    assert _run(env, "--verify-only") == builder.EXIT_MISMATCH
    assert env.out_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("flag", ["--confirm", "--force"])
def test_verify_only_with_confirm_is_usage_error(make_env, flag: str) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    assert _run(env, "--verify-only", flag) == builder.EXIT_USAGE


def test_atomic_write_leaves_no_temp_file(
    make_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    stale = _manifest(env)
    stale["dataset_epoch"] = "kosa_0813"
    env.out_path.write_text(builder.serialize(stale), encoding="utf-8")
    before = env.out_path.read_text(encoding="utf-8")

    def _boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(builder.os, "replace", _boom)
    assert _run(env, "--confirm") == builder.EXIT_USAGE
    assert env.out_path.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


# --- 6. dim_parameter 신규 계약·비-UTF-8 stdout ----------------------------------


def test_dim_parameter_contract_is_pinned() -> None:
    """구 SOURCE_TABLE_FILES에 없던 9번째 테이블 — 승계 원본이 없는 신규 계약.

    10컬럼과 이 데이터셋 유일의 boolean(`upper_only`)을 상수 층위에서 고정한다.
    실물 CSV·DDL과의 대조는 스크립트 실행 자체가 수행한다(§2.1-5).
    """
    assert "dim_parameter" not in mv3.SOURCE_TABLE_FILES
    assert builder.EXPECTED_COLUMNS["dim_parameter"] == (
        "parameter_id",
        "parameter_name",
        "unit",
        "area",
        "target_value",
        "spec_lower",
        "ctrl_lower",
        "ctrl_upper",
        "spec_upper",
        "upper_only",
    )
    types = builder.EXPECTED_COLUMN_TYPES["dim_parameter"]
    assert types["upper_only"] == "boolean"
    assert set(types.values()) == {"text", "numeric", "boolean"}


# --- 6-1. 시스템설계 §2.3 확장 계약 (최종검증 1차 필수 1·2) -----------------------


def test_first_creation_requires_confirm(
    make_env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """대상 부재 + 무승인 → 쓰기 0회·EXIT_CONFIRM_REQUIRED (시스템설계 §2.3).

    오염된 source가 형식 검사를 통과해도 운영자 확인 없이 최초 기준 artifact로
    확정되지 않는다(최종검증 1차 필수 2). --confirm이 있으면 원자 생성된다.
    """
    env = make_env()

    assert _run(env) == builder.EXIT_CONFIRM_REQUIRED
    assert not env.out_path.exists()
    assert list(tmp_path.glob("**/*.tmp")) == []
    err = capsys.readouterr().err
    assert err.startswith("[manifest-v4] 실패:")
    assert err.count("\n") == 1
    assert "생성 예정" in err

    assert _run(env, "--confirm") == builder.EXIT_OK
    assert json.loads(env.out_path.read_text(encoding="utf-8"))["format_version"] == 4


def test_ddl_primary_key_drift_is_rejected(
    make_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """DDL 실물의 선언 PK가 상수와 다르면 멈춘다 — 컬럼 타입 대조와 같은 층위."""
    ddl = _synth_ddl().replace(
        b"PRIMARY KEY (lot_hist_id, parameter_id, seq_no)",
        b"PRIMARY KEY (lot_hist_id, parameter_id)",
    )
    env = make_env(_default_members(ddl=ddl))
    assert _run(env, "--confirm") == builder.EXIT_MISMATCH
    assert "fdc_trace" in capsys.readouterr().err


def test_ddl_missing_primary_key_is_rejected(make_env) -> None:
    ddl = _synth_ddl().replace(
        b"alarm_id varchar(20) PRIMARY KEY", b"alarm_id varchar(20)", 1
    )
    env = make_env(_default_members(ddl=ddl))
    assert _run(env, "--confirm") == builder.EXIT_MISMATCH


def test_primary_key_outside_column_contract_is_rejected(
    make_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PK 상수가 CSV 컬럼 계약 밖의 컬럼을 가리키면 실패한다.

    DDL과 상수가 함께 틀린 경우(둘 다 유령 컬럼을 PK로 선언)에도 컬럼 계약이
    마지막 방어선이 된다. 복합 PK로 만들어야 DDL 대조를 통과해 이 검사에 도달한다.
    """
    poisoned = dict(builder.EXPECTED_PRIMARY_KEYS)
    poisoned["dim_parameter"] = ("parameter_id", "ghost_col")
    monkeypatch.setattr(builder, "EXPECTED_PRIMARY_KEYS", poisoned)

    # fixture DDL은 monkeypatch된 상수로 생성되므로 유령 PK를 그대로 선언한다.
    env = make_env(_default_members())
    assert _run(env, "--confirm") == builder.EXIT_MISMATCH
    assert not env.out_path.exists()
    assert "dim_parameter" in capsys.readouterr().err


def test_intake_byte_change_breaks_verify(make_env) -> None:
    """intake artifact가 같은 경로에서 내용만 바뀌면 verify가 실패한다.

    `selected_entry_manifest_sha256`이 만드는 provenance 결합이다(최종검증 1차
    필수 1-2). 객체 의미가 같아도(공백만 변경) 바이트가 다르면 어긋난다.
    """
    env = make_env()
    assert _create(env) == builder.EXIT_OK

    raw = builder.INTAKE_ARTIFACT_PATH.read_text(encoding="utf-8")
    builder.INTAKE_ARTIFACT_PATH.write_text(
        json.dumps(json.loads(raw), ensure_ascii=False, indent=4), encoding="utf-8"
    )
    assert _run(env, "--verify-only") == builder.EXIT_MISMATCH


def test_profile_inclusion_constant_is_pinned() -> None:
    """시스템설계 §2.4 profile 표의 상수 고정 — action_history만 evaluation 전용."""
    assert builder.PROFILES == ("runtime", "runtime-e2e", "evaluation")
    for table, profiles in builder.INCLUDED_BY_PROFILE.items():
        if table == "action_history":
            assert profiles == ("evaluation",)
        else:
            assert profiles == builder.PROFILES


# --- 7. ① origin package (WBS 확대분 — 구현리뷰 1차 필수 1) ----------------------


def test_origin_artifacts_hash_mismatch_is_rejected(
    make_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """① 실물이 기준 문서 해시(상수)와 다르면 실패한다."""
    env = make_env()
    target = env.origin_path / "03_db" / "01_schema.sql"
    target.write_bytes(b"-- tampered ddl\n")
    assert _run(env) == builder.EXIT_MISMATCH
    assert not env.out_path.exists()
    assert "01_schema.sql" in capsys.readouterr().err


def test_origin_artifact_missing_is_usage_error(make_env) -> None:
    env = make_env()
    (env.origin_path / "05_scripts" / "load_documents.py").unlink()
    assert _run(env) == builder.EXIT_USAGE


def test_origin_package_dir_missing_is_usage_error(make_env, tmp_path: Path) -> None:
    env = make_env()
    absent = Env(
        zip_path=env.zip_path,
        origin_path=tmp_path / "absent-origin",
        out_path=env.out_path,
    )
    assert _run(absent) == builder.EXIT_USAGE


def test_final_package_artifact_cannot_be_sourced_from_origin(
    make_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """③에 존재하는 file_id를 ① 출처로 기록하려 하면 실패한다 — 확대분의 핵심 방어.

    RAG 3종·master.cypher는 ①·③ 양쪽에 같은 이름으로 존재한다(배포패키지_기준.md
    §3.2). 판정 규칙 "③에 있으면 ③을 쓴다"(§2)를 기계로 강제한다.
    """
    env = make_env()
    poisoned = dict(builder.ORIGIN_ARTIFACTS)
    payload = b"// stale origin cypher\n"
    (env.origin_path / "02_docs_rag").mkdir(parents=True, exist_ok=True)
    (env.origin_path / "02_docs_rag" / "master.cypher").write_bytes(payload)
    poisoned["stale_cypher"] = {
        "file_id": "02_docs_rag/master.cypher",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "role": "poisoned",
    }
    monkeypatch.setattr(builder, "ORIGIN_ARTIFACTS", poisoned)

    assert _run(env) == builder.EXIT_MISMATCH
    assert not env.out_path.exists()
    assert "대체할 수 없습니다" in capsys.readouterr().err


def test_repository_origin_constants_match_reference_document() -> None:
    """상수와 배포패키지_기준.md §3.1 표의 3원 대조 중 문서↔상수 축(tripwire).

    문서가 바뀌면 실패해 사람이 의도적으로 상수를 동기화하게 한다. 상수↔manifest
    축은 `test_source_manifest_v4.py`가 실 발급물로 고정한다.
    """
    section = (
        (REPOSITORY_ROOT / "docs" / "reference" / "배포패키지_기준.md")
        .read_text(encoding="utf-8")
        .split("### 3.1")[1]
        .split("### 3.2")[0]
    )
    documented = dict(re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", section))
    assert len(documented) == 3
    # monkeypatch 없이 import 시점의 실제 상수를 본다.
    import importlib

    fresh = importlib.import_module("build_source_manifest_v4")
    constants = {
        spec["file_id"]: spec["sha256"] for spec in fresh.ORIGIN_ARTIFACTS.values()
    }
    assert constants == documented


def test_success_output_survives_ascii_stdout(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """비-UTF-8 stdout에서도 정상 생성이 EXIT_OK로 끝난다(CM-1.2 필수 1 승계 완화)."""
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="ascii"))
    env = make_env()
    assert _create(env) == builder.EXIT_OK
    assert _manifest(env)["format_version"] == 4
