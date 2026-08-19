# A — Detection

> 기준: 멘토 최종 패키지 (2026-08-18) · 역할분담 v10.0 A · WBS `V4-A-*`
> 마지막 동기화: 2026-08-18

## 담당 범위

알람 산출·재현 검증, 알람 조회 API, 대시보드·알람 화면(1·2) 데이터 공급,
anomaly score·feature (설계 4.5).

## 최소 구현 (골든 시나리오)

```
GET /alarms       trace(OOS)+summary(OOC) UNION — 제공 SQL 있음 (04 문서 2장)
GET /trace        wafer 트렌드 raw 시계열
GET /parameters   dim_parameter 전체 — 한계선 단일 출처
```

## 핵심 규칙

- 알람 재현의 정본은 패키지 `04_알람_재현_가이드.md`. 실측 기대값:
  **summary 51 · trace 138** (구본 47·126 무효).
- R03: (chamber, parameter, recipe step) 키 · `chamber_wafer_cum` 순 ·
  LOT 경계 넘어 연속 3 OOS · 도달 시 1회.
- `upper_only=true` 파라미터는 상한만 판정 — dim_parameter 컬럼이 정의.
- 필터: `date_from/date_to` + `area`(필수, 'Photo'/'Etch') + equipment/chamber 선택.
  **필드명 불일치는 조용한 0건** — API 가이드 이름 그대로.
- trace 차트 렌더 규칙은 `06-frontend-guide.md` 3장 (step 경계선·5선 점선·알람 점 마킹).

## anomaly score

조치 규칙에 직접 반영하지 않는다. Agent 보조 근거로만. `fault_code` 를 feature 로
쓰지 않는다 (평가 정답 누수).
