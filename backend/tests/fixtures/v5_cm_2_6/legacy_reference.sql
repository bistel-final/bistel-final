-- V5-CM-2.6 전용 legacy fixture (vendored)
--
-- 원본: infra/bootstrap/001_base_schema.sql (PR #40)
--       backend/migrations/001_reference_extensions.sql (PR #48) 의 r03·view 부분
--
-- `V5-CM-1.7`이 `infra/bootstrap/001_base_schema.sql`을 삭제하면 2.6의 legacy View
-- fingerprint 회귀가 깨진다(구현리뷰 1차 권장 1 · 계획리뷰 7차 권장 1). 그래서 필요한
-- 최소본만 여기로 vendoring한다. 이 파일은 **격리 fingerprint 재현 전용**이며 공용 DB
-- 적용·COPY·복구 입력으로 쓰지 않는다.

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
