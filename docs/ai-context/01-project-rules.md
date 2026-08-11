# 01. 프로젝트 강제 규칙

> 기준 요구사항: v1.9
> 기준 시스템설계서: v1.10
> 기준 역할분담: v9.6
> 마지막 동기화: 2026-08-11

이 문서는 코드·계약·보안 강제 규칙의 **단일 출처**다. 사람과 AI 도구가 같은 규칙을 쓴다.
각 규칙은 새로 해석하지 않고 원본 절 번호를 근거로 둔다. 원본과 충돌하면 원본이 우선한다.

---

## 1. 절대 금지 (어기면 되돌리기 비싸다)

| # | 금지 | 근거 |
|---|---|---|
| 1 | 배포 원본 `01_schema.sql`·`02_master_data.sql`·`03_load_data.sql`·`master.cypher`·`02_docs_rag/*.md` 수정 | 설계 0.2·3.1 / 요구사항 12장 |
| 2 | 스키마 변경을 원본 파일에 직접 반영 — 반드시 `backend/migrations/001_agent_runtime.sql` 사용 | 설계 3.1·3.2 |
| 3 | 공용 개발 서버의 기준·생산·문서 데이터 재적재·삭제·덮어쓰기 | 설계 13.2.1 / 요구사항 13장 |
| 4 | 공용 PostgreSQL·Neo4j·n8n 컨테이너 `docker stop` (장애 테스트 포함) | 설계 14.2 / 요구사항 부록 B 4-A·4-B |
| 5 | 비밀번호·API Key·전체 DSN을 로그·stdout·응답·문서·커밋에 출력 | NFR-02·NFR-14 / 설계 13.2.2 |
| 6 | `audit_log` UPDATE·DELETE (애플리케이션 메서드·API 모두) | NFR-05 / 설계 11장·14.1 |
| 7 | 조치 판정·승인 게이트를 LLM에 위임 | NFR-04 / 요구사항 8.2 / 설계 1.2·7.7 |
| 8 | 테스트를 실행하지 않고 완료로 보고 | 요구사항 13장 |
| 9 | 담당 영역 밖 파일 변경을 사전 공유 없이 수행 | 역할분담 1.1 |
| 10 | `.env`·`*.joblib`·`backend/model-cache/` 커밋 | 설계 13.5 / NFR-02 |

---

## 2. 계층 구조

```
Router → Service → Repository
```

| 계층 | 책임 | 금지 |
|---|---|---|
| Router | HTTP 입력 검증, Service 호출, 상태코드 변환 | SQL·Cypher 직접 실행, 업무 규칙 구현 |
| Schema | 요청·응답 Pydantic 모델 | DB 세션 접근 |
| Service | 업무 흐름, 트랜잭션 경계 | 문자열 SQL 직접 작성 |
| Repository | PostgreSQL·Neo4j 조회·저장 | HTTP 응답 생성, LLM 호출 |
| Tool | Service를 Agent 계약으로 감싸기 | DB 규칙 중복 구현 |
| Model/Rules | IsolationForest, 규칙 판정, `decide_action` | HTTP·DB 상태 변경 |

근거: 설계 2.2

---

## 3. Tool 반환 계약

Tool은 **예외를 바깥으로 던지지 않는다.** 실패·타임아웃도 정상 JSON으로 반환한다.

```python
{"ok": True,  ...도구별 필드..., "reason": ""}
{"ok": False, ...데이터 필드는 null 또는 빈 목록..., "reason": "NOT_FOUND: ..."}
```

실패 시 `reason`은 아래 접두어 중 하나로 시작한다. **임의 접두어를 만들지 않는다.**

```
NOT_FOUND:  TIMEOUT:  MODEL_NOT_READY:  LLM_NOT_READY:
DEPENDENCY_ERROR:  POLICY_REJECTED:  IDEMPOTENCY_CONFLICT:
```

`latency_ms`·호출 상태는 반환값에 넣지 않는다. `agent_tool_call`에 기록한다.

근거: 설계 10.6 / 요구사항 6장 공통 계약 · NFR-09

---

## 4. REST 오류 계약

| 상황 | HTTP |
|---|---|
| 존재하지 않는 리소스 | 404 |
| 진행 중 incident 수동 재실행, 승인 중복·EXPIRED | 409 |
| 요청 본문·쿼리 형식 오류 | 422 |
| Text2SQL 정책 거부 | 200 + `is_valid=false`, `is_rejected=true`, `reject_reason` |
| 모델·LLM 산출물 미준비 | 503 |
| 예기치 못한 서버 오류 | 500 |

공통 오류 본문은 `{code, message, details}`다. 500 응답과 로그에 비밀번호·전체 DSN·API Key·내부 SQL 원문을 노출하지 않는다.

Tool과 REST는 같은 Service를 쓰되 **오류 표현을 분리한다.** Tool 실패를 HTTP 상태코드로 바꾸지 않는다. Text2SQL 정책 거부는 SQL을 실행하지 않은 정상적인 안전 판정 결과이므로 REST에서 200으로 반환하되, 요청 body 누락·타입·길이 오류만 422다. `PolicyRejectedError`와 Tool의 `POLICY_REJECTED:` 접두어는 유지한다.

근거: 설계 2.3 / NFR-10·NFR-11

---

## 5. Agent Tool 예산

```
AGENT_MAX_TOOL_CALLS = 8   그래프 1회 실행의 총 실제 호출 수 (재시도 포함)
AGENT_MAX_RETRY      = 3   같은 Tool의 최초 실패 후 추가 시도 수 (동일 Tool 최대 4회)
```

- 조치 생성 가능 경로는 `reserved_send_calls=1`로 시작해 **최초 `send_action` 1회를 예약**한다. 진단·선택 호출이 이를 소비할 수 없다.
- 호출 수의 영속 단일 기준은 `agent_tool_call`이다. State의 `tool_call_count`는 캐시다.
- HITL 재개·checkpoint 유실 복구 시 `COUNT(*)`로 복원한다. **0으로 초기화하지 않는다.**
- 독립 Analytics Tool `generate_analysis_plan`은 이 8회와 `agent_tool_call`에 포함하지 않는다.

근거: 설계 7.4·7.4.1 / 요구사항 8.5 · FR-C-08

---

## 6. DB 접근

| 용도 | 연결 | 계정 |
|---|---|---|
| 애플리케이션 쓰기 | `APP_DATABASE_URL` → `kosa_agent` | `kosa_app` |
| 운영 Text2SQL 생성 SQL 실행 | `TEXT2SQL_DATABASE_URL` → `kosa_agent` | `kosa_readonly` |
| 운영 질의 로그 | `TEXT2SQL_LOG_DATABASE_URL` → `kosa_agent` | `kosa_query_logger` |
| 평가 골드·방어 실행 | `TEXT2SQL_EVAL_DATABASE_URL` → `kosa_text2sql` | `kosa_readonly` |
| 평가 로그 | `TEXT2SQL_EVAL_LOG_DATABASE_URL` → `kosa_text2sql` | `kosa_query_logger` |

- LLM이 생성한 SQL은 **readonly pool 이외의 연결에 전달하지 않는다.**
- `QueryLogRepository`는 SQL 문자열을 실행하는 메서드를 제공하지 않는다. 고정 INSERT만 노출한다.
- SQL 문자열 조합 금지. 파라미터 바인딩을 쓴다.
- 필요한 컬럼만 조회한다. `SELECT *`를 피한다.
- 배포 기본 비밀번호(`kosa_readonly` 포함)를 그대로 쓰지 않는다.

근거: 설계 9.5·14.1 / NFR-01

---

## 7. Git

- `main` 직접 commit·push 금지. `<type>/<area>-<description>` 브랜치를 만든다.
- Type: `feat` `fix` `refactor` `test` `docs` `chore`
- Area: `common` `detection` `knowledge` `agent` `analytics` `integration`
- 커밋 메시지: `<type>: <한 줄 요약>` + 본문에 무엇을 왜.
- PR 제목: `[담당영역] 변경 요약` (`Common`·`A`·`B`·`C`·`D`)

상세 흐름은 `docs/development-guide.md`.

---

## 8. 완료 보고 규칙

작업을 완료로 보고할 때 다음을 함께 제시한다.

1. 실행한 테스트 명령과 결과
2. 대상 요구사항 ID별 충족 여부
3. 미완료·미검증 항목 (숨기지 않는다)
4. 범위 밖 변경이 있었다면 그 사유

근거: 요구사항 13장 / 역할분담 1.1
