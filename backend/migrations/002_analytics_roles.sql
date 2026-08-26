-- RETIRED: V4 analytics role migration
--
-- 이 파일의 `ALL TABLES`·future default grant·password reset은 final profile별
-- 최소권한 계약과 충돌한다. 호환을 위해 경로만 남기되 어떤 실행 방식에서도
-- role 또는 ACL을 변경하기 전에 fail-closed한다.
--
-- 대체 경로: backend/scripts/apply_postgres_role_matrix.py

DO $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '0A000',
        MESSAGE = 'RETIRED_PIPELINE: use apply_postgres_role_matrix.py (V5-CM-3.5)';
END
$$;
