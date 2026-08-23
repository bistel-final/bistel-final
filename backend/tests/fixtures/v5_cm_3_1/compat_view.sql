-- V5-CM-2.6 호환 View의 실측 정의(`pg_get_viewdef(oid, true)`).
--
-- CM-3.1의 `V4_REFERENCE_COMPAT` 상태 판정을 DB 없이 회귀로 고정하려고 남긴다.
-- 격리 PostgreSQL 16에서 legacy → CM-2.6 전환을 재현해 뽑았고, 정규화 hash가
-- `postgres_transition.COMPAT_VIEW_SHA256`(79b35b5d5a5ea187…)과 같다.
 SELECT 'TRACE'::character varying(10) AS source,
    a.alarm_id::character varying(24) AS alarm_id,
    a.occurred_at,
    a.area,
    a.equipment AS equipment_id,
    a.chamber AS chamber_id,
    a.parameter AS parameter_id,
    a.recipe AS recipe_id,
    a.lot AS lot_id,
    h.wafer_no,
    a.step_no AS recipe_step_no,
    a.seq_no,
    a.value,
    a.alarm_type,
    h.lot_hist_id
   FROM trace_alarm_history a
     LEFT JOIN lot_history h ON h.lot_id::text = a.lot::text AND h.wafer_id::text = a.wafer::text AND h.chamber_id::text = a.chamber::text
UNION ALL
 SELECT 'SUMMARY'::character varying(10) AS source,
    a.alarm_id::character varying(24) AS alarm_id,
    a.occurred_at,
    a.area,
    a.equipment AS equipment_id,
    a.chamber AS chamber_id,
    a.parameter AS parameter_id,
    a.recipe AS recipe_id,
    a.lot AS lot_id,
    h.wafer_no,
    a.step_no AS recipe_step_no,
    NULL::smallint AS seq_no,
    a.stat_value AS value,
    a.alarm_type,
    h.lot_hist_id
   FROM summary_alarm_history a
     LEFT JOIN lot_history h ON h.lot_id::text = a.lot::text AND h.wafer_id::text = a.wafer::text AND h.chamber_id::text = a.chamber::text
UNION ALL
 SELECT 'R03'::character varying(10) AS source,
    a.alarm_id,
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
    'OOS'::character varying(10) AS alarm_type,
    a.lot_hist_id
   FROM r03_alarm_history a
     JOIN lot_history h ON h.lot_hist_id::text = a.lot_hist_id::text;
