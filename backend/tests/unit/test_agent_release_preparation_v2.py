"""Actual preflight/capture schemas with synthetic read-only infrastructure."""

import pytest

from app.agent.release_artifacts import EvidenceError, write_private
from app.agent.release_capture import collect_preparation
from app.agent.release_prepare import PreparationCaptureV2, parse_capture
from app.agent.u10_runtime import verify_runtime_readbacks
from tests.unit.test_agent_release_capture import rig  # noqa: F401
from tests.unit.test_agent_release_n8n import node
from tests.unit.test_agent_u10_runtime import inputs


def test_runtime_v2_survives_full_observation_serialization():
    args, values, _ = inputs()
    for value in values.values():
        value.update(
            schema_version="agent-runtime-readback-v2", action_policy="MOCK-NOTIFY-V1"
        )
    result = verify_runtime_readbacks(**args)
    assert all(
        r["action_policy"] == "MOCK-NOTIFY-V1"
        for r in result.model_dump()["readbacks"].values()
    )


def test_runtime_cannot_mix_legacy_backend_with_mock_runner():
    args, values, _ = inputs()
    values[args["container_ids"]["runner"]].update(
        schema_version="agent-runtime-readback-v2", action_policy="MOCK-NOTIFY-V1"
    )
    with pytest.raises(EvidenceError, match="U10_RUNTIME_POLICY_MISMATCH"):
        verify_runtime_readbacks(**args)


@pytest.mark.parametrize("retention", ["all", "none"])
def test_new_policy_capture_requires_independent_mes_probes(rig, tmp_path, retention):  # noqa: F811
    args, _, _, config, *_ = rig
    args = dict(args)
    original_read = args["read"]

    def read(*a):
        return {
            **original_read(*a),
            "schema_version": "agent-runtime-readback-v2",
            "action_policy": "MOCK-NOTIFY-V1",
        }

    args["read"] = read
    original_api = args["api"]

    class Api:
        def workflow(self, identifier):
            if identifier not in {"wf3", "wf4"}:
                return original_api.workflow(identifier)
            return dict(
                id=identifier,
                versionId="v",
                active=True,
                settings=dict(
                    saveDataSuccessExecution=retention, saveDataErrorExecution="all"
                ),
                nodes=[
                    dict(
                        name="Validate MES Payload"
                        if identifier == "wf3"
                        else "Validate MES Result",
                        type="n8n-nodes-base.code",
                    )
                ],
            )

        def execution(self, identifier):
            if identifier not in {"30", "40"}:
                return original_api.execution(identifier)
            wf = "wf3" if identifier == "30" else "wf4"
            name, field = (
                ("Validate MES Payload", "schema_ok")
                if wf == "wf3"
                else ("Validate MES Result", "valid")
            )
            return dict(
                id=identifier,
                workflowId=wf,
                workflowData=dict(id=wf, versionId="v"),
                status="success",
                startedAt="2026-09-05T01:00:00Z",
                stoppedAt="2026-09-05T01:00:01Z",
                data={
                    "resultData": {
                        "runData": {
                            name: node(
                                {field: True, "payload": {"action_id": "old-action"}}
                            )
                        }
                    }
                },
            )

    args["api"] = Api()
    config["n8n_workflow_versions"].update(wf3="v", wf4="v")
    root = tmp_path / "preflight"
    root.mkdir(mode=0o700)
    write_private(
        root,
        "db.json",
        {
            k: []
            for k in (
                "runs",
                "actions",
                "approvals",
                "deliveries",
                "tools",
                "audits",
                "r03_incidents",
            )
        },
    )
    args.update(
        preflight_snapshot=root / "db.json",
        mock_workflows={
            w: dict(workflow_id=w.lower(), version="v") for w in ("WF3", "WF4")
        },
        mock_samples=dict(WF3="30", WF4="40"),
        read_trail_probe=lambda: True,
        n8n_original_retention={
            workflow: {
                "saveDataSuccessExecution": "DEFAULT",
                "saveDataErrorExecution": "DEFAULT",
            }
            for workflow in ("WF3", "WF4")
        },
    )
    if retention == "none":
        with pytest.raises(EvidenceError, match="N8N_EXECUTION_RETENTION_REQUIRED"):
            collect_preparation(**args)
    else:
        result = collect_preparation(**args)
        assert isinstance(result, PreparationCaptureV2)
        assert parse_capture(result.model_dump()) == result
        assert result.n8n_evidence_probe.wf4_execution_detail_retained is True
