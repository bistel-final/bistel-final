-- Gate 0가 관측한 **legacy 행 수**를 격리 환경에서 재현한다.
--
-- 실제 legacy 데이터는 구 `kosa_0813` epoch이라 입력으로 쓸 수 없다(CLAUDE.md).
-- 그런데 `classify_base()`의 legacy 판정은 catalog hash와 **행 수**만 본다 —
-- legacy content hash는 pin되어 있지 않고 approval이 Gate 0 시점 실측을 동결한다
-- (계획 §4.2). 그래서 행 수만 정확히 맞춘 합성 데이터로 legacy 상태를 만든다.
--
-- 목표: dim_parameter 8 · lot_history 600 · fdc_trace 14400 · summary_data 4800
--       evaluation 4800 · metrology 48 · trace_alarm_history 126
--       summary_alarm_history 47 · action_history 0 (evaluation profile은 48)

INSERT INTO dim_parameter (parameter_id, parameter_name, unit, area)
SELECT 'PARAM' || lpad(g::text, 3, '0'), 'p' || g, 'unit', 'photo'
  FROM generate_series(1, 8) AS g;

INSERT INTO lot_history (lot_hist_id, lot_id, wafer_no, wafer_id)
SELECT 'LH' || lpad(g::text, 8, '0'),
       'LOT' || lpad(((g - 1) / 25 + 1)::text, 3, '0'),
       ((g - 1) % 25 + 1)::smallint,
       'W' || lpad(g::text, 8, '0')
  FROM generate_series(1, 600) AS g;

INSERT INTO fdc_trace (lot_hist_id, parameter_id, seq_no, value)
SELECT 'LH' || lpad(l::text, 8, '0'),
       'PARAM' || lpad(((s - 1) % 8 + 1)::text, 3, '0'),
       s::smallint,
       (l * s)::numeric
  FROM generate_series(1, 600) AS l, generate_series(1, 24) AS s;

-- PK (lot_hist_id, parameter, step_no) — 600 lot × 8 parameter = 4,800
INSERT INTO summary_data (lot_hist_id, parameter, step_no, wafer, value_mean)
SELECT 'LH' || lpad(l::text, 8, '0'),
       'PARAM' || lpad(s::text, 3, '0'),
       1::smallint,
       ((l - 1) % 25 + 1)::smallint,
       (l * s)::numeric
  FROM generate_series(1, 600) AS l, generate_series(1, 8) AS s;

-- PK (lot_hist_id, parameter, step_no) — 600 lot × 8 parameter = 4,800
INSERT INTO evaluation (lot_hist_id, parameter, step_no, wafer, alarm_type)
SELECT 'LH' || lpad(l::text, 8, '0'),
       'PARAM' || lpad(s::text, 3, '0'),
       1::smallint,
       ((l - 1) % 25 + 1)::smallint,
       'IN'
  FROM generate_series(1, 600) AS l, generate_series(1, 8) AS s;

INSERT INTO metrology (metrology_id, lot_hist_id, wafer_no, alarm_result)
SELECT 'MT' || lpad(g::text, 8, '0'),
       'LH' || lpad(g::text, 8, '0'),
       ((g - 1) % 25 + 1)::smallint,
       'PASS'
  FROM generate_series(1, 48) AS g;

INSERT INTO trace_alarm_history (alarm_id, wafer, limit_type, alarm_type)
SELECT 'TA' || lpad(g::text, 8, '0'), ((g - 1) % 25 + 1)::smallint, 'USL', 'OOS'
  FROM generate_series(1, 126) AS g;

INSERT INTO summary_alarm_history (alarm_id, wafer, limit_type, alarm_type)
SELECT 'SA' || lpad(g::text, 8, '0'), ((g - 1) % 25 + 1)::smallint, 'UCL', 'OOC'
  FROM generate_series(1, 47) AS g;
