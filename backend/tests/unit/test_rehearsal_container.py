from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3  # noqa: E402
import rebuild_runner as runner  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402
import rehearsal_profile_verifier as verifier  # noqa: E402
import rehearsal_recovery as recovery  # noqa: E402
import rehearsal_schema as schema  # noqa: E402
import rehearse_recovery as recovery_cli  # noqa: E402
import rehearse_schema as wrapper  # noqa: E402
import value_normalization  # noqa: E402

pytestmark = pytest.mark.container


# 축소 acceptance fixture (계획 §6.3). 실제 최종 ZIP 24,845행 대신 같은 계약을
# 만족하는 최소 행으로 DB type·행 수·typed hash·PK·FK·reference·timestamp를 모두
# 실제 PostgreSQL transaction 안에서 통과시킨다.
_FixtureTable = tuple[str, tuple[tuple[str, str], ...], tuple[str, ...], tuple]

_TS = datetime(2026, 8, 18, 9, 30, 0)

FIXTURE_TABLES: tuple[_FixtureTable, ...] = (
    (
        "dim_parameter",
        (("parameter_id", "integer"), ("param_name", "text")),
        ("parameter_id",),
        ((1, "p1"), (2, "p2")),
    ),
    (
        "lot_history",
        (("lot_hist_id", "integer"), ("event_dtts", "timestamp")),
        ("lot_hist_id",),
        ((10, _TS), (11, _TS.replace(hour=10))),
    ),
    (
        # 실제 데이터에서 seq_no는 recipe_step_no별 전역 순번이다
        # (step 1 -> 0·1·2, step 2 -> 3·4·5, 각 2,400행). `V5-CM-2.4`는 step 경계를
        # 검증하지 않지만(A 영역 범위), DISTINCT 집합만은 실제와 같은 6종으로 둔다
        # (PR #96 코멘트 2).
        "fdc_trace",
        (
            ("trace_id", "integer"),
            ("lot_hist_id", "integer"),
            ("parameter_id", "integer"),
            ("recipe_step_no", "integer"),
            ("seq_no", "text"),
        ),
        ("trace_id",),
        (
            (1, 10, 1, 1, "0"),
            (2, 10, 1, 1, "1"),
            (3, 10, 2, 1, "2"),
            (4, 11, 2, 2, "3"),
            (5, 11, 1, 2, "4"),
            (6, 11, 2, 2, "5"),
        ),
    ),
    (
        "summary_data",
        (("summary_id", "integer"), ("lot_hist_id", "integer")),
        ("summary_id",),
        ((1, 10), (2, 11)),
    ),
    (
        "evaluation",
        (
            ("evaluation_id", "integer"),
            ("lot_hist_id", "integer"),
            ("alarm_type", "text"),
        ),
        ("evaluation_id",),
        ((1, 10, "IN"), (2, 10, "IN"), (3, 11, "OOC"), (4, 11, "OOS")),
    ),
    (
        "trace_alarm_history",
        (("trace_alarm_id", "integer"),),
        ("trace_alarm_id",),
        ((1,), (2,), (3,)),
    ),
    (
        "summary_alarm_history",
        (("summary_alarm_id", "integer"),),
        ("summary_alarm_id",),
        ((1,), (2,)),
    ),
    (
        "metrology",
        (("metrology_id", "integer"), ("lot_hist_id", "integer")),
        ("metrology_id",),
        ((1, 10), (2, 11)),
    ),
    (
        # loader 최소 postcheck가 evaluation profile action 수를 12로 못박는다.
        "action_history",
        (("action_id", "integer"),),
        ("action_id",),
        tuple((value,) for value in range(1, 13)),
    ),
)

# 위 행 분포를 그대로 옮긴 축소 reference. alarm 분포·행 수는 최종 상수
# (4538/216/46·138·51)와 다른 값을 주입해 gate가 상수 암기가 아님을 보이고,
# seq 집합만은 실제 계약과 같은 6종으로 맞춘다(PR #96 코멘트 2).
FIXTURE_REFERENCE = verifier.AcceptanceReference(
    evaluation_alarm_types=MappingProxyType({"IN": 2, "OOC": 1, "OOS": 1}),
    trace_alarm_rows=3,
    summary_alarm_rows=2,
    trace_seq_values=("0", "1", "2", "3", "4", "5"),
)

_PG_LOGICAL = {"integer": "numeric", "text": "text", "timestamp": "timestamp"}


def _fixture_sql() -> bytes:
    statements = []
    for name, columns, primary_key, _rows in FIXTURE_TABLES:
        body = ", ".join(f"{column} {pg_type}" for column, pg_type in columns)
        keys = ", ".join(primary_key)
        statements.append(f"CREATE TABLE {name} ({body}, PRIMARY KEY ({keys}));")
    statements.extend(
        [
            "CREATE INDEX ix_evaluation_type ON evaluation (alarm_type);",
            "CREATE INDEX ix_lot_history_cum ON lot_history (lot_hist_id);",
            "CREATE INDEX ix_summary_data_key ON summary_data (summary_id);",
            "CREATE INDEX ix_trace_alarm_time ON trace_alarm_history "
            "(trace_alarm_id);",
        ]
    )
    return ("\n".join(statements) + "\n").encode()


def _fixture_csv(columns: tuple[tuple[str, str], ...], rows: tuple) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([column for column, _ in columns])
    for row in rows:
        writer.writerow(
            [
                value.isoformat(sep=" ") if isinstance(value, datetime) else value
                for value in row
            ]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _fixture_entry(
    columns: tuple[tuple[str, str], ...], primary_key: tuple[str, ...], rows: tuple
) -> dict:
    column_types = {column: _PG_LOGICAL[pg_type] for column, pg_type in columns}
    names = [column for column, _ in columns]
    normalized = [
        value_normalization.normalize_db_row(
            dict(zip(names, row, strict=True)), column_types
        )
        for row in rows
    ]
    return {
        "columns": names,
        "column_types": column_types,
        "primary_key": list(primary_key),
        "row_count": len(rows),
        "content_hash": manifest_v3.hash_canonical_rows(normalized),
    }


def _fixture_artifacts(tmp_path: Path) -> tuple[Path, runner.ArtifactPaths]:
    sql = _fixture_sql()
    member = "project/repository/sample/schema/03_schema_clean.sql"
    archive_path = tmp_path / "fixture.zip"
    info = zipfile.ZipInfo(member, date_time=(2026, 8, 20, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    real_manifest = json.loads(runner.SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))

    entries = {
        name: _fixture_entry(columns, primary_key, rows)
        for name, columns, primary_key, rows in FIXTURE_TABLES
    }
    csv_members = {
        real_manifest["tables"][name]["file_id"]: _fixture_csv(columns, rows)
        for name, columns, _primary_key, rows in FIXTURE_TABLES
    }

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
    for name, entry in manifest["tables"].items():
        # file_id·included_by_profile은 실제 manifest 값을 그대로 둔다.
        entry.update(entries[name])
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
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, profile)
    assert len(snapshot.tables) == (8 if profile == "runtime" else 9)
    assert len(snapshot.acceptances) == 9
    handler, postcheck = wrapper._composite(
        snapshot, profile, reference=FIXTURE_REFERENCE
    )
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


# ---------------------------------------------------------------------------
# V5-CM-2.5 — 실제 transaction에서의 rollback · no-op · 복구
# ---------------------------------------------------------------------------


def _public_relations(endpoint: postgres.RehearsalEndpoint) -> int:
    with psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(recovery.RELATIONS_SQL)
            return len(cursor.fetchall())


def _table_rows(endpoint: postgres.RehearsalEndpoint, table: str) -> int:
    with psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM public."{table}"')
            row = cursor.fetchone()
            assert row is not None
            return int(row[0])


@pytest.mark.parametrize("profile", ["runtime", "evaluation"])
def test_recovery_rehearsal_on_real_postgres(tmp_path: Path, profile: str) -> None:
    """실패 rollback → apply → no-op → marker 유실 → 복구를 실제 DB로 확인한다."""

    archive, artifacts = _fixture_artifacts(tmp_path)
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    snapshot = wrapper._verified_archive_snapshot(archive, artifacts, profile)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    store = recovery.MarkerStore(marker_root, profile)
    database = f"fdc_rehearsal_{profile}"

    identity = recovery_cli._identity(
        snapshot,
        profile=profile,
        database=database,
        artifact_paths=artifacts,
        archive_sha=archive_sha,
    )

    def session(recover: bool = False, poison: object = None) -> object:
        return recovery_cli._session(
            snapshot,
            profile=profile,
            store=store,
            identity=identity,
            recover_artifact=recover,
            poison=poison,
            reference=FIXTURE_REFERENCE,
        )

    def invoke(current: Any, recover: bool = False) -> wrapper.RunnerOutcome:
        return recovery_cli._invoke(
            endpoint,
            current,
            profile=profile,
            artifact_paths=artifacts,
            recover_artifact=recover,
        )

    with postgres.one_off_postgres(database=database) as endpoint:
        # 1. 실패 주입 → 전체 rollback
        assert invoke(session(poison=recovery_cli._poison)) == wrapper.RunnerOutcome(
            1, "MODE_CONTRACT_ERROR"
        )
        assert _public_relations(endpoint) == 0
        assert store.markers() == []

        # 2. 최초 apply → commit → marker-last
        applied = session()
        assert invoke(applied) == wrapper.RunnerOutcome(0, None)
        assert applied.outcome is recovery.Outcome.APPLIED
        assert _public_relations(endpoint) > 0
        assert _table_rows(endpoint, "action_history") == (
            12 if profile == "evaluation" else 0
        )
        assert len(store.markers()) == 1
        first_marker = store.path.read_bytes()
        first_mtime = store.path.stat().st_mtime_ns

        # 3. 동일 apply → no-op, marker bytes·mtime 불변
        noop = session()
        assert invoke(noop) == wrapper.RunnerOutcome(0, None)
        assert noop.outcome is recovery.Outcome.NOOP
        assert store.path.read_bytes() == first_marker
        assert store.path.stat().st_mtime_ns == first_mtime

        # 4. valid marker에 복구 요청 → 거부
        assert invoke(session(recover=True), recover=True) == wrapper.RunnerOutcome(
            1, "RECOVERY_NOT_ALLOWED"
        )
        assert store.path.read_bytes() == first_marker

        # 5. marker 유실 → 일반 apply 거부
        store.path.unlink()
        assert invoke(session()) == wrapper.RunnerOutcome(1, "RECOVERY_REQUIRED")
        assert store.markers() == []

        # 6. 명시 복구 → marker만 byte-identical 복원
        recovered = session(recover=True)
        assert invoke(recovered, recover=True) == wrapper.RunnerOutcome(0, None)
        assert recovered.outcome is recovery.Outcome.RECOVER
        assert store.path.read_bytes() == first_marker

        # 7. DB cell 변조 → no-op도 복구도 거부
        with psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=endpoint.username,
            password=endpoint.password,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE public.metrology SET lot_hist_id = 99")
            connection.commit()
        assert invoke(session()) == wrapper.RunnerOutcome(1, "MODE_CONTRACT_ERROR")
        store.path.unlink()
        assert invoke(session(recover=True), recover=True) == wrapper.RunnerOutcome(
            1, "MODE_CONTRACT_ERROR"
        )
        assert store.markers() == []
