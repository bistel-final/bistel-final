# AI 작업 문서

> 기준 요구사항: v2.0 작업본
> 기준 시스템설계서: v2.0 작업본
> 기준 역할분담: v10.0 작업본
> 기준 WBS: v4 작업본
> 마지막 동기화: 2026-08-14

> [!CAUTION]
> **신규 데이터 전환 중 — 개별 요약 문서 사용 중지.** `01`~`07`, `PROMPT_TEMPLATE.md`,
> `tasks/*.md`의 본문은 v1.9/v1.10/v9.6 기준 구 이력이며 구현·참고·복사·프롬프트 입력에
> 사용하면 안 됩니다. v2 요약 문서가 재생성되기 전에는 v2 원본 3종과 WBS의 해당
> `V4-*` Task만 사용하십시오. 구 51건 알람, Fault 정답, ACT-0001~0010, 4단계 조치는
> 폐기 대상입니다.

현재 이 문서는 AI 코딩 도구(Claude Code · Codex 등)와 팀원을 v2 원본·역할·WBS로 보내는
**전환기 라우팅 인덱스**다. 원본 작업본은 `docs/specifications/`, Task 기준본은
`docs/planning/`에 있다.

---

## 문서 우선순위

충돌하면 위쪽이 이긴다.

```
0. docs/specifications/요구사항정의서_v2_0_작업본.md          사용자 동작·업무 규칙·수용 기준
1. docs/specifications/시스템설계서_v2_0_작업본.md           구현·데이터·상태 전이 계약
2. docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md  소유권·평가 책임
3. docs/planning/Task분해_WBS_v4_작업본.md                  v4 전용 Task ID·선행관계·완료 기준
4. docs/planning/신규데이터_정답라벨제거_전환기획_v1.md     전환 분석·폐기 근거·미확정 기록
5. docs/ai-context/README.md                                  전환기 라우팅 인덱스
6. 코드
```

**구 요약본은 읽거나 새로 해석하지 않는다.** 요구사항 번호·상태 전이·임계값은 원본 절과
해당 `V4-*` Task를 근거로 인용한다. 요약본 재생성 전에는 개별 `ai-context` 문서를 고쳐서
부분적으로 살리는 방식도 금지한다.

> **저장소 경로 주의**: 설계서 2.1의 목표 트리는 이 저장소 구조를 따른다.
> 원본 3종은 `docs/requirements/`·`docs/design/`으로 나누지 않고 `docs/specifications/` 하나로 관리한다.
> 도메인·규칙은 원본이 우선하지만, **경로는 이 문서가 우선**한다.

---

## 라우팅 — 무엇을 할 때 무엇을 읽는가

**작업 시작 시 반드시 읽는 것**

```
docs/ai-context/README.md                                  (이 문서)
docs/specifications/요구사항정의서_v2_0_작업본.md          사용자 동작·수용 기준
docs/specifications/시스템설계서_v2_0_작업본.md           구현·데이터 계약
docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md  소유권·역할
docs/planning/Task분해_WBS_v4_작업본.md                  현재 수행할 V4 Task
```

작업 요청에는 담당자와 해당 `V4-*` Task ID를 반드시 적는다. `01-project-rules.md`를 포함한
구 개별 요약 문서는 v2 재생성 전까지 읽지 않는다.

**담당 파트**

| 담당 | 현재 읽을 기준 | 구 문서 상태 |
|---|---|---|
| A Detection | 역할분담 A + WBS `V4-A-*` | `tasks/A-detection.md` 사용 중지 |
| B Knowledge | 역할분담 B + WBS `V4-B-*` | `tasks/B-knowledge.md` 사용 중지 |
| C Agent·HITL | 역할분담 C + WBS `V4-C-*` | `tasks/C-agent.md` 사용 중지 |
| D Analytics | 역할분담 D + WBS `V4-D-*` | `tasks/D-analytics.md` 사용 중지 |
| Common | 역할분담 공통 + WBS `V4-CM-*` | 구 요약문서 대신 원본 직접 확인 |

**구 주제별 요약 문서 — 전부 사용 중지**

| 구 문서 | 상태 | 현재 읽을 기준 |
|---|---|---|
| `01-project-rules.md` | 구 이력 / 사용 중지 | 요구사항·설계·역할분담 + 해당 `V4-*` Task |
| `02-domain-rules.md` | 구 이력 / 사용 중지 | 요구사항·설계 + 해당 `V4-*` Task |
| `03-database-rules.md` | 구 이력 / 사용 중지 | 설계 데이터·migration 절 + `V4-CM-*` |
| `04-api-tool-contracts.md` | 구 이력 / 사용 중지 | 설계 API·Tool 절 + `V4-CM-0.2~0.3` 및 담당 Task |
| `05-agent-workflow.md` | 구 이력 / 사용 중지 | 설계 Agent·HITL 절 + `V4-C-*` |
| `06-frontend-guide.md` | 구 이력 / 사용 중지 | 요구사항 화면 절·설계 Frontend 절 + 담당 Task |
| `07-testing-guide.md` | 구 이력 / 사용 중지 | 요구사항·설계 테스트 절 + 해당 검증 Task |
| `PROMPT_TEMPLATE.md` | 구 이력 / 사용 중지 | 요청에 v2 원본 절·역할·`V4-*` Task를 직접 명시 |
| `tasks/*.md` | 구 이력 / 사용 중지 | 역할분담 담당 절 + WBS 담당 `V4-*` Task |

**주제별 원본 절** — 요약으로 부족하거나 정확한 근거가 필요할 때

| 작업 | 원본 |
|---|---|
| Summary·evaluation·TRACE/SUMMARY 알람·R03 | 설계 4.1~4.4 · 요구사항 8.1~8.2 |
| anomaly score·feature·no-GT 평가 | 설계 4.5 · 요구사항 FR-A-03~04 |
| 대시보드·AlarmRef·Trace API | 설계 9.3·12 · 요구사항 FR-A-06~07 |
| Neo4j 관계 조회 | 설계 5.1 |
| 문서 정정·검색·임베딩 | 설계 5.2 · 요구사항 FR-B-02·04·07 |
| LangGraph State·Node·Level | 설계 6.2~6.3 |
| Tool 호출 예산 | 설계 6.6 · 요구사항 FR-C-08 |
| 3단계 조치 결정 | 설계 6.5 · 요구사항 8.3 |
| 승인·EMAIL·MES Mock·멱등성 | 설계 7장 · 요구사항 8.4 |
| 원인 가설·라벨 없는 평가 | 설계 15.4 · 요구사항 9장·FR-C-15 |
| Text2SQL 검증·실행·회귀 질문셋 | 설계 10장 · 요구사항 FR-D-01~10 |
| API DTO·엔드포인트 | 설계 9장 · `../deliverables/api/API명세서.md`(차기 개정 필요) |
| Tool 5종 계약 | 설계 8장 · 요구사항 6장 |
| 감사로그 | 설계 11장 |
| React 5개 기능 그룹·8개 화면 | 설계 12장 · 요구사항 11.2 |
| corrected bootstrap·clean Runtime migration | 설계 2~3장 · 요구사항 7장 |
| 배포·복구·공용 DB | 설계 13~14장 |
| 테스트·격리·평가 | 설계 15장 · 요구사항 13장 |

---

## 구 문서 목록 — 재생성 전 사용 중지

| 파일 | 기존 내용 | 현재 상태 |
|---|---|---|
| `01-project-rules.md` | 강제 규칙 · 계층 · Tool/REST 계약 · 예산 · DB 접근 · Git | 구 이력 / 사용 중지 |
| `02-domain-rules.md` | FDC 도메인 · 구 수치 · 판정/조치 규칙 · Fault Code | 구 이력 / 사용 중지 |
| `03-database-rules.md` | 구 스키마 · migration · ID·시간 · 계정 권한 | 구 이력 / 사용 중지 |
| `04-api-tool-contracts.md` | 구 API 목록 · Tool 5종 · DTO | 구 이력 / 사용 중지 |
| `05-agent-workflow.md` | 구 State · Node · 배치 · 승인 · 전송 · 복구 · 감사 | 구 이력 / 사용 중지 |
| `06-frontend-guide.md` | 구 화면 · 라우트 · 상태 원칙 · polling · 실행 | 구 이력 / 사용 중지 |
| `07-testing-guide.md` | 구 테스트 계층 · E2E · 평가 artifact | 구 이력 / 사용 중지 |
| `PROMPT_TEMPLATE.md` | 구 요구사항 ID를 쓰는 요청 양식 | 구 이력 / 사용 중지 |
| `tasks/*.md` | 구 역할별 범위 · 요구사항 ID · 완료 기준 | 구 이력 / 사용 중지 |

Task ID·범위·선행관계·완료 기준의 전환 Git 기준본은 `docs/planning/Task분해_WBS_v4_작업본.md`다. v3 Task는 이력으로 동결하고, Notion에서 v4 전용 ID로 신규 등록한다. 담당자·진행 상태·일정·블로커는 Notion Task DB에서 실시간 관리한다.

개별 문서는 전부 읽지 않는다. 위 라우팅에 따라 **v2 원본과 현재 수행할 V4 Task만 조합**한다.

---

## 동기화 절차

v2 요약 문서를 재생성하는 별도 Task에서만 다음을 수행한다.

1. v2 원본과 WBS의 채택 계약을 기준으로 대상 문서를 전체 재검증한다
2. 해당 문서 상단의 기준 버전 헤더를 v2로 갱신한다
3. CAUTION을 제거하기 전에 구 수치·구 ID·구 상태 전이 잔존 0건을 검사한다
4. 같은 PR에 포함한다

그 전까지 원본 변경 때 구 개별 문서를 부분 동기화하지 않는다.

---

## 도구별 진입점

```
CLAUDE.md    Claude Code가 자동으로 읽는다
AGENTS.md    Codex가 자동으로 읽는다
```

둘 다 이 폴더를 가리키는 얇은 포인터이며 **내용이 동일해야 한다.** 포인터가 구 개별 문서를
추가로 읽으라고 하더라도 이 README의 사용 중지 지시가 우선한다. 그 밖의 도구나 웹 채팅에도
구 `PROMPT_TEMPLATE.md`를 붙여넣지 말고 v2 원본 절·역할·해당 `V4-*` Task를 직접 지정한다.
