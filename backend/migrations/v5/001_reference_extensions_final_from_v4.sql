-- V5-CM-3.1 successor: V4 reference extensions → final
--
-- 대상: `V4_REFERENCE_COMPAT`. final base 9 + 구 001의 11컬럼 `r03_alarm_history`(0행)
-- + `V5-CM-2.6`이 복구한 임시 호환 `v_alarm_event`.
--
-- **canonical 경로와 같은 schema로 수렴해야 한다.** 두 경로가 다른 컬럼 순서나 다른
-- constraint 이름을 만들면 `schema_signature_sha256`가 갈라지고 `V5_REFERENCE_FINAL`이
-- 하나의 상태가 되지 못한다(계획 §4.1).
--
-- 그래서 `ALTER`로 승계하지 않고 **빈 table을 버리고 canonical DDL로 다시 만든다.**
-- `ALTER TABLE ... ADD COLUMN`은 컬럼을 항상 **끝에** 붙이므로
-- `member_alarm_refs`가 `policy_version` 뒤로 가서 계획 §5.1의 12컬럼 순서와 어긋난다.
-- 격리 PostgreSQL 16에서 실측해 확인했다. PostgreSQL은 컬럼 순서를 바꿀 수 없다.
--
-- 아래 DDL 본문은 `001_reference_extensions_final.sql`과 **같아야 한다.** 회귀가 두
-- 파일의 DDL 본문이 일치하는지 확인한다.
--
-- 적용은 `psql -v ON_ERROR_STOP=1 --single-transaction -f`로 한다.

-- 0. 전제 확인. 행이 하나라도 있으면 내용을 추측하지 않고 중단한다.
DO $$
DECLARE
    row_count bigint;
BEGIN
    SELECT count(*) INTO row_count FROM r03_alarm_history;
    IF row_count <> 0 THEN
        RAISE EXCEPTION
            'r03_alarm_history에 %행이 있어 successor migration을 중단한다', row_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'r03_alarm_history'
           AND column_name = 'member_refs'
    ) THEN
        RAISE EXCEPTION 'V4 형상이 아니다: member_refs 컬럼이 없다';
    END IF;
END
$$;

-- 1. 임시 호환 View를 먼저 내린다. CASCADE는 쓰지 않는다 —
--    예상 못 한 dependent object가 있으면 여기서 실패해야 한다.
DROP VIEW v_alarm_event;

-- 2. 빈 V4 table을 버린다. 여기까지 왔으면 0행이 확인된 상태다.
DROP TABLE r03_alarm_history;

-- 3. 아래는 canonical DDL과 동일하다.

CREATE TABLE r03_alarm_history (
    alarm_id          varchar(24) NOT NULL,
    occurred_at       timestamp   NOT NULL,
    lot_hist_id       varchar(20) NOT NULL,
    lot_id            varchar(20) NOT NULL,
    equipment_id      varchar(20) NOT NULL,
    chamber_id        varchar(24) NOT NULL,
    parameter_id      varchar(20) NOT NULL,
    recipe_step_no    smallint    NOT NULL,
    trigger_wafer_no  smallint    NOT NULL,
    member_wafer_refs jsonb       NOT NULL,
    member_alarm_refs jsonb       NOT NULL,
    policy_version    varchar(20) NOT NULL,

    CONSTRAINT r03_alarm_history_pkey PRIMARY KEY (alarm_id),

    -- delete action은 둘 다 NO ACTION이다. CASCADE면 base 9 DELETE가 R03로 번진다.
    CONSTRAINT r03_alarm_history_lot_hist_id_fkey
        FOREIGN KEY (lot_hist_id) REFERENCES lot_history (lot_hist_id),
    CONSTRAINT r03_alarm_history_parameter_id_fkey
        FOREIGN KEY (parameter_id) REFERENCES dim_parameter (parameter_id),

    CONSTRAINT r03_alarm_history_incident_key
        UNIQUE (lot_hist_id, parameter_id, recipe_step_no, policy_version),

    -- CHECK 7개. 개수와 이름은 successor 경로와 **같아야** 한다(계획 §5.2).
    CONSTRAINT r03_alarm_history_alarm_id_check
        CHECK (alarm_id ~ '^R03-[0-9a-f]{20}$'),
    CONSTRAINT r03_alarm_history_recipe_step_no_check
        CHECK (recipe_step_no >= 1),
    CONSTRAINT r03_alarm_history_trigger_wafer_no_check
        CHECK (trigger_wafer_no >= 1),
    CONSTRAINT r03_alarm_history_member_wafer_refs_array_check
        CHECK (jsonb_typeof(member_wafer_refs) = 'array'),
    CONSTRAINT r03_alarm_history_member_wafer_refs_len_check
        CHECK (jsonb_array_length(member_wafer_refs) = 3),
    CONSTRAINT r03_alarm_history_member_alarm_refs_array_check
        CHECK (jsonb_typeof(member_alarm_refs) = 'array'),
    CONSTRAINT r03_alarm_history_policy_version_check
        CHECK (policy_version = 'R03_CONSEC_V1')
);

COMMENT ON TABLE r03_alarm_history IS
    'V5-CM-3.1 final reference extension. R03 파생은 V5-A-1.4가 적재한다.';

-- v_alarm_event: TRACE·SUMMARY·R03을 UNION ALL한 17컬럼 (설계 §3.5).
--
-- final epoch에서 alarm table의 `wafer`는 varchar(24) = wafer_id 문자열이다.
-- lot_history는 wafer_no(smallint)와 wafer_id(varchar)를 **모두** 갖는다. 그래서
-- resolve는 반드시 `h.wafer_id = a.wafer`이고, View는 두 값을 별도 컬럼으로 낸다
-- (설계 §3.3 `:415`).
--
-- TRACE·SUMMARY는 LEFT JOIN이다. lot_history resolve가 실패해도 저장된 알람을
-- 숨기지 않는다. R03는 유효 FK를 전제로 하므로 JOIN이다.
CREATE VIEW v_alarm_event AS
SELECT
    'TRACE'::varchar(10)            AS source,
    a.alarm_id::varchar(24)         AS alarm_id,
    a.occurred_at                   AS occurred_at,
    a.area                          AS area,
    a.equipment                     AS equipment_id,
    a.chamber                       AS chamber_id,
    a.parameter                     AS parameter_id,
    a.recipe                        AS recipe_id,
    h.lot_hist_id                   AS lot_hist_id,
    a.lot                           AS lot_id,
    a.wafer                         AS wafer_id,
    h.wafer_no                      AS wafer_no,
    a.step_no                       AS recipe_step_no,
    a.seq_no                        AS seq_no,
    a.value                         AS value,
    a.alarm_type                    AS alarm_type,
    'TRACE_OOS'::varchar(20)        AS rule_code
FROM trace_alarm_history AS a
LEFT JOIN lot_history AS h
  ON h.lot_id = a.lot
 AND h.wafer_id = a.wafer
 AND h.chamber_id = a.chamber

UNION ALL

SELECT
    'SUMMARY'::varchar(10)          AS source,
    a.alarm_id::varchar(24)         AS alarm_id,
    a.occurred_at                   AS occurred_at,
    a.area                          AS area,
    a.equipment                     AS equipment_id,
    a.chamber                       AS chamber_id,
    a.parameter                     AS parameter_id,
    a.recipe                        AS recipe_id,
    h.lot_hist_id                   AS lot_hist_id,
    a.lot                           AS lot_id,
    a.wafer                         AS wafer_id,
    h.wafer_no                      AS wafer_no,
    a.step_no                       AS recipe_step_no,
    NULL::smallint                  AS seq_no,
    a.stat_value                    AS value,
    a.alarm_type                    AS alarm_type,
    'SUMMARY_OOC'::varchar(20)      AS rule_code
FROM summary_alarm_history AS a
LEFT JOIN lot_history AS h
  ON h.lot_id = a.lot
 AND h.wafer_id = a.wafer
 AND h.chamber_id = a.chamber

UNION ALL

SELECT
    'R03'::varchar(10)              AS source,
    a.alarm_id::varchar(24)         AS alarm_id,
    a.occurred_at                   AS occurred_at,
    h.area_id                       AS area,
    a.equipment_id                  AS equipment_id,
    a.chamber_id                    AS chamber_id,
    a.parameter_id                  AS parameter_id,
    h.recipe_id                     AS recipe_id,
    a.lot_hist_id                   AS lot_hist_id,
    a.lot_id                        AS lot_id,
    h.wafer_id                      AS wafer_id,
    h.wafer_no                      AS wafer_no,
    a.recipe_step_no                AS recipe_step_no,
    NULL::smallint                  AS seq_no,
    NULL::numeric(12,4)             AS value,
    'OOS'::varchar(10)              AS alarm_type,
    'R03_CONSEC'::varchar(20)       AS rule_code
FROM r03_alarm_history AS a
JOIN lot_history AS h ON h.lot_hist_id = a.lot_hist_id;
