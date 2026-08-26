from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_postgres_role_matrix as runner  # noqa: E402
import db_target  # noqa: E402
import postgres_role_matrix as matrix  # noqa: E402

pytestmark = [
    pytest.mark.container,
    # 로컬 전체 pytest가 Docker container를 묵시적으로 기동하지 않게 opt-in으로 둔다.
    # PR CI의 전용 role-matrix step은 이 값을 1로 주므로 실제 회귀가 skip되지 않는다.
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_ROLE_MATRIX_CONTAINER") != "1",
        reason="set RUN_POSTGRES_ROLE_MATRIX_CONTAINER=1",
    ),
]

IMAGE = "pgvector/pgvector:pg16"
ADMIN_PASSWORD = "cm35_admin_pw"
ROLE_PASSWORDS = {
    "APP_DB_PASSWORD": "cm35_app_pw",
    "READONLY_PASSWORD": "cm35_readonly_pw",
    "EVALUATION_DB_PASSWORD": "cm35_evaluation_pw",
    "QUERY_LOGGER_PASSWORD": "cm35_logger_pw",
}
VECTOR_1024 = "[1," + ",".join("0" for _ in range(1023)) + "]"


def _docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip()


def _admin_dsn(port: int, database: str = "postgres") -> str:
    return f"postgresql://postgres:{ADMIN_PASSWORD}@127.0.0.1:{port}/{database}"


def _wait_ready(port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(_admin_dsn(port), connect_timeout=1):
                return
        except psycopg.Error:
            time.sleep(0.25)
    raise AssertionError("isolated PostgreSQL did not become ready")


def _create_profile_schema(port: int, contract: matrix.RoleMatrixContract) -> None:
    columns = matrix.expected_table_columns(contract.profile, contract.schema_stage)
    with psycopg.connect(_admin_dsn(port, contract.database), autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute("CREATE EXTENSION vector")
            for table in sorted(contract.inventory.tables):
                declarations = ", ".join(
                    f'"{name}" {"vector(1024)" if name == "embedding" else "text"}'
                    for name in columns[table]
                )
                cursor.execute(f'CREATE TABLE "{table}" ({declarations})')
            cursor.execute(
                "CREATE VIEW v_alarm_event AS "
                "SELECT NULL::text AS alarm_id WHERE false"
            )
            for sequence in sorted(contract.inventory.sequences):
                cursor.execute(f'CREATE SEQUENCE "{sequence}"')
            cursor.execute(
                "INSERT INTO document_chunk (embedding) VALUES (%s::vector)",
                (VECTOR_1024,),
            )


def _approval(path: Path, contract: matrix.RoleMatrixContract) -> None:
    payload = {
        "artifact_type": runner.APPROVAL_ARTIFACT_TYPE,
        "format_version": runner.APPROVAL_FORMAT_VERSION,
        "task_id": matrix.TASK_ID,
        "dataset_epoch": matrix.DATASET_EPOCH,
        "change_reference": "GH-999",
        "status": "APPROVED",
        "targets": [
            {
                "database": contract.database,
                "profile": contract.profile.value,
                "schema_stage": contract.schema_stage.value,
                "matrix_stage": contract.matrix_stage.value,
                "matrix_digest_sha256": matrix.contract_digest(contract),
            }
        ],
        "approved_at": "2026-08-26T00:00:00Z",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _args(
    contract: matrix.RoleMatrixContract,
    approval: Path,
    snapshot: Path,
) -> object:
    return runner._parser().parse_args(
        [
            "--database",
            contract.database,
            "--profile",
            contract.profile.value,
            "--schema-stage",
            contract.schema_stage.value,
            "--matrix-stage",
            contract.matrix_stage.value,
            "--confirm-target",
            contract.database,
            "--change-ref",
            "GH-999",
            "--approval",
            str(approval),
            "--snapshot-out",
            str(snapshot),
            "--apply",
        ]
    )


def _recover_args(
    contract: matrix.RoleMatrixContract,
    approval: Path,
    snapshot: Path,
) -> object:
    return runner._parser().parse_args(
        [
            "--database",
            contract.database,
            "--profile",
            contract.profile.value,
            "--schema-stage",
            contract.schema_stage.value,
            "--matrix-stage",
            contract.matrix_stage.value,
            "--change-ref",
            "GH-999",
            "--approval",
            str(approval),
            "--snapshot-out",
            str(snapshot),
            "--recover-marker",
        ]
    )


def _connect(
    port: int, database: str, role: str, password: str
) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(
        f"postgresql://{role}:{password}@127.0.0.1:{port}/{database}",
        autocommit=True,
        connect_timeout=3,
    )


def _assert_denied(
    port: int,
    database: str,
    role: str,
    password: str,
    statement: str,
    *,
    override_read_only: bool = False,
) -> None:
    with _connect(port, database, role, password) as conn:
        if override_read_only:
            conn.execute("SET default_transaction_read_only=off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as caught:
            conn.execute(statement)
    assert caught.value.sqlstate == "42501"


def test_role_matrix_real_login_core_checkpoint_and_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"cm35-{uuid.uuid4().hex[:10]}"
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    source_markers = Path(__file__).resolve().parents[3] / "infra/bootstrap/markers"
    for database in ("kosa_agent", "kosa_agent_e2e"):
        for prefix in ("agent_severity_guard_final", "checkpoint_setup_final"):
            shutil.copy2(
                source_markers / f"{prefix}.{database}.json",
                marker_root / f"{prefix}.{database}.json",
            )

    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        name,
        "-e",
        f"POSTGRES_PASSWORD={ADMIN_PASSWORD}",
        "-p",
        "127.0.0.1::5432",
        IMAGE,
    )
    try:
        port_text = _docker("port", name, "5432/tcp").rsplit(":", 1)[1]
        port = int(port_text)
        _wait_ready(port)
        with psycopg.connect(_admin_dsn(port), autocommit=True) as conn:
            for database in matrix.DATABASE_PROFILES:
                conn.execute(f'CREATE DATABASE "{database}"')

        runtime_core = matrix.build_contract(
            "kosa_agent_e2e",
            matrix.MatrixStage.CORE,
            matrix.SchemaStage.RUNTIME_CHECKPOINTED,
        )
        runtime_checkpoint = matrix.build_contract(
            "kosa_agent_e2e",
            matrix.MatrixStage.CHECKPOINTED,
            matrix.SchemaStage.RUNTIME_CHECKPOINTED,
        )
        runtime = matrix.build_contract(
            "kosa_agent",
            matrix.MatrixStage.CORE,
            matrix.SchemaStage.RUNTIME_CHECKPOINTED,
        )
        runtime_checkpoint_2 = matrix.build_contract(
            "kosa_agent",
            matrix.MatrixStage.CHECKPOINTED,
            matrix.SchemaStage.RUNTIME_CHECKPOINTED,
        )
        evaluation = matrix.build_contract(
            "kosa_text2sql",
            matrix.MatrixStage.CORE,
            matrix.SchemaStage.EVALUATION_REFERENCE,
        )
        for contract in (runtime_core, runtime, evaluation):
            _create_profile_schema(port, contract)

        environ = {
            "POSTGRES_BOOTSTRAP_HOST": "127.0.0.1",
            "POSTGRES_BOOTSTRAP_PORT": str(port),
            "POSTGRES_BOOTSTRAP_USER": "postgres",
            "POSTGRES_BOOTSTRAP_PASSWORD": ADMIN_PASSWORD,
            "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": db_target.host_fingerprint(
                "127.0.0.1", port
            ),
            **ROLE_PASSWORDS,
        }
        monkeypatch.setattr(
            runner.db_target,
            "load_bootstrap_target",
            lambda database, environ=None: db_target.BootstrapTarget(
                host="127.0.0.1",
                port=port,
                username="postgres",
                password=ADMIN_PASSWORD,
                database=database,
                profile=matrix.DATABASE_PROFILES[database].value,
            ),
        )

        for index, contract in enumerate(
            (
                runtime_core,
                runtime_checkpoint,
                runtime,
                runtime_checkpoint_2,
                evaluation,
            ),
            start=1,
        ):
            approval = tmp_path / f"approval-{index}.json"
            snapshot = tmp_path / f"snapshot-{index}.json"
            _approval(approval, contract)
            code, result = runner.run(
                _args(contract, approval, snapshot),
                environ=environ,
                marker_root=marker_root,
            )
            assert (code, result["status"]) == (0, "APPLIED")
            if index == 1:
                role_marker = runner.marker_path(contract, marker_root)
                marker_bytes = role_marker.read_bytes()
                marker_mtime = role_marker.stat().st_mtime_ns
                unused = tmp_path / "same-stage-unused.json"
                code, result = runner.run(
                    _args(contract, approval, unused),
                    environ=environ,
                    marker_root=marker_root,
                )
                assert (code, result["status"]) == (0, "NO_OP")
                assert not unused.exists()
                assert role_marker.read_bytes() == marker_bytes
                assert role_marker.stat().st_mtime_ns == marker_mtime
                role_marker.unlink()
                code, result = runner.run(
                    _recover_args(contract, approval, snapshot),
                    environ=environ,
                    marker_root=marker_root,
                )
                assert (code, result["status"]) == (0, "RECOVERED")
                recovered = json.loads(role_marker.read_text(encoding="utf-8"))
                assert recovered["approval_sha256"] != runner.ZERO_SHA256
                assert recovered["snapshot_sha256"] != runner.ZERO_SHA256

        core_marker = runner.marker_path(runtime_core, marker_root)
        before_bytes = core_marker.read_bytes()
        before_mtime = core_marker.stat().st_mtime_ns
        code, result = runner.run(
            _args(
                runtime_core,
                tmp_path / "approval-1.json",
                tmp_path / "unused-existing-snapshot.json",
            ),
            environ=environ,
            marker_root=marker_root,
        )
        assert (code, result["status"]) == (0, "SUPERSEDED_NO_OP")
        assert core_marker.read_bytes() == before_bytes
        assert core_marker.stat().st_mtime_ns == before_mtime

        with _connect(
            port,
            "kosa_agent_e2e",
            "kosa_app",
            ROLE_PASSWORDS["APP_DB_PASSWORD"],
        ) as conn:
            conn.execute("SELECT parameter_id FROM dim_parameter")
            conn.execute("SELECT doc_id FROM document")
            conn.execute(
                "SELECT embedding <=> %s::vector FROM document_chunk",
                (VECTOR_1024,),
            )
            conn.execute("SELECT * FROM checkpoints")
            conn.execute("INSERT INTO agent_run (agent_run_id) VALUES ('run-1')")
            conn.execute(
                "UPDATE agent_run SET agent_run_id='run-2' "
                "WHERE agent_run_id='run-1'"
            )
            conn.execute("INSERT INTO audit_log (audit_id) VALUES ('audit-1')")
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_app",
            ROLE_PASSWORDS["APP_DB_PASSWORD"],
            "SELECT fault_code FROM lot_history",
        )
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_app",
            ROLE_PASSWORDS["APP_DB_PASSWORD"],
            "SELECT * FROM metrology",
        )
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_app",
            ROLE_PASSWORDS["APP_DB_PASSWORD"],
            "DELETE FROM agent_run",
        )
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_app",
            ROLE_PASSWORDS["APP_DB_PASSWORD"],
            "UPDATE audit_log SET audit_id='audit-2'",
        )
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_app",
            ROLE_PASSWORDS["APP_DB_PASSWORD"],
            "CREATE TABLE forbidden_table (id integer)",
        )
        with _connect(
            port,
            "kosa_agent_e2e",
            "kosa_readonly",
            ROLE_PASSWORDS["READONLY_PASSWORD"],
        ) as conn:
            conn.execute("SELECT lot_id FROM lot_history")
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_readonly",
            ROLE_PASSWORDS["READONLY_PASSWORD"],
            "SELECT * FROM agent_run",
        )
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_readonly",
            ROLE_PASSWORDS["READONLY_PASSWORD"],
            "SELECT * FROM nl_query_log",
        )
        _assert_denied(
            port,
            "kosa_agent_e2e",
            "kosa_readonly",
            ROLE_PASSWORDS["READONLY_PASSWORD"],
            "INSERT INTO action_history (action_id) VALUES ('action-1')",
            override_read_only=True,
        )
        with _connect(
            port,
            "kosa_text2sql",
            "kosa_evaluation",
            ROLE_PASSWORDS["EVALUATION_DB_PASSWORD"],
        ) as conn:
            conn.execute("SELECT fault_code FROM lot_history")
        with _connect(
            port,
            "kosa_text2sql",
            "kosa_query_logger",
            ROLE_PASSWORDS["QUERY_LOGGER_PASSWORD"],
        ) as conn:
            conn.execute(
                "INSERT INTO nl_query_log (nl_query_log_id) VALUES ('test-id') "
                "RETURNING nl_query_log_id"
            )
            conn.execute("SELECT nextval('nl_query_log_nl_query_log_id_seq')")
        _assert_denied(
            port,
            "kosa_text2sql",
            "kosa_query_logger",
            ROLE_PASSWORDS["QUERY_LOGGER_PASSWORD"],
            "SELECT * FROM dim_parameter",
        )

        with pytest.raises(psycopg.OperationalError):
            _connect(
                port,
                "kosa_text2sql",
                "kosa_app",
                ROLE_PASSWORDS["APP_DB_PASSWORD"],
            )
        with pytest.raises(psycopg.OperationalError):
            _connect(port, "kosa_agent", "kosa_n8n_delivery", "unused")
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
