#!/usr/bin/env python3
"""별도 Docker network에서 Kafka advertised listener와 인증 경계를 검증한다."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from preflight_team_env import parse_env_file, validate

KAFKA_IMAGE = "apache/kafka:3.9.1"
REQUIRED_TOPICS = frozenset({"fdc.actions", "fdc.actions.result"})
SCRIPT_PATH = Path(__file__).resolve().parent / "kafka" / "manage_topics.sh"


def _run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


def _probe_command(
    *,
    network: str,
    user_file: Path,
    password_file: Path,
    operation: str,
    bootstrap: str,
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--volume",
        f"{SCRIPT_PATH}:/opt/team/manage_topics.sh:ro",
        "--volume",
        f"{user_file}:/run/secrets/kafka_client_user:ro",
        "--volume",
        f"{password_file}:/run/secrets/kafka_client_password:ro",
        "--entrypoint",
        "/opt/team/manage_topics.sh",
        KAFKA_IMAGE,
        operation,
        bootstrap,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args(argv)

    values, parse_findings = parse_env_file(args.env_file)
    findings = parse_findings or validate(values)
    if findings:
        print("ERROR TEAM_ENV_INVALID")
        return 1

    network = f"bistel-kafka-probe-{os.getpid()}"
    bootstrap = f"{values['KAFKA_ADVERTISED_HOST']}:53004"
    created = _run(["docker", "network", "create", network])
    if created.returncode != 0:
        print("ERROR PROBE_NETWORK_CREATE_FAILED")
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="bistel-kafka-probe-") as tmp:
            tmp_path = Path(tmp)
            user_file = tmp_path / "client-user"
            password_file = tmp_path / "client-password"
            wrong_password_file = tmp_path / "wrong-password"
            _write_secret(user_file, values["KAFKA_CLIENT_USER"])
            _write_secret(password_file, values["KAFKA_CLIENT_PASSWORD"])
            _write_secret(wrong_password_file, "intentionally-wrong-credential")

            positive = _run(
                _probe_command(
                    network=network,
                    user_file=user_file,
                    password_file=password_file,
                    operation="health",
                    bootstrap=bootstrap,
                )
            )
            if positive.returncode != 0:
                print("ERROR KAFKA_EXTERNAL_AUTH_FAILED")
                return 1

            listed = _run(
                _probe_command(
                    network=network,
                    user_file=user_file,
                    password_file=password_file,
                    operation="list-topics",
                    bootstrap=bootstrap,
                )
            )
            topics = frozenset(listed.stdout.splitlines())
            if listed.returncode != 0 or not REQUIRED_TOPICS <= topics:
                print("ERROR KAFKA_REQUIRED_TOPICS_MISSING")
                return 1

            negative = _run(
                _probe_command(
                    network=network,
                    user_file=user_file,
                    password_file=wrong_password_file,
                    operation="health",
                    bootstrap=bootstrap,
                )
            )
            if negative.returncode == 0:
                print("ERROR KAFKA_INVALID_CREDENTIAL_ACCEPTED")
                return 1
    except subprocess.TimeoutExpired:
        print("ERROR KAFKA_PROBE_TIMEOUT")
        return 1
    finally:
        _run(["docker", "network", "rm", network], timeout=30)

    print("OK KAFKA_EXTERNAL_PROBE topics=2 invalid_credential=rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
