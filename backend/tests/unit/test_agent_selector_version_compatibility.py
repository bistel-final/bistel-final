"""Current selector admission and historical artifact/schema compatibility."""

from copy import deepcopy

import pytest

from app.agent.public_schemas import ReactStepPublic
from app.agent.release_artifacts import canonical_json
from app.agent.release_model import RuntimeLlmConfiguration
from app.agent.u10_batch import execute_batch
from app.agent.u10_comparison import Artifact, LlmConfiguration, validate_artifact
from tests.unit.test_agent_react_public import CASES
from tests.unit.test_agent_u10_batch import inputs

VERSIONS = (
    "agent-react-v2-ko1",
    "agent-react-v2-ko2",
    "agent-react-v2-ko3",
    "agent-react-v2-ko4",
)


@pytest.mark.parametrize("version", VERSIONS)
def test_known_selector_versions_remain_readable_in_metadata_and_public_trace(version):
    params, *_ = inputs()
    value = {**params["llm"].model_dump(), "selector_prompt_version": version}
    config = LlmConfiguration.model_validate(value)
    assert canonical_json(config) == canonical_json(value)
    runtime = RuntimeLlmConfiguration.model_validate({**value, "seed": None})
    assert runtime.selector_prompt_version == version
    step = next(
        s for case in CASES for s in case["react_trace"] if s["react_prompt_version"]
    )
    step = {**step, "react_prompt_version": version}
    assert ReactStepPublic.model_validate(step).model_dump(mode="json") == step


@pytest.mark.parametrize("version", VERSIONS)
def test_batch_keeps_legacy_bytes_and_trace_contract_by_declared_version(version):
    params, *_ = inputs()
    params["llm"] = LlmConfiguration.model_validate(
        {**params["llm"].model_dump(), "selector_prompt_version": version}
    )
    artifact = execute_batch(**params)
    payload = artifact.model_dump(mode="json")
    assert len(payload["attempts"]) == 32
    assert canonical_json(Artifact.model_validate(payload)) == canonical_json(payload)
    assert (
        validate_artifact(payload, params["benchmark"].model_dump()) == artifact.result
    )
    if version == "agent-react-v2-ko1":
        assert all("selector_trace" not in row for row in payload["attempts"])
        return
    assert all("selector_trace" in row for row in payload["attempts"])
    assert all(
        row.selector_trace == []
        for row in artifact.attempts
        if row.policy == "FIXED_POLICY_V21"
    )
    assert all(
        row.selector_trace for row in artifact.attempts if row.policy == "REACT_V2"
    )
    missing = deepcopy(payload)
    del missing["attempts"][0]["selector_trace"]
    with pytest.raises(ValueError, match="U10_SCHEMA_INVALID"):
        Artifact.model_validate(missing)
    changed = deepcopy(payload)
    row = next(row for row in changed["attempts"] if row["policy"] == "REACT_V2")
    row["selector_trace"][0]["llm_call"] = False
    with pytest.raises(ValueError, match="U10_DIAGNOSTIC_INCONSISTENT"):
        Artifact.model_validate(changed)


@pytest.mark.parametrize(
    "version", ("agent-hypothesis-v3-ko2", "agent-hypothesis-v3-ko3")
)
def test_hypothesis_diagnostic_versions_keep_identical_schema_and_validate(version):
    params, *_ = inputs()
    params["llm"] = LlmConfiguration.model_validate(
        {**params["llm"].model_dump(), "hypothesis_prompt_version": version}
    )
    artifact = execute_batch(**params)
    payload = artifact.model_dump(mode="json")
    assert canonical_json(Artifact.model_validate(payload)) == canonical_json(payload)
    assert (
        validate_artifact(payload, params["benchmark"].model_dump()) == artifact.result
    )
    missing = deepcopy(payload)
    del missing["attempts"][0]["origin_degraded"]
    with pytest.raises(ValueError, match="U10_SCHEMA_INVALID"):
        Artifact.model_validate(missing)
