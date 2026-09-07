"""Current production LLM projection; no provider request or credential output.

Unlike U10's experiment, production does not pass a seed and does not force
temperature zero. Keep that distinction explicit instead of attesting synthetic
experiment settings for the real Stage2 graph.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from app.agent.release_artifacts import (
    EvidenceError,
    EvidenceModel,
    Sha256,
    canonical_json,
    digest,
    parse_json,
)
from app.agent.release_prepared import Attempt
from app.agent.u10_comparison import Identifier


class RuntimeLlmConfiguration(EvidenceModel):
    hypothesis_model_revision: Identifier
    selector_model_revision: Identifier
    hypothesis_prompt_version: Literal[
        "agent-hypothesis-v3-ko1", "agent-hypothesis-v3-ko2"
    ]
    selector_prompt_version: Literal[
        "agent-react-v2-ko1", "agent-react-v2-ko2", "agent-react-v2-ko3"
    ]
    temperature: float | None = Field(ge=0, le=2, allow_inf_nan=False)
    seed: None


class ModelContext(EvidenceModel):
    schema_version: Literal["level3-model-context-v1"]
    llm: RuntimeLlmConfiguration
    endpoint_sha256: Sha256
    model_config_digest: Sha256
    published_attempt_id: Attempt | None
    published_artifact_sha256: dict[str, Sha256] | None = None


def published_hashes(attempt, *, root=Path("/reports")):
    """Hash actual mounted publications by anchored no-follow descriptors.

    Host-owned 0600 files may have a different UID inside the read-only bind
    mount. Do not weaken no-follow/regular/single-link/size validation for that.
    """
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", attempt):
        raise EvidenceError("RELEASE_PUBLICATION_INVALID")
    fds = []
    try:
        fds.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in ("cm-5.2", attempt):
            fds.append(
                os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]
                )
            )
        result = {}
        for name in ("attempt.json", "golden-flow.json", "fault-5class.json"):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[-1])
            with os.fdopen(fd, "rb") as f:
                before = os.fstat(f.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_nlink != 1
                    or before.st_size > 4 * 1024 * 1024
                ):
                    raise ValueError
                raw = f.read(4 * 1024 * 1024 + 1)
                after = os.fstat(f.fileno())
                stable = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if (
                    any(getattr(before, k) != getattr(after, k) for k in stable)
                    or len(raw) != before.st_size
                ):
                    raise ValueError
                result[name] = digest(raw)
        return result
    except Exception:
        raise EvidenceError("RELEASE_PUBLICATION_INVALID") from None
    finally:
        for fd in reversed(fds):
            os.close(fd)


def collect_model_context() -> ModelContext:
    # Lazy imports preserve offline CLI/import safety. _resolve_endpoint reads
    # config only; do not instantiate RealProvider (which performs DNS lookup).
    try:
        from app.agent.prompts import PROMPT_VERSION
        from app.agent.react import REACT_PROMPT_VERSION
        from app.common import config, llm

        endpoint, _key = llm._resolve_endpoint()
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or re.search(r"[\s\\]", endpoint)
        ):
            raise ValueError
        model = RuntimeLlmConfiguration(
            hypothesis_model_revision=llm.LLM_MODEL_MAIN,
            selector_model_revision=llm.LLM_MODEL_MAIN,
            hypothesis_prompt_version=PROMPT_VERSION,
            selector_prompt_version=REACT_PROMPT_VERSION,
            temperature=None
            if llm._is_reasoning_model(llm.LLM_MODEL_MAIN)
            else llm.LLM_TEMPERATURE,
            seed=None,
        )
        endpoint_sha = digest(endpoint.encode("utf-8"))
        fault, golden = (
            config.AGENT_FAULT_EVAL_ARTIFACT_PATH,
            config.AGENT_GOLDEN_FLOW_SUMMARY_PATH,
        )
        attempt = None
        if fault or golden:
            match = re.fullmatch(
                r"/reports/cm-5\.2/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12})/fault-5class\.json",
                fault or "",
            )
            if not match or golden != f"/reports/cm-5.2/{match[1]}/golden-flow.json":
                raise ValueError
            attempt = match[1]
        return ModelContext(
            schema_version="level3-model-context-v1",
            llm=model,
            endpoint_sha256=endpoint_sha,
            model_config_digest=digest(
                canonical_json(
                    dict(llm=model.model_dump(), endpoint_sha256=endpoint_sha)
                )
            ),
            published_attempt_id=attempt,
            published_artifact_sha256=None
            if attempt is None
            else published_hashes(attempt),
        )
    except Exception:
        raise EvidenceError("RELEASE_MODEL_CONTEXT_UNAVAILABLE") from None


def docker_model_context(container_id: str) -> ModelContext:
    if type(container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise EvidenceError("RELEASE_MODEL_CONTAINER_INVALID")
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-B",
                "/workspace/backend/scripts/read_release_model.py",
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError
        return ModelContext.model_validate(parse_json(result.stdout))
    except Exception:
        raise EvidenceError("RELEASE_MODEL_CONTEXT_UNAVAILABLE") from None
