# D — Analytics

> 기준: 멘토 최종 패키지 (2026-08-18) · 역할분담 v10.0 D · WBS `V4-D-*`
> 마지막 동기화: 2026-08-18

## 담당 범위

Text2SQL 전 경로(자연어 질의 화면 5), 통계·차트 계획, 질의 로그,
`generate_analysis_plan` Tool. (감사로그 조회는 C 와 담당 협의 중 — README 참조)

## 최소 구현 (골든 시나리오)

```
POST /analytics/query   body { question }
→ { generated_sql, columns, rows, is_valid, is_rejected, reject_reason }
```

정책 거부도 HTTP 200 + `is_rejected=true`. 내부 확장 필드(outcome·visualization)는
상위 호환으로 추가 (04 문서 3장). 회귀 질문셋 시드: "챔버별 OOS 알람 수" ·
"파라미터별 알람 수" · "승인 대기 조치".

## 구현 완료 계층 (main 반영)

```
계정 방어선   kosa_readonly(SELECT만) · kosa_query_logger(nl_query_log INSERT만)
              backend/migrations/002_analytics_roles.sql — 실접속 검증 완료
DB pool       app/analytics/db_pool.py — (논리DB×용도) 4pool, 계정 강제, DSN 마스킹
preflight     app/analytics/preflight.py — manifest·실DSN(host·port·db) 대조
schema cache  app/analytics/schema_cache.py — 논리DB당 information_schema 1회
SQL 검증기    app/analytics/sql_validator.py — 방어 6종 전부 allowlist·fail-closed
              (객체 base9+ref6 · 함수 allowlist · 컬럼 manifest · 스코프 해석 ·
               카탈로그 차단 · LIMIT 500 주입) — fixture 105 케이스가 수용 기준
```

## 남은 경로 (WBS `V4-D-*`)

```
3.x  schema context → generate_analysis_plan Tool (LLM)
4.1  readonly executor (검증 통과 normalized_sql 만 실행)
4.2  QueryLog 4상태 기록 (logger pool)
4.3  파이프라인 조립 (plan→validate→execute→log)
5.2  차트 계획 (일자별=라인 잠정 · 챔버/파라미터별=누적막대 OOS/OOC)
5.3  API 노출 (멘토 6필드 + 확장)
7.x  평가 (분류=fault_code · 탐지=metrology.alarm_result — kosa_text2sql DB)
```

## 핵심 규칙

- LLM 생성 SQL 은 **검증기 통과 후 `normalized_sql` 을 `kosa_readonly` pool 로만** 실행.
- `fault_code` 는 Text2SQL 응답 가공·프롬프트에 넣지 않는다 (평가 정답 누수).
- 신본 값 체계 주의: area='Photo'/'Etch' · wafer=wafer_id 문자열 · recipe 4종 —
  질의 예시·프롬프트 컨텍스트·기대값 전부 신본 기준.
- Tool 계약 `AnalysisPlanToolInput/Result` (`app/common/tool_contracts.py`),
  응답 DTO `app/analytics/schemas.py` 가 정본.
