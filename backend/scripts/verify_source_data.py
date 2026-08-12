"""공용 서버의 기준·생산 데이터를 읽기 전용으로 검증한다. (CM-0.4)

원본 데이터를 재적재·수정하지 않는다.
근거: 시스템설계서 13.2.1 / 01-project-rules.md 1절 3번.

Text2SQL allowlist 16개 base table(03-database-rules.md 8절)의 행 수와
canonical content hash를 `infra/bootstrap/source-data-manifest.json` 의
단일 `source.tables` 기준값과 비교한다. runtime(kosa_agent/kosa)과
evaluation(kosa_text2sql)은 fresh bootstrap 시점에 같은 원본을 적재하므로
(설계 13.2.1 3·8단계) 같은 기준값 하나를 공유한다.

`001_agent_runtime.sql`은 kosa_agent 의 action_history 에만 컬럼을 추가한다
(created_by_agent_run_id·send_started_at·send_attempt_count, 설계 3.2). 이
검증은 migration 전후 어느 시점에 실행되어도 같은 결과가 나와야 하므로,
manifest 생성 시점의 "원본 컬럼" 목록을 함께 저장해두고 검증 시에는 그
컬럼만 조회한다 — migration이 나중에 추가한 컬럼은 여기서 무시하고
`verify_migrations.py` 가 별도로 검증한다.

사용:
    # 최초 1회, 사내망에서 실제 값을 읽어 manifest 를 만든다 (반드시 --confirm)
    python backend/scripts/verify_source_data.py --profile runtime --generate --confirm

    # 이후에는 검증만 한다 (기본 동작)
    python backend/scripts/verify_source_data.py --profile runtime
    python backend/scripts/verify_source_data.py --profile evaluation

    # --confirm 없이 --generate 만 실행하면 무엇이 바뀌는지 미리보기만 한다
    python backend/scripts/verify_source_data.py --profile runtime --generate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, Engine, create_engine, make_url
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "infra" / "bootstrap" / "source-data-manifest.json"

sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

# 설계 9.2 / docs/ai-context/03-database-rules.md 8절 — allowlist 16종.
ALLOWLIST_TABLES: tuple[str, ...] = (
    "dim_process_step",
    "dim_recipe",
    "dim_recipe_step",
    "dim_equipment",
    "dim_chamber",
    "dim_sensor",
    "dim_metrology_item",
    "fdc_rule",
    "code_fault",
    "code_action",
    "lot_history",
    "fdc_trace",
    "fdc_summary",
    "fdc_alarm",
    "metrology",
    "action_history",
)

# nl_query_log 는 평가 이력이 계속 쌓이는 테이블이라 allowlist 16종에 없고
# 이 hash 비교 대상도 아니다 (설계 9.5 문단 3, 13.2.1 8번).
#
# action_history 는 두 DB 모두 fresh bootstrap 시점 기준 10행이다(설계 13.2.1
# 3·8단계 — 두 DB에 같은 03_load_data.sql 을 적재한다). "Agent E2E runtime
# 테이블 초기화 후 입력 기준 15개 재검증"은 격리된 E2E 전용 DB를 다루는 별도
# 절차(reset_agent_e2e_db.py, C-8.2/CM-3.1 소관)이며 이 스크립트의 대상이
# 아니다.

# v2: 테이블별 "원본 컬럼" 목록을 manifest 에 저장하고 검증 시 그 컬럼만
# 조회하도록 바꿨다. v1 은 대상 DB에 존재하는 컬럼 전부를 해시했는데,
# 001_agent_runtime.sql 적용 후의 kosa_agent 에서는 이 방식이 항상 실패한다.
MANIFEST_FORMAT_VERSION = 2
HASH_ALGORITHM = "sha256-canonical-json-nfc-codepoint-v1"
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# profile 은 "어떤 DB 에 연결하는가"만 결정한다. 기준값은 runtime·evaluation
# 모두 manifest 의 단일 source.tables 를 공유한다.
_PROFILE_URL_ENV = {
    "runtime": "TEXT2SQL_DATABASE_URL",
    "evaluation": "TEXT2SQL_EVAL_DATABASE_URL",
}

# 대상 DB명이 프로파일과 실제로 대응하는지 검증한다. "kosa"는 D-1.3(pool
# factory) 이전, 공용 서버가 아직 kosa_agent/kosa_text2sql 로 분리되지 않은
# 과도기 DB명이다 — 분리 후에는 kosa_agent 만 남겨도 된다. evaluation 은
# 과도기 예외 없이 kosa_text2sql 만 허용한다.
_PROFILE_EXPECTED_DB: dict[str, frozenset[str]] = {
    "runtime": frozenset({"kosa_agent", "kosa"}),
    "evaluation": frozenset({"kosa_text2sql"}),
}


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _canonicalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """문자열은 NFC 정규화한다(설계 3.2.5 규칙과 동일)."""
    canonical: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str):
            value = unicodedata.normalize("NFC", value)
        canonical[key] = value
    return canonical


def _hash_canonical_rows(rows: list[dict[str, Any]]) -> str:
    """행 목록을 canonical hash 로 만든다.

    PostgreSQL `ORDER BY` 에 정렬을 맡기지 않는다. DB의 locale/collation이
    다르면 문자열 데이터가 같아도 정렬 순서가 달라져 같은 내용이 다른 해시가
    될 수 있다. 각 행을 canonical JSON 문자열로 만든 뒤 Python 문자열
    codepoint 순서로 재정렬해 DB·플랫폼과 무관하게 결정론적으로 만든다.
    """
    row_payloads = sorted(
        json.dumps(
            _canonicalize_row(dict(row)),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=_json_default,
        )
        for row in rows
    )
    combined = "[" + ",".join(row_payloads) + "]"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def discover_columns(connection: Connection, table: str) -> list[str]:
    columns = (
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table "
                "ORDER BY ordinal_position"
            ),
            {"table": table},
        )
        .scalars()
        .all()
    )
    if not columns:
        raise RuntimeError(f"public 스키마에서 테이블을 찾을 수 없습니다: {table}")
    return list(columns)


def _ensure_columns_exist(
    connection: Connection, table: str, columns: list[str]
) -> None:
    """migration이 컬럼을 추가하는 건 무시해도 되지만, manifest가 기대하는
    원본 컬럼이 실제로 없어졌다면(오타·컬럼 삭제 등) 그건 진짜 문제다."""
    existing = set(discover_columns(connection, table))
    missing = [c for c in columns if c not in existing]
    if missing:
        raise RuntimeError(
            f"{table}: manifest 원본 컬럼이 대상 DB에 없습니다: {missing}"
        )


def canonical_table_hash(
    connection: Connection, table: str, columns: list[str]
) -> tuple[int, str]:
    identifiers = [table, *columns]
    invalid = [
        name for name in identifiers if not SQL_IDENTIFIER_PATTERN.fullmatch(name)
    ]
    if invalid:
        raise ValueError(f"SQL 식별자 형식이 잘못됐습니다: {invalid}")

    column_list = ", ".join(f'"{c}"' for c in columns)
    query = text(f'SELECT {column_list} FROM "{table}"')
    rows = connection.execute(query).mappings().all()
    dict_rows = [dict(row) for row in rows]
    return len(dict_rows), _hash_canonical_rows(dict_rows)


# ---------------------------------------------------------------------
# 프로파일별 연결 — D-1.3(pool factory)이 아직 없으므로 이 스크립트가 직접
# 최소한의 읽기 전용 연결을 구성한다. 최종 설계의 TEXT2SQL_*_URL 이 생기면
# 그쪽을 그대로 쓰고, 지금 당장은 기존 POSTGRES_*/READONLY_* 로 동작한다.
# ---------------------------------------------------------------------


def resolve_profile_url(profile: str) -> str | URL:
    explicit = os.getenv(_PROFILE_URL_ENV[profile])
    if explicit:
        return explicit

    if profile == "runtime":
        from app.common.config import (
            POSTGRES_DB,
            POSTGRES_HOST,
            POSTGRES_PORT,
            READONLY_PASSWORD,
            READONLY_USER,
        )

        return URL.create(
            drivername="postgresql+psycopg",
            username=READONLY_USER,
            password=READONLY_PASSWORD,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
        )

    raise RuntimeError(
        f"{_PROFILE_URL_ENV[profile]} 이 설정돼 있지 않습니다. evaluation 프로파일은 "
        "D-1.3(process별 pool factory)에서 kosa_text2sql 연결이 추가된 뒤 사용할 수 "
        "있습니다. 그 전까지는 .env 에 TEXT2SQL_EVAL_DATABASE_URL 을 직접 넣어주세요."
    )


def describe_target(url: str | URL) -> dict[str, str]:
    """host 별칭·port·DB명만 남긴다 — 계정·비밀번호는 절대 포함하지 않는다."""
    try:
        parsed = url if isinstance(url, URL) else make_url(url)
        return {
            "host": str(parsed.host),
            "port": str(parsed.port),
            "database": str(parsed.database),
        }
    except Exception:
        return {"host": "<unknown>", "port": "<unknown>", "database": "<unknown>"}


def validate_target_database(profile: str, target: dict[str, str]) -> None:
    """DSN을 파싱만 하고 실제 대상 검증을 생략하면, evaluation DSN이 실수로
    운영 DB를 가리켜도 두 DB 내용이 같아 조용히 통과할 수 있다. 해시 계산
    전에 DB명 자체를 프로파일 기대값과 대조해 그 경로를 막는다."""
    allowed = _PROFILE_EXPECTED_DB[profile]
    if target["database"] not in allowed:
        raise RuntimeError(
            f"[{profile}] 대상 DB명이 예상과 다릅니다: {target['database']!r} "
            f"(허용: {sorted(allowed)}). .env 또는 {_PROFILE_URL_ENV[profile]} "
            "설정을 확인하세요."
        )


def build_engine(profile: str) -> Engine:
    url = resolve_profile_url(profile)
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


# ---------------------------------------------------------------------
# manifest 생성·검증
# ---------------------------------------------------------------------


def generate_manifest_tables(engine: Engine) -> dict[str, dict[str, Any]]:
    """--generate 는 그 순간 대상 DB에 존재하는 컬럼 전체를 "원본 컬럼"으로
    기록한다. 반드시 migration 적용 전(fresh 원본 또는 과도기 공용 서버)
    DB에 대해서만 실행해야 하는 이유다."""
    tables: dict[str, dict[str, Any]] = {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET statement_timeout = '30s'"))
        for table in ALLOWLIST_TABLES:
            columns = discover_columns(connection, table)
            row_count, digest = canonical_table_hash(connection, table, columns)
            tables[table] = {
                "columns": columns,
                "row_count": row_count,
                "content_hash": digest,
            }
    return tables


def validate_manifest_schema(tables: dict[str, Any]) -> list[str]:
    """v2 manifest 의 source.tables 형태를 DB 조회 전에 검증한다.

    columns 가 없거나 손상된 항목을 discover_columns 로 조용히 대체하면
    "원본 컬럼 고정"이라는 v2의 핵심 계약이 우회된다 — 그 fallback을 여기서
    아예 없앤다. 이 함수가 통과해야만 verify_manifest_tables 를 호출한다."""
    errors: list[str] = []

    actual_keys = set(tables)
    expected_keys = set(ALLOWLIST_TABLES)
    missing = expected_keys - actual_keys
    extra = actual_keys - expected_keys
    if missing:
        errors.append(f"테이블 누락: {sorted(missing)}")
    if extra:
        errors.append(f"허용되지 않은 테이블: {sorted(extra)}")

    hex64 = re.compile(r"^[0-9a-f]{64}$")
    for table in ALLOWLIST_TABLES:
        entry = tables.get(table)
        if entry is None:
            continue  # 이미 위에서 "테이블 누락"으로 보고했다

        columns = entry.get("columns")
        if not isinstance(columns, list) or not columns:
            errors.append(f"{table}: columns 가 없거나 비어 있습니다")
        elif not all(
            isinstance(c, str) and SQL_IDENTIFIER_PATTERN.fullmatch(c) for c in columns
        ):
            errors.append(f"{table}: columns 에 잘못된 SQL 식별자가 있습니다")
        elif len(columns) != len(set(columns)):
            errors.append(f"{table}: columns 에 중복이 있습니다")

        row_count = entry.get("row_count")
        row_count_ok = isinstance(row_count, int) and not isinstance(row_count, bool)
        if not row_count_ok or row_count < 0:
            errors.append(f"{table}: row_count 가 0 이상 정수가 아닙니다")

        content_hash = entry.get("content_hash")
        if not isinstance(content_hash, str) or not hex64.match(content_hash):
            errors.append(f"{table}: content_hash 형식이 잘못됐습니다")

    return errors


def verify_manifest_tables(
    engine: Engine, expected: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """manifest 에 기록된 원본 컬럼만 조회해 해시한다. migration이 나중에
    추가한 컬럼은 대상에 있어도 무시한다 — 그 컬럼·제약 검증은
    verify_migrations.py 의 몫이다.

    호출 전에 validate_manifest_schema 가 통과했다고 가정한다 — 여기서는
    더 이상 columns 누락에 대한 fallback을 두지 않는다."""
    tables: dict[str, dict[str, Any]] = {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET statement_timeout = '30s'"))
        for table in ALLOWLIST_TABLES:
            columns = expected[table]["columns"]
            _ensure_columns_exist(connection, table, columns)
            row_count, digest = canonical_table_hash(connection, table, columns)
            tables[table] = {
                "columns": columns,
                "row_count": row_count,
                "content_hash": digest,
            }
    return tables


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"manifest 가 없습니다: {MANIFEST_PATH}\n"
            "먼저 --generate --confirm 으로 기준값을 만들어 커밋해야 합니다."
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any]) -> None:
    """임시 파일에 먼저 쓰고 원자적으로 교체한다 — 쓰는 도중 중단돼도 기존
    manifest 가 반쪽짜리로 깨지지 않는다."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(MANIFEST_PATH)


def manifest_metadata_ok(manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("format_version") == MANIFEST_FORMAT_VERSION
        and manifest.get("hash_algorithm") == HASH_ALGORITHM
    )


def _describe_manifest_metadata(manifest: dict[str, Any]) -> str:
    version = manifest.get("format_version")
    algorithm = manifest.get("hash_algorithm")
    return f"{version!r}/{algorithm!r}"


def compare_tables(
    actual: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> list[str]:
    mismatches: list[str] = []
    for table in ALLOWLIST_TABLES:
        exp = expected.get(table)
        if exp is None:
            mismatches.append(f"{table}: manifest 에 기준값이 없습니다")
            continue
        act = actual[table]
        if exp.get("columns") is not None and act.get("columns") != exp.get("columns"):
            mismatches.append(f"{table}: 원본 컬럼 목록 불일치")
        if act["row_count"] != exp["row_count"]:
            mismatches.append(
                f"{table}: 행 수 불일치 "
                f"(기준 {exp['row_count']} / 실제 {act['row_count']})"
            )
        if act["content_hash"] != exp["content_hash"]:
            mismatches.append(f"{table}: content hash 불일치")
    return mismatches


def _do_generate(
    profile: str, actual: dict[str, dict[str, Any]], *, confirm: bool
) -> int:
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    existing = manifest.get("source", {}).get("tables")
    metadata_ok = manifest_metadata_ok(manifest)
    if existing:
        mismatches = compare_tables(actual, existing)
        if not mismatches and metadata_ok:
            print(f"[{profile}] 기존 manifest 와 동일합니다. 변경하지 않았습니다.")
            return 0
        preview = list(mismatches)
        if not metadata_ok:
            old = _describe_manifest_metadata(manifest)
            new = f"{MANIFEST_FORMAT_VERSION!r}/{HASH_ALGORITHM!r}"
            preview.append(f"manifest 형식이 낡았습니다 ({old} -> {new})")
        verb = "변경"
    else:
        preview = [f"{t}: {actual[t]['row_count']}행 (신규)" for t in ALLOWLIST_TABLES]
        verb = "신규 생성"

    # 최초 생성이든 기존 값·메타데이터 변경이든 항상 사람이 확인한 뒤에만
    # 쓴다 — 오염된 DB 값이 확인 없이 바로 기준값으로 굳는 경로를 막는다.
    if not confirm:
        print(
            f"[{profile}] {verb} 대상입니다. 아무 것도 쓰지 않았습니다. "
            "아래 값이 맞으면 --confirm 을 추가해 다시 실행하세요."
        )
        for line in preview:
            print(f"  - {line}")
        return 3

    manifest["format_version"] = MANIFEST_FORMAT_VERSION
    manifest["hash_algorithm"] = HASH_ALGORITHM
    manifest["source"] = {"tables": actual}
    save_manifest(manifest)
    print(f"[{profile}] manifest 갱신 완료: {MANIFEST_PATH}")
    for table in ALLOWLIST_TABLES:
        print(f"  {table}: {actual[table]['row_count']}행")
    return 0


def _do_verify(
    profile: str, actual: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> int:
    mismatches = compare_tables(actual, expected)
    if mismatches:
        print(f"[{profile}] 불일치 {len(mismatches)}건:")
        for line in mismatches:
            print(f"  - {line}")
        return 1

    print(f"[{profile}] 전체 {len(ALLOWLIST_TABLES)}개 테이블 일치.")
    print("재적재 없이 검증만 했습니다.")
    return 0


def run(profile: str, *, generate: bool, confirm: bool) -> int:
    engine = build_engine(profile)
    target = describe_target(resolve_profile_url(profile))
    validate_target_database(profile, target)
    print(
        f"[{profile}] 대상: host={target['host']} "
        f"port={target['port']} db={target['database']}"
    )

    try:
        if generate:
            actual = generate_manifest_tables(engine)
            return _do_generate(profile, actual, confirm=confirm)

        manifest = load_manifest()
        expected = manifest.get("source", {}).get("tables")
        if not expected:
            print(
                "manifest 에 기준값이 없습니다. --generate --confirm 먼저 실행하세요."
            )
            return 2

        if not manifest_metadata_ok(manifest):
            old = _describe_manifest_metadata(manifest)
            new = f"{MANIFEST_FORMAT_VERSION!r}/{HASH_ALGORITHM!r}"
            print(f"[{profile}] manifest 형식이 다릅니다 ({old} -> {new}).")
            print("데이터가 바뀐 게 아니라 manifest 형식이 바뀐 것일 수 있습니다.")
            print("--generate --confirm 으로 재생성하세요.")
            return 4

        schema_errors = validate_manifest_schema(expected)
        if schema_errors:
            print(f"[{profile}] manifest 스키마가 올바르지 않습니다:")
            for line in schema_errors:
                print(f"  - {line}")
            return 5

        actual = verify_manifest_tables(engine, expected)
        return _do_verify(profile, actual, expected)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(_PROFILE_URL_ENV), required=True)
    parser.add_argument(
        "--generate",
        action="store_true",
        help="DB 값을 읽어 manifest 를 (재)생성한다. 기준값 작성 모드다.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="미리보기가 아니라 실제로 manifest 를 쓴다(최초 생성 포함).",
    )
    args = parser.parse_args()

    try:
        exit_code = run(args.profile, generate=args.generate, confirm=args.confirm)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(2)
    except SQLAlchemyError:
        print("오류: DB 연결/조회에 실패했습니다(접속 정보 비공개).", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"오류: manifest JSON 형식이 잘못됐습니다: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
