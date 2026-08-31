"""V5-C-6.2 합성 Fault 5-class 격리 평가 CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.fault_5class import (  # noqa: E402
    ArtifactProvenance,
    FaultEvaluationContractError,
    artifact_to_dict,
    evaluate_fault_5class,
    freeze_predictions,
)
from app.evaluation.predictions_repository import (  # noqa: E402
    TARGET_DATABASE,
    PredictionRepositoryError,
    PredictionTargetMismatch,
    read_evaluation_label_snapshot,
    read_runtime_evaluation_snapshot,
)
from scripts.fault_evaluation_artifact import (  # noqa: E402
    FaultArtifactWriteError,
    write_fault_evaluation_artifact,
)
from scripts.fault_evaluation_population import (  # noqa: E402
    PopulationEvidenceInvalid,
    load_evaluation_population,
)
from scripts.fault_evaluation_provenance import (  # noqa: E402
    ProvenanceInvalid,
    load_static_provenance,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_EVIDENCE = 3


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _UsageError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__, add_help=False)
    parser.add_argument(
        "--agent-database",
        required=True,
        choices=(TARGET_DATABASE,),
    )
    parser.add_argument("--golden-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _emit(payload: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )


def _stderr(reason: str) -> None:
    print(reason, file=sys.stderr)


def _code_revision() -> str:
    supplied = os.environ.get("GITHUB_SHA", "")
    if re.fullmatch(r"[0-9a-f]{40}", supplied):
        return supplied
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceInvalid("EVIDENCE_INVALID") from exc
    revision = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ProvenanceInvalid("EVIDENCE_INVALID")
    return revision


def main(
    argv: Sequence[str] | None = None,
    *,
    app_engine_factory: Callable[[], Any] | None = None,
    evaluation_engine_factory: Callable[[], Any] | None = None,
    population_loader: Callable[[Path], Any] = load_evaluation_population,
    provenance_loader: Callable[[], Any] = load_static_provenance,
    label_loader: Callable[[Any, Sequence[tuple[str, str]]], Sequence[Any]]
    | None = None,
    artifact_writer: Callable[[Path, Mapping[str, Any]], str] = (
        write_fault_evaluation_artifact
    ),
    revision_loader: Callable[[], str] = _code_revision,
) -> int:
    """고정 증빙→prediction freeze→label read→metric→불변 artifact 순서."""

    try:
        args = _parser().parse_args(argv)
    except _UsageError:
        _stderr("USAGE_INVALID")
        return EXIT_USAGE

    # 어떤 connector factory도 만들기 전에 원 evidence와 named provenance를 끝낸다.
    try:
        population = population_loader(args.golden_evidence)
        static = provenance_loader()
        revision = revision_loader()
    except (PopulationEvidenceInvalid, ProvenanceInvalid):
        _stderr("EVIDENCE_INVALID")
        return EXIT_EVIDENCE

    try:
        if app_engine_factory is None:
            from app.common.db import get_app_engine

            app_engine_factory = get_app_engine
        runtime = read_runtime_evaluation_snapshot(
            app_engine_factory(),
            database=args.agent_database,
            run_ids=population.run_ids,
        )
        # 이 hash가 만들어지기 전에는 evaluation_loader를 import조차 하지 않는다.
        frozen = freeze_predictions(runtime.records)

        if label_loader is None:
            from app.detection.evaluation_loader import fetch_incident_fault_labels

            label_loader = fetch_incident_fault_labels
        if evaluation_engine_factory is None:
            from app.common.db import get_evaluation_engine

            evaluation_engine_factory = get_evaluation_engine
        labels = read_evaluation_label_snapshot(
            evaluation_engine_factory(),
            frozen=frozen,
            expected_shared_key_sha256=runtime.shared_key_sha256,
            label_loader=label_loader,
        )
        result = evaluate_fault_5class(
            frozen,
            labels,
            population.expected_actions,
        )
        artifact = artifact_to_dict(
            result,
            ArtifactProvenance(
                golden_evidence_sha256=population.golden_evidence_sha256,
                baseline_snapshot_artifact_sha256=(
                    population.baseline_snapshot_artifact_sha256
                ),
                oracle_sha256=population.oracle_sha256,
                population_sha256=population.population_sha256,
                prediction_hash=frozen.prediction_hash,
                runtime_provenance_sha256=static.runtime_sha256,
                evaluation_provenance_sha256=static.evaluation_sha256,
                shared_key_sha256=runtime.shared_key_sha256,
                code_revision=revision,
            ),
        )
        artifact_sha256 = artifact_writer(args.output, artifact)
    except PredictionTargetMismatch:
        _stderr("TARGET_MISMATCH")
        return EXIT_EVIDENCE
    except FaultEvaluationContractError:
        _stderr("EVALUATION_CONTRACT_FAILED")
        return EXIT_FAILED
    except (PredictionRepositoryError, FaultArtifactWriteError):
        _stderr("FAULT_EVALUATION_FAILED")
        return EXIT_FAILED
    except Exception:  # noqa: BLE001 - CLI는 driver·설정 원문을 노출하지 않는다.
        _stderr("FAULT_EVALUATION_FAILED")
        return EXIT_FAILED

    _emit(
        {
            "type": "final",
            "status": "PASS" if result.hard_gate_passed else "FAIL",
            "hard_gate_reasons": list(result.hard_gate_reasons),
            "classification_population_count": 7,
            "artifact_sha256": artifact_sha256,
        }
    )
    return EXIT_OK if result.hard_gate_passed else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
