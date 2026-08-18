"""V4-D-1.1 Runtime/evaluation 논리 DB preflight.

Text2SQL 은 Runtime(kosa_agent)과 평가(kosa_text2sql) 두 논리 DB 를 쓴다.
둘이 **같은 source 에서 나왔는지**, 그리고 **각자의 변경 가능성이 다른지**를
질의 실행 전에 확인한다.

왜 필요한가
- 같은 source 가 아니면 평가 결과를 Runtime 동작의 근거로 쓸 수 없다.
  기대 SQL·기대 결과가 다른 데이터를 가리키게 된다 (V4-D-7.x).
- Runtime 은 agent 실행에 따라 행이 늘어나는 write state 이고, 평가는
  고정된 immutable snapshot 이다. 이 구분이 없으면 평가 재현성이 깨진다.

이 모듈은 manifest 를 만들지 않는다. V4-CM-1.1 이 등록한
`infra/bootstrap/manifests/*.json` 을 읽어 비교만 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.analytics.db_pool import LogicalDb

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_ROOT = REPOSITORY_ROOT / "infra" / "bootstrap" / "manifests"

#: 논리 DB -> 검사 대상 bootstrap stage.
#: Runtime 은 agent 가 쓰는 최종 stage, 평가는 Mock action 이 고정된 stage 를 본다.
#: manifest_v3 계약상 immutable 48행 action_history 는 evaluation_mock 에만 있다.
_STAGE_BY_LOGICAL_DB: dict[LogicalDb, str] = {
    LogicalDb.RUNTIME: "runtime_clean",
    LogicalDb.EVALUATION: "evaluation_mock",
}

#: manifest 의 profile 이름. LogicalDb 값과 같지만 의미가 다르므로 분리해 둔다.
_PROFILE_BY_LOGICAL_DB: dict[LogicalDb, str] = {
    LogicalDb.RUNTIME: "runtime",
    LogicalDb.EVALUATION: "evaluation",
}

#: 각 논리 DB 가 가져야 할 변경 정책.
#: Runtime 의 action_history 는 agent 가 채우므로 비어서 시작하고(write state),
#: 평가의 같은 table 은 Mock 48행이 고정된 immutable snapshot 이다.
_EXPECTED_POLICY: dict[LogicalDb, str] = {
    LogicalDb.RUNTIME: "bootstrap_empty",
    LogicalDb.EVALUATION: "immutable_content",
}


class PreflightError(RuntimeError):
    """preflight 실패. 메시지에 접속 정보를 담지 않는다."""


@dataclass(frozen=True)
class LogicalDbState:
    """한 논리 DB 의 bootstrap 상태 요약."""

    logical_db: LogicalDb
    profile: str
    bootstrap_stage: str
    source_archive_sha256: str
    correction_version: str
    applies_to: tuple[str, ...]
    is_mutable: bool

    def describe(self) -> str:
        mutability = "write state" if self.is_mutable else "immutable snapshot"
        return (
            f"{self.logical_db.value}({self.bootstrap_stage}, {mutability},"
            f" source={self.source_archive_sha256[:12]}…)"
        )


@dataclass(frozen=True)
class PreflightResult:
    """preflight 결과. ok 가 False 면 reason 이 채워진다."""

    ok: bool
    runtime: LogicalDbState | None
    evaluation: LogicalDbState | None
    reason: str | None = None


def _load_manifest(profile: str, stage: str) -> dict:
    path = MANIFEST_ROOT / f"{profile}.{stage}.json"

    if not path.exists():
        raise PreflightError(
            f"{profile}.{stage} manifest 가 등록되지 않았다. "
            "V4-CM-1.1 bootstrap 을 먼저 완료해야 한다."
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreflightError(
            f"{profile}.{stage} manifest JSON 형식이 잘못됐다."
        ) from exc

    if not isinstance(payload, dict):
        raise PreflightError(f"{profile}.{stage} manifest 최상위는 object 여야 한다.")

    return payload


def read_state(logical_db: LogicalDb) -> LogicalDbState:
    """논리 DB 하나의 bootstrap 상태를 읽는다."""
    profile = _PROFILE_BY_LOGICAL_DB[logical_db]
    stage = _STAGE_BY_LOGICAL_DB[logical_db]
    manifest = _load_manifest(profile, stage)

    for key in ("source_archive_sha256", "correction_version", "applies_to", "tables"):
        if key not in manifest:
            raise PreflightError(f"{profile}.{stage} manifest 에 {key} 가 없다.")

    # action_history 의 검증 정책이 곧 그 논리 DB 의 변경 가능성이다.
    action = manifest["tables"].get("action_history")
    if not isinstance(action, dict) or "verification_policy" not in action:
        raise PreflightError(
            f"{profile}.{stage} manifest 에 action_history 검증 정책이 없다."
        )

    policy = action["verification_policy"]
    expected_policy = _EXPECTED_POLICY[logical_db]
    if policy != expected_policy:
        raise PreflightError(
            f"{logical_db.value} 의 action_history 정책이 계약과 다르다: "
            f"{policy} (기대 {expected_policy})"
        )

    return LogicalDbState(
        logical_db=logical_db,
        profile=profile,
        bootstrap_stage=stage,
        source_archive_sha256=manifest["source_archive_sha256"],
        correction_version=manifest["correction_version"],
        applies_to=tuple(manifest["applies_to"]),
        is_mutable=(policy == "bootstrap_empty"),
    )


def run_preflight() -> PreflightResult:
    """Runtime 과 평가가 같은 source 인지, 변경 정책이 구분되는지 확인한다.

    예외를 던지지 않고 결과 객체로 돌려준다. 호출부(V4-D-4.x pipeline)가
    Tool 계약 {ok, ..., reason} 으로 그대로 옮길 수 있게 하기 위해서다.
    """
    try:
        runtime = read_state(LogicalDb.RUNTIME)
        evaluation = read_state(LogicalDb.EVALUATION)
    except PreflightError as exc:
        return PreflightResult(ok=False, runtime=None, evaluation=None, reason=str(exc))

    if runtime.source_archive_sha256 != evaluation.source_archive_sha256:
        return PreflightResult(
            ok=False,
            runtime=runtime,
            evaluation=evaluation,
            reason=(
                "Runtime 과 평가의 source archive 가 다르다. 평가 결과를 Runtime "
                "근거로 쓸 수 없다. "
                f"runtime={runtime.source_archive_sha256[:12]}…, "
                f"evaluation={evaluation.source_archive_sha256[:12]}…"
            ),
        )

    if runtime.correction_version != evaluation.correction_version:
        return PreflightResult(
            ok=False,
            runtime=runtime,
            evaluation=evaluation,
            reason=(
                "Runtime 과 평가의 correction_version 이 다르다: "
                f"runtime={runtime.correction_version}, "
                f"evaluation={evaluation.correction_version}"
            ),
        )

    if runtime.is_mutable == evaluation.is_mutable:
        return PreflightResult(
            ok=False,
            runtime=runtime,
            evaluation=evaluation,
            reason=(
                "Runtime write state 와 평가 immutable snapshot 이 구분되지 않는다. "
                "평가 재현성을 보장할 수 없다."
            ),
        )

    if set(runtime.applies_to) & set(evaluation.applies_to):
        return PreflightResult(
            ok=False,
            runtime=runtime,
            evaluation=evaluation,
            reason=(
                "Runtime 과 평가가 같은 물리 DB 를 가리킨다: "
                f"{sorted(set(runtime.applies_to) & set(evaluation.applies_to))}"
            ),
        )

    return PreflightResult(ok=True, runtime=runtime, evaluation=evaluation)
