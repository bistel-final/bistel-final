-- V5-CM-3.1 canonical `v_alarm_event` 정본
--
-- `001_reference_extensions_final.sql`을 격리 PostgreSQL 16에 적용한 뒤
-- `pg_get_viewdef(oid, true)`가 돌려준 **실측 정규형**이다. 손으로 쓰지 않았다.
--
-- `apply_reference_extensions_v5.CANONICAL_VIEW_SHA256`이 이 내용의 공백 정규화
-- SHA-256이다. major가 바뀌면 정규형도 바뀌므로 hash·계획·marker를 함께 재승인한다.
 SELECT 'TRACE'::character varying(10) AS source,
    a.alarm_id::character varying(24) AS alarm_id,
    a.occurred_at,
    a.area,
    a.equipment AS equipment_id,
    a.chamber AS chamber_id,
    a.parameter AS parameter_id,
    a.recipe AS recipe_id,
    h.lot_hist_id,
    a.lot AS lot_id,
    a.wafer AS wafer_id,
    h.wafer_no,
    a.step_no AS recipe_step_no,
    a.seq_no,
    a.value,
    a.alarm_type,
    'TRACE_OOS'::character varying(20) AS rule_code
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
    h.lot_hist_id,
    a.lot AS lot_id,
    a.wafer AS wafer_id,
    h.wafer_no,
    a.step_no AS recipe_step_no,
    NULL::smallint AS seq_no,
    a.stat_value AS value,
    a.alarm_type,
    'SUMMARY_OOC'::character varying(20) AS rule_code
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
    a.lot_hist_id,
    a.lot_id,
    h.wafer_id,
    h.wafer_no,
    a.recipe_step_no,
    NULL::smallint AS seq_no,
    NULL::numeric(12,4) AS value,
    'OOS'::character varying(10) AS alarm_type,
    'R03_CONSEC'::character varying(20) AS rule_code
   FROM r03_alarm_history a
     JOIN lot_history h ON h.lot_hist_id::text = a.lot_hist_id::text;