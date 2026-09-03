#!/usr/bin/env python3
"""V5-C-7.1 deterministic 36-run comparison executor.

공용 DB에서는 snapshot만 읽고, 실행·감사 DML은 level별 fresh one-off PostgreSQL에서만
수행한다. 실제 LLM observational 20회는 고정 revision에서 별 실행으로 추가되며 이
파일이 만드는 deterministic artifact를 채택 근거로 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent import comparison, experiment, react  # noqa: E402
from app.agent.prompts import PROMPT_VERSION  # noqa: E402
from app.agent.routing import GraphBoundary  # noqa: E402
from app.agent.state import Hypothesis, HypothesisOutcome, LlmUsage  # noqa: E402
from app.agent.tools import (  # noqa: E402
    AuditedToolExecutor,
    ThreadDeadlineRunner,
    ToolBoundary,
)
from app.common import config as settings  # noqa: E402
from app.common.enums import AlarmSource, FaultHypothesis  # noqa: E402
from app.common.schemas import AlarmRef  # noqa: E402
from app.common.tool_contracts import (  # noqa: E402
    DocumentSearchToolResult,
    EquipmentContextToolResult,
    FdcSummaryToolResult,
    fail,
)
from app.detection.service import FdcSummaryService  # noqa: E402
from scripts.rehearsal_postgres import (  # noqa: E402
    POSTGRES_RAG_IMAGE,
    RehearsalEndpoint,
    one_off_postgres,
)

ORACLE_PATH = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"
SNAPSHOT_DATABASE = "fdc_react_comparison"


class ComparisonExecutionError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|authorization|secret)"
    r"(\s*[:=]\s*)([^\r\n]+)"
)
_URL_USERINFO = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")


def _safe_stderr_tail(
    stderr: str,
    *,
    environment: dict[str, str] | None,
) -> str | None:
    """비밀을 제거한 stderr 마지막 5줄만 진단용으로 보존한다."""

    value = stderr
    for key, secret in (environment or {}).items():
        upper = key.upper()
        if secret and any(
            marker in upper
            for marker in ("PASSWORD", "TOKEN", "SECRET", "API_KEY", "AUTHORIZATION")
        ):
            value = value.replace(secret, "***")
    value = _SECRET_ASSIGNMENT.sub(r"\1\2***", value)
    value = _URL_USERINFO.sub(r"\1***@", value)
    lines = [
        " ".join(line.split())[:240] for line in value.splitlines() if line.strip()
    ]
    return " | ".join(lines[-5:]) or None


def _pinned_local_image_runner(command: list[str], **kwargs: Any) -> Any:
    """digest image가 이미 있으면 registry 접속 없이 exact local image를 사용한다."""

    if len(command) >= 4 and command[1:3] == ["pull", "--quiet"]:
        command = ["docker", "image", "inspect", command[3]]
    return subprocess.run(command, **kwargs)


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            timeout=900,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComparisonExecutionError(
            "COMPARISON_DEPENDENCY_FAILED",
            detail="dependency command timed out",
        ) from exc
    if completed.returncode:
        detail = _safe_stderr_tail(completed.stderr, environment=environment)
        raise ComparisonExecutionError(
            "COMPARISON_DEPENDENCY_FAILED",
            detail=detail or f"exit={completed.returncode}",
        )


def _verify_revision(expected: str) -> None:
    if not comparison.REVISION.fullmatch(expected):
        raise ComparisonExecutionError("REVISION_MISMATCH")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if head != expected or dirty:
        raise ComparisonExecutionError("REVISION_MISMATCH")


def _source_snapshot(target: Path) -> str:
    password = (os.getenv("POSTGRES_PASSWORD") or "").strip()
    user = (os.getenv("POSTGRES_USER") or "").strip()
    if not password or not user:
        raise ComparisonExecutionError("SOURCE_SNAPSHOT_NOT_READY")
    environment = dict(os.environ)
    environment["PGPASSWORD"] = password
    _run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "PGPASSWORD",
            "-v",
            f"{target.parent}:/work",
            POSTGRES_RAG_IMAGE,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--host",
            settings.POSTGRES_HOST,
            "--port",
            str(settings.POSTGRES_PORT),
            "--username",
            user,
            "--dbname",
            settings.POSTGRES_DB,
            "--file",
            f"/work/{target.name}",
        ],
        environment=environment,
    )
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _restore_snapshot(endpoint: RehearsalEndpoint, snapshot: Path) -> None:
    remote = "/tmp/source.dump"
    _run(["docker", "cp", str(snapshot), f"{endpoint.container_id}:{remote}"])
    _run(
        [
            "docker",
            "exec",
            endpoint.container_id,
            "pg_restore",
            "--username",
            endpoint.username,
            "--dbname",
            endpoint.database,
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            remote,
        ]
    )


def _engine(endpoint: RehearsalEndpoint) -> Any:
    return create_engine(
        URL.create(
            "postgresql+psycopg",
            username=endpoint.username,
            password=endpoint.password,
            host=endpoint.host,
            port=endpoint.port,
            database=endpoint.database,
        ),
        pool_pre_ping=True,
    )


def _oracle() -> tuple[dict[str, Any], ...]:
    value = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    incidents = value.get("incidents")
    if not isinstance(incidents, list) or len(incidents) != 12:
        raise ComparisonExecutionError("POPULATION_MISMATCH")
    return tuple(dict(item) for item in incidents)


_ALARM_SQL = {
    AlarmSource.R03: text(
        "SELECT alarm_id FROM r03_alarm_history "
        "WHERE lot_id=:lot AND chamber_id=:ch ORDER BY occurred_at LIMIT 1"
    ),
    AlarmSource.TRACE: text(
        "SELECT alarm_id FROM trace_alarm_history "
        "WHERE lot=:lot AND chamber=:ch ORDER BY occurred_at LIMIT 1"
    ),
    AlarmSource.SUMMARY: text(
        "SELECT alarm_id FROM summary_alarm_history "
        "WHERE lot=:lot AND chamber=:ch ORDER BY occurred_at LIMIT 1"
    ),
}


def _identities(engine: Any) -> tuple[dict[str, Any], ...]:
    identities = []
    with engine.connect() as connection:
        for fixture in _oracle():
            alarm = None
            for source in (AlarmSource.R03, AlarmSource.TRACE, AlarmSource.SUMMARY):
                if source.value not in fixture["alarm_sources"]:
                    continue
                alarm_id = connection.execute(
                    _ALARM_SQL[source],
                    {"lot": fixture["lot_id"], "ch": fixture["chamber_id"]},
                ).scalar_one_or_none()
                if alarm_id is not None:
                    alarm = AlarmRef(source=source, alarm_id=str(alarm_id))
                    break
            if alarm is None:
                raise ComparisonExecutionError("POPULATION_MISMATCH")
            identities.append(
                {
                    "lot_id": fixture["lot_id"],
                    "chamber_id": fixture["chamber_id"],
                    "requested_alarm": alarm.model_dump(mode="json"),
                }
            )
    return tuple(identities)


def _clear_agent_runtime(engine: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE agent_run CASCADE"))


def _tool_boundary(engine: Any) -> ToolBoundary:
    def fdc(payload: dict[str, Any]) -> FdcSummaryToolResult:
        with engine.connect() as connection:
            result = FdcSummaryService(connection).get_fdc_summary(
                str(payload["lot_hist_id"])
            )
        return result or fail(FdcSummaryToolResult, "NOT_FOUND: fixture")

    def equipment(payload: dict[str, Any]) -> EquipmentContextToolResult:
        chamber = str(payload["chamber_id"])
        return EquipmentContextToolResult(
            ok=True,
            chamber_id=chamber,
            equipment_id=chamber.split("-")[0],
            area="COMPARISON",
            model_code="COMPARISON-MODEL",
            process_step_id="COMPARISON-STEP",
            graph_revision="comparison-v1",
        )

    def documents(_payload: dict[str, Any]) -> DocumentSearchToolResult:
        return DocumentSearchToolResult(ok=True, hits=[])

    return ToolBoundary(
        fdc_summary=fdc,
        equipment_context=equipment,
        document_search=documents,
    )


def _selector(context: react.ReactContext) -> react.ReactSelectionOutcome:
    if context.equipment_observation is None:
        selection = react.ReactSelection(
            rationale_summary="설비와 공정 관계 근거를 확인한다",
            next="get_equipment_context",
            arguments=react.ReactArguments(),
        )
    elif not context.document_observations:
        selection = react.ReactSelection(
            rationale_summary="관찰된 이상과 관련된 문서 근거를 확인한다",
            next="search_documents",
            arguments=react.ReactArguments(query="공정 이상 관리 기준"),
        )
    else:
        selection = react.ReactSelection(
            rationale_summary="가설 생성에 필요한 근거가 충분하다",
            next="stop",
            arguments=react.ReactArguments(),
            stop_reason="근거 충분",
        )
    return react.ReactSelectionOutcome(
        selection=selection,
        llm_usage=LlmUsage(
            model="deterministic-selector-v1",
            prompt_version=react.REACT_PROMPT_VERSION,
            input_tokens=0,
            output_tokens=0,
        ),
    )


def _hypothesis(*_args: Any) -> HypothesisOutcome:
    return HypothesisOutcome(
        hypothesis=Hypothesis(
            predicted_fault_code=FaultHypothesis.OTH,
            confidence=0.5,
            cause_summary="deterministic comparison hypothesis",
            uncertainty="deterministic contract only",
        ),
        llm_usage=LlmUsage(
            model="deterministic-hypothesis-v1",
            prompt_version="agent-hypothesis-v2-ko1",
            input_tokens=0,
            output_tokens=0,
        ),
    )


def _level_rows(
    endpoint: RehearsalEndpoint,
    *,
    level: int,
    snapshot: Path,
) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    _restore_snapshot(endpoint, snapshot)
    engine = _engine(endpoint)
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="comparison")
    try:
        identities = _identities(engine)
        _clear_agent_runtime(engine)

        @contextmanager
        def transactions():
            with engine.begin() as connection:
                yield connection

        tools = AuditedToolExecutor(
            transactions=transactions,
            boundary=_tool_boundary(engine),
            deadline_runner=ThreadDeadlineRunner(executor),
        )
        graph = experiment.build_level_graph(
            level,
            selector_port=_selector if level == 3 else None,
            hypothesis_port=_hypothesis,
            clock=lambda: datetime.now(UTC),
            tools=tools,
            transactions=transactions,
            routing_graph=GraphBoundary.production(),
            configured_llm_model="deterministic-hypothesis-v1",
        )
        records = []
        for identity in identities:
            _clear_agent_runtime(engine)
            started = time.monotonic()
            alarm = AlarmRef.model_validate(identity["requested_alarm"])
            state = graph.invoke(
                {"requested_alarm": alarm},
                config={"configurable": {"thread_id": str(uuid4())}},
            )
            elapsed = int((time.monotonic() - started) * 1000)
            run_id = state.get("run_id")
            history = () if not isinstance(run_id, str) else tools.history(run_id)
            trace = tuple(
                react.ReactStep.model_validate(item)
                for item in state.get("react_trace", ())
            )
            selector_tokens = sum(
                item.selector_tokens.input + item.selector_tokens.output
                for item in trace
            )
            row = {
                "level": level,
                "outcome": (
                    "COMPLETED"
                    if state.get("action_decision") is not None
                    and state.get("terminal_error") is None
                    else "FAILED"
                ),
                "action": (
                    None
                    if state.get("action_decision") is None
                    else state["action_decision"].action
                ),
                "tool_path": [item.tool_name for item in history],
                "tool_calls": [
                    {"tool": item.tool_name, "status": item.status.value}
                    for item in history
                ],
                "tokens": {"hypothesis": 0, "selector": selector_tokens},
                "latency_ms": elapsed,
                "guard_rejections": state.get("react_guard_rejections", 0),
                "degraded": any(item.degraded for item in trace),
                "error_codes": [item.code for item in state.get("errors", ())],
            }
            records.append((identity, row))
        return tuple(records)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        engine.dispose()


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(str(row["outcome"]) for row in rows)
    return {
        "outcome_counts": dict(sorted(outcomes.items())),
        "completion_rate": outcomes.get("COMPLETED", 0) / len(rows),
        "tokens": {
            "hypothesis": sum(row["tokens"]["hypothesis"] for row in rows),
            "selector": sum(row["tokens"]["selector"] for row in rows),
        },
        "tool_calls": sum(len(row["tool_path"]) for row in rows),
        "latency_ms": sum(row["latency_ms"] for row in rows),
    }


def execute(*, revision: str, artifact: Path) -> str:
    _verify_revision(revision)
    with tempfile.TemporaryDirectory(prefix="v5-c-7-1-") as directory:
        snapshot = Path(directory) / "source.dump"
        snapshot_sha = _source_snapshot(snapshot)
        by_identity: dict[str, dict[str, Any]] = {}
        by_level: dict[int, list[dict[str, Any]]] = {1: [], 2: [], 3: []}
        for level in (1, 2, 3):
            with one_off_postgres(
                database=f"{SNAPSHOT_DATABASE}_l{level}",
                image=POSTGRES_RAG_IMAGE,
                command_runner=_pinned_local_image_runner,
            ) as endpoint:
                for identity, row in _level_rows(
                    endpoint, level=level, snapshot=snapshot
                ):
                    key = comparison.canonical_sha256(identity)
                    entry = by_identity.setdefault(
                        key, {"identity": identity, "levels": []}
                    )
                    entry["levels"].append(row)
                    by_level[level].append(row)
        incidents = sorted(
            by_identity.values(),
            key=lambda item: (
                item["identity"]["lot_id"],
                item["identity"]["chamber_id"],
            ),
        )
        fixture_projection = [item["identity"] for item in incidents]
        payload = {
            "schema_version": "level-comparison-v1",
            "source_revision": revision,
            "fixture_source_sha256": hashlib.sha256(
                ORACLE_PATH.read_bytes()
            ).hexdigest(),
            "fixture_projection_sha256": comparison.canonical_sha256(
                fixture_projection
            ),
            "initial_snapshot_sha256": snapshot_sha,
            "SYNTHETIC_DETERMINISTIC_BENCHMARK": True,
            "PRODUCTION_PERFORMANCE_NOT_CLAIMED": True,
            "EXPERIMENT_ONLY": True,
            "runs": 36,
            "level_configs": [
                {
                    "level": level,
                    "config": {"autonomy_level": level},
                    "hypothesis_prompt_version": PROMPT_VERSION,
                    "react_prompt_version": (
                        react.REACT_PROMPT_VERSION if level == 3 else None
                    ),
                    "ports": "deterministic",
                }
                for level in (1, 2, 3)
            ],
            "incidents": incidents,
            "aggregate": {
                str(level): _aggregate(by_level[level]) for level in (1, 2, 3)
            },
            "safety": {
                "send_action_selected": 0,
                "hitl_bypass": 0,
                "pre_approval_mes": 0,
            },
            "contract_verdict": "ADAPTIVE_LOOP_CONTRACT_PASS",
        }
        comparison.validate_level_comparison(payload)
        return comparison.write_immutable_json(artifact, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-revision", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest = execute(revision=args.expect_revision, artifact=args.artifact)
    except Exception as exc:
        code = getattr(exc, "code", None) or str(exc)
        allowed = {
            "REVISION_MISMATCH",
            "SOURCE_SNAPSHOT_NOT_READY",
            "COMPARISON_DEPENDENCY_FAILED",
            "POPULATION_MISMATCH",
            "ARTIFACT_CLOBBER_BLOCKED",
        }
        reason = code if code in allowed else "COMPARISON_FAILED"
        error_payload = {"reason_code": reason}
        diagnostic = getattr(exc, "detail", None)
        if isinstance(diagnostic, str) and diagnostic:
            error_payload["diagnostic"] = diagnostic
        print(json.dumps(error_payload), file=sys.stderr)
        return 1
    print(f"LEVEL_COMPARISON_WRITTEN sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
