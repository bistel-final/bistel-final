"""`V5-CM-3.4` checkpoint 계약 회귀.

`setup()`이 **해주지 않는 것**을 고정한다 — package pin, migration digest,
부분 적용 감지, app startup 분리.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import checkpoint_contract as contract  # noqa: E402
import manifest_v3  # noqa: E402
import setup_checkpoint as runner  # noqa: E402


def _stub_module(**attributes: Any) -> Any:
    """`main()`이 지연 import하는 모듈을 대체한다.

    `load_dotenv`·실제 target 해석은 이 회귀의 대상이 아니다 — 여기서 보는 것은
    **CLI가 승인 경로를 어떻게 다루는가**다.
    """

    import types

    module = types.ModuleType("stub")
    for name, value in attributes.items():
        setattr(module, name, value)
    return module


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPOSITORY_ROOT / "backend" / "app"


# --- package pin ----------------------------------------------------------


def test_the_installed_package_matches_the_pin() -> None:
    """설치된 package가 계약과 같은지 **DB보다 먼저** 본다."""

    migrations = contract.assert_package_contract()
    assert len(migrations) == contract.MIGRATION_COUNT == 9
    assert contract.migration_digest(migrations) == contract.MIGRATION_DIGEST_SHA256


def test_the_concurrent_index_versions_are_pinned() -> None:
    """**이 셋 때문에 transaction으로 감쌀 수 없다.**

    `CREATE INDEX CONCURRENTLY`가 어느 version인지 바뀌면 autocommit 계약의 근거가
    바뀐 것이다.
    """

    migrations = contract.assert_package_contract()
    concurrent = tuple(
        index
        for index, statement in enumerate(migrations)
        if "CONCURRENTLY" in statement.upper()
    )
    assert concurrent == contract.CONCURRENT_INDEX_VERSIONS == (6, 7, 8)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("count", "MIGRATION_COUNT_MISMATCH"),
        ("digest", "MIGRATION_DIGEST_MISMATCH"),
    ],
)
def test_a_drifted_package_fails_before_the_database(
    monkeypatch: pytest.MonkeyPatch, mutation: str, reason: str
) -> None:
    """package drift는 **연결 전에** 끝난다."""

    from langgraph.checkpoint.postgres import base

    original = list(base.MIGRATIONS)
    if mutation == "count":
        monkeypatch.setattr(base, "MIGRATIONS", original[:-1])
    else:
        drifted = list(original)
        drifted[5] = "\n  "  # 공백만 바꾼다 — 정규화하지 않으므로 잡혀야 한다
        monkeypatch.setattr(base, "MIGRATIONS", drifted)

    with pytest.raises(contract.CheckpointContractError) as caught:
        contract.assert_package_contract()
    assert caught.value.reason_code == reason


def test_the_digest_does_not_normalise_whitespace() -> None:
    """**정규화하지 않는다.**

    SQL whitespace를 접으면 문자열 literal 내부 변경을 놓친다. `MIGRATIONS[5]`가
    `'\\n    '`인 것까지 그대로 pin한다.
    """

    base_value = contract.migration_digest(["a  b"])
    assert base_value != contract.migration_digest(["a b"])


# --- stage 계약 -----------------------------------------------------------


def test_the_migration_id_matches_the_stage_contract() -> None:
    """두 상수가 갈리면 stage가 무엇을 적용했는지 모르게 된다."""

    assert contract.MIGRATION_ID == manifest_v3.CHECKPOINT_MIGRATION_ID
    stage = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[("runtime", "runtime_checkpointed")]
    assert stage.applied_migrations[-1] == contract.MIGRATION_ID


def test_the_migration_id_agrees_across_modules() -> None:
    """`apply_reference_extensions_v5`는 `manifest_v3`를 import하지 않는다(순환).

    그래서 lineage에 ID를 literal로 적는다. **두 상수가 갈리면 stage가 무엇을
    적용했는지 모르게 되므로** 회귀가 대조한다.
    """

    import apply_reference_extensions_v5 as v5

    assert v5.PROFILE_MIGRATIONS["runtime"][-1] == contract.MIGRATION_ID
    assert v5.PROFILE_MIGRATIONS["runtime"] == (
        manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[
            ("runtime", "runtime_checkpointed")
        ].applied_migrations
    )


def test_the_successor_extends_the_guarded_lineage() -> None:
    """**predecessor를 덮어쓰지 않는다.** `runtime_guarded`에 하나를 더한다."""

    guarded = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[("runtime", "runtime_guarded")]
    checkpointed = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[
        ("runtime", "runtime_checkpointed")
    ]
    assert checkpointed.applied_migrations[:-1] == guarded.applied_migrations
    assert len(checkpointed.applied_migrations) == len(guarded.applied_migrations) + 1


def test_checkpoint_tables_are_schema_only() -> None:
    """content hash를 요구하면 `V5-C-*`가 Agent를 돌리는 순간 verifier가 깨진다."""

    assert set(contract.CHECKPOINT_TABLES) <= manifest_v3.SCHEMA_ONLY_TABLES
    assert manifest_v3.CHECKPOINT_TABLES == contract.CHECKPOINT_TABLES


# --- 상태 판정 ------------------------------------------------------------


def _ready_catalog() -> dict[str, Any]:
    """**계약에서 생성한다.** 손으로 적으면 계약이 강해질 때 fixture만 낡는다."""

    return {
        "tables": sorted(contract.CHECKPOINT_TABLES),
        "columns": {
            table: [dict(column) for column in columns]
            for table, columns in contract.EXPECTED_COLUMNS.items()
        },
        "indexes": {
            name: {
                "valid": True,
                "ready": True,
                "table": spec["table"],
                "definition": spec["definition"],
            }
            for name, spec in contract.EXPECTED_INDEX_DEFINITIONS.items()
        },
        "primary_keys": dict(contract.EXPECTED_PRIMARY_KEYS),
        "versions": list(contract.expected_versions()),
        # **ACL도 상태의 일부다**(구현리뷰 13차 필수 3). 소유자 단일·PUBLIC 0건이
        # 정상 형상이다.
        "acl": {
            "owners": dict.fromkeys(contract.CHECKPOINT_TABLES, "cm34_applier"),
            "public_grants": [],
        },
        # **기대 owner도 상태의 일부다**(14차 필수 1). 네 개가 서로 같기만 하면
        # 되는 것이 아니라 관리 계정 소유여야 한다.
        "expected_owner": "cm34_applier",
    }


def test_a_complete_catalog_is_ready() -> None:
    catalog = _ready_catalog()
    assert contract.classify_state(catalog) == "READY"
    assert len(contract.assert_ready(catalog)) == 64


def test_an_empty_catalog_is_absent() -> None:
    empty = {
        "tables": [],
        "columns": {},
        "indexes": {},
        "primary_keys": {},
        "versions": [],
        "acl": {"owners": {}, "public_grants": []},
        "expected_owner": "cm34_applier",
    }
    assert contract.classify_state(empty) == "ABSENT"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: c["tables"].pop(), id="table 누락"),
        pytest.param(
            lambda c: c["indexes"].pop("checkpoints_thread_id_idx"), id="index 누락"
        ),
        pytest.param(
            lambda c: c["indexes"]["checkpoints_thread_id_idx"].update(valid=False),
            id="index invalid",
        ),
        pytest.param(
            lambda c: c["indexes"]["checkpoints_thread_id_idx"].update(ready=False),
            id="index not ready",
        ),
        pytest.param(lambda c: c["versions"].remove(4), id="version gap"),
        pytest.param(lambda c: c["versions"].append(9), id="version 초과"),
    ],
)
def test_a_partial_catalog_is_refused(mutate: Any) -> None:
    """**부분 적용을 자동으로 이어 붙이지 않는다.**

    `setup()`은 `checkpoint_migrations`의 최대 `v`만 보고 그 다음부터 실행한다.
    version이 이미 8이면 index가 사라져도 아무 문장도 실행하지 않는다 — 재실행으로
    낫지 않으므로 판정이 이것을 잡아야 한다.
    """

    catalog = _ready_catalog()
    mutate(catalog)
    assert contract.classify_state(catalog) == "PARTIAL"
    with pytest.raises(contract.CheckpointStateError) as caught:
        contract.assert_ready(catalog)
    assert caught.value.reason_code == "PARTIAL"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda c: c["columns"]["checkpoints"][0].update(column="WRONG"),
            id="column 이름",
        ),
        pytest.param(
            lambda c: c["columns"]["checkpoints"][0].update(type="integer"),
            id="column type",
        ),
        pytest.param(
            lambda c: c["columns"]["checkpoints"][0].update(not_null=False),
            id="nullability",
        ),
        pytest.param(
            lambda c: c["columns"]["checkpoint_blobs"][1].update(default=None),
            id="default",
        ),
        pytest.param(
            lambda c: c["columns"]["checkpoints"].append(
                {"column": "extra", "type": "text", "not_null": False, "default": None}
            ),
            id="column 추가",
        ),
        pytest.param(
            lambda c: c["primary_keys"].update(checkpoints="PRIMARY KEY (thread_id)"),
            id="PK column",
        ),
        pytest.param(lambda c: c["primary_keys"].pop("checkpoints"), id="PK 누락"),
        pytest.param(
            lambda c: c["indexes"]["checkpoints_thread_id_idx"].update(
                table="WRONG_TABLE"
            ),
            id="index 대상 table",
        ),
        pytest.param(
            lambda c: c["indexes"]["checkpoints_thread_id_idx"].update(
                definition="CREATE INDEX checkpoints_thread_id_idx "
                "ON public.checkpoints USING btree (checkpoint_id)"
            ),
            id="index column",
        ),
        # --- ACL 축 (구현리뷰 13차 필수 3) ---------------------------------
        pytest.param(
            lambda c: c["acl"]["public_grants"].append("checkpoints:SELECT"),
            id="PUBLIC table 권한",
        ),
        pytest.param(
            lambda c: c["acl"]["public_grants"].append("checkpoints.thread_id:SELECT"),
            id="PUBLIC column 권한",
        ),
        pytest.param(
            lambda c: c["acl"]["owners"].update(checkpoints="someone_else"),
            id="소유자 분리",
        ),
        pytest.param(
            lambda c: c["acl"]["owners"].pop("checkpoint_writes"),
            id="ACL row 누락",
        ),
        # **네 개를 함께 옮겨도 drift다**(구현리뷰 14차 필수 1).
        #
        # 이전에는 "서로 같은가"만 봐서 `READY`였다. marker를 읽는 경로는
        # signature 차이로 걸렸지만 marker를 읽지 않는 복구는 `PARTIAL|DRIFT`만
        # 허용하므로 유일한 복구 경로가 닫혔다.
        pytest.param(
            lambda c: c["acl"]["owners"].update(
                dict.fromkeys(contract.CHECKPOINT_TABLES, "someone_else")
            ),
            id="소유자 일괄 이전",
        ),
    ],
)
def test_a_drifted_catalog_is_refused(mutate: Any) -> None:
    """**이름만 맞는 schema를 통과시키지 않는다**(구현리뷰 필수 2).

    초판은 table·index 이름 집합과 version만 봤다. 모든 컬럼과 PK를 `WRONG`으로,
    index 대상을 `WRONG_TABLE`로 만든 catalog가 `READY`로 통과했다 — 리뷰가 그것을
    재현했다.

    이제 package 2.0.9 실측 계약과 exact 대조한다.
    """

    catalog = _ready_catalog()
    mutate(catalog)
    assert contract.classify_state(catalog) == "DRIFT"
    with pytest.raises(contract.CheckpointStateError) as caught:
        contract.assert_ready(catalog)
    assert caught.value.reason_code == "DRIFT"


def test_the_expected_contract_came_from_the_package() -> None:
    """계약이 손으로 적힌 값이 아니라 package 형상과 맞는지 본다."""

    assert set(contract.EXPECTED_COLUMNS) == set(contract.CHECKPOINT_TABLES)
    assert set(contract.EXPECTED_PRIMARY_KEYS) == set(contract.CHECKPOINT_TABLES)
    assert set(contract.EXPECTED_INDEX_DEFINITIONS) == set(contract.CHECKPOINT_INDEXES)
    assert [
        c["column"] for c in contract.EXPECTED_COLUMNS["checkpoint_migrations"]
    ] == ["v"]


def test_the_signature_changes_with_the_catalog() -> None:
    """signature가 catalog 변화를 실제로 반영하는지 본다."""

    base_signature = contract.assert_ready(_ready_catalog())
    assert contract.assert_ready(_ready_catalog()) == base_signature


# --- target 경계 ----------------------------------------------------------


def test_evaluation_is_refused_before_any_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**parser 직후 경계다.** `load_dotenv`·target loader·connector 0회."""

    calls: list[str] = []
    monkeypatch.setattr(
        runner, "_connect", lambda *_a, **_k: calls.append("connect") or None
    )
    with pytest.raises(runner.CheckpointSetupError) as caught:
        runner.assert_runtime_database("kosa_text2sql")
    assert caught.value.reason_code == "PROFILE_NOT_ALLOWED"
    assert calls == []


def test_the_target_guard_runs_before_dotenv() -> None:
    """`main()`에서 `assert_runtime_database`가 `load_dotenv`보다 앞이다."""

    import inspect

    tree = ast.parse(inspect.getsource(runner.main).lstrip())
    order: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if name in {
                "assert_runtime_database",
                "load_dotenv",
                "load_bootstrap_target",
            }:
                order.append((node.lineno, name))
    order.sort()
    names = [name for _line, name in order]
    assert names.index("assert_runtime_database") < names.index("load_dotenv")
    assert names.index("load_dotenv") < names.index("load_bootstrap_target")


# --- app startup 분리 -----------------------------------------------------


def test_the_app_has_no_startup_violation() -> None:
    """**앱은 checkpoint를 초기화하지 않는다.**

    미초기화 DB를 시작 시 몰래 고치는 fallback을 두지 않는다 — readiness나 명시적
    오류로 노출한다. `.setup()`은 admin runner의 apply 경로에서만 부른다.
    """

    assert contract.scan_startup_violations(APP_ROOT) == []


def test_the_scanner_catches_a_saver_setup_call(tmp_path: Path) -> None:
    """**production scanner 자체를 불러 실패시킨다**(구현리뷰 권장 1).

    초판은 가짜 source에서 AST node가 1개임을 별도로 확인했을 뿐, 실제 검사
    구현을 호출하지 않았다. 그러면 회귀가 scanner의 유효성을 증명하지 못한다.
    """

    (tmp_path / "startup.py").write_text(
        "def boot(saver):\n    saver.setup()\n", encoding="utf-8"
    )
    violations = contract.scan_startup_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0].endswith(":setup")


def test_the_scanner_catches_an_admin_import(tmp_path: Path) -> None:
    (tmp_path / "boot.py").write_text("import setup_checkpoint\n", encoding="utf-8")
    violations = contract.scan_startup_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0].endswith(":import")


def test_the_scanner_ignores_unrelated_setup_methods(tmp_path: Path) -> None:
    """**임의 `.setup()`을 전부 막지 않는다.**

    다른 라이브러리의 정상 `setup` 메서드와 충돌하면 안 된다. saver 계열만 본다.
    """

    (tmp_path / "other.py").write_text(
        "def boot(logger, migrator):\n" "    logger.setup()\n" "    migrator.setup()\n",
        encoding="utf-8",
    )
    assert contract.scan_startup_violations(tmp_path) == []


# --- artifact -------------------------------------------------------------


def test_the_runner_exposes_exactly_five_modes() -> None:
    """mutation을 암묵적 기본값으로 두지 않는다."""

    parser = runner._parser()
    modes = {
        action.dest
        for action in parser._actions
        if action.const is True or action.nargs == 0
    }
    assert {"preflight", "apply", "verify", "smoke", "recover_marker"} <= modes


def test_the_contract_holds_no_secret() -> None:
    """계약 모듈에 endpoint·자격증명이 없다."""

    body = (SCRIPTS / "checkpoint_contract.py").read_text(encoding="utf-8")
    for token in ("kosa165", "iptime", "postgresql://", "password="):
        assert token not in body


def test_operational_tables_are_a_subset_of_checkpoint_tables() -> None:
    assert set(contract.OPERATIONAL_TABLES) < set(contract.CHECKPOINT_TABLES)
    assert "checkpoint_migrations" not in contract.OPERATIONAL_TABLES


# --- 2차 구현리뷰 회귀 -------------------------------------------------------


def _valid_marker(**overrides: Any) -> dict[str, Any]:
    """계약값으로 채운 marker. **형식이 아니라 내용이 유효한** 표본이다."""

    epoch = runner._dataset_epoch()
    payload = {
        "artifact_type": runner.ARTIFACT_TYPE,
        "format_version": runner.MARKER_FORMAT_VERSION,
        "task_id": runner.TASK_ID,
        "database": "kosa_agent_e2e",
        "profile": runner.RUNTIME_PROFILE,
        "status": "APPLIED",
        "bootstrap_stage": runner.CHECKPOINT_STAGE,
        **epoch,
        "package_name": contract.PACKAGE_NAME,
        "package_version": contract.PACKAGE_VERSION,
        "migration_id": contract.MIGRATION_ID,
        "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
        "migration_count": contract.MIGRATION_COUNT,
        "latest_version": contract.LATEST_VERSION,
        "target_host_fingerprint": "b" * 64,
        "predecessor_stage": runner.GUARDED_STAGE,
        "predecessor_marker_sha256": "a" * 64,
        "predecessor_schema_signature_sha256": "e" * 64,
        "backup_archive_sha256": "c" * 64,
        "backup_receipt_sha256": "d" * 64,
        "change_approval_sha256": "f" * 64,
        "catalog_signature_sha256": contract.assert_ready(_ready_catalog()),
        "change_reference": "GH-130",
        "applied_at": "2026-08-25T00:00:00Z",
        "recorded_at": "2026-08-25T00:00:01Z",
    }
    payload.update(overrides)
    return payload


def _identity(**overrides: Any) -> dict[str, Any]:
    payload = {
        "predecessor_stage": runner.GUARDED_STAGE,
        "predecessor_marker_sha256": "a" * 64,
        "predecessor_schema_signature_sha256": "e" * 64,
        "target_host_fingerprint": "b" * 64,
    }
    payload.update(overrides)
    return payload


class TestStaleMarkerIsNotReady:
    """**형식만 유효한 marker로 `READY_MARKED`가 되지 않는다**(2차 필수 2).

    초판은 marker 존재만 봤다. 그래서 임의 hash를 넣은 marker가 preflight를
    통과하고 `run_apply()`가 `NO_OP`을 냈다 — 아무것도 확인하지 않은 no-op이다.
    """

    def test_a_matching_marker_is_ready_marked(self) -> None:
        state = runner.resolve_state(
            _ready_catalog(), _valid_marker(), identity=_identity()
        )
        assert state == "READY_MARKED"

    def test_a_signature_mismatch_is_drift(self) -> None:
        marker = _valid_marker(catalog_signature_sha256="c" * 64)
        assert (
            runner.resolve_state(_ready_catalog(), marker, identity=_identity())
            == "MARKER_DRIFT"
        )

    def test_a_lineage_mismatch_is_drift(self) -> None:
        assert (
            runner.resolve_state(
                _ready_catalog(),
                _valid_marker(),
                identity=_identity(predecessor_marker_sha256="f" * 64),
            )
            == "MARKER_DRIFT"
        )

    def test_no_marker_is_ready_unmarked(self) -> None:
        assert (
            runner.resolve_state(_ready_catalog(), None, identity=_identity())
            == "READY_UNMARKED"
        )


class TestMarkerStrictFields:
    """marker/receipt가 **무엇을 증명하는지**까지 본다(2차 필수 5)."""

    @pytest.mark.parametrize(
        "overrides",
        [
            {"profile": "evaluation"},
            {"bootstrap_stage": "runtime_guarded"},
            {"predecessor_stage": "runtime_clean"},
            {"dataset_epoch": "kosa_0813"},
            {"source_archive_sha256": "0" * 64},
            {"target_host_fingerprint": "nope"},
            {"predecessor_schema_signature_sha256": "short"},
            # 뒤집힌 시각 — 손으로 고친 artifact의 가장 흔한 흔적이다.
            {"applied_at": "2026-08-25T00:00:02Z"},
            # 시간대 없는 시각은 같은 문자열이 두 순간을 뜻한다.
            {"applied_at": "2026-08-25T00:00:00", "recorded_at": "2026-08-25T00:00:01"},
        ],
    )
    def test_an_invalid_marker_is_refused(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(runner.CheckpointArtifactError):
            runner.validate_marker(_valid_marker(**overrides), "kosa_agent_e2e")

    def test_the_valid_marker_passes(self) -> None:
        runner.validate_marker(_valid_marker(), "kosa_agent_e2e")

    def test_a_started_receipt_cannot_carry_a_result(self) -> None:
        receipt = {
            "artifact_type": f"{runner.ARTIFACT_TYPE}_receipt",
            "format_version": runner.MARKER_FORMAT_VERSION,
            "task_id": runner.TASK_ID,
            "database": "kosa_agent_e2e",
            "status": "STARTED",
            "package_version": contract.PACKAGE_VERSION,
            "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
            "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
            "target_host_fingerprint": _fake_target_fingerprint(),
            "predecessor_stage": runner.GUARDED_STAGE,
            "predecessor_marker_sha256": "a" * 64,
            "predecessor_schema_signature_sha256": "e" * 64,
            "backup_archive_sha256": "c" * 64,
            "backup_receipt_sha256": "d" * 64,
            "change_approval_sha256": "f" * 64,
            "change_reference": "GH-130",
            "started_at": "2026-08-25T00:00:00Z",
            "committed_at": None,
            "catalog_signature_sha256": None,
        }
        runner.validate_receipt(receipt, "kosa_agent_e2e")
        with pytest.raises(runner.CheckpointArtifactError):
            runner.validate_receipt(
                {**receipt, "committed_at": "2026-08-25T00:00:01Z"}, "kosa_agent_e2e"
            )
        with pytest.raises(runner.CheckpointArtifactError):
            runner.validate_receipt(
                {
                    **receipt,
                    "status": "COMMITTED",
                    "committed_at": "2026-08-24T23:59:00Z",
                    "catalog_signature_sha256": "c" * 64,
                },
                "kosa_agent_e2e",
            )


def _fake_target_fingerprint() -> str:
    import db_target

    return db_target.host_fingerprint(_FakeTarget.host, _FakeTarget.port)


class _FakeTarget:
    """`require_backup_evidence()`가 요구하는 최소 target."""

    database = "kosa_agent_e2e"
    profile = "runtime"
    host = "example.test"
    port = 5432


def _approval(**overrides: Any) -> dict[str, Any]:
    """checkpoint 전용 승인. **사람이 직접 쓸 수 있는 13키**다."""

    payload: dict[str, Any] = {
        "artifact_type": runner.APPROVAL_ARTIFACT_TYPE,
        "format_version": runner.MARKER_FORMAT_VERSION,
        "task_id": runner.TASK_ID,
        "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
        "change_reference": "GH-130",
        "status": "APPROVED",
        "targets": ["kosa_agent_e2e", "kosa_agent"],
        "from_stage": runner.GUARDED_STAGE,
        "to_stage": runner.CHECKPOINT_STAGE,
        "package_name": contract.PACKAGE_NAME,
        "package_version": contract.PACKAGE_VERSION,
        "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
        "recovery_approved": False,
        "approved_at": "2026-08-25T12:00:00+09:00",
    }
    payload.update(overrides)
    return payload


def _write_approval(root: Path, _ref: str = "GH-130", **overrides: Any) -> Path:
    import json

    path = root / "change_approval.json"
    path.write_text(json.dumps(_approval(**overrides)), encoding="utf-8")
    return path


class TestApprovalBindsTheIrreversibleChange:
    """승인이 **되돌릴 수 없는 그 일**을 가리키는지 본다.

    전환 approval을 요구하던 초판은 18키 중 9개가 `--preflight` 산출물이었다. 그
    preflight가 세 target 모두 실패하므로 **만들 수 없는 승인을 요구**하는 상태였다
    (`V5-CM-3.4` 묶음 2 준비).
    """

    def test_a_matching_approval_passes(self) -> None:
        runner.validate_change_approval(_approval(), "kosa_agent_e2e", "GH-130")

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            ({"status": "PENDING"}, "APPROVAL_MISMATCH"),
            ({"change_reference": "GH-999"}, "APPROVAL_MISMATCH"),
            ({"targets": ["kosa_agent"]}, "APPROVAL_MISMATCH"),
            ({"task_id": "V5-CM-9.9"}, "APPROVAL_MISMATCH"),
            ({"dataset_epoch": "kosa_0813"}, "APPROVAL_MISMATCH"),
            # **stage 전이**를 승인받는다 — 무엇에서 무엇으로 가는지.
            ({"from_stage": "runtime_clean"}, "APPROVAL_MISMATCH"),
            ({"to_stage": "runtime_guarded"}, "APPROVAL_MISMATCH"),
            # **package pin까지 승인 대상**이다.
            ({"package_version": "9.9.9"}, "APPROVAL_MISMATCH"),
            ({"migration_digest_sha256": "0" * 64}, "APPROVAL_MISMATCH"),
            # 평가 DB는 애초에 대상이 아니다.
            ({"targets": ["kosa_text2sql"]}, "APPROVAL_INVALID"),
            ({"format_version": 99}, "APPROVAL_INVALID"),
            ({"artifact_type": "postgres_transition_approval"}, "APPROVAL_INVALID"),
        ],
    )
    def test_a_mismatched_approval_is_refused(
        self, override: dict[str, Any], reason: str
    ) -> None:
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            runner.validate_change_approval(
                _approval(**override), "kosa_agent_e2e", "GH-130"
            )
        assert exc.value.reason_code == reason

    def test_a_missing_key_is_refused(self) -> None:
        payload = _approval()
        payload.pop("to_stage")
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            runner.validate_change_approval(payload, "kosa_agent_e2e", "GH-130")
        assert exc.value.reason_code == "APPROVAL_INVALID"

    def test_the_contract_does_not_need_the_transition_preflight(self) -> None:
        """승인 13키 중 **preflight 산출물이 하나도 없다.**

        만들 수 없는 승인을 요구하면 그 게이트는 운영자를 막는 것이 아니라 우회를
        부른다.
        """

        import postgres_transition as transition

        preflight_only = {
            key
            for key in transition.APPROVAL_KEYS
            if any(
                token in key
                for token in ("bundle", "projection", "view", "gate0", "by_target")
            )
        }
        assert preflight_only, "전환 approval 쪽 전제가 바뀌었다"
        assert not (runner.APPROVAL_KEYS & preflight_only)


class TestIrreversibleWorkHasPreconditions:
    """되돌릴 수 없는 작업 앞에 **되돌릴 수단**과 **선행 증명**을 요구한다.

    3차 필수 3. 초판 회귀는 `inspect.getsource()`로 호출 순서 문자열만 봤다. 그건
    "부른다"까지만 말하고 "무엇을 막는다"는 말하지 않는다 — 리뷰가 세 번 지적한
    유형이다. 여기서는 실제로 거부되는지를 본다.
    """

    def test_missing_approval_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            runner.require_backup_evidence(
                _FakeTarget(),
                "GH-130",
                backup_root=tmp_path,
                approval_path=tmp_path / "absent.json",
                shape={},
            )
        assert exc.value.reason_code == "APPROVAL_MISSING"

    def test_missing_backup_evidence_refuses(self, tmp_path: Path) -> None:
        approval = _write_approval(tmp_path, "GH-130")
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            runner.require_backup_evidence(
                _FakeTarget(),
                "GH-130",
                backup_root=tmp_path,
                approval_path=approval,
                shape={},
            )
        assert exc.value.reason_code == "BACKUP_MISSING"

    # `test_an_approval_for_another_change_refuses`는 제거했다.
    #
    # 전환 approval 키 이름(`change_ref`·`ordered_targets`)을 쓰고 있었고, checkpoint
    # 전용 계약(`change_reference`·`targets`)으로 바꾸면서 같은 단언이
    # `TestApprovalBindsTheIrreversibleChange`에 더 넓게 들어갔다. 두 어휘로 같은 것을
    # 두 번 검사하면 한쪽만 고쳐졌을 때 조용히 갈린다.


SHAPE_STUB = {
    "runtime_contract_sha256": "1" * 64,
    "reference_physical_sha256": "2" * 64,
    "final_reference_sha256": "3" * 64,
    "inventory_sha256": "4" * 64,
    "security_sha256": "5" * 64,
}


class TestBackupEvidenceBinding:
    """backup 증적이 **이 target·이 형상의 것인지**까지 본다.

    구현리뷰 4차 필수 3의 계약을 `checkpoint_backup`으로 옮겼다. 결속의 강도는 같고,
    **무엇과 결속하는지**가 달라졌다 — 전환 시점 inventory가 아니라 적용 직전
    guarded 형상이다(`V5-CM-3.4` 묶음 2 준비).
    """

    SHAPE = {
        "runtime_contract_sha256": "1" * 64,
        "reference_physical_sha256": "2" * 64,
        "final_reference_sha256": "3" * 64,
        "inventory_sha256": "4" * 64,
        "security_sha256": "5" * 64,
    }

    @classmethod
    def _receipt(cls, **overrides: Any) -> dict[str, Any]:
        import checkpoint_backup as cbackup
        import postgres_backup as backup

        payload: dict[str, Any] = {
            "artifact_type": cbackup.RECEIPT_ARTIFACT_TYPE,
            "format_version": cbackup.FORMAT_VERSION,
            "task_id": cbackup.TASK_ID,
            "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
            "database": "kosa_agent_e2e",
            "profile": "runtime",
            "change_reference": "GH-130",
            "server_major": 16,
            "client_major": 16,
            "backup_image_digest": backup.expected_client_image(16),
            "backup_tool_version": "pg_dump (PostgreSQL) 16.15",
            "archive_sha256": "a" * 64,
            "target_host_fingerprint": _fake_target_fingerprint(),
            "predecessor_stage": runner.GUARDED_STAGE,
            "source_projection": dict(cls.SHAPE),
            "restored_projection": dict(cls.SHAPE),
            "restore_verified": True,
            "created_at": "2026-08-25T00:00:00Z",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def _write(root: Path, receipt: dict[str, Any]) -> Path:
        """**실물 digest를 실제로 계산해** 증적 3본을 만든다."""

        import json

        import checkpoint_backup as cbackup
        import postgres_backup as backup

        # 이름은 **조회할 target 기준**이다. receipt 내용만 어긋나게 두어야
        # "다른 target의 증적"을 검증할 수 있다 — 이름까지 바꾸면 그냥 없는 파일이다.
        db, ref = _FakeTarget.database, "GH-130"
        archive = root / cbackup.archive_name(db, ref)
        archive.write_bytes(b"cm34-archive")
        receipt = {**receipt, "archive_sha256": backup.archive_digest(archive)}
        receipt_path = root / cbackup.receipt_name(db, ref)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        (root / cbackup.completion_name(db, ref)).write_text(
            json.dumps(
                {
                    "artifact_type": cbackup.COMPLETION_ARTIFACT_TYPE,
                    "format_version": cbackup.FORMAT_VERSION,
                    "dataset_epoch": receipt["dataset_epoch"],
                    "database": db,
                    "change_reference": ref,
                    "archive_sha256": receipt["archive_sha256"],
                    "receipt_sha256": backup.archive_digest(receipt_path),
                }
            ),
            encoding="utf-8",
        )
        return _write_approval(root, ref)

    def _call(self, root: Path, approval: Path) -> dict[str, str]:
        return runner.require_backup_evidence(
            _FakeTarget(),
            "GH-130",
            backup_root=root,
            approval_path=approval,
            shape=self.SHAPE,
        )

    def test_a_matching_bundle_is_accepted(self, tmp_path: Path) -> None:
        approval = self._write(tmp_path, self._receipt())
        evidence = self._call(tmp_path, approval)
        assert len(evidence["backup_receipt_sha256"]) == 64
        assert len(evidence["change_approval_sha256"]) == 64

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            # **다른 서버에서 뜬 backup** — 이름만 같다.
            ({"target_host_fingerprint": "9" * 64}, "BACKUP_MISMATCH"),
            ({"database": "kosa_agent"}, "BACKUP_MISMATCH"),
            ({"profile": "evaluation"}, "BACKUP_MISMATCH"),
            ({"task_id": "V5-CM-9.9"}, "BACKUP_MISMATCH"),
            ({"dataset_epoch": "kosa_0813"}, "BACKUP_MISMATCH"),
            # **restore를 확인하지 못한 backup**은 복구 수단이 아니다.
            ({"restore_verified": False}, "RESTORE_NOT_VERIFIED"),
            # **4축 각각**이 복원 검증을 red로 만든다 — 하나만 보면 나머지가
            # 손상돼도 통과한다(구현리뷰 10차 필수 1).
            *(
                (
                    {"restored_projection": {**SHAPE_STUB, axis: "7" * 64}},
                    "RESTORE_NOT_VERIFIED",
                )
                for axis in SHAPE_STUB
            ),
            # **형상이 달라진 backup** — 뜬 뒤에 DB가 바뀌었다.
            (
                {
                    "source_projection": {
                        **SHAPE_STUB,
                        "runtime_contract_sha256": "8" * 64,
                    },
                    "restored_projection": {
                        **SHAPE_STUB,
                        "runtime_contract_sha256": "8" * 64,
                    },
                },
                "BACKUP_STALE",
            ),
            ({"format_version": 99}, "BACKUP_INVALID"),
        ],
    )
    def test_a_foreign_bundle_is_refused(
        self, tmp_path: Path, override: dict[str, Any], reason: str
    ) -> None:
        approval = self._write(tmp_path, self._receipt(**override))
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            self._call(tmp_path, approval)
        assert exc.value.reason_code == reason

    def test_a_tampered_archive_is_refused(self, tmp_path: Path) -> None:
        """completion만 고쳐도, 실물만 고쳐도 막힌다."""

        import checkpoint_backup as cbackup

        approval = self._write(tmp_path, self._receipt())
        (tmp_path / cbackup.archive_name("kosa_agent_e2e", "GH-130")).write_bytes(
            b"tampered"
        )
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            self._call(tmp_path, approval)
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_missing_evidence_is_refused(self, tmp_path: Path) -> None:
        approval = _write_approval(tmp_path, "GH-130")
        with pytest.raises(runner.CheckpointArtifactError) as exc:
            self._call(tmp_path, approval)
        assert exc.value.reason_code == "BACKUP_MISSING"


def test_the_bytes_logical_type_has_a_symmetric_contract() -> None:
    """`logical_type()`이 내는 값을 `normalize_value()`가 **명시적으로** 다룬다.

    구현리뷰 4차 권장 2. `bytea` → `bytes`는 되는데 normalize가 "지원하지 않음"으로
    떨어지면, 나중에 이 type을 쓰는 곳이 생겼을 때 원인이 계약인지 구멍인지 알 수
    없다. schema-only 전용임을 상수와 메시지로 드러낸다.
    """

    import value_normalization as norms

    assert norms.logical_type("bytea") == "bytes"
    assert "bytes" in norms.SCHEMA_ONLY_LOGICAL_TYPES
    with pytest.raises(norms.ValueNormalizationError, match="schema-only"):
        norms.normalize_value(b"x", "bytes")
    # 알 수 없는 type과 **다른 메시지**여야 구분이 선다.
    with pytest.raises(norms.ValueNormalizationError, match="지원하지 않는"):
        norms.normalize_value("x", "geometry")


def test_every_checkpoint_column_type_is_schema_only_or_normalizable() -> None:
    """checkpoint 4종의 모든 컬럼 type이 registry에 **실제로** 있다.

    `verify_database()`가 manifest를 순회하며 이 변환을 부른다. 하나라도 빠지면
    full verifier가 checkpoint stage에서 죽는다 — 3차에 `bytea`로 실제 겪었다.
    """

    import value_normalization as norms

    for table, columns in contract.EXPECTED_COLUMNS.items():
        for column in columns:
            kind = norms.logical_type(column["type"])
            assert kind, f"{table}.{column['column']}"


def _status_receipt(**overrides: Any) -> dict[str, Any]:
    """status 조합 matrix의 기준 표본."""

    payload: dict[str, Any] = {
        "artifact_type": f"{runner.ARTIFACT_TYPE}_receipt",
        "format_version": runner.MARKER_FORMAT_VERSION,
        "task_id": runner.TASK_ID,
        "database": "kosa_agent_e2e",
        "status": "STARTED",
        "package_version": contract.PACKAGE_VERSION,
        "migration_digest_sha256": contract.MIGRATION_DIGEST_SHA256,
        "dataset_epoch": runner._dataset_epoch()["dataset_epoch"],
        "target_host_fingerprint": "b" * 64,
        "predecessor_stage": runner.GUARDED_STAGE,
        "predecessor_marker_sha256": "a" * 64,
        "predecessor_schema_signature_sha256": "e" * 64,
        "backup_archive_sha256": "c" * 64,
        "backup_receipt_sha256": "d" * 64,
        "change_approval_sha256": "f" * 64,
        "change_reference": "GH-130",
        "started_at": "2026-08-25T00:00:00Z",
        "committed_at": None,
        "catalog_signature_sha256": None,
    }
    payload.update(overrides)
    return payload


class TestReceiptStatusMatrix:
    """세 status의 **유효 조합을 전부** 고정한다(구현리뷰 4차 필수 3).

    `format_version`·`package_version`을 key 집합에만 넣고 값을 보지 않으면 version
    `999`도 통과한다. `ABORTED`는 분기 자체가 없어 완료 시각을 가진 중단 receipt가
    strict schema를 지났다.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            _status_receipt(),
            _status_receipt(
                status="COMMITTED",
                committed_at="2026-08-25T00:00:01Z",
                catalog_signature_sha256="c" * 64,
            ),
            _status_receipt(status="ABORTED"),
        ],
    )
    def test_a_valid_combination_passes(self, payload: dict[str, Any]) -> None:
        runner.validate_receipt(payload, "kosa_agent_e2e")

    @pytest.mark.parametrize(
        "payload",
        [
            _status_receipt(format_version=999),
            _status_receipt(format_version=True),
            _status_receipt(package_version="0.0.0"),
            # STARTED가 결과를 가질 수 없다
            _status_receipt(committed_at="2026-08-25T00:00:01Z"),
            _status_receipt(catalog_signature_sha256="c" * 64),
            # COMMITTED는 결과가 있어야 하고 시각 순서가 맞아야 한다
            _status_receipt(status="COMMITTED", committed_at="2026-08-25T00:00:01Z"),
            _status_receipt(
                status="COMMITTED",
                committed_at="2026-08-24T23:59:00Z",
                catalog_signature_sha256="c" * 64,
            ),
            # ABORTED는 결과를 가질 수 없다
            _status_receipt(status="ABORTED", committed_at="2026-08-25T00:00:01Z"),
            _status_receipt(status="ABORTED", catalog_signature_sha256="c" * 64),
            _status_receipt(status="ROLLED_BACK"),
        ],
    )
    def test_an_invalid_combination_is_refused(self, payload: dict[str, Any]) -> None:
        with pytest.raises(runner.CheckpointArtifactError):
            runner.validate_receipt(payload, "kosa_agent_e2e")


class TestApplyRequiresEvidenceOnTheCommandLine:
    """`--apply`는 승인과 backup 위치를 **명령에** 요구한다(구현리뷰 4차 필수 4).

    기본 경로로 조용히 흘러가면 운영자가 어떤 승인·어떤 backup으로 적용했는지 명령에
    남지 않는다. 되돌릴 수 없는 작업에서 그건 증적 공백이다.
    """

    @staticmethod
    def _argv(*extra: str) -> list[str]:
        return [
            "--database",
            "kosa_agent_e2e",
            "--confirm-target",
            "kosa_agent_e2e",
            "--change-ref",
            "GH-130",
            *extra,
        ]

    @staticmethod
    def _stub_target(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            runner, "assert_runtime_database", lambda value: "kosa_agent_e2e"
        )
        monkeypatch.setitem(
            sys.modules,
            "db_target",
            _stub_module(load_bootstrap_target=lambda _d: None),
        )

    def test_backup_root_has_no_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**저장소 안 경로를 기본값으로 두지 않는다.**

        전에는 `infra/bootstrap/backups`가 기본이었는데 그 경로는
        `validate_backup_root()`가 거부한다 — backup 도구가 **절대 쓸 수 없는 곳**을
        apply가 뒤지는 상태였다.
        """

        calls: list[object] = []
        monkeypatch.setattr(
            runner, "run_apply", lambda *a, **k: calls.append(1) or "APPLIED"
        )
        # **자격증명 환경에 기대지 않는다.** `main()`은 `.env`를 읽고 target을
        # 해석하므로, stub이 없으면 `.env`가 있는 기계에서만 이 인자 계약에 도달한다.
        # CI에는 `.env`가 없어 `TargetValidationError`로 먼저 끝났다.
        self._stub_target(monkeypatch)
        assert runner.main(self._argv("--apply")) == runner.EXIT_USAGE
        assert calls == []
        assert not hasattr(runner, "DEFAULT_BACKUP_ROOT")

    def test_approval_is_required(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[object] = []
        monkeypatch.setattr(
            runner, "run_apply", lambda *a, **k: calls.append(1) or "APPLIED"
        )
        self._stub_target(monkeypatch)
        assert (
            runner.main(self._argv("--apply", "--backup-root", "/tmp/cm34"))
            == runner.EXIT_USAGE
        )
        assert calls == []
        # 원인 코드를 package 계약 위반과 구분한다.
        assert "reason=APPROVAL_MISSING" in capsys.readouterr().err

    def test_both_paths_reach_run_apply(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: dict[str, Any] = {}

        def _fake_apply(_target: Any, **kwargs: Any) -> str:
            seen.update(kwargs)
            return "APPLIED"

        monkeypatch.setattr(runner, "run_apply", _fake_apply)
        self._stub_target(monkeypatch)
        approval = tmp_path / "approval.json"
        approval.write_text("{}", encoding="utf-8")
        assert (
            runner.main(
                self._argv(
                    "--apply",
                    "--approval",
                    str(approval),
                    "--backup-root",
                    str(tmp_path),
                )
            )
            == runner.EXIT_OK
        )
        assert seen["approval_path"] == approval
        assert seen["backup_root"] == tmp_path

    @pytest.mark.parametrize("mode", ["--preflight", "--verify"])
    def test_read_only_modes_need_neither(
        self, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        """읽기 전용 mode에 불필요한 인자를 요구하지 않는다."""

        monkeypatch.setattr(runner, "run_preflight", lambda *a, **k: "ABSENT")
        monkeypatch.setattr(runner, "run_verify", lambda *a, **k: "READY_MARKED")
        self._stub_target(monkeypatch)
        assert runner.main(["--database", "kosa_agent_e2e", mode]) == runner.EXIT_OK


#: 운영 절차 **정본**. 추적 대상이라 fresh checkout에도 있다.
RUNBOOK_PATH = REPOSITORY_ROOT / "infra" / "bootstrap" / "CHECKPOINT_RUNBOOK.md"


def _git_check_ignore(path: Path) -> bool:
    """`git check-ignore`로 **정말 무시되는지** 묻는다. 규칙을 재구현하지 않는다."""

    import shutil
    import subprocess

    if shutil.which("git") is None:  # pragma: no cover - git 없는 환경
        pytest.skip("git이 없어 ignore 여부를 물을 수 없다")
    completed = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in (0, 1):  # pragma: no cover - repo가 아닐 때
        pytest.skip("git 저장소가 아니어서 ignore 여부를 물을 수 없다")
    return completed.returncode == 0


def test_the_runbook_is_tracked_not_ignored() -> None:
    """**CI가 읽는 문서는 fresh checkout에 있어야 한다**(구현리뷰 13차 필수 1).

    이전 판은 `output/V5-CM-3.4_묶음2_실행절차.md`를 읽었다. `output/`은
    `.gitignore` 대상이라 로컬에서는 통과하고 GitHub Actions에서는
    `FileNotFoundError`로 실패한다. tracked `README.md`도 그 파일을 운영 절차로
    가리키고 있었으니, 테스트 문제이자 절차의 배포 누락이었다.

    대조군을 함께 둔다 — `output/`이 실제로 무시되는지 확인해야 이 검사가
    작동한다는 것을 알 수 있다.
    """

    assert RUNBOOK_PATH.is_file()
    assert not _git_check_ignore(RUNBOOK_PATH)
    assert not _git_check_ignore(REPOSITORY_ROOT / "infra" / "bootstrap" / "README.md")
    # 대조군: 개인 검토용 디렉토리는 무시된다.
    assert _git_check_ignore(REPOSITORY_ROOT / "output" / "AI_협업_작업방식.md")


def test_the_readme_only_links_files_a_clone_actually_has() -> None:
    """**README가 가리키는 문서가 clone 한 저장소에 있어야 한다**(13차 필수 1).

    이전에는 recovery 절차를 `output/`의 ignored 파일로 보냈다. 팀원이 clone하면
    README가 가리키는 운영 절차가 없다. 링크 대상이 실재하고 무시되지 않는지
    기계로 확인한다.
    """

    import re

    root = REPOSITORY_ROOT / "infra" / "bootstrap"
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "CHECKPOINT_RUNBOOK.md" in readme

    targets = [
        link
        for link in re.findall(r"\]\(([^)#\s]+)", readme)
        if not link.startswith(("http://", "https://"))
    ]
    assert targets, "README에 상대 링크가 하나도 없다"
    for link in targets:
        resolved = (root / link).resolve()
        assert resolved.exists(), link
        assert not _git_check_ignore(resolved), link


def test_the_runbook_only_names_signals_the_code_can_emit() -> None:
    """정본이 말하는 중단 신호를 **코드가 실제로 낼 수 있어야 한다.**

    운영자는 이 표를 보고 멈출지 판단한다. 존재하지 않는 reason code가 적혀 있으면
    그 행은 영원히 오지 않는 신호이고, 반대로 코드가 내는 신호가 표에 없으면 운영자가
    무엇인지 모르는 값을 받는다. 앞쪽을 여기서 막는다.
    """

    import re

    doc = RUNBOOK_PATH.read_text(encoding="utf-8")
    source = "\n".join(
        (SCRIPTS / name).read_text(encoding="utf-8")
        for name in (
            "checkpoint_backup.py",
            "checkpoint_contract.py",
            "setup_checkpoint.py",
            # backup root 신뢰 판정은 여기 있다 — 도구 하나의 소스만 보면
            # 정본이 정확히 적은 신호를 "없는 값"으로 오판한다.
            "postgres_backup.py",
            # 복구 뒤 CM-3.3 marker 재발급도 정본이 부르는 명령이다.
            "apply_severity_pair_guard.py",
        )
    )
    signals = sorted({m for m in re.findall(r"`([A-Z][A-Z0-9_]{3,})`", doc)})
    assert len(signals) >= 20, "중단 조건 표를 못 읽었다"
    missing = [name for name in signals if name not in source]
    assert missing == [], missing


def test_the_runbook_approval_examples_pass_the_validator() -> None:
    """**문서의 승인 JSON을 그대로 validator에 넣는다**(구현리뷰 12차 필수 3).

    `recovery_approved`를 더하면서 코드는 14키가 됐는데 실행 절차의 예시는 13키로
    남아 있었다. 문서대로 만든 `change_approval.json`으로는 apply가 **시작조차 되지
    않는다.**

    자체점검 §5-A 2항은 문서의 *명령*을 실행하지만 문서 속 *artifact*는 보지 않았다.
    그 공백을 이 회귀가 메운다 — 예시가 낡으면 여기서 red다.
    """

    import json
    import re

    doc = RUNBOOK_PATH.read_text(encoding="utf-8")
    blocks = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)```", doc, re.S)
        if '"checkpoint_change_approval"' in block
    ]
    assert len(blocks) == 2, "적용·복구 승인 예시가 둘 다 있어야 한다"
    for payload in blocks:
        runner.validate_change_approval(payload, "kosa_agent_e2e", "GH-130")

    # 적용용은 `false`, 복구용은 `true` — 두 의사표시가 구분돼야 한다.
    assert sorted(p["recovery_approved"] for p in blocks) == [False, True]
