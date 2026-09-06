"""Current E2E binding read for the under-lock resume decision.

No saved preflight PASS, reset, workload, grant or service lifecycle. Read the
same pinned backend/runner twice around the mandatory current SMTP observer.
The observer's temporary n8n metadata mutation needs separate operator consent.
"""

from app.agent.release_artifacts import EvidenceError
from app.agent.release_context import PreparationContext, docker_context
from app.agent.release_prepared import (
    EffectiveEnv,
    EffectiveEnvV2,
    Recipient,
    SmtpConfigSnapshot,
    canonical_recipients,
    config_digest,
    recipient_hash,
)
from app.agent.release_runtime import RuntimeSnapshot
from app.agent.u10_revision import verify_execution_revision
from app.agent.u10_runtime import docker_readback, verify_runtime_readbacks


def observe_resume(
    *,
    runtime_adapter,
    running,
    repository,
    read_smtp_config,
    read_context=docker_context,
    read=docker_readback,
):
    """Return independently observed fields, never prepared values as truth."""
    try:
        running = RuntimeSnapshot.model_validate(running.model_dump()).model_copy(
            deep=True
        )
        if running.phase != "running":
            raise ValueError
        verify_execution_revision(repository, running.revision)
        runtime_adapter.verify_running(running)
        ids = {r: running.containers[r].container_id for r in ("backend", "runner")}

        def current():
            contexts = {
                r: PreparationContext.model_validate(
                    read_context(cid).model_dump()
                ).model_copy(deep=True)
                for r, cid in ids.items()
            }
            if contexts["backend"] != contexts["runner"]:
                raise ValueError
            env = verify_runtime_readbacks(
                profile="e2e_level3", container_ids=ids, read=read
            )
            if env.readbacks["backend"] != env.readbacks["runner"]:
                raise ValueError
            return contexts["backend"], env.readbacks["backend"]

        context, env = current()
        smtp = SmtpConfigSnapshot.model_validate(read_smtp_config()).model_copy(
            deep=True
        )
        addresses = canonical_recipients(context.recipients)
        if addresses != context.recipients or addresses != canonical_recipients(
            smtp.recipient_allowlist
        ):
            raise ValueError
        sha = config_digest(smtp.model_dump())
        after_context, after_env = current()
        if after_context != context or after_env != env:
            raise ValueError
        after_smtp = SmtpConfigSnapshot.model_validate(read_smtp_config())
        if config_digest(after_smtp.model_dump()) != sha:
            raise ValueError
        runtime_adapter.verify_running(running)
        verify_execution_revision(repository, running.revision)
        return {
            "images": running.images.model_dump(),
            "containers": running.prepared_containers().model_dump(),
            "effective_env": (
                EffectiveEnvV2
                if getattr(env, "action_policy", None) == "MOCK-NOTIFY-V1"
                else EffectiveEnv
            )(
                **(
                    {"AGENT_ACTION_POLICY": env.action_policy}
                    if getattr(env, "action_policy", None) == "MOCK-NOTIFY-V1"
                    else {}
                ),
                AGENT_AUTONOMY_LEVEL=env.autonomy_level,
                AGENT_LEVEL3_ENABLED=env.level3_enabled,
                AGENT_LEVEL3_DEMO_ACK=env.demo_ack or "",
                **env.budget_policy,
            ).model_dump(),
            "db_identity": context.identity.model_dump(),
            "recipient": Recipient(
                canonical_addresses=addresses,
                canonical_hash=recipient_hash(addresses),
                count=len(addresses),
                recipient_hash_version=2,
            ).model_dump(),
        }, sha
    except Exception:
        # Driver/API errors and exact recipients cannot leave this boundary.
        raise EvidenceError("PREPARED_RUNTIME_DRIFT") from None
