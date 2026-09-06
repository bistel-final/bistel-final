"""Stage2 cleanup/restore leaves; Bash owns their ordering and lifecycle lock.

Only exact prepared containers and the two named E2E infrastructure services
may be removed. No volumes/images/network pruning, reset, grants or Agent runs.
"""

import os
import re
from pathlib import Path

import httpx

from app.agent.evaluation_schemas import AgentEvaluationResponse
from app.agent.release_artifacts import EvidenceError, digest, parse_json, read_private
from app.agent.release_fence import ProductionFence
from app.agent.release_production import ProductionPorts, update_env
from app.agent.release_runtime import PROJECT, SERVICES, command
from app.agent.u10_images import docker_inspect

_FORMAT = (
    '{"id":{{json .Id}},"image":{{json .Image}},'
    '"running":{{json .State.Running}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"oneoff":{{json (index .Config.Labels "com.docker.compose.oneoff")}}}'
)


def docker_command(argv, *, timeout=30):
    return command(argv, env=dict(os.environ), timeout=timeout)


def verify_evaluation_api(previous_state, *, transport=None):
    """Fixed local GET, bounded body, no redirects/proxy/credentials/retries."""
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=15.0, transport=transport
        ) as client:
            with client.stream(
                "GET", "http://127.0.0.1:8080/api/agent/evaluations"
            ) as response:
                if response.status_code != 200:
                    raise ValueError
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise ValueError
        value = AgentEvaluationResponse.model_validate(
            parse_json(bytes(raw)), strict=True
        )
        for artifact, reason in (
            (value.fault_5class, value.fault_5class_empty_reason),
            (value.golden_flow, value.golden_flow_empty_reason),
        ):
            if previous_state == "empty":
                if artifact is not None or reason != "NOT_CONFIGURED":
                    raise ValueError
            elif previous_state != "bound" or artifact is None or reason is not None:
                raise ValueError
    except Exception:
        raise EvidenceError("STAGE2_RESTORE_EVALUATION_API_FAILED") from None


def e2e_inventory(*, run=docker_command):
    raw = run(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
        ]
    )
    ids = raw.splitlines()
    if (
        len(ids) > 5
        or len(set(ids)) != len(ids)
        or any(not re.fullmatch(rb"[0-9a-f]{64}", cid) for cid in ids)
    ):
        raise EvidenceError("STAGE2_CLEANUP_POPULATION_INVALID")
    return [cid.decode("ascii") for cid in ids]


def cleanup_e2e(prepared, *, run=docker_command):
    ids = e2e_inventory(run=run)
    if not ids:
        return {"result": "NOT_ATTEMPTED", "basis": "CLEANUP_E2E_ABSENT"}
    roles = {service: role for role, service in SERVICES.items()}
    seen, before = set(), {}
    for cid in ids:
        value = parse_json(
            run(["docker", "container", "inspect", "--format", _FORMAT, cid])
        )
        if (
            type(value) is not dict
            or set(value) != {"id", "image", "running", "project", "service", "oneoff"}
            or value["id"] != cid
            or value["project"] != PROJECT
            or value["oneoff"] != "False"
            or type(value["running"]) is not bool
            or value["service"] not in {*roles, "kafka", "mes-mock"}
            or value["service"] in seen
        ):
            raise EvidenceError("STAGE2_CLEANUP_TARGET_INVALID")
        role = roles.get(value["service"])
        if role is not None and (
            cid != getattr(prepared.containers, role).container_id
            or value["image"] != getattr(prepared.images, role).image_id
        ):
            raise EvidenceError("STAGE2_CLEANUP_TARGET_DRIFT")
        seen.add(value["service"])
        before[cid] = value
    # Recheck the WHOLE set before any stop; remove immutable IDs only.
    if set(e2e_inventory(run=run)) != set(ids):
        raise EvidenceError("STAGE2_CLEANUP_TARGET_DRIFT")
    for cid, value in before.items():
        if (
            parse_json(
                run(["docker", "container", "inspect", "--format", _FORMAT, cid])
            )
            != value
        ):
            raise EvidenceError("STAGE2_CLEANUP_TARGET_DRIFT")
    run(["docker", "stop", "--time", "30", *ids], timeout=180)
    run(["docker", "container", "rm", *ids], timeout=60)
    if e2e_inventory(run=run):
        raise EvidenceError("STAGE2_CLEANUP_REMAINS")
    return {
        "result": "OK",
        "basis": "Scoped E2E containers stopped and removed; volumes retained",
    }


def restore_level2(
    *,
    repository,
    report_root,
    env_file,
    prepared,
    preflight_arguments,
    ports_factory=ProductionPorts,
    fence_factory=ProductionFence,
    inspect_image=docker_inspect,
    run=docker_command,
    verify_api=verify_evaluation_api,
):
    # Even after a failed cleanup command, observe absence afresh. Never start
    # production while E2E (including Kafka on the same published port) remains.
    if e2e_inventory(run=run):
        raise EvidenceError("STAGE2_RESTORE_E2E_REMAINS")
    if repository.resolve() != Path(__file__).resolve().parents[3]:
        raise EvidenceError("RELEASE_REPOSITORY_INVALID")
    if not preflight_arguments:
        raise EvidenceError("STAGE2_RESTORE_PREFLIGHT_INPUTS_REQUIRED")
    images = {r: getattr(prepared.images, r).image_id for r in ("backend", "frontend")}
    ports = ports_factory(
        repository=repository,
        env_file=env_file,
        report_root=report_root,
        artifact=None,
        published_root=report_root / "cm-5.2" / prepared.attempt_id,
        revision=prepared.R,
        attempt_id=prepared.attempt_id,
        image_ids=images,
        preflight_arguments=preflight_arguments,
    )
    values = ports.values
    if (
        values.get("POSTGRES_DB") != "kosa_agent"
        or values.get("APP_DB_USER") != "kosa_app"
        or values.get("SOURCE_REVISION") != prepared.R
        or Path(values.get("AGENT_EVAL_REPORTS_DIR") or "").resolve()
        != report_root.resolve()
    ):
        raise EvidenceError("RELEASE_ENV_BINDING_MISMATCH")
    for image in images.values():
        if inspect_image("image", image) != {
            "image_id": image,
            "label_revision": prepared.R,
        }:
            raise EvidenceError("RELEASE_IMAGE_BINDING_MISMATCH")
    previous = (prepared.prev_fault_path or "", prepared.prev_golden_path or "")
    sources = {}
    if prepared.prev_state == "bound":
        for path, expected in zip(
            previous,
            (prepared.prev_fault_sha256, prepared.prev_golden_sha256),
            strict=True,
        ):
            relative = path.removeprefix("/reports/")
            payload = read_private(report_root, relative)
            if digest(payload) != expected:
                raise EvidenceError("PREPARATION_RESTORE_BINDING_INVALID")
            sources[relative] = payload
    with fence_factory(report_root, prepared.R, prepared.attempt_id) as fence:
        # Production can be down: use one inert, pinned read-only SQL process,
        # not a temporary application server and not the E2E run population.
        raw = ports.command(
            ports.compose
            + [
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "-T",
                "--entrypoint",
                "python",
                "backend",
                "-B",
                "/workspace/backend/scripts/read_release_quiescence.py",
            ],
            timeout=60,
        )
        quiet = parse_json(raw)
        if (
            quiet != {"schema_version": "level3-quiescence-v1", "active_runs": 0}
            or type(quiet.get("active_runs")) is not int
        ):
            raise EvidenceError("RELEASE_ACTIVE_RUNS")
        if e2e_inventory(run=run):
            raise EvidenceError("STAGE2_RESTORE_E2E_REMAINS")
        ports.env_bytes = update_env(
            env_file,
            ports.env_bytes,
            level=2,
            attempt=prepared.attempt_id,
            artifact_paths=previous,
        )
        # Restore existing Kafka/MES infrastructure without building or pulling;
        # the application pair is recreated separately by immutable image ID.
        ports.command(
            ports.compose
            + [
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "kafka",
                "mes-mock",
            ],
            timeout=180,
        )
        ports.recreate()
        result = ports.preflight(2)
        if (
            result.get("integrity") != "PASS"
            or result.get("profile") != "production_level2"
            or result.get("evaluated_revision") != prepared.R
        ):
            raise EvidenceError("RELEASE_PREFLIGHT_FAILED")
        cid = ports.containers(("backend",))["backend"]
        if prepared.prev_state == "bound":
            ports.command(
                [
                    "docker",
                    "exec",
                    cid,
                    "python",
                    "scripts/preflight_agent_evaluation_artifacts.py",
                    "--fault",
                    previous[0],
                    "--golden",
                    previous[1],
                    "--expect-fault-sha",
                    prepared.prev_fault_sha256,
                    "--expect-golden-sha",
                    prepared.prev_golden_sha256,
                    "--expect-revision",
                    prepared.prev_rev,
                    "--expect-container-revision",
                    prepared.R,
                    "--attempt-id",
                    prepared.prev_attempt,
                ],
                timeout=60,
            )
        # Read actual env even when old artifacts were empty; U10 Level2's
        # readback alone deliberately does not bind publication paths.
        raw = ports.command(
            [
                "docker",
                "exec",
                cid,
                "python",
                "-c",
                "import json,os; print(json.dumps([os.environ.get(k,'') for k in "
                "('AGENT_FAULT_EVAL_ARTIFACT_PATH','AGENT_GOLDEN_FLOW_SUMMARY_PATH')]))",
            ]
        )
        if parse_json(raw) != list(previous):
            raise EvidenceError("STAGE2_RESTORE_ARTIFACT_ENV_MISMATCH")
        verify_api(prepared.prev_state)
        if ports.containers(("backend",))["backend"] != cid:
            raise EvidenceError("STAGE2_RESTORE_RUNTIME_DRIFT")
        if any(
            read_private(report_root, name) != value for name, value in sources.items()
        ):
            raise EvidenceError("PREPARATION_RESTORE_BINDING_INVALID")
        fence.reopen(level=2)
    return {
        "result": "OK",
        "basis": "Pinned Level2 preflight and previous artifact pair verified",
    }
