from __future__ import annotations

import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_postgres_role_matrix as runner  # noqa: E402
import db_target  # noqa: E402
import postgres_role_matrix as matrix  # noqa: E402


def _contract(
    database: str = "kosa_agent",
    matrix_stage: matrix.MatrixStage = matrix.MatrixStage.CORE,
) -> matrix.RoleMatrixContract:
    schema_stage = (
        matrix.SchemaStage.EVALUATION_REFERENCE
        if database == "kosa_text2sql"
        else matrix.SchemaStage.RUNTIME_CHECKPOINTED
    )
    return matrix.build_contract(database, matrix_stage, schema_stage)


def _privilege_rows(
    contract: matrix.RoleMatrixContract,
    category: str,
    objects: list[str],
    privileges: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "role_name": role.value,
            "object_name": object_name,
            "privilege_name": privilege,
            "allowed": runner._expected_allowed(
                contract, category, role, object_name, privilege
            ),
        }
        for role in matrix.MANAGED_ROLES
        for object_name in objects
        for privilege in privileges
    ]


def _exact_snapshot(contract: matrix.RoleMatrixContract) -> dict[str, Any]:
    table_columns = {
        name: list(columns)
        for name, columns in matrix.expected_table_columns(
            contract.profile, contract.schema_stage
        ).items()
    }
    table_columns["v_alarm_event"] = ["alarm_id"]
    column_rows = []
    for role in matrix.MANAGED_ROLES:
        for relation, columns in table_columns.items():
            for column in columns:
                for privilege in matrix.COLUMN_PRIVILEGES:
                    column_rows.append(
                        {
                            "role_name": role.value,
                            "object_name": relation,
                            "column_name": column,
                            "privilege_name": privilege,
                            "allowed": runner._expected_allowed(
                                contract,
                                "column",
                                role,
                                relation,
                                privilege,
                                column=column,
                            ),
                        }
                    )
    return {
        "database": contract.database,
        "tables": sorted(contract.inventory.tables),
        "views": sorted(contract.inventory.views),
        "sequences": sorted(contract.inventory.sequences),
        "relation_columns": table_columns,
        "roles": [
            {
                "role_name": role.value,
                "login": matrix.ROLE_SPECS[role].login,
                "superuser": False,
                "createdb": False,
                "createrole": False,
                "replication": False,
                "bypassrls": False,
            }
            for role in matrix.MANAGED_ROLES
        ],
        "memberships": [],
        "role_settings": [
            {"role_name": role.value, "setting": setting}
            for role in (matrix.ManagedRole.READONLY, matrix.ManagedRole.EVALUATION)
            for setting in (
                "default_transaction_read_only=on",
                "statement_timeout=5s",
            )
        ],
        "managed_owners": [],
        "unmanageable_owners": [],
        "public_acl": [],
        "default_acl": [],
        "database_privileges": _privilege_rows(
            contract,
            "database",
            [contract.database],
            matrix.DATABASE_PRIVILEGES,
        ),
        "schema_privileges": _privilege_rows(
            contract, "schema", ["public"], matrix.SCHEMA_PRIVILEGES
        ),
        "relation_privileges": _privilege_rows(
            contract,
            "relation",
            sorted(contract.inventory.relations),
            matrix.RELATION_PRIVILEGES,
        ),
        "column_privileges": column_rows,
        "sequence_privileges": _privilege_rows(
            contract,
            "sequence",
            sorted(contract.inventory.sequences),
            matrix.SEQUENCE_PRIVILEGES,
        ),
        "row_counts": {name: 0 for name in contract.inventory.tables},
        "content_hashes": {name: "0" * 64 for name in contract.inventory.tables},
    }


@pytest.mark.parametrize(
    ("database", "stage", "table_count"),
    [
        ("kosa_agent", matrix.MatrixStage.CORE, 26),
        ("kosa_agent_e2e", matrix.MatrixStage.CHECKPOINTED, 26),
        ("kosa_text2sql", matrix.MatrixStage.CORE, 13),
    ],
)
def test_contract_inventory_and_digest_are_stable(
    database: str, stage: matrix.MatrixStage, table_count: int
) -> None:
    contract = _contract(database, stage)

    assert len(contract.inventory.tables) == table_count
    assert matrix.contract_digest(contract) == matrix.contract_digest(
        _contract(database, stage)
    )
    assert len(matrix.contract_digest(contract)) == 64


def test_runtime_core_then_checkpoint_is_exact_successor() -> None:
    core = _contract()
    checkpoint = _contract(matrix_stage=matrix.MatrixStage.CHECKPOINTED)

    for name in matrix.CHECKPOINT_CATALOG | matrix.CHECKPOINT_OPERATIONAL:
        assert core.relation_privileges[name][matrix.ManagedRole.APP] == frozenset()
    assert checkpoint.relation_privileges["checkpoint_migrations"][
        matrix.ManagedRole.APP
    ] == frozenset({"SELECT"})
    for name in matrix.CHECKPOINT_OPERATIONAL:
        assert checkpoint.relation_privileges[name][
            matrix.ManagedRole.APP
        ] == frozenset({"SELECT", "INSERT", "UPDATE"})
    assert matrix.contract_digest(core) != matrix.contract_digest(checkpoint)


def test_sensitive_columns_and_query_log_are_profile_scoped() -> None:
    runtime = _contract()
    evaluation = _contract("kosa_text2sql")

    for role in (matrix.ManagedRole.APP, matrix.ManagedRole.READONLY):
        assert runtime.relation_privileges["lot_history"][role] == frozenset()
        assert "fault_code" not in runtime.column_privileges["lot_history"]
    for role in matrix.MANAGED_ROLES:
        assert runtime.relation_privileges["nl_query_log"][role] == frozenset()
    assert evaluation.relation_privileges["nl_query_log"][
        matrix.ManagedRole.READONLY
    ] == frozenset({"SELECT"})
    assert evaluation.relation_privileges["nl_query_log"][
        matrix.ManagedRole.LOGGER
    ] == frozenset({"INSERT"})
    assert evaluation.column_privileges["nl_query_log"]["nl_query_log_id"][
        matrix.ManagedRole.LOGGER
    ] == frozenset({"SELECT"})


def test_exact_snapshot_and_mutations_are_detected() -> None:
    contract = _contract()
    snapshot = _exact_snapshot(contract)

    assert runner.inspect_snapshot(snapshot, contract).exact

    snapshot["public_acl"] = [
        {
            "object_type": "schema",
            "object_name": "public",
            "privilege_type": "USAGE",
        }
    ]
    assert (
        "PUBLIC_PRIVILEGE_PRESENT" in runner.inspect_snapshot(snapshot, contract).delta
    )


def test_inventory_membership_owner_and_role_elevation_fail_closed() -> None:
    contract = _contract()
    snapshot = _exact_snapshot(contract)
    snapshot["tables"] = snapshot["tables"][:-1]
    snapshot["memberships"] = [{"member": "kosa_app", "granted_role": "writer"}]
    snapshot["roles"][0]["superuser"] = True

    inspection = runner.inspect_snapshot(snapshot, contract)

    assert "TABLE_INVENTORY_MISMATCH" in inspection.unsafe
    assert "ROLE_MEMBERSHIP_PRESENT" in inspection.unsafe
    assert any(item.startswith("ROLE_ELEVATED:") for item in inspection.unsafe)


def test_manifest_column_drift_is_unsafe() -> None:
    contract = _contract()
    snapshot = _exact_snapshot(contract)
    snapshot["relation_columns"]["dim_parameter"].append("unexpected")

    assert (
        "TABLE_COLUMN_INVENTORY_MISMATCH"
        in runner.inspect_snapshot(snapshot, contract).unsafe
    )


def test_unknown_target_and_invalid_stage_are_rejected() -> None:
    with pytest.raises(matrix.ContractError, match="allowlist"):
        matrix.build_contract(
            "other", matrix.MatrixStage.CORE, matrix.SchemaStage.RUNTIME_GUARDED
        )
    with pytest.raises(matrix.ContractError, match="checkpoint"):
        matrix.build_contract(
            "kosa_text2sql",
            matrix.MatrixStage.CHECKPOINTED,
            matrix.SchemaStage.EVALUATION_REFERENCE,
        )


def test_preview_payload_contains_no_secret_material() -> None:
    contract = _contract()
    payload: Mapping[str, Any] = matrix.contract_payload(contract)
    rendered = str(payload).lower()

    assert "password" not in rendered
    assert "postgresql://" not in rendered
    assert "kosa_app" in rendered


def test_legacy_role_sql_is_fail_closed() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations/002_analytics_roles.sql"
    body = path.read_text(encoding="utf-8")

    assert "RETIRED_PIPELINE" in body
    assert "RAISE EXCEPTION" in body
    assert "GRANT SELECT ON ALL TABLES" not in body
    assert "PASSWORD '" not in body


def test_backend_runtime_has_no_admin_credential_fallback() -> None:
    root = Path(__file__).resolve().parents[2] / "app/common"
    config = (root / "config.py").read_text(encoding="utf-8")
    db = (root / "db.py").read_text(encoding="utf-8")

    assert 'get_env("POSTGRES_USER")' not in config
    assert 'get_env("POSTGRES_PASSWORD")' not in config
    assert "POSTGRES_USER" not in db
    assert "POSTGRES_PASSWORD" not in db
    assert "create_app_postgres_url()" in db


def test_app_database_credential_is_checked_on_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.common import db

    db.dispose_engines()
    monkeypatch.setattr(db, "APP_DATABASE_URL", None)
    monkeypatch.setattr(db, "APP_DB_PASSWORD", None)

    with pytest.raises(RuntimeError, match="APP_DB_PASSWORD"):
        db.get_app_engine()


def test_marker_binds_target_contract_and_predecessors(tmp_path: Path) -> None:
    contract = _contract()
    source = Path(__file__).resolve().parents[3] / "infra/bootstrap/markers"
    for prefix in ("agent_severity_guard_final", "checkpoint_setup_final"):
        shutil.copy2(
            source / f"{prefix}.kosa_agent.json",
            tmp_path / f"{prefix}.kosa_agent.json",
        )
    target = db_target.BootstrapTarget(
        host="db.example.invalid",
        port=5432,
        username="bootstrap",
        password="not-persisted",
        database="kosa_agent",
        profile="runtime",
    )
    payload = runner._marker_payload(
        contract,
        target,
        "GH-999",
        runner.ZERO_SHA256,
        runner.ZERO_SHA256,
        "a" * 64,
        "RECOVERED",
        marker_root=tmp_path,
    )

    runner.validate_marker(payload, contract, target, tmp_path)

    forged = dict(payload)
    forged["matrix_digest_sha256"] = "f" * 64
    with pytest.raises(runner.RoleMatrixError, match="identity"):
        runner.validate_marker(forged, contract, target, tmp_path)

    (tmp_path / "checkpoint_setup_final.kosa_agent.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(runner.RoleMatrixError, match="identity"):
        runner.validate_marker(payload, contract, target, tmp_path)


def test_snapshot_rejects_repository_and_symlink_paths(tmp_path: Path) -> None:
    with pytest.raises(runner.RoleMatrixError, match="저장소 밖"):
        runner._validate_snapshot_path(
            Path(__file__).resolve().parents[3] / "snapshot.json"
        )

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(runner.RoleMatrixError, match="symlink"):
        runner._validate_snapshot_path(link / "snapshot.json")
