from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rebuild_runner as runner  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402
import rehearsal_schema as schema  # noqa: E402
import rehearse_schema as wrapper  # noqa: E402

pytestmark = pytest.mark.container


def _fixture_sql() -> bytes:
    statements = [
        f"CREATE TABLE {table} (id integer PRIMARY KEY);"
        for table in sorted(schema.EXPECTED_TABLES)
    ]
    statements.extend(
        [
            "CREATE INDEX ix_evaluation_type ON evaluation (id);",
            "CREATE INDEX ix_lot_history_cum ON lot_history (id);",
            "CREATE INDEX ix_summary_data_key ON summary_data (id);",
            "CREATE INDEX ix_trace_alarm_time ON trace_alarm_history (id);",
        ]
    )
    return ("\n".join(statements) + "\n").encode()


def _fixture_artifacts(tmp_path: Path) -> tuple[Path, runner.ArtifactPaths]:
    sql = _fixture_sql()
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


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_schema_rehearsal_postcheck_rollback_and_cleanup(
    tmp_path: Path, profile: str
) -> None:
    archive, artifacts = _fixture_artifacts(tmp_path)
    schema_bytes = wrapper._verified_schema_bytes(archive, artifacts)
    handler, postcheck = schema.make_handlers(schema_bytes, runner.RunnerError)
    database = f"fdc_rehearsal_{profile}"
    container_id = ""
    volume_ids: tuple[str, ...] = ()

    with postgres.one_off_postgres(database=database) as endpoint:
        container_id = endpoint.container_id
        volume_ids = postgres._volume_ids(container_id, runner=subprocess.run)
        outcome = wrapper._call_runner(
            endpoint,
            profile=profile,
            artifact_paths=artifacts,
            handler=handler,
            postcheck=postcheck,
        )
        assert outcome == wrapper.RunnerOutcome(0, None)
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=endpoint.username,
            password=endpoint.password,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r','p','v','m','S','f','i','I')
                    """
                )
                assert cursor.fetchone() == (0,)

    inspected = subprocess.run(
        ["docker", "inspect", container_id],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert inspected.returncode != 0
    for volume_id in volume_ids:
        volume = subprocess.run(
            ["docker", "volume", "inspect", volume_id],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert volume.returncode != 0


def test_real_postgres_rejects_non_fresh_public_schema(tmp_path: Path) -> None:
    _, artifacts = _fixture_artifacts(tmp_path)
    handler, postcheck = schema.make_handlers(_fixture_sql(), runner.RunnerError)
    with postgres.one_off_postgres(database="fdc_rehearsal_runtime") as endpoint:
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=endpoint.username,
            password=endpoint.password,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE preexisting_object(id integer)")

        outcome = wrapper._call_runner(
            endpoint,
            profile="runtime",
            artifact_paths=artifacts,
            handler=handler,
            postcheck=postcheck,
        )
        assert outcome == wrapper.RunnerOutcome(1, "TARGET_NOT_FRESH")
