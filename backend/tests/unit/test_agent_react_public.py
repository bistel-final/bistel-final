from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent import public_read_model as subject
from app.agent.public_schemas import ReactStepPublic
from app.agent.repository import RepositoryContractError
from app.common.enums import RunStatus
from tests.unit.test_agent_screen_read_model import _run

FIXTURE = Path(__file__).parents[1] / "fixtures/v5_c_7_1/react_trace_public.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


def _internal(steps):
    return [
        {
            **step,
            "argument_digest": "a" * 64 if step["argument_summary"] else None,
            "llm_model": "private-model" if step["react_prompt_version"] else None,
        }
        for step in steps
    ]


@pytest.mark.parametrize("case", CASES)
def test_shared_public_fixture_matches_server_projection(case):
    evidence = (
        None
        if case["trace_state"] == "UNAVAILABLE"
        else {"react_trace": _internal(case["react_trace"])}
    )
    record = replace(
        _run(),
        status=RunStatus(case["status"]),
        autonomy_level=case["autonomy_level"],
        run_evidence=evidence,
    )
    actual = subject._public_trace(record)
    assert actual["trace_state"] == case["trace_state"]
    assert [item.model_dump(mode="json") for item in actual["react_trace"]] == case[
        "react_trace"
    ]
    serialized = json.dumps(actual, default=lambda item: item.model_dump())
    for private in ("argument_digest", "llm_model", "lot_hist_id", "private-model"):
        assert private not in serialized


@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.WAITING_APPROVAL])
def test_checkpoint_owned_state_never_reads_terminal_json(status):
    record = replace(
        _run(),
        status=status,
        autonomy_level=3,
        run_evidence={"react_trace": [{"raw_query": "private"}]},
    )
    assert subject._public_trace(record) == {
        "trace_state": "PENDING",
        "react_trace": [],
    }


@pytest.mark.parametrize("mutation", ["sequence", "argument", "phase", "unknown"])
def test_malformed_terminal_trace_is_rejected(mutation):
    steps = _internal(CASES[2]["react_trace"])
    if mutation == "sequence":
        steps[0]["seq"] = 3
    elif mutation == "argument":
        steps[0]["argument_summary"] = "LH-PRIVATE raw query"
    elif mutation == "phase":
        steps[0]["stop_reason"] = "LLM_STOP"
    else:
        steps[0]["thought"] = "private"
    with pytest.raises(RepositoryContractError, match="PUBLIC_REACT_TRACE_INVALID"):
        subject._public_trace(
            replace(
                _run(),
                status=RunStatus.COMPLETED,
                autonomy_level=3,
                run_evidence={"react_trace": steps},
            )
        )


def test_public_schema_does_not_advertise_private_fields():
    keys = ReactStepPublic.model_json_schema()["properties"]
    assert not {"argument_digest", "llm_model", "query", "lot_hist_id"} & keys.keys()
