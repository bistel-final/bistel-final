from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_postgres as postgres  # noqa: E402

pytestmark = pytest.mark.container


def test_pinned_image_manifest_supports_team_platforms() -> None:
    completed = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "postgres:16-alpine",
            "--format",
            "{{json .Manifest}}",
        ],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0
    manifest = json.loads(completed.stdout)
    assert f"postgres@{manifest['digest']}" == postgres.POSTGRES_IMAGE
    platforms = {
        (
            item.get("platform", {}).get("os"),
            item.get("platform", {}).get("architecture"),
            item.get("platform", {}).get("variant"),
        )
        for item in manifest["manifests"]
    }
    assert ("linux", "amd64", None) in platforms
    assert ("linux", "arm64", "v8") in platforms
