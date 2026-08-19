# D — 감사 · 선택 확장 Analytics

> 기준 원천: 멘토 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API v3 · WBS v5
> 마지막 동기화: 2026-08-19
> 담당: 천승현 · 모듈 `backend/app/analytics/` · `frontend/src/features/analytics/`

공통 append-only 감사로그의 조회 read model·API·화면을 책임진다. Text2SQL은 최종 5화면과 필수
인수 기준에서 제거됐으므로 **선택 확장**으로만 유지한다.

---

## 요구사항

| ID | 명칭 | 우선순위 |
|---|---|---|
| FR-D-07 | 감사로그 조회 | 필수 |
| FR-D-01 | 분석 계획 | 권장 |
| FR-D-02 | SQL 안전 검증 | 권장 |
| FR-D-03 | 읽기 전용 실행 | 권장 |
| FR-D-04 | 표·통계·차트 | 권장 |
| FR-D-05 | 질의 이력 | 권장 |
| FR-D-06 | 분석 확장 UI | 권장 |
| FR-D-08 | Text2SQL 평가 | 권장 |
| FR-D-09 | 수정 SQL 재검증 | 권장 |
| FR-D-10 | MCP wrapping | 도전 |

## Task (WBS v5)

| ID | 내용 | 공수 |
|---|---|---:|
| V5-D-1.1 | 감사 read model | 1.5h |
| V5-D-1.2 | `GET /audit-logs` | 1.5h |
| V5-D-1.3 | 화면 3 감사 subview | 2.0h |
| V5-D-2.1 | schema allowlist·pool 분리 | 2.0h |
| V5-D-2.2 | SQL 안전 검증 (선택) | 2.5h |
| V5-D-2.3 | 분석 계획·실행 (선택) | 2.5h |
| V5-D-2.4 | 질의 이력 (선택) | 1.5h |
| V5-D-2.5 | 선택 확장 UI·평가 | 2.0h |
| V5-D-3.1 | MCP 서버 노출 (P2) | 1.5h |

**합계 15.5h** (P2 1.5h 제외)

---

## 완료 기준

```text
감사 조회      audit_log 직접 조회. action_history 에서 사후 합성 0건
정렬          occurred_at DESC, audit_id DESC 안정 정렬
집계          동일 필터의 event count 와 page 결과를 구분
권한          UPDATE·DELETE API 없음. 생성·수정 권한 없음
화면          화면 3 감사 subview 에서 api.audit() wrapper 실제 소비

선택 확장(구현 시)
SQL 방어      단일 SELECT · AST 방어 · 위험 함수 · 다중 문장 차단 · LIMIT 500
              방어 fixture 전부 미실행
실행          runtime readonly / evaluation readonly pool 분리 · DSN fallback 0
정책 거부     SQL 미실행 상태의 구조화된 정상 결과(200)로 반환. 요청 형식 오류와 구분
평가          질문셋 12건 이상 중 10건 이상 정답 · 정렬·오차·차트 기준 기록
```

---

## 주의

**감사 이벤트를 사후 합성하지 않는다.** 각 도메인이 자기 업무 트랜잭션 안에서 기록한 것을 그대로
읽는다. 쓰기 계약은 Common, 업무 event 쓰기는 각 도메인, 조회 API·화면이 D의 몫이다.

**Text2SQL은 필수 인수 기준이 아니다.** 미구현·장애 상태에서도 기본 5화면과 필수 E2E가 정상
동작해야 한다. 구현하더라도 5화면 navigation과 분리된 route로 제공한다.

**선택 확장은 최종 schema와 허용 column만 조회한다.** 구 스키마·구 기대값 질문셋을 재사용하지
않고 final snapshot으로 다시 만든다.

**로그 writer는 SQL 실행 권한을 갖지 않는다.** 실행 계정은 DB 레벨 `READ ONLY`이며 질의 이력
기록은 별도 최소권한 writer가 담당한다.

**`kosa_text2sql`은 이름과 무관하게 격리 evaluation/reference DB다.** Text2SQL 화면 활성 여부와
무관하게 유지하며, 선택 확장에는 별도 readonly projection만 제공한다.

---

## API

```http
GET  /audit-logs        호환 필수. event·actor·entity·기간 필터, 동일 필터 전체 집계
```

선택 확장: `POST /analytics/query`, `POST /analytics/validate`, `GET /analytics/history`,
`GET /analytics/evaluations`, `GET /audit-logs/paged`.

---

## 화면

| 화면 | 내용 |
|---|---|
| 3 Agent — 감사 subview | 감사 목록·필터·정렬·상세 |
| (선택) 분석 확장 | Text2SQL 구현 시에만 별도 route 활성화 |

---

## 원본 절

```text
요구사항 v2.1  5.4 FR-D-01~10 · 7.3 공용 DB 안전
설계 v2.1      10. Analytics · 11. 감사로그
역할분담 v10.1  9. D — 감사·선택 확장 Analytics Full-stack · 5.2 감사로그 소유권
기준표          6. 화면·API 기준
```
