"""Five live snapshots are recounted; isolated locators never count as live PASS."""

from copy import deepcopy

import pytest

from app.agent.release_artifacts import (
    Component,
    EvidenceError,
    component_ref,
    parse_json,
    read_private,
    write_private,
)
from app.agent.release_golden import LIVE, verify_mock_golden
from tests.unit.test_agent_release import AT, ATTEMPT, REV
from tests.unit.test_agent_release_aggregate import bundle  # noqa: F401
from tests.unit.test_agent_release_bundle_v2 import mock_bundle  # noqa: F401
from tests.unit.test_agent_release_evidence import delivery, replace  # noqa: F401
from tests.unit.test_agent_release_mock import evidence as mock_evidence  # noqa: F401
from tests.unit.test_agent_release_round import template  # noqa: F401


def test_fault_population_uses_original_l3_baseline_prediction_pin(golden):
    from scripts.fault_evaluation_population import load_evaluation_population

    args, _ = golden
    population = load_evaluation_population(
        args["root"] / args["evidence"].relative_path
    )
    assert len(population.members) == 12
    assert population.prediction_hash == "a" * 64


@pytest.fixture
def golden(mock_bundle):  # noqa: F811
    root = mock_bundle["published_root"]
    round1 = parse_json(read_private(mock_bundle["root"], "round1.json"))
    preflight = component_ref(root, "evidence/artifacts/PREFLIGHT/db-snapshot.json")
    snapshot = parse_json(read_private(root, preflight.relative_path))
    for run in round1["runs"]:
        identity = {k: run["route"]["incident"][k] for k in ("lot_id", "chamber_id")}
        snapshot["runs"].append(
            dict(
                agent_run_id=run["run_id"],
                **identity,
                status="COMPLETED",
                autonomy_level=3,
                action=run["action_code"],
                retry_of_run_id=None,
                latency_ms=run["latency_ms"],
                input_tokens=1,
                output_tokens=1,
                rehydration_snapshot_bytes=None,
            )
        )
        snapshot["actions"].append(
            dict(
                agent_run_id=run["run_id"],
                action_id=run["action_id"],
                link_role="CREATED",
                **identity,
                action_code=run["action_code"],
            )
        )
        snapshot["deliveries"].extend(
            dict(
                action_id=run["action_id"],
                channel="MES_MOCK" if d["channel"] == "MES" else "EMAIL",
                status="SENT",
                attempt_count=1,
            )
            for d in run["deliveries"]
        )
    refs = {"PREFLIGHT": preflight.model_dump()}
    for phase in LIVE[1:]:
        (root / "evidence/artifacts" / phase).mkdir(mode=0o700)
        refs[phase] = write_private(
            root,
            f"evidence/artifacts/{phase}/db-snapshot.json",
            dict(
                schema_version="mock-notify-golden-snapshot-v1",
                action_policy_version="MOCK-NOTIFY-V1",
                database="kosa_agent_e2e",
                phase=phase,
                recorded_at=AT,
                snapshot=snapshot,
                kafka_offsets=round1["kafka_after"],
                prediction_hash="a" * 64 if phase == "BATCH_BASELINE" else None,
            ),
        ).model_dump()
    second = write_private(
        root,
        "evidence/second-batch.jsonl",
        dict(
            type="final",
            attempted=0,
            succeeded=0,
            failed=0,
            skipped=0,
            new_runs_observed=0,
            new_actions_observed=0,
            new_deliveries_observed=0,
        ),
    )
    from scripts.fault_evaluation_population import (
        SOURCE_MANIFEST_PATH,
        _file_sha256,
        _load_oracle,
    )

    pending = write_private(
        root,
        "evidence/artifacts/PREFLIGHT/pending.json",
        dict(
            type="plan",
            database="kosa_agent_e2e",
            selected=[
                dict(
                    lot_id=i.lot_id,
                    chamber_id=i.chamber_id,
                    member_count=1,
                    representative=dict(
                        source=i.alarm_sources[0], alarm_id="synthetic"
                    ),
                )
                for i in _load_oracle().incidents
            ],
            rejected=[],
            incomplete=[],
            excluded=dict(canonical_null_rows=0, canonical_null_by_source={}),
        ),
    )
    before = write_private(
        root, "evidence/artifacts/PREFLIGHT/kafka.json", round1["kafka_before"]
    )
    value = dict(
        schema_version="mock-notify-golden-evidence-v1",
        protocol="MOCK-NOTIFY-V1",
        attempt_id=ATTEMPT,
        R=REV,
        source_manifest_sha256=_file_sha256(SOURCE_MANIFEST_PATH),
        preflight_pending=pending.model_dump(),
        preflight_offsets=before.model_dump(),
        round1_sha256=mock_bundle["round1"].sha256,
        snapshots=refs,
        second_batch=second.model_dump(),
        isolated={
            p: dict(test_ref="tests/unit/test_agent_mock_notify.py", revision=REV)
            for p in ("UNKNOWN", "MANUAL_RETRY")
        },
    )
    ref = write_private(root, "evidence/mock-notify-evidence.json", value)
    return dict(
        root=root, evidence=ref, expected_attempt_id=ATTEMPT, expected_revision=REV
    ), value


def test_recounts_live_five_and_marks_two_not_live(golden):
    args, _ = golden
    summary, _, baseline = verify_mock_golden(**args)
    assert [p["status"] for p in summary["phases"]] == ["PASS"] * 5 + ["NOT_LIVE"] * 2
    assert len(baseline.runs) == 12
    assert summary["phases"][2]["metrics"]["mes_writebacks"] == 3


@pytest.mark.parametrize(
    "change",
    [
        "extra_run",
        "missing_delivery",
        "kafka_delta",
        "decision",
        "second_batch",
        "isolated_revision",
        "phase",
        "time",
    ],
)
def test_changed_source_is_recounted_even_with_updated_manifest_sha(golden, change):
    args, value = golden
    root = args["root"]
    phase = "NO_DECISIONS" if change == "decision" else "SECOND_BATCH"
    ref = Component.model_validate(value["snapshots"][phase])
    row = parse_json(read_private(root, ref.relative_path))
    if change == "extra_run":
        row["snapshot"]["runs"].append(deepcopy(row["snapshot"]["runs"][0]))
    elif change == "missing_delivery":
        row["snapshot"]["deliveries"].pop()
    elif change == "kafka_delta":
        row["kafka_offsets"]["fdc.actions:0"] += 1
    elif change == "decision":
        row["snapshot"]["audits"].append(
            dict(event_type="APPROVE", entity_id="a", channel=None)
        )
    elif change == "phase":
        row["phase"] = "MOCK_RESULTS"
    elif change == "time":
        row["recorded_at"] = "2020-01-01T00:00:00Z"
    elif change == "second_batch":
        # Preserve every required field and rebind SHA: only the zero-count
        # contract, not missing keys or a hash mismatch, must reject this.
        second = parse_json(read_private(root, value["second_batch"]["relative_path"]))
        second["attempted"] = 1
        value["second_batch"] = replace(
            root,
            value["second_batch"]["relative_path"],
            second,
        ).model_dump()
    else:
        value["isolated"]["UNKNOWN"]["revision"] = "b" * 40
    value["snapshots"][phase] = replace(root, ref.relative_path, row).model_dump()
    args["evidence"] = replace(root, args["evidence"].relative_path, value)
    code = "^GOLDEN_MOCK_SECOND_BATCH_NOT_EMPTY$" if change == "second_batch" else None
    with pytest.raises(EvidenceError, match=code):
        verify_mock_golden(**args)
