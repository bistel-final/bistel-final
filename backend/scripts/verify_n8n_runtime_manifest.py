"""Validate the secret-free n8n runtime pin for V5-C-4.2.

``--check-schema`` verifies the tracked JSON Schema without reading the manifest,
while the default command also requires and validates
``deploy/n8n/runtime-manifest.json``. Public inventory values must never be guessed or
represented as placeholders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
N8N_ROOT = REPOSITORY_ROOT / "deploy" / "n8n"
MANIFEST_PATH = N8N_ROOT / "runtime-manifest.json"
SCHEMA_PATH = N8N_ROOT / "schemas" / "runtime-manifest.schema.json"

FORMAT_VERSION = 1
WORKFLOW_FILES = (
    "WF2-notify-email.json",
    "WF3-mes-hold.json",
    "WF4-result-writeback.json",
)
STABLE_SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
EXPECTED_N8N_VERSION = "2.32.7"
EXPECTED_N8N_MANIFEST_LIST_DIGEST = (
    "sha256:882b126a8ddd0646e7d17ec47630e7704615e4647f3363471859fddc3f8946e2"
)
EXPECTED_N8N_PLATFORM_DIGESTS = {
    "linux/amd64": (
        "sha256:60dac72c8a23a3ad0921fb9e8e4cd8a67981b4377ea2caca7c672bed6c0c6886"
    ),
    "linux/arm64": (
        "sha256:baeb79dee61c89e9e4369a3b124b4cc66d88bf3250bc5e8d6f7dd00a6546077c"
    ),
}


class StrictModel(BaseModel):
    """Reject fields that could hide an unreviewed runtime setting."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlatformDigests(StrictModel):
    linux_amd64: str = Field(alias="linux/amd64", pattern=DIGEST_PATTERN)
    linux_arm64: str = Field(alias="linux/arm64", pattern=DIGEST_PATTERN)


class N8nImagePin(StrictModel):
    repository: Literal["n8nio/n8n"]
    version: str = Field(pattern=STABLE_SEMVER_PATTERN)
    manifest_list_digest: str = Field(pattern=DIGEST_PATTERN)
    platform_digests: PlatformDigests

    @model_validator(mode="after")
    def matches_reviewed_target(self) -> N8nImagePin:
        platform_digests = self.platform_digests.model_dump(by_alias=True)
        if (
            self.version != EXPECTED_N8N_VERSION
            or self.manifest_list_digest != EXPECTED_N8N_MANIFEST_LIST_DIGEST
            or platform_digests != EXPECTED_N8N_PLATFORM_DIGESTS
        ):
            raise ValueError("n8n image does not match the reviewed target pin")
        return self


class RuntimeTopology(StrictModel):
    database_type: Literal["sqlite", "postgresdb"]
    execution_mode: Literal["regular", "queue"]
    main_worker: Literal["single", "separated"]

    @model_validator(mode="after")
    def topology_is_coherent(self) -> RuntimeTopology:
        if self.execution_mode == "regular" and self.main_worker != "single":
            raise ValueError("regular execution requires a single main/worker topology")
        if self.execution_mode == "queue" and self.main_worker != "separated":
            raise ValueError(
                "queue execution requires a separated main/worker topology"
            )
        return self


class RunnerImagePin(StrictModel):
    repository: Literal["n8nio/runners"]
    version: str = Field(pattern=STABLE_SEMVER_PATTERN)
    manifest_list_digest: str = Field(pattern=DIGEST_PATTERN)
    platform_digests: PlatformDigests


class InternalRunner(StrictModel):
    mode: Literal["internal"]
    image: None


class ExternalRunner(StrictModel):
    mode: Literal["external"]
    image: RunnerImagePin


RunnerPin = Annotated[
    InternalRunner | ExternalRunner,
    Field(discriminator="mode"),
]


class WorkflowSourceHashes(StrictModel):
    wf2_notify_email: str = Field(alias="WF2-notify-email.json", pattern=DIGEST_PATTERN)
    wf3_mes_hold: str = Field(alias="WF3-mes-hold.json", pattern=DIGEST_PATTERN)
    wf4_result_writeback: str = Field(
        alias="WF4-result-writeback.json", pattern=DIGEST_PATTERN
    )


class RuntimeManifest(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "$id": "https://bistel-fdc.local/schemas/n8n-runtime-manifest-v1.json"
        },
    )

    format_version: Literal[FORMAT_VERSION]
    n8n: N8nImagePin
    runtime: RuntimeTopology
    task_runner: RunnerPin
    workflow_source_sha256: WorkflowSourceHashes

    @model_validator(mode="after")
    def runner_matches_n8n(self) -> RuntimeManifest:
        if isinstance(self.task_runner, ExternalRunner):
            runner = self.task_runner.image
            if runner.version != self.n8n.version:
                raise ValueError("external runner version must match the n8n version")
        return self


def validate_running_image(
    manifest: RuntimeManifest,
    *,
    platform: str,
    digest: str,
) -> None:
    """Check Monday's host attestation against the cross-platform target pin."""

    platform_digests = manifest.n8n.platform_digests.model_dump(by_alias=True)
    if platform not in platform_digests:
        raise RuntimeManifestError("running n8n platform is not allowlisted")
    if digest != platform_digests[platform]:
        raise RuntimeManifestError("running n8n child digest does not match the pin")


class RuntimeManifestError(RuntimeError):
    """A sanitized manifest validation failure."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def workflow_source_hashes(workflow_root: Path = N8N_ROOT) -> dict[str, str]:
    missing = [name for name in WORKFLOW_FILES if not (workflow_root / name).is_file()]
    if missing:
        raise RuntimeManifestError(
            "workflow source file is missing: " + ", ".join(missing)
        )
    return {name: _sha256_file(workflow_root / name) for name in WORKFLOW_FILES}


def _sanitized_validation_message(error: ValidationError) -> str:
    details: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "manifest"
        details.append(f"{location}: {item['msg']}")
    return "invalid n8n runtime manifest: " + "; ".join(details)


def validate_runtime_manifest(
    payload: Any,
    *,
    workflow_root: Path = N8N_ROOT,
) -> RuntimeManifest:
    try:
        manifest = RuntimeManifest.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeManifestError(_sanitized_validation_message(exc)) from None

    expected_hashes = workflow_source_hashes(workflow_root)
    recorded_hashes = manifest.workflow_source_sha256.model_dump(by_alias=True)
    mismatches = [
        name
        for name in WORKFLOW_FILES
        if recorded_hashes[name] != expected_hashes[name]
    ]
    if mismatches:
        raise RuntimeManifestError(
            "workflow source hash mismatch: " + ", ".join(mismatches)
        )
    return manifest


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeManifestError(f"required file is missing: {path.name}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeManifestError(f"cannot read valid JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise RuntimeManifestError(f"JSON root must be an object: {path.name}")
    return payload


def generated_schema() -> dict[str, Any]:
    return RuntimeManifest.model_json_schema(by_alias=True)


def schema_text() -> str:
    return (
        json.dumps(generated_schema(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def write_schema(path: Path = SCHEMA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_text(), encoding="utf-8")


def check_schema(path: Path = SCHEMA_PATH) -> None:
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeManifestError(f"required file is missing: {path.name}") from None
    if actual != schema_text():
        raise RuntimeManifestError("tracked runtime manifest schema is stale")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    parser.add_argument(
        "--check-schema",
        action="store_true",
        help="validate only the tracked schema; the public manifest may be absent",
    )
    parser.add_argument(
        "--write-schema",
        action="store_true",
        help="regenerate the tracked JSON Schema and exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.write_schema:
            write_schema(args.schema)
            return 0
        check_schema(args.schema)
        if args.check_schema:
            return 0
        payload = load_json_object(args.manifest)
        validate_runtime_manifest(payload)
    except RuntimeManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("n8n runtime manifest: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
