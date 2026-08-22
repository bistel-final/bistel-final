"""공용 3 DB 전환 CLI(`V5-CM-2.6`).

고정 순서 `kosa_agent_e2e` → `kosa_agent` → `kosa_text2sql`로만 동작한다. target을
인자로 고를 수 없고 순서를 바꿀 수 없다.

**기본 동작은 read-only preflight다.** mutation은 아래를 전부 통과해야만 시작한다.

1. approval artifact가 exact schema이고 현재 preflight hash와 일치
2. target별 backup·restore receipt
3. `--confirm-target`이 현재 target 이름과 exact
4. `--change-ref`가 approval과 exact

하나라도 어긋나면 **DB에 연결하기 전에** 거부한다. 연결 자체가 증적이라 순서가 중요하다.

연결 규칙(계획 §5.3)

- mutation-capable session은 **현재 target에만** 만든다.
- 나머지 두 DB에는 read-only inspector만 만든다.
- 실패로 판정한 즉시 새 connection은 read-only든 mutation이든 0건이다.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import postgres_transition as transition  # noqa: E402
import rehearse_schema as wrapper  # noqa: E402

EXIT_OK = transition.EXIT_OK
EXIT_MISMATCH = transition.EXIT_MISMATCH
EXIT_USAGE = transition.EXIT_USAGE
EXIT_CONFIRM_REQUIRED = transition.EXIT_CONFIRM_REQUIRED

#: read-only inspector / mutation session factory. 테스트가 호출 수를 센다.
SessionFactory = Callable[[str], Any]

#: mutation session factory는 두 단계를 나눠 준다.
#: `(mutex_connection, transaction_factory)` — mutex는 transaction **밖에서** 잡는다.
#: 두 값을 한 factory가 주는 이유는 같은 physical session이어야 session-level
#: advisory lock이 그 transaction에서 보이기 때문이다(구현리뷰 7차 필수 1).
#: 한 target의 transaction 본문. 묶음 3에서만 배선한다.
TargetHandler = Callable[[Any, str, "transition.TargetInventory"], None]

#: 공용 session factory·handler는 `V5-CM-2.6` 묶음 3에서만 채운다. 묶음 1에서는
#: `rebuild_runner`의 registry와 같이 의도적으로 비어 있다.
PUBLIC_SESSIONS: dict[str, Any] = {}


class TransitionAbort(RuntimeError):
    """CLI 경계의 **유일한** 실패 타입.

    `postgres_transition.TransitionError`와 `postgres_backup.BackupError`가 각자
    타입으로 올라오면 호출부가 어느 것을 잡아야 할지 갈린다. `V5-CM-2.5`에서
    `RunnerError`/`RehearsalError`를 섞었다가 분류가 `INTERNAL_ERROR`로 무너진 적이
    있다. 여기서는 `_boundary()`가 전부 이 타입으로 모은다.
    """

    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exit_code = exit_code


@contextlib.contextmanager
def _boundary() -> Iterator[None]:
    """하위 모듈의 typed 실패를 `TransitionAbort` 하나로 정규화한다.

    session·orchestrator의 실패도 여기 포함한다. 빠뜨리면 "자격증명이 없다"가
    `INTERNAL_ERROR`로 뭉개져 운영자가 원인을 알 수 없다(구현리뷰 10차 필수 4).
    """

    import backup_orchestrator
    import postgres_backup
    import transition_sessions

    typed = (
        transition.TransitionError,
        postgres_backup.BackupError,
        transition_sessions.SessionError,
        backup_orchestrator.OrchestrationError,
    )
    try:
        yield None
    except TransitionAbort:
        raise
    except typed as exc:
        raise TransitionAbort(exc.reason_code, exc.exit_code) from exc


@dataclass
class ConnectionLedger:
    """연결을 누가 몇 번 열었는지 센다. fail-stop 회귀의 근거다."""

    read_only: list[str] = field(default_factory=list)
    mutating: list[str] = field(default_factory=list)
    closed: bool = False

    def open_read_only(self, database: str) -> None:
        if self.closed:
            raise TransitionAbort("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        self.read_only.append(database)

    def open_mutating(self, database: str) -> None:
        if self.closed:
            raise TransitionAbort("MODE_CONTRACT_ERROR", EXIT_MISMATCH)
        self.mutating.append(database)

    def close(self) -> None:
        self.closed = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    # 사후 증적. DB에 닿지 않는다(구현리뷰 16차 필수 3).
    parser.add_argument("--closure", action="store_true")
    parser.add_argument("--approval")
    parser.add_argument("--backup-root")
    parser.add_argument("--receipt", action="append")
    parser.add_argument("--change-ref")
    # 세 target을 **순서대로 전부** 명시해야 한다. 하나만 받으면 순차 적용을 완주할 수
    # 없고 첫 DB만 바뀐 채 항상 실패한다(구현리뷰 1차 필수 1).
    parser.add_argument("--confirm-target", action="append")
    return parser


def _select_mode(args: argparse.Namespace) -> str:
    chosen = [
        name
        for name, on in (
            ("preflight", args.preflight),
            ("apply", args.apply),
            ("closure", args.closure),
        )
        if on
    ]
    if len(chosen) != 1:
        raise TransitionAbort("MODE_CONFLICT", EXIT_USAGE)
    return chosen[0]


def _validate_apply_arguments(args: argparse.Namespace) -> None:
    """mutation 인자 검증. 이 함수는 DB에 닿지 않는다."""

    if args.approval is None or args.backup_root is None:
        raise TransitionAbort("APPROVAL_REQUIRED", EXIT_CONFIRM_REQUIRED)
    if len(args.receipt or ()) != len(transition.ORDERED_TARGETS):
        # target마다 backup·restore receipt가 하나씩 있어야 한다(계획 §7).
        raise TransitionAbort("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED)
    if args.change_ref is None or not transition.CHANGE_REF.fullmatch(args.change_ref):
        raise TransitionAbort("ARG_INVALID", EXIT_USAGE)
    confirmed = tuple(args.confirm_target or ())
    if confirmed != transition.ORDERED_TARGETS:
        # 부분집합·순서 변경·중복 전부 거부한다. 순서가 곧 안전 계약이다.
        raise TransitionAbort("CONFIRM_REQUIRED", EXIT_CONFIRM_REQUIRED)


def _validate_closure_arguments(args: argparse.Namespace) -> None:
    """closure는 이름 규칙으로 실물을 직접 찾는다. receipt 경로·confirm은 읽지 않는다.

    `_validate_apply_arguments()`를 재사용하면 읽지도 않는 인자 6개를 요구하게 되고,
    회귀도 그걸 채우려고 가짜 값을 넘긴다(구현리뷰 17차 권장 1).
    """

    if args.approval is None or args.backup_root is None:
        raise TransitionAbort("APPROVAL_REQUIRED", EXIT_CONFIRM_REQUIRED)
    if args.change_ref is None or not transition.CHANGE_REF.fullmatch(args.change_ref):
        raise TransitionAbort("ARG_INVALID", EXIT_USAGE)
    if args.receipt or args.confirm_target:
        raise TransitionAbort("ARG_INVALID", EXIT_USAGE)


def _validate_preflight_arguments(args: argparse.Namespace) -> None:
    if any(
        value is not None
        for value in (args.approval, args.backup_root, args.change_ref)
    ):
        raise TransitionAbort("ARG_INVALID", EXIT_USAGE)
    if args.confirm_target or args.receipt:
        raise TransitionAbort("ARG_INVALID", EXIT_USAGE)


def _load_json(path: Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE)
        return json.loads(path.read_text(encoding="utf-8"))
    except TransitionAbort:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE) from exc


@contextlib.contextmanager
def _inspect(
    database: str, ledger: ConnectionLedger, factory: SessionFactory
) -> Iterator[Any]:
    ledger.open_read_only(database)
    with factory(database) as connection:
        yield connection


def collect_inventories(
    ledger: ConnectionLedger,
    inspector: SessionFactory,
    targets: Sequence[str] = transition.ORDERED_TARGETS,
) -> dict[str, transition.TargetInventory]:
    """세 DB를 read-only로 읽는다. 어떤 mutation도 보내지 않는다."""

    result: dict[str, transition.TargetInventory] = {}
    for database in targets:
        profile = transition.TARGET_PROFILE[database]
        with _inspect(database, ledger, inspector) as connection:
            result[database] = transition.read_inventory(
                connection, database=database, profile=profile, require_snapshot=True
            )
    return result


def preflight_report(
    inventories: Mapping[str, transition.TargetInventory],
) -> dict[str, Any]:
    """sanitized report. host·port·user·DSN·row content를 넣지 않는다."""

    report: dict[str, Any] = {
        "artifact_type": "postgres_transition_preflight",
        "dataset_epoch": transition.DATASET_EPOCH,
        "ordered_targets": list(transition.ORDERED_TARGETS),
        "targets": {},
    }
    for database in transition.ORDERED_TARGETS:
        report["targets"][database] = preflight_entry(inventories[database])
    report["bundle_sha256"] = bundle_sha256(report["targets"])
    return report


def preflight_entry(inventory: transition.TargetInventory) -> dict[str, Any]:
    """한 target의 report 항목. marker에도 **그대로** 실려 재실행 산출에 쓰인다."""

    return {
        "profile": inventory.profile,
        "server_major": inventory.server_major,
        "state": transition.classify_target(inventory).value,
        "preserved_projection_sha256": transition.preserved_projection_sha256(
            inventory
        ),
        "external_fk_projection_sha256": transition.external_fk_projection_sha256(
            inventory
        ),
        "target_fingerprint_sha256": transition.target_fingerprint(inventory),
        "legacy_view_sha256": inventory.view_sha256,
        "action_history_rows": inventory.row_counts.get("action_history"),
    }


def bundle_sha256(targets: Mapping[str, Any]) -> str:
    import manifest_v3

    return manifest_v3.hash_canonical_rows(
        [{"targets": json.dumps(targets, sort_keys=True)}]
    )


def expected_approval(
    report: Mapping[str, Any],
    inventories: Mapping[str, transition.TargetInventory],
    change_ref: str,
) -> dict[str, Any]:
    """approval이 맞춰야 할 값을 **독립 산출**한다.

    approval 자신의 값을 기대값으로 되돌려주면 형식만 맞는 임의 hex가 통과한다
    (구현리뷰 1차 필수 3). 여기서는 pinned 상수와 현재 inventory에서만 만든다.
    """

    import manifest_v3
    import rebuild_runner

    return {
        "change_ref": change_ref,
        "gate0_inventory_sha256": transition.GATE0_INVENTORY_SHA256,
        "source_manifest_sha256": _file_sha256(rebuild_runner.SOURCE_MANIFEST_PATH),
        "preflight_bundle_sha256": report["bundle_sha256"],
        "compatibility_view_sha256": transition.COMPAT_VIEW_SHA256,
        "compatibility_view_owner_acl_sha256": (
            transition.compatibility_view_owner_acl_sha256()
        ),
        "server_major_by_target": {
            database: inventory.server_major
            for database, inventory in inventories.items()
        },
        "planned_outcome_by_target": {
            database: transition.classify_target(inventory).value
            for database, inventory in inventories.items()
        },
        "preserved_projection_sha256_by_target": {
            database: transition.preserved_projection_sha256(inventory)
            for database, inventory in inventories.items()
        },
        "external_fk_projection_sha256_by_target": {
            database: transition.external_fk_projection_sha256(inventory)
            for database, inventory in inventories.items()
        },
        "target_fingerprint_sha256_by_target": {
            database: transition.target_fingerprint(inventory)
            for database, inventory in inventories.items()
        },
        "_manifest_hash": manifest_v3.HASH_ALGORITHM,
    }


def read_approval(path: Path) -> tuple[Mapping[str, Any], str]:
    """approval을 **한 번의 byte read**로 읽고 그 bytes에서 객체와 digest를 함께 낸다.

    `load → 같은 path 재읽기 hash`로 나누면 두 읽기 사이에 파일이 교체될 수 있다. 그러면
    mutation 판단에는 A를 쓰면서 marker에는 B의 hash를 봉인한다 — `approval_sha256`이
    "실제로 쓴 approval"을 증명하지 못하게 된다(구현리뷰 19차 필수 1).

    symlink·비정규파일·UTF-8·JSON·schema 오류는 그대로 fail-closed다.
    """

    import hashlib

    if path.is_symlink() or not path.is_file():
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE) from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE) from exc
    if not isinstance(payload, Mapping):
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE)
    try:
        transition.validate_approval_schema(payload)
    except transition.TransitionError as exc:
        raise TransitionAbort(exc.reason_code, exc.exit_code) from exc
    return payload, digest


def _load_receipts(args: argparse.Namespace) -> list[Mapping[str, Any]]:
    payloads: list[Mapping[str, Any]] = []
    for raw in args.receipt or ():
        payload = _load_json(Path(raw))
        if not isinstance(payload, Mapping):
            raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE)
        payloads.append(payload)
    return payloads


def _validate_receipts(
    receipts: Sequence[Mapping[str, Any]],
    approval: Mapping[str, Any],
    inventories: Mapping[str, transition.TargetInventory],
    backup_root: Path,
    completed: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, str]]:
    """backup·restore receipt를 **mutation connection 전에** 대조한다(계획 §7).

    `archive_sha256`를 형식만 보면 임의 64hex가 통과한다. archive 이름을
    database·change-ref로 결정하고 **실물 digest**와 비교한다
    (구현리뷰 2차 필수 1-2).

    View sidecar도 같은 규칙으로 이름을 정하고, trusted root 안 regular file인지 본 뒤
    실물 digest를 receipt와 대조한다(구현리뷰 4차 필수 3). sidecar 내용은 **현재 base
    상태에 맞는** View identity여야 한다 — legacy 전용으로 고정하면 이미 전환된 target에
    유효한 증적을 만들 수 없어 재실행이 막힌다(구현리뷰 6차 필수 1).
    """

    import postgres_backup

    by_target = {payload["database"]: payload for payload in receipts}
    if set(by_target) != set(transition.ORDERED_TARGETS):
        raise TransitionAbort("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED)
    change_ref = approval["change_ref"]
    evidence: dict[str, dict[str, str]] = {}
    done = completed or {}
    for database, payload in by_target.items():
        inventory = inventories[database]
        if database in done:
            # 이미 전환된 target의 증적은 **전환 전** 상태를 기술한다. 그 근거는
            # marker가 기록한 pre-state 항목이다(구현리뷰 7차 필수 2).
            entry = done[database]["preflight_target_entry"]
            expected_fp = entry["target_fingerprint_sha256"]
            sidecar_state = transition.BaseState(entry["state"])
            sidecar_view = entry["legacy_view_sha256"]
        else:
            expected_fp = transition.target_fingerprint(inventory)
            sidecar_state = transition.classify_target(inventory)
            sidecar_view = inventory.view_sha256
        path = backup_root / transition.archive_name(database, change_ref)
        postgres_backup.validate_archive_path(path, trusted_root=backup_root)
        digest = postgres_backup.archive_digest(path)
        sidecar = backup_root / transition.view_sidecar_name(database, change_ref)
        postgres_backup.validate_evidence_path(sidecar, trusted_root=backup_root)
        transition.assert_receipt_matches(
            payload,
            inventory=inventories[database],
            change_ref=change_ref,
            preflight_bundle_sha256=approval["preflight_bundle_sha256"],
            archive_sha256=digest,
            view_sidecar_sha256=postgres_backup.archive_digest(sidecar),
            expected_fingerprint=expected_fp,
        )
        # digest가 실물이라는 것과 그 실물이 승인한 View라는 것은 다른 얘기다.
        transition.assert_sidecar_matches(
            _read_sidecar(sidecar),
            database=database,
            profile=inventory.profile,
            state=sidecar_state,
            view_sha256=sidecar_view,
            view_owner=inventory.view_owner,
            view_acl=inventory.view_acl,
            view_comment=inventory.view_comment,
            change_ref=change_ref,
            preflight_bundle_sha256=approval["preflight_bundle_sha256"],
        )
        sidecar_digest = postgres_backup.archive_digest(sidecar)
        # producer가 "marker가 있어야만 정상 증적"이라고 정했으므로 소비자도 그것만
        # 받아들인다. 연결 전에 세 실물 digest까지 대조한다(구현리뷰 13차 필수 2).
        _assert_backup_completed(
            backup_root,
            database,
            change_ref,
            archive_sha256=digest,
            sidecar_sha256=sidecar_digest,
        )
        evidence[database] = {"archive": digest, "sidecar": sidecar_digest}
    return evidence


def _assert_backup_completed(
    backup_root: Path,
    database: str,
    change_ref: str,
    *,
    archive_sha256: str,
    sidecar_sha256: str,
) -> None:
    """completion marker와 세 실물 digest가 일치하는지 본다."""

    import backup_orchestrator
    import postgres_backup

    path = backup_root / backup_orchestrator.completion_name(database, change_ref)
    if not path.is_file():
        raise TransitionAbort("BACKUP_REQUIRED", EXIT_CONFIRM_REQUIRED)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE) from exc
    backup_orchestrator.validate_completion(payload)
    if payload["database"] != database or payload["change_ref"] != change_ref:
        raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["archive_sha256"] != archive_sha256:
        raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
    if payload["view_sidecar_sha256"] != sidecar_sha256:
        raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
    receipt_path = backup_root / backup_orchestrator.receipt_name(database, change_ref)
    postgres_backup.validate_evidence_path(receipt_path, trusted_root=backup_root)
    if payload["receipt_sha256"] != postgres_backup.archive_digest(receipt_path):
        raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).astimezone().isoformat()


def completed_targets(
    approval: Mapping[str, Any],
    backup_root: Path,
    *,
    backup_root_trust: str,
    approval_sha256: str,
) -> dict[str, Mapping[str, Any]]:
    """이 approval로 **이미 완료된** target의 marker를 모은다.

    재실행은 같은 artifact set으로 돼야 한다(구현리뷰 7차 필수 2). 그런데 approval의
    fingerprint는 전환 **전** 상태다. 이미 final이 된 target은 그 값과 다를 수밖에 없다.

    marker가 같은 `change_ref`·`preflight_bundle_sha256`를 담고 있으면 "이 approval로
    저 target은 이미 끝났다"는 기록이다. 그때만 그 target의 기대값을 marker 기준으로
    바꾼다. marker가 없거나 다른 Task의 것이면 tolerance는 없다.
    """

    result: dict[str, Mapping[str, Any]] = {}
    for database in transition.ORDERED_TARGETS:
        path = _marker_path(backup_root, database)
        if not path.exists():
            continue
        payload = _read_marker(path)
        if payload["database"] != database:
            raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
        if payload["change_ref"] != approval["change_ref"]:
            raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
        if payload["preflight_bundle_sha256"] != approval["preflight_bundle_sha256"]:
            raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
        if payload["base_state"] != transition.BaseState.FINAL_ADOPTED.value:
            raise TransitionAbort("BACKUP_INVALID", EXIT_MISMATCH)
        # 이전 실행이 신뢰하지 못하는 root에 남긴 marker이거나 다른 approval의
        # 것이면, **남은 target을 건드리기 전에** 전체를 막는다(구현리뷰 18차 필수 2).
        try:
            transition.assert_marker_identity(
                payload,
                backup_root_trust=backup_root_trust,
                approval_sha256=approval_sha256,
            )
        except transition.TransitionError as exc:
            raise TransitionAbort(exc.reason_code, exc.exit_code) from exc
        result[database] = payload
    return result


def reconstruct_preflight_entry(
    approval: Mapping[str, Any], database: str
) -> dict[str, Any]:
    """marker 없이 commit만 끝난 target의 **전환 전** report 항목을 되살린다.

    `commit 성공 → marker write 전 중단`이면 marker가 없다. 그 target을 복구하려면
    bundle을 다시 계산할 pre-state 항목이 필요한데, 값은 전부 approval과 Gate 0 pin에서
    결정적으로 나온다(구현리뷰 8차 필수 1).

    이 경로는 그 자체로 승인이 되지 않는다. transaction 안에서
    `assert_recoverable_without_marker()`가 실제 상태를 다시 대조한다.
    """

    profile = transition.TARGET_PROFILE[database]
    state = approval["planned_outcome_by_target"][database]
    if state != transition.BaseState.BASE_LEGACY_EPOCH.value:
        raise TransitionAbort("APPROVAL_MISMATCH", EXIT_MISMATCH)
    return {
        "profile": profile,
        "server_major": approval["server_major_by_target"][database],
        "state": state,
        "preserved_projection_sha256": approval[
            "preserved_projection_sha256_by_target"
        ][database],
        "external_fk_projection_sha256": approval[
            "external_fk_projection_sha256_by_target"
        ][database],
        "target_fingerprint_sha256": approval["target_fingerprint_sha256_by_target"][
            database
        ],
        "legacy_view_sha256": transition.LEGACY_VIEW_SHA256,
        "action_history_rows": transition.LEGACY_BASE_ROWS[profile]["action_history"],
    }


def recovered_targets(
    approval: Mapping[str, Any],
    inventories: Mapping[str, transition.TargetInventory],
    completed: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """commit은 끝났는데 marker가 없는 target을 찾는다."""

    result: dict[str, Mapping[str, Any]] = {}
    for database in transition.ORDERED_TARGETS:
        if database in completed:
            continue
        inventory = inventories[database]
        if transition.classify_target(inventory) is not (
            transition.BaseState.FINAL_ADOPTED
        ):
            continue
        result[database] = {
            "preflight_target_entry": reconstruct_preflight_entry(approval, database),
            "target_fingerprint_sha256": transition.target_fingerprint(inventory),
        }
    return result


def resume_aware_expectation(
    expected: dict[str, Any],
    inventories: Mapping[str, transition.TargetInventory],
    completed: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """완료된 target은 marker가, 나머지는 approval이 기대값의 근거다.

    완료된 target의 approval 기대값은 **전환 전** 값이다. 현재 상태와 marker
    post-state의 대조는 transaction 안 `assert_marker_matches()`가 한다.
    """

    if not completed:
        return expected
    patched = dict(expected)
    for key in (
        "planned_outcome_by_target",
        "preserved_projection_sha256_by_target",
        "external_fk_projection_sha256_by_target",
        "target_fingerprint_sha256_by_target",
        "server_major_by_target",
    ):
        patched[key] = dict(patched[key])
    # 완료된 target의 **전환 전** report 항목을 marker에서 되살려 bundle을 다시 만든다.
    # approval의 값을 그대로 기대값으로 쓰면 approval이 자기 자신을 증명하게 된다
    # (구현리뷰 1차 필수 3). marker는 별개 파일이므로 독립 산출이 유지된다.
    entries = {
        database: (
            dict(completed[database]["preflight_target_entry"])
            if database in completed
            else preflight_entry(inventories[database])
        )
        for database in transition.ORDERED_TARGETS
    }
    patched["preflight_bundle_sha256"] = bundle_sha256(entries)

    for database, marker in completed.items():
        entry = marker["preflight_target_entry"]
        patched["planned_outcome_by_target"][database] = entry["state"]
        patched["preserved_projection_sha256_by_target"][database] = entry[
            "preserved_projection_sha256"
        ]
        patched["external_fk_projection_sha256_by_target"][database] = entry[
            "external_fk_projection_sha256"
        ]
        patched["target_fingerprint_sha256_by_target"][database] = entry[
            "target_fingerprint_sha256"
        ]
        patched["server_major_by_target"][database] = entry["server_major"]
    return patched


def _read_marker(path: Path) -> Mapping[str, Any]:
    """COMMITTED marker를 읽고 schema를 판정한다."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE) from exc
    transition.validate_marker_schema(payload)
    return payload


def _marker_path(root: Path, database: str) -> Path:
    return root / transition.marker_name(database)


def _read_sidecar(path: Path) -> Mapping[str, Any]:
    """sidecar를 읽고 schema를 판정한다. 임의 바이트는 여기서 걸린다."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE) from exc
    transition.validate_sidecar_schema(payload)
    return payload


def _file_sha256(path: Path) -> str:
    """실물 파일의 digest. 없거나 symlink면 closure를 막는다."""

    if path.is_symlink() or not path.is_file():
        raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_closure_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH) from exc
    if not isinstance(payload, Mapping):
        raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH)
    return payload


def _require(condition: bool) -> None:
    if not condition:
        raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH)


@contextlib.contextmanager
def _producer_check() -> Iterator[None]:
    """producer validator의 거부를 closure의 단일 reason으로 모은다.

    같은 "묶음이 깨졌다"를 운영자가 artifact 종류마다 다른 reason으로 보면, 무엇을
    고쳐야 하는지 흐려진다(구현리뷰 17차 필수 1).
    """

    import backup_orchestrator as orchestrator

    try:
        yield None
    except TransitionAbort:
        raise
    except (transition.TransitionError, orchestrator.OrchestrationError) as exc:
        raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH) from exc


def collect_closure_digests(
    backup_root: Path,
    change_ref: str,
    *,
    approval: Mapping[str, Any],
    root_trust: str,
    approval_sha256: str,
) -> dict[str, dict[str, str]]:
    """target별 증적 5종이 **한 실행의 유효한 묶음**인지 확인하고 digest를 낸다.

    hash를 복사하기만 하면 그 artifact는 전환을 증명하지 못한다. 디렉터리에 놓인 임의
    receipt와 다른 bundle을 가진 approval도 통과한다(구현리뷰 17차 필수 1). 그래서
    producer의 validator를 **실제 closure 경로에서** 부르고, approval → marker →
    sidecar·receipt·completion → 실물 순으로 같은 `change_ref`·target·profile·preflight
    bundle에 묶였는지 본다.

    하나라도 어긋나면 `CLOSURE_BLOCKED`다. **DB 결과는 되돌리지 않는다** — 전환은 이미
    commit됐고 COMMITTED marker가 그것을 증명한다. 막히는 것은 closure gate와 Git
    게시뿐이다.
    """

    import backup_orchestrator as orchestrator

    bundle = approval["preflight_bundle_sha256"]
    digests: dict[str, dict[str, str]] = {}
    entries: dict[str, Any] = {}
    for database in transition.ORDERED_TARGETS:
        profile = transition.TARGET_PROFILE[database]
        archive_path = backup_root / transition.archive_name(database, change_ref)
        sidecar_path = backup_root / transition.view_sidecar_name(database, change_ref)
        receipt_path = backup_root / orchestrator.receipt_name(database, change_ref)
        completion_path = backup_root / orchestrator.completion_name(
            database, change_ref
        )
        marker_path = _marker_path(backup_root, database)

        # --- COMMITTED marker: 그 target이 실제로 전환됐다는 유일한 증거 ---
        with _producer_check():
            marker = _read_marker(marker_path)
        _require(marker["database"] == database)
        _require(marker["profile"] == profile)
        _require(marker["change_ref"] == change_ref)
        _require(marker["preflight_bundle_sha256"] == bundle)
        # 전환이 끝난 target만 closure 대상이다.
        _require(marker["base_state"] == transition.BaseState.FINAL_ADOPTED.value)
        # mutation 직전에 잰 값과 지금 값이 다르면 실행 중 root 신뢰가 바뀐 것이다
        # (구현리뷰 17차 필수 2).
        with _producer_check():
            transition.assert_marker_identity(
                marker,
                backup_root_trust=root_trust,
                approval_sha256=approval_sha256,
            )
        # marker의 `target_fingerprint_sha256`은 전환 **뒤** 값이라 approval과 다르다.
        # approval이 승인한 것은 전환 **전** 신원이고, 그 값은 marker가 따로 담고 있다.
        _require(
            marker["preflight_target_entry"]["target_fingerprint_sha256"]
            == approval["target_fingerprint_sha256_by_target"][database]
        )

        # --- sidecar ---
        sidecar = _read_closure_json(sidecar_path)
        with _producer_check():
            transition.validate_sidecar_schema(sidecar)
        _require(sidecar["database"] == database)
        _require(sidecar["profile"] == profile)
        _require(sidecar["change_ref"] == change_ref)
        _require(sidecar["preflight_bundle_sha256"] == bundle)

        # --- receipt ---
        receipt = _read_closure_json(receipt_path)
        with _producer_check():
            transition.validate_receipt(receipt)
        _require(receipt["database"] == database)
        _require(receipt["profile"] == profile)
        _require(receipt["change_ref"] == change_ref)
        _require(receipt["preflight_bundle_sha256"] == bundle)
        _require(receipt["restore_verified"] is True)
        _require(receipt["view_sidecar_name"] == sidecar_path.name)
        _require(receipt["archive_sha256"] == marker["archive_sha256"])
        _require(receipt["view_sidecar_sha256"] == marker["view_sidecar_sha256"])

        # --- completion: 세 파일이 다 게시됐다는 유일한 근거 ---
        completion = _read_closure_json(completion_path)
        with _producer_check():
            orchestrator.validate_completion(completion)
        _require(completion["database"] == database)
        _require(completion["change_ref"] == change_ref)

        # --- 실물과 대조 ---
        found = {
            "archive": _file_sha256(archive_path),
            "view_sidecar": _file_sha256(sidecar_path),
            "receipt": _file_sha256(receipt_path),
            "completion": _file_sha256(completion_path),
            "committed_marker": _file_sha256(marker_path),
        }
        _require(found["archive"] == marker["archive_sha256"])
        _require(found["view_sidecar"] == marker["view_sidecar_sha256"])
        _require(found["archive"] == completion["archive_sha256"])
        _require(found["view_sidecar"] == completion["view_sidecar_sha256"])
        _require(found["receipt"] == completion["receipt_sha256"])
        digests[database] = found
        entries[database] = marker["preflight_target_entry"]

    # 세 marker의 **전환 전** entry로 canonical bundle을 다시 만들어 approval과 맞춘다.
    # entry 안의 한 필드만 바꿔도 여기서 걸린다(구현리뷰 18차 필수 1).
    _require(bundle_sha256(entries) == bundle)
    return digests


def record_closure_evidence(
    *,
    approval_path: Path,
    backup_root: Path,
    change_ref: str,
    repository_root: Path,
    now: Callable[[], str] | None = None,
) -> Path:
    """세 target 전환이 끝난 뒤 외부 무결성 기록을 남긴다.

    사전 approval 18키는 **건드리지 않는다.** 그 값들은 전환 전에 확정되고 여기서 쓰는
    값들은 전환 뒤에 생기므로, 한 artifact에 담으면 exact schema가 깨지거나 승인을
    사후 수정하게 된다(구현리뷰 16차 필수 3).

    같은 내용이면 다시 쓰지 않는다. 다른 내용이면 덮지 않고 `CLOSURE_BLOCKED`다 —
    이미 팀에 제출된 기록을 조용히 바꾸지 않는다.
    """

    import postgres_backup

    postgres_backup.validate_backup_root(backup_root, repository_root=repository_root)
    # backup·apply와 **같은** validator로 사후에도 다시 본다(구현리뷰 17차 필수 2).
    mode, rejection = postgres_backup.backup_root_trust(
        backup_root, change_ref=change_ref
    )
    if rejection is not None:
        raise TransitionAbort(*rejection)

    # apply와 **같은** reader다. 객체와 digest가 같은 bytes에서 나온다.
    approval, approval_digest = read_approval(approval_path)
    if approval["change_ref"] != change_ref:
        raise TransitionAbort("APPROVAL_MISMATCH", EXIT_MISMATCH)
    payload = transition.build_closure_evidence(
        change_ref=change_ref,
        approval_sha256=approval_digest,
        digests_by_target=collect_closure_digests(
            backup_root,
            change_ref,
            approval=approval,
            root_trust=mode,
            approval_sha256=approval_digest,
        ),
        backup_root_mode=mode,
        operator_os_user=getpass.getuser(),
        recorded_at=(now or _now)(),
    )
    transition.validate_closure_schema(payload)

    path = backup_root / transition.closure_name(change_ref)
    if path.is_file():
        existing = _load_json(path)
        if not isinstance(existing, Mapping):
            raise TransitionAbort("ARTIFACT_INVALID", EXIT_USAGE)
        transition.validate_closure_schema(existing)
        # 시각·role은 재실행마다 다르다. 그 둘을 뺀 나머지가 같으면 no-op다.
        volatile = ("recorded_at", "operator_os_user")
        if {k: v for k, v in existing.items() if k not in volatile} != {
            k: v for k, v in payload.items() if k not in volatile
        }:
            raise TransitionAbort("CLOSURE_BLOCKED", EXIT_MISMATCH)
        return path

    postgres_backup.atomic_write_json(path, payload, trusted_root=backup_root)
    return path


def _run(
    argv: Sequence[str] | None,
    *,
    inspector: SessionFactory,
    mutator: SessionFactory | None = None,
    handler: TargetHandler | None = None,
    ledger: ConnectionLedger | None = None,
    repository_root: Path = SCRIPTS_ROOT.parents[1],
) -> int:
    args = _parser().parse_args(argv)
    mode = _select_mode(args)
    book = ledger if ledger is not None else ConnectionLedger()

    if mode == "closure":
        # 연결을 열지 않는다. 실물 파일만 본다.
        _validate_closure_arguments(args)
        with _boundary():
            record_closure_evidence(
                approval_path=Path(args.approval),
                backup_root=Path(args.backup_root),
                change_ref=args.change_ref,
                repository_root=repository_root,
            )
        return EXIT_OK

    if mode == "preflight":
        _validate_preflight_arguments(args)
        with _boundary():
            inventories = collect_inventories(book, inspector)
            preflight_report(inventories)
        return EXIT_OK

    # apply. 인자·artifact 검증을 모두 끝낸 뒤에만 연결한다.
    _validate_apply_arguments(args)
    with _boundary():
        import postgres_backup

        root = postgres_backup.validate_backup_root(
            Path(args.backup_root), repository_root=repository_root
        )
        # 증적을 쓸 곳이 신뢰할 수 있는지 **연결 전에** 본다(구현리뷰 17차 필수 2).
        root_trust, rejection = postgres_backup.backup_root_trust(
            root, change_ref=args.change_ref
        )
        if rejection is not None:
            raise TransitionAbort(*rejection)
        # 형식 검증은 연결 **전에** 끝낸다. 깨진 artifact는 연결 0건으로 거부된다.
        # 객체와 digest가 **같은 bytes**에서 나온다 — 이 뒤로 approval path를 다시 읽지
        # 않는다(구현리뷰 18차 필수 1 · 19차 필수 1).
        approval, approval_sha256 = read_approval(Path(args.approval))
        receipts = _load_receipts(args)
        for payload in receipts:
            transition.validate_receipt(payload)

        # 내용 대조는 현재 inventory가 있어야 하므로 read-only 뒤에 한다.
        inventories = collect_inventories(book, inspector)
        report = preflight_report(inventories)
        completed = dict(
            completed_targets(
                approval,
                Path(args.backup_root),
                backup_root_trust=root_trust,
                approval_sha256=approval_sha256,
            )
        )
        # commit은 끝났는데 marker가 없는 target도 재개 대상이다(구현리뷰 8차 필수 1).
        completed.update(recovered_targets(approval, inventories, completed))
        transition.assert_approval_matches(
            approval,
            resume_aware_expectation(
                expected_approval(report, inventories, args.change_ref),
                inventories,
                completed,
            ),
        )
        evidence = _validate_receipts(
            receipts, approval, inventories, Path(args.backup_root), completed
        )

    # 공용 mutation은 묶음 3에서만 배선한다. 여기까지 통과해도 handler가 없으면
    # 아무것도 쓰지 않고 멈춘다(`rebuild_runner`의 빈 registry와 같은 패턴).
    if handler is None or mutator is None:
        raise TransitionAbort("MODE_NOT_WIRED", EXIT_USAGE)
    with _boundary():
        return _apply_targets(
            args,
            approval,
            inventories,
            book,
            inspector,
            mutator,
            handler,
            evidence,
            root_trust=root_trust,
            approval_sha256=approval_sha256,
        )


def _apply_targets(
    args: argparse.Namespace,
    approval: Mapping[str, Any],
    inventories: Mapping[str, transition.TargetInventory],
    book: ConnectionLedger,
    inspector: SessionFactory,
    mutator: SessionFactory,
    handler: TargetHandler,
    evidence: Mapping[str, Mapping[str, str]],
    *,
    root_trust: str,
    approval_sha256: str,
) -> int:
    """고정 순서로 세 target을 처리한다.

    **기준선을 갱신한다.** 첫 target이 legacy → final로 바뀐 뒤에도 비교 기준을
    preflight 시점 legacy로 두면, 두 번째 target을 처리한 뒤 첫 target을 "비대상 변경"
    으로 오판해 정상 전환이 완주하지 못한다(구현리뷰 4차 필수 1).

    **재확인은 lock을 잡은 mutation transaction 안에서 한다.** 같은 session에서 다시
    읽고, `classify_target()` exact 허용 상태와 approval fingerprint를 모두 통과한
    뒤에만 handler를 부른다. handler에는 preflight의 stale inventory가 아니라 **그때
    읽은 inventory**를 넘긴다(구현리뷰 4차 필수 2).

    **이미 final인 target은 no-op으로 완주한다.** 앞 target commit 뒤 실패하면 재실행이
    필요한데, 모든 target에 전환을 강요하면 첫 target에서 막힌다(구현리뷰 5차 필수 2).
    no-op은 `SHARE`만 들고 끝나므로 공용 조회를 막지 않는다(구현리뷰 6차 필수 3).

    **보존 대상 불변을 commit 전에 확인한다.** handler가 base 9 밖을 건드렸다면 거기서
    멈춘다. 갱신된 값을 기준선으로 채택하면 비대상 검사까지 함께 통과한다
    (구현리뷰 5차 필수 1).

    **어떤 실패든** ledger를 즉시 닫는다(구현리뷰 1차 필수 2).
    """

    fingerprints = {
        database: transition.target_fingerprint(inventory)
        for database, inventory in inventories.items()
    }
    approved = approval["target_fingerprint_sha256_by_target"]
    change_ref = approval["change_ref"]
    bundle = approval["preflight_bundle_sha256"]
    backup_root = Path(args.backup_root)
    try:
        for database in transition.ORDERED_TARGETS:
            profile = transition.TARGET_PROFILE[database]
            marker_path = _marker_path(backup_root, database)
            existing = _read_marker(marker_path) if marker_path.exists() else None
            recovered = False

            book.open_mutating(database)
            with _target_session(mutator, database) as connection:
                started = time.monotonic()
                clock = transition.BudgetClock(started)
                with connection.begin():
                    transition.acquire_target_locks(
                        connection, database=database, clock=clock
                    )
                    current = _read_locked(connection, database, profile, clock)
                    state = transition.classify_target(current)

                    if state is transition.BaseState.FINAL_ADOPTED:
                        # 이미 전환된 target. 읽기만 하고 아무것도 보내지 않는다.
                        # base 9는 `SHARE`인 채로 둔다 — 공용 조회를 막지 않는다.
                        after = _read_locked(connection, database, profile, clock)
                        transition.assert_target_untouched(current, after)
                        if existing is None:
                            # commit은 끝났는데 marker가 없는 상태다. approval이 기록한
                            # 전환 전 불변량과 대조해 복구 가능한지 본다.
                            recovered = True
                            transition.assert_recoverable_without_marker(
                                after,
                                preserved_projection_sha256=approval[
                                    "preserved_projection_sha256_by_target"
                                ][database],
                                external_fk_projection_sha256=approval[
                                    "external_fk_projection_sha256_by_target"
                                ][database],
                            )
                        else:
                            # 완료된 target의 기준은 approval의 pre-state가 아니라
                            # marker다. `completed_targets()`가 approval에 묶었다.
                            transition.assert_marker_matches(
                                existing,
                                inventory=after,
                                change_ref=change_ref,
                                preflight_bundle_sha256=bundle,
                                backup_root_trust=root_trust,
                                approval_sha256=approval_sha256,
                            )
                    else:
                        if transition.target_fingerprint(current) != approved[database]:
                            raise TransitionAbort("APPROVAL_MISMATCH", EXIT_MISMATCH)
                        if existing is not None:
                            # legacy인데 COMMITTED marker가 있으면 하나는 거짓이다.
                            raise TransitionAbort("APPROVAL_MISMATCH", EXIT_MISMATCH)
                        # 실제로 바꿀 때만 base 9를 `ACCESS EXCLUSIVE`로 올린다.
                        transition.escalate_base_locks(connection, database=database)
                        clock.apply(connection, now=time.monotonic())
                        handler(connection, database, current)
                        after = _read_locked(connection, database, profile, clock)
                        transition.assert_target_invariants_held(current, after)
                    transition.classify_post_state(after)
                    # lock을 쥔 총 시간이 예산을 넘으면 commit하지 않는다.
                    transition.assert_within_budget(started, now=time.monotonic())

            # 성공한 target의 새 상태를 다음 iteration 기준선으로 교체한다.
            fingerprints[database] = transition.target_fingerprint(after)

            # **target별 marker-last.** 그 target의 commit·검증이 끝난 직후에 쓴다.
            # 셋을 모아 뒀다 마지막에 쓰면 둘째 target에서 실패했을 때
            # `[final, legacy, legacy] + marker 0`이 남아 재개가 불가능하다
            # (구현리뷰 8차 필수 1).
            if existing is None:
                # marker의 entry는 **항상 전환 전** 값이다. 복구 경로에서 현재
                # inventory를 쓰면 `state`가 `FINAL_ADOPTED`로 기록돼, 다음 실행이
                # 그 marker로 bundle을 다시 만들 때 approval과 어긋난다
                # (구현리뷰 9차 필수 1).
                pre_entry = (
                    reconstruct_preflight_entry(approval, database)
                    if recovered
                    else preflight_entry(current)
                )
                _write_marker(
                    marker_path,
                    transition.build_marker(
                        after,
                        state=transition.BaseState.FINAL_ADOPTED,
                        change_ref=change_ref,
                        preflight_bundle_sha256=bundle,
                        archive_sha256=evidence[database]["archive"],
                        view_sidecar_sha256=evidence[database]["sidecar"],
                        recorded_at=_now(),
                        preflight_target_entry=pre_entry,
                        backup_root_trust=root_trust,
                        approval_sha256=approval_sha256,
                    ),
                    backup_root,
                )

            others = [d for d in transition.ORDERED_TARGETS if d != database]
            observed = collect_inventories(book, inspector, others)
            transition.assert_other_targets_unchanged(
                {d: fingerprints[d] for d in others},
                {d: transition.target_fingerprint(v) for d, v in observed.items()},
            )
    except BaseException:
        book.close()
        raise
    return EXIT_OK


@contextlib.contextmanager
def _target_session(mutator: SessionFactory, database: str) -> Iterator[Any]:
    """mutex 생명주기를 **CLI가 직접 소유한다**.

    factory가 알아서 잡았을 것이라고 가정하면 배선 누락을 아무도 잡지 못한다. 실제로
    `_held_mutex()`가 정의만 되고 호출이 0건이었다(구현리뷰 8차 필수 2).

    순서는 `mutex 획득 → transaction → table lock → 처리 → commit/rollback → mutex 해제`
    이고, 어떤 종료 경로에서도 lock이 남지 않는다.

    **cleanup guard는 획득 직후부터 켠다.** 획득 뒤 첫 `commit()`이 실패하면 lock은 이미
    잡혀 있는데 `finally`에 들어가지 못한다. session advisory lock은 rollback으로
    사라지지 않고 connection은 pool로 돌아갈 수 있어 mutex가 남는다(9차 필수 2).
    """

    with mutator(database) as connection:
        transition.acquire_target_mutex(connection, database=database)
        try:
            # mutex를 잡은 암묵 transaction을 닫아야 다음 transaction의 snapshot이
            # table lock 뒤에 고정된다. 이 commit도 guard 안에 있다.
            connection.commit()
            yield connection
        finally:
            transition.release_target_mutex(connection, database=database)


def _write_marker(path: Path, payload: Mapping[str, Any], backup_root: Path) -> None:
    """검증된 marker 하나를 원자적으로 기록한다."""

    import postgres_backup

    transition.validate_marker_schema(payload)
    postgres_backup.atomic_write_json(path, payload, trusted_root=backup_root)


def _read_locked(
    connection: Any,
    database: str,
    profile: str,
    clock: transition.BudgetClock | None = None,
) -> transition.TargetInventory:
    """lock을 잡은 transaction 안에서 한 snapshot으로 수집한다."""

    return transition.read_inventory(
        connection,
        database=database,
        profile=profile,
        require_snapshot=True,
        clock=clock,
    )


def _emit(reason_code: str, exit_code: int) -> int:
    sanitized = (
        reason_code if reason_code in wrapper.REASON_ALLOWLIST else ("INTERNAL_ERROR")
    )
    print(
        json.dumps({"reason_code": sanitized, "status": "FAILED"}, sort_keys=True),
        file=sys.stderr,
    )
    return exit_code


def resolve_sessions(
    argv: Sequence[str] | None,
    *,
    registry: Mapping[str, Any] | None = None,
    builder: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """실행에 필요한 factory를 고른다. **연결은 아직 열지 않는다.**

    지금까지 `PUBLIC_SESSIONS`가 비어 있으면 argv를 보기도 전에 `MODE_NOT_WIRED`로
    끝났다. 그래서 격리 환경에서도 production 경로를 돌려볼 수 없었다
    (구현리뷰 10차 필수 4).

    registry가 이미 채워져 있으면 그것을 쓰고, 아니면 `builder`로 만든다. builder는
    factory만 돌려주며 자격증명이 없으면 **첫 호출에서** `APPROVAL_REQUIRED`로 멈춘다.
    """

    source = PUBLIC_SESSIONS if registry is None else registry
    if source:
        return dict(source)
    if builder is None:
        return {}
    return dict(builder())


#: 최종 ZIP 경로. 없으면 handler를 배선하지 않고 apply는 `MODE_NOT_WIRED`다.
ARCHIVE_ENV_KEY = "POSTGRES_TRANSITION_ARCHIVE"


def _default_builder() -> Mapping[str, Any]:
    """운영 기본 배선. preflight는 자격증명만으로, apply는 최종 ZIP까지 있어야 한다."""

    import os

    import transition_sessions

    archive = os.environ.get(ARCHIVE_ENV_KEY, "").strip()
    return transition_sessions.build_public_wiring(Path(archive) if archive else None)


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: Mapping[str, Any] | None = None,
    builder: Callable[[], Mapping[str, Any]] | None = _default_builder,
) -> int:
    """운영 진입점.

    artifact·confirm 검증은 `_run()`이 연결 **전에** 끝낸다. handler가 없으면 apply는
    `MODE_NOT_WIRED`로 멈추지만 preflight는 read-only factory만으로 돈다.
    """

    try:
        sessions = resolve_sessions(argv, registry=registry, builder=builder)
        inspector = sessions.get("read_only")
        if inspector is None:
            raise TransitionAbort("MODE_NOT_WIRED", EXIT_USAGE)
        return _run(
            argv,
            inspector=inspector,
            mutator=sessions.get("mutating"),
            handler=sessions.get("handler"),
        )
    except TransitionAbort as exc:
        return _emit(exc.reason_code, exc.exit_code)
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception:
        return _emit("INTERNAL_ERROR", EXIT_USAGE)


if __name__ == "__main__":
    sys.exit(main())
