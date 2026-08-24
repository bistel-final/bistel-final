"""최종 profile manifest **발급 runner** (`V5-CM-1.8` 묶음 2·3).

candidate를 만드는 것은 `final_profile_manifests`가, 그것을 저장소 active manifest로
**교체**하는 것은 이 모듈이 한다. 그 사이에 공용 3 DB read-only 대조가 들어간다.

```text
candidate 2종 (DB 없이 결정론적, RAG hash는 live 측정이 필요)
      ↓  --read-public  (공용 DB read-only 조회 승인)
kosa_agent_e2e · kosa_agent · kosa_text2sql  read-only 대조
      ↓  세 target 전부 통과 + 두 Runtime이 같은 후보와 일치
--confirm 없으면 sanitized preview + exit 3
      ↓  --confirm  (저장소 쓰기 승인)
history 보존 → active bundle 교체 → 재독 검증
```

**고정 3 target만 받는다.** 임의 DSN이나 profile/stage 문자열로 한 DB만 발급하는 우회
경로를 만들지 않는다(계획 §3.6). CLI는 positional target을 받지 않고 **두 축**의 승인
flag만 쓴다.

| flag | 승인하는 것 |
|---|---|
| `--read-public` | 공용 3 DB **read-only 조회** |
| `--confirm` | 저장소 active manifest **쓰기** |

두 축을 나눈 이유는 candidate의 Runtime RAG hash를 live에서 측정해야 하므로 **preview와
no-op 판정도 DB를 읽기** 때문이다. `--confirm`만으로는 조기 접속을 막을 수 없다 —
`--read-public`이 없으면 connection 자체를 만들지 않는다(계획 §1.2 · 구현보고 §4.2).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from types import MappingProxyType
from typing import Any

import apply_reference_extensions_v5 as reference_v5
import final_profile_manifests as candidates
import load_rag_documents as rag
import manifest_v3
import verify_bootstrap_state as verifier
from dotenv import load_dotenv

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2
EXIT_CONFIRM_REQUIRED = 3

#: **고정 발급 대상.** 순서가 계약이다 — e2e를 먼저 본다.
BUNDLE_TARGETS: tuple[tuple[str, str], ...] = (
    ("kosa_agent_e2e", "runtime"),
    ("kosa_agent", "runtime"),
    ("kosa_text2sql", "evaluation"),
)

#: profile → active manifest 경로. 교체 대상 3경로 전체가 bundle이다.
ACTIVE_PATHS: Mapping[str, Path] = MappingProxyType(
    {
        profile: manifest_v3.MANIFEST_REGISTRY_ROOT
        / f"{profile}.{reference_v5.FINAL_STAGE_BY_PROFILE[profile]}.json"
        for profile in reference_v5.FINAL_STAGE_BY_PROFILE
    }
)

#: 구 evaluation active manifest. **마지막 단계에서** 제거한다.
RETIRED_ACTIVE_PATH = (
    manifest_v3.MANIFEST_REGISTRY_ROOT / "evaluation.evaluation_mock.json"
)

HISTORY_ROOT = (
    manifest_v3.BOOTSTRAP_ROOT
    / "history"
    / manifest_v3.SUPERSEDED_DATASET_EPOCH
    / "manifests"
)


class RegistrarError(manifest_v3.VerificationError):
    """발급을 진행할 수 없다. **저장소에 아무것도 쓰지 않는다.**"""


def _rag_marker_contract() -> dict[str, Any]:
    """marker가 만족해야 하는 **정본값**(계획 §3.4 Gate 1).

    값을 여기 다시 적지 않고 loader 상수에서 가져온다. 두 곳에 적으면 갈린다 —
    양쪽 marker가 **같은 잘못된 값**이면 상호 비교로는 잡히지 않는다
    (구현리뷰 12차 필수 1).
    """

    return {
        "artifact_type": "rag_load_marker",
        # marker v1. `V5-B-1.4`가 version을 올려 원본/corrected provenance를 분리한다.
        "format_version": 1,
        "status": "COMMITTED",
        "profile": "runtime",
        "document_count": len(rag.CANONICAL_DOCUMENT_IDS),
        "chunk_count": 25,
        "null_embedding_count": 0,
        "dimension": rag.EMBEDDING_DIMENSION,
        "document_ids": list(rag.CANONICAL_DOCUMENT_IDS),
        "chunk_schema_version": rag.CHUNK_SCHEMA_VERSION,
        "chunk_contract_sha256": rag.CHUNK_CONTRACT_SHA256,
        "embedding_model": rag.EMBEDDING_MODEL,
        "embedding_model_revision": rag.EMBEDDING_MODEL_REVISION,
        "schema_sha256": _schema_digest(),
    }


def _schema_digest() -> str:
    import hashlib

    return hashlib.sha256(rag.SCHEMA_PATH.read_bytes()).hexdigest()


RAG_MARKER_CONTRACT: Mapping[str, Any] = MappingProxyType(_rag_marker_contract())

#: Runtime 두 DB. marker는 database별로 하나씩 있다.
RUNTIME_DATABASES: tuple[str, ...] = manifest_v3.PROFILE_APPLIES_TO["runtime"]

#: marker 사이에서 **exact 동일**해야 하는 field.
#:
#: 두 Runtime DB가 같은 corpus를 담고 있다는 것이 candidate 하나를 두 DB에 쓰는 근거다.
#: 하나라도 다르면 어느 쪽을 정답으로 삼을지 알 수 없다(계획 §3.4 Gate 3·4).
#: `V5-B-1.3` 적재 결과. builder와 **같은 상수**를 쓴다 — 두 곳이 갈리면 candidate와
#: 측정값이 서로 다른 기대치를 갖는다.
RUNTIME_RAG_ROWS = candidates.RUNTIME_RAG_ROWS

RAG_CROSS_DATABASE_FIELDS: tuple[str, ...] = (
    "document_ids",
    "corrected_sha256_by_document",
    "chunk_contract_sha256",
    "chunk_schema_version",
    "embedding_model",
    "embedding_model_revision",
    "schema_sha256",
    "live_db_fingerprint_sha256",
)


def _load_rag_marker(database: str) -> dict[str, Any]:
    marker = _read_json(rag.marker_path(database))
    if set(marker) != RAG_MARKER_KEYS:
        raise RegistrarError(f"RAG marker key 집합이 다릅니다: {database}")
    for key, expected in RAG_MARKER_CONTRACT.items():
        if marker.get(key) != expected:
            raise RegistrarError(f"RAG marker 계약 위반: {database}.{key}")
    if marker.get("database") != database:
        raise RegistrarError(f"RAG marker database가 다릅니다: {database}")
    return marker


#: marker의 exact key. 하나가 통째로 빠지면 상호 비교가 `None == None`으로 지나간다
#: (구현리뷰 11차 필수 1).
RAG_MARKER_KEYS: frozenset[str] = frozenset(
    {
        "artifact_type",
        "chunk_contract_sha256",
        "chunk_count",
        "chunk_schema_version",
        "corrected_sha256_by_document",
        "database",
        "dimension",
        "document_count",
        "document_ids",
        "embedding_model",
        "embedding_model_revision",
        "format_version",
        "live_db_fingerprint_sha256",
        "null_embedding_count",
        "profile",
        "recorded_at",
        "schema_sha256",
        "search_smoke",
        "source_sha256_by_document",
        "status",
    }
)


def runtime_rag_provenance(
    readers: Mapping[str, Any],
    *,
    columns: Mapping[str, Sequence[str]],
) -> dict[str, dict[str, Any]]:
    """**production provenance Gate**(계획 §3.4).

    `readers`는 database → read-only connection이다. **live를 읽어야 성립한다** —
    marker의 `live_db_fingerprint_sha256`은 document·chunk·metadata를 묶은 loader 전용
    combined hash라서, manifest의 table별 `content_hash`와 **산식이 다르다.** 그것을
    두 table에 그대로 넣으면 실제 DB가 marker와 일치해도 `CONTENT_HASH`가 난다
    (구현리뷰 11차 필수 1).

    Gate 5종.

    1. marker 두 종이 exact key·`COMMITTED`·profile·3/25·NULL 0·dimension 1024
    2. 두 marker의 corpus identity가 서로 exact
    3. marker corrected hash가 **저장소 RAG 원본의 실제 digest**와 일치
    4. 두 DB에서 manifest column 순서로 계산한 table별 row count·canonical hash가 exact
    5. 같은 snapshot의 fresh `live_fingerprint()`가 각 marker와 일치
    """

    markers = {database: _load_rag_marker(database) for database in RUNTIME_DATABASES}
    if set(readers) != set(RUNTIME_DATABASES):
        raise RegistrarError("Runtime DB 두 곳을 모두 읽어야 합니다")

    first, *rest = RUNTIME_DATABASES
    for database in rest:
        for field in RAG_CROSS_DATABASE_FIELDS:
            if markers[database][field] != markers[first][field]:
                raise RegistrarError(
                    f"RAG marker가 두 Runtime DB에서 다릅니다: {field}"
                )

    _assert_corrected_sources(markers[first])

    measured = {
        database: _measure_runtime_rag(readers[database], markers[database], columns)
        for database in RUNTIME_DATABASES
    }
    if measured[first] != measured[rest[0]]:
        raise RegistrarError("두 Runtime DB의 RAG 내용이 다릅니다")
    return measured[first]


def _assert_corrected_sources(marker: Mapping[str, Any]) -> None:
    """marker의 corrected hash가 저장소 RAG 원본과 일치하는지 본다.

    marker v1은 loader 구현상 `source_sha256_by_document`에도 corrected hash를
    복제한다. **그것을 원본 hash로 오해하지 않는다** — provenance 보강은 `V5-B-1.4`
    소유이며 CM-1.8은 marker schema를 고치지 않는다(계획 §3.4-2).
    """

    corrected = marker.get("corrected_sha256_by_document")
    if not isinstance(corrected, dict) or not corrected:
        raise RegistrarError("RAG marker corrected hash가 없습니다")
    if sorted(corrected) != sorted(marker.get("document_ids") or ()):
        raise RegistrarError("RAG marker 문서 id 집합이 다릅니다")

    # **형식이 아니라 실물과 대조한다.** 64자리이기만 하면 통과하던 검사는
    # 전부 `0`으로 바꾼 marker도 받아들였다(구현리뷰 11차 필수 1).
    prepared = rag.prepare_corpus(rag.DEFAULT_CORRECTED_RAG_DIR)
    actual = {
        document.document_id: document.corrected_sha256
        for document in prepared.documents
    }
    if actual != dict(corrected):
        raise RegistrarError("RAG corrected hash가 저장소 원본과 다릅니다")

    # **marker v1의 source map 계약**(구현리뷰 13차 필수 2).
    #
    # loader가 source map에도 corrected digest를 복제한다. 그것을 원본 ZIP provenance로
    # 해석하지 않는 것과, 값을 아예 검증하지 않는 것은 다르다. v1에서는 두 map이 같아야
    # 하며 원본/corrected 분리는 `V5-B-1.4`가 version을 올려 수행한다.
    if marker.get("source_sha256_by_document") != dict(corrected):
        raise RegistrarError("RAG marker v1의 source map이 corrected와 다릅니다")


def _measure_runtime_rag(
    connection: Any,
    marker: Mapping[str, Any],
    columns: Mapping[str, Sequence[str]],
) -> dict[str, dict[str, Any]]:
    """한 DB의 RAG table을 **manifest column 순서로** 읽어 canonical hash를 만든다."""

    import verify_bootstrap_state as v

    measured: dict[str, dict[str, Any]] = {}
    for table, expected_rows in RUNTIME_RAG_ROWS.items():
        # **builder와 같은 부분집합으로 hash한다.** `created_at`처럼 두 DB가 항상
        # 다른 컬럼을 빼야 measured와 candidate가 일치한다(구현리뷰 16차 필수 1).
        volatile = candidates.RUNTIME_RAG_VOLATILE_COLUMNS.get(table, frozenset())
        table_columns = [c for c in columns[table] if c not in volatile]
        types = v._expected_column_types(table)
        hashed_types = {name: types[name] for name in table_columns}
        selected = ", ".join(f'"{column}"' for column in table_columns)
        rows = [
            v.normalize_db_row(row, hashed_types)
            for row in v._rows(v._sql(connection, f'SELECT {selected} FROM "{table}"'))
        ]
        if len(rows) != expected_rows:
            raise RegistrarError(f"RAG {table} 행 수가 계약과 다릅니다")
        measured[table] = {
            "row_count": len(rows),
            "content_hash": manifest_v3.hash_canonical_rows(rows),
        }

    # **live embedding을 직접 센다**(구현리뷰 12차 필수 1).
    #
    # marker의 `null_embedding_count=0`만 믿으면, 두 DB가 **똑같이** NULL이거나
    # dimension이 똑같이 틀려도 서로 exact해서 손상이 새 기준으로 등록된다.
    # `live_fingerprint()`는 embedding vector를 hash하지 않으므로 그것으로도 못 잡는다.
    broken = int(
        v._scalar(
            v._sql(
                connection,
                "SELECT count(*) FROM document_chunk "
                "WHERE embedding IS NULL OR vector_dims(embedding) <> "
                f"{int(rag.EMBEDDING_DIMENSION)}",
            )
        )
    )
    if broken:
        raise RegistrarError("live embedding이 NULL이거나 dimension이 다릅니다")

    # **fresh fingerprint를 같은 snapshot에서 다시 잰다.** marker가 stale이면 잡힌다.
    fingerprint = rag.live_fingerprint(connection, marker["document_ids"])
    if fingerprint != marker["live_db_fingerprint_sha256"]:
        raise RegistrarError("live fingerprint가 marker와 다릅니다")
    return measured


def _verify_target(
    database: str,
    profile: str,
    candidate: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None,
    verify: Callable[..., Any],
) -> dict[str, Any]:
    """target 하나를 후보와 대조한다. 실패는 그대로 올린다."""

    result = verify(
        database,
        reference_v5.FINAL_STAGE_BY_PROFILE[profile],
        environ=environ,
        candidate=candidate,
        # **CM-1.8은 manifest 내용만 판정한다.** `agent_runtime` marker는 final
        # migration 적용의 증명서이고 `V5-CM-3.2`가 소유한다. 여기서 요구하면
        # CM-1.8 ↔ CM-3.2 순환이 된다(구현리뷰 12차 필수 2).
        require_runtime_marker=False,
    )
    if result.exit_code != verifier.EXIT_OK:
        raise RegistrarError(f"target 검증 실패: {database}")
    return {"database": database, "profile": profile, "status": result.status}


def verify_bundle(
    bundle: Mapping[str, Mapping[str, Any]],
    *,
    environ: Mapping[str, str] | None = None,
    verify: Callable[..., Any] = verifier.verify_database,
) -> list[dict[str, Any]]:
    """고정 3 target을 순서대로 대조한다.

    **하나라도 실패하면 전체 실패다.** Runtime 후보는 두 DB에 **같은 것**을 넘기므로,
    둘이 서로 다르면 뒤 target에서 반드시 걸린다 — 후보를 DB별로 만들지 않는 것이
    그 동일성 계약의 구현이다(계획 §5 묶음 2-3).
    """

    if set(bundle) != set(reference_v5.FINAL_STAGE_BY_PROFILE):
        raise RegistrarError("bundle profile 집합이 계약과 다릅니다")

    receipts: list[dict[str, Any]] = []
    for database, profile in BUNDLE_TARGETS:
        receipts.append(
            _verify_target(
                database,
                profile,
                bundle[profile],
                environ=environ,
                verify=verify,
            )
        )
    return receipts


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistrarError("manifest를 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise RegistrarError("manifest 최상위 값이 object가 아닙니다")
    return payload


def bundle_state(bundle: Mapping[str, Mapping[str, Any]]) -> str:
    """지금 저장소가 어떤 상태인지 판정한다.

    - `current` — 두 active가 후보와 exact하고 구 evaluation active가 없다
    - `pending` — 그 밖
    """

    if RETIRED_ACTIVE_PATH.exists():
        return "pending"
    for profile, path in ACTIVE_PATHS.items():
        if not path.exists() or _read_json(path) != dict(bundle[profile]):
            return "pending"
    return "current"


def preview(bundle: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """무엇이 바뀌는지 **값 없이** 알려준다.

    row 값·hash·경로 전체를 찍으면 preview 자체가 유출 경로가 된다.
    """

    lines: list[str] = []
    for profile, path in sorted(ACTIVE_PATHS.items()):
        stage = reference_v5.FINAL_STAGE_BY_PROFILE[profile]
        if not path.exists():
            lines.append(f"{profile}.{stage}: 생성")
        elif _read_json(path) != dict(bundle[profile]):
            differences = manifest_v3.compare_manifests(
                dict(bundle[profile]), _read_json(path)
            )
            lines.append(f"{profile}.{stage}: 교체 ({len(differences)}곳)")
        else:
            lines.append(f"{profile}.{stage}: 변경 없음")
    if RETIRED_ACTIVE_PATH.exists():
        lines.append("evaluation.evaluation_mock: history 보존 후 제거")
    return lines


def _assert_history_is_reusable(path: Path) -> None:
    """history 충돌을 **쓰기 전에** 판정한다.

    이미 있으면 exact할 때만 재사용한다. 다르면 어느 쪽이 원본인지 알 수 없으므로
    아무것도 쓰기 전에 멈춘다.
    """

    if not path.exists():
        return
    target = HISTORY_ROOT / path.name
    if target.exists() and target.read_bytes() != path.read_bytes():
        raise RegistrarError("history 보존본이 active와 다릅니다")


def _preserve_history(path: Path) -> None:
    """active bytes를 history에 보존한다. 충돌은 이미 preflight가 걸렀다."""

    if not path.exists():
        return
    target = HISTORY_ROOT / path.name
    payload = path.read_bytes()
    if target.exists():
        return
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if target.read_bytes() != payload:
        raise RegistrarError("history 보존본 재독에 실패했습니다")


def commit_bundle(bundle: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """history 보존 → active 교체 → 재독. **실패하면 전부 되돌린다.**

    구 evaluation active 제거는 **마지막**이다. 먼저 지우면 새 파일 저장이 실패했을 때
    어느 evaluation manifest도 남지 않는다(계획 §3.6).
    """

    for profile in sorted(ACTIVE_PATHS):
        manifest_v3.validate_manifest_schema(
            bundle[profile],
            expected_artifact_type="db_bootstrap",
            expected_profile=profile,
            expected_stage=reference_v5.FINAL_STAGE_BY_PROFILE[profile],
            expected_archive_sha256=manifest_v3.FINAL_ARCHIVE_SHA256,
        )

    active_paths = (*ACTIVE_PATHS.values(), RETIRED_ACTIVE_PATH)

    # **충돌은 쓰기 전에 전부 본다**(구현리뷰 16차 필수 2).
    #
    # 예전에는 `_preserve_history()`를 순서대로 호출한 뒤 `try`에 들어갔다. 첫 보존이
    # 성공하고 둘째가 충돌하면 첫 파일이 그대로 남아 "history collision이면 파일 쓰기
    # 0"이 성립하지 않았다.
    for path in active_paths:
        _assert_history_is_reusable(path)

    # **history와 active를 하나의 snapshot으로 잡는다.** history 신규 파일도 rollback
    # 대상이어야 실패 시 시작 bytes로 돌아간다.
    original: dict[Path, bytes | None] = {
        path: (path.read_bytes() if path.exists() else None)
        for path in (*active_paths, *(HISTORY_ROOT / p.name for p in active_paths))
    }
    written: list[str] = []
    try:
        for path in active_paths:
            _preserve_history(path)
        for profile, path in sorted(ACTIVE_PATHS.items()):
            manifest_v3.atomic_save_json(path, bundle[profile])
            if _read_json(path) != dict(bundle[profile]):
                raise RegistrarError("저장본 재독이 후보와 다릅니다")
            written.append(path.name)
        if RETIRED_ACTIVE_PATH.exists():
            RETIRED_ACTIVE_PATH.unlink()
            written.append(f"-{RETIRED_ACTIVE_PATH.name}")
    except Exception:
        _rollback(original)
        raise
    return written


def _rollback(original: Mapping[Path, bytes | None]) -> None:
    for path, payload in original.items():
        if payload is None:
            if path.exists():
                path.unlink()
        else:
            path.write_bytes(payload)


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    verify: Callable[..., Any] = verifier.verify_database,
    runtime_rag: Mapping[str, Mapping[str, Any]] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if runtime_rag is None:
        # **production 경로.** provenance는 live를 읽어야 성립한다(§3.4 Gate 4·5).
        if not args.read_public:
            raise RegistrarError(
                "공용 DB 조회에는 --read-public이 필요합니다 (계획 §1.2)"
            )
        runtime_rag = _measure_runtime_rag_from_live(environ=environ)
    bundle = candidates.build_final_bundle(runtime_rag=runtime_rag)

    if bundle_state(bundle) == "current":
        print("active final manifest bundle이 이미 후보와 일치합니다")
        return EXIT_OK

    if not args.confirm:
        print("변경 예정:")
        for line in preview(bundle):
            print(f"  {line}")
        print("--confirm이 필요합니다")
        return EXIT_CONFIRM_REQUIRED

    verify_bundle(bundle, environ=environ, verify=verify)
    for name in commit_bundle(bundle):
        print(f"기록: {name}")
    return EXIT_OK


def _measure_runtime_rag_from_live(
    *, environ: Mapping[str, str] | None
) -> dict[str, dict[str, Any]]:
    """두 Runtime DB를 read-only로 열어 provenance를 만든다.

    RAG table의 column 순서는 candidate builder와 **같은 출처**를 쓴다 — 다른 순서로
    읽으면 hash가 달라진다.
    """

    columns = {table: candidates._v4_columns(table) for table in RUNTIME_RAG_ROWS}
    # **`ExitStack`이다.** 예전에는 begin·SET·identity를 모두 마친 뒤에야 connection을
    # `readers`에 넣어서, 그 전에 실패하면 checked-out connection이 닫히지 않았다.
    # `Engine.dispose()`는 이미 checkout된 connection의 `close()`를 대신하지 않는다
    # (구현리뷰 12차 필수 3).
    with ExitStack() as stack:
        readers: dict[str, Any] = {}
        for database in RUNTIME_DATABASES:
            target = verifier.load_bootstrap_target(database, environ=environ)
            engine = verifier._engine_for(target)
            stack.callback(engine.dispose)
            connection = engine.connect()
            stack.callback(connection.close)
            connection.begin()
            verifier._sql(connection, verifier.READ_ONLY_TRANSACTION_SQL)
            verifier._sql(connection, "SET LOCAL search_path = public")
            verifier._sql(connection, "SET LOCAL statement_timeout = '30s'")
            verifier._validate_read_identity(connection, target)
            readers[database] = connection
        return runtime_rag_provenance(readers, columns=columns)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="최종 profile manifest bundle을 발급한다",
    )
    # positional target을 받지 않는다 — 임의 DB 하나만 발급하는 우회를 막는다.
    parser.add_argument("--confirm", action="store_true")
    # **공용 DB 접근은 명시적 opt-in이다.**
    #
    # provenance는 두 Runtime DB를 read-only로 읽어야 성립하는데, `.env`에 자격증명이
    # 있으면 아무 생각 없이 실행한 CLI가 그대로 공용 서버에 닿는다. 계획 §1.2는 그
    # 시점을 "묶음 2 구현리뷰 필수 0 이후, 묶음 3 시작"으로 고정했다. flag 없이는
    # 연결 자체를 만들지 않는다.
    parser.add_argument("--read-public", action="store_true")
    return parser


#: known error → reason. **값을 담지 않는다.**
_KNOWN_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "CandidateError": "CANDIDATE_CONTRACT",
        "RegistrarError": "REGISTRAR_CONTRACT",
        "NotRegisteredError": "NOT_REGISTERED",
        "ArtifactMismatchError": "ARTIFACT_MISMATCH",
        "ManifestMetadataError": "INVALID_METADATA",
        "ManifestSchemaError": "INVALID_SCHEMA",
        "VerificationError": "INVALID_INPUT",
        "TargetValidationError": "TARGET_UNVERIFIABLE",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 경계. **traceback·DSN·절대경로를 내보내지 않는다.**

    known error를 안정된 exit code와 값 없는 reason으로 수렴시킨다. 예상하지 못한
    programmer error는 **삼키지 않는다** — 성공이나 일반 mismatch로 바꾸면 그 자리에서
    조용히 잘못된 manifest가 발급될 수 있다.
    """

    load_dotenv(manifest_v3.REPOSITORY_ROOT / ".env", override=False)
    try:
        return run(argv, environ=dict(os.environ))
    except tuple(_known_error_types()) as exc:
        reason = _KNOWN_REASONS.get(type(exc).__name__, "INVALID_INPUT")
        return _fail(reason, getattr(exc, "exit_code", EXIT_MISMATCH))
    except _operational_error_types() as exc:
        # **연결 실패·권한·네트워크·디스크 오류는 발급에서 흔하다**(구현리뷰 11차
        # 필수 3). traceback으로 나가면 DSN·절대경로가 함께 새어 나간다.
        return _fail(_OPERATIONAL_REASONS[isinstance(exc, OSError)], EXIT_UNVERIFIABLE)


#: 운영 오류 reason. DB인지 filesystem인지만 구분하고 값은 담지 않는다.
_OPERATIONAL_REASONS = {False: "CONNECT_OR_QUERY_FAILED", True: "REGISTRAR_IO"}
EXIT_UNVERIFIABLE = 4


def _fail(reason: str, exit_code: int) -> int:
    print(json.dumps({"status": "FAIL", "reason_code": reason}, sort_keys=True))
    return exit_code


def _known_error_types() -> tuple[type[BaseException], ...]:
    from db_target import TargetValidationError

    return (manifest_v3.VerificationError, TargetValidationError)


def _operational_error_types() -> tuple[type[BaseException], ...]:
    from sqlalchemy.exc import SQLAlchemyError
    from value_normalization import ValueNormalizationError

    # 정규화 오류도 여기 둔다 — live row를 읽다 나므로 traceback에 값이 실린다.
    return (SQLAlchemyError, OSError, ValueNormalizationError)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
