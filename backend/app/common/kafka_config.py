"""Kafka client 설정과 Docker secret-file 읽기 공통 계약."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SECRET_BYTES = 4096
PLAINTEXT_CREDENTIAL_KEYS = ("KAFKA_CLIENT_USER", "KAFKA_CLIENT_PASSWORD")


class KafkaConfigError(RuntimeError):
    """Kafka 설정이 최소권한 secret-file 계약을 만족하지 않는다."""


class KafkaNotConfiguredError(KafkaConfigError):
    """필수 Kafka 설정이나 secret file이 없다."""


class KafkaContractError(KafkaConfigError):
    """Kafka 설정이나 secret 값이 고정 계약과 다르다."""


def read_secret_file(values: Mapping[str, str], key: str) -> str:
    raw_path = values.get(key)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise KafkaNotConfiguredError("Kafka secret file이 설정되지 않았습니다")
    try:
        with Path(raw_path.strip()).open("rb") as stream:
            raw = stream.read(MAX_SECRET_BYTES + 1)
        if len(raw) > MAX_SECRET_BYTES:
            raise KafkaContractError(
                "Kafka secret file 크기가 허용 범위를 벗어났습니다"
            )
        value = raw.decode("utf-8").strip()
    except KafkaConfigError:
        raise
    except OSError:
        raise KafkaNotConfiguredError("Kafka secret file을 읽을 수 없습니다") from None
    except (UnicodeError, ValueError):
        raise KafkaContractError("Kafka secret file 형식이 올바르지 않습니다") from None
    if not value or any(character.isspace() for character in value):
        raise KafkaContractError("Kafka secret 값 형식이 올바르지 않습니다")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class KafkaClientConfig:
    bootstrap_servers: str
    username: str
    password: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> KafkaClientConfig:
        if any(values.get(key, "").strip() for key in PLAINTEXT_CREDENTIAL_KEYS):
            raise KafkaContractError(
                "Kafka 평문 credential fallback은 허용되지 않습니다"
            )
        bootstrap = values.get("KAFKA_BOOTSTRAP_INTERNAL", "").strip()
        if not bootstrap:
            raise KafkaNotConfiguredError("Kafka bootstrap 설정이 없습니다")
        if any(character.isspace() for character in bootstrap) or ":" not in bootstrap:
            raise KafkaContractError("Kafka bootstrap 설정이 올바르지 않습니다")
        return cls(
            bootstrap_servers=bootstrap,
            username=read_secret_file(values, "KAFKA_CLIENT_USER_FILE"),
            password=read_secret_file(values, "KAFKA_CLIENT_PASSWORD_FILE"),
        )

    def common_settings(self) -> dict[str, Any]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": "SASL_PLAINTEXT",
            "sasl.mechanism": "PLAIN",
            "sasl.username": self.username,
            "sasl.password": self.password,
        }
