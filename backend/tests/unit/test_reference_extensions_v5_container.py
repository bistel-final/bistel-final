"""`V5-CM-3.1` 묶음 2를 실제 PostgreSQL 16에서 실증한다.

계획 §9.2. 공용 DB에는 접근하지 않는다 — 격리 container에 CM-2.6 완료 형상과 base-only
형상을 각각 세우고 두 route를 돌린다.

legacy 형상은 CM-2.6이 만든 `tests/fixtures/v5_cm_2_6/` vendored fixture를 그대로 쓴다.
폐기된 `kosa_0813` 패키지는 입력으로 쓰지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_reference_extensions_v5 as v5  # noqa: E402
import postgres_transition as transition  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402

pytestmark = pytest.mark.container

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v5_cm_2_6"
WAFER_ALTER = "\n".join(
    f"ALTER TABLE {table} ALTER COLUMN wafer TYPE varchar(24) "
    f"USING wafer::varchar(24);"
    for table in transition.WAFER_ALTER_TABLES
)


def _execute(cursor: Any) -> Any:
    """module이 driver를 고르지 않는다 — `(sql, params) -> rows` 하나만 넘긴다."""

    def run(sql: str, params: Any = None) -> list[dict[str, Any]]:
        cursor.execute(sql, params)
        if cursor.description is None:
            return []
        columns = [column.name for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    return run


def _build_base_only(cursor: Any) -> None:
    cursor.execute((FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8"))
    cursor.execute(WAFER_ALTER)


def _build_cm26_state(cursor: Any) -> None:
    """CM-2.6 완료 직후의 공용 형상 — final base 9 + 구 R03 + 호환 View."""

    cursor.execute((FIXTURES / "legacy_base_schema.sql").read_text(encoding="utf-8"))
    cursor.execute((FIXTURES / "legacy_reference.sql").read_text(encoding="utf-8"))
    cursor.execute(v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW})
    legacy = cursor.fetchone()[0]
    cursor.execute(f"DROP VIEW {v5.ALARM_VIEW}")
    cursor.execute(WAFER_ALTER)
    cursor.execute(transition.build_compatibility_view_sql(legacy))


def _build_preserved(cursor: Any, profile: str) -> None:
    """공용 inventory의 보존 table을 최소 형상으로 세운다.

    격리 fixture에는 Runtime·RAG·D 객체가 없다. runner가 이들을 `SHARE`로 잡고 전후
    fingerprint를 대조하므로 **존재 자체**를 재현해야 한다. 내용은 보지 않는다.
    """

    for name in v5.PRESERVED_TABLES_BY_PROFILE[profile]:
        if name == v5.R03_TABLE:
            continue
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS public.{name} "
            "(stub_id integer PRIMARY KEY, note text)"
        )


FINAL_ARCHIVE_ENV_KEY = "MENTOR_FINAL_ARCHIVE"


def _final_archive() -> Path:
    """최종 ZIP을 찾는다. **지정됐는데 없거나 hash가 다르면 skip이 아니라 실패다.**

    CM-2.6의 3-target E2E와 같은 gate다. 이 archive가 없으면 189/192 exact를 실증할 수
    없다 — CM-2.6 fixture는 행 수 재현용이라 legacy 값(126/47)을 담고 있다.
    """

    import os

    import transition_sessions as ts

    declared = os.environ.get(FINAL_ARCHIVE_ENV_KEY, "").strip()
    if not declared:
        pytest.skip(f"{FINAL_ARCHIVE_ENV_KEY}가 없다")
    path = Path(declared).expanduser()
    if not path.is_file():
        pytest.fail(f"{FINAL_ARCHIVE_ENV_KEY}가 가리키는 파일이 없다")
    ts.assert_archive_is_pinned(path)
    return path


def _load_final_dataset(cursor: Any, profile: str) -> None:
    """최종 ZIP의 9 CSV를 base 9에 그대로 적재한다. **완화 flag가 필요 없어진다.**"""

    import csv
    import io

    import transition_sessions as ts

    snapshots = ts.load_profile_snapshots(_final_archive())
    snapshot = snapshots[profile]
    for table in snapshot.tables:
        columns = snapshot.columns_by_table[table]
        body = snapshot.csv_bodies[table].decode("utf-8")
        rows = list(csv.reader(io.StringIO(body)))[1:]
        if not rows:
            continue
        placeholders = ", ".join(["%s"] * len(columns))
        names = ", ".join(f'"{c}"' for c in columns)
        cursor.executemany(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            [[None if v == "" else v for v in row] for row in rows],
        )


def _load_rows(cursor: Any) -> None:
    cursor.execute((FIXTURES / "legacy_rows.sql").read_text(encoding="utf-8"))


def _counts(execute: Any) -> tuple[int, int, int]:
    view_rows = execute(f"SELECT count(*) AS n FROM {v5.ALARM_VIEW}")[0]["n"]
    r03_rows = execute(f"SELECT count(*) AS n FROM {v5.R03_TABLE}")[0]["n"]
    action_rows = execute("SELECT count(*) AS n FROM action_history")[0]["n"]
    return view_rows, r03_rows, action_rows


def _run_plan(execute: Any, plan: tuple[str, ...]) -> None:
    for statement in plan:
        if statement == v5.ISOLATION_SQL:
            assert execute(statement)[0]["transaction_isolation"] == (
                v5.EXPECTED_ISOLATION
            )
            continue
        execute(statement)


def _session(endpoint: Any, database: str) -> Any:
    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=database,
        user=endpoint.username,
        password=endpoint.password,
    )


def _make_database(endpoint: Any, name: str) -> None:
    with (
        psycopg.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=endpoint.username,
            password=endpoint.password,
            autocommit=True,
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(f'CREATE DATABASE "{name}"')


def test_both_routes_converge_and_are_idempotent() -> None:
    """base-only와 CM-2.6 형상이 **같은 final schema로 수렴**하고 재실행이 no-op이다."""

    signatures: dict[str, tuple[str, str]] = {}
    with postgres.one_off_postgres(database="v5b2") as endpoint:
        for route, name in (("canonical", "fresh"), ("successor", "after26")):
            _make_database(endpoint, name)
            with _session(endpoint, name) as connection, connection.cursor() as cursor:
                execute = _execute(cursor)
                if route == "canonical":
                    _build_base_only(cursor)
                else:
                    _build_cm26_state(cursor)
                _load_rows(cursor)
                _build_preserved(cursor, "runtime")
                if route == "successor":
                    # 공용 pre-state 재현 — CM-2.6 뒤 두 객체에는 readonly SELECT가
                    # 붙어 있다.
                    cursor.execute("CREATE ROLE kosa_readonly NOLOGIN")
                    cursor.execute(
                        f"GRANT SELECT ON {v5.R03_TABLE}, {v5.ALARM_VIEW} "
                        "TO kosa_readonly"
                    )
                connection.commit()

                r03_present, view_present = v5.read_relation_presence(execute)
                state = v5.classify_schema_state(
                    r03_present=r03_present,
                    view_present=view_present,
                    r03_columns=(
                        execute(v5.R03_COLUMNS_SQL, {"table": v5.R03_TABLE})
                        if r03_present
                        else None
                    ),
                    view_definition=(
                        execute(v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW})[0][
                            "definition"
                        ]
                        if view_present
                        else None
                    ),
                )
                expected_state = (
                    "BASE_ONLY" if route == "canonical" else "V4_REFERENCE_COMPAT"
                )
                assert state == expected_state
                v5.assert_state_allows_apply(state, route=route)

                mode = "successor" if route == "successor" else "base_only"
                security = (
                    {
                        str(row["relname"]): row
                        for row in execute(
                            v5.RELATION_SECURITY_SQL,
                            {"names": list(v5.OWNED_BY_CM31)},
                        )
                    }
                    if mode == "successor"
                    else {}
                )
                # base-only는 복원할 pre-state가 없어 security가 비어 있다.
                plan = v5.apply_plan(route=route, profile="runtime", security=security)
                v5.assert_plan_shape(
                    plan, route=route, profile="runtime", security=security
                )
                _run_plan(execute, plan)
                connection.commit()

                live = v5.read_live_schema(execute)
                view_rows, r03_rows, action_rows = _counts(execute)
                # **data phase는 최종 dataset 기준이다.** 이 fixture는 형상 재현용이라
                # 189/192를 갖지 않으므로 `assert_postcheck()` 전체가 아니라 그것이
                # 부르는 계약 판정을 직접 본다. phase 판정은 단위 회귀가 덮는다.
                assert r03_rows == 0
                stored = execute(
                    "SELECT (SELECT count(*) FROM trace_alarm_history) "
                    "+ (SELECT count(*) FROM summary_alarm_history) AS n"
                )[0]["n"]
                assert view_rows == stored + r03_rows
                assert action_rows >= 0
                signatures[route] = v5.live_signatures(live, mode=mode)

    # 두 경로의 **schema** identity는 같아야 한다. security는 경로별 pre-state를 따른다.
    assert signatures["canonical"][0] == signatures["successor"][0]


def test_the_successor_restores_the_pre_state_acl() -> None:
    """`DROP TABLE`이 버린 권한이 같은 transaction 안에서 되돌아온다(계획 §6.5)."""

    with postgres.one_off_postgres(database="v5acl") as endpoint:
        _make_database(endpoint, "acl")
        with _session(endpoint, "acl") as connection, connection.cursor() as cursor:
            execute = _execute(cursor)
            _build_cm26_state(cursor)
            _load_rows(cursor)
            _build_preserved(cursor, "runtime")
            cursor.execute("CREATE ROLE kosa_readonly NOLOGIN")
            cursor.execute(
                f"GRANT SELECT ON {v5.R03_TABLE}, {v5.ALARM_VIEW} TO kosa_readonly"
            )
            connection.commit()

            before = {
                str(row["relname"]): row
                for row in execute(
                    v5.RELATION_SECURITY_SQL, {"names": list(v5.OWNED_BY_CM31)}
                )
            }
            expected = v5.security_signature_sha256(before, mode="successor")

            plan = v5.apply_plan(route="successor", profile="runtime", security=before)
            _run_plan(execute, plan)
            connection.commit()

            live = v5.read_live_schema(execute)
            _schema, security = v5.live_signatures(live, mode="successor")
            assert security == expected
            grantees = {
                grantee
                for row in live["security"].values()
                for grantee, _p, _g in v5.parse_acl(row["acl"])
            }
            assert "kosa_readonly" in grantees


def test_a_failure_after_the_drop_rolls_everything_back() -> None:
    """중간 실패는 구 R03 schema와 CM-2.6 View까지 원상 복구되어야 한다(계획 §10)."""

    with postgres.one_off_postgres(database="v5rb") as endpoint:
        _make_database(endpoint, "rb")
        with _session(endpoint, "rb") as connection, connection.cursor() as cursor:
            execute = _execute(cursor)
            _build_cm26_state(cursor)
            _load_rows(cursor)
            connection.commit()
            before_view = execute(v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW})[0][
                "definition"
            ]
            before_columns = [
                row["column_name"]
                for row in execute(v5.R03_COLUMNS_SQL, {"table": v5.R03_TABLE})
            ]

            with pytest.raises(psycopg.errors.UndefinedTable):
                execute(f"DROP VIEW public.{v5.ALARM_VIEW}")
                execute(f"DROP TABLE public.{v5.R03_TABLE}")
                execute("SELECT * FROM public.this_table_does_not_exist")
            connection.rollback()

            after_view = execute(v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW})[0][
                "definition"
            ]
            after_columns = [
                row["column_name"]
                for row in execute(v5.R03_COLUMNS_SQL, {"table": v5.R03_TABLE})
            ]
            assert after_columns == before_columns
            assert v5.view_definition_sha256(after_view) == v5.view_definition_sha256(
                before_view
            )
            assert v5.view_definition_sha256(after_view) == v5.COMPAT_VIEW_SHA256


def test_the_final_schema_verifies_in_both_data_phases() -> None:
    """`0/189`와 A-1.4 형상 `3/192` 양쪽에서 schema identity가 같다(계획 §4.2)."""

    with postgres.one_off_postgres(database="v5ph") as endpoint:
        _make_database(endpoint, "ph")
        with _session(endpoint, "ph") as connection, connection.cursor() as cursor:
            execute = _execute(cursor)
            _build_cm26_state(cursor)
            _load_rows(cursor)
            _build_preserved(cursor, "runtime")
            cursor.execute("CREATE ROLE kosa_readonly NOLOGIN")
            cursor.execute(
                f"GRANT SELECT ON {v5.R03_TABLE}, {v5.ALARM_VIEW} TO kosa_readonly"
            )
            connection.commit()
            security = {
                str(row["relname"]): row
                for row in execute(
                    v5.RELATION_SECURITY_SQL, {"names": list(v5.OWNED_BY_CM31)}
                )
            }
            _run_plan(
                execute,
                v5.apply_plan(route="successor", profile="runtime", security=security),
            )
            connection.commit()

            empty = v5.live_signatures(v5.read_live_schema(execute), mode="successor")
            before_view, before_r03, _a = _counts(execute)
            assert before_r03 == 0

            cursor.execute(
                (FIXTURES.parent / "v5_cm_3_1" / "r03_seed.sql").read_text(
                    encoding="utf-8"
                )
            )
            connection.commit()
            view_rows, r03_rows, _action = _counts(execute)
            # R03 3건이 곧바로 View branch에 나타난다.
            assert r03_rows == 3
            assert view_rows == before_view + 3
            populated = v5.live_signatures(
                v5.read_live_schema(execute), mode="successor"
            )
            # **행이 늘어도 schema identity는 그대로다.**
            assert populated == empty


# ---------------------------------------------------------------------------
# 실제 runner lifecycle (구현리뷰 9차 필수 1·4·5)
# ---------------------------------------------------------------------------


def _target(endpoint: Any, name: str, *, route: str, final: bool = True) -> Any:
    """공용 target 형상을 세운다. `name`은 실제 DB 이름(allowlist)이다.

    기본은 **최종 dataset**이다 — 그래야 189/192·138/51·null owner 0을 완화 없이
    통과한다(구현리뷰 11차 필수 2). `final=False`는 CM-2.6 소형 fixture다.
    """

    _make_database(endpoint, name)
    connection = _session(endpoint, name)
    cursor = connection.cursor()
    if route == "successor":
        _build_cm26_state(cursor)
    else:
        _build_base_only(cursor)
    if final:
        _load_final_dataset(cursor, v5.TARGET_PROFILE[name])
    else:
        _load_rows(cursor)
    _build_preserved(cursor, "runtime")
    if route == "successor":
        cursor.execute("CREATE ROLE kosa_readonly NOLOGIN")
        cursor.execute(
            f"GRANT SELECT ON {v5.R03_TABLE}, {v5.ALARM_VIEW} TO kosa_readonly"
        )
    connection.commit()
    return connection, cursor


def _run(cursor: Any, connection: Any, root: Path, **kwargs: Any) -> Any:
    return v5.apply_to_target(
        _execute(cursor),
        database=kwargs.pop("database", "kosa_agent"),
        confirm_target=kwargs.pop("confirm_target", "kosa_agent"),
        change_ref=kwargs.pop("change_ref", "GH-110"),
        artifact_root=root,
        commit=connection.commit,
        rollback=connection.rollback,
        **kwargs,
    )


def test_the_runner_applies_commits_and_writes_marker_last(tmp_path: Path) -> None:
    """전체 lifecycle — lock → transaction → DDL → ACL 복원 → postcheck → marker."""

    with postgres.one_off_postgres(database="v5run") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            marker = _run(cursor, connection, tmp_path)

    assert marker["status"] == "COMMITTED"
    assert marker["artifact_type"] == v5.MARKER_ARTIFACT_TYPE
    v5.assert_marker_contract(marker)

    receipt = v5.read_artifact(tmp_path / v5.receipt_name("kosa_agent", "GH-110"))
    v5.assert_receipt_contract(receipt)
    assert receipt["status"] == "COMMITTED"
    # marker는 receipt 뒤에 쓰인다.
    marker_path = tmp_path / v5.marker_name("kosa_agent", "GH-110")
    assert marker_path.is_file()
    assert marker_path.stat().st_mode & 0o777 == v5.ARTIFACT_FILE_MODE
    # commit 직후 identity가 receipt와 marker에 같이 실린다.
    for key in v5.POST_COMMIT_IDENTITY_KEYS:
        assert receipt[key] == marker[key]


def test_a_second_run_is_a_real_noop(tmp_path: Path) -> None:
    """**같은 runner를 두 번 부른다.** 두 번째는 `verify`가 no-op을 판정한다.

    이전 회귀는 `assert_marker_allows_noop()`을 직접 부르고 apply가 거부되는 것만 봤다.
    이름과 달리 실제 no-op이 아니었다(구현리뷰 10차 필수 2).
    """

    with postgres.one_off_postgres(database="v5noop") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            marker = _run(cursor, connection, tmp_path)

            # 두 번째 호출 — 실제 handler다.
            result = v5.verify_target(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                marker=marker,
            )
            assert result["noop"] is True
            assert result["state"] == "V5_REFERENCE_FINAL"
            assert (
                result["schema_signature_sha256"] == marker["schema_signature_sha256"]
            )
            assert (
                result["excluded_projection_sha256"]
                == marker["excluded_projection_sha256"]
            )

            # apply를 다시 부르면 재적용이 아니라 거부다.
            with pytest.raises(v5.ReferenceV5Error) as caught:
                _run(cursor, connection, tmp_path, change_ref="GH-111")
            assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_verify_refuses_a_target_that_is_not_final(tmp_path: Path) -> None:
    """CM-2.6 형상에 `verify`를 부르면 성공이 아니다."""

    with postgres.one_off_postgres(database="v5vfy") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            with pytest.raises(v5.ReferenceV5Error) as caught:
                v5.verify_target(
                    _execute(cursor),
                    database="kosa_agent",
                    confirm_target="kosa_agent",
                )
            assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_preflight_reads_the_pre_state_without_writing(tmp_path: Path) -> None:
    with postgres.one_off_postgres(database="v5pre") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            bundle = v5.preflight_target(
                execute, database="kosa_agent", confirm_target="kosa_agent"
            )
            assert bundle["state"] == "V4_REFERENCE_COMPAT"
            assert bundle["route"] == "successor"
            assert bundle["r03_rows"] == 0
            # 여전히 구 형상이다.
            definition = execute(v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW})[0][
                "definition"
            ]
            assert v5.view_definition_sha256(definition) == v5.COMPAT_VIEW_SHA256


def test_rehearse_changes_nothing(tmp_path: Path) -> None:
    """`rehearse`는 실제 DDL을 돌려보되 rollback하고 marker를 남기지 않는다."""

    with postgres.one_off_postgres(database="v5reh") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            before = v5.read_target_state(execute, profile="runtime")
            result = _run(cursor, connection, tmp_path, dry_run=True)
            after = v5.read_target_state(execute, profile="runtime")

    assert result["status"] == "ABORTED"
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()
    assert after["state"] == before["state"] == "V4_REFERENCE_COMPAT"
    assert after["excluded_projection_sha256"] == before["excluded_projection_sha256"]


def test_a_drifted_pre_state_stops_before_any_write(tmp_path: Path) -> None:
    """컬럼 type을 바꾼 구 R03에는 `DROP TABLE`이 돌지 않는다(구현리뷰 9차 필수 3)."""

    with postgres.one_off_postgres(database="v5drift") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            # View가 참조하지 않는 컬럼이라야 type을 바꿀 수 있다. drift를 만드는 것이
            # 목적이므로 어느 컬럼이든 상관없다.
            cursor.execute(
                f"ALTER TABLE {v5.R03_TABLE} "
                "ALTER COLUMN policy_version TYPE varchar(64)"
            )
            connection.commit()
            with pytest.raises(v5.ReferenceV5Error) as caught:
                _run(cursor, connection, tmp_path)
            assert caught.value.reason_code in {
                "PRE_STATE_COLUMNS_MISMATCH",
                "TARGET_STATE_UNSUPPORTED",
            }
            connection.rollback()
            execute = _execute(cursor)
            # 구 View가 그대로 살아 있다.
            definition = execute(v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW})[0][
                "definition"
            ]
            assert v5.view_definition_sha256(definition) == v5.COMPAT_VIEW_SHA256

    # 실패는 ABORTED receipt를 남기고 marker는 남기지 않는다.
    assert not (tmp_path / v5.marker_name("kosa_agent", "GH-110")).exists()


def test_a_lost_marker_is_recovered_from_the_committed_receipt(tmp_path: Path) -> None:
    """**marker 파일을 실제로 다시 쓴다.**

    판정만 하는 게 아니다(구현리뷰 10차 필수 2).
    """

    with postgres.one_off_postgres(database="v5rec") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            marker = _run(cursor, connection, tmp_path)
            marker_path = tmp_path / v5.marker_name("kosa_agent", "GH-110")

            # 이미 있으면 복구가 아니다 — 기존 정본을 덮지 않는다.
            with pytest.raises(v5.ReferenceV5Error) as caught:
                v5.recover_marker(
                    execute,
                    database="kosa_agent",
                    confirm_target="kosa_agent",
                    change_ref="GH-110",
                    artifact_root=tmp_path,
                )
            assert caught.value.reason_code == "MARKER_ALREADY_PRESENT"

            marker_path.unlink()
            restored = v5.recover_marker(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                change_ref="GH-110",
                artifact_root=tmp_path,
            )
            assert marker_path.is_file()
            for key in v5.POST_COMMIT_IDENTITY_KEYS:
                assert restored[key] == marker[key]
            assert v5.read_artifact(marker_path)["status"] == "COMMITTED"


def test_a_foreign_receipt_cannot_recover_a_marker(tmp_path: Path) -> None:
    """다른 실행의 `COMMITTED` receipt로는 복구되지 않는다(구현리뷰 9차 필수 4)."""

    with postgres.one_off_postgres(database="v5foreign") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            _run(cursor, connection, tmp_path)
            execute = _execute(cursor)
            receipt = v5.read_artifact(
                tmp_path / v5.receipt_name("kosa_agent", "GH-110")
            )
            live = v5.read_live_schema(execute)
            schema, _security = v5.live_signatures(live, mode="successor")
            with pytest.raises(v5.ReferenceV5Error) as caught:
                v5.assert_recovery_is_allowed(
                    receipt,
                    schema_signature=schema,
                    security_signature=receipt["security_signature_sha256"],
                    excluded_projection="c" * 64,
                    view_definition_sha256_value=v5.view_definition_sha256(
                        live["view_definition"]
                    ),
                )
            assert caught.value.reason_code == "EXCLUDED_PROJECTION_MISMATCH"


def test_the_excluded_objects_are_unchanged(tmp_path: Path) -> None:
    """base 9·Runtime·RAG·D 형상과 행 수가 전후로 같다(계획 §12-5)."""

    with postgres.one_off_postgres(database="v5exc") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            before = v5.excluded_projection_sha256(execute, profile="runtime")
            marker = _run(cursor, connection, tmp_path)
            after = v5.excluded_projection_sha256(execute, profile="runtime")

    assert after == before == marker["excluded_projection_sha256"]


def test_the_final_dataset_yields_189_and_the_wafer_join_resolves(
    tmp_path: Path,
) -> None:
    """**최종 ZIP 9 CSV로 0/189를 실증한다**(구현리뷰 11차 필수 2 · 계획 §9.2).

    CM-2.6 fixture는 legacy 값(126/47 · `lot`/`chamber` NULL)이라 이걸 증명할 수 없었다.
    `h.wafer_id = a.wafer` resolve도 여기서 함께 확인한다(계획 §12-4).
    """

    with postgres.one_off_postgres(database="v5final") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            marker = _run(cursor, connection, tmp_path)

            assert (marker["view_rows"], marker["r03_rows"]) == (189, 0)
            branches = {
                str(row["source"]): int(row["n"]) for row in execute(v5.VIEW_BRANCH_SQL)
            }
            assert branches["TRACE"] == v5.BRANCH_ROWS_EMPTY["TRACE"] == 138
            assert branches["SUMMARY"] == v5.BRANCH_ROWS_EMPTY["SUMMARY"] == 51
            # owner가 전부 풀린다 — legacy 값에서는 하나도 풀리지 않았다.
            assert (
                execute(
                    "SELECT count(*) AS n FROM public.v_alarm_event "
                    "WHERE lot_hist_id IS NULL"
                )[0]["n"]
                == 0
            )
            row = execute(
                "SELECT wafer_id, wafer_no, lot_id FROM public.v_alarm_event "
                "WHERE source = 'TRACE' LIMIT 1"
            )[0]
            # `wafer_id`는 `LOT001W001` 형태의 문자열, `wafer_no`는 정수다.
            # 둘을 같은 컬럼으로 합치면 resolve가 성립하지 않는다(계획 §12-4).
            assert isinstance(row["wafer_id"], str)
            assert str(row["lot_id"]) in str(row["wafer_id"])
            assert isinstance(row["wafer_no"], int)
            assert str(row["wafer_id"]) != str(row["wafer_no"])

            # 완화 없이 전체 postcheck를 통과한다.
            result = v5.verify_target(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                marker=marker,
            )
            assert result["data_phase"] == "REFERENCE_EMPTY"
            assert result["noop"] is True


def test_the_populated_phase_is_verified_on_the_final_dataset(
    tmp_path: Path,
) -> None:
    """A-1.4 형상 **3/192**를 최종 dataset에서 실증한다(계획 §9.2)."""

    with postgres.one_off_postgres(database="v5pop") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            marker = _run(cursor, connection, tmp_path)
            empty = v5.verify_target(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                marker=marker,
            )

            owners = execute(
                "SELECT lot_hist_id, lot_id, equipment_id, chamber_id, wafer_id "
                "FROM lot_history ORDER BY lot_hist_id LIMIT 3"
            )
            parameter = execute(
                "SELECT parameter_id FROM dim_parameter ORDER BY parameter_id LIMIT 1"
            )[0]["parameter_id"]
            import hashlib

            for index, owner in enumerate(owners, start=1):
                alarm_id = (
                    "R03-"
                    + hashlib.sha256(
                        f"{owner['lot_hist_id']}|{parameter}|{index}".encode()
                    ).hexdigest()[:20]
                )
                cursor.execute(
                    "INSERT INTO r03_alarm_history VALUES (%s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, 'R03_CONSEC_V1')",
                    (
                        alarm_id,
                        "2026-08-18 00:00:00",
                        owner["lot_hist_id"],
                        owner["lot_id"],
                        owner["equipment_id"],
                        owner["chamber_id"],
                        parameter,
                        index,
                        index,
                        json.dumps([owner["wafer_id"]] * 3),
                        json.dumps(
                            [f"TRACE:{owner['lot_hist_id']}:{n}" for n in range(9)]
                        ),
                    ),
                )
            connection.commit()

            populated = v5.verify_target(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
            )
            assert (populated["view_rows"], populated["r03_rows"]) == (192, 3)
            assert populated["data_phase"] == "R03_POPULATED"
            # **schema identity는 그대로다.**
            assert (
                populated["schema_signature_sha256"] == empty["schema_signature_sha256"]
            )


# ---------------------------------------------------------------------------
# production adapter와 최종 ZIP E2E (구현리뷰 11차 필수 1·2)
# ---------------------------------------------------------------------------


def _bootstrap_env(endpoint: Any, monkeypatch: Any) -> None:
    """`db_target`이 읽는 5개 키만 채운다. DSN은 만들지 않는다."""

    import db_target

    monkeypatch.setenv("POSTGRES_BOOTSTRAP_HOST", endpoint.host)
    monkeypatch.setenv("POSTGRES_BOOTSTRAP_PORT", str(endpoint.port))
    monkeypatch.setenv("POSTGRES_BOOTSTRAP_USER", endpoint.username)
    monkeypatch.setenv("POSTGRES_BOOTSTRAP_PASSWORD", endpoint.password)
    monkeypatch.setenv(
        "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256",
        db_target.host_fingerprint(endpoint.host, endpoint.port),
    )


def test_the_adapter_refuses_a_local_host(monkeypatch: Any) -> None:
    """`db_target`은 로컬 host를 **의도적으로** 막는다.

    그래서 격리 container로는 adapter의 **성공 경로**를 탈 수 없다 — 공용 DB에서만
    가능하다. 대신 typed reason으로 끝나는지, traceback이 새지 않는지를 본다
    (구현리뷰 11차 필수 1).
    """

    import contextlib
    import io

    with postgres.one_off_postgres(database="v5adapter") as endpoint:
        _bootstrap_env(endpoint, monkeypatch)
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = v5.main(
                [
                    "--preflight",
                    "--database",
                    "kosa_agent",
                    "--confirm-target",
                    "kosa_agent",
                ]
            )
    assert code == v5.EXIT_USAGE
    assert stderr.getvalue().strip() == "TARGET_ENV_INVALID"


def test_every_contract_sql_runs_on_real_sqlalchemy(tmp_path: Path) -> None:
    """모듈 SQL이 **실제 SQLAlchemy**에서 도는지 본다(구현리뷰 11차 필수 1).

    계약 SQL은 psycopg `%(name)s`로 쓰여 있고 adapter가 `:name`으로 옮긴다. 그 변환이
    틀리면 production에서만 깨진다 — 단위 회귀는 psycopg로만 돌기 때문이다.
    """

    from sqlalchemy import create_engine, text

    with postgres.one_off_postgres(database="v5sa") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            _run(cursor, connection, tmp_path / "first")

    with postgres.one_off_postgres(database="v5sa2") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            _run(cursor, connection, tmp_path / "second")
            url = (
                f"postgresql+psycopg://{endpoint.username}:{endpoint.password}"
                f"@{endpoint.host}:{endpoint.port}/kosa_agent"
            )
            engine = create_engine(url, future=True)
            try:
                with engine.connect() as sa_connection:
                    names = list(v5.OWNED_BY_CM31)
                    for sql, params in (
                        (v5.RELATION_PRESENCE_SQL, {"names": names}),
                        (v5.R03_COLUMNS_SQL, {"table": v5.R03_TABLE}),
                        (v5.R03_CONSTRAINTS_SQL, {}),
                        (v5.VIEW_COLUMNS_SQL, {"view": v5.ALARM_VIEW}),
                        (v5.VIEW_DEFINITION_SQL, {"view": v5.ALARM_VIEW}),
                        (v5.RELATION_SECURITY_SQL, {"names": names}),
                        (v5.DEPENDENTS_SQL, {"names": names}),
                        (v5.TRIGGERS_SQL, {"names": names}),
                        (v5.EXCLUDED_COLUMNS_SQL, {"names": names}),
                        (v5.EXCLUDED_CONSTRAINTS_SQL, {"names": names}),
                        (v5.EXCLUDED_INDEXES_SQL, {"names": names}),
                        (v5.VIEW_BRANCH_SQL, {}),
                        (v5.VIEW_DUPLICATE_SQL, {}),
                    ):
                        translated = v5._to_named_binds(sql)
                        assert "%(" not in translated, sql[:40]
                        sa_connection.execute(text(translated), params).fetchall()
            finally:
                engine.dispose()


def test_open_session_runs_end_to_end_on_a_container(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`open_session()` **본체**를 실제 SQLAlchemy로 통과시킨다(구현리뷰 12차 필수 3).

    `db_target`은 로컬 host를 막으므로 loader만 container target으로 바꾼다. URL 생성·
    connect·identity·search_path·`_SessionOwner`는 그대로 production 경로다. local-host
    guard 자체는 별도 회귀가 지킨다.
    """

    import json

    import db_target

    with postgres.one_off_postgres(database="v5open") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            _run(cursor, connection, tmp_path)

        target = db_target.BootstrapTarget(
            host=endpoint.host,
            port=endpoint.port,
            username=endpoint.username,
            password=endpoint.password,
            database="kosa_agent",
            profile="runtime",
        )
        monkeypatch.setattr(db_target, "load_bootstrap_target", lambda **kwargs: target)

        import contextlib
        import io

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = v5.main(
                [
                    "--verify",
                    "--database",
                    "kosa_agent",
                    "--confirm-target",
                    "kosa_agent",
                ]
            )
        assert (code, stderr.getvalue().strip()) == (v5.EXIT_OK, "")
        payload = json.loads(stdout.getvalue())
        assert payload["mode"] == "verify"
        assert payload["state"] == "V5_REFERENCE_FINAL"
        assert payload["data_phase"] == "REFERENCE_EMPTY"


def test_a_busy_target_is_refused_without_writing(tmp_path: Path) -> None:
    """두 connection이 경쟁하면 뒤엣것이 **쓰기 0**으로 끝난다(구현리뷰 12차 필수 2)."""

    import time

    with postgres.one_off_postgres(database="v5busy") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        holder = _session(endpoint, "kosa_agent")
        with connection, holder:
            holder_cursor = holder.cursor()
            holder_cursor.execute(
                v5.ADVISORY_LOCK_SQL,
                {
                    "namespace": v5.ADVISORY_LOCK_NAMESPACE,
                    "key": v5.advisory_lock_key("kosa_agent"),
                },
            )
            assert holder_cursor.fetchone()[0] is True

            started = time.monotonic()
            with pytest.raises(v5.ReferenceV5Error) as caught:
                _run(cursor, connection, tmp_path)
            elapsed = time.monotonic() - started

        assert caught.value.reason_code == "TARGET_BUSY"
        # **기다리지 않는다.** blocking lock이면 여기서 무한 대기했다.
        assert elapsed < 5.0
        assert not list(tmp_path.iterdir())


def test_the_canonical_route_verifies_and_recovers(tmp_path: Path) -> None:
    """base-only는 `relacl IS NULL`이 정상이다.

    verify/recover가 그걸 알아야 한다(구현리뷰 11차 필수 3).
    """

    with postgres.one_off_postgres(database="v5canon") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="canonical")
        with connection:
            execute = _execute(cursor)
            marker = _run(cursor, connection, tmp_path)
            assert marker["route"] == "canonical"

            result = v5.verify_target(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                marker=marker,
            )
            assert result["route"] == "canonical"
            assert result["noop"] is True

            (tmp_path / v5.marker_name("kosa_agent", "GH-110")).unlink()
            recovered = v5.recover_marker(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                change_ref="GH-110",
                artifact_root=tmp_path,
            )
            assert recovered["route"] == "canonical"


def test_the_recovery_runbook_completes_end_to_end(tmp_path: Path) -> None:
    """`STARTED → 새 connection verify → 승격 → recover-marker`를 완주한다.

    commit 응답 유실과 receipt 첫 쓰기 실패가 남기는 상태가 같다 — 디스크에 `STARTED`만
    있고 DB는 final이다. DB record 없이 artifact와 live 실측만으로 복구한다
    (구현리뷰 13차 필수 2).
    """

    with postgres.one_off_postgres(database="v5runbook") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            execute = _execute(cursor)
            marker = _run(cursor, connection, tmp_path)
            receipt_path = tmp_path / v5.receipt_name("kosa_agent", "GH-110")
            marker_path = tmp_path / v5.marker_name("kosa_agent", "GH-110")

            # commit 뒤 artifact가 유실된 상태를 만든다.
            marker_path.unlink()
            v5.write_artifact(
                receipt_path,
                {
                    **v5.read_artifact(receipt_path),
                    "status": "STARTED",
                    **dict.fromkeys(v5.POST_COMMIT_IDENTITY_KEYS),
                },
            )

            # 이 상태에서는 marker를 만들 수 없다.
            with pytest.raises(v5.ReferenceV5Error) as caught:
                v5.recover_marker(
                    execute,
                    database="kosa_agent",
                    confirm_target="kosa_agent",
                    change_ref="GH-110",
                    artifact_root=tmp_path,
                )
            assert caught.value.reason_code == "RECEIPT_STATUS_NOT_ALLOWED"

            # 1) verify — live가 정말 final인가
            verified = v5.verify_target(
                execute, database="kosa_agent", confirm_target="kosa_agent"
            )
            assert verified["state"] == "V5_REFERENCE_FINAL"

            # 2) 승격 — operator 확인이 있어야 한다
            promoted = v5.promote_receipt(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                change_ref="GH-110",
                artifact_root=tmp_path,
                confirm_recovery=True,
            )
            assert promoted["status"] == "COMMITTED"
            for key in v5.POST_COMMIT_IDENTITY_KEYS:
                assert promoted[key] == marker[key]

            # 3) marker 복구
            recovered = v5.recover_marker(
                execute,
                database="kosa_agent",
                confirm_target="kosa_agent",
                change_ref="GH-110",
                artifact_root=tmp_path,
            )
            assert marker_path.is_file()
            assert (
                recovered["schema_signature_sha256"]
                == marker["schema_signature_sha256"]
            )


def test_a_table_lock_race_reports_target_busy(tmp_path: Path) -> None:
    """**advisory lock이 아니라 table lock** 경쟁을 본다(구현리뷰 13차 필수 1)."""

    import time

    with postgres.one_off_postgres(database="v5tlock") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        blocker = _session(endpoint, "kosa_agent")
        with connection, blocker:
            _run(cursor, connection, tmp_path)
            blocker_cursor = blocker.cursor()
            # 다른 session이 base 9 하나를 배타적으로 잡는다.
            blocker_cursor.execute(
                "LOCK TABLE public.lot_history IN ACCESS EXCLUSIVE MODE"
            )

            started = time.monotonic()
            with pytest.raises(v5.ReferenceV5Error) as caught:
                v5.verify_target(
                    _execute(cursor),
                    database="kosa_agent",
                    confirm_target="kosa_agent",
                )
            elapsed = time.monotonic() - started
            connection.rollback()

        assert caught.value.reason_code == "TARGET_BUSY"
        # `NOWAIT`이라 기다리지 않는다.
        assert elapsed < 5.0


def test_a_permission_error_is_not_reported_as_busy(tmp_path: Path) -> None:
    """권한 없음이 잠금 경쟁으로 위장되면 재시도 대상이 된다(구현리뷰 13차 필수 1)."""

    import psycopg.errors

    with postgres.one_off_postgres(database="v5perm") as endpoint:
        connection, cursor = _target(endpoint, "kosa_agent", route="successor")
        with connection:
            _run(cursor, connection, tmp_path)
            cursor.execute("CREATE ROLE kosa_limited NOLOGIN")
            cursor.execute("GRANT USAGE ON SCHEMA public TO kosa_limited")
            connection.commit()
            cursor.execute("SET ROLE kosa_limited")

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                v5.verify_target(
                    _execute(cursor),
                    database="kosa_agent",
                    confirm_target="kosa_agent",
                )
            connection.rollback()
