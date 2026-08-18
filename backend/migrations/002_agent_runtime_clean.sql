-- V4-CM-2.4: clean runtime profile only.
-- Supported apply path: backend/scripts/apply_agent_runtime.py
DO $$
BEGIN
    IF current_database() NOT IN ('kosa_agent', 'kosa_agent_e2e') THEN
        RAISE EXCEPTION '002는 runtime database 에만 적용한다: %', current_database();
    END IF;
    IF (SELECT count(*) FROM action_history) <> 0 THEN
        RAISE EXCEPTION '002는 action_history=0 에서만 적용한다';
    END IF;
END $$;

CREATE TABLE agent_run (
    agent_run_id varchar(20) PRIMARY KEY
        CHECK (agent_run_id ~ '^RUN-[0-9a-f]{16}$'),
    thread_id varchar(36) NOT NULL CHECK (btrim(thread_id) <> ''),
    retry_of_run_id varchar(20) REFERENCES agent_run(agent_run_id),
    lot_id varchar(20) NOT NULL CHECK (btrim(lot_id) <> ''),
    chamber_id varchar(24) NOT NULL CHECK (btrim(chamber_id) <> ''),
    requested_alarm_source varchar(10) NOT NULL
        CHECK (requested_alarm_source IN ('TRACE', 'SUMMARY', 'R03')),
    requested_alarm_id varchar(24) NOT NULL
        CHECK (btrim(requested_alarm_id) <> ''),
    representative_alarm_source varchar(10) NOT NULL
        CHECK (representative_alarm_source IN ('TRACE', 'SUMMARY', 'R03')),
    representative_alarm_id varchar(24) NOT NULL
        CHECK (btrim(representative_alarm_id) <> ''),
    status varchar(20) NOT NULL
        CHECK (status IN ('RUNNING', 'WAITING_APPROVAL', 'COMPLETED', 'FAILED')),
    autonomy_level smallint NOT NULL CHECK (autonomy_level IN (1, 2, 3)),
    action varchar(20)
        CHECK (action IN ('MONITORING', 'WARNING', 'EQP_HOLD')),
    severity varchar(10) CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
    llm_model varchar(64)
        CHECK (llm_model IS NULL OR btrim(llm_model) <> ''),
    prompt_version varchar(40)
        CHECK (prompt_version IS NULL OR btrim(prompt_version) <> ''),
    evidence jsonb,
    input_tokens integer CHECK (input_tokens >= 0),
    output_tokens integer CHECK (output_tokens >= 0),
    latency_ms integer CHECK (latency_ms >= 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (
        (action IS NULL AND severity IS NULL)
        OR (action = 'MONITORING' AND severity = 'LOW')
        OR (action = 'WARNING' AND severity = 'MEDIUM')
        OR (action = 'EQP_HOLD' AND severity = 'HIGH')
    )
);

CREATE TABLE agent_run_alarm (
    agent_run_id varchar(20) NOT NULL REFERENCES agent_run(agent_run_id),
    alarm_source varchar(10) NOT NULL
        CHECK (alarm_source IN ('TRACE', 'SUMMARY', 'R03')),
    alarm_id varchar(24) NOT NULL CHECK (btrim(alarm_id) <> ''),
    is_representative boolean NOT NULL DEFAULT false,
    PRIMARY KEY (agent_run_id, alarm_source, alarm_id)
);

CREATE TABLE agent_prediction (
    agent_run_id varchar(20) PRIMARY KEY REFERENCES agent_run(agent_run_id),
    predicted_fault_code varchar(10) NOT NULL
        CHECK (predicted_fault_code IN ('FOC', 'RFM', 'MFD', 'TMD', 'OTH')),
    confidence numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    cause_summary text NOT NULL CHECK (btrim(cause_summary) <> ''),
    evidence jsonb NOT NULL,
    llm_model varchar(64) NOT NULL CHECK (btrim(llm_model) <> ''),
    prompt_version varchar(40) NOT NULL CHECK (btrim(prompt_version) <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_prediction_review (
    review_id bigserial PRIMARY KEY,
    agent_run_id varchar(20) NOT NULL REFERENCES agent_run(agent_run_id),
    reviewed_fault_code varchar(10)
        CHECK (reviewed_fault_code IN ('FOC', 'RFM', 'MFD', 'TMD', 'OTH')),
    disposition varchar(16) NOT NULL
        CHECK (disposition IN ('ACCEPTED', 'CORRECTED', 'UNDETERMINED')),
    label_source varchar(16) NOT NULL
        CHECK (label_source IN ('HUMAN_REVIEW', 'MENTOR_REVIEW', 'HIDDEN_GOLD')),
    reviewer varchar(40) NOT NULL CHECK (btrim(reviewer) <> ''),
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    comment text,
    CHECK (disposition <> 'CORRECTED' OR reviewed_fault_code IS NOT NULL)
);

CREATE TABLE agent_run_action (
    agent_run_id varchar(20) PRIMARY KEY REFERENCES agent_run(agent_run_id),
    action_id varchar(20) NOT NULL REFERENCES action_history(action_id)
        CHECK (btrim(action_id) <> ''),
    link_role varchar(8) NOT NULL CHECK (link_role IN ('CREATED', 'REUSED')),
    lot_id varchar(20) NOT NULL CHECK (btrim(lot_id) <> ''),
    chamber_id varchar(24) NOT NULL CHECK (btrim(chamber_id) <> ''),
    trigger_alarm_source varchar(10) NOT NULL
        CHECK (trigger_alarm_source IN ('TRACE', 'SUMMARY', 'R03')),
    trigger_alarm_id varchar(24) NOT NULL
        CHECK (btrim(trigger_alarm_id) <> ''),
    linked_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_tool_call (
    tool_call_id varchar(29) PRIMARY KEY
        CHECK (tool_call_id ~ '^TOOL-[0-9a-f]{24}$'),
    agent_run_id varchar(20) NOT NULL REFERENCES agent_run(agent_run_id),
    call_seq integer NOT NULL CHECK (call_seq >= 1),
    tool_name varchar(40) NOT NULL CHECK (btrim(tool_name) <> ''),
    input jsonb,
    output jsonb,
    status varchar(10) NOT NULL CHECK (status IN ('SUCCESS', 'ERROR', 'TIMEOUT')),
    latency_ms integer CHECK (latency_ms >= 0),
    called_at timestamptz NOT NULL DEFAULT now(),
    error_msg text,
    UNIQUE (agent_run_id, call_seq)
);

CREATE TABLE approval_request (
    approval_id varchar(20) PRIMARY KEY
        CHECK (approval_id ~ '^APR-[0-9a-f]{16}$'),
    action_id varchar(20) NOT NULL UNIQUE REFERENCES action_history(action_id),
    agent_run_id varchar(20) NOT NULL REFERENCES agent_run(agent_run_id),
    status varchar(12) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    decided_by varchar(40),
    decided_at timestamptz,
    decision_comment varchar(1000)
        CHECK (decision_comment IS NULL OR btrim(decision_comment) <> ''),
    CHECK (
        (status = 'PENDING'
            AND decided_by IS NULL AND decided_at IS NULL
            AND decision_comment IS NULL)
        OR (status IN ('APPROVED', 'REJECTED')
            AND coalesce(btrim(decided_by), '') <> ''
            AND decided_at IS NOT NULL AND decided_at >= requested_at)
        OR (status = 'EXPIRED' AND decided_by IS NULL)
    )
);

CREATE TABLE action_delivery (
    action_id varchar(20) NOT NULL REFERENCES action_history(action_id),
    channel varchar(10) NOT NULL CHECK (channel IN ('EMAIL', 'MES_MOCK')),
    status varchar(10) NOT NULL
        CHECK (status IN ('BLOCKED', 'WAITING', 'SENDING', 'SENT',
                          'FAILED', 'CANCELED', 'UNKNOWN')),
    request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    provider_message_id text
        CHECK (provider_message_id IS NULL OR btrim(provider_message_id) <> ''),
    started_at timestamptz,
    completed_at timestamptz,
    last_error text,
    result jsonb,
    PRIMARY KEY (action_id, channel),
    CHECK (completed_at IS NULL OR started_at IS NOT NULL),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE audit_log (
    audit_id bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_type varchar(10) NOT NULL CHECK (actor_type IN ('SYSTEM', 'AGENT', 'HUMAN')),
    actor_id varchar(40) CHECK (actor_id IS NULL OR btrim(actor_id) <> ''),
    event_type varchar(32) NOT NULL,
    entity_type varchar(16) NOT NULL,
    entity_id varchar(20) NOT NULL CHECK (btrim(entity_id) <> ''),
    before_json jsonb,
    after_json jsonb,
    detail text,
    CHECK (
        (event_type, entity_type) IN (
            ('DETECTION_COMPLETED', 'LOT_HIST'),
            ('AGENT_RUN_STARTED', 'AGENT_RUN'),
            ('HYPOTHESIS_GENERATED', 'AGENT_RUN'),
            ('APPROVAL_REQUESTED', 'APPROVAL'),
            ('APPROVAL_DECIDED', 'APPROVAL'),
            ('ACTION_SENT', 'ACTION'),
            ('ACTION_SEND_FAILED', 'ACTION'),
            ('AGENT_RUN_COMPLETED', 'AGENT_RUN'),
            ('AGENT_RUN_FAILED', 'AGENT_RUN')
        )
    )
);

CREATE UNIQUE INDEX ux_agent_run_incident_active
    ON agent_run (lot_id, chamber_id)
    WHERE status IN ('RUNNING', 'WAITING_APPROVAL');

CREATE UNIQUE INDEX ux_agent_run_action_created
    ON agent_run_action (action_id)
    WHERE link_role = 'CREATED';

CREATE UNIQUE INDEX ux_agent_run_action_incident
    ON agent_run_action (lot_id, chamber_id)
    WHERE link_role = 'CREATED';

CREATE UNIQUE INDEX ux_agent_run_alarm_representative
    ON agent_run_alarm (agent_run_id)
    WHERE is_representative;
