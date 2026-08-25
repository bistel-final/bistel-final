-- 003_agent_run_severity_pair — action/severity pair guard (V5-CM-3.3)
--
-- ## 왜 필요한가 — 이름 문제가 아니다
--
-- `002_agent_runtime_clean.sql`의 익명 CHECK가 4조합을 강제한다고 알려져 있었으나
-- 사실이 아니다. PostgreSQL CHECK는 결과가 **FALSE일 때만** 거부하는데, 반쪽 NULL은
-- 식 전체가 NULL이 되어 통과한다.
--
--     action='WARNING', severity=NULL
--       (action IS NULL AND severity IS NULL)          → FALSE
--       (action='MONITORING' AND severity='LOW')       → FALSE
--       (action='WARNING' AND severity='MEDIUM')       → TRUE AND NULL = NULL
--       (action='EQP_HOLD' AND severity='HIGH')        → FALSE
--       ────────────────────────────────────────────────────────
--       FALSE OR FALSE OR NULL OR FALSE                → NULL → 통과
--
-- PostgreSQL 16 실측에서 16조합 중 **10건**이 수락됐다(기대 4건). 반쪽 NULL 6종이
-- 전부 구멍이다.
--
-- ## 왜 002를 고치지 않는가
--
-- `002`는 공용 두 Runtime DB에 이미 적용됐고 `V5-CM-3.2`가 그 상태를
-- `agent_runtime_final` marker로 증명했다. 파일을 고치면 `migration_sha256`이 바뀌어
-- 그 marker 2본이 무효가 된다. successor로 더한다.
--
-- ## 새 CHECK — 3값 논리에 기대지 않는다
--
-- `IS NOT NULL` 가드를 앞에 세워 어떤 입력에서도 boolean을 반환한다.
-- 실측: TRUE 4 · FALSE 12 · **NULL 0**.
--
-- ## 적용 계약
--
-- 이 파일은 runner(`apply_severity_pair_guard.py`)로만 적용한다. `psql -f` 직접
-- 실행은 지원하지 않는다 — advisory lock·행 사전검증·16조합 승인 Gate·receipt·marker가
-- runner 소유이기 때문이다.
--
-- `NOT VALID`을 쓰지 않는다. 쓰면 기존 row가 재검증을 건너뛰어, 이 migration이 막으려는
-- 바로 그 구멍이 남는다.

DO $$
DECLARE
    violations bigint;
    predecessor_def text;
BEGIN
    IF current_database() NOT IN ('kosa_agent', 'kosa_agent_e2e') THEN
        RAISE EXCEPTION '003은 runtime database 에만 적용한다: %', current_database();
    END IF;

    -- predecessor가 그대로여야 교체가 의미를 갖는다. 이미 named successor가 있거나
    -- 익명 CHECK가 없으면 상태를 추정하지 않고 멈춘다.
    -- **이름만 보지 않는다.** 같은 이름의 `CHECK (true)`가 있으면 그것을 그대로
    -- drop하고 지나간다 — 무엇을 교체했는지 모르는 상태가 된다.
    SELECT pg_get_constraintdef(oid, true) INTO predecessor_def
    FROM pg_constraint
    WHERE conrelid = 'public.agent_run'::regclass
      AND conname = 'agent_run_check1'
      AND contype = 'c';

    IF predecessor_def IS NULL THEN
        RAISE EXCEPTION '003 predecessor agent_run_check1 이 없다';
    END IF;

    -- 기대값은 **PostgreSQL 16 실측**이다. 손으로 적으면 괄호·cast 표기가 달라
    -- 정상 경로까지 막힌다. 공백만 접어 비교한다.
    IF regexp_replace(predecessor_def, '\\s+', ' ', 'g') IS DISTINCT FROM
       regexp_replace($def$CHECK (action IS NULL AND severity IS NULL OR action::text = 'MONITORING'::text AND severity::text = 'LOW'::text OR action::text = 'WARNING'::text AND severity::text = 'MEDIUM'::text OR action::text = 'EQP_HOLD'::text AND severity::text = 'HIGH'::text)$def$, '\\s+', ' ', 'g')
    THEN
        RAISE EXCEPTION '003 predecessor 정의가 계약과 다르다';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.agent_run'::regclass
          AND conname = 'ck_agent_run_action_severity_pair'
    ) THEN
        RAISE EXCEPTION '003 successor constraint 가 이미 있다';
    END IF;

    -- ADD CONSTRAINT 가 어차피 전수 재검증하지만, 여기서 먼저 세어 두면 실패 원인이
    -- "기존 데이터가 계약을 위반한다"로 분명해진다.
    SELECT count(*) INTO violations
    FROM public.agent_run
    WHERE NOT (
        (action IS NULL AND severity IS NULL)
        OR (
            action IS NOT NULL
            AND severity IS NOT NULL
            AND (
                (action = 'MONITORING' AND severity = 'LOW')
                OR (action = 'WARNING' AND severity = 'MEDIUM')
                OR (action = 'EQP_HOLD' AND severity = 'HIGH')
            )
        )
    );

    IF violations > 0 THEN
        RAISE EXCEPTION '003 적용 전 pair 위반 행이 있다: %', violations;
    END IF;
END
$$;

ALTER TABLE public.agent_run
    DROP CONSTRAINT agent_run_check1,
    ADD CONSTRAINT ck_agent_run_action_severity_pair CHECK (
        (action IS NULL AND severity IS NULL)
        OR (
            action IS NOT NULL
            AND severity IS NOT NULL
            AND (
                (action = 'MONITORING' AND severity = 'LOW')
                OR (action = 'WARNING' AND severity = 'MEDIUM')
                OR (action = 'EQP_HOLD' AND severity = 'HIGH')
            )
        )
    );
