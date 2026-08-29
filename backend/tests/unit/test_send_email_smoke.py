from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import send_email_smoke as smoke  # noqa: E402

from app.agent.email_delivery import (  # noqa: E402
    EmailDeliveryOutcome,
    EmailDeliveryResult,
)


def _config(
    monkeypatch: pytest.MonkeyPatch, recipients: str = "one@example.com"
) -> None:
    monkeypatch.setattr(
        smoke.config,
        "N8N_WEBHOOK_URL",
        "http://localhost:5678/webhook/fdc-notify-email",
    )
    monkeypatch.setattr(smoke.config, "N8N_WEBHOOK_TIMEOUT_SEC", 30)
    monkeypatch.setattr(smoke.config, "N8N_WEBHOOK_SECRET", "unit-secret")
    monkeypatch.setattr(smoke.config, "AGENT_EMAIL_RECIPIENTS", recipients)


def _args(*extra: str) -> list[str]:
    return [
        "--warning-action-id",
        "ACT-C43000000000001",
        "--approval-action-id",
        "ACT-C43000000000002",
        "--approval-id",
        "APR-C43000000000001",
        *extra,
    ]


def test_preview_has_zero_db_calls_and_hides_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _config(monkeypatch)
    monkeypatch.setattr(
        smoke,
        "get_app_engine",
        lambda: pytest.fail("preview must not access the database"),
    )
    assert smoke.main(_args()) == smoke.EXIT_CONFIRM_REQUIRED
    output = capsys.readouterr().err
    assert "one@example.com" not in output
    assert json.loads(output)["recipient_count"] == 1
    assert json.loads(output)["send_count"] == 2


@pytest.mark.parametrize("recipients", ["a@example.com,b@example.com", "bad"])
def test_smoke_requires_exactly_one_valid_recipient(
    monkeypatch: pytest.MonkeyPatch,
    recipients: str,
) -> None:
    _config(monkeypatch, recipients)
    assert smoke.main(_args()) == smoke.EXIT_USAGE


class _Scalar:
    def scalar_one(self) -> str:
        return smoke.TARGET_DATABASE


class _Connection:
    def execute(self, statement: Any) -> _Scalar:
        return _Scalar()


class _Engine:
    @contextmanager
    def connect(self):
        yield _Connection()

    @contextmanager
    def begin(self):
        yield object()


@pytest.mark.parametrize(
    ("warning_outcome", "approval_outcome"),
    [
        (EmailDeliveryOutcome.ACCEPTED, EmailDeliveryOutcome.ACCEPTED),
        (EmailDeliveryOutcome.SENT, EmailDeliveryOutcome.SENT),
        (EmailDeliveryOutcome.ACCEPTED, EmailDeliveryOutcome.SENT),
    ],
)
def test_confirmed_runner_accepts_successful_outcomes_and_calls_two_adapters(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    warning_outcome: EmailDeliveryOutcome,
    approval_outcome: EmailDeliveryOutcome,
) -> None:
    _config(monkeypatch)
    engine = _Engine()
    monkeypatch.setattr(smoke, "get_app_engine", lambda: engine)
    calls: list[tuple[str, ...]] = []

    class Service:
        def send_warning(self, action_id: str) -> EmailDeliveryResult:
            calls.append(("WARNING", action_id))
            return EmailDeliveryResult(
                action_id,
                warning_outcome,
                response_status=200,
            )

        def send_approval(
            self, action_id: str, approval_id: str
        ) -> EmailDeliveryResult:
            calls.append(("EQP_HOLD", action_id, approval_id))
            return EmailDeliveryResult(
                action_id,
                approval_outcome,
                response_status=200,
            )

    monkeypatch.setattr(
        smoke,
        "production_ports",
        lambda settings, transactions: SimpleNamespace(service=Service()),
    )
    code = smoke.main(
        _args("--target", smoke.TARGET_DATABASE, "--confirm", smoke.CONFIRMATION)
    )
    assert code == smoke.EXIT_OK
    assert [call[0] for call in calls] == ["WARNING", "EQP_HOLD"]
    output = capsys.readouterr().err
    assert "one@example.com" not in output
    assert json.loads(output)["completed_calls"] == 2
    assert json.loads(output)["outcomes"] == [
        warning_outcome.value,
        approval_outcome.value,
    ]
