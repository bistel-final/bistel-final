-- V5-D-2.4 질의 이력 nl_query_log (evaluation-only)
--
-- 대상: `kosa_text2sql` **만**. runtime(`kosa_agent`)·E2E(`kosa_agent_e2e`)·
-- reference(`fdc_final`)에는 이 테이블도, 쓰기 권한도 만들지 않는다 (완료 기준).
-- migration identity는 `v5_002_text2sql_nl_query_log`다.
--
-- 만드는 것: `nl_query_log` 테이블 + 최소권한 grant **그 둘뿐이다.**
--   - kosa_query_logger: INSERT + id 시퀀스 (기록 전용, SELECT 없음 — 위조·삭제 차단)
--   - kosa_readonly   : SELECT (GET /analytics/history 조회 전용)
--
-- 만들지 않는 것: 계정(role). role은 cluster 전역 객체라 V5-CM-3.5의
-- `apply_postgres_role_matrix.py`가 소유한다. 계정이 없으면 아래 가드가
-- 실행을 중단시킨다.
--
-- 적용은 `psql -v ON_ERROR_STOP=1 --single-transaction -f`로 한다. 파일 안에
-- BEGIN/COMMIT을 두지 않는다.

-- ---------------------------------------------------------------------
-- 0. 가드 — 대상 DB·선행 계정 확인
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF current_database() <> 'kosa_text2sql' THEN
        RAISE EXCEPTION
            'nl_query_log 는 kosa_text2sql 전용이다 (현재: %). runtime·E2E·reference DB 에는 만들지 않는다.',
            current_database();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kosa_query_logger') THEN
        RAISE EXCEPTION
            'kosa_query_logger 계정이 없다. V5-CM-3.5 role core를 먼저 적용하라.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kosa_readonly') THEN
        RAISE EXCEPTION
            'kosa_readonly 계정이 없다. V5-CM-3.5 role core를 먼저 적용하라.';
    END IF;
END
$$;

-- ---------------------------------------------------------------------
-- 1. 테이블 — schemas.NlQueryLogItem 과 1:1
-- ---------------------------------------------------------------------
CREATE TABLE nl_query_log (
    nl_query_log_id bigserial    PRIMARY KEY,
    asked_at        timestamptz  NOT NULL DEFAULT now(),
    question        text         NOT NULL,
    generated_sql   text,
    outcome         varchar(20)  NOT NULL,
    is_valid        boolean      NOT NULL,
    is_rejected     boolean      NOT NULL,
    reject_reason   text,
    row_cnt         integer,
    latency_ms      integer,
    error_msg       text,

    CONSTRAINT nl_query_log_outcome_check CHECK (
        outcome IN ('SUCCESS', 'POLICY_REJECTED', 'VALIDATION_FAILED', 'DB_ERROR')
    ),
    CONSTRAINT nl_query_log_row_cnt_check CHECK (row_cnt IS NULL OR row_cnt >= 0),
    CONSTRAINT nl_query_log_latency_ms_check CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

COMMENT ON TABLE nl_query_log IS
    'V5-D-2.4 질의 이력 (evaluation-only). 성공·정책 거부·실행 오류를 기록한다.';

-- 조회 정렬(asked_at DESC, nl_query_log_id DESC)과 동일한 index
CREATE INDEX nl_query_log_asked_at_idx
    ON nl_query_log (asked_at DESC, nl_query_log_id DESC);

-- ---------------------------------------------------------------------
-- 2. 최소권한 — logger 는 INSERT 만, 조회는 readonly 만
-- ---------------------------------------------------------------------
GRANT CONNECT ON DATABASE kosa_text2sql TO kosa_query_logger;
GRANT USAGE   ON SCHEMA public          TO kosa_query_logger;
REVOKE ALL    ON nl_query_log           FROM kosa_query_logger;
GRANT INSERT  ON nl_query_log           TO kosa_query_logger;
-- INSERT ... RETURNING nl_query_log_id 는 반환 컬럼에 SELECT 권한이 필요하다.
-- id 컬럼 하나만 컬럼 단위로 허용한다 — 다른 컬럼 조회는 여전히 불가하다.
GRANT SELECT (nl_query_log_id) ON nl_query_log TO kosa_query_logger;
GRANT USAGE, SELECT ON SEQUENCE nl_query_log_nl_query_log_id_seq
    TO kosa_query_logger;

GRANT CONNECT ON DATABASE kosa_text2sql TO kosa_readonly;
GRANT USAGE   ON SCHEMA public          TO kosa_readonly;
GRANT SELECT  ON nl_query_log           TO kosa_readonly;

-- ---------------------------------------------------------------------
-- 3. 결과 확인 — logger 는 INSERT 만, readonly 는 SELECT 만 나와야 한다
-- ---------------------------------------------------------------------
SELECT
    grantee,
    string_agg(DISTINCT privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_name = 'nl_query_log'
  AND grantee IN ('kosa_readonly', 'kosa_query_logger')
GROUP BY grantee
ORDER BY grantee;
