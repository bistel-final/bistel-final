"""공용 전환의 session adapter와 data handler(`V5-CM-2.6`).

구현리뷰 9차 필수 3이 요구한 배선이다. 이 모듈은 **연결 정보를 스스로 만들지 않는다** —
환경변수에서 읽고, 없으면 열지 않는다. 그래서 자격증명 없이 import해도 공용 DB에 닿지
않는다(`PUBLIC_SESSIONS`는 `build_public_sessions()`를 부른 쪽에서만 채워진다).

세 가지를 고정한다.

1. **read-only session**은 `default_transaction_read_only=on`으로 열어 실수로 쓰는
   경로를 서버가 막게 한다.
2. **mutating session**은 REPEATABLE READ로 열고 autocommit을 쓰지 않는다. mutex
   획득·transaction 경계·해제는 호출자(`_target_session`)가 소유한다.
3. **handler**는 schema/View만 바꾸고 DDL 순서는 `transition_statements()`가 정한다.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import postgres_transition as transition  # noqa: E402
import rehearsal_profile_loader as loader  # noqa: E402

EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

#: DSN을 구성할 환경변수. 값 자체는 어디에도 직렬화하지 않는다.
DSN_ENV_KEYS = (
    "POSTGRES_TRANSITION_HOST",
    "POSTGRES_TRANSITION_PORT",
    "POSTGRES_TRANSITION_USER",
    "POSTGRES_TRANSITION_PASSWORD",
)

#: DSN 4종에 더해 **허용 endpoint 지정**까지 있어야 접속을 만든다(구현리뷰 16차 필수 1).
REQUIRED_ENV_KEYS = DSN_ENV_KEYS + (transition.ALLOWED_ENDPOINT_ENV_KEY,)

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"


class SessionError(Exception):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _require_env(environ: Mapping[str, str]) -> dict[str, str]:
    missing = [key for key in DSN_ENV_KEYS if not environ.get(key, "").strip()]
    if missing:
        # 어떤 키가 비었는지까지만 알린다. 값은 싣지 않는다.
        raise SessionError("APPROVAL_REQUIRED", EXIT_CONFIRM_REQUIRED)
    return {key: environ[key].strip() for key in DSN_ENV_KEYS}


def _engine(database: str, *, read_only: bool, environ: Mapping[str, str]) -> Any:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    if database not in transition.TARGET_PROFILE:
        raise SessionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    env = _require_env(environ)
    host = env["POSTGRES_TRANSITION_HOST"]
    # backup과 **같은** parser다. `int()`를 그대로 쓰면 `not-a-port`가 raw
    # `ValueError`로 새어나가 `INTERNAL_ERROR`가 된다(구현리뷰 17차 필수 3).
    rejection = transition.port_rejection(env["POSTGRES_TRANSITION_PORT"])
    if rejection is not None:
        raise SessionError(*rejection)
    port = int(env["POSTGRES_TRANSITION_PORT"])
    # backup과 **같은** validator로, engine 생성 전에 서버 신원을 고정한다
    # (구현리뷰 16차 필수 1).
    rejection = transition.endpoint_rejection(host, port, environ=environ)
    if rejection is not None:
        raise SessionError(*rejection)
    url = URL.create(
        "postgresql+psycopg",
        username=env["POSTGRES_TRANSITION_USER"],
        password=env["POSTGRES_TRANSITION_PASSWORD"],
        host=host,
        port=port,
        database=database,
    )
    connect_args: dict[str, Any] = {}
    if read_only:
        connect_args["options"] = READ_ONLY_OPTIONS
    return create_engine(
        url,
        isolation_level="REPEATABLE READ",
        connect_args=connect_args,
        pool_pre_ping=True,
        # 예외·log에 DSN이 실리지 않게 한다.
        hide_parameters=True,
    )


def read_only_session(
    database: str, *, environ: Mapping[str, str] | None = None
) -> Any:
    """preflight·비대상 확인용. 서버가 쓰기를 거부하도록 열린다."""

    source = os.environ if environ is None else environ

    @contextlib.contextmanager
    def opened() -> Iterator[Any]:
        engine = _engine(database, read_only=True, environ=source)
        try:
            with engine.connect() as connection:
                yield connection
        finally:
            engine.dispose()

    return opened()


def mutating_session(database: str, *, environ: Mapping[str, str] | None = None) -> Any:
    """전환용. transaction 경계와 mutex는 호출자가 소유한다."""

    source = os.environ if environ is None else environ

    @contextlib.contextmanager
    def opened() -> Iterator[Any]:
        engine = _engine(database, read_only=False, environ=source)
        try:
            with engine.connect() as connection:
                yield connection
        finally:
            engine.dispose()

    return opened()


def build_public_sessions(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Callable[[str], Any]]:
    """`PUBLIC_SESSIONS`에 넣을 factory 2종을 만든다.

    이 함수를 부르지 않으면 registry는 비어 있고 `--apply`는 `MODE_NOT_WIRED`다.
    자격증명이 없으면 factory를 만들어도 첫 호출에서 `APPROVAL_REQUIRED`로 멈춘다.
    """

    source = os.environ if environ is None else environ
    return {
        "read_only": lambda database: read_only_session(database, environ=source),
        "mutating": lambda database: mutating_session(database, environ=source),
    }


#: 데이터 교체는 이 순서로 지운다. FK child가 먼저다.
DELETE_ORDER: tuple[str, ...] = tuple(reversed(loader.LOAD_ORDER))


def schema_statements(connection: Any) -> list[str]:
    """이 target에 보낼 DDL. 현재 View가 pin된 legacy 정의일 때만 만든다."""

    return transition.transition_statements(_verified_definition(connection))


def _verified_definition(connection: Any) -> str:
    definition = _legacy_definition(connection)
    if transition.view_fingerprint(definition) != transition.LEGACY_VIEW_SHA256:
        raise SessionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    return definition


def _compatibility_view(connection: Any) -> str:
    """지울 View의 정의에서 호환 View SQL을 미리 만들어 둔다.

    DROP 뒤에는 정의를 읽을 수 없다.
    """

    return transition.build_compatibility_view_sql(_verified_definition(connection))


def _assert_view_acl(connection: Any) -> None:
    """같은 transaction에서 owner·ACL·comment가 pin 값과 같은지 확인한다."""

    from sqlalchemy import text

    rows = transition._rows(
        connection.execute(
            text(
                "SELECT pg_get_userbyid(c.relowner) AS owner, "
                "c.relacl::text AS acl, "
                "obj_description(c.oid, 'pg_class') AS comment "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = :view"
            ),
            {"view": transition.LEGACY_VIEW},
        )
    )
    row = rows[0] if rows else None
    if row is None:
        raise SessionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    if row["owner"] != transition.LEGACY_VIEW_OWNER:
        raise SessionError("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
    if str(row["acl"]) != transition.LEGACY_VIEW_ACL:
        raise SessionError("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
    if row["comment"] != transition.LEGACY_VIEW_COMMENT:
        raise SessionError("MODE_CONTRACT_ERROR", EXIT_MISMATCH)


def _require_empty(connection: Any, tables: Sequence[str]) -> None:
    """ALTER 직전에 그 table이 실제로 비었는지 본다(구현리뷰 11차 필수 2)."""

    from sqlalchemy import text

    for table in sorted(tables):
        # 이름은 `WAFER_ALTER_TABLES` 상수에서만 온다.
        remaining = connection.execute(
            text(f'SELECT count(*) AS n FROM public."{table}"')
        ).scalar()
        if int(remaining or 0) != 0:
            raise SessionError("MODE_CONTRACT_ERROR", EXIT_MISMATCH)


def make_transition_handler(
    csv_bodies: Mapping[str, bytes],
    columns_by_table: Mapping[str, tuple[str, ...]],
    tables: Sequence[str],
    profile: str,
) -> Callable[[Any, str, transition.TargetInventory], None]:
    """한 target을 legacy → final로 바꾸는 handler를 만든다.

    **DDL과 데이터 교체를 같은 transaction에서 한다**(계획 §8). schema만 바꾸면 legacy
    행 수(TRACE 126 등)가 남아 같은 transaction의 `classify_post_state()`를 통과할 수
    없다 — 즉 legacy target이 성공할 수 없었다(구현리뷰 10차 필수 3).

    순서는 계획 §8 그대로다.

    ```
    DROP VIEW → base 9 DELETE(FK 역순) → 빈 table 4개 wafer ALTER → COPY → compat View
    ```

    **ALTER는 DELETE 뒤에 온다.** 채워진 table을 먼저 바꾸면 곧 지울 행을 rewrite하며
    WAL과 lock 보유 시간을 늘린다(구현리뷰 11차 필수 2).

    `csv_bodies`는 호출자가 hash까지 검증한 최종 ZIP 스냅샷이다.
    """

    load_handler, _postcheck = loader.make_load_handlers(
        csv_bodies=csv_bodies,
        columns_by_table=columns_by_table,
        tables=tables,
        profile=profile,
        error_factory=lambda reason, exit_code=EXIT_MISMATCH: SessionError(
            reason, exit_code
        ),
    )
    ordered_delete = tuple(name for name in DELETE_ORDER if name in tables)

    def handler(
        connection: Any, database: str, inventory: transition.TargetInventory
    ) -> None:
        from psycopg import sql
        from sqlalchemy import text

        if inventory.database != database:
            raise SessionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
        compat_view = _compatibility_view(connection)

        # 1. legacy View만 지운다. 이게 `wafer` ALTER를 막고 있다.
        connection.execute(text(transition.DROP_VIEW_SQL))

        # 2. base 9를 FK 역순으로 비운다.
        driver = connection.connection
        for table in ordered_delete:
            # 식별자를 문자열로 잇지 않는다. 이름은 상수에서만 온다.
            with driver.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {table}").format(
                        table=sql.Identifier("public", table)
                    )
                )

        # 3. **빈** table 4개만 ALTER한다.
        _require_empty(connection, transition.WAFER_ALTER_TABLES)
        for statement in transition.alter_wafer_statements():
            connection.execute(text(statement))

        # 4. 최종 CSV를 적재하고 5. 호환 View를 되살린다.
        load_handler(connection, None)
        connection.execute(text(compat_view))

        # 6. DROP으로 사라진 owner·ACL을 계획 §8.1대로 명시 복원한다.
        for statement in transition.compatibility_view_acl_statements():
            connection.execute(text(statement))
        _assert_view_acl(connection)

    return handler


def transition_handler(
    connection: Any, database: str, inventory: transition.TargetInventory
) -> None:
    """데이터 없이 schema/View만 바꾼다.

    **정상 전환 경로가 아니다.** 격리 환경에서 DDL 순서만 확인할 때 쓴다. 실제 전환은
    `make_transition_handler()`로 데이터까지 같은 transaction에서 바꾼다.
    """

    from sqlalchemy import text

    if inventory.database != database:
        raise SessionError("TARGET_NOT_ALLOWED", EXIT_USAGE)
    for statement in schema_statements(connection):
        connection.execute(text(statement))


def _legacy_definition(connection: Any) -> str:
    from sqlalchemy import text

    row = connection.execute(
        text(
            "SELECT pg_get_viewdef(c.oid, true) AS body FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :view"
        ),
        {"view": transition.LEGACY_VIEW},
    ).scalar()
    if not row:
        raise SessionError("TARGET_STATE_UNSUPPORTED", EXIT_MISMATCH)
    return str(row)


# ---------------------------------------------------------------------------
# production 배선 — 최종 ZIP·manifest v4에서 handler 입력을 만든다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileSnapshot:
    """한 profile의 적재 입력. 한 번 검증하고 그대로 재사용한다."""

    profile: str
    tables: tuple[str, ...]
    csv_bodies: Mapping[str, bytes]
    columns_by_table: Mapping[str, tuple[str, ...]]


def load_profile_snapshots(
    archive_path: Path,
    *,
    manifest_path: Path | None = None,
    intake_path: Path | None = None,
) -> dict[str, ProfileSnapshot]:
    """hash 고정 최종 ZIP에서 runtime·evaluation 스냅샷을 만든다.

    확인 순서는 다음과 같다(구현리뷰 12차 필수 2).

    1. epoch·manifest·intake의 schema와 상호 SHA를
       `rebuild_runner.validate_artifacts()`로 교차 검증한다.
    2. **archive 전체 SHA-256**을 streaming으로 읽어 pin 값과 대조한다. 선택 CSV만 보면
       extra member를 넣어 재포장한 ZIP도 통과한다 — 실제로 통과했다.
    3. ZIP member set이 intake가 선언한 것과 정확히 같은지, duplicate가 없는지 본다.
    4. 그다음에야 table별 column·file id와 member SHA를 확인하고 본문을 돌려준다.
    """

    import json
    import zipfile

    import rebuild_runner

    rebuild_runner.validate_artifacts(
        rebuild_runner.ArtifactPaths(
            **{
                key: value
                for key, value in (
                    ("source_manifest", manifest_path),
                    ("intake", intake_path),
                )
                if value is not None
            }
        )
    )
    assert_archive_is_pinned(Path(archive_path))

    manifest = json.loads(
        (manifest_path or rebuild_runner.SOURCE_MANIFEST_PATH).read_text(
            encoding="utf-8"
        )
    )
    intake = json.loads(
        (intake_path or rebuild_runner.INTAKE_PATH).read_text(encoding="utf-8")
    )

    def fail(reason: str, exit_code: int = EXIT_USAGE) -> BaseException:
        return SessionError(reason, exit_code)

    manifest_tables = loader.validate_manifest_tables(manifest.get("tables"), fail)
    members = loader.validate_intake_members(intake.get("selected_members"), fail)

    snapshots: dict[str, ProfileSnapshot] = {}
    with zipfile.ZipFile(archive_path) as archive:
        assert_members_match_intake(archive, members)
        for profile in ("runtime", "evaluation"):
            tables = loader.select_tables(manifest_tables, profile, fail)
            bodies = loader.verified_csv_bodies(
                archive, manifest_tables, members, tables, fail
            )
            snapshots[profile] = ProfileSnapshot(
                profile=profile,
                tables=tables,
                csv_bodies=bodies,
                columns_by_table={
                    table: tuple(manifest_tables[table]["columns"]) for table in tables
                },
            )
    return snapshots


def assert_archive_is_pinned(archive_path: Path) -> None:
    """archive **전체** SHA-256이 pin 값과 같은지 본다.

    선택 CSV만 확인하면 `unexpected-extra-member.txt`를 넣어 재포장한 ZIP도 승인된다.
    작업계획 §2·§13의 "source 정본은 최종 ZIP과 manifest v4뿐"이 성립하려면 archive
    자체를 고정해야 한다(구현리뷰 12차 필수 2).
    """

    import hashlib

    import intake_final_zip

    if not archive_path.is_file() or archive_path.is_symlink():
        raise SessionError("ARCHIVE_INVALID", EXIT_USAGE)
    digest = hashlib.sha256()
    with archive_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != intake_final_zip.EXPECTED_ARCHIVE_SHA256:
        raise SessionError("ARCHIVE_MISMATCH", EXIT_MISMATCH)


def assert_members_match_intake(
    archive: Any, members: Mapping[str, Mapping[str, Any]]
) -> None:
    """ZIP member가 intake 선언과 정확히 같고 duplicate가 없는지 본다."""

    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SessionError("ARCHIVE_INVALID", EXIT_USAGE)
    if not set(members).issubset(set(names)):
        raise SessionError("ARCHIVE_MISMATCH", EXIT_MISMATCH)


def make_dispatching_handler(
    snapshots: Mapping[str, ProfileSnapshot],
) -> Callable[[Any, str, transition.TargetInventory], None]:
    """target → profile을 exact 확인해 알맞은 handler로 보낸다.

    runtime 두 target과 evaluation 한 target이 서로 다른 CSV 집합을 쓴다. 잘못 붙으면
    행 수가 어긋나 `classify_post_state()`에서 rollback되지만, 그 전에 여기서 막는다.
    """

    handlers = {
        profile: make_transition_handler(
            csv_bodies=snapshot.csv_bodies,
            columns_by_table=snapshot.columns_by_table,
            tables=snapshot.tables,
            profile=profile,
        )
        for profile, snapshot in snapshots.items()
    }

    def dispatch(
        connection: Any, database: str, inventory: transition.TargetInventory
    ) -> None:
        profile = transition.TARGET_PROFILE.get(database)
        if profile is None or profile != inventory.profile:
            raise SessionError("PROFILE_MISMATCH", EXIT_MISMATCH)
        handler = handlers.get(profile)
        if handler is None:
            raise SessionError("MODE_NOT_WIRED", EXIT_USAGE)
        handler(connection, database, inventory)

    return dispatch


def build_public_wiring(
    archive_path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """session factory 2종 + final data handler.

    `archive_path`가 없으면 handler를 배선하지 않는다 — preflight는 되고 apply는
    `MODE_NOT_WIRED`로 멈춘다.
    """

    wiring: dict[str, Any] = dict(build_public_sessions(environ))
    if archive_path is not None:
        wiring["handler"] = make_dispatching_handler(
            load_profile_snapshots(Path(archive_path))
        )
    return wiring
