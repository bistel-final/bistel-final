"""Read the actual graph checkpoint and persisted Tool audit, not a replay run.

The caller supplies the already-composed production graph's checkpoint reader.
No second graph executor, provider call, label lookup, write, reset or retry.
Incomplete/missing observations cannot be replaced with synthetic successes.
"""

from copy import deepcopy
from typing import Literal

from pydantic import Field, TypeAdapter
from sqlalchemy import text

from app.agent import repository as repo
from app.agent.graph import _document_model_code
from app.agent.react import ReactStep, arguments_digest
from app.agent.release_artifacts import EvidenceError, EvidenceModel
from app.agent.release_database import IDENTITY_SQL
from app.agent.release_model import ModelContext
from app.agent.release_round import READ_TOOLS, CapturedRun, CapturedRunV2, _assess_run
from app.agent.routing import ResolvedIncidentRoute


class CapturedRuns(EvidenceModel):
    schema_version: Literal["level3-captured-runs-v1"]
    model: ModelContext
    runs: list[CapturedRun] = Field(min_length=12, max_length=12)


class CapturedRunsV2(CapturedRuns):
    schema_version: Literal["level3-captured-runs-v2"]
    runs: list[CapturedRunV2] = Field(min_length=12, max_length=12)


def capture_run(
    *, state, run, action, calls, deliveries, model, action_policy="ACTION-POLICY-V1"
):
    """Project complete persisted observations; preserve negative verdict inputs."""
    try:
        state = deepcopy(state)
        if (
            state["run_id"] != run.agent_run_id
            or state["thread_id"] != run.thread_id
            or state["action_id"] != action.action_id
            or state["autonomy_level"] != run.autonomy_level
            or (run.lot_id, run.chamber_id) != (action.lot_id, action.chamber_id)
            or str(run.action) != str(action.action_code)
            or run.retry_of_run_id is not None
            or [r.call_seq for r in calls] != list(range(1, len(calls) + 1))
            or any(r.agent_run_id != run.agent_run_id for r in calls)
            or any(d.action_id != action.action_id for d in deliveries)
            or run.llm_model != model.llm.hypothesis_model_revision
            or run.prompt_version != model.llm.hypothesis_prompt_version
        ):
            raise ValueError
        trace = [ReactStep.model_validate(r) for r in state.get("react_trace", ())]
        observed = [r for r in trace if r.phase == "OBSERVED"]
        reads = [r for r in calls if r.tool_name in READ_TOOLS]
        if any(r.tool_name not in {*READ_TOOLS, "send_action"} for r in calls):
            raise ValueError
        initial_count = len(reads) - len(observed)
        if initial_count not in (1, 2):
            raise ValueError
        slots = [None] * initial_count + observed
        captured_reads = []
        for seq, (row, slot) in enumerate(zip(reads, slots, strict=True), 1):
            if row.error_msg == repo.RESERVED_ERROR_MSG or row.latency_ms is None:
                raise ValueError
            if slot is not None and (
                slot.tool != row.tool_name
                or slot.argument_digest
                != arguments_digest({"tool": row.tool_name, **row.input})
            ):
                raise ValueError
            captured_reads.append(
                dict(
                    seq=seq,
                    selector_seq=None if slot is None else slot.seq,
                    tool=row.tool_name,
                    request=row.input,
                    status=str(row.status),
                    result=deepcopy(row.output)
                    if str(row.status) == "SUCCESS"
                    else None,
                    latency_ms=row.latency_ms,
                )
            )
        if action_policy not in {"ACTION-POLICY-V1", "MOCK-NOTIFY-V1"}:
            raise ValueError
        is_mock = action_policy == "MOCK-NOTIFY-V1"
        if (
            is_mock
            and (run.evidence or {})
            .get("action_provenance", {})
            .get("action_policy_version")
            != action_policy
        ):
            raise ValueError
        value = (CapturedRunV2 if is_mock else CapturedRun)(
            **(
                dict(action_policy_version=action_policy, link_type="CREATED")
                if is_mock
                else {}
            ),
            run_id=run.agent_run_id,
            action_id=action.action_id,
            autonomy_level=run.autonomy_level,
            status=str(run.status),
            action_code=str(run.action),
            route=TypeAdapter(ResolvedIncidentRoute).dump_python(
                state["route"], mode="json"
            ),
            current_lot_hist_ids=list(state["fdc_lot_hist_ids"]),
            document_model_code=_document_model_code(state),
            reads=captured_reads,
            hypothesis=None
            if state.get("hypothesis") is None
            else state["hypothesis"].model_dump(mode="json"),
            react_trace=[r.model_dump(mode="json") for r in trace],
            error_codes=[e.code for e in state.get("errors", ())],
            hypothesis_tokens=dict(input=run.input_tokens, output=run.output_tokens),
            hypothesis_model_revision=run.llm_model,
            hypothesis_prompt_version=run.prompt_version,
            latency_ms=run.latency_ms,
            model_config_digest=model.model_config_digest,
            deliveries=[
                dict(
                    channel="MES" if str(d.channel) == "MES_MOCK" else str(d.channel),
                    status=str(d.status),
                    request_hash=d.request_hash,
                )
                for d in deliveries
            ],
            send_action_selected=sum(r.tool == "send_action" for r in trace),
            # This records unexpected channels in the persisted run only. The
            # full-batch DB/n8n/Kafka observation remains independently required.
            unexpected_external_effects=sum(
                str(d.channel) not in {"EMAIL", "MES_MOCK"} for d in deliveries
            ),
        )
        _assess_run(
            value, is_mock=is_mock
        )  # Exact trace/request joins and scope replay.
        return value
    except Exception:
        raise EvidenceError("ROUND_RUN_CAPTURE_INVALID") from None


def collect_runs(
    engine, *, identity, read_checkpoint, read_model, action_policy="ACTION-POLICY-V1"
):
    """Two fresh read-only DB snapshots with checkpoint/model drift checks.

    Max 12 runs, 100 audit rows per run. No production DB and no privileged
    fallback. Model/endpoint settings are observed, not taken from prepared SHA.
    """
    try:
        if (engine.url.host, engine.url.database, engine.url.username) != (
            identity.host_alias,
            "kosa_agent_e2e",
            "kosa_app",
        ):
            raise ValueError
        model = ModelContext.model_validate(read_model().model_dump()).model_copy(
            deep=True
        )
        if model.published_attempt_id is not None:
            raise ValueError

        def snapshot():
            result = []
            with engine.connect() as c:
                c.exec_driver_sql("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                c.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                c.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                if dict(c.execute(IDENTITY_SQL).mappings().one()) != dict(
                    database_name="kosa_agent_e2e",
                    role_name="kosa_app",
                    read_only="on",
                    isolation="repeatable read",
                    system_identifier=identity.system_identifier,
                ):
                    raise ValueError
                ids = list(
                    c.execute(
                        text(
                            "SELECT agent_run_id FROM public.agent_run "
                            "ORDER BY agent_run_id LIMIT 13"
                        )
                    ).scalars()
                )
                if len(ids) != 12 or len(set(ids)) != 12:
                    raise ValueError
                for run_id in ids:
                    run = repo.get_agent_run(c, run_id)
                    link = repo.get_run_action(c, run_id)
                    if (
                        str(link.link_role) != "CREATED"
                        or repo.count_tool_calls(c, run_id) > 100
                    ):
                        raise ValueError
                    result.append(
                        capture_run(
                            state=read_checkpoint(run.thread_id),
                            run=run,
                            action=repo.get_action_history(c, link.action_id),
                            calls=repo.list_tool_calls(c, run_id),
                            deliveries=repo.list_action_deliveries(c, link.action_id),
                            model=model,
                            action_policy=action_policy,
                        )
                    )
            return result

        before = snapshot()
        after = snapshot()
        if before != after or read_model() != model:
            raise ValueError
        is_mock = action_policy == "MOCK-NOTIFY-V1"
        return (CapturedRunsV2 if is_mock else CapturedRuns)(
            schema_version="level3-captured-runs-v2"
            if is_mock
            else "level3-captured-runs-v1",
            model=model,
            runs=before,
        )
    except Exception:
        raise EvidenceError("ROUND_RUN_CAPTURE_FAILED") from None
