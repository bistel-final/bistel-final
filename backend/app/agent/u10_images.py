"""U10 preflight image/tree/container checks only, not an enable/deploy gate.

Inspect projections deliberately exclude Config.Env, mounts and other labels.
Callers must supply independently pinned image/container IDs. No discovery by
mutable tag, Docker lifecycle action, readiness request or artifact write occurs.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.agent.release_artifacts import EvidenceError, EvidenceModel, parse_json
from app.agent.release_prepared import RuntimeContainer, RuntimeImage
from app.agent.u10_revision import RevisionTrees, read_revision_identity

Profile = Literal["production_level2", "e2e_level3", "production_level3"]
Kind = Literal["image", "container"]
Inspect = Callable[[Kind, str], dict[str, Any]]

# Match cm52_common.sh and the actual Compose service key, not the role alias.
_PROFILES = {
    "production_level2": (
        "bistel-team",
        {"backend": "backend", "frontend": "frontend"},
    ),
    "production_level3": (
        "bistel-team",
        {"backend": "backend", "frontend": "frontend"},
    ),
    "e2e_level3": (
        "bistel-team-e2e",
        {"backend": "backend", "frontend": "frontend", "runner": "e2e-runner"},
    ),
}
_IMAGE_FORMAT = (
    '{"image_id":{{json .Id}},'
    '"label_revision":{{json (index .Config.Labels '
    '"org.opencontainers.image.revision")}}}'
)
_CONTAINER_FORMAT = (
    '{"container_id":{{json .Id}},"image_id":{{json .Image}},'
    '"running":{{json .State.Running}},"status":{{json .State.Status}},'
    '"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},'
    '"started_at":{{json .State.StartedAt}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
)


def docker_inspect(kind: Kind, identifier: str) -> dict[str, Any]:
    """Read a small allowlisted projection by immutable ID; no raw inspect dump."""
    pattern = r"sha256:[0-9a-f]{64}" if kind == "image" else r"[0-9a-f]{64}"
    if (
        kind not in ("image", "container")
        or type(identifier) is not str
        or not re.fullmatch(pattern, identifier)
    ):
        raise EvidenceError("U10_DOCKER_ID_INVALID")
    try:
        result = subprocess.run(
            [
                "docker",
                kind,
                "inspect",
                "--format",
                _IMAGE_FORMAT if kind == "image" else _CONTAINER_FORMAT,
                identifier,
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode or len(result.stdout) > 16384:
            raise EvidenceError("U10_DOCKER_INSPECT_INVALID")
        payload = parse_json(result.stdout)
        if not isinstance(payload, dict):
            raise EvidenceError("U10_DOCKER_INSPECT_INVALID")
        return payload
    except (OSError, subprocess.TimeoutExpired, EvidenceError):
        raise EvidenceError("U10_DOCKER_INSPECT_INVALID") from None


class ImageBindings(EvidenceModel):
    profile: Profile
    repository_root: str
    evaluated_revision: str
    evaluated_tree_oid: RevisionTrees
    images: dict[str, RuntimeImage]
    containers: dict[str, RuntimeContainer]


def _container(
    payload: Any, identifier: str, image_id: str, project: str, service: str
) -> RuntimeContainer:
    if not isinstance(payload, dict) or set(payload) != {
        "container_id",
        "image_id",
        "running",
        "status",
        "paused",
        "restarting",
        "started_at",
        "project",
        "service",
    }:
        raise EvidenceError("U10_CONTAINER_INSPECT_INVALID")
    if (
        payload["container_id"] != identifier
        or payload["image_id"] != image_id
        or payload["project"] != project
        or payload["service"] != service
    ):
        raise EvidenceError("U10_CONTAINER_BINDING_MISMATCH")
    if (
        payload["running"] is not True
        or payload["status"] != "running"
        or payload["paused"] is not False
        or payload["restarting"] is not False
    ):
        raise EvidenceError("U10_CONTAINER_NOT_RUNNING")
    try:
        return RuntimeContainer(
            container_id=identifier, started_at=payload["started_at"]
        )
    except ValidationError:
        raise EvidenceError("U10_CONTAINER_INSPECT_INVALID") from None


def verify_image_bindings(
    *,
    repository: Path,
    evaluated_revision: str,
    expected_trees: RevisionTrees,
    profile: Profile,
    expected_image_ids: dict[str, str],
    container_ids: dict[str, str],
    inspect: Inspect = docker_inspect,
) -> ImageBindings:
    """Check preflight items 4/5/6; never claim full integrity or enable permission.

    expected_trees belongs to the independently validated evaluation receipt;
    image IDs belong to the selected build, not to the container being inspected.
    Profile expectations are code-owned; E2E runner may share backend's image but
    must have its own running container, verified under the e2e-runner service.
    """
    if type(profile) is not str or profile not in _PROFILES:
        raise EvidenceError("U10_IMAGE_PROFILE_INVALID")
    project, services = _PROFILES[profile]
    if type(evaluated_revision) is not str or not re.fullmatch(
        r"[0-9a-f]{40}", evaluated_revision
    ):
        raise EvidenceError("U10_REVISION_INVALID")
    if (
        not isinstance(expected_image_ids, dict)
        or not isinstance(container_ids, dict)
        or set(expected_image_ids) != set(services)
        or set(container_ids) != set(services)
    ):
        raise EvidenceError("U10_IMAGE_ROLES_INVALID")
    images_pin, containers_pin = dict(expected_image_ids), dict(container_ids)
    if any(
        type(v) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", v)
        for v in images_pin.values()
    ) or any(
        type(v) is not str or not re.fullmatch(r"[0-9a-f]{64}", v)
        for v in containers_pin.values()
    ):
        raise EvidenceError("U10_DOCKER_ID_INVALID")
    if len(set(containers_pin.values())) != len(services):
        raise EvidenceError("U10_CONTAINER_REUSED")
    expected_trees = RevisionTrees.model_validate(
        expected_trees.model_dump()
    ).model_copy(deep=True)
    identity = read_revision_identity(repository, evaluated_revision)
    if identity.evaluated_tree_oid != expected_trees:
        raise EvidenceError("U10_EVALUATED_TREE_MISMATCH")
    images, containers = {}, {}
    for role, service in services.items():
        containers[role] = _container(
            inspect("container", containers_pin[role]),
            containers_pin[role],
            images_pin[role],
            project,
            service,
        )
        try:
            image = RuntimeImage.model_validate(inspect("image", images_pin[role]))
        except ValidationError:
            raise EvidenceError("U10_IMAGE_INSPECT_INVALID") from None
        if (
            image.image_id != images_pin[role]
            or image.label_revision != evaluated_revision
        ):
            raise EvidenceError("U10_IMAGE_BINDING_MISMATCH")
        # Resolve the image label explicitly; never substitute the current HEAD.
        label_identity = read_revision_identity(repository, image.label_revision)
        if label_identity.evaluated_tree_oid != expected_trees:
            raise EvidenceError("U10_IMAGE_TREE_MISMATCH")
        images[role] = image
    # Detect restart/state/image drift over this observation window. This is not
    # an atomic Docker snapshot or a lock against later lifecycle changes.
    for role, service in services.items():
        after = _container(
            inspect("container", containers_pin[role]),
            containers_pin[role],
            images_pin[role],
            project,
            service,
        )
        if after != containers[role]:
            raise EvidenceError("U10_CONTAINER_DRIFT")
    return ImageBindings(
        profile=profile,
        repository_root=identity.repository_root,
        evaluated_revision=evaluated_revision,
        evaluated_tree_oid=expected_trees,
        images=images,
        containers=containers,
    )
