"""Code-owned U10 tool/projection binding, independent of benchmark declarations.

No provider or database is initialized on import. Source bytes come from THIS
loaded package, never from a caller-supplied directory or artifact file list.
"""

from copy import deepcopy
from pathlib import Path

from app.agent.release_artifacts import EvidenceError, canonical_json, digest
from app.agent.u10_comparison import Benchmark
from app.agent.u10_evidence import projection_sha256
from app.agent.u10_read_execution import fixed_policy_sha256

SOURCE_FILES = (
    "app/agent/react.py",
    "app/agent/prompts.py",
    "app/agent/hypothesis.py",
    "app/agent/hypothesis_v3.py",
    "app/agent/investigation.py",
    "app/agent/u10_evidence.py",
    "app/agent/u10_observations.py",
    "app/agent/u10_read_adapter.py",
    "app/agent/u10_read_execution.py",
    "app/agent/u10_react_execution.py",
    "app/agent/u10_hypothesis.py",
    "app/agent/u10_attempt.py",
    "app/agent/u10_batch.py",
    "app/agent/u10_comparison.py",
    "app/agent/u10_inventory.py",
    "app/agent/u10_source.py",
    "app/agent/u10_export.py",
    "app/agent/u10_preparation.py",
    "app/agent/u10_fixture_source.py",
    "app/agent/u10_counterfactual.py",
    "app/agent/u10_fixture_tools.py",
    "app/agent/u10_fixture_bundle.py",
    "app/agent/u10_fixture_database.py",
    "app/agent/u10_provider.py",
    "app/agent/u10_observer.py",
    "app/agent/u10_runner.py",
    "scripts/run_u10_comparison.py",
    "app/agent/routing.py",
    "app/agent/routing_repository.py",
    "app/agent/incident.py",
    "app/agent/rehydration.py",
    "app/detection/rules.py",
    "app/common/tool_contracts.py",
    "app/common/llm.py",
    "app/common/config.py",
    "app/agent/tools.py",
    "scripts/intake_final_zip.py",
    "scripts/master_cypher.py",
    "scripts/rehearsal_postgres.py",
    "scripts/prepare_u10_fixtures.py",
)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def source_hashes() -> dict[str, str]:
    result = {}
    for name in SOURCE_FILES:
        path = BACKEND_ROOT / name
        if path.is_symlink() or not path.is_file():
            raise EvidenceError("U10_SOURCE_FILE_INVALID")
        result[name] = digest(path.read_bytes())
    return result


def tool_contract_spec() -> dict:
    from app.agent.react import REACT_SELECT_SCHEMA
    from app.common import tool_contracts as dto

    models = (
        ("get_fdc_summary", dto.FdcSummaryToolInput, dto.FdcSummaryToolResult),
        (
            "get_equipment_context",
            dto.EquipmentContextToolInput,
            dto.EquipmentContextToolResult,
        ),
        ("search_documents", dto.DocumentSearchToolInput, dto.DocumentSearchToolResult),
        (
            "get_chamber_parameter_history",
            dto.ChamberParameterHistoryToolInput,
            dto.ChamberParameterHistoryToolResult,
        ),
        (
            "get_metrology_result",
            dto.MetrologyResultToolInput,
            dto.MetrologyResultToolResult,
        ),
    )
    return {
        "version": "u10-tool-source-v1",
        "selector": deepcopy(REACT_SELECT_SCHEMA),
        "tools": {
            name: {
                "input": incoming.model_json_schema(),
                "result": outgoing.model_json_schema(),
            }
            for name, incoming, outgoing in models
        },
        # Matrix/resolver/history algorithms are pinned by their source bytes,
        # not by a manually maintained duplicate of their implementation.
        "source_sha256": source_hashes(),
        "projection_sha256": projection_sha256(),
        "fixed_policy_sha256": fixed_policy_sha256(),
    }


def tool_contract_sha256() -> str:
    return digest(canonical_json(tool_contract_spec()))


def verify_source_binding(benchmark: Benchmark) -> str:
    actual = tool_contract_sha256()
    if benchmark.tool_contract_sha256 != actual:
        raise EvidenceError("TOOL_CONTRACT_MISMATCH")
    if benchmark.fixed_policy_sha256 != fixed_policy_sha256():
        raise EvidenceError("FIXED_POLICY_MISMATCH")
    return actual
