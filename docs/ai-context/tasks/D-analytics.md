# D — 감사 · Analytics

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-29
> 담당: 천승현 · 모듈 `backend/app/analytics/` · `frontend/src/features/analytics/`

공통 append-only 감사로그의 read model·화면 3 문맥 subview·화면 7 전역 조회와, 화면 6의
Text2SQL·질의 이력·immutable 평가 이력을 책임진다. FR-D-01~09와 팀 release API 5개는 필수다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-D-07 | 감사로그 조회 | 필수 |
| FR-D-01 | `generate_analysis_plan` | 필수 |
| FR-D-02 | SQL 안전 검증 | 필수 |
| FR-D-03 | 읽기 전용 실행 | 필수 |
| FR-D-04 | 표·통계·차트 | 필수 |
| FR-D-05 | 질의 이력 | 필수 |
| FR-D-06 | 자연어 분석 UI | 필수 |
| FR-D-08 | Text2SQL 평가 | 필수 |
| FR-D-09 | 수정 SQL 재검증 | 필수 |
| FR-D-10 | MCP wrapping | 도전·P2 |

관련 비기능 요구사항은 NFR-01·05·07·09·11·17이다.

## Task (WBS v5 정본)

| ID | P | 완료 기준 | FR/NFR | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-D-1.1 | P0 | 감사 read model. 완료: `audit_log`를 직접 조회하고 `action_history`에서 사후 합성하지 않는다. `occurred_at DESC, audit_id DESC` 안정 정렬을 적용한다 | FR-D-07, NFR-05 | V5-CM-4.2, V5-CM-3.2 | 1.5h |
| V5-D-1.2 | P0 | `GET /audit-logs`. 완료: event·actor·entity·기간 필터와 `occurred_at DESC, audit_id DESC` 정렬의 **bare array**를 반환하고 `total=items.length`로 해석한다. paged response·전체 집계는 팀 release 전역 감사 path에서 제공하며 UPDATE·DELETE는 만들지 않는다 | FR-D-07, NFR-05, NFR-11 | V5-D-1.1 | 1.5h |
| V5-D-1.3 | P1 | 화면 3 감사 subview. 완료: D가 `api.audit()`를 실제 소비해 필터·정렬·상세와 Loading·Error·Empty·Success를 구현하고 C는 이 subview를 조립만 한다 | FR-D-07, FR-I-02, NFR-17 | V5-D-1.2 | 2.0h |
| V5-D-1.4 | P0 | 화면 7 전역 감사로그. 완료: `GET /audit-logs/paged`를 실제 소비해 event·actor·entity·기간 필터, 동일 전체 필터 기준 유형 집계, 안정 pagination, 상세 before·after와 Loading·Error·Empty·Success를 제공한다. Agent 문맥 감사와 목적을 분리하고 route-level Mock은 0건이다 | FR-D-07, FR-I-02~03, NFR-17 | V5-D-1.2, V5-CM-4.4-1 | 2.0h |
| V5-D-2.1 | P0 | schema allowlist·pool. 완료: `V5-CM-1.8`이 재발급한 final manifest-backed R03 12컬럼을 기반으로 table/column allowlist 정책을 소유하고 runtime readonly·evaluation readonly pool을 분리한다. DSN fallback 0건 | FR-D-03, NFR-01 | V5-CM-3.5 | 2.0h |
| V5-D-2.2 | P0 | SQL 안전 검증. 완료: 생성 SQL과 사용자 수정 SQL 모두 같은 단일 SELECT·AST·allowlist·위험 함수·다중 문장·LIMIT 500 정책으로 재검증하며 거부 fixture는 실행 0건과 사유를 반환한다 | FR-D-02, FR-D-09, NFR-07 | V5-D-2.1 | 2.0h |
| V5-D-2.3 | P0 | `generate_analysis_plan(question)` Tool·실행. 완료: 단일 `question`으로 SQL·metric·group_by·table/bar/line/histogram 계획을 만들고 검증기를 통과한 경우만 실행한다. 정책 거부·형식 오류·timeout은 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따른다 | FR-D-01, FR-D-04, NFR-09 | V5-D-2.2 | 2.0h |
| V5-D-2.4 | P0 | 질의 이력. 완료: runtime·E2E의 `nl_query_log`와 최소권한 writer로 성공·정책 거부·실행 오류를 기록한다. evaluation 실행 DB는 읽기 전용이며 log pool은 사용자 SQL 실행 권한을 갖지 않는다 | FR-D-05, NFR-01 | V5-D-2.3 | 1.5h |
| V5-D-2.5 | P0 | 자연어 분석 UI·평가 실행. 완료: `/analytics`에서 질문·SQL·표·통계·차트와 Loading·Error·Empty·Success를 실제 API로 제공하고 final 질문셋 12건 이상 중 10건 이상 정답인 immutable 평가 artifact를 만든다 | FR-D-06, FR-D-08, NFR-17 | V5-D-2.4 | 2.0h |
| V5-D-2.6 | P0 | 이력·평가 release 연결. 완료: `GET /analytics/history` 실응답을 이력 탭에 hydrate하고 과거 질문을 같은 안전 경계로 재실행한다. 기존 평가 채점 로직을 재구현하지 않고 immutable artifact를 validate·stable sort·page하는 read-only `GET /analytics/evaluations` adapter와 평가 보조 탭을 제공한다. 별도 8번째 primary menu와 route-level Mock은 0건이며 API·UI 회귀를 통과한다 | FR-D-05~06, FR-D-08, FR-I-02~03 | V5-D-2.5, V5-CM-4.4-1 | 4.0h |
| V5-D-3.1 | P2 | MCP 서버 노출 | FR-D-10 | V5-D-2.5 | 1.5h |

**합계 11 Task / 22.0h** · **P2 제외 필수 합계 20.5h**

---

## 감사 API·화면 계약

```http
GET /audit-logs
```

- `audit_log`를 직접 읽고 `action_history`에서 이벤트를 사후 합성하지 않는다.
- event·actor·entity·기간 필터와 `occurred_at DESC, audit_id DESC` 안정 정렬을 적용한다.
- 최소 호환 응답은 **bare array**다. 화면의 `total`은 서버 전체 건수가 아니라
  `items.length`로 해석한다.
- 전역 감사 화면은 기존 bare-array shape를 바꾸지 않고 별도 필수 `GET /audit-logs/paged`를 쓴다.
- UPDATE·DELETE API는 만들지 않는다. 쓰기 계약은 Common, 업무 이벤트 기록은 각 도메인,
  조회 read model·API·화면은 D가 소유한다.
- D가 화면 3의 `api.audit()` subview를 구현하고 C는 이를 탭에 조립만 한다.
- 감사 subview는 Loading·Error·Empty·Success 네 상태를 component test로 검증한다.

## 팀 release Analytics

`generate_analysis_plan(question)`은 단일 자연어 `question`을 SQL·metric·group_by·chart 계획으로
구조화하는 Tool이다.
생성 SQL과 사용자 수정 SQL은 같은 검증 경로를 거쳐야 하며 검증 전 실행하지 않는다.

```text
허용 statement   단일 SELECT
방어             AST · table/column allowlist · 위험 함수 · 다중 문장 차단
결과 제한         LIMIT 500
chart             table / bar / line / histogram
Tool 공통 필드    ok / reason / payload
```

정책 거부·형식 오류·timeout은 공통 reason prefix와 실행 0건·빈 payload 규칙을 지킨다. Tool
실행 시간은 결과가 아니라 `agent_tool_call` metadata에 기록한다. Runtime readonly와
evaluation readonly pool을 분리하고 DSN fallback을 금지한다. 질의 이력 writer는 최소권한이며
SQL 실행 권한을 갖지 않는다. 운영 질의 이력은 runtime·E2E에 기록하고, `kosa_text2sql`은
immutable 평가 실행용 read-only reference DB로 사용한다.

화면 6과 7은 Loading·Error·Empty·Success 네 상태를 구분하고 route-level Mock 없이 실제 API를
소비한다. final 질문셋 12건 이상 중 10건 이상 정답과 실패 사례를 immutable artifact에 기록하며,
평가 API는 이 artifact를 읽기만 한다.

## 선행조건·협업 주의

- 감사 read model은 `V5-CM-4.2` append-only helper와 `V5-CM-3.2` schema 이후 착수한다.
- C의 화면 3 조립(`V5-C-5.2`)은 D의 감사 subview(`V5-D-1.3`)를 선행으로 둔다.
- Analytics pool은 `V5-CM-3.5` 권한 분리 이후 생성한다.
- `kosa_text2sql`은 이름과 무관하게 격리 evaluation/reference DB다. 기본 화면의 Runtime DB로
  fallback하지 않는다.
- FR-D-10 MCP는 P2이며 필수·권장 합계에서 제외한다.

## 원본 절

```text
요구사항 v2.1  5.4 FR-D-01~10 · 7.3 공용 DB 안전
설계 v2.1      10. Analytics · 11. 감사로그
역할분담 v10.1  9. D — 감사·Analytics Full-stack · 5.2 감사로그 소유권
기준표          6. 화면·API 기준
```
