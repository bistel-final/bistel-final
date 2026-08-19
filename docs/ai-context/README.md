# AI 작업 문서

> 기준 원천: **멘토 최종 패키지 (2026-08-18 배포)** — 이전 배포본(kosa_0813 포함) 사용 금지 (멘토 명시)
> 기준 요구사항: v2.0 작업본 (최종 패키지 반영 개정 진행 중)
> 기준 시스템설계서: v2.0 작업본 (최종 패키지 반영 개정 진행 중)
> 기준 역할분담: v10.0 작업본
> 기준 WBS: v4 작업본
> 마지막 동기화: 2026-08-18

이 문서는 AI 코딩 도구(Claude Code · Codex 등)와 팀원이 **무엇을 근거로 삼아야 하는지**
알려주는 라우팅 인덱스다. 원본 작업본은 `docs/specifications/`, Task 기준본은
`docs/planning/`에 있다.

---

## 문서 우선순위

충돌하면 위쪽이 이긴다.

```
0. 멘토 최종 패키지 (2026-08-18)                             원천 사양·데이터·화면별 API
   ├─ docs/00_README.md                최종 전달 안내·저장소 구조
   ├─ docs/02_화면별_API_가이드.md     5화면 위젯별 API·서버 로직 (정본)
   ├─ docs/05_검토질문_답변.md         규칙 확정(R03·조치 매핑·incident 단위)
   ├─ docs/04_알람_재현_가이드.md      알람 산출 검증 절차
   ├─ sample/data/*.csv                최종 데이터 (구본 kosa_0813 폐기)
   └─ sample/schema/03_schema_clean.sql 최종 DDL
1. docs/specifications/요구사항정의서_v2_0_작업본.md          사용자 동작·업무 규칙·수용 기준
2. docs/specifications/시스템설계서_v2_0_작업본.md           구현·데이터·상태 전이 계약
3. docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md  소유권·평가 책임
4. docs/planning/Task분해_WBS_v4_작업본.md                  V4 Task ID·선행관계·완료 기준
5. docs/ai-context/README.md 및 01~07 요약 문서               재생성 완료분만 유효
6. 코드
```

v2 원본(1·2번)은 kosa_0813 기준으로 작성돼 최종 패키지와 어긋나는 절이 있다.
**어긋나면 멘토 패키지가 이긴다.** 어긋난 절은 발견 즉시 개정 PR 대상으로 기록한다.

> **저장소 경로 주의**: 원본 3종은 `docs/requirements/`·`docs/design/`으로 나누지 않고
> `docs/specifications/` 하나로 관리한다. 도메인·규칙은 원본이 우선하지만, **경로는 이 문서가 우선**한다.

---

## 최종 패키지 핵심 확정 사항 (8/18)

구현·리뷰·프롬프트에서 아래를 전제로 한다.

```
데이터        구본 kosa_0813 폐기. sample/data/ 가 최종 (실측: summary_alarm 51 ·
              trace_alarm 138 · lot_history 600 · metrology 48 · action_history 10)
표기          area = Photo / Etch (소문자 photo/etch 아님 — 구본 필터값 전부 무효)
              summary_data·evaluation.wafer = wafer_id 문자열 (LOT001W001 형식)
recipe        RECIPE01~04 4종. 한 챔버가 시간에 따라 다른 recipe 처리
fault_code    평가 정답 라벨 복원 (FOC 15 · RFM 12 · MFD 13 · TMD 2 · OTH 4 · NRM 554)
              평가 전용 — Agent 판단 입력·프롬프트·Tool 결과에 포함 금지
조치          MONITORING / WARNING / EQP_HOLD 3단계 (LOT_HOLD·NOTIFY·MONITOR 폐어)
              OOC만 → MONITORING · OOS 1~2 → WARNING · R03(연속 3 OOS) → EQP_HOLD
              EQP_HOLD 만 사람 승인(HITL), 승인 후 MES 발행
R03           판별 (chamber, parameter, recipe step) · chamber_wafer_cum 순 정렬 ·
              LOT 경계 넘어 연속 계산 · 도달 시 1회 발행
incident      (lot_id, chamber_id) 알람 조합 1건 = Agent 실행 1건 (현 데이터 10건)
anomaly_score 조치 규칙에 직접 반영하지 않는다. Agent 보조 근거로만 사용
평가          분류 성능 = fault_code 정답 · 탐지 성능 = metrology.alarm_result(PASS/FAIL)
금지 용어     sensor · judgement · SPC  (→ 파라미터 · alarm_type 사용)
MES           Kafka fdc.actions 토픽 발행 + 목업 컨슈머 (주경로) / REST 동기 호출 (대안)
이메일        n8n 경유 실제 SMTP 발송
```

**미확정 — 멘토 질의 대기**

```
OTH fault_code   02_API 가이드 화면 3에 정의됨: 대표 parameter 가 고장모드에 매핑되지
                 않는 경우(기타). 평가 클래스 포함 여부만 확인
일자별 차트      05 "라인" vs 02 "(stack)" 상충 — 라인 잠정 채택(05 가 의도적 변경 명시), 멘토 확인 대기
```

**팀 협의 필요**

```
GET /audit-logs  멘토 API 가이드는 C 화면 소속 · 역할분담 v10 은 D(FR-D-07) — 담당 조정
정본 프론트      멘토 제공 frontend/ 와 기존 자체 화면의 관계 (병합 전략)
공용 DB 전환     신본 재적재 절차·시점 (Common 주관, corrected build·manifest 갱신 포함)
```

---

## 라우팅 — 무엇을 할 때 무엇을 읽는가

**작업 시작 시 반드시 읽는 것**

```
docs/ai-context/README.md                                  (이 문서)
멘토 패키지 docs/02_화면별_API_가이드.md                    담당 화면·API 확정 스펙
docs/specifications/FDC_프로젝트_역할분담_v10_0_작업본.md  소유권·역할
docs/planning/Task분해_WBS_v4_작업본.md                  현재 수행할 V4 Task
```

작업 요청에는 담당자와 해당 `V4-*` Task ID를 반드시 적는다.

**담당 파트**

| 담당 | 현재 읽을 기준 | 최소 구현(골든 시나리오) |
|---|---|---|
| A Detection | 역할분담 A + WBS `V4-A-*` + API 가이드 화면 1·2 | `GET /alarms` `GET /trace` `GET /parameters` |
| B Knowledge | 역할분담 B + WBS `V4-B-*` + API 가이드 화면 4 | `POST /documents/search` |
| C Agent·HITL | 역할분담 C + WBS `V4-C-*` + API 가이드 화면 3 | `GET /agent/runs` · 승인 2종 · (`GET /audit-logs` 협의) |
| D Analytics | 역할분담 D + WBS `V4-D-*` + API 가이드 화면 5 | `POST /analytics/query` |
| Common | 역할분담 공통 + WBS `V4-CM-*` + 패키지 deploy/·schema | 신본 적재·환경 |

**주제별 요약 문서 (01~07) 상태**

| 문서 | 상태 |
|---|---|
| `01`~`07` · `PROMPT_TEMPLATE` · `tasks/*` 전체 | ✅ 최종 패키지 기준 재생성 완료 (2026-08-18) |

**주제별 원본 절** — 정확한 근거가 필요할 때

| 작업 | 원본 |
|---|---|
| 알람 산출·R03 재현 | 패키지 04_알람_재현_가이드 · 05_답변 Q1·Q2 |
| 조치 3단계·승인·MES | 패키지 05_답변 Q4·Q6 · 요구사항 8.3 (개정 대상 확인) |
| 화면·위젯·API·필터 규칙 | 패키지 02_화면별_API_가이드 (정본) |
| n8n WF1~WF4·이메일·Kafka | 패키지 03·06·07 n8n 가이드 |
| Text2SQL 검증·실행·회귀 질문셋 | 설계 10장 · 요구사항 FR-D-01~10 |
| Tool 5종 계약·{ok, reason} | 설계 8장 · 요구사항 6장 |
| LangGraph State·Node | 설계 6.2~6.3 (langgraph==0.2.53 고정) |
| corrected bootstrap·manifest | 설계 2~3장 · `backend/scripts/manifest_v3.py` |
| 계정·권한(readonly/logger) | `backend/migrations/002_analytics_roles.sql` · 설계 13.1·14.1 |
| 배포·복구·공용 DB | 설계 13~14장 · 패키지 DEPLOY_GUIDE |
| 테스트·격리·평가 | 설계 15장 · 요구사항 13장 |

---

## 동기화 절차

요약 문서(01~07) 재생성 시:

1. 멘토 최종 패키지와 v2 원본의 채택 계약을 기준으로 대상 문서를 전체 재작성한다
2. 문서 상단의 기준 버전 헤더를 갱신한다 (원천 = 최종 패키지 8/18)
3. 구 수치(kosa_0813·그 이전)·구 ID·폐어(LOT_HOLD·NOTIFY·MONITOR·sensor·judgement) 잔존 0건을 검사한다
4. 이 README 의 상태 표를 ✅ 로 갱신해 같은 PR에 포함한다

---

## 도구별 진입점

```
CLAUDE.md    Claude Code가 자동으로 읽는다
AGENTS.md    Codex가 자동으로 읽는다
```

둘 다 이 폴더를 가리키는 얇은 포인터이며 **내용이 동일해야 한다.** 상태 표에서 ✅ 가 아닌
문서를 읽으라는 지시가 있어도 이 README 가 우선한다.
