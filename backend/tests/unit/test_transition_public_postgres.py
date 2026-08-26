"""`V5-CM-2.6` 전환 CLI 단위 테스트.

공용 DB에 붙지 않는다. session factory를 주입하고 `ConnectionLedger`로 **누가 몇 번
연결했는지** 센다. 이 Task에서 가장 중요한 안전 속성이 "승인 전에는 연결하지 않는다"와
"실패 후에는 새 연결이 없다"이기 때문이다.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
UNIT_ROOT = Path(__file__).resolve().parent
for _root in (SCRIPTS_ROOT, UNIT_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import postgres_transition as transition  # noqa: E402
import transition_public_postgres as cli  # noqa: E402

# Gate 0 형상 builder는 상태 판정 테스트와 하나만 둔다. 두 벌이 되면 한쪽만 고쳐진다.
from test_postgres_transition import (  # noqa: E402
    FINAL_ALARMS,
    _final,
    _inventory,
)


@pytest.fixture(autouse=True)
def _pin_base_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """축소 fixture 전용. 상태 판정 테스트와 같은 이유다."""

    def pinned(inventory: transition.TargetInventory) -> str:
        wafer = {
            inventory.column_types.get(name, {}).get("wafer")
            for name in transition.WAFER_ALTER_TABLES
        }
        return (
            transition.FINAL_BASE_CATALOG_SHA256
            if wafer == {transition.FINAL_WAFER_TYPE}
            else transition.LEGACY_BASE_CATALOG_SHA256
        )

    monkeypatch.setattr(transition, "base_catalog_sha256", pinned)


@pytest.fixture(autouse=True)
def _trusted_backup_root(tmp_path: Path) -> None:
    """production backup root는 실행 계정 단독 소유의 `0700`이다(계획 §16.1).

    pytest 기본 mode는 그보다 넓어서, 이걸 맞추지 않으면 모든 회귀가
    `BACKUP_ROOT_UNTRUSTED`로 끝난다. 신뢰 조건 자체는 전용 회귀가 확인한다.
    """

    tmp_path.chmod(0o700)


def _inspector(seen: list[str]) -> cli.SessionFactory:
    @contextlib.contextmanager
    def factory(database: str) -> Iterator[Any]:
        seen.append(database)
        yield _FakeConnection()

    return factory


class _StatefulTargets:
    """실제로 `legacy → final`로 상태가 바뀌는 fixture.

    handler가 이름만 모으고 `read_inventory()`가 늘 같은 legacy를 돌려주면 순차 적용
    기준선이 갱신되지 않는 결함을 통과시켜 버린다(구현리뷰 4차 필수 1).
    """

    def __init__(self) -> None:
        self.final: set[str] = set()
        self.handled: list[str] = []
        self.seen_states: list[str] = []
        # handler가 전환과 함께 저지른 부수 변경. post-state 읽기에 반영된다.
        self.drift: dict[str, Any] = {}

    def read(
        self,
        _connection: Any,
        *,
        database: str,
        profile: str,
        with_content: bool = True,
        require_snapshot: bool = False,
        clock: Any = None,
    ) -> transition.TargetInventory:
        if database not in self.final:
            return _inventory(database)
        inventory = _final(database)
        mutate = self.drift.get(database)
        return mutate(inventory) if mutate is not None else inventory

    def handler(self, _connection: Any, database: str, inventory: Any) -> None:
        # handler는 preflight의 stale inventory가 아니라 방금 읽은 것을 받아야 한다.
        self.seen_states.append(inventory.column_types["evaluation"]["wafer"])
        self.handled.append(database)
        self.final.add(database)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transition, "read_inventory", self.read)


def _stub_inventories(monkeypatch: pytest.MonkeyPatch) -> _StatefulTargets:
    """`read_inventory`를 Gate 0 형상 stub으로 바꾼다. SQL을 보내지 않는다."""

    state = _StatefulTargets()
    state.install(monkeypatch)
    return state


def _approval(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_type": transition.APPROVAL_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "change_ref": "GH-104",
        "status": "APPROVED",
        "ordered_targets": list(transition.ORDERED_TARGETS),
        "preflight_bundle_sha256": "0" * 64,
        "source_manifest_sha256": "1" * 64,
        "gate0_inventory_sha256": "2" * 64,
        "server_major_by_target": dict.fromkeys(transition.ORDERED_TARGETS, 16),
        "planned_outcome_by_target": dict.fromkeys(
            transition.ORDERED_TARGETS, "BASE_LEGACY_EPOCH"
        ),
        "preserved_projection_sha256_by_target": dict.fromkeys(
            transition.ORDERED_TARGETS, "3" * 64
        ),
        "external_fk_projection_sha256_by_target": dict.fromkeys(
            transition.ORDERED_TARGETS, "6" * 64
        ),
        "target_fingerprint_sha256_by_target": dict.fromkeys(
            transition.ORDERED_TARGETS, "7" * 64
        ),
        "compatibility_view_sha256": "4" * 64,
        "compatibility_view_owner_acl_sha256": "5" * 64,
        "execution_privilege": transition.EXECUTION_PRIVILEGE,
        "owner_match": True,
        "approved_at": "2026-08-21T12:00:00+09:00",
    }
    payload.update(overrides)
    return payload


def _write(tmp_path: Path, payload: Mapping[str, Any], name: str = "approval") -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _receipt(database: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_type": transition.RECEIPT_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": database,
        "profile": transition.TARGET_PROFILE[database],
        "change_ref": "GH-104",
        "preflight_bundle_sha256": "0" * 64,
        "table_allowlist": list(transition.BASE_TABLES),
        "archive_sha256": "a" * 64,
        "client_major": 16,
        "server_major": 16,
        "restore_verified": True,
        "target_fingerprint_sha256": "7" * 64,
        "compatibility_view_sha256": transition.COMPAT_VIEW_SHA256,
        "compatibility_view_owner_acl_sha256": (
            transition.compatibility_view_owner_acl_sha256()
        ),
        "backup_image_digest": transition_backup_image(16),
        "backup_tool_version": transition_backup_version(16),
        "view_sidecar_name": transition.view_sidecar_name(database, "GH-104"),
        "view_sidecar_sha256": "b" * 64,
        "execution_role": transition.LEGACY_VIEW_OWNER,
        "execution_is_superuser": True,
        "execution_owner_match": True,
    }
    payload.update(overrides)
    return payload


def _make_archives(
    root: Path, change_ref: str = "GH-104", bundle: str = "0" * 64
) -> dict[str, str]:
    """실물 archive를 만들고 target별 digest를 돌려준다.

    receipt의 `archive_sha256`를 형식만 보면 임의 64hex가 통과한다. 실물과 대조해야
    한다(구현리뷰 2차 필수 1-2).
    """

    import postgres_backup

    digests: dict[str, str] = {}
    for database in transition.ORDERED_TARGETS:
        path = root / transition.archive_name(database, change_ref)
        path.write_bytes(f"dump-of-{database}".encode())
        digests[database] = postgres_backup.archive_digest(path)
        sidecar = root / transition.view_sidecar_name(database, change_ref)
        sidecar.write_text(
            json.dumps(_sidecar(database, change_ref, preflight_bundle_sha256=bundle)),
            encoding="utf-8",
        )
        _complete(root, database, change_ref)
    return digests


def _complete(root: Path, database: str, change_ref: str = "GH-104") -> None:
    """producer가 남기는 receipt + completion marker를 fixture로 만든다.

    소비자가 marker를 요구하므로 fixture도 실제와 같은 set을 갖춰야 한다
    (구현리뷰 13차 필수 2).
    """

    import backup_orchestrator
    import postgres_backup

    receipt_path = root / backup_orchestrator.receipt_name(database, change_ref)
    if not receipt_path.exists():
        receipt_path.write_text(json.dumps({"placeholder": database}), encoding="utf-8")
    payload = {
        "artifact_type": backup_orchestrator.COMPLETION_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": database,
        "change_ref": change_ref,
        "archive_sha256": postgres_backup.archive_digest(
            root / transition.archive_name(database, change_ref)
        ),
        "view_sidecar_sha256": postgres_backup.archive_digest(
            root / transition.view_sidecar_name(database, change_ref)
        ),
        "receipt_sha256": postgres_backup.archive_digest(receipt_path),
    }
    (root / backup_orchestrator.completion_name(database, change_ref)).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _sidecar(
    database: str,
    change_ref: str = "GH-104",
    *,
    final: bool = False,
    **overrides: Any,
) -> dict[str, Any]:
    state = (
        transition.BaseState.FINAL_ADOPTED
        if final
        else transition.BaseState.BASE_LEGACY_EPOCH
    )
    payload: dict[str, Any] = {
        "artifact_type": transition.SIDECAR_ARTIFACT_TYPE,
        "dataset_epoch": transition.DATASET_EPOCH,
        "database": database,
        "profile": transition.TARGET_PROFILE[database],
        "change_ref": change_ref,
        "preflight_bundle_sha256": "0" * 64,
        "view_name": transition.LEGACY_VIEW,
        "base_state": state.value,
        "view_definition_sha256": transition.SIDECAR_VIEW_BY_STATE[state.value],
        "view_owner": transition.LEGACY_VIEW_OWNER,
        "view_acl": transition.LEGACY_VIEW_ACL,
        "view_comment": transition.LEGACY_VIEW_COMMENT,
    }
    payload.update(overrides)
    return payload


def _sidecar_digests(root: Path, change_ref: str = "GH-104") -> dict[str, str]:
    import postgres_backup

    return {
        database: postgres_backup.archive_digest(
            root / transition.view_sidecar_name(database, change_ref)
        )
        for database in transition.ORDERED_TARGETS
    }


def transition_backup_image(major: int) -> str:
    import postgres_backup

    return postgres_backup.expected_client_image(major)


def transition_backup_version(major: int) -> str:
    import postgres_backup

    return postgres_backup.expected_client_version(major)


def _receipt_args(
    tmp_path: Path,
    bundle: str = "0" * 64,
    digests: Mapping[str, str] | None = None,
    fingerprints: Mapping[str, str] | None = None,
    sidecars: Mapping[str, str] | None = None,
) -> list[str]:
    argv: list[str] = []
    for database in transition.ORDERED_TARGETS:
        overrides: dict[str, Any] = {"preflight_bundle_sha256": bundle}
        if sidecars is not None:
            overrides["view_sidecar_sha256"] = sidecars[database]
        if digests is not None:
            overrides["archive_sha256"] = digests[database]
        if fingerprints is not None:
            overrides["target_fingerprint_sha256"] = fingerprints[database]
        path = _write(
            tmp_path,
            _receipt(database, **overrides),
            name=f"receipt_{database}",
        )
        argv += ["--receipt", str(path)]
    return argv


# ---------------------------------------------------------------------------
# preflight — read-only만
# ---------------------------------------------------------------------------


def test_preflight_reads_three_targets_in_order_without_mutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_inventories(monkeypatch)
    seen: list[str] = []
    ledger = cli.ConnectionLedger()
    assert cli._run(["--preflight"], inspector=_inspector(seen), ledger=ledger) == 0
    assert seen == list(transition.ORDERED_TARGETS)
    assert ledger.read_only == list(transition.ORDERED_TARGETS)
    assert ledger.mutating == []


def test_preflight_report_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_inventories(monkeypatch)
    seen: list[str] = []
    ledger = cli.ConnectionLedger()
    inventories = cli.collect_inventories(ledger, _inspector(seen))
    report = cli.preflight_report(inventories)
    rendered = json.dumps(report)
    for forbidden in ("password", "5432", "localhost", "://", "/Users"):
        assert forbidden not in rendered
    assert set(report["targets"]) == set(transition.ORDERED_TARGETS)
    assert report["targets"]["kosa_text2sql"]["action_history_rows"] == 48
    assert all(
        entry["state"] == "BASE_LEGACY_EPOCH" for entry in report["targets"].values()
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--preflight", "--approval", "x.json"],
        ["--preflight", "--backup-root", "/tmp/x"],
        ["--preflight", "--change-ref", "GH-104"],
        ["--preflight", "--confirm-target", "kosa_agent"],
    ],
)
def test_preflight_rejects_mutation_arguments(argv: list[str]) -> None:
    seen: list[str] = []
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(argv, inspector=_inspector(seen))
    assert caught.value.reason_code == "ARG_INVALID"
    assert seen == []


@pytest.mark.parametrize("argv", [[], ["--preflight", "--apply"]])
def test_mode_must_be_exactly_one(argv: list[str]) -> None:
    seen: list[str] = []
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(argv, inspector=_inspector(seen))
    assert caught.value.reason_code == "MODE_CONFLICT"
    assert seen == []


# ---------------------------------------------------------------------------
# apply — 승인 전에는 연결 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["--apply"], "APPROVAL_REQUIRED"),
        (["--apply", "--approval", "a.json"], "APPROVAL_REQUIRED"),
        (
            ["--apply", "--approval", "a.json", "--backup-root", "/outside/backups"],
            "BACKUP_REQUIRED",
        ),
    ],
)
def test_apply_missing_arguments_never_connects(argv: list[str], reason: str) -> None:
    """인자가 모자라면 **연결 전에** 멈춘다. 연결 자체가 증적이다."""

    seen: list[str] = []
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(argv, inspector=_inspector(seen), ledger=ledger)
    assert caught.value.reason_code == reason
    assert seen == []
    assert ledger.read_only == [] and ledger.mutating == []


def test_apply_with_repo_internal_backup_root_never_connects(tmp_path: Path) -> None:
    approval = _write(tmp_path, _approval())
    receipts = _receipt_args(tmp_path)
    seen: list[str] = []
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            [
                "--apply",
                "--approval",
                str(approval),
                "--backup-root",
                str(SCRIPTS_ROOT.parents[1] / "backups"),
                *receipts,
                "--change-ref",
                "GH-104",
                "--confirm-target",
                "kosa_agent_e2e",
                "--confirm-target",
                "kosa_agent",
                "--confirm-target",
                "kosa_text2sql",
            ],
            inspector=_inspector(seen),
            ledger=ledger,
        )
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert seen == []
    assert ledger.mutating == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("status"),
        lambda p: p.update(status="PENDING"),
        lambda p: p.update(artifact_type="other"),
        lambda p: p.update(change_ref="CM-21"),
        lambda p: p.update(ordered_targets=["kosa_agent", "kosa_agent_e2e"]),
        lambda p: p.update(preflight_bundle_sha256="ABC"),
        lambda p: p.update(planned_outcome_by_target={"kosa_agent": "BASE_FRESH"}),
        lambda p: p.update(extra_key=1),
        lambda p: p.update(execution_privilege="USER"),
        lambda p: p.update(owner_match=False),
        lambda p: p.pop("external_fk_projection_sha256_by_target"),
    ],
)
def test_malformed_approval_never_connects(tmp_path: Path, mutate: Any) -> None:
    payload = _approval()
    mutate(payload)
    approval = _write(tmp_path, payload)
    receipts = _receipt_args(tmp_path)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    seen: list[str] = []
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort):
        cli._run(
            [
                "--apply",
                "--approval",
                str(approval),
                "--backup-root",
                str(root),
                *receipts,
                "--change-ref",
                "GH-104",
                "--confirm-target",
                "kosa_agent_e2e",
                "--confirm-target",
                "kosa_agent",
                "--confirm-target",
                "kosa_text2sql",
            ],
            inspector=_inspector(seen),
            ledger=ledger,
        )
    assert seen == []
    assert ledger.mutating == []


def _target_fingerprints() -> dict[str, str]:
    return {
        database: transition.target_fingerprint(_inventory(database))
        for database in transition.ORDERED_TARGETS
    }


def _valid_approval(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """현재 inventory에서 독립 산출한 값으로 채운 approval.

    이제 CLI가 approval 자신의 값을 기대값으로 쓰지 않으므로, 통과하는 approval을
    만들려면 같은 산출을 거쳐야 한다(구현리뷰 1차 필수 3).
    """

    _stub_inventories(monkeypatch)
    inventories = cli.collect_inventories(cli.ConnectionLedger(), _inspector([]))
    report = cli.preflight_report(inventories)
    expected = cli.expected_approval(report, inventories, "GH-104")
    payload = _approval()
    for key in transition.APPROVAL_EXPECTED_KEYS:
        payload[key] = expected[key]
    return payload


def test_apply_stops_at_mode_not_wired_without_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """묶음 1에서는 handler가 없으므로 검증을 다 통과해도 쓰지 않는다."""

    payload = _valid_approval(monkeypatch)
    approval = _write(tmp_path, payload)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    digests = _make_archives(root, bundle=payload["preflight_bundle_sha256"])
    receipts = _receipt_args(
        tmp_path,
        payload["preflight_bundle_sha256"],
        digests,
        _target_fingerprints(),
        _sidecar_digests(root),
    )
    ledger = cli.ConnectionLedger()

    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            [
                "--apply",
                "--approval",
                str(approval),
                "--backup-root",
                str(root),
                *receipts,
                "--change-ref",
                "GH-104",
                *[
                    a
                    for t in transition.ORDERED_TARGETS
                    for a in ("--confirm-target", t)
                ],
            ],
            inspector=_inspector([]),
            ledger=ledger,
        )
    assert caught.value.reason_code == "MODE_NOT_WIRED"
    assert ledger.read_only == list(transition.ORDERED_TARGETS)
    assert ledger.mutating == []


def _drifted(key: str, original: Any) -> Any:
    """구조는 유효하되 값만 다르게 만든다. 구조를 깨면 다른 reason이 나온다."""

    if key == "planned_outcome_by_target":
        # 유효한 enum 값이되 현재 상태(BASE_LEGACY_EPOCH)와 다른 값이어야 한다.
        return dict.fromkeys(original, transition.BaseState.FINAL_ADOPTED.value)
    if key == "server_major_by_target":
        return dict.fromkeys(
            original, 16 if next(iter(original.values())) != 16 else 15
        )
    if isinstance(original, dict):
        return dict.fromkeys(original, "9" * 64)
    if isinstance(original, str) and len(original) == 64:
        return "9" * 64
    return "GH-999"


@pytest.mark.parametrize(
    "key",
    sorted(transition.APPROVAL_EXPECTED_KEYS),
)
def test_approval_value_drift_stops_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """대조 대상 10키 중 무엇이 달라도 mutation 단계로 가지 않는다.

    이전 구현은 approval 자신의 값을 기대값으로 넘겨 임의 hex가 통과했다.
    """

    payload = _valid_approval(monkeypatch)
    payload[key] = _drifted(key, payload[key])
    approval = _write(tmp_path, payload)
    receipts = _receipt_args(tmp_path, payload["preflight_bundle_sha256"])
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            [
                "--apply",
                "--approval",
                str(approval),
                "--backup-root",
                str(root),
                *receipts,
                "--change-ref",
                "GH-104",
                *[
                    a
                    for t in transition.ORDERED_TARGETS
                    for a in ("--confirm-target", t)
                ],
            ],
            inspector=_inspector([]),
            ledger=ledger,
        )
    assert caught.value.reason_code in {"APPROVAL_MISMATCH", "ARG_INVALID"}
    assert ledger.mutating == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda r: r.update(database="kosa"), "ARTIFACT_INVALID"),
        (lambda r: r.update(profile="evaluation"), "ARTIFACT_INVALID"),
        (lambda r: r.update(change_ref="GH-999"), "BACKUP_INVALID"),
        (lambda r: r.update(preflight_bundle_sha256="9" * 64), "BACKUP_INVALID"),
        (lambda r: r.update(client_major=15), "BACKUP_CLIENT_VERSION_MISMATCH"),
        (lambda r: r.update(server_major=17), "BACKUP_CLIENT_VERSION_MISMATCH"),
        (lambda r: r.update(restore_verified=False), "RESTORE_NOT_VERIFIED"),
        (lambda r: r.update(table_allowlist=["action_history"]), "ARTIFACT_INVALID"),
        (lambda r: r.pop("archive_sha256"), "ARTIFACT_INVALID"),
        (lambda r: r.update(archive_sha256="9" * 64), "BACKUP_INVALID"),
        (lambda r: r.update(target_fingerprint_sha256="9" * 64), "BACKUP_INVALID"),
        (lambda r: r.update(execution_role="postgres"), "BACKUP_INVALID"),
        (lambda r: r.update(execution_is_superuser=False), "ARTIFACT_INVALID"),
        (lambda r: r.update(execution_owner_match=False), "ARTIFACT_INVALID"),
        (
            lambda r: r.update(compatibility_view_sha256=transition.LEGACY_VIEW_SHA256),
            "BACKUP_INVALID",
        ),
        (
            lambda r: r.update(compatibility_view_owner_acl_sha256="9" * 64),
            "BACKUP_INVALID",
        ),
        (lambda r: r.update(backup_image_digest="postgres:16"), "ARTIFACT_INVALID"),
        (lambda r: r.update(backup_tool_version=""), "ARTIFACT_INVALID"),
    ],
)
def test_bad_receipt_stops_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any, reason: str
) -> None:
    """**유효한** receipt 세트에서 한 필드만 바꿔 그 필드의 방어를 격리한다.

    receipt 전체가 애초에 틀려 있으면 어떤 검사를 지워도 다른 이유로 실패해
    테스트가 아무것도 검증하지 못한다.
    """

    payload = _valid_approval(monkeypatch)
    approval = _write(tmp_path, payload)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    digests = _make_archives(root)
    fingerprints = _target_fingerprints()
    bundle = payload["preflight_bundle_sha256"]
    argv: list[str] = []
    for index, database in enumerate(transition.ORDERED_TARGETS):
        receipt = _receipt(
            database,
            preflight_bundle_sha256=bundle,
            archive_sha256=digests[database],
            target_fingerprint_sha256=fingerprints[database],
        )
        if index == 0:
            mutate(receipt)
        argv += [
            "--receipt",
            str(_write(tmp_path, receipt, name=f"receipt_{database}")),
        ]
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            [
                "--apply",
                "--approval",
                str(approval),
                "--backup-root",
                str(root),
                *argv,
                "--change-ref",
                "GH-104",
                *[
                    a
                    for t in transition.ORDERED_TARGETS
                    for a in ("--confirm-target", t)
                ],
            ],
            inspector=_inspector([]),
            ledger=ledger,
        )
    assert caught.value.reason_code == reason
    assert ledger.mutating == []


# ---------------------------------------------------------------------------
# production 경계
# ---------------------------------------------------------------------------


def test_public_sessions_registry_is_empty() -> None:
    """묶음 1에서는 공용 session factory를 배선하지 않는다."""

    assert cli.PUBLIC_SESSIONS == {}


def test_main_without_wiring_emits_allowlisted_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """factory를 만들 수 없으면 `MODE_NOT_WIRED`다.

    자격증명이 있는 환경에서는 `main()`이 read-only factory를 만들어 preflight를
    시도하므로, 배선 부재만 보려면 builder를 비워 준다(구현리뷰 10차 필수 4).
    """

    assert cli.main(["--preflight"], builder=lambda: {}) == cli.EXIT_USAGE
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload == {"reason_code": "MODE_NOT_WIRED", "status": "FAILED"}


def test_main_reports_missing_credentials_with_a_typed_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """자격증명 누락이 `INTERNAL_ERROR`로 뭉개지면 원인을 알 수 없다."""

    import transition_sessions

    assert (
        cli.main(
            ["--preflight"],
            builder=lambda: transition_sessions.build_public_sessions(environ={}),
        )
        == cli.EXIT_CONFIRM_REQUIRED
    )
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload == {"reason_code": "APPROVAL_REQUIRED", "status": "FAILED"}


def test_parser_cannot_choose_or_reorder_targets() -> None:
    options = {
        action.option_strings[0]
        for action in cli._parser()._actions
        if action.option_strings
    }
    assert options == {
        "-h",
        "--preflight",
        "--apply",
        "--closure",
        "--approval",
        "--backup-root",
        "--change-ref",
        "--confirm-target",
        "--receipt",
    }
    assert "--target" not in options
    assert "--order" not in options


# ---------------------------------------------------------------------------
# _apply_targets — 구현리뷰 1차 필수 1·2
# ---------------------------------------------------------------------------


def _seed_markers(
    root: Path, databases: Sequence[str], inventories: Mapping[str, Any]
) -> None:
    """이미 전환된 target의 COMMITTED marker를 미리 둔다.

    final target은 marker를 새로 쓰지 않고 **읽어서 검증**한다(구현리뷰 7차 필수 2).
    """

    for database in databases:
        payload = transition.build_marker(
            inventories[database],
            state=transition.BaseState.FINAL_ADOPTED,
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
            archive_sha256="a" * 64,
            view_sidecar_sha256="b" * 64,
            recorded_at="2026-08-22T12:00:00+09:00",
            backup_root_trust="0700",
            approval_sha256="a" * 64,
            preflight_target_entry=cli.preflight_entry(_inventory(database)),
        )
        (root / transition.marker_name(database)).write_text(
            json.dumps(payload), encoding="utf-8"
        )


def _evidence() -> dict[str, dict[str, str]]:
    return {
        database: {"archive": "a" * 64, "sidecar": "b" * 64}
        for database in transition.ORDERED_TARGETS
    }


def _args(
    confirmed: tuple[str, ...] = transition.ORDERED_TARGETS,
    backup_root: Path | None = None,
) -> Any:
    root = backup_root or Path(tempfile.mkdtemp(prefix="v5cm26-"))
    parsed = cli._parser().parse_args(
        [
            "--apply",
            "--backup-root",
            str(root),
            "--change-ref",
            "GH-104",
            *[a for t in confirmed for a in ("--confirm-target", t)],
        ]
    )
    return parsed


class _FakeConnection:
    """lock·설정 query에 답하면서 보낸 SQL을 순서대로 기록한다.

    transaction 경계(`begin`/`commit`)와 `invalidate()`도 기록한다. CLI가 mutex
    생명주기를 직접 소유하는지 보려면 그 경계가 로그에 남아야 한다.
    """

    def __init__(self, log: list[str] | None = None) -> None:
        self.log: list[str] = [] if log is None else log
        self.invalidated = False

    def commit(self) -> None:
        self.log.append("COMMIT")

    def invalidate(self) -> None:
        self.invalidated = True
        self.log.append("INVALIDATE")

    @contextlib.contextmanager
    def begin(self) -> Iterator[None]:
        self.log.append("BEGIN")
        try:
            yield
        except BaseException:
            self.log.append("ROLLBACK")
            raise
        self.log.append("COMMIT")

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        self.log.append(sql)
        return _FakeResult(_answer(sql))


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> Any:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


TIMEOUT_VALUES = {
    "lock_timeout": "5s",
    "statement_timeout": "600s",
    "idle_in_transaction_session_timeout": "60s",
}


def _answer(sql: str) -> list[dict[str, Any]]:
    if sql == transition.ISOLATION_SQL:
        return [{"transaction_isolation": "repeatable read"}]
    if sql.startswith("SELECT pg_current_xact_id_if_assigned()"):
        return [{"assigned": True, "started": True, "pid": 1}]
    if sql.startswith("SELECT count(*) AS held FROM pg_locks"):
        return [{"held": 1}]
    if "pg_advisory_unlock" in sql:
        return [{"released": True}]
    for name, statement in transition.SHOW_TIMEOUT_SQL.items():
        if sql == statement:
            return [{name: TIMEOUT_VALUES[name]}]
    return []


def _mutator(seen: list[str], log: list[str] | None = None) -> cli.SessionFactory:
    @contextlib.contextmanager
    def factory(database: str) -> Iterator[Any]:
        seen.append(database)
        yield _FakeConnection(log)

    return factory


def _run_apply(
    handler: Any,
    ledger: cli.ConnectionLedger,
    monkeypatch: pytest.MonkeyPatch,
    state: _StatefulTargets | None = None,
) -> tuple[list[str], list[str]]:
    state = state or _stub_inventories(monkeypatch)
    if handler is not None:
        original = state.handler

        def wrapped(connection: Any, database: str, inventory: Any) -> None:
            original(connection, database, inventory)
            handler(connection, database, inventory)

        chosen: Any = wrapped
    else:
        chosen = state.handler
    read_seen: list[str] = []
    write_seen: list[str] = []
    inventories = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    approval = _approval(target_fingerprint_sha256_by_target=_target_fingerprints())
    cli._apply_targets(
        _args(),
        approval,
        inventories,
        ledger,
        _inspector(read_seen),
        _mutator(write_seen),
        chosen,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    return read_seen, write_seen


def test_apply_targets_completes_all_three_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """단일 confirmation으로는 완주할 수 없었다. 세 개를 순서대로 처리해야 한다."""

    state = _stub_inventories(monkeypatch)
    ledger = cli.ConnectionLedger()
    _read, written = _run_apply(None, ledger, monkeypatch, state)
    assert state.handled == list(transition.ORDERED_TARGETS)
    assert written == list(transition.ORDERED_TARGETS)
    assert ledger.mutating == list(transition.ORDERED_TARGETS)
    # 세 target 모두 실제로 final이 됐다.
    assert state.final == set(transition.ORDERED_TARGETS)
    # handler는 늘 legacy 상태를 보고 들어간다 — 이미 final인 걸 다시 바꾸지 않는다.
    assert state.seen_states == [transition.LEGACY_WAFER_TYPE] * 3
    # 자기 재확인은 mutating connection 안에서 하므로 read-only는 나머지 2개씩이다.
    assert len(ledger.read_only) == 2 * len(transition.ORDERED_TARGETS)
    assert ledger.closed is False


@pytest.mark.parametrize(
    "confirmed",
    [
        (),
        ("kosa_agent_e2e",),
        ("kosa_agent", "kosa_agent_e2e", "kosa_text2sql"),
        ("kosa_agent_e2e", "kosa_agent"),
        (*transition.ORDERED_TARGETS, "kosa_agent"),
    ],
)
def test_confirmation_must_be_all_three_in_order(confirmed: tuple[str, ...]) -> None:
    """부분집합·순서 변경·중복을 전부 거부한다."""

    seen: list[str] = []
    ledger = cli.ConnectionLedger()
    # `--change-ref`가 없으면 confirm 검사에 도달하기 전에 ARG_INVALID로 끝나
    # 이 테스트가 아무것도 검증하지 못한다.
    argv = [
        "--apply",
        "--approval",
        "a.json",
        "--backup-root",
        "/outside/b",
        "--change-ref",
        "GH-104",
        *[a for _ in transition.ORDERED_TARGETS for a in ("--receipt", "r.json")],
    ]
    for target in confirmed:
        argv += ["--confirm-target", target]
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(argv, inspector=_inspector(seen), ledger=ledger)
    assert caught.value.reason_code == "CONFIRM_REQUIRED"
    assert seen == []
    assert ledger.mutating == []


@pytest.mark.parametrize(
    "error",
    [
        cli.TransitionAbort("MODE_CONTRACT_ERROR", 1),
        transition.TransitionError("TARGET_STATE_UNSUPPORTED", 1),
        RuntimeError("예상 밖"),
    ],
)
def test_any_failure_closes_the_ledger(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """typed·raw 어떤 실패든 뒤 connection이 0이어야 한다.

    `TransitionAbort`만 잡으면 `TransitionError`·raw 예외에서 fail-stop이 성립하지
    않는다(구현리뷰 1차 필수 2).
    """

    def boom(_c: Any, database: str, _i: Any) -> None:
        if database == "kosa_agent_e2e":
            raise error

    ledger = cli.ConnectionLedger()
    with pytest.raises(type(error)):
        _run_apply(boom, ledger, monkeypatch)
    assert ledger.closed is True
    assert ledger.mutating == ["kosa_agent_e2e"]
    with pytest.raises(cli.TransitionAbort):
        ledger.open_read_only("kosa_agent")
    with pytest.raises(cli.TransitionAbort):
        ledger.open_mutating("kosa_agent")


def test_second_target_failure_leaves_third_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """앞 target 성공 뒤 실패해도 세 번째 mutation은 0건이다."""

    def boom(_c: Any, database: str, _i: Any) -> None:
        if database == "kosa_agent":
            raise transition.TransitionError("TARGET_STATE_UNSUPPORTED", 1)

    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError):
        _run_apply(boom, ledger, monkeypatch)
    assert ledger.mutating == ["kosa_agent_e2e", "kosa_agent"]
    assert "kosa_text2sql" not in ledger.mutating
    assert ledger.closed is True


def test_other_target_drift_stops_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비대상 DB에 모르는 table이 생기면 그 자리에서 멈춘다.

    fingerprint가 relation 집합을 담지 않으면 이 drift가 값을 바꾸지 않아 통과한다
    (구현리뷰 4차 필수 2).
    """

    second = transition.ORDERED_TARGETS[1]
    state = _stub_inventories(monkeypatch)
    base_read = state.read

    def drifting(connection: Any, *, database: str, **kwargs: Any) -> Any:
        if database == second and database not in state.final:
            return _inventory(second, extra_tables=("shadow_table",))
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", drifting)
    inventories = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        cli._apply_targets(
            _args(),
            _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
            inventories,
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "OTHER_TARGET_CHANGED"
    assert ledger.closed is True
    # 첫 target만 처리하고 멈춘다.
    assert state.handled == [transition.ORDERED_TARGETS[0]]


def test_unknown_relation_changes_the_fingerprint() -> None:
    """`target_fingerprint()`가 relation 집합을 담는지 직접 본다."""

    baseline = transition.target_fingerprint(_inventory())
    for drift in (
        _inventory(extra_tables=("shadow_table",)),
        _inventory(extra_sequences=("shadow_seq",)),
        _inventory(other=("shadow_other",)),
        _inventory(extensions=("plpgsql", "vector", "pg_trgm")),
        _inventory(views=(transition.LEGACY_VIEW, "shadow_view")),
    ):
        assert transition.target_fingerprint(drift) != baseline


def test_execution_identity_changes_the_fingerprint() -> None:
    """server major·role·owner도 fingerprint에 들어간다."""

    baseline = transition.target_fingerprint(_inventory())
    assert transition.target_fingerprint(_inventory(server_major=15)) != baseline
    assert transition.target_fingerprint(_inventory(role_name="someone")) != baseline
    assert transition.target_fingerprint(_inventory(owner_match=False)) != baseline
    assert transition.target_fingerprint(_inventory(is_superuser=False)) != baseline


# ---------------------------------------------------------------------------
# 구현리뷰 2차 필수 1
# ---------------------------------------------------------------------------


def test_expected_compatibility_hash_is_not_the_legacy_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approval이 증명해야 할 것은 **만들어질 View**다.

    legacy hash를 재사용하면 치환 결과와 무관한 값을 승인하게 된다
    (구현리뷰 2차 필수 1-1).
    """

    _stub_inventories(monkeypatch)
    inventories = cli.collect_inventories(cli.ConnectionLedger(), _inspector([]))
    report = cli.preflight_report(inventories)
    expected = cli.expected_approval(report, inventories, "GH-104")
    assert expected["compatibility_view_sha256"] == transition.COMPAT_VIEW_SHA256
    assert expected["compatibility_view_sha256"] != transition.LEGACY_VIEW_SHA256


@pytest.mark.parametrize(
    "sabotage",
    [
        "missing",
        "replaced",
        "other_target",
    ],
)
def test_archive_digest_must_match_the_real_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sabotage: str
) -> None:
    """receipt의 archive hash를 형식만 보면 임의 64hex가 통과한다."""

    payload = _valid_approval(monkeypatch)
    approval = _write(tmp_path, payload)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    digests = _make_archives(root)
    first = transition.ORDERED_TARGETS[0]
    if sabotage == "missing":
        (root / transition.archive_name(first, "GH-104")).unlink()
    elif sabotage == "replaced":
        # 같은 길이로 내용만 바꾼다.
        path = root / transition.archive_name(first, "GH-104")
        path.write_bytes(b"X" * len(path.read_bytes()))
    else:
        digests[first] = digests[transition.ORDERED_TARGETS[1]]
    receipts = _receipt_args(tmp_path, payload["preflight_bundle_sha256"], digests)
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            [
                "--apply",
                "--approval",
                str(approval),
                "--backup-root",
                str(root),
                *receipts,
                "--change-ref",
                "GH-104",
                *[
                    a
                    for t in transition.ORDERED_TARGETS
                    for a in ("--confirm-target", t)
                ],
            ],
            inspector=_inspector([]),
            ledger=ledger,
        )
    assert caught.value.reason_code in {"BACKUP_INVALID", "BACKUP_REQUIRED"}
    assert ledger.mutating == []


@pytest.mark.parametrize(
    "timestamp",
    ["not-a-date", "", "2026-08-21T12:00:00", "2026-08-21", "12:00:00+09:00"],
)
def test_approved_at_requires_offset_timestamp(tmp_path: Path, timestamp: str) -> None:
    """`not-a-date`가 통과하면 승인 시점을 신뢰할 수 없다(구현리뷰 2차 필수 1-3)."""

    payload = _approval(approved_at=timestamp)
    with pytest.raises(transition.TransitionError) as caught:
        transition.validate_approval_schema(payload)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_valid_offset_timestamp_passes() -> None:
    # `20260821T120000+0900` 같은 basic format도 Python이 받는다. 정상이다.
    for value in (
        "2026-08-21T12:00:00+09:00",
        "2026-08-21T03:00:00Z",
        "20260821T120000+0900",
    ):
        transition.validate_approval_schema(_approval(approved_at=value))


def test_target_drift_after_approval_stops_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approval 이후 현재 target이 흔들리면 그 target에 쓰지 않는다.

    승인 시점 상태와 쓰기 시점 상태가 다르면 승인이 무의미하다
    (구현리뷰 3차 필수 1).
    """

    state = _stub_inventories(monkeypatch)
    inventories = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    stale = dict.fromkeys(transition.ORDERED_TARGETS, "9" * 64)
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._apply_targets(
            _args(),
            _approval(target_fingerprint_sha256_by_target=stale),
            inventories,
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "APPROVAL_MISMATCH"
    # lock을 잡은 뒤 확인하므로 connection은 열리지만 handler는 부르지 않는다.
    assert state.handled == []
    assert state.final == set()
    assert ledger.closed is True


def test_other_target_base_drift_is_caught_even_when_preserved_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """preserved projection이 같아도 base content가 바뀌면 잡아야 한다.

    비대상 불변 검사가 `preserved_projection_sha256()`만 쓰면 통과한다
    (구현리뷰 3차 필수 1).
    """

    second = transition.ORDERED_TARGETS[1]
    drifted_content = {**dict(_inventory(second).base_content), "lot_history": "9" * 64}
    drifted = _inventory(second, base_content=drifted_content)

    # 전제: preserved projection은 동일하다. base content만 다르다.
    assert transition.preserved_projection_sha256(
        drifted
    ) == transition.preserved_projection_sha256(_inventory(second))

    inventories = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    approved = _target_fingerprints()

    state = _stub_inventories(monkeypatch)
    base_read = state.read

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        if database == second and database not in state.final:
            return drifted
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", reading)
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        cli._apply_targets(
            _args(),
            _approval(target_fingerprint_sha256_by_target=approved),
            inventories,
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "OTHER_TARGET_CHANGED"
    assert ledger.closed is True


# ---------------------------------------------------------------------------
# 구현리뷰 4차 필수 1·2 — 순차 기준선·transaction 내 재확인
# ---------------------------------------------------------------------------


def test_handler_receives_the_in_transaction_inventory_not_the_preflight_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler에 preflight의 stale inventory를 넘기면 확인한 것과 다른 것을 바꾼다.

    preflight dict와 transaction 내 수집 결과는 값이 같아도 **다른 객체**다. handler가
    받은 것이 어느 쪽인지 객체 동일성으로 가른다(구현리뷰 4차 필수 2).
    """

    state = _stub_inventories(monkeypatch)
    base_read = state.read
    produced: list[int] = []

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        inventory = base_read(connection, database=database, **kwargs)
        produced.append(id(inventory))
        return inventory

    monkeypatch.setattr(transition, "read_inventory", reading)
    preflight = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    stale = {id(v) for v in preflight.values()}
    got: list[int] = []

    def handler(connection: Any, database: str, inventory: Any) -> None:
        got.append(id(inventory))
        state.handler(connection, database, inventory)

    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        preflight,
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([]),
        handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    assert len(got) == len(transition.ORDERED_TARGETS)
    assert not (set(got) & stale)
    assert set(got) <= set(produced)


def test_recheck_happens_after_the_mutating_connection_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재확인이 mutating session **밖**에서 일어나면 그 사이 상태가 바뀔 수 있다."""

    state = _stub_inventories(monkeypatch)
    order: list[str] = []
    base_read = state.read

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        order.append(f"read:{database}")
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", reading)

    @contextlib.contextmanager
    def mutator(database: str) -> Iterator[Any]:
        order.append(f"open:{database}")
        yield _FakeConnection()
        order.append(f"close:{database}")

    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        {d: _inventory(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        mutator,
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    first = transition.ORDERED_TARGETS[0]
    # 첫 target의 pre/post 수집이 모두 open과 close 사이에 있어야 한다.
    opened = order.index(f"open:{first}")
    closed = order.index(f"close:{first}")
    reads = [i for i, e in enumerate(order) if e == f"read:{first}"][:2]
    assert len(reads) == 2
    assert all(opened < i < closed for i in reads)


def test_post_state_must_be_final_before_the_transaction_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler가 아무것도 바꾸지 않았는데 성공으로 넘어가면 안 된다."""

    _stub_inventories(monkeypatch)
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        cli._apply_targets(
            _args(),
            _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
            {d: _inventory(d) for d in transition.ORDERED_TARGETS},
            ledger,
            _inspector([]),
            _mutator([]),
            lambda _c, _d, _i: None,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"
    assert ledger.closed is True


def test_post_state_rejects_a_target_left_on_the_legacy_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """행은 final인데 View를 되돌리지 않았다면 전환이 끝난 게 아니다."""

    half_done = _inventory(
        wafer_type=transition.FINAL_WAFER_TYPE,
        alarms=FINAL_ALARMS,
        view_sha=transition.LEGACY_VIEW_SHA256,
    )
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_post_state(half_done)
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_post_state_rejects_a_target_still_on_legacy_rows() -> None:
    """compat View만 만들고 데이터를 바꾸지 않은 상태도 거부한다."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_post_state(
            _inventory(view_sha=transition.COMPAT_VIEW_SHA256)
        )
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_post_state_accepts_the_completed_transition() -> None:
    for database in transition.ORDERED_TARGETS:
        assert transition.classify_post_state(_final(database)) is (
            transition.BaseState.FINAL_ADOPTED
        )


# ---------------------------------------------------------------------------
# 구현리뷰 4차 필수 3 — backup 증거를 고정 값·실물과 대조
# ---------------------------------------------------------------------------


def _apply_argv(approval: Path, root: Path, receipts: list[str]) -> list[str]:
    return [
        "--apply",
        "--approval",
        str(approval),
        "--backup-root",
        str(root),
        *receipts,
        "--change-ref",
        "GH-104",
        *[a for t in transition.ORDERED_TARGETS for a in ("--confirm-target", t)],
    ]


def _prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Any]:
    payload = _valid_approval(monkeypatch)
    approval = _write(tmp_path, payload)
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    return approval, root, payload


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("backup_image_digest", "postgres@sha256:" + "d" * 64),
        ("backup_tool_version", "pg_dump (PostgreSQL) 16.15 (Ubuntu)"),
        ("backup_tool_version", "anything"),
        ("backup_tool_version", "pg_dump (PostgreSQL) 16.10"),
    ],
)
def test_forged_backup_client_evidence_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    """형식만 맞는 image digest·version 문자열이 통과하면 증적이 무의미하다."""

    approval, root, payload = _prepared(tmp_path, monkeypatch)
    digests = _make_archives(root, bundle=payload["preflight_bundle_sha256"])
    sidecars = _sidecar_digests(root)
    argv: list[str] = []
    for database in transition.ORDERED_TARGETS:
        path = _write(
            tmp_path,
            _receipt(
                database,
                preflight_bundle_sha256=payload["preflight_bundle_sha256"],
                archive_sha256=digests[database],
                view_sidecar_sha256=sidecars[database],
                target_fingerprint_sha256=_target_fingerprints()[database],
                **{key: value},
            ),
            name=f"receipt_{database}",
        )
        argv += ["--receipt", str(path)]
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            _apply_argv(approval, root, argv), inspector=_inspector([]), ledger=ledger
        )
    assert caught.value.reason_code == "BACKUP_CLIENT_VERSION_MISMATCH"
    assert ledger.mutating == []


def test_pinned_backup_client_evidence_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전제 확인 — 고정 값 그대로면 증적 단계를 통과해 MODE_NOT_WIRED까지 간다."""

    approval, root, payload = _prepared(tmp_path, monkeypatch)
    digests = _make_archives(root, bundle=payload["preflight_bundle_sha256"])
    receipts = _receipt_args(
        tmp_path,
        payload["preflight_bundle_sha256"],
        digests,
        _target_fingerprints(),
        _sidecar_digests(root),
    )
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            _apply_argv(approval, root, receipts),
            inspector=_inspector([]),
            ledger=cli.ConnectionLedger(),
        )
    assert caught.value.reason_code == "MODE_NOT_WIRED"


@pytest.mark.parametrize("tamper", ["missing", "replaced", "symlinked", "directory"])
def test_view_sidecar_must_be_a_real_file_in_the_trusted_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    """sidecar를 경로만 보고 믿으면 저장소 밖 임의 파일이 증적이 된다."""

    approval, root, payload = _prepared(tmp_path, monkeypatch)
    digests = _make_archives(root, bundle=payload["preflight_bundle_sha256"])
    sidecars = _sidecar_digests(root)
    receipts = _receipt_args(
        tmp_path,
        payload["preflight_bundle_sha256"],
        digests,
        _target_fingerprints(),
        sidecars,
    )
    first = transition.ORDERED_TARGETS[0]
    path = root / transition.view_sidecar_name(first, "GH-104")
    if tamper == "missing":
        path.unlink()
        expected = "BACKUP_REQUIRED"
    elif tamper == "replaced":
        path.write_bytes(b"Y" * len(path.read_bytes()))
        expected = "BACKUP_INVALID"
    elif tamper == "symlinked":
        outside = tmp_path / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        expected = "BACKUP_INVALID"
    else:
        path.unlink()
        path.mkdir(mode=0o700)
        expected = "BACKUP_INVALID"

    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            _apply_argv(approval, root, receipts),
            inspector=_inspector([]),
            ledger=ledger,
        )
    assert caught.value.reason_code == expected
    assert ledger.mutating == []


def test_view_sidecar_name_is_bound_to_target_and_change_ref() -> None:
    """다른 target·다른 Task의 sidecar를 돌려 쓸 수 없다."""

    names = {
        transition.view_sidecar_name(d, "GH-104") for d in transition.ORDERED_TARGETS
    }
    assert len(names) == len(transition.ORDERED_TARGETS)
    first = transition.ORDERED_TARGETS[0]
    assert transition.view_sidecar_name(first, "GH-104") != (
        transition.view_sidecar_name(first, "GH-999")
    )
    assert all(transition.DATASET_EPOCH in name for name in names)


def test_receipt_with_another_targets_sidecar_name_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval, root, payload = _prepared(tmp_path, monkeypatch)
    digests = _make_archives(root, bundle=payload["preflight_bundle_sha256"])
    sidecars = _sidecar_digests(root)
    other = transition.ORDERED_TARGETS[1]
    argv: list[str] = []
    for database in transition.ORDERED_TARGETS:
        overrides: dict[str, Any] = {
            "preflight_bundle_sha256": payload["preflight_bundle_sha256"],
            "archive_sha256": digests[database],
            "view_sidecar_sha256": sidecars[database],
            "target_fingerprint_sha256": _target_fingerprints()[database],
        }
        if database == transition.ORDERED_TARGETS[0]:
            overrides["view_sidecar_name"] = transition.view_sidecar_name(
                other, "GH-104"
            )
        path = _write(
            tmp_path, _receipt(database, **overrides), name=f"receipt_{database}"
        )
        argv += ["--receipt", str(path)]
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            _apply_argv(approval, root, argv),
            inspector=_inspector([]),
            ledger=cli.ConnectionLedger(),
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_recheck_rejects_a_disallowed_state_even_when_approval_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approval이 허용 밖 상태로 발급됐다면 fingerprint가 맞아도 쓰면 안 된다.

    승인 대조만 하면 승인서가 곧 허용이 된다. transaction 안 재확인은
    `classify_target()`의 exact 허용 상태 판정을 함께 거쳐야 한다(구현리뷰 4차 필수 2).
    """

    # RAG table이 없는 상태 — 어떤 승인으로도 전환 대상이 될 수 없다.
    broken = {
        d: _inventory(d, drop_tables=(next(iter(transition.RAG_TABLES)),))
        for d in transition.ORDERED_TARGETS
    }
    state = _stub_inventories(monkeypatch)

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        if database in state.final:
            return state.read(connection, database=database, **kwargs)
        return broken[database]

    monkeypatch.setattr(transition, "read_inventory", reading)
    # 승인서가 바로 그 잘못된 상태를 담고 발급됐다고 가정한다.
    approved = {d: transition.target_fingerprint(v) for d, v in broken.items()}
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        cli._apply_targets(
            _args(),
            _approval(target_fingerprint_sha256_by_target=approved),
            broken,
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"
    assert state.handled == []
    assert ledger.closed is True


def test_every_transition_read_demands_a_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 곳이라도 `require_snapshot`을 끄면 그 수집은 여러 시점이 섞인 값이 된다."""

    state = _stub_inventories(monkeypatch)
    base_read = state.read
    flags: list[Any] = []

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        flags.append(kwargs.get("require_snapshot"))
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", reading)
    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        {d: _inventory(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([]),
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    # target당 pre/post 2회 + 나머지 2개 = 4회, 세 target이면 12회다.
    assert len(flags) == 4 * len(transition.ORDERED_TARGETS)
    assert flags == [True] * len(flags)


def test_preflight_collection_also_demands_a_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`collect_inventories()`의 수집도 같은 계약을 지켜야 한다."""

    flags: list[Any] = []

    def reading(_connection: Any, *, database: str, **kwargs: Any) -> Any:
        flags.append(kwargs.get("require_snapshot"))
        return _inventory(database)

    monkeypatch.setattr(transition, "read_inventory", reading)
    cli.collect_inventories(cli.ConnectionLedger(), _inspector([]))
    assert flags == [True] * len(transition.ORDERED_TARGETS)


# ---------------------------------------------------------------------------
# 구현리뷰 5차 필수 1 — handler가 보존 대상을 건드리면 commit 전에 멈춘다
# ---------------------------------------------------------------------------


def _replace(inventory: Any, **fields: Any) -> Any:
    import dataclasses

    return dataclasses.replace(inventory, **fields)


def _bump_row(name: str) -> Any:
    def drift(inventory: Any) -> Any:
        counts = dict(inventory.row_counts)
        counts[name] = counts.get(name, 0) + 1
        return _replace(inventory, row_counts=counts)

    return drift


def _drop_handoff_index(inventory: Any) -> Any:
    """handoff table의 index 하나를 지운다.

    주체를 `document_corpus`에서 `document_chunk`로 옮겼다 — 전자는 최종 기준에 없어
    handoff 목록에서 빠졌고, 없어진 table로 만드는 drift는 아무것도 증명하지 않는다.
    """

    indexes = {k: dict(v) for k, v in inventory.indexes.items()}
    target = indexes.get("document_chunk", {})
    if target:
        target.pop(next(iter(target)))
    return _replace(inventory, indexes=indexes)


def _tampering(state: _StatefulTargets, drift: Any, only: str | None = None) -> Any:
    """전환은 정상 수행하되 보존 대상 한 가지를 함께 바꾸는 handler.

    `only`는 그 변경이 의미를 갖는 target을 지정한다 — handoff table·index는
    `kosa_text2sql`에만 있어서 다른 target에 적용하면 아무것도 바뀌지 않는다.
    """

    def handler(connection: Any, database: str, inventory: Any) -> None:
        if only is None or database == only:
            state.drift[database] = drift
        state.handler(connection, database, inventory)

    return handler


@pytest.mark.parametrize(
    ("label", "drift", "only"),
    [
        ("nl_query_log 행 추가", _bump_row("nl_query_log"), None),
        ("RAG document 행 추가", _bump_row("document"), None),
        (
            "RAG live fingerprint 변경",
            lambda i: _replace(i, rag_live_fingerprint="1" * 64),
            None,
        ),
        (
            "RAG embedding projection 변경",
            lambda i: _replace(i, rag_embedding_projection="2" * 64),
            None,
        ),
        ("handoff table 행 추가", _bump_row("document_chunk"), "kosa_text2sql"),
        ("handoff index 제거", _drop_handoff_index, "kosa_text2sql"),
        (
            "sequence 추가",
            lambda i: _replace(i, sequences=(*i.sequences, "shadow_seq")),
            None,
        ),
        ("외부 FK child 행 추가", _bump_row("action_delivery"), None),
        (
            "extension 추가",
            lambda i: _replace(i, extensions=(*i.extensions, "pg_trgm")),
            None,
        ),
        ("실행 role 교체", lambda i: _replace(i, role_name="someone_else"), None),
        ("소유자 확인 뒤집기", lambda i: _replace(i, owner_match=False), None),
        (
            "모르는 table 추가",
            lambda i: _replace(i, tables=(*i.tables, "shadow_table")),
            None,
        ),
    ],
)
def test_preserved_drift_during_transition_is_rejected(
    monkeypatch: pytest.MonkeyPatch, label: str, drift: Any, only: str | None
) -> None:
    """전환이 base 9 밖을 건드리면 commit 전에 멈춰야 한다.

    `classify_post_state()`는 "final base와 compat View가 있다"만 보므로 이 변경들을
    전부 통과시킨다. 게다가 바뀐 값이 새 기준선이 되어 비대상 검사까지 통과한다
    (구현리뷰 5차 필수 1).
    """

    state = _stub_inventories(monkeypatch)
    ledger = cli.ConnectionLedger()
    stops_at = only or transition.ORDERED_TARGETS[0]
    with pytest.raises(transition.TransitionError) as caught:
        _run_apply(_tampering(state, drift, only), ledger, monkeypatch, state)
    assert caught.value.reason_code in {
        "RAG_PRESERVATION_FAILED",
        "OTHER_TARGET_CHANGED",
        "TARGET_STATE_UNSUPPORTED",
    }, label
    # 변경이 일어난 target에서 곧바로 멈춘다 — 그 뒤로는 손대지 않는다.
    index = transition.ORDERED_TARGETS.index(stops_at)
    assert ledger.mutating == list(transition.ORDERED_TARGETS[: index + 1])
    assert ledger.closed is True


def test_clean_transition_still_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """전제 확인 — 보존 대상을 건드리지 않으면 세 target을 완주한다."""

    state = _stub_inventories(monkeypatch)
    _run_apply(None, cli.ConnectionLedger(), monkeypatch, state)
    assert state.handled == list(transition.ORDERED_TARGETS)


# ---------------------------------------------------------------------------
# 구현리뷰 5차 필수 2 — final no-op과 부분 성공 후 재실행
# ---------------------------------------------------------------------------


def test_already_final_target_is_a_no_op_and_the_rest_still_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """첫 DB commit 뒤 실패했을 때 재실행이 첫 DB에서 막히면 복구가 불가능하다."""

    first = transition.ORDERED_TARGETS[0]
    state = _stub_inventories(monkeypatch)
    state.final.add(first)  # 앞선 실행에서 이미 전환된 상태
    approved = {
        d: transition.target_fingerprint(_final(d) if d == first else _inventory(d))
        for d in transition.ORDERED_TARGETS
    }
    inventories = {
        d: (_final(d) if d == first else _inventory(d))
        for d in transition.ORDERED_TARGETS
    }
    root = Path(tempfile.mkdtemp(prefix="v5cm26-"))
    _seed_markers(root, [first], {first: _final(first)})
    ledger = cli.ConnectionLedger()
    assert (
        cli._apply_targets(
            _args(backup_root=root),
            _approval(target_fingerprint_sha256_by_target=approved),
            inventories,
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
        == cli.EXIT_OK
    )
    # 이미 final인 target에는 handler를 부르지 않는다.
    assert state.handled == list(transition.ORDERED_TARGETS[1:])
    assert state.final == set(transition.ORDERED_TARGETS)


def test_apply_targets_helper_skips_handler_when_all_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """내부 helper 확인. exact no-op의 정본은 `_run()` 전체 회귀다."""

    state = _stub_inventories(monkeypatch)
    state.final.update(transition.ORDERED_TARGETS)
    approved = {
        d: transition.target_fingerprint(_final(d)) for d in transition.ORDERED_TARGETS
    }
    root = Path(tempfile.mkdtemp(prefix="v5cm26-"))
    _seed_markers(
        root,
        transition.ORDERED_TARGETS,
        {d: _final(d) for d in transition.ORDERED_TARGETS},
    )
    log: list[str] = []
    assert (
        cli._apply_targets(
            _args(backup_root=root),
            _approval(target_fingerprint_sha256_by_target=approved),
            {d: _final(d) for d in transition.ORDERED_TARGETS},
            cli.ConnectionLedger(),
            _inspector([]),
            _mutator([], log),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
        == cli.EXIT_OK
    )
    assert state.handled == []
    # lock은 잡되 어떤 DML/DDL도 보내지 않는다.
    assert not [
        s
        for s in log
        if s.split()[0] in {"INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE"}
    ]


def test_final_target_that_drifts_inside_the_lock_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """no-op 경로도 전후 불변을 확인한다."""

    state = _stub_inventories(monkeypatch)
    state.final.update(transition.ORDERED_TARGETS)
    calls = {"n": 0}
    base_read = state.read

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # 같은 target의 두 번째 읽기
            return _final(database, extra_tables=("shadow_table",))
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", reading)
    approved = {
        d: transition.target_fingerprint(_final(d)) for d in transition.ORDERED_TARGETS
    }
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        cli._apply_targets(
            _args(),
            _approval(target_fingerprint_sha256_by_target=approved),
            {d: _final(d) for d in transition.ORDERED_TARGETS},
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "OTHER_TARGET_CHANGED"
    assert ledger.closed is True


# ---------------------------------------------------------------------------
# 구현리뷰 5차 필수 3 — lock 획득 순서를 SQL로 고정한다
# ---------------------------------------------------------------------------


def test_no_snapshot_bearing_statement_precedes_the_table_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPEATABLE READ snapshot은 첫 SELECT에서 고정된다.

    그 SELECT가 `LOCK TABLE`보다 앞서면, lock을 기다리는 사이 다른 transaction이
    commit해도 보이지 않는다 — "lock 안 재확인"이 아니라 "lock 이전 과거 재확인"이 된다
    (구현리뷰 6차 필수 2). 그래서 lock 전에는 `SHOW`·`SET LOCAL`·`LOCK TABLE`만 쓴다.
    """

    state = _stub_inventories(monkeypatch)
    log: list[str] = []
    base_read = state.read

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        log.append("READ_INVENTORY")
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", reading)
    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        {d: _inventory(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([], log),
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    prefix = log[: log.index("READ_INVENTORY")]
    # mutex는 transaction **밖**에서 잡는다. 여기서 보는 것은 BEGIN 이후 구간이다.
    first = prefix[len(prefix) - prefix[::-1].index("BEGIN") :]
    last_lock = max(i for i, s in enumerate(first) if s.startswith("LOCK TABLE"))
    selects = [i for i, s in enumerate(first) if s.startswith("SELECT")]
    assert selects, "snapshot을 만드는 SELECT가 하나도 없다 — 확인이 사라졌다"
    assert min(selects) > last_lock

    # lock 이전 statement는 전부 snapshot을 만들지 않는 utility여야 한다.
    for statement in first[: last_lock + 1]:
        assert statement.startswith(("SHOW", "SET LOCAL", "LOCK TABLE")), statement


def test_lock_order_and_modes_are_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """보존·base 모두 `SHARE`를 이름 정렬 순서로 잡는다."""

    state = _stub_inventories(monkeypatch)
    log: list[str] = []
    base_read = state.read

    def reading(connection: Any, *, database: str, **kwargs: Any) -> Any:
        log.append("READ_INVENTORY")
        return base_read(connection, database=database, **kwargs)

    monkeypatch.setattr(transition, "read_inventory", reading)
    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        {d: _inventory(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([], log),
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    first_target = transition.ORDERED_TARGETS[0]
    whole = log[: log.index("READ_INVENTORY")]
    # mutex 획득은 BEGIN 앞에 있어야 한다(구현리뷰 8차 필수 2).
    assert any("pg_advisory_lock(" in s for s in whole[: whole.index("BEGIN")])
    prefix = whole[whole.index("BEGIN") + 1 :]
    assert prefix[0] == transition.ISOLATION_SQL
    assert prefix[1:4] == [
        transition.LOCK_TIMEOUT_SQL,
        transition.STATEMENT_TIMEOUT_SQL,
        transition.IDLE_TIMEOUT_SQL,
    ]

    def names(statements: list[str]) -> list[str]:
        return [s.split("public.")[1].split(" ")[0] for s in statements]

    shares = names([s for s in prefix if "IN SHARE MODE" in s])
    base = sorted(transition.BASE_TABLES)
    assert shares[-len(base) :] == base
    preserved = shares[: -len(base)]
    # 정렬 순서 자체를 본다 — `watched_tables()`를 기대값으로 쓰면 양변이 함께 흔들린다.
    assert preserved == sorted(preserved)
    assert set(preserved) == set(transition.watched_tables(first_target))
    assert len(preserved) == len(set(preserved))
    # 상태를 확인하기 전에는 ACCESS EXCLUSIVE를 잡지 않는다.
    assert not [s for s in prefix if "ACCESS EXCLUSIVE" in s]


def test_advisory_lock_key_is_distinct_per_target() -> None:
    keys = {transition.advisory_lock_key(d) for d in transition.ORDERED_TARGETS}
    assert len(keys) == len(transition.ORDERED_TARGETS)
    assert all(0 <= key <= 0x7FFFFFFF for key in keys)
    # 결정적이다 — 실행마다 달라지면 상호 배제가 성립하지 않는다.
    assert transition.advisory_lock_key("kosa_agent") == transition.advisory_lock_key(
        "kosa_agent"
    )
    with pytest.raises(transition.TransitionError) as caught:
        transition.advisory_lock_key("kosa")
    assert caught.value.reason_code == "TARGET_NOT_ALLOWED"


@pytest.mark.parametrize("setting", sorted(transition.SHOW_TIMEOUT_SQL))
def test_disabled_timeout_fails_closed_before_any_lock(setting: str) -> None:
    """timeout이 0이면 공용 서버를 무한히 붙잡을 수 있다. lock 전에 멈춘다.

    autocommit이면 `SET LOCAL`이 버려져 `SHOW`가 session 기본값 `0`을 답한다. 즉 이
    확인 하나가 timeout 적용과 열린 transaction을 함께 증명한다.
    """

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            if sql == transition.SHOW_TIMEOUT_SQL[setting]:
                return _FakeResult([{setting: "0"}])
            return _FakeResult(_answer(sql))

    connection = _Connection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.acquire_target_locks(connection, database="kosa_agent")
    assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"
    assert not [s for s in connection.log if s.startswith("LOCK TABLE")]
    assert not [s for s in connection.log if "pg_advisory" in s]


def test_autocommit_session_is_rejected_even_at_repeatable_read() -> None:
    """session default가 REPEATABLE READ여도 autocommit이면 snapshot이 매번 새로 잡힌다.

    `SET LOCAL`이 자기 암묵 transaction과 함께 버려지므로 `SHOW`가 `0`을 답한다.
    문자열 isolation 확인만으로는 이 상황을 가릴 수 없다(구현리뷰 5차 필수 3).
    """

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            for name, show in transition.SHOW_TIMEOUT_SQL.items():
                if sql == show:
                    return _FakeResult([{name: "0"}])
            return _FakeResult(_answer(sql))

    connection = _Connection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.acquire_target_locks(connection, database="kosa_agent")
    assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"
    assert not [s for s in connection.log if s.startswith("LOCK TABLE")]


def test_open_transaction_is_confirmed_after_the_locks() -> None:
    """SELECT로 하는 확인은 lock 뒤에 온다. 그래도 확인은 반드시 일어난다."""

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            if sql.startswith("SELECT pg_current_xact_id_if_assigned()"):
                return _FakeResult([{"assigned": False, "started": False, "pid": 1}])
            return _FakeResult(_answer(sql))

    connection = _Connection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.acquire_target_locks(connection, database="kosa_agent")
    assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"
    # lock은 이미 잡혔지만 transaction은 곧 rollback된다.
    assert [s for s in connection.log if s.startswith("LOCK TABLE")]


def test_missing_lock_target_fails_with_a_reason_code_not_a_raw_error() -> None:
    """없는 table에 `LOCK TABLE`을 보내면 raw `UndefinedTable`이 새어나간다.

    존재를 미리 catalog SELECT로 확인하면 그 SELECT가 lock보다 먼저 snapshot을 고정한다
    (구현리뷰 6차 필수 2). 그래서 확인 없이 보내고 경계에서 정규화한다.
    """

    from sqlalchemy.exc import ProgrammingError

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            if sql.startswith("LOCK TABLE"):
                raise ProgrammingError(sql, {}, Exception("UndefinedTable"))
            return _FakeResult(_answer(sql))

    connection = _Connection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.acquire_target_locks(connection, database="kosa_agent")
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"
    # lock 전에 catalog를 조회하지 않는다.
    assert not [s for s in connection.log if s.startswith("SELECT")]


def test_access_exclusive_is_only_taken_when_escalating() -> None:
    """`acquire_target_locks()`는 절대 `ACCESS EXCLUSIVE`를 잡지 않는다."""

    connection = _FakeConnection()
    transition.acquire_target_locks(connection, database="kosa_agent")
    assert not [s for s in connection.log if "ACCESS EXCLUSIVE" in s]

    transition.escalate_base_locks(connection, database="kosa_agent")
    escalated = [s for s in connection.log if "ACCESS EXCLUSIVE" in s]
    names = [s.split("public.")[1].split(" ")[0] for s in escalated]
    assert names == sorted(transition.BASE_TABLES)


# ---------------------------------------------------------------------------
# 구현리뷰 5차 필수 4 — sidecar 내용을 검증한다
# ---------------------------------------------------------------------------


def _apply_with_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any
) -> Any:
    approval, root, payload = _prepared(tmp_path, monkeypatch)
    bundle = payload["preflight_bundle_sha256"]
    digests = _make_archives(root, bundle=bundle)
    first = transition.ORDERED_TARGETS[0]
    path = root / transition.view_sidecar_name(first, "GH-104")
    mutate(path, first, bundle)
    receipts = _receipt_args(
        tmp_path, bundle, digests, _target_fingerprints(), _sidecar_digests(root)
    )
    ledger = cli.ConnectionLedger()
    with pytest.raises(cli.TransitionAbort) as caught:
        cli._run(
            _apply_argv(approval, root, receipts),
            inspector=_inspector([]),
            ledger=ledger,
        )
    assert ledger.mutating == []
    return caught.value


def test_arbitrary_bytes_sidecar_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이전에는 `view-of-kosa_agent_e2e` 같은 임의 바이트가 증적으로 통과했다."""

    def mutate(path: Path, _db: str, _bundle: str) -> None:
        path.write_bytes(b"view-of-something")

    assert _apply_with_sidecar(tmp_path, monkeypatch, mutate).reason_code == (
        "ARTIFACT_INVALID"
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("artifact_type", "something_else", "ARTIFACT_INVALID"),
        ("dataset_epoch", "kosa_0813", "ARTIFACT_INVALID"),
        ("view_name", "v_other", "ARTIFACT_INVALID"),
        ("view_definition_sha256", "not-a-hash", "ARTIFACT_INVALID"),
        ("view_definition_sha256", "9" * 64, "BACKUP_INVALID"),
        ("view_owner", "someone", "BACKUP_INVALID"),
        ("view_acl", "{public=r/public}", "BACKUP_INVALID"),
        ("change_ref", "GH-999", "BACKUP_INVALID"),
        ("preflight_bundle_sha256", "8" * 64, "BACKUP_INVALID"),
        ("database", "kosa_text2sql", "ARTIFACT_INVALID"),
    ],
)
def test_forged_sidecar_content_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    expected: str,
) -> None:
    """의미상 유효한 JSON이어도 승인한 View가 아니면 거부한다.

    파일 digest는 receipt와 self-consistent하게 다시 계산되므로, digest만 보는
    구현에서는 이 모든 위조가 통과한다(구현리뷰 5차 필수 4).
    """

    def mutate(path: Path, database: str, bundle: str) -> None:
        payload = _sidecar(database, preflight_bundle_sha256=bundle)
        payload[field] = value
        path.write_text(json.dumps(payload), encoding="utf-8")

    assert _apply_with_sidecar(tmp_path, monkeypatch, mutate).reason_code == expected


@pytest.mark.parametrize("field", sorted(transition.SIDECAR_KEYS))
def test_sidecar_schema_is_exact(field: str) -> None:
    """key가 빠지거나 늘면 거부한다."""

    payload = _sidecar("kosa_agent")
    del payload[field]
    with pytest.raises(transition.TransitionError) as caught:
        transition.validate_sidecar_schema(payload)
    assert caught.value.reason_code == "ARTIFACT_INVALID"

    payload = _sidecar("kosa_agent")
    payload["extra"] = 1
    with pytest.raises(transition.TransitionError):
        transition.validate_sidecar_schema(payload)


def test_another_targets_valid_sidecar_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schema까지 완전히 유효한 다른 target의 sidecar를 돌려 쓸 수 없다."""

    other = transition.ORDERED_TARGETS[2]

    def mutate(path: Path, _db: str, bundle: str) -> None:
        payload = _sidecar(other, preflight_bundle_sha256=bundle)
        transition.validate_sidecar_schema(payload)  # 전제: 그 자체로는 유효하다
        path.write_text(json.dumps(payload), encoding="utf-8")

    assert _apply_with_sidecar(tmp_path, monkeypatch, mutate).reason_code == (
        "BACKUP_INVALID"
    )


def test_sidecar_must_describe_the_state_the_target_is_actually_in() -> None:
    """이미 compat View인 target에 legacy sidecar를 붙일 수 없다."""

    with pytest.raises(transition.TransitionError) as caught:
        final = _final("kosa_agent")
        transition.assert_sidecar_matches(
            _sidecar("kosa_agent"),
            database=final.database,
            profile=final.profile,
            state=transition.BaseState.FINAL_ADOPTED,
            view_sha256=final.view_sha256,
            view_owner=final.view_owner,
            view_acl=final.view_acl,
            view_comment=final.view_comment,
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


# ---------------------------------------------------------------------------
# 구현리뷰 6차 필수 1 — `_run()` 전체 경로의 no-op과 부분 성공 후 재실행
# ---------------------------------------------------------------------------


def _mixed_state(
    monkeypatch: pytest.MonkeyPatch, already_final: Sequence[str]
) -> _StatefulTargets:
    state = _StatefulTargets()
    state.final.update(already_final)
    state.install(monkeypatch)
    return state


def _prepare_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: _StatefulTargets,
    sidecar_final: bool | None = None,
) -> tuple[Path, Path, list[str]]:
    """approval·archive·sidecar·receipt·기존 marker를 **한 번만** 준비한다.

    `_run()`을 두 번 부르려면 준비와 실행이 분리돼야 한다. 이전 회귀는 한 번 부른 뒤
    같은 값을 자기 자신과 비교해 아무것도 증명하지 못했다(구현리뷰 7차 필수 2).
    """

    import postgres_backup

    # 증적 세트는 **전환 전** 상태를 기술한다. 재실행이 같은 세트를 쓰려면 이 값들이
    # 현재 상태를 따라 움직이면 안 된다(구현리뷰 7차 필수 2).
    pre = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    report = cli.preflight_report(pre)
    expected = cli.expected_approval(report, pre, "GH-104")
    payload = _approval()
    for key in transition.APPROVAL_EXPECTED_KEYS:
        payload[key] = expected[key]
    approval = _write(tmp_path, payload)

    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    bundle = payload["preflight_bundle_sha256"]
    digests: dict[str, str] = {}
    sidecars: dict[str, str] = {}
    for database in transition.ORDERED_TARGETS:
        archive = root / transition.archive_name(database, "GH-104")
        archive.write_bytes(f"dump-of-{database}".encode())
        digests[database] = postgres_backup.archive_digest(archive)
        path = root / transition.view_sidecar_name(database, "GH-104")
        path.write_text(
            json.dumps(
                _sidecar(
                    database,
                    final=False if sidecar_final is None else sidecar_final,
                    preflight_bundle_sha256=bundle,
                )
            ),
            encoding="utf-8",
        )
        sidecars[database] = postgres_backup.archive_digest(path)
        _complete(root, database)

    # 이미 전환된 target은 앞선 실행이 남긴 marker를 갖고 있다.
    for database in sorted(state.final):
        marker = transition.build_marker(
            _final(database),
            state=transition.BaseState.FINAL_ADOPTED,
            change_ref="GH-104",
            preflight_bundle_sha256=bundle,
            archive_sha256=digests[database],
            view_sidecar_sha256=sidecars[database],
            recorded_at="2026-08-22T12:00:00+09:00",
            backup_root_trust="0700",
            # 앞선 실행이 **이 approval로** 전환했다는 기록이다(구현리뷰 18차 필수 1).
            approval_sha256=cli._file_sha256(approval),
            preflight_target_entry=cli.preflight_entry(pre[database]),
        )
        (root / transition.marker_name(database)).write_text(
            json.dumps(marker), encoding="utf-8"
        )

    fingerprints = {
        d: transition.target_fingerprint(pre[d]) for d in transition.ORDERED_TARGETS
    }
    receipts = _receipt_args(tmp_path, bundle, digests, fingerprints, sidecars)
    return approval, root, receipts


def _invoke(
    approval: Path, root: Path, receipts: list[str], handler: Any
) -> tuple[int, cli.ConnectionLedger]:
    ledger = cli.ConnectionLedger()
    code = cli._run(
        _apply_argv(approval, root, receipts),
        inspector=_inspector([]),
        mutator=_mutator([]),
        handler=handler,
        ledger=ledger,
    )
    return code, ledger


def _full_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: _StatefulTargets,
    handler: Any,
    sidecar_final: bool | None = None,
) -> tuple[int, Path, cli.ConnectionLedger]:
    """준비 + 1회 실행."""

    approval, root, receipts = _prepare_artifacts(
        tmp_path, monkeypatch, state, sidecar_final
    )
    code, ledger = _invoke(approval, root, receipts, handler)
    return code, root, ledger


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.iterdir())
    }


def test_full_cli_all_final_is_an_exact_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """세 target 모두 final이면 CLI 전체가 exit 0으로 끝난다.

    이전에는 sidecar가 legacy 전용이라 `_apply_targets()`를 직접 부르는 테스트만
    통과하고 `_run()`은 `BACKUP_INVALID`로 끝났다 — 재실행 자체가 불가능했다
    (구현리뷰 6차 필수 1).
    """

    state = _mixed_state(monkeypatch, transition.ORDERED_TARGETS)
    code, root, ledger = _full_run(tmp_path, monkeypatch, state, state.handler)
    assert code == cli.EXIT_OK
    assert state.handled == []
    assert ledger.mutating == list(transition.ORDERED_TARGETS)
    # 기존 marker 3개가 그대로 있다.
    assert sorted(p.name for p in root.glob("postgres_profile.*.json")) == sorted(
        transition.marker_name(d) for d in transition.ORDERED_TARGETS
    )


def test_full_cli_second_run_changes_no_artifact_byte_or_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 artifact set으로 `_run()`을 **실제로 두 번** 부른다.

    marker를 포함한 backup root 전체의 byte·mtime이 첫 실행 전후로만 바뀌고, 둘째
    실행에서는 한 바이트도 달라지지 않아야 한다(구현리뷰 7차 필수 2).
    """

    state = _mixed_state(monkeypatch, [])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)

    before = _tree(root)
    assert not [name for name in before if name.startswith("postgres_profile.")]

    first_code, _ = _invoke(approval, root, receipts, state.handler)
    assert first_code == cli.EXIT_OK
    assert state.handled == list(transition.ORDERED_TARGETS)
    after_first = _tree(root)
    # 첫 실행은 marker 3개를 새로 남긴다 — 그 외 파일은 그대로다.
    new_names = set(after_first) - set(before)
    assert new_names == {transition.marker_name(d) for d in transition.ORDERED_TARGETS}
    for name in before:
        assert after_first[name] == before[name], name

    # 두 번째 실행. 이제 세 target 모두 final이고 marker도 있다.
    state.handled.clear()
    second_code, _ = _invoke(approval, root, receipts, state.handler)
    assert second_code == cli.EXIT_OK
    assert state.handled == []
    assert _tree(root) == after_first


def test_full_cli_resumes_after_a_partial_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """첫 DB만 전환된 상태에서 재실행하면 나머지 둘을 마치고 완주한다."""

    first = transition.ORDERED_TARGETS[0]
    state = _mixed_state(monkeypatch, [first])
    code, root, ledger = _full_run(tmp_path, monkeypatch, state, state.handler)
    assert code == cli.EXIT_OK
    # 이미 final인 첫 target에는 handler를 부르지 않는다.
    assert state.handled == list(transition.ORDERED_TARGETS[1:])
    assert state.final == set(transition.ORDERED_TARGETS)
    assert ledger.mutating == list(transition.ORDERED_TARGETS)
    # 나머지 두 target의 marker가 새로 생겼다.
    assert sorted(p.name for p in root.glob("postgres_profile.*.json")) == sorted(
        transition.marker_name(d) for d in transition.ORDERED_TARGETS
    )


def test_a_real_mid_run_failure_is_resumable_with_the_same_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """둘째 target에서 **실제로** 실패시킨 뒤 같은 증적으로 다시 돌린다.

    marker를 셋 모아 뒀다 마지막에 쓰면 이 상황에서 `[final, legacy, legacy] + marker
    0`이 남아 재개가 불가능하다. 앞선 회귀는 실패를 만들지 않고 처음부터 final target과
    marker를 함께 심어서 이 장애를 재현하지 못했다(구현리뷰 8차 필수 1).
    """

    state = _mixed_state(monkeypatch, [])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)
    first, second, third = transition.ORDERED_TARGETS

    def boom(connection: Any, database: str, inventory: Any) -> None:
        state.handler(connection, database, inventory)
        if database == second:
            raise transition.TransitionError("TARGET_STATE_UNSUPPORTED", 1)

    with pytest.raises(cli.TransitionAbort):
        _invoke(approval, root, receipts, boom)

    # 첫 target은 commit·marker까지 끝났고, 둘째는 handler가 돌았지만 rollback됐다.
    assert (root / transition.marker_name(first)).exists()
    assert not (root / transition.marker_name(second)).exists()
    assert not (root / transition.marker_name(third)).exists()
    state.final.discard(second)  # rollback — DB 상태는 legacy로 남는다

    # 같은 approval·receipt·archive·sidecar로 재개한다.
    state.handled.clear()
    code, _ledger = _invoke(approval, root, receipts, state.handler)
    assert code == cli.EXIT_OK
    assert state.handled == [second, third]
    assert state.final == set(transition.ORDERED_TARGETS)
    assert sorted(p.name for p in root.glob("postgres_profile.*.json")) == sorted(
        transition.marker_name(d) for d in transition.ORDERED_TARGETS
    )


def test_a_commit_without_its_marker_is_recovered_on_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`commit 성공 → marker write 전 중단` 창을 복구한다.

    target별로 marker를 앞당겨도 이 창은 남는다. 전환이 건드리지 않는 값(보존
    projection·외부 FK)이 approval의 전환 전 기록과 같으면 완료로 인정한다
    (구현리뷰 8차 필수 1).
    """

    state = _mixed_state(monkeypatch, [])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)
    first = transition.ORDERED_TARGETS[0]

    real = cli._write_marker
    calls = {"n": 0}

    def crash(path: Path, payload: Any, backup_root: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("marker 기록 직전 중단")
        real(path, payload, backup_root)

    monkeypatch.setattr(cli, "_write_marker", crash)
    with pytest.raises(RuntimeError):
        _invoke(approval, root, receipts, state.handler)
    assert not list(root.glob("postgres_profile.*.json"))
    assert first in state.final  # DB는 commit됐다

    monkeypatch.setattr(cli, "_write_marker", real)
    state.handled.clear()
    code, _ledger = _invoke(approval, root, receipts, state.handler)
    assert code == cli.EXIT_OK
    assert state.handled == list(transition.ORDERED_TARGETS[1:])
    assert sorted(p.name for p in root.glob("postgres_profile.*.json")) == sorted(
        transition.marker_name(d) for d in transition.ORDERED_TARGETS
    )


def test_recovery_needs_the_approval_recorded_invariants() -> None:
    """아무 final target이나 완료로 인정하지 않는다."""

    inventory = _final("kosa_agent")
    good_preserved = transition.preserved_projection_sha256(inventory)
    good_fk = transition.external_fk_projection_sha256(inventory)
    transition.assert_recoverable_without_marker(
        inventory,
        preserved_projection_sha256=good_preserved,
        external_fk_projection_sha256=good_fk,
    )
    for preserved, fk in (("9" * 64, good_fk), (good_preserved, "9" * 64)):
        with pytest.raises(transition.TransitionError) as caught:
            transition.assert_recoverable_without_marker(
                inventory,
                preserved_projection_sha256=preserved,
                external_fk_projection_sha256=fk,
            )
        assert caught.value.reason_code == "APPROVAL_MISMATCH"


def test_legacy_target_with_a_committed_marker_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """legacy인데 COMMITTED marker가 있으면 둘 중 하나가 거짓이다."""

    state = _mixed_state(monkeypatch, [])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)
    first = transition.ORDERED_TARGETS[0]
    _seed_markers(root, [first], {first: _final(first)})
    with pytest.raises(cli.TransitionAbort) as caught:
        _invoke(approval, root, receipts, state.handler)
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert state.handled == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("change_ref", "GH-999"),
        ("preflight_bundle_sha256", "8" * 64),
        ("target_fingerprint_sha256", "9" * 64),
        ("base_state", transition.BaseState.BASE_LEGACY_EPOCH.value),
    ],
)
def test_stale_marker_is_rejected_before_any_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    """다른 Task·다른 상태의 marker를 재사용할 수 없다."""

    state = _mixed_state(monkeypatch, transition.ORDERED_TARGETS)
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)
    first = transition.ORDERED_TARGETS[0]
    path = root / transition.marker_name(first)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(cli.TransitionAbort) as caught:
        _invoke(approval, root, receipts, state.handler)
    assert caught.value.reason_code == "BACKUP_INVALID"
    assert state.handled == []


def test_full_cli_rejects_a_final_sidecar_on_a_completed_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상태와 증적을 교차 대조한다 — 아무 sidecar나 받지 않는다.

    이미 전환된 target의 증적도 **전환 전** 상태를 기술한다. compat View를 담은
    sidecar는 그 시점에 없었으므로 거부한다(구현리뷰 6차 필수 1·7차 필수 2).
    """

    state = _mixed_state(monkeypatch, transition.ORDERED_TARGETS)
    with pytest.raises(cli.TransitionAbort) as caught:
        _full_run(tmp_path, monkeypatch, state, state.handler, sidecar_final=True)
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_full_cli_rejects_a_final_sidecar_on_a_legacy_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """반대 방향도 막는다."""

    state = _mixed_state(monkeypatch, [])
    with pytest.raises(cli.TransitionAbort) as caught:
        _full_run(tmp_path, monkeypatch, state, state.handler, sidecar_final=True)
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_sidecar_state_declaration_is_checked_on_its_own() -> None:
    """선언한 `base_state`가 실제 상태와 다르면 나머지가 다 맞아도 거부한다.

    View hash·owner·ACL이 현재 상태와 일치하도록 맞춘 payload를 쓴다. 이 조합은 상태
    선언 대조만이 걸러낼 수 있다(구현리뷰 6차 필수 1).
    """

    inventory = _inventory("kosa_agent")
    forged = _sidecar("kosa_agent")
    forged["base_state"] = transition.BaseState.FINAL_ADOPTED.value
    # 전제: View 관련 값은 전부 현재 상태와 일치한다.
    assert forged["view_definition_sha256"] == inventory.view_sha256

    with pytest.raises(transition.TransitionError) as caught:
        transition.assert_sidecar_matches(
            forged,
            database=inventory.database,
            profile=inventory.profile,
            state=transition.BaseState.BASE_LEGACY_EPOCH,
            view_sha256=inventory.view_sha256,
            view_owner=inventory.view_owner,
            view_acl=inventory.view_acl,
            view_comment=inventory.view_comment,
            change_ref="GH-104",
            preflight_bundle_sha256="0" * 64,
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


@pytest.mark.parametrize(
    "value", ["", "PARTIAL_OR_DRIFT", "final_adopted", "BASE_LEGACY", None, 1]
)
def test_sidecar_base_state_must_be_a_known_state(value: Any) -> None:
    """schema 단계에서 거른다 — 연결 전에 `ARTIFACT_INVALID`다."""

    payload = _sidecar("kosa_agent")
    payload["base_state"] = value
    with pytest.raises(transition.TransitionError) as caught:
        transition.validate_sidecar_schema(payload)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_legacy_transition_escalates_base_locks_before_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제로 바꿀 때는 `ACCESS EXCLUSIVE`로 올린 뒤 handler를 부른다.

    승격을 빼면 `SHARE`만 든 채 `ALTER TABLE`을 보내게 된다(구현리뷰 6차 필수 3).
    """

    state = _stub_inventories(monkeypatch)
    log: list[str] = []

    def handler(connection: Any, database: str, inventory: Any) -> None:
        log.append(f"HANDLER:{database}")
        state.handler(connection, database, inventory)

    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        {d: _inventory(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([], log),
        handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    first = log.index("HANDLER:" + transition.ORDERED_TARGETS[0])
    escalations = [
        i for i, s in enumerate(log[:first]) if "IN ACCESS EXCLUSIVE MODE" in s
    ]
    names = [log[i].split("public.")[1].split(" ")[0] for i in escalations]
    assert names == sorted(transition.BASE_TABLES)


def test_final_no_op_never_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    """이미 final인 target에는 `ACCESS EXCLUSIVE`를 잡지 않는다."""

    state = _stub_inventories(monkeypatch)
    state.final.update(transition.ORDERED_TARGETS)
    root = Path(tempfile.mkdtemp(prefix="v5cm26-"))
    _seed_markers(
        root,
        transition.ORDERED_TARGETS,
        {d: _final(d) for d in transition.ORDERED_TARGETS},
    )
    log: list[str] = []
    cli._apply_targets(
        _args(backup_root=root),
        _approval(
            target_fingerprint_sha256_by_target={
                d: transition.target_fingerprint(_final(d))
                for d in transition.ORDERED_TARGETS
            }
        ),
        {d: _final(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([], log),
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    assert not [s for s in log if "ACCESS EXCLUSIVE" in s]
    assert [s for s in log if "IN SHARE MODE" in s]


# ---------------------------------------------------------------------------
# 구현리뷰 7차 필수 1 · 권장 1 — mutex 계약과 transaction 예산
# ---------------------------------------------------------------------------


def test_mutex_is_a_session_lock_not_a_transaction_lock() -> None:
    """`pg_advisory_xact_lock`을 쓰면 transaction과 함께 사라져 직렬화가 깨진다."""

    connection = _FakeConnection()
    transition.acquire_target_mutex(connection, database="kosa_agent")
    assert any("pg_advisory_lock(" in s for s in connection.log)
    assert not [s for s in connection.log if "pg_advisory_xact_lock" in s]
    # mutex를 기다리는 동안에도 무한 대기하지 않는다.
    assert transition.MUTEX_TIMEOUT_SQL in connection.log


def test_mutex_contention_is_a_typed_busy_result() -> None:
    """`lock_timeout`으로 끝나면 raw 예외가 아니라 `TARGET_BUSY`다."""

    from sqlalchemy.exc import OperationalError

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            if "pg_advisory_lock(" in sql:
                raise OperationalError(sql, {}, Exception("lock timeout"))
            return _FakeResult(_answer(sql))

    with pytest.raises(transition.TransitionError) as caught:
        transition.acquire_target_mutex(_Connection(), database="kosa_agent")
    assert caught.value.reason_code == "TARGET_BUSY"
    assert caught.value.exit_code == transition.EXIT_CONFIRM_REQUIRED


def test_locks_are_refused_without_the_mutex() -> None:
    """mutex를 잡지 않고 들어온 배선을 transaction 안에서 잡는다."""

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            if sql.startswith("SELECT count(*) AS held FROM pg_locks"):
                return _FakeResult([{"held": 0}])
            return _FakeResult(_answer(sql))

    with pytest.raises(transition.TransitionError) as caught:
        transition.acquire_target_locks(_Connection(), database="kosa_agent")
    assert caught.value.reason_code == "TARGET_MUTEX_MISSING"


def test_mutex_release_failure_invalidates_the_connection() -> None:
    """해제 실패를 삼키면 pool 안에 lock이 남는다.

    SQLAlchemy connection close는 physical 종료가 아니라 pool 반환일 수 있고, session
    advisory lock은 rollback으로 사라지지 않는다(구현리뷰 8차 필수 2).
    """

    from sqlalchemy.exc import OperationalError

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            raise OperationalError("x", {}, Exception("closed"))

    connection = _Connection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.release_target_mutex(connection, database="kosa_agent")
    assert caught.value.reason_code == "TARGET_MUTEX_LEAKED"
    assert connection.invalidated is True


def test_mutex_release_that_returns_false_is_a_leak() -> None:
    """`pg_advisory_unlock`은 안 잡고 있으면 예외 없이 `false`만 돌려준다."""

    class _Connection(_FakeConnection):
        def execute(self, statement: Any, parameters: Any = None) -> Any:
            sql = " ".join(str(statement).split())
            self.log.append(sql)
            if "pg_advisory_unlock" in sql:
                return _FakeResult([{"released": False}])
            return _FakeResult(_answer(sql))

    connection = _Connection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.release_target_mutex(connection, database="kosa_agent")
    assert caught.value.reason_code == "TARGET_MUTEX_LEAKED"
    assert connection.invalidated is True


def test_successful_release_keeps_the_connection() -> None:
    connection = _FakeConnection()
    transition.release_target_mutex(connection, database="kosa_agent")
    assert connection.invalidated is False


def test_transaction_budget_stops_a_long_lock_hold() -> None:
    """`statement_timeout`은 statement별이라 합산 보유 시간을 못 막는다."""

    transition.assert_within_budget(100.0, now=100.0)
    transition.assert_within_budget(
        100.0, now=100.0 + transition.TRANSACTION_BUDGET_SECONDS
    )
    with pytest.raises(transition.TransitionError) as caught:
        transition.assert_within_budget(
            100.0, now=100.0 + transition.TRANSACTION_BUDGET_SECONDS + 0.01
        )
    assert caught.value.reason_code == "TARGET_BUSY"


def test_apply_stops_when_the_budget_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예산을 넘기면 commit 전에 멈추고 marker도 남지 않는다."""

    state = _stub_inventories(monkeypatch)
    ticks = iter([0.0, 10_000.0] * 10)
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        _run_apply(None, ledger, monkeypatch, state)
    assert caught.value.reason_code == "TARGET_BUSY"
    assert ledger.closed is True


# ---------------------------------------------------------------------------
# 구현리뷰 7차 필수 2 — marker 검사 각각을 격리해서 본다
# ---------------------------------------------------------------------------


def _marker_for(database: str, **overrides: Any) -> dict[str, Any]:
    payload = transition.build_marker(
        _final(database),
        state=transition.BaseState.FINAL_ADOPTED,
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        archive_sha256="a" * 64,
        view_sidecar_sha256="b" * 64,
        recorded_at="2026-08-22T12:00:00+09:00",
        backup_root_trust="0700",
        approval_sha256="a" * 64,
        preflight_target_entry=cli.preflight_entry(_inventory(database)),
    )
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("change_ref", "GH-999"),
        ("preflight_bundle_sha256", "8" * 64),
        ("base_state", transition.BaseState.BASE_LEGACY_EPOCH.value),
    ],
)
def test_completed_targets_rejects_a_marker_from_another_run(
    tmp_path: Path, field: str, value: str
) -> None:
    """다른 Task·다른 상태의 marker는 "완료됨"의 근거가 될 수 없다.

    이 판정이 없으면 그 marker가 재실행 tolerance로 흘러들어간다(구현리뷰 7차 필수 2).
    """

    first = transition.ORDERED_TARGETS[0]
    (tmp_path / transition.marker_name(first)).write_text(
        json.dumps(_marker_for(first, **{field: value})), encoding="utf-8"
    )
    with pytest.raises(cli.TransitionAbort) as caught:
        cli.completed_targets(
            _approval(),
            tmp_path,
            backup_root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_completed_targets_accepts_a_matching_marker(tmp_path: Path) -> None:
    """전제 확인 — 같은 Task·같은 bundle·final 상태면 완료로 인정한다."""

    first = transition.ORDERED_TARGETS[0]
    (tmp_path / transition.marker_name(first)).write_text(
        json.dumps(_marker_for(first)), encoding="utf-8"
    )
    assert set(
        cli.completed_targets(
            _approval(),
            tmp_path,
            backup_root_trust="0700",
            approval_sha256="a" * 64,
        )
    ) == {first}


def _apply_with_markers(
    monkeypatch: pytest.MonkeyPatch,
    already_final: Sequence[str],
    markers: Mapping[str, Mapping[str, Any]],
) -> tuple[Any, cli.ConnectionLedger]:
    """`_run()`의 승인 gate를 거치지 않고 `_apply_targets()`만 본다.

    상위 검사가 먼저 걸리면 transaction 안 marker 판정이 한 번도 실행되지 않아,
    그 방어를 지워도 아무 테스트가 깨지지 않는다.
    """

    state = _StatefulTargets()
    state.final.update(already_final)
    state.install(monkeypatch)
    root = Path(tempfile.mkdtemp(prefix="v5cm26-"))
    for database, payload in markers.items():
        (root / transition.marker_name(database)).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    approved = {
        d: transition.target_fingerprint(
            _final(d) if d in already_final else _inventory(d)
        )
        for d in transition.ORDERED_TARGETS
    }
    # 보존 projection·외부 FK는 전환 전후로 같다. approval은 전환 **전** 값을 담는다.
    pre = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    ledger = cli.ConnectionLedger()
    cli._apply_targets(
        _args(backup_root=root),
        _approval(
            target_fingerprint_sha256_by_target=approved,
            preserved_projection_sha256_by_target={
                d: transition.preserved_projection_sha256(pre[d])
                for d in transition.ORDERED_TARGETS
            },
            external_fk_projection_sha256_by_target={
                d: transition.external_fk_projection_sha256(pre[d])
                for d in transition.ORDERED_TARGETS
            },
        ),
        {
            d: (_final(d) if d in already_final else _inventory(d))
            for d in transition.ORDERED_TARGETS
        },
        ledger,
        _inspector([]),
        _mutator([]),
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    return state, ledger


def test_final_target_without_a_marker_is_recovered_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker가 없어도 approval의 전환 전 불변량과 맞으면 복구한다.

    거부하면 `commit 성공 → marker 기록 전 중단`에서 영영 재개할 수 없다
    (구현리뷰 8차 필수 1).
    """

    state, ledger = _apply_with_markers(monkeypatch, transition.ORDERED_TARGETS, {})
    assert state.handled == []
    assert ledger.mutating == list(transition.ORDERED_TARGETS)


def test_final_target_whose_preserved_projection_drifted_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전환이 건드리지 않는 값이 달라졌다면 그 approval이 승인한 target이 아니다."""

    approval = _approval(
        target_fingerprint_sha256_by_target={
            d: transition.target_fingerprint(_final(d))
            for d in transition.ORDERED_TARGETS
        },
        preserved_projection_sha256_by_target=dict.fromkeys(
            transition.ORDERED_TARGETS, "9" * 64
        ),
    )
    state = _StatefulTargets()
    state.final.update(transition.ORDERED_TARGETS)
    state.install(monkeypatch)
    ledger = cli.ConnectionLedger()
    with pytest.raises(transition.TransitionError) as caught:
        cli._apply_targets(
            _args(backup_root=Path(tempfile.mkdtemp(prefix="v5cm26-"))),
            approval,
            {d: _final(d) for d in transition.ORDERED_TARGETS},
            ledger,
            _inspector([]),
            _mutator([]),
            state.handler,
            _evidence(),
            root_trust="0700",
            approval_sha256="a" * 64,
        )
    assert caught.value.reason_code == "APPROVAL_MISMATCH"


def test_final_target_with_a_mismatched_marker_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marker가 지금 그 target을 가리키지 않으면 no-op을 인정하지 않는다."""

    first = transition.ORDERED_TARGETS[0]
    forged = _marker_for(first, target_fingerprint_sha256="9" * 64)
    with pytest.raises(transition.TransitionError) as caught:
        _apply_with_markers(monkeypatch, transition.ORDERED_TARGETS, {first: forged})
    assert caught.value.reason_code == "BACKUP_INVALID"


def test_legacy_target_carrying_a_committed_marker_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy인데 COMMITTED marker가 있으면 둘 중 하나가 거짓이다."""

    first = transition.ORDERED_TARGETS[0]
    with pytest.raises(cli.TransitionAbort) as caught:
        _apply_with_markers(monkeypatch, [], {first: _marker_for(first)})
    assert caught.value.reason_code == "APPROVAL_MISMATCH"


def test_all_final_with_valid_markers_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전제 확인 — marker가 다 맞으면 handler 0회로 완주한다."""

    state, ledger = _apply_with_markers(
        monkeypatch,
        transition.ORDERED_TARGETS,
        {d: _marker_for(d) for d in transition.ORDERED_TARGETS},
    )
    assert state.handled == []
    assert ledger.mutating == list(transition.ORDERED_TARGETS)


# ---------------------------------------------------------------------------
# 구현리뷰 8차 필수 3 — sidecar·receipt producer
# ---------------------------------------------------------------------------


def test_sidecar_producer_output_passes_its_own_validator() -> None:
    """producer가 만든 것이 validator를 통과해야 계약이 닫힌다.

    지금까지는 validator만 있고 producer가 없어 테스트 fixture가 그 자리를 대신했다
    (구현리뷰 8차 필수 3·편집 1).
    """

    for database in transition.ORDERED_TARGETS:
        for inventory, state in (
            (_inventory(database), transition.BaseState.BASE_LEGACY_EPOCH),
            (_final(database), transition.BaseState.FINAL_ADOPTED),
        ):
            payload = transition.build_sidecar(
                inventory,
                state=state,
                change_ref="GH-104",
                preflight_bundle_sha256="0" * 64,
            )
            transition.validate_sidecar_schema(payload)
            transition.assert_sidecar_matches(
                payload,
                database=inventory.database,
                profile=inventory.profile,
                state=state,
                view_sha256=inventory.view_sha256,
                view_owner=inventory.view_owner,
                view_acl=inventory.view_acl,
                view_comment=inventory.view_comment,
                change_ref="GH-104",
                preflight_bundle_sha256="0" * 64,
            )


def test_receipt_producer_output_passes_its_own_validator() -> None:
    import postgres_backup

    inventory = _inventory("kosa_agent")
    payload = transition.build_receipt(
        inventory,
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        archive_sha256="a" * 64,
        view_sidecar_sha256="b" * 64,
        restore_verified=True,
        backup_image_digest=postgres_backup.expected_client_image(16),
        backup_tool_version=postgres_backup.expected_client_version(16),
    )
    transition.validate_receipt(payload)
    transition.assert_receipt_matches(
        payload,
        inventory=inventory,
        change_ref="GH-104",
        preflight_bundle_sha256="0" * 64,
        archive_sha256="a" * 64,
        view_sidecar_sha256="b" * 64,
    )


def test_produced_artifacts_drive_the_whole_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fixture가 아니라 **producer가 만든** 증적으로 `_run()`이 통과한다."""

    import postgres_backup

    state = _mixed_state(monkeypatch, [])
    pre = {d: _inventory(d) for d in transition.ORDERED_TARGETS}
    report = cli.preflight_report(pre)
    expected = cli.expected_approval(report, pre, "GH-104")
    payload = _approval()
    for key in transition.APPROVAL_EXPECTED_KEYS:
        payload[key] = expected[key]
    approval = _write(tmp_path, payload)
    bundle = payload["preflight_bundle_sha256"]

    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    argv: list[str] = []
    for database in transition.ORDERED_TARGETS:
        archive = root / transition.archive_name(database, "GH-104")
        archive.write_bytes(f"dump-of-{database}".encode())
        sidecar_path = root / transition.view_sidecar_name(database, "GH-104")
        sidecar_digest = postgres_backup.atomic_write_json(
            sidecar_path,
            transition.build_sidecar(
                pre[database],
                state=transition.BaseState.BASE_LEGACY_EPOCH,
                change_ref="GH-104",
                preflight_bundle_sha256=bundle,
            ),
            trusted_root=root,
        )
        receipt = transition.build_receipt(
            pre[database],
            change_ref="GH-104",
            preflight_bundle_sha256=bundle,
            archive_sha256=postgres_backup.archive_digest(archive),
            view_sidecar_sha256=sidecar_digest,
            restore_verified=True,
            backup_image_digest=postgres_backup.expected_client_image(16),
            backup_tool_version=postgres_backup.expected_client_version(16),
        )
        argv += ["--receipt", str(_write(tmp_path, receipt, f"receipt_{database}"))]
        _complete(root, database)

    code, _ledger = _invoke(approval, root, argv, state.handler)
    assert code == cli.EXIT_OK
    assert state.handled == list(transition.ORDERED_TARGETS)


def test_transition_statements_are_fixed_in_order() -> None:
    """`DROP VIEW → 4× ALTER(정렬) → compat View`. 순서가 바뀌면 첫 ALTER가 막힌다."""

    legacy = (
        "SELECT a.wafer AS wafer_no, h.wafer_no AS x FROM a JOIN h "
        "ON h.wafer_no = a.wafer WHERE a.wafer AS wafer_no IS NOT NULL "
        "AND h.wafer_no = a.wafer"
    )
    statements = transition.transition_statements(legacy)
    assert statements[0] == transition.DROP_VIEW_SQL
    alters = statements[1:-1]
    names = [s.split("public.")[1].split(" ")[0] for s in alters]
    assert names == sorted(transition.WAFER_ALTER_TABLES)
    assert all(transition.FINAL_WAFER_DDL_TYPE in s for s in alters)
    assert statements[-1].startswith(f"CREATE VIEW public.{transition.LEGACY_VIEW}")
    # 데이터 교체는 이 경로가 만들지 않는다.
    assert not [
        s
        for s in statements
        if s.split()[0] in {"INSERT", "UPDATE", "DELETE", "COPY", "TRUNCATE"}
    ]


def test_mutex_is_released_on_every_exit_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """성공·handler 실패·중단 어느 경로에서도 mutex가 남지 않는다.

    해제를 빼면 pool 안에 session advisory lock이 남는다(구현리뷰 8차 필수 2).
    """

    for label, handler in (
        ("성공", None),
        ("실패", lambda _c, _d, _i: (_ for _ in ()).throw(RuntimeError("boom"))),
    ):
        state = _stub_inventories(monkeypatch)
        log: list[str] = []
        try:
            cli._apply_targets(
                _args(),
                _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
                {d: _inventory(d) for d in transition.ORDERED_TARGETS},
                cli.ConnectionLedger(),
                _inspector([]),
                _mutator([], log),
                handler or state.handler,
                _evidence(),
                root_trust="0700",
                approval_sha256="a" * 64,
            )
        except RuntimeError:
            pass
        locks = [s for s in log if "pg_advisory_lock(" in s]
        unlocks = [s for s in log if "pg_advisory_unlock" in s]
        assert locks, label
        assert len(unlocks) == len(locks), label


def test_remaining_budget_shortens_the_statement_timeout() -> None:
    """남은 예산만큼으로 다시 걸지 않으면 단일 statement가 600초 lock을 쥔다."""

    connection = _FakeConnection()
    remaining = transition.apply_remaining_budget(connection, 100.0, now=120.0)
    assert remaining == pytest.approx(transition.TRANSACTION_BUDGET_SECONDS - 20.0)
    applied = [s for s in connection.log if "statement_timeout" in s]
    assert applied == [f"SET LOCAL statement_timeout = '{int(remaining * 1000)}ms'"]
    # 남은 예산이 줄면 timeout도 함께 줄어든다.
    later = _FakeConnection()
    transition.apply_remaining_budget(later, 100.0, now=200.0)
    first = int(applied[0].split("'")[1].rstrip("ms"))
    second = int(
        [s for s in later.log if "statement_timeout" in s][0].split("'")[1].rstrip("ms")
    )
    assert second < first


def test_apply_shortens_the_timeout_before_each_blocking_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler 앞에서 남은 예산을 다시 건다.

    stub 수집은 SQL을 보내지 않으므로 여기서 세는 것은 handler 앞 1회뿐이다. 수집
    query마다 다시 거는 것은 `BudgetClock`이 `read_inventory()` 안에서 한다.
    """

    state = _stub_inventories(monkeypatch)
    log: list[str] = []
    cli._apply_targets(
        _args(),
        _approval(target_fingerprint_sha256_by_target=_target_fingerprints()),
        {d: _inventory(d) for d in transition.ORDERED_TARGETS},
        cli.ConnectionLedger(),
        _inspector([]),
        _mutator([], log),
        state.handler,
        _evidence(),
        root_trust="0700",
        approval_sha256="a" * 64,
    )
    shortened = [s for s in log if "SET LOCAL statement_timeout" in s and "ms'" in s]
    assert len(shortened) == len(transition.ORDERED_TARGETS)


def test_budget_clock_uses_an_absolute_deadline() -> None:
    """phase 앞에서만 걸면 그 안의 여러 statement가 같은 남은 시간을 새로 쓴다."""

    clock = transition.BudgetClock(100.0, budget=60.0)
    assert clock.remaining(now=100.0) == pytest.approx(60.0)
    assert clock.remaining(now=130.0) == pytest.approx(30.0)
    with pytest.raises(transition.TransitionError) as caught:
        clock.remaining(now=160.0)
    assert caught.value.reason_code == "TARGET_BUSY"

    connection = _FakeConnection()
    clock.apply(connection, now=100.0)
    clock.apply(connection, now=130.0)
    values = [
        int(s.split("'")[1].rstrip("ms"))
        for s in connection.log
        if "statement_timeout" in s
    ]
    assert values == [60_000, 30_000]


def test_collection_reapplies_the_deadline_before_reading() -> None:
    """수집 직전에 남은 예산으로 timeout을 다시 건다."""

    import time as _time

    connection = _FakeConnection()
    with pytest.raises(IndexError):
        # stub은 catalog query에 빈 결과를 주므로 수집 자체는 끝까지 가지 않는다.
        # 여기서 보는 것은 그 **전에** deadline이 적용됐는가다.
        transition.read_inventory(
            connection,
            database="kosa_agent",
            profile="runtime",
            require_snapshot=True,
            clock=transition.BudgetClock(_time.monotonic()),
        )
    assert [s for s in connection.log if "statement_timeout" in s]


def test_collection_stops_when_the_deadline_has_passed() -> None:
    """예산이 남지 않았으면 query를 보내기 전에 멈춘다."""

    connection = _FakeConnection()
    with pytest.raises(transition.TransitionError) as caught:
        transition.read_inventory(
            connection,
            database="kosa_agent",
            profile="runtime",
            require_snapshot=True,
            clock=transition.BudgetClock(0.0),
        )
    assert caught.value.reason_code == "TARGET_BUSY"
    assert not [s for s in connection.log if "statement_timeout" in s]


def _alter_names(legacy: str) -> list[str]:
    return [
        s.split("public.")[1].split(" ")[0]
        for s in transition.transition_statements(legacy)
        if s.startswith("ALTER TABLE")
    ]


LEGACY_VIEW_BODY = (
    "SELECT a.wafer AS wafer_no FROM a JOIN h ON h.wafer_no = a.wafer "
    "WHERE a.wafer AS wafer_no IS NOT NULL AND h.wafer_no = a.wafer"
)


def test_alter_statements_are_emitted_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """고정 순서가 아니면 두 실행이 서로를 기다리는 교착이 가능하다.

    상수가 이미 정렬돼 있어서, 선언 순서를 그대로 쓰는 구현과 정렬하는 구현을 평소에는
    구분할 수 없다. **일부러 뒤집은 상수**로 정렬이 실제로 일하는지 본다.
    """

    assert _alter_names(LEGACY_VIEW_BODY) == sorted(transition.WAFER_ALTER_TABLES)

    monkeypatch.setattr(
        transition,
        "WAFER_ALTER_TABLES",
        tuple(reversed(transition.WAFER_ALTER_TABLES)),
    )
    names = _alter_names(LEGACY_VIEW_BODY)
    assert names == sorted(names)
    assert len(names) == len(set(names)) == 4


def test_wafer_ddl_type_matches_the_pinned_catalog() -> None:
    """DDL 길이가 틀리면 최종 catalog hash와 어긋난다.

    `information_schema.data_type`은 길이를 담지 않으므로, DDL 상수와 pin된 길이를
    따로 대조해야 한다(구현리뷰 8차 필수 3).
    """

    assert transition.FINAL_WAFER_DDL_TYPE == (
        f"varchar({transition.FINAL_WAFER_MAX_LENGTH})"
    )
    assert transition.FINAL_WAFER_MAX_LENGTH == 24
    detail = _final("kosa_agent").column_details["evaluation"]["wafer"]
    assert detail["data_type"] == transition.FINAL_WAFER_TYPE


def test_recovery_only_applies_to_targets_approval_planned_to_transition() -> None:
    """approval이 이미 final로 계획한 target은 복구 대상이 아니다."""

    approval = _approval(
        planned_outcome_by_target=dict.fromkeys(
            transition.ORDERED_TARGETS,
            transition.BaseState.FINAL_ADOPTED.value,
        )
    )
    with pytest.raises(cli.TransitionAbort) as caught:
        cli.reconstruct_preflight_entry(approval, transition.ORDERED_TARGETS[0])
    assert caught.value.reason_code == "APPROVAL_MISMATCH"


# ---------------------------------------------------------------------------
# 구현리뷰 9차 필수 1·2 — 복구 marker의 pre-state, mutex cleanup guard
# ---------------------------------------------------------------------------


def test_recovery_marker_records_the_pre_state_not_the_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """복구가 만든 marker로 **그 다음** 실행도 통과해야 한다.

    현재(final) inventory로 entry를 기록하면 `state`가 `FINAL_ADOPTED`가 되고, 다음
    실행이 그 marker로 bundle을 다시 만들 때 approval과 어긋난다(구현리뷰 9차 필수 1).
    그래서 crash → 복구 → 3차 → 4차까지 실제로 `_run()`을 돌린다.
    """

    state = _mixed_state(monkeypatch, [])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)

    real = cli._write_marker
    calls = {"n": 0}

    def crash(path: Path, payload: Any, backup_root: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("marker 기록 직전 중단")
        real(path, payload, backup_root)

    monkeypatch.setattr(cli, "_write_marker", crash)
    with pytest.raises(RuntimeError):
        _invoke(approval, root, receipts, state.handler)
    monkeypatch.setattr(cli, "_write_marker", real)

    # 2차 — 복구 실행
    state.handled.clear()
    assert _invoke(approval, root, receipts, state.handler)[0] == cli.EXIT_OK
    first = transition.ORDERED_TARGETS[0]
    recovered = json.loads(
        (root / transition.marker_name(first)).read_text(encoding="utf-8")
    )
    assert recovered["preflight_target_entry"]["state"] == (
        transition.BaseState.BASE_LEGACY_EPOCH.value
    )

    # 3차·4차 — 같은 증적으로 exact no-op
    settled = _tree(root)
    for _ in range(2):
        state.handled.clear()
        assert _invoke(approval, root, receipts, state.handler)[0] == cli.EXIT_OK
        assert state.handled == []
        assert _tree(root) == settled


def test_marker_schema_rejects_a_non_legacy_view_in_the_pre_state_entry() -> None:
    """entry의 View도 전환 전 값이어야 한다.

    상태만 legacy로 적고 View를 compat으로 두면 bundle 재계산이 어긋난다.
    """

    payload = _marker_for(transition.ORDERED_TARGETS[0])
    entry = dict(payload["preflight_target_entry"])
    entry["legacy_view_sha256"] = transition.COMPAT_VIEW_SHA256
    payload["preflight_target_entry"] = entry
    with pytest.raises(transition.TransitionError) as caught:
        transition.validate_marker_schema(payload)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_marker_schema_rejects_a_final_pre_state_entry() -> None:
    """approval이 legacy 전환을 승인했으므로 marker의 전환 전 상태는 항상 legacy다."""

    payload = _marker_for(transition.ORDERED_TARGETS[0])
    entry = dict(payload["preflight_target_entry"])
    entry["state"] = transition.BaseState.FINAL_ADOPTED.value
    payload["preflight_target_entry"] = entry
    with pytest.raises(transition.TransitionError) as caught:
        transition.validate_marker_schema(payload)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_mutex_is_released_when_the_first_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mutex는 잡혔는데 첫 commit이 실패하면 `finally`에 못 들어간다.

    guard를 획득 **직후**부터 켜지 않으면 pool 안에 lock이 남는다(구현리뷰 9차 필수 2).
    """

    from sqlalchemy.exc import OperationalError

    log: list[str] = []

    class _Connection(_FakeConnection):
        def commit(self) -> None:
            self.log.append("COMMIT")
            raise OperationalError("commit", {}, Exception("boom"))

    @contextlib.contextmanager
    def mutator(database: str) -> Iterator[Any]:
        yield _Connection(log)

    with pytest.raises(OperationalError):
        with cli._target_session(mutator, "kosa_agent"):
            pass
    assert [s for s in log if "pg_advisory_lock(" in s]
    assert [s for s in log if "pg_advisory_unlock" in s], "해제를 시도하지 않았다"


def test_invalidate_falls_back_when_the_primary_cleanup_fails() -> None:
    """`invalidate()`가 없거나 실패해도 pool 반환으로 이어지지 않는다."""

    class _NoInvalidate:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    connection = _NoInvalidate()
    transition._invalidate(connection)
    assert connection.closed is True

    class _AllFail:
        def invalidate(self) -> None:
            raise RuntimeError("x")

        def close(self) -> None:
            raise RuntimeError("x")

    with pytest.raises(transition.TransitionError) as caught:
        transition._invalidate(_AllFail())
    assert caught.value.reason_code == "TARGET_MUTEX_LEAKED"


# ---------------------------------------------------------------------------
# 구현리뷰 13차 필수 2 — apply gate가 completion marker를 요구한다
# ---------------------------------------------------------------------------


def _apply_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: Any = None
) -> Any:
    """증적을 갖춰 `_run()`을 부르고, `tamper`로 marker를 흔든다."""

    state = _mixed_state(monkeypatch, [])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)
    if tamper is not None:
        tamper(root)
    with pytest.raises(cli.TransitionAbort) as caught:
        _invoke(approval, root, receipts, state.handler)
    assert state.handled == []
    return caught.value


def test_apply_requires_a_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """producer가 "marker가 있어야만 정상 증적"이라 정했으니 소비자도 그것만 받는다.

    소비자가 marker를 읽지 않으면 그 계약이 apply gate로 이어지지 않는다
    (구현리뷰 13차 필수 2).
    """

    import backup_orchestrator

    def remove(root: Path) -> None:
        (
            root
            / backup_orchestrator.completion_name(
                transition.ORDERED_TARGETS[0], "GH-104"
            )
        ).unlink()

    error = _apply_with_evidence(tmp_path, monkeypatch, remove)
    assert error.reason_code == "BACKUP_REQUIRED"


@pytest.mark.parametrize(
    "field", ["archive_sha256", "view_sidecar_sha256", "receipt_sha256"]
)
def test_apply_rejects_a_marker_whose_digest_does_not_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """marker가 가리키는 실물이 지금 그 파일이 아니면 거부한다."""

    import backup_orchestrator

    def forge(root: Path) -> None:
        path = root / backup_orchestrator.completion_name(
            transition.ORDERED_TARGETS[0], "GH-104"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload[field] = "9" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")

    error = _apply_with_evidence(tmp_path, monkeypatch, forge)
    assert error.reason_code == "BACKUP_INVALID"


def test_apply_rejects_a_marker_from_another_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """다른 target의 marker를 이름만 바꿔 쓸 수 없다."""

    import backup_orchestrator

    def swap(root: Path) -> None:
        first, second = transition.ORDERED_TARGETS[:2]
        path = root / backup_orchestrator.completion_name(first, "GH-104")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["database"] = second
        path.write_text(json.dumps(payload), encoding="utf-8")

    error = _apply_with_evidence(tmp_path, monkeypatch, swap)
    assert error.reason_code == "BACKUP_INVALID"


# ---------------------------------------------------------------------------
# closure evidence — 구현리뷰 16차 필수 3
# ---------------------------------------------------------------------------


def _closure_root(tmp_path: Path) -> tuple[Path, Path]:
    """전환이 끝난 상태의 backup root를 만든다.

    **producer가 실제로 만드는 payload**를 쓴다. 가짜 payload로 positive를 채우면
    closure가 validator를 부르지 않아도 통과해, 무엇을 검증하는지 알 수 없다
    (구현리뷰 17차 필수 1).
    """

    import backup_orchestrator as orchestrator

    root = tmp_path / "outside"
    root.mkdir(mode=0o700)
    inventories = {
        database: _inventory(database) for database in transition.ORDERED_TARGETS
    }
    # bundle은 세 target의 **전환 전** entry에서 나온다. 상수를 박으면 closure의
    # 재구성 검증이 무엇을 확인하는지 알 수 없다(구현리뷰 18차 필수 1).
    entries = {
        database: cli.preflight_entry(inventories[database])
        for database in transition.ORDERED_TARGETS
    }
    bundle = cli.bundle_sha256(entries)
    approval = _approval(
        preflight_bundle_sha256=bundle,
        planned_outcome_by_target=dict.fromkeys(
            transition.ORDERED_TARGETS, transition.BaseState.FINAL_ADOPTED.value
        ),
        target_fingerprint_sha256_by_target={
            database: transition.target_fingerprint(inventories[database])
            for database in transition.ORDERED_TARGETS
        },
    )
    # marker는 그 mutation에 **쓴** approval을 봉인한다. 그러려면 approval 파일이 먼저
    # 있어야 한다(구현리뷰 18차 필수 1).
    approval_path = root / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    approval_digest = cli._file_sha256(approval_path)

    for database in transition.ORDERED_TARGETS:
        inventory = inventories[database]
        archive = root / transition.archive_name(database, "GH-104")
        archive.write_bytes(f"archive-{database}".encode())
        archive_sha = cli._file_sha256(archive)

        sidecar_path = root / transition.view_sidecar_name(database, "GH-104")
        sidecar_path.write_text(
            json.dumps(
                transition.build_sidecar(
                    inventory,
                    state=transition.BaseState.FINAL_ADOPTED,
                    change_ref="GH-104",
                    preflight_bundle_sha256=bundle,
                )
            ),
            encoding="utf-8",
        )
        sidecar_sha = cli._file_sha256(sidecar_path)

        receipt_path = root / orchestrator.receipt_name(database, "GH-104")
        receipt_path.write_text(
            json.dumps(
                transition.build_receipt(
                    inventory,
                    change_ref="GH-104",
                    preflight_bundle_sha256=bundle,
                    archive_sha256=archive_sha,
                    view_sidecar_sha256=sidecar_sha,
                    restore_verified=True,
                    backup_image_digest=transition_backup_image(16),
                    backup_tool_version=transition_backup_version(16),
                )
            ),
            encoding="utf-8",
        )
        receipt_sha = cli._file_sha256(receipt_path)

        (root / orchestrator.completion_name(database, "GH-104")).write_text(
            json.dumps(
                {
                    "artifact_type": orchestrator.COMPLETION_ARTIFACT_TYPE,
                    "dataset_epoch": transition.DATASET_EPOCH,
                    "database": database,
                    "change_ref": "GH-104",
                    "archive_sha256": archive_sha,
                    "view_sidecar_sha256": sidecar_sha,
                    "receipt_sha256": receipt_sha,
                }
            ),
            encoding="utf-8",
        )
        (root / transition.marker_name(database)).write_text(
            json.dumps(
                transition.build_marker(
                    inventory,
                    state=transition.BaseState.FINAL_ADOPTED,
                    change_ref="GH-104",
                    preflight_bundle_sha256=bundle,
                    archive_sha256=archive_sha,
                    view_sidecar_sha256=sidecar_sha,
                    recorded_at="2026-08-22T12:00:00+09:00",
                    backup_root_trust="0700",
                    approval_sha256=approval_digest,
                    preflight_target_entry=cli.preflight_entry(inventory),
                )
            ),
            encoding="utf-8",
        )
    return root, approval_path


def _record(root: Path, approval_path: Path, tmp_path: Path) -> Path:
    return cli.record_closure_evidence(
        approval_path=approval_path,
        backup_root=root,
        change_ref="GH-104",
        repository_root=tmp_path / "repo",
    )


def test_approval_schema_stays_exactly_eighteen_keys() -> None:
    """사전 승인은 전환 **전** 입력이다. 사후 값이 섞이면 승인이 아니게 된다.

    계획 §16.3이 사후 hash를 "approval에 함께" 넣으라고 했는데, 넣으면
    `validate_approval_schema()`가 extra key로 거부한다(구현리뷰 16차 필수 3).
    """

    assert len(transition.APPROVAL_KEYS) == 18
    assert transition.APPROVAL_KEYS & transition.CLOSURE_KEYS == {
        "artifact_type",
        "dataset_epoch",
        "change_ref",
        "ordered_targets",
    }
    payload = _approval()
    payload["archive_sha256_by_target"] = dict.fromkeys(
        transition.ORDERED_TARGETS, "a" * 64
    )
    with pytest.raises(transition.TransitionError) as caught:
        transition.validate_approval_schema(payload)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_closure_records_every_artifact_digest(tmp_path: Path) -> None:
    """소비자가 대조할 대상은 실물 5종 × 3 target과 approval이다."""

    import backup_orchestrator as orchestrator

    root, approval_path = _closure_root(tmp_path)
    path = _record(root, approval_path, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    transition.validate_closure_schema(payload)

    assert path.name == transition.closure_name("GH-104")
    assert payload["approval_sha256"] == cli._file_sha256(approval_path)
    assert payload["backup_root_mode"] == "0700"
    for database in transition.ORDERED_TARGETS:
        assert payload["archive_sha256_by_target"][database] == cli._file_sha256(
            root / transition.archive_name(database, "GH-104")
        )
        assert payload["receipt_sha256_by_target"][database] == cli._file_sha256(
            root / orchestrator.receipt_name(database, "GH-104")
        )
        assert payload["committed_marker_sha256_by_target"][database] == (
            cli._file_sha256(root / transition.marker_name(database))
        )


def test_closure_is_a_no_op_on_rerun(tmp_path: Path) -> None:
    """재실행이 기록을 다시 쓰면 팀에 제출된 hash가 조용히 바뀐다."""

    root, approval_path = _closure_root(tmp_path)
    path = _record(root, approval_path, tmp_path)
    before = path.read_bytes()
    assert _record(root, approval_path, tmp_path) == path
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "artifact",
    ["archive", "view_sidecar", "receipt", "completion", "marker"],
)
def test_missing_artifact_blocks_closure_but_keeps_the_db_result(
    tmp_path: Path, artifact: str
) -> None:
    """사후 증적이 없으면 closure만 막는다. **전환을 되돌리지 않는다.**

    전환은 이미 commit됐고 COMMITTED marker가 그것을 증명한다. 여기서 막히는 것은
    closure gate와 Git 게시뿐이다(구현리뷰 16차 필수 3).
    """

    import backup_orchestrator as orchestrator

    root, approval_path = _closure_root(tmp_path)
    database = transition.ORDERED_TARGETS[1]
    target = {
        "archive": transition.archive_name(database, "GH-104"),
        "view_sidecar": transition.view_sidecar_name(database, "GH-104"),
        "receipt": orchestrator.receipt_name(database, "GH-104"),
        "completion": orchestrator.completion_name(database, "GH-104"),
        "marker": transition.marker_name(database),
    }[artifact]
    (root / target).unlink()

    with pytest.raises(cli.TransitionAbort) as caught:
        _record(root, approval_path, tmp_path)
    assert caught.value.reason_code in {"CLOSURE_BLOCKED", "ARTIFACT_INVALID"}
    # 나머지 증적은 그대로다. 되돌린 것이 없다.
    for other in transition.ORDERED_TARGETS:
        if other != database:
            assert (root / transition.marker_name(other)).is_file()
    assert not (root / transition.closure_name("GH-104")).exists()


def test_tampered_archive_blocks_closure(tmp_path: Path) -> None:
    """marker가 고정한 digest와 실물이 다르면 사후에 바뀐 것이다."""

    root, approval_path = _closure_root(tmp_path)
    database = transition.ORDERED_TARGETS[0]
    (root / transition.archive_name(database, "GH-104")).write_bytes(b"tampered")
    with pytest.raises(cli.TransitionAbort) as caught:
        _record(root, approval_path, tmp_path)
    assert caught.value.reason_code == "CLOSURE_BLOCKED"


def test_closure_never_overwrites_a_different_record(tmp_path: Path) -> None:
    root, approval_path = _closure_root(tmp_path)
    path = _record(root, approval_path, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["approval_sha256"] = "9" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(cli.TransitionAbort) as caught:
        _record(root, approval_path, tmp_path)
    assert caught.value.reason_code == "CLOSURE_BLOCKED"
    assert json.loads(path.read_text(encoding="utf-8"))["approval_sha256"] == "9" * 64


def test_backup_root_that_others_can_write_blocks_closure(tmp_path: Path) -> None:
    """`0700`이 아니면 root 쓰기 권한자가 marker를 위조할 수 있다(권장 1)."""

    root, approval_path = _closure_root(tmp_path)
    root.chmod(0o755)
    try:
        with pytest.raises(cli.TransitionAbort) as caught:
            _record(root, approval_path, tmp_path)
        assert caught.value.reason_code == "BACKUP_ROOT_UNTRUSTED"
    finally:
        root.chmod(0o700)


def test_closure_payload_carries_no_secret(tmp_path: Path) -> None:
    """host·port·자격증명·절대경로가 팀 첨부로 새면 안 된다."""

    root, approval_path = _closure_root(tmp_path)
    body = _record(root, approval_path, tmp_path).read_text(encoding="utf-8")
    for forbidden in (str(root), str(tmp_path), "password", "5432", "://"):
        assert forbidden not in body


def test_closure_mode_opens_no_connection(tmp_path: Path) -> None:
    """closure는 파일만 본다. DB에 닿으면 approval 없이 공용을 여는 셈이다."""

    root, approval_path = _closure_root(tmp_path)

    def forbidden(database: str) -> Any:
        raise AssertionError("closure가 연결을 열었다")

    argv = [
        "--closure",
        "--approval",
        str(approval_path),
        "--backup-root",
        str(root),
        "--change-ref",
        "GH-104",
    ]
    assert cli._run(argv, inspector=forbidden, repository_root=tmp_path / "repo") == 0
    assert (root / transition.closure_name("GH-104")).is_file()


def _artifact_path(root: Path, artifact: str, database: str) -> Path:
    import backup_orchestrator as orchestrator

    return {
        "sidecar": root / transition.view_sidecar_name(database, "GH-104"),
        "receipt": root / orchestrator.receipt_name(database, "GH-104"),
        "completion": root / orchestrator.completion_name(database, "GH-104"),
        "marker": root / transition.marker_name(database),
    }[artifact]


@pytest.mark.parametrize(
    ("artifact", "field", "value"),
    [
        # marker — 전환됐다는 유일한 증거. identity와 bundle이 approval에 묶여야 한다.
        ("marker", "change_ref", "GH-999"),
        ("marker", "database", "kosa_text2sql"),
        ("marker", "profile", "evaluation"),
        ("marker", "preflight_bundle_sha256", "f" * 64),
        ("marker", "base_state", "BASE_LEGACY_EPOCH"),
        # 18차 필수 1이 재현한 두 필드. 이전엔 64-hex이기만 하면 통과했다.
        ("marker", "compatibility_view_sha256", "a" * 64),
        ("marker", "approval_sha256", "b" * 64),
        ("marker", "backup_root_trust", "0777"),
        # marker가 담은 **전환 전** 신원. approval이 승인한 값과 같아야 한다.
        # (marker의 최상위 `target_fingerprint_sha256`은 전환 **뒤** 값이라 다르다.)
        ("marker", "preflight_target_entry", "PRE_FINGERPRINT"),
        ("marker", "preflight_target_entry", "PRE_SERVER_MAJOR"),
        ("marker", "preflight_target_entry", "PRE_PRESERVED"),
        ("marker", "preflight_target_entry", "PRE_ACTION_ROWS"),
        ("marker", "archive_sha256", "d" * 64),
        ("marker", "view_sidecar_sha256", "c" * 64),
        # sidecar — 전환 대상 View 증적.
        ("sidecar", "change_ref", "GH-999"),
        ("sidecar", "database", "kosa_text2sql"),
        ("sidecar", "preflight_bundle_sha256", "f" * 64),
        # receipt — backup·독립 restore 증적.
        ("receipt", "change_ref", "GH-999"),
        ("receipt", "database", "kosa_text2sql"),
        ("receipt", "preflight_bundle_sha256", "f" * 64),
        ("receipt", "restore_verified", False),
        ("receipt", "archive_sha256", "d" * 64),
        ("receipt", "view_sidecar_sha256", "c" * 64),
        # completion — 세 파일이 다 게시됐다는 근거.
        ("completion", "change_ref", "GH-999"),
        ("completion", "database", "kosa_text2sql"),
        ("completion", "archive_sha256", "d" * 64),
        ("completion", "view_sidecar_sha256", "c" * 64),
        ("completion", "receipt_sha256", "b" * 64),
    ],
)
def test_a_single_field_change_blocks_closure(
    tmp_path: Path, artifact: str, field: str, value: Any
) -> None:
    """한 필드만 바꿔도 묶음이 깨진다.

    이전 구현은 marker의 archive·sidecar hash 두 개만 봤다. 임의 receipt와 다른 bundle을
    가진 approval이 모두 채택됐다(구현리뷰 17차 필수 1).
    """

    root, approval_path = _closure_root(tmp_path)
    # 첫 target만 바꾼다. 나머지 둘은 정상이라 "전부 깨져서 막혔다"가 아니다.
    path = _artifact_path(root, artifact, transition.ORDERED_TARGETS[0])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert field in payload, "없는 필드를 바꾸면 회귀가 아무것도 확인하지 않는다"
    if isinstance(value, str) and value.startswith("PRE_"):
        key, replacement = {
            "PRE_FINGERPRINT": ("target_fingerprint_sha256", "e" * 64),
            "PRE_SERVER_MAJOR": ("server_major", 15),
            "PRE_PRESERVED": ("preserved_projection_sha256", "e" * 64),
            "PRE_ACTION_ROWS": ("action_history_rows", 999),
        }[value]
        entry = dict(payload[field])
        assert entry[key] != replacement
        entry[key] = replacement
        payload[field] = entry
    else:
        assert payload[field] != value
        payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli.TransitionAbort) as caught:
        _record(root, approval_path, tmp_path)
    assert caught.value.reason_code == "CLOSURE_BLOCKED"
    assert not (root / transition.closure_name("GH-104")).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preflight_bundle_sha256", "f" * 64),
        ("change_ref", "GH-999"),
        # 18차 필수 1이 재현한 세 필드. 전환 뒤 approval을 바꿔도 통과했다.
        ("approved_at", "2026-01-01T00:00:00+09:00"),
        ("source_manifest_sha256", "d" * 64),
        ("gate0_inventory_sha256", "c" * 64),
    ],
)
def test_an_approval_that_does_not_match_the_run_blocks_closure(
    tmp_path: Path, field: str, value: str
) -> None:
    """다른 bundle의 approval이 그대로 봉인되면 closure는 아무것도 증명하지 않는다."""

    root, approval_path = _closure_root(tmp_path)
    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    payload[field] = value
    approval_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli.TransitionAbort) as caught:
        _record(root, approval_path, tmp_path)
    assert caught.value.reason_code in {"CLOSURE_BLOCKED", "APPROVAL_MISMATCH"}
    assert not (root / transition.closure_name("GH-104")).exists()


def test_closure_calls_the_producer_validators(tmp_path: Path) -> None:
    """schema를 안 보면 임의 JSON도 통과한다. 실제 경로에서 부르는지 확인한다."""

    seen: list[str] = []
    root, approval_path = _closure_root(tmp_path)

    import backup_orchestrator as orchestrator

    originals = (
        transition.validate_sidecar_schema,
        transition.validate_receipt,
        orchestrator.validate_completion,
    )

    def spy(name: str, original: Any) -> Any:
        def wrapped(payload: Any) -> None:
            seen.append(name)
            original(payload)

        return wrapped

    transition.validate_sidecar_schema = spy("sidecar", originals[0])
    transition.validate_receipt = spy("receipt", originals[1])
    orchestrator.validate_completion = spy("completion", originals[2])
    try:
        _record(root, approval_path, tmp_path)
    finally:
        (
            transition.validate_sidecar_schema,
            transition.validate_receipt,
            orchestrator.validate_completion,
        ) = originals
    assert seen.count("sidecar") == 3
    assert seen.count("receipt") == 3
    assert seen.count("completion") == 3


# ---------------------------------------------------------------------------
# 구현리뷰 18차 필수 2 — 재개 marker 신원은 남은 mutation **전에** 본다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # 이전 실행이 신뢰하지 못하는 root에 남긴 marker.
        ("backup_root_trust", "0777"),
        # 다른 approval로 전환한 marker.
        ("approval_sha256", "9" * 64),
    ],
)
def test_a_stale_marker_identity_stops_before_touching_later_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    """첫 target marker의 신원이 다르면 **뒤 두 legacy target을 건드리지 않는다.**

    이전에는 `completed_targets()`가 database·change·bundle·final만 봤다. `0777` root에
    남은 marker도 완료로 인정되고, 뒤 target을 mutation한 다음 closure에서야 불일치가
    드러났다(구현리뷰 18차 필수 2). 안전 gate는 사후 차단이 아니다.
    """

    first = transition.ORDERED_TARGETS[0]
    state = _mixed_state(monkeypatch, [first])
    approval, root, receipts = _prepare_artifacts(tmp_path, monkeypatch, state)

    marker_path = root / transition.marker_name(first)
    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    assert payload[field] != value
    payload[field] = value
    marker_path.write_text(json.dumps(payload), encoding="utf-8")

    def forbidden(connection: Any, database: str, inventory: Any) -> None:
        raise AssertionError(f"신원이 어긋나는데 {database}를 바꿨다")

    before = _tree(root)
    with pytest.raises(cli.TransitionAbort) as caught:
        _invoke(approval, root, receipts, forbidden)
    assert (
        caught.value.reason_code
        == {
            "backup_root_trust": "BACKUP_ROOT_UNTRUSTED",
            "approval_sha256": "APPROVAL_MISMATCH",
        }[field]
    )
    # 뒤 target marker를 쓴 적도, backup root를 건드린 적도 없다.
    assert _tree(root) == before
    for database in transition.ORDERED_TARGETS[1:]:
        assert not (root / transition.marker_name(database)).exists(), database


def test_fresh_resume_and_closure_share_one_identity_helper() -> None:
    """세 경로가 각자 검사하면 한 곳이 빠져도 나머지가 통과시킨다."""

    import inspect

    source = inspect.getsource(cli)
    # `completed_targets`(재개) · `assert_marker_matches`(final 재검증) · closure
    assert source.count("assert_marker_identity(") == 2
    assert "assert_marker_identity(" in inspect.getsource(
        transition.assert_marker_matches
    )


# ---------------------------------------------------------------------------
# 구현리뷰 19차 필수 1 — approval 객체와 digest는 같은 bytes에서 나온다
# ---------------------------------------------------------------------------


def test_approval_object_and_digest_come_from_one_read(tmp_path: Path) -> None:
    """읽는 도중 파일이 교체돼도 객체와 digest가 갈라지면 안 된다.

    `load → 같은 path 재읽기 hash`면 두 읽기 사이에 교체가 가능하다. mutation 판단에는
    A를 쓰면서 marker에는 B의 hash를 봉인하게 되고, 그러면 `approval_sha256`은 "실제로
    쓴 approval"을 증명하지 못한다(구현리뷰 19차 필수 1).
    """

    import hashlib

    path = tmp_path / "approval.json"
    first = _approval()
    path.write_text(json.dumps(first), encoding="utf-8")
    first_bytes = path.read_bytes()

    second = _approval(approved_at="2026-01-01T00:00:00+09:00")
    second_bytes = json.dumps(second).encode("utf-8")
    assert first_bytes != second_bytes

    real_read_bytes = Path.read_bytes
    swapped: list[int] = []

    def swapping(self: Path) -> bytes:
        data = real_read_bytes(self)
        if self == path and not swapped:
            swapped.append(1)
            # 읽은 **직후** 교체한다. 두 번 읽는 구현이면 여기서 갈라진다.
            real_write = Path.write_bytes
            real_write(self, second_bytes)
        return data

    Path.read_bytes = swapping  # type: ignore[method-assign]
    try:
        payload, digest = cli.read_approval(path)
    finally:
        Path.read_bytes = real_read_bytes  # type: ignore[method-assign]

    assert swapped == [1], "회귀가 교체를 못 했다"
    assert path.read_bytes() == second_bytes, "교체가 반영되지 않았다"
    # 객체와 digest는 **둘 다 A**여야 한다.
    assert payload["approved_at"] == first["approved_at"]
    assert digest == hashlib.sha256(first_bytes).hexdigest()


def test_apply_and_closure_use_the_same_approval_reader() -> None:
    """한쪽만 helper를 쓰면 다른 쪽에 같은 경쟁이 남는다."""

    import inspect

    source = inspect.getsource(cli)
    assert source.count("read_approval(") == 3  # 정의 1 + 호출 2
    # helper 뒤로 approval path를 다시 읽는 경로가 없어야 한다.
    assert "_file_sha256(Path(args.approval))" not in source
    assert "_file_sha256(approval_path)" not in source
    # `_file_sha256`은 **한 번만** 정의된다. 두 번 정의하면 뒤 정의가 앞을 덮는다.
    assert source.count("def _file_sha256(") == 1


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-json",
        b"[]",
        b'{"artifact_type": "wrong"}',
        # UTF-8이 아닌 bytes.
        b"\xff\xfe{}",
    ],
)
def test_a_broken_approval_is_refused_by_the_reader(
    tmp_path: Path, body: bytes
) -> None:
    path = tmp_path / "approval.json"
    path.write_bytes(body)
    with pytest.raises(cli.TransitionAbort) as caught:
        cli.read_approval(path)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_a_symlinked_approval_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps(_approval()), encoding="utf-8")
    link = tmp_path / "approval.json"
    link.symlink_to(real)
    with pytest.raises(cli.TransitionAbort) as caught:
        cli.read_approval(link)
    assert caught.value.reason_code == "ARTIFACT_INVALID"


def test_replacing_the_approval_after_apply_blocks_closure(tmp_path: Path) -> None:
    """전환 뒤 approval을 바꾸면 closure가 marker hash 불일치로 막는다."""

    root, approval_path = _closure_root(tmp_path)
    # 정상 closure가 되는 상태에서 시작한다.
    assert _record(root, approval_path, tmp_path).is_file()
    (root / transition.closure_name("GH-104")).unlink()

    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    # schema는 그대로 유효하지만 한 바이트가 다르다.
    payload["approved_at"] = "2026-01-01T00:00:00+09:00"
    approval_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli.TransitionAbort) as caught:
        _record(root, approval_path, tmp_path)
    assert caught.value.reason_code == "CLOSURE_BLOCKED"
    assert not (root / transition.closure_name("GH-104")).exists()
