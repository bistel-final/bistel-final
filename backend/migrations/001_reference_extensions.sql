-- =====================================================================
-- V4-CM-2.1 profile-neutral reference extensions
--
-- Creates schema only.  It must not insert, update, delete, backfill, grant,
-- or otherwise change rows in the clean base tables.
-- =====================================================================

-- FR-B-02 / system design 5.2: RAG embeddings use pgvector(1024).
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- System design 3.3: deterministic R03 reference events.
CREATE TABLE r03_alarm_history (
    alarm_id         varchar(24) PRIMARY KEY
                     CHECK (alarm_id ~ '^R03-[0-9a-f]{20}$'),
    occurred_at      timestamp   NOT NULL,
    lot_hist_id      varchar(20) NOT NULL REFERENCES lot_history(lot_hist_id),
    lot_id           varchar(20) NOT NULL,
    equipment_id     varchar(20) NOT NULL,
    chamber_id       varchar(24) NOT NULL,
    parameter_id     varchar(20) NOT NULL REFERENCES dim_parameter(parameter_id),
    recipe_step_no   smallint    NOT NULL CHECK (recipe_step_no >= 1),
    trigger_wafer_no smallint    NOT NULL CHECK (trigger_wafer_no >= 1),
    member_refs      jsonb       NOT NULL,
    policy_version   varchar(20) NOT NULL,
    UNIQUE (lot_hist_id, parameter_id, recipe_step_no, policy_version)
);

-- FR-B-02 / system design 5.2: mentor RAG document schema.
CREATE TABLE document (
    doc_id       varchar(30)  PRIMARY KEY,
    title        varchar(200) NOT NULL,
    doc_type     varchar(20)  CHECK (doc_type IN ('SPEC','MANUAL','TROUBLESHOOT')),
    model_code   varchar(20),
    source_path  varchar(300),
    version      varchar(20),
    created_at   timestamp    DEFAULT now()
);

CREATE TABLE document_chunk (
    chunk_id       varchar(40)  PRIMARY KEY,
    doc_id         varchar(30)  NOT NULL REFERENCES document(doc_id) ON DELETE CASCADE,
    chunk_seq      integer      NOT NULL,
    section_title  varchar(200),
    content        text         NOT NULL,
    token_cnt      integer,
    embedding      vector(1024),
    metadata_json  jsonb,
    UNIQUE (doc_id, chunk_seq)
);

-- FR-D-05 / V4-D-4.2: append-only Text2SQL execution outcomes.
CREATE TABLE nl_query_log (
    nl_query_log_id bigserial   PRIMARY KEY,
    asked_at        timestamptz NOT NULL DEFAULT now(),
    question        text        NOT NULL,
    generated_sql   text,
    outcome         varchar(20) NOT NULL
                    CHECK (outcome IN (
                        'SUCCESS',
                        'POLICY_REJECTED',
                        'VALIDATION_FAILED',
                        'DB_ERROR'
                    )),
    is_valid        boolean     NOT NULL,
    is_rejected     boolean     NOT NULL,
    reject_reason   text,
    row_cnt         integer     CHECK (row_cnt >= 0),
    latency_ms      integer     CHECK (latency_ms >= 0),
    error_msg       text,
    CHECK (
        (outcome = 'SUCCESS'
            AND is_valid
            AND NOT is_rejected
            AND reject_reason IS NULL
            AND error_msg IS NULL
            AND row_cnt IS NOT NULL)
        OR
        (outcome = 'POLICY_REJECTED'
            AND NOT is_valid
            AND is_rejected
            AND coalesce(btrim(reject_reason), '') <> ''
            AND error_msg IS NULL
            AND row_cnt IS NULL)
        OR
        (outcome = 'VALIDATION_FAILED'
            AND NOT is_valid
            AND NOT is_rejected
            AND reject_reason IS NULL
            AND coalesce(btrim(error_msg), '') <> ''
            AND row_cnt IS NULL)
        OR
        (outcome = 'DB_ERROR'
            AND is_valid
            AND NOT is_rejected
            AND reject_reason IS NULL
            AND coalesce(btrim(error_msg), '') <> ''
            AND row_cnt IS NULL)
    )
);

-- FR-A-06 / system design 3.8: read-only normalized alarm source.
CREATE VIEW v_alarm_event AS
SELECT
    'TRACE'::varchar(10) AS source,
    a.alarm_id::varchar(24) AS alarm_id,
    a.occurred_at,
    a.area,
    a.equipment AS equipment_id,
    a.chamber AS chamber_id,
    a.parameter AS parameter_id,
    a.recipe AS recipe_id,
    a.lot AS lot_id,
    a.wafer AS wafer_no,
    a.step_no AS recipe_step_no,
    a.seq_no,
    a.value,
    a.alarm_type,
    h.lot_hist_id
FROM trace_alarm_history AS a
LEFT JOIN lot_history AS h
  ON h.lot_id = a.lot
 AND h.wafer_no = a.wafer
 AND h.chamber_id = a.chamber

UNION ALL

SELECT
    'SUMMARY'::varchar(10) AS source,
    a.alarm_id::varchar(24) AS alarm_id,
    a.occurred_at,
    a.area,
    a.equipment AS equipment_id,
    a.chamber AS chamber_id,
    a.parameter AS parameter_id,
    a.recipe AS recipe_id,
    a.lot AS lot_id,
    a.wafer AS wafer_no,
    a.step_no AS recipe_step_no,
    NULL::smallint AS seq_no,
    a.stat_value AS value,
    a.alarm_type,
    h.lot_hist_id
FROM summary_alarm_history AS a
LEFT JOIN lot_history AS h
  ON h.lot_id = a.lot
 AND h.wafer_no = a.wafer
 AND h.chamber_id = a.chamber

UNION ALL

SELECT
    'R03'::varchar(10) AS source,
    a.alarm_id::varchar(24) AS alarm_id,
    a.occurred_at,
    h.area_id AS area,
    a.equipment_id,
    a.chamber_id,
    a.parameter_id,
    h.recipe_id,
    a.lot_id,
    a.trigger_wafer_no AS wafer_no,
    a.recipe_step_no,
    NULL::smallint AS seq_no,
    NULL::numeric(12,4) AS value,
    'OOS'::varchar(10) AS alarm_type,
    a.lot_hist_id
FROM r03_alarm_history AS a
JOIN lot_history AS h ON h.lot_hist_id = a.lot_hist_id;
