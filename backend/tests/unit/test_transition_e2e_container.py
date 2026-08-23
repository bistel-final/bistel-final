"""격리 3-target production E2E(`V5-CM-2.6`).

구현리뷰 9차부터 이월된 항목이다. orchestrator·session·handler를 각각 회귀로 덮었지만,
production entrypoint 그대로 세 target을 도는 흐름은 없었다.

이 회귀는 격리 PostgreSQL **3개**를 세우고 다음을 한 흐름으로 검증한다.

```
preflight → backup/restore → apply → 중간 실패 재개 → no-op → full verify
```

공용 서버에는 접근하지 않는다. 모든 target은 일회성 container이고 종료 시 사라진다.
`pg_dump`/`pg_restore`도 digest 고정 image 안에서 실제로 돈다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import backup_orchestrator as orchestrator  # noqa: E402
import postgres_backup as backup  # noqa: E402
import postgres_transition as transition  # noqa: E402
import rehearsal_postgres as postgres  # noqa: E402

pytestmark = pytest.mark.container

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "v5_cm_2_6"
BASE_SQL = FIXTURE_ROOT / "legacy_base_schema.sql"
REFERENCE_SQL = FIXTURE_ROOT / "legacy_reference.sql"


def _block(pattern: str) -> str:
    import re

    text = REFERENCE_SQL.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.DOTALL)
    assert match, pattern
    return match.group(0)


def _build_legacy(cursor: Any) -> None:
    cursor.execute(BASE_SQL.read_text(encoding="utf-8"))
    cursor.execute(_block(r"CREATE TABLE r03_alarm_history\s*\(.*?\);"))
    cursor.execute(_block(r"CREATE VIEW v_alarm_event AS.*?;"))


class _Target:
    """일회성 격리 target 하나."""

    def __init__(self, name: str, endpoint: Any) -> None:
        self.name = name
        self.endpoint = endpoint


def _dump_argv_seen(calls: list[Any]) -> list[str]:
    return [
        argv[argv.index("--entrypoint") + 1] for argv in calls if "--entrypoint" in argv
    ]


def test_real_pinned_image_dump_and_isolated_restore(tmp_path: Path) -> None:
    """**실제** `pg_dump` → 격리 `pg_restore` → 복원본 대조를 Docker로 돌린다.

    지금까지 이 경로는 주입한 가짜 runner로만 돌았다(구현리뷰 13차 필수 1). 여기서는

    - digest 고정 image 안에서 `pg_dump`·`pg_restore`가 실제로 실행되고
    - archive object list가 base 9 allowlist와 정확히 같고
    - 복원본의 column shape·행 수·content hash가 원본과 같은지

    를 실물로 확인한다.

    **범위 제한.** 원본을 완전한 운영 형상(RAG 포함)으로 만들지 못한다. 고정 rehearsal
    image에 `pgvector`가 없어 `CREATE EXTENSION vector`가 실패하기 때문이다. 그래서
    `classify_target()`까지 가는 `backup_and_verify()` 전체가 아니라 backup·restore
    경로를 직접 검증한다. 전체 3-target E2E는 pgvector가 있는 image가 필요하다.
    """

    import subprocess

    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    calls: list[Any] = []

    def recording_runner(argv: Any, **kwargs: Any) -> Any:
        calls.append(list(argv))
        return subprocess.run(argv, **kwargs)

    # RAG가 있는 운영 형상을 만들려면 `vector` 확장이 필요하다.
    with postgres.one_off_postgres(
        database="fdc_e2e_source", image=postgres.POSTGRES_RAG_IMAGE
    ) as source:
        with psycopg.connect(
            host=source.host,
            port=source.port,
            dbname=source.database,
            user=source.username,
            password=source.password,
        ) as raw:
            with raw.cursor() as cursor:
                _build_legacy(cursor)
                # 복원 충실도를 볼 수 있도록 행을 하나 넣는다.
                cursor.execute(
                    "INSERT INTO lot_history (lot_hist_id, lot_id, wafer_no) "
                    "VALUES ('LOT-E2E-0001', 'LOT-E2E', 1)"
                )
            raw.commit()

        url = URL.create(
            "postgresql+psycopg",
            username=source.username,
            password=source.password,
            host=source.host,
            port=source.port,
            database=source.database,
        )
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                inventory = transition.read_inventory(
                    connection,
                    database="kosa_agent",
                    profile="runtime",
                    require_snapshot=False,
                )
        finally:
            engine.dispose()

        assert set(transition.BASE_TABLES) <= set(inventory.tables)

        staging = tmp_path / "staging"
        staging.mkdir()
        archive = staging / transition.archive_name("kosa_agent", "GH-104")
        client = backup.select_backup_client(16)
        child_env = backup.child_environment(
            source.password,
            host=backup.rewrite_host(source.host, sys.platform),
            port=source.port,
            user=source.username,
            database=source.database,
        )

        backup.run_command(
            backup.pinned_client_argv(
                backup.dump_argv(
                    database=source.database,
                    tables=sorted(transition.BASE_TABLES),
                    out_path=orchestrator.container_path(archive.name),
                ),
                image=client.image,
                child_env=child_env,
                mounts={str(staging.resolve()): orchestrator.CONTAINER_BACKUP_DIR},
            ),
            runner=recording_runner,
            child_env=child_env,
        )
        assert archive.is_file(), "실제 dump가 만들어지지 않았다"

        # archive object list가 base 9와 exact다.
        orchestrator.assert_archive_contains_only_base(
            archive,
            image=client.image,
            child_env=child_env,
            staging=staging,
            runner=recording_runner,
        )

        restored = orchestrator.restore_and_fingerprint(
            archive,
            profile="runtime",
            lifecycle=postgres.one_off_postgres,
            runner=recording_runner,
            reader=orchestrator.default_restore_reader,
            image=client.image,
            staging=staging,
        )

    # 복원본이 원본과 같다.
    assert restored["base_column_shape_sha256"] == (
        transition.base_column_shape_sha256(inventory)
    )
    assert restored["base_content"] == dict(inventory.base_content)
    assert restored["base_rows"] == {
        name: inventory.row_counts.get(name) for name in transition.BASE_TABLES
    }
    assert restored["base_rows"]["lot_history"] == 1

    # 모든 client 실행이 digest 고정 image 안에서 일어났다.
    entrypoints = _dump_argv_seen(calls)
    assert "pg_dump" in entrypoints and "pg_restore" in entrypoints
    for argv in calls:
        assert argv[0] == "docker"
        assert client.image in argv
        # host 절대경로가 container 경로로 새지 않는다.
        assert str(tmp_path) not in " ".join(
            arg for arg in argv if not arg.startswith(str(staging.resolve()) + ":")
        )


OWNER_PASSWORD = "e2e-owner-pw"


def _become_owner(setup: Any, text: Any) -> None:
    """운영과 같은 소유 구조를 만든다.

    `check_execution_privilege()`는 public relation 전부가 **접속 role 소유**이길
    요구하고, `check_legacy_view_identity()`는 View owner가 `kosa`이길 요구한다. 둘을
    동시에 만족하려면 `kosa`로 접속해야 한다 — 운영이 바로 그 구조다.
    """

    for role, options in (
        (transition.LEGACY_VIEW_OWNER, f"LOGIN SUPERUSER PASSWORD '{OWNER_PASSWORD}'"),
        (transition.READONLY_ROLE, "NOLOGIN"),
    ):
        setup.execute(
            text(
                "DO $do$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles "
                f"WHERE rolname = '{role}') THEN CREATE ROLE \"{role}\" {options}; "
                "END IF; END $do$"
            )
        )
    # `REASSIGN OWNED`는 system object까지 건드려 실패한다. public schema의
    # relation만 옮긴다.
    rows = setup.execute(
        text(
            "SELECT c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'S')"
        )
    ).all()
    keyword = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "S": "SEQUENCE"}
    for name, kind in rows:
        setup.execute(
            text(
                f"ALTER {keyword[kind]} public.{name} "
                f'OWNER TO "{transition.LEGACY_VIEW_OWNER}"'
            )
        )
    setup.execute(
        text(f'ALTER SCHEMA public OWNER TO "{transition.LEGACY_VIEW_OWNER}"')
    )
    for statement in transition.compatibility_view_acl_statements():
        setup.execute(text(statement))


class _OwnerEndpoint:
    """같은 서버에 **소유 role**로 붙는 endpoint."""

    def __init__(self, endpoint: Any) -> None:
        self.host = endpoint.host
        self.port = endpoint.port
        self.database = endpoint.database
        self.username = transition.LEGACY_VIEW_OWNER
        self.password = OWNER_PASSWORD


def _build_external_fks(setup: Any, text: Any, *, profile: str) -> None:
    """`EXPECTED_EXTERNAL_FKS_BY_PROFILE`와 exact 일치하는 FK를 만든다.

    이름·child/parent column·update/delete action까지 비교되므로 stub table의 column을
    그 계약에 맞춰 바꾼 뒤 constraint를 붙인다.
    """

    for name, child, child_cols, parent, parent_cols, _d, _u in sorted(
        transition.EXPECTED_EXTERNAL_FKS_BY_PROFILE[profile]
    ):
        for column in child_cols:
            setup.execute(
                text(
                    f"ALTER TABLE public.{child} "
                    f"ADD COLUMN IF NOT EXISTS {column} varchar(20)"
                )
            )
        existing = setup.execute(
            text(
                "SELECT 1 FROM pg_constraint WHERE conname = :n "
                "AND conrelid = to_regclass(:t)"
            ),
            {"n": name, "t": f"public.{child}"},
        ).scalar()
        if existing:
            continue
        setup.execute(
            text(
                f"ALTER TABLE public.{child} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({', '.join(child_cols)}) "
                f"REFERENCES public.{parent} ({', '.join(parent_cols)})"
            )
        )


def _build_operational_shape(
    setup: Any, text: Any, database: str = "kosa_agent"
) -> None:
    """운영 형상에 가깝게 채운다 — `vector` 확장 + 보존·RAG relation.

    교육생 배포패키지①의 compose가 쓰는 `pgvector/pgvector:pg16`을 고정했으므로 이제
    `CREATE EXTENSION vector`가 된다. 순정 `postgres:16`에는 pgvector가 없다.
    """

    setup.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    rag_columns = {
        "document": (
            "doc_id text primary key, title text, doc_type text, "
            "model_code text, source_path text, version text"
        ),
        "document_chunk": (
            "chunk_id text primary key, doc_id text, chunk_seq integer, "
            "section_title text, content text, token_cnt integer, "
            "metadata_json jsonb, embedding vector(1024)"
        ),
    }
    profile = transition.TARGET_PROFILE[database]
    expected_tables, expected_sequences = transition.expected_relations(
        transition.TargetInventory(
            database=database,
            profile=profile,
            server_major=16,
            tables=(),
            views=(),
            sequences=(),
            other_relations=(),
            extensions=(),
        )
    )
    for name in sorted(expected_tables):
        if not setup.execute(
            text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
        ).scalar():
            columns = rag_columns.get(name, "id integer")
            setup.execute(text(f"CREATE TABLE public.{name} ({columns})"))
    for name in sorted(expected_sequences):
        if not setup.execute(
            text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
        ).scalar():
            setup.execute(text(f"CREATE SEQUENCE public.{name}"))

    # 외부 FK identity도 exact 비교 대상이다. stub table에 같은 이름·컬럼으로 만든다.
    _build_external_fks(setup, text, profile=profile)

    # 마지막에 소유권을 옮긴다 — 그 뒤 만든 object는 다시 옮겨야 한다.
    _become_owner(setup, text)

    # RAG는 **존재만으로는** 부족하다. 행이 0이면 drift로 판정된다(계획 §4.2).
    if not setup.execute(text("SELECT count(*) FROM public.document")).scalar():
        setup.execute(
            text(
                "INSERT INTO public.document "
                "(doc_id, title, doc_type, model_code, source_path, version) "
                "SELECT 'DOC' || g, 't' || g, 'spec', 'M', 'p', 'v1' "
                "FROM generate_series(1, 3) AS g"
            )
        )
        setup.execute(
            text(
                "INSERT INTO public.document_chunk "
                "(chunk_id, doc_id, chunk_seq, section_title, content, "
                " token_cnt, metadata_json, embedding) "
                "SELECT 'CH' || g, 'DOC' || (mod(g - 1, 3) + 1), g, 's', 'c', 1, "
                "'{}'::jsonb, array_fill(0.0::real, ARRAY[1024])::vector "
                "FROM generate_series(1, 9) AS g"
            )
        )


# ---------------------------------------------------------------------------
# 구현리뷰 13차 필수 1 — 격리 3-target 전체 흐름
# ---------------------------------------------------------------------------

#: 최종 ZIP 경로. CI와 로컬이 **같은 변수**를 읽는다.
#: 개인 경로를 fallback으로 두면 CI에서 조용히 skip되고 job은 green이 된다
#: (구현리뷰 14차 필수 2).
FINAL_ARCHIVE_ENV_KEY = "MENTOR_FINAL_ARCHIVE"


def _final_archive() -> Path:
    """최종 ZIP을 찾는다. **지정됐는데 없거나 hash가 다르면 skip이 아니라 실패다.**"""

    import os

    import transition_sessions as ts

    declared = os.environ.get(FINAL_ARCHIVE_ENV_KEY, "").strip()
    if not declared:
        pytest.skip(f"{FINAL_ARCHIVE_ENV_KEY}가 없다")
    path = Path(declared).expanduser()
    if not path.is_file():
        pytest.fail(f"{FINAL_ARCHIVE_ENV_KEY}가 가리키는 파일이 없다")
    # pin 대조까지 여기서 한다. 다른 ZIP을 넣어 통과시킬 수 없다.
    ts.assert_archive_is_pinned(path)
    return path


LEGACY_ROWS_SQL = FIXTURE_ROOT / "legacy_rows.sql"

#: 합성 legacy 행이 조용히 바뀌면 상태 판정이 의미를 잃는다.
LEGACY_ROWS_SHA256 = "1e3c9205fc2fdad15a03e5ec99c969f9fba0a53fe1bedd2c1e09c0e72e2b07db"


def test_legacy_rows_fixture_is_pinned() -> None:
    import hashlib

    assert (
        hashlib.sha256(LEGACY_ROWS_SQL.read_bytes()).hexdigest() == LEGACY_ROWS_SHA256
    )


def _build_legacy_state(cursor: Any, profile: str) -> None:
    """Gate 0가 관측한 **legacy 상태**를 격리 환경에 만든다.

    실제 legacy 데이터는 구 `kosa_0813` epoch이라 쓸 수 없다. `classify_base()`의 legacy
    판정이 catalog hash와 행 수만 보므로(legacy content hash는 pin되지 않는다 — 계획
    §4.2) 행 수를 정확히 맞춘 합성 데이터로 만든다.
    """

    _build_legacy(cursor)
    cursor.execute(LEGACY_ROWS_SQL.read_text(encoding="utf-8"))
    if profile == "evaluation":
        cursor.execute(
            "INSERT INTO action_history (action_id) "
            "SELECT 'AC' || lpad(g::text, 8, '0') FROM generate_series(1, 48) AS g"
        )


def test_legacy_state_fixture_classifies_as_base_legacy_epoch() -> None:
    """전제 확인 — 합성 fixture가 Gate 0 pin과 같은 legacy 상태로 판정된다.

    이게 성립해야 3-target 흐름이 `classify_target()`을 우회하지 않는다.
    """

    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    for profile in ("runtime", "evaluation"):
        with postgres.one_off_postgres(
            database=f"fdc_state_{profile}", image=postgres.POSTGRES_RAG_IMAGE
        ) as endpoint:
            with psycopg.connect(
                host=endpoint.host,
                port=endpoint.port,
                dbname=endpoint.database,
                user=endpoint.username,
                password=endpoint.password,
            ) as raw:
                with raw.cursor() as cursor:
                    _build_legacy_state(cursor, profile)
                raw.commit()

            url = URL.create(
                "postgresql+psycopg",
                username=endpoint.username,
                password=endpoint.password,
                host=endpoint.host,
                port=endpoint.port,
                database=endpoint.database,
            )
            engine = create_engine(url)
            try:
                with engine.begin() as connection:
                    inventory = transition.read_inventory(
                        connection,
                        database=(
                            "kosa_agent" if profile == "runtime" else "kosa_text2sql"
                        ),
                        profile=profile,
                        require_snapshot=False,
                    )
            finally:
                engine.dispose()

            rows = {
                name: inventory.row_counts.get(name) for name in transition.BASE_TABLES
            }
            assert rows == dict(transition.LEGACY_BASE_ROWS[profile]), profile
            assert transition.base_catalog_sha256(inventory) == (
                transition.LEGACY_BASE_CATALOG_SHA256
            ), profile
            assert transition.classify_base(inventory) is (
                transition.BaseState.BASE_LEGACY_EPOCH
            ), profile


def _url(endpoint: Any) -> Any:
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg",
        username=endpoint.username,
        password=endpoint.password,
        host=endpoint.host,
        port=endpoint.port,
        database=endpoint.database,
    )


def _read(endpoint: Any, database: str) -> Any:
    from sqlalchemy import create_engine

    engine = create_engine(_url(endpoint))
    try:
        with engine.begin() as connection:
            return transition.read_inventory(
                connection,
                database=database,
                profile=transition.TARGET_PROFILE[database],
                require_snapshot=False,
            )
    finally:
        engine.dispose()


def _session_registry(owners: Mapping[str, Any], handler: Any) -> dict[str, Any]:
    """세 격리 target을 production registry 모양으로 배선한다.

    `PUBLIC_SESSIONS`가 받는 것과 같은 factory 3종이다. 테스트가 production 알고리즘을
    다시 구현하지 않고 `main()`을 그대로 부르기 위한 유일한 주입점이다
    (구현리뷰 14차 필수 1).
    """

    import contextlib

    from sqlalchemy import create_engine

    engines = {
        database: create_engine(
            _url(endpoint), isolation_level="REPEATABLE READ", hide_parameters=True
        )
        for database, endpoint in owners.items()
    }

    @contextlib.contextmanager
    def session(database: str) -> Iterator[Any]:
        with engines[database].connect() as connection:
            yield connection

    return {
        "read_only": session,
        "mutating": session,
        "handler": handler,
        "_engines": engines,
    }


def _dispose(registry: Mapping[str, Any]) -> None:
    for engine in registry["_engines"].values():
        engine.dispose()


def _write_approval(
    tmp_path: Path, inventories: Mapping[str, Any], change_ref: str
) -> tuple[Path, str]:
    """실측 inventory에서 approval을 만든다. 값은 전부 독립 산출이다."""

    import transition_public_postgres as cli

    report = cli.preflight_report(inventories)
    expected = cli.expected_approval(report, inventories, change_ref)
    payload = {
        "artifact_type": transition.APPROVAL_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "status": "APPROVED",
        "ordered_targets": list(transition.ORDERED_TARGETS),
        "execution_privilege": transition.EXECUTION_PRIVILEGE,
        "owner_match": True,
        "approved_at": "2026-08-22T12:00:00+09:00",
        **{key: expected[key] for key in transition.APPROVAL_EXPECTED_KEYS},
    }
    transition.validate_approval_schema(payload)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload["preflight_bundle_sha256"]


def _apply_argv(approval: Path, root: Path, change_ref: str) -> list[str]:
    argv = [
        "--apply",
        "--approval",
        str(approval),
        "--backup-root",
        str(root),
        "--change-ref",
        change_ref,
    ]
    for database in transition.ORDERED_TARGETS:
        argv += [
            "--receipt",
            str(root / orchestrator.receipt_name(database, change_ref)),
        ]
    for database in transition.ORDERED_TARGETS:
        argv += ["--confirm-target", database]
    return argv


def _single_server_targets(endpoint: Any) -> dict[str, Any]:
    """**한 서버에 세 DB.** 공용 구조와 같다.

    target마다 container를 따로 띄우면 host·port가 달라 `build_public_sessions()`의 단일
    DSN으로 배선할 수 없고, `backup_orchestrator.main()`도 부를 수 없다(구현리뷰 15차
    필수 1). 공용은 세 DB가 같은 서버에 있으므로 그 구조를 그대로 만든다.
    """

    import psycopg

    with psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password,
        autocommit=True,
    ) as raw:
        with raw.cursor() as cursor:
            cursor.execute(
                f'CREATE ROLE "{transition.LEGACY_VIEW_OWNER}" '
                f"LOGIN SUPERUSER PASSWORD '{OWNER_PASSWORD}'"
            )
            cursor.execute(f'CREATE ROLE "{transition.READONLY_ROLE}" NOLOGIN')
            for database in transition.ORDERED_TARGETS:
                cursor.execute(
                    f'CREATE DATABASE "{database}" '
                    f'OWNER "{transition.LEGACY_VIEW_OWNER}"'
                )

    owners: dict[str, Any] = {}
    for database in transition.ORDERED_TARGETS:
        owners[database] = _NamedEndpoint(endpoint, database)
    return owners


class _NamedEndpoint:
    """같은 서버의 다른 DB에 **소유 role**로 붙는 endpoint."""

    def __init__(self, endpoint: Any, database: str) -> None:
        self.host = endpoint.host
        self.port = endpoint.port
        self.database = database
        self.username = transition.LEGACY_VIEW_OWNER
        self.password = OWNER_PASSWORD


def _transition_environ(endpoint: Any) -> dict[str, str]:
    """production session factory가 읽는 환경변수. 세 DB가 같은 서버라 하나로 족하다."""

    return {
        "POSTGRES_TRANSITION_HOST": endpoint.host,
        "POSTGRES_TRANSITION_PORT": str(endpoint.port),
        "POSTGRES_TRANSITION_USER": transition.LEGACY_VIEW_OWNER,
        "POSTGRES_TRANSITION_PASSWORD": OWNER_PASSWORD,
        # 격리 server는 port가 매번 다르다. 운영자가 `.env`에 넣을 값과 **같은 함수**로
        # 만들어, guard가 실제 endpoint를 고정하는지 그대로 확인한다.
        transition.ALLOWED_ENDPOINT_ENV_KEY: transition.endpoint_fingerprint(
            endpoint.host, endpoint.port
        ),
    }


def test_production_entrypoints_transition_three_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**backup CLI와 transition CLI를 둘 다 production `main()`으로** 완주시킨다.

    앞선 회귀는 transition만 `cli.main()`이었고 backup은 `backup_and_verify()`를 직접
    불렀다. approval도 helper가 만들었다(구현리뷰 15차 필수 1). 이제 공용과 같은 단일
    서버 3 DB 구조로 두 CLI를 모두 진입점으로 부른다.

    ```
    preflight(main) → backup CLI(main) → approval 인계 → transition apply(main)
    → 둘째 target 실패 → 재개 → no-op
    ```
    """

    import contextlib

    import psycopg
    import transition_public_postgres as cli
    import transition_sessions as ts
    from sqlalchemy import create_engine
    from sqlalchemy import text as _text

    archive_path = _final_archive()
    handler = ts.make_dispatching_handler(ts.load_profile_snapshots(archive_path))
    root = tmp_path / "backups"
    # production backup root는 실행 계정 단독 소유의 0700이다(계획 §16.1).
    root.mkdir(mode=0o700)
    change_ref = "GH-104"

    with postgres.one_off_postgres(
        database="fdc_e2e_hub", image=postgres.POSTGRES_RAG_IMAGE
    ) as endpoint:
        owners = _single_server_targets(endpoint)
        for database, owner in owners.items():
            with psycopg.connect(
                host=owner.host,
                port=owner.port,
                dbname=owner.database,
                user=owner.username,
                password=owner.password,
            ) as raw:
                with raw.cursor() as cursor:
                    _build_legacy_state(cursor, transition.TARGET_PROFILE[database])
                raw.commit()
            engine = create_engine(_url(owner))
            try:
                with engine.begin() as setup:
                    _build_operational_shape(setup, _text, database)
            finally:
                engine.dispose()

        # 환경변수만으로 production 기본 배선이 서게 한다. registry 주입을 쓰지 않는다
        # (구현보고 §72 4번).
        environ = _transition_environ(endpoint)
        environ[cli.ARCHIVE_ENV_KEY] = str(archive_path)
        for key, value in environ.items():
            monkeypatch.setenv(key, value)

        # --- preflight: production main(), 기본 builder ---
        assert cli.main(["--preflight"]) == cli.EXIT_OK

        inventories = {d: _read(owners[d], d) for d in transition.ORDERED_TARGETS}
        approval, bundle = _write_approval(tmp_path, inventories, change_ref)

        # --- backup: production main() ---
        backup_argv = [
            "--backup-root",
            str(root),
            "--change-ref",
            change_ref,
            "--preflight-bundle-sha256",
            bundle,
        ]
        for database in transition.ORDERED_TARGETS:
            backup_argv += ["--confirm-target", database]
        assert orchestrator.main(backup_argv, environ=environ) == 0
        for database in transition.ORDERED_TARGETS:
            assert (
                root / orchestrator.completion_name(database, change_ref)
            ).is_file(), database

        argv = _apply_argv(approval, root, change_ref)
        second = transition.ORDERED_TARGETS[1]

        def failing(connection: Any, database: str, inventory: Any) -> None:
            handler(connection, database, inventory)
            if database == second:
                raise ts.SessionError("MODE_CONTRACT_ERROR", 1)

        # --- apply: 둘째 target 실패 ---
        # 실패 주입만 registry로 넣는다. 나머지 배선은 기본값이다.
        assert cli.main(argv, registry=_with_handler(failing)) != cli.EXIT_OK
        after = {d: _read(owners[d], d) for d in transition.ORDERED_TARGETS}
        first = transition.ORDERED_TARGETS[0]
        assert transition.classify_target(after[first]) is (
            transition.BaseState.FINAL_ADOPTED
        )
        assert (root / transition.marker_name(first)).is_file()
        for database in transition.ORDERED_TARGETS[1:]:
            assert transition.classify_target(after[database]) is (
                transition.BaseState.BASE_LEGACY_EPOCH
            ), database
            assert not (root / transition.marker_name(database)).exists()

        # --- 재개 ---
        # 재개는 **주입 없이** 기본 배선으로 돈다 — handler도 환경변수에서 온다.
        assert cli.main(argv) == cli.EXIT_OK
        final_state = {d: _read(owners[d], d) for d in transition.ORDERED_TARGETS}
        for database, inventory in final_state.items():
            assert transition.classify_post_state(inventory) is (
                transition.BaseState.FINAL_ADOPTED
            ), database
            profile = transition.TARGET_PROFILE[database]
            assert (
                inventory.row_counts["action_history"]
                == (transition.FINAL_ACTION_ROWS[profile])
            ), database
            assert inventory.row_counts["trace_alarm_history"] == 138
            assert inventory.row_counts["summary_alarm_history"] == 51
            assert (root / transition.marker_name(database)).is_file()

        # --- 재실행 no-op ---
        before_tree = _tree_of(root)
        assert cli.main(argv, registry=_with_handler(_forbidden_handler)) == (
            cli.EXIT_OK
        )
        assert _tree_of(root) == before_tree
        for database in transition.ORDERED_TARGETS:
            assert transition.target_fingerprint(
                _read(owners[database], database)
            ) == transition.target_fingerprint(final_state[database]), database

        # --- B 산식 호출 범위: **실제 수집 경로**에서 센다(구현리뷰 21차 필수 1) ---
        # 소스 문자열 검사로는 "조건문을 남긴 채 아래에서 다시 부르는" 구현을 못 잡는다.
        # 공용 장애가 정확히 "이름만 보고 B SQL 실행"이었으므로 호출 자체를 센다.
        calls: dict[str, list[str]] = {d: [] for d in transition.ORDERED_TARGETS}
        seen: list[str] = []
        real_live = transition._rag_live_fingerprint
        real_embed = transition._rag_embedding_projection

        def counting(name: str, real: Any) -> Any:
            def _fn(connection: Any) -> Any:
                seen.append(name)
                return real(connection)

            return _fn

        transition._rag_live_fingerprint = counting("live", real_live)
        transition._rag_embedding_projection = counting("embedding", real_embed)
        try:
            for database in transition.ORDERED_TARGETS:
                seen.clear()
                _read(owners[database], database)
                calls[database] = list(seen)
        finally:
            transition._rag_live_fingerprint = real_live
            transition._rag_embedding_projection = real_embed

        for database in transition.B_MANAGED_RAG_TARGETS:
            assert calls[database] == ["live", "embedding"], database
        # B 관리 밖 target에서는 **0회**. 여기서 부르면 공용에서 UndefinedColumn이 난다.
        assert calls["kosa_text2sql"] == []

        # --- closure: production main(), 실제 producer artifact로 사후 증적 ---
        closure_argv = [
            "--closure",
            "--approval",
            str(approval),
            "--backup-root",
            str(root),
            "--change-ref",
            change_ref,
        ]
        assert cli.main(closure_argv) == cli.EXIT_OK
        closure_path = root / transition.closure_name(change_ref)
        payload = json.loads(closure_path.read_text(encoding="utf-8"))
        transition.validate_closure_schema(payload)
        assert payload["approval_sha256"] == cli._file_sha256(approval)
        assert payload["backup_root_mode"] == "0700"
        for database in transition.ORDERED_TARGETS:
            assert payload["committed_marker_sha256_by_target"][database] == (
                cli._file_sha256(root / transition.marker_name(database))
            )

        # 재실행은 no-op다. 팀에 제출된 hash가 조용히 바뀌면 안 된다.
        before_closure = closure_path.read_bytes()
        assert cli.main(closure_argv) == cli.EXIT_OK
        assert closure_path.read_bytes() == before_closure

        # 증적 하나가 사후에 바뀌면 closure만 막힌다. 전환 결과는 그대로다.
        victim = root / transition.archive_name(
            transition.ORDERED_TARGETS[0], change_ref
        )
        original = victim.read_bytes()
        victim.write_bytes(b"tampered")
        closure_path.unlink()
        assert cli.main(closure_argv) == cli.EXIT_MISMATCH
        assert not closure_path.exists()
        victim.write_bytes(original)
        for database in transition.ORDERED_TARGETS:
            assert (root / transition.marker_name(database)).is_file(), database

    _ = contextlib


def _with_handler(handler: Any) -> dict[str, Any]:
    """기본 배선에 handler만 바꿔 끼운다. session factory는 환경변수에서 온다."""

    import transition_public_postgres as cli
    import transition_sessions as ts

    return {**ts.build_public_sessions(), "handler": handler, "_cli": cli}


def _tree_of(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.iterdir())
    }


def _forbidden_handler(connection: Any, database: str, inventory: Any) -> None:
    raise AssertionError(f"이미 final인 {database}에 handler를 불렀다")


# ---------------------------------------------------------------------------
# 구현리뷰 15차 권장 1 — 120초 예산을 실측한다
# ---------------------------------------------------------------------------

#: 계측 결과를 남길 파일. 값이 아니라 **근거**를 보고서에 옮기기 위한 것이다.
BUDGET_REPORT_ENV_KEY = "POSTGRES_TRANSITION_BUDGET_REPORT"


def test_transaction_lock_hold_time_is_measured(tmp_path: Path) -> None:
    """target별 `lock 취득 → commit 직전` 구간을 production 경로에서 잰다.

    `TRANSACTION_BUDGET_SECONDS = 120`은 지금까지 근거 없는 값이었다(7차부터 이월).
    `assert_within_budget()`이 commit 직전에 불리므로 그 시점의 경과가 곧 lock 보유
    시간이다(구현리뷰 15차 권장 1).
    """

    import os

    import psycopg
    import transition_public_postgres as cli
    import transition_sessions as ts
    from sqlalchemy import create_engine
    from sqlalchemy import text as _text

    archive_path = _final_archive()
    handler = ts.make_dispatching_handler(ts.load_profile_snapshots(archive_path))
    root = tmp_path / "backups"
    # production backup root는 실행 계정 단독 소유의 0700이다(계획 §16.1).
    root.mkdir(mode=0o700)
    change_ref = "GH-104"

    elapsed: list[float] = []
    real_budget = transition.assert_within_budget

    def measuring(started: float, *, now: float) -> None:
        elapsed.append(now - started)
        return real_budget(started, now=now)

    with postgres.one_off_postgres(
        database="fdc_e2e_budget", image=postgres.POSTGRES_RAG_IMAGE
    ) as endpoint:
        owners = _single_server_targets(endpoint)
        for database, owner in owners.items():
            with psycopg.connect(
                host=owner.host,
                port=owner.port,
                dbname=owner.database,
                user=owner.username,
                password=owner.password,
            ) as raw:
                with raw.cursor() as cursor:
                    _build_legacy_state(cursor, transition.TARGET_PROFILE[database])
                raw.commit()
            engine = create_engine(_url(owner))
            try:
                with engine.begin() as setup:
                    _build_operational_shape(setup, _text, database)
            finally:
                engine.dispose()

        environ = _transition_environ(endpoint)
        sessions = ts.build_public_sessions(environ)
        inventories = {d: _read(owners[d], d) for d in transition.ORDERED_TARGETS}
        approval, bundle = _write_approval(tmp_path, inventories, change_ref)

        backup_argv = [
            "--backup-root",
            str(root),
            "--change-ref",
            change_ref,
            "--preflight-bundle-sha256",
            bundle,
        ]
        for database in transition.ORDERED_TARGETS:
            backup_argv += ["--confirm-target", database]
        assert orchestrator.main(backup_argv, environ=environ) == 0

        transition.assert_within_budget = measuring  # type: ignore[assignment]
        try:
            assert (
                cli.main(
                    _apply_argv(approval, root, change_ref),
                    registry={**sessions, "handler": handler},
                )
                == cli.EXIT_OK
            )
        finally:
            transition.assert_within_budget = real_budget  # type: ignore[assignment]

    assert len(elapsed) == len(transition.ORDERED_TARGETS)
    worst = max(elapsed)
    # 실측이 예산 안이어야 한다. 넘으면 예산이 잘못됐거나 전환이 느려진 것이다.
    assert worst < transition.TRANSACTION_BUDGET_SECONDS

    report = {
        "per_target_seconds": [round(value, 3) for value in elapsed],
        "worst_seconds": round(worst, 3),
        "budget_seconds": transition.TRANSACTION_BUDGET_SECONDS,
        "headroom_ratio": round(transition.TRANSACTION_BUDGET_SECONDS / worst, 1),
    }
    destination = os.environ.get(BUDGET_REPORT_ENV_KEY, "").strip()
    if destination:
        Path(destination).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("BUDGET_MEASUREMENT " + json.dumps(report))
