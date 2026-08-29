from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import rehearsal_neo4j as neo4j_rehearsal  # noqa: E402


class DockerFake:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.alive = False
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] > 0
        operation = argv[1:]
        if operation[0] == "pull":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if operation[0] == "run":
            self.alive = True
            return subprocess.CompletedProcess(argv, 0, "neo4j-container\n", "")
        if operation[0] == "port":
            return subprocess.CompletedProcess(argv, 0, "127.0.0.1:57687\n", "")
        if operation[:2] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(
                argv, 0, "neo4j-container\n" if self.alive else "", ""
            )
        if operation[:2] == ["rm", "-f"]:
            self.alive = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


def test_lifecycle_uses_exact_image_secret_env_and_owned_cleanup() -> None:
    docker = DockerFake()

    with neo4j_rehearsal.one_off_neo4j(
        command_runner=docker,
        host_probe=lambda *_: True,
        sleep=lambda _: None,
    ) as endpoint:
        assert endpoint.port == 57687
        assert endpoint.uri == "bolt://127.0.0.1:57687"
        assert docker.alive is True
        assert endpoint.password not in repr(endpoint)

    assert docker.alive is False
    pull_argv = next(call[0] for call in docker.calls if call[0][1] == "pull")
    run_argv, run_kwargs = next(call for call in docker.calls if call[0][1] == "run")
    assert pull_argv[-1] == neo4j_rehearsal.NEO4J_IMAGE
    assert run_argv[-1] == neo4j_rehearsal.NEO4J_IMAGE
    assert "NEO4J_AUTH" in run_argv
    secret = run_kwargs["env"]["NEO4J_AUTH"]
    assert all(secret not in argument for argument in run_argv)
    assert any(f"{neo4j_rehearsal.LABEL_KEY}=" in argument for argument in run_argv)


def test_ready_timeout_is_sanitized_and_cleanup_runs() -> None:
    docker = DockerFake(ready=False)
    ticks = iter((0.0, 2.0, 2.0))

    with pytest.raises(neo4j_rehearsal.Neo4jRehearsalError) as raised:
        with neo4j_rehearsal.one_off_neo4j(
            command_runner=docker,
            host_probe=lambda *_: False,
            monotonic=lambda: next(ticks),
            sleep=lambda _: None,
            ready_timeout=1.0,
        ):
            pytest.fail("must not yield")

    assert raised.value.reason_code == "REHEARSAL_NOT_READY"
    assert docker.alive is False


def test_run_timeout_uses_label_fallback_cleanup() -> None:
    class LostResponse(DockerFake):
        def __call__(self, argv: list[str], **kwargs: Any) -> Any:
            if argv[1] == "run":
                self.calls.append((argv, kwargs))
                self.alive = True
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return super().__call__(argv, **kwargs)

    docker = LostResponse()
    with pytest.raises(neo4j_rehearsal.Neo4jRehearsalError) as raised:
        with neo4j_rehearsal.one_off_neo4j(
            command_runner=docker,
            host_probe=lambda *_: True,
            sleep=lambda _: None,
        ):
            pytest.fail("must not yield")

    assert raised.value.reason_code == "DOCKER_TIMEOUT"
    assert docker.alive is False
    assert any(
        "label=fdc.neo4j-timeout.run=" in " ".join(call[0]) for call in docker.calls
    )


def test_module_has_only_standard_library_top_level_imports() -> None:
    source = (SCRIPTS_ROOT / "rehearsal_neo4j.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
    }
    imports.update(
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imports <= {
        "__future__",
        "os",
        "re",
        "secrets",
        "subprocess",
        "time",
        "collections",
        "contextlib",
        "dataclasses",
    }
    assert "kosa165.iptime.org" not in source
    assert "fcntl" not in source
