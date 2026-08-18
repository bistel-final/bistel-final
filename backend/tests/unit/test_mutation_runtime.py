from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import mutation_runtime as runtime  # noqa: E402
from db_target import BootstrapTarget  # noqa: E402


class Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> None:
        self.statements.append(" ".join(statement.split()))


def _target() -> BootstrapTarget:
    return BootstrapTarget("host", 5432, "user", "pw", "kosa_agent", "runtime")


@pytest.mark.parametrize(
    ("readonly", "expected"),
    [
        (True, ["SET TRANSACTION READ ONLY"]),
        (False, []),
    ],
)
def test_read_committed_preserves_reference_runner_preamble(
    monkeypatch: pytest.MonkeyPatch, readonly: bool, expected: list[str]
) -> None:
    connection = Connection()
    events: list[str] = []
    monkeypatch.setattr(
        runtime,
        "validate_connected_identity",
        lambda *_: events.append("identity"),
    )
    monkeypatch.setattr(
        runtime,
        "set_and_validate_public_search_path",
        lambda *_: events.append("search_path"),
    )

    runtime.prepare_transaction(
        connection,
        _target(),
        readonly=readonly,
        acquire_lock=lambda *_: events.append("lock"),
    )

    assert connection.statements == expected
    assert events == ["identity", "search_path", "lock"]


@pytest.mark.parametrize("readonly", [True, False])
def test_repeatable_read_sets_explicit_access_mode(
    monkeypatch: pytest.MonkeyPatch, readonly: bool
) -> None:
    connection = Connection()
    monkeypatch.setattr(runtime, "validate_connected_identity", lambda *_: None)
    monkeypatch.setattr(runtime, "set_and_validate_public_search_path", lambda *_: None)

    runtime.prepare_transaction(
        connection,
        _target(),
        readonly=readonly,
        isolation_level="repeatable   read",
        acquire_lock=lambda *_: None,
    )

    access = "READ ONLY" if readonly else "READ WRITE"
    assert connection.statements == [
        f"SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, {access}"
    ]


def test_unsupported_isolation_is_rejected_before_target_checks() -> None:
    with pytest.raises(runtime.MutationRuntimeError, match="지원하지"):
        runtime.prepare_transaction(
            Connection(),
            _target(),
            readonly=True,
            isolation_level="serializable",
            acquire_lock=lambda *_: pytest.fail("lock must not run"),
        )


def test_mode_and_choice_guards() -> None:
    assert (
        runtime.resolve_exclusive_mode(
            {"preflight": False, "rehearse": False},
            default_mode="apply",
            mutually_exclusive_message="exclusive",
        )
        == "apply"
    )
    with pytest.raises(runtime.MutationRuntimeError, match="exclusive"):
        runtime.resolve_exclusive_mode(
            {"preflight": True, "rehearse": True},
            default_mode="apply",
            mutually_exclusive_message="exclusive",
        )
    assert runtime.require_exact_choice("a", ("a", "b"), label="mode") == "a"
    with pytest.raises(runtime.MutationRuntimeError, match="mode"):
        runtime.require_exact_choice("c", ("a", "b"), label="mode")
