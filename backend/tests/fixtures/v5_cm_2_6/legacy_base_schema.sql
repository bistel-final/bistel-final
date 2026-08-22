-- V5-CM-2.6 전용 legacy fixture (vendored)
--
-- 원본: infra/bootstrap/001_base_schema.sql (PR #40)
--       backend/migrations/001_reference_extensions.sql (PR #48) 의 r03·view 부분
--
-- `V5-CM-1.7`이 `infra/bootstrap/001_base_schema.sql`을 삭제하면 2.6의 legacy View
-- fingerprint 회귀가 깨진다(구현리뷰 1차 권장 1 · 계획리뷰 7차 권장 1). 그래서 필요한
-- 최소본만 여기로 vendoring한다. 이 파일은 **격리 fingerprint 재현 전용**이며 공용 DB
-- 적용·COPY·복구 입력으로 쓰지 않는다.

-- =====================================================================
-- FDC clean base schema — source-anchored corrected overlay
-- Source archive: kosa_0813.zip
-- Source member: kosa_0813/클린데이터셋/03_schema_clean.sql
-- Source member SHA-256:
--   bf6cc620065850a0e15e052179a1ba25b9fc3bec30966ca2480d77fb27212d9b
--
-- Structural DDL is preserved from the registered source member. Only the
-- stale path and semantic comments listed in infra/bootstrap/README.md are
-- corrected for the v2 contract. This file contains schema only: no data,
-- role, database, transaction, or destructive statements.
--
-- 용어: judgement/SPC 미사용. 이탈=alarm_type(OOS/OOC/IN), 계측=alarm_result(PASS/FAIL)
-- 네이밍: area=photo/etch · EQP01~06 · EQP0x-PMy · RECIPE01/02 · LOT001 · LOT001W001 · step=번호
-- 적재: \copy <table> FROM '클린데이터셋/postgres/<table>.csv' CSV HEADER
-- =====================================================================

-- 파라미터 기준정보 (raw Trace/evaluation 고정 5선 기준)
CREATE TABLE dim_parameter (
    parameter_id   varchar(20) PRIMARY KEY,
    parameter_name varchar(60),
    unit           varchar(20),
    area           varchar(10),                  -- photo | etch
    target_value   numeric(12,4),
    spec_lower     numeric(12,4),                -- LSL
    ctrl_lower     numeric(12,4),                -- LCL
    ctrl_upper     numeric(12,4),                -- UCL
    spec_upper     numeric(12,4),                -- USL
    upper_only     boolean DEFAULT false         -- true면 하한 미판정 (예: ET_REFL)
);
COMMENT ON TABLE dim_parameter IS '파라미터 raw Trace/evaluation 고정 한계 5선(LSL/LCL/TARGET/UCL/USL). Summary 동적 CL±3σ는 별도 계산';

-- 처리 이력
CREATE TABLE lot_history (
    lot_hist_id       varchar(20) PRIMARY KEY,  -- WAFER 1장이 STEP 1개를 지난 기록의 대리키
    lot_id            varchar(20) NOT NULL,     -- LOT001 …
    wafer_no          smallint    NOT NULL,     -- LOT 내 웨이퍼 번호 1~25
    wafer_id          varchar(24),              -- LOT001W001 (lot 종속 표기)
    device_id         varchar(20),              -- 제품종류 (DRAM-8G / LOGIC-7N)
    step_id           varchar(20),              -- 공정 스텝 (CT-PHOTO / CT-ETCH)
    area_id           varchar(10),              -- photo | etch
    equipment_id      varchar(20),              -- EQP01 … EQP06
    chamber_id        varchar(24),              -- EQP0x-PMy
    recipe_id         varchar(20),              -- RECIPE01 | RECIPE02
    track_in_at       timestamp,                -- 장비 투입 시각
    track_out_at      timestamp,                -- 장비 배출 시각
    duration_sec      integer,
    chamber_wafer_cum integer,                  -- 챔버 누적 처리 순번 (연속 판정 정렬 기준)
    lot_seq           integer,                  -- LOT 처리 순서
    fault_code        varchar(10)               -- NRM placeholder. 공개 Fault 정답이 아니며 판단 입력 금지
);
COMMENT ON COLUMN lot_history.chamber_wafer_cum IS '챔버 처리 순번. 연속 이탈 판정 시 LOT 경계를 넘어 이 값 오름차순으로 정렬';

CREATE TABLE fdc_trace (
    lot_hist_id    varchar(20) NOT NULL REFERENCES lot_history(lot_hist_id),
    parameter_id   varchar(20) NOT NULL REFERENCES dim_parameter(parameter_id),
    seq_no         smallint    NOT NULL,        -- 측정 시점 순번
    recipe_step_no smallint,                    -- recipe 내 스텝 번호 (1,2)
    step_seq       smallint,                    -- = recipe_step_no
    measured_at    timestamp,
    value          numeric(12,4),               -- raw 측정값
    PRIMARY KEY (lot_hist_id, parameter_id, seq_no)
);
COMMENT ON TABLE fdc_trace IS 'WAFER×파라미터 원본 시계열. trace_alarm(규격 이탈)의 입력';

-- summary_data: 통계만
CREATE TABLE summary_data (
    lot_hist_id varchar(20) NOT NULL REFERENCES lot_history(lot_hist_id),
    area        varchar(10),
    equipment   varchar(20),
    chamber     varchar(24),
    parameter   varchar(20),
    recipe      varchar(20),
    lot         varchar(20),
    wafer       smallint,
    step_no     smallint,                       -- recipe step 번호
    step_seq    smallint,
    value_mean  numeric(12,4),                  -- 평균
    value_std   numeric(12,4),                  -- 표본표준편차(ddof=1)
    value_min   numeric(12,4),
    value_max   numeric(12,4),
    point_cnt   smallint,                       -- 측정 점 수
    PRIMARY KEY (lot_hist_id, parameter, step_no)
);
COMMENT ON TABLE summary_data IS '통계만 (mean·std·min·max·count). fdc_trace 집계. 판정 없음';

-- evaluation: 이탈 개수 + alarm_type
CREATE TABLE evaluation (
    lot_hist_id   varchar(20) NOT NULL REFERENCES lot_history(lot_hist_id),
    area          varchar(10),
    equipment     varchar(20),
    chamber       varchar(24),
    parameter     varchar(20),
    recipe        varchar(20),
    lot           varchar(20),
    wafer         smallint,
    step_no       smallint,
    step_seq      smallint,
    point_cnt     smallint,
    ooc_point_cnt smallint,                     -- 관리한계 이탈 점 수
    oos_point_cnt smallint,                     -- 규격한계 이탈 점 수
    alarm_type    varchar(10) CHECK (alarm_type IN ('OOS','OOC','IN')),
    PRIMARY KEY (lot_hist_id, parameter, step_no)
);
COMMENT ON TABLE evaluation IS '각 값의 이탈 개수·분류(alarm_type) 적재. 값 vs 한계선';

-- trace_alarm_history: raw 규격 이탈(OOS)
CREATE TABLE trace_alarm_history (
    alarm_id    varchar(20) PRIMARY KEY,
    occurred_at timestamp,
    area        varchar(10),
    equipment   varchar(20),
    chamber     varchar(24),
    parameter   varchar(20),
    recipe      varchar(20),
    lot         varchar(20),
    wafer       smallint,
    step_no     smallint,
    step_seq    smallint,
    seq_no      smallint,                       -- 이탈 발생 측정 점
    value       numeric(12,4),                  -- 이탈 값
    limit_type  varchar(4) CHECK (limit_type IN ('USL','LSL')),
    limit_value numeric(12,4),
    alarm_type  varchar(10) DEFAULT 'OOS'
);
COMMENT ON TABLE trace_alarm_history IS 'raw 점이 규격(USL/LSL)을 벗어난 알람 (하드리밋). 점 단위';

-- summary_alarm_history: 통계 관리 이탈(OOC)
CREATE TABLE summary_alarm_history (
    alarm_id       varchar(20) PRIMARY KEY,
    occurred_at    timestamp,
    area           varchar(10),
    equipment      varchar(20),
    chamber        varchar(24),
    parameter      varchar(20),
    recipe         varchar(20),
    lot            varchar(20),
    wafer          smallint,
    step_no        smallint,
    step_seq       smallint,
    statistic_type varchar(10),                 -- 어떤 통계인가 (mean …)
    stat_value     numeric(12,4),               -- 그 통계값
    cl             numeric(12,4),               -- 중심선 (정상 데이터 평균)
    ucl            numeric(12,4),               -- 관리상한 (= CL + 3σ)
    lcl            numeric(12,4),               -- 관리하한 (= CL - 3σ)
    limit_type     varchar(4) CHECK (limit_type IN ('UCL','LCL')),
    alarm_type     varchar(10) DEFAULT 'OOC'
);
COMMENT ON TABLE summary_alarm_history IS '통계값이 관리한계(UCL/LCL=mean±3σ)를 벗어난 알람. 관리한계는 정상 웨이퍼로만 산출';

-- 계측
CREATE TABLE metrology (
    metrology_id   varchar(20) PRIMARY KEY,
    lot_hist_id    varchar(20),
    lot_id         varchar(20),
    wafer_no       smallint,
    wafer_id       varchar(24),
    step_id        varchar(20),
    measure_type   varchar(20),                 -- CD_ADI | CD_AEI
    unit           varchar(20),
    measured_value numeric(12,4),
    spec_center    numeric(12,4),
    spec_lower     numeric(12,4),
    spec_upper     numeric(12,4),
    alarm_result   varchar(10) CHECK (alarm_result IN ('PASS','FAIL')),
    measured_at    timestamp
);
COMMENT ON TABLE metrology IS '계측(CD) PASS/FAIL 결과. 제품 CD 품질 근거이며 Fault Mode 정답이 아님';

-- 조치
CREATE TABLE action_history (
    action_id                 varchar(20) PRIMARY KEY,
    lot_id                    varchar(20),
    recipe_step_name          varchar(40),
    equipment_id              varchar(20),
    chamber_id                varchar(24),
    trigger_alarm_lot_hist_id varchar(20),
    action_code               varchar(20),      -- MONITORING|WARNING|EQP_HOLD
    reason                    text,
    approval_required         char(1),          -- Y|N
    approval_status           varchar(12),      -- AUTO|PENDING|APPROVED|REJECTED
    approved_by               varchar(40),
    approved_at               timestamp,
    notify_status             varchar(12),      -- 담당자 이메일 통지: SENT|'' (WARNING·EQP_HOLD)
    notify_at                 timestamp,
    mes_status                varchar(12),      -- MES 홀드 집행: SENT|WAITING|'' (EQP_HOLD, 승인 후 SENT)
    mes_at                    timestamp,
    created_at                timestamp
);
COMMENT ON TABLE action_history IS '조치 이력. 알람은 WAFER 단위, 조치는 (lot,chamber) incident 단위';
COMMENT ON COLUMN action_history.notify_status IS '담당자 이메일 통지. WARNING·EQP_HOLD는 SENT, MONITORING은 통지 없음(관찰만)';
COMMENT ON COLUMN action_history.mes_status IS 'MES 설비홀드 집행. EQP_HOLD만: 승인 시 SENT, 승인 대기 시 WAITING';

CREATE INDEX ix_summary_data_key ON summary_data (chamber, parameter, step_no);
CREATE INDEX ix_evaluation_type ON evaluation (alarm_type);
CREATE INDEX ix_trace_alarm_time ON trace_alarm_history (occurred_at);
CREATE INDEX ix_lot_history_cum ON lot_history (chamber_id, chamber_wafer_cum);
