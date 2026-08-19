# D — Analytics

> [!CAUTION]
> **사용 중지 — 아래 본문은 이전 epoch·부분 동기화 이력이며 구현 근거로 사용하면 안 됩니다.**
> 현재는 `docs/ai-context/README.md`에서 안내하는 최종 패키지 기준표와 v2.1 요구사항·설계·
> 역할분담·API v3만 사용합니다. WBS v5와 새 `V5-D-*` Task 문서가 확정되기 전에는 아래 본문의
> 참고·복사·프롬프트 입력을 금지합니다.

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-11
> 담당: 천승현 · 모듈 `backend/app/analytics/` · `frontend/src/features/analytics/`

Text2SQL 자연어 질의, SQL 안전장치, 통계·동적 차트, 감사로그 조회 화면, Text2SQL 평가를 책임진다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-D-01 | Tool `generate_analysis_plan` | 필수 |
| FR-D-02 | SQL 검증 (sqlglot) | 필수 |
| FR-D-03 | 읽기 전용 실행 | 필수 |
| FR-D-04 | 결과 표·통계·차트 | 필수 |
| FR-D-05 | 질의 이력 기록 | 필수 |
| FR-D-06 | 자연어 분석 화면 | 필수 |
| FR-D-07 | 감사로그 조회 API·화면 | 필수 |
| FR-D-08 | Text2SQL 평가 | 필수 |
| FR-D-09 | SQL 재검증 API | 권장 |
| FR-D-10 | Tool MCP wrapping | 도전 |

---

## 완료 기준

```
방어      9.2의 6종 전부 안전 차단·처리, 정책 위반은 재생성·실행 0회
골드      12건 중 10건 이상 (>= 83%)
          정수·문자열·ID 정확 비교 / 실수 절대오차 0.001
          정렬 요구 문항만 순서 비교 / 시각화 문항은 결과 + chart_type·x·y
교정      교정 가능 오류만 1회 재생성 (총 2회 시도), 재실패 시 안전 종료
실행      쓰기 시도 DB 레벨 거부, statement_timeout 5s, LIMIT 500
로그      성공·거부·검증 실패·실행 오류 4가지 상태 조합 일치
감사      A·C가 기록한 이벤트 9종이 화면에 표시
```

---

## 운영 DB와 평가 DB가 다르다 (중요)

```
운영  /analytics/query        TEXT2SQL_DATABASE_URL      → kosa_agent      최신 상태
평가  evaluate_text2sql.py    TEXT2SQL_EVAL_DATABASE_URL → kosa_text2sql   배포 초기 상태
```

- 운영 질의는 **승인·조치 화면과 같은 최신 상태**를 본다. `action_history` 결과가 전용 화면과 일치해야 한다.
- 골드 12건 기대값은 **평가 DB 기준**이다. 운영 상태를 참조하지 않는다.
- `evaluate_text2sql.py`는 평가 URL 2개만 읽고 **운영 URL로 fallback하지 않는다.**
- 두 DB의 `action_history` 스키마가 다르다(`001_agent_runtime.sql`은 `kosa_agent`에만 적용).
  **allowlist 컬럼 캐시와 프롬프트 스키마 컨텍스트를 pool별로 각각 만든다.** (설계 9.5)

---

## 허용 테이블 16종 (정확히 이것만)

```
dim_process_step  dim_recipe  dim_recipe_step  dim_equipment
dim_chamber  dim_sensor  dim_metrology_item  fdc_rule
code_fault  code_action
lot_history  fdc_trace  fdc_summary  fdc_alarm  metrology  action_history
```

`agent_run`·`approval_request`·`audit_log`·`document*`·`checkpoint*`·`action_delivery`·시스템 카탈로그는 **불허**.
해당 질문은 `POLICY_REJECTED`로 끝내고 전용 화면/API를 안내한다.

Tool 경계는 `{ok:false, reason:"POLICY_REJECTED: ..."}`를 유지한다. REST `POST /analytics/query`는 정책 거부를 HTTP 200 + `is_valid=false`, `is_rejected=true`, `reject_reason`으로 반환하고 SQL을 실행하지 않는다. 요청 body 누락·타입·길이 오류만 422다.

---

## sqlglot 검증 순서 (12단계 — 순서를 바꾸지 않는다)

```
1 빈 SQL 거부                      7  CTE 이름과 base table 분리, base만 allowlist 대조
2 정확히 1문장                     8  스키마 명시 시 public만 허용
3 최상위 SELECT                    9  스키마 없는 pg_class도 거부
4 AST 재귀 순회 — 쓰기·DDL 차단   10  실행 대상 pool의 information_schema 컬럼 검증
5 함수 노드 재귀 — 위험 함수      11  LIMIT 없거나 500 초과 시 LIMIT 500 적용
6 Table 노드 catalog.db.name 정규화 12  정규화 SQL 재파싱 후 동일 검증
```

**`Table.name`만 비교하면 안 된다.** `pg_catalog.pg_tables`가 `pg_tables`로 축약돼 우회된다.

**교정 재생성**은 구문 오류·없는 컬럼·읽기 의도 스키마 불일치만 1회 허용한다.
쓰기 의도·다중 문장·비허용 테이블·위험 함수·시스템 카탈로그는 **즉시 거부**한다.
이 횟수는 `AGENT_MAX_RETRY`와 무관하다.

---

## `nl_query_log` 상태 조합 (고정)

| 결과 | is_valid | is_rejected | reject_reason | error_msg | row_cnt |
|---|---|---|---|---|---|
| 성공 | true | false | NULL | NULL | 실제 행 수 |
| 정책 거부 | false | true | 필수 | NULL | NULL |
| 파싱·컬럼 검증 실패 | false | false | NULL | 사용자용 사유 | NULL |
| DB 실행 오류 | true | false | NULL | 사용자용 사유 | NULL |

`nl_query_log`에 `llm_model` 컬럼을 추가하지 않는다. 단일 모델 평가이므로 의도적 결정이다. (설계 9.7)

**로그 기록 실패 시 생성 SQL을 write pool로 재실행하지 않는다.**

---

## 차트 호환성

| chart_type | 요구 | 위반 시 |
|---|---|---|
| bar | 범주형 x 1 + 숫자 y 1 | table 강등 |
| line | 시간·순서형 x + 숫자 y | bar 보정 |
| histogram | 숫자 컬럼 1 | table 강등 |
| table | 제한 없음 | 기본 폴백 |

프론트는 `chart_type`을 다시 판단하지 않고 Backend 계획을 그대로 렌더링한다.

---

## API · 화면

```http
POST /analytics/query        {question}
POST /analytics/validate     {sql}   검증만, 실행하지 않음
GET  /analytics/history      is_valid?, is_rejected?, date_from?, date_to?, page, size
GET  /analytics/evaluations  latest=true, page, size
GET  /audit-logs             event_type?, actor_type?, entity_type?, entity_id?,
                             date_from?, date_to?, page, size
```

| 경로 | 내용 |
|---|---|
| `/analytics` | 질문 입력 → 생성 SQL 확인 → 표·통계·동적 차트 |
| `/audit-logs` | 기간·이벤트·주체 필터, 이벤트 수 통계, before/after 펼침 |

`event_type_counts`는 현재 페이지가 아니라 **동일 필터 전체 결과의 집계**다.
정책 거부(HTTP 200 구조화 응답)와 실행 오류를 화면에서 구분 표시한다. `POST /analytics/query`는 요청별 `timeout=150000ms`를 써서 LLM 최대 2회 시도를 지원한다.

---

## 원본 절

```
설계 9.1  파이프라인            설계 9.2  허용 테이블
설계 9.3  sqlglot 검증 순서     설계 9.4  교정 재생성
설계 9.5  DB 실행 방어·pool 분리  설계 9.6  분석 결과와 차트
설계 9.7  모델 사용·기록 범위   설계 9.8  평가 실행·결과 artifact
설계 10.5  D API DTO            설계 11장  감사로그
요구사항 5.4 · 9.1 골드 12건 · 9.2 방어 6종 · 8.4 차트 규칙
```
