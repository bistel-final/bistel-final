-- =====================================================================
-- V4-D-1.2 analytics 최소권한 계정
--
-- Text2SQL 의 1차 방어선은 코드가 아니라 DB 권한이다. 검증기(V4-D-2.2)가
-- 뚫려도 계정에 쓰기 권한이 없으면 데이터는 바뀌지 않는다.
--
-- 이 파일은 계정과 권한만 만든다. 테이블은 001_reference_extensions.sql 이
-- 이미 생성했으므로 여기서 스키마를 바꾸지 않는다.
--
-- 실행 전 CHANGE_ME_READONLY 와 CHANGE_ME_LOGGER 를 실제 비밀번호로 바꾼다.
-- 바꾼 파일은 커밋하지 않는다. 저장소에는 CHANGE_ME 상태를 유지한다.
--
-- 비밀번호는 영숫자만 사용한다. @ : / # 이 들어가면 DSN URL 파싱이 깨진다.
--
-- 멱등이다. 계정이 이미 있으면 비밀번호만 재설정한다.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. kosa_readonly — Text2SQL 실행 전용
--
-- 서버에 수동 생성돼 있고 비밀번호가 기본값이라 재설정한다.
-- SELECT 외 권한은 부여하지 않는다.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kosa_readonly') THEN
        EXECUTE 'ALTER ROLE kosa_readonly LOGIN PASSWORD ''CHANGE_ME_READONLY''';
    ELSE
        EXECUTE 'CREATE ROLE kosa_readonly LOGIN PASSWORD ''CHANGE_ME_READONLY''';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE kosa_agent TO kosa_readonly;
GRANT USAGE   ON SCHEMA public       TO kosa_readonly;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO kosa_readonly;

-- 앞으로 추가될 테이블에도 SELECT 만 자동 부여한다.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO kosa_readonly;

-- 방어선 확인: 쓰기 권한이 남아 있으면 회수한다.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM kosa_readonly;

-- ---------------------------------------------------------------------
-- 2. kosa_query_logger — nl_query_log append-only 기록 전용
--
-- FR-D-05. 기록만 한다. 조회는 app 계정이 담당하므로 SELECT 를 주지 않는다.
-- 로그 위조·삭제를 계정 수준에서 차단한다.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kosa_query_logger') THEN
        EXECUTE 'ALTER ROLE kosa_query_logger LOGIN PASSWORD ''CHANGE_ME_LOGGER''';
    ELSE
        EXECUTE 'CREATE ROLE kosa_query_logger LOGIN PASSWORD ''CHANGE_ME_LOGGER''';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE kosa_agent TO kosa_query_logger;
GRANT USAGE   ON SCHEMA public       TO kosa_query_logger;

-- 상속된 권한이 있으면 먼저 비운 뒤 필요한 것만 준다.
REVOKE ALL   ON ALL TABLES IN SCHEMA public FROM kosa_query_logger;
GRANT INSERT ON public.nl_query_log         TO   kosa_query_logger;

-- nl_query_log_id 가 bigserial 이므로 시퀀스 권한이 없으면 INSERT 가 실패한다.
GRANT USAGE, SELECT ON SEQUENCE public.nl_query_log_nl_query_log_id_seq
    TO kosa_query_logger;

-- ---------------------------------------------------------------------
-- 3. 결과 확인
--
-- kosa_readonly 는 SELECT 만, kosa_query_logger 는 nl_query_log INSERT 만
-- 나와야 한다.
-- ---------------------------------------------------------------------
SELECT
    grantee,
    table_name,
    string_agg(DISTINCT privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE grantee IN ('kosa_readonly', 'kosa_query_logger')
GROUP BY grantee, table_name
ORDER BY grantee, table_name;
