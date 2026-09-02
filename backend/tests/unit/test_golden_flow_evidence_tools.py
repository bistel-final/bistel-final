"""C-6.1 evidence 보조 스크립트(snapshot 기록·manifest 생성) 회귀."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = BACKEND_ROOT / "scripts"
for entry in (BACKEND_ROOT, SCRIPTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from scripts import build_golden_flow_evidence as builder  # noqa: E402
from scripts import capture_golden_flow_snapshot as capture  # noqa: E402
from scripts.verify_golden_flow import load_evidence_bundle  # noqa: E402

EMPTY_SNAPSHOT: dict[str, list[Any]] = {
    "runs": [],
    "actions": [],
    "approvals": [],
    "deliveries": [],
    "tools": [],
    "audits": [],
    "r03_incidents": [],
}


class _FakeConnection:
    def __init__(self, identity: tuple[str, str], snapshot: Any) -> None:
        self.statements: list[str] = []
        self._identity = identity
        self._snapshot = snapshot

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)

    def execute(self, statement: Any) -> Any:
        self.statements.append(str(statement))
        if "current_database" in str(statement):
            database_name, role_name = self._identity
            return SimpleNamespace(
                one=lambda: SimpleNamespace(
                    database_name=database_name, role_name=role_name
                )
            )
        return SimpleNamespace(one=lambda: SimpleNamespace(snapshot=self._snapshot))


class _FakeEngine:
    def __init__(self, identity: tuple[str, str], snapshot: Any) -> None:
        self.connection = _FakeConnection(identity, snapshot)

    def connect(self) -> _FakeConnection:
        return self.connection


def test_capture_writes_0600_snapshot_after_identity_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = _FakeEngine(("kosa_agent_e2e", "kosa_app"), EMPTY_SNAPSHOT)
    output = tmp_path / "snapshot-preflight.json"

    rc = capture.main(
        [
            "--database",
            "kosa_agent_e2e",
            "--phase",
            "PREFLIGHT",
            "--output",
            str(output),
        ],
        engine_factory=lambda: engine,
    )

    assert rc == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8")) == EMPTY_SNAPSHOT
    assert engine.connection.statements[0].startswith(
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    report = json.loads(capsys.readouterr().out.strip())
    assert report["status"] == "PASSED"
    assert report["phase"] == "PREFLIGHT"
    assert report["counts"] == dict.fromkeys(EMPTY_SNAPSHOT, 0)


@pytest.mark.parametrize(
    ("database", "identity", "reason"),
    [
        ("kosa_agent", ("kosa_agent_e2e", "kosa_app"), "TARGET_MISMATCH"),
        ("kosa_agent_e2e", ("kosa_agent", "kosa_app"), "TARGET_MISMATCH"),
        ("kosa_agent_e2e", ("kosa_agent_e2e", "kosa_readonly"), "TARGET_MISMATCH"),
    ],
)
def test_capture_blocks_wrong_target_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    database: str,
    identity: tuple[str, str],
    reason: str,
) -> None:
    output = tmp_path / "snapshot-x.json"
    rc = capture.main(
        ["--database", database, "--phase", "PREFLIGHT", "--output", str(output)],
        engine_factory=lambda: _FakeEngine(identity, EMPTY_SNAPSHOT),
    )
    assert rc == 3
    assert not output.exists()
    assert json.loads(capsys.readouterr().out.strip())["reason_code"] == reason


def test_capture_refuses_to_overwrite_and_invalid_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "snapshot-x.json"
    output.write_text("{}", encoding="utf-8")
    rc = capture.main(
        ["--database", "kosa_agent_e2e", "--phase", "UNKNOWN", "--output", str(output)],
        engine_factory=lambda: _FakeEngine(
            ("kosa_agent_e2e", "kosa_app"), EMPTY_SNAPSHOT
        ),
    )
    assert rc == 3
    assert json.loads(capsys.readouterr().out.strip())["reason_code"] == (
        "RECEIPT_ALREADY_EXISTS"
    )

    bad = {**EMPTY_SNAPSHOT, "extra": []}
    rc = capture.main(
        [
            "--database",
            "kosa_agent_e2e",
            "--phase",
            "UNKNOWN",
            "--output",
            str(tmp_path / "snapshot-y.json"),
        ],
        engine_factory=lambda: _FakeEngine(("kosa_agent_e2e", "kosa_app"), bad),
    )
    assert rc == 3
    assert json.loads(capsys.readouterr().out.strip())["reason_code"] == (
        "SNAPSHOT_INVALID"
    )


def _layout(root: Path) -> None:
    for phase in builder.PHASES:
        directory = root / "artifacts" / phase
        directory.mkdir(parents=True)
        (directory / "snapshot-end.json").write_text(
            json.dumps(EMPTY_SNAPSHOT), encoding="utf-8"
        )
    (root / "artifacts" / "PREFLIGHT" / "batch-dry-run.ndjson").write_text(
        json.dumps(
            {
                "type": "plan",
                "database": "kosa_agent_e2e",
                "selected": [],
                "rejected": [],
                "incomplete": [],
                "excluded": {"canonical_null_rows": 0, "canonical_null_by_source": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "artifacts" / "PRE_APPROVAL" / "kafka-actions.json").write_text(
        json.dumps(
            {"format_version": 1, "topic": "fdc.actions", "before": 0, "after": 0}
        ),
        encoding="utf-8",
    )


def test_builder_emits_manifest_the_verifier_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "evidence"
    _layout(root)

    rc = builder.main(["--root", str(root)])

    assert rc == 0
    manifest_path = root / "evidence.json"
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "format_version",
        "dataset_epoch",
        "gate_kind",
        "level_round",
        "phases",
        "artifacts",
    }
    assert manifest["phases"]["MANUAL_RETRY"]["execution_scope"] == (
        "ISOLATED_CONTAINER"
    )
    assert manifest["phases"]["DECISIONS"]["execution_scope"] == "PUBLIC_E2E"
    kinds = {item["artifact_id"]: item["kind"] for item in manifest["artifacts"]}
    assert kinds["preflight-batch-dry-run"] == "BATCH_NDJSON"
    assert kinds["pre_approval-kafka-actions"] == "KAFKA_OFFSETS"
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"])
    # verifier의 bundle loader가 schema·path·hash·parser를 그대로 통과해야 한다.
    bundle = load_evidence_bundle(manifest_path)
    assert bundle.level_rounds == (2,) or list(bundle.level_rounds) == [2]
    report = json.loads(capsys.readouterr().out.strip())
    assert report["status"] == "PASSED"
    assert report["artifact_count"] == 9


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda r: (r / "artifacts" / "UNKNOWN" / "snapshot-end.json").unlink(),
            "PHASE_SNAPSHOT_MISSING",
        ),
        (lambda r: (r / "artifacts" / "EXTRA").mkdir(), "PHASE_DIRECTORY_UNKNOWN"),
        (
            lambda r: (r / "artifacts" / "DECISIONS" / "note-x.json").write_text("{}"),
            "ARTIFACT_PREFIX_INVALID",
        ),
        (
            lambda r: (r / "artifacts" / "DECISIONS" / "batch-x.json").write_text("{}"),
            "ARTIFACT_SUFFIX_INVALID",
        ),
        (
            lambda r: (r / "artifacts" / "DECISIONS" / "http-link.json").symlink_to(
                r / "artifacts" / "DECISIONS" / "snapshot-end.json"
            ),
            "ARTIFACT_NOT_REGULAR_FILE",
        ),
    ],
)
def test_builder_blocks_layout_violations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutate: Any,
    reason: str,
) -> None:
    root = tmp_path / "evidence"
    _layout(root)
    mutate(root)

    rc = builder.main(["--root", str(root)])

    assert rc == 3
    assert not (root / "evidence.json").exists()
    assert json.loads(capsys.readouterr().out.strip())["reason_code"] == reason


def test_builder_manual_retry_public_flag_and_no_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "evidence"
    _layout(root)
    assert builder.main(["--root", str(root), "--manual-retry-public"]) == 0
    manifest = json.loads((root / "evidence.json").read_text(encoding="utf-8"))
    assert manifest["phases"]["MANUAL_RETRY"]["execution_scope"] == "PUBLIC_E2E"
    capsys.readouterr()

    assert builder.main(["--root", str(root)]) == 3
    assert json.loads(capsys.readouterr().out.strip())["reason_code"] == (
        "RECEIPT_ALREADY_EXISTS"
    )
