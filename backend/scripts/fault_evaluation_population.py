"""C-6.1 원 evidence에서 C-6.2 평가 모집단을 재도출한다.

이 모듈은 evidence manifest가 주장하는 임의의 run 목록이나 batch incident line의
선택적 ``agent_run_id``를 신뢰하지 않는다. C-6.1의 검증된 loader가 연 round-2
``BATCH_BASELINE`` 단일 ``DB_SNAPSHOT``과 독립 oracle에서 exact 12개를 다시 만든다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.agent.golden_flow import (
    ArtifactKind,
    ExecutionScope,
    ExpectedOracle,
    GateKind,
    GoldenFlowContractError,
    GoldenPhase,
    load_expected_oracle,
    snapshot_from_mapping,
    validate_expected_oracle_source,
)
from app.evaluation.fault_5class import EXPECTED_POPULATION_COUNT, IncidentKey
from scripts.verify_golden_flow import EvidenceInvalid, load_evidence_bundle

BACKEND_ROOT: Final = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT: Final = BACKEND_ROOT.parent
ORACLE_PATH: Final = BACKEND_ROOT / "tests/fixtures/v5_c_6_1/golden_incidents.json"
SOURCE_MANIFEST_PATH: Final = (
    REPOSITORY_ROOT / "infra/bootstrap/source-manifest-v4.json"
)


class PopulationEvidenceInvalid(ValueError):
    """원 evidence·oracle·baseline snapshot의 결속이 유효하지 않다."""


@dataclass(frozen=True, order=True, slots=True)
class PopulationMember:
    incident: IncidentKey
    agent_run_id: str
    action_id: str
    expected_action: str


@dataclass(frozen=True, slots=True)
class EvaluationPopulation:
    members: tuple[PopulationMember, ...]
    golden_evidence_sha256: str
    baseline_snapshot_artifact_sha256: str
    oracle_sha256: str
    population_sha256: str

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(item.agent_run_id for item in self.members)

    @property
    def expected_actions(self) -> Mapping[IncidentKey, str]:
        return {item.incident: item.expected_action for item in self.members}


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID") from exc


def _json_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID") from exc
    if not isinstance(value, Mapping):
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
    return value


def _load_oracle() -> ExpectedOracle:
    try:
        oracle = load_expected_oracle(_json_object(ORACLE_PATH))
    except GoldenFlowContractError as exc:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID") from exc
    try:
        validate_expected_oracle_source(oracle, _file_sha256(SOURCE_MANIFEST_PATH))
    except GoldenFlowContractError as exc:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID") from exc
    return oracle


def _baseline_artifact_sha256(raw_manifest: Mapping[str, Any], artifact_id: str) -> str:
    artifacts = raw_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
    matches = [
        item
        for item in artifacts
        if isinstance(item, Mapping) and item.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
    digest = matches[0].get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
    return digest


def _population_sha256(members: tuple[PopulationMember, ...]) -> str:
    payload = [
        {
            "action_id": item.action_id,
            "agent_run_id": item.agent_run_id,
            "chamber_id": item.incident.chamber_id,
            "expected_action": item.expected_action,
            "lot_id": item.incident.lot_id,
        }
        for item in members
    ]
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_evaluation_population(evidence_path: Path) -> EvaluationPopulation:
    """원 evidence를 완전 검증한 뒤 baseline exact 모집단을 다시 도출한다."""

    try:
        bundle = load_evidence_bundle(evidence_path)
    except (EvidenceInvalid, GoldenFlowContractError) as exc:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID") from exc
    if (
        bundle.gate_kind is not GateKind.PUBLIC_GOLDEN_FLOW
        or bundle.level_rounds != (2,)
        or bundle.scopes.get(GoldenPhase.BATCH_BASELINE)
        is not ExecutionScope.PUBLIC_E2E
    ):
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")

    artifacts = bundle.for_phase(
        GoldenPhase.BATCH_BASELINE,
        ArtifactKind.DB_SNAPSHOT,
        level_round=2,
    )
    if len(artifacts) != 1:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
    # evidence loader와 여기 모두 C-6.1의 exact snapshot parser 하나를 공유한다.
    try:
        snapshot = snapshot_from_mapping(artifacts[0].payload)
    except GoldenFlowContractError as exc:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID") from exc

    oracle = _load_oracle()
    oracle_by_key = {
        IncidentKey(item.lot_id, item.chamber_id): item for item in oracle.incidents
    }

    runs = tuple(snapshot.runs)
    actions = tuple(snapshot.actions)
    if (
        len(runs) != EXPECTED_POPULATION_COUNT
        or len(actions) != EXPECTED_POPULATION_COUNT
        or len({action.action_id for action in actions}) != EXPECTED_POPULATION_COUNT
        or any(run.retry_of_run_id is not None for run in runs)
        or any(run.autonomy_level != 2 for run in runs)
        or any(action.link_role != "CREATED" for action in actions)
    ):
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
    runs_by_id = {run.agent_run_id: run for run in runs}
    if len(runs_by_id) != EXPECTED_POPULATION_COUNT:
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")

    members: list[PopulationMember] = []
    seen_keys: set[IncidentKey] = set()
    for action in actions:
        key = IncidentKey(action.lot_id, action.chamber_id)
        expected = oracle_by_key.get(key)
        run = runs_by_id.get(action.agent_run_id)
        if (
            expected is None
            or run is None
            or key in seen_keys
            or run.lot_id != key.lot_id
            or run.chamber_id != key.chamber_id
            or action.action_code != run.action
        ):
            raise PopulationEvidenceInvalid("EVIDENCE_INVALID")
        seen_keys.add(key)
        members.append(
            PopulationMember(
                incident=key,
                agent_run_id=run.agent_run_id,
                action_id=action.action_id,
                expected_action=expected.expected_action,
            )
        )
    if seen_keys != set(oracle_by_key):
        raise PopulationEvidenceInvalid("EVIDENCE_INVALID")

    ordered = tuple(sorted(members))
    raw_manifest = _json_object(evidence_path)
    return EvaluationPopulation(
        members=ordered,
        golden_evidence_sha256=_file_sha256(evidence_path),
        baseline_snapshot_artifact_sha256=_baseline_artifact_sha256(
            raw_manifest, artifacts[0].artifact_id
        ),
        oracle_sha256=_file_sha256(ORACLE_PATH),
        population_sha256=_population_sha256(ordered),
    )


__all__ = [
    "EvaluationPopulation",
    "PopulationEvidenceInvalid",
    "PopulationMember",
    "load_evaluation_population",
]
