# 07. 테스트 가이드

> 기준 원천: 멘토 최종 패키지 sample/data 실측 (2026-08-18) · 04_알람_재현_가이드
> 보조 기준: 요구사항 v2.0 13장 · 시스템설계서 v2.0 15장
> 마지막 동기화: 2026-08-18

---

## 1. 실행 규칙

```
커밋 전 필수    cd backend && ruff format . && ruff check . && pytest
순서 강제       ruff format 이 git add 보다 먼저다
전체 실행       pytest 인자 없이 — 내 변경이 남의 테스트를 깨는지 확인하는 목적
개발 중         pytest tests/unit/test_<대상>.py 로 좁혀 빠르게
```

- CI 는 lint·test 를 돌리지 않는다. **결과 수치를 PR 본문에 수동 기록**한다.
- 전체 실패가 내 변경 탓인지 확신이 없으면 origin/main 단독 실행과 수치를 비교해
  근거를 남긴다.

## 2. 로컬 전제 조건

- bootstrap 계열 테스트는 **corrected build 산출물**을 요구한다. 없으면
  `NotRegisteredError: active corrected build` 로 실패한다 — 코드 버그가 아니다.
  절차: `01-project-rules.md` 5장. 신본 전환 후 대상 아카이브가 바뀐다 (Common 공지).
- 단위 테스트는 DB 접속 없이 돌아야 한다. pool·preflight·schema cache·검증기는
  가짜 factory·임시 manifest 로 격리한다 (기존 테스트가 패턴 예시).

## 3. 수용 기준 수치 (최종 데이터 실측)

테스트 기대값은 아래 실측을 쓴다. 재계산으로 다른 값을 만들지 않는다.

```
summary_alarm 51 · trace_alarm 138 · lot_history 600 · fdc_trace 14,400
summary_data 4,800 · evaluation 4,800 · metrology 48 (PASS 39 / FAIL 9)
action_history 시드 10 (EQP_HOLD 3 / WARNING 2 / MONITORING 5)
fault_code   NRM 554 / FOC 15 / MFD 13 / RFM 12 / OTH 4 / TMD 2
incident (lot_id, chamber_id) 10
area 값 'Photo'/'Etch' · wafer 는 wafer_id 문자열
```

알람 재현 검증 절차의 정본은 패키지 `04_알람_재현_가이드.md` (A 주관).

## 4. 계층별 테스트

| 계층 | 방식 |
|---|---|
| 단위 | DB 없이. 계약·경계·실패 경로 중심 (fail-closed 는 실패 케이스로 고정) |
| 검증기 (D) | red/green fixture 가 수용 기준. RED = 차단 + reason 키워드, GREEN = 통과. 구현 전 조건부 xfail(strict) → 구현 시 자동 해제 |
| 통합·E2E | `kosa_agent_e2e` 격리 DB. 상세는 설계 15장 |
| 평가 | 분류 = fault_code 정답 대조 · 탐지 = metrology.alarm_result 대조. TMD 는 표본 2건 — 정량 평가 시 표본 수 명시 |

## 5. 원칙

- **완료 기준 문장 하나 = 테스트 하나.** "로그 0건" 같은 기준은 눈이 아니라
  테스트로 증명한다.
- 조건부 xfail 은 `strict=True` — 구현 후 XPASS 가 남으면 실패로 처리해
  fixture 와 구현의 어긋남을 드러낸다.
- 테스트 간 간섭 금지: 전역 상태(monkeypatch·lru_cache)는 fixture 에서 복원하고,
  cache 는 `cache_clear()` 를 try/finally 로 감싼다.
- 실패를 침묵시키지 않는다: 넓은 except 로 테스트를 통과시키는 패턴 금지.
