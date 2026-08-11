# AI 작업 문서

> 기준 요구사항: v1.9
> 기준 시스템설계서: v1.10
> 기준 역할분담: v9.6
> 마지막 동기화: 2026-08-11

이 폴더는 AI 코딩 도구(Claude Code · Codex 등)와 팀원이 **빠르게 문맥을 잡기 위한 요약본**이다.
원본은 `docs/specifications/` 이며, 세 문서 합계가 약 137K 토큰이라 통째로 읽히지 않는다.

---

## 문서 우선순위

충돌하면 위쪽이 이긴다.

```
1. docs/specifications/요구사항정의서_v1_9_최종.md       기능 동작·상태 전이·수용 기준
2. docs/specifications/시스템설계서_v1_10_최종.md        구현 구조·DTO·트랜잭션
3. docs/specifications/FDC_프로젝트_역할분담_v9.6(최종).md  역할·Tool 소유권
4. docs/ai-context/**                                   요약·작업 지침
5. 코드
```

**요약본은 새로 해석하지 않는다.** 요구사항 번호·상태 전이·임계값은 원본 절 번호를 근거로 인용한다.
요약본과 원본이 어긋나면 요약본이 틀린 것이다. 발견 즉시 고치고 기준 버전 헤더를 갱신한다.

> **저장소 경로 주의**: 설계서 2.1의 목표 트리는 이 저장소 구조를 따른다.
> 원본 3종은 `docs/requirements/`·`docs/design/`으로 나누지 않고 `docs/specifications/` 하나로 관리한다.
> 도메인·규칙은 원본이 우선하지만, **경로는 이 문서가 우선**한다.

---

## 라우팅 — 무엇을 할 때 무엇을 읽는가

**항상 읽는 것**

```
docs/ai-context/README.md            (이 문서)
docs/ai-context/01-project-rules.md  강제 규칙
```

**담당 파트**

| 담당 | 문서 |
|---|---|
| A Detection | `tasks/A-detection.md` |
| B Knowledge | `tasks/B-knowledge.md` |
| C Agent·HITL | `tasks/C-agent.md` |
| D Analytics | `tasks/D-analytics.md` |

**주제별 요약 문서**

| 하는 일 | 읽을 것 |
|---|---|
| 스키마 변경 · migration · DB 계정 | `03-database-rules.md` |
| API 추가·수정 · Tool 구현 | `04-api-tool-contracts.md` |
| LangGraph · 승인 · 전송 · 복구 | `05-agent-workflow.md` |
| React 화면 | `06-frontend-guide.md` |
| 테스트 작성·실행 | `07-testing-guide.md` |

**주제별 원본 절** — 요약으로 부족하거나 정확한 근거가 필요할 때

| 작업 | 원본 |
|---|---|
| 요약 재계산·R01/R02/R03 | 설계 5.1~5.2 · 요구사항 8.1 |
| anomaly score·feature | 설계 5.3 |
| 대시보드 KPI | 요구사항 5.1 「대시보드 KPI 확정 규칙」 · 설계 5.5 |
| Neo4j 관계 조회 | 설계 6.1 |
| 문서 검색·임베딩 | 설계 6.2~6.5 |
| LangGraph State·Node·Level | 설계 7.1~7.3 |
| Tool 호출 예산 | 설계 7.4·7.4.1 · 요구사항 8.5 |
| 조치 결정 규칙 | 요구사항 8.2 · 설계 7.7 |
| 승인 트랜잭션·복구 | 요구사항 8.3 · 설계 7.5 |
| 전송 멱등성·SENDING 복구 | 설계 7.8 · 3.2.5 |
| Fault 분류·오프라인 평가 | 설계 7.6 · 요구사항 FR-C-15 |
| Text2SQL 검증·실행 | 설계 9.2~9.5 · 요구사항 9.2 |
| 골드 질문셋 | 요구사항 9.1 |
| API DTO·엔드포인트 명세 | 설계 10.1~10.5 · `../deliverables/api/API명세서.md` |
| Tool 5종 계약 | 설계 10.6 · 요구사항 6장 |
| 감사로그 이벤트 9종 | 설계 11장 · 요구사항 11.1 |
| React 화면·라우트 | 설계 12.1~12.2 |
| 마이그레이션 | 설계 3.1~3.2 |
| 배포·환경변수·Compose | 설계 13장 |
| 테스트 계층·격리 | 설계 14.1·15.1 · 요구사항 13장 |

---

## 문서 목록

| 파일 | 내용 | 주요 독자 |
|---|---|---|
| `01-project-rules.md` | 강제 규칙 · 계층 · Tool/REST 계약 · 예산 · DB 접근 · Git | 전원 |
| `02-domain-rules.md` | FDC 도메인 · **불변 수치** · 판정/조치 규칙 · Fault Code | 전원 |
| `03-database-rules.md` | 원본 스키마 보존 · migration · ID·시간 · 계정 권한 | 전원 |
| `04-api-tool-contracts.md` | API 목록 · Tool 5종 · DTO | 전원 |
| `05-agent-workflow.md` | State · Node · 배치 · 승인 · 전송 · 복구 · 감사 | C |
| `06-frontend-guide.md` | 8화면 · 라우트 · 상태 원칙 · polling · 실행 | 전원 |
| `07-testing-guide.md` | 테스트 계층 · 실행 · E2E 격리 · 평가 artifact | 전원 |
| `PROMPT_TEMPLATE.md` | 도구 무관 요청 양식 | 전원 |
| `tasks/*.md` | 역할별 범위 · 요구사항 ID · 완료 기준 | 각 담당자 |

전부 읽을 필요는 없다. 위 라우팅에 따라 **필요한 것만 조합**한다.

---

## 동기화 절차

`docs/specifications/`를 고치면 다음을 함께 한다.

1. 영향받는 `ai-context/` 문서를 수정한다
2. 해당 문서 상단의 기준 버전 헤더를 갱신한다
3. 같은 PR에 포함한다 (PR 템플릿 체크박스)

수치가 바뀌면 `02-domain-rules.md`의 불변 수치 블록을 반드시 확인한다.
그 값들은 테스트 수용 기준과 직결된다.

---

## 도구별 진입점

```
CLAUDE.md    Claude Code가 자동으로 읽는다
AGENTS.md    Codex가 자동으로 읽는다
```

둘 다 이 폴더를 가리키는 얇은 포인터이며 **내용이 동일해야 한다.** 규칙 본문을 복제하지 않는다.
그 밖의 도구를 쓰거나 웹 채팅에 붙여넣는 경우 `PROMPT_TEMPLATE.md`를 따른다.
