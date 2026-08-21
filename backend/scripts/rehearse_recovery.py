"""격리 재실행·복구 rehearsal CLI(`V5-CM-2.5`).

profile 하나마다 일회성 PostgreSQL lifecycle을 **한 번** 열고, 그 안에서 아래
전이를 실제 transaction과 임시 marker로 증명한다(계획 §7.3).

```text
1 source 4개 SHA snapshot
2 실패 주입 apply    -> rollback, relation 0, marker 0
3 정상 apply         -> commit, marker-last
4 동일 apply         -> no-op (DDL/COPY 0회, marker 불변)
5 marker 제거        -> 유실 상황 구성
6 일반 apply         -> RECOVERY_REQUIRED
7 --recover-artifact -> marker만 원자 복구
8 fingerprint·source SHA·marker payload·cleanup 확인
```

내부에서 **의도적으로** 재현한 rollback과 `RECOVERY_REQUIRED`는 기대 결과로 소비하고
외부로 출력하지 않는다. 성공은 exit 0 · stdout/stderr 0B다.

공용 DB에 대한 apply·marker 커밋은 `V5-CM-2.6` 범위이며 이 CLI는 수행하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rebuild_runner  # noqa: E402
import rehearsal_profile_verifier  # noqa: E402
import rehearsal_recovery as recovery  # noqa: E402
import rehearsal_schema  # noqa: E402
import rehearse_schema as wrapper  # noqa: E402
from rehearsal_postgres import (  # noqa: E402
    RehearsalEndpoint,
    RehearsalError,
    one_off_postgres,
)

EXIT_OK = wrapper.EXIT_OK
EXIT_MISMATCH = wrapper.EXIT_MISMATCH
EXIT_USAGE = wrapper.EXIT_USAGE

LifecycleFactory = Callable[..., Iterator[RehearsalEndpoint]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--profile", required=True, choices=("runtime", "evaluation"))
    return parser


def _fail(reason_code: str, exit_code: int) -> RehearsalError:
    """CLI 경계용. wrapper의 `_emit_error()`가 그대로 소비한다."""

    return RehearsalError(reason_code, exit_code)


def _runner_fail(reason_code: str, exit_code: int) -> rebuild_runner.RunnerError:
    """transaction 안에서 던질 때 쓴다.

    `rebuild_runner.run()`은 `RunnerError`만 reason JSON으로 외부화하고 나머지는
    `INTERNAL_ERROR`로 숨긴다. handler·postcheck·post-commit hook이 내는 실패는
    반드시 이 타입이어야 §9 분류가 살아남는다.
    """

    return rebuild_runner.RunnerError(reason_code, exit_code)


def _source_digests(
    archive: Path, artifact_paths: rebuild_runner.ArtifactPaths
) -> tuple[str, ...]:
    """archive·epoch·manifest·intake 네 입력의 SHA snapshot(계획 §8.1)."""

    paths = (
        archive,
        artifact_paths.epoch,
        artifact_paths.source_manifest,
        artifact_paths.intake,
    )
    return tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)


def _identity(
    snapshot: wrapper.VerifiedArchiveSnapshot,
    *,
    profile: str,
    database: str,
    artifact_paths: rebuild_runner.ArtifactPaths,
    archive_sha: str,
) -> recovery.MarkerIdentity:
    return recovery.MarkerIdentity(
        profile=profile,
        database=database,
        logical_targets=rebuild_runner.REHEARSAL_LOGICAL_TARGETS[profile],
        source_archive_sha256=archive_sha,
        source_manifest_sha256=hashlib.sha256(
            artifact_paths.source_manifest.read_bytes()
        ).hexdigest(),
        schema_sha256=hashlib.sha256(snapshot.schema_bytes).hexdigest(),
    )


def _session(
    snapshot: wrapper.VerifiedArchiveSnapshot,
    *,
    profile: str,
    store: recovery.MarkerStore,
    identity: recovery.MarkerIdentity,
    recover_artifact: bool,
    poison: Callable[[Any, Any], None] | None = None,
    reference: Any = rehearsal_profile_verifier.FINAL_REFERENCE,
) -> recovery.RecoverySession:
    """2.3 composite와 2.4 acceptance를 그대로 재사용해 세션을 만든다.

    `reference`는 2.4와 같은 축소 fixture 전용 주입점이다. `_run()`·CLI는 넘기지
    않으므로 production 경로는 항상 최종 epoch 상수를 쓴다.
    """

    fresh_handler, acceptance_postcheck = wrapper._composite(
        snapshot, profile, reference=reference
    )
    if poison is not None:
        base = fresh_handler

        def fresh_handler(connection: Any, plan: Any) -> None:  # noqa: F811
            base(connection, plan)
            poison(connection, plan)

    return recovery.RecoverySession(
        store=store,
        identity=identity,
        acceptances=snapshot.acceptances,
        profile=profile,
        expected_tables=sorted(rehearsal_schema.EXPECTED_TABLES),
        expected_indexes=sorted(rehearsal_schema.EXPECTED_INDEXES),
        fresh_handler=fresh_handler,
        acceptance_postcheck=acceptance_postcheck,
        error_factory=_runner_fail,
        recover_artifact=recover_artifact,
    )


def _invoke(
    endpoint: RehearsalEndpoint,
    session: recovery.RecoverySession,
    *,
    profile: str,
    artifact_paths: rebuild_runner.ArtifactPaths,
    recover_artifact: bool = False,
) -> wrapper.RunnerOutcome:
    return wrapper._call_runner(
        endpoint,
        profile=profile,
        artifact_paths=artifact_paths,
        handler=session.handler,
        postcheck=session.postcheck,
        mode=rebuild_runner.RunMode.APPLY,
        post_commit=session.post_commit,
        recover_artifact=recover_artifact,
    )


def _expect(outcome: wrapper.RunnerOutcome, reason: str | None, exit_code: int) -> None:
    """기대한 결과가 아니면 시나리오가 성립하지 않은 것이다."""

    if outcome.reason_code != reason or outcome.exit_code != exit_code:
        raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)


def _relation_count(endpoint: RehearsalEndpoint) -> int:
    import psycopg

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


def _poison(connection: Any, _plan: Any) -> None:
    """schema·COPY 성공 뒤 transaction을 실패시킨다(계획 §8.1).

    DB를 건드리지 않고 typed 실패만 던진다. 같은 transaction이 통째로 rollback되어야
    public relation 0·marker 0으로 돌아온다.
    """

    raise _runner_fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)


def _rehearse_profile(
    endpoint: RehearsalEndpoint,
    snapshot: wrapper.VerifiedArchiveSnapshot,
    *,
    profile: str,
    artifact_paths: rebuild_runner.ArtifactPaths,
    marker_root: Path,
    archive_sha: str,
) -> None:
    # CLI가 직접 만든 디렉터리이므로 신뢰 경계로 선언한다. macOS의
    # `/var -> /private/var`처럼 플랫폼이 소유한 symlink를 오탐하지 않는다.
    store = recovery.MarkerStore(marker_root, profile, trusted_root=marker_root)
    identity = _identity(
        snapshot,
        profile=profile,
        database=endpoint.database,
        artifact_paths=artifact_paths,
        archive_sha=archive_sha,
    )

    def make(recover: bool = False, poison: Any = None) -> recovery.RecoverySession:
        return _session(
            snapshot,
            profile=profile,
            store=store,
            identity=identity,
            recover_artifact=recover,
            poison=poison,
        )

    with recovery.marker_lock(marker_root, profile, trusted_root=marker_root):
        # 2. 실패 주입 -> rollback
        _expect(
            _invoke(
                endpoint,
                make(poison=_poison),
                profile=profile,
                artifact_paths=artifact_paths,
            ),
            "MODE_CONTRACT_ERROR",
            EXIT_MISMATCH,
        )
        if _relation_count(endpoint) != 0 or store.markers():
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)

        # 3. 정상 apply -> commit -> marker-last
        applied = make()
        _expect(
            _invoke(endpoint, applied, profile=profile, artifact_paths=artifact_paths),
            None,
            EXIT_OK,
        )
        if applied.outcome is not recovery.Outcome.APPLIED:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        first_marker = store.path.read_bytes()
        if len(store.markers()) != 1:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)

        # 4. 동일 apply -> no-op
        noop = make()
        _expect(
            _invoke(endpoint, noop, profile=profile, artifact_paths=artifact_paths),
            None,
            EXIT_OK,
        )
        if noop.outcome is not recovery.Outcome.NOOP:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        if store.path.read_bytes() != first_marker:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)

        # valid marker에 대한 복구 요청은 거부된다.
        _expect(
            _invoke(
                endpoint,
                make(recover=True),
                profile=profile,
                artifact_paths=artifact_paths,
                recover_artifact=True,
            ),
            "RECOVERY_NOT_ALLOWED",
            EXIT_MISMATCH,
        )

        # 5·6. marker 유실 -> 일반 apply 거부
        store.path.unlink()
        _expect(
            _invoke(endpoint, make(), profile=profile, artifact_paths=artifact_paths),
            "RECOVERY_REQUIRED",
            EXIT_MISMATCH,
        )
        if store.markers():
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)

        # 7. 명시 복구 -> marker만 원자 복구
        recovered = make(recover=True)
        _expect(
            _invoke(
                endpoint,
                recovered,
                profile=profile,
                artifact_paths=artifact_paths,
                recover_artifact=True,
            ),
            None,
            EXIT_OK,
        )
        if recovered.outcome is not recovery.Outcome.RECOVER:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)

        # 8. 복구본은 최초본과 byte-identical이다.
        if store.path.read_bytes() != first_marker:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        if len(store.markers()) != 1:
            raise _fail("MODE_CONTRACT_ERROR", EXIT_MISMATCH)


def _run(
    argv: Sequence[str] | None,
    *,
    artifact_paths: rebuild_runner.ArtifactPaths = (
        rebuild_runner.DEFAULT_ARTIFACT_PATHS
    ),
    lifecycle: LifecycleFactory = one_off_postgres,
) -> int:
    args = _parser().parse_args(argv)
    archive = Path(args.archive)
    snapshot = wrapper._verified_archive_snapshot(archive, artifact_paths, args.profile)
    rebuild_runner.validate_artifacts(artifact_paths)
    before = _source_digests(archive, artifact_paths)

    with tempfile.TemporaryDirectory() as temporary:
        # resolve해 두면 아래 symlink 검사가 플랫폼 경로에서 오탐하지 않는다.
        marker_root = Path(temporary).resolve()
        with lifecycle(
            database=wrapper._database_for_profile(args.profile)
        ) as endpoint:
            _rehearse_profile(
                endpoint,
                snapshot,
                profile=args.profile,
                artifact_paths=artifact_paths,
                marker_root=marker_root,
                archive_sha=before[0],
            )

    # source 4개는 실행 전후로 한 바이트도 달라지지 않는다.
    if _source_digests(archive, artifact_paths) != before:
        raise _fail("ARCHIVE_MISMATCH", EXIT_MISMATCH)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(argv)
    except RehearsalError as exc:
        return wrapper._emit_error(exc)
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        return wrapper._emit_error(RehearsalError("INTERRUPTED", EXIT_USAGE))
    except Exception:
        return wrapper._emit_error(RehearsalError("INTERNAL_ERROR", EXIT_USAGE))


if __name__ == "__main__":
    sys.exit(main())
