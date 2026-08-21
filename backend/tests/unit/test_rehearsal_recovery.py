"""`V5-CM-2.5` 재실행·복구 단위 테스트.

DB에 붙지 않는다. catalog 질의만 흉내 내는 fake로 상태 판정·marker 계약·outcome
전이를 고정한다. 실제 PostgreSQL transaction은 `test_rehearsal_container.py`가
`container` marker로 따로 검증한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import manifest_v3  # noqa: E402
import rehearsal_profile_verifier as verifier  # noqa: E402
import rehearsal_recovery as recovery  # noqa: E402
import rehearsal_schema  # noqa: E402

TABLES = tuple(sorted(rehearsal_schema.EXPECTED_TABLES))
INDEXES = tuple(sorted(rehearsal_schema.EXPECTED_INDEXES))


class _Fail(RuntimeError):
    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


def _fail(reason_code: str, exit_code: int) -> _Fail:
    return _Fail(reason_code, exit_code)


class _Result:
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = list(rows)
        self.statements: list[str] = []

    def execute(self, statement: Any) -> _Result:
        self.statements.append(str(statement))
        return _Result(self._rows)


def _catalog(
    tables: Sequence[str] = TABLES,
    indexes: Sequence[str] = INDEXES,
    extra: Sequence[tuple[str, str]] = (),
) -> list[dict[str, str]]:
    rows = [{"name": name, "kind": "r"} for name in tables]
    rows += [{"name": name, "kind": "i"} for name in indexes]
    rows += [{"name": name, "kind": kind} for name, kind in extra]
    return rows


def _acceptance(name: str, profiles: Sequence[str]) -> verifier.TableAcceptance:
    return verifier.TableAcceptance(
        name=name,
        columns=("id",),
        column_types=MappingProxyType({"id": "numeric"}),
        primary_key=("id",),
        source_row_count=12 if name == verifier.EVALUATION_ONLY_TABLE else 5,
        source_content_hash=("a" if name == "metrology" else "b") * 64,
        included_by_profile=tuple(profiles),
    )


ACCEPTANCES = tuple(
    _acceptance(
        name,
        ("evaluation",)
        if name == verifier.EVALUATION_ONLY_TABLE
        else ("runtime", "evaluation"),
    )
    for name in TABLES
)


def _identity(**overrides: Any) -> recovery.MarkerIdentity:
    values: dict[str, Any] = {
        "profile": "runtime",
        "database": "fdc_rehearsal_runtime",
        "logical_targets": ("kosa_agent", "kosa_agent_e2e"),
        "source_archive_sha256": "1" * 64,
        "source_manifest_sha256": "2" * 64,
        "schema_sha256": "3" * 64,
    }
    values.update(overrides)
    return recovery.MarkerIdentity(**values)


# ---------------------------------------------------------------------------
# fingerprint · marker payload
# ---------------------------------------------------------------------------


def test_runtime_projects_evaluation_only_table_to_empty() -> None:
    """runtime fingerprint의 action_history는 source 12행이 아니다(계획 §6.1)."""

    runtime = recovery.table_fingerprints(ACCEPTANCES, "runtime")
    evaluation = recovery.table_fingerprints(ACCEPTANCES, "evaluation")
    action = verifier.EVALUATION_ONLY_TABLE
    assert runtime[action] == {
        "row_count": 0,
        "content_hash": verifier.EMPTY_ROWS_HASH,
    }
    assert evaluation[action]["row_count"] == 12
    assert list(runtime) == sorted(runtime)
    assert len(runtime) == 9


def test_live_fingerprint_formula_is_exact_and_deterministic() -> None:
    fingerprints = recovery.table_fingerprints(ACCEPTANCES, "runtime")
    expected = manifest_v3.hash_canonical_rows(
        [
            {
                "dataset_epoch": recovery.DATASET_EPOCH,
                "profile": "runtime",
                "schema_sha256": "3" * 64,
                "table_fingerprints": fingerprints,
            }
        ]
    )
    actual = recovery.live_db_fingerprint(
        dataset_epoch=recovery.DATASET_EPOCH,
        profile="runtime",
        schema_sha256="3" * 64,
        fingerprints=fingerprints,
    )
    assert actual == expected
    # 삽입 순서를 뒤집어도 같은 값이다.
    reversed_input = dict(reversed(list(fingerprints.items())))
    assert (
        recovery.live_db_fingerprint(
            dataset_epoch=recovery.DATASET_EPOCH,
            profile="runtime",
            schema_sha256="3" * 64,
            fingerprints=reversed_input,
        )
        == expected
    )


def test_profile_changes_live_fingerprint() -> None:
    """profile을 payload에서 빼면 두 profile이 같은 값이 된다(변이 대상)."""

    common = {"dataset_epoch": recovery.DATASET_EPOCH, "schema_sha256": "3" * 64}
    runtime = recovery.live_db_fingerprint(
        profile="runtime",
        fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime"),
        **common,
    )
    evaluation = recovery.live_db_fingerprint(
        profile="evaluation",
        fingerprints=recovery.table_fingerprints(ACCEPTANCES, "evaluation"),
        **common,
    )
    assert runtime != evaluation


def test_marker_payload_is_exact_and_secret_free() -> None:
    identity = _identity(
        table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
    )
    payload = recovery.build_marker(identity)
    assert set(payload) == recovery.MARKER_KEYS
    assert payload["dataset_epoch"] == "fdc_final_20260818"
    assert payload["logical_targets"] == ["kosa_agent", "kosa_agent_e2e"]
    rendered = json.dumps(payload)
    # `artifact_type`가 "postgres_profile_marker"이므로 "postgres" 자체는 금칙이
    # 아니다. 막아야 하는 것은 credential·host·port·경로·시각이다.
    for forbidden in ("password", "localhost", "127.0.0.1", "5432", "/Users", "://"):
        assert forbidden not in rendered
    assert "timestamp" not in rendered and "created_at" not in rendered
    # producer가 쓰는 secret scanner도 통과해야 저장된다.
    manifest_v3.scan_for_sensitive_values(payload)


def test_marker_bytes_are_stable_across_key_order() -> None:
    identity = _identity(
        table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
    )
    payload = recovery.build_marker(identity)
    shuffled = dict(reversed(list(payload.items())))
    assert recovery.marker_bytes(payload) == recovery.marker_bytes(shuffled)
    assert recovery.marker_bytes(payload).endswith(b"\n")


# ---------------------------------------------------------------------------
# marker validator
# ---------------------------------------------------------------------------


@pytest.fixture
def expected_marker() -> dict[str, Any]:
    return recovery.build_marker(
        _identity(
            table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
        )
    )


def test_matching_marker_is_valid(expected_marker: dict[str, Any]) -> None:
    assert (
        recovery.validate_marker_payload(dict(expected_marker), expected_marker)
        is recovery.MarkerState.VALID
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("status"),
        lambda p: p.update(extra_key=1),
        lambda p: p.update(format_version=2),
        lambda p: p.update(artifact_type="other"),
        lambda p: p.update(status="PENDING"),
        lambda p: p.update(logical_targets=[]),
        lambda p: p.update(logical_targets=["kosa_agent", "kosa_agent"]),
        lambda p: p.update(logical_targets=["Kosa-Agent"]),
        lambda p: p.update(source_archive_sha256="ABC"),
        lambda p: p.update(schema_sha256="3" * 63),
        lambda p: p.update(table_fingerprints={}),
        lambda p: p.update(table_fingerprints={"metrology": {"row_count": 1}}),
        lambda p: p.update(
            table_fingerprints={
                "metrology": {"row_count": True, "content_hash": "a" * 64}
            }
        ),
        lambda p: p.update(
            table_fingerprints={
                "metrology": {"row_count": -1, "content_hash": "a" * 64}
            }
        ),
        lambda p: p.update(profile=""),
    ],
)
def test_structural_errors_are_invalid(
    expected_marker: dict[str, Any], mutate: Any
) -> None:
    payload = json.loads(json.dumps(expected_marker))
    mutate(payload)
    assert (
        recovery.validate_marker_payload(payload, expected_marker)
        is recovery.MarkerState.INVALID
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(dataset_epoch="kosa_0813"),
        lambda p: p.update(profile="evaluation"),
        lambda p: p.update(database="kosa_agent"),
        lambda p: p.update(source_archive_sha256="9" * 64),
        lambda p: p.update(source_manifest_sha256="9" * 64),
        lambda p: p.update(schema_sha256="9" * 64),
        lambda p: p.update(logical_targets=["kosa_agent"]),
        lambda p: p.update(live_db_fingerprint_sha256="9" * 64),
    ],
)
def test_provenance_differences_are_mismatch(
    expected_marker: dict[str, Any], mutate: Any
) -> None:
    payload = json.loads(json.dumps(expected_marker))
    mutate(payload)
    assert (
        recovery.validate_marker_payload(payload, expected_marker)
        is recovery.MarkerState.MISMATCH
    )


def test_single_row_count_drift_is_mismatch(expected_marker: dict[str, Any]) -> None:
    payload = json.loads(json.dumps(expected_marker))
    payload["table_fingerprints"]["metrology"]["row_count"] += 1
    assert (
        recovery.validate_marker_payload(payload, expected_marker)
        is recovery.MarkerState.MISMATCH
    )


def test_non_mapping_payload_is_invalid(expected_marker: dict[str, Any]) -> None:
    for payload in ([], "marker", 7, None):
        assert (
            recovery.validate_marker_payload(payload, expected_marker)
            is recovery.MarkerState.INVALID
        )


# ---------------------------------------------------------------------------
# DB 상태 판정 — catalog만 본다
# ---------------------------------------------------------------------------


def test_empty_public_schema_is_fresh() -> None:
    connection = FakeConnection([])
    assert recovery.classify_db_state(connection, TABLES, INDEXES) is (
        recovery.DbState.FRESH
    )
    assert len(connection.statements) == 1


def test_exact_catalog_is_adopted_candidate() -> None:
    connection = FakeConnection(_catalog())
    assert recovery.classify_db_state(connection, TABLES, INDEXES) is (
        recovery.DbState.ADOPTED_CANDIDATE
    )


@pytest.mark.parametrize(
    "rows",
    [
        _catalog(tables=TABLES[:-1]),
        _catalog(tables=(*TABLES, "stray_table")),
        _catalog(indexes=INDEXES[:-1]),
        _catalog(extra=(("some_view", "v"),)),
        _catalog(extra=(("some_sequence", "S"),)),
        _catalog(extra=(("some_matview", "m"),)),
    ],
)
def test_drift_catalog_is_partial_or_drift(rows: list[dict[str, str]]) -> None:
    connection = FakeConnection(rows)
    assert recovery.classify_db_state(connection, TABLES, INDEXES) is (
        recovery.DbState.PARTIAL_OR_DRIFT
    )


def test_classifier_does_not_run_full_acceptance() -> None:
    """handler는 catalog 한 번만 질의한다. 값 검증은 postcheck 몫이다(계획 §5.1)."""

    connection = FakeConnection(_catalog())
    recovery.classify_db_state(connection, TABLES, INDEXES)
    assert len(connection.statements) == 1
    statement = connection.statements[0]
    assert "pg_class" in statement
    assert "count(*)" not in statement
    assert "information_schema" not in statement


# ---------------------------------------------------------------------------
# MarkerStore · lock
# ---------------------------------------------------------------------------


@pytest.mark.windows_contract
def test_store_round_trip_and_marker_count(tmp_path: Path) -> None:
    store = recovery.MarkerStore(tmp_path, "runtime")
    assert store.read() == (recovery.MarkerState.MISSING, None)
    assert store.markers() == []

    payload = recovery.build_marker(
        _identity(
            table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
        )
    )
    store.save(payload)
    state, loaded = store.read()
    assert state is recovery.MarkerState.VALID
    assert loaded == payload
    assert store.markers() == [store.path]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.windows_contract
def test_lock_file_does_not_change_marker_count(tmp_path: Path) -> None:
    """lock은 marker root에 있어도 `*.json` 계수를 흔들지 않는다(2차 권장 1)."""

    store = recovery.MarkerStore(tmp_path, "runtime")
    store.save(
        recovery.build_marker(
            _identity(
                table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
            )
        )
    )
    with recovery.marker_lock(tmp_path, "runtime"):
        assert (tmp_path / "runtime.lock").exists()
        assert len(store.markers()) == 1


@pytest.mark.windows_contract
def test_save_is_atomic_and_keeps_previous_on_failure(tmp_path: Path) -> None:
    store = recovery.MarkerStore(tmp_path, "runtime")
    payload = recovery.build_marker(
        _identity(
            table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
        )
    )
    store.save(payload)
    original = store.path.read_bytes()

    class Unserializable:
        pass

    with pytest.raises((TypeError, ValueError)):
        store.save({**payload, "profile": Unserializable()})
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def _require_symlinks(tmp_path: Path) -> None:
    probe = tmp_path / ".symlink-probe"
    try:
        probe.symlink_to(tmp_path)
    except (OSError, NotImplementedError):  # pragma: no cover - 플랫폼 의존
        pytest.skip("이 플랫폼에서는 symlink를 만들 수 없어 검사를 구성할 수 없다")
    probe.unlink()


@pytest.mark.windows_contract
def test_symlinked_marker_is_invalid(tmp_path: Path) -> None:
    _require_symlinks(tmp_path)
    real = tmp_path / "elsewhere.json"
    real.write_text("{}", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "runtime.json").symlink_to(real)
    store = recovery.MarkerStore(root, "runtime")
    assert store.read()[0] is recovery.MarkerState.INVALID
    with pytest.raises(OSError):
        store.save({"any": "payload"})


@pytest.mark.windows_contract
def test_corrupt_json_is_invalid(tmp_path: Path) -> None:
    store = recovery.MarkerStore(tmp_path, "runtime")
    store.path.write_bytes(b"{not json")
    assert store.read()[0] is recovery.MarkerState.INVALID


_CHILD_LOCK_PROBE = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import rehearsal_recovery as recovery

try:
    with recovery.marker_lock(Path(sys.argv[2]), "runtime"):
        print("acquired")
except recovery.LockUnavailableError:
    print("busy")
"""


def _probe_lock_from_child(root: Path) -> str:
    """별도 프로세스에서 같은 lock을 시도한다.

    `multiprocessing` 대신 subprocess를 쓴다. Windows의 spawn 시작 방식에서
    pytest 모듈을 재import하는 경로가 플랫폼마다 달라지기 때문이다.
    """

    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_LOCK_PROBE, str(SCRIPTS_ROOT), str(root)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


@pytest.mark.windows_contract
def test_lock_is_exclusive(tmp_path: Path) -> None:
    with recovery.marker_lock(tmp_path, "runtime"):
        assert _probe_lock_from_child(tmp_path) == "busy"
    # 해제 뒤에는 다시 잡힌다.
    assert _probe_lock_from_child(tmp_path) == "acquired"


# ---------------------------------------------------------------------------
# RecoverySession 전이
# ---------------------------------------------------------------------------


def _session(
    tmp_path: Path,
    *,
    rows: Sequence[Mapping[str, Any]],
    recover: bool = False,
    events: list[str] | None = None,
    identity: recovery.MarkerIdentity | None = None,
) -> tuple[recovery.RecoverySession, recovery.MarkerStore, FakeConnection]:
    log = events if events is not None else []

    def fresh_handler(_c: Any, _p: Any) -> None:
        log.append("fresh")

    def acceptance(_c: Any, _p: Any) -> None:
        log.append("acceptance")

    store = recovery.MarkerStore(tmp_path, "runtime")
    session = recovery.RecoverySession(
        store=store,
        identity=identity or _identity(),
        acceptances=ACCEPTANCES,
        profile="runtime",
        expected_tables=TABLES,
        expected_indexes=INDEXES,
        fresh_handler=fresh_handler,
        acceptance_postcheck=acceptance,
        error_factory=_fail,
        recover_artifact=recover,
    )
    return session, store, FakeConnection(rows)


def _write_expected(store: recovery.MarkerStore, session: Any) -> None:
    store.save(session.expected_marker)


def test_fresh_apply_runs_loader_then_saves_marker(tmp_path: Path) -> None:
    events: list[str] = []
    session, store, connection = _session(tmp_path, rows=[], events=events)

    assert session.handler(connection, object()) is None
    assert session.outcome is recovery.Outcome.APPLIED
    assert session.postcheck(connection, object()) is None
    assert session.post_commit(connection, object()) is None

    assert events == ["fresh", "acceptance"]
    assert store.markers() == [store.path]
    assert store.path.read_bytes() == recovery.marker_bytes(session.expected_marker)


def test_valid_marker_on_adopted_is_noop(tmp_path: Path) -> None:
    events: list[str] = []
    session, store, connection = _session(tmp_path, rows=_catalog(), events=events)
    _write_expected(store, session)
    before = store.path.read_bytes()
    before_mtime = store.path.stat().st_mtime_ns

    session.handler(connection, object())
    assert session.outcome is recovery.Outcome.NOOP
    session.postcheck(connection, object())
    session.post_commit(connection, object())

    # DDL/COPY 0회, full acceptance 1회, marker read/write 0회.
    assert events == ["acceptance"]
    assert store.path.read_bytes() == before
    assert store.path.stat().st_mtime_ns == before_mtime


def test_missing_marker_on_adopted_requires_recovery(tmp_path: Path) -> None:
    events: list[str] = []
    session, store, connection = _session(tmp_path, rows=_catalog(), events=events)

    session.handler(connection, object())
    assert session.outcome is recovery.Outcome.RECOVERY_REQUIRED
    with pytest.raises(_Fail) as caught:
        session.postcheck(connection, object())
    assert caught.value.reason_code == "RECOVERY_REQUIRED"
    assert caught.value.exit_code == recovery.EXIT_MISMATCH
    # 거부하기 전에 full acceptance를 반드시 통과시킨다 — 그래야 drift와 구분된다.
    assert events == ["acceptance"]
    assert store.markers() == []


def test_recover_restores_marker_only(tmp_path: Path) -> None:
    events: list[str] = []
    session, store, connection = _session(
        tmp_path, rows=_catalog(), recover=True, events=events
    )

    session.handler(connection, object())
    assert session.outcome is recovery.Outcome.RECOVER
    session.postcheck(connection, object())
    session.post_commit(connection, object())

    assert events == ["acceptance"]  # DDL/COPY 0회
    assert store.path.read_bytes() == recovery.marker_bytes(session.expected_marker)


def test_recovered_marker_is_byte_identical_to_first(tmp_path: Path) -> None:
    first, store, connection = _session(tmp_path, rows=[])
    first.handler(connection, object())
    first.postcheck(connection, object())
    first.post_commit(connection, object())
    original = store.path.read_bytes()

    store.path.unlink()
    second, _store, adopted = _session(tmp_path, rows=_catalog(), recover=True)
    second.handler(adopted, object())
    second.postcheck(adopted, object())
    second.post_commit(adopted, object())
    assert store.path.read_bytes() == original


def test_recover_on_valid_marker_is_not_allowed(tmp_path: Path) -> None:
    session, store, connection = _session(tmp_path, rows=_catalog(), recover=True)
    _write_expected(store, session)
    before = store.path.read_bytes()
    with pytest.raises(_Fail) as caught:
        session.handler(connection, object())
    assert caught.value.reason_code == "RECOVERY_NOT_ALLOWED"
    assert caught.value.exit_code == recovery.EXIT_MISMATCH
    assert store.path.read_bytes() == before


def test_recover_on_fresh_is_not_allowed(tmp_path: Path) -> None:
    session, store, connection = _session(tmp_path, rows=[], recover=True)
    with pytest.raises(_Fail) as caught:
        session.handler(connection, object())
    assert caught.value.reason_code == "RECOVERY_NOT_ALLOWED"
    assert store.markers() == []


def test_drift_db_is_refused_before_any_write(tmp_path: Path) -> None:
    events: list[str] = []
    session, store, connection = _session(
        tmp_path, rows=_catalog(tables=TABLES[:-1]), events=events
    )
    with pytest.raises(_Fail) as caught:
        session.handler(connection, object())
    assert caught.value.reason_code == "MODE_CONTRACT_ERROR"
    assert events == []
    assert store.markers() == []


@pytest.mark.parametrize("rows", [[], _catalog()])
def test_corrupt_marker_is_never_overwritten(
    tmp_path: Path, rows: list[dict[str, str]]
) -> None:
    for recover in (False, True):
        session, store, connection = _session(tmp_path, rows=rows, recover=recover)
        store.path.write_bytes(b"{not json")
        before = store.path.read_bytes()
        with pytest.raises(_Fail) as caught:
            session.handler(connection, object())
        assert caught.value.reason_code == "ARTIFACT_INVALID"
        assert caught.value.exit_code == recovery.EXIT_USAGE
        assert store.path.read_bytes() == before
    store.path.unlink()


@pytest.mark.parametrize("rows", [[], _catalog()])
def test_mismatched_marker_is_never_overwritten(
    tmp_path: Path, rows: list[dict[str, str]]
) -> None:
    for recover in (False, True):
        session, store, connection = _session(tmp_path, rows=rows, recover=recover)
        foreign = dict(session.expected_marker)
        foreign["source_archive_sha256"] = "9" * 64
        store.save(foreign)
        before = store.path.read_bytes()
        with pytest.raises(_Fail) as caught:
            session.handler(connection, object())
        assert caught.value.reason_code == "ARTIFACT_MISMATCH"
        assert caught.value.exit_code == recovery.EXIT_MISMATCH
        assert store.path.read_bytes() == before
    store.path.unlink()


def test_valid_marker_pointing_at_fresh_db_is_mismatch(tmp_path: Path) -> None:
    """DB가 비었는데 marker가 남아 있으면 덮어쓰지 않는다."""

    session, store, connection = _session(tmp_path, rows=[])
    _write_expected(store, session)
    with pytest.raises(_Fail) as caught:
        session.handler(connection, object())
    assert caught.value.reason_code == "ARTIFACT_MISMATCH"


def test_marker_write_failure_is_reported_after_commit(tmp_path: Path) -> None:
    session, store, connection = _session(tmp_path, rows=[])
    session.handler(connection, object())
    session.postcheck(connection, object())

    def explode(_payload: Any) -> None:
        raise OSError("디스크가 가득 찼습니다")

    store.save = explode  # type: ignore[method-assign]
    with pytest.raises(_Fail) as caught:
        session.post_commit(connection, object())
    assert caught.value.reason_code == "ARTIFACT_WRITE_FAILED"
    assert caught.value.exit_code == recovery.EXIT_USAGE


def test_postcheck_without_handler_is_contract_error(tmp_path: Path) -> None:
    session, _store, connection = _session(tmp_path, rows=[])
    with pytest.raises(_Fail) as caught:
        session.postcheck(connection, object())
    assert caught.value.reason_code == "MODE_CONTRACT_ERROR"


def test_handler_never_writes_marker(tmp_path: Path) -> None:
    """marker는 오직 post-commit hook에서만 생긴다(marker-last, 계획 §7.2).

    handler에서 저장하면 transaction이 rollback돼도 marker가 남아 "적재됐다"는
    거짓 증적이 만들어진다.
    """

    for rows in ([], _catalog()):
        session, store, connection = _session(tmp_path, rows=rows)
        try:
            session.handler(connection, object())
        except _Fail:
            pass
        assert store.markers() == []
        try:
            session.postcheck(connection, object())
        except _Fail:
            pass
        assert store.markers() == []


def test_profile_alone_changes_live_fingerprint() -> None:
    """table_fingerprints가 완전히 같아도 profile이 다르면 값이 달라야 한다.

    runtime/evaluation은 `action_history` 때문에 fingerprint도 다르므로, profile
    필드가 실제로 해시에 들어가는지는 **같은 fingerprint**로만 확인할 수 있다.
    """

    shared = recovery.table_fingerprints(ACCEPTANCES, "runtime")
    common = {
        "dataset_epoch": recovery.DATASET_EPOCH,
        "schema_sha256": "3" * 64,
        "fingerprints": shared,
    }
    assert recovery.live_db_fingerprint(profile="runtime", **common) != (
        recovery.live_db_fingerprint(profile="evaluation", **common)
    )


def test_dataset_epoch_alone_changes_live_fingerprint() -> None:
    shared = recovery.table_fingerprints(ACCEPTANCES, "runtime")
    common = {"profile": "runtime", "schema_sha256": "3" * 64, "fingerprints": shared}
    assert recovery.live_db_fingerprint(
        dataset_epoch=recovery.DATASET_EPOCH, **common
    ) != recovery.live_db_fingerprint(dataset_epoch="kosa_0813", **common)


def test_schema_sha_alone_changes_live_fingerprint() -> None:
    shared = recovery.table_fingerprints(ACCEPTANCES, "runtime")
    common = {
        "dataset_epoch": recovery.DATASET_EPOCH,
        "profile": "runtime",
        "fingerprints": shared,
    }
    assert recovery.live_db_fingerprint(
        schema_sha256="3" * 64, **common
    ) != recovery.live_db_fingerprint(schema_sha256="4" * 64, **common)


# ---------------------------------------------------------------------------
# 구현리뷰 1차 필수 1 — platform 경계
# ---------------------------------------------------------------------------


_NO_NOFOLLOW_PROBE = """
import os, sys
# Windows에는 애초에 없다. 무조건 지우면 probe 자체가 AttributeError로 죽는다
# (구현리뷰 2차 필수 1).
if hasattr(os, "O_NOFOLLOW"):
    del os.O_NOFOLLOW
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import rehearsal_recovery as recovery  # import 시점에 죽으면 안 된다

root = Path(sys.argv[2])
store = recovery.MarkerStore(root, "runtime", trusted_root=root)
store.save({"payload": "value"})
print("saved" if store.path.exists() else "missing")
"""


@pytest.mark.windows_contract
def test_save_works_without_o_nofollow(tmp_path: Path) -> None:
    """Windows에는 `os.O_NOFOLLOW`가 없다. 참조만으로 죽으면 안 된다(필수 1).

    `AttributeError`는 `OSError`가 아니라서 post-commit의 저장 실패 처리에 잡히지
    않고 public reason이 `INTERNAL_ERROR`로 축소된다.

    모듈 상수를 monkeypatch로 덮으면 `getattr` 방어를 되돌려도 테스트가 통과한다.
    그래서 **별도 프로세스에서 `os.O_NOFOLLOW`를 지운 뒤 import**한다.
    """

    completed = subprocess.run(
        [sys.executable, "-c", _NO_NOFOLLOW_PROBE, str(SCRIPTS_ROOT), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "saved"


@pytest.mark.windows_contract
def test_directory_fsync_is_skipped_where_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows에서는 디렉터리 fd를 열려고 시도조차 하지 않는다(필수 1)."""

    opened: list[Any] = []
    monkeypatch.setattr(recovery, "DIRECTORY_FSYNC_SUPPORTED", False)
    monkeypatch.setattr(
        recovery.os,
        "open",
        lambda *a, **k: opened.append(a)
        or (_ for _ in ()).throw(AssertionError("directory fd를 열면 안 된다")),
    )
    recovery.fsync_directory(tmp_path)
    assert opened == []


@pytest.mark.windows_contract
def test_directory_fsync_never_raises_on_this_platform(tmp_path: Path) -> None:
    """POSIX에서는 실제로 수행하고 Windows에서는 건너뛴다. 어느 쪽도 던지지 않는다."""

    recovery.fsync_directory(tmp_path)


@pytest.mark.windows_contract
def test_probe_survives_platform_without_o_nofollow(tmp_path: Path) -> None:
    """probe 자체가 Windows에서 실행 가능해야 한다(구현리뷰 2차 필수 1).

    `del os.O_NOFOLLOW`를 무조건 실행하면 속성이 애초에 없는 Windows에서 probe가
    `AttributeError`로 죽어 Gate가 성립하지 않는다. 속성을 먼저 없앤 부모에서
    probe를 실행해 그 상황을 재현한다.
    """

    script = tmp_path / "probe.py"
    script.write_text(_NO_NOFOLLOW_PROBE, encoding="utf-8")
    target = tmp_path / "markers"
    target.mkdir()
    wrapper = (
        "import os, runpy, sys\n"
        "if hasattr(os, 'O_NOFOLLOW'):\n"
        "    del os.O_NOFOLLOW\n"
        "runpy.run_path(sys.argv[3], run_name='__main__')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", wrapper, str(SCRIPTS_ROOT), str(target), str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "saved"


@pytest.mark.windows_contract
def test_lock_writes_one_byte_before_windows_range_lock(tmp_path: Path) -> None:
    """`msvcrt.locking(..., 1)`은 빈 파일에 걸 수 없다(필수 1).

    실제 Windows 실행은 CI job이 담당하고, 여기서는 adapter가 1 byte를 먼저
    보장하는지 `_msvcrt` 대역으로 확인한다.
    """

    calls: list[str] = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 0

        @staticmethod
        def locking(_fd: int, mode: int, _length: int) -> None:
            calls.append("lock" if mode == 1 else "unlock")

    lock_path = tmp_path / "runtime.lock"
    with open(lock_path, "a+b") as handle:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(recovery, "_fcntl", None)
            patch.setattr(recovery, "_msvcrt", FakeMsvcrt)
            recovery._acquire(handle)
            assert lock_path.stat().st_size >= 1
            recovery._release(handle)
    assert calls == ["lock", "unlock"]


# ---------------------------------------------------------------------------
# 구현리뷰 1차 필수 2 — 저장 실패는 marker를 남기지 않는다
# ---------------------------------------------------------------------------


def _fail_second_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    real = recovery.os.fsync
    seen = {"count": 0}

    def fsync(descriptor: int) -> None:
        seen["count"] += 1
        if seen["count"] >= 2:  # 1회는 파일, 2회째가 디렉터리다
            raise OSError("directory fsync 미지원")
        real(descriptor)

    monkeypatch.setattr(recovery.os, "fsync", fsync)


@pytest.mark.windows_contract
def test_durability_failure_after_replace_leaves_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """replace 뒤 실패해도 marker가 남으면 안 된다(필수 2).

    남으면 `ARTIFACT_WRITE_FAILED`를 보고하고도 다음 명시 복구가
    `RECOVERY_NOT_ALLOWED`로 막히는 막다른 상태가 된다.
    """

    store = recovery.MarkerStore(tmp_path, "runtime")
    _fail_second_fsync(monkeypatch)
    with pytest.raises(OSError):
        store.save({"payload": "value"})
    assert not store.path.exists()
    assert store.markers() == []
    assert not list(tmp_path.glob("*.tmp"))


def test_write_failure_converges_to_explicit_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """저장 실패 → 다음 일반 apply는 `RECOVERY_REQUIRED`, 명시 복구는 성공한다."""

    session, store, connection = _session(tmp_path, rows=[])
    session.handler(connection, object())
    session.postcheck(connection, object())
    _fail_second_fsync(monkeypatch)
    with pytest.raises(_Fail) as caught:
        session.post_commit(connection, object())
    assert caught.value.reason_code == "ARTIFACT_WRITE_FAILED"
    assert store.markers() == []
    monkeypatch.undo()

    # DB는 commit된 상태이므로 다음 실행은 ADOPTED_CANDIDATE + marker 없음이다.
    ordinary, _s, adopted = _session(tmp_path, rows=_catalog())
    ordinary.handler(adopted, object())
    with pytest.raises(_Fail) as required:
        ordinary.postcheck(adopted, object())
    assert required.value.reason_code == "RECOVERY_REQUIRED"

    recovered, _s2, adopted2 = _session(tmp_path, rows=_catalog(), recover=True)
    recovered.handler(adopted2, object())
    recovered.postcheck(adopted2, object())
    recovered.post_commit(adopted2, object())
    assert store.path.read_bytes() == recovery.marker_bytes(recovered.expected_marker)


# ---------------------------------------------------------------------------
# 구현리뷰 1차 필수 3 — 중간 부모 symlink
# ---------------------------------------------------------------------------


@pytest.fixture
def linked_root(tmp_path: Path) -> Path:
    _require_symlinks(tmp_path)
    real = tmp_path / "real-parent" / "markers"
    real.mkdir(parents=True)
    (tmp_path / "linked-parent").symlink_to(tmp_path / "real-parent")
    return tmp_path / "linked-parent" / "markers"


@pytest.mark.windows_contract
def test_ancestor_symlink_is_rejected_for_save(linked_root: Path) -> None:
    store = recovery.MarkerStore(linked_root, "runtime")
    assert linked_root.is_symlink() is False  # terminal component만 보면 통과한다
    with pytest.raises(recovery.MarkerPathError):
        store.save({"payload": "value"})
    assert not (linked_root / "runtime.json").exists()
    assert not list(linked_root.glob("*"))


@pytest.mark.windows_contract
def test_ancestor_symlink_is_rejected_for_read(linked_root: Path) -> None:
    (linked_root / "runtime.json").write_text("{}", encoding="utf-8")
    store = recovery.MarkerStore(linked_root, "runtime")
    assert store.read()[0] is recovery.MarkerState.INVALID
    # 거부하면서 기존 파일을 건드리지 않는다.
    assert (linked_root / "runtime.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.windows_contract
def test_ancestor_symlink_is_rejected_for_lock(linked_root: Path) -> None:
    with pytest.raises(recovery.MarkerPathError):
        with recovery.marker_lock(linked_root, "runtime"):
            pass
    assert not (linked_root / "runtime.lock").exists()


@pytest.mark.windows_contract
def test_symlinked_lock_file_is_rejected(tmp_path: Path) -> None:
    _require_symlinks(tmp_path)
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"original")
    root = tmp_path / "markers"
    root.mkdir()
    (root / "runtime.lock").symlink_to(outside)
    with pytest.raises(recovery.MarkerPathError):
        with recovery.marker_lock(root, "runtime"):
            pass
    assert outside.read_bytes() == b"original"


@pytest.mark.windows_contract
def test_marker_path_error_is_oserror() -> None:
    """post-commit의 저장 실패 처리가 그대로 `ARTIFACT_WRITE_FAILED`로 외부화한다."""

    assert issubclass(recovery.MarkerPathError, OSError)


def test_symlink_rejection_maps_to_artifact_write_failed(
    tmp_path: Path, linked_root: Path
) -> None:
    session, _store, connection = _session(tmp_path, rows=[])
    session.handler(connection, object())
    session.postcheck(connection, object())
    session._store = recovery.MarkerStore(linked_root, "runtime")
    with pytest.raises(_Fail) as caught:
        session.post_commit(connection, object())
    assert caught.value.reason_code == "ARTIFACT_WRITE_FAILED"


@pytest.mark.windows_contract
def test_trusted_root_scopes_the_check(tmp_path: Path) -> None:
    """`trusted_root` 아래만 검사한다. 그 위는 호출자가 신뢰한다고 선언한 것이다."""

    _require_symlinks(tmp_path)
    real = tmp_path / "real-parent" / "markers"
    real.mkdir(parents=True)
    (tmp_path / "linked-parent").symlink_to(tmp_path / "real-parent")
    root = tmp_path / "linked-parent" / "markers"
    store = recovery.MarkerStore(root, "runtime", trusted_root=root)
    store.save({"payload": "value"})
    assert store.read()[0] is recovery.MarkerState.VALID

    outside = recovery.MarkerStore(root, "runtime", trusted_root=tmp_path / "other")
    with pytest.raises(recovery.MarkerPathError):
        outside.save({"payload": "value"})


@pytest.mark.windows_contract
def test_windows_shaped_platform_completes_full_store_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows의 세 제약을 한꺼번에 걸고 store 전 주기를 돈다(필수 1 종합).

    `O_NOFOLLOW` 부재 · 디렉터리 fsync 미지원 · `fcntl` 부재를 동시에 적용한다.
    실제 Windows 실행은 CI job이 담당하고, 이 테스트는 macOS·Linux에서도 회귀가
    깨지도록 같은 경계를 국소적으로 재현한다.
    """

    calls: list[str] = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 0

        @staticmethod
        def locking(_fd: int, mode: int, _length: int) -> None:
            calls.append("lock" if mode == 1 else "unlock")

    monkeypatch.setattr(recovery, "O_NOFOLLOW", 0)
    monkeypatch.setattr(recovery, "DIRECTORY_FSYNC_SUPPORTED", False)
    monkeypatch.setattr(recovery, "_fcntl", None)
    monkeypatch.setattr(recovery, "_msvcrt", FakeMsvcrt)

    payload = recovery.build_marker(
        _identity(
            table_fingerprints=recovery.table_fingerprints(ACCEPTANCES, "runtime")
        )
    )
    store = recovery.MarkerStore(tmp_path, "runtime", trusted_root=tmp_path)
    with recovery.marker_lock(tmp_path, "runtime", trusted_root=tmp_path):
        store.save(payload)
        state, loaded = store.read()
        assert state is recovery.MarkerState.VALID
        assert loaded == payload
        assert store.markers() == [store.path]
        assert not list(tmp_path.glob("*.tmp"))
    assert calls == ["lock", "unlock"]


_CLEAN_JOB_PROBE = """
import sys, importlib.abc


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise ModuleNotFoundError("clean Windows job: no sqlalchemy")
        return None


sys.meta_path.insert(0, Block())
import pytest

sys.exit(
    pytest.main(
        ["-q", sys.argv[1], "-m", "windows_contract", "-p", "no:cacheprovider"]
    )
)
"""


def test_windows_contract_subset_needs_only_pytest() -> None:
    """Windows job은 `pytest`만 설치한다. marker 집합이 그 계약을 지켜야 한다.

    substring `-k "store"`가 `test_recover_restores_marker_only`까지 잡아 SQLAlchemy를
    요구했던 것이 2차 필수 1이다. 여기서 SQLAlchemy를 차단한 채 같은 집합을 실행해,
    앞으로 상태전이 테스트에 marker가 잘못 붙어도 바로 드러나게 한다.
    """

    completed = subprocess.run(
        [sys.executable, "-c", _CLEAN_JOB_PROBE, str(Path(__file__))],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert " passed" in completed.stdout
    assert "failed" not in completed.stdout
