"""Preflight check 8: private config/DB readback from independently pinned IDs.

No lifecycle changes, runtime workload, env dump or enable permission. Callers
must separately verify image bindings and the source of the expected attempt ID.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Any, Literal

from app.agent.release_artifacts import EvidenceError, EvidenceModel, parse_json
from app.agent.runtime_readback import PROFILES, validate_readback
from app.agent.u10_images import Profile


class RuntimeReadback(EvidenceModel):
    schema_version: Literal["agent-runtime-readback-v1"]
    status: Literal["PASS"]
    profile: Profile
    database: str
    database_user: str
    autonomy_level: int
    level3_enabled: bool
    demo_ack: str | None
    ack_matches_receipt: bool
    budget_policy: dict[str, int]


class RuntimeObservation(EvidenceModel):
    profile: Profile
    container_ids: dict[str, str]
    readbacks: dict[str, RuntimeReadback]


def _profile(profile: str) -> None:
    if type(profile) is not str or profile not in PROFILES:
        raise EvidenceError("U10_RUNTIME_PROFILE_INVALID")


def _container_id(value: str) -> None:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceError("U10_RUNTIME_CONTAINER_INVALID")


def docker_readback(container_id: str, profile: Profile) -> dict[str, Any]:
    """Run the read-only script by pinned ID, without environment overrides."""
    _profile(profile)
    _container_id(container_id)
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-B",
                "/workspace/backend/scripts/read_agent_runtime.py",
                "--profile",
                profile,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode or len(result.stdout) > 16384:
            raise EvidenceError("U10_RUNTIME_READ_FAILED")
        payload = parse_json(result.stdout)
        if type(payload) is not dict:
            raise EvidenceError("U10_RUNTIME_READ_FAILED")
        return payload
    except (OSError, subprocess.TimeoutExpired, EvidenceError):
        raise EvidenceError("U10_RUNTIME_READ_FAILED") from None


Read = Callable[[str, Profile], dict[str, Any]]


def verify_runtime_readbacks(
    *,
    profile: Profile,
    container_ids: dict[str, str],
    expected_attempt_id: str | None = None,
    read: Read = docker_readback,
) -> RuntimeObservation:
    """Check backend (+ E2E runner); frontend has no Python config/DB identity.

    Production L3 requires the caller's independently verified attempt ID in
    addition to the container's legacy receipt match. Neither replaces the
    full robustness/delivery validators or the final three-gate decision.
    """
    _profile(profile)
    roles = ("backend", "runner") if profile == "e2e_level3" else ("backend",)
    if type(container_ids) is not dict or set(container_ids) != set(roles):
        raise EvidenceError("U10_RUNTIME_ROLES_INVALID")
    ids = container_ids.copy()
    for identifier in ids.values():
        _container_id(identifier)
    if len(set(ids.values())) != len(ids):
        raise EvidenceError("U10_RUNTIME_CONTAINER_INVALID")
    if profile == "production_level3":
        if type(expected_attempt_id) is not str or not re.fullmatch(
            r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", expected_attempt_id
        ):
            raise EvidenceError("U10_RUNTIME_ATTEMPT_INVALID")
    elif expected_attempt_id is not None:
        raise EvidenceError("U10_RUNTIME_ATTEMPT_INVALID")

    readbacks = {}
    for role in roles:
        try:
            payload = read(ids[role], profile)
            if type(payload) is not dict:
                raise ValueError("payload type invalid")
            observed = RuntimeReadback.model_validate(payload)
            if observed.profile != profile:
                raise ValueError("profile mismatch")
            validate_readback(observed.model_dump(), profile)
            if (
                profile == "production_level3"
                and observed.demo_ack != expected_attempt_id
            ):
                raise ValueError("attempt mismatch")
        except (ValueError, OSError, subprocess.TimeoutExpired):
            raise EvidenceError("U10_RUNTIME_READBACK_INVALID") from None
        readbacks[role] = observed
    return RuntimeObservation(profile=profile, container_ids=ids, readbacks=readbacks)
