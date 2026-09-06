"""Private operator inputs shared by Stage2 leaves. No service/network effects."""

import io
import os
from pathlib import Path

from dotenv import dotenv_values

from app.agent.release_aggregate import AttemptReceipt
from app.agent.release_artifacts import (
    EvidenceError,
    component_ref,
    parse_json,
    read_private,
    validate_report_root,
)
from app.agent.release_prepared import Revision
from app.agent.release_production import read_env
from app.agent.release_runtime import ImagePins
from app.agent.u10_evaluation import verify_evaluation
from app.agent.u10_revision import verify_execution_revision


def u10_inputs(repository):
    names = {
        "artifact": "CM52_U10_ARTIFACT",
        "evaluation_receipt": "CM52_U10_EVALUATION_RECEIPT",
        "benchmark": "CM52_U10_BENCHMARK",
        "pinned_benchmark_sha256": "CM52_U10_BENCHMARK_SHA256",
    }
    values = {k: os.environ.get(v, "") for k, v in names.items()}
    if not all(values.values()):
        raise EvidenceError("STAGE2_RESTORE_PREFLIGHT_INPUTS_REQUIRED")
    for key in ("artifact", "evaluation_receipt", "benchmark"):
        values[key] = Path(values[key])
        if not values[key].is_absolute():
            raise EvidenceError("STAGE2_RESTORE_PREFLIGHT_INPUTS_REQUIRED")
    return values


def preflight_arguments(repository):
    inputs = u10_inputs(repository)
    args = ["--repository", str(repository)]
    for key, value in inputs.items():
        args += [
            "--"
            + (
                "benchmark-sha256"
                if key == "pinned_benchmark_sha256"
                else key.replace("_", "-")
            ),
            str(value),
        ]
    return args


def preparation_inputs(*, repository, report_root, attempt_id):
    from pydantic import TypeAdapter

    validate_report_root(report_root, report_root, repository)
    prefix = f"cm-5.2/{attempt_id}"
    attempt = AttemptReceipt.model_validate(
        parse_json(read_private(report_root, prefix + "/attempt.json"))
    )
    if attempt.attempt != attempt_id or not attempt_id.endswith(attempt.revision[:12]):
        raise EvidenceError("ATTEMPT_ID_MISMATCH")
    TypeAdapter(Revision).validate_python(attempt.revision, strict=True)
    verify_execution_revision(repository, attempt.revision)
    images = {}
    for role in ("backend", "frontend"):
        fields = getattr(attempt, f"{role}_image").split(" ")
        if len(fields) != 2 or fields[1] != attempt.revision:
            raise EvidenceError("RELEASE_IMAGE_BINDING_MISMATCH")
        images[role] = fields[0]
    images["runner"] = os.environ.get("CM52_RUNNER_IMAGE_ID", images["backend"])
    images = ImagePins.model_validate(images).model_dump()
    evaluation = verify_evaluation(**u10_inputs(repository))
    if evaluation.receipt.evaluated_revision != attempt.revision:
        raise EvidenceError("RELEASE_EVALUATION_REVISION_MISMATCH")
    reset_path = Path(os.environ.get("CM52_RESET_FINAL_RECEIPT", ""))
    if not reset_path.is_absolute() or not reset_path.is_relative_to(report_root):
        raise EvidenceError("PREPARATION_RESET_REQUIRED")
    reset = component_ref(report_root, str(reset_path.relative_to(report_root)))
    reset_value = parse_json(read_private(report_root, reset.relative_path))
    for name in (
        "observer-baseline.json",
        "evidence/artifacts/PREFLIGHT/db-snapshot.json",
        "evidence/artifacts/PREFLIGHT/pending.json",
        "evidence/artifacts/PREFLIGHT/kafka.json",
    ):
        read_private(report_root, prefix + "/" + name)
    return attempt, images, reset, reset_value["run_id"]


def n8n_settings():
    path = Path(os.environ.get("CM52_N8N_OPERATOR_ENV_FILE", ""))
    if not path.is_absolute():
        raise EvidenceError("N8N_OPERATOR_ENV_REQUIRED")
    values = dotenv_values(
        stream=io.StringIO(read_env(path).decode()), interpolate=False
    )
    if not all(values.get(k) for k in ("N8N_BASE_URL", "N8N_USERNAME", "N8N_PASSWORD")):
        raise EvidenceError("N8N_OPERATOR_ENV_REQUIRED")
    if os.environ.get("CM52_ALLOW_TEMPORARY_N8N_PROBE") != "true":
        raise EvidenceError("N8N_TEMPORARY_PROBE_NOT_AUTHORIZED")
    workflows = {
        w: os.environ.get(f"CM52_N8N_{w}_ID", "") for w in ("WF2", "WF3", "WF4")
    }
    samples = {
        w: os.environ.get(f"CM52_N8N_{w}_SAMPLE_EXECUTION", "") for w in workflows
    }
    from app.agent.release_n8n import _id

    for identifier in (*workflows.values(), *samples.values()):
        _id(identifier)
    if len(set(workflows.values())) != 3:
        raise EvidenceError("N8N_CONFIG_WORKFLOWS_INVALID")
    return values, workflows, samples
