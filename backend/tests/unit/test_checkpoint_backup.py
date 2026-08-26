"""`V5-CM-3.4` checkpoint 전용 backup 계약 회귀.

전환 backup을 쓰지 않기로 한 판단(`V5-CM-2.6` gate는 전환 **이전** 질문이고, 그 도구는
base 9만 담는다)이 코드에 그대로 남아 있는지 본다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import checkpoint_backup as cbackup  # noqa: E402
import postgres_backup as backup  # noqa: E402

#: apply의 선행 확인이 요구하는 **4축**. 하나만 보면 나머지 손상이 통과한다.
_SHAPE = {
    "runtime_contract_sha256": "1" * 64,
    "reference_physical_sha256": "2" * 64,
    "final_reference_sha256": "3" * 64,
    "inventory_sha256": "4" * 64,
    "security_sha256": "5" * 64,
}


def _receipt(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_type": cbackup.RECEIPT_ARTIFACT_TYPE,
        "format_version": cbackup.FORMAT_VERSION,
        "task_id": cbackup.TASK_ID,
        "dataset_epoch": "fdc_final_20260818",
        "database": "kosa_agent_e2e",
        "profile": "runtime",
        "change_reference": "GH-130",
        "server_major": 16,
        "client_major": 16,
        "backup_image_digest": backup.expected_client_image(16),
        "backup_tool_version": backup.expected_client_version(16),
        "archive_sha256": "a" * 64,
        "target_host_fingerprint": "b" * 64,
        "predecessor_stage": "runtime_guarded",
        "source_projection": dict(_SHAPE),
        "restored_projection": dict(_SHAPE),
        "restore_verified": True,
        "created_at": "2026-08-25T00:00:00Z",
    }
    payload.update(overrides)
    return payload


class TestDumpCoversTheWholeDatabase:
    """**base 9만 담는 전환 backup과 다른 계약**이라는 것을 고정한다.

    checkpoint가 되돌려야 하는 것은 `runtime_guarded` 형상 전체다. table filter가
    다시 들어오면 복구 수단이 조용히 좁아진다.
    """

    def test_the_dump_has_no_table_filter(self) -> None:
        argv = cbackup.dump_argv(database="kosa_agent_e2e", out_path="/backups/x.dump")
        assert not any(part.startswith("--table") for part in argv)
        assert "--format=custom" in argv

    def test_the_transition_dump_is_narrower(self) -> None:
        """전환 backup은 base 9만 담는다 — 이 차이가 새 경로의 이유다."""

        assert set(backup.BACKUP_TABLES) == {
            "action_history",
            "dim_parameter",
            "evaluation",
            "fdc_trace",
            "lot_history",
            "metrology",
            "summary_alarm_history",
            "summary_data",
            "trace_alarm_history",
        }

    @pytest.mark.parametrize("database", ["kosa agent", "kosa;drop", ""])
    def test_a_bad_identifier_is_refused(self, database: str) -> None:
        with pytest.raises(cbackup.CheckpointBackupError):
            cbackup.dump_argv(database=database, out_path="/backups/x.dump")


class TestRestoreVerifiedIsObserved:
    """`restore_verified`는 주장이 아니라 **관측 결과**다."""

    def test_a_consistent_receipt_passes(self) -> None:
        cbackup.validate_receipt(_receipt(), "kosa_agent_e2e")

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            ({"restore_verified": False}, "RESTORE_NOT_VERIFIED"),
            # 복원본이 원본과 다르면 True라고 적어도 통과하지 못한다 — **4축 각각**.
            *(
                (
                    {"restored_projection": {**_SHAPE, axis: "9" * 64}},
                    "RESTORE_NOT_VERIFIED",
                )
                for axis in _SHAPE
            ),
            # 축 하나가 빠지면 형식 위반이다.
            (
                {"restored_projection": {k: v for k, v in list(_SHAPE.items())[:3]}},
                "BACKUP_INVALID",
            ),
            ({"format_version": 99}, "BACKUP_INVALID"),
            ({"format_version": True}, "BACKUP_INVALID"),
            ({"artifact_type": "other"}, "BACKUP_INVALID"),
            ({"task_id": "V5-CM-9.9"}, "BACKUP_MISMATCH"),
            ({"dataset_epoch": "kosa_0813"}, "BACKUP_MISMATCH"),
            ({"archive_sha256": "nope"}, "BACKUP_INVALID"),
            # --- 10차 권장 1 — strict schema라면 나머지도 닫는다 ---------------
            ({"profile": "evaluation"}, "BACKUP_MISMATCH"),
            ({"predecessor_stage": "runtime_clean"}, "BACKUP_MISMATCH"),
            ({"change_reference": "gh-130"}, "BACKUP_INVALID"),
            ({"server_major": 0}, "BACKUP_INVALID"),
            ({"client_major": True}, "BACKUP_INVALID"),
            # major가 다른 client로 뜬 덤프는 복원 호환을 보장하지 않는다.
            ({"client_major": 15}, "BACKUP_MISMATCH"),
            # **pin되지 않은 image** — 이전에는 `"img"`도 통과했다.
            ({"backup_image_digest": "img"}, "BACKUP_MISMATCH"),
            # 비어 있지 않은지만 보면 아무 문자열이나 통과한다.
            ({"backup_tool_version": "anything-at-all"}, "BACKUP_MISMATCH"),
            # 지원하지 않는 major는 하위 예외가 아니라 자기 예외로 끝난다.
            (
                {"server_major": 99, "client_major": 99},
                "BACKUP_MISMATCH",
            ),
            ({"created_at": "2026-08-25T00:00:00"}, "BACKUP_INVALID"),
        ],
    )
    def test_an_inconsistent_receipt_is_refused(
        self, override: dict[str, Any], reason: str
    ) -> None:
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.validate_receipt(_receipt(**override), "kosa_agent_e2e")
        assert exc.value.reason_code == reason

    def test_run_backup_derives_the_flag_from_observation(self) -> None:
        """`run_backup()`이 두 형상을 **비교해서** flag를 만든다."""

        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(cbackup.run_backup)))
        assigned = [
            ast.unparse(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key, node.value in zip(node.keys, node.values, strict=False)
            if isinstance(key, ast.Constant) and key.value == "restore_verified"
        ]
        assert assigned, "restore_verified를 만드는 자리가 없다"
        # 상수 True를 그대로 적으면 관측이 아니라 주장이다.
        assert all(value not in {"True", "False"} for value in assigned)


class TestEvidenceIsRecomputed:
    """증적을 **다시 계산해** 확인한다 — 파일을 읽고 믿지 않는다."""

    @staticmethod
    def _write(root: Path, **overrides: Any) -> None:
        receipt = _receipt(**overrides)
        db, ref = "kosa_agent_e2e", "GH-130"
        archive = root / cbackup.archive_name(db, ref)
        archive.write_bytes(b"payload")
        receipt["archive_sha256"] = backup.archive_digest(archive)
        receipt_path = root / cbackup.receipt_name(db, ref)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        (root / cbackup.completion_name(db, ref)).write_text(
            json.dumps(
                {
                    "artifact_type": cbackup.COMPLETION_ARTIFACT_TYPE,
                    "format_version": cbackup.FORMAT_VERSION,
                    "dataset_epoch": receipt["dataset_epoch"],
                    "database": db,
                    "change_reference": ref,
                    "archive_sha256": receipt["archive_sha256"],
                    "receipt_sha256": backup.archive_digest(receipt_path),
                }
            ),
            encoding="utf-8",
        )

    def test_a_complete_set_loads(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        receipt = cbackup.load_evidence(
            "kosa_agent_e2e", "GH-130", backup_root=tmp_path
        )
        assert receipt["restore_verified"] is True

    def test_a_tampered_archive_is_refused(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        (tmp_path / cbackup.archive_name("kosa_agent_e2e", "GH-130")).write_bytes(
            b"tampered"
        )
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.load_evidence("kosa_agent_e2e", "GH-130", backup_root=tmp_path)
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_a_tampered_receipt_is_refused(self, tmp_path: Path) -> None:
        """completion의 receipt digest가 실물과 어긋나면 막힌다."""

        self._write(tmp_path)
        path = tmp_path / cbackup.receipt_name("kosa_agent_e2e", "GH-130")
        payload = json.loads(path.read_text())
        payload["created_at"] = "2026-01-01T00:00:00Z"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.load_evidence("kosa_agent_e2e", "GH-130", backup_root=tmp_path)
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_missing_files_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.load_evidence("kosa_agent_e2e", "GH-130", backup_root=tmp_path)
        assert exc.value.reason_code == "BACKUP_MISSING"


class TestNamesDoNotCollideWithTransition:
    """같은 디렉토리에 섞여도 소비자가 다른 계약을 잘못 읽지 않는다."""

    def test_the_suffixes_differ(self) -> None:
        import backup_orchestrator as orchestrator
        import postgres_transition as transition

        db, ref = "kosa_agent_e2e", "GH-130"
        checkpoint_names = {
            cbackup.archive_name(db, ref),
            cbackup.receipt_name(db, ref),
            cbackup.completion_name(db, ref),
        }
        transition_names = {
            transition.archive_name(db, ref),
            transition.view_sidecar_name(db, ref),
            orchestrator.receipt_name(db, ref),
            orchestrator.completion_name(db, ref),
        }
        assert not (checkpoint_names & transition_names)
        assert all("checkpoint" in name for name in checkpoint_names)


def test_the_cli_refuses_a_non_runtime_target() -> None:
    """평가 DB에는 checkpoint backup을 뜨지 않는다."""

    code = cbackup.main(
        [
            "--database",
            "kosa_text2sql",
            "--confirm-target",
            "kosa_text2sql",
            "--change-ref",
            "GH-130",
            "--backup-root",
            "/tmp/does-not-matter",
        ]
    )
    assert code == backup.EXIT_MISMATCH


class TestFailedRunLeavesNoResidue:
    """완결되지 못한 실행은 **자기 잔재를 치우고** 끝난다.

    completion 없이 archive만 남으면 증적이 아니라 잔재인데, 재실행은 "같은 이름의
    archive가 이미 있습니다"로 막힌다. 운영자가 손으로 지우기 전까지 그 change ref로는
    backup을 다시 뜰 수 없다.
    """

    def test_run_backup_removes_the_archive_on_failure(self) -> None:
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(cbackup.run_backup)))
        handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and "staging.unlink(missing_ok=True)" in ast.unparse(node)
        ]
        # **하나의 경계**로 묶었다. dump·restore·증적 발급이 모두 그 안이다.
        #
        # 초판은 handler를 정확히 2개로 고정해 `pg_dump` 실패 창을 **누락된 채로
        # 정상화**했다(구현리뷰 10차 필수 3). 개수가 아니라 "dump가 그 안에 있는가"를
        # 본다.
        assert len(handlers) == 1
        body = ast.unparse(tree)
        assert body.index("dump_argv(") < body.index("except BaseException")
        # 원인을 삼키지 않는다.
        assert all("raise" in ast.unparse(h) for h in handlers)

    def test_an_unverified_restore_is_refused_at_save(self, tmp_path: Path) -> None:
        """`restore_verified=False`는 증적으로 남지 않는다."""

        archive = tmp_path / cbackup.archive_name("kosa_agent_e2e", "GH-130")
        archive.write_bytes(b"x")
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.save_evidence(
                _receipt(restore_verified=False),
                backup_root=tmp_path,
                archive=archive,
            )
        assert exc.value.reason_code == "RESTORE_NOT_VERIFIED"
        assert not (
            tmp_path / cbackup.completion_name("kosa_agent_e2e", "GH-130")
        ).exists()


# ---------------------------------------------------------------------------
# 13차 필수 2 — predecessor archive는 checkpoint가 **없는** 상태에서만 뜬다
# ---------------------------------------------------------------------------


def _catalog(state: str) -> dict[str, Any]:
    """상태별 live catalog를 **계약에서** 만든다.

    `classify_state()`를 patch하지 않는다 — 판정을 대신 써 주면 무엇을 보는지가
    아니라 무엇을 적었는지를 검증하게 된다.
    """

    import checkpoint_contract as contract

    if state == "ABSENT":
        return {
            "tables": [],
            "columns": {},
            "indexes": {},
            "primary_keys": {},
            "versions": [],
            "acl": {"owners": {}, "public_grants": []},
            "expected_owner": "cm34_applier",
        }
    ready = {
        "tables": sorted(contract.CHECKPOINT_TABLES),
        "columns": {
            table: [dict(column) for column in columns]
            for table, columns in contract.EXPECTED_COLUMNS.items()
        },
        "indexes": {
            name: {
                "valid": True,
                "ready": True,
                "table": spec["table"],
                "definition": spec["definition"],
            }
            for name, spec in contract.EXPECTED_INDEX_DEFINITIONS.items()
        },
        "primary_keys": dict(contract.EXPECTED_PRIMARY_KEYS),
        "versions": list(contract.expected_versions()),
        "acl": {
            "owners": dict.fromkeys(contract.CHECKPOINT_TABLES, "cm34_applier"),
            "public_grants": [],
        },
        "expected_owner": "cm34_applier",
    }
    if state == "PARTIAL":
        ready["indexes"].pop("checkpoints_thread_id_idx")
    elif state == "DRIFT":
        ready["acl"]["public_grants"].append("checkpoints:SELECT")
    elif state != "READY":  # pragma: no cover - 오탈자 방지
        raise AssertionError(state)
    assert contract.classify_state(ready) == state
    return ready


class _Cursor:
    """catalog 질의는 patch된 `read_catalog()`가 답한다. 나머지만 흉내 낸다."""

    def __init__(self, database: str) -> None:
        self.database = database
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> None:
        self._last = sql

    def fetchone(self) -> dict[str, Any]:
        if "current_database" in self._last:
            return {"db": self.database, "schema_name": "public"}
        if "server_version_num" in self._last:
            return {"v": 160000}
        return {}


class _Connection:
    def __init__(self, database: str) -> None:
        self.cursor_ = _Cursor(database)
        self.closed = False

    def cursor(self) -> _Cursor:
        return self.cursor_

    def close(self) -> None:
        self.closed = True


class _Target:
    database = "kosa_agent_e2e"
    profile = "runtime"
    host = "db.invalid"
    port = 5432
    username = "cm34"
    password = "unused"


def _trusted_root(tmp_path: Path) -> Path:
    root = tmp_path / "backups"
    root.mkdir()
    root.chmod(0o700)
    return root


@pytest.fixture
def source_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """live catalog만 갈아 끼우고 lock·계보 판정은 **실물 함수**를 남긴다.

    ## marker와 identity를 명시적으로 고정한다

    초판은 `predecessor_identity`를 `{"stage": "guarded"}`로 두고 marker는 patch하지
    않았다. 그때는 저장소에 checkpoint marker가 **없어서** `load_marker()`가 `None`을
    돌려줬고, `READY`가 곧 `READY_UNMARKED`였다. 즉 회귀가 **저장소에 파일이 없다는
    사실**에 기대고 있었다.

    묶음 2가 그 marker 2본을 tracked로 발급하자 `load_marker()`가 실제 marker를
    돌려줬고, 자리표시자 identity를 소비하는 순간 `KeyError`가 났다. 테스트 환경을
    저장소 상태에 의존시키지 않는다.
    """

    import setup_checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "_acquire_session_lock", lambda cursor: None)
    monkeypatch.setattr(checkpoint, "_release_session_lock", lambda cursor: None)
    monkeypatch.setattr(
        checkpoint,
        "predecessor_identity",
        lambda target: {key: "a" * 64 for key in checkpoint.IDENTITY_BOUND_KEYS},
    )
    monkeypatch.setattr(
        checkpoint, "predecessor_postcheck", lambda cursor, identity, **kw: None
    )
    # 기본값은 **marker 없음**이다. `READY_MARKED`가 필요한 회귀는
    # `_install_marked_state()`가 이 patch를 덮어쓴다.
    monkeypatch.setattr(checkpoint, "load_marker", lambda *a, **k: None)

    def _install(state: str) -> None:
        monkeypatch.setattr(checkpoint, "read_catalog", lambda cursor: _catalog(state))

    return _install


@pytest.mark.parametrize("state", ["READY", "PARTIAL", "DRIFT"])
def test_run_backup_refuses_a_source_that_still_has_checkpoints(
    state: str, tmp_path: Path, source_state: Any
) -> None:
    """**checkpoint가 섞인 archive는 복구 수단이 아니다**(구현리뷰 13차 필수 2).

    5축 projection의 inventory·security는 checkpoint 4종을 의도적으로 제외하므로
    `READY`·`PARTIAL` DB를 떠도 source/restored가 같게 나와 `restore_verified=true`가
    찍힌다. 그 archive로 복구하면 4종을 지운 직후 restore가 다시 만들어
    `RECOVERY_INCOMPLETE`다.

    dump runner 호출과 파일 생성이 **둘 다 0건**인지 본다 — 거부가 늦으면 잔재가 남는다.
    """

    source_state(state)
    root = _trusted_root(tmp_path)
    calls: list[Any] = []

    def _runner(argv: Any, **kwargs: Any) -> Any:
        calls.append(argv)
        raise AssertionError("상태 Gate 전에 client를 부르면 안 된다")

    with pytest.raises(cbackup.CheckpointBackupError) as exc:
        cbackup.run_backup(
            _Target(),
            change_reference="GH-130",
            backup_root=root,
            connect=lambda target: _Connection(target.database),
            runner=_runner,
            lifecycle=None,
        )
    assert exc.value.reason_code == "SOURCE_STATE_INVALID"
    assert calls == []
    assert list(root.iterdir()) == []


def test_run_backup_reaches_the_client_when_the_source_is_absent(
    tmp_path: Path, source_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**양성 대조군.** Gate가 전부를 막는 것이 아니라 상태를 보는 것이다."""

    source_state("ABSENT")
    # 형상 관측은 실 DB가 필요하다. 여기서 보려는 것은 Gate 통과 여부뿐이다.
    monkeypatch.setattr(cbackup, "observe_shape", lambda cursor, **kw: dict(_SHAPE))
    monkeypatch.setattr(cbackup, "source_roles", lambda cursor: [])
    root = _trusted_root(tmp_path)
    calls: list[Any] = []

    def _runner(argv: Any, **kwargs: Any) -> Any:
        calls.append(argv)
        raise RuntimeError("여기서 멈춘다")

    with pytest.raises(RuntimeError):
        cbackup.run_backup(
            _Target(),
            change_reference="GH-130",
            backup_root=root,
            connect=lambda target: _Connection(target.database),
            runner=_runner,
            lifecycle=None,
        )
    assert calls, "ABSENT source는 client 호출까지 가야 한다"
    # 실패한 실행은 잔재를 남기지 않는다.
    assert list(root.iterdir()) == []


def test_run_backup_refuses_a_connection_to_another_database(
    tmp_path: Path, source_state: Any
) -> None:
    """DSN을 믿지 않는다 — **연결된 곳**이 대상인지 본다."""

    source_state("ABSENT")
    root = _trusted_root(tmp_path)
    calls: list[Any] = []

    with pytest.raises(Exception) as exc:
        cbackup.run_backup(
            _Target(),
            change_reference="GH-130",
            backup_root=root,
            connect=lambda target: _Connection("kosa_text2sql"),
            runner=lambda *a, **k: calls.append(a),
            lifecycle=None,
        )
    assert getattr(exc.value, "reason_code", None) == "IDENTITY_MISMATCH"
    assert calls == []
    assert list(root.iterdir()) == []


def test_the_source_gate_runs_before_the_dump_and_again_after() -> None:
    """상태 확인이 **덤프 양쪽**에 있고 같은 lock 안이다.

    한 번만 보면 확인과 덤프 사이에 apply가 끼어드는 TOCTOU가 열린다.
    """

    import ast
    import inspect
    import textwrap

    body = ast.unparse(
        ast.parse(textwrap.dedent(inspect.getsource(cbackup.run_backup)))
    )
    first = body.index("assert_backup_source")
    dump = body.index("dump_argv(")
    last = body.rindex("assert_backup_source")
    assert first < dump < last
    assert body.index("_acquire_session_lock") < first
    assert last < body.index("_release_session_lock")


# ---------------------------------------------------------------------------
# 13차 권장 1 — 복구 증적도 exact validator로 닫는다
# ---------------------------------------------------------------------------


def _recovery(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_type": cbackup.RECOVERY_ARTIFACT_TYPE,
        "format_version": cbackup.FORMAT_VERSION,
        "task_id": cbackup.TASK_ID,
        "dataset_epoch": "fdc_final_20260818",
        "database": "kosa_agent_e2e",
        "change_reference": "GH-130",
        "target_host_fingerprint": "b" * 64,
        "archive_sha256": "a" * 64,
        "backup_receipt_sha256": "c" * 64,
        "change_approval_sha256": "d" * 64,
        "status": "COMMITTED",
        "state_before": "PARTIAL",
        "recovered_projection": dict(_SHAPE),
        "started_at": "2026-08-25T00:00:00Z",
        "completed_at": "2026-08-25T00:05:00Z",
    }
    payload.update(overrides)
    return payload


class TestRecoveryReceiptIsValidated:
    """파괴 작업의 증적이므로 **값까지** 닫는다(구현리뷰 13차 권장 1)."""

    def test_a_committed_record_passes(self) -> None:
        cbackup.validate_recovery_receipt(_recovery(), "kosa_agent_e2e")

    def test_a_started_record_passes(self) -> None:
        cbackup.validate_recovery_receipt(
            _recovery(status="STARTED", recovered_projection=None, completed_at=None),
            "kosa_agent_e2e",
        )

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            ({"artifact_type": "other"}, "BACKUP_INVALID"),
            ({"format_version": 2}, "BACKUP_INVALID"),
            ({"format_version": True}, "BACKUP_INVALID"),
            ({"task_id": "V5-CM-9.9"}, "BACKUP_MISMATCH"),
            ({"dataset_epoch": "kosa_0813"}, "BACKUP_MISMATCH"),
            ({"status": "DONE"}, "BACKUP_INVALID"),
            # 되돌릴 대상이 아닌 상태를 증적이 주장하지 못한다.
            ({"state_before": "ABSENT"}, "BACKUP_INVALID"),
            ({"state_before": "READY_MARKED"}, "BACKUP_INVALID"),
            ({"archive_sha256": "nope"}, "BACKUP_INVALID"),
            ({"backup_receipt_sha256": ""}, "BACKUP_INVALID"),
            ({"change_approval_sha256": "e" * 63}, "BACKUP_INVALID"),
            ({"target_host_fingerprint": "zz"}, "BACKUP_INVALID"),
            ({"change_reference": "gh-130"}, "BACKUP_INVALID"),
            ({"started_at": "2026-08-25T00:00:00"}, "BACKUP_INVALID"),
            ({"completed_at": None}, "BACKUP_INVALID"),
            # **시각이 뒤집힌 증적**은 무엇이 먼저였는지 말하지 못한다.
            ({"completed_at": "2026-08-24T23:00:00Z"}, "BACKUP_INVALID"),
            ({"recovered_projection": None}, "BACKUP_INVALID"),
            (
                {"recovered_projection": {**_SHAPE, "security_sha256": "nope"}},
                "BACKUP_INVALID",
            ),
            (
                {"recovered_projection": {k: v for k, v in list(_SHAPE.items())[:2]}},
                "BACKUP_INVALID",
            ),
            # 끝나지 않은 복구가 결과를 주장하지 못한다.
            ({"status": "STARTED"}, "BACKUP_INVALID"),
            (
                {"status": "ABORTED", "recovered_projection": None},
                "BACKUP_INVALID",
            ),
        ],
    )
    def test_an_inconsistent_record_is_refused(
        self, override: dict[str, Any], reason: str
    ) -> None:
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.validate_recovery_receipt(_recovery(**override), "kosa_agent_e2e")
        assert exc.value.reason_code == reason

    @pytest.mark.parametrize("key", sorted(cbackup.RECOVERY_KEYS))
    def test_a_missing_key_is_refused(self, key: str) -> None:
        payload = _recovery()
        payload.pop(key)
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.validate_recovery_receipt(payload, "kosa_agent_e2e")
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_an_extra_key_is_refused(self) -> None:
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.validate_recovery_receipt(_recovery(extra="x"), "kosa_agent_e2e")
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_an_invalid_record_is_not_written(self, tmp_path: Path) -> None:
        """validator를 통과하지 못한 증적은 **파일이 되지 않는다.**"""

        with pytest.raises(cbackup.CheckpointBackupError):
            cbackup.save_recovery_receipt(
                _recovery(status="DONE"), backup_root=tmp_path
            )
        assert list(tmp_path.iterdir()) == []

    def test_the_record_is_one_atomic_file(self, tmp_path: Path) -> None:
        """completion 짝을 두지 않는다 — 같은 경로를 상태만 바꿔 교체한다."""

        started = cbackup.save_recovery_receipt(
            _recovery(status="STARTED", recovered_projection=None, completed_at=None),
            backup_root=tmp_path,
        )
        path = tmp_path / cbackup.recovery_receipt_name("kosa_agent_e2e", "GH-130")
        assert json.loads(path.read_text())["status"] == "STARTED"
        cbackup.save_recovery_receipt(
            {
                **started,
                "status": "COMMITTED",
                **{
                    "recovered_projection": dict(_SHAPE),
                    "completed_at": "2026-08-25T00:05:00Z",
                },
            },
            backup_root=tmp_path,
        )
        assert json.loads(path.read_text())["status"] == "COMMITTED"
        assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_the_recovery_marks_started_before_it_drops_anything() -> None:
    """물리 복구 **전에** 증적을 남긴다(구현리뷰 13차 권장 1).

    DB 복구가 끝난 뒤 증적 쓰기만 실패하면 같은 명령을 다시 돌려도 상태 Gate가
    `ABSENT`를 보고 거부한다 — 증적만 되살릴 방법이 없다.
    """

    import ast
    import inspect
    import textwrap

    body = ast.unparse(
        ast.parse(textwrap.dedent(inspect.getsource(cbackup.run_recover)))
    )
    assert body.index("'STARTED'") < body.index("DROP TABLE IF EXISTS")
    assert body.index("DROP TABLE IF EXISTS") < body.index("'COMMITTED'")
    assert "'ABORTED'" in body


# ---------------------------------------------------------------------------
# 14차 권장 1 — 복구 증적을 다시 읽는 consumer
# ---------------------------------------------------------------------------


def _host_fingerprint() -> str:
    import db_target

    return db_target.host_fingerprint(_Target.host, _Target.port)


def _write_recovery(root: Path, **overrides: Any) -> Path:
    payload = _recovery(**{"target_host_fingerprint": _host_fingerprint(), **overrides})
    path = root / cbackup.recovery_receipt_name("kosa_agent_e2e", "GH-130")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestRecoveryEvidenceIsConsumed:
    """validator만으로는 부족하다 — **저장 뒤 읽는 Gate**가 있어야 한다.

    13차 권장 1은 writer 앞의 validator를 만들었다. 그러나 production에는 그 파일을
    다시 읽는 loader도, 완료 판정에 쓰는 소비자도 없었다. 저장 뒤 변조되거나 잘려도
    어느 Gate도 발견하지 못했다(구현리뷰 14차 권장 1).
    """

    def test_a_missing_record_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.load_recovery_evidence(
                "kosa_agent_e2e", "GH-130", backup_root=tmp_path
            )
        assert exc.value.reason_code == "BACKUP_MISSING"

    def test_a_truncated_record_is_refused(self, tmp_path: Path) -> None:
        """**잘린 파일도 sanitized로 끝난다.** traceback으로 새지 않는다."""

        path = tmp_path / cbackup.recovery_receipt_name("kosa_agent_e2e", "GH-130")
        path.write_text('{"artifact_type": "checkpoint_recov', encoding="utf-8")
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.load_recovery_evidence(
                "kosa_agent_e2e", "GH-130", backup_root=tmp_path
            )
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_a_tampered_record_is_refused(self, tmp_path: Path) -> None:
        """저장 뒤 status를 바꿔도 loader가 validator를 다시 태운다."""

        _write_recovery(tmp_path, status="DONE")
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            cbackup.load_recovery_evidence(
                "kosa_agent_e2e", "GH-130", backup_root=tmp_path
            )
        assert exc.value.reason_code == "BACKUP_INVALID"

    def test_a_committed_record_loads(self, tmp_path: Path) -> None:
        _write_recovery(tmp_path)
        record = cbackup.load_recovery_evidence(
            "kosa_agent_e2e", "GH-130", backup_root=tmp_path
        )
        assert record["status"] == "COMMITTED"


def _install_marked_state(
    monkeypatch: pytest.MonkeyPatch, *, marker_overrides: Any = None
) -> None:
    """`READY_MARKED`가 되도록 marker·계보를 실제 값으로 맞춘다.

    `resolve_state()`를 patch하지 않는다 — 판정을 대신 써 주면 무엇을 보는지가 아니라
    무엇을 적었는지를 검증하게 된다.
    """

    import checkpoint_contract as contract
    import setup_checkpoint as checkpoint

    identity = {
        "predecessor_stage": "runtime_guarded",
        "predecessor_marker_sha256": "a" * 64,
        "predecessor_schema_signature_sha256": "b" * 64,
        "target_host_fingerprint": "c" * 64,
    }
    marker = {
        **identity,
        "catalog_signature_sha256": contract.assert_ready(_catalog("READY")),
        **(marker_overrides or {}),
    }
    monkeypatch.setattr(
        checkpoint, "predecessor_identity", lambda target: dict(identity)
    )
    monkeypatch.setattr(checkpoint, "load_marker", lambda *a, **k: dict(marker))


class TestRecoveryVerifyGate:
    """ "복구된 적이 있다"를 인정하려면 **증적이 지금 DB와 같아야** 한다."""

    @staticmethod
    def _run(root: Path, monkeypatch: pytest.MonkeyPatch, shape: Any) -> Any:
        monkeypatch.setattr(cbackup, "observe_shape", lambda cursor, **kw: dict(shape))
        return cbackup.run_verify_recovery(
            _Target(),
            change_reference="GH-130",
            backup_root=root,
            connect=lambda target: _Connection(target.database),
        )

    def test_a_committed_record_matching_the_live_shape_passes(
        self, tmp_path: Path, source_state: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source_state("ABSENT")
        root = _trusted_root(tmp_path)
        _write_recovery(root)
        record = self._run(root, monkeypatch, _SHAPE)
        assert record["status"] == "COMMITTED"

    @pytest.mark.parametrize("status", ["STARTED", "ABORTED"])
    def test_an_unfinished_recovery_is_not_completion_evidence(
        self,
        tmp_path: Path,
        source_state: Any,
        monkeypatch: pytest.MonkeyPatch,
        status: str,
    ) -> None:
        """`STARTED`는 물리 복구 도중, `ABORTED`는 실패다. 둘 다 완료가 아니다."""

        source_state("ABSENT")
        root = _trusted_root(tmp_path)
        _write_recovery(
            root, status=status, recovered_projection=None, completed_at=None
        )
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            self._run(root, monkeypatch, _SHAPE)
        assert exc.value.reason_code == "RECOVERY_INCOMPLETE"

    def test_a_drifted_shape_is_refused(
        self, tmp_path: Path, source_state: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """증적이 서술하는 형상과 지금이 다르면 그 증적은 현재를 증명하지 못한다."""

        source_state("ABSENT")
        root = _trusted_root(tmp_path)
        _write_recovery(root)
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            self._run(root, monkeypatch, {**_SHAPE, "security_sha256": "9" * 64})
        assert exc.value.reason_code == "RECOVERY_DRIFT"

    def test_the_reapplied_target_passes_at_closure(
        self, tmp_path: Path, source_state: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**재적용 뒤 closure에서도 통과한다**(구현리뷰 15차 필수 1).

        정본 §3.6은 복구가 끝나면 `ABSENT`를 확인하고 §3.3 (b) 재적용으로 돌아가라고
        하고, §3.7은 복구가 발생한 target을 closure에서 다시 확인하라고 한다. 그
        두 시점의 상태는 서로 다르다. 이전 판은 backup 발급용 helper를 그대로 써서
        `ABSENT` 하나만 허용했고, 그래서 **정상 절차가 코드상 성립하지 않았다.**
        """

        source_state("READY")
        _install_marked_state(monkeypatch)
        root = _trusted_root(tmp_path)
        _write_recovery(root)
        record = self._run(root, monkeypatch, _SHAPE)
        assert record["_observed_state"] == "READY_MARKED"

    @pytest.mark.parametrize("physical", ["READY", "PARTIAL", "DRIFT"])
    def test_states_between_the_two_checkpoints_are_refused(
        self,
        tmp_path: Path,
        source_state: Any,
        monkeypatch: pytest.MonkeyPatch,
        physical: str,
    ) -> None:
        """두 시점 **사이의 값**은 그 주장을 뒷받침하지 못한다.

        marker 없는 `READY`(=`READY_UNMARKED`)·`PARTIAL`·`DRIFT` 전부다.
        """

        source_state(physical)
        root = _trusted_root(tmp_path)
        _write_recovery(root)
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            self._run(root, monkeypatch, _SHAPE)
        assert exc.value.reason_code == "RECOVERY_EVIDENCE_INVALID"

    def test_a_marker_from_another_lineage_is_refused(
        self, tmp_path: Path, source_state: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """marker drift도 거부한다 — 재적용을 증명하지 못한다."""

        source_state("READY")
        _install_marked_state(
            monkeypatch, marker_overrides={"target_host_fingerprint": "9" * 64}
        )
        root = _trusted_root(tmp_path)
        _write_recovery(root)
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            self._run(root, monkeypatch, _SHAPE)
        assert exc.value.reason_code == "RECOVERY_EVIDENCE_INVALID"

    def test_the_two_accepted_states_are_declared(self) -> None:
        """계약이 상수로 드러나 있다 — 문서와 코드가 같은 목록을 쓴다."""

        assert cbackup.RECOVERY_EVIDENCE_STATES == ("ABSENT", "READY_MARKED")

    def test_another_hosts_record_is_refused(
        self, tmp_path: Path, source_state: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source_state("ABSENT")
        root = _trusted_root(tmp_path)
        _write_recovery(root, target_host_fingerprint="f" * 64)
        with pytest.raises(cbackup.CheckpointBackupError) as exc:
            self._run(root, monkeypatch, _SHAPE)
        assert exc.value.reason_code == "BACKUP_MISMATCH"

    def test_the_gate_is_read_only(self) -> None:
        """`--verify-recovery`는 승인을 요구하지 않는다 — 아무것도 바꾸지 않는다."""

        import ast
        import inspect
        import textwrap

        body = ast.unparse(
            ast.parse(textwrap.dedent(inspect.getsource(cbackup.run_verify_recovery)))
        )
        for forbidden in ("DROP TABLE", "pg_restore", "_acquire_session_lock"):
            assert forbidden not in body, forbidden
