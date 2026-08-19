# 03. 데이터베이스 규칙

> 기준 원천: 멘토 최종 패키지 `sample/schema/03_schema_clean.sql` (2026-08-18)
> 보조 기준: 시스템설계서 v2.0 2~3·13~14장 · `backend/migrations/`
> 마지막 동기화: 2026-08-18

---

## 1. 스키마 계층

```
[정본] 멘토 03_schema_clean.sql        base 9 table (아래 2장)
  + backend/migrations/001_reference_extensions.sql   reference 6종
     (r03_alarm_history · v_alarm_event · nl_query_log ·
      document_corpus · document · document_chunk)
  + infra 002_agent_runtime_clean                     agent_run 등 runtime 계열
  + backend/migrations/002_analytics_roles.sql        계정·권한 (4장)
```

> ⚠️ **정합 검증 필요**: 확장 계층(001·002)은 구본 스키마 기준으로 작성됐다.
> 신본에서 `wafer` 가 `varchar(24)` 로 바뀌는 등 타입 변경이 있어
> `v_alarm_event`(UNION 뷰) 등은 신본 적재 후 재검증한다. (Common 전환 작업과 연동)

## 2. base 9 table 요점 (신본 DDL 확정 사항)

| 테이블 | PK | 비고 |
|---|---|---|
| `dim_parameter` | parameter_id | **한계선 5선의 유일한 출처** (LSL<LCL<TARGET<UCL<USL). `upper_only=true` 는 하한 미판정 (예: ET_REFL) |
| `lot_history` | lot_hist_id | wafer 1장 × step 1개 기록. `chamber_wafer_cum` = 챔버 누적 순번 — **R03 연속 판정의 정렬 기준 (LOT 경계 넘음)** |
| `fdc_trace` | (lot_hist_id, parameter_id, seq_no) | raw 시계열. trace_alarm 의 입력 |
| `summary_data` | (lot_hist_id, parameter, step_no) | 통계만 (mean·std(ddof=1)·min·max·count). **판정 없음** |
| `evaluation` | (lot_hist_id, parameter, step_no) | 이탈 점 수 + `alarm_type` CHECK ('OOS','OOC','IN') |
| `trace_alarm_history` | alarm_id | raw 점의 규격 이탈. `limit_type` CHECK ('USL','LSL') |
| `summary_alarm_history` | alarm_id | 통계의 관리 이탈. UCL/LCL = 정상 wafer 평균 ±3σ. `limit_type` CHECK ('UCL','LCL') |
| `metrology` | metrology_id | 계측(CD_ADI/CD_AEI). `alarm_result` CHECK ('PASS','FAIL') = 탐지 평가 정답 |
| `action_history` | action_id | 조치. `action_code` = MONITORING\|WARNING\|EQP_HOLD |

**컬럼 의미 확정 (DDL 주석 기준)**

```
action_history.approval_status   AUTO | PENDING | APPROVED | REJECTED
action_history.notify_status     담당자 이메일 통지. WARNING·EQP_HOLD → SENT, MONITORING 은 통지 없음
action_history.mes_status        MES 홀드 집행. EQP_HOLD 만: 승인 대기 WAITING → 승인 시 SENT
recipe 매핑                       RECIPE01·03 = Photo / RECIPE02·04 = Etch
wafer (summary·evaluation·alarm) varchar(24) — wafer_id 문자열 (LOT001W001)
area                             varchar — 'Photo' | 'Etch'
```

**구본 대비 주의** — 구본 기준 코드가 조용히 깨지는 지점

```
wafer 타입        integer → varchar(24)   조인·비교 캐스팅 확인
area 값           photo/etch → Photo/Etch  필터 상수 전수 교체
send_status 없음  통지·집행은 notify_status / mes_status 로 분리됐다
```

## 3. 논리 DB

```
Runtime    kosa_agent (+ kosa_agent_e2e)   agent 실행으로 행이 늘어나는 write state
평가       kosa_text2sql                    immutable snapshot
```

- 두 논리 DB 는 같은 source 에서 나와야 하며 같은 물리 DB 를 가리키면 안 된다.
  기동 시 `app/analytics/preflight.py` 가 manifest 와 실제 DSN(host·port·database)을
  대조해 강제한다.
- 스키마 조회는 `app/analytics/schema_cache.py` (논리 DB 당 information_schema 1회).
  migration 진행도 차이는 정상이며 `diff_tables()` 로만 노출한다.
  **migration 적용 후에는 워커 재시작 또는 `invalidate()` 가 배포 절차에 포함돼야 한다.**

## 4. 계정·권한 (1차 방어선)

정본: `backend/migrations/002_analytics_roles.sql` (멱등 · CHANGE_ME 치환 가드)

| 계정 | 권한 | 용도 |
|---|---|---|
| `kosa` | 관리 | 부트스트랩·적재 전용. 앱 코드에서 사용 금지 |
| `kosa_app` | 앱 쓰기 | Agent runtime |
| `kosa_readonly` | **SELECT 만** | LLM 생성 SQL 실행은 이 계정만 |
| `kosa_query_logger` | **nl_query_log INSERT + 시퀀스만** | 질의 로그 append-only |

- `app/analytics/db_pool.py` 가 (논리 DB × 용도) pool 별로 계정을 강제한다.
  QUERY 자리에 다른 계정 DSN 을 넣으면 기동이 거부된다.
- DSN·비밀번호는 예외·로그·repr 에 남지 않는다 (`hide_parameters` + 마스킹 PoolInfo).
- 검증된 사실 (실접속 확인): readonly 는 쓰기 거부, logger 는 INSERT 만 성공하고
  SELECT·타 테이블 거부.

## 5. DSN

`.env` 5종. `postgresql+psycopg://` 접두 필수. 비밀번호는 영숫자만 (URL 파싱).

```
APP_DATABASE_URL                kosa_app        → kosa_agent
TEXT2SQL_DATABASE_URL           kosa_readonly   → kosa_agent
TEXT2SQL_LOG_DATABASE_URL       kosa_query_logger → kosa_agent
TEXT2SQL_EVAL_DATABASE_URL      kosa_readonly   → kosa_text2sql
TEXT2SQL_EVAL_LOG_DATABASE_URL  kosa_query_logger → kosa_text2sql
```

## 6. bootstrap·manifest

- 적재 산출물은 manifest(`infra/bootstrap/manifests/*.json`, format v3)로 검증한다.
  Runtime 은 `runtime_clean`(write state), 평가는 `evaluation_mock`(immutable) stage 기준.
- 로컬은 corrected build 구축이 선행이다 (`01-project-rules.md` 5장).
- **신본 전환 시**: 대상 아카이브·source hash·manifest 전부 갱신 대상 (Common 주관).
  전환 전까지 공용 DB 는 구본 상태일 수 있으므로 수치 검증은 `sample/data/` 실측을 기준으로 한다.

## 7. Text2SQL allowlist (D)

- 객체: base 9 + reference 6 만. `audit_log`·`agent_run` 등 runtime 계열은
  Text2SQL 에서 조회 불가 (전용 API 소관).
- 함수: allowlist 방식 (집계·수학·날짜·문자열·조건·윈도우 표준만).
- 컬럼: bootstrap manifest 의 컬럼 정의로 오프라인 판정.
- 정본: `app/analytics/sql_validator.py` · fixture `tests/unit/test_sql_validator.py`.
